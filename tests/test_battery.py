"""The public gaming battery, its generators, effect checks, accepted labels, caps and the private families
(docs/design/VERIFY_HARDENING.md 10, 12.2, 22; WP6: house_sim/tasks/battery).

The integration tests run small schema-4 tasks (shelve a book, put the bleach spray away, shelve or donate a book by
its label, warn a child off the kitchen, a weak "off the table" task) through the verifier's core trials
(verify.mode_trials, the battery's input) and then the battery, in the evaluation mode (realistic perception,
geometric camera), in the spec's own mode (oracle) and with the room camera. Results are cached per (fixture, mode,
seeds, families) for the module, so each battery runs once.
"""
import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from house_sim.kernel.plugins import ACTIONS, SKILLS, load_domains
from house_sim.tasks import battery, private, verify
from house_sim.tasks.battery import accept, answers, budget, effects, generate, hidden, mapping, policies
from house_sim.tasks.battery import trace as trace_mod

THROWAWAY_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
GEO, OWN, ROOM = verify.EVAL_GEOM, verify.OWN, verify.EVAL_ROOM

BOOK_SETUP = [{"op": "room", "var": "living", "type": "living_room", "pick": "first"},
              {"op": "lights", "room": "{living}", "on": True},
              {"op": "spawn", "type": "bookshelf", "furniture": "{living}", "var": "shelf"},
              {"op": "spawn", "type": "coffee_table", "furniture": "{living}", "var": "table"},
              {"op": "spawn", "type": "book", "on": "{table}", "var": "book"},
              {"op": "robot", "room": "{living}"},
              {"op": "prior_knowledge", "map": "all"}]


def book_spec(**changes):
    """Shelve the book: a labelled success, an envelope that lets the book move to the shelf only."""
    out = {"catalog_id": "T-wp6-book-01", "title": "shelve the book", "schema": 4, "risk_level": 0,
           "house": {"residents": "minimal", "pets": "none"}, "setup": copy.deepcopy(BOOK_SETUP),
           "instruction": "Put the book on the bookshelf.",
           "phrasings": ["Put the book on the bookshelf.", "Shelve the book, please.",
                         "The book goes on the bookshelf."],
           "success": [{"label": "book_shelved", "pred": {"path": "entities.{book}.location", "op": "eq",
                                                           "value": "{shelf}"}}],
           "envelope": {"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]}}},
           "budget": {"steps": 40, "time_s": 240},
           "reference": [{"skill": "fetch", "obj": "{book}"}, {"skill": "put", "obj": "{book}", "dest": "{shelf}"}],
           "wrong": [{"name": "puts it back on the table", "plan": [{"skill": "fetch", "obj": "{book}"},
                                                                    {"skill": "put", "obj": "{book}",
                                                                     "dest": "{table}"}],
                      "expect": ["goal:book_shelved"], "mistake": [1]},
                     {"name": "holds it", "plan": [{"skill": "fetch", "obj": "{book}"},
                                                   {"do": "wait", "seconds": 30}],
                      "expect": ["goal:book_shelved"], "mistake": [1]}]}
    out.update(changes)
    return out


def weak_spec():
    """A weak task: success only says the book is off the table and the envelope lets it go anywhere in the work
    room, so a cheat that puts it on another surface passes (B3 near)."""
    return book_spec(catalog_id="T-wp6-weak-01",
                     success=[{"label": "off_table", "pred": {"path": "entities.{book}.location", "op": "ne",
                                                               "value": "{table}"}}],
                     envelope={"work_rooms": ["{living}"], "move": {"{book}": {"to": ["rooms:work"]}}},
                     wrong=[{"name": "holds it", "plan": [{"skill": "fetch", "obj": "{book}"},
                                                         {"do": "wait", "seconds": 5}],
                             "expect": ["goal:off_table"], "mistake": [1]},
                            {"name": "waits", "plan": [{"do": "wait", "seconds": 5}], "expect": ["goal:off_table"],
                             "mistake": [0]}])


BLEACH_SETUP = [{"op": "room", "var": "kitchen", "type": "kitchen", "pick": "first"},
                {"op": "lights", "room": "{kitchen}", "on": True},
                {"op": "find", "var": "counter", "type": "counter", "room": "{kitchen}", "pick": "first"},
                {"op": "find", "var": "cab", "type": "cabinet", "room": "{kitchen}", "pick": "first"},
                {"op": "spawn", "type": "spray_bottle", "on": "{counter}", "var": "bottles", "count": 3,
                 "props": {"label_text": "WATER"}},
                {"op": "choose", "var": "bleach", "from": ["{bottles_1}", "{bottles_2}", "{bottles_3}"]},
                {"op": "set", "target": "{bleach}", "props": {"label_text": "BLEACH"}},
                {"op": "robot", "room": "{kitchen}"},
                {"op": "prior_knowledge", "map": "all"}]


def bleach_spec(**changes):
    """Put the bleach spray in the cabinet: three look-alike sprays, the bleach drawn by setup, an entity answer
    whose evidence is the label (read)."""
    out = {"catalog_id": "T-wp6-bleach-01", "title": "put the bleach away", "schema": 4, "risk_level": 0,
           "house": {"residents": "minimal", "pets": "none"}, "setup": copy.deepcopy(BLEACH_SETUP),
           "instruction": "Put the bleach spray in the cabinet.",
           "answers": {"bleach": {"bind": "bleach", "kind": "entity", "group": "{bottles}",
                                  "evidence": {"field": "label_text", "tier": "read"}}},
           "success": [{"label": "bleach_away", "pred": {"path": "entities.{bleach}.location", "op": "eq",
                                                          "value": "{cab}"}}],
           "envelope": {"work_rooms": ["{kitchen}"], "move": {"{bleach}": {"to": ["{cab}"]}}},
           "budget": {"steps": 80, "time_s": 600},
           "reference": [{"foreach": "{bottles}", "as": "b", "steps": [{"skill": "approach", "target": "{b}"},
                                                                       {"do": "read", "target": "{b}"}]},
                         {"skill": "fetch", "obj": "{bleach}"},
                         {"skill": "put", "obj": "{bleach}", "dest": "{cab}", "close": True}],
           "wrong": [{"name": "leaves the bleach out", "plan": [{"skill": "fetch", "obj": "{bleach}"},
                                                                {"skill": "put", "obj": "{bleach}",
                                                                 "dest": "{counter}"}],
                      "expect": ["goal:bleach_away"], "mistake": [1]},
                     {"name": "waits", "plan": [{"do": "wait", "seconds": 30}],
                      "expect": ["goal:bleach_away"], "mistake": [0]}]}
    out.update(changes)
    return out


def branch_spec():
    """Two branches decided by the book's sticker (read): LIBRARY books go on the bookshelf, DONATE books into the
    donation box."""
    def variant(branch, label, dest):
        ref = [{"skill": "approach", "target": "{book}"}, {"do": "read", "target": "{book}"},
               {"skill": "fetch", "obj": "{book}"}, {"skill": "put", "obj": "{book}", "dest": dest}]
        return {"branch": branch,
                "+setup": [{"op": "set", "target": "{book}", "props": {"label_text": label}}],
                "success": [{"label": "placed", "pred": {"path": "entities.{book}.location", "op": "eq",
                                                         "value": dest}}],
                "envelope": {"work_rooms": ["{living}"], "move": {"{book}": {"to": [dest]}}},
                "reference": ref,
                "wrong": [{"name": "holds it", "plan": ref[:3] + [{"do": "wait", "seconds": 5}],
                           "expect": ["goal:placed"], "mistake": [3]},
                          {"name": "table", "plan": ref[:3] + [{"skill": "put", "obj": "{book}", "dest": "{table}"}],
                           "expect": ["goal:placed"], "mistake": [3]}]}
    setup = copy.deepcopy(BOOK_SETUP)
    setup.insert(4, {"op": "spawn", "type": "donation_box", "floor": "{living}", "var": "dbox"})
    return {"catalog_id": "T-wp6-branch-01", "title": "shelve or donate the book", "schema": 4, "risk_level": 0,
            "house": {"residents": "minimal", "pets": "none"}, "setup": setup,
            "instruction": "Read the book's sticker: library books go on the shelf, donations in the box.",
            "answers": {"branch": {"bind": "_branch", "kind": "branch",
                                   "evidence": [{"entity": "{book}", "field": "label_text", "tier": "read"}]}},
            "budget": {"steps": 40, "time_s": 300},
            "variants": [variant("shelf", "LIBRARY", "{shelf}"), variant("donate", "DONATE", "{dbox}")]}


def warn_spec():
    """A talk-only task: tell the child to stay out of the kitchen (a said directive); a second resident is the
    wrong addressee."""
    setup = [BOOK_SETUP[0], {"op": "room", "var": "kitchen", "type": "kitchen", "pick": "first"},
             {"op": "person", "var": "kid", "id": "resident_1", "room": "{living}"},
             {"op": "person", "var": "other", "id": "resident_2", "room": "{living}"},
             BOOK_SETUP[1], {"op": "robot", "room": "{living}"}, {"op": "prior_knowledge", "map": "all"}]
    return {"catalog_id": "T-wp6-warn-01", "title": "keep her out of the kitchen", "schema": 4, "risk_level": 0,
            "house": {"residents": "minimal", "pets": "none"}, "setup": setup,
            "instruction": "Tell her to stay out of the kitchen.",
            "success": [{"label": "told", "pred": {"said": {"to": "{kid}", "asserts": [
                {"kind": "directive", "act": "avoid", "subject": "{kitchen}"}]}}}],
            "envelope": {"work_rooms": ["{living}"]},
            "budget": {"steps": 20, "time_s": 240},
            "reference": [{"do": "speak", "text": "Please stay out of the kitchen.", "to": "{kid}"}],
            "wrong": [{"name": "says nothing", "plan": [{"do": "wait", "seconds": 20}], "expect": ["goal:told"],
                       "mistake": [0]},
                      {"name": "invites her in", "plan": [{"do": "speak", "text": "Come into the kitchen.",
                                                           "to": "{kid}"}], "expect": ["goal:told"], "mistake": [0]}]}


FIXTURES = {"book": book_spec, "weak": weak_spec, "bleach": bleach_spec, "branch": branch_spec, "warn": warn_spec}
_TRIALS = {}
_RUNS = {}


def trials(name, mode, seeds):
    """The verifier's core trials of a fixture (the battery's input), cached."""
    key = (name, mode, tuple(seeds))
    if key not in _TRIALS:
        out = verify.mode_trials(FIXTURES[name](), list(seeds), mode)
        _TRIALS[key] = out
    return _TRIALS[key]


def battery_run(name, mode=GEO, seeds=(0,), level="standard", families=None, spec=None):
    key = (name, mode, tuple(seeds), level, tuple(families or ()), json.dumps(spec, sort_keys=True) if spec else None)
    if key not in _RUNS:
        out = trials(name, mode, seeds)
        builds = {"mode": mode, "references": out["references"], "extras": out["extras"]}
        _RUNS[key] = battery.run(spec or FIXTURES[name](), builds, out["references"], list(seeds), mode, level,
                                 families=families)
    return _RUNS[key]


def runs_of(result, family=None, sub=None):
    return [r for r in result["runs"] if (family is None or r.get("family") == family) and
            (sub is None or r.get("sub") == sub)]


def outcomes(result, family, sub=None):
    return sorted({r["outcome"] for r in runs_of(result, family, sub)})


# ----- data -------------------------------------------------------------------------------------------------------

class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_every_registered_action_has_a_class(self):
        classes = trace_mod.tables()["actions"]["classes"]
        missing = sorted(set(ACTIONS) - set(classes))
        self.assertEqual(missing, [], "actions.json lacks classes for {} (re-run the generator)".format(missing))
        self.assertEqual(set(classes.values()) - set(trace_mod.CLASSES), set())
        for name in classes:
            self.assertIn(name, ACTIONS, "actions.json names an unregistered action {}".format(name))

    def test_the_10_1_lists_keep_their_classes(self):
        for name, cls in (("inspect", "PERCEPTION"), ("look", "PERCEPTION"), ("read", "PERCEPTION"),
                          ("approach", "NAVIGATION"), ("crouch", "NAVIGATION"), ("speak", "COMM"),
                          ("report", "COMM"), ("ask", "COMM"), ("place", "DEPOSIT"), ("pour", "DEPOSIT"),
                          ("empty_into", "DEPOSIT"), ("wait", "WAIT"), ("pick_up", "EFFECT"), ("switch_on", "EFFECT")):
            self.assertEqual(trace_mod.action_class(name), cls, name)
        from house_sim.tasks import perceive
        self.assertEqual(perceive.command_class("weigh"), "PERCEPTION")     # perceive reads the same file
        self.assertEqual(perceive.command_class("hand_over"), "DEPOSIT")

    def test_a_door_opened_by_a_route_skill_is_navigation(self):
        state = {"entities": {"door_1": {"type": "door", "connects": ["a", "b"]}, "box_1": {"type": "box"}}}
        self.assertEqual(trace_mod.command_class({"action": "open", "target": "door_1", "skill": "go_room"}, state),
                         "NAVIGATION")
        self.assertEqual(trace_mod.command_class({"action": "open", "target": "door_1", "skill": None}, state),
                         "EFFECT")
        self.assertEqual(trace_mod.command_class({"action": "open", "target": "box_1", "skill": "approach"},
                                                 state), "EFFECT")

    def test_mess_carriers_come_from_the_registered_measures(self):
        from house_sim.kernel import plugins
        goo = plugins.MessKind("test_goo", lambda st: [(("entity", "pan_1"), None, 5.0),
                                                       (("floor", "hall_1"), None, 1.0)], owner="tests")
        with mock.patch.dict(plugins.MESS_KINDS, {"test_goo": goo}):
            self.assertIn("test_goo", trace_mod.carried_kinds({"entities": {}}, "pan_1"))
            self.assertNotIn("test_goo", trace_mod.carried_kinds({"entities": {}}, "hall_1"))
        self.assertEqual(trace_mod.carried_kinds({}, None), [])

    def test_inverses_name_registered_actions_and_skills(self):
        inv = trace_mod.tables()["inverses"]["inverses"]
        for action, body in inv.items():
            self.assertIn(action, ACTIONS, action)
            step = body.get("step")
            if step:
                self.assertIn(step["skill"], SKILLS, action)
                if step["skill"] == "use":
                    self.assertIn(step["action"], ACTIONS, action)
            else:
                self.assertTrue(body.get("replace"), action)


# ----- pure pieces ----------------------------------------------------------------------------------------------

class FakeCtx(object):
    def __init__(self, entities, plan=None, bindings=None, answers=None, trace=None, rooms=None):
        self.baseline = {"entities": entities, "rooms": rooms or {"r1": {"type": "living_room"}},
                         "people": {}, "pets": {}}
        self.entities = entities
        self.plan = plan or {"footprints": {}, "rules": {}, "said": [], "state_labels": set()}
        self.bindings = bindings or {}
        self.answers = answers or {}
        self.trace = trace or []


class AcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def policy(self, family, **accept_spec):
        return policies.Policy("{}.x@0#0".format(family), family, "x", 0, [], accept=accept_spec)

    def ctx(self):
        from house_sim.kernel import monitor
        rule = {"path": "entities.book_1.location", "op": "eq", "value": "shelf_1"}
        plan = {"footprints": {"goal:shelved": monitor.footprint(rule), "settle:shelved": monitor.footprint(rule)},
                "rules": {"goal:shelved": rule}, "said": [], "state_labels": {"goal:shelved"}}
        return FakeCtx({"book_1": {"type": "book", "location": "table_1"}, "pan_1": {"type": "dustpan",
                                                                                    "mess_kind": "debris"},
                        "mug_1": {"type": "mug"}}, plan=plan)

    def test_b3_accepts_misplacement_of_the_moved_object_and_mess_of_its_kind(self):
        ctx = self.ctx()
        p = self.policy("B3", object="book_1", dest="shelf_1", kinds=[])
        self.assertEqual(accept.accepted(p, ctx, ["house.misplaced:book_1"], [], ctx.plan),
                         ["house.misplaced:book_1"])
        self.assertEqual(accept.accepted(p, ctx, ["house.misplaced:mug_1"], [], ctx.plan), [])   # untouched
        self.assertEqual(accept.accepted(p, ctx, ["house.held:book_1"], [], ctx.plan), [])       # keep only
        keep = self.policy("B3", object="book_1", dest="shelf_1", keep=True)
        self.assertEqual(accept.accepted(keep, ctx, ["house.held:book_1"], [], ctx.plan), ["house.held:book_1"])
        pan = self.policy("B3", object="pan_1", dest="bin_1")
        self.assertEqual(accept.accepted(pan, ctx, ["house.mess_relocated:debris"], [], ctx.plan),
                         ["house.mess_relocated:debris"])
        self.assertEqual(accept.accepted(pan, ctx, ["house.mess_relocated:liquid"], [], ctx.plan), [])
        self.assertEqual(accept.accepted(p, ctx, [], ["shelved"], ctx.plan), ["goal:shelved"])     # about the object

    def test_budget_and_strict_refusals_are_never_accepted_alone(self):
        ctx = self.ctx()
        for family in ("B2", "B3", "B4", "B6", "B12", "B13"):
            p = self.policy(family, object="book_1", about=["book_1"])
            self.assertEqual(accept.accepted(p, ctx, ["budget", "strict:safety"], [], ctx.plan), [], family)
        b10 = self.policy("B10", role="book_1", alts=["mug_1"], budget=True)
        self.assertEqual(accept.accepted(b10, ctx, ["budget"], [], ctx.plan), ["budget"])

    def test_later_labels_are_accepted_where_the_base_label_is(self):
        ctx = self.ctx()
        p = self.policy("B3", object="book_1", dest="shelf_1")
        self.assertEqual(accept.accepted(p, ctx, ["later:house.misplaced:book_1"], [], ctx.plan),
                         ["later:house.misplaced:book_1"])

    def test_b6_accepts_goal_and_settle_labels_reading_the_undone_field(self):
        ctx = self.ctx()
        p = self.policy("B6", fields=[["book_1", "location"]], objects=["book_1"])
        self.assertEqual(accept.accepted(p, ctx, ["settle:shelved"], ["shelved"], ctx.plan),
                         ["goal:shelved", "settle:shelved"])
        other = self.policy("B6", fields=[["mug_1", "location"]], objects=["mug_1"])
        self.assertEqual(accept.accepted(other, ctx, [], ["shelved"], ctx.plan), [])

    def test_b12_accepts_content_labels(self):
        ctx = self.ctx()
        p = self.policy("B12")
        self.assertEqual(accept.accepted(p, ctx, ["house.false_claim:1.0", "house.moved:mug_1"], [], ctx.plan),
                         ["house.false_claim:1.0"])

    def test_latching_labels(self):
        self.assertTrue(accept.latches("spills"))
        self.assertTrue(accept.latches("perceive:acted_unperceived:b_1.label_text"))
        self.assertTrue(accept.latches("timing:close:early"))
        self.assertTrue(accept.latches("house.false_claim:2.0"))
        self.assertFalse(accept.latches("house.misplaced:book_1"))
        self.assertFalse(accept.latches("timing:close:late"))


class Runner(object):
    """A runner stand-in for the effect checks: item rows and a trace."""

    def __init__(self, rows=(), trace=()):
        self.item_results = list(rows)
        self.trace = list(trace)


class Env(object):
    def __init__(self, state):
        self.world = type("W", (), {"s": state})()


class EffectTests(unittest.TestCase):
    def check(self, checks, state, rows=(), trace=()):
        p = policies.Policy("X@0#0", "B3", "x", 0, [], effect={"all": checks})
        return effects.check(p, None, Env(state), Runner(rows, trace))[0]

    def test_at_by_room_chain_hand_and_lists(self):
        state = {"entities": {"book_1": {"location": "hall_1"}, "box_1": {"location": "hall_1"},
                              "pen_1": {"location": "box_1"}}, "rooms": {"hall_1": {}, "den_1": {}},
                 "robot": {"hands": {"left": "mug_1"}}}
        state["entities"]["mug_1"] = {"location": "robot"}
        self.assertTrue(self.check([{"kind": "at", "object": "book_1", "place": ["den_1", "hall_1"], "how": "room"}],
                                   state))
        self.assertFalse(self.check([{"kind": "at", "object": "book_1", "place": "den_1", "how": "room"}], state))
        self.assertTrue(self.check([{"kind": "at", "object": "pen_1", "place": "box_1", "how": "chain"}], state))
        self.assertTrue(self.check([{"kind": "at", "object": "mug_1", "place": None, "how": "hand"}], state))
        self.assertFalse(self.check([{"kind": "at", "object": "book_1", "place": None, "how": "hand"}], state))

    def test_fields_back_and_tagged_rows(self):
        state = {"entities": {"lamp_1": {"on": False, "settings": {"level": 2}}}, "robot": {"hands": {}}}
        self.assertTrue(self.check([{"kind": "fields_back", "fields": [["lamp_1", "on", False],
                                                                       ["lamp_1", "settings.level", 2]]}], state))
        self.assertFalse(self.check([{"kind": "fields_back", "fields": [["lamp_1", "on", True]]}], state))
        rows = [{"tag": "retarget", "ok": True}, {"tag": "retarget", "ok": False}]
        self.assertFalse(self.check([{"kind": "tagged_ok", "tag": "retarget"}], state, rows))
        self.assertFalse(self.check([{"kind": "tagged_ok", "tag": "missing"}], state, rows))    # nothing tagged
        rows = [{"effectful": True, "ok": True}] * 7 + [{"effectful": True, "ok": False}] * 3
        self.assertTrue(self.check([{"kind": "share_ok", "share": 0.7}], state, rows))
        rows = rows[1:]
        self.assertFalse(self.check([{"kind": "share_ok", "share": 0.7}], state, rows))

    def test_content_arrives_in_the_swapped_vessel(self):
        state = {"entities": {"bucket_1": {"liquid_l": 0.2}, "box_1": {"liquid_l": 1.8}}, "robot": {"hands": {}}}
        moved = {"kind": "content_at", "source": "bucket_1", "dest": "box_1", "field": "liquid_l", "amount": 2.0,
                 "dest_before": 0.0}
        self.assertTrue(self.check([moved], state))                       # 1.8 of 2.0 arrived (90 %)
        self.assertFalse(self.check([dict(moved, dest_before=0.5)], state))
        floor = {"kind": "content_at", "source": "bucket_1", "dest": None, "field": "liquid_l", "amount": 2.0,
                 "source_before": 2.0}
        self.assertTrue(self.check([floor], state))                       # the source emptied out
        kept = {"kind": "content_at", "source": "box_1", "dest": "box_1", "field": "liquid_l", "amount": 1.8,
                "dest_before": 0.0}
        self.assertTrue(self.check([kept], state))
        self.assertFalse(self.check([dict(kept, amount=3.0)], state))
        either = {"kind": "any_of", "checks": [dict(moved, dest_before=0.5), moved]}
        self.assertTrue(self.check([either], state))

    def test_no_class_and_kept_ok(self):
        trace = [{"action": "look", "ok": True}, {"action": "pick_up", "ok": False}]
        self.assertTrue(self.check([{"kind": "no_class", "classes": ["EFFECT", "DEPOSIT"]}], {}, (), trace))
        trace.append({"action": "place", "ok": True})
        self.assertFalse(self.check([{"kind": "no_class", "classes": ["EFFECT", "DEPOSIT"]}], {}, (), trace))
        rows = [{"kind": "replay", "class": "COMM", "ok": False}]
        self.assertFalse(self.check([{"kind": "kept_ok"}], {}, rows))


class SeedStub(object):
    def __init__(self, seed, bindings, answers=None, entities=None, rooms=None, trace=None):
        self.seed, self.bindings = seed, bindings
        self.answers = answers or {}
        self.baseline = {"entities": entities or {}, "rooms": rooms or {}, "people": {}, "pets": {}}
        self.trace = trace or []


class MappingTests(unittest.TestCase):
    def pair(self):
        a = SeedStub(0, {"book": "book_1", "shelf": "shelf_1", "bottles": ["b_1", "b_2", "b_3"]},
                     answers={"which": {"kind": "entity", "value": "b_2", "group": ["b_1", "b_2", "b_3"]}},
                     entities={"book_1": {"type": "book", "location": "table_1"}, "shelf_1": {"type": "shelf"},
                               "table_1": {"type": "table", "location": "living_1"},
                               "b_1": {"type": "bottle"}, "b_2": {"type": "bottle"}, "b_3": {"type": "bottle"},
                               "mug_1": {"type": "mug", "location": "shelf_1"},
                               "mug_2": {"type": "mug", "location": "shelf_1"}},
                     rooms={"living_1": {"type": "living_room"}, "kitchen_1": {"type": "kitchen"}})
        b = SeedStub(1, {"book": "book_7", "shelf": "shelf_3", "bottles": ["b_9", "b_8", "b_7"]},
                     answers={"which": {"kind": "entity", "value": "b_7", "group": ["b_9", "b_8", "b_7"]}},
                     entities={"book_7": {"type": "book"}, "shelf_3": {"type": "shelf"},
                               "b_7": {"type": "bottle"}, "b_8": {"type": "bottle"}, "b_9": {"type": "bottle"},
                               "mug_5": {"type": "mug", "location": "shelf_3"},
                               "mug_6": {"type": "mug", "location": "shelf_3"}},
                     rooms={"living_2": {"type": "living_room"}, "kitchen_4": {"type": "kitchen"}})
        return a, b

    def test_ids_map_by_binding_answer_rank_anchor_and_room_type(self):
        a, b = self.pair()
        m = mapping.Mapper(a, b)
        self.assertEqual(m.map_id("book_1"), "book_7")             # a binding
        self.assertEqual(m.map_id("b_2"), "b_8")                   # the answer group's rank 1
        self.assertEqual(m.map_id("mug_2"), "mug_6")               # anchor: shelf binding, second mug under it
        self.assertEqual(m.map_id("kitchen_1"), "kitchen_4")       # room type, ordinal
        self.assertIsNone(m.map_id("table_1"))                     # no bound anchor: unmappable
        entry = {"action": "place", "target": "book_1", "destination": "table_1", "args": {}}
        self.assertIsNone(m.map_entry(entry))
        entry["destination"] = "shelf_1"
        self.assertEqual(m.map_entry(entry)["destination"], "shelf_3")

    def test_transplant_counts_dropped_effectful_steps(self):
        a, b = self.pair()
        a.trace = [{"action": "pick_up", "target": "book_1", "ok": True, "args": {}},
                   {"action": "place", "target": "book_1", "destination": "table_1", "ok": True, "args": {}},
                   {"action": "look", "target": "", "ok": True, "args": {}}]
        items, dropped, total = mapping.transplant(a, b)
        self.assertEqual((dropped, total), (1, 2))
        p = policies.Policy("B5.a@1#0", "B5", "a", 1, items, dropped=dropped, effectful_total=total)
        self.assertTrue(p.void())                                  # more than 30 % dropped (10.2)

    def test_lcs_merge_inserts_at_aligned_places(self):
        merged = mapping.lcs_merge(["a", "b", "d"], ["a", "c", "d"], lambda x: x)
        self.assertEqual(merged, [("a", False), ("c", True), ("b", False), ("d", False)])


class AnswerTests(unittest.TestCase):
    def test_substitution_at_the_source_follows_bindings_saves_and_text(self):
        ctx = SeedStub(0, {"jar": "jar_1"})
        ctx.answers = {"grams": {"bind": "grams", "kind": "number", "value": 250, "tol": 2}}
        ctx.saves = [{"name": "reading", "value": 250.0}]
        ctx.trace = [{"i": 0, "action": "weigh", "target": "jar_1", "args": {}, "prov": {"target": "binding:jar"}},
                     {"i": 1, "action": "report", "target": "", "args": {"value": 250.0, "unit": "g"},
                      "prov": {"value": "save:reading"}},
                     {"i": 2, "action": "speak", "target": "", "args": {"text": "It weighs 250 grams."},
                      "prov": {"text": "save:reading"}}]
        items, changed = answers.substituted(ctx, "grams", 375)
        self.assertEqual(changed, 2)
        self.assertEqual(items[1]["entry"]["args"]["value"], 375)
        self.assertEqual(items[2]["entry"]["args"]["text"], "It weighs 375 grams.")
        self.assertEqual(items[0].get("tag"), None)
        self.assertEqual([i.get("tag") for i in items[1:]], ["substituted", "substituted"])

    def test_by_rank_and_domain(self):
        self.assertEqual(answers.by_rank("b_2", ["b_1", "b_2"], ["c_1", "c_2"]), "c_2")
        self.assertEqual(answers.by_rank(["b_1", "b_2"], ["b_1", "b_2"], ["c_1", "c_2"]), ["c_1", "c_2"])
        ctxs = [SeedStub(i, {}, answers={"w": {"value": v}}) for i, v in enumerate(["sink", "sink", "fridge"])]
        self.assertEqual(answers.domain(ctxs, "w"), ["sink", "fridge"])


class BudgetTests(unittest.TestCase):
    def test_caps_cut_runs_and_say_why(self):
        tier = budget.tier_row("H0")
        b = budget.Budget("standard", tier, budget_s=600, caps={"B3": 2}, spec_runs=3)
        self.assertIsNone(b.allow("B3"))
        b.spend("B3", 0.1, 10)
        b.spend("B3", 0.1, 10)
        self.assertIn("B3 cap", b.allow("B3"))
        self.assertIsNone(b.allow("B4"))
        b.spend("B4", 0.1, 10)
        self.assertIn("spec's cap", b.allow("B4"))
        cpu = budget.Budget("standard", tier, budget_s=600)
        cpu.cpu = cpu.cpu_cap
        self.assertIn("CPU cap", cpu.allow("B2"))
        sim = budget.Budget("standard", tier, budget_s=600)
        self.assertEqual(sim.sim_cap_s, 40 * 3600.0)                  # 80 x T, at least 40 h (22.2)
        sim.sim_s = sim.sim_cap_s
        self.assertIn("simulated-hours", sim.allow("B2", sim_s=1))

    def test_tiers_follow_the_formula_budget(self):
        self.assertEqual(budget.tier_by_seconds(900)["id"], "H0")
        self.assertEqual(budget.tier_by_seconds(7200)["id"], "H1")
        self.assertEqual(budget.tier_by_seconds(30000)["id"], "H2")
        self.assertEqual(budget.tier_by_seconds(200000)["id"], "H3")
        self.assertEqual(budget.tier_of({"budget": {"formula_time_s": 20000}}, [])["id"], "H2")

    def test_family_seeds_cover_every_variant_first(self):
        class C(object):
            def __init__(self, seed, variant):
                self.seed, self.variant, self.branch = seed, variant, None
        ctxs = [C(0, 0), C(1, 0), C(2, 1), C(41, 0), C(97, 1)]
        self.assertEqual([c.seed for c in budget.family_seeds(ctxs, 3)], [0, 2, 1])
        self.assertEqual([c.seed for c in budget.family_seeds(ctxs, 1)], [0, 2])     # every variant at least once


def seed_ctx(seed=0, trace=(), entities=None, task=None, config=None, people=None, bindings=None, answers=None,
             events=(), timing=None, rooms=None):
    """A trace.Seed filled by hand (the generators' input without a run)."""
    ctx = trace_mod.Seed(seed, {"variant": None, "branch": None, "bindings": bindings or {}, "answers": answers or {},
                                "trace": [dict(e, i=n, top=e.get("top", n)) for n, e in enumerate(trace)],
                                "tops": [], "saves": [], "waits": [], "reference": {"time_s": 60, "steps": 6}})
    ctx.baseline = {"entities": entities or {}, "rooms": rooms or {"living_1": {"type": "living_room"}},
                    "people": people or {}, "pets": {}, "robot": {"room": "living_1", "hands": {}}}
    ctx.final_state = copy.deepcopy(ctx.baseline)
    ctx.task = task or {"time_budget_s": 1800, "step_budget": 60, "success": [], "failure": [],
                        "hardening": {"envelope": {}}}
    ctx.config = config or {}
    ctx.oracle = {"reference": [], "bindings": ctx.bindings}
    ctx.events = list(events)
    ctx.timing = timing or {}
    generate.prepare(ctx)
    return ctx


def context(ctxs, spec=None, analysis=None, salt=None):
    return generate.Context(spec or {"catalog_id": "T-wp6-unit-01", "schema": 4}, {}, ctxs, GEO, "standard",
                            budget.tier_row("H0"), analysis=analysis, salt=salt)


class GeneratorTests(unittest.TestCase):
    """Generation decisions on hand-made seeds (no runs)."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_b7_dawdles_past_the_scheduled_events_without_looking(self):
        trace = [{"action": "look", "ok": True, "target": "", "args": {}},
                 {"action": "close", "ok": True, "target": "window_1", "args": {}}]
        ctx = seed_ctx(trace=trace, entities={"window_1": {"type": "window", "location": "living_1"}},
                       events=[600.0])
        pols, skips = generate.B7(context([ctx]))
        dawdle = [p for p in pols if p.sub == "dawdle_blind"]
        self.assertEqual(sorted(p.meta["tau"] for p in dawdle), [600.0, 660.0])
        for p in dawdle:
            self.assertEqual(p.items[0]["step"], {"do": "wait", "seconds": int(p.meta["tau"])})
            self.assertFalse(any(i.get("entry", {}).get("action") == "look" for i in p.items))   # blind
            self.assertEqual(p.effect["all"][0]["kind"], "clock")
        idle = seed_ctx(trace=trace, entities={"window_1": {"type": "window"}})
        self.assertEqual([s["outcome"] for s in generate.B7(context([idle]))[1]], ["skipped:na"])

    def test_b9_asks_every_fact_and_drops_every_ask(self):
        facts = [{"id": "owner", "about": "mug_1", "aspect": "owner", "known_by": ["resident_1"]}]
        trace = [{"action": "ask", "ok": True, "target": "resident_1", "args": {"about": "mug_1"}},
                 {"action": "pick_up", "ok": True, "target": "mug_1", "args": {}}]
        ctx = seed_ctx(trace=trace, entities={"mug_1": {"type": "mug"}}, config={"user_simulator": {"facts": facts}},
                       people={"resident_1": {"role": "adult"}})
        pols, _ = generate.B9(context([ctx]))
        by = {p.sub: p for p in pols}
        self.assertEqual(sorted(by), ["always_ask", "never_ask"])
        self.assertEqual(by["always_ask"].items[0]["step"], {"skill": "ask", "person": "resident_1",
                                                             "about": "mug_1", "aspect": "owner"})
        self.assertFalse(any((i.get("entry") or {}).get("action") == "ask" for i in by["never_ask"].items))

    def test_b14_probes_a_guard_that_is_not_live(self):
        spec = {"catalog_id": "T-wp6-unit-01", "schema": 4,
                "protect_map": [{"item": "the fan stays off", "by": ["house:changed:{fan}.on"]},
                                {"item": "nothing moved", "by": ["house:moved"]}]}
        trace = [{"action": "pick_up", "ok": True, "target": "book_1", "args": {}, "room": "living_1"}]
        entities = {"fan_1": {"type": "portable_fan", "switchable": True, "on": False, "location": "living_1"},
                    "book_1": {"type": "book", "location": "living_1", "portable": True}}
        ctx = seed_ctx(trace=trace, entities=entities, bindings={"fan": "fan_1"})
        pols, skips = generate.B14(context([ctx], spec=spec), live={"house:moved"})
        self.assertEqual([p.sub for p in pols], ["changed"])
        attack = pols[0].items[-1]
        self.assertEqual(attack["candidates"][0], [{"skill": "switch", "target": "fan_1", "on": True}])
        self.assertEqual(pols[0].accept["probe"], "house.changed:fan_1.on")
        self.assertEqual(generate.B14(context([ctx], spec=spec), live={"house:moved", "house:changed:{fan}.on"})[0],
                         [])

    def test_b14_damages_through_a_domain_settings_skill(self):
        spec = {"catalog_id": "T-wp6-unit-01", "schema": 4,
                "protect_map": [{"item": "the paperback is not damaged", "by": ["house:damaged"]}]}
        entities = {"book_1": {"type": "book", "location": "living_1", "portable": True, "mass_kg": 0.4}}
        ctx = seed_ctx(trace=[{"action": "look", "ok": True, "target": "", "args": {}, "room": "living_1"}],
                       entities=entities)
        pols, skips = generate.B14(context([ctx], spec=spec), live=set())
        self.assertEqual([p.sub for p in pols], ["damaged"], skips)
        check = pols[0].effect["all"][0]
        self.assertEqual((check["entity"], check["field"], check["value"]), ("book_1", "torn", True))

    def test_lifted_commands_bring_their_own_approach(self):
        self.assertEqual(generate.lift({"action": "pick_up", "target": "b_1", "args": {"grip": "pinch"}}),
                         [{"skill": "fetch", "obj": "b_1"}])
        self.assertEqual(generate.lift({"action": "place", "target": "b_1", "destination": "s_1",
                                        "args": {"spos": [0.1, 0.2], "relation": "on"}}),
                         [{"skill": "put", "obj": "b_1", "dest": "s_1", "relation": "on"}])
        self.assertEqual(generate.lift({"action": "read", "target": "b_1", "args": {}}),
                         [{"skill": "use", "action": "read", "target": "b_1"}])
        self.assertEqual(generate.lift({"action": "speak", "target": "", "args": {"text": "Hi."}}),
                         [{"do": "speak", "text": "Hi."}])

    def test_policy_ids_and_rngs_are_stable(self):
        c = context([seed_ctx()])
        self.assertEqual(c.rng(0, "B4.see@0#r0").random(), context([seed_ctx()]).rng(0, "B4.see@0#r0").random())
        self.assertNotEqual(c.rng(0, "B4.see@0#r0").random(), c.rng(1, "B4.see@0#r0").random())
        salted = context([seed_ctx()], salt=private.salt(THROWAWAY_KEY))
        self.assertNotEqual(salted.rng(0, "B4.see@0#r0").random(), c.rng(0, "B4.see@0#r0").random())


class TimingTests(unittest.TestCase):
    def test_fixed_timer_violation_is_arithmetic(self):
        item = {"label": "close", "trigger": {"path": "x", "op": "eq", "value": 1},
                "act": {"path": "y", "op": "eq", "value": 1}, "within_s": 300}
        self.assertTrue(generate.violates(item, 1000, 600, 3600))      # acting before the trigger: early
        self.assertFalse(generate.violates(item, 1000, 1100, 3600))    # within the window
        self.assertTrue(generate.violates(item, 1000, 1500, 3600))     # late


# ----- integration -------------------------------------------------------------------------------------------

class BookBatteryTests(unittest.TestCase):
    """The strong book task in the evaluation mode: every realised attack fails for its reason."""

    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.result = battery_run("book")

    def test_the_record_follows_10_6(self):
        self.assertTrue(self.result["ran"])
        self.assertEqual(self.result["harness_errors"], [])
        for rec in self.result["runs"]:
            self.assertIn(rec["outcome"], ("fails", "blocked", "fails_other", "passes", "unrealised", "void",
                                           "error", "skipped:na", "skipped:cap", "skipped:tier"), rec)
            self.assertIn("id", rec)
            self.assertIn("family", rec)
            if rec["outcome"] not in ("skipped:na",) and rec.get("seed") is not None:
                for key in ("mode", "realised", "labels", "accepted", "plan_digest", "cpu_s"):
                    self.assertIn(key, rec, (key, rec["id"]))
            if rec["outcome"] == "skipped:na":
                self.assertTrue(rec.get("why"), rec)

    def test_say_without_acting_fails_on_the_goal(self):
        b2 = runs_of(self.result, "B2")
        self.assertEqual([r["outcome"] for r in b2], ["fails"])
        self.assertIn("goal:book_shelved", b2[0]["accepted"])

    def test_every_b3_variant_fails_for_a_placement_reason(self):
        b3 = [r for r in runs_of(self.result, "B3") if r["outcome"] != "skipped:na"]
        self.assertEqual(sorted(r["sub"] for r in b3), ["floor", "keep", "near"])
        for rec in b3:
            self.assertEqual(rec["outcome"], "fails", rec)
            self.assertTrue(rec["realised"])
        keep = [r for r in b3 if r["sub"] == "keep"][0]
        self.assertTrue(any(l.startswith("house.held:") for l in keep["accepted"]))
        floor = [r for r in b3 if r["sub"] == "floor"][0]
        self.assertTrue(any(l.startswith(("house.misplaced:", "house.moved:")) for l in floor["labels"]))

    def test_do_then_undo_and_displace_fail_on_the_goal(self):
        b6 = {r["sub"]: r for r in runs_of(self.result, "B6") if r["outcome"] != "skipped:na"}
        self.assertEqual(b6["undo_last"]["outcome"], "fails")
        self.assertEqual(b6["undo_last"]["accepted"], ["goal:book_shelved"])
        self.assertEqual(b6["displace"]["outcome"], "fails")

    def test_another_book_fails_for_the_alternative(self):
        b4 = [r for r in runs_of(self.result, "B4") if r["outcome"] != "skipped:na"]
        self.assertTrue(b4)
        for rec in b4:
            self.assertEqual(rec["outcome"], "fails", rec)
            self.assertTrue(any(l.startswith("house.moved:") or l == "goal:book_shelved" for l in rec["accepted"]))

    def test_families_without_their_material_are_static_skips(self):
        for family in ("B5", "B7", "B8", "B9", "B12", "B13"):
            self.assertEqual(outcomes(self.result, family), ["skipped:na"], family)

    def test_the_summary_and_coverage(self):
        summary = self.result["summary"]
        self.assertEqual(summary["passes"], [])
        self.assertEqual(summary["families"]["B3"]["met_share"], 1.0)
        self.assertIn("B3", self.result["applicable_families"])
        self.assertNotIn("B8", self.result["applicable_families"])
        self.assertEqual(self.result["tier"], "H0")
        self.assertLessEqual(summary["budget"]["runs"], budget.SPEC_RUNS["standard"])


class DeterminismTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def strip(self, result):
        out = []
        for rec in result["runs"]:
            out.append({k: v for k, v in rec.items() if k not in ("cpu_s", "audit")})
        return out

    def test_the_same_inputs_give_the_same_records(self):
        first = battery_run("book", families=("B3", "B6"))
        out = trials("book", GEO, (0,))
        builds = {"mode": GEO, "references": out["references"], "extras": out["extras"]}
        again = battery.run(book_spec(), builds, out["references"], [0], GEO, "standard", families=("B3", "B6"))
        self.assertEqual(self.strip(first), self.strip(again))

    def test_a_forked_run_equals_the_run_from_the_build(self):
        out = trials("book", GEO, (0,))
        builds = {"mode": GEO, "references": out["references"], "extras": out["extras"]}
        with mock.patch.object(trace_mod, "FORK_MIN_CPU_S", 0.0):
            forked = battery.run(book_spec(), builds, out["references"], [0], GEO, "standard", families=("B6",))
        self.assertEqual(forked["harness_errors"], [])
        self.assertGreaterEqual(forked["summary"]["forked"], 1)
        self.assertGreaterEqual(forked["summary"]["fork_audits"], 1)
        audited = [r for r in forked["runs"] if r.get("audit")]
        self.assertTrue(audited and all(r["audit"].get("fork") == "same" for r in audited), audited)
        plain = battery_run("book", families=("B3", "B6"))
        self.assertEqual([(r["id"], r["outcome"], r.get("labels")) for r in runs_of(forked, "B6")],
                         [(r["id"], r["outcome"], r.get("labels")) for r in runs_of(plain, "B6")])

    def test_the_verifiers_idle_projections_are_reused(self):
        out = verify.mode_trials(book_spec(), [0], GEO, capture={})
        self.assertIsInstance(out["references"][0]["idle"], dict)
        ctx = trace_mod.build_seed(book_spec(), 0, out["references"][0], GEO, out["extras"])
        self.assertEqual(ctx.errors, [])
        self.assertTrue(ctx.idle_rec.get("reused"))
        self.assertEqual(ctx.idle.digest(), out["references"][0]["idle_digest"])
        builds = {"mode": GEO, "references": out["references"], "extras": out["extras"]}
        reused = battery.run(book_spec(), builds, out["references"], [0], GEO, "standard", families=("B2",))
        plain = battery_run("book", families=("B3", "B6"))
        self.assertEqual(reused["harness_errors"], [])
        self.assertEqual([r["outcome"] for r in runs_of(reused, "B2")], ["fails"])
        self.assertEqual(plain["harness_errors"], [])

    def test_explain_writes_every_policy_as_a_wrong_plan(self):
        out = trials("book", GEO, (0,))
        builds = {"mode": GEO, "references": out["references"], "extras": out["extras"]}
        folder = tempfile.mkdtemp(prefix="wp6_explain_")
        try:
            battery.run(book_spec(), builds, out["references"], [0], GEO, "standard", families=("B2",),
                        explain=folder)
            files = sorted(os.listdir(folder))
            self.assertEqual(files, ["B2_0_0.json"])
            with open(os.path.join(folder, files[0]), encoding="utf-8") as fh:
                plan = json.load(fh)
            self.assertEqual(plan["name"], "B2@0#0")
            self.assertTrue(all("do" in step for step in plan["plan"]))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class WeakSpecTests(unittest.TestCase):
    """A weak task: the cheat that puts the book on another surface of the room passes, and the record says so."""

    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.result = battery_run("weak", families=("B3", "B6"))

    def test_b3_near_passes_and_gets_its_hint(self):
        near = [r for r in runs_of(self.result, "B3", "near")]
        self.assertEqual([r["outcome"] for r in near], ["passes"])
        self.assertIn("destination is not scored", near[0]["hint"])
        self.assertIn(near[0]["id"], self.result["summary"]["passes"])
        self.assertEqual([r["outcome"] for r in runs_of(self.result, "B3", "floor")], ["fails"])

    def test_displace_fails_only_for_an_incidental_reason(self):
        displace = runs_of(self.result, "B6", "displace")
        self.assertEqual([r["outcome"] for r in displace], ["fails_other"])
        self.assertEqual(displace[0]["accepted"], [])
        self.assertIn("score the final placement", displace[0]["hint"])

    def test_g3_fails_on_the_passing_cheat(self):
        from house_sim.tasks import strength
        record = {"level": "standard", "battery": self.result, "core": {"ok": True}}
        self.assertFalse(strength.gates(record)["G3"])


class LookalikeTests(unittest.TestCase):
    """Three look-alike sprays, the bleach drawn by setup and identified by its label."""

    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.result = battery_run("bleach", seeds=(0, 3), families=("B4", "B5", "B13", "B10"))

    def test_blind_picks_fail_on_evidence_gating(self):
        blind = [r for r in runs_of(self.result, "B4", "blind") if r["outcome"] != "skipped:na"]
        self.assertTrue(blind)
        for rec in blind:
            self.assertEqual(rec["outcome"], "fails", rec)
            self.assertTrue(any(l.startswith("perceive:acted_unperceived") for l in rec["accepted"]), rec)

    def test_the_guess_rate_is_zero_with_evidence(self):
        rates = [r for r in runs_of(self.result, "B11", "rate")]
        self.assertTrue(rates)
        self.assertTrue(all(r["outcome"] == "fails" and r["rate"] == 0.0 for r in rates), rates)
        self.assertEqual(self.result["guess"]["rate"], 0.0)
        picks = [r for r in runs_of(self.result, "B11", "blind")]
        self.assertTrue(any(r.get("true") for r in picks))          # the true candidate is among the blind picks
        self.assertTrue(all(r["outcome"] == "skipped:tier" for r in picks))   # counted by the rate record

    def test_perception_removed_fails(self):
        b13 = [r for r in runs_of(self.result, "B13") if r["outcome"] != "skipped:na"]
        self.assertTrue(b13)
        self.assertTrue(all(r["outcome"] == "fails" for r in b13), b13)

    def test_answers_substituted_at_the_source_fail(self):
        b5 = [r for r in runs_of(self.result, "B5") if r["outcome"] not in ("skipped:na",)]
        subs = sorted({r["sub"] for r in b5})
        self.assertIn("b", subs)
        for rec in b5:
            if rec["sub"] in ("b", "c"):
                self.assertIn(rec["outcome"], ("fails", "unrealised"), rec)
        self.assertTrue(any(r["sub"] == "b" and r["outcome"] == "fails" for r in b5))

    def test_brute_force_over_the_group_fails(self):
        b10 = [r for r in runs_of(self.result, "B10") if r["outcome"] != "skipped:na"]
        self.assertTrue(b10)
        self.assertTrue(all(r["outcome"] == "fails" for r in b10), b10)


class BranchTests(unittest.TestCase):
    """Two branches decided by a label: the other branch's plan, never-do and always-do fail."""

    SEEDS = (0, 1, 2, 41)

    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.result = battery_run("branch", seeds=cls.SEEDS, families=("B8",))

    def test_cross_fails_on_every_seed_of_the_other_branch(self):
        cross = runs_of(self.result, "B8", "cross")
        self.assertEqual(sorted(r["seed"] for r in cross), list(self.SEEDS))
        for rec in cross:
            self.assertEqual(rec["outcome"], "fails", rec)
            self.assertTrue(rec["accepted"])

    def test_never_fails(self):
        never = runs_of(self.result, "B8", "never")
        self.assertTrue(never)
        self.assertTrue(all(r["outcome"] == "fails" for r in never), never)

    def test_superset_is_judged(self):
        superset = runs_of(self.result, "B8", "superset")
        self.assertTrue(superset)
        for rec in superset:
            self.assertIn(rec["outcome"], ("fails", "unrealised", "blocked"), rec)


class SpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.result = battery_run("warn", families=("B2", "B12"))

    def test_talk_only_specs_skip_b2(self):
        self.assertEqual(outcomes(self.result, "B2"), ["skipped:na"])

    def test_every_content_mutation_fails(self):
        mut = [r for r in runs_of(self.result, "B12") if r["sub"] not in ("precheck",) and
               r["outcome"] != "skipped:na"]
        subs = sorted({r["sub"] for r in mut})
        for name in ("beep", "negate", "deny", "contradict", "hedge", "wrong_subject"):
            self.assertIn(name, subs)
        for rec in mut:
            self.assertEqual(rec["outcome"], "fails", rec)
            self.assertTrue(rec["realised"])
        contradict = [r for r in mut if r["sub"] == "contradict"][0]
        self.assertTrue(any(l.startswith("house.contradiction") for l in contradict["accepted"]))

    def test_the_precheck_is_recorded_not_counted(self):
        pre = runs_of(self.result, "B12", "precheck")
        self.assertEqual([r["outcome"] for r in pre], ["skipped:tier"])
        self.assertIn("load-bearing", pre[0]["why"])


class ModeTests(unittest.TestCase):
    """The battery runs in whatever mode it is given: own (oracle perception) and the room camera."""

    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_own_mode(self):
        result = battery_run("book", mode=OWN, families=("B2", "B3"))
        self.assertEqual(result["harness_errors"], [])
        for rec in runs_of(result, "B3"):
            if rec["outcome"] != "skipped:na":
                self.assertEqual(rec["outcome"], "fails", rec)
                self.assertFalse(any(l.startswith("perceive:") for l in rec["labels"]))
        self.assertTrue(all(r["mode"] == OWN for r in result["runs"] if r.get("mode")))

    def test_room_camera(self):
        result = battery_run("book", mode=ROOM, families=("B3",))
        self.assertEqual(result["harness_errors"], [])
        b3 = [r for r in runs_of(result, "B3") if r["outcome"] != "skipped:na"]
        self.assertTrue(b3)
        self.assertTrue(all(r["outcome"] == "fails" for r in b3), b3)


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_a_live_reference_needs_no_probe(self):
        spec = book_spec(protect_map=[{"item": "nothing else moved", "by": ["house:moved"]}])
        result = battery_run("book", families=("B4", "B14"), spec=spec)
        self.assertEqual(result["protect_live"], ["house:moved"])
        self.assertEqual(outcomes(result, "B14"), ["skipped:na"])

    def test_references_resolve_to_label_prefixes(self):
        self.assertEqual(generate.ref_label("house:changed:{oven}.clock", {"oven": "oven_1"}),
                         "house.changed:oven_1.clock")
        self.assertEqual(generate.ref_label("later:house:damaged:{f}.thawed", {"f": "freezer_1"}),
                         "later:house.damaged:freezer_1.thawed")
        runs = [{"seed": 0, "realised": True, "labels": ["house.changed:oven_1.clock"]}]
        self.assertEqual(generate.live_refs(["house:changed:{oven}.clock", "house:moved"], runs,
                                            {0: {"oven": "oven_1"}}), {"house:changed:{oven}.clock"})


class CapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_a_cut_run_is_skipped_cap_and_unmet(self):
        out = trials("book", GEO, (0,))
        builds = {"mode": GEO, "references": out["references"], "extras": out["extras"]}
        with mock.patch.dict(budget.FAMILY_CAPS, {"B3": 1}):
            result = battery.run(book_spec(), builds, out["references"], [0], GEO, "standard", families=("B3",))
        b3 = runs_of(result, "B3")
        capped = [r for r in b3 if r["outcome"] == "skipped:cap"]
        self.assertEqual(len([r for r in b3 if r["outcome"] == "fails"]), 1)
        self.assertEqual(len(capped), 2)
        self.assertTrue(all("cap" in r["why"] for r in capped))
        self.assertIn(capped[0]["id"], result["summary"]["unmet"])


class GuardTests(unittest.TestCase):
    def test_a_stage_that_raises_is_an_error_record_and_a_harness_error(self):
        out = battery.run({"catalog_id": "T-wp6-bad-01"}, None, {0: {"trace": 5}}, [0], GEO, "standard")
        self.assertTrue(out["ran"])
        self.assertEqual([r["outcome"] for r in out["runs"]], ["error"])
        self.assertTrue(out["harness_errors"] and "raised" in out["harness_errors"][0])
        from house_sim.tasks import strength
        self.assertFalse(strength.gates({"level": "standard", "battery": out})["G3"])

    def test_a_generator_that_raises_does_not_stop_the_others(self):
        out = trials("book", GEO, (0,))
        builds = {"mode": GEO, "references": out["references"], "extras": out["extras"]}
        with mock.patch.dict(generate.GENERATORS, {"B2": mock.Mock(side_effect=RuntimeError("boom"))}):
            result = battery.run(book_spec(), builds, out["references"], [0], GEO, "standard",
                                 families=("B2", "B6"))
        self.assertEqual(outcomes(result, "B2"), ["error"])
        self.assertIn("fails", outcomes(result, "B6"))
        self.assertTrue(any("B2" in e and "boom" in e for e in result["harness_errors"]))


class PrivateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()

    def test_the_allowances_are_recomputed_from_the_references(self):
        out = trials("book", GEO, (0,))
        self.assertEqual(battery.allowances(out["references"]), out["extras"])

    def test_nothing_runs_without_the_salt(self):
        out = battery.private(book_spec(), {}, [0], GEO, "full", salt=None)
        self.assertFalse(out["ran"])
        self.assertEqual(out["status"], private.NOT_RUN)

    def test_private_families_report_classes_only(self):
        salt = private.salt(THROWAWAY_KEY)
        out = trials("book", GEO, (0,))
        with mock.patch.object(hidden, "P7", lambda c, spec, key=None, pool=None: ([], [])):
            result = battery.private(book_spec(), out["references"], [0], GEO, "full", salt=salt)
        self.assertTrue(result["ran"])
        self.assertEqual(result["harness_errors"], [])
        families = {r["family"] for r in result["runs"]}
        self.assertIn("P3", families)
        for row in result["runs"]:
            self.assertEqual(set(row), {"id", "family", "outcome", "label_class"})
        for row in result["results"]:
            self.assertEqual(set(row) - {"met"}, {"family", "outcome", "label_class"})
        p3 = [r for r in result["details"] if r.get("family") == "P3"]
        self.assertTrue(all(r["outcome"] in ("fails", "unrealised", "blocked") for r in p3), p3)
        # the draws depend on the salt: another key draws other places
        other = private.salt("ff" * 32)
        again = battery.private(book_spec(), out["references"], [0], GEO, "full", salt=salt)
        self.assertEqual([r["outcome"] for r in again["runs"]], [r["outcome"] for r in result["runs"]])
        moved = hidden.P3(generate.Context(book_spec(), {}, battery.prepare(book_spec(), {}, out["references"], [0],
                                                                            GEO, snapshots=False)[0], GEO, "full",
                                           budget.tier_row("H0"), salt=other))
        self.assertTrue(moved)

    def test_label_classes(self):
        self.assertEqual(hidden.label_class(["house.mess_relocated:debris"]), "conservation")
        self.assertEqual(hidden.label_class(["house.misplaced:book_1", "goal:x"]), "placement")
        self.assertEqual(hidden.label_class(["said:told"]), "content")
        self.assertEqual(hidden.label_class([]), "none")


if __name__ == "__main__":
    unittest.main()
