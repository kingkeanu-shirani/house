"""Offline tests for the 2D episode replay viewer (house_sim/eval/viewer.py, docs/VIEWER.md).

No network access anywhere: episodes are produced locally with the simulator and exported to temp files. The whole
module runs with a temporary HOME/USERPROFILE, a throwaway split key (HOUSE_AI_SPLIT_KEY) and a redirected commitment
file, so the user's real ~/.house_ai (key, private manifest, phrasings) is never read.
"""
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sim.eval import viewer  # noqa: E402
from house_sim.generator import generate_house  # noqa: E402
from house_sim.kernel import replays  # noqa: E402
from house_sim.kernel.engine import HouseEnv  # noqa: E402

EXPLORE_TASK = {"instruction": "Explore the house and look around.", "catalog_id": "custom",
                "success": [{"path": "robot.room", "op": "eq", "value": "nowhere"}],
                "milestones": {"a_minute_passed": {"path": "time_s", "op": "gt", "value": 60}},
                "step_budget": 400, "time_budget_s": 72000}
DESCRIPTIVE = viewer._descriptive_keys()
THROWAWAY_KEY_HEX = "5eed" * 16           # a fixed test-only split key, never a real one
PRIVATE_SEED = 900000007                   # inside the private (test) seed range
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def scripted_run(env, steps):
    """Observation-only scripted agent: moves between rooms, approaches, inspects, picks up, waits, looks.
    Uses only ids found in observations (handles in realistic mode)."""
    obs = env.observe()
    for i in range(steps):
        visible = [h for h, r in sorted(obs["visible_entities"].items()) if r.get("pos") and not r.get("mounted")]
        exits = sorted(c["to"] for c in obs.get("connections", []))
        k = i % 7
        if k == 0 and exits:
            command = {"action": "move", "target": exits[(i // 7) % len(exits)]}
        elif k in (1, 3) and visible:
            command = {"action": "approach", "target": visible[(i * 3) % len(visible)]}
        elif k == 2 and visible:
            command = {"action": "inspect", "target": visible[(i * 5) % len(visible)]}
        elif k == 4:
            command = {"action": "wait", "seconds": 20}
        elif k == 5:
            command = {"action": "look"}
        else:
            command = {"action": "pick_up", "target": visible[0] if visible else "nothing_here"}
        result = env.step(**command)
        obs = result["observation"] if result["observation"] is not None else env.observe()
    return env


def make_replay(folder, name, seed, steps, perception, trace, keep_changes=None):
    """A compact replay (house-replay-v3). Traced episodes keep their recorded changes by default here, so the
    recorded-change fallback of the viewer can be tested; keep_changes=False writes the default compact form."""
    env = HouseEnv(generate_house(seed=seed), dict(EXPLORE_TASK), seed, perception=perception, trace=trace)
    scripted_run(env, steps)
    path = Path(folder) / (name + ".json")
    env.save_replay(path, keep_changes=trace if keep_changes is None else keep_changes)
    return path, env


def strings_and_tokens(value, out, key=None):
    """Collect (whole strings, tokens) from keys and values; descriptive text values contribute whole strings only."""
    if isinstance(value, str):
        out[0].add(value)
        if key not in DESCRIPTIVE:
            out[1].update(TOKEN.findall(value))
    elif isinstance(value, dict):
        for k, v in value.items():
            out[0].add(k)
            out[1].update(TOKEN.findall(k))
            strings_and_tokens(v, out, k)
    elif isinstance(value, list):
        for v in value:
            strings_and_tokens(v, out, key)
    return out


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="house_viewer_test_"))
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
        cls._env = mock.patch.dict(os.environ, {"HOME": str(cls.home), "USERPROFILE": str(cls.home),
                                                "HOUSE_AI_SPLIT_KEY": THROWAWAY_KEY_HEX,
                                                "HOUSE_AI_COMMITMENT_FILE": str(cls.tmp / "reports" / "test_commitment.json")})
        cls._env.start()
        cls.clear_split_caches()
        if Path.home().resolve() != cls.home.resolve():   # never run against the real profile
            cls._env.stop()
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise RuntimeError("could not redirect the home directory for the viewer tests")
        # 300-step realistic episode with recorded changes (trace=True): size bound, round trip, perceived leaks
        cls.long_path, cls.long_env = make_replay(cls.tmp, "long_realistic", 3, 300, "realistic", True)
        cls.long_html = viewer.export_viewer(cls.long_path, cls.tmp / "long.html")
        cls.long_text = cls.long_html.read_text(encoding="utf-8")
        cls.long_data = viewer.extract_data(cls.long_text)
        # short realistic episode without recorded changes (like harness runs) and a short oracle one with trace
        cls.small_path, cls.small_env = make_replay(cls.tmp, "small_realistic", 5, 24, "realistic", False)
        cls.oracle_path, cls.oracle_env = make_replay(cls.tmp, "small_oracle", 1, 20, "oracle", True)

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        cls.clear_split_caches()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def clear_split_caches():
        splits = viewer._splits()
        cache = getattr(splits, "_MANIFEST_CACHE", None)
        if isinstance(cache, dict):
            cache.clear()

    def true_ids(self, env):
        ids = set(env.world.s["entities"]) | set(env._replay_start["entities"])
        return ids

    # ---------------------------------------------------------------------------------------------------------
    def test_replay_round_trip(self):
        data = self.long_data
        steps = len(self.long_env._history)
        self.assertEqual(steps, 300)
        self.assertEqual(data["format"], viewer.FORMAT)
        self.assertEqual(len(data["steps"]), steps)
        self.assertEqual(len(data["frames"]), steps + 1)
        self.assertEqual(len(data["pframes"]), steps + 1)
        self.assertEqual(len(data["evals"]), steps + 1)
        self.assertEqual(len(data["diffs"]), steps)
        frames = viewer.decode_deltas(data["frames"])
        robot = self.long_env.world.s["robot"]
        self.assertEqual(frames[-1]["rp"], list(robot["pos"]))
        self.assertEqual(frames[-1]["r"], robot["room"])
        self.assertEqual(frames[0]["rp"], list(self.long_env._replay_start["robot"]["pos"]))
        pframes = viewer.decode_deltas(data["pframes"])
        self.assertEqual(pframes[-1]["rp"], list(robot["pos"]))
        for index, record in enumerate(self.long_env._history):
            self.assertEqual(data["steps"][index]["ok"], record["ok"], index)
            self.assertEqual(data["steps"][index]["a"], record["command"]["action"])
        self.assertEqual(data["meta"]["warnings"], [])
        self.assertEqual(data["meta"]["perception"], "realistic")
        self.assertEqual(sorted(data["meta"]["views"]), ["oracle", "perceived"])
        evals = viewer.decode_deltas(data["evals"])
        final = self.long_env.evaluate()
        self.assertEqual(evals[-1]["s"], final["success"])
        self.assertEqual(evals[-1]["f"], final["failures"][:40])
        reached = self.long_env._milestones["a_minute_passed"]
        self.assertEqual(evals[-1]["m"]["a_minute_passed"][0], reached["step"])
        self.assertNotIn("a_minute_passed", evals[reached["step"] - 1].get("m", {}))
        self.assertEqual(data["meta"]["milestones"], ["a_minute_passed"])
        self.assertTrue(any(not s["ok"] for s in data["steps"]), "the scripted run should contain refused steps")

    def test_round_trip_without_recorded_changes_and_oracle(self):
        out = viewer.export_viewer(self.small_path, self.tmp / "small.html")
        data = viewer.extract_data(out.read_text(encoding="utf-8"))
        n = len(self.small_env._history)
        self.assertEqual(len(data["frames"]), n + 1)
        self.assertEqual(len(data["pframes"]), n + 1)
        self.assertEqual(len(data["diffs"]), n)
        self.assertEqual(viewer.decode_deltas(data["frames"])[-1]["rp"], list(self.small_env.world.s["robot"]["pos"]))
        out = viewer.export_viewer(self.oracle_path, self.tmp / "oracle.html")
        data = viewer.extract_data(out.read_text(encoding="utf-8"))
        n = len(self.oracle_env._history)
        self.assertEqual(len(data["frames"]), n + 1)
        self.assertNotIn("pframes", data)
        self.assertEqual(data["meta"]["views"], ["oracle"])
        self.assertEqual(viewer.decode_deltas(data["frames"])[-1]["rp"], list(self.oracle_env.world.s["robot"]["pos"]))
        with self.assertRaises(viewer.ViewerError):
            viewer.export_viewer(self.oracle_path, self.tmp / "nope.html", view="perceived")

    def test_size_bound_300_steps(self):
        size = os.path.getsize(self.long_html)
        self.assertLess(size, 3 * 1000 * 1000, "viewer page is {} bytes".format(size))
        # far smaller than the episode's content (the replay file itself is compact: kernel/replays.py)
        content = len(json.dumps(replays.read(self.long_path), ensure_ascii=False))
        self.assertLess(size, content / 4)

    def test_no_external_urls_or_network(self):
        for text in (self.long_text, viewer.TEMPLATE_PATH.read_text(encoding="utf-8")):
            self.assertIsNone(re.search(r"(?i)\b(?:https?|ftp|wss?)://", text))
            self.assertNotIn("//cdn", text)
            self.assertIsNone(re.search(r"(?i)<(?:link|iframe|img|object|embed)\b", text))
            self.assertIsNone(re.search(r"(?i)\bsrc\s*=", text))
            self.assertIsNone(re.search(r"(?i)@import", text))
            self.assertIsNone(re.search(r"url\((?!#)", text))
            for word in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import("):
                self.assertNotIn(word, text)
        self.assertEqual(self.long_text.count("<script"), 2)   # the JSON data block and the inline app

    def test_perceived_frames_contain_no_true_entity_ids(self):
        ids = self.true_ids(self.long_env)
        found = strings_and_tokens(viewer.decode_deltas(self.long_data["pframes"]), (set(), set()))
        self.assertFalse(found[0] & ids, sorted(found[0] & ids)[:10])
        self.assertFalse(found[1] & ids, sorted(found[1] & ids)[:10])
        handles = {k for frame in viewer.decode_deltas(self.long_data["pframes"]) for room in frame["R"].values() for k in room["e"]}
        self.assertTrue(handles and all(re.match(r"^obj_[0-9a-f]{8,16}$", h) for h in handles))
        # the oracle frames, by contrast, use true ids
        oracle_keys = {k for frame in viewer.decode_deltas(self.long_data["frames"]) for room in frame["R"].values() for k in room["e"]}
        self.assertTrue(oracle_keys <= ids)
        # a perceived-only export carries no privileged data at all
        out = viewer.export_viewer(self.small_path, self.tmp / "agent_only.html", view="perceived")
        data = viewer.extract_data(out.read_text(encoding="utf-8"))
        self.assertNotIn("frames", data)
        self.assertNotIn("diffs", data)
        self.assertEqual(data["meta"]["views"], ["perceived"])
        self.assertNotIn("success_rules", data["meta"])
        found = strings_and_tokens(data, (set(), set()))
        small_ids = self.true_ids(self.small_env)
        self.assertFalse(found[0] & small_ids, sorted(found[0] & small_ids)[:10])
        self.assertFalse(found[1] & small_ids, sorted(found[1] & small_ids)[:10])

    def test_test_split_refusal(self):
        # an episode record from the test split is refused before its command log is even read
        run = self.tmp / "results" / "run_test"
        (run / "logs").mkdir(parents=True)
        record = {"catalog_id": "T-anything", "split": "test", "seed_tag": "test:0123456789ab",
                  "log": "logs/T-anything__test_0123456789ab.json", "success": False}
        (run / "episodes.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        with self.assertRaises(viewer.SplitRefused):
            viewer.export_viewer(run / "episodes.jsonl")
        with self.assertRaises(viewer.SplitRefused):
            viewer.export_viewer(record)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = viewer.main(["--viewer", str(run / "episodes.jsonl"), "--out", str(self.tmp / "refused.html")])
        self.assertEqual(code, 2)
        self.assertIn("--private", err.getvalue())
        self.assertFalse((self.tmp / "refused.html").exists())
        self.assertFalse((run / "viewer").exists())
        # a replay whose seed lies in the private range: refused, then exported only with the private flag
        private_seed = 900000007
        path, env = make_replay(self.tmp, "private_seed", private_seed, 6, "oracle", False)
        with self.assertRaises(viewer.SplitRefused):
            viewer.export_viewer(path, self.tmp / "private.html")
        out = viewer.export_viewer(path, private=True)
        self.assertIn("PRIVATE", out.name)
        text = out.read_text(encoding="utf-8")
        data = viewer.extract_data(text)
        self.assertTrue(data["meta"]["private"])
        self.assertEqual(data["meta"]["split"], "test")
        self.assertNotIn("seed", data["meta"])
        self.assertIn("<title>PRIVATE - ", text)
        self.assertIn("PRIVATE - test split", text)
        self.assertEqual(len(data["frames"]), len(env._history) + 1)

    def test_perceived_only_layout_holds_only_what_the_agent_saw(self):
        """Regression (red team, viewer): a perceived-only page used to embed and draw the whole true floor plan,
        labels, sizes, floors and doorway cells of rooms the robot never perceived included."""
        out = viewer.export_viewer(self.small_path, self.tmp / "agent_layout.html", view="perceived")
        data = viewer.extract_data(out.read_text(encoding="utf-8"))
        layout = data["layout"]
        pframes = [f for f in viewer.decode_deltas(data["pframes"]) if not f.get("na")]
        seen = {f["r"] for f in pframes}
        exits = {c["to"] for f in pframes for room in f["R"].values() for c in room.get("c", [])}
        true_rooms = self.small_env._replay_start["rooms"]
        self.assertTrue(seen and len(seen) < len(true_rooms), (seen, len(true_rooms)))   # the check means something
        self.assertTrue(set(layout["rooms"]) <= seen, sorted(set(layout["rooms"]) - seen))
        self.assertEqual(set(layout["rooms"]), seen)
        self.assertEqual({r[0] for floor in layout["floors"] for r in floor["rooms"]}, seen)
        stubs = set(layout["stubs"])
        self.assertTrue(stubs, "the agent saw exits it never took")
        self.assertTrue(stubs <= exits, sorted(stubs - exits))
        self.assertFalse(stubs & seen)
        for link in layout["links"]:
            self.assertIn(link["a"], seen)
            self.assertIn(link["b"], seen | stubs)
            if link["b"] in stubs:
                self.assertNotIn("bx", link)          # the far side's doorway cell was never observed
        truth = viewer.compute_layout(self.small_env._replay_start)
        for rid in seen:                              # observed rooms: as the agent was told
            for key in ("l", "cols", "rows"):
                self.assertEqual(layout["rooms"][rid][key], truth["rooms"][rid][key], (rid, key))
        seen_labels = {true_rooms[rid].get("label") for rid in seen}
        text = json.dumps(layout)
        for rid in set(true_rooms) - seen:
            label = true_rooms[rid].get("label")
            if label and label not in seen_labels:
                self.assertNotIn(json.dumps(label), text, rid)
        # never-perceived rooms that no observed exit named appear nowhere in the page data
        page = json.dumps(data)
        for rid in set(true_rooms) - seen - stubs:
            self.assertNotIn(json.dumps(rid), page, rid)
        # pages with the oracle view keep the full plan
        both = viewer.build_viewer_data(self.small_path, view="both")
        self.assertEqual(set(both["layout"]["rooms"]), set(true_rooms))
        self.assertNotIn("stubs", both["layout"])
        # after a divergence the perceived frames stop; a perceived-only page still names no unobserved room
        path, _ = make_replay(self.tmp, "realistic_traced", 5, 16, "realistic", True)
        replay = replays.read(path)
        replay["steps"][1]["ok"] = not replay["steps"][1]["ok"]
        replays.write(path, replay, keep_changes=True)
        data = viewer.build_viewer_data(path, view="perceived")
        frames = viewer.decode_deltas(data["pframes"])
        self.assertTrue(any(f.get("na") for f in frames))
        observed = {f["r"] for f in frames if not f.get("na")}
        self.assertEqual(set(data["layout"]["rooms"]), observed)
        self.assertTrue({f["r"] for f in frames if f.get("na") and "r" in f} <= observed)

    def test_seed_type_cannot_bypass_the_test_split_guard(self):
        """Regression (red team, viewer): a test replay whose seed was written as a string or a float skipped the
        private-range check and was rendered as a public page carrying the raw hidden seed."""
        replay = json.loads(self.oracle_path.read_text(encoding="utf-8"))

        def variant(name, seed):
            path = self.tmp / "seedtype_{}.json".format(name)
            path.write_text(json.dumps(dict(replay, seed=seed)), encoding="utf-8")
            return path
        for name, seed in (("str", str(PRIVATE_SEED)), ("float", float(PRIVATE_SEED)), ("padded", " {} ".format(PRIVATE_SEED)),
                           ("strfloat", "{}.0".format(PRIVATE_SEED))):
            path = variant(name, seed)
            out = self.tmp / "seedtype_{}.html".format(name)
            with self.assertRaises(viewer.SplitRefused, msg=name):
                viewer.export_viewer(path, out)
            self.assertFalse(out.exists(), name)
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(viewer.main(["--viewer", str(path), "--out", str(out)]), 2, name)
            self.assertFalse(out.exists(), name)
            page = viewer.export_viewer(path, private=True)
            text = page.read_text(encoding="utf-8")
            data = viewer.extract_data(text)
            self.assertTrue(data["meta"]["private"], name)
            self.assertEqual(data["meta"]["split"], "test", name)
            self.assertNotIn("seed", data["meta"], name)
            self.assertNotIn(str(PRIVATE_SEED), text, name)
            self.assertIn("PRIVATE - test split", text)
            self.assertIn("PRIVATE", page.name)
        # a public seed written as text is shown as the int it is
        data = viewer.build_viewer_data(variant("public_str", " 1 "))
        self.assertIs(type(data["meta"]["seed"]), int)
        self.assertEqual(data["meta"]["seed"], 1)
        self.assertFalse(data["meta"]["private"])
        # seeds that are not integers cannot be checked: refused as unusable (exit code 1), nothing written
        for name, seed in (("text", "abc"), ("fraction", PRIVATE_SEED + 0.5), ("bool", True), ("list", [PRIVATE_SEED]),
                           ("object", {"seed": PRIVATE_SEED}), ("nan", "nan"), ("inf", "inf")):
            with self.assertRaises(viewer.ViewerError, msg=name) as caught:
                viewer.build_viewer_data(variant(name, seed), private=True)
            self.assertNotIsInstance(caught.exception, viewer.SplitRefused, name)
            self.assertIn("seed", str(caught.exception), name)
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(viewer.main(["--viewer", str(variant(name, seed)), "--out", str(self.tmp / "bad.html")]), 1)
            self.assertFalse((self.tmp / "bad.html").exists())
        # a replay with a start state but no seed cannot be shown to be public: private only
        path = variant("none", None)
        with self.assertRaises(viewer.SplitRefused):
            viewer.build_viewer_data(path)
        data = viewer.build_viewer_data(path, private=True)
        self.assertTrue(data["meta"]["private"])
        self.assertNotIn("seed", data["meta"])
        # an episode record with a private-range seed written as text is refused before its log is read
        for seed in (str(PRIVATE_SEED), float(PRIVATE_SEED)):
            record = {"catalog_id": "custom", "split": "dev", "seed_tag": "dev:0", "seed": seed, "log": "logs/never_read.json"}
            with self.assertRaises(viewer.SplitRefused):
                viewer.export_viewer(record)
        # a seed listed in the private manifest (outside the private range), written as text or as a float
        manifest = Path.home() / ".house_ai" / "test_manifest.json"
        self.assertTrue(str(manifest.resolve()).startswith(str(self.home.resolve())))
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"format": "house_ai.test_manifest/1", "entries": {"custom": {"seeds": [77]}}}),
                            encoding="utf-8")
        self.clear_split_caches()
        try:
            for name, seed in (("listed_str", "77"), ("listed_float", 77.0), ("listed_int", 77)):
                with self.assertRaises(viewer.SplitRefused, msg=name):
                    viewer.build_viewer_data(variant(name, seed))
        finally:
            manifest.unlink()
            self.clear_split_caches()
        # split markers are matched regardless of case and padding; metadata seeds are normalized too
        for meta in ({"split": " Test "}, {"seed_tag": "TEST:0123456789ab"}, {"seed_tag": "test-0123456789ab"},
                     {"seed": str(PRIVATE_SEED)}, {"seed": float(PRIVATE_SEED)}):
            self.assertTrue(viewer.is_test_episode(meta), meta)
        self.assertEqual(viewer.normalize_seed("{}.0".format(PRIVATE_SEED)), PRIVATE_SEED)
        self.assertIsNone(viewer.normalize_seed(None))

    def test_private_page_hides_seed_bearing_tags_and_names(self):
        """A private page (seed in the private range) never shows the raw seed inside a public-style seed tag or the
        source file name either."""
        replay = json.loads(self.oracle_path.read_text(encoding="utf-8"))
        replay["seed"] = PRIVATE_SEED
        replay["meta"] = {"seed_tag": "seed:{}".format(PRIVATE_SEED)}
        path = self.tmp / "custom__seed-{}.json".format(PRIVATE_SEED)
        path.write_text(json.dumps(replay), encoding="utf-8")
        data = viewer.build_viewer_data(path, private=True)
        self.assertTrue(data["meta"]["private"])
        self.assertNotIn("seed_tag", data["meta"])
        self.assertNotIn(str(PRIVATE_SEED), json.dumps(data["meta"]))

    def buildable_spec(self):
        from house_sim.eval.common import load_authored
        from house_sim.tasks.runner import build
        specs, _, _ = load_authored()
        for catalog_id in sorted(specs)[:25]:
            try:
                config, task, _ = build(specs[catalog_id], 0)
            except Exception:  # noqa: BLE001 - another component may be mid-edit; any buildable spec will do
                continue
            return catalog_id, specs[catalog_id], config, task
        self.skipTest("no buildable authored spec")

    def test_harness_log_and_episode_record(self):
        """A command log in the harness format (house-harness-log-v1) without a saved replay: rebuilt from the spec."""
        from house_sim.tasks.verify import spec_hash
        chosen, spec, config, task = self.buildable_spec()
        env = HouseEnv(config, task, 0, perception="realistic", trace=False, observe_on_step=True)
        obs = env.observe()
        log_steps = []
        for i in range(12):
            visible = sorted(h for h, r in obs["visible_entities"].items() if r.get("pos"))
            exits = sorted(c["to"] for c in obs["connections"])
            if i % 4 == 0 and exits:
                command = {"action": "move", "target": exits[0]}
            elif i % 4 == 1 and visible:
                command = {"action": "approach", "target": visible[i % len(visible)]}
            elif i % 4 == 2 and visible:
                command = {"action": "inspect", "target": visible[(2 * i) % len(visible)]}
            else:
                command = {"action": "wait", "seconds": 15}
            sent = dict(command, destination="") if command["action"] != "wait" else dict(command)
            result = env.step(**sent)
            obs = result["observation"]
            log_steps.append({"i": i + 1, "command": dict(command, _raw="model text"), "sent": sent, "parse": "ok",
                              "result": {"ok": result["ok"], "message": result["message"], "category": result["category"]},
                              "t_agent_s": 0.01, "t_env_s": 0.02, "sim_time_s": env.world.s["time_s"]})
        log_steps.append({"i": 13, "command": {"action": "done"}, "done": True})
        run = self.tmp / "results" / "run_dev"
        (run / "logs").mkdir(parents=True)
        name = "{}__dev-0".format(chosen)
        log = {"format": "house-harness-log-v1", "catalog_id": chosen, "split": "dev", "seed_tag": "dev:0", "seed": 0,
               "perception": "realistic", "agent": "scripted-test", "privileged": False, "require_done": True,
               "instruction": task["instruction"], "steps": log_steps, "done_declared": True}
        (run / "logs" / (name + ".json")).write_text(json.dumps(log), encoding="utf-8")
        record = {"catalog_id": chosen, "split": "dev", "seed_tag": "dev:0", "seed": 0, "agent": "scripted-test",
                  "privileged": False, "success": env.evaluate()["success"], "steps": 12, "log": "logs/{}.json".format(name),
                  "spec_hash": spec_hash(spec), "perception": "realistic", "done_declared": True}
        (run / "episodes.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        out = viewer.export_viewer(str(run / "episodes.jsonl") + "#0")
        self.assertEqual(out, run / "viewer" / (name + ".html"))
        data = viewer.extract_data(out.read_text(encoding="utf-8"))
        self.assertEqual(len(data["steps"]), 12)            # the agent's "done" is not an env step
        self.assertEqual(len(data["frames"]), 13)
        self.assertEqual(len(data["pframes"]), 13)
        self.assertEqual(data["meta"]["seed_tag"], "dev:0")
        self.assertEqual(data["meta"]["agent"], "scripted-test")
        self.assertTrue(data["meta"]["done_declared"])
        self.assertEqual(data["meta"]["warnings"], [])
        self.assertEqual([s["ok"] for s in data["steps"]], [s["result"]["ok"] for s in log_steps[:12]])
        self.assertEqual([s.get("am", s.get("m")) for s in data["steps"]], [s["result"]["message"][:600] for s in log_steps[:12]])
        self.assertEqual(viewer.decode_deltas(data["frames"])[-1]["rp"], list(env.world.s["robot"]["pos"]))
        self.assertIn(data["meta"]["spec_status"]["state"].split(" ")[0], ("verified", "unverified", "unknown"))
        # the bare log path finds its record next to it and gives the same frames
        again = viewer.extract_data(viewer.export_viewer(run / "logs" / (name + ".json"), self.tmp / "log.html").read_text(encoding="utf-8"))
        self.assertEqual(again["frames"], data["frames"])
        self.assertEqual(again["meta"]["agent"], "scripted-test")
        # explicit on-the-fly verification of the episode's spec (dev seeds, the spec's own perception mode)
        checked = viewer.build_viewer_data(run / "logs" / (name + ".json"), view="oracle", verify_spec=True)
        self.assertIn(checked["meta"]["spec_status"]["state"], ("verified now", "failed verification now"))
        self.assertEqual(checked["meta"]["spec_status"]["seeds"], [0, 1, 2, 41, 97])
        self.assertNotIn("pframes", checked)

    def test_real_harness_episode(self):
        """End to end with the harness (house_sim/eval/run.py) when it is available: its log and saved replay."""
        try:
            from house_sim.eval.agents import make_agent
            from house_sim.eval.run import run_episode
        except ImportError:
            self.skipTest("harness not available")
        chosen, spec, _, _ = self.buildable_spec()
        run = self.tmp / "results" / "run_harness"
        episode = {"catalog_id": chosen, "split": "dev", "seed": 0, "seed_tag": "dev:0", "phrasing": None, "perception": "realistic"}
        try:
            record = run_episode(spec, episode, make_agent("random"), caps={"max_steps": 8, "max_wall_s": 600}, out_dir=run)
        except Exception as exc:  # noqa: BLE001 - the harness is another component, built concurrently
            self.skipTest("harness run failed: {}: {}".format(type(exc).__name__, exc))
        self.assertTrue(record.get("log"), record)
        (run / "episodes.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        out = viewer.export_viewer(run / "episodes.jsonl")
        self.assertEqual(out.parent, run / "viewer")
        data = viewer.extract_data(out.read_text(encoding="utf-8"))
        self.assertEqual(len(data["frames"]), record["steps"] + 1)
        self.assertEqual(len(data["pframes"]), record["steps"] + 1)
        self.assertEqual(data["meta"]["split"], "dev")
        self.assertFalse([w for w in data["meta"]["warnings"] if "diverge" in w or "differs" in w], data["meta"]["warnings"])

    def test_divergence_falls_back_to_recorded_changes(self):
        replay = replays.read(self.oracle_path)
        replay["steps"][2]["ok"] = not replay["steps"][2]["ok"]
        path = self.tmp / "diverged.json"
        replays.write(path, replay, keep_changes=True)
        data = viewer.build_viewer_data(path)
        n = len(replay["steps"])
        self.assertEqual(len(data["frames"]), n + 1)
        self.assertEqual(data["meta"]["diverged_at"], 3)
        self.assertTrue(any(w.startswith("Step 3:") for w in data["meta"]["warnings"]))
        self.assertEqual(viewer.decode_deltas(data["frames"])[-1]["rp"], list(self.oracle_env.world.s["robot"]["pos"]))
        self.assertIn("dv", data["steps"][2])

    def test_cli_and_wrapper(self):
        from house_sim.cli import main as cli_main
        out = self.tmp / "cli" / "page.html"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_main(["--viewer", str(self.oracle_path), "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue().strip(), str(out))
        self.assertTrue(out.is_file())
        wrapper_out = self.tmp / "wrapper.html"
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "export_viewer.py"), str(self.oracle_path), "--out", str(wrapper_out)],
                              cwd=str(self.tmp), capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), str(wrapper_out))
        self.assertEqual(viewer.extract_data(wrapper_out.read_text(encoding="utf-8")), viewer.extract_data(out.read_text(encoding="utf-8")))
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(viewer.main(["--viewer", str(self.tmp / "missing.json")]), 1)

    def test_layout_is_stable_and_non_overlapping(self):
        state = self.long_env._replay_start
        first, second = viewer.compute_layout(state), viewer.compute_layout(json.loads(json.dumps(state)))
        self.assertEqual(first, second)
        placed = [r[0] for floor in first["floors"] for r in floor["rooms"]]
        self.assertEqual(sorted(placed), sorted(state["rooms"]))
        for floor in first["floors"]:
            boxes = floor["rooms"]
            for i, a in enumerate(boxes):
                self.assertEqual(first["rooms"][a[0]]["f"], floor["f"])
                self.assertLessEqual(a[1] + a[3], floor["W"] + 1e-6)
                self.assertLessEqual(a[2] + a[4], floor["H"] + 1e-6)
                for b in boxes[i + 1:]:
                    overlap = a[1] < b[1] + b[3] - 1e-6 and b[1] < a[1] + a[3] - 1e-6 and a[2] < b[2] + b[4] - 1e-6 and b[2] < a[2] + a[4] - 1e-6
                    self.assertFalse(overlap, (a, b))
        self.assertEqual(self.long_data["layout"], first)

    def test_merge_patch_and_embedding(self):
        frames = [{"a": 1, "b": {"c": [1, 2], "d": "x"}, "e": {"f": 1}},
                  {"a": 1, "b": {"c": [1, 2, 3]}, "e": {}, "g": True},
                  {"a": 2, "b": {"c": [1, 2, 3], "d": "y"}, "g": False},
                  {"a": 2, "b": {}, "g": False, "h": {"i": {"j": 0}}}]
        stream = viewer._DeltaStream()
        for frame in frames:
            stream.add(frame)
        self.assertEqual(viewer.decode_deltas(stream.items), frames)
        self.assertEqual(stream.items[1], {"b": {"c": [1, 2, 3], "d": None}, "e": {"f": None}, "g": True})
        nasty = {"format": viewer.FORMAT, "meta": {"title": "</script><!-- <b>&amp; https://example.invalid/x  "},
                 "layout": {"rooms": {}, "floors": [], "links": []}, "steps": [], "evals": [{"na": True}]}
        html_text = viewer.render_html(nasty)
        self.assertEqual(viewer.extract_data(html_text), nasty)
        self.assertNotIn("https://", html_text)
        self.assertEqual(html_text.count("</script>"), 2)

    # ----- adoption round (K-EVAL): compact replays, multi-phase episodes, the monitor's milestones ------------------
    def test_a_v2_replay_and_its_compact_form_render_the_same_page(self):
        env = HouseEnv(generate_house(seed=4), dict(EXPLORE_TASK), 4, perception="realistic", trace=False)
        scripted_run(env, 10)
        plain, compact = self.tmp / "same_v2.json", self.tmp / "same_v3.json"
        env.save_replay(plain, compact=False)
        env.save_replay(compact)
        a, b = viewer.build_viewer_data(plain), viewer.build_viewer_data(compact)
        for key in ("frames", "pframes", "steps", "evals", "layout"):
            self.assertEqual(a[key], b[key], key)
        self.assertEqual(a["meta"]["warnings"], [])

    def test_a_compact_traced_replay_takes_its_diffs_from_the_re_run(self):
        path, env = make_replay(self.tmp, "lean_traced", 1, 12, "oracle", True, keep_changes=False)
        self.assertFalse(any("changes" in s for s in replays.read(path)["steps"]))
        lean = viewer.build_viewer_data(path)
        full_path, _ = make_replay(self.tmp, "full_traced", 1, 12, "oracle", True, keep_changes=True)
        full = viewer.build_viewer_data(full_path)
        self.assertEqual(lean["diffs"], full["diffs"])            # the re-run's trace gives the recorded changes
        self.assertTrue(any(lean["diffs"]))
        self.assertEqual(lean["frames"], full["frames"])

    def test_recorded_milestones_come_from_the_monitor_record_after_a_divergence(self):
        """Regression (TIME-EVAL request 4): the frames after a divergence re-evaluated milestones with a plain
        predicate check, which fails on the extended forms ({"pred": ..., "held_for_s": ...}) and misses times reached
        inside waits; they now come from the recording's monitor record (the final evaluation's milestones)."""
        task = dict(EXPLORE_TASK, milestones={"settled": {"pred": {"path": "time_s", "op": "gt", "value": 10},
                                                          "held_for_s": 20}})
        env = HouseEnv(generate_house(seed=1), task, 1, trace=True)
        scripted_run(env, 14)
        reached = env.evaluate()["milestones"]["settled"]
        self.assertGreater(reached["step"], 3)
        path = self.tmp / "milestone_diverged.json"
        env.save_replay(path, keep_changes=True)
        replay = replays.read(path)
        replay["steps"][1]["ok"] = not replay["steps"][1]["ok"]          # diverges at step 2
        replays.write(path, replay, keep_changes=True)
        data = viewer.build_viewer_data(path, view="oracle")
        self.assertEqual(data["meta"]["diverged_at"], 2)
        evals = viewer.decode_deltas(data["evals"])
        self.assertEqual(evals[-1]["m"]["settled"], [reached["step"], round(reached["time_s"], 1)])
        self.assertNotIn("settled", evals[reached["step"] - 1].get("m", {}))
        self.assertIn("settled", evals[reached["step"]]["m"])

    def phased_episode(self, folder):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "T-viewer-phases-01", "title": "two requests", "risk_level": 0,
                "house": {"residents": "minimal", "pets": "none"},
                "phases": [{"id": "p1", "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say one."]},
                            "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "one"}],
                            "milestones": {"spoke": {"path": "facts.last_speech.text", "op": "eq", "value": "one"}},
                            "budget": {"steps": 20, "time_s": 900}},
                           {"id": "p2", "start": {"after": "p1", "gap_s": 600},
                            "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say two."]},
                            "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "two"}],
                            "budget": {"steps": 20, "time_s": 900}}],
                "reference": [{"do": "announce", "text": "one"}, {"phase_done": True},
                              {"do": "announce", "text": "two"}, {"phase_done": True}],
                "wrong": [{"name": "stops early", "plan": [{"do": "announce", "text": "one"}]}]}
        config, task, _ = build(spec, 0, perception="realistic")
        env = HouseEnv(config, task, 0, perception="realistic", trace=False)
        env.step("announce", text="one")
        env.phase_done()
        env.step("announce", text="two")
        env.phase_done()
        self.assertTrue(env.evaluate()["success"])
        path = Path(folder) / "phases.json"
        env.save_replay(path)
        return spec, env, path

    def test_a_multi_phase_replay_shows_its_phase_ends(self):
        """TIME-EVAL request 4: phase_done history records re-run as env.phase_done() (they used to be sent to
        env.step and diverge) and are listed as phase_done steps (ended phase -> next phase)."""
        _, env, path = self.phased_episode(self.tmp)
        data = viewer.build_viewer_data(path)
        self.assertEqual(data["meta"]["warnings"], [])
        self.assertEqual([(s["a"], s.get("tg"), s.get("de")) for s in data["steps"]],
                         [("announce", None, None), ("phase_done", "p1", "p2"), ("announce", None, None),
                          ("phase_done", "p2", None)])
        self.assertEqual(data["meta"]["phases"], ["p1", "p2"])
        self.assertEqual(data["meta"]["milestones"], ["p1/spoke"])
        evals = viewer.decode_deltas(data["evals"])
        self.assertTrue(evals[-1]["s"])
        self.assertIn("p1/spoke", evals[-1]["m"])
        self.assertEqual(len(data["frames"]), len(env._history) + 1)
        # the phase boundaries are timeline marks of the phase_done frames (a phase end shares the step number of the
        # action before it: its frame lists only its own events, never that action's again)
        marks = data.get("marks", {})
        self.assertEqual([m[:2] for m in marks.get("2", [])], [["phase_end", "p1"], ["phase_start", "p2"]])
        self.assertEqual([m[0] for m in marks.get("4", [])], ["phase_end"])
        self.assertNotIn("1", marks)
        page = viewer.render_html(data)
        self.assertIn(".mk.time", page)

    def test_notices_and_orders_are_time_marks(self):
        state = {"steps": 3, "event_log": [{"step": 3, "kind": "standing_order_fired", "order": 0},
                                           {"step": 2, "kind": "phase_start", "phase": "p1"},
                                           {"step": 3, "kind": "phys.fall", "target": "cup_1", "by": "robot"}],
                 "notices": [{"step": 3, "kind": "addressed", "text": "Ann: come here"},
                             {"step": 2, "kind": "speech", "text": "earlier"}]}
        self.assertEqual(viewer.step_marks(state), [["standing_order_fired", "order 0"], ["phys.fall", "cup_1 (robot)"],
                                                    ["notice", "addressed: Ann: come here"]])

    def test_a_multi_phase_harness_log_re_runs_its_phase_ends(self):
        """A harness log's done that ended a phase ({"done": true, "phase_done": true, ...}) is re-run as
        env.phase_done() (it was skipped as a plain done, so the re-run never left phase 1)."""
        spec, env, _ = self.phased_episode(self.tmp)
        steps, n = [], 0
        for record in env._history:
            if record.get("phase_done"):
                steps.append({"i": n, "command": {"action": "done"}, "done": True, "phase_done": True,
                              "phase": record["phase"], "next_phase": record["next_phase"], "waiting": False})
            else:
                n += 1
                steps.append({"i": n, "command": record["command"], "sent": record["command"],
                              "result": {"ok": record["ok"], "message": "", "category": record["category"]}})
        log = {"format": "house-harness-log-v1", "catalog_id": spec["catalog_id"], "split": "dev", "seed_tag": "dev:0",
               "seed": 0, "perception": "realistic", "agent": "scripted-test", "privileged": False,
               "require_done": True, "steps": steps, "done_declared": True}
        path = self.tmp / "phases_log.json"
        path.write_text(json.dumps(log), encoding="utf-8")
        with mock.patch.object(viewer, "_find_spec", lambda cid: copy.deepcopy(spec) if cid == spec["catalog_id"] else None):
            data = viewer.build_viewer_data(path)
        self.assertEqual(data["meta"]["warnings"], [])
        self.assertEqual([s["a"] for s in data["steps"]], ["announce", "phase_done", "announce", "phase_done"])
        self.assertTrue(data["meta"]["done_declared"])
        self.assertTrue(viewer.decode_deltas(data["evals"])[-1]["s"])

    def test_the_perceived_layout_reads_the_minds_known_map(self):
        """The perceived-only page's known rooms come from env.known_map() (the Mind's map; env._known_map is a
        read-only compatibility view of it)."""
        data = viewer.build_viewer_data(self.small_path, view="perceived")
        env = self.small_env
        self.assertTrue(set(env.known_map()) <= set(data["layout"]["rooms"]) | set(data["layout"].get("stubs", [])))


if __name__ == "__main__":
    unittest.main()
