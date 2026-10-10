"""Tests for the textiles_laundry domain: every mechanic has a success and a failure/consequence case.

Run in isolation:  HOUSE_SIM_DOMAINS=textiles_laundry python -m unittest tests/test_textiles_laundry.py
Strict journaling: HOUSE_SIM_STRICT=1 (same command). The tests build their own small houses so they also
pass when all domains are loaded (other domains' systems may run, so assertions avoid their fields).
"""
import random
import unittest

from house_sim.generator import generate_house
from house_sim.generator.place import Placer
from house_sim.kernel import geometry
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import load_domains
from house_sim.kernel.predicates import check
from house_sim.kernel.registry import default_registry
from house_sim.kernel.schema import _default_connections

load_domains()
from house_sim.systems.textiles_laundry import model as M  # noqa: E402
from house_sim.systems.textiles_laundry.furnish import install_station  # noqa: E402


class Ctx:
    """Minimal stand-in for the setup context used by scenario helpers."""

    def __init__(self, config, placer, seed=0):
        self.config, self.placer, self.rng = config, placer, random.Random(seed)


def build(seed=0, start="2026-07-01T10:00:00", weather=None, people=None, pets=None, region_us=False):
    """laundry (4x4, tile) - hall (2x6, hardwood) - bedroom (4x4.5, carpet) - yard (8x6, outdoor) - porch."""
    reg = default_registry()
    rooms = {
        "laundry": {"label": "Laundry room", "type": "laundry_room", "size_m": [4.0, 4.0], "exits": ["hall"], "floor_material": "tile"},
        "hall": {"label": "Hallway", "type": "hallway", "size_m": [2.0, 6.0], "exits": ["laundry", "bedroom", "yard"], "floor_material": "hardwood"},
        "bedroom": {"label": "Bedroom", "type": "bedroom", "size_m": [4.0, 4.5], "exits": ["hall"], "floor_material": "carpet"},
        "yard": {"label": "Backyard", "type": "backyard", "size_m": [8.0, 6.0], "exits": ["hall"], "outdoor": True, "terrain": "grass",
                 "floor_material": "grass"},
    }
    config = {"schema_version": 2, "start_room": "laundry", "rooms": rooms, "entities": {}, "pets": pets or {},
              "people": people if people is not None else {"resident_1": {"label": "Alex", "role": "adult", "location": "bedroom", "pos": [3, 3]}},
              "clock": {"start": start, "hemisphere": "north"},
              "weather": weather or {"condition": "clear", "temperature_c": 22.0, "wind_mps": 2.0},
              "utilities": {"water_l": 100000.0}, "household": {}}
    config["connections"] = _default_connections(rooms)
    p = Placer(config, reg, random.Random(seed))
    for rid in ("laundry", "hall", "bedroom"):
        p.mount(rid, "ceiling_light", height=2.4, on=True)
        p.c["entities"]["floor_" + rid] = reg.make("floor_surface", rid, material=rooms[rid]["floor_material"], pos=[0, 0],
                                                   size_m=[rooms[rid]["size_m"][0], rooms[rid]["size_m"][1], 0.01])
    p.furniture("laundry", "shelf_unit")
    station = install_station(p, "laundry", "top_load" if region_us else "front_load", us=region_us)
    station["table"] = p.furniture("laundry", "table", wall=False, label="folding table")
    station["shelf_unit"] = station.get("shelf")
    return config, p, station


def env_of(config, seed=0):
    return HouseEnv(config, seed=seed, trace=True)


def ok(env, action, target="", destination="", **kw):
    result = env.step(action, target, destination, **kw)
    if not result["ok"]:
        raise AssertionError("{} {} {} refused: {}".format(action, target, destination, result["message"]))
    return result


def no(env, action, target="", destination="", **kw):
    result = env.step(action, target, destination, **kw)
    if result["ok"]:
        raise AssertionError("{} {} should have been refused".format(action, target))
    return result


def ent(env, eid):
    return env.world.s["entities"][eid]


def stat(env, name):
    return env.world.s["stats"].get(name, 0)


def hold(env, eid, **kw):
    ok(env, "approach", eid)
    from house_sim.kernel import geometry
    if geometry.height_m(env.world.s, eid) < geometry.LOW_REACH_M and env.world.s["robot"]["posture"] == "standing":
        ok(env, "crouch")
    ok(env, "pick_up", eid, **kw)
    if env.world.s["robot"]["posture"] != "standing":
        ok(env, "stand")


def set_up_rack(env, rack):
    """Lift a folded rack off the laundry wall hooks (where generated stations keep it), stand it on the floor, open it."""
    if ent(env, rack).get("location") not in env.world.s["rooms"]:
        hold(env, rack)
        ok(env, "place", rack)
    ok(env, "approach", rack)
    ok(env, "open", rack)


def run_washer(env, washer, program="normal", temp=None, spin=None):
    ok(env, "approach", washer)
    if ent(env, washer).get("open"):
        ok(env, "close", washer)
    if not ent(env, washer).get("on"):
        ok(env, "switch_on", washer)
    ok(env, "set_mode", washer, mode=program)
    if temp is not None:
        ok(env, "set", washer, setting="temp", value=temp)
    if spin is not None:
        ok(env, "set", washer, setting="spin", value=spin)
    ok(env, "start", washer)


def wait_long(env, seconds):
    while seconds > 0:
        chunk = min(seconds, 86400)
        ok(env, "wait", seconds=chunk)
        seconds -= chunk


def wait_done(env, machine, limit=4 * 3600):
    waited = 0
    while ent(env, machine)["appliance"]["state"] in ("running", "delayed") and waited < limit:
        ok(env, "wait", seconds=600)
        waited += 600
    ok(env, "wait", seconds=120)


class WasherTests(unittest.TestCase):
    def test_wash_cleans_with_detergent_but_not_without(self):
        for with_pod in (True, False):
            config, p, s = build()
            tee = p.inside(s["washer"], "t_shirt", soil={"dirt": 0.5}, worn=True)
            if not with_pod:
                for pod in s["pods"]:
                    del config["entities"][pod]
            else:
                pod = s["pods"][0]
                config["entities"][pod].update(location=s["washer"], relation="in", _load_seq=0)
                config["entities"][tee]["_load_seq"] = 1
            env = env_of(config)
            run_washer(env, s["washer"], "normal", temp=40)
            self.assertTrue(ent(env, s["washer"])["door_locked"])
            no(env, "open", s["washer"])
            wait_done(env, s["washer"])
            rec = ent(env, tee)
            last = ent(env, s["washer"])["last_cycle"]
            self.assertTrue(last["completed"])
            self.assertAlmostEqual(rec["wetness"], 0.38, delta=0.05)
            if with_pod:
                self.assertLess(M.soil_level(rec, "dirt"), 0.05)
                self.assertFalse(rec["worn"])
                self.assertGreater(last["dose_ratio"], 0.6)
            else:
                self.assertGreater(M.soil_level(rec, "dirt"), 0.2)
                self.assertEqual(last["dose_ratio"], 0.0)
            self.assertFalse(ent(env, s["washer"])["door_locked"])
            ok(env, "open", s["washer"])

    def test_hot_normal_wash_felts_wool_and_sets_blood_but_wool_cycle_is_safe(self):
        for program, temp, expect_damage in (("normal", 60, True), ("wool", 20, False)):
            config, p, s = build()
            sweater = p.inside(s["washer"], "sweater")
            sheet = p.inside(s["washer"], "flat_sheet", soil={"stain_blood": 0.4})
            env = env_of(config)
            run_washer(env, s["washer"], program, temp=temp)
            wait_done(env, s["washer"])
            sw = ent(env, sweater)
            if expect_damage:
                self.assertGreaterEqual(sw["shrunk"], 0.1)
                self.assertIn("felted", sw["damage"])
                self.assertIn("stain_blood", ent(env, sheet)["set_stains"])
                self.assertGreater(stat(env, "damage"), 0)
                self.assertFalse(env.evaluate()["success"])
            else:
                self.assertLess(sw["shrunk"], 0.03)
                self.assertNotIn("felted", sw.get("damage", {}))
                self.assertNotIn("stain_blood", ent(env, sheet)["set_stains"])

    def test_new_red_shirt_bleeds_into_whites_only_when_washed_together(self):
        for together in (True, False):
            config, p, s = build()
            white = p.inside(s["washer"], "dress_shirt", color="white", color_class="white")
            if together:
                p.inside(s["washer"], "t_shirt", color="red", color_class="red_bleeds", bleed=0.8)
            env = env_of(config)
            run_washer(env, s["washer"], "normal", temp=40)
            wait_done(env, s["washer"])
            dye = ent(env, white).get("dye_damage", 0.0)
            if together:
                self.assertGreaterEqual(dye, 0.06)
                self.assertIn("dye_transfer", ent(env, white)["damage"])
            else:
                self.assertLess(dye, 0.01)

    def test_pause_releases_lock_unless_water_above_door(self):
        config, p, s = build()
        p.inside(s["washer"], "t_shirt")
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        ok(env, "wait", seconds=600)
        ok(env, "pause", s["washer"])
        no(env, "open", s["washer"])
        ok(env, "wait", seconds=120)
        ok(env, "open", s["washer"])
        ok(env, "close", s["washer"])
        ok(env, "start", s["washer"])
        wait_done(env, s["washer"])
        self.assertTrue(ent(env, s["washer"])["last_cycle"]["completed"])
        # bulky program keeps water above the door: forcing it is a safety refusal
        config, p, s = build()
        p.inside(s["washer"], "blanket")
        p.inside(s["washer"], "bath_towel")
        env = env_of(config)
        run_washer(env, s["washer"], "bulky")
        ok(env, "wait", seconds=900)
        ok(env, "pause", s["washer"])
        ok(env, "wait", seconds=300)
        result = no(env, "open", s["washer"])
        self.assertEqual(result["category"], "safety")
        self.assertEqual(stat(env, "safety_blocks"), 1)

    def test_power_off_cancels_and_leaves_laundry_soaking(self):
        config, p, s = build()
        tee = p.inside(s["washer"], "t_shirt", soil={"dirt": 0.5})
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        ok(env, "wait", seconds=900)
        ok(env, "switch_off", s["washer"])
        w = ent(env, s["washer"])
        self.assertEqual(w["appliance"]["state"], "idle")
        self.assertFalse(w["last_cycle"]["completed"])
        self.assertGreaterEqual(ent(env, tee)["wetness"], 0.9)
        self.assertTrue(w["door_locked"])

    def test_cat_in_drum_is_injured_when_started_but_leaves_if_door_left_open(self):
        config, p, s = build(pets={"cat_1": {"label": "Miso", "species": "cat", "location": "laundry", "inside": None}})
        config["pets"]["cat_1"]["inside"] = s["washer"]
        p.inside(s["washer"], "t_shirt")
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        self.assertEqual(stat(env, "injury"), 1)
        self.assertIn("injury", env.evaluate()["failures"])
        config, p, s = build(pets={"cat_1": {"label": "Miso", "species": "cat", "location": "laundry"}})
        config["pets"]["cat_1"]["inside"] = s["washer"]
        env = env_of(config)
        ok(env, "approach", s["washer"])
        # the front loader's glass door shows the cat before the door is opened
        details = ok(env, "inspect", s["washer"])["message"]
        self.assertIn("occupant", details)
        ok(env, "open", s["washer"])
        # it climbs out once disturbed with the door open (immediately when the pets domain drives it)
        ok(env, "wait", seconds=180)
        self.assertIsNone(env.world.s["pets"]["cat_1"].get("inside"))
        ok(env, "close", s["washer"])
        run_washer(env, s["washer"], "quick")
        self.assertEqual(stat(env, "injury"), 0)

    def test_overstuffed_drum_catches_a_sleeve_and_leaks(self):
        config, p, s = build()
        towels = [p.inside(s["washer"], "bath_towel") for _ in range(6)]
        self.assertTrue(all(towels))
        env = env_of(config)
        ok(env, "approach", s["washer"])
        ok(env, "open", s["washer"])
        hold(env, towels[-1])
        ok(env, "place", towels[-1], s["washer"], relation="in")
        ok(env, "close", s["washer"])
        self.assertTrue(ent(env, towels[-1])["caught_in_door"])
        run_washer(env, s["washer"], "normal")
        self.assertGreaterEqual(stat(env, "spills"), 1)
        self.assertIn("torn", ent(env, towels[-1])["damage"])
        self.assertTrue(env.world.s["rooms"]["laundry"]["wet_cells"])

    def test_open_zipper_snags_a_delicate_but_a_closed_one_does_not(self):
        from house_sim.kernel.plugins import ACTIONS
        for closed in (False, True):
            config, p, s = build()
            jeans = p.inside(s["washer"], "jeans", zipper={"closed_frac": 0.0, "meshed": False})
            blouse = p.inside(s["washer"], "blouse")
            if closed and "zip" not in ACTIONS:
                config["entities"][jeans]["zipper"]["closed_frac"] = 1.0
            env = env_of(config)
            if closed and "zip" in ACTIONS:      # manipulation's zip verb acts on the shared zipper field
                ok(env, "approach", s["washer"])
                ok(env, "open", s["washer"])
                ok(env, "zip", jeans)
                self.assertGreaterEqual(ent(env, jeans)["zipper"]["closed_frac"], 0.9)
            run_washer(env, s["washer"], "perm_press", temp=30)
            wait_done(env, s["washer"])
            self.assertEqual("snagged" in ent(env, blouse).get("damage", {}), not closed)

    def test_pen_left_in_pocket_inks_the_whole_load(self):
        from house_sim.systems.textiles_laundry.helpers import pockets
        config, p, s = build()
        jeans = p.inside(s["washer"], "jeans")
        shirt = p.inside(s["washer"], "dress_shirt")

        class Ctx:
            pass
        ctx = Ctx()
        ctx.config, ctx.placer = config, p
        pockets(ctx, jeans, contents=[{"pocket": "back_right", "label": "ballpoint pen", "laundry_hazard": "ink", "flat": True}],
                empty=["front_left"])
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        wait_done(env, s["washer"])
        self.assertGreaterEqual(M.soil_level(ent(env, shirt), "stain_ink"), 0.3)
        self.assertGreaterEqual(M.soil_level(ent(env, jeans), "stain_ink"), 0.3)

    def test_dispensed_softener_clogs_microfiber_and_scents(self):
        config, p, s = build()
        cloth = p.inside(s["washer"], "workout_towel")
        tee = p.inside(s["washer"], "t_shirt")
        config["entities"][s["softener"]].update(liquid="fabric_softener", liquid_l=0.05)
        config["entities"][s["main"]].update(liquid="laundry_detergent_he", liquid_l=0.03)
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        wait_done(env, s["washer"])
        self.assertTrue(ent(env, cloth)["softener_residue"])
        self.assertTrue(ent(env, tee)["scented"])
        self.assertEqual(ent(env, s["softener"])["liquid_l"], 0.0)
        self.assertEqual(ent(env, s["washer"])["last_cycle"]["softener"], "ok")
        # overfilled past MAX: siphons in the main wash, no softening
        config, p, s = build()
        tee = p.inside(s["washer"], "t_shirt")
        config["entities"][s["softener"]].update(liquid="fabric_softener", liquid_l=0.115)
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        wait_done(env, s["washer"])
        self.assertEqual(ent(env, s["washer"])["last_cycle"]["softener"], "siphoned")
        self.assertFalse(ent(env, tee).get("softened"))

    def test_chlorine_bleach_damages_colours_not_bleach_safe_whites_and_mixing_with_vinegar_gasses(self):
        config, p, s = build()
        colour = p.inside(s["washer"], "t_shirt", color="blue", color_class="color")
        white = p.inside(s["washer"], "bath_towel", care={"wash": "machine", "wash_max_c": 60, "gentle": 0, "bleach": "any", "tumble": "high", "dry": "tumble", "iron": "none"},
                         soil={"mildew": 0.3})
        config["entities"][s["bleach"]].update(liquid="chlorine_bleach", liquid_l=0.08)
        config["entities"][s["main"]].update(liquid="laundry_detergent_he", liquid_l=0.035)
        env = env_of(config)
        run_washer(env, s["washer"], "normal", temp=60)
        wait_done(env, s["washer"])
        self.assertIn("bleach_damage", ent(env, colour)["damage"])
        self.assertNotIn("bleach_damage", ent(env, white).get("damage", {}))
        self.assertLess(M.soil_level(ent(env, white), "mildew"), 0.05)
        config, p, s = build()
        p.inside(s["washer"], "bath_towel")
        config["entities"][s["bleach"]].update(liquid="chlorine_bleach", liquid_l=0.08)
        config["entities"][s["washer"]].update(liquid="vinegar", liquid_l=0.2)
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        self.assertEqual(stat(env, "gas_exposure"), 1)

    def test_single_heavy_item_unbalances_but_towels_balance_it(self):
        for balanced in (False, True):
            config, p, s = build()
            self.assertTrue(p.inside(s["washer"], "bath_mat", mass_kg=1.6, size_m=[0.45, 0.3, 0.2]))
            if balanced:
                for _ in range(3):
                    p.inside(s["washer"], "bath_towel")
            env = env_of(config)
            run_washer(env, s["washer"], "normal", temp=30)
            wait_done(env, s["washer"])
            err = ent(env, s["washer"])["last_cycle"]["error"]
            self.assertEqual(err == "UE", not balanced)

    def test_regular_detergent_oversudses_an_he_machine(self):
        config, p, s = build()
        tee = p.inside(s["washer"], "t_shirt")
        config["entities"][s["main"]].update(liquid="laundry_detergent", liquid_l=0.06)
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        wait_done(env, s["washer"])
        self.assertTrue(ent(env, s["washer"])["last_cycle"]["suds"])
        self.assertGreater(M.soil_level(ent(env, tee), "detergent_residue"), 0.1)

    def test_wet_laundry_left_in_closed_washer_sours_but_hung_laundry_dries(self):
        config, p, s = build()
        tee = p.inside(s["washer"], "t_shirt", wetness=0.4)
        env = env_of(config)
        ok(env, "wait", seconds=9 * 3600)
        self.assertTrue(ent(env, tee)["musty"])
        self.assertIsInstance(ent(env, tee)["odor"], dict)          # CONVENTIONS 5: odor is {kind: intensity}
        self.assertGreater(ent(env, tee)["odor"]["musty"], 0.3)
        config, p, s = build()
        tee = p.inside(s["washer"], "t_shirt", wetness=0.4)
        env = env_of(config)
        set_up_rack(env, s["rack"])
        ok(env, "approach", s["washer"])
        ok(env, "open", s["washer"])
        hold(env, tee)
        ok(env, "approach", s["rack"])
        ok(env, "hang_dry", tee, s["rack"], method="hang", side="left")
        ok(env, "wait", seconds=4 * 3600)
        self.assertLess(ent(env, tee)["wetness"], M.DRY)
        self.assertFalse(ent(env, tee).get("musty"))

    def test_tub_clean_with_clothes_inside_ruins_them_but_empty_drum_clears_biofilm(self):
        config, p, s = build()
        config["entities"][s["washer"]].update(_biofilm=0.6, odor=0.5, odor_kind="musty")
        env = env_of(config)
        self.assertEqual(ent(env, s["washer"])["odor"], {"musty": 0.5})   # a legacy float is converted at reset
        run_washer(env, s["washer"], "tub_clean")
        wait_done(env, s["washer"], limit=3 * 3600)
        self.assertLess(ent(env, s["washer"])["_biofilm"], 0.3)
        config, p, s = build()
        shirt = p.inside(s["washer"], "dress_shirt", color="navy", color_class="dark")
        config["entities"][s["bleach"]].update(liquid="chlorine_bleach", liquid_l=0.08)
        env = env_of(config)
        run_washer(env, s["washer"], "tub_clean")
        wait_done(env, s["washer"], limit=3 * 3600)
        self.assertIn("bleach_damage", ent(env, shirt)["damage"])


class DryerTests(unittest.TestCase):
    def test_sensor_dry_dries_but_heat_sets_a_remaining_stain(self):
        config, p, s = build()
        clean = p.inside(s["dryer"], "t_shirt", wetness=0.38)
        stained = p.inside(s["dryer"], "t_shirt", wetness=0.38, soil={"stain_wine": 0.3})
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "switch_on", s["dryer"])
        ok(env, "set_mode", s["dryer"], mode="sensor_normal")
        ok(env, "start", s["dryer"])
        wait_done(env, s["dryer"])
        self.assertLess(ent(env, clean)["wetness"], 0.05)
        self.assertNotIn("set_stain", ent(env, clean).get("damage", {}))
        self.assertIn("stain_wine", ent(env, stained)["set_stains"])
        self.assertLess(ent(env, s["dryer"])["last_cycle"]["overdry_min"], 15)

    def test_heat_beyond_the_label_felts_wool_and_crumbles_rubber_backing(self):
        for heat, expect in (("high", True), ("low", False)):
            config, p, s = build()
            mat = p.inside(s["dryer"], "bath_mat", wetness=0.5, size_m=[0.45, 0.3, 0.2], laid_flat=False)
            env = env_of(config)
            ok(env, "approach", s["dryer"])
            ok(env, "switch_on", s["dryer"])
            ok(env, "set_mode", s["dryer"], mode="sensor_normal")
            ok(env, "set", s["dryer"], setting="heat", value=heat)
            ok(env, "start", s["dryer"])
            wait_done(env, s["dryer"])
            self.assertEqual("heat_damaged_backing" in ent(env, mat).get("damage", {}), expect)
        config, p, s = build()
        sweater = p.inside(s["dryer"], "sweater", wetness=0.4)
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "switch_on", s["dryer"])
        ok(env, "set_mode", s["dryer"], mode="heavy_duty")
        ok(env, "start", s["dryer"])
        wait_done(env, s["dryer"])
        self.assertIn("felted", ent(env, sweater)["damage"])

    def test_clogged_lint_screen_overheats_into_fire_and_clean_screen_does_not(self):
        for lint, expect_fire in ((1.0, True), (0.0, False)):
            config, p, s = build()
            config["entities"][s["lint_screen"]]["soil"] = {"lint": lint}
            for _ in range(4):
                p.inside(s["dryer"], "bath_towel", wetness=0.45)
            env = env_of(config)
            ok(env, "approach", s["dryer"])
            ok(env, "switch_on", s["dryer"])
            ok(env, "set_mode", s["dryer"], mode="heavy_duty")
            ok(env, "start", s["dryer"])
            ok(env, "wait", seconds=1800)
            self.assertEqual(stat(env, "fire") > 0, expect_fire)

    def test_lint_peeled_off_a_removed_screen_goes_to_trash_but_wiping_in_place_drops_it_in_the_slot(self):
        config, p, s = build()
        trash = p.floor_item("laundry", "generic_item", label="trash can", container=True, accepts_items=True, capacity_l=30.0,
                             waste_stream="trash", size_m=[0.3, 0.3, 0.4], inner_size_m=[0.28, 0.28, 0.38], portable=False)
        config["entities"][s["lint_screen"]]["soil"] = {"lint": 0.7}
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "open", s["dryer"])
        ok(env, "lint_roll", s["lint_screen"])
        self.assertGreater(M.soil_level(ent(env, s["dryer"]), "lint"), 0.3)
        config["entities"][s["lint_screen"]]["soil"] = {"lint": 0.7}
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "open", s["dryer"])
        ok(env, "uninstall", s["dryer"], slot="lint_screen")
        ok(env, "approach", trash)
        ok(env, "lint_roll", s["lint_screen"], into=trash)
        self.assertEqual(M.soil_level(ent(env, s["lint_screen"]), "lint"), 0.0)
        self.assertTrue(any(e.get("location") == trash and e.get("type") == "lint_wad" for e in env.world.s["entities"].values()))
        self.assertEqual(M.soil_level(ent(env, s["dryer"]), "lint"), 0.0)

    def test_oily_rags_self_heat_into_a_fire(self):
        config, p, s = build()
        for _ in range(2):
            p.inside(s["dryer"], "kitchen_towel", wetness=0.3, soil={"stain_oil": 0.5})
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "switch_on", s["dryer"])
        ok(env, "set_mode", s["dryer"], mode="timed_dry")
        ok(env, "set", s["dryer"], setting="minutes", value=40)
        ok(env, "set", s["dryer"], setting="heat", value="high")
        ok(env, "start", s["dryer"])
        ok(env, "wait", seconds=1800)
        self.assertGreater(stat(env, "fire"), 0)

    def test_dryer_sheets_scent_the_load_and_leave_residue_on_microfiber(self):
        config, p, s = build()
        cloth = p.inside(s["dryer"], "workout_towel", wetness=0.3)
        tee = p.inside(s["dryer"], "t_shirt", wetness=0.3)
        sheet = s["dryer_sheets"][0]
        config["entities"][sheet].update(location=s["dryer"], relation="in")
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "switch_on", s["dryer"])
        ok(env, "set_mode", s["dryer"], mode="delicate")
        ok(env, "start", s["dryer"])
        wait_done(env, s["dryer"])
        self.assertTrue(ent(env, tee)["scented"])
        self.assertIn("softener_residue", ent(env, cloth)["damage"])

    def test_load_left_in_a_finished_dryer_wrinkles_but_prompt_removal_does_not(self):
        for delay, expect in ((3600, True), (60, False)):
            config, p, s = build()
            shirt = p.inside(s["dryer"], "dress_shirt", wetness=0.3, wrinkled=0.3)
            env = env_of(config)
            ok(env, "approach", s["dryer"])
            ok(env, "switch_on", s["dryer"])
            ok(env, "set_mode", s["dryer"], mode="perm_press")
            ok(env, "start", s["dryer"])
            waited = 0
            while ent(env, s["dryer"])["appliance"]["state"] == "running" and waited < 9000:
                ok(env, "wait", seconds=60)
                waited += 60
            before = ent(env, shirt)["wrinkled"]
            ok(env, "wait", seconds=delay)
            self.assertEqual(ent(env, shirt)["wrinkled"] > before + 0.2, expect)

    def test_opening_the_dryer_pauses_it(self):
        config, p, s = build()
        p.inside(s["dryer"], "t_shirt", wetness=0.3)
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "switch_on", s["dryer"])
        ok(env, "start", s["dryer"])
        ok(env, "wait", seconds=300)
        ok(env, "open", s["dryer"])
        self.assertEqual(ent(env, s["dryer"])["appliance"]["state"], "paused")
        self.assertIsNone(ent(env, s["dryer"]).get("internal_temp_c"))


class AirDryingTests(unittest.TestCase):
    def test_knits_dried_flat_keep_shape_but_hung_wet_knits_stretch(self):
        for method, expect in (("flat", False), ("hang", True)):
            config, p, s = build()
            sweater = p.on(s["table"], "sweater", wetness=0.6)
            env = env_of(config)
            set_up_rack(env, s["rack"])
            hold(env, sweater)
            ok(env, "approach", s["rack"])
            ok(env, "hang_dry", sweater, s["rack"], method=method)
            wait_long(env, 48 * 3600)
            self.assertEqual("stretched" in ent(env, sweater).get("damage", {}), expect)
            self.assertLess(ent(env, sweater)["wetness"], 0.1)

    def test_damp_towel_balled_in_a_closed_hamper_mildews_and_wets_neighbours(self):
        config, p, s = build()
        hamper = p.floor_item("laundry", "hamper", open=False)
        towel = p.inside(hamper, "bath_towel", wetness=0.8)
        tee = p.inside(hamper, "t_shirt", wetness=0.0)
        env = env_of(config)
        wait_long(env, 30 * 3600)
        self.assertTrue(ent(env, towel)["musty"])
        self.assertGreater(M.soil_level(ent(env, towel), "mildew"), 0.05)
        self.assertGreater(ent(env, tee)["wetness"], 0.1)

    def test_clothesline_needs_enough_pins_for_the_wind(self):
        for pins, blown in ((1, True), (6, False)):
            config, p, s = build(weather={"condition": "clear", "temperature_c": 22.0, "wind_mps": 7.0})
            line = p.mount("yard", "clothesline", height=1.8, cell=[2, 2])
            bag = p.hang(line, "clothespin_bag", secured=True)     # pin bags hook onto the line (a loose one blows away)
            sheet = p.on(s["table"], "flat_sheet", wetness=0.4)
            env = env_of(config)
            hold(env, sheet, hand="both")
            ok(env, "move", "hall")
            ok(env, "move", "yard")
            ok(env, "walk_to", cell=[2, 3], adjacent=True)
            ok(env, "hang_dry", sheet, line, method="pinned", pins=pins, pin_at="edge", pin_bag=bag)
            ok(env, "wait", seconds=600)
            rec = ent(env, sheet)
            self.assertEqual(bool(rec.get("blown_down")), blown)
            if blown:
                self.assertGreater(M.soil_level(rec, "dirt") + M.soil_level(rec, "mud"), 0.2)

    def test_unpinned_laundry_on_a_windy_line_ends_up_dirty_on_the_ground(self):
        # Whichever wind model moves it (this domain's pin model, or a generic one from another domain),
        # a sheet draped over the line without pins lands on the ground dirty and creased.
        config, p, s = build(weather={"condition": "clear", "temperature_c": 22.0, "wind_mps": 9.0})
        line = p.mount("yard", "clothesline", height=1.8, cell=[2, 2])
        sheet = p.on(s["table"], "flat_sheet", wetness=0.4)
        env = env_of(config)
        hold(env, sheet, hand="both")
        ok(env, "move", "hall")
        ok(env, "move", "yard")
        ok(env, "walk_to", cell=[2, 3], adjacent=True)
        ok(env, "hang_dry", sheet, line, method="hang")
        ok(env, "wait", seconds=900)
        rec = ent(env, sheet)
        self.assertTrue(rec.get("blown_down"))
        self.assertNotEqual(rec.get("location"), line)
        self.assertGreater(M.soil_level(rec, "dirt") + M.soil_level(rec, "mud"), 0.2)
        self.assertGreaterEqual(rec.get("wrinkled", 0.0), 0.6)

    def test_rain_wets_laundry_left_outside(self):
        config, p, s = build()
        config["events"] = [{"at_s": 1200, "changes": {"weather.condition": "rain"}}]
        line = p.mount("yard", "clothesline", height=1.8, cell=[2, 2])
        towel = p.hang(line, "beach_towel", wetness=0.1, drying_mode="hung", pins=3)
        env = env_of(config)
        ok(env, "wait", seconds=3600)
        self.assertGreater(ent(env, towel)["wetness"], 0.9)
        self.assertTrue(ent(env, towel).get("rained_on"))

    def test_dripping_laundry_over_hardwood_marks_the_floor_and_is_seen(self):
        config, p, s = build()
        rack = s["rack"]
        config["entities"][rack].update(location="hall", relation="on", pos=[0, 2])
        shirt = p.on(s["table"], "dress_shirt", wetness=1.0)
        env = env_of(config)
        hold(env, shirt)
        ok(env, "move", "hall")
        ok(env, "approach", rack)
        ok(env, "open", rack)
        ok(env, "hang_dry", shirt, rack, method="hang")
        obs = env.observe()
        self.assertEqual(obs["visible_entities"][shirt]["appearance"], "dripping wet")
        ok(env, "wait", seconds=120)
        self.assertIn("water_damage", ent(env, "floor_hall").get("damage", {}))
        self.assertTrue(env.world.s["rooms"]["hall"]["wet_cells"])
        self.assertTrue(ent(env, rack)["blocks_egress"])

    def test_one_sided_rack_tips(self):
        config, p, s = build()
        towels = [p.on(s["table"], "bath_towel", wetness=0.5) for _ in range(3)]
        env = env_of(config)
        set_up_rack(env, s["rack"])
        for t in towels:
            hold(env, t)
            ok(env, "approach", s["rack"])
            ok(env, "hang_dry", t, s["rack"], method="hang", side="left")
        self.assertTrue(ent(env, s["rack"])["tipped"])
        self.assertTrue(all(ent(env, t)["location"] == "laundry" for t in towels))

    def test_thick_seams_stay_damp_after_the_surface_feels_dry(self):
        config, p, s = build()
        jeans = p.on(s["table"], "jeans", wetness=0.4, _core_wetness=0.5)
        env = env_of(config)
        set_up_rack(env, s["rack"])
        hold(env, jeans)
        ok(env, "approach", s["rack"])
        ok(env, "hang_dry", jeans, s["rack"], method="hang")
        # The room's humidity is evolved by the climate domain when it is loaded (a closed laundry room on a summer
        # day settles near 60 % RH, which slows drying), so wait until the surface feels nearly dry (<= 16 h).
        waited = 0
        while ent(env, jeans)["wetness"] >= 0.1 and waited < 16 * 3600:
            ok(env, "wait", seconds=1800)
            waited += 1800
        rec = ent(env, jeans)
        self.assertGreaterEqual(waited, 4 * 3600)       # jeans do not air-dry in minutes
        self.assertLess(rec["wetness"], 0.1)
        self.assertGreater(rec["_core_wetness"], rec["wetness"])
        quick = ok(env, "inspect", jeans)["message"]
        close = ok(env, "inspect", jeans, where="closely")["message"]
        self.assertNotIn("seam_wetness", quick)
        self.assertIn("seam_wetness", close)


class FoldTests(unittest.TestCase):
    def test_folding_shrinks_the_footprint_so_more_fits_a_drawer(self):
        config, p, s = build()
        tees = [p.on(s["table"], "t_shirt") for _ in range(2)]
        env = env_of(config)
        ok(env, "approach", s["table"])
        before = list(ent(env, tees[0])["size_m"])
        ok(env, "fold", tees[0], style="retail")
        after = ent(env, tees[0])["size_m"]
        self.assertLess(after[0] * after[1] * after[2], before[0] * before[1] * before[2])
        self.assertEqual(ent(env, tees[0])["folded"], "retail")
        no(env, "fold", tees[1], style="fitted_nest")

    def test_fitted_sheet_folded_without_nesting_is_lumpy_and_bigger(self):
        dims = {}
        for style in ("fitted_nest", "half_half"):
            config, p, s = build()
            sheet = p.on(s["table"], "fitted_sheet")
            env = env_of(config)
            ok(env, "approach", s["table"])
            if style == "half_half":
                with self.assertRaises(AssertionError):
                    ok(env, "fold", sheet, style="quarters")
            ok(env, "fold", sheet, style=style if style == "fitted_nest" else "thirds_thirds")
            dims[style] = ent(env, sheet)["size_m"]
            self.assertEqual(ent(env, sheet)["fold_quality"]["lumpy"], style != "fitted_nest")
        self.assertLess(dims["fitted_nest"][0] * dims["fitted_nest"][1], dims["half_half"][0] * dims["half_half"][1])

    def test_large_items_need_a_surface_and_floor_folding_soils_them(self):
        config, p, s = build()
        sheet = p.on(s["table"], "flat_sheet")
        sheet2 = p.on(s["table"], "flat_sheet")
        blanket = p.floor_item("laundry", "throw_blanket")
        bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
        env = env_of(config)
        hold(env, sheet, hand="both")
        no(env, "fold", sheet, style="quarters")
        ok(env, "place", sheet, s["table"])
        ok(env, "fold", sheet2, style="quarters")
        self.assertIn("floor_contact", ent(env, sheet2))   # a queen sheet overhangs a small table onto the floor
        ok(env, "pick_up", sheet, hand="both")
        ok(env, "move", "hall")
        ok(env, "move", "bedroom")
        ok(env, "approach", bed)
        ok(env, "place", sheet, bed)
        ok(env, "fold", sheet, style="quarters")
        self.assertNotIn("floor_contact", ent(env, sheet))
        ok(env, "move", "hall")
        ok(env, "move", "laundry")
        ok(env, "approach", blanket)
        ok(env, "fold", blanket, style="thirds_half")
        self.assertIn("floor_contact", ent(env, blanket))

    def test_same_style_stacks_but_a_larger_fold_does_not(self):
        config, p, s = build()
        a = p.on(s["table"], "bath_towel")
        b = p.on(s["table"], "bath_towel")
        c = p.on(s["table"], "bath_towel")
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "fold", a, style="thirds_half")
        ok(env, "fold", b, style="thirds_half")
        ok(env, "fold", c, style="half_half")
        ok(env, "pick_up", b)
        ok(env, "place", b, a)
        ok(env, "pick_up", c)
        result = no(env, "place", c, b)
        self.assertIn("space", result["message"])

    def test_drawer_stack_height_and_capacity_are_enforced(self):
        config, p, s = build()
        drawer = p.mount("laundry", "drawer", height=0.75, open=True, inner_size_m=[0.45, 0.45, 0.06], capacity_l=12.0)
        tees = [p.on(s["table"], "t_shirt") for _ in range(4)]
        env = env_of(config)
        ok(env, "approach", s["table"])
        for t in tees:
            ok(env, "fold", t, style="retail")
        ok(env, "pick_up", tees[0])
        ok(env, "approach", drawer)
        ok(env, "place", tees[0], drawer, relation="in")
        placed = 1
        refused = None
        for t in tees[1:]:
            ok(env, "approach", s["table"])
            ok(env, "pick_up", t)
            ok(env, "approach", drawer)
            result = env.step("place", t, tees[0])
            if not result["ok"]:
                refused = result["message"]
                break
            placed += 1
        self.assertIsNotNone(refused)
        self.assertLess(placed, 4)

    def test_damp_folded_towel_sours_and_dry_one_does_not(self):
        for wet, expect in ((0.3, True), (0.0, False)):
            config, p, s = build()
            towel = p.on(s["table"], "bath_towel", wetness=wet)
            env = env_of(config)
            ok(env, "approach", s["table"])
            ok(env, "fold", towel, style="thirds_half")
            ok(env, "wait", seconds=14 * 3600)
            self.assertEqual(bool(ent(env, towel).get("musty")), expect)

    def test_molded_bra_must_be_nested_not_folded(self):
        config, p, s = build()
        bra = p.on(s["table"], "bra")
        bra2 = p.on(s["table"], "bra")
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "fold", bra, style="nest")
        self.assertNotIn("cups_crushed", ent(env, bra).get("damage", {}))
        ok(env, "fold", bra2, style="half")
        self.assertIn("cups_crushed", ent(env, bra2)["damage"])

    def test_items_on_a_blanket_get_folded_inside(self):
        config, p, s = build()
        throw = p.on(s["table"], "throw_blanket")
        remote = p.on(throw, "generic_item", label="TV remote", size_m=[0.05, 0.18, 0.02])
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "fold", throw, style="thirds_half")
        self.assertEqual(ent(env, remote)["relation"], "under")
        self.assertNotIn(remote, env.observe()["visible_entities"])


class HangerAndPairTests(unittest.TestCase):
    def _hang(self, kind, hanger_type, hours, **garment_props):
        config, p, s = build()
        closet = p.furniture("bedroom", "wardrobe", open=True)
        garment = p.on(s["table"], kind, **garment_props)
        hanger = p.on(s["table"], hanger_type)
        env = env_of(config)
        hold(env, garment)
        ok(env, "hang_on_hanger", garment, hanger=hanger)
        ok(env, "move", "hall")
        ok(env, "move", "bedroom")
        ok(env, "approach", closet)
        ok(env, "place", garment, closet, relation="hung")
        ok(env, "wait", seconds=int(hours * 3600))
        return env, garment, hanger, closet

    def test_silk_slides_off_a_wire_hanger_but_stays_on_velvet(self):
        env, blouse, _, closet = self._hang("blouse", "wire_hanger", 4)
        self.assertTrue(ent(env, blouse).get("slipped_off"))
        self.assertEqual(ent(env, blouse)["relation"], "in")
        env, blouse, _, _ = self._hang("blouse", "velvet_hanger", 24)
        self.assertFalse(ent(env, blouse).get("slipped_off"))

    def test_heavy_coat_bends_a_wire_hanger_but_not_a_wooden_one(self):
        env, coat, hanger, _ = self._hang("coat", "wire_hanger", 2)
        self.assertIn("hanger_bumps", ent(env, coat)["damage"])
        self.assertTrue(ent(env, hanger)["broken"])
        env, coat, hanger, _ = self._hang("coat", "wooden_hanger", 24)
        self.assertNotIn("hanger_bumps", ent(env, coat).get("damage", {}))

    def test_long_gown_on_a_low_rod_drags_its_hem(self):
        config, p, s = build()
        low = p.mount("bedroom", "closet_rod", height=1.0, label="lower rod")
        high = p.mount("bedroom", "closet_rod", height=1.75, label="long-hang rod")
        gowns = [p.on(s["table"], "evening_gown"), p.on(s["table"], "evening_gown")]
        hangers = [p.on(s["table"], "padded_hanger"), p.on(s["table"], "padded_hanger")]
        env = env_of(config)
        for gown, hanger, rod in zip(gowns, hangers, (low, high)):
            if env.world.s["robot"]["room"] != "laundry":
                ok(env, "move", "hall")
                ok(env, "move", "laundry")
            hold(env, gown)
            ok(env, "hang_on_hanger", gown, hanger=hanger)
            ok(env, "move", "hall")
            ok(env, "move", "bedroom")
            ok(env, "approach", rod)
            ok(env, "place", gown, rod, relation="hung")
        self.assertTrue(ent(env, gowns[0])["hem_on_floor"])
        self.assertFalse(ent(env, gowns[1])["hem_on_floor"])

    def test_pairing_binds_socks_and_mismatch_is_detectable(self):
        config, p, s = build()
        a1 = p.on(s["table"], "sock", pair_id="p1", size="L")
        a2 = p.on(s["table"], "sock", pair_id="p1", size="L")
        b1 = p.on(s["table"], "sock", pair_id="p2", size="S")
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "pick_up", a1)
        ok(env, "pair", a1, a2)
        self.assertEqual(ent(env, a2)["location"], a1)
        no(env, "pick_up", a2)
        ok(env, "place", a1, s["table"])
        self.assertEqual(ent(env, a2)["location"], a1)
        self.assertTrue(check(env.world.s, {"path": "entities.{}.pair_id".format(a2), "op": "eq", "ref": "entities.{}.pair_id".format(a1)}))
        ok(env, "pick_up", a1)
        ok(env, "pair", a1, split=True)
        ok(env, "place", a2, s["table"])
        ok(env, "pair", a1, b1)
        self.assertFalse(check(env.world.s, {"path": "entities.{}.pair_id".format(b1), "op": "eq", "ref": "entities.{}.pair_id".format(a1)}))
        self.assertNotIn("pair_id", env.observe()["visible_entities"][a2])


class BeddingTests(unittest.TestCase):
    def _bare(self, size="queen"):
        from house_sim.systems.textiles_laundry.helpers import bed_helper, linens

        class Ctx:
            pass
        config, p, s = build()
        bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
        nightstand = p.furniture("bedroom", "nightstand")
        ctx = Ctx()
        ctx.config, ctx.placer, ctx.rng = config, p, random.Random(0)
        bed_helper(ctx, bed=bed, state="bare")
        out = linens(ctx, at=nightstand if False else s["table"], size=size, items=["fitted", "flat", "case"], folded=False)
        return config, p, s, bed, out

    def test_fitted_sheet_of_the_right_size_holds_a_night_but_a_twin_sheet_pops_off(self):
        for size, expect_corners in (("queen", 4), ("twin", 2)):
            config, p, s, bed, lin = self._bare(size)
            env = env_of(config)
            hold(env, lin["fitted"], hand="both")
            ok(env, "move", "hall")
            ok(env, "move", "bedroom")
            ok(env, "approach", bed)
            ok(env, "dress_bed", lin["fitted"], bed, face_up="finished", orientation="lengthwise")
            self.assertEqual(ent(env, lin["fitted"])["corners_seated"], expect_corners)
            ok(env, "wait", seconds=8 * 3600)
            popped = bool(ent(env, lin["fitted"]).get("popped_off"))
            self.assertEqual(popped, size == "twin")

    def test_crosswise_or_seams_up_is_recorded(self):
        config, p, s, bed, lin = self._bare()
        env = env_of(config)
        hold(env, lin["fitted"], hand="both")
        ok(env, "move", "hall")
        ok(env, "move", "bedroom")
        ok(env, "approach", bed)
        no(env, "dress_bed", lin["fitted"], bed)
        ok(env, "dress_bed", lin["fitted"], bed, face_up="back", orientation="crosswise")
        rec = ent(env, lin["fitted"])
        self.assertEqual(rec["corners_seated"], 2)
        self.assertEqual(rec["face_up"], "back")

    def test_making_a_bed_buries_a_phone_left_under_the_duvet(self):
        from house_sim.systems.textiles_laundry.helpers import bed_helper

        class Ctx:
            pass
        for remove_first in (False, True):
            config, p, s = build()
            bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
            stand = p.furniture("bedroom", "nightstand")
            ctx = Ctx()
            ctx.config, ctx.placer, ctx.rng = config, p, random.Random(0)
            out = bed_helper(ctx, bed=bed, state="slept_in")
            phone = p.make("generic_item", out["cover"], label="phone", category="electronics", size_m=[0.07, 0.15, 0.01])
            config["entities"][phone]["relation"] = "under"
            env = env_of(config)
            ok(env, "move", "hall")
            ok(env, "move", "bedroom")
            ok(env, "approach", bed)
            self.assertNotIn(phone, env.observe()["visible_entities"])
            if remove_first:
                ok(env, "inspect", out["cover"], where="under")
                ok(env, "pick_up", phone)
                ok(env, "approach", stand)
                ok(env, "place", phone, stand)
                ok(env, "approach", bed)
            ok(env, "make_bed", bed)
            self.assertEqual(bool(ent(env, phone).get("buried_in_bed")), not remove_first)
            self.assertTrue(ent(env, bed)["made"])
            self.assertLessEqual(ent(env, bed)["bed_state"]["wrinkle"], 0.1)

    def test_occupied_bed_is_refused(self):
        from house_sim.systems.textiles_laundry.helpers import bed_helper

        class Ctx:
            pass
        config, p, s = build()
        bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
        ctx = Ctx()
        ctx.config, ctx.placer, ctx.rng = config, p, random.Random(0)
        bed_helper(ctx, bed=bed, state="slept_in")
        config["people"]["resident_1"].update(location="bedroom", posture="sleeping", awake=False, in_bed=bed)
        env = env_of(config)
        ok(env, "move", "hall")
        ok(env, "move", "bedroom")
        ok(env, "approach", bed)
        self.assertEqual(no(env, "make_bed", bed)["category"], "social")
        self.assertEqual(no(env, "strip_bed", bed)["category"], "social")

    def test_strip_bed_sends_hidden_items_to_the_laundry_and_yanking_tears_elastic(self):
        from house_sim.systems.textiles_laundry.helpers import bed_helper

        class Ctx:
            pass
        config, p, s = build()
        bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
        # the basket stands beside the bed and the robot starts where it reaches both (layout-independent)
        room = config["rooms"]["bedroom"]
        state = {"rooms": config["rooms"], "entities": config["entities"], "connections": config["connections"], "people": {}, "pets": {},
                 "robot": {"room": None}}
        taken = set(geometry.blocked_cells(state, "bedroom", include_agents=False)) | geometry.door_clearance(state, "bedroom")
        bed_cells = geometry.footprint(config["entities"][bed])
        free = [c for c in sorted(geometry.near_cells(bed_cells, room)) if c not in taken and c not in bed_cells and geometry.in_grid(room, c)]
        clear = {(x, y) for x in range(8) for y in range(9) if (x, y) not in taken and (x, y) not in bed_cells and geometry.in_grid(room, (x, y))}
        spot = next((b, r) for b in free for r in free if (b[0] + 1, b[1]) in clear and r not in (b, (b[0] + 1, b[1]))
                    and geometry.chebyshev(b, r) <= 1)
        basket = p.floor_item("bedroom", "laundry_basket", cell=list(spot[0]))
        config["robot"] = {"room": "bedroom", "pos": list(spot[1])}
        ctx = Ctx()
        ctx.config, ctx.placer, ctx.rng = config, p, random.Random(0)
        out = bed_helper(ctx, bed=bed, state="slept_in", protector=True)
        aid = p.make("generic_item", out["case_1"], label="hearing aid", category="electronics", size_m=[0.02, 0.02, 0.01], value=900.0)
        config["entities"][aid]["relation"] = "in"
        env = env_of(config)
        self.assertIsNone(geometry.reach_problem(env.world.s, bed))
        self.assertIsNone(geometry.reach_problem(env.world.s, basket))
        ok(env, "strip_bed", bed, into=basket, keep=["protector"], method="yank")
        self.assertEqual(ent(env, out["fitted"])["location"], basket)
        self.assertIn("elastic_torn", ent(env, out["fitted"])["damage"])
        self.assertEqual(ent(env, out["protector"])["location"], bed)
        self.assertTrue(ent(env, aid)["in_laundry"])
        self.assertEqual(ent(env, out["insert"])["location"], bed)

    def test_duvet_inside_out_method_fills_evenly_and_stuffing_bunches(self):
        from house_sim.systems.textiles_laundry.helpers import linens

        class Ctx:
            pass
        for method, even in (("inside_out", True), ("stuff", False)):
            config, p, s = build()
            bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
            ctx = Ctx()
            ctx.config, ctx.placer, ctx.rng = config, p, random.Random(0)
            lin = linens(ctx, at=bed, items=["insert", "cover"], folded=False)
            config["entities"][lin["insert"]].update(spread_on=bed, size_m=[2.29, 2.18, 0.05])
            config["entities"][lin["insert"]].pop("spos", None)
            env = env_of(config)
            ok(env, "move", "hall")
            ok(env, "move", "bedroom")
            ok(env, "approach", bed)
            ok(env, "dress_bed", lin["cover"], lin["insert"], method=method, orientation="lengthwise")
            cover = ent(env, lin["cover"])
            self.assertEqual(cover["filled_with"], lin["insert"])
            self.assertEqual(cover["fill_evenness"] >= 0.9, even)
            self.assertEqual(cover["corners_matched"] == 4, even)
            self.assertEqual(ent(env, lin["insert"])["location"], lin["cover"])

    def test_pillow_protector_must_go_under_the_case(self):
        config, p, s = build()
        pillow = p.on(s["table"], "bed_pillow")
        case = p.on(s["table"], "pillowcase")
        prot = p.on(s["table"], "pillow_protector")
        env = env_of(config)
        hold(env, case)
        ok(env, "dress_bed", case, pillow, method="invert")
        ok(env, "pick_up", prot)
        no(env, "dress_bed", prot, pillow)
        self.assertEqual(ent(env, pillow)["case"], case)
        self.assertTrue(ent(env, case)["corners_filled"])


class IronSteamTests(unittest.TestCase):
    def _ironing(self, kind, **props):
        """An ironing station: board set up, iron resting on it (never carried around while plugged in)."""
        from house_sim.systems.textiles_laundry.helpers import station
        config, p, s = build()
        s.update(station(Ctx(config, p), need=["iron", "board"], iron_on_board=True))
        config["entities"][s["iron"]]["plugged_in"] = True
        garment = p.on(s["board"], kind, wrinkled=0.7, **props)
        env = env_of(config)
        ok(env, "approach", s["board"])
        ok(env, "pick_up", s["iron"])
        return env, s, garment

    def test_iron_heats_to_the_dial_and_smooths_cotton(self):
        env, s, shirt = self._ironing("dress_shirt")
        ok(env, "switch_on", s["iron"])
        ok(env, "set", s["iron"], setting="temp", value="high")
        ok(env, "wait", seconds=120)
        self.assertGreaterEqual(ent(env, s["iron"])["temperature_c"], 195)
        ok(env, "iron", shirt)
        self.assertLessEqual(ent(env, shirt)["wrinkled"], 0.05)
        self.assertEqual(ent(env, shirt).get("damage", {}), {})
        ok(env, "switch_off", s["iron"])
        ok(env, "place", s["iron"], s["board"], orientation="upright")
        self.assertNotIn("iron_left_on", env.evaluate()["failures"])

    def test_cotton_setting_melts_a_polyester_blouse_and_a_cold_iron_does_little(self):
        env, s, blouse = self._ironing("skirt")
        ok(env, "switch_on", s["iron"])
        ok(env, "set", s["iron"], setting="temp", value="high")
        ok(env, "wait", seconds=120)
        ok(env, "iron", blouse)
        self.assertIn("melted", ent(env, blouse)["damage"])
        env, s, shirt = self._ironing("dress_shirt")
        ok(env, "switch_on", s["iron"])
        ok(env, "set", s["iron"], setting="temp", value="high")
        ok(env, "iron", shirt)
        self.assertGreater(ent(env, shirt)["wrinkled"], 0.2)

    def test_hot_iron_left_face_down_scorches_without_auto_off(self):
        env, s, shirt = self._ironing("dress_shirt")
        env.world.s["entities"][s["iron"]]["auto_off"] = False
        ok(env, "switch_on", s["iron"])
        ok(env, "set", s["iron"], setting="temp", value="high")
        ok(env, "wait", seconds=120)
        ok(env, "place", s["iron"], s["board"], orientation="flat")
        ok(env, "wait", seconds=300)
        self.assertIn("scorched", ent(env, s["board"]).get("damage", {}))
        self.assertIn("iron_left_on", env.evaluate()["failures"])

    def test_leaving_the_room_with_the_iron_on_is_a_safety_violation(self):
        env, s, shirt = self._ironing("dress_shirt")
        ok(env, "switch_on", s["iron"])
        ok(env, "wait", seconds=120)
        ok(env, "place", s["iron"], s["board"], orientation="upright")
        ok(env, "move", "hall")
        ok(env, "wait", seconds=60)
        self.assertGreaterEqual(stat(env, "safety_violations"), 1)

    def test_steaming_a_worn_shirt_burns_and_suede_is_damaged(self):
        config, p, s = build(people={"resident_1": {"label": "Alex", "role": "adult", "location": "laundry", "pos": [3, 3]}})
        steamer = p.on(s["table"], "garment_steamer", plug_required=False, battery_powered=True, battery_level=1.0, liquid_l=0.2,
                       label="cordless garment steamer")
        worn = p.make("dress_shirt", "resident_1")
        config["entities"][worn]["relation"] = "worn"
        rod = p.mount("laundry", "closet_rod", height=1.6)
        jacket = p.hang(rod, "light_jacket", fabric="suede", wrinkled=0.5)
        env = env_of(config)
        hold(env, steamer)
        ok(env, "switch_on", steamer)
        ok(env, "wait", seconds=60)
        ok(env, "approach", "resident_1")
        ok(env, "steam", worn)
        self.assertGreaterEqual(stat(env, "burns"), 1)
        ok(env, "approach", rod)
        ok(env, "steam", jacket)
        self.assertIn("steam_damage", ent(env, jacket)["damage"])


class StainSoakTests(unittest.TestCase):
    def test_cold_enzyme_pretreatment_lifts_blood_but_hot_water_sets_it(self):
        for water, expect_set in (("cold", False), ("hot", True)):
            config, p, s = build()
            sheet = p.on(s["table"], "pillowcase", soil={"stain_blood": 0.5})
            env = env_of(config)
            ok(env, "approach", s["stain_remover"])
            ok(env, "pick_up", s["stain_remover"])
            ok(env, "approach", s["table"])
            ok(env, "treat_stain", sheet, agent=s["stain_remover"], method="blot", water=water)
            self.assertEqual("stain_blood" in ent(env, sheet).get("set_stains", []), expect_set)
            ok(env, "wait", seconds=600)
            ok(env, "place", s["stain_remover"], s["table"])
            ok(env, "pick_up", sheet)
            ok(env, "approach", s["washer"])
            ok(env, "open", s["washer"])
            ok(env, "place", sheet, s["washer"], relation="in")
            ok(env, "approach", s["pod_tub"])
            ok(env, "open", s["pod_tub"])
            ok(env, "pick_up", s["pods"][0])
            ok(env, "approach", s["washer"])
            ok(env, "place", s["pods"][0], s["washer"], relation="in")
            run_washer(env, s["washer"], "normal", temp=30)
            wait_done(env, s["washer"])
            level = M.soil_level(ent(env, sheet), "stain_blood")
            if expect_set:
                self.assertGreater(level, 0.2)
            else:
                self.assertLess(level, 0.05)

    def test_rubbing_spreads_and_a_remover_left_to_dry_rings(self):
        config, p, s = build()
        blouse = p.on(s["table"], "blouse", soil={"stain_coffee": 0.4})
        tee = p.on(s["table"], "t_shirt", soil={"stain_food": 0.4})
        env = env_of(config)
        ok(env, "approach", s["stain_remover"])
        ok(env, "pick_up", s["stain_remover"])
        ok(env, "approach", s["table"])
        ok(env, "treat_stain", blouse, agent=s["stain_remover"], method="rub")
        self.assertIn("abraded", ent(env, blouse)["damage"])
        self.assertIn("remover_ring", ent(env, blouse)["damage"])
        ok(env, "treat_stain", tee, agent=s["stain_remover"], method="blot")
        ok(env, "wait", seconds=4 * 3600)
        self.assertIn("remover_ring", ent(env, tee)["damage"])

    def test_spot_test_reveals_non_colourfast_fabric(self):
        config, p, s = build()
        shirt = p.on(s["table"], "t_shirt", colorfast=False, soil={"stain_wine": 0.3})
        booster = p.on(s["table"], "oxygen_laundry_booster")
        env = env_of(config)
        ok(env, "approach", booster)
        ok(env, "pick_up", booster)
        ok(env, "approach", s["table"])
        msg = ok(env, "treat_stain", shirt, agent=booster, spot_test=True)["message"]
        self.assertIn("not colourfast", msg)
        self.assertEqual(ent(env, shirt).get("damage", {}), {})
        ok(env, "treat_stain", shirt, agent=booster)
        self.assertIn("color_loss", ent(env, shirt)["damage"])

    def test_soak_in_a_basin_lifts_stains_but_a_new_red_shirt_dyes_whites(self):
        config, p, s = build()
        basin = p.floor_item("laundry", "wash_basin", liquid="water", liquid_l=10.0, liquid_temp_c=18.0)
        white = p.on(s["table"], "t_shirt", color="white", color_class="white", soil={"stain_grass": 0.5})
        red = p.on(s["table"], "t_shirt", color="red", color_class="red_bleeds", bleed=0.8)
        presoak = p.on(s["table"], "enzyme_presoak")
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "pick_up", white)
        ok(env, "pick_up", presoak)
        ok(env, "approach", basin)
        ok(env, "soak", white, basin, agent=presoak)
        ok(env, "approach", s["table"])
        ok(env, "place", presoak, s["table"])
        ok(env, "pick_up", red)
        ok(env, "approach", basin)
        ok(env, "soak", red, basin)
        ok(env, "wait", seconds=3 * 3600)
        self.assertLess(M.soil_level(ent(env, white), "stain_grass"), 0.25)
        self.assertIn("dye_transfer", ent(env, white)["damage"])

    def test_filling_a_sink_too_full_overflows(self):
        config, p, s = build()
        sink = p.furniture("laundry", "generic_item", label="utility sink", category="sink", water_source=True, drain=True,
                           liquid_container=True, liquid_l=0.0, capacity_l=20.0, accepts_items=True, container=True,
                           inner_size_m=[0.5, 0.4, 0.3], size_m=[0.6, 0.5, 0.9], portable=False, blocks_path=True)
        shirt = p.on(s["table"], "t_shirt")
        env = env_of(config)
        hold(env, shirt)
        ok(env, "approach", sink)
        ok(env, "soak", shirt, sink, water="cold", water_l=30.0)
        self.assertGreaterEqual(stat(env, "spills"), 1)
        self.assertEqual(ent(env, sink)["liquid_l"], 20.0)


class LintSewingShoeTests(unittest.TestCase):
    def test_lint_roller_must_cover_every_face_and_saturated_sheets_smear(self):
        config, p, s = build()
        roller = p.on(s["table"], "lint_roller")
        coat = p.on(s["table"], "suit_jacket", face_soil={"front": {"pet_hair": 0.5}, "back": {"pet_hair": 0.5}, "sleeves": {"pet_hair": 0.4}})
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "pick_up", roller)
        ok(env, "lint_roll", coat, faces=["front"])
        self.assertGreaterEqual(M.soil_level(ent(env, coat), "pet_hair"), 0.4)
        env.world.s["entities"][roller]["sheet_load"] = 0.9
        ok(env, "lint_roll", coat, faces=["back"])
        self.assertGreaterEqual(ent(env, coat)["face_soil"]["back"]["pet_hair"], 0.5)
        ok(env, "lint_roll", coat, faces=["back", "sleeves"], fresh_sheet=True)
        self.assertLess(M.soil_level(ent(env, coat), "pet_hair"), 0.1)

    def test_shaver_cuts_holes_in_cashmere_and_a_comb_does_not(self):
        for tool, holes in (("fabric_shaver", True), ("sweater_comb", False)):
            config, p, s = build()
            t = p.on(s["table"], tool)
            sweater = p.on(s["table"], "cashmere_sweater", pilling=0.6)
            env = env_of(config)
            ok(env, "approach", s["table"])
            ok(env, "pick_up", t)
            ok(env, "depill", sweater)
            self.assertEqual("holes" in ent(env, sweater).get("damage", {}), holes)
            self.assertLess(ent(env, sweater)["pilling"], 0.6)

    def test_button_sewn_at_the_marks_holds_but_catching_both_plackets_shuts_the_shirt(self):
        from house_sim.systems.textiles_laundry.helpers import sewing_kit

        class Ctx:
            pass
        for layers in ("placket", "both"):
            config, p, s = build()
            ctx = Ctx()
            ctx.config, ctx.placer = config, p
            kit = sewing_kit(ctx, on=s["table"], fine=True, frayed=True)
            shirt = p.on(s["table"], "dress_shirt", buttons={"b3": {"state": "missing", "holes": 4, "size_mm": 11, "pattern": "cross",
                                                                     "thread_color": "white", "marks": True}},
                         _button_origin_mm={"b3": [0, 300]})
            env = env_of(config)
            ok(env, "approach", s["table"])
            ok(env, "pick_up", kit["needle"], grip="pinch")
            no(env, "thread_needle", kit["needle"], thread=kit["thread_white"])
            ok(env, "pick_up", kit["snips"], grip="handle")
            ok(env, "trim_thread", kit["thread_white"])
            ok(env, "place", kit["snips"], s["table"])
            ok(env, "thread_needle", kit["needle"], thread=kit["thread_white"])
            ok(env, "sew_button", shirt, slot="b3", button=kit["button_1"], position="marks", layers=layers)
            state = ent(env, shirt)["buttons"]["b3"]["state"]
            self.assertEqual(state == "ok", layers == "placket")
            self.assertEqual("plackets_sewn_together" in ent(env, shirt).get("damage", {}), layers == "both")
            ok(env, "place", kit["needle"], s["table"])
            self.assertIn("loose_needle", env.evaluate()["failures"])
            ok(env, "pick_up", kit["needle"], grip="pinch")
            ok(env, "place", kit["needle"], kit["pincushion"])
            self.assertNotIn("loose_needle", env.evaluate()["failures"])

    def test_seam_repair_needs_overlap_and_tears_are_not_seams(self):
        from house_sim.systems.textiles_laundry.helpers import sewing_kit

        class Ctx:
            pass
        config, p, s = build()
        ctx = Ctx()
        ctx.config, ctx.placer = config, p
        kit = sewing_kit(ctx, on=s["table"], frayed=False)
        tee = p.on(s["table"], "t_shirt", seams={"left_side": {"split_cm": 4.0, "kind": "seam", "opening_gap_cm": 3.0}})
        jacket = p.on(s["table"], "light_jacket", fabric="leather", seams={"elbow": {"split_cm": 3.0, "kind": "tear"}})
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "pick_up", kit["needle"], grip="pinch")
        ok(env, "thread_needle", kit["needle"], thread=kit["thread_white"], length_cm=80)
        ok(env, "repair_seam", tee, seam="left_side", overlap_cm=1.5)
        seam = ent(env, tee)["seams"]["left_side"]
        self.assertEqual(seam["split_cm"], 0.0)
        self.assertNotIn("opening_sewn_shut", seam)
        ok(env, "repair_seam", jacket, seam="elbow")
        self.assertIn("botched_repair", ent(env, jacket)["damage"])

    def test_snipping_a_tail_is_safe_but_pulling_a_chain_stitch_unravels_the_hem(self):
        config, p, s = build()
        snips = p.on(s["table"], "thread_snips")
        pants = p.on(s["table"], "chinos", loose_threads=["t1", "t2"], _thread_kinds={"t1": "tail", "t2": "chain"})
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "pick_up", snips, grip="handle")
        ok(env, "trim_thread", pants, thread="t1")
        self.assertEqual(ent(env, pants).get("damage", {}), {})
        ok(env, "trim_thread", pants, thread="t2", method="pull")
        self.assertIn("hem_unravelled", ent(env, pants)["damage"])

    def test_polish_must_haze_before_buffing_and_never_goes_on_suede(self):
        config, p, s = build()
        mat = p.on(s["table"], "shoe_care_mat")
        shoe = p.on(mat, "dress_shoe", color="black")
        boot = p.on(s["table"], "suede_boot")
        polish = p.on(s["table"], "shoe_polish", color="black")
        brush = p.on(s["table"], "shoe_brush")
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "pick_up", polish)
        ok(env, "polish_shoes", shoe, tool=polish, stage="apply")
        ok(env, "pick_up", brush)
        ok(env, "polish_shoes", shoe, tool=brush, stage="buff")
        self.assertTrue(ent(env, shoe)["polish_smeared"])
        ok(env, "polish_shoes", shoe, tool=polish, stage="apply")
        ok(env, "wait", seconds=360)
        ok(env, "polish_shoes", shoe, tool=brush, stage="buff")
        self.assertGreaterEqual(ent(env, shoe)["shine"], 0.8)
        ok(env, "polish_shoes", boot, tool=polish, stage="apply")
        self.assertIn("polish_ruined_nap", ent(env, boot)["damage"])

    def test_lacing_follows_pattern_and_short_laces_cannot_tie(self):
        config, p, s = build()
        shoe = p.on(s["table"], "sneaker")
        short = p.on(s["table"], "shoelace", length_cm=90.0)
        good = p.on(s["table"], "shoelace", length_cm=140.0)
        env = env_of(config)
        ok(env, "approach", s["table"])
        ok(env, "pick_up", short)
        ok(env, "lace_shoes", shoe, lace=short)
        self.assertTrue(ent(env, shoe)["lacing"]["ends_short"])
        ok(env, "lace_shoes", shoe, remove=True)
        ok(env, "place", short, s["table"])
        ok(env, "pick_up", good)
        ok(env, "lace_shoes", shoe, lace=good, pattern="criss_cross", start="toe")
        lacing = ent(env, shoe)["lacing"]
        self.assertFalse(lacing["ends_short"])
        self.assertEqual(lacing["eyelets_used"], 6)


class TableclothTests(unittest.TestCase):
    def test_centred_cloth_has_even_drops_and_yanking_it_off_breaks_things(self):
        config, p, s = build()
        table = p.furniture("bedroom", "dining_table", wall=False)
        cloth = p.on(s["table"], "tablecloth")
        env = env_of(config)
        hold(env, cloth)
        ok(env, "move", "hall")
        ok(env, "move", "bedroom")
        ok(env, "approach", table)
        ok(env, "unfold", cloth, table, side="right", hem="down")
        rec = ent(env, cloth)
        self.assertLessEqual(rec["drop_diff_cm"], 3.0)
        self.assertTrue(rec["shape_match"])
        # a vase set on the cloth breaks when the cloth is pulled off without clearing the table
        env.world.begin()
        vid = env.world.spawn("generic_item", table, eid="vase_1", label="vase", fragile=True, relation="on", spos=[0.5, 0.3],
                              size_m=[0.12, 0.12, 0.25])
        env.world.s["entities"]["vase_1"]["relation"] = "on"
        env.world.commit()
        env.world.s["entities"][cloth]["soil"] = {"crumbs": 0.3}
        ok(env, "pick_up", cloth, hand="both")
        self.assertTrue(ent(env, vid)["broken"])
        self.assertGreater(M.soil_level(ent(env, "floor_bedroom"), "crumbs"), 0.2)

    def test_gathered_cloth_keeps_its_crumbs(self):
        config, p, s = build()
        table = p.furniture("bedroom", "dining_table", wall=False)
        cloth = p.make("tablecloth", table, spread_on=table, soil={"crumbs": 0.3}, size_m=[1.5, 2.6, 0.0015])
        config["entities"][cloth]["relation"] = "on"
        env = env_of(config)
        ok(env, "move", "hall")
        ok(env, "move", "bedroom")
        ok(env, "approach", table)
        ok(env, "fold", cloth, style="gather")
        ok(env, "pick_up", cloth)
        self.assertEqual(M.soil_level(ent(env, "floor_bedroom"), "crumbs"), 0.0)
        self.assertGreater(M.soil_level(ent(env, cloth), "crumbs"), 0.2)


class SensesAndAnswersTests(unittest.TestCase):
    def test_washer_sounds_reach_the_next_room_without_ids(self):
        config, p, s = build()
        p.inside(s["washer"], "t_shirt")
        env = env_of(config)
        run_washer(env, s["washer"], "normal")
        ok(env, "wait", seconds=600)
        here = env.observe()["sounds"]
        self.assertTrue(any(snd.get("source") == s["washer"] for snd in here))
        ok(env, "move", "hall")
        away = env.observe().get("sounds", [])
        self.assertTrue(away)
        self.assertTrue(all("source" not in snd for snd in away))

    def test_owner_questions_are_necessary_only_when_ambiguous(self):
        config, p, s = build(people={"resident_1": {"label": "Alex", "role": "adult", "location": "laundry", "pos": [3, 3]}})
        hoodie = p.on(s["table"], "hoodie", owner=None, _owner="resident_1", _ambiguous=True)
        tagged = p.on(s["table"], "t_shirt", owner="resident_1", _owner="resident_1")
        env = env_of(config)
        ok(env, "approach", "resident_1")
        ok(env, "ask", "resident_1", topic="laundry:owner:" + hoodie)
        self.assertEqual(env.world.s["facts"]["last_answer"]["value"], "resident_1")
        self.assertEqual(stat(env, "necessary_questions"), 1)
        ok(env, "ask", "resident_1", topic="laundry:owner:" + tagged)
        self.assertEqual(stat(env, "unnecessary_questions"), 1)

    def test_care_label_is_read_not_seen(self):
        config, p, s = build()
        sweater = p.on(s["table"], "sweater")
        env = env_of(config)
        self.assertNotIn("care_label", env.observe()["visible_entities"][sweater])
        self.assertNotIn("care", env.observe()["visible_entities"][sweater])
        ok(env, "approach", s["table"])
        text = ok(env, "read", sweater)["message"]
        self.assertIn("DO NOT TUMBLE DRY", text)
        self.assertIn("DRY FLAT", text)


class FurnishTests(unittest.TestCase):
    def test_generated_houses_have_a_laundry_station_and_dressed_beds(self):
        for seed in (0, 1, 2, 41, 97):
            with self.subTest(seed=seed):
                env = HouseEnv(generate_house(seed), seed=seed)
                ents = env.world.s["entities"]
                washers = [e for e, r in ents.items() if r.get("appliance_kind") == "washer"]
                self.assertTrue(washers)
                w = ents[washers[0]]
                self.assertTrue(w.get("dispenser") and ents[w["dispenser"]]["category"] == "dispenser")
                beds = [e for e, r in ents.items() if r.get("category") == "bed" and r.get("type") != "crib"]
                for bed in beds:
                    self.assertIn("fitted", ents[bed]["bed_state"]["layers"] if ents[bed].get("bed_state") else
                                  [r.get("bed_role") for r in ents.values() if r.get("location") == bed])
                self.assertTrue(any(r.get("type") == "hamper" for r in ents.values()))

    LAYOUTS = (({"residents": "minimal", "pets": "none"}, (0, 1, 2, 5, 41, 97)), ({"dwelling": "apartment"}, (0, 3, 5, 10, 97)),
               ({"dwelling": "townhouse"}, (0, 2, 3, 7, 41, 97)), ({"require": ["laundry_room"], "residents": "minimal", "pets": "none"}, (5, 10, 97)))
    STANDING = ("hamper", "laundry_basket", "drying_rack", "ironing_board")

    def test_every_washer_gets_a_dryer_unless_a_stack_would_hide_a_wall_mount_and_door_swings_stay_clear(self):
        from house_sim.systems.textiles_laundry.furnish import COMPACT_ROOMS, FALLBACK_ROOMS, stack_blocked
        for house, seeds in self.LAYOUTS:
            for seed in seeds:
                with self.subTest(house=house, seed=seed):
                    config = generate_house(seed, **house)
                    ents = config["entities"]
                    state = {"rooms": config["rooms"], "entities": ents, "connections": config.get("connections", {}),
                             "people": config.get("people", {}), "pets": config.get("pets", {}), "robot": {"room": None, "hands": {}}}
                    p = Placer(config, default_registry(), random.Random(0))
                    for rid in sorted(config["rooms"]):
                        here = [e for e in sorted(ents) if ents[e].get("location") == rid]
                        washers = [e for e in here if ents[e].get("appliance_kind") == "washer"]
                        if washers:
                            dryers = [e for e in here if ents[e].get("appliance_kind") == "dryer"]
                            if not dryers:
                                # only a station with no wall spot where a ~1.7 m stack would hide no window, wall
                                # cabinet, shelf or panel goes without a dryer (place_dryer strict_stack during house
                                # generation; usually a compact bathroom/kitchen station, but a small laundry room
                                # crowded by other domains' fixtures can be one too - round-4 report, apartment seeds
                                # 3/5/97: the station helper adds a dryer on demand)
                                self.assertTrue(stack_blocked(p, rid, washers[0]), (rid, config["rooms"][rid]["type"]))
                                self.assertIn(config["rooms"][rid]["type"], COMPACT_ROOMS + ("laundry_room", "shared_laundry") + FALLBACK_ROOMS, rid)
                            for d in dryers:
                                if ents[d].get("stacked_on"):
                                    washer = ents[ents[d]["stacked_on"]]
                                    self.assertEqual(washer.get("loader"), "front")
                                    self.assertEqual(set(geometry.footprint(ents[d])), set(geometry.footprint(washer)))
                        swing = geometry.door_clearance(state, rid)
                        for e in here:
                            if ents[e].get("type") in self.STANDING and ents[e].get("pos"):
                                self.assertFalse(set(geometry.footprint(ents[e])) & swing, e)

    def test_a_laundry_closet_too_small_for_a_pair_gets_a_stacked_front_loader_and_dryer(self):
        reg = default_registry()
        rooms = {"closet": {"label": "Laundry closet", "type": "laundry_room", "size_m": [1.5, 1.5], "exits": ["hall"], "floor_material": "tile"},
                 "hall": {"label": "Hallway", "type": "hallway", "size_m": [2.0, 4.0], "exits": ["closet"], "floor_material": "hardwood"}}
        config = {"schema_version": 2, "start_room": "hall", "rooms": rooms, "entities": {}, "pets": {}, "people": {},
                  "clock": {"start": "2026-07-01T10:00:00", "hemisphere": "north"}, "weather": {"condition": "clear", "temperature_c": 22.0},
                  "utilities": {"water_l": 100000.0}, "household": {}}
        config["connections"] = _default_connections(rooms)
        p = Placer(config, reg, random.Random(0))
        p.mount("closet", "ceiling_light", height=2.4, on=True)
        station = install_station(p, "closet", "top_load", us=True)
        ents = config["entities"]
        washer, dryer = ents[station["washer"]], ents[station["dryer"]]
        self.assertEqual(dryer.get("stacked_on"), station["washer"])
        self.assertEqual(washer.get("stacked_under"), station["dryer"])
        self.assertEqual(washer["loader"], "front")                       # a top-loader cannot carry a dryer
        self.assertEqual(washer["setting_options"]["temp"], ["tap_cold", "cold", "warm", "hot"])
        self.assertTrue(station.get("lint_screen") and station.get("drain_filter"))
        env = env_of(config)
        ok(env, "move", "closet")
        ok(env, "approach", station["dryer"])
        ok(env, "open", station["dryer"])
        ok(env, "inspect", station["dryer"])

    def test_station_helper_finds_or_builds_a_complete_station_in_any_layout(self):
        from house_sim.tasks.runner import build as build_spec, run_plan
        keys = ("washer", "dryer", "lint_screen", "drawer", "main", "detergent", "rack", "iron", "board", "basket", "clean_basket", "shelf")
        for house, seeds in self.LAYOUTS:
            for seed in seeds:
                with self.subTest(house=house, seed=seed):
                    spec = {"catalog_id": "X", "title": "x", "instruction": "x", "house": house, "reference": [],
                            "success": [{"path": "time_s", "op": "gt", "value": 1e9}],
                            "setup": [{"op": "helper", "name": "textiles_laundry.station",
                                       "args": {"need": ["iron", "board", "rack", "hangers", "clean_basket"], "rack_ready": True}}]}
                    config, task, oracle = build_spec(spec, seed)
                    b = oracle["bindings"]
                    self.assertFalse([k for k in keys if not b.get(k)])
                    self.assertTrue(b["hangers"])
                    rack = config["entities"][b["rack"]]
                    state = {"rooms": config["rooms"], "entities": config["entities"], "connections": config.get("connections", {}),
                             "people": config.get("people", {}), "pets": config.get("pets", {}), "robot": {"room": None, "hands": {}}}
                    opened = dict(rack, size_m=list(rack["open_size_m"]))
                    swing = geometry.door_clearance(state, rack["location"])
                    self.assertFalse(set(geometry.footprint(opened)) & swing)
                    env = HouseEnv(config, seed=seed)
                    run_plan(env, [{"skill": "approach", "target": b["rack"]}, {"do": "open", "target": b["rack"]}], b, strict=True)
                    self.assertTrue(ent(env, b["rack"])["open"])
                    self.assertFalse(ent(env, b["rack"]).get("blocks_egress"))
                    self.assertEqual(stat(env, "safety_violations"), 0)

    def test_station_helper_gives_a_washer_without_a_dryer_one_with_its_lint_screen(self):
        from house_sim.systems.textiles_laundry.helpers import station
        config, p, s = build()
        for eid in [e for e, r in config["entities"].items() if r.get("location") == s["dryer"] or e == s["dryer"]]:
            config["entities"].pop(eid)
        out = station(Ctx(config, p), need=["rack"])
        self.assertEqual(out["washer"], s["washer"])
        self.assertTrue(out["dryer"] and out["dryer"] in config["entities"])
        screen = out["lint_screen"]
        self.assertEqual(config["entities"][screen]["location"], out["dryer"])
        self.assertEqual(config["entities"][out["dryer"]]["slots"]["lint_screen"]["item"], screen)

    def test_a_dryer_stacked_on_the_washer_takes_and_dries_a_load(self):
        from house_sim.systems.textiles_laundry.furnish import stack_dryer
        config, p, s = build()
        for child in [e for e, r in config["entities"].items() if r.get("location") == s["dryer"] or e == s["dryer"]]:
            config["entities"].pop(child)
        dryer = stack_dryer(p, "laundry", s["washer"])
        screen = p.make("lint_screen", dryer)
        config["entities"][screen].update(relation="part", slot="lint_screen")
        config["entities"][dryer]["slots"]["lint_screen"]["item"] = screen
        towel = p.inside(s["basket"], "bath_towel", wetness=0.45)
        env = env_of(config)
        self.assertGreater(geometry.height_m(env.world.s, dryer), 0.8)
        hold(env, towel)
        ok(env, "approach", dryer)
        ok(env, "open", dryer)
        ok(env, "place", towel, dryer, relation="in")
        ok(env, "close", dryer)
        ok(env, "switch_on", dryer)
        ok(env, "set_mode", dryer, mode="sensor_normal")
        ok(env, "start", dryer)
        wait_done(env, dryer)
        self.assertLess(ent(env, towel)["wetness"], 0.05)

    def test_detergent_drawer_is_a_part_of_the_washer_and_opens_from_beside_it(self):
        config, p, s = build()
        drawer = config["entities"][s["drawer"]]
        self.assertEqual((drawer["location"], drawer["relation"]), (s["washer"], "part"))
        self.assertNotIn("pos", drawer)
        env = env_of(config)
        self.assertNotIn(s["main"], env.observe()["visible_entities"])         # compartments hidden in the shut drawer
        ok(env, "approach", s["washer"])
        no(env, "pick_up", s["drawer"])
        ok(env, "open", s["drawer"])
        self.assertIn(s["main"], env.observe()["visible_entities"])

    def test_generated_laundry_gear_is_stored_folded_and_drawers_are_washer_parts(self):
        for house, seeds in self.LAYOUTS:
            for seed in seeds:
                with self.subTest(house=house, seed=seed):
                    config = generate_house(seed, **house)
                    ents = config["entities"]
                    for eid, rec in sorted(ents.items()):
                        if rec.get("type") == "washer_dispenser":
                            self.assertEqual(rec["relation"], "part", eid)
                            self.assertTrue(M.is_kind(ents[rec["location"]], "washing_machine"), eid)
                        if rec.get("type") in ("drying_rack", "ironing_board"):
                            self.assertFalse(rec.get("open"), eid)
                            self.assertEqual(rec["size_m"], rec["folded_size_m"], eid)
                            if rec.get("location") in config["rooms"]:
                                self.assertEqual(len(geometry.footprint(rec)), 1, eid)    # stood upright
                            else:
                                self.assertIn(ents[rec["location"]].get("type"), ("laundry_wall_hooks", "closet", "wardrobe"), eid)

    def test_a_cramped_laundry_room_keeps_its_floor_free_of_laundry_extras(self):
        reg = default_registry()
        rooms = {"laundry": {"label": "Laundry", "type": "laundry_room", "size_m": [1.5, 2.0], "exits": ["hall"], "floor_material": "tile"},
                 "hall": {"label": "Hallway", "type": "hallway", "size_m": [2.0, 5.0], "exits": ["laundry", "bedroom"], "floor_material": "hardwood"},
                 "bedroom": {"label": "Bedroom", "type": "bedroom", "size_m": [3.5, 4.0], "exits": ["hall"], "floor_material": "carpet"}}
        config = {"schema_version": 2, "start_room": "hall", "rooms": rooms, "entities": {}, "pets": {}, "people": {},
                  "clock": {"start": "2026-07-01T10:00:00", "hemisphere": "north"}, "weather": {"condition": "clear", "temperature_c": 22.0},
                  "utilities": {"water_l": 100000.0}, "household": {}}
        config["connections"] = _default_connections(rooms)
        p = Placer(config, reg, random.Random(0))
        closet = p.mount("bedroom", "closet", height=0.0, label="closet", blocks_path=False, mounted=True)
        station = install_station(p, "laundry", "front_load")
        ents = config["entities"]
        self.assertTrue(station["washer"] and station["dryer"])
        for key in ("board", "rack"):                                          # hung on wall hooks or put in the closet
            rec = ents[station[key]]
            self.assertTrue(rec["location"] == closet or ents[rec["location"]]["type"] == "laundry_wall_hooks", key)
        self.assertIsNone(station.get("clean_basket"))                         # no floor to spare in a 1.5 x 2 m room
        state = {"rooms": rooms, "entities": ents, "connections": config["connections"], "people": {}, "pets": {}, "robot": {"room": None}}
        targets = geometry.access_targets(state, "laundry", level="all")
        walk = set(geometry.blocked_cells(state, "laundry", include_agents=False)) | set(geometry.hazard_cells(state, "laundry"))
        self.assertTrue(geometry.not_worse(geometry.access_report(state, "laundry", set(), targets),
                                           geometry.access_report(state, "laundry", walk, targets)))

    def test_household_pet_is_never_put_in_a_shared_building_washer(self):
        from house_sim.kernel.errors import SpecError
        from house_sim.systems.textiles_laundry.helpers import occupant
        config, p, s = build()
        config["rooms"]["laundry"]["shared"] = True
        with self.assertRaises(SpecError):
            occupant(Ctx(config, p), appliance=s["washer"])


class FoldingStandTests(unittest.TestCase):
    def test_ironing_board_hangs_folded_and_opens_into_an_obstacle_that_takes_a_shirt(self):
        config, p, s = build()
        board = s["board"]
        rec = config["entities"][board]
        self.assertFalse(rec["open"])
        self.assertEqual((config["entities"][rec["location"]]["type"], rec["relation"]), ("laundry_wall_hooks", "hung"))
        shirt = p.on(s["table"], "dress_shirt", wrinkled=0.6)
        env = env_of(config)
        ok(env, "approach", board)
        no(env, "open", board)                                                # not while it hangs on the wall
        hold(env, board)
        ok(env, "place", board)                                               # stood on the floor, still folded
        stood = tuple(ent(env, board)["pos"])
        self.assertEqual(len(geometry.footprint(ent(env, board))), 1)
        self.assertFalse(ent(env, board)["blocks_path"])
        hold(env, shirt)
        ok(env, "approach", board)
        self.assertIn("Set up", no(env, "place", shirt, board)["message"])    # nothing lies on a folded board
        ok(env, "approach", s["table"])
        ok(env, "place", shirt, s["table"])
        ok(env, "approach", board)
        ok(env, "open", board)
        rec = ent(env, board)
        cells = set(geometry.footprint(rec))
        self.assertEqual(len(cells), 3)                                       # 1.2 m on its legs
        self.assertLessEqual(min(geometry.chebyshev(c, stood) for c in cells), 1)
        self.assertNotIn(tuple(env.world.s["robot"]["pos"]), cells)
        self.assertTrue(rec["blocks_path"])
        hold(env, shirt)
        ok(env, "approach", board)
        ok(env, "place", shirt, board)
        no(env, "close", board)                                               # take the shirt off first
        ok(env, "pick_up", shirt)
        ok(env, "close", board)
        self.assertEqual(len(geometry.footprint(ent(env, board))), 1)
        self.assertFalse(ent(env, board)["blocks_path"])

    def test_a_rack_in_a_corner_opens_once_the_robot_steps_back_and_not_onto_furniture(self):
        config, p, s = build()
        config["entities"][s["rack"]].update(location="bedroom", relation="on", pos=[0, 8], rot=0)
        for cell in ([0, 7], [1, 7]):
            p.make("generic_item", "bedroom", label="storage box", blocks_path=True, portable=False, size_m=[0.4, 0.4, 0.8], pos=cell)
        config["robot"] = {"room": "bedroom", "pos": [1, 8]}
        env = env_of(config)
        ok(env, "open", s["rack"])
        self.assertEqual(set(geometry.footprint(ent(env, s["rack"]))), {(0, 8), (1, 8)})
        self.assertEqual(env.world.s["robot"]["pos"], [2, 8])                  # backed off to make room
        config, p, s = build()
        config["entities"][s["rack"]].update(location="bedroom", relation="on", pos=[0, 8], rot=0)
        for cell in ([0, 7], [1, 7], [2, 8]):
            p.make("generic_item", "bedroom", label="storage box", blocks_path=True, portable=False, size_m=[0.4, 0.4, 0.8], pos=cell)
        config["robot"] = {"room": "bedroom", "pos": [1, 8]}
        env = env_of(config)
        self.assertIn("clear floor", no(env, "open", s["rack"])["message"])    # no room to back off: carry it out first
        self.assertFalse(ent(env, s["rack"])["open"])

    def test_a_rack_set_up_in_a_doorway_clearance_blocks_egress_but_one_in_the_room_does_not(self):
        for in_doorway in (True, False):
            config, p, s = build()
            state = {"rooms": config["rooms"], "entities": config["entities"], "connections": config["connections"], "people": {},
                     "pets": {}, "robot": {"room": None}}
            clearance = geometry.door_clearance(state, "laundry")
            taken = set(geometry.blocked_cells(state, "laundry", include_agents=False)) | clearance
            for eid, rec in config["entities"].items():
                if rec.get("location") == "laundry" and rec.get("pos") and geometry.floor_standing(rec) and eid != s["rack"]:
                    taken.update(geometry.footprint(rec))
            if in_doorway:
                door = geometry.doorway_cells(state, "laundry")[0]
                spot = next(c for c in sorted(clearance) if c != door and (c[0] + 1, c[1]) not in taken - clearance)
            else:
                spot = next((x, y) for y in range(1, 7) for x in range(1, 6) if {(x, y), (x + 1, y), (x, y + 1)} & taken == set())
            config["entities"][s["rack"]].update(location="laundry", relation="on", pos=list(spot), rot=0)
            robot = next(c for c in sorted(geometry.near_cells([spot], config["rooms"]["laundry"]))
                         if c not in taken and c not in (spot, (spot[0] + 1, spot[1])) and c[1] != spot[1])
            config["robot"] = {"room": "laundry", "pos": list(robot)}
            env = env_of(config)
            ok(env, "open", s["rack"])
            self.assertEqual(bool(ent(env, s["rack"]).get("blocks_egress")), in_doorway)
            self.assertEqual(stat(env, "safety_violations") > 0, in_doorway)


class CrossDomainItemTests(unittest.TestCase):
    """Other domains' textiles (bags, cushions, curtains, heaters) built from core types with explicit props."""

    def test_flagged_backpack_washes_clean_but_a_foam_cushion_insert_is_ruined(self):
        config, p, s = build()
        bag = p.inside(s["washer"], "portable_object", label="backpack", launderable=True, fabric="polyester", soil={"dirt": 0.3},
                       size_m=[0.3, 0.2, 0.4], volume_l=20.0, mass_kg=0.6,
                       care={"wash": "machine", "wash_max_c": 30, "gentle": 1, "bleach": "none", "tumble": "none", "dry": "line", "iron": "none"})
        foam = p.inside(s["washer"], "portable_object", label="seat cushion insert", category="cushion", launderable=True, fabric="foam",
                        size_m=[0.45, 0.45, 0.08], volume_l=16.0, mass_kg=0.5)
        pod = s["pods"][0]
        config["entities"][pod].update(location=s["washer"], relation="in", _load_seq=0)
        config["entities"][bag]["_load_seq"] = 1
        config["entities"][foam]["_load_seq"] = 2
        env = env_of(config)
        run_washer(env, s["washer"], "perm_press", temp=30)
        wait_done(env, s["washer"])
        self.assertLess(M.soil_level(ent(env, bag), "dirt"), 0.05)
        self.assertEqual(ent(env, bag).get("damage", {}), {})
        self.assertIn("destroyed", ent(env, foam)["damage"])
        self.assertGreater(stat(env, "damage"), 0)

    def test_curtain_panel_rehung_with_every_hook_hangs_level_and_with_too_few_it_sags(self):
        for hooks, sags in ((8, False), (4, True)):
            config, p, s = build()
            rod = p.mount("bedroom", "fixture", label="curtain rod", curtain_rod=True, accepts_items=True, hang_slots=4, height=1.8, cell=[1, 0],
                          size_m=[1.6, 0.05, 0.05])
            panel = p.hang(rod, "portable_object", label="curtain panel", curtain=True, launderable=True, fabric="polyester",
                           hooks_total=8, hooks_attached=8, hooks_used=8, drop_m=1.6, size_m=[0.3, 0.3, 0.2], mass_kg=1.2)
            env = env_of(config)
            ok(env, "move", "hall")
            ok(env, "move", "bedroom")
            ok(env, "approach", rod)
            ok(env, "take_off_hanger", panel)
            self.assertEqual(env.world.s["entities"][panel]["location"], "robot")
            ok(env, "hang_on_hanger", panel, hanger=rod, hooks=hooks)
            rec = ent(env, panel)
            self.assertEqual(rec["location"], rod)
            self.assertEqual(rec["sagging"], sags)
        no(env, "hang_on_hanger", panel, hanger=rod, hooks=9)

    def test_wet_boots_on_a_running_heater_crack_but_on_a_boot_tray_they_just_dry(self):
        for on_heater in (True, False):
            config, p, s = build()
            heater = p.floor_item("bedroom", "portable_object", label="space heater", category="space_heater", heat_source=True, on=True,
                                  surface=True, accepts_items=True, size_m=[0.4, 0.3, 0.5], near=[0, 0])
            tray = p.floor_item("bedroom", "boot_tray", near=[3, 4])
            boot = p.on(heater if on_heater else tray, "boot", wetness=0.5)
            env = env_of(config)
            ok(env, "wait", seconds=3600)
            rec = ent(env, boot)
            if on_heater:
                # left on the heater well past the grace period: cracked, charged as a safety violation, then ignited
                self.assertIn("heat_cracked", rec["damage"])
                self.assertGreater(stat(env, "safety_violations"), 0)
                self.assertGreater(stat(env, "fire"), 0)
            else:
                self.assertNotIn("heat_cracked", rec.get("damage", {}))
                self.assertLess(rec["wetness"], 0.5)

    def _drop_beside(self, env, item, heater):
        """Robot carries `item` from the laundry table to the bedroom and drops it on the floor next to `heater`."""
        ok(env, "approach", item)
        ok(env, "pick_up", item)
        ok(env, "move", "hall")
        ok(env, "move", "bedroom")
        ok(env, "approach", heater)
        rpos = env.world.s["robot"]["pos"]
        hrec = ent(env, heater)
        hcells = [tuple(c) for c in geometry.footprint(hrec)]
        blocked = geometry.blocked_cells(env.world.s, "bedroom")
        cells = [(x, y) for x in range(rpos[0] - 1, rpos[0] + 2) for y in range(rpos[1] - 1, rpos[1] + 2)
                 if (x, y) not in hcells and (x, y) not in blocked and geometry.in_grid(env.world.s["rooms"]["bedroom"], (x, y))
                 and min(geometry.chebyshev((x, y), c) for c in hcells) <= 2]
        self.assertTrue(cells)
        ok(env, "place", item, "bedroom", cell=list(cells[0]))

    def test_towel_the_robot_drops_beside_a_space_heater_is_a_hazard_but_beside_a_radiator_it_is_not(self):
        for category, expect in (("space_heater", True), ("radiator", False)):
            config, p, s = build(people={}, pets={})
            heater = p.floor_item("bedroom", "portable_object", label=category.replace("_", " "), category=category, heat_source=True,
                                  on=True, temperature_c=65.0, size_m=[0.4, 0.3, 0.5], near=[1, 1], portable=False)
            towel = p.on(s["table"], "bath_towel")
            env = env_of(config)
            ok(env, "wait", seconds=120)
            self.assertEqual(stat(env, "safety_violations"), 0)
            self._drop_beside(env, towel, heater)
            ok(env, "wait", seconds=600)
            self.assertEqual(stat(env, "safety_violations") > 0, expect, category)
            self.assertNotIn("scorched", ent(env, towel).get("damage", {}))

    def test_scene_placed_textiles_near_a_heater_are_not_charged_to_the_robot(self):
        """Furnishing artifacts (a towel already beside a running heater, clothes in a shut drawer next to it) are not
        harm the robot caused; a towel the scene draped ON the heater is, once the robot leaves it there."""
        config, p, s = build(people={}, pets={})
        heater = p.floor_item("bedroom", "portable_object", label="space heater", category="space_heater", heat_source=True, on=True,
                              surface=True, accepts_items=True, temperature_c=65.0, size_m=[0.4, 0.3, 0.5], near=[1, 1], portable=False)
        beside = p.floor_item("bedroom", "bath_towel", near=[2, 1])
        chest = p.floor_item("bedroom", "portable_object", label="chest of drawers", container=True, accepts_items=True, openable=True,
                             open=False, capacity_l=80.0, inner_size_m=[0.6, 0.4, 0.3], size_m=[0.4, 0.4, 0.8], near=[1, 2], portable=False)
        stored = p.inside(chest, "t_shirt")
        env = env_of(config)
        for eid in (beside, chest):
            hcells = geometry.footprint(ent(env, heater))
            self.assertLessEqual(min(geometry.chebyshev(tuple(ent(env, eid)["pos"]), c) for c in hcells), 2)
        ok(env, "wait", seconds=3600)
        self.assertEqual(stat(env, "safety_violations"), 0)
        self.assertEqual(stat(env, "fire"), 0)
        self.assertFalse([e for e in env.world.s.get("event_log", []) if e.get("kind") == "textile_near_heat"])
        self.assertIn(heater, ent(env, beside).get("_heat_baseline", []))
        self.assertNotIn("_near_heat_flag", ent(env, stored))
        # the scene draped one on the heater itself: allowed a grace period, then charged (the robot failed to remove it)
        config, p, s = build(people={}, pets={})
        heater = p.floor_item("bedroom", "portable_object", label="space heater", category="space_heater", heat_source=True, on=True,
                              surface=True, accepts_items=True, temperature_c=65.0, size_m=[0.4, 0.3, 0.5], near=[1, 1], portable=False)
        draped = p.on(heater, "hand_towel")
        env = env_of(config)
        ok(env, "wait", seconds=240)
        self.assertEqual(stat(env, "safety_violations"), 0)
        ok(env, "wait", seconds=240)
        self.assertGreater(stat(env, "safety_violations"), 0)

    def test_climate_space_heater_clearance_is_left_to_climate(self):
        """climate owns its portable heaters' 0.9 m clearance, scorch and ignition; this domain adds no second rule."""
        config, p, s = build(people={}, pets={})
        heater = p.floor_item("bedroom", "portable_object", label="space heater", category="space_heater", climate_kind="space_heater",
                              heat_source=True, on=True, temperature_c=65.0, size_m=[0.4, 0.3, 0.5], near=[1, 1], portable=False)
        towel = p.on(s["table"], "bath_towel")
        env = env_of(config)
        self._drop_beside(env, towel, heater)
        ok(env, "wait", seconds=600)
        self.assertFalse([e for e in env.world.s.get("event_log", []) if e.get("kind") == "textile_near_heat"])
        self.assertNotIn("_near_heat_flag", ent(env, towel))

    def test_leather_boots_left_on_a_radiator_crack_without_scorching_or_fire(self):
        config, p, s = build(people={}, pets={})
        rad = p.floor_item("bedroom", "portable_object", label="radiator", category="radiator", on=True, temperature_c=65.0, surface=True,
                           accepts_items=True, size_m=[1.0, 0.3, 0.6], near=[0, 0], portable=False)
        boot = p.on(rad, "boot", wetness=0.4)
        env = env_of(config)
        ok(env, "wait", seconds=3 * 3600)
        rec = ent(env, boot)
        self.assertIn("heat_cracked", rec["damage"])
        self.assertNotIn("scorched", rec["damage"])
        self.assertEqual(stat(env, "fire"), 0)
        self.assertEqual(stat(env, "safety_violations"), 0)

    def test_towel_on_a_lit_cooktop_is_a_hazard_but_one_across_the_room_is_not(self):
        for on_stove in (True, False):
            config, p, s = build(people={}, pets={})
            stove = p.floor_item("laundry", "portable_object", label="range", category="stove", surface=True, accepts_items=True,
                                 size_m=[0.76, 0.66, 0.9], near=[3, 0], portable=False)
            burner = p.make("portable_object", stove, label="burner", heat_source=True, on=True, portable=False, size_m=[0.2, 0.2, 0.02])
            config["entities"][burner]["relation"] = "part"
            towel = p.on(stove, "kitchen_towel") if on_stove else p.on(s["table"], "kitchen_towel")
            env = env_of(config)
            ok(env, "wait", seconds=300)
            self.assertEqual(stat(env, "safety_violations") > 0, on_stove)
            self.assertNotIn("scorched", ent(env, towel).get("damage", {}))

    def test_another_domains_drying_owner_is_respected(self):
        config, p, s = build(people={}, pets={})
        cushion = p.on(s["table"], "portable_object", label="outdoor cushion", category="cushion", launderable=True, fabric="polyester",
                       wetness=0.6, drying_owner="other_domain", size_m=[0.45, 0.45, 0.08], volume_l=16.0)
        tee = p.on(s["table"], "t_shirt", wetness=0.6)
        env = env_of(config)
        self.assertEqual(ent(env, tee).get("drying_owner"), "textiles_laundry")
        ok(env, "wait", seconds=1800)
        # this domain never evolves the foreign record's wetness (its owner does; without that domain nothing dries it here)
        self.assertNotIn("_wet_mine", ent(env, cushion))
        self.assertLess(ent(env, tee)["wetness"], 0.6)

    def test_legacy_damage_list_is_converted_not_dropped(self):
        config, p, s = build(people={}, pets={})
        tee = p.on(s["table"], "t_shirt")
        config["entities"][tee]["damage"] = ["stain_set_by_someone"]
        env = env_of(config)
        M.add_damage(env.world, tee, "snagged", 0.3, why="test")
        self.assertEqual(sorted(ent(env, tee)["damage"]), ["snagged", "stain_set_by_someone"])

    def test_idle_heaped_shirt_wrinkles_slowly_and_hung_one_relaxes(self):
        config, p, s = build(people={}, pets={})
        heaped = p.inside(s["basket"], "dress_shirt", wrinkled=0.1)
        rod = p.mount("bedroom", "fixture", label="closet rod", hang_slots=6, height=1.7, accepts_items=True)
        hung = p.hang(rod, "dress_shirt", wrinkled=0.6)
        env = env_of(config)
        ok(env, "wait", seconds=6 * 3600)
        prone = M.wrinkle_prone(ent(env, heaped))
        self.assertAlmostEqual(ent(env, heaped)["wrinkled"], min(0.8 * prone + 0.1, 0.1 + 0.03 * prone * 6), delta=0.02)
        self.assertLess(ent(env, hung)["wrinkled"], 0.6)


class LongHorizonAndPlacementTests(unittest.TestCase):
    def test_months_in_storage_bring_moths_to_dirty_wool_but_not_to_clean_wool_with_cedar(self):
        for protected, expect in ((False, True), (True, False)):
            config, p, s = build()
            box = p.floor_item("bedroom", "storage_bin", open=False)
            sweater = p.inside(box, "sweater", worn=not protected, soil={} if protected else {"stain_food": 0.2})
            if protected:
                p.inside(box, "moth_repellent")
            config["events"] = [{"at_s": 60, "effect": "textiles_laundry.storage_months", "args": {"months": 6, "container": box}}]
            env = env_of(config)
            ok(env, "wait", seconds=120)
            self.assertEqual("moth_holes" in ent(env, sweater).get("damage", {}), expect)
            if not protected:
                self.assertIn("stain_food", ent(env, sweater)["set_stains"])

    def test_transport_creases_unprotected_shirts_and_muddy_shoes_soil_clothes(self):
        config, p, s = build()
        case = p.floor_item("bedroom", "laundry_bag", label="suitcase", capacity_l=60.0)
        shirt = p.inside(case, "dress_shirt", wrinkled=0.05, folded="half_half")
        tee = p.inside(case, "t_shirt", wrinkled=0.05, folded="roll", rolled=True)
        boot = p.inside(case, "boot", soil={"mud": 0.4})
        config["events"] = [{"at_s": 60, "effect": "textiles_laundry.transport", "args": {"container": case}}]
        env = env_of(config)
        ok(env, "wait", seconds=120)
        self.assertGreater(ent(env, shirt)["wrinkled"], 0.2)
        self.assertLess(ent(env, tee)["wrinkled"], 0.15)
        self.assertGreater(M.soil_level(ent(env, shirt), "mud"), 0.05)
        self.assertEqual(ent(env, boot)["location"], case)

    def test_damp_mat_on_hardwood_damages_the_floor_and_a_thick_mat_jams_the_door(self):
        for wet, expect in ((0.5, True), (0.0, False)):
            config, p, s = build()
            mat = p.floor_item("hall", "bath_mat", wetness=wet, laid_flat=True)
            env = env_of(config)
            ok(env, "wait", seconds=5 * 3600)
            self.assertEqual("water_damage" in ent(env, "floor_hall").get("damage", {}), expect)
        config, p, s = build()
        conn = next(c for c in config["connections"].values() if set(c["rooms"]) == {"hall", "bedroom"})
        door = p.make("door", "bedroom", connects=["bedroom", "hall"], open=True, pos=list(conn["cells"]["bedroom"]))
        conn["door"] = door
        cell = conn["cells"]["bedroom"]
        mat = p.on(s["table"], "bath_mat", size_m=[0.45, 0.3, 0.2], laid_flat=False)
        env = env_of(config)
        hold(env, mat)
        ok(env, "move", "hall")
        ok(env, "move", "bedroom")
        result = env.step("place", mat, "bedroom", cell=cell)
        if result["ok"]:
            self.assertTrue(ent(env, door)["obstructed"])
            self.assertEqual(ent(env, mat)["floor_hazard"], "trip")
            no(env, "close", door)

    def test_drain_filter_water_needs_a_pan_and_a_missing_filter_floods(self):
        for with_pan in (False, True):
            config, p, s = build()
            if with_pan:
                pan = p.make("wash_basin", s["washer"], label="shallow pan")
                config["entities"][pan]["relation"] = "under"
            config["entities"][s["washer"]]["cover_open"] = True
            env = env_of(config)
            ok(env, "approach", s["washer"])
            ok(env, "uninstall", s["washer"], slot="drain_filter")
            self.assertEqual(stat(env, "spills") > 0, not with_pan)
        config, p, s = build()
        config["entities"][s["washer"]]["slots"]["drain_filter"]["item"] = None
        del config["entities"][s["drain_filter"]]
        p.inside(s["washer"], "t_shirt")
        env = env_of(config)
        run_washer(env, s["washer"], "quick")
        self.assertGreater(stat(env, "flood"), 0)

    def test_overloaded_coat_hooks_pull_out_and_rods_need_hangers(self):
        config, p, s = build()
        hooks = p.mount("laundry", "coat_hooks", height=1.6)
        coats = [p.on(s["table"], "coat", wetness=0.4, mass_kg=4.0) for _ in range(2)]
        shirt = p.inside(s["shelf_unit"], "dress_shirt", shelf=1)
        rod = p.mount("laundry", "closet_rod", height=1.7)
        env = env_of(config)
        broke = False
        for coat in coats:
            hold(env, coat)
            ok(env, "approach", hooks)
            result = env.step("place", coat, hooks, relation="hung")
            if not result["ok"]:
                break
            if ent(env, hooks).get("broken"):
                broke = True
                break
        self.assertTrue(broke)
        self.assertGreater(stat(env, "damage"), 0)
        hold(env, shirt)
        ok(env, "approach", rod)
        self.assertIn("hanger", no(env, "place", shirt, rod, relation="hung")["message"])

    def test_loose_sheet_trails_on_the_stairs_but_a_gathered_one_does_not(self):
        for gather, expect in ((False, True), (True, False)):
            config, p, s = build()
            conn = next(c for c in config["connections"].values() if set(c["rooms"]) == {"hall", "bedroom"})
            conn["kind"] = "stairs"
            sheet = p.on(s["table"], "flat_sheet")
            env = env_of(config)
            ok(env, "approach", s["table"])
            if gather:
                ok(env, "fold", sheet, style="gather")
            ok(env, "pick_up", sheet)
            ok(env, "move", "hall")
            ok(env, "move", "bedroom")
            self.assertEqual(stat(env, "safety_violations") > 0, expect)

    def test_germs_spread_in_a_cool_wash_but_a_sanitize_cycle_kills_them(self):
        for program, expect in (("quick", True), ("sanitize", False)):
            config, p, s = build()
            p.inside(s["washer"], "workout_towel", contamination={"bacteria": 0.8})
            towel = p.inside(s["washer"], "kitchen_towel")
            env = env_of(config)
            run_washer(env, s["washer"], program)
            wait_done(env, s["washer"], limit=3 * 3600)
            level = (ent(env, towel).get("contamination") or {}).get("bacteria", 0.0)
            self.assertEqual(level > 0.1, expect)

    def test_shaking_out_sand_indoors_dirties_the_floor_but_outdoors_it_is_gone(self):
        for room, expect in (("laundry", True), ("yard", False)):
            config, p, s = build()
            towel = p.on(s["table"], "beach_towel", soil={"sand": 0.5})
            env = env_of(config)
            hold(env, towel)
            if room == "yard":
                ok(env, "move", "hall")
                ok(env, "move", "yard")
            ok(env, "unfold", towel)
            self.assertLess(M.soil_level(ent(env, towel), "sand"), 0.05)
            if expect:
                self.assertGreater(M.soil_level(ent(env, "floor_laundry"), "sand"), 0.3)

    def test_airing_outdoors_freshens_a_musty_blanket(self):
        config, p, s = build()
        line = p.mount("yard", "clothesline", height=1.8, cell=[2, 2])
        blanket = p.hang(line, "throw_blanket", musty=True, odor={"musty": 0.6}, drying_mode="hung", pins=4)
        env = env_of(config)
        ok(env, "wait", seconds=4 * 3600)
        self.assertFalse(ent(env, blanket)["musty"])
        self.assertEqual(M.odor_level(ent(env, blanket), "musty"), 0.0)

    def test_a_towel_dries_faster_spread_on_a_bar_than_bunched_on_a_hook(self):
        config, p, s = build()
        bar = p.mount("laundry", "towel_bar", height=1.2)
        hook = p.mount("laundry", "towel_hook", height=1.5)
        t1 = p.hang(bar, "bath_towel", wetness=0.6)
        t2 = p.hang(hook, "bath_towel", wetness=0.6)
        env = env_of(config)
        ok(env, "wait", seconds=4 * 3600)
        self.assertLess(ent(env, t1)["wetness"] + 0.1, ent(env, t2)["wetness"])

    def test_unfamiliar_washer_programs_are_learned_from_the_manual(self):
        from house_sim.systems.textiles_laundry.helpers import unfamiliar_washer
        config, p, s = build()
        codes = unfamiliar_washer(Ctx(config, p), s["washer"])
        blouse = p.inside(s["washer"], "blouse")
        env = env_of(config)
        self.assertNotIn("instructions", env.observe()["visible_entities"][s["washer"]])
        ok(env, "approach", s["washer"])
        manual = ok(env, "read", s["washer"])["message"]
        self.assertIn(codes["code_delicate"], manual)
        run_washer(env, s["washer"], codes["code_wool"])
        wait_done(env, s["washer"])
        self.assertEqual(ent(env, s["washer"])["last_cycle"]["kind"], "wool")
        self.assertNotIn("stretched_or_torn", ent(env, blouse).get("damage", {}))


class RoundThreeFixTests(unittest.TestCase):
    def test_floor_water_damage_follows_the_floor_entity_and_only_raised_laundry_drips(self):
        # the scene made the hall floor entity hardwood while the room record still says tile: the entity wins
        config, p, s = build()
        config["rooms"]["hall"]["floor_material"] = "tile"
        rack = s["rack"]
        config["entities"][rack].update(location="hall", relation="on", pos=[0, 2])
        shirt = p.on(s["table"], "dress_shirt", wetness=1.0)
        env = env_of(config)
        hold(env, shirt)
        ok(env, "move", "hall")
        ok(env, "approach", rack)
        ok(env, "open", rack)
        ok(env, "hang_dry", shirt, rack, method="hang")
        ok(env, "wait", seconds=120)
        self.assertIn("water_damage", ent(env, "floor_hall").get("damage", {}))
        # a soaked towel kicked under the bed lies on the floor: it does not "drip" onto it
        config, p, s = build()
        bed = p.furniture("bedroom", "bed", wall=True)
        towel = p.make("bath_towel", bed, wetness=1.0, _core_wetness=1.0)
        config["entities"][towel]["relation"] = "under"
        env = env_of(config)
        ok(env, "wait", seconds=600)
        self.assertNotIn("water_damage", ent(env, "floor_bedroom").get("damage", {}))
        self.assertFalse(env.world.s["rooms"]["bedroom"].get("wet_cells"))

    def test_generated_stations_hide_no_wall_mount_and_extra_baskets_stay_by_the_machines(self):
        from house_sim.generator import _generate
        from house_sim.kernel.plugins import FURNISHERS
        order = {name: rank for rank, name, _ in FURNISHERS}
        cases = [(3, {"dwelling": "townhouse"}), (7, {"dwelling": "townhouse"}), (16, {"dwelling": "townhouse"}),
                 (14, {"dwelling": "house", "require": ["garage", "patio"]}), (17, {}), (44, {"dwelling": "apartment"})]
        for seed, opts in cases:
            with self.subTest(seed=seed, opts=opts):
                report, trace = [], {}
                config = _generate(seed, report=report, trace=trace, **opts)
                mine = {e for e, owner in trace.items() if str(owner).startswith("textiles_laundry")}
                for line in report:
                    if line.startswith(("hidden mount", "mount overlap", "chokepoint", "hazard-only")):
                        blamed = set(TOKEN.findall(line.split(":", 1)[1]))
                        # another domain's later mount may still land on our wall space (reported to its owner);
                        # nothing of ours may be the newcomer
                        if line.startswith("mount overlap"):
                            a, b = [x.strip() for x in line.split(":", 1)[1].split(" and ")]
                            ours = [x for x in (a, b) if x in mine]
                            theirs = [x for x in (a, b) if x not in mine]
                            # ours overlapping something placed before our furnisher ran is our fault
                            self.assertFalse(ours and (not theirs or order.get(trace.get(theirs[0]), 0) <= order[trace[ours[0]]]), line)
                            continue
                        self.assertFalse(blamed & mine, line)


TOKEN = __import__("re").compile(r"[A-Za-z][A-Za-z0-9_]*")


def true_ids_in(obj, entity_ids):
    """True entity ids (with an underscore, so legacy label-like ids are skipped) appearing anywhere in obj."""
    ids = {e for e in entity_ids if "_" in e}
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


class RealisticPerceptionTests(unittest.TestCase):
    """perception="realistic": textiles show glance-level facts until felt, inspected closely or read; look-alike
    socks and laundry jugs are confused at a glance; observers and messages leak no true ids or hidden values."""

    def _env(self, config, seed=0):
        env = HouseEnv(config, seed=seed, perception="realistic")
        return env, env.realism.handles.handle

    def _do(self, env, action, target="", destination="", **kw):
        result = env.step(action, target, destination, **kw)
        self.assertTrue(result["ok"], "{} {} refused: {}".format(action, target, result["message"]))
        return result

    def _json(self, result):
        import json
        return json.loads(result["message"])

    def test_glance_hides_textile_truth_and_feel_and_close_inspection_reveal_it_coarsely(self):
        config, p, s = build()
        towel = p.on(s["table"], "bath_towel", wetness=0.02, _core_wetness=0.3)
        shirt = p.on(s["table"], "dress_shirt", wetness=0.7, owner="resident_1", soil={"stain_wine": 0.3},
                     set_stains=["stain_wine"], size="L")
        env, H = self._env(config)
        obs = env.observe()
        glance = obs["visible_entities"][H(towel)]
        self.assertEqual(glance["label"], "bath towel")              # the camera sees what it is ...
        for hidden in ("fabric", "wetness", "_core_wetness", "dry_time_h", "max_touch_temp_c", "flat_size_m", "care",
                       "care_label", "drying_owner", "absorbency"):
            self.assertNotIn(hidden, glance)                          # ... not its fibre, moisture or model numbers
        sg = obs["visible_entities"][H(shirt)]
        self.assertEqual(set(sg["soil"]), {"overall"})                # a stain is a bucket at a glance
        for hidden in ("owner", "set_stains", "size", "wetness"):
            self.assertNotIn(hidden, sg)
        self.assertEqual(sg.get("appearance"), "dripping wet")     # the visible cue rides on the record (kernel v2.6)
        self.assertNotIn("textile_appearance", obs)
        self._do(env, "approach", H(s["table"]))
        felt = self._json(self._do(env, "inspect", H(towel)))
        self.assertEqual(felt["feel"], "dry")                         # the surface is dry ...
        self.assertNotIn("seams_feel", felt)
        close = self._json(self._do(env, "inspect", H(towel), where="closely"))
        self.assertEqual(close["seams_feel"], "damp")                 # ... the seams are not, felt as a word
        self.assertNotIn("seam_wetness", close)
        self.assertFalse(any(isinstance(v, float) and abs(v - 0.3) < 0.05 for v in close.values()))
        details = self._json(self._do(env, "inspect", H(shirt)))
        self.assertEqual(details["feel"], "dripping")
        self.assertIsInstance(details["soil"]["stain_wine"], str)   # per-kind bucket, not 0.3
        for hidden in ("owner", "set_stains", "size", "care_label"):
            self.assertNotIn(hidden, details)                         # owner is asked, size is read off the tag
        self.assertNotIn("size", env.observe()["visible_entities"][H(shirt)])
        label = self._json(self._do(env, "read", H(shirt)))
        self.assertIn("care_label", label)
        self.assertEqual(env.observe()["visible_entities"][H(shirt)]["size"], "L")

    def test_oracle_mode_keeps_exact_seam_wetness_and_appearance_on_the_record(self):
        config, p, s = build()
        towel = p.on(s["table"], "bath_towel", wetness=0.02, _core_wetness=0.3)
        shirt = p.on(s["table"], "dress_shirt", wetness=0.7)
        env = env_of(config)
        self.assertIsNone(env.realism)
        obs = env.observe()
        self.assertEqual(obs["visible_entities"][shirt]["appearance"], "dripping wet")
        self.assertNotIn("textile_appearance", obs)
        ok(env, "approach", s["table"])
        import json
        close = json.loads(ok(env, "inspect", towel, where="closely")["message"])
        self.assertAlmostEqual(close["seam_wetness"], 0.3, places=3)
        self.assertNotIn("marks", close)

    def test_look_alike_socks_are_confused_at_a_glance_and_told_apart_by_close_marks(self):
        config, p, s = build()
        mine = p.on(s["table"], "sock", color="black", length="crew", brand="Hanes", size="L", size_label="L 10-13", pair_id="p1")
        mate = p.on(s["table"], "sock", color="black", length="crew", brand="Hanes", size="L", size_label="L 10-13", pair_id="p1")
        decoy = p.on(s["table"], "sock", color="black", length="crew", brand="Hanes", size="S", size_label="Kids S", pair_id="p2")
        env, H = self._env(config)
        self._do(env, "approach", H(s["table"]))
        vis = env.observe()["visible_entities"]
        self.assertEqual(vis[H(mate)]["label"], vis[H(decoy)]["label"])
        self.assertIn("candidates", vis[H(decoy)])
        strip = ("location", "spos", "size_m", "candidates")
        self.assertEqual({k: v for k, v in vis[H(mate)].items() if k not in strip},
                         {k: v for k, v in vis[H(decoy)].items() if k not in strip})   # nothing at a glance tells them apart
        plain = [self._json(self._do(env, "inspect", H(e))) for e in (mate, decoy)]
        self.assertNotIn("marks", plain[0])
        marks = {e: self._json(self._do(env, "inspect", H(e), where="closely")).get("marks") for e in (mine, mate, decoy)}
        self.assertEqual(marks[mine], marks[mate])
        self.assertNotEqual(marks[mine], marks[decoy])
        self.assertEqual(marks[decoy]["size_label"], "Kids S")
        self.assertNotIn("pair_id", str(env.observe()))
        self._do(env, "pick_up", H(mine))
        self._do(env, "pair", H(mine), H(mate))
        self.assertEqual(ent(env, mate)["location"], mine)

    def test_laundry_jugs_are_look_alikes_until_their_labels_are_read(self):
        config, p, s = build()
        jug, soft = s["detergent"], s["softener_bottle"]
        env, H = self._env(config)
        vis = env.observe()["visible_entities"]
        self.assertEqual(vis[H(jug)]["label"], "plastic jug of laundry liquid")
        self.assertEqual(vis[H(soft)]["label"], vis[H(jug)]["label"])
        self.assertIn("fabric softener", vis[H(jug)]["candidates"])
        for hidden in ("liquid", "he", "label_text"):
            self.assertNotIn(hidden, vis[H(jug)])
        self._do(env, "approach", H(s["shelf"]))
        looked = self._json(self._do(env, "inspect", H(jug)))
        self.assertEqual(looked["label"], "plastic jug of laundry liquid")   # inspecting does not identify the liquid
        self.assertNotIn("liquid", looked)
        text = self._json(self._do(env, "read", H(jug)))
        self.assertIn("HE", text["label_text"])
        vis = env.observe()["visible_entities"]
        self.assertEqual(vis[H(jug)]["label"], "laundry detergent")
        self.assertTrue(vis[H(jug)]["he"])
        self.assertEqual(vis[H(soft)]["label"], "plastic jug of laundry liquid")   # the other jug is still unknown

    def test_softener_film_on_a_clean_looking_lint_screen_shows_only_in_the_water_test(self):
        config, p, s = build()
        screen = s["lint_screen"]
        config["entities"][screen].update(soil={"lint": 0.02}, film=0.6)
        env, H = self._env(config)
        self._do(env, "approach", H(s["dryer"]))
        self._do(env, "open", H(s["dryer"]))
        looked = self._json(self._do(env, "inspect", H(screen)))
        self.assertEqual(looked["lint_load"], "clean")
        self.assertNotIn("film", looked)
        self.assertNotIn("film", env.observe()["visible_entities"][H(screen)])
        close = self._json(self._do(env, "inspect", H(screen), where="closely"))
        self.assertIn("beads", close["water_test"])

    def test_actions_and_observers_leak_no_true_ids_or_exact_hidden_values(self):
        from house_sim.systems.textiles_laundry.helpers import station
        config, p, s = build()
        s.update(station(Ctx(config, p), need=["iron", "board"], iron_on_board=True))
        config["entities"][s["iron"]]["plugged_in"] = True
        shirt = p.on(s["board"], "dress_shirt", wrinkled=0.7)
        tee = p.on(s["table"], "t_shirt", wetness=0.5)
        cloth = p.on(s["table"], "napkin")
        env, H = self._env(config)
        ids = set(env.world.s["entities"])
        messages = []
        self._do(env, "approach", H(s["table"]))
        messages.append(self._do(env, "fold", H(cloth), style="half")["message"])
        self.assertIn("about", messages[-1])
        dims = ent(env, cloth)["size_m"]
        self.assertNotIn("{:.3f}".format(dims[2]), messages[-1])
        self._do(env, "approach", H(s["board"]))
        self._do(env, "pick_up", H(s["iron"]))
        self._do(env, "switch_on", H(s["iron"]))
        self._do(env, "set", H(s["iron"]), setting="temp", value="high")
        self._do(env, "wait", seconds=120)
        messages.append(self._do(env, "iron", H(shirt))["message"])
        self.assertNotIn(" C", messages[-1])                        # no plate temperature read off nothing
        self.assertIn("high", messages[-1])
        self._do(env, "switch_off", H(s["iron"]))
        obs = env.observe()
        self.assertEqual(true_ids_in(obs, ids), set())
        self.assertEqual(true_ids_in(messages, ids), set())
        for handle, rec in obs["visible_entities"].items():
            eid = env.realism.handles.resolve(handle)
            for hidden in ("owner", "set_stains", "dry_time_h", "last_cycle", "vent_airflow", "film", "pair_id",
                           "brand", "size_label", "care", "worn", "trapped_water_l", "hang_load_kg", "max_touch_temp_c"):
                self.assertNotIn(hidden, rec, (eid, hidden))
            if eid == s["iron"]:
                self.assertNotIsInstance(rec.get("temperature_c", ""), float)   # thermal glance: a bucket
        self.assertEqual(obs["visible_entities"][H(tee)].get("appearance"), "visibly wet")

    def test_making_the_bed_over_a_hidden_phone_does_not_hand_out_its_handle(self):
        from house_sim.systems.textiles_laundry.helpers import bed_helper
        config, p, s = build()
        bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
        out = bed_helper(Ctx(config, p), bed=bed, state="slept_in")
        phone = p.make("generic_item", out["cover"], label="phone", category="electronics", size_m=[0.07, 0.15, 0.01])
        config["entities"][phone]["relation"] = "under"
        env, H = self._env(config)
        self._do(env, "move", "hall")
        self._do(env, "move", "bedroom")
        self._do(env, "approach", H(bed))
        message = self._do(env, "make_bed", H(bed))["message"]
        self.assertIn("lumpy", message)
        self.assertNotIn(H(phone), message)
        self.assertNotIn(H(phone), str(env.observe()["visible_entities"]))
        self.assertTrue(ent(env, phone).get("buried_in_bed"))

    def test_sounds_name_the_machine_by_handle_and_unseen_ones_only_by_room(self):
        config, p, s = build()
        towels = [p.inside(s["washer"], "bath_towel") for _ in range(2)]
        self.assertTrue(all(towels))
        env, H = self._env(config)
        self._do(env, "approach", H(s["washer"]))
        if ent(env, s["washer"]).get("open"):
            self._do(env, "close", H(s["washer"]))
        self._do(env, "switch_on", H(s["washer"]))
        self._do(env, "start", H(s["washer"]))
        self._do(env, "wait", seconds=120)
        sounds = env.observe().get("sounds", [])
        self.assertTrue(sounds)
        self.assertTrue(all(snd.get("source") in (None, H(s["washer"])) for snd in sounds if snd["kind"].startswith("washer")))
        self._do(env, "move", "hall")
        far = env.observe()
        self.assertEqual(true_ids_in(far, set(env.world.s["entities"])), set())
        self.assertTrue(all("source" not in snd for snd in far.get("sounds", []) if snd["kind"].startswith("washer")))


class PerceptionHardeningTests(unittest.TestCase):
    """Kernel v2.8 round (red-team findings): smells give no counts, inspections give percepts instead of hidden
    numbers, look-alikes get identical answers until identified, durations do not read out hidden sizes, fields this
    domain writes on other domains' things follow its field_tiers."""

    def _env(self, config, seed=0):
        env = HouseEnv(config, seed=seed, perception="realistic")
        return env, env.realism.handles.handle

    def _do(self, env, action, target="", destination="", **kw):
        result = env.step(action, target, destination, **kw)
        self.assertTrue(result["ok"], "{} {} refused: {}".format(action, target, result["message"]))
        self.assertNotIn("evaluation", result)          # kernel v2.8: realistic steps carry no grader output
        return result

    def _no(self, env, action, target="", destination="", **kw):
        result = env.step(action, target, destination, **kw)
        self.assertFalse(result["ok"], "{} {} should have been refused".format(action, target))
        return result

    def _json(self, result):
        import json
        return json.loads(result["message"])

    @staticmethod
    def _musty(obs):
        return [s for s in obs.get("smells", []) if s.get("kind") == "musty_laundry"]

    def test_musty_smell_is_a_strength_word_not_an_item_count_and_a_sealed_bag_holds_it(self):
        words = []
        for n in (1, 2, 4):
            config, p, s = build()
            for _ in range(n):
                p.on(s["table"], "hand_towel", musty=True)
            env, H = self._env(config)
            smell = self._musty(env.observe())
            self.assertEqual(len(smell), 1)
            self.assertIsInstance(smell[0]["intensity"], str)
            words.append(smell[0]["intensity"])
            oracle = self._musty(env_of(config).observe())
            self.assertAlmostEqual(oracle[0]["intensity"], round(min(1.0, 0.2 * n), 2))
        self.assertEqual(words, ["faint", "noticeable", "strong"])
        config, p, s = build()
        bag = p.on(s["table"], "generic_item", label="vacuum storage bag", container=True, accepts_items=True, sealed=True,
                   size_m=[0.5, 0.4, 0.2], inner_size_m=[0.48, 0.38, 0.18], capacity_l=30.0)
        towel = p.inside(bag, "hand_towel", musty=True)
        self.assertTrue(towel)
        env, H = self._env(config)
        self.assertEqual(self._musty(env.observe()), [])          # nothing gets out of a sealed bag

    def test_inspection_gives_percepts_for_buttons_seams_pretreat_and_odor_not_hidden_numbers(self):
        config, p, s = build()
        shirt = p.on(s["table"], "dress_shirt", odor={"musty": 0.6}, odor_kind="musty", musty=True,
                     buttons={"b1": {"state": "sewn_badly", "holes": 4, "size_mm": 11, "pattern": "cross", "position_error_mm": 0.5,
                                     "holds_n": 2.0, "crooked": False, "fastens": True},
                              "b2": {"state": "ok", "holes": 4, "size_mm": 11, "pattern": "cross", "position_error_mm": 0.5,
                                     "holds_n": 40.0, "crooked": False, "fastens": True}},
                     seams={"side": {"split_cm": 4.3, "kind": "seam", "opening_gap_cm": 3.0}},
                     pretreat={"stain_wine": {"agent": "oxygen", "t": 0.0, "eff": 0.92, "method": "blot", "ring_risk": True}},
                     soil={"stain_wine": 0.2}, fold_quality={"align_cm": 2.7, "lumpy": False, "neat": True},
                     secured={"by": "clothespins", "rating_n": 800.0})
        env, H = self._env(config)
        self._do(env, "approach", H(s["table"]))
        plain = self._json(self._do(env, "inspect", H(shirt)))
        close = self._json(self._do(env, "inspect", H(shirt), where="closely"))
        for d in (plain, close):
            self.assertEqual(d["odor"], "musty")
            self.assertEqual(d["pretreat"], {"stain_wine": "treated"})            # never the agent's efficacy
            self.assertEqual(d["buttons"]["b1"]["state"], d["buttons"]["b2"]["state"])   # a weak knot looks like a good one
            for hidden in ("holds_n", "position_error_mm", "size_mm"):
                self.assertNotIn(hidden, str(d["buttons"]))
            self.assertNotIn("split_cm", str(d["seams"]))
            self.assertNotIn("align_cm", str(d.get("fold_quality")))
        self.assertEqual(plain["seams"]["side"]["split"], "small")
        self.assertEqual(close["seams"]["side"]["split"], "about 4 cm")
        rec = env.observe()["visible_entities"][H(shirt)]
        for hidden in ("odor", "odor_kind", "pretreat", "buttons", "seams"):
            self.assertNotIn(hidden, rec)
        self.assertNotIn("align_cm", rec.get("fold_quality", {}))
        self.assertNotIn("rating_n", rec.get("secured", {}))
        oracle = env_of(config)
        ok(oracle, "approach", s["table"])
        import json
        exact = json.loads(ok(oracle, "inspect", shirt)["message"])
        self.assertEqual(exact["buttons"]["b1"]["holds_n"], 2.0)                  # oracle mode is unchanged
        self.assertEqual(exact["seams"]["side"]["split_cm"], 4.3)

    def test_look_alike_sheets_and_covers_get_one_answer_until_the_robot_has_looked(self):
        config, p, s = build()
        fitted = p.on(s["table"], "fitted_sheet")
        flat = p.on(s["table"], "flat_sheet")
        self.assertTrue(fitted and flat)
        env, H = self._env(config)
        self._do(env, "approach", H(s["table"]))
        vis = env.observe()["visible_entities"]
        self.assertEqual(vis[H(fitted)]["label"], vis[H(flat)]["label"])
        folds = [self._no(env, "fold", H(e), style="quarters") for e in (fitted, flat)]
        self.assertEqual({(r["message"], r["category"]) for r in folds}, {(folds[0]["message"], folds[0]["category"])})
        self.assertIn("inspect", folds[0]["message"])
        self._do(env, "inspect", H(flat))
        self._do(env, "fold", H(flat), style="quarters")                         # identified: it folds
        self._do(env, "inspect", H(fitted))
        refused = self._no(env, "fold", H(fitted), style="quarters")
        self.assertIn("fitted_nest", refused["message"])                         # now the robot knows what it holds
        messages = [r["message"] for r in folds] + [refused["message"]]
        config, p, s = build()
        case = p.on(s["table"], "pillowcase")
        sham = p.on(s["table"], "pillow_sham")
        pillow = p.on(s["table"], "bed_pillow")
        self.assertTrue(case and sham and pillow)
        env, H = self._env(config)
        self._do(env, "approach", H(s["table"]))
        covers = []
        for cover in (case, sham):
            self._do(env, "pick_up", H(cover))
            covers.append(self._no(env, "dress_bed", H(cover), H(pillow)))
            self._do(env, "place", H(cover), H(s["table"]))
        self.assertEqual({(r["message"], r["category"]) for r in covers}, {(covers[0]["message"], covers[0]["category"])})
        self.assertIn("inspect", covers[0]["message"])
        self._do(env, "inspect", H(case))
        self._do(env, "pick_up", H(case))
        self._do(env, "dress_bed", H(case), H(pillow))
        messages += [r["message"] for r in covers]
        self.assertEqual(true_ids_in(messages, set(env.world.s["entities"])), set())

    def test_socks_and_novel_textiles_are_not_held_up_by_the_look_alike_rule(self):
        config, p, s = build()
        sock = p.on(s["table"], "sock", color="black", brand="Hanes", size_label="L 10-13")
        odd = p.on(s["table"], "t_shirt", novel=True)
        env, H = self._env(config)
        self._do(env, "approach", H(s["table"]))
        self.assertIn("candidates", env.observe()["visible_entities"][H(sock)])     # an uninspected sock is unresolved
        self._do(env, "fold", H(sock), style="ball")                             # every sock folds alike: no wait
        self._do(env, "fold", H(odd), style="half")                              # a novel thing never resolves

    def test_an_unread_jug_is_applied_not_refused_and_a_set_stain_is_not_announced(self):
        config, p, s = build()
        shirt = p.on(s["table"], "dress_shirt", soil={"stain_wine": 0.3}, set_stains=["stain_wine"])
        trace = p.on(s["table"], "t_shirt", soil={"stain_coffee": 0.01})
        jug = p.on(s["table"], "fabric_softener")
        env, H = self._env(config)
        self._do(env, "approach", H(s["table"]))
        self._do(env, "pick_up", H(jug))
        self.assertEqual(env.observe()["visible_entities"][H(jug)]["label"], "plastic jug of laundry liquid")
        message = self._do(env, "treat_stain", H(shirt), agent=H(jug), method="blot")["message"]
        self.assertNotIn("set", message)                                         # a set stain looks like any stain
        self.assertNotIn("softener", message)
        looked = self._json(self._do(env, "inspect", H(shirt)))
        self.assertEqual(looked["pretreat"], {"stain_wine": "treated"})          # the same whatever the jug held
        refused = self._no(env, "treat_stain", H(trace), stain="stain_coffee")
        self.assertNotIn("stain_coffee", refused["message"])                     # an invisible trace is not "found"
        oracle = env_of(config)
        ok(oracle, "approach", s["table"])
        ok(oracle, "pick_up", jug)
        self.assertIn("not a stain treatment", no(oracle, "treat_stain", shirt, agent=jug)["message"])

    def test_ironing_time_does_not_read_out_the_exact_flat_size(self):
        from house_sim.systems.textiles_laundry.helpers import station
        spans = {}
        for mode in ("realistic", "oracle"):
            for size in ([0.61, 0.83], [0.66, 0.87]):
                config, p, s = build()
                s.update(station(Ctx(config, p), need=["iron", "board"], iron_on_board=True))
                config["entities"][s["iron"]]["plugged_in"] = True
                shirt = p.on(s["board"], "dress_shirt", wrinkled=0.7, flat_size_m=size)
                env = HouseEnv(config, seed=0, perception=mode)
                H = env.realism.handles.handle if env.realism else (lambda e: e)
                for action, target, kw in (("approach", s["board"], {}), ("pick_up", s["iron"], {}), ("switch_on", s["iron"], {}),
                                           ("set", s["iron"], {"setting": "temp", "value": "high"}), ("wait", "", {"seconds": 120})):
                    self.assertTrue(env.step(action, H(target) if target else "", **kw)["ok"], action)
                t0 = env.world.s["time_s"]
                self.assertTrue(env.step("iron", H(shirt))["ok"])
                spans[(mode, size[0])] = round(env.world.s["time_s"] - t0, 3)
        self.assertEqual(spans[("realistic", 0.61)], spans[("realistic", 0.66)])
        self.assertEqual(spans[("realistic", 0.61)] % 30, 0)
        self.assertNotEqual(spans[("oracle", 0.61)], spans[("oracle", 0.66)])     # oracle keeps the exact physics

    def test_fields_written_on_other_things_follow_field_tiers_and_the_bed_shows_a_look(self):
        from house_sim.systems.textiles_laundry.helpers import bed_helper
        config, p, s = build()
        cloth = p.on(s["table"], "generic_item", label="cleaning rag", launderable=True, wetness=0.5, size_m=[0.3, 0.3, 0.01])
        bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
        bed_helper(Ctx(config, p), bed=bed, state="slept_in")
        env, H = self._env(config)
        self.assertEqual(ent(env, cloth).get("drying_owner"), "textiles_laundry")
        self._do(env, "approach", H(s["table"]))
        looked = self._json(self._do(env, "inspect", H(cloth)))
        self.assertNotIn("drying_owner", looked)
        self.assertNotIn("drying_owner", env.observe()["visible_entities"][H(cloth)])
        self._do(env, "move", "hall")
        self._do(env, "move", "bedroom")
        self._do(env, "approach", H(bed))
        details = self._json(self._do(env, "inspect", H(bed)))
        state = details.get("bed_state") or {}
        for hidden in ("wrinkle", "duvet_offset_cm", "flat_offset_cm"):
            self.assertNotIn(hidden, state)
        self.assertEqual(details["bed_look"]["wrinkles"], "rumpled")
        self.assertIn("off centre", details["bed_look"]["duvet"])

    def test_sewing_refusals_do_not_list_what_the_robot_has_not_looked_at(self):
        config, p, s = build()
        shirt = p.on(s["table"], "dress_shirt", seams={"side": {"split_cm": 4.0, "kind": "seam", "opening_gap_cm": 3.0}})
        kit = sewing_kit_for(config, p, s)
        env, H = self._env(config)
        self._do(env, "approach", H(s["table"]))
        self._do(env, "pick_up", H(kit["needle"]))
        self._do(env, "thread_needle", H(kit["needle"]), thread=H(kit["spools"][0]))
        refused = self._no(env, "repair_seam", H(shirt), seam="collar")
        self.assertNotIn("side", refused["message"])


def sewing_kit_for(config, p, s):
    from house_sim.systems.textiles_laundry.helpers import sewing_kit
    return sewing_kit(Ctx(config, p), s["table"], frayed=False)


if __name__ == "__main__":
    unittest.main()
