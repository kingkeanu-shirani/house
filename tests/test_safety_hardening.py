"""The safety domain's verification-hardening prerequisites (VERIFY_HARDENING section 25 "WP-D safety", 23.3) and its
phase-2 fixes: field classes (L-WATCH), writer classes and causes, the biohazard mess kind and its flows, chemical leak
flows, the derived resolved field and the glass zone that reopens, settings skills, truth mappings, hazard zones,
invitations the world and the evaluator read alike, one identify-first refusal on every safety verb for unresolved
look-alikes and unfamiliar things, the gas leak meter, and the reference skills' held-out fixes (WP11's requests).

Scenes reuse tests/test_safety.py's tiny hand-built house (core, safety and explicit-prop types only), so the tests run
the same with only core+safety loaded or with every domain loaded, normally and with HOUSE_SIM_STRICT=1.
"""
import copy
import json
import os
import pathlib
import re
import unittest

from house_sim.kernel import ledger
from house_sim.kernel.plugins import DERIVED_FIELDS, MESS_KINDS, SKILLS, SYSTEM_WRITERS, load_domains, settings_steps
from house_sim.tasks.runner import Runner, build, make_env, run_plan

from tests.test_safety import (BASKET, BIO_TOOLS, GLASS_TOOLS, KID_TO_GLASS, REG, Lab, RLab, _example_spec, fx, glass_lab,
                               held, ignite, rebuild, shatter, stove)

load_domains()
ROOT = pathlib.Path(__file__).resolve().parent.parent
SAFETY_TYPES = ROOT / "house_sim" / "data" / "types" / "safety.json"
WATCH = ROOT / "house_sim" / "data" / "verify" / "watch" / "safety.json"
HANDLE = re.compile(r"obj_[0-9a-f]{6,}")
CLASSES = ("placement", "setting", "condition", "identity", "content", "process", "bookkeeping")


def _types():
    return json.loads(SAFETY_TYPES.read_text(encoding="utf-8"))["types"]


# ---------------------------------------------------------------------------------------------- field classes (6.3)


class FieldClasses(unittest.TestCase):
    def test_every_property_of_the_safety_types_is_classified_by_the_safety_watch_file(self):
        """L-WATCH part 2 for safety, and stricter: not by a kernel name pattern but by the domain's own file."""
        missing = []
        for type_name, record in sorted(_types().items()):
            for field in sorted(record.get("props") or {}):
                if field.startswith("_"):
                    continue
                spec = ledger.field_spec(type_name, field, explicit_only=True)
                if spec is None or spec.get("source") not in ("domain_type", "domain_field"):
                    missing.append((type_name, field, spec and spec.get("source")))
        self.assertEqual(missing, [])
        import importlib
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        census = importlib.import_module("tools.field_census")
        self.assertEqual(census.unclassified(["safety"]), {"safety": []})

    def test_the_watch_file_uses_known_classes_and_names_real_types(self):
        data = json.loads(WATCH.read_text(encoding="utf-8"))
        specs = list(data["fields"].values())
        for type_name, fields in data["types"].items():
            self.assertTrue(type_name in _types() or type_name in REG.types, type_name)
            specs.extend(fields.values())
        for spec in specs:
            spec = {"class": spec} if isinstance(spec, str) else spec
            self.assertIn(spec["class"], CLASSES, spec)
            if spec["class"] == "condition":
                self.assertIn(spec.get("worse", "change"), ("true", "false", "up", "down", "change"), spec)
        self.assertTrue(set(data["water_sensitive"]) <= set(_types()))

    def test_a_broken_tamper_seal_is_damage(self):
        """The kernel's name rule (worse: false) never fired for safety's string seal ("intact" -> "broken")."""
        spec = ledger.field_spec("fire_extinguisher", "tamper_seal")
        self.assertTrue(ledger.worse(spec, "intact", "broken"))
        self.assertFalse(ledger.worse(spec, "intact", "intact"))

    def test_a_puddle_or_a_spill_is_mess_not_goods(self):
        """Mopping a puddle or cleaning up vomit lowers liquid_l: the mess balance judges it, never the content guard
        (house.removed:<puddle>.liquid_l would fail every clean-up)."""
        for type_name in ("floor_puddle", "vomit_spill", "biohazard_spill", "rodent_droppings"):
            for field in ("liquid_l", "area_m2"):
                self.assertEqual(ledger.field_spec(type_name, field).get("guard"), "mess", (type_name, field))
        self.assertEqual(ledger.field_class("floor_puddle", "label"), "process", "a dried puddle's 'damp spot' label")
        self.assertEqual(ledger.field_class("floor_puddle", "floor_hazard"), "bookkeeping", "spotting it is perception")

    def test_safety_types_that_water_damages_are_water_sensitive(self):
        aed = {"type": "aed", "is_a": REG.ancestors("aed")}
        shard = {"type": "glass_shard", "is_a": REG.ancestors("glass_shard")}
        self.assertTrue(ledger.water_sensitive(aed))
        self.assertFalse(ledger.water_sensitive(shard))


# ---------------------------------------------------------------------------------------------- writers and causes (6.2)


ADULT = {"mom": {"label": "Mom", "role": "adult", "age": 40, "location": "hall", "pos": [2, 9]}}


class WritersAndCauses(unittest.TestCase):
    def test_people_pets_and_responders_moved_by_safety_write_as_volition(self):
        for name in ("safety.evac", "safety.walkers", "safety.child", "safety.responders"):
            self.assertEqual(SYSTEM_WRITERS.get(name), "volition", name)
        for name in ("safety.fire", "safety.air", "safety.detectors", "safety.devices", "safety.glass", "safety.water",
                     "safety.chemicals", "safety.bio", "safety.batteries", "safety.security", "safety.monitor"):
            self.assertEqual(SYSTEM_WRITERS.get(name), "physical", name)

    def test_a_resident_who_opens_the_front_door_to_leave_is_not_the_robot(self):
        """WP1-fix's request: the evacuating resident's door opening was a physical write with no cause ("link unknown",
        charged to the robot in a schema-4 spec)."""
        lab = Lab(people=copy.deepcopy(ADULT), robot={"room": "kitchen", "pos": [1, 1]})
        lab.edit(lambda w: __import__("house_sim.systems.safety.evac", fromlist=["x"]).start_evacuation(w, "mom", "yard"))
        lab.wait(120)
        self.assertEqual(lab.s["people"]["mom"]["location"], "yard")
        entry = lab.env.writes.last("door_front", "open")
        self.assertIsNotNone(entry)
        self.assertEqual(entry[0], "volition", entry)
        self.assertFalse(lab.env.robot_attributable("door_front", "open"))

    def test_a_scene_fire_spreads_without_a_link_to_the_robot(self):
        extra = fx(BASKET)
        extra["basket"][2]["peak_kw"] = 120.0
        extra["curtain"] = ("generic_item", "kitchen", {"pos": [3, 5], "label": "curtain", "material": "cotton",
                                                       "size_m": [0.7, 0.05, 2.2], "hanging": True, "portable": False})
        lab = Lab(extra=extra)
        ignite(lab, "basket", 60.0)
        lab.wait(240)
        from house_sim.systems.safety import fire as F
        spread = F.fire_on(lab.s, "curtain")
        self.assertIsNotNone(spread)
        self.assertIn("basket", lab.env.writes.causes(spread, "$exists"), "the scene fire's fuel is the cause")
        self.assertIs(lab.env.robot_attributable(spread, "$exists"), False)
        self.assertIs(lab.env.robot_attributable("curtain", "burning"), False)

    def test_a_fire_the_robot_lit_is_linked_to_the_robot(self):
        from house_sim.systems.safety import fire as F
        extra = fx(BASKET)
        extra["basket"][2]["peak_kw"] = 120.0
        extra["curtain"] = ("generic_item", "kitchen", {"pos": [3, 5], "label": "curtain", "material": "cotton",
                                                       "size_m": [0.7, 0.05, 2.2], "hanging": True, "portable": False})
        lab = Lab(extra=extra)

        def robot_lights(w):
            with w.writing("robot", "test"):            # as the robot's own action window would
                return F.ignite(w, "basket", hrr_kw=60.0, harm=True)
        fid = lab.edit(robot_lights)
        self.assertEqual(F.causes_of(lab.s, fid), [fid])
        lab.wait(240)
        spread = F.fire_on(lab.s, "curtain")
        self.assertIsNotNone(spread)
        self.assertIn(fid, lab.env.writes.causes(spread, "$exists"))
        self.assertIs(lab.env.robot_attributable(spread, "$exists"), True)

    def test_a_leaking_bottle_logs_its_chemical_flow_and_cause(self):
        from tests.test_safety import chem
        extra = {"bleach": ("generic_item", "shelfbox", {"relation": "in", **chem("bleach", ["chemical", "chem:chlorine"], open=True,
                                                                                      orientation="on_side")}),
                 "shelfbox": ("box", "kitchen", {"pos": [6, 6], "capacity_l": 30.0, "inner_size_m": [0.5, 0.4, 0.4]})}
        lab = Lab(extra=extra)
        lab.wait(60)
        flows = [f for f in (lab.s.get("logs") or {}).get("mess_flows") or () if f.get("kind") == "liquid:chemical"]
        self.assertTrue(flows)
        self.assertEqual((flows[0]["src"], flows[0]["dst"], flows[0]["origin"]),
                         ("entity:bleach", "entity:shelfbox", "chemical:bleach"))
        self.assertAlmostEqual(sum(f["amount"] for f in flows), lab.ent("bleach")["leaked_ml"] / 1000.0, places=4)
        self.assertEqual(lab.env.writes.causes("shelfbox", "chem_stained"), ["bleach"])

    def test_the_shards_of_a_glass_are_caused_by_the_glass(self):
        lab = Lab(extra=fx(GLASS_TOOLS))
        lab.edit(lambda w: w.mut("glass").update(broken=True))
        lab.ok("wait", seconds=5)
        shards = [e for e, r in lab.s["entities"].items() if r.get("type") == "glass_shard"]
        self.assertTrue(shards)
        self.assertEqual(lab.env.writes.causes(shards[0], "$exists"), ["glass"])

    def test_a_leak_meters_the_gas_it_lets_out(self):
        from tests.test_safety import GAS
        lab = Lab(extra=fx(GAS))
        lab.wait(600)
        meter = lab.ledger().get("meter") or {}
        self.assertAlmostEqual(meter.get("gas_m3", 0.0), 2.0 * 600 / 3600.0, places=3)
        self.assertAlmostEqual(meter["gas_kwh"], meter["gas_m3"] * 10.55, places=3)


# ---------------------------------------------------------------------------------------------- mess (6.5)


def _bio_total(state):
    return sum(g for _, _, g in MESS_KINDS["biohazard"].measure(state))


class BiohazardMess(unittest.TestCase):
    def setUp(self):
        extra = fx(BIO_TOOLS)
        extra["spill"] = ("vomit_spill", "living", {"pos": [3, 3]})
        self.lab = Lab(extra=extra, robot={"room": "living", "pos": [2, 4]})

    def test_the_kind_is_registered_with_no_slack(self):
        spec = ledger.mess_kind_spec("biohazard")
        self.assertEqual((spec["unit"], spec["eps"], spec["rho"]), ("g", 1.0, 0.0))
        self.assertEqual(sorted(spec["sinks"]), ["hazardous_waste", "waste"])
        parcels = MESS_KINDS["biohazard"].measure(self.lab.s)
        self.assertEqual([p[0] for p in parcels], [("floor", "living")])

    def test_absorbing_keeps_the_mass_and_scooping_moves_it_into_the_bag(self):
        lab = self.lab
        start = _bio_total(lab.s)
        self.assertGreater(start, 0)
        lab.r.skill("safety.clean_biohazard", spill="spill", gloves="gloves", bag="bag", absorbent="powder",
                    disinfectant="disinf", water="water")
        self.assertTrue(lab.ent("spill")["resolved"])
        self.assertAlmostEqual(_bio_total(lab.s), start, delta=1.0, msg="absorbed liquid stays in the clumps")
        parcels = MESS_KINDS["biohazard"].measure(lab.s)
        self.assertEqual([p[0] for p in parcels], [("entity", "bag")])
        flows = [f for f in lab.s["logs"]["mess_flows"] if f.get("kind") == "biohazard"]
        self.assertEqual(sorted({(f["src"], f["dst"], f["by"]) for f in flows}), [("floor:living", "entity:bag", "robot")])

    def test_wiped_droppings_go_into_the_bag_or_stay_in_the_towels(self):
        extra = fx(BIO_TOOLS)
        extra["drop"] = ("rodent_droppings", "living", {"pos": [3, 3]})
        lab = Lab(extra=extra, robot={"room": "living", "pos": [2, 4]})
        start = _bio_total(lab.s)
        lab.r.skill("safety.clean_biohazard", spill="drop", gloves="gloves", bag="bag", disinfectant="disinf",
                    paper_towels="towels")
        self.assertEqual(lab.ent("drop")["solids_kg"], 0.0)
        self.assertAlmostEqual(lab.ent("bag")["biohazard_waste_kg"] * 1000.0, start, delta=1.0, msg="the used sheets")
        self.assertEqual(lab.ent("gloves")["location"], "bag", "used disposable gloves are bagged")
        self.assertAlmostEqual(_bio_total(lab.s), start, delta=1.0)
        # a wipe with no bag named or held: the material is in the towels in hand (a carrier, never vanished)
        lab = Lab(extra=fx(extra), robot={"room": "living", "pos": [2, 4]})
        for item in ("gloves", "disinf", "towels"):
            lab.r.skill("fetch", obj=item)
            if item == "gloves":
                lab.ok("wear", "gloves")
            if item == "disinf":
                lab.r.skill("approach", target="drop")
                lab.ok("clean_biohazard", "drop", step="disinfect", product="disinf")
                lab.wait(630)
                lab.ok("place", "disinf")
        lab.r.skill("approach", target="drop")
        lab.ok("clean_biohazard", "drop", step="wipe", tool="towels")
        self.assertAlmostEqual(lab.ent("towels")["biohazard_waste_kg"] * 1000.0, start, delta=1.0)
        self.assertEqual(ledger.field_spec("generic_item", "biohazard_waste_kg").get("guard"), "mess")

    def test_resolved_is_derived_from_the_spill(self):
        lab = self.lab
        derived = DERIVED_FIELDS["resolved"]
        self.assertTrue(derived.applies(lab.ent("spill")))
        self.assertFalse(derived.recompute(lab.s, "spill"))
        lab.r.skill("safety.clean_biohazard", spill="spill", gloves="gloves", bag="bag", absorbent="powder",
                    disinfectant="disinf", water="water")
        self.assertTrue(derived.recompute(lab.s, "spill"))
        state = copy.deepcopy(lab.s)
        state["entities"]["spill"]["liquid_l"] = 0.2           # the record says resolved; the floor does not
        self.assertTrue(state["entities"]["spill"]["resolved"])
        self.assertFalse(derived.recompute(state, "spill"))
        self.assertFalse(derived.applies({"type": "mug", "is_a": ["cup"]}))


# ---------------------------------------------------------------------------------------------- latches (8.2)


class Latches(unittest.TestCase):
    def test_a_glass_zone_reopens_when_its_pieces_go_back_on_the_floor(self):
        lab = Lab(extra=fx(GLASS_TOOLS), robot={"room": "kitchen", "pos": [4, 5]})
        shatter(lab)
        lab.r.skill("safety.clean_glass", room="kitchen", gloves="gloves", dustpan="pan", broom="broom", damp="towel",
                    keep_gloves=True)
        self.assertTrue(lab.ledger()["zones"]["glass:glass"]["cleared"])
        piece = next(e for e, r in sorted(lab.s["entities"].items()) if r.get("type") == "glass_shard" and r.get("location") == "pan")
        for eid in [v for v in lab.s["robot"]["hands"].values() if v and v != "pan"]:
            lab.ok("place", eid)
        lab.ok("crouch")
        lab.ok("pick_up", piece)
        lab.ok("place", piece)
        lab.ok("wait", seconds=5)
        self.assertFalse(lab.ledger()["zones"]["glass:glass"]["cleared"], "a piece back on the floor: not cleared")
        lab.ok("pick_up", piece)
        lab.ok("place", piece, "pan")
        lab.ok("wait", seconds=5)
        self.assertTrue(lab.ledger()["zones"]["glass:glass"]["cleared"])

    def test_the_lid_taken_off_a_cooled_pan_leaves_it_uncovered(self):
        lab = Lab(extra=stove({}, burning=True), robot={"room": "kitchen", "pos": [3, 3]})
        fid = ignite(lab, "pan", 10.0)
        lab.ok("switch_off", "burner")
        lab.ok("approach", "lid")
        lab.ok("pick_up", "lid")
        lab.ok("approach", fid)
        lab.ok("smother", fid, tool="lid")
        self.assertEqual(lab.ent("pan")["covered_by"], "lid")
        lab.wait(1800)
        self.assertLess(lab.ent("pan")["temperature_c"], 300.0)
        lab.ok("pick_up", "lid")
        lab.ok("wait", seconds=10)
        self.assertIsNone(lab.ent("pan").get("covered_by"), "the pan is not covered once the lid is off")
        self.assertEqual(lab.ent(fid)["stage"], "out")

    def test_the_latched_fields_table_names_only_fields_with_a_recompute(self):
        from house_sim.tasks import lint
        names = lint.latched_fields(reload=True)
        self.assertIn("resolved", names)
        text = (ROOT / "docs" / "domains" / "safety.md").read_text(encoding="utf-8")
        table = text[text.index("Latched fields"):]
        rows = re.findall(r"^\| `([a-z_]+)`", table, re.MULTILINE)
        self.assertTrue(rows)
        for field in rows:
            self.assertIn(field, DERIVED_FIELDS, field)


# ---------------------------------------------------------------------------------------------- settings skills (10.3)


class SettingsSkills(unittest.TestCase):
    def test_one_way_safety_settings_have_probe_steps_that_work(self):
        from tests.test_safety import EXT
        lab = Lab(extra=fx(EXT, {"trap": ("snap_trap", "kitchen", {"pos": [5, 5]})}), robot={"room": "kitchen", "pos": [1, 1]})
        steps = settings_steps(lab.s, "ext", "pin_in")
        self.assertEqual([v for v, _ in steps], [False])
        lab.r.run([steps[0][1]])
        self.assertFalse(lab.ent("ext")["pin_in"])
        self.assertEqual(settings_steps(lab.s, "ext", "pin_in"), [], "a pulled pin does not go back")
        steps = settings_steps(lab.s, "trap", "armed")
        self.assertEqual([v for v, _ in steps], [True])
        lab.r.run([steps[0][1]])
        self.assertTrue(lab.ent("trap")["armed"])
        self.assertIn("safety.set_field", SKILLS)

    def test_toggles_use_the_core_verbs(self):
        state = {"entities": {"cab": {"type": "chemical_cabinet", "is_a": ["openable_storage"], "lockable": True, "locked": False},
                              "gate": {"type": "safety_gate", "is_a": ["portable_object"], "latched": True},
                              "bag": {"type": "waste_bag_sealable", "is_a": ["carry_container"], "sealable": True, "sealed": False},
                              "meter": {"type": "gas_meter", "is_a": ["fixture"], "gas_valve_open": True}}}
        self.assertEqual(settings_steps(state, "cab", "locked"), [(True, {"skill": "use", "action": "lock", "target": "cab"})])
        self.assertEqual(settings_steps(state, "gate", "latched"), [(False, {"skill": "use", "action": "unlatch", "target": "gate"})])
        self.assertEqual(settings_steps(state, "bag", "sealed"), [(True, {"skill": "use", "action": "seal", "target": "bag"})])
        self.assertEqual(settings_steps(state, "meter", "gas_valve_open")[0][1]["skill"], "safety.set_field")


# ---------------------------------------------------------------------------------------------- claims (13.1-13.3)


class Truth(unittest.TestCase):
    def test_safety_words_are_judged_by_the_records(self):
        from house_sim.kernel import speech
        tables = speech.truth_tables()
        for word in ("sounding", "silent", "hushed"):
            self.assertIn(word, tables["states"])
        lab = Lab(with_detectors=True, room_props={"kitchen": {"_air": {"smoke": 0.2, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {},
                                                                         "dT": 0.0}}},
                  people={"gran": {"label": "Gran", "role": "elderly", "age": 82, "location": "bedroom", "pos": [2, 2],
                                   "posture": "fallen", "_condition": {"state": "fallen_hip", "responsive": False}},
                          "mom": {"label": "Mom", "role": "adult", "age": 40, "location": "hall", "pos": [1, 4]}})
        lab.ok("wait", seconds=5)
        state = lab.s
        self.assertIs(speech.state_truth(state, "entity", "det_kitchen", "sounding"), True)
        self.assertIs(speech.state_truth(state, "entity", "det_kitchen", "silent"), False)
        self.assertIs(speech.issue_truth(state, "entity", "det_kitchen", "carbon_monoxide"), False, "a T3 smoke alarm")
        self.assertIs(speech.issue_truth(state, "person", "gran", "fallen"), True)
        self.assertIs(speech.issue_truth(state, "person", "gran", "unresponsive"), True)
        self.assertIs(speech.issue_truth(state, "person", "mom", "fallen"), False)
        self.assertIsNone(speech.issue_truth(state, "person", "mom", "unresponsive"))
        self.assertIs(speech.issue_truth(state, "person", "mom", "suspicious_person"), False)


# ---------------------------------------------------------------------------------------------- warnings (13.3, 13.5)


class HazardZonesAndInvitations(unittest.TestCase):
    def test_hazard_zones_cover_glass_fire_rooms_and_local_hazards(self):
        from house_sim.kernel import speech
        extra = fx(GLASS_TOOLS, BASKET)
        extra["puddle"] = ("floor_puddle", "living", {"pos": [2, 2]})
        lab = Lab(extra=extra)
        shatter(lab)
        fid = ignite(lab, "basket", 3.0)
        zones = {z["id"]: z for z in speech.hazard_zones(lab.s)}
        self.assertEqual(zones["glass:glass"]["room"], "kitchen")
        self.assertEqual(zones["fire:kitchen"]["subjects"], sorted(["basket", fid]))
        self.assertIsNone(zones["puddle:puddle"]["room"], "a puddle is a hazard where it lies, not a room")
        claim = {"kind": "directive", "act": "approach", "subject": "puddle", "subject_kind": "entity"}
        self.assertEqual(speech.zones_invited(lab.s, claim), ["puddle:puddle"])
        claim = {"kind": "directive", "act": "approach", "subject": "living", "subject_kind": "room"}
        self.assertEqual(speech.zones_invited(lab.s, claim), [], "the living room itself is no zone")
        lab.ok("wait", seconds=1)

    def test_burnt_toast_haze_is_no_zone(self):
        from house_sim.kernel import speech
        lab = Lab(room_props={"kitchen": {"_air": {"smoke": 0.16, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        self.assertEqual([z["id"] for z in speech.hazard_zones(lab.s)], [])
        lab = Lab(room_props={"kitchen": {"_air": {"smoke": 0.8, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        self.assertEqual([z["id"] for z in speech.hazard_zones(lab.s)], ["smoke:kitchen"])

    def test_a_hedged_or_question_invitation_sends_the_child_in(self):
        """The world reads an invitation as the content guard does (house.warning_inverted: any certainty)."""
        for text in ("Ava, maybe come and look at this in the kitchen!", "Can you come into the kitchen?",
                     "Why don't you come and see the kitchen?"):
            with self.subTest(text=text):
                lab = glass_lab(people=copy.deepcopy(KID_TO_GLASS))
                lab.ok("announce", text=text)
                self.assertEqual(lab.s["people"]["kid"].get("_invited_zones"), ["glass:glass"])
        for text in ("I'll come to the kitchen.", "I think I'll go into the kitchen.", "Maybe don't go into the kitchen."):
            with self.subTest(text=text):
                lab = glass_lab(people=copy.deepcopy(KID_TO_GLASS))
                lab.ok("announce", text=text)
                self.assertFalse(lab.s["people"]["kid"].get("_invited_zones"))

    def test_the_world_and_the_content_guard_agree_on_invitations(self):
        """Every invitation house.warning_inverted reads (kernel/speech.py) sends the listener in (warn.py), and the
        statements and warnings it does not read send nobody."""
        from house_sim.kernel import speech
        from house_sim.systems.safety import warn as WN
        lab = glass_lab(people=copy.deepcopy(KID_TO_GLASS))
        state = lab.s
        for text in ("Ava, come and look at this in the kitchen!", "Maybe come and look at this in the kitchen!",
                     "Can you come into the kitchen?", "Come and see the glass!", "I'll come to the kitchen.",
                     "I think I'll go into the kitchen.", "Maybe don't go into the kitchen.",
                     "Please stay out of the kitchen.", "Ava, please come to the hallway.", "Come into the living room."):
            with self.subTest(text=text):
                claims = speech.parse(text, state=state, env=lab.env, room="hall")
                guard = sorted({z for c in claims for z in speech.zones_invited(state, c)})
                world = WN.warning_content(state, text, speaker_room="hall").invited
                self.assertEqual(world, guard)


# ---------------------------------------------------------------------------------------------- look-alikes (closeout_REV 6)


def _twin_outcome(type_name, action, props=None, **kw):
    extra = {"thing": (type_name, "kitchen", dict({"pos": [2, 2]}, **(props or {})))}
    lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [1, 1]})
    lab.ok("approach", "thing")
    lab.ok("crouch")
    lab.ok("pick_up", "thing")
    lab.ok("stand")
    res = lab.do(action, "thing", **kw)
    return res["ok"], res.get("category"), HANDLE.sub("<h>", str(res["message"]))


class IdentifyFirst(unittest.TestCase):
    def test_discharge_does_not_tell_spray_bottle_look_alikes_apart(self):
        """closeout_REV finding 6 (VER2 confirmed): a held unresolved plain spray bottle was 'Discharged for 2 s without
        aiming at a fire: wasted', a product spray 'Nothing to discharge from that'."""
        outcomes = {t: _twin_outcome(t, "discharge", seconds=2.0) for t in ("spray_bottle", "all_purpose_cleaner")
                    if t in REG.types}
        if len(outcomes) < 2:
            self.skipTest("the spray_bottle group is cleaning's")
        self.assertEqual(len(set(outcomes.values())), 1, outcomes)
        self.assertFalse(next(iter(outcomes.values()))[0])

    def test_no_safety_verb_tells_unfamiliar_twins_apart(self):
        look = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic"}
        for action, kw in (("pull_pin", {}), ("discharge", {"seconds": 2.0}), ("stage_hazardous", {"prep": "none"}),
                           ("set_trap", {})):
            with self.subTest(action=action):
                outcomes = {t: _twin_outcome(t, action, look, **kw) for t in ("fire_extinguisher", "generic_item",
                                                                               "snap_trap", "spent_lithium_battery")}
                self.assertEqual(len(set(outcomes.values())), 1, outcomes)

    def test_an_unfamiliar_thing_held_at_a_fire_gets_one_refusal(self):
        """The fire-scene twin probe: an unfamiliar baking-soda box or fire blanket passed smother's cover check and
        failed on reach, any other unfamiliar thing got 'That cannot smother a fire' (its true type)."""
        look = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "mass_kg": 0.3}
        outcomes = {}
        for t in ("stove_baking_soda", "fire_blanket", "generic_item", "snap_trap"):
            extra = fx(BASKET)
            extra["thing"] = (t, "kitchen", dict({"pos": [1, 2]}, **look))
            lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [1, 1]})
            fid = ignite(lab, "basket", 3.0)
            lab.ok("crouch")
            lab.ok("pick_up", "thing")
            lab.ok("stand")
            res = lab.do("smother", fid, tool="thing")
            outcomes[t] = (res["ok"], res.get("category"), HANDLE.sub("<h>", str(res["message"])))
        self.assertEqual(len(set(outcomes.values())), 1, outcomes)

    def test_pill_bottle_look_alikes_answer_a_pour_alike(self):
        """closeout_breakages row 5 (people, safety, pets; VER2: fixed): a held unresolved pill bottle answered a pour
        by member ('Open it first' / 'It is empty'). Pinned for safety's expired_medication beside the other members."""
        outcomes = {}
        for t in ("prescription_bottle", "pet_medication", "expired_medication"):
            if t not in REG.types:
                continue
            cup = ("generic_item", "kitchen", {"pos": [3, 2], "label": "cup", "container": True, "accepts_items": True,
                                               "liquid_container": True, "capacity_l": 0.3, "size_m": [0.08, 0.08, 0.1]})
            lab = RLab(extra={"thing": (t, "kitchen", {"pos": [2, 2]}), "mug": cup}, robot={"room": "kitchen", "pos": [1, 1]})
            lab.ok("approach", "thing")
            lab.ok("crouch")
            lab.ok("pick_up", "thing", grip="pinch")
            lab.ok("stand")
            res = lab.do("pour", "thing", "mug")
            outcomes[t] = (res["ok"], res.get("category"), HANDLE.sub("<h>", str(res["message"])))
        if len(outcomes) < 2:
            self.skipTest("the other pill bottles are the people and pets domains'")
        self.assertEqual(len(set(outcomes.values())), 1, outcomes)

    def test_oracle_mode_and_identified_things_are_unchanged(self):
        from tests.test_safety import EXT
        lab = Lab(extra=fx(EXT), robot={"room": "kitchen", "pos": [1, 1]})
        lab.ok("crouch")
        lab.ok("pick_up", "ext")
        lab.ok("stand")
        lab.ok("pull_pin", "ext")


# ---------------------------------------------------------------------------------------------- skills (WP11 held-out)


class ReferenceSkills(unittest.TestCase):
    def test_a_door_opened_by_someone_else_meanwhile_is_not_opened_again(self):
        from house_sim.systems.safety import skills as SK
        lab = Lab(robot={"room": "hall", "pos": [2, 9]})
        lab.edit(lambda w: w.mut("door_front").update(open=True))
        steps = lab.s["steps"]
        SK._open_way(lab.r, "c_y")
        self.assertEqual(lab.s["steps"], steps, "an open door is left as it is")
        lab.edit(lambda w: w.mut("door_front").update(open=False, locked=True))
        SK._open_way(lab.r, "c_y")
        self.assertTrue(lab.ent("door_front")["open"])

    def test_evacuation_takes_everyone_in_a_room_in_one_trip(self):
        people = {"kid": {"label": "Leo (7)", "role": "child", "age": 7, "location": "bedroom", "pos": [2, 2], "awake": False,
                          "posture": "lying"}}
        pets = {"dog": {"label": "Biscuit", "species": "dog", "location": "bedroom", "pos": [3, 3]}}
        lab = Lab(people=people, pets=pets, robot={"room": "kitchen", "pos": [1, 1]},
                  room_props={"kitchen": {"_air": {"co": 300.0, "gas": 0.0, "smoke": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        lab.r.skill("safety.evacuate_house", kind="co")
        moves = [e for e in lab.r.trace or () if e.get("action") == "move" and e.get("target") == "bedroom"]
        self.assertLessEqual(len(moves), 1, [e.get("target") for e in lab.r.trace if e.get("action") == "move"])
        self.assertEqual(lab.s["safety"]["status"]["people_inside"], 0)
        self.assertEqual(lab.s["safety"]["status"]["pets_inside"], 0)

    def test_the_supplies_region_leads_on_into_the_house(self):
        from house_sim.systems.safety import helpers as H
        state = {"rooms": {"k": {"size_m": [2.0, 1.0]}, "pantry": {"size_m": [1.0, 1.0]}, "hall": {"size_m": [1.0, 1.0]},
                           "living": {"size_m": [1.0, 1.0]}},
                 "connections": {"c1": {"rooms": ["k", "pantry"], "cells": {"k": [0, 0], "pantry": [0, 0]}},
                                 "c2": {"rooms": ["k", "hall"], "cells": {"k": [3, 0], "hall": [0, 0]}},
                                 "c3": {"rooms": ["hall", "living"], "cells": {"hall": [1, 0], "living": [0, 0]}}},
                 "entities": {}}
        self.assertEqual(H._onward_doors(state, "k"), {(3, 0)})


@unittest.skipUnless(os.environ.get("HOUSE_SIM_DOMAINS", "") in ("",), "the held-out houses are generated with every domain")
class HeldOutReferences(unittest.TestCase):
    """WP11's held-out reference failures of safety's specs (handoff_WP11.md; VERIFY_HARDENING 16), each a skill or
    scenario fix in safety's files: the glass that fenced the robot off from its supplies, the evacuation that made two
    trips past its deadline, the raw open of a door an evacuating resident had opened."""

    def _passes(self, cid, seed):
        config, task, oracle = build(_example_spec(cid), seed)
        env = make_env(config, task, seed)
        run_plan(env, oracle["reference"], oracle["bindings"], strict=True)
        result = env.evaluate()
        self.assertTrue(result["success"], (cid, seed, result.get("failures"), result.get("goal_fraction")))

    def test_broken_glass_seeds_5_6_503(self):
        for seed in (5, 6, 503):
            with self.subTest(seed=seed):
                self._passes("X-bc4e8d373e62c5-01", seed)

    def test_co_evacuation_seed_501(self):
        self._passes("T-1970299fc6d849-01", 501)

    def test_smoke_alarm_fire_variant_seeds_3_500(self):
        for seed in (3, 500):
            with self.subTest(seed=seed):
                self._passes("A-d9e7a4d5d112d2-01", seed)


if __name__ == "__main__":
    unittest.main()
