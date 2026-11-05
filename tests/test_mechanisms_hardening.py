"""Mechanisms' verification-hardening package (docs/design/VERIFY_HARDENING.md 6.2-6.5, 8.2, 10.3, 13.3, 15.2, 23.3; WP-D
mechanisms; docs/domains/mechanisms.md section 21): field classes (L-WATCH), writer classes and causes of the time systems,
real spills, meters, the pump gauge's structured reading, derived fields of what the hooks latch, settings skills and
damaging verbs, truth mappings, hazard zones, and the phase-1 carry-over (the test swing's direction, the one water
sensitivity predicate, the unfamiliar padlock's and hasp's identify-first refusal).

Run isolated:   HOUSE_SIM_DOMAINS=core,mechanisms python -m unittest tests/test_mechanisms_hardening.py
Strict journal: HOUSE_SIM_STRICT=1 python -m unittest tests/test_mechanisms_hardening.py
"""
import json
import os
import sys
import unittest
from pathlib import Path

from house_sim.kernel import evaluation, ledger, plugins, speech
from house_sim.kernel.actions import NOVEL_OPERATE_MESSAGE
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import load_domains
from house_sim.kernel.registry import default_registry
from house_sim.systems.mechanisms import common as C
from house_sim.tasks.runner import Runner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_mechanisms import House, H, ent, mech, no, ok, stat  # noqa: E402

load_domains()
ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "house_sim" / "data" / "verify" / "watch" / "mechanisms.json"
TYPES = ROOT / "house_sim" / "data" / "types" / "mechanisms.json"


def hardened(**block):
    block.setdefault("envelope", {})
    return {"success": [], "failure": [], "hardening": block}


def run_step(env, step):
    Runner(env, {}).skill(step["skill"], **{k: v for k, v in step.items() if k != "skill"})


def spills_in(env):
    return [e for e, r in env.world.s["entities"].items() if r.get("_phys_spill") or r.get("category") == "spill"]


# ======================================================================================
class FieldClassTests(unittest.TestCase):
    """6.3, lint L-WATCH part 2; the requests of climate, safety and admin_inventory's as-if runs (mech.kind, bolt_throw,
    ingress_l, shifted showed on every closure a reference worked)."""

    def test_every_type_property_is_classified_explicitly(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import field_census
        finally:
            sys.path.pop(0)
        self.assertEqual({"mechanisms": []}, field_census.unclassified(["mechanisms"]))

    def test_every_written_mechanism_field_is_classified_on_this_domains_types(self):
        data = json.loads(WATCH.read_text(encoding="utf-8"))
        for path in data["writes"]:
            self.assertIsNotNone(ledger.field_spec("curtains", path, explicit_only=True), path)

    def test_bookkeeping_and_mirrors_of_a_closure_are_not_settings_on_the_kernels_types(self):
        for type_name in ("window", "gate", "door", "exterior_door", "drawer", "sliding_door", "garage_door"):
            self.assertEqual("bookkeeping", ledger.field_class(type_name, "mech.kind"), type_name)
            self.assertEqual("bookkeeping", ledger.field_class(type_name, "mech.tested_at"), type_name)
            self.assertEqual("process", ledger.field_class(type_name, "mech.bolt_throw"), type_name)
            self.assertEqual("process", ledger.field_class(type_name, "mech.ingress_l"), type_name)
        self.assertEqual("setting", ledger.field_class("window", "open"))
        self.assertEqual("setting", ledger.field_class("window", "mech.open_frac"))
        self.assertEqual("setting", ledger.field_class("curtains", "mech.panels"))
        self.assertEqual("process", ledger.field_class("curtains", "mech.gap_cm"))
        self.assertEqual("condition", ledger.field_class("curtains", "mech.jammed"))
        self.assertEqual("false", ledger.field_spec("screw_lid_jar", "mech.seal_intact")["worse"])
        self.assertEqual("identity", ledger.field_class("pump_bottle", "pump.ml_per_stroke"))
        self.assertEqual("setting", ledger.field_class("pump_bottle", "pump.locked"))
        self.assertEqual("process", ledger.field_class("pump_bottle", "pump.primed"))
        self.assertEqual("setting", ledger.field_class("trigger_bottle", "trigger.nozzle"))

    def test_this_domains_electronics_are_water_sensitive(self):
        self.assertTrue(ledger.water_sensitive({"type": "alarm_panel", "water_safe": True}))
        self.assertTrue(ledger.water_sensitive({"type": "motorized_blind", "water_safe": True}))
        self.assertFalse(ledger.water_sensitive({"type": "venetian_blind", "water_safe": True}))

    def test_closing_a_gate_a_window_and_a_shut_exterior_door_leaves_no_mechanism_bookkeeping_label(self):
        h = House()
        h.c["entities"]["side_gate"].update(open=True, latched=False)
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.5, "bolt_throw": 0.0}, open=True, locked=False)
        e = h.env(room="living", pos=(2, 1))
        ok(self, e.step("close", "win_living"))
        ok(self, e.step("pull_handle", "win_living", test=True))
        e2 = h.env(room="back", pos=(14, 6))
        ok(self, e2.step("close", "side_gate"))
        for env, ident in ((e, "win_living"), (e2, "side_gate")):
            found = ledger.failures(hardened(), env.world.s, env._baseline, env.world, None)
            bad = [label for label in found if ".mech." in label and label.split(".mech.")[1] not in ("open_frac", )]
            self.assertEqual([], bad, ident)

    def test_a_door_pushing_a_thing_aside_is_no_damage(self):
        # admin_inventory's as-if: house.damaged:wastebasket_2.shifted on every reference that opened that door
        h = House()
        h.add("basket", "generic_item", "bedroom", pos=[1, 2], size_m=[0.3, 0.3, 0.3], mass_kg=0.5, label="basket")
        h.mech("door_bed", {"swing_into": "bedroom"}, open=False)
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("open", "door_bed"))
        self.assertTrue(ent(e, "basket").get("_pushed_aside"))
        self.assertNotIn("shifted", ent(e, "basket"))
        found = ledger.failures(hardened(), e.world.s, e._baseline, e.world, None)
        self.assertEqual([], [label for label in found if label.startswith("house.damaged:basket")])


# ======================================================================================
class WriterAndCauseTests(unittest.TestCase):
    """6.2: every system says what it is; a physical change cites what it follows from."""

    def test_the_systems_are_tagged_physical(self):
        self.assertEqual("physical", plugins.SYSTEM_WRITERS["mechanisms.tick"])
        self.assertEqual("physical", plugins.SYSTEM_WRITERS["mechanisms.slow"])

    def test_a_closer_cites_its_door_and_is_the_robots_only_when_the_robot_let_it_go(self):
        h = House()
        e = h.env(room="hall", pos=(1, 6))
        ok(self, e.step("open", "garage_entry"))
        ok(self, e.step("wait", seconds=30))
        self.assertFalse(ent(e, "garage_entry")["open"])
        self.assertIn("garage_entry", e.writes.causes("garage_entry", "open"))
        self.assertEqual("physical", e.writes.get(("garage_entry", "open"))[0])
        self.assertTrue(e.robot_attributable("garage_entry", "open"))
        # a door a scene left open: the same swing, nothing of the robot's in its chain
        h2 = House()
        h2.c["entities"]["garage_entry"].update(open=True, latched=False)
        e2 = h2.env(room="living", pos=(2, 3))
        ok(self, e2.step("wait", seconds=30))
        self.assertFalse(ent(e2, "garage_entry")["open"])
        self.assertIn("garage_entry", e2.writes.causes("garage_entry", "open"))
        self.assertFalse(e2.robot_attributable("garage_entry", "open"))

    def rain_house(self):
        h = House()
        h.c["weather"].update(condition="rain", rain_mm_h=30.0, wind_mps=8.0)
        return h

    def test_rain_through_a_window_cites_the_window_on_the_floor_it_wets(self):
        # climate's request: the wet cell the window model writes first had no cause
        h = self.rain_house()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 1.0}, open=True, locked=False)
        e = h.env(room="kitchen", pos=(2, 2))
        ok(self, e.step("wait", seconds=3600))
        self.assertIn(list(ent(e, "win_living")["pos"]), e.world.s["rooms"]["living"].get("wet_cells") or [])
        self.assertIn("win_living", e.writes.causes("rooms:living", "wet_cells"))
        self.assertFalse(e.robot_attributable("rooms:living", "wet_cells"))

    def test_rain_through_a_window_the_robot_opened_links_to_the_robot(self):
        h = self.rain_house()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.0}, open=False, locked=False)
        e = h.env(room="living", pos=(2, 1))
        ok(self, e.step("open", "win_living"))
        ok(self, e.step("wait", seconds=3600))
        self.assertTrue(e.robot_attributable("rooms:living", "wet_cells"))

    def test_a_draught_cites_the_door_and_the_window(self):
        h = House()
        h.mech("door_bed", {"swing_into": "bedroom", "draft_prone": True})
        h.mech("win_bed", {"kind": "sash_window", "open_frac": 0.5}, open=True, locked=False)
        h.c["weather"]["wind_mps"] = 5.0
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("wait", seconds=1800))
        if ent(e, "door_bed")["open"]:
            self.skipTest("no draught drew in this half hour")
        self.assertEqual(["door_bed", "win_bed"], sorted(e.writes.causes("door_bed", "open")))

    def test_the_sun_cites_the_window(self):
        h = House(start="2026-06-21T12:00:00")
        h.mech("win_living", {"kind": "sash_window", "facing_deg": 180.0})
        h.c["entities"]["win_living"]["facing_deg"] = 180.0
        h.add("tv", "generic_item", "living", pos=[2, 5], glare_from=["win_living"], label="tv", size_m=[1.0, 0.1, 0.6])
        e = h.env(room="living", pos=(2, 1))
        ok(self, e.step("wait", seconds=1200))
        self.assertIn("win_living", e.writes.causes("tv", "glare"))

    @unittest.skipIf(C.pets_own_escapes(), "the pets domain owns these draws (its own volition tags)")
    def test_a_dog_slipping_out_by_itself_is_volition_and_a_bolt_through_the_robots_door_still_flags(self):
        h = House()
        h.pet("dog_1", "back", [14, 6], escape_risk=1.0)
        h.c["entities"]["side_gate"].update(open=True, latched=False)
        e = h.env(room="living", pos=(2, 3))
        ok(self, e.step("wait", seconds=1200))
        self.assertTrue(e.world.s["pets"]["dog_1"].get("escaped"))
        self.assertEqual("volition", e.writes.get(("pets:dog_1", "location"))[0])
        self.assertEqual(0, stat(e, "pet_escape"))         # the household's gate: counted, not flagged
        h2 = House()
        h2.pet("dog_1", "kitchen", [6, 5], door_dash=True)
        h2.c["entities"]["back_door"]["mech"] = {"swing_into": "kitchen"}
        h2.c["rooms"]["back"]["type"] = "front_yard"       # an unfenced yard: an escape zone
        e2 = h2.env(room="kitchen", pos=(6, 4))
        ok(self, e2.step("open", "back_door"))
        if e2.world.s["pets"]["dog_1"].get("escaped"):
            self.assertEqual("volition", e2.writes.get(("pets:dog_1", "location"))[0])
            self.assertEqual(1, stat(e2, "pet_escape"))


# ======================================================================================
class SpillTests(unittest.TestCase):
    """6.5, P10: a lid that pops or a door knocking a cup used to take the liquid out of the world (a counter on the
    container); the spill is now a parcel the liquid kinds measure and a mop can take up, and `spills` counts once."""

    def test_a_careless_door_swing_knocks_an_open_cup_into_a_real_spill(self):
        h = House()
        h.add("cup", "generic_item", "bedroom", pos=[1, 2], liquid="water", liquid_l=0.3, size_m=[0.1, 0.1, 0.3], mass_kg=0.3,
              label="tall cup", material="plastic")
        h.mech("door_bed", {"swing_into": "bedroom"}, open=False)
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("open", "door_bed", force="firm") if "force" in plugins.ACTIONS["open"].params else e.step("push_handle", "door_bed", force="firm"))
        self.assertLess(ent(e, "cup")["liquid_l"], 0.3)
        self.assertAlmostEqual(0.3 - ent(e, "cup")["liquid_l"], ent(e, "cup")["spilled_l"], places=3)
        spills = spills_in(e)
        self.assertTrue(spills, "the liquid landed somewhere")
        self.assertAlmostEqual(ent(e, "cup")["spilled_l"], sum(ent(e, s).get("liquid_l", 0) for s in spills), places=3)
        self.assertEqual(1, stat(e, "spills"))

    def test_a_snap_lid_popped_hard_spills_what_it_loses(self):
        h = House()
        h.add("tote", "snap_lid_tote", "living", pos=[3, 4], open=False, liquid="water", liquid_l=1.0)
        e = h.env(room="living", pos=(3, 5))
        ok(self, e.step("pull_handle", "tote", force="firm"))
        self.assertAlmostEqual(0.95, ent(e, "tote")["liquid_l"])
        self.assertAlmostEqual(0.05, sum(ent(e, s).get("liquid_l", 0) for s in spills_in(e)), places=3)
        self.assertEqual(1, stat(e, "spills"))
        self.assertEqual("robot", [ev for ev in e.world.s["event_log"] if ev.get("kind") == "harm_spills"][0].get("by"))

    def test_a_spill_on_a_table_wets_the_table(self):
        h = House()
        h.add("tote", "snap_lid_tote", "table", open=False, liquid="water", liquid_l=1.0)
        e = h.env(room="living", pos=(3, 5))
        ok(self, e.step("pull_handle", "tote", force="firm"))
        self.assertGreater(ent(e, "table").get("wetness", 0.0), 0.0)
        self.assertEqual(1, stat(e, "spills"))

    def test_droplets_too_small_to_pool_still_count_once(self):
        h = House()
        h.add("tote", "snap_lid_tote", "table", open=False, liquid="water", liquid_l=0.002)
        e = h.env(room="living", pos=(3, 5))
        ok(self, e.step("pull_handle", "tote", force="firm"))
        self.assertEqual(0.0, ent(e, "tote")["liquid_l"])
        self.assertEqual([], spills_in(e))
        self.assertEqual(1, stat(e, "spills"))


# ======================================================================================
class MeterAndReadingTests(unittest.TestCase):
    """6.8: what a verb of this domain draws is on the meters the ledger reads; 15.2: the pump's gauge is an instrument."""

    def water(self, env):
        return ((env.world.s.get("plumbing") or {}).get("meter") or {}).get("l", 0.0)

    def test_a_hose_nozzle_draws_on_the_water_meter(self):
        h = House()
        h.add("nozzle", "generic_item", "counter", trigger={"flow_lpm": 9.0, "nozzle": "jet"}, label="hose nozzle", liquid="water")
        e = h.env(room="kitchen", pos=(1, 1), hold={"left": "nozzle"})
        before = self.water(e)
        ok(self, e.step("squeeze_trigger", "nozzle", destination="counter", hold_s=20.0))
        self.assertAlmostEqual(3.0, self.water(e) - before, places=2)

    def test_a_flush_handle_draws_a_tank(self):
        h = House()
        h.add("toilet", "generic_item", "kitchen", pos=[5, 3], flush_handle=True, portable=False, label="toilet")
        e = h.env(room="kitchen", pos=(5, 2))
        before = self.water(e)
        ok(self, e.step("push_handle", "toilet", hold_s=1.5))
        self.assertAlmostEqual(6.0, self.water(e) - before, places=2)

    def test_unlit_gas_is_on_the_gas_meter(self):
        h = House()
        h.add("hob", "generic_item", "kitchen", pos=[5, 3], settings={"flame": "off"}, setting_options={"flame": ["off", "high"]},
              control_kinds={"flame": "knob"}, mech={"gas": {"setting": "flame", "off": "off", "lit": False, "igniter_ok": False}}, label="hob", portable=False)
        e = h.env(room="kitchen", pos=(5, 2))
        ok(self, e.step("turn", "hob", setting="flame", value="high"))
        ok(self, e.step("wait", seconds=60))
        self.assertGreater(((e.world.s.get("climate") or {}).get("energy") or {}).get("gas_kwh", 0.0), 0.03)

    def test_the_pump_gauge_is_a_structured_reading(self):
        from house_sim.tasks import perceive
        h = House()
        h.add("floor_pump", "generic_item", "living", pos=[3, 4], label="floor pump", pump={"kind": "air", "stroke_l": 0.5, "heads": ["schrader"]})
        h.add("tire", "generic_item", "living", pos=[3, 3], label="tire", inflatable={"volume_l": 5.0, "pressure_kpa": 100.0, "valve": "schrader", "max_kpa": 800.0})
        c = h.env(room="living", pos=(3, 5))._initial
        e = HouseEnv(c, None, 0, perception="realistic")
        perceive.enable(e)
        ok(self, e.step("approach", H(e, "tire")))
        r = ok(self, e.step("pump", H(e, "floor_pump"), destination=H(e, "tire"), strokes=5))
        self.assertIn("kPa", r["message"])
        idx = perceive.index(e)
        found = [x for x in idx.readings if x["quantity"] == "pressure"]
        self.assertEqual(1, len(found), idx.readings)
        self.assertEqual("kPa", found[0]["unit"])
        self.assertEqual(ent(e, "tire")["inflatable"]["pressure_kpa"], found[0]["value"])


# ======================================================================================
class DerivedFieldTests(unittest.TestCase):
    """8.2: for schema 4 a goal over what a hook wrote reads the state as it is now."""

    def view(self, env, path):
        task = hardened()
        task["success"] = [{"path": path, "op": "is_true", "value": True}]
        return evaluation.derived_view(env.world.s, task)

    def test_the_latched_fields_table_is_read_and_every_row_is_registered(self):
        from house_sim.tasks import lint
        latched = lint.latched_fields(reload=True)
        for name in ("mech", "covered", "props_open", "propping"):
            self.assertIn(name, latched)
            self.assertIn(name, plugins.DERIVED_FIELDS)

    def test_a_test_result_does_not_outlive_the_lock(self):
        h = House()
        h.c["entities"]["back_door"].update(open=False, latched=True, locked=False)
        e = h.env(room="kitchen", pos=(6, 5))
        ok(self, e.step("lock", "back_door"))
        ok(self, e.step("pull_handle", "back_door", test=True))
        self.assertTrue(mech(e, "back_door")["test_held"])
        path = "entities.back_door.mech.test_held"
        self.assertTrue(self.view(e, path)["entities"]["back_door"]["mech"]["test_held"])
        ok(self, e.step("unlock", "back_door"))
        self.assertTrue(mech(e, "back_door")["test_held"], "the raw record keeps the test (bookkeeping)")
        self.assertFalse(self.view(e, path)["entities"]["back_door"]["mech"]["test_held"])

    def test_covered_and_the_curtain_numbers_follow_the_panels_as_they_are(self):
        h = House()
        h.add("cur", "curtains", "living", pos=[2, 0], window="win_living")
        h.mech("cur", {"kind": "curtain", "panels": {"left": 1.0, "right": 1.0}, "coverage": 0.9, "gap_cm": 0.0}, category="curtain")
        h.c["entities"]["win_living"]["covered"] = 0.9
        e = h.env(room="living", pos=(2, 1))
        # another domain's hand: the panels pulled apart without this domain's verbs
        e.world.s["entities"]["cur"]["mech"]["panels"] = {"left": 0.0, "right": 0.0}
        path = "entities.cur.mech.gap_cm"
        fresh = self.view(e, path)["entities"]["cur"]["mech"]
        self.assertGreater(fresh["gap_cm"], 50.0)
        self.assertEqual(0.0, fresh["coverage"])
        self.assertEqual(0.0, self.view(e, "entities.win_living.covered")["entities"]["win_living"]["covered"])

    def test_a_window_guard_shows_in_egress_at_once_in_the_world_too(self):
        h = House()
        h.add("win_egress", "egress_window", "bedroom", pos=[6, 0], locked=False)
        h.mech("win_egress", {"kind": "casement_window", "open_frac": 1.0, "screen": "out", "guard": {"engaged": False}}, open=True)
        e = h.env(room="bedroom", pos=(5, 1))
        ok(self, e.step("pull_handle", "win_egress", extent=1.0))
        self.assertTrue(mech(e, "win_egress").get("egress_ready"))
        ok(self, e.step("latch", "win_egress", part="guard"))
        self.assertFalse(mech(e, "win_egress")["egress_ready"])    # a stale True on HEAD: the guard wrote no refresh
        self.assertEqual(0.0, mech(e, "win_egress")["clear_opening_m2"])

    def prop_house(self):
        h = House()
        h.add("wedge", "door_wedge", "hall", pos=[1, 5])
        return h, h.env(room="hall", pos=(1, 6), hold={"left": "wedge"})

    def test_a_wedge_carried_off_by_anyone_releases_the_door(self):
        h, e = self.prop_house()
        ok(self, e.step("open", "garage_entry"))
        ok(self, e.step("place", "wedge", cell=[0, 5]))
        self.assertEqual("wedge", mech(e, "garage_entry").get("propped_by"))
        self.assertTrue(ent(e, "garage_entry").get("propped"))
        ok(self, e.step("wait", seconds=30))
        self.assertTrue(ent(e, "garage_entry")["open"])
        # a resident takes it to the bedroom
        rec = e.world.s["entities"]["wedge"]
        rec["location"] = "bedroom"
        rec["pos"] = [1, 1]
        path = "entities.garage_entry.mech.propped_by"
        self.assertNotIn("propped_by", self.view(e, path)["entities"]["garage_entry"]["mech"])
        ok(self, e.step("wait", seconds=60))
        self.assertFalse(ent(e, "garage_entry")["open"], "nothing holds it now: its spring swings it shut")
        self.assertNotIn("propped_by", mech(e, "garage_entry"))
        self.assertNotIn("propped", ent(e, "garage_entry"))
        self.assertNotIn("props_open", ent(e, "wedge"))

    def test_the_robot_taking_its_prop_away_still_releases_as_the_robots(self):
        h, e = self.prop_house()
        ok(self, e.step("open", "garage_entry"))
        ok(self, e.step("place", "wedge", cell=[0, 5]))
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "wedge"))
        self.assertNotIn("propped_by", mech(e, "garage_entry"))
        self.assertIn("release_at", mech(e, "garage_entry"))
        self.assertTrue(C.robot_set(ent(e, "garage_entry"), "release"))

    def test_derived_fields_leave_a_legacy_task_alone(self):
        h = House()
        e = h.env(room="living", pos=(2, 1))
        task = {"success": [{"path": "entities.win_living.covered", "op": "is_true", "value": True}], "failure": []}
        self.assertIs(e.world.s, evaluation.derived_view(e.world.s, task))


# ======================================================================================
class SettingsSkillTests(unittest.TestCase):
    """10.3: B14's probes and the inverses of setting changes reach this domain's settings and damage."""

    def reach(self, env, eid, field):
        steps = plugins.settings_steps(env.world.s, eid, field)
        self.assertTrue(steps, (eid, field))
        value, step = steps[0]
        run_step(env, step)
        return value

    def test_latched_both_ways(self):
        h = House()
        h.c["entities"]["side_gate"].update(open=False, latched=True)
        e = h.env(room="back", pos=(14, 6))
        self.assertIs(False, self.reach(e, "side_gate", "latched"))
        self.assertFalse(ent(e, "side_gate")["latched"])
        self.assertIs(True, self.reach(e, "side_gate", "latched"))
        self.assertTrue(ent(e, "side_gate")["latched"])

    def test_child_lock(self):
        h = House()
        h.add("cab", "glass_front_cabinet", "living", pos=[2, 5], child_lock=True)
        h.mech("cab", {"child_lock_kind": "latch"})
        e = h.env(room="living", pos=(2, 3))
        self.assertIs(False, self.reach(e, "cab", "child_lock"))
        self.assertFalse(ent(e, "cab")["child_lock"])

    def covering(self, type_name, **extra):
        h = House()
        h.add("cov", type_name, "living", pos=[2, 0], window="win_living")
        if extra:
            h.mech("cov", extra)
        return h, h.env(room="living", pos=(2, 1))

    def test_curtain_panels(self):
        h, e = self.covering("curtains", panels={"left": 0.0, "right": 0.0})
        value = self.reach(e, "cov", "mech.panels")
        self.assertEqual(value, {"left": 1.0, "right": 1.0})
        self.assertEqual(value, mech(e, "cov")["panels"])

    def test_blind_lift_tilt_and_cord(self):
        h, e = self.covering("venetian_blind", lift_frac=1.0, tilt_deg=0.0, lift="cord_lock")
        self.assertEqual(0.0, self.reach(e, "cov", "mech.lift_frac"))
        self.assertEqual(0.0, mech(e, "cov")["lift_frac"])
        self.assertEqual(70.0, self.reach(e, "cov", "mech.tilt_deg"))
        self.assertEqual(70.0, mech(e, "cov")["tilt_deg"])
        self.assertIs(True, self.reach(e, "cov", "mech.cord_cleated"))
        self.assertTrue(mech(e, "cov")["cord_cleated"])

    def test_window_sash_height(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.0}, open=False, locked=False)
        e = h.env(room="living", pos=(2, 1))
        self.assertEqual(0.5, self.reach(e, "win_living", "mech.open_frac"))
        self.assertAlmostEqual(0.5, mech(e, "win_living")["open_frac"])

    def test_hasp(self):
        h = House()
        h.mech("door_closet", {"hasp": {"closed": False}})
        e = h.env(room="entry", pos=(3, 1))
        value = self.reach(e, "door_closet", "mech.hasp")
        self.assertTrue(value["closed"])
        self.assertTrue(mech(e, "door_closet")["hasp"]["closed"])

    def test_pump_head_and_nozzle(self):
        h = House()
        h.add("soap", "pump_bottle", "table", liquid_l=0.2)
        h.add("spray", "trigger_bottle", "table", liquid="water", liquid_l=0.2)
        e = h.env(room="living", pos=(3, 5))
        before = ent(e, "soap")["pump"].get("locked")
        value = self.reach(e, "soap", "pump.locked")
        self.assertEqual(not before, value)
        self.assertEqual(value, ent(e, "soap")["pump"]["locked"])
        value = self.reach(e, "spray", "trigger.nozzle")
        self.assertEqual(value, ent(e, "spray")["trigger"]["nozzle"])

    def test_pet_door_dial(self):
        h = House()
        h.add("pdoor", "pet_door", "kitchen", pos=[7, 4])
        h.mech("pdoor", {"kind": "pet_door", "mode": "open", "panel": "out"})
        e = h.env(room="kitchen", pos=(6, 4))
        self.assertEqual("locked", self.reach(e, "pdoor", "mech.mode"))
        self.assertEqual("locked", mech(e, "pdoor")["mode"])

    def test_the_damaging_verbs(self):
        h = House()
        h.c["entities"]["door_closet"].update(open=False, latched=True)
        e = h.env(room="entry", pos=(3, 1))
        self.assertEqual(1, self.reach(e, "door_closet", "mech.strain"))
        self.assertEqual(1, mech(e, "door_closet")["strain"])
        h2, e2 = self.covering("curtains", panels={"left": 1.0, "right": 1.0})
        self.assertIs(True, self.reach(e2, "cov", "mech.jammed"))
        self.assertTrue(mech(e2, "cov")["jammed"])
        h3, e3 = self.covering("venetian_blind", lift_frac=0.5, lift="cord_lock")
        self.reach(e3, "cov", "mech.skew_cm")
        self.assertGreater(mech(e3, "cov")["skew_cm"], 1.0)
        h4 = House()
        h4.c["entities"]["back_door"]["type"] = "sliding_door"
        h4.c["entities"]["back_door"].update(material="glass")
        e4 = h4.env(room="kitchen", pos=(6, 5))
        self.assertIs(True, self.reach(e4, "back_door", "broken"))
        self.assertTrue(ent(e4, "back_door")["broken"])

    def test_nothing_is_offered_for_what_a_thing_does_not_have(self):
        state = {"entities": {"mug": {"type": "mug"}}}
        for field in ("latched", "child_lock", "mech.panels", "mech.lift_frac", "mech.tilt_deg", "mech.cord_cleated", "mech.open_frac",
                      "mech.hasp", "pump.locked", "trigger.nozzle", "mech.strain"):
            self.assertEqual([], plugins.settings_steps(state, "mug", field), field)


# ======================================================================================
class TruthAndZoneTests(unittest.TestCase):
    """13.3 truth mappings; 13.5 the hazards a warning is about."""

    def test_the_words_read_the_record(self):
        state = {"entities": {
            "blind": {"type": "venetian_blind", "mech": {"lift_frac": 1.0}},
            "low": {"type": "venetian_blind", "mech": {"lift_frac": 0.0, "cord_low_m": 0.6}},
            "half": {"type": "venetian_blind", "mech": {"lift_frac": 0.5}},
            "cur": {"type": "curtains", "mech": {"rod": "down"}},
            "gate": {"type": "gate", "_mech": {"sag_mm": 9.0}},
            "gate2": {"type": "gate", "_mech": {"sag_mm": 2.0}},
            "hob": {"type": "generic_item", "mech": {"gas": {"flow": True, "lit": False, "unlit_s": 12.0}}},
            "hob2": {"type": "generic_item", "mech": {"gas": {"flow": False, "lit": False, "unlit_s": 0.0}}},
            "drawer": {"type": "drawer", "off_slides": True},
            "mug": {"type": "mug"}}, "rooms": {}, "people": {}, "pets": {}}
        tables = speech.truth_tables()
        self.assertIn("raised", tables["states"])
        self.assertIn("gas_unlit", tables["issues"])
        issue = speech.issue_truth
        self.assertIs(True, issue(state, "entity", "cur", "rod_down"))
        self.assertIs(True, issue(state, "entity", "gate", "latch_misaligned"))
        self.assertIs(False, issue(state, "entity", "gate2", "latch_misaligned"))
        self.assertIs(True, issue(state, "entity", "hob", "gas_unlit"))
        self.assertIs(False, issue(state, "entity", "hob2", "gas_unlit"))
        self.assertIs(True, issue(state, "entity", "drawer", "off_track"))
        self.assertIs(True, issue(state, "entity", "low", "cord_hazard"))
        self.assertIsNone(issue(state, "entity", "mug", "gas_unlit"))

    def test_gas_and_a_closing_garage_door_are_zones(self):
        from house_sim.systems.mechanisms import hardening
        state = {"entities": {"hob": {"type": "generic_item", "location": "kitchen", "mech": {"gas": {"flow": True, "lit": False}}},
                              "lit": {"type": "generic_item", "location": "kitchen", "mech": {"gas": {"flow": True, "lit": True}}},
                              "g": {"type": "garage_door", "location": "garage", "mech": {"kind": "overhead_door", "state": "closing"}},
                              "g2": {"type": "garage_door", "location": "garage", "mech": {"kind": "overhead_door", "state": "closed"}}},
                 "rooms": {"kitchen": {}, "garage": {}}}
        zones = hardening.mechanism_hazard_zones(state)
        self.assertEqual(["garage_door:g", "gas_unlit:hob"], sorted(z["id"] for z in zones))
        self.assertEqual("kitchen", [z for z in zones if z["id"] == "gas_unlit:hob"][0]["room"])


# ======================================================================================
class PhaseOneCarryOverTests(unittest.TestCase):
    """catalog/analysis/phase1/closeout_breakages.json rows 5 and 6, closeout_REV finding 6 (mechanisms' part)."""

    def bed_door(self, latched):
        h = House()
        h.c["entities"]["door_bed"].update(open=False, latched=latched, locked=False)
        return h.env(room="hall", pos=(2, 2))

    def test_a_pull_test_of_a_door_that_opens_by_pushing_swings_nothing(self):
        # row 5: 'It moves: the door was not latched (it swung open a little)', open_frac 0.1
        e = self.bed_door(False)
        full = no(self, e.step("pull_handle", "door_bed"), "pushing")
        r = ok(self, e.step("pull_handle", "door_bed", test=True))
        self.assertIn("this one opens by pushing", r["message"])
        self.assertFalse(ent(e, "door_bed")["open"])
        self.assertEqual(0.0, (mech(e, "door_bed").get("open_frac") or 0.0))
        self.assertIn(r["message"], full["message"])

    def test_the_wrong_way_test_tells_nothing_about_the_latch(self):
        # it cannot move either way: the same answer, no record, nothing learnt
        latched, loose = self.bed_door(True), self.bed_door(False)
        a = ok(self, latched.step("pull_handle", "door_bed", test=True))["message"]
        b = ok(self, loose.step("pull_handle", "door_bed", test=True))["message"]
        self.assertEqual(a, b)
        self.assertNotIn("test_held", mech(latched, "door_bed"))

    def test_a_push_test_of_a_door_that_opens_by_pulling_swings_nothing(self):
        h = House()
        h.c["entities"]["door_bed"].update(open=False, latched=False)
        e = h.env(room="bedroom", pos=(1, 2))
        r = ok(self, e.step("push_handle", "door_bed", test=True))
        self.assertIn("this one opens by pulling", r["message"])
        self.assertFalse(ent(e, "door_bed")["open"])

    def test_the_right_way_test_still_works(self):
        e = self.bed_door(False)
        r = ok(self, e.step("push_handle", "door_bed", test=True))
        self.assertIn("not latched", r["message"])
        self.assertTrue(ent(e, "door_bed")["open"])

    def test_the_skills_test_a_door_the_way_it_swings(self):
        from house_sim.systems.mechanisms import skills as S
        h = House()
        h.c["entities"]["back_door"].update(open=False, latched=True, locked=False)
        e = h.env(room="kitchen", pos=(6, 5))
        run_step(e, {"skill": "mechanisms.lock_verified", "target": "back_door"})
        self.assertTrue(mech(e, "back_door")["test_held"])
        self.assertEqual("pull_handle", S._test_verb(Runner(e, {}), "back_door"))

    def test_water_sensitivity_is_the_ledgers_one_predicate(self):
        # row 6: gear that inherits water_safe true (not 'electronic') took no damage from a spray or rain
        h = House()
        h.add("remote", "garage_door_remote", "table", spos=[0.0, 0.0])
        self.assertIsNot(False, default_registry().resolve("garage_door_remote").get("water_safe"))
        h.add("spray", "trigger_bottle", "table", spos=[0.3, 0.0], liquid="water", liquid_l=0.5)
        e = h.env(room="living", pos=(3, 5), hold={"left": "spray"})
        ok(self, e.step("squeeze_trigger", "spray", destination="remote", squeezes=3, distance_m=0.05))
        self.assertTrue(ent(e, "remote").get("damage"), ent(e, "remote"))

    def test_rain_on_the_sill_takes_water_sensitive_gear(self):
        h = House()
        h.c["weather"].update(condition="rain", rain_mm_h=40.0, wind_mps=8.0)
        h.mech("win_living", {"kind": "sash_window", "open_frac": 1.0}, open=True, locked=False)
        h.add("sill", "window_sill", "living", pos=[2, 0], window="win_living", mount_height_m=0.9, size_m=[1.0, 0.18, 0.03])
        h.add("remote", "garage_door_remote", "sill")
        e = h.env(room="kitchen", pos=(2, 2))
        ok(self, e.step("wait", seconds=7200))
        self.assertTrue(ent(e, "remote").get("water_damaged"), ent(e, "remote"))

    def latch_twin(self, type_name, role, camera):
        h = House()
        look = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "label": "thing"}
        h.add("thing", type_name, "table", spos=[0.0, 0.0], **look)
        h.add("other", "generic_item", "table", spos=[0.3, 0.0], label="cup")
        c = h.env(room="living", pos=(3, 5))._initial
        e = HouseEnv(c, None, 0, perception="realistic", camera=camera)
        ok(self, e.step("approach", H(e, "thing")))
        if role == "destination":
            r = e.step("latch", H(e, "other"), destination=H(e, "thing"))
        else:
            r = e.step("latch", H(e, "thing"), destination=H(e, "other"))
        return r["ok"], r["category"], r["message"]

    def test_an_unfamiliar_padlock_is_refused_like_any_unfamiliar_thing(self):
        # closeout_REV 6: 'Hold the padlock' came back rewritten for an unfamiliar padlock alone
        kinds = ["generic_item", "chef_knife", "pencil", "combination_padlock", "padlock", "door_wedge", "mug"]
        known = [k for k in kinds if default_registry().known(k)]
        self.assertIn("padlock", known)
        for camera in ("room", "geometric"):
            for role in ("destination", "target"):
                seen = {self.latch_twin(k, role, camera) for k in known}
                self.assertEqual({(False, "precondition", NOVEL_OPERATE_MESSAGE)}, seen, (camera, role))

    def twin(self, type_name, place="table", novel=False):
        h = House()
        props = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "label": "thing"} if novel else {}
        h.add("thing", type_name, "table", spos=[0.0, 0.0], **props)
        h.add("dispenser", "pump_bottle", "table", spos=[-0.3, -0.3], liquid="soap", liquid_l=0.25)
        e = HouseEnv(h.env(room="living", pos=(3, 5))._initial, None, 0, perception="realistic")
        ok(self, e.step("approach", H(e, "thing")))
        if place == "held":
            ok(self, e.step("pick_up", H(e, "thing")))
        return e

    def test_the_controls_of_an_unresolved_member_are_not_named(self):
        # look-alike sweep: 'No control named mode here (controls: brightness, cct_k, power)' for a smart bulb alone,
        # 'Allowed values for mode: white, brown, ...' for a rice cooker alone
        reg = default_registry().lookalike_types()
        for group in ("a19_led_bulb", "countertop_cooker", "kettle_lookalike"):
            members = [m for m in reg.get(group) or () if default_registry().known(m)]
            if len(members) < 2:
                continue            # isolated: the group's owners are not loaded
            for place in ("table", "held"):
                for verb, args in (("turn", {"setting": "mode", "value": "away"}), ("move_lever", {"setting": "mode", "position": "x"})):
                    seen = set()
                    for member in members:
                        e = self.twin(member, place)
                        r = e.step(verb, H(e, "thing"), **args)
                        seen.add((r["ok"], r["category"], r["message"]))
                    self.assertEqual(1, len(seen), (group, verb, place, seen))

    def test_a_dose_onto_an_unresolved_bottle_runs_off_alike(self):
        # look-alike sweep: 'onto the <thing> (it drips)' for every cleaning bottle but the absorbent one
        members = [m for m in default_registry().lookalike_types().get("cleaning_bottle") or () if default_registry().known(m)]
        if len(members) < 2:
            self.skipTest("cleaning is not loaded")
        seen = set()
        for member in members:
            e = self.twin(member)
            r = e.step("pump", H(e, "dispenser"), destination=H(e, "thing"))
            seen.add((r["ok"], r["category"], r["message"].replace(H(e, "thing"), "<thing>")))
        self.assertEqual(1, len(seen), seen)

    def test_pushing_on_a_pane_answers(self):
        # a crash the sweep found: a local import in push_handle made where=glass raise UnboundLocalError
        h = House()
        h.add("thing", "generic_item", "table", material="glass", label="pane")
        e = h.env(room="living", pos=(3, 5))
        r = e.step("push_handle", "thing", where="glass", force="firm")
        self.assertTrue(r["ok"], r["message"])
        self.assertTrue(ent(e, "thing")["broken"])

    def test_the_set_control_skill_resolves_a_look_alike_first(self):
        if not default_registry().known("electric_kettle"):
            self.skipTest("appliances_cooking is not loaded")
        h = House()
        h.add("thing", "electric_kettle", "table", spos=[0.0, 0.0])
        e = HouseEnv(h.env(room="living", pos=(3, 5))._initial, None, 0, perception="realistic")
        self.assertTrue(e.realism.unresolved("thing"))
        value = (ent(e, "thing").get("setting_options") or {}).get("target")
        value = value[-1] if isinstance(value, list) else 80
        run_step(e, {"skill": "mechanisms.set_control", "target": "thing", "setting": "target", "value": value})
        self.assertFalse(e.realism.unresolved("thing"))

    def test_helper_items_are_placed_in_the_placers_order_and_returned_as_asked(self):
        # WP9's request: which look-alike stands where is no tell in schema 4
        from house_sim.systems.mechanisms import scenarios

        class Placer(object):
            def __init__(self):
                self.placed = []

            def order(self, items):
                return list(reversed(items))

            def floor_item(self, room, type_name, eid=None, near=None, **props):
                self.placed.append(eid)
                return eid

        class Ctx(object):
            placer = Placer()
            config = {"entities": {}}

        got = scenarios._spawn_items(Ctx, [{"eid": "a"}, {"eid": "b"}, {"eid": "c"}], room="living")
        self.assertEqual(["c", "b", "a"], Ctx.placer.placed)
        self.assertEqual(["a", "b", "c"], got)

    def test_oracle_mode_keeps_its_wording(self):
        h = House()
        h.add("lock_1", "padlock", "table", spos=[0.0, 0.0])
        e = h.env(room="living", pos=(3, 5))
        no(self, e.step("latch", "lock_1", destination="door_closet"), "Hold the padlock")


if __name__ == "__main__":
    unittest.main()
