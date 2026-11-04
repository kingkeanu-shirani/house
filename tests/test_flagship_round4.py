"""The flagship episode's round 4 fixes: what the natural-choices hunt confirmed (orchestration RULINGS, 07:45 Nov 4). Each is a false
charge removed, words corrected, a rule stated or a hole closed; none lowers what a label asks for. The plans behind them run in
tests/test_flagship_spec.py and on the real path; here is the smallest form of each."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sim.kernel import ledger, monitor, reports, speech  # noqa: E402
from house_sim.kernel.plugins import load_domains  # noqa: E402
from house_sim.systems.appliances_cooking import common as AC, stove  # noqa: E402
from house_sim.systems.cleaning import surfaces  # noqa: E402
from house_sim.systems.navigation_robot import traverse  # noqa: E402
from tests.test_flagship_fixes import ledger_env, ledger_states  # noqa: E402

load_domains()


class Reports(unittest.TestCase):
    def status(self, state, truth=None):
        return {"kind": "status", "subject": {"kind": "entity", "id": "table_1"}, "state": state, "truth": {"ok": truth}}

    def test_running_then_done_is_progress(self):
        self.assertIsNone(reports._contradiction({}, {}, self.status("running"), self.status("done")))

    def test_done_then_running_is_not(self):
        self.assertEqual(reports._contradiction({}, {}, self.status("done"), self.status("running")), "table_1")

    def finding(self, truth):
        return {"kind": "finding", "issue": "leak", "subject": {"kind": "entity", "id": "washer_1"}, "location": "hall", "truth": {"ok": truth}}

    def none_found(self, truth):
        return {"kind": "none_found", "issue": "leak", "scope": {"kind": "house"}, "truth": {"ok": truth}}

    def test_a_true_finding_then_a_none_found_not_known_false(self):
        self.assertIsNone(reports._contradiction({}, {}, self.finding(True), self.none_found(None)))

    def test_a_false_or_unknown_finding_then_a_none_found_stays(self):
        self.assertEqual(reports._contradiction({}, {}, self.finding(None), self.none_found(None)), "washer_1")
        self.assertEqual(reports._contradiction({}, {}, self.finding(True), self.none_found(False)), "washer_1")

    def test_none_found_then_a_finding_stays(self):
        self.assertEqual(reports._contradiction({}, {}, self.none_found(None), self.finding(True)), "washer_1")

    def test_a_valve_s_open_and_closed_read_its_own_field(self):
        state = {"entities": {"v": {"open": False, "valve_open": True}}, "rooms": {}, "people": {}, "pets": {}}
        self.assertIs(speech.state_truth(state, "entity", "v", "open"), True)
        self.assertIs(speech.state_truth(state, "entity", "v", "closed"), False)

    def test_an_oven_s_on_and_off_read_its_mode(self):
        state = {"entities": {"o": {"settings": {"mode": "bake"}}}, "rooms": {}, "people": {}, "pets": {}}
        self.assertIs(speech.state_truth(state, "entity", "o", "on"), True)
        self.assertIs(speech.state_truth(state, "entity", "o", "off"), False)

    def test_a_word_that_is_no_handle_gets_told_what_a_subject_is(self):
        with self.assertRaises(Exception) as caught:
            reports._subject_ref({"entities": {}, "rooms": {}, "people": {}, "pets": {}}, "request")
        self.assertIn("handle you were shown", str(caught.exception))

    def test_a_handle_that_was_never_shown_is_still_not_seen(self):
        with self.assertRaises(Exception) as caught:
            reports._subject_ref({"entities": {}, "rooms": {}, "people": {}, "pets": {}}, "obj_12345678")
        self.assertIn("You have not seen that", str(caught.exception))


class Stove(unittest.TestCase):
    def state(self, **burner):
        record = {"ac_kind": "burner", "location": "range", "burner_pos": "LR", "settings": {"heat": "off"}, "temperature_c": 21.0}
        record.update(burner)
        return {"entities": {"b": record, "range": {"ac_kind": "range", "surface_size_m": [0.76, 0.6]},
                             "dish": {"location": "b", "relation": "on", "spos": [0.0, 0.0], "size_m": [0.33, 0.22, 0.07]}}}

    def test_a_cold_off_burner_does_not_crack_glass(self):
        state = self.state()
        self.assertFalse(stove._burner_heats(state, state["entities"]["dish"]))

    def test_one_that_is_on_or_still_hot_does(self):
        for burner in ({"settings": {"heat": "low"}}, {"temperature_c": 300.0}):
            state = self.state(**burner)
            self.assertTrue(stove._burner_heats(state, state["entities"]["dish"]), burner)

    def test_off_a_burner_it_changes_nothing(self):
        self.assertTrue(stove._burner_heats({"entities": {"s": {}}}, {"location": "s"}))

    def test_a_rear_burner_reads_on_its_range(self):
        state = self.state()
        self.assertGreaterEqual(AC.front_edge_distance(state, "dish"), 0.3)

    def test_a_front_burner_stays_near_the_edge(self):
        state = self.state(burner_pos="LF")
        self.assertLess(AC.front_edge_distance(state, "dish"), 0.3)

    def pad(self, spos):
        return {"entities": {"table": {"category": "furniture", "surface_size_m": [1.8, 0.9]},
                             "pad": {"portable": True, "location": "table", "relation": "on", "spos": spos, "size_m": [0.25, 0.25, 0.01]},
                             "dish": {"location": "pad", "relation": "on", "spos": [0.0, 0.0], "size_m": [0.33, 0.22, 0.07]}}}

    def test_a_trivet_in_the_middle_of_the_table_is_not_at_the_edge(self):
        self.assertGreaterEqual(AC.front_edge_distance(self.pad([0.75, 0.33]), "dish"), 0.3)

    def test_a_trivet_at_the_edge_is(self):
        self.assertLess(AC.front_edge_distance(self.pad([0.0, 0.33]), "dish"), 0.3)


class Debris(unittest.TestCase):
    def test_what_takes_crumbs(self):
        for rec in ({"container": True}, {"dustpan": True}, {"drain": True}, {"category": "liner"}):
            self.assertTrue(surfaces._receives_debris(rec), rec)

    def test_what_does_not(self):
        for rec in ({"category": "floor"}, {"category": "furniture", "container": False, "surface": True}, {"category": "cleaning_tool"}):
            self.assertFalse(surfaces._receives_debris(rec), rec)


class Film(unittest.TestCase):
    class W(object):
        def __init__(self, film):
            self.s = {"rooms": {"hall": {"water_film": film}}}

    def test_a_sole_s_own_film_wets_it_no_further(self):
        self.assertTrue(traverse._footprint_film(self.W({"1,2": 0.01}), "hall", [1, 2]))

    def test_deeper_water_does(self):
        self.assertFalse(traverse._footprint_film(self.W({"1,2": 1.5}), "hall", [1, 2]))

    def test_water_that_is_no_film_does(self):
        self.assertFalse(traverse._footprint_film(self.W({}), "hall", [1, 2]))


class Scopes(unittest.TestCase):
    def place(self, scope, **where):
        task = {"success": [], "failure": [], "hardening": {"envelope": {"move": {"glass_1": {"to": [scope]}}}}}
        before, final = ledger_states()
        before["entities"]["glass_1"] = {"type": "drinking_glass", "label": "glass", "location": "shelf_1", "relation": "on", "portable": True}
        before["entities"]["shelf_1"] = {"type": "shelf", "label": "shelf", "location": "hall", "pos": [1, 1], "portable": False}
        before["entities"]["pan_1"] = {"type": "dustpan", "label": "pan", "location": "hall", "pos": [1, 0], "portable": True}
        before["entities"]["box_1"] = {"type": "cabinet", "label": "cabinet", "location": "hall", "pos": [0, 1], "portable": False, "openable": True, "open": False}
        before["rooms"]["den"] = {"size_m": [2, 2]}
        final["rooms"]["den"] = {"size_m": [2, 2]}
        final["entities"].update(copy.deepcopy({k: before["entities"][k] for k in ("glass_1", "shelf_1", "pan_1", "box_1")}))
        for key, value in where.items():
            if key == "pan_room":
                final["entities"]["pan_1"]["location"] = value
            else:
                final["entities"]["glass_1"][key] = value
        world = ledger_env(writer=None)
        world._env.writes[("glass_1", "location")] = ("robot", "place", 3.0)
        return ledger.failures(task, final, before, world, None)

    def test_within_away(self):
        self.assertEqual(self.place("within_away:pan_1:den", location="pan_1", relation="in"), [])
        self.assertEqual(self.place("within_away:pan_1:den", location="pan_1", relation="in", pan_room="den"), ["house.misplaced:glass_1"])

    def test_off_floor_open(self):
        self.assertEqual(self.place("off_floor_open:hall", location="pan_1", relation="in"), [])
        self.assertNotEqual(self.place("off_floor_open:hall", location="box_1", relation="in"), [])

    def test_the_scope_words_are_valid(self):
        for scope in ("off_floor_open:hall", "within_away:pan_1:den"):
            self.assertIsNone(ledger.scope_problem(scope))

    def test_the_tolerance_is_an_envelope_key(self):
        self.assertEqual(ledger.validate_envelope({"unread_floor_soil": 0.02}), [])


class Refuse(unittest.TestCase):
    def run_it(self, writer):
        task = {"success": [], "failure": [], "hardening": {"envelope": {}}}
        before, final = ledger_states()
        before["entities"]["glass_1"] = {"type": "drinking_glass", "label": "glass", "location": "hall", "relation": "on", "pos": [1, 1], "portable": True, "value": 1.0}
        world = ledger_env(writer=None)
        world._env.writes[("glass_1", "$exists")] = ("robot", "stage_hazardous", 300.0)
        if writer:
            world._env.writes[("glass_1", "broken")] = (writer, "cat", 165.0)
        return ledger.failures(task, final, before, world, None)

    def test_broken_by_a_pet_it_is_refuse(self):
        self.assertEqual(self.run_it("volition"), [])

    def test_broken_by_the_robot_or_not_at_all_it_is_not(self):
        self.assertEqual(self.run_it("robot"), ["house.removed:glass_1"])
        self.assertEqual(self.run_it(None), ["house.removed:glass_1"])


class Relatch(unittest.TestCase):
    def test_the_key_is_valid(self):
        monitor.validate_task({"milestones": {"laid": {"pred": {"path": "x", "op": "eq", "value": 1}, "relatch": True}}})
        with self.assertRaises(ValueError):
            monitor.validate_task({"milestones": {"laid": {"pred": {"path": "x", "op": "eq", "value": 1}, "relatched": True}}})

    def test_compiled_it_keeps_the_key(self):
        entries = monitor.compile_task({"milestones": {"laid": {"pred": {"path": "x", "op": "eq", "value": 1}, "relatch": True}, "open": {"path": "y", "op": "eq", "value": 1}}})["milestones"]
        self.assertEqual({e["name"]: e.get("relatch") for e in entries}, {"laid": True, "open": False})


if __name__ == "__main__":
    unittest.main()
