"""Tier-1 physics: heat (docs/design/TIER1_PHYSICS.md 5.9 and the thermal half of the test plan of 12.4; package
PHYS-FLUIDS): time constants, contact heat, condensation and wet contact, thermal shock, freezing and freeze bursts,
melting, burns, heat content, the law entry points, and the kernel thermal step's equivalence with
physics.thermal_system.

The sibling packages' modules are the fakes of tests/test_physics_fluids.py (their frozen contracts over explicit
record fields and the material rows of section 4.3).
"""
import math
import time
import unittest

from house_sim.kernel import physics
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.phys import liquids, processes, thermal
from house_sim.kernel.world import World, clone

try:
    from test_physics_fluids import PhysTest, advance, item, mug, room, table, world
except ImportError:                     # run as tests/test_physics_thermal.py from the repository root
    from tests.test_physics_fluids import PhysTest, advance, item, mug, room, table, world

MIN = 60.0


# ----- time constants and heat capacity ------------------------------------------------------------------------------

class TimeConstantTests(PhysTest):
    def test_tau_anchors(self):
        bottle = item("kitchen", material="plastic", mass_kg=0.02, size_m=[0.065, 0.065, 0.2], capacity_l=0.5,
                      liquid_l=0.5, liquid="water")
        self.assertAlmostEqual(thermal.tau_s(bottle) / MIN, 56, delta=4)               # about 56 min
        coffee = mug("kitchen", liters=0.3, size_m=[0.09, 0.09, 0.095])
        tau = thermal.tau_s(coffee)
        self.assertAlmostEqual(tau / MIN, 50, delta=4)                                    # about 50 min
        self.assertAlmostEqual(-tau * math.log((60.0 - 21.0) / (85.0 - 21.0)) / MIN, 25, delta=3)   # 85 -> 60 degC
        pan = item("kitchen", material="cast_iron", mass_kg=2.5, size_m=[0.40, 0.26, 0.05])
        self.assertAlmostEqual(thermal.tau_s(pan) / MIN, 8, delta=1.5)                   # about 8 min
        bulb = item("kitchen", material="glass", mass_kg=0.03, size_m=[0.06, 0.06, 0.11])
        self.assertAlmostEqual(thermal.tau_s(bulb), 90, delta=20)                         # about 90 s

    def test_tau_clamps_explicit_value_and_the_kernel_switch(self):
        self.assertEqual(thermal.tau_s(item("kitchen", mass_kg=0.001, size_m=[0.5, 0.5, 0.001])), thermal.TAU_MIN_S)
        tank = item("kitchen", material="steel", mass_kg=50, size_m=[0.5, 0.5, 1.5], capacity_l=200, liquid_l=200)
        self.assertEqual(thermal.tau_s(tank), thermal.TAU_MAX_S)
        self.assertEqual(thermal.tau_s(dict(tank, thermal_tau_s=900)), 900)
        cup = mug("kitchen")
        self.assertEqual(thermal.kernel_tau_s(cup), physics.tau_s(cup))                  # law deferred: legacy
        self.assertEqual(thermal.kernel_tau_s(cup, lumped=True), thermal.tau_s(cup))
        self.fake.lawbook.on.add("thermal_tau")
        self.assertEqual(thermal.kernel_tau_s(cup), thermal.tau_s(cup))

    def test_heat_capacity_counts_body_liquid_and_ice(self):
        rec = item("kitchen", material="glass", mass_kg=0.4, capacity_l=1.0, liquid_l=0.5, liquid="oil")
        self.assertAlmostEqual(thermal.heat_capacity_j_k(rec), 0.4 * 840 + 0.5 * 0.92 * 1970)
        frozen = item("kitchen", material="plastic", mass_kg=0.0, capacity_l=1.0, liquid_l=1.0, ice_frac=0.5)
        self.assertAlmostEqual(thermal.heat_capacity_j_k(frozen), 0.5 * 4186 + 0.5 * 2100)
        self.assertAlmostEqual(thermal.heat_capacity_j_k(rec, cp=1000.0), 0.4 * 1000 + 0.5 * 0.92 * 1970)

    def test_owned_entities_keep_their_temperature_when_liquid_is_poured_in(self):
        w = world({"pot": item("kitchen", pos=[0, 0], material="steel", capacity_l=3.0, liquid_l=1.0,
                               temperature_c=90.0, thermal_owner="appliances_cooking")})
        thermal.mix_in(w, "pot", 1.0, 10.0, "water")
        self.assertEqual(w.s["entities"]["pot"]["temperature_c"], 90.0)
        self.assertTrue(thermal.owned({"fixed_temperature": True}))
        self.assertTrue(thermal.owned({"_phys_phase": {"on": True}}))
        self.assertFalse(thermal.owned({}))


# ----- contact heat -------------------------------------------------------------------------------------------------

class ContactHeatTests(PhysTest):
    def kitchen(self, support):
        return world({"top": support, "pan": item("top", material="cast_iron", mass_kg=2.5,
                                                  size_m=[0.4, 0.26, 0.05], temperature_c=200.0)})

    def test_a_200_degree_pan_scorches_wood_and_not_a_trivet(self):
        w = self.kitchen(table())
        self.assertEqual(thermal.contact_check(w, "pan", "top", by="robot"), "scorch")
        self.assertNotIn("damage", w.s["entities"]["top"])                  # it accrues over the next seconds
        advance(w, 30)
        self.assertAlmostEqual(w.s["entities"]["top"]["damage"]["scorch"], 0.5)   # min(1, 140 K / 100) x 30/60
        advance(w, 30)
        self.assertEqual(w.s["entities"]["top"]["damage"]["scorch"], 1.0)
        [record] = self.records(w)
        self.assertEqual((record["kind"], record["by"], record["note"]), ("phys.scorch", "robot", "top got a scorch mark"))
        w = self.kitchen(item("kitchen", pos=[0, 0], material="steel", size_m=[0.2, 0.2, 0.02], heat_limit_c=400))
        self.assertIsNone(thermal.contact_check(w, "pan", "top"))
        self.assertIsNone(processes.section(w.s))

    def test_accrual_stops_when_the_pan_is_lifted_or_cools(self):
        w = self.kitchen(table())
        thermal.contact_check(w, "pan", "top")
        advance(w, 6)
        self.assertAlmostEqual(w.s["entities"]["top"]["damage"]["scorch"], 0.1)
        w.mut("pan").update(location="robot")
        advance(w, 30)
        self.assertAlmostEqual(w.s["entities"]["top"]["damage"]["scorch"], 0.1)
        self.assertFalse(processes.is_active(w.s, "contact", "pan"))
        w = self.kitchen(table())
        w.s["entities"]["pan"]["temperature_c"] = 80.0
        self.assertEqual(thermal.contact_check(w, "pan", "top"), "ring_stain")   # below 150 C: a white heat ring
        advance(w, 60)
        self.assertAlmostEqual(w.s["entities"]["top"]["damage"]["ring_stain"], 0.2)    # 20 K over: at most 0.2
        self.assertEqual([(r["kind"], r["note"]) for r in self.records(w)], [("phys.ring", "top got a heat mark")])
        w.mut("pan")["temperature_c"] = 55.0
        advance(w, 30)
        self.assertFalse(processes.is_active(w.s, "contact", "pan"))

    def test_plastic_melts_glass_cracks_and_hooks_may_claim_the_contact(self):
        w = self.kitchen(item("kitchen", pos=[0, 0], material="plastic", size_m=[0.5, 0.4, 0.02]))
        self.assertEqual(thermal.contact_check(w, "pan", "top"), "melted")
        advance(w, 60)
        self.assertEqual(w.s["entities"]["top"]["damage"], {"melted": 1.0})
        w = self.kitchen(item("kitchen", pos=[0, 0], material="glass", size_m=[1.0, 0.6, 0.01], temperature_c=21.0))
        self.assertEqual(thermal.contact_check(w, "pan", "top"), "shock")       # (200 - 21) x 0.6 > 45 K
        self.assertTrue(w.s["entities"]["top"]["broken"])
        claimed = []
        self.hooks.add("phys.contact_heat", lambda w, hot, sid, temp, severity: claimed.append((hot, sid, severity))
                       or True)
        w = self.kitchen(table())
        self.assertIsNone(thermal.contact_check(w, "pan", "top"))
        self.assertEqual(claimed, [("pan", "top", 1.0)])
        # the same claim check, public for domains' legacy contact-heat code (textiles_laundry's adoption request 8)
        self.assertTrue(thermal.contact_claimed(w, "pan", "top", 200.0, 0.5))
        self.assertEqual(claimed[-1], ("pan", "top", 0.5))

    def test_nobody_claims_a_contact_without_a_hook(self):
        w = self.kitchen(table())
        self.assertFalse(thermal.contact_claimed(w, "pan", "top", 200.0, 1.0))

    def test_only_a_direct_support_counts(self):
        w = world({"table": table(), "trivet": item("table", material="cork", size_m=[0.2, 0.2, 0.01],
                                                     heat_limit_c=400),
                   "pan": item("trivet", material="cast_iron", temperature_c=200.0)})
        self.assertIsNone(thermal.contact_check(w, "pan", "table"))
        self.assertEqual(thermal.law_contact_heat(w, "pan"), [])
        self.assertIsNone(processes.section(w.s))

    def test_a_vessel_touches_its_support_through_a_cooler_wall(self):
        """Review finding (contact heat): the vessel's lumped temperature is the liquid's, but its foot ring sits
        at ambient + 0.75 of the excess (ceramic, glass, plastic walls), 0.95 through metal and 0.2 insulated."""
        hot = lambda **f: mug("top", liters=0.3, temperature_c=85.0, **f)
        w = world({"top": table(), "cup": hot()})
        self.assertAlmostEqual(thermal.contact_temp_c(w.s, "cup"), 21.0 + 0.75 * 64.0)        # 69 C, not 85 C
        w = world({"top": table(), "pot": hot(material="steel")})
        self.assertAlmostEqual(thermal.contact_temp_c(w.s, "pot"), 21.0 + 0.95 * 64.0)
        w = world({"top": table(), "flask": hot(material="steel", insulated=True)})
        self.assertAlmostEqual(thermal.contact_temp_c(w.s, "flask"), 21.0 + 0.2 * 64.0)
        w = world({"top": table(), "pan": item("top", material="cast_iron", temperature_c=85.0)})
        self.assertEqual(thermal.contact_temp_c(w.s, "pan"), 85.0)                   # a solid body: its own
        w = world({"top": table(), "cold": mug("top", liters=0.3, temperature_c=4.0)})
        self.assertEqual(thermal.contact_temp_c(w.s, "cold"), 4.0)

    def test_a_hot_mug_may_ring_wood_but_never_marks_plastic(self):
        """Review finding: a mug of 85 C coffee left a scorch on every wooden table and melted plastic trays.
        Wood (lacquer and oil finishes, about 60 C) gets a light white heat ring, not a scorch; a household plastic
        (about 100 C as a support finish; the fakes' class row is the spec's 80 C, so the record states it) is
        never marked, even by boiling water."""
        w = world({"top": table(), "cup": mug("top", liters=0.3, temperature_c=85.0)})
        self.assertEqual(thermal.contact_check(w, "cup", "top", by="robot"), "ring_stain")
        advance(w, 300)
        self.assertAlmostEqual(w.s["entities"]["top"]["damage"]["ring_stain"], 0.09)    # (69 - 60) / 100
        self.assertNotIn("scorch", w.s["entities"]["top"]["damage"])
        w = world({"top": table(), "cup": mug("top", liters=0.3, temperature_c=70.0)})
        self.assertIsNone(thermal.contact_check(w, "cup", "top"))                    # 57.8 C: no mark
        for temp in (85.0, 100.0):
            w = world({"tray": item("kitchen", pos=[0, 0], material="plastic", size_m=[0.5, 0.35, 0.02],
                                    heat_limit_c=100.0),
                       "cup": mug("tray", liters=0.3, temperature_c=temp)})
            self.assertIsNone(thermal.contact_check(w, "cup", "tray"), temp)
            advance(w, 300)
            self.assertNotIn("damage", w.s["entities"]["tray"])
        w = world({"tray": item("kitchen", pos=[0, 0], material="plastic", size_m=[0.5, 0.35, 0.02],
                                heat_limit_c=100.0), "pan": item("tray", material="cast_iron", temperature_c=200.0)})
        self.assertEqual(thermal.contact_check(w, "pan", "tray"), "melted")         # a hot pan still does
        w = world({"top": table(), "pan": item("top", material="cast_iron", temperature_c=200.0)})
        self.assertEqual(thermal.contact_check(w, "pan", "top"), "scorch")           # and still scorches wood


# ----- condensation and wet contact ----------------------------------------------------------------------------------

class WetContactTests(PhysTest):
    def test_dew_point_and_sweating(self):
        self.assertAlmostEqual(thermal.dew_point_c(21.0, 0.45), 8.6, places=1)
        self.assertAlmostEqual(thermal.dew_point_c(20.0, 1.0), 20.0, places=6)
        w = world({"can": item("kitchen", pos=[0, 0], material="aluminum", temperature_c=4.0),
                   "warm": item("kitchen", pos=[1, 0], temperature_c=10.0),
                   "outside": item("patio", pos=[0, 0], temperature_c=4.0)},
                  rooms={"kitchen": room(), "patio": room("concrete", outdoor=True)},
                  weather={"temperature_c": 6.0, "humidity": 0.9})
        self.assertTrue(thermal.sweats(w.s, "can"))                       # 4 degC < 8.6 - 1
        self.assertFalse(thermal.sweats(w.s, "warm"))
        self.assertFalse(thermal.sweats(w.s, "outside"))                  # outdoors: dew point 4.5 degC in the rain
        w.s["entities"]["outside"]["temperature_c"] = 2.0
        self.assertTrue(thermal.sweats(w.s, "outside"))

    def scene(self, *on_table):
        entities = {"table": table()}
        entities.update(on_table)
        return world(entities)

    def test_a_cold_can_rings_an_unprotected_wood_table_but_not_one_with_a_coaster(self):
        w = self.scene(("can", item("table", material="aluminum", temperature_c=4.0, size_m=[0.066, 0.066, 0.122])))
        self.assertEqual(thermal.wet_contact_check(w, "can", "table"), "ring_stain")
        self.assertIsNone(thermal.wet_contact_check(w, "can", "table"))   # one pending check per item and support
        advance(w, 570)
        self.assertNotIn("damage", w.s["entities"]["table"])
        advance(w, 30)
        self.assertEqual(w.s["entities"]["table"]["damage"], {"ring_stain": 0.3})
        [record] = self.records(w)
        self.assertEqual((record["kind"], record["note"]), ("phys.ring", "table got a water mark"))
        w = self.scene(("coaster", item("table", material="cork", size_m=[0.1, 0.1, 0.005])),
                       ("can", item("coaster", material="aluminum", temperature_c=4.0)))
        self.assertIsNone(thermal.wet_contact_check(w, "can"))            # its direct support is the cork coaster
        self.assertEqual(thermal.law_wet_contact(w, "can"), [])
        advance(w, 1200)
        self.assertNotIn("damage", w.s["entities"]["table"])

    def test_the_check_needs_the_item_still_there_and_still_wet(self):
        w = self.scene(("glass", item("table", wetness=0.5)), ("can", item("table", temperature_c=4.0)))
        thermal.wet_contact_check(w, "glass", "table")
        thermal.wet_contact_check(w, "can", "table")
        w.mut("glass")["wetness"] = 0.1
        w.mut("can")["location"] = "kitchen"
        advance(w, 900)
        self.assertNotIn("damage", w.s["entities"]["table"])
        self.assertEqual(w.s["physics"]["queue"], [])

    def test_soil_only_gets_damp_and_damage_is_read_in_every_shape(self):
        w = world({"bed": item("kitchen", pos=[0, 0], material="granular", mass_kg=40.0, size_m=[1.0, 0.5, 0.3]),
                   "pot": item("bed", wetness=1.0)})
        self.assertIsNone(thermal.wet_contact_check(w, "pot"))
        self.assertEqual(thermal.damage_level({"damage": {"scorch": 0.4}}, "scorch"), 0.4)
        self.assertEqual(thermal.damage_level({"damage": {"scorch": True}}, "scorch"), 1.0)
        self.assertEqual(thermal.damage_level({"damage": ["scorch"]}, "scorch"), 1.0)
        self.assertEqual(thermal.damage_level({"damage": [{"kind": "dent"}]}, "dent"), 1.0)
        self.assertEqual(thermal.damage_level({"damage": 0.5}, "scorch"), 0.0)
        self.assertEqual(thermal.damage_level({}, "scorch"), 0.0)

    def test_mdf_swells_and_paper_or_electronics_get_water_damage_later(self):
        wetted = []
        self.hooks.add("phys.wetted", lambda w, eid, liters, liquid, source: wetted.append((eid, source)))
        w = world({"shelf": item("kitchen", pos=[0, 0], material="engineered_wood", size_m=[0.8, 0.3, 0.02]),
                   "book": item("kitchen", pos=[1, 0], material="paper", size_m=[0.2, 0.15, 0.03]),
                   "laptop": item("kitchen", pos=[2, 0], material="electronics", size_m=[0.33, 0.23, 0.02]),
                   "counter": item("kitchen", pos=[3, 0], material="stone", size_m=[1.0, 0.6, 0.04]),
                   "vase": item("shelf", wetness=0.6), "cup": item("book", wetness=0.6),
                   "bottle": item("laptop", wetness=0.6), "jug": item("counter", wetness=0.6)})
        self.assertEqual([thermal.wet_contact_check(w, eid) for eid in ("vase", "cup", "bottle", "jug")],
                         ["swollen", "water_damage", "water_damage", None])
        advance(w, 600)
        self.assertNotIn("damage", w.s["entities"]["shelf"])
        advance(w, 300)
        self.assertEqual(w.s["entities"]["shelf"]["damage"], {"swollen": 0.3})
        self.assertEqual(w.s["entities"]["book"]["damage"], {"water_damage": 0.3})
        self.assertEqual(sorted(wetted), [("book", "cup"), ("laptop", "bottle")])


# ----- thermal shock -------------------------------------------------------------------------------------------------

class ThermalShockTests(PhysTest):
    def test_96_kelvin_breaks_soda_lime_and_not_borosilicate(self):
        w = world({"jar": item("kitchen", pos=[0, 0], material="glass", temperature_c=4.0),
                   "dish": item("kitchen", pos=[1, 0], material="borosilicate", temperature_c=4.0)})
        self.assertFalse(thermal.shock_check(w, "dish", 100.0, thermal.COUPLING_IMMERSION))
        self.assertTrue(thermal.shock_check(w, "jar", 100.0, thermal.COUPLING_IMMERSION, by="robot"))
        self.assertTrue(w.s["entities"]["jar"]["broken"])
        self.assertFalse(thermal.shock_check(w, "jar", 100.0, 1.0))        # already broken
        record = [r for r in self.records(w) if r["kind"] == "phys.shock"][0]
        self.assertEqual((record["note"], record["by"]), ("jar cracked", "robot"))
        self.assertNotRegex(record["note"], r"\d")

    def test_enamel_chips_instead_of_breaking_and_steel_never_shocks(self):
        w = world({"pot": item("kitchen", pos=[0, 0], material="enameled_metal", temperature_c=250.0),
                   "pan": item("kitchen", pos=[1, 0], material="steel", temperature_c=250.0)})
        self.assertTrue(thermal.shock_check(w, "pot", 10.0, 1.0))
        self.assertFalse(w.s["entities"]["pot"].get("broken"))
        self.assertGreater(w.s["entities"]["pot"]["damage"]["chipped"], 0)
        self.assertFalse(thermal.shock_check(w, "pan", 10.0, 1.0))

    def test_where_an_item_rests_sets_the_coupling(self):
        w = world({"oven": item("kitchen", pos=[0, 0], internal_temp_c=220.0, accepts_items=True),
                   "freezer": item("kitchen", pos=[1, 0], internal_temp_c=-18.0, accepts_items=True),
                   "sink": item("kitchen", pos=[2, 0], capacity_l=20, liquid_l=8, temperature_c=60.0),
                   "slab": item("kitchen", pos=[3, 0], material="stone", wetness=0.6, temperature_c=15.0),
                   "board": item("kitchen", pos=[4, 0], material="wood", wetness=0.6),
                   "cold_dish": item("oven", relation="in", material="glass", temperature_c=4.0),
                   "hot_dish": item("freezer", relation="in", material="glass", temperature_c=90.0),
                   "glass": item("sink", relation="in", material="glass", temperature_c=21.0),
                   "hot_pan": item("slab", material="glass", temperature_c=90.0),
                   "on_board": item("board", material="glass", temperature_c=90.0)})
        self.assertEqual(thermal.shock_exposure(w.s, "cold_dish"), (220.0, 0.5))
        self.assertEqual(thermal.shock_exposure(w.s, "hot_dish"), (-18.0, 0.3))
        self.assertEqual(thermal.shock_exposure(w.s, "glass"), (60.0, 0.6))      # into hotter water: upshock
        self.assertEqual(thermal.shock_exposure(w.s, "hot_pan"), (15.0, 0.6))
        self.assertIsNone(thermal.shock_exposure(w.s, "on_board"))
        self.assertEqual([r["kind"] for r in thermal.law_thermal_shock(w, "cold_dish")], ["phys.shock", "phys.break"])
        self.assertEqual(thermal.law_thermal_shock(w, "glass"), [])        # 39 K x 0.6 < 45 K
        w.mut("glass")["temperature_c"] = 120.0                           # a hot glass into 60 degC water
        self.assertEqual(thermal.shock_exposure(w.s, "glass"), (60.0, 1.0))      # downshock: 60 K > 45 K
        self.assertEqual([r["kind"] for r in thermal.law_thermal_shock(w, "glass")], ["phys.shock", "phys.break"])
        self.assertEqual((thermal.liquid_coupling(21.0, 100.0), thermal.liquid_coupling(90.0, 10.0)), (0.6, 1.0))
        self.assertEqual(thermal.law_thermal_shock(w, "on_board"), [])


# ----- freezing, thawing and bursts ----------------------------------------------------------------------------------

def jar(location, fill, **fields):
    """A sealed 1 L wide-mouth glass jar (mason style) filled to `fill` with water."""
    rec = {"material": "glass", "mass_kg": 0.4, "size_m": [0.1, 0.1, 0.17], "capacity_l": 1.0,
           "liquid_l": fill, "liquid": "water", "lid_on": True, "relation": "in", "mouth_frac": 0.85}
    rec.update(fields)
    return item(location, **rec)


class PhaseTests(PhysTest):
    def freezer(self, *contents):
        entities = {"freezer": item("kitchen", pos=[0, 0], internal_temp_c=-18.0, accepts_items=True,
                                    container=True)}
        entities.update(contents)
        return world(entities)

    def test_freeze_burst_at_zero_headspace_and_not_at_ten_percent(self):
        w = self.freezer(("full", jar("freezer", 1.0, ice_frac=0.1)), ("headroom", jar("freezer", 0.9, ice_frac=1.0)))
        self.assertEqual(thermal.freeze_burst_check(w, "full"), "burst")
        self.assertTrue(w.s["entities"]["full"]["broken"])
        self.assertIsNone(thermal.freeze_burst_check(w, "headroom"))        # 9 % x 0.9 = 8.1 % < 10 %
        bottle = jar("freezer", 0.9, ice_frac=1.0, mouth_frac=0.3)
        w = self.freezer(("bottle", bottle))
        self.assertEqual(thermal.freeze_burst_check(w, "bottle"), "burst")  # a narrow neck plugs: needs 11.1 %
        self.assertIsNone(thermal.freeze_burst_check(w, "bottle"))

    def test_a_domain_can_pass_its_own_fill_and_ice(self):
        """Adoption-round request (food 3): food keeps liquid and ice in its own model, so it passes fill and ice
        instead of copying the formula; the record's liquid_l and ice_frac are not read then."""
        w = self.freezer(("soup", jar("freezer", 0.0)), ("stock", jar("freezer", 0.0)))
        self.assertIsNone(thermal.freeze_burst_check(w, "soup"))                          # no liquid on record
        self.assertIsNone(thermal.freeze_burst_check(w, "soup", fill=0.9, ice=1.0))       # 8.1 % into 10 %
        self.assertEqual(thermal.freeze_burst_check(w, "soup", fill=1.0, ice=0.5), "burst")
        self.assertEqual(thermal.freeze_burst_check(w, "stock", fill=0.98, ice=1.0), "burst")
        self.assertIsNone(thermal.freeze_burst_check(w, "stock", fill=1.0, ice=1.0))       # already broken

    def test_cans_bulge_then_burst_plastic_bulges_and_open_or_soft_containers_never_burst(self):
        w = self.freezer(("can", jar("freezer", 0.92, material="aluminum", ice_frac=0.5)),
                         ("can2", jar("freezer", 1.0, material="aluminum", ice_frac=0.5)),
                         ("bottle", jar("freezer", 1.0, material="plastic", ice_frac=1.0)),
                         ("open", jar("freezer", 1.0, lid_on=False, ice_frac=1.0)),
                         ("pouch", jar("freezer", 1.0, material="film", ice_frac=1.0)))
        self.assertIsNone(thermal.freeze_burst_check(w, "can"))             # 4.1 % expansion into 8 % headspace
        w.s["entities"]["can"]["ice_frac"] = 1.0
        self.assertEqual(thermal.freeze_burst_check(w, "can"), "bulged")    # 8.3 %: 0.3 % too much
        self.assertAlmostEqual(w.s["entities"]["can"]["damage"]["bulged"], round((0.0828 - 0.08) / 0.02, 4))
        self.assertEqual(thermal.freeze_burst_check(w, "can2"), "burst")    # 4.5 % excess > 2 %
        self.assertEqual(thermal.freeze_burst_check(w, "bottle"), "bulged")
        self.assertEqual(w.s["entities"]["bottle"]["damage"]["bulged"], 1.0)
        self.assertIsNone(thermal.freeze_burst_check(w, "open"))
        self.assertIsNone(thermal.freeze_burst_check(w, "pouch"))
        self.assertEqual([r["kind"] for r in self.records(w)].count("phys.burst"), 3)

    def test_a_bottle_in_the_freezer_holds_at_zero_while_it_freezes_then_bursts_full(self):
        bottle = jar("freezer", 0.9, temperature_c=0.0, material="plastic", mass_kg=0.03, size_m=[0.08, 0.08, 0.25])
        w = self.freezer(("bottle", bottle), ("full", jar("freezer", 1.0, temperature_c=0.0)))
        self.assertEqual(thermal.phase_needed(w.s, "bottle"), "freeze")
        self.assertEqual(thermal.law_phase(w, "bottle"), [])
        thermal.law_phase(w, "full")
        self.assertTrue(thermal.owned(w.s["entities"]["bottle"]))
        record = w.s["entities"]["bottle"]
        advance(w, 600)
        # the latent heat leaves through the lumped conductance (10 W/m2K over 0.091 m2), whatever thermal_tau's state
        rate = 600 * 1.0 * 18.0 * 10.0 * 2 * (0.08 * 0.08 + 2 * 0.08 * 0.25) / (0.9 * 334000.0)
        self.assertAlmostEqual(thermal.phase_rate_per_s(record, 1.0, 18.0) * 600, rate, places=9)
        self.assertAlmostEqual(w.s["entities"]["bottle"]["ice_frac"], rate, delta=0.001)    # 4-decimal steps
        self.assertEqual(w.s["entities"]["bottle"]["temperature_c"], 0.0)
        self.assertTrue(w.s["entities"]["full"]["broken"])                # 0 % headspace: the first ice bursts it
        self.assertFalse(processes.is_active(w.s, "phase", "full"))
        advance(w, 36000)
        self.assertEqual(w.s["entities"]["bottle"]["ice_frac"], 1.0)
        self.assertFalse(processes.is_active(w.s, "phase", "bottle"))
        self.assertFalse(thermal.owned(w.s["entities"]["bottle"]))        # the kernel thermal system cools it on

    def test_thawing_on_the_counter_and_what_has_no_phase(self):
        w = world({"tray": item("kitchen", pos=[0, 0], capacity_l=0.5, liquid_l=0.3, ice_frac=1.0,
                                temperature_c=0.0, liquid="water"),
                   "honey": item("kitchen", pos=[1, 0], capacity_l=0.5, liquid_l=0.3, liquid="honey",
                                 temperature_c=-5.0),
                   "soup": item("kitchen", pos=[2, 0], capacity_l=0.5, liquid_l=0.3, liquid="soup", ice_frac=1.0,
                                temperature_c=-1.0, fixed_temperature=True)})
        self.assertEqual(thermal.phase_needed(w.s, "tray"), "thaw")
        self.assertIsNone(thermal.phase_needed(w.s, "honey"))             # no freezing point
        self.assertIsNone(thermal.phase_needed(w.s, "soup"))              # food owns its temperature
        processes.start(w, "phase", "tray")
        advance(w, 36000)
        self.assertNotIn("ice_frac", w.s["entities"]["tray"])
        self.assertFalse(processes.is_active(w.s, "phase", "tray"))
        self.assertFalse(processes.start(w, "phase", "honey"))

    def test_thaw_time_is_the_latent_heat_over_the_lumped_conductance_whatever_thermal_tau_says(self):
        """Review finding: with phase active and thermal_tau deferred the rate divided by 80 K x the legacy kernel
        time constant (300 sqrt(m) + 60 s), so a frozen 0.5 L bottle thawed in 17.5 min instead of about 3.4 h.
        The latent heat (0.5 kg x 334 kJ) leaves through h = 10 W/m2K over the box area whichever law is on."""
        size = [0.065, 0.065, 0.2]
        area = 2 * (size[0] * size[1] + 2 * size[0] * size[2])
        expected = 0.5 * 334000.0 / (10.0 * area * 21.0)                         # about 3.4 h
        times = []
        for lumped in (False, True):
            if lumped:
                self.fake.lawbook.on.add("thermal_tau")
            w = world({"bottle": item("kitchen", pos=[0, 0], material="plastic", mass_kg=0.02, size_m=size,
                                      capacity_l=0.5, liquid_l=0.5, liquid="water", ice_frac=1.0,
                                      temperature_c=0.0, lid_on=True)})
            self.assertTrue(processes.start(w, "phase", "bottle"))
            seconds = 0.0
            while processes.is_active(w.s, "phase", "bottle") and seconds < 10 * expected:
                advance(w, 60.0)
                seconds += 60.0
            times.append(seconds)
        self.assertEqual(times[0], times[1])
        self.assertAlmostEqual(times[0] / expected, 1.0, delta=0.01)
        self.assertGreater(expected, 3 * 3600)

    def test_slow_thawing_keeps_its_rate_although_ice_frac_is_stored_to_4_decimals(self):
        """Review probe: near the freezing point a substep moves ice_frac by about 1e-4 or less, so rounding the
        stored value each substep made a frozen bottle thaw 10-15 % too slowly (or too fast). The phase process keeps
        the exact progress; ice_frac shows it to 4 decimals. Reference: the same rate integrated without rounding
        (phase_rate_per_s, recomputed every substep)."""
        rooms = {"kitchen": room(temperature_c=0.5)}
        for liters in (0.5, 2.0):
            w = world({"bottle": item("kitchen", pos=[0, 0], material="plastic", mass_kg=0.03, capacity_l=liters,
                                      liquid_l=liters, liquid="water", ice_frac=1.0, temperature_c=0.0,
                                      size_m=[0.08, 0.08, 0.25] if liters < 1 else [0.12, 0.12, 0.3])},
                      rooms=rooms)
            ambient, coupling = physics.ambient(w.s, "bottle")
            record = dict(w.s["entities"]["bottle"])

            def per_step(ice):
                return 30.0 * thermal.phase_rate_per_s(dict(record, ice_frac=ice), coupling, ambient - 0.0)
            self.assertLess(per_step(1.0), 2e-4)                                # below the stored precision
            ideal, ideal_steps = 1.0, 0
            while ideal > 0.0:
                ideal = max(0.0, ideal - per_step(ideal))
                ideal_steps += 1
            self.assertTrue(processes.start(w, "phase", "bottle"))
            steps = 0
            while processes.is_active(w.s, "phase", "bottle") and steps < 2 * ideal_steps:
                advance(w, 30.0)
                steps += 1
                if steps == 10:
                    shown = w.s["entities"]["bottle"]["ice_frac"]
                    exact = processes.params(w.s, "phase", "bottle")["ice"]
                    self.assertEqual(shown, round(exact, 4))
                    self.assertNotEqual(exact, shown)                           # more precise than what is shown
            self.assertNotIn("ice_frac", w.s["entities"]["bottle"])
            self.assertLessEqual(abs(steps - ideal_steps), max(2, ideal_steps // 1000), (steps, ideal_steps))
        w = world({"tray": item("kitchen", pos=[0, 0], capacity_l=0.5, liquid_l=0.3, ice_frac=1.0,
                                temperature_c=0.0, liquid="water")}, rooms=rooms)
        processes.start(w, "phase", "tray")
        advance(w, 300.0)
        w.mut("tray")["ice_frac"] = 0.5                                         # a domain sets it: that wins
        advance(w, 30.0)
        self.assertLess(w.s["entities"]["tray"]["ice_frac"], 0.5)
        self.assertGreater(w.s["entities"]["tray"]["ice_frac"], 0.499)

    def test_heat_content_is_the_freezer_load(self):
        bottle = item("kitchen", material="plastic", mass_kg=0.02, capacity_l=0.5, liquid_l=0.5, liquid="water")
        load = thermal.heat_content_j(bottle, -18.0)
        expected = 0.02 * 1500 * 39 + 0.5 * (334000 + 4186 * 21 + 2100 * 18)
        self.assertAlmostEqual(load, expected)
        self.assertAlmostEqual(load / 1e6, 0.23, places=2)
        self.assertAlmostEqual(thermal.heat_content_j(dict(bottle, liquid="honey"), 0.0),
                               0.02 * 1500 * 21 + 0.5 * 1.42 * 2250 * 21)
        self.assertAlmostEqual(thermal.heat_content_j(dict(bottle, ice_frac=1.0, temperature_c=-18.0), 21.0), -load)


# ----- melting and burns ---------------------------------------------------------------------------------------------

class MeltBurnTests(PhysTest):
    def test_wax_melts_at_50_degrees(self):
        w = world({"candle": item("kitchen", pos=[0, 0], material="wax", temperature_c=49.0),
                   "hot": item("kitchen", pos=[1, 0], material="wax", temperature_c=60.0),
                   "lid": item("kitchen", pos=[2, 0], material="plastic", temperature_c=125.0)})
        self.assertFalse(processes.start(w, "melting", "candle"))
        self.assertEqual(thermal.melt_check(w, "candle", 600), 0.0)
        thermal.law_melt(w, "hot")
        advance(w, 300)
        self.assertAlmostEqual(w.s["entities"]["hot"]["damage"]["melted"], 0.5)
        advance(w, 300)
        self.assertEqual(w.s["entities"]["hot"]["damage"]["melted"], 1.0)
        self.assertFalse(processes.is_active(w.s, "melting", "hot"))
        self.assertEqual([r["kind"] for r in self.records(w)], ["phys.melt"])
        self.assertAlmostEqual(thermal.melt_check(w, "lid", 600), 0.5)    # 5 K over 120 degC: half
        self.assertIn("deformed", w.s["entities"]["lid"]["damage"])

    def test_burn_seconds_table(self):
        for temp, seconds in ((44, 21600), (47, 1200), (50, 180), (55, 20), (60, 5), (65, 2), (70, 1)):
            self.assertAlmostEqual(thermal.burn_seconds(temp), seconds, places=6)
        self.assertEqual(thermal.burn_seconds(43.9), math.inf)
        self.assertAlmostEqual(thermal.burn_seconds(52.5), math.sqrt(180 * 20))      # log-linear in between
        self.assertAlmostEqual(thermal.burn_seconds(75), 0.5)                         # the last slope continues
        self.assertAlmostEqual(thermal.burn_seconds(60, child=True), 1.25)
        times = [thermal.burn_seconds(t) for t in range(44, 90)]
        self.assertEqual(times, sorted(times, reverse=True))


# ----- the kernel thermal step ---------------------------------------------------------------------------------------

def perturbed(seed):
    """A generated house with hot and cold items, an oven, a conducting pot and an insulated box."""
    state = clone(HouseEnv(seed=seed).world.s)
    entities = state["entities"]
    ids = sorted(entities)
    for i, eid in enumerate(ids):
        if i % 7 == 0:
            entities[eid]["temperature_c"] = 85.0
        elif i % 11 == 0:
            entities[eid]["temperature_c"] = 4.0
    holders = [e for e in ids if entities[e].get("accepts_items")]
    entities[holders[0]]["internal_temp_c"] = 180.0
    entities[holders[1]].update(conducts_heat=True, temperature_c=95.0)
    entities[holders[2]]["insulated"] = True
    return state


class ThermalStepTests(PhysTest):
    def test_identical_to_the_kernel_thermal_system_on_generated_houses(self):
        for seed in (0, 1, 2):
            base = perturbed(seed)
            legacy, step = World(clone(base)), World(clone(base))
            legacy.begin()
            step.begin()
            for tick in range(20):
                physics.thermal_system(legacy, 120.0, tick)
                thermal.thermal_step(step, 120.0, tick)
                self.assertEqual(legacy.s, step.s, "seed {} tick {}".format(seed, tick))
            self.assertEqual(sorted(legacy.journal_keys()), sorted(step.journal_keys()))

    def test_faster_than_the_kernel_thermal_system_on_a_settled_house(self):
        settled = World(clone(HouseEnv(seed=2).world.s))
        for tick in range(120):
            physics.thermal_system(settled, 120.0, tick)
        # a relative bound: asserted in the perf job, recorded in the correctness runs (whose real clock it reads;
        # tools/orchestration/wallclock.py, phase-1 close-out: the body runs there too)
        from tools.orchestration.wallclock import wall_clock_bound

        def best(fn):
            times = []
            for _ in range(7):
                w = World(clone(settled.s))
                start = time.perf_counter()
                for tick in range(5):
                    fn(w, 120.0, tick)
                times.append(time.perf_counter() - start)
            return min(times) / 5 * 1000.0

        legacy_ms, step_ms = best(physics.thermal_system), best(thermal.thermal_step)
        wall_clock_bound(self, step_ms, legacy_ms, "thermal_step vs legacy (ms)")
        # amortized over the four 30 s substeps of its 120 s period
        wall_clock_bound(self, step_ms / 4, 1.5, "amortized thermal step (ms)")

    def test_owned_entities_and_the_quiet_writer(self):
        w = world({"pot": item("kitchen", pos=[0, 0], temperature_c=90.0, thermal_owner="food"),
                   "cup": item("kitchen", pos=[1, 0], temperature_c=90.0)})
        quiet = []

        def mut_quiet(eid):
            quiet.append(eid)
            return w.mut(eid)

        thermal.thermal_step(w, 120.0, 0, mut=mut_quiet)
        self.assertEqual(w.s["entities"]["pot"]["temperature_c"], 90.0)
        self.assertEqual(quiet, ["cup"])
        self.assertLess(w.s["entities"]["cup"]["temperature_c"], 90.0)

    def test_with_the_phase_law_a_liquid_stops_at_its_freezing_point(self):
        self.fake.lawbook.on.update(("phase", "melt"))
        w = world({"freezer": item("kitchen", pos=[0, 0], internal_temp_c=-18.0, accepts_items=True,
                                   container=True),
                   "bottle": jar("freezer", 0.5, temperature_c=3.0, lid_on=False),
                   "crayon": item("kitchen", pos=[1, 0], material="wax", temperature_c=48.0),
                   "radiator": item("kitchen", pos=[2, 0], internal_temp_c=70.0, accepts_items=True,
                                    container=True)})
        w.s["entities"]["crayon"]["location"] = "radiator"
        thermal.thermal_step(w, 600.0, 0)
        self.assertEqual(w.s["entities"]["bottle"]["temperature_c"], 0.0)
        self.assertTrue(processes.is_active(w.s, "phase", "bottle"))
        self.assertTrue(processes.is_active(w.s, "melting", "crayon"))
        thermal.thermal_step(w, 600.0, 1)
        self.assertEqual(w.s["entities"]["bottle"]["temperature_c"], 0.0)   # owned by the plateau now


# ----- law entry points ----------------------------------------------------------------------------------------------

class LawTests(PhysTest):
    def test_laws_table_and_signature(self):
        self.assertEqual([(law, order) for law, order, _ in liquids.LAWS + thermal.LAWS],
                         [("overflow", 20), ("open_spill", 70), ("contact_heat", 80), ("wet_contact", 85),
                          ("thermal_shock", 90), ("phase", 95), ("melt", 96)])
        w = world({"table": table(), "pan": item("table", material="cast_iron", temperature_c=200.0),
                   "ghost": item("kitchen", pos=[0, 0])})
        for _, _, law in liquids.LAWS + thermal.LAWS:
            self.assertEqual(law(w, "missing", env=None, cmap=None), [])
            self.assertIsInstance(law(w, "ghost"), list)
        self.assertEqual(thermal.law_contact_heat(w, "pan"), [])             # the mark accrues later
        self.assertTrue(processes.is_active(w.s, "contact", "pan"))


if __name__ == "__main__":
    unittest.main()
