"""The last-writer log, robot placements, causes, mess flows and destroyed records (docs/design/VERIFY_HARDENING.md
6.2, 6.5, 8.6, 10.5 and 25, package WP2; kernel/world.py WriterLog and kernel/engine.py).

* every write after the baseline is attributed per record field to its writer: the robot's action window (its handler
  and its own settle), a system by its class (plugins.SYSTEM_WRITERS, default physical), a scheduled event (volition),
  an auto standing-order command (robot), the substep settle pass (physical; robot when the kernel physics attributes
  the consequence to the robot); refused actions and rolled-back savepoints leave nothing; quiet writes are physical;
* P_R(e), the robot placement, survives a person or pet moving e afterwards;
* causes reported with a write are followed back (at most 5 physical links) to the robot;
* logs.gone keeps the final record of every entity destroyed during the episode, with its writer; logs.mess_flows logs
  flows and transforms with the writer, merging identical flows; neither exists before the baseline or in a World that
  does not track, and neither changes anything else (state digests without destructions or flows are unchanged);
* save / load is deterministic for forking (the writer log included) and a replay rebuilds the log -- in both
  perception modes and both camera modes, on authored specs.

Scenes use core types only, so the file passes with HOUSE_SIM_DOMAINS=core; the authored-spec tests skip when their
domain is not loaded. Normal and HOUSE_SIM_STRICT=1.
"""
import contextlib
import copy
import glob
import json
import os
import tempfile
import unittest
from pathlib import Path

from house_sim.kernel import plugins
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.predicates import MISSING, digest
from house_sim.kernel.registry import default_registry
from house_sim.kernel.world import EXISTS, World, WriterLog, is_bookkeeping

REG = default_registry()
ROOT = Path(__file__).resolve().parent.parent


def make(type_name, location, **props):
    return REG.make(type_name, location, **props)


def room(label, size, exits, floor="tile", **extra):
    out = {"label": label, "size_m": list(size), "floor_material": floor, "exits": list(exits)}
    out.update(extra)
    return out


def config(entities=None, people=None, events=None):
    """kitchen (5 x 4 m) - hall (3 x 2 m): a counter with a mug, a table, a box, ceiling lights."""
    cfg = {"schema_version": 2, "start_room": "kitchen",
           "rooms": {"kitchen": room("kitchen", (5.0, 4.0), ["hall"], type="kitchen"),
                     "hall": room("hall", (3.0, 2.0), ["kitchen"], floor="hardwood", type="hallway")},
           "entities": {"table_1": make("table", "kitchen", pos=[3, 2]),
                        "counter_1": make("counter", "kitchen", pos=[1, 2]),
                        "cup_1": make("generic_item", "counter_1", relation="on", label="cup", portable=True,
                                      mass_kg=0.3, size_m=[0.08, 0.08, 0.1]),
                        "vase_1": make("generic_item", "table_1", relation="on", label="vase", portable=True,
                                       mass_kg=1.0, size_m=[0.15, 0.15, 0.3]),
                        "lamp_1": make("generic_item", "kitchen", label="lamp", category="lamp", switchable=True,
                                       on=True, pos=[2, 1], portable=False, size_m=[0.3, 0.3, 0.5])},
           "robot": {"room": "kitchen", "pos": [1, 1]}, "people": dict(people or {}), "pets": {}}
    for rid in ("kitchen", "hall"):
        cfg["entities"]["light_" + rid] = make("generic_item", rid, label="ceiling light", category="light", on=True,
                                               mounted=True, portable=False, mount_height_m=2.4, pos=[0, 0])
    cfg["entities"].update(entities or {})
    if events:
        cfg["events"] = list(events)
    return cfg


def ok(test, env, *args, **kw):
    result = env.step(*args, **kw)
    test.assertTrue(result["ok"], (args, kw, result["message"]))
    return result


@contextlib.contextmanager
def system(name, fn, writer="physical", order=999, max_dt=30.0):
    """A system registered for the duration of a test, with its writer class (plugins.SYSTEM_WRITERS, WP1's
    @system(..., writer=); created here when the registry does not have it yet). The plugin tables are left as they
    were."""
    entry = (order, name, float(max_dt), fn)
    created = getattr(plugins, "SYSTEM_WRITERS", None) is None
    if created:
        plugins.SYSTEM_WRITERS = {}
    table = plugins.SYSTEM_WRITERS
    old = table.get(name, MISSING)
    table[name] = writer
    plugins.SYSTEMS.append(entry)
    plugins.SYSTEMS.sort(key=lambda t: (t[0], t[1]))
    try:
        yield
    finally:
        plugins.SYSTEMS.remove(entry)
        if created:
            del plugins.SYSTEM_WRITERS
        elif old is MISSING:
            table.pop(name, None)
        else:
            table[name] = old


def carry_cup_to_table(test, env):
    for command in (("approach", "cup_1"), ("pick_up", "cup_1"), ("approach", "table_1"),
                    ("place", "cup_1", "table_1")):
        ok(test, env, *command)


def load_spec(cid):
    for path in sorted(glob.glob(str(ROOT / "tasks" / "authored" / "*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for spec in data.get("specs", []) if isinstance(data, dict) else data:
            if spec.get("catalog_id") == cid:
                return spec
    raise KeyError(cid)


def domains_loaded(*names):
    plugins.load_domains()
    import sys
    return all("house_sim.systems." + n in sys.modules for n in names)


def hardened_book_spec(**changes):
    """A small schema-4 spec on core types: shelve the book (labelled success, an envelope that lets it move there)."""
    out = {"catalog_id": "T-wp2-book-01", "title": "shelve the book", "schema": 4, "risk_level": 0,
           "house": {"residents": "minimal", "pets": "none"},
           "setup": [{"op": "room", "var": "living", "type": "living_room", "pick": "first"},
                     {"op": "lights", "room": "{living}", "on": True},
                     {"op": "spawn", "type": "bookshelf", "furniture": "{living}", "var": "shelf"},
                     {"op": "spawn", "type": "coffee_table", "furniture": "{living}", "var": "table"},
                     {"op": "spawn", "type": "book", "on": "{table}", "var": "book"},
                     {"op": "robot", "room": "{living}"},
                     {"op": "prior_knowledge", "map": "all"}],
           "instruction": "Put the book on the bookshelf.",
           "phrasings": ["Put the book on the bookshelf.", "Shelve the book, please.",
                         "The book goes on the bookshelf."],
           "success": [{"label": "book_shelved", "pred": {"path": "entities.{book}.location", "op": "eq",
                                                           "value": "{shelf}"}}],
           "envelope": {"work_rooms": ["{living}"], "move": {"{book}": {"to": ["{shelf}"]}}},
           "budget": {"steps": 40, "time_s": 240},
           "reference": [{"skill": "fetch", "obj": "{book}"}, {"skill": "put", "obj": "{book}", "dest": "{shelf}"}],
           "wrong": []}
    out.update(changes)
    return out


def idle_projections(env, seconds):
    """The idle counterfactual of env's house: projections at the start and after an idle wait of `seconds`."""
    from house_sim.kernel import counterfactual
    proj = counterfactual.Projections([counterfactual.project(env.world.s)])
    ok_ = env.step("wait", seconds=int(seconds))
    assert ok_["ok"], ok_["message"]
    proj.add(counterfactual.project(env.world.s))
    return proj


def index_digest(idx):
    """A digest of a perceive.Index (the evaluation-mode evidence a fork must reproduce)."""
    body = {"fields": sorted([[k[0], k[1], [list(o) for o in v]] for k, v in idx.fields.items()], key=str),
            "views": sorted([[k, [list(v) for v in vs]] for k, vs in idx.views.items()], key=str),
            "agents": sorted([[k, v] for k, v in idx.agents.items()], key=str),
            "rest": [idx.rooms, idx.readings, idx.notices, idx.heard, idx.acts, idx.messages, idx.last_step]}
    return digest(json.loads(json.dumps(body, sort_keys=True, default=repr)))


class WriterClassTests(unittest.TestCase):
    def test_the_robots_action_window_writes_are_the_robots_and_systems_by_their_class(self):
        def nudge(w, dt, tick):                 # a physical system: the vase slowly creeps (a test process)
            w.mut("vase_1")["creep_mm"] = float(w.ent("vase_1").get("creep_mm", 0.0)) + 1.0

        def walker(w, dt, tick):                # a volition system: a resident's own decision
            if w.ent("light_hall").get("on"):
                w.mut("light_hall")["on"] = False

        with system("tests.nudge", nudge), system("tests.walker", walker, writer="volition"):
            env = HouseEnv(config(), seed=0)
            carry_cup_to_table(self, env)
            ok(self, env, "wait", seconds=60)
        log = env.writes
        self.assertEqual(log[("cup_1", "location")][:2], ("robot", "place"))
        self.assertEqual(log[("cup_1", "relation")][:2], ("robot", "place"))
        self.assertEqual(log[("vase_1", "creep_mm")][:2], ("physical", "tests.nudge"))
        self.assertEqual(log[("light_hall", "on")][:2], ("volition", "tests.walker"))
        self.assertEqual(env.robot_placement("cup_1"), {"location": "table_1", "relation": "on",
                                                        "spos": env.world.ent("cup_1")["spos"]})
        self.assertIsNone(env.robot_placement("vase_1"))
        self.assertTrue(log.robot_wrote("cup_1"))
        self.assertFalse(log.robot_wrote("vase_1"))
        self.assertFalse(log.robot_wrote("light_hall"))
        # the writer log is outside the state and never part of an observation
        self.assertNotIn("writes", env.world.s)
        self.assertNotIn("creep_mm", json.dumps(env.observe().get("message", "")))

    def test_two_writers_of_one_record_in_one_step_split_by_field_and_the_last_writer_wins(self):
        def mark(w, dt, tick):                  # a physical system touching the robot's cup in the same step
            if w.ent("cup_1").get("location") == "table_1":
                w.mut("cup_1")["dust"] = round(float(w.ent("cup_1").get("dust", 0.0)) + 0.1, 3)

        with system("tests.dust", mark):
            env = HouseEnv(config(), seed=0)
            for command in (("approach", "cup_1"), ("pick_up", "cup_1"), ("approach", "table_1")):
                ok(self, env, *command)
            ok(self, env, "place", "cup_1", "table_1")     # 1 s action + one substep of the same step
        log = env.writes
        self.assertEqual(log[("cup_1", "location")][:2], ("robot", "place"))
        self.assertEqual(log[("cup_1", "dust")][:2], ("physical", "tests.dust"))
        history = log.history("cup_1", "location")
        self.assertEqual([h["name"] for h in history], ["pick_up", "place"])

    def test_scheduled_events_are_volition_and_a_refused_action_logs_nothing(self):
        events = [{"at_s": 30, "kind": "draft", "changes": {"entities.light_hall.on": False}}]
        env = HouseEnv(config(events=events), seed=0)
        before = len(env.writes)
        result = env.step("pick_up", "table_1")          # refused (fixed in place): rolled back, nothing logged
        self.assertFalse(result["ok"])
        self.assertFalse(any(v[0] == "robot" and k[0] == "table_1" for k, v in env.writes.items()))
        ok(self, env, "wait", seconds=60)
        self.assertEqual(env.writes[("light_hall", "on")][:2], ("volition", "event:draft"))
        self.assertGreater(len(env.writes), before)

    def test_an_auto_standing_order_command_is_the_robots_and_a_refused_one_logs_nothing(self):
        env = HouseEnv(config(), seed=0)
        ok(self, env, "approach", "lamp_1")
        ok(self, env, "watch", condition={"path": "time_s", "op": "gte", "value": 60},
           commands=[{"action": "switch_off", "target": "light_kitchen"}], mode="auto")    # too high: refused
        ok(self, env, "watch", condition={"path": "time_s", "op": "gte", "value": 60},
           commands=[{"action": "switch_off", "target": "lamp_1"}], mode="auto")
        ok(self, env, "wait", seconds=180)
        self.assertFalse(env.world.ent("lamp_1")["on"])
        self.assertEqual(env.writes[("lamp_1", "on")][:2], ("robot", "order:switch_off"))
        self.assertTrue(env.world.ent("light_kitchen")["on"])
        self.assertFalse(env.writes.robot_wrote("light_kitchen"))
        self.assertIn(False, [e["ok"] for e in env.world.s["order_log"]])

    def test_quiet_writes_are_physical_and_left_out_of_the_record_diffs(self):
        def warm(w, dt, tick):
            w.set_quiet("cup_1", "temperature_c", round(float(w.ent("cup_1").get("temperature_c", 20.0)) + 0.5, 3))

        with system("tests.warm", warm, writer="volition"):
            env = HouseEnv(config(), seed=0)
            ok(self, env, "approach", "cup_1")
            ok(self, env, "pick_up", "cup_1")
            ok(self, env, "wait", seconds=90)
        cls, name, _ = env.writes[("cup_1", "temperature_c")]
        self.assertEqual((cls, name), ("physical", "tests.warm"))
        self.assertEqual(env.writes[("cup_1", "location")][:2], ("robot", "pick_up"))

    def test_the_settle_pass_attributes_its_consequences_like_the_kernel_physics(self):
        """A substep settle consequence the kernel physics attributes to the robot (impact record by "robot": the
        robot arranged the item or its support) is a robot write; an exogenous one stays physical."""
        env = HouseEnv(config(), seed=0)
        w = env.world
        w.begin()
        w._writer = ("physical", "physics.settle")
        w.mut("vase_1")["tipped"] = True
        w.mut("cup_1")["tipped"] = True
        w.destroy("cup_1")
        env._robot_consequences([{"kind": "phys.fall", "target": "cup_1", "ids": [], "by": "robot"},
                                 {"kind": "phys.fall", "target": "vase_1", "ids": [], "by": "exogenous"}])
        w.commit()
        log = env.writes
        self.assertEqual(log[("cup_1", EXISTS)][:2], ("robot", "physics.settle"))
        self.assertTrue(w.s["logs"]["gone"][-1]["last"]["tipped"])       # a destroyed record's fields: in logs.gone
        self.assertEqual(log[("vase_1", "tipped")][:2], ("physical", "physics.settle"))
        self.assertEqual((w.s["logs"]["gone"][-1]["by"], w.s["logs"]["gone"][-1]["name"]), ("robot", "physics.settle"))

    def test_reactions_to_the_robots_sounds_and_the_end_of_step_perception_are_not_the_robots(self):
        def startle(w, level, action, room, cell):
            w.mut("vase_1")["startled_by"] = action

        class Spec(object):
            name, noise_db = "vacuum", 70.0

        class Ctx(object):
            spec = Spec()

        plugins.HOOKS["robot_sound"].append((999, "tests.startle", startle))
        try:
            env = HouseEnv(config(), seed=0)
            ok(self, env, "approach", "table_1")
            w = env.world
            w.begin()
            w._writer = ("robot", "vacuum")
            env._robot_sound(Ctx())                     # a noisy action's sound: the hook reacts
            w.mut("lamp_1")["dusted"] = True             # the action goes on: the robot's again
            w.commit()
        finally:
            plugins.HOOKS["robot_sound"].remove((999, "tests.startle", startle))
        self.assertEqual(env.writes[("vase_1", "startled_by")][:2], ("volition", "robot_sound"))
        self.assertEqual(env.writes[("lamp_1", "dusted")][:2], ("robot", "vacuum"))
        # no write of the end-of-step perception updates is the robot's
        self.assertFalse(any(name == "kernel.perception" and cls == "robot" for cls, name, _ in env.writes.values()))


class SegmentTests(unittest.TestCase):
    """World-level: frames, savepoints and rollbacks of the writer log."""

    def world(self):
        state = {"rooms": {"r": {"size_m": [4.0, 4.0]}},
                 "entities": {"a": {"location": "r", "v": 1}, "b": {"location": "r", "v": 2}},
                 "connections": {"d": {"open": False}}, "people": {"p": {"location": "r"}},
                 "robot": {"room": "r", "pos": [0, 0], "hands": {}}, "steps": 0, "time_s": 0.0, "stats": {},
                 "logs": {"moves": []}}
        w = World(state)
        log = WriterLog()
        w.track(log)
        return w, log

    def test_interleaved_writers_of_one_record_get_their_own_fields(self):
        w, log = self.world()
        w.begin()
        with w.writing("robot", "place"):
            w.mut("a")["location"] = "shelf"
        with w.writing("physical", "wind"):
            w.mut("a")["v"] = 5
        with w.writing("volition", "cat"):
            w.mut("a")["location"] = "floor"
            w.conn_m("d")["open"] = True
            w.person_m("p")["location"] = "hall"
        w.commit()
        self.assertEqual(log[("a", "location")][:2], ("volition", "cat"))
        self.assertEqual([h["class"] for h in log.history("a", "location")], ["robot", "volition"])
        self.assertEqual(log[("a", "v")][:2], ("physical", "wind"))
        self.assertEqual(log[("connections:d", "open")][:2], ("volition", "cat"))
        self.assertEqual(log[("people:p", "location")][:2], ("volition", "cat"))
        self.assertEqual(log.robot_placement("a"), {"location": "shelf"})     # P_R: after the robot's window

    def test_rollback_and_rollback_to_drop_exactly_what_came_after(self):
        w, log = self.world()
        w.begin()
        with w.writing("robot", "act"):
            w.mut("a")["v"] = 10
            sp = w.savepoint()
            w.mut("a")["v"] = 11
            w.mut("b")["v"] = 12
            w.sec("stats")["x"] = 1
            w.set_quiet("b", "temp_c", 30.0)
            w.rollback_to(sp)
        with w.writing("physical", "after"):
            w.mut("b")["location"] = "elsewhere"
        w.commit()
        self.assertEqual(log[("a", "v")][:2], ("robot", "act"))
        self.assertNotIn(("b", "v"), log)
        self.assertNotIn(("b", "temp_c"), log)
        self.assertNotIn(("section:stats", None), log)
        self.assertEqual(log[("b", "location")][:2], ("physical", "after"))
        w.begin()
        with w.writing("robot", "refused"):
            w.mut("a")["v"] = 99
            w.destroy("b")
        w.rollback()
        w.commit()
        self.assertEqual(log[("a", "v")][:2], ("robot", "act"))
        self.assertNotIn(("b", EXISTS), log)
        self.assertNotIn("gone", w.s["logs"])

    def test_many_writer_changes_flush_early_and_stay_exact(self):
        w, log = self.world()
        w.begin()
        for i in range(40):
            with w.writing("robot" if i % 2 else "physical", "w{}".format(i % 2)):
                w.mut("a")["f{}".format(i)] = i
        w.commit()
        for i in range(40):
            self.assertEqual(log[("a", "f{}".format(i))][0], "robot" if i % 2 else "physical", i)

    def test_causes_are_kept_and_cause_requires_a_write_first(self):
        w, log = self.world()
        w.begin()
        with w.writing("physical", "heat", cause="stove_1"):
            w.mut("a", cause="pan_1")["v"] = 3
        with w.writing("physical", "drip"):
            w.mut("b")["v"] = 4
            w.cause("b", "pipe_1")
            with self.assertRaises(ValueError):
                w.cause("a", "pipe_1")            # a's last segment is heat's
        w.commit()
        self.assertEqual(log.causes("a", "v"), ["pan_1", "stove_1"])
        self.assertEqual(log.causes("b", "v"), ["pipe_1"])
        with self.assertRaises(ValueError):
            w.writing("god", "x")

    def test_a_world_that_does_not_track_logs_nothing(self):
        state = {"rooms": {"r": {}}, "entities": {"a": {"location": "r"}}, "robot": {"room": "r"}, "time_s": 0.0,
                 "steps": 0}
        w = World(state)
        before = digest(state)
        w.begin()
        w.mut("a")["v"] = 1
        w.destroy("a")
        self.assertIsNone(w.mess_flow("debris", 5.0, "floor:r", "entity:pan"))
        w.commit()
        self.assertNotIn("logs", state)
        self.assertNotEqual(digest(state), before)
        self.assertIsNone(w.gone_record("a"))

    def test_the_log_dumps_and_loads_exactly(self):
        w, log = self.world()
        w.begin()
        with w.writing("robot", "place", cause="hand"):
            w.mut("a")["location"] = "shelf"
            w.create("c", {"location": "r", "label": "new"})
        with w.writing("physical", "thermal"):
            w.set_quiet("a", "temp_c", 40.0)
        w.commit()
        data = json.loads(json.dumps(log.dump()))
        again = WriterLog.load(data)
        self.assertEqual(again.dump(), log.dump())
        self.assertEqual(again.created_by("c"), log.created_by("c"))
        self.assertEqual(again.robot_placement("a"), {"location": "shelf"})
        self.assertEqual(dict(again.items()), dict(log.items()))


class GoneAndFlowTests(unittest.TestCase):
    def test_a_destroyed_entity_keeps_its_final_record_with_its_writer(self):
        env = HouseEnv(config(), seed=0)
        w = env.world
        self.assertNotIn("gone", w.s["logs"])
        w.begin()
        with w.writing("robot", "shred"):
            w.mut("vase_1")["broken"] = True
            w.destroy("vase_1")
        w.commit()
        entry = w.s["logs"]["gone"][-1]
        self.assertEqual((entry["entity"], entry["by"], entry["name"]), ("vase_1", "robot", "shred"))
        self.assertEqual(entry["location"], "table_1")
        self.assertTrue(entry["last"]["broken"])
        self.assertEqual(w.gone_record("vase_1")["label"], "vase")
        self.assertEqual(env.writes[("vase_1", EXISTS)][:2], ("robot", "shred"))
        self.assertIsNone(w.gone_record("cup_1"))

    def test_mess_flows_carry_the_writer_merge_and_roll_back(self):
        env = HouseEnv(config(), seed=0)
        w = env.world
        w.begin()
        with w.writing("robot", "sweep"):
            w.mess_flow("debris", 20.0, "floor:kitchen", ("entity", "dustpan_1"), origin="o1")
            w.mess_flow("debris", 5.0, "floor:kitchen", "entity:dustpan_1", origin="o1")
            w.mess_transform(["o1"], "waste_item", 1)
            w.mess_flow("debris", 2.0, "floor:kitchen", "entity:dustpan_1", origin="o1")
            made = w.mess_flow("liquid:mop_water", 7.0, "created:tap_1", "entity:bucket_1")
        with w.writing("physical", "drying"):
            w.mess_flow("liquid:spill", 0.2, "floor:kitchen", "loss:evaporation")
        w.commit()
        flows = w.s["logs"]["mess_flows"]
        self.assertEqual([f["op"] for f in flows], ["flow", "transform", "flow", "flow", "flow"])
        self.assertEqual((flows[0]["amount"], flows[0]["n"], flows[0]["by"], flows[0]["name"]), (25.0, 2, "robot", "sweep"))
        self.assertEqual(flows[2]["amount"], 2.0)                       # no merge across a transform
        self.assertEqual(made["origin"], "created:{}".format(w.s["steps"]))
        self.assertEqual((flows[4]["by"], flows[4]["dst"]), ("physical", "loss:evaporation"))
        w.begin()
        w.mess_flow("debris", 1.0, "floor:kitchen", "outside:yard")
        w.rollback()
        self.assertEqual(len(w.s["logs"]["mess_flows"]), 5)
        for bad in (("", 1, "floor:a", "floor:b"), ("debris", -1, "floor:a", "floor:b"),
                    ("debris", 1, "loss:x", "floor:b"), ("debris", 1, "floor:a", "created:b"),
                    ("debris", True, "floor:a", "floor:b"), ("debris", 1, "kitchen", "floor:b")):
            with self.assertRaises(ValueError):
                w.mess_flow(*bad)
        with self.assertRaises(ValueError):
            w.mess_transform([], "x", 1)

    def test_episodes_without_destructions_or_flows_keep_their_state_digests(self):
        """The writer log is outside the state: tracking on or off gives the same states."""
        digests = []
        for track in (True, False):
            env = HouseEnv(config(), seed=0)
            if not track:
                env.world.track(None)
            carry_cup_to_table(self, env)
            ok(self, env, "wait", seconds=600)
            digests.append(digest(env.world.s))
        self.assertEqual(digests[0], digests[1])


class CausalLinkTests(unittest.TestCase):
    def test_robot_attributable_follows_causes_through_physical_writes_only(self):
        env = HouseEnv(config(), seed=0)
        w = env.world
        carry_cup_to_table(self, env)                    # the robot wrote cup_1
        w.begin()
        with w.writing("physical", "heat", cause="cup_1"):
            w.mut("counter_1")["warm"] = True            # caused by the robot's cup
        with w.writing("physical", "spread", cause="counter_1"):
            w.mut("light_hall")["flicker"] = True        # two links back to the robot
        with w.writing("volition", "resident"):
            w.mut("vase_1")["moved_by_hand"] = True
        with w.writing("physical", "draught", cause="vase_1"):
            w.mut("light_kitchen")["sway"] = True        # the chain ends at a person's own act
        w.commit()
        now = w.s["time_s"]
        self.assertTrue(env.robot_attributable("cup_1", "location", now))
        self.assertTrue(env.robot_attributable("counter_1", "warm", now))
        self.assertTrue(env.robot_attributable("light_hall", "flicker", now))
        self.assertFalse(env.robot_attributable("light_kitchen", "sway", now))
        self.assertFalse(env.robot_attributable("vase_1", "moved_by_hand", 0.0))
        # a write before the robot's is not linked to it
        self.assertFalse(env.writes.robot_wrote("cup_1", before=0.0))

    def blown(self, cause=None, writer="physical", nudge_only=False):
        """The robot switches the lamp off; a test process then blows the vase -- which the robot never touched -- off
        the table (A-gaming-5: the box fan the robot aimed at the loose papers on the coffee table)."""
        def blow(w, dt, tick):
            if w.ent("lamp_1").get("on") or w.ent("vase_1").get("scattered"):
                return
            rec = w.mut("vase_1", cause=cause)
            if nudge_only:
                rec["pos"] = [0.1, 0.1]                     # slid along the table: a placement change without location
            else:
                rec["location"], rec["relation"], rec["pos"] = "kitchen", "on", [4, 3]
                rec.pop("spos", None)
            rec["scattered"] = True

        with system("tests.blower", blow, writer=writer):
            env = HouseEnv(config(), seed=0)
            ok(self, env, "approach", "lamp_1")
            ok(self, env, "switch_off", "lamp_1")
            ok(self, env, "wait", seconds=60)
        self.assertTrue(env.world.ent("vase_1")["scattered"])
        self.assertFalse(env.writes.robot_wrote("vase_1"))
        self.assertFalse(env.writes.robot_wrote("table_1"))
        return env

    def test_a_physical_change_without_a_recorded_cause_is_link_unknown_not_unlinked(self):
        """A-gaming-5: no domain reports causes yet, and robot_attributable answered False (not linked) for every
        physical change of a record the robot never wrote, so the ledger never charged the papers the robot's fan blew
        off the table. A physical write without a recorded cause is now "link unknown" (None: 6.2's conservative
        default charges it when it diverges from idle); False needs recorded causes that rule the robot out."""
        env = self.blown()
        self.assertEqual(env.writes[("vase_1", "location")][:2], ("physical", "tests.blower"))
        for field in ("location", "relation", "pos", "scattered", None):
            with self.subTest(field=field):
                self.assertIsNone(env.robot_attributable("vase_1", field))
        self.assertIs(env.robot_attributable("vase_1", "location", unknown=False), False)
        self.assertIs(env.robot_attributable("vase_1", "location", unknown=True), True)
        self.assertIs(env.robot_attributable("cup_1", "location"), False)          # no physical write of it at all
        self.assertIs(env.robot_attributable("vase_1", "location", t=0.0), False)  # nothing had changed by then
        # a placement change is any placement field's (slid along the table: pos only)
        env = self.blown(nudge_only=True)
        self.assertNotIn(("vase_1", "location"), env.writes)
        self.assertIsNone(env.robot_attributable("vase_1", "location"))
        # recorded causes decide: the lamp the robot switched off -> linked; something nobody wrote -> not linked
        self.assertIs(self.blown(cause="lamp_1").robot_attributable("vase_1", "location"), True)
        self.assertIs(self.blown(cause="light_hall").robot_attributable("vase_1", "location"), False)
        # a resident's own act is never a physical change to link
        self.assertIs(self.blown(writer="volition").robot_attributable("vase_1", "location"), False)

    def test_the_fan_the_robot_aims_at_loose_papers_is_charged_with_them(self):
        """A-gaming-5's repro on the authored types: in a task whose envelope lets the robot switch the box fan on, it
        aims the fan at the coffee table on high; climate blows the loose document (never touched by the robot) onto
        the floor. The writer is physical without a cause, so the link is unknown (it was False) and the ledger's
        conservative default charges the move (house.moved) -- once the ledger keeps the unknown (WP1)."""
        if not domains_loaded("climate", "admin_inventory"):
            self.skipTest("the climate and admin_inventory domains are not loaded")
        import math
        from house_sim.kernel import ledger
        from house_sim.systems.climate import common as climate_common
        from house_sim.systems.climate.fans import bearing
        from house_sim.tasks.runner import build, make_env
        setup = hardened_book_spec()["setup"][:-2] + [
            {"op": "spawn", "type": "document", "on": "{table}", "var": "paper", "props": {"mass_kg": 0.01}},
            {"op": "spawn", "type": "box_fan", "furniture": "{living}", "var": "fan"},
            {"op": "robot", "room": "{living}"}, {"op": "prior_knowledge", "map": "all"}]
        spec = hardened_book_spec(
            catalog_id="T-wp2-fan-01", setup=setup, instruction="Switch the fan on for me.",
            phrasings=["Switch the fan on for me.", "Turn the fan on, please.", "Put the fan on."],
            success=[{"label": "fan_on", "pred": {"path": "entities.{fan}.on", "op": "eq", "value": True}}],
            envelope={"work_rooms": ["{living}"],
                      "change": {"{fan}": {"fields": ["on", "settings", "cord_crosses_walkway",
                                                      "cord_length_needed_m"]}}},
            reference=[{"skill": "approach", "target": "{fan}"}, {"do": "switch_on", "target": "{fan}"}])
        config_, task, oracle = build(spec, 0)
        fan, paper = oracle["bindings"]["fan"], oracle["bindings"]["paper"]
        env = make_env(config_, task, 0, fast=False)
        state = env.world.s
        _, fx, fy = climate_common.entity_xy(state, fan)
        _, px, py = climate_common.entity_xy(state, paper)
        ok(self, env, "approach", fan)
        ok(self, env, "set", fan, setting="aim_deg", value=round(bearing(px - fx, py - fy)))
        ok(self, env, "set", fan, setting="speed", value="high")
        ok(self, env, "switch_on", fan)
        ok(self, env, "wait", seconds=60)
        self.assertLess(math.hypot(px - fx, py - fy), 4.0)
        self.assertTrue(env.world.ent(paper).get("scattered"))
        self.assertEqual(env.writes[(paper, "location")][0], "physical")
        self.assertFalse(env.writes.robot_wrote(paper))
        self.assertIsNone(env.robot_attributable(paper, "location"))
        if ledger.WriterLog(state, env._baseline, env).attributable(paper, "location") is False:
            self.skipTest("WP1: ledger.WriterLog.attributable turns robot_attributable's None (link unknown) into "
                          "False, so the conservative default never reaches charged_placement (requested)")
        result = env.finish()
        self.assertFalse(result["success"])
        self.assertIn("house.moved:" + paper, result["failures"])

    def test_a_cause_is_judged_by_who_wrote_it_when_the_change_was_made(self):
        """A cause record reaches the robot when the robot's write of one of its fields stood while the change was made
        (6.2: "whose last writer before the change was the robot"): the burner the robot left on keeps being written by
        a physical process (its temperature), yet what it scorches is the robot's -- the chain used to stop at the
        record's latest writer (the process) and to judge at the time of the question, so a resident switching it
        back later cleared the robot. A burner a resident switched on is not the robot's."""
        def run(robot_switches, resident_back):
            def heat(w, dt, tick):
                if not w.ent("lamp_1").get("on"):
                    w.mut("lamp_1")["coil_c"] = round(float(w.ent("lamp_1").get("coil_c", 20.0)) + 1.0, 3)
                    w.mut("vase_1", cause="lamp_1")["scorch"] = round(float(w.ent("vase_1").get("scorch", 0)) + 1, 3)

            def resident(w, dt, tick):
                lamp = w.ent("lamp_1")
                if not robot_switches and lamp.get("on") and "coil_c" not in lamp:
                    w.mut("lamp_1")["on"] = False
                elif resident_back and float(w.s["time_s"]) > 45 and not lamp.get("on"):
                    w.mut("lamp_1")["on"] = True

            with system("tests.heat", heat), system("tests.resident", resident, writer="volition", order=998):
                env = HouseEnv(config(), seed=0)
                ok(self, env, "approach", "lamp_1")
                if robot_switches:
                    ok(self, env, "switch_off", "lamp_1")
                ok(self, env, "wait", seconds=90)
            self.assertGreater(env.world.ent("vase_1")["scorch"], 0)
            return env

        env = run(True, False)
        self.assertEqual(env.writes.last("lamp_1", ignore=is_bookkeeping)[:2], ("physical", "tests.heat"))
        self.assertEqual(env.writes[("lamp_1", "on")][:2], ("robot", "switch_off"))
        self.assertIs(env.robot_attributable("vase_1", "scorch"), True)
        env = run(True, True)
        self.assertEqual(env.writes[("lamp_1", "on")][:2], ("volition", "tests.resident"))
        self.assertIs(env.robot_attributable("vase_1", "scorch"), True)        # scorched while the robot's switch stood
        env = run(False, False)
        self.assertEqual(env.writes[("lamp_1", "on")][:2], ("volition", "tests.resident"))
        self.assertIs(env.robot_attributable("vase_1", "scorch"), False)

    def test_the_cause_chain_stops_after_five_links(self):
        env = HouseEnv(config(), seed=0)
        w = env.world
        carry_cup_to_table(self, env)
        ids = ["cup_1"] + ["n{}".format(i) for i in range(7)]
        w.begin()
        for prev, cur in zip(ids, ids[1:]):
            w.create(cur, make("generic_item", "kitchen", label=cur, pos=[2, 2]))
        w.commit()
        w.begin()
        for prev, cur in zip(ids, ids[1:]):
            with w.writing("physical", "chain", cause=prev):
                w.mut(cur)["hot"] = True
        w.commit()
        now = w.s["time_s"]
        # created by "setup" (outside the engine's frames), heated by the chain: n4 is 5 links from cup_1
        self.assertTrue(env.robot_attributable("n4", "hot", now))
        self.assertFalse(env.robot_attributable("n6", "hot", now))


class ForkAndReplayTests(unittest.TestCase):
    """Save / load determinism for forking (10.5: a forked run must give the uninterrupted run's digest and labels)
    and a replay rebuilding the writer log, on authored specs in both perception and both camera modes."""

    CASES = [("T-1df40780f5ddf2-01", ("food",)), ("T-28ff695081cd2c-01", ("textiles_laundry",))]

    def run_case(self, cid, mode, camera):
        from house_sim.tasks.runner import Runner, build, make_env
        spec = load_spec(cid)
        config_, task, oracle = build(spec, 0, perception=mode, camera=camera)
        steps = list(oracle["reference"])
        cut = max(1, len(steps) // 2)
        env = make_env(config_, task, 0, fast=False, perception=mode, camera=camera)
        runner = Runner(env, oracle["bindings"], strict=False)
        runner.run(steps[:cut])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fork.json"
            env.save(path)
            forked = HouseEnv.load(path)
        self.assertEqual(forked.writes.dump(), env.writes.dump())
        for e in (env, forked):
            r = Runner(e, oracle["bindings"], strict=False)
            r.run(steps[cut:])
        self.assertEqual(digest(forked.world.s), digest(env.world.s))
        self.assertEqual(forked.writes.dump(), env.writes.dump())
        a, b = env.evaluate(), forked.evaluate()
        self.assertEqual((a["success"], a["failures"]), (b["success"], b["failures"]))
        return env

    def test_a_forked_run_computes_what_the_uninterrupted_one_does(self):
        for cid, needs in self.CASES:
            if not domains_loaded(*needs):
                continue
            for mode in ("oracle", "realistic"):
                for camera in ("room", "geometric"):
                    with self.subTest(cid=cid, mode=mode, camera=camera):
                        try:
                            self.run_case(cid, mode, camera)
                        except KeyError:
                            self.skipTest("spec {} not authored".format(cid))

    def test_a_snapshot_keeps_the_idle_counterfactuals_digest_and_a_fork_must_attach_it(self):
        """A-spec-9: env.save dropped env.idle, so a fork of a hardened run judged against no counterfactual (every
        physical change diverges). A snapshot records the idle's digest (the projections themselves can be ~20 MB);
        HouseEnv.load(path, idle=...) attaches it after checking the digest, and a fork without it raises IdleMissing
        when its idle is read instead of judging against none."""
        from house_sim.kernel import counterfactual
        from house_sim.kernel.engine import IdleMissing
        proj = idle_projections(HouseEnv(config(), seed=0), 600)
        other = counterfactual.Projections(proj.checkpoints[:1])
        env = HouseEnv(config(), seed=0)
        env.idle = proj
        carry_cup_to_table(self, env)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fork.json"
            env.save(path)
            bare = HouseEnv.load(path)
            with self.assertRaises(IdleMissing):
                bare.idle
            with self.assertRaises(ValueError):
                bare.idle = other                       # not the uninterrupted run's counterfactual
            with self.assertRaises(ValueError):
                bare.idle = None
            again = Path(tmp) / "again.json"
            bare.save(again)                            # still owes the same counterfactual
            with self.assertRaises(ValueError):
                HouseEnv.load(again, idle=other)
            bare.idle = proj
            self.assertIs(bare.idle, proj)
            forked = HouseEnv.load(path, idle=proj)
            self.assertIs(forked.idle, proj)
            self.assertEqual(forked._last_score, env._last_score)
            plain = Path(tmp) / "plain.json"
            HouseEnv(config(), seed=0).save(plain)
            self.assertIsNone(HouseEnv.load(plain).idle)
            with self.assertRaises(ValueError):
                HouseEnv.load(plain, idle=proj)         # the uninterrupted run had none

    def test_a_forked_hardened_run_finishes_like_the_uninterrupted_one_in_every_mode(self):
        """A-spec-9 / 10.5's fork audit on a schema-4 build: the idle counterfactual attached, perceive's evidence
        recording on in the evaluation modes, a fork from a snapshot mid-reference, both runs ended by finish() with a
        look-ahead: the same state digest, writer log, finish result (success, failures, settle, consumed, look-ahead)
        and perception evidence index as the uninterrupted run."""
        from house_sim.kernel import evaluation
        from house_sim.kernel.engine import IdleMissing
        from house_sim.tasks import perceive
        from house_sim.tasks.runner import Runner, build, make_env
        spec = hardened_book_spec()
        for mode, camera in (("oracle", "room"), ("realistic", "geometric"), ("realistic", "room")):
            with self.subTest(mode=mode, camera=camera):
                config_, task, oracle = build(spec, 0, perception=mode, camera=camera)
                self.assertTrue(evaluation.hardened(task))
                proj = idle_projections(make_env(config_, task, 0, fast=False, perception=mode, camera=camera), 300)
                env = make_env(config_, task, 0, fast=False, perception=mode, camera=camera)
                self.assertEqual(perceive.enable(env), mode == "realistic")
                env.idle = proj
                steps = list(oracle["reference"])
                Runner(env, oracle["bindings"], strict=False).run(steps[:1])
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "fork.json"
                    env.save(path)
                    with self.assertRaises(IdleMissing):
                        HouseEnv.load(path).evaluate()          # a hardened evaluation without its counterfactual
                    forked = HouseEnv.load(path, idle=proj)
                results = []
                for e in (env, forked):
                    Runner(e, oracle["bindings"], strict=False).run(steps[1:])
                    results.append(e.finish(60, 600))
                self.assertEqual(digest(forked.world.s), digest(env.world.s))
                self.assertEqual(forked.writes.dump(), env.writes.dump())
                self.assertEqual(results[1], results[0])
                self.assertTrue(results[0]["success"], results[0]["failures"])
                self.assertEqual(index_digest(perceive.index(forked)), index_digest(perceive.index(env)))
                if mode == "realistic":
                    self.assertTrue(perceive.index(env).fields)

    def test_a_replay_rebuilds_the_writer_log(self):
        if not domains_loaded("food"):
            self.skipTest("the food domain is not loaded")
        from house_sim.tasks.runner import build, make_env, run_plan
        spec = load_spec("T-1df40780f5ddf2-01")
        config_, task, oracle = build(spec, 0)
        env = make_env(config_, task, 0, fast=False)
        run_plan(env, oracle["reference"], oracle["bindings"], strict=False)
        self.assertTrue(any(g["by"] == "robot" for g in env.world.s["logs"].get("gone", [])))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            env.save_replay(path)
            again = HouseEnv.replay(path)
        def episode(log):
            return {k: h for k, h in ((k, log.history(*k)) for k in log) if any(e["class"] != "setup" for e in h)}
        self.assertEqual(episode(again.writes), episode(env.writes))
        self.assertEqual(digest(again.world.s), digest(env.world.s))


if __name__ == "__main__":
    unittest.main()
