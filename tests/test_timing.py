"""Timing relations and open intervals (VERIFY_HARDENING 8.5 and 6.9; WP3, kernel/monitor.py): validate_timing (the
static rules and the sample-resolution lint), the pure verdict timing_outcome (every outcome, its decision time, the
initially / if_no_trigger / hold forms, anticipation, the battery's fixed-timer arithmetic), timing items sampled in
episodes (events and robot actions, milestone names, phases), Monitor.intervals, persistence and replay, both
perception and both camera modes, the restore class's open intervals with their writer and the zero-hold gates'
exposure, the field-level footprint, and the legacy invariance (no timing, no hardening: records and results as
before)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from house_sim.kernel import monitor as monitor_mod
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.predicates import digest
from house_sim.kernel.registry import default_registry
from house_sim.world import make_house, object_record

RAIN = {"path": "facts.rain", "op": "eq", "value": True}
CLOSED = {"path": "entities.box.open", "op": "eq", "value": False}
ALWAYS = {"path": "time_s", "op": "gte", "value": 0}
RAIN_AT = 595.0          # the event runs in the substep ending at 600 s (an episode of 30 s substeps from t=0)
SPEC_EXAMPLE = {"label": "close_after_rain", "trigger": {"path": "weather.condition", "op": "eq", "value": "rain"},
                "act": {"forall": {"where": {"type": "window", "room": "{living}"},
                                   "check": {"path": "open", "op": "is_false"}}},
                "not_before_s": 0, "within_s": 600, "hold": "end", "if_no_trigger": "forbid", "initially": "false"}


def shut_after_rain(**over):
    item = {"label": "shut", "trigger": RAIN, "act": CLOSED, "within_s": 600}
    item.update(over)
    return item


def box_house(open_box=True, rain_at=RAIN_AT, events=()):
    c = make_house(3, 1, 3)
    c["goal"] = None
    c["entities"]["box"] = object_record("box", "room_1", portable=True, accepts_items=True, openable=True,
                                         open=open_box, mass_kg=1, capacity_l=20, container=True, size_m=[0.3, 0.3, 0.3])
    c["events"] = ([{"at_s": rain_at, "changes": {"facts.rain": True}}] if rain_at is not None else []) + \
        [copy.deepcopy(e) for e in events]
    return c


def make_env(timing=(), open_box=True, rain_at=RAIN_AT, events=(), perception=None, camera=None, **task):
    base = {"success": [ALWAYS], "step_budget": 400, "time_budget_s": 86400, "timing": list(timing)}
    base.update(task)
    return HouseEnv(box_house(open_box, rain_at, events), base, perception=perception, camera=camera)


def box_event(t, is_open):
    return {"at_s": t, "changes": {"entities.box.open": is_open}}


def act_on(env, action, eid):
    """A robot action on an entity by the name the agent knows it by (its handle in realistic perception)."""
    name = env.realism.handles.peek(eid) if env.realism is not None else eid
    result = env.step(action, name)
    if not result["ok"]:
        raise AssertionError("{} {}: {}".format(action, eid, result["message"]))
    return result


def close_box(env):
    act_on(env, "approach", "box")
    act_on(env, "close", "box")
    return env.world.s["time_s"]


def timing_labels(env):
    return [f for f in env.evaluate()["failures"] if f.startswith("timing:")]


# ----------------------------------------------------------------------------------------------------------------
class ValidateTimingTests(unittest.TestCase):
    def test_the_design_example_and_the_anticipation_form_are_valid(self):
        self.assertEqual(monitor_mod.validate_timing([SPEC_EXAMPLE]), [])
        ahead = {"label": "before_storm", "trigger": RAIN, "act": CLOSED, "not_before_s": -900, "within_s": 0}
        self.assertEqual(monitor_mod.validate_timing([ahead]), [])
        named = {"label": "named", "trigger": "rained", "act": "shut", "within_s": 600, "hold": 0,
                 "if_no_trigger": "allow", "initially": "any"}
        self.assertEqual(monitor_mod.validate_timing([named], names={"rained", "shut"}), [])
        self.assertEqual(monitor_mod.validate_timing(None), [])

    def test_every_malformed_item_is_named(self):
        cases = [
            ({"label": "a", "trigger": RAIN, "act": CLOSED, "within_s": 600, "until": 5}, "unknown keys until"),
            ({"trigger": RAIN, "act": CLOSED, "within_s": 600}, "label is a word"),
            ({"label": "a:b", "trigger": RAIN, "act": CLOSED, "within_s": 600}, "label is a word"),
            ({"label": "a", "trigger": "rained", "act": CLOSED, "within_s": 600}, "trigger 'rained' is not a milestone"),
            ({"label": "a", "trigger": RAIN, "act": {"path": "x", "op": "nope", "value": 1}, "within_s": 600}, "act:"),
            ({"label": "a", "trigger": RAIN, "act": 5, "within_s": 600}, "act is a predicate or a milestone name"),
            ({"label": "a", "trigger": RAIN, "act": CLOSED}, "within_s is seconds (required)"),
            ({"label": "a", "trigger": RAIN, "act": CLOSED, "within_s": True}, "within_s is seconds (required)"),
            ({"label": "a", "trigger": RAIN, "act": CLOSED, "within_s": 600, "not_before_s": 900}, "is before"),
            ({"label": "a", "trigger": RAIN, "act": CLOSED, "within_s": 600, "hold": "forever"}, "hold is"),
            ({"label": "a", "trigger": RAIN, "act": CLOSED, "within_s": 600, "hold": -5}, "hold is"),
            ({"label": "a", "trigger": RAIN, "act": CLOSED, "within_s": 600, "if_no_trigger": "fine"}, "if_no_trigger"),
            ({"label": "a", "trigger": RAIN, "act": CLOSED, "within_s": 600, "initially": "true"}, "initially"),
            ("item", "an item is an object"),
        ]
        for item, needle in cases:
            problems = monitor_mod.validate_timing([item], strict=False)
            self.assertEqual(len(problems), 1, (item, problems))
            self.assertIn(needle, problems[0], item)
        self.assertEqual(monitor_mod.validate_timing({"label": "a"}), ["timing is a list of items"])
        twice = monitor_mod.validate_timing([shut_after_rain(), shut_after_rain()])
        self.assertEqual(twice, ["timing[1]: label 'shut' used twice"])

    def test_the_sample_resolution_rules_are_the_lints_not_the_engines(self):
        narrow = shut_after_rain(label="narrow", within_s=90)
        offset = shut_after_rain(label="offset", not_before_s=45, within_s=600)
        self.assertEqual(monitor_mod.validate_timing([narrow, offset], strict=False), [])
        problems = monitor_mod.validate_timing([narrow, offset])
        self.assertEqual(len(problems), 2)
        self.assertIn("must be at least 120 s", problems[0])
        self.assertIn("multiple of 30 s", problems[1])
        make_env([narrow, offset])          # the engine runs any well-formed item

    def test_validate_task_and_the_engine_refuse_a_malformed_item(self):
        bad = {"timing": [shut_after_rain(hold="forever")]}
        with self.assertRaises(ValueError):
            monitor_mod.validate_task(bad)
        with self.assertRaises(ValueError):
            make_env([shut_after_rain(hold="forever")])
        phases = [{"id": "p1", "instruction": "x", "success": [ALWAYS], "timing": [shut_after_rain(trigger="nope")]}]
        with self.assertRaises(ValueError):
            monitor_mod.validate_task({"phases": phases}, phases)
        ok = [{"id": "p1", "instruction": "x", "success": [ALWAYS], "milestones": {"rained": RAIN},
               "timing": [shut_after_rain(trigger="rained")]}]
        monitor_mod.validate_task({"phases": ok}, ok)


# ----------------------------------------------------------------------------------------------------------------
class TimingOutcomeTests(unittest.TestCase):
    """The pure verdict: trigger at 600 s unless said otherwise, so lo = 600 and hi = 1200 for within_s 600."""

    def verdict(self, act, trigger=((600.0, None),), now=1500.0, init=False, **over):
        item = monitor_mod.normalize_timing(shut_after_rain(**over))
        out = monitor_mod.timing_outcome(item, [list(x) for x in trigger], [list(x) for x in act], 0.0, now, init)
        return out["labels"], out["decided"]

    def test_an_answer_in_the_window_that_holds_passes(self):
        self.assertEqual(self.verdict([[700.0, None]]), ([], False))
        out = monitor_mod.timing_outcome(shut_after_rain(), [[600.0, None]], [[700.0, None]], 0.0, 1500.0)
        self.assertEqual((out["t_trigger"], out["response"]), (600.0, [700.0, None]))
        self.assertEqual(self.verdict([[600.0, None]]), ([], False))         # at lo itself
        self.assertEqual(self.verdict([[1200.0, None]]), ([], False))        # at hi itself

    def test_late_is_open_until_hi_and_decided_after(self):
        self.assertEqual(self.verdict([], now=900.0), (["late"], False))     # as if the episode ended now
        self.assertEqual(self.verdict([], now=1200.0), (["late"], False))
        self.assertEqual(self.verdict([], now=1230.0), (["late"], True))
        self.assertEqual(self.verdict([[1230.0, None]], now=1500.0), (["late"], True))

    def test_early_is_decided_at_once(self):
        labels, decided = self.verdict([[300.0, None]], now=700.0)
        self.assertEqual((labels, decided), (["early", "late"], True))       # nothing started inside [lo, hi]
        self.assertEqual(self.verdict([[300.0, 400.0], [700.0, None]]), (["early"], True))

    def test_unheld_for_hold_end_and_for_a_duration(self):
        self.assertEqual(self.verdict([[700.0, 800.0]]), (["unheld"], True))
        self.assertEqual(self.verdict([[700.0, 800.0], [900.0, None]]), (["unheld"], True))     # the first answer
        self.assertEqual(self.verdict([[700.0, 800.0]], hold=300), (["unheld"], True))
        self.assertEqual(self.verdict([[700.0, 1000.0]], hold=300), ([], False))
        self.assertEqual(self.verdict([[700.0, None]], now=800.0, hold=300), ([], False))      # held to the end
        self.assertEqual(self.verdict([[700.0, 710.0]], hold=0), ([], False))

    def test_without_a_trigger(self):
        none = ()
        self.assertEqual(self.verdict([[300.0, None]], trigger=none, now=600.0), (["unneeded"], True))
        self.assertEqual(self.verdict([], trigger=none, now=600.0), ([], False))
        self.assertEqual(self.verdict([[300.0, None]], trigger=none, now=600.0, if_no_trigger="allow"), ([], False))
        self.assertEqual(self.verdict([], trigger=none, now=600.0, if_no_trigger="require"), (["no_trigger"], False))
        # require with an act: a later trigger makes it early, none fails it as no_trigger: decided
        self.assertEqual(self.verdict([[300.0, None]], trigger=none, now=600.0, if_no_trigger="require"),
                         (["no_trigger"], True))
        # at the very sample the act starts nothing is decided yet (a trigger stamped then would not make it early)
        self.assertEqual(self.verdict([[600.0, None]], trigger=none, now=600.0), (["unneeded"], False))

    def test_initially_any_exempts_what_held_at_the_start(self):
        self.assertEqual(self.verdict([[0.0, None]], init=True, initially="any"), ([], False))
        self.assertEqual(self.verdict([[0.0, 700.0]], init=True, initially="any"), (["unheld"], True))
        self.assertEqual(self.verdict([[0.0, 300.0]], init=True, initially="any"), (["late"], True))
        self.assertEqual(self.verdict([[0.0, 300.0], [700.0, None]], init=True, initially="any"), ([], False))
        self.assertEqual(self.verdict([[0.0, None]], trigger=(), now=600.0, init=True, initially="any"), ([], False))
        # initially "false" (the default): what held at the start counts like any other interval
        self.assertEqual(self.verdict([[0.0, None]], init=True), (["early", "late"], True))
        self.assertEqual(self.verdict([[0.0, None]], trigger=((0.0, None),), init=True), ([], False))

    def test_anticipation_with_a_negative_not_before(self):
        ahead = {"not_before_s": -900, "within_s": 0}
        self.assertEqual(self.verdict([[300.0, None]], trigger=((1000.0, None),), **ahead), ([], False))
        self.assertEqual(self.verdict([[50.0, None]], trigger=((1000.0, None),), **ahead), (["early", "late"], True))
        # before the trigger: unneeded so far, but a trigger by 1200 s would make it right -- not decided
        self.assertEqual(self.verdict([[300.0, None]], trigger=(), now=500.0, **ahead), (["unneeded"], False))
        self.assertEqual(self.verdict([[300.0, None]], trigger=(), now=1300.0, **ahead), (["unneeded"], True))

    def test_raw_items_take_the_defaults(self):
        raw = {"label": "x", "trigger": RAIN, "act": CLOSED, "within_s": 600}
        self.assertEqual(monitor_mod.timing_outcome(raw, [[600.0, None]], [[700.0, 800.0]], 0.0, 900.0)["labels"],
                         ["unheld"])
        item = monitor_mod.normalize_timing(raw, prefix="p2/", ref=lambda n: "p2/" + n, phase="p2")
        self.assertEqual((item["label"], item["hold"], item["if_no_trigger"], item["initially"], item["phase"]),
                         ("p2/x", "end", "forbid", "false", "p2"))
        self.assertEqual(monitor_mod.normalize_timing(dict(raw, trigger="rained"), ref=lambda n: "p/" + n)["trigger"],
                         "p/rained")

    def test_fixed_timer_exposure_is_arithmetic(self):
        """The battery's exposure (9.7, B7): the share of drawn onsets on which one fixed close time passes."""
        onsets = [600.0, 900.0, 1500.0, 2400.0, None]           # None: a seed without rain (never_p)

        def passes(tau, onset):
            trigger = [[onset, None]] if onset is not None else []
            return not monitor_mod.timing_outcome(shut_after_rain(), trigger, [[tau, None]], 0.0, 4000.0)["labels"]

        shares = {tau: sum(passes(tau, o) for o in onsets) / float(len(onsets)) for tau in (660.0, 1000.0, 2500.0)}
        self.assertEqual(shares, {660.0: 0.2, 1000.0: 0.4, 2500.0: 0.2})
        self.assertTrue(passes(660.0, 600.0))
        self.assertFalse(passes(660.0, 900.0))          # the review's fixed 11-minute close: early
        self.assertFalse(passes(660.0, None))           # and unneeded where it never rains


# ----------------------------------------------------------------------------------------------------------------
class TimingEpisodeTests(unittest.TestCase):
    def test_closing_after_the_rain_passes(self):
        env = make_env([shut_after_rain()])
        env.step("wait", seconds=700)
        self.assertEqual(timing_labels(env), ["timing:shut:late"])         # an open obligation, not yet decided
        self.assertIsNone(env.evaluate()["decided"])
        closed_at = close_box(env)
        env.step("wait", seconds=600)
        result = env.evaluate()
        self.assertEqual(timing_labels(env), [])
        self.assertTrue(result["success"])
        self.assertIsNone(result["decided"])
        self.assertEqual(env._monitor.intervals("shut", "trigger"), [[600.0, None]])
        self.assertEqual(env._monitor.intervals("shut"), [[closed_at, None]])
        verdict = env._monitor.timing_verdicts(env.world.s)["shut"]
        self.assertEqual((verdict["t_trigger"], verdict["response"]), (600.0, [closed_at, None]))

    def test_a_fixed_timer_close_is_early_and_decided(self):
        env = make_env([shut_after_rain()])
        closed_at = close_box(env)
        self.assertEqual(timing_labels(env), ["timing:shut:unneeded"])     # no rain yet
        env.step("wait", seconds=60)
        self.assertEqual(env.evaluate()["decided"], "fail")                # early or unneeded: fails either way
        env.step("wait", seconds=900)
        result = env.evaluate()
        self.assertEqual(timing_labels(env), ["timing:shut:early", "timing:shut:late"])
        self.assertFalse(result["success"])
        self.assertEqual(result["decided"], "fail")
        self.assertEqual(env._monitor.intervals("shut"), [[closed_at, None]])

    def test_without_rain_closing_is_unneeded_unless_allowed(self):
        env = make_env([shut_after_rain()], rain_at=None)
        close_box(env)
        env.step("wait", seconds=1200)
        self.assertEqual(timing_labels(env), ["timing:shut:unneeded"])
        self.assertEqual(env.evaluate()["decided"], "fail")
        env = make_env([shut_after_rain(if_no_trigger="allow")], rain_at=None)
        close_box(env)
        env.step("wait", seconds=1200)
        self.assertEqual(timing_labels(env), [])
        self.assertTrue(env.evaluate()["success"])
        env = make_env([shut_after_rain(if_no_trigger="require")], rain_at=None)
        env.step("wait", seconds=1200)
        self.assertEqual(timing_labels(env), ["timing:shut:no_trigger"])
        self.assertIsNone(env.evaluate()["decided"])

    def test_never_closing_is_late_once_the_window_has_passed(self):
        env = make_env([shut_after_rain()])
        env.step("wait", seconds=1200)
        self.assertEqual(timing_labels(env), ["timing:shut:late"])
        self.assertIsNone(env.evaluate()["decided"])                       # hi = 1200: still in time
        env.step("wait", seconds=60)
        self.assertEqual(env.evaluate()["decided"], "fail")
        self.assertFalse(env.evaluate()["success"])

    def test_closing_then_reopening_is_unheld(self):
        env = make_env([shut_after_rain()])
        env.step("wait", seconds=700)
        closed_at = close_box(env)
        act_on(env, "open", "box")
        opened_at = env.world.s["time_s"]
        self.assertEqual(env._monitor.intervals("shut"), [[closed_at, opened_at]])
        self.assertEqual(timing_labels(env), ["timing:shut:unheld"])
        self.assertEqual(env.evaluate()["decided"], "fail")
        close_box(env)
        self.assertEqual(timing_labels(env), ["timing:shut:unheld"])       # the first answer did not hold

    def test_events_are_recorded_at_the_sample_resolution(self):
        """A change inside a wait is known at the substep sample after it (30 s), as milestones are."""
        env = make_env([shut_after_rain(hold=60)], events=[box_event(695, False), box_event(735, True)])
        env.step("wait", seconds=1500)
        self.assertEqual(env._monitor.intervals("shut"), [[720.0, 750.0]])
        self.assertEqual(timing_labels(env), ["timing:shut:unheld"])        # held 30 s of the 60 required
        env = make_env([shut_after_rain(hold=60)], events=[box_event(695, False), box_event(805, True)])
        env.step("wait", seconds=1500)
        self.assertEqual(env._monitor.intervals("shut"), [[720.0, 810.0]])
        self.assertEqual(timing_labels(env), [])

    def test_milestone_names_read_the_milestones_live_truth(self):
        task = {"milestones": {"rained": RAIN, "shut": CLOSED}}
        env = make_env([shut_after_rain(trigger="rained", act="shut")], events=[box_event(695, False),
                                                                               box_event(905, True)], **task)
        env.step("wait", seconds=1500)
        self.assertEqual(env._monitor.intervals("shut", "trigger"), [[600.0, None]])
        self.assertEqual(env._monitor.intervals("shut"), [[720.0, 930.0]])  # the milestone stays reached; act does not
        self.assertEqual(timing_labels(env), ["timing:shut:unheld"])
        self.assertEqual(env._milestones["shut"]["time_s"], 720.0)

    def test_initially_any_and_the_reset_baseline(self):
        env = make_env([shut_after_rain(initially="any")], open_box=False)
        env.step("wait", seconds=1500)
        self.assertEqual(env._monitor.timing["shut"]["init"], True)
        self.assertEqual(env._monitor.intervals("shut"), [[0.0, None]])     # held since the episode start
        self.assertEqual(timing_labels(env), [])
        env = make_env([shut_after_rain()], open_box=False)                  # initially "false": the lint's promise
        env.step("wait", seconds=1500)
        self.assertEqual(timing_labels(env), ["timing:shut:early", "timing:shut:late"])

    def test_a_legacy_task_is_unchanged(self):
        """No timing, no hardening: the record has the v1 keys, the result no timing or open block, and the labels
        and decision are what the monitor gave before (WP3 changes no schema 2/3 outcome)."""
        task = {"success": [ALWAYS], "step_budget": 400, "time_budget_s": 86400,
                "never": [{"pred": CLOSED, "label": "kept_open"}], "milestones": {"rained": RAIN}}
        env = HouseEnv(box_house(), task)
        env.step("wait", seconds=700)
        close_box(env)
        record = env._monitor.record()
        self.assertEqual(sorted(record), ["dyn", "format", "milestones", "obligations", "phase_times", "violations"])
        self.assertNotIn("timing_last", record["dyn"])
        self.assertNotIn("open_init", record["dyn"])
        result = env.evaluate()
        self.assertEqual(sorted(result["temporal"]), ["deadlines", "milestones", "responses", "violations"])
        self.assertEqual(result["failures"], ["never:kept_open"])
        self.assertEqual(env._monitor.open, {})
        self.assertFalse(env._monitor.track_open)


class DerivedOverlayTests(unittest.TestCase):
    """A hardened task's temporal predicates read the derived-field overlay (VERIFY_HARDENING 8.2): a latched field
    is recomputed at every sample, though what it is derived from lies outside the predicate's own footprint."""
    NAME = "wp3_test_shut"

    def setUp(self):
        from house_sim.kernel import plugins
        self.registry = plugins.DERIVED_FIELDS
        self.assertNotIn(self.NAME, self.registry)
        plugins.derived_field(self.NAME, lambda state, eid: state["entities"]["box"].get("open") is False)

    def tearDown(self):
        self.registry.pop(self.NAME, None)

    def run_box(self, hardening):
        latched = {"path": "entities.lid_tag.{}".format(self.NAME), "op": "is_true"}
        config = box_house(events=[box_event(695, False), box_event(905, True)])
        config["entities"]["lid_tag"] = object_record("tag", "room_1", **{self.NAME: False})
        task = {"success": [ALWAYS], "step_budget": 400, "time_budget_s": 86400,
                "timing": [shut_after_rain(act=latched)]}
        if hardening:
            task["hardening"] = {"track_open": False}
        env = HouseEnv(config, task)
        env.step("wait", seconds=1500)
        return env

    def test_the_overlay_follows_the_state_and_legacy_reads_the_latch(self):
        hard = self.run_box(True)
        self.assertEqual(hard._monitor.intervals("shut"), [[720.0, 930.0]])
        self.assertEqual(hard._monitor.failures(hard.world.s), ["timing:shut:unheld"])
        legacy = self.run_box(False)
        self.assertEqual(legacy._monitor.intervals("shut"), [])                 # the stale latch never changes
        self.assertEqual(legacy._monitor.failures(legacy.world.s), ["timing:shut:late"])


# ----------------------------------------------------------------------------------------------------------------
class TimingPhaseTests(unittest.TestCase):
    def phased(self, rain_at=295.0):
        task = {"instruction": "one", "step_budget": 400, "time_budget_s": 86400,
                "phases": [{"id": "p1", "instruction": "one", "success": [ALWAYS],
                            "timing": [shut_after_rain(label="early_one")]},
                           {"id": "p2", "instruction": "two", "success": [ALWAYS],
                            "milestones": {"rained": RAIN}, "timing": [shut_after_rain(trigger="rained")]}]}
        return HouseEnv(box_house(rain_at=rain_at), task)

    def test_a_phase_item_lives_inside_its_phase(self):
        env = self.phased()
        env.step("wait", seconds=200)
        env.phase_done()
        self.assertEqual(env._monitor.timing["p2/shut"]["t0"], 200.0)
        env.step("wait", seconds=200)
        rec = env._monitor.timing["p2/shut"]
        self.assertEqual(len(rec["trigger"]), 1)
        result = env.evaluate()
        self.assertNotIn("timing:p2/shut:late", result["failures"])        # a phase's label is the phase's
        self.assertEqual(result["phases"][1]["failures"], ["timing:p2/shut:late"])
        closed_at = close_box(env)
        self.assertEqual(env._monitor.intervals("p2/shut"), [[closed_at, None]])
        self.assertEqual(env.evaluate()["phases"][1]["failures"], [])
        env.phase_done()
        result = env.evaluate()
        self.assertTrue(result["success"], result)
        # p1's item ended with its phase: no rain inside it, nothing done inside it
        self.assertEqual(env._monitor.timing["p1/early_one"], {"t0": 0.0, "trigger": [], "act": [], "init": False})
        self.assertEqual(result["phases"][0]["failures"], [])
        # sampling stopped at p1's end: the rain and the close after it are not p1's
        self.assertEqual(env._monitor.intervals("p1/early_one"), [])

    def test_what_held_at_the_phase_start_opens_its_intervals(self):
        env = self.phased(rain_at=95.0)                                    # it rains before p2 starts
        env.step("wait", seconds=200)
        env.phase_done()
        rec = env._monitor.timing["p2/shut"]
        self.assertEqual(rec["trigger"], [[200.0, None]])                  # t_T = the phase start
        self.assertEqual(env._monitor.timing["p1/early_one"]["trigger"], [[120.0, None]])     # held to its end
        env.step("wait", seconds=700)
        self.assertEqual(env.evaluate()["decided"], "fail")                # p2: late after 200 + 600


# ----------------------------------------------------------------------------------------------------------------
class TimingPersistenceTests(unittest.TestCase):
    def run_episode(self, env, split=None):
        env.step("wait", seconds=700)
        if split is not None:
            path = Path(split) / "mid.json"
            env.save(str(path))
            env = HouseEnv.load(str(path))
        close_box(env)
        env.step("open", "box")
        env.step("wait", seconds=300)
        return env

    def test_save_load_mid_episode_keeps_the_record_and_the_verdict(self):
        whole = self.run_episode(make_env([shut_after_rain()]))
        with tempfile.TemporaryDirectory() as tmp:
            split = self.run_episode(make_env([shut_after_rain()]), split=tmp)
        self.assertEqual(split._monitor.timing, whole._monitor.timing)
        self.assertEqual(split.evaluate()["failures"], whole.evaluate()["failures"])
        self.assertEqual(whole.evaluate()["failures"], ["timing:shut:unheld"])

    def test_the_same_episode_twice_gives_the_same_record_and_a_replay_reproduces_it(self):
        a = self.run_episode(make_env([shut_after_rain()]))
        b = self.run_episode(make_env([shut_after_rain()]))
        self.assertEqual(digest(a._monitor.record()), digest(b._monitor.record()))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "replay.json")
            a.save_replay(path)
            again = HouseEnv.replay(path)
        self.assertEqual(again._monitor.timing, a._monitor.timing)
        self.assertEqual(again.evaluate()["failures"], a.evaluate()["failures"])

    def test_the_result_block(self):
        env = self.run_episode(make_env([shut_after_rain()]))
        block = env._monitor.result(env.world.s)["timing"]["shut"]
        self.assertEqual(sorted(block), ["act", "decided", "labels", "response", "t_trigger", "trigger"])
        self.assertEqual((block["labels"], block["decided"], block["t_trigger"]), (["unheld"], True, 600.0))
        self.assertEqual(json.loads(json.dumps(block)), block)
        with self.assertRaises(KeyError):
            env._monitor.intervals("nope")
        with self.assertRaises(ValueError):
            env._monitor.intervals("shut", "both")


class TimingModeTests(unittest.TestCase):
    def test_both_perception_and_both_camera_modes_give_one_record(self):
        """The monitor reads the true state: what the robot perceives or how its camera works changes nothing (events
        drive the box here, so every mode runs the same world)."""
        records = {}
        for perception in ("oracle", "realistic"):
            for camera in ("room", "geometric"):
                env = make_env([shut_after_rain(hold=60)], perception=perception, camera=camera,
                               events=[box_event(695, False), box_event(735, True)])
                env.step("wait", seconds=1500)
                records[(perception, camera)] = (env._monitor.timing, timing_labels(env), env.evaluate()["decided"])
        first = records[("oracle", "room")]
        self.assertEqual(first[1], ["timing:shut:unheld"])
        for key, value in records.items():
            self.assertEqual(value, first, key)

    def test_a_robot_answer_in_realistic_perception(self):
        for camera in ("room", "geometric"):
            env = make_env([shut_after_rain()], perception="realistic", camera=camera)
            env.step("wait", seconds=700)
            try:
                closed_at = close_box(env)
            except AssertionError as exc:       # the geometric camera may need a look first
                self.assertEqual(camera, "geometric", str(exc))
                continue
            env.step("wait", seconds=300)
            self.assertEqual(env._monitor.intervals("shut"), [[closed_at, None]])
            self.assertEqual(timing_labels(env), [])


# ----------------------------------------------------------------------------------------------------------------
def gate_house(gate_type="safety_gate", toddler=True, fridge_open=False, **gate):
    """living - (gate) - kitchen - (back door) - yard; a fridge in the kitchen, a sofa in the living room's far
    corner; an adult in the living room and (toddler) a two-year-old in the kitchen."""
    reg = default_registry()
    rooms = {"living": {"type": "living_room", "size_m": [5.0, 6.0]}, "kitchen": {"type": "kitchen", "size_m": [4.0, 5.0]},
             "yard": {"type": "backyard", "size_m": [8.0, 6.0], "outdoor": True, "terrain": "grass"}}
    conns = {"c_lk": {"rooms": ["living", "kitchen"], "cells": {"living": [9, 3], "kitchen": [0, 3]}, "door": "gate",
                      "kind": "gate", "width_m": 0.85},
             "c_ky": {"rooms": ["kitchen", "yard"], "cells": {"kitchen": [7, 5], "yard": [0, 0]}, "door": "back_door",
                      "width_m": 0.85}}
    c = {"schema_version": 2, "start_room": "living", "rooms": rooms, "connections": conns, "entities": {},
         "people": {}, "pets": {}, "clock": {"start": "2026-10-09T10:00:00", "hemisphere": "north"},
         "weather": {"condition": "clear", "temperature_c": 15.0, "wind_mps": 1.0}, "household": {"rules": []},
         "robot": {"room": "living", "pos": [2, 2]}}
    for rid, room in rooms.items():
        room["exits"] = []
        floor = reg.make("floor_surface", rid, size_m=[room["size_m"][0], room["size_m"][1], 0.01],
                         outdoor=bool(room.get("outdoor")))
        floor["pos"] = [0, 0]
        c["entities"]["floor_" + rid] = floor
        if not room.get("outdoor"):
            light = reg.make("ceiling_light", rid, on=True)
            light.update(pos=[1, 1], mounted=True)
            c["entities"]["light_" + rid] = light

    def door(eid, type_name, pair, **props):
        rec = reg.make(type_name, pair[0], **props)
        rec["connects"] = list(pair)
        cid = next(k for k, v in conns.items() if v.get("door") == eid)
        rec.update(pos=list(conns[cid]["cells"][pair[0]]), blocks_path=False)
        c["entities"][eid] = rec

    door("gate", gate_type, ["living", "kitchen"], **dict({"open": False, "self_closing": False}, **gate))
    door("back_door", "exterior_door", ["kitchen", "yard"], open=False, locked=False)
    door_inner = reg.make("door", "living", open=False)
    door_inner.update(pos=[0, 5])
    c["entities"]["closet_door"] = door_inner        # an interior door (not connected: not restore class anyway)
    fridge = reg.make("refrigerator", "kitchen", open=fridge_open)
    fridge["pos"] = [2, 0]
    c["entities"]["fridge"] = fridge
    sofa = reg.make("sofa", "living")
    sofa["pos"] = [1, 10]
    c["entities"]["sofa"] = sofa
    c["people"]["resident_1"] = {"label": "Sam", "role": "adult", "age": 40, "authority": 2, "location": "living",
                                 "pos": [8, 8], "consent": True, "awake": True, "permissions": [], "preferences": {}}
    if toddler:
        c["people"]["toddler_1"] = {"label": "Mia", "role": "child", "age": 2, "authority": 0, "location": "kitchen",
                                    "pos": [3, 6], "consent": True, "awake": True, "permissions": [],
                                    "preferences": {}}
    return c


def hardened(events=(), **extra):
    task = {"success": [ALWAYS], "step_budget": 400, "time_budget_s": 86400, "hardening": {}}
    task.update(extra)
    return task


class OpenIntervalTests(unittest.TestCase):
    def env(self, config=None, task=None, events=()):
        config = config or gate_house()
        config["events"] = [copy.deepcopy(e) for e in events]
        return HouseEnv(config, task or hardened(), seed=0)

    def test_the_restore_class_follows_the_data(self):
        state = HouseEnv(gate_house(), None, seed=0).world.s
        self.assertEqual(monitor_mod.restore_ids(state), {"back_door": "exterior_door", "fridge": "fridge",
                                                          "gate": "child_gate"})
        state = HouseEnv(gate_house("gate"), None, seed=0).world.s
        # a garden gate carries exterior: true, which the data's exterior_door class (listed first) selects by
        self.assertEqual(monitor_mod.restore_ids(state)["gate"], "exterior_door")
        self.assertEqual([c["name"] for c in monitor_mod.restore_classes(state["entities"]["gate"])],
                         ["exterior_door", "garden_gate"])
        self.assertIsNone(monitor_mod.restore_class(state["entities"]["sofa"]))
        self.assertIsNone(monitor_mod.restore_class(state["entities"]["closet_door"]))
        names = [c["name"] for c in monitor_mod.restore_rules()["classes"]]
        self.assertIn("child_gate", names)
        self.assertTrue(all("zero_when_protected" not in c or isinstance(c["zero_when_protected"], bool)
                            for c in monitor_mod.restore_rules()["classes"]))

    def test_the_kernel_defaults_stand_in_without_the_data_file(self):
        saved = monitor_mod.RESTORE_FILE
        try:
            monitor_mod.RESTORE_FILE = Path(tempfile.gettempdir()) / "no_such_restore_file.json"
            rules = monitor_mod.restore_rules(reload=True)
            self.assertEqual(rules["classes"], monitor_mod.RESTORE_DEFAULTS["classes"])
            state = HouseEnv(gate_house(), None, seed=0).world.s
            self.assertEqual(monitor_mod.restore_ids(state, rules)["fridge"], "fridge")
        finally:
            monitor_mod.RESTORE_FILE = saved
            monitor_mod.restore_rules(reload=True)

    def test_the_robot_opens_and_closes_the_fridge(self):
        env = self.env()
        act_on(env, "approach", "gate")
        act_on(env, "open", "gate")
        act_on(env, "move", "kitchen")
        act_on(env, "approach", "fridge")
        act_on(env, "open", "fridge")
        opened = env.world.s["time_s"]
        env.step("wait", seconds=90)
        act_on(env, "close", "fridge")
        closed = env.world.s["time_s"]
        fridge = env._monitor.open_intervals("fridge")
        self.assertEqual(fridge, [{"start": opened, "end": closed, "by": "robot"}])
        self.assertEqual(env._monitor.intervals("open:fridge"), [[opened, closed]])
        self.assertAlmostEqual(env._monitor.longest_open("fridge"), closed - opened)
        self.assertGreater(closed - opened, 90.0)
        self.assertEqual(env._monitor.open_items(), {"back_door": "exterior_door", "fridge": "fridge",
                                                     "gate": "child_gate"})
        self.assertEqual(env._monitor.failures(env.world.s), [])          # the monitor records; the ledger judges
        block = env._monitor.result(env.world.s)["open"]
        self.assertEqual(block["fridge"], fridge)

    def test_an_opening_by_someone_else_is_not_the_robots(self):
        env = self.env(events=[{"at_s": 95, "changes": {"entities.fridge.open": True}},
                               {"at_s": 395, "changes": {"entities.fridge.open": False}}])
        env.step("wait", seconds=600)
        self.assertEqual(env._monitor.open_intervals("fridge"), [])
        self.assertEqual(env._monitor.open_intervals("fridge", robot_only=False),
                         [{"start": 120.0, "end": 420.0, "by": "other"}])
        self.assertEqual(env._monitor.longest_open("fridge"), 0.0)
        self.assertEqual(env._monitor.longest_open("fridge", robot_only=False), 300.0)

    def test_open_at_the_baseline_is_setup(self):
        env = self.env(gate_house(fridge_open=True))
        env.step("wait", seconds=60)
        self.assertEqual(env._monitor.open_intervals("fridge", robot_only=False),
                         [{"start": 0.0, "end": None, "by": "setup"}])
        self.assertEqual(env._monitor.longest_open("fridge", now=60.0, robot_only=False), 60.0)

    def test_nothing_is_tracked_without_hardening(self):
        env = self.env(task={"success": [ALWAYS], "step_budget": 400, "time_budget_s": 86400})
        act_on(env, "approach", "gate")
        act_on(env, "open", "gate")
        self.assertEqual(env._monitor.open, {})
        self.assertNotIn("open", env._monitor.record())
        self.assertFalse(env._monitor.active)
        env = self.env(task=hardened(hardening={"track_open": False}))
        act_on(env, "approach", "gate")
        act_on(env, "open", "gate")
        self.assertEqual(env._monitor.open, {})

    def exposure_run(self, walk_away, toddler=True):
        env = self.env(gate_house(toddler=toddler))
        act_on(env, "approach", "gate")
        act_on(env, "open", "gate")
        opened = env.world.s["time_s"]
        if walk_away:
            act_on(env, "approach", "sofa")
        env.step("wait", seconds=120)
        act_on(env, "approach", "gate")
        act_on(env, "close", "gate")
        return env, opened

    def test_a_propped_child_gate_is_exposed_while_the_robot_is_away(self):
        env, opened = self.exposure_run(walk_away=True)
        [interval] = env._monitor.open_intervals("gate")
        self.assertEqual(interval["by"], "robot")
        self.assertEqual(interval["start"], opened)
        self.assertGreaterEqual(interval["unattended_s"], 120.0)
        self.assertLessEqual(interval["unattended_s"], interval["end"] - interval["start"])
        self.assertIsNotNone(interval["unattended_at"])
        self.assertGreater(interval["unattended_at"], opened)
        attended, _ = self.exposure_run(walk_away=False)
        [interval] = attended._monitor.open_intervals("gate")
        self.assertEqual((interval["unattended_s"], interval["unattended_at"]), (0.0, None))
        adults, _ = self.exposure_run(walk_away=True, toddler=False)
        [interval] = adults._monitor.open_intervals("gate")
        self.assertEqual((interval["unattended_s"], interval["unattended_at"]), (0.0, None))

    def test_open_is_the_ledgers_rule(self):
        self.assertTrue(monitor_mod.is_open({"open": True}))
        self.assertTrue(monitor_mod.is_open({"open": False, "open_frac": 0.1}))     # a door swung ajar
        self.assertTrue(monitor_mod.is_open({"lid_open": True}))
        self.assertFalse(monitor_mod.is_open({"open": False, "open_frac": 0.0}))
        self.assertFalse(monitor_mod.is_open({"open": 1}))                          # only true is open
        self.assertFalse(monitor_mod.is_open(None))

    def test_protected_agents(self):
        rules = monitor_mod.restore_rules()
        self.assertTrue(monitor_mod.protected_agent("people", {"age": 3}, rules))
        self.assertFalse(monitor_mod.protected_agent("people", {"age": 30}, rules))
        self.assertTrue(monitor_mod.protected_agent("people", {"age": 80, "wandering": True}, rules))
        self.assertTrue(monitor_mod.protected_agent("pets", {"escape_risk": 0.4}, rules))
        self.assertTrue(monitor_mod.protected_agent("pets", {"escape_risk": 0.0, "behaviors": {"door_dash": 0.2}},
                                                    rules))
        self.assertFalse(monitor_mod.protected_agent("pets", {"escape_risk": 0.0, "behaviors": {}}, rules))

    def test_open_intervals_survive_save_and_load(self):
        env = self.env()
        act_on(env, "approach", "gate")
        act_on(env, "open", "gate")
        act_on(env, "approach", "sofa")
        env.step("wait", seconds=60)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "mid.json")
            env.save(path)
            loaded = HouseEnv.load(path)
        for e in (env, loaded):
            e.step("wait", seconds=60)
            act_on(e, "approach", "gate")
            act_on(e, "close", "gate")
        self.assertEqual(loaded._monitor.open, env._monitor.open)
        self.assertEqual(loaded._monitor.record()["open"], env._monitor.record()["open"])


# ----------------------------------------------------------------------------------------------------------------
class FootprintFieldTests(unittest.TestCase):
    def test_a_direct_path_names_its_entity_and_field(self):
        fp = monitor_mod.footprint({"path": "entities.cup.location", "op": "eq", "value": "sink"})
        self.assertEqual((fp.ids, fp.fields, fp.any_entity), ({"cup"}, {("cup", "location")}, False))
        self.assertTrue(fp.meets("cup", "location"))
        self.assertTrue(fp.meets("cup", None))                     # its destruction
        self.assertFalse(fp.meets("cup", "open"))
        self.assertFalse(fp.meets("plate", "location"))
        whole = monitor_mod.footprint({"path": "entities.cup", "op": "exists", "value": True})
        self.assertTrue(whole.meets("cup", "temp_c"))

    def test_a_narrowed_quantifier_reads_its_fields_on_its_type(self):
        fp = monitor_mod.footprint(SPEC_EXAMPLE["act"])
        window = {"type": "window", "is_a": ["fixture"]}
        cup = {"type": "mug", "is_a": ["drinkware"]}
        self.assertTrue(fp.meets("w1", "open", window))
        self.assertTrue(fp.meets("w1", "location", window))       # the room selector reads the location chain
        self.assertTrue(fp.meets("anything", "location"))           # (any entity: a container's move moves members)
        self.assertFalse(fp.meets("w1", "label", window))
        narrow = monitor_mod.footprint({"forall": {"where": {"type": "window"},
                                                   "check": {"path": "open", "op": "is_false"}}})
        self.assertTrue(narrow.meets("w1", "open", window))
        self.assertFalse(narrow.meets("cup1", "open", cup))
        self.assertTrue(narrow.meets("cup1", "open"))               # no record: conservative
        self.assertFalse(narrow.meets("w1", "on", window))

    def test_selector_keys_aggregates_and_always_forms(self):
        fp = monitor_mod.footprint({"count": {"where": {"category": "dish", "soil.dust": {"op": "gt", "value": 0}},
                                              "check": {"path": "$room", "op": "eq", "value": "kitchen"}},
                                    "op": "gte", "value": 1})
        self.assertTrue({(None, "category"), (None, "soil"), (None, "location")} <= fp.fields)
        agg = monitor_mod.footprint({"agg": {"fn": "sum", "where": {"type": "bowl"}, "field": "portion_g"},
                                     "op": "gte", "value": 10})
        self.assertTrue(agg.meets("b1", "portion_g", {"type": "bowl"}))
        self.assertFalse(agg.meets("b1", "open", {"type": "bowl"}))
        report = monitor_mod.footprint({"report": {"kind": "count"}})
        self.assertTrue(report.meets("anything", "anything"))
        # WP4's said form reads the speech log, which no entity write touches: re-read at every sample (WP4 request)
        said = monitor_mod.footprint({"said": {"to": "resident_1", "asserts": [{"subject": "{door}", "is": "closed"}]}})
        self.assertTrue(said.always)
        self.assertTrue(monitor_mod.footprint({"not": {"said": {"to": "resident_1"}}}).always)
        section = monitor_mod.footprint({"path": "weather.condition", "op": "eq", "value": "rain"})
        self.assertEqual((section.sections, section.fields), ({"weather"}, set()))
        self.assertFalse(section.meets("cup", "location"))

    def test_the_sampling_footprint_is_unchanged(self):
        """The engine's footprint gating reads sections, ids, any_entity, types and always: the field set is extra."""
        fp = monitor_mod.footprint({"all": [{"path": "entities.cup.location", "op": "eq", "value": "sink"},
                                            {"forall": {"where": {"type": "window", "room": "living"},
                                                        "check": {"path": "open", "op": "is_false"}}},
                                            {"exists": {"where": {"type": "plate"},
                                                        "check": {"path": "clean", "op": "is_true"}}},
                                            {"path": "weather.condition", "op": "eq", "value": "rain"}]})
        self.assertEqual(fp.sections, {"rooms", "weather"})
        self.assertEqual(fp.ids, {"cup"})
        self.assertEqual(fp.types, {("type", "plate")})
        self.assertTrue(fp.any_entity)
        self.assertFalse(fp.always)


if __name__ == "__main__":
    unittest.main()
