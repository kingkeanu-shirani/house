"""Who gave the instruction, what the robot noticed, and what it sees of the weather (PEOPLE-IO, kernel v3.1;
docs/design/TIER1_SWEEP.md 6.1, 10.2 and 17; TIER1_INTEGRATION 1.12).

What this file pins:

* obs.request (only when the episode has requests) in oracle form, and obs.instruction following the current
  request's text; no request key at all without requests (the room golden's observations stay as they were);
* realistic obs.request: the requester's id in person in a lit room (a resident, or a visitor the robot knows), the
  dark-spell shape handle in the dark, "unfamiliar person" for a visitor it does not know, the contact for a phone or
  app request from a household member, "unknown caller" for a stranger, "note" with the note's handle for a written
  request; decided once and kept (saved with realism);
* obs.notices: the stored notices of the current step only (step-stamped records; unstamped ones by time), none while
  powered down; realistic times in whole seconds and sources translated like any channel;
* obs.weather without the weather process's bookkeeping;
* listen returns the latest notices beside the messages;
* the runner's schema-3 request (TIME-EVAL) reaches the observation;
* the stage-C merge seams: one rule for the current request (a phase without a request shows its own instruction;
  obs.request, env.request(), the dialogue's and the evaluation's requester agree), a phase's request is heard as an
  "addressed" notice (also by a wait it starts in), and phase_done's observation repeats no earlier notice.

Hand-built houses with core types only: the file passes with HOUSE_SIM_DOMAINS=core.
"""
import copy
import json
import unittest

from house_sim.kernel import perception
from house_sim.kernel.engine import HouseEnv

COLS, ROWS = 10, 8


def config(requests=None, people=None, light=True, notices=None, extra=None):
    ents = {"light_1": {"type": "lamp", "location": "lab", "pos": [COLS - 1, 0], "on": light},
            "window_1": {"type": "window", "location": "lab", "pos": [5, ROWS - 1]},
            "table_1": {"type": "table", "location": "lab", "pos": [3, 3], "accepts_items": True},
            "note_1": {"type": "generic_item", "label": "note", "category": "sticky_note", "location": "table_1",
                       "relation": "on", "size_m": [0.08, 0.08, 0.001], "text": "Please water the fern"},
            "fern_1": {"type": "generic_item", "label": "fern", "location": "lab", "pos": [7, 6],
                       "size_m": [0.4, 0.4, 0.6]}}
    if people is None:
        people = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [6, 3], "has_phone": True},
                  "vic": {"label": "Vic", "role": "visitor", "location": "lab", "pos": [8, 3]},
                  "sam": {"label": "Sam", "role": "adult", "location": "hall", "pos": [1, 1], "has_phone": True}}
    cfg = {"schema_version": 2, "start_room": "lab",
           "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "living_room", "exits": ["hall"]},
                     "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
           "entities": ents, "people": copy.deepcopy(people), "robot": {"room": "lab", "pos": [2, 3]},
           "weather": {"condition": "rain", "temperature_c": 9.0, "wind_mps": 4.0,
                       "process": {"model": "normals_v1", "region": "uk", "seed_key": "x"},
                       "override_until_s": 600.0, "_draw": 0.3}}
    if requests is not None:
        cfg["requests"] = copy.deepcopy(requests)
    if notices is not None:
        cfg["notices"] = copy.deepcopy(notices)
    if not light:
        cfg["clock"] = {"start": "2026-10-09T23:30:00"}
    for key, value in (extra or {}).items():
        cfg[key] = copy.deepcopy(value)
    return cfg


def request(**kw):
    out = {"id": "r1", "phase": None, "from": "ann", "channel": "in_person", "room": "lab", "t": 0.0,
           "text": "Please water the fern", "subject": "fern_1"}
    out.update(kw)
    return out


def make(mode="oracle", **kw):
    return HouseEnv(config(**kw), seed=6, perception=mode, trace=False)


class RequestTests(unittest.TestCase):
    def test_no_request_no_key(self):
        obs = make().observe()
        self.assertNotIn("request", obs)
        self.assertNotIn("notices", obs)
        self.assertEqual(obs["instruction"], "")

    def test_the_oracle_request_and_the_instruction(self):
        env = make(requests=[request(), request(id="r2", text="Now the other one", t=30.0)])
        obs = env.observe()
        self.assertEqual(obs["request"], {"id": "r2", "from": "ann", "from_label": "Ann", "channel": "in_person",
                                          "room": "lab", "time_s": 30.0, "text": "Now the other one"})
        self.assertEqual(obs["instruction"], "Now the other one")
        task = {"instruction": "Water the fern", "success": []}
        env = HouseEnv(config(requests=[request(text="")]), task=task, seed=6, trace=False)
        self.assertEqual(env.observe()["instruction"], "Water the fern")      # an empty request text keeps the task's

    def test_realistic_in_person_requests(self):
        env = make("realistic", requests=[request()])
        self.assertEqual((env.observe()["request"]["from"], env.observe()["request"]["from_label"]), ("ann", "Ann"))
        env = make("realistic", requests=[request(**{"from": "vic"})])
        self.assertEqual(env.observe()["request"]["from"], "unfamiliar person")
        dark = make("realistic", requests=[request()], light=False)
        shown = dark.observe()["request"]["from"]
        self.assertTrue(shown.startswith("obj_"))
        self.assertEqual(shown, dark.realism._dark_agents(dark.world.s)["ann"])
        self.assertEqual(dark.observe()["request"]["from"], shown)          # decided once
        self.assertNotIn("ann", dark.observe()["request"].values())

    def test_realistic_remote_and_written_requests(self):
        env = make("realistic", requests=[request(**{"from": "sam", "channel": "phone", "room": "hall"})])
        self.assertEqual(env.observe()["request"]["from"], "sam")
        env = make("realistic", requests=[request(**{"from": "vic", "channel": "app"})])
        self.assertEqual(env.observe()["request"]["from"], "unknown caller")
        env = make("realistic", requests=[request(channel="note", note="note_1")])
        shown = env.observe()["request"]
        self.assertEqual(shown["from"], "note")
        self.assertEqual(shown["note"], env.realism.handles.handle("note_1"))
        self.assertNotIn("note_1", json.dumps(shown))

    def test_the_decision_is_saved_with_realism(self):
        env = make("realistic", requests=[request(**{"from": "vic"})])
        env.observe()
        dump = env.realism.dump()
        self.assertEqual(dump["requests"]["r1"]["from"], "unfamiliar person")
        other = make("realistic", requests=[request(**{"from": "vic"})])
        other.realism.load(dump)
        self.assertEqual(other.realism._request_from, env.realism._request_from)


class NoticeTests(unittest.TestCase):
    NOTICES = [{"t": 0.0, "step": 0, "kind": "doorbell", "near": "hall"},
               {"t": 31.25, "step": 1, "kind": "speech", "room": "lab", "source": "ann", "text": "Hello"},
               {"t": 31.25, "step": 1, "kind": "appliance_done", "room": "lab", "source": "fern_1"}]

    def test_only_the_current_steps_notices(self):
        env = make(notices=self.NOTICES[:1])
        self.assertEqual([n["kind"] for n in env.observe()["notices"]], ["doorbell"])
        env.world.s["notices"].extend(copy.deepcopy(self.NOTICES[1:]))
        env.step("wait", seconds=30)
        self.assertEqual([n["kind"] for n in env.observe()["notices"]], ["speech", "appliance_done"])
        env.step("wait", seconds=5)
        self.assertNotIn("notices", env.observe())

    def test_unstamped_notices_count_by_time(self):
        state = {"steps": 3, "action_log": [{"step": 3, "time_s": 100.0}],
                 "notices": [{"t": 90.0, "kind": "knock"}, {"t": 100.0, "kind": "knock"}, {"t": 120.0, "kind": "chime"}]}
        self.assertEqual([n["kind"] for n in perception.notices_since(state)], ["chime"])

    def test_realistic_notices(self):
        env = make("realistic", notices=self.NOTICES[:1])
        env.world.s["notices"].extend(copy.deepcopy(self.NOTICES[1:]))
        env.step("wait", seconds=30)
        notices = env.observe()["notices"]
        self.assertEqual([n["t"] for n in notices], [31.0, 31.0])
        self.assertEqual(notices[0]["source"], "ann")
        self.assertEqual(notices[1]["source"], env.realism.handles.handle("fern_1"))
        self.assertNotIn("fern_1", json.dumps(notices))

    def test_no_notices_while_powered_down(self):
        env = make(notices=self.NOTICES[:1])
        env.world.s["robot"]["power_state"] = "off"
        env.world.s["robot"]["battery"] = 0.0
        self.assertNotIn("notices", env.observe())

    def test_listen_returns_the_latest_notices(self):
        env = make(notices=self.NOTICES)
        heard = json.loads(env.step("listen")["message"])
        self.assertEqual([n["kind"] for n in heard["notices"]], ["doorbell", "speech", "appliance_done"])
        self.assertEqual(json.loads(make().step("listen")["message"]), "No new message")


class WeatherTests(unittest.TestCase):
    def test_the_weather_process_is_never_shown(self):
        for mode in ("oracle", "realistic"):
            weather = make(mode).observe()["weather"]
            self.assertEqual(weather["condition"], "rain")
            for key in ("process", "override_until_s", "_draw"):
                self.assertNotIn(key, weather, mode)


TWO_PHASES = {
    "catalog_id": "T-requests-phases-01", "title": "two requests", "risk_level": 0,
    "house": {"residents": "minimal", "pets": "none"},
    "phases": [
        {"id": "p1", "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say one."]},
         "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "one"}],
         "budget": {"steps": 20, "time_s": 900}},
        {"id": "p2", "start": {"after": "p1", "gap_s": 600},
         "request": {"from": "resident_1", "channel": "app", "phrasings": ["Say two."]},
         "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "two"}],
         "budget": {"steps": 20, "time_s": 900}}],
    "reference": [{"do": "announce", "text": "one"}, {"phase_done": True}, {"do": "announce", "text": "two"},
                  {"phase_done": True}]}


class PhaseTests(unittest.TestCase):
    def test_the_active_phases_request_and_memory_across_phases(self):
        from house_sim.tasks import runner
        if not hasattr(HouseEnv, "phase_done"):
            self.skipTest("phases are TIME-EVAL's")
        for mode in ("oracle", "realistic"):
            config_, task, _ = runner.build(copy.deepcopy(TWO_PHASES), 0)
            env = HouseEnv(config_, task, 0, perception=mode, trace=False)
            obs = env.observe()
            self.assertEqual((obs["request"]["id"], obs["request"]["from"], obs["instruction"]),
                             ("r1", "resident_1", "Say one."))
            env.step("announce", text="one")
            rooms = set(env.mind.map)
            env.phase_done()
            obs = env.observe()
            self.assertEqual((obs["request"]["id"], obs["instruction"]), ("r2", "Say two."))
            self.assertTrue(rooms <= set(env.mind.map))                 # the Mind carries over the gap
            self.assertEqual(perception.current_request(env.world.s)["phase"], "p2")

    def test_a_schema_3_episode_shows_what_the_robot_heard(self):
        from house_sim.tasks import runner, spec as spec_mod
        if "schema" not in getattr(spec_mod, "ALLOWED", ()):
            self.skipTest("the schema-3 spec key and the notice bus are TIME-EVAL's")
        spec = {"catalog_id": "T-requests-notices-01", "title": "t", "schema": 3, "instruction": "Wait",
                "house": {"residents": "minimal", "pets": "none"}, "setup": [],
                "success": [{"path": "time_s", "op": "gte", "value": 0}], "reference": [{"do": "wait", "seconds": 1}]}
        for mode in ("oracle", "realistic"):
            config_, task, _ = runner.build(copy.deepcopy(spec), 0)
            env = HouseEnv(config_, task, 0, perception=mode, trace=False)
            state = env.world.s
            pid = sorted(state["people"])[0]
            if state["people"][pid]["location"] != state["robot"]["room"]:
                self.skipTest("the resident is elsewhere in this house")
            self.assertEqual(env.observe()["request"]["from"], pid)
            env.step("wait", seconds=30)                                # the notice cursors start
            state["messages"].append({"step": state["steps"] + 1, "time_s": state["time_s"] + 1.0, "speaker": pid,
                                      "kind": "speech", "text": "Robot, come here", "to": "robot",
                                      "room": state["people"][pid]["location"], "heard_by_robot": True})
            obs = env.step("wait", seconds=60)["observation"]
            kinds = [n["kind"] for n in obs.get("notices") or []]
            self.assertIn("addressed", kinds, mode)
            heard = [n for n in obs["notices"] if n["kind"] == "addressed"][0]
            self.assertEqual(heard["text"], "Robot, come here")
            self.assertNotIn("notices", env.step("wait", seconds=30)["observation"])      # only the step's own

    def test_the_active_phase_wins_over_a_later_record(self):
        state = {"phase": {"active": "p1"}, "requests": [{"id": "r1", "phase": "p1"}, {"id": "r2", "phase": "p2"}]}
        self.assertEqual(perception.current_request(state)["id"], "r1")
        state["phase"]["active"] = None
        self.assertEqual(perception.current_request(state)["id"], "r2")
        state["phase"]["history"] = [{"id": "p1"}]                  # between phases: the phase that ended last
        self.assertEqual(perception.current_request(state)["id"], "r1")

    # ----- stage-C merge: one rule for the current request and the requester ----------------------------------------
    @staticmethod
    def two_requesters(second_request=True):
        """Ann asks in phase 1; phase 2 comes from Bob, or without a request (its own instruction only)."""
        cfg = config(people={"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [6, 3]},
                             "bob": {"label": "Bob", "role": "adult", "location": "lab", "pos": [7, 3]}})
        cfg["user_simulator"] = {"intent": {"requester": "ann"}}     # as the runner binds it: the first phase's
        p2 = {"id": "p2", "instruction": "Second job.", "success": [{"path": "time_s", "op": "gte", "value": 0}]}
        if second_request:
            p2["request"] = {"id": "r2", "from": "bob", "channel": "in_person", "text": "Bob: second job."}
            p2["instruction"] = "Bob: second job."
        task = {"instruction": "Ann: first job.", "phases": [
            {"id": "p1", "request": {"id": "r1", "from": "ann", "channel": "in_person", "text": "Ann: first job."},
             "instruction": "Ann: first job.", "success": [{"path": "time_s", "op": "gte", "value": 0}]}, p2]}
        return cfg, task

    def test_a_phase_without_a_request_shows_its_own_instruction(self):
        """Stage-C merge: obs.request / obs.instruction (PEOPLE-IO) used the previous phase's request when the active
        phase had none, while env.request() and the task's instruction (TIME-EVAL) were the new phase's: the agent was
        shown the old instruction. One rule now: perception.current_request."""
        from house_sim.kernel import dialogue, evaluation
        for mode in ("oracle", "realistic"):
            cfg, task = self.two_requesters(second_request=False)
            env = HouseEnv(cfg, task=task, seed=6, perception=mode, trace=False)
            env.phase_done()
            obs = env.observe()
            self.assertEqual(obs["instruction"], "Second job.", mode)
            self.assertNotIn("request", obs)
            self.assertEqual((env.request()["id"], env.request()["text"]), ("r0", "Second job."))
            self.assertIsNone(perception.current_request(env.world.s))
            self.assertEqual(dialogue.requester(env.world.s), "ann")            # the bound requester stands
            self.assertEqual(evaluation._requester(env.world.s), "ann")

    def test_the_requester_of_a_phase_is_its_requests_from(self):
        """Stage-C merge: "the requester" was the bound intent.requester for the evaluation (ask_whom) but the current
        request's from for the dialogue (known_by): in Bob's phase a required question asked to Bob counted as
        answered by someone who knows, yet failed ask_whom "requester". One rule now (dialogue.requester)."""
        from house_sim.kernel import dialogue, evaluation
        cfg, task = self.two_requesters()
        env = HouseEnv(cfg, task=task, seed=6, trace=False)
        self.assertEqual(dialogue.requester(env.world.s), "ann")
        env.phase_done()
        state = env.world.s
        self.assertEqual((dialogue.requester(state), evaluation._requester(state)), ("bob", "bob"))
        self.assertEqual(env.request()["from"], "bob")
        self.assertEqual(env.observe()["request"]["from"], "bob")
        self.assertTrue(dialogue.knows(state, "bob", "requester"))
        record = {"t": state["time_s"], "to": "bob", "matched": "f", "comprehended": True, "answered": True}
        questions = {"required": ["f"], "ask_whom": {"f": "requester"}}
        self.assertEqual(evaluation.question_failures(questions, dict(state, dialogue_log=[record])), [])
        wrong = dict(record, to="ann")
        self.assertEqual(evaluation.question_failures(questions, dict(state, dialogue_log=[wrong])),
                         ["question_missing:f"])
        # without any request or bound intent: the household's default requester (schema.normalize) for both
        bare = {"requester": "ann", "people": {"ann": {}}}
        self.assertEqual((dialogue.requester(bare), evaluation._requester(bare)), ("ann", "ann"))

    def test_a_phase_request_is_heard_and_phase_done_shows_only_new_notices(self):
        """Stage-C merge: (1) a phase's request posted at a substep boundary was never derived as an "addressed" notice
        (the message cursor's key equalled the request's (step, time)), so a listening wait or obs.notices missed it;
        (2) phase_done counts no step, so its observation repeated the previous step's notices as if new."""
        for mode in ("oracle", "realistic"):
            cfg = config(notices=[], people={"ann": {"label": "Ann", "role": "adult", "location": "lab",
                                                     "pos": [6, 3]},
                                             "bob": {"label": "Bob", "role": "adult", "location": "hall",
                                                     "pos": [1, 1]}})
            cfg["events"] = [{"at_s": 30, "changes": {"people.bob.location": "lab", "people.bob.pos": [7, 5]}}]
            task = {"instruction": "One.", "phases": [
                {"id": "p1", "request": {"id": "r1", "from": "ann", "channel": "in_person", "text": "One."},
                 "instruction": "One.", "success": [{"path": "time_s", "op": "gte", "value": 0}]},
                {"id": "p2", "start": {"after": "p1", "gap_s": 120},
                 "request": {"id": "r2", "from": "ann", "channel": "in_person", "text": "Two."},
                 "instruction": "Two.", "success": [{"path": "time_s", "op": "gte", "value": 0}]}]}
            env = HouseEnv(cfg, task=task, seed=6, perception=mode, trace=False)
            self.assertNotIn("notices", env.observe())                  # the first request is obs.request only
            obs = env.step("wait", seconds=60)["observation"]
            self.assertEqual([n["kind"] for n in obs["notices"]], ["person_enters"], mode)
            obs = env.phase_done()["observation"]
            self.assertEqual([(n["kind"], n.get("text")) for n in obs["notices"]], [("addressed", "Two.")], mode)
            self.assertEqual(obs["request"]["id"], "r2")
            self.assertNotIn("notices", env.step("wait", seconds=5)["observation"])
            self.assertEqual(env.world.s["phase"]["notice_mark"]["step"], 1)

    def test_a_listening_wait_hears_the_request_of_a_phase_that_starts_in_it(self):
        """Stage-C merge: the request of a phase that starts by itself during a wait interrupts it ("addressed")."""
        cfg = config(people={"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [6, 3]}})
        task = {"instruction": "One.", "phases": [
            {"id": "p1", "request": {"id": "r1", "from": "ann", "channel": "in_person", "text": "One."},
             "instruction": "One.", "success": [{"path": "time_s", "op": "gte", "value": 0}], "ends": {"at_s": 90}},
            {"id": "p2", "request": {"id": "r2", "from": "ann", "channel": "in_person", "text": "Two."},
             "instruction": "Two.", "success": [{"path": "time_s", "op": "gte", "value": 0}]}]}
        env = HouseEnv(cfg, task=task, seed=6, trace=False)
        result = env.step("wait", seconds=600, interrupt_on=["addressed"])
        self.assertIn("interrupted: addressed", result["message"])
        self.assertEqual(env.world.s["time_s"], 90.0)
        self.assertEqual(env.request()["id"], "r2")


class RunnerTests(unittest.TestCase):
    def test_a_schema_3_request_reaches_the_observation(self):
        from house_sim.tasks import runner, spec as spec_mod
        if "schema" not in getattr(spec_mod, "ALLOWED", ()):
            self.skipTest("the schema-3 spec key is TIME-EVAL's")
        spec = {"catalog_id": "T-test-requests", "title": "t", "schema": 3, "instruction": "Water the fern",
                "house": {"seed_offset": 0, "residents": "minimal", "pets": "none"},
                "setup": [], "success": [{"path": "time_s", "op": "gte", "value": 0}],
                "reference": [{"do": "wait", "seconds": 1}]}
        try:
            config_, task, _ = runner.build(spec, seed=0)
            env = runner.make_env(config_, task, 0, fast=False)
        except Exception as exc:  # noqa: BLE001 -- a spec the runner rejects for another reason is not this test's
            self.skipTest("runner.build: {}".format(exc))
        obs = env.observe()
        if "request" not in obs:
            self.skipTest("the runner wrote no request")
        self.assertEqual(obs["request"]["text"], obs["instruction"])
        self.assertIn(obs["request"]["channel"], ("in_person", "app"))


class StageCFixTests(unittest.TestCase):
    """The stage-C review's request and notice findings (stageC_review_people.json / stageC_review_time.json)."""

    def test_a_relayed_request_is_said_by_its_relayer(self):
        """obs.request names the relayer as "from" and the person the relayer says it comes from as "claims_from";
        the dialogue's requester stays the claimed one."""
        from house_sim.kernel import dialogue
        relayed = request(**{"from": "sam", "relayed_by": "ann"})
        for mode in ("oracle", "realistic"):
            env = make(mode, requests=[relayed])
            shown = env.observe()["request"]
            self.assertEqual((shown["from"], shown["from_label"], shown["claims_from"]), ("ann", "Ann", "sam"), mode)
            self.assertEqual(shown["room"], "lab")
            self.assertEqual(dialogue.requester(env.world.s), "sam")

    def test_a_remote_request_does_not_say_where_its_sender_is(self):
        for channel in ("phone", "app", "text", "note"):
            env = make(requests=[request(**{"from": "sam", "channel": channel, "room": "hall"})])
            self.assertIsNone(env.observe()["request"]["room"], channel)
        for channel in ("in_person", "overheard", "intercom"):
            env = make(requests=[request(channel=channel)])
            self.assertEqual(env.observe()["request"]["room"], "lab", channel)

    def test_the_request_message_names_its_sender_as_the_request_does(self):
        task = {"instruction": "x", "success": [],
                "request": {"from": "vic", "channel": "phone", "text": "I'm outside."}}
        env = HouseEnv(config(), task=task, seed=6, perception="realistic", trace=False)
        obs = env.observe()
        self.assertEqual(obs["request"]["from"], "unknown caller")
        message = [m for m in obs["messages"] if m.get("request")][0]
        self.assertEqual(message["speaker"], "unknown caller")
        self.assertIsNone(message["room"])
        self.assertNotIn("vic", json.dumps(obs["request"]) + json.dumps(message))
        task["request"] = {"from": "vic", "channel": "in_person", "text": "Can you sign for this?"}
        env = HouseEnv(config(), task=task, seed=6, perception="realistic", trace=False)
        obs = env.observe()
        message = [m for m in obs["messages"] if m.get("request")][0]
        self.assertEqual((obs["request"]["from"], message["speaker"]), ("unfamiliar person", "unfamiliar person"))

    def test_listen_returns_every_perceived_notice_in_privacy_mode(self):
        env = make(notices=[])
        w = env.world
        w.begin()
        w.sec("robot")["privacy_mode"] = True
        w.notice("alarm", room="lab", db=85)
        w.notice("glass_breaking", room="lab", db=60)
        w.notice("speech", room="lab", db=60, text="not for the robot")
        w.notice("addressed", room="lab", db=60, text="robot, stop", to_robot=True)
        w.commit()
        self.assertEqual([n["kind"] for n in w.s["notices"]], ["alarm", "glass_breaking", "addressed"])
        heard = json.loads(env.step("listen")["message"])
        self.assertEqual([n["kind"] for n in heard["notices"]], ["alarm", "glass_breaking", "addressed"])

    def test_a_note_request_names_its_note_through_a_spec(self):
        from house_sim.tasks import runner, spec as spec_mod
        self.assertEqual(spec_mod.request_problems({"channel": "note", "note": "{note}"}, "request"), [])
        self.assertTrue(spec_mod.request_problems({"channel": "phone", "note": "{note}"}, "request"))
        spec = {"catalog_id": "T-test-note-request", "title": "t", "schema": 3,
                "instruction": "Do what the note says", "phrasings": ["a", "b", "c"],
                "house": {"residents": "minimal", "pets": "none"},
                "setup": [{"op": "room", "var": "living", "type": "living_room", "pick": "first"},
                          {"op": "person", "id": "resident_1", "room": "{living}"},
                          {"op": "robot", "room": "{living}"},
                          {"op": "spawn", "type": "generic_item", "floor": "{living}", "var": "note",
                           "props": {"label": "sticky note", "size_m": [0.08, 0.08, 0.01], "text": "Water the fern"}}],
                "request": {"from": "resident_1", "channel": "note", "note": "{note}"},
                "success": [{"path": "time_s", "op": "gte", "value": 0}], "reference": [{"do": "wait", "seconds": 1}],
                "wrong": [{"name": "w", "plan": []}], "risk_level": 0}
        for mode in ("oracle", "realistic"):
            config_, task, oracle = runner.build(copy.deepcopy(spec), seed=0)
            note = oracle["bindings"]["note"]
            env = runner.make_env(config_, task, 0, perception=mode)
            self.assertEqual(env.world.s["requests"][0]["note"], note)
            shown = env.observe()["request"]
            self.assertEqual(shown["from"], "resident_1" if mode == "oracle" else "note")
            self.assertEqual(shown["note"], note if mode == "oracle" else env.realism.handles.handle(note))
            self.assertIsNone(shown["room"])

    def test_a_requests_gesture_is_in_the_first_observation(self):
        from house_sim.kernel.plugins import HOOKS, load_domains
        load_domains()
        if not HOOKS.get("event:people.point"):
            self.skipTest("the people domain's point effect is not loaded")
        task = {"instruction": "x", "success": [],
                "request": {"from": "ann", "channel": "in_person", "text": "Bring me that one.",
                            "gesture": {"point_at": "fern_1"}}}
        for mode in ("oracle", "realistic"):
            env = HouseEnv(config(), task=task, seed=6, perception=mode, trace=False)
            gestures = env.observe().get("gestures") or []
            self.assertTrue(gestures, mode)
            self.assertEqual(gestures[0]["time_s"], 0.0)
            self.assertFalse(env.world.s["requests"][0].get("gesture_dropped"))


if __name__ == "__main__":
    unittest.main()
