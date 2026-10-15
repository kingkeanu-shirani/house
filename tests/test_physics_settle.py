"""Tier-1 physics integration (PHYS-INT, kernel v3.0): the World touch log, the settle pass in the engine, attribution,
notes and the undo rule, the deferred laws switched on with lawbook.forced, the Ctx helpers and motion records, the
sounds observer and the physics inspector, determinism and replays, and the cost budgets
(docs/design/TIER1_PHYSICS.md 6.1, 7.2, 7.6, 9, 11, 12.5; docs/design/TIER1_INTEGRATION.md 1.4, 1.8-1.10).

Every scene is built from core types (and generic items with explicit physical fields), so the file passes with
HOUSE_SIM_DOMAINS=core as well as with every domain loaded, normal and HOUSE_SIM_STRICT=1.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path

from house_sim.kernel import plugins
from house_sim.kernel.actions import Ctx, motion_record, motion_records
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.phys import impact, lawbook, observe, settle, support
from house_sim.kernel.predicates import MISSING, digest
from house_sim.kernel.registry import default_registry
from house_sim.kernel.world import World

ROOT = Path(__file__).resolve().parent.parent
REG = default_registry()


# ----- scenes -----------------------------------------------------------------------------------------------------------

def room(floor, size, exits, **extra):
    out = {"label": extra.pop("label", "room"), "size_m": list(size), "floor_material": floor, "exits": list(exits)}
    out.update(extra)
    return out


def make(type_name, location, **props):
    return REG.make(type_name, location, **props)


def item(location, **props):
    """A generic item with explicit physical fields (phys_exact: no per-instance variation)."""
    props.setdefault("phys_exact", True)
    return REG.make("generic_item", location, **props)


def glass(location, **props):
    base = dict(label="drinking glass", material="glass", mass_kg=0.25, size_m=[0.08, 0.08, 0.12], shape="cylinder")
    base.update(props)
    return item(location, **base)


def egg(location, **props):
    base = dict(label="egg", material="eggshell", mass_kg=0.06, size_m=[0.045, 0.045, 0.06])
    base.update(props)
    return item(location, **base)


def mug(location, **props):
    base = dict(label="mug", material="ceramic", mass_kg=0.35, size_m=[0.09, 0.09, 0.1], shape="cylinder",
                capacity_l=0.35, container=True, accepts_items=True)
    base.update(props)
    return item(location, **base)


def config(entities=None, floor="tile", robot=None, events=None, **rooms_extra):
    """kitchen (5 x 4 m, lit) - hall (3 x 2 m, hardwood, lit) - yard (6 x 6 m, grass, outdoor)."""
    cfg = {"schema_version": 2, "start_room": "kitchen",
           "rooms": {"kitchen": room(floor, (5.0, 4.0), ["hall"], type="kitchen", label="kitchen"),
                     "hall": room("hardwood", (3.0, 2.0), ["kitchen", "yard"], type="hallway", label="hall"),
                     "yard": room("grass", (6.0, 6.0), ["hall"], type="backyard", label="yard", outdoor=True)},
           "entities": {}, "robot": dict(robot or {"room": "kitchen", "pos": [1, 1]}), "people": {}, "pets": {},
           "events": list(events or [])}
    cfg["rooms"]["kitchen"].update(rooms_extra)
    for rid in ("kitchen", "hall"):
        cfg["entities"]["light_" + rid] = make("generic_item", rid, label="ceiling light", category="light", on=True,
                                               mounted=True, portable=False, mount_height_m=2.4, pos=[0, 0])
    for eid, record in (entities or {}).items():
        cfg["entities"][eid] = record
    return cfg


def env_of(entities=None, perception="oracle", **kw):
    return HouseEnv(config(entities, **kw), seed=0, perception=perception)


def table_scene(**extra):
    scene = {"table": make("table", "kitchen", pos=[2, 1])}
    scene.update(extra)
    return scene


def ok(test, env, *args, **kw):
    result = env.step(*args, **kw)
    test.assertTrue(result["ok"], (args, kw, result["message"]))
    return result


def phys_events(env, kind=None):
    return [e for e in env.world.s["event_log"] if str(e.get("kind")).startswith("phys.")
            and (kind is None or e["kind"] == kind)]


# ----- the World's transient bookkeeping ------------------------------------------------------------------------------------

def tiny_world():
    state = {"rooms": {"r": {"size_m": [4.0, 4.0]}}, "entities": {"a": {"location": "r"}, "b": {"location": "r"}},
             "robot": {"room": "r", "pos": [0, 0], "hands": {}}, "steps": 0, "time_s": 0.0, "stats": {}}
    w = World(state)
    w.begin()
    return w


class WorldTouchLogTests(unittest.TestCase):
    def test_every_entity_write_is_a_touch_and_only_entity_writes(self):
        w = tiny_world()
        w.mut("a")
        w.mut("a")                                        # journaled once, touched twice
        w.create("c", {"location": "r"})
        w.destroy("b")
        w.room_m("r")["lit"] = True
        w.set_scalar("time_s", 1.0)
        w.sec("stats")["x"] = 1
        self.assertEqual(w._touched, ["a", "a", "c", "b"])
        self.assertEqual(w.journal_before("entities", "c"), MISSING)
        self.assertEqual(w.journal_before("entities", "a"), {"location": "r"})
        self.assertIsNone(w.journal_before("entities", "zz"))

    def test_mut_quiet_journals_without_touching(self):
        w = tiny_world()
        count = w.mutation_count
        w.mut_quiet("a")["temperature_c"] = 30.0
        self.assertEqual(w._touched, [])
        self.assertEqual(w.mutation_count, count + 1)
        self.assertIn(("entities", "a"), w.journal_keys())
        w.rollback()
        self.assertNotIn("temperature_c", w.s["entities"]["a"])        # rolled back like any journaled write

    def test_mutation_count_counts_every_journaled_write_and_rollback_and_never_drops(self):
        w = tiny_world()
        seen = [w.mutation_count]
        for write in (lambda: w.mut("a"), lambda: w.room_m("r"), lambda: w.sec("stats"), lambda: w.robot_m(),
                      lambda: w.set_scalar("steps", 1), lambda: w.set_path("entities.a.x", 1),
                      lambda: w.create("c", {"location": "r"}), lambda: w.destroy("c"), w.rollback):
            write()
            seen.append(w.mutation_count)
        self.assertEqual(seen, sorted(seen))
        self.assertTrue(all(b > a for a, b in zip(seen, seen[1:])), seen)

    def test_begin_and_rollback_clear_the_step_bookkeeping(self):
        w = tiny_world()
        w.mut("a")
        w.phys_scratch["x"] = 1
        w._power_down_step = 3
        w.touched_since(0, "settle")
        w.begin()
        self.assertEqual((w._touched, w.phys_scratch, w._power_down_step), ([], {}, None))
        self.assertEqual(w._touch_readers, {"settle": 0})
        w.mut("b")
        w.phys_scratch["y"] = 2
        w.rollback()
        self.assertEqual((w._touched, w.phys_scratch), ([], {}))

    def test_cursors_are_absolute_and_trimming_keeps_unread_touches(self):
        w = tiny_world()
        w.mut("a")
        ids, cursor = w.touched_since(0, "settle")
        self.assertEqual((ids, cursor), (["a"], 1))
        w.mut("b")
        w.touched_since(0, "monitor")                      # a second reader that read everything
        w.mut("a")
        w.trim_touched()                                   # settle has read 1, monitor 2: the first touch goes
        self.assertEqual(w._touched, ["b", "a"])
        self.assertEqual(w.touched_since(cursor)[0], ["b", "a"])
        ids, cursor = w.touched_since(cursor, "settle")
        self.assertEqual(cursor, 3)
        w.touched_since(0, "monitor")
        w.trim_touched()
        self.assertEqual(w._touched, [])
        self.assertEqual(w.touched_since(3), ([], 3))

    def test_the_touch_log_stays_bounded_over_a_week_long_wait(self):
        """Risk 11 of TIER1_INTEGRATION: without trim_touched a week-long wait would pile up ids. A leaking jug
        touches itself every substep for the whole week; the log never holds more than a substep's touches."""
        scene = table_scene(tank=item("table", relation="on", spos=[0.1, 0.1], label="water tank", material="plastic",
                                      mass_kg=2.0, size_m=[0.4, 0.3, 0.5], capacity_l=200.0, liquid="water",
                                      liquid_l=200.0, container=True, accepts_items=True))
        env = env_of(scene)
        from house_sim.kernel.phys import liquids
        env.world.begin()
        self.assertTrue(liquids.leak_start(env.world, "tank", l_per_h=0.5))
        env.world.commit()
        peaks = []

        def probe(w, dt, tick):
            peaks.append(len(w._touched))
        entry = (999, "test.touch_probe", 30.0, probe)
        plugins.SYSTEMS.append(entry)
        self.addCleanup(plugins.SYSTEMS.remove, entry)
        ok(self, env, "wait", seconds=7 * 24 * 3600)
        self.assertEqual(len(peaks), 7 * 24 * 120)
        self.assertLessEqual(max(peaks), 16)
        self.assertLess(env.world.s["entities"]["tank"]["liquid_l"], 200.0 - 80.0)     # it really leaked all week


# ----- the settle pass in the engine ---------------------------------------------------------------------------------------

class SettleEngineTests(unittest.TestCase):
    def test_an_action_marks_what_it_changed_and_nothing_else(self):
        env = env_of(table_scene(book=make("book", "table", relation="on", spos=[0.1, 0.1]),
                                 other=make("book", "table", relation="on", spos=[0.5, 0.1])))
        ok(self, env, "approach", "table")
        ok(self, env, "pick_up", "book")
        entities = env.world.s["entities"]
        self.assertTrue(entities["book"].get("_robot_moved"))
        self.assertNotIn("_robot_moved", entities["other"])
        self.assertNotIn("_robot_moved", entities["table"])
        ok(self, env, "wait", seconds=3600)               # systems and substeps never mark anything
        self.assertNotIn("_robot_moved", env.world.s["entities"]["other"])
        self.assertNotIn("_robot_moved", env.observe()["visible_entities"]["book"])     # an underscore field: unseen

    def test_thermal_writes_are_no_touches(self):
        env = env_of(table_scene(hot=mug("table", relation="on", spos=[0.1, 0.1], temperature_c=90.0)))
        ok(self, env, "wait", seconds=240)
        self.assertLess(env.world.s["entities"]["hot"]["temperature_c"], 90.0)           # it cooled ...
        self.assertNotIn("_robot_moved", env.world.s["entities"]["hot"])
        self.assertEqual(env.world._touched, [])                                         # ... quietly

    def test_a_refused_action_settles_nothing(self):
        env = env_of(table_scene(e=egg("kitchen", pos=[1, 2])))
        before = digest(env.world.s["entities"])
        result = env.step("place", "e", "table")
        self.assertFalse(result["ok"])
        self.assertEqual(digest(env.world.s["entities"]), before)
        self.assertEqual(phys_events(env), [])

    def test_settling_again_changes_nothing(self):
        env = env_of(table_scene(e=egg("kitchen", pos=[1, 2])))
        ok(self, env, "set_speed", "fast")
        for command in (("approach", "e"), ("crouch",), ("pick_up", "e"), ("stand",), ("approach", "table")):
            ok(self, env, *command)
        ok(self, env, "place", "e", "table")
        before = digest(env.world.s)
        env.world.begin()
        self.assertEqual(settle.settle(env, "substep"), [])
        self.assertEqual(settle.settle(env, "substep"), [])
        env.world.commit()
        self.assertEqual(digest(env.world.s), before)

    def test_a_set_down_break_is_a_note_and_cannot_be_undone(self):
        counter = make("counter", "kitchen", pos=[2, 1])
        for speed, breaks in (("normal", False), ("fast", True)):
            env = env_of({"counter": counter, "e": egg("kitchen", pos=[1, 2])})
            ok(self, env, "set_speed", speed)
            for command in (("approach", "e"), ("crouch",), ("pick_up", "e"), ("stand",), ("approach", "counter")):
                ok(self, env, *command)
            undo_before = len(env._undo)
            with lawbook.forced(setdown=True):                          # deferred until manipulation and food adopt it
                result = ok(self, env, "place", "e", "counter")
            self.assertEqual(bool(env.world.s["entities"]["e"].get("broken")), breaks, speed)
            self.assertEqual("e broke" in result["message"], breaks, result["message"])
            self.assertEqual(len(env._undo), undo_before + (0 if breaks else 1), speed)
            if breaks:
                self.assertEqual(env.world.s["stats"]["breakages"], 1)        # the robot's doing: a harm flag
                self.assertTrue(env.world.s["entities"]["e"]["shattered"])
                self.assertEqual([e["by"] for e in phys_events(env, "phys.break")], ["robot"])

    def test_the_set_down_law_is_deferred_so_one_fast_set_down_counts_once(self):
        """Stage-B review (physics, medium x2): with the set-down law active, a fast set-down of a fragile item on stone
        was counted twice (manipulation's after_placement chip plus the kernel break: stats.damage 2 for one event),
        and food kept showing the kernel-broken egg as intact. The law is deferred (TIER1_PHYSICS 10.1: only laws no
        domain duplicates are active) until manipulation and food adopt it."""
        from house_sim.kernel.phys import lawbook as book
        self.assertFalse(book.active("setdown"))
        self.assertEqual(set(book.LAWS["setdown"]["duplicated_by"]), {"manipulation", "food"})
        counter = make("counter", "kitchen", pos=[2, 1], material="stone")
        env = env_of({"counter": counter, "e": egg("kitchen", pos=[1, 2], fragile=True)})
        ok(self, env, "set_speed", "fast")
        for command in (("approach", "e"), ("crouch",), ("pick_up", "e"), ("stand",), ("approach", "counter")):
            ok(self, env, *command)
        result = ok(self, env, "place", "e", "counter")
        rec = env.world.s["entities"]["e"]
        stats = env.world.s["stats"]
        self.assertFalse(rec.get("broken") or rec.get("shattered"), result["message"])
        self.assertNotIn("e broke", result["message"])
        self.assertEqual((stats.get("breakages", 0), phys_events(env, "phys.break")), (0, []))
        self.assertLessEqual(stats.get("damage", 0), 1)                 # manipulation's chip alone, when loaded
        if "house_sim.systems.manipulation" in sys.modules:
            self.assertEqual(stats.get("damage", 0), 1)
            self.assertTrue(rec.get("chipped"))

    def test_an_event_in_minute_two_lands_in_its_substep_and_is_exogenous(self):
        """The settle pass runs after every 30 s substep: a knock at 90 s lands at 90 s, not at the end of the hour,
        and a scripted event never raises the robot's harm flags for a glass it never touched."""
        scene = table_scene(g=glass("table", relation="on", spos=[1.13, 0.36]))
        env = env_of(scene, events=[{"at_s": 90, "effect": "physics.bump", "args": {"target": "g", "g": 0.9}}])
        ok(self, env, "wait", seconds=3600)
        falls = phys_events(env, "phys.fall")
        self.assertEqual([(e["time_s"], e["target"], e["by"]) for e in falls], [(90.0, "g", "exogenous")])
        stats = env.world.s["stats"]
        self.assertEqual((stats["phys_breaks"], stats["breakages"]), (1, 0))
        self.assertEqual(env.world.s["entities"]["g"]["location"], "kitchen")

    def test_the_same_knock_counts_against_the_robot_once_it_placed_the_glass(self):
        scene = table_scene(g=glass("kitchen", pos=[1, 2]))
        env = env_of(scene, events=[{"at_s": 30, "effect": "physics.bump", "args": {"target": "g", "g": 0.9}}])
        for command in (("approach", "g"), ("crouch",), ("pick_up", "g"), ("stand",), ("approach", "table")):
            ok(self, env, *command)
        ok(self, env, "place", "g", "table", spos=[1.12, 0.36])
        self.assertTrue(env.world.s["entities"]["g"]["_robot_moved"])
        ok(self, env, "wait", seconds=120)
        self.assertEqual([e["by"] for e in phys_events(env, "phys.break")], ["robot"])
        self.assertEqual(env.world.s["stats"]["breakages"], 1)

    def test_a_scripted_knock_of_a_centrepiece_is_exogenous_although_the_robot_set_the_table(self):
        """Stage-B review (physics, high): the robot placing something on a table must not make a scripted bump of a
        centrepiece it never touched the robot's (the old rule counted any marker under the same top-level support,
        so the 'perturb after completion' recipe failed the task through breakages)."""
        scene = table_scene(bowl=glass("table", relation="on", spos=[1.13, 0.36]), cup=mug("kitchen", pos=[1, 2]))
        env = env_of(scene, events=[{"at_s": 600, "effect": "physics.bump", "args": {"target": "bowl", "g": 0.9}}])
        for command in (("approach", "cup"), ("crouch",), ("pick_up", "cup"), ("stand",), ("approach", "table")):
            ok(self, env, *command)
        ok(self, env, "place", "cup", "table", spos=[0.2, 0.2])
        entities = env.world.s["entities"]
        self.assertTrue(entities["cup"]["_robot_moved"])
        self.assertNotIn("_robot_moved", entities["table"])
        ok(self, env, "wait", seconds=900)
        self.assertEqual([(e["target"], e["by"]) for e in phys_events(env, "phys.fall")], [("bowl", "exogenous")])
        stats = env.world.s["stats"]
        self.assertEqual((stats["phys_breaks"], stats.get("breakages", 0), stats.get("damage", 0)), (1, 0, 0))

    def test_opening_a_cabinet_does_not_make_a_scripted_drop_inside_it_the_robots(self):
        """Stage-B review (physics, high): open / close changed the cabinet's structural signature, so the marker went
        on the cabinet and a later scripted drop of a glass inside it counted as the robot's breakage."""
        scene = {"cab": make("cabinet", "kitchen", pos=[2, 0]), "g": glass("cab", relation="in")}
        env = env_of(scene, events=[{"at_s": 600, "effect": "physics.drop", "args": {"target": "g", "height_m": 1.0}}])
        ok(self, env, "approach", "cab")
        ok(self, env, "open", "cab")
        ok(self, env, "close", "cab")
        self.assertNotIn("_robot_moved", env.world.s["entities"]["cab"])
        ok(self, env, "wait", seconds=900)
        self.assertEqual([(e["target"], e["by"]) for e in phys_events(env, "phys.break")], [("g", "exogenous")])
        self.assertEqual(env.world.s["stats"].get("breakages", 0), 0)
        # control: the same drop counts against the robot once it put the glass there itself
        scene = {"cab": make("cabinet", "kitchen", pos=[2, 0]), "g": glass("kitchen", pos=[1, 2])}
        env = env_of(scene, events=[{"at_s": 600, "effect": "physics.drop", "args": {"target": "g", "height_m": 1.0}}])
        for command in (("approach", "g"), ("crouch",), ("pick_up", "g"), ("stand",), ("approach", "cab"),
                        ("open", "cab")):
            ok(self, env, *command)
        ok(self, env, "place", "g", "cab")
        ok(self, env, "wait", seconds=900)
        self.assertEqual([(e["target"], e["by"]) for e in phys_events(env, "phys.break")], [("g", "robot")])
        self.assertEqual(env.world.s["stats"]["breakages"], 1)

    def test_a_support_failure_is_the_robots_only_when_its_own_load_is(self):
        """A collapsing shelf is the robot's when the load that made it fail carries the marker (the robot added it),
        not when the robot only put something on another shelf of the same bookcase."""
        def scene():
            return {"case": make("bookshelf", "kitchen", pos=[2, 0]),
                    "heavy": item("case", relation="in", shelf=3, mass_kg=6.0, size_m=[0.3, 0.2, 0.25]),
                    "light": item("case", relation="in", shelf=1, mass_kg=0.5, size_m=[0.1, 0.1, 0.1])}
        for marked, expected in ((None, "exogenous"), ("light", "exogenous"), ("heavy", "robot")):
            env = env_of(scene())
            w = env.world
            w.begin()
            if marked:
                w.mut(marked)["_robot_moved"] = True
            impact.collapse(w, "case", ("shelf", 3))
            collapses = [r for r in impact.consequences(w) if r["kind"] == "phys.collapse"]
            self.assertEqual([r["by"] for r in collapses], [expected], marked)
            self.assertEqual(bool(w.s["stats"].get("damage")), expected == "robot", marked)
            w.rollback()

    def test_orphans_fall_when_their_support_is_destroyed(self):
        env = env_of({"tray": make("tray", "kitchen", pos=[3, 2], phys_exact=True),
                      "g": glass("tray", relation="on", spos=[0.1, 0.1])})
        w = env.world
        w.begin()
        settle.begin_action(w)
        w.destroy("tray")
        out = settle.settle(env, "action")
        self.assertEqual([r["kind"] for r in out], ["phys.fall"])
        rec = w.s["entities"]["g"]
        self.assertEqual((rec["location"], rec["relation"]), ("kitchen", "on"))
        self.assertTrue(rec["fell"])
        self.assertEqual(out[0]["by"], "robot")                  # in the action phase
        w.rollback()

    def test_realistic_notes_name_handles_and_no_numbers(self):
        counter = make("counter", "kitchen", pos=[2, 1])
        env = env_of({"counter": counter, "e": egg("kitchen", pos=[1, 2])}, perception="realistic")
        realism = env.realism

        def do(*args, **kw):
            args = [realism.to_handles(a) for a in args]
            return ok(self, env, *args, **kw)
        do("set_speed", "fast")
        for command in (("approach", "e"), ("crouch",), ("pick_up", "e"), ("stand",), ("approach", "counter")):
            do(*command)
        with lawbook.forced(setdown=True):
            message = do("place", "e", "counter")["message"]
        handle = realism.handles.handle("e")
        self.assertIn("{} broke".format(handle), message)
        self.assertNotIn(" e ", " " + message.replace("|", " ") + " ")
        self.assertEqual([c for c in message.replace(handle, "").replace(realism.handles.handle("counter"), "")
                          if c.isdigit()], [])


# ----- deferred laws switched on ---------------------------------------------------------------------------------------------

class DeferredLawTests(unittest.TestCase):
    """lawbook.forced turns a deferred law on, and the settle pass (or the system that applies it) applies it
    (TIER1_PHYSICS 12.5). The laws are exercised at the World level, inside one action phase, so the domains' own
    legacy copies of a rule (which is why the law is deferred) cannot interfere."""

    def action(self, env, change, **laws):
        """Run change(w) inside an action phase with the laws forced on, settle, and return the records. The world
        is left as it is (each test rolls back or reads it)."""
        w = env.world
        w.begin()
        with lawbook.forced(**laws):
            settle.begin_action(w)
            change(w)
            return settle.settle(env, "action")

    def test_overload_a_shelf_loaded_past_its_rating_gives_way_onto_the_shelf_below(self):
        env = env_of({"shelf": make("shelf_unit", "kitchen", pos=[2, 0], phys_exact=True, shelf_load_kg=5.0)})
        def bricks(kg=6.0):
            return item("shelf", relation="in", shelf=2, label="bricks", material="concrete", mass_kg=kg,
                        size_m=[0.3, 0.2, 0.1], volume_l=6.0)
        out = self.action(env, lambda w: w.create("bricks", bricks()), overload=True)
        s = env.world.s
        # 6 kg on a 5 kg shelf: it gives way, the bricks land on the shelf below, which gives way too (cascade)
        self.assertEqual(s["entities"]["shelf"]["collapsed_shelves"], [0, 1, 2])
        self.assertEqual(s["entities"]["bricks"]["location"], "kitchen")
        self.assertEqual([r["slot"] for r in out if r["kind"] == "phys.collapse"],
                         [["shelf", 2], ["shelf", 1], ["shelf", 0]])
        env.world.rollback()
        out = self.action(env, lambda w: w.create("bricks", bricks(4.8)), overload=True)
        self.assertEqual(out, [])                                        # within the rating: it holds ...
        self.assertTrue(env.world.s["entities"]["shelf"]["sagging"])     # ... and sags (over 90 %)
        env.world.rollback()
        out = self.action(env, lambda w: w.create("bricks", bricks()))   # the law deferred: nothing
        self.assertEqual(out, [])
        env.world.rollback()

    def test_the_consequence_budget_stops_a_cascade_deterministically(self):
        def run():
            env = env_of({"shelf": make("shelf_unit", "kitchen", pos=[3, 0], phys_exact=True, shelf_load_kg=1.0)})

            def load(w):
                for i in range(70):
                    w.create("g{:02d}".format(i), glass("shelf", relation="in", shelf=3, size_m=[0.03, 0.03, 0.05],
                                                        mass_kg=0.05))
            out = self.action(env, load, overload=True)
            return env.world, out
        w, out = run()
        self.assertEqual(w.s["stats"].get("phys_budget_hits"), 1)
        self.assertGreater(len(out), settle.BUDGET)
        self.assertEqual(sum(1 for e in w.s["event_log"] if e["kind"] == "phys.budget"), 1)
        w2, out2 = run()
        self.assertEqual(digest(w.s), digest(w2.s))
        self.assertEqual([r["kind"] for r in out], [r["kind"] for r in out2])

    def test_edge_an_item_whose_centre_passes_the_edge_falls(self):
        env = env_of(table_scene(g=glass("table", relation="on", spos=[1.10, 0.36])))

        def nudge(w):
            w.mut("g")["spos"] = [1.17, 0.36]                            # the centre (1.21 m) past the 1.2 m edge
        out = self.action(env, nudge, edge=True)
        self.assertIn("phys.fall", [r["kind"] for r in out])
        self.assertEqual(env.world.s["entities"]["g"]["location"], "kitchen")
        env.world.rollback()
        out = self.action(env, lambda w: w.mut("g").update(spos=[1.12, 0.36]), edge=True)
        self.assertEqual(out, [])                                        # overhanging but balanced: it stays
        env.world.rollback()

    def test_open_spill_a_tipped_open_mug_empties_onto_the_table(self):
        env = env_of(table_scene(m=mug("table", relation="on", spos=[0.3, 0.3], liquid="water", liquid_l=0.3)))
        out = self.action(env, lambda w: w.mut("m").update(orientation="on_side"), open_spill=True)
        s = env.world.s
        self.assertEqual(s["entities"]["m"]["liquid_l"], 0.0)
        self.assertEqual(out[0]["kind"], "phys.spill")
        # 0.3 L spreads into a 1.5 mm film on the table top (up to 1.4 L): none reaches the floor
        self.assertAlmostEqual(s["entities"]["table"]["wetness"], round(0.3 / (1.2 * 0.8 * 1.5), 4))
        self.assertFalse(any(e.get("category") == "spill" for e in s["entities"].values()))
        env.world.rollback()

    def test_stack_a_slender_stack_topples(self):
        base = dict(size_m=[0.06, 0.06, 0.2], mass_kg=0.3, stackable=True, accepts_items=True, surface=True)
        env = env_of(table_scene(a=item("table", relation="on", spos=[0.3, 0.3], **base)))

        def stack(w):
            w.create("b", item("a", relation="on", spos=[0.0, 0.0], **base))
            w.create("c", item("b", relation="on", spos=[0.0, 0.0], size_m=[0.06, 0.06, 0.2], mass_kg=0.3))
        out = self.action(env, stack, stack=True)
        self.assertIn("phys.tip", [r["kind"] for r in out])
        env.world.rollback()

    def test_carry_a_full_open_mug_carried_fast_slops_and_careful_does_not(self):
        left = {}
        for speed in ("careful", "fast"):
            env = env_of({"m": mug("robot", relation="held", held_hand="right", liquid="water", liquid_l=0.3)},
                         robot={"room": "kitchen", "pos": [1, 1], "hands": {"left": None, "right": "m"},
                                "speed_mode": speed})
            w = env.world

            def walk(w):
                w.robot_m()["pos"] = [4, 3]
            out = self.action(env, walk, carry=True)
            left[speed] = w.s["entities"]["m"]["liquid_l"]
            if speed == "fast":
                self.assertEqual(out[0]["kind"], "phys.spill")
                self.assertEqual(out[0]["cause"], "slosh")
            w.rollback()
        self.assertEqual(left["careful"], 0.3)
        self.assertLess(left["fast"], 0.3)

    def test_wind_the_environment_system_blows_a_napkin_away(self):
        scene = {"napkin": item("yard", pos=[2, 2], label="paper napkin", material="paper", mass_kg=0.004,
                                size_m=[0.4, 0.4, 0.002]),
                 "brick": item("yard", pos=[4, 4], label="brick", material="concrete", mass_kg=2.5,
                               size_m=[0.2, 0.1, 0.06])}
        cfg = config(scene)
        cfg["weather"] = {"wind_mps": 9.0, "gust_mps": 13.5, "wind_from_deg": 270}
        env = HouseEnv(cfg, seed=0)
        system = [fn for order, name, _, fn in plugins.SYSTEMS if name == "physics.environment"]
        self.assertEqual([(o, n) for o, n, _, _ in plugins.SYSTEMS if n.startswith("physics.")],
                         [(47, "physics.environment"), (48, "physics.processes")])
        w = env.world
        w.begin()
        system[0](w, 30.0, None)                                         # deferred: nothing
        self.assertEqual(w.s["entities"]["napkin"]["pos"], [2, 2])
        with lawbook.forced(wind=True):
            system[0](w, 30.0, None)
        self.assertTrue(w.s["entities"]["napkin"]["blown_away"])
        self.assertNotEqual(w.s["entities"]["napkin"]["pos"], [2, 2])
        self.assertEqual(w.s["entities"]["brick"]["pos"], [4, 4])          # a brick stays
        w.rollback()

    def test_contact_heat_a_hot_pan_set_on_wood_marks_it_while_it_stays(self):
        env = env_of(table_scene(pan=item("kitchen", pos=[1, 2], label="pan", material="steel", mass_kg=1.5,
                                          size_m=[0.3, 0.3, 0.06], temperature_c=220.0)))

        def set_down(w):
            m = w.mut("pan")
            m.pop("pos", None)
            m.update(location="table", relation="on", spos=[0.2, 0.2])
        self.action(env, set_down, contact_heat=True)
        w = env.world
        w.commit()
        with lawbook.forced(contact_heat=True):
            ok(self, env, "wait", seconds=120)
        self.assertGreater(impact.damage_map(w.s["entities"]["table"].get("damage")).get("scorch", 0.0), 0.0)

    def test_thermal_tau_a_mug_of_coffee_cools_over_an_hour_not_minutes(self):
        def cooled(lumped):
            env = env_of(table_scene(m=mug("table", relation="on", spos=[0.2, 0.2], liquid="coffee", liquid_l=0.3,
                                           temperature_c=85.0)))
            with lawbook.forced(thermal_tau=lumped):
                ok(self, env, "wait", seconds=1800)
            return env.world.s["entities"]["m"]["temperature_c"]
        legacy, lumped = cooled(False), cooled(True)
        self.assertLess(legacy, 30.0)                 # the legacy 5 min time constant: near room temperature
        self.assertGreater(lumped, 45.0)              # lumped m c / h A: still warm after 30 min


# ----- Ctx helpers and motion records ------------------------------------------------------------------------------------

class CtxAndMotionTests(unittest.TestCase):
    def ctx(self, env, name="look", args=None):
        from house_sim.kernel.plugins import ACTIONS
        import random
        return Ctx(env, ACTIONS[name], dict(args or {}), random.Random(0))

    def test_helpers_reach_the_layer_and_attribute_to_the_robot(self):
        env = env_of(table_scene(g=glass("table", relation="on", spos=[0.5, 0.3]),
                                 b=make("book", "table", relation="on", spos=[0.1, 0.1])))
        w = env.world
        w.begin()
        ctx = self.ctx(env)
        self.assertEqual(ctx.phys("g").cls, "glass")
        self.assertEqual(ctx.lift_plan("b")["hands"], ["right"])
        out = ctx.fall("g", height_m=1.0, at=("kitchen", [4, 3]))
        self.assertEqual((out["by"], out["outcome"]), ("robot", "broke"))
        self.assertIsNone(ctx.spill(0.001))                         # droplets: nothing
        self.assertIsNotNone(ctx.spill(0.5, "water", at=("kitchen", [4, 2])))
        self.assertEqual(w.s["stats"]["spills"], 1)
        self.assertEqual(ctx.knock("b", 0.05)["slid_m"], 0.0)       # below friction: nothing
        w.rollback()

    def test_settle_physics_settles_early_and_the_engine_does_not_repeat_it(self):
        env = env_of({"tray": make("tray", "kitchen", pos=[3, 2], phys_exact=True),
                      "g": glass("tray", relation="on", spos=[0.1, 0.1])})
        w = env.world
        w.begin()
        settle.begin_action(w)
        ctx = self.ctx(env)
        w.destroy("tray")
        early = ctx.settle_physics()
        self.assertEqual([r["kind"] for r in early], ["phys.fall"])
        self.assertEqual(ctx.notes, ["g fell"])
        self.assertEqual(w.phys_scratch["phase"], "action")         # still the action phase
        again = settle.settle(env, "action", ctx)
        self.assertEqual([r["kind"] for r in again], ["phys.fall"])  # the step's list, nothing new
        self.assertEqual(ctx.notes, ["g fell"])                      # no duplicate note
        self.assertIsNone(w.phys_scratch["phase"])
        w.rollback()

    def test_motion_records_of_approach_move_and_nested_leaves(self):
        env = env_of({"table": make("table", "kitchen", pos=[3, 0])}, robot={"room": "kitchen", "pos": [0, 3]})
        w = env.world
        w.begin()
        approach = self.ctx(env, "approach", {"target": "table"})
        from house_sim.kernel.plugins import ACTIONS
        ACTIONS["approach"].fn(approach)
        record = motion_record(approach)
        self.assertEqual((record["from_room"], record["to_room"], record["connection"]), ("kitchen", "kitchen", None))
        self.assertGreater(record["meters"], 0)
        self.assertEqual(record["speed"], "normal")
        self.assertIsNone(motion_record(self.ctx(env, "look")))
        move = self.ctx(env, "move", {"target": "hall", "speed": "careful"})
        ACTIONS["move"].fn(move)
        leg = motion_record(move)
        self.assertEqual((leg["from_room"], leg["to_room"], leg["speed"]), ("kitchen", "hall", "careful"))
        self.assertIsNotNone(leg["connection"])
        self.assertGreaterEqual(leg["meters"], 1.0)                 # the doorway crossing
        parent = self.ctx(env, "look")
        parent.data["motions"] = [record, leg]
        self.assertEqual(motion_records(parent), [record, leg])
        merged = settle.travel_of(env, parent)
        self.assertEqual((merged["from_room"], merged["to_room"], merged["speed"]), ("kitchen", "hall", "normal"))
        self.assertAlmostEqual(merged["meters"], record["meters"] + leg["meters"])
        w.rollback()

    def test_perform_hands_nested_motion_to_the_enclosing_step(self):
        env = env_of({"table": make("table", "kitchen", pos=[3, 0])}, robot={"room": "kitchen", "pos": [0, 3]})
        w = env.world
        w.begin()
        parent = self.ctx(env, "look")
        env.perform({"action": "approach", "target": "table"}, parent)
        self.assertEqual(len(parent.data["motions"]), 1)
        self.assertEqual(parent.data["motions"][0]["to_room"], "kitchen")
        self.assertEqual(settle.travel_of(env, parent)["legs"], parent.data["motions"])
        w.rollback()

    def test_travel_without_records_comes_from_the_robots_journal(self):
        env = env_of(table_scene())
        w = env.world
        w.begin()
        self.assertIsNone(settle.travel_of(env))
        w.robot_m()["pos"] = [3, 3]
        travel = settle.travel_of(env)
        self.assertEqual((travel["from_room"], travel["path"]), ("kitchen", [[3, 3]]))
        w.rollback()


# ----- what the robot perceives ----------------------------------------------------------------------------------------------

class ObserveTests(unittest.TestCase):
    def test_glass_breaking_is_heard_here_with_its_source_and_a_thud_next_door_is_not_heard_far_away(self):
        env = env_of(table_scene(g=glass("table", relation="on", spos=[0.5, 0.3])))
        w = env.world
        w.begin()
        impact.fall(w, "g", height_m=0.75, at=("kitchen", [4, 3]), by="exogenous")
        heard = observe.heard_sounds(w.s)
        self.assertEqual(heard, [{"kind": "glass_breaking", "source_hint": "here", "loudness": "noticeable",
                                  "source": "g"}])
        w.rollback()
        w.begin()
        w.robot_m()["room"] = "hall"
        impact.fall(w, "g", height_m=0.75, at=("kitchen", [4, 3]), by="exogenous")
        self.assertEqual(observe.heard_sounds(w.s), [{"kind": "glass_breaking", "source_hint": "nearby",
                                                      "loudness": "noticeable"}])
        w.robot_m()["room"] = "yard"
        self.assertEqual(observe.heard_sounds(w.s), [])            # two rooms away: only loud sounds carry
        w.rollback()

    def test_loudness_follows_the_impact_energy(self):
        env = env_of({"box": item("kitchen", pos=[2, 2], label="box", material="cardboard", mass_kg=10.0,
                                  size_m=[0.4, 0.3, 0.3]),
                      "pen": item("kitchen", pos=[3, 2], label="pen", material="plastic", mass_kg=0.01,
                                  size_m=[0.14, 0.01, 0.01])})
        w = env.world
        w.begin()
        impact.fall(w, "box", height_m=1.0, at=("kitchen", [2, 2]))
        impact.fall(w, "pen", height_m=0.75, at=("kitchen", [3, 2]))
        heard = {s["source"]: (s["kind"], s["loudness"]) for s in observe.heard_sounds(w.s)}
        self.assertEqual(heard, {"box": ("thud", "loud"), "pen": ("clatter", "faint")})
        w.rollback()

    def test_a_cascade_is_heard_as_one_crash_and_one_clatter_not_one_sound_per_item(self):
        """Stage-B review (physics, low): a shelf collapse listed every fallen book as its own sound (39 entries for
        one collapse), which a listener cannot hear and which counted the fallen items exactly."""
        scene = {"case": make("bookshelf", "kitchen", pos=[2, 0])}
        for k in range(8):
            scene["b{}".format(k)] = item("case", relation="in", shelf=3, mass_kg=1.5, size_m=[0.2, 0.15, 0.25])
        env = env_of(scene)
        w = env.world
        w.begin()
        impact.collapse(w, "case", ("shelf", 3))
        falls = [e for e in w.s["event_log"] if e["kind"] == "phys.fall"]
        self.assertGreaterEqual(len(falls), 8)
        heard = observe.heard_sounds(w.s)
        kinds = [s["kind"] for s in heard]
        self.assertEqual(len(kinds), len(set(kinds)), heard)              # one entry per kind (all of it is "here")
        self.assertIn("crash", kinds)
        crash = [s for s in heard if s["kind"] == "crash"][0]
        self.assertEqual((crash["source"], crash["loudness"]), ("case", "loud"))
        thuds = [s for s in heard if s["kind"] in ("thud", "clatter") and s.get("count") == "many"]
        self.assertEqual(len(thuds), 1, heard)
        self.assertTrue(thuds[0]["source"].startswith("b"))
        w.rollback()

    def test_the_observer_adds_to_the_sounds_channel_and_realism_translates_the_source(self):
        env = env_of(table_scene(g=glass("table", relation="on", spos=[1.13, 0.36])), perception="realistic",
                     events=[{"at_s": 10, "effect": "physics.bump", "args": {"target": "g", "g": 0.9}}])
        obs = ok(self, env, "wait", seconds=30)["observation"]
        sounds = [s for s in obs.get("sounds", []) if s["kind"] == "glass_breaking"]
        self.assertEqual(len(sounds), 1)
        self.assertTrue(sounds[0]["source"].startswith("obj_"))
        self.assertNotIn("g", json.dumps(sounds[0]).replace("glass_breaking", "").split('"'))

    def test_feel_needs_a_touch_and_looks_a_close_look(self):
        scene = table_scene(b=make("book", "table", relation="on", spos=[0.1, 0.1]))
        env = env_of(scene, perception="realistic")
        realism = env.realism
        H = realism.to_handles
        ok(self, env, "approach", H("table"))
        first = json.loads(ok(self, env, "inspect", H("b"))["message"])
        self.assertNotIn("feel", first)
        ok(self, env, "pick_up", H("b"))
        touched = json.loads(ok(self, env, "inspect", H("b"))["message"])
        self.assertEqual(touched["feel"], "hard, rigid")
        plain = json.loads(ok(self, env, "inspect", H("table"))["message"])
        self.assertNotIn("looks", plain)
        close = json.loads(ok(self, env, "inspect", H("table"), where="closely")["message"])
        self.assertEqual(close["looks"], "sturdy")
        self.assertEqual(observe.looks_of(make("wall_shelf", "kitchen")), "flimsy")
        oracle = env_of(scene)
        ok(self, oracle, "approach", "table")
        self.assertEqual(json.loads(ok(self, oracle, "inspect", "b")["message"])["feel"], "hard, rigid")


# ----- determinism, replays ------------------------------------------------------------------------------------------------

SCENARIO = r"""
import json, sys, warnings
warnings.simplefilter("ignore")
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
import test_physics_settle as T
from house_sim.kernel.predicates import digest
env = T.env_of(T.table_scene(g=T.glass("table", relation="on", spos=[1.13, 0.36]),
                             e=T.egg("kitchen", pos=[1, 2])),
               events=[{"at_s": 30, "effect": "physics.bump", "args": {"target": "g", "g": 0.9}}])
for command in (("set_speed", "fast"), ("approach", "e"), ("crouch",), ("pick_up", "e"), ("stand",),
                ("approach", "table"), ("place", "e", "table")):
    env.step(*command)
env.step("wait", seconds=600)
print(json.dumps([digest(env.world.s), [e["kind"] for e in env.world.s["event_log"]]]))
"""


class DeterminismAndReplayTests(unittest.TestCase):
    def test_two_hash_seeds_give_the_same_consequences(self):
        outs = []
        for seed in ("1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run([sys.executable, "-c", SCENARIO, str(ROOT), str(ROOT / "tests")], cwd=str(ROOT),
                                  env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            outs.append(json.loads(proc.stdout.strip().splitlines()[-1]))
        self.assertEqual(outs[0], outs[1])
        self.assertIn("phys.break", outs[0][1])

    def test_replays_carry_the_physics_version_and_replay_exactly(self):
        from house_sim.kernel import physics
        env = env_of(table_scene(e=egg("kitchen", pos=[1, 2])))
        for command in (("set_speed", "fast"), ("approach", "e"), ("crouch",), ("pick_up", "e"), ("stand",),
                        ("approach", "table"), ("place", "e", "table"), ("wait",)):
            env.step(*command) if command[0] != "wait" else env.step("wait", seconds=120)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            env.save_replay(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["metadata"]["physics_version"], physics.physics_version())
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                again = HouseEnv.replay(path)
            self.assertEqual(digest(again.world.s), digest(env.world.s))
            self.assertFalse([c for c in caught if "physics data" in str(c.message)])
            data["metadata"]["physics_version"] = "0000000000000000"
            path.write_text(json.dumps(data), encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                HouseEnv.replay(path)
            self.assertTrue([c for c in caught if "physics data" in str(c.message)])

    def test_save_and_load_keep_pending_processes(self):
        env = env_of(table_scene(m=mug("table", relation="on", spos=[0.2, 0.2], liquid="water", liquid_l=0.3)))
        from house_sim.kernel.phys import liquids
        env.world.begin()
        liquids.leak_start(env.world, "m", l_per_h=0.6)
        env.world.commit()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            env.save(path)
            loaded = HouseEnv.load(path)
        for e in (env, loaded):
            e.step("wait", seconds=1800)
        self.assertEqual(digest(env.world.s), digest(loaded.world.s))
        self.assertLess(env.world.s["entities"]["m"]["liquid_l"], 0.3)


# ----- cost budgets (TIER1_INTEGRATION 3.4; tests fail at 3x the budget) ----------------------------------------------------

def best_ms(fn, repeats=7, inner=20):
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(inner):
            fn()
        times.append((time.perf_counter() - start) / inner)
    return min(times) * 1000.0


class BudgetTests(unittest.TestCase):
    def test_settle_costs(self):
        from house_sim.generator import generate_house
        env = HouseEnv(generate_house(seed=0), seed=0)
        w = env.world
        w.begin()
        idle = best_ms(lambda: settle.settle(env, "substep"))
        self.assertLess(idle, 3 * 0.02, idle)                       # nothing touched: <= 0.02 ms
        portable = sorted(e for e, r in w.s["entities"].items() if r.get("portable") and r.get("relation") == "on"
                          and r.get("location") in w.s["entities"])

        def typical():
            w.begin()
            settle.begin_action(w)
            for eid in portable[:3]:
                w.mut(eid)["spos"] = list(w.s["entities"][eid].get("spos") or [0.0, 0.0])
            settle.settle(env, "action")
            w.rollback()
        cost = best_ms(typical, repeats=5, inner=5)
        self.assertLess(cost, 3 * 1.0, cost)                          # a typical action: <= 1 ms

    def test_worst_case_cascade_cost(self):
        shelf = make("shelf_unit", "kitchen", pos=[3, 0], phys_exact=True, shelf_load_kg=1.0)
        scene = {"shelf": shelf}
        for i in range(70):
            scene["g{:02d}".format(i)] = glass("shelf", relation="in", shelf=3, size_m=[0.03, 0.03, 0.05],
                                               mass_kg=0.05)
        env = env_of(scene)
        w = env.world

        def cascade():
            w.begin()
            with lawbook.forced(overload=True):
                w.mut("shelf")["_phys_staged"] = True
                settle.begin_action(w)
                settle.settle(env, "action")
            w.rollback()
        cost = best_ms(cascade, repeats=3, inner=2)
        self.assertLess(cost, 3 * 10.0, cost)                          # 64 consequences: <= 10 ms

    def test_system_costs(self):
        from house_sim.generator import generate_house
        from house_sim.kernel.phys import environment, processes, thermal
        env = HouseEnv(generate_house(seed=0), seed=0)
        w = env.world
        w.begin()
        idle = best_ms(lambda: processes.run(w, 30.0, 0))
        self.assertLess(idle, 3 * 0.02, idle)                          # physics.processes idle: <= 0.02 ms
        calm = best_ms(lambda: environment.run(w, 30.0, 0))
        self.assertLess(calm, 3 * 0.02, calm)                          # deferred or calm: about 0
        for _ in range(60):
            thermal.thermal_step(w, 120.0, 0, mut=w.mut_quiet)         # let the house settle
        step = best_ms(lambda: thermal.thermal_step(w, 120.0, 0, mut=w.mut_quiet), repeats=5, inner=5)
        self.assertLess(step / 4.0, 3 * 0.5, step)                     # 120 s period over four substeps: <= 0.5 ms
        w.rollback()


if __name__ == "__main__":
    unittest.main()
