"""Canonical questions to people (PEOPLE-IO, kernel v3.1; docs/design/TIER1_SWEEP.md 4 and 16; docs/DIALOGUE.md).

What this file pins:

* subjects: a handle (realistic) or id, a room, person or pet id, words through plurals, the synonym table and stop
  words, "it" (the last subject discussed with that person), "request" (the current request's subject), "house";
  a word phrase naming exactly one room or person is that room or person;
* fact matching: every aspect; specificity (entity id > type > room / person / pet / event > kind / topic / alias),
  then a matching detail, then list order; an "ok" act matches the fact's detail; calendar events answer "when";
* the pipeline: clarify-back when a word names several household things and one has a fact (4 s, nothing counted);
  known_by and otherwise; reliability and wrong_value (deterministic); grants and sets; options; the unknown answer
  (unnecessary + questions_unmatched); the conversation context; the dialogue_log record;
* necessity: not_known (asked before, memory, observable_via at glance / inspect / read), always, never, ambiguous,
  no_history, pred, unless, and asking someone outside known_by;
* channels: voice in the room only, phone and text over the network, written next to the person in a lit room with
  the cameras on; deaf people do not hear voice questions; sleepers; the microphones; languages (a misunderstood
  question counts nothing);
* routes: every people prefix answers its canonical question with the same answer and the same question statistics
  as the legacy topic (people domain loaded), and the legacy topic path is byte-identical to the code before kernel
  v3.1 (message and state digest, with the pre-3.1 handler swapped in);
* realistic perception: an answer never gives a handle for a thing the robot has not seen; clarify options and
  refers_to are descriptions;
* hooks (dialogue_channel, dialogue_comprehends, dialogue_render, dialogue_reliability), the vocabulary loader and the
  ask budget (2 ms; the test fails at 3x);
* the stage-C merge: the verifier runs the schema-3 lints (nested plan blocks, phase questions), and a schema-3
  preference spec using both packages verifies end to end (seed 0; generated house, core types).

The houses are built by hand (core types only), so the file passes with HOUSE_SIM_DOMAINS=core; the route tests need
the people domain and skip without it.
"""
import copy
import json
import time
import unittest

from house_sim.generator import generate_house
from house_sim.kernel import dialogue, plugins
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, ANSWERERS, HOOKS
from house_sim.kernel.predicates import digest
from house_sim.systems import core

COLS, ROWS = 12, 8


def config(people=None, entities=None, facts=None, extra=None, light=True):
    """A lit living room ("lab": a light, a sofa, a coffee table, a bed, three books, a box, a blanket on the table)
    and a hallway;
    Ann (adult) and Kim (child) in the lab, Sam (adult, has a phone) in the hall, Rex the dog in the lab."""
    ents = {"light_1": {"type": "lamp", "location": "lab", "pos": [COLS - 1, 0], "on": light},
            "light_2": {"type": "lamp", "location": "hall", "pos": [0, 0], "on": True},
            "sofa_1": {"type": "sofa", "location": "lab", "pos": [3, 1]},
            "table_1": {"type": "coffee_table", "location": "lab", "pos": [5, 1], "accepts_items": True},
            "bed_1": {"type": "bed", "location": "lab", "pos": [8, 5], "accepts_items": True},
            "book_1": {"type": "book", "label": "red book", "color": "red", "location": "table_1", "relation": "on"},
            "book_2": {"type": "book", "label": "blue book", "color": "blue", "location": "bed_1", "relation": "on"},
            "book_3": {"type": "book", "label": "green book", "color": "green", "location": "lab", "pos": [6, 6]},
            "box_1": {"type": "box", "location": "lab", "pos": [1, 6]},
            "blanket_1": {"type": "generic_item", "label": "comfort blanket", "location": "table_1", "relation": "on",
                          "size_m": [0.6, 0.4, 0.1], "mass_kg": 1.0}}
    ents.update(copy.deepcopy(entities or {}))
    if people is None:
        people = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [4, 3]},
                  "kim": {"label": "Kim", "role": "child", "age": 8, "location": "lab", "pos": [5, 4]},
                  "sam": {"label": "Sam", "role": "adult", "location": "hall", "pos": [1, 1], "has_phone": True}}
    cfg = {"schema_version": 2, "start_room": "lab",
           "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "living_room", "label": "Living room",
                             "exits": ["hall"]},
                     "hall": {"size_m": [2.0, 3.0], "type": "hallway", "label": "Hall", "exits": ["lab"]}},
           "entities": ents, "people": copy.deepcopy(people),
           "pets": {"rex": {"label": "Rex", "species": "dog", "location": "lab", "pos": [7, 2]}},
           "robot": {"room": "lab", "pos": [3, 3]},
           "user_simulator": {"facts": copy.deepcopy(facts or [])}}
    for key, value in (extra or {}).items():
        cfg[key] = copy.deepcopy(value)
    return cfg


def env_of(facts=None, perception="oracle", **kw):
    return HouseEnv(config(facts=facts, **kw), seed=3, perception=perception, trace=False)


def ask(env, pid, **args):
    result = env.step("ask", pid, **args)
    data = json.loads(result["message"]) if result["ok"] else None
    return result, data


def stats(env):
    s = env.world.s.get("stats") or {}
    return s.get("necessary_questions", 0), s.get("unnecessary_questions", 0), s.get("questions_unmatched", 0)


def people_loaded():
    plugins.load_domains()
    return "which:" in ANSWERERS


class VocabularyTests(unittest.TestCase):
    def test_the_vocabulary_loads_once_without_problems(self):
        vocab = dialogue.vocabulary()
        self.assertIs(vocab, dialogue.vocabulary())
        self.assertEqual(vocab["_index"]["problems"], [])
        self.assertEqual(set(vocab["aspects"]), set(dialogue.ASPECTS))
        start = time.perf_counter()
        dialogue.load_vocabulary()
        self.assertLess(time.perf_counter() - start, 0.06)          # budget 20 ms, 3x

    def test_normalization_singularizes_and_applies_synonyms(self):
        cases = {"Washing machines": "washing_machine", "the couch": "sofa", "Books": "book", "knives": "knife",
                 "boxes": "box", "glasses": "glasses", "berries": "berry", "cell phone": "smartphone",
                 "TV": "television", "My comfort blanket, please": "comfort_blanket"}
        for text, want in cases.items():
            self.assertEqual(dialogue.phrase(text), want, text)

    def test_aspects_details_and_acts_have_canonical_forms(self):
        self.assertEqual(dialogue.canonical_aspect("Whose"), "owner")
        self.assertEqual(dialogue.canonical_aspect("how many"), "amount")
        self.assertEqual(dialogue.canonical_aspect("permission"), "ok")
        self.assertEqual(dialogue.canonical_aspect("help"), "help")
        self.assertIsNone(dialogue.canonical_aspect("flavour of the month"))
        self.assertEqual(dialogue.canonical_detail("pillows"), "pillow_layout")
        self.assertEqual(dialogue.canonical_detail("cat toys"), "cat_toy")
        self.assertEqual(dialogue.canonical_act("Pick Up"), "pick_up")

    def test_the_docs_tables_are_generated_from_the_data(self):
        from pathlib import Path
        from tools import vocab as vocab_tool
        docs = (Path(__file__).resolve().parents[1] / "docs" / "DIALOGUE.md").read_text(encoding="utf-8")
        self.assertIn(vocab_tool.markdown(dialogue.vocabulary()).strip(), docs,
                      "regenerate the tables: python tools/vocab.py --markdown")
        self.assertEqual(vocab_tool.problems(dialogue.vocabulary()), [])

    def test_a_key_defined_in_two_files_fails_at_load(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.json", Path(tmp) / "b.json"
            a.write_text(json.dumps({"issues": {"leak": {"class": "moisture"}}, "issue_classes": ["moisture"]}))
            b.write_text(json.dumps({"issues": {"leak": {"class": "moisture"}}}))
            with self.assertRaises(ValueError):
                dialogue.load_vocabulary([a, b])
            b.write_text(json.dumps({"bogus": {}}))
            with self.assertRaises(ValueError):
                dialogue.load_vocabulary([a, b])


class SubjectTests(unittest.TestCase):
    def setUp(self):
        self.env = env_of()

    def resolve(self, about, pid="ann"):
        return dialogue.resolve_subject(self.env, pid, about)

    def test_ids_rooms_people_pets_and_the_house(self):
        self.assertEqual(self.resolve("book_1"), dialogue.Subject("entity", "book_1"))
        self.assertEqual(self.resolve("hall"), dialogue.Subject("room", "hall"))
        self.assertEqual(self.resolve("sam"), dialogue.Subject("person", "sam"))
        self.assertEqual(self.resolve("rex"), dialogue.Subject("pet", "rex"))
        self.assertEqual(self.resolve("house").kind, "house")

    def test_words_name_rooms_and_people_by_label(self):
        self.assertEqual(self.resolve("the living room").id, "lab")
        self.assertEqual(self.resolve("Sam").id, "sam")
        word = self.resolve("my Comfort Blankets")
        self.assertEqual((word.kind, word.words), ("word", ("comfort", "blanket")))
        self.assertEqual(self.resolve("couch").words, ("sofa",))

    def test_it_is_the_last_subject_discussed_with_that_person(self):
        with self.assertRaises(Exception):
            self.resolve("it")
        result, data = ask(self.env, "ann", aspect="where", about="book_2")
        self.assertTrue(result["ok"])
        self.assertEqual(self.resolve("it"), dialogue.Subject("entity", "book_2"))
        refused = self.env.step("ask", "kim", aspect="where", about="it")
        self.assertFalse(refused["ok"])
        self.assertIn("Ask about something first", refused["message"])

    def test_request_is_the_current_requests_subject(self):
        env = env_of(extra={"requests": [{"id": "r1", "from": "ann", "channel": "in_person", "room": "lab", "t": 0.0,
                                          "text": "Bring me my book", "subject": "book_3"}]})
        subject = dialogue.resolve_subject(env, "ann", "request")
        # "the request" stays a request subject (its record id); what it stands for is used for matching only
        self.assertEqual((subject.kind, subject.id), ("request", "r1"))
        self.assertIn("request", subject.words)
        self.assertEqual(dialogue.bound_subject(env.world.s, subject), dialogue.Subject("entity", "book_3",
                                                                                         ("request",)))
        refused = self.env.step("ask", "ann", aspect="which", about="request")
        self.assertFalse(refused["ok"])

    def test_empty_subjects_are_refused(self):
        result = self.env.step("ask", "ann", aspect="where", about="the")
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "argument")


class FactTests(unittest.TestCase):
    def test_every_aspect_is_answered_from_a_fact(self):
        facts = [{"id": "f_" + a, "about": "house", "aspect": a, "value": "answer " + a, "known_by": "everyone"}
                 for a in dialogue.ASPECTS]
        env = env_of(facts)
        for aspect in dialogue.ASPECTS:
            result, data = ask(env, "ann", aspect=aspect, about="house")
            self.assertTrue(result["ok"], result["message"])
            self.assertEqual(data["value"], "answer " + aspect)
            self.assertEqual(data["said"], "answer " + aspect)
            self.assertTrue(data["known"])
            self.assertEqual(data["key"], "q:{}:house".format(aspect))
            self.assertEqual(env.world.s["facts"]["q:{}:house".format(aspect)], "answer " + aspect)
        self.assertEqual(stats(env), (14, 0, 0))

    def test_the_most_specific_fact_wins(self):
        facts = [{"id": "kind", "about": "kind:book", "aspect": "where", "value": "kind"},
                 {"id": "type", "about": "type:book", "aspect": "where", "value": "type"},
                 {"id": "room", "about": "room:lab", "aspect": "where", "value": "room"},
                 {"id": "ent", "about": "book_2", "aspect": "where", "value": "entity"},
                 {"id": "ent_detail", "about": "book_2", "aspect": "where", "detail": "storage", "value": "detail"}]
        env = env_of(facts)
        state = env.world.s
        subject = dialogue.Subject
        self.assertEqual(dialogue.match_fact(state, subject("entity", "book_2"), "where", None, env)["id"], "ent")
        self.assertEqual(dialogue.match_fact(state, subject("entity", "book_2"), "where", "storage", env)["id"],
                         "ent_detail")
        self.assertEqual(dialogue.match_fact(state, subject("entity", "book_1"), "where", None, env)["id"], "type")
        self.assertEqual(dialogue.match_fact(state, subject("word", None, ("book",)), "where", None, env)["id"],
                         "type")
        self.assertEqual(dialogue.match_fact(state, subject("room", "lab"), "where", None, env)["id"], "room")
        self.assertIsNone(dialogue.match_fact(state, subject("entity", "book_2"), "owner", None, env))

    def test_a_fact_with_a_detail_answers_only_that_detail(self):
        facts = [{"id": "fold", "about": "kind:towel", "aspect": "preference", "detail": "fold_style",
                  "value": "thirds"},
                 {"id": "any", "about": "kind:towel", "aspect": "preference", "value": "anything"}]
        env = env_of(facts)
        self.assertEqual(ask(env, "ann", aspect="preference", about="towels", detail="folding")[1]["value"], "thirds")
        self.assertEqual(ask(env, "ann", aspect="preference", about="towels", detail="color")[1]["value"], "anything")

    def test_list_order_breaks_ties_and_aliases_match_words(self):
        facts = [{"id": "first", "about": "kind:tea", "aspect": "preference", "value": 1},
                 {"id": "second", "about": "kind:tea", "aspect": "preference", "value": 2},
                 {"id": "alias", "about": "topic:guests", "aspect": "amount", "aliases": ["visitors", "company"],
                  "value": 4}]
        env = env_of(facts)
        self.assertEqual(ask(env, "ann", aspect="preference", about="tea")[1]["value"], 1)
        self.assertEqual(ask(env, "ann", aspect="amount", about="company")[1]["value"], 4)
        self.assertEqual(ask(env, "sam", aspect="amount", about="guests", channel="phone")[1]["value"], 4)

    def test_ok_questions_match_the_fact_of_their_act(self):
        facts = [{"id": "cut_ok", "about": "box_1", "aspect": "ok", "detail": "cut", "value": True, "said": "Sure."}]
        env = env_of(facts)
        result, data = ask(env, "ann", aspect="ok", act="cut", about="box_1")
        self.assertEqual((data["value"], data["said"]), (True, "Sure."))

    def test_calendar_events_answer_when(self):
        events = {"calendar": {"events": [{"id": "ev_gym", "title": "Gym class", "kind": "practice",
                                           "start": "2026-10-10T18:00", "status": "tentative", "who": ["ann"],
                                           "hidden": {"bring_towel": True}}]}}
        env = env_of(extra={"household": events})
        result, data = ask(env, "ann", aspect="when", about="gym class")
        self.assertEqual(data["value"]["start"], "2026-10-10T18:00")
        self.assertIn("not confirmed", data["said"])
        result, data = ask(env, "ann", aspect="status", about="ev_gym", detail="bring towel")
        self.assertIs(data["value"], True)
        result, data = ask(env, "kim", aspect="when", about="ev_gym")
        self.assertFalse(data["known"])                 # the child is not among the event's known_by


class PipelineTests(unittest.TestCase):
    def test_a_word_naming_several_things_with_a_fact_gets_a_clarify_back(self):
        facts = [{"id": "b%d" % i, "about": "book_%d" % i, "aspect": "owner", "value": "x"} for i in (1, 2, 3)]
        env = env_of(facts)
        t0 = env.world.s["time_s"]
        result, data = ask(env, "ann", aspect="owner", about="book")
        self.assertEqual(data["clarify"], "Which book do you mean?")
        self.assertEqual(len(data["options"]), 3)
        self.assertTrue(all("book" in o and "obj_" not in o for o in data["options"]))
        self.assertEqual(stats(env), (0, 0, 0))
        self.assertAlmostEqual(env.world.s["time_s"] - t0, dialogue.ASK_TIME_S)
        log = env.world.s["dialogue_log"][-1]
        self.assertTrue(log["clarify"])
        self.assertIsNone(log["counted"])
        result, data = ask(env, "ann", aspect="owner", about="blue book")      # one thing: answered
        self.assertEqual(data["value"], "x")

    def test_known_by_and_otherwise(self):
        facts = [{"id": "secret", "about": "box_1", "aspect": "why", "value": "presents", "otherwise": "Ask Mum."}]
        env = env_of(facts)
        result, data = ask(env, "kim", aspect="why", about="box_1")
        self.assertEqual((data["said"], data["value"], data["known"]), ("Ask Mum.", None, False))
        self.assertEqual(stats(env), (0, 1, 0))
        self.assertTrue(env.world.s["dialogue_log"][-1]["otherwise"])
        result, data = ask(env, "ann", aspect="why", about="box_1")
        self.assertEqual(data["value"], "presents")
        self.assertEqual(stats(env), (1, 1, 0))
        for known_by, pid, ok in (("everyone", "kim", True), (["kim"], "kim", True), (["kim"], "ann", False),
                                  ("residents", "kim", True), ("requester", "ann", True), ("requester", "sam", False)):
            env = env_of([{"id": "k", "about": "house", "aspect": "how", "value": 1, "known_by": known_by}],
                         extra={"requests": [{"id": "r1", "from": "ann", "channel": "in_person", "t": 0.0}]})
            channel = "phone" if pid == "sam" else None
            data = ask(env, pid, aspect="how", about="house", channel=channel)[1]
            self.assertEqual(data["known"], ok, (known_by, pid))

    def test_reliability_gives_the_wrong_value_deterministically(self):
        facts = [{"id": "muddled", "about": "box_1", "aspect": "where", "value": "attic", "reliability": 0.0,
                  "wrong_value": "garage"}]
        env = env_of(facts)
        data = ask(env, "ann", aspect="where", about="box_1")[1]
        self.assertEqual(data["value"], "garage")
        self.assertFalse(env.world.s["dialogue_log"][-1]["reliable"])
        half = [dict(facts[0], reliability=0.5)]
        values = [ask(env_of(half), "ann", aspect="where", about="box_1")[1]["value"] for _ in range(2)]
        self.assertEqual(values[0], values[1])

    def test_grants_and_sets_apply_when_answered(self):
        facts = [{"id": "ok_box", "about": "box_1", "aspect": "ok", "detail": "open", "value": True,
                  "grants": ["touch:box_1"], "sets": {"user_simulator.intent.resolved": True}}]
        env = env_of(facts)
        ask(env, "ann", aspect="ok", act="open", about="box_1")
        self.assertIn("touch:box_1", env.world.s["approvals"])
        self.assertTrue(env.world.s["user_simulator"]["intent"]["resolved"])
        env = env_of(facts)
        ask(env, "kim", aspect="ok", act="open", about="box_1")             # the child does not know: nothing granted
        self.assertNotIn("touch:box_1", env.world.s.get("approvals", []))

    def test_options_are_answered_by_index(self):
        facts = [{"id": "which_book", "about": "kind:book", "aspect": "which", "value": "book_2"}]
        env = env_of(facts)
        data = ask(env, "ann", aspect="which", about="book", options=["book_1", "book_2"])[1]
        self.assertEqual(data["picked"], 1)
        data = ask(env, "ann", aspect="which", about="book", options=["red book", "blue book"])[1]
        self.assertEqual(data["picked"], 1)
        data = ask(env, "ann", aspect="which", about="book", options=["book_1", "book_3"])[1]
        self.assertIsNone(data["picked"])
        self.assertTrue(data["said"].startswith("Neither"))

    def test_an_unmatched_question_counts_unnecessary_and_unmatched(self):
        env = env_of()
        result, data = ask(env, "ann", aspect="why", about="spaceship")
        self.assertEqual((data["value"], data["said"], data["known"]), (None, "I don't know.", False))
        self.assertEqual(stats(env), (0, 1, 1))

    def test_the_log_record_and_the_conversation_context(self):
        facts = [{"id": "where_box", "about": "box_1", "aspect": "where", "value": "hall"}]
        env = env_of(facts)
        ask(env, "ann", aspect="where", about="box_1", question="Where does the box live?")
        record = env.world.s["dialogue_log"][-1]
        for key in ("t", "to", "about", "aspect", "detail", "act", "matched", "route", "counted", "said", "channel",
                    "comprehended", "answered", "key"):
            self.assertIn(key, record)
        self.assertEqual((record["to"], record["matched"], record["counted"], record["channel"]),
                         ("ann", "where_box", "necessary", "voice"))
        self.assertEqual(record["question"], "Where does the box live?")
        self.assertEqual(env.world.s["dialogue"]["ann"]["turns"], 1)
        data = ask(env, "ann", aspect="where", about="it")[1]
        self.assertEqual(data["value"], "hall")
        self.assertEqual(env.world.s["dialogue"]["ann"]["turns"], 2)
        self.assertEqual(env.world.s["facts"]["last_answer"]["value"], "hall")

    def test_topic_and_canonical_forms_do_not_mix(self):
        env = env_of()
        result = env.step("ask", "ann", topic="rules", aspect="how")
        self.assertFalse(result["ok"])
        self.assertIn("Use either topic or about/aspect", result["message"])
        result = env.step("ask", "ann")
        self.assertFalse(result["ok"])
        result = env.step("ask", "ann", aspect="flavour", about="box_1")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown aspect", result["message"])


class NecessityTests(unittest.TestCase):
    def one(self, fact, **kw):
        env = env_of([fact], **kw)
        return env

    def test_not_known_first_time_only(self):
        env = self.one({"id": "f", "about": "box_1", "aspect": "where", "value": "hall"})
        ask(env, "ann", aspect="where", about="box_1")
        ask(env, "kim", aspect="where", about="box_1")                      # asked before (any person): unnecessary
        self.assertEqual(stats(env)[:2], (1, 1))

    def test_not_known_respects_memory_and_evidence(self):
        env = self.one({"id": "f", "about": "box_1", "aspect": "where", "value": "hall"},
                       extra={"memory": {"note:f": {"value": "hall", "time_s": -1}}})
        ask(env, "ann", aspect="where", about="box_1")
        self.assertEqual(stats(env)[:2], (0, 1))
        glance = {"id": "g", "about": "book_3", "aspect": "how", "value": 1,
                  "observable_via": [{"entity": "book_3", "field": "color", "tier": "glance"}]}
        env = self.one(glance)
        ask(env, "ann", aspect="how", about="book_3")                       # the book is in plain view
        self.assertEqual(stats(env)[:2], (0, 1))
        inspect = dict(glance, observable_via=[{"entity": "book_3", "field": "color", "tier": "inspect"}])
        env = self.one(inspect)
        ask(env, "ann", aspect="how", about="book_3")
        self.assertEqual(stats(env)[:2], (1, 0))
        env = self.one(inspect)
        self.assertTrue(env.step("approach", "book_3")["ok"])
        self.assertTrue(env.step("inspect", "book_3")["ok"])
        ask(env, "ann", aspect="how", about="book_3")
        self.assertEqual(stats(env)[:2], (0, 1))
        read = dict(glance, observable_via=[{"entity": "book_3", "field": "title", "tier": "read"}])
        env = self.one(read, entities={"book_3": {"type": "book", "label": "green book", "location": "lab",
                                                  "pos": [6, 6], "text": "Gardening"}})
        env.step("approach", "book_3")
        env.step("inspect", "book_3")
        ask(env, "ann", aspect="how", about="book_3")                       # inspected, not read: still necessary
        self.assertEqual(stats(env)[:2], (1, 0))

    def test_realistic_evidence_uses_knowledge_levels(self):
        fact = {"id": "g", "about": "book_3", "aspect": "how", "value": 1,
                "observable_via": [{"entity": "book_3", "field": "color", "tier": "inspect"}]}
        env = env_of([fact], perception="realistic")
        handle = env.realism.handles.handle("book_3")
        ask(env, "ann", aspect="how", about=handle)
        self.assertEqual(stats(env)[:2], (1, 0))

    def test_always_and_never(self):
        env = self.one({"id": "a", "about": "house", "aspect": "how", "value": 1, "necessary": "always",
                        "known_by": "everyone"})
        ask(env, "ann", aspect="how", about="house")
        ask(env, "kim", aspect="how", about="house")
        self.assertEqual(stats(env)[:2], (1, 1))
        env = self.one({"id": "n", "about": "house", "aspect": "how", "value": 1, "necessary": "never"})
        ask(env, "ann", aspect="how", about="house")
        self.assertEqual(stats(env)[:2], (0, 1))

    def test_ambiguous_needs_two_candidates(self):
        fact = {"id": "amb", "about": "type:book", "aspect": "which", "value": "book_1", "necessary": "ambiguous"}
        env = self.one(fact)
        ask(env, "ann", aspect="which", about="book")                       # three books in view
        self.assertEqual(stats(env)[:2], (1, 0))
        env = self.one(dict(fact, about="box_1"))                           # one box
        ask(env, "ann", aspect="which", about="box_1")
        self.assertEqual(stats(env)[:2], (0, 1))
        env = self.one(dict(fact, about="box_1", candidates=["box_1", "book_1"]))
        ask(env, "ann", aspect="which", about="box_1")
        self.assertEqual(stats(env)[:2], (1, 0))

    def test_no_history_reads_placement_memories(self):
        fact = {"id": "hist", "about": "box_1", "aspect": "where", "value": "hall", "necessary": "no_history"}
        env = self.one(fact)
        ask(env, "ann", aspect="where", about="box_1")
        self.assertEqual(stats(env)[:2], (1, 0))
        history = [{"to": "hall"}, {"to": "hall"}, {"to": "hall"}, {"to": "lab"}]
        env = self.one(fact, extra={"memory": {"history:box_1": {"value": history, "time_s": -1}}})
        ask(env, "ann", aspect="where", about="box_1")
        self.assertEqual(stats(env)[:2], (0, 1))

    def test_pred_and_unless(self):
        fact = {"id": "p", "about": "box_1", "aspect": "keep", "value": "keep",
                "necessary": {"pred": {"path": "entities.box_1.type", "op": "eq", "value": "box"}}}
        env = self.one(fact)
        ask(env, "ann", aspect="keep", about="box_1")
        self.assertEqual(stats(env)[:2], (1, 0))
        fact = dict(fact, necessary={"unless": [{"path": "rooms.lab.type", "op": "eq", "value": "living_room"}]})
        env = self.one(fact)
        ask(env, "ann", aspect="keep", about="box_1")
        self.assertEqual(stats(env)[:2], (0, 1))

    def test_asking_someone_who_would_not_know_is_unnecessary(self):
        env = self.one({"id": "f", "about": "box_1", "aspect": "where", "value": "hall", "known_by": ["sam"]})
        ask(env, "ann", aspect="where", about="box_1")
        self.assertEqual(stats(env)[:2], (0, 1))


class ChannelTests(unittest.TestCase):
    FACT = [{"id": "f", "about": "house", "aspect": "how", "value": 1, "known_by": "everyone"}]

    def test_voice_needs_the_person_in_the_room(self):
        env = env_of(self.FACT)
        result = env.step("ask", "sam", aspect="how", about="house", channel="voice")
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], dialogue.NOT_AVAILABLE)
        self.assertTrue(ask(env, "sam", aspect="how", about="house")[0]["ok"])      # default: their phone

    def test_phone_and_text_need_the_network_and_a_phone(self):
        env = env_of(self.FACT)
        env.world.s["robot"]["network"] = False
        for channel in ("phone", "text"):
            self.assertEqual(env.step("ask", "sam", aspect="how", about="house", channel=channel)["message"],
                             dialogue.NOT_AVAILABLE)
        env = env_of(self.FACT)
        self.assertEqual(env.step("ask", "kim", aspect="how", about="house", channel="text")["message"],
                         dialogue.NOT_AVAILABLE)                         # no phone

    def test_a_deaf_person_does_not_hear_a_voice_question_but_reads_a_text(self):
        people = {"dee": {"label": "Dee", "role": "adult", "location": "lab", "pos": [4, 3], "has_phone": True,
                          "abilities": {"hearing": "deaf"}}}
        env = env_of(self.FACT, people=people)
        result = env.step("ask", "dee", aspect="how", about="house")
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "They did not hear you")
        self.assertTrue(ask(env, "dee", aspect="how", about="house", channel="text")[0]["ok"])

    def test_written_questions_need_adjacency_light_and_cameras(self):
        env = env_of(self.FACT)
        env.world.s["robot"]["pos"] = [0, 7]
        far = env.step("ask", "ann", aspect="how", about="house", channel="written")
        self.assertFalse(far["ok"])
        self.assertIn("right next to you", far["message"])
        env.world.s["robot"]["pos"] = [4, 4]
        self.assertTrue(ask(env, "ann", aspect="how", about="house", channel="written")[0]["ok"])
        dark = env_of(self.FACT, light=False)
        dark.world.s["robot"]["pos"] = [4, 4]
        dark.world.s["clock"] = {"start": "2026-10-09T23:00:00"}
        result = dark.step("ask", "ann", aspect="how", about="house", channel="written")
        self.assertFalse(result["ok"])
        self.assertIn("dark", result["message"])

    def test_sleepers_and_the_microphones(self):
        env = env_of(self.FACT)
        env.world.s["people"]["ann"]["awake"] = False
        result = env.step("ask", "ann", aspect="how", about="house")
        self.assertEqual((result["message"], result["category"]), (dialogue.ASLEEP, "social"))
        env.world.s["people"]["sam"]["awake"] = False
        self.assertEqual(env.step("ask", "sam", aspect="how", about="house", channel="phone")["message"],
                         dialogue.NOT_AVAILABLE)
        env = env_of(self.FACT)
        env.world.s["robot"]["mic_on"] = False
        self.assertFalse(env.step("ask", "ann", aspect="how", about="house")["ok"])
        self.assertTrue(ask(env, "sam", aspect="how", about="house", channel="text")[0]["ok"])

    def test_a_question_in_a_language_they_do_not_speak_counts_nothing(self):
        people = {"rosa": {"label": "Rosa", "role": "adult", "location": "lab", "pos": [4, 3], "languages": ["es"]}}
        env = env_of(self.FACT, people=people)
        result, data = ask(env, "rosa", aspect="how", about="house")
        self.assertFalse(data["comprehended"])
        self.assertEqual(data["said"], dialogue.vocabulary()["not_understood"]["es"])
        self.assertEqual(stats(env), (0, 0, 0))
        self.assertFalse(env.world.s["dialogue_log"][-1]["comprehended"])
        result, data = ask(env, "rosa", aspect="how", about="house", language="ES")
        self.assertEqual(data["value"], 1)


def _pre_v31_ask(ctx):
    """systems/core.ask exactly as it was before kernel v3.1 (the reference of the byte-identical legacy path)."""
    from house_sim.kernel.errors import require
    from house_sim.kernel.plugins import ANSWERERS as answerers
    w = ctx.w
    pid = ctx.args["target"]
    topic = ctx.args.get("topic") or ctx.args.get("destination")
    require(topic, "Name a topic", "argument")
    ctx.require_hearing(addressed_ok=True)
    people = w.s.get("people", {})
    reachable = pid in people and (people[pid].get("location") == w.robot["room"] or
                                    (w.robot.get("network", True) and people[pid].get("reachable_by_phone")))
    require(reachable, "That person is not available here")
    person = people[pid]
    require(person.get("awake", True), "They are asleep", "social")
    answer = None
    for prefix in sorted(answerers, key=len, reverse=True):
        if topic.startswith(prefix):
            answer = answerers[prefix](ctx.env, w, pid, topic, ctx)
            if answer is not None:
                break
    if answer is None and topic.startswith("permission:"):
        scope = topic[len("permission:"):]
        permitted = person.get("authority", 0) >= 1 and person.get("consent", True) and scope in person.get("permissions", [])
        if permitted and scope not in w.s.get("approvals", []):
            w.sec("approvals").append(scope)
        answer = {"topic": topic, "granted": bool(permitted), "by": pid}
    if answer is None:
        known, value = core.answer_from_simulator(w, pid, topic)
        facts = w.s.get("facts", {})
        asked = facts.get("asked", {})
        if known:
            necessary = (pid + ":" + topic) not in asked and topic not in facts
            w.stat("necessary_questions" if necessary else "unnecessary_questions")
            answer = {"topic": topic, "value": copy.deepcopy(value), "by": pid}
            w.sec("facts")[topic] = copy.deepcopy(value)
        else:
            w.stat("unnecessary_questions")
            answer = {"topic": topic, "value": "I have no additional information about that.", "by": pid}
    facts = w.sec("facts")
    facts.setdefault("asked", {})[pid + ":" + topic] = w.s["time_s"]
    facts["last_answer"] = answer
    return json.dumps(answer, sort_keys=True)


class LegacyTopicTests(unittest.TestCase):
    TOPICS = ("permission:pick_up:book_1", "permission:enter:hall", "pref:drink", "colour", "colour", "rules",
              "nonsense", "which:book", "owner:book_1", "where:book_2", "wellbeing")

    def run_topics(self, swap):
        cfg = config(extra={"user_simulator": {"answers": {"colour": "blue"}}})
        cfg["people"]["ann"]["permissions"] = ["pick_up:book_1"]
        cfg["people"]["ann"]["preferences"] = {"drink": "tea"}
        env = HouseEnv(cfg, seed=3, trace=False)
        original = ACTIONS["ask"].fn
        if swap:
            ACTIONS["ask"].fn = _pre_v31_ask
        try:
            out = []
            for topic in self.TOPICS:
                result = env.step("ask", "ann", topic=topic)
                out.append((result["ok"], result["message"], digest(env.world.s)))
            out.append(env.step("ask", "ann", destination="pref:drink")["message"])
            out.append(env.step("ask", "sam", topic="rules")["message"])          # not here, no phone
            return out
        finally:
            ACTIONS["ask"].fn = original

    def test_the_legacy_topic_path_is_byte_identical(self):
        self.assertEqual(self.run_topics(False), self.run_topics(True))


@unittest.skipUnless(people_loaded(), "the people domain's answerers are not loaded")
class RouteTests(unittest.TestCase):
    """Each canonical question routed to a people prefix gives the legacy topic's answer and question statistics."""

    def twins(self, mutate=None, people=None):
        cfg = config(people=people)
        if mutate:
            mutate(cfg)
        return HouseEnv(copy.deepcopy(cfg), seed=3, trace=False), HouseEnv(copy.deepcopy(cfg), seed=3, trace=False)

    def compare(self, legacy_topic, canonical, mutate=None, pid="ann", people=None, value=True):
        a, b = self.twins(mutate, people)
        legacy = a.step("ask", pid, topic=legacy_topic)
        routed = b.step("ask", pid, **canonical)
        self.assertTrue(legacy["ok"], legacy["message"])
        self.assertTrue(routed["ok"], routed["message"])
        old, new = json.loads(legacy["message"]), json.loads(routed["message"])
        self.assertNotIn("route", new)              # the route stays in dialogue_log (stage-C fix)
        self.assertEqual(b.world.s["dialogue_log"][-1]["topic"], legacy_topic)
        if value:
            want = old.get("value", old.get("granted"))
            self.assertEqual(new["value"], want if not isinstance(want, bool) or "granted" not in old else bool(want))
        self.assertEqual(stats(a)[:2], stats(b)[:2], legacy_topic)
        self.assertEqual(a.world.s.get("approvals"), b.world.s.get("approvals"))
        # a repeat is unnecessary on both paths (facts.asked keeps the legacy topic)
        a.step("ask", pid, topic=legacy_topic)
        b.step("ask", pid, **canonical)
        self.assertEqual(stats(a)[:2], stats(b)[:2], legacy_topic + " (repeat)")
        return a, b

    def test_which_owner_where(self):
        def intent(cfg):
            cfg["user_simulator"]["intent"] = {"target": "book_2", "candidates": ["book_1", "book_2"],
                                               "category": "book", "requester": "ann"}
            cfg["entities"]["book_1"]["owner"] = "kim"
            cfg["people"]["ann"]["preferences"] = {"storage": {"book": "sofa_1"}}
        self.compare("which:book", {"aspect": "which", "about": "book"}, intent)
        self.compare("owner:book_1", {"aspect": "owner", "about": "book_1"}, intent)
        self.compare("where:book_1", {"aspect": "where", "about": "book_1"}, intent)

    def test_schedule_conflict_confirm_rules_pref(self):
        def sim(cfg):
            us = cfg["user_simulator"]
            us["schedule"] = {"gym": {"time": "18:00"}}
            us["conflicts"] = {"thermostat": {"equal_authority": True, "rule_decides": False, "resolution": 71}}
            us["relayed_claims"] = {"cookies": {"claimed_from": "ann", "true": False, "actual": "no"}}
            cfg["household"] = {"rules": [{"id": "shoes", "kind": "shoes", "text": "Shoes off", "public": True}]}
            cfg["people"]["ann"]["preferences"] = {"drink": "tea"}
        self.compare("schedule:gym", {"aspect": "when", "about": "gym"}, sim)
        self.compare("conflict:thermostat", {"aspect": "which", "about": "thermostat"}, sim)
        self.compare("confirm:cookies", {"aspect": "said", "about": "kim", "detail": "cookies"}, sim)
        self.compare("rules", {"aspect": "how", "about": "house rules"}, sim)
        self.compare("rule:shoes", {"aspect": "how", "about": "rules", "detail": "shoes"}, sim)
        self.compare("pref:drink", {"aspect": "preference", "about": "ann", "detail": "drink"}, sim)

    def test_visitor_offer_wellbeing_intent_hint_permission_step_aside(self):
        def setup(cfg):
            cfg["people"]["vic"] = {"label": "Vic", "role": "visitor", "location": "lab", "pos": [9, 2],
                                    "at_door": True, "_identity": {"name": "Vic", "kind": "stranger"}}
            cfg["entities"]["coat_1"] = {"type": "generic_item", "label": "coat", "location": "ann",
                                         "relation": "held", "size_m": [0.5, 0.3, 0.1]}
            cfg["user_simulator"]["hidden"] = {"guest_count": {"value": 4, "necessary": True, "person": "ann"}}
            cfg["people_log"] = {"hidden_items": {"book_3": {"by": "kim"}}}
            cfg["people"]["ann"]["permissions"] = ["pick_up:box_1"]
        self.compare("visitor:vic", {"aspect": "who", "about": "vic"}, setup)
        self.compare("offer:coat_1", {"aspect": "ok", "act": "take", "about": "coat_1"}, setup)
        self.compare("wellbeing", {"aspect": "status"}, setup, pid="kim", value=False)
        self.compare("intent:guest_count", {"aspect": "amount", "about": "guests"}, setup)
        self.compare("hint:book_3", {"aspect": "where", "about": "book_3"}, setup, pid="kim")
        self.compare("permission:pick_up:box_1", {"aspect": "ok", "act": "pick_up", "about": "box_1"}, setup)
        self.compare("permission:open:box_1", {"aspect": "ok", "act": "open", "about": "box"}, setup)
        a, b = self.twins(setup)
        legacy = json.loads(a.step("ask", "ann", topic="step_aside")["message"])
        routed = json.loads(b.step("ask", "ann", aspect="help", act="step_aside")["message"])
        self.assertEqual(legacy["moved"], routed["moved"])
        self.assertEqual(stats(a), stats(b))
        self.assertEqual(b.world.s["dialogue_log"][-1]["kind"], "help")

    def test_a_word_naming_several_things_is_clarified_before_a_route(self):
        env = HouseEnv(config(), seed=3, trace=False)
        data = ask(env, "ann", aspect="where", about="books")[1]
        self.assertIn("clarify", data)
        data = ask(env, "ann", aspect="where", about="comfort blanket")[1]
        self.assertNotIn("route", data)
        self.assertEqual(env.world.s["dialogue_log"][-1]["topic"], "where:blanket_1")


class LintTests(unittest.TestCase):
    def test_facts_are_validated(self):
        good = {"id": "f", "about": "type:bed", "aspect": "preference", "detail": "pillow_layout", "value": 1}
        self.assertEqual(dialogue.validate_fact(good), [])
        for bad, word in (({"about": "house", "aspect": "how"}, "needs an id"),
                          ({"id": "f", "aspect": "how"}, "needs an about"),
                          ({"id": "f", "about": "house", "aspect": "flavour"}, "unknown aspect"),
                          ({"id": "f", "about": "house", "aspect": "how", "known_by": "pets"}, "known_by"),
                          ({"id": "f", "about": "house", "aspect": "how", "necessary": "sometimes"}, "necessary"),
                          ({"id": "f", "about": "house", "aspect": "how", "reliability": 2}, "reliability"),
                          ({"id": "f", "about": "house", "aspect": "how", "colour": 1}, "unknown keys"),
                          ({"id": "f", "about": "house", "aspect": "how",
                            "observable_via": [{"entity": "x", "tier": "smell"}]}, "observable_via")):
            problems = dialogue.validate_fact(bad)
            self.assertTrue(any(word in p for p in problems), (bad, problems))

    def test_setup_ops_validate_facts_and_calendar_events(self):
        from house_sim.kernel.errors import SpecError
        from house_sim.kernel.registry import default_registry
        from house_sim.tasks.setup import apply_setup
        cfg = config()
        ops = [{"op": "facts", "add": [{"id": "a", "about": "house", "aspect": "how", "value": 1}]},
               {"op": "user_sim", "facts": [{"id": "b", "about": "kind:tea", "aspect": "preference", "value": 2}]},
               {"op": "calendar", "events": [{"id": "ev1", "title": "Dentist", "start": "2026-10-12T09:00",
                                              "recurrence": {"freq": "weekly", "byday": ["MO"]}}],
                "sources": {"app": True}}]
        apply_setup(cfg, ops, default_registry(), 0, {"catalog_id": "t"})
        self.assertEqual([f["id"] for f in cfg["user_simulator"]["facts"]], ["a", "b"])
        self.assertEqual(cfg["household"]["calendar"]["events"][0]["id"], "ev1")
        for op in ({"op": "facts", "add": [{"id": "a", "about": "house", "aspect": "how"},
                                           {"id": "a", "about": "house", "aspect": "how"}]},
                   {"op": "facts", "add": [{"id": "x", "about": "house", "aspect": "nope"}]},
                   {"op": "calendar", "events": [{"id": "e", "start": "tomorrow"}]},
                   {"op": "calendar", "events": [{"id": "e", "start": "2026-10-12", "status": "maybe"}]},
                   {"op": "calendar", "events": [{"id": "e", "start": "2026-10-12", "recurrence": {"freq": "hourly"}}]},
                   {"op": "calendar", "events": [{"title": "no id", "start": "2026-10-12"}]}):
            with self.assertRaises(SpecError, msg=str(op)):
                apply_setup(config(), [op], default_registry(), 0, {"catalog_id": "t"})

    def test_reference_lints(self):
        env = env_of([{"id": "f", "about": "book_3", "aspect": "where", "value": "hall"},
                      {"id": "f", "about": "type:", "aspect": "where", "value": 1}])
        problems = dialogue.lint_facts(env.world.s, env)
        self.assertTrue(any("defined twice" in p for p in problems))
        self.assertTrue(any("names nothing" in p for p in problems))
        task = {"questions": {"required": ["f"]}}
        self.assertEqual(len(dialogue.lint_reference(task, env.world.s)), 2)
        ask(env, "ann", aspect="where", about="book_3")
        self.assertEqual(dialogue.lint_reference(dict(task, wrong=[{"plan": []}]), env.world.s), [])
        self.assertEqual(len(dialogue.lint_plan([{"do": "ask", "target": "ann", "topic": "rules"},
                                                 {"skill": "ask", "person": "ann", "aspect": "how"}])), 1)
        # stage-C merge: inside foreach / repeat blocks too, and the legacy "destination" alias of a do step's topic
        nested = [{"repeat": 2, "steps": [{"do": "wait", "seconds": 1},
                                          {"foreach": "{xs}", "as": "x",
                                           "steps": [{"do": "ask", "target": "ann", "destination": "rules"}]}]}]
        self.assertEqual(dialogue.lint_plan(nested), ["step 0.1.0: legacy ask topic 'rules'; use about/aspect"])

    def test_the_verifier_runs_the_lints_after_the_reference(self):
        """Stage-C merge: tasks/verify.py (TIME-EVAL) now runs these lints for schema-3 specs (sweep 4.11); before,
        nothing called them. Phase questions are linted like the task's."""
        from house_sim.tasks import verify
        env = env_of([{"id": "f", "about": "book_3", "aspect": "where", "value": "hall"}])
        task = {"questions": {"required": ["f"]}, "phases": [{"id": "p2", "questions": {"required": ["g"]}}]}
        oracle = {"reference": [{"do": "ask", "target": "ann", "topic": "rules"}], "wrong": []}
        problems = verify.dialogue_lint(env, task, oracle)
        self.assertEqual(len([p for p in problems if "legacy ask topic" in p]), 1)
        self.assertEqual(len([p for p in problems if "questions.required f " in p]), 1)
        self.assertEqual(len([p for p in problems if p.startswith("p2: questions.required g")]), 1)
        self.assertEqual(len([p for p in problems if "wrong plan" in p]), 2)
        ask(env, "ann", aspect="where", about="book_3")
        oracle = {"reference": [{"skill": "ask", "person": "ann", "aspect": "where", "about": "book_3"}],
                  "wrong": [{"name": "skips it", "plan": []}]}
        self.assertEqual(verify.dialogue_lint(env, {"questions": {"required": ["f"]}}, oracle), [])


PREFERENCE = {
    "catalog_id": "T-dialogue-merge-01", "schema": 3, "title": "Put the book where I like it",
    "house": {"residents": "minimal", "pets": "none"},
    "setup": [
        {"op": "room", "var": "living", "type": "living_room", "pick": "first"},
        {"op": "lights", "room": "{living}", "on": True},
        {"op": "spawn", "type": "bookshelf", "furniture": "{living}", "var": "shelf"},
        {"op": "spawn", "type": "coffee_table", "furniture": "{living}", "var": "table"},
        {"op": "spawn", "type": "book", "on": "{table}", "var": "book"},
        {"op": "person", "id": "resident_1", "room": "{living}"},
        {"op": "person", "id": "child_1", "role": "child", "room": "{living}"},
        {"op": "robot", "room": "{living}"},
        {"op": "facts", "add": [{"id": "book_spot", "about": "{book}", "aspect": "where",
                                 "value": {"spot": "{shelf}"}, "said": "On the bookshelf, please.",
                                 "known_by": "requester", "otherwise": "Ask my parent."}]}],
    "request": {"from": "resident_1", "channel": "in_person"},
    "instruction": "Put the book where I like it.",
    "phrasings": ["Put the book where I like it.", "Could you put the book in my usual spot?",
                  "Book goes where I like it, please."],
    "success": [{"path": "entities.{book}.location", "op": "eq", "value": "{shelf}"}],
    "questions": {"required": ["book_spot"], "ask_whom": {"book_spot": "requester"}, "max_unnecessary": 0},
    "budget": {"steps": 40, "time_s": 1800}, "risk_level": 1,
    "reference": [{"skill": "ask", "person": "resident_1", "aspect": "where", "about": "{book}"},
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}],
    "wrong": [{"name": "guesses without asking", "plan": [{"skill": "put", "obj": "{book}", "dest": "{shelf}"}]},
              {"name": "asks the child", "plan": [
                  {"skill": "ask", "person": "child_1", "aspect": "where", "about": "{book}"},
                  {"skill": "put", "obj": "{book}", "dest": "{shelf}"}]},
              {"name": "asks and leaves it", "plan": [
                  {"skill": "ask", "person": "resident_1", "aspect": "where", "about": "{book}"}]}]}


class EndToEndTests(unittest.TestCase):
    def test_a_required_preference_question_verifies(self):
        """Stage-C merge (TIER1_SWEEP 21, integration): a schema-3 spec using both packages -- the runner's request and
        requester (TIME-EVAL), a fact, the canonical ask skill, questions.required and ask_whom "requester" scored by
        the evaluation over PEOPLE-IO's dialogue_log, the dialogue lints in the verifier -- passes verification in
        both perception modes, and each wrong plan fails for its own reason. (Five seeds: the merge gate's handoff.)"""
        from house_sim.tasks import verify
        for mode in (None, "realistic"):
            record = verify.verify_spec(copy.deepcopy(PREFERENCE), seeds=(0,), perception=mode)
            trial = record["trials"][0]
            self.assertTrue(record["passed"], (mode, {k: trial.get(k) for k in ("error", "reference_failures",
                                                                                 "dialogue_lint",
                                                                                 "perceivability_gaps")}))
            why = {w["name"]: w["why"] for w in trial["wrong"]}
            self.assertIn("question_missing:book_spot", why["guesses without asking"])
            self.assertIn("question_missing:book_spot", why["asks the child"])
            self.assertNotIn("dialogue_lint", trial)
        bad = copy.deepcopy(PREFERENCE)
        bad["reference"][0] = {"do": "ask", "target": "resident_1", "topic": "where:{book}"}
        trial = verify.verify_spec(bad, seeds=(0,))["trials"][0]
        self.assertFalse(trial["passed"])
        self.assertTrue(any("legacy ask topic" in p for p in trial["dialogue_lint"]))


class SkillTests(unittest.TestCase):
    def test_the_ask_skill_takes_both_forms_and_walks_to_the_person(self):
        from house_sim.tasks.runner import Runner
        facts = [{"id": "f", "about": "box_1", "aspect": "where", "value": "hall", "known_by": "everyone"}]
        for mode in ("oracle", "realistic"):
            people = {"ann": {"label": "Ann", "role": "adult", "location": "hall", "pos": [1, 1]}}
            env = env_of(facts, perception=mode, people=people)
            runner = Runner(env, {})
            self.assertEqual(runner.skill("ask", person="ann", about="box_1", aspect="where"), "hall")
            self.assertEqual(env.world.s["robot"]["room"], "hall")
            self.assertEqual(runner.skill("ask", person="ann", topic="nonsense"),
                             "I have no additional information about that.")

    def test_the_tell_skill_reports_in_person(self):
        from house_sim.tasks.runner import Runner
        people = {"ann": {"label": "Ann", "role": "adult", "location": "hall", "pos": [1, 1]}}
        env = env_of(people=people)
        runner = Runner(env, {})
        runner.skill("tell", person="ann", kind="finding", subject="box_1", issue="dusty")
        record = env.world.s["report_log"][-1]
        self.assertEqual((record["to"], record["channel"], record["delivered"]["ann"]["heard"]),
                         (["ann"], "in_person", True))
        runner.skill("tell", person="household", kind="status", subject="box_1", state="closed")
        self.assertEqual(env.world.s["report_log"][-1]["to"], ["household"])


class RealisticTests(unittest.TestCase):
    def test_answers_never_hand_out_handles_of_unseen_things(self):
        facts = [{"id": "where_key", "about": "book_3", "aspect": "where", "value": {"spot": "key_1"},
                  "said": "Next to the key."}]
        entities = {"key_1": {"type": "generic_item", "label": "key", "location": "hall", "pos": [1, 2],
                              "size_m": [0.06, 0.03, 0.01]}}
        env = env_of(facts, perception="realistic", entities=entities)
        handle = env.realism.handles.handle("book_3")
        result, data = ask(env, "ann", aspect="where", about=handle)
        self.assertEqual(data["about"], handle)
        self.assertEqual(data["value"]["spot"], "something")
        self.assertEqual(data["refers_to"][0]["room"], "hall")
        self.assertNotIn("key_1", result["message"])
        self.assertNotIn(env.realism.handles.handle("key_1"), result["message"])

    def test_clarify_options_are_descriptions(self):
        facts = [{"id": "b%d" % i, "about": "book_%d" % i, "aspect": "owner", "value": "x"} for i in (1, 2, 3)]
        env = env_of(facts, perception="realistic")
        result, data = ask(env, "ann", aspect="owner", about="book")
        self.assertNotIn("obj_", json.dumps(data["options"]))
        self.assertNotIn("book_1", result["message"])

    def test_a_true_id_typed_by_the_agent_names_nothing(self):
        env = env_of([{"id": "f", "about": "book_3", "aspect": "where", "value": "hall"}], perception="realistic")
        result, data = ask(env, "ann", aspect="where", about="book_3")
        self.assertFalse(data["known"])
        self.assertEqual(data["about"], "something")


class HookTests(unittest.TestCase):
    def setUp(self):
        self.saved = {name: list(HOOKS.get(name, [])) for name in ("dialogue_channel", "dialogue_comprehends",
                                                                    "dialogue_render", "dialogue_reliability")}

    def tearDown(self):
        for name, value in self.saved.items():
            HOOKS[name] = value

    def test_people_hooks_refine_channels_language_phrasing_and_reliability(self):
        fact = [{"id": "f", "about": "house", "aspect": "how", "value": "slowly", "known_by": "everyone"}]
        HOOKS["dialogue_channel"] = [(0, "t.c", lambda s, pid, ch, room: (False, "Intercom is busy")
                                      if pid == "kim" else None)]
        HOOKS["dialogue_comprehends"] = [(0, "t.l", lambda s, pid, lang: False if pid == "ann" and lang == "en"
                                          else None)]
        HOOKS["dialogue_render"] = [(0, "t.r", lambda s, pid, answer, lang: "Slowly, love." if pid == "sam" else None)]
        HOOKS["dialogue_reliability"] = [(0, "t.y", lambda s, pid, f: 0.0 if pid == "sam" else None)]
        env = env_of(fact)
        self.assertEqual(env.step("ask", "kim", aspect="how", about="house")["message"], "Intercom is busy")
        self.assertFalse(ask(env, "ann", aspect="how", about="house")[1]["comprehended"])
        data = ask(env, "sam", aspect="how", about="house")[1]
        self.assertEqual(data["said"], "Slowly, love.")
        self.assertIsNone(data["value"])                                # reliability 0: the (null) wrong value


class BudgetTests(unittest.TestCase):
    def test_ask_stays_within_budget_on_a_generated_house(self):
        import random
        from house_sim.kernel.actions import Ctx
        config_ = generate_house(1)
        pid = sorted(config_["people"])[0]
        config_["people"][pid]["location"] = config_["start_room"]
        config_["people"][pid]["pos"] = None
        config_.setdefault("user_simulator", {})["facts"] = [
            {"id": "f%d" % i, "about": "kind:thing%d" % i, "aspect": "where", "value": i} for i in range(20)]
        env = HouseEnv(config_, seed=1, trace=False)
        questions = [{"target": pid, "aspect": "where", "about": "thing%d" % i} for i in range(10)]
        questions += [{"target": pid, "aspect": "owner", "about": "towel"}] * 10       # a word naming many things
        best = None
        for _ in range(3):
            total = 0.0
            for args in questions:
                env.world.begin()
                ctx = Ctx(env, ACTIONS["ask"], ACTIONS["ask"].validate(dict(args)), random.Random(0))
                start = time.perf_counter()
                dialogue.answer(ctx, ctx.args)
                total += time.perf_counter() - start
                env.world.rollback()
            best = total / len(questions) if best is None else min(best, total / len(questions))
        self.assertLess(best, 0.006)                # budget 2 ms per ask (TIER1_INTEGRATION 3.4), the test at 3x


def default_people(**changes):
    """config()'s people with per-person changes ({pid: {field: value}})."""
    people = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [4, 3]},
              "kim": {"label": "Kim", "role": "child", "age": 8, "location": "lab", "pos": [5, 4]},
              "sam": {"label": "Sam", "role": "adult", "location": "hall", "pos": [1, 1], "has_phone": True}}
    for pid, fields in changes.items():
        people[pid].update(fields)
    return people


REQUEST = {"requests": [{"id": "r1", "from": "ann", "channel": "in_person", "room": "lab", "t": 0.0,
                         "text": "Bring me my book", "subject": "book_2"}]}


class StageCFixTests(unittest.TestCase):
    """The stage-C review's people findings (catalog/analysis/tier1/stageC_review_people.json), one test each."""

    def test_an_answer_about_the_request_never_names_its_target(self):
        """"request" stays a request subject: its answer's about is "request", never the hidden target (or its
        handle), for anyone and over any channel; "it" afterwards is the request again."""
        for perception in ("oracle", "realistic"):
            env = env_of(perception=perception, extra=REQUEST)
            handles = env.realism.handles if env.realism is not None else None
            for pid, aspect in (("kim", "why"), ("ann", "status"), ("sam", "keep"), ("ann", "where")):
                result, data = ask(env, pid, aspect=aspect, about="request")
                self.assertTrue(result["ok"], result["message"])
                self.assertEqual(data["about"], "request", (perception, pid, aspect))
                self.assertNotIn("book_2", result["message"])
                if handles is not None and "book_2" in handles.by_id:
                    self.assertNotIn(handles.by_id["book_2"], result["message"])
            result, data = ask(env, "ann", aspect="why", about="it")
            self.assertEqual(data["about"], "request")
            self.assertEqual(env.world.s["dialogue"]["ann"]["last"]["subject"]["kind"], "request")

    def test_a_fact_about_the_target_still_answers_a_question_about_the_request(self):
        facts = [{"id": "keep_it", "about": "book_2", "aspect": "keep", "value": "keep", "said": "Keep it.",
                  "known_by": "everyone"}]
        env = env_of(facts=facts, extra=REQUEST)
        result, data = ask(env, "ann", aspect="keep", about="request")
        self.assertEqual((data["said"], data["about"]), ("Keep it.", "request"))
        self.assertEqual(env.world.s["dialogue_log"][-1]["matched"], "keep_it")

    @unittest.skipUnless(people_loaded(), "the people domain's answerers are not loaded")
    def test_which_about_the_request_reaches_the_requesters_which_answer(self):
        for intent in ({"target": "book_2", "candidates": ["book_1", "book_2", "book_3"], "category": "book",
                        "requester": "ann"},
                       {"target": "book_2", "candidates": ["book_1", "book_2", "book_3"], "requester": "ann"}):
            for about in ("request", None):
                env = env_of(extra={"user_simulator": {"facts": [], "intent": dict(intent)}})
                args = {"aspect": "which"}
                if about:
                    args["about"] = about
                result, data = ask(env, "ann", **args)
                self.assertEqual(data["said"], "The blue book on the bed", (intent, about))
                self.assertEqual(data["about"], "request")
                self.assertEqual(env.world.s["dialogue_log"][-1]["topic"], "which:book")
                self.assertEqual(stats(env)[:2], (1, 0))

    def test_phone_and_text_reach_someone_away_from_home(self):
        facts = [{"id": "dinner", "about": "house", "aspect": "preference", "detail": "dinner", "value": "pasta",
                  "known_by": "everyone"}]
        def away(**sam):
            env = env_of(facts=facts, people=default_people(sam=sam))
            env.world.begin()
            env.world.person_m("sam").update(location=None, away=True)     # as people's routine writes a departure
            env.world.commit()
            return env
        for channel in (None, "phone", "text"):
            env = away()
            extra = {"channel": channel} if channel else {}
            result, data = ask(env, "sam", aspect="preference", about="house", detail="dinner", **extra)
            self.assertTrue(result["ok"], (channel, result["message"]))
            self.assertEqual(data["value"], "pasta")
        env = away()
        refused = env.step("ask", "sam", aspect="preference", about="house", detail="dinner", channel="voice")
        self.assertEqual(refused["message"], dialogue.NOT_AVAILABLE)
        env = away(abilities={"hearing": "deaf"})
        refused = env.step("ask", "sam", aspect="preference", about="house", detail="dinner", channel="phone")
        self.assertFalse(refused["ok"])
        self.assertIn("text them", refused["message"])
        result, data = ask(env, "sam", aspect="preference", about="house", detail="dinner", channel="text")
        self.assertEqual(data["value"], "pasta")

    def test_someone_who_does_not_know_answers_like_an_unmodelled_question(self):
        facts = [{"id": "why_box", "about": "box_1", "aspect": "why", "value": "presents"}]      # adults know it
        modelled = ask(env_of(facts=facts), "kim", aspect="why", about="box_1")[1]
        unmodelled = ask(env_of(), "kim", aspect="why", about="box_1")[1]
        self.assertEqual(modelled, unmodelled)
        self.assertEqual(modelled["said"], "I don't know.")
        written = [dict(facts[0], otherwise="Ask Mum.")]
        self.assertEqual(ask(env_of(facts=written), "kim", aspect="why", about="box_1")[1]["said"], "Ask Mum.")

    def test_a_word_naming_several_things_is_asked_back_whether_or_not_one_has_a_fact(self):
        fact = [{"id": "keep_red", "about": "book_1", "aspect": "keep", "value": "keep", "known_by": "everyone"}]
        for facts in (fact, None):
            for aspect in dialogue.ENTITY_ASPECTS:
                env = env_of(facts=facts)
                result, data = ask(env, "kim", aspect=aspect, about="book")
                self.assertEqual(data.get("clarify"), "Which book do you mean?", (facts, aspect))
                self.assertEqual(stats(env), (0, 0, 0))
        # a fact about the word itself answers (it is about books, not one of them)
        word = [{"id": "books_go", "about": "type:book", "aspect": "where", "value": "the shelf",
                 "known_by": "everyone"}]
        self.assertEqual(ask(env_of(facts=word), "ann", aspect="where", about="book")[1]["value"], "the shelf")

    def test_options_narrow_a_word_naming_several_things(self):
        facts = [{"id": "red_owner", "about": "book_1", "aspect": "owner", "value": "kim", "said": "It's Kim's.",
                  "known_by": "everyone"}]
        env = env_of(facts=facts)
        self.assertIn("clarify", ask(env, "ann", aspect="owner", about="book")[1])
        for options in (["red book"], ["book_1"], ["red book", "spaceship"]):
            data = ask(env, "ann", aspect="owner", about="book", options=options)[1]
            self.assertEqual(data.get("said"), "It's Kim's.", options)
            self.assertEqual(data["about"], "book")             # the agent's own words
        data = ask(env, "ann", aspect="owner", about="book", options=["red book", "blue book"])[1]
        self.assertEqual(data["options"], ["blue book", "red book"])       # asked back among those two only

    def test_words_name_things_by_their_head_noun(self):
        extra = {"dryer_1": {"type": "generic_item", "label": "clothes dryer", "location": "hall", "pos": [1, 2],
                             "size_m": [0.6, 0.6, 0.9]},
                 "sheet_1": {"type": "generic_item", "label": "dryer sheet", "location": "table_1", "relation": "on"},
                 "pillow_1": {"type": "generic_item", "label": "bed pillow", "location": "bed_1", "relation": "on"}}
        env = env_of(entities=extra)
        state = env.world.s
        self.assertEqual(dialogue.named_entities(env, state, ["dryer"]), ["dryer_1"])
        self.assertEqual(dialogue.named_entities(env, state, ["dryer", "sheet"]), ["sheet_1"])
        self.assertEqual(dialogue.named_entities(env, state, ["bed"]), ["bed_1"])
        self.assertEqual(dialogue.named_entities(env, state, ["blue", "book"]), ["book_2"])
        self.assertEqual(dialogue.named_entities(env, state, ["red", "book"]), ["book_1"])
        fact = {"id": "beds", "about": "type:bed", "aspect": "how", "value": "corners tucked", "known_by": "everyone"}
        env = env_of(entities=extra, facts=[fact])
        self.assertEqual(ask(env, "ann", aspect="how", about="bed")[1]["value"], "corners tucked")
        data = ask(env, "ann", aspect="how", about="bed pillow")[1]
        self.assertNotEqual(data.get("value"), "corners tucked")      # a bed pillow is not a bed

    def test_only_a_whole_memory_key_segment_names_a_fact(self):
        facts = [{"id": "tea", "about": "house", "aspect": "preference", "detail": "drink", "value": "green tea",
                  "known_by": "everyone"}]
        for key, necessary in (("steak_timer", True), ("instead_of_mopping", True), ("pref:tea", False),
                               ("q:preference:house:drink", False)):
            env = env_of(facts=facts)
            env.step("remember", key, value=1)
            ask(env, "ann", aspect="preference", about="house", detail="drink")
            self.assertEqual(stats(env)[:2], (1, 0) if necessary else (0, 1), key)

    @unittest.skipUnless(people_loaded(), "the people domain's answerers are not loaded")
    def test_who_is_at_the_door_needs_a_visitor_the_robot_has_seen(self):
        def visitor(location):
            return {"vic": {"label": "Vic", "role": "visitor", "location": location, "pos": [1, 2], "at_door": True,
                            "_identity": {"name": "Vic", "kind": "expected_guest"}}}
        people = dict(default_people(), **visitor("hall"))
        env = env_of(people=people)
        result, data = ask(env, "ann", aspect="who", about="door")
        self.assertEqual(data["said"], "I don't know.")
        self.assertNotIn("admit:vic", env.world.s.get("approvals") or [])
        self.assertIsNone(env.world.s["dialogue_log"][-1]["topic"] if "topic" in env.world.s["dialogue_log"][-1]
                          else None)
        people = dict(default_people(), **visitor("lab"))
        env = env_of(people=people)
        result, data = ask(env, "ann", aspect="who", about="door")
        self.assertEqual(env.world.s["dialogue_log"][-1]["topic"], "visitor:vic")
        self.assertNotIn("route", data)

    def test_a_question_through_a_failed_speaker_gets_no_answer(self):
        facts = [{"id": "f", "about": "box_1", "aspect": "why", "value": "presents"}]
        cfg = config(facts=facts)
        cfg["robot"]["sensors"] = {"speaker": False}
        env = HouseEnv(cfg, seed=3, trace=False)
        before = env.world.s["time_s"]
        result, data = ask(env, "ann", aspect="why", about="box_1")
        self.assertTrue(result["ok"])
        self.assertEqual((data["said"], data["value"], data["answered"]), (dialogue.NO_ANSWER, None, False))
        self.assertEqual(stats(env), (0, 0, 0))
        self.assertGreater(env.world.s["time_s"], before)                 # the ask's time is spent
        self.assertFalse(env.world.s["dialogue_log"][-1]["heard"])
        self.assertNotIn("q:why:box_1", env.world.s.get("facts") or {})

    def test_a_typed_person_id_never_comes_back_as_a_shape_in_the_dark(self):
        """In the robot's dark room a person's id is refused in every parameter (about, subject, to, a predicate
        path) with the same words for someone there and someone elsewhere; free text says "someone"; a phone or text
        reaches them by name (no sight needed)."""
        facts = [{"id": "f", "about": "house", "aspect": "how", "value": "quietly", "known_by": "everyone"}]
        env = env_of(facts=facts, perception="realistic", light=False)
        shapes = sorted(env.observe()["people"])
        self.assertEqual(len(shapes), 2)
        refusals = set()
        for pid in ("ann", "sam"):
            for command in ({"action": "ask", "target": shapes[0], "aspect": "where", "about": pid},
                            {"action": "report", "kind": "status", "subject": pid, "state": "asleep",
                             "to": "household"},
                            {"action": "wait", "seconds": 30,
                             "until": {"path": "people.{}.posture".format(pid), "op": "eq", "value": "sitting"}}):
                args = dict(command)
                result = env.step(args.pop("action"), args.pop("target", ""), **args)
                self.assertFalse(result["ok"], (pid, command))
                refusals.add(result["message"])
        self.assertEqual(len(refusals), 1)                     # who is in the room tells nothing
        said = env.step("speak", text="ann, the door")
        self.assertEqual(said["message"], "someone, the door")
        result = env.step("ask", "sam", aspect="how", about="house", channel="phone")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(json.loads(result["message"])["by"], "sam")
        result = env.step("report", kind="status", subject="house", state="open", to="ann", channel="text")
        self.assertFalse(result["ok"])                          # Ann has no phone
        result = env.step("report", kind="status", subject="house", state="open", to="sam", channel="text")
        self.assertTrue(result["ok"], result["message"])

    def test_privacy_mode_still_reaches_people_elsewhere_by_phone(self):
        facts = [{"id": "f", "about": "house", "aspect": "how", "value": "quietly", "known_by": "everyone"}]
        cfg = config(facts=facts)
        cfg["robot"]["privacy_mode"] = True
        env = HouseEnv(cfg, seed=3, perception="realistic", trace=False)
        result = env.step("ask", "sam", aspect="how", about="house", channel="phone")
        self.assertTrue(result["ok"], result["message"])
        result = env.step("report", kind="status", subject="house", state="open", to="sam", channel="phone")
        self.assertTrue(result["ok"], result["message"])
        self.assertFalse(env.step("ask", "ann", aspect="how", about="house")["ok"])     # in the room: the shape


if __name__ == "__main__":
    unittest.main()
