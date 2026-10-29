"""Room-mode golden (docs/design/TIER1_INTEGRATION.md 3.5; recorded by tools/golden.py into tests/data/room_golden.json).

State and observation digests after reset and after every step of a fixed 12-action script on seeds 0-3 in oracle and
realistic perception, and the final state digests of the reference plans of 20 fixed specs (every spec with
HOUSE_SIM_GOLDEN_ALL=1), all with the room camera passed explicitly once HouseEnv takes a camera argument. Room-mode
states never carry camera state, and `look` lasts 1 s.

A difference means room-mode behaviour changed. Only the packages allowed to change it (TIER1_INTEGRATION 3.5:
PHYS-INT, BODY-ENGINE, BODY-FAULTS) re-record, and only after `python tools/golden.py dump --out BEFORE` (before the
first edit), the same into AFTER (before hand-over) and `python tools/golden.py diff BEFORE AFTER` show that every
changed path belongs to their reviewed classes; then `python tools/golden.py record --by <ID> --classes <...>`.
A known-failing pin excuses a changed reference only while the gate's committed allowlist excuses its spec (K-GATE3:
the file's own flag excuses nothing), and the tier-1 gate's G8 lists every re-recorded entry for two signatures.

    python -m unittest tests.test_room_golden
    HOUSE_SIM_GOLDEN_ALL=1 python -m unittest tests.test_room_golden
"""
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import golden  # noqa: E402

ALL = os.environ.get("HOUSE_SIM_GOLDEN_ALL") == "1"
DOMAINS = os.environ.get("HOUSE_SIM_DOMAINS", "").strip()
SCRIPT_ACTIONS = ["look", "approach", "pick_up", "move", "approach", "place", "open", "close", "inspect", "read",
                  "wait", "wait"]
HINT = ("room-mode behaviour changed; see tools/golden.py (dump before/after, diff) and TIER1_INTEGRATION 3.5 before "
        "re-recording")


@unittest.skipIf(DOMAINS not in ("", "all"), "the room golden is recorded with every domain loaded")
class RoomGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = golden.load()
        keys = None if ALL else cls.golden["fixed_specs"]
        workers = golden.default_workers() if ALL else 1
        cls.runs, cls.specs = golden.compute(cls.golden, keys, workers=workers)

    def test_file_pins_the_script_and_the_specs(self):
        g = self.golden
        self.assertEqual(g["format"], golden.FORMAT)
        self.assertEqual(g["camera"], "room")
        self.assertEqual(sorted(g["scripts"]), [str(s) for s in golden.SEEDS])
        for seed, script in g["scripts"].items():
            self.assertEqual([c["action"] for c in script], SCRIPT_ACTIONS, seed)
            move = script[3]
            self.assertEqual(move.get("speed"), "careful", seed)
            self.assertEqual([c.get("seconds") for c in script[-2:]], list(golden.SCRIPT_WAITS), seed)
        self.assertEqual(len(g["fixed_specs"]), golden.FIXED_SPEC_COUNT)
        self.assertTrue(set(g["fixed_specs"]) <= set(g["specs"]))
        self.assertEqual(sorted(g["runs"]), sorted("{}/{}".format(s, m) for s in golden.SEEDS for m in golden.MODES))
        self.assertTrue(g["history"] and all({"by", "classes", "date"} <= set(h) for h in g["history"]))

    def test_oracle_script_steps_all_succeed(self):
        # the scripts were chosen so that every step succeeds in oracle mode; a refusal here is a behaviour change
        for key, run in sorted(self.runs.items()):
            if key.endswith("/oracle"):
                self.assertEqual([s["ok"] for s in run["steps"]], [True] * len(SCRIPT_ACTIONS), key)

    def test_room_mode_has_no_camera_state_and_look_lasts_one_second(self):
        for key, run in sorted(self.runs.items()):
            self.assertEqual(run["problems"], [], key)
            self.assertEqual(run["steps"][0]["action"], "look")
            self.assertEqual(run["steps"][0]["seconds"], 1.0, key)
        for key, spec in sorted(self.specs.items()):
            self.assertEqual(spec.get("problems", []), [], key)

    def test_script_digests_match(self):
        diffs = golden.compare(self.golden, self.runs, {})
        self.assertEqual(diffs, [], HINT)

    def test_reference_plan_digests_match(self):
        expected = set(self.golden["specs"]) if ALL else set(self.golden["fixed_specs"])
        self.assertEqual(set(self.specs), expected)
        diffs = golden.compare(self.golden, {}, self.specs)
        self.assertEqual(diffs, [], HINT)


class KnownFailingGuardTests(unittest.TestCase):
    """Phase-1 close-out (K-GATE3; finish_VER.json, golden attack G2): a hand edit of tests/data/room_golden.json that
    changed an entry's digest and marked it {"known_failing": true, "passes": false} made compare skip it whenever the
    run passed, so any digest was accepted. Only the gate's committed allowlist vouches for a known-failing pin now."""

    def golden(self):
        return {"runs": {}, "specs": {"T-1": {"passes": True, "state": "s1", "steps": 3},
                                      "T-2": {"passes": True, "state": "s2", "steps": 4}}}

    def test_a_hand_marked_pin_does_not_hide_a_changed_digest(self):
        fresh = {"T-1": {"passes": True, "state": "s1", "steps": 3}, "T-2": {"passes": True, "state": "s2", "steps": 4}}
        forged = self.golden()
        forged["specs"]["T-1"].update(state="0" * 64, known_failing=True, passes=False)     # the attack
        diffs = golden.compare(forged, {}, fresh, vouched=set())
        self.assertTrue(any(d.startswith("spec T-1: state, passes differ") for d in diffs), diffs)
        self.assertTrue(any("without the gate's committed allowlist" in d for d in diffs), diffs)
        self.assertEqual(golden.expected_changes(forged, fresh, vouched=set()), [])
        with unittest.mock.patch.object(golden, "allowlisted_failing", lambda keys: set()):
            self.assertTrue(golden.compare(forged, {}, fresh))              # the default reads the committed allowlist
        # vouched by the committed allowlist: the fix the entry waited for, an expected change (re-record)
        self.assertEqual(golden.compare(forged, {}, fresh, vouched={"T-1"}), [])
        self.assertEqual(len(golden.expected_changes(forged, fresh, vouched={"T-1"})), 1)
        with unittest.mock.patch.object(golden, "allowlisted_failing", lambda keys: set(keys) & {"T-1"}):
            self.assertEqual(golden.compare(forged, {}, fresh), [])

    def test_an_unvouched_pin_is_reported_even_while_it_still_fails(self):
        pinned = self.golden()
        pinned["specs"]["T-2"].update(passes=False, known_failing=True)
        fresh = {"T-1": {"passes": True, "state": "s1", "steps": 3}, "T-2": {"passes": False, "state": "s2", "steps": 4}}
        self.assertEqual(golden.compare(pinned, {}, fresh, vouched={"T-2"}), [])
        diffs = golden.compare(pinned, {}, fresh, vouched=set())
        self.assertEqual(len(diffs), 1)
        self.assertIn("pinned known-failing without the gate's committed allowlist", diffs[0])


class GoldenToolTests(unittest.TestCase):
    """The diff tool's grouping (pure; no simulation)."""

    def test_patterns_replace_ids_and_indexes(self):
        self.assertEqual(golden.pattern("entities.mug_1._robot_moved"), "entities.*._robot_moved")
        self.assertEqual(golden.pattern("robot.battery"), "robot.battery")
        self.assertEqual(golden.pattern("obs.visible_entities.obj_01e78d03.state"), "obs.visible_entities.*.state")
        self.assertEqual(golden.pattern("event_log.12.kind"), "event_log.*.kind")
        self.assertEqual(golden.pattern("rooms.kitchen.wet_cells"), "rooms.*.wet_cells")

    def test_leaf_diffs_walk_dicts_and_equal_length_lists(self):
        before = {"a": {"b": 1, "c": [1, 2]}, "d": [1]}
        after = {"a": {"b": 2, "c": [1, 3]}, "d": [1, 2], "e": True}
        paths = [p for p, _, _ in golden.leaf_diffs(before, after)]
        self.assertEqual(paths, ["a.b", "a.c.1", "d", "e"])


if __name__ == "__main__":
    unittest.main()
