"""Typed reports to people (PEOPLE-IO, kernel v3.1; docs/design/TIER1_SWEEP.md 5 and 16; docs/DIALOGUE.md).

What this file pins:

* every kind (finding, measurement, status, count, none_found), list subjects (one record per subject), the
  report_log record, the message (how the report was understood and delivered, never whether it is right);
* the vocabulary: issue and state synonyms, severities and their aliases, unknown words kept as issue_raw /
  state_raw with stats.reports_unclassified and suggestions -- a static list per perceived category, identical for
  every member of a look-alike group;
* units: conversions to the canonical unit (F to C, lb to kg, gal to l), tolerances in the expectation's unit, the
  household's display units (format_quantity);
* preconditions: a subject the robot has not perceived is refused; to + channel reachability (in_person, phone,
  text, app, intercom, note, household) with the fixed wording; delivery records (heard; a text is read later);
* the legacy target / field / value form: its store and messages unchanged, no report_log, seen by match() as a
  "field" record; domains' report hooks see no legacy arguments for a typed report;
* match / match_set (the report / report_set predicate forms) with every key, the precision / recall edge cases
  and scopes;
* perceivability of scored findings (evidence fields at their tier, notices; sweep 5.5), and the verifier's use of
  it on the records a spec's report / report_set success predicates score (stage-C merge; matching_records,
  matching_set_records);
* the report budget (1 ms; the test fails at 3x).

Hand-built houses with core types only: the file passes with HOUSE_SIM_DOMAINS=core (the domain-hook test skips
without the food domain).
"""
import copy
import json
import os
import random
import time
import unittest

from house_sim.kernel import plugins, reports
from house_sim.kernel.actions import Ctx
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, HOOKS

COLS, ROWS = 12, 8


def config(entities=None, people=None, extra=None):
    """A lit kitchen ("lab") with a sink and a leaking pipe under it, a shelf with two look-alike jars, two towels in a
    closet, a sticky note on the counter; Ann (adult, has a phone) in the lab, Sam (adult, has a phone) and Dee (deaf)
    in the hall; an intercom station in each room."""
    ents = {"light_1": {"type": "lamp", "location": "lab", "pos": [COLS - 1, 0], "on": True},
            "counter_1": {"type": "table", "label": "counter", "location": "lab", "pos": [2, 1], "accepts_items": True},
            "pipe_1": {"type": "generic_item", "label": "pipe", "location": "lab", "pos": [5, 1], "category": "pipe",
                       "leaking": True, "size_m": [0.6, 0.1, 0.1], "portable": False},
            "shelf_1": {"type": "table", "label": "shelf", "location": "lab", "pos": [8, 1], "accepts_items": True},
            "jar_1": {"type": "generic_item", "label": "salt jar", "location": "shelf_1", "relation": "on",
                      "looks_like": "white_powder_jar", "category": "spice", "label_text": "SALT",
                      "size_m": [0.08, 0.08, 0.12]},
            "jar_2": {"type": "generic_item", "label": "sugar jar", "location": "shelf_1", "relation": "on",
                      "looks_like": "white_powder_jar", "category": "baking", "label_text": "SUGAR",
                      "size_m": [0.08, 0.08, 0.12]},
            "closet_1": {"type": "cabinet", "label": "closet", "location": "lab", "pos": [10, 6], "accepts_items": True,
                         "openable": True, "open": True},
            "towel_1": {"type": "generic_item", "label": "towel", "location": "closet_1", "relation": "in",
                        "size_m": [0.3, 0.3, 0.05]},
            "towel_2": {"type": "generic_item", "label": "towel", "location": "closet_1", "relation": "in",
                        "size_m": [0.3, 0.3, 0.05]},
            "note_1": {"type": "generic_item", "label": "sticky note", "category": "sticky_note", "writable": True,
                       "location": "counter_1", "relation": "on", "size_m": [0.08, 0.08, 0.001]},
            "intercom_1": {"type": "generic_item", "label": "intercom", "category": "intercom", "location": "lab",
                           "pos": [0, 4], "portable": False, "size_m": [0.15, 0.05, 0.2]},
            "intercom_2": {"type": "generic_item", "label": "intercom", "category": "intercom", "location": "hall",
                           "pos": [0, 1], "portable": False, "size_m": [0.15, 0.05, 0.2]},
            "lamp_2": {"type": "lamp", "location": "attic", "pos": [1, 1]}}
    ents.update(copy.deepcopy(entities or {}))
    if people is None:
        people = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [4, 4], "has_phone": True},
                  "sam": {"label": "Sam", "role": "adult", "location": "hall", "pos": [1, 2], "has_phone": True,
                          "ack_delay_s": 120},
                  "dee": {"label": "Dee", "role": "adult", "location": "hall", "pos": [1, 1],
                          "abilities": {"hearing": "deaf"}}}
    cfg = {"schema_version": 2, "start_room": "lab",
           "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "kitchen", "label": "Kitchen",
                             "exits": ["hall"]},
                     "hall": {"size_m": [2.0, 3.0], "type": "hallway", "label": "Hall", "exits": ["lab", "attic"]},
                     "attic": {"size_m": [2.0, 2.0], "type": "attic", "label": "Attic", "exits": ["hall"]}},
           "entities": ents, "people": copy.deepcopy(people), "robot": {"room": "lab", "pos": [3, 3]}}
    for key, value in (extra or {}).items():
        cfg[key] = copy.deepcopy(value)
    return cfg


def env_of(perception="oracle", **kw):
    return HouseEnv(config(**kw), seed=5, perception=perception, trace=False)


def report(env, **args):
    result = env.step("report", **args)
    return result, (json.loads(result["message"]) if result["ok"] else None)


def log(env):
    return env.world.s.get("report_log") or []


class KindTests(unittest.TestCase):
    def test_a_finding_with_synonyms_severity_and_delivery(self):
        env = env_of()
        result, msg = report(env, kind="finding", subject="pipe_1", issue="dripping", severity="high", to="ann")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual((msg["reported"], msg["issue"], msg["severity"], msg["channel"], msg["to"]),
                         ("finding", "leak", "major", "in_person", ["ann"]))
        rec = log(env)[0]
        for key in ("n", "t", "kind", "subject", "issue", "issue_raw", "severity", "quantity", "value_si", "value",
                    "unit", "state", "of", "scope", "location", "to", "channel", "delivered", "detail"):
            self.assertIn(key, rec)
        self.assertEqual(rec["subject"], {"kind": "entity", "id": "pipe_1"})
        self.assertEqual((rec["location"], rec["n"]), ("lab", 1))
        self.assertTrue(rec["delivered"]["ann"]["heard"])
        self.assertNotIn("correct", json.dumps(msg))

    def test_a_measurement_is_stored_in_canonical_units(self):
        env = env_of()
        result, msg = report(env, kind="measurement", subject="pipe_1", value=72, unit="Fahrenheit")
        rec = log(env)[0]
        self.assertEqual((rec["quantity"], rec["unit"], rec["value"]), ("temperature", "F", 72))
        self.assertAlmostEqual(rec["value_si"], (72 - 32) * 5 / 9, places=4)
        self.assertFalse(env.step("report", kind="measurement", subject="pipe_1", value=3, unit="furlong")["ok"])
        self.assertFalse(env.step("report", kind="measurement", subject="pipe_1", value="hot", unit="C")["ok"])

    def test_status_count_and_none_found(self):
        env = env_of()
        self.assertEqual(report(env, kind="status", subject="closet_1", state="ajar")[1]["state"], "open")
        msg = report(env, kind="count", of="towels", value=2, scope="closet_1")[1]
        self.assertEqual((msg["of"], msg["value"], msg["scope"]), ("towel", 2, "closet_1"))
        msg = report(env, kind="none_found", issue="pests", scope="lab")[1]
        self.assertEqual(msg["issue"], "pests")
        self.assertEqual(log(env)[-1]["location"], "lab")
        self.assertFalse(env.step("report", kind="none_found", issue="pests")["ok"])          # needs a scope
        self.assertFalse(env.step("report", kind="count", of="towel", value=-1, scope="closet_1")["ok"])

    def test_a_list_subject_is_one_record_per_subject(self):
        env = env_of()
        msg = report(env, kind="finding", subject=["jar_1", "jar_2", "jar_1"], issue="expired")[1]
        self.assertEqual(msg["records"], 2)
        self.assertEqual([r["subject"]["id"] for r in log(env)], ["jar_1", "jar_2"])
        self.assertEqual([r["n"] for r in log(env)], [1, 2])

    def test_unknown_words_are_kept_with_suggestions(self):
        env = env_of()
        msg = report(env, kind="finding", subject="pipe_1", issue="zorbled", severity="spicy")[1]
        self.assertIn("unclassified", msg)
        self.assertIn("severity_unclassified", msg)
        self.assertEqual(msg["suggest"], reports.issue_words("pipe")[:reports.SUGGESTIONS])
        rec = log(env)[0]
        self.assertEqual((rec["issue"], rec["issue_raw"], rec["severity"]), (None, "zorbled", None))
        self.assertEqual(env.world.s["stats"]["reports_unclassified"], 1)
        msg = report(env, kind="status", subject="closet_1", state="frobbed")[1]
        self.assertEqual(msg["suggest"], reports.state_words()[:reports.SUGGESTIONS])

    def test_suggestions_are_the_same_for_every_member_of_a_look_alike_group(self):
        env = env_of("realistic")
        h1, h2 = (env.realism.handles.handle(e) for e in ("jar_1", "jar_2"))
        env.step("look")
        one = report(env, kind="finding", subject=h1, issue="weird")[1]["suggest"]
        two = report(env, kind="finding", subject=h2, issue="weird")[1]["suggest"]
        self.assertEqual(one, two)
        self.assertEqual(one, reports.issue_words("object")[:reports.SUGGESTIONS])
        oracle = env_of()
        self.assertNotEqual(report(oracle, kind="finding", subject="jar_1", issue="weird")[1]["suggest"],
                            report(oracle, kind="finding", subject="pipe_1", issue="weird")[1]["suggest"])

    def test_malformed_reports_are_refused(self):
        env = env_of()
        for args, text in (({"kind": "finding", "subject": "pipe_1"}, "needs an issue"),
                           ({"kind": "finding", "issue": "leak"}, "needs a subject"),
                           ({"kind": "status", "subject": "closet_1"}, "needs a state"),
                           ({"kind": "finding", "subject": "pipe_1", "issue": "leak", "target": "pipe_1"}, "Use either"),
                           ({"kind": "finding", "subject": "pipe_1", "issue": "leak", "to": "nobody_x"}, "recipient")):
            result = env.step("report", **args)
            self.assertFalse(result["ok"], args)
            self.assertIn(text, result["message"], args)
            self.assertEqual(result["category"], "argument")

    def test_a_subject_the_robot_has_not_seen_is_refused(self):
        env = env_of()
        result = env.step("report", kind="finding", subject="lamp_2", issue="burnt out")
        self.assertEqual((result["ok"], result["message"]), (False, "You have not seen that"))
        self.assertEqual(env.step("report", kind="finding", subject="attic", issue="dusty")["message"],
                         "You have not seen that")
        self.assertTrue(env.step("report", kind="finding", subject="house", issue="dusty")["ok"])
        self.assertTrue(env.step("report", kind="finding", subject="ann", issue="injured")["ok"])


class DeliveryTests(unittest.TestCase):
    def test_in_person_needs_them_here_unless_a_channel_is_named(self):
        env = env_of()
        result = env.step("report", kind="finding", subject="pipe_1", issue="leak", to="sam")
        self.assertEqual((result["ok"], result["message"]), (False, reports.NOT_HERE))
        result = env.step("report", kind="finding", subject="pipe_1", issue="leak", to="sam", channel="in_person")
        self.assertEqual(result["message"], "That person is not here")

    def test_phone_and_text_reach_a_phone_over_the_network(self):
        env = env_of()
        report(env, kind="finding", subject="pipe_1", issue="leak", to="sam", channel="phone")
        self.assertTrue(log(env)[-1]["delivered"]["sam"]["heard"])
        report(env, kind="finding", subject="pipe_1", issue="leak", to="sam", channel="text")
        entry = log(env)[-1]["delivered"]["sam"]
        self.assertFalse(entry["heard"])
        self.assertEqual(entry["read_s"], entry["t"] + 120)
        self.assertFalse(reports.match(env.world.s, {"kind": "finding", "to": "sam", "channel": "text", "heard": True}))
        env.step("wait", seconds=200)
        self.assertTrue(reports.match(env.world.s, {"kind": "finding", "to": "sam", "channel": "text", "heard": True}))
        self.assertEqual(env.step("report", kind="finding", subject="pipe_1", issue="leak", to="dee",
                                  channel="phone")["message"], "They cannot be reached by phone")
        env.world.s["robot"]["network"] = False
        self.assertFalse(env.step("report", kind="finding", subject="pipe_1", issue="leak", to="sam",
                                  channel="text")["ok"])

    def test_phone_text_and_app_reach_someone_away_from_home(self):
        """Stage-C fix: a phone, a text or the app reaches people wherever they are (location None while away);
        in person still needs them here; the text is read after their delay; both perception modes."""
        for mode in ("oracle", "realistic"):
            env = env_of(mode)
            env.world.begin()
            env.world.person_m("sam").update(location=None, away=True)
            env.world.commit()
            pipe = env.realism.handles.handle("pipe_1") if env.realism is not None else "pipe_1"
            for channel in ("phone", "text"):
                result, msg = report(env, kind="finding", subject=pipe, issue="leak", to="sam", channel=channel)
                self.assertTrue(result["ok"], (mode, channel, result["message"]))
            self.assertTrue(log(env)[-2]["delivered"]["sam"]["heard"])            # the call
            self.assertFalse(log(env)[-1]["delivered"]["sam"]["heard"])           # the text: read later
            env.step("wait", seconds=200)
            self.assertTrue(reports.match(env.world.s, {"kind": "finding", "to": "sam", "channel": "text",
                                                        "heard": True}))
            report(env, kind="status", subject=pipe, state="open", channel="app")
            self.assertEqual(sorted(log(env)[-1]["delivered"]), ["ann", "sam"])
            self.assertFalse(env.step("report", kind="finding", subject=pipe, issue="leak", to="sam")["ok"])

    def test_the_app_reaches_everyone_with_a_phone(self):
        env = env_of()
        msg = report(env, kind="status", subject="closet_1", state="open", channel="app")[1]
        self.assertEqual(sorted(log(env)[-1]["delivered"]), ["ann", "sam"])
        self.assertEqual(msg["records"], 1)

    def test_a_deaf_person_does_not_hear_but_the_intercom_needs_stations(self):
        env = env_of()
        report(env, kind="finding", subject="pipe_1", issue="leak", to="dee", channel="intercom")
        self.assertFalse(log(env)[-1]["delivered"]["dee"]["heard"])
        report(env, kind="finding", subject="pipe_1", issue="leak", to="sam", channel="intercom")
        self.assertTrue(log(env)[-1]["delivered"]["sam"]["heard"])
        bare = env_of(entities={})
        del bare.world.s["entities"]["intercom_2"]
        self.assertEqual(bare.step("report", kind="finding", subject="pipe_1", issue="leak", to="sam",
                                   channel="intercom")["message"], "There is no intercom route to them")

    def test_a_note_is_written_on_what_the_robot_holds(self):
        env = env_of()
        self.assertFalse(env.step("report", kind="finding", subject="pipe_1", issue="leak", to="sam",
                                  channel="note")["ok"])
        env.step("approach", "note_1")
        self.assertTrue(env.step("pick_up", "note_1")["ok"])
        msg = report(env, kind="finding", subject="pipe_1", issue="leak", severity="minor", to="sam", channel="note")[1]
        self.assertEqual(msg["written_on"], "note_1")
        self.assertEqual(env.world.s["entities"]["note_1"]["text"], "Leak: pipe (minor)")
        self.assertFalse(log(env)[-1]["delivered"]["sam"]["heard"])
        report(env, kind="finding", subject="pipe_1", issue="leak", to="sam", channel="note")
        self.assertEqual(env.world.s["entities"]["note_1"]["text"], "Leak: pipe")     # a sticky note holds one note

    def test_a_whiteboard_keeps_what_was_on_it(self):
        """Adoption-round request (people K-NOTE-APPEND): a note written on a whiteboard replaced everything on it
        (the household's calendar block); a board now gets the note on a new line below."""
        board = {"board_1": {"type": "generic_item", "label": "whiteboard", "category": "whiteboard", "location": "lab",
                             "pos": [3, 2], "portable": False, "size_m": [0.9, 0.02, 0.6], "mount_height_m": 1.0,
                             "text": "MON: swim 5pm\nTUE: dentist"}}
        env = env_of(entities=board)
        result, msg = report(env, kind="finding", subject="pipe_1", issue="leak", to="sam", channel="note")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(msg["written_on"], "board_1")
        self.assertEqual(env.world.s["entities"]["board_1"]["text"], "MON: swim 5pm\nTUE: dentist\nLeak: pipe")
        env.world.s["entities"]["board_1"]["text"] = ""
        report(env, kind="finding", subject="pipe_1", issue="leak", to="sam", channel="note")
        self.assertEqual(env.world.s["entities"]["board_1"]["text"], "Leak: pipe")

    def test_household_reports_are_logged_for_nobody(self):
        env = env_of()
        msg = report(env, kind="finding", subject="pipe_1", issue="leak", to="household")[1]
        self.assertEqual(log(env)[-1]["delivered"], {})
        self.assertIsNone(log(env)[-1]["channel"])
        self.assertEqual(msg["to"], ["household"])
        self.assertTrue(reports.match(env.world.s, {"to": "household", "issue": "leak"}))

    def test_the_people_hook_decides_who_heard(self):
        saved = list(HOOKS.get("report_delivered", []))
        HOOKS["report_delivered"] = [(0, "t.h", lambda w, rec, pid, ch: {"heard": False, "how": "busy"})]
        try:
            env = env_of()
            report(env, kind="finding", subject="pipe_1", issue="leak", to="ann")
            self.assertEqual(log(env)[-1]["delivered"]["ann"]["heard"], False)
            self.assertEqual(log(env)[-1]["delivered"]["ann"]["how"], "busy")
        finally:
            HOOKS["report_delivered"] = saved


class LegacyTests(unittest.TestCase):
    def test_the_legacy_form_is_unchanged(self):
        env = env_of()
        self.assertEqual(env.step("report", "pipe_1", field="leaking", value=True)["message"],
                         "Inspect it before reporting")
        env.step("approach", "pipe_1")
        env.step("inspect", "pipe_1")
        result = env.step("report", "pipe_1", field="leaking", value=True, to="ann")
        self.assertEqual(result["message"], "Reported pipe_1.leaking")
        self.assertEqual(env.world.s["reports"]["pipe_1"], {"leaking": True, "_to": {"leaking": "ann"}})
        self.assertNotIn("report_log", env.world.s)
        self.assertEqual(env.step("report", "pipe_1", value=True)["message"], "report requires field")
        self.assertEqual(env.step("report", field="x", value=True)["message"], "report requires target")
        self.assertEqual(env.step("report", "pipe_1", field="x")["message"], "report requires value")
        self.assertIn("Give kind", env.step("report", "pipe_1", field="x", value=1, issue="leak")["message"])
        records = reports.records_of(env.world.s)
        self.assertEqual((records[0]["kind"], records[0]["field"], records[0]["value"], records[0]["to"]),
                         ("field", "leaking", True, ["ann"]))
        self.assertTrue(reports.match(env.world.s, {"kind": "field", "subject": "pipe_1", "field": "leaking",
                                                    "value": True, "to": "ann"}))

    def test_domain_report_hooks_ignore_typed_reports(self):
        plugins.load_domains()
        env = env_of()
        before = copy.deepcopy(env.world.s["entities"]["pipe_1"])
        self.assertTrue(env.step("report", kind="measurement", subject="pipe_1", value=40, unit="C")["ok"])
        after = env.world.s["entities"]["pipe_1"]
        self.assertEqual({k: v for k, v in after.items() if not k.startswith("_")},
                         {k: v for k, v in before.items() if not k.startswith("_")})


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.env = env_of()
        report(self.env, kind="finding", subject="pipe_1", issue="leak", severity="moderate", to="ann")
        self.env.step("wait", seconds=100)
        report(self.env, kind="measurement", subject="pipe_1", value=50, unit="F")
        report(self.env, kind="count", of="towel", value=2, scope="closet_1")
        report(self.env, kind="finding", subject=["jar_1", "jar_2"], issue="expired", location="lab")
        self.state = self.env.world.s

    def test_every_key(self):
        m = lambda exp, **kw: reports.match(self.state, exp, **kw)   # noqa: E731
        self.assertTrue(m({"kind": "finding", "subject": "pipe_1", "issue": ["leaking"]}))
        self.assertTrue(m({"subject": ["jar_2", "towel_1"], "issue": "out of date"}))
        self.assertFalse(m({"subject": "pipe_1", "issue": "broken"}))
        self.assertTrue(m({"issue": "leak", "severity_at_least": "minor"}))
        self.assertFalse(m({"issue": "leak", "severity_at_least": "major"}))
        self.assertTrue(m({"issue": "leak", "severity": "high"}))                 # +-1 rank
        self.assertFalse(m({"issue": "leak", "severity": "critical"}))
        self.assertTrue(m({"issue": "leak", "severity": "moderate", "severity_tol": 0}))
        self.assertTrue(m({"issue": "leak", "room": "lab", "to": "ann", "heard": True, "channel": "in_person"}))
        self.assertFalse(m({"issue": "leak", "to": "sam"}))
        self.assertTrue(m({"issue": "leak", "before_s": 50}))
        self.assertFalse(m({"issue": "expired", "before_s": 50}))
        self.assertTrue(m({"issue": "expired", "after": 100}))
        self.assertTrue(m({"issue": "expired", "after": "spill_seen", "before_s": 60}, times={"spill_seen": 90.0}))
        self.assertFalse(m({"issue": "expired", "after": "never_reached"}))
        self.assertTrue(m({"kind": "measurement", "quantity": "temperature", "value": 10, "unit": "C", "tol": 0.1}))
        self.assertTrue(m({"kind": "measurement", "value": 283.15, "unit": "K", "tol": 0.1}))
        self.assertTrue(m({"kind": "measurement", "value": 51, "unit": "F", "tol": 1}))
        self.assertFalse(m({"kind": "measurement", "value": 52, "unit": "F", "tol": 1}))
        self.assertTrue(m({"kind": "count", "of": "towels", "value": 2, "scope": "closet_1"}))
        self.assertTrue(m({"kind": "count", "of": "towel", "value": 3, "tol": 1}))
        self.assertFalse(m({"kind": "count", "of": "towel", "value": 3}))
        self.assertEqual(reports.validate_expectation({"kind": "rumour", "bogus": 1}),
                         ["unknown report keys: bogus", "unknown report kind 'rumour'"])

    def test_match_set_precision_and_recall(self):
        p, r, d = reports.match_set(self.state, {"issue": "expired", "expected": ["jar_1", "jar_2"]})
        self.assertEqual((p, r), (1.0, 1.0))
        p, r, d = reports.match_set(self.state, {"issue": "expired", "expected": ["jar_1", "towel_1"]})
        self.assertEqual((p, r, d["false"], d["missed"]), (0.5, 0.5, ["jar_2"], ["towel_1"]))
        self.assertEqual(reports.match_set(self.state, {"issue": "wilted", "expected": []})[:2], (1.0, 1.0))
        self.assertEqual(reports.match_set(self.state, {"issue": "wilted", "expected": ["jar_1"]})[:2], (0.0, 0.0))
        self.assertEqual(reports.match_set(self.state, {"issue": "expired", "expected": []})[:2], (0.0, 1.0))
        p, r, _ = reports.match_set(self.state, {"issue": "expired", "expected": ["jar_1", "jar_2"],
                                                  "scope": {"rooms": ["hall"]}})
        self.assertEqual((p, r), (0.0, 0.0))
        p, r, _ = reports.match_set(self.state, {"issue": "expired", "expected": ["jar_1", "jar_2"],
                                                  "scope": {"within": "shelf_1"}})
        self.assertEqual((p, r), (1.0, 1.0))


class UnitTests(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(reports.convert(212, "F", "temperature"), 100.0, places=4)
        self.assertAlmostEqual(reports.convert(0, "K", "temperature"), -273.15)
        self.assertAlmostEqual(reports.convert(10, "lb", "mass"), 4.5359237)
        self.assertAlmostEqual(reports.convert(1, "gallons", None), 3.785411784)
        self.assertAlmostEqual(reports.convert(30, "cm", "length"), 0.3)
        self.assertAlmostEqual(reports.from_canonical(100.0, "F", "temperature"), 212.0, places=3)
        with self.assertRaises(ValueError):
            reports.convert(1, "%", None)                       # percent or humidity: ambiguous without a quantity
        self.assertAlmostEqual(reports.convert(40, "%", "humidity"), 40.0)

    def test_the_household_reads_its_own_units(self):
        us = {"metadata": {"region": "us", "units": "imperial"}}
        eu = {"metadata": {"region": "eu", "units": "metric"}}
        self.assertEqual(reports.format_quantity(us, 100.0, "temperature"), "212 F")
        self.assertEqual(reports.format_quantity(eu, 21.55, "temperature"), "21.6 C")
        self.assertEqual(reports.format_quantity(us, 1.0, "mass"), "2.2 lb")
        self.assertEqual(reports.format_quantity(eu, 0.25, "volume"), "250 ml")
        self.assertEqual(reports.format_quantity({}, 3, "count"), "3")

    def test_the_domains_report_words_load_after_the_kernels(self):
        """The adoption round's report vocabulary (data/vocab/<domain>.json, one file per domain, all kernel-owned):
        core.json loads first, every domain file loads without a clash, and the domains' words resolve; a word core
        already has keeps core's meaning (clogged's 'slow drain', paused's 'on hold')."""
        from house_sim.kernel import dialogue
        vocab = dialogue.vocabulary()
        self.assertEqual(vocab["sources"][0], "core.json")
        domains = {"admin_inventory", "appliances_cooking", "cleaning", "climate", "electrical", "food",
                   "manipulation", "mechanisms", "navigation_robot", "outdoor_plants", "pets", "plumbing_liquids",
                   "safety", "textiles_laundry", "tools_vehicles"}
        self.assertTrue({d + ".json" for d in domains} <= set(vocab["sources"]))
        self.assertEqual(vocab["_index"]["problems"], [])
        for word, issue in (("freezer burnt", "freezer_burn"), ("paper jam", "paper_jam"), ("vomiting", "vomiting"),
                            ("running toilet", "running_toilet"), ("blocked vent", "blocked_vent"),
                            ("pilled", "pilled"), ("stripped screw", "stripped"), ("yellow leaves", "yellowing"),
                            ("overloaded circuit", "overloaded"), ("over capacity", "overloaded"),
                            ("slow drain", "clogged")):
            self.assertEqual(reports.issue_of(word), issue, word)
        for word, state in (("child lock on", "child_locked"), ("preheated", "preheated"), ("on hold", "paused"),
                            ("burner lit", "lit"), ("glowing", "lit")):
            self.assertEqual(reports.state_of(word), state, word)

    def test_issue_words_by_class_and_category(self):
        self.assertIn("leak", reports.issue_words("moisture"))
        self.assertNotIn("leak", reports.issue_words("living"))
        plant = reports.issue_words("plant")
        self.assertEqual(plant[0], "pests")
        self.assertIn("needs_water", plant)
        self.assertNotIn("injured", plant)                       # applies to people and pets
        self.assertEqual(reports.issue_words("no such category"), reports.issue_words("default"))


class PerceivabilityTests(unittest.TestCase):
    def test_findings_need_perceived_evidence(self):
        env = env_of("realistic")
        h = env.realism.handles.handle("pipe_1")
        env.step("look")
        report(env, kind="finding", subject=h, issue="leak")
        report(env, kind="finding", subject=h, issue="broken")
        records = log(env)
        gaps = reports.perceivability_gaps(env, records)
        self.assertEqual(len(gaps), 1)
        self.assertIn("broken", gaps[0])
        self.assertTrue(reports.evidence_perceived(env, {"kind": "finding", "issue": "beeping",
                                                         "subject": {"kind": "entity", "id": "pipe_1"}},
                                                   notices=["alarm"]))
        self.assertFalse(reports.evidence_perceived(env, {"kind": "measurement", "quantity": "temperature",
                                                          "subject": {"kind": "entity", "id": "pipe_1"}}))

    def test_the_verifier_checks_the_reports_its_success_predicates_score(self):
        """Stage-C merge: tasks/verify.py (TIME-EVAL) now runs this check (sweep 5.5) on the records the spec's report
        / report_set success predicates score -- top level and per phase, not under "not" -- with the fields the
        reference run's observations showed (things, people and pets) as evidence. Before, nothing called it."""
        from house_sim.tasks import verify
        env = env_of("realistic")
        env.realism.record_observations = True
        h = env.realism.handles.handle("pipe_1")
        env.step("look")
        report(env, kind="finding", subject=h, issue="leak")
        report(env, kind="finding", subject=h, issue="broken")
        report(env, kind="status", subject="ann", state="awake", to="ann")
        gaps = lambda success: verify.report_perceivability_gaps(env, {"success": success})   # noqa: E731
        self.assertEqual(gaps([{"report": {"kind": "finding", "subject": "pipe_1", "issue": "leak"}}]), [])
        broken = {"report": {"kind": "finding", "subject": "pipe_1", "issue": "broken"}}
        self.assertEqual(len(gaps([broken])), 1)
        self.assertIn("broken", gaps([broken])[0]["report"])
        self.assertEqual(gaps([{"not": broken}]), [])                     # a forbidden report is no claim to check
        self.assertEqual(len(gaps([{"report_set": {"issue": "broken", "expected": ["pipe_1"]}}])), 1)
        self.assertEqual(gaps([{"report_set": {"issue": "broken", "expected": []}}]), [])   # nothing scored right
        # a person's state: the observation showed Ann's posture (the "awake" evidence)
        self.assertEqual(gaps([{"report": {"kind": "status", "subject": "ann", "state": "awake"}}]), [])
        self.assertEqual(verify.report_perceivability_gaps(env, {"phases": [{"id": "p1", "success": [broken]}]}),
                         gaps([broken]))
        self.assertEqual(verify.success_rules({"success": [1], "phases": [{"success": [2]}, {}]}), [1, 2])

    def test_matching_records(self):
        env = env_of()
        report(env, kind="finding", subject=["jar_1", "jar_2"], issue="expired")
        report(env, kind="finding", subject="pipe_1", issue="leak")
        state = env.world.s
        self.assertEqual([r["subject"]["id"] for r in reports.matching_records(state, {"issue": "expired"})],
                         ["jar_1", "jar_2"])
        self.assertEqual(reports.matching_records(state, "expired"), [])
        found = reports.matching_set_records(state, {"issue": ["expired", "leak"], "expected": ["jar_2", "pipe_1"]})
        self.assertEqual([r["subject"]["id"] for r in found], ["jar_2", "pipe_1"])


class BudgetTests(unittest.TestCase):
    def test_report_stays_within_budget(self):
        env = env_of()
        args = {"kind": "finding", "subject": ["jar_1", "jar_2", "pipe_1"], "issue": "dripping", "severity": "high",
                "to": "ann"}
        runs, total = 30, 0.0
        if os.environ.get("HOUSE_SIM_PERF_TESTS", "1") == "0":
            self.skipTest("wall-clock bound off (HOUSE_SIM_PERF_TESTS=0: CI runs the bounds alone)")
        for _ in range(runs):
            env.world.begin()
            ctx = Ctx(env, ACTIONS["report"], ACTIONS["report"].validate(dict(args)), random.Random(0))
            start = time.perf_counter()
            reports.record(ctx, reports.normalize(ctx.args, env.world.s))
            total += time.perf_counter() - start
            env.world.rollback()
        self.assertLess(total / runs, 0.003)


if __name__ == "__main__":
    unittest.main()
