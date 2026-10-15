"""Predicate forms of kernel v3.1 (TIER1_SWEEP 5.4, 8.1, 8.4 and 21 "TIME-EVAL" item 6): quantifiers over people,
pets and paths, aggregates, milestone_count, mind.* paths through the evaluation overlay, and the report / report_set
forms delegating to kernel/reports.py (PEOPLE-IO's module, with their time window over typed and legacy reports)."""
import unittest
from unittest import mock

from house_sim.kernel import evaluation, reports
from house_sim.kernel.predicates import MISSING, TEMPORAL_KEY, check, validate_predicate


def state():
    return {
        "time_s": 900.0,
        "rooms": {"kitchen": {}, "hall": {}},
        "entities": {"bowl_1": {"type": "bowl", "location": "table", "portion_g": 120, "category": "dish"},
                     "bowl_2": {"type": "bowl", "location": "table", "portion_g": 80, "category": "dish"},
                     "plate_1": {"type": "plate", "location": "table", "portion_g": 0},
                     "table": {"type": "table", "location": "kitchen"}},
        "people": {"ana": {"role": "adult", "location": "kitchen", "awake": True, "hunger": 0.7},
                   "ben": {"role": "child", "location": "kitchen", "awake": True, "hunger": 0.2},
                   "cat_lady": {"role": "adult", "location": "hall", "awake": False}},
        "pets": {"rex": {"species": "dog", "location": "hall", "fed": False}},
        "household": {"guests": [{"name": "Kim", "diet": "vegan"}, {"name": "Lou", "diet": "omnivore"}]},
        "report_log": [{"t": 100.0, "kind": "finding", "issue": "leak"},
                       {"t": 700.0, "kind": "finding", "issue": "mold"}],
    }


class QuantifierInTests(unittest.TestCase):
    def test_people_pets_and_paths(self):
        s = state()
        rule = {"forall": {"in": "people", "where": {"room": "kitchen"}, "check": {"path": "awake", "op": "is_true"}}}
        validate_predicate(rule)
        self.assertTrue(check(s, rule))
        self.assertTrue(check(s, {"exists": {"in": "pets", "where": {"type": "dog"},
                                             "check": {"path": "fed", "op": "is_false"}}}))
        self.assertTrue(check(s, {"count": {"in": "people", "where": {"role": "adult"}}, "op": "eq", "value": 2}))
        self.assertTrue(check(s, {"exists": {"in": "household.guests", "where": {"diet": "vegan"},
                                             "check": {"path": "$id", "op": "eq", "value": "0"}}}))
        self.assertTrue(check(s, {"forall": {"in": "people", "where": {"ids": ["ana", "ben"]},
                                             "check": {"path": "$room", "op": "eq", "value": "kitchen"}}}))
        self.assertTrue(check(s, {"count": {"in": "no.such.path"}, "op": "eq", "value": 0}))

    def test_bad_in_is_refused(self):
        with self.assertRaises(ValueError):
            validate_predicate({"exists": {"in": ["people"]}})


class AggregateTests(unittest.TestCase):
    def test_sum_count_min_max_avg_and_operands(self):
        s = state()
        def agg(fn, **kw):
            body = {"fn": fn, "where": {"type": "bowl"}}
            if fn != "count":
                body["field"] = "portion_g"
            body.update(kw)
            return body
        cases = [({"agg": agg("sum"), "op": "eq", "value": 200.0}, True),
                 ({"agg": agg("count"), "op": "eq", "value": 2}, True),
                 ({"agg": agg("min"), "op": "eq", "value": 80.0}, True),
                 ({"agg": agg("max"), "op": "eq", "value": 120.0}, True),
                 ({"agg": agg("avg"), "op": "approx", "value": [100.0, 0.01]}, True),
                 # enough portions for whoever is in the kitchen: 2 bowls >= 2 people
                 ({"agg": agg("count"), "op": "gte", "ref_agg": {"fn": "count", "in": "people",
                                                                   "where": {"room": "kitchen"}}}, True),
                 ({"agg": {"fn": "sum", "in": "people", "field": "hunger"}, "op": "approx", "value": [0.9, 1e-9]}, True),
                 ({"agg": agg("sum"), "op": "gt", "ref": "time_s"}, False),
                 ({"agg": agg("sum", check={"path": "portion_g", "op": "gte", "value": 100}), "op": "eq",
                   "value": 120.0}, True)]
        for rule, expected in cases:
            validate_predicate(rule)
            self.assertEqual(check(s, rule), expected, rule)

    def test_empty_selections(self):
        s = state()
        empty = {"type": "cup"}
        self.assertTrue(check(s, {"agg": {"fn": "sum", "where": empty, "field": "x"}, "op": "eq", "value": 0}))
        self.assertTrue(check(s, {"agg": {"fn": "count", "where": empty}, "op": "eq", "value": 0}))
        for fn in ("min", "max", "avg"):
            self.assertFalse(check(s, {"agg": {"fn": fn, "where": empty, "field": "x"}, "op": "lt", "value": 1e9}))
            self.assertFalse(check(s, {"not": {"not": {"agg": {"fn": fn, "where": empty, "field": "x"},
                                                       "op": "gte", "value": -1e9}}}))
        self.assertFalse(check(s, {"agg": {"fn": "sum", "where": {"type": "bowl"}, "field": "portion_g"},
                                   "op": "eq", "ref": "no.such"}))

    def test_malformed_aggregates(self):
        for rule in ({"agg": {"fn": "median", "field": "x"}, "op": "eq", "value": 1},
                     {"agg": {"fn": "sum"}, "op": "eq", "value": 1},
                     {"agg": {"fn": "count"}, "op": "eq"},
                     {"agg": {"fn": "count"}, "op": "eq", "value": 1, "ref": "x"},
                     {"agg": {"fn": "count", "bogus": 1}, "op": "eq", "value": 1}):
            with self.assertRaises(ValueError):
                validate_predicate(rule)


class OverlayTests(unittest.TestCase):
    def test_milestone_count_and_mind_paths(self):
        s = state()
        rule = {"milestone_count": "chime", "op": "gte", "value": 2}
        validate_predicate(rule)
        self.assertFalse(check(s, rule))                         # a plain state: nothing counted
        view = evaluation.evaluation_view(s, temporal={"milestones": {"chime": {"time_s": 5.0, "count": 2}},
                                                       "phases": {}})
        self.assertTrue(check(view, rule))
        plain = dict(view)
        plain[TEMPORAL_KEY] = {"milestones": {"chime": {"time_s": 5.0}}}
        self.assertFalse(check(plain, rule))                      # a plain milestone counts 1
        mind = {"map": {"kitchen": {}, "hall": {}}, "seen": {"bowl_1": {"room": "kitchen"}}}
        view = evaluation.evaluation_view(s, mind=mind)
        self.assertTrue(check(view, {"path": "mind.map", "op": "len_gte", "value": 2}))
        self.assertTrue(check(view, {"path": "mind.seen.bowl_1.room", "op": "eq", "value": "kitchen"}))
        self.assertFalse(check(s, {"path": "mind.map", "op": "len_gte", "value": 0}))
        self.assertIs(evaluation.evaluation_view(s), s)

    def test_evaluate_reads_mind_paths_through_the_overlay(self):
        class FakeMind(object):
            def view(self):
                return {"map": {"kitchen": {}, "hall": {}, "garage": {}}}
        task = {"success": [{"path": "mind.map", "op": "len_gte", "value": 3}]}
        s = state()
        s.update(stats={}, steps=1)
        with mock.patch.dict(evaluation.INVARIANTS, clear=True):   # domain invariants need a full house state
            result = evaluation.evaluate(task, s, s, {}, 1, mind=FakeMind())
            self.assertTrue(result["success"])
            self.assertFalse(evaluation.evaluate(task, s, s, {}, 1)["success"])


class ReportFormTests(unittest.TestCase):
    def test_report_forms_bound_typed_and_legacy_reports_by_their_window(self):
        """The forms run kernel/reports.py itself; after / before_s bound every record it matches, report_log records
        and legacy field reports alike (stage-C merge: the window used to bound report_log only, so a legacy report
        made before an "after" milestone still matched)."""
        s = state()
        s["report_log"] = [{"n": 1, "t": 100.0, "kind": "finding", "subject": {"kind": "entity", "id": "bowl_1"},
                            "issue": "leak", "to": [], "delivered": {}},
                           {"n": 2, "t": 700.0, "kind": "finding", "subject": {"kind": "entity", "id": "bowl_2"},
                            "issue": "mold", "to": [], "delivered": {}}]
        s["reports"] = {"plate_1": {"status": "chipped"}}
        s["event_log"] = [{"kind": "report", "target": "plate_1", "field": "status", "time_s": 300.0}]
        rule = {"report": {"kind": "finding", "issue": "mold"}}
        validate_predicate(rule)
        self.assertTrue(check(s, rule))
        # after a milestone, within 300 s of it: the overlay's milestone time bounds the records
        view = evaluation.evaluation_view(s, temporal={"milestones": {"seen": {"time_s": 500.0}}, "phases": {}})
        bounded = {"report": {"kind": "finding", "issue": "mold", "after": "seen", "before_s": 300}}
        self.assertTrue(check(view, bounded))
        self.assertFalse(check(view, {"report": {"kind": "finding", "issue": "mold", "after": "seen", "before_s": 100}}))
        self.assertFalse(check(view, {"report": {"kind": "finding", "issue": "leak", "after": "seen"}}))
        self.assertFalse(check(s, bounded))                      # the anchor never happened: nothing is after it
        self.assertTrue(check(s, {"report": {"issue": "leak", "before_s": 150}}))
        self.assertFalse(check(s, {"report": {"issue": "mold", "before_s": 150}}))
        self.assertFalse(check(view, {"report": {"issue": "mold", "after": "phase:p2"}}))
        self.assertTrue(check(s, {"not": {"report": {"kind": "finding", "issue": "fire"}}}))
        # a legacy field report (reports.<target>.<field>, its time from the event log) obeys the same window
        legacy = {"kind": "field", "subject": "plate_1", "field": "status", "value": "chipped"}
        self.assertTrue(check(s, {"report": legacy}))
        self.assertTrue(check(s, {"report": dict(legacy, before_s=400)}))
        self.assertFalse(check(s, {"report": dict(legacy, before_s=200)}))
        self.assertFalse(check(view, {"report": dict(legacy, after="seen")}))          # reported at 300, before 500
        self.assertTrue(check(view, {"not": {"report": dict(legacy, after="seen")}}))
        s["event_log"] = []                                      # its time is no longer known: outside every window
        self.assertTrue(check(s, {"report": legacy}))
        self.assertFalse(check(s, {"report": dict(legacy, before_s=100000)}))
        # report_set over the window: the leak reported before it does not count against the precision
        both = {"kind": "finding", "issue": ["leak", "mold"], "expected": ["bowl_2"]}
        self.assertFalse(check(view, {"report_set": both}))      # precision 0.5
        self.assertTrue(check(view, {"report_set": dict(both, after="seen")}))
        self.assertTrue(check(view, {"report_set": dict(both, min_precision=0.5)}))

    def test_report_set_thresholds(self):
        s = state()
        with mock.patch.object(reports, "match_set", return_value=(1.0, 0.5, {})) as fake:
            self.assertFalse(check(s, {"report_set": {"kind": "finding", "issue": "needs_water", "expected": ["a"]}}))
            self.assertTrue(check(s, {"report_set": {"kind": "finding", "issue": "needs_water", "expected": ["a"],
                                                     "min_recall": 0.5}}))
            args = fake.call_args[0][1]
            self.assertNotIn("min_recall", args)
            self.assertEqual(args["expected"], ["a"])

    def test_malformed_report_forms(self):
        for rule in ({"report": "leak"}, {"report": {}, "op": "eq"}, {"report_set": []}):
            with self.assertRaises(ValueError):
                validate_predicate(rule)
        self.assertIs(MISSING, MISSING)


class ValuelessOpsTests(unittest.TestCase):
    def test_is_true_and_is_false_need_no_value(self):
        s = state()
        validate_predicate({"path": "people.ana.awake", "op": "is_true"})
        self.assertTrue(check(s, {"path": "people.ana.awake", "op": "is_true"}))
        self.assertTrue(check(s, {"path": "people.nobody.awake", "op": "is_false"}))
        with self.assertRaises(ValueError):
            validate_predicate({"path": "people.ana.awake", "op": "eq"})


if __name__ == "__main__":
    unittest.main()
