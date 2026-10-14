"""Food domain mechanics: each mechanic has a success case and a failure/consequence case.

Scenes are built through the spec machinery (house generation + setup ops + the food.kitchen helper), so the
tests run the same way with only core+food loaded or with every domain loaded: the helper uses whatever
fridge/sink/bins the house has and builds stand-ins from core furniture otherwise.
"""
import json
import os
import re
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.predicates import check, digest
from house_sim.tasks.runner import PlanError, Runner, build

FOOD = "house_sim.systems.food"


def scene(setup, seed=0, house=None, kitchen_args=None, place_robot=True, perception=None):
    ops = [{"op": "helper", "name": "food.kitchen", "args": kitchen_args or {}}] + list(setup)
    if place_robot:
        ops.append({"op": "robot", "room": "{kitchen}", "cell": "{stand}"})
    spec = {"catalog_id": "test-food", "house": house or {"residents": "minimal", "pets": "none"}, "setup": ops,
            "success": [{"path": "time_s", "op": "gte", "value": 0}], "reference": []}
    config, task, oracle = build(spec, seed)
    env = HouseEnv(config, task, seed, trace=False, observe_on_step=False, perception=perception)
    b = oracle["bindings"]
    return env, b, Runner(env, b, strict=True)


def msg_json(message):
    """JSON payload of an action message (kernel v2.1 appends ' | note' texts after it)."""
    return json.JSONDecoder().raw_decode(message)[0]


def ent(env, eid):
    return env.world.s["entities"][eid]


def food(env, eid):
    return env.world.s["entities"][eid]["food"]


def step(env, action, target="", destination="", **kw):
    return env.step(action, target, destination, **kw)


def knife_ready(r, b, knife="knife"):
    r.skill("fetch", obj=b[knife], grip="handle")


def set_down(r, b, eid=None, where="counter"):
    """Put a held item (default: whatever is held) back on a counter."""
    held = [eid] if eid else list(r.env.world.held_items())
    for item in held:
        r.skill("put", obj=item, dest=b[where])


class FoodTypesAndFurnishing(unittest.TestCase):
    def test_food_types_have_realistic_props(self):
        env, b, r = scene([])
        reg = env.registry
        for name in ("hen_egg", "milk_carton", "chicken_breast", "all_purpose_flour", "chef_knife", "kitchen_scale", "stock_pot"):
            self.assertTrue(reg.known(name), name)
        egg = reg.resolve("hen_egg")
        self.assertAlmostEqual(egg["mass_kg"], 0.057, places=3)
        self.assertEqual(egg["food"]["kind"], "egg")
        milk = reg.resolve("milk_carton")
        self.assertTrue(milk["perishable"])
        self.assertEqual(milk["storage"], "fridge")
        self.assertIn("sharp", reg.resolve("chef_knife")["hazards"])

    def test_generated_kitchens_are_stocked_without_errors(self):
        from house_sim.generator import generate_house
        for seed in (0, 1, 2, 7, 41):
            config = generate_house(seed)
            ents = config["entities"]
            knives = [e for e, r in ents.items() if r.get("type") == "chef_knife"]
            staples = [e for e, r in ents.items() if r.get("type") == "all_purpose_flour"]
            self.assertTrue(knives, seed)
            self.assertTrue(staples, seed)
            for eid in staples:
                self.assertIn("date_label", ents[eid])
                self.assertIn("_expires", ents[eid])
            env = HouseEnv(config, seed=seed, trace=False, observe_on_step=False)
            self.assertTrue(env.step("wait", seconds=120)["ok"])

    def test_cupboards_are_packed_like_a_cook_packs_them(self):
        # pots nest, pans stack, bowls nest and sheet pans are filed on edge, so a small kitchen whose only base cabinet
        # is under the sink still gets its cookware and dry goods (in the wall units), never next to the cleaners
        from house_sim.generator import generate_house
        from house_sim.systems.food import furnish as F
        from house_sim.systems.food import model as M
        ents = {"cab": {"type": "cabinet", "inner_size_m": [0.75, 0.55, 0.8], "shelf_heights": [0.1, 0.45]},
                "pot": {"type": "stock_pot", "category": "cookware", "location": "cab", "relation": "in", "shelf": 0, "size_m": [0.3, 0.27, 0.17]},
                "sauce": {"type": "saucepan", "category": "cookware", "location": "cab", "relation": "in", "shelf": 0, "size_m": [0.38, 0.19, 0.1]},
                "jar": {"type": "honey_jar", "category": "food", "location": "cab", "relation": "in", "shelf": 0, "size_m": [0.08, 0.08, 0.12]}}
        self.assertAlmostEqual(F._shelf_area(ents, "cab", 0), max(0.3 * 0.27, 0.38 * 0.19) + 0.08 * 0.08, places=6)
        sheet = {"type": "baking_sheet", "category": "bakeware", "size_m": [0.46, 0.34, 0.025], "pack_orientation": "on_edge"}
        self.assertAlmostEqual(F._added_area(ents, "cab", 1, sheet), 0.46 * 0.03, places=6)
        self.assertEqual(F._added_area(ents, "cab", 0, {"type": "nonstick_pan", "category": "cookware", "size_m": [0.2, 0.2, 0.06]}),
                         0.2 * 0.2)                                    # pans stack on pans, not inside the stock pot
        stocked = 0
        for seed in (0, 1, 2):
            config = generate_house(seed)
            e = config["entities"]
            types = {r.get("type") for r in e.values()}
            stocked += bool(types & {"stock_pot", "saucepan"}) and "all_purpose_flour" in types
            for eid, rec in e.items():
                parent = e.get(rec.get("location")) or {}
                if rec.get("category") in ("cookware", "bakeware") or M.food(rec) is not None:
                    shelf_mates = [o for o in e.values() if o.get("location") == rec.get("location") and o.get("shelf") == rec.get("shelf")]
                    self.assertFalse(any(M.is_chemical(o) for o in shelf_mates), (seed, eid, rec.get("location")))
                if parent.get("type") == "cabinet" and rec.get("pack_orientation") == "on_edge":
                    self.assertLessEqual(rec["size_m"][1], F._clearance(parent, rec.get("shelf", 0)))
        self.assertEqual(stocked, 3)


class KnifeWork(unittest.TestCase):
    def setUp(self):
        self.env, self.b, self.r = scene([
            {"op": "spawn", "var": "onion", "type": "onion", "on": "{board}"},
            {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"}])

    def test_dice_after_halving_gives_even_medium_dice(self):
        env, b, r = self.env, self.b, self.r
        knife_ready(r, b)
        r.do("cut", target=b["onion"], style="halve", axis="lengthwise")
        r.do("trim", target=b["onion"], part="top")
        r.do("peel", target=b["onion"])
        r.do("dice", target=b["onion"], size="medium")
        f = food(env, b["onion"])
        self.assertEqual(f["cut"], "diced")
        self.assertEqual(f["piece_mm"][0], 12.0)
        self.assertLessEqual(f["piece_cv"], 0.15)
        self.assertNotIn("skin", f["parts"])
        self.assertEqual(env.world.s["stats"]["damage"], 0)
        scraps = [e for e, rec in env.world.s["entities"].items() if rec.get("type") == "prep_scraps" and rec.get("location") == b["board"]]
        self.assertTrue(scraps)

    def test_whole_round_onion_rolls_and_knife_slips(self):
        env, b, r = self.env, self.b, self.r
        knife_ready(r, b)
        res = step(env, "dice", b["onion"], size="medium")
        self.assertTrue(res["ok"])
        self.assertIn("slipped", res["message"])
        self.assertEqual(env.world.s["stats"]["damage"], 1)
        self.assertIn("end_effector_cut", env.world.s["robot"]["faults"])
        self.assertGreaterEqual(food(env, b["onion"])["piece_cv"], 0.45)

    def test_root_removed_before_dicing_splays_into_slivers(self):
        env, b, r = self.env, self.b, self.r
        knife_ready(r, b)
        r.do("cut", target=b["onion"], style="halve", axis="lengthwise")
        r.do("trim", target=b["onion"], part="root")
        r.do("peel", target=b["onion"])
        r.do("dice", target=b["onion"], size="medium")
        f = food(env, b["onion"])
        self.assertTrue(f.get("slivers"))
        self.assertGreater(f["piece_cv"], 0.4)

    def test_cutting_needs_a_knife_and_never_in_hand(self):
        env, b, r = self.env, self.b, self.r
        res = step(env, "dice", b["onion"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "capability")
        knife_ready(r, b)
        r.do("pick_up", target=b["onion"])
        res = step(env, "cut", b["onion"], style="halve")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "safety")

    def test_cutting_on_a_wooden_counter_scratches_it(self):
        env, b, r = self.env, self.b, self.r
        knife_ready(r, b)
        r.do("pick_up", target=b["onion"])
        r.do("place", target=b["onion"], destination=b["counter"])
        ent_counter = ent(env, b["counter"])
        res = step(env, "cut", b["onion"], style="halve")
        self.assertTrue(res["ok"])
        if ent_counter.get("material") in ("stone", "granite", "marble", "quartz"):
            self.assertLess(ent(env, b["knife"])["sharpness"], 1.0)
        else:
            self.assertGreaterEqual(env.world.s["stats"]["damage"], 1)

    def test_knife_can_be_put_down_and_carried_by_its_handle(self):
        env, b, r = self.env, self.b, self.r
        knife_ready(r, b)
        self.assertTrue(step(env, "place", b["knife"], b["counter"])["ok"])
        res = step(env, "pick_up", b["knife"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "safety")


class ProduceParts(unittest.TestCase):
    def test_hull_strawberries_huller_vs_slicing_tops(self):
        results = {}
        for method in ("huller", "slice_top"):
            env, b, r = scene([
                {"op": "spawn", "var": "berries", "type": "strawberries", "on": "{board}"},
                {"op": "spawn", "var": "huller", "type": "strawberry_huller", "on": "{counter}"},
                {"op": "spawn", "var": "knife", "type": "paring_knife", "on": "{counter}"}])
            r.skill("fetch", obj=b["huller"] if method == "huller" else b["knife"], grip="handle" if method != "huller" else None)
            r.do("remove_part", target=b["berries"], part="calyx", method=method)
            results[method] = food(env, b["berries"])
            self.assertNotIn("calyx", results[method]["parts"])
        self.assertGreater(results["huller"]["qty_g"], results["slice_top"]["qty_g"] + 30)

    def test_avocado_pit_spoon_is_safe_knife_strike_in_palm_is_not(self):
        env, b, r = scene([
            {"op": "spawn", "var": "avo", "type": "avocado", "on": "{board}"},
            {"op": "spawn", "var": "spoon", "type": "table_spoon", "on": "{counter}"},
            {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"}])
        knife_ready(r, b)
        r.do("cut", target=b["avo"], style="halve", axis="lengthwise")
        set_down(r, b)
        r.skill("fetch", obj=b["spoon"])
        r.do("remove_part", target=b["avo"], part="pit", tool=b["spoon"])
        self.assertNotIn("pit", food(env, b["avo"])["parts"])
        self.assertEqual(env.world.s["stats"]["damage"], 0)
        env2, b2, r2 = scene([
            {"op": "spawn", "var": "avo", "type": "avocado", "on": "{board}"},
            {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"}])
        knife_ready(r2, b2)
        r2.do("cut", target=b2["avo"], style="halve", axis="lengthwise")
        r2.do("pick_up", target=b2["avo"])
        r2.do("remove_part", target=b2["avo"], part="pit", method="knife_strike")
        self.assertEqual(env2.world.s["stats"]["damage"], 1)

    def test_pin_bones_are_hidden_until_inspected_closely(self):
        env, b, r = scene([
            {"op": "spawn", "var": "salmon", "type": "salmon_fillet", "on": "{board}", "props": {"temperature_c": 4.0}},
            {"op": "spawn", "var": "tweezers", "type": "fish_tweezers", "on": "{counter}"}])
        self.assertNotIn("pin_bones", food(env, b["salmon"])["parts"])
        obs = env.observe()
        self.assertNotIn("_pin_bones", obs["visible_entities"].get(b["salmon"], {}))
        details = msg_json(r.do("inspect", target=b["salmon"], where="closely")["message"])
        self.assertIn("pin_bones", details["food"]["parts"])
        r.skill("fetch", obj=b["tweezers"])
        r.do("remove_part", target=b["salmon"], part="pin_bones")
        self.assertNotIn("_pin_bones", ent(env, b["salmon"]))

    def test_peeling_toward_the_hand_slips_and_thick_peel_wastes_flesh(self):
        env, b, r = scene([
            {"op": "spawn", "var": "potato", "type": "potato", "on": "{board}"},
            {"op": "spawn", "var": "peeler", "type": "vegetable_peeler", "on": "{counter}"},
            {"op": "spawn", "var": "knife", "type": "paring_knife", "on": "{counter}"}])
        r.skill("fetch", obj=b["peeler"], grip="handle")
        r.do("peel", target=b["potato"])
        f = food(env, b["potato"])
        self.assertTrue(f["peeled"])
        self.assertIn("eyes", f["parts"])        # peeling does not dig out eyes
        self.assertLess(f["peel_loss_frac"], 0.2)
        env2, b2, r2 = scene([
            {"op": "spawn", "var": "potato", "type": "potato", "on": "{board}"},
            {"op": "spawn", "var": "knife", "type": "paring_knife", "on": "{counter}"}])
        knife_ready(r2, b2)
        r2.do("peel", target=b2["potato"], direction="toward", thickness_mm=6)
        self.assertEqual(env2.world.s["stats"]["damage"], 1)
        self.assertGreater(food(env2, b2["potato"])["peel_loss_frac"], f["peel_loss_frac"])

    def test_herb_leaves_kept_or_thrown_away(self):
        env, b, r = scene([{"op": "spawn", "var": "rosemary", "type": "rosemary", "on": "{board}"}])
        r.do("remove_part", target=b["rosemary"], part="woody_stem", method="strip")
        self.assertEqual(food(env, b["rosemary"])["cut"], "leaves_stripped")
        env2, b2, r2 = scene([{"op": "spawn", "var": "rosemary", "type": "rosemary", "on": "{board}"}])
        r2.do("remove_part", target=b2["rosemary"], part="leaves", method="strip")
        self.assertEqual(food(env2, b2["rosemary"])["cut"], "stems_only")
        self.assertLess(food(env2, b2["rosemary"])["qty_g"], 6.0)


class Eggs(unittest.TestCase):
    def setUp(self):
        self.env, self.b, self.r = scene([
            {"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"},
            {"op": "spawn", "var": "egg2", "type": "hen_egg", "on": "{counter}", "props": {"_rotten": True}},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "cup", "type": "prep_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "whisk", "type": "whisk", "on": "{counter}"}])

    def test_crack_on_flat_surface_is_clean_and_leaves_shell_in_hand(self):
        env, b, r = self.env, self.b, self.r
        r.skill("fetch", obj=b["egg"])
        out = msg_json(r.do("crack_egg", target=b["egg"], destination=b["bowl"], surface="flat")["message"])
        self.assertEqual(out["shell_fragments_added"], 0)
        self.assertIn(out["shell_in_hand"], env.world.held_items())
        raw = ent(env, out["egg"])
        self.assertEqual(raw["food"]["kind"], "raw_egg")
        self.assertAlmostEqual(raw["food"]["qty_g"], 57 * 0.89, delta=0.5)

    def test_rim_crack_drives_shell_in_and_rotten_egg_smells(self):
        env, b, r = self.env, self.b, self.r
        r.skill("fetch", obj=b["egg2"])
        out = msg_json(r.do("crack_egg", target=b["egg2"], destination=b["cup"], surface="rim")["message"])
        self.assertEqual(out["shell_fragments_added"], 1)
        self.assertTrue(ent(env, out["egg"])["_rotten"])
        details = msg_json(r.do("inspect", target=b["cup"])["message"])
        self.assertIn("rotten", details["food_contents"][0]["odor"])

    def test_beating_until_uniform(self):
        env, b, r = self.env, self.b, self.r
        for egg in ("egg",):
            r.skill("fetch", obj=b[egg])
            r.do("crack_egg", target=b[egg], destination=b["bowl"])
            set_down(r, b)
        r.skill("fetch", obj=b["whisk"])
        mix = msg_json(r.do("whisk", target=b["bowl"], strokes=10)["message"])
        self.assertIn("streaky", mix["state"])
        mix = msg_json(r.do("whisk", target=b["bowl"], strokes=120)["message"])
        self.assertIn("smooth", mix["state"])
        self.assertEqual(food(env, mix["mixture"])["kind"], "beaten_egg")


class Whipping(unittest.TestCase):
    def _whites(self, weak):
        env, b, r = scene([
            {"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}", "props": {"_weak_yolk": weak}},
            {"op": "spawn", "var": "egg2", "type": "hen_egg", "on": "{counter}"},
            {"op": "spawn", "var": "whites", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "yolks", "type": "prep_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "whisk", "type": "whisk", "on": "{counter}"}])
        for egg in ("egg", "egg2"):
            r.skill("fetch", obj=b[egg])
            r.do("separate_egg", target=b[egg], whites_to=b["whites"], yolk_to=b["yolks"], method="hand")
            set_down(r, b)
        r.skill("fetch", obj=b["whisk"])
        return env, b, r

    def test_clean_whites_reach_stiff_peaks_then_overwhip(self):
        env, b, r = self._whites(False)
        out = msg_json(r.do("whisk", target=b["whites"], strokes=500, speed="fast")["message"])
        self.assertIn("stiff peaks", out["state"])
        out = msg_json(r.do("whisk", target=b["whites"], strokes=400, speed="fast")["message"])
        self.assertIn("over-whipped", out["state"])

    def test_a_trace_of_yolk_stops_whites_whipping(self):
        env, b, r = self._whites(True)
        out = msg_json(r.do("whisk", target=b["whites"], strokes=600, speed="fast")["message"])
        self.assertNotIn("peaks", out["state"])
        self.assertLessEqual(food(env, out["mixture"])["mix"]["aeration"], 0.25)

    def test_warm_cream_will_not_whip_cold_cream_splits_if_overdone(self):
        env, b, r = scene([
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "cream", "type": "heavy_cream_carton", "on": "{counter}", "props": {"open": True, "temperature_c": 4.0, "_core_c": 4.0}},
            {"op": "spawn", "var": "whisk", "type": "whisk", "on": "{counter}"}])
        r.skill("fetch", obj=b["cream"])
        r.do("transfer", target=b["cream"], destination=b["bowl"], amount_ml=240)
        set_down(r, b)
        r.skill("fetch", obj=b["whisk"])
        out = msg_json(r.do("whisk", target=b["bowl"], strokes=300, speed="fast")["message"])
        self.assertEqual(food(env, out["mixture"])["kind"], "whipped_cream")
        out = msg_json(r.do("whisk", target=b["bowl"], strokes=300, speed="fast")["message"])
        self.assertTrue(food(env, out["mixture"])["split"])
        env2, b2, r2 = scene([
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "cream", "type": "heavy_cream_carton", "on": "{counter}", "props": {"open": True, "temperature_c": 21.0, "_core_c": 21.0}},
            {"op": "spawn", "var": "whisk", "type": "whisk", "on": "{counter}"}])
        r2.skill("fetch", obj=b2["cream"])
        r2.do("transfer", target=b2["cream"], destination=b2["bowl"], amount_ml=240)
        set_down(r2, b2)
        r2.skill("fetch", obj=b2["whisk"])
        out = msg_json(r2.do("whisk", target=b2["bowl"], strokes=400, speed="fast")["message"])
        self.assertLessEqual(food(env2, out["mixture"])["mix"]["aeration"], 0.4)


class Batter(unittest.TestCase):
    def test_muffin_batter_mixed_just_enough_vs_overmixed(self):
        def make():
            env, b, r = scene([
                {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
                {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter}", "props": {"open": True}},
                {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}", "props": {"open": True}},
                {"op": "spawn", "var": "spatula", "type": "silicone_spatula", "on": "{counter}"}])
            r.skill("fetch", obj=b["milk"])
            r.do("transfer", target=b["milk"], destination=b["bowl"], amount_ml=300)
            set_down(r, b)
            r.skill("fetch", obj=b["flour"])
            r.do("transfer", target=b["flour"], destination=b["bowl"], amount_g=250)
            set_down(r, b)
            r.skill("fetch", obj=b["spatula"])
            return env, b, r
        env, b, r = make()
        out = msg_json(r.do("mix", target=b["bowl"], strokes=15, method="fold")["message"])
        self.assertTrue(food(env, out["mixture"])["mix"]["dry_pockets"])
        out = msg_json(r.do("mix", target=b["bowl"], strokes=60, method="stir")["message"])
        f = food(env, out["mixture"])
        self.assertEqual(f["kind"], "batter")
        self.assertFalse(f["mix"]["dry_pockets"])
        self.assertFalse(f["mix"]["overmixed"])
        env2, b2, r2 = make()
        out = msg_json(r2.do("mix", target=b2["bowl"], strokes=400, method="beat", speed="fast")["message"])
        self.assertTrue(food(env2, out["mixture"])["mix"]["overmixed"])

    def test_knead_to_windowpane_extra_flour_dries_it(self):
        env, b, r = scene([{"op": "spawn", "var": "dough", "type": "bread_dough", "on": "{board}"}])
        r.do("knead", target=b["dough"], minutes=8)
        self.assertTrue(food(env, b["dough"])["windowpane"])
        env2, b2, r2 = scene([{"op": "spawn", "var": "dough", "type": "bread_dough", "on": "{board}"}])
        r2.do("knead", target=b2["dough"], minutes=8, added_flour_g=120)
        f = food(env2, b2["dough"])
        self.assertFalse(f["windowpane"])
        self.assertEqual(f.get("texture"), "dry and stiff")

    def test_roll_rotating_vs_warm_pastry_tearing(self):
        env, b, r = scene([{"op": "spawn", "var": "dough", "type": "pie_dough", "on": "{board}"},
                           {"op": "spawn", "var": "pin", "type": "rolling_pin", "on": "{counter}"}])
        r.skill("fetch", obj=b["pin"])
        r.do("roll_dough", target=b["dough"], thickness_mm=3, passes=12, rotate=True)
        sheet = food(env, b["dough"])["sheet"]
        self.assertFalse(sheet["holes"])
        self.assertEqual(sheet["shape"], "round")
        env2, b2, r2 = scene([{"op": "spawn", "var": "dough", "type": "pie_dough", "on": "{board}", "props": {"temperature_c": 24.0}},
                              {"op": "spawn", "var": "pin", "type": "rolling_pin", "on": "{counter}"}])
        r2.skill("fetch", obj=b2["pin"])
        r2.do("roll_dough", target=b2["dough"], thickness_mm=3, passes=12, rotate=False)
        sheet = food(env2, b2["dough"])["sheet"]
        self.assertTrue(sheet["holes"])
        self.assertEqual(sheet["shape"], "oval")

    def test_shape_rolls_by_scale_vs_eyeballing_and_spacing(self):
        env, b, r = scene([{"op": "spawn", "var": "dough", "type": "bread_dough", "on": "{board}"},
                           {"op": "spawn", "var": "tray", "type": "baking_sheet", "on": "{counter2}"},
                           {"op": "spawn", "var": "scale", "type": "kitchen_scale", "on": "{counter}", "props": {"on": True}}])
        out = msg_json(r.do("shape", target=b["dough"], form="rolls", count=6, method="scale", destination=b["tray"], spacing_mm=40)["message"])
        grams = out["grams"]
        self.assertLess((max(grams) - min(grams)) / (sum(grams) / 6), 0.05)
        self.assertTrue(all(food(env, p)["gap_mm"] >= 39 for p in out["pieces"]))
        env2, b2, r2 = scene([{"op": "spawn", "var": "dough", "type": "bread_dough", "on": "{board}"},
                              {"op": "spawn", "var": "tray", "type": "baking_sheet", "on": "{counter2}"}])
        out = msg_json(r2.do("shape", target=b2["dough"], form="rolls", count=6, destination=b2["tray"], spacing_mm=5)["message"])
        grams = out["grams"]
        self.assertGreater((max(grams) - min(grams)) / (sum(grams) / 6), 0.3)
        self.assertTrue(any(food(env2, p)["gap_mm"] < 10 for p in out["pieces"]))


class Measuring(unittest.TestCase):
    def _flour_scene(self):
        return scene([
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "cup", "type": "measuring_cup_1", "on": "{counter}"},
            {"op": "spawn", "var": "spoon", "type": "table_spoon", "on": "{counter}"}])

    def test_spoon_and_level_gives_125_g_per_cup_dipping_packs_it(self):
        env, b, r = self._flour_scene()
        r.skill("fetch", obj=b["cup"])
        r.skill("fetch", obj=b["spoon"])
        r.do("scoop", target=b["flour"], tool=b["cup"], technique="spoon_and_level", count=2, destination=b["bowl"])
        self.assertAlmostEqual(flour_in(env, b["bowl"]), 250.0, delta=4.0)
        env2, b2, r2 = self._flour_scene()
        r2.skill("fetch", obj=b2["cup"])
        r2.do("scoop", target=b2["flour"], tool=b2["cup"], technique="dip_and_sweep", count=2, destination=b2["bowl"])
        self.assertGreater(flour_in(env2, b2["bowl"]), 290.0)
        res = step(env2, "scoop", b2["flour"], tool=b2["cup"], technique="spoon_and_level", destination=b2["bowl"])
        self.assertFalse(res["ok"])           # spoon-and-level needs a spoon in the other hand

    def test_wet_scoop_clumps_and_scoop_carries_allergens(self):
        env, b, r = scene([
            {"op": "spawn", "var": "pb", "type": "peanut_butter_jar", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "tbsp", "type": "measuring_spoon_tbsp", "on": "{counter}", "props": {"wetness": 0.5}},
            {"op": "spawn", "var": "bowl", "type": "small_mixing_bowl", "on": "{counter}"}])
        r.skill("fetch", obj=b["tbsp"])
        out = msg_json(r.do("scoop", target=b["flour"], tool=b["tbsp"], destination=b["bowl"])["message"])
        self.assertIn("clumped", out["warning"])
        r.do("scoop", target=b["pb"], tool=b["tbsp"], destination=b["bowl"])
        r.do("scoop", target=b["flour"], tool=b["tbsp"], destination=b["bowl"])
        contam = ent(env, b["flour"]).get("contamination", {})
        self.assertGreater(contam.get("allergen_peanut", 0.0), 0.05)

    def test_scale_tare_units_and_overload(self):
        env, b, r = scene([
            {"op": "spawn", "var": "scale", "type": "kitchen_scale", "on": "{counter}", "props": {"on": True, "settings": {"unit": "g"}}},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter}", "props": {"open": True}}])
        r.skill("put", obj=b["bowl"], dest=b["scale"])
        out = msg_json(r.do("weigh", target=b["scale"])["message"])
        self.assertEqual(out["display"], "500 g")              # the bowl's own mass shows until tared
        r.do("weigh", target=b["scale"], tare=True)
        self.assertEqual(ent(env, b["scale"])["display"], "0 g")
        r.skill("fetch", obj=b["flour"])
        r.do("transfer", target=b["flour"], destination=b["bowl"], until_reading=300, rate="slow")
        self.assertAlmostEqual(flour_in(env, b["bowl"]), 300.0, delta=3.0)
        r.do("weigh", target=b["scale"], unit="oz")
        self.assertTrue(ent(env, b["scale"])["display"].endswith("oz"))
        reading = float(ent(env, b["scale"])["display"].split()[0])
        self.assertAlmostEqual(reading, 300.0 / 28.35, delta=0.3)
        heavy = env.world.spawn("generic_item", b["scale"], label="cast iron weight", mass_kg=6.0, portable=True, size_m=[0.1, 0.1, 0.1])
        env.world.mut(heavy).update(relation="on", spos=[0.2, 0.2])
        self.assertEqual(msg_json(r.do("weigh", target=b["scale"])["message"])["display"], "O-Ld")

    def test_portion_by_scale_is_even_eyeballing_is_not(self):
        def run(method):
            env, b, r = scene([
                {"op": "spawn", "var": "beef", "type": "ground_beef", "on": "{board}", "props": {"food": {"qty_g": 600.0}, "mass_kg": 0.6}},
                {"op": "spawn", "var": "b1", "type": "zip_top_bag", "on": "{counter}"},
                {"op": "spawn", "var": "b2", "type": "zip_top_bag", "on": "{counter}"},
                {"op": "spawn", "var": "b3", "type": "zip_top_bag", "on": "{counter}"},
                {"op": "spawn", "var": "b4", "type": "zip_top_bag", "on": "{counter}"},
                {"op": "spawn", "var": "scale", "type": "kitchen_scale", "on": "{counter}", "props": {"on": True}}])
            out = msg_json(r.do("portion", target=b["beef"], into=[b["b1"], b["b2"], b["b3"], b["b4"]], method=method, press_air=True)["message"])
            return [p["g"] for p in out["portions"]], env, b
        even, env, b = run("scale")
        self.assertLess(max(even) - min(even), 0.05 * 150)
        self.assertTrue(ent(env, b["b1"])["air_removed"])
        uneven, _, _ = run("eyeball")
        self.assertGreater(max(uneven) - min(uneven), 0.3 * 150)


def flour_in(env, bowl):
    return sum(food(env, e)["qty_g"] for e, r in env.world.s["entities"].items()
               if r.get("location") == bowl and (r.get("food") or {}).get("kind") == "all_purpose_flour")


class Transfers(unittest.TestCase):
    def test_scraping_gets_it_all_pouring_leaves_residue_and_small_box_overflows(self):
        env, b, r = scene([
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}"},
            {"op": "spawn", "var": "chili", "type": "chili", "in": "{pot}", "props": {"food": {"qty_g": 900.0}, "mass_kg": 0.9, "temperature_c": 30.0}},
            {"op": "spawn", "var": "box", "type": "storage_container_medium", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "spatula", "type": "silicone_spatula", "on": "{counter}"}])
        r.skill("fetch", obj=b["spatula"])
        out = msg_json(r.do("transfer", target=b["pot"], destination=b["box"], method="scrape", tool=b["spatula"])["message"])
        self.assertLess(out["residue_left_g"], 15.0)
        self.assertAlmostEqual(ent(env, b["box"])["fill_level"], 0.75, delta=0.05)
        env2, b2, r2 = scene([
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}"},
            {"op": "spawn", "var": "chili", "type": "chili", "in": "{pot}", "props": {"food": {"qty_g": 900.0}, "mass_kg": 0.9, "temperature_c": 30.0}},
            {"op": "spawn", "var": "small", "type": "storage_container_small", "on": "{counter}", "props": {"open": True}}])
        r2.skill("fetch", obj=b2["pot"])
        out = msg_json(r2.do("transfer", target=b2["pot"], destination=b2["small"], method="pour")["message"])
        self.assertGreater(out["residue_left_g"], 40.0)          # thick chili clings without scraping
        self.assertGreater(out.get("overflow_g", 0), 300.0)       # 0.5 L box
        self.assertGreater(env2.world.s["stats"]["spills"], 0)

    def test_boiling_liquid_cracks_cold_glass_and_non_food_bucket_taints(self):
        env, b, r = scene([
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}"},
            {"op": "spawn", "var": "soup", "type": "soup", "in": "{pot}", "props": {"food": {"qty_g": 600.0}, "mass_kg": 0.6, "temperature_c": 95.0, "_core_c": 95.0}},
            {"op": "spawn", "var": "jar", "type": "mason_jar", "on": "{counter}", "props": {"open": True, "temperature_c": 3.0, "fixed_temperature": True}},
            {"op": "spawn", "var": "ladle", "type": "ladle", "on": "{counter}"}])
        r.skill("fetch", obj=b["ladle"])
        out = msg_json(r.do("transfer", target=b["pot"], destination=b["jar"], amount_ml=300, method="ladle", tool=b["ladle"])["message"])
        self.assertIn("cracked", out["warning"])
        self.assertTrue(ent(env, b["jar"])["broken"])
        self.assertGreaterEqual(env.world.s["stats"]["damage"], 1)
        env2, b2, r2 = scene([
            {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "bucket", "type": "storage_bin", "on": "{counter}",
             "props": {"open": True, "food_safe": False, "label": "old paint bucket", "size_m": [0.3, 0.3, 0.3]}}])
        r2.skill("fetch", obj=b2["flour"])
        out = msg_json(r2.do("transfer", target=b2["flour"], destination=b2["bucket"], amount_g=500)["message"])
        self.assertIn("food-safe", out["warning"])
        self.assertGreater(ent(env2, out["new"])["contamination"]["chemical"], 0.1)

    def test_fat_down_the_drain_fails_any_task(self):
        setup = [{"op": "spawn", "var": "pan", "type": "frying_pan", "on": "{counter}"},
                 {"op": "spawn", "var": "grease", "type": "food_item", "in": "{pan}",
                  "props": {"label": "bacon grease", "food": {"kind": "bacon_grease", "cat": "oil", "phase": "liquid", "qty_g": 80.0, "density": 0.9}, "mass_kg": 0.08}},
                 {"op": "spawn", "var": "jar", "type": "mason_jar", "on": "{counter}", "props": {"open": True}}]
        env, b, r = scene(setup, kitchen_args={"need": ["sink"]})
        r.skill("fetch", obj=b["pan"])
        r.do("transfer", target=b["pan"], destination=b["jar"])
        self.assertNotIn("food.fat_down_drain", env.evaluate()["failures"])
        env2, b2, r2 = scene(setup, kitchen_args={"need": ["sink"]})
        r2.skill("fetch", obj=b2["pan"])
        r2.skill("approach", target=b2["sink"])
        r2.do("transfer", target=b2["pan"], destination=b2["sink"])
        self.assertIn("food.fat_down_drain", env2.evaluate()["failures"])

    def test_strain_keeps_stock_over_a_bowl_loses_it_over_the_sink(self):
        def run(dest_key):
            env, b, r = scene([
                {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}"},
                {"op": "spawn", "var": "stock", "type": "food_item", "in": "{pot}",
                 "props": {"label": "chicken stock", "food": {"kind": "stock", "cat": "prepared", "phase": "liquid", "qty_g": 1200.0}, "mass_kg": 1.2, "temperature_c": 40.0}},
                {"op": "spawn", "var": "bones", "type": "food_item", "in": "{pot}",
                 "props": {"label": "chicken carcass", "food": {"kind": "chicken_bones", "cat": "scraps", "phase": "solid", "qty_g": 300.0}, "mass_kg": 0.3, "size_m": [0.12, 0.1, 0.08]}},
                {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
                {"op": "spawn", "var": "sieve", "type": "fine_mesh_sieve", "on": "{counter}"}], kitchen_args={"need": ["sink"]})
            r.skill("put", obj=b["sieve"], dest=b[dest_key], relation="in")
            r.skill("fetch", obj=b["pot"])
            r.skill("approach", target=b[dest_key])
            out = msg_json(r.do("strain", target=b["pot"], destination=b[dest_key], tool=b["sieve"])["message"])
            return env, b, out
        env, b, out = run("bowl")
        kept = [e for e, rec in env.world.s["entities"].items() if rec.get("location") == b["bowl"] and (rec.get("food") or {}).get("kind") == "stock"]
        self.assertTrue(kept)
        self.assertIn(b["bones"], out["solids_in_strainer"])
        env2, b2, out2 = run("sink")
        self.assertTrue(out2["to_drain"])
        self.assertFalse([e for e, rec in env2.world.s["entities"].items() if (rec.get("food") or {}).get("kind") == "stock"])

    def test_strained_liquid_keeps_its_provenance(self):
        # kernel v2.9 contents_from: the reserved and the strained water came from wherever the pot's water came from
        # (chains stay one deep); the emptied pot no longer holds anything from elsewhere
        env, b, r = scene([
            {"op": "spawn", "var": "kettle", "type": "stovetop_kettle", "on": "{counter}"},
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}",
             "props": {"liquid": "water", "liquid_l": 1.0, "contents_from": "{kettle}"}},
            {"op": "spawn", "var": "bones", "type": "food_item", "in": "{pot}",
             "props": {"label": "chicken carcass", "food": {"kind": "chicken_bones", "cat": "scraps", "phase": "solid", "qty_g": 300.0}, "mass_kg": 0.3, "size_m": [0.12, 0.1, 0.08]}},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "cup", "type": "liquid_measuring_cup", "on": "{counter}"},
            {"op": "spawn", "var": "sieve", "type": "fine_mesh_sieve", "on": "{counter}"}], kitchen_args={"need": ["sink"]})
        r.skill("put", obj=b["sieve"], dest=b["bowl"], relation="in")
        r.skill("fetch", obj=b["pot"])
        r.skill("approach", target=b["bowl"])
        r.do("strain", target=b["pot"], destination=b["bowl"], tool=b["sieve"], reserve_ml=200, reserve_to=b["cup"])
        self.assertAlmostEqual(ent(env, b["cup"])["liquid_l"], 0.2, places=3)
        self.assertAlmostEqual(ent(env, b["bowl"])["liquid_l"], 0.8, places=3)
        self.assertEqual(ent(env, b["cup"]).get("contents_from"), b["kettle"])
        self.assertEqual(ent(env, b["bowl"]).get("contents_from"), b["kettle"])
        self.assertNotIn("contents_from", ent(env, b["pot"]))

    def test_bulk_food_takes_the_shape_of_its_container(self):
        # 1.6 L of chili scraped into a 22 x 16 cm box fills it wall to wall 4.5 cm deep, so the lid closes; the old
        # 20 x 20 cm mound stood on its side 20 cm tall in the 11 cm box and no lid could close over it
        from house_sim.systems.food import model as M
        env, b, r = scene([
            {"op": "spawn", "var": "pot", "type": "stock_pot", "on": "{counter}"},
            {"op": "spawn", "var": "chili", "type": "chili", "in": "{pot}",
             "props": {"food": {"qty_g": 1600.0}, "mass_kg": 1.6, "volume_l": 1.6, "temperature_c": 30.0, "size_m": [0.25, 0.25, 0.04]}},
            {"op": "spawn", "var": "box", "type": "storage_container_large", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "spatula", "type": "silicone_spatula", "on": "{counter}"}])
        r.skill("fetch", obj=b["spatula"])
        r.skill("approach", target=b["pot"])
        out = msg_json(r.do("transfer", target=b["pot"], destination=b["box"], method="scrape", tool=b["spatula"])["message"])
        inner = ent(env, b["box"])["inner_size_m"]
        size = ent(env, out["new"])["size_m"]
        self.assertEqual(size[:2], inner[:2])
        self.assertAlmostEqual(size[0] * size[1] * size[2] * 1000.0, M.volume_ml(ent(env, out["new"])) / 1000.0, delta=0.02)
        self.assertLess(size[2], inner[2])
        self.assertTrue(r.do("close", target=b["box"])["ok"])
        # a pile of diced carrot keeps its mound in a wide bowl; too much chili for the box rises above the rim
        carrot = {"food": {"kind": "carrot", "phase": "pieces", "qty_g": 120.0, "density": 0.6}, "relation": "in"}
        bowl = {"inner_size_m": [0.28, 0.28, 0.11]}
        self.assertLess(M.vessel_size(carrot, bowl)[0], 0.28)
        flood = {"food": {"kind": "chili", "phase": "paste", "qty_g": 4500.0, "density": 1.05}, "relation": "in"}
        self.assertGreater(M.vessel_size(flood, {"inner_size_m": inner})[2], inner[2])
        self.assertIsNone(M.vessel_size(dict(flood, package="tub"), {"inner_size_m": inner}))

    def test_sifting_removes_lumps_and_lightens(self):
        env, b, r = scene([
            {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "sifter", "type": "flour_sifter", "on": "{counter}"}])
        r.skill("fetch", obj=b["sifter"])
        out = msg_json(r.do("sift", target=b["flour"], destination=b["bowl"], amount_g=300)["message"])
        f = food(env, out["sifted"])
        self.assertTrue(f["sifted"])
        self.assertEqual(f["lumps"], 0)
        self.assertLess(f["density"], 0.5)


class SeasonCoat(unittest.TestCase):
    def test_loose_shaker_lid_dumps_and_unlabeled_jar_is_really_sugar(self):
        env, b, r = scene([
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}"},
            {"op": "spawn", "var": "soup", "type": "soup", "in": "{pot}", "props": {"food": {"qty_g": 1000.0}, "mass_kg": 1.0}},
            {"op": "spawn", "var": "shaker", "type": "salt_shaker", "on": "{counter}", "props": {"_loose_lid": True}},
            {"op": "spawn", "var": "jar", "type": "food_canister", "on": "{counter}", "props": {"open": True, "label": "white jar"}},
            {"op": "spawn", "var": "sugar", "type": "food_item", "in": "{jar}",
             "props": {"label": "white granules", "secret_fields": ["food", "contamination"],
                       "food": {"kind": "sugar", "cat": "sugar", "phase": "granular", "qty_g": 400.0, "density": 0.845}, "mass_kg": 0.4}},
            {"op": "spawn", "var": "tsp", "type": "measuring_spoon_tsp", "on": "{counter}"}])
        r.skill("fetch", obj=b["shaker"])
        out = msg_json(r.do("season", target=b["pot"], seasoning=b["shaker"], shakes=5)["message"])
        self.assertIn("dumped", out["warning"])
        self.assertGreater(food(env, b["soup"])["composition"]["salt"], 14.0)
        env.observe_on_step = True
        obs = env.observe()
        self.assertNotIn("food", obs["visible_entities"][b["sugar"]])     # unlabeled jar: identity hidden
        set_down(r, b, b["shaker"])
        r.skill("fetch", obj=b["tsp"])
        r.do("season", target=b["pot"], seasoning=b["sugar"], tool=b["tsp"])
        self.assertGreater(food(env, b["soup"])["composition"]["sugar"], 3.0)

    def test_breading_order_flour_egg_crumbs_vs_skipping_flour(self):
        def run(skip_flour):
            env, b, r = scene([
                {"op": "spawn", "var": "chicken", "type": "chicken_breast", "on": "{board}"},
                {"op": "spawn", "var": "flour_bowl", "type": "prep_bowl", "on": "{counter}"},
                {"op": "spawn", "var": "flour", "type": "food_item", "in": "{flour_bowl}",
                 "props": {"food": {"kind": "all_purpose_flour", "cat": "flour", "phase": "powder", "qty_g": 100.0, "density": 0.55}, "mass_kg": 0.1, "allergens": ["wheat"]}},
                {"op": "spawn", "var": "egg_bowl", "type": "prep_bowl", "on": "{counter}"},
                {"op": "spawn", "var": "wash", "type": "egg_wash", "in": "{egg_bowl}"},
                {"op": "spawn", "var": "crumb_bowl", "type": "prep_bowl", "on": "{counter}"},
                {"op": "spawn", "var": "crumbs", "type": "food_item", "in": "{crumb_bowl}",
                 "props": {"food": {"kind": "breadcrumbs", "cat": "grain", "phase": "granular", "qty_g": 100.0, "density": 0.47}, "mass_kg": 0.1}}])
            r.skill("fetch", obj=b["chicken"])
            if not skip_flour:
                r.do("coat", target=b["chicken"], coating=b["flour_bowl"], method="dredge")
            r.do("coat", target=b["chicken"], coating=b["egg_bowl"], method="dip")
            r.do("coat", target=b["chicken"], coating=b["crumb_bowl"], method="press")
            return env, b, food(env, b["chicken"])["layers"]
        env, b, layers = run(False)
        self.assertEqual([l["kind"] for l in layers], ["all_purpose_flour", "beaten_egg", "breadcrumbs"])
        self.assertTrue(all(l["coverage"] >= 0.9 for l in layers))
        self.assertGreater(ent(env, b["flour"]).get("contamination", {}).get("raw_meat", 0), 0.1)   # leftover dredge is raw-meat tainted
        env2, b2, layers2 = run(True)
        self.assertLess(layers2[0]["coverage"], 0.6)

    def test_spreading_cold_butter_tears_bread_soft_butter_covers(self):
        def run(temp):
            env, b, r = scene([
                {"op": "spawn", "var": "bread", "type": "bread_slice", "on": "{board}"},
                {"op": "spawn", "var": "butter", "type": "butter_unsalted", "on": "{counter}",
                 "props": {"open": True, "temperature_c": temp, "_core_c": temp}},
                {"op": "spawn", "var": "knife", "type": "butter_knife", "on": "{counter}"}])
            r.skill("fetch", obj=b["knife"])
            r.do("coat", target=b["bread"], coating=b["butter"], method="spread", amount_g=8)
            return food(env, b["bread"])
        cold = run(4.0)
        self.assertTrue(cold.get("torn"))
        soft = run(19.0)
        self.assertFalse(soft.get("torn", False))
        self.assertGreaterEqual(soft["layers"][0]["coverage"], 0.9)

    def test_acid_marinade_turns_fish_mushy_after_an_hour(self):
        env, b, r = scene([
            {"op": "spawn", "var": "fish", "type": "salmon_fillet", "on": "{board}", "props": {"temperature_c": 4.0, "_core_c": 4.0}},
            {"op": "spawn", "var": "jug", "type": "small_mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "marinade", "type": "marinade", "in": "{jug}"},
            {"op": "spawn", "var": "bag", "type": "zip_top_bag", "on": "{counter}"}], kitchen_args={"need": ["fridge"]})
        r.skill("fetch", obj=b["fish"])
        out = msg_json(r.do("marinate", target=b["fish"], marinade=b["jug"], destination=b["bag"])["message"])
        self.assertTrue(out["acid"])
        r.do("close", target=b["bag"])
        r.skill("put", obj=b["bag"], dest=b["fridge"], relation="in", shelf=b["fridge_bottom"])
        r.do("wait", seconds=1800)
        self.assertNotEqual(food(env, b["fish"]).get("texture"), "mushy")
        r.do("wait", seconds=3600)
        self.assertEqual(food(env, b["fish"])["texture"], "mushy")
        self.assertFalse(ent(env, b["fish"]).get("_abused", False))
        marinade = [e for e, rec in env.world.s["entities"].items() if rec.get("location") == b["bag"] and (rec.get("food") or {}).get("raw_marinade")]
        self.assertTrue(marinade)          # raw-fish marinade is not ready to eat


class ProduceTools(unittest.TestCase):
    def test_grate_to_the_nub_cuts_knuckles_stopping_is_safe(self):
        def run(stop):
            env, b, r = scene([
                {"op": "spawn", "var": "cheese", "type": "cheddar_block", "on": "{counter}", "props": {"package": None, "temperature_c": 5.0}},
                {"op": "spawn", "var": "grater", "type": "box_grater", "on": "{counter}"}])
            r.skill("fetch", obj=b["grater"], grip="handle")
            r.skill("fetch", obj=b["cheese"])
            out = msg_json(r.do("grate", target=b["cheese"], destination=b["board"], side="coarse", stop_at_mm=stop)["message"])
            return env, out
        env, out = run(25)
        self.assertEqual(env.world.s["stats"]["damage"], 0)
        self.assertGreaterEqual(out["remaining_mm"], 24)
        env2, out2 = run(5)
        self.assertEqual(env2.world.s["stats"]["damage"], 1)

    def test_zest_before_juice_and_seeds_fall_in_without_a_strainer(self):
        env, b, r = scene([
            {"op": "spawn", "var": "lemon", "type": "lemon", "on": "{board}"},
            {"op": "spawn", "var": "plane", "type": "microplane", "on": "{counter}"},
            {"op": "spawn", "var": "knife", "type": "paring_knife", "on": "{counter}"},
            {"op": "spawn", "var": "reamer", "type": "citrus_reamer", "on": "{counter}"},
            {"op": "spawn", "var": "cup", "type": "prep_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "cup2", "type": "prep_bowl", "on": "{counter}"}])
        r.skill("fetch", obj=b["plane"], grip="handle")
        r.skill("fetch", obj=b["lemon"])
        out = msg_json(r.do("zest", target=b["lemon"], destination=b["cup"], pressure="light")["message"])
        self.assertLessEqual(out["pith_frac"], 0.05)
        r.skill("put", obj=b["lemon"], dest=b["board"])
        set_down(r, b, b["plane"])
        knife_ready(r, b)
        r.do("cut", target=b["lemon"], style="halve", axis="crosswise")
        set_down(r, b, b["knife"])
        r.skill("fetch", obj=b["reamer"])
        out = msg_json(r.do("juice", target=b["lemon"], destination=b["cup2"])["message"])
        self.assertGreater(out["seeds_in_juice"], 0)
        self.assertGreater(out["ml"], 30)
        res = step(env, "zest", b["lemon"], destination=b["cup"])
        self.assertFalse(res["ok"])                  # juiced halves are too limp to zest

    def test_crushing_in_a_closed_bag_vs_open_bag_and_glass_hammer(self):
        env, b, r = scene([
            {"op": "spawn", "var": "bag", "type": "zip_top_bag", "on": "{counter}"},
            {"op": "spawn", "var": "crackers", "type": "food_item", "in": "{bag}",
             "props": {"food": {"kind": "crackers", "cat": "snack", "phase": "pieces", "qty_g": 100.0, "density": 0.4}, "mass_kg": 0.1, "size_m": [0.1, 0.1, 0.05]}},
            {"op": "spawn", "var": "pin", "type": "rolling_pin", "on": "{counter}"},
            {"op": "spawn", "var": "jar", "type": "mason_jar", "on": "{counter}"}])
        r.do("close", target=b["bag"])
        r.skill("fetch", obj=b["pin"])
        r.do("crush", target=b["crackers"], passes=10)
        self.assertEqual(env.world.s["stats"]["spills"], 0)
        self.assertEqual(food(env, b["crackers"])["cut"], "crushed")
        r.do("open", target=b["bag"])
        r.do("crush", target=b["crackers"], passes=4)
        self.assertGreater(env.world.s["stats"]["spills"], 0)
        set_down(r, b, b["pin"])
        r.skill("fetch", obj=b["jar"])
        out = msg_json(r.do("crush", target=b["crackers"], tool=b["jar"])["message"])
        self.assertIn("shattered", out["result"])
        self.assertGreaterEqual(env.world.s["stats"]["breakages"], 1)

    def test_grinder_keeps_spice_flavour_and_open_lid_sprays(self):
        env, b, r = scene([
            {"op": "spawn", "var": "grinder", "type": "spice_grinder", "on": "{counter}", "props": {"open": True, "designated_for": "coffee"}},
            {"op": "spawn", "var": "cumin", "type": "food_item", "in": "{grinder}",
             "props": {"food": {"kind": "cumin", "cat": "spice", "phase": "granular", "qty_g": 20.0, "density": 0.5}, "mass_kg": 0.02, "volume_l": 0.04}}])
        res = step(env, "grind", b["grinder"], setting="fine")
        self.assertTrue(res["ok"])
        self.assertGreater(env.world.s["stats"]["spills"], 0)     # lid left open
        self.assertGreater(ent(env, b["grinder"])["contamination"].get("flavor_cumin", 0.0), 0.5)
        r.do("close", target=b["grinder"])
        out = msg_json(r.do("grind", target=b["grinder"], setting="fine")["message"])
        self.assertLess(out["particle_mm"], 0.5)

    def test_mashing_hot_potatoes_briefly_vs_cold_overworked_glue(self):
        def run(temp, strokes):
            env, b, r = scene([
                {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}", "props": {"temperature_c": temp}},
                {"op": "spawn", "var": "spuds", "type": "potato", "in": "{pot}",
                 "props": {"food": {"cooked": True, "qty_g": 600.0}, "mass_kg": 0.6, "temperature_c": temp, "_core_c": temp}},
                {"op": "spawn", "var": "masher", "type": "potato_masher", "on": "{counter}"}])
            r.skill("fetch", obj=b["masher"])
            return msg_json(r.do("mash", target=b["pot"], strokes=strokes)["message"])
        good = run(85.0, 60)
        self.assertLess(good["lump_frac"], 0.05)
        self.assertLess(good["glue"], 0.1)
        bad = run(25.0, 250)
        self.assertGreater(bad["glue"], 0.5)
        env, b, r = scene([
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}"},
            {"op": "spawn", "var": "spuds", "type": "potato", "in": "{pot}"},
            {"op": "spawn", "var": "masher", "type": "potato_masher", "on": "{counter}"}])
        r.skill("fetch", obj=b["masher"])
        self.assertFalse(step(env, "mash", b["pot"])["ok"])        # raw potatoes do not mash


class Proteins(unittest.TestCase):
    def test_carve_after_resting_vs_straight_off_the_heat(self):
        def run(rest):
            env, b, r = scene([
                {"op": "spawn", "var": "roast", "type": "roast_beef", "on": "{board}", "props": {"temperature_c": 55.0, "_core_c": 55.0, "_heat_off_s": 0.0}},
                {"op": "spawn", "var": "knife", "type": "carving_knife", "on": "{counter}"}])
            if rest:
                r.do("wait", seconds=900)
            knife_ready(r, b)
            r.do("carve", target=b["roast"], direction="across_grain", thickness_mm=6)
            return food(env, b["roast"])
        rested = run(True)
        self.assertTrue(rested["rested_before_carving"])
        self.assertEqual(rested["texture"], "tender")
        hasty = run(False)
        self.assertEqual(hasty["juiciness"], "dry")

    def test_shuck_oysters_needs_hand_protection(self):
        env, b, r = scene([
            {"op": "spawn", "var": "oysters", "type": "oysters", "on": "{board}", "props": {"temperature_c": 4.0}},
            {"op": "spawn", "var": "knife", "type": "oyster_knife", "on": "{counter}"},
            {"op": "spawn", "var": "glove", "type": "kitchen_cut_glove", "on": "{counter}"}])
        knife_ready(r, b)
        r.do("shuck", target=b["oysters"], count=1)
        self.assertEqual(env.world.s["stats"]["damage"], 1)
        set_down(r, b, b["knife"])
        r.skill("fetch", obj=b["glove"])
        r.do("wear", target=b["glove"])
        knife_ready(r, b)
        before = env.world.s["stats"]["damage"]
        out = msg_json(r.do("shuck", target=b["oysters"], count=2, method="hinge")["message"])
        self.assertEqual(env.world.s["stats"]["damage"], before)
        self.assertEqual(out["shell_fragments"], 0)

    def test_fillet_with_flexible_knife_yields_more(self):
        def run(knife):
            env, b, r = scene([
                {"op": "spawn", "var": "fish", "type": "whole_fish", "on": "{board}", "props": {"temperature_c": 3.0}},
                {"op": "spawn", "var": "knife", "type": knife, "on": "{counter}"}])
            knife_ready(r, b)
            return msg_json(r.do("fillet", target=b["fish"])["message"])["yield_frac"]
        self.assertGreater(run("fillet_knife"), run("chef_knife"))


class ColdChain(unittest.TestCase):
    def test_deep_pot_cools_too_slowly_shallow_uncovered_box_passes(self):
        def run(container, liters):
            env, b, r = scene([   # FDA: cool in shallow pans, loosely covered, until cold
                {"op": "spawn", "var": "box", "type": container, "on": "{counter}", "props": {"open": True}},
                {"op": "spawn", "var": "chili", "type": "chili", "in": "{box}",
                 "props": {"food": {"qty_g": liters * 1000.0}, "mass_kg": liters, "volume_l": liters, "temperature_c": 59.0, "_core_c": 59.0,
                           "size_m": [0.18, 0.12, 0.05], "_hot_since": 0.0}}], kitchen_args={"need": ["fridge"]})
            r.skill("open", target=b["fridge"])
            r.skill("put", obj=b["box"], dest=b["fridge"], relation="in", shelf=b["fridge_mid"])
            r.skill("close", target=b["fridge"])
            r.do("wait", seconds=3600 * 7)
            return env, b
        env, b = run("storage_container_medium", 1.0)
        self.assertTrue(ent(env, b["chili"])["_cooling"]["ok"])
        self.assertFalse(ent(env, b["chili"]).get("_unsafe", False))
        env2, b2 = run("stock_pot", 6.0)
        self.assertTrue(ent(env2, b2["chili"]).get("_unsafe"))
        self.assertEqual(ent(env2, b2["chili"])["_unsafe_reason"], "slow_cooling")

    def test_ice_bath_cools_a_pot_fast(self):
        env, b, r = scene([
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}", "props": {"liquid": "water", "liquid_l": 1.5}},
            {"op": "spawn", "var": "ice", "type": "bag_of_ice", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "pot", "type": "small_mixing_bowl", "on": "{counter}", "props": {"temperature_c": 45.0}},
            {"op": "spawn", "var": "soup", "type": "soup", "in": "{pot}",
             "props": {"food": {"qty_g": 800.0}, "mass_kg": 0.8, "volume_l": 0.8, "temperature_c": 50.0, "_core_c": 55.0, "_hot_since": 0.0}}])
        r.skill("fetch", obj=b["ice"])
        r.do("transfer", target=b["ice"], destination=b["bowl"], amount_g=1500)
        set_down(r, b, b["ice"])
        self.assertTrue(ent(env, b["bowl"])["ice_bath"])
        r.skill("put", obj=b["pot"], dest=b["bowl"], relation="in")
        r.do("wait", seconds=1800)
        self.assertLess(ent(env, b["soup"])["temperature_c"], 21.0)
        self.assertIsNotNone(ent(env, b["soup"])["_cooling"]["t21_s"])

    def test_time_out_on_counter_makes_food_unsafe_and_breaks_cold_chain(self):
        env, b, r = scene([], kitchen_args={"need": ["fridge"]})
        milk = env.world.spawn("milk_carton", b["fridge"], relation="in", shelf=0, temperature_c=4.0, _core_c=4.0)
        env.world.commit()
        env._baseline = __import__("house_sim.kernel.world", fromlist=["clone"]).clone(env.world.s)
        r.skill("fetch", obj=milk)
        r.skill("close", target=b["fridge"])
        r.skill("put", obj=milk, dest=b["counter"])      # the counter need not be within reach of the fridge
        r.do("wait", seconds=3600)
        self.assertFalse(ent(env, milk).get("_abused", False))
        r.do("wait", seconds=3 * 3600)
        self.assertTrue(ent(env, milk)["_abused"])
        self.assertEqual(ent(env, milk)["_food_status"]["class"], "unsafe")
        self.assertIn("food.cold_chain_break", env.evaluate()["failures"])

    def test_ice_cream_softens_on_the_counter_not_in_the_freezer(self):
        env, b, r = scene([{"op": "spawn", "var": "ice_cream", "type": "ice_cream_tub", "on": "{counter}", "props": {"temperature_c": -16.0, "_core_c": -16.0}}],
                          kitchen_args={"need": ["freezer"]})
        r.do("wait", seconds=900)
        self.assertTrue(food(env, b["ice_cream"])["softened"])
        env2, b2, r2 = scene([{"op": "spawn", "var": "ice_cream", "type": "ice_cream_tub", "on": "{counter}", "props": {"temperature_c": -16.0, "_core_c": -16.0}}],
                             kitchen_args={"need": ["freezer"]})
        r2.skill("put", obj=b2["ice_cream"], dest=b2["freezer"], relation="in", shelf=0)
        r2.skill("close", target=b2["freezer"])
        r2.do("wait", seconds=900)
        self.assertFalse(food(env2, b2["ice_cream"]).get("softened", False))

    def test_frozen_pack_thaws_in_fridge_with_latent_heat_and_drips_onto_lettuce_below(self):
        env, b, r = scene([
            {"op": "spawn", "var": "chicken", "type": "frozen_chicken_pack", "in": "{fridge}", "shelf": "{fridge_top}"},
            {"op": "spawn", "var": "lettuce", "type": "lettuce", "in": "{fridge}", "shelf": "{fridge_bottom}", "props": {"temperature_c": 4.0}}],
            kitchen_args={"need": ["fridge"]})
        r.do("wait", seconds=3600 * 6)
        f = food(env, b["chicken"])
        self.assertGreater(f["ice"], 0.05)              # 1 kg still partly frozen after 6 h in the fridge
        self.assertLess(f["ice"], 0.99)
        self.assertLess(ent(env, b["chicken"])["temperature_c"], 2.0)
        self.assertTrue(ent(env, b["chicken"]).get("_leaking"))
        self.assertGreater(ent(env, b["lettuce"]).get("contamination", {}).get("raw_meat", 0.0), 0.05)

    def test_insulated_lunch_box_with_ice_pack_keeps_yogurt_cold(self):
        def run(with_pack):
            setup = [{"op": "spawn", "var": "box", "type": "lunch_box", "on": "{counter}", "props": {"open": True}},
                     {"op": "spawn", "var": "yogurt", "type": "yogurt_snack_cup", "in": "{box}", "props": {"temperature_c": 4.0, "_core_c": 4.0}}]
            if with_pack:      # USDA: use at least two frozen ice packs
                setup.append({"op": "spawn", "var": "pack", "type": "freezer_ice_pack", "in": "{box}"})
                setup.append({"op": "spawn", "var": "pack2", "type": "freezer_ice_pack", "in": "{box}"})
            env, b, r = scene(setup)
            r.do("close", target=b["box"])
            r.do("wait", seconds=4 * 3600)
            return ent(env, b["yogurt"])
        cold = run(True)
        self.assertLess(cold["temperature_c"], 7.0)
        self.assertLess(cold.get("_abuse_s", 0), 3600)
        warm = run(False)
        self.assertGreater(warm["temperature_c"], 15.0)
        self.assertTrue(warm.get("_abused"))


class Packaging(unittest.TestCase):
    def _two_ketchups(self):
        return scene([
            {"op": "helper", "name": "food.dated", "args": {"type": "ketchup_bottle", "days": 20, "var": "old", "on": "{counter}",
                                                            "opened_days_ago": 30, "props": {"food": {"qty_g": 150.0}}}},
            {"op": "helper", "name": "food.dated", "args": {"type": "ketchup_bottle", "days": 200, "var": "new", "on": "{counter}",
                                                            "opened_days_ago": 2, "props": {"food": {"qty_g": 400.0}}}},
            {"op": "spawn", "var": "mustard", "type": "mustard_bottle", "on": "{counter}", "props": {"opened": True, "open": True}}])

    def test_consolidating_duplicates_must_keep_the_earliest_date(self):
        env, b, r = self._two_ketchups()
        old_exp = ent(env, b["old"])["_expires"]
        r.skill("fetch", obj=b["old"])
        for p in ("old", "new"):
            if not ent(env, b[p]).get("open"):
                r.do("open", target=b[p])
        out = r.do("transfer", target=b["old"], destination=b["new"])
        self.assertIn("printed date", out["message"])
        new = ent(env, b["new"])
        self.assertEqual(new["_expires"], old_exp)            # contents now expire with the older ketchup
        self.assertTrue(new["_date_label_late"])
        self.assertAlmostEqual(new["food"]["qty_g"], 541.0, delta=1.0)
        with self.assertRaises(PlanError):
            r.do("transfer", target=b["old"], destination=b["mustard"])
        # right way round: the newer bottle is poured into the older one, whose printed date is the earliest
        env, b, r = self._two_ketchups()
        r.skill("fetch", obj=b["new"])
        for p in ("old", "new"):
            if not ent(env, b[p]).get("open"):
                r.do("open", target=b[p])
        r.do("transfer", target=b["new"], destination=b["old"])
        old = ent(env, b["old"])
        self.assertFalse(old.get("_date_label_late", False))
        self.assertLessEqual(old["food"]["qty_g"], 567.0 * 1.02 + 0.01)

    def test_removing_outer_packaging_keeps_needed_information(self):
        env, b, r = scene([
            {"op": "spawn", "var": "pizza", "type": "frozen_pizza_box", "on": "{counter}"},
            {"op": "spawn", "var": "yogurt", "type": "yogurt_cup_multipack", "on": "{counter}"},
            {"op": "spawn", "var": "spinach", "type": "baby_spinach_bag", "on": "{counter}"}])
        from house_sim.kernel.actions import ACTIONS
        if "unwrap" not in ACTIONS:
            self.skipTest("unwrap is provided by the manipulation domain")
        for key in ("pizza", "yogurt", "spinach"):
            r.skill("fetch", obj=b[key])
            r.do("unwrap", target=b[key])
            set_down(r, b, b[key])
        r.do("wait", seconds=300)
        pizza = ent(env, b["pizza"])
        self.assertIn("cooking_instructions", pizza["_info_lost"])
        self.assertFalse(pizza["_packaging_ok"])
        self.assertTrue(ent(env, b["yogurt"])["_packaging_ok"])
        self.assertFalse(ent(env, b["spinach"])["_packaging_ok"])
        scraps = [e for e, x in env.world.s["entities"].items() if x.get("type") == "food_outer_packaging"]
        self.assertEqual(len(scraps), 2)
        self.assertTrue(all(env.world.s["entities"][e]["waste_stream"] == "recycling" for e in scraps))


class DatesAndReports(unittest.TestCase):
    def test_status_classes_and_graded_reports(self):
        env, b, r = scene([
            {"op": "helper", "name": "food.dated", "args": {"type": "milk_carton", "days": 4, "var": "milk", "on": "{counter}", "opened_days_ago": 9}},
            {"op": "helper", "name": "food.dated", "args": {"type": "pasta_box", "days": -30, "var": "pasta", "on": "{counter}"}},
            {"op": "helper", "name": "food.dated", "args": {"type": "yogurt_tub", "days": -1, "var": "yog", "on": "{counter}", "date_type": "use_by"}},
            {"op": "helper", "name": "food.dated", "args": {"type": "canned_beans", "days": 300, "var": "beans", "on": "{counter}"}},
            {"op": "helper", "name": "food.dated", "args": {"type": "cheddar_block", "days": 2, "var": "cheddar", "on": "{counter}"}}])
        r.do("wait", seconds=1)
        st = {k: ent(env, b[k])["_food_status"]["class"] for k in ("milk", "pasta", "yog", "beans", "cheddar")}
        self.assertEqual(st, {"milk": "unsafe", "pasta": "quality_expired", "yog": "unsafe", "beans": "ok", "cheddar": "expiring_soon"})
        obs_label = ent(env, b["pasta"])["date_label"]
        self.assertTrue(obs_label.startswith("BEST BEFORE"))
        r.skill("approach", target=b["pasta"])
        r.do("inspect", target=b["pasta"])
        r.do("report", target=b["pasta"], field="status", value="unsafe")
        self.assertFalse(ent(env, b["pasta"])["_report_check"]["status"])
        r.do("report", target=b["pasta"], field="status", value="quality_expired")
        self.assertTrue(ent(env, b["pasta"])["_report_check"]["status"])
        self.assertNotIn("_expires", env.perception.redact(env.world.s, b["pasta"], ent(env, b["pasta"]), False, False))

    def test_regional_date_labels_are_ambiguous_without_the_format(self):
        from house_sim.systems.food import model as M
        import datetime
        day = datetime.date(2026, 3, 4)
        self.assertEqual(M.format_date(day, "MDY"), "03/04/26")
        self.assertEqual(M.format_date(day, "DMY"), "04/03/2026")
        from house_sim.systems.food.skills import parse_printed_date
        self.assertEqual(parse_printed_date("USE BY 04/03/2026", "uk")[1], day)
        self.assertNotEqual(parse_printed_date("USE BY 04/03/2026", "us")[1], day)

    def test_spoilage_shows_mold_and_odor_only_when_exposed(self):
        env, b, r = scene([
            {"op": "spawn", "var": "berries", "type": "strawberries", "on": "{counter}", "props": {"_spoil": 1.05}},
            {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}", "props": {"_spoil": 0.95}}])
        r.do("wait", seconds=1)
        self.assertIn("mold", ent(env, b["berries"])["appearance"])
        self.assertEqual(ent(env, b["milk"])["appearance"], "sealed package")
        r.skill("approach", target=b["milk"])
        details = msg_json(r.do("inspect", target=b["milk"])["message"])
        self.assertNotIn("odor", details["food"])
        r.do("open", target=b["milk"])
        details = msg_json(r.do("inspect", target=b["milk"])["message"])
        self.assertIn("sour", details["food"]["odor"])

    def test_ripening_bananas_on_counter_and_ripeness_report(self):
        env, b, r = scene([{"op": "spawn", "var": "avo", "type": "avocado", "on": "{counter}", "props": {"food": {"ripeness": 0.3}}}])
        r.skill("approach", target=b["avo"])
        r.do("inspect", target=b["avo"], where="closely")
        r.do("report", target=b["avo"], field="ripe", value=False)
        self.assertTrue(ent(env, b["avo"])["_report_check"]["ripe"])
        r.do("wait", seconds=86400)
        r.do("wait", seconds=86400)
        self.assertGreater(food(env, b["avo"])["ripeness"], 0.6)


class StorageZones(unittest.TestCase):
    def test_cleaning_products_on_the_pantry_shelf_are_a_storage_issue(self):
        # cleaning tags its products 'chemical' / 'chem:<class>' without a toxic/corrosive hazard
        env, b, r = scene([{"op": "helper", "name": "food.dated", "args": {"type": "pasta_box", "days": 200, "var": "pasta",
                                                                           "into": "{pantry}", "shelf": "{pantry_low}"}},
                           {"op": "spawn", "var": "cleaner", "type": "generic_item", "in": "{pantry}", "shelf": "{pantry_low}",
                            "props": {"label": "degreaser", "category": "cleaning_product", "tags": ["chemical", "chem:alkali"],
                                      "size_m": [0.1, 0.06, 0.26], "mass_kg": 0.85}}], kitchen_args={"need": []})
        r.do("wait", seconds=60)
        self.assertFalse(ent(env, b["pasta"])["_storage_ok"])
        self.assertEqual(ent(env, b["pasta"])["_storage_issue"], "chemical_on_food_shelf")
        env2, b2, r2 = scene([{"op": "helper", "name": "food.dated", "args": {"type": "pasta_box", "days": 200, "var": "pasta",
                                                                              "into": "{pantry}", "shelf": "{pantry_low}"}}],
                             kitchen_args={"need": []})
        r2.do("wait", seconds=60)
        self.assertNotEqual(ent(env2, b2["pasta"]).get("_storage_issue"), "chemical_on_food_shelf")

    def test_furnisher_keeps_shelves_under_their_load_rating(self):
        import random
        from house_sim.kernel.registry import default_registry
        from house_sim.generator.place import Placer
        from house_sim.systems.food import furnish as F
        reg = default_registry()
        config = {"rooms": {"kitchen_1": {"type": "kitchen", "size_m": [3.0, 3.0]}}, "connections": {}, "people": {}, "pets": {},
                  "entities": {"cab": reg.make("cabinet", "kitchen_1", pos=[0, 0]),
                               "anvil": dict(reg.make("generic_item", "cab", mass_kg=19.0), relation="in", shelf=0)}}
        p = Placer(config, reg, random.Random(0))
        pot = F._put(p, config, reg, random.Random(0), "cab", "stock_pot", shelf=0)          # 19 + 2.5 > 0.7 x 30 kg
        self.assertEqual(config["entities"][pot]["shelf"], 1)
        self.assertIsNone(F._put(p, config, reg, random.Random(0), "cab", "stock_pot", shelf=0, strict=True))
        from house_sim.systems.food import model as M
        self.assertLessEqual(M.shelf_load(config["entities"], "cab", 1), F.FILL * 30.0)

    def test_raw_chicken_must_be_below_ready_to_eat_food(self):
        env, b, r = scene([
            {"op": "spawn", "var": "chicken", "type": "chicken_breast_pack", "on": "{counter}", "props": {"temperature_c": 5.0}},
            {"op": "spawn", "var": "leftovers", "type": "storage_container_medium", "in": "{fridge}", "shelf": "{fridge_mid}"},
            {"op": "spawn", "var": "soup", "type": "soup", "in": "{leftovers}", "props": {"food": {"qty_g": 500.0}, "mass_kg": 0.5, "volume_l": 0.5, "temperature_c": 4.0}}],
            kitchen_args={"need": ["fridge"]})
        r.skill("put", obj=b["chicken"], dest=b["fridge"], relation="in", shelf=b["fridge_top"])
        r.do("wait", seconds=1)
        self.assertFalse(ent(env, b["chicken"])["_storage_ok"])
        self.assertEqual(ent(env, b["chicken"])["_storage_issue"], "raw_above_ready_to_eat")
        self.assertFalse(ent(env, b["soup"])["_storage_ok"])
        r.skill("put", obj=b["chicken"], dest=b["fridge"], relation="in", shelf=b["fridge_bottom"])
        r.do("wait", seconds=1)
        self.assertTrue(ent(env, b["chicken"])["_storage_ok"])
        self.assertTrue(ent(env, b["soup"])["_storage_ok"])

    def test_crispers_doors_and_pantry_rules(self):
        env, b, r = scene([
            {"op": "spawn", "var": "apples", "type": "apple", "on": "{counter}"},
            {"op": "spawn", "var": "lettuce", "type": "lettuce", "on": "{counter}", "props": {"temperature_c": 6.0}},
            {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}", "props": {"temperature_c": 5.0}},
            {"op": "spawn", "var": "potato", "type": "potato", "on": "{counter}"},
            {"op": "spawn", "var": "onion", "type": "onion", "on": "{counter}"}], kitchen_args={"need": ["fridge"]})
        r.skill("put", obj=b["lettuce"], dest=b["crisper"], relation="in")
        r.skill("put", obj=b["apples"], dest=b["crisper"], relation="in")
        r.skill("put", obj=b["milk"], dest=b["door_bin"], relation="in")
        r.do("wait", seconds=1)
        self.assertEqual(ent(env, b["lettuce"])["_storage_issue"], "ethylene_exposure")
        self.assertEqual(ent(env, b["milk"])["_storage_issue"], "warm_door_zone")
        r.skill("put", obj=b["apples"], dest=b["crisper_low"], relation="in")
        r.skill("put", obj=b["milk"], dest=b["fridge"], relation="in", shelf=b["fridge_mid"])
        r.do("wait", seconds=1)
        self.assertTrue(ent(env, b["lettuce"])["_storage_ok"])
        self.assertTrue(ent(env, b["milk"])["_storage_ok"])
        r.skill("put", obj=b["potato"], dest=b["pantry"], relation="in", shelf=0)
        r.skill("put", obj=b["onion"], dest=b["pantry"], relation="in", shelf=0)
        r.do("wait", seconds=1)
        self.assertEqual(ent(env, b["potato"])["_storage_issue"], "stored_with_onion")

    def test_fifo_order_on_a_shelf_and_items_in_front_block_reaching(self):
        env, b, r = scene([
            {"op": "helper", "name": "food.dated", "args": {"type": "canned_beans", "days": 30, "var": "old", "into": "{pantry}", "shelf": 0, "spos": [0.1, 0.0]}},
            {"op": "helper", "name": "food.dated", "args": {"type": "canned_beans", "days": 300, "var": "new", "on": "{counter}"}}])
        r.skill("put", obj=b["new"], dest=b["pantry"], relation="in", shelf=0, spos=[0.1, 0.12])
        r.do("wait", seconds=1)
        self.assertTrue(ent(env, b["new"])["_fifo_ok"])
        self.assertTrue(ent(env, b["old"])["_fifo_ok"])
        res = step(env, "pick_up", b["new"])
        self.assertFalse(res["ok"])
        self.assertIn("in front", res["message"])
        env2, b2, r2 = scene([
            {"op": "helper", "name": "food.dated", "args": {"type": "canned_beans", "days": 30, "var": "old", "into": "{pantry}", "shelf": 0, "spos": [0.1, 0.12]}},
            {"op": "helper", "name": "food.dated", "args": {"type": "canned_beans", "days": 300, "var": "new", "on": "{counter}"}}])
        r2.skill("put", obj=b2["new"], dest=b2["pantry"], relation="in", shelf=0)
        r2.do("wait", seconds=1)
        self.assertEqual(ent(env2, b2["new"])["spos"][1], 0.0)     # default placement goes to the front
        self.assertFalse(ent(env2, b2["new"])["_fifo_ok"])


class Contamination(unittest.TestCase):
    def test_glove_change_keeps_salad_clean_same_hands_contaminate(self):
        def run(change):
            env, b, r = scene([
                {"op": "spawn", "var": "red", "type": "cutting_board_red", "on": "{counter}"},
                {"op": "spawn", "var": "green", "type": "cutting_board_green", "on": "{counter2}"},
                {"op": "spawn", "var": "chicken", "type": "chicken_breast", "on": "{red}"},
                {"op": "spawn", "var": "cucumber", "type": "cucumber", "on": "{green}"},
                {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"},
                {"op": "spawn", "var": "knife2", "type": "paring_knife", "on": "{counter2}"},
                {"op": "spawn", "var": "gbox", "type": "food_glove_box", "on": "{counter}", "props": {"open": True}},
                {"op": "spawn", "var": "gloves", "type": "food_glove_pair", "in": "{gbox}"}], kitchen_args={"need": ["trash"]})
            if change:
                r.skill("fetch", obj=b["gloves"])
                r.do("wear", target=b["gloves"])
            knife_ready(r, b)
            r.do("cut", target=b["chicken"], style="strips", size_mm=10)
            set_down(r, b, b["knife"])
            if change:
                r.do("take_off", target=b["gloves"])
                r.skill("put", obj=b["gloves"], dest=b["trash"])
            knife_ready(r, b, "knife2")
            r.do("cut", target=b["cucumber"], style="flat_side")
            r.do("slice", target=b["cucumber"], thickness_mm=4)
            return env, b
        env, b = run(True)
        self.assertLess(ent(env, b["cucumber"]).get("contamination", {}).get("raw_meat", 0.0), 0.05)
        self.assertNotIn("food.rte_cross_contamination", env.evaluate()["failures"])
        self.assertEqual(env.world.s["food_log"].get("dirty_rte_contacts", 0), 0)
        env2, b2 = run(False)
        self.assertGreater(ent(env2, b2["cucumber"])["contamination"]["raw_meat"], 0.1)
        self.assertIn("food.rte_cross_contamination", env2.evaluate()["failures"])
        self.assertGreater(env2.world.s["food_log"]["dirty_rte_contacts"], 0)

    def test_cooking_to_safe_core_temperature_kills_pathogens(self):
        env, b, r = scene([
            {"op": "spawn", "var": "oven", "type": "cabinet", "on": "{counter2}",
             "props": {"label": "countertop oven stand-in", "internal_temp_c": 200.0, "heat_coupling": 1.0, "size_m": [0.45, 0.35, 0.28],
                       "inner_size_m": [0.32, 0.26, 0.2], "shelf_heights": [0.05], "mass_kg": 6.0, "portable": False}},
            {"op": "spawn", "var": "chicken", "type": "chicken_breast", "in": "{oven}", "shelf": 0, "props": {"temperature_c": 4.0, "_core_c": 4.0, "contamination": {"raw_meat": 0.8}}}])
        r.do("wait", seconds=600)
        self.assertFalse(food(env, b["chicken"]).get("cooked", False))
        r.do("wait", seconds=2400)
        f = food(env, b["chicken"])
        self.assertTrue(f["cooked"])
        self.assertGreaterEqual(ent(env, b["chicken"])["_peak_core_c"], 74.0)
        self.assertNotIn("raw_meat", ent(env, b["chicken"]).get("contamination", {}))


# a portable single hot plate (~0.40 x 0.28 m); with every domain furnishing the kitchen the counters are crowded
BURNER = {"op": "spawn", "var": "burner", "type": "cutting_board", "on": "{counter2}",
          "props": {"label": "burner stand-in", "heat_source": True, "size_m": [0.4, 0.28, 0.02], "material": "steel"}}


class HeatDoneness(unittest.TestCase):
    """Food owns what heat does to food (appliance domains own the heat): stand-in burner/toaster here."""

    def test_fridge_egg_in_boiling_water_runny_at_6_min_set_at_11(self):
        env, b, r = scene([BURNER,
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{burner}", "props": {"temperature_c": 100.0, "fixed_temperature": True}},
            {"op": "spawn", "var": "egg", "type": "hen_egg", "in": "{pot}", "props": {"temperature_c": 4.0, "_core_c": 4.0}}])
        r.do("wait", seconds=360)
        f = food(env, b["egg"])
        self.assertEqual(f["egg_doneness"], "runny_yolk")
        self.assertFalse(f.get("cooked", False))
        r.do("wait", seconds=300)
        f = food(env, b["egg"])
        self.assertEqual(f["egg_doneness"], "set")
        self.assertTrue(f["cooked"])
        r.do("wait", seconds=600)
        self.assertEqual(food(env, b["egg"])["egg_doneness"], "overcooked_rubbery")

    def test_toast_goes_golden_then_burns_and_smokes(self):
        toaster = {"op": "spawn", "var": "toaster", "type": "cabinet", "on": "{counter2}",
                   "props": {"label": "toaster stand-in", "internal_temp_c": 250.0, "heat_coupling": 2.0, "size_m": [0.3, 0.18, 0.2],
                             "inner_size_m": [0.14, 0.04, 0.14], "shelf_heights": [0.0], "mass_kg": 2.0}}
        env, b, r = scene([toaster, {"op": "spawn", "var": "bread", "type": "bread_slice", "in": "{toaster}", "shelf": 0}])
        r.do("wait", seconds=150)
        self.assertEqual(food(env, b["bread"])["toast_level"], "golden")
        self.assertFalse([e for e in env.world.s["event_log"] if e["kind"] == "smoke"])
        r.do("wait", seconds=120)
        self.assertEqual(food(env, b["bread"])["toast_level"], "burnt")
        self.assertTrue([e for e in env.world.s["event_log"] if e["kind"] == "smoke"])
        self.assertIn("burnt", ent(env, b["bread"])["appearance"])

    def test_oats_absorb_the_pot_water_and_turn_into_oatmeal(self):
        env, b, r = scene([BURNER,
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{burner}",
             "props": {"temperature_c": 100.0, "fixed_temperature": True, "liquid_l": 0.24, "liquid": "water", "liquid_temp_c": 95.0}},
            {"op": "spawn", "var": "oats", "type": "rolled_oats_canister", "on": "{counter}", "props": {"open": True, "opened": True}},
            {"op": "spawn", "var": "cup", "type": "measuring_cup_half", "on": "{counter}"}])
        r.skill("fetch", obj=b["cup"])
        r.do("scoop", target=b["oats"], destination=b["pot"], technique="level")
        oats = [e for e, x in env.world.s["entities"].items() if x.get("location") == b["pot"] and x.get("food")][0]
        r.do("wait", seconds=120)
        self.assertIn("raw", food(env, oats)["grain_texture"])
        r.do("wait", seconds=240)
        f = food(env, oats)
        self.assertEqual(f["kind"], "oatmeal")
        self.assertTrue(f["grain_texture"].startswith("tender"))
        self.assertGreater(f["composition"]["water"], 200)
        self.assertEqual(ent(env, b["pot"]).get("liquid_l"), 0.0)

    def test_dry_grain_in_a_hot_pan_only_toasts(self):
        env, b, r = scene([BURNER,
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{burner}", "props": {"temperature_c": 100.0, "fixed_temperature": True}},
            {"op": "spawn", "var": "rice", "type": "rice_bag", "on": "{counter}", "props": {"open": True, "opened": True}},
            {"op": "spawn", "var": "cup", "type": "measuring_cup_half", "on": "{counter}"}])
        r.skill("fetch", obj=b["cup"])
        r.do("scoop", target=b["rice"], destination=b["pot"], technique="level")
        rice = [e for e, x in env.world.s["entities"].items() if x.get("location") == b["pot"] and x.get("food")][0]
        r.do("wait", seconds=600)
        f = food(env, rice)
        self.assertEqual(f["grain_texture"], "dry, toasting")
        self.assertFalse(f.get("cooked", False))

    def _smoothie(self, berries_g, milk_ml):
        env, b, r = scene([
            {"op": "spawn", "var": "jar", "type": "storage_container_large", "on": "{counter}", "props": {"open": True, "label": "blender jar stand-in"}},
            {"op": "spawn", "var": "berries", "type": "frozen_berries_bag", "on": "{counter}", "props": {"open": True, "opened": True}},
            {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}", "props": {"open": True, "opened": True, "temperature_c": 4.0}}])
        r.skill("fetch", obj=b["berries"])
        r.do("transfer", target=b["berries"], destination=b["jar"], amount_g=berries_g)
        set_down(r, b, b["berries"])
        r.skill("fetch", obj=b["milk"])
        r.do("transfer", target=b["milk"], destination=b["jar"], amount_ml=milk_ml)
        set_down(r, b, b["milk"])
        r.do("close", target=b["jar"])
        from house_sim.systems.food import mixing as X
        out = X.blend(env.world, b["jar"], 45, "high")
        return env, ent(env, out)

    def test_blending_frozen_fruit_needs_enough_liquid(self):
        env, rec = self._smoothie(150, 240)
        f = rec["food"]
        self.assertEqual(f["kind"], "smoothie")
        self.assertEqual(f["frozen_chunks"], 0)
        self.assertLess(rec["temperature_c"], 4.0)
        self.assertNotIn("cavitated", f["mix"])
        env, rec = self._smoothie(300, 40)
        self.assertTrue(rec["food"]["mix"].get("cavitated"))
        self.assertGreater(rec["food"]["frozen_chunks"], 5)

    def test_watering_down_an_oversalted_soup(self):
        soup = {"op": "spawn", "var": "soup", "type": "soup", "in": "{pot}",
                "props": {"food": {"qty_g": 1422.0, "composition": {"water": 900.0, "vegetables": 500.0, "salt": 22.0}},
                          "mass_kg": 1.422, "volume_l": 1.4, "temperature_c": 60.0}}
        env, b, r = scene([{"op": "spawn", "var": "pot", "type": "stock_pot", "on": "{counter}"}, soup,
                           {"op": "spawn", "var": "spoon", "type": "wooden_spoon", "on": "{counter}"}])
        r.do("wait", seconds=1)
        self.assertGreater(ent(env, b["soup"])["_salt_pct"], 1.2)
        # 0.5 L of tap water poured in (plumbing domain), then stirred through
        env.world.mut(b["pot"]).update(liquid_l=0.5, liquid="water")
        r.skill("fetch", obj=b["spoon"])
        r.do("stir", target=b["pot"], strokes=20)
        r.do("wait", seconds=300)
        rec = ent(env, b["soup"])
        self.assertLessEqual(rec["_salt_pct"], 1.2)
        self.assertGreater(rec["_liquid_pct"], 70.0)
        self.assertAlmostEqual(rec["food"]["qty_g"], 1922.0, delta=2.0)


class Serving(unittest.TestCase):
    def _kids(self):
        return [{"op": "helper", "name": "food.eaters", "args": {"people": [
            {"id": "toddler_1", "role": "child", "age": 2, "room": "kitchen"},
            {"id": "guest_1", "role": "guest", "age": 30, "room": "kitchen", "allergies": ["peanut"]},
            {"id": "teen_1", "role": "child", "age": 16, "room": "kitchen"}]}}]

    def test_quartered_grapes_are_safe_for_a_toddler_whole_grapes_are_not(self):
        env, b, r = scene(self._kids() + [
            {"op": "spawn", "var": "plate", "type": "kids_plate", "on": "{counter}"},
            {"op": "spawn", "var": "grapes", "type": "grapes", "on": "{plate}", "props": {"food": {"count": 8, "qty_g": 64.0}, "mass_kg": 0.064, "size_m": [0.08, 0.06, 0.03]}},
            {"op": "spawn", "var": "knife", "type": "paring_knife", "on": "{counter}"}])
        knife_ready(r, b)
        r.do("cut", target=b["grapes"], style="quarter", axis="lengthwise")
        set_down(r, b, b["knife"])
        r.skill("give", obj=b["plate"], person="toddler_1")
        self.assertEqual(env.world.s["stats"]["safety_violations"], 0)
        env2, b2, r2 = scene(self._kids() + [
            {"op": "spawn", "var": "plate", "type": "kids_plate", "on": "{counter}"},
            {"op": "spawn", "var": "grapes", "type": "grapes", "on": "{plate}", "props": {"food": {"count": 8, "qty_g": 64.0}, "mass_kg": 0.064, "size_m": [0.08, 0.06, 0.03]}}])
        r2.skill("give", obj=b2["plate"], person="toddler_1")
        self.assertEqual(env2.world.s["stats"]["safety_violations"], 1)
        self.assertIn("choking", env2.world.s["food_log"]["served"][-1]["issues"][0])

    def test_peanut_snack_to_allergic_guest_and_beer_to_teen_are_violations(self):
        env, b, r = scene(self._kids() + [
            {"op": "spawn", "var": "bowl", "type": "serving_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "nuts", "type": "food_item", "in": "{bowl}",
             "props": {"food": {"kind": "peanuts", "cat": "nut", "phase": "pieces", "qty_g": 80.0}, "allergens": ["peanut"], "mass_kg": 0.08}},
            {"op": "spawn", "var": "beer", "type": "beer_can", "on": "{counter}", "props": {"open": True}}])
        r.skill("give", obj=b["bowl"], person="guest_1")
        self.assertEqual(env.world.s["stats"]["safety_violations"], 1)
        r.skill("give", obj=b["beer"], person="teen_1")
        self.assertEqual(env.world.s["stats"]["authority_violations"], 1)

    def test_dish_summary_flags_hot_food_for_a_child(self):
        env, b, r = scene(self._kids() + [
            {"op": "spawn", "var": "bowl", "type": "soup_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "soup", "type": "soup", "in": "{bowl}", "props": {"food": {"qty_g": 300.0}, "mass_kg": 0.3, "volume_l": 0.3, "temperature_c": 75.0, "_core_c": 75.0}}])
        r.do("wait", seconds=1)
        dish = ent(env, b["bowl"])["_dish"]
        self.assertFalse(dish["ok_for"]["toddler_1"])
        self.assertTrue(any(i.startswith("too_hot") for i in dish["issues"]["toddler_1"]))
        self.assertTrue(dish["ok_for"]["guest_1"])

    def test_plating_with_a_shared_spoon_carries_the_allergen(self):
        env, b, r = scene(self._kids() + [
            {"op": "spawn", "var": "satay", "type": "serving_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "sauce", "type": "food_item", "in": "{satay}",
             "props": {"food": {"kind": "peanut_sauce", "cat": "sauce", "phase": "paste", "qty_g": 300.0}, "allergens": ["peanut"], "mass_kg": 0.3}},
            {"op": "spawn", "var": "rice_bowl", "type": "serving_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "rice", "type": "cooked_rice", "in": "{rice_bowl}", "props": {"food": {"qty_g": 400.0}, "mass_kg": 0.4, "temperature_c": 50.0}},
            {"op": "spawn", "var": "plate", "type": "dinner_plate", "on": "{counter}"},
            {"op": "spawn", "var": "spoon", "type": "serving_spoon", "on": "{counter}"}])
        r.skill("fetch", obj=b["spoon"])
        r.do("plate", target=b["satay"], destination=b["plate"], amount_g=10, tool=b["spoon"])
        r.do("plate", target=b["rice_bowl"], destination=b["plate"], amount_g=150, tool=b["spoon"])
        r.do("wait", seconds=1)
        self.assertGreater(ent(env, b["rice"]).get("contamination", {}).get("allergen_peanut", 0.0), 0.01)
        self.assertFalse(ent(env, b["plate"])["_dish"]["ok_for"]["guest_1"])


class Perception(unittest.TestCase):
    def test_probe_thermometer_reads_core_and_carries_raw_juice(self):
        env, b, r = scene([
            {"op": "spawn", "var": "chicken", "type": "chicken_breast", "on": "{board}", "props": {"temperature_c": 70.0, "_core_c": 58.0}},
            {"op": "spawn", "var": "probe", "type": "generic_item", "on": "{counter}",
             "props": {"label": "instant-read thermometer", "tool_kind": "thermometer", "capabilities": ["probe_temperature"], "size_m": [0.15, 0.02, 0.02]}}])
        r.skill("fetch", obj=b["probe"])
        details = msg_json(r.do("inspect", target=b["chicken"], where="closely")["message"])
        self.assertAlmostEqual(details["probe_temp_c"], 58.0, delta=1.5)
        self.assertGreater(ent(env, b["probe"]).get("contamination", {}).get("raw_meat", 0.0), 0.1)
        r.do("report", target=b["chicken"], field="core_temp_c", value=details["probe_temp_c"])
        self.assertTrue(ent(env, b["chicken"])["_report_check"]["core_temp_c"])
        r.do("report", target=b["chicken"], field="done", value=True)
        self.assertFalse(ent(env, b["chicken"])["_report_check"]["done"])

    def test_rotten_food_smell_reaches_the_robot_only_from_exposed_food(self):
        env, b, r = scene([{"op": "spawn", "var": "box", "type": "storage_container_medium", "on": "{counter}", "props": {"open": False}},
                           {"op": "spawn", "var": "stew", "type": "stew", "in": "{box}", "props": {"_spoil": 1.2, "food": {"qty_g": 500.0}, "mass_kg": 0.5, "volume_l": 0.5}}])
        obs = env.observe()
        self.assertFalse([s for s in obs.get("smells", []) if s["kind"] == "spoiled_food"])
        r.do("open", target=b["box"])
        obs = env.observe()
        self.assertTrue([s for s in obs.get("smells", []) if s["kind"] == "spoiled_food"])

    def test_residents_answer_food_questions_from_their_profile(self):
        env, b, r = scene([{"op": "helper", "name": "food.eaters", "args": {"people": [
            {"id": "guest_1", "role": "guest", "age": 30, "room": "kitchen", "allergies": ["sesame"], "dislikes": ["cilantro"]}]}}])
        r.skill("approach", target="guest_1")
        ans = msg_json(r.do("ask", target="guest_1", topic="food:allergies")["message"])
        self.assertEqual(ans["value"], ["sesame"])
        self.assertEqual(env.world.s["stats"]["necessary_questions"], 1)
        r.do("ask", target="guest_1", topic="food:allergies")
        self.assertEqual(env.world.s["stats"]["unnecessary_questions"], 1)


class Dishes(unittest.TestCase):
    def test_radial_cut_fairness_depends_on_the_offset(self):
        tops = [[a, 0.6, "rose"] for a in (10, 20, 30, 40)]
        env, b, r = scene([
            {"op": "spawn", "var": "cake", "type": "cake", "on": "{board}", "props": {"food": {"toppings": tops}}},
            {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"}])
        knife_ready(r, b)
        r.do("cut", target=b["cake"], style="radial", pieces=4, offset_deg=0)
        self.assertEqual(food(env, b["cake"])["topping_spread"], 4)
        env2, b2, r2 = scene([
            {"op": "spawn", "var": "cake", "type": "cake", "on": "{board}", "props": {"food": {"toppings": [[a, 0.6, "rose"] for a in (10, 100, 190, 280)]}}},
            {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"}])
        knife_ready(r2, b2)
        r2.do("cut", target=b2["cake"], style="radial", pieces=4, offset_deg=0)
        self.assertEqual(food(env2, b2["cake"])["topping_spread"], 0)

    def test_roughly_equal_pieces_from_different_sized_potatoes(self):
        env, b, r = scene([
            {"op": "spawn", "var": "small", "type": "potato", "on": "{board}", "props": {"food": {"qty_g": 120.0}, "mass_kg": 0.12, "size_m": [0.08, 0.055, 0.05]}},
            {"op": "spawn", "var": "big", "type": "potato", "on": "{board}", "props": {"food": {"qty_g": 360.0}, "mass_kg": 0.36, "size_m": [0.12, 0.08, 0.07]}},
            {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"}])
        knife_ready(r, b)
        r.do("cut", target=b["small"], style="flat_side")
        r.do("cut", target=b["big"], style="flat_side")
        r.do("cut", target=b["small"], style="pieces", pieces=2)
        r.do("cut", target=b["big"], style="pieces", pieces=6)
        a, c = food(env, b["small"])["piece_ml"], food(env, b["big"])["piece_ml"]
        self.assertLess(abs(a - c) / ((a + c) / 2), 0.1)

    def test_sandwich_tomato_on_bread_goes_soggy_lettuce_barrier_protects(self):
        def run(barrier):
            setup = [{"op": "spawn", "var": "bread", "type": "bread_slice", "on": "{board}"},
                     {"op": "spawn", "var": "lettuce", "type": "lettuce_leaf", "on": "{counter}"},
                     {"op": "spawn", "var": "tomato", "type": "tomato_slices", "on": "{counter}"}]
            env, b, r = scene(setup)
            if barrier:
                r.skill("put", obj=b["lettuce"], dest=b["bread"])
                r.skill("put", obj=b["tomato"], dest=b["lettuce"])
            else:
                r.skill("put", obj=b["tomato"], dest=b["bread"])
            r.do("wait", seconds=3 * 3600)
            return food(env, b["bread"]).get("soggy", 0.0)
        self.assertLess(run(True), 0.1)
        self.assertGreater(run(False), 0.5)

    def test_cold_tortilla_cracks_warm_one_folds(self):
        def run(temp):
            env, b, r = scene([
                {"op": "spawn", "var": "tortilla", "type": "tortilla", "on": "{board}", "props": {"temperature_c": temp, "_core_c": temp}},
                {"op": "spawn", "var": "beans", "type": "food_item", "on": "{tortilla}",
                 "props": {"food": {"kind": "refried_beans", "cat": "prepared", "phase": "paste", "qty_g": 200.0, "density": 1.05}, "mass_kg": 0.2, "size_m": [0.1, 0.1, 0.03]}}])
            r.do("shape", target=b["tortilla"], form="burrito", fold_ends=True)
            return food(env, b["tortilla"])
        self.assertFalse(run(45.0).get("cracked", False))
        self.assertTrue(run(5.0)["cracked"])

    def test_skimming_foam_with_a_skimmer_vs_stirring_it_back(self):
        def make():
            return scene([
                {"op": "spawn", "var": "pot", "type": "stock_pot", "on": "{counter}"},
                {"op": "spawn", "var": "stock", "type": "food_item", "in": "{pot}",
                 "props": {"food": {"kind": "stock", "cat": "prepared", "phase": "liquid", "qty_g": 3000.0, "foam_g": 40.0}, "mass_kg": 3.0}},
                {"op": "spawn", "var": "skimmer", "type": "kitchen_skimmer", "on": "{counter}"},
                {"op": "spawn", "var": "spoon", "type": "wooden_spoon", "on": "{counter}"},
                {"op": "spawn", "var": "discard", "type": "prep_bowl", "on": "{counter}"}])
        env, b, r = make()
        r.skill("fetch", obj=b["skimmer"])
        out = msg_json(r.do("transfer", target=b["pot"], destination=b["discard"], method="skim", tool=b["skimmer"])["message"])
        self.assertLess(out["broth_lost_g"], 30.0)
        self.assertEqual(food(env, b["stock"])["foam_g"], 0.0)
        self.assertFalse(food(env, b["stock"]).get("cloudy", False))
        env2, b2, r2 = make()
        r2.skill("fetch", obj=b2["spoon"])
        r2.do("stir", target=b2["pot"], strokes=10)
        self.assertTrue(food(env2, b2["stock"])["cloudy"])

    def test_soaking_greens_lets_grit_settle_mushrooms_waterlog(self):
        env, b, r = scene([
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}", "props": {"liquid": "water", "liquid_l": 3.0}},
            {"op": "spawn", "var": "kale", "type": "radishes", "on": "{counter}", "props": {"soil": {"dirt": 0.4}}},
            {"op": "spawn", "var": "shrooms", "type": "mushrooms", "on": "{counter}"}])
        r.skill("put", obj=b["kale"], dest=b["bowl"], relation="in")
        r.skill("put", obj=b["shrooms"], dest=b["bowl"], relation="in")
        r.do("wait", seconds=600)
        self.assertLess(ent(env, b["kale"])["soil"]["dirt"], 0.05)
        self.assertGreater(ent(env, b["bowl"])["grit"], 0.3)
        self.assertTrue(food(env, b["shrooms"])["waterlogged"])

    def test_cereal_goes_soggy_in_milk_within_minutes(self):
        env, b, r = scene([
            {"op": "spawn", "var": "box", "type": "cereal_box", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "milk", "type": "oat_milk_carton", "on": "{counter}", "props": {"open": True}},
            {"op": "spawn", "var": "bowl", "type": "cereal_bowl", "on": "{counter}"}])
        r.skill("fetch", obj=b["box"])
        r.do("transfer", target=b["box"], destination=b["bowl"], amount_g=40)
        self.assertFalse(ent(env, b["box"])["liner_closed"])
        r.do("close", target=b["box"])
        self.assertTrue(ent(env, b["box"])["liner_closed"])
        set_down(r, b, b["box"])
        r.skill("fetch", obj=b["milk"])
        r.do("transfer", target=b["milk"], destination=b["bowl"], amount_ml=200)
        cereal = [e for e, rec in env.world.s["entities"].items() if rec.get("location") == b["bowl"] and (rec.get("food") or {}).get("kind") == "cereal"][0]
        r.do("wait", seconds=60)
        self.assertLess(food(env, cereal).get("soggy", 0.0), 0.5)
        r.do("wait", seconds=600)
        self.assertGreater(food(env, cereal)["soggy"], 0.9)


class RecipesAndLabels(unittest.TestCase):
    RECIPE = {"name": "Buttermilk pancakes", "servings": 4, "oven_c": None,
              "ingredients": [{"kind": "all_purpose_flour", "qty": 1.5, "text_qty": "1 1/2", "unit": "cup", "g": 190},
                              {"kind": "buttermilk", "qty": 1.25, "text_qty": "1 1/4", "unit": "cup", "g": 300},
                              {"kind": "baking_soda", "qty": 1, "unit": "tsp", "g": 5}],
              "steps": ["Whisk dry ingredients", "Add buttermilk", "Rest batter 5 minutes"],
              "order": [["all_purpose_flour", "buttermilk"]], "scale_to": 8}

    def test_reading_a_recipe_and_reporting_parsed_quantities(self):
        env, b, r = scene([{"op": "helper", "name": "food.recipe", "args": {"on": "{counter}", "recipe": self.RECIPE}}])
        r.skill("approach", target=b["recipe"])
        text = msg_json(r.do("read", target=b["recipe"])["message"])["text"]
        self.assertIn("1 1/2 cup", text)
        r.do("report", target=b["recipe"], field="ingredients",
             value=[{"kind": "all_purpose_flour", "qty": "1 1/2", "unit": "cup"}, {"kind": "buttermilk", "qty": "1 1/4", "unit": "cups"},
                    {"kind": "baking_soda", "qty": 1, "unit": "tsp"}])
        self.assertTrue(ent(env, b["recipe"])["_report_check"]["ingredients"])
        r.do("report", target=b["recipe"], field="ingredients",
             value=[{"kind": "all_purpose_flour", "qty": "11/2", "unit": "cup"}, {"kind": "buttermilk", "qty": "1 1/4", "unit": "cup"},
                    {"kind": "baking_soda", "qty": 1, "unit": "tbsp"}])
        self.assertFalse(ent(env, b["recipe"])["_report_check"]["ingredients"])
        r.do("report", target=b["recipe"], field="scaled_ingredients",
             value=[{"kind": "all_purpose_flour", "qty": 3, "unit": "cup"}, {"kind": "buttermilk", "qty": 2.5, "unit": "cup"},
                    {"kind": "baking_soda", "qty": 2, "unit": "tsp"}])
        self.assertTrue(ent(env, b["recipe"])["_report_check"]["scaled_ingredients"])

    def test_recipe_check_order_and_buttermilk_substitute(self):
        from house_sim.systems.food import model as M
        state = {"entities": {"bowl": {"_mix_log": [{"op": "add", "kind": "all_purpose_flour", "g": 190}, {"op": "add", "kind": "baking_soda", "g": 5},
                                                    {"op": "add", "kind": "milk", "g": 282}, {"op": "add", "kind": "lemon_juice", "g": 18}]}}}
        res = M.recipe_check(state, "bowl", self.RECIPE)
        self.assertTrue(res["ok"])
        self.assertEqual(res["substituted"], ["buttermilk"])
        state["entities"]["bowl"]["_mix_log"] = [{"op": "add", "kind": "milk", "g": 300}, {"op": "add", "kind": "all_purpose_flour", "g": 190},
                                                 {"op": "add", "kind": "baking_soda", "g": 5}]
        res = M.recipe_check(state, "bowl", self.RECIPE)
        self.assertFalse(res["ok"])

    def test_label_parse_flags_ambiguous_dates_and_wrong_contents(self):
        from house_sim.systems.food import model as M
        good = M.parse_label("beef chili 2026-10-07", None)
        self.assertEqual(good["contents"], "beef_chili")
        self.assertIn("2026-10-07", good["dates"])
        self.assertFalse(good["ambiguous_date"])
        self.assertTrue(M.parse_label("chili 10/07", None)["ambiguous_date"])
        state = {"entities": {"box": {"food_label": good}, "c": {"location": "box", "label": "beef chili",
                                                                "food": {"kind": "chili", "made_on": "2026-10-07"}}}}
        self.assertTrue(M.label_check(state, "box")["ok"])
        state["entities"]["box"]["food_label"] = M.parse_label("leftovers 2026-10-09", None)
        self.assertFalse(M.label_check(state, "box")["ok"])


class Robustness(unittest.TestCase):
    def test_replays_are_deterministic(self):
        def run():
            env, b, r = scene([
                {"op": "spawn", "var": "onion", "type": "onion", "on": "{board}"},
                {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"},
                {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}"}], seed=3)
            knife_ready(r, b)
            r.do("dice", target=b["onion"])
            r.do("wait", seconds=5000)
            return digest(env.world.s)
        self.assertEqual(run(), run())

    def test_strict_mode_journals_every_food_change(self):
        import importlib
        from house_sim.kernel import engine
        os.environ["HOUSE_SIM_STRICT"] = "1"
        try:
            importlib.reload(engine)
            from house_sim.tasks.runner import build as build_
            spec = {"catalog_id": "test-food-strict", "house": {"residents": "minimal", "pets": "none"},
                    "setup": [{"op": "helper", "name": "food.kitchen", "args": {"need": ["fridge", "trash", "compost"]}},
                              {"op": "spawn", "var": "onion", "type": "onion", "on": "{board}"},
                              {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"},
                              {"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"},
                              {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
                              {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}", "props": {"open": True}},
                              {"op": "robot", "room": "{kitchen}", "cell": "{stand}"}],
                    "success": [{"path": "time_s", "op": "gte", "value": 0}], "reference": []}
            config, task, oracle = build_(spec, 0)
            env = engine.HouseEnv(config, task, 0)
            b = oracle["bindings"]
            r = Runner(env, b, strict=True)
            knife_ready(r, b)
            r.do("cut", target=b["onion"], style="halve", axis="lengthwise")
            r.do("dice", target=b["onion"])
            set_down(r, b, b["knife"])
            r.skill("fetch", obj=b["egg"])
            r.do("crack_egg", target=b["egg"], destination=b["bowl"])
            r.skill("put", obj=env.world.held_items()[0], dest=b["compost"])
            r.skill("fetch", obj=b["milk"])
            r.do("transfer", target=b["milk"], destination=b["bowl"], amount_ml=100)
            r.do("wait", seconds=4000)
            self.assertFalse(step(env, "dice", b["milk"])["ok"])
        finally:
            os.environ.pop("HOUSE_SIM_STRICT", None)
            importlib.reload(engine)



TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
PROBE = {"op": "spawn", "var": "probe", "type": "generic_item", "on": "{counter}",
         "props": {"label": "instant-read thermometer", "tool_kind": "thermometer", "capabilities": ["probe_temperature"],
                   "accuracy_c": 0.5, "size_m": [0.15, 0.03, 0.02]}}


def true_ids(env, value):
    """True entity ids (with an underscore, as generated ids have) appearing anywhere in value."""
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


def two_sig(value):
    return value == float("{:.2g}".format(value))


class RealisticPerception(unittest.TestCase):
    """perception="realistic" (kernel v2.4): food fields by tier, look-alikes, instruments, honest messages."""

    def setUp(self):
        self.env, self.b, self.r = scene([
            {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter2}"},
            {"op": "spawn", "var": "sugar", "type": "sugar_bag", "on": "{counter2}"},
            {"op": "spawn", "var": "chicken", "type": "chicken_breast", "on": "{counter}",
             "props": {"temperature_c": 78.0, "_core_c": 58.0, "_peak_c": 82.0}},
            {"op": "spawn", "var": "potato", "type": "sweet_potato", "on": "{counter2}",
             "props": {"mass_kg": 0.742, "food": {"qty_g": 742.0}}},
            {"op": "spawn", "var": "scale", "type": "kitchen_scale", "on": "{counter}", "props": {"on": True}},
            {"op": "helper", "name": "food.dated", "args": {"type": "yogurt_snack_cup", "days": -3, "var": "yog",
                                                            "on": "{counter}", "date_type": "use_by"}},
            PROBE], perception="realistic")
        self.h = self.env.realism.handles.handle

    def seen(self, key, env=None):
        env = env or self.env
        return env.observe()["visible_entities"].get(env.realism.handles.handle(self.b[key]), {})

    def test_glance_hides_the_food_record_and_inspect_gives_estimates_not_model_numbers(self):
        glance = self.seen("potato")
        self.assertEqual(glance["label"], "sweet potato")
        for field in ("food", "appearance", "allergens", "mass_kg", "contamination"):
            self.assertNotIn(field, glance)
        self.assertIsInstance(glance["temperature_c"], str)              # thermal camera: a bucket
        details = msg_json(self.r.do("inspect", target=self.b["potato"])["agent_message"])
        self.assertNotIn("food", details)                                # the raw record never shows
        view = details["food_view"]
        self.assertEqual(view["kind"], "sweet_potato")
        self.assertIn("appearance", view)
        self.assertTrue(two_sig(view["approx_g"]))                       # a visual estimate, two significant figures
        self.assertNotIn("food", self.seen("potato"))
        self.assertNotIn("mass_kg", self.seen("potato"))                 # still unweighed

    def test_kitchen_scale_measures_what_a_look_only_estimates(self):
        r, b = self.r, self.b
        r.skill("put", obj=b["potato"], dest=b["scale"])
        out = msg_json(r.do("weigh", target=b["potato"], unit="g")["agent_message"])
        self.assertEqual(out["display"], "742 g")
        self.assertAlmostEqual(self.seen("potato")["mass_kg"], 0.742, places=2)   # measured by the scale
        r.do("report", target=b["potato"], field="weight_g", value=742)
        self.assertTrue(ent(self.env, b["potato"])["_report_check"]["weight_g"])
        r.do("report", target=b["potato"], field="weight_g", value=700)
        self.assertFalse(ent(self.env, b["potato"])["_report_check"]["weight_g"])

    def test_probe_thermometer_reads_the_core_the_outside_only_looks_cooked(self):
        r, b = self.r, self.b
        details = msg_json(r.do("inspect", target=b["chicken"])["agent_message"])
        self.assertEqual(details["food_view"]["surface"], "looks cooked on the outside")
        self.assertNotIn("cooked", details["food_view"])
        self.assertNotIn("probe_temp_c", details)
        self.assertIsInstance(details["temperature_c"], str)
        r.skill("fetch", obj=b["probe"])
        details = msg_json(r.do("inspect", target=b["chicken"], where="closely")["agent_message"])
        core = ent(self.env, b["chicken"])["_core_c"]
        self.assertAlmostEqual(details["probe_temp_c"], core, delta=1.0)  # the probe's accuracy, not the exact core
        self.assertEqual(details["probe_temp_c"], round(details["probe_temp_c"], 1))
        r.do("report", target=b["chicken"], field="probe_temp_c", value=details["probe_temp_c"])
        self.assertTrue(ent(self.env, b["chicken"])["_report_check"]["probe_temp_c"])

    def test_look_alike_bags_confused_at_a_glance_resolved_by_inspect(self):
        flour, sugar = self.seen("flour"), self.seen("sugar")
        self.assertEqual(flour["label"], "paper bag of white powder")
        self.assertEqual(flour["label"], sugar["label"])
        self.assertIn("sugar bag", flour["candidates"])
        self.r.do("inspect", target=self.b["sugar"])
        self.assertEqual(self.seen("sugar")["label"], "granulated sugar")
        self.assertEqual(self.seen("flour")["label"], "paper bag of white powder")   # the other one is still unknown

    def test_eggs_in_shell_stay_unidentified_and_keep_their_smell_in(self):
        env, b, r = scene([{"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}", "props": {"_rotten": True}},
                           {"op": "spawn", "var": "boiled", "type": "hard_boiled_egg", "on": "{counter}"}],
                          perception="realistic")
        for key in ("egg", "boiled"):
            details = msg_json(r.do("inspect", target=b[key])["agent_message"])
            self.assertEqual(details["label"], "egg in its shell")       # a boiled egg looks like a raw one
            self.assertNotIn("kind", details["food_view"])
            self.assertNotIn("odor", details["food_view"])               # no smell through the shell
        self.assertFalse(env.observe().get("smells"))
        self.assertEqual(env.observe()["visible_entities"][env.realism.handles.handle(b["egg"])]["label"], "egg in its shell")
        oracle_env, ob, orr = scene([{"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}", "props": {"_rotten": True}}])
        self.assertIn("sulfur", msg_json(orr.do("inspect", target=ob["egg"])["message"])["food"]["odor"])   # oracle unchanged

    def test_printed_dates_need_read_not_inspect(self):
        r, b = self.r, self.b
        details = msg_json(r.do("inspect", target=b["yog"], where="closely")["agent_message"])
        self.assertNotIn("date_label", details["food_view"])
        self.assertNotIn("date_label", self.seen("yog"))
        printed = msg_json(r.do("read", target=b["yog"])["agent_message"])
        self.assertIn("USE BY", printed["date_label"])
        self.assertEqual(self.seen("yog")["date_label"], printed["date_label"])
        again = msg_json(r.do("inspect", target=b["yog"])["agent_message"])
        self.assertEqual(again["food_view"]["date_label"], printed["date_label"])

    def test_action_messages_give_estimates_handles_and_no_labels(self):
        env, b, r = scene([
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter2}"},
            {"op": "spawn", "var": "cup", "type": "measuring_cup_1", "on": "{counter}"},
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}"},
            {"op": "spawn", "var": "soup", "type": "soup", "in": "{pot}",
             "props": {"food": {"qty_g": 1000.0}, "mass_kg": 1.0, "temperature_c": 65.0, "_core_c": 65.0}},
            {"op": "spawn", "var": "spoon", "type": "wooden_spoon", "on": "{counter}"}], perception="realistic")
        r.skill("fetch", obj=b["cup"])
        r.skill("approach", target=b["flour"])
        refused = r.do("scoop", target=b["flour"], tool=b["cup"], destination=b["bowl"], expect="fail")
        self.assertIn(env.realism.handles.handle(b["flour"]), refused["agent_message"])    # "Open the obj_... first"
        self.assertNotIn("flour", refused["agent_message"])
        r.do("open", target=b["flour"])
        out = r.do("scoop", target=b["flour"], tool=b["cup"], destination=b["bowl"], technique="dip_and_sweep")
        data = msg_json(out["agent_message"])
        self.assertNotIn("grams", data)                  # an unidentified white powder: a volume, never grams
        self.assertTrue(two_sig(data["ml"]))
        r.do("inspect", target=b["flour"])               # now it is known to be flour: grams by eye
        out = r.do("scoop", target=b["flour"], tool=b["cup"], destination=b["bowl"], technique="dip_and_sweep")
        raw, data = msg_json(out["message"]), msg_json(out["agent_message"])
        true_g = raw["grams"]
        self.assertTrue(two_sig(data["grams"]))           # an estimate: two significant figures, within its error
        self.assertLessEqual(abs(data["grams"] - true_g), 0.6 * true_g)
        self.assertEqual(true_ids(env, out["agent_message"]), set())
        r.skill("put", obj=b["cup"], dest=b["counter"])
        r.skill("fetch", obj=b["spoon"])
        stirred = msg_json(r.do("stir", target=b["pot"], tool=b["spoon"], strokes=5)["agent_message"])
        self.assertIsInstance(stirred["temperature_c"], str)              # felt through the spoon, not read off
        oracle_env, ob, orr = scene([
            {"op": "spawn", "var": "pot", "type": "saucepan", "on": "{counter}"},
            {"op": "spawn", "var": "soup", "type": "soup", "in": "{pot}",
             "props": {"food": {"qty_g": 1000.0}, "mass_kg": 1.0, "temperature_c": 65.0, "_core_c": 65.0}},
            {"op": "spawn", "var": "spoon", "type": "wooden_spoon", "on": "{counter}"}])
        orr.skill("fetch", obj=ob["spoon"])
        self.assertIsInstance(msg_json(orr.do("stir", target=ob["pot"], tool=ob["spoon"], strokes=5)["message"])["temperature_c"], float)

    def test_smell_channel_and_observations_leak_no_ids_or_hidden_fields(self):
        env, b, r = scene([{"op": "spawn", "var": "box", "type": "storage_container_medium", "on": "{counter}", "props": {"open": True}},
                           {"op": "spawn", "var": "stew", "type": "stew", "in": "{box}",
                            "props": {"_spoil": 1.2, "food": {"qty_g": 500.0}, "mass_kg": 0.5, "volume_l": 0.5}}],
                          perception="realistic")
        obs = env.observe()
        smells = [s for s in obs.get("smells", []) if s["kind"] == "spoiled_food"]
        self.assertTrue(smells)
        self.assertIn(smells[0]["intensity"], ("faint", "noticeable", "strong"))
        self.assertEqual(true_ids(env, obs), set())
        details = r.do("inspect", target=b["box"])["agent_message"]
        self.assertEqual(true_ids(env, details), set())
        self.assertIn("food_contents", msg_json(details))
        for record in env.observe()["visible_entities"].values():
            for field in ("food", "_spoil", "_food_status", "contamination", "allergens", "opened_on"):
                self.assertNotIn(field, record)

    def test_counting_eggs_needs_the_carton_open(self):
        env, b, r = scene([{"op": "spawn", "var": "carton", "type": "egg_carton", "on": "{counter}", "props": {"open": False}},
                           {"op": "spawn", "var": "eggs", "type": "hen_egg", "in": "{carton}", "count": 7}],
                          perception="realistic")
        h = env.realism.handles.handle
        seen = env.observe()["visible_entities"]
        self.assertFalse([e for e in b["eggs"] if h(e) in seen])           # inside a closed carton
        r.skill("approach", target=b["carton"])
        closed = msg_json(r.do("inspect", target=b["carton"])["agent_message"])
        self.assertNotIn("whole_items", closed)
        r.do("open", target=b["carton"])
        details = msg_json(r.do("inspect", target=b["carton"])["agent_message"])
        self.assertEqual(details["whole_items"], 7)                        # counted by eye, exact up to 12
        self.assertEqual(len(details["food_contents"]), 7)
        r.do("report", target=b["carton"], field="count", value=7)
        self.assertTrue(ent(env, b["carton"])["_report_check"]["count"])
        r.do("report", target=b["carton"], field="count", value=12)
        self.assertFalse(ent(env, b["carton"])["_report_check"]["count"])

    def test_type_tiers_and_groups_are_declared(self):
        realism = self.env.realism
        rec = ent(self.env, self.b["potato"])
        self.assertEqual(realism.tier(rec, "food"), "never")
        self.assertEqual(realism.tier(rec, "allergens"), "never")        # loose food: ask, do not guess
        yog = ent(self.env, self.b["yog"])
        self.assertEqual(realism.tier(yog, "allergens"), "read")         # packaged: printed on the label
        self.assertEqual(realism.tier(yog, "date_label"), "read")
        self.assertEqual(realism.tier(yog, "mass_kg"), "measure")
        scale = ent(self.env, self.b["scale"])
        self.assertEqual(realism.tier(scale, "display"), "read")
        self.assertEqual(realism.tier(scale, "tare_g"), "never")
        groups = realism.groups()
        for name in ("flour_sugar_bag", "small_baking_box", "nut_butter_jar", "beverage_can", "dairy_tub", "spice_jar",
                     "measuring_cup", "egg_in_shell"):
            self.assertIn(name, groups)


class RealisticLeaks(unittest.TestCase):
    """Kernel v2.8 red-team round: nothing food says, refuses or takes time over identifies an unresolved look-alike or
    reveals a hidden value; estimates are fixed draws; percepts are the same for every member of a group."""

    def seen(self, env, eid):
        return env.observe()["visible_entities"].get(env.realism.handles.handle(eid), {})

    def bags(self):
        return scene([{"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter2}", "props": {"open": True}},
                      {"op": "spawn", "var": "sugar", "type": "sugar_bag", "on": "{counter2}", "props": {"open": True}},
                      {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter2}"},
                      {"op": "spawn", "var": "bowl2", "type": "mixing_bowl", "on": "{counter2}"}], perception="realistic")

    def test_portions_poured_from_an_unidentified_bag_stay_unidentified(self):
        env, b, r = self.bags()
        r.skill("approach", target=b["flour"])
        views, refusals, portions = [], set(), {}
        for src, bowl in (("flour", "bowl"), ("sugar", "bowl2")):
            refusals.add(r.do("transfer", target=b[src], destination=b[bowl], amount_g=100, expect="fail")["agent_message"])
            out = r.do("transfer", target=b[src], destination=b[bowl], amount_ml=150)
            data = msg_json(out["agent_message"])
            self.assertNotIn("moved_g", data)                       # a volume: the camera cannot see density
            self.assertIn("moved_ml", data)
            new = msg_json(out["message"])["new"]
            portions[src] = new
            self.assertEqual(ent(env, new)["derived_from"], b[src])
            glance = self.seen(env, new)
            # the group's portion descriptor: not "sugar" / "all purpose flour", and not the bag it came out of
            self.assertEqual(glance["label"], "pile of white powder")
            self.assertEqual(glance["kind"], "baking staple")
            self.assertNotIn("sugar", json.dumps(glance).replace("sugar bag", ""))
            contents = msg_json(r.do("inspect", target=b[bowl])["agent_message"])["food_contents"]
            self.assertEqual(len(contents), 1)
            views.append(contents[0])
            r.do("read", target=new, expect="fail")                # a scoop of powder carries no label
            self.assertEqual(self.seen(env, new)["label"], "pile of white powder")
        self.assertEqual(len(refusals), 1)                          # the same refusal for flour and sugar
        self.assertEqual(sorted(views[0]), sorted(views[1]))        # same view shape: no kind, no phase
        for view in views:
            for key in ("kind", "phase", "approx_g", "cut"):
                self.assertNotIn(key, view)
        r.do("inspect", target=b["flour"])                          # identifying the bag identifies what came out of it
        self.assertEqual(self.seen(env, portions["flour"])["label"], "all purpose flour")    # the portion's own label
        self.assertEqual(self.seen(env, portions["sugar"])["label"], "pile of white powder")
        r.do("inspect", target=portions["sugar"])                   # or a close look at the portion itself
        self.assertEqual(self.seen(env, portions["sugar"])["label"], "sugar")
        self.assertEqual(self.seen(env, b["sugar"])["label"], "paper bag of white powder")    # the bag is still unread

    def test_a_coating_whose_unidentified_source_is_gone_stays_unidentified(self):
        # kernel v2.9: percept.unidentified is Realism.unresolved, which answers for a destroyed id as it was when it went
        env, b, r = scene([{"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter2}", "props": {"open": True}},
                           {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter2}"},
                           {"op": "spawn", "var": "chicken", "type": "chicken_breast", "on": "{counter2}"}],
                          kitchen_args={"need": ["sink"]}, perception="realistic")
        r.skill("approach", target=b["flour"])
        portion = msg_json(r.do("transfer", target=b["flour"], destination=b["bowl"], amount_ml=150)["message"])["new"]
        r.skill("fetch", obj=b["chicken"])
        r.do("coat", target=b["chicken"], coating=b["bowl"], method="dredge")
        layer = ent(env, b["chicken"])["food"]["layers"][0]
        self.assertEqual(layer["_src"], portion)
        set_down(r, b, b["chicken"], where="counter2")
        r.skill("fetch", obj=b["bowl"])
        r.skill("approach", target=b["sink"])
        r.do("transfer", target=b["bowl"], destination=b["sink"], fraction=1.0)
        self.assertNotIn(portion, env.world.s["entities"])          # the rest of the powder went down the drain
        r.skill("approach", target=b["chicken"])
        view = msg_json(r.do("inspect", target=b["chicken"])["agent_message"])["food_view"]
        self.assertEqual([l["kind"] for l in view["layers"]], ["unidentified coating"])

    def test_consolidating_and_weighing_by_eye_need_identified_packages(self):
        env, b, r = self.bags()
        r.skill("approach", target=b["flour"])
        r.do("transfer", target=b["flour"], destination=b["bowl"], amount_ml=100)
        r.do("transfer", target=b["sugar"], destination=b["bowl2"], amount_ml=100)
        same = r.do("transfer", target=b["bowl"], destination=b["flour"], fraction=1.0, expect="fail")["agent_message"]
        other = r.do("transfer", target=b["bowl2"], destination=b["flour"], fraction=1.0, expect="fail")["agent_message"]
        self.assertEqual(same, other)                               # no "different product" oracle
        oracle_env, ob, orr = scene([{"op": "spawn", "var": "sugar", "type": "sugar_bag", "on": "{counter2}", "props": {"open": True}},
                                     {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter2}", "props": {"open": True}},
                                     {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter2}"}])
        orr.skill("approach", target=ob["flour"])
        orr.do("transfer", target=ob["sugar"], destination=ob["bowl"], amount_g=100)
        self.assertIn("different product", orr.do("transfer", target=ob["bowl"], destination=ob["flour"], fraction=1.0,
                                                  expect="fail")["message"])

    def test_eggs_are_told_apart_by_cracking_not_by_a_refusal(self):
        env, b, r = scene([{"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"},
                           {"op": "spawn", "var": "boiled", "type": "hard_boiled_egg", "on": "{counter}"},
                           {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"}], perception="realistic")
        views = [msg_json(r.do("inspect", target=b[k])["agent_message"]) for k in ("egg", "boiled")]
        self.assertEqual(views[0]["candidates"], views[1]["candidates"])     # no member-specific candidate list
        self.assertEqual(views[0]["kind"], views[1]["kind"])
        self.assertEqual(sorted(views[0]["food_view"]), sorted(views[1]["food_view"]))
        r.skill("fetch", obj=b["boiled"])
        out = r.do("crack_egg", target=b["boiled"], destination=b["bowl"])
        self.assertIn("cooked inside", msg_json(out["agent_message"])["result"])
        self.assertTrue(food(env, b["boiled"])["cracked"])
        self.assertEqual(self.seen(env, b["boiled"])["label"], "hard-boiled egg")   # now it is identified
        self.assertEqual(self.seen(env, b["egg"])["label"], "egg in its shell")
        r.skill("put", obj=b["boiled"], dest=b["counter"])
        r.skill("fetch", obj=b["egg"])
        self.assertIn("egg", msg_json(r.do("crack_egg", target=b["egg"], destination=b["bowl"])["message"]))
        oracle_env, ob, orr = scene([{"op": "spawn", "var": "boiled", "type": "hard_boiled_egg", "on": "{counter}"},
                                     {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"}])
        orr.skill("fetch", obj=ob["boiled"])
        self.assertIn("Peel cooked eggs", orr.do("crack_egg", target=ob["boiled"], destination=ob["bowl"], expect="fail")["message"])

    def test_estimates_are_fixed_draws_not_redrawn_by_moving(self):
        env, b, r = scene([{"op": "spawn", "var": "potato", "type": "sweet_potato", "on": "{counter2}"},
                           {"op": "spawn", "var": "chicken", "type": "chicken_breast", "on": "{counter}",
                            "props": {"temperature_c": 60.0, "_core_c": 58.0, "_peak_c": 82.0, "fixed_temperature": True}},
                           PROBE], perception="realistic")
        seen = set()
        for _ in range(3):
            r.skill("approach", target=b["potato"])
            seen.add(msg_json(r.do("inspect", target=b["potato"])["agent_message"])["food_view"]["approx_g"])
            r.skill("approach", target=b["counter"])
        self.assertEqual(len(seen), 1)
        r.skill("fetch", obj=b["probe"])
        r.skill("approach", target=b["chicken"])
        first = msg_json(r.do("inspect", target=b["chicken"], where="closely")["agent_message"])["probe_temp_c"]
        core = ent(env, b["chicken"])["_core_c"]
        second = msg_json(r.do("inspect", target=b["chicken"], where="closely")["agent_message"])["probe_temp_c"]
        self.assertAlmostEqual(second - first, ent(env, b["chicken"])["_core_c"] - core, delta=0.15)   # same probe error

    def test_searching_and_cutting_show_what_hands_and_knife_find(self):
        setup = [{"op": "spawn", "var": "fish", "type": "salmon_fillet", "on": "{board}", "props": {"_pin_bones": 0}},
                 {"op": "spawn", "var": "tweezers", "type": "fish_tweezers", "on": "{counter}"},
                 {"op": "spawn", "var": "roast", "type": "chicken_breast", "on": "{board}",
                  "props": {"temperature_c": 70.0, "_core_c": 50.0, "_peak_c": 180.0}},
                 {"op": "spawn", "var": "knife", "type": "carving_knife", "on": "{counter}"}]
        env, b, r = scene(setup, perception="realistic")
        r.skill("fetch", obj=b["tweezers"])
        found = msg_json(r.do("remove_part", target=b["fish"], part="pin_bones")["agent_message"])
        self.assertEqual((found["count"], found["found"]), (0, "none"))   # searched, found none (not a refusal)
        r.skill("put", obj=b["tweezers"], dest=b["counter"])
        r.skill("fetch", obj=b["knife"], grip="handle")
        carved = msg_json(r.do("carve", target=b["roast"])["agent_message"])
        self.assertEqual(carved["inside"], "pink and raw in the centre")   # the browned outside fooled it; the cut shows
        oracle_env, ob, orr = scene(setup)
        orr.skill("fetch", obj=ob["tweezers"])
        self.assertIn("no pin bones", orr.do("remove_part", target=ob["fish"], part="pin_bones", expect="fail")["message"])
        orr.skill("put", obj=ob["tweezers"], dest=ob["counter"])
        orr.skill("fetch", obj=ob["knife"], grip="handle")
        self.assertIn("Carve cooked meat", orr.do("carve", target=ob["roast"], expect="fail")["message"])

    def test_a_sealed_package_shows_nothing_of_what_is_inside(self):
        env, b, r = scene([{"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter2}",
                            "props": {"open": False, "opened": True, "food": {"qty_g": 300.0}, "mass_kg": 0.32}}],
                          perception="realistic")
        r.skill("approach", target=b["flour"])
        view = msg_json(r.do("inspect", target=b["flour"])["agent_message"])["food_view"]
        for key in ("approx_g", "approx_ml", "phase", "cut"):
            self.assertNotIn(key, view)
        self.assertEqual(view["kind"], "all_purpose_flour")                 # the printed product name is legible
        r.do("open", target=b["flour"])
        self.assertIn("approx_g", msg_json(r.do("inspect", target=b["flour"])["agent_message"])["food_view"])

    def test_fields_food_writes_on_other_entities_have_tiers(self):
        env, b, r = scene([], perception="realistic")
        realism = env.realism
        pan = {"type": "generic_item"}
        self.assertEqual(realism.tier(pan, "greased"), "glance")
        self.assertEqual(realism.tier(pan, "grease_g"), "never")
        self.assertEqual(realism.tier(pan, "served_to"), "never")
        self.assertEqual(realism.tier(pan, "food_label"), "read")
        self.assertEqual(realism.tier({"type": "freezer_ice_pack"}, "ice_frac"), "never")


class Integration(unittest.TestCase):
    """Behaviour that depends on every domain being loaded (crowded kitchens, appliance parts, kernel v2.1)."""

    def test_kitchen_helper_work_surfaces_reachable_from_the_stand_cell(self):
        from house_sim.kernel import geometry
        for seed in (0, 1, 2, 41, 97):
            env, b, r = scene([], seed=seed)
            for key in ("counter", "counter2", "board"):
                self.assertIsNone(geometry.reach_problem(env.world.s, b[key]), (seed, key, b[key]))

    def test_eaters_stay_approachable_in_a_cramped_kitchen(self):
        env, b, r = scene([{"op": "helper", "name": "food.eaters", "args": {"people": [
            {"id": "kid_a", "role": "child", "age": 6, "room": "kitchen"},
            {"id": "kid_b", "role": "child", "age": 8, "room": "kitchen"},
            {"id": "guest_a", "role": "guest", "age": 40, "room": "kitchen"},
            {"id": "guest_b", "role": "guest", "age": 50, "room": "kitchen"}]}}])
        cells = [tuple(env.world.s["people"][p]["pos"]) + (env.world.s["people"][p]["location"],)
                 for p in ("kid_a", "kid_b", "guest_a", "guest_b")]
        self.assertEqual(len(set(cells)), 4)                 # nobody stacked on one cell
        for pid in ("kid_a", "kid_b", "guest_a", "guest_b"):
            r.skill("approach", target=pid)                  # raises PlanError when someone is boxed in

    def test_kitchen_helper_fits_the_task_items_on_crowded_counters(self):
        # other domains crowd the counters (microwave, espresso machine, air fryer ...): the helper picks counters by
        # packing what the task will put on them, puts food dressing away, else adds a free-standing work counter
        setup = [{"op": "spawn", "var": "red", "type": "cutting_board_red", "on": "{counter}"},
                 {"op": "spawn", "var": "green", "type": "cutting_board_green", "on": "{counter2}"},
                 {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
                 {"op": "spawn", "var": "carton", "type": "egg_carton", "on": "{counter2}"},
                 {"op": "spawn", "var": "cup", "type": "prep_bowl", "on": "{counter2}"},
                 {"op": "spawn", "var": "box", "type": "storage_container_small", "on": "{counter2}"}]
        for seed in (0, 1, 2, 4, 14, 41, 97):
            env, b, r = scene(setup, seed=seed, kitchen_args={"need": ["trash", "compost"], "board": False})
            st = env.world.s
            for var, key in (("red", "counter"), ("bowl", "counter"), ("green", "counter2"), ("carton", "counter2")):
                self.assertEqual(st["entities"][b[var]]["location"], b[key], (seed, var))

    def test_kitchen_helper_space_probe_matches_placement(self):
        from house_sim.systems.food import scenarios as SC
        env, b, r = scene([], seed=4, kitchen_args={"need": [], "board": False,
                                                     "room_for": {"counter": ["kitchen_scale"], "counter2": ["mixing_bowl", "all_purpose_flour"]}})
        for t, key in (("kitchen_scale", "counter"), ("mixing_bowl", "counter2"), ("all_purpose_flour", "counter2")):
            eid = env.world.spawn(t, b[key], relation="on")
            spot = SC.geometry.surface_spot(env.world.s, b[key], eid)
            self.assertIsNotNone(spot, (t, key))
            env.world.mut(eid)["spos"] = spot

    def test_eaters_never_cut_the_robot_off_from_the_work_area(self):
        # a person in a one-cell corridor can split the kitchen so that the counter is only reachable from a doorway the
        # robot does not use; the robot walks in from its start room and must reach every work surface and every eater
        for seed in (0, 1, 2, 41, 97):
            env, b, r = scene([{"op": "helper", "name": "food.eaters", "args": {"people": [
                {"id": "kid_a", "role": "child", "age": 2, "room": "kitchen"},
                {"id": "kid_b", "role": "child", "age": 8, "room": "kitchen"},
                {"id": "guest_a", "role": "guest", "age": 40, "room": "kitchen"}]}}], seed=seed, place_robot=False)
            for target in ("counter", "counter2", "board", "fridge", "sink", "trash", "compost"):
                if b.get(target):
                    r.skill("approach", target=b[target])
            for pid in ("kid_a", "kid_b", "guest_a"):
                r.skill("approach", target=pid)

    def test_an_eater_never_stands_in_a_corridor_that_splits_the_kitchen(self):
        # 7 x 3 kitchen: a counter fills the top-left, a one-cell corridor runs along the bottom between the living-room
        # doorway (0,0) and the pantry doorway (6,2). A person in the corridor leaves every cell reachable from SOME
        # doorway (the old union check passed) but cuts the robot's entry doorway off from the far side.
        import random
        from house_sim.kernel.registry import default_registry
        from house_sim.systems.food import scenarios as SC
        from house_sim.tasks.setup import SetupCtx
        for seed in range(12):
            config = {"rooms": {"living_room_1": {"type": "living_room", "size_m": [3.0, 3.0]},
                                "kitchen_1": {"type": "kitchen", "size_m": [3.5, 1.5]},
                                "pantry_1": {"type": "pantry", "size_m": [1.5, 1.5]}},
                      "connections": {"c1": {"rooms": ["living_room_1", "kitchen_1"], "kind": "doorway",
                                             "cells": {"living_room_1": [5, 2], "kitchen_1": [0, 0]}},
                                      "c2": {"rooms": ["kitchen_1", "pantry_1"], "kind": "doorway",
                                             "cells": {"kitchen_1": [6, 2], "pantry_1": [0, 0]}}},
                      "entities": {"counter_1": {"type": "counter", "is_a": ["surface_furniture"], "location": "kitchen_1",
                                                 "pos": [1, 0], "size_m": [2.0, 0.6, 0.9], "blocks_path": True,
                                                 "accepts_items": True, "surface": True}},
                      "people": {"kid_1": {"label": "kid"}}, "pets": {}, "start_room": "living_room_1",
                      "robot": {"room": "living_room_1", "pos": None}}
            ctx = SetupCtx(config, default_registry(), random.Random(seed), seed, {})
            cell = SC._free_agent_cell(ctx, "kitchen_1", "kid_1", False)
            self.assertIn(tuple(cell), {(5, 0), (5, 1), (6, 0)}, seed)

    def test_every_dwelling_gets_a_roomy_chemical_free_pantry(self):
        # some apartment kitchens have only wall units and drawers: the helper never binds a 0.30 m deep wall unit or the
        # under-sink cupboard with the cleaners as the pantry; it adds a stand-in when nothing fits
        for dwelling in ("apartment", "townhouse", "house"):
            for seed in (0, 1, 2):
                env, b, r = scene([], seed=seed, house={"residents": "minimal", "pets": "none", "dwelling": dwelling})
                st = env.world.s
                pantry = st["entities"][b["pantry"]]
                if not os.environ.get("HOUSE_SIM_DOMAINS"):
                    # core-only furniture can wall a kitchen down to a few cells; a shallow wall unit is the last resort
                    self.assertGreaterEqual(pantry["inner_size_m"][1], 0.35, (dwelling, seed, b["pantry"]))
                self.assertIsNotNone(b["pantry_low"])
                from house_sim.systems.food import model as M
                for eid, rec in st["entities"].items():
                    if rec.get("location") == b["pantry"]:
                        self.assertFalse(M.is_chemical(rec), (dwelling, seed, eid))
                r.skill("approach", target=b["pantry"])

    def test_three_bowl_skill_brings_the_cup_beside_the_yolk_bowl(self):
        # crowded kitchens can leave the white cup and the yolk bowl on counters no single floor cell reaches: the skill
        # carries the empty cup over first instead of failing "out of arm's reach"
        from house_sim.kernel import geometry
        from house_sim.systems.food import skills as SK
        for seed in (0, 1, 2, 41, 97, 3, 5):
            env, b, r = scene([{"op": "spawn", "var": "yolks", "type": "small_mixing_bowl", "on": "{counter}"}], seed=seed)
            st = env.world.s
            far = [s for s in sorted(st["entities"]) if st["entities"][s].get("location") == b["kitchen"]
                   and st["entities"][s].get("surface") and geometry.blocks(st["entities"][s]) and s != b["counter"]
                   and not st["entities"][s].get("heat_source") and "counter" in st["entities"][s].get("is_a", []) + [st["entities"][s].get("type")]]
            for surface in far:
                cup = env.world.spawn("prep_bowl", surface, relation="on")
                spot = geometry.surface_spot(env.world.s, surface, cup)
                if spot is None:
                    env.world.destroy(cup)
                    continue
                env.world.mut(cup)["spos"] = spot
                env.world.commit()
                if SK._shared_cell(env.world.s, [cup, b["yolks"]]):
                    env.world.destroy(cup)
                    env.world.commit()
                    continue
                self.assertTrue(SK._side_by_side(r, cup, b["yolks"]))
                self.assertTrue(SK._shared_cell(env.world.s, [cup, b["yolks"]]))
                return
        self.skipTest("no generated kitchen with two counters out of common reach")

    def test_invariants_take_the_world_and_agree_with_a_full_scan(self):
        from house_sim.kernel import plugins
        from house_sim.kernel.world import clone
        env, b, r = scene([{"op": "spawn", "var": "chicken", "type": "chicken_breast", "on": "{board}"},
                           {"op": "spawn", "var": "cuke", "type": "cucumber", "on": "{counter}"}])
        base = clone(env.world.s)
        cont = env.world.mut(b["cuke"]).setdefault("contamination", {})
        cont["raw_meat"] = 0.4
        for name in ("food.rte_cross_contamination", "food.cold_chain_break", "food.solids_down_drain", "food.fat_down_drain"):
            fn = plugins.INVARIANTS[name]
            self.assertEqual(fn(env.world.s, base, env.world), fn(env.world.s, base, None), name)
        self.assertEqual(plugins.INVARIANTS["food.rte_cross_contamination"](env.world.s, base, env.world), "food.rte_cross_contamination")

    def test_food_in_a_fridge_bin_sits_in_fridge_air_not_in_contact_with_the_bin(self):
        def run(material, category):
            env, b, r = scene([
                {"op": "spawn", "var": "bin", "type": "storage_bin", "in": "{fridge}", "shelf": "{fridge_bottom}",
                 "props": {"label": "crisper drawer", "conducts_heat": True, "heat_coupling": 0.8, "material": material,
                           "category": category, "temperature_c": 21.0, "container": True, "open": True,
                           "size_m": [0.3, 0.4, 0.2], "inner_size_m": [0.28, 0.38, 0.18], "capacity_l": 15.0}},
                {"op": "spawn", "var": "lettuce", "type": "lettuce", "in": "{bin}", "props": {"temperature_c": 4.0, "_core_c": 4.0}}],
                kitchen_args={"need": ["fridge"]})
            r.do("close", target=b["fridge"]) if env.world.s["entities"][b["fridge"]].get("open") else None
            r.do("wait", seconds=1800)
            return ent(env, b["lettuce"]).get("_peak_c", 4.0)
        self.assertLess(run("plastic", "appliance_part"), 6.0)       # fridge air, the warm plastic bin does not matter
        self.assertGreater(run("steel", "cookware"), 6.0)            # a metal pot that is still warm does drive its contents

    def test_skipping_slow_items_between_full_passes_is_lossless(self):
        def run(chunks):
            env, b, r = scene([{"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}",
                                "props": {"temperature_c": 4.0, "_core_c": 4.0}}])
            for _ in range(chunks):
                r.do("wait", seconds=3600 // chunks)
            rec = ent(env, b["milk"])
            return rec["temperature_c"], rec.get("_tfast")
        one, fast = run(1)
        many, _ = run(12)
        self.assertGreater(one, 8.0)                     # a 2 L carton warms on the counter ...
        self.assertAlmostEqual(one, many, delta=0.3)     # ... the same whether observed once or every 5 min

    def test_damage_is_a_dict_and_legacy_lists_are_converted(self):
        from house_sim.systems.food import model as M
        rec = {"damage": ["scratched"]}
        M.add_damage(rec, "heat_mark", 0.4)
        self.assertEqual(rec["damage"], {"scratched": 1.0, "heat_mark": 0.4})
        M.add_damage(rec, "heat_mark", 0.2)
        self.assertEqual(rec["damage"]["heat_mark"], 0.4)

    def test_derived_status_uses_the_food_prefixed_field(self):
        env, b, r = scene([{"op": "helper", "name": "food.dated", "args": {"type": "yogurt_snack_cup", "days": -3, "var": "yog",
                                                                           "on": "{counter}", "date_type": "use_by"}}])
        rec = ent(env, b["yog"])                         # computed at episode start, before any action
        self.assertEqual(rec["_food_status"]["class"], "unsafe")
        self.assertNotIn("_status", rec)
        self.assertNotIn("_odor", rec)                   # safety owns _odor (room-air odor sources)


if __name__ == "__main__":
    unittest.main()
