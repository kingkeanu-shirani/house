"""--workers N (house_sim/eval/run.py): an episode's outcome must not depend on what runs beside it.

The simulator keeps per-episode working data in module-level objects, so episodes that share a process read each other's
values: sometimes with an exception (sim_error, or a plan_error the oracle swallows), sometimes with only a different
record or final state. With worker threads, TestEpisodesAreIsolated fails: the flag test every time (a harm flag is lost)
and the low switch interval test on most runs (real episodes, nothing injected). Outcomes are compared on the record, the
whole command log and the replay (final state digest, and a checksum of every step and starting state), not the record alone.

Separate processes bring faults threads never had, covered by TestWhenAProcessDies: a worker that dies, a parent that dies,
a script that a worker imports again.

Fully offline: a temporary HOME/USERPROFILE (worker processes inherit it), no API keys, no split key, no network. Episode
nonces are pinned, since handles and fixed draws follow the nonce and a public episode otherwise gets a fresh random one.
"""
import hashlib
import json
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest import mock

from house_sim.eval import agents as A
from house_sim.eval import llm as L
from house_sim.eval import run as R

ROOT = Path(__file__).resolve().parents[1]
WORKERS = 2
HANG_LIMIT_S = 180                 # what a run that hangs gets, far above what these runs take on a machine under load
WET = "T-089c40fb507289-01"        # one-step reference: the cheapest episode there is
GLASSES = "T-6af2ea41755bbe-01"    # a few steps
# oracle episodes that spend the most time in the racy paths (fridge and plant ticks), by the threaded reproduction
HOT = [("A-d66994a566899c-01", 97), ("T-e7a39a7a0aa001-01", 41)]
ROUNDS = 2
# fails with "damage" in one worker: a robot-caused freeze burst, unattributed while anything is in a plumbing system
PLUMBING_HARM = ("T-5d2d79cc6ec2ca-01", 0)
_ENV_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HOUSE_AI_LLM_BASE_URL", "HOUSE_AI_FORBID_NETWORK", "HOUSE_AI_SPLIT_KEY",
             "HOME", "USERPROFILE")
_SAVED = {}
_RECORDS = {}
_VERSIONS = {}


def setUpModule():
    for key in _ENV_KEYS:
        _SAVED[key] = os.environ.pop(key, None)
    os.environ["HOUSE_AI_FORBID_NETWORK"] = "1"
    _SAVED["home"] = tempfile.mkdtemp(prefix="workers_home_")
    os.environ["HOME"] = os.environ["USERPROFILE"] = _SAVED["home"]   # never the user's real ~/.house_ai


def tearDownModule():
    for key in _ENV_KEYS:
        if _SAVED.get(key) is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = _SAVED[key]
    shutil.rmtree(_SAVED["home"], ignore_errors=True)


def record(cid):
    if not _RECORDS:
        _RECORDS.update((r["catalog_id"], r) for r in R.load_specs())
    return _RECORDS[cid]


def versions(real=R.versions):
    if not _VERSIONS:           # fingerprints hash the whole tree: once per test module is enough
        _VERSIONS.update(real())
    return dict(_VERSIONS)


def nonce(cid, seed):
    return hashlib.sha256("parallel-workers:{}:{}".format(cid, seed).encode("utf-8")).hexdigest()[:32]


def run_episodes(pairs, out, workers, agent="oracle", **filters):
    """R.run on exactly these (catalog id, seed) episodes, in this order, with pinned nonces. -> (summary, run.json)"""
    original = R.make_episodes

    def pinned(split, records, seeds_per_entry=None, seeds=None, perception="realistic"):
        made, source = original(split, records, seeds=sorted({s for _, s in pairs}), perception=perception)
        by_key = {(e["catalog_id"], e["seed"]): e for e in made}
        return [dict(by_key[pair], episode_nonce=nonce(*pair)) for pair in pairs], source

    records = [record(c) for c in dict.fromkeys(c for c, _ in pairs)]
    with mock.patch.object(R, "make_episodes", pinned), mock.patch.object(R, "versions", versions):
        summary = R.run(agent, "dev", records, out, workers=workers, verify="none", emit=None, **filters)
    return summary, json.loads((Path(out) / "run.json").read_text(encoding="utf-8"))


def without(row, *names):
    return {k: v for k, v in row.items() if k not in names}


def outcomes(out):
    """[((catalog id, seed tag), what the episode came to)] in episode order: everything an episode's outcome is recorded
    in, without wall times and the names that depend on the run."""
    out, rows = Path(out), []
    for line in (out / "episodes.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        log = json.loads((out / rec["log"]).read_text(encoding="utf-8"))
        steps = [without(step, "t_agent_s", "t_env_s") for step in log["steps"]]
        # packed_sha256 covers every step record, the initial config, the start state and the baseline
        replay = None if not log.get("replay") else without(json.loads((out / log["replay"]).read_text(encoding="utf-8")), "packed")
        rows.append(((rec["catalog_id"], rec["seed_tag"]),
                     {"record": without(rec, "wall_s", "log"), "log": dict(without(log, "run_id", "wall_s", "replay"), steps=steps),
                      "replay": replay}))
    return rows


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="workers_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class TestEpisodesAreIsolated(Temp):
    def test_low_switch_interval_gives_the_outcomes_of_one_worker(self):
        """Real episodes, nothing injected: in threads these race, and the exceptions and changed outcomes show here."""
        run_episodes(HOT, self.tmp / "one", 1)
        expected = dict(outcomes(self.tmp / "one"))
        self.assertEqual({"done"}, {row["record"]["end_reason"] for row in expected.values()})
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            summary, info = run_episodes(HOT * ROUNDS, self.tmp / "many", WORKERS)
        finally:
            sys.setswitchinterval(previous)
        self.assertEqual("complete", info["status"], info.get("aborted"))
        self.assertEqual(len(HOT) * ROUNDS, summary["episodes"])
        parallel = outcomes(self.tmp / "many")
        problems = [(key, row["log"].get("error"), row["log"].get("agent_info", {}).get("plan_error")) for key, row in parallel]
        self.assertEqual([], [row for row in problems if row[1] or row[2]], "exceptions that episodes alone never raise")
        for key, outcome in parallel:
            for part in ("record", "log", "replay"):
                self.assertEqual(expected[key][part], outcome[part], "{} differs for {}".format(part, key))

    def test_a_flag_held_elsewhere_in_the_process_does_not_reach_the_episodes(self):
        """Deterministic. While anything in a process sits inside a plumbing system, robot_acting() is False for every
        episode in that process, so a freeze burst the robot caused is not counted against it: in threads the record
        loses its "damage" flag with no exception anywhere. A worker process has its own flag."""
        from house_sim.systems.plumbing_liquids import common as PL
        PL._CONTEXT["system"] += 1
        try:
            run_episodes([PLUMBING_HARM, (WET, 0)], self.tmp / "many", WORKERS, camera="geometric", save_replays=False)
        finally:
            PL._CONTEXT["system"] -= 1
        harmed = outcomes(self.tmp / "many")[0][1]["record"]
        self.assertEqual(["damage"], harmed["failures"], "as with one worker: if this spec changed, pick another that shows it")


class TestOneEpisodePerProcess(Temp):
    def test_no_episode_of_a_parallel_run_runs_in_this_process(self):
        pairs = [(WET, 0), (GLASSES, 0), (WET, 1)]
        options = {"responses": [{"action": "look"}, {"action": "done"}]}
        run_episodes(pairs, self.tmp / "one", 1, agent="mock", agent_options=options, save_replays=False)

        def here(*args, **kwargs):
            raise AssertionError("an episode ran in the parent process")
        with mock.patch.object(R, "_run_episode", here):
            summary, info = run_episodes(pairs, self.tmp / "two", WORKERS, agent="mock", agent_options=options, save_replays=False)
        self.assertEqual("complete", info["status"], info.get("aborted"))
        self.assertEqual(3, summary["episodes"])
        self.assertEqual((WORKERS, WORKERS), (info["workers"], info["worker_processes"]))
        self.assertEqual(outcomes(self.tmp / "one"), outcomes(self.tmp / "two"))

    def test_a_second_episode_at_once_is_refused(self):
        with R._ONE_EPISODE:            # as if another thread were inside an episode
            with self.assertRaisesRegex(RuntimeError, "another episode is running in this process"):
                R.run_episode(record(WET), {"catalog_id": WET, "split": "dev", "seed": 0, "seed_tag": "dev:0", "phrasing": None,
                                            "perception": "realistic"}, A.make_agent("oracle"))
        self.assertFalse(R._ONE_EPISODE.locked(), "the guard is released after a refusal")

    def test_one_worker_and_one_episode_stay_in_this_process(self):
        for pairs, workers in (([(WET, 0), (WET, 1)], 1), ([(WET, 0)], 7)):
            _, info = run_episodes(pairs, self.tmp / "w{}".format(workers), workers, save_replays=False)
            self.assertEqual(("complete", 0), (info["status"], info["worker_processes"]))

    def test_a_worker_starts_as_the_parent_stands(self):
        # the parent's engine may have been imported before main() set HOUSE_SIM_SAFE; run.json records the parent's
        from house_sim.kernel import engine
        from house_sim.kernel.plugins import load_domains
        load_domains()
        context = multiprocessing.get_context("spawn")
        safe = not engine.SAFE
        with ProcessPoolExecutor(1, mp_context=context, initializer=R._worker_init,
                                 initargs=("oracle", {}, {}, safe, R._simulator_modules())) as pool:
            theirs = pool.submit(_worker_state).result()
        self.assertEqual(safe, theirs.pop("safe"))
        self.assertEqual(without(_worker_state(), "safe"), theirs, "the plugin tables fill in import order")


class DiesWhenUnpickled:
    """An agent option that ends a worker process the moment it arrives there."""

    def __reduce__(self):
        return os._exit, (3,)


class CountingAnswers:
    """One count of the model's answers meant for a whole run. It pickles, so only its being an object can refuse it: one
    copy per worker would quietly give each worker its own count, and the records would follow N."""

    def __init__(self):
        self.total = 0

    def __call__(self, body, index):
        self.total += 1
        return {"action": "look"} if self.total <= 3 else {"action": "done"}


class TestWhenAProcessDies(Temp):
    def test_a_worker_that_dies_starting_aborts_the_run(self):
        with mock.patch.object(R, "_worker_problem", lambda *a: None):      # it would be refused as an object otherwise
            summary, info = run_episodes([(WET, 0), (WET, 1)], self.tmp / "run", WORKERS, save_replays=False,
                                         agent_options={"poison": DiesWhenUnpickled()})
        self.assertEqual("aborted", info["status"])
        self.assertIn("worker process failed on " + WET, info["aborted"])
        self.assertEqual(0, summary["episodes"])
        self.assertEqual(2, info["episodes_skipped"])

    def test_a_worker_that_dies_mid_episode_holding_the_shared_locks_aborts_the_run(self):
        """The worst moment: nothing else can take the ledger's or the limiter's lock again. The other worker is ended by
        the pool, and the parent must not wait for the lock for ever: at bc1dc72 a dead worker was the whole process."""
        outcome = {}

        def play():
            outcome["result"] = run_episodes([(WET, 0), (WET, 1), (GLASSES, 0)], self.tmp / "run", WORKERS, agent="mock", save_replays=False,
                                             rpm=6000, caps={"max_cost_usd": 100.0}, agent_options={"responses": _die_holding_the_locks})
        thread = threading.Thread(target=play, daemon=True)
        thread.start()
        thread.join(HANG_LIMIT_S)
        blocked = thread.is_alive()
        left = multiprocessing.active_children()
        for child in left:
            child.kill()
        self.assertFalse(blocked, "run() never returned")
        self.assertEqual([], left, "no worker outlives the run")
        summary, info = outcome["result"]
        self.assertEqual("aborted", info["status"])
        self.assertIn("worker process failed on ", info["aborted"])
        self.assertIsNotNone(info["ended"], "the run finished its run.json")
        self.assertEqual(3 - summary["episodes"], info["episodes_skipped"])
        self.assertGreater(info["ledger"]["spent_usd"], info["totals"]["cost_usd"], "a lost episode's spending stays in the ledger")

    def test_the_workers_end_with_a_killed_parent(self):
        """A parent killed alone (a watchdog, Task Manager, subprocess.run's timeout). Workers that stay keep working and
        spending, and they hold the output pipes they inherited, so a caller's subprocess.run(capture_output=True,
        timeout=...), the gate's run_job among them, does not come back until they end."""
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p), WORKERS_TEST_OUT=str(self.tmp))
        parent = subprocess.Popen(_child("_parent_of_a_slow_run", self.tmp), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(ROOT))
        output, closed = [], threading.Event()

        def read():
            output.append(parent.stdout.read())
            closed.set()
        threading.Thread(target=read, daemon=True).start()
        left = []
        try:
            deadline = time.monotonic() + HANG_LIMIT_S
            while len(left) < WORKERS and parent.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
                left = [int(path.name.split("_")[1]) for path in self.tmp.glob("worker_*")]
            self.assertEqual(WORKERS, len(left), "both workers should be mid-episode now")
            parent.kill()
            parent.wait()
            self.assertTrue(closed.wait(60), "workers still hold the parent's output pipe 60 s after it was killed")
            deadline = time.monotonic() + 60
            while left and time.monotonic() < deadline:
                time.sleep(0.2)
                left = [pid for pid in left if _alive(pid)]
            self.assertEqual([], left, "workers alive 60 s after the parent was killed")
        finally:
            parent.kill()
            for pid in left:            # only those not seen to end: a pid that has ended can come back as anyone's
                _end(pid)
            closed.wait(10)
            parent.stdout.close()

    def test_a_script_without_a_guard_is_told_so(self):
        """A spawned worker imports the main script again. Unguarded, its run() would start a second run on the same
        files inside the worker: refused there, so the script fails at once and says why."""
        script = self.tmp / "unguarded.py"
        script.write_text(textwrap.dedent("""\
            from house_sim.eval import run as R
            summary = R.run("oracle", "dev", None, {out!r}, workers=2, ids=[{wet!r}], seeds=[0, 1], verify="none", emit=None, save_replays=False)
            print("ended", __name__, summary["status"])
            """).format(out=str(self.tmp / "run"), wet=WET), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
        done = subprocess.run([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(ROOT),
                              timeout=HANG_LIMIT_S)
        lines = done.stdout.decode("utf-8", "replace").split()
        self.assertEqual(["ended", "__main__", "aborted"], lines, "only the script itself reaches the end of its run")
        self.assertIn('if __name__ == "__main__"', done.stderr.decode("utf-8", "replace"))
        info = json.loads((self.tmp / "run" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("aborted", info["status"])
        self.assertIn("worker process failed on " + WET, info["aborted"])

    def test_an_interrupted_parent_keeps_the_records_of_the_episodes_it_waited_for(self):
        """Ctrl-C in the parent alone: the pool waits for the episodes that are out, and each writes its log."""
        with mock.patch.object(R, "wait", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_episodes([(WET, 0), (WET, 1), (GLASSES, 0)], self.tmp / "run", WORKERS, save_replays=False)
        rows = [json.loads(line) for line in (self.tmp / "run" / "episodes.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(WORKERS, len(rows))
        self.assertEqual(sorted(row["log"].split("/")[-1] for row in rows), sorted(path.name for path in (self.tmp / "run" / "logs").glob("*.json")))


def _child(function, *arguments):
    """A python process that runs a function of this module as its main program (no script for a worker to import again)."""
    code = "import importlib, sys; getattr(importlib.import_module(sys.argv[1]), sys.argv[2])(*sys.argv[3:])"
    return [sys.executable, "-c", code, __name__, function] + [str(argument) for argument in arguments]


def _alive(pid):
    if os.name == "nt":             # never os.kill(pid, 0) here: 0 is CTRL_C_EVENT on Windows
        listed = subprocess.run(["tasklist", "/FI", "PID eq {}".format(pid), "/NH"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return str(pid).encode("ascii") in listed.stdout
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()
    return bool(state) and not state.startswith(b"Z")   # a zombie that nobody has reaped yet has ended


def _end(pid):
    try:
        os.kill(pid, signal.SIGTERM)    # TerminateProcess on Windows
    except OSError:
        pass


def _parent_of_a_slow_run(out):
    run_episodes([(WET, seed) for seed in (0, 1, 2, 41)], Path(out) / "run", WORKERS, agent="mock", save_replays=False,
                 agent_options={"responses": _slow_looks})


def _slow_looks(body, index):
    """A model that takes its time, and leaves a note that this worker is mid-episode."""
    (Path(os.environ["WORKERS_TEST_OUT"]) / "worker_{}".format(os.getpid())).touch()
    time.sleep(0.2)
    return {"action": "look"}


def _die_holding_the_locks(body, index):
    if index == 0:
        return {"action": "look"}
    R._WORKER["arguments"]["ledger"]._lock.acquire()
    R._WORKER["agent_options"]["rate_limiter"]._lock.acquire()
    os._exit(9)


class TestWhatEpisodesShare(Temp):
    def test_the_run_cost_cap_holds_across_workers(self):
        # mock prices: $0.0125 per call, less than each call reserves first, so the cap is a hard limit even at once
        usage = {"input_tokens": 10000, "output_tokens": 500, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        summary, info = run_episodes([(WET, 0), (WET, 1), (GLASSES, 0)], self.tmp / "run", WORKERS, agent="mock", caps={"max_cost_usd": 0.1},
                                     save_replays=False, agent_options={"responses": [{"action": "look"}] * 50, "usage": usage,
                                                                        "max_output_tokens": 1000})
        rows = [json.loads(line) for line in (self.tmp / "run" / "episodes.jsonl").read_text(encoding="utf-8").splitlines()]
        spent = sum(row["cost_usd"] for row in rows)
        self.assertGreater(spent, 0.0125)
        self.assertLessEqual(spent, 0.1, "workers must not overshoot the cap together")
        self.assertAlmostEqual(spent, info["ledger"]["spent_usd"], places=4, msg="every worker's spending is in the one ledger")
        self.assertEqual(0.0, info["ledger"]["reserved_usd"])
        self.assertTrue(any(row["end_reason"] == "run_cost_cap" for row in rows), [row["end_reason"] for row in rows])

    def test_the_ledger_and_the_rate_limiter_are_one_object_across_processes(self):
        context = multiprocessing.get_context("spawn")
        ledger = A.SharedCostLedger(100.0, context)
        limiter = L.SharedRateLimiter(1200, context)        # one start every 0.05 s
        limiter.clock, limiter.sleep = _clock_stands_still, _no_sleep
        delays, refused = context.RawArray("d", 8), context.RawArray("d", 1)
        barrier = context.Barrier(2)
        ledger.reserve(75.0)
        worker = context.Process(target=_share, args=(ledger, limiter, delays, refused, 4, barrier))
        worker.start()
        _share(ledger, limiter, delays, refused, 0, barrier)
        worker.join()
        self.assertEqual(0, worker.exitcode)
        self.assertEqual(1.0, refused[0], "a reservation in one process counts in the other")
        ledger.release(75.0)
        self.assertEqual({"spent_usd": 8.0, "reserved_usd": 0.0, "max_cost_usd": 100.0}, ledger.snapshot())
        # on a clock that stands still, a request's delay is its place in the one queue: no two workers got the same one
        self.assertEqual([round(0.05 * i, 6) for i in range(8)], sorted(round(delay, 6) for delay in delays))

    def test_objects_that_cannot_cross_into_a_worker_are_refused_up_front(self):
        cases = (({"transport": L.MockTransport([{"action": "done"}])}, "MockTransport"),
                 ({"agent_options": {"transport": L.FakeTransport(lambda url, headers, body: {})}}, "FakeTransport"),
                 ({"agent_options": {"responses": lambda body, index: {"action": "done"}}}, "agent options"),
                 ({"agent_options": {"responses": CountingAnswers()}}, "option responses holds a CountingAnswers"),
                 ({"agent_options": {"extra_body": {"nested": [CountingAnswers()]}}}, "option extra_body holds a CountingAnswers"))
        for filters, reason in cases:
            out = self.tmp / "refused"
            with self.assertRaisesRegex(ValueError, "refusing to run with 2 workers.*" + reason):
                run_episodes([(WET, 0), (WET, 1)], out, WORKERS, agent="mock", **filters)
            self.assertFalse(out.exists(), "refused before anything is written")

    def test_plain_options_and_named_functions_cross_into_a_worker(self):
        options = {"seed": 1, "price_free": None, "prices_path": Path("prices.json"), "cache": L.ResponseCache(self.tmp),
                   "transport": L.DisabledTransport(), "extra_body": {"a": [1, 2.5, ("b", b"c")]}, "responses": _slow_looks}
        self.assertIsNone(R._worker_problem({}, options))

    def test_a_fatal_provider_error_in_a_worker_stops_the_run(self):
        # the real transport crosses into the workers and refuses there: HOUSE_AI_FORBID_NETWORK=1, set up above
        _, info = run_episodes([(WET, 0), (WET, 1)], self.tmp / "run", WORKERS, agent="llm:anthropic:claude-opus-5-5", save_replays=False,
                               transport=L.UrllibTransport(allow_network=True), use_cache=False)
        self.assertEqual("network", info["llm_transport"])
        self.assertEqual("aborted", info["status"])
        self.assertRegex(info["aborted"], "fatal provider error: (NetworkDisabled|LLMConfigError)")

    def test_cache_files_of_two_processes_never_collide(self):
        real, names = os.replace, []

        def spy(source, target):
            names.append(os.path.basename(str(source)))
            return real(source, target)
        with mock.patch.object(L.os, "replace", spy):
            L.ResponseCache(self.tmp / "cache").put("ab" + "0" * 62, {"ok": True})
        self.assertIn(".{}.".format(os.getpid()), names[0])


def _worker_state():
    """A process's engine mode and the order of its plugin tables."""
    from house_sim.kernel import engine, plugins
    tables = {name: list(value) for name, value in vars(plugins).items() if name.isupper() and isinstance(value, dict)}
    hooks = {name: [entry[:2] for entry in value] for name, value in plugins.HOOKS.items()}
    return {"safe": engine.SAFE, "tables": tables, "hooks": hooks, "systems": [entry[:3] for entry in plugins.SYSTEMS]}


def _clock_stands_still():
    return 0.0


def _no_sleep(delay):
    return None


def _share(ledger, limiter, delays, refused, offset, barrier):
    """One side of the shared-objects test: both sides start together, each books $4 and takes 4 places in the queue."""
    barrier.wait()
    for i in range(4):
        ledger.commit(ledger.reserve(0.25), 1.0)
        delays[offset + i] = limiter.wait()
    try:
        ledger.release(ledger.reserve(50.0))
    except A.BudgetExceeded:
        refused[0] = 1.0


if __name__ == "__main__":
    unittest.main()
