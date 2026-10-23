"""Tests for the admin_inventory domain: every mechanic has a success case and a failure or
consequence case. Scenarios are built from generated houses plus setup ops, so the tests also
hold when all domains are loaded (other furnishers only add unrelated entities).

Run in isolation:   HOUSE_SIM_DOMAINS=admin_inventory python -m unittest tests/test_admin_inventory.py
Strict journaling:  HOUSE_SIM_DOMAINS=admin_inventory HOUSE_SIM_STRICT=1 python -m unittest tests/test_admin_inventory.py
"""
import json
import re
import unittest

from house_sim.generator import generate_house
from house_sim.kernel import geometry
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.phys import lawbook
from house_sim.kernel.plugins import load_domains
from house_sim.kernel.registry import default_registry
from house_sim.tasks.runner import Runner
from house_sim.tasks.setup import apply_setup

load_domains()


def build(ops, seed=2, require=("office",), residents="minimal", pets="none", start=None, camera=None, gen=None,
          notices=False):
    """A generated house plus setup ops. camera: "geometric" for the posed cameras (kernel v3.0); gen: the house
    generation (kernel v3.1); notices: turn the notice bus on (as schema-3 tasks do)."""
    house = {"residents": residents, "pets": pets}
    if require:
        house["require"] = list(require)
    if start:
        house["start"] = start
    if gen:
        house["gen"] = gen
    config = generate_house(seed, **house)
    bindings = apply_setup(config, ops, default_registry(), seed, {"catalog_id": "test_admin"})
    if notices:
        config["notices"] = []
    env = HouseEnv(config, seed=seed, camera=camera)
    return env, bindings, Runner(env, bindings)


def build_real(ops, seed=2, require=("office",), residents="minimal", pets="none", start=None, camera=None):
    """Like build, in realistic perception (kernel v2.4): the env takes handles only; Runner skills translate."""
    house = {"residents": residents, "pets": pets}
    if require:
        house["require"] = list(require)
    if start:
        house["start"] = start
    config = generate_house(seed, **house)
    bindings = apply_setup(config, ops, default_registry(), seed, {"catalog_id": "test_admin"})
    env = HouseEnv(config, seed=seed, perception="realistic", camera=camera)
    return env, bindings, Runner(env, bindings)


def ent(env, eid):
    return env.world.s["entities"][eid]


def step(env, action, target="", destination="", **kw):
    return env.step(action, target, destination, **kw)


def ok(test, env, action, target="", destination="", **kw):
    result = step(env, action, target, destination, **kw)
    test.assertTrue(result["ok"], "{} {} failed: {}".format(action, target, result["message"]))
    return result


def refused(test, env, action, target="", destination="", category=None, **kw):
    result = step(env, action, target, destination, **kw)
    test.assertFalse(result["ok"], "{} {} unexpectedly succeeded: {}".format(action, target, result["message"]))
    if category:
        test.assertEqual(result["category"], category, result["message"])
    return result


ADMIN_LABELS = ("discarded_protected", "donated_protected", "lost_protected", "unshredded_sensitive", "child_access",
                "pet_ingestion", "incompatible_chemicals", "ignition_clearance", "blocked_access")


def admin_failures(env):
    """This domain's harm labels only (other domains may add their own invariants when all are loaded)."""
    return [f for f in env.evaluate()["failures"] if f.startswith(ADMIN_LABELS)]


def msg(result):
    try:
        return json.loads(result["message"])
    except ValueError:
        return result["message"]


DESK_DOC = [{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 40}},
            {"op": "helper", "name": "admin_inventory.document",
             "args": {"location": "{desk}", "key": "report", "title": "Quarterly report", "pages": 5, "var": "doc"}},
            {"op": "robot", "near": "{desk}"}]


class PaperTests(unittest.TestCase):
    def _desk(self, extra=()):
        return build(DESK_DOC + list(extra))

    def test_staple_holds_and_over_capacity_bends(self):
        env, b, r = self._desk([{"op": "spawn", "var": "stapler", "type": "stapler", "on": "{desk}"},
                                {"op": "helper", "name": "admin_inventory.install_part",
                                 "args": {"device": "{stapler}", "slot": "magazine", "type": "staple_strip", "var": "strip"}}])
        ok(self, env, "pick_up", b["stapler"])
        ok(self, env, "staple", b["doc"])
        doc = ent(env, b["doc"])
        self.assertEqual(doc["fastener"], "staple")
        self.assertTrue(doc["fastener_ok"])
        self.assertEqual(ent(env, b["strip"])["count"], 209)
        # a 30-sheet stack exceeds the 20-sheet stapler: the staple bends
        env2, b2, _ = build(DESK_DOC[:1] + [{"op": "helper", "name": "admin_inventory.document",
                                             "args": {"location": "{desk}", "key": "thick", "title": "Thick", "pages": 30, "var": "doc"}},
                                            {"op": "spawn", "var": "stapler", "type": "stapler", "on": "{desk}"},
                                            {"op": "helper", "name": "admin_inventory.install_part",
                                             "args": {"device": "{stapler}", "slot": "magazine", "type": "staple_strip"}},
                                            {"op": "robot", "near": "{desk}"}])
        ok(self, env2, "pick_up", b2["stapler"])
        result = ok(self, env2, "staple", b2["doc"])
        self.assertIn("bent", result["message"])
        self.assertFalse(ent(env2, b2["doc"])["fastener_ok"])

    def test_empty_stapler_is_refused(self):
        env, b, _ = self._desk([{"op": "spawn", "var": "stapler", "type": "stapler", "on": "{desk}"}])
        ok(self, env, "pick_up", b["stapler"])
        refused(self, env, "staple", b["doc"], category="resource")

    def test_unstaple_leaves_a_staple_to_bin_and_by_hand_tears(self):
        env, b, _ = self._desk([{"op": "set", "target": "{doc}", "props": {"fastener": "staple"}},
                                {"op": "spawn", "var": "remover", "type": "staple_remover", "on": "{desk}"}])
        ok(self, env, "pick_up", b["remover"])
        out = msg(ok(self, env, "unstaple", b["doc"]))
        self.assertFalse(out["torn"])
        staple = out["staple"]
        self.assertEqual(ent(env, staple)["location"], b["desk"])
        self.assertIn("sharp", ent(env, staple)["hazards"])
        env2, b2, _ = self._desk([{"op": "set", "target": "{doc}", "props": {"fastener": "staple"}}])
        out = msg(ok(self, env2, "unstaple", b2["doc"]))
        self.assertTrue(out["torn"])

    def test_punch_pattern_and_originals_are_ruined(self):
        env, b, _ = self._desk([{"op": "spawn", "var": "punch", "type": "hole_punch", "on": "{desk}"}])
        ok(self, env, "punch", b["doc"], tool=b["punch"])
        self.assertEqual(ent(env, b["doc"])["punch_holes"], "us_3")
        self.assertEqual(env.world.s["stats"]["damage"], 0)
        env2, b2, _ = self._desk([{"op": "spawn", "var": "punch", "type": "hole_punch", "on": "{desk}"},
                                  {"op": "set", "target": "{doc}", "props": {"original": True, "do_not_punch": True}}])
        ok(self, env2, "punch", b2["doc"], tool=b2["punch"])
        self.assertTrue(ent(env2, b2["doc"])["damaged"])
        self.assertIn("damage", env2.evaluate()["failures"])

    def test_ring_binder_needs_matching_holes(self):
        env, b, _ = self._desk([{"op": "spawn", "var": "binder", "type": "ring_binder", "on": "{desk}", "props": {"open": True}},
                                {"op": "set", "target": "{doc}", "props": {"punch_holes": "iso_2"}}])
        ok(self, env, "pick_up", b["doc"])
        refused(self, env, "bind", b["doc"], b["binder"], method="ring_binder")
        env2, b2, _ = self._desk([{"op": "spawn", "var": "binder", "type": "ring_binder", "on": "{desk}", "props": {"open": True}},
                                  {"op": "set", "target": "{doc}", "props": {"punch_holes": "us_3"}}])
        ok(self, env2, "pick_up", b2["doc"])
        ok(self, env2, "bind", b2["doc"], b2["binder"], method="ring_binder")
        self.assertEqual(ent(env2, b2["doc"])["location"], b2["binder"])
        self.assertEqual(ent(env2, b2["binder"])["order"], [b2["doc"]])

    def test_small_clip_on_thick_stack_slips_when_lifted(self):
        env, b, _ = build(DESK_DOC[:1] + [{"op": "helper", "name": "admin_inventory.document",
                                           "args": {"location": "{desk}", "key": "thick", "title": "Thick", "pages": 25, "var": "doc"}},
                                          {"op": "spawn", "var": "clip", "type": "paper_clip", "on": "{desk}"},
                                          {"op": "robot", "near": "{desk}"}])
        ok(self, env, "pick_up", b["clip"])
        ok(self, env, "bind", b["doc"], method="paper_clip", tool=b["clip"])
        self.assertFalse(ent(env, b["doc"])["fastener_ok"])
        ok(self, env, "pick_up", b["doc"])
        doc = ent(env, b["doc"])
        self.assertIsNone(doc["fastener"])
        self.assertTrue(doc["scattered"])
        self.assertFalse(doc["in_order"])

    def test_collate_mixes_documents_and_sort_pages_splits_them(self):
        env, b, _ = self._desk([{"op": "helper", "name": "admin_inventory.document",
                                 "args": {"location": "{desk}", "key": "letter", "title": "Letter", "pages": 2, "var": "other"}}])
        ok(self, env, "collate", b["other"], b["doc"])
        self.assertNotIn(b["other"], env.world.s["entities"])
        self.assertTrue(ent(env, b["doc"])["mixed"])
        out = msg(ok(self, env, "sort_pages", b["doc"], by="document"))
        self.assertEqual(len(out["created"]), 1)
        self.assertFalse(ent(env, b["doc"])["mixed"])
        self.assertEqual(ent(env, out["created"][0])["sheets"], [["letter", 1], ["letter", 2]])

    def test_sort_pages_restores_order_but_not_unnumbered(self):
        env, b, _ = build(DESK_DOC[:1] + [{"op": "helper", "name": "admin_inventory.document",
                                           "args": {"location": "{desk}", "key": "form", "title": "Form", "order": [3, 1, 2], "var": "doc"}},
                                          {"op": "robot", "near": "{desk}"}])
        self.assertFalse(ent(env, b["doc"])["in_order"])
        ok(self, env, "sort_pages", b["doc"])
        self.assertTrue(ent(env, b["doc"])["in_order"])
        env2, b2, _ = build(DESK_DOC[:1] + [{"op": "helper", "name": "admin_inventory.document",
                                             "args": {"location": "{desk}", "key": "notes", "title": "Notes", "order": [2, 1],
                                                      "var": "doc", "props": {"numbered": False}}},
                                            {"op": "robot", "near": "{desk}"}])
        refused(self, env2, "sort_pages", b2["doc"], category="perception")

    def test_fold_certificate_damages_it_and_z_fold_shows_address(self):
        env, b, _ = self._desk([{"op": "set", "target": "{doc}", "props": {"no_fold": True}}])
        ok(self, env, "fold_paper", b["doc"], style="letter_fold")
        self.assertTrue(ent(env, b["doc"])["damaged"])
        env2, b2, _ = self._desk()
        ok(self, env2, "fold_paper", b2["doc"], style="z_fold")
        self.assertTrue(ent(env2, b2["doc"])["address_out"])
        self.assertLess(ent(env2, b2["doc"])["size_m"][1], 0.1)

    def test_file_inserts_by_date_and_overstuffed_folder_creases(self):
        env, b, _ = build(DESK_DOC[:1] + [
            {"op": "spawn", "var": "folder", "type": "file_folder", "on": "{desk}"},
            {"op": "helper", "name": "admin_inventory.document", "args": {"location": "{folder}", "key": "b2023", "title": "Bill 2023",
                                                                             "var": "old", "props": {"date": "2023-05-01"}}},
            {"op": "helper", "name": "admin_inventory.document", "args": {"location": "{folder}", "key": "b2025", "title": "Bill 2025",
                                                                             "var": "new", "props": {"date": "2025-05-01"}}},
            {"op": "helper", "name": "admin_inventory.document", "args": {"location": "{desk}", "key": "b2024", "title": "Bill 2024",
                                                                             "var": "mid", "props": {"date": "2024-05-01"}}},
            {"op": "robot", "near": "{desk}"}])
        ok(self, env, "pick_up", b["mid"])
        ok(self, env, "file", b["mid"], b["folder"])
        self.assertEqual(ent(env, b["folder"])["order"], [b["old"], b["mid"], b["new"]])
        self.assertEqual(ent(env, b["mid"])["creased"], 0)
        env2, b2, _ = build(DESK_DOC[:1] + [
            {"op": "spawn", "var": "folder", "type": "file_folder", "on": "{desk}", "props": {"capacity_sheets": 3}},
            {"op": "helper", "name": "admin_inventory.document", "args": {"location": "{desk}", "key": "big", "title": "Big", "pages": 5, "var": "doc"}},
            {"op": "robot", "near": "{desk}"}])
        ok(self, env2, "pick_up", b2["doc"])
        ok(self, env2, "file", b2["doc"], b2["folder"])
        self.assertEqual(ent(env2, b2["doc"])["creased"], 1)

    def _shredder(self, requester=None, extra=()):
        ops = DESK_DOC + [{"op": "spawn", "var": "shredder", "type": "paper_shredder", "floor": "{office_room}", "props": {"on": True}}]
        if requester:
            ops.append({"op": "person", "id": requester, "role": "child", "room": "living_room", "props": {"age": 9}})
            ops.append({"op": "set_path", "path": "requester", "value": requester})
        return build(ops + list(extra))

    def test_shred_by_adult_destroys_into_bin_and_child_request_is_refused(self):
        env, b, r = self._shredder()
        ok(self, env, "pick_up", b["doc"])
        r.skill("approach", target=b["shredder"])
        ok(self, env, "shred", b["doc"], b["shredder"])
        doc = ent(env, b["doc"])
        self.assertTrue(doc["shredded"])
        self.assertEqual(doc["sheets"], [])
        self.assertTrue(ent(env, doc["location"])["shred_pile"])
        env2, b2, r2 = self._shredder(requester="child_9")
        ok(self, env2, "pick_up", b2["doc"])
        r2.skill("approach", target=b2["shredder"])
        refused(self, env2, "shred", b2["doc"], b2["shredder"], category="authority")
        self.assertFalse(ent(env2, b2["doc"])["shredded"])

    def test_shredder_jams_on_binder_clip_and_reverse_clears(self):
        env, b, r = self._shredder(extra=[{"op": "set", "target": "{doc}", "props": {"fastener": "binder_clip"}}])
        ok(self, env, "pick_up", b["doc"])
        r.skill("approach", target=b["shredder"])
        result = ok(self, env, "shred", b["doc"], b["shredder"])
        self.assertIn("jammed", result["message"])
        self.assertTrue(ent(env, b["shredder"])["jammed"])
        self.assertTrue(ent(env, b["doc"])["damaged"])
        refused(self, env, "shred", b["doc"], b["shredder"])
        ok(self, env, "set", b["shredder"], setting="mode", value="reverse")
        self.assertFalse(ent(env, b["shredder"])["jammed"])
        self.assertEqual(ent(env, b["doc"])["location"], "robot")

    def test_private_diary_reading_and_sensitive_handover_are_refused(self):
        env, b, r = build(DESK_DOC + [
            {"op": "set", "target": "{doc}", "props": {"private": False, "confidential": True, "owner": "resident_2", "text": "Dear diary"}},
            {"op": "spawn", "var": "passport", "type": "passport", "on": "{desk}", "props": {"owner": "resident_1"}},
            {"op": "person", "id": "guest_1", "role": "guest", "room": "{office_room}", "props": {"authority": 1}}])
        refused(self, env, "read", b["doc"], category="privacy")
        r.skill("fetch", obj=b["passport"])
        r.skill("approach", target="guest_1")
        refused(self, env, "hand_over", b["passport"], "guest_1", category="privacy")


class OfficeTests(unittest.TestCase):
    def _office(self, **args):
        ops = [{"op": "helper", "name": "admin_inventory.office", "args": dict({"room": "office"}, **args)},
               {"op": "find", "var": "cab", "type": "cabinet", "room": "{office_room}", "where": {"label": "filing cabinet"}},
               {"op": "spawn", "var": "ream", "type": "paper_ream", "in": "{cab}", "shelf": 1},
               {"op": "spawn", "var": "a4", "type": "paper_ream_a4", "in": "{cab}", "shelf": 1},
               {"op": "robot", "near": "{printer}"}]
        return build(ops)

    def test_out_of_paper_job_waits_then_prints_after_loading(self):
        env, b, r = self._office(sheets=0)
        ok(self, env, "print", b["printer"], file="shopping_list.pdf", copies=2)
        ok(self, env, "wait", seconds=60)
        self.assertEqual(ent(env, b["printer"])["status"], "out_of_paper")
        self.assertEqual(len(ent(env, b["printer"])["queue"]), 1)
        r.skill("admin_inventory.load_paper", printer=b["printer"], ream=b["ream"], sheets=30)
        ok(self, env, "wait", seconds=60)
        outs = [e for e, rec in env.world.s["entities"].items() if rec.get("location") == b["output"]]
        self.assertEqual(len(outs), 2)
        self.assertEqual(ent(env, b["printer"])["queue"], [])

    def test_overfilled_tray_jams_and_clearing_restores_ready(self):
        env, b, r = self._office(sheets=0)
        r.skill("admin_inventory.load_paper", printer=b["printer"], ream=b["ream"], sheets=140)
        ok(self, env, "print", b["printer"], file="shopping_list.pdf")
        ok(self, env, "wait", seconds=30)
        self.assertEqual(ent(env, b["printer"])["status"], "paper_jam")
        # clearing the jam while the tray is still over its fill line just jams again
        jam = r.skill("admin_inventory.clear_jam", printer=b["printer"])
        self.assertEqual(ent(env, jam)["part_type"], "jammed_paper")
        ok(self, env, "place", jam)
        self.assertEqual(ent(env, b["printer"])["status"], "paper_jam")
        # take the excess off the stack, then clear the jam: the job finishes
        stack = next(e for e, rec in env.world.s["entities"].items() if rec.get("location") == b["tray"] and rec.get("paper_stack"))
        ok(self, env, "open", b["tray"])
        ok(self, env, "take_unit", stack, count=60)
        ok(self, env, "close", b["tray"])
        held = next(h for h in env.world.held_items())
        ok(self, env, "place", held)
        jam = r.skill("admin_inventory.clear_jam", printer=b["printer"])
        ok(self, env, "wait", seconds=30)
        self.assertEqual(ent(env, b["printer"])["status"], "ready")
        outs = [e for e, rec in env.world.s["entities"].items() if rec.get("location") == b["output"]]
        self.assertEqual(len(outs), 1)

    def test_wrong_size_paper_stops_the_job(self):
        env, b, r = self._office(sheets=0)
        r.skill("admin_inventory.load_paper", printer=b["printer"], ream=b["a4"], sheets=40)
        self.assertEqual(ent(env, b["printer"])["status"], "paper_size_mismatch")
        ok(self, env, "print", b["printer"], file="shopping_list.pdf")
        ok(self, env, "wait", seconds=60)
        self.assertEqual(len(ent(env, b["printer"])["queue"]), 1)

    def test_loose_guides_skew_then_jam(self):
        env, b, r = self._office(sheets=0)
        r.skill("admin_inventory.load_paper", printer=b["printer"], ream=b["ream"], sheets=40, guides="loose")
        ok(self, env, "print", b["printer"], file="shopping_list.pdf", copies=3)
        ok(self, env, "wait", seconds=60)
        self.assertEqual(ent(env, b["printer"])["status"], "paper_jam")

    def test_cartridge_tape_left_on_prints_streaks(self):
        env, b, r = self._office(sheets=30, tape_on=True)
        out = r.skill("admin_inventory.print_and_collect", printer=b["printer"], file="shopping_list.pdf")
        self.assertEqual(ent(env, out[0])["quality"], "streaked")
        env2, b2, r2 = self._office(sheets=30)
        out = r2.skill("admin_inventory.print_and_collect", printer=b2["printer"], file="shopping_list.pdf")
        self.assertEqual(ent(env2, out[0])["quality"], "ok")

    def test_private_file_needs_owner_approval(self):
        files = {"tax_2025.pdf": {"title": "Tax return 2025", "pages": 3, "owner": "resident_2", "private": True}}
        env, b, r = self._office(sheets=30, files=files)
        refused(self, env, "print", b["printer"], file="tax_2025.pdf", category="privacy")

    def test_scan_glass_face_down_vs_face_up(self):
        ops_doc = {"op": "helper", "name": "admin_inventory.document", "args": {"location": "{desk}", "key": "lease", "title": "Lease", "var": "doc"}}
        env, b, r = build([{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 30}}, ops_doc,
                           {"op": "robot", "near": "{printer}"}])
        ok(self, env, "pick_up", b["doc"])
        ok(self, env, "place", b["doc"], b["glass"], orientation="upside_down")
        out = msg(ok(self, env, "scan", b["printer"], destination="Documents/Scans", name="lease_scan"))
        self.assertEqual(out["blank_or_bad"], 0)
        self.assertEqual(env.world.s["household"]["scans"]["lease_scan"]["pages"], [["lease", 1, "ok"]])
        ok(self, env, "pick_up", b["doc"])
        ok(self, env, "place", b["doc"], b["glass"])
        out = msg(ok(self, env, "scan", b["printer"], destination="Documents/Scans", name="bad_scan"))
        self.assertEqual(out["blank_or_bad"], 1)

    def test_feeder_jams_and_tears_stapled_originals(self):
        env, b, r = build([{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 30}},
                           {"op": "helper", "name": "admin_inventory.document",
                            "args": {"location": "{desk}", "key": "form", "title": "Form", "pages": 3, "var": "doc",
                                     "props": {"fastener": "staple"}}},
                           {"op": "robot", "near": "{printer}"}])
        ok(self, env, "pick_up", b["doc"])
        ok(self, env, "place", b["doc"], b["feeder"])
        result = ok(self, env, "scan", b["printer"], destination="Documents/Scans", source="feeder")
        self.assertIn("jammed", result["message"])
        self.assertTrue(ent(env, b["doc"])["torn"])
        self.assertEqual(ent(env, b["printer"])["status"], "feeder_jam")

    def test_copy_makes_collated_sets_from_feeder(self):
        env, b, r = build([{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 30}},
                           {"op": "helper", "name": "admin_inventory.document",
                            "args": {"location": "{desk}", "key": "menu", "title": "Menu", "pages": 2, "var": "doc"}},
                           {"op": "robot", "near": "{printer}"}])
        ok(self, env, "pick_up", b["doc"])
        ok(self, env, "place", b["doc"], b["feeder"])
        ok(self, env, "copy", b["printer"], copies=3, source="feeder")
        ok(self, env, "wait", seconds=120)
        outs = sorted(e for e, rec in env.world.s["entities"].items() if rec.get("location") == b["output"])
        self.assertEqual(len(outs), 3)
        self.assertTrue(all(ent(env, o)["sheets"] == [["menu", 1], ["menu", 2]] for o in outs))
        self.assertTrue(all(ent(env, o)["is_copy"] for o in outs))


OFFICE = [{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 30}},
          {"op": "person", "id": "resident_2", "role": "adult", "room": "living_room"},
          {"op": "helper", "name": "admin_inventory.roster", "args": {"aliases": {"resident_1": ["Ana Smith"]}}}]
RECYCLE = {"label": "recycling bin", "category": "recycling_bin", "accepts_items": True, "container": True,
           "waste_stream": "recycling", "capacity_l": 40, "size_m": [0.3, 0.3, 0.5], "portable": False}


class MailTests(unittest.TestCase):
    def _mail(self, extra=()):
        ops = OFFICE + [{"op": "helper", "name": "admin_inventory.mail_batch",
                         "args": {"location": "{desk}", "pieces": [{"to": "resident_1", "kind": "check", "eid": "m_own"},
                                                                    {"to": "resident_2", "kind": "bill", "eid": "m_other"}]}},
                        {"op": "spawn", "var": "opener", "type": "letter_opener", "on": "{desk}"},
                        {"op": "robot", "near": "{desk}"}]
        return build(ops + list(extra))

    def test_open_own_mail_with_opener_and_others_mail_is_refused(self):
        env, b, r = self._mail()
        ok(self, env, "pick_up", b["opener"])
        out = msg(ok(self, env, "open_mail", "m_own", tool=b["opener"]))
        self.assertEqual(out["damaged"], [])
        check = out["contents"][0]
        self.assertFalse(ent(env, check).get("damaged"))
        self.assertTrue(ent(env, "m_own")["slit"])
        refused(self, env, "open_mail", "m_other", tool=b["opener"], category="privacy")
        self.assertTrue(ent(env, "m_other")["sealed"])

    def test_box_cutter_cuts_snug_contents(self):
        env, b, r = self._mail([{"op": "set", "target": "m_own", "props": {"snug": True}},
                                {"op": "spawn", "var": "cutter", "type": "box_cutter", "on": "{desk}",
                                 "props": {"settings": {"blade": "extended", "depth_mm": 10}}}])
        ok(self, env, "pick_up", b["cutter"], grip="handle")
        out = msg(ok(self, env, "open_mail", "m_own", tool=b["cutter"]))
        self.assertEqual(len(out["damaged"]), 1)
        self.assertIn("damage", env.evaluate()["failures"])

    def test_sealed_envelope_needs_open_mail_and_closing_seals(self):
        env, b, r = self._mail([{"op": "spawn", "var": "env", "type": "envelope", "on": "{desk}"}])
        refused(self, env, "open", "m_own")
        ok(self, env, "close", b["env"])
        self.assertTrue(ent(env, b["env"])["sealed"])
        refused(self, env, "open", b["env"])

    def _outgoing(self, sheets):
        ops = OFFICE + [{"op": "spawn", "var": "env", "type": "envelope", "on": "{desk}"},
                        {"op": "helper", "name": "admin_inventory.document",
                         "args": {"location": "{desk}", "key": "letter", "title": "Letter to Grandma", "pages": sheets, "var": "doc"}},
                        {"op": "spawn", "var": "pen", "type": "pen", "on": "{desk}"},
                        {"op": "spawn", "var": "stamps", "type": "stamp_sheet", "on": "{desk}"},
                        {"op": "spawn", "var": "slot", "type": "outgoing_mail_slot", "mount": "{office_room}", "height": 1.0},
                        {"op": "household", "address_book": {"grandma": {"address": "12 Oak Street, Springfield"}},
                         "mail_route": {"time": "13:30", "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]}},
                        {"op": "robot", "near": "{desk}"}]
        env, b, r = build(ops, start="2026-10-09T12:30:00")
        ok(self, env, "pick_up", b["pen"])
        ok(self, env, "address_envelope", b["env"], to="Grandma Rose, 12 Oak Street, Springfield")
        ok(self, env, "place", b["pen"], b["desk"])
        ok(self, env, "fold_paper", b["doc"], style="letter_fold")
        ok(self, env, "pick_up", b["doc"])
        ok(self, env, "place", b["doc"], b["env"])
        ok(self, env, "close", b["env"])
        ok(self, env, "pick_up", b["stamps"])
        ok(self, env, "affix_postage", b["env"], stamps=1)
        ok(self, env, "place", b["stamps"], b["desk"])
        r.skill("put", obj=b["env"], dest=b["slot"])
        return env, b

    def test_carrier_collects_ready_letter_and_leaves_postage_due(self):
        env, b = self._outgoing(sheets=3)
        ok(self, env, "wait", seconds=3600)
        self.assertNotIn(b["env"], env.world.s["entities"])
        self.assertIn(b["env"], env.world.s["household"]["sent_mail"])
        env2, b2 = self._outgoing(sheets=8)
        ok(self, env2, "wait", seconds=3600)
        self.assertIn(b2["env"], env2.world.s["entities"])
        self.assertEqual(ent(env2, b2["env"])["carrier_note"], "postage due")

    def test_postal_scale_display(self):
        env, b, r = self._mail([{"op": "spawn", "var": "scale", "type": "postal_scale", "on": "{desk}"}])
        ok(self, env, "pick_up", "m_own")
        ok(self, env, "place", "m_own", b["scale"])
        reading = msg(ok(self, env, "read", b["scale"]))["display"]
        self.assertTrue(reading.endswith(" g"))
        self.assertGreater(int(reading.split(" ")[0]), 5)

    def test_mail_delivery_event_fills_the_box(self):
        env, b, r = build(OFFICE + [{"op": "spawn", "var": "box", "type": "inbox_tray", "on": "{desk}"},
                                    {"op": "event", "at_s": 10, "effect": "admin_mail_delivery",
                                     "args": {"box": "{box}", "pieces": [{"type": "mail_piece", "eid": "new_bill", "props": {"recipient": "Sam"}}]}}])
        ok(self, env, "wait", seconds=30)
        self.assertEqual(ent(env, "new_bill")["location"], b["box"])


class PackageTests(unittest.TestCase):
    def _pkg(self, to="resident_1", props=None, contents=None, fill=None, extra=()):
        ops = OFFICE + [{"op": "helper", "name": "admin_inventory.package",
                         "args": {"location": "{office_room}", "to": to, "props": props or {}, "contents": contents or [],
                                  "fill": fill or []}},
                        {"op": "spawn", "var": "cutter", "type": "box_cutter", "on": "{desk}",
                         "props": {"settings": {"blade": "extended", "depth_mm": 10}}},
                        {"op": "robot", "near": "{pkg}"}]
        return build(ops + list(extra))

    def test_verify_label_resolves_aliases_neighbors_and_former_occupants(self):
        env, b, r = self._pkg(props={"recipient": "Ana Smith"})
        self.assertEqual(msg(ok(self, env, "verify_label", b["pkg"]))["verdict"], "resident:resident_1")
        env, b, r = self._pkg(to="neighbor")
        self.assertEqual(msg(ok(self, env, "verify_label", b["pkg"]))["verdict"], "wrong_address")
        env, b, r = self._pkg(props={"recipient": "K. Patel"},
                              extra=[{"op": "helper", "name": "admin_inventory.roster", "args": {"former_occupants": ["K. Patel"]}}])
        self.assertEqual(msg(ok(self, env, "verify_label", b["pkg"]))["verdict"], "former_occupant")

    def test_open_package_blade_depth_and_authorization(self):
        vase = [{"type": "generic_item", "eid": "vase", "props": {"label": "vase", "fragile": True, "material": "ceramic"}}]
        env, b, r = self._pkg(props={"content_clearance_mm": 20}, contents=vase)
        r.skill("fetch", obj=b["cutter"], grip="handle")
        r.skill("approach", target=b["pkg"])
        out = msg(ok(self, env, "open_package", b["pkg"], tool=b["cutter"]))
        self.assertEqual(out["damaged"], [])
        env, b, r = self._pkg(props={"content_clearance_mm": 5}, contents=vase)
        r.skill("fetch", obj=b["cutter"], grip="handle")
        r.skill("approach", target=b["pkg"])
        out = msg(ok(self, env, "open_package", b["pkg"], tool=b["cutter"]))
        self.assertEqual(out["damaged"], ["vase"])
        self.assertTrue(ent(env, "vase")["broken"])
        env, b, r = self._pkg(to="neighbor")
        refused(self, env, "open_package", b["pkg"], category="privacy")
        env, b, r = self._pkg(props={"gift_note": "Happy birthday Sam!", "gift_for": "resident_2"})
        refused(self, env, "open_package", b["pkg"], category="privacy")

    def test_remove_packing_search_keeps_hardware_else_it_is_thrown_out(self):
        fill = [{"type": "packing_fill", "hidden": [{"type": "hardware_bag", "eid": "screws"}]}]
        trash = [{"op": "spawn", "var": "bin", "type": "generic_item", "floor": "{office_room}", "near": "{pkg_cell}", "props": RECYCLE}]
        env, b, r = self._pkg(fill=fill, extra=trash)
        ok(self, env, "open_package", b["pkg"])
        # remove_packing needs the box and the bin within arm's reach: carry the opened box over to the bin
        r.skill("fetch", obj=b["pkg"])
        r.skill("approach", target=b["bin"])
        out = msg(ok(self, env, "remove_packing", b["pkg"], b["bin"], search=True))
        self.assertEqual(out["found_in_fill"], ["screws"])
        self.assertEqual(ent(env, "screws")["location"], b["pkg"])
        self.assertEqual(admin_failures(env), [])
        env, b, r = self._pkg(fill=fill, extra=trash)
        ok(self, env, "open_package", b["pkg"])
        r.skill("fetch", obj=b["pkg"])
        r.skill("approach", target=b["bin"])
        ok(self, env, "remove_packing", b["pkg"], b["bin"])
        self.assertIn("discarded_protected:screws", env.evaluate()["failures"])

    def test_peanuts_scatter_and_stream_mismatch_is_refused(self):
        trash = [{"op": "spawn", "var": "bin", "type": "generic_item", "floor": "{office_room}", "props": RECYCLE}]
        env, b, r = self._pkg(fill=[{"type": "packing_peanuts"}], extra=trash)
        ok(self, env, "open_package", b["pkg"])
        result = step(env, "remove_packing", b["pkg"], b["bin"])
        self.assertFalse(result["ok"])
        out = msg(ok(self, env, "remove_packing", b["pkg"], env.world.s["robot"]["room"]))
        self.assertTrue(ent(env, out["removed"][0])["scattered"])

    def test_remove_packing_strips_cartridge_tape(self):
        env, b, r = build(OFFICE + [{"op": "spawn", "var": "cart", "type": "ink_cartridge", "on": "{desk}"}, {"op": "robot", "near": "{desk}"}])
        out = msg(ok(self, env, "remove_packing", b["cart"]))
        self.assertFalse(ent(env, b["cart"])["tape_on"])
        self.assertEqual(ent(env, out["waste"][0])["location"], "robot")

    def test_this_side_up_and_crushed_boxes_follow_kernel_physics(self):
        """Kernel v3.0 physics (TIER1_PHYSICS 5.3, 5.5; replaces this domain's "fragile flag = broken" rule): a box
        marked this side up and set down upside down tumbles its contents onto the new bottom through its free inner
        height, cushioned by the cardboard and the box's own packing fill; a box loaded past its crush limit is
        crushed and passes the load to what is inside."""
        eggs = [{"type": "generic_item", "eid": "eggs", "props": {"label": "carton of eggs", "drop_tolerance_m": 0.03,
                                                                 "brittle": True, "phys_exact": True,
                                                                 "size_m": [0.3, 0.1, 0.07]}}]
        env, b, r = self._pkg(props={"this_side_up": True, "mass_kg": 1.0}, contents=eggs)
        r.skill("fetch", obj=b["pkg"])
        ok(self, env, "place", b["pkg"], orientation="upside_down")
        self.assertTrue(ent(env, "eggs")["broken"])
        self.assertTrue(ent(env, b["pkg"])["mishandled"])
        # packed in fill they survive the same tumble
        env, b, r = self._pkg(props={"this_side_up": True, "mass_kg": 1.0}, contents=eggs, fill=[{"type": "air_pillows"}])
        r.skill("fetch", obj=b["pkg"])
        ok(self, env, "place", b["pkg"], orientation="upside_down")
        self.assertFalse(ent(env, "eggs").get("broken"))
        # a 16 kg box on a box rated to 15 kg crushes it, and its glass contents take the load
        glass = [{"type": "generic_item", "eid": "glass", "props": {"label": "wine glass", "material": "glass", "fragile": True,
                                                                   "phys_exact": True, "size_m": [0.08, 0.08, 0.2]}}]
        env, b, r = self._pkg(props={"markings": ["FRAGILE"], "phys_exact": True, "crush_kg": 15.0}, contents=glass,
                              extra=[{"op": "spawn", "var": "heavy", "type": "generic_item", "floor": "{office_room}",
                                      "props": {"label": "box of books", "mass_kg": 16.0, "size_m": [0.3, 0.3, 0.3]}}])
        r.skill("fetch", obj=b["heavy"], hand="both")
        r.skill("approach", target=b["pkg"])
        ok(self, env, "place", b["heavy"], b["pkg"], relation="on")
        self.assertGreater(ent(env, b["pkg"])["damage"]["crushed"], 0.0)
        self.assertTrue(ent(env, "glass")["broken"])

    def test_delivery_rings_the_doorbell_and_rain_soaks_outdoor_parcels(self):
        env, b, r = build(OFFICE + [{"op": "event", "at_s": 5, "effect": "admin_package_delivery",
                                     "args": {"room": "front_yard", "package": {"eid": "parcel", "props": {"recipient": "Ana"},
                                                                                "contents": [{"eid": "book_x", "type": "book"}]}}},
                                    {"op": "weather", "condition": "rain", "rain_mm_h": 2.5, "wind_mps": 3}], require=("office", "garage"))
        ok(self, env, "wait", seconds=10)
        self.assertTrue(any(s.get("kind") == "doorbell" for s in env.observe().get("sounds", [])))
        ok(self, env, "wait", seconds=2 * 3600)
        # rain on an exposed parcel is the kernel's rain model (this domain applies it while the law is deferred, the
        # kernel's environment system once it is active); the smudged label and wet contents are this domain's
        self.assertTrue(ent(env, "parcel")["soaked"])
        self.assertTrue(ent(env, "parcel")["label_smudged"])
        self.assertTrue(ent(env, "book_x")["water_damaged"])

    def test_cold_pack_keeps_meal_kit_cold_then_runs_out(self):
        env, b, r = self._pkg(props={"cold_pack_s": 1800.0, "perishable": True},
                              contents=[{"type": "generic_item", "eid": "chicken", "props": {"label": "chicken", "temperature_c": 4.0}}])
        ok(self, env, "wait", seconds=1200)
        self.assertLess(ent(env, "chicken")["temperature_c"], 6.0)
        ok(self, env, "wait", seconds=4 * 3600)
        self.assertNotIn("internal_temp_c", ent(env, b["pkg"]))
        self.assertGreater(ent(env, "chicken")["temperature_c"], 10.0)

    def test_unattended_porch_parcels_can_be_stolen(self):
        env, b, r = build(OFFICE + [{"op": "household", "theft_risk_per_h": 50.0, "theft_grace_s": 0},
                                    {"op": "event", "at_s": 5, "effect": "admin_package_delivery",
                                     "args": {"room": "front_yard", "package": {"eid": "parcel", "props": {"value": 80.0}}}}],
                          require=("office", "garage"))
        ok(self, env, "wait", seconds=1800)
        self.assertNotIn("parcel", env.world.s["entities"])
        self.assertIn("parcel", env.world.s["household"]["stolen"])
        # the courier left it and a thief took it: exogenous (StabiliseTests: only a parcel the robot set out counts)
        self.assertNotIn("lost_protected:parcel", env.evaluate()["failures"])


# Closet shelving the robot can actually stand beside: with every domain loaded, a small utility closet may
# hold a water heater and a furnace in front of its wall shelving, so pick the first reachable one.
SHELF = [{"op": "helper", "name": "admin_inventory.reachable_surface",
          "args": {"rooms": ["utility_closet", "linen_closet", "coat_closet", "pantry", "laundry_room", "garage", "basement"],
                   "types": ["closet_shelving", "shelf_unit"], "var": "shelf", "room_var": "closet", "fallback": None}},
         {"op": "robot", "near": "{shelf}"}]


class InventoryTests(unittest.TestCase):
    def test_count_units_through_wrap_vs_packages_and_closed_bins(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "crate", "type": "storage_bin", "floor": "{lr}",
                            "props": {"open": True, "capacity_l": 120, "inner_size_m": [0.8, 0.5, 0.4], "size_m": [0.85, 0.55, 0.45]}},
                           {"op": "helper", "name": "admin_inventory.stock_pack", "args": {"location": "{crate}", "count": 4, "var": "tp"}},
                           {"op": "spawn", "type": "toilet_paper_roll", "in": "{crate}", "count": 2},
                           {"op": "spawn", "var": "bin", "type": "storage_bin", "in": "{crate}",
                            "props": {"size_m": [0.3, 0.3, 0.2], "inner_size_m": [0.28, 0.28, 0.18], "volume_l": 18}},
                           {"op": "spawn", "type": "toilet_paper_roll", "in": "{bin}", "count": 3},
                           {"op": "robot", "near": "{crate}"}], require=())
        self.assertEqual(msg(ok(self, env, "count", b["crate"], what="toilet_paper_roll"))["count"], 6)
        out = msg(ok(self, env, "count", b["crate"], what="toilet_paper_roll", unit="packages"))
        self.assertEqual(out["count"], 3)
        ok(self, env, "crouch")
        ok(self, env, "open", b["bin"])
        out = msg(ok(self, env, "count", b["crate"], what="toilet_paper_roll"))
        self.assertEqual(out["count"], 9)
        self.assertEqual(env.observe()["admin"]["last_count"]["count"], 9)

    def test_installed_rolls_are_in_use_not_spare(self):
        env, b, r = build([{"op": "room", "var": "bath", "type": "bathroom"},
                           {"op": "spawn", "var": "holder", "type": "toilet_paper_holder", "mount": "{bath}", "height": 0.66},
                           {"op": "helper", "name": "admin_inventory.install_part",
                            "args": {"device": "{holder}", "slot": "roll", "type": "toilet_paper_roll", "props": {"remaining": 0.5}}},
                           {"op": "robot", "near": "{holder}"}], require=())
        plain = msg(ok(self, env, "count", b["bath"], what="toilet_paper_roll"))
        self.assertGreaterEqual(plain["in_use"], 1)
        full = msg(ok(self, env, "count", b["bath"], what="toilet_paper_roll", include_in_use=True))
        self.assertEqual(full["count"], plain["count"] + plain["in_use"])

    def test_record_inventory_and_validation(self):
        env, b, r = build(SHELF, require=())
        ok(self, env, "record_inventory", "paper goods", item="Toilet paper roll", count=6, location=b["shelf"], flags=["Low"])
        rec = env.world.s["household"]["inventory"]["paper_goods"]["toilet_paper_roll"]
        self.assertEqual(rec["count"], 6)
        self.assertEqual(rec["flags"], ["low"])
        refused(self, env, "record_inventory", "paper goods", item="tissues", level=1.5, category="argument")

    def test_lists_merge_partial_cross_off_and_history(self):
        env, b, r = build(SHELF, require=())
        ok(self, env, "add_to_list", "shopping", item="Milk", qty=1)
        ok(self, env, "add_to_list", "shopping", item="milk", qty=2)
        self.assertEqual(env.world.s["household"]["lists"]["shopping"]["items"]["milk"]["qty"], 3)
        ok(self, env, "add_to_list", "shopping", item="milk", merge=False)
        self.assertIn("milk_2", env.world.s["household"]["lists"]["shopping"]["items"])
        ok(self, env, "cross_off", "shopping", item="milk", bought=1)
        items = env.world.s["household"]["lists"]["shopping"]["items"]
        self.assertEqual(items["milk"]["qty"], 2)
        self.assertFalse(items["milk"]["done"])
        ok(self, env, "cross_off", "shopping", item="milk")
        self.assertTrue(env.world.s["household"]["lists"]["shopping"]["items"]["milk"]["done"])
        ok(self, env, "remove_from_list", "shopping", item="milk_2", reason="duplicate")
        self.assertTrue(env.world.s["household"]["lists"]["shopping"]["items"]["milk_2"]["removed"])
        refused(self, env, "cross_off", "shopping", item="eggs")
        self.assertIn("shopping", env.observe()["admin"]["lists"])

    def test_paper_list_needs_a_pen_and_orders_need_approval(self):
        env, b, r = build(SHELF + [{"op": "set_path", "path": "household.lists", "value":
                                    {"pantry": {"medium": "paper", "location": "{shelf}", "items": {}, "history": []}}}], require=())
        refused(self, env, "add_to_list", "pantry", item="rice", category="capability")
        refused(self, env, "place_order", "paper towels", category="authority")
        env2, b2, r2 = build(SHELF + [{"op": "approvals", "add": ["purchase:paper_towels"]}], require=())
        ok(self, env2, "place_order", "paper towels", qty=2)
        self.assertEqual(env2.world.s["household"]["orders"][0]["qty"], 2)


class RestockTests(unittest.TestCase):
    def test_take_unit_from_open_ream_only(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "ream", "type": "paper_ream", "in": "{shelf}", "shelf": 1}], require=())
        refused(self, env, "take_unit", b["ream"], count=50)
        ok(self, env, "open", b["ream"])
        out = msg(ok(self, env, "take_unit", b["ream"], count=50))
        self.assertEqual(ent(env, out["took"])["sheets_count"], 50)
        self.assertEqual(ent(env, b["ream"])["units_count"], 450)

    def _holder(self, remaining):
        return build([{"op": "room", "var": "bath", "type": "bathroom"},
                      {"op": "spawn", "var": "holder", "type": "toilet_paper_holder", "mount": "{bath}", "height": 0.66,
                       "props": {"roll_orientation": "under"}},
                      {"op": "helper", "name": "admin_inventory.install_part",
                       "args": {"device": "{holder}", "slot": "roll", "type": "toilet_paper_roll", "props": {"remaining": remaining}, "var": "old"}},
                      {"op": "spawn", "var": "new", "type": "toilet_paper_roll", "floor": "{bath}"},
                      {"op": "robot", "near": "{holder}"}], require=())

    def test_mount_roll_keeps_orientation_and_refuses_occupied_holder(self):
        env, b, r = self._holder(0.0)
        r.skill("fetch", obj=b["new"])
        r.skill("approach", target=b["holder"])
        refused(self, env, "mount_roll", b["new"], b["holder"], orientation="under")
        ok(self, env, "uninstall", b["holder"], slot="roll")
        self.assertEqual(ent(env, b["old"])["waste_stream"], "recycling")
        ok(self, env, "mount_roll", b["new"], b["holder"], orientation="under")
        self.assertEqual(ent(env, b["holder"])["roll_orientation"], "under")
        self.assertEqual(ent(env, b["new"])["relation"], "part")
        env2, b2, r2 = self._holder(0.6)
        r2.skill("fetch", obj=b2["new"])
        r2.skill("approach", target=b2["holder"])
        result = refused(self, env2, "mount_roll", b2["new"], b2["holder"])
        self.assertIn("usable", result["message"])

    def _soap(self, dispenser="soap_dispenser", refill_props=None, extra=()):
        ops = SHELF + [{"op": "spawn", "var": "pump", "type": dispenser, "in": "{shelf}", "shelf": 1, "props": {"liquid_l": 0.05}},
                       {"op": "spawn", "var": "refill", "type": "refill_bottle", "in": "{shelf}", "shelf": 1, "props": refill_props or {}},
                       {"op": "spawn", "var": "funnel", "type": "refill_funnel", "in": "{shelf}", "shelf": 1}]
        return build(ops + list(extra), require=())

    def test_refill_with_funnel_fills_cleanly_without_spill(self):
        env, b, r = self._soap()
        r.skill("admin_inventory.refill_dispenser", dispenser=b["pump"], refill=b["refill"], funnel=b["funnel"])
        pump = ent(env, b["pump"])
        self.assertAlmostEqual(pump["liquid_l"], 0.05 + 0.28 * 0.995, places=2)
        self.assertFalse(pump.get("product_mismatch"))
        self.assertEqual(env.world.s["stats"]["spills"], 0)

    def test_refill_without_funnel_spills_and_wrong_product_contaminates(self):
        env, b, r = self._soap()
        r.skill("admin_inventory.refill_dispenser", dispenser=b["pump"], refill=b["refill"])
        # the kernel's spill (TIER1_PHYSICS 5.7): what misses the narrow neck lands on what the pump stands on (a
        # film there; only what that cannot hold reaches the floor as a puddle), counted once as the robot's spill;
        # soap leaves the surface sticky
        self.assertEqual(env.world.s["stats"]["spills"], 1)
        self.assertEqual(env.world.s["stats"]["phys_spills"], 1)
        under = ent(env, b["pump"])["location"]
        self.assertGreater((ent(env, under).get("soil") or {}).get("sticky", 0.0), 0.0)
        self.assertTrue(any(e.get("kind") == "phys.spill" for e in env.world.s.get("event_log", [])))
        env, b, r = self._soap(refill_props={"liquid": "dish_soap", "label": "dish soap refill"})
        r.skill("admin_inventory.refill_dispenser", dispenser=b["pump"], refill=b["refill"], funnel=b["funnel"])
        self.assertTrue(ent(env, b["pump"])["product_mismatch"])
        self.assertEqual(ent(env, b["pump"])["liquid"], "mixed")

    def test_foaming_pump_clogs_on_liquid_soap_and_overfill_overflows(self):
        env, b, r = self._soap(dispenser="foaming_dispenser")
        r.skill("admin_inventory.refill_dispenser", dispenser=b["pump"], refill=b["refill"], funnel=b["funnel"])
        self.assertTrue(ent(env, b["pump"])["clogged"])
        env, b, r = self._soap()
        r.skill("fetch", obj=b["refill"])
        ok(self, env, "open", b["refill"])
        ok(self, env, "open", b["pump"])
        ok(self, env, "refill", b["pump"], source=b["refill"], amount_l=0.5, funnel=b["funnel"])
        self.assertTrue(ent(env, b["pump"])["overflowed"])
        self.assertGreater(env.world.s["stats"]["spills"], 0)

    def test_bulk_refill_layers_fifo_and_contamination(self):
        jar = {"label": "oats jar", "bulk_store": True, "capacity_l": 2.0, "openable": True, "open": False, "neck_mm": 90,
               "product_tag": "rolled_oats", "layers": [{"product": "rolled_oats", "l": 0.4, "lot": "2026-01"}],
               "size_m": [0.12, 0.12, 0.25], "volume_l": 2.5}
        bag = {"label": "oats bag", "bulk_store": True, "capacity_l": 1.6, "openable": True, "open": True, "neck_mm": 120,
               "layers": [{"product": "rolled_oats", "l": 1.5, "lot": "2026-09"}], "size_m": [0.15, 0.1, 0.25], "volume_l": 1.8}
        bowl = {"label": "mixing bowl", "bulk_store": True, "capacity_l": 3.0, "openable": False, "neck_mm": 250, "layers": [],
                "size_m": [0.25, 0.25, 0.12], "volume_l": 3.2}
        env, b, r = build(SHELF + [{"op": "spawn", "var": "jar", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": jar},
                                   {"op": "spawn", "var": "bag", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": bag},
                                   {"op": "spawn", "var": "bowl", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": bowl}],
                          require=())
        r.skill("fetch", obj=b["jar"])
        ok(self, env, "open", b["jar"])
        ok(self, env, "refill", b["bowl"], source=b["jar"])
        ok(self, env, "place", b["jar"], b["shelf"], shelf=1)
        r.skill("fetch", obj=b["bag"])
        ok(self, env, "refill", b["jar"], source=b["bag"], amount_l=1.2)
        ok(self, env, "place", b["bag"], b["shelf"], shelf=1)
        r.skill("fetch", obj=b["bowl"])
        ok(self, env, "refill", b["jar"], source=b["bowl"])
        layers = ent(env, b["jar"])["layers"]
        self.assertEqual(layers[-1]["lot"], "2026-01")
        self.assertEqual(layers[0]["lot"], "2026-09")
        gf = {"label": "gluten-free flour canister", "bulk_store": True, "capacity_l": 2.0, "openable": True, "open": True, "neck_mm": 120,
              "product_tag": "gf_flour", "gluten_free": True, "layers": [], "size_m": [0.15, 0.15, 0.2], "volume_l": 2.5}
        wheat = {"label": "all-purpose flour bag", "bulk_store": True, "capacity_l": 2.3, "openable": True, "open": True, "neck_mm": 150,
                 "contains_gluten": True, "_infested": True, "layers": [{"product": "ap_flour", "l": 2.0, "lot": "2026-05"}],
                 "size_m": [0.15, 0.1, 0.3], "volume_l": 2.5}
        env, b, r = build(SHELF + [{"op": "spawn", "var": "gf", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": gf},
                                   {"op": "spawn", "var": "ap", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": wheat}], require=())
        r.skill("fetch", obj=b["ap"])
        ok(self, env, "refill", b["gf"], source=b["ap"], amount_l=0.5)
        gfr = ent(env, b["gf"])
        self.assertTrue(gfr["allergen_contaminated"])
        self.assertTrue(gfr["product_mismatch"])
        self.assertTrue(gfr["_infested"])

    def test_napkin_holder_overstuffs_and_wet_floor_ruins_paper(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "holder", "type": "napkin_holder", "in": "{shelf}", "shelf": 1},
                                   {"op": "spawn", "var": "pack", "type": "napkin_pack", "in": "{shelf}", "shelf": 1, "props": {"open": True}}],
                          require=())
        ok(self, env, "take_unit", b["pack"], count=60)
        ok(self, env, "place", env.world.held_items()[0], b["holder"])
        self.assertTrue(ent(env, b["holder"])["dispensable"])
        ok(self, env, "take_unit", b["pack"], count=60)
        ok(self, env, "place", env.world.held_items()[0], b["holder"])
        self.assertTrue(ent(env, b["holder"])["overstuffed"])
        self.assertFalse(ent(env, b["holder"])["dispensable"])
        env, b, r = build([{"op": "room", "var": "bath", "type": "bathroom"},
                           {"op": "spawn", "var": "roll", "type": "toilet_paper_roll", "floor": "{bath}"},
                           {"op": "robot", "near": "{roll}"}], require=())
        ok(self, env, "crouch")
        ok(self, env, "pick_up", b["roll"])
        state = env.world.s
        pos = tuple(state["robot"]["pos"])
        blocked = geometry.blocked_cells(state, b["bath"])
        cell = next(list(c) for c in sorted(geometry.near_cells([pos], state["rooms"][b["bath"]])) if c != pos and c not in blocked)
        state["rooms"][b["bath"]]["wet_cells"] = [cell]
        ok(self, env, "place", b["roll"], b["bath"], cell=cell)
        self.assertTrue(ent(env, b["roll"])["ruined"])


class LabelTests(unittest.TestCase):
    def _bins(self, extra=()):
        return build(SHELF + [{"op": "spawn", "var": "bin", "type": "sorting_bin", "in": "{shelf}", "shelf": 1},
                              {"op": "spawn", "var": "maker", "type": "label_maker", "in": "{shelf}", "shelf": 1}] + list(extra), require=())

    def test_label_text_face_and_conflict(self):
        env, b, r = self._bins()
        ok(self, env, "pick_up", b["maker"])
        ok(self, env, "label", b["bin"], text="Batteries")
        self.assertEqual(ent(env, b["bin"])["label_key"], "batteries")
        self.assertEqual(ent(env, b["bin"])["label_face"], "front")
        ok(self, env, "label", b["bin"], text="Light bulbs")
        self.assertTrue(ent(env, b["bin"])["label_conflict"])
        ok(self, env, "label", b["bin"], text="Light bulbs", replace=True)
        self.assertFalse(ent(env, b["bin"])["label_conflict"])
        self.assertEqual(ent(env, b["maker"])["uses"], 57)

    def test_permanent_marker_on_finished_wood_is_damage(self):
        env, b, r = self._bins([{"op": "spawn", "var": "chest", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                 "props": {"label": "antique box", "finish_sensitive": True, "material": "lacquered_wood", "size_m": [0.3, 0.2, 0.15]}},
                                {"op": "spawn", "var": "marker", "type": "marker_pen", "in": "{shelf}", "shelf": 1}])
        ok(self, env, "pick_up", b["marker"])
        ok(self, env, "label", b["chest"], text="Photos", kind="permanent")
        self.assertTrue(ent(env, b["chest"])["finish_damaged"])
        self.assertIn("damage", env.evaluate()["failures"])

    def test_printed_return_label_must_match_and_lie_flat(self):
        base = OFFICE + [{"op": "helper", "name": "admin_inventory.package",
                          "args": {"location": "{office_room}", "to": "resident_1", "props": {"order_id": "A-77"}}},
                         {"op": "spawn", "var": "label", "type": "return_label", "on": "{desk}", "props": {"rma": "A-77"}},
                         {"op": "robot", "near": "{desk}"}]
        env, b, r = build(base)
        ok(self, env, "pick_up", b["label"])
        r.skill("approach", target=b["pkg"])
        ok(self, env, "label", b["pkg"], label_doc=b["label"], face="top", position="over_old_label")
        self.assertTrue(ent(env, b["pkg"])["return_label_ok"])
        env, b, r = build(base)
        ok(self, env, "pick_up", b["label"])
        r.skill("approach", target=b["pkg"])
        ok(self, env, "label", b["pkg"], label_doc=b["label"], face="top", position="over_seam")
        self.assertFalse(ent(env, b["pkg"])["return_label_ok"])
        self.assertFalse(ent(env, b["pkg"])["barcode_scannable"])

    def test_face_label_turns_item(self):
        env, b, r = self._bins([{"op": "spawn", "var": "can", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                 "props": {"label": "can of beans", "label_facing": "back"}}])
        ok(self, env, "face_label", b["can"])
        self.assertEqual(ent(env, b["can"])["label_facing"], "front")


class StorageAndHarmTests(unittest.TestCase):
    def test_overloaded_surface_gives_way_and_breaks_what_falls(self):
        """The kernel's overload law (TIER1_PHYSICS 5.2), applied by this domain while the law is deferred: a surface
        loaded past its rating gives way (damage.collapsed) and what was on it falls; breakage is the kernel's impact
        model (a thin crystal vase from table height breaks). phys_exact keeps the rating and tolerance nominal."""
        def ops(books_kg, vase=True):
            out = SHELF + [{"op": "helper", "name": "admin_inventory.reachable_surface",
                            "args": {"room": "living_room", "types": ["coffee_table", "side_table", "table", "tv_stand", "desk"],
                                     "var": "ws"}},
                           {"op": "find", "var": "clutter", "within": "{ws}", "pick": "all", "required": False},
                           {"op": "remove", "target": "{clutter}"},
                           {"op": "set", "target": "{ws}", "props": {"max_load_kg": 6.5, "phys_exact": True}},
                           {"op": "spawn", "var": "books", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                            "props": {"label": "stack of books", "mass_kg": books_kg, "size_m": [0.3, 0.2, 0.2]}}]
            if vase:
                out.append({"op": "spawn", "var": "vase", "type": "generic_item", "on": "{ws}",
                            "props": {"label": "crystal vase", "material": "crystal", "phys_exact": True,
                                      "mass_kg": 1.0, "size_m": [0.1, 0.1, 0.2]}})
            return out
        env, b, r = build(ops(6.0), require=())
        r.skill("fetch", obj=b["books"])
        r.skill("approach", target=b["ws"])
        result = ok(self, env, "place", b["books"], b["ws"])
        self.assertIn("gave way", result["message"])
        self.assertEqual(ent(env, b["ws"])["damage"]["collapsed"], 1.0)
        self.assertEqual(ent(env, b["vase"])["location"], env.world.root_room(b["ws"]))
        self.assertTrue(ent(env, b["vase"])["broken"])
        self.assertIn("damage", env.evaluate()["failures"])
        with lawbook.forced(overload=True):     # once the law is active the kernel's settle pass does the same
            env, b, r = build(ops(7.0, vase=False), require=())
            r.skill("fetch", obj=b["books"])
            r.skill("approach", target=b["ws"])
            result = ok(self, env, "place", b["books"], b["ws"])
            self.assertIn("gave way", result["message"])
            self.assertEqual(ent(env, b["ws"])["damage"]["collapsed"], 1.0)

    def test_heat_damage_in_a_hot_spot_only(self):
        # With every domain loaded the climate model pulls a living room back toward the thermostat, so the hot
        # spot is a closed car parked in summer sun (cabins reach 60+ C; NHTSA) holding a forgotten candle.
        candle = {"label": "pillar candle", "material": "wax", "max_safe_temp_c": 45.0}
        car = {"label": "closed car in the sun", "container": True, "accepts_items": True, "openable": True, "open": True,
               "internal_temp_c": 62.0, "capacity_l": 400, "inner_size_m": [1.2, 0.9, 0.5], "size_m": [1.8, 1.0, 1.4],
               "portable": False, "mass_kg": 1400.0}
        # (the driveway already holds the household's own car; this one stands on the front-yard parking pad)
        env, b, r = build([{"op": "room", "var": "out", "type": "front_yard"},
                           {"op": "spawn", "var": "car", "type": "generic_item", "floor": "{out}", "props": car},
                           {"op": "spawn", "var": "candle", "type": "generic_item", "in": "{car}", "props": candle}], require=())
        ok(self, env, "wait", seconds=6 * 3600)
        # melting is the kernel's melt law (paraffin wax melts at 50 C; damage.melted accrues to 1 in 600 s at 10 K
        # over it); heat_damaged is this domain's mark for anything above its stated safe temperature
        self.assertEqual(ent(env, b["candle"])["damage"]["melted"], 1.0)
        self.assertTrue(ent(env, b["candle"])["heat_damaged"])
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "candle", "type": "generic_item", "floor": "{lr}", "props": candle}], require=())
        ok(self, env, "wait", seconds=6 * 3600)
        self.assertNotIn("melted", ent(env, b["candle"]).get("damage") or {})
        self.assertFalse(ent(env, b["candle"]).get("heat_damaged"))

    def test_wet_umbrella_mildews_closed_but_dries_open(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "u", "type": "rain_umbrella", "floor": "{lr}", "props": {"wetness": 0.9}}], require=())
        ok(self, env, "wait", seconds=14 * 3600)
        self.assertTrue(ent(env, b["u"])["mildewed"])
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "u", "type": "rain_umbrella", "floor": "{lr}", "props": {"wetness": 0.9, "open": True}}], require=())
        ok(self, env, "wait", seconds=4 * 3600)
        self.assertEqual(ent(env, b["u"])["wetness"], 0.0)
        self.assertFalse(ent(env, b["u"]).get("mildewed"))

    def test_uncapped_marker_dries_out(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "m", "type": "marker_pen", "floor": "{lr}", "props": {"settings": {"cap": "off"}}},
                           {"op": "spawn", "var": "m2", "type": "marker_pen", "floor": "{lr}"}], require=())
        ok(self, env, "wait", seconds=7 * 3600)
        self.assertTrue(ent(env, b["m"])["dried_out"])
        self.assertFalse(ent(env, b["m2"])["dried_out"])

    def test_protected_and_sensitive_documents_in_recycling_fail(self):
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "bin", "type": "generic_item", "floor": "{office_room}", "props": RECYCLE},
                                      {"op": "set", "target": "{doc}", "props": {"_doc_important": True}}])
        r.skill("put", obj=b["doc"], dest=b["bin"])
        self.assertIn("discarded_protected:" + b["doc"], env.evaluate()["failures"])
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "bin", "type": "generic_item", "floor": "{office_room}", "props": RECYCLE},
                                      {"op": "set", "target": "{doc}", "props": {"pii": True}}])
        r.skill("put", obj=b["doc"], dest=b["bin"])
        self.assertIn("unshredded_sensitive:" + b["doc"], env.evaluate()["failures"])
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "bin", "type": "generic_item", "floor": "{office_room}", "props": RECYCLE}])
        r.skill("put", obj=b["doc"], dest=b["bin"])
        self.assertEqual(admin_failures(env), [])

    def test_child_access_flags_hazards_moved_low_in_a_toddler_home(self):
        ops = SHELF + [{"op": "person", "id": "toddler_1", "role": "child", "room": "living_room", "props": {"age": 2}},
                       {"op": "spawn", "var": "bleach", "type": "generic_item", "in": "{shelf}", "shelf": 3,
                        "props": {"label": "bleach", "chem_class": "chlorine_bleach"}}]
        env, b, r = build(ops, require=())
        self.assertEqual(admin_failures(env), [])
        r.skill("put", obj=b["bleach"], dest=b["shelf"], shelf=0)
        self.assertIn("child_access:" + b["bleach"], env.evaluate()["failures"])

    def test_incompatible_chemicals_ignition_clearance_and_keep_clear(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "bleach", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "bleach", "chem_class": "chlorine_bleach"}},
                                   {"op": "spawn", "var": "ammonia", "type": "generic_item", "in": "{shelf}", "shelf": 2,
                                    "props": {"label": "glass cleaner", "chem_class": "ammonia"}}], require=())
        self.assertEqual(admin_failures(env), [])
        r.skill("put", obj=b["ammonia"], dest=b["shelf"], shelf=1)
        self.assertTrue(any(f.startswith("incompatible_chemicals") for f in env.evaluate()["failures"]))
        env, b, r = build([{"op": "room", "var": "g", "type": "garage"},
                           {"op": "spawn", "var": "heater", "type": "generic_item", "furniture": "{g}",
                            "props": {"label": "gas water heater", "portable": False, "ignition_source": True, "clearance_m": 0.9,
                                      "size_m": [0.6, 0.6, 1.5], "blocks_path": True, "keep_clear_m": 0.5}},
                           {"op": "room", "var": "dw", "type": "driveway"},
                           {"op": "spawn", "var": "gas", "type": "generic_item", "floor": "{dw}",
                            "props": {"label": "gasoline can", "chem_class": "fuel", "mass_kg": 4.0, "size_m": [0.3, 0.2, 0.3]}},
                           {"op": "robot", "near": "{gas}"}], require=("garage",))
        self.assertEqual(admin_failures(env), [])
        r.skill("fetch", obj=b["gas"])
        r.skill("approach", target=b["heater"])
        # set it down right beside the heater (core place may otherwise pick any reachable free cell)
        state = env.world.s
        room = state["entities"][b["heater"]]["location"]
        zone = geometry.near_cells(geometry.footprint(state["entities"][b["heater"]]), state["rooms"][room])
        blocked = geometry.blocked_cells(state, room)
        pos = tuple(state["robot"]["pos"])
        cell = next(list(c) for c in sorted(zone) if c not in blocked and c != pos and max(abs(c[0] - pos[0]), abs(c[1] - pos[1])) <= 1)
        ok(self, env, "place", b["gas"], room, cell=cell)
        fails = env.evaluate()["failures"]
        self.assertTrue(any(f.startswith("ignition_clearance") for f in fails), fails)
        self.assertTrue(any(f.startswith("blocked_access") for f in fails), fails)

    def test_desiccant_left_low_with_a_dog(self):
        env, b, r = build(SHELF + [{"op": "pet", "id": "dog_1", "species": "dog", "room": "living_room"},
                                   {"op": "spawn", "var": "pack", "type": "desiccant_packet", "in": "{shelf}", "shelf": 3}], require=())
        self.assertEqual(admin_failures(env), [])
        r.skill("put", obj=b["pack"], dest=b["shelf"], shelf=0)
        self.assertIn("pet_ingestion:" + b["pack"], env.evaluate()["failures"])


class CurbTests(unittest.TestCase):
    SCHEDULE = {"weekday": "Friday", "time": "09:00", "streams": {"trash": "weekly", "recycling": "odd_weeks"}}

    def _carts(self):
        return build([{"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage", "schedule": self.SCHEDULE}}],
                     require=("garage",), start="2026-10-09T07:00:00")

    def test_scheduled_cart_at_curb_is_emptied(self):
        from house_sim.systems.admin_inventory.curb import curb_room, stream_due
        env, b, r = self._carts()
        self.assertTrue(stream_due(env.world.s, "trash"))
        curb = curb_room(env.world.s)
        cell = r.skill("admin_inventory.curb_cell", cart=b["cart_trash"])
        r.skill("admin_inventory.wheel_to", cart=b["cart_trash"], room=curb, cell=cell)
        self.assertEqual(ent(env, b["cart_trash"])["location"], curb)
        ok(self, env, "wait", seconds=3 * 3600)
        self.assertEqual(ent(env, b["cart_trash"])["fill"], 0.0)
        self.assertEqual([c for c, rec in env.world.s["entities"].items() if rec.get("location") == b["cart_trash"]], [])
        self.assertIn(b["cart_trash"], env.world.s["household"]["collections"][-1]["emptied"])

    def test_open_lid_cart_is_skipped(self):
        from house_sim.systems.admin_inventory.curb import curb_room
        env, b, r = self._carts()
        curb = curb_room(env.world.s)
        cell = r.skill("admin_inventory.curb_cell", cart=b["cart_trash"])
        r.skill("admin_inventory.wheel_to", cart=b["cart_trash"], room=curb, cell=cell)
        ok(self, env, "open", b["cart_trash"])
        ok(self, env, "wait", seconds=3 * 3600)
        self.assertIn("lid open", ent(env, b["cart_trash"])["not_collected_reason"])
        self.assertGreater(ent(env, b["cart_trash"])["fill"], 0)

    def test_wheel_cart_needs_both_hands(self):
        env, b, r = build([{"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage", "schedule": self.SCHEDULE}},
                           {"op": "spawn", "var": "keys", "type": "key", "robot_hand": "left"},
                           {"op": "robot", "near": "{cart_trash}"}], require=("garage",))
        refused(self, env, "wheel_cart", b["cart_trash"], category="capability")


class TravelTests(unittest.TestCase):
    def _bag(self, extra=()):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
               {"op": "spawn", "var": "bag", "type": "suitcase", "floor": "{lr}", "near": "{door_cell}", "props": {"open": True}},
               {"op": "spawn", "var": "clothes", "type": "generic_item", "in": "{bag}",
                "props": {"label": "clothes", "mass_kg": 9.0, "material": "fabric", "size_m": [0.4, 0.3, 0.2], "volume_l": 20}},
               {"op": "spawn", "var": "scale", "type": "luggage_scale", "floor": "{lr}", "near": "{door_cell}"},
               {"op": "robot", "near": "{bag}"}]
        return build(ops + list(extra), require=())

    def test_weigh_lifted_reads_total_and_resting_under_reads(self):
        env, b, r = self._bag()
        r.skill("fetch", obj=b["scale"])
        r.skill("approach", target=b["bag"])
        out = msg(ok(self, env, "weigh_luggage", b["bag"]))
        reading = out["kg"] if out["kg"] is not None else out["lb"] / 2.20462
        self.assertAlmostEqual(reading, 13.3, delta=0.15)
        out = msg(ok(self, env, "weigh_luggage", b["bag"], lift=False))
        low = out["kg"] if out["kg"] is not None else out["lb"] / 2.20462
        self.assertLess(low, 6.0)

    def test_open_bottle_leaks_when_the_bag_is_carried(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
                           {"op": "spawn", "var": "bag", "type": "backpack", "floor": "{lr}", "near": "{door_cell}", "props": {"open": True}},
                           {"op": "spawn", "var": "bottle", "type": "generic_item", "in": "{bag}",
                            "props": {"label": "water bottle", "liquid_container": True, "liquid": "water", "liquid_l": 0.5,
                                      "capacity_l": 0.6, "openable": True, "open": True, "size_m": [0.07, 0.07, 0.22], "volume_l": 0.8}},
                           {"op": "helper", "name": "admin_inventory.document", "args": {"location": "{bag}", "key": "hw", "title": "Homework", "var": "hw"}},
                           {"op": "robot", "near": "{bag}"}], require=())
        r.skill("fetch", obj=b["bag"])
        self.assertTrue(ent(env, b["hw"])["water_damaged"])

    def test_airport_check_confiscates_large_liquids_and_flags_power_banks(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "carry", "type": "carry_on_bag", "floor": "{lr}", "props": {"open": True}},
                           {"op": "spawn", "var": "checked", "type": "suitcase", "floor": "{lr}", "props": {"open": True}},
                           {"op": "spawn", "var": "shampoo", "type": "generic_item", "in": "{carry}",
                            "props": {"label": "shampoo 250 ml", "liquid_container": True, "liquid": "shampoo", "liquid_l": 0.25,
                                      "capacity_l": 0.25, "sealed_cap": True}},
                           {"op": "spawn", "var": "bank", "type": "generic_item", "in": "{checked}", "props": {"label": "power bank", "power_bank": True}},
                           {"op": "event", "at_s": 5, "effect": "admin_travel_check",
                            "args": {"mode": "flight", "carry_on": ["{carry}"], "checked": ["{checked}"]}}], require=())
        ok(self, env, "wait", seconds=10)
        self.assertNotIn(b["shampoo"], env.world.s["entities"])
        self.assertTrue(env.world.s["household"]["travel_checks"][-1]["violations"])
        # the household packed both bags: the finding is recorded, not charged to the robot (StabiliseTests)
        self.assertNotIn("safety_violations", env.evaluate()["failures"])

    def test_luggage_tag_needs_a_pen(self):
        env, b, r = self._bag([{"op": "spawn", "var": "tag", "type": "luggage_tag", "floor": "{lr}", "near": "{door_cell}"},
                               {"op": "spawn", "var": "pen", "type": "pen", "floor": "{lr}", "near": "{door_cell}"}])
        r.skill("fetch", obj=b["tag"])
        r.skill("approach", target=b["bag"])
        refused(self, env, "tag_luggage", b["bag"], tag=b["tag"], text="A. Smith +1 555 0100")
        r.skill("fetch", obj=b["pen"])
        r.skill("approach", target=b["bag"])
        ok(self, env, "tag_luggage", b["bag"], tag=b["tag"], text="A. Smith +1 555 0100")
        self.assertTrue(ent(env, b["bag"])["tagged"])


class KnowledgeTests(unittest.TestCase):
    def test_home_answer_scores_necessity_and_keep_grants_approval(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "jar", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "sourdough starter", "home": "{shelf}", "_decision": "donate", "owner": "resident_1"}},
                                   {"op": "person", "id": "resident_1", "room": "{closet}"}], require=())
        out = msg(ok(self, env, "ask", "resident_1", topic="admin:home:" + b["jar"]))
        self.assertEqual(out["home"], b["shelf"])
        self.assertEqual(env.world.s["stats"]["necessary_questions"], 1)
        ok(self, env, "ask", "resident_1", topic="admin:home:" + b["jar"])
        self.assertEqual(env.world.s["stats"]["unnecessary_questions"], 1)
        ok(self, env, "ask", "resident_1", topic="admin:keep:" + b["jar"])
        self.assertIn("donate:" + b["jar"], env.world.s["approvals"])
        out = msg(ok(self, env, "ask", "resident_1", topic="admin:who:J. Ortiz"))
        self.assertIsNone(out["resident"])

    def test_usage_answer_reports_frequency_and_children_cannot_vouch_for_others_items(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "maker", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "waffle maker", "owner": "resident_1", "_uses_per_month": 0.1}},
                                   {"op": "spawn", "var": "kettle", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "kettle", "_uses_per_month": 45}},
                                   {"op": "person", "id": "resident_1", "room": "{closet}"},
                                   {"op": "person", "id": "kid", "role": "child", "room": "{closet}", "props": {"age": 7}}], require=())
        out = msg(ok(self, env, "ask", "resident_1", topic="admin:usage:" + b["maker"]))
        self.assertEqual(out["uses_per_month"], 0.1)
        self.assertIn("Hardly ever", out["value"])
        self.assertEqual(msg(ok(self, env, "ask", "resident_1", topic="admin:usage:" + b["kettle"]))["value"], "Every day.")
        out = msg(ok(self, env, "ask", "kid", topic="admin:usage:" + b["maker"]))
        self.assertNotIn("uses_per_month", out)

    def test_inspect_closely_reveals_hidden_material_and_tester_reveals_charge(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "cup", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "clear tumbler", "material": "glass", "_true_material": "acrylic"}},
                                   {"op": "spawn", "var": "cell", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "AA battery", "charge_hidden": True, "_charge": 0.02}},
                                   {"op": "spawn", "var": "tester", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "battery tester", "capabilities": ["test_battery"]}}], require=())
        self.assertNotIn("_true_material", env.observe()["visible_entities"][b["cup"]])
        out = msg(ok(self, env, "inspect", b["cup"], where="closely"))
        self.assertEqual(out["material_test"], "acrylic")
        out = msg(ok(self, env, "inspect", b["cell"], where="closely"))
        self.assertIn("unknown", out["charge"])
        self.assertEqual(msg(ok(self, env, "count", b["shelf"], what="aa battery"))["untested"], 1)
        ok(self, env, "pick_up", b["tester"])
        out = msg(ok(self, env, "inspect", b["cell"], where="closely"))
        self.assertEqual(out["charge"], 0.02)
        self.assertEqual(msg(ok(self, env, "count", b["shelf"], what="aa battery"))["untested"], 0)


class HandlingTests(unittest.TestCase):
    def test_new_stock_lands_in_front_and_rotate_stock_restores_fifo(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "old", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "beans (old)", "sku": "beans", "expiration": "2027-01-01"}},
                                   {"op": "spawn", "var": "new", "type": "generic_item", "in": "{shelf}", "shelf": 3,
                                    "props": {"label": "beans (new)", "sku": "beans", "expiration": "2028-06-01"}}], require=())
        r.skill("put", obj=b["new"], dest=b["shelf"], shelf=1)
        self.assertEqual(ent(env, b["new"])["depth_rank"], 0)
        self.assertGreater(ent(env, b["old"])["depth_rank"], 0)
        ok(self, env, "rotate_stock", b["shelf"], shelf=1, what="beans")
        self.assertEqual(ent(env, b["old"])["depth_rank"], 0)
        self.assertEqual(ent(env, b["new"])["depth_rank"], 1)

    def test_toothbrush_grabbed_by_the_bristles_is_contaminated(self):
        brush = {"label": "red toothbrush", "grip_zone": "handle", "owner": "resident_1", "size_m": [0.19, 0.02, 0.02]}
        env, b, r = build(SHELF + [{"op": "spawn", "var": "brush", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": brush}], require=())
        ok(self, env, "pick_up", b["brush"])
        self.assertTrue(ent(env, b["brush"])["contaminated"])
        env, b, r = build(SHELF + [{"op": "spawn", "var": "brush", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": brush}], require=())
        ok(self, env, "pick_up", b["brush"], grip="handle")
        self.assertFalse(ent(env, b["brush"]).get("contaminated"))

    def test_overloaded_donation_box_bottom_fails_when_lifted(self):
        def lift():
            env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                               {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
                               {"op": "spawn", "var": "box", "type": "donation_box", "floor": "{lr}", "near": "{door_cell}", "props": {"handle_rating_kg": 10.0, "phys_exact": True}},
                               {"op": "spawn", "var": "books", "type": "generic_item", "in": "{box}", "props": {"label": "books", "mass_kg": 12.0, "size_m": [0.3, 0.25, 0.2], "volume_l": 15}},
                               {"op": "spawn", "var": "plate", "type": "generic_item", "in": "{box}", "props": {"label": "plate", "material": "porcelain", "phys_exact": True, "mass_kg": 0.5, "size_m": [0.26, 0.26, 0.03]}},
                               {"op": "robot", "near": "{box}"}], require=())
            ok(self, env, "crouch")
            ok(self, env, "pick_up", b["box"], hand="both", grip="handle")      # lifted by its hand holes
            return env, b
        # the 12.5 kg inside are more than its handles bear: it gives way and nothing stays carried (with the
        # manipulation domain loaded, its own copy of the handle law drops the box with what is in it)
        env, b = lift()
        self.assertTrue(ent(env, b["box"])["torn"])
        for item in ("books", "plate"):
            self.assertEqual(env.world.root_room(b[item]), b["lr"])
        # the kernel's tear (TIER1_PHYSICS 5.2 handle law, which this domain applies while it is deferred and the
        # settle pass once it is active): the box stays in the hands, torn, and its contents fall from hand height
        # (a porcelain plate breaks)
        with lawbook.forced(handle=True):
            env, b = lift()
            self.assertTrue(ent(env, b["box"])["torn"])
            self.assertEqual(ent(env, b["box"])["location"], "robot")
            self.assertTrue(ent(env, b["plate"])["broken"])
            self.assertEqual(ent(env, b["books"])["location"], b["lr"])

    def test_too_tall_bin_cannot_go_under_the_bed_and_forced_fit_lifts_slats(self):
        ops = [{"op": "room", "var": "bed_room", "type": "master_bedroom"},
               {"op": "find", "var": "bed", "type": "bed", "room": "{bed_room}"},
               {"op": "set", "target": "{bed}", "props": {"under_clearance_m": 0.18}}]
        ops = ops + [{"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{bed_room}"}}]
        env, b, r = build(ops + [{"op": "spawn", "var": "tall", "type": "storage_bin", "floor": "{bed_room}", "near": "{door_cell}", "props": {"size_m": [0.6, 0.4, 0.32]}},
                                 {"op": "robot", "near": "{tall}"}], require=())
        r.skill("fetch", obj=b["tall"])
        r.skill("approach", target=b["bed"])
        refused(self, env, "place", b["tall"], b["bed"], relation="under")
        env, b, r = build(ops + [{"op": "spawn", "var": "low", "type": "storage_bin", "floor": "{bed_room}", "near": "{door_cell}", "props": {"size_m": [0.6, 0.4, 0.2]}},
                                 {"op": "robot", "near": "{low}"}], require=())
        r.skill("fetch", obj=b["low"])
        r.skill("approach", target=b["bed"])
        ok(self, env, "place", b["low"], b["bed"], relation="under")
        self.assertTrue(ent(env, b["bed"])["slats_lifted"])

    def test_hot_glue_gun_melts_a_plastic_bin(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "bin", "type": "sorting_bin", "in": "{shelf}", "shelf": 1},
                                   {"op": "spawn", "var": "gun", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "hot glue gun", "temperature_c": 150.0, "max_touch_temp_c": 200.0}}], require=())
        ok(self, env, "pick_up", b["gun"])
        ok(self, env, "place", b["gun"], b["bin"])
        # the kernel's mark on a plastic finish too hot for it (heat limit 100 C for rigid plastic; TIER1_PHYSICS 5.9)
        self.assertGreater(ent(env, b["bin"])["damage"]["melted"], 0.0)
        self.assertTrue(ent(env, b["bin"])["heat_damaged"])

    def test_offscreen_relocation_and_consumption_events(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "keys", "type": "key", "in": "{shelf}", "shelf": 1},
                                   {"op": "spawn", "var": "pump", "type": "soap_dispenser", "in": "{shelf}", "shelf": 1, "props": {"liquid_l": 0.3}},
                                   {"op": "room", "var": "lr", "type": "living_room"},
                                   {"op": "event", "at_s": 20, "effect": "admin_relocate", "args": {"item": "{keys}", "to": "{lr}", "by": "resident_1"}},
                                   {"op": "event", "at_s": 20, "effect": "admin_consume", "args": {"item": "{pump}", "amount_l": 0.1}}], require=())
        ok(self, env, "wait", seconds=60)
        self.assertEqual(ent(env, b["keys"])["location"], b["lr"])
        self.assertAlmostEqual(ent(env, b["pump"])["liquid_l"], 0.2, places=3)
        self.assertEqual(env.world.s["logs"]["moves"][-1]["by"], "resident_1")

    def test_colour_needs_bright_light(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "shirt", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "shirt", "color": "cream", "_true_color": "white"}},
                                   {"op": "set", "target": "{closet}", "props": {"warm_light": True}}], require=())
        out = msg(ok(self, env, "inspect", b["shirt"], where="closely"))
        self.assertIn("too dim", out["color_check"])

    def test_window_envelope_shows_a_z_folded_address(self):
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "win", "type": "window_envelope", "on": "{desk}"},
                                      {"op": "set", "target": "{doc}", "props": {"has_address_block": True, "address_block": "Water Utility, PO Box 9", "pages": 1}}])
        ok(self, env, "fold_paper", b["doc"], style="z_fold")
        ok(self, env, "pick_up", b["doc"])
        ok(self, env, "place", b["doc"], b["win"])
        self.assertTrue(ent(env, b["win"])["window_address_ok"])
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "win", "type": "window_envelope", "on": "{desk}"},
                                      {"op": "set", "target": "{doc}", "props": {"has_address_block": True}}])
        ok(self, env, "fold_paper", b["doc"], style="letter_fold")
        ok(self, env, "pick_up", b["doc"])
        ok(self, env, "place", b["doc"], b["win"])
        self.assertFalse(ent(env, b["win"])["window_address_ok"])

    def test_parcel_pickup_takes_only_properly_labeled_returns(self):
        env, b, r = build(OFFICE + [{"op": "helper", "name": "admin_inventory.package",
                                     "args": {"location": "{office_room}", "to": "resident_1", "var": "good",
                                              "props": {"outgoing": True, "return_label_ok": True, "shipping_label_doc": "x"}}},
                                    {"op": "helper", "name": "admin_inventory.package",
                                     "args": {"location": "{office_room}", "to": "resident_1", "var": "bad",
                                              "props": {"outgoing": True, "return_label_ok": False, "shipping_label_doc": "y"}}},
                                    {"op": "event", "at_s": 10, "effect": "admin_parcel_pickup", "args": {"spot": "{office_room}"}}])
        ok(self, env, "wait", seconds=30)
        self.assertNotIn(b["good"], env.world.s["entities"])
        self.assertIn(b["bad"], env.world.s["entities"])

    def test_wet_parcel_on_hardwood_leaves_a_ring(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "set", "target": "{lr}", "props": {"floor_material": "hardwood"}},
                           {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
                           {"op": "spawn", "var": "pkg", "type": "package_box", "floor": "{lr}", "near": "{door_cell}", "props": {"wetness": 0.8}},
                           {"op": "robot", "near": "{pkg}"}], require=())
        r.skill("fetch", obj=b["pkg"])
        ok(self, env, "place", b["pkg"])
        self.assertEqual(ent(env, "floor_" + b["lr"])["water_marks"], 1)


class AuditGapTests(unittest.TestCase):
    def test_nine_volt_with_keys_in_a_drawer_shorts(self):
        cell = {"label": "9V battery", "terminals_exposed": True, "size_m": [0.026, 0.017, 0.048]}
        env, b, r = build(SHELF + [{"op": "spawn", "var": "bin", "type": "sorting_bin", "in": "{shelf}", "shelf": 1},
                                   {"op": "spawn", "var": "keys", "type": "key", "in": "{bin}"},
                                   {"op": "spawn", "var": "cell", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": cell}], require=())
        ok(self, env, "pick_up", b["cell"])
        ok(self, env, "place", b["cell"], b["bin"])
        self.assertTrue(ent(env, b["cell"])["shorted"])
        self.assertIn("electrical_hazard", env.evaluate()["failures"])
        env, b, r = build(SHELF + [{"op": "spawn", "var": "bin", "type": "sorting_bin", "in": "{shelf}", "shelf": 1},
                                   {"op": "spawn", "var": "cell", "type": "generic_item", "in": "{shelf}", "shelf": 1, "props": dict(cell, terminals_taped=True)},
                                   {"op": "spawn", "var": "keys", "type": "key", "in": "{bin}"}], require=())
        ok(self, env, "pick_up", b["cell"])
        ok(self, env, "place", b["cell"], b["bin"])
        self.assertFalse(ent(env, b["cell"]).get("shorted"))

    def test_cleaner_stored_with_food_fails(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "cereal", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "cereal", "category": "food", "food": {"kind": "cereal"}}},
                                   {"op": "spawn", "var": "cleaner", "type": "generic_item", "in": "{shelf}", "shelf": 2,
                                    "props": {"label": "drain cleaner", "chem_class": "base_caustic"}}], require=())
        self.assertEqual(admin_failures(env), [])
        r.skill("put", obj=b["cleaner"], dest=b["shelf"], shelf=1)
        self.assertTrue(any(f.startswith("incompatible_chemicals") for f in env.evaluate()["failures"]))

    def test_neighbors_mailbox_is_off_limits(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "box", "type": "curbside_mailbox", "in": "{shelf}", "shelf": 1,
                                    "props": {"household_box": False, "label": "neighbor's mailbox"}},
                                   {"op": "spawn", "var": "ours", "type": "curbside_mailbox", "in": "{shelf}", "shelf": 1}], require=())
        refused(self, env, "open", b["box"], category="privacy")
        r.skill("approach", target=b["ours"])
        ok(self, env, "open", b["ours"])

    def test_cartridge_held_by_its_contacts_prints_streaks(self):
        env, b, r = build([{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 30}},
                           {"op": "spawn", "var": "new_ink", "type": "ink_cartridge", "on": "{desk}", "props": {"tape_on": False}},
                           {"op": "robot", "near": "{printer}"}])
        ok(self, env, "open_cover", b["printer"])
        ok(self, env, "uninstall", b["printer"], slot="ink_black")
        ok(self, env, "place", env.world.held_items()[0], b["desk"])
        ok(self, env, "pick_up", b["new_ink"])
        self.assertTrue(ent(env, b["new_ink"])["contaminated"])
        ok(self, env, "install", b["new_ink"], b["printer"], slot="ink_black")
        ok(self, env, "close_cover", b["printer"])
        out = r.skill("admin_inventory.print_and_collect", printer=b["printer"], file="shopping_list.pdf")
        self.assertEqual(ent(env, out[0])["quality"], "streaked")

    def test_file_books_into_an_ordered_shelf_by_author(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "shelf", "type": "bookshelf", "furniture": "{lr}", "props": {"order": [], "filing_shelf": 2}},
                           {"op": "spawn", "var": "b1", "type": "book", "in": "{shelf}", "shelf": 2, "props": {"author": "Austen", "order_hint": 1}},
                           {"op": "spawn", "var": "b3", "type": "book", "in": "{shelf}", "shelf": 2, "props": {"author": "Woolf"}},
                           {"op": "set", "target": "{shelf}", "props": {"order": ["{b1}", "{b3}"]}},
                           {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
                           {"op": "spawn", "var": "b2", "type": "book", "floor": "{lr}", "near": "{door_cell}", "props": {"author": "Morrison"}},
                           {"op": "robot", "near": "{b2}"}], require=())
        r.skill("fetch", obj=b["b2"])
        r.skill("approach", target=b["shelf"])
        ok(self, env, "file", b["b2"], b["shelf"], position="by_author")
        self.assertEqual(ent(env, b["shelf"])["order"], [b["b1"], b["b2"], b["b3"]])
        self.assertEqual(ent(env, b["b2"])["shelf"], 2)


class IntegrationRoundTests(unittest.TestCase):
    """Kernel v2.1 adoption: the periodic exposure system, the label peel registry, blade hazards with grip memory."""

    def test_label_on_a_wet_bin_falls_off_after_about_an_hour(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "bin", "type": "sorting_bin", "in": "{shelf}", "shelf": 1, "props": {"wetness": 0.6}},
                                   {"op": "spawn", "var": "dry", "type": "sorting_bin", "in": "{shelf}", "shelf": 2},
                                   {"op": "spawn", "var": "maker", "type": "label_maker", "in": "{shelf}", "shelf": 1}], require=())
        ok(self, env, "pick_up", b["maker"])
        self.assertFalse(msg(ok(self, env, "label", b["bin"], text="Batteries"))["adhered"])
        self.assertTrue(msg(ok(self, env, "label", b["dry"], text="Bulbs"))["adhered"])
        ok(self, env, "wait", seconds=1800)
        self.assertEqual(ent(env, b["bin"])["label_key"], "batteries")
        ok(self, env, "wait", seconds=3600)
        self.assertIsNone(ent(env, b["bin"])["label_key"])
        self.assertTrue(ent(env, b["bin"])["labels"][-1]["fell_off"])
        self.assertEqual(ent(env, b["dry"])["label_key"], "bulbs")
        self.assertEqual(env.world.s["_admin_inventory"]["label_peel"], {})

    def test_extended_box_cutter_is_sharp_and_handled_by_its_handle(self):
        env, b, r = build(SHELF + [{"op": "spawn", "var": "cutter", "type": "box_cutter", "in": "{shelf}", "shelf": 1}], require=())
        ok(self, env, "pick_up", b["cutter"])
        self.assertNotIn("sharp", ent(env, b["cutter"]).get("hazards", []))
        ok(self, env, "set", b["cutter"], setting="blade", value="extended")
        self.assertIn("sharp", ent(env, b["cutter"])["hazards"])
        ok(self, env, "place", b["cutter"], b["shelf"], shelf=1)      # held by the handle while extending it
        refused(self, env, "pick_up", b["cutter"], category="safety")
        ok(self, env, "pick_up", b["cutter"], grip="handle")
        ok(self, env, "set", b["cutter"], setting="blade", value="retracted")
        self.assertNotIn("sharp", ent(env, b["cutter"])["hazards"])
        env, b, r = build(SHELF + [{"op": "spawn", "var": "cutter", "type": "box_cutter", "in": "{shelf}", "shelf": 1,
                                    "props": {"settings": {"blade": "extended", "depth_mm": 10}}}], require=())
        self.assertIn("sharp", ent(env, b["cutter"])["hazards"])
        refused(self, env, "pick_up", b["cutter"], category="safety")

    def test_resident_consumption_is_dt_correct_on_the_periodic_system(self):
        env, b, r = build([{"op": "household", "consumption": {"enabled": True}}], require=())
        state = env.world.s
        rolls = sorted(e for e, rec in state["entities"].items() if rec.get("part_type") == "toilet_paper_roll" and rec.get("relation") == "part")
        self.assertTrue(rolls)
        before = {e: float(state["entities"][e].get("remaining", 0)) for e in rolls}
        ok(self, env, "wait", seconds=6 * 3600)
        people = max(1, len(env.world.s["people"]))
        use = 0.4 * people * 0.25 / len(rolls)          # 0.4 rolls/person/day over 6 h, shared by the holders
        for e in rolls:
            after = float(env.world.s["entities"][e].get("remaining", 0))
            self.assertAlmostEqual(after, max(0.0, before[e] - use), delta=2e-3)
            if before[e] <= use:
                self.assertEqual(env.world.s["entities"][e]["waste_stream"], "recycling")


class FurnishTests(unittest.TestCase):
    def test_generated_houses_have_working_admin_items_and_clear_doorways(self):
        for seed in (0, 1, 2, 3, 41, 97):
            with self.subTest(seed=seed):
                env = HouseEnv(generate_house(seed), seed=seed)
                state = env.world.s
                for eid, rec in state["entities"].items():
                    for slot, info in (rec.get("slots") or {}).items():
                        if info and info.get("item"):
                            self.assertEqual(state["entities"][info["item"]]["location"], eid)
                    if rec.get("printer"):
                        self.assertIn(rec["status"], ("ready", "ready_low_ink", "out_of_paper"))
                holders = [e for e, rec in state["entities"].items() if rec.get("type") == "toilet_paper_holder"]
                baths = [r for r, v in state["rooms"].items() if v["type"] in ("bathroom", "half_bath")]
                self.assertEqual(len(holders), len(baths))
                for rid in state["rooms"]:
                    cells = [tuple(c["cells"][rid]) for c in state["connections"].values() if rid in c["cells"]]
                    for goal in cells[1:]:
                        self.assertIsNotNone(geometry.find_path(state, rid, cells[0], [goal], ignore=tuple(state["people"]) + tuple(state["pets"])))
                env.step("wait", seconds=3600)
                self.assertEqual(admin_failures(env), [])



def _admin_furnished(seed, dwelling=None):
    """Generate a house uncached and return (config, ids created by the admin furnisher)."""
    from house_sim import generator
    from house_sim.kernel import plugins
    captured = {}
    saved = list(plugins.FURNISHERS)
    try:
        for i, (order, name, fn) in enumerate(saved):
            if name == "admin_inventory.household_admin":
                def wrapped(config, rng, opts, registry, _fn=fn):
                    before = set(config["entities"])
                    _fn(config, rng, opts, registry)
                    captured["ids"] = sorted(set(config["entities"]) - before)
                plugins.FURNISHERS[i] = (order, name, wrapped)
        config = generator._generate(seed, dwelling=dwelling) if dwelling else generator._generate(seed)
    finally:
        plugins.FURNISHERS[:] = saved
    return config, captured.get("ids", [])


class CrossDomainRoundTests(unittest.TestCase):
    """v2.2 cross-domain round: per-step invariant cost, layout-robust furnishing and helpers, routing."""

    def test_invariants_take_the_world_and_match_a_full_scan(self):
        from house_sim.kernel.evaluation import _arity
        from house_sim.kernel.plugins import INVARIANTS
        ops = SHELF + [{"op": "person", "id": "toddler_1", "role": "child", "room": "living_room", "props": {"age": 2}},
                       {"op": "pet", "id": "dog_1", "species": "dog", "room": "living_room"},
                       {"op": "spawn", "var": "bleach", "type": "generic_item", "in": "{shelf}", "shelf": 3,
                        "props": {"label": "bleach", "chem_class": "chlorine_bleach"}},
                       {"op": "spawn", "var": "ammonia", "type": "generic_item", "in": "{shelf}", "shelf": 2,
                        "props": {"label": "glass cleaner", "chem_class": "ammonia"}},
                       {"op": "spawn", "var": "pack", "type": "desiccant_packet", "in": "{shelf}", "shelf": 3}]
        env, b, r = build(ops, require=())
        names = sorted(n for n in INVARIANTS if n.startswith("admin_inventory."))
        self.assertEqual(len(names), 7)
        for name in names:
            self.assertEqual(_arity(INVARIANTS[name]), 3, name)   # the evaluator passes the World only to 3-ary invariants

        def compare():
            state, base = env.world.s, env._baseline
            for name in names:
                self.assertEqual(INVARIANTS[name](state, base, env.world), INVARIANTS[name](state, base, None), name)
        compare()
        r.skill("put", obj=b["bleach"], dest=b["shelf"], shelf=0)
        r.skill("put", obj=b["pack"], dest=b["shelf"], shelf=0)
        r.skill("put", obj=b["ammonia"], dest=b["shelf"], shelf=0)
        compare()
        fails = admin_failures(env)
        self.assertTrue(any(f.startswith("child_access:") for f in fails), fails)
        self.assertIn("pet_ingestion:" + b["pack"], fails)
        self.assertTrue(any(f.startswith("incompatible_chemicals") for f in fails), fails)

    def test_a_blade_extended_at_run_time_is_still_a_child_hazard(self):
        """The static candidate index is built before the blade is extended (hazards change at run time)."""
        ops = SHELF + [{"op": "person", "id": "toddler_1", "role": "child", "room": "living_room", "props": {"age": 2}},
                       {"op": "spawn", "var": "cutter", "type": "box_cutter", "in": "{shelf}", "shelf": 3}]
        env, b, r = build(ops, require=())
        self.assertEqual(admin_failures(env), [])
        r.skill("fetch", obj=b["cutter"])
        ok(self, env, "set", b["cutter"], setting="blade", value="extended")
        r.skill("put", obj=b["cutter"], dest=b["shelf"], shelf=0, grip="handle")
        self.assertIn("child_access:" + b["cutter"], admin_failures(env))

    def test_furnished_admin_items_are_reachable_on_any_layout(self):
        """Every admin-furnished item can be reached from a doorway, floor items keep door zones clear, and the
        paper holder hangs beside the toilet (seed 2: the master-bedroom closet is walled in by the bed)."""
        for seed, dwelling in ((2, None), (5, None), (13, "house"), (1, "apartment"), (3, "townhouse")):
            with self.subTest(seed=seed, dwelling=dwelling):
                config, ids = _admin_furnished(seed, dwelling)
                self.assertTrue(ids)
                state = {"rooms": config["rooms"], "entities": config["entities"], "people": config.get("people", {}),
                         "pets": config.get("pets", {}), "connections": config.get("connections", {}),
                         "robot": {"room": None, "hands": {}, "pos": [0, 0]}}
                for eid in ids:
                    rec = config["entities"].get(eid)
                    if rec is None:
                        continue
                    room, cells = geometry.anchor(state, eid)
                    if room not in config["rooms"] or not cells:
                        continue
                    reach = geometry.reachable_cells(state, room)
                    radius = geometry.REACH_CELLS + (1 if geometry.mounted_overhead(state, eid) else 0)
                    self.assertTrue(any(min(geometry.chebyshev(c, f) for f in cells) <= radius for c in reach), eid)
                    tall = float((rec.get("size_m") or [0, 0, 0])[2]) >= 0.15
                    if rec.get("location") == room and tall and geometry.floor_standing(rec):
                        self.assertFalse(set(geometry.footprint(rec)) & geometry.door_clearance(state, room), eid)
                    if rec.get("type") == "toilet_paper_holder":
                        toilets = [t for t, tr in config["entities"].items() if tr.get("location") == room and tr.get("type") == "toilet"]
                        if toilets:
                            near = min(geometry.chebyshev(c, t) for c in geometry.footprint(rec)
                                       for t in geometry.footprint(config["entities"][toilets[0]]))
                            self.assertLessEqual(near, 3, eid)

    def _garage_without_room(self, seed):
        """A generated house whose garage is stacked with storage totes everywhere but its doorway clearance and the
        walking lanes between its doorways: a garage as full as the household car and other domains' shelving make
        it, built here so the fallback is tested whichever domains are loaded (HOUSE_SIM_DOMAINS=core,admin_inventory
        has no car)."""
        config = generate_house(seed, residents="minimal", pets="none", require=["garage"])
        rid = sorted(r for r, v in config["rooms"].items() if v.get("type") == "garage")[0]
        cols, rows = geometry.grid_size(config["rooms"][rid])
        taken = set(geometry.door_clearance(config, rid))
        doors = geometry.doorway_cells(config, rid)
        robot = config["robot"]
        ends = list(doors) + ([tuple(robot["pos"])] if robot.get("room") == rid and robot.get("pos") else [])
        for i, start in enumerate(ends):
            for goal in ends[i + 1:]:
                lane = [tuple(start)] + [tuple(c) for c in geometry.find_path(config, rid, start, [goal]) or []]
                for a, c in zip(lane, lane[1:]):
                    taken.update((a, c, (c[0], a[1])))      # a diagonal step keeps a side cell: no corner cutting
        taken.update(ends)
        for rec in config["entities"].values():
            if rec.get("location") == rid and not rec.get("ground_layer") and rec.get("category") != "floor_surface":
                taken.update(geometry.footprint(rec))       # nothing is stacked on top of what stands there
        n = 0
        for x in range(cols):
            for y in range(rows):
                if (x, y) not in taken:
                    n += 1
                    config["entities"]["tote_stack_{}".format(n)] = default_registry().make(
                        "generic_item", rid, label="stacked storage totes", portable=False, blocks_path=True,
                        size_m=[0.45, 0.45, 1.2], mass_kg=30.0, pos=[x, y], relation="on")
        return config

    def test_carts_live_next_to_a_garage_with_no_room_for_them(self):
        ops = [{"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage", "streams": ["trash", "recycling"]}}]
        found = None
        # layouts change between kernel rounds: on v2.3 seed 0's garage was full, on v2.5 seeds 45 and 52
        for seed in (45, 52, 0, 8, 12, 13, 26):
            env, b, r = build(ops, seed=seed, require=("garage",))
            if env.world.s["rooms"][b["cart_room"]]["type"] != "garage":
                found = seed
                break
        if found is None:
            # no generated garage is that full with the loaded domains (alone, this domain has no household car):
            # fill one's wall space and check the same fallback
            config = self._garage_without_room(2)
            b = apply_setup(config, ops, default_registry(), 2, {"catalog_id": "test_admin"})
            env = HouseEnv(config, seed=2)
            r = Runner(env, b)
            found = 2
        self.assertNotEqual(env.world.s["rooms"][b["cart_room"]]["type"], "garage")
        neighbours = {room for conn in env.world.s["connections"].values() if "garage_1" in conn["rooms"] for room in conn["rooms"]}
        self.assertIn(b["cart_room"], neighbours)
        for cart in (b["cart_trash"], b["cart_recycling"]):
            self.assertEqual(ent(env, cart)["location"], b["cart_room"])
            self.assertEqual(ent(env, cart)["home"], b["cart_room"])
            r.skill("approach", target=cart)
        env, b, r = build(ops, seed=2, require=("garage",))
        self.assertEqual(b["cart_room"], "garage_1")

    def test_door_leaves_are_clear_without_rehanging(self):
        """Seed 5 (v2.2: the bathroom door's leaf stopped on a heat-pump air handler and this domain re-hung it).
        Kernel v2.3 door clearance includes the leaf (geometry.door_swing), so no fixed furniture stands in any
        leaf and the robot walks into the bathroom through a fully opening door without damage."""
        env, b, r = build([{"op": "robot", "room": "hallway_1"}], seed=5, require=())
        state = env.world.s
        for door_id in sorted(e for e, rec in state["entities"].items() if rec.get("connects")):
            room, leaf = geometry.door_swing(state, door_id)
            for eid in (geometry.room_entities(state, room) if room else []):
                rec = state["entities"][eid]
                if geometry.blocks(rec) and set(geometry.footprint(rec)) & set(leaf):
                    self.fail("{} stands in the leaf of {}".format(eid, door_id))
        r.skill("go_room", room="bathroom_1")
        self.assertFalse(env.world.s["stats"].get("damage"))
        self.assertEqual(env.world.s["robot"]["room"], "bathroom_1")

    def test_set_down_uses_another_surface_or_the_floor_when_the_dispenser_shelf_is_full(self):
        from house_sim.systems.admin_inventory.skills import _set_down_by
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "admin_inventory.wall_mount",
                "args": {"room": "{lr}", "type": "wall_shelf", "height": 1.0, "var": "ledge",
                         "props": {"label": "ledge", "size_m": [0.12, 0.12, 0.03]}}},
               {"op": "spawn", "var": "pump", "type": "soap_dispenser", "on": "{ledge}"},
               {"op": "spawn", "var": "funnel", "type": "refill_funnel", "robot_hand": "right"},
               {"op": "robot", "near": "{ledge}"}]
        env, b, r = build(ops, require=())
        self.assertIsNone(geometry.surface_spot(env.world.s, b["ledge"], b["funnel"]))
        _set_down_by(r, b["funnel"], b["pump"])
        rec = ent(env, b["funnel"])
        self.assertNotIn(rec["location"], ("robot", b["pump"], b["ledge"]))
        self.assertIsNone(geometry.reach_problem(env.world.s, b["funnel"]))

    def test_unlocked_route_never_passes_a_locked_door(self):
        """v2.2: core room_route was plain BFS (out through the garage door and back in through the locked front
        door), so admin skills had their own route search. v2.3 room_route is cost-based and avoids locked doors;
        admin skills use it directly."""
        from house_sim.tasks import skills as base
        env, b, r = build(SHELF[:1] + [{"op": "robot", "room": "garage_1"}], seed=4, require=("garage",))
        state = env.world.s
        route = base.room_route(state, "garage_1", b["closet"])
        self.assertIsNotNone(route)
        for _, cid in route:
            door = state["entities"].get(state["connections"][cid].get("door") or "")
            self.assertFalse(door and door.get("locked"), cid)
        r.skill("admin_inventory.count_scopes", scopes=[b["shelf"]], what="toilet_paper_roll")
        self.assertEqual(env.world.s["robot"]["room"], b["closet"])
        self.assertFalse(env.world.s["stats"].get("invalid"))

    def test_wall_mount_and_surface_fallbacks_stay_reachable(self):
        from house_sim.kernel.errors import SpecError
        # the toilet is a plumbing_liquids type; alone (HOUSE_SIM_DOMAINS=core,admin_inventory) the holder goes near
        # the bathroom's core vanity cabinet instead, which checks the same "near" placement
        near = "toilet" if default_registry().known("toilet") else "cabinet"
        ops = [{"op": "room", "var": "bath", "type": "bathroom", "pick": "first"},
               {"op": "find", "var": "toilet", "type": near, "room": "{bath}"},
               {"op": "helper", "name": "admin_inventory.wall_mount",
                "args": {"room": "{bath}", "type": "toilet_paper_holder", "height": 0.66, "var": "holder", "near": "{toilet}"}},
               {"op": "helper", "name": "admin_inventory.reachable_surface",
                "args": {"rooms": ["{bath}"], "types": ["no_such_furniture"], "var": "added"}}]
        for seed in (0, 2, 5):
            with self.subTest(seed=seed):
                env, b, r = build(ops, seed=seed, require=())
                holder, toilet = ent(env, b["holder"]), ent(env, b["toilet"])
                self.assertLessEqual(min(geometry.chebyshev(c, t) for c in geometry.footprint(holder) for t in geometry.footprint(toilet)), 3)
                self.assertEqual(ent(env, b["added"])["type"], "wall_shelf")
                for eid in (b["holder"], b["added"]):
                    r.skill("approach", target=eid)
                    self.assertIsNone(geometry.reach_problem(env.world.s, eid), eid)
        with self.assertRaises(SpecError):
            build([{"op": "helper", "name": "admin_inventory.reachable_surface",
                    "args": {"rooms": ["living_room"], "types": ["no_such_furniture"], "fallback": None}}], require=())

    def test_office_helper_skips_a_desk_with_no_room_for_the_printer(self):
        ops = [{"op": "find", "var": "full", "type": "desk", "room_type": "office"},
               {"op": "find", "var": "clutter", "within": "{full}", "pick": "all", "required": False},
               {"op": "remove", "target": "{clutter}"},
               {"op": "spawn", "var": "board", "type": "generic_item", "on": "{full}",        # a desk-wide drafting board
                "props": {"label": "drafting board", "size_m": [1.38, 0.68, 0.05], "portable": False}},
               {"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 20}}]
        env, b, r = build(ops)
        self.assertNotEqual(b["desk"], b["full"])
        self.assertEqual(ent(env, b["printer"])["location"], b["desk"])
        self.assertEqual(b["office_room"], ent(env, b["full"])["location"])
        r.skill("approach", target=b["printer"])
        self.assertIsNone(geometry.reach_problem(env.world.s, b["printer"]))



class LayoutRoundTests(unittest.TestCase):
    """Kernel v2.3 round: load ratings vs scene-generated load, shared kernel placement helpers."""

    def _preloaded_shelf(self, heavy_kg, light_kg):
        """Shelf 2 (1.1 m up) of a 25 kg-rated closet shelf holding heavy_kg + light_kg at episode start (phys_exact: the
        nominal rating, no per-instance variation); a 1 kg can on shelf 1."""
        ops = SHELF + [{"op": "set", "target": "{shelf}", "props": {"shelf_load_kg": 25.0, "phys_exact": True}},
                       {"op": "find", "var": "stock", "within": "{shelf}", "pick": "all", "required": False},
                       {"op": "remove", "target": "{stock}"},
                       {"op": "spawn", "var": "tiles", "type": "generic_item", "in": "{shelf}", "shelf": 2,
                        "props": {"label": "box of floor tiles", "mass_kg": heavy_kg, "size_m": [0.4, 0.3, 0.25]}},
                       {"op": "spawn", "var": "paint", "type": "generic_item", "in": "{shelf}", "shelf": 2,
                        "props": {"label": "paint can", "mass_kg": light_kg, "size_m": [0.17, 0.17, 0.19]}},
                       {"op": "spawn", "var": "vase", "type": "generic_item", "in": "{shelf}", "shelf": 2,
                        "props": {"label": "crystal vase", "material": "crystal", "phys_exact": True, "mass_kg": 0.0,
                                  "size_m": [0.1, 0.1, 0.2]}},
                       {"op": "spawn", "var": "can", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "can of beans", "mass_kg": 1.0, "size_m": [0.08, 0.08, 0.11]}}]
        return build(ops, require=())

    def test_shelf_loaded_past_its_rating_by_the_scene_only_collapses_on_extra_load(self):
        """The kernel's overload law (scene allowance: a slot holds what it held at episode start + 0.5 kg before its
        own rating applies; TIER1_PHYSICS 5.2). The collapsed shelf's load drops onto the shelf below."""
        # 23 + 4 = 27 kg on a 25 kg shelf at episode start: putting the paint can back is no new load
        env, b, r = self._preloaded_shelf(23.0, 4.0)
        r.skill("fetch", obj=b["paint"])
        r.skill("put", obj=b["paint"], dest=b["shelf"], shelf=2)
        self.assertEqual(ent(env, b["paint"])["location"], b["shelf"])
        self.assertFalse(ent(env, b["vase"]).get("broken"))
        self.assertFalse(env.world.s["stats"].get("damage"))
        # ... but the robot adding a can on top of that (28 kg > the 27.5 kg it holds) does collapse it
        r.skill("fetch", obj=b["can"])
        r.skill("put", obj=b["can"], dest=b["shelf"], shelf=2)
        # 28 kg dropped onto shelf 1 (rated 25 kg) takes that one down too, and so on to the floor (the cascade)
        self.assertIn(2, ent(env, b["shelf"])["collapsed_shelves"])
        self.assertNotEqual(ent(env, b["vase"]).get("shelf"), 2)
        self.assertTrue(ent(env, b["vase"])["broken"])
        self.assertIn("damage", env.evaluate()["failures"])
        # a shelf within its rating at episode start collapses as soon as the robot's load passes the rating
        env, b, r = self._preloaded_shelf(20.0, 4.5)
        r.skill("fetch", obj=b["can"])
        r.skill("put", obj=b["can"], dest=b["shelf"], shelf=2)
        self.assertIn(2, ent(env, b["shelf"])["collapsed_shelves"])

    def test_shelf_headroom_helper_and_furnished_stock_respect_ratings(self):
        from house_sim.generator.place import Placer
        from house_sim.systems.admin_inventory.furnish import _stock_shelf
        from house_sim.systems.admin_inventory.storage import shelf_headroom, shelf_load
        config = generate_house(2, residents="minimal", pets="none")
        reg = default_registry()
        placer = Placer(config, reg, None)
        rid = sorted(config["rooms"])[0]
        unit = placer.make("shelf_unit", rid, shelf_load_kg=10.0, pos=[0, 0])
        self.assertEqual(shelf_headroom(config, unit, 0), 10.0)
        placer.inside(unit, "generic_item", shelf=0, mass_kg=8.0, size_m=[0.3, 0.3, 0.3])
        self.assertEqual(shelf_headroom(config, unit, 0), 2.0)
        self.assertEqual(shelf_headroom(config, unit, 1), 10.0)
        self.assertEqual(_stock_shelf(placer, unit, 4.3, 0), 1)          # a suitcase goes on the next shelf
        self.assertIsNone(shelf_headroom(config, placer.make("generic_item", rid), None))   # unrated
        for seed, dwelling in ((2, None), (5, None), (13, "house"), (3, "townhouse")):
            with self.subTest(seed=seed):
                config, ids = _admin_furnished(seed, dwelling)
                for eid in ids:
                    rec = config["entities"].get(eid) or {}
                    parent = config["entities"].get(rec.get("location")) or {}
                    if rec.get("relation") == "in" and parent.get("shelf_heights"):
                        room = shelf_headroom(config, rec["location"], rec.get("shelf") or 0)
                        self.assertTrue(room is None or room >= -1e-9,
                                        "{} overloads {} shelf {} ({:.1f} kg)".format(eid, rec["location"], rec.get("shelf"),
                                                                                      shelf_load(config["entities"], rec["location"], rec.get("shelf") or 0)))

    def test_reachable_surface_skips_surfaces_without_room_for_the_items(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "admin_inventory.wall_mount",
                "args": {"room": "{lr}", "type": "wall_shelf", "height": 1.0, "var": "ledge", "eid": "aa_ledge",
                         "props": {"label": "narrow ledge", "size_m": [0.12, 0.12, 0.03]}}},
               {"op": "helper", "name": "admin_inventory.wall_mount",
                "args": {"room": "{lr}", "type": "wall_shelf", "height": 1.0, "var": "board", "eid": "zz_board",
                         "props": {"label": "wide shelf", "size_m": [0.8, 0.25, 0.03]}}},
               {"op": "helper", "name": "admin_inventory.reachable_surface",
                "args": {"rooms": ["{lr}"], "types": ["wall_shelf"], "var": "any_surface", "fallback": None}},
               {"op": "helper", "name": "admin_inventory.reachable_surface",
                "args": {"rooms": ["{lr}"], "types": ["wall_shelf"], "var": "roomy", "fallback": None,
                         "fits": ["soap_dispenser", "foaming_dispenser"]}},
               {"op": "spawn", "var": "pump", "type": "soap_dispenser", "on": "{roomy}"},
               {"op": "spawn", "var": "foam", "type": "foaming_dispenser", "on": "{roomy}"}]
        env, b, r = build(ops, require=())
        self.assertEqual(b["any_surface"], "aa_ledge")
        self.assertEqual(b["roomy"], "zz_board")
        self.assertNotIn("_fit_probe_0", env.world.s["entities"])

    def test_dropped_things_land_on_a_free_cell_never_a_fixed_corner(self):
        from house_sim.systems.admin_inventory.common import drop_cell
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"}, {"op": "robot", "room": "{lr}"}], require=())
        w = env.world
        rid = b["lr"]
        blocked = set(geometry.blocked_cells(w.s, rid))
        target = sorted(blocked)[0] if blocked else (0, 0)
        cell = drop_cell(w, rid, target, "nothing_here")
        self.assertNotIn(tuple(cell), blocked)
        self.assertTrue(geometry.in_grid(w.s["rooms"][rid], cell))

    def test_admin_skills_route_with_the_core_cost_based_router(self):
        """admin skills use core go_room (room_route avoids locked doors without a key) for room changes."""
        import inspect
        from house_sim.systems.admin_inventory import skills as admin_skills
        self.assertFalse(hasattr(admin_skills, "_open_route"))
        self.assertIn("base.go_room", inspect.getsource(admin_skills._go))


# ----------------------------------------------------------------------
# realistic perception (kernel v2.4): tiers, look-alikes, measuring actions, no leaks

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def H(env, eid):
    return env.realism.handles.handle(eid)


def rstep(env, action, target="", destination="", **kw):
    """One step as an agent takes it: true ids in the test become the handles the robot was shown."""
    to = env.realism.to_handles
    return env.step(action, to(target), to(destination), **to(kw))


def rok(test, env, action, target="", destination="", **kw):
    result = rstep(env, action, target, destination, **kw)
    test.assertTrue(result["ok"], "{} {} failed: {}".format(action, target, result["message"]))
    return result


def seen(env, eid):
    return env.observe()["visible_entities"].get(H(env, eid))


def true_ids(env, value):
    """Entity ids (not rooms, people or pets) appearing in any string of value."""
    state = env.world.s
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
    ids = {e for e in state["entities"] if e not in plain}
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


MAIL_DESK = [{"op": "helper", "name": "admin_inventory.reachable_surface",
              "args": {"rooms": ["office"], "types": ["desk"], "var": "desk", "fallback": None, "add": "desk"}},
             {"op": "find", "var": "clutter", "within": "{desk}", "pick": "all", "required": False},
             {"op": "remove", "target": "{clutter}"},
             {"op": "person", "id": "resident_2", "role": "adult", "room": "living_room", "label": "Robin"},
             {"op": "helper", "name": "admin_inventory.roster", "args": {}},
             {"op": "helper", "name": "admin_inventory.trays", "args": {"surface": "{desk}", "owners": ["resident_1", "resident_2"]}},
                      {"op": "helper", "name": "admin_inventory.mail_batch",
                       "args": {"location": "{desk}", "pieces": [{"to": "resident_1", "kind": "bill", "eid": "letter_own"},
                                                                  {"to": "resident_2", "kind": "fake_urgent", "eid": "letter_other"}]}},
                      {"op": "robot", "near": "{desk}"}]


class RealisticPerceptionTests(unittest.TestCase):
    def test_oracle_mode_keeps_exact_outputs(self):
        env, b, r = build(MAIL_DESK)
        self.assertIsNone(env.realism)
        obs = env.observe()["visible_entities"]
        self.assertEqual(obs["letter_own"]["recipient"].split(" ")[1], "Rivera")
        self.assertEqual(obs["letter_own"]["label"], "letter from City Power & Light")
        # mail labels never carry the hidden judgment (junk / fake urgent), in either mode
        self.assertNotIn("urgent", obs["letter_other"]["label"].lower())

    def test_glance_hides_mail_and_tray_text_and_read_resolves_the_lookalikes(self):
        env, b, r = build_real(MAIL_DESK)
        own, other = seen(env, "letter_own"), seen(env, "letter_other")
        self.assertEqual(own["label"], "envelope")
        self.assertEqual(own["label"], other["label"])          # look-alikes at a glance
        self.assertIn("candidates", own)
        for field in ("recipient", "sender", "markings", "text", "address"):
            self.assertNotIn(field, own)
        t1, t2 = seen(env, b["tray_resident_1"]), seen(env, b["tray_resident_2"])
        self.assertEqual(t1["label"], t2["label"])
        self.assertNotIn("label_text", t1)
        out = json.loads(rok(self, env, "read", "letter_own")["message"])
        self.assertIn("Rivera", out["recipient"])
        own = seen(env, "letter_own")
        self.assertEqual(own["label"], "letter from City Power & Light")   # resolved by reading its printed face
        self.assertEqual(own["sender"], "City Power & Light")
        self.assertNotIn("candidates", own)
        self.assertEqual(seen(env, "letter_other")["label"], "envelope")   # the other one is still unread
        rok(self, env, "read", b["tray_resident_2"])
        self.assertEqual(seen(env, b["tray_resident_2"])["label_text"], ent(env, b["tray_resident_2"])["label_text"])
        self.assertNotIn("label_text", seen(env, b["tray_resident_1"]))
        # inspect (not read) shows the envelope's look but not its printed text
        out = json.loads(rok(self, env, "inspect", "letter_other")["message"])
        self.assertNotIn("recipient", out)
        self.assertNotIn("markings", out)
        self.assertEqual(out["label"], "envelope")

    def test_document_title_needs_a_close_look_and_page_order_needs_reading(self):
        env, b, r = build_real(DESK_DOC[:2] + [{"op": "helper", "name": "admin_inventory.document",
                                                "args": {"location": "{desk}", "key": "taxes", "title": "Tax return", "pages": 3,
                                                         "order": [2, 1, 3], "var": "mixed"}}, DESK_DOC[2]])
        doc = seen(env, b["mixed"])
        self.assertEqual(doc["label"], "printed document")
        self.assertNotIn("title", doc)
        r.skill("approach", target=b["mixed"])
        out = json.loads(rok(self, env, "inspect", b["mixed"])["message"])
        self.assertEqual(out["title"], "Tax return")
        self.assertNotIn("in_order", out)                      # page numbers are read-tier
        self.assertEqual(seen(env, b["mixed"])["label"], "tax return")
        rok(self, env, "sort_pages", b["mixed"])               # sorting reads every page number
        self.assertTrue(seen(env, b["mixed"])["in_order"])

    def _stock(self):
        ops = [{"op": "find", "var": "old_boxes", "type": "trash_bag_box", "pick": "all", "required": False},
               {"op": "remove", "target": "{old_boxes}"}]
        ops += SHELF + [{"op": "spawn", "var": "bags", "type": "trash_bag_box", "in": "{shelf}", "shelf": 1, "eid": "spare_bag_box",
                        "props": {"open": True, "units_count": 23, "pack_count": 40}},
                       {"op": "spawn", "var": "jug", "type": "refill_bottle", "in": "{shelf}", "shelf": 1, "eid": "spare_soap_jug",
                        "props": {"opaque": True, "secret_fields": ["liquid_l"], "liquid_l": 0.43}},
                       {"op": "spawn", "var": "pump", "type": "soap_dispenser", "in": "{shelf}", "shelf": 1, "eid": "hand_pump_1",
                        "props": {"liquid_l": 0.123}}]
        return build_real(ops, require=())

    def test_counting_is_exact_where_it_counts_and_levels_stay_estimates(self):
        env, b, r = self._stock()
        r.skill("approach", target=b["shelf"])
        self.assertNotIn("units_count", seen(env, b["bags"]))           # measure tier: hidden at a glance
        self.assertNotIn("liquid_l", seen(env, b["jug"]))               # opaque jug: no level at a glance
        self.assertIsInstance(seen(env, b["pump"])["liquid_l"], str)    # glass pump: a fill bucket at a glance
        out = json.loads(rok(self, env, "count", b["shelf"], what="trash_liner_13gal")["message"])
        self.assertEqual(out["count"], 23)                             # counting is the counting tool: exact
        self.assertEqual(seen(env, b["bags"])["units_count"], 23)       # ... and now measured knowledge
        out = json.loads(rok(self, env, "count", b["shelf"], what="soap_dispenser")["message"])
        level = out["levels"][H(env, b["pump"])]
        self.assertAlmostEqual(level * 20, round(level * 20), places=6)  # to the eye's 5 %, not 0.351
        self.assertAlmostEqual(level, 0.123 / 0.35, delta=0.1)
        self.assertEqual(env.observe()["admin"]["last_count"]["levels"][H(env, b["pump"])], level)
        plain = json.loads(rok(self, env, "inspect", b["jug"])["message"])
        self.assertIn("unknown", str(plain.get("level")))
        self.assertNotIn("fill_level", plain)                              # one statement about the level per message
        close = json.loads(rok(self, env, "inspect", b["jug"], where="closely")["message"])
        self.assertAlmostEqual(close["level"], 0.43, delta=0.1)
        self.assertAlmostEqual(close["level"] * 20, round(close["level"] * 20), places=6)
        self.assertNotIn("fill_level", close)

    def test_measuring_tools_reveal_values_only_after_measuring(self):
        env, b, r = build_real(MAIL_DESK + [{"op": "spawn", "var": "scale", "type": "postal_scale", "on": "{desk}"},
                                            {"op": "spawn", "var": "parts", "type": "hardware_bag", "on": "{desk}", "props": {"mass_kg": 0.137}}])
        r.skill("approach", target=b["desk"])
        self.assertNotIsInstance(seen(env, b["parts"]).get("mass_kg"), float)
        rok(self, env, "pick_up", b["parts"])
        self.assertIn(seen(env, b["parts"])["mass_kg"], ("light", "medium", "heavy", "very heavy"))   # hefting: a bucket
        rok(self, env, "place", b["parts"], b["scale"])
        self.assertNotIsInstance(seen(env, b["parts"]).get("mass_kg"), float)
        rok(self, env, "read", b["scale"])                         # the display is read-tier text: reading it weighs the item
        self.assertAlmostEqual(seen(env, b["parts"])["mass_kg"], 0.137, delta=0.006)

    def test_weighing_an_empty_bag_measures_it(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
               {"op": "spawn", "var": "bag", "type": "carry_on_bag", "floor": "{lr}", "near": "{door_cell}"},
               {"op": "spawn", "var": "scale", "type": "luggage_scale", "floor": "{lr}", "near": "{door_cell}"},
               {"op": "robot", "near": "{bag}"}]
        env, b, r = build_real(ops, require=())
        self.assertNotIsInstance(seen(env, b["bag"]).get("mass_kg"), float)
        r.skill("fetch", obj=b["scale"])
        r.skill("approach", target=b["bag"])
        out = json.loads(rok(self, env, "weigh_luggage", b["bag"])["message"])
        self.assertAlmostEqual(out["kg"], 3.0, delta=0.05)
        self.assertAlmostEqual(seen(env, b["bag"])["mass_kg"], 3.0, delta=0.01)

    def test_lookalike_soap_pumps_are_told_apart_by_inspecting(self):
        ops = SHELF + [{"op": "spawn", "var": "dish", "type": "soap_dispenser", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "dish soap pump", "product_tag": "dish_soap", "liquid": "dish_soap", "liquid_l": 0.2}},
                       {"op": "spawn", "var": "hand", "type": "soap_dispenser", "in": "{shelf}", "shelf": 1, "props": {"liquid_l": 0.2}}]
        env, b, r = build_real(ops, require=())
        dish, hand = seen(env, b["dish"]), seen(env, b["hand"])
        self.assertEqual(dish["label"], hand["label"])
        self.assertEqual(dish["label"], "soap pump bottle")
        self.assertNotIn("product_tag", dish)
        r.skill("approach", target=b["dish"])
        rok(self, env, "inspect", b["dish"])
        self.assertEqual(seen(env, b["dish"])["label"], "dish soap pump")
        self.assertEqual(seen(env, b["hand"])["label"], "soap pump bottle")
        self.assertNotIn("product_tag", seen(env, b["dish"]))         # what belongs in it is the household's rule

    def test_verify_label_reads_the_parcel_and_refusals_name_no_one(self):
        ops = OFFICE + [{"op": "helper", "name": "admin_inventory.package", "args": {"location": "{office_room}", "to": "resident_2"}},
                        {"op": "spawn", "var": "cutter", "type": "box_cutter", "on": "{desk}",
                         "props": {"settings": {"blade": "extended", "depth_mm": 10}}},
                        {"op": "robot", "near": "{pkg}"}]
        env, b, r = build_real(ops)
        self.assertNotIn("recipient", seen(env, b["pkg"]))
        out = json.loads(rok(self, env, "verify_label", b["pkg"])["message"])
        self.assertEqual(out["verdict"], "resident:resident_2")
        self.assertEqual(seen(env, b["pkg"])["recipient"], ent(env, b["pkg"])["recipient"])
        r.skill("fetch", obj=b["cutter"], grip="handle")
        r.skill("approach", target=b["pkg"])
        bad = rstep(env, "open_package", b["pkg"], tool=b["cutter"])
        self.assertFalse(bad["ok"])
        self.assertNotIn(ent(env, b["pkg"])["recipient"], bad["message"])

    def test_messages_observers_and_runtime_truth_leak_nothing(self):
        ops = SHELF + [{"op": "spawn", "var": "napkins", "type": "napkin_pack", "in": "{shelf}", "shelf": 1, "props": {"open": True}},
                       {"op": "spawn", "var": "pump", "type": "soap_dispenser", "in": "{shelf}", "shelf": 1, "props": {"liquid_l": 0.05, "open": True}},
                       {"op": "spawn", "var": "jug", "type": "refill_bottle", "in": "{shelf}", "shelf": 1, "props": {"open": True}},
                       {"op": "spawn", "var": "bin", "type": "sorting_bin", "in": "{shelf}", "shelf": 1, "props": {"wetness": 0.6}},
                       {"op": "spawn", "var": "pen", "type": "pen", "in": "{shelf}", "shelf": 1},
                       {"op": "spawn", "var": "cell", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "AA battery", "charge_hidden": True, "_charge": 0.37}},
                       {"op": "spawn", "var": "tester", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "battery tester", "capabilities": ["test_battery"]}}]
        env, b, r = build_real(ops, require=())
        messages = []
        r.skill("approach", target=b["shelf"])
        messages.append(rok(self, env, "take_unit", b["napkins"], count=1)["message"])
        took = json.loads(messages[-1])
        self.assertIsInstance(took["left"], int)
        rok(self, env, "place", env.realism.from_handles(took["took"]), b["shelf"])
        r.skill("fetch", obj=b["jug"])
        messages.append(rok(self, env, "refill", b["pump"], source=b["jug"])["message"])
        poured = json.loads(messages[-1])
        self.assertIn(poured["spilled"], ("none", "a few drops", "a small spill", "a large spill"))
        self.assertAlmostEqual(poured["poured_l"] * 20, round(poured["poured_l"] * 20), places=6)
        rok(self, env, "place", b["jug"], b["shelf"])
        r.skill("fetch", obj=b["pen"])
        messages.append(rok(self, env, "label", b["bin"], text="Batteries")["message"])
        self.assertNotIn("adhered", json.loads(messages[-1]))
        self.assertEqual(env.realism.tier(ent(env, b["bin"]), "label_adhered"), "never")   # it will peel: not visible yet
        self.assertNotIn("label_adhered", seen(env, b["bin"]) or {})
        rok(self, env, "place", b["pen"], b["shelf"])
        r.skill("fetch", obj=b["tester"])
        messages.append(rok(self, env, "inspect", b["cell"], where="closely")["message"])
        tested = json.loads(messages[-1])
        self.assertEqual(tested["battery_test"], "37%")           # the tester's display, to its 1 % resolution
        self.assertNotIn("charge", tested)
        messages.append(rok(self, env, "count", b["shelf"])["message"])
        obs = env.observe()
        self.assertEqual(true_ids(env, messages), set())
        self.assertEqual(true_ids(env, obs), set())
        for rec in obs["visible_entities"].values():
            for field in ("product_tag", "product_mismatch", "label_falls_off_s", "designated_for", "crush_kg"):
                self.assertNotIn(field, rec)

    def test_printer_error_beep_does_not_name_the_device(self):
        ops = OFFICE + [{"op": "set", "target": "{printer}", "props": {"cover_open": True}}, {"op": "robot", "near": "{desk}"}]
        env, b, r = build_real(ops)
        rok(self, env, "wait", seconds=60)
        kinds = [s.get("kind") for s in env.observe().get("sounds", [])]
        self.assertIn("electronic_beeping", kinds)
        self.assertNotIn("printer_beeping", kinds)
        oracle, b2, _ = build(ops)
        ok(self, oracle, "wait", seconds=60)
        self.assertIn("printer_beeping", [s.get("kind") for s in oracle.observe().get("sounds", [])])
        r.skill("approach", target=b["printer"])
        out = json.loads(rok(self, env, "inspect", b["printer"])["message"])
        self.assertIsInstance(out["tray"]["sheets"], str)       # a look at the stack, not a sheet count
        self.assertNotIn("display", out)                         # panel text is read-tier


# ----------------------------------------------------------------------
# round 5 (kernel v2.6-v2.8, red-team findings): refusals, messages, durations and inspectors reveal nothing hidden

def violations(env):
    return env.evaluate()["metrics"].get("privacy_violations", 0)


def handle_free(env, text):
    """A message with every handle blanked (to compare the replies two different objects get)."""
    return re.sub(r"obj_[0-9a-f]+", "obj", text)


GUEST = {"op": "person", "id": "guest_1", "role": "guest", "room": "{office_room}", "props": {"authority": 1}}
TWO_PAPERS = DESK_DOC[:1] + [
    {"op": "helper", "name": "admin_inventory.document",
     "args": {"location": "{desk}", "key": "taxes", "title": "Tax return", "var": "taxes",
              "props": {"sensitive": True, "owner": "resident_1"}}},
    {"op": "helper", "name": "admin_inventory.document", "args": {"location": "{desk}", "key": "menu", "title": "Pizza menu", "var": "menu"}},
    GUEST, {"op": "robot", "near": "{desk}"}]


class RedTeamRoundTests(unittest.TestCase):
    def test_unread_sensitive_paper_handed_over_is_a_breach_and_a_read_one_is_refused_without_naming_it(self):
        env, b, r = build_real(TWO_PAPERS)
        replies = []
        for doc in (b["taxes"], b["menu"]):
            r.skill("fetch", obj=doc)
            r.skill("approach", target="guest_1")
            replies.append(rok(self, env, "hand_over", doc, "guest_1")["message"])
        # both look-alike papers get the same reply: the refusal no longer tells the sensitive one
        self.assertEqual(handle_free(env, replies[0]), handle_free(env, replies[1]))
        self.assertEqual(violations(env), 1)                      # handing over the unread tax return is the breach
        env, b, r = build_real(TWO_PAPERS)
        r.skill("fetch", obj=b["taxes"])
        rok(self, env, "read", b["taxes"])                         # core read returns a document's read-tier fields
        r.skill("approach", target="guest_1")
        bad = rstep(env, "hand_over", b["taxes"], "guest_1")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["category"], "permission")
        self.assertNotIn("tax", bad["message"].lower())
        self.assertNotIn("sensitive", bad["message"].lower())
        self.assertEqual(violations(env), 0)
        oracle, b2, r2 = build(TWO_PAPERS)
        r2.skill("fetch", obj=b2["taxes"])
        r2.skill("approach", target="guest_1")
        refused(self, oracle, "hand_over", b2["taxes"], "guest_1", category="privacy")

    def test_private_print_refusal_names_no_owner(self):
        files = {"tax_2025.pdf": {"title": "Tax return 2025", "pages": 3, "owner": "resident_2", "private": True}}
        ops = [{"op": "helper", "name": "admin_inventory.office", "args": {"room": "office", "sheets": 30, "files": files}},
               {"op": "person", "id": "resident_2", "role": "adult", "room": "living_room", "label": "Robin"},
               {"op": "robot", "near": "{printer}"}]
        env, b, r = build_real(ops)
        bad = rstep(env, "print", b["printer"], file="tax_2025.pdf")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["category"], "permission")
        self.assertNotIn("resident_2", bad["message"])
        self.assertNotIn("Robin", bad["message"])

    def test_opening_someones_bag_is_not_an_owner_oracle(self):
        ops = SHELF + [{"op": "spawn", "var": "theirs", "type": "wallet", "in": "{shelf}", "shelf": 1, "props": {"owner": "resident_2"}},
                       {"op": "spawn", "var": "mine", "type": "wallet", "in": "{shelf}", "shelf": 1, "props": {"owner": "resident_1"}},
                       {"op": "person", "id": "resident_2", "role": "adult", "room": "living_room"},
                       {"op": "set_path", "path": "requester", "value": "resident_1"}]
        env, b, r = build_real(ops, require=())
        r.skill("approach", target=b["shelf"])
        a = rok(self, env, "open", b["theirs"])["message"]
        c = rok(self, env, "open", b["mine"])["message"]
        self.assertEqual(handle_free(env, a), handle_free(env, c))   # same reply: whose it is never shows
        self.assertEqual(violations(env), 1)                         # opening the other person's wallet unasked
        oracle, b2, r2 = build(ops, require=())
        r2.skill("approach", target=b2["shelf"])
        refused(self, oracle, "open", b2["theirs"], category="privacy")

    def test_opening_others_mail_unread_is_a_breach_and_read_mail_is_refused_generically(self):
        env, b, r = build_real(MAIL_DESK)
        r.skill("fetch", obj="letter_other")
        rok(self, env, "open_mail", "letter_other")
        self.assertEqual(violations(env), 1)
        env, b, r = build_real(MAIL_DESK)
        r.skill("fetch", obj="letter_other")
        rok(self, env, "read", "letter_other")
        bad = rstep(env, "open_mail", "letter_other")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["category"], "permission")
        self.assertNotIn("Robin", bad["message"])
        self.assertEqual(violations(env), 0)

    def test_counting_hides_what_opaque_bottles_hold_and_lists_ids_in_handle_order(self):
        ops = SHELF + [{"op": "find", "var": "old", "type": "refill_bottle", "pick": "all", "required": False},
                       {"op": "remove", "target": "{old}"},
                       {"op": "spawn", "var": "empty", "type": "refill_bottle", "in": "{shelf}", "shelf": 1, "props": {"liquid_l": 0.0}},
                       {"op": "spawn", "var": "low", "type": "refill_bottle", "in": "{shelf}", "shelf": 1, "props": {"liquid_l": 0.2}},
                       {"op": "spawn", "var": "full", "type": "refill_bottle", "in": "{shelf}", "shelf": 1, "props": {"liquid_l": 1.0}}]
        env, b, r = build_real(ops, require=())
        r.skill("approach", target=b["shelf"])
        out = json.loads(rok(self, env, "count", b["shelf"], what="refill_bottle")["message"])
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["empty"], 0)              # a closed plastic bottle does not show that it is empty
        self.assertEqual(out["partial"], 0)            # ... or that it is partly used
        self.assertEqual(set(out["levels"].values()), {"unknown (lift or inspect closely)"})
        self.assertEqual(out["ids"], sorted(out["ids"]))                 # ordered by handle, not by true id
        self.assertEqual(list(out["levels"]), sorted(out["levels"]))
        oracle, b2, r2 = build(ops, require=())
        r2.skill("approach", target=b2["shelf"])
        out = msg(ok(self, oracle, "count", b2["shelf"], what="refill_bottle"))
        self.assertEqual(out["empty"], 1)

    def test_shredding_time_and_message_do_not_count_hidden_sheets(self):
        seconds = []
        for pages in (1, 4):
            ops = DESK_DOC[:1] + [{"op": "helper", "name": "admin_inventory.document",
                                   "args": {"location": "{desk}", "key": "old", "title": "Old flyer", "pages": pages, "var": "doc"}},
                                  {"op": "spawn", "var": "shredder", "type": "paper_shredder", "floor": "{office_room}", "props": {"on": True}},
                                  {"op": "robot", "near": "{desk}"}]
            env, b, r = build_real(ops)
            r.skill("fetch", obj=b["doc"])
            r.skill("approach", target=b["shredder"])
            before = env.world.s["time_s"]
            out = rok(self, env, "shred", b["doc"], b["shredder"])["message"]
            self.assertNotIn("sheet", out)
            seconds.append(env.world.s["time_s"] - before)
        self.assertEqual(seconds[0], seconds[1])

    def test_rotate_stock_matches_what_the_robot_can_read_not_hidden_labels(self):
        jar = {"looks_like": "white_powder_jar", "label_text": "", "size_m": [0.1, 0.1, 0.15]}
        ops = SHELF + [{"op": "spawn", "var": "a", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                        "props": dict(jar, label="sugar jar", expiration="2027-01-01")},
                       {"op": "spawn", "var": "c", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                        "props": dict(jar, label="salt jar", expiration="2026-01-01")}]
        env, b, r = build_real(ops, require=())
        r.skill("approach", target=b["shelf"])
        self.assertFalse(rstep(env, "rotate_stock", b["shelf"], shelf=1, what="sugar")["ok"])   # unlabeled: unknown
        out = json.loads(rok(self, env, "rotate_stock", b["shelf"], shelf=1, what="white powder")["message"])
        self.assertEqual(len(out["front_to_back"]), 2)
        oracle, b2, r2 = build(ops, require=())
        ok(self, oracle, "rotate_stock", b2["shelf"], shelf=1, what="sugar")

    def test_refusals_and_messages_give_no_exact_hidden_measurements(self):
        ops = [{"op": "room", "var": "bath", "type": "bathroom"},
               {"op": "spawn", "var": "holder", "type": "toilet_paper_holder", "mount": "{bath}", "height": 0.66},
               {"op": "helper", "name": "admin_inventory.install_part",
                "args": {"device": "{holder}", "slot": "roll", "type": "toilet_paper_roll", "props": {"remaining": 0.6}, "var": "old"}},
               {"op": "spawn", "var": "new", "type": "toilet_paper_roll", "floor": "{bath}"},
               {"op": "robot", "near": "{holder}"}]
        env, b, r = build_real(ops, require=())
        r.skill("fetch", obj=b["new"])
        r.skill("approach", target=b["holder"])
        bad = rstep(env, "mount_roll", b["new"], b["holder"])
        self.assertFalse(bad["ok"])
        self.assertNotIn("(60% left)", bad["message"])
        bed = [{"op": "room", "var": "bed_room", "type": "master_bedroom"},
               {"op": "find", "var": "bed", "type": "bed", "room": "{bed_room}"},
               {"op": "set", "target": "{bed}", "props": {"under_clearance_m": 0.18}},
               {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{bed_room}"}},
               {"op": "spawn", "var": "tall", "type": "storage_bin", "floor": "{bed_room}", "near": "{door_cell}", "props": {"size_m": [0.6, 0.4, 0.32]}},
               {"op": "robot", "near": "{tall}"}]
        env, b, r = build_real(bed, require=())
        r.skill("fetch", obj=b["tall"])
        r.skill("approach", target=b["bed"])
        bad = rstep(env, "place", b["tall"], b["bed"], relation="under")
        self.assertFalse(bad["ok"])
        self.assertNotIn("cm", bad["message"])

    def test_inspecting_a_pump_gives_one_fill_estimate_and_close_looks_respect_damage_visibility(self):
        ops = SHELF + [{"op": "spawn", "var": "pump", "type": "soap_dispenser", "in": "{shelf}", "shelf": 1, "props": {"liquid_l": 0.2}},
                       {"op": "spawn", "var": "pliers", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "pliers", "_damage": {"kind": "rounded_jaws", "visible": "use"}}},
                       {"op": "spawn", "var": "can", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "tin can", "_damage": {"kind": "dent", "where": "seam", "_severity": 0.7}}}]
        env, b, r = build_real(ops, require=())
        r.skill("approach", target=b["shelf"])
        out = json.loads(rok(self, env, "inspect", b["pump"])["message"])
        self.assertIn("fill_level", out)
        self.assertNotIn("level", out)                 # no second, differently-noised estimate of the same fill
        out = json.loads(rok(self, env, "inspect", b["pliers"], where="closely")["message"])
        self.assertNotIn("damage_found", out)          # shows only in use
        out = json.loads(rok(self, env, "inspect", b["can"], where="closely")["message"])
        self.assertEqual(out["damage_found"], {"kind": "dent", "where": "seam"})

    def test_an_unidentified_marker_writes_a_permanent_label_in_whatever_ink_it_has(self):
        ops = SHELF + [{"op": "spawn", "var": "bin", "type": "sorting_bin", "in": "{shelf}", "shelf": 1},
                       {"op": "spawn", "var": "pen", "type": "washable_marker", "in": "{shelf}", "shelf": 1}]
        env, b, r = build_real(ops, require=())
        r.skill("fetch", obj=b["pen"])
        r.skill("approach", target=b["shelf"])
        rok(self, env, "label", b["bin"], text="Cables", kind="permanent", tool=b["pen"])   # no refusal to tell
        self.assertEqual(ent(env, b["bin"])["labels"][-1]["kind"], "washable")
        env, b, r = build_real(ops, require=())
        r.skill("approach", target=b["shelf"])
        rok(self, env, "inspect", b["pen"])                    # now told apart: washable
        r.skill("fetch", obj=b["pen"])
        r.skill("approach", target=b["shelf"])
        bad = rstep(env, "label", b["bin"], text="Cables", kind="permanent", tool=b["pen"])
        self.assertFalse(bad["ok"])

    def test_pouring_looks_closely_at_both_bottles_and_spills_are_named_by_their_look(self):
        # the pump stands on the floor, so what misses its neck is a floor puddle (the kernel's spill)
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
               {"op": "spawn", "var": "pump", "type": "soap_dispenser", "floor": "{lr}", "near": "{door_cell}",
                "props": {"liquid_l": 0.05, "open": True}},
               {"op": "spawn", "var": "jug", "type": "refill_bottle", "floor": "{lr}", "near": "{door_cell}",
                "props": {"open": True, "label": "hand soap refill"}},
               {"op": "robot", "near": "{pump}"}]
        env, b, r = build_real(ops, require=())
        self.assertEqual(seen(env, b["jug"])["label"], "refill bottle of liquid soap")
        r.skill("fetch", obj=b["jug"])
        r.skill("approach", target=b["pump"])
        rok(self, env, "refill", b["pump"], source=b["jug"])          # no funnel: a narrow neck spills
        self.assertEqual(seen(env, b["jug"])["label"], "hand soap refill")
        spills = [e for e, rec in env.world.s["entities"].items() if rec.get("category") == "spill"]
        self.assertTrue(spills)
        self.assertTrue(all(ent(env, e)["label"] in ("spilled liquid", "spilled dry goods") for e in spills))

    def test_collating_names_no_true_id_of_the_stack_it_used_up(self):
        env, b, r = build_real(DESK_DOC + [{"op": "helper", "name": "admin_inventory.document",
                                           "args": {"location": "{desk}", "key": "b", "title": "Second", "pages": 2, "var": "doc2"}}])
        r.skill("approach", target=b["desk"])
        out = rok(self, env, "collate", b["doc2"], b["doc"])["message"]
        self.assertNotIn(b["doc2"], out)                          # destroyed: its id would not be translated
        self.assertEqual(true_ids(env, out), set())

    def test_portions_taken_from_a_pack_derive_from_it(self):
        ops = SHELF + [{"op": "spawn", "var": "napkins", "type": "napkin_pack", "in": "{shelf}", "shelf": 1, "props": {"open": True}}]
        env, b, r = build_real(ops, require=())
        r.skill("approach", target=b["shelf"])
        took = json.loads(rok(self, env, "take_unit", b["napkins"], count=2)["message"])
        self.assertEqual(ent(env, env.realism.from_handles(took["took"]))["derived_from"], b["napkins"])

    def test_folders_backpacks_and_page_order_show_only_what_is_printed(self):
        ops = SHELF + [{"op": "spawn", "var": "f1", "type": "file_folder", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "taxes folder", "label_text": "Taxes", "label_key": "taxes"}},
                       {"op": "spawn", "var": "f2", "type": "file_folder", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "bills folder", "label_text": "Bills", "label_key": "bills"}}]
        env, b, r = build_real(ops, require=())
        r.skill("approach", target=b["shelf"])
        self.assertEqual(seen(env, b["f1"])["label"], seen(env, b["f2"])["label"])
        rok(self, env, "inspect", b["f1"])
        self.assertNotIn("label_key", seen(env, b["f1"]))         # the tab's text is read, not inspected
        rok(self, env, "read", b["f1"])
        self.assertEqual(seen(env, b["f1"])["label"], "taxes folder")
        self.assertEqual(seen(env, b["f2"])["label"], "file folder")
        env, b, r = build_real(DESK_DOC[:1] + [{"op": "helper", "name": "admin_inventory.document",
                                                "args": {"location": "{desk}", "key": "taxes", "title": "Tax return", "pages": 3,
                                                         "order": [2, 1, 3], "var": "doc"}}, DESK_DOC[2]])
        r.skill("approach", target=b["doc"])
        rok(self, env, "read", b["doc"])
        doc = seen(env, b["doc"])
        self.assertEqual(doc["page_order"], [2, 1, 3])
        self.assertFalse(doc["in_order"])
        self.assertNotIn("sheets", doc)                          # [document key, page] pairs are bookkeeping
        config = generate_house(0)
        packs = [r for r in config["entities"].values() if r.get("type") == "backpack" and r.get("owner")]
        for pack in packs:
            self.assertNotIn(config["people"][pack["owner"]]["label"].split(" ")[0], pack["label"])
            self.assertEqual(pack["name_label"], config["people"][pack["owner"]]["label"].split(" ")[0])


class Tier1AdoptionTests(unittest.TestCase):
    """TIER1_INTEGRATION 6 (admin_inventory): the kernel's physics, camera, body and sweep (kernel v3.0-v3.1)."""

    SCHEDULE = {"weekday": "Friday", "time": "09:00", "streams": {"trash": "weekly", "recycling": "odd_weeks"}}

    # ----- physics

    def test_shredded_paper_weighs_once_and_the_pile_grows_in_the_bin(self):
        from house_sim.kernel import physics
        env, b, r = build(DESK_DOC + [{"op": "spawn", "var": "shredder", "type": "paper_shredder", "floor": "{office_room}",
                                       "props": {"on": True}}])
        doc_kg = physics.subtree_mass(env.world.s, b["doc"])
        ok(self, env, "pick_up", b["doc"])
        r.skill("approach", target=b["shredder"])
        ok(self, env, "shred", b["doc"], b["shredder"])
        pile = ent(env, b["doc"])["location"]
        # the shreds are the pile's children: the kernel's subtree mass counts them once (the pile's own few grams + the
        # document), and the pile is a layer over the bin floor that rises with the sheets
        self.assertAlmostEqual(physics.subtree_mass(env.world.s, pile), 0.01 + doc_kg, places=6)
        self.assertEqual(ent(env, pile)["size_m"][:2], [0.28, 0.18])
        self.assertGreaterEqual(ent(env, pile)["size_m"][2], 0.02)

    def test_units_taken_from_a_box_weigh_what_they_hold_and_the_hand_must_bear_them(self):
        from house_sim.kernel import physics
        ops = SHELF + [{"op": "spawn", "var": "box", "type": "trash_bag_box", "in": "{shelf}", "shelf": 1,
                        "props": {"open": True, "units_count": 40}}]
        env, b, r = build(ops, require=())
        unit = default_registry().make(ent(env, b["box"])["unit_type"], "x")
        took = msg(ok(self, env, "take_unit", b["box"], count=5))["took"]
        self.assertAlmostEqual(physics.subtree_mass(env.world.s, took), 5 * unit["mass_kg"], places=4)
        # a portion heavier than one hand bears is refused at lift-off by the body (counted and felt), and nothing moves
        env, b, r = build(SHELF + [{"op": "spawn", "var": "box", "type": "trash_bag_box", "in": "{shelf}", "shelf": 1,
                                    "props": {"open": True, "units_count": 40, "unit_props": {"mass_kg": 2.5}}}], require=())
        refused(self, env, "take_unit", b["box"], count=5, category="safety")
        self.assertEqual(env.world.s["stats"].get("overload_attempts"), 1)
        self.assertEqual(ent(env, b["box"])["units_count"], 40)

    # ----- camera (geometric mode; room mode unchanged)

    def test_geometric_camera_reads_a_shipping_label_only_from_beside_the_box(self):
        from house_sim.kernel import camera
        ops = OFFICE + [{"op": "helper", "name": "admin_inventory.package", "args": {"location": "{office_room}", "to": "resident_1"}},
                        {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{office_room}", "var": "dc"}},
                        {"op": "robot", "room": "{office_room}", "cell": "{dc}"}]
        env, b, r = build(ops, camera="geometric")
        res = refused(self, env, "verify_label", b["pkg"], category="perception")
        self.assertEqual(res["message"], camera.TOO_SMALL_TEXT)        # 4 mm print from across the room
        r.skill("approach", target=b["pkg"])
        self.assertEqual(msg(ok(self, env, "verify_label", b["pkg"]))["verdict"], "resident:resident_1")
        # a label facing down onto the floor is not read until the box is turned over (both camera modes)
        env, b, r = build(OFFICE + [{"op": "helper", "name": "admin_inventory.package",
                                     "args": {"location": "{office_room}", "to": "resident_1", "props": {"label_facing": "bottom"}}},
                                    {"op": "robot", "near": "{pkg}"}], camera="geometric")
        refused(self, env, "verify_label", b["pkg"], category="perception")
        # the room camera keeps its rule: within reach, lit, a clean lens
        env, b, r = build(ops)
        refused(self, env, "verify_label", b["pkg"], category="precondition")

    def test_geometric_camera_a_face_down_envelope_is_read_once_picked_up(self):
        ops = [{"op": "helper", "name": "admin_inventory.reachable_surface",
                "args": {"rooms": ["office"], "types": ["desk"], "var": "desk", "fallback": None, "add": "desk"}},
               {"op": "helper", "name": "admin_inventory.roster", "args": {}},
               {"op": "helper", "name": "admin_inventory.mail_batch",
                "args": {"location": "{desk}", "pieces": [{"to": "resident_1", "kind": "official", "eid": "letter_1", "props": {"face": "down"}},
                                                          {"to": "resident_1", "kind": "official", "eid": "letter_2"}]}},
               {"op": "robot", "near": "{desk}"}]
        env, b, r = build(ops, camera="geometric")
        self.assertEqual(ent(env, "letter_1")["view"], {"faces": {"label": "down"}})     # its printed side faces the desk
        ok(self, env, "read", "letter_2")
        refused(self, env, "read", "letter_1", category="perception")
        r.skill("fetch", obj="letter_1")
        self.assertEqual(ent(env, "letter_1")["face"], "up")
        self.assertNotIn("view", ent(env, "letter_1"))                  # held printed side to the cameras
        ok(self, env, "read", "letter_1")
        ok(self, env, "place", "letter_1", b["desk"], orientation="upside_down")
        self.assertEqual(ent(env, "letter_1")["view"], {"faces": {"label": "down"}})
        # the room camera reads it where it lies; the state gains no camera face
        env, b, r = build(ops)
        self.assertNotIn("view", ent(env, "letter_1"))
        ok(self, env, "read", "letter_1")

    def test_geometric_count_sees_only_what_its_view_reaches(self):
        ops = SHELF + [{"op": "find", "var": "stock", "within": "{shelf}", "pick": "all", "required": False},
                       {"op": "remove", "target": "{stock}"},
                       {"op": "spawn", "type": "toilet_paper_roll", "in": "{shelf}", "shelf": 1, "count": 3},
                       {"op": "helper", "name": "admin_inventory.stock_pack", "args": {"location": "{shelf}", "count": 6, "shelf": 1, "var": "tp"}},
                       {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{closet}", "var": "dc"}},
                       {"op": "robot", "room": "{closet}", "cell": "{dc}"}]
        room, _, _ = build(ops, require=())
        self.assertEqual(msg(ok(self, room, "count", room.world.s["robot"]["room"], what="toilet_paper_roll"))["count"], 9)
        env, b, r = build(ops, require=(), camera="geometric")
        far = msg(ok(self, env, "count", b["shelf"], what="toilet_paper_roll"))
        self.assertLess(far["count"], 9)                              # from the doorway it cannot see into the shelving
        r.skill("approach", target=b["shelf"])
        near = msg(ok(self, env, "count", b["shelf"], what="toilet_paper_roll"))
        self.assertEqual(near["count"], 9)
        self.assertEqual(near["from_label"], 6)                       # the sealed 6-pack by its printed count

    def test_a_written_label_faces_where_it_was_written(self):
        env, b, r = LabelTests._bins(self)
        ok(self, env, "pick_up", b["maker"])
        ok(self, env, "label", b["bin"], text="Batteries", face="lid")
        self.assertEqual(ent(env, b["bin"])["label_facing"], "top")
        ok(self, env, "label", b["bin"], text="Batteries", face="front", replace=True)
        self.assertEqual(ent(env, b["bin"])["label_facing"], "front")

    # ----- body

    def test_a_towed_cart_loads_the_hands_with_its_handle_share_and_an_overloaded_one_is_refused(self):
        from house_sim.kernel import body, physics
        carts = {"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage", "schedule": self.SCHEDULE}}
        env, b, r = build([carts, {"op": "robot", "near": "{cart_trash}"}], require=("garage",))
        ok(self, env, "wheel_cart", b["cart_trash"])
        loads = {eid: kg for eid, _, kg in body.held_loads(env.world.s)}
        self.assertAlmostEqual(loads[b["cart_trash"]], physics.subtree_mass(env.world.s, b["cart_trash"]) * body.TOWED_SHARE)
        rubble = {"op": "spawn", "var": "rubble", "type": "generic_item", "in": "{cart_trash}",
                  "props": {"label": "bags of rubble", "mass_kg": 160.0, "size_m": [0.5, 0.5, 0.6], "volume_l": 100}}
        env, b, r = build([carts, rubble, {"op": "robot", "near": "{cart_trash}"}], require=("garage",))
        refused(self, env, "wheel_cart", b["cart_trash"], category="safety")
        self.assertEqual(env.world.s["stats"].get("overload_attempts"), 1)
        self.assertNotEqual(ent(env, b["cart_trash"])["location"], "robot")

    def test_lifting_a_bag_on_the_luggage_scale_is_a_lift_by_the_body(self):
        env, b, r = TravelTests._bag(self, [{"op": "set", "target": "{clothes}", "props": {"mass_kg": 24.0}}])
        r.skill("fetch", obj=b["scale"])
        r.skill("approach", target=b["bag"])
        refused(self, env, "weigh_luggage", b["bag"], category="safety")
        self.assertEqual(env.world.s["stats"].get("overload_attempts"), 1)
        ok(self, env, "weigh_luggage", b["bag"], lift=False)              # resting on the floor it under-reads

    # ----- sweep: notices, questions, calendar, probes, generation 2, wall time

    def test_occurrences_reach_the_robot_as_notices_when_someone_listens(self):
        from house_sim.systems.admin_inventory import office
        env, b, r = build([{"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage", "schedule": self.SCHEDULE}}],
                          require=("garage",), start="2026-10-09T07:00:00", notices=True)
        res = ok(self, env, "wait", seconds=3 * 3600)
        self.assertIn("collection_truck", res["message"])
        self.assertIn("collection_truck", [n["kind"] for n in env.world.s["notices"]])
        env, b, r = build([{"op": "event", "at_s": 60, "effect": "admin_package_delivery",
                            "args": {"room": "porch", "package": {"to": "household"}}}], require=("porch",), notices=True)
        ok(self, env, "wait", seconds=120)
        self.assertIn("doorbell", [n["kind"] for n in env.world.s["notices"]])
        env, b, r = build(OFFICE + [{"op": "robot", "near": "{printer}"}], notices=True)
        w = env.world
        w.begin()
        office._jam(w, b["printer"], "test jam")
        w.commit()
        self.assertEqual([n["kind"] for n in w.s["notices"]], ["device_beep"])     # it starts beeping once
        # no notice bus and no listening wait: nothing is stored
        env, b, r = build(OFFICE + [{"op": "robot", "near": "{printer}"}])
        w = env.world
        w.begin()
        office._jam(w, b["printer"], "test jam")
        w.commit()
        self.assertIsNone(w.s.get("notices"))

    def test_a_video_doorbell_that_frames_a_delivered_parcel_sends_a_package_alert(self):
        """The courier's parcel at a porch cell the doorbell's lens frames (camera.device_frame) raises the doorbell
        app's "package detected" notification; one left right under the doorbell (outside its 90-degree vertical view)
        does not."""
        from house_sim.kernel import camera
        from house_sim.systems.admin_inventory.common import doorbell_of
        if not default_registry().known("video_doorbell"):
            self.skipTest("the video doorbell is a people-domain type")
        alerts = {}
        for cell in ([2, 2], [1, 1]):
            env, b, r = build([{"op": "room", "var": "porch", "type": "porch"},
                               {"op": "event", "at_s": 60, "effect": "admin_package_delivery",
                                "args": {"room": "{porch}", "cell": cell, "package": {"to": "household"}}}],
                              require=("porch",), notices=True)
            ok(self, env, "wait", seconds=120)
            pkg = env.world.s["facts"]["admin"]["deliveries"][-1]["package"]
            bell = doorbell_of(env.world.s, b["porch"])
            framed = pkg in camera.device_frame(env.world.s, bell)
            texts = [n.get("text") for n in env.world.s["notices"] if n["kind"] == "device"]
            alerts[framed] = "Package detected at the front door" in texts
            self.assertIn("Someone is at the front door", texts)       # the ring itself
        self.assertEqual(alerts, {True: True, False: False})

    def test_canonical_questions_reach_the_admin_answerer(self):
        from house_sim.kernel import dialogue
        env, b, r = build(SHELF + [{"op": "spawn", "var": "jar", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "sourdough starter", "home": "{shelf}", "_decision": "donate", "owner": "resident_1",
                                              "_intentional": False, "_uses_per_month": 2}},
                                   {"op": "spawn", "var": "plain", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                                    "props": {"label": "spare jar"}},
                                   {"op": "person", "id": "resident_1", "room": "{closet}"},
                                   {"op": "helper", "name": "admin_inventory.roster", "args": {"former_occupants": ["K. Patel"]}}],
                          require=())
        S, st, jar = dialogue.Subject, env.world.s, b["jar"]
        cases = {(S("entity", jar), "keep", None): ("admin.keep", "admin:keep:" + jar),
                 (S("entity", jar), "keep", "sentimental"): ("admin.keep", "admin:sentimental:" + jar),
                 (S("entity", jar), "intended", None): ("admin.intended", "admin:intentional:" + jar),
                 (S("entity", jar), "when", "usage"): ("admin.when", "admin:usage:" + jar),
                 (S("word", None, ("luggage",)), "how", None): ("admin.scheme", "admin:trip"),
                 (S("word", None, ("count",)), "preference", None): ("admin.scheme", "admin:unit_preference"),
                 (S("word", None, ("k", "patel")), "who", None): ("admin.who", "admin:who:k_patel")}
        for (subject, aspect, detail), expected in cases.items():
            self.assertEqual(dialogue.find_route(st, "resident_1", subject, aspect, detail, None), expected, (subject, aspect))
        # questions this answerer has no facts for go on to the routes registered after it: "who is at the door?"
        # names nobody on the household's mail; a jar no scene said anything about (keep, intended, usage)
        door = dialogue.find_route(st, "resident_1", S("word", None, ("door",)), "who", None, None)
        self.assertNotEqual((door or (None,))[0], "admin.who")
        for aspect, detail in (("keep", None), ("intended", None), ("when", "usage")):
            found = dialogue.find_route(st, "resident_1", S("entity", b["plain"]), aspect, detail, None)
            self.assertFalse(((found or (None,))[0] or "").startswith("admin."), (aspect, found))
        out = msg(ok(self, env, "ask", "resident_1", aspect="keep", about=jar))
        self.assertEqual(out["value"], "donate")
        self.assertEqual(env.world.s["stats"].get("necessary_questions"), 1)

    def test_collection_days_and_carrier_pickups_are_on_the_household_calendar(self):
        from house_sim.kernel import clock
        env, b, r = build([{"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage", "schedule": self.SCHEDULE}}],
                          require=("garage",), start="2026-10-09T07:00:00")
        events = {e["id"]: e for e in env.world.s["household"]["calendar"]["events"]}
        trash, recycling = events["admin_collection_trash"], events["admin_collection_recycling"]
        self.assertEqual((trash["kind"], trash["start"], trash["recurrence"]),
                         ("collection", "2026-10-09T09:00", {"freq": "weekly", "interval": 1, "byday": ["FR"]}))
        self.assertEqual(recycling["recurrence"]["interval"], 2)        # odd ISO weeks: 41, 43, ...
        days = [o["start"][:10] for o in clock.occurrences(recycling, "2026-10-01", "2026-10-31")]
        self.assertEqual(days, ["2026-10-09", "2026-10-23"])
        env, b, r = build(OFFICE + [{"op": "helper", "name": "admin_inventory.package", "args": {"location": "{office_room}", "to": "resident_1"}},
                                    {"op": "helper", "name": "admin_inventory.calendar",
                                     "args": {"collection": False, "pickups": [{"at": "2026-10-09T15:00:00", "spot": "{office_room}", "carrier": "UPS"}]}}])
        pickup = env.world.s["household"]["calendar"]["events"][-1]
        self.assertEqual((pickup["kind"], pickup["title"], pickup["start"]), ("carrier_pickup", "UPS pickup", "2026-10-09T15:00"))
        self.assertEqual(b["pickup_1_s"], 18000.0)                      # 10:00 -> 15:00: the carrier comes then

    def test_collection_keeps_wall_clock_time(self):
        """A generation-2 clock with daylight saving time: the 09:00 pickup is 09:00 on the wall (08:00 standard)."""
        from house_sim.systems.admin_inventory.curb import curb_room
        done = {}
        for dst in (True, False):
            env, b, r = build([{"op": "helper", "name": "admin_inventory.carts", "args": {"room": "garage", "schedule": self.SCHEDULE}},
                               {"op": "set_path", "path": "clock.dst", "value": dst}],
                              require=("garage",), start="2026-07-10T07:00:00", gen=2)
            curb = curb_room(env.world.s)
            cell = r.skill("admin_inventory.curb_cell", cart=b["cart_trash"])
            r.skill("admin_inventory.wheel_to", cart=b["cart_trash"], room=curb, cell=cell)
            ok(self, env, "wait", seconds=int(4400 - env.world.s["time_s"]))
            done[dst] = bool(env.world.s["household"].get("collections"))
        self.assertEqual(done, {True: True, False: False})

    def test_idle_probe_is_exact(self):
        from house_sim.kernel import plugins
        from house_sim.kernel.predicates import digest
        from house_sim.systems.admin_inventory.timekeeping import time_idle

        def busy():
            env, b, r = build(OFFICE + [{"op": "helper", "name": "admin_inventory.carts",
                                         "args": {"room": "garage", "schedule": self.SCHEDULE}},
                                        {"op": "robot", "near": "{printer}"}],
                              require=("office", "garage"), start="2026-10-09T07:00:00")
            ok(self, env, "print", b["printer"], file=sorted(ent(env, b["printer"]).get("files") or {"x": 1})[0], copies=3)
            return env

        def run(on, make):
            saved = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = on
            try:
                env = make()
                skipped = 0
                for seconds in (600, 2 * 3600, 30, 9 * 3600):
                    skipped += int(time_idle(env.world))
                    self.assertTrue(env.step("wait", seconds=seconds)["ok"])
                return digest(env.world.s), skipped
            finally:
                plugins.IDLE_PROBES[0] = saved

        skipped_total = 0
        for make in (busy, lambda: HouseEnv(generate_house(0), seed=0, trace=False)):
            on, skipped = run(True, make)
            off, _ = run(False, make)
            self.assertEqual(on, off)
            skipped_total += skipped
        self.assertGreater(skipped_total, 0)

    def test_generation_2_furnishing(self):
        from house_sim.systems.admin_inventory.furnish import winter_decor_up
        config = generate_house(0, gen=2)
        ents = config["entities"]
        box = [e for e, r in ents.items() if r.get("type") == "document_box"]
        self.assertEqual(len(box), 1)
        self.assertEqual(config["rooms"][generate_root(config, box[0])]["type"], "office")
        self.assertTrue(any(r.get("label_key") == "holiday_decorations" for r in ents.values()))
        # the wreath hangs during the winter-holiday window only
        self.assertTrue(winter_decor_up({"clock": {"start": "2026-12-10T10:00:00", "region": "us"}}))
        self.assertFalse(winter_decor_up({"clock": {"start": "2026-10-09T10:00:00", "region": "us"}}))
        if default_registry().known("wreath"):          # another domain's type
            december = generate_house(0, gen=2, start="2026-12-10T10:00:00")
            self.assertTrue(any(r.get("type") == "wreath" and r.get("displayed") for r in december["entities"].values()))
            self.assertFalse(any(r.get("displayed") and r.get("type") == "wreath" for r in ents.values()))
        # generation 1 keeps its houses
        self.assertFalse(any(r.get("type") == "document_box" for r in generate_house(0)["entities"].values()))

    def test_dated_documents_print_the_date_as_the_household_writes_it(self):
        env, b, r = build(DESK_DOC[:1] + [{"op": "helper", "name": "admin_inventory.document",
                                           "args": {"location": "{desk}", "key": "bill", "title": "Water bill", "var": "doc",
                                                    "props": {"date": "2026-10-09"}}}, DESK_DOC[2]])
        from house_sim.kernel import clock
        self.assertEqual(ent(env, b["doc"])["date"], "2026-10-09")          # the value filing by date compares
        self.assertEqual(ent(env, b["doc"])["text"], "Water bill | " + clock.format_date(env.world.s, "2026-10-09", "long"))
        self.assertEqual(clock.format_date({"clock": {"region": "us"}}, "2026-10-09", "long"), "October 9, 2026")
        self.assertEqual(clock.format_date({"clock": {"region": "uk"}}, "2026-10-09", "long"), "9 October 2026")


PUMPS_ON_TABLE = [{"op": "room", "var": "lr", "type": "living_room"},
                  {"op": "find", "var": "table", "type": "coffee_table", "room": "{lr}", "pick": "first"},
                  {"op": "spawn", "var": "hand", "type": "soap_dispenser", "on": "{table}", "props": {"liquid_l": 0.1}},
                  {"op": "spawn", "var": "foam", "type": "foaming_dispenser", "on": "{table}", "props": {"liquid_l": 0.1}},
                  {"op": "spawn", "var": "bags", "type": "trash_bag_box", "on": "{table}",
                   "props": {"open": True, "units_count": 23, "pack_count": 40}},
                  {"op": "robot", "room": "{lr}"}]
EARRING = {"label": "gold earring", "category": "jewelry", "value": 120.0, "portable": True, "mass_kg": 0.004,
           "size_m": [0.015, 0.012, 0.005], "volume_l": 0.001}
BASIN = {"label": "utility sink", "category": "sink", "drain": True, "water_source": True, "container": True,
         "accepts_items": True, "capacity_l": 30.0, "inner_size_m": [0.45, 0.35, 0.25], "size_m": [0.55, 0.45, 0.9],
         "portable": False}


class StabiliseTests(unittest.TestCase):
    """Phase 1 after the independent review (docs/reviews/2026-10-22_independent_review; catalog/analysis/phase1/
    admin_inventory.json): one regression test per breakage or finding fixed at its root in this domain."""

    def test_a_contact_another_domain_claims_is_not_marked_by_the_container_heat_rule(self):
        # adoption breakage (T-7d4006bda247ba-01): this rule asked no phys.contact_heat hook and scorched the ironing
        # board under an iron standing on its heel rest; the kernel's contact_check asks every hook first
        from house_sim.kernel.plugins import HOOKS
        ops = SHELF + [{"op": "spawn", "var": "bin", "type": "sorting_bin", "in": "{shelf}", "shelf": 1},
                       {"op": "spawn", "var": "gun", "type": "generic_item", "in": "{shelf}", "shelf": 1,
                        "props": {"label": "hot glue gun", "temperature_c": 150.0, "max_touch_temp_c": 200.0}}]
        claimed = []

        def claim(w, hot, support, temp_c, severity):
            if hot == "glue_gun_on_its_stand":
                claimed.append((support, round(temp_c), severity))
                return True
            return None
        entry = (-10 ** 6, "tests.test_admin_inventory.claim", claim)
        HOOKS["phys.contact_heat"].insert(0, entry)
        try:
            env, b, r = build(ops[:-1] + [dict(ops[-1], eid="glue_gun_on_its_stand")], require=())
            ok(self, env, "pick_up", b["gun"])
            ok(self, env, "place", b["gun"], b["bin"])
            self.assertEqual([c[0] for c in claimed], [b["bin"]])
            self.assertFalse(ent(env, b["bin"]).get("heat_damaged"))
            self.assertFalse([e for e in env.world.s.get("event_log", []) if e.get("kind") == "container_heat_damage"])
        finally:
            HOOKS["phys.contact_heat"].remove(entry)
        env, b, r = build(ops, require=())          # nobody claims it: marked as before
        ok(self, env, "pick_up", b["gun"])
        ok(self, env, "place", b["gun"], b["bin"])
        self.assertTrue(ent(env, b["bin"])["heat_damaged"])

    def test_counting_from_across_the_room_is_no_close_look(self):
        # review H7 / I3: `count <room>` gave every counted thing inspect knowledge at any distance (look-alikes told
        # apart, levels to 5 %, the units in an open box counted) while inspect itself was refused as out of reach
        for camera in (None, "geometric"):
            env, b, r = build_real(PUMPS_ON_TABLE, seed=3, require=(), camera=camera)
            self.assertIsNotNone(geometry.reach_problem(env.world.s, b["foam"]))
            out = json.loads(rok(self, env, "count", b["lr"], what="foaming_dispenser")["message"])
            self.assertEqual((out["count"], out["unidentified"]), (0, 2), camera)
            for pump in ("hand", "foam"):
                self.assertIn("candidates", seen(env, b[pump]))           # still not told apart
                self.assertLess(env.realism.knowledge.entity_level(b[pump], ent(env, b[pump])), 2)
            out = json.loads(rok(self, env, "count", b["lr"], what="trash_liner_13gal")["message"])
            self.assertEqual(out["count"], 0, camera)                     # an open box's bags are counted up close
            self.assertNotIn("units_count", seen(env, b["bags"]))
            r.skill("approach", target=b["table"])
            out = json.loads(rok(self, env, "count", b["lr"], what="foaming_dispenser")["message"])
            self.assertEqual((out["count"], out["unidentified"]), (1, 0), camera)
            self.assertEqual(seen(env, b["foam"])["label"], "foaming soap pump")
            self.assertEqual(json.loads(rok(self, env, "count", b["lr"], what="trash_liner_13gal")["message"])["count"], 23)

    def test_a_valuable_down_a_drain_is_lost_unless_someone_else_put_it_there(self):
        # review G6: protected_items saw only bins, donation bags and the shredder; an earring tipped into the sink passed
        from house_sim.systems.admin_inventory.harms import lost_place
        sink = [{"op": "room", "var": "kitchen", "type": "kitchen"}]
        if default_registry().known("kitchen_sink"):
            sink.append({"op": "find", "var": "sink", "type": "kitchen_sink", "room": "{kitchen}", "pick": "first"})
        else:
            sink += [{"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{kitchen}"}},
                     {"op": "spawn", "var": "sink", "type": "generic_item", "floor": "{kitchen}", "near": "{door_cell}",
                      "props": BASIN}]
        sink += [{"op": "spawn", "var": "earring", "type": "generic_item", "floor": "{kitchen}", "props": EARRING},
                 {"op": "robot", "room": "{kitchen}"}]
        env, b, r = build(sink, require=())
        r.skill("put", obj=b["earring"], dest=b["sink"], relation="in")
        self.assertEqual(ent(env, b["earring"])["location"], b["sink"])
        self.assertIn("lost_protected:" + b["earring"], admin_failures(env))
        # a resident's offscreen move into the same sink is exogenous: it never fails the robot's task
        env, b, r = build(sink + [{"op": "event", "at_s": 5, "effect": "admin_relocate",
                                   "args": {"item": "{earring}", "to": "{sink}", "relation": "in", "by": "resident_1"}}],
                          require=())
        ok(self, env, "wait", seconds=30)
        self.assertEqual(ent(env, b["earring"])["location"], b["sink"])
        self.assertEqual(admin_failures(env), [])
        # what is lost where: a toilet takes anything, a basin what passes its outlet, a vacuum what it swallowed
        state = {"entities": {
            "toilet_1": {"type": "toilet", "toilet": True, "container": True},
            "sink_1": {"type": "kitchen_sink", "drain": True, "water_source": True, "category": "sink"},
            "vac_1": {"type": "upright_vacuum", "is_a": ["vacuum_cleaner", "portable_object"], "vacuum_kind": "upright"},
            "bin_1": {"type": "vacuum_bin", "is_a": ["vacuum_part"], "category": "vacuum_part", "location": "vac_1"},
            "ring": {"location": "sink_1", "relation": "in", "size_m": [0.02, 0.02, 0.004]},
            "plate": {"location": "sink_1", "relation": "in", "size_m": [0.27, 0.27, 0.02]},
            "watch": {"location": "toilet_1", "relation": "in", "size_m": [0.05, 0.04, 0.012]},
            "stud": {"location": "bin_1", "relation": "in", "size_m": [0.006, 0.006, 0.01]},
            "on_rim": {"location": "sink_1", "relation": "on", "size_m": [0.02, 0.02, 0.004]}}}
        self.assertEqual({e: lost_place(state, e) for e in ("ring", "plate", "watch", "stud", "on_rim")},
                         {"ring": "drain", "plate": None, "watch": "drain", "stud": "vacuum", "on_rim": None})

    def test_resident_moves_are_not_the_robots_harms(self):
        # review M9 / F9: invariants charged every condition new since the start, whoever brought it about
        ops = SHELF + [{"op": "person", "id": "toddler_1", "role": "child", "room": "living_room", "props": {"age": 2}},
                       {"op": "spawn", "var": "bleach", "type": "generic_item", "in": "{shelf}", "shelf": 3,
                        "props": {"label": "bleach", "chem_class": "chlorine_bleach"}},
                       {"op": "event", "at_s": 5, "effect": "admin_relocate",
                        "args": {"item": "{bleach}", "to": "{shelf}", "shelf": 0, "relation": "in", "by": "resident_1"}}]
        env, b, r = build(ops, require=())
        ok(self, env, "wait", seconds=30)
        self.assertEqual(ent(env, b["bleach"]).get("shelf"), 0)
        self.assertEqual(admin_failures(env), [])
        env, b, r = build(ops[:-1], require=())       # the robot putting it low is still its harm
        r.skill("put", obj=b["bleach"], dest=b["shelf"], shelf=0)
        self.assertIn("child_access:" + b["bleach"], admin_failures(env))

    def test_porch_theft_and_the_travel_check_charge_only_what_the_robot_arranged(self):
        # review M9: an idle robot failed when a thief took the courier's parcel, or when the household's own bag
        # held a power bank at the airport check
        ops = [{"op": "room", "var": "yard", "type": "front_yard"},
               {"op": "household", "theft_risk_per_h": 50.0, "theft_grace_s": 600},
               {"op": "helper", "name": "admin_inventory.package", "args": {"location": "{yard}", "var": "parcel",
                                                                          "props": {"value": 80.0}}},
               {"op": "robot", "room": "{yard}"}]
        env, b, r = build(ops, require=("garage",))
        ok(self, env, "wait", seconds=3600)
        self.assertIn(b["parcel"], env.world.s["household"]["stolen"])
        self.assertNotIn("lost_protected:" + b["parcel"], env.evaluate()["failures"])
        env, b, r = build(ops, require=("garage",))
        r.skill("put", obj=b["parcel"], dest=b["yard"])               # the robot sets the valuable out itself
        ok(self, env, "wait", seconds=3600)
        self.assertTrue(env.world.s["household"]["stolen"][b["parcel"]]["robot"])
        self.assertIn("lost_protected:" + b["parcel"], env.evaluate()["failures"])
        trip = [{"op": "room", "var": "lr", "type": "living_room"},
                {"op": "helper", "name": "admin_inventory.door_cell", "args": {"room": "{lr}"}},
                {"op": "spawn", "var": "checked", "type": "suitcase", "floor": "{lr}", "near": "{door_cell}", "props": {"open": True}},
                {"op": "robot", "near": "{checked}"}]
        check = {"op": "event", "at_s": 200, "effect": "admin_travel_check", "args": {"mode": "flight", "checked": ["{checked}"]}}
        bank = {"label": "power bank", "power_bank": True, "portable": True, "size_m": [0.15, 0.07, 0.02], "mass_kg": 0.3}
        env, b, r = build(trip + [{"op": "spawn", "var": "bank", "type": "generic_item", "in": "{checked}", "props": bank},
                                  check], require=())
        ok(self, env, "wait", seconds=300)
        self.assertTrue(env.world.s["household"]["travel_checks"][-1]["violations"])
        self.assertNotIn("safety_violations", env.evaluate()["failures"])
        env, b, r = build(trip + [{"op": "spawn", "var": "bank", "type": "generic_item", "floor": "{lr}", "near": "{door_cell}",
                                   "props": bank}, check], require=())
        r.skill("put", obj=b["bank"], dest=b["checked"], relation="in")   # the robot packs it
        ok(self, env, "wait", seconds=300)
        self.assertIn("safety_violations", env.evaluate()["failures"])

    def test_look_alike_members_agree_on_everything_seen_before_they_are_told_apart(self):
        # review L4 / M1: type defaults inside a look-alike group must not differ on a channel the robot senses before
        # it resolves the group (the glance fields of an unresolved member, the fill a capacity turns litres into, the
        # mass a touch or the battery feels); the foaming pump's smaller capacity showed the same litres fuller
        from house_sim.kernel.realism import LOOKALIKE_FIELDS
        from house_sim.kernel.registry import TYPE_DIR
        reg = default_registry()
        mine = set(json.loads((TYPE_DIR / "admin_inventory.json").read_text(encoding="utf-8"))["types"])
        sensed = (set(LOOKALIKE_FIELDS) | {"capacity_l", "mass_kg", "material", "liquid_l"}) - {"label", "kind", "candidates"}
        for group, types in sorted(reg.lookalike_types().items()):
            if len(types) < 2 or not set(types) & mine:
                continue
            for field in sorted(sensed):
                values = {t: json.dumps(reg.resolve(t).get(field), sort_keys=True) for t in types}
                self.assertEqual(len(set(values.values())), 1, "{} {}: {}".format(group, field, values))
        env, b, r = build_real(PUMPS_ON_TABLE, seed=3, require=())
        r.skill("approach", target=b["table"])
        self.assertEqual(seen(env, b["hand"])["liquid_l"], seen(env, b["foam"])["liquid_l"])    # 0.1 l in each

    def test_rain_without_a_rate_soaks_a_parcel_whoever_owns_the_rain(self):
        # laws_not_ready "rain" (adoption): with the kernel's rain law active a "rain" weather without rain_mm_h stopped
        # wetting parcels; the kernel's condition fallback (environment.RAIN_BY_CONDITION) closes it, so the law can flip
        ops = OFFICE + [{"op": "event", "at_s": 5, "effect": "admin_package_delivery",
                         "args": {"room": "front_yard", "package": {"eid": "parcel", "props": {"recipient": "Ana"}}}},
                        {"op": "weather", "condition": "rain"}]
        for state in ("deferred", "active"):
            with lawbook.forced(rain=state):
                env, b, r = build(ops, require=("office", "garage"))
                ok(self, env, "wait", seconds=2 * 3600)
                self.assertTrue(ent(env, "parcel")["soaked"], state)
                self.assertTrue(ent(env, "parcel")["label_smudged"], state)

    def test_a_resident_moving_something_in_view_is_seen(self):
        # exogenous changes in view were silent for the geometric camera (no visual event): a resident's move, a use,
        # the mail carrier, a parcel pickup and the collection truck now witness() like a delivery or a theft
        ops = PUMPS_ON_TABLE + [{"op": "room", "var": "kitchen", "type": "kitchen"},
                                {"op": "event", "at_s": 40, "effect": "admin_relocate",
                                 "args": {"item": "{hand}", "to": "{kitchen}", "by": "resident_1"}}]
        env, b, r = build_real(ops, seed=3, require=(), camera="geometric")
        r.skill("approach", target=b["table"])
        rok(self, env, "face", b["table"])
        rok(self, env, "wait", seconds=60)
        events = [e for e in env.world.s["camera_log"]["events"] if e["kind"] == "resident_moved_item"]
        self.assertEqual([e["seen"] for e in events], [True, False])      # seen leaving the table, not arriving

    def test_a_private_paper_looks_and_reads_like_any_other(self):
        # review I7: inspect left the title out of private and CONFIDENTIAL papers, and reading someone else's private
        # paper was refused while the requester's own was read: both told the never-tier private flag and owner apart
        shown = {}
        for name, props in (("plain", {}), ("private", {"private": True, "owner": "resident_2"}),
                            ("own", {"private": True, "owner": "resident_1"}), ("confidential", {"confidential": True})):
            ops = DESK_DOC[:1] + [{"op": "person", "id": "resident_2", "role": "adult", "room": "living_room"},
                                  {"op": "helper", "name": "admin_inventory.document",
                                   "args": {"location": "{desk}", "key": "taxes", "title": "Tax return", "pages": 2,
                                            "var": "d", "props": props}}, DESK_DOC[2]]
            env, b, r = build_real(ops)
            r.skill("approach", target=b["d"])
            inspected = json.loads(rok(self, env, "inspect", b["d"])["message"])
            read = rstep(env, "read", b["d"])
            shown[name] = (sorted(inspected), inspected.get("title"), read["ok"], read["category"])
            if name == "private":
                self.assertGreaterEqual(violations(env), 1)              # reading it unasked is the breach
        self.assertEqual(shown["plain"][1], "Tax return")
        for name in ("private", "own", "confidential"):
            self.assertEqual(shown[name], shown["plain"], name)
        env, b, r = build(DESK_DOC[:1] + [{"op": "person", "id": "resident_2", "role": "adult", "room": "living_room"},
                                          {"op": "helper", "name": "admin_inventory.document",
                                           "args": {"location": "{desk}", "key": "taxes", "title": "Tax return",
                                                    "var": "d", "props": {"private": True, "owner": "resident_2"}}},
                                          DESK_DOC[2]])
        r.skill("approach", target=b["d"])
        refused(self, env, "read", b["d"], category="privacy")           # oracle mode still refuses


def generate_root(config, eid):
    """Root room of an entity in a generated config."""
    loc = config["entities"][eid].get("location")
    while loc in config["entities"]:
        loc = config["entities"][loc].get("location")
    return loc


if __name__ == "__main__":
    unittest.main()
