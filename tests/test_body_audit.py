"""tools/body_audit.py after STAGE-B-FIX B9 (adoption round, K-EVAL): the audit reads the kernel's own lift rules --
the hands from physics' lift plan (forces.lift_plan) and the capacity from body.capacity_kg (as enforced and as the
robot believes it) -- instead of its own hand rule, and reports what the body did per trial and per task: overload
refusals, dropped items, energy refusals, power-downs and the energy drawn. Core types only."""
import sys
import unittest
import warnings
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import body_audit  # noqa: E402
from house_sim.generator import generate_house  # noqa: E402
from house_sim.kernel import body  # noqa: E402
from house_sim.kernel.actions import Ctx  # noqa: E402
from house_sim.kernel.engine import HouseEnv  # noqa: E402
from house_sim.kernel.phys import forces  # noqa: E402


def portable(env):
    state = env.world.s
    return next(eid for eid, rec in sorted(state["entities"].items())
                if rec.get("portable") and isinstance(rec.get("location"), str) and rec["location"] in state["rooms"])


class _Spec(object):
    def __init__(self, name):
        self.name, self.category = name, "physical"


class PickFactsTests(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore")
        self.env = HouseEnv(generate_house(0), seed=0)
        self.item = portable(self.env)

    def facts(self, **args):
        ctx = Ctx(self.env, _Spec("pick_up"), dict(target=self.item, **args), None)
        return body_audit._pick_facts(ctx, "pick_up")

    def test_the_hands_come_from_the_lift_plan(self):
        planned = []

        def plan(w, eid, hands=None, helpers=()):
            planned.append((eid, hands))
            return {"ok": False, "hands": ["left"], "mass_kg": 3.25, "reason": "over_capacity", "helpers": []}
        with mock.patch.object(forces, "lift_plan", plan):
            facts = self.facts()
            both = self.facts(grip="two_hand")
        self.assertEqual(planned, [(self.item, None), (self.item, "both")])
        self.assertEqual((facts["hands"], facts["mass_kg"], facts["plan_ok"], facts["plan_reason"]),
                         ("left", 3.25, False, "over_capacity"))
        self.assertEqual(both["hands"], "left")                     # the plan decides, not a local rule

    def test_the_capacity_is_the_bodys_enforced_and_believed_one(self):
        calls = []
        real = body.capacity_kg

        def capacity(state, hand=None, eid=None, believed=False):
            calls.append((hand, eid, believed))
            return real(state, hand, eid, believed=believed) + (1.0 if believed else 0.0)
        with mock.patch.object(body, "capacity_kg", capacity):
            facts = self.facts()
        self.assertIn((facts["hands"], self.item, False), calls)
        self.assertIn((facts["hands"], self.item, True), calls)
        self.assertEqual(round(facts["cap_believed_kg"] - facts["cap_kg"], 4), 1.0)
        self.assertEqual(facts["over_capacity"], facts["mass_kg"] > facts["cap_kg"] + 1e-9)


class BodyReportTests(unittest.TestCase):
    def test_a_trial_records_overloads_drops_and_energy(self):
        warnings.simplefilter("ignore")

        def build(spec, seed):
            return generate_house(seed), {"instruction": "walk", "success": []}, {"reference": [], "bindings": {}}

        def make_env(config, task, seed, perception=None):
            return HouseEnv(config, task, seed, trace=False)

        def run_plan(env, plan, bindings, strict=True):
            state = env.world.s
            moved = [env.step("move", rid)["ok"] for rid in sorted(state["rooms"][state["robot"]["room"]]["exits"])]
            self.assertTrue(any(moved))                              # (an exit behind a closed door is refused)
            env.world.begin()
            env.world.stat("overload_attempts")
            env.world.stat("robot_drops")
            env.world.stat("robot_drops")
            env.world.commit()
        from house_sim.tasks import runner
        with mock.patch.object(runner, "build", build), mock.patch.object(runner, "make_env", make_env), \
                mock.patch.object(runner, "run_plan", run_plan):
            rows = body_audit._run_spec(("X-1", "x.json", {}, (0, 1)))
        self.assertEqual([r.get("error") for r in rows], [None, None])
        for row in rows:
            self.assertEqual((row["overload_refusals"], row["robot_drops"], row["energy_refusals"],
                              row["power_downs"]), (1, 2, 0, 0))
            self.assertGreater(row["energy_wh"], 0.0)               # walking costs energy (body.settle_energy)
        per_task = body_audit.per_task_body(rows)
        self.assertEqual(per_task["X-1"]["seeds"], 2)
        self.assertEqual((per_task["X-1"]["overload_refusals"], per_task["X-1"]["robot_drops"]), (2, 4))
        self.assertEqual(per_task["X-1"]["energy_wh_max"], max(r["energy_wh"] for r in rows))
        summary, hits = body_audit.summarize(rows, [("X-1", "x.json", {})])
        self.assertEqual((summary["body"]["overload_refusals"], summary["body"]["robot_drops"]), (2, 4))
        self.assertEqual(summary["body"]["most_energy"]["catalog_id"], "X-1")
        self.assertEqual(sorted({e["kind"] for e in hits["body_events"]}), ["overload_refusals", "robot_drops"])
        baseline = {"trials": [dict(rows[0], robot_drops=0), dict(rows[1])], "hits": {}}
        diff = body_audit.compare(baseline, rows)
        self.assertEqual(diff["body_events_changed"], [{"catalog_id": "X-1", "seed": 0, "kind": "robot_drops",
                                                        "count": [0, 2]}])
        old = {"trials": [{k: v for k, v in rows[0].items() if k not in body_audit.BODY_STATS + (
            "overload_refusals", "robot_drops", "energy_wh")}], "hits": {}}
        self.assertEqual(body_audit.compare(old, rows)["body_events_changed"], [])    # a pre-B9 baseline


if __name__ == "__main__":
    unittest.main()
