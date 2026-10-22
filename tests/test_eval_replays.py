"""Compact episode replays (house_sim/kernel/replays.py, house-replay-v3; adoption round K-EVAL).

A replay keeps the start state, the baseline and the command log exactly (every recorded step digest), compressed
together: HouseEnv.replay reproduces it, the viewer reads it, old house-replay-v2 files keep loading, a damaged file
is refused, and a typical episode stays far under 200 KB. Also: tools/compact_replays.py, the committed demo replays,
and phase_done records of multi-phase episodes."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sim.generator import generate_house  # noqa: E402
from house_sim.kernel import replays  # noqa: E402
from house_sim.kernel.engine import HouseEnv  # noqa: E402
from house_sim.kernel.predicates import digest  # noqa: E402
from house_sim.tasks.runner import build  # noqa: E402

SAY = {"path": "facts.last_speech.text", "op": "eq"}
TWO_PHASES = {
    "catalog_id": "T-replay-phases-01", "title": "two requests", "risk_level": 0,
    "house": {"residents": "minimal", "pets": "none"},
    "phases": [
        {"id": "p1", "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say one."]},
         "success": [dict(SAY, value="one")], "milestones": {"spoke": dict(SAY, value="one")},
         "budget": {"steps": 20, "time_s": 900}},
        {"id": "p2", "start": {"after": "p1", "gap_s": 600},
         "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say two."]},
         "success": [dict(SAY, value="two")], "budget": {"steps": 20, "time_s": 900}}],
    "reference": [{"do": "announce", "text": "one"}, {"phase_done": True}, {"do": "announce", "text": "two"},
                  {"phase_done": True}],
    "wrong": [{"name": "never ends the first request", "plan": [{"do": "announce", "text": "one"}]}]}


def walk(env, steps=6):
    """A short episode: look, move to a neighbouring room, wait, look again."""
    state = env.world.s
    exits = sorted(state["rooms"][state["robot"]["room"]]["exits"])
    commands = [("look",), ("move", exits[0]), ("wait",), ("look",)]
    for i in range(steps):
        command = commands[i % len(commands)]
        if command[0] == "wait":
            env.step("wait", seconds=120)
        else:
            env.step(*command)
    return env


class FormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        warnings.simplefilter("ignore")
        cls.tmp = Path(tempfile.mkdtemp(prefix="house_replays_test_"))
        cls.env = walk(HouseEnv(generate_house(0), seed=0, perception="realistic"))
        cls.traced = walk(HouseEnv(generate_house(1), seed=1, trace=True))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def path(self, name):
        return self.tmp / name

    def test_save_replay_writes_a_compact_file_that_replays_exactly(self):
        path = self.path("compact.json")
        self.env.save_replay(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["format"], replays.FORMAT_V3)
        for key in replays.BULKY:
            self.assertNotIn(key, raw)                       # packed, not plain
        self.assertEqual(raw["seed"], 0)                     # identity stays readable (and auditable)
        self.assertEqual(raw["summary"]["steps"], len(self.env._history))
        self.assertLess(os.path.getsize(path), 200 * 1000)  # the target: well under 200 KB per typical episode
        replayed = HouseEnv.replay(path)
        self.assertEqual(digest(replayed.world.s), digest(self.env.world.s))
        self.assertEqual(replayed.observe(), self.env.observe())

    def test_the_plain_format_still_writes_and_loads(self):
        path = self.path("plain.json")
        self.env.save_replay(path, compact=False)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["format"], replays.FORMAT_V2)
        self.assertIn("start_state", raw)
        self.assertEqual(digest(HouseEnv.replay(path).world.s), digest(self.env.world.s))

    def test_compact_keeps_the_states_and_log_exactly_and_drops_only_changes(self):
        plain, compact, full = self.path("t2.json"), self.path("t3.json"), self.path("t3_changes.json")
        self.traced.save_replay(plain, compact=False)
        self.traced.save_replay(compact)
        self.traced.save_replay(full, keep_changes=True)
        v2 = json.loads(plain.read_text(encoding="utf-8"))
        lean, lossless = replays.read(compact), replays.read(full)
        self.assertEqual(lossless["format"], replays.FORMAT_V3)
        self.assertEqual({k: v for k, v in lossless.items() if k != "format"},
                         {k: v for k, v in v2.items() if k != "format"})
        for key in ("initial", "start_state", "baseline", "task", "final_hash", "evaluation", "actions_hash"):
            self.assertEqual(lean[key], v2[key], key)
        self.assertEqual(lean["steps"], [{k: v for k, v in s.items() if k != "changes"} for s in v2["steps"]])
        self.assertTrue(all("state_hash" in s for s in lean["steps"]))       # every step digest stays
        self.assertTrue(json.loads(full.read_text(encoding="utf-8"))["summary"]["changes"])
        self.assertFalse(json.loads(compact.read_text(encoding="utf-8"))["summary"]["changes"])
        self.assertLess(os.path.getsize(compact), os.path.getsize(plain) / 10)
        self.assertEqual(digest(HouseEnv.replay(compact).world.s), digest(self.traced.world.s))

    def test_writing_is_deterministic_and_plain_ascii_json(self):
        a, b = self.path("det_a.json"), self.path("det_b.json")
        self.env.save_replay(a)
        self.env.save_replay(b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertNotIn(b"\r\n", a.read_bytes())
        packed = json.loads(a.read_text(encoding="utf-8"))["packed"]
        self.assertTrue(all(32 < ord(c) < 127 and c not in '"\\' for c in packed))

    def test_a_damaged_or_unknown_file_is_refused(self):
        path = self.path("damaged.json")
        self.env.save_replay(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        bad = dict(raw, packed_sha256="0" * 64)
        path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            HouseEnv.replay(path)
        truncated = dict(raw, packed=raw["packed"][:-40])
        path.write_text(json.dumps(truncated), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Corrupt"):
            HouseEnv.replay(path)
        path.write_text(json.dumps(dict(raw, codec="rot13")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "codec"):
            replays.read(path)
        path.write_text(json.dumps(dict(raw, format="house-replay-v9")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported replay"):
            HouseEnv.replay(path)

    def test_zlib_is_the_fallback_without_lzma(self):
        data = {"format": replays.FORMAT_V2, "initial": {"a": [1, 2, 3]}, "start_state": {"b": "x" * 500},
                "baseline": {"b": "x" * 500}, "steps": [{"command": {"action": "look"}, "ok": True}], "seed": 3}
        with mock.patch.object(replays, "lzma", None):
            encoded = replays.encode(data)
            self.assertEqual(encoded["codec"], "zlib+b85")
            self.assertEqual(replays.decode(encoded)["start_state"], data["start_state"])
        xz = replays.encode(data)
        self.assertEqual(xz["codec"], "xz+b85")
        with mock.patch.object(replays, "lzma", None):
            with self.assertRaisesRegex(ValueError, "lzma"):
                replays.decode(xz)

class PhaseRecordTests(unittest.TestCase):
    def test_a_multi_phase_episode_replays_its_phase_ends(self):
        config, task, _ = build(copy.deepcopy(TWO_PHASES), 0)
        env = HouseEnv(config, task, 0, trace=True)
        env.step("announce", text="one")
        env.phase_done()
        env.step("announce", text="two")
        env.phase_done()
        self.assertTrue(env.evaluate()["success"])
        ends = [r for r in env._history if r.get("phase_done")]
        self.assertEqual([(r["phase"], r["next_phase"], r["waiting"]) for r in ends],
                         [("p1", "p2", False), ("p2", None, False)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phases.json"
            env.save_replay(path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["summary"]["phase_done"], 2)
            replayed = HouseEnv.replay(path)
        self.assertEqual(digest(replayed.world.s), digest(env.world.s))
        self.assertTrue(replayed.evaluate()["success"])


class ToolTests(unittest.TestCase):
    def test_compact_replays_converts_v2_files_losslessly_and_skips_the_rest(self):
        env = walk(HouseEnv(generate_house(2), seed=2, trace=True), steps=4)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            env.save_replay(folder / "old.json", compact=False)
            original = json.loads((folder / "old.json").read_text(encoding="utf-8"))
            (folder / "notes.json").write_text('{"hello": 1}', encoding="utf-8")
            env.save_replay(folder / "new.json")
            before_new = (folder / "new.json").read_bytes()
            out = subprocess.run([sys.executable, str(ROOT / "tools" / "compact_replays.py"), str(folder)],
                                 capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("converted 1, already compact 1, not replays 1", out.stdout)
            converted = replays.read(folder / "old.json")
            self.assertEqual({k: v for k, v in converted.items() if k != "format"},
                             {k: v for k, v in original.items() if k != "format"})     # changes kept
            self.assertEqual((folder / "new.json").read_bytes(), before_new)
            self.assertEqual((folder / "notes.json").read_text(encoding="utf-8"), '{"hello": 1}')
            self.assertEqual(digest(HouseEnv.replay(folder / "old.json").world.s), digest(env.world.s))
            # --drop-changes re-encodes a compact file that kept its changes (the default save_replay form)
            out = subprocess.run([sys.executable, str(ROOT / "tools" / "compact_replays.py"), str(folder / "old.json"),
                                  "--drop-changes"], capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(out.returncode, 0, out.stderr)
            lean = replays.read(folder / "old.json")
            self.assertFalse(any("changes" in s for s in lean["steps"]))
            self.assertEqual(lean["start_state"], original["start_state"])
            self.assertEqual(digest(HouseEnv.replay(folder / "old.json").world.s), digest(env.world.s))


class DemoReplayTests(unittest.TestCase):
    def test_committed_demo_replays_are_compact(self):
        """demos/replays/ is committed again (it was git-ignored while replays were about 3 MB each): every replay
        the demo manifest lists is a compact house-replay-v3 file of at most 250 KB."""
        manifest = json.loads((ROOT / "demos" / "manifest.json").read_text(encoding="utf-8"))
        listed = [s["replay"] for s in manifest.get("scenarios", [])]
        present = [p for p in listed if (ROOT / p).is_file()]
        if not present:
            self.skipTest("no demo replays in this checkout (python -m house_sim --demo builds them)")
        for rel in present:
            path = ROOT / rel
            self.assertLessEqual(path.stat().st_size, 250 * 1000, rel)
            with open(path, encoding="utf-8") as stream:
                head = stream.read(64)
            self.assertIn('"format":"house-replay-v3"', head.replace(" ", ""), rel)


if __name__ == "__main__":
    unittest.main()
