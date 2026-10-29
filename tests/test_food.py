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


def scene(setup, seed=0, house=None, kitchen_args=None, place_robot=True, perception=None, camera=None, notices=False):
    """A kitchen scene built through the spec machinery. camera: "geometric" for the posed cameras; notices: turn the
    kernel notice bus on (the state carries the "notices" section, as schema-3 tasks do)."""
    ops = [{"op": "helper", "name": "food.kitchen", "args": kitchen_args or {}}] + list(setup)
    if place_robot:
        ops.append({"op": "robot", "room": "{kitchen}", "cell": "{stand}"})
    spec = {"catalog_id": "test-food", "house": house or {"residents": "minimal", "pets": "none"}, "setup": ops,
            "success": [{"path": "time_s", "op": "gte", "value": 0}], "reference": []}
    if camera:
        spec["camera"] = camera
    config, task, oracle = build(spec, seed)
    if notices:
        config["notices"] = []
    env = HouseEnv(config, task, seed, trace=False, observe_on_step=False, perception=perception, camera=camera)
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
        # the blade cut the steadying hand's gripper pad: a typed body fault (body_faults), which makes that pad slip
        knife_hand = next(h for h, held in env.world.s["robot"]["hands"].items() if held == b["knife"])
        other = "left" if knife_hand == "right" else "right"
        state = env.world.s["robot"]["fault_state"]
        self.assertIn("gripper_cut_" + other, state)
        self.assertTrue(state["gripper_cut_" + other]["detected"])
        self.assertIn("gripper_slip_" + other, state)
        self.assertTrue(all(isinstance(entry, dict) for entry in env.world.s["robot"]["faults"]))
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
        from house_sim.kernel.registry import default_registry
        if not default_registry().known("stovetop_kettle"):
            self.skipTest("stovetop_kettle is an appliances_cooking type (HOUSE_SIM_DOMAINS without it)")
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
                     "egg_in_shell"):
            self.assertIn(name, groups)
        # measures of different sizes are told apart by their size (phase-1 red team: the geometric camera did)
        self.assertNotIn("measuring_cup", groups)
        self.assertNotIn("measuring_spoon", groups)


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



def drop_event(eid_var, at_s=30, height_m=0.9):
    """A physics.drop perturbation (someone drops it; exogenous) at at_s."""
    return {"op": "event", "at_s": at_s, "effect": "physics.drop", "args": {"target": "{" + eid_var + "}", "height_m": height_m}}


def spills_of(env, liquid=None):
    """Floor spill entities (kernel puddles, or cleaning's spill entities while cleaning is loaded) of a liquid."""
    state = env.world.s
    return [eid for eid, rec in sorted(state["entities"].items())
            if rec.get("location") in state["rooms"] and (rec.get("_phys_spill") or rec.get("category") == "spill")
            and (liquid is None or rec.get("liquid") == liquid)]


class TierOnePhysics(unittest.TestCase):
    """Food in the kernel physics layer (kernel v3.0; docs/domains/food.md "Physics"): phys.props (packaging,
    produce), phys.broke (eggs, packages), phys.fell (bruises), kernel spills, the freeze-burst rule, kernel masses."""

    def props(self, env, eid):
        from house_sim.kernel import physics
        return physics.props(env.world.s, eid)

    def test_a_retail_package_is_physically_its_packaging(self):
        env, b, r = scene([
            {"op": "spawn", "var": "honey", "type": "honey_jar", "on": "{counter}", "props": {"phys_exact": True}},
            {"op": "spawn", "var": "can", "type": "canned_tomatoes", "on": "{counter}", "props": {"phys_exact": True}},
            {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}", "props": {"phys_exact": True}},
            {"op": "spawn", "var": "bread", "type": "bread_loaf", "on": "{counter2}", "props": {"phys_exact": True}},
            {"op": "spawn", "var": "chips", "type": "chips_bag", "on": "{counter2}", "props": {"phys_exact": True}}])
        honey, can, milk = self.props(env, b["honey"]), self.props(env, b["can"]), self.props(env, b["milk"])
        self.assertEqual((honey.cls, honey.break_mode), ("glass", "shatter"))     # a jar of honey breaks like a jar
        self.assertEqual(can.cls, "steel")
        self.assertEqual(can.drop_tol_m, float("inf"))                           # a can dents, it does not break
        self.assertEqual(milk.cls, "lined_paperboard")
        self.assertTrue(milk.crushable)
        self.assertEqual(self.props(env, b["bread"]).crush_kg, 1.5)              # a sliced loaf squashes
        self.assertEqual(self.props(env, b["chips"]).crush_kg, 1.0)
        self.assertEqual(ent(env, b["honey"])["material"], "organic")            # nothing is written to the record

    def test_a_record_override_never_leaks_into_another_packages_physics(self):
        # the packaging values are memoised per type: one record's own drop tolerance must stay its own, whichever
        # record the process saw first (a leak would make a run depend on what ran before it in the same process)
        from house_sim.kernel.phys import props as P
        from house_sim.kernel.registry import default_registry
        from house_sim.systems.food import physical as PH
        plain = dict(default_registry().make("honey_jar", "probe_room"), phys_exact=True)
        tough = dict(plain, drop_tolerance_m=2.0)
        PH._PACK_MEMO.clear()
        alone = P.props_of(plain).drop_tol_m
        PH._PACK_MEMO.clear()
        self.assertEqual(P.props_of(tough).drop_tol_m, 2.0)
        self.assertEqual(P.props_of(plain).drop_tol_m, alone)
        self.assertLess(alone, 2.0)

    def test_look_alike_packages_share_one_packaging(self):
        # a drop or a squeeze must never tell the members of a look-alike group apart
        from house_sim.kernel.phys import props as P
        from house_sim.kernel.registry import default_registry
        reg = default_registry()
        for group, spec in reg.looks_like.items():
            members = [m for m in spec.get("members", []) if reg.known(m) and reg.resolve(m).get("package")]
            classes = {P.props_of(reg.make(m, "probe_room")).cls for m in members}
            self.assertLessEqual(len(classes), 1, (group, classes))

    def test_round_whole_produce_rolls_and_ripe_fruit_bruises_from_lower(self):
        env, b, r = scene([
            {"op": "spawn", "var": "hard", "type": "peach", "on": "{counter}", "props": {"phys_exact": True, "food": {"ripeness": 0.2}}},
            {"op": "spawn", "var": "ripe", "type": "peach", "on": "{counter}", "props": {"phys_exact": True, "food": {"ripeness": 0.95}}},
            {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"}])
        hard, ripe = self.props(env, b["hard"]), self.props(env, b["ripe"])
        self.assertEqual(hard.shape, "sphere")
        self.assertTrue(hard.rollable)
        self.assertLess(ripe.drop_tol_m, hard.drop_tol_m)        # a ripe peach bruises from a lower drop ...
        self.assertGreater(ripe.softness, hard.softness)         # ... and is softer
        env.world.s["entities"][b["hard"]]["food"]["cut"] = "halved"
        self.assertNotEqual(self.props(env, b["hard"]).shape, "sphere")      # halves do not roll

    def test_a_dropped_raw_egg_becomes_a_raw_egg_spill_and_its_shell(self):
        env, b, r = scene([{"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"}, drop_event("egg")])
        r.do("wait", seconds=60)
        rec = ent(env, b["egg"])
        self.assertTrue(rec["broken"])
        self.assertEqual(rec["food"]["kind"], "eggshell")
        self.assertLess(rec["food"]["qty_g"], 10.0)
        self.assertGreaterEqual(rec["contamination"]["raw_meat"], 0.3)
        puddles = spills_of(env, "raw_egg")
        self.assertTrue(puddles)
        self.assertEqual(env.world.s["stats"].get("phys_spills"), 1)
        self.assertEqual(env.world.s["stats"].get("spills", 0), 0)    # someone else dropped it: not the robot's harm

    def test_a_dropped_hard_boiled_egg_only_cracks(self):
        env, b, r = scene([{"op": "spawn", "var": "egg", "type": "hard_boiled_egg", "on": "{counter}"}, drop_event("egg")])
        r.do("wait", seconds=60)
        rec = ent(env, b["egg"])
        self.assertTrue(rec["food"]["cracked"])
        self.assertEqual(rec["food"]["kind"], "hard_boiled_egg")
        self.assertFalse(spills_of(env, "raw_egg"))

    def test_a_dropped_jar_breaks_spills_and_what_stays_is_unsafe(self):
        env, b, r = scene([{"op": "spawn", "var": "jam", "type": "honey_jar", "on": "{counter}"}, drop_event("jam", height_m=1.2)])
        before = food(env, b["jam"])["qty_g"]
        r.do("wait", seconds=60)
        rec = ent(env, b["jam"])
        self.assertTrue(rec["broken"])
        self.assertTrue(rec["_unsafe"])
        self.assertAlmostEqual(rec["food"]["qty_g"], before / 2.0, delta=1.0)     # honey oozes: half stays in the shards
        self.assertTrue(spills_of(env, "honey"))
        self.assertEqual(M_eater(env, b["jam"]), ["unsafe_food:" + b["jam"]])

    def test_a_dropped_peach_bruises_and_picks_up_bacteria_from_the_floor(self):
        env, b, r = scene([{"op": "spawn", "var": "peach", "type": "peach", "on": "{counter}", "props": {"food": {"ripeness": 0.9}}},
                           drop_event("peach")])
        r.do("wait", seconds=60)
        rec = ent(env, b["peach"])
        self.assertFalse(rec.get("broken", False))
        self.assertGreater(rec["food"]["bruise"], 0.3)
        self.assertIn("bruised", rec["appearance"])
        self.assertGreaterEqual(rec["contamination"]["bacteria"], 0.2)

    def test_liquid_food_spills_are_kernel_spills_and_solids_only_soil(self):
        env, b, r = scene([
            {"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"}])
        r.skill("fetch", obj=b["egg"])
        res = r.do("crack_egg", target=b["egg"], destination=b["bowl"], force="hard")      # 35 % misses the bowl
        self.assertTrue(res["ok"])
        stats = env.world.s["stats"]
        self.assertEqual(stats.get("phys_spills"), 1)
        self.assertEqual(stats.get("spills"), 1)                       # the robot's spill
        counter = ent(env, b["counter"])
        self.assertGreater(float(counter.get("wetness", 0.0)), 0.0)    # the white wets the counter top (kernel film)
        self.assertIn("food", counter.get("soil", {}))                 # and leaves food residue (food's soil mark)

    def test_freezing_a_full_glass_bottle_bursts_it_an_open_jar_does_not(self):
        env, b, r = scene([
            {"op": "spawn", "var": "wine", "type": "wine_bottle", "in": "{freezer}", "props": {"temperature_c": 0.0}},
            {"op": "spawn", "var": "jar", "type": "mason_jar", "in": "{freezer}", "props": {"open": True}},
            {"op": "spawn", "var": "stock", "type": "food_item", "in": "{jar}",
             "props": {"food": {"kind": "chicken_stock", "cat": "prepared", "phase": "liquid", "qty_g": 900.0, "density": 1.0},
                       "mass_kg": 0.9, "volume_l": 0.9, "temperature_c": 0.0, "_core_c": 0.0}}],
            kitchen_args={"need": ["freezer"]})
        r.do("wait", seconds=12 * 3600)
        self.assertTrue(ent(env, b["wine"]).get("broken"))                # sealed, full, rigid glass: it burst ...
        self.assertGreater(food(env, b["wine"])["ice"], 0.3)               # ... once about a third had frozen
        self.assertEqual(food(env, b["wine"])["qty_g"], 0.0)               # and the wine ran out
        self.assertGreaterEqual(env.world.s["stats"].get("phys_breaks", 0), 1)
        self.assertFalse(ent(env, b["jar"]).get("broken", False))        # an open jar: the ice rises out of the mouth

    def test_the_scale_weighs_liquids_at_their_density(self):
        env, b, r = scene([
            {"op": "spawn", "var": "scale", "type": "kitchen_scale", "on": "{counter}", "props": {"on": True, "tare_g": 0.0}},
            {"op": "spawn", "var": "box", "type": "storage_container_medium", "on": "{scale}",
             "props": {"liquid": "oil", "liquid_l": 1.0, "mass_kg": 0.2}}])
        from house_sim.systems.food import systems as S
        self.assertAlmostEqual(S.scale_load_g(env.world.s, b["scale"]), 200.0 + 920.0, delta=0.01)   # oil: 0.92 kg/L

    def test_shelf_ratings_and_loads_are_the_kernels(self):
        from house_sim.systems.food import model as M
        self.assertEqual(M.shelf_rating({"type": "upper_cabinet", "is_a": ["cabinet"]}), 20.0)
        self.assertEqual(M.shelf_rating({"type": "shelf_unit"}), 90.0)
        self.assertEqual(M.shelf_rating({"type": "cabinet", "shelf_load_kg": 12.0}), 12.0)
        ents = {"cab": {"type": "cabinet", "shelf_heights": [0.0, 0.4]},
                "a": {"location": "cab", "relation": "in", "shelf": 1, "mass_kg": 1.0},
                "b": {"location": "cab", "relation": "in", "shelf": 1, "mass_kg": 0.5, "liquid": "water", "liquid_l": 0.5},
                "c": {"location": "b", "relation": "in", "mass_kg": 0.25}}
        self.assertAlmostEqual(M.shelf_load(ents, "cab", 1), 2.25, places=6)    # liquids and contents included
        self.assertEqual(M.shelf_load(ents, "cab", 0), 0.0)

    def test_set_down_law_breaks_a_fast_placed_egg_into_a_spill(self):
        # food is ready for the kernel's set-down law: an egg the kernel breaks becomes the raw-egg spill
        from house_sim.kernel.phys import lawbook
        with lawbook.forced(setdown=True):
            env, b, r = scene([{"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter2}"}])
            r.skill("fetch", obj=b["egg"])
            r.do("set_speed", target="fast")
            r.skill("put", obj=b["egg"], dest=b["counter"])
            rec = ent(env, b["egg"])
            self.assertTrue(rec.get("broken"))
            self.assertEqual(rec["food"]["kind"], "eggshell")
            self.assertEqual(env.world.s["stats"].get("spills"), 1)
            env2, b2, r2 = scene([{"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter2}"}])
            r2.skill("fetch", obj=b2["egg"])
            r2.skill("put", obj=b2["egg"], dest=b2["counter"])            # a normal set-down: 2 cm, the egg survives
            self.assertFalse(ent(env2, b2["egg"]).get("broken", False))


def M_eater(env, eid):
    from house_sim.systems.food import model as M
    pid = sorted(env.world.s["people"])[0]
    return [i for i in M.eater_issues(env.world.s, pid, [eid]) if i.startswith("unsafe_food")]


class TierOneBody(unittest.TestCase):
    """The robot body (kernel v3.0): typed faults, soiled grippers, commanded durations, quiet mode."""

    def test_kneading_by_hand_soils_both_grippers(self):
        env, b, r = scene([{"op": "spawn", "var": "dough", "type": "bread_dough", "on": "{board}"}])
        r.do("knead", target=b["dough"], minutes=8, added_flour_g=20)
        soil = env.world.s["robot"]["hand_soil"]
        for hand in ("left", "right"):
            self.assertGreaterEqual(soil[hand]["food"], 0.3)
            self.assertGreaterEqual(soil[hand]["flour"], 0.1)
        # slippery fingers: a latent gripper_slip fault on each hand (body.soil_hand)
        self.assertIn("gripper_slip_left", env.world.s["robot"]["fault_state"])

    def test_a_commanded_kneading_time_is_not_stretched_by_careful_speed(self):
        def seconds(speed):
            env, b, r = scene([{"op": "spawn", "var": "dough", "type": "bread_dough", "on": "{board}"}])
            if speed:
                r.do("set_speed", target=speed)
            t0 = env.world.s["time_s"]
            r.do("knead", target=b["dough"], minutes=2)
            return env.world.s["time_s"] - t0
        self.assertEqual(seconds("careful"), seconds(None))

    def test_quiet_mode_refuses_the_electric_grinder_not_the_mortar(self):
        def run(kind):
            env, b, r = scene([
                {"op": "spawn", "var": "grinder", "type": kind, "on": "{counter}", "props": {"open": False}},
                {"op": "spawn", "var": "pepper", "type": "food_item", "in": "{grinder}",
                 "props": {"food": {"kind": "peppercorns", "cat": "spice", "phase": "granular", "qty_g": 10.0, "density": 0.5},
                           "mass_kg": 0.01, "volume_l": 0.02}},
                {"op": "spawn", "var": "pestle", "type": "spice_pestle", "on": "{counter}"}])
            r.do("set_quiet", target="on")
            if kind == "spice_mortar":
                r.skill("fetch", obj=b["pestle"])
            return step(env, "grind", b["grinder"], seconds=10)
        loud = run("spice_grinder")
        self.assertFalse(loud["ok"])
        self.assertIn("Quiet mode", loud["message"])
        self.assertTrue(run("spice_mortar")["ok"])

    def _greasy_dice(self, oil, sharpness=1.0, wash=False):
        from house_sim.kernel import body
        env, b, r = scene([{"op": "spawn", "var": "onion", "type": "onion", "on": "{board}"},
                           {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}",
                            "props": {"sharpness": sharpness}}])
        knife_ready(r, b)
        r.do("cut", target=b["onion"], style="halve", axis="lengthwise")
        hand = next(h for h, held in env.world.s["robot"]["hands"].items() if held == b["knife"])
        w = env.world
        w.begin()
        body.soil_hand(w, hand, "oil", oil)
        if wash:
            body.clean_hand(w, "both")
        w.commit()
        return env, b, step(env, "dice", b["onion"], size="medium")

    def test_greasy_fingers_on_the_knife_handle_cut_unevenly_and_with_a_dull_blade_slip(self):
        env, b, res = self._greasy_dice(1.0)
        self.assertNotIn("slipped", res["message"])                       # greasy fingers alone: no slip ...
        self.assertAlmostEqual(food(env, b["onion"])["piece_cv"], 0.2, places=3)     # ... but uneven pieces
        env, b, res = self._greasy_dice(1.0, sharpness=0.3)
        self.assertIn("handle slid in greasy fingers", res["message"])     # with a dull blade the cut slips
        self.assertEqual(env.world.s["stats"]["damage"], 1)
        env, b, res = self._greasy_dice(1.0, sharpness=0.3, wash=True)
        self.assertNotIn("greasy", res["message"])                       # washed hands: only the dull blade's risk
        self.assertEqual(food(self._greasy_dice(0.0)[0], b["onion"])["piece_cv"], 0.12)

    def test_pin_bones_and_an_oyster_hinge_need_a_precise_hand(self):
        from house_sim.kernel import body

        def run(target_type, tool_type, action, oil, speed=None, **kw):
            env, b, r = scene([
                {"op": "spawn", "var": "food", "type": target_type, "on": "{board}", "props": {"temperature_c": 4.0}},
                {"op": "spawn", "var": "tool", "type": tool_type, "on": "{counter}"},
                {"op": "spawn", "var": "glove", "type": "kitchen_cut_glove", "on": "{counter}"}])
            r.skill("fetch", obj=b["glove"])
            r.do("wear", target=b["glove"])
            r.skill("fetch", obj=b["tool"], grip="handle")
            if speed:
                r.do("set_speed", target=speed)
            hand = next(h for h, held in env.world.s["robot"]["hands"].items() if held == b["tool"])
            w = env.world
            w.begin()
            body.soil_hand(w, hand, "oil", oil)
            w.commit()
            return step(env, action, b["food"], **kw)
        # a humanoid hand positions within 2 mm; slippery fingers x (1 + 0.5 x slipperiness), fast speed x 1.5
        self.assertTrue(run("salmon_fillet", "fish_tweezers", "remove_part", 0.0, part="pin_bones")["ok"])
        fumbled = run("salmon_fillet", "fish_tweezers", "remove_part", 0.6, part="pin_bones")    # 2.6 mm > 2.5
        self.assertFalse(fumbled["ok"])
        self.assertEqual(fumbled["message"], "Cannot position precisely enough for gripping pin bone tips right now")
        self.assertTrue(run("oysters", "oyster_knife", "shuck", 1.0, count=1)["ok"])            # 3 mm <= 4 mm
        rushed = run("oysters", "oyster_knife", "shuck", 1.0, speed="fast", count=1)            # 4.5 mm
        self.assertFalse(rushed["ok"])
        self.assertIn("into the hinge", rushed["message"])
        self.assertTrue(run("oysters", "oyster_knife", "shuck", 1.0, speed="fast", count=1, method="side")["ok"])

    def test_the_food_robot_field_is_declared_hidden(self):
        from house_sim.kernel import body
        entry = body.robot_fields()["hand_contamination"]
        self.assertEqual((entry["owner"], entry["tier"], entry["materialize"]), ("food", "hidden", False))


class TierOneCamera(unittest.TestCase):
    """The geometric camera (kernel v3.0): shelf rows, reading dates, counting by eye, visual events. Room mode keeps
    its outputs."""

    def test_food_put_on_a_fridge_shelf_keeps_its_row_for_the_camera(self):
        env, b, r = scene([{"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}"}], kitchen_args={"need": ["fridge"]})
        r.skill("open", target=b["fridge"])
        r.skill("put", obj=b["milk"], dest=b["fridge"], relation="in", shelf=b["fridge_mid"])
        rec = ent(env, b["milk"])
        self.assertEqual(rec["spos"], rec["inner_spos"])         # the FIFO position is the camera's row position
        r.skill("fetch", obj=b["milk"])
        r.skill("put", obj=b["milk"], dest=b["counter"])
        self.assertNotIn("inner_spos", ent(env, b["milk"]))

    def test_dates_on_packs_are_small_print_and_tub_dates_are_on_the_lid(self):
        from house_sim.kernel import sight
        env, b, r = scene([{"op": "spawn", "var": "tub", "type": "yogurt_tub", "on": "{counter}"},
                           {"op": "spawn", "var": "box", "type": "storage_container_medium", "on": "{counter}"}])
        st = env.world.s
        self.assertEqual(sight.text_m(st, b["tub"], "date_label"), 0.003)
        self.assertEqual(sight.gated_face(st, b["tub"], "date_label"), "lid")
        self.assertEqual(sight.text_m(st, b["box"], "food_label"), 0.008)

    def test_a_date_read_in_geometric_mode_needs_the_cameras_read_rule(self):
        env, b, r = scene([{"op": "helper", "name": "food.dated",
                            "args": {"type": "yogurt_tub", "days": 3, "var": "tub", "on": "{counter2}"}}], camera="geometric")
        r.skill("approach", target=b["tub"])
        details = msg_json(r.do("inspect", target=b["tub"], where="closely")["message"])
        self.assertIn("date_label", details["food"])             # close enough (head or wrist camera) to read it

    def test_spills_are_visual_events_the_camera_can_witness(self):
        env, b, r = scene([
            {"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"}], camera="geometric")
        r.skill("fetch", obj=b["egg"])
        r.do("crack_egg", target=b["egg"], destination=b["bowl"], force="hard")
        events = [e for e in env.world.s["camera_log"]["events"] if e["kind"] == "food_spill"]
        self.assertTrue(events)
        self.assertTrue(events[-1]["seen"])                      # right in front of the robot: seen
        log = [e for e in env.world.s["event_log"] if e.get("kind") == "food_spill"]
        self.assertTrue(log)                                     # the event log entry itself is unchanged

    def test_room_mode_states_carry_no_camera_fields(self):
        env, b, r = scene([{"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"},
                           {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"}])
        r.skill("fetch", obj=b["egg"])
        r.do("crack_egg", target=b["egg"], destination=b["bowl"], force="hard")
        self.assertNotIn("camera_log", env.world.s)
        self.assertNotIn("view", env.world.s["robot"])


class TierOneSweep(unittest.TestCase):
    """Notices, dialogue routes, idle probes and generation-2 houses (kernel v3.1)."""

    def test_a_spraying_can_is_heard_as_a_hiss(self):
        env, b, r = scene([{"op": "spawn", "var": "soda", "type": "canned_soda", "on": "{counter}",
                            "props": {"_agitated_until": 600.0}}], notices=True)
        r.skill("fetch", obj=b["soda"])
        r.do("open", target=b["soda"])
        kinds = [n["kind"] for n in env.world.s["notices"]]
        self.assertIn("hiss", kinds)

    def test_burnt_toast_smoke_is_seen_as_a_smoke_notice(self):
        from house_sim.systems.food import systems as S
        env, b, r = scene([{"op": "spawn", "var": "bread", "type": "food_item", "on": "{counter}",
                            "props": {"food": {"kind": "bread", "cat": "bread", "phase": "solid", "qty_g": 30.0, "toast": 2.5},
                                      "temperature_c": 200.0}}], notices=True)
        w = env.world
        w.begin()
        S._doneness(w, b["bread"], 200.0, 150.0, 30.0)
        w.commit()
        self.assertIn("smoke", [n["kind"] for n in env.world.s["notices"]])

    def test_canonical_food_questions_reach_the_food_answerer(self):
        from house_sim.kernel import dialogue
        from house_sim.systems.food import perception as FP
        env, b, r = scene([{"op": "helper", "name": "food.eaters", "args": {"people": [
            {"id": "kid_a", "role": "child", "age": 6, "room": "kitchen", "allergies": ["peanut"]}]}}])
        st = env.world.s
        words = dialogue.Subject("word", None, ("food",))
        self.assertEqual(dialogue.find_route(st, "kid_a", words, "preference", None, None)[1], "food:tastes")
        self.assertEqual(FP.route_food(st, "kid_a", None, "preference", "diet", None), "food:restrictions")
        leftovers = dialogue.Subject("word", None, tuple(dialogue.words_of("leftovers")))
        self.assertEqual(dialogue.find_route(st, "kid_a", leftovers, "how", None, None)[1], "food:rule")
        answer = FP.food_answers(env, env.world, "kid_a", "food:restrictions", None)
        self.assertEqual(answer["value"]["allergies"], ["peanut"])
        # words as the dialogue normalizes them (singular); questions about storing other things are not food's
        for text, topic in (("how do we keep the scraps", "food:rule"), ("how should I store the towels", None),
                            ("how do I clean the fridge", None), ("how long do sweets last", "food:rule")):
            subject = dialogue.Subject("word", None, tuple(dialogue.words_of(text)))
            self.assertEqual(FP.route_food(st, "kid_a", subject, "how", None, None), topic, text)

    def test_a_portion_keeps_its_link_to_a_package_outside_the_food_model(self):
        # round-4 residual: food.refresh dropped derived_from whenever the source had no food kind (another domain's
        # package); it now drops it only when the source is food of a different kind (the portion became a batter)
        env, b, r = scene([{"op": "spawn", "var": "bag", "type": "mixing_bowl", "on": "{counter2}"},
                           {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter2}"},
                           {"op": "spawn", "var": "pile", "type": "food_item", "on": "{counter2}",
                            "props": {"food": {"kind": "bread", "cat": "bread", "phase": "solid", "qty_g": 40.0}}},
                           {"op": "spawn", "var": "batter", "type": "food_item", "on": "{counter2}",
                            "props": {"food": {"kind": "batter", "cat": "baking", "phase": "paste", "qty_g": 200.0}}}])
        w = env.world
        w.begin()
        w.mut(b["pile"])["derived_from"] = b["bag"]
        w.mut(b["batter"])["derived_from"] = b["flour"]
        w.commit()
        r.do("wait", seconds=60)
        self.assertEqual(ent(env, b["pile"]).get("derived_from"), b["bag"])
        self.assertNotIn("derived_from", ent(env, b["batter"]))

    def test_an_opened_date_written_on_the_carton_is_a_matching_label(self):
        from house_sim.systems.food import model as M
        env, b, r = scene([{"op": "helper", "name": "food.dated", "args": {
            "type": "milk_carton", "days": 3, "var": "milk", "on": "{counter}", "opened_days_ago": 2,
            "opened_label": True}}])
        rec = ent(env, b["milk"])
        self.assertEqual(rec["food_label"]["dates"], [rec["opened_on"]])
        check = M.label_check(env.world.s, b["milk"])
        self.assertTrue(check["contents_ok"] and check["ok"], check)

    def test_idle_probes_are_exact(self):
        from house_sim.kernel import plugins
        for name in ("food.thermal", "food.safety", "food.refresh"):
            self.assertIn(name, plugins.SYSTEM_IDLE)

        def run(probes):
            old = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = probes
            try:
                env, b, r = scene([
                    {"op": "spawn", "var": "box", "type": "storage_container_medium", "on": "{counter}", "props": {"open": True}},
                    {"op": "spawn", "var": "chili", "type": "chili", "in": "{box}",
                     "props": {"food": {"qty_g": 800.0}, "mass_kg": 0.8, "volume_l": 0.8, "temperature_c": 70.0,
                               "_core_c": 72.0, "size_m": [0.18, 0.12, 0.05]}},
                    {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter2}",
                     "props": {"temperature_c": 4.0, "_core_c": 4.0}}], kitchen_args={"need": ["fridge"]})
                for seconds in (1800, 600, 3 * 3600):
                    r.do("wait", seconds=seconds)
                return digest(env.world.s)
            finally:
                plugins.IDLE_PROBES[0] = old
        self.assertEqual(run(True), run(False))

    def test_generation_2_kitchens_are_stocked_for_the_household(self):
        from house_sim.systems.food import furnish as F
        config = {"people": {"a": {"role": "adult", "age": 35, "profile": {"diet": "vegetarian", "allergies": ["peanut"]}},
                             "b": {"role": "adult", "age": 36, "profile": {"diet": "vegan", "allergies": []}},
                             "k": {"role": "infant", "age": 0.7, "profile": {"diet": "vegetarian", "allergies": []}}},
                  "household": {"profile": {"members": {"k": {"role": "infant", "age": 0.7}}}}}
        stock = F.household_stock(config, {"gen": 2})
        self.assertEqual(stock["subs"]["frozen_chicken_pack"], "veggie_patties_box")
        self.assertEqual(stock["subs"]["peanut_butter_jar"], "jam_jar")
        self.assertEqual(stock["subs"]["milk_carton"], "oat_milk_carton")
        self.assertIsNone(stock["subs"]["egg_carton"])
        self.assertIn("baby_formula_can", stock["pantry"])
        self.assertEqual(stock["pantry"].count("baby_puree_jar"), 3)
        self.assertEqual(F.household_stock(config, {"gen": 1}), {"subs": {}, "pantry": [], "fridge": []})

    def test_generation_2_profiles_constrain_what_people_eat(self):
        env, b, r = scene([{"op": "spawn", "var": "pb", "type": "peanut_butter_jar", "on": "{counter}", "props": {"open": True}}])
        st = env.world.s
        st["people"]["kid"] = {"role": "child", "age": 7, "location": b["kitchen"],
                               "profile": {"diet": "omnivore", "allergies": ["peanut"]}}
        from house_sim.systems.food import model as M
        issues = M.eater_issues(st, "kid", [b["pb"]])
        self.assertIn("allergen:peanut:" + b["pb"], issues)
        self.assertEqual(M.profile(st, "kid")["diet"], set())         # omnivore restricts nothing
        # and they say so when asked (the answerer reads the same profile)
        from house_sim.systems.food import perception as FP
        self.assertEqual(FP.food_answers(env, env.world, "kid", "food:allergies", None)["value"], ["peanut"])
        self.assertEqual(FP.food_answers(env, env.world, "kid", "food:restrictions", None)["value"],
                         {"allergies": ["peanut"], "diet": []})

    def test_meals_are_logged_and_people_say_what_they_ate(self):
        from house_sim.systems.food import model as M
        from house_sim.systems.food import perception as FP
        env, b, r = scene([
            {"op": "helper", "name": "food.eaters", "args": {"people": [
                {"id": "kid_a", "role": "child", "age": 8, "room": "kitchen"}]}},
            {"op": "spawn", "var": "plate", "type": "kids_plate", "on": "{counter}"},
            {"op": "spawn", "var": "apple", "type": "apple", "on": "{plate}"}])
        r.skill("give", obj=b["plate"], person="kid_a")
        meals = M.meal_log(env.world.s, "kid_a")
        self.assertEqual(len(meals), 1)
        self.assertEqual((meals[0]["slot"], meals[0]["kinds"], meals[0]["day"]), ("breakfast", ["apple"], "2026-10-09"))
        self.assertNotIn(b["apple"], json.dumps(meals))               # no entity ids: the history outlives the things
        answer = FP.food_answers(env, env.world, "kid_a", "food:meals", None)["value"]
        self.assertEqual(answer, [{"slot": "breakfast", "kinds": ["apple"]}])
        self.assertIsNone(FP.food_answers(env, env.world, "kid_a", "food:meals:dinner", None)["value"])

    def test_an_opened_date_is_written_in_the_households_date_style(self):
        for region, words in (("uk", "opened 7 October 2026"), ("us", "opened October 7, 2026")):
            env, b, r = scene([{"op": "helper", "name": "food.dated", "args": {
                "type": "milk_carton", "days": 3, "var": "milk", "on": "{counter}", "opened_days_ago": 2,
                "opened_label": True}}], house={"residents": "minimal", "pets": "none", "region": region})
            label = ent(env, b["milk"])["food_label"]
            self.assertEqual(label["text"], "milk; " + words)
            self.assertEqual((label["contents"], label["dates"]), ("milk", ["2026-10-07"]))

    def test_a_settled_lunch_box_lets_food_thermal_idle(self):
        from house_sim.kernel import plugins
        from house_sim.systems.food import systems as S

        def run(probes):
            old, original, calls = plugins.IDLE_PROBES[0], S.thermal_idle, []
            plugins.IDLE_PROBES[0] = probes
            S.thermal_idle = lambda w: calls.append(original(w)) or calls[-1]
            try:
                # (no stand-in fridge or freezer: their cooling keeps food.thermal running when no appliance exists)
                env, b, r = scene([{"op": "spawn", "var": "box", "type": "lunch_box", "on": "{counter}",
                                    "props": {"internal_temp_c": 26.0}}], kitchen_args={"need": ["trash"]})
                for seconds in (1800, 6 * 3600):
                    r.do("wait", seconds=seconds)
                return digest(env.world.s), sum(1 for c in calls if c), ent(env, b["box"])["internal_temp_c"]
            finally:
                S.thermal_idle = original
                plugins.IDLE_PROBES[0] = old
        on, idle, temp = run(True)
        off, _, temp_off = run(False)
        self.assertEqual(on, off)                  # exact: the same states with and without the probes
        self.assertEqual(temp, temp_off)
        self.assertGreater(idle, 50)
        # an empty box at the temperature around it is settled; one a degree off keeps food.thermal running
        env, b, r = scene([{"op": "spawn", "var": "box", "type": "lunch_box", "on": "{counter}"}])
        st = env.world.s
        rec = st["entities"][b["box"]]
        rec["internal_temp_c"] = round(S._air_outside(st, b["box"], rec) + 0.05, 3)
        self.assertTrue(S._air_settled(st, b["box"], rec))
        rec["internal_temp_c"] = round(rec["internal_temp_c"] + 1.0, 3)
        self.assertFalse(S._air_settled(st, b["box"], rec))


class PhaseOneStabilise(unittest.TestCase):
    """Independent review 2026-10-22 (docs/reviews/2026-10-22_independent_review), phase 1: food's share of the
    findings, each with its regression."""

    def test_a_warm_appliance_is_never_a_fridge(self):
        # M3: food's cold-space index was keyed on internal_temp_c, which a toaster gains when it heats; the toaster
        # (and every oven and dishwasher) then got a fridge's free volume and a cold unit's heat load
        from house_sim.systems.food import scenarios as SC, systems as S
        toaster = {"ac_kind": "toaster", "container": True, "capacity_l": 2.0, "internal_temp_c": 180.0}
        oven = {"ac_kind": "oven", "container": True, "capacity_l": 75.0, "internal_temp_c": 21.0}
        fridge = {"ac_kind": "fridge", "container": True, "capacity_l": 400.0, "internal_temp_c": 3.5}
        standin = dict(SC.STANDIN_FRIDGE)
        lunch = {"food_air_model": True, "insulated": True, "capacity_l": 4.0, "internal_temp_c": 21.0}
        for rec in (toaster, oven):
            self.assertFalse(S.cold_unit(rec) or S.cold_storage(rec) or S._checked(rec), rec)
        for rec in (fridge, standin):
            self.assertTrue(S.cold_unit(rec) and S.cold_storage(rec) and S._checked(rec), rec)
        self.assertTrue(S.cold_unit(lunch))
        self.assertFalse(S.cold_storage(lunch))                        # an air model, not a refrigerator
        # end to end: bread in a hot unit that is not a fridge toasts, and the unit gets no fridge bookkeeping
        env, b, r = scene([{"op": "spawn", "var": "unit", "type": "storage_container_large", "on": "{counter}",
                            "props": {"open": True, "lid": False, "internal_temp_c": 200.0, "heat_coupling": 1.0}},
                           {"op": "spawn", "var": "slice", "type": "bread_slice", "in": "{unit}"}],
                          kitchen_args={"need": ["fridge"]})
        for _ in range(3):
            r.do("wait", seconds=60)
        unit, fridge_rec = ent(env, b["unit"]), ent(env, b["fridge"])
        self.assertGreater(food(env, b["slice"]).get("toast", 0.0), 0.0)   # it really heats in there
        self.assertNotIn("_free_l", unit)
        self.assertNotIn("heat_load_w", unit)
        self.assertIn("_free_l", fridge_rec)                           # the refrigerator keeps its free volume

    def test_a_toaster_heating_up_gets_no_fridge_fields_and_save_load_agrees(self):
        # M3 repro (T-50c78da60ad2a8-01, k = 12): the uninterrupted run and a saved and reloaded one must agree, and
        # neither may give the toaster a fridge's free volume or a heat load
        import tempfile
        from pathlib import Path
        from house_sim.kernel.registry import default_registry
        from house_sim.tasks.runner import make_env, run_plan
        path = Path(__file__).resolve().parent.parent / "tasks" / "authored" / "appliances_cooking__examples.json"
        specs = [s for s in json.loads(path.read_text(encoding="utf-8"))["specs"]
                 if s["catalog_id"] == "T-50c78da60ad2a8-01"] if path.exists() else []
        if not specs or not default_registry().known("toaster"):
            self.skipTest("the toaster spec needs appliances_cooking (HOUSE_SIM_DOMAINS without it)")
        config, task, oracle = build(specs[0], 41)
        ref = make_env(config, task, 41, fast=False)
        run_plan(ref, oracle["reference"], oracle["bindings"], strict=False)
        history = list(ref._history)

        def do(env, rec):
            return env.phase_done() if rec.get("phase_done") else env.step(**dict(rec["command"]))
        whole = make_env(config, task, 41, fast=False)
        for rec in history:
            do(whole, rec)
        part = make_env(config, task, 41, fast=False)
        for rec in history[:12]:
            do(part, rec)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            part.save(path)
            loaded = HouseEnv.load(path)
        for rec in history[12:]:
            do(loaded, rec)
        self.assertEqual(digest(loaded.world.s), digest(whole.world.s))
        toaster = ent(whole, oracle["bindings"]["toaster"])
        self.assertNotIn("_free_l", toaster)
        self.assertNotIn("heat_load_w", toaster)

    def test_nothing_pours_out_of_an_egg_in_its_shell(self):
        # found by the phase-1 twin probe: transfer amount_ml=50 poured 41 ml out of an intact egg (raw or boiled)
        for perception in ("realistic", None):
            env, b, r = scene([{"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"},
                               {"op": "spawn", "var": "boiled", "type": "hard_boiled_egg", "on": "{counter}"},
                               {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
                               {"op": "spawn", "var": "cup", "type": "measuring_spoon_tbsp", "on": "{counter}"}],
                              perception=perception)
            key = "agent_message" if perception else "message"
            refusals = set()
            for egg in ("egg", "boiled"):
                for args in ({"amount_ml": 50}, {"fraction": 0.5}, {"amount_g": 20}):
                    out = r.do("transfer", target=b[egg], destination=b["bowl"], expect="fail", **args)
                    refusals.add((out["category"], out[key].replace(env.realism.handles.handle(b[egg]), "EGG")
                                  if perception else out[key]))
                self.assertEqual(food(env, b[egg])["qty_g"], 57.0)
            self.assertEqual(len(refusals), 1, refusals)                # one refusal, whichever egg it is
            self.assertIn("shell", next(iter(refusals))[1])
            r.skill("fetch", obj=b["cup"])
            self.assertIn("shell", r.do("scoop", target=b["egg"], tool=b["cup"], destination=b["bowl"],
                                        expect="fail")[key])
            r.skill("put", obj=b["cup"], dest=b["counter"])
            moved = msg_json(r.do("transfer", target=b["boiled"], destination=b["bowl"])["message"])   # whole: fine
            self.assertEqual(food(env, moved["new"])["parts"], ["shell"])

    def test_a_level_cup_of_any_white_powder_looks_the_same(self):
        # found by the phase-1 twin probe: a level half cup scooped from an unidentified bag read 93 ml of flour and
        # 96 ml of sugar (grams over the bag's density, not the cup's volume), and the piles differed in size
        seen = {}
        for t in ("all_purpose_flour", "sugar_bag", "powdered_sugar_bag"):
            # (the same id for every bag: estimates are fixed draws per entity, so twins share theirs)
            env, b, r = scene([{"op": "spawn", "var": "bag", "type": t, "on": "{counter2}", "eid": "powder_bag_1",
                                "props": {"open": True}},
                               {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
                               {"op": "spawn", "var": "cup", "type": "measuring_cup_half", "on": "{counter}"}],
                              perception="realistic")
            r.skill("fetch", obj=b["cup"])
            r.skill("approach", target=b["bag"])
            out = r.do("scoop", target=b["bag"], tool=b["cup"], destination=b["bowl"])
            data = msg_json(out["agent_message"])
            pile = env.observe()["visible_entities"][data["portion"]]        # the agent message gives its handle
            true_pile = ent(env, msg_json(out["message"])["portion"])
            # (the pile's own size estimate is a fixed draw per pile, whose id is the scooped kind's: compare its truth)
            seen[t] = (data["ml"], data["per_scoop_ml"], pile["label"], true_pile["size_m"])
            self.assertAlmostEqual(true_pile["food"]["qty_g"] / true_pile["food"]["density"], 118.3, delta=1.0)
        self.assertEqual(len(set(json.dumps(v) for v in seen.values())), 1, seen)

    def test_a_scoop_of_powder_stirred_into_soup_leaves_it_soup(self):
        # found by the phase-1 twin probe: a soup (no water in its composition) with a scoop of baking soda or flour
        # was reclassified as a "dry mix" powder; a finished dish keeps its kind, a mixing base still becomes a batter
        env, b, r = scene([{"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
                           {"op": "spawn", "var": "soup", "type": "soup", "in": "{bowl}",
                            "props": {"food": {"qty_g": 300.0}, "mass_kg": 0.3, "volume_l": 0.3}},
                           {"op": "spawn", "var": "jug", "type": "liquid_measuring_cup", "on": "{counter}"},
                           {"op": "spawn", "var": "milk", "type": "food_item", "in": "{jug}",
                            "props": {"food": {"kind": "milk", "cat": "dairy", "phase": "liquid", "qty_g": 240.0, "density": 1.03},
                                      "mass_kg": 0.24, "volume_l": 0.23}},
                           {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter2}", "props": {"open": True}},
                           {"op": "spawn", "var": "cup", "type": "measuring_cup_half", "on": "{counter}"}])
        r.skill("fetch", obj=b["cup"])
        r.skill("approach", target=b["flour"])
        r.do("scoop", target=b["flour"], tool=b["cup"], destination=b["bowl"])
        soup = food(env, b["soup"])
        self.assertEqual((soup["kind"], soup["phase"]), ("soup", "liquid"))
        self.assertIn("all_purpose_flour", soup["composition"])          # it is in there (allergens follow it)
        self.assertIn("wheat", ent(env, b["soup"])["allergens"])
        for _ in range(2):
            r.do("scoop", target=b["flour"], tool=b["cup"], destination=b["jug"])
        self.assertEqual(food(env, b["milk"])["kind"], "batter")        # flour into milk: a batter, as before

    def test_the_person_eats_extension_point_applies_the_eater_checks(self):
        # P4: food listens to person_eats, which no domain runs yet; the listener is a documented, tested extension
        # point that gives a person eating on their own the eater checks of a hand-over. M9: it is their own choice,
        # so no harm flag is charged to the robot (a hand-over by the robot is)
        from house_sim.kernel.plugins import run_hooks
        env, b, r = scene(Serving()._kids() + [
            {"op": "spawn", "var": "bowl", "type": "serving_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "nuts", "type": "food_item", "in": "{bowl}",
             "props": {"food": {"kind": "peanuts", "cat": "nut", "phase": "pieces", "qty_g": 80.0},
                       "allergens": ["peanut"], "mass_kg": 0.08}}])
        issues = [i for out in run_hooks("person_eats", env.world, "guest_1", b["bowl"]) for i in (out or [])]
        self.assertTrue(any(i.startswith("allergen") for i in issues), issues)
        self.assertEqual(env.world.s["stats"].get("safety_violations", 0), 0)
        self.assertEqual(env.world.s["food_log"]["served"][-1]["by"], "volition")
        self.assertEqual(env.world.s["food_log"]["meals"][-1]["person"], "guest_1")
        r.skill("give", obj=b["bowl"], person="guest_1")                 # the robot handing it over is charged
        self.assertEqual(env.world.s["stats"]["safety_violations"], 1)

    def test_a_utensil_melting_in_someone_elses_hot_pan_is_not_the_robots_damage(self):
        # M9: food.safety charged stats.damage for every melted utensil, even one a resident left in a hot pan while
        # the robot only waited; the kernel's attribution (impact.damage) decides now
        def hot_pan(spatula_in_pan):
            ops = [{"op": "spawn", "var": "pan", "type": "frying_pan", "on": "{counter}",
                    "props": {"temperature_c": 200.0, "fixed_temperature": True}}]
            ops.append({"op": "spawn", "var": "spatula", "type": "plastic_spatula", "props": {"size_m": [0.22, 0.05, 0.01]},
                        **({"in": "{pan}"} if spatula_in_pan else {"on": "{counter2}"})})     # a short one fits the pan
            return scene(ops)
        env, b, r = hot_pan(True)
        r.do("wait", seconds=60)
        rec = ent(env, b["spatula"])
        self.assertTrue(rec["broken"])
        self.assertEqual(rec["damage"].get("melted"), 1.0)
        self.assertEqual(env.world.s["stats"].get("damage", 0), 0)       # exogenous: not the robot's
        env, b, r = hot_pan(False)
        r.skill("put", obj=b["spatula"], dest=b["pan"])                  # the robot leaves it in the hot pan
        r.do("wait", seconds=60)
        self.assertTrue(ent(env, b["spatula"])["broken"])
        self.assertEqual(env.world.s["stats"]["damage"], 1)

    def test_bleach_or_dirty_water_reaching_food_makes_it_unsafe(self):
        # G10 / L3: bleach mop water over a loaf left it only wet, and food judged neither chemical nor germ marks
        from house_sim.kernel.phys import liquids
        env, b, r = scene(Serving()._kids() + [
            {"op": "spawn", "var": "plate", "type": "dinner_plate", "on": "{counter}"},
            {"op": "spawn", "var": "slice", "type": "bread_slice", "on": "{plate}"},
            {"op": "spawn", "var": "apple", "type": "apple", "on": "{counter}"},
            {"op": "spawn", "var": "soda", "type": "canned_soda", "on": "{counter}"},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter2}"},
            {"op": "spawn", "var": "salad", "type": "green_salad", "in": "{bowl}"}])
        w = env.world
        liquids.spill(w, 0.05, "bleach", onto=b["slice"])               # a splash of bleach runs down onto the bread
        liquids.spill(w, 0.05, "dirty_water", onto=b["apple"])          # dirty water over the apple
        liquids.spill(w, 0.05, "bleach", onto=b["soda"])                # the sealed can keeps it out
        bowl = w.mut(b["bowl"])                                          # the salad bowl filled with mop water
        bowl.update(liquid="mop_water", liquid_l=0.5, _mix={"water": 0.45, "bleach": 0.05})
        r.do("wait", seconds=1)
        for key in ("slice", "salad"):
            self.assertGreaterEqual(ent(env, b[key])["contamination"]["chemical"], 0.05, key)
            self.assertEqual(ent(env, b[key])["_food_status"]["class"], "unsafe", key)
            self.assertIn("chemical_contamination", ent(env, b[key])["_food_status"]["reasons"])
        # the plate under the bread: a chemical on a food-contact surface is what sanitizing is, germs are not
        self.assertNotIn("chemical", ent(env, b["plate"]).get("contamination") or {})
        liquids.spill(w, 0.05, "dirty_water", onto=b["plate"])
        self.assertEqual(ent(env, b["plate"])["contamination"]["bacteria"], 0.5)
        self.assertEqual(ent(env, b["salad"])["contamination"]["bacteria"], 0.5)           # mop water carries germs
        self.assertNotIn("chemical", ent(env, b["soda"]).get("contamination") or {})
        self.assertNotEqual(ent(env, b["soda"])["_food_status"]["class"], "unsafe")
        apple = ent(env, b["apple"])
        self.assertEqual(apple["contamination"]["bacteria"], 0.5)
        self.assertNotEqual(apple["_food_status"]["class"], "unsafe")    # germs wash off produce: an eater check only
        self.assertFalse(ent(env, b["plate"])["_dish"]["ok_for"]["guest_1"])
        r.skill("give", obj=b["plate"], person="guest_1")
        self.assertEqual(w.s["stats"]["food_poisoning_risk"], 1)
        self.assertTrue(any(i.startswith("chemical:") for i in w.s["food_log"]["served"][-1]["issues"]))
        r.skill("give", obj=b["apple"], person="teen_1")
        self.assertIn("pathogen:" + b["apple"], w.s["food_log"]["served"][-1]["issues"])

    def test_mop_water_poured_over_bread_leaves_it_unsafe(self):
        # G10 repro: bleach mop water poured over a loaf left it wet with no contamination (plumbing's pour)
        from house_sim.kernel.plugins import SCENARIO_HELPERS, load_domains
        load_domains()
        if "plumbing_liquids.dirty_bucket" not in SCENARIO_HELPERS:
            self.skipTest("the mop bucket is plumbing_liquids' (HOUSE_SIM_DOMAINS without it)")
        env, b, r = scene([{"op": "spawn", "var": "slice", "type": "bread_slice", "on": "{counter}"},
                           {"op": "helper", "name": "plumbing_liquids.dirty_bucket",
                            "args": {"room": "{kitchen}", "liters": 1.0, "chemical": True}}])
        r.skill("plumbing_liquids.dump", container=b["bucket"], dest=b["slice"])
        r.do("wait", seconds=1)
        rec = ent(env, b["slice"])
        self.assertGreaterEqual(rec["contamination"].get("chemical", 0.0), 0.05)
        self.assertGreaterEqual(rec["contamination"].get("bacteria", 0.0), 0.05)
        self.assertEqual(rec["_food_status"]["class"], "unsafe")

    def test_only_liquid_tight_kitchen_containers_hold_poured_liquid(self):
        # L3 / G10: a bread box, a colander and a sieve held poured liquid; holes and a roll-top front now let it out
        from house_sim.kernel.phys import liquids
        from house_sim.kernel.registry import default_registry
        reg = default_registry()
        for name in ("colander", "fine_mesh_sieve"):
            self.assertTrue(reg.resolve(name)["perforated"], name)
        for name, tight in (("bread_box", False), ("grocery_bag", False), ("zip_top_bag", True), ("freezer_bag", True),
                            ("utensil_crock", True)):
            self.assertIs(reg.resolve(name)["watertight"], tight, name)
        env, b, r = scene([{"op": "spawn", "var": "colander", "type": "colander", "on": "{counter}"},
                           {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter2}"}])
        w = env.world
        self.assertFalse(liquids.holds_liquid(w.s, b["colander"]))       # the kernel's liquid model: it runs through
        self.assertTrue(liquids.holds_liquid(w.s, b["bowl"]))
        liquids.spill(w, 0.3, "water", onto=b["colander"])
        self.assertFalse(ent(env, b["colander"]).get("liquid_l"))

    def _food_groups(self):
        from pathlib import Path
        from house_sim.kernel.registry import default_registry
        path = Path(__file__).resolve().parent.parent / "house_sim" / "data" / "types" / "food.json"
        names = sorted(json.loads(path.read_text(encoding="utf-8"))["looks_like"])
        reg = default_registry()
        return reg, {g: reg.lookalike_types()[g] for g in names}

    def test_look_alike_members_share_one_package(self):
        # L2 / L4 / I9 / L5: members of a food look-alike group differed in true size (pixels, placement), mass (touch
        # bucket, load word), material (a clink), fragility (a fragile ring) and piece format; now each group is one
        # package format, so only reading (or opening and looking) tells the contents apart
        from house_sim.kernel import proprio
        from house_sim.kernel.realism import MASS4
        reg, groups = self._food_groups()

        def bucket(value, scheme):
            return next((name for edge, name in zip(scheme[0], scheme[1]) if value < edge), scheme[1][-1])
        for group, members in sorted(groups.items()):
            self.assertGreaterEqual(len(members), 2, group)
            recs = {t: reg.resolve(t) for t in members}
            for key in ("size_m", "tare_kg", "package", "material", "fragile", "openable", "open", "transparent",
                        "color", "shape"):
                values = {json.dumps(rec.get(key)) for rec in recs.values()}
                self.assertEqual(len(values), 1, (group, key, {t: rec.get(key) for t, rec in recs.items()}))
            for key in ("count", "unit_g", "unit_size_m"):
                values = {json.dumps((rec.get("food") or {}).get(key)) for rec in recs.values()}
                self.assertEqual(len(values), 1, (group, "food." + key))
            views = {json.dumps(reg.view_props(t), sort_keys=True) for t in members}
            self.assertEqual(len(views), 1, (group, "view"))
            masses = sorted(float(rec["mass_kg"]) for rec in recs.values())
            self.assertEqual(len({bucket(m, MASS4) for m in masses}), 1, (group, masses))         # touch
            self.assertEqual(len({proprio.bucket_load(m / 8.0) for m in masses}), 1, (group, masses))   # load word
            self.assertLessEqual(masses[-1] - masses[0], max(0.05, 0.06 * masses[-1]), (group, masses))
        self.assertEqual(reg.lookalike_warnings, [])
        # no food group shows one size for members of different true size (the registry's LookalikeSizeWarning)
        self.assertEqual([s for s in reg.lookalike_spreads if s.split(":")[0] in groups], [])

    def test_look_alike_members_cannot_be_told_apart_by_handling(self):
        # the same scene with each member in the same spot: what the robot sees, holds, hears and where it lies are the
        # same (handles mapped back to true ids, analysis side only) until the member is identified. With the geometric
        # camera the whole observation is compared: a member of another true size turns the robot to another facing
        # when it approaches, frames it in other pixels and at another bearing (phase-1 red team: the measuring cups
        # and spoons, 1 cup vs 1/4 cup, were one group and told apart this way)
        reg, groups = self._food_groups()

        def canon(env, value):
            text = json.dumps(value, sort_keys=True)
            for handle, eid in sorted(env.realism.handles.by_handle.items()):
                text = text.replace(handle, eid)
            return json.dumps(json.loads(text), sort_keys=True)     # keyed by id, not by the episode's handles
        for camera in (None, "geometric"):
            for group, members in sorted(groups.items()):
                seen = {}
                for t in members:
                    env, b, r = scene([{"op": "spawn", "var": "probe", "type": t, "on": "{counter2}", "eid": "probe_item"}],
                                      perception="realistic", camera=camera)
                    h = env.realism.handles.handle(b["probe"])
                    trail = [env.observe()["visible_entities"].get(h)]
                    for action, args in (("approach", (h,)), ("pick_up", (h,)),
                                         ("place", (h, env.realism.handles.handle(b["counter2"])))):
                        out = env.step(action, *args)
                        obs = env.observe()
                        if camera:
                            trail.append([out["ok"], out["message"],
                                          {k: v for k, v in obs.items() if k not in ("time", "time_s", "steps")}])
                        else:
                            trail.append([out["ok"], out["message"], obs["visible_entities"].get(h), obs["robot"].get("load"),
                                          obs.get("sounds"), obs.get("smells"), (obs.get("grid") or {}).get("hazards")])
                    seen[t] = canon(env, trail)
                self.assertEqual(len(set(seen.values())), 1, (camera, group, sorted(seen)))


class PhaseOneFinish(unittest.TestCase):
    """Phase 1 finish: food's adoption breakages and red-team findings (catalog/analysis/phase1/breakages.json,
    red_leaks.json), each with its regression."""

    HOT = {"temperature_c": 80.0, "max_touch_temp_c": 250.0}      # a hot dish with stay-cool handles (no mitts needed)

    def _contact_scene(self):
        return scene([{"op": "spawn", "var": "plastic", "type": "cutting_board_plastic", "on": "{counter2}"},
                      {"op": "spawn", "var": "chili", "type": "chili", "on": "{counter}", "props": dict(self.HOT)},
                      {"op": "spawn", "var": "pan", "type": "frying_pan", "on": "{counter}",
                       "props": {"temperature_c": 200.0, "fixed_temperature": True, "max_touch_temp_c": 250.0}}])

    def test_the_legacy_contact_heat_copy_uses_the_kernels_limits_and_every_domains_claim(self):
        # breakage (T-7d4006bda247ba-01, appliances_cooking's request): food's legacy copy of contact_heat marked any
        # item above 70 C set on wood, laminate or plastic with heat_mark 0.4 and a damage count on every placement,
        # whatever the support's heat limit and whoever claimed the contact. The copy itself, on records laid out
        # analysis-side (other domains' place hooks, which keep copies of their own, do not run)
        from unittest import mock
        from house_sim.kernel import plugins
        from house_sim.kernel.phys import lawbook, thermal
        from house_sim.systems.food import physical as PH
        env, b, r = self._contact_scene()
        w = env.world
        claimed = set()

        def claim(w_, hot, support, temp_c, severity):          # another domain's claim (a heel rest, its own burner)
            return (hot, support) in claimed

        def rest(item, support):
            w.begin()
            m = w.mut(item)
            m["location"], m["relation"] = support, "on"
            m.pop("spos", None)
            out = PH.hot_contact(w, item, support)
            w.commit()
            return out
        entry = (100, "tests.food_claim", claim)
        plugins.HOOKS["phys.contact_heat"].append(entry)
        try:
            with lawbook.forced(contact_heat=False), mock.patch.object(PH, "other_copy_loaded", lambda law: False):
                limit = thermal.heat_limit_c(w.s, b["board"])                 # finished wood, about 60 C
                before = w.s["stats"].get("damage", 0)
                self.assertEqual(rest(b["chili"], b["board"]), "ring_stain")   # a heat ring, not heat_mark 0.4
                self.assertEqual(ent(env, b["board"])["damage"], {"ring_stain": round(min(1.0, (80.0 - limit) / 100.0), 4)})
                self.assertEqual(w.s["stats"]["damage"], before + 1)
                self.assertIsNone(rest(b["chili"], b["board"]))                # again: nothing new to mark or count
                self.assertEqual(w.s["stats"]["damage"], before + 1)
                self.assertIsNone(rest(b["chili"], b["plastic"]))              # 80 C is under plastic's 100 C limit
                claimed.add((b["pan"], b["plastic"]))
                self.assertIsNone(rest(b["pan"], b["plastic"]))                # 200 C, but another domain owns it
                self.assertFalse(ent(env, b["plastic"]).get("damage"))
                claimed.clear()
                with lawbook.forced(contact_heat=True):                         # the law active: the kernel's alone
                    self.assertIsNone(rest(b["pan"], b["plastic"]))
                self.assertEqual(rest(b["pan"], b["plastic"]), "melted")        # nobody claims it: plastic melts
        finally:
            plugins.HOOKS["phys.contact_heat"].remove(entry)
        # one copy per contact: food's steps aside while a domain laws.json lists as keeping its own copy is loaded
        others = [d for d in lawbook.LAWS["contact_heat"]["duplicated_by"] if d != "food"]
        loaded = any(str(owner).startswith(tuple("house_sim.systems.{}.".format(d) for d in others))
                     for owner in plugins.OWNERS.values())
        self.assertEqual(PH.other_copy_loaded("contact_heat"), loaded)

    def test_a_hot_dish_set_on_a_wooden_board_is_marked_once(self):
        # end to end through place: with only core and food loaded (HOUSE_SIM_DOMAINS=core,food) food's copy marks the
        # board once and counts it once however often the dish is set down; with every domain loaded it leaves the
        # contact to the domains that keep their own copies (no extra mark, no extra count from food)
        from unittest import mock
        from house_sim.kernel.phys import lawbook
        from house_sim.systems.food import physical as PH
        real, marks = PH.hot_contact, []

        def spy(w, hot, support, by="robot"):
            out = real(w, hot, support, by)
            marks.append(out)
            return out
        env, b, r = self._contact_scene()
        with lawbook.forced(contact_heat=False), mock.patch.object(PH, "hot_contact", spy):
            r.skill("put", obj=b["chili"], dest=b["board"])
            r.skill("put", obj=b["chili"], dest=b["board"])
        self.assertEqual(len(marks), 2)
        if PH.other_copy_loaded("contact_heat"):
            self.assertEqual(marks, [None, None])
        else:
            self.assertEqual(marks, ["ring_stain", None])
            self.assertEqual(sorted(ent(env, b["board"])["damage"]), ["ring_stain"])
            self.assertEqual(env.world.s["stats"]["damage"], 1)

    NOVEL_LOOK = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic"}
    # food and core things an unfamiliar object may really be: a knife, a closed package, an empty bowl, a kitchen
    # scale, a pot of soup and a cucumber
    NOVEL_KINDS = (("chef_knife", {}), ("all_purpose_flour", {"open": False}), ("mixing_bowl", {}),
                   ("kitchen_scale", {"on": True}), ("stock_pot", {}), ("cucumber", {}))

    def _novel_scene(self, kind, props):
        ops = [{"op": "spawn", "var": "thing", "type": kind, "on": "{counter}", "eid": "thing_1",
                "props": dict(props, **self.NOVEL_LOOK)},
               {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}", "eid": "partner_1"},
               {"op": "spawn", "var": "cuc", "type": "cucumber", "on": "{board}", "eid": "cucumber_1"}]
        if kind == "stock_pot":
            ops.append({"op": "spawn", "var": "soup", "type": "chili", "in": "{thing}", "eid": "soup_1"})
        return scene(ops, perception="realistic")

    def _food_verbs(self):
        from house_sim.kernel import plugins
        return sorted((n, s) for n, s in plugins.ACTIONS.items() if s.fn.__module__.startswith(FOOD + ".actions_"))

    def test_every_food_verb_refuses_an_unfamiliar_thing_alike(self):
        # review I4 / phase-1 red team novel sweep: garnish, plate, portion, scoop and transfer said "Open it first" for
        # an unfamiliar (openable, closed) kettle and "It is empty" for every other unfamiliar thing; now every food
        # verb gives the kernel's one identify-first refusal for an unfamiliar thing in any entity parameter (the
        # verbs operate their target: operates=True), and weigh weighs one as an object (never reads it as a scale)
        from house_sim.kernel.actions import NOVEL_OPERATE_MESSAGE
        verbs = self._food_verbs()
        self.assertTrue(verbs)
        self.assertEqual([n for n, s in verbs if not s.operates], ["weigh"])
        answers = {}
        for kind, props in self.NOVEL_KINDS:
            env, b, r = self._novel_scene(kind, props)
            r.skill("approach", target=b["thing"])
            rows = []
            for name, spec in verbs:
                for pname, p in sorted(spec.params.items()):
                    if p.get("type") not in ("entity", "list"):
                        continue
                    args = {}
                    for key, q in spec.params.items():
                        if q.get("type") == "entity" and (not q.get("optional") or key == pname):
                            args[key] = b["thing"] if key == pname else b["bowl"]
                        elif q.get("type") == "list" and (not q.get("optional") or key == pname):
                            args[key] = [b["thing"]] if key == pname else [b["bowl"]]
                        elif not q.get("optional"):
                            args[key] = q["choices"][0] if "choices" in q else "shell"
                    out = r.do(name, expect="fail", **args)
                    message = out["agent_message"].replace(env.realism.handles.handle(b["thing"]), "THING")
                    rows.append((name, pname, out["category"], message))
                    if name != "weigh":
                        self.assertEqual(message, NOVEL_OPERATE_MESSAGE, (kind, name, pname))
            answers[kind] = rows
        self.assertEqual(len({json.dumps(rows) for rows in answers.values()}), 1,
                         {k: [row for row in v if row not in answers["chef_knife"]] for k, v in answers.items()})

    def test_an_unfamiliar_food_or_container_shows_no_food_view(self):
        # found by the phase-1 finish probe: inspecting an unfamiliar cucumber gave a food view (appearance, approx_ml,
        # odor) and an unfamiliar pot of chili its food contents, an unfamiliar knife neither; an unfamiliar thing now
        # shows the kernel's fixed key set only (what lies in it can still be inspected on its own)
        for kind, props in self.NOVEL_KINDS:
            env, b, r = self._novel_scene(kind, props)
            r.skill("approach", target=b["thing"])
            for where in (None, "closely", "inside"):
                out = r.do("inspect", target=b["thing"], **({"where": where} if where else {}))
                details = msg_json(out["agent_message"])
                self.assertFalse({"food_view", "food_contents", "whole_items", "probe_temp_c"} & set(details),
                                 (kind, where, sorted(details)))
            if kind == "stock_pot":
                soup = msg_json(r.do("inspect", target=b["soup"])["agent_message"])
                self.assertIn("food_view", soup)                  # the chili itself is food the robot can look at

    def test_an_unfamiliar_thing_in_hand_is_no_tool(self):
        # an unfamiliar chef's knife in hand cut the cucumber, an unfamiliar kettle did not: what the robot cannot
        # identify is no tool, utensil, opener or probe it knows of
        answers = {}
        for kind, props in self.NOVEL_KINDS:
            if kind in ("stock_pot", "kitchen_scale"):
                continue                                      # too big for one hand beside the steadying one
            env, b, r = self._novel_scene(kind, props)
            r.skill("fetch", obj=b["thing"])
            r.skill("approach", target=b["cuc"])
            rows = []
            for name in ("slice", "peel", "cut", "dice"):
                out = r.do(name, target=b["cuc"], expect="fail")
                rows.append((name, out["category"], out["agent_message"]))
            answers[kind] = rows
        self.assertEqual(len({json.dumps(rows) for rows in answers.values()}), 1, answers)


if __name__ == "__main__":
    unittest.main()
