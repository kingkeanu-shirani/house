"""The timeline (kernel v3.1, TIER1_SWEEP 6 and 21 "TIME-EVAL" item 1): notice perceivability, interruptible waits,
auto standing orders, the fire-time perceivability fix, the weather rule and the data-gated notice bus; the stage-C
merge's end-to-end monitoring spec (an auto order reporting to the requester, scored by a response)."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from house_sim.generator import generate_house
from house_sim.kernel import body, plugins, timeline
from house_sim.kernel.phys import lawbook
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.phys import observe
from house_sim.kernel.plugins import HOOKS, hook
from house_sim.kernel.predicates import digest
from house_sim.world import make_house, object_record

SECTIONS = ("notices", "order_log", "requests", "phase")


@hook("event:test_timeline.notice")
def _post_notice(w, args, env):
    """Test effect: post the notice in args (a domain would post its doorbell, alarm or chime like this)."""
    w.notice(**args)
    return True


@hook("event:test_timeline.say")
def _say(w, args, env):
    """Test effect: a person speaks (as people's person_says writes it)."""
    state = w.s
    person = state["people"][args["pid"]]
    messages = w.sec("messages")
    messages.append({"step": state.get("steps", 0), "time_s": state["time_s"], "speaker": args["pid"], "kind": "speech",
                     "text": args["text"], "to": args.get("to"), "room": person.get("location"),
                     "heard_by_robot": bool(args.get("heard", True))})
    return True


def fixture(bus=False, door_open=False):
    """room_1 - room_2 - room_3 in a row; a door between room_1 and room_2; the robot and resident_1 in room_1."""
    c = make_house(3, 1, 3)
    c["goal"] = None
    c["entities"]["box"] = object_record("box", "room_1", portable=True, accepts_items=True, openable=True, open=False,
                                         mass_kg=1, capacity_l=20, container=True, size_m=[0.3, 0.3, 0.3])
    c["entities"]["door_12"] = object_record("door", "room_1", category="door", connects=["room_1", "room_2"],
                                             openable=True, open=door_open, portable=False, size_m=[0.9, 0.05, 2.0])
    c["entities"]["window_2"] = object_record("window", "room_2", category="window", portable=False,
                                              size_m=[1.0, 0.05, 1.2])
    if bus:
        c["notices"] = []
    return c


def env_of(bus=False, door_open=False, events=(), perception=None, task=None):
    c = fixture(bus, door_open)
    c["events"] = [dict(e) for e in events]
    return HouseEnv(c, task, perception=perception)


def notice(kind, **kw):
    out = {"kind": kind, "room": None, "db": None, "source": None, "to_robot": False, "remote": False, "visual": False,
           "text": None, "data": None}
    out.update(kw)
    return out


class AudibilityTests(unittest.TestCase):
    def setUp(self):
        self.env = env_of(bus=True)
        self.w = self.env.world

    def perceive(self, **kw):
        return timeline.robot_perceives(self.w, notice(**kw))

    def test_same_room_needs_40_db(self):
        self.assertEqual(self.perceive(kind="knock", room="room_1", db=40)["room"], "room_1")
        self.assertIsNone(self.perceive(kind="knock", room="room_1", db=39.9))

    def test_one_open_hop_costs_6_db_and_a_closed_door_20(self):
        door = self.w.mut("door_12")
        door["open"] = True
        heard = self.perceive(kind="knock", room="room_2", db=46)
        self.assertEqual(heard["near"], "room_2")
        self.assertNotIn("room", heard)
        self.assertIsNone(self.perceive(kind="knock", room="room_2", db=45.9))
        door["open"] = False
        self.assertIsNotNone(self.perceive(kind="knock", room="room_2", db=60))
        self.assertIsNone(self.perceive(kind="knock", room="room_2", db=59.9))
        # two rooms away through the closed door and a doorway: 26 dB, and "far"
        self.assertTrue(self.perceive(kind="alarm", room="room_3", db=66)["far"])
        self.assertIsNone(self.perceive(kind="alarm", room="room_3", db=65.9))

    def test_the_kind_default_level_applies(self):
        self.assertIsNotNone(self.perceive(kind="doorbell", room="room_2"))     # 75 dB - 20
        self.assertIsNone(self.perceive(kind="clatter", room="room_2"))         # 45 dB - 20

    def test_speech_words_need_the_same_room_or_55_db_to_the_robot(self):
        self.w.mut("door_12")["open"] = True
        here = self.perceive(kind="speech", room="room_1", db=45, text="the kettle")
        self.assertEqual(here["text"], "the kettle")
        loud = self.perceive(kind="addressed", room="room_2", db=61, text="come here", to_robot=True)
        self.assertEqual(loud["text"], "come here")
        quiet = self.perceive(kind="addressed", room="room_2", db=60, text="come here", to_robot=True)
        self.assertNotIn("text", quiet)
        overheard = self.perceive(kind="speech", room="room_2", db=75, text="not for you")
        self.assertNotIn("text", overheard)

    def test_hearing_privacy_powered_down_remote_and_visual(self):
        robot = self.w.sec("robot")
        robot["privacy_mode"] = True        # hearing "addressed": speech only when addressed; other sounds stay (1.3)
        self.assertEqual(body.can_hear(self.w.s), "addressed")
        self.assertIsNotNone(self.perceive(kind="knock", room="room_1", db=70))
        self.assertIsNotNone(self.perceive(kind="glass_breaking", room="room_1", db=60))
        self.assertIsNotNone(self.perceive(kind="alarm", room="room_1", db=70))
        self.assertIsNotNone(self.perceive(kind="addressed", room="room_1", db=60, to_robot=True))
        self.assertIsNone(self.perceive(kind="speech", room="room_1", db=60, text="not for the robot"))
        robot["privacy_mode"] = False
        self.assertIsNotNone(self.perceive(kind="device", remote=True))
        robot["network"] = False
        self.assertIsNone(self.perceive(kind="device", remote=True))
        robot["network"] = True
        seen = self.perceive(kind="smoke", room="room_1", visual=True)
        self.assertEqual(seen["room"], "room_1")
        self.assertIsNone(self.perceive(kind="smoke", room="room_2", visual=True))
        robot["power_state"] = "off"
        self.assertIsNone(self.perceive(kind="alarm", room="room_1", db=90))

    def test_the_source_is_named_only_when_perceived(self):
        record = self.perceive(kind="knock", room="room_1", db=60, source="box")
        self.assertEqual(record["source"], "box")
        record = self.perceive(kind="knock", room="room_2", db=90, source="window_2")
        self.assertNotIn("source", record)

    def test_a_robot_hears_hook_replaces_the_propagation(self):
        def deaf_to_room_3(state, posted):
            return False if posted.get("room") == "room_3" else None
        HOOKS["robot_hears"].append((100, "test.deaf", deaf_to_room_3))
        try:
            self.assertIsNone(self.perceive(kind="alarm", room="room_3", db=120))
            self.assertIsNotNone(self.perceive(kind="alarm", room="room_2", db=120))
        finally:
            HOOKS["robot_hears"].remove((100, "test.deaf", deaf_to_room_3))

    def test_kinds_register_and_unknown_kinds_are_refused(self):
        timeline.register_notice_kind("test_buzz", "a test buzzer", implies=("any_sound",), db=70)
        timeline.register_notice_kind("test_buzz", "a test buzzer", implies=("any_sound",), db=70)     # same: no-op
        with self.assertRaises(ValueError):
            timeline.register_notice_kind("test_buzz", "different", implies=())
        with self.assertRaises(ValueError):
            timeline.register_notice_kind("urgent", "a group")
        with self.assertRaises(ValueError):
            self.w.notice("no_such_kind", room="room_1")
        self.assertEqual(timeline.expand_kinds(["urgent"]),
                         {"alarm", "call_for_help", "addressed", "person_down", "smoke"})
        self.assertTrue(timeline.matches({"kind": "doorbell"}, {"any_sound"}))
        self.assertTrue(timeline.matches({"kind": "addressed"}, {"speech"}))
        self.assertFalse(timeline.matches({"kind": "visual"}, {"any_sound"}))


class NoticeBusTests(unittest.TestCase):
    def test_notices_are_stored_only_where_the_bus_is_on(self):
        legacy = env_of(bus=False)
        legacy.world.begin()
        self.assertIsNone(legacy.world.notice("doorbell", room="room_1"))
        legacy.world.commit()
        self.assertNotIn("notices", legacy.world.s)
        env = env_of(bus=True)
        env.world.begin()
        stored = env.world.notice("doorbell", room="room_2")
        env.world.commit()
        self.assertEqual(stored, {"t": 0.0, "step": 0, "kind": "doorbell", "near": "room_2"})
        self.assertEqual(env.world.s["notices"], [stored])

    def test_the_store_is_capped_at_200(self):
        env = env_of(bus=True)
        w = env.world
        w.begin()
        for i in range(230):
            w.notice("knock", room="room_1", data={"i": i})
        w.commit()
        self.assertEqual(len(w.s["notices"]), 200)
        self.assertEqual(w.s["notices"][-1]["data"], {"i": 229})

    def test_kernel_notices_speech_people_and_the_body(self):
        env = env_of(bus=True, door_open=True, events=[
            {"at_s": 25, "effect": "test_timeline.say", "args": {"pid": "resident_1", "text": "robot, the door",
                                                                  "to": "robot"}},
            {"at_s": 55, "changes": {"people.resident_1.location": "room_2"}},
            {"at_s": 85, "changes": {"people.resident_1.location": "room_1"}}])
        env.step("wait", seconds=120)
        kinds = [n["kind"] for n in env.world.s["notices"]]
        self.assertEqual(kinds, ["addressed", "person_leaves", "person_enters"])
        self.assertEqual(env.world.s["notices"][0]["text"], "robot, the door")
        self.assertEqual(env.world.s["notices"][2]["source"], "resident_1")
        w = env.world
        w.begin()
        w.emit("battery_low", gauge=20.0)
        w.emit("fault_detected", fault="camera_dirty", how="self_check")
        w.commit()
        self.assertEqual([n["kind"] for n in w.s["notices"][-2:]], ["battery", "fault"])
        self.assertEqual(w.s["notices"][-2]["data"], {"warning": "low"})

    def test_physics_consequences_post_their_sound(self):
        env = env_of(bus=True)
        w = env.world
        w.begin()
        observe.emit_consequence(w, "phys.collapse", "box", room="room_1")
        w.commit()
        self.assertEqual(w.s["notices"][-1]["kind"], "crash")
        self.assertEqual(w.s["notices"][-1]["data"], {"loudness": "loud"})


class WaitTests(unittest.TestCase):
    def test_until_ends_at_the_first_boundary_where_it_holds_and_is_perceivable(self):
        env = env_of(events=[{"at_s": 95, "changes": {"entities.box.open": True}}])
        result = env.step("wait", seconds=3600, until={"path": "entities.box.open", "op": "eq", "value": True})
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "Waited 120 s (until condition met)")
        self.assertEqual(env.world.s["time_s"], 120.0)
        self.assertEqual(env.world.s["facts"]["action_times"]["robot"]["wait"], 120.0)

    def test_until_already_true_ends_at_once(self):
        env = env_of()
        result = env.step("wait", seconds=600, until={"path": "entities.box.open", "op": "eq", "value": False})
        self.assertEqual(result["message"], "Waited 0 s (until condition met)")
        self.assertEqual(env.world.s["time_s"], 0.0)

    def test_a_condition_the_robot_stops_perceiving_does_not_end_the_wait(self):
        env = env_of(door_open=True, events=[{"at_s": 45, "changes": {"people.resident_1.location": "room_2"}}])
        rule = {"path": "people.resident_1.location", "op": "eq", "value": "room_2"}
        result = env.step("wait", seconds=300, until=rule)
        # true from 60 s on, but not in the robot's room: the wait runs its full time (it noticed her leave)
        self.assertEqual(result["message"], "Waited 300 s | noticed: person_leaves in room_1 from resident_1")
        self.assertEqual(env.world.s["time_s"], 300.0)

    def test_a_condition_not_perceivable_when_issued_is_refused(self):
        env = env_of()
        for rule in ({"path": "entities.window_2.open", "op": "eq", "value": True},     # another room
                     {"path": "entities.box._secret", "op": "eq", "value": 1},         # not an observable field
                     {"path": "notices.0.kind", "op": "eq", "value": "doorbell"},      # use interrupt_on
                     {"exists": {"where": {"type": "box"}}}):                          # quantifiers
            result = env.step("wait", seconds=60, until=rule)
            self.assertFalse(result["ok"], rule)
            self.assertEqual(result["message"], "Wait only on what the robot can observe")
        self.assertEqual(env.world.s["stats"]["invalid"], 4)

    def test_interrupts_doorbell_addressed_person_enters_and_urgent(self):
        cases = [
            (["doorbell"], {"at_s": 70, "effect": "test_timeline.notice", "args": {"kind": "doorbell", "room": "room_2"}},
             "Waited 90 s (interrupted: doorbell near room_2)"),
            (["addressed"], {"at_s": 40, "effect": "test_timeline.say", "args": {"pid": "resident_1", "text": "hey",
                                                                                 "to": "robot"}},
             'Waited 60 s (interrupted: addressed in room_1 from resident_1: "hey")'),
            (["person_enters"], {"at_s": 130, "changes": {"people.resident_1.location": "room_1"}},
             "Waited 150 s (interrupted: person_enters in room_1 from resident_1) | noticed: person_leaves in room_1 "
             "from resident_1"),
            (["urgent"], {"at_s": 10, "effect": "test_timeline.notice", "args": {"kind": "alarm", "room": "room_3"}},
             "Waited 30 s (interrupted: alarm far away)")]
        for kinds, event, message in cases:
            events = [event]
            if kinds == ["person_enters"]:
                events = [{"at_s": 5, "changes": {"people.resident_1.location": "room_2"}}, event]
            env = env_of(door_open=True, events=events)
            result = env.step("wait", seconds=3600, interrupt_on=kinds)
            self.assertEqual(result["message"], message, kinds)

    def test_other_notices_are_listed_but_do_not_interrupt(self):
        env = env_of(door_open=True, events=[
            {"at_s": 40, "effect": "test_timeline.notice", "args": {"kind": "knock", "room": "room_1"}}])
        result = env.step("wait", seconds=120, interrupt_on=["doorbell"])
        self.assertEqual(result["message"], "Waited 120 s | noticed: knock in room_1")

    def test_unknown_kinds_are_refused(self):
        env = env_of()
        result = env.step("wait", seconds=60, interrupt_on=["doorbel"])
        self.assertFalse(result["ok"])
        self.assertIn("Unknown notice kind: doorbel", result["message"])

    def test_the_default_wait_and_a_listening_wait_leave_identical_states(self):
        quiet = env_of()
        listening = env_of()
        self.assertEqual(quiet.step("wait", seconds=600)["message"], "Waited 600 s")
        self.assertEqual(listening.step("wait", seconds=600, interrupt_on=["doorbell"])["message"], "Waited 600 s")
        self.assertEqual(digest(quiet.world.s), digest(listening.world.s))
        for section in SECTIONS:
            self.assertNotIn(section, quiet.world.s)

    def test_a_wait_in_an_episode_with_the_bus_lists_what_it_heard(self):
        env = env_of(bus=True, events=[
            {"at_s": 40, "effect": "test_timeline.notice", "args": {"kind": "knock", "room": "room_1"}}])
        self.assertEqual(env.step("wait", seconds=60)["message"], "Waited 60 s | noticed: knock in room_1")


class AutoOrderTests(unittest.TestCase):
    def watch(self, env, condition, commands, **kw):
        result = env.step("watch", condition=condition, commands=commands, mode="auto", **kw)
        self.assertTrue(result["ok"], result["message"])
        return result

    def test_a_refused_command_rolls_back_alone_and_other_orders_go_on(self):
        env = env_of()
        when = {"path": "time_s", "op": "gte", "value": 30}
        self.watch(env, when, [{"action": "approach", "target": "box"},
                               {"action": "pick_up", "target": "nothing_here"},
                               {"action": "open", "target": "box"}])
        self.watch(env, when, [{"action": "announce", "text": "Thirty seconds"}])
        result = env.step("wait", seconds=300)
        state = env.world.s
        log = [(e["order"], e["cmd"], e["action"], e["ok"]) for e in state["order_log"]]
        self.assertEqual(log, [(0, 0, "approach", True), (0, 1, "pick_up", False), (1, 0, "announce", True)])
        self.assertFalse(state["entities"]["box"]["open"])               # skipped after the refusal
        self.assertEqual(state["stats"]["order_refusals"], 1)
        self.assertEqual(state["stats"]["invalid"], 0)
        self.assertEqual(state["facts"]["last_speech"]["text"], "Thirty seconds")
        self.assertIn("standing orders ran: order 0: approach ok, pick_up refused; order 1: announce ok",
                      result["message"])
        self.assertAlmostEqual(state["time_s"], 302.0, delta=1e-6)       # two watches of 1 s, then the 300 s wait

    def test_command_time_counts_against_the_wait_and_the_world_keeps_running(self):
        env = env_of()
        self.watch(env, {"path": "time_s", "op": "gte", "value": 30}, [{"action": "approach", "target": "box"}])
        start = env.world.s["time_s"]
        env.step("wait", seconds=300)
        self.assertAlmostEqual(env.world.s["time_s"] - start, 300.0, delta=1e-6)

    def test_orders_that_fire_during_a_busy_action_run_at_the_next_idle_moment(self):
        env = env_of()
        self.watch(env, {"path": "time_s", "op": "gte", "value": 1}, [{"action": "announce", "text": "held"}])
        env.step("approach", "box")
        state = env.world.s
        self.assertTrue(state["orders"][0]["due"])
        self.assertNotIn("order_log", state)
        start = state["time_s"]
        env.step("wait", seconds=30)
        self.assertEqual(env.world.s["order_log"][0]["t"], start)
        self.assertFalse(env.world.s["orders"][0]["due"])

    def test_max_runs_cooldown_and_expiry(self):
        toggles = [{"at_s": t, "changes": {"entities.box.open": i % 2 == 0}} for i, t in enumerate(range(15, 1200, 60))]
        env = env_of(events=toggles)
        opened = {"path": "entities.box.open", "op": "eq", "value": True}
        self.watch(env, opened, [{"action": "announce", "text": "open"}], persistent=True, max_runs=2, cooldown_s=0)
        self.watch(env, opened, [{"action": "announce", "text": "cooled"}], persistent=True, cooldown_s=600)
        self.watch(env, {"path": "time_s", "op": "gte", "value": 120}, [{"action": "announce", "text": "late"}],
                   expires_s=60)
        env.step("wait", seconds=1200)
        state = env.world.s
        runs = {}
        for entry in state["order_log"]:
            runs[entry["order"]] = runs.get(entry["order"], 0) + 1
        self.assertEqual(runs.get(0), 2)
        self.assertEqual(runs.get(1), 2)                  # fired at ~31 s and again once 600 s had passed
        self.assertNotIn(2, runs)
        self.assertTrue(state["orders"][2]["expired"])

    def test_the_command_budget_pauses_auto_orders_until_the_next_step(self):
        old = timeline.AUTO_BUDGET
        timeline.AUTO_BUDGET = 3
        try:
            env = env_of()
            self.watch(env, {"path": "time_s", "op": "gte", "value": 30},
                       [{"action": "announce", "text": str(i)} for i in range(5)])
            result = env.step("wait", seconds=120)
            log = env.world.s["order_log"]
            self.assertEqual([e["ok"] for e in log], [True, True, True, False])
            self.assertEqual(log[-1]["message"], "order_budget")
            self.assertIn("auto orders paused (order budget)", result["message"])
        finally:
            timeline.AUTO_BUDGET = old

    def test_forbidden_commands_and_guards(self):
        env = env_of()
        for action in timeline.AUTO_FORBIDDEN:
            result = env.step("watch", condition={"path": "time_s", "op": "gte", "value": 30},
                              commands=[{"action": action}], mode="auto")
            self.assertFalse(result["ok"], action)
        self.watch(env, {"path": "time_s", "op": "gte", "value": 30}, [{"action": "announce", "text": "x"}],
                   guard={"path": "entities.box.open", "op": "eq", "value": True})
        env.step("wait", seconds=60)
        self.assertEqual(env.world.s["order_log"][0]["message"], "guard_false")
        self.assertNotIn("last_speech", env.world.s["facts"])

    def test_queue_mode_is_unchanged(self):
        env = env_of()
        self.assertEqual(env.step("watch", condition={"path": "time_s", "op": "gte", "value": 30},
                                  commands=[{"action": "announce", "text": "q"}])["message"], "Watching (order 0)")
        order = env.world.s["orders"][0]
        self.assertEqual(set(order), {"condition", "commands", "fired", "persistent", "armed", "created_s"})
        env.step("wait", seconds=60)
        self.assertEqual(env.world.s["pending"], [{"action": "announce", "text": "q"}])
        self.assertNotIn("order_log", env.world.s)

    def test_a_power_down_stops_auto_execution(self):
        env = env_of()
        self.watch(env, {"path": "time_s", "op": "gte", "value": 30}, [{"action": "announce", "text": "x"}])
        w = env.world
        w.begin()
        w.sec("robot")["power_state"] = "off"
        w.commit()
        env.step("wait", seconds=60)
        self.assertNotIn("order_log", w.s)


class PerceivabilityTests(unittest.TestCase):
    def test_fire_time_check_applies_realism(self):
        mood = {"path": "people.resident_1.mood", "op": "eq", "value": "neutral"}
        for perception, fires in (("oracle", True), ("realistic", False)):
            env = env_of(perception=perception)
            w = env.world
            w.begin()
            w.sec("orders").append({"condition": copy.deepcopy(mood), "commands": [{"action": "announce", "text": "m"}],
                                    "fired": False, "persistent": False, "armed": True, "created_s": 0.0})
            w.commit()
            env.step("wait", seconds=60)
            self.assertEqual(bool(w.s["pending"]), fires, perception)

    def test_weather_needs_an_outdoor_room_a_window_or_the_network(self):
        env = env_of()
        w = env.world
        rule = {"path": "weather.condition", "op": "eq", "value": "rain"}
        w.begin()
        robot = w.sec("robot")
        self.assertTrue(timeline.perceivable(w, rule))         # the weather app (network on)
        robot["network"] = False
        self.assertFalse(timeline.perceivable(w, rule))        # room_1 has no window
        robot["room"] = "room_2"
        self.assertTrue(timeline.perceivable(w, rule))         # a window, cameras on
        robot["privacy_mode"] = True
        self.assertFalse(timeline.perceivable(w, rule))        # cameras off
        self.assertTrue(timeline.perceivable(w, {"path": "time_s", "op": "gte", "value": 5}))
        robot["power_state"] = "off"
        self.assertFalse(timeline.perceivable(w, {"path": "time_s", "op": "gte", "value": 5}))
        w.rollback()

    def test_the_old_rule_stays_exported(self):
        from house_sim.systems import core
        self.assertIs(core.watch_condition_visible, timeline.watch_condition_visible)


def scripted_run():
    """A wait with interrupts and notices, auto orders with a refused command, all in one episode with the bus on;
    returns the env (used in this process and in a child process)."""
    env = env_of(bus=True, door_open=True, events=[
        {"at_s": 70, "effect": "test_timeline.notice", "args": {"kind": "doorbell", "room": "room_2"}},
        {"at_s": 200, "effect": "test_timeline.say", "args": {"pid": "resident_1", "text": "hi", "to": "robot"}}])
    env.step("watch", condition={"path": "time_s", "op": "gte", "value": 100},
             commands=[{"action": "approach", "target": "box"}, {"action": "pick_up", "target": "nothing_here"},
                       {"action": "open", "target": "box"}], mode="auto")
    env.step("wait", seconds=600, interrupt_on=["doorbell"])
    env.step("wait", seconds=600, interrupt_on=["urgent"])
    env.step("wait", seconds=300, until={"path": "entities.box.open", "op": "eq", "value": True})
    return env


class DeterminismTests(unittest.TestCase):
    def test_save_load_and_replay_of_waits_and_auto_orders(self):
        env = scripted_run()
        messages = [r["message"] for r in env._history]
        self.assertIn("(interrupted: doorbell near room_2)", messages[1])
        self.assertIn("(interrupted: addressed", messages[2])
        self.assertEqual([e["ok"] for e in env.world.s["order_log"]], [True, False])
        with tempfile.TemporaryDirectory() as folder:
            path, replay = Path(folder) / "s.json", Path(folder) / "r.json"
            env.save(path)
            loaded = HouseEnv.load(path)
            self.assertEqual(digest(loaded.world.s), digest(env.world.s))
            for e in (env, loaded):
                e.step("wait", seconds=120, interrupt_on=["any_sound"])
            self.assertEqual(digest(loaded.world.s), digest(env.world.s))
            env.save_replay(replay)
            again = HouseEnv.replay(replay)
            self.assertEqual(digest(again.world.s), digest(env.world.s))

    def test_two_processes_with_different_hash_seeds_agree(self):
        code = ("import sys; sys.path.insert(0, {root!r}); sys.path.insert(0, {tests!r}); "
                "from house_sim.kernel.predicates import digest; import test_timeline as t; "
                "env = t.scripted_run(); print(digest(env.world.s))")
        root = str(Path(__file__).resolve().parents[1])
        cmd = [sys.executable, "-c", code.format(root=root, tests=str(Path(__file__).resolve().parent))]
        digests = []
        for seed in ("1", "2"):
            out = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, PYTHONHASHSEED=seed),
                                 cwd=root, timeout=600)
            self.assertEqual(out.returncode, 0, out.stderr[-2000:])
            digests.append(out.stdout.strip().splitlines()[-1])
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(digests[0], digest(scripted_run().world.s))


class IdleProbeTests(unittest.TestCase):
    def test_probes_change_nothing(self):
        """The kernel's idle probes are exact: waits with probes on and off end in the same states (the wind and rain
        laws forced on so physics.environment has work on windy substeps)."""
        def run(probes, seed):
            old = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = probes
            try:
                env = HouseEnv(generate_house(seed), seed=seed, trace=False, observe_on_step=False)
                for seconds in (3 * 3600, 1800, 7 * 3600):
                    env.step("wait", seconds=seconds)
                return digest(env.world.s)
            finally:
                plugins.IDLE_PROBES[0] = old
        with lawbook.forced(wind=True, rain=True):
            for seed in (0, 3):
                self.assertEqual(run(True, seed), run(False, seed), seed)

    def test_probes_are_registered_and_skip_idle_systems(self):
        for name in ("physics.environment", "physics.processes", "camera.watch"):
            self.assertIn(name, plugins.SYSTEM_IDLE)
        env = env_of()
        self.assertTrue(plugins.SYSTEM_IDLE["camera.watch"](env.world))         # room mode
        self.assertTrue(plugins.SYSTEM_IDLE["physics.processes"](env.world))    # nothing queued
        with self.assertRaises(TypeError):
            plugins.system("test_timeline.bad_probe", idle="yes")

    def test_a_probe_may_take_the_substeps_tick(self):
        """Adoption-round request (navigation_robot 5): a system gated on its own substep schedule (the first substep
        of a step, every 20 substeps) needs the tick to have an exact probe; fn(w, tick) gets it, fn(w) as before."""
        seen, ran = [], []

        def probe(w, tick):
            seen.append(tick.index)
            return tick.index % 2 == 1                     # idle on odd substeps

        def plain(w):
            return True

        def fn(w, dt, tick):
            ran.append(tick.index)
        try:
            plugins.system("test_timeline.ticked", order=999, max_dt=30.0, idle=probe)(fn)
            plugins.system("test_timeline.plain", order=999, max_dt=30.0, idle=plain)(lambda w, dt, tick: None)
            self.assertIn("test_timeline.ticked", plugins.IDLE_TAKES_TICK)
            self.assertNotIn("test_timeline.plain", plugins.IDLE_TAKES_TICK)
            env = env_of()
            start = env._tick_index
            env.step("wait", seconds=120)                   # four 30 s substeps
            self.assertEqual(seen, [start, start + 1, start + 2, start + 3])
            self.assertEqual(ran, [start, start + 2] if start % 2 == 0 else [start + 1, start + 3])
        finally:
            for name in ("test_timeline.ticked", "test_timeline.plain"):
                plugins.SYSTEMS[:] = [s for s in plugins.SYSTEMS if s[1] != name]
                plugins.SYSTEM_IDLE.pop(name, None)
                plugins.IDLE_TAKES_TICK.discard(name)


MONITORING = {
    "catalog_id": "T-timeline-merge-01", "schema": 3, "title": "Tell me when Sam gets up",
    "house": {"residents": "minimal", "pets": "none"},
    "setup": [
        {"op": "room", "var": "living", "type": "living_room", "pick": "first"},
        {"op": "lights", "room": "{living}", "on": True},
        {"op": "person", "id": "resident_1", "room": "{living}", "props": {"has_phone": True}},
        {"op": "person", "id": "sam", "room": "{living}", "props": {"label": "Sam", "posture": "sitting"}},
        {"op": "robot", "room": "{living}"},
        {"op": "event", "at_s": 900, "changes": {"people.sam.posture": "standing"}}],
    "request": {"from": "resident_1", "channel": "in_person"},
    "instruction": "Let me know when Sam gets up.",
    "phrasings": ["Let me know when Sam gets up.", "Text me once Sam is on his feet.", "Tell me when Sam stands up, ok?"],
    "milestones": {"sam_up": {"path": "people.sam.posture", "op": "eq", "value": "standing"}},
    "responses": [{"trigger": "sam_up", "within_s": 120, "label": "told", "response": {
        "report": {"kind": "status", "subject": "sam", "state": "awake", "to": "resident_1"}}}],
    "never": [{"pred": {"report": {"kind": "status", "subject": "sam"}}, "until": "sam_up", "label": "early"}],
    "success": [{"report": {"kind": "status", "subject": "sam", "state": "awake", "to": "resident_1",
                            "after": "sam_up"}}],
    "budget": {"steps": 20, "time_s": 2400}, "risk_level": 1,
    "reference": [{"do": "watch", "condition": {"path": "people.sam.posture", "op": "eq", "value": "standing"},
                   "mode": "auto", "commands": [{"action": "report", "kind": "status", "subject": "sam",
                                                 "state": "awake", "to": "resident_1", "channel": "text"}]},
                  {"do": "wait", "seconds": 1800}],
    "wrong": [{"name": "reports at once", "plan": [
                  {"do": "report", "kind": "status", "subject": "sam", "state": "awake", "to": "resident_1",
                   "channel": "text"}, {"do": "wait", "seconds": 1800}]},
              {"name": "waits and never tells", "plan": [{"do": "wait", "seconds": 1800}]},
              {"name": "a queue order nobody runs", "plan": [
                  {"do": "watch", "condition": {"path": "people.sam.posture", "op": "eq", "value": "standing"},
                   "commands": [{"action": "report", "kind": "status", "subject": "sam", "state": "awake",
                                 "to": "resident_1", "channel": "text"}]},
                  {"do": "wait", "seconds": 1800}]}]}


class EndToEndTests(unittest.TestCase):
    def test_an_auto_order_tells_the_requester_in_time(self):
        """Stage-C merge (TIER1_SWEEP 21, integration): a schema-3 monitoring spec using both packages -- an auto
        standing order (TIME-EVAL) whose command is a typed report by text to the requester (PEOPLE-IO), scored by a
        response obligation, a never window and a report predicate after a milestone, with the realistic report
        evidence check -- passes verification in both perception modes, and each wrong plan fails for its own
        reason. (Five seeds: the merge gate's handoff.)"""
        from house_sim.tasks import verify
        for mode in (None, "realistic"):
            record = verify.verify_spec(copy.deepcopy(MONITORING), seeds=(0,), perception=mode)
            trial = record["trials"][0]
            self.assertTrue(record["passed"], (mode, {k: trial.get(k) for k in ("error", "reference_failures",
                                                                                 "reference_error",
                                                                                 "perceivability_gaps")}))
            why = {w["name"]: w["why"] for w in trial["wrong"]}
            self.assertIn("never:early", why["reports at once"])
            self.assertIn("response:told", why["waits and never tells"])
            self.assertIn("response:told", why["a queue order nobody runs"])


def dark_fixture(**kw):
    c = fixture(**kw)
    for record in c["entities"].values():
        if record.get("category") == "light":
            record["on"] = False
    c["clock"] = {"start": "2026-10-09T23:30:00"}           # night: no daylight through the window either
    return c


def smoke_alarm(room="room_3"):
    return object_record("smoke alarm", room, category="smoke_detector", portable=False, alarm_state="idle",
                         size_m=[0.12, 0.12, 0.04])


def entity_rule(env, eid, field, value, op="eq"):
    """A condition on an entity as the agent writes it: by its handle in realistic perception."""
    name = env.realism.handles.handle(eid) if env.realism is not None else eid
    return {"path": "entities.{}.{}".format(name, field), "op": op, "value": value}


LEAVES = {"path": "people.resident_1.location", "op": "ne", "value": "room_1"}
COMES_IN = {"path": "people.resident_1.location", "op": "eq", "value": "room_1"}


class StageCFixTests(unittest.TestCase):
    """The stage-C review's time findings (catalog/analysis/tier1/stageC_review_time.json), one test or more each."""

    def test_until_and_watch_need_the_thing_seen_now(self):
        """Per referenced id: a dark shape shows no field, so a wait or a standing order on its state is refused in
        the dark (both perception modes) and accepted in the light; a held order does not fire on what the robot
        cannot see."""
        for perception in ("oracle", "realistic"):
            lit = HouseEnv(fixture(), None, perception=perception)
            door = entity_rule(lit, "door_12", "open", True)
            self.assertTrue(lit.step("wait", seconds=30, until=door)["ok"], perception)
            dark = HouseEnv(dark_fixture(), None, perception=perception)
            self.assertIn("door_12", dark.world.s["entities"])
            door = entity_rule(dark, "door_12", "open", True)
            if dark.realism is not None:
                self.assertEqual(dark.observe()["visible_entities"][door["path"].split(".")[1]]["label"],
                                 "dark shape")
            result = dark.step("wait", seconds=30, until=door)
            self.assertEqual(result["message"], "Wait only on what the robot can observe", perception)
            self.assertFalse(dark.step("watch", condition=door, commands=[{"action": "announce", "text": "x"}],
                                       mode="auto")["ok"])

    def test_in_the_dark_a_wait_on_someone_tells_nothing_about_who_is_there(self):
        """Realistic perception, the robot's room dark with a shape in it: a wait on the person there and one on a
        person elsewhere are refused with the same words (no who-is-here oracle by id)."""
        c = dark_fixture()
        c["people"]["resident_2"] = dict(c["people"]["resident_1"], label="Other", location="room_3")
        env = HouseEnv(c, None, perception="realistic")
        messages = set()
        for pid in ("resident_1", "resident_2"):
            result = env.step("wait", seconds=30, until={"path": "people.{}.posture".format(pid), "op": "eq",
                                                         "value": "sitting"})
            self.assertFalse(result["ok"])
            messages.add(result["message"])
        self.assertEqual(len(messages), 1)

    def test_a_person_behind_the_geometric_camera_is_not_observed(self):
        """Geometric camera: facing away from the person, a wait or standing order on their posture is refused and
        their coming in is a notice without a source; facing them, the wait is accepted."""
        rule = {"path": "people.resident_1.posture", "op": "eq", "value": "sitting"}
        for perception in ("oracle", "realistic"):
            seen, unseen = None, None
            for deg in range(0, 360, 45):
                env = HouseEnv(fixture(bus=True), None, perception=perception, camera="geometric")
                env.step("face", deg=deg)
                if env.observe().get("people"):
                    seen = seen if seen is not None else deg
                else:
                    unseen = unseen if unseen is not None else deg
            self.assertIsNotNone(seen)
            self.assertIsNotNone(unseen)
            env = HouseEnv(fixture(bus=True), None, perception=perception, camera="geometric")
            env.step("face", deg=seen)
            self.assertTrue(env.step("wait", seconds=30, until=rule)["ok"])
            env = HouseEnv(fixture(bus=True), None, perception=perception, camera="geometric")
            env.step("face", deg=unseen)
            self.assertFalse(env.step("wait", seconds=30, until=rule)["ok"], perception)
            self.assertFalse(env.step("watch", condition=rule, commands=[{"action": "announce", "text": "x"}],
                                      mode="auto")["ok"])
            c = fixture(bus=True, door_open=True)
            c["people"]["resident_1"]["location"] = "room_2"
            env = HouseEnv(c, None, perception=perception, camera="geometric")
            env.step("face", deg=unseen)
            pos = HouseEnv(fixture(), None).world.s["people"]["resident_1"].get("pos")
            env.world.s["events"] = [{"at_s": 30, "changes": {"people.resident_1.location": "room_1",
                                                              "people.resident_1.pos": pos}}]
            result = env.step("wait", seconds=300, interrupt_on=["person_enters"])
            self.assertIn("(interrupted: person_enters in room_1)", result["message"])
            self.assertNotIn("from resident_1", result["message"])
            self.assertTrue(all("source" not in n for n in env.world.s["notices"]))

    def test_until_and_watch_on_someone_coming_in_or_leaving(self):
        """A location compared with the robot's own room is watched as the room: "until they leave" ends when they
        go, "when they come in" is accepted while they are elsewhere and fires on their arrival; in a dark room
        (realistic) neither is accepted."""
        for perception in ("oracle", "realistic"):
            env = env_of(door_open=True, perception=perception,
                         events=[{"at_s": 60, "changes": {"people.resident_1.location": "room_2"}}])
            result = env.step("wait", seconds=600, until=LEAVES)
            self.assertTrue(result["message"].startswith("Waited 60 s (until condition met)"), result["message"])
            env = env_of(door_open=True, perception=perception,
                         events=[{"at_s": 60, "changes": {"people.resident_1.location": "room_2"}}])
            result = env.step("wait", seconds=600, until={"not": COMES_IN})
            self.assertTrue(result["message"].startswith("Waited 60 s (until condition met)"))
            env = env_of(door_open=True, perception=perception,
                         events=[{"at_s": 5, "changes": {"people.resident_1.location": "room_2"}},
                                 {"at_s": 200, "changes": {"people.resident_1.location": "room_1"}}])
            env.step("wait", seconds=30)
            result = env.step("watch", condition=COMES_IN, commands=[{"action": "announce", "text": "hi"}],
                              mode="auto")
            self.assertTrue(result["ok"], result["message"])
            env.step("wait", seconds=600)
            self.assertEqual(env.world.s["order_log"][0]["action"], "announce")
            self.assertAlmostEqual(env.world.s["order_log"][0]["t"], 210.0, delta=30.0)
        env = HouseEnv(dark_fixture(door_open=True), None, perception="realistic")
        self.assertFalse(env.step("wait", seconds=60, until=LEAVES)["ok"])

    def test_a_condition_seen_hidden_does_not_end_a_wait_at_once(self):
        """The person is in the room but hiding: "until they come in" holds in truth yet the robot sees an empty
        room, so the wait does not end at once (it would tell where they hide)."""
        c = fixture()
        c["people"]["resident_1"]["concealed"] = True
        env = HouseEnv(c, None)
        result = env.step("wait", seconds=60, until=COMES_IN)
        self.assertEqual(result["message"], "Waited 60 s")

    def test_smoke_alarms_post_alarm_notices_and_can_be_waited_on(self):
        """The kernel bridges alarms until the domains post their own: an alarm that starts sounding is an "alarm"
        notice (also in privacy mode), so wait(interrupt_on=["urgent"]) ends at the boundary after; alarm_state
        compared with "alarm" is heard, so until / watch accept it in realistic perception."""
        for privacy in (False, True):
            c = fixture(bus=True)
            c["entities"]["smoke_1"] = smoke_alarm()
            c["robot"]["privacy_mode"] = privacy
            c["events"] = [{"at_s": 40, "changes": {"entities.smoke_1.alarm_state": "alarm"}}]
            env = HouseEnv(c, None)
            result = env.step("wait", seconds=3600, interrupt_on=["urgent"])
            self.assertEqual(result["message"], "Waited 60 s (interrupted: alarm far away)", privacy)
            self.assertEqual(env.world.s["notices"][-1]["kind"], "alarm")
        for perception in ("oracle", "realistic"):
            c = fixture(door_open=True)
            c["entities"]["smoke_1"] = smoke_alarm("room_1")      # seen once (its handle), then the robot leaves
            c["events"] = [{"at_s": 100, "changes": {"entities.smoke_1.alarm_state": "alarm"}}]
            env = HouseEnv(c, None, perception=perception)
            sounding = entity_rule(env, "smoke_1", "alarm_state", "alarm")
            hushed = entity_rule(env, "smoke_1", "alarm_state", "hushed")
            self.assertTrue(env.step("move", "room_2")["ok"])
            result = env.step("wait", seconds=3600, until=sounding)
            self.assertIn("(until condition met)", result["message"], perception)
            self.assertLessEqual(env.world.s["time_s"], 160.0)
            self.assertFalse(env.step("wait", seconds=60, until=hushed)["ok"])     # silence is not heard

    def test_an_unattended_wait_hears_the_safety_specs_smoke_alarm(self):
        """The review's case: A-d9e7a4d5d112d2-01 (burnt toast) with the notice bus on -- wait(interrupt_on=["urgent"])
        ends at the first alarm boundary (the detectors start sounding at the safety system's first tick, 30 s)."""
        import json as json_
        from house_sim.kernel.plugins import load_domains
        from house_sim.tasks.runner import build
        load_domains()
        path = Path(__file__).resolve().parents[1] / "tasks" / "authored" / "safety__examples.json"
        specs = json_.loads(path.read_text(encoding="utf-8"))
        spec = next((s for s in specs.get("specs", specs) if s.get("catalog_id") == "A-d9e7a4d5d112d2-01"), None)
        if spec is None:
            self.skipTest("the safety spec is not authored here")
        try:
            config, task, _ = build(copy.deepcopy(spec), 0)
        except Exception as exc:  # noqa: BLE001 -- a domain left out (HOUSE_SIM_DOMAINS): not this test's
            self.skipTest("build: {}".format(exc))
        for perception in ("oracle", "realistic"):
            c = copy.deepcopy(config)
            c["notices"] = []
            env = HouseEnv(c, copy.deepcopy(task), 0, perception=perception)
            result = env.step("wait", seconds=900, interrupt_on=["urgent"])
            self.assertIn("(interrupted: alarm", result["message"], perception)
            self.assertLessEqual(env.world.s["time_s"], 31.0)

    def test_an_any_condition_ends_on_the_branch_the_robot_sees(self):
        """Three-valued: an "any" with one observed true branch holds (the other on a thing in another room); a "not"
        of what the robot cannot see does not."""
        env = env_of(events=[{"at_s": 45, "changes": {"entities.box.open": True}}])
        hidden = {"path": "entities.window_2.open", "op": "eq", "value": False}      # room_2: not seen
        seen = {"path": "entities.box.open", "op": "eq", "value": True}
        w = env.world
        self.assertIsNone(timeline.observed(w, hidden))
        self.assertIsNone(timeline.observed(w, {"not": hidden}))
        self.assertFalse(timeline.observed(w, {"all": [seen, hidden]}))
        w.begin()
        w.mut("box")["open"] = True
        self.assertTrue(timeline.observed(w, {"any": [seen, hidden]}))
        self.assertIsNone(timeline.observed(w, {"all": [seen, hidden]}))
        w.rollback()

    def test_the_weather_bookkeeping_is_not_observable(self):
        from house_sim.kernel.perception import WEATHER_HIDDEN
        for perception in ("oracle", "realistic"):
            c = fixture()
            c["weather"] = {"condition": "clear", "temperature_c": 12.0,
                            "process": {"model": "normals_v1", "seed_key": "wx-2", "region": "us"},
                            "forecast_state": {"x": 1}, "override_until_s": 600.0, "_draw": 0.5}
            env = HouseEnv(c, None, perception=perception)
            for key in WEATHER_HIDDEN + ("_draw",):
                for path in ("weather." + key, "weather.{}.model".format(key)):
                    result = env.step("wait", seconds=30, until={"path": path, "op": "eq", "value": 1})
                    self.assertFalse(result["ok"], (perception, path))
            app = {"path": "weather.temperature_c", "op": "lt", "value": 20}
            self.assertTrue(env.step("wait", seconds=30, until=app)["ok"])          # the weather app (network)
            w = env.world
            w.begin()
            w.sec("robot")["network"] = False
            w.sec("robot")["room"] = "room_2"                                     # it has a window
            w.commit()
            window_word = {"path": "weather.condition", "op": "eq", "value": "rain"}
            self.assertTrue(timeline.perceivable(w, window_word, env.realism, env))
            self.assertEqual(timeline.perceivable(w, app, env.realism, env), perception == "oracle")

    def test_an_order_that_fires_in_a_listening_wait_runs_before_it_ends(self):
        env = env_of()
        env.step("watch", condition={"path": "time_s", "op": "gte", "value": 100},
                 commands=[{"action": "announce", "text": "hundred"}], mode="auto")
        result = env.step("wait", seconds=600, interrupt_on=["order"])
        self.assertIn("(interrupted: order in room_1)", result["message"])
        self.assertIn("standing orders ran: order 0: announce ok", result["message"])
        self.assertEqual(env.world.s["facts"]["last_speech"]["text"], "hundred")
        self.assertFalse(env.world.s["orders"][0].get("due"))

    def test_the_command_budget_is_per_hour_of_a_long_wait(self):
        old = timeline.AUTO_BUDGET
        timeline.AUTO_BUDGET = 3
        try:
            env = env_of()
            env.step("watch", condition={"path": "time_s", "op": "gte", "value": 30},
                     commands=[{"action": "announce", "text": str(i)} for i in range(5)], mode="auto")
            env.step("watch", condition={"path": "time_s", "op": "gte", "value": 4000},
                     commands=[{"action": "announce", "text": "late"}], mode="auto")
            result = env.step("wait", seconds=3 * 3600)
            log = env.world.s["order_log"]
            self.assertEqual([e["message"] for e in log if not e["ok"]], ["order_budget"])     # logged once
            self.assertIn("late", [e["message"] for e in log if e["ok"]])          # the next hour ran it
            self.assertIn("auto orders paused (order budget)", result["message"])
            self.assertFalse(any(o.get("due") for o in env.world.s["orders"]))
        finally:
            timeline.AUTO_BUDGET = old

    def test_the_standing_orders_note_is_capped(self):
        env = env_of()
        env.step("watch", condition={"path": "time_s", "op": "gte", "value": 30},
                 commands=[{"action": "announce", "text": str(i)} for i in range(15)], mode="auto")
        result = env.step("wait", seconds=120)
        self.assertIn("(+5 more)", result["message"])
        self.assertEqual(result["message"].count("announce ok"), timeline.NOTE_LIMIT)

    def test_a_speech_notice_names_its_speaker_only_when_seen(self):
        env = env_of(bus=True, door_open=True, events=[
            {"at_s": 5, "changes": {"people.resident_1.location": "room_2"}},
            {"at_s": 40, "effect": "test_timeline.say", "args": {"pid": "resident_1", "text": "where are my keys"}}])
        env.step("wait", seconds=60)
        speech = [n for n in env.world.s["notices"] if n["kind"] == "speech"]
        self.assertTrue(speech)
        self.assertNotIn("resident_1", json.dumps(speech))


class CameraAwareTests(unittest.TestCase):
    """C-MERGE's request, checked by the adoption round's camera follow-up (K-CAM): timeline.perceivable and the
    person_enters / person_leaves derivation are light-, imaging- and geometric-camera-aware (the stage-C fix T1 did
    the work per referenced id; these pin the cases its tests left out: the cameras switched off, a thing the
    geometric camera must frame, someone leaving behind the geometric camera)."""

    PERSON = {"path": "people.resident_1.posture", "op": "eq", "value": "sitting"}

    def test_with_the_cameras_off_a_wait_watches_nothing_it_would_have_to_see(self):
        for perception in ("oracle", "realistic"):
            for robot in ({"privacy_mode": True}, {"camera_on": False}):
                c = fixture()
                c["robot"].update(robot)
                env = HouseEnv(c, None, perception=perception)
                door = entity_rule(env, "door_12", "open", True)
                result = env.step("wait", seconds=30, until=door)
                self.assertEqual(result["message"], "Wait only on what the robot can observe", (perception, robot))
                person = env.step("wait", seconds=30, until=self.PERSON)
                # the oracle observation lists the room's people with only the depth camera (TIER1_INTEGRATION 1.3:
                # the darkness rule), realistic perception sees shapes and no one
                self.assertEqual(person["ok"], perception == "oracle", (perception, robot, person["message"]))

    def test_the_geometric_camera_must_frame_the_thing_a_wait_watches(self):
        for perception in ("oracle", "realistic"):
            accepted = {}
            for deg in range(0, 360, 45):
                env = HouseEnv(fixture(), None, perception=perception, camera="geometric")
                env.step("face", deg=deg)
                door = entity_rule(env, "door_12", "open", True)
                accepted[deg] = env.step("wait", seconds=30, until=door)["ok"]
                room = HouseEnv(fixture(), None, perception=perception)
                self.assertTrue(room.step("wait", seconds=30, until=entity_rule(room, "door_12", "open", True))["ok"])
            self.assertTrue(any(accepted.values()), perception)              # facing the door
            self.assertFalse(all(accepted.values()), perception)             # not with the door behind the camera

    def test_someone_leaving_is_named_only_when_the_robot_saw_them_go(self):
        def leaves(perception, camera, facing=None, track=False, dark=False):
            c = (dark_fixture if dark else fixture)(bus=True, door_open=True)
            c["events"] = [{"at_s": 60, "changes": {"people.resident_1.location": "room_2"}}]
            env = HouseEnv(c, None, perception=perception, camera=camera)
            if facing is not None:
                env.step("face", deg=facing)
            if track:
                self.assertTrue(env.step("look", target="resident_1", track=True)["ok"])
            env.step("wait", seconds=300)
            return [n.get("source") for n in env.world.s["notices"] if n["kind"] == "person_leaves"]
        for perception in ("oracle", "realistic"):
            self.assertEqual(leaves(perception, "room"), ["resident_1"], perception)
            self.assertEqual(leaves(perception, "room", dark=True), [None], perception)     # heard, not seen
            sources = {deg: leaves(perception, "geometric", facing=deg) for deg in (0, 90, 180, 270)}
            self.assertIn(["resident_1"], sources.values(), perception)                     # facing her
            self.assertIn([None], sources.values(), perception)                             # behind the camera
            unseen = next(deg for deg, found in sources.items() if found == [None])
            self.assertEqual(leaves(perception, "geometric", facing=unseen, track=True), ["resident_1"], perception)


if __name__ == "__main__":
    unittest.main()
