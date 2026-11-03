"""Manipulation's verification-hardening package (docs/design/VERIFY_HARDENING.md 6.2-6.5, 8.2, 10.3, 13.3; WP-D
manipulation): field classes (L-WATCH), writer classes and causes of the systems, the partner's own steps, relation
records kept true after any move (world rule and derived fields), mess flows of refuse this domain makes and moves,
settings skills, truth mappings, the dialogue route of a thing gone out of view, and the look-alike wrap.

Run isolated:   HOUSE_SIM_DOMAINS=core,manipulation python -m unittest tests/test_manipulation_hardening.py
Strict journal: HOUSE_SIM_STRICT=1 python -m unittest tests/test_manipulation_hardening.py
"""
import json
import os
import sys
import unittest
from pathlib import Path

from house_sim.kernel import dialogue, evaluation, ledger, plugins, speech
from house_sim.kernel.plugins import load_domains
from house_sim.tasks.runner import Runner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_manipulation import Mini, _setup, ent, ok  # noqa: E402

load_domains()
ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "house_sim" / "data" / "verify" / "watch" / "manipulation.json"


def flows(env, kind=None):
    return [f for f in (env.world.s.get("logs") or {}).get("mess_flows") or ()
            if kind is None or f.get("kind") == kind]


def hardened(**block):
    block.setdefault("envelope", {})
    return {"success": [], "failure": [], "hardening": block}


def _waste_parcels(state):
    """A waste_item measure for these tests (cleaning owns the real one): every waste entity where it lies."""
    from house_sim.systems.core import mess_location, mess_parcel
    out = []
    for eid in sorted(state.get("entities") or {}):
        parcel = mess_parcel(state, eid)
        if parcel is None or parcel[0] != "waste_item":
            continue
        rec = state["entities"][eid]
        loc = mess_location(state, rec.get("location"), rec.get("relation"))
        if loc is not None:
            out.append((tuple(loc), parcel[2], parcel[1]))
    return out


# ======================================================================================
class FieldClassTests(unittest.TestCase):
    """6.3, lint L-WATCH part 2: every property of this domain's type data is classified explicitly (an error since
    this package landed), and the classes this domain gives are the ones it means."""

    def test_every_type_property_is_classified(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import field_census
        finally:
            sys.path.pop(0)
        self.assertEqual(field_census.unclassified(["manipulation"]), {"manipulation": []})

    def test_the_watch_file_is_well_formed(self):
        data = json.loads(WATCH.read_text(encoding="utf-8"))
        self.assertEqual(sorted(data), ["about", "fields", "types", "water_sensitive", "writes"])
        classes = {"placement", "setting", "condition", "identity", "content", "process", "bookkeeping"}
        types = json.loads((ROOT / "house_sim" / "data" / "types" / "manipulation.json").read_text(encoding="utf-8"))["types"]
        for name, spec in data["fields"].items():
            spec = {"class": spec} if isinstance(spec, str) else spec
            self.assertIn(spec["class"], classes, name)
            if spec["class"] == "condition":
                self.assertIn(spec.get("worse"), ("true", "false", "up", "down"), name)
        for name in data["writes"]:
            self.assertIn(name, data["fields"], name)
        for type_name in list(data["types"]) + data["water_sensitive"]:
            self.assertIn(type_name, types, type_name)

    def test_classes_for_this_domains_types(self):
        cases = [("price_tag", "text", "identity"), ("trivet", "heat_limit_c", "identity"),
                 ("moving_box_small", "load_rating_text", "identity"), ("collectible_figure_boxed", "packaging_intact",
                                                                        "condition"),
                 ("insulated_bag", "zipper.closed_frac", "setting"), ("velcro_strap", "velcro.engaged", "setting"),
                 ("resistance_band", "tangle.crossings", "condition"), ("resistance_band", "tangle.with", "setting"),
                 ("game_box", "components.dice", "content"), ("dumbbell_rack", "receptacles.saddle_5kg_a",
                                                              "bookkeeping"),
                 ("gift_wrap_roll", "length_m", "content"), ("jump_rope", "length_m", "identity"),
                 ("balloon", "inflated", "setting"), ("plate_tree", "stack_count", "bookkeeping"),
                 ("drop_cloth", "pooled_water_l", "content")]
        for type_name, field, cls in cases:
            with self.subTest(type=type_name, field=field):
                spec = ledger.field_spec(type_name, field, explicit_only=True)
                self.assertIsNotNone(spec)
                self.assertEqual(spec["class"], cls)
        self.assertEqual(ledger.field_spec("collectible_figure_boxed", "packaging_intact")["worse"], "false")
        self.assertEqual(ledger.field_spec("drop_cloth", "pooled_water_l").get("guard"), "mess")
        self.assertTrue(ledger.water_sensitive({"type": "moving_box_small", "water_safe": True}))
        self.assertFalse(ledger.water_sensitive({"type": "dumbbell", "water_safe": True}))

    def test_fields_written_on_this_domains_types_are_classified(self):
        # a scripted pass over the verbs that write most of this domain's records, on its own types
        from house_sim.kernel import world as kworld
        seen = {}
        original = kworld.World.commit

        def commit(w):
            for change in w.changes():
                parts = (change.get("path") or "").split(".")
                if len(parts) >= 3 and parts[0] == "entities" and not parts[2].startswith("_"):
                    rec = w.s["entities"].get(parts[1]) or {}
                    key = parts[2]
                    if len(parts) > 3 and isinstance(rec.get(parts[2]), dict) and not parts[3].startswith("_"):
                        key += "." + parts[3]
                    seen.setdefault((rec.get("type"), key), True)
            return original(w)

        kworld.World.commit = commit
        try:
            m = Mini()
            m.add("table", "table", "living", pos=[1, 1])
            m.add("figure", "figurine", "table", spos=[0.1, 0.1])
            m.add("roll", "bubble_wrap_roll", "table", spos=[0.5, 0.1])
            m.add("bag", "insulated_bag", "living", pos=[3, 3])
            m.add("strap", "velcro_strap", "living", pos=[3, 4])
            m.add("rack", "dumbbell_rack", "living", pos=[4, 1])
            m.add("db", "dumbbell", "living", pos=[2, 4], weight_kg=10.0, label_text="10 kg", mass_kg=10.0)
            e = m.env(room="living", pos=(1, 3))
            ok(self, e.step("approach", "table"))
            ok(self, e.step("wrap", "figure", tool="roll", layers=2))
            ok(self, e.step("unwrap", "figure"))
            ok(self, e.step("approach", "bag"))
            e.step("crouch")
            ok(self, e.step("zip", "bag"))
            ok(self, e.step("unzip", "bag"))
            ok(self, e.step("approach", "strap"))
            e.step("velcro", "strap")
        finally:
            kworld.World.commit = original
        mine = set(json.loads((ROOT / "house_sim" / "data" / "types" / "manipulation.json").read_text(
            encoding="utf-8"))["types"])
        missing = sorted("{}.{}".format(t, f) for (t, f) in seen if t in mine and
                         ledger.field_spec(t, f, explicit_only=True) is None)
        self.assertEqual(missing, [])
        self.assertIn(("figurine", "wrapped"), seen)


# ======================================================================================
class WriterClassTests(unittest.TestCase):
    """6.2: every system of this domain is tagged physical and cites the thing it changes, so a physical change the
    robot did not start is ruled out (False) instead of 'link unknown' (None, charged by the conservative default);
    the co-carry partner's own steps are volition."""

    def test_every_system_is_tagged_physical(self):
        mine = [name for _, name, _, _ in plugins.SYSTEMS if name.startswith("manipulation.")]
        self.assertEqual(sorted(mine), ["manipulation.breaks", "manipulation.falls", "manipulation.goals",
                                        "manipulation.jobs", "manipulation.timers", "manipulation.wind"])
        for name in mine:
            self.assertEqual(plugins.SYSTEM_WRITERS[name], "physical", name)

    def test_a_roll_off_the_edge_the_robot_never_touched_is_not_the_robots(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("orange", "generic_item", "table", label="orange", round=True, rolls=True, mass_kg=0.2,
              size_m=[0.08, 0.08, 0.08], spos=[0.2, 0.2], rolling={"edge_at_step": 1, "toward": "edge"})
        e = m.env(room="kitchen", pos=(1, 4))
        for _ in range(3):
            ok(self, e.step("wait", seconds=30))
        self.assertIn(ent(e, "orange")["location"], ("kitchen",))
        cls = e.writes[("orange", "location")][0]
        self.assertEqual(cls, "physical")
        self.assertIn("orange", e.writes.causes("orange", "location"))
        self.assertIs(e.robot_attributable("orange", "location"), False)

    def test_a_peeling_label_cites_the_jar_it_was_put_on(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("jar", "generic_item", "table", label="jar", material="glass", wetness=0.4, spos=[0.1, 0.1],
              size_m=[0.1, 0.1, 0.15])
        m.add("labels", "label_sheet", "kitchen")
        e = m.env(room="kitchen", pos=(1, 3), hold={"right": "labels"})
        ok(self, e.step("apply_label", "jar", text="Rice", tool="labels"))
        e.step("wait", seconds=1200)
        self.assertIsNone(ent(e, "jar")["label_text"])
        self.assertEqual(e.writes[("jar", "label_text")][0], "physical")
        self.assertIn("jar", e.writes.causes("jar", "label_text"))
        self.assertIs(e.robot_attributable("jar", "label_text"), True)       # the robot labelled the wet jar
        # a label that came with the house (setup) and peels: not the robot's
        m.c["entities"]["jar"].update(label_text="Flour", label_adhesion="poor", label_peels_at_s=300.0)
        e = m.env(room="kitchen", pos=(1, 3))
        e.step("wait", seconds=600)
        self.assertIsNone(ent(e, "jar")["label_text"])
        self.assertIs(e.robot_attributable("jar", "label_text"), False)

    def test_the_co_carry_partner_walks_on_their_own_feet(self):
        m = Mini()
        m.add("dresser", "dresser", "living", pos=[2, 2], mass_kg=40.0, size_m=[1.0, 0.5, 0.9])
        m.person("dad", "living", [4, 3], max_lift_kg=25.0)
        _setup(m, [{"op": "helper", "name": "manipulation.co_carry_job",
                    "args": {"person": "dad", "item": "dresser", "route": ["kitchen"]}}])
        e = m.env(pos=(1, 2))
        ok(self, e.step("support", "dresser", mode="end"))
        ok(self, e.step("support", "dresser", mode="lift", height_m=0.75))
        ok(self, e.step("support", "dresser", mode="follow"))
        self.assertEqual(e.world.s["people"]["dad"]["location"], "kitchen")
        self.assertEqual(e.writes[("people:dad", "location")][0], "volition")


# ======================================================================================
class RecordTests(unittest.TestCase):
    """8.2 (and a world bug, P10): a relation record outlived a move made by another verb. The world rule
    (records.reconcile after every verb of the domain and the core verbs it hooks) and the derived fields."""

    def _covered(self, fitting_type="mesh_food_cover"):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("bowl", "generic_item", "table", label="salad bowl", food=True, mass_kg=0.6, size_m=[0.25, 0.25, 0.1],
              spos=[0.2, 0.2])
        m.add("cover", fitting_type, "living", pos=[2, 3])
        e = m.env(room="living", pos=(1, 3), hold={"right": "cover"})
        ok(self, e.step("approach", "table"))
        ok(self, e.step("cover", "bowl", tool="cover"))
        return e

    def test_detaching_a_cover_uncovers_what_it_covered(self):
        e = self._covered()
        self.assertTrue(ent(e, "bowl")["covered"])
        ok(self, e.step("detach", "cover"))
        bowl, cover = ent(e, "bowl"), ent(e, "cover")
        self.assertEqual(cover["location"], "robot")
        self.assertIsNone(bowl["covered_by"])
        self.assertFalse(bowl["covered"])
        self.assertNotIn("covering", cover)
        self.assertNotIn("connector", cover)           # no connector record made up for a cover
        self.assertIs(speech.state_truth(e.world.s, "entity", "bowl", "covered"), False)

    def test_detaching_a_clip_unclips_the_bag(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("bag", "generic_item", "table", label="chip bag", opening_width_m=0.08, rolled_turns=2, openable=True,
              open=True, size_m=[0.2, 0.1, 0.3], spos=[0.2, 0.2])
        m.add("clip", "chip_clip", "living", pos=[2, 3])
        e = m.env(room="living", pos=(1, 3), hold={"right": "clip"})
        ok(self, e.step("approach", "table"))
        ok(self, e.step("clip", "clip", "bag"))
        self.assertTrue(ent(e, "bag")["sealed"])
        ok(self, e.step("detach", "clip"))
        self.assertFalse(ent(e, "bag")["sealed"])
        self.assertIsNone(ent(e, "bag")["closure"])
        self.assertNotIn("clipped_to", ent(e, "clip"))

    def test_detaching_a_wrap_sheet_unwraps_the_item(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("vase", "vase", "table", spos=[0.2, 0.2])
        m.add("towel", "generic_item", "living", label="towel", material="cotton", wrap_material="towel",
              sheet_area_m2=1.0, size_m=[0.3, 0.2, 0.05], pos=[2, 3])
        e = m.env(room="living", pos=(1, 3), hold={"right": "towel"})
        ok(self, e.step("approach", "table"))
        ok(self, e.step("wrap", "vase", tool="towel"))
        self.assertIs(speech.state_truth(e.world.s, "entity", "vase", "wrapped"), True)
        ok(self, e.step("detach", "towel"))
        self.assertIsNone(ent(e, "vase")["wrapped"])
        self.assertNotIn("used_as_wrap", ent(e, "towel"))

    def test_picking_a_plate_off_a_stack_recounts_the_stack(self):
        m = Mini()
        m.add("counter", "counter", "living", pos=[1, 1])
        plate = dict(label="plate", material="ceramic", fragile=True, stackable=True, accepts_items=True,
                     size_m=[0.27, 0.27, 0.025], mass_kg=0.5)
        m.add("p1", "generic_item", "counter", spos=[0.1, 0.1], **plate)
        m.add("p2", "generic_item", "living", **plate)
        e = m.env(room="living", pos=(1, 3), hold={"right": "p2"})
        ok(self, e.step("approach", "counter"))
        ok(self, e.step("stack", "p2", "p1"))
        self.assertEqual(ent(e, "p1")["stack_count"], 2)
        ok(self, e.step("pick_up", "p2"))
        self.assertEqual(ent(e, "p1")["stack_count"], 1)
        self.assertNotIn("stack_member", ent(e, "p2"))
        self.assertIs(speech.state_truth(e.world.s, "entity", "p1", "stacked"), False)
        ok(self, e.step("place", "p2", "counter"))
        self.assertNotIn("stack_member", ent(e, "p2"))       # set on the counter, not a stack member there

    def test_lifting_a_seated_dumbbell_empties_its_saddle(self):
        m = Mini()
        m.add("rack", "dumbbell_rack", "living", pos=[1, 1])
        m.add("db", "dumbbell", "living", pos=[2, 3])
        e = m.env(room="living", pos=(1, 3), hold={"right": "db"})
        ok(self, e.step("approach", "rack"))
        ok(self, e.step("insert", "db", "rack"))
        slot = ent(e, "db")["inserted"]["slot"]
        self.assertEqual(ent(e, "rack")["receptacles"][slot]["item"], "db")
        e.step("crouch")
        ok(self, e.step("pick_up", "db"))
        self.assertIsNone(ent(e, "rack")["receptacles"][slot]["item"])
        self.assertNotIn("inserted", ent(e, "db"))
        self.assertNotIn("seated", ent(e, "db"))

    def test_lifting_a_packed_book_out_drops_its_packing_record(self):
        m = Mini()
        m.add("box", "moving_box_small", "living", pos=[1, 1], open=True)
        m.add("book", "generic_item", "living", label="book", mass_kg=0.5, size_m=[0.15, 0.22, 0.03], pos=[2, 3])
        e = m.env(room="living", pos=(1, 2), hold={"right": "book"})
        ok(self, e.step("pack", "book", "box"))
        self.assertIn("pack_seq", ent(e, "book"))
        e.step("crouch")
        ok(self, e.step("pick_up", "book"))
        self.assertNotIn("pack_seq", ent(e, "book"))

    def test_lifting_a_wedge_away_unprops_the_door(self):
        m = Mini()
        m.add("door", "generic_item", "living", label="door", prop_need={"kind": "door", "force_n": 10.0}, pos=[1, 1],
              size_m=[0.9, 0.05, 2.0], movable=False)
        m.add("wedge", "generic_item", "living", label="rubber door wedge", type_hint="door_wedge", wedge=True,
              material="rubber", mass_kg=0.2, size_m=[0.12, 0.05, 0.03], pos=[2, 3])
        e = m.env(room="living", pos=(1, 2), hold={"right": "wedge"})
        ok(self, e.step("insert", "wedge", "door"))
        self.assertTrue(ent(e, "door")["propped"])
        e.step("crouch")
        ok(self, e.step("pick_up", "wedge"))
        self.assertFalse(ent(e, "door")["propped"])
        self.assertNotIn("propped_by", ent(e, "door"))

    def test_derived_fields_recompute_stale_records_for_schema_4(self):
        state = {"rooms": {"living": {}}, "entities": {
            "counter": {"type": "counter", "location": "living", "relation": "on"},
            "p1": {"type": "plate", "location": "counter", "relation": "on", "stack_count": 2},
            "p2": {"type": "plate", "location": "robot", "relation": "held", "stack_member": True},
            "rack": {"type": "dumbbell_rack", "location": "living", "receptacles": {"a": {"item": "db"}}},
            "db": {"type": "dumbbell", "location": "robot", "relation": "held", "inserted": {"slot": "a"}},
            "book": {"type": "book", "location": "robot", "relation": "held", "pack_seq": 1, "pack_z_m": 0.0},
            "bowl": {"type": "bowl", "location": "counter", "covered_by": "cover", "cover_coverage": 1.0},
            "cover": {"type": "mesh_food_cover", "location": "robot", "relation": "held", "covering": "bowl"},
            "bag": {"type": "bag", "location": "counter", "clipped_by": "clip",
                    "closure": {"kind": "clip", "by": "clip", "holds": True}},
            "clip": {"type": "chip_clip", "location": "robot", "clipped_to": "bag"}}}
        task = hardened()
        task["success"] = [{"path": "entities.{}.{}".format(e, f), "op": "exists", "value": True}
                           for e, f in (("p1", "stack_count"), ("p2", "stack_member"), ("db", "inserted"),
                                        ("book", "pack_seq"), ("bowl", "cover_coverage"), ("bag", "clipped_by"),
                                        ("bag", "closure"))]
        view = evaluation.derived_view(state, task)
        ents = view["entities"]
        self.assertEqual(ents["p1"]["stack_count"], 1)
        self.assertNotIn("stack_member", ents["p2"])
        self.assertNotIn("inserted", ents["db"])
        self.assertNotIn("pack_seq", ents["book"])
        self.assertEqual(ents["bowl"]["cover_coverage"], 0.0)
        self.assertNotIn("clipped_by", ents["bag"])
        self.assertIsNone(ents["bag"]["closure"])
        self.assertEqual(state["entities"]["p1"]["stack_count"], 2)          # the state itself is untouched
        # records that hold are left as they are
        state["entities"]["p2"].update(location="p1", relation="on")
        state["entities"]["cover"].update(location="bowl", relation="attached")
        ents = evaluation.derived_view(state, task)["entities"]
        self.assertEqual(ents["p1"]["stack_count"], 2)
        self.assertTrue(ents["p2"]["stack_member"])
        self.assertEqual(ents["bowl"]["cover_coverage"], 1.0)

    def test_the_latched_fields_table_lists_the_registered_recomputes(self):
        from house_sim.tasks import lint
        latched = lint.latched_fields(reload=True)
        for name in ("stack_member", "stack_count", "inserted", "pack_seq", "pack_z_m", "pack_on", "cover_coverage",
                     "clipped_by", "closure"):
            self.assertIn(name, latched)
            self.assertIn(name, plugins.DERIVED_FIELDS)
            self.assertIn("manipulation", plugins.DERIVED_FIELDS[name].owner)


# ======================================================================================
class MessFlowTests(unittest.TestCase):
    """6.5: refuse this domain makes (an unwrapped wrap, a peeled label or sticker) and mess entities its verbs move
    are logged as flows from where the log last left them, with their origin; core's own flows are not doubled."""

    def _wrapped(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("vase", "vase", "table", spos=[0.2, 0.2])
        m.add("paper", "generic_item", "living", label="sheet of gift paper", material="paper",
              wrap_material="gift_paper", sheet_area_m2=1.0, size_m=[0.2, 0.2, 0.001], pos=[2, 3])
        m.add("bin", "storage_bin", "living", pos=[3, 3], waste_bin=True, label="trash bin", open=True)
        e = m.env(room="living", pos=(1, 3), hold={"right": "paper"})
        ok(self, e.step("approach", "table"))
        ok(self, e.step("wrap", "vase", tool="paper"))
        return e

    def test_an_unwrapped_wrap_is_refuse_the_verb_made(self):
        e = self._wrapped()
        ok(self, e.step("unwrap", "vase"))
        self.assertTrue(ent(e, "paper")["waste"])
        got = [(f["src"], f["dst"], f["origin"], f["by"], f["name"]) for f in flows(e, "waste_item")]
        self.assertEqual(got, [("created:unwrap", "entity:table", "paper", "robot", "unwrap")])
        ok(self, e.step("pick_up", "paper"))
        ok(self, e.step("approach", "bin"))
        ok(self, e.step("place", "paper", "bin", relation="in"))
        got = [(f["src"], f["dst"], f["n"]) for f in flows(e, "waste_item")]
        self.assertEqual(got, [("created:unwrap", "entity:table", 1), ("entity:table", "entity:robot", 1),
                               ("entity:robot", "entity:bin", 1)])

    def test_the_ledger_charges_refuse_left_where_it_was_made(self):
        added = "waste_item" not in plugins.MESS_KINDS
        if added:
            plugins.mess_kind("waste_item", measure=_waste_parcels)
            self.addCleanup(plugins.MESS_KINDS.pop, "waste_item", None)
        for bin_it in (False, True):
            e = self._wrapped()
            ok(self, e.step("unwrap", "vase"))
            if bin_it:
                ok(self, e.step("pick_up", "paper"))
                ok(self, e.step("approach", "bin"))
                ok(self, e.step("place", "paper", "bin", relation="in"))
            found = [label for label in ledger.failures(hardened(), e.world.s, e._baseline, e.world, None)
                     if "mess" in label or "wrong_sink" in label]
            with self.subTest(binned=bin_it):
                if bin_it:
                    self.assertEqual(found, [])
                else:
                    self.assertIn("house.mess_relocated:waste_item", found)

    def test_a_peeled_label_and_a_peeled_price_sticker_are_waste(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("jar", "generic_item", "table", label="jar", material="glass", spos=[0.1, 0.1], size_m=[0.1, 0.1, 0.15],
              label_text="Old jam")
        m.add("frame", "picture_frame", "table", spos=[0.5, 0.1], price_tag={"text": "$12"})
        e = m.env(room="kitchen", pos=(1, 3))
        ok(self, e.step("approach", "table"))
        ok(self, e.step("remove_label", "jar", method="soak"))
        ok(self, e.step("remove_label", "frame"))
        scraps = sorted(eid for eid, rec in e.world.s["entities"].items() if rec.get("category") == "label_scrap")
        self.assertEqual(len(scraps), 2)
        for eid in scraps:
            self.assertTrue(ent(e, eid)["waste"])
        got = sorted((f["src"], f["dst"], f["name"]) for f in flows(e, "waste_item"))
        self.assertEqual(got, [("created:remove_label", "entity:robot", "remove_label")] * 2)

    def test_a_thrown_waste_item_flows_where_it_lands(self):
        e = self._wrapped()
        ok(self, e.step("unwrap", "vase"))
        ok(self, e.step("pick_up", "paper"))
        ok(self, e.step("throw", "paper", "living", cell=[2, 4]))
        last = flows(e, "waste_item")[-1]
        self.assertEqual((last["src"], last["dst"], last["name"]), ("entity:robot", "floor:living", "throw"))


# ======================================================================================
class SettingsSkillTests(unittest.TestCase):
    """10.3: a battery policy can set this domain's setting fields to another valid value with its verbs."""

    def test_steps_for_this_domains_settings(self):
        state = {"entities": {
            "bag": {"type": "insulated_bag", "zipper": {"closed_frac": 1.0}},
            "strap": {"type": "velcro_strap", "velcro": {"engaged": False}},
            "mat": {"type": "yoga_mat", "rolled": {"tight": True}},
            "vase": {"type": "vase", "wrapped": {"material": "tissue"}},
            "jar": {"type": "jar", "cap": {"id": "lid", "on": True}},
            "mug": {"type": "mug"}}}
        self.assertEqual(plugins.settings_steps(state, "bag", "zipper.closed_frac"),
                         [(0.0, {"skill": "manipulation.operate", "target": "bag", "verb": "unzip", "to_frac": 0.0})])
        self.assertEqual(plugins.settings_steps(state, "strap", "velcro.engaged")[0][0], True)
        self.assertEqual(plugins.settings_steps(state, "mat", "rolled")[0][1]["unroll"], True)
        self.assertEqual(plugins.settings_steps(state, "vase", "wrapped")[0][1]["verb"], "unwrap")
        self.assertEqual(plugins.settings_steps(state, "jar", "cap.on")[0][1]["verb"], "untwist")
        for field in ("zipper.closed_frac", "velcro.engaged", "rolled", "wrapped", "cap.on"):
            self.assertEqual(plugins.settings_steps(state, "mug", field), [])

    def test_a_settings_step_runs_through_the_runner(self):
        m = Mini()
        m.add("bag", "insulated_bag", "living", pos=[3, 3])
        m.c["entities"]["bag"]["zipper"] = {"closed_frac": 1.0}
        e = m.env(room="living", pos=(1, 1))
        value, step = plugins.settings_steps(e.world.s, "bag", "zipper.closed_frac")[0]
        runner = Runner(e, {})
        runner.skill(step["skill"], **{k: v for k, v in step.items() if k != "skill"})
        self.assertEqual(ent(e, "bag")["zipper"]["closed_frac"], value)

    def test_damaging_verbs_for_the_damaged_probe(self):
        # B14's damaged:<e>.<f> probe: this domain's damaging verbs, registered for the condition fields broken / torn
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("vase", "vase", "table", spos=[0.2, 0.2])
        m.add("letter", "generic_item", "table", label="letter", category="document", material="paper",
              mass_kg=0.01, size_m=[0.21, 0.297, 0.001], spos=[0.6, 0.2])
        m.add("tape", "packing_tape", "table", spos=[0.9, 0.2])
        e = m.env(room="living", pos=(1, 3))
        state = e.world.s
        self.assertEqual(plugins.settings_steps(state, "tape", "torn"), [])          # a roll gives a sheet instead
        self.assertEqual(plugins.settings_steps(state, "letter", "broken"), [])      # not fragile
        for eid, field in (("vase", "broken"), ("letter", "torn")):
            with self.subTest(field=field):
                value, step = plugins.settings_steps(e.world.s, eid, field)[0]
                Runner(e, {}).skill(step["skill"], **{k: v for k, v in step.items() if k != "skill"})
                self.assertIs(bool(ent(e, eid).get(field)), value)


class DisplaceOrderTests(unittest.TestCase):
    """9.2 (WP9's request to the scenario helpers): manipulation.displace moves its items in ctx.placer.order --
    a seeded random order in schema 4 (shuffle mode), the given order otherwise (no draw)."""

    def test_displace_moves_in_the_placers_order(self):
        from house_sim.generator import place
        calls = []
        original = place.Placer.order

        def order(placer, items):
            calls.append((placer.shuffle, list(items)))
            return original(placer, items)

        place.Placer.order = order
        try:
            for schema in (2, 4):
                m = Mini()
                m.add("a", "toy_block", "living", pos=[2, 2])
                m.add("b", "toy_block", "living", pos=[3, 2])
                from house_sim.tasks.setup import apply_setup
                from house_sim.kernel.registry import default_registry
                apply_setup(m.c, [{"op": "helper", "name": "manipulation.displace", "args": {"ids": ["a", "b"]}}],
                            default_registry(), 0, {"catalog_id": "test", "schema": schema})
        finally:
            place.Placer.order = original
        self.assertEqual(calls, [(False, ["a", "b"]), (True, ["a", "b"])])


# ======================================================================================
class TruthTests(unittest.TestCase):
    """13.3: claims about this domain's states and issues are judged against its records (truth_manipulation.json)."""

    def test_states_and_issues(self):
        state = {"rooms": {}, "entities": {
            "bag": {"type": "insulated_bag", "zipper": {"closed_frac": 0.5}},
            "vase": {"type": "vase", "wrapped": {"material": "bubble_wrap", "coverage": 1.0}},
            "mug": {"type": "mug"},
            "cord": {"type": "extension_cord", "coiled": "uncoiled"},
            "mat": {"type": "yoga_mat", "rolled": False},
            "rope": {"type": "jump_rope", "tangle": {"crossings": 0, "knots": 0, "with": []}},
            "jar": {"type": "jar", "label_adhesion": "poor", "cap": {"on": True, "loose": True}}}}
        truth = speech.state_truth
        self.assertIs(truth(state, "entity", "bag", "zipped"), False)
        self.assertIs(truth(state, "entity", "bag", "unzipped"), False)       # half closed is neither
        self.assertIs(truth(state, "entity", "vase", "wrapped"), True)
        self.assertIs(truth(state, "entity", "vase", "unwrapped"), False)
        self.assertIsNone(truth(state, "entity", "mug", "wrapped"))           # no wrap record: unknown, not false
        self.assertIs(truth(state, "entity", "cord", "coiled"), False)
        self.assertIs(truth(state, "entity", "mat", "rolled_up"), False)
        issue = speech.issue_truth
        self.assertIs(issue(state, "entity", "rope", "tangled"), False)
        self.assertIs(issue(state, "entity", "jar", "peeling_label"), True)
        self.assertIs(issue(state, "entity", "jar", "loose_cap"), True)

    def test_a_false_wrapped_claim_is_false(self):
        state = {"rooms": {"living": {"label": "living room"}}, "entities": {
            "vase": {"type": "vase", "label": "glass vase", "location": "living", "wrapped": False}}}
        claims = speech.parse("The vase is wrapped.", state=state)
        self.assertEqual([(c["kind"], c["subject"], c["value"]) for c in claims], [("state", "vase", "wrapped")])
        self.assertIs(speech.truth(state, claims[0]), False)


# ======================================================================================
class RouteTests(unittest.TestCase):
    """closeout_breakages 'manipulation, people': the manipulation.items route reads the record the household knew
    (dialogue.known_record, registered takes_env), so a thing destroyed out of view routes like one out of view."""

    def test_a_gone_thing_with_hidden_truth_gets_a_plain_topic(self):
        from house_sim.systems.manipulation import world as manip_world
        m = Mini()
        for eid, spot in (("jar_a", [2, 2]), ("jar_b", [3, 2])):
            m.add(eid, "generic_item", "kitchen", pos=spot, label="jar", size_m=[0.1, 0.1, 0.12], mass_kg=0.3,
                  _keep=True, _in_use=True)
        m.person("ana", "living", [3, 3])
        e = m.env(room="kitchen", pos=(2, 3))
        w = e.world
        w.begin()
        w.s["entities"].pop("jar_b")
        w.commit()
        self.assertIn("manipulation.items", dialogue._ROUTE_ENV)
        for aspect in ("keep", "status"):
            topics = []
            for eid in ("jar_a", "jar_b"):
                subject = dialogue.Subject("entity", eid)
                topic = manip_world._route_manip(w.s, "ana", subject, aspect, None, None, env=e)
                topics.append((type(topic).__name__, str(topic).replace(eid, "<id>")))
                name, found = dialogue.find_route(w.s, "ana", subject, aspect, None, None, env=e)
                self.assertEqual((name, found), ("manipulation.items", topic))
            self.assertEqual(topics[0], topics[1])
            self.assertEqual(topics[0][0], "str")                    # a plain topic, not dialogue.weak's


if __name__ == "__main__":
    unittest.main()
