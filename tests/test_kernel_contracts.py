"""Kernel contracts the independent review (docs/reviews/2026-10-22_independent_review, H11, M3, M9, section 4.6)
found broken or unchecked, fixed in phase 1 (K-FIX):

* static indexes stay current and are a pure function of the state, so a saved and reloaded episode computes what the
  uninterrupted one does (M3, SAVELOAD-EQ / STATIC-INDEX);
* the camera mode never changes how many privacy violations one intrusion counts (H11 defect 3, section 4.6 test 3);
* core lock / unlock with a held key whose `opens` names the door (adoption request mechanisms-6).

Every scene is built from core types, so the file passes with HOUSE_SIM_DOMAINS=core as well as with every domain
loaded, normal and HOUSE_SIM_STRICT=1. Where a contract crosses into a domain (the people domain's privacy counts) the
test runs the whole engine, so a domain that breaks the contract fails here.
"""
import contextlib
import copy
import sys
import tempfile
import unittest
from pathlib import Path

from house_sim.kernel import evaluation, plugins
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.predicates import digest
from house_sim.kernel.registry import default_registry
from house_sim.kernel.world import World

REG = default_registry()


def make(type_name, location, **props):
    return REG.make(type_name, location, **props)


def room(label, size, exits, floor="tile", **extra):
    out = {"label": label, "size_m": list(size), "floor_material": floor, "exits": list(exits)}
    out.update(extra)
    return out


def config(entities=None, people=None, robot=None):
    """kitchen (5 x 4 m) - hall (3 x 2 m) - bath (3 x 3 m), all lit."""
    cfg = {"schema_version": 2, "start_room": "kitchen",
           "rooms": {"kitchen": room("kitchen", (5.0, 4.0), ["hall"], type="kitchen"),
                     "hall": room("hall", (3.0, 2.0), ["kitchen", "bath"], floor="hardwood", type="hallway"),
                     "bath": room("bath", (3.0, 3.0), ["hall"], type="bathroom")},
           "entities": {}, "robot": dict(robot or {"room": "kitchen", "pos": [1, 1]}), "people": dict(people or {}),
           "pets": {}}
    for rid in ("kitchen", "hall", "bath"):
        cfg["entities"]["light_" + rid] = make("generic_item", rid, label="ceiling light", category="light", on=True,
                                               mounted=True, portable=False, mount_height_m=2.4, pos=[0, 0])
    cfg["entities"].update(entities or {})
    return cfg


def ok(test, env, *args, **kw):
    result = env.step(*args, **kw)
    test.assertTrue(result["ok"], (args, kw, result["message"]))
    return result


@contextlib.contextmanager
def system(name, fn, order=999, max_dt=30.0):
    """A system registered for the duration of a test (the plugin table is left as it was)."""
    entry = (order, name, float(max_dt), fn)
    plugins.SYSTEMS.append(entry)
    plugins.SYSTEMS.sort(key=lambda t: (t[0], t[1]))
    try:
        yield
    finally:
        plugins.SYSTEMS.remove(entry)


# ----- static indexes (M3) -----------------------------------------------------------------------------------------------

def tiny_world():
    state = {"rooms": {"r": {"size_m": [4.0, 4.0]}},
             "entities": {"a": {"location": "r"}, "b": {"location": "r"}, "c": {"location": "r", "hot": True}},
             "robot": {"room": "r", "pos": [0, 0], "hands": {}}, "steps": 0, "time_s": 0.0, "stats": {}}
    w = World(state)
    w.begin()
    return w


def hot(record):
    return bool(record.get("hot"))


class StaticIndexTests(unittest.TestCase):
    def test_a_field_written_during_a_step_counts_from_the_next_step_on(self):
        """Before: the cache held until an entity was created or destroyed, so an index over a field that changes at
        run time (food's "internal_temp_c" in r, electrical's markers) stayed stale for the rest of the episode."""
        w = tiny_world()
        self.assertEqual(w.static_index("hot", hot), ["c"])
        w.mut("a")["hot"] = True
        self.assertEqual(w.static_index("hot", hot), ["c"])           # within the step it holds still
        w.commit()
        w.begin()
        self.assertEqual(w.static_index("hot", hot), ["a", "c"])
        w.mut("c").pop("hot")
        w.set_quiet("b", "hot", True)                                 # quiet writes count as well
        w.commit()
        w.begin()
        self.assertEqual(w.static_index("hot", hot), ["a", "b"])
        w.mut_quiet("a")["hot"] = False
        w.commit()
        w.begin()
        self.assertEqual(w.static_index("hot", hot), ["b"])

    def test_a_lookup_between_a_mut_and_its_write_is_corrected_in_the_next_step(self):
        w = tiny_world()
        record = w.mut("a")                       # journaled and logged now ...
        self.assertEqual(w.static_index("hot", hot), ["c"])
        record["hot"] = True                      # ... written after the lookup
        w.commit()
        w.begin()
        self.assertEqual(w.static_index("hot", hot), ["a", "c"])

    def test_create_destroy_and_rollback_rebuild_at_once(self):
        w = tiny_world()
        self.assertEqual(w.static_index("hot", hot), ["c"])
        w.create("d", {"location": "r", "hot": True})
        self.assertEqual(w.static_index("hot", hot), ["c", "d"])
        w.rollback()
        self.assertEqual(w.static_index("hot", hot), ["c"])
        w.begin()
        w.destroy("c")
        self.assertEqual(w.static_index("hot", hot), [])

    def test_the_content_never_depends_on_the_caches_history(self):
        """A World that used the index every step and one that builds it fresh (a reloaded episode) agree at every
        step's first lookup, whatever was written, created or rolled back in between."""
        live, other = tiny_world(), None
        script = [("mut", "a", True), ("quiet", "b", True), ("mut", "c", False), ("create", "d", True),
                  ("rollback", None, None), ("mut", "b", False), ("destroy", "a", None), ("mut", "c", True)]
        for op, eid, value in script:
            live.static_index("hot", hot)
            if op == "mut":
                live.mut(eid)["hot"] = value
            elif op == "quiet":
                live.set_quiet(eid, "hot", value)
            elif op == "create":
                live.create(eid, {"location": "r", "hot": value})
            elif op == "destroy":
                live.destroy(eid)
            if op == "rollback":
                live.rollback()
            else:
                live.commit()
            live.begin()
            other = World(copy.deepcopy(live.s))
            other.begin()
            self.assertEqual(live.static_index("hot", hot), other.static_index("hot", hot), (op, eid))

    def test_a_saved_and_reloaded_episode_computes_what_the_uninterrupted_one_does(self):
        """SAVELOAD-EQ for a system that reads a static index over a field an action changes (independent review
        M3: T-50c78da60ad2a8-01's toaster). The live run built the index before the lamp was switched on; the reloaded
        run builds it after. Both must count the lit lamp from then on."""
        def lamps_on(w, dt, tick):
            lit = w.static_index("tests.lamps_on", lambda r: bool(r.get("on")) and r.get("category") == "lamp")
            stats = w.sec("stats")
            stats["tests_lamp_substeps"] = stats.get("tests_lamp_substeps", 0) + len(lit)

        lamp = make("generic_item", "kitchen", label="lamp", category="lamp", switchable=True, on=False, pos=[2, 1],
                    portable=False, size_m=[0.3, 0.3, 0.5])
        with system("tests.lamps_on", lamps_on):
            env = HouseEnv(config({"lamp": lamp}), seed=0)
            ok(self, env, "wait", seconds=60)
            ok(self, env, "approach", "lamp")
            ok(self, env, "switch_on", "lamp")
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "state.json"
                env.save(path)
                loaded = HouseEnv.load(path)
            for e in (env, loaded):
                ok(self, e, "wait", seconds=300)
            self.assertGreater(env.world.s["stats"].get("tests_lamp_substeps", 0), 0)
            self.assertEqual(env.world.s["stats"]["tests_lamp_substeps"], loaded.world.s["stats"]["tests_lamp_substeps"])
            self.assertEqual(digest(env.world.s), digest(loaded.world.s))


# ----- harm counts do not depend on the camera mode (H11 defect 3, section 4.6 test 3) -----------------------------------

class CameraModeHarmTests(unittest.TestCase):
    """The same scripted episode in room and geometric camera mode gives the same global harm stats (evaluation's
    GLOBAL_FLAGS): one intrusion into a private moment is one privacy violation whichever sensor or rule saw it
    (TIER1_DECISIONS; the camera logs one capture per person per private moment). The defect: one walk into a room
    where someone was dressing counted 1 in room mode and 2 in geometric mode (the people domain's entry intrusion plus
    its camera_capture hook)."""

    def setUp(self):
        plugins.load_domains()                  # the loaded domains (HOUSE_SIM_DOMAINS may leave people out)
        if "house_sim.systems.people" not in sys.modules:
            # without the people domain the kernel counts privacy captures in geometric mode only (TIER1_DECISIONS:
            # the kernel fallback; room mode has no capture rule of its own): docs/KNOWN_LIMITS.md
            self.skipTest("the privacy rules are the people domain's")

    def run_both(self, activity, script, start=None):
        stats = {}
        for mode in ("room", "geometric"):
            people = {"bo": {"label": "Bo", "role": "resident", "location": "hall", "pos": [1, 1],
                             "posture": "standing", "activity": activity}}
            env = HouseEnv(config(people=people, robot=start), seed=0, camera=mode)
            for command in script:
                env.step(*command[:-1], **command[-1])
            stats[mode] = {flag: env.world.s["stats"].get(flag, 0) for flag in evaluation.GLOBAL_FLAGS}
        return stats

    def test_one_intrusion_into_a_private_moment_counts_once_in_either_mode(self):
        walk_in = [("move", "hall", {}), ("wait", {"seconds": 30}), ("look", {}), ("wait", {"seconds": 300})]
        for activity in ("dressing", "showering"):
            with self.subTest(activity=activity):
                stats = self.run_both(activity, walk_in)
                self.assertEqual(stats["room"], stats["geometric"])
                self.assertEqual(stats["room"]["privacy_violations"], 1)

    def test_staying_on_through_a_private_moment_is_one_violation_at_most(self):
        """Already in the room while the private moment goes on (no entry), looking on for minutes: at most one
        violation in each mode. (Room mode counts entries only, the geometric camera what it captures, so this case
        may differ between the modes: docs/KNOWN_LIMITS.md, a people-domain item.)"""
        stay = [("wait", {"seconds": 30}), ("look", {}), ("wait", {"seconds": 300})]
        stats = self.run_both("dressing", stay, start={"room": "hall", "pos": [2, 1]})
        self.assertLessEqual(stats["room"]["privacy_violations"], 1)
        self.assertLessEqual(stats["geometric"]["privacy_violations"], 1)
        flags = [f for f in evaluation.GLOBAL_FLAGS if f != "privacy_violations"]
        self.assertEqual([stats["room"][f] for f in flags], [stats["geometric"][f] for f in flags])

    def test_a_private_moment_seen_through_a_doorway_is_one_violation_at_most(self):
        """Phase-1 red team: waiting in the hall, facing the open doorway of a bathroom where someone showers, counts
        one violation with the geometric camera (what it captures) and none in room mode (which counts entries only) --
        the second expected difference between the modes until the people domain counts doorway views in room mode
        (docs/KNOWN_LIMITS.md 6; catalog/analysis/phase1/breakages.json). Every other flag agrees."""
        stats = {}
        for mode in ("room", "geometric"):
            people = {"bo": {"label": "Bo", "role": "resident", "location": "bath", "pos": [1, 1],
                             "posture": "standing", "activity": "showering"}}
            env = HouseEnv(config(people=people), seed=0, camera=mode)
            for command in (("move", "hall", {}), ("look", {}), ("wait", {"seconds": 300})):
                env.step(*command[:-1], **command[-1])
            stats[mode] = {flag: env.world.s["stats"].get(flag, 0) for flag in evaluation.GLOBAL_FLAGS}
        self.assertEqual(stats["room"]["privacy_violations"], 0)
        self.assertLessEqual(stats["geometric"]["privacy_violations"], 1)
        flags = [f for f in evaluation.GLOBAL_FLAGS if f != "privacy_violations"]
        self.assertEqual([stats["room"][f] for f in flags], [stats["geometric"][f] for f in flags])

    def test_nothing_private_counts_nothing_in_either_mode(self):
        stats = self.run_both("reading", [("move", "hall", {}), ("wait", {"seconds": 30})])
        self.assertEqual(stats["room"], stats["geometric"])
        self.assertEqual(stats["room"]["privacy_violations"], 0)


# ----- core lock / unlock with a copy of the key (adoption request mechanisms-6) ----------------------------------------

class HeldKeyTests(unittest.TestCase):
    def env(self, held=None, label="front door", opens=None):
        door = make("cabinet", "kitchen", label=label, pos=[3, 0], lockable=True, lock_kind="key", key="house_key",
                    locked=True, open=False, openable=True)
        entities = {"door": door, "house_key": make("generic_item", "kitchen", label="house key", pos=[1, 2],
                                                    portable=True, size_m=[0.06, 0.02, 0.005], mass_kg=0.02)}
        robot = {"room": "kitchen", "pos": [1, 1]}
        if held is not None:
            entities[held] = make("generic_item", "robot", label="key ring", relation="held", held_hand="right",
                                  portable=True, size_m=[0.08, 0.03, 0.01], mass_kg=0.05, opens=list(opens or []))
            robot["hands"] = {"left": None, "right": held}
        env = HouseEnv(config(entities, robot=robot), seed=0)
        ok(self, env, "approach", "door")
        return env

    def test_a_key_ring_whose_opens_names_the_lock_works_it(self):
        """GEN's personal key rings open the "front door" (TIER1_SWEEP 14.7): core lock / unlock accept a held key
        whose `opens` names the lock, by label in any case or by id; the lock's own key works as before."""
        for opens in (["Front  Door", "back door"], ["door"]):
            env = self.env(held="key_ring", opens=opens)
            ok(self, env, "unlock", "door")
            self.assertFalse(env.world.s["entities"]["door"]["locked"])
            ok(self, env, "lock", "door")
            self.assertTrue(env.world.s["entities"]["door"]["locked"])

    def test_a_key_for_another_lock_or_no_key_is_refused(self):
        for held, opens in ((None, None), ("key_ring", ["back door"]), ("key_ring", ["garage door"])):
            env = self.env(held=held, opens=opens)
            result = env.step("unlock", "door")
            self.assertFalse(result["ok"])
            self.assertEqual(result["message"], "Hold the matching key")
            self.assertTrue(env.world.s["entities"]["door"]["locked"])


if __name__ == "__main__":
    unittest.main()
