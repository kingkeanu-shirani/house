"""The must-pass battery: legitimate variants that must pass (docs/design/VERIFY_HARDENING.md 11; WP6,
house_sim/tasks/battery/mustpass.py).

Small schema-4 tasks run through the verifier's core trials and then the must-pass battery: a book task whose
reference first moves a second book aside (M2 alternative places, M3 reorder, M4 detour), with a broad and with a
fitted `to` scope for that book (the fitted scope is the false negative M2 exists to find); a talk-only warning (M5
paraphrase); a task whose reference waits for a fan to come on (M6 polling, in the spec's own mode, where the wait is
legal).
"""
import copy
import importlib
import unittest

from house_sim.kernel.plugins import load_domains
from house_sim.tasks import battery, strength, verify
from tests.test_battery import BOOK_SETUP, book_spec, warn_spec

# battery.mustpass is the S5 entry point (a function, section 25); the module of the same name holds the families
mustpass = importlib.import_module("house_sim.tasks.battery.mustpass")
GEO, OWN = verify.EVAL_GEOM, verify.OWN


def access_spec(scope):
    """Shelve the book; the reference first moves a second book from the table to the shelf (a non-target the
    instruction does not ask about), whose envelope scope is `scope`."""
    setup = copy.deepcopy(BOOK_SETUP)
    setup.insert(5, {"op": "spawn", "type": "book", "on": "{table}", "var": "other"})
    return book_spec(catalog_id="T-wp6-access-01", setup=setup,
                     envelope={"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]},
                                                                    "{other}": {"to": scope}}},
                     reference=[{"skill": "fetch", "obj": "{other}"},
                                {"skill": "put", "obj": "{other}", "dest": "{shelf}"},
                                {"skill": "fetch", "obj": "{book}"},
                                {"skill": "put", "obj": "{book}", "dest": "{shelf}"}])


def poll_spec():
    """Shelve the book once the fan has come on (an event at 90 s); the reference waits for it."""
    setup = copy.deepcopy(BOOK_SETUP)
    setup.insert(4, {"op": "spawn", "type": "portable_fan", "floor": "{living}", "var": "fan"})
    setup.insert(5, {"op": "event", "at_s": 90, "changes": {"entities.{fan}.on": True}})
    return book_spec(catalog_id="T-wp6-poll-01", setup=setup,
                     reference=[{"wait_until": {"path": "entities.{fan}.on", "op": "eq", "value": True},
                                 "max_s": 300},
                                {"skill": "fetch", "obj": "{book}"},
                                {"skill": "put", "obj": "{book}", "dest": "{shelf}"}],
                     budget={"steps": 40, "time_s": 600})


SPECS = {"broad": lambda: access_spec(["rooms:work"]), "fitted": lambda: access_spec(["{shelf}"]),
         "warn": warn_spec, "poll": poll_spec, "book": book_spec}
_CACHE = {}


def mustpass_run(name, mode=GEO, seeds=(0,)):
    key = (name, mode, tuple(seeds))
    if key not in _CACHE:
        spec = SPECS[name]()
        out = verify.mode_trials(spec, list(seeds), mode)
        assert all(t["passed"] for t in out["trials"]), [(t["seed"], t.get("error")) for t in out["trials"]]
        builds = {"mode": mode, "references": out["references"], "extras": out["extras"]}
        _CACHE[key] = battery.mustpass(spec, builds, out["references"], list(seeds), mode, "standard")
    return _CACHE[key]


def by_family(result, family):
    return [r for r in result["runs"] if r.get("family") == family]


class AccessTests(unittest.TestCase):
    """A non-target moved for access: its legitimate alternative places pass with a broad scope and the fitted
    scope is caught."""

    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.broad = mustpass_run("broad")
        cls.fitted = mustpass_run("fitted")

    def test_alternative_places_pass_with_a_broad_scope(self):
        m2 = {r["sub"]: r for r in by_family(self.broad, "M2")}
        self.assertEqual(sorted(m2), ["baseline", "surface"])
        for rec in m2.values():
            self.assertEqual(rec["outcome"], "passes", rec)
            self.assertTrue(rec["realised"])

    def test_a_fitted_scope_rejects_a_legitimate_place(self):
        m2 = {r["sub"]: r for r in by_family(self.fitted, "M2")}
        self.assertEqual(m2["baseline"]["outcome"], "passes")
        surface = m2["surface"]
        self.assertEqual(surface["outcome"], "fails")
        self.assertTrue(any(l.startswith("house.misplaced:") for l in surface["rejected_by"]), surface)
        self.assertIn("narrow `to` scope", surface["hint"])
        self.assertEqual(self.fitted["summary"]["failed_unexempt"], 1)
        record = {"level": "standard", "mustpass": self.fitted}
        self.assertFalse(strength.gates(record)["G8"])
        record = {"level": "standard", "mustpass": self.broad}
        self.assertTrue(strength.gates(record)["G8"])

    def test_reorder_of_independent_steps_passes(self):
        m3 = by_family(self.broad, "M3")
        self.assertEqual([r["outcome"] for r in m3], ["passes"])

    def test_a_detour_within_the_slack_passes(self):
        self.assertEqual([r["outcome"] for r in by_family(self.broad, "M4")], ["passes"])

    def test_a_book_moved_aside_is_no_instrument(self):
        self.assertEqual([r["outcome"] for r in by_family(self.broad, "M1")], ["skipped:na"])

    def test_the_summary_counts_for_g8(self):
        summary = self.broad["summary"]
        self.assertEqual(summary["failed_unexempt"], 0)
        self.assertEqual(summary["applicable"], summary["realised"])
        self.assertEqual(summary["passed"], summary["realised"])


class ParaphraseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.result = mustpass_run("warn")

    def test_a_paraphrased_warning_passes(self):
        m5 = by_family(self.result, "M5")
        self.assertEqual([r["outcome"] for r in m5], ["passes"])
        self.assertTrue(m5[0]["realised"])


class PollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.result = mustpass_run("poll", mode=OWN)

    def test_polling_instead_of_the_conditioned_wait_passes(self):
        m6 = by_family(self.result, "M6")
        self.assertEqual([r["outcome"] for r in m6], ["passes"])
        self.assertTrue(m6[0]["realised"])


class ModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_the_book_detour_passes_in_both_modes(self):
        for mode in (GEO, OWN):
            result = mustpass_run("book", mode=mode)
            self.assertEqual([r["outcome"] for r in by_family(result, "M4")], ["passes"], mode)
            self.assertEqual(result["harness_errors"], [])


class GenerationTests(unittest.TestCase):
    """Generation decisions that need no run."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_carriers_and_tools_are_instruments(self):
        class Ctx(object):
            baseline = {"entities": {"pan_1": {"type": "dustpan"}, "bin_1": {"type": "trash_can"},
                                     "basket_1": {"type": "laundry_basket"}, "shirt_1": {"type": "shirt"}}}
            entities = baseline["entities"]
            trace = [{"action": "pick_up", "target": "pan_1", "ok": True},
                     {"action": "empty_into", "target": "pan_1", "destination": "bin_1", "ok": True},
                     {"action": "pick_up", "target": "basket_1", "ok": True},
                     {"action": "pick_up", "target": "shirt_1", "ok": True},
                     {"action": "place", "target": "shirt_1", "destination": "basket_1", "ok": True}]
        self.assertEqual(mustpass._carriers(Ctx()), {"pan_1", "basket_1"})
        self.assertTrue(mustpass._tool_like({"type": "broom", "is_a": ["cleaning_tool"]}))
        self.assertFalse(mustpass._tool_like({"type": "book"}))

    def test_typed_measurements_change_units(self):
        out = mustpass._convert_report({"do": "report", "kind": "measurement", "value": 250, "unit": "g"})
        self.assertEqual(out["unit"], "oz")
        self.assertAlmostEqual(out["value"], 8.818, places=2)
        self.assertEqual(mustpass._convert_report({"do": "report", "value": 20, "unit": "c"})["value"], 68.0)
        self.assertIsNone(mustpass._convert_report({"do": "report", "value": "x", "unit": "g"}))

    def test_hints_cover_every_family(self):
        self.assertEqual(sorted(mustpass.HINTS), ["M1", "M2", "M3", "M4", "M5", "M6"])
        self.assertEqual([f for f, _ in mustpass.GENERATORS], ["M1", "M2", "M3", "M4", "M5", "M6"])


if __name__ == "__main__":
    unittest.main()
