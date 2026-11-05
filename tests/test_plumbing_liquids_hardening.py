"""WP-D plumbing_liquids (VERIFY_HARDENING 6.2-6.11, 8.2, 10.3, 13; package WP-D plumbing_liquids): the liquid mess
kinds and their flows (other domains' reported parcels included), the house water meter, causes for the time systems'
writes, the derived coiled / drained fields, settings skills and damaging verbs, hazard zones, the drain relation, field
classes, truth mappings, the twin-sweep regressions (unfamiliar twins and look-alikes in every verb) and the phase-1
close-out breakage row 7 (electrical gear is water sensitive to this domain's drips and spills).

Run isolated:   HOUSE_SIM_DOMAINS=core,plumbing_liquids python -m unittest tests/test_plumbing_liquids_hardening.py
Strict journal: HOUSE_SIM_STRICT=1 python -m unittest tests/test_plumbing_liquids_hardening.py
"""
import copy
import unittest

from house_sim.kernel import ledger, plugins
from house_sim.kernel.plugins import load_domains

load_domains()
from house_sim.systems.plumbing_liquids import common as C  # noqa: E402
from house_sim.systems.plumbing_liquids import hardening as H  # noqa: E402
from tests.test_plumbing_liquids import Mini, ent, grab, ok, spills, wait, yard  # noqa: E402

MOP_BUCKET = dict(liquid="mop_water", liquid_l=7.0, _mix={"dirty_water": 7.0}, cl_soil_g=40.0, cl_chem={"bleach": 56.0})


def hardened(envelope=None, protections=None):
    return {"success": [], "failure": [], "hardening": {"envelope": envelope or {}, "protections": protections or {}}}


def labels(env, task=None):
    return ledger.failures(task or hardened(), env.world.s, env._baseline, world=env.world, idle=None)


def mess_labels(env, task=None):
    return sorted(f for f in labels(env, task) if f.startswith(("house.mess_", "house.wrong_sink")))


def flows(env, kind=None):
    return [(f["kind"], f["src"], f["dst"], round(f["amount"], 3)) for f in (env.world.s.get("logs") or {}).get("mess_flows") or ()
            if f.get("op") == "flow" and (kind is None or f["kind"] == kind)]


def mop_house(**extra):
    """A kitchen sink with plates, a bathroom toilet, a bleach mop bucket in the bathroom, a trash can, a dog's water
    bowl, a spare bucket and the backyard lawn."""
    m = Mini()
    m.kitchen_sink()
    m.add("plate", "generic_item", "ksink", relation="in", category="dish", food_safe=True, material="ceramic", mass_kg=0.4)
    m.add("wc", "toilet", "bath", pos=[2, 0], open=True)
    m.add("bucket", "utility_bucket", "bath", pos=[1, 2], **dict(MOP_BUCKET, **extra))
    m.add("spare", "utility_bucket", "bath", pos=[0, 2])
    m.add("trash", "generic_item", "kitchen", pos=[1, 1], label="kitchen trash can", category="waste_container",
          waste_stream="trash", accepts_items=True, container=True, capacity_l=50.0, size_m=[0.4, 0.35, 0.65], mass_kg=3.0)
    m.add("bowl", "generic_item", "kitchen", pos=[2, 1], label="dog water bowl", type_hint="pet_water_bowl",
          category="pet_bowl", container=True, liquid_container=True, capacity_l=1.5, portable=True, mass_kg=0.3)
    return m


def holding_bucket(test, m, room="bath", pos=(1, 1)):
    e = m.env(room=room, pos=pos)
    if room != "bath":
        e.world.s["entities"]["bucket"]["location"] = room
        e.world.s["entities"]["bucket"]["pos"] = [pos[0], pos[1] - 1]
    ok(test, e.step("crouch"))
    ok(test, e.step("pick_up", "bucket"))
    ok(test, e.step("stand"))
    return e


class LiquidClassTests(unittest.TestCase):
    def test_every_liquid_has_one_kind_by_its_class(self):
        expect = {"water": H.SPILL, "milk": H.SPILL, "broth": H.SPILL, "coffee": H.SPILL, "soapy_water": H.SPILL,
                  "dish_soap": H.SPILL, "condensate": H.SPILL, "hose_water": H.SPILL, "cooking_oil": H.GREASE,
                  "bleach": H.CHEMICAL, "drain_cleaner": H.CHEMICAL, "motor_oil": H.CHEMICAL, "gasoline": H.CHEMICAL,
                  "mop_water": H.MOP, "dirty_water": H.MOP, "sewage": H.DIRTY, "toilet_water": H.DIRTY, "urine": H.DIRTY}
        self.assertEqual({k: H.liquid_kind(k) for k in expect}, expect)

    def test_used_water_is_mop_water_a_fresh_cleaning_solution_and_soapy_water_are_not(self):
        self.assertEqual(H.liquid_kind("mop_water", {"bleach": 56.0}, 7.0, 40.0, {"dirty_water": 7.0}), H.MOP)
        self.assertEqual(H.liquid_kind("water", None, 4.0, 40.0), H.MOP)          # 10 g/l of dirt
        self.assertEqual(H.liquid_kind("mop_water"), H.MOP)                       # spilled: no composition known
        # a bucket made up for mopping (this domain names water with a cleaner in it "mop_water"): content until used
        self.assertEqual(H.liquid_kind("mop_water", {"wood_floor_cleaner": 30.0}, 6.0, 0.0, {"water": 6.0}), H.SPILL)
        self.assertEqual(H.liquid_kind("soapy_water", {"dish_soap": 20.0}, 30.0, 0.0, {"water": 30.0}), H.SPILL)

    def test_the_kinds_are_registered_with_the_kernel_tolerances(self):
        for kind in H.KINDS:
            self.assertIn(kind, plugins.MESS_KINDS)
            self.assertEqual(ledger.mess_kind_spec(kind)["unit"], "l")
        self.assertEqual(ledger.mess_kind_spec(H.MOP)["rho"], 0.0)          # it carries chemicals: zero slack
        self.assertEqual(ledger.mess_kind_spec(H.DIRTY)["rho"], 0.0)
        self.assertEqual(ledger.mess_kind_spec(H.CHEMICAL)["eps"], 0.01)
        self.assertEqual(ledger.mess_kind_spec(H.SPILL)["eps"], 0.05)


class ParcelTests(unittest.TestCase):
    def test_parcels_are_spills_and_dirty_vessels_never_clean_content_or_fixture_water(self):
        m = mop_house()
        m.add("pitcher", "water_pitcher", "kitchen", pos=[3, 2], liquid="water", liquid_l=1.5, _mix={"water": 1.5})
        m.add("jug", "generic_item", "kitchen", pos=[3, 3], label="bleach jug", product="bleach", liquid="bleach",
              liquid_l=2.0, amount_ml=2000.0, container=True, portable=True)
        m.add("puddle", "generic_item", "kitchen", pos=[1, 3], category="spill", liquid="water", liquid_l=0.4,
              portable=False, label="clear liquid spill")
        e = m.env(room="kitchen", pos=[2, 2])
        e.world.s["entities"]["ksink"].update(liquid="dirty_water", liquid_l=10.0)     # basin water is the fixture's
        found = H.parcels(e.world.s)
        self.assertEqual(found[H.SPILL], [(("floor", "kitchen"), None, 0.4)])
        self.assertEqual(found[H.MOP], [(("entity", "bucket"), None, 7.0)])
        self.assertEqual(found[H.CHEMICAL], [])        # the jug's bleach is goods (the content guard's)
        self.assertEqual(found[H.GREASE] + found[H.DIRTY], [])

    def test_safety_puddles_kernel_puddles_and_leaked_chemicals_are_counted(self):
        state = {"rooms": {"hall": {}}, "entities": {
            "p1": {"type": "floor_puddle", "category": "puddle", "liquid": "coffee", "liquid_l": 0.3, "location": "hall"},
            "p2": {"type": "generic_item", "category": "spill", "_phys_spill": True, "liquid": "oil", "liquid_l": 0.2,
                   "location": "hall"},
            "shelf": {"type": "shelf", "location": "hall", "_chem_spill": {"chlorine": 120.0, "oxidizer": 120.0}},
            "ice": {"type": "generic_item", "category": "ice_cubes", "ice_g": 200.0, "location": "hall"}}}
        found = H.parcels(state)
        self.assertEqual(found[H.SPILL], [(("floor", "hall"), None, 0.2), (("floor", "hall"), None, 0.3)])
        self.assertEqual(found[H.GREASE], [(("floor", "hall"), None, 0.2)])
        self.assertEqual(found[H.CHEMICAL], [(("entity", "shelf"), None, 0.12)])    # per class, not summed

    def test_a_spill_keeps_the_strictest_kind_of_what_reached_it(self):
        m = Mini()
        m.add("bucket", "utility_bucket", "kitchen", pos=[2, 1], liquid="water", liquid_l=4.0, _mix={"water": 4.0},
              cl_soil_g=40.0)
        e = m.env(room="kitchen", pos=[2, 2])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "bucket"))
        ok(self, e.step("stand"))
        ok(self, e.step("empty", "bucket", "kitchen"))
        sid = spills(e)[0]
        self.assertEqual(ent(e, sid)["liquid"], "water")              # named by what it is made of ...
        self.assertEqual(ent(e, sid)[C.MESS_KIND], H.MOP)             # ... but the dirt makes it mop water
        self.assertAlmostEqual(sum(a for _, _, a in H.parcels(e.world.s)[H.MOP]), 4.0, 2)


class FlowAndBalanceTests(unittest.TestCase):
    """The review's cheat class 1 on its own spec (T-151f6733397156-01): the bleach mop bucket's water must arrive in
    a toilet, utility sink or floor drain; anywhere else the ledger fails it."""

    def test_the_toilet_is_an_accepting_sink(self):
        e = holding_bucket(self, mop_house())
        ok(self, e.step("empty", "bucket", "wc"))
        self.assertIn((H.MOP, "entity:bucket", "entity:wc", 7.0), flows(e))
        self.assertEqual(mess_labels(e), [])

    def test_the_kitchen_sink_the_trash_and_the_dog_bowl_are_wrong_sinks(self):
        for dest, room, pos in (("ksink", "kitchen", (3, 1)), ("trash", "kitchen", (1, 2)), ("bowl", "kitchen", (2, 2))):
            e = holding_bucket(self, mop_house(), room, pos)
            if dest == "ksink":
                ok(self, e.step("approach", "ksink"))
            ok(self, e.step("empty", "bucket", dest))
            self.assertIn("house.wrong_sink:liquid:mop_water", mess_labels(e), dest)
            self.assertIn("house.mess_unaccounted:liquid:mop_water", mess_labels(e), dest)

    def test_the_lawn_is_a_relocation(self):
        e = holding_bucket(self, mop_house(), "yard", (2, 2))
        ok(self, e.step("empty", "bucket", "yard"))
        self.assertIn((H.MOP, "entity:bucket", "outside:yard", 7.0), flows(e))
        self.assertIn("house.mess_relocated:liquid:mop_water", mess_labels(e))

    def test_a_spare_bucket_is_mess_in_transit_and_a_listed_one_is_allowed(self):
        e = holding_bucket(self, mop_house())
        ok(self, e.step("pour", "bucket", "spare"))
        self.assertIn("house.mess_in_transit:liquid:mop_water", mess_labels(e))
        task = hardened({"mess": {H.MOP: {"transit_ok": ["spare"]}}})
        self.assertNotIn("house.mess_in_transit:liquid:mop_water", mess_labels(e, task))

    def test_hiding_a_little_in_a_towel_is_unaccounted_with_zero_slack(self):
        """0.3 l of 7 l gone where no measure sees it (soaked into a towel): the 5 % slack the draft allowed is 0."""
        e = holding_bucket(self, mop_house())
        ok(self, e.step("pour", "bucket", "wc", amount_l=6.7))
        e.world.s["entities"]["bucket"]["liquid_l"] = 0.0          # the rest wiped out with a towel (no measure)
        self.assertIn("house.mess_unaccounted:liquid:mop_water", mess_labels(e))

    def test_clean_liquid_and_products_used_where_they_belong_log_no_flow(self):
        m = mop_house(liquid="water", _mix={"water": 7.0}, cl_soil_g=0.0, cl_chem={})
        e = holding_bucket(self, m)
        ok(self, e.step("empty", "bucket", "wc"))                 # clean water down the toilet: content, no mess
        self.assertEqual(flows(e), [])
        m = Mini()
        m.kitchen_sink()
        m.add("counter", "counter", "kitchen", pos=[5, 0], size_m=[2.0, 0.6, 0.9])
        m.add("jug", "generic_item", "counter", spos=[0.0, 0.0], label="drain cleaner", product="drain_cleaner",
              liquid="drain_cleaner", liquid_l=1.0, _mix={"drain_cleaner": 1.0}, container=True, portable=True,
              liquid_container=True, capacity_l=1.0, open=True, size_m=[0.1, 0.1, 0.25], mass_kg=1.1)
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "jug"))
        ok(self, e.step("approach", "ksink"))
        ok(self, e.step("pour", "jug", "ksink", amount_l=0.2))      # a product poured where it is used
        self.assertEqual(flows(e), [])

    def test_water_into_a_bin_and_bleach_onto_a_plant_are_wrong_sinks(self):
        m = mop_house()
        m.add("counter", "counter", "kitchen", pos=[5, 0], size_m=[2.0, 0.6, 0.9])
        m.add("jug", "generic_item", "counter", spos=[0.0, 0.0], label="water jug", liquid="water", liquid_l=1.0,
              _mix={"water": 1.0}, container=True, portable=True, liquid_container=True, capacity_l=1.0, open=True,
              size_m=[0.1, 0.1, 0.25], mass_kg=0.2)
        m.add("bleach", "generic_item", "counter", spos=[0.4, 0.0], label="bleach", product="bleach", liquid="bleach",
              liquid_l=1.0, _mix={"bleach": 1.0}, container=True, portable=True, liquid_container=True, capacity_l=1.0,
              open=True, size_m=[0.1, 0.1, 0.25], mass_kg=1.1)
        m.add("fern", "generic_item", "kitchen", pos=[3, 4], label="potted fern", category="plant", is_a=["plant"],
              size_m=[0.3, 0.3, 0.5], mass_kg=2.0)
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("approach", "jug"))
        ok(self, e.step("pick_up", "jug"))
        ok(self, e.step("approach", "trash"))
        ok(self, e.step("pour", "jug", "trash"))
        self.assertIn((H.SPILL, "created:jug", "entity:trash"), [f[:3] for f in flows(e)])
        self.assertIn("house.wrong_sink:liquid:spill", mess_labels(e))
        ok(self, e.step("approach", "bleach"))
        ok(self, e.step("pick_up", "bleach"))
        ok(self, e.step("approach", "fern"))
        ok(self, e.step("pour", "bleach", "fern", amount_l=0.2))
        self.assertIn("house.wrong_sink:liquid:chemical", mess_labels(e))

    def test_a_flow_carries_the_origin_of_what_it_moves(self):
        e = holding_bucket(self, mop_house())
        ok(self, e.step("empty", "bucket", "wc"))
        entry = [f for f in e.world.s["logs"]["mess_flows"] if f.get("op") == "flow"][0]
        self.assertEqual(entry["origin"], "mop_water:bucket")
        self.assertEqual(mess_labels(e), [])        # parcels report none: one kind, one balance, whatever the flow says

    def test_a_bread_box_is_no_sink_it_runs_out_and_the_kind_stays_mop_water(self):
        """The review's cheat on T-151f6733397156-01: 0.3 l of bleach mop water "into" the bread box (which opens at the
        front and holds no liquid), the rest down the toilet."""
        m = mop_house()
        m.add("counter", "counter", "bath", pos=[2, 2], size_m=[1.0, 0.6, 0.9])
        m.add("breadbox", "generic_item", "counter", spos=[0.0, 0.0], label="bread box", category="kitchen_storage",
              container=True, openable=True, open=True, watertight=False, capacity_l=12.0, size_m=[0.4, 0.3, 0.2],
              mass_kg=1.5, material="wood")
        e = holding_bucket(self, m)
        ok(self, e.step("pour", "bucket", "breadbox", amount_l=0.3))
        ok(self, e.step("approach", "wc"))
        ok(self, e.step("empty", "bucket", "wc"))
        found = H.parcels(e.world.s)[H.MOP]
        self.assertAlmostEqual(sum(a for _, _, a in found), 0.3, 2)
        self.assertIn("house.mess_relocated:liquid:mop_water", mess_labels(e))

    def test_a_cloth_wrung_where_it_belongs_is_no_mess_a_bin_is_out_of_place(self):
        """A dirty dishcloth over the kitchen sink is what the sink is for; a cloth's water was never a measured parcel
        (the first version logged it as a mop-water flow from the cloth into a wrong sink)."""
        m = Mini()
        m.kitchen_sink()
        m.add("trash", "generic_item", "kitchen", pos=[2, 1], label="kitchen trash can", category="waste_container",
              waste_stream="trash", accepts_items=True, container=True, capacity_l=50.0, size_m=[0.4, 0.35, 0.65], mass_kg=3.0)
        m.add("cloth", "generic_item", "kitchen", pos=[3, 2], label="dish cloth", category="cloth", material="cotton",
              wetness=0.9, soil_load=0.5, size_m=[0.3, 0.3, 0.01], mass_kg=0.05, absorb_capacity_l=0.3)
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "cloth"))
        ok(self, e.step("stand"))
        ok(self, e.step("wring", "cloth", destination="ksink"))
        self.assertEqual(flows(e), [])
        self.assertEqual(mess_labels(e), [])
        e.world.s["entities"]["cloth"]["wetness"] = 0.9
        ok(self, e.step("approach", "trash"))
        ok(self, e.step("wring", "cloth", destination="trash"))
        self.assertIn(H.MOP, [f[0] for f in flows(e)])
        self.assertIn("house.wrong_sink:liquid:mop_water", mess_labels(e))

    def test_a_textile_holding_spill_liquid_is_logged_by_its_owner_not_twice(self):
        m = mop_house()
        m.add("cloth", "generic_item", "bath", pos=[1, 2], label="floor cloth", category="cloth", material="cotton", wetness=0.9,
              soil_load=0.5, size_m=[0.3, 0.3, 0.01], mass_kg=0.05, absorb_capacity_l=0.3, _spill={"l": {H.MOP + "|": 0.2}, "ref_l": 0.0})
        m.c["entities"].pop("bucket")
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "cloth"))
        ok(self, e.step("stand"))
        ok(self, e.step("wring", "cloth", destination="wc"))
        mine = [f for f in flows(e) if f[1] == "entity:cloth" and f[2] == "entity:wc"]
        self.assertLessEqual(len(mine), 1)
        if plugins.HOOKS.get("mess_parcels"):       # textiles_laundry loaded: its wring hook logs it, once
            self.assertEqual(len(mine), 1)

    def test_a_pre_existing_leak_logs_no_flow(self):
        m = Mini()
        m.add("pipe", "water_pipe", "basement", pos=[3, 0], mounted=True, mount_height_m=2.1,
              _leak={"component": "split_pipe", "rate_lpm": 2.0, "when": "always", "to": {"room": "basement", "cell": [3, 1]}})
        e = m.env(room="living", pos=[1, 3])
        wait(self, e, 300)
        self.assertTrue(spills(e))
        self.assertEqual(flows(e), [])


class DrainRelationTests(unittest.TestCase):
    def test_an_object_plunged_into_the_trap_is_lost_out_of_sight_and_reach(self):
        """6.4 house.lost reads the drain relation. It was relation "part": still seen in the bowl, "Detach it first", and
        only house.moved."""
        m = Mini()
        m.add("wc", "toilet", "bath", pos=[2, 0], open=True,
              _clog={"kind": "object", "amount": 0.9, "object": "ring", "object_visible": True, "depth_m": 0.1})
        m.add("ring", "generic_item", "wc", relation="in", label="gold ring", category="jewelry", size_m=[0.02, 0.02, 0.01],
              mass_kg=0.01, material="gold")
        m.add("plunger", "flange_plunger", "bath", pos=[1, 1])
        e = m.env(room="bath", pos=[2, 1], perception="realistic")
        h = e.realism.handles.handle
        ring = h("ring")
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", h("plunger")))
        ok(self, e.step("stand"))
        ok(self, e.step("plunge", h("wc"), strokes=5))
        self.assertEqual(ent(e, "ring")["relation"], "drain")
        self.assertIn("house.lost:ring", labels(e))
        self.assertNotIn(ring, str(e.observe()))
        ok(self, e.step("crouch"))
        got = e.step("pick_up", ring)
        self.assertFalse(got["ok"])
        self.assertEqual(got["message"], "That is not visible here")


class ReportedParcelTests(unittest.TestCase):
    """textiles_laundry's request: the liquid measures add what other domains report (the mess_parcels hook), so soaking
    a spill up in a towel is a relocation an envelope can allow, not an unexplained disappearance."""

    def setUp(self):
        self.before = list(plugins.HOOKS.get("mess_parcels") or ())

    def tearDown(self):
        plugins.HOOKS["mess_parcels"] = self.before

    def stand_in(self, litres):
        def hook(state, kind):
            soaked = kind == H.SPILL and "spill_1" not in state["entities"]
            return [(("entity", "towel"), "jug_1", litres)] if soaked else []
        plugins.HOOKS["mess_parcels"] = self.before + [(999, "test.parcels", hook)]

    def test_a_reported_parcel_is_measured_and_its_origin_goes(self):
        self.stand_in(0.4)
        found = H.parcels({"rooms": {}, "entities": {}})
        self.assertIn((("entity", "towel"), None, 0.4), found[H.SPILL])
        self.assertEqual(found[H.MOP], [])

    def test_soaking_a_spill_into_a_towel_is_relocated_and_an_envelope_can_allow_it(self):
        m = Mini()
        m.add("spill_1", "generic_item", "kitchen", pos=[2, 2], category="spill", liquid="water", liquid_l=0.4, portable=False)
        m.add("towel", "generic_item", "kitchen", pos=[1, 1], label="towel", category="towel", size_m=[0.5, 0.3, 0.02], mass_kg=0.3)
        e = m.env(room="kitchen", pos=[1, 2])
        e.world.s["entities"].pop("spill_1")         # the mop-up, by whatever verb
        self.assertIn("house.mess_unaccounted:liquid:spill", mess_labels(e))
        self.stand_in(0.4)
        self.assertIn("house.mess_relocated:liquid:spill", mess_labels(e))
        self.assertEqual(mess_labels(e, hardened({"mess": {H.SPILL: {"allow_at": ["towel"]}}})), [])

    def test_a_dirty_spill_records_its_kind_for_what_soaks_it_up(self):
        m = Mini()
        m.add("bucket", "utility_bucket", "kitchen", pos=[2, 1], liquid="dirty_water", liquid_l=2.0, _mix={"dirty_water": 2.0})
        e = m.env(room="kitchen", pos=[2, 2])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "bucket"))
        ok(self, e.step("stand"))
        ok(self, e.step("empty", "bucket", "kitchen"))
        sid = spills(e)[0]
        self.assertEqual(ent(e, sid)[C.MESS_KIND], H.MOP)
        m = Mini()
        m.add("jug", "water_pitcher", "kitchen", pos=[2, 1], liquid="water", liquid_l=1.0, _mix={"water": 1.0})
        e = m.env(room="kitchen", pos=[2, 2])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "jug"))
        ok(self, e.step("stand"))
        ok(self, e.step("empty", "jug", "kitchen"))
        self.assertNotIn(C.MESS_KIND, ent(e, spills(e)[0]))      # a plain spill records nothing

    def test_a_towel_really_soaking_up_a_spill_when_both_domains_are_loaded(self):
        if "absorb" not in plugins.ACTIONS or not self.before:
            self.skipTest("cleaning and textiles_laundry not loaded")
        m = Mini()
        m.add("spill_1", "spill", "kitchen", pos=[2, 2], liquid="water", liquid_l=0.3)
        m.add("towel", "bath_towel", "kitchen", pos=[2, 1])
        e = m.env(room="kitchen", pos=[2, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "towel"))
        ok(self, e.step("absorb", "spill_1", tool="towel"))
        left = H.parcels(e.world.s)[H.SPILL]
        self.assertTrue(any(loc == ("entity", "towel") for loc, _, _ in left), left)
        self.assertIn("house.mess_in_transit:liquid:spill", mess_labels(e))     # the wet towel still in hand
        self.assertEqual(mess_labels(e, hardened({"mess": {H.SPILL: {"transit_ok": ["towel"]}}})), [])


class MeterTests(unittest.TestCase):
    def test_a_tap_left_running_is_water_waste_and_a_sink_of_washing_water_is_not(self):
        m = Mini()
        m.kitchen_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("turn_on_water", "ksink", flow=1.0))
        wait(self, e, 60)
        ok(self, e.step("turn_off_water", "ksink"))
        self.assertLess(C.house_meter_l(e.world.s), 10.0)
        self.assertNotIn("house.waste:water", labels(e))
        ok(self, e.step("turn_on_water", "ksink", flow=1.0))
        wait(self, e, 1800)
        self.assertIn("house.waste:water", labels(e))
        self.assertGreater(C.house_meter_l(e.world.s), 10.0)

    def test_the_house_meter_counts_this_domains_draws_and_other_domains_counted_ones(self):
        path = ledger.data("resources")["meters"]["water"]["path"]
        self.assertEqual(path, "plumbing.meter.l")
        m = Mini()
        m.kitchen_sink()
        m.add("meter", "water_meter", "basement", pos=[2, 0], mounted=True, mount_height_m=0.3)
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("turn_on_water", "ksink", flow=1.0))
        wait(self, e, 60)
        ok(self, e.step("turn_off_water", "ksink"))
        sec = e.world.s["plumbing"]
        self.assertAlmostEqual(sec["meter"]["l"], sec["meter_l"], 3)
        self.assertEqual(ledger.meter_value(e.world.s, path), sec["meter"]["l"])
        C.meter_add(e.world, 3.0)                      # another domain's draw (cleaning's state.count_water path)
        self.assertAlmostEqual(C.house_meter_l(e.world.s), sec["meter_l"] + 3.0, 3)
        wait(self, e, 30)
        self.assertIn("{:.1f}".format(482113.0 + C.house_meter_l(e.world.s)), ent(e, "meter")["display"])


class CauseTests(unittest.TestCase):
    """6.2's causal link: the time systems' writes cite the thing whose water it is."""

    def test_an_exogenous_leak_is_not_linked_to_the_robot(self):
        m = Mini()
        m.add("pipe", "water_pipe", "basement", pos=[3, 0], mounted=True, mount_height_m=2.1,
              _leak={"component": "split_pipe", "rate_lpm": 2.0, "when": "always", "to": {"room": "basement", "cell": [3, 1]}})
        e = m.env(room="living", pos=[1, 3])
        wait(self, e, 120)
        sid = spills(e)[0]
        self.assertIn("pipe", e.writes.causes(sid, "$exists"))         # the leak's water cites its host ...
        self.assertIs(e.robot_attributable(sid, "liquid_l"), False)     # ... which the robot never touched

    def test_a_stem_the_robot_snapped_is_its_doing(self):
        m = Mini()
        m.add("main", "main_shutoff_valve", "basement", pos=[3, 0], mounted=True, mount_height_m=0.6, _seized=True)
        e = m.env(room="basement", pos=[3, 1])
        ok(self, e.step("shut_off", "main", force="firm"))
        wait(self, e, 60)
        sid = spills(e)[0]
        self.assertIs(e.robot_attributable(sid, "liquid_l"), True)

    def test_a_tap_the_robot_left_running_overflows_on_its_account(self):
        m = Mini()
        m.kitchen_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        e.world.s["entities"]["ksink"]["plug"] = "closed"
        ok(self, e.step("turn_on_water", "ksink", flow=1.0))
        wait(self, e, 600)
        sid = spills(e)[0]
        self.assertIn("ksink", e.writes.causes(sid, "$exists"))
        self.assertIs(e.robot_attributable(sid, "liquid_l"), True)


class DerivedFieldTests(unittest.TestCase):
    def test_a_hose_is_coiled_only_while_it_rests_on_its_hanger_or_where_it_was_coiled(self):
        m = yard(attached=False, nozzle=False)
        e = m.env(room="yard", pos=[1, 1])
        grab(self, e, "hose", hand="both")
        ok(self, e.step("approach", "hanger"))
        ok(self, e.step("place", "hose", "hanger", relation="hung"))
        self.assertTrue(ent(e, "hose")["coiled"])
        self.assertTrue(H.derived_coiled(e.world.s, "hose"))
        ok(self, e.step("pick_up", "hose", hand="both"))
        self.assertTrue(ent(e, "hose")["coiled"])                 # the stored flag is latched by the place hook ...
        self.assertFalse(H.derived_coiled(e.world.s, "hose"))     # ... the derived one follows where it is
        m = yard(attached=False, nozzle=False)
        m.c["entities"]["hose"]["coiled"] = True                  # put away coiled on the ground
        e = m.env(room="yard", pos=[1, 1])
        self.assertTrue(H.derived_coiled(e.world.s, "hose"))
        e.world.s["entities"]["hose"]["pos"] = [4, 4]             # the wind dragged it off
        self.assertFalse(H.derived_coiled(e.world.s, "hose"))
        self.assertIn("coiled", plugins.DERIVED_FIELDS)

    def test_a_drained_spigot_refills_when_its_interior_valve_opens_by_any_path(self):
        m = Mini()
        m.add("inner", "shutoff_valve", "basement", pos=[0, 0], mounted=True, settings={"position": 0.0}, valve_open=False)
        m.add("spigot", "outdoor_spigot", "yard", pos=[0, 1], mounted=True, interior_valve="inner", drained=True)
        e = m.env(room="yard", pos=[0, 2])
        self.assertTrue(H.derived_drained(e.world.s, "spigot"))
        e.world.s["entities"]["inner"]["settings"]["position"] = 1.0     # a resident opened it (no robot verb ran)
        self.assertTrue(ent(e, "spigot")["drained"])
        self.assertFalse(H.derived_drained(e.world.s, "spigot"))


class SettingsSkillTests(unittest.TestCase):
    def test_each_skill_names_a_step_that_reaches_its_value(self):
        m = Mini()
        m.kitchen_sink()
        m.bath_sink()
        m.add("wc", "toilet", "bath", pos=[2, 0], open=True)
        e = m.env(room="kitchen", pos=[3, 1])
        state = e.world.s
        steps = plugins.settings_steps(state, "ksink", "settings.flow")
        self.assertEqual(steps[0][0], H.FLOW_ON)
        self.assertEqual(steps[0][1]["action"], "turn_on_water")
        self.assertEqual(plugins.settings_steps(state, "k_cold", "settings.position")[0][0], 0.0)
        self.assertEqual(plugins.settings_steps(state, "wc", "settings.supply_valve")[0][0], "closed")
        self.assertEqual(plugins.settings_steps(state, "bsink", "settings.left")[0][1]["handle"], "left")
        bsink_plug = plugins.settings_steps(state, "bsink", "plug")
        self.assertEqual(bsink_plug[0][0], "closed")
        args = dict(steps[0][1])
        ok(self, e.step(args.pop("action"), args.pop("target"), **{k: v for k, v in args.items() if k != "skill"}))
        self.assertEqual(ent(e, "ksink")["settings"]["flow"], H.FLOW_ON)
        steps = plugins.settings_steps(e.world.s, "ksink", "settings.flow")
        self.assertEqual((steps[0][0], steps[0][1]["action"]), (0.0, "turn_off_water"))


class DamagingVerbTests(unittest.TestCase):
    """B14's damaged probe: a settings skill for a condition field whose step really does the damage."""

    def run_step(self, e, step):
        from house_sim.tasks.runner import run_plan
        run_plan(e, [step], {})

    def test_a_firm_close_crushes_a_compression_washer(self):
        m = Mini()
        m.bath_sink()
        m.kitchen_sink()
        e = m.env(room="bath", pos=[0, 1])
        self.assertEqual(plugins.settings_steps(e.world.s, "ksink", "damage.washer_crushed"), [])   # a single lever
        value, step = plugins.settings_steps(e.world.s, "bsink", "damage.washer_crushed")[0]
        self.assertEqual(ledger.field_class("bathroom_sink", "damage.washer_crushed"), "condition")
        self.run_step(e, step)
        self.assertEqual(ent(e, "bsink")["damage"]["washer_crushed"], value)
        self.assertEqual(plugins.settings_steps(e.world.s, "bsink", "damage.washer_crushed"), [])

    def test_forcing_a_seized_valve_snaps_it(self):
        m = Mini()
        m.add("main", "main_shutoff_valve", "basement", pos=[3, 0], mounted=True, mount_height_m=0.6, _seized=True)
        m.add("free", "main_shutoff_valve", "basement", pos=[4, 0], mounted=True, mount_height_m=0.6)
        e = m.env(room="basement", pos=[3, 1])
        self.assertEqual(plugins.settings_steps(e.world.s, "free", "broken"), [])
        value, step = plugins.settings_steps(e.world.s, "main", "broken")[0]
        self.run_step(e, step)
        self.assertIs(ent(e, "main").get("broken"), value)

    def test_a_hose_threaded_at_an_angle_or_with_a_wrench(self):
        for field, value in (("cross_threaded", True), ("coupling_washer_crushed", True)):
            m = yard(attached=False, nozzle=False)
            e = m.env(room="yard", pos=[1, 1])
            options = plugins.settings_steps(e.world.s, "hose", field)
            self.assertEqual(options[0][0], value)
            self.run_step(e, options[0][1])
            self.assertIs(ent(e, "hose").get(field), value)
            self.assertEqual(plugins.settings_steps(e.world.s, "hose", field), [])       # attached now

    def test_a_tray_straight_from_the_freezer_cracks(self):
        from house_sim.systems.plumbing_liquids.ice import fresh_from_cold
        m = Mini()
        m.add("tray", "ice_cube_tray", "kitchen", pos=[2, 1], ice_g=200.0, ice_cubes=14, frozen_frac=1.0, temperature_c=-18.0)
        e = m.env(room="kitchen", pos=[2, 2])
        self.assertTrue(fresh_from_cold(ent(e, "tray")))
        value, step = plugins.settings_steps(e.world.s, "tray", "cracked")[0]
        self.run_step(e, step)
        self.assertIs(ent(e, "tray").get("cracked"), value)


class HazardZoneTests(unittest.TestCase):
    def zones(self, e):
        return {z["id"]: z for z in H.water_hazard_zones(e.world.s)}

    def test_a_wet_floor_and_dropped_ice_are_hazards_as_the_thing_itself_never_a_room(self):
        m = Mini()
        m.add("wet", "generic_item", "kitchen", pos=[1, 1], category="spill", liquid="water", liquid_l=0.4, portable=False)
        m.add("drops", "generic_item", "kitchen", pos=[2, 1], category="spill", liquid="water", liquid_l=0.01, portable=False)
        m.add("cubes", "generic_item", "kitchen", pos=[1, 2], category="ice_cubes", ice_g=100.0)
        e = m.env(room="kitchen", pos=[3, 3])
        zones = self.zones(e)
        self.assertEqual(sorted(zones), ["ice:cubes", "spill:wet"])
        self.assertEqual(zones["spill:wet"], {"id": "spill:wet", "room": None, "subjects": ["wet"]})

    def test_a_running_scalding_tap_and_a_hot_vessel_are_hazards_while_they_last(self):
        from house_sim.kernel import speech
        m = Mini()
        m.kitchen_sink()
        m.add("pot", "water_pitcher", "kitchen", pos=[2, 1], liquid="water", liquid_l=1.0, _mix={"water": 1.0}, temperature_c=80.0)
        e = m.env(room="kitchen", pos=[3, 1])
        wait(self, e, 2)
        self.assertIn("hot:pot", self.zones(e))
        ok(self, e.step("turn_on_water", "ksink", temp="hot", flow=1.0))
        e.world.s["entities"]["ksink"]["outlet_temp_c"] = 62.0          # a heater set too high
        self.assertIn("scald:ksink", self.zones(e))
        ok(self, e.step("turn_off_water", "ksink"))
        self.assertNotIn("scald:ksink", self.zones(e))
        self.assertIn("hot:pot", [z["id"] for z in speech.hazard_zones(e.world.s)])

    def test_coming_to_look_at_the_spill_invites_and_the_room_is_not_named(self):
        from house_sim.kernel import speech
        m = Mini()
        m.add("wet", "generic_item", "kitchen", pos=[1, 1], category="spill", liquid="water", liquid_l=0.4, portable=False)
        m.add("mug", "water_carafe", "kitchen", pos=[3, 1])
        e = m.env(room="kitchen", pos=[3, 3])
        state = e.world.s
        invite = {"kind": "directive", "act": "approach", "subject": "wet", "subject_kind": "entity"}
        self.assertEqual(speech.zones_invited(state, invite), ["spill:wet"])
        self.assertEqual(speech.zones_invited(state, dict(invite, subject="mug")), [])
        self.assertEqual(speech.zones_invited(state, dict(invite, subject="kitchen", subject_kind="room")), [])


class FieldClassTests(unittest.TestCase):
    def test_every_property_of_the_domains_types_is_classified(self):
        from house_sim.tasks import lint
        problems = [p for p in lint.watch_problems(domains=["plumbing_liquids"]) if "plumbing_liquids" in str(p)]
        self.assertEqual(problems, [])

    def test_the_classes_this_domains_mechanics_need(self):
        self.assertEqual(ledger.field_class("kitchen_sink", "water_running"), "process")
        self.assertEqual(ledger.field_class("kitchen_sink", "potable"), "process")
        self.assertEqual(ledger.field_class("toilet", "bowl_l"), "process")
        self.assertEqual(ledger.field_class("kitchen_sink", "basin_l"), "identity")
        self.assertEqual(ledger.field_class("kitchen_sink", "overflowed_l"), "bookkeeping")
        self.assertEqual(ledger.field_spec("garden_hose", "cross_threaded")["worse"], "true")
        self.assertEqual(ledger.field_spec("garden_hose", "coupling_washer")["worse"], "false")
        self.assertEqual(ledger.field_spec("utility_bucket", "liquid_l")["guard"], "mess")
        self.assertEqual(ledger.field_class("water_pitcher", "liquid_l"), "content")
        self.assertEqual(ledger.field_class("shutoff_valve", "settings.position"), "setting")
        self.assertEqual(ledger.field_class("shutoff_valve", "valve_open"), "process")       # follows the position
        self.assertEqual(ledger.field_class("kitchen_sink", "settings.mix"), "process")      # any use moves it
        self.assertEqual(ledger.field_class("kitchen_sink", "settings.flow"), "setting")
        self.assertEqual(ledger.field_class("garden_hose", "liquid_l"), "process")           # draining consumes nothing
        self.assertEqual(ledger.field_spec("utility_bucket", "soil.dirt")["guard"], "soiled")  # one label: not "damaged"

    def test_a_soiled_bucket_is_soiled_and_not_also_damaged(self):
        e = holding_bucket(self, mop_house())
        ok(self, e.step("empty", "bucket", "wc"))            # the dirty water leaves a film on the bucket's wall
        got = labels(e)
        self.assertIn("house.soiled:bucket", got)
        self.assertFalse([x for x in got if x.startswith("house.damaged:bucket")], got)


class TruthTests(unittest.TestCase):
    def holds(self, issue, record):
        from house_sim.kernel import speech
        state = {"rooms": {"bath": {"label": "bathroom"}}, "entities": {"x_1": dict(record, location="bath")},
                 "people": {}, "pets": {}, "robot": {"room": "bath"}}
        return speech.issue_truth(state, "entity", "x_1", issue)

    def test_a_running_toilet_is_read_from_its_tank(self):
        sound = {"type": "toilet", "label": "toilet", "toilet": True, "_tank": {"fill_valve_ok": True}}
        self.assertIs(self.holds("running_toilet", sound), False)
        self.assertIs(self.holds("running_toilet", dict(sound, _tank={"fill_valve_ok": False})), True)
        self.assertIs(self.holds("running_toilet", dict(sound, running=True, _leak_lpm=0.25)), True)

    def test_no_hot_water_and_frozen_pipes(self):
        self.assertIs(self.holds("no_hot_water", {"type": "water_heater", "tank_temp_c": 49.0}), False)
        self.assertIs(self.holds("no_hot_water", {"type": "water_heater", "tank_temp_c": 18.0}), True)
        self.assertIs(self.holds("no_hot_water", {"type": "kitchen_sink"}), None)
        self.assertIs(self.holds("frozen_pipe", {"type": "outdoor_spigot", "frozen_burst": True}), True)
        self.assertIs(self.holds("frozen_pipe", {"type": "outdoor_spigot"}), None)

    def test_the_words_parse_to_this_domains_issues(self):
        from house_sim.kernel import speech
        for text, issue in (("There is a running toilet in the bathroom", "running_toilet"),
                            ("The toilet has a phantom flush", "running_toilet"),
                            ("The spigot has a frozen pipe", "frozen_pipe")):
            self.assertIn(issue, [c.get("value") for c in speech.parse(text) if c.get("kind") == "issue"], text)


class ReadingTests(unittest.TestCase):
    """15.2: a held instrument's reading is a structured result {"subject", "quantity", "value", "unit"} the evidence
    index reads from the action's message (it was free text under "measured" only)."""

    def test_a_probe_reading_of_the_stream_is_indexed_for_the_tap(self):
        from house_sim.tasks import perceive
        m = Mini()
        m.kitchen_sink()
        m.add("probe_1", "generic_item", "kitchen", label="instant-read thermometer", tool_kind="thermometer",
              accuracy_c=0.5, size_m=[0.15, 0.03, 0.02], portable=True)
        e = m.env(room="kitchen", pos=[3, 1], hold={"left": "probe_1"}, perception="realistic")
        perceive.enable(e)
        h = e.realism.handles.handle
        ok(self, e.step("turn_on_water", h("ksink"), temp="hot", flow=1.0))
        ok(self, e.step("wait", seconds=40))
        ok(self, e.step("inspect", h("ksink"), where="closely"))
        found = [r for r in perceive.index(e).readings if r["subject"] == "ksink"]
        self.assertEqual([(r["quantity"], r["unit"]) for r in found], [("temperature", "C")])
        self.assertAlmostEqual(found[0]["value"], ent(e, "ksink")["outlet_temp_c"], delta=1.0)

    def test_an_unfamiliar_held_thermometer_measures_nothing(self):
        import json
        m = Mini()
        m.kitchen_sink()
        m.add("probe_1", "generic_item", "kitchen", label="thing", tool_kind="thermometer", accuracy_c=0.5,
              size_m=[0.2, 0.15, 0.25], portable=True, novel=True, color="gray", material="plastic")
        e = m.env(room="kitchen", pos=[3, 1], hold={"left": "probe_1"}, perception="realistic")
        h = e.realism.handles.handle
        ok(self, e.step("turn_on_water", h("ksink"), temp="hot", flow=1.0))
        ok(self, e.step("wait", seconds=40))
        details = json.loads(ok(self, e.step("inspect", h("ksink"), where="closely"))["message"])
        self.assertNotIn("measured", details)
        self.assertNotIn("readings", details)


class UnfamiliarTests(unittest.TestCase):
    """The twin sweep of every plumbing_liquids verb x every entity parameter (scratch sweep.py novel): an unfamiliar
    hose, nozzle or plunger was answered by its true type ("It is not connected to anything", "Nozzle open", "Nothing
    to plunge there") where every other unfamiliar thing got "That is not a hose or nozzle" / "Hold a plunger"."""

    LOOK = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "label": "thing"}

    def outcomes(self, kind, verb, held=False, **args):
        m = Mini()
        m.add("table", "dining_table", "living", pos=[3, 3])
        m.add("thing", kind, "table", spos=[0.0, 0.0], **self.LOOK)
        e = m.env(room="living", pos=[3, 2], perception="realistic")
        h = e.realism.handles.handle
        if held:
            ok(self, e.step("pick_up", h("thing")))
        r = e.step(verb, h("thing"), **args)
        return (r["ok"], r["category"], r["message"])

    def test_every_verb_answers_an_unfamiliar_hose_nozzle_or_plunger_like_any_unfamiliar_thing(self):
        for verb, held in (("disconnect_hose", False), ("drain", False), ("flush", False), ("turn_on_water", False),
                           ("turn_off_water", True), ("spray", True), ("plunge", True)):
            seen = {self.outcomes(kind, verb, held) for kind in ("garden_hose", "hose_nozzle", "cup_plunger", "water_carafe")}
            self.assertEqual(len(seen), 1, (verb, seen))


class LookAlikeTests(unittest.TestCase):
    """The twin sweep of every verb x every entity parameter (scratch sweep.py lookalike, 78 groups): the one tell left was
    fill on the spray_bottle group: "That cannot hold water" for an unread cleaner's bottle, "It is already filled" or
    "Hold the container under the tap" for a blank one."""

    def answers(self, kind, held):
        m = Mini()
        m.kitchen_sink()
        m.add("table", "dining_table", "kitchen", pos=[3, 2])
        m.add("thing", kind, "table", spos=[0.0, 0.0])
        e = m.env(room="kitchen", pos=[3, 1], perception="realistic")
        h = e.realism.handles.handle
        if held:
            ok(self, e.step("pick_up", h("thing")))
        r = e.step("fill", h("thing"), source=h("ksink"))
        return (r["ok"], r["category"], r["message"]), e, h

    def test_filling_an_unread_spray_bottle_answers_every_member_alike(self):
        kinds = [k for k in ("spray_bottle", "trigger_bottle", "glass_cleaner", "degreaser", "all_purpose_cleaner")
                 if Mini().reg.known(k)]
        if len(kinds) < 3:
            self.skipTest("cleaning not loaded")
        for held in (False, True):
            seen = {self.answers(kind, held)[0] for kind in kinds}
            self.assertEqual(len(seen), 1, (held, seen))
            self.assertEqual(seen.pop()[1], "precondition")

    def test_once_read_the_bottle_answers_for_itself(self):
        if not Mini().reg.known("glass_cleaner"):
            self.skipTest("cleaning not loaded")
        _, e, h = self.answers("glass_cleaner", True)
        ok(self, e.step("read", h("thing")))
        r = e.step("fill", h("thing"), source=h("ksink"))
        self.assertEqual((r["ok"], r["message"]), (False, "That cannot hold water"))
        # a blank bottle has nothing to read and stays unresolved, but reading told what there is to tell
        m = Mini()
        m.kitchen_sink()
        m.add("bottle", "spray_bottle", "kitchen", pos=[3, 1], liquid_l=0.1, _mix={"water": 0.1})
        e = m.env(room="kitchen", pos=[3, 2], perception="realistic")
        h = e.realism.handles.handle
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", h("bottle")))
        ok(self, e.step("stand"))
        self.assertEqual(e.step("fill", h("bottle"), source=h("ksink"))["category"], "precondition")
        self.assertAlmostEqual(ent(e, "bottle")["liquid_l"], 0.1, 3)
        ok(self, e.step("read", h("bottle")))
        ok(self, e.step("fill", h("bottle"), source=h("ksink"), amount_l=0.3))
        self.assertGreater(ent(e, "bottle")["liquid_l"], 0.3)

    def pour_into(self, kind, verb="pour"):
        m = Mini()
        m.add("table", "dining_table", "kitchen", pos=[3, 2])
        m.add("thing", kind, "table", spos=[0.0, 0.0])
        m.add("jug", "water_carafe", "table", spos=[0.3, 0.0], liquid="water", liquid_l=0.2, _mix={"water": 0.2})
        e = m.env(room="kitchen", pos=[3, 1], perception="realistic")
        h = e.realism.handles.handle
        ok(self, e.step("pick_up", h("jug")))
        r = e.step(verb, h("jug"), h("thing"))
        return r["ok"], r["category"], r["message"].replace(h("thing"), "<thing>").replace(h("jug"), "<jug>")

    def test_pouring_into_an_unread_spray_bottle_answers_every_member_alike(self):
        kinds = [k for k in ("spray_bottle", "trigger_bottle", "glass_cleaner", "degreaser") if Mini().reg.known(k)]
        if len(kinds) < 3:
            self.skipTest("cleaning not loaded")
        for verb in ("pour", "empty"):
            seen = {self.pour_into(kind, verb) for kind in kinds}
            self.assertEqual(len(seen), 1, (verb, seen))

    def test_water_over_an_unresolved_cell_or_level_names_no_damage(self):
        """Electrical's ingress raises "X was damaged" for a NiMH cell or a digital level, none for an alkaline cell or
        a spirit level: the note told them apart. The damage itself is recorded."""
        for kinds in (("aa_battery", "nimh_aa_battery"), ("spirit_level", "digital_level")):
            if not all(Mini().reg.known(k) for k in kinds):
                self.skipTest("electrical / tools not loaded")
            self.assertEqual(len({self.pour_into(kind) for kind in kinds}), 1, kinds)

    def test_a_group_of_real_vessels_is_not_refused(self):
        self.assertFalse(H.group_mixes_vessels("no_such_group"))
        m = Mini()
        m.kitchen_sink()
        m.add("jug", "water_pitcher", "kitchen", pos=[3, 1], liquid_l=0.0)
        e = m.env(room="kitchen", pos=[3, 2], perception="realistic")
        h = e.realism.handles.handle
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", h("jug")))
        ok(self, e.step("stand"))
        ok(self, e.step("fill", h("jug"), source=h("ksink"), amount_l=0.5))


def ent_record(m, eid):
    return m.c["entities"][eid]


class WaterSensitiveGearTests(unittest.TestCase):
    """closeout_breakages row 7: a plugged wireless charging pad (registry water_safe true, not 'electronic') under a
    dripping pot was a waterproof drip catcher: 20 ml pooled on it and no phys.wetted hook ran."""

    def test_electrical_gear_is_water_sensitive_unless_rated(self):
        self.assertTrue(C.water_sensitive({"type": "wireless_charging_pad", "plug_required": True, "material": "plastic"}))
        self.assertTrue(C.water_sensitive({"type": "table_lamp", "category": "light"}))
        self.assertFalse(C.water_sensitive({"type": "outdoor_light", "category": "light", "weatherproof": True}))
        self.assertFalse(C.water_sensitive({"type": "utility_bucket", "material": "plastic"}))

    def test_drips_onto_a_plugged_charging_pad_wet_it_through_the_kernel(self):
        m = Mini()
        m.add("counter", "counter", "kitchen", pos=[5, 0], size_m=[2.0, 0.6, 0.9])
        if m.reg.known("wireless_charging_pad"):
            m.add("pad", "wireless_charging_pad", "counter", spos=[0.0, 0.0], plugged_in=True)
        else:                                       # (electrical not loaded: its type's record as a generic item)
            m.add("pad", "generic_item", "counter", spos=[0.0, 0.0], plugged_in=True, label="wireless charging pad",
                  category="charger", plug_required=True, surface=True, accepts_items=True, material="plastic",
                  size_m=[0.1, 0.1, 0.012], mass_kg=0.15, water_safe=True)
        self.assertIs(ent_record(m, "pad").get("water_safe"), True)          # the registry's default: not "electronic"
        m.add("pot", "generic_item", "pad", label="watered pot", material="ceramic", drip_lpm=0.02, size_m=[0.2, 0.2, 0.2],
              mass_kg=1.0, relation="on", spos=[0.0, 0.0])
        e = m.env(room="kitchen", pos=[5, 1])
        seen = []

        def hook(w, eid, liters, liquid, source):
            seen.append(eid)
        plugins.HOOKS["phys.wetted"].append((999, "test.seen", hook))
        try:
            wait(self, e, 120)
        finally:
            plugins.HOOKS["phys.wetted"] = [h for h in plugins.HOOKS["phys.wetted"] if h[2] is not hook]
        self.assertIn("pad", seen)
        self.assertGreater(ent(e, "pad").get("wetness", 0.0), 0.0)
        self.assertTrue(ent(e, "pad").get("water_damaged"))
        self.assertNotIn("pad", (e.world.s["plumbing"].get("drips_caught") or {}))


if __name__ == "__main__":
    unittest.main()
