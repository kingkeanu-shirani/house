"""World savepoints (kernel v3.1, TIER1_SWEEP 6.4 and 16.1, TIER1_INTEGRATION 1.8): nested savepoints, rollback_to
restores exactly what changed after the savepoint (entities, sections, scalars, keyed records, quiet fields), the
touch log, the physics scratch and the heard-notice buffer follow, release keeps changes, and strict mode stays clean."""
import os
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.predicates import digest
from house_sim.kernel.world import World, clone
from house_sim.world import make_house, object_record


def fixture():
    c = make_house(3, 1, 3)
    c["goal"] = None
    c["entities"]["box"] = object_record("box", "room_1", portable=True, accepts_items=True, openable=True, open=False,
                                         mass_kg=1, capacity_l=20, container=True, size_m=[0.3, 0.3, 0.3])
    c["entities"]["toy"] = object_record("toy", "box", portable=True, mass_kg=1, volume_l=1, size_m=[0.1, 0.1, 0.1])
    return c


class SavepointTests(unittest.TestCase):
    def setUp(self):
        self.env = HouseEnv(fixture())
        self.w = self.env.world
        self.w.begin()

    def tearDown(self):
        self.w.rollback()

    def test_rollback_to_restores_only_what_changed_after_the_savepoint(self):
        w = self.w
        w.mut("box")["open"] = True                     # before the savepoint: kept
        w.set_scalar("time_s", 5.0)
        before = clone(w.s)
        sp = w.savepoint()
        w.mut("box")["label"] = "crate"
        w.mut("toy")["location"] = "room_2"
        w.create("ball", object_record("ball", "room_1"))
        w.destroy("cup")
        w.sec("facts")["x"] = 1
        w.sec("brand_new_section")["y"] = 2
        w.set_scalar("time_s", 99.0)
        w.person_m("resident_1")["mood"] = "angry"
        w.set_quiet("table_1", "temperature_c", 80.0)
        w.rollback_to(sp)
        self.assertEqual(digest(w.s), digest(before))
        self.assertTrue(w.s["entities"]["box"]["open"])
        self.assertEqual(w.s["time_s"], 5.0)
        self.assertNotIn("brand_new_section", w.s)
        # the step journal still covers everything: a full rollback goes back to the step start
        changed = {d["path"] for d in w.changes()}
        self.assertIn("entities.box.open", changed)
        self.assertNotIn("entities.box.label", changed)

    def test_nested_savepoints_roll_back_and_release_independently(self):
        w = self.w
        outer = w.savepoint()
        w.mut("box")["label"] = "outer"
        inner = w.savepoint()
        w.mut("box")["label"] = "inner"
        w.mut("toy")["mass_kg"] = 7
        w.rollback_to(inner)
        self.assertEqual(w.s["entities"]["box"]["label"], "outer")
        self.assertEqual(w.s["entities"]["toy"]["mass_kg"], 1)
        inner = w.savepoint()
        w.mut("toy")["mass_kg"] = 9
        w.release(inner)                                # kept, but still under the outer savepoint
        self.assertEqual(w.s["entities"]["toy"]["mass_kg"], 9)
        w.rollback_to(outer)
        self.assertEqual(w.s["entities"]["box"]["label"], "box")
        self.assertEqual(w.s["entities"]["toy"]["mass_kg"], 1)
        with self.assertRaises(ValueError):
            w.rollback_to(outer)                        # ended

    def test_rolling_back_several_layers_at_once(self):
        w = self.w
        first = w.savepoint()
        w.mut("box")["label"] = "a"
        w.savepoint()
        w.mut("box")["label"] = "b"
        w.create("ball", object_record("ball", "room_1"))
        w.rollback_to(first)
        self.assertEqual(w.s["entities"]["box"]["label"], "box")
        self.assertNotIn("ball", w.s["entities"])
        self.assertEqual(w._layers, [])

    def test_quiet_fields_written_before_and_after_a_savepoint(self):
        w = self.w
        w.set_quiet("table_1", "temperature_c", 30.0)
        sp = w.savepoint()
        w.set_quiet("table_1", "temperature_c", 60.0)
        w.mut("table_1")["label"] = "hot table"
        w.set_quiet("table_1", "temperature_c", 70.0)
        w.rollback_to(sp)
        self.assertEqual(w.s["entities"]["table_1"]["temperature_c"], 30.0)
        self.assertEqual(w.s["entities"]["table_1"]["label"], "table")
        w.rollback()
        self.assertNotEqual(w.s["entities"]["table_1"].get("temperature_c"), 30.0)

    def test_touch_log_cursors_scratch_and_heard_buffer_follow_the_rollback(self):
        w = self.w
        w.mut("box")
        ids, cursor = w.touched_since(0, "reader")
        self.assertEqual(ids, ["box"])
        w.phys_scratch["sig"] = {"box": ("x",)}
        w._heard.append({"kind": "doorbell"})
        count = w.mutation_count
        sp = w.savepoint()
        w.mut("toy")
        w.mut("cup")
        w.touched_since(cursor, "reader")               # the reader saw the touches after sp
        w.phys_scratch["sig"]["toy"] = ("y",)
        w.phys_scratch["new"] = 1
        w._heard.append({"kind": "knock"})
        w.rollback_to(sp)
        self.assertEqual(w._touched, ["box"])
        self.assertEqual(w._touch_readers["reader"], 1)  # clamped to the log's length at sp
        self.assertEqual(w.phys_scratch, {"sig": {"box": ("x",)}})
        self.assertEqual(w._heard, [{"kind": "doorbell"}])
        self.assertGreater(w.mutation_count, count)

    def test_release_without_a_parent_layer_and_step_boundaries_end_savepoints(self):
        w = self.w
        sp = w.savepoint()
        w.mut("box")["label"] = "kept"
        w.release(sp)
        self.assertEqual(w._layers, [])
        self.assertEqual(w.s["entities"]["box"]["label"], "kept")
        w.savepoint()
        w.commit()
        self.assertEqual(w._layers, [])
        w.begin()
        w.savepoint()
        w.begin()
        self.assertEqual(w._layers, [])

    def test_bad_tokens_are_refused(self):
        for token in (0, 3, "1", True, None):
            with self.assertRaises(ValueError):
                self.w.rollback_to(token)
            with self.assertRaises(ValueError):
                self.w.release(token)

    def test_world_without_an_env(self):
        state = clone(self.env.world.s)
        w = World(state)
        w.begin()
        sp = w.savepoint()
        w.sec("robot")["room"] = "room_3"
        w.rollback_to(sp)
        self.assertEqual(w.s["robot"]["room"], self.env.world.s["robot"]["room"])


class SavepointStrictTests(unittest.TestCase):
    def test_a_rolled_back_savepoint_is_clean_in_strict_mode(self):
        import importlib
        from house_sim.kernel import engine
        os.environ["HOUSE_SIM_STRICT"] = "1"
        try:
            importlib.reload(engine)
            env = engine.HouseEnv(fixture())
            # an auto order whose second command is refused: the savepoint rolls it back inside a strict step
            self.assertTrue(env.step("watch", condition={"path": "time_s", "op": "gte", "value": 30},
                                     commands=[{"action": "approach", "target": "box"},
                                               {"action": "open", "target": "box"},
                                               {"action": "pick_up", "target": "nothing_here"}],
                                     mode="auto")["ok"])
            result = env.step("wait", seconds=120)
            self.assertTrue(result["ok"], result["message"])
            log = env.world.s["order_log"]
            self.assertEqual([entry["ok"] for entry in log], [True, True, False])
            self.assertTrue(env.world.s["entities"]["box"]["open"])
        finally:
            os.environ.pop("HOUSE_SIM_STRICT", None)
            importlib.reload(engine)


if __name__ == "__main__":
    unittest.main()
