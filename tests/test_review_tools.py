"""The review, audit and evidence tools and the harness on schema 4 (docs/design/VERIFY_HARDENING.md 10.7, 15.5, 18, 19.4, 21,
22.6, 22.7, 22.9, 22.10; WP10).

tools/battery_review.py: mechanical checks, who may sign, blind review, the default-fix rule, the review budget, spot-checks.
tools/evidence_replay.py: change classes, the run fingerprint, carried / stale / overdue, the rolling run and its audit.
tools/harden_audit.py: the as-if spec and the cost calibration. house_sim/eval: verified_specs takes HARDENED, the manifest in
the evaluation mode with its drop rate, the episode's finish and idle counterfactual, the chance-corrected column.

Everything writes to temporary folders; the split key in the owner tests is a throwaway from the environment.
"""
import contextlib
import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sim.eval import agents as A
from house_sim.eval import leaderboard as lb
from house_sim.eval import run as R
from house_sim.eval import splits
from house_sim.kernel.plugins import load_domains
from house_sim.tasks import checklog, escapes, verify
from house_sim.tasks.spec import expand

from tests.test_verify_hardening import book_spec, legacy_spec
from tools import battery_review as review
from tools import evidence_replay as replay
from tools import harden_audit as audit

load_domains()
KEY = "ab" * 32
LOGGED_ARGS = ["--session", "rev-1", "--model", "m", "--prompt-digest", "p" * 16, "--transcript-digest", "t" * 16]


def record_of(spec, **more):
    out = {"format": "house_ai.verification/2", "catalog_id": spec["catalog_id"], "spec_hash": verify.spec_hash(spec),
           "fingerprint": "code", "verdict": "HARDENED", "gates": {}, "budget": {"formula_ok": True},
           "seeds": {"verification": [0, 1]}, "battery": {"runs": []}, "coverage": {"kind_class": "plain"}}
    out.update(more)
    return out


class ToolCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review_tools_"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.exemptions = self.tmp / "exemptions.jsonl"
        self.sessions = self.tmp / "sessions.jsonl"
        self.spec_file = self.tmp / "batch__a.json"
        self.evidence = self.tmp / "verification.jsonl"
        env = mock.patch.dict(os.environ, {checklog.DIR_ENV: str(self.tmp / "check_log")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("HOUSE_AI_SPLIT_KEY", None)
        os.environ.pop(checklog.SESSION_ENV, None)
        no_key = mock.patch("house_sim.tasks.private._key", lambda key=None: key)
        no_key.start()
        self.addCleanup(no_key.stop)

    def write_specs(self, *specs):
        self.spec_file.write_text(json.dumps({"specs": list(specs)}), encoding="utf-8")

    def write_record(self, record):
        self.evidence.write_text(json.dumps(record) + "\n", encoding="utf-8")

    def tool(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = review.main(["--spec-file", str(self.spec_file), "--evidence", str(self.evidence), "--exemptions",
                                str(self.exemptions), "--sessions", str(self.sessions)] + list(argv))
        return code, out.getvalue()

    def rows(self):
        return review.read_rows(self.exemptions)


def exempt_spec(category="budget_defense", policy="B10.report_all", **more):
    return book_spec(battery_exempt=[{"policy": policy, "category": category, "reason": "the author's reason"}], **more)


class AcceptanceTests(ToolCase):
    def test_an_agent_acceptance_logs_its_session_and_counts_in_g9(self):
        spec = exempt_spec()
        self.write_specs(spec)
        self.write_record(record_of(spec))
        code, text = self.tool("accept", spec["catalog_id"], "B10.report_all@3#1", "--reviewer", "agent", "--category",
                               "budget_defense", "--reason", "my reason", *LOGGED_ARGS)
        self.assertEqual(code, 0, text)
        self.assertIn("the author's reason", text)      # revealed after the reviewer wrote its own
        row = self.rows()[0]
        self.assertEqual((row["policy"], row["decision"], row["reason"]), ("B10.report_all", "accept", "my reason"))
        self.assertEqual(row["spec_hash"], verify.spec_hash(spec))
        self.assertTrue(row["mechanical_check"]["ok"])
        self.assertEqual(row["reviewer"]["session_id"], "rev-1")
        self.assertIn("behaviour_digest", row)
        priced = escapes.price(expand(spec), record_of(spec), exemptions_path=self.exemptions, reviews_path=self.sessions,
                               sessions=[])
        self.assertTrue(priced["g9"], priced["why"])
        self.assertEqual(priced["exemptions_accepted"], 1)

    def test_blind_review_refuses_another_category(self):
        spec = exempt_spec()
        self.write_specs(spec)
        self.write_record(record_of(spec))
        code, text = self.tool("accept", spec["catalog_id"], "B10.report_all", "--reviewer", "agent", "--category",
                               "safe_superset", *LOGGED_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("blind review", text)
        self.assertEqual(self.rows(), [])

    def test_a_session_in_the_check_log_or_without_its_digests_may_not_accept(self):
        spec = exempt_spec()
        self.write_specs(spec)
        self.write_record(record_of(spec))
        args = ("accept", spec["catalog_id"], "B10.report_all", "--reviewer", "agent", "--category", "budget_defense")
        code, text = self.tool(*(args + ("--session", "rev-1")))
        self.assertEqual(code, 1)
        self.assertIn("--model", text)
        with mock.patch.object(checklog, "sessions", lambda catalog_id, root=None: ["rev-1"]):
            code, text = self.tool(*(args + tuple(LOGGED_ARGS)))
        self.assertEqual(code, 1)
        self.assertIn("check log", text)
        self.assertEqual(self.rows(), [])

    def test_other_is_the_owners_and_the_owner_needs_the_split_key(self):
        spec = exempt_spec(category="other", policy="B7.dawdle")
        self.write_specs(spec)
        self.write_record(record_of(spec))
        code, text = self.tool("accept", spec["catalog_id"], "B7.dawdle", "--reviewer", "agent", "--category", "other",
                               *LOGGED_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("only by the owner", text)
        code, text = self.tool("accept", spec["catalog_id"], "B7.dawdle", "--reviewer", "owner", "--category", "other")
        self.assertEqual(code, 1)
        self.assertIn("split key", text)

    def test_the_owner_signs_where_the_split_key_is_and_a_forged_row_does_not_count(self):
        spec = exempt_spec(category="other", policy="B7.dawdle")
        self.write_specs(spec)
        self.write_record(record_of(spec))
        raw = bytes.fromhex(KEY)
        with mock.patch("house_sim.tasks.private._key", lambda key=None: raw):
            code, text = self.tool("accept", spec["catalog_id"], "B7.dawdle", "--reviewer", "owner", "--category", "other")
            self.assertEqual(code, 0, text)
            row = self.rows()[0]
            self.assertTrue(row["signature"])
            self.assertIsNone(escapes.ineligible(row, [], "other", {}))
            forged = dict(row, signature="0" * 64)
            self.assertIn("does not verify", escapes.ineligible(forged, [], "other", {}))

    def test_a_failing_mechanical_check_rejects_before_a_reviewer_counts(self):
        spec = exempt_spec()
        self.write_specs(spec)
        self.write_record(record_of(spec, budget={"formula_ok": False}))
        code, text = self.tool("accept", spec["catalog_id"], "B10.report_all", "--reviewer", "agent", "--category",
                               "budget_defense", *LOGGED_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("rejected automatically", text)
        self.assertEqual(self.rows()[0]["decision"], "reject")
        self.assertEqual(review.read_rows(self.sessions), [])      # no acceptance, no session counted for it
        self.assertEqual(escapes.accepted_records(spec["catalog_id"], verify.spec_hash(spec), self.exemptions), {})

    def test_unrealisable_is_battery_debt_with_its_refusals(self):
        spec = exempt_spec(category="unrealisable", policy="B3.floor")
        self.write_specs(spec)
        run = {"id": "B3.floor@0#0", "family": "B3", "outcome": "unrealised", "refusals": [["precondition", "too small"]]}
        self.write_record(record_of(spec, battery={"runs": [run]}))
        args = ("accept", spec["catalog_id"], "B3.floor", "--reviewer", "agent", "--category", "unrealisable")
        code, text = self.tool(*(args + tuple(LOGGED_ARGS)))
        self.assertEqual(code, 1)
        self.assertIn("--battery-owner", text)
        code, text = self.tool(*(args + tuple(LOGGED_ARGS) + ("--battery-owner",)))
        self.assertEqual(code, 0, text)

    def test_a_rejection_and_a_reversal_undo_an_acceptance(self):
        spec = exempt_spec()
        self.write_specs(spec)
        self.write_record(record_of(spec))
        self.tool("accept", spec["catalog_id"], "B10.report_all", "--reviewer", "agent", "--category", "budget_defense",
                  *LOGGED_ARGS)
        shash = verify.spec_hash(spec)
        self.assertIn("B10.report_all", escapes.accepted_records(spec["catalog_id"], shash, self.exemptions))
        code, text = self.tool("reverse", spec["catalog_id"], "B10.report_all", "--reviewer", "agent")
        self.assertEqual(code, 1)       # the owner's, where the key is
        with mock.patch("house_sim.tasks.private._key", lambda key=None: bytes.fromhex(KEY)):
            self.assertEqual(self.tool("reverse", spec["catalog_id"], "B10.report_all", "--reviewer", "owner")[0], 0)
        self.assertEqual(escapes.accepted_records(spec["catalog_id"], shash, self.exemptions), {})

    def test_the_blind_form_hides_the_author_until_revealed(self):
        spec = exempt_spec()
        self.write_specs(spec)
        run = {"id": "B10.report_all@0#0", "family": "B10", "outcome": "passes", "labels": []}
        self.write_record(record_of(spec, battery={"runs": [run]}))
        code, text = self.tool("show", spec["catalog_id"], "B10.report_all")
        self.assertEqual(code, 0, text)
        self.assertNotIn("the author's reason", text)
        self.assertIn("B10.report_all@0#0", text)
        code, text = self.tool("show", spec["catalog_id"], "B10.report_all", "--reveal")
        self.assertIn("the author's reason", text)
        code, text = self.tool("list", "--pending")
        self.assertEqual(code, 0)
        self.assertIn("B10.report_all", text)


class ExemptionLifeTests(unittest.TestCase):
    def test_a_policy_that_now_fails_makes_its_exemption_dormant_and_a_family_flip_asks_for_review(self):
        runs = [{"id": "B8.superset@0#0", "outcome": "fails"}, {"id": "B8.superset@1#0", "outcome": "blocked"},
                {"id": "B4.see@0#0", "outcome": "passes"}, {"id": "B8.cross@0#0", "outcome": "fails"}]
        states, flips = review.exemption_states({"battery": {"runs": runs}}, ["B8.superset", "B4.see"])
        self.assertEqual(states, {"B8.superset": "dormant", "B4.see": "active"})
        self.assertEqual(flips, [])
        runs.append({"id": "B8.cross@1#0", "outcome": "passes"})
        self.assertEqual(review.exemption_states({"battery": {"runs": runs}}, ["B8.superset"])[1], ["B8.cross"])
        self.assertEqual(review.exemption_states({"battery": {"runs": []}}, ["B8.superset"]), ({"B8.superset": "active"}, []))


class MechanicalCheckTests(ToolCase):
    def fake(self, recs):
        def execute(spec, seeds, family, base=None):
            return [(mock.Mock(effect={"tau": 300}, meta={}), rec, env) for rec, env in recs], []
        return execute

    def test_safe_superset_reads_the_ledgers_own_judgment(self):
        spec = book_spec()
        record = record_of(spec)
        clean = {"id": "B8.superset@0#0", "seed": 0, "outcome": "passes", "labels": []}
        out = review.check_safe_superset(spec, record, "B8.superset", self.fake([(clean, None)]))
        self.assertTrue(out["ok"], out)
        harmful = dict(clean, labels=["house.changed:oven.settings"])
        out = review.check_safe_superset(spec, record, "B8.superset", self.fake([(harmful, None)]))
        self.assertFalse(out["ok"])
        self.assertIn("house.changed:oven.settings", out["why"])
        later = dict(clean, labels=["later:house.waste:energy"])
        self.assertFalse(review.check_safe_superset(spec, record, "B8.superset", self.fake([(later, None)]))["ok"])
        unrealised = dict(clean, outcome="unrealised")
        self.assertFalse(review.check_safe_superset(spec, record, "B8.superset", self.fake([(unrealised, None)]))["ok"])
        self.assertFalse(review.check_safe_superset(spec, record, "B8.superset", self.fake([]))["ok"])
        self.assertFalse(review.check_safe_superset(spec, {}, "B8.superset", self.fake([(clean, None)]))["ok"])

    def test_world_resolves_needs_the_hazard_gone_before_the_first_effect(self):
        spec = book_spec()
        record = record_of(spec)

        class Monitor(object):
            def __init__(self, spans):
                self.spans = spans
                self.entries = {"timing": [{"label": "hazard"}]}

            def intervals(self, label, which="act"):
                return self.spans

        def env(spans):
            return mock.Mock(_monitor=Monitor(spans))
        rec = {"id": "B7.dawdle_blind@0#0", "outcome": "passes"}
        self.assertTrue(review.check_world_resolves(spec, record, "B7.dawdle_blind",
                                                    self.fake([(rec, env([[0.0, 240.0]]))]))["ok"])
        out = review.check_world_resolves(spec, record, "B7.dawdle_blind", self.fake([(rec, env([[0.0, 500.0]]))]))
        self.assertFalse(out["ok"])
        self.assertIn("not gone", out["why"])
        self.assertFalse(review.check_world_resolves(spec, record, "B7.dawdle_blind",
                                                     self.fake([(rec, env([[0.0, None]]))]))["ok"])

    def test_world_resolves_without_a_timing_item_shows_nothing(self):
        env = mock.Mock(_monitor=mock.Mock(entries={"timing": []}))
        rec = {"id": "B7.dawdle_blind@0#0", "outcome": "passes"}
        out = review.check_world_resolves(book_spec(), record_of(book_spec()), "B7.dawdle_blind", self.fake([(rec, env)]))
        self.assertFalse(out["ok"])

    def test_fungible_instance_reads_no_answer_or_evidence_field(self):
        spec = book_spec(fungible=["book"], success=[{"label": "shelved", "pred": {"path": "entities.{book}.location",
                                                                                     "op": "eq", "value": "{shelf}"}}])
        self.assertTrue(review.check_fungible_instance(spec, {}, "B4.see")["ok"])
        reading = copy.deepcopy(spec)
        reading["answers"] = {"mark": {"bind": "mark", "kind": "word", "evidence": [{"entity": "{book}", "field": "torn"}]}}
        reading["success"] = [{"label": "x", "pred": {"path": "entities.{book}.torn", "op": "is_false"}}]
        out = review.check_fungible_instance(reading, {}, "B4.see")
        self.assertFalse(out["ok"], out)
        answered = dict(spec, answers={"book": {"bind": "book", "kind": "entity"}})
        self.assertFalse(review.check_fungible_instance(answered, {}, "B4.see")["ok"])
        self.assertFalse(review.check_fungible_instance(book_spec(), {}, "B4.see")["ok"])     # no role named

    def test_budget_defense_never_for_a_place_answer(self):
        spec = book_spec()
        self.assertTrue(review.check_budget_defense(spec, {"budget": {"formula_ok": True}}, "x")["ok"])
        self.assertFalse(review.check_budget_defense(spec, {"budget": {"formula_ok": False}}, "x")["ok"])
        placed = book_spec(answers={"spot": {"bind": "shelf", "kind": "place"}})
        self.assertFalse(review.check_budget_defense(placed, {"budget": {"formula_ok": True}}, "x")["ok"])

    def test_a_real_superset_run_through_the_battery(self):
        """check_safe_superset over a real two-branch spec would take minutes; the wiring is pinned on the book task: the
        family's generator is reached and a spec with no second branch generates no run, which fails the check (P4)."""
        spec = book_spec()
        out = review.check_safe_superset(spec, {"seeds": {"verification": [0]}}, "B8.superset")
        self.assertFalse(out["ok"])
        self.assertIn("no run", out["why"])


class BudgetAndSpotCheckTests(ToolCase):
    def items(self, n, escapes_each=2, key="protections_off", reason="the same reason", source="tools__a.json"):
        rows = []
        for i in range(n):
            priced = {"escapes": [{"key": key, "reason": reason, "needs_review": True, "accepted": False}
                                  for _ in range(escapes_each)]}
            rows.append(({"catalog_id": "T-{:02d}".format(i)}, source, priced))
        return rows

    def test_the_review_budget_of_a_batch(self):
        fine = review.review_queue(self.items(50, 1))
        self.assertEqual(fine["batches"]["tools__a.json"]["budget"], 65)
        self.assertEqual(fine["paused"], [])
        over = review.review_queue(self.items(50, 2))
        self.assertEqual(over["paused"], ["tools__a.json"])
        self.assertEqual(over["batches"]["tools__a.json"]["over"], 35)

    def test_the_default_fix_rule_clusters_by_key_and_reason_class(self):
        queue = review.review_queue(self.items(10, 1))
        self.assertEqual(len(queue["default_fix"]), 1)
        self.assertEqual(queue["default_fix"][0]["reason_class"], "same reason")
        self.assertEqual(review.review_queue(self.items(9, 1))["default_fix"], [])
        other = self.items(5, 1) + [({"catalog_id": "T-{}".format(i)}, "tools__a.json",
                                     {"escapes": [{"key": "protections_off", "reason": "quite another thing",
                                                   "needs_review": True}]}) for i in range(5)]
        self.assertEqual(review.review_queue(other)["default_fix"], [])

    def test_a_configuration_routed_to_the_owner_may_not_accept(self):
        spec = exempt_spec()
        self.write_specs(spec)
        self.write_record(record_of(spec))
        for i in range(50):
            review.append_row(self.exemptions, dict(self.agent_row(i, config="m"), reviewer={
                "kind": "agent", "session_id": "s", "model": "m", "prompt_digest": "p" * 16}))
        code, text = self.tool("accept", spec["catalog_id"], "B10.report_all", "--reviewer", "agent", "--category",
                               "budget_defense", *LOGGED_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("go to the owner", text)

    def test_a_paused_batch_stops_agent_reviews(self):
        spec = exempt_spec()
        self.write_specs(spec)
        self.write_record(record_of(spec))
        with mock.patch.object(review, "_queue_items", lambda *a, **k: self.items(1, 3, source=self.spec_file.name)):
            code, text = self.tool("accept", spec["catalog_id"], "B10.report_all", "--reviewer", "agent", "--category",
                                   "budget_defense", *LOGGED_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("review pauses", text)

    def agent_row(self, i, decision="accept", config="m", date="2026-11-02"):
        return {"catalog_id": "T-{:03d}".format(i), "policy": "B3.floor", "spec_hash": "h{}".format(i), "decision": decision,
                "date": date, "reviewer": {"kind": "agent", "session_id": "s{}".format(i), "model": config,
                                           "prompt_digest": "p"}}

    def test_the_spot_check_samples_a_tenth_and_one_per_domain_per_week(self):
        rows = [self.agent_row(i, config="m{}".format(i % 10)) for i in range(200)]     # no configuration routed: 20 each
        sample = review.spot_sample(rows, lambda c: "tools")
        self.assertTrue(10 <= len(sample) <= 35, len(sample))
        self.assertEqual(sample, review.spot_sample(rows, lambda c: "tools"))     # deterministic
        one = review.spot_sample([self.agent_row(1)], lambda c: "tools")
        self.assertEqual(len(one), 1)                                             # the weekly minimum
        self.assertEqual(review.spot_sample([dict(self.agent_row(1), reviewer={"kind": "owner"})]), [])

    def test_a_reversal_doubles_the_sample_and_a_high_rate_routes_to_the_owner(self):
        rows = [self.agent_row(i) for i in range(10)]
        rows.append({"catalog_id": "T-000", "policy": "B3.floor", "spec_hash": "h0", "decision": "reverse",
                     "reviewer": {"kind": "owner"}})
        state = review.reviewer_configs(rows)["m|p"]
        self.assertEqual((state["reversed"], state["doubled_left"]), (1, 50))
        rows.append(self.agent_row(100))
        self.assertEqual(review.reviewer_configs(rows)["m|p"]["doubled_left"], 49)
        many = [self.agent_row(i) for i in range(50)]
        self.assertTrue(review.reviewer_configs(many)["m|p"]["routed_to_owner"])
        routed = [r for r in review.spot_sample(many, lambda c: "tools") if r["why"] == "routed_to_owner"]
        self.assertEqual(len(routed), 50)
        half = [self.agent_row(i, "accept" if i % 2 else "reject") for i in range(50)]
        self.assertFalse(review.reviewer_configs(half)["m|p"]["routed_to_owner"])


class ChangeClassTests(unittest.TestCase):
    def test_files_fall_into_their_classes(self):
        for rel, cls in (("house_sim/kernel/world.py", "simulation"), ("house_sim/systems/cleaning/floors.py", "simulation"),
                         ("house_sim/tasks/skills.py", "simulation"), ("house_sim/tasks/setup.py", "simulation"),
                         ("house_sim/generator/place.py", "simulation"), ("house_sim/data/types/cleaning.json", "simulation"),
                         ("house_sim/kernel/ledger.py", "evaluator"), ("house_sim/tasks/verify.py", "evaluator"),
                         ("house_sim/tasks/battery/generate.py", "evaluator"), ("house_sim/data/speech/lexicon.json", "evaluator"),
                         ("house_sim/data/verify/watch/cleaning.json", "evaluator"), ("house_sim/eval/run.py", "harness"),
                         ("tools/fuzz.py", "harness"), ("docs/REVIEW.md", "harness"), ("tests/test_x.py", "harness"),
                         ("tasks/authored/cleaning__examples.json", "spec")):
            self.assertEqual(replay.classify(rel), cls, rel)

    def test_every_evaluator_file_of_the_tree_is_in_a_component(self):
        """A file of the evaluator class in no component would change judgment and stale nothing."""
        root = Path(replay.ROOT)
        unmapped = []
        for path in list(root.joinpath("house_sim", "data", "verify").rglob("*.json")) + \
                list(root.joinpath("house_sim", "data", "speech").rglob("*.json")) + \
                list(root.joinpath("house_sim", "tasks", "battery").rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if replay.classify(rel) == "evaluator" and not replay.changed_components([rel]):
                unmapped.append(rel)
        for rel in replay.components()["classes"]["evaluator"]:
            if rel.endswith(".py") and not replay.changed_components([rel]):
                unmapped.append(rel)
        self.assertEqual(sorted(unmapped), [])

    def test_a_record_lists_the_components_its_runs_used(self):
        plain = replay.record_components(book_spec(), {})
        self.assertIn("ledger", plain)
        self.assertNotIn("speech", plain)
        said = book_spec(success=[{"label": "told", "pred": {"said": {"to": "ann", "asserts": [{"kind": "refusal"}]}}}])
        self.assertIn("speech", replay.record_components(said, {}))
        self.assertIn("timing", replay.record_components(book_spec(timing=[{"label": "t"}]), {}))


class ReplayStatusTests(unittest.TestCase):
    def setUp(self):
        self.spec = book_spec()
        self.record = record_of(self.spec, evidence={"run_fingerprint": "fp", "last_full": "2026-11-01", "footprint":
                                                     ["house_sim/kernel/world.py"], "components": ["ledger", "battery"],
                                                     "run_seeds": {"0": {"trace": "a"}}}, date="2026-11-01")

    def status(self, changed=(), replayed=None, today="2026-11-03", current="other"):
        classes = replay.split_classes(changed)
        return replay.replay_status(self.record, self.spec, current, today, replayed, changed, classes)

    def test_current_at_its_fingerprint(self):
        self.assertEqual(self.status(current="code")["status"], "current")

    def test_a_changed_spec_is_stale(self):
        self.assertEqual(replay.replay_status(self.record, book_spec(title="another"), "code", "2026-11-03")["status"], "stale")

    def test_an_evaluator_change_stales_the_records_that_used_the_component(self):
        out = self.status(["house_sim/kernel/ledger.py"])
        self.assertEqual(out["status"], "stale")
        self.assertIn("ledger", out["why"])
        # speech is no component of this record: its evidence stands
        self.assertEqual(self.status(["house_sim/kernel/speech.py"])["status"], "carried")

    def test_a_simulation_change_replays_and_carries_or_stales(self):
        same = self.status(["house_sim/kernel/world.py"], {"digest": "fp", "seeds": {"0": {"trace": "a"}}})
        self.assertEqual(same["status"], "carried")
        self.assertEqual(same["evidence"]["carried_from"], "code")
        self.assertEqual(same["evidence"]["replays"], ["2026-11-03"])
        other = self.status(["house_sim/kernel/world.py"], {"digest": "else", "seeds": {"0": {"trace": "b"}}})
        self.assertEqual(other["status"], "stale")
        self.assertIn("0", other["why"])
        self.assertEqual(self.status(["house_sim/kernel/world.py"], None)["status"], "stale")

    def test_a_change_outside_the_code_footprint_needs_no_replay(self):
        self.assertEqual(self.status(["house_sim/systems/food/model.py"])["status"], "carried")
        # data files and the generator meet every footprint
        self.assertTrue(replay.footprint_meets(["house_sim/kernel/world.py"], ["house_sim/data/types/food.json"]))
        self.assertTrue(replay.footprint_meets(["house_sim/kernel/world.py"], ["house_sim/generator/place.py"]))
        self.assertTrue(replay.footprint_meets(None, ["house_sim/systems/food/model.py"]))

    def test_a_harness_only_change_costs_nothing(self):
        self.assertEqual(self.status(["house_sim/eval/run.py", "docs/REVIEW.md"])["status"], "carried")

    def test_a_carried_record_is_overdue_after_14_days(self):
        self.assertEqual(self.status(["docs/REVIEW.md"], today="2026-11-15")["status"], "carried")
        out = self.status(["docs/REVIEW.md"], today="2026-11-16")
        self.assertEqual(out["status"], "overdue")
        self.assertIn("15 days", out["why"])

    def test_counts_as_current_reads_the_carried_block(self):
        block = {"carried_from": "x", "status": "carried", "last_full": "2026-11-01"}
        self.assertTrue(replay.counts_as_current({"evidence": block}, "2026-11-10"))
        self.assertFalse(replay.counts_as_current({"evidence": block}, "2026-11-20"))
        self.assertFalse(replay.counts_as_current({"evidence": {}}, "2026-11-10"))

    def test_release_gating_and_the_rolling_run(self):
        self.assertFalse(replay.release_needed(["a"] * 10, 100))
        self.assertTrue(replay.release_needed(["a"] * 11, 100))
        records = [{"catalog_id": "new", "evidence": {"last_full": "2026-11-02"}},
                   {"catalog_id": "old", "evidence": {"last_full": "2026-10-01"}},
                   {"catalog_id": "older", "evidence": {"last_full": "2026-09-01"}}]
        self.assertEqual(replay.rolling_due(records, "2026-11-03"), ["older", "old"])
        self.assertEqual(replay.rolling_due(records, "2026-11-03", limit=3), ["older", "old", "new"])

    def test_the_replay_miss_audit(self):
        old = {"verdict": "HARDENED", "gates": {"G3": True}, "battery": {"runs": [{"family": "B3", "outcome": "fails"}]}}
        same = copy.deepcopy(old)
        flipped = dict(copy.deepcopy(old), verdict="FAIL", gates={"G3": False})
        family = copy.deepcopy(old)
        family["battery"]["runs"][0]["outcome"] = "passes"
        out = replay.replay_misses({"a": old, "b": old, "c": old}, {"a": same, "b": flipped, "c": family})
        self.assertEqual(sorted(out["misses"]), ["b", "c"])
        self.assertIn("verdict HARDENED -> FAIL", out["misses"]["b"])
        self.assertIn("gate G3 True -> False", out["misses"]["b"])
        self.assertTrue(out["added_runs"])
        self.assertFalse(replay.replay_misses({"a": old}, {"a": same})["added_runs"])


class ReplayToolTests(unittest.TestCase):
    def test_the_tool_marks_records_and_writes_their_blocks_back(self):
        tmp = Path(tempfile.mkdtemp(prefix="replay_tool_"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        spec = book_spec()
        cid = spec["catalog_id"]
        record = record_of(spec, evidence={"last_full": "2026-11-01", "components": ["ledger"], "footprint": [
            "house_sim/kernel/world.py"], "run_fingerprint": "fp"}, date="2026-11-01")
        legacy = {"catalog_id": "T-old-01", "passed": True}
        path = tmp / "verification.jsonl"
        path.write_text(json.dumps(record) + "\n" + json.dumps(legacy) + "\n", encoding="utf-8")
        out = replay.run(path, ["docs/REVIEW.md"], today="2026-11-03", specs={cid: spec}, current="new")
        self.assertEqual((out["carried"], out["stale"], out["release"]), ([cid], [], False))
        self.assertEqual(replay.write_back(path, out["rows"]), 1)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["evidence"]["status"], "carried")
        self.assertEqual(rows[0]["evidence"]["carried_from"], "code")
        self.assertEqual(rows[1], legacy)                 # a legacy record is not this tool's
        self.assertTrue(replay.counts_as_current(rows[0], "2026-11-03"))
        self.assertIsNone(splits.hardened_problem(rows[0], verify.spec_hash(spec), "new", today="2026-11-03"))
        out = replay.run(path, ["house_sim/kernel/ledger.py"], today="2026-11-03", specs={cid: spec}, current="new")
        self.assertEqual(out["stale"], [cid])
        self.assertTrue(out["release"])                   # one spec of one: more than 10 %
        replay.write_back(path, out["rows"])
        again = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual((again["evidence"]["status"], again["evidence"]["stale_since"]), ("stale", "2026-11-03"))
        self.assertIsNotNone(splits.hardened_problem(again, verify.spec_hash(spec), "new", today="2026-11-03"))


class RunFingerprintTests(unittest.TestCase):
    def test_it_is_deterministic_and_sees_a_behaviour_change(self):
        spec = book_spec()
        one = replay.run_fingerprint(spec, [0], footprint=True)
        self.assertNotIn("error", one["seeds"]["0"], one["seeds"]["0"])
        self.assertIn("idle", one)
        self.assertTrue(one["footprint"])
        self.assertTrue(any(m.startswith("house_sim/kernel/") for m in one["footprint"]))
        self.assertEqual(replay.run_fingerprint(spec, [0])["digest"], one["digest"])
        self.assertEqual(replay.run_fingerprint(spec, [0])["seeds"], one["seeds"])
        moved = copy.deepcopy(spec)
        moved["reference"] = [{"do": "wait", "seconds": 5}] + moved["reference"]
        other = replay.run_fingerprint(moved, [0])
        self.assertNotEqual(other["seeds"]["0"]["trace"], one["seeds"]["0"]["trace"])
        self.assertNotEqual(other["digest"], one["digest"])
        stamped = replay.stamp_evidence(record_of(spec, seeds={"verification": [0]}), spec, fingerprint=one, today="2026-11-03")
        self.assertEqual((stamped["run_fingerprint"], stamped["last_full"]), (one["digest"], "2026-11-03"))
        self.assertIn("battery", stamped["components"])


class AuditTests(unittest.TestCase):
    def test_the_as_if_spec_fills_the_drafts_and_never_the_authored_spec(self):
        spec = legacy_spec()
        before = copy.deepcopy(spec)
        draft = {"envelope": {"move": {"{book}": {"to": ["{shelf}"]}}}, "protect_map": [{"item": "x", "waive": "y"}],
                 "instruction_roles": {"shelf": "destination"}, "process": ["cooled"], "lookahead_triggered": True,
                 "lookahead_s": 21600.0, "answers": {"candidates": ["book"]},
                 "wrong_observed": {spec["wrong"][0]["name"]: ["goal:book_shelved"]}}
        target, notes = audit.as_if(spec, draft)
        self.assertEqual(spec, before)
        self.assertEqual(target["schema"], 4)
        self.assertEqual(target["instruction_roles"], {"shelf": "destination"})
        self.assertEqual(target["envelope"], draft["envelope"])
        self.assertEqual(target["process"], ["cooled"])
        self.assertEqual(target["lookahead_s"], 21600.0)
        self.assertEqual(target["answers"], {"book": {"bind": "book", "kind": "entity"}})
        self.assertEqual(target["wrong"][0]["expect"], ["goal:book_shelved"])
        self.assertEqual(target["wrong"][0]["mistake"], [1])       # the grammar of 14.1: a stand-in the stages can read
        self.assertEqual(notes["expect_missing"][0]["observed"], ["goal:book_shelved"])
        self.assertEqual(len(notes["expect_missing"]), 2)

    def test_the_audit_row_reports_g2_as_expect_missing_and_writes_no_check_log(self):
        tmp = tempfile.mkdtemp(prefix="audit_log_")
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {checklog.DIR_ENV: tmp}):
            row = audit.audit_spec({"spec": legacy_spec(), "level": "quick", "file": "books__a.json"})
        self.assertEqual(os.listdir(tmp), [])
        self.assertFalse(row.get("harness_errors"), row)
        self.assertFalse([p for p in row["lint"] if p["id"] == "STRUCTURE"], row["lint"])      # the stages ran
        self.assertTrue(row["gates"]["G1"], row["gates"])
        self.assertFalse(row["gates"]["G2"])
        self.assertIn("expect missing", row["g2_note"])
        self.assertTrue(row["expect_suggestions"])
        self.assertIn("envelope", row["drafted"])
        self.assertEqual(row["file"], "books__a.json")

    def test_summary_by_domain_and_stratum(self):
        rows = [{"catalog_id": "a", "file": "cleaning__x.json", "verdict": "HARDENED", "kind_class": "plain",
                 "battery_debt": {"B3": 2}, "counterfactual_charges": 1, "expect_suggestions": [{}], "lint": [{}]},
                {"catalog_id": "b", "file": "cleaning__x.json", "verdict": "FAIL", "kind_class": "plain"},
                {"catalog_id": "c", "file": "food__y.json", "verdict": "FAIL", "kind_class": "speech"}]
        out = audit.summary_of(rows)
        self.assertEqual(out["verdicts"], {"HARDENED": 1, "FAIL": 2})
        self.assertEqual(out["domains"]["cleaning"]["battery_debt"], {"B3": 2})
        self.assertEqual(out["domains"]["cleaning"]["counterfactual_charges"], 1)
        self.assertEqual(out["strata"]["plain/cleaning"], {"specs": 2, "hardened": 1, "rate": 0.5})
        self.assertEqual(out["strata"]["speech/food"]["rate"], 0.0)
        self.assertIn("plain/cleaning", audit.render_summary(out))

    def test_the_calibration_alarm_and_its_work_item(self):
        planning = {"per_spec": {"H0": {"full": [320, 298], "standard": [214, 192]}}, "calibration_alarm": 0.25}
        cheap = [{"catalog_id": "a", "tier": "H0", "level": "full", "cpu_s": 300.0, "cpu_by_stage": {"S5": 100.0}}]
        out = audit.cost_report(records=cheap, planning=planning)
        self.assertEqual(out["alarms"], [])
        self.assertTrue(out["projection"]["within_contingency"])
        heavy = [{"catalog_id": "b", "tier": "H0", "level": "full", "cpu_s": 500.0, "cpu_by_stage": {"S5": 400.0, "S2": 100.0}}]
        out = audit.cost_report(records=heavy, planning=planning)
        self.assertEqual(len(out["alarms"]), 1)
        self.assertIn("+56%", out["alarms"][0])
        self.assertEqual(out["work_items"][0]["owner"], "WP6")      # the battery's stage
        self.assertEqual(out["work_items"][0]["specs"], ["b"])
        self.assertFalse(out["projection"]["within_contingency"])
        self.assertIn("ALARM", audit.render_cost(out))

    def test_the_real_budget_file_is_what_it_reads(self):
        planning = audit._planning()
        self.assertIn("H0", planning["per_spec"])
        self.assertAlmostEqual(sum(audit.CATALOG_MIX.values()), 1.0)
        # 22.4: about 390 CPU-s on the catalog's mix, about 295 CPU-hours for the 2,720 entries at the full level
        self.assertAlmostEqual(audit.cost_report(records=[])["projection"]["planned_cpu_hours"], 295, delta=5)


class SplitsTests(unittest.TestCase):
    def test_a_schema_4_spec_needs_a_hardened_record(self):
        digest = "d"
        good = {"format": splits.EVIDENCE_V2, "verdict": "HARDENED", "spec_hash": digest, "fingerprint": "code"}
        self.assertIsNone(splits.hardened_problem(good, digest, "code"))
        self.assertIn("no verification", splits.hardened_problem(None, digest, "code"))
        self.assertIn("evidence record v2", splits.hardened_problem({"passed": True, "spec_hash": digest}, digest, "code"))
        for verdict in ("PENDING_GATE", "PENDING_REVIEW", "PROVISIONAL", "FAIL"):
            # PENDING_GATE passes --check on an authoring machine: no HARDENED
            self.assertIn(verdict, splits.hardened_problem(dict(good, verdict=verdict, passed=True), digest, "code"))
        self.assertIn("spec changed", splits.hardened_problem(good, "other", "code"))
        self.assertIn("implementation", splits.hardened_problem(good, digest, "new"))

    def test_a_carried_record_counts_until_its_staleness_limit(self):
        digest = "d"
        carried = {"format": splits.EVIDENCE_V2, "verdict": "HARDENED", "spec_hash": digest, "fingerprint": "old",
                   "evidence": {"status": "carried", "carried_from": "old", "last_full": "2026-11-01"}}
        self.assertIsNone(splits.hardened_problem(carried, digest, "new", today="2026-11-15"))
        self.assertIn("overdue", splits.hardened_problem(carried, digest, "new", today="2026-11-16"))

    def test_verified_specs_takes_hardened_for_schema_4_and_keeps_legacy_until_r4(self):
        hard, weak, legacy = book_spec(catalog_id="T-a-01"), book_spec(catalog_id="T-b-01"), legacy_spec()
        legacy["catalog_id"] = "T-c-01"
        items = [{"spec": hard}, {"spec": weak}, {"spec": legacy}]
        evidence = [record_of(hard, fingerprint="code"), record_of(weak, fingerprint="code", verdict="PENDING_GATE", passed=True),
                    {"catalog_id": "T-c-01", "passed": True, "implementation_hash": "code", "spec_hash": verify.spec_hash(legacy)}]
        root = Path(tempfile.mkdtemp(prefix="splits_"))
        self.addCleanup(shutil.rmtree, str(root), True)
        (root / "reports").mkdir()
        (root / "reports" / "verification.jsonl").write_text("\n".join(json.dumps(e) for e in evidence) + "\n", encoding="utf-8")
        with mock.patch("house_sim.audit.fingerprint", lambda r=None: "code"):
            kept, rejected = splits.filter_verified(items, root=root)
            self.assertEqual(sorted(i["spec"]["catalog_id"] for i, _ in kept), ["T-a-01", "T-c-01"])
            self.assertIn("PENDING_GATE", dict((i["spec"]["catalog_id"], why) for i, why in rejected)["T-b-01"])
            with mock.patch.dict(os.environ, {splits.HARDENED_ONLY_ENV: "1"}):       # R4
                kept, rejected = splits.filter_verified(items, root=root)
                self.assertEqual([i["spec"]["catalog_id"] for i, _ in kept], ["T-a-01"])

    def stratum_record(self, cid, kind, domain):
        return {"catalog_id": cid, "domain": domain, "verification": {"coverage": {"kind_class": kind}}}

    def test_the_draw_keeps_every_stratum_at_three_quarters_of_its_catalog_share(self):
        plain = [self.stratum_record("p{}".format(i), "plain", "food") for i in range(90)]
        talk = [self.stratum_record("t{}".format(i), "speech", "food") for i in range(5)]
        catalog = {("plain", "food"): 50, ("speech", "food"): 50}
        out = splits.stratified(plain + talk, catalog)
        kinds = [splits.stratum_of(r)[0] for r in out["selected"]]
        self.assertGreaterEqual(kinds.count("speech") / float(len(kinds)), 0.375)
        self.assertEqual(kinds.count("speech"), 5)      # nothing of the short stratum is thinned
        self.assertEqual(out["shortfall"], {})
        none = splits.stratified(plain, catalog)
        self.assertIn("speech/food", none["shortfall"])
        self.assertEqual(none["shortfall"]["speech/food"]["have"], 0)
        self.assertEqual(len(none["selected"]), 90)
        self.assertEqual(splits.stratified(plain + talk)["shortfall"], {})
        self.assertEqual(splits.stratified(plain + talk, catalog), splits.stratified(list(reversed(plain + talk)), catalog))


class ManifestTests(unittest.TestCase):
    def job(self, spec, hardened=True, excluded=None):
        return {"catalog_id": spec["catalog_id"], "spec": spec, "candidates": [900000000 + i for i in range(30)], "k": 3,
                "k_requested": 3, "mode": None, "pool": None, "spec_hash": "h", "hardened": hardened, "excluded": excluded}

    def test_no_hardened_evidence_no_candidates(self):
        entry = splits._entry_job(self.job(book_spec(), excluded="no HARDENED evidence: verdict PENDING_GATE"))
        self.assertEqual(entry["seeds"], [])
        self.assertFalse(entry["complete"])
        self.assertIn("PENDING_GATE", entry["error"])
        self.assertEqual(entry["excluded"], "no HARDENED evidence: verdict PENDING_GATE")

    def test_candidates_run_in_the_evaluation_mode_and_a_high_drop_rate_is_skill_debt(self):
        seen = []

        def trial(spec, seed, mode=None, **kw):
            seen.append(mode)
            return {"passed": seed % 3 != 1, "reference_error": "fell on the winder stairs"}
        with mock.patch.object(verify, "run_trial", trial):
            entry = splits._entry_job(self.job(book_spec()))
        self.assertEqual(set(seen), set(verify.eval_modes()))
        self.assertEqual(entry["perception"], "realistic")
        self.assertGreater(entry["drop_rate"], 0.10)
        self.assertFalse(entry["complete"])
        self.assertEqual(entry["excluded"], "drop_rate")
        self.assertIn("skill debt", entry["error"])
        self.assertTrue(entry["skill_debt"])

    def test_a_low_drop_rate_is_recorded_and_a_legacy_entry_keeps_its_own_mode(self):
        with mock.patch.object(verify, "run_trial", lambda spec, seed, mode=None, **kw: {"passed": True}):
            entry = splits._entry_job(self.job(book_spec()))
        self.assertTrue(entry["complete"])
        self.assertEqual(entry["drop_rate"], 0.0)
        calls = []

        def legacy_trial(spec, seed, perception=None):
            calls.append(perception)
            return {"passed": seed != 900000000, "reference_error": "x"}
        spec = legacy_spec()
        with mock.patch.object(verify, "run_trial", legacy_trial):
            entry = splits._entry_job(self.job(spec, hardened=False))
        self.assertEqual(set(calls), {None})
        self.assertTrue(entry["complete"])           # a legacy spec's drops are recorded, nothing more
        self.assertEqual(entry["drop_rate"], 0.25)

    def test_build_test_manifest_reads_the_evidence(self):
        spec = book_spec()
        good = record_of(spec, fingerprint="code")
        with mock.patch("house_sim.audit.fingerprint", lambda r=None: "code"), \
                mock.patch.object(verify, "run_trial", lambda s, seed, mode=None, **kw: {"passed": True}):
            built = splits.build_test_manifest([spec], k=2, workers=1, key=KEY, phrasings={}, evidence={spec["catalog_id"]: good})
            self.assertTrue(built[spec["catalog_id"]]["complete"])
            built = splits.build_test_manifest([spec], k=2, workers=1, key=KEY, phrasings={}, evidence={})
            self.assertFalse(built[spec["catalog_id"]]["complete"])
            self.assertIn("no HARDENED evidence", built[spec["catalog_id"]]["error"])


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review_run_"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        R._IDLE_CACHE.clear()

    def episode(self, spec, seed=0):
        return {"catalog_id": spec["catalog_id"], "split": "dev", "seed": seed, "seed_tag": "s{}".format(seed),
                "perception": "realistic"}

    def run_one(self, spec, agent):
        with mock.patch.object(R, "ROOT", self.tmp):
            return R._run_episode(spec, self.episode(spec), agent, out_dir=self.tmp / "out", perception="realistic",
                                  save_replay=False)

    def test_a_schema_4_episode_ends_with_finish_and_its_idle_counterfactual(self):
        spec = book_spec()
        agent = A.make_agent("oracle")
        record, extra = self.run_one(spec, agent)
        self.assertEqual(extra["error"], None)
        self.assertTrue(record["success"], record["failures"])
        self.assertEqual(record["failures"], [])
        # the rollout is cached: on disk for a public seed, keyed by the spec, seed, modes, fingerprint and data
        cached = list((self.tmp / "reports" / "cache" / "idle").glob("*.json"))
        self.assertEqual(len(cached), 1)
        self.assertIn("checkpoints", json.loads(cached[0].read_text(encoding="utf-8")))

    def test_an_agent_that_does_nothing_is_judged_as_the_verifier_judges(self):
        spec = book_spec()
        record, _ = self.run_one(spec, _Script([]))
        self.assertFalse(record["success"])
        self.assertAlmostEqual(record["goal_fraction"], 0.0)

    def test_holding_the_book_fails_on_the_ledger_in_a_real_episode(self):
        """The schema-4 ending in a real episode: a legacy evaluate() would only see the goal unmet; finish sees the house."""
        from house_sim.tasks.runner import build
        spec = book_spec()
        book = build(spec, 0)[2]["bindings"]["book"]
        agent = _Script([{"action": "approach", "target": book}, {"action": "pick_up", "target": book}])
        with mock.patch.object(R, "ROOT", self.tmp):
            record, _ = R._run_episode(spec, dict(self.episode(spec), perception="oracle"), agent, out_dir=self.tmp / "out",
                                       perception="oracle", save_replay=False)
        self.assertFalse(record["success"])
        self.assertIn("house.held:{}".format(book), record["failures"])
        self.assertIn("house.held:{}".format(book), record["side_effects"])
        self.assertNotIn("house.held:{}".format(book), record["harm_flags"])

    def test_the_same_rollout_is_reused_and_a_private_seed_stays_in_the_process(self):
        from house_sim.tasks.runner import build
        spec = book_spec()
        config, task, _ = build(spec, 0, perception="realistic")
        calls = []
        real = verify.idle_rollout

        def counting(env, t, horizon_s=None):
            calls.append(1)
            return real(env, t, horizon_s)
        with mock.patch.object(R, "ROOT", self.tmp), mock.patch.object(verify, "idle_rollout", counting):
            one = R.idle_counterfactual(spec, config, task, 0, "realistic", "room", private=True, code="c")
            self.assertEqual(list((self.tmp).rglob("*.json")), [])      # nothing of a private seed on disk
            two = R.idle_counterfactual(spec, config, task, 0, "realistic", "room", private=True, code="c")
        self.assertEqual(len(calls), 1)
        self.assertEqual(one.digest(), two.digest())

    def test_an_agents_branch_decision_is_gated_with_the_seeds_diff_set(self):
        """A coin flip on a conditional fails in the harness too: the seed's branch diff set reaches the agent rule."""
        spec = book_spec()
        task = {"hardening": {"answers": {"kept": {"kind": "branch", "value": "keep"}}}}
        with mock.patch.object(verify, "_perceive_answers", lambda t: t["hardening"]["answers"]), \
                mock.patch.object(verify, "reference_run", self.fake_reference()), \
                mock.patch("house_sim.tasks.seeds.coverage", lambda s: {"seeds": [0, 1]}), \
                mock.patch("house_sim.tasks.runner.build", lambda s, seed, **kw: ({}, {}, {"bindings": {}, "branch": seed % 2})):
            diff = R.branch_diff_for(spec, task, 3, "room", code="c")     # seed 3: the branch with the place
        self.assertEqual([c["action"] for c in diff], ["place"])
        self.assertIsNone(R.branch_diff_for(spec, {"hardening": {"answers": {}}}, 2, "room", code="c"))
        seen = {}

        def finish(env, t, mode, branch_diff=None):
            seen.update(mode=mode, diff=branch_diff)
            return {"success": True}
        env = mock.Mock(camera_mode="room")
        with mock.patch.object(verify, "_finish", finish), mock.patch.object(R, "branch_diff_for", lambda *a, **k: ["d"]):
            R.episode_end(env, spec, {}, "realistic", 2)
        self.assertEqual(seen, {"mode": verify.EVAL_GEOM, "diff": ["d"]})
        with mock.patch.object(verify, "_finish", finish):
            R.episode_end(env, spec, {}, "oracle", 2)
        self.assertEqual(seen, {"mode": verify.OWN, "diff": None})

    def test_a_real_two_branch_spec_has_a_diff_set(self):
        from house_sim.tasks.runner import build
        from tests.test_battery import branch_spec
        spec = branch_spec()
        task = build(spec, 0, perception="realistic")[1]
        diff = R.branch_diff_for(spec, task, 0, "geometric", code="c")
        self.assertTrue(diff, "the two branches' references differ")
        self.assertTrue(all("action" in c for c in diff))
        self.assertEqual(R.branch_diff_for(spec, task, 0, "geometric", code="c"), diff)     # cached

    @staticmethod
    def fake_reference():
        def run(config, task, oracle, seed, mode, idle):
            trace = [{"action": "approach", "target": "x", "args": {}, "ok": True}]
            if oracle["branch"]:
                trace.append({"action": "place", "target": "x", "destination": "y", "args": {}, "ok": True})
            return {}, None, mock.Mock(trace=trace)
        return run

    def test_the_cache_key_holds_everything_a_rollout_depends_on(self):
        spec = book_spec()
        base = R.idle_key(spec, 0, "realistic", "room", code="c")
        self.assertEqual(base, R.idle_key(spec, 0, "realistic", "room", code="c"))
        for other in (R.idle_key(spec, 1, "realistic", "room", code="c"), R.idle_key(spec, 0, "oracle", "room", code="c"),
                      R.idle_key(spec, 0, "realistic", "geometric", code="c"), R.idle_key(spec, 0, "realistic", "room", code="d"),
                      R.idle_key(book_spec(title="x"), 0, "realistic", "room", code="c")):
            self.assertNotEqual(base, other)
        with mock.patch.object(R, "IDLE_FORMAT", 1):         # a rollout from before WP1-fix's projections never counts
            self.assertNotEqual(base, R.idle_key(spec, 0, "realistic", "room", code="c"))
        with mock.patch.object(R, "_ledger_data_digest", lambda: "other data"):
            self.assertNotEqual(base, R.idle_key(spec, 0, "realistic", "room", code="c"))

    def test_a_legacy_episode_is_as_it_was(self):
        spec = legacy_spec()
        env = mock.Mock(idle=None)
        R.episode_start(env, spec, {}, {}, 0, "realistic", "room")
        self.assertIsNone(env.idle)
        env.evaluate.return_value = {"success": True}
        self.assertEqual(R.episode_end(env, spec, {}, "realistic"), {"success": True})
        env.finish.assert_not_called()

    def test_the_ledgers_labels_are_side_effects_not_harm(self):
        harms, side = R._classify(["house.moved:cup", "later:house.waste:energy", "settle:cup_on_table",
                                   "perceive:claim_unperceived:1", "fire"])
        self.assertEqual(harms, ["fire"])
        self.assertEqual(side, ["house.moved:cup", "later:house.waste:energy"])
        self.assertEqual(lb.harm_labels(["house.moved:cup", "settle:x", "perceive:y", "fire"]), ["fire"])

    def test_the_replays_agree_with_the_episode(self):
        spec = book_spec()
        agent = A.make_agent("oracle")
        out = self.tmp / "out"
        with mock.patch.object(R, "ROOT", self.tmp):
            record, _ = R._run_episode(spec, self.episode(spec), agent, out_dir=out, perception="realistic", save_replay=False)
            logs = list((out / "logs").glob("*.json")) if (out / "logs").is_dir() else []
            self.assertEqual(len(logs), 1)
            log = json.loads(logs[0].read_text(encoding="utf-8"))
            again = R.replay_log(log, spec, seed=0)
        self.assertEqual(again["success"], record["success"])
        self.assertEqual(list(again["evaluation"].get("failures") or []), record["failures"])
        with mock.patch.object(R, "ROOT", self.tmp):
            scores, issues, _ = lb.replay_episode(spec, 0, log, perception="realistic", camera=log.get("camera"),
                                                  nonce=R.log_nonce(log))
        self.assertEqual(issues, [])
        self.assertEqual((scores["success"], list(scores["failures"])), (record["success"], record["failures"]))


class _Script(A.Agent):
    """An agent with a fixed command list, then done."""

    def __init__(self, commands):
        A.Agent.__init__(self)
        self.commands = list(commands)
        self.name = "script"

    def act(self, observation, last):
        return self.commands.pop(0) if self.commands else {"action": "done"}


class LeaderboardTests(unittest.TestCase):
    def unit(self, cid, success):
        return [{"catalog_id": cid, "success": success}]

    def test_the_chance_corrected_score(self):
        statuses = {"a": {"best_constant_share": 0.4, "kind_class": "lookalike"},
                    "b": {"best_constant_share": 0.0, "kind_class": "plain"}, "c": {}}
        units = [self.unit("a", True), self.unit("a", False), self.unit("b", True), self.unit("c", True)]
        out = lb.chance_corrected(units, statuses)
        self.assertAlmostEqual(out["value"], ((0.5 - 0.4) + (1.0 - 0.0)) / 2, places=3)
        self.assertEqual((out["specs"], out["uncorrected_specs"]), (2, 1))
        self.assertAlmostEqual(out["by_kind"]["lookalike"]["value"], 0.1, places=3)
        self.assertEqual(out["by_kind"]["plain"]["specs"], 1)
        self.assertIsNone(lb.chance_corrected([self.unit("c", True)], statuses)["value"])

    def test_the_column_is_in_every_rendering(self):
        self.assertIn("Chance-corrected", [name for name, _ in lb.MAIN_COLUMNS])
        self.assertIn("chance_corrected", lb.CSV_FIELDS)
        row = {"chance_corrected": {"value": 0.25, "specs": 3}}
        self.assertEqual(lb._fmt_chance(row), "+25.0% (3 specs)")
        self.assertEqual(lb._fmt_chance({"chance_corrected": {"value": None}}), "n/a")

    def test_spec_status_reads_the_hardened_record(self):
        hard, weak = book_spec(catalog_id="T-a-01"), book_spec(catalog_id="T-b-01")
        evidence = {"T-a-01": record_of(hard, fingerprint="code", randomisation={"best_constant_share": 0.3},
                                        coverage={"kind_class": "lookalike"}),
                    "T-b-01": record_of(weak, fingerprint="code", verdict="PENDING_GATE", passed=True)}
        with mock.patch("house_sim.audit.fingerprint", lambda r=None: "code"), \
                mock.patch.object(lb, "_evidence", lambda: evidence):
            out = lb.spec_status(["T-a-01", "T-b-01"], specs={"T-a-01": hard, "T-b-01": weak})
        self.assertEqual(out["T-a-01"]["status"], "verified")
        self.assertEqual((out["T-a-01"]["best_constant_share"], out["T-a-01"]["kind_class"]), (0.3, "lookalike"))
        self.assertEqual(out["T-b-01"]["status"], "unverified")
        self.assertIn("PENDING_GATE", out["T-b-01"]["reason"])


class DataFileTests(unittest.TestCase):
    def test_the_review_folder_exists_and_docs_cover_every_category(self):
        root = Path(replay.ROOT)
        for rel in ("catalog/review/exemptions.jsonl", "catalog/review/review_sessions.jsonl", "docs/REVIEW.md",
                    "house_sim/data/verify/components.json"):
            self.assertTrue((root / rel).is_file(), rel)
        self.assertTrue((root / "catalog" / "review" / "check_log").is_dir())
        text = (root / "docs" / "REVIEW.md").read_text(encoding="utf-8")
        for category in review.CATEGORIES:
            self.assertIn("`{}`".format(category), text)
        for row in review.read_rows(root / "catalog" / "review" / "exemptions.jsonl"):
            self.assertIn("decision", row)


if __name__ == "__main__":
    unittest.main()
