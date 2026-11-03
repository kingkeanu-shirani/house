"""Cleaning domain, phase 2 (VERIFY_HARDENING WP-D cleaning): what the house ledger needs from this domain, the
world-behaviour fixes of 23.3 and the phase-1 carry-over.

* mess kinds debris (g) and waste_item (count): measures, flows logged by the verbs, the ledger's mess guards on real
  cleaning episodes (relocated, wrong sink, in transit, sunk);
* refuse marked waste: true at episode_start (house.lost / house.removed exempt refuse, keepers stay protected);
* the drain relation: what goes down a sink, toilet or floor drain is gone (relation drain, out of sight, lost);
* derived fields in_stream, stream_ok, polished (8.2);
* field classes for every property of the domain's types (L-WATCH part 2), water-sensitive types;
* truth mappings (data/speech/truth_cleaning.json) and a floor's wetness that follows its film;
* settings skills; mop films that converge; a vacuum without a bin keeps nothing;
* look-alikes and unfamiliar things: no cleaning verb tells members of an unresolved group (robot_vacuum,
  spray_bottle) or unfamiliar twins apart before resolution (closeout_REV findings 5 and 6, closeout_breakages row 4).

Scenes reuse tests/test_cleaning.py's Lab (a small hand-built house), so the file runs with every domain loaded and
with HOUSE_SIM_DOMAINS=core,cleaning.
"""
import copy
import json
import os
import re
import unittest

from house_sim.kernel import ledger, speech
from house_sim.kernel.plugins import (BEFORE, DERIVED_FIELDS, MESS_KINDS, SETTINGS_SKILLS, load_domains,
                                      settings_steps)
from house_sim.kernel.predicates import MISSING

load_domains()

from house_sim.kernel.actions import NOVEL_OPERATE_MESSAGE  # noqa: E402
from house_sim.systems.cleaning import floors as F  # noqa: E402
from house_sim.systems.cleaning import hardening as H  # noqa: E402
from house_sim.systems.cleaning import state as S  # noqa: E402
from tests.test_cleaning import UNFAMILIAR, Lab, _on_counter, with_vacuum  # noqa: E402

from house_sim.kernel.registry import default_registry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRUMBS = {"kitchen": {"4,3": {"crumbs": 0.6, "dirt": 0.4}, "5,3": {"crumbs": 0.6}}}
HANDLE = re.compile(r"obj_[0-9a-f]{6,16}")


def canon(message):
    """A message with the realistic handles replaced (handles are salted by the house, which the twins differ in)."""
    return HANDLE.sub("<h>", message if isinstance(message, str) else json.dumps(message, sort_keys=True))


def hardened(envelope=None, protections=None):
    return {"success": [], "failure": [], "hardening": {"envelope": envelope or {}, "protections": protections or {}}}


def house_labels(lab, task=None, prefix="house."):
    found = ledger.failures(task or hardened(), lab.w.s, lab.env._baseline, world=lab.w, idle=None)
    return [f for f in found if f.startswith(prefix)]


def mess_labels(lab):
    return sorted(f for f in house_labels(lab) if f.split(":", 1)[0] in (
        "house.mess_relocated", "house.wrong_sink", "house.mess_in_transit", "house.mess_unaccounted"))


def sweep_and_collect(lab, toward=(4, 2), out_to=None):
    lab.hold("broom", "pan")
    args = {"target": "floor_kitchen", "tool": "broom", "zone": "all", "toward": list(toward)}
    if out_to:
        args["out_to"] = out_to
    lab.do("sweep", **args)
    piles = [e for e, r in sorted(lab.w.s["entities"].items()) if r.get("category") == "debris"]
    if out_to:
        return piles
    lab.do("walk_to", cell=list(toward), adjacent=True)
    lab.do("crouch")
    for pile in piles:
        lab.do("collect_debris", target=pile, tool="pan", passes=3)
    lab.do("stand")
    return piles


class MessKindTests(unittest.TestCase):
    """6.5: the debris and waste_item kinds are registered, measured and logged by the verbs that move them."""

    def test_the_kinds_are_registered_with_their_units(self):
        self.assertIn("debris", MESS_KINDS)
        self.assertIn("waste_item", MESS_KINDS)
        self.assertEqual(ledger.mess_kind_spec("debris")["unit"], "g")
        self.assertEqual(ledger.mess_kind_spec("waste_item")["unit"], "count")

    def test_the_debris_measure_reads_floor_cells_piles_and_loads(self):
        lab = Lab(floor_cells=CRUMBS)
        parcels = ledger._parcels(H.debris_parcels(lab.w.s))
        on_floor = parcels.get((("floor", "kitchen"), None), 0.0)
        expected = H.floor_grams(CRUMBS["kitchen"])
        self.assertAlmostEqual(on_floor, expected, places=6)
        self.assertGreater(on_floor, 20.0)
        sweep_and_collect(lab)
        parcels = ledger._parcels(H.debris_parcels(lab.w.s))
        self.assertGreater(parcels.get((("entity", "pan"), None), 0.0), 20.0)

    def test_a_swept_and_binned_floor_leaves_no_mess_label(self):
        lab = Lab(floor_cells=CRUMBS)
        sweep_and_collect(lab)
        lab.go("can")
        lab.do("open", target="can")
        lab.do("empty_into", target="pan", destination="can")
        self.assertEqual(mess_labels(lab), [])
        flows = lab.w.s["logs"]["mess_flows"]
        kinds = {(f["kind"], f["src"], f["dst"]) for f in flows}
        self.assertIn(("debris", "floor:kitchen", "entity:pan"), kinds)
        self.assertIn(("debris", "entity:pan", "entity:can"), kinds)

    def test_the_pan_never_emptied_is_mess_in_transit(self):
        lab = Lab(floor_cells=CRUMBS)
        sweep_and_collect(lab)
        self.assertIn("house.mess_in_transit:debris", mess_labels(lab))

    def test_the_pile_swept_into_the_next_room_is_relocated(self):
        """T-d389e2979f6aad-01's game (review p2_gaming_a 3.1): sweep ... out_to=<first exit>."""
        lab = Lab(floor_cells=CRUMBS)
        lab.hold("broom")
        lab.do("sweep", target="floor_kitchen", tool="broom", zone="all", out_to="hall")
        self.assertIn("house.mess_relocated:debris", mess_labels(lab))
        flows = [f for f in lab.w.s["logs"]["mess_flows"] if f["dst"] == "floor:hall"]
        self.assertTrue(flows)

    def test_the_pan_tipped_into_the_sink_is_a_wrong_sink(self):
        lab = Lab(floor_cells=CRUMBS)
        sweep_and_collect(lab)
        lab.go("sink")
        self.assertTrue(lab.step("empty_into", "pan", "sink")["ok"])
        self.assertIn("house.wrong_sink:debris", mess_labels(lab))

    def test_a_vacuum_without_a_bin_keeps_nothing(self):
        """A vacuum run with neither bin nor bag blows what it takes back out (the take used to vanish)."""
        lab = Lab(floor_cells={"living": {"3,3": {"crumbs": 0.5, "dust": 0.4}}}, build=with_vacuum(room="living"))
        lab.ent("vac")["slots"]["bin"]["item"] = None
        lab.w.s["entities"].pop("vac_bin")
        def levels():
            return sum(v for cell in lab.ent("floor_living")["_cells"].values() for v in cell.values())
        before = levels()
        lab.r.strict = False
        lab.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=1)
        after = levels()
        self.assertTrue(lab.env._history and any(r["command"].get("action") == "vacuum" and r["ok"]
                                                 for r in lab.env._history))
        self.assertGreater(after, before * 0.7)

    def test_a_vacuumed_floor_sinks_its_debris_in_the_bin(self):
        lab = Lab(floor_cells={"living": {"3,3": {"crumbs": 0.5, "dust": 0.4}, "4,4": {"crumbs": 0.5}}},
                  build=with_vacuum(room="living"))
        lab.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=2)
        self.assertEqual(mess_labels(lab), [])
        self.assertTrue(any(f["dst"] == "entity:vac_bin" for f in lab.w.s["logs"]["mess_flows"]))


class RefuseTests(unittest.TestCase):
    """Refuse carries waste: true from the baseline; keepers do not (6.4 house.lost / house.removed exemptions)."""

    EXTRA = {"can_1": _on_counter("soda_can", 0.3), "keep": _on_counter("gift_bag", 0.5, _disposition="keep")}

    def test_refuse_is_marked_and_keepers_are_not(self):
        lab = Lab(extra=self.EXTRA)
        self.assertIs(lab.ent("can_1").get("waste"), True)
        self.assertIs(lab.env._baseline["entities"]["can_1"].get("waste"), True)
        self.assertNotIn("waste", lab.ent("keep"))
        self.assertNotIn("waste", lab.ent("cloth"))
        self.assertTrue(H.is_refuse(lab.ent("can_1")))

    def test_binning_refuse_is_no_loss_but_binning_a_keeper_is(self):
        lab = Lab(extra=self.EXTRA)
        lab.hold("can_1", "keep")
        lab.go("recycling")
        lab.do("place", target="can_1", destination="recycling")
        lab.do("place", target="keep", destination="recycling")
        found = house_labels(lab)
        self.assertNotIn("house.lost:can_1", found)
        self.assertIn("house.lost:keep", found)

    def test_a_refuse_item_dropped_in_a_drawer_is_relocated_waste(self):
        lab = Lab(extra=dict(self.EXTRA, drawer=("generic_item", "kitchen", {"label": "drawer", "container": True,
                                                                             "accepts_items": True, "pos": [2, 1],
                                                                             "portable": False,
                                                                             "inner_size_m": [0.4, 0.4, 0.15]})))
        lab.hold("can_1")
        lab.go("drawer")
        lab.do("place", target="can_1", destination="drawer")
        self.assertIn("house.mess_relocated:waste_item", house_labels(lab))

    def test_a_wiped_up_spill_is_no_removal(self):
        from house_sim.systems.cleaning import make_spill
        lab = Lab()
        spill = make_spill(lab.w, "kitchen", "water", 0.05, cell=[4, 3])
        lab.env._baseline = copy.deepcopy(lab.w.s)
        H.mark_refuse(lab.w, lab.env)
        lab.env._baseline = copy.deepcopy(lab.w.s)
        self.assertIs(lab.ent(spill).get("waste"), True)
        lab.hold("cloth")
        lab.do("walk_to", cell=[4, 2], adjacent=True)
        for _ in range(4):
            if spill not in lab.w.s["entities"]:
                break
            lab.step("absorb", spill, tool="cloth")
        self.assertNotIn(spill, lab.w.s["entities"])
        self.assertNotIn("house.removed:" + spill, house_labels(lab))


    def test_a_trash_bag_that_holds_trash_is_refuse(self):
        lab = Lab(extra={"peel": ("fruit_peel", "can", {"relation": "in"})})
        self.assertIs(lab.ent("liner1").get("waste"), True)
        empty = Lab()
        self.assertNotIn("waste", empty.ent("liner1"))


    def test_dumped_mop_water_is_logged_once_a_liquid_kind_exists(self):
        """dump_liquid logs liquid flows for the kinds another domain registers (plumbing_liquids' liquid kinds); none
        before, so no flow names an unmeasured kind."""
        from house_sim.kernel import plugins
        bucket = ("mop_bucket", "bath", {"pos": [1, 1], "liquid": "water", "liquid_l": 4.0, "cl_soil_g": 40.0})
        lab = Lab(extra={"bucket": bucket}, robot={"room": "bath", "pos": [1, 2]})
        lab.hold("bucket")
        lab.go("toilet")
        self.assertTrue(lab.step("empty_into", "bucket", "toilet")["ok"])
        registered = "liquid:mop_water" in plugins.MESS_KINDS
        logged = [f for f in lab.w.s["logs"].get("mess_flows") or () if f["kind"] == "liquid:mop_water"]
        self.assertEqual(bool(logged), registered)
        if not registered:
            plugins.MESS_KINDS["liquid:mop_water"] = plugins.MessKind("liquid:mop_water", lambda state: [], owner="test")
        try:
            lab = Lab(extra={"bucket": bucket}, robot={"room": "bath", "pos": [1, 2]})
            lab.hold("bucket")
            lab.go("toilet")
            lab.step("empty_into", "bucket", "toilet")
            flows = [(f["kind"], f["src"], f["dst"], f["amount"]) for f in lab.w.s["logs"]["mess_flows"]]
            self.assertIn(("liquid:mop_water", "entity:bucket", "drain:toilet", 4.0), flows)
        finally:
            if not registered:
                plugins.MESS_KINDS.pop("liquid:mop_water", None)


class MeterAndProjectionTests(unittest.TestCase):
    def test_cleaning_water_counts_on_the_house_meter(self):
        """6.8: cleaning's water_used_l counts into the meter resources.json names (plumbing.meter.l)."""
        from house_sim.systems.cleaning import surfaces
        lab = Lab()
        path = ledger.data("resources")["meters"]["water"]["path"].split(".")
        surfaces.water_use(lab.w, 2.5)
        node = lab.w.s
        for key in path:
            node = node[key]
        self.assertAlmostEqual(node, 2.5)
        self.assertAlmostEqual(lab.w.s["stats"]["water_used_l"], 2.5)

    def test_the_idle_projection_carries_the_mess_parcels(self):
        from house_sim.kernel import counterfactual
        lab = Lab(floor_cells=CRUMBS)
        proj = counterfactual.project(lab.w.s)
        text = json.dumps(proj, default=str)
        self.assertIn("debris", text)


class DrainRelationTests(unittest.TestCase):
    """23.3 / 6.11: an item tipped into a basin goes down the drain when it fits (relation drain), out of sight."""

    def _pan_with(self, *items):
        """A dustpan holding the items (what collect_debris scoops up with a pile)."""
        extra = {eid: ("generic_item", "kitchen", dict({"pos": [5, 5]}, **props)) for eid, props in items}
        lab = Lab(extra=extra)
        for eid, _ in items:
            rec = lab.w.s["entities"][eid]
            rec.update(location="pan", relation="in")
            rec.pop("pos", None)
        return lab

    def test_an_earring_tipped_into_the_sink_goes_down_the_drain(self):
        lab = self._pan_with(("earring", {"label": "gold earring", "size_m": [0.015, 0.01, 0.005], "mass_kg": 0.003,
                                          "value": 300.0, "category": "jewelry"}))
        lab.hold("pan")
        lab.go("sink")
        self.assertTrue(lab.step("empty_into", "pan", "sink")["ok"])
        rec = lab.ent("earring")
        self.assertEqual((rec["location"], rec["relation"]), ("sink", "drain"))
        self.assertTrue(lab.log("item_down_drain"))
        obs = lab.env.observe()
        self.assertNotIn("earring", json.dumps(obs.get("visible_entities") or {}))
        self.assertIn("house.lost:earring", house_labels(lab))

    def test_a_bigger_thing_stays_in_the_basin(self):
        lab = self._pan_with(("toy", {"label": "toy car", "size_m": [0.12, 0.06, 0.05], "mass_kg": 0.1}))
        lab.hold("pan")
        lab.go("sink")
        lab.step("empty_into", "pan", "sink")
        self.assertEqual((lab.ent("toy")["location"], lab.ent("toy")["relation"]), ("sink", "in"))

    def test_the_drain_rule(self):
        sink = {"water_source": True, "drain": True}
        self.assertTrue(S.has_drain(sink) and S.has_drain({"toilet": True}) and S.has_drain({"floor_drain": True}))
        self.assertFalse(S.has_drain({"liquid_container": True}))
        self.assertTrue(S.drains_away({"size_m": [0.05, 0.02, 0.01]}, sink))
        self.assertFalse(S.drains_away({"size_m": [0.07, 0.02, 0.01]}, sink))
        self.assertTrue(S.drains_away({"size_m": [0.07, 0.02, 0.01]}, {"toilet": True}))


class DerivedFieldTests(unittest.TestCase):
    """8.2: in_stream, stream_ok and polished are recomputed from the state, so a latched verdict cannot outlive it."""

    def test_the_fields_are_registered(self):
        for name in ("in_stream", "stream_ok", "polished"):
            self.assertIn(name, DERIVED_FIELDS)
            self.assertTrue(DERIVED_FIELDS[name].owner.startswith("house_sim.systems.cleaning"))

    def test_the_stream_verdict_follows_where_the_item_is(self):
        lab = Lab(extra={"can_1": _on_counter("soda_can", 0.3)})
        lab.hold("can_1")
        lab.go("recycling")
        lab.do("place", target="can_1", destination="recycling")
        self.assertEqual(lab.ent("can_1").get("in_stream"), "recycling")
        self.assertEqual(H.derived_in_stream(lab.w.s, "can_1"), "recycling")
        self.assertEqual(H.derived_stream_ok(lab.w.s, "can_1"), lab.ent("can_1").get("stream_ok"))
        state = copy.deepcopy(lab.w.s)
        state["entities"]["can_1"].update(location="counter", relation="on")      # moved out without any hook
        self.assertIs(H.derived_in_stream(state, "can_1"), MISSING)
        self.assertIs(H.derived_stream_ok(state, "can_1"), MISSING)
        state["entities"]["can_1"].update(location="can", relation="in")           # into the trash: wrong stream
        self.assertEqual(H.derived_in_stream(state, "can_1"), "trash")
        self.assertFalse(H.derived_stream_ok(state, "can_1"))

    def test_a_hardened_task_reads_the_recomputed_verdict(self):
        from house_sim.kernel import evaluation
        lab = Lab(extra={"can_1": _on_counter("soda_can", 0.3)})
        lab.hold("can_1")
        lab.go("recycling")
        lab.do("place", target="can_1", destination="recycling")
        state = copy.deepcopy(lab.w.s)
        state["entities"]["can_1"].update(location="counter", relation="on")      # the latch would still say ok
        task = dict(hardened(), success=[{"path": "entities.can_1.stream_ok", "op": "is_true"}])
        view = evaluation.derived_view(state, task)
        self.assertNotIn("stream_ok", view["entities"]["can_1"])
        self.assertTrue(state["entities"]["can_1"].get("stream_ok"))                 # the stored latch

    def test_polished_holds_only_while_the_finish_is_clean(self):
        rec = {"polished": True, "soil": {}}
        state = {"entities": {"t": rec}}
        self.assertTrue(H.derived_polished(state, "t"))
        rec["soil"] = {"fingerprints": 0.2}
        self.assertFalse(H.derived_polished(state, "t"))
        self.assertIs(H.derived_polished({"entities": {"t": {}}}, "t"), MISSING)


class FieldClassTests(unittest.TestCase):
    """6.3, L-WATCH part 2: every property of house_sim/data/types/cleaning.json is classified."""

    def test_every_type_property_is_classified(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import field_census
        rows = field_census.unclassified(["cleaning"])["cleaning"]
        self.assertEqual(rows, [])

    def test_container_derived_content_and_tool_wear(self):
        self.assertEqual(ledger.field_class("kitchen_trash_can", "fill_level"), "bookkeeping")
        self.assertEqual(ledger.field_class("vacuum_bin", "fill_level"), "bookkeeping")
        self.assertEqual(ledger.field_spec("dustpan", "cl_load")["guard"], "mess")
        self.assertEqual(ledger.field_spec("debris_pile", "composition")["guard"], "mess")
        self.assertGreaterEqual(ledger.field_spec("microfiber_cloth", "wear")["tol"], 0.1)
        self.assertEqual(ledger.field_class("trash_liner_13gal", "folded"), "process")
        self.assertEqual(ledger.field_class("soda_can", "waste"), "identity")
        self.assertTrue(ledger.water_sensitive({"type": "vacuum_filter_hepa"}))


class TruthTests(unittest.TestCase):
    """13.1-13.3: claims about cleaning state are scored by data/speech/truth_cleaning.json."""

    def test_the_file_loads_with_the_kernel_tables(self):
        tables = speech.truth_tables()
        for word in ("clean", "dirty", "full", "soapy", "rinsed"):
            self.assertIn(word, tables["states"])
        for word in ("dusty", "crumbs", "wrong_bin", "overfull", "fingerprints"):
            self.assertIn(word, tables["issues"])
        self.assertEqual(tables["acts"]["clean"]["agency"], "lenient")

    def test_issues_and_states_read_the_records(self):
        state = {"entities": {"t": {"soil": {"dust": 0.3}}, "u": {"soil": {"dust": 0.01}}, "bin": {"fill_level": 0.95},
                              "x": {"stream_ok": False}}}
        self.assertTrue(speech.issue_truth(state, "entity", "t", "dusty"))
        self.assertFalse(speech.issue_truth(state, "entity", "u", "dusty"))
        self.assertTrue(speech.state_truth(state, "entity", "bin", "full"))
        self.assertTrue(speech.issue_truth(state, "entity", "x", "wrong_bin"))

    def test_a_mopped_floor_is_wet(self):
        """The reference's 'the kitchen floor is wet from mopping' was scored a false claim (the floor's own record
        never showed its film)."""
        lab = Lab(kitchen_floor="laminate")
        for key in ("2,2", "3,3"):
            S.set_film(lab.w, "kitchen", key, 0.012)
        self.assertGreater(lab.ent("floor_kitchen")["wetness"], 0.05)
        self.assertTrue(speech.state_truth(lab.w.s, "entity", "floor_kitchen", "wet"))
        for key in ("2,2", "3,3"):
            S.set_film(lab.w, "kitchen", key, 0.0)
        self.assertEqual(lab.ent("floor_kitchen")["wetness"], 0.0)
        self.assertTrue(speech.state_truth(lab.w.s, "entity", "floor_kitchen", "dry"))


class SettingsSkillTests(unittest.TestCase):
    def test_vacuum_settings_carts_and_liners(self):
        lab = Lab(build=with_vacuum(type_name="vacuum_cleaner"), extra={"cart": ("waste_cart", "yard", {"pos": [3, 3]})})
        steps = settings_steps(lab.w.s, "vac", "settings.suction")
        self.assertTrue(steps and all(step["skill"] == "set" for _, step in steps))
        self.assertNotIn(lab.ent("vac")["settings"]["suction"], [value for value, _ in steps])
        lock = settings_steps(lab.w.s, "cart", "locked")
        self.assertEqual(lock, [(True, {"skill": "use", "action": "lock", "target": "cart"})])
        lab.ent("liner1")["opened"] = False
        self.assertEqual(settings_steps(lab.w.s, "liner1", "opened")[0][0], True)
        self.assertIn("settings.mode", SETTINGS_SKILLS)


class WorldFixTests(unittest.TestCase):
    def test_mop_passes_converge_and_spare_laminate(self):
        """Three barely damp passes added up to 0.036 mm and water-damaged laminate (T-7e93003b0dcefa-01's reference)."""
        self.assertAlmostEqual(F.mopped_film(F.mopped_film(F.mopped_film(0.0, 0.012), 0.012), 0.012), 0.012 + 0.003
                               + 0.25 * 0.012, places=6)
        self.assertEqual(F.mopped_film(0.0, 0.18), 0.18)
        self.assertGreater(F.mopped_film(0.012, 0.18), 0.18)

    def test_a_plain_water_spray_sprays_water_and_contaminates_no_food(self):
        """The refill spray bottle of water was no bottle for apply_cleaner (only products and solutions were): one rule
        for the spray_bottle group now (closeout_REV 6); plain water contaminates nothing it oversprays."""
        lab = Lab(extra={"bread": _on_counter("generic_item", 0.4, label="bread", category="food", food=True),
                         "refill": _on_counter("spray_bottle", 0.1)})
        lab.hold("refill")
        lab.go("counter")
        res = lab.step("apply_cleaner", "counter", tool="refill")
        self.assertTrue(res["ok"], res["message"])
        self.assertEqual(float((lab.ent("bread").get("contamination") or {}).get("chemical", 0.0)), 0.0)
        self.assertLess(float(lab.ent("refill")["liquid_l"]), 0.75)


class LookalikeTests(unittest.TestCase):
    """Phase-1 close-out review findings 5 and 6 (confirmed by VER2): no cleaning verb tells an unresolved look-alike
    member from its group before resolution."""

    def _held_answers(self, type_name, commands):
        mug = _on_counter("generic_item", 0.6, label="mug", material="ceramic", size_m=[0.09, 0.09, 0.1], mass_kg=0.3)
        lab = Lab(perception="realistic", extra={"thing": _on_counter(type_name, 0.3), "mug": mug})
        lab.r.strict = False
        handle = lab.env.realism.handles.handle
        lab.go("counter")
        lab.step("pick_up", handle("thing"))
        out = []
        for action, target, kw in commands:
            snap = copy.deepcopy(lab.env)
            args = {k: handle(v) if v in ("thing", "mug", "counter") else v for k, v in kw.items()}
            res = snap.step(action, handle(target), **args)
            out.append((action, res["ok"], res["category"], canon(res["message"])))
        return out

    def test_robot_vacuum_members_answer_alike(self):
        if not {"robot_vacuum", "robot_vacuum_mop"} <= set(default_registry().types):
            self.skipTest("the navigation_robot domain (the robot_vacuum group) is not loaded")
        commands = [("absorb", "mug", {"tool": "thing"}), ("mop", "mug", {"tool": "thing"}), ("absorb", "mug", {})]
        answers = {t: self._held_answers(t, commands) for t in ("robot_vacuum", "robot_vacuum_mop")}
        self.assertEqual(answers["robot_vacuum"], answers["robot_vacuum_mop"])

    def test_the_robot_vacuum_mop_is_no_hand_mop(self):
        self.assertIsNone(S.tool_kind({"type": "robot_vacuum_mop", "label": "robot vacuum-mop",
                                       "rv": {"mop": True}, "appliance": {"programs": {}}}))
        self.assertEqual(S.tool_kind({"type": "generic_item", "label": "old mop"}), "flat_mop")

    def test_spray_bottle_members_answer_alike(self):
        commands = [("apply_cleaner", "mug", {}), ("apply_cleaner", "thing", {}), ("disinfect", "mug", {}),
                    ("descale", "mug", {})]
        known = default_registry().types
        answers = {t: self._held_answers(t, commands) for t in ("spray_bottle", "all_purpose_cleaner",
                                                               "glass_cleaner", "trigger_bottle") if t in known}
        reference = answers["all_purpose_cleaner"]
        for name, got in answers.items():
            self.assertEqual(got, reference, name)


UF = {"slicker_brush": {"label": "slicker brush", "tool_kind": "soft_brush", "affordances": ["dust", "scrub"]},
      "kettle": {"label": "kettle", "liquid_container": True, "liquid": "water", "liquid_l": 1.0, "capacity_l": 1.7},
      "towel": {"label": "towel", "tool_kind": "towel"},
      "liner": {"label": "trash bag", "liner": True, "folded": True, "opened": False}}


class UnfamiliarTests(unittest.TestCase):
    """closeout_breakages row 4 (pets' repro): cleaning's held-tool pickers read an unfamiliar thing's true kind."""

    def _answers(self, name, held=True):
        lab = Lab(perception="realistic",
                  extra={"thing": _on_counter("generic_item", 0.3, **dict(UF[name], **UNFAMILIAR))})
        lab.ent("thing").pop("pos", None)
        lab.r.strict = False
        handle = lab.env.realism.handles.handle
        lab.go("counter")
        if held:
            lab.step("pick_up", handle("thing"))
        out = []
        for action, kw in (("dust", {"target": "counter"}), ("scrub", {"target": "counter"}),
                           ("absorb", {"target": "counter"}), ("dust", {"target": "counter", "tool": "thing"}),
                           ("wipe", {"target": "thing"}), ("shake_out", {"target": "thing"}),
                           ("absorb", {"target": "counter", "tool": "thing"})):
            snap = copy.deepcopy(lab.env)
            args = {k: handle(v) for k, v in kw.items()}
            target = args.pop("target")
            res = snap.step(action, target, **args)
            out.append((action, res["ok"], res["category"], canon(res["message"])))
        return out

    def test_every_unfamiliar_twin_gets_the_same_answer(self):
        rows = {name: self._answers(name) for name in UF}
        first = rows["slicker_brush"]
        for name, got in rows.items():
            self.assertEqual(got, first, name)
        named = [row for row in first if row[0] in ("dust", "absorb") and row[3] == NOVEL_OPERATE_MESSAGE]
        self.assertTrue(named)

    def test_on_the_counter_too(self):
        rows = {name: self._answers(name, held=False) for name in UF}
        first = rows["kettle"]
        for name, got in rows.items():
            self.assertEqual(got, first, name)

    def test_the_hook_guards_every_cleaning_verb(self):
        verbs = sorted(n for n, entries in BEFORE.items() if any(e[1].endswith("hardening.unfamiliar_first")
                                                                 for e in entries))
        for verb in ("absorb", "apply_cleaner", "dust", "empty_into", "mop", "shake_out", "sweep", "vacuum", "wipe"):
            self.assertIn(verb, verbs)


if __name__ == "__main__":
    unittest.main()
