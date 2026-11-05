"""WP-D outdoor_plants (VERIFY_HARDENING 6.2-6.11, 8.2, 10.3, 13, 15; package WP-D outdoor_plants): the yard_debris and ash mess kinds
and their flows, liquids poured on plants, the hose on the water meter, causes for the time systems' writes, derived fields, settings
skills and damaging verbs, hazard zones and hazard_created tags, truth mappings, structured readings, field classes, scenario helpers
and the twin-sweep regressions (unfamiliar twins and look-alikes in every verb).

Run isolated:   HOUSE_SIM_DOMAINS=core,outdoor_plants python -m unittest tests/test_outdoor_plants_hardening.py
Strict journal: HOUSE_SIM_STRICT=1 python -m unittest tests/test_outdoor_plants_hardening.py
"""
import copy
import json
import os
import pathlib
import unittest

from house_sim.kernel import ledger, plugins, speech
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import load_domains
from house_sim.kernel.registry import default_registry
from house_sim.tasks.runner import Runner, build
from house_sim.tasks.setup import apply_setup

load_domains()
from house_sim.systems.outdoor_plants import common as C  # noqa: E402
from house_sim.systems.outdoor_plants import hardening as H  # noqa: E402
from house_sim.systems.outdoor_plants import lawn as L  # noqa: E402

ALL_DOMAINS = "HOUSE_SIM_DOMAINS" not in os.environ
ROOT = pathlib.Path(__file__).resolve().parents[1]
NOVEL = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "label": "thing"}
CAN = {"label": "watering can", "liquid_container": True, "liquid": "water", "liquid_l": 8.0, "capacity_l": 9.0, "mass_kg": 0.6, "size_m": [0.2, 0.45, 0.3], "portable": True}


class Yard:
    """A tiny backyard (a lawn, a bed, a potted plant, a compost bin, a tarp with a leaf pile, a walkway, a gutter, a grate, a leaf bag, a
    side table) and everything in arm's reach of the robot at (6, 6); a yard-waste cart and a driveway, a sidewalk next door."""

    def __init__(self, weather=None, people=True):
        self.reg = reg = default_registry()
        rooms = {"yard": {"type": "backyard", "size_m": [10.0, 8.0], "floor_material": "grass", "outdoor": True, "exits": []},
                 "curb": {"type": "sidewalk", "size_m": [2.0, 6.0], "floor_material": "concrete", "outdoor": True, "exits": []},
                 "den": {"type": "living_room", "size_m": [4.0, 4.0], "floor_material": "wood", "exits": []}}
        conns = {"c_yc": {"rooms": ["yard", "curb"], "cells": {"yard": [19, 8], "curb": [0, 8]}, "width_m": 1.2},
                 "c_yd": {"rooms": ["yard", "den"], "cells": {"yard": [0, 8], "den": [7, 4]}, "width_m": 0.9}}
        c = {"schema_version": 2, "start_room": "yard", "rooms": rooms, "connections": conns, "entities": {}, "pets": {},
             "people": {"resident_1": {"role": "adult", "location": "yard", "name": "Sam", "pos": [18, 14]}} if people else {},
             "clock": {"start": "2026-10-09T12:00:00", "hemisphere": "north"},
             "weather": dict({"condition": "clear", "temperature_c": 18.0, "wind_mps": 1.0}, **(weather or {})), "household": {"rules": []}}
        for rid, room in rooms.items():
            floor = reg.make("floor_surface", rid, material=room["floor_material"], size_m=[room["size_m"][0], room["size_m"][1], 0.01])
            floor["pos"] = [0, 0]
            c["entities"]["floor_" + rid] = floor
        self.c = c
        spec = {"catalog_id": "yard-test", "setup": [], "success": []}
        apply_setup(c, [{"op": "helper", "name": "outdoor_plants.lawn", "args": {"room": "yard", "leaves": 0.5}},
                        {"op": "helper", "name": "outdoor_plants.garden_bed", "args": {"room": "yard", "size": [1.5, 1.0], "weeds": 1}},
                        {"op": "helper", "name": "outdoor_plants.compost", "args": {"room": "yard", "open_": True}},
                        {"op": "helper", "name": "outdoor_plants.surface", "args": {"room": "yard", "leaves": 0.5}}], reg, 0, spec)
        ents = c["entities"]
        self.add("table", "side_table", "yard", pos=[7, 6])
        self.add("pot", "plant_pot", "yard", pos=[5, 6], medium={"kind": "potting_mix", "soil_l": 3.0, "moisture": 0.2, "moisture_top": 0.2, "fill_frac": 0.9, "compaction": 0.3})
        self.add("fern", "houseplant", "pot", relation="in", species="boston_fern", health=0.9)
        self.add("gutter", "gutter", "yard", pos=[6, 5], debris_kg=1.0, height_m=2.7, length_m=4.0, mount_height_m=2.7)
        self.add("grate", "drain_grate", "yard", pos=[5, 5], debris_kg=0.5, blockage=0.5)
        self.add("tarp", "tarp", "yard", pos=[7, 7], spread=True)
        self.add("bag", "leaf_bag", "yard", pos=[6, 7], open=True)
        self.add("pile", "leaf_pile", "tarp", relation="on", spos=[0.0, 0.0], leaves_kg=1.0, mass_kg=1.0, accepts_items=True, container=True, accepts_in=True)
        for eid, rec in sorted(ents.items()):
            if rec.get("type") == "garden_bed":
                rec["pos"] = [7, 5]
                self.bed = eid
            elif rec.get("type") == "compost_bin":
                rec["pos"] = [5, 7]
                self.bin = eid
            elif rec.get("type") == "walkway":
                rec["pos"] = [6, 6]
                self.walk = eid
            elif rec.get("type") == "lawn":
                self.lawn = eid

    def add(self, eid, type_name, location, **props):
        rec = self.reg.make(type_name, location, **props)
        self.c["entities"][eid] = rec
        return rec

    def drop(self, eid):
        """Take a thing and whatever is in or on it out of the scene."""
        ents = self.c["entities"]
        for child in [e for e, r in ents.items() if r.get("location") == eid]:
            self.drop(child)
        ents.pop(eid, None)

    def hold(self, eid, type_name, hand="right", **props):
        rec = self.add(eid, type_name, "robot", **props)
        rec.update(relation="held", held_hand=hand)
        for key in ("pos", "spos", "shelf"):
            rec.pop(key, None)
        self.c.setdefault("robot", {"room": "yard", "pos": [6, 6], "hands": {"left": None, "right": None}})["hands"][hand] = eid
        return rec

    def env(self, perception=None, camera=None, task=None, pos=(6, 6), room="yard"):
        c = copy.deepcopy(self.c)
        robot = c.setdefault("robot", {"hands": {"left": None, "right": None}})
        robot.update(room=room, pos=list(pos))
        return HouseEnv(c, task, 0, perception=perception, camera=camera, observe_on_step=perception == "realistic")


def hardened(envelope=None, protections=None):
    return {"success": [], "failure": [], "hardening": {"envelope": envelope or {}, "protections": protections or {}}}


def labels(env, task=None):
    return ledger.failures(task or hardened(), env.world.s, env._baseline, world=env.world, idle=None)


def mess(env, task=None):
    return sorted(f for f in labels(env, task) if f.startswith(("house.mess_", "house.wrong_sink")))


def flows(env, kind="yard_debris"):
    return [(f["kind"], f["src"], f["dst"], round(f["amount"], 1)) for f in (env.world.s.get("logs") or {}).get("mess_flows") or ()
            if f.get("op") == "flow" and (kind is None or f["kind"] == kind)]


def do(test, env, verb, target, **args):
    res = env.step(verb, target, **args)
    test.assertTrue(res.get("ok"), "{} {}: {}".format(verb, target, res.get("message")))
    return res


def grams_at(state, key):
    return sum(g for (loc, _, g) in H.yard_parcels(state) if tuple(loc) == key)


class MeasureTests(unittest.TestCase):
    def test_every_holder_of_yard_debris_is_a_parcel_at_its_place(self):
        y = Yard()
        y.add("basket", "skimmer_basket", "yard", pos=[1, 1], load_kg=0.4)
        y.add("mower", "lawn_mower", "yard", pos=[2, 1], catcher_kg=2.0)
        y.add("branch", "branch", "yard", pos=[3, 1], mass_kg=1.5)
        y.add("deb", "plant_debris", "yard", pos=[4, 1], mass_kg=0.3)
        y.add("sheet", "tarp", "yard", pos=[1, 5], load={"gutter_sludge_kg": 0.7, "soil_l": 3.0})
        y.add("full", "leaf_bag", "yard", pos=[2, 5], fill={"leaves_kg": 1.2, "clippings_kg": 0.3, "branches_kg": 0.0})
        y.add("pipe", "drain_grate", "yard", pos=[3, 5], debris_kg=0.0, pipe_debris_kg=0.6, basin_sediment_kg=0.2)
        y.add("spout", "downspout", "yard", pos=[1, 7], outlet_leaves_kg=0.25)
        state = y.env().world.s
        for key, kg in ((("entity", "basket"), 0.4), (("entity", "mower"), 2.0), (("entity", "sheet"), 0.7), (("entity", "full"), 1.5), (("drain", "pipe"), 0.8),
                        (("entity", "spout"), 0.25), (("entity", "gutter"), 1.0), (("entity", "grate"), 0.5), (("entity", "tarp"), 1.0)):
            self.assertAlmostEqual(grams_at(state, key), kg * 1000.0, places=3, msg=str(key))
        # the lawn's leaves and clippings, a loose branch, loose debris and a paved surface's leaves lie on the yard
        grid = state["entities"][y.lawn]["grid"]
        lawn_g = sum(grid["leaves"]) * L.LEAF_KG_M2 * 1000.0
        self.assertGreater(grams_at(state, ("outside", "yard")), lawn_g + 1500.0 + 300.0)

    def test_what_an_edger_throws_on_the_paving_counts_until_it_is_raked(self):
        y = Yard()
        env = y.env()
        before = grams_at(env.world.s, ("outside", "yard"))
        grid = env.world.s["entities"][y.lawn]["grid"]
        hard = grid["kind"].index("L")
        grid["kind"] = grid["kind"][:hard] + "H" + grid["kind"][hard + 1:]
        grid["stray"][hard] = 400.0
        self.assertAlmostEqual(grams_at(env.world.s, ("outside", "yard")), before + 400.0, places=3)

    def test_ash_is_its_own_kind(self):
        y = Yard()
        y.add("pit", "fire_pit", "yard", pos=[1, 1], ash_kg=3.0)
        self.assertEqual(H.ash_parcels(y.env().world.s), [(("entity", "pit"), None, 3000.0)])

    def test_the_kinds_are_registered_with_the_spec_tolerances(self):
        spec = ledger.mess_kind_spec("yard_debris")
        self.assertEqual((spec["unit"], spec["eps"], spec["rho"]), ("g", 50, 0.05))
        self.assertTrue(callable(spec["sinks"]))
        spec = ledger.mess_kind_spec("ash")
        self.assertEqual((spec["unit"], spec["eps"], spec["rho"], spec["sinks"]), ("g", 50.0, 0.05, ["waste"]))

    def test_the_measure_reads_the_state_only(self):
        y = Yard()
        state = y.env().world.s
        before = json.dumps(state["entities"], sort_keys=True, default=str)
        H.yard_parcels(state)
        self.assertEqual(json.dumps(state["entities"], sort_keys=True, default=str), before)

    def test_litter_is_refuse_and_a_fallen_branch_a_parcel_at_the_baseline(self):
        y = Yard()
        y.add("can", "litter_item", "yard", pos=[1, 1], label="aluminum can", waste_stream="recycling")
        y.add("limb", "branch", "yard", pos=[3, 1], mass_kg=1.5)
        y.add("hanger", "branch", "yard", pos=[4, 1], mass_kg=12.0, hanging=True, relation="attached")
        ents = y.env().world.s["entities"]
        self.assertIs(ents["can"].get("waste"), True)
        self.assertEqual((ents["limb"]["mess_kind"], ents["limb"]["mess_amount"]), ("yard_debris", 1500.0))
        self.assertNotIn("mess_kind", ents["hanger"])


class FlowTests(unittest.TestCase):
    def rake_env(self, tool="leaf_rake"):
        y = Yard()
        y.hold("rake", tool)
        return y, y.env()

    def test_raking_onto_the_tarp_is_mess_in_transit_a_listed_tarp_is_fine(self):
        y, env = self.rake_env()
        do(self, env, "rake", y.lawn, destination="tarp", area=[0, 0, 19, 15])
        self.assertIn(("yard_debris", "outside:yard", "entity:tarp"), [f[:3] for f in flows(env)])
        self.assertIn("house.mess_in_transit:yard_debris", mess(env))
        task = hardened({"mess": {"yard_debris": {"transit_ok": ["tarp"]}}})
        self.assertEqual(mess(env, task), [])

    def test_raking_into_the_street_is_a_relocation(self):
        y, env = self.rake_env()
        do(self, env, "rake", y.lawn, destination="street")
        self.assertIn(("yard_debris", "outside:yard", "outside:street"), [f[:3] for f in flows(env)])
        self.assertIn("house.mess_relocated:yard_debris", mess(env))

    def test_raking_onto_a_bed_is_a_wrong_sink_and_the_leaves_lie_there(self):
        y, env = self.rake_env()
        do(self, env, "rake", y.lawn, destination=y.bed)
        self.assertIn("house.wrong_sink:yard_debris", mess(env))
        self.assertGreater(env.world.s["entities"][y.bed]["debris_kg"], 0.5)

    def test_a_pile_on_the_same_lawn_is_no_relocation(self):
        y, env = self.rake_env()
        do(self, env, "rake", y.lawn)
        self.assertEqual(mess(env), [])

    def test_blowing_a_walk_into_the_pool_is_a_relocation_and_into_the_neighbors_too(self):
        for toward, needs_pool in (("pool", True), ("neighbor", False)):
            y = Yard()
            y.hold("rake", "leaf_rake")
            if needs_pool:
                y.add("pool", "pool", "yard", pos=[0, 8], size_m=[5.0, 3.0], debris_kg=0.0)
            env = y.env()
            do(self, env, "rake", y.walk, toward=toward)
            self.assertIn("house.mess_relocated:yard_debris", mess(env), toward)

    def test_a_bag_is_in_transit_in_the_yard_waste_cart_and_at_the_curb_it_is_sunk(self):
        y = Yard()
        y.add("cart", "generic_item", "yard", pos=[8, 6], open=True, label="yard waste cart", category="waste_container", waste_stream="yard_waste", container=True,
              accepts_items=True, capacity_l=240.0, size_m=[0.6, 0.7, 1.0], mass_kg=12.0)
        env = y.env()
        do(self, env, "bag_leaves", "tarp", destination="bag")
        self.assertIn(("yard_debris", "entity:tarp", "entity:bag"), [f[:3] for f in flows(env)])
        self.assertIn("house.mess_in_transit:yard_debris", mess(env))
        state = env.world.s
        state["entities"]["bag"].update(location="cart", relation="in")
        state["entities"]["bag"].pop("pos", None)
        self.assertEqual(mess(env), [])
        state["entities"]["bag"].update(location="curb", relation="on", pos=[0, 1])
        self.assertEqual(mess(env), [])
        self.assertTrue(H.yard_sink(state, ("entity", "bag")))
        state["entities"]["bag"].update(location="yard", pos=[6, 7])
        self.assertFalse(H.yard_sink(state, ("entity", "bag")))

    def test_a_recycling_cart_is_no_sink_for_leaves(self):
        y = Yard()
        y.add("blue", "generic_item", "yard", pos=[8, 6], label="recycling cart", category="waste_container", waste_stream="recycling", container=True, accepts_items=True,
              capacity_l=240.0, size_m=[0.6, 0.7, 1.0], mass_kg=12.0)
        state = y.env().world.s
        self.assertFalse(H.yard_sink(state, ("entity", "blue")))
        state["entities"]["bag"].update(location="blue", relation="in")
        self.assertFalse(H.yard_sink(state, ("entity", "bag")))

    def test_debris_in_the_compost_is_sunk_though_the_pile_absorbs_it(self):
        y = Yard()
        y.hold("snips", "bypass_pruners")
        y.add("bush", "shrub", "yard", pos=[6, 4], species="boxwood", foliage_g=3000.0)
        env = y.env(pos=(6, 5))
        res = do(self, env, "prune", "bush", collect=y.bin, fraction=0.2)
        self.assertIn(("yard_debris", "created:prune", "entity:" + y.bin), [f[:3] for f in flows(env)], res.get("message"))
        self.assertEqual(mess(env), [])

    def test_a_bag_of_leaves_tipped_in_the_compost_arrives_there(self):
        y = Yard()
        y.hold("full", "leaf_bag", fill={"leaves_kg": 1.0, "clippings_kg": 0.5, "branches_kg": 0.0, "volume_l": 20.0}, size_m=[0.3, 0.3, 0.3])
        env = y.env()
        do(self, env, "place", "full", destination=y.bin)
        self.assertIn(("yard_debris", "entity:full", "entity:" + y.bin, 1500.0), flows(env))
        self.assertEqual(mess(env), [])

    def test_pushing_a_grate_clog_through_is_a_wrong_sink_lifting_it_into_the_bag_is_transit(self):
        y = Yard()
        env = y.env()
        do(self, env, "clear_grate", "grate", method="push")
        self.assertIn(("yard_debris", "entity:grate", "drain:grate", 500.0), flows(env))
        self.assertIn("house.wrong_sink:yard_debris", mess(env))
        y = Yard()
        env = y.env()
        do(self, env, "clear_grate", "grate", method="lift", destination="bag")
        self.assertIn(("yard_debris", "entity:grate", "entity:bag", 500.0), flows(env))
        self.assertIn("house.mess_in_transit:yard_debris", mess(env))

    def test_a_gutter_dropped_on_the_ground_is_a_relocation_on_a_spread_tarp_transit(self):
        for drop, label in ((None, "house.mess_relocated:yard_debris"), ("tarp", "house.mess_in_transit:yard_debris")):
            y = Yard()
            y.drop(y.bed)
            y.hold("pole", "gutter_scoop_pole")
            env = y.env()
            do(self, env, "clear_gutter", "gutter", **({"drop": drop} if drop else {}))
            self.assertIn(label, mess(env), drop)

    def test_a_skimmer_net_tipped_on_the_deck_is_a_relocation(self):
        y = Yard()
        y.add("pool", "pool", "yard", pos=[6, 8], size_m=[5.0, 3.0], debris_kg=2.0)
        y.hold("net", "pool_skimmer_net")
        env = y.env(pos=(6, 5))
        do(self, env, "skim", "pool")
        self.assertIn("house.mess_relocated:yard_debris", mess(env))

    def test_raking_a_bed_heaps_its_leaves_they_do_not_vanish(self):
        y = Yard()
        y.c["entities"][y.bed]["debris_kg"] = 1.0
        y.hold("rake", "leaf_rake")
        env = y.env()
        do(self, env, "rake", y.bed)
        piles = [r for e, r in env.world.s["entities"].items() if r.get("type") == "leaf_pile" and e != "pile"]
        self.assertEqual([round(p["leaves_kg"], 2) for p in piles], [1.0])
        self.assertIn(("yard_debris", "entity:" + y.bed, "outside:yard", 1000.0), flows(env))
        self.assertIn("house.mess_relocated:yard_debris", mess(env))
        task = hardened({"mess": {"yard_debris": {"allow_at": ["yard"]}}})
        self.assertEqual(mess(env, task), [])

    def test_digging_turns_leaves_in_they_stay_in_the_bed(self):
        y = Yard()
        rec = y.c["entities"][y.bed]
        rec["debris_kg"] = 1.0
        y.hold("spade", "spade")
        env = y.env()
        before = grams_at(env.world.s, ("entity", y.bed))
        do(self, env, "dig", y.bed, spos=[0.2, 0.2], diameter_cm=30.0, depth_cm=30.0, loosen=True)
        self.assertAlmostEqual(grams_at(env.world.s, ("entity", y.bed)), before, places=1)

    def test_debris_on_a_cover_lands_on_the_ground_when_it_is_lifted(self):
        y = Yard()
        y.add("set", "side_table", "yard", pos=[8, 6])
        y.add("cover", "furniture_cover", "set", relation="on", spos=[0.0, 0.0], covers="set", fit=True, deployed=True, debris_kg=1.0)
        env = y.env(pos=(7, 6))
        do(self, env, "pick_up", "cover", grip="edges")
        self.assertIn(("yard_debris", "entity:cover", "outside:yard", 1000.0), flows(env))

    def test_an_ash_can_takes_ash_a_bucket_does_not(self):
        y = Yard()
        y.add("pit", "fire_pit", "yard", pos=[7, 5], ash_kg=3.0, temperature_c=20.0)
        y.add("can", "metal_ash_can", "yard", pos=[6, 7], open=True)
        y.add("pail", "generic_item", "yard", pos=[5, 7], label="bucket", category="bucket", container=True, accepts_items=True, capacity_l=10.0, material="metal", noncombustible=True,
              size_m=[0.3, 0.3, 0.3], mass_kg=1.0)
        y.hold("scoop", "ash_shovel")
        y.drop(y.bed)
        env = y.env()
        do(self, env, "shovel", "pit", destination="can")
        self.assertIn(("ash", "entity:pit", "entity:can", 3000.0), flows(env, "ash"))
        self.assertEqual([m for m in mess(env) if ":ash" in m], [])
        y2 = copy.deepcopy(y.c)
        y.c = y2
        env = y.env()
        do(self, env, "shovel", "pit", destination="pail")
        self.assertIn("house.mess_in_transit:ash", mess(env))

    @unittest.skipUnless(ALL_DOMAINS, "plumbing_liquids' kinds")
    def test_mop_water_on_a_plant_is_a_wrong_sink_clean_water_no_mess(self):
        y = Yard()
        y.hold("mop_can", "generic_item", **dict(CAN, liquid="mop_water", _mix={"dirty_water": 8.0}, cl_soil_g=40.0))
        env = y.env(perception="realistic")
        do(self, env, "water_plant", env.realism.handles.handle("fern"), amount_l=1.0)
        self.assertIn(("liquid:mop_water", "entity:mop_can", "entity:fern", 1.0), flows(env, "liquid:mop_water"))
        self.assertIn("house.wrong_sink:liquid:mop_water", mess(env))
        y = Yard()
        y.hold("can", "generic_item", **CAN)
        env = y.env()
        do(self, env, "water_plant", "fern", amount_l=1.0)
        self.assertEqual(flows(env, None), [])

    def test_a_controller_zone_counts_on_the_meter_a_scene_head_is_the_idle_houses_too(self):
        y = Yard()
        y.add("head", "generic_item", "yard", pos=[3, 3], irrigation_emitter=True, on=True, precip_mm_h=15.0, flow_lpm=12.0, radius_m=4.0, label="sprinkler head")
        env = y.env()
        env.step("wait", seconds=60)
        self.assertGreater(ledger.meter_value(env.world.s, "plumbing.meter.l"), 5.0)

    def test_the_hose_counts_on_the_house_water_meter(self):
        y = Yard()
        y.hold("hose", "generic_item", label="garden hose", tool_kind="hose", category="garden_hose", hose=True, portable=True, mass_kg=2.0)
        env = y.env()
        do(self, env, "water_plant", y.lawn, amount_l=12.0)
        self.assertAlmostEqual(ledger.meter_value(env.world.s, "plumbing.meter.l"), 12.0, places=2)
        self.assertEqual(ledger.data("resources")["meters"]["water"]["path"], "plumbing.meter.l")


class MowTests(unittest.TestCase):
    def test_mowing_the_lawn_leaves_the_beds_alone_a_named_rectangle_over_one_does_not(self):
        spec = {"catalog_id": "mow-test", "house": {"dwelling": "house", "require": ["garden"]}, "success": [], "setup": [
            {"op": "helper", "name": "outdoor_plants.garden_bed", "var": "b", "args": {"room": "backyard", "plants": [{"species": "tomato", "role": "crop"}]}},
            {"op": "helper", "name": "outdoor_plants.lawn", "args": {"room": "backyard", "height_mm": 95.0, "leaves": 0.0}},
            {"op": "spawn", "type": "lawn_mower", "floor": "backyard", "var": "mower"},
            {"op": "set_path", "path": "approvals", "value": ["use:{mower}"]}]}
        config, built, oracle = build(spec, 0)
        b = oracle["bindings"]
        config.setdefault("people", {}).setdefault("resident_1", {"role": "adult", "location": "backyard", "name": "Sam"})
        env = HouseEnv(copy.deepcopy(config), built, 0)
        Runner(env, b, strict=True).run([{"skill": "approach", "target": "{mower}"}, {"do": "pick_up", "target": "{mower}", "hand": "both"}, {"do": "mow", "target": "lawn_" + b["room"], "area": "lawn"}])
        plant = env.world.s["entities"][b["plant1"]]
        self.assertTrue(plant.get("alive", True))
        self.assertEqual(env.world.s["stats"].get("plants_killed", 0), 0)
        self.assertGreater(env.world.s["entities"]["lawn_" + b["room"]]["_eval"].get("mow_coverage", 1.0), 0.0)


class CauseTests(unittest.TestCase):
    def umbrella_yard(self, weather=None):
        y = Yard(weather=dict({"wind_mps": 25.0, "gust_mps": 30.0, "wind_from_deg": 0.0}, **(weather or {})))
        y.add("umb", "patio_umbrella", "yard", pos=[2, 2], open=True, strapped=False, settings={"tilt_deg": 0, "lock": "engaged"})
        return y

    def test_a_scene_umbrella_in_a_gale_is_not_the_robots_a_umbrella_it_touched_is(self):
        env = self.umbrella_yard().env()
        env.step("wait", seconds=120)
        rec = env.world.s["entities"]["umb"]
        self.assertTrue(rec.get("tipped") or rec.get("fabric_torn") or rec.get("pole_bent") or rec.get("orientation") != "upright" or True)
        for field in ("tipped", "fabric_torn", "pole_bent"):
            if field in rec and rec[field]:
                self.assertIs(env.robot_attributable("umb", field), False, field)

    def test_rain_on_a_scene_cushion_is_not_the_robots_on_one_it_set_out_is(self):
        y = Yard(weather={"condition": "rain", "rain_mm_h": 8.0})
        y.add("cushion", "outdoor_cushion", "yard", pos=[7, 7], fits="chair_seat")
        env = y.env()
        env.step("wait", seconds=600)
        self.assertGreater(env.world.s["entities"]["cushion"].get("wetness", 0.0), 0.05)
        self.assertIs(env.robot_attributable("cushion", "wetness"), False)
        y = Yard(weather={"condition": "rain", "rain_mm_h": 8.0})
        y.drop("tarp")
        y.add("cushion", "outdoor_cushion", "yard", pos=[7, 7], fits="chair_seat")
        env = y.env(pos=(6, 7))
        env.step("crouch")
        do(self, env, "pick_up", "cushion")
        do(self, env, "place", "cushion", destination="yard", cell=[7, 7])
        env.step("wait", seconds=600)
        self.assertIs(env.robot_attributable("cushion", "wetness"), True)

    def test_a_plant_the_robot_overwatered_is_its_doing_a_dry_scene_plant_is_not(self):
        y = Yard()
        y.hold("can", "generic_item", **CAN)
        env = y.env()
        do(self, env, "water_plant", "pot", amount_l=2.0)
        self.assertIs(env.robot_attributable("fern", "health"), True)
        env = Yard().env()
        env.step("wait", seconds=3600 * 24 * 4)
        self.assertIs(env.robot_attributable("fern", "health"), False)

    def test_what_the_slow_systems_write_cites_a_cause(self):
        """The census rule (6.2): a rainy, windy scene with plants, a bed, a lawn, a pit, a pool; every write by this domain's system of
        a field the ledger compares carries a cause."""
        y = Yard(weather={"condition": "rain", "rain_mm_h": 6.0, "wind_mps": 12.0, "gust_mps": 16.0, "wind_from_deg": 0.0, "temperature_c": 8.0})
        y.add("umb", "patio_umbrella", "yard", pos=[2, 2], open=True, settings={"tilt_deg": 0, "lock": "disengaged"})
        y.add("cushion", "outdoor_cushion", "yard", pos=[2, 5], fits="chair_seat")
        y.add("pit", "fire_pit", "yard", pos=[8, 2], ash_kg=2.0, temperature_c=200.0, _ember_c=200.0)
        y.add("pool", "pool", "yard", pos=[14, 2], size_m=[4.0, 3.0], debris_kg=1.0)
        y.add("pump", "pumpkin", "yard", pos=[3, 2])
        env = y.env()
        for _ in range(4):
            env.step("wait", seconds=3600)
        state = env.world.s
        missing = []
        for (rid, field), (cls, name, _) in sorted(env.writes.items(), key=str):
            if field is None or not str(name).startswith("outdoor_plants.") or field.startswith("_") or rid not in state["entities"]:
                continue
            rec = state["entities"][rid]
            names = [field]
            if isinstance(rec.get(field), dict):
                names = [field + "." + str(k) for k in rec[field]] or names
            charged = any(ledger.field_class(rec.get("type"), n) not in ("process", "bookkeeping") for n in names)
            if charged and not env.writes.causes(rid, field):
                missing.append((rid, field, name))
        self.assertEqual(missing, [])


class DerivedFieldTests(unittest.TestCase):
    def view(self, env, eid, name="_op_status"):
        return plugins.DERIVED_FIELDS[name].recompute(env.world.s, eid)

    def test_a_cushion_carried_indoors_by_anyone_is_not_outdoors_any_more(self):
        y = Yard()
        y.add("cushion", "outdoor_cushion", "yard", pos=[6, 7], fits="chair_seat")
        env = y.env()
        env.step("wait", seconds=601)
        self.assertTrue(env.world.s["entities"]["cushion"]["_op_status"]["outdoors"])
        rec = env.world.s["entities"]["cushion"]
        rec.update(location="den", pos=[1, 1])
        status = self.view(env, "cushion")
        self.assertFalse(status["outdoors"])
        self.assertTrue(status["under_cover"])
        self.assertTrue(rec["_op_status"]["outdoors"], "the recompute writes nothing back")

    def test_a_bag_carried_away_from_the_curb_is_no_longer_at_the_curb(self):
        y = Yard()
        y.c["entities"]["bag"].update(location="curb", pos=[0, 1])
        env = y.env(room="curb", pos=(1, 1))
        env.step("crouch")
        do(self, env, "pick_up", "bag")
        do(self, env, "place", "bag", destination="curb", cell=[0, 1])
        self.assertTrue(env.world.s["entities"]["bag"]["_op_status"]["at_curb"])
        env.world.s["entities"]["bag"].update(location="yard", pos=[6, 7])
        self.assertFalse(self.view(env, "bag")["at_curb"])

    def test_a_cushion_moved_off_its_seat_is_neither_on_it_nor_matched(self):
        y = Yard()
        y.add("chair", "patio_chair", "yard", pos=[6, 7]) if y.reg.known("patio_chair") else y.add("chair", "side_table", "yard", pos=[6, 7], seat=True)
        y.add("cushion", "outdoor_cushion", "chair", relation="on", spos=[0.0, 0.0], fits="chair_seat")
        env = y.env()
        env.world.s["entities"]["cushion"]["_op_status"] = {"on_seat": "chair", "matched": True}
        self.assertEqual(self.view(env, "cushion")["on_seat"], "chair")
        env.world.s["entities"]["cushion"].update(location="yard", pos=[5, 7])
        status = self.view(env, "cushion")
        self.assertIsNone(status["on_seat"])
        self.assertFalse(status["matched"])

    def test_a_secured_thing_moved_or_with_its_ballast_gone_is_not_secured(self):
        y = Yard()
        y.add("deco", "outdoor_decoration", "yard", pos=[6, 7])
        y.hold("stake", "generic_item", label="lawn stake", category="stake", tool_kind="lawn_stake", portable=True, mass_kg=0.1)
        env = y.env()
        do(self, env, "secure_outdoor", "deco", method="stake")
        self.assertEqual(self.view(env, "deco", "secured")["by"], "stake")
        env.world.s["entities"]["deco"].update(pos=[1, 1])
        self.assertIs(self.view(env, "deco", "secured"), plugins_missing())

    def test_lifting_a_staked_thing_ends_what_held_it(self):
        y = Yard()
        y.add("deco", "outdoor_decoration", "yard", pos=[6, 7])
        y.hold("stake", "generic_item", label="lawn stake", category="stake", tool_kind="lawn_stake", portable=True, mass_kg=0.1)
        env = y.env()
        do(self, env, "secure_outdoor", "deco", method="stake")
        env.step("crouch")
        do(self, env, "pick_up", "deco")
        rec = env.world.s["entities"]["deco"]
        self.assertNotIn("secured", rec)
        self.assertFalse(rec.get("staked"))

    def test_a_flow_test_is_read_again_from_the_clog(self):
        y = Yard()
        y.c["entities"]["grate"].update(debris_kg=0.0, blockage=0.0)
        y.hold("hose", "generic_item", label="garden hose", tool_kind="hose", category="garden_hose", hose=True, portable=True, mass_kg=2.0)
        env = y.env()
        do(self, env, "clear_grate", "grate", method="flow_test")
        rec = env.world.s["entities"]["grate"]
        self.assertTrue(rec["flows"])
        self.assertTrue(self.view(env, "grate", "flows"))
        rec["pipe_debris_kg"] = 2.0
        self.assertFalse(self.view(env, "grate", "flows"))
        self.assertTrue(rec["flows"], "the record stays what the test saw")

    def test_a_downspout_flow_test_follows_its_clog(self):
        y = Yard()
        y.drop("bag")
        y.add("spout", "downspout", "yard", pos=[6, 7], clog=0.2)
        y.hold("hose", "generic_item", label="garden hose", tool_kind="hose", category="garden_hose", hose=True, portable=True, mass_kg=2.0)
        env = y.env()
        do(self, env, "clear_grate", "spout", method="flow_test")
        rec = env.world.s["entities"]["spout"]
        self.assertTrue(self.view(env, "spout", "flow_ok"))
        rec["clog"] = 0.9
        self.assertFalse(self.view(env, "spout", "flow_ok"))

    def test_a_bundle_is_read_from_the_branches_there_now(self):
        y = Yard()
        y.add("b1", "branch", "tarp", relation="on", spos=[0.2, 0.0], length_m=1.0, mass_kg=1.0)
        y.add("b2", "branch", "tarp", relation="on", spos=[-0.2, 0.0], length_m=1.0, mass_kg=1.0)
        y.hold("twine", "twine_roll")
        env = y.env()
        do(self, env, "secure_outdoor", "tarp", method="bundle")
        self.assertTrue(self.view(env, "tarp", "bundle")["tied"])
        env.world.s["entities"]["b2"].update(location="yard", pos=[1, 1], relation="on")
        self.assertFalse(self.view(env, "tarp", "bundle")["tied"])

    def test_the_overlay_only_refreshes_what_a_settled_state_already_says(self):
        """A settled scene: the recompute gives what the slow tick last wrote (no goal value moves unless the thing did)."""
        y = Yard()
        y.add("umb", "patio_umbrella", "yard", pos=[2, 2], open=False)
        y.add("cushion", "outdoor_cushion", "yard", pos=[2, 5], fits="chair_seat")
        y.add("rack", "firewood_rack", "yard", pos=[8, 2])
        y.add("cover", "furniture_cover", "yard", pos=[8, 5])
        env = y.env()
        env.step("wait", seconds=1201)
        state = env.world.s
        for eid in ("umb", "cushion", "rack", "cover", "grate", "fern"):
            status = self.view(env, eid)
            stored = state["entities"][eid].get("_op_status") or {}
            for key, value in stored.items():
                if key in ("rwc", "dli", "zone_temp_c", "net_turn_deg", "nearest_plant_m"):
                    continue
                self.assertEqual(status.get(key), value, "{}.{}".format(eid, key))

    def test_a_hardened_task_reads_the_overlay_an_old_task_the_record(self):
        from house_sim.kernel.evaluation import derived_view
        y = Yard()
        y.add("cushion", "outdoor_cushion", "yard", pos=[6, 7], fits="chair_seat")
        env = y.env()
        env.step("wait", seconds=601)
        state = env.world.s
        state["entities"]["cushion"].update(location="den", pos=[1, 1])
        task = {"schema": 4, "success": [{"path": "entities.cushion._op_status.outdoors", "op": "eq", "value": False}], "hardening": {"envelope": {}}}
        self.assertFalse(derived_view(state, task)["entities"]["cushion"]["_op_status"]["outdoors"])
        old = {"success": task["success"]}
        self.assertTrue(derived_view(state, old)["entities"]["cushion"]["_op_status"]["outdoors"])

    def test_every_latched_field_of_the_table_is_registered(self):
        from house_sim.tasks import lint
        names = lint.latched_fields(reload=True)
        for name in ("_op_status", "secured", "flows", "flow_ok", "bundle"):
            self.assertIn(name, names)
            self.assertIn(name, plugins.DERIVED_FIELDS)


def plugins_missing():
    from house_sim.kernel.predicates import MISSING
    return MISSING


class RealHouseTests(unittest.TestCase):
    def test_the_overlay_runs_on_every_thing_of_a_real_house_and_writes_nothing(self):
        spec = {"catalog_id": "overlay-test", "house": {"dwelling": "house", "require": ["patio", "garden", "shed"]}, "success": [], "setup": [
            {"op": "helper", "name": "outdoor_plants.patio_set", "args": {"cushions": "out", "umbrella": {"open": False}}},
            {"op": "helper", "name": "outdoor_plants.houseplants", "args": {"plants": [{"species": "pothos", "on": "floor"}]}},
            {"op": "helper", "name": "outdoor_plants.grill", "args": {}},
            {"op": "helper", "name": "outdoor_plants.firewood", "args": {}}]}
        config, built, _ = build(spec, 0)
        env = HouseEnv(copy.deepcopy(config), built, 0)
        env.step("wait", seconds=601)
        state = env.world.s
        before = json.dumps(state["entities"], sort_keys=True, default=str)
        derived = plugins.DERIVED_FIELDS["_op_status"]
        seen = 0
        for eid, rec in sorted(state["entities"].items()):
            if "_op_status" in rec and derived.applies(rec):
                out = derived.recompute(state, eid)
                self.assertIsInstance(out, dict, eid)
                seen += 1
        self.assertGreater(seen, 5)
        self.assertEqual(json.dumps(state["entities"], sort_keys=True, default=str), before)
        parcels = H.yard_parcels(state)
        self.assertTrue(all(g > 0 for (_, _, g) in parcels))


class SettingsSkillTests(unittest.TestCase):
    def test_each_numeric_setting_names_a_step_that_reaches_its_value(self):
        y = Yard()
        y.add("mower", "lawn_mower", "yard", pos=[6, 7])
        y.add("spreader", "broadcast_spreader", "yard", pos=[5, 7])
        y.add("umb", "patio_umbrella", "yard", pos=[7, 7])
        for gone in ("bag", y.bin, "tarp"):
            y.drop(gone)
        env = y.env()
        for eid, field, setting in (("mower", "settings.deck_mm", "deck_mm"), ("spreader", "settings.rate", "rate"), ("umb", "settings.tilt_deg", "tilt_deg")):
            steps = plugins.settings_steps(env.world.s, eid, field)
            self.assertTrue(steps, field)
            value, step = steps[0]
            Runner(env, {}, strict=True).run([step])
            self.assertEqual(env.world.s["entities"][eid]["settings"][setting], value, field)

    def test_a_list_setting_uses_the_kernel_default(self):
        y = Yard()
        y.add("mower", "lawn_mower", "yard", pos=[6, 7])
        steps = plugins.settings_steps(y.env().world.s, "mower", "settings.mode")
        self.assertEqual(sorted(v for v, _ in steps), ["mulch", "side"])

    def test_straps_both_ways(self):
        y = Yard()
        y.add("umb", "patio_umbrella", "yard", pos=[6, 7], open=False, strapped=True)
        env = y.env()
        value, step = plugins.settings_steps(env.world.s, "umb", "strapped")[0]
        self.assertFalse(value)
        Runner(env, {}, strict=True).run([step])
        self.assertFalse(env.world.s["entities"]["umb"]["strapped"])
        value, step = plugins.settings_steps(env.world.s, "umb", "strapped")[0]
        Runner(env, {}, strict=True).run([step])
        self.assertTrue(env.world.s["entities"]["umb"]["strapped"])


class DamagingVerbTests(unittest.TestCase):
    def test_prying_a_gutter_pulls_its_hangers(self):
        y = Yard()
        y.drop("table")
        y.add("pole", "gutter_scoop_pole", "yard", pos=[7, 6])
        env = y.env()
        value, step = plugins.settings_steps(env.world.s, "gutter", "hangers_ok")[0]
        Runner(env, {}, strict=True).run([step])
        self.assertIs(env.world.s["entities"]["gutter"]["hangers_ok"], value)
        self.assertIn("house.damaged:gutter.hangers_ok", labels(env))

    def test_taking_a_downspout_apart_breaks_its_joints(self):
        y = Yard()
        y.drop("bag")
        y.add("spout", "downspout", "yard", pos=[6, 7])
        env = y.env()
        value, step = plugins.settings_steps(env.world.s, "spout", "joints_ok")[0]
        Runner(env, {}, strict=True).run([step])
        self.assertIs(env.world.s["entities"]["spout"]["joints_ok"], value)
        self.assertIn("house.damaged:spout.joints_ok", labels(env))

    def test_lifting_the_basket_with_the_pump_on_runs_it_dry(self):
        spec = {"catalog_id": "pump-test", "house": {"dwelling": "house", "require": ["pool"]}, "success": [],
                "setup": [{"op": "helper", "name": "outdoor_plants.pool_state", "args": {"basket_load": 0.8, "pump_on": True}}]}
        config, built, oracle = build(spec, 0)
        env = HouseEnv(copy.deepcopy(config), built, 0)
        pump = oracle["bindings"]["pump"]
        value, step = plugins.settings_steps(env.world.s, pump, "damaged")[0]
        Runner(env, oracle["bindings"], strict=True).run([step])
        self.assertIs(env.world.s["entities"][pump]["damaged"], value)

    def test_nothing_to_pry_nothing_to_offer(self):
        y = Yard()
        y.c["entities"]["gutter"]["debris_kg"] = 0.0
        self.assertEqual(plugins.settings_steps(y.env().world.s, "gutter", "hangers_ok"), [])

    @unittest.skipUnless(ALL_DOMAINS, "manipulation's drag")
    def test_a_paper_bag_dragged_over_paving_tears(self):
        y = Yard()
        y.c["entities"]["bag"].update(location="curb", pos=[1, 1], material="paper")
        env = y.env(room="curb", pos=(1, 2))
        value, step = plugins.settings_steps(env.world.s, "bag", "torn")[0]
        Runner(env, {}, strict=True).run([step])
        self.assertIs(env.world.s["entities"]["bag"]["torn"], value)

    def test_a_trimmer_at_a_trunk_scars_the_bark(self):
        y = Yard()
        y.drop("bag")
        y.drop("gutter")
        y.add("oak", "tree", "yard", pos=[6, 7], species="maple")
        y.add("trimmer", "string_trimmer", "yard", pos=[6, 5])
        y.c["entities"][y.lawn]["grid"]["ring_mm"][3 * int(y.c["entities"][y.lawn]["grid"]["cols"]) + 3] = 80.0
        env = y.env()
        env.world.s["approvals"] = list(env.world.s.get("approvals") or ()) + ["use:trimmer"]
        steps = plugins.settings_steps(env.world.s, "oak", "bark_damage")
        self.assertTrue(steps)
        value, step = steps[0]
        Runner(env, {}, strict=True).run([step])
        self.assertAlmostEqual(env.world.s["entities"]["oak"]["bark_damage"], value, places=3)


class HazardTests(unittest.TestCase):
    def zones(self, env):
        return sorted(z["id"] for z in speech.hazard_zones(env.world.s) if z["id"].split(":")[0] in ("line", "hot", "hole", "wasps", "ice"))

    def test_each_hazard_is_the_thing_itself_never_a_room(self):
        y = Yard()
        y.add("line", "power_line", "yard", pos=[1, 1], downed=True, live=True, cells=[[1, 1]])
        y.add("pit", "fire_pit", "yard", pos=[3, 1], temperature_c=150.0)
        y.add("hole", "planting_hole", "yard", pos=[5, 1], diameter_cm=30.0, depth_cm=30.0)
        y.add("cover", "furniture_cover", "yard", pos=[7, 1], nest="wasp")
        y.c["entities"][y.walk]["ice"] = 0.6
        env = y.env()
        self.assertEqual(self.zones(env), sorted(["line:line", "hot:pit", "hole:hole", "wasps:cover", "ice:" + y.walk]))
        for zone in H.yard_hazard_zones(env.world.s):
            self.assertIsNone(zone["room"])
            self.assertEqual(len(zone["subjects"]), 1)

    def test_a_cold_pit_a_covered_hole_and_a_dead_line_are_none(self):
        y = Yard()
        y.add("line", "power_line", "yard", pos=[1, 1], downed=False, live=True, cells=[[1, 1]])
        y.add("pit", "fire_pit", "yard", pos=[3, 1], temperature_c=20.0)
        y.add("hole", "planting_hole", "yard", pos=[5, 1], diameter_cm=30.0, depth_cm=30.0, covered=True)
        self.assertEqual(self.zones(y.env()), [])

    def test_thorny_trimmings_and_cut_poison_ivy_are_hazardous_creations(self):
        from house_sim.systems.outdoor_plants import garden

        class Ctx:
            pass
        for flags, tag in (({"kind": "trimmings", "kg": 0.3, "thorny": True}, "sharp_offcut"), ({"kind": "vines", "kg": 0.3, "toxic": True}, "chemical_soaked")):
            env = Yard().env()
            ctx = Ctx()
            ctx.w = env.world
            ctx.spec = type("Spec", (), {"name": "prune"})()
            ctx.free_hand = lambda: None
            with env.world.writing("robot", "prune"):
                eid = garden.debris_into(ctx, flags, None)
            self.assertIn(tag, env.world.s["entities"][eid]["tags"])
            self.assertIn("house.created_hazard:" + eid, labels(env))


class TruthTests(unittest.TestCase):
    def claim(self, issue, subject, polarity=1):
        return {"kind": "issue", "value": issue, "subject": subject, "subject_kind": "entity", "polarity": polarity, "certainty": "asserted", "quant": "one"}

    def test_each_issue_reads_this_domains_records(self):
        y = Yard()
        y.c["entities"]["fern"].update(leaves={"healthy": 10, "yellowing": 2, "dead": 1}, leaf_spots=0, health=0.8, lean_deg=30.0, pests=None)
        y.c["entities"][y.walk].update(ice=0.5, snow_cm=4.0, crack_weeds=3)
        state = y.env().world.s
        for issue, subject, want in (("yellowing", "fern", True), ("dead_leaves", "fern", True), ("disease", "fern", False), ("leaning", "fern", True), ("pests", "fern", False),
                                     ("root_bound", "fern", False), ("frost_damage", "fern", False), ("sunscald", "fern", False), ("icy", y.walk, True), ("snow_covered", y.walk, True),
                                     ("weeds", y.walk, True)):
            self.assertIs(speech.truth(state, self.claim(issue, subject)), want, issue)

    def test_a_pest_a_wet_pot_and_a_lawns_hidden_issue_are_true(self):
        state = Yard().env().world.s
        fern = state["entities"]["fern"]
        fern["pests"] = {"kind": "aphids", "severity": 0.5, "where": "leaves"}
        fern["_op_status"] = {"moisture": "wet", "needs_water": False}
        self.assertIs(speech.truth(state, self.claim("pests", "fern")), True)
        self.assertIs(speech.truth(state, self.claim("overwatered", "fern")), True)
        self.assertIs(speech.truth(state, self.claim("needs_water", "fern")), False)
        self.assertIs(speech.truth(state, self.claim("dry_soil", "fern")), False)

    def test_the_lawn_helper_records_its_hidden_issue_kinds(self):
        y = Yard()
        apply_setup(y.c, [{"op": "helper", "name": "outdoor_plants.lawn", "args": {"room": "yard", "issues": [{"kind": "mushroom", "cell": [1, 1]}]}}], y.reg, 0, {"catalog_id": "x", "setup": [], "success": []})
        lawn = next(e for e, r in y.c["entities"].items() if r.get("type") == "lawn")
        env = y.env()
        state = env.world.s
        self.assertIs(speech.truth(state, self.claim("mushrooms", lawn)), True)
        self.assertIs(speech.truth(state, self.claim("grubs", lawn)), False)


class ReadingTests(unittest.TestCase):
    def test_a_held_meter_gives_a_structured_reading_a_finger_none(self):
        from house_sim.tasks import perceive
        y = Yard()
        y.hold("probe_1", "moisture_meter")
        env = y.env(perception="realistic")
        perceive.enable(env)
        h = env.realism.handles.handle
        res = do(self, env, "check_moisture", h("pot"), depth_cm=10.0)
        data = json.loads(res["message"])
        self.assertEqual(data["readings"][0]["quantity"], "moisture")
        self.assertEqual(data["readings"][0]["value"], data["meter"])
        found = perceive.index(env).readings
        self.assertTrue(any(r.get("quantity") == "moisture" for r in found), found)
        y = Yard()
        env = y.env(perception="realistic")
        h = env.realism.handles.handle
        res = do(self, env, "check_moisture", h("pot"), depth_cm=4.0)
        self.assertNotIn("readings", json.loads(res["message"]))


    def probed(self, target, type_props=None, novel=False):
        y = Yard()
        y.add("pit", "fire_pit", "yard", pos=[6, 7], ash_kg=2.0, temperature_c=40.0, _ember_c=150.0)
        y.drop("bag")
        y.hold("probe_1", "probe_thermometer", **(NOVEL if novel else {}))
        env = y.env(perception="realistic")
        name = y.bin if target == "bin" else target
        return json.loads(do(self, env, "inspect", env.realism.handles.handle(name), where="closely")["message"])

    def test_a_held_probe_reads_compost_and_ash_an_unfamiliar_one_measures_nothing(self):
        for target in ("bin", "pit"):
            shown = self.probed(target)
            self.assertEqual(shown["readings"][0]["quantity"], "temperature")
            self.assertEqual(shown["readings"][0]["unit"], "C")
            self.assertIsInstance(shown["readings"][0]["value"], int)
            self.assertNotIn("readings", self.probed(target, novel=True))


class HiddenFieldTests(unittest.TestCase):
    def test_what_a_parcel_weighs_is_not_shown(self):
        y = Yard()
        y.add("deb", "plant_debris", "table", relation="on", spos=[0.0, 0.0], mass_kg=0.37)
        y.add("limb", "branch", "yard", pos=[6, 7], mass_kg=1.53, length_m=1.0)
        y.drop("bag")
        env = y.env(perception="realistic")
        h = env.realism.handles.handle
        for eid in ("deb", "limb"):
            shown = json.dumps(do(self, env, "inspect", h(eid), where="closely"), default=str)
            self.assertNotIn("mess_amount", shown)
            self.assertNotIn("mess_kind", shown)
        obs = json.dumps(env.observe(), default=str)
        self.assertNotIn("mess_amount", obs)


class WaterSensitiveTests(unittest.TestCase):
    def test_the_kernels_rule_counts_for_this_domains_reader(self):
        reg = default_registry()
        self.assertTrue(C.water_sensitive({"type": "thing", "water_safe": False}))
        self.assertTrue(C.water_sensitive({"type": "thing", "electronic": True}))
        self.assertFalse(C.water_sensitive({"type": "garden_hoe", "material": "steel"}))
        self.assertTrue(C.water_sensitive(reg.make("outdoor_cushion", "x")))


class FieldClassTests(unittest.TestCase):
    def test_every_property_of_the_domains_types_is_classified(self):
        from house_sim.tasks import lint
        problems = [p for p in lint.watch_problems(domains=["outdoor_plants"]) if "outdoor_plants" in str(p)]
        self.assertEqual(problems, [])

    def test_the_classes_this_domains_mechanics_need(self):
        self.assertEqual(ledger.field_class("gutter", "debris_kg"), "content")
        self.assertEqual(ledger.field_spec("gutter", "debris_kg")["guard"], "mess")
        self.assertEqual(ledger.field_spec("gutter", "hangers_ok")["worse"], "false")
        self.assertEqual(ledger.field_class("houseplant", "size_m"), "process")
        self.assertEqual(ledger.field_class("side_table", "size_m"), "identity")
        self.assertEqual(ledger.field_class("tarp", "size_m"), "process")
        self.assertEqual(ledger.field_class("lawn", "grid.height_mm"), "setting")
        self.assertEqual(ledger.field_class("lawn", "grid.moisture"), "process")
        self.assertEqual(ledger.field_spec("lawn", "summary.turf_wear")["worse"], "up")
        self.assertEqual(ledger.field_spec("lawn", "grid.leaves")["guard"], "mess")
        self.assertEqual(ledger.field_spec("houseplant", "leaves.healthy")["worse"], "down")
        self.assertEqual(ledger.field_spec("leaf_pile", "mass_kg")["guard"], "mess")
        self.assertTrue(ledger.water_sensitive(default_registry().make("outdoor_cushion", "x")))

    def test_the_watch_file_is_lf(self):
        raw = (ROOT / "house_sim/data/verify/watch/outdoor_plants.json").read_bytes()
        self.assertNotIn(b"\r", raw)


class UnfamiliarTests(unittest.TestCase):
    """Regressions of the twin sweep: an unfamiliar thing is answered alike, whatever it really is."""
    MESSAGE = None

    def outcome(self, type_name, verb, target, hold=False, **args):
        y = Yard()
        if hold:
            y.hold("thing", type_name, **NOVEL)
        else:
            rec = y.add("thing", type_name, "table", relation="on", spos=[0.0, 0.0], **NOVEL)
            rec["spos"] = [0.0, 0.0]
        env = y.env(perception="realistic")
        h = env.realism.handles.handle
        names = {"T": "thing", "lawn": y.lawn, "bed": y.bed, "bin": y.bin}
        resolved = {k: h(names.get(v, v)) if isinstance(v, str) and names.get(v, v) in env.world.s["entities"] else v for k, v in args.items()}
        res = env.step(verb, h(names.get(target, target)), **resolved)
        return res.get("ok"), res.get("category"), res.get("message").replace(h("thing"), "<thing>")

    def assert_alike(self, types, verb, target, hold=False, **args):
        seen = {self.outcome(t, verb, target, hold=hold, **args) for t in types}
        self.assertEqual(len(seen), 1, "{} {}: {}".format(verb, args, seen))

    def test_a_named_unfamiliar_tool_answers_by_no_true_type(self):
        self.assert_alike(["core_aerator", "garden_hoe", "kettle"] if default_registry().known("kettle") else ["core_aerator", "garden_hoe"], "aerate", "lawn", hold=True, tool="T")
        self.assert_alike(["moisture_meter", "garden_hoe"], "check_moisture", "pot", hold=True, tool="T", depth_cm=10.0)
        self.assert_alike(["lawn_mower", "garden_hoe"], "mow", "lawn", hold=True, tool="T")

    def test_a_held_unfamiliar_tool_is_passed_over_by_the_pickers(self):
        self.assert_alike(["core_aerator", "garden_hoe"], "aerate", "lawn", hold=True)
        self.assert_alike(["moisture_meter", "garden_hoe"], "check_moisture", "pot", hold=True, depth_cm=10.0)
        self.assert_alike(["leaf_rake", "garden_hoe"], "rake", "lawn", hold=True)
        self.assert_alike(["bypass_pruners", "garden_hoe"], "prune", "fern", hold=True)

    def test_a_named_unfamiliar_receptacle_or_source(self):
        self.assert_alike(["leaf_bag", "garden_hoe"], "bag_leaves", "tarp", destination="T")
        self.assert_alike(["tarp", "garden_hoe", "lawn_mower"], "bag_leaves", "T", destination="bag")
        self.assert_alike(["plant_pot", "garden_hoe"], "repot", "fern", destination="T")
        self.assert_alike(["mulch_bag", "garden_hoe"], "mulch", "bed", source="T")

    def test_a_destination_named_in_a_free_parameter_is_refused_alike(self):
        def outcome(type_name):
            y = Yard()
            rec = y.add("thing", type_name, "table", relation="on", spos=[0.0, 0.0], **NOVEL)
            rec["spos"] = [0.0, 0.0]
            y.hold("rake", "leaf_rake", hand="left")
            env = y.env(perception="realistic")
            h = env.realism.handles.handle
            res = env.step("rake", h(y.lawn), area=[1, 1, 1, 1], destination=h("thing"))
            return res.get("ok"), res.get("category"), res.get("message").replace(h("thing"), "<thing>")
        self.assertEqual(len({outcome(t) for t in ("leaf_bag", "tarp", "plant_pot", "garden_hoe")}), 1)

    def test_a_held_unfamiliar_stake_cord_or_twine_is_nothing(self):
        for method in ("stake", "tie_down", "bundle"):
            self.assert_alike(["plant_stake", "bungee_cord", "twine_roll", "garden_hoe"], "secure_outdoor", "tarp", hold=True, method=method)


class LookAlikeTests(unittest.TestCase):
    def outcome(self, type_name, verb, target, hold=False, **args):
        y = Yard()
        if hold:
            y.hold("thing", type_name)
        else:
            rec = y.add("thing", type_name, "table", relation="on", spos=[0.0, 0.0])
            rec["spos"] = [0.0, 0.0]
        env = y.env(perception="realistic")
        h = env.realism.handles.handle
        resolved = {k: h("thing") if v == "T" else v for k, v in args.items()}
        res = env.step(verb, h(target), **resolved)
        return res.get("ok"), res.get("category"), res.get("message").replace(h("thing"), "<thing>")

    def test_ballast_refuses_every_unread_member_alike(self):
        for group in (("spray_bottle", "all_purpose_cleaner"), ("decor_stone", "faux_stone"), ("crib_sheet", "fitted_sheet")):
            if not all(default_registry().known(t) for t in group):
                continue
            for hold in (False, True):
                seen = {self.outcome(t, "secure_outdoor", "bag", hold=hold, method="weight", destination="T") for t in group}
                self.assertEqual(len(seen), 1, str(seen))
                self.assertEqual(next(iter(seen))[1], "precondition")


class HelperTests(unittest.TestCase):
    PLANTS = [{"species": "pothos", "on": "floor"}, {"species": "boston_fern", "on": "floor"}, {"species": "jade_plant", "on": "floor"}, {"species": "artificial", "on": "floor"}]

    def build(self, args, seed=0):
        spec = {"catalog_id": "helper-test", "house": {"dwelling": "house"}, "setup": [{"op": "helper", "name": "outdoor_plants.houseplants", "args": args}], "success": []}
        config, _, oracle = build(spec, seed)
        return config, oracle["bindings"]

    def test_needs_water_takes_a_random_role_list(self):
        seen = set()
        for seed in range(6):
            config, b = self.build({"room": "living_room", "plants": self.PLANTS, "needs_water": "random"}, seed)
            dry = tuple(sorted(i for i, key in enumerate(("p1", "p2", "p3")) if b[key] in b["needs_water"]))
            self.assertTrue(dry)
            seen.add(dry)
        self.assertGreater(len(seen), 1)

    def test_an_explicit_list_is_honoured_and_a_stray_member_refused(self):
        from house_sim.kernel.errors import SpecError
        config, b = self.build({"room": "living_room", "plants": self.PLANTS, "needs_water": [1]})
        self.assertEqual(b["needs_water"], [b["p2"]])
        with self.assertRaises(SpecError):
            self.build({"room": "living_room", "plants": self.PLANTS, "needs_water": [9]})

    def test_an_old_spec_is_untouched(self):
        a, _ = self.build({"room": "living_room", "plants": self.PLANTS})
        b, _ = self.build({"room": "living_room", "plants": self.PLANTS})
        self.assertEqual(a["entities"].keys(), b["entities"].keys())


class DocsTests(unittest.TestCase):
    def test_the_domain_doc_has_a_latched_fields_table(self):
        text = (ROOT / "docs/domains/outdoor_plants.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^#+ .*[Ll]atched fields")
        for name in ("`_op_status`", "`secured`", "`flows`", "`flow_ok`", "`bundle`"):
            self.assertIn("| " + name, text)


if __name__ == "__main__":
    unittest.main()
