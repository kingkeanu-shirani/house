"""Schema 4 and the lints (docs/design/VERIFY_HARDENING.md 5.4, 7, 8.6, 9.1, 13.2-13.8, 14, 15.4, 17, 18, 20; WP8:
house_sim/tasks/spec.py, house_sim/tasks/lint.py, tools/protect_map.py, house_sim/data/verify/protect_rules.json,
house_sim/data/verify/escapes.json, tools/show_entries.py, catalog/review/legacy_ids.json).

* spec.py: schema 4 is accepted; a catalog id outside the frozen legacy list must declare it (L-SCHEMA); the
  schema-4 keys pass expand and are validated through the WP1 / WP3 / WP4 validators (envelope, protections,
  timing in its strict form, said / report bodies); envelope merges like side_effects, +timing appends; wrong-plan
  fields, labelled predicates, variant keys; the schema-3 camera rule is unchanged; every authored spec stays clean;
* lint.py: every lint of 20.2 with a case that fires and one that does not, the variant collapse, the legacy pass
  (L-SCHEMA only), the as-if pass, determinism, the dynamic lints on real builds (the same facts whatever the spec's
  declared perception and camera);
* the protect classifier: every rule compiles and every reference it proposes resolves; coverage over the 6,355
  analysis items is above the design's 72.9 %; the review's cases map as 7.2 shows;
* the price list matches section 18 and the verifier's keys; the tools print.
"""
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sim.kernel.errors import SpecError
from house_sim.kernel.plugins import load_domains
from house_sim.tasks import escapes, lint
from house_sim.tasks import spec as spec_mod
from house_sim.tasks.runner import build

ROOT = Path(__file__).resolve().parents[1]
NEW_ID = "T-c70376d22cdba2-01"          # a catalog entry with no authored spec (not a legacy id)
LEGACY_ID = "T-d990c58b123a5e-01"       # an authored (legacy) spec: "Check HVAC vents."
SETUP = [{"op": "room", "var": "living", "type": "living_room", "pick": "first"},
         {"op": "lights", "room": "{living}", "on": True},
         {"op": "spawn", "type": "bookshelf", "furniture": "{living}", "var": "shelf"},
         {"op": "spawn", "type": "coffee_table", "furniture": "{living}", "var": "table"},
         {"op": "spawn", "type": "book", "on": "{table}", "var": "book"},
         {"op": "robot", "room": "{living}"},
         {"op": "prior_knowledge", "map": "all"}]


def wrong(name, expect, mistake=(1,)):
    return {"name": name, "plan": [{"skill": "fetch", "obj": "{book}"}, {"do": "wait", "seconds": 30}],
            "expect": list(expect), "mistake": list(mistake)}


def book_spec(**changes):
    """A small, lint-clean schema-4 spec (a fake catalog id: no analysis record, no catalog entry)."""
    out = {"catalog_id": "T-wp8-book-01", "title": "shelve the book", "schema": 4, "risk_level": 0,
           "house": {"residents": "minimal", "pets": "none"}, "setup": copy.deepcopy(SETUP),
           "instruction": "Put the book on the {shelf}.",
           "phrasings": ["Put the book on the {shelf}.", "Shelve the book on the {shelf}, please.",
                         "The book goes on the {shelf}."],
           "success": [{"label": "book_shelved", "pred": {"path": "entities.{book}.location", "op": "eq",
                                                           "value": "{shelf}"}}],
           "envelope": {"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]}}},
           "budget": {"steps": 40, "time_s": 600},
           "reference": [{"skill": "fetch", "obj": "{book}"}, {"skill": "put", "obj": "{book}", "dest": "{shelf}"}],
           "wrong": [wrong("puts the book back on the table", ["goal:book_shelved"]),
                     wrong("holds the book and waits", ["goal:book_shelved"])]}
    out.update(changes)
    return out


def ids_of(problems, severity=None):
    return sorted({p["id"] for p in problems if severity is None or p["severity"] == severity})


def messages(problems, lint_id):
    return [p["message"] for p in problems if p["id"] == lint_id]


class SchemaTests(unittest.TestCase):
    def test_schema_4_is_accepted_and_new_specs_must_declare_it(self):
        self.assertIn(4, spec_mod.SCHEMAS)
        self.assertEqual(spec_mod.NEW_SPEC_SCHEMA, 4)
        base = book_spec(catalog_id=NEW_ID)
        self.assertEqual(spec_mod.schema_problems(base), [])
        legacy_form = dict(base)
        legacy_form.pop("schema")
        self.assertTrue(any(p.startswith("L-SCHEMA") for p in spec_mod.schema_problems(legacy_form)))
        self.assertTrue(any(p.startswith("L-SCHEMA") for p in spec_mod.check_structure(dict(legacy_form, schema=3))))
        self.assertEqual(ids_of(lint.run(legacy_form)), ["L-SCHEMA"])
        # a legacy id and an id that is no catalog entry (tests, custom tasks) keep their schema
        self.assertEqual(spec_mod.schema_problems(dict(legacy_form, catalog_id=LEGACY_ID)), [])
        self.assertEqual(spec_mod.schema_problems(dict(legacy_form, catalog_id="T-wp8-book-01")), [])
        self.assertEqual(spec_mod.schema_problems(dict(base, schema=5)), ["schema must be 2, 3 or 4"])
        self.assertEqual(spec_mod.schema_problems(dict(base, schema=True, catalog_id="T-wp8-book-01")),
                         ["schema must be 2, 3 or 4"])

    def test_the_legacy_list_is_the_243_authored_ids(self):
        data = json.loads((ROOT / "catalog" / "review" / "legacy_ids.json").read_text(encoding="utf-8"))
        authored = set()
        for path in sorted((ROOT / "tasks" / "authored").glob("*.json")):
            authored |= {s["catalog_id"] for s in json.loads(path.read_text(encoding="utf-8"))["specs"]}
        self.assertEqual(data["count"], 243)
        self.assertEqual(len(data["ids"]), 243)
        self.assertEqual(data["ids"], sorted(data["ids"]))
        self.assertTrue(authored <= set(data["ids"]), "an authored spec outside the frozen list must be schema 4: "
                        "{}".format(sorted(authored - set(data["ids"]))[:5]))
        self.assertEqual(spec_mod.legacy_ids(), frozenset(data["ids"]))

    def test_every_authored_spec_stays_clean(self):
        for path in sorted((ROOT / "tasks" / "authored").glob("*.json")):
            for spec in json.loads(path.read_text(encoding="utf-8"))["specs"]:
                with self.subTest(spec=spec["catalog_id"]):
                    self.assertEqual(spec_mod.check_structure(spec_mod.expand(spec)), [])
                    self.assertEqual(lint.run(spec, lint.analysis_record(spec["catalog_id"])), [])

    def test_the_schema_4_keys_expand_and_merge(self):
        self.assertTrue(set(spec_mod.HARDENING_KEYS) <= spec_mod.ALLOWED)
        self.assertIn("timing", spec_mod.TASK_KEYS)
        self.assertIn("timing", spec_mod.PHASE_KEYS)
        merged = spec_mod.merge({"envelope": {"work_rooms": ["{a}"], "move": {"{x}": {"to": ["baseline"]}}},
                                 "timing": [{"label": "a"}]},
                                {"envelope": {"consume": ["{soap}"]}, "+timing": [{"label": "b"}],
                                 "+battery_exempt": [{"policy": "B8.never"}]})
        self.assertEqual(merged["envelope"], {"work_rooms": ["{a}"], "move": {"{x}": {"to": ["baseline"]}},
                                              "consume": ["{soap}"]})
        self.assertEqual([t["label"] for t in merged["timing"]], ["a", "b"])
        self.assertEqual(merged["battery_exempt"], [{"policy": "B8.never"}])
        spec_mod.expand(book_spec(settle_s=45, answers={}, protect_map=[], instruction_roles={"shelf": "destination"},
                                  process=[], free_ok=[], absent_ok=[], fungible=[], track_open=True))

    def test_the_wp1_validators_check_envelope_and_protections(self):
        problems = spec_mod.check_structure(book_spec(envelope={"frobnicate": []}))
        self.assertTrue(any("envelope: unknown key 'frobnicate'" in p for p in problems), problems)
        problems = spec_mod.check_structure(book_spec(protections={"off": {"house.moved": "short"}}))
        self.assertTrue(any("30 characters" in p for p in problems), problems)
        problems = spec_mod.check_structure(book_spec(protections={"off": {"house.lost": "x" * 40}}))
        self.assertTrue(any("named entity" in p for p in problems), problems)
        # legacy specs carrying R1 drafts are validated too
        legacy = book_spec(envelope={"move": {"{x}": {"to": ["type:mug"]}}})
        legacy.pop("schema")
        self.assertTrue(any("to scope" in p for p in spec_mod.check_structure(legacy)))

    def test_the_wp3_timing_validator_runs_strictly(self):
        item = {"label": "close", "trigger": {"path": "time_s", "op": "gte", "value": 60},
                "act": {"path": "entities.{book}.location", "op": "eq", "value": "{shelf}"}, "within_s": 600}
        self.assertEqual(spec_mod.check_structure(book_spec(timing=[item])), [])
        narrow = dict(item, within_s=90)
        self.assertTrue(any("must be at least 120" in p for p in spec_mod.check_structure(book_spec(timing=[narrow]))))
        odd = dict(item, not_before_s=45)
        self.assertTrue(any("multiple of 30" in p for p in spec_mod.check_structure(book_spec(timing=[odd]))))
        unknown = dict(item, act="no_such_milestone")
        self.assertTrue(any("not a milestone" in p for p in spec_mod.check_structure(book_spec(timing=[unknown]))))
        phased = {"id": "p1", "timing": [narrow]}
        self.assertTrue(any(p.startswith("phase p1: ") for p in spec_mod.timing_problems({"phases": [phased]})))

    def test_the_wp4_validators_check_said_and_report_bodies(self):
        said = {"label": "told", "pred": {"said": {"to": "{kid}", "asserts": [{"kind": "nonsense"}]}}}
        problems = spec_mod.check_structure(book_spec(success=book_spec()["success"] + [said]))
        self.assertTrue(any("unknown claim kind" in p for p in problems), problems)
        report = {"report": {"kind": "measurement", "as_of": "observed", "frob": 1}}
        problems = spec_mod.check_structure(book_spec(success=book_spec()["success"] + [report]))
        self.assertTrue(any("unknown report keys: frob" in p for p in problems), problems)
        # legacy specs keep their (unvalidated) predicates: no legacy outcome moves
        legacy = book_spec(success=[report])
        legacy.pop("schema")
        self.assertFalse(any("report keys" in p for p in spec_mod.check_structure(legacy)))

    def test_answers_roles_exemptions_and_reasons(self):
        bad = {"x": {"bind": "x", "kind": "colour"}, "y": {"bind": "y", "kind": "entity",
                                                         "evidence": {"field": "label_text", "tier": "squint"}},
               "z": {"bind": "z", "kind": "number", "derive": {"op": "median", "of": ["{a}.mass_kg"]}},
               "b": {"bind": "other", "kind": "branch", "evidence": [{"field": "freshness", "tier": "smell"}]}}
        problems = spec_mod.answers_problems(bad)
        for text in ("answers.x: kind is one of", "evidence tier 'squint'", "derive is", "a branch answer binds "
                     "_branch", "branch evidence names its entity"):
            self.assertTrue(any(text in p for p in problems), (text, problems))
        good = {"grams": {"bind": "grams", "kind": "number", "tol": 2},
                "branch": {"bind": "_branch", "kind": "branch",
                           "evidence": [{"entity": "{milk}", "field": "freshness", "tier": "smell"}]},
                "net": {"bind": "net", "kind": "number", "derive": {"op": "diff", "of": ["{j}.gross_g", "{j}.tare_g"]}}}
        self.assertEqual(spec_mod.answers_problems(good), [])
        problems = spec_mod.check_structure(book_spec(
            instruction_roles={"shelf": "place"}, settle_s=900, lookahead_s=600, budget_reason="too short",
            battery_exempt=[{"policy": "B8.superset", "category": "nice", "reason": "short"}],
            mustpass_exempt=[{"policy": "M2.alt_place", "category": "required_place", "reason": "r" * 40}],
            protect_map=[{"item": "no spills", "by": ["flag:spills"], "waive": "both given"}],
            informative={"start_room": "short"}, clauses=[{"clause": "x"}]))
        for text in ("instruction_roles[shelf] is one of", "settle_s is seconds 0..600", "lookahead_s is 0 (off) or",
                     "budget_reason is a reason", "category is one of", "a reason of at least 30",
                     "by or waive, not both", "informative maps features", "clauses[0]: labels or waive"):
            self.assertTrue(any(text in p for p in problems), (text, problems))
        self.assertFalse(any("mustpass_exempt" in p for p in problems), problems)

    def test_wrong_plan_fields_and_labels(self):
        spec = book_spec(wrong=[{"name": "w", "plan": [{"do": "wait", "seconds": 1}], "mistake": [3],
                                 "modes": ["fast"], "variants": [2], "expect_budget": "x"}], variants=[{}, {}])
        problems = spec_mod.check_structure(spec)
        for text in ("expect is a non-empty list", "mistake lists the plan's top-level step indices",
                     "modes is a non-empty subset", "variants lists variant indices", "expect_budget is a reason",
                     "schema 4 needs at least 2 wrong plans"):
            self.assertTrue(any(text in p for p in problems), (text, problems))
        twice = book_spec(success=[{"label": "a", "pred": {"path": "time_s", "op": "gte", "value": 1}},
                                   {"label": "a", "pred": {"path": "time_s", "op": "gte", "value": 2}},
                                   {"label": "b:c", "pred": {"path": "time_s", "op": "gte", "value": 2}}])
        problems = spec_mod.check_structure(twice)
        self.assertTrue(any("label 'a' is used twice" in p for p in problems), problems)
        self.assertTrue(any("without ':'" in p for p in problems), problems)

    def test_variant_keys_and_the_unchanged_camera_rule(self):
        spec = book_spec(variants=[{"branch": "act", "+wrong": []}, {"branch": "dont", "frobnicate": 1}])
        problems = spec_mod.check_structure(spec)
        self.assertIn("variant 1: unknown keys frobnicate", problems)
        self.assertFalse(any(p.startswith("variant 0: unknown") for p in problems))
        geometric = {"camera": "geometric", "schema": 3, "wrong": [{"name": "w", "plan": []}]}
        self.assertTrue(spec_mod.camera_problems(geometric))          # still the schema-3 rule
        self.assertTrue(spec_mod.camera_problems(dict(geometric, schema=4)))
        self.assertEqual(spec_mod.camera_problems(dict(geometric, schema=2)), [])

    def test_timing_reaches_the_built_task(self):
        load_domains()
        item = {"label": "close", "trigger": {"path": "time_s", "op": "gte", "value": 60},
                "act": {"path": "entities.{book}.location", "op": "eq", "value": "{shelf}"}, "within_s": 600}
        _, task, oracle = build(book_spec(timing=[item]), 0)
        self.assertEqual(task["timing"][0]["act"]["value"], oracle["bindings"]["shelf"])


class LintStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_a_clean_spec_has_no_problem_and_the_result_is_deterministic(self):
        first = lint.run(book_spec())
        self.assertEqual(first, [])
        noisy = book_spec(wrong=[wrong("vague", ["goal"]), wrong("other", ["fail:nothing", "house:moved"])])
        a, b = lint.run(noisy), lint.run(copy.deepcopy(noisy))
        self.assertEqual(a, b)
        self.assertEqual(a, sorted(a, key=lambda p: (lint.SEVERITIES.index(p["severity"]),
                                                     lint.LINT_IDS.index(p["id"]), p["message"])))
        for p in a:
            self.assertEqual(set(p), {"id", "severity", "message"})

    def test_l_expect(self):
        spec = book_spec(wrong=[wrong("vague", ["goal"]), wrong("other", ["fail:nothing", "house:moved"]),
                                wrong("spills the water", ["flag:spills"]), wrong("pushes", ["flag:spills"]),
                                wrong("slow", ["budget"]), dict(wrong("x", ["goal:book_shelved"]), mistake=[])])
        text = " | ".join(messages(lint.run(spec), "L-EXPECT"))
        for part in ("a bare goal is not specific", "fail:nothing: no failure label 'nothing'",
                     "house:moved names no entity", "'pushes': a flag: pattern alone",
                     "budget needs expect_budget", "'x' has no mistake"):
            self.assertIn(part, text)
        self.assertNotIn("'spills the water': a flag", text)
        ok = book_spec(failure=[{"label": "dropped", "pred": {"path": "stats.drops", "op": "gt", "value": 0}}],
                       wrong=[wrong("a", ["fail:dropped", "house:moved:{book}", "goal:book_shelved:absent"]),
                              wrong("b", ["inv:people.unwanted_wake", "later:house:damaged:{book}",
                                          "perceive:acted_unresolved", "strict:safety", "settle:book_shelved"])])
        self.assertEqual(messages(lint.run(ok), "L-EXPECT"), [])

    def test_l_protect(self):
        record = {"id": "T-wp8-book-01", "protect": ["no spills", "oven clock not changed", "lights left as found",
                                                     "no fire", "furniture not moved"]}
        spec = book_spec(protect_map=[
            {"item": "No spills.", "by": ["flag:spills"]},
            {"item": "oven clock not changed", "by": ["house:changed:{oven}.clock_offset_s", "flag:fire"]},
            {"item": "lights left as found", "by": ["house:changed:category:light"]},
            {"item": "furniture not moved", "by": ["flag:breakages"]},
            {"item": "furniture not moved", "by": ["house:moved"]},
            {"item": "stale item", "by": ["house:frobnicated", "flag:nonsense", "inv:no.such", "fail:none",
                                          "later:house:moved"]}])
        text = " | ".join(messages(lint.run(spec, record), "L-PROTECT"))
        for part in ("'no fire' is unmapped", "'furniture not moved' is mapped 2 times",
                     "protects lights: add protections.restore", "unknown guard 'frobnicated'",
                     "unknown global flag", "no registered invariant", "no failure label 'none'",
                     "the look-ahead is off", "no protect item of the analysis record"):
            self.assertIn(part, text)
        self.assertNotIn("'No spills.': a flag", text)      # flag:spills alone covers a spill item
        fixed = book_spec(protect_map=[{"item": i, "by": ["house:moved"]} for i in record["protect"]],
                          protections={"restore": ["category:light"]})
        self.assertEqual(messages(lint.run(fixed, record), "L-PROTECT"), [])
        flag_only = book_spec(protect_map=[{"item": i, "by": ["flag:fire"]} for i in record["protect"]],
                              protections={"restore": ["category:light"]})
        self.assertTrue(any("'oven clock not changed': a flag: reference alone" in m
                            for m in messages(lint.run(flag_only, record), "L-PROTECT")))
        allowed = book_spec(protect_map=[{"item": i, "by": ["house:moved:{book}"]} for i in record["protect"]],
                            protections={"restore": ["category:light"]})
        self.assertTrue(any("the envelope allows it" in m for m in messages(lint.run(allowed, record), "L-PROTECT")))
        off = book_spec(protect_map=[{"item": i, "by": ["house:moved"]} for i in record["protect"]],
                        protections={"restore": ["category:light"], "off": {"house.moved": "x" * 40}})
        self.assertTrue(any("the guard is off" in m for m in messages(lint.run(off, record), "L-PROTECT")))

    def test_l_protect_variant_scoping(self):
        record = {"id": "T-wp8-book-01", "protect": ["no breakage"]}
        fail = {"label": "broke", "pred": {"path": "entities.{book}.broken", "op": "is_true"}}
        spec = book_spec(variants=[{"branch": "act", "failure": [fail]}, {"branch": "act"}, {"branch": "dont"}],
                         protect_map=[{"item": "no breakage", "by": ["fail:broke@0"]}])
        text = " | ".join(messages(lint.run(spec, record), "L-PROTECT"))
        self.assertIn("protection in one variant only", text)
        self.assertIn("[1]", text)
        scoped = book_spec(variants=[{"branch": "act", "failure": [fail]}, {"branch": "act", "failure": [fail]},
                                     {"branch": "dont"}],
                           protect_map=[{"item": "no breakage", "by": ["fail:broke@0,1"]}])
        problems = [p for p in lint.run(scoped, record) if p["id"] == "L-PROTECT"]
        self.assertEqual([p["severity"] for p in problems], ["warning"])
        unscoped = book_spec(variants=[{"branch": "act", "failure": [fail]}, {"branch": "dont"}],
                             protect_map=[{"item": "no breakage", "by": ["fail:broke"]}])
        self.assertTrue(any("variant 1: 'no breakage': fail:broke: no failure label" in m
                            for m in messages(lint.run(unscoped, record), "L-PROTECT")))

    def test_l_roles_and_l_envelope(self):
        spec = book_spec(instruction="Put the book on the {shelf} and tell {resident}.",
                         phrasings=["Put the book on the {shelf} and tell {resident}."] * 3,
                         success=[{"label": "near", "pred": {"path": "robot.room", "op": "eq", "value": "{living}"}}],
                         instruction_roles={"resident": "recipient"}, wrong=[wrong("a", ["goal:near"]),
                                                                            wrong("b", ["goal:near"])])
        text = " | ".join(messages(lint.run(spec), "L-ROLES"))
        self.assertIn("{shelf} follows a destination preposition", text)
        self.assertIn("{resident} is a recipient", text)
        answer = book_spec(instruction_roles={"shelf": "answer"})
        self.assertIn("{shelf}: an instruction placeholder is never an answer (9.1)", messages(lint.run(answer),
                                                                                               "L-ROLES"))
        by_scope = book_spec(success=[{"label": "near", "pred": {"path": "robot.room", "op": "eq",
                                                                  "value": "{living}"}},
                                      {"label": "kept", "pred": {"path": "entities.{book}.broken", "op": "is_false"}}],
                             wrong=[wrong("a", ["goal:near"]), wrong("b", ["goal:near"])])
        self.assertEqual(messages(lint.run(by_scope), "L-ROLES"), [])     # the book's to scope is exactly {shelf}
        env = book_spec(envelope={"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]},
                                                                       "{table}": {"to": ["{shelf}"]}},
                                  "consume": ["{table}", "type:paper_towel_roll"]})
        problems = [p for p in lint.run(env) if p["id"] == "L-ENVELOPE"]
        self.assertTrue(all(p["severity"] == "warning" for p in problems), problems)
        text = " | ".join(p["message"] for p in problems)
        self.assertIn("envelope.consume {table} is no instruction target", text)
        self.assertIn("envelope.move {table}: a narrow to scope", text)
        self.assertNotIn("paper_towel", text)
        target = book_spec(instruction="Put the {book} on the {shelf}.",
                           phrasings=["Put the {book} on the {shelf}."] * 3,
                           instruction_roles={"book": "target"}, success=[{"label": "near", "pred": {
                               "path": "entities.{shelf}.location", "op": "exists", "value": True}}],
                           envelope={"consume": ["{book}"]}, wrong=[wrong("a", ["goal:near"]),
                                                                   wrong("b", ["goal:near"])])
        self.assertTrue(any("free only when a success predicate requires it" in m
                            for m in messages(lint.run(target), "L-ENVELOPE")))

    def test_l_weak_l_kind_l_latch_l_exists(self):
        spec = book_spec(success=book_spec()["success"] + [
            {"label": "fragile", "pred": {"path": "entities.{book}.label_text", "op": "contains", "value": "FRAGILE"}},
            {"label": "ordered", "pred": {"path": "orders", "op": "len_gte", "value": 1}},
            {"label": "count", "pred": {"path": "stats.handoffs", "op": "gte", "value": 1}},
            {"label": "sorted", "pred": {"path": "entities.{book}.sorted_ok", "op": "is_true"}},
            {"label": "gone", "pred": {"path": "entities.{book}.leaking", "op": "is_false"}}],
            absent_ok=["gone", "fragile", "missing"])
        problems = lint.run(spec)
        self.assertEqual(len(messages(problems, "L-WEAK")), 2)
        self.assertTrue(any("stats.handoffs" in m for m in messages(problems, "L-KIND")))
        self.assertTrue(any("latched field sorted_ok" in m for m in messages(problems, "L-LATCH")))
        exists = " | ".join(messages(problems, "L-EXISTS"))
        self.assertIn("absent_ok 'missing' is no success label", exists)
        self.assertIn("absent_ok 'fragile' is not a negated or protective predicate", exists)
        self.assertNotIn("'gone'", exists)
        with mock.patch.dict("house_sim.kernel.plugins.DERIVED_FIELDS", {"sorted_ok": object()}):
            self.assertEqual(messages(lint.run(spec), "L-LATCH"), [])
        content = book_spec(success=book_spec()["success"] + [
            {"label": "ordered", "pred": {"path": "orders", "op": "len_gte", "value": 1}},
            {"label": "what", "pred": {"path": "orders.0.item", "op": "eq", "value": "milk"}}])
        self.assertEqual(messages(lint.run(content), "L-WEAK"), [])

    def test_l_speech_l_request_l_priv(self):
        spec = book_spec(success=book_spec()["success"] + [
            {"label": "told", "pred": {"path": "people.{kid}.told_count", "op": "gte", "value": 1}},
            {"label": "to", "pred": {"path": "reports.{vent}._to", "op": "eq", "value": "household"}},
            {"label": "free", "pred": {"path": "reports.{vent}.note", "op": "contains", "value": "dusty"}}],
            phrasings=["Put the book on the {shelf}.", "Mia (9) says: shelve the book on the {shelf}.",
                       "Move book_1 to the {shelf}."],
            reference=[{"save_state": "x", "path": "entities.{book}.location"},
                       {"wait_until": {"path": "time_s", "op": "gte", "value": 5}, "max_s": 60, "poll_s": 10}])
        problems = lint.run(spec)
        speech = " | ".join(messages(problems, "L-SPEECH"))
        self.assertIn("told_count without a said content predicate", speech)
        self.assertIn("a free-text report field", speech)
        self.assertNotIn("who got report", speech)          # reports.{vent}.note is content on that report
        request = " | ".join(messages(problems, "L-REQUEST"))
        self.assertIn("a role tag", request)
        self.assertIn("id-shaped words ['book_1']", request)
        self.assertEqual(len(messages(problems, "L-PRIV")), 2)
        said = book_spec(success=spec["success"][:3] + [
            {"label": "said", "pred": {"said": {"to": "{kid}", "asserts": [{"kind": "directive", "act": "avoid"}]}}}])
        self.assertFalse(any("told_count" in m for m in messages(lint.run(said), "L-SPEECH")))
        self.assertTrue(any("who got report" in m for m in messages(lint.run(said), "L-SPEECH")))

    def test_l_namedroom(self):
        setup = SETUP + [{"op": "room", "var": "hall", "type": "hallway", "pick": "first"}]
        spec = book_spec(setup=setup, failure=[
            {"label": "debris", "pred": {"exists": {"where": {"room": "{hall}", "type": "debris"}}}},
            {"label": "lit", "pred": {"path": "rooms.kitchen_1.light", "op": "is_true"}}])
        text = " | ".join(messages(lint.run(spec), "L-NAMEDROOM"))
        self.assertIn("names the room {hall}", text)
        self.assertIn("names the room 'kitchen_1' literally", text)
        work = book_spec(setup=setup, failure=spec["failure"][:1],
                         envelope={"work_rooms": ["{living}", "{hall}"], "move": {"{book}": {"to": ["{shelf}"]}}})
        self.assertEqual(messages(lint.run(work), "L-NAMEDROOM"), [])

    def test_l_answers_static(self):
        entry = {"id": "T-wp8-book-01", "kind": "task", "text": "Find the remote."}
        self.assertTrue(any("must declare its hidden answers" in m
                            for m in messages(lint.run(book_spec(), None, entry=entry), "L-ANSWERS")))
        answers = {"spot": {"bind": "spot", "kind": "entity", "group": "{spots}"}}
        text = " | ".join(messages(lint.run(book_spec(answers=answers), None, entry=entry), "L-ANSWERS"))
        self.assertIn("a Find entry declares a place answer", text)
        self.assertIn("entity answer spot needs evidence", text)
        told = {"where": {"bind": "shelf", "kind": "place", "group": ["a", "b"]}}
        self.assertTrue(any("is in the instruction" in m for m in messages(lint.run(book_spec(answers=told)),
                                                                           "L-ANSWERS")))
        conditional = {"id": "T-wp8-book-01", "conditional": "if the milk is sour, discard it"}
        self.assertTrue(any("branch answer" in m for m in messages(lint.run(book_spec(), conditional),
                                                                   "L-ANSWERS")))
        item = {"label": "close", "trigger": {"path": "time_s", "op": "gte", "value": 60},
                "act": {"path": "entities.{book}.location", "op": "eq", "value": "{shelf}"}, "within_s": 600}
        self.assertTrue(any("time answer" in m for m in messages(lint.run(book_spec(timing=[item])), "L-ANSWERS")))

    def test_l_branch_and_l_questions(self):
        conditional = {"id": "T-wp8-book-01", "conditional": "if the milk is sour, discard it"}
        flat = lint.run(book_spec(), conditional)
        self.assertTrue(any("at least 2 branches" in m for m in messages(flat, "L-BRANCH")))
        two = book_spec(variants=[{"branch": "act"}, {"branch": "dont", "wrong": []}])
        text = " | ".join(messages(lint.run(two, conditional), "L-BRANCH"))
        self.assertIn('branch "dont" has no wrong plan', text)
        self.assertNotIn("at least 2 branches", text)
        merged = lint.lint_branch(two, lint._variant_scopes(spec_mod.expand(two)), conditional, None,
                                  {"branch_cross": [["act", "dont", True], ["dont", "act", True]]})
        self.assertTrue(any("merge them" in p["message"] for p in merged))
        always = book_spec(questions={"min_necessary": 1})
        self.assertTrue(messages(lint.run(always), "L-QUESTIONS"))
        mixed = book_spec(variants=[{"questions": {"min_necessary": 1}}, {"questions": {"max_unnecessary": 0}}])
        self.assertEqual(messages(lint.run(mixed), "L-QUESTIONS"), [])
        exempt = book_spec(questions={"min_necessary": 1}, battery_exempt=[
            {"policy": "B9.always_ask", "category": "questions_always_required", "reason": "q" * 40}])
        self.assertEqual(messages(lint.run(exempt), "L-QUESTIONS"), [])

    def test_l_clauses(self):
        entry = {"id": "X-wp8-01", "kind": "appendix",
                 "text": "Robot upkeep: empty the robot vacuum, clean its brushes (and the roller), refill the tank."}
        self.assertEqual(lint.clauses_of(entry["text"]), ["empty the robot vacuum", "clean its brushes",
                                                          "refill the tank"])
        text = " | ".join(messages(lint.run(book_spec(), None, entry=entry), "L-CLAUSES"))
        self.assertIn("'clean its brushes' is not mapped", text)
        mapped = book_spec(clauses=[{"clause": "Empty the robot vacuum", "labels": ["book_shelved"]},
                                    {"clause": "clean its brushes", "labels": ["nope"]},
                                    {"clause": "refill the tank", "waive": "the tank refill is a separate entry here"}])
        self.assertEqual(messages(lint.run(mapped, None, entry=entry), "L-CLAUSES"),
                         ["clause 'clean its brushes' maps to 'nope', which is no success, failure or timing label"])

    def test_l_escape(self):
        heavy = book_spec(protections={"off": {"house.moved": "m" * 40, "house.changed": "c" * 40,
                                               "house.soiled": "s" * 40, "house.wet": "w" * 40}})
        problems = lint.run(heavy)
        self.assertTrue(any("20 escape points (at most 15)" in m for m in messages(problems, "L-ESCAPE")))
        pending = [p for p in problems if p["id"] == "L-ESCAPE" and "waits for an accepted review" in p["message"]]
        self.assertEqual(len(pending), 4)
        self.assertEqual({p["severity"] for p in pending}, {"warning"})
        three = book_spec(battery_exempt=[{"policy": "B{}.x".format(i), "category": "safe_superset",
                                           "reason": "r" * 40} for i in (2, 3, 4)])
        self.assertTrue(any("3 battery and must-pass exemptions proposed" in m
                            for m in messages(lint.run(three), "L-ESCAPE")))
        one = book_spec(protections={"off": {"house.moved": "m" * 40}})
        shash = escapes.price(one)["escapes"] and lint._spec_hash(spec_mod.expand(one))
        row = {"catalog_id": "T-wp8-book-01", "policy": "escape:protections.off:house.moved", "spec_hash": shash,
               "decision": "accept", "reviewer": {"kind": "agent", "session_id": "author-session"}}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "exemptions.jsonl")
            with open(path, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(row) + "\n")
            with mock.patch.object(escapes, "EXEMPTIONS", Path(path)), \
                    mock.patch("house_sim.tasks.checklog.sessions", return_value=["author-session"]):
                bad = messages(lint.run(one), "L-ESCAPE")
            logged = {"author-session": {"session_id": "author-session", "model": "m", "prompt_digest": "p",
                                         "transcript_digest": "t"}}     # a logged review session (10.7)
            with mock.patch.object(escapes, "EXEMPTIONS", Path(path)), \
                    mock.patch("house_sim.tasks.checklog.sessions", return_value=["someone-else"]), \
                    mock.patch.object(escapes, "review_sessions", return_value=logged, create=True):
                good = messages(lint.run(one), "L-ESCAPE")
        self.assertTrue(any("reviewer is not eligible" in m for m in bad), bad)
        self.assertEqual(good, [])
        # an owner acceptance an authoring machine cannot verify (no split key) waits for the gate: a warning
        owner = dict(row, reviewer={"kind": "owner"}, signature="0" * 64)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "exemptions.jsonl")
            with open(path, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(owner) + "\n")
            with mock.patch.object(escapes, "EXEMPTIONS", Path(path)), \
                    mock.patch("house_sim.tasks.checklog.sessions", return_value=[]), \
                    mock.patch.object(escapes, "_review_key", return_value=None, create=True):
                waits = [p for p in lint.run(one) if p["id"] == "L-ESCAPE"]
        if any("signature" in p["message"] for p in waits):     # escapes.py with owner signatures (WP5-fix)
            self.assertEqual([p["severity"] for p in waits], ["warning"], waits)

    def test_variants_collapse_and_the_as_if_pass(self):
        spec = book_spec(variants=[{"wrong": [wrong("v", ["goal"]), wrong("w", ["goal:book_shelved"])]},
                                   {"wrong": [wrong("v", ["goal"]), wrong("w", ["goal:book_shelved"])]}])
        self.assertIn("variants 0, 1: wrong plan 'v': a bare goal is not specific", messages(lint.run(spec),
                                                                                              "L-EXPECT"))
        legacy = json.loads((ROOT / "tasks" / "authored" / "climate__examples.json").read_text(encoding="utf-8"))
        spec = next(s for s in legacy["specs"] if s["catalog_id"] == LEGACY_ID)
        record = lint.analysis_record(LEGACY_ID)
        self.assertEqual(lint.run(spec, record), [])
        as_if = lint.run(spec, record, as_if=True)
        self.assertIn("L-EXPECT", ids_of(as_if))            # legacy wrong plans have no expect
        self.assertIn("L-PROTECT", ids_of(as_if))           # nor a protect map
        self.assertEqual(as_if, lint.run(copy.deepcopy(spec), record, as_if=True))


class LintDynamicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.facts = lint.dynamic_facts(book_spec(), [0, 1])

    def test_the_facts_of_a_build(self):
        facts = self.facts
        self.assertEqual(facts["seeds"], [0, 1])
        self.assertEqual(facts["free"], {0: {"book_shelved": False}, 1: {"book_shelved": False}})
        self.assertEqual(facts["binding_types"][0]["shelf"], "type:bookshelf")
        self.assertGreaterEqual(facts["scene"][0]["book"], 1)
        self.assertGreaterEqual(facts["scene"][0]["shelf"], 1)
        self.assertEqual(lint.run(book_spec(), None, dynamic=facts), [])

    def test_prebuilt_builds_give_the_same_facts(self):
        prebuilt = {0: build(book_spec(), 0)}
        facts = lint.dynamic_facts(book_spec(), [0], builds=prebuilt)
        for key in ("free", "bindings", "binding_types", "scene", "variants", "instructions", "people"):
            self.assertEqual(facts[key][0], self.facts[key][0], key)

    def test_the_facts_do_not_depend_on_the_declared_modes(self):
        for perception, camera in (("realistic", "room"), ("realistic", "geometric"), ("oracle", "geometric")):
            with self.subTest(perception=perception, camera=camera):
                spec = book_spec(perception=perception, camera=camera)
                if camera == "geometric":
                    spec["wrong"][0]["camera"] = True
                facts = lint.dynamic_facts(spec, [0])
                for key in ("free", "bindings", "binding_types", "scene", "variants", "instructions"):
                    self.assertEqual(facts[key][0], self.facts[key][0], key)

    def test_l_vacuous_and_l_timing(self):
        late = book_spec(success=[{"label": "late", "pred": {"path": "time_s", "op": "gte", "value": 120}}],
                         wrong=[wrong("a", ["goal:late"]), wrong("b", ["goal:late"])])
        facts = lint.dynamic_facts(late, [0])
        self.assertTrue(any("only weak predicates (late)" in m for m in messages(lint.run(late, None, facts),
                                                                                 "L-VACUOUS")))
        free = {"free": {0: {"book_shelved": True}, 1: {"book_shelved": True}}}
        self.assertTrue(any("holds at t=0 on every seed" in m for m in messages(lint.run(book_spec(), None, free),
                                                                                "L-VACUOUS")))
        two = book_spec(success=book_spec()["success"] + [
            {"label": "tidy", "pred": {"path": "entities.{table}.broken", "op": "is_false"}}])
        some = {"free": {0: {"book_shelved": True, "tidy": False}, 1: {"book_shelved": False, "tidy": False}}}
        problems = [p for p in lint.run(two, None, some) if p["id"] == "L-VACUOUS"]
        self.assertEqual([p["severity"] for p in problems], ["warning"])
        self.assertEqual([p for p in lint.run(dict(two, free_ok=["book_shelved"]), None, some)
                          if p["id"] == "L-VACUOUS"], [])
        solved = {"free": {0: {"book_shelved": True, "tidy": True}, 1: {"book_shelved": False, "tidy": False}}}
        self.assertIn("seed 0: every success predicate already holds at t=0",
                      messages(lint.run(two, None, solved), "L-VACUOUS"))
        protect = {"free": {0: {"book_shelved": False, "tidy": True}, 1: {"book_shelved": False, "tidy": True}}}
        problems = [p for p in lint.run(two, None, protect) if p["id"] == "L-VACUOUS"]
        self.assertEqual([p["severity"] for p in problems], ["warning"])
        self.assertIn("a never item states it better", problems[0]["message"])
        item = {"label": "shelved", "trigger": {"path": "time_s", "op": "gte", "value": 0},
                "act": {"path": "entities.{book}.location", "op": "eq", "value": "{table}"}, "within_s": 600}
        spec = book_spec(timing=[item], answers={"t": {"bind": "t", "kind": "time"}})
        facts = lint.dynamic_facts(spec, [0])
        self.assertEqual(facts["timing_initial"][0], {"shelved": True})
        self.assertTrue(any("already holds at t=0" in m for m in messages(lint.run(spec, None, facts), "L-TIMING")))
        self.assertEqual(messages(lint.run(dict(spec, timing=[dict(item, initially="any")]), None, facts),
                                  "L-TIMING"), [])

    def test_l_answers_scene_budget_cover_envelope_counts(self):
        drawn = book_spec(setup=SETUP + [{"op": "choose", "var": "pick", "from": ["a", "b"]}],
                          success=book_spec()["success"] + [{"label": "said_pick", "pred": {
                              "path": "facts.answer", "op": "eq", "value": "{pick}"}}],
                          wrong=[wrong("a", ["goal:book_shelved"]), wrong("b", ["goal:book_shelved"])])
        facts = {"bindings": {0: {"pick": "a"}, 1: {"pick": "b"}}, "variants": {0: None, 1: None}}
        self.assertTrue(any("{pick} varies over V" in m for m in messages(lint.run(drawn, None, facts),
                                                                          "L-ANSWERS")))
        record = {"id": "T-wp8-book-01", "wrong": ["picks a similar-looking neighbouring book"]}
        alone = {"scene": {0: {"book": 1}, 1: {"book": 2}}}
        self.assertTrue(any(m.startswith("seed 0: no commanded success role")
                            for m in messages(lint.run(book_spec(), record, alone), "L-SCENE")))
        self.assertEqual(messages(lint.run(book_spec(scene_exception="the book is the only one of its kind on "
                                                                     "purpose"), record, alone), "L-SCENE"), [])
        budget = {"budget": {"variants": {"None": {"formula": {"steps": 20, "time_s": 300},
                                                   "authored": {"steps": 40, "time_s": 600},
                                                   "ok": False, "share_ok": False}}}}
        text = messages(lint.run(book_spec(), None, budget), "L-BUDGET")
        self.assertEqual(len(text), 2)
        self.assertEqual(messages(lint.run(book_spec(), None, {"coverage": {"covered": False, "deficient": ["v1"]}}),
                                  "L-COVER"), ["coverage not reached within 12 verification seeds: v1"])
        broad = book_spec(envelope={"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]},
                                                                         "type:book": {"to": ["baseline"]}}})
        self.assertTrue(any("type:book matches 9 instances" in m for m in messages(
            lint.run(broad, None, {"type_counts": {0: {"book": 9}}}), "L-ENVELOPE")))


def authored(catalog_id):
    for path in sorted((ROOT / "tasks" / "authored").glob("*.json")):
        for spec in json.loads(path.read_text(encoding="utf-8"))["specs"]:
            if spec["catalog_id"] == catalog_id:
                return spec
    raise KeyError(catalog_id)


class ReviewCaseTests(unittest.TestCase):
    """The independent review's gaming cases the lints close (VERIFY_HARDENING 2), linted as if schema 4 (the R0
    audit); as legacy specs they are untouched."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    def as_if(self, catalog_id, dynamic=None):
        spec = authored(catalog_id)
        self.assertEqual(lint.run(spec, lint.analysis_record(catalog_id)), [])
        return lint.run(spec, lint.analysis_record(catalog_id), dynamic=dynamic, as_if=True)

    def test_the_static_cases(self):
        cases = {"A-04b40946bea880-01": ("L-ROLES", "{staging_room} follows a destination preposition"),
                 "A-b6bf8052cc5211-01": ("L-WEAK", '"NOT FRAGILE" contains "FRAGILE"'),
                 "T-dde0975c7a26b8-01": ("L-REQUEST", "Mia (9) says:"),
                 "T-f7f4105ce49060-01": ("L-LATCH", "latched field sorted_ok"),
                 "T-d389e2979f6aad-01": ("L-LATCH", "latched field in_stream"),
                 "A-8910a09f802fbf-01": ("L-WEAK", "orders length without a content predicate")}
        for cid, (lint_id, text) in sorted(cases.items()):
            with self.subTest(spec=cid):
                self.assertTrue(any(text in m for m in messages(self.as_if(cid), lint_id)), cid)

    def test_the_vacuous_cases(self):
        heater = "T-e19f473c5cfd0d-01"           # feel_c > 17 already true at t=0
        problems = self.as_if(heater, lint.dynamic_facts(authored(heater), [0, 1]))
        self.assertTrue(any("holds at t=0 on every seed" in m for m in messages(problems, "L-VACUOUS")))
        lie = "V-10424aa789320a-01"              # variant 0: told_count is all that is left to do
        problems = self.as_if(lie, lint.dynamic_facts(authored(lie), [0, 1, 2]))
        self.assertTrue(any("only weak predicates" in m for m in messages(problems, "L-VACUOUS")))
        self.assertTrue(any("told_count without a said content predicate" in m
                            for m in messages(problems, "L-SPEECH")))


class ProtectClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_every_rule_compiles_and_every_candidate_resolves(self):
        data = json.loads(lint.PROTECT_RULES_FILE.read_text(encoding="utf-8"))
        ids = [r["id"] for r in data["rules"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 30)
        scope = {"lookahead_s": 21600}
        for rule in data["rules"]:
            for ref in rule["by"]:
                with self.subTest(rule=rule["id"], ref=ref):
                    self.assertIsNone(lint.protect_ref_problem(ref, scope, True))
            for entry in rule.get("restore") or ():
                self.assertIn(entry, ("category:light", "doors:interior"))

    def test_coverage_beats_the_prototype(self):
        sys.path.insert(0, str(ROOT))
        try:
            import tools.protect_map as tool
        finally:
            sys.path.remove(str(ROOT))
        cover = tool.coverage()
        self.assertEqual(cover["items"], 6355)
        self.assertGreater(cover["share"], 0.729)

    def test_the_design_examples_map(self):
        cases = {"oven clock not changed": "house:changed", "no spills": "flag:spills",
                 "no thawing freezer": "later:house:damaged", "water/energy waste": "house:waste:energy",
                 "sleeping baby not woken": "inv:people.unwanted_wake", "no other items disturbed in the fetch":
                 "house:moved", "keepsakes not discarded": "house:lost", "mud tracked through the house":
                 "house:soiled", "no trip hazard left on the stairs": "inv:electrical.trip_hazard"}
        for text, ref in cases.items():
            with self.subTest(item=text):
                self.assertIn(ref, lint.classify_protect_item(text)["by"])
        lights = lint.classify_protect_item("doors/lights left as found")
        self.assertEqual(lights["restore"], ["category:light", "doors:interior"])
        self.assertEqual(lint.classify_protect_item("family friction")["by"][:0], [])
        self.assertTrue(lint.classify_protect_item("family friction")["waive_hint"])
        self.assertEqual(lint.classify_protect_item("in-season shoes made inaccessible")["by"],
                         lint.classify_protect_item("in-season shoes made inaccessible")["by"])

    def test_the_draft_and_the_tools(self):
        draft = lint.draft_protect_map(LEGACY_ID)
        self.assertEqual(draft["items"], 2)
        self.assertEqual(draft, lint.draft_protect_map(LEGACY_ID))
        self.assertIn("house:changed", draft["protect_map"][0]["by"])
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        out = subprocess.run([sys.executable, str(ROOT / "tools" / "protect_map.py"), LEGACY_ID, "--json"],
                             capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["catalog_id"], LEGACY_ID)
        out = subprocess.run([sys.executable, str(ROOT / "tools" / "show_entries.py"), "ids", LEGACY_ID],
                             capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("draft protect_map (2 of 2 item(s) with candidates", out.stdout)
        self.assertIn('"unrequested changes to vent states" -> ', out.stdout)
        missing = subprocess.run([sys.executable, str(ROOT / "tools" / "protect_map.py"), "T-no-such-01"],
                                 capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
        self.assertEqual(missing.returncode, 1)


class WatchTests(unittest.TestCase):
    def test_l_watch_wraps_the_census(self):
        problems = lint.watch_problems()
        self.assertEqual([p for p in problems if p["severity"] == "error"], [])     # WP1: all 993 fields classified
        self.assertTrue(all(p["id"] == "L-WATCH" for p in problems))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "census.json")
            with open(path, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"fields": {"wp8_never_classified_field": {"types": {"mug": 1}}}}, stream)
            errors = [p for p in lint.watch_problems(path, domains=["core"]) if p["severity"] == "error"]
        self.assertEqual([p["message"] for p in errors],
                         ["census field without an explicit class: wp8_never_classified_field"])


class PriceListTests(unittest.TestCase):
    SECTION_18 = {"protections.off": (5, "review"), "deliver_non_target": (5, "review"),
                  "consume_non_target": (5, "review"), "agents_non_target": (5, "review"),
                  "battery_exempt": (3, "review"), "lookahead_short": (3, "review"), "settle_below_30": (3, "review"),
                  "no_state_label": (3, "review"), "mustpass_exempt": (2, "review"), "protect_waive": (2, "review"),
                  "protect_waive_first": (1, "review"), "settle_30_59": (2, "quota"), "allow_at": (2, "review"),
                  "held_at_end": (2, "review"), "roles_context": (2, "review"), "envelope_resources": (2, "review"),
                  "never_touch_change": (2, "review"), "create_unnamed": (2, "review"),
                  "scene_exception": (2, "review"), "unrealisable": (1, "review"),
                  "questions_always_required": (1, "review"), "informative": (1, "quota"),
                  "informative_structure": (1, "review"), "free_ok": (1, "quota"), "fungible": (1, "quota"),
                  "transit_ok": (1, "quota"), "narrow_to": (1, "quota"), "process_unproposed": (1, "quota"),
                  "absent_ok_uncovered": (1, "quota"), "hold_open": (1, "quota"), "expect_budget": (1, "quota"),
                  "budget_reason": (0, "review"), "budget_only_defense": (1, "none"), "tier_reduced": (0, "none")}

    def test_the_price_list_is_section_18(self):
        data = json.loads(lint.ESCAPES_FILE.read_text(encoding="utf-8"))
        self.assertEqual((data["quota_points"], data["max_points"], data["max_exemptions"]), (3, 15, 2))
        table = {k: (v["points"], v["review"]) for k, v in data["escapes"].items()}
        self.assertEqual(table, self.SECTION_18)
        # every escape the verifier emits is priced by the file (no built-in table behind it: P2-S2 merge gate)
        source = Path(escapes.__file__).read_text(encoding="utf-8")
        emitted = set(re.findall(r'_escape\("([a-z_.0-9]+)"', source))
        self.assertTrue(emitted)
        self.assertEqual(emitted - set(table), set())
        self.assertFalse(hasattr(escapes, "DEFAULT_PRICES"))
        self.assertEqual(escapes.price_list(reload=True), table)
        self.assertEqual((escapes.QUOTA_POINTS, escapes.MAX_POINTS, escapes.MAX_EXEMPTIONS), (3, 15, 2))


class RunAFindingTests(unittest.TestCase):
    """The run-A review findings against WP8 (catalog/analysis/phase2/review_A_*.json, confirmed in verify_A_*.json):
    each test replays its finding's repro, and each failed on the tree before the fix."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    # A-bugs-7: check_structure raised TypeError for a non-list success / failure and aborted the catalog compile

    BAD = {"success": (-1, True, "x", {"a": 1}), "failure": (5, {"a": 1}), "wrong": (None, 0, True, [5],
           [{"name": "w", "plan": 5}]), "variants": (-1, True, [5], [{"+success": 5}], [{"success": 7}]),
           "phases": (5, [3], [{"id": "a", "success": 5}]), "setup": (5,), "reference": ({},), "phrasings": ("x",),
           "house": (5,), "budget": ([],), "milestones": ([],), "never": (5,), "timing": (5,), "allow": ("x",)}

    def compile_like(self, spec):
        """house_sim/compiler.py's path: expand, then check_structure, a SpecError marks the spec INVALID."""
        try:
            return spec_mod.check_structure(spec_mod.expand(spec))
        except SpecError as exc:
            return [str(exc)]

    def test_a_bugs_7_a_malformed_spec_is_invalid_never_a_crash(self):
        legacy = book_spec(catalog_id="T-wp8-book-01")
        legacy.pop("schema")
        for base in (book_spec(), legacy):
            for key, values in sorted(self.BAD.items()):
                for value in values:
                    with self.subTest(schema=base.get("schema", 2), key=key, value=value):
                        spec = dict(copy.deepcopy(base), **{key: copy.deepcopy(value)})
                        problems = self.compile_like(spec)
                        self.assertTrue(problems and all(isinstance(p, str) for p in problems), problems)
                        for p in lint.run(spec, None):              # the verifier's S0 too
                            self.assertEqual(set(p), {"id", "severity", "message"})
        # the review's exact cases name the key
        self.assertEqual(spec_mod.check_structure(book_spec(success=-1)), ["success is a list (not number -1)"])
        self.assertEqual(spec_mod.check_structure(book_spec(failure=5)), ["failure is a list (not number 5)"])
        self.assertEqual(spec_mod.check_structure(book_spec(variants=[{"+success": 5}])),
                         ["variant 0: \"+success\" appends a list (not number 5)"])
        with self.assertRaises(SpecError):
            spec_mod.expand(dict(book_spec(), **{"+setup": 5}))       # the compiler marks it INVALID
        self.assertEqual(spec_mod.check_structure(book_spec()), [])
        # malformed schema-4 blocks: check_structure names them, and L-ENVELOPE / L-PROTECT read them as empty, never
        # "could not be checked" (L-ESCAPE goes through WP5's escapes.price)
        record = {"id": "T-wp8-book-01", "protect": ["x"]}
        for changes in ({"envelope": {"deliver": 5, "move": {"{book}": {"to": 5}}}}, {"envelope": {"move": 5}},
                        {"protections": 5}, {"protections": {"off": 5, "restore": 5}}, {"protect_map": 5},
                        {"protect_map": [{"item": "x", "by": 5}]}):
            with self.subTest(changes=changes):
                spec = book_spec(**changes)
                self.assertTrue(spec_mod.check_structure(spec))
                self.assertEqual([p for p in lint.run(spec, record) if "could not be checked" in p["message"] and
                                  p["id"] in ("L-ENVELOPE", "L-PROTECT")], [])

    # A-gaming-13: the bare pattern inv: passed L-EXPECT and matched every invariant label

    def test_a_gaming_13_an_inv_pattern_names_a_whole_invariant(self):
        scope = {"success": []}
        for pattern in ("inv:", "inv:s", "inv:people", "inv:people.", "inv:people.unwanted", "inv:flag:fire",
                        "inv:people.unwanted_wake:", "inv:climate.building_"):
            with self.subTest(pattern=pattern):
                self.assertIsNotNone(lint.expect_pattern_problem(pattern, scope, {"name": "n"}))
                self.assertIsNotNone(lint.protect_ref_problem(pattern, scope))
        for pattern in ("inv:people.unwanted_wake", "inv:people.fall:resident_1", "inv:climate.pipe_freeze",
                        "inv:navigation_robot.tracked_dirt"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(lint.expect_pattern_problem(pattern, scope, {"name": "n"}))
                self.assertIsNone(lint.protect_ref_problem(pattern, scope))
        from house_sim.kernel.plugins import INVARIANTS
        labels = lint.invariant_labels()
        self.assertTrue(set(INVARIANTS) <= labels)
        self.assertIn("climate.pipe_freeze", labels)        # climate.building reports it (not its own name)
        self.assertEqual([n for n in labels - set(INVARIANTS) if not lint.INV_LABEL_RE.match(n)], [])
        vague = book_spec(wrong=[dict(wrong("puts the book on the floor", ["inv:"])),
                                 wrong("holds the book and waits", ["goal:book_shelved"])])
        self.assertIn("wrong plan 'puts the book on the floor': inv:: names no invariant "
                      "(inv:<domain>.<name>[:<detail>])", messages(lint.run(vague), "L-EXPECT"))
        self.assertIn("L-EXPECT", ids_of(lint.run(vague), "error"))     # S0 stops the spec

    # A-spec-14: selectors, to scopes and narrowed protect references were never resolved

    def test_a_spec_14_envelope_selectors_and_scopes_resolve(self):
        def errors_of(**changes):
            return [p["message"] for p in lint.run(book_spec(**changes)) if p["id"] == "L-ENVELOPE" and
                    p["severity"] == "error"]
        typo = errors_of(envelope={"move": {"typo:book": {"to": ["rooms:wrok"]}}})
        self.assertEqual(len(typo), 2, typo)
        self.assertTrue(typo[0].startswith("envelope.move: 'typo:book': unknown form"), typo)
        self.assertTrue(typo[1].startswith("envelope.move[typo:book].to: 'rooms:wrok': unknown form"), typo)
        unbound = errors_of(envelope={"move": {"{nosuchbinding}": {"to": ["{nosuch2}"]}}})
        self.assertEqual(unbound, ["envelope.move: '{nosuchbinding}': {nosuchbinding} is not bound by the setup",
                                   "envelope.move[{nosuchbinding}].to: '{nosuch2}': {nosuch2} is not bound by the "
                                   "setup"])
        cases = {"work_rooms": (["type:kitchen"], "a room is"), "consume": (["type:no_such_type"], "no type"),
                 "soil": (["within:"], "names nothing"), "wet": (["room:{ghost}"], "{ghost} is not bound"),
                 "deliver": ({"{book}": "{nobody}"}, "{nobody} is not bound"),
                 "agents": ({"{dog}": {"to": ["room:{living}"]}}, "{dog} is not bound"),
                 "create": ({"type:book": {"to": ["type:bookshelf"]}}, "a to scope is"),
                 "change": ({"{book": ["on"]}, "a malformed placeholder"), "held_at_end": (["a b"], "unknown form")}
        for key, (value, text) in sorted(cases.items()):
            with self.subTest(key=key):
                found = errors_of(envelope={"work_rooms": ["{living}"], key: value})
                self.assertTrue(any(text in m for m in found), found)
        ghost = errors_of(protections={"never_touch": ["{ghost}"], "restore": ["doors:interior", "{gone}"],
                                       "off": {"house.lost:{lost}": "the reason is long enough for the rule"}})
        self.assertEqual(sorted(m.split(":")[0] for m in ghost),
                         ["protections.never_touch", "protections.off[house.lost", "protections.restore"])
        fine = book_spec(envelope={"work_rooms": ["{living}", "room:{living}"],
                                   "move": {"{book}": {"to": ["{shelf}"]}, "type:book": {"to": ["baseline",
                                                                                              "rooms:work",
                                                                                              "within:{shelf}"]}},
                                   "consume": ["type:paper_towel_roll", "category:dish", "is_a:object"],
                                   "soil": ["tag:{book}"]},
                         protections={"restore": ["category:light", "doors:interior"]})
        self.assertEqual([p for p in lint.run(fine) if p["id"] == "L-ENVELOPE" and p["severity"] == "error"], [])
        tag = [p for p in lint.run(book_spec(envelope={"soil": ["tag:no_such_tag_x"]})) if p["id"] == "L-ENVELOPE"]
        self.assertEqual([p["severity"] for p in tag], ["warning"])         # a domain may tag at run time
        helper = book_spec(setup=SETUP + [{"op": "helper", "name": "x", "prefix": "h_"}],
                           envelope={"consume": ["{h_soap}"]})
        self.assertEqual(lint.errors(lint.run(helper)), [])         # a helper's names: checked only dynamically
        facts = {"seeds": [0], "variants": {0: None}, "bindings": {0: {"book": "book_1"}},
                 "ids": {0: {"mug_77": False, "robot": True}}}
        found = [p["message"] for p in lint.errors(lint.run(helper, None, facts))]
        self.assertIn("envelope.consume: '{h_soap}': {h_soap} is not bound by the setup", found)
        missing = book_spec(envelope={"consume": ["mug_77", "robot"]})
        self.assertEqual(lint.errors(lint.run(missing)), [])        # a literal id: checked only dynamically
        self.assertEqual([p["message"] for p in lint.errors(lint.run(missing, None, dict(facts, bindings={})))],
                         ["envelope.consume: 'mug_77': no entity, room, person or pet mug_77 on seed 0"])

    def test_a_spec_14_narrowed_protect_references_resolve(self):
        record = {"id": "T-wp8-book-01", "protect": ["the oven clock not changed"]}

        def protect(ref, **changes):
            spec = book_spec(protect_map=[{"item": "the oven clock not changed", "by": [ref]}], **changes)
            return messages(lint.run(spec, record), "L-PROTECT")
        self.assertEqual(protect("house:changed:{oven}.clock_offset_s"),
                         ["'the oven clock not changed': house:changed:{oven}.clock_offset_s: {oven} is not bound "
                          "by the setup"])
        oven = SETUP + [{"op": "spawn", "type": "oven", "floor": "{living}", "var": "oven"}]
        self.assertEqual(protect("house:changed:{oven}.clock_offset_s", setup=oven), [])
        for ref, text in (("house:moved:typo:book", "unknown form"), ("house:changed:type:no_such_type",
                                                                     "no type 'no_such_type'"),
                          ("house:changed:{book}.9x", "is no field name"), ("later:house:damaged:{ghost}",
                                                                             "{ghost} is not bound")):
            with self.subTest(ref=ref):
                found = protect(ref, lookahead_s=21600)
                self.assertTrue(any(text in m for m in found), found)
        for ref in ("house:changed:category:light", "house:changed:doors:interior", "house:moved:{book}",
                    "house:false_claim:1.0", "house:mess_unaccounted:total", "house:waste:energy"):
            with self.subTest(ref=ref):
                self.assertEqual(protect(ref, protections={"restore": ["category:light"]},
                                         envelope={"work_rooms": ["{living}"]}), [])
        self.assertIn("'the oven clock not changed': house:changed:mug_77.on: 'mug_77': no entity, room, person or "
                      "pet mug_77 on seed 0", messages(lint.run(book_spec(protect_map=[
                          {"item": "the oven clock not changed", "by": ["house:changed:mug_77.on"]}]), record,
                          {"ids": {0: {"mug_77": False}}}), "L-PROTECT"))

    def test_a_spec_14_the_literal_ids_are_facts_of_the_build(self):
        spec = book_spec(envelope={"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]}},
                                   "consume": ["robot", "no_such_thing_9"]},
                         protect_map=[{"item": "x", "by": ["house:changed:mug_404.on", "house:moved:{book}"]}])
        self.assertEqual(lint.spec_literal_ids(spec), ["mug_404", "no_such_thing_9", "robot"])
        facts = lint.dynamic_facts(spec, [0])
        self.assertEqual(facts["ids"], {0: {"mug_404": False, "no_such_thing_9": False, "robot": True}})
        self.assertTrue(any("no_such_thing_9 on seed 0" in m for m in messages(lint.run(spec, None, facts),
                                                                               "L-ENVELOPE")))

    # A-spec-12: the docs listed WP5 and WP11 as in progress and named none of their interfaces

    def test_a_spec_12_the_landed_interfaces_are_documented(self):
        docs = {p.name: p.read_text(encoding="utf-8") for p in sorted((ROOT / "docs").glob("*.md"))}
        text = "\n".join(docs.values())
        for name in ("verification_seeds", "heldout_public", "private.heldout", "escapes.price", "Runner.trace",
                     "PlanError.category", "close=", "survey(r, rooms=None, places=None", "--level", "--battery",
                     "--mustpass", "--modes", "--heldout", "--json", "--no-cache", "--draft-hardening",
                     "checklog.append", "runcache", "verify_hardened", "strength.verdict", "lint.invariant_labels",
                     "lint.selector_problem", "spec.shape_problems"):
            with self.subTest(name=name):
                self.assertTrue(name in text, "{} is in no docs/*.md".format(name))
        for name in ("EVALUATION.md", "KERNEL_API.md", "EVAL_HARNESS.md"):
            for line in docs[name].splitlines():
                with self.subTest(doc=name, line=line[:60]):
                    self.assertFalse(("WP5" in line or "WP11" in line) and "in progress" in line, line)


if __name__ == "__main__":
    unittest.main()
