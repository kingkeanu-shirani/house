"""Tests for the outdoor_plants domain: each mechanic has a success case and a failure/consequence case.

All domains:   python -m unittest tests/test_outdoor_plants.py            (the integration target)
Strict mode:   HOUSE_SIM_STRICT=1 python -m unittest tests/test_outdoor_plants.py
Isolated:      HOUSE_SIM_DOMAINS=outdoor_plants python -m unittest tests/test_outdoor_plants.py
The tests build worlds through the task builder (setup ops + scenario helpers) and drive them with
env.step / reference skills, so they hold with all 16 domains loaded (other furnishers' objects, people/pet
agents, other systems running).
"""
import json
import os
import unittest

from house_sim.kernel.plugins import load_domains
from house_sim.tasks.runner import Runner, build, make_env

load_domains()

from house_sim.systems.outdoor_plants import common as C  # noqa: E402
from house_sim.systems.outdoor_plants import plants as P  # noqa: E402

CAN = {"label": "watering can", "liquid_container": True, "liquid": "water", "liquid_l": 8.0, "capacity_l": 9.0, "mass_kg": 0.6, "size_m": [0.2, 0.45, 0.3], "volume_l": 9.0}


def make(setup, seed=0, house=None, task=None):
    spec = {"catalog_id": "test-outdoor", "house": house or {"dwelling": "house", "require": ["patio", "garden", "shed"]}, "setup": setup, "success": []}
    spec.update(task or {})
    config, built, oracle = build(spec, seed)
    env = make_env(config, built, seed, fast=False)
    return env, oracle["bindings"]


def run(env, bindings, steps, strict=False):
    runner = Runner(env, bindings, strict=strict)
    runner.run(steps)
    return runner


def wait(env, seconds):
    remaining = int(seconds)
    while remaining > 0:
        chunk = min(remaining, 86400)
        res = env.step("wait", seconds=chunk)
        assert res["ok"], res["message"]
        remaining -= chunk


def stat(env, name):
    return env.world.s["stats"].get(name, 0)


def ent(env, eid):
    return env.world.s["entities"][eid]


def status(env, eid):
    return ent(env, eid).get("_op_status") or {}


def room_of_type(env, kind):
    return sorted(r for r, v in env.world.s["rooms"].items() if v.get("type") == kind)[0]


def settle(env):
    """One short tick so the domain's one-time derivation (_op_status / _eval) has run."""
    res = env.step("wait", seconds=1)
    assert res["ok"], res["message"]


class WateringTests(unittest.TestCase):
    def setUp(self):
        self.env, self.b = make([
            {"op": "helper", "name": "outdoor_plants.houseplants", "args": {"room": "living_room", "plants": [
                {"species": "boston_fern", "rwc": "dry", "on": "table"}, {"species": "jade_plant", "rwc": "moist", "on": "table"},
                {"species": "artificial", "on": "table"}, {"species": "pothos", "rwc": "dry", "on": "floor", "drainage": False}]}},
            {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": CAN}])

    def test_dry_plant_watered_to_drainage_ends_in_band(self):
        env, b = self.env, self.b
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "check_moisture", "target": "{p1}"},
                     {"do": "water_plant", "target": "{p1}", "until": "drainage"}], strict=True)
        st = status(env, b["p1"])
        self.assertEqual(st["moisture"], "ok")
        self.assertFalse(st.get("overwatered", False))
        saucer = ent(env, b["saucer1"])
        self.assertGreater(saucer["liquid_l"], 0.0)
        self.assertLessEqual(saucer["liquid_l"], saucer["capacity_l"])
        self.assertEqual(stat(env, "spills"), 0)

    def test_moist_succulent_and_artificial_watering_are_flagged(self):
        env, b = self.env, self.b
        run(env, b, [{"skill": "approach", "target": "{p2}"}, {"do": "water_plant", "target": "{p2}", "until": "drainage"},
                     {"do": "water_plant", "target": "{p3}", "amount_l": 0.4}], strict=True)
        self.assertTrue(status(env, b["p2"]).get("overwatered"))
        self.assertEqual(status(env, b["p2"])["moisture"], "wet")
        self.assertTrue(status(env, b["p3"]).get("watered_artificial"))
        self.assertGreaterEqual(stat(env, "overwatered"), 1)

    def test_pot_without_drainage_waterlogs_and_overflowing_saucer_damages_wood(self):
        env, b = self.env, self.b
        run(env, b, [{"skill": "approach", "target": "{p4}"}, {"do": "water_plant", "target": "{p4}", "until": "drainage"}], strict=True)
        pot = ent(env, b["pot4"])
        self.assertGreater(pot["medium"].get("standing_l", 0.0) + pot["medium"]["moisture"], 0.6)
        wait(env, 3600 * 24 * 3)
        self.assertGreater(ent(env, b["p4"]).get("root_rot", 0.0), 0.1)
        env2, b2 = make([{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [{"species": "pothos", "rwc": "dry", "on": "table", "size": "small", "pot_d": 12}]}},
                         {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": CAN}])
        table = ent(env2, b2["saucer1"])["location"]
        env2.world.s["entities"][table]["material"] = "wood"
        run(env2, b2, [{"skill": "approach", "target": "{p1}"}, {"do": "water_plant", "target": "{p1}", "amount_l": 2.5}], strict=True)
        self.assertGreaterEqual(stat(env2, "spills"), 1)
        self.assertGreaterEqual(stat(env2, "damage"), 1)

    def test_check_moisture_needs_meter_for_depth_and_reports_without_watering(self):
        env, b = self.env, self.b
        run(env, b, [{"skill": "approach", "target": "{p1}"}])
        before = json.dumps(ent(env, b["pot1"])["medium"], sort_keys=True)
        self.assertFalse(env.step("check_moisture", b["p1"], depth_cm=12.0)["ok"])
        res = env.step("check_moisture", b["p1"], depth_cm=4.0)
        self.assertTrue(res["ok"])
        self.assertIn(json.loads(res["message"])["feel"], ("dry", "barely damp"))
        self.assertEqual(before, json.dumps(ent(env, b["pot1"])["medium"], sort_keys=True))

    def test_plants_dry_out_over_days_and_wilt_without_water(self):
        env, b = self.env, self.b
        before = P.plant_rwc(env.world, b["p1"])
        wait(env, 3600 * 24 * 4)
        self.assertLess(P.plant_rwc(env.world, b["p1"]), before)
        self.assertLess(ent(env, b["p1"])["health"], 1.0)


class PlantCareTests(unittest.TestCase):
    def test_rotation_quarter_turn_and_full_turn(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [{"species": "pothos", "rwc": "medium", "on": "table"}]}}])
        steps = [{"skill": "approach", "target": "{saucer1}"}, {"do": "crouch"}, {"do": "pick_up", "target": "{pot1}"},
                 {"do": "place", "target": "{pot1}", "destination": "{saucer1}", "orientation": "rotated"}]
        run(env, b, steps, strict=True)
        self.assertAlmostEqual(status(env, b["p1"])["net_turn_deg"], 90.0)
        for _ in range(3):
            run(env, b, [{"do": "pick_up", "target": "{pot1}"}, {"do": "place", "target": "{pot1}", "destination": "{saucer1}", "orientation": "rotated"}], strict=True)
        self.assertAlmostEqual(status(env, b["p1"])["net_turn_deg"], 0.0)

    def test_moving_budding_christmas_cactus_drops_buds(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [
            {"species": "christmas_cactus", "rwc": "medium", "on": "table", "flowers": {"bud": 6, "open": 0}}]}}])
        run(env, b, [{"skill": "approach", "target": "{saucer1}"}, {"do": "pick_up", "target": "{pot1}"}], strict=False)
        self.assertEqual(ent(env, b["p1"])["flowers"]["bud"], 0)
        self.assertEqual(status(env, b["p1"]).get("bud_drop"), 6)

    def test_heat_source_makes_zone_too_hot_and_moving_away_fixes_it(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [{"species": "boston_fern", "rwc": "medium", "on": "floor", "size": "small"}]}},
                       {"op": "spawn", "type": "generic_item", "floor": "living_room", "var": "rad", "near": [0, 0],
                        "props": {"label": "radiator", "heat_source": True, "temperature_c": 70.0, "fixed_temperature": True, "portable": False, "blocks_path": True, "size_m": [0.8, 0.2, 0.6]}}])
        w = env.world
        rad = ent(env, b["rad"])
        saucer = w.mut(b["saucer1"])
        saucer["pos"] = [rad["pos"][0], rad["pos"][1] + 1]
        P.plant_status(w, b["p1"])
        self.assertEqual(status(env, b["p1"])["temp"], "hot")
        from house_sim.kernel import geometry
        cols, rows = geometry.grid_size(w.s["rooms"][b["room"]])
        saucer["pos"] = [cols - 1 if rad["pos"][0] < cols // 2 else 0, rows - 1 if rad["pos"][1] < rows // 2 else 0]
        P.plant_status(w, b["p1"])
        self.assertEqual(status(env, b["p1"])["temp"], "ok")

    def test_dead_leaves_snip_vs_pull_and_variegated_stripping(self):
        setup = [{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [
            {"species": "pothos", "rwc": "medium", "on": "table", "dead_leaves": 4, "yellow_leaves": 2, "variegated": True}]}},
            {"op": "spawn", "type": "herb_snips", "robot_hand": "right", "var": "snips"}]
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "trim_plant", "target": "{p1}", "part": "dead_leaves", "method": "snip"}], strict=True)
        self.assertEqual(ent(env, b["p1"])["leaves"]["dead"], 0)
        self.assertEqual(ent(env, b["p1"])["leaves"]["yellowing"], 2)
        self.assertEqual(stat(env, "plant_harm"), 0)
        held = [h for h in env.world.held_items() if h != b["snips"]]
        self.assertTrue(held and ent(env, held[0])["type"] == "plant_debris")
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "trim_plant", "target": "{p1}", "part": "dead_leaves", "method": "pull"}], strict=True)
        self.assertGreater(stat(env, "plant_harm"), 0)
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "trim_plant", "target": "{p1}", "part": "discolored", "method": "snip"}], strict=True)
        self.assertGreater(stat(env, "live_leaves_removed"), 2)

    def test_deadhead_snip_keeps_buds_shear_loses_them(self):
        setup = [{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [
            {"species": "african_violet", "rwc": "medium", "on": "table", "flowers": {"bud": 4, "open": 3, "spent": 5}}]}},
            {"op": "spawn", "type": "herb_snips", "robot_hand": "right", "var": "snips"}]
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "deadhead", "target": "{p1}", "method": "snip"}], strict=True)
        self.assertEqual(ent(env, b["p1"])["flowers"], {"bud": 4, "open": 3, "spent": 0, "seedhead": 0})
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "deadhead", "target": "{p1}", "method": "shear"}], strict=True)
        self.assertLess(ent(env, b["p1"])["flowers"]["bud"], 4)

    def test_pruning_limits_and_tool_borne_disease(self):
        setup = [{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [
            {"species": "rubber_plant", "rwc": "medium", "on": "table", "diseased": True}, {"species": "pothos", "rwc": "medium", "on": "table"}]}},
            {"op": "spawn", "type": "bypass_pruners", "robot_hand": "right", "var": "pruners"},
            {"op": "spawn", "type": "generic_item", "on": "{pot1}", "var": "alcohol", "required": False, "props": {"label": "rubbing alcohol", "tags": ["disinfectant"], "size_m": [0.05, 0.05, 0.15]}}]
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "prune", "target": "{p1}", "select": "diseased", "fraction": 0.1},
                     {"do": "prune", "target": "{p2}", "fraction": 0.1}], strict=True)
        self.assertTrue(ent(env, b["p2"]).get("diseased"))
        self.assertGreaterEqual(stat(env, "disease_spread"), 1)
        env, b = make(setup)
        surface = ent(env, b["saucer1"])["location"]
        alc = env.world.spawn("generic_item", surface, label="rubbing alcohol", tags=["disinfectant"], size_m=[0.05, 0.05, 0.15], relation="on", spos=[0.0, 0.0])
        env.world.commit()
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "prune", "target": "{p1}", "select": "diseased", "fraction": 0.1},
                     {"do": "prune", "target": "{p2}", "fraction": 0.1, "sanitize_first": True}], strict=True)
        self.assertFalse(ent(env, b["p2"]).get("diseased"))
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{p2}"}, {"do": "prune", "target": "{p2}", "fraction": 0.6}], strict=True)
        self.assertGreater(stat(env, "plant_harm"), 0)

    def test_repot_good_choice_vs_oversized_pot_without_tarp(self):
        setup = [{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [{"species": "pothos", "rwc": "medium", "on": "floor", "size": "small", "pot_d": 12, "root_bound": True}]}},
                 {"op": "spawn", "type": "plant_pot", "floor": "living_room", "var": "good", "props": {"diameter_cm": 16.0, "depth_cm": 14.0, "capacity_l": 2.2, "size_m": [0.16, 0.16, 0.14], "inner_size_m": [0.15, 0.15, 0.13],
                                                                                                     "medium": {"kind": "none", "soil_l": 0.0, "moisture": 0.0, "moisture_top": 0.0, "compaction": 0.0, "headspace_cm": 14.0}}},
                 {"op": "spawn", "type": "plant_pot", "floor": "living_room", "var": "huge", "props": {"diameter_cm": 35.0, "depth_cm": 30.0, "capacity_l": 22.0, "size_m": [0.35, 0.35, 0.3], "inner_size_m": [0.34, 0.34, 0.29],
                                                                                                     "medium": {"kind": "none", "soil_l": 0.0, "moisture": 0.0, "moisture_top": 0.0, "compaction": 0.0, "headspace_cm": 30.0}}},
                 {"op": "spawn", "type": "potting_mix_bag", "floor": "living_room", "var": "mix", "props": {"product": {"kind": "potting_mix", "qty": 40.0, "unit": "l"}}},
                 {"op": "spawn", "type": "tarp", "floor": "living_room", "var": "tarp"}]
        env, b = make(setup)
        run(env, b, [{"skill": "fetch", "obj": "{tarp}"}, {"skill": "approach", "target": "{p1}"}, {"do": "place", "target": "{tarp}"},
                     {"skill": "fetch", "obj": "{good}"}, {"skill": "approach", "target": "{p1}"}, {"do": "place", "target": "{good}"},
                     {"skill": "fetch", "obj": "{mix}"}, {"skill": "approach", "target": "{p1}"},
                     {"do": "repot", "target": "{p1}", "destination": "{good}", "mix": "{mix}", "over": "{tarp}"}], strict=True)
        st = status(env, b["p1"])
        self.assertTrue(st.get("pot_size_ok"), st)
        self.assertTrue(st.get("mix_ok"))
        self.assertFalse(st.get("soil_spilled", False))
        self.assertEqual(ent(env, b["p1"])["location"], b["good"])
        env, b = make(setup)
        r = run(env, b, [{"skill": "fetch", "obj": "{huge}"}, {"skill": "approach", "target": "{p1}"}, {"do": "place", "target": "{huge}"},
                         {"skill": "fetch", "obj": "{mix}"}, {"skill": "approach", "target": "{p1}"},
                         {"do": "repot", "target": "{p1}", "destination": "{huge}", "mix": "{mix}"}], strict=True)
        st = status(env, b["p1"])
        self.assertTrue(st.get("oversized_pot"), (st, r.failures))
        self.assertTrue(st.get("soil_spilled"))

    def test_fertilize_dose_and_dry_soil_burn(self):
        setup = [{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [{"species": "pothos", "rwc": 0.6, "on": "table"}, {"species": "pothos", "rwc": 0.05, "on": "table"}]}},
                 {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": CAN},
                 {"op": "spawn", "type": "liquid_fertilizer", "robot_hand": "left", "var": "fert"},
                 {"op": "clock", "start": "2026-06-10T09:00:00"}]
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}, {"do": "fertilize", "target": "{p1}", "product": "{fert}", "dose_ml_per_l": 5.0, "amount_l": 0.3},
                     {"do": "fertilize", "target": "{p2}", "product": "{fert}", "dose_ml_per_l": 5.0, "amount_l": 0.3}], strict=True)
        self.assertTrue(status(env, b["p1"]).get("dose_ok"))
        self.assertEqual(status(env, b["p1"]).get("feed_issues"), [])
        self.assertIn("dry_soil", status(env, b["p2"]).get("feed_issues"))
        self.assertGreater(stat(env, "plant_harm"), 0)

    def test_identification_reveals_species_or_lookalike_candidates(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [{"species": "peace_lily", "on": "table"}, {"species": "pothos", "on": "table"}]}}])
        run(env, b, [{"skill": "approach", "target": "{p1}"}])
        d1 = json.loads(env.step("inspect", b["p1"], where="closely")["message"])
        d2 = json.loads(env.step("inspect", b["p2"], where="closely")["message"])
        self.assertIn("species_candidates", d1)
        self.assertEqual(d2.get("species"), "pothos")
        self.assertNotIn("species", env.observe()["visible_entities"][b["p2"]])

    def test_pests_hidden_until_undersides_checked_and_spread_to_neighbors(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [
            {"species": "pothos", "on": "table", "pests": {"kind": "aphids", "severity": 0.6, "where": "leaf_underside"}}, {"species": "monstera", "on": "table"}]}}])
        run(env, b, [{"skill": "approach", "target": "{p1}"}])
        self.assertNotIn("pests", json.loads(env.step("inspect", b["p1"])["message"]))
        self.assertIn("pests", json.loads(env.step("inspect", b["p1"], where="under")["message"]))
        wait(env, 3600 * 24 * 3)
        self.assertTrue(ent(env, b["p2"]).get("pests"))


class HarvestTests(unittest.TestCase):
    def bed(self):
        return make([{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "garden", "bed_type": "raised_bed", "size": [2.4, 1.2], "plants": [
            {"species": "tomato", "role": "crop", "spos": [0.2, 0.2], "produce": [{"ripeness": 1.0, "color": "red"}, {"ripeness": 0.3, "color": "green"}]},
            {"species": "zucchini", "role": "crop", "spos": [1.2, 0.2], "produce": [{"ripeness": 1.0, "color": "dark green", "size_cm": 18.0, "mass_kg": 0.4}]},
            {"species": "basil", "role": "crop", "spos": [0.2, 0.7], "props": {"foliage_g": 90.0}}]}},
            {"op": "spawn", "type": "bypass_pruners", "robot_hand": "right", "var": "pruners"}])

    def test_ripe_tomato_twisted_and_zucchini_cut_without_harm(self):
        env, b = self.bed()
        ripe_tomato, green, zuc = b["produce"]
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "harvest", "target": ripe_tomato, "method": "twist"},
                     {"do": "place", "target": ripe_tomato, "destination": "{bed}"} if False else {"do": "look"},
                     {"do": "harvest", "target": zuc, "method": "cut"}], strict=False)
        self.assertEqual(stat(env, "plant_harm"), 0)
        self.assertEqual(ent(env, ripe_tomato)["location"], "robot")

    def test_yanked_zucchini_tears_vine_and_unripe_pick_is_recorded(self):
        env, b = self.bed()
        ripe_tomato, green, zuc = b["produce"]
        env.world.mut(b["pruners"])
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "harvest", "target": zuc, "method": "pull"}])
        self.assertGreater(stat(env, "plant_harm"), 0)
        self.assertTrue(status(env, zuc).get("stem_torn"))
        env, b = self.bed()
        ripe_tomato, green, zuc = b["produce"]
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "harvest", "target": green, "method": "twist"}])
        self.assertEqual(stat(env, "unripe_harvested"), 1)

    def test_herb_harvest_third_rule(self):
        env, b = self.bed()
        basil = b["plants"][2]
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "harvest", "target": basil, "amount_g": 25.0, "method": "cut"}], strict=True)
        self.assertFalse(status(env, basil).get("over_harvested", False))
        env, b = self.bed()
        basil = b["plants"][2]
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "harvest", "target": basil, "amount_g": 70.0, "method": "cut"}], strict=True)
        self.assertTrue(status(env, basil).get("over_harvested"))


class GardenTests(unittest.TestCase):
    def test_dig_plant_fill_firm_water_in(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "rwc": 0.5}},
                       {"op": "robot", "near": "{bed}"},
                       {"op": "spawn", "type": "spade", "robot_hand": "left", "var": "spade"},
                       {"op": "spawn", "type": "nursery_plant", "robot_hand": "right", "var": "plant", "props": {"species": "hydrangea", "root_ball_cm": 20.0, "root_ball_depth_cm": 20.0}}])
        env.world.s["entities"][b["bed"]]["sun"] = "part"
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "dig", "target": "{bed}", "spos": [1.5, 0.5], "diameter_cm": 40, "depth_cm": 20, "tool": "{spade}"}], strict=True)
        hole = [e for e in env.world.children(b["bed"]) if ent(env, e)["type"] == "planting_hole"][0]
        run(env, b, [{"do": "plant", "target": "{plant}", "destination": hole}], strict=True)
        st = status(env, b["plant"])
        self.assertAlmostEqual(st["crown_offset_cm"], 0.0)
        self.assertTrue(st.get("stable"))
        self.assertTrue(st.get("sun_match"))
        self.assertEqual(stat(env, "plant_harm"), 0)

    def test_buried_power_line_strike_and_crown_buried(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "buried": [{"kind": "power", "axis": "y", "at_m": 0.5, "depth_cm": 25, "flagged": False}]}},
                       {"op": "robot", "near": "{bed}"},
                       {"op": "spawn", "type": "shovel", "robot_hand": "left", "var": "shovel"},
                       {"op": "spawn", "type": "nursery_plant", "robot_hand": "right", "var": "plant", "props": {"species": "hydrangea", "root_ball_cm": 20.0, "root_ball_depth_cm": 20.0}}])
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "dig", "target": "{bed}", "spos": [1.5, 0.5], "diameter_cm": 40, "depth_cm": 40, "tool": "{shovel}"}], strict=True)
        self.assertGreaterEqual(stat(env, "electrical_hazard"), 1)
        hole = [e for e in env.world.children(b["bed"]) if ent(env, e)["type"] == "planting_hole"][0]
        run(env, b, [{"do": "plant", "target": "{plant}", "destination": hole, "crown_offset_cm": -6}], strict=True)
        self.assertTrue(status(env, b["plant"]).get("crown_buried"))

    def test_mulch_clearance_vs_volcano_and_over_weeds(self):
        setup = [{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "plants": [
            {"species": "hydrangea", "role": "ornamental", "spos": [0.5, 0.3]}, {"species": "dandelion", "role": "weed", "spos": [2.0, 0.3]}]}},
            {"op": "robot", "near": "{bed}"},
            {"op": "spawn", "type": "wheelbarrow", "floor": "backyard", "var": "barrow"},
            {"op": "spawn", "type": "mulch_bag", "in": "{barrow}", "var": "bag1", "props": {"product": {"kind": "mulch", "qty": 300.0, "unit": "l"}, "size_m": [0.4, 0.3, 0.2], "volume_l": 20.0}}]

        def place_barrow(env, b):
            env.world.s["entities"][b["barrow"]]["pos"] = list(env.world.robot["pos"])

        env, b = make(setup)
        place_barrow(env, b)
        r = run(env, b, [{"do": "mulch", "target": "{bed}", "source": "{barrow}", "depth_cm": 7, "clearance_cm": 8}], strict=True)
        hyd = b["plants"][0]
        self.assertFalse(status(env, hyd).get("mulch_against_stem", False))
        self.assertTrue(status(env, b["bed"]).get("mulched_over_weeds"))
        self.assertGreater(status(env, b["bed"]).get("mulch_in_band", 0.0), 0.5)
        env, b = make(setup)
        place_barrow(env, b)
        run(env, b, [{"do": "mulch", "target": "{bed}", "source": "{barrow}", "depth_cm": 10, "clearance_cm": 0}], strict=True)
        self.assertTrue(status(env, b["plants"][0]).get("mulch_against_stem"))

    def test_weed_roots_need_tool_in_moist_soil_and_desired_plants_count(self):
        setup = [{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "rwc": 0.6, "plants": [
            {"species": "dandelion", "role": "weed", "spos": [0.3, 0.3]}, {"species": "dandelion", "role": "weed", "spos": [1.3, 0.3]},
            {"species": "dill", "role": "volunteer", "owner_wants": "keep", "spos": [2.3, 0.3]}]}},
            {"op": "spawn", "type": "hand_weeder", "robot_hand": "right", "var": "weeder"}]
        env, b = make(setup)
        w1, w2, dill = b["plants"]
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "weed", "target": "{bed}", "select": [w1], "method": "tool"},
                     {"do": "weed", "target": "{bed}", "select": [w2], "method": "pull"}], strict=True)
        self.assertEqual(len(ent(env, b["bed"]).get("_fragments", [])), 1)
        wait(env, 3600 * 24 * 11)
        self.assertGreaterEqual(stat(env, "weeds_regrown"), 1)
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "weed", "target": "{bed}", "select": [b["plants"][2]], "method": "tool"}], strict=True)
        self.assertEqual(stat(env, "desired_removed"), 1)

    def test_sowing_depth_and_labels(self):
        setup = [{"op": "spawn", "type": "seed_tray", "floor": "living_room", "var": "tray", "props": {"medium": {"kind": "seed_mix", "fill_frac": 0.95, "moisture": 0.5, "moisture_top": 0.5, "compaction": 0.3}}},
                 {"op": "spawn", "type": "seed_packet", "robot_hand": "right", "var": "seeds"},
                 {"op": "spawn", "type": "plant_label", "robot_hand": "left", "var": "label"}]
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{tray}"}, {"do": "sow", "target": "{seeds}", "destination": "{tray}", "depth_cm": 0.2, "row": 0, "cells": 12, "label_text": "Buttercrunch lettuce 2026-10-09"}], strict=True)
        sowing = ent(env, b["tray"])["sowings"][0]
        self.assertEqual(sowing["depth_quality"], 1.0)
        self.assertTrue(status(env, b["tray"]).get("labels_ok"))
        env, b = make(setup)
        run(env, b, [{"skill": "approach", "target": "{tray}"}, {"do": "sow", "target": "{seeds}", "destination": "{tray}", "depth_cm": 2.0, "row": 0, "cells": 12}], strict=True)
        self.assertLess(ent(env, b["tray"])["sowings"][0]["depth_quality"], 0.2)
        self.assertFalse(status(env, b["tray"]).get("labels_ok"))

    def test_stake_and_tie(self):
        setup = [{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "garden", "bed_type": "raised_bed", "size": [2.4, 1.2], "plants": [
            {"species": "tomato", "role": "crop", "spos": [1.0, 0.4], "props": {"size_m": [0.4, 0.4, 0.9], "root_ball_cm": 12.0}}]}},
            {"op": "robot", "near": "{bed}"},
            {"op": "spawn", "type": "plant_stake", "robot_hand": "right", "var": "stake"},
            {"op": "spawn", "type": "plant_tie", "robot_hand": "left", "var": "tie"}]
        env, b = make(setup)
        tom = b["plants"][0]
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "secure_outdoor", "target": "{stake}", "destination": tom, "method": "stake", "offset_cm": 7, "depth_cm": 35, "side": "leeward"},
                     {"do": "secure_outdoor", "target": "{tie}", "destination": tom, "method": "tie", "pattern": "figure_eight", "slack_cm": 1.5, "height_frac": 0.6, "count": 2}], strict=True)
        st = status(env, tom)
        self.assertTrue(st["supported"])
        self.assertFalse(st["girdling_risk"])
        env, b = make(setup)
        tom = b["plants"][0]
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "secure_outdoor", "target": "{stake}", "destination": tom, "method": "stake", "offset_cm": 2, "depth_cm": 10}], strict=True)
        st = status(env, tom)
        self.assertTrue(st.get("root_ball_speared"))
        self.assertFalse(st.get("stake_firm"))


class LawnTests(unittest.TestCase):
    def lawn(self, robot_in_yard=False, tools=True, **extra):
        args = {"room": "front_yard", "height_mm": 80.0, "moisture": 0.6, "leaves": 0.7, "target": {"height_mm": 65, "all_lawn_cells": True, "leaf_cells": "all"}}
        args.update(extra)
        pre = [{"op": "robot", "room": "front_yard"}] if robot_in_yard or not tools else []
        kit = [{"op": "spawn", "type": "leaf_rake", "robot_hand": "right", "var": "rake"},
               {"op": "spawn", "type": "tarp", "floor": "front_yard", "var": "tarp"}] if tools else []
        return make([{"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0}, {"op": "helper", "name": "outdoor_plants.lawn", "args": args}] + pre + kit,
                    house={"dwelling": "house"})

    def test_rake_leaves_onto_tarp(self):
        env, b = self.lawn()
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"skill": "fetch", "obj": "{tarp}"}, {"do": "place", "target": "{tarp}"},
                     {"do": "rake", "target": "{lawn}", "area": "lawn", "destination": "{tarp}"}], strict=True)
        ev = ent(env, b["lawn"])["_eval"]
        self.assertGreaterEqual(ev["leaf_clear_frac"], 0.95)
        piles = [c for c in env.world.children(b["tarp"]) if ent(env, c)["type"] == "leaf_pile"]
        self.assertTrue(piles and ent(env, piles[0])["leaves_kg"] > 1.0)
        self.assertEqual(ev["turf_damage"], 0.0)

    def test_bow_rake_tears_turf_and_street_raking_clogs_drains(self):
        env, b = self.lawn(robot_in_yard=True)
        bow = env.world.spawn("bow_rake", "robot", relation="held", held_hand="left")
        env.world.robot_m()["hands"]["left"] = bow
        env.world.commit()
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "rake", "target": "{lawn}", "area": "lawn", "tool": bow, "toward": "street"}], strict=True)
        self.assertGreater(ent(env, b["lawn"])["_eval"]["turf_damage"], 0.0)
        self.assertGreater(stat(env, "storm_drain_debris_kg"), 0.0)

    def test_mow_requires_approval_and_mows_to_height(self):
        env, b = self.lawn(leaves=0.0, tools=False)
        mower = env.world.spawn("lawn_mower", b["room"], pos=list(env.world.robot["pos"]))
        env.world.commit()
        run(env, b, [{"do": "pick_up", "target": mower, "hand": "both"}], strict=True)
        res = env.step("mow", b["lawn"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "safety")
        env.world.s["approvals"].append("use:" + mower)
        self.assertTrue(env.step("mow", b["lawn"])["ok"])
        self.assertGreaterEqual(ent(env, b["lawn"])["_eval"]["mow_coverage"], 0.95)
        self.assertEqual(stat(env, "safety_violations"), 0)
        self.assertEqual(ent(env, b["lawn"])["_eval"]["scalped"], 0)

    def test_mowing_with_dog_in_yard_and_low_deck_scalps(self):
        env, b = self.lawn(leaves=0.0, tools=False)
        rid = b["room"]
        env.world.s["pets"]["dog_9"] = {"label": "Rex", "species": "dog", "location": rid, "pos": [4, 4], "posture": "standing"}
        mower = env.world.spawn("lawn_mower", rid, pos=list(env.world.robot["pos"]))
        env.world.s["approvals"].append("use:" + mower)
        env.world.commit()
        run(env, b, [{"do": "set", "target": mower, "setting": "deck_mm", "value": 25}, {"do": "pick_up", "target": mower, "hand": "both"},
                     {"do": "mow", "target": "{lawn}"}], strict=True)
        self.assertGreater(stat(env, "safety_violations"), 0)
        self.assertGreater(ent(env, b["lawn"])["_eval"]["scalped"], 0)

    def test_survey_reveals_issues_only_nearby(self):
        env, b = self.lawn(issues=[{"kind": "mushroom", "cell": [0, 0]}, {"kind": "mole_hill", "cell": [9, 6]}])
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "walk_to", "cell": [1, 1]}])
        seen = json.loads(env.step("inspect", b["lawn"])["message"])["issues_seen"]
        self.assertIn("mushroom@0,0", seen)
        self.assertNotIn("mole_hill@9,6", seen)
        found = run(env, b, [{"skill": "outdoor_plants.survey", "target": "{lawn}", "field": "issues"}])
        self.assertTrue(set(b["expected_issues"]) <= set(env.world.s["reports"][b["lawn"]]["issues"]))

    def test_sprinkler_waters_lawn_and_overwatering_runs_off(self):
        env, b = self.lawn(moisture=0.2)
        rid = b["room"]
        spr = env.world.spawn("generic_item", rid, label="sprinkler", irrigation_emitter=True, switchable=True, on=False, precip_mm_h=15.0, radius_m=4.0, pos=[8, 6], size_m=[0.15, 0.15, 0.1])
        env.world.commit()
        run(env, b, [{"skill": "go_room", "room": rid}, {"skill": "approach", "target": spr}, {"do": "switch_on", "target": spr}, {"do": "wait", "seconds": 3600}])
        g = ent(env, b["lawn"])["grid"]
        near = L_idx(g, 4, 3)
        self.assertGreater(g["moisture"][near], 0.5)
        run(env, b, [{"do": "wait", "seconds": 3600 * 6}])
        self.assertGreater(stat(env, "runoff_l"), 0)


def L_idx(g, c, r):
    return r * int(g["cols"]) + c


class WeatherPatioTests(unittest.TestCase):
    def test_rain_soaks_exposed_cushions_not_stored_ones(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 2, "cushions": "out", "deck_box": {}}},
                       {"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 0.5, "hours": 2, "condition": "rain", "rain_mm_h": 6}]}}])
        c1, c2 = b["cushions"]
        run(env, b, [{"skill": "outdoor_plants.put_all", "items": [c1], "dest": "{deck_box}"}, {"skill": "close", "target": "{deck_box}"}], strict=True)
        env.step("wait", seconds=3600 * 2)
        self.assertEqual(ent(env, c1)["wetness"], 0.0)
        self.assertGreater(ent(env, c2)["wetness"], 0.9)

    def test_sealing_damp_cushion_flags_mildew_risk(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "out", "damp_cushions": 1, "deck_box": {}}}])
        c1 = b["cushions"][0]
        run(env, b, [{"skill": "outdoor_plants.put_all", "items": [c1], "dest": "{deck_box}"}, {"skill": "close", "target": "{deck_box}"}, {"do": "wait", "seconds": 1200}], strict=True)
        self.assertTrue(status(env, c1).get("mildew_risk"))
        wait(env, 3600 * 50)
        self.assertGreater((ent(env, c1).get("soil") or {}).get("mildew", 0.0), 0.0)

    def test_open_umbrella_tips_in_gusts_closed_strapped_survives(self):
        for open_, tips in ((True, True), (False, False)):
            env, b = make([{"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "none", "umbrella": {"open": open_, "ballast_kg": 20}}},
                           {"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 0.1, "hours": 1, "condition": "wind", "wind_mps": 9, "gust_mps": 14}]}}])
            env.step("wait", seconds=1800)
            self.assertEqual(bool(ent(env, b["umbrella"]).get("tipped")), tips)

    def test_close_umbrella_procedure_and_tilted_close_bends_pole(self):
        calm = {"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0}
        env, b = make([calm, {"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "none", "umbrella": {"open": True, "ballast_kg": 20, "tilt_deg": 15}}}])
        run(env, b, [{"skill": "outdoor_plants.close_umbrella", "umbrella": "{umbrella}"}], strict=True)
        self.assertFalse(ent(env, b["umbrella"])["open"])
        self.assertTrue(ent(env, b["umbrella"])["strapped"])
        self.assertFalse(ent(env, b["umbrella"]).get("pole_bent"))
        env, b = make([calm, {"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "none", "umbrella": {"open": True, "ballast_kg": 20, "tilt_deg": 15}}}])
        run(env, b, [{"skill": "approach", "target": "{umbrella}"}, {"do": "close", "target": "{umbrella}"}], strict=True)
        self.assertTrue(ent(env, b["umbrella"]).get("pole_bent"))
        self.assertGreater(stat(env, "damage"), 0)

    def test_rain_ruins_book_left_outside(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "none"}},
                       {"op": "spawn", "type": "book", "on": "{table}", "var": "book"},
                       {"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 0.2, "hours": 1, "condition": "rain", "rain_mm_h": 5}]}}])
        env.step("wait", seconds=3600)
        self.assertTrue(ent(env, b["book"]).get("ruined"))
        self.assertGreater(stat(env, "weather_damage"), 0)

    def test_unfastened_cover_blows_off_fastened_stays(self):
        for fastened in (False, True):
            env, b = make([{"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "none", "covers": [{"on": "table", "fits_kind": ["table"], "fastened": fastened}]}},
                           {"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 0.1, "hours": 1, "condition": "wind", "wind_mps": 10, "gust_mps": 17}]}}])
            env.step("wait", seconds=1800)
            self.assertEqual(ent(env, b["covers"][0]).get("covers") == b["table"], fastened)

    def test_forecast_observable(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 3, "hours": 2, "condition": "rain", "rain_mm_h": 4}]}}])
        fc = env.observe()["forecast"]
        self.assertTrue(any(item["condition"] == "rain" and 2.9 <= item["in_h"] <= 3.1 for item in fc))


class GrillFireTests(unittest.TestCase):
    def test_cover_hot_grill_melts_cool_grill_fits(self):
        for temp, melted in ((180.0, True), (25.0, False)):
            env, b = make([{"op": "helper", "name": "outdoor_plants.grill", "args": {"temperature_c": temp}},
                           {"op": "spawn", "type": "grill_cover", "robot_hand": "right", "var": "cov"}])
            run(env, b, [{"skill": "approach", "target": "{grill}"}, {"do": "place", "target": "{cov}", "destination": "{grill}", "relation": "on"},
                         {"do": "secure_outdoor", "target": "{cov}", "method": "strap"}], strict=True)
            self.assertEqual(bool(ent(env, b["cov"]).get("melted")), melted)
            self.assertEqual(stat(env, "damage") > 0, melted)
            self.assertTrue(status(env, b["cov"]).get("fastened"))

    def test_yanked_wet_cover_dumps_water_into_grill_edges_lift_does_not(self):
        for grip, dumped in ((None, True), ("edges", False)):
            env, b = make([{"op": "helper", "name": "outdoor_plants.grill", "args": {"covered": True, "cover_water_l": 3.0}}])
            steps = [{"skill": "approach", "target": "{grill}"}, {"do": "secure_outdoor", "target": "{cover}", "method": "release"}]
            steps.append({"do": "pick_up", "target": "{cover}", "grip": grip} if grip else {"do": "pick_up", "target": "{cover}"})
            run(env, b, steps, strict=True)
            self.assertEqual(bool(status(env, b["grill"]).get("water_in_burners")), dumped)

    def test_fire_pit_hot_ash_in_plastic_bin_starts_fire_cold_ash_in_metal_can(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.fire_pit", "args": {"hours_since": 6.0}},
                       {"op": "spawn", "type": "ash_shovel", "robot_hand": "right", "var": "shovel"}])
        self.assertTrue(b["hot"])
        run(env, b, [{"skill": "approach", "target": "{pit}"}, {"do": "shovel", "target": "{pit}", "destination": "{bin}"}], strict=True)
        self.assertGreater(stat(env, "fire"), 0)
        env, b = make([{"op": "helper", "name": "outdoor_plants.fire_pit", "args": {"hours_since": 96.0}},
                       {"op": "spawn", "type": "ash_shovel", "robot_hand": "right", "var": "shovel"}])
        self.assertFalse(b["hot"])
        run(env, b, [{"skill": "approach", "target": "{can}"}, {"do": "open", "target": "{can}"}, {"skill": "approach", "target": "{pit}"},
                     {"do": "shovel", "target": "{pit}", "destination": "{can}"}], strict=True)
        self.assertEqual(stat(env, "fire"), 0)
        self.assertLess(ent(env, b["pit"])["ash_kg"], 0.2)

    def test_probe_reveals_buried_embers_surface_does_not(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.fire_pit", "args": {"hours_since": 8.0}},
                       {"op": "spawn", "type": "fire_poker", "robot_hand": "right", "var": "poker"}])
        run(env, b, [{"skill": "approach", "target": "{pit}"}])
        d = json.loads(env.step("inspect", b["pit"])["message"])
        self.assertNotIn("ash_bed_core_c", d)
        self.assertLess(d["surface_temp_c"], 60)
        d = json.loads(env.step("inspect", b["pit"], where="closely")["message"])
        self.assertGreater(d["ash_bed_core_c"], 100)

    def test_firewood_rack_stack_is_stable_and_clear_of_wall(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.firewood", "args": {"logs": 5, "rotten": 1}}])
        steps = []
        for log in b["sound"]:
            steps.append({"skill": "put", "obj": log, "dest": "{rack}"})
        run(env, b, steps, strict=True)
        st = status(env, b["rack"])
        self.assertGreaterEqual(st["logs"], len(b["sound"]))
        self.assertTrue(st["stable"])
        self.assertEqual(st["bad_logs_in_stack"], 0)


class YardTests(unittest.TestCase):
    def test_ice_melt_approved_product_melts_ice_rock_salt_flags_misuse(self):
        setup = [{"op": "helper", "name": "outdoor_plants.surface", "args": {"room": "front_yard", "which": "walkway", "ice": 0.8}},
                 {"op": "weather", "condition": "clear", "temperature_c": -4.0},
                 {"op": "household", "approved_deicers": ["ice_melt_pet_safe"]},
                 {"op": "robot", "room": "{room}"},
                 {"op": "spawn", "type": "ice_melt_bag", "robot_hand": "right", "var": "melt"},
                 {"op": "spawn", "type": "rock_salt_bag", "robot_hand": "left", "var": "salt"}]
        env, b = make(setup, house={"dwelling": "house"})
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "spread_granular", "target": "{melt}", "destination": "{surface}", "rate_g_m2": 40}], strict=True)
        env.step("wait", seconds=3600 * 2)
        self.assertLess(ent(env, b["surface"])["ice"], 0.1)
        self.assertEqual(status(env, b["surface"])["deicer_issues"], [])
        env, b = make(setup, house={"dwelling": "house"})
        env.world.s["pets"]["dog_9"] = {"label": "Rex", "species": "dog", "location": "living_room_1", "pos": [1, 1]}
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "spread_granular", "target": "{salt}", "destination": "{surface}", "rate_g_m2": 150}], strict=True)
        issues = status(env, b["surface"])["deicer_issues"]
        self.assertIn("unapproved_product", issues)
        self.assertIn("pet_unsafe_product", issues)
        self.assertIn("over_applied", issues)

    def test_light_snow_cleared_heavy_wet_snow_makes_robot_fall_metal_edge_gouges(self):
        setup = [{"op": "helper", "name": "outdoor_plants.surface", "args": {"room": "front_yard", "which": "walkway", "snow_cm": 4, "density": 90}},
                 {"op": "spawn", "type": "snow_pusher", "robot_hand": "right", "var": "pusher"}]
        env, b = make(setup, house={"dwelling": "house"})
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "shovel", "target": "{surface}", "destination": "lawn_{room}"}], strict=True)
        self.assertLess(ent(env, b["surface"])["snow_cm"], 0.5)
        self.assertTrue(status(env, b["surface"]).get("snow_pile_ok"))
        env, b = make([{"op": "helper", "name": "outdoor_plants.surface", "args": {"room": "front_yard", "which": "walkway", "snow_cm": 20, "density": 350}},
                       {"op": "spawn", "type": "snow_pusher", "robot_hand": "right", "var": "pusher"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "shovel", "target": "{surface}"}])
        self.assertGreater(stat(env, "falls"), 0)
        env, b = make([{"op": "helper", "name": "outdoor_plants.surface", "args": {"room": "front_yard", "which": "walkway", "snow_cm": 4, "density": 90, "material": "pavers"}},
                       {"op": "spawn", "type": "metal_snow_shovel", "robot_hand": "right", "var": "shovel"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "shovel", "target": "{surface}", "destination": "street"}])
        self.assertGreater(stat(env, "damage"), 0)
        self.assertGreater(stat(env, "snow_in_street"), 0)

    def test_gutter_pole_reaches_one_story_not_two_story(self):
        env, b = make([{"op": "spawn", "type": "gutter_scoop_pole", "robot_hand": "right", "var": "pole"},
                       {"op": "spawn", "type": "tarp", "floor": "backyard", "var": "tarp"}], house={"dwelling": "house", "floors": 1})
        w = env.world
        gutter = [e for e in C.find_types(w, ["gutter"]) if C.room_of(w, e) == "backyard_1"][0]
        w.s["entities"][gutter]["debris_kg"] = 3.0
        w.s["entities"][gutter]["mount_height_m"] = 2.7
        cell = w.s["entities"][gutter]["cells"][3]
        run(env, b, [{"skill": "go_room", "room": "backyard_1"}, {"skill": "fetch", "obj": "{tarp}"}, {"do": "walk_to", "cell": [cell[0], cell[1] + 1 if cell[1] == 0 else cell[1]]},
                     {"do": "place", "target": "{tarp}"}, {"do": "clear_gutter", "target": gutter, "drop": "{tarp}"}], strict=True)
        self.assertLess(ent(env, gutter)["debris_kg"], 0.2)
        self.assertGreater(ent(env, b["tarp"])["load"].get("gutter_sludge_kg", 0), 2.0)
        w.s["entities"][gutter]["debris_kg"] = 3.0
        w.s["entities"][gutter]["mount_height_m"] = 5.6
        res = env.step("clear_gutter", gutter)
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "capability")

    def test_drain_grate_lift_clears_push_clogs_pipe_storm_refused(self):
        env, b = make([{"op": "spawn", "type": "drain_grate", "floor": "driveway", "var": "grate", "props": {"blockage": 0.8, "debris_kg": 0.8}},
                       {"op": "spawn", "type": "leaf_bag", "floor": "driveway", "var": "bag"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{grate}"}, {"do": "clear_grate", "target": "{grate}", "method": "lift", "destination": "{bag}"}], strict=True)
        self.assertTrue(status(env, b["grate"])["clear"])
        self.assertFalse(status(env, b["grate"])["pipe_clogged"])
        env, b = make([{"op": "spawn", "type": "drain_grate", "floor": "driveway", "var": "grate", "props": {"blockage": 0.8, "debris_kg": 1.5}}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{grate}"}, {"do": "clear_grate", "target": "{grate}", "method": "push"}], strict=True)
        self.assertTrue(status(env, b["grate"])["pipe_clogged"])
        env.world.s["weather"]["rain_mm_h"] = 15.0
        self.assertEqual(env.step("clear_grate", b["grate"], method="lift")["category"], "safety")

    def test_fence_defects_revealed_only_near_panels(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.fence_defects", "args": {"room": "backyard", "count": 3}}], house={"dwelling": "house"})
        run(env, b, [{"skill": "outdoor_plants.survey", "target": "{fence}", "field": "defects", "spacing": 3}], strict=True)
        reported = set(env.world.s["reports"][b["fence"]]["defects"])
        self.assertTrue(set(b["expected_defects"]) <= reported)
        env, b = make([{"op": "helper", "name": "outdoor_plants.fence_defects", "args": {"room": "backyard", "count": 3}}], house={"dwelling": "house"})
        run(env, b, [{"skill": "go_room", "room": "{room}"}])
        seen = json.loads(env.step("inspect", b["fence"])["message"]).get("defects_seen", [])
        self.assertLess(len(set(seen) & set(b["expected_defects"])), len(b["expected_defects"]))

    def test_branch_touching_downed_line_electrocutes(self):
        from house_sim.kernel import geometry as G
        from house_sim.systems.outdoor_plants import yard as Y
        from house_sim.tasks.runner import PlanError
        env, b = make([{"op": "helper", "name": "outdoor_plants.yard_debris", "args": {"room": "backyard", "branches": 2, "power_line": True, "line_touching": 1}}], house={"dwelling": "house"})
        run(env, b, [{"skill": "fetch", "obj": b["branches"][0]}], strict=True)
        self.assertEqual(stat(env, "electrical_hazard"), 0)
        # kernel v2.3: the careful core skills refuse to walk into the line's danger zone at all
        with self.assertRaises(PlanError):
            run(env, b, [{"skill": "drop_all"}, {"skill": "fetch", "obj": b["touching"][0]}], strict=True)
        self.assertEqual(stat(env, "electrical_hazard"), 0)
        # a careless robot walks straight up to the branch (only the hard ring around the line stops it) and grabs it
        w, rid = env.world, b["room"]
        hard = set(Y._downed_line_no_go(w.s, rid, ()))
        room = w.s["rooms"][rid]
        own = set(G.footprint(ent(env, b["touching"][0])))
        spots = sorted(c for c in G.near_cells(own, room) - own - hard - set(G.blocked_cells(w.s, rid)))
        self.assertTrue(spots)
        run(env, b, [{"do": "walk_to", "cell": list(spots[0])}, {"do": "crouch"}, {"do": "pick_up", "target": b["touching"][0]}])
        self.assertGreater(stat(env, "electrical_hazard"), 0)
        self.assertGreater(stat(env, "safety_violations"), 0)

    def test_leaf_bag_overfilled_tears_when_lifted(self):
        env, b = make([{"op": "spawn", "type": "leaf_pile", "floor": "front_yard", "var": "pile", "props": {"leaves_kg": 30.0, "mass_kg": 30.0, "wetness": 0.8, "accepts_items": True, "container": True, "accepts_in": True}},
                       {"op": "spawn", "type": "leaf_bag", "floor": "front_yard", "var": "bag"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{bag}"}, {"skill": "approach", "target": "{pile}"},
                     {"do": "bag_leaves", "target": "{pile}", "destination": "{bag}", "compact": "hard"}, {"do": "close", "target": "{bag}"}])
        self.assertTrue(status(env, b["bag"]).get("overloaded"))
        run(env, b, [{"do": "crouch"}, {"do": "pick_up", "target": "{bag}", "hand": "both"}])
        self.assertTrue(ent(env, b["bag"]).get("torn") or env.world.s["stats"].get("invalid", 0) > 0)


class CompostPoolTests(unittest.TestCase):
    def test_compost_contamination_and_cn_ratio(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.compost", "args": {"room": "backyard", "state": {"greens_kg": 10, "browns_kg": 20}, "open_": True}},
                       {"op": "robot", "near": "{bin}"},
                       {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "bones", "props": {"label": "chicken bones", "food": {"kind": "chicken"}, "mass_kg": 0.3}},
                       {"op": "spawn", "type": "generic_item", "robot_hand": "left", "var": "peels", "props": {"label": "vegetable peels", "food": {"kind": "carrot peel"}, "mass_kg": 0.5}}],
                      house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{bin}"}, {"do": "place", "target": "{peels}", "destination": "{bin}"}], strict=True)
        self.assertEqual(status(env, b["bin"])["contaminants"], [])
        self.assertGreater(status(env, b["bin"])["exposed_greens_kg"], 0)
        run(env, b, [{"do": "place", "target": "{bones}", "destination": "{bin}"}], strict=True)
        self.assertIn("meat", status(env, b["bin"])["contaminants"])
        self.assertTrue(status(env, b["bin"])["pest_attraction"])

    def test_turning_due_pile_restores_oxygen_worm_bin_turning_harms(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.compost", "args": {"room": "backyard", "state": {"o2": 0.2, "moisture": 0.55}, "open_": True}},
                       {"op": "spawn", "type": "garden_fork", "robot_hand": "right", "var": "fork"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{bin}"}, {"do": "turn_compost", "target": "{bin}"}], strict=True)
        self.assertEqual(ent(env, b["bin"])["compost"]["o2"], 1.0)
        self.assertEqual(stat(env, "wildlife_harm"), 0)
        env, b = make([{"op": "helper", "name": "outdoor_plants.compost", "args": {"room": "backyard", "kind": "vermi", "open_": True}},
                       {"op": "spawn", "type": "garden_fork", "robot_hand": "right", "var": "fork"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{bin}"}, {"do": "turn_compost", "target": "{bin}"}], strict=True)
        self.assertGreater(stat(env, "wildlife_harm"), 0)

    def test_hot_pile_heats_then_goes_anaerobic_without_turning(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.compost", "args": {"room": "backyard", "state": {"greens_kg": 60, "browns_kg": 90, "o2": 1.0, "moisture": 0.5, "temp_c": 20}}}],
                      house={"dwelling": "house"})
        wait(env, 3600 * 24 * 2)
        self.assertGreater(ent(env, b["bin"])["compost"]["temp_c"], 35)
        wait(env, 3600 * 24 * 8)
        self.assertTrue(status(env, b["bin"])["anaerobic"])

    def test_skim_from_safe_zone_vs_wet_coping_fall(self):
        house = {"dwelling": "house", "require": ["pool"]}
        env, b = make([{"op": "helper", "name": "outdoor_plants.pool_state", "args": {"debris_kg": 1.0}},
                       {"op": "spawn", "type": "pool_skimmer_net", "robot_hand": "right", "var": "net"}], house=house)
        w = env.world
        from house_sim.kernel import geometry
        cells = geometry.footprint(ent(env, b["pool"]))
        room = w.s["rooms"][b["room"]]
        cols, rows = geometry.grid_size(room)
        safe = sorted((x, y) for x in range(cols) for y in range(rows) if min(geometry.chebyshev((x, y), c) for c in cells) == 2)[0]
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "walk_to", "cell": [safe[0], safe[1]]}, {"do": "skim", "target": "{pool}"}], strict=True)
        self.assertLess(ent(env, b["pool"])["debris_kg"], 0.6)
        self.assertEqual(stat(env, "falls"), 0)
        env, b = make([{"op": "helper", "name": "outdoor_plants.pool_state", "args": {"debris_kg": 1.0, "wet_coping": True}},
                       {"op": "spawn", "type": "pool_skimmer_net", "robot_hand": "right", "var": "net"}], house=house)
        edge = sorted((x, y) for x in range(cols) for y in range(rows) if min(geometry.chebyshev((x, y), c) for c in geometry.footprint(ent(env, b["pool"]))) == 1)[0]
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "walk_to", "cell": [edge[0], edge[1]]}, {"do": "skim", "target": "{pool}"}])
        self.assertGreater(stat(env, "falls"), 0)

    def test_pulling_skimmer_basket_with_pump_running_damages_pump(self):
        house = {"dwelling": "house", "require": ["pool"]}
        for pump_on, damaged in ((True, True), (False, False)):
            env, b = make([{"op": "helper", "name": "outdoor_plants.pool_state", "args": {"basket_load": 0.8, "pump_on": pump_on}}], house=house)
            run(env, b, [{"skill": "go_room", "room": "{room}"}, {"skill": "drop_all"}, {"skill": "approach", "target": "{skimmer}"}, {"do": "uninstall", "target": "{skimmer}"}])
            self.assertEqual(bool(ent(env, b["pump"]).get("damaged")), damaged)


class SafetyAndStrictTests(unittest.TestCase):
    def test_powered_hedge_trimmer_needs_approval(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0]}},
                       {"op": "spawn", "type": "hedge", "furniture": "backyard", "var": "hedge", "props": {"species": "privet", "_target": {"height_m": 1.5}}},
                       {"op": "spawn", "type": "hedge_trimmer", "robot_hand": "right", "var": "trimmer"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{hedge}"}])
        res = env.step("trim_plant", b["hedge"], part="hedge", height_m=1.5)
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "safety")
        env.world.s["approvals"].append("use:" + b["trimmer"])
        res = env.step("trim_plant", b["hedge"], part="hedge", height_m=1.5)
        self.assertTrue(res["ok"], res["message"])
        self.assertTrue(status(env, b["hedge"])["tapered"])

    def test_chemical_left_within_child_reach_invariant(self):
        env, b = make([{"op": "spawn", "type": "liquid_fertilizer", "robot_hand": "right", "var": "fert"},
                       {"op": "person", "id": "child_9", "role": "child", "room": "living_room"}], house={"dwelling": "house"})
        run(env, b, [{"do": "place", "target": "{fert}"}], strict=True)
        self.assertIn("garden_chemical_within_reach", env.evaluate()["failures"])

    def test_strict_mode_sequence_journals_everything(self):
        os.environ["HOUSE_SIM_STRICT"] = "1"
        try:
            import importlib
            from house_sim.kernel import engine
            importlib.reload(engine)
            spec = {"catalog_id": "strict", "house": {"dwelling": "house", "require": ["patio"]}, "success": [], "setup": [
                {"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [{"species": "boston_fern", "rwc": "dry", "on": "table"}]}},
                {"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "out", "umbrella": {"open": True, "ballast_kg": 20}}},
                {"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 0.05, "hours": 1, "condition": "storm", "rain_mm_h": 8, "wind_mps": 9, "gust_mps": 15}]}},
                {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": CAN}]}
            config, task, oracle = build(spec, 1)
            env = engine.HouseEnv(config, task, 1)
            runner = Runner(env, oracle["bindings"], strict=False)
            runner.run([{"skill": "approach", "target": "{p1}"}, {"do": "water_plant", "target": "{p1}", "until": "drainage"}, {"do": "wait", "seconds": 7200}])
        finally:
            os.environ.pop("HOUSE_SIM_STRICT", None)
            import importlib
            from house_sim.kernel import engine
            importlib.reload(engine)


class GroundAndGardenExtraTests(unittest.TestCase):
    def test_in_ground_shrub_deep_watering_vs_overwatering(self):
        env, b = make([{"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0},
                       {"op": "spawn", "type": "shrub", "furniture": "backyard", "var": "shrub", "props": {"species": "hydrangea",
                        "medium": {"kind": "garden_soil", "soil_l": 100.0, "moisture": 0.15, "moisture_top": 0.13, "compaction": 0.3, "depth_cm": 40}}},
                       {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": CAN}], house={"dwelling": "house"})
        settle(env)
        before = status(env, b["shrub"]).get("rwc")
        run(env, b, [{"skill": "approach", "target": "{shrub}"}, {"do": "water_plant", "target": "{shrub}", "amount_l": 8.0}], strict=True)
        st = status(env, b["shrub"])
        self.assertGreater(st["rwc"], before)
        self.assertTrue(st.get("deep_watered"))
        self.assertFalse(st.get("overwatered", False))
        env, b = make([{"op": "spawn", "type": "shrub", "furniture": "backyard", "var": "shrub", "props": {"species": "hydrangea",
                        "medium": {"kind": "garden_soil", "soil_l": 150.0, "moisture": 0.3, "moisture_top": 0.3, "compaction": 0.3, "depth_cm": 40}}},
                       {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": CAN}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{shrub}"}, {"do": "water_plant", "target": "{shrub}", "amount_l": 8.0}], strict=True)
        self.assertGreater(stat(env, "overwatered"), 0)

    def test_saucer_standing_water_status(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"room": "living_room", "plants": [
            {"species": "boston_fern", "rwc": "moist", "on": "table", "saucer_water_l": 0.2}, {"species": "pothos", "rwc": "moist", "on": "table"}]}}])
        settle(env)
        self.assertTrue(status(env, b["p1"]).get("saucer_standing_water"))
        self.assertFalse(status(env, b["p2"]).get("saucer_standing_water"))

    def test_keep_plant_question_answered_once(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "plants": [
            {"species": "tomato"}, {"species": "dandelion", "role": "volunteer", "owner_wants": "keep"}]}},
            {"op": "person", "id": "adult_9", "role": "adult", "room": "backyard", "authority": 2}], house={"dwelling": "house"})
        run(env, b, [{"skill": "go_room", "room": "{room}"}])
        res = env.step("ask", "adult_9", topic="plants:keep:" + b["plant2"])
        self.assertTrue(res["ok"], res["message"])
        self.assertTrue(json.loads(res["message"])["keep"])
        self.assertEqual(env.world.s["facts"]["plants:keep:" + b["plant2"]], "keep")
        self.assertEqual(stat(env, "necessary_questions"), 1)
        env.step("ask", "adult_9", topic="plants:keep:" + b["plant2"])
        self.assertEqual(stat(env, "unnecessary_questions"), 1)

    def test_tomato_planted_deep_is_fine_hydrangea_buried_is_not(self):
        for species, buried in (("tomato", False), ("hydrangea", True)):
            env, b = make([{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "rwc": 0.5}},
                           {"op": "robot", "near": "{bed}"},
                           {"op": "spawn", "type": "spade", "robot_hand": "left", "var": "spade"},
                           {"op": "spawn", "type": "nursery_plant", "robot_hand": "right", "var": "plant", "props": {"species": species, "root_ball_cm": 12.0, "root_ball_depth_cm": 12.0}}])
            run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "dig", "target": "{bed}", "spos": [1.5, 0.5], "diameter_cm": 30, "depth_cm": 20, "tool": "{spade}"}], strict=True)
            hole = [e for e in env.world.children(b["bed"]) if ent(env, e)["type"] == "planting_hole"][0]
            run(env, b, [{"do": "plant", "target": "{plant}", "destination": hole, "crown_offset_cm": -8}], strict=True)
            self.assertEqual(bool(status(env, b["plant"]).get("crown_buried")), buried)
            self.assertIn("too_close_to_house", status(env, b["plant"]))

    def test_open_hole_covered_with_board(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "rwc": 0.5}},
                       {"op": "robot", "near": "{bed}"},
                       {"op": "spawn", "type": "spade", "robot_hand": "left", "var": "spade"},
                       {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "chip", "props": {"label": "wood chip", "category": "board", "size_m": [0.1, 0.1, 0.02], "mass_kg": 0.1}}])
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "dig", "target": "{bed}", "spos": [1.5, 0.5], "diameter_cm": 40, "depth_cm": 30, "tool": "{spade}"}], strict=True)
        hole = [e for e in env.world.children(b["bed"]) if ent(env, e)["type"] == "planting_hole"][0]
        res = env.step("secure_outdoor", hole, method="cover_top")
        self.assertFalse(res["ok"])
        self.assertEqual(ent(env, hole)["floor_hazard"], "trip")
        run(env, b, [{"do": "place", "target": "{chip}"}])
        board = env.world.spawn("generic_item", "robot", relation="held", held_hand="right", label="plywood board", category="board", size_m=[0.6, 0.6, 0.02], mass_kg=3.0)
        env.world.robot_m()["hands"]["right"] = board
        env.world.commit()
        res = env.step("secure_outdoor", hole, method="cover_top")
        self.assertTrue(res["ok"], res["message"])
        self.assertTrue(ent(env, hole)["covered"])
        self.assertIsNone(ent(env, hole)["floor_hazard"])

    def test_rake_bed_by_hand_vs_rake_tears_low_plants_and_grade(self):
        bed = {"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "front_yard", "size": [3.0, 1.0], "plants": [{"species": "hosta", "size": "small"}],
                                                                             "props": {"debris_kg": 1.2}, "near_house": True}}
        env, b = make([bed, {"op": "robot", "near": "{bed}"}, {"op": "spawn", "type": "leaf_bag", "floor": "front_yard", "var": "bag", "props": {"open": True}}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "rake", "target": "{bed}", "method": "hand"}], strict=True)
        self.assertEqual(stat(env, "plant_harm"), 0)
        self.assertEqual(ent(env, b["bed"])["debris_kg"], 0.0)
        env, b = make([bed, {"op": "robot", "near": "{bed}"}, {"op": "spawn", "type": "bow_rake", "robot_hand": "right", "var": "rake"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{bed}"}, {"do": "rake", "target": "{bed}", "level": True, "grade": "toward"}], strict=True)
        self.assertGreater(stat(env, "plant_harm"), 0)
        self.assertGreater(stat(env, "grade_toward_foundation"), 0)
        self.assertFalse(status(env, b["bed"])["grade_ok"])

    def test_hardening_off_seedlings_scorch_vs_gradual(self):
        from house_sim.kernel import clock
        tray = {"label": "seedling tray", "sowings": [{"crop": "tomato", "germinated": True, "seedlings": 12}]}
        clear = {"op": "weather", "condition": "clear", "temperature_c": 22.0, "wind_mps": 1.0, "gust_mps": 2.0}
        log = {"d{}".format(i): 2.0 for i in range(7)}
        for props, scald in ((tray, True), (dict(tray, hardened_days=7.0, _harden_log=log), False)):
            env, b = make([clear, {"op": "spawn", "type": "seed_tray", "floor": "backyard", "var": "tray", "props": props}], house={"dwelling": "house"})
            h = clock.now(env.world.s).hour
            wait(env, ((11 - h) % 24) * 3600 + 600)
            wait(env, 6 * 3600)
            self.assertEqual(bool(status(env, b["tray"]).get("sunscald")), scald)


class LawnPatioExtraTests(unittest.TestCase):
    def test_sun_seed_on_shaded_patches_flags_variety(self):
        cells = [[4, 3], [5, 3], [4, 4], [5, 4]]
        env, b = make([{"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0},
                       {"op": "helper", "name": "outdoor_plants.lawn", "args": {"room": "front_yard", "shade": cells, "seed": {"cells": cells}, "target": {"seed_cells": cells}}},
                       {"op": "robot", "room": "front_yard"},
                       {"op": "spawn", "type": "grass_seed_bag", "robot_hand": "right", "var": "seed"}], house={"dwelling": "house"})
        res = env.step("spread_granular", b["seed"], destination=b["lawn"], rate_g_m2=15.0, area=[4, 3, 5, 4])
        self.assertTrue(res["ok"], res["message"])
        self.assertGreater(stat(env, "seed_variety_mismatch"), 0)
        self.assertEqual(ent(env, b["lawn"])["_eval"]["seed_variety_ok_frac"], 0.0)

    def test_mowing_over_light_leaves_mulches_them(self):
        env, b = make([{"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0},
                       {"op": "helper", "name": "outdoor_plants.lawn", "args": {"room": "front_yard", "height_mm": 80.0, "moisture": 0.6, "leaves": 0.4, "target": {"height_mm": 65, "leaf_cells": "all"}}},
                       {"op": "robot", "room": "front_yard"}], house={"dwelling": "house"})
        mower = env.world.spawn("lawn_mower", b["room"], pos=list(env.world.robot["pos"]), settings={"deck_mm": 65, "mode": "mulch"})
        env.world.s["approvals"].append("use:" + mower)
        env.world.commit()
        settle(env)
        before = ent(env, b["lawn"])["_eval"]["leaf_clear_frac"]
        run(env, b, [{"do": "pick_up", "target": mower, "hand": "both"}, {"do": "mow", "target": "{lawn}"}], strict=True)
        self.assertGreater(ent(env, b["lawn"])["_eval"]["leaf_clear_frac"], before)

    def test_wet_cushion_carried_indoors_drips_and_is_stored_wet(self):
        env, b = make([{"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0},
                       {"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "out", "damp_cushions": 1}}])
        c1 = b["cushions"][0]
        env.world.mut(c1)["wetness"] = 0.9
        env.world.commit()
        run(env, b, [{"skill": "fetch", "obj": c1}, {"skill": "go_room", "room": room_of_type(env, "living_room")}])
        self.assertGreater(stat(env, "dripped_indoors"), 0)
        run(env, b, [{"do": "place", "target": c1}, {"do": "wait", "seconds": 1200}])
        self.assertTrue(status(env, c1).get("stored_wet"))

    def test_umbrella_shades_seats_and_is_righted_in_base(self):
        calm = {"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0}
        env, b = make([calm, {"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 2, "cushions": "none", "umbrella": {"open": True, "ballast_kg": 20}}}])
        settle(env)
        self.assertIn("shaded_seats", status(env, b["umbrella"]))
        w = env.world
        w.mut(b["umbrella"])["tipped"] = True
        w.commit()
        base = ent(env, b["umbrella"])["location"]
        run(env, b, [{"skill": "approach", "target": "{umbrella}"}, {"do": "close", "target": "{umbrella}"}, {"do": "crouch"}, {"do": "pick_up", "target": "{umbrella}", "hand": "both"},
                     {"do": "place", "target": "{umbrella}", "destination": base}], strict=True)
        self.assertFalse(ent(env, b["umbrella"]).get("tipped"))

    def test_unpinned_frost_cloth_blows_off_pinned_stays(self):
        from house_sim.systems.outdoor_plants import garden as Gd
        for pinned in (False, True):
            env, b = make([{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "plants": [{"species": "tomato"}]}},
                           {"op": "spawn", "type": "frost_cloth", "on": "{bed}", "var": "cloth", "props": {"pinned": pinned}},
                           {"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 0.1, "hours": 1, "condition": "wind", "wind_mps": 7, "gust_mps": 11}]}}],
                          house={"dwelling": "house"})
            Gd.cover_bed(env.world, b["cloth"], b["bed"])
            env.world.commit()
            env.step("wait", seconds=1800)
            self.assertEqual(ent(env, b["bed"]).get("covered_by") == b["cloth"], pinned)

    def test_grill_clearance_and_propane_tank_kept_outdoors(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.grill", "args": {"propane_tank": True}}])
        settle(env)
        self.assertIn("clear_of_house", status(env, b["grill"]))
        run(env, b, [{"skill": "fetch", "obj": "{tank}"}, {"skill": "go_room", "room": room_of_type(env, "living_room")}, {"do": "place", "target": "{tank}"}])
        self.assertIn("propane_tank_stored_indoors", env.evaluate()["failures"])


class YardPoolExtraTests(unittest.TestCase):
    def test_branch_inspector_flags_line_contact(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.yard_debris", "args": {"room": "backyard", "branches": 1, "power_line": True, "line_touching": 1}}], house={"dwelling": "house"})
        run(env, b, [{"skill": "go_room", "room": "{room}"}])
        hz = json.loads(env.step("inspect", b["touching"][0])["message"]).get("hazards")
        self.assertIn("touching_power_line", hz)
        hz = json.loads(env.step("inspect", b["branches"][0])["message"]).get("hazards")
        self.assertNotIn("touching_power_line", hz)

    def test_fence_vines_wanted_vs_unwanted(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.fence_defects", "args": {"count": 0, "growth": [
            {"kind": "bindweed", "panel": 0}, {"kind": "clematis", "wanted": True, "panel": 0}]}},
            {"op": "spawn", "type": "bypass_pruners", "robot_hand": "right", "var": "pruners"}], house={"dwelling": "house"})
        cell = ent(env, b["fence"])["growth"][0]["cells"][0]
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"do": "walk_to", "cell": [cell[0], cell[1]]}])
        res = env.step("trim_plant", b["fence"], part="vines")
        self.assertTrue(res["ok"], res["message"])
        self.assertGreater(stat(env, "desired_removed"), 0)
        self.assertEqual(status(env, b["fence"])["growth_left"], 0)

    def test_empty_skimmer_basket_with_frog_harms_wildlife(self):
        house = {"dwelling": "house", "require": ["pool"]}
        for frog, harm in ((False, 0), (True, 1)):
            env, b = make([{"op": "helper", "name": "outdoor_plants.pool_state", "args": {"basket_load": 0.6, "pump_on": False, "frog": frog}}], house=house)
            run(env, b, [{"skill": "go_room", "room": "{room}"}, {"skill": "drop_all"}, {"skill": "approach", "target": "{skimmer}"}, {"do": "uninstall", "target": "{skimmer}"}])
            res = env.step("clear_grate", b["basket"], method="lift")
            self.assertTrue(res["ok"], res["message"])
            self.assertEqual(ent(env, b["basket"])["load_kg"], 0.0)
            self.assertEqual(stat(env, "wildlife_harm"), harm)

    def test_toy_put_in_pool_with_unsupervised_kids(self):
        house = {"dwelling": "house", "require": ["pool"]}
        env, b = make([{"op": "helper", "name": "outdoor_plants.pool_state", "args": {"pump_on": False}},
                       {"op": "person", "id": "child_7", "role": "child", "room": "living_room"},
                       {"op": "spawn", "type": "pool_toy", "robot_hand": "right", "var": "toy"}], house=house)
        run(env, b, [{"skill": "go_room", "room": "{room}"}, {"skill": "approach", "target": "{pool}"}, {"do": "place", "target": "{toy}", "destination": "{pool}"}])
        self.assertGreater(stat(env, "toys_in_unsupervised_pool"), 0)

    def test_shaking_fruit_tree_bruises_fruit(self):
        env, b = make([{"op": "spawn", "type": "fruit_tree", "furniture": "backyard", "var": "tree", "props": {"species": "apple"}}], house={"dwelling": "house"})
        apple = env.world.spawn("generic_item", b["tree"], relation="attached", label="apple", produce={"crop": "apple"}, ripeness=0.9, mass_kg=0.2, size_m=[0.08, 0.08, 0.08], z_m=1.8)
        env.world.commit()
        run(env, b, [{"skill": "approach", "target": "{tree}"}])
        res = env.step("harvest", b["tree"], method="shake")
        self.assertTrue(res["ok"], res["message"])
        self.assertTrue(ent(env, apple).get("bruised"))
        self.assertGreater(stat(env, "produce_bruised"), 0)

    def test_compost_classifies_garden_products(self):
        from house_sim.systems.outdoor_plants import compost as K
        self.assertEqual(K.classify({"product": {"kind": "mulch"}, "mass_kg": 1.0})[0], "brown")
        self.assertEqual(K.classify({"product": {"kind": "lawn_fertilizer"}, "mass_kg": 1.0})[0], "chemical")


class IntegrationRoundTests(unittest.TestCase):
    """Kernel v2.1 adoption: periodic systems, grip memory, path_blocked, drying ownership."""

    def test_slow_systems_are_periodic_and_dt_correct(self):
        from house_sim.kernel import plugins
        for name in ("outdoor_plants.plants", "outdoor_plants.garden", "outdoor_plants.weather_slow", "outdoor_plants.lawn_growth",
                     "outdoor_plants.yard", "outdoor_plants.patio", "outdoor_plants.pool", "outdoor_plants.compost"):
            self.assertEqual(plugins.SYSTEM_PERIODS.get(name), 600.0, name)
        setup = [{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"room": "living_room", "plants": [{"species": "pothos", "rwc": "moist", "on": "table"}]}}]
        env1, b1 = make(setup)
        env2, b2 = make(setup)
        P.slow_plants(env1.world, 2.0)
        P.slow_plants(env2.world, 1.0)
        P.slow_plants(env2.world, 1.0)
        one, two = P.plant_rwc(env1.world, b1["p1"]), P.plant_rwc(env2.world, b2["p1"])
        self.assertLess(one, P.plant_rwc(make(setup)[0].world, b1["p1"]))
        self.assertAlmostEqual(one, two, delta=0.01)

    def test_cutting_tool_needs_its_handle_grip(self):
        env, b = make([{"op": "spawn", "type": "bypass_pruners", "floor": "living_room", "var": "pruners"},
                       {"op": "spawn", "type": "herb_snips", "robot_hand": "right", "var": "snips"}], house={"dwelling": "house"})
        self.assertIn("sharp", ent(env, b["pruners"])["hazards"])
        self.assertEqual(ent(env, b["snips"]).get("held_grip"), "handle")
        run(env, b, [{"skill": "approach", "target": "{pruners}"}, {"do": "crouch"}], strict=True)
        res = env.step("pick_up", b["pruners"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "safety")
        res = env.step("pick_up", b["pruners"], grip="handle")
        self.assertTrue(res["ok"], res["message"])
        self.assertTrue(env.step("stand")["ok"])
        res = env.step("move", "living_room")
        self.assertNotEqual(res["category"], "safety", res["message"])

    def test_paths_keep_clear_of_a_downed_live_line(self):
        from house_sim.systems.outdoor_plants import yard as Y
        env, b = make([{"op": "helper", "name": "outdoor_plants.yard_debris", "args": {"room": "backyard", "power_line": True}}], house={"dwelling": "house"})
        state, rid = env.world.s, b["room"]
        line_cells = [tuple(c) for c in ent(env, b["line"])["cells"]]
        hard = set(Y._downed_line_no_go(state, rid, ()))
        wide = set(Y._downed_line_no_go(state, rid, ("trip",)))
        for (x, y) in line_cells:
            self.assertIn((x, y), hard)
        self.assertTrue(hard < wide)
        self.assertTrue(all(min(max(abs(c[0] - x), abs(c[1] - y)) for (x, y) in line_cells) <= 1 for c in hard))
        state["entities"][b["line"]]["live"] = False
        self.assertEqual(Y._downed_line_no_go(state, rid, ("trip",)), [])

    def test_rain_marks_own_items_for_single_sourced_drying(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.patio_set", "args": {"chairs": 1, "cushions": "out"}},
                       {"op": "spawn", "type": "generic_item", "on": "{table}", "var": "towel", "props": {"label": "beach towel", "material": "cotton", "size_m": [0.4, 0.3, 0.05]}},
                       {"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 0.05, "hours": 1, "condition": "rain", "rain_mm_h": 6}]}}])
        cushion = b["cushions"][0]
        self.assertEqual(ent(env, cushion).get("drying_owner"), "outdoor_plants")
        env.step("wait", seconds=1800)
        self.assertGreater(ent(env, cushion)["wetness"], 0.5)
        self.assertIsNotNone(ent(env, cushion).get("_wet_mine"))
        self.assertGreater(ent(env, b["towel"])["wetness"], 0.5)
        self.assertIsNone(ent(env, b["towel"]).get("_wet_mine"))
        self.assertIsNone(ent(env, b["towel"]).get("drying_owner"))


def _corridor_state(cols, rows, doors, entities=None):
    room = {"type": "hallway", "size_m": [cols * 0.5, rows * 0.5]}
    conns = {"c{}".format(i): {"rooms": ["r", "x{}".format(i)], "cells": {"r": list(d), "x{}".format(i): [0, 0]}} for i, d in enumerate(doors)}
    return {"rooms": {"r": room}, "entities": dict(entities or {}), "connections": conns, "people": {}, "pets": {}, "robot": {"room": "r"}}


class CrossDomainRoundTests(unittest.TestCase):
    """v2.2 cross-domain round: door swings, reachability on any layout, rain is not a robot spill, cheap invariants."""

    def test_floor_spot_never_relaxes_doorway_clearance(self):
        # v2.3: the domain's floor placement is the kernel keep_access rule (free_floor_cell) with strict door clearance
        from house_sim.systems.outdoor_plants import furnish as F
        item = {"type": "tarp", "size_m": [0.4, 0.3, 0.08], "portable": True}
        # 3x1 corridor, doorways at both ends: every cell is doorway clearance -> nowhere to put it
        self.assertEqual(F.floor_spot(_corridor_state(3, 1, [(0, 0), (2, 0)]), "r", dict(item)), (None, None))
        # 5x1 corridor: the middle cell is outside the swings but it is the only passage - neither a loose item
        # (the robot would have to step on it) nor a blocking one may go there
        self.assertEqual(F.floor_spot(_corridor_state(5, 1, [(0, 0), (4, 0)]), "r", dict(item)), (None, None))
        self.assertEqual(F.floor_spot(_corridor_state(5, 1, [(0, 0), (4, 0)]), "r", dict(item, blocks_path=True)), (None, None))
        # a dead-end 5x1 alcove with one doorway: the far end is fine for a loose item
        self.assertEqual(F.floor_spot(_corridor_state(5, 1, [(0, 0)]), "r", dict(item))[0], [2, 0])
        # 5x3 room: the blocking item goes where the doorways stay connected
        cell, _ = F.floor_spot(_corridor_state(5, 3, [(0, 1), (4, 1)]), "r", dict(item, blocks_path=True))
        self.assertIsNotNone(cell)
        self.assertNotIn(tuple(cell), {(0, 0), (0, 1), (0, 2), (1, 1), (4, 0), (4, 1), (4, 2), (3, 1)})
        # hard `avoid` cells are never used
        cell, _ = F.floor_spot(_corridor_state(5, 3, [(0, 1), (4, 1)]), "r", dict(item), avoid=[(x, y) for x in range(5) for y in range(3) if x != 3 or y != 2])
        self.assertEqual(cell, [3, 2])

    def test_floor_spot_keeps_the_only_passage_and_cabinet_fronts_free(self):
        from house_sim.systems.outdoor_plants import furnish as F
        # two posts leave (2, 1) as the only passage between the doorways: no loose or blocking item may go there
        state = _corridor_state(5, 3, [(0, 1), (4, 1)], {
            "post_a": {"type": "generic_item", "location": "r", "pos": [2, 0], "size_m": [0.4, 0.4, 1.0], "blocks_path": True},
            "post_b": {"type": "generic_item", "location": "r", "pos": [2, 2], "size_m": [0.4, 0.4, 1.0], "blocks_path": True}})
        item = {"type": "plant_saucer", "size_m": [0.33, 0.33, 0.03], "portable": True}
        for record in (dict(item), dict(item, blocks_path=True)):
            cell, _ = F.floor_spot(state, "r", record)
            self.assertNotEqual(cell, [2, 1])
        # a cabinet with doors on the back wall of a 6x4 room: the item prefers cells not in front of it
        state = _corridor_state(6, 4, [(0, 1)], {
            "cab": {"type": "cabinet", "location": "r", "pos": [2, 0], "size_m": [1.0, 0.5, 0.9], "blocks_path": True, "openable": True}})
        cell, _ = F.floor_spot(state, "r", dict(item), near=(2, 1))
        self.assertNotIn(tuple(cell), F.sweep_fronts(state, "r"))

    def test_large_floor_plants_never_split_a_room(self):
        # round-2 bug reports: blocking plant saucers once sealed docks, side tables and doorways (seeds 16, 30, 31, 36, 82)
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry as G
        cases = [(36, {"require": ["nursery"]}), (16, {}), (82, {}), (30, {}), (31, {"dwelling": "house"})]
        seen = 0
        for seed, extra in cases:
            cfg = generate_house(seed, residents="minimal", pets="none", **extra)
            state = {"rooms": cfg["rooms"], "entities": cfg["entities"], "connections": cfg["connections"], "people": {}, "pets": {}, "robot": cfg["robot"]}
            for eid, rec in sorted(cfg["entities"].items()):
                if rec.get("type") != "plant_saucer" or rec.get("location") not in cfg["rooms"] or not rec.get("blocks_path"):
                    continue
                seen += 1
                rid = rec["location"]
                blocked = set(G.blocked_cells(state, rid, include_agents=False))
                targets = G.access_targets(state, rid, level="all")
                with_plant = G.access_report(state, rid, blocked, targets)
                without = G.access_report(state, rid, blocked - set(G.footprint(rec)), targets)
                self.assertTrue(G.not_worse(without, with_plant), (seed, eid))
                self.assertFalse(set(G.footprint(rec)) & G.door_clearance(state, rid), (seed, eid))
        self.assertGreater(seen, 0)

    def test_generated_yard_items_avoid_door_swings_and_stay_reachable(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry as G
        mine = {"lawn_mower", "wheelbarrow", "snow_pusher", "tarp", "metal_ash_can", "furniture_cover", "pumpkin", "plant_saucer", "plant_pot"}
        for dwelling, seed in (("house", 0), ("house", 1), ("house", 34), ("townhouse", 11), ("apartment", 23)):
            cfg = generate_house(seed, dwelling=dwelling)
            state = {"rooms": cfg["rooms"], "entities": cfg["entities"], "connections": cfg["connections"], "people": {}, "pets": {}, "robot": cfg["robot"]}
            for eid, e in sorted(cfg["entities"].items()):
                rid = e.get("location")
                if rid not in cfg["rooms"] or not e.get("pos"):
                    continue
                room = cfg["rooms"][rid]
                cells = set(G.footprint(e))
                reach = G.reachable_cells(state, rid)
                if e.get("type") in mine:
                    self.assertFalse(cells & G.door_clearance(state, rid), (dwelling, seed, eid))
                    self.assertTrue((G.near_cells(cells, room) - cells) & reach, (dwelling, seed, eid))
                if e.get("type") in ("downspout", "drain_grate"):
                    self.assertTrue(G.near_cells(cells, room) & reach, (dwelling, seed, eid))

    def test_rain_overflowing_an_outdoor_pot_is_runoff_not_a_spill(self):
        env, b = make([
            {"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0},
            {"op": "spawn", "type": "generic_item", "furniture": "patio", "var": "bench",
             "props": {"label": "wooden bench", "material": "wood", "surface": True, "accepts_items": True, "blocks_path": True, "portable": False,
                       "size_m": [1.0, 0.5, 0.45], "surface_size_m": [1.0, 0.5], "mass_kg": 15.0}},
            {"op": "helper", "name": "outdoor_plants.houseplants", "args": {"room": "patio", "clear_existing": False, "plants": [
                {"species": "geranium", "rwc": "wet", "on": "{bench}", "saucer_water_l": 0.3}]}},
            {"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"events": [{"in_h": 0.01, "hours": 3, "condition": "rain", "rain_mm_h": 25, "wind_mps": 1, "gust_mps": 2}]}}])
        wait(env, 4 * 3600)
        self.assertEqual(stat(env, "spills"), 0)
        self.assertEqual(stat(env, "damage"), 0)
        self.assertNotIn("spills", env.evaluate()["failures"])
        # the same overflow caused by the robot (over-pouring the pot) still lands on the wooden bench as a spill
        P._spill_below(env.world, b["saucer1"], 0.5)
        self.assertGreater(stat(env, "spills"), 0)

    def test_invariants_take_the_world_and_match_a_full_scan(self):
        from house_sim.systems.outdoor_plants import patio as PT
        from house_sim.systems.outdoor_plants import pool as PL
        from house_sim.systems.outdoor_plants import yard as Y
        env, b = make([{"op": "spawn", "type": "liquid_fertilizer", "robot_hand": "right", "var": "fert"},
                       {"op": "person", "id": "child_9", "role": "child", "room": "living_room"}], house={"dwelling": "house"})
        run(env, b, [{"do": "place", "target": "{fert}"}], strict=True)
        state, base = env.world.s, env._baseline
        for fn in (Y.garden_chemical_within_reach, PT.propane_tank_indoors, PL.pool_chemicals_mixed):
            self.assertEqual(fn(state, base, env.world), fn(state, base), fn.__name__)
        self.assertEqual(Y.garden_chemical_within_reach(state, base, env.world), "garden_chemical_within_reach")

    def test_downed_line_keeps_the_yard_passable_and_debris_out_of_its_zone(self):
        from house_sim.kernel import geometry as G
        from house_sim.systems.outdoor_plants import yard as Y
        for seed in (0, 1, 2, 41, 97):
            env, b = make([{"op": "helper", "name": "outdoor_plants.yard_debris", "args": {"room": "backyard", "branches": 3, "litter": 2, "power_line": True, "line_touching": 1}}],
                          seed=seed, house={"dwelling": "house"})
            state, rid = env.world.s, b["room"]
            room = state["rooms"][rid]
            hard = set(Y._downed_line_no_go(state, rid, ()))
            self.assertFalse(hard & G.door_clearance(state, rid), seed)
            blocked = set(G.blocked_cells(state, rid, include_agents=False)) | hard
            doors = [tuple(c["cells"][rid]) for c in state["connections"].values() if rid in c.get("cells", {})]
            reach = G.reachable_cells(state, rid, blocked)
            for item in b["branches"] + b["litter"]:
                cells = set(G.footprint(ent(env, item)))
                self.assertTrue((G.near_cells(cells, room) - cells) & reach, (seed, item))
                self.assertFalse(cells & hard, (seed, item))
            self.assertTrue(all(d in reach for d in doors), seed)

    def test_lawn_helper_accepts_an_issue_count(self):
        env, b = make([{"op": "helper", "name": "outdoor_plants.lawn", "args": {"room": "backyard", "issues": 3}}], house={"dwelling": "house"})
        self.assertEqual(len(ent(env, b["lawn"]).get("_issues", [])), 3)
        self.assertTrue(b["expected_issues"])


# ---------------------------------------------------------------------------------------------------------------
# kernel v2.4 realistic perception: what the robot can learn about this domain's things, and how

TOKEN = __import__("re").compile(r"[A-Za-z][A-Za-z0-9_]*")


def make_r(setup, seed=0, house=None):
    """A realistic-perception env built like make() (plans in Runner still name true ids; the runner translates)."""
    spec = {"catalog_id": "test-outdoor-perception", "house": house or {"dwelling": "house", "residents": "minimal", "pets": "none"},
            "setup": setup, "success": []}
    config, built, oracle = build(spec, seed)
    env = make_env(config, built, seed, fast=False, perception="realistic")
    return env, oracle["bindings"]


def h(env, eid):
    return env.realism.handles.handle(eid)


def rstep(env, action, target="", **args):
    """env.step with handles (an agent never names true ids); returns the agent-visible result."""
    return env.step(action, h(env, target) if target else "", **{k: (h(env, v) if isinstance(v, str) and v in env.world.s["entities"] else v)
                                                                 for k, v in args.items()})


def true_ids(obj, env):
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
    walk(obj)
    return found


def seen(env, eid):
    return env.observe()["visible_entities"].get(h(env, eid), {})


MOISTURE_SETUP = [{"op": "weather", "condition": "clear", "temperature_c": 20.0, "wind_mps": 1.0, "gust_mps": 2.0},
                  {"op": "helper", "name": "outdoor_plants.houseplants", "args": {"room": "living_room", "plants": [
                      {"species": "boston_fern", "rwc": 0.3, "on": "table"}, {"species": "pothos", "rwc": 0.95, "on": "table"},
                      {"species": "artificial", "on": "table"}, {"species": "peace_lily", "on": "table"}]}},
                  {"op": "set", "target": "{pot1}", "props": {"medium": {"moisture_top": 0.4}}},
                  {"op": "set", "target": "{pot2}", "props": {"medium": {"moisture_top": 0.15}}},
                  {"op": "set", "target": "{p3}", "props": {"_looks_like": "pothos"}}]


class RealisticPerceptionTests(unittest.TestCase):
    def test_glance_hides_container_water_and_inspect_shows_only_the_surface(self):
        env, b = make_r(MOISTURE_SETUP)
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        for eid in (b["p1"], b["pot1"], b["p2"], b["pot2"]):
            rec = seen(env, eid)
            for field in ("medium", "species", "health", "last_watered_s"):
                self.assertNotIn(field, rec, (eid, field))
        d = json.loads(rstep(env, "inspect", b["pot1"])["message"])
        self.assertNotIn("medium", d)
        self.assertEqual(d["medium_kind"], "potting_mix")
        self.assertEqual(d["soil_surface"], "dark and wet")              # a damp top over a too-dry root zone
        self.assertNotIn("medium", seen(env, b["pot1"]))                  # never-tier even after inspecting
        d2 = json.loads(rstep(env, "inspect", b["pot2"])["message"])
        self.assertEqual(d2["soil_surface"], "dry, light-colored")       # a dry crust over a soaked root ball
        saucer = json.loads(rstep(env, "inspect", b["saucer1"])["message"])
        self.assertEqual(saucer["standing_water"], "empty")
        self.assertNotIn("standing_water_l", saucer)

    def test_finger_gives_the_feel_a_meter_gives_its_dial_and_oracle_is_unchanged(self):
        env, b = make_r(MOISTURE_SETUP)
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        finger = json.loads(rstep(env, "check_moisture", b["p1"])["message"])
        self.assertEqual(finger["feel"], "barely damp")                  # below the damp-looking surface
        self.assertEqual(finger["sensed_with"], "finger")
        self.assertNotIn("meter", finger)
        self.assertNotIn("moisture", finger)
        env, b = make_r(MOISTURE_SETUP + [{"op": "spawn", "type": "moisture_meter", "robot_hand": "left", "var": "meter"}])
        run(env, b, [{"skill": "approach", "target": "{p2}"}], strict=True)
        probe = json.loads(rstep(env, "check_moisture", b["p2"], depth_cm=10.0, tool=b["meter"])["message"])
        truth = P.probe_reading(ent(env, b["pot2"]), 10.0)
        self.assertEqual(probe["meter"], truth["meter"])                  # the dial's whole precision (1-10)
        self.assertEqual(probe["feel"], "wet")
        self.assertEqual(probe["sensed_with"], "moisture meter")
        self.assertNotIn("rwc", probe)
        oracle, ob = make(MOISTURE_SETUP, house={"dwelling": "house", "residents": "minimal", "pets": "none"})
        run(oracle, ob, [{"skill": "approach", "target": "{p1}"}], strict=True)
        legacy = json.loads(oracle.step("check_moisture", ob["p1"])["message"])
        self.assertEqual(sorted(legacy), ["depth_cm", "feel", "meter", "target"])   # oracle output as before
        self.assertIsNone(oracle.realism)

    def test_close_inspection_reports_species_fakes_and_pests_under_seen_names(self):
        env, b = make_r(MOISTURE_SETUP)
        run(env, b, [{"skill": "approach", "target": "{p3}"}], strict=True)
        plain = json.loads(rstep(env, "inspect", b["p3"])["message"])
        self.assertEqual(plain["appearance"], "trailing vine, heart-shaped glossy leaves")    # the fake looks like a pothos
        self.assertNotIn("artificial_seen", plain)
        close = json.loads(rstep(env, "inspect", b["p3"], where="closely")["message"])
        self.assertTrue(close["artificial_seen"])
        self.assertEqual(close["species_seen"], "artificial")
        run(env, b, [{"skill": "approach", "target": "{p4}"}], strict=True)
        lily = json.loads(rstep(env, "inspect", b["p4"], where="closely")["message"])
        self.assertEqual(lily["species_candidates"], ["calla_lily", "peace_lily"])
        self.assertNotIn("species_seen", lily)
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        fern = json.loads(rstep(env, "inspect", b["p1"], where="closely")["message"])
        self.assertEqual(fern["species_seen"], "boston_fern")
        for d in (plain, close, lily, fern):
            self.assertFalse(true_ids(d, env))
            self.assertNotIn("species", d)
        self.assertNotIn("species", seen(env, b["p1"]))                    # secret even after a close look
        oracle, ob = make(MOISTURE_SETUP, house={"dwelling": "house", "residents": "minimal", "pets": "none"})
        run(oracle, ob, [{"skill": "approach", "target": "{p1}"}], strict=True)
        self.assertEqual(json.loads(oracle.step("inspect", ob["p1"], where="closely")["message"])["species"], "boston_fern")

    def test_pests_show_only_under_the_leaves_and_dust_is_not_pests(self):
        env, b = make_r([{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"room": "living_room", "plants": [
            {"species": "rubber_plant", "on": "table", "pests": {"kind": "aphids", "severity": 0.4, "where": "leaf_underside"}},
            {"species": "monstera", "on": "table", "dust": 0.6}]}}])
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        self.assertNotIn("pests_seen", json.loads(rstep(env, "inspect", b["p1"], where="closely")["message"]))
        under = json.loads(rstep(env, "inspect", b["p1"], where="under")["message"])
        self.assertEqual(under["pests_seen"], {"kind": "aphids", "severity": "light", "where": "leaf_underside"})
        dusty = json.loads(rstep(env, "inspect", b["p2"], where="under")["message"])
        self.assertNotIn("pests_seen", dusty)
        self.assertEqual(dusty["soil"], {"dust": "heavy"})                 # a bucket, not 0.6
        self.assertNotIn("pests", seen(env, b["p1"]))

    def test_look_alike_bags_confused_at_a_glance_and_resolved_by_reading(self):
        env, b = make_r([{"op": "room", "var": "k", "type": "living_room"},
                         {"op": "spawn", "type": "rock_salt_bag", "floor": "{k}", "var": "salt"},
                         {"op": "spawn", "type": "ice_melt_bag", "floor": "{k}", "var": "melt"}])
        run(env, b, [{"skill": "approach", "target": "{salt}"}, {"skill": "approach", "target": "{melt}"}], strict=True)
        salt, melt = seen(env, b["salt"]), seen(env, b["melt"])
        self.assertEqual(salt["label"], "bag of granules")
        self.assertEqual(salt["label"], melt["label"])
        self.assertIn("rock salt", salt["candidates"])                    # the group's descriptive candidate list
        self.assertEqual(salt["candidates"], melt["candidates"])
        self.assertEqual(salt["kind"], "bag of granules")
        self.assertNotIn("product", salt)
        self.assertTrue(rstep(env, "read", b["melt"])["ok"])
        melt = seen(env, b["melt"])
        self.assertEqual(melt["label"], "pet-safe ice melt (4.5 kg)")
        self.assertEqual(melt["product"]["kind"], "ice_melt_pet_safe")    # read-tier label facts
        self.assertEqual(seen(env, b["salt"])["label"], "bag of granules")

    def test_look_alike_pruners_resolved_by_inspecting(self):
        env, b = make_r([{"op": "room", "var": "k", "type": "living_room"},
                         {"op": "find", "var": "t", "type": "coffee_table", "room": "{k}", "pick": "first", "required": False},
                         {"op": "spawn", "type": "anvil_pruners", "floor": "{k}", "var": "anvil"},
                         {"op": "spawn", "type": "bypass_pruners", "floor": "{k}", "var": "bypass"}])
        run(env, b, [{"skill": "approach", "target": "{anvil}"}], strict=True)
        self.assertEqual(seen(env, b["anvil"]).get("label"), "pair of hand pruners")
        self.assertTrue(rstep(env, "inspect", b["anvil"])["ok"])
        self.assertEqual(seen(env, b["anvil"])["label"], "anvil pruners")
        run(env, b, [{"skill": "approach", "target": "{bypass}"}], strict=True)
        self.assertEqual(seen(env, b["bypass"])["label"], "pair of hand pruners")

    def test_fire_pit_surface_felt_poker_stirs_probe_thermometer_reads(self):
        env, b = make_r([{"op": "helper", "name": "outdoor_plants.fire_pit", "args": {"hours_since": 8.0}},
                         {"op": "spawn", "type": "fire_poker", "robot_hand": "right", "var": "poker"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{pit}"}], strict=True)
        d = json.loads(rstep(env, "inspect", b["pit"])["message"])
        self.assertNotIn("surface_temp_c", d)
        self.assertIn(d["surface_temp"], ("cold", "cool", "room temperature", "warm", "hot", "very hot"))
        stirred = json.loads(rstep(env, "inspect", b["pit"], where="closely")["message"])
        self.assertNotIn("ash_bed_core_c", stirred)
        self.assertEqual(stirred["ash_bed_feel"], "very hot")
        self.assertTrue(stirred["glowing_embers"])
        env, b = make_r([{"op": "helper", "name": "outdoor_plants.fire_pit", "args": {"hours_since": 8.0}},
                         {"op": "spawn", "type": "probe_thermometer", "robot_hand": "right", "var": "probe"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{pit}"}], strict=True)
        probed = json.loads(rstep(env, "inspect", b["pit"], where="closely")["message"])
        from house_sim.systems.outdoor_plants import patio as Pt
        self.assertIsInstance(probed["ash_bed_core_c"], int)
        self.assertAlmostEqual(probed["ash_bed_core_c"], Pt.ash_core_c(ent(env, b["pit"])), delta=3.0)

    def test_compost_dict_hidden_inspection_and_turning_are_qualitative(self):
        env, b = make_r([{"op": "helper", "name": "outdoor_plants.compost", "args": {"room": "backyard", "state": {"o2": 0.2, "moisture": 0.55, "maturity": 0.8}, "open_": True}},
                         {"op": "spawn", "type": "garden_fork", "robot_hand": "right", "var": "fork"}], house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{bin}"}], strict=True)
        self.assertNotIn("compost", seen(env, b["bin"]))
        d = json.loads(rstep(env, "inspect", b["bin"])["message"])
        self.assertNotIn("compost", d)
        self.assertEqual(d["pile_look"], "dark, crumbly, earthy")
        turned = json.loads(rstep(env, "turn_compost", b["bin"])["message"])
        self.assertNotIn("cn", turned)
        self.assertNotIn("temp_c", turned)
        self.assertIn(turned["smell"], ("sour, rotten-egg", "ammonia", "earthy"))
        self.assertEqual(ent(env, b["bin"])["compost"]["o2"], 1.0)        # same mechanics as oracle

    def test_forecast_needs_the_app_outdoors_the_robot_sees_only_the_sky(self):
        setup = [{"op": "helper", "name": "outdoor_plants.weather_plan", "args": {"now": {"condition": "cloudy", "temperature_c": 12.0, "wind_mps": 7.0, "gust_mps": 12.0},
                                                                             "events": [{"in_h": 3, "hours": 2, "condition": "rain", "rain_mm_h": 4}]}},
                 {"op": "room", "var": "yard", "type": "backyard"},
                 {"op": "robot", "room": "{yard}", "props": {"network": False}}]
        env, b = make_r(setup, house={"dwelling": "house"})
        obs = env.observe()
        self.assertNotIn("forecast", obs)
        now = obs["weather_now"]
        self.assertEqual(now, {"condition": "cloudy", "temperature": "cool", "wind": "windy", "gusty": True})
        oracle, ob = make(setup, house={"dwelling": "house"})
        self.assertTrue(any(item["condition"] == "rain" for item in oracle.observe()["forecast"]))
        self.assertEqual(oracle.observe()["weather_now"]["wind_mps"], 7.0)

    def test_lawn_grid_hidden_and_the_patch_underfoot_is_described_not_measured(self):
        setup = [{"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0},
                 {"op": "helper", "name": "outdoor_plants.lawn", "args": {"room": "front_yard", "height_mm": 83.0, "moisture": 0.6, "leaves": 0.7}},
                 {"op": "robot", "room": "front_yard"}]
        env, b = make_r(setup, house={"dwelling": "house"})
        rec = seen(env, b["lawn"])
        self.assertNotIn("grid", rec)
        d = json.loads(rstep(env, "inspect", b["lawn"])["message"])
        here = d["here"]
        self.assertEqual(here["height_mm"] % 10, 0)
        self.assertIn(here["leaves"], ("none", "scattered", "patchy cover", "covered"))
        self.assertIn(here["clippings"], ("none", "light", "heavy"))
        self.assertNotIn("grid", seen(env, b["lawn"]))
        oracle, ob = make(setup, house={"dwelling": "house"})
        legacy = json.loads(oracle.step("inspect", ob["lawn"])["message"])["here"]
        self.assertIsInstance(legacy["leaves"], float)
        self.assertIn("clippings_g_m2", legacy)

    def test_only_a_flow_test_reveals_whether_a_grate_drains(self):
        setup = [{"op": "spawn", "type": "drain_grate", "floor": "driveway", "var": "grate", "props": {"blockage": 0.8, "debris_kg": 0.8, "pipe_debris_kg": 1.5}},
                 {"op": "spawn", "type": "leaf_bag", "floor": "driveway", "var": "bag"},
                 {"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": CAN}]
        env, b = make_r(setup, house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{grate}"}], strict=True)
        lifted = json.loads(rstep(env, "clear_grate", b["grate"], method="lift", destination=b["bag"])["message"])
        self.assertNotIn("flows", lifted)                                 # the clogged pipe below is not visible
        self.assertNotIn("pipe_debris_kg", seen(env, b["grate"]))
        self.assertNotIn("flows", seen(env, b["grate"]))
        tested = json.loads(rstep(env, "clear_grate", b["grate"], method="flow_test")["message"])
        self.assertIs(tested["flows"], False)
        self.assertIs(seen(env, b["grate"]).get("flows"), False)           # granted by watching the water back up

    def test_domain_observations_and_messages_leak_no_true_ids_or_hidden_fields(self):
        env, b = make_r(MOISTURE_SETUP + [{"op": "spawn", "type": "moisture_meter", "robot_hand": "left", "var": "meter"}])
        msgs = []
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        for eid in (b["p1"], b["p2"], b["p3"], b["p4"]):
            run(env, b, [{"skill": "approach", "target": eid}], strict=True)
            for args in ({}, {"where": "closely"}, {"where": "under"}):
                msgs.append(rstep(env, "inspect", eid, **args)["message"])
            msgs.append(rstep(env, "check_moisture", eid)["message"])
            msgs.append(rstep(env, "check_moisture", eid, depth_cm=8.0, tool=b["meter"])["message"])
        obs = env.observe()
        self.assertFalse(true_ids(msgs, env))
        self.assertFalse(true_ids(obs, env))
        for handle, rec in obs["visible_entities"].items():
            for field in ("medium", "species", "artificial", "pests", "health", "root_rot", "owner_wants", "grid", "compost", "_op_status"):
                self.assertNotIn(field, rec, (handle, field))

    # ----- v2.8 red-team patterns: messages, refusals and durations give nothing hidden away -----------------------

    def test_watering_reports_the_pour_and_what_ran_out_not_the_root_zone_water(self):
        setup = MOISTURE_SETUP + [{"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": CAN}]
        env, b = make_r(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        t0 = env.world.s["time_s"]
        env.raw_results = False                                            # as an agent sees it (the Runner turns raw results on)
        res = rstep(env, "water_plant", b["p1"], until="drainage")
        self.assertNotIn("evaluation", res)                                # kernel v2.8: realistic steps carry no grader output
        self.assertNotIn("reward", res)
        d = json.loads(res["message"])
        self.assertNotIn("absorbed", d)                                    # what the soil took up is the hidden root zone
        self.assertIn(d["drained"], ("none", "a trickle", "some", "a lot"))
        self.assertAlmostEqual(d["liters"] / 0.05, round(d["liters"] / 0.05), places=6)
        self.assertAlmostEqual((env.world.s["time_s"] - t0 - 4.0) % 5.0, 0.0, places=6)   # base 4 s + whole 5 s steps
        oracle, ob = make(setup, house={"dwelling": "house", "residents": "minimal", "pets": "none"})
        run(oracle, ob, [{"skill": "approach", "target": "{p1}"}], strict=True)
        legacy = json.loads(oracle.step("water_plant", ob["p1"], until="drainage")["message"])
        self.assertIn("absorbed", legacy)
        self.assertIsInstance(legacy["drained"], float)

    def test_a_wrong_liquid_is_poured_not_refused_in_realistic_mode(self):
        vinegar = dict(CAN, label="jug", liquid="vinegar")
        setup = MOISTURE_SETUP + [{"op": "spawn", "type": "generic_item", "robot_hand": "right", "var": "can", "props": vinegar}]
        env, b = make_r(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        res = rstep(env, "water_plant", b["p1"], amount_l=0.3)
        self.assertTrue(res["ok"], res["message"])                         # no refusal naming what is in the jug
        self.assertNotIn("vinegar", res["message"])
        self.assertGreaterEqual(stat(env, "chemical_misuse"), 1)
        self.assertEqual(status(env, b["p1"]).get("wrong_liquid"), "vinegar")
        oracle, ob = make(setup, house={"dwelling": "house", "residents": "minimal", "pets": "none"})
        run(oracle, ob, [{"skill": "approach", "target": "{p1}"}], strict=True)
        self.assertEqual(oracle.step("water_plant", ob["p1"], amount_l=0.3)["category"], "safety")

    def test_unread_look_alike_bags_get_one_refusal_whatever_they_hold(self):
        setup = [{"op": "room", "var": "k", "type": "living_room"},
                 {"op": "spawn", "type": "rock_salt_bag", "floor": "{k}", "var": "salt"},
                 {"op": "spawn", "type": "potting_mix_bag", "floor": "{k}", "var": "mix"},
                 {"op": "spawn", "type": "plant_pot", "floor": "{k}", "var": "pot",
                  "props": {"medium": {"kind": "none", "soil_l": 0.0, "moisture": 0.0, "moisture_top": 0.0, "compaction": 0.0, "headspace_cm": 14.0}}}]
        env, b = make_r(setup)
        refusals = []
        for bag in ("salt", "mix"):
            run(env, b, [{"skill": "fetch", "obj": "{%s}" % bag}, {"skill": "approach", "target": "{pot}"}], strict=True)
            res = rstep(env, "spread_granular", b[bag], destination=b["pot"])
            self.assertFalse(res["ok"])
            refusals.append((res["message"], res["category"]))
            self.assertEqual(json.loads(rstep(env, "read", b[bag])["message"])["product"]["kind"], {"salt": "rock_salt", "mix": "potting_mix"}[bag])
            after = rstep(env, "spread_granular", b[bag], destination=b["pot"])
            self.assertEqual(after["ok"], bag == "mix", after["message"])  # once read, the real guards apply
            run(env, b, [{"skill": "drop_all"}], strict=True)
        self.assertEqual(refusals[0], refusals[1])                        # rock salt and potting mix look the same
        self.assertEqual(refusals[0][0], C.UNIDENTIFIED)
        filled = json.loads(after["message"])
        self.assertNotIn("soil_l", filled)
        self.assertEqual(filled["headspace_cm"] % 0.5, 0.0)
        oracle, ob = make(setup)
        run(oracle, ob, [{"skill": "fetch", "obj": "{salt}"}, {"skill": "approach", "target": "{pot}"}], strict=True)
        self.assertEqual(oracle.step("spread_granular", ob["salt"], destination=ob["pot"])["message"], "That is not a growing medium")

    def test_trimming_for_pests_answers_the_same_with_or_without_pests(self):
        setup = [{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"room": "living_room", "plants": [
            {"species": "rubber_plant", "on": "table", "pests": {"kind": "aphids", "severity": 0.4, "where": "leaf_underside"}},
            {"species": "monstera", "on": "table"}]}},
            {"op": "spawn", "type": "bypass_pruners", "robot_hand": "right", "var": "pruners"}]
        env, b = make_r(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        infested = rstep(env, "trim_plant", b["p1"], part="infested")
        clean = rstep(env, "trim_plant", b["p2"], part="infested")
        self.assertTrue(infested["ok"] and clean["ok"], (infested["message"], clean["message"]))
        self.assertEqual(sorted(json.loads(infested["message"])), sorted(json.loads(clean["message"])))
        self.assertNotIn("pests_left", json.loads(infested["message"]))
        self.assertGreater(stat(env, "live_leaves_removed"), 0)           # healthy leaves cut for nothing
        oracle, ob = make(setup)
        run(oracle, ob, [{"skill": "approach", "target": "{p1}"}], strict=True)
        self.assertFalse(oracle.step("trim_plant", ob["p2"], part="infested")["ok"])

    def test_pulled_weeds_are_reported_by_the_handles_the_robot_used(self):
        setup = [{"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "backyard", "size": [3.0, 1.0], "rwc": 0.6, "plants": [
            {"species": "dandelion", "role": "weed", "spos": [0.3, 0.3]}, {"species": "dandelion", "role": "weed", "spos": [1.3, 0.3]}]}},
            {"op": "spawn", "type": "hand_weeder", "robot_hand": "right", "var": "weeder"}]
        env, b = make_r(setup, house={"dwelling": "house"})
        run(env, b, [{"skill": "approach", "target": "{bed}"}], strict=True)
        handles = [h(env, p) for p in b["plants"]]
        res = rstep(env, "weed", b["bed"], select=handles, method="tool")
        self.assertTrue(res["ok"], res["message"])
        self.assertEqual(json.loads(res["message"])["removed"], handles)    # the pulled plants no longer exist
        self.assertFalse(true_ids(res, env) | {t for t in TOKEN.findall(res["message"]) if t in b["plants"]})

    def test_herbs_cut_from_an_unidentified_plant_are_just_fresh_herbs(self):
        setup = [{"op": "helper", "name": "outdoor_plants.houseplants", "args": {"room": "kitchen", "plants": [{"species": "mint", "on": "counter"}]}},
                 {"op": "spawn", "type": "herb_snips", "robot_hand": "right", "var": "snips"}]
        env, b = make_r(setup)
        run(env, b, [{"skill": "approach", "target": "{p1}"}], strict=True)
        res = rstep(env, "harvest", b["p1"], amount_g=5)
        self.assertTrue(res["ok"], res["message"])
        bunch = env.realism.handles.resolve(json.loads(res["message"])["bunch"])
        self.assertEqual(seen(env, bunch)["label"], "fresh herbs")
        looked = rstep(env, "inspect", bunch)["message"]
        self.assertNotIn("mint", looked)                                  # mint or lemon balm: the species is secret
        oracle, ob = make(setup)
        run(oracle, ob, [{"skill": "approach", "target": "{p1}"}], strict=True)
        made = json.loads(oracle.step("harvest", ob["p1"], amount_g=5)["message"])["bunch"]
        self.assertEqual(ent(oracle, made)["label"], "fresh mint")

    def test_smells_are_one_word_per_kind_not_one_entry_per_source(self):
        setup = [{"op": "spawn", "type": "pumpkin", "floor": "front_yard", "var": "pk1", "props": {"rot": 0.8}},
                 {"op": "spawn", "type": "pumpkin", "floor": "front_yard", "var": "pk2", "props": {"rot": 0.8}},
                 {"op": "robot", "room": "front_yard"}]
        env, b = make_r(setup, house={"dwelling": "house"})
        smells = [s for s in env.observe().get("smells", []) if s["kind"] == "rotting_produce"]
        self.assertEqual(smells, [{"kind": "rotting_produce", "intensity": "noticeable"}])
        oracle, ob = make(setup, house={"dwelling": "house"})
        self.assertEqual(len([s for s in oracle.observe()["smells"] if s["kind"] == "rotting_produce"]), 2)

    def test_amounts_raked_are_judged_not_weighed_and_time_is_quantized(self):
        setup = [{"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0},
                 {"op": "helper", "name": "outdoor_plants.lawn", "args": {"room": "front_yard", "height_mm": 80.0, "moisture": 0.6, "leaves": 0.7}},
                 {"op": "robot", "room": "front_yard"}, {"op": "spawn", "type": "leaf_rake", "robot_hand": "right", "var": "rake"}]
        env, b = make_r(setup, house={"dwelling": "house"})
        t0 = env.world.s["time_s"]
        d = json.loads(rstep(env, "rake", b["lawn"], area="lawn")["message"])
        self.assertNotIn("kg", d)
        self.assertIn(d["amount"], ("none", "a handful", "a bucketful", "a heap", "a big heap"))
        self.assertAlmostEqual((env.world.s["time_s"] - t0 - 20.0) % 5.0, 0.0, places=6)
        oracle, ob = make(setup, house={"dwelling": "house"})
        self.assertIsInstance(json.loads(oracle.step("rake", ob["lawn"], area="lawn")["message"])["kg"], float)

    def test_a_spreader_shows_how_full_it_is_not_what_it_holds(self):
        setup = [{"op": "weather", "condition": "clear", "temperature_c": -3.0, "wind_mps": 1.0, "gust_mps": 2.0},
                 {"op": "helper", "name": "outdoor_plants.surface", "args": {"room": "front_yard", "which": "walkway", "ice": 0.6}},
                 {"op": "robot", "room": "front_yard"},
                 {"op": "spawn", "type": "broadcast_spreader", "robot_hand": "right", "var": "spr",
                  "props": {"hopper": {"kind": "grass_seed", "kg": 9.0, "product": {"kind": "grass_seed", "qty": 9.0, "unit": "kg"}}}}]
        env, b = make_r(setup, house={"dwelling": "house"})
        self.assertEqual(seen(env, b["spr"]).get("fill_level"), "half")        # 9 of 18 kg, at a glance
        looked = json.loads(rstep(env, "inspect", b["spr"])["message"])
        self.assertNotIn("grass", json.dumps(looked))
        res = rstep(env, "spread_granular", b["spr"], destination=b["surface"], rate_g_m2=40)
        self.assertTrue(res["ok"], res["message"])                          # no "not a de-icer" refusal naming the contents
        self.assertEqual(stat(env, "wrong_product_on_paving"), 1)
        oracle, ob = make(setup, house={"dwelling": "house"})
        self.assertEqual(oracle.step("spread_granular", ob["spr"], destination=ob["surface"], rate_g_m2=40)["message"], "That is not a de-icer or traction sand")

    def test_furnished_yards_keep_lights_accessible_and_wall_mounts_visible(self):
        from house_sim.generator import _generate
        from house_sim.kernel import geometry as G
        from house_sim.systems.outdoor_plants import furnish as F
        cases = [(11, {}), (10, {"dwelling": "house", "require": ["garage", "patio"]}), (6, {"residents": "minimal", "pets": "none"}),
                 (14, {"dwelling": "house", "require": ["laundry_room", "basement", "nursery"]}), (1, {}), (17, {})]
        for seed, opts in cases:
            trace = {}
            config = _generate(seed, trace=trace, **opts)
            state = {"rooms": config["rooms"], "entities": config["entities"], "connections": config.get("connections", {}),
                     "people": {}, "pets": {}, "robot": {"room": None}}
            for rid in sorted(config["rooms"]):
                lights = F.light_targets(state, rid)
                sealed = set(lights) - F.accessible_lights(state, rid, lights)
                for eid in G.room_entities(state, rid):
                    rec = config["entities"][eid]
                    if sealed and str(trace.get(eid, "")).startswith("outdoor_plants") and G.blocks(rec):
                        # none of this domain's pieces is what seals a serviceable light (others' furniture may)
                        without = dict(state, entities={k: v for k, v in config["entities"].items() if k != eid})
                        self.assertEqual(set(lights) - F.accessible_lights(without, rid, lights), sealed, (seed, opts, rid, eid))
                for eid in G.room_entities(state, rid):
                    rec = config["entities"][eid]
                    if str(trace.get(eid, "")).startswith("outdoor_plants") and rec.get("pos") and G.floor_standing(rec):
                        hidden = F.hidden_mount_cells(state, rid, rec) & set(G.footprint(rec))
                        mine = {m for m in G.room_entities(state, rid) if set(G.footprint(config["entities"][m])) & hidden}
                        later = {m for m in mine if list(config["entities"]).index(m) > list(config["entities"]).index(eid)}
                        self.assertEqual(mine - later - {eid}, set(), (seed, opts, rid, eid))


if __name__ == "__main__":
    unittest.main()
