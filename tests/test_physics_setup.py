"""Tier-1 physics for authors (PHYS-INT, kernel v3.0): the setup ops phys, load, edge, stage and check_physics, the
physics lint the verifier runs after setup, the physics.* perturbation events, and the core verbs that now go through
the physics layer (pick_up's lift plan, place's containment, set-down and broken-support refusals, the kernel's one
subtree mass) (docs/design/TIER1_PHYSICS.md 5.5-5.6, 7.4, 7.5, 12.5; TIER1_INTEGRATION.md 1.5, 1.13, 1.15).

Scenes use core types and generic items only, so the file passes with HOUSE_SIM_DOMAINS=core too.
"""
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.errors import SpecError
from house_sim.kernel.phys import lawbook, statics, support
from house_sim.tasks.setup import PHYS_FIELDS, apply_setup, physics_problems
from tests import test_physics_settle as S

SPEC = {"catalog_id": "T-physics-setup-test"}


def setup(cfg, ops, spec=None):
    return apply_setup(cfg, ops, S.REG, 0, dict(spec or SPEC))


def furnished(**extra):
    scene = {"shelf": S.make("bookshelf", "kitchen", pos=[2, 0]), "table": S.make("table", "kitchen", pos=[2, 2]),
             "g": S.glass("kitchen", pos=[1, 3])}
    scene.update(extra)
    return S.config(scene)


class PhysOpTests(unittest.TestCase):
    def test_validated_overrides_reach_the_physical_properties(self):
        cfg = furnished()
        setup(cfg, [{"op": "phys", "target": "g", "props": {"drop_tolerance_m": 0.9, "material": "borosilicate",
                                                             "friction_mu": 0.3, "opening_m": [0.05, 0.05],
                                                             "secured": {"rating_n": 50}}}])
        env = HouseEnv(cfg, seed=0)
        p = env.world.s
        from house_sim.kernel.phys import props
        phys = props.props(p, "g")
        self.assertEqual((phys.cls, phys.drop_tol_m, phys.mu, phys.opening_m), ("borosilicate", 0.9, 0.3, (0.05, 0.05)))

    def test_unknown_fields_bad_values_and_unknown_targets_are_spec_errors(self):
        for props, words in (({"colour": "red"}, "not a physical field"), ({"drop_tolerance_m": -1}, "at least 0"),
                             ({"com_frac": 0}, "above 0"), ({"material": "unobtainium"}, "known material"),
                             ({"shape": "torus"}, "one of"), ({"opening_m": [0.1]}, "diameter"),
                             ({"secured": "yes"}, "rating_n"), ({"tilt_deg": 120}, "at most 90"),
                             ({"fragile": 1}, "true or false"), ({}, "non-empty")):
            with self.assertRaises(SpecError) as caught:
                setup(furnished(), [{"op": "phys", "target": "g", "props": props}])
            self.assertIn(words, str(caught.exception), props)
        with self.assertRaises(SpecError):
            setup(furnished(), [{"op": "phys", "target": "ghost", "props": {"fragile": True}}])
        self.assertIn("load_rating_text", PHYS_FIELDS)
        self.assertNotIn("temperature_c", PHYS_FIELDS)              # state, not a property: use set


class LoadOpTests(unittest.TestCase):
    def test_fills_a_shelf_to_exactly_the_load_with_filler_items(self):
        cfg = furnished()
        bindings = setup(cfg, [{"op": "load", "target": "shelf", "shelf": 2, "kg": 18.4, "var": "books"},
                               {"op": "load", "target": "shelf", "shelf": 3, "fraction": 0.8, "items": "cans",
                                "var": "cans"}])
        entities = cfg["entities"]
        books = bindings["books"]
        self.assertEqual(len(books), 19)                                     # 18 books of 1 kg and one of 0.4 kg
        self.assertAlmostEqual(sum(entities[b]["mass_kg"] for b in books), 18.4)
        self.assertEqual({(entities[b]["location"], entities[b]["shelf"]) for b in books}, {("shelf", 2)})
        self.assertAlmostEqual(sum(entities[c]["mass_kg"] for c in bindings["cans"]), 0.8 * 25.0)   # nominal rating
        env = HouseEnv(cfg, seed=0)
        self.assertAlmostEqual(support.load_on(env.world.s, "shelf", ("shelf", 2)), 18.4)

    def test_loads_a_surface_and_refuses_what_does_not_fit(self):
        cfg = furnished()
        bindings = setup(cfg, [{"op": "load", "target": "table", "kg": 3.0, "items": "boxes", "var": "b"}])
        self.assertEqual([cfg["entities"][e]["location"] for e in bindings["b"]], ["table"])
        for op, words in (({"op": "load", "target": "table", "kg": 400, "items": "boxes"}, "no room"),
                          ({"op": "load", "target": "shelf", "shelf": 9, "kg": 1}, "index"),
                          ({"op": "load", "target": "table", "shelf": 0, "kg": 1}, "no shelves"),
                          ({"op": "load", "target": "table", "kg": -2}, "positive"),
                          ({"op": "load", "target": "table"}, "kg or fraction"),
                          ({"op": "load", "target": "g", "fraction": 0.5}, "cannot hold"),
                          ({"op": "load", "target": "table", "kg": 1, "items": "bricks"}, "items must be")):
            with self.assertRaises(SpecError) as caught:
                setup(furnished(), [op])
            self.assertIn(words, str(caught.exception), op)


class EdgeOpTests(unittest.TestCase):
    def test_puts_an_item_a_margin_from_an_edge_and_allows_a_small_overhang(self):
        for margin in (0.03, 0.0, -0.02):
            cfg = furnished()
            setup(cfg, [{"op": "edge", "target": "g", "on": "table", "margin_m": margin, "side": "front"}])
            env = HouseEnv(cfg, seed=0)
            s = env.world.s
            self.assertEqual(s["entities"]["g"]["location"], "table")
            frame = statics.surface_frame(s, "g")
            gaps = [frame[2], frame[0] - frame[2] - frame[4], frame[3], frame[1] - frame[3] - frame[5]]
            self.assertAlmostEqual(min(gaps), margin, places=3)
            self.assertGreaterEqual(statics.edge_margin_m(s, "g"), 0.0)          # still statically stable
        with self.assertRaises(SpecError):
            setup(furnished(), [{"op": "edge", "target": "g", "on": "table", "margin_m": -0.03}])   # > 30 % of 8 cm
        with self.assertRaises(SpecError):
            setup(furnished(), [{"op": "edge", "target": "g", "on": "table", "side": "top"}])

    def test_each_side_is_a_different_edge(self):
        spots = set()
        for side in ("front", "back", "left", "right"):
            cfg = furnished()
            setup(cfg, [{"op": "edge", "target": "g", "on": "table", "margin_m": 0.01, "side": side}])
            spots.add(tuple(cfg["entities"]["g"]["spos"]))
        self.assertEqual(len(spots), 4)


class StageOpTests(unittest.TestCase):
    def overloaded(self, creep_s=600, unload=False):
        cfg = furnished()
        bindings = setup(cfg, [{"op": "load", "target": "shelf", "shelf": 2, "kg": 32, "var": "books"},
                               {"op": "stage", "target": "shelf", "state": "overloaded", "creep_s": creep_s}])
        env = HouseEnv(cfg, seed=0)
        if unload:
            w = env.world
            w.begin()
            for eid in bindings["books"][:12]:
                m = w.mut(eid)
                m.pop("shelf", None)
                m.update(location="kitchen", relation="on", pos=[4, 3])
            w.commit()
        return env, bindings

    def test_an_about_to_give_way_shelf_holds_until_its_creep_time(self):
        env, _ = self.overloaded()
        s = env.world.s
        self.assertTrue(s["entities"]["shelf"]["_phys_staged"])
        self.assertEqual(s["entities"]["shelf"]["phys_creep_s"], 600.0)
        S.ok(self, env, "wait", seconds=570)
        self.assertNotIn("collapsed_shelves", env.world.s["entities"]["shelf"])
        S.ok(self, env, "wait", seconds=60)
        self.assertEqual(env.world.s["entities"]["shelf"]["collapsed_shelves"], [2])
        self.assertEqual(env.world.s["stats"]["breakages"], 0)                   # the scene's doing, not the robot's

    def test_unloaded_in_time_it_holds(self):
        env, _ = self.overloaded(unload=True)
        S.ok(self, env, "wait", seconds=1200)
        self.assertNotIn("collapsed_shelves", env.world.s["entities"]["shelf"])

    def test_a_staged_shelf_sags_at_a_glance_before_its_creep_time_and_stops_once_unloaded(self):
        """Stage-B review (physics, medium): the sagging cue came only from the deferred overload law, so a shelf staged
        "about to give way" looked sound in both perception modes until it collapsed. The cue is now the staged
        support's own (setup, settle and the creep check), in either op order and whatever the overload law's state."""
        for order in ("load_first", "stage_first"):
            cfg = furnished()
            ops = [{"op": "load", "target": "shelf", "shelf": 2, "kg": 32, "var": "books"},
                   {"op": "stage", "target": "shelf", "state": "overloaded", "creep_s": 1200}]
            bindings = setup(cfg, ops if order == "load_first" else ops[::-1])
            self.assertFalse(lawbook.active("overload"))
            env = HouseEnv(cfg, seed=0, perception="realistic")
            shelf = env.world.s["entities"]["shelf"]
            self.assertTrue(shelf["sagging"] and shelf["_phys_sag"], order)
            S.ok(self, env, env.realism.to_handles("approach"), env.realism.to_handles("shelf"))
            seen = env.observe()["visible_entities"][env.realism.handles.handle("shelf")]
            self.assertTrue(seen.get("sagging"), (order, seen))                  # an honest glance cue
            self.assertNotIn("_phys_sag", seen)
            # the robot unloads it in time: settle refreshes the cue (no collapse, no law needed)
            w = env.world
            w.begin()
            S.settle.begin_action(w)
            for eid in bindings["books"][:12]:
                m = w.mut(eid)
                m.pop("shelf", None)
                m.update(location="kitchen", relation="on", pos=[4, 3])
            S.settle.settle(env, "action")
            w.commit()
            self.assertNotIn("sagging", env.world.s["entities"]["shelf"], order)
            self.assertNotIn("_phys_sag", env.world.s["entities"]["shelf"], order)
            S.ok(self, env, "wait", seconds=1500)
            self.assertNotIn("collapsed_shelves", env.world.s["entities"]["shelf"], order)
        oracle, _ = self.overloaded()
        self.assertTrue(oracle.observe()["visible_entities"]["shelf"]["sagging"])

    def test_bad_stage_requests(self):
        for op in ({"op": "stage", "target": "shelf", "state": "wobbly"},
                   {"op": "stage", "target": "shelf", "creep_s": 0}, {"op": "stage", "target": "ghost"}):
            with self.assertRaises(SpecError):
                setup(furnished(), [op])


class CheckPhysicsTests(unittest.TestCase):
    CASES = (
        ("more than its rating", [{"op": "set", "target": "shelf", "props": {"shelf_load_kg": 1.0, "phys_exact": True}},
                             {"op": "load", "target": "shelf", "shelf": 1, "kg": 4}]),
        ("not a surface", [{"op": "spawn", "type": "generic_item", "floor": "kitchen", "var": "x"},
                           {"op": "set", "target": "g", "props": {"location": "{x}", "relation": "on"}}]),
        ("larger than", [{"op": "spawn", "type": "generic_item", "on": "table", "var": "x"},
                         {"op": "set", "target": "{x}", "props": {"size_m": [2.0, 0.5, 0.1]}}]),
        ("centre of mass off", [{"op": "move", "target": "g", "to": "table"},
                                {"op": "set", "target": "g", "props": {"spos": [1.17, 0.3]}}]),
        ("does not fit", [{"op": "set", "target": "g", "props": {"location": "shelf", "relation": "in", "shelf": 0,
                                                                 "size_m": [0.5, 0.5, 0.5]}}]),
        ("more liquid than it can", [{"op": "set", "target": "g", "props": {"capacity_l": 0.3, "liquid_l": 0.5,
                                                                            "liquid": "water"}}]),
        ("open with liquid", [{"op": "set", "target": "g", "props": {"capacity_l": 0.3, "liquid_l": 0.2,
                                                                     "liquid": "water", "orientation": "on_side"}}]),
        ("too hot", [{"op": "move", "target": "g", "to": "table"},
                     {"op": "set", "target": "g", "props": {"temperature_c": 200.0}}]),
    )

    def test_each_impossible_scene_is_rejected_and_staged_ones_are_exempt(self):
        for words, ops in self.CASES:
            with self.assertRaises(SpecError) as caught:
                setup(furnished(), ops + [{"op": "check_physics", "strict": True}])
            self.assertIn(words, str(caught.exception), words)
            bindings = setup(furnished(), ops)                               # an older spec: warnings only
            self.assertTrue(any(words in p for p in bindings["_physics_warnings"]), (words, bindings))
            with self.assertRaises(SpecError):
                setup(furnished(), ops, spec=dict(SPEC, schema=3))           # a tier-1 spec: an error
        bindings = setup(furnished(), self.CASES[0][1] + [{"op": "stage", "target": "shelf"},
                                                          {"op": "check_physics"}])
        self.assertNotIn("_physics_warnings", bindings)

    def test_a_clean_scene_and_the_generated_house_are_not_linted(self):
        bindings = setup(furnished(), [{"op": "move", "target": "g", "to": "table"},
                                       {"op": "load", "target": "shelf", "shelf": 1, "kg": 10},
                                       {"op": "check_physics", "strict": True}])
        self.assertNotIn("_physics_warnings", bindings)
        from house_sim.generator import generate_house
        from house_sim.tasks.setup import SetupCtx
        import random
        cfg = generate_house(seed=3)
        ctx = SetupCtx(cfg, S.REG, random.Random(0), 3, SPEC)
        self.assertEqual(physics_problems(ctx), [])                         # untouched: nothing to judge

    def test_non_strict_check_collects_warnings_without_failing(self):
        bindings = setup(furnished(), self.CASES[5][1] + [{"op": "check_physics", "strict": False}])
        self.assertEqual(bindings["_physics_warnings"], ["g holds more liquid than it can"])

    def test_the_verifier_records_the_warnings(self):
        from house_sim.tasks import verify
        spec = {"catalog_id": "T-physics-lint-test", "instruction": "Pick up the glass.", "house": {},
                "setup": [{"op": "room", "var": "k", "type": "kitchen"},
                          {"op": "spawn", "var": "g", "type": "generic_item", "floor": "{k}",
                           "props": {"capacity_l": 0.3, "liquid_l": 0.5, "liquid": "water"}}],
                "success": [{"path": "entities.{g}.location", "op": "eq", "value": "robot"}],
                "reference": [{"skill": "fetch", "obj": "{g}"}], "wrong": []}
        trial = verify.run_trial(spec, 0)
        self.assertTrue(any("more liquid" in w for w in trial.get("physics_warnings", [])), trial)


class WeatherOpTests(unittest.TestCase):
    """Adoption-round requests (manipulation 11, plumbing_liquids 4): the weather op was a plain shallow update, so a
    spec's calm 2 m/s wind kept a generation-2 day's drawn 7-10 m/s gust (light outdoor things blew away at once) and
    a spec's condition kept the day's precipitation rates."""

    def weather(self, day, op):
        cfg = S.config()
        cfg["weather"] = dict(day)
        setup(cfg, [dict(op, op="weather")])
        return cfg["weather"]

    def test_a_given_wind_resets_a_generated_gust(self):
        day = {"condition": "cloudy", "wind_mps": 6.8, "gust_mps": 10.2, "rain_mm_h": 0.0, "snow_cm_h": 0.0}
        self.assertEqual(self.weather(day, {"wind_mps": 2.0})["gust_mps"], 3.0)
        self.assertEqual(self.weather(day, {"wind_mps": 2.0, "gust_mps": 6.0})["gust_mps"], 6.0)   # stated: kept
        self.assertEqual(self.weather({"condition": "clear"}, {"wind_mps": 2.0}), {"condition": "clear", "wind_mps": 2.0})

    def test_a_new_condition_drops_the_days_rates(self):
        day = {"condition": "rain", "wind_mps": 3.0, "gust_mps": 4.5, "rain_mm_h": 6.2, "snow_cm_h": 0.0}
        self.assertEqual(self.weather(day, {"condition": "clear"}),
                         {"condition": "clear", "wind_mps": 3.0, "gust_mps": 4.5})
        self.assertEqual(self.weather(day, {"condition": "storm", "rain_mm_h": 20.0})["rain_mm_h"], 20.0)
        self.assertEqual(self.weather(day, {"condition": "rain"})["rain_mm_h"], 6.2)          # the day's own rain
        self.assertEqual(self.weather(day, {"temperature_c": 4.0})["rain_mm_h"], 6.2)


class EventTests(unittest.TestCase):
    """Every physics.* event, scheduled with the event setup op and fired by the engine."""

    def run_event(self, scene, effect, args, seconds=60, at_s=10, laws=None, cfg_extra=None):
        cfg = S.config(scene, events=[{"at_s": at_s, "effect": effect, "args": args}])
        cfg.update(cfg_extra or {})
        env = HouseEnv(cfg, seed=0)
        if laws:
            with lawbook.forced(**laws):
                S.ok(self, env, "wait", seconds=seconds)
        else:
            S.ok(self, env, "wait", seconds=seconds)
        return env

    def test_bump_knocks_what_is_on_a_surface(self):
        env = self.run_event(S.table_scene(g=S.glass("table", relation="on", spos=[1.13, 0.36]),
                                           b=S.make("book", "table", relation="on", spos=[0.2, 0.2])),
                             "physics.bump", {"target": "table", "g": 0.9})
        self.assertEqual(env.world.s["entities"]["g"]["location"], "kitchen")      # off the edge, broken
        self.assertTrue(env.world.s["entities"]["g"]["broken"])
        self.assertEqual(env.world.s["entities"]["b"]["location"], "table")        # a book in the middle stays
        low = self.run_event(S.table_scene(g=S.glass("table", relation="on", spos=[1.13, 0.36])),
                             "physics.bump", {"room": "kitchen", "g": 0.9, "max_h_m": 0.6})
        self.assertEqual(low.world.s["entities"]["g"]["location"], "table")        # a tail sweep stays low

    def test_nudge_tips_a_tall_glass_pushed_high_and_slides_a_squat_one(self):
        tall = self.run_event(S.table_scene(g=S.glass("table", relation="on", spos=[0.5, 0.3],
                                                      size_m=[0.07, 0.07, 0.2])),
                              "physics.nudge", {"target": "g", "newtons": 1.0, "height_m": 0.18, "direction": 0})
        self.assertTrue(tall.world.s["entities"]["g"]["tipped_over"])
        squat = self.run_event(S.table_scene(g=S.glass("table", relation="on", spos=[0.5, 0.3],
                                                       size_m=[0.1, 0.1, 0.05])),
                               "physics.nudge", {"target": "g", "newtons": 1.5, "height_m": 0.02, "direction": 0})
        rec = squat.world.s["entities"]["g"]
        self.assertNotIn("tipped_over", rec)
        self.assertGreater(rec["spos"][0], 0.5)

    def test_pull_tips_an_unanchored_dresser_and_not_an_anchored_one(self):
        for anchored in (False, True):
            scene = {"dresser": S.make("dresser", "kitchen", pos=[2, 0], anchored=anchored, phys_exact=True)}
            env = self.run_event(scene, "physics.pull", {"target": "dresser", "kg": 60, "at": "top_drawer"})
            self.assertEqual(bool(env.world.s["entities"]["dresser"].get("tipped_over")), not anchored)

    def test_load_test_breaks_a_weak_shelf(self):
        scene = {"shelf": S.make("wall_shelf", "kitchen", pos=[2, 0], mounted=True, mount_height_m=1.5,
                                 phys_exact=True, max_load_kg=12.0)}
        weak = self.run_event(scene, "physics.load_test", {"target": "shelf", "kg": 20})
        self.assertGreater(len([e for e in weak.world.s["event_log"] if e["kind"] in ("phys.pullout",
                                                                                     "phys.collapse")]), 0)
        strong = self.run_event(dict(scene), "physics.load_test", {"target": "shelf", "kg": 5})
        self.assertFalse([e for e in strong.world.s["event_log"] if str(e["kind"]).startswith("phys.")])

    def test_wind_gust_and_rain_over_the_yard(self):
        napkin = S.item("yard", pos=[2, 2], label="paper napkin", material="paper", mass_kg=0.004,
                        size_m=[0.4, 0.4, 0.002])
        env = self.run_event({"napkin": napkin}, "physics.wind_gust", {"outdoor": True, "mps": 12.0})
        self.assertTrue(env.world.s["entities"]["napkin"]["blown_away"])
        paper = S.item("yard", pos=[2, 2], label="newspaper", material="paper", mass_kg=0.3,
                       size_m=[0.3, 0.4, 0.01])
        wet = self.run_event({"paper": paper}, "physics.rain", {"mm_h": 8.0, "seconds": 600})
        self.assertEqual(wet.world.s["entities"]["paper"]["wetness"], 1.0)
        self.assertGreater(wet.world.s["entities"]["paper"]["moisture"], 0.0)

    def test_transport_breaks_an_unpadded_glass_in_a_box_and_not_a_wrapped_one(self):
        for wrapped, broken in ((None, True), ({"material": "bubble_wrap", "padding_mm": 12, "coverage": 1.0}, False)):
            props = {"wrapped": wrapped} if wrapped else {}
            scene = {"box": S.make("box", "kitchen", pos=[3, 2], open=True),
                     "g": S.glass("box", relation="in", **props)}
            env = self.run_event(scene, "physics.transport", {"target": "box", "drop_m": 0.76, "drops": 2})
            self.assertEqual(bool(env.world.s["entities"]["g"].get("broken")), broken, wrapped)

    def test_brake_knocks_a_load_forward(self):
        scene = S.table_scene(g=S.glass("table", relation="on", spos=[1.15, 0.36], size_m=[0.08, 0.08, 0.08]))
        env = self.run_event(scene, "physics.brake", {"target": "table", "accel_g": 0.6, "direction": 0})
        self.assertEqual(env.world.s["entities"]["g"]["location"], "kitchen")

    def test_tilt_spills_an_open_mug_and_rolls_a_ball(self):
        scene = {"tray": S.make("tray", "kitchen", pos=[3, 2], phys_exact=True),
                 "m": S.mug("tray", relation="on", spos=[0.1, 0.1], liquid="water", liquid_l=0.3),
                 "ball": S.item("tray", relation="on", spos=[0.35, 0.1], label="ball", material="rubber",
                                mass_kg=0.05, size_m=[0.06, 0.06, 0.06], shape="sphere")}
        env = self.run_event(scene, "physics.tilt", {"target": "tray", "deg": 30, "direction_deg": 0}, seconds=120)
        s = env.world.s
        self.assertEqual(s["entities"]["tray"]["tilt_deg"], 30.0)
        self.assertLess(s["entities"]["m"]["liquid_l"], 0.3)
        self.assertEqual(s["entities"]["ball"]["location"], "kitchen")            # it rolled off

    def test_drop_breaks_a_glass_dropped_from_hand_height(self):
        env = self.run_event(S.table_scene(g=S.glass("table", relation="on", spos=[0.5, 0.3])),
                             "physics.drop", {"target": "g", "height_m": 1.2})
        self.assertTrue(env.world.s["entities"]["g"]["broken"])
        self.assertEqual(env.world.s["stats"]["breakages"], 0)
        self.assertEqual([e["by"] for e in env.world.s["event_log"] if e["kind"] == "phys.break"], ["exogenous"])

    def test_events_on_missing_targets_do_nothing(self):
        for effect in ("physics.bump", "physics.nudge", "physics.pull", "physics.load_test", "physics.transport",
                       "physics.brake", "physics.tilt", "physics.drop"):
            env = self.run_event(S.table_scene(), effect, {"target": "ghost", "kg": 5, "newtons": 5, "deg": 10})
            self.assertFalse([e for e in env.world.s["event_log"] if str(e["kind"]).startswith("phys.")], effect)


class CoreVerbTests(unittest.TestCase):
    def test_pick_up_refuses_with_todays_texts(self):
        heavy = S.item("kitchen", pos=[1, 2], label="sack", material="cotton", mass_kg=25.0, size_m=[0.5, 0.3, 0.3])
        env = S.env_of({"sack": heavy, "b": S.make("book", "kitchen", pos=[2, 2]),
                        "c": S.make("book", "kitchen", pos=[2, 1])})
        for command in (("crouch",),):
            S.ok(self, env, *command)
        result = env.step("pick_up", "sack")
        self.assertEqual((result["message"], result["category"]),
                         ("Object and contents exceed carrying capacity; get help or slide it", "safety"))
        S.ok(self, env, "pick_up", "b")
        result = env.step("pick_up", "sack")
        self.assertEqual((result["message"], result["category"]), ("Both hands must be free for this item", "capability"))
        S.ok(self, env, "pick_up", "c")
        result = env.step("pick_up", "b", hand="left")
        self.assertEqual(result["message"], "Already holding it")

    def test_no_free_hand(self):
        env = S.env_of({"b": S.make("book", "kitchen", pos=[2, 2]), "c": S.make("book", "kitchen", pos=[2, 1]),
                        "d": S.make("book", "kitchen", pos=[1, 2])})
        S.ok(self, env, "crouch")
        S.ok(self, env, "pick_up", "b")
        S.ok(self, env, "pick_up", "c")
        result = env.step("pick_up", "d")
        self.assertEqual((result["message"], result["category"]), ("No free hand; place something first", "capability"))

    def test_placing_an_open_mug_upside_down_pours_it_out(self):
        """Stage-B review (physics, low): core place's orientation argument kept all the liquid in an open mug set down
        upside down (the open_spill law is deferred for the domains' orientation verbs; core place's orientation is
        nobody else's)."""
        self.assertFalse(lawbook.active("open_spill"))
        for orientation, left in (("upright", 0.3), ("upside_down", 0.0), ("on_side", 0.0)):
            env = S.env_of(S.table_scene(m=S.mug("kitchen", pos=[1, 2], liquid="water", liquid_l=0.3)))
            for command in (("crouch",), ("pick_up", "m"), ("stand",), ("approach", "table")):
                S.ok(self, env, *command)
            result = S.ok(self, env, "place", "m", "table", orientation=orientation)
            w = env.world.s
            self.assertEqual(w["entities"]["m"]["liquid_l"], left, (orientation, result["message"]))
            spills = [e for e in w["event_log"] if e["kind"] == "phys.spill"]
            self.assertEqual(bool(spills), left < 0.3, orientation)
            if spills:
                self.assertIn("spilled", result["message"])
                self.assertEqual(spills[0]["by"], "robot")

    def test_liquid_mass_uses_its_density(self):
        from house_sim.systems.core import subtree_mass
        jug = S.item("kitchen", pos=[1, 2], label="jug", material="plastic", mass_kg=0.2, size_m=[0.15, 0.1, 0.3],
                     capacity_l=3.0, liquid="honey", liquid_l=2.0)
        env = S.env_of({"jug": jug})
        self.assertAlmostEqual(subtree_mass(env.world, "jug"), 0.2 + 2.0 * 1.42)
        self.assertAlmostEqual(subtree_mass(env.world, "jug"), support.subtree_mass(env.world.s, "jug"))

    def test_place_checks_the_opening_and_refuses_broken_supports(self):
        jar = S.item("kitchen", pos=[3, 2], label="jar", material="glass", mass_kg=0.3, size_m=[0.1, 0.1, 0.15],
                     container=True, accepts_items=True, inner_size_m=[0.09, 0.09, 0.14], capacity_l=1.0,
                     opening_m=0.03)
        scene = {"jar": jar, "spoon": S.item("kitchen", pos=[1, 2], label="ball", material="rubber", mass_kg=0.05,
                                             size_m=[0.05, 0.05, 0.05], shape="sphere", volume_l=0.06),
                 "shelf": S.make("bookshelf", "kitchen", pos=[2, 0], collapsed_shelves=[2]),
                 "glass_table": S.make("side_table", "kitchen", pos=[0, 2], broken=True, shattered=True)}
        env = S.env_of(scene)
        for command in (("crouch",), ("pick_up", "spoon"), ("stand",)):
            S.ok(self, env, *command)
        S.ok(self, env, "approach", "jar")
        result = env.step("place", "spoon", "jar")
        self.assertEqual(result["message"], "It does not fit through the opening")
        S.ok(self, env, "approach", "shelf")
        self.assertEqual(env.step("place", "spoon", "shelf", shelf=2)["message"], "That shelf is broken")
        S.ok(self, env, "place", "spoon", "shelf", shelf=1)
        S.ok(self, env, "pick_up", "spoon")
        S.ok(self, env, "approach", "glass_table")
        self.assertEqual(env.step("place", "spoon", "glass_table")["message"], "It is broken and cannot hold anything")


if __name__ == "__main__":
    unittest.main()
