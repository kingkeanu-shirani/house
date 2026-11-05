"""The admin_inventory domain's verification-hardening prerequisites (VERIFY_HARDENING section 25 "WP-D
admin_inventory", 23.3) and its phase-2 fixes: field classes (L-WATCH), refuse flags and mess flows with origins,
hazardous creations, writer classes and causes, derived fields for what this domain's hooks keep, settings skills and
damaging verbs, truth mappings, the structured luggage-scale reading, the collection truck's record of what it took,
the placer order of look-alike mail and trays (WP9), and the phase-1 carry-over (the kernel's unfamiliar-thing
helpers).

Scenes reuse tests/test_admin_inventory.py's generated houses plus setup ops, so the tests run the same with only
core+admin_inventory loaded or with every domain loaded, normally and with HOUSE_SIM_STRICT=1. Where a test needs the
waste_item mess kind (the cleaning domain registers it) and cleaning is not loaded, a stand-in measure with the same
rule (every entity flagged waste: true is one item) is registered for the test and removed after it.
"""
import json
import pathlib
import re
import unittest

from house_sim.kernel import ledger
from house_sim.kernel.plugins import (DERIVED_FIELDS, MESS_KINDS, SKILLS, SYSTEM_WRITERS, load_domains, mess_kind,
                                      settings_steps)
from house_sim.kernel.registry import default_registry

from tests.test_admin_inventory import (DESK_DOC, OFFICE, SHELF, build, build_real, ent, msg, ok)

load_domains()
ROOT = pathlib.Path(__file__).resolve().parent.parent
TYPES = ROOT / "house_sim" / "data" / "types" / "admin_inventory.json"
WATCH = ROOT / "house_sim" / "data" / "verify" / "watch" / "admin_inventory.json"
CLASSES = ("placement", "setting", "condition", "identity", "content", "process", "bookkeeping")
REG = default_registry()


def _types():
    return json.loads(TYPES.read_text(encoding="utf-8"))["types"]


BIN = {"label": "wastebasket", "category": "waste_bin", "waste_bin": True, "accepts_items": True, "container": True,
       "waste_stream": "trash", "accepts_any_waste": True, "capacity_l": 20, "size_m": [0.3, 0.3, 0.4], "portable": False}


def hardened(envelope=None, protections=None):
    return {"success": [], "failure": [], "hardening": {"envelope": envelope or {}, "protections": protections or {}}}


def house_labels(env, task=None):
    return ledger.failures(task or hardened(), env.world.s, env._baseline, world=env.world, idle=None)


def flows(env):
    return [f for f in (env.world.s.get("logs") or {}).get("mess_flows") or () if f.get("kind") == "waste_item"]


class WasteKind(object):
    """The waste_item kind for a test: cleaning's when it is loaded, else a stand-in with its rule."""

    def __init__(self, test):
        self.test = test

    def __enter__(self):
        self.added = "waste_item" not in MESS_KINDS
        if self.added:
            def measure(state):
                from house_sim.systems.core import mess_location
                out = []
                for eid, rec in sorted((state.get("entities") or {}).items()):
                    if isinstance(rec, dict) and rec.get("waste") is True:
                        loc = mess_location(state, rec.get("location"), rec.get("relation"))
                        if loc is not None:
                            out.append((tuple(loc), None, 1.0))
                return out
            mess_kind("waste_item", unit="count", measure=measure)
        return self

    def __exit__(self, *exc):
        if self.added:
            MESS_KINDS.pop("waste_item", None)
        return False


# ---------------------------------------------------------------------------------------------- field classes (6.3)


class FieldClasses(unittest.TestCase):
    def test_every_property_of_the_admin_types_is_classified_by_the_admin_watch_file(self):
        """L-WATCH part 2 for admin_inventory, and stricter: by the domain's own file, not a kernel name pattern."""
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
        self.assertEqual(census.unclassified(["admin_inventory"]), {"admin_inventory": []})

    def test_the_watch_file_uses_known_classes_and_names_real_types(self):
        data = json.loads(WATCH.read_text(encoding="utf-8"))
        specs = list(data["fields"].values())
        for type_name, fields in data["types"].items():
            self.assertTrue(type_name in _types() or type_name in ledger.type_domains(), type_name)
            specs.extend(fields.values())
        for spec in specs:
            spec = {"class": spec} if isinstance(spec, str) else spec
            self.assertIn(spec["class"], CLASSES, spec)
            if spec["class"] == "condition":
                self.assertIn(spec.get("worse", "change"), ("true", "false", "up", "down", "change"), spec)
        self.assertTrue(set(data["water_sensitive"]) <= set(_types()))

    def test_goods_ratings_and_mirrors_have_the_right_class(self):
        """Kernel name rules that misfit these types: a counted pack's units and a paper stack's sheets are what they
        hold (content, not counters); a document's text is printed (identity, not bookkeeping); a printer's power and a
        dispenser's fill line are ratings; a bag's load and a tray's loaded sheets mirror what is in them."""
        self.assertEqual(ledger.field_class("napkin_pack", "units_count"), "content")
        self.assertEqual(ledger.field_class("paper_stack", "sheets_count"), "content")
        self.assertEqual(ledger.field_spec("paper_chad", "sheets_count").get("guard"), "mess")
        self.assertEqual(ledger.field_spec("hole_punch", "chad_sheets").get("guard"), "mess")
        self.assertEqual(ledger.field_class("document", "text"), "identity")
        self.assertEqual(ledger.field_class("laser_printer", "power_w"), "identity")
        self.assertEqual(ledger.field_class("soap_dispenser", "fill_line_l"), "identity")
        for type_name, field in (("suitcase", "load_kg"), ("suitcase", "fill_l"), ("printer_paper_tray", "sheets_loaded"),
                                 ("napkin_holder", "napkins"), ("toilet_paper_holder", "slots"), ("envelope", "waste")):
            self.assertEqual(ledger.field_class(type_name, field), "bookkeeping", (type_name, field))
        self.assertEqual(ledger.field_class("napkin_stack", "napkins"), "content")
        self.assertTrue(ledger.worse(ledger.field_spec("document", "in_order"), True, False))
        self.assertTrue(ledger.worse(ledger.field_spec("document", "punch_holes"), None, "us_3"))

    def test_paper_goods_and_electronics_are_water_sensitive(self):
        def rec(t):
            return {"type": t, "is_a": REG.ancestors(t)}
        for t in ("document", "envelope", "package_box", "file_folder", "multifunction_printer", "postal_scale"):
            self.assertTrue(ledger.water_sensitive(rec(t)), t)
        self.assertFalse(ledger.water_sensitive(rec("paper_clip")))


# ---------------------------------------------------------------------------------------------- refuse and flows (6.4, 6.5)


class RefuseAndFlows(unittest.TestCase):
    def test_a_pulled_staple_is_created_refuse_the_robot_must_bin(self):
        with WasteKind(self):
            env, b, r = build(DESK_DOC + [{"op": "set", "target": "{doc}", "props": {"fastener": "staple"}},
                                          {"op": "spawn", "var": "remover", "type": "staple_remover", "on": "{desk}"},
                                          {"op": "spawn", "var": "bin", "type": "generic_item", "floor": "{office_room}",
                                           "props": BIN}])
            ok(self, env, "pick_up", b["remover"])
            staple = msg(ok(self, env, "unstaple", b["doc"]))["staple"]
            rec = ent(env, staple)
            self.assertIs(rec["waste"], True)
            self.assertEqual(rec["tags"], ["choking_hazard", "sharp_offcut"])
            self.assertEqual([(f["src"], f["dst"], f["by"]) for f in flows(env)],
                             [("created:unstaple", "entity:" + b["desk"], "robot")])
            labels = house_labels(env, hardened({"change": {"{doc}": ["fastener"]}}))
            self.assertIn("house.mess_relocated:waste_item", labels)
            self.assertIn("house.created_hazard:" + staple, labels)
            ok(self, env, "place", b["remover"], b["desk"])
            r.skill("put", obj=staple, dest=b["bin"])
            labels = house_labels(env, hardened({"change": {"{doc}": ["fastener"]}, "move": {"{remover}": {"to": ["baseline"]}}}))
            self.assertNotIn("house.mess_relocated:waste_item", labels)
            self.assertNotIn("house.created_hazard:" + staple, labels)

    def test_chad_in_the_tray_is_its_load_and_spilled_chad_is_litter(self):
        with WasteKind(self):
            env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "punch", "type": "hole_punch", "on": "{desk}",
                                           "props": {"chad_sheets": 398}}])
            ok(self, env, "pick_up", b["punch"])
            ok(self, env, "punch", b["doc"])
            spilled = [e for e, rec in env.world.s["entities"].items() if rec.get("type") == "paper_chad" and
                       rec.get("location") != b["punch"]]
            self.assertEqual(len(spilled), 1)
            self.assertIs(ent(env, spilled[0])["waste"], True)
            self.assertEqual(flows(env)[-1]["src"], "created:punch")
            env2, b2, _ = build(DESK_DOC + [{"op": "spawn", "var": "punch", "type": "hole_punch", "on": "{desk}"}])
            ok(self, env2, "pick_up", b2["punch"])
            ok(self, env2, "punch", b2["doc"])
            tray = [e for e, rec in env2.world.s["entities"].items() if rec.get("type") == "paper_chad"]
            self.assertTrue(tray)
            self.assertNotIn("waste", ent(env2, tray[0]))

    def test_fill_taken_out_and_peeled_packaging_are_refuse(self):
        with WasteKind(self):
            ops = OFFICE + [{"op": "helper", "name": "admin_inventory.package",
                             "args": {"location": "{office_room}", "to": "resident_1",
                                      "contents": [{"type": "ink_cartridge", "eid": "cart_new"}],
                                      "fill": [{"type": "packing_peanuts", "eid": "peanuts"}]}},
                            {"op": "spawn", "var": "cutter", "type": "box_cutter", "on": "{desk}",
                             "props": {"settings": {"blade": "extended", "depth_mm": 10}}},
                            {"op": "robot", "near": "{pkg}"}]
            env, b, r = build(ops)
            self.assertNotIn("waste", ent(env, "peanuts"))
            r.skill("fetch", obj=b["cutter"])
            r.skill("approach", target=b["pkg"])
            ok(self, env, "open_package", b["pkg"])
            ok(self, env, "remove_packing", b["pkg"], destination=env.world.robot["room"])
            self.assertIs(ent(env, "peanuts")["waste"], True)
            self.assertEqual(flows(env)[-1]["src"], "created:remove_packing")
            self.assertIn("house.mess_relocated:waste_item",
                          house_labels(env, hardened({"move": {"type:packing_peanuts": {"to": ["rooms:work"]}}})))
            ok(self, env, "place", b["cutter"], b["desk"])
            r.skill("fetch", obj="cart_new")
            out = msg(ok(self, env, "remove_packing", "cart_new"))
            scraps = out["waste"]
            self.assertTrue(scraps)
            for scrap in scraps:
                self.assertIs(ent(env, scrap)["waste"], True)
            clips = [s for s in scraps if ent(env, s).get("label") == "shipping clip"]
            for clip in clips:
                self.assertIn("choking_hazard", ent(env, clip)["tags"])

    def test_a_printer_jam_is_refuse_caused_by_the_printer(self):
        with WasteKind(self):
            env, b, r = build([{"op": "helper", "name": "admin_inventory.office",
                                "args": {"room": "office", "sheets": 120, "files": {"memo": {"title": "Memo", "pages": 3}}}},
                               {"op": "robot", "near": "{printer}"}])
            ok(self, env, "print", b["printer"], file="memo")
            ok(self, env, "wait", seconds=60)
            jam = ent(env, b["printer"])["slots"]["paper_path"]["item"]
            self.assertTrue(jam)
            self.assertIs(ent(env, jam)["waste"], True)
            flow = [f for f in flows(env) if f["src"] == "created:printer_jam"]
            self.assertEqual(len(flow), 1)
            self.assertEqual(flow[0]["by"], "physical")
            self.assertTrue(env.robot_attributable(jam, "$exists"), "the robot overfilled the tray and printed")

    def test_baseline_refuse_is_flagged_and_keepers_are_not(self):
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "staple", "type": "loose_staple", "on": "{desk}"},
                                      {"op": "spawn", "var": "jam", "type": "jammed_paper", "on": "{desk}"},
                                      {"op": "spawn", "var": "core", "type": "toilet_paper_roll", "on": "{desk}",
                                       "props": {"remaining": 0.0, "waste_stream": "recycling"}},
                                      {"op": "spawn", "var": "kept", "type": "loose_staple", "on": "{desk}",
                                       "props": {"keep": True}}])
        for name in ("staple", "jam", "core"):
            self.assertIs(ent(env, b[name]).get("waste"), True, name)
        self.assertNotIn("waste", ent(env, b["kept"]))
        self.assertNotIn("waste", ent(env, b["doc"]))
        self.assertIs(env._baseline["entities"][b["staple"]].get("waste"), True, "flagged before the baseline")


class WindowEnvelope(unittest.TestCase):
    def _sealed_window(self, take_out):
        ops = OFFICE + [{"op": "spawn", "var": "env", "type": "window_envelope", "on": "{desk}"},
                        {"op": "spawn", "var": "pen", "type": "click_pen", "on": "{desk}"},
                        {"op": "helper", "name": "admin_inventory.document",
                         "args": {"location": "{desk}", "key": "bill", "title": "Bill", "kind": "bill", "var": "doc",
                                  "props": {"address_block": "9 Elm St, Springfield IL 62704"}}},
                        {"op": "robot", "near": "{desk}"}]
        env, b, r = build(ops)
        ok(self, env, "fold_paper", b["doc"], style="z_fold")
        r.skill("put", obj=b["doc"], dest=b["env"])
        self.assertTrue(ent(env, b["env"])["addressed"])
        if take_out:
            _move_away(env, b["doc"], b["office_room"])
        env.world.mut(b["env"]).update({"sealed": True, "open": False, "postage_cents": 200})
        env.world.commit()
        return env, b

    def test_a_letter_taken_back_out_takes_its_address_along(self):
        # known gap of the interrupted run (fails before): an empty window envelope still counted as addressed, and the
        # carrier took it to the address of the letter it once held
        from house_sim.systems.admin_inventory.mail import ready_to_send
        env, b = self._sealed_window(take_out=False)
        self.assertEqual(ready_to_send(env.world, b["env"]), (True, None))
        env, b = self._sealed_window(take_out=True)
        self.assertEqual(ready_to_send(env.world, b["env"]), (False, "no address"))

    def test_an_envelope_addressed_by_hand_keeps_its_address(self):
        from house_sim.systems.admin_inventory.mail import ready_to_send
        env, b = self._sealed_window(take_out=True)
        ok(self, env, "pick_up", b["pen"])
        ok(self, env, "address_envelope", b["env"], to="12 Oak Street, Springfield IL 62704", tool=b["pen"])
        self.assertEqual(ready_to_send(env.world, b["env"]), (True, None))


class InvariantLabels(unittest.TestCase):
    """P2-S2-merge request 7 / WP8-fix: a schema-4 spec names a harm by inv:<registered name> (L-EXPECT, L-PROTECT;
    protect_rules.json drafts inv:admin_inventory.child_access and three more). Fails before: the invariants fired
    child_access:<id>, discarded_protected:<id>, ..., which that pattern never matches, and which L-EXPECT rejects."""

    def _names(self):
        from house_sim.kernel.plugins import INVARIANTS
        return sorted(n for n in INVARIANTS if n.startswith("admin_inventory."))

    def test_every_label_starts_with_its_registered_name(self):
        import ast
        import inspect
        import textwrap
        from house_sim.kernel.plugins import INVARIANTS
        names = self._names()
        self.assertEqual(len(names), 7, names)
        for name in names:
            tree = ast.parse(textwrap.dedent(inspect.getsource(INVARIANTS[name])))
            texts = [leaf.value for node in ast.walk(tree) if isinstance(node, ast.Return) and node.value is not None
                     for leaf in ast.walk(node.value) if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str)]
            self.assertTrue(texts, name)
            for text in texts:
                self.assertTrue(text.startswith(name + ":"), (name, text))

    def test_a_harm_is_named_by_its_invariant(self):
        from house_sim.tasks import lint, verify
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "bin", "type": "generic_item", "floor": "{office_room}",
                                       "props": dict(BIN)},
                                      {"op": "set", "target": "{doc}", "props": {"_doc_important": True}}])
        r.skill("put", obj=b["doc"], dest=b["bin"])
        labels = [f for f in env.evaluate()["failures"] if f.startswith("admin_inventory.")]
        self.assertEqual(labels, ["admin_inventory.protected_items:discarded:" + b["doc"]])
        canon = verify.canonical_labels(labels)
        for pattern in ("inv:admin_inventory.protected_items", "inv:admin_inventory.protected_items:discarded"):
            self.assertTrue(verify.pattern_matches(pattern, canon), pattern)
            self.assertIsNone(lint.invariant_problem(pattern[len("inv:"):]), pattern)
        self.assertFalse(verify.pattern_matches("inv:admin_inventory.protected_items:lost", canon))


class Curbside(unittest.TestCase):
    SCHEDULE = {"weekday": "Friday", "time": "09:00", "streams": {"trash": "weekly", "recycling": "odd_weeks"}}

    def _carts(self, extra=()):
        return build([{"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage", "schedule": self.SCHEDULE}}]
                     + list(extra), require=("garage",), start="2026-10-09T07:00:00")

    def _out(self, env, b, r):
        from house_sim.systems.admin_inventory.curb import curb_room
        curb = curb_room(env.world.s)
        cell = r.skill("admin_inventory.curb_cell", cart=b["cart_trash"])
        r.skill("admin_inventory.wheel_to", cart=b["cart_trash"], room=curb, cell=cell)
        return curb

    def test_the_bags_in_a_cart_are_refuse_and_the_truck_takes_them_through_the_cart(self):
        with WasteKind(self):
            env, b, r = self._carts()
            bags = [c for c, rec in env.world.s["entities"].items() if rec.get("location") == b["cart_trash"]]
            self.assertTrue(bags)
            for bag in bags:
                self.assertIs(ent(env, bag)["waste"], True)
            self._out(env, b, r)
            ok(self, env, "wait", seconds=3 * 3600)
            taken = [f for f in flows(env) if f["dst"] == "drain:" + b["cart_trash"]]
            self.assertEqual(len(taken), 1)
            self.assertEqual((taken[0]["amount"], taken[0]["by"]), (float(len(bags)), "physical"))
            labels = house_labels(env, hardened({"move": {"{cart_trash}": {"to": ["room:{curb}".format(curb="x")]}}}))
            self.assertFalse([lab for lab in labels if lab.startswith("house.mess_unaccounted")], labels)
            gone = [g for g in env.world.s["logs"]["gone"] if g["entity"] in bags]
            self.assertTrue(gone and all(g["by"] == "physical" for g in gone))
            self.assertTrue(env.robot_attributable(bags[0], "$exists"), "the robot wheeled the cart out")

    def test_a_protected_thing_the_robot_put_out_is_not_forgiven_by_the_truck(self):
        """Regression (fails before the fix): the passport binned in the cart the robot wheeled out was
        discarded_protected until the truck emptied the cart, then the label vanished with the passport."""
        deed = {"op": "spawn", "var": "pp", "type": "document", "floor": "{cart_room}",
                "props": {"label": "house deed", "_doc_important": True, "waste_stream": "trash", "value": 1.0}}
        env, b, r = self._carts([deed])
        r.skill("open", target=b["cart_trash"])
        r.skill("put", obj=b["pp"], dest=b["cart_trash"])
        r.skill("close", target=b["cart_trash"])
        self.assertIn("admin_inventory.protected_items:discarded:" + b["pp"], env.evaluate()["failures"])
        self._out(env, b, r)
        ok(self, env, "wait", seconds=3 * 3600)
        self.assertNotIn(b["pp"], env.world.s["entities"])
        self.assertIn("admin_inventory.protected_items:discarded:" + b["pp"], env.evaluate()["failures"])
        self.assertTrue(env.world.s["household"]["collected"][b["pp"]]["robot"])

    def test_wind_litter_comes_out_of_the_open_cart_it_blew_from(self):
        with WasteKind(self):
            env, b, r = self._carts([{"op": "weather", "wind_mps": 14.0, "gust_mps": 22.0}])
            self._out(env, b, r)
            ok(self, env, "open", b["cart_trash"])
            ok(self, env, "wait", seconds=4 * 3600)
            litter = [e for e, rec in env.world.s["entities"].items() if rec.get("label") == "blown litter"]
            if not litter:
                self.skipTest("no gust lifted litter in this house's exposure")
            self.assertIs(ent(env, litter[0])["waste"], True)
            flow = [f for f in flows(env) if f["src"] == "entity:" + b["cart_trash"] and f["dst"] != "drain:" + b["cart_trash"]]
            self.assertTrue(flow)
            self.assertIn(b["cart_trash"], env.writes.causes(litter[0], "$exists"))


# ---------------------------------------------------------------------------------------------- writers and causes (6.2)


class WritersAndCauses(unittest.TestCase):
    def test_both_systems_are_physical(self):
        self.assertEqual(SYSTEM_WRITERS.get("admin_inventory.time"), "physical")
        self.assertEqual(SYSTEM_WRITERS.get("admin_inventory.exposure"), "physical")

    def test_the_mail_carrier_acts_by_their_own_decision(self):
        from tests.test_admin_inventory import MailTests
        env, b = MailTests._outgoing(MailTests("test_postal_scale_display"), sheets=3)
        ok(self, env, "wait", seconds=3600)
        gone = [g for g in env.world.s["logs"]["gone"] if g["entity"] == b["env"]]
        self.assertEqual([(g["by"], g["name"]) for g in gone], [("volition", "admin_inventory.carrier")])

    def test_residents_use_up_stock_by_their_own_decision(self):
        env, b, r = build([{"op": "room", "var": "bath", "type": "bathroom"},
                           {"op": "spawn", "var": "holder", "type": "toilet_paper_holder", "mount": "{bath}", "height": 0.7},
                           {"op": "helper", "name": "admin_inventory.install_part",
                            "args": {"device": "{holder}", "slot": "roll", "type": "toilet_paper_roll", "var": "roll"}},
                           {"op": "household", "consumption": {"enabled": True}}], require=())
        ok(self, env, "wait", seconds=6 * 3600)
        entry = env.writes.entry(b["roll"], "remaining")
        self.assertEqual((entry[0], entry[1]), ("volition", "admin_inventory.residents"))

    def test_a_thief_takes_what_the_robot_set_out_on_the_robots_account_only(self):
        ops = [{"op": "room", "var": "yard", "type": "front_yard"},
               {"op": "household", "theft_risk_per_h": 50.0, "theft_grace_s": 600},
               {"op": "helper", "name": "admin_inventory.package", "args": {"location": "{yard}", "var": "parcel",
                                                                          "props": {"value": 80.0}}},
               {"op": "robot", "room": "{yard}"}]
        env, b, r = build(ops, require=("garage",))
        ok(self, env, "wait", seconds=3600)
        self.assertNotIn(b["parcel"], env.world.s["entities"])
        self.assertIs(env.robot_attributable(b["parcel"], "$exists"), False, "the courier left it there")
        env, b, r = build(ops, require=("garage",))
        r.skill("put", obj=b["parcel"], dest=b["yard"])
        ok(self, env, "wait", seconds=3600)
        self.assertNotIn(b["parcel"], env.world.s["entities"])
        self.assertIs(env.robot_attributable(b["parcel"], "$exists"), True, "the robot set it out")

    def test_the_print_engine_cites_its_printer(self):
        env, b, r = build([{"op": "helper", "name": "admin_inventory.office",
                            "args": {"room": "office", "sheets": 40, "files": {"memo": {"title": "Memo", "pages": 2}}}},
                           {"op": "robot", "near": "{printer}"}])
        ok(self, env, "print", b["printer"], file="memo")
        ok(self, env, "wait", seconds=60)
        self.assertEqual(ent(env, b["printer"])["jobs_done"], 1)
        self.assertIn(b["printer"], env.writes.causes(b["printer"], "jobs_done"))
        self.assertIs(env.robot_attributable(b["printer"], "jobs_done"), True)


class Meter(unittest.TestCase):
    def test_a_printer_draws_its_rated_power_only_while_it_prints(self):
        """6.8 (a world fix): the climate meter booked a laser printer's 450 W around the clock (power_w while on, no
        standby); now power_state is printing only while a job runs, standby_w otherwise (fails before)."""
        env, b, r = build([{"op": "helper", "name": "admin_inventory.office",
                            "args": {"room": "office", "kind": "laser_printer", "sheets": 200,
                                     "files": {"memo": {"title": "Memo", "pages": 40}}}},
                           {"op": "robot", "near": "{printer}"}])
        rec = ent(env, b["printer"])
        self.assertEqual((rec["power_state"], rec["standby_w"]), ("standby", 8.0))
        ok(self, env, "print", b["printer"], file="memo")
        ok(self, env, "wait", seconds=30)
        self.assertEqual(ent(env, b["printer"])["power_state"], "printing")
        ok(self, env, "wait", seconds=300)
        self.assertEqual(ent(env, b["printer"])["jobs_done"], 1)
        self.assertEqual(ent(env, b["printer"])["power_state"], "standby")
        self.assertNotIn("power_state", REG.resolve("laser_printer"), "generated houses are unchanged (gen-1 digests)")
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "shredder", "type": "paper_shredder", "floor": "{office_room}"}])
        self.assertEqual((ent(env, b["shredder"])["power_state"], ent(env, b["shredder"])["standby_w"]), ("standby", 0.5))


# ---------------------------------------------------------------------------------------------- derived fields (8.2)


def _move_away(env, eid, room):
    """Another domain's verb (or a resident) moved eid without this domain's hooks: a direct journaled write."""
    m = env.world.mut(eid)
    for key in ("spos", "shelf", "held_hand", "slot"):
        m.pop(key, None)
    m["location"], m["relation"], m["pos"] = room, "on", [1, 1]


class DerivedFields(unittest.TestCase):
    def test_every_latched_goal_field_has_a_recompute_and_the_table_lists_them(self):
        from house_sim.systems.admin_inventory import hardening
        from house_sim.tasks import lint
        names = lint.latched_fields(reload=True)
        text = (ROOT / "docs" / "domains" / "admin_inventory.md").read_text(encoding="utf-8")
        table = text[text.index("Latched fields"):]
        rows = re.findall(r"^\| `([a-z_]+)`", table, re.MULTILINE)
        self.assertEqual(sorted(rows), sorted(hardening.DERIVED))
        for field in rows:
            self.assertIn(field, DERIVED_FIELDS, field)
            self.assertIn(field, names)

    def test_a_folder_forgets_a_paper_taken_out_by_another_verb(self):
        from house_sim.kernel.evaluation import evaluation_view
        from house_sim.systems.admin_inventory.hardening import derived_order
        env, b, r = build([{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office"}},
                           {"op": "spawn", "var": "folder", "type": "file_folder", "on": "{desk}"},
                           {"op": "helper", "name": "admin_inventory.document",
                            "args": {"location": "{folder}", "key": "a", "title": "A", "var": "a", "props": {"date": "2025-01-01"}}},
                           {"op": "helper", "name": "admin_inventory.document",
                            "args": {"location": "{folder}", "key": "b", "title": "B", "var": "b", "props": {"date": "2025-02-01"}}},
                           {"op": "robot", "near": "{desk}"}])
        self.assertEqual(ent(env, b["folder"])["order"], [b["a"], b["b"]])
        _move_away(env, b["a"], b["office_room"])
        self.assertEqual(ent(env, b["folder"])["order"], [b["a"], b["b"]], "the stored order is stale")
        self.assertEqual(derived_order(env.world.s, b["folder"]), [b["b"]])
        task = dict(hardened(), success=[{"path": "entities.{}.order".format(b["folder"]), "op": "eq", "value": [b["b"]]}])
        view = evaluation_view(env.world.s, task=task)
        self.assertEqual(view["entities"][b["folder"]]["order"], [b["b"]])

    def test_tray_napkin_and_bag_mirrors_follow_what_is_in_them(self):
        from house_sim.systems.admin_inventory import hardening as H
        env, b, r = build(SHELF + [{"op": "spawn", "var": "holder", "type": "napkin_holder", "in": "{shelf}", "shelf": 1},
                                   {"op": "spawn", "var": "pack", "type": "napkin_pack", "in": "{shelf}", "shelf": 1,
                                    "props": {"open": True}}], require=())
        ok(self, env, "take_unit", b["pack"], count=40)
        stack = env.world.held_items()[0]
        ok(self, env, "place", stack, b["holder"])
        self.assertTrue(ent(env, b["holder"])["dispensable"])
        _move_away(env, stack, env.world.robot["room"])
        self.assertTrue(ent(env, b["holder"])["dispensable"], "stale")
        self.assertEqual(H.derived_napkins(env.world.s, b["holder"]), 0)
        self.assertIs(H.derived_dispensable(env.world.s, b["holder"]), False)
        self.assertIs(H.derived_overstuffed(env.world.s, b["holder"]), False)
        env, b, r = build([{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 40}},
                           {"op": "robot", "near": "{printer}"}])
        stacks = [c for c, rec in env.world.s["entities"].items() if rec.get("location") == b["tray"]]
        self.assertEqual(H.derived_sheets_loaded(env.world.s, b["tray"]),
                         sum(ent(env, c)["sheets_count"] for c in stacks))
        self.assertEqual(H.derived_sizes_loaded(env.world.s, b["tray"]), ["letter"])
        stack = [c for c, rec in env.world.s["entities"].items() if rec.get("location") == b["tray"]][0]
        _move_away(env, stack, b["office_room"])
        self.assertEqual(H.derived_sheets_loaded(env.world.s, b["tray"]), 0)
        self.assertEqual(H.derived_sizes_loaded(env.world.s, b["tray"]), [])

    def test_a_tag_or_a_letter_taken_off_takes_its_verdict_with_it(self):
        from house_sim.systems.admin_inventory import hardening as H
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "bag", "type": "suitcase", "floor": "{lr}"},
               {"op": "spawn", "var": "tag", "type": "luggage_tag", "floor": "{lr}", "props": {"tag_text": "Ana, 555-0101"}},
               {"op": "robot", "near": "{bag}"}]
        env, b, r = build(ops, require=())
        r.skill("fetch", obj=b["tag"])
        ok(self, env, "tag_luggage", b["bag"], tag=b["tag"], text="Ana, 555-0101")
        self.assertIs(H.derived_tagged(env.world.s, b["bag"]), True)
        self.assertEqual(H.derived_tag_text(env.world.s, b["bag"]), "Ana, 555-0101")
        _move_away(env, b["tag"], b["lr"])
        self.assertTrue(ent(env, b["bag"])["tagged"], "stale")
        self.assertIs(H.derived_tagged(env.world.s, b["bag"]), False)
        self.assertIsNone(H.derived_tag_text(env.world.s, b["bag"]))
        self.assertEqual(H.derived_tag_text(env.world.s, b["tag"]), "Ana, 555-0101", "the tag's own text is its own")
        env, b, r = build(OFFICE + [{"op": "spawn", "var": "env", "type": "window_envelope", "on": "{desk}"},
                                    {"op": "helper", "name": "admin_inventory.document",
                                     "args": {"location": "{desk}", "key": "bill", "title": "Bill", "kind": "bill", "var": "doc"}},
                                    {"op": "robot", "near": "{desk}"}])
        ok(self, env, "fold_paper", b["doc"], style="z_fold")
        r.skill("put", obj=b["doc"], dest=b["env"])
        self.assertTrue(ent(env, b["env"])["window_address_ok"])
        _move_away(env, b["doc"], b["office_room"])
        self.assertTrue(ent(env, b["env"])["window_address_ok"], "stale")
        self.assertIs(H.derived_window_address_ok(env.world.s, b["env"]), False)

    def test_a_return_label_counts_only_while_it_is_on_the_parcel(self):
        from house_sim.systems.admin_inventory.hardening import derived_return_label_ok
        state = {"entities": {"pkg": {"type": "package_box", "return_label_ok": True, "shipping_label_doc": "lab"},
                              "lab": {"type": "return_label", "location": "pkg", "relation": "attached"}}}
        self.assertIs(derived_return_label_ok(state, "pkg"), True)
        state["entities"]["lab"].update(location="hall", relation="on")
        self.assertIs(derived_return_label_ok(state, "pkg"), False)


# ---------------------------------------------------------------------------------------------- settings skills (10.3)


class SettingsSkills(unittest.TestCase):
    def test_the_domain_setting_fields_have_probe_steps(self):
        from house_sim.systems.admin_inventory import hardening as H
        state = {"entities": {
            "pr": {"type": "multifunction_printer", "cover_open": False},
            "env": {"type": "envelope", "is_a": ["portable_object"], "sealed": False, "open": True},
            "pkg": {"type": "package_box", "package": True, "tape_sealed": True},
            "doc": {"type": "document", "category": "document", "sheets": [["k", 1], ["k", 2]], "fastener": None},
            "cert": {"type": "certificate", "category": "document", "sheets": [["c", 1]], "no_fold": True},
            "stapled": {"type": "document", "category": "document", "sheets": [["s", 1]], "fastener": "staple"},
            "punch": {"type": "hole_punch", "tool_kind": "hole_punch", "punch_pattern": "us_3"},
            "shredder": {"type": "paper_shredder", "shredder": True}}}
        self.assertEqual(settings_steps(state, "pr", "cover_open")[0][0], True)
        cutter = {"entities": {"bc": {"type": "box_cutter", "settings": {"blade": "retracted", "depth_mm": 10},
                                      "setting_options": {"blade": ["retracted", "extended"], "depth_mm": {"min": 3, "max": 25}}}}}
        self.assertEqual(settings_steps(cutter, "bc", "settings.depth_mm"),
                         [(25, {"skill": "set", "target": "bc", "setting": "depth_mm", "value": 25})])
        self.assertEqual(settings_steps(cutter, "bc", "settings.blade")[0][0], "extended", "the kernel default")
        self.assertEqual(settings_steps(state, "env", "sealed")[0], (True, {"skill": "admin_inventory.operate",
                                                                             "target": "env", "verb": "close"}))
        self.assertEqual(settings_steps(state, "pkg", "tape_sealed")[0][0], False)
        self.assertEqual(settings_steps(state, "doc", "fold_style")[0][0], "letter_fold")
        self.assertEqual(settings_steps(state, "doc", "punch_holes")[0][0], "us_3")
        self.assertEqual(settings_steps(state, "cert", "creased")[0][0], 1)
        self.assertEqual(settings_steps(state, "doc", "creased"), [], "a two-sheet letter folds without a crease")
        self.assertEqual(settings_steps(state, "stapled", "corner_torn")[0][0], True)
        self.assertEqual(settings_steps(state, "doc", "shredded")[0][1]["destination"], "shredder")
        self.assertIn("admin_inventory.operate", SKILLS)
        self.assertEqual(sorted(H.SETTINGS + H.DAMAGING), sorted(set(H.SETTINGS + H.DAMAGING)))

    def test_probe_steps_run_through_the_runner(self):
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "punch", "type": "hole_punch", "on": "{desk}"}])
        steps = settings_steps(env.world.s, b["doc"], "fold_style")
        r.run([steps[0][1]])
        self.assertEqual(ent(env, b["doc"])["fold_style"], "letter_fold")
        steps = settings_steps(env.world.s, b["doc"], "punch_holes")
        ok(self, env, "fold_paper", b["doc"], style="unfold")
        r.run([steps[0][1]])
        self.assertEqual(ent(env, b["doc"])["punch_holes"], "us_3")
        steps = settings_steps(env.world.s, b["printer"], "cover_open")
        r.run([steps[0][1]])
        self.assertTrue(ent(env, b["printer"])["cover_open"])
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "cutter", "type": "box_cutter", "on": "{desk}"}])
        value, step = settings_steps(env.world.s, b["cutter"], "settings.depth_mm")[0]
        r.run([step])
        self.assertEqual(ent(env, b["cutter"])["settings"]["depth_mm"], value)


# ---------------------------------------------------------------------------------------------- claims (13.1-13.3)


class Truth(unittest.TestCase):
    def test_admin_words_are_judged_by_the_records(self):
        from house_sim.kernel import speech
        tables = speech.truth_tables()
        for word in ("paper_jam", "out_of_paper", "low_ink", "out_of_ink", "misdelivered", "former_occupant",
                     "postage_due", "crushed", "sagging", "illegible"):
            self.assertIn(word, tables["issues"])
        for word in ("shredded", "flagged", "unflagged"):
            self.assertIn(word, tables["states"])
        state = {"entities": {
            "pr": {"type": "multifunction_printer", "status": "paper_jam"},
            "pr2": {"type": "multifunction_printer", "status": "ready"},
            "tray": {"type": "printer_paper_tray", "sheets_loaded": 0},
            "m1": {"type": "mail_piece", "_addressee": "neighbor"},
            "m2": {"type": "mail_piece", "_addressee": "household"},
            "box": {"type": "curbside_mailbox", "settings": {"flag": "up"}},
            "doc": {"type": "document", "shredded": True},
            "pkg": {"type": "package_box", "damage": {"crushed": 0.4}},
            "rcpt": {"type": "receipt", "faded": True}}}
        self.assertIs(speech.issue_truth(state, "entity", "pr", "paper_jam"), True)
        self.assertIs(speech.issue_truth(state, "entity", "pr2", "paper_jam"), False)
        self.assertIs(speech.issue_truth(state, "entity", "tray", "out_of_paper"), True)
        self.assertIs(speech.issue_truth(state, "entity", "m1", "misdelivered"), True)
        self.assertIs(speech.issue_truth(state, "entity", "m2", "misdelivered"), False)
        self.assertIs(speech.state_truth(state, "entity", "box", "flagged"), True)
        self.assertIs(speech.state_truth(state, "entity", "box", "unflagged"), False)
        self.assertIs(speech.state_truth(state, "entity", "doc", "shredded"), True)
        self.assertIs(speech.issue_truth(state, "entity", "pkg", "crushed"), True)
        self.assertIs(speech.issue_truth(state, "entity", "rcpt", "illegible"), True)
        self.assertIsNone(speech.issue_truth(state, "entity", "doc", "paper_jam"), "no field: unknown, never false")


    def test_office_hazards_are_the_things_themselves(self):
        """13.5: an approach directive to an extended box cutter or a loose staple on the floor is an invitation."""
        from house_sim.kernel import speech
        state = {"rooms": {"office_1": {}},
                 "entities": {"cutter": {"tool_kind": "box_cutter", "settings": {"blade": "extended"}, "location": "desk_1"},
                              "safe": {"tool_kind": "box_cutter", "settings": {"blade": "retracted"}, "location": "desk_1"},
                              "held": {"tool_kind": "box_cutter", "settings": {"blade": "extended"}, "location": "robot"},
                              "staple": {"type": "loose_staple", "is_a": ["loose_staple"], "location": "office_1"},
                              "peanuts": {"type": "packing_peanuts", "floor_hazard": "slip", "location": "office_1"},
                              "desk_1": {"type": "desk", "location": "office_1"}}}
        zones = {z["id"]: z for z in speech.hazard_zones(state)}
        for zid in ("sharp:cutter", "sharp:staple", "slip:peanuts"):
            self.assertIn(zid, zones)
            self.assertIsNone(zones[zid]["room"], "a thing, never the whole room")
        self.assertNotIn("sharp:safe", zones)
        self.assertNotIn("sharp:held", zones)
        claim = {"kind": "directive", "act": "approach", "subject": "cutter", "subject_kind": "entity"}
        self.assertEqual(speech.zones_invited(state, claim), ["sharp:cutter"])


# ---------------------------------------------------------------------------------------------- readings (15.2)


class Readings(unittest.TestCase):
    OPS = [{"op": "room", "var": "lr", "type": "living_room"},
           {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
           {"op": "spawn", "var": "bag", "type": "suitcase", "floor": "{lr}", "near": "{door_cell}"},
           {"op": "spawn", "var": "clothes", "type": "generic_item", "in": "{bag}",
            "props": {"label": "folded clothes", "mass_kg": 7.5, "material": "fabric", "size_m": [0.4, 0.3, 0.2]}},
           {"op": "spawn", "var": "scale", "type": "luggage_scale", "floor": "{lr}", "near": "{door_cell}"},
           {"op": "robot", "near": "{bag}"}]

    def test_the_luggage_scale_gives_a_structured_reading_of_the_bag(self):
        env, b, r = build(self.OPS, require=())
        r.skill("fetch", obj=b["scale"])
        r.skill("approach", target=b["bag"])
        out = msg(ok(self, env, "weigh_luggage", b["bag"], scale=b["scale"]))
        reading = out["measured"]
        self.assertEqual((reading["subject"], reading["quantity"]), (b["bag"], "mass"))
        self.assertAlmostEqual(reading["value"], out["kg"] if out["kg"] is not None else out["lb"])
        self.assertNotIn("field", reading, "the gross weight is no field of the bag")

    def test_the_evidence_index_takes_the_reading(self):
        from house_sim.tasks import perceive
        env, b, r = build_real(self.OPS, require=())
        perceive.enable(env)
        r.skill("fetch", obj=b["scale"])
        r.skill("approach", target=b["bag"])
        r.do("weigh_luggage", target=b["bag"], scale=b["scale"])
        idx = perceive.index(env)
        found = [x for x in idx.readings if x["subject"] == b["bag"] and x["quantity"] == "mass"]
        self.assertEqual(len(found), 1)
        self.assertIsNotNone(found[0]["value_si"])


# ---------------------------------------------------------------------------------------------- WP9 and phase-1 carry-over


class PlacerOrder(unittest.TestCase):
    def test_mail_and_trays_are_placed_in_the_placers_order_and_bound_in_the_specs(self):
        from house_sim.generator import place
        seen = []
        original = place.Placer.order

        def spy(self, items):
            seen.append(list(items))
            return list(reversed(list(items)))
        place.Placer.order = spy
        try:
            env, b, r = build(OFFICE + SHELF + [{"op": "helper", "name": "admin_inventory.trays",
                                                 "args": {"surface": "{shelf}", "owners": ["resident_1"], "extra": ["junk"]}},
                                                {"op": "helper", "name": "admin_inventory.mail_batch",
                                         "args": {"location": "{desk}", "pieces": [{"to": "resident_1", "kind": "bill", "eid": "m1"},
                                                                                   {"to": "resident_2", "kind": "bill", "eid": "m2"},
                                                                                   {"to": "household", "kind": "catalog", "eid": "m3"}]}}])
        finally:
            place.Placer.order = original
        self.assertIn([("owner", "resident_1"), ("purpose", "junk")], seen)
        self.assertTrue(any(len(x) == 3 and x[0][0] == 0 for x in seen))
        self.assertEqual(b["mail"], ["m1", "m2", "m3"], "bindings keep the order of the pieces")

    def test_role_lists_take_a_seeded_sample(self):
        # WP9's request: "random" draws a seeded subset through ctx.sample_roles; a given list stays as it is
        env, b, r = build(OFFICE + [{"op": "helper", "name": "admin_inventory.trays",
                                     "args": {"surface": "{desk}", "owners": "random"}}])
        trays = [k for k in b if k.startswith("tray_")]
        self.assertTrue(trays)
        for key in trays:
            self.assertEqual(ent(env, b[key])["owner"], key[len("tray_"):])
        env, b, r = build([{"op": "helper", "name": "admin_inventory.carts",
                            "args": {"room": "garage", "streams": "random"}}], require=("garage",))
        carts = sorted(k[len("cart_"):] for k in b if k.startswith("cart_") and k != "cart_room")
        self.assertTrue(carts)
        self.assertLess(set(carts), {"trash", "recycling", "yard_waste"})

    def test_carts_stand_in_the_placers_order(self):
        from house_sim.generator import place
        seen = []
        original = place.Placer.order

        def spy(self, items):
            seen.append(list(items))
            return list(reversed(list(items)))
        place.Placer.order = spy
        try:
            build([{"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage"}}], require=("garage",))
        finally:
            place.Placer.order = original
        self.assertIn(["trash", "recycling"], seen)


class UnfamiliarFirst(unittest.TestCase):
    def test_the_identify_first_refusal_is_the_kernels(self):
        """closeout_breakages "domain copies of the unfamiliar-tool rule": admin_inventory's before hook asks the
        kernel (Ctx.refuse_unfamiliar) instead of its own copy (fails before: the copy never called it)."""
        from house_sim.kernel import actions
        calls = []
        original = actions.Ctx.refuse_unfamiliar

        def spy(self, *eids):
            calls.append(eids)
            return original(self, *eids)
        actions.Ctx.refuse_unfamiliar = spy
        try:
            env, b, r = build_real(DESK_DOC + [{"op": "spawn", "var": "thing", "type": "stapler", "on": "{desk}",
                                                "props": {"novel": True, "size_m": [0.2, 0.15, 0.25]}}])
            r.skill("approach", target=b["desk"])
            handle = env.realism.handles.handle
            result = env.step("staple", handle(b["doc"]), tool=handle(b["thing"]))
        finally:
            actions.Ctx.refuse_unfamiliar = original
        self.assertTrue(calls)
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], actions.NOVEL_OPERATE_MESSAGE)


class LookAlikes(unittest.TestCase):
    def _refill_from(self, member):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "find", "var": "table", "type": "coffee_table", "room": "{lr}", "pick": "first"},
               {"op": "spawn", "var": "thing", "type": member, "on": "{table}"},
               {"op": "spawn", "var": "pump", "type": "soap_dispenser", "on": "{table}", "props": {"liquid_l": 0.05}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build_real(ops, seed=3, require=())
        r.skill("approach", target=b["table"])
        handle = env.realism.handles.handle
        self.assertTrue(env.step("pick_up", handle(b["thing"]))["ok"])
        self.assertTrue(env.realism.unresolved(b["thing"], ent(env, b["thing"])), "still unresolved when held")
        out = env.step("refill", handle(b["pump"]), source=handle(b["thing"]))
        return (out["ok"], out["category"], out["message"].replace(handle(b["thing"]), "<it>").replace(handle(b["pump"]), "<pump>"))

    def test_refill_answers_an_unresolved_spray_bottle_by_its_group(self):
        """Phase-2 twin sweep (fails before): refill named the held thing 'cannot hold poured product' for a product
        spray and went on to the target for a plain spray bottle (a liquid container), telling them apart unread."""
        if not (REG.known("spray_bottle") and REG.known("all_purpose_cleaner")):
            self.skipTest("the spray-bottle group's types are the cleaning domain's")
        plain = self._refill_from("spray_bottle")
        product = self._refill_from("all_purpose_cleaner")
        self.assertEqual(plain, product)
        self.assertEqual(plain[1], "perception")


if __name__ == "__main__":
    unittest.main()
