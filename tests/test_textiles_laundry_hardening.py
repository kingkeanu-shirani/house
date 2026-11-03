"""textiles_laundry, phase 2 (VERIFY_HARDENING WP-D textiles_laundry): what the house ledger needs from this domain, the
world-behaviour fixes of 23.3 and the phase-1 carry-over.

* refuse: lint peeled off a screen, a peeled lint-roller sheet, the fuzz tipped out of a fabric shaver and a dryer
  sheet spent by a cycle are refuse (waste: true) for cleaning's waste_item measure;
* absorbed liquid: what a towel soaks up of a spill is reported as parcels of the spill's kind (the mess_parcels hook)
  and leaves through the washer's drain, the wring and evaporation as flows;
* derived fields (8.2): made, bed_state, paired_with, spread_on, drying_mode, on_hanger, holding_garment recomputed
  from the current state, so a later move by any path cannot leave them stale;
* field classes (L-WATCH part 2), settings skills, truth mappings, hazard zones, the house water meter, causes;
* look-alikes and unfamiliar things: no verb of the domain tells members of an unresolved group or unfamiliar twins
  apart (closeout_breakages: strip_bed's into; the twin sweep's roll_garment and depill findings);
* reference skills: feel_garment never lights a room over a sleeper, wait_dry feels until dry (WP11's held-out runs).

Scenes reuse tests/test_textiles_laundry.py's build() (a small hand-built house), so the file runs with every domain
loaded and with HOUSE_SIM_DOMAINS=core,textiles_laundry (the checks that need another domain's mess kinds skip).
"""
import copy
import json
import os
import unittest

from house_sim.kernel import ledger, speech
from house_sim.kernel.actions import NOVEL_OPERATE_MESSAGE
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import (BEFORE, DERIVED_FIELDS, HOOKS, MESS_KINDS, load_domains, mess_kind,
                                      run_hooks, settings_steps)
from house_sim.kernel.predicates import MISSING

load_domains()

from house_sim.systems.textiles_laundry import bedding as B  # noqa: E402
from house_sim.systems.textiles_laundry import hardening as H  # noqa: E402
from house_sim.systems.textiles_laundry import model as M  # noqa: E402
from house_sim.systems.textiles_laundry import physical as P  # noqa: E402
from house_sim.systems.textiles_laundry.furnish import dress_bed  # noqa: E402
from tests.test_textiles_laundry import build, ent, env_of, hold, no, ok, wait_done  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRASH = dict(label="trash can", container=True, accepts_items=True, capacity_l=30.0, waste_stream="trash",
             size_m=[0.3, 0.3, 0.4], inner_size_m=[0.28, 0.28, 0.38], portable=False)


def trash_can(p, room="laundry"):
    """A waste container the ledger knows (cleaning's wastebasket when it is loaded, else a stand-in bin)."""
    if p.reg.known("wastebasket"):
        return p.floor_item(room, "wastebasket")
    return p.floor_item(room, "generic_item", **TRASH)


def hardened(envelope=None, protections=None):
    return {"success": [], "failure": [], "hardening": {"envelope": envelope or {}, "protections": protections or {}}}


def mess_labels(env, task=None):
    found = ledger.failures(task or hardened(), env.world.s, env._baseline, world=env.world, idle=None)
    return sorted(f for f in found if f.split(":", 1)[0] in (
        "house.mess_relocated", "house.wrong_sink", "house.mess_in_transit", "house.mess_unaccounted"))


def realistic(config, seed=0):
    env = HouseEnv(config, seed=seed, perception="realistic")
    return env, env.realism.handles.handle


def step(env, action, target="", destination="", **kw):
    r = env.step(action, target, destination, **kw)
    return {"ok": r["ok"], "category": r["category"], "message": r["message"]}


class RefuseTests(unittest.TestCase):
    """6.4 / 6.5: what this domain leaves behind as refuse carries waste: true (cleaning's waste_item counts it)."""

    def test_a_lint_wad_is_refuse_and_binned_lint_leaves_no_mess_label(self):
        config, p, s = build()
        trash = trash_can(p)
        config["entities"][s["lint_screen"]]["soil"] = {"lint": 0.7}
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "open", s["dryer"])
        ok(env, "uninstall", s["dryer"], slot="lint_screen")
        ok(env, "lint_roll", s["lint_screen"])                    # peeled into the free hand
        wad = next(e for e, r in env.world.s["entities"].items() if r.get("type") == "lint_wad")
        self.assertIs(ent(env, wad).get("waste"), True)
        if "waste_item" in MESS_KINDS:
            self.assertIn("house.mess_in_transit:waste_item", mess_labels(env))   # still in the robot's hand
        ok(env, "approach", trash)
        ok(env, "place", wad, trash, relation="in")
        if "waste_item" in MESS_KINDS:
            self.assertEqual(mess_labels(env), [])

    def test_wiping_a_fitted_screen_leaves_half_in_the_slot_and_half_in_hand(self):
        config, p, s = build()
        config["entities"][s["lint_screen"]]["soil"] = {"lint": 0.6}
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "open", s["dryer"])
        ok(env, "lint_roll", s["lint_screen"])
        self.assertAlmostEqual(M.soil_level(ent(env, s["dryer"]), "lint"), 0.3, places=6)
        wads = [r for r in env.world.s["entities"].values() if r.get("type") == "lint_wad"]
        self.assertEqual(len(wads), 1)                          # the other half no longer vanishes
        self.assertEqual(wads[0]["location"], "robot")

    def test_a_peeled_lint_roller_sheet_is_refuse_in_hand_or_in_the_trash(self):
        config, p, s = build()
        trash = p.on(s["table"], "generic_item", **dict(TRASH, size_m=[0.2, 0.2, 0.25], inner_size_m=[0.18, 0.18, 0.23],
                                                         capacity_l=8.0))
        coat = p.on(s["table"], "sweater", soil={"pet_hair": 0.5})
        roller = p.on(s["table"], "lint_roller", sheet_load=0.9)
        env = env_of(config)
        hold(env, roller)
        ok(env, "lint_roll", coat, tool=roller, fresh_sheet=True)
        sheets = [e for e, r in env.world.s["entities"].items() if r.get("type") == "lint_roller_sheet"]
        self.assertEqual(len(sheets), 1)
        self.assertIs(ent(env, sheets[0]).get("waste"), True)
        self.assertEqual(ent(env, sheets[0])["location"], "robot")
        self.assertEqual(ent(env, roller)["sheets"], 59)
        ok(env, "approach", trash)
        ok(env, "place", sheets[0], trash, relation="in")
        ok(env, "approach", coat)
        ok(env, "lint_roll", coat, tool=roller, fresh_sheet=True, into=trash)
        sheets = [e for e, r in env.world.s["entities"].items() if r.get("type") == "lint_roller_sheet"]
        self.assertEqual(sorted(ent(env, e)["location"] for e in sheets), [trash, trash])

    def test_a_spent_dryer_sheet_is_refuse(self):
        config, p, s = build()
        p.inside(s["dryer"], "t_shirt", wetness=0.3)
        sheet = s["dryer_sheets"][0]
        config["entities"][sheet].update(location=s["dryer"], relation="in")
        env = env_of(config)
        ok(env, "approach", s["dryer"])
        ok(env, "switch_on", s["dryer"])
        ok(env, "set_mode", s["dryer"], mode="delicate")
        ok(env, "start", s["dryer"])
        wait_done(env, s["dryer"])
        self.assertTrue(ent(env, sheet).get("spent"))
        self.assertIs(ent(env, sheet).get("waste"), True)
        if "waste_item" in MESS_KINDS:
            self.assertIn("house.mess_relocated:waste_item", mess_labels(env))   # left in the drum

    def test_emptying_a_fabric_shaver_gives_a_lint_wad(self):
        config, p, s = build()
        sweater = p.on(s["table"], "sweater", pilling=0.6, knit_grade="sturdy")
        shaver = p.on(s["table"], "fabric_shaver", lint_chamber=0.6)
        env = env_of(config)
        hold(env, shaver)
        ok(env, "depill", sweater, tool=shaver, empty_chamber=True)
        self.assertEqual(ent(env, shaver)["lint_chamber"], 0.15)
        wads = [r for r in env.world.s["entities"].values() if r.get("type") == "lint_wad"]
        self.assertEqual(len(wads), 1)
        self.assertTrue(wads[0]["waste"])

    def test_depill_takes_only_depilling_tools(self):
        config, p, s = build()
        sweater = p.on(s["table"], "sweater", pilling=0.6)
        brush = p.on(s["table"], "clothes_brush")
        env = env_of(config)
        hold(env, brush)
        result = no(env, "depill", sweater, tool=brush)
        self.assertIn("does not take pills off", result["message"])


class AbsorbedLiquidTests(unittest.TestCase):
    """6.5 / 6.11: a textile reports the spill liquid it soaked up as parcels of the spill's kind."""

    def setUp(self):
        self.added = None
        if "liquid:spill" not in MESS_KINDS:
            def stand_in(state):
                return H.absorbed_parcels(state, "liquid:spill")
            mess_kind("liquid:spill", unit="l", measure=stand_in, eps=0.05, rho=0.05)
            self.added = "liquid:spill"

    def tearDown(self):
        if self.added:
            MESS_KINDS.pop(self.added, None)

    def test_a_towel_reports_what_it_soaked_up_and_drying_or_washing_takes_it_out(self):
        config, p, s = build()
        towel = p.on(s["table"], "bath_towel", wetness=0.4)
        env = env_of(config)
        H.record_absorbed(env.world, towel, "liquid:spill", "jug_1", 0.4)
        parcels = H.absorbed_parcels(env.world.s, "liquid:spill")
        self.assertEqual(len(parcels), 1)
        (loc, origin, litres), = parcels
        self.assertEqual((loc, origin), (("entity", towel), "jug_1"))
        self.assertAlmostEqual(litres, 0.4, places=6)
        self.assertIn((("entity", towel), "jug_1", litres), run_hooks("mess_parcels", env.world.s, "liquid:spill")[0])
        self.assertEqual(H.absorbed_parcels(env.world.s, "liquid:chemical"), [])
        # half the water dries off: half the parcel is left
        held = P.water_held_l(env.world.s, towel)
        env.world.mut(towel)["wetness"] = round(float(ent(env, towel)["wetness"]) / 2.0, 6)
        self.assertAlmostEqual(H.absorbed_parcels(env.world.s)[0][2], 0.4 * P.water_held_l(env.world.s, towel) / held,
                               places=5)

    def test_washing_the_towel_sends_the_spill_down_the_washer_drain(self):
        config, p, s = build()
        towel = p.inside(s["washer"], "bath_towel")
        env = env_of(config)
        H.record_absorbed(env.world, towel, "liquid:spill", None, 0.3)
        ok(env, "approach", s["washer"])
        if ent(env, s["washer"]).get("open"):
            ok(env, "close", s["washer"])
        if not ent(env, s["washer"]).get("on"):
            ok(env, "switch_on", s["washer"])
        ok(env, "set_mode", s["washer"], mode="normal")
        ok(env, "start", s["washer"])
        wait_done(env, s["washer"], limit=3 * 3600)
        self.assertNotIn("_spill", ent(env, towel))
        flows = [f for f in (env.world.s.get("logs") or {}).get("mess_flows") or () if f.get("kind") == "liquid:spill"]
        self.assertTrue(any(f["src"] == "entity:" + towel and f["dst"] == "drain:" + s["washer"] for f in flows), flows)
        self.assertEqual(H.absorbed_parcels(env.world.s), [])

    def test_absorb_records_the_spill_and_an_unregistered_kind_records_nothing(self):
        from house_sim.kernel.plugins import ACTIONS
        if "absorb" not in ACTIONS:
            self.skipTest("cleaning's absorb verb is not loaded")
        config, p, s = build()
        spill = p.floor_item("laundry", "spill", liquid="water", liquid_l=0.3, category="spill", portable=False)
        towel = p.on(s["table"], "bath_towel")
        env = env_of(config)
        hold(env, towel)
        ok(env, "approach", spill)
        result = env.step("absorb", spill, "", tool=towel)
        if not result["ok"]:
            self.skipTest("absorb refused in this scene: " + result["message"])
        held = H.held_spill(env.world.s, towel)
        self.assertTrue(held, "the towel recorded nothing")
        self.assertEqual(sorted(held), ["liquid:spill|"])


class DerivedFieldTests(unittest.TestCase):
    """8.2: fields this domain's verbs latch are recomputed from the current state."""

    def test_the_fields_are_registered_to_this_domain(self):
        for name in ("made", "bed_state", "paired_with", "spread_on", "drying_mode", "on_hanger", "holding_garment"):
            self.assertIn(name, DERIVED_FIELDS)
            self.assertTrue(DERIVED_FIELDS[name].owner.startswith("house_sim.systems.textiles_laundry"), name)

    def test_a_made_bed_reads_unmade_once_its_layers_go_by_another_path(self):
        config, p, s = build()
        bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
        layers = dress_bed(p, bed, made=True)
        env = env_of(config)
        state = env.world.s
        self.assertIs(H.derived_made(state, bed), bool(state["entities"][bed].get("made")))
        state["entities"][bed]["made"] = True
        self.assertTrue(H.derived_made(state, bed))
        self.assertEqual(H.derived_bed_state(state, bed)["layers"], B.bed_summary(state, bed)["layers"])
        # every layer carried off the bed by a path that never calls refresh_bed (another domain's verb, a pet)
        for key, eid in layers.items():
            rec = state["entities"].get(eid) if isinstance(eid, str) else None
            if rec is not None and rec.get("spread_on") == bed:
                rec.update(location="bedroom", relation="on", pos=[0, 0])
        self.assertTrue(state["entities"][bed]["made"])              # the latched flag
        self.assertFalse(H.derived_made(state, bed))                  # the state
        self.assertEqual(H.derived_bed_state(state, bed)["layers"], [])

    def test_pair_hanger_spread_and_drying_follow_the_things(self):
        config, p, s = build()
        a = p.on(s["table"], "sock", pair_id="p1")
        b = p.on(s["table"], "sock", pair_id="p1")
        shirt = p.on(s["table"], "dress_shirt")
        hanger = p.on(s["table"], "plastic_hanger")
        env = env_of(config)
        hold(env, a)
        ok(env, "pair", a, b)
        state = env.world.s
        self.assertEqual(H.derived_paired_with(state, a), b)
        self.assertEqual(H.derived_paired_with(state, b), a)
        state["entities"][b].update(location="laundry", relation="on", pos=[1, 1])     # pulled apart by any path
        self.assertIsNone(H.derived_paired_with(state, a))
        self.assertEqual(state["entities"][a]["paired_with"], b)
        ok(env, "place", a, s["table"])
        hold(env, shirt)
        ok(env, "hang_on_hanger", shirt, hanger=hanger)
        self.assertEqual(H.derived_on_hanger(state, shirt), hanger)
        self.assertEqual(H.derived_holding_garment(state, hanger), shirt)
        state["entities"][hanger].update(location="laundry", relation="on", pos=[1, 1])
        self.assertIsNone(H.derived_on_hanger(state, shirt))
        self.assertIsNone(H.derived_holding_garment(state, hanger))
        self.assertIs(H.derived_spread_on(state, shirt), MISSING)        # never spread: the field does not apply

    def test_drying_mode_lasts_while_on_the_rack(self):
        config, p, s = build()
        towel = p.on(s["table"], "bath_towel", wetness=0.5)
        env = env_of(config)
        from tests.test_textiles_laundry import set_up_rack
        set_up_rack(env, s["rack"])
        hold(env, towel)
        ok(env, "approach", s["rack"])
        ok(env, "hang_dry", towel, s["rack"], method="hang")
        state = env.world.s
        self.assertEqual(H.derived_drying_mode(state, towel), "hung")
        state["entities"][towel].update(location="laundry", relation="on", pos=[0, 0])
        self.assertEqual(state["entities"][towel]["drying_mode"], "hung")
        self.assertIsNone(H.derived_drying_mode(state, towel))

    def test_schema4_evaluation_reads_the_derived_made(self):
        from house_sim.kernel import evaluation
        config, p, s = build()
        bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
        layers = dress_bed(p, bed, made=True)
        env = env_of(config)
        state = copy.deepcopy(env.world.s)
        state["entities"][bed]["made"] = True
        for eid in layers.values():
            rec = state["entities"].get(eid) if isinstance(eid, str) else None
            if rec is not None and rec.get("spread_on") == bed:
                rec.update(location="bedroom", relation="on", pos=[0, 0])
        task = hardened()
        task["success"] = [{"path": "entities.{}.made".format(bed), "op": "is_true"}]
        view = evaluation.derived_view(state, task)
        self.assertFalse(view["entities"][bed]["made"])
        self.assertTrue(state["entities"][bed]["made"])

    def test_the_latched_fields_table_lists_exactly_the_registered_fields(self):
        from house_sim.tasks import lint
        names = set()
        inside = False
        with open(os.path.join(ROOT, "docs", "domains", "textiles_laundry.md"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    inside = "latched fields" in line.lower()
                    continue
                if inside and line.strip().startswith("|"):
                    cell = line.strip().strip("|").split("|")[0]
                    if "`" in cell:
                        names.add(cell.split("`")[1])
        self.assertEqual(names, {"made", "bed_state", "paired_with", "spread_on", "drying_mode", "on_hanger",
                                 "holding_garment"})
        self.assertTrue(names <= set(lint.latched_fields(reload=True)))


class SettingsSkillTests(unittest.TestCase):
    """10.3: the settings a battery policy can change on this domain's things, and the plan step that does it."""

    def test_machine_programs_and_range_settings(self):
        config, p, s = build()
        env = env_of(config)
        state = env.world.s
        washer, dryer = s["washer"], s["dryer"]
        programs = settings_steps(state, washer, "appliance.program")
        current = state["entities"][washer]["appliance"]["program"]
        self.assertTrue(programs)
        self.assertNotIn(current, [v for v, _ in programs])
        self.assertEqual(programs[0][1]["skill"], "set_mode")
        delay = settings_steps(state, washer, "settings.delay_h")
        self.assertEqual(sorted(v for v, _ in delay), [12, 24])
        minutes = settings_steps(state, dryer, "settings.minutes")
        self.assertTrue(minutes and all(step["setting"] == "minutes" for _, step in minutes))

    def test_garment_and_bed_settings_and_the_operate_skill(self):
        from house_sim.tasks.runner import Runner
        config, p, s = build()
        shirt = p.on(s["table"], "dress_shirt")
        towel = p.on(s["table"], "bath_towel", folded="thirds_half")
        env = env_of(config)
        state = env.world.s
        (value, step_), = settings_steps(state, shirt, "inside_out")
        self.assertIs(value, True)
        self.assertEqual(step_["skill"], "textiles_laundry.operate")
        self.assertEqual(settings_steps(state, shirt, "sleeves_rolled")[0][0], True)
        (fvalue, fstep), = settings_steps(state, towel, "folded")
        self.assertIs(fvalue, False)
        runner = Runner(env, {}, strict=True)
        runner.run_top([dict(step_)])
        self.assertTrue(ent(env, shirt)["inside_out"])
        runner.run_top([dict(fstep)])
        self.assertFalse(ent(env, towel)["folded"])


class WatchTests(unittest.TestCase):
    """6.3, L-WATCH part 2: every property of the domain's type data is classified."""

    def test_every_type_property_is_classified(self):
        import importlib
        import sys
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        tool = importlib.import_module("tools.field_census")
        rows = (tool.unclassified(["textiles_laundry"]) or {}).get("textiles_laundry") or []
        self.assertEqual(rows, [])

    def test_key_classes(self):
        self.assertEqual(ledger.field_spec("t_shirt", "wrinkled"), {"class": "condition", "worse": "up", "tol": 0.15,
                                                                     "source": "domain_field"})
        self.assertEqual(ledger.field_spec("t_shirt", "wetness")["guard"], "wet")
        self.assertEqual(ledger.field_spec("front_load_washer", "appliance.program")["class"], "setting")
        self.assertEqual(ledger.field_spec("front_load_washer", "appliance.state")["class"], "process")
        self.assertEqual(ledger.field_spec("wire_hanger", "holding_garment")["class"], "bookkeeping")
        self.assertEqual(ledger.field_spec("dryer_sheet", "waste")["class"], "bookkeeping")
        self.assertEqual(ledger.field_spec("fitted_sheet", "corners_seated")["worse"], "down")
        with open(os.path.join(ROOT, "house_sim", "data", "verify", "watch", "textiles_laundry.json"), encoding="utf-8") as f:
            watch = json.load(f)
        self.assertIn("suede_boot", watch["water_sensitive"])
        self.assertNotIn("t_shirt", watch["water_sensitive"])


class TruthTests(unittest.TestCase):
    """13.3: claims about this domain's state have truth mappings."""

    def test_the_table_loads_and_reads_the_records(self):
        tables = speech.truth_tables()
        for word in ("made", "unmade", "inside_out", "right_side_out"):
            self.assertIn(word, tables["states"])
        for word in ("pilled", "felted", "snagged", "stretched", "dye_transfer", "melted", "linty"):
            self.assertIn(word, tables["issues"])
        state = {"entities": {"s": {"type": "sweater", "damage": {"felted": 0.5}, "pilling": 0.0, "inside_out": True},
                              "t": {"type": "t_shirt", "damage": {}, "pilling": 0.4, "inside_out": False},
                              "b": {"type": "bed", "made": True}}, "rooms": {}}
        self.assertIs(speech.issue_truth(state, "entity", "s", "felted"), True)
        self.assertIs(speech.issue_truth(state, "entity", "t", "felted"), False)
        self.assertIs(speech.issue_truth(state, "entity", "t", "pilled"), True)
        self.assertIs(speech.issue_truth(state, "entity", "s", "pilled"), False)
        self.assertIs(speech.state_truth(state, "entity", "s", "inside_out"), True)
        self.assertIs(speech.state_truth(state, "entity", "t", "right_side_out"), True)
        self.assertIs(speech.state_truth(state, "entity", "b", "made"), True)
        self.assertIs(speech.state_truth(state, "entity", "b", "unmade"), False)


class HazardZoneTests(unittest.TestCase):
    def test_a_hot_iron_is_a_zone_a_cold_one_is_not(self):
        state = {"entities": {"i": {"category": "iron", "temperature_c": 150.0},
                              "j": {"category": "iron", "temperature_c": 22.0}}, "rooms": {}}
        zones = H.hot_presses(state)
        self.assertEqual(zones, [{"id": "hot_iron:i", "room": None, "subjects": ["i"]}])
        self.assertIn("hot_iron:i", [z["id"] for z in speech.hazard_zones(state)])


class MeterAndCauseTests(unittest.TestCase):
    """6.8: a washer's fill counts on the house water meter; 6.2: the fabric system cites what it writes about."""

    def test_the_washer_fill_counts_on_the_water_meter(self):
        config, p, s = build()
        p.inside(s["washer"], "t_shirt")
        env = env_of(config)
        path = ledger.data("resources")["meters"]["water"]["path"]
        before = ledger.meter_value(env.world.s, path)
        ok(env, "approach", s["washer"])
        if ent(env, s["washer"]).get("open"):
            ok(env, "close", s["washer"])
        if not ent(env, s["washer"]).get("on"):
            ok(env, "switch_on", s["washer"])
        ok(env, "set_mode", s["washer"], mode="normal")
        ok(env, "start", s["washer"])
        program = ent(env, s["washer"])["appliance"]["programs"]["normal"]
        self.assertAlmostEqual(ledger.meter_value(env.world.s, path) - before, float(program["water_l"]), places=3)

    def test_a_heaped_textile_creasing_cites_itself(self):
        config, p, s = build()
        shirt = p.inside(s["basket"], "dress_shirt", wrinkled=0.1)          # heaped in a basket: it creases
        env = env_of(config)
        ok(env, "wait", seconds=4 * 3600)
        self.assertGreater(ent(env, shirt)["wrinkled"], 0.1)
        self.assertIn(shirt, env.writes.causes(shirt, "wrinkled"))


class LookalikeAndUnfamiliarTests(unittest.TestCase):
    """Phase-1 carry-over: no verb of the domain tells unresolved look-alikes or unfamiliar twins apart."""

    def test_every_verb_has_the_identify_first_hook(self):
        from house_sim.kernel.plugins import ACTIONS
        verbs = sorted(n for n, spec in ACTIONS.items() if spec.fn.__module__.startswith("house_sim.systems.textiles"))
        self.assertEqual(len(verbs), 27)
        for name in verbs:
            labels = [label for _, label, _ in BEFORE.get(name, [])]
            self.assertIn("house_sim.systems.textiles_laundry.hardening.unfamiliar_first", labels, name)

    def test_strip_bed_into_an_unfamiliar_thing_gets_one_answer(self):
        """closeout_breakages (appliances_cooking, textiles_laundry): strip_bed into= answered an unfamiliar hamper
        ("Open the hamper first"), a garment ("Strip into a basket, hamper or laundry bag") and a laundry bag (it
        stripped into it) by their true types."""
        answers = {}
        for kind in ("hamper", "laundry_bag", "t_shirt", "lint_roller"):
            config, p, s = build()
            bed = p.furniture("bedroom", "bed", size_m=[1.6, 2.0, 0.55])
            dress_bed(p, bed, made=True)
            thing = p.on(bed, kind, novel=True, eid="thing_1")
            env, Hd = realistic(config)
            for room in ("hall", "bedroom"):
                self.assertTrue(env.step("move", room)["ok"])
            self.assertTrue(env.step("approach", Hd(bed))["ok"])
            answers[kind] = step(env, "strip_bed", Hd(bed), into=Hd(thing))
        self.assertEqual({(a["ok"], a["message"]) for a in answers.values()}, {(False, NOVEL_OPERATE_MESSAGE)}, answers)

    def test_unfamiliar_twins_answer_alike_across_the_verbs(self):
        cmds = [("fold", {"style": "half"}), ("hang_on_hanger", {"hanger": None}), ("lace_shoes", {}),
                ("polish_shoes", {}), ("pair", {}), ("depill", {}), ("fluff", {}), ("lint_roll", {})]
        outs = {}
        for kind in ("t_shirt", "sneaker", "duvet_cover", "lint_roller", "plastic_hanger"):
            config, p, s = build()
            thing = p.on(s["table"], kind, novel=True, eid="thing_1")
            partner = p.on(s["table"], "t_shirt", eid="partner_1")
            env, Hd = realistic(config)
            env.step("approach", Hd(s["table"]))
            row = []
            for verb, args in cmds:
                args = {k: (Hd(partner) if v is None else v) for k, v in args.items()}
                row.append(json.dumps(step(env, verb, Hd(thing), **args), sort_keys=True))
            outs[kind] = row
        self.assertEqual(len({tuple(r) for r in outs.values()}), 1, outs)

    def test_a_held_unfamiliar_tool_is_passed_over_by_the_implicit_pickers(self):
        outs = {}
        for kind in ("lint_roller", "clothes_brush", "t_shirt"):
            config, p, s = build()
            coat = p.on(s["table"], "sweater", soil={"pet_hair": 0.4})
            tool = p.on(s["table"], kind, novel=True, eid="thing_1")
            env, Hd = realistic(config)
            env.step("approach", Hd(s["table"]))
            self.assertTrue(env.step("pick_up", Hd(tool))["ok"])
            outs[kind] = step(env, "lint_roll", Hd(coat))
        self.assertEqual(len({json.dumps(o, sort_keys=True) for o in outs.values()}), 1, outs)

    def test_unresolved_bed_sheets_are_not_rolled_apart(self):
        """The twin sweep: roll_garment rolled an unresolved crib sheet and refused a fitted or flat one ("Too large")."""
        outs = {}
        for kind in ("crib_sheet", "fitted_sheet", "flat_sheet"):
            config, p, s = build()
            sheet = p.on(s["table"], kind, eid="thing_1")
            env, Hd = realistic(config)
            env.step("approach", Hd(s["table"]))
            outs[kind] = step(env, "roll_garment", Hd(sheet))
        self.assertEqual(len({json.dumps(o, sort_keys=True) for o in outs.values()}), 1, outs)
        self.assertIn("inspect", outs["crib_sheet"]["message"])

    def test_look_alike_brushes_are_no_depilling_tools_alike(self):
        """The twin sweep: depill tool=<unresolved grill brush> named its kind ("with grill brush" / "with wire_brush")."""
        from house_sim.kernel.registry import default_registry
        reg = default_registry()
        group = [t for t in ("nylon_grill_brush", "wire_grill_brush") if reg.known(t)]
        if len(group) < 2:
            self.skipTest("the grill brushes are another domain's types")
        outs = {}
        for kind in group:
            config, p, s = build()
            sweater = p.on(s["table"], "sweater", pilling=0.5)
            brush = p.on(s["table"], kind, eid="thing_1")
            env, Hd = realistic(config)
            env.step("approach", Hd(s["table"]))
            self.assertTrue(env.step("pick_up", Hd(brush))["ok"])
            outs[kind] = step(env, "depill", Hd(sweater), tool=Hd(brush))
        self.assertEqual(len({json.dumps(o, sort_keys=True) for o in outs.values()}), 1, outs)
        self.assertFalse(next(iter(outs.values()))["ok"])


class SkillTests(unittest.TestCase):
    """WP11's held-out reference failures of A-d66994a566899c-01: a light switched on over a sleeper (seed 503) and a
    wool sweater still damp after a fixed 56 h in a cold laundry room (seed 500)."""

    def test_feel_garment_never_lights_a_room_over_a_sleeper(self):
        """Seed 503: in the dark bedroom where the resident slept the skill went for the light switch (woke her, or,
        with no doorway switch, failed the plan); now the check waits: no step is taken."""
        from house_sim.tasks.runner import Runner
        people = {"resident_1": {"label": "Alex", "role": "adult", "location": "bedroom", "pos": [3, 3],
                                 "posture": "sleeping", "awake": False}}
        config, p, s = build(start="2026-07-01T02:00:00", people=people)
        towel = p.floor_item("bedroom", "bath_towel", wetness=0.4)
        env = env_of(config)
        lit = env.perception.lit
        env.perception.lit = lambda state, room: room != "bedroom" and lit(state, room)    # the bedroom is dark
        runner = Runner(env, {}, strict=True)
        steps = env.world.s.get("steps")
        runner.run_top([{"skill": "textiles_laundry.feel_garment", "garment": towel}])
        self.assertEqual(env.world.s.get("steps"), steps)
        # awake: the robot lights the room from the doorway to feel the towel (here: no doorway switch, a plan error)
        env.world.s["people"]["resident_1"].update(posture="standing", awake=True)
        with self.assertRaises(Exception):
            runner.run_top([{"skill": "textiles_laundry.feel_garment", "garment": towel}])

    def test_wait_dry_stops_once_the_garment_feels_dry(self):
        from house_sim.tasks.runner import Runner
        config, p, s = build()
        tee = p.on(s["table"], "t_shirt", wetness=0.3)
        env = env_of(config)
        runner = Runner(env, {}, strict=False)
        runner.run_top([{"skill": "textiles_laundry.wait_dry", "garment": tee, "check_s": 3600, "max_checks": 12}])
        self.assertLess(float(ent(env, tee)["wetness"]), M.DRY)
        self.assertLess(env.world.s["time_s"], 12 * 3600)


class ScenarioHelperTests(unittest.TestCase):
    """WP9: the sock pile's look-alikes are placed in the placer's order (seeded in schema 4, as listed before)."""

    def _pile(self, schema, seed):
        import random
        from house_sim.generator.place import Placer
        from house_sim.kernel.registry import default_registry
        from house_sim.systems.textiles_laundry.helpers import sock_pile
        config, p, s = build()

        class Ctx(object):
            pass
        ctx = Ctx()
        ctx.config = config
        ctx.rng = random.Random(seed)
        ctx.placer = Placer(config, default_registry(), random.Random(seed), shuffle=schema >= 4)
        out = sock_pile(ctx, s["table"], pairs=3, orphans=1, scramble=False)
        order = sorted(out["socks"], key=lambda e: int(e.rsplit("_", 1)[1]))
        return out, order

    def test_legacy_order_is_the_design_order_and_schema4_shuffles_it(self):
        out, order = self._pile(2, 0)
        self.assertEqual(order, out["socks"])                  # created (ids) in the listed order
        self.assertEqual(out["orphans"], [out["socks"][-1]])
        shuffled = 0
        for seed in range(6):
            out4, order4 = self._pile(4, seed)
            self.assertEqual(len(out4["pairs"]), 3)
            self.assertEqual(sorted(out4["socks"]), sorted(order4))
            shuffled += order4 != out4["socks"]
        self.assertGreater(shuffled, 0)


if __name__ == "__main__":
    unittest.main()
