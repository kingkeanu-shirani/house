"""The independent review's cheat policies against the hardened evaluator (docs/design/VERIFY_HARDENING.md 23.6; WP10).

The review (docs/reviews/2026-10-22_independent_review/) wrote cheat policies by hand and found they passed most of the
specs it attacked. tests/data/review_cheats/ keeps them. The acceptance rule: every cheat must be caught, meaning the run
fails, or the spec is not HARDENED.

Until a spec is migrated (WP12) it is schema 2 / 3 and has no HARDENED record, so it is "not HARDENED" and cannot reach dev
or test: that half of the rule holds by construction, and says nothing about the evaluator. So every cheat is also replayed
on the spec built as if it were schema 4 (runner.build's as-if build, with the envelope drafted from the reference's footprint,
as tools/harden_audit.py does), judged as a wrong plan is, with the house ledger, settle and the content guards. A label the
reference raises itself is not charged to the cheat. What that shows is pinned in caught_as_if.json:

* "caught": fails as-if already (the labels are the evidence). A cheat listed here must stay caught; a change that lets
  one through fails this test.
* "pending": still passes as-if. These need the spec's own migration: a declaration only the author can write (answers that
  vary, a `said` predicate, a destination role, `timing`). The spec must not be HARDENED while one passes.
* "raised": the run raised, so it shows nothing (14.5); "no_scene": the review's binding op finds nothing on the seeds.

Once a spec is schema 4 nothing is as-if any more: its cheats replay against the spec itself in the evaluation mode, and a
passing cheat on a spec with a HARDENED record fails here.
"""
import copy
import glob
import json
import unittest
from pathlib import Path

from house_sim.kernel import ledger
from house_sim.kernel.errors import SpecError
from house_sim.kernel.plugins import load_domains
from house_sim.tasks import verify
from house_sim.tasks.runner import build

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data" / "review_cheats"
# 23.4 item 1: the 36 specs the review gamed (all with suffix -01)
GAMED = ("T-d389e2979f6aad T-c49fadcea69182 T-151f6733397156 X-c77e2cbe7181f0 T-1cd8e5b7e25846 P-386d0f28709b05 "
         "X-44e90cc83ba0db T-032657e9583337 A-2bbd9d39ff1512 A-d66994a566899c T-ba18cc4b56e89d T-fffb3941b40c87 "
         "T-020dc4b5a8de89 A-8910a09f802fbf T-35efe739c7c779 T-6203fdc5fa8986 T-d990c58b123a5e A-350cc067998929 "
         "T-dde0975c7a26b8 T-f7f4105ce49060 X-1c71303328a5b8 A-b6bf8052cc5211 V-10424aa789320a P-47196e0991cdf4 "
         "A-afe8d8c6d6d883 T-457afb032d7be7 T-e9cf6ca709ec77 X-bc4e8d373e62c5 A-04b40946bea880 T-e262f715d0bd60 "
         "T-d8d0ca913007d4 T-718ad251e38789 T-70a9e6a7a83f64 T-5d63abe55f7147 T-c39a630dc5c6f6 T-2e07f1f4626671").split()
# the gamed specs whose game the review's reports describe as a scene tell or a label check, measured by S6 and B13 rather
# than by a plan one can write down in the wrong-plan format
NO_PLAN = {"A-b6bf8052cc5211": "the NOT FRAGILE label: the plan needs the box's packing helpers (kept for WP12's migration)"}
_READY = []


def _ensure():
    if not _READY:
        load_domains()
        _READY.append(True)


def cheats():
    """{catalog id: {"setup": [read-only binding ops], "games": [{"name", "plan", "variants"?}]}} of every lens."""
    out = {}
    for path in sorted(DATA.glob("lens_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for cid, row in data["specs"].items():
            block = out.setdefault(cid, {"setup": [], "games": []})
            block["setup"] += row.get("setup") or []
            block["games"] += row.get("games") or []
    return out


def authored():
    out = {}
    for path in sorted(glob.glob(str(ROOT / "tasks" / "authored" / "*.json"))):
        for spec in json.loads(Path(path).read_text(encoding="utf-8")).get("specs", []):
            out[spec["catalog_id"]] = spec
    return out


def with_setup(spec, extra):
    """The spec with the cheat's read-only binding ops at the end of its setup (they draw no random numbers: the review's
    gamecheck proved the scene digest unchanged)."""
    out = copy.deepcopy(spec)
    if extra:
        out["setup"] = list(out.get("setup") or []) + copy.deepcopy(extra)
    return out


SEEDS = (0, 1, 2, 41, 97)       # a game for one variant, or a setup that binds only on some houses, takes the first that fits


def _built(spec, target, seed):
    """(config, task, oracle, idle projections, reference record) of one seed: the spec itself when it is schema 4, in the
    evaluation mode; else as if it were schema 4 in its own mode, with the envelope drafted from the reference's own
    footprint (23.1; narrow `to` scopes on what it moved), so the ledger judges a cheat on what the reference does not do."""
    hardened = verify.is_hardened(spec)
    mode = verify.EVAL_GEOM if hardened else verify.OWN
    perception, camera = verify.mode_settings(mode)
    target = copy.deepcopy(target)
    config, task, oracle = build(target, seed, camera=camera, perception=perception, hardening=None if hardened else {})
    idle_env = verify._hard_env(config, task, seed, mode, record=False)
    projections, _ = verify.idle_rollout(idle_env, task)
    ref, ref_env, _ = verify.reference_run(config, task, oracle, seed, mode, projections)
    if not hardened:
        # the envelope changes the judgment, not the simulation: the same runs, judged again with it (the simulation of a
        # cheat costs minutes over the catalog of cheats, a second reference and idle rollout per spec would double it)
        target["envelope"] = ledger.footprint(ref_env._task, ref_env.world.s, ref_env._baseline, ref_env.world,
                                              ref_env.idle, bindings=oracle["bindings"])["envelope"]
        config, task, oracle = build(target, seed, camera=camera, perception=perception, hardening={})
        task_now = copy.deepcopy(task)
        task_now["hardening"].update({"reference_resources": ref.get("resources") or {},
                                      "reference_open_s": ref.get("open_s") or {}})
        others = [f for f in ref.get("failures") or () if not str(f).startswith(("house.", "later:house."))]
        ref = dict(ref, failures=others + ledger.failures(task_now, ref_env.world.s, ref_env._baseline, ref_env.world,
                                                          projections))
    task = copy.deepcopy(task)
    task["hardening"].update({"reference_resources": ref.get("resources") or {},
                              "reference_open_s": ref.get("open_s") or {}})
    return mode, config, task, oracle, projections, ref


def judge(rec, reference):
    """One run's result: the labels the reference does not raise itself are what is charged to the cheat; a plan that
    raised shows nothing (14.5)."""
    if rec.get("raised") or rec.get("error"):
        return {"state": "raised", "error": str(rec.get("error"))[:200]}
    known = set(reference.get("failures") or ())
    charged = [str(l) for l in rec.get("failures") or () if l not in known]
    unmet = [str(u) for u in rec.get("unmet") or ()]
    return {"state": "caught" if charged or unmet else "pending", "labels": charged[:8], "unmet": unmet[:6]}


def replay_spec(spec, row, seeds=SEEDS):
    """Every cheat of a spec: {name: {"state": caught | pending | raised | no_scene, "seed", "labels", "unmet"}}. A cheat
    runs on the first seed where its variant comes up."""
    target = with_setup(spec, row.get("setup"))
    todo = list(row["games"])
    out = {}
    for seed in seeds:
        if not todo:
            break
        try:
            mode, config, task, oracle, projections, ref = _built(spec, target, seed)
        except SpecError as exc:         # a binding op of the review's that this house has nothing for
            for game in todo:
                out.setdefault(game["name"], {"state": "no_scene", "error": str(exc)[:200]})
            continue
        rest = []
        for game in todo:
            only = game.get("variants")
            if only is not None and oracle["variant"] not in only:
                rest.append(game)
                continue
            item = {"name": game["name"], "plan": copy.deepcopy(game["plan"])}
            try:
                rec, _ = verify.wrong_run(config, task, oracle, seed, mode, item, projections)
            except SpecError as exc:
                out[game["name"]] = {"state": "raised", "seed": seed, "error": str(exc)[:200]}
                continue
            out[game["name"]] = dict(judge(rec, ref), seed=seed)
        todo = rest
    return out


def has_hardened_evidence(cid, spec):
    from house_sim.audit import fingerprint
    from house_sim.eval import splits
    path = ROOT / "reports" / "verification.jsonl"
    if not path.is_file():
        return False
    proof = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("catalog_id") == cid:
                proof = row
    return splits.hardened_problem(proof, verify.spec_hash(spec), fingerprint()) is None


class CheatDataTests(unittest.TestCase):
    def test_every_gamed_spec_has_its_cheats_kept(self):
        have = {cid[:-3] for cid in cheats()}
        missing = [cid for cid in GAMED if cid not in have and cid not in NO_PLAN]
        self.assertEqual(missing, [], "23.4 item 1's specs without a kept cheat")
        for cid, row in cheats().items():
            self.assertTrue(row["games"], cid)
            for game in row["games"]:
                self.assertTrue(game.get("name") and isinstance(game.get("plan"), list) and game["plan"], (cid, game))

    def test_the_cheats_name_authored_specs(self):
        specs = authored()
        self.assertEqual(sorted(cid for cid in cheats() if cid not in specs), [])


class ReviewCheatTests(unittest.TestCase):
    """The replay itself. A seed per cheat keeps this file to minutes; the battery (B2-B14, P3-P8) is what runs at scale."""

    @classmethod
    def setUpClass(cls):
        _ensure()
        cls.specs = authored()
        cls.expected = json.loads((DATA / "caught_as_if.json").read_text(encoding="utf-8"))
        cls.results = {}
        for cid, row in sorted(cheats().items()):
            if cid in cls.expected["specs"]:
                cls.results[cid] = replay_spec(cls.specs[cid], row)

    def test_every_cheat_has_an_expectation(self):
        missing = [(cid, g["name"]) for cid, row in cheats().items() for g in row["games"]
                   if g["name"] not in self.expected["specs"].get(cid, {})]
        self.assertEqual(missing, [])

    def test_a_cheat_the_hardened_evaluator_catches_stays_caught(self):
        let_through = []
        for cid, table in sorted(self.expected["specs"].items()):
            for name, row in table.items():
                if row["state"] == "caught" and self.results[cid][name]["state"] != "caught":
                    let_through.append("{}: {} (was caught by {}; now {})".format(
                        cid, name, row.get("labels") or row.get("unmet"), self.results[cid][name]))
        self.assertEqual(let_through, [])

    def test_a_passing_cheat_keeps_its_spec_out_of_the_hardened_set(self):
        """The acceptance rule of 23.6: the run fails for a cheat, or the spec is not HARDENED."""
        offenders = []
        for cid, table in sorted(self.results.items()):
            for name, row in table.items():
                if row["state"] != "caught" and has_hardened_evidence(cid, self.specs[cid]):
                    offenders.append("{}: {} ({})".format(cid, name, row["state"]))
        self.assertEqual(offenders, [])

    def test_more_cheats_are_caught_than_wait_for_their_spec(self):
        """A direction, not a number: if most cheats passed as-if, the default protections would be doing nothing."""
        states = [row["state"] for table in self.expected["specs"].values() for row in table.values()]
        self.assertGreater(states.count("caught"), states.count("pending"))

    def test_the_labels_of_a_caught_cheat_are_specific(self):
        """A cheat caught only because it ran out of budget proves nothing (14.5)."""
        for cid, table in sorted(self.expected["specs"].items()):
            for name, row in table.items():
                if row["state"] == "caught":
                    self.assertTrue(row.get("labels") or row.get("unmet"), (cid, name))
                    self.assertNotEqual(row.get("labels"), ["budget"], (cid, name))


if __name__ == "__main__":
    unittest.main()
