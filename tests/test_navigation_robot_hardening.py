"""navigation_robot's verification-hardening prerequisites (docs/design/VERIFY_HARDENING.md 23.3, WP-D navigation_robot).

Field classes for every type property (L-WATCH part 2), water sensitivity, writer classes and causes of the domain's
systems (6.2), mess flows with origins for what its verbs and systems move or create (6.5), truth mappings for claims
about its state (13.1-13.3), the derived `docked` field (8.2) and self-healing leveling tilt, settings skills (10.3),
structured instrument readings (15.2), and the carried-over rows: mark's identify-first refusal (closeout_REV 7),
dock_setup's blocker on held-out seed 502 (WP11), careful pace down unguided winders in navigation_robot.go (23.3).

Run:  python -m unittest tests.test_navigation_robot_hardening       (also HOUSE_SIM_STRICT=1, HOUSE_SIM_DOMAINS=core,navigation_robot)
"""
import copy
import json
import unittest
from pathlib import Path

from house_sim.kernel import evaluation, ledger, speech
from house_sim.kernel.plugins import DERIVED_FIELDS, SYSTEM_WRITERS, load_domains, settings_steps
from house_sim.kernel.predicates import MISSING
from tests.test_navigation_robot import Mini, ok, step

load_domains()

ROOT = Path(__file__).resolve().parents[1]
TYPES = json.loads((ROOT / "house_sim" / "data" / "types" / "navigation_robot.json").read_text(encoding="utf-8"))["types"]


def flows(env, kind=None):
    return [f for f in ((env.world.s.get("logs") or {}).get("mess_flows") or ())
            if f.get("op") == "flow" and (kind is None or f.get("kind") == kind)]


def hardened(task=None):
    """A minimal schema-4 task (a hardening block) for the derived-field overlay."""
    out = dict(task or {})
    out.setdefault("hardening", {"envelope": {}, "protections": {}})
    return out


class FieldClassTests(unittest.TestCase):
    """6.3 / L-WATCH part 2: every property of the domain's type data -- and every sub-key of the dict fields -- has an
    explicit class (the domain's watch file or a kernel name or pattern), never the default."""

    def test_every_type_property_and_dict_sub_key_is_classified_explicitly(self):
        missing = []
        for type_name, record in sorted(TYPES.items()):
            for field, value in sorted((record.get("props") or {}).items()):
                if field.startswith("_"):
                    continue
                if ledger.field_spec(type_name, field, explicit_only=True) is None:
                    missing.append((type_name, field))
                if isinstance(value, dict):
                    for sub in sorted(value):
                        if not sub.startswith("_") and ledger.field_spec(type_name, field + "." + sub,
                                                                         explicit_only=True) is None:
                            missing.append((type_name, field + "." + sub))
        self.assertEqual(missing, [])

    def test_the_classes_that_decide_the_guards(self):
        spec = ledger.field_spec
        self.assertEqual((spec("robot_vacuum", "rv.bin_fill")["class"], spec("robot_vacuum", "rv.bin_fill")["guard"]),
                         ("content", "mess"))                  # a debris load the mess balance judges
        self.assertEqual((spec("auto_empty_dock", "bag_fill")["class"], spec("auto_empty_dock", "bag_fill")["guard"]),
                         ("content", "mess"))
        self.assertEqual((spec("robot_vacuum_mop", "rv.bumper_ok")["class"], spec("robot_vacuum_mop", "rv.bumper_ok")["worse"]),
                         ("condition", "false"))               # the bumper broken by dragging it
        self.assertEqual(spec("robot_vacuum_mop", "rv.damaged")["worse"], "true")   # the tank ruined by bleach
        self.assertEqual(spec("robot_vacuum", "rv.state")["class"], "process")       # its own state machine
        self.assertEqual(spec("robot_vacuum", "rv.schedule")["class"], "setting")
        self.assertEqual(spec("robot_vacuum", "docked")["class"], "setting")       # left off its dock: changed
        self.assertEqual(spec("robot_vacuum", "rv.aborted_runs")["class"], "bookkeeping")
        # a spare pack's record follows the physical pack swapped onto it: its health is no damage there
        self.assertEqual(spec("robot_battery_pack", "health")["class"], "process")
        self.assertEqual(spec("robot_battery_pack", "swollen")["class"], "condition")
        self.assertEqual(spec("step_stool", "standing_height_m")["class"], "identity")   # the kernel's name rule says placement
        self.assertEqual(spec("step_ladder", "settings.spreader_lock")["class"], "setting")
        self.assertEqual(spec("step_stool", "open")["class"], "setting")               # left unfolded: changed
        for source in ("domain_field", "domain_type"):
            self.assertTrue(any(spec(t, f).get("source") == source for t, f in (
                ("robot_vacuum", "rv.bin_fill"), ("robot_battery_pack", "health"))), source)

    def test_water_sensitive_types(self):
        sensitive = ledger.water_sensitive
        reg = Mini().reg
        for type_name in ("robot_vacuum", "robot_vacuum_mop", "auto_empty_dock", "robot_headlamp", "laser_distance_meter",
                          "digital_level", "robot_battery_pack", "battery_charging_station"):
            self.assertTrue(sensitive(reg.make(type_name, "living")), type_name)
        for type_name in ("step_stool", "tape_measure", "reacher_grabber", "handrail", "spirit_level"):
            self.assertFalse(sensitive(reg.make(type_name, "living")), type_name)


class WriterClassTests(unittest.TestCase):
    """6.2: residents' and pets' own decisions are volition, machines physical with their causes."""

    def test_the_domain_systems_are_registered(self):
        for name in ("navigation_robot.robot_vacuums", "navigation_robot.doors", "navigation_robot.leveling",
                     "navigation_robot.elevator", "navigation_robot.robot", "navigation_robot.spare_packs",
                     "navigation_robot.watch"):
            self.assertEqual(SYSTEM_WRITERS.get(name), "physical", name)

    def test_a_reversing_car_and_a_walking_resident_are_volition(self):
        m = Mini()
        m.add("car", "generic_item", "front", pos=[3, 2], label="car", size_m=[4.5, 1.8, 1.5], mass_kg=1500.0, blocks_path=True,
              vehicle={"state": "reversing", "_until_s": 20.0, "then": "parked", "reverse_dir": "south", "zone_cells": []})
        m.person("dad", "living", [4, 4], moving=True, _moving_until_s=10.0)
        env = m.env(pos=(1, 1))
        ok(self, step(env, "wait", seconds=60))
        self.assertEqual(env.world.s["entities"]["car"]["vehicle"]["state"], "parked")
        self.assertEqual(env.writes.last("car", "vehicle")[:2], ("volition", "navigation_robot.vehicles"))
        self.assertFalse(env.world.s["people"]["dad"]["moving"])
        self.assertEqual(env.writes.last("people:dad", "moving")[:2], ("volition", "navigation_robot.people"))

    def test_a_pet_slipping_through_an_open_door_is_volition(self):
        m = Mini()
        m.c["entities"]["door_bath"]["open"] = True
        m.pet("rex", "bath", [1, 1], nav_wanders=True, nav_wander_delay_s=30.0)
        env = m.env(pos=(1, 1))
        ok(self, step(env, "wait", seconds=60))                # it notices the open door, then slips through
        self.assertEqual(env.world.s["pets"]["rex"]["location"], "hall")
        self.assertEqual(env.writes.last("pets:rex", "location")[:2], ("volition", "navigation_robot.pets"))

    def test_a_self_closing_door_shuts_with_its_own_cause(self):
        if not __import__("house_sim.systems.navigation_robot.doors", fromlist=["closer_owned_here"]).closer_owned_here():
            self.skipTest("the mechanisms / safety domains own door closers when loaded")
        m = Mini()
        m.c["entities"]["door_bath"].update(open=True, self_closing=True, close_delay_s=15.0, _opened_at_s=0.0)
        env = m.env(pos=(1, 1))
        ok(self, step(env, "wait", seconds=60))
        self.assertFalse(env.world.s["entities"]["door_bath"]["open"])
        self.assertEqual(env.writes.last("door_bath", "open")[0], "physical")
        self.assertIn("door_bath", env.writes.causes("door_bath", "open"))

    def _rv_house(self, **rv):
        m = Mini()
        m.add("rvdock", "robot_vacuum_dock", "living", pos=[11, 9])
        data = {"dock": "rvdock", "state": "docked", "docked": True, "bin_fill": 0.1}
        data.update(rv)
        m.add("rv", "robot_vacuum", "living", pos=[11, 9], rv=data)
        m.c["entities"]["floor_living"]["soil"] = {"dust": 0.4, "crumbs": 0.2}
        return m

    def test_a_robot_vacuum_the_robot_started_is_linked_to_the_robot_and_a_scheduled_one_is_not(self):
        env = self._rv_house().env(pos=(10, 8))
        ok(self, step(env, "start", "rv", program="clean"))
        ok(self, step(env, "wait", seconds=300))
        self.assertLess(env.world.s["entities"]["floor_living"]["soil"]["dust"], 0.4)
        self.assertEqual(env.writes.last("floor_living", "soil")[:2], ("physical", "navigation_robot.robot_vacuums"))
        self.assertIn("rv", env.writes.causes("floor_living", "soil"))
        self.assertIs(env.robot_attributable("floor_living", "soil"), True)
        # a run its schedule started (cleaning since setup): the robot did nothing, so nothing links it
        idle = self._rv_house(state="cleaning", docked=False, run_room="living", run_remaining_s=3000.0).env(pos=(1, 1))
        ok(self, step(idle, "wait", seconds=300))
        self.assertLess(idle.world.s["entities"]["floor_living"]["soil"]["dust"], 0.4)
        self.assertIs(idle.robot_attributable("floor_living", "soil"), False)


class MessFlowTests(unittest.TestCase):
    """6.5: what the domain moves or creates is logged with its kind, origin, writer and cause."""

    def setUp(self):
        self.m = Mini()
        self.m.add("rvdock", "robot_vacuum_dock", "living", pos=[11, 9])
        self.m.add("rv", "robot_vacuum", "living", pos=[11, 9], rv={"dock": "rvdock", "state": "docked", "docked": True,
                                                                     "bin_fill": 0.1})
        self.m.add("bin", "generic_item", "living", pos=[9, 9], label="trash can", category="trash", waste_stream="trash",
                   accepts_items=True, container=True, capacity_l=30.0)
        self.m.c["entities"]["floor_living"]["soil"] = {"dust": 0.4, "crumbs": 0.2}

    def test_a_cleaning_run_carries_debris_from_the_floor_into_the_bin_and_emptying_it_into_the_trash(self):
        env = self.m.env(pos=(10, 8))
        ok(self, step(env, "start", "rv", program="clean"))
        ok(self, step(env, "wait", seconds=600))
        into_bin = [f for f in flows(env, "debris") if f["src"] == "floor:living" and f["dst"] == "entity:rv"]
        self.assertTrue(into_bin)
        self.assertEqual({f["by"] for f in into_bin}, {"physical"})
        self.assertIn("rv", into_bin[0].get("cause") or [])
        picked = sum(f["amount"] for f in into_bin)
        self.assertGreater(picked, 5.0)                        # grams, above the debris tolerance
        tally = env.world.s["entities"]["rv"]["rv"]["_bin_g"]
        self.assertAlmostEqual(tally, 0.1 * 0.45 * 200.0 + picked, delta=0.01)
        env.world.s["entities"]["rv"]["rv"]["state"] = "idle"            # stopped where it is (a test's direct write)
        tally = env.world.s["entities"]["rv"]["rv"]["_bin_g"]
        ok(self, step(env, "empty_robot_vacuum", "rv", destination="bin"))
        out = [f for f in flows(env, "debris") if f["src"] == "entity:rv" and f["dst"] == "entity:bin"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["by"], "robot")
        self.assertAlmostEqual(out[0]["amount"], tally, delta=0.01)
        self.assertEqual(env.world.s["entities"]["rv"]["rv"]["_bin_g"], 0.0)
        # the vacuum and the trash are accepting sinks for debris (location_classes.json)
        self.assertIn("vacuum", ledger.classes_of(env.world.s["entities"]["rv"]))

    def test_an_auto_empty_dock_takes_the_bin_into_its_bag_and_the_bag_into_the_trash(self):
        m = Mini()
        m.add("rvdock", "auto_empty_dock", "living", pos=[11, 9], bag_fill=0.2)
        m.add("rv", "robot_vacuum", "living", pos=[10, 7], rv={"dock": "rvdock", "state": "returning", "docked": False,
                                                               "bin_fill": 0.5})
        m.add("bin", "generic_item", "living", pos=[9, 9], label="trash can", category="trash", waste_stream="trash",
              accepts_items=True, container=True, capacity_l=30.0)
        env = m.env(pos=(10, 8))
        ok(self, step(env, "wait", seconds=60))
        self.assertTrue(env.world.s["entities"]["rv"]["rv"]["docked"])
        into_bag = [f for f in flows(env, "debris") if f["src"] == "entity:rv" and f["dst"] == "entity:rvdock"]
        self.assertEqual(len(into_bag), 1)
        self.assertAlmostEqual(into_bag[0]["amount"], 0.5 * 0.45 * 200.0, delta=0.01)
        bag = env.world.s["entities"]["rvdock"]["_bag_g"]
        self.assertAlmostEqual(bag, 0.2 * 2.5 * 200.0 + 45.0, delta=0.01)
        ok(self, step(env, "empty_robot_vacuum", "rvdock", destination="bin"))
        out = [f for f in flows(env, "debris") if f["src"] == "entity:rvdock" and f["dst"] == "entity:bin"]
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["amount"], bag, delta=0.01)

    def test_mud_the_robot_tracks_in_is_created_debris_on_that_floor(self):
        env = self.m.env(room="kitchen", pos=(8, 4), nav_foot_soil={"mud": 0.6})
        ok(self, step(env, "move", "living"))
        tracked = [f for f in flows(env) if f["src"] == "created:robot_feet"]
        self.assertTrue(tracked)
        self.assertEqual({f["kind"] for f in tracked}, {"debris"})
        self.assertEqual({f["by"] for f in tracked}, {"robot"})
        self.assertTrue({f["dst"] for f in tracked} <= {"floor:kitchen", "floor:living"})

    def test_an_overfilled_tank_spills_created_mess_of_its_liquid_class(self):
        m = Mini()
        m.add("rv", "robot_vacuum_mop", "living", pos=[5, 5], rv={"state": "idle"})
        m.add("jug", "generic_item", "living", liquid_l=1.0, liquid="water", capacity_l=1.0)
        env = m.env(pos=(5, 4), hold={"right": "jug"})
        ok(self, step(env, "refill_robot_tank", "rv", tool="jug", amount_l=0.5))
        spilled = [f for f in flows(env) if f["src"] == "created:rv"]
        self.assertEqual(len(spilled), 1)
        self.assertEqual((spilled[0]["kind"], spilled[0]["origin"], spilled[0]["dst"]), ("liquid:spill", "water:rv", "floor:living"))
        self.assertAlmostEqual(spilled[0]["amount"], 0.2, places=3)

    def test_liquid_classes_of_spills(self):
        from house_sim.systems.navigation_robot import common as C
        self.assertEqual(C.liquid_mess("water"), ("liquid:spill", "water"))
        self.assertEqual(C.liquid_mess("coffee"), ("liquid:spill", "food"))
        self.assertEqual(C.liquid_mess("bleach"), ("liquid:chemical", "chemical"))
        self.assertEqual(C.liquid_mess("cooking_oil"), ("liquid:grease", "grease"))
        self.assertEqual(C.liquid_mess("dirty_water"), ("liquid:dirty_water", "dirty_water"))


class TruthTests(unittest.TestCase):
    """13.1-13.3: claims about the domain's state are scored by its truth mappings."""

    def setUp(self):
        m = Mini()
        m.add("rvdock", "robot_vacuum_dock", "living", pos=[11, 9])
        m.add("rv", "robot_vacuum", "living", pos=[11, 9], docked=True,
              rv={"dock": "rvdock", "state": "docked", "docked": True, "bin_fill": 0.97})
        m.add("puddle", "generic_item", "kitchen", pos=[2, 2], label="puddle", category="spill", floor_hazard="wet",
              liquid_l=0.3, _phys_spill=True)
        m.person("guest", "living", [3, 3], role="stranger")
        m.person("mom", "living", [4, 3])
        self.env = m.env(pos=(1, 1))
        self.state = self.env.world.s

    def truth(self, text):
        claims = [c for c in speech.parse(text, self.state, self.env) if c.get("certainty") == "asserted"]
        self.assertTrue(claims, text)
        return speech.truth(self.state, claims[0], env=self.env)

    def test_docked_and_bin_full(self):
        self.assertIs(self.truth("The robot vacuum is docked."), True)
        self.assertIs(self.truth("The robot vacuum is off its dock."), False)
        self.assertIs(self.truth("The robot vacuum's bin is full."), True)
        self.state["entities"]["rv"]["rv"]["bin_fill"] = 0.2
        self.state["entities"]["rv"]["docked"] = False
        self.assertIs(self.truth("The robot vacuum's bin is full."), False)
        self.assertIs(self.truth("The robot vacuum is docked."), False)

    def test_tables_merge_without_conflict_and_cover_the_patrol_words(self):
        tables = speech.truth_tables()
        for word in ("docked", "undocked"):
            self.assertIn(word, tables["states"])
        for word in ("bin_full", "water_on_floor", "unexpected_person"):
            self.assertIn(word, tables["issues"])
        self.assertIs(speech.issue_truth(self.state, "entity", "puddle", "water_on_floor"), True)
        self.assertIs(speech.issue_truth(self.state, "entity", "rv", "water_on_floor"), False)   # a trip hazard
        self.assertIs(speech.issue_truth(self.state, "person", "guest", "unexpected_person"), True)
        self.assertIs(speech.issue_truth(self.state, "person", "mom", "unexpected_person"), False)


class DerivedAndSettingsTests(unittest.TestCase):
    """8.2 derived fields and 10.3 settings skills."""

    def _house(self):
        m = Mini()
        m.add("rvdock", "robot_vacuum_dock", "living", pos=[11, 9])
        m.add("rv", "robot_vacuum", "living", pos=[11, 9], docked=True,
              rv={"dock": "rvdock", "state": "docked", "docked": True})
        return m

    def test_docked_is_recomputed_from_where_the_robot_vacuum_is(self):
        self.assertIn("docked", DERIVED_FIELDS)
        state = self._house().env(pos=(1, 1)).world.s
        task = hardened({"success": [{"path": "entities.rv.docked", "op": "is_true"}]})
        self.assertTrue(evaluation.derived_view(state, task)["entities"]["rv"]["docked"])
        moved = copy.deepcopy(state)
        moved["entities"]["rv"]["pos"] = [4, 4]                # carried off by some other verb; the flag stayed
        self.assertTrue(moved["entities"]["rv"]["docked"])
        self.assertFalse(evaluation.derived_view(moved, task)["entities"]["rv"]["docked"])
        self.assertIs(evaluation.derived_view(moved, {"success": []}), moved)     # legacy tasks: untouched
        self.assertIs(DERIVED_FIELDS["docked"].recompute(state, "rvdock"), MISSING)

    def test_settings_skills_for_plugs_and_docking(self):
        state = self._house().env(pos=(1, 1)).world.s
        self.assertEqual(settings_steps(state, "rvdock", "plugged_in"),
                         [(False, {"skill": "use", "action": "unplug", "target": "rvdock"})])
        self.assertEqual(settings_steps(state, "rv", "docked"), [(False, {"skill": "fetch", "obj": "rv"})])
        state["entities"]["rv"]["docked"] = False
        state["entities"]["rv"]["rv"]["docked"] = False
        self.assertEqual(settings_steps(state, "rv", "docked"), [(True, {"skill": "use", "action": "dock", "target": "rv"})])
        ladder = Mini().reg.make("step_ladder", "living")
        state["entities"]["ladder"] = ladder
        self.assertEqual(settings_steps(state, "ladder", "settings.spreader_lock")[0][0], "locked")   # kernel default

    def test_leveling_tilt_follows_its_feet_whatever_wrote_them(self):
        from house_sim.systems.navigation_robot import measure as M
        m = Mini()
        m.add("washer", "generic_item", "living", pos=[8, 1], label="washing machine", size_m=[0.6, 0.6, 0.85],
              leveling={"floor_mm": {"fl": 0.0, "fr": 4.0, "bl": 0.0, "br": 4.0}, "span_mm": [560.0, 560.0]},
              settings={"foot_fl_mm": 0.0, "foot_fr_mm": 0.0, "foot_bl_mm": 0.0, "foot_br_mm": 0.0, "lock_nuts": "locked"})
        rec = m.c["entities"]["washer"]
        rec["_tilt_deg"], rec["_wobble_mm"] = M.compute_level(rec)
        env = m.env(pos=(1, 1))
        self.assertGreater(abs(env.world.s["entities"]["washer"]["_tilt_deg"][0]), 0.1)
        # another verb's direct write of the feet (no setting_changed hook): the stored tilt must not stay latched
        env.world.s["entities"]["washer"]["settings"].update(foot_fl_mm=4.0, foot_bl_mm=4.0)
        ok(self, step(env, "wait", seconds=30))
        self.assertEqual(env.world.s["entities"]["washer"]["_tilt_deg"], [0.0, 0.0])
        self.assertTrue(M.leveling_idle(env.world))


class CarryOverTests(unittest.TestCase):
    """Phase-1 and run-A rows closed in this package (each failed before its fix)."""

    def test_mark_refuses_an_unfamiliar_target_identify_first_whatever_it_is(self):
        # closeout_REV 7 / VER2 6: "Hold a pencil" became "Hold a unfamiliar medium object" for an unfamiliar pencil alone
        look = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "label": "thing"}
        out = {}
        for kind in ("generic_item", "carpenter_pencil", "pencil", "tape_measure"):
            m = Mini()
            if kind not in m.reg.types:
                continue
            m.add("thing", kind, "living", pos=[2, 3], **look)
            env = m.env(pos=(1, 3), perception="realistic")
            h = env.realism.handles.handle("thing")
            ok(self, step(env, "approach", h))
            r = step(env, "mark", h, height_m=1.0)
            out[kind] = (r["ok"], r["category"], r["message"])
        self.assertEqual(len(set(out.values())), 1, out)
        self.assertIn("identify it first", next(iter(out.values()))[2])

    def test_robot_vacuum_look_alikes_answer_alike_to_every_verb_of_the_domain_until_inspected(self):
        # closeout_REV 5 / VER2 4 (the robot_vacuum group): no verb of this domain tells an unresolved robot vacuum
        # from a vacuum-mop, on the floor or held (the sweep of the handoff ran every verb x parameter, room and
        # geometric; this pins the household-robot verbs and the tool verbs). Handles differ between the twins (the
        # handle salt is a digest of the initial house), so each answer names them by role.
        plans = [("empty_robot_vacuum", {"destination": "trash_1"}), ("clean_robot_brushes", {}), ("free_stuck_robot", {}),
                 ("dock", {}), ("refill_robot_tank", {"tool": "jug_1"}), ("climb_on", {}), ("check_level", {}),
                 ("mark", {"height_m": 1.0}), ("measure", {}), ("use_reacher", {}), ("swap_battery", {})]
        out = {}
        for kind in ("robot_vacuum", "robot_vacuum_mop"):
            rows = []
            for held in (False, True):
                m = Mini()
                m.add("thing_1", kind, "living", pos=[2, 3], rv={"state": "idle", "docked": False})
                m.add("trash_1", "generic_item", "living", pos=[1, 4], label="trash can", category="trash",
                      waste_stream="trash", accepts_items=True, container=True, capacity_l=30.0)
                m.add("jug_1", "generic_item", "living", pos=[1, 2], liquid_l=1.0, liquid="water", capacity_l=1.0)
                env = m.env(pos=(1, 3), perception="realistic")
                names = {env.realism.handles.handle(e): "<{}>".format(e) for e in ("thing_1", "trash_1", "jug_1")}
                h = env.realism.handles.handle("thing_1")
                ok(self, step(env, "approach", h))
                if held:
                    ok(self, step(env, "crouch"))
                    ok(self, step(env, "pick_up", h))
                for verb, kw in plans:
                    args = {k: env.realism.handles.handle(v) if v in env.world.s["entities"] else v for k, v in kw.items()}
                    r = step(copy.deepcopy(env), verb, h, **args)
                    message = r["message"]
                    for handle, name in names.items():
                        message = message.replace(handle, name)
                    rows.append((held, verb, r["ok"], r["category"], message))
            out[kind] = rows
        self.maxDiff = None
        self.assertEqual(out["robot_vacuum"], out["robot_vacuum_mop"])

    def test_dock_setup_always_places_its_blocker(self):
        # WP11 held-out finding: T-5ad61b6881821b-01 seed 502 -- a coffee table across the dock's front left no cell for
        # the basket, so {blocker} bound None and the reference's fetch failed
        from house_sim.tasks.runner import build
        spec = json.loads((ROOT / "tasks" / "authored" / "navigation_robot__examples.json").read_text(encoding="utf-8"))
        spec = copy.deepcopy(next(s for s in spec["specs"] if s["catalog_id"] == "T-5ad61b6881821b-01"))
        spec["variants"] = spec["variants"][:1]
        config, _task, oracle = build(spec, seed=502)
        bindings = oracle["bindings"]
        self.assertIsNotNone(bindings.get("blocker"))
        from house_sim.systems.navigation_robot.robot import front_cells
        rec = config["entities"][bindings["blocker"]]
        self.assertIn(tuple(rec["pos"]), [tuple(c) for c in front_cells(config, bindings["dock"])])

    def test_go_takes_unguided_winders_at_the_careful_pace(self):
        from house_sim.tasks.runner import run_plan
        m = Mini()
        m.c["connections"]["c_hu"].update(winders=3, handrail=False)
        del m.c["entities"]["rail_1"]
        env = m.env(room="upstairs", pos=(1, 6))
        run_plan(env, [{"skill": "navigation_robot.go", "target": "hall"}], {})
        moves = [h["command"] for h in env._history if (h.get("command") or {}).get("action") == "go_to"]
        self.assertEqual(moves[-1].get("speed"), "careful")
        self.assertEqual(env.world.s["robot"]["room"], "hall")
        self.assertFalse(env.world.s["robot"].get("fallen"))
        # a guided flight (rail, free hand, lit) keeps the robot's own pace
        m2 = Mini()
        m2.c["connections"]["c_hu"]["winders"] = 3
        env2 = m2.env(room="upstairs", pos=(1, 6))
        run_plan(env2, [{"skill": "navigation_robot.go", "target": "hall"}], {})
        moves2 = [h["command"] for h in env2._history if (h.get("command") or {}).get("action") == "go_to"]
        self.assertIsNone(moves2[-1].get("speed"))


class ReadingTests(unittest.TestCase):
    """15.2: measuring verbs return structured instrument results the evidence index reads."""

    def test_measure_and_check_level_return_structured_readings(self):
        from house_sim.kernel import reports
        m = Mini()
        m.add("tape", "tape_measure", "living")
        m.add("cab", "generic_item", "living", pos=[2, 4], label="cabinet", size_m=[0.8, 0.4, 0.9])
        env = m.env(pos=(1, 4), hold={"right": "tape"})
        r = ok(self, step(env, "measure", "cab", dimension="width", units="cm"))
        data = json.loads(r["message"]) if isinstance(r["message"], str) and r["message"].startswith("{") else r.get("result")
        readings = data["readings"]
        self.assertEqual(len(readings), 1)
        self.assertEqual((readings[0]["subject"], readings[0]["quantity"], readings[0]["unit"]), ("cab", "length", "cm"))
        self.assertEqual(readings[0]["value"], data["value"])
        q, u = reports.unit_of(readings[0]["unit"], readings[0]["quantity"])
        self.assertAlmostEqual(reports.convert(readings[0]["value"], u, q), 0.8, delta=0.01)
        self.assertNotIn("readings", env.world.s["facts"]["nav_last_measure"])     # stored records unchanged
        lv = ok(self, step(env, "check_level", "cab"))
        lvd = json.loads(lv["message"]) if lv["message"].startswith("{") else lv.get("result")
        self.assertEqual([x["dimension"] for x in lvd["readings"]], ["tilt_x", "tilt_y"])


if __name__ == "__main__":
    unittest.main()
