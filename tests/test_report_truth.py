"""Report truth, corrections, contradictions and false reports (docs/design/VERIFY_HARDENING.md 13.6 and 15.2 `as_of`,
work package WP4: kernel/reports.py).

What this file pins:

* reports.record stores each record's truth at report time ("truth") only in an episode that scores content (schema 4
  or a task using `said`): a status's state, a finding's issue on its subject, a none_found's issue in its scope, a
  measurement's true value and the value the robot had learned; legacy episodes' records and the legacy report's
  event are unchanged;
* the `corrects=<n>` report argument (validated, echoed in the message);
* reports.guards: house.false_report:<n> (status, finding, none_found, measurement beyond max(3 x the spec's tolerance,
  25 %, the floor)), report:contradiction:<subject> (opposite states, measurements apart, counts, a none_found against
  a finding of the same issue class, legacy fields reported twice with different values) unless corrected or both true
  when made, opt-outs per recipient;
* the report predicate's "as_of": "report" | "observed" (oracle and realistic perception) and its validation.

Hand-built houses with core types only: the file passes with HOUSE_SIM_DOMAINS=core.
"""
import copy
import json
import unittest

from house_sim.kernel import predicates, reports
from house_sim.kernel.engine import HouseEnv

COLS, ROWS = 12, 8


def config(extra_entities=None):
    """A lit kitchen ("lab") with a leaking pipe, a dry sink, an open window, a pot of soup at 60 C, a kitchen counter
    with a box on it; a hall; Ann (adult) in the lab and Sam (adult, with a phone) in the hall."""
    ents = {"counter_1": {"type": "table", "label": "counter", "location": "lab", "pos": [2, 1], "accepts_items": True},
            "pipe_1": {"type": "generic_item", "label": "pipe", "location": "lab", "pos": [5, 1], "category": "pipe",
                       "leaking": True, "size_m": [0.6, 0.1, 0.1], "portable": False},
            "sink_1": {"type": "generic_item", "label": "sink", "location": "lab", "pos": [7, 1], "leaking": False,
                       "size_m": [0.6, 0.5, 0.3], "portable": False},
            "window_1": {"type": "window", "label": "kitchen window", "location": "lab", "pos": [0, 3], "open": True},
            "pot_1": {"type": "generic_item", "label": "soup pot", "location": "counter_1", "relation": "on",
                      "temp_c": 60.0, "size_m": [0.25, 0.25, 0.2]},
            "box_1": {"type": "generic_item", "label": "box", "location": "counter_1", "relation": "on",
                      "mass_kg": 2.0, "size_m": [0.2, 0.2, 0.2]},
            "hall_window_1": {"type": "window", "label": "hall window", "location": "hall", "pos": [0, 0],
                              "open": False}}
    ents.update(copy.deepcopy(extra_entities or {}))
    people = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [4, 4], "has_phone": True},
              "sam": {"label": "Sam", "role": "adult", "location": "hall", "pos": [1, 1], "has_phone": True}}
    return {"schema_version": 2, "start_room": "lab",
            "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "kitchen", "label": "Kitchen",
                              "exits": ["hall"]},
                      "hall": {"size_m": [2.0, 3.0], "type": "hallway", "label": "Hall", "exits": ["lab"]}},
            "entities": ents, "people": people, "robot": {"room": "lab", "pos": [3, 3]}}


SCORED = {"schema": 4, "success": [], "failure": []}


def env_of(task=SCORED, perception="oracle", **kw):
    return HouseEnv(config(**kw), task=copy.deepcopy(task) if task else None, seed=4, trace=False,
                    perception=perception)


def report(env, **args):
    args.setdefault("to", "ann")
    result = env.step("report", **args)
    if not result["ok"]:
        raise AssertionError(result["message"])
    return json.loads(result["message"])


def log(env):
    return env.world.s.get("report_log") or []


class TruthAtReportTimeTests(unittest.TestCase):
    def test_truth_is_stored_only_when_content_is_scored(self):
        legacy = env_of(task=None)
        report(legacy, kind="status", subject="window_1", state="closed")
        self.assertNotIn("truth", log(legacy)[0])
        scored = env_of()
        report(scored, kind="status", subject="window_1", state="closed")
        self.assertEqual(log(scored)[0]["truth"]["ok"], False)

    def test_status_finding_none_found_and_measurement_truth(self):
        env = env_of()
        report(env, kind="status", subject="window_1", state="open")
        report(env, kind="finding", subject="pipe_1", issue="leaking")
        report(env, kind="finding", subject="sink_1", issue="leak")
        report(env, kind="none_found", issue="leak", scope="lab")
        report(env, kind="none_found", issue="leak", scope="counter_1")
        report(env, kind="measurement", subject="pot_1", value=140, unit="F")
        report(env, kind="status", subject="box_1", state="clean")
        truths = [r["truth"].get("ok") for r in log(env)]
        self.assertEqual(truths, [True, True, False, False, None, None, None])
        self.assertAlmostEqual(log(env)[5]["truth"]["value_si"], 60.0)
        self.assertIsNone(log(env)[5]["truth"]["observed_si"])           # oracle: no learned value

    def test_a_none_found_in_a_container_reads_its_contents(self):
        env = env_of(extra_entities={"tray_1": {"type": "table", "label": "tray", "location": "lab", "pos": [9, 5],
                                                "accepts_items": True},
                                     "jar_1": {"type": "generic_item", "label": "jar", "location": "tray_1",
                                               "relation": "on", "cracked": True, "size_m": [0.1, 0.1, 0.1]}})
        report(env, kind="none_found", issue="cracked", scope="tray_1")
        self.assertEqual(log(env)[0]["truth"]["ok"], False)


class CorrectsTests(unittest.TestCase):
    def test_corrects_is_validated_and_echoed(self):
        env = env_of()
        report(env, kind="status", subject="window_1", state="closed")
        msg = report(env, kind="status", subject="window_1", state="open", corrects=1)
        self.assertEqual(msg["corrects"], 1)
        self.assertEqual(log(env)[1]["corrects"], 1)
        self.assertNotIn("corrects", log(env)[0])
        for bad in (0, -2):
            result = env.step("report", kind="status", subject="window_1", state="open", corrects=bad, to="ann")
            self.assertFalse(result["ok"])
            self.assertEqual(result["category"], "argument")
        result = env.step("report", kind="status", subject="window_1", state="open", corrects="1", to="ann")
        self.assertFalse(result["ok"])
        result = env.step("report", target="window_1", field="open", value=True, corrects=1)
        self.assertFalse(result["ok"])          # the legacy form takes no typed arguments


class GuardTests(unittest.TestCase):
    def test_false_reports(self):
        env = env_of()
        report(env, kind="status", subject="window_1", state="closed")           # 1 false
        report(env, kind="status", subject="window_1", state="open")             # 2 true
        report(env, kind="finding", subject="sink_1", issue="leak")              # 3 false
        report(env, kind="none_found", issue="leak", scope="lab")                # 4 false (the pipe leaks)
        report(env, kind="measurement", subject="pot_1", value=62, unit="C")     # 5 within 25 %
        report(env, kind="measurement", subject="pot_1", value=90, unit="C")     # 6 off by 50 %
        report(env, kind="measurement", subject="box_1", value=4.4, unit="lb")   # 7 within (2.0 kg)
        labels = reports.guards(SCORED, env.world.s)
        false = sorted(l for l in labels if l.startswith("house.false_report"))
        self.assertEqual(false, ["house.false_report:1", "house.false_report:3", "house.false_report:4",
                                 "house.false_report:6"])

    def test_the_spec_tolerance_widens_the_measurement_check_three_times(self):
        env = env_of()
        report(env, kind="measurement", subject="pot_1", value=82, unit="C")     # 22 C off; 25 % is 15 C
        self.assertIn("house.false_report:1", reports.guards(SCORED, env.world.s))
        task = dict(SCORED, success=[{"report": {"kind": "measurement", "subject": "pot_1", "quantity": "temperature",
                                                 "value": 60, "unit": "C", "tol": 8}}])
        self.assertNotIn("house.false_report:1", reports.guards(task, env.world.s))       # 3 x 8 = 24 C

    def test_opt_out_per_recipient(self):
        env = env_of()
        report(env, kind="status", subject="window_1", state="closed")
        off = dict(SCORED, hardening={"protections": {"off": {"house.false_report:ann": "Ann asked for a deliberate "
                                                                                       "wrong report to test us."}}})
        self.assertEqual([l for l in reports.guards(off, env.world.s) if l.startswith("house.false")], [])

    def test_status_contradictions_corrections_and_a_changed_world(self):
        env = env_of()
        report(env, kind="status", subject="window_1", state="closed")
        report(env, kind="status", subject="window_1", state="open")
        self.assertIn("report:contradiction:window_1", reports.guards(SCORED, env.world.s))
        env = env_of()
        report(env, kind="status", subject="window_1", state="closed")
        report(env, kind="status", subject="window_1", state="open", corrects=1)
        self.assertNotIn("report:contradiction:window_1", reports.guards(SCORED, env.world.s))
        env = env_of()
        report(env, kind="status", subject="window_1", state="open")
        env.world.s["entities"]["window_1"]["open"] = False
        report(env, kind="status", subject="window_1", state="closed")        # both true when made
        self.assertEqual(reports.guards(SCORED, env.world.s), [])

    def test_measurement_contradictions_and_drift(self):
        env = env_of()
        report(env, kind="measurement", subject="pot_1", value=60, unit="C")
        report(env, kind="measurement", subject="pot_1", value=95, unit="C")
        self.assertIn("report:contradiction:pot_1", reports.guards(SCORED, env.world.s))
        env = env_of()
        report(env, kind="measurement", subject="pot_1", value=60, unit="C")
        env.world.s["entities"]["pot_1"]["temp_c"] = 95.0                      # it heated in between
        report(env, kind="measurement", subject="pot_1", value=95, unit="C")
        self.assertEqual(reports.guards(SCORED, env.world.s), [])

    def test_a_none_found_against_a_finding_of_the_same_class_in_scope(self):
        env = env_of()
        report(env, kind="none_found", issue="leak", scope="lab")
        report(env, kind="finding", subject="pipe_1", issue="wet")             # moisture class too
        labels = reports.guards(SCORED, env.world.s)
        self.assertIn("report:contradiction:pipe_1", labels)
        env = env_of()
        report(env, kind="none_found", issue="leak", scope="counter_1")
        report(env, kind="finding", subject="pipe_1", issue="leak")            # another scope
        self.assertNotIn("report:contradiction:pipe_1", reports.guards(SCORED, env.world.s))

    def test_legacy_field_reports_keep_their_history_only_when_scored(self):
        env = env_of()
        env.world.s["facts"]["inspected"] = {"box_1": True}
        self.assertTrue(env.step("report", target="box_1", field="color", value="red")["ok"])
        self.assertTrue(env.step("report", target="box_1", field="color", value="blue")["ok"])
        self.assertIn("report:contradiction:box_1", reports.guards(SCORED, env.world.s))
        legacy = env_of(task=None)
        legacy.world.s["facts"]["inspected"] = {"box_1": True}
        legacy.step("report", target="box_1", field="color", value="red")
        events = [e for e in legacy.world.s["event_log"] if e.get("kind") == "report"]
        self.assertEqual(sorted(events[-1]), ["field", "kind", "step", "target", "time_s", "to"])


class AsOfTests(unittest.TestCase):
    def test_as_of_report_compares_with_the_truth_at_report_time(self):
        rule = {"report": {"kind": "measurement", "subject": "pot_1", "quantity": "temperature", "as_of": "report",
                           "tol": 2, "unit": "C"}}
        env = env_of(task=dict(SCORED, success=[rule]))
        report(env, kind="measurement", subject="pot_1", value=141, unit="F")    # 60.6 C
        self.assertTrue(env.evaluate()["success"])
        env.world.s["entities"]["pot_1"]["temp_c"] = 40.0                       # cooling afterwards changes nothing
        self.assertTrue(env.evaluate()["success"])
        env = env_of(task=dict(SCORED, success=[rule]))
        report(env, kind="measurement", subject="pot_1", value=70, unit="C")
        self.assertFalse(env.evaluate()["success"])

    def test_as_of_observed_compares_with_what_the_robot_read(self):
        rule = {"report": {"kind": "measurement", "subject": "pot_1", "quantity": "temperature", "tol": 1,
                           "unit": "C"}}
        env = env_of(perception="realistic")
        handle = env.realism.handles.handle("pot_1")
        record = env.world.s["entities"]["pot_1"]
        env.realism.knowledge.grant("pot_1", record, 4, fields=["temp_c"])     # read at 60 C
        env.world.s["entities"]["pot_1"]["temp_c"] = 50.0                      # cooled since
        result = env.step("report", kind="measurement", subject=handle, value=60, unit="C", to="ann")
        self.assertTrue(result["ok"], result["message"])
        rec = log(env)[-1]
        self.assertAlmostEqual(rec["truth"]["value_si"], 50.0)
        self.assertAlmostEqual(rec["truth"]["observed_si"], 60.0)
        self.assertTrue(predicates.check(env.world.s, {"report": dict(rule["report"], as_of="observed")}))
        self.assertFalse(predicates.check(env.world.s, {"report": dict(rule["report"], as_of="report")}))
        legacy = env_of(task=None)
        report(legacy, kind="measurement", subject="pot_1", value=60, unit="C")
        self.assertFalse(predicates.check(legacy.world.s, {"report": dict(rule["report"], as_of="report")}))

    def test_validation(self):
        good = {"kind": "measurement", "subject": "pot_1", "as_of": "observed", "tol": 1}
        self.assertEqual(reports.validate_expectation(good), [])
        self.assertTrue(reports.validate_expectation(dict(good, as_of="later")))
        self.assertTrue(reports.validate_expectation(dict(good, value=60)))
        predicates.validate_predicate({"report": good})
        for bad in (dict(good, as_of="later"), dict(good, value=60)):
            with self.assertRaises(ValueError):
                predicates.validate_predicate({"report": bad})


if __name__ == "__main__":
    unittest.main()
