"""Food domain, phase 2 (VERIFY_HARDENING WP-D food): what the house ledger needs from this domain and the phase-1
carry-over.

* field classes for every property of the domain's types, the food record's sub-keys included (L-WATCH part 2);
* refuse marked waste: true -- prep scraps, eggshells, removed sleeves, used disposable gloves -- from the baseline or
  from their creation (with a mess flow from "created"); the ledger's removal exemption and mess balance on them;
* writer classes and causes of the food systems (drips cite the dripping pack, grit the food it soaked out of);
* settings skills (opened packages, bruised soft produce), the water meter of a refreshed cold-water bath;
* structured instrument readings (the kitchen scale, the probe thermometer, a cold space's dial);
* truth mappings (data/speech/truth_food.json) and the latched-field table L-LATCH reads;
* the Placer's order for grocery placement (WP9) and the kernel's unfamiliar-thing helpers (closeout_breakages row
  "navigation_robot, food, manipulation").

Scenes reuse tests/test_food.py's scene builder, so the file runs with every domain loaded and with
HOUSE_SIM_DOMAINS=core,food (cases that need another domain's types or verbs skip).
"""
import copy
import json
import os
import re
import sys
import unittest
from unittest import mock

from house_sim.kernel import ledger, plugins, speech
from house_sim.kernel.plugins import MESS_KINDS, SYSTEM_WRITERS, load_domains, settings_steps

load_domains()

from house_sim.systems.food import common as C  # noqa: E402
from house_sim.systems.food import hardening as H  # noqa: E402
from house_sim.systems.food import percept as P  # noqa: E402
from tests.test_food import ent, food, knife_ready, msg_json, scene  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANDLE = re.compile(r"obj_[0-9a-f]{6,16}")
MESS_GUARDS = ("house.mess_relocated", "house.wrong_sink", "house.mess_in_transit", "house.mess_unaccounted")


def hardened(envelope=None, protections=None):
    return {"success": [], "failure": [], "hardening": {"envelope": envelope or {}, "protections": protections or {}}}


def house_labels(env, task=None):
    found = ledger.failures(task or hardened(), env.world.s, env._baseline, world=env.world, idle=None)
    return [f for f in found if f.startswith("house.")]


def created_flows(env, kind="waste_item"):
    flows = (env.world.s.get("logs") or {}).get("mess_flows") or []
    return [f for f in flows if f.get("op") == "flow" and f.get("kind") == kind and
            str(f.get("src", "")).startswith("created:")]


def domain_loaded(name):
    return name in plugins.enabled_domains()


class FieldClassTests(unittest.TestCase):
    """6.3, L-WATCH part 2: every property of house_sim/data/types/food.json is classified, food.* included."""

    def test_every_type_property_is_classified(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import field_census
        rows = field_census.unclassified(["food"])["food"]
        self.assertEqual(rows, [])

    def test_the_food_record_sub_keys_take_their_classes(self):
        self.assertEqual(ledger.field_class("onion", "food.qty_g"), "content")
        self.assertEqual(ledger.field_class("onion", "food.composition"), "content")
        self.assertEqual(ledger.field_class("onion", "food.cut"), "identity")
        self.assertEqual(ledger.field_class("onion", "food.kind"), "identity")
        self.assertEqual(ledger.field_class("frozen_peas_bag", "food.ice"), "process")
        self.assertEqual(ledger.field_class("bread_slice", "food.toast"), "process")
        bruise = ledger.field_spec("peach", "food.bruise")
        self.assertEqual((bruise["class"], bruise["worse"]), ("condition", "up"))
        self.assertEqual(ledger.field_spec("food_item", "food.shell_fragments")["class"], "condition")
        # an unlisted sub-key falls to the watched catch-all, not to a hole
        self.assertEqual(ledger.field_class("onion", "food.something_new"), "identity")

    def test_amounts_and_shapes_of_bulk_food(self):
        # a portion taken out of a package lowers the package's volume_l with its qty_g: an amount, not an identity
        self.assertEqual(ledger.field_class("all_purpose_flour", "volume_l"), "content")
        # bulk food takes its mound's or vessel's shape from its amount: the shape follows, nothing to judge
        self.assertEqual(ledger.field_class("soup", "size_m"), "bookkeeping")
        self.assertEqual(ledger.field_class("all_purpose_flour", "full_g"), "identity")
        self.assertEqual(ledger.field_class("all_purpose_flour", "tare_kg"), "identity")
        # a vessel's fill follows the food in it, a grocery bag's load what it holds (container-derived content)
        self.assertEqual(ledger.field_class("mixing_bowl", "fill_level"), "bookkeeping")
        self.assertEqual(ledger.field_class("grocery_bag", "load_kg"), "bookkeeping")

    def test_contamination_tags_hazards_and_food_law_marks(self):
        for tag in ("raw_meat", "bacteria", "chemical", "allergen_peanut", "allergen_egg"):
            spec = ledger.field_spec("cutting_board_wood", "contamination." + tag)
            self.assertEqual((spec["class"], spec["worse"]), ("condition", "up"), tag)
        for tag in ("kosher_meat", "kosher_dairy", "flavor_cumin", "capsaicin"):
            self.assertEqual(ledger.field_class("chef_knife", "contamination." + tag), "process", tag)

    def test_settings_conditions_and_water_sensitivity(self):
        self.assertEqual(ledger.field_class("kitchen_scale", "tare_g"), "setting")
        self.assertEqual(ledger.field_class("kitchen_scale", "settings.unit"), "setting")
        self.assertEqual(ledger.field_class("cereal_box", "liner_closed"), "setting")
        self.assertEqual(ledger.field_spec("chef_knife", "sharpness")["worse"], "down")
        self.assertEqual(ledger.field_class("prep_scraps", "waste"), "identity")
        self.assertTrue(ledger.water_sensitive({"type": "recipe_card"}))
        self.assertFalse(ledger.water_sensitive({"type": "mixing_bowl"}))


class RefuseTests(unittest.TestCase):
    """6.4-6.5: food refuse carries the kernel's waste flag, from the baseline or from its creation."""

    def test_refuse_in_the_house_is_flagged_before_the_baseline(self):
        env, b, r = scene([
            {"op": "spawn", "var": "scraps", "type": "prep_scraps", "on": "{board}",
             "props": {"food": {"kind": "scraps", "cat": "scraps", "phase": "pieces", "qty_g": 40.0}}},
            {"op": "spawn", "var": "sleeve", "type": "food_outer_packaging", "on": "{counter}"},
            {"op": "spawn", "var": "used", "type": "food_glove_pair", "on": "{counter}", "props": {"used": True}},
            {"op": "spawn", "var": "fresh", "type": "food_glove_pair", "on": "{counter}"},
            {"op": "spawn", "var": "apple", "type": "apple", "on": "{counter}"}])
        for name in ("scraps", "sleeve", "used"):
            self.assertIs(ent(env, b[name]).get("waste"), True, name)
            self.assertIs(env._baseline["entities"][b[name]].get("waste"), True, name)
        for name in ("fresh", "apple"):
            self.assertNotIn("waste", ent(env, b[name]), name)

    def test_peeling_makes_a_scraps_pile_that_is_refuse_from_its_creation(self):
        env, b, r = scene([
            {"op": "spawn", "var": "potato", "type": "potato", "on": "{board}"},
            {"op": "spawn", "var": "potato2", "type": "potato", "on": "{board}"},
            {"op": "spawn", "var": "peeler", "type": "vegetable_peeler", "on": "{counter}"}])
        r.skill("fetch", obj=b["peeler"], grip="handle")
        r.do("peel", target=b["potato"])
        piles = [e for e, rec in env.world.s["entities"].items() if rec.get("type") == "prep_scraps"]
        self.assertEqual(len(piles), 1)
        self.assertIs(ent(env, piles[0]).get("waste"), True)
        flows = created_flows(env)
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["dst"], "entity:" + b["board"])
        self.assertEqual(flows[0]["by"], "robot")
        # a second potato's peel goes on the same pile: no second parcel, no second flow
        r.do("peel", target=b["potato2"])
        self.assertEqual(len([e for e, rec in env.world.s["entities"].items() if rec.get("type") == "prep_scraps"]), 1)
        self.assertEqual(len(created_flows(env)), 1)

    def test_eggshell_halves_and_a_worn_pair_of_gloves_are_refuse(self):
        env, b, r = scene([
            {"op": "spawn", "var": "egg", "type": "hen_egg", "on": "{counter}"},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "gloves", "type": "food_glove_pair", "on": "{counter}"}])
        r.skill("fetch", obj=b["egg"])
        out = msg_json(r.do("crack_egg", target=b["egg"], destination=b["bowl"], surface="flat")["message"])
        shell = out["shell_in_hand"]
        self.assertIs(ent(env, shell).get("waste"), True)
        self.assertTrue(any(f["dst"] == "entity:robot" for f in created_flows(env)))
        r.skill("put", obj=shell, dest=b["counter"])
        r.skill("fetch", obj=b["gloves"])
        r.do("wear", target=b["gloves"])
        self.assertNotIn("waste", ent(env, b["gloves"]))
        r.do("take_off", target=b["gloves"])
        self.assertIs(ent(env, b["gloves"]).get("waste"), True)

    def test_the_stand_in_compost_caddy_is_a_waste_container(self):
        env, b, r = scene([], kitchen_args={"need": ["compost", "trash"]})
        for name in ("compost", "trash"):
            self.assertIn("waste", ledger.chain_classes(env.world.s, b[name], ent(env, b[name])) |
                          ledger.classes_of(ent(env, b[name])), name)

    def test_refuse_that_leaves_the_house_is_no_removal(self):
        env, b, r = scene([
            {"op": "spawn", "var": "scraps", "type": "prep_scraps", "on": "{board}",
             "props": {"food": {"kind": "scraps", "cat": "scraps", "phase": "pieces", "qty_g": 40.0}}}],
            kitchen_args={"need": ["compost"]})
        r.skill("fetch", obj=b["scraps"])
        r.skill("put", obj=b["scraps"], dest=b["compost"])
        labels = house_labels(env)
        self.assertFalse([f for f in labels if f.startswith(("house.lost", "house.removed", "house.hidden"))], labels)

    @unittest.skipUnless("waste_item" in MESS_KINDS, "the waste_item mess kind is cleaning's")
    def test_scraps_left_on_the_board_are_mess_and_composted_ones_are_not(self):
        def peeled(compost):
            env, b, r = scene([
                {"op": "spawn", "var": "potato", "type": "potato", "on": "{board}"},
                {"op": "spawn", "var": "peeler", "type": "vegetable_peeler", "on": "{counter}"}],
                kitchen_args={"need": ["compost"]})
            r.skill("fetch", obj=b["peeler"], grip="handle")
            r.do("peel", target=b["potato"])
            if compost:
                pile = next(e for e, rec in env.world.s["entities"].items() if rec.get("type") == "prep_scraps")
                r.skill("put", obj=b["peeler"], dest=b["counter"])
                r.skill("fetch", obj=pile)
                r.skill("put", obj=pile, dest=b["compost"])
            return [f for f in house_labels(env) if f.split(":", 1)[0] in MESS_GUARDS]
        # the board is a food-contact surface: refuse left on it is in a wrong sink (6.5)
        self.assertIn("house.wrong_sink:waste_item", peeled(False))
        self.assertEqual(peeled(True), [])


class WriterTests(unittest.TestCase):
    """6.2: the food systems are physical writers and report causes where they know them."""

    def test_the_food_systems_are_tagged_physical(self):
        for name in ("food.thermal", "food.safety", "food.refresh"):
            self.assertEqual(SYSTEM_WRITERS[name], "physical")

    def test_a_drip_cites_the_raw_meat_it_came_from(self):
        env, b, r = scene([
            {"op": "spawn", "var": "chicken", "type": "frozen_chicken_pack", "in": "{fridge}", "shelf": "{fridge_top}"},
            {"op": "spawn", "var": "lettuce", "type": "lettuce", "in": "{fridge}", "shelf": "{fridge_bottom}",
             "props": {"temperature_c": 4.0}}],
            kitchen_args={"need": ["fridge"]})
        r.do("wait", seconds=3600 * 6)
        self.assertGreater(ent(env, b["lettuce"]).get("contamination", {}).get("raw_meat", 0.0), 0.05)
        entry = env.writes.entry(b["lettuce"], "contamination")
        self.assertEqual(entry[0], "physical")
        self.assertIn(b["chicken"], entry[4])

    def test_grit_in_the_bowl_cites_what_soaked_in_it(self):
        env, b, r = scene([
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}",
             "props": {"liquid": "water", "liquid_l": 2.0}},
            {"op": "spawn", "var": "leek", "type": "carrot", "in": "{bowl}", "props": {"soil": {"dirt": 0.5}}}])
        r.do("wait", seconds=900)
        if not ent(env, b["bowl"]).get("grit"):
            self.skipTest("no grit settled in this scene")
        entry = env.writes.entry(b["bowl"], "grit")
        self.assertEqual(entry[0], "physical")
        self.assertIn(b["leek"], entry[4])


class SettingsSkillTests(unittest.TestCase):
    """10.3: how a battery policy changes a food setting or damages a food (B14's changed / damaged probes)."""

    def test_a_package_is_opened_with_the_opener_it_needs(self):
        env, b, r = scene([
            {"op": "spawn", "var": "milk", "type": "milk_carton", "on": "{counter}"},
            {"op": "spawn", "var": "beans", "type": "canned_beans", "on": "{counter}"},
            {"op": "spawn", "var": "opener", "type": "can_opener", "on": "{counter2}"}])
        state = env.world.s
        self.assertEqual(settings_steps(state, b["milk"], "opened"),
                         [(True, {"skill": "use", "action": "open", "target": b["milk"]})])
        steps = settings_steps(state, b["beans"], "opened")
        self.assertEqual(ent(env, steps[0][1]["tool"])["tool_kind"], "can_opener")
        r.skill("use", action="open", target=b["milk"])
        self.assertTrue(ent(env, b["milk"])["opened"])
        self.assertEqual(settings_steps(env.world.s, b["milk"], "opened"), [])       # one way

    def test_a_can_without_an_opener_in_the_house_has_no_step(self):
        state = {"entities": {"can": {"type": "canned_beans", "is_a": ["packaged_food"], "package": "can",
                                      "opener_required": "can_opener"}}}
        self.assertEqual(H.package_open_steps(state, "can"), [])

    def test_soft_produce_bruises_under_a_firm_grip(self):
        env, b, r = scene([{"op": "spawn", "var": "berries", "type": "raspberries", "on": "{counter}"},
                           {"op": "spawn", "var": "apple", "type": "apple", "on": "{counter}"}])
        steps = settings_steps(env.world.s, b["berries"], "food.bruise")
        self.assertEqual(len(steps), 1)
        value, step = steps[0]
        self.assertEqual(settings_steps(env.world.s, b["apple"], "food.bruise"), [])
        r.skill(step.pop("skill"), **step)
        self.assertAlmostEqual(food(env, b["berries"])["bruise"], value)


class MeterTests(unittest.TestCase):
    """6.8: a refreshed cold-water thaw bath draws its bowl of tap water on the house water meter."""

    def test_refreshing_the_bath_counts_its_litres(self):
        env, b, r = scene([
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}",
             "props": {"liquid": "water", "liquid_l": 2.0}},
            {"op": "spawn", "var": "pack", "type": "frozen_chicken_pack", "on": "{counter}"}])
        r.skill("fetch", obj=b["pack"])
        r.do("thaw", target=b["pack"], method="cold_water", destination=b["bowl"])
        path = ledger.data("resources")["meters"]["water"]["path"]
        before = ledger.meter_value(env.world.s, path)      # setting up the bath drew nothing: the bowl was filled
        r.do("thaw", target=b["pack"], method="cold_water", destination=b["bowl"], refresh_water=True)
        self.assertAlmostEqual(ledger.meter_value(env.world.s, path) - before, 2.0, delta=0.05)


class ReadingTests(unittest.TestCase):
    """15.2: measuring verbs return structured readings tasks/perceive.py indexes."""

    def _scale_scene(self, perception=None):
        return scene([
            {"op": "spawn", "var": "scale", "type": "kitchen_scale", "on": "{counter}",
             "props": {"on": True, "settings": {"unit": "g"}}},
            {"op": "spawn", "var": "apple", "type": "apple", "on": "{counter}"},
            {"op": "spawn", "var": "bowl", "type": "mixing_bowl", "on": "{counter}"},
            {"op": "spawn", "var": "flour", "type": "all_purpose_flour", "on": "{counter}", "props": {"open": True}}],
            perception=perception)

    def test_the_scale_reading_names_the_one_thing_it_weighs(self):
        env, b, r = self._scale_scene()
        r.skill("put", obj=b["apple"], dest=b["scale"])
        out = msg_json(r.do("weigh", target=b["scale"])["message"])
        self.assertEqual(out["reading"], {"subject": b["apple"], "quantity": "mass", "unit": "g", "field": "mass_kg",
                                          "value": float(out["display"].split()[0])})
        self.assertAlmostEqual(out["reading"]["value"] / 1000.0, ent(env, b["apple"])["mass_kg"], delta=0.001)

    def test_a_tared_bowl_reads_what_is_in_it_and_a_crowded_scale_reads_its_load(self):
        env, b, r = self._scale_scene()
        r.skill("put", obj=b["bowl"], dest=b["scale"])
        r.do("weigh", target=b["scale"], tare=True)
        r.skill("fetch", obj=b["flour"])
        r.do("transfer", target=b["flour"], destination=b["bowl"], until_reading=200, rate="slow")
        out = msg_json(r.do("weigh", target=b["scale"])["message"])
        inside = [e for e, rec in env.world.s["entities"].items() if rec.get("location") == b["bowl"]]
        self.assertEqual(out["reading"]["subject"], inside[0])
        # two things on the scale: the reading is of its load
        state = {"entities": {"s": {"tare_g": 0.0}, "a": {"location": "s", "relation": "on", "mass_kg": 0.2},
                              "c": {"location": "s", "relation": "on", "mass_kg": 0.3}}}
        found = H.scale_reading(state, "s", "500 g")
        self.assertEqual((found["subject"], found["value"], found["unit"]), ("s", 500.0, "g"))
        self.assertNotIn("field", found)
        # a thing in a tared box the robot cannot see is never named
        state = {"entities": {"s": {"tare_g": 300.0}, "box": {"location": "s", "relation": "on", "mass_kg": 0.3},
                              "x": {"location": "box", "relation": "in", "mass_kg": 0.1}}}
        self.assertEqual(H.scale_reading(state, "s", "100 g", known=lambda eid: False)["subject"], "s")
        self.assertEqual(H.scale_reading(state, "s", "100 g")["subject"], "x")

    def test_readings_reach_the_evidence_index_with_handles(self):
        from house_sim.tasks import perceive
        env, b, r = self._scale_scene(perception="realistic")
        perceive.enable(env)
        r.skill("put", obj=b["apple"], dest=b["scale"])
        message = env.step("weigh", env.realism.handles.handle(b["scale"]))["message"]
        self.assertNotIn(b["apple"], message)                       # the agent sees the apple's handle, never its id
        idx = perceive.index(env)
        found = [x for x in idx.readings if x["subject"] == b["apple"]]
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["value_si"], ent(env, b["apple"])["mass_kg"], delta=0.001)

    def test_the_probe_thermometer_reads_the_core(self):
        env, b, r = scene([
            {"op": "spawn", "var": "chicken", "type": "chicken_breast", "on": "{board}",
             "props": {"temperature_c": 70.0, "_core_c": 58.0}},
            {"op": "spawn", "var": "probe", "type": "generic_item", "on": "{counter}",
             "props": {"label": "instant-read thermometer", "tool_kind": "thermometer",
                       "capabilities": ["probe_temperature"], "size_m": [0.15, 0.02, 0.02]}}])
        r.skill("fetch", obj=b["probe"])
        details = msg_json(r.do("inspect", target=b["chicken"], where="closely")["message"])
        readings = details["readings"]
        self.assertEqual(len(readings), 1)
        self.assertEqual((readings[0]["subject"], readings[0]["quantity"], readings[0]["unit"]),
                         (b["chicken"], "temperature", "C"))
        self.assertEqual(readings[0]["value"], details["probe_temp_c"])

    def test_no_reading_without_a_number(self):
        self.assertIsNone(H.scale_reading({"entities": {}}, "s", ""))
        self.assertIsNone(H.scale_reading({"entities": {}}, "s", "O-Ld"))


class TruthTests(unittest.TestCase):
    """13.1-13.3: claims about food state are scored by data/speech/truth_food.json; anything else stays unknown."""

    def test_the_file_loads_with_the_kernel_tables(self):
        tables = speech.truth_tables()
        for word in ("frozen", "thawed", "cooked", "raw", "ripe", "unripe", "overripe"):
            self.assertIn(word, tables["states"])
        for word in ("expired", "bruised", "bulging", "freezer_burn"):
            self.assertIn(word, tables["issues"])
        self.assertEqual(tables["acts"]["cook"]["state"], "cooked")

    def test_food_states_read_the_food_record(self):
        state = {"entities": {
            "peas": {"food": {"ice": 1.0}}, "steak": {"food": {"ice": 0.3}},
            "chicken": {"food": {"raw": True}}, "roast": {"food": {"cooked": True, "raw": False}},
            "banana": {"food": {"ripeness": 0.97}}, "lamp": {"temperature_c": 30.0}}}

        def st(sid, word):
            return speech.state_truth(state, "entity", sid, word)
        self.assertIs(st("peas", "frozen"), True)
        self.assertIs(st("steak", "frozen"), False)
        self.assertIs(st("steak", "thawed"), False)             # partly thawed is neither
        self.assertIs(st("chicken", "cooked"), False)           # "the chicken is cooked" about raw chicken is false
        self.assertIs(st("roast", "cooked"), True)
        self.assertIs(st("roast", "raw"), False)
        self.assertIsNone(st("chicken", "raw"))                 # raw is scored only on a cooked flag: never wrong
        self.assertIs(st("banana", "ripe"), True)
        self.assertIs(st("banana", "overripe"), True)
        self.assertIs(st("banana", "unripe"), False)
        self.assertIsNone(st("lamp", "frozen"))                 # not food: unknown

    def test_food_issues_read_the_food_record(self):
        state = {"entities": {
            "yogurt": {"food": {}, "_food_status": {"class": "quality_expired", "days_left": -2}},
            "milk": {"food": {}, "_food_status": {"class": "ok", "days_left": 5}},
            "peach": {"food": {"bruise": 0.6}}, "can": {"food": {}, "_bulging": True}}}

        def it(sid, word):
            return speech.issue_truth(state, "entity", sid, word)
        self.assertIs(it("yogurt", "expired"), True)
        self.assertIs(it("milk", "expired"), False)
        self.assertIs(it("peach", "bruised"), True)
        self.assertIs(it("can", "bulging"), True)
        self.assertIsNone(it("milk", "bulging"))


class LatchTests(unittest.TestCase):
    """8.2: food's hook-written goal fields that are never recomputed are in the docs' latched-field table."""

    def test_report_checks_and_served_to_are_latched(self):
        from house_sim.tasks import lint
        latched = lint.latched_fields(reload=True)
        self.assertIn("_report_check", latched)
        self.assertIn("served_to", latched)
        for name in ("_report_check", "served_to"):
            self.assertNotIn(name, plugins.DERIVED_FIELDS)


class ScenarioTests(unittest.TestCase):
    """WP9: grocery items are placed in the Placer's order (seeded for schema 4, as given for older specs)."""

    def test_groceries_follow_the_placer_order(self):
        from house_sim.generator import place
        items = ["milk_carton", "canned_beans", "apple"]
        setup = [{"op": "helper", "name": "food.groceries", "args": {"on": "{counter}", "items": items, "bags": 1}}]
        env, b, r = scene(setup)
        given = [ent(env, e)["type"] for e in b["all_groceries"]]
        self.assertEqual(given, items)
        with mock.patch.object(place.Placer, "order", lambda self, seq: list(reversed(list(seq)))):
            env, b, r = scene(setup)
        self.assertEqual([ent(env, e)["type"] for e in b["all_groceries"]], list(reversed(items)))


def canon(message):
    """A message with the realistic handles replaced (handles are salted by the house, which twins differ in)."""
    return HANDLE.sub("<h>", message if isinstance(message, str) else json.dumps(message, sort_keys=True))


class LookalikeTests(unittest.TestCase):
    """The phase-1 look-alike rows, food's verbs (the WP-D food sweep: every food verb x every entity parameter x
    every registry look-alike group, on the counter and held): crush judged an improvised pounder by its true mass, so
    a held unresolved empty plain spray bottle (0.1 kg) was "too light to crush with" while a full cleaner crushed, and
    a pre-filter panel was refused where a HEPA panel crushed (and, judged by the felt weight, a crib sheet where a flat
    sheet crushed). An unresolved pounder now gets the kernel's identify-first refusal; an identified one keeps the
    0.2 kg rule."""

    def _crush_with(self, type_name):
        env, b, r = scene([
            {"op": "spawn", "var": "thing", "type": type_name, "on": "{counter}"},
            {"op": "spawn", "var": "garlic", "type": "garlic_clove", "on": "{board}"}], perception="realistic")
        h = env.realism.handles.handle
        self.assertTrue(env.step("pick_up", h(b["thing"]))["ok"])
        out = []
        for kw in ({"tool": h(b["thing"])}, {"tool": h(b["thing"]), "force": "firm"}):
            snap = copy.deepcopy(env)
            res = snap.step("crush", h(b["garlic"]), **kw)
            out.append((res["ok"], res["category"], canon(res["message"])))
        return out

    def _group(self, members):
        known = set(plugins_registry().types)
        members = [m for m in members if m in known]
        if len(members) < 2:
            self.skipTest("the group's types are another domain's (not loaded)")
        answers = {m: self._crush_with(m) for m in members}
        reference = answers[members[0]]
        for name, got in answers.items():
            self.assertEqual(got, reference, name)

    def test_spray_bottle_members_crush_alike(self):
        self._group(["all_purpose_cleaner", "spray_bottle", "trigger_bottle", "glass_cleaner"])

    def test_air_filter_panels_crush_alike(self):
        self._group(["purifier_hepa_filter", "purifier_prefilter", "purifier_carbon_filter"])

    def test_sheets_and_stones_crush_alike(self):
        self._group(["flat_sheet", "fitted_sheet", "crib_sheet"])
        self._group(["decor_stone", "faux_stone"])

    def test_an_unresolved_pounder_is_identified_first_and_a_known_one_crushes(self):
        from house_sim.kernel.actions import UNIDENTIFIED_MESSAGE
        known = set(plugins_registry().types)
        if "spray_bottle" in known:
            ok, category, message = self._crush_with("spray_bottle")[0]
            self.assertEqual((ok, category, message), (False, "precondition", UNIDENTIFIED_MESSAGE))
        ok, category, message = self._crush_with("canned_beans")[0]
        self.assertTrue(ok, message)


def plugins_registry():
    from house_sim.kernel.registry import default_registry
    return default_registry()


class UnfamiliarHelperTests(unittest.TestCase):
    """closeout_breakages row "navigation_robot, food, manipulation": food's unfamiliar-tool rule is the kernel's
    (Ctx.unfamiliar / Ctx.refuse_unfamiliar), not a domain copy."""

    def test_held_known_and_the_tool_picker_use_the_kernel_rule(self):
        from house_sim.kernel.actions import Ctx
        env, b, r = scene([
            {"op": "spawn", "var": "knife", "type": "chef_knife", "on": "{counter}"},
            {"op": "spawn", "var": "onion", "type": "onion", "on": "{board}"}])
        knife_ready(r, b)
        knife = b["knife"]
        seen = []

        def unfamiliar(self, eid):
            seen.append(eid)
            return eid == knife
        with mock.patch.object(Ctx, "unfamiliar", unfamiliar):
            out = env.step("cut", b["onion"], style="halve")
        self.assertFalse(out["ok"])                         # the knife counts as unfamiliar: no known tool in hand
        self.assertEqual(out["category"], "capability")
        self.assertIn(knife, seen)

    def test_the_identify_first_hook_uses_the_kernel_refusal(self):
        calls = []

        class FakeCtx(object):
            env = mock.Mock(realism=object())
            spec = mock.Mock(params={"target": {"type": "entity"}, "destination": {"type": "entity"}},
                             operates=True)
            args = {"target": "a", "destination": "b"}

            def refuse_unfamiliar(self, *eids):
                calls.append(eids)
        with mock.patch.object(P, "realism", lambda env: object()):
            P.unfamiliar_first(FakeCtx())
        self.assertEqual(sorted(e for c in calls for e in c), ["a", "b"])

    def test_held_known_reads_ctx(self):
        ctx = mock.Mock()
        ctx.w.held_items.return_value = ["x", "y"]
        ctx.unfamiliar.side_effect = lambda eid: eid == "x"
        self.assertEqual(C.held_known(ctx), ["y"])


if __name__ == "__main__":
    unittest.main()
