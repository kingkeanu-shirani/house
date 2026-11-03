"""Audits over extra houses: tells, balance, best constants, fixed-timer exposure and nuisance features
(docs/design/VERIFY_HARDENING.md 9.3, 9.6, 9.7; WP6, house_sim/tasks/battery/audits.py).

The statistics are tested on synthetic data with exact expectations (the binomial bound of 9.7: more than 120 of
200 at k = 2 and 87 or more at k = 3 fail). The audit itself runs the real setup fast path (WP9) on one generated
house reused for every audit seed (the house generator is not what is audited: the setup's draws and placements
are), so a whole audit takes seconds; the house cache path (generator/disk_cache) is exercised by the default.
"""
import copy
import json
import random
import unittest

from house_sim.generator import generate_house
from house_sim.kernel.plugins import load_domains
from house_sim.tasks import battery
from house_sim.tasks.battery import audits
from tests.test_battery import BLEACH_SETUP, bleach_spec, book_spec

_HOUSE = {}


def one_house(seed, kwargs):
    """Every audit seed on the same generated house (fast); the setup still draws per seed."""
    key = json.dumps(kwargs, sort_keys=True)
    if key not in _HOUSE:
        _HOUSE[key] = generate_house(100000, **kwargs)
    return copy.deepcopy(_HOUSE[key])


def leftmost_spec():
    """Three sprays arranged left to right in the given order, the bleach always the first: a positional tell."""
    setup = [op for op in BLEACH_SETUP if op.get("op") not in ("spawn", "choose", "set")]
    setup[3:3] = [{"op": "spawn", "type": "spray_bottle", "on": "{counter}", "var": "b1",
                   "props": {"label_text": "BLEACH"}},
                  {"op": "spawn", "type": "spray_bottle", "on": "{counter}", "var": "b2",
                   "props": {"label_text": "WATER"}},
                  {"op": "spawn", "type": "spray_bottle", "on": "{counter}", "var": "b3",
                   "props": {"label_text": "WATER"}},
                  {"op": "arrange", "items": ["{b1}", "{b2}", "{b3}"], "on": "{counter}", "order": "given",
                   "gap_m": [0.05, 0.1]},
                  {"op": "shuffle", "var": "bottles", "from": ["{b1}", "{b2}", "{b3}"]},
                  {"op": "bind", "var": "bleach", "value": "{b1}"}]
    return bleach_spec(catalog_id="T-wp6-tell-01", setup=setup)


def time_spec(lo, hi):
    setup = copy.deepcopy(book_spec()["setup"])
    setup.insert(1, {"op": "time", "var": "onset", "min": lo, "max": hi, "step_s": 30})
    return book_spec(catalog_id="T-wp6-time-01", setup=setup,
                     answers={"onset": {"bind": "onset", "kind": "time", "tol": 60}},
                     budget={"steps": 40, "time_s": 3600})


class StatisticsTests(unittest.TestCase):
    def test_the_binomial_tail_is_exact(self):
        self.assertAlmostEqual(audits.binom_tail(1, 1, 0.5), 0.5)
        self.assertAlmostEqual(audits.binom_tail(2, 2, 0.5), 0.25)
        self.assertAlmostEqual(audits.binom_tail(0, 10, 0.3), 1.0)
        self.assertAlmostEqual(audits.binom_tail(3, 3, 1.0 / 3), 1.0 / 27)

    def test_the_tell_bounds_of_9_7(self):
        def ranks(top, n, k):
            return [0] * top + [1 + (i % (k - 1)) for i in range(n - top)]
        self.assertTrue(audits.tell_check(ranks(120, 200, 2), 2, 4)[0])
        self.assertFalse(audits.tell_check(ranks(121, 200, 2), 2, 4)[0])
        self.assertTrue(audits.tell_check(ranks(86, 200, 3), 3, 4)[0])
        self.assertFalse(audits.tell_check(ranks(87, 200, 3), 3, 4)[0])
        # the review's bottle statistic (26 of 40 at k = 3) fails
        ok, detail = audits.tell_check(ranks(26, 40, 3), 3, 4)
        self.assertFalse(ok)
        self.assertEqual(detail["rank"], 0)

    def test_the_decision_tree_finds_a_nuisance_feature(self):
        rng = random.Random(7)
        X, y = [], []
        for i in range(200):
            room = rng.choice(["kitchen", "hall", "den"])
            X.append({"start_room": room, "noise": rng.random(), "phrasing": rng.randint(0, 3)})
            y.append({"kitchen": "0", "hall": "1"}.get(room, rng.choice(["1", "2"])) if rng.random() < 0.9 else
                     rng.choice(["0", "1", "2"]))
        ok, detail = audits.tree_check(X, y)
        self.assertFalse(ok, detail)
        self.assertGreaterEqual(detail["accuracy"], detail["bound"])
        self.assertEqual(detail["root_split"][1], "start_room")
        # a feature that only shifts the odds a little stays under the bound (base rate + 0.15, at least 0.7)
        weak = ["0" if (x["start_room"] == "kitchen" and rng.random() < 0.4) else rng.choice(["0", "1", "2"])
                for x in X]
        self.assertTrue(audits.tree_check(X, weak)[0])
        noise = [rng.choice(["0", "1", "2"]) for _ in range(200)]
        ok, detail = audits.tree_check(X, noise)
        self.assertTrue(ok, detail)
        self.assertEqual(audits.tree_check(X, noise), audits.tree_check(X, noise))      # deterministic

    def test_fixed_timer_exposure_is_arithmetic(self):
        item = {"label": "close", "trigger": {"path": "x", "op": "eq", "value": 1},
                "act": {"path": "y", "op": "eq", "value": 1}, "within_s": 300}
        fixed = [1200.0] * 50
        best, tau = audits.exposure(fixed, [item], 3600)
        self.assertEqual(best, 1.0)
        self.assertTrue(1200 <= tau <= 1500)
        spread = [600.0 + 30 * i for i in range(80)]
        best, _ = audits.exposure(spread, [item], 3600)
        self.assertLessEqual(best, 0.2)
        nothing = [None] * 10
        self.assertEqual(audits.exposure(nothing, [], 3600)[0], 0.0)


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        seeds = list(range(100000, 100060))
        cls.uniform = audits.audit(bleach_spec(), seeds=seeds, house=one_house)
        cls.tell = audits.audit(leftmost_spec(), seeds=seeds, house=one_house)

    def test_a_drawn_answer_passes_its_audit(self):
        rec = self.uniform
        self.assertTrue(rec["ran"])
        self.assertEqual(rec["harness_errors"], [])          # the fast path builds what a full build does
        body = rec["answers"]["bleach"]
        self.assertEqual(body["kind"], "entity")
        self.assertEqual(len(body["audit_values"]), 60)
        self.assertEqual(body["k"], 3)
        self.assertLessEqual(body["best_constant_share"], body["bound"] + 1e-9)
        self.assertTrue(all(t["ok"] for t in rec["tells"]), rec["tells"])
        self.assertTrue(rec["ok"], rec["failures"])
        self.assertEqual(rec["lint"], [])

    def test_a_positional_tell_is_found(self):
        rec = self.tell
        self.assertFalse(rec["ok"])
        lr = [t for t in rec["tells"] if t["ordering"] == "left_to_right"]
        self.assertTrue(lr and not lr[0]["ok"], rec["tells"])
        self.assertEqual(lr[0]["rank"], 0)
        self.assertEqual(lr[0]["share"], 1.0)
        self.assertTrue(any(f.startswith("tell:") for f in rec["failures"]))
        self.assertTrue(rec["lint"] and all(row["id"] == "L-AUDIT" and row["severity"] == "error"
                                            for row in rec["lint"]))

    def test_audits_are_deterministic(self):
        again = audits.audit(bleach_spec(), seeds=list(range(100000, 100060)), house=one_house)
        strip = lambda r: {k: v for k, v in r.items() if k != "cpu_s"}     # noqa: E731
        self.assertEqual(strip(again), strip(self.uniform))

    def test_rate_records_carry_the_best_constant(self):
        rows = audits.rate_records(self.uniform)
        self.assertEqual([r["id"] for r in rows], ["B5.c-share@audit#bleach"])
        self.assertEqual(rows[0]["outcome"], "fails")             # met: within 1/k + 0.1
        self.assertEqual(rows[0]["family"], "B5")

    def test_a_constant_time_fails_the_exposure(self):
        seeds = list(range(100000, 100040))
        fixed = audits.audit(time_spec(1200, 1200), seeds=seeds, house=one_house)
        self.assertFalse(fixed["ok"])
        self.assertEqual(fixed["exposure"]["onset"]["best_share"], 1.0)
        rows = audits.rate_records(fixed)
        self.assertEqual([(r["id"], r["outcome"]) for r in rows], [("B7.exposure@audit#onset", "passes")])
        drawn = audits.audit(time_spec(600, 3000), seeds=seeds, house=one_house)
        self.assertLessEqual(drawn["exposure"]["onset"]["best_share"], 0.2)
        self.assertTrue(drawn["ok"], drawn["failures"])

    def test_a_spec_without_drawn_answers_is_not_audited(self):
        rec = audits.audit(book_spec(), seeds=[100000], house=one_house)
        self.assertFalse(rec["ran"])
        self.assertTrue(rec["ok"])

    def test_features_leave_out_what_the_spec_declares_informative(self):
        row = audits.build_row(bleach_spec(), 100001, house=one_house)
        feats = audits.features(row)
        for key in ("start_room", "requester", "weather", "dwelling", "generation", "floors", "phrasing",
                    "group_size:bleach", "answer_room:bleach"):
            self.assertIn(key, feats)
        trimmed = audits.features(row, informative={"requester": "only an adult may authorise it"})
        self.assertNotIn("requester", trimmed)
        self.assertNotIn("requester_role", trimmed)

    def test_the_battery_caches_the_audit_per_spec(self):
        battery._AUDITS.clear()
        with unittest.mock.patch.object(audits, "audit", return_value={"ran": False, "ok": True, "lint": []}) as fn:
            battery.audit(book_spec(), n=5)
            battery.audit(book_spec(), n=5)
        self.assertEqual(fn.call_count, 1)
        battery._AUDITS.clear()


if __name__ == "__main__":
    import unittest.mock          # noqa: F401
    unittest.main()
