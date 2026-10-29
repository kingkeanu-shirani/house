"""Perception evidence (docs/design/VERIFY_HARDENING.md 15.2-15.3, WP7): realism's act log, grant log and timed
observation log, the split knowledge signature (identity and intrinsic fields survive a move the robot tracked), the
twin-invariant name rewrite of unfamiliar things (closeout_REV latch / mark), and tasks/perceive.py's index, report
gaps with the drift rule and derivations, act gaps, founded claims and the agent rule's labels.

  ACTS      the act log records every action before it ran, deterministically, across save / load
  SPLIT     a tracked move keeps identity and intrinsic grants; an unseen move lapses them (room and geometric camera)
  TWINS     an unfamiliar thing's names are rewritten alike whatever it truly is (latch "Hold the padlock", mark
            "Hold a pencil": closeout_REV), in both cameras
  INDEX     observations, messages, grants and readings by time and step
  REPORTS   a reported value is founded on an observation at its tier before it, within precision, under the drift rule
            and as_of; derived answers follow derivations.json (sum, mean, min/max, diff, rate, duration, convert,
            count, any/all/none_found; composed; the table is closed)
  ACTGATE   decisive acts need their evidence first (entity, set, branch), by elimination for an entity answer
  CLAIMS    factual claims need their subject's evidence first; uncertain claims are always founded
  LABELS    the agent rule's perceive:* labels; nothing in oracle mode
  TOLD      a never-tier fact is founded by a person's own words only, never the robot's (review A-gaming-1)
  FORK      a run forked from env.save / HouseEnv.load judges evidence as the straight run (review A-spec-2)
  BRANCH    for an agent, any verb doing what a branch diff command does decides the branch (review A-gaming-4)

Scenes with domain types or verbs skip when HOUSE_SIM_DOMAINS leaves the domain out; the rest use core types.
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import enabled_domains
from house_sim.kernel.realism import (GLANCE, INSPECT, MEASURE, READ, Knowledge, ObservationLog,
                                      _trie_pattern)
from house_sim.kernel.registry import default_registry
from house_sim.tasks import perceive

COLS, ROWS = 12, 10


def config(entities=None, pos=(1, 1), people=None, events=None):
    """A lit 6 x 5 m kitchen ("lab") with a lit hallway next door (tests/test_kernel_redteam.py's scene)."""
    ents = {"light_1": {"type": "ceiling_light", "location": "lab", "pos": [COLS - 1, 0], "on": True, "mounted": True,
                        "mount_height_m": 2.4},
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [1, 1], "on": True, "mounted": True,
                        "mount_height_m": 2.4},
            "table_1": {"type": "table", "location": "lab", "pos": [3, 2], "surface": True, "accepts_items": True,
                        "size_m": [1.2, 0.8, 0.75]}}
    ents.update(copy.deepcopy(entities or {}))
    out = {"schema_version": 2, "start_room": "lab",
           "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "kitchen", "exits": ["hall"]},
                     "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
           "entities": ents, "people": copy.deepcopy(people or {}), "pets": {},
           "robot": {"room": "lab", "pos": list(pos)}}
    if events:
        out["events"] = copy.deepcopy(events)
    return out


def env_of(entities=None, perception="realistic", camera="room", seed=0, record=True, **kw):
    env = HouseEnv(config(entities, **kw), task=None, seed=seed, perception=perception, camera=camera)
    if record:
        perceive.enable(env)
    return env


def bottle(label_text, cell=(2, 1), **props):
    """A spray bottle (look-alike group spray_bottle, resolved by reading its label) on the floor."""
    out = {"type": "generic_item", "location": "lab", "pos": list(cell), "label": "spray bottle",
           "looks_like": "spray_bottle", "size_m": [0.1, 0.07, 0.25], "portable": True, "label_text": label_text,
           "mass_kg": 0.5}
    out.update(props)
    return out


def known_types(*names):
    reg = default_registry()
    return all(reg.known(n) for n in names)


def domains_on(*names):
    """The domains are loaded (HOUSE_SIM_DOMAINS leaves them in)."""
    only = enabled_domains()
    return only is None or all(n in only for n in names)


def h(env, eid):
    env.observe()
    return env.realism.handles.handle(eid)


class ActLogTests(unittest.TestCase):
    """ACTS (15.3): (t, action, ids, {id: unresolved}) and what the robot knew of each target, taken before the
    action ran; kept outside world state, deterministic, saved and restored with the episode."""

    def run_plan(self, camera="room"):
        env = env_of({"b_1": bottle("BLEACH"), "b_2": bottle("VINEGAR", cell=(4, 1))}, camera=camera)
        hb = h(env, "b_1")
        env.step("approach", hb)
        env.step("read", hb)
        env.step("inspect", hb)
        env.step("open", "obj_00000000")         # refused: a handle this episode never issued
        return env

    def test_every_action_is_logged_with_what_was_known_before_it(self):
        env = self.run_plan()
        acts = env.realism.acts
        self.assertEqual([a["action"] for a in acts], ["approach", "read", "inspect", "open"])
        self.assertEqual([a["step"] for a in acts], [1, 2, 3, 4])
        self.assertEqual([a["ok"] for a in acts], [True, True, True, False])
        self.assertTrue(all(acts[i]["t"] <= acts[i + 1]["t"] for i in range(3)))
        before_read, after_read = acts[1]["targets"]["b_1"], acts[2]["targets"]["b_1"]
        self.assertTrue(before_read["unresolved"])                  # taken before the read ran
        self.assertNotIn("label_text", before_read["fields"])
        self.assertFalse(after_read["unresolved"])
        self.assertEqual(after_read["fields"].get("label_text"), READ)
        self.assertEqual(after_read["values"]["label_text"], "BLEACH")
        self.assertEqual(acts[1]["ids"], ["b_1"])
        self.assertEqual(acts[3]["ids"], [])                         # refused before it named anything
        self.assertNotIn("acts", env.world.s)                        # never in world state

    def test_the_log_is_deterministic_and_survives_save_and_load(self):
        for camera in ("room", "geometric"):
            a, b = self.run_plan(camera), self.run_plan(camera)
            self.assertEqual(json.dumps(a.realism.acts, sort_keys=True), json.dumps(b.realism.acts, sort_keys=True),
                             camera)
            self.assertEqual(a.realism.grants_log, b.realism.grants_log, camera)
            dumped = a.realism.dump()
            self.assertEqual(dumped["acts"], a.realism.acts)
            other = env_of({"b_1": bottle("BLEACH"), "b_2": bottle("VINEGAR", cell=(4, 1))}, camera=camera,
                           record=False)
            other.realism.load(dumped)
            self.assertEqual(other.realism.acts, a.realism.acts)
            self.assertEqual(other.realism.grants_log, a.realism.grants_log)

    def test_an_availability_probe_logs_no_act(self):
        env = env_of({"b_1": bottle("BLEACH")})
        env.affordances(h(env, "b_1"))
        self.assertEqual(env.realism.acts, [])

    def test_the_grant_log_keeps_what_was_learned_and_when(self):
        env = self.run_plan()
        read = [g for g in env.realism.grants_log if g["id"] == "b_1" and g["level"] == READ]
        self.assertTrue(read)
        self.assertEqual(read[0]["values"]["label_text"], "BLEACH")
        self.assertEqual(read[0]["step"], 2)
        self.assertNotIn("owner", read[0]["values"])                 # never-tier fields are no evidence


class SplitSignatureTests(unittest.TestCase):
    """SPLIT (15.2): realism's knowledge reset every field of a thing when its placement signature changed, so a
    look-alike the robot had read and then watched being moved was unresolved again. A move the robot tracked (it saw
    the thing, identified, in its own lit room when the step began and when it ended) keeps identity and intrinsic
    grants; placement-dependent fields lapse; an unseen move lapses everything as before."""

    @staticmethod
    def move(to):
        return [{"at_s": 30.0, "changes": {"entities.b_1.pos": list(to)}}]

    def scene(self, camera="room", track=True, to=(2, 3)):
        """b_1 read and inspected, then moved during a wait to `to` (the default stays in the head camera's frame,
        which keeps aiming where the read looked)."""
        env = env_of({"b_1": bottle("BLEACH", cell=(2, 2)), "b_2": bottle("VINEGAR", cell=(4, 1))}, camera=camera,
                     events=self.move(to), pos=(2, 1))
        env.realism.track_moves = track
        hb = h(env, "b_1")
        self.assertTrue(env.step("read", hb)["ok"])
        self.assertFalse(env.realism.unresolved("b_1"))
        self.assertTrue(env.step("inspect", hb)["ok"])
        return env, hb

    def test_a_watched_move_keeps_identity_and_intrinsic_grants(self):
        for camera in ("room", "geometric"):
            env, hb = self.scene(camera)
            record = env.world.s["entities"]["b_1"]
            self.assertTrue(env.step("wait", seconds=60)["ok"])
            env.observe()
            record = env.world.s["entities"]["b_1"]
            self.assertEqual(record["pos"], [2, 3], camera)
            self.assertFalse(env.realism.unresolved("b_1"), camera)
            self.assertEqual(env.realism.knowledge.level("b_1", record, "label_text"), READ, camera)
            self.assertEqual(env.realism.knowledge.level("b_1", record, "material"), INSPECT, camera)
            self.assertEqual(env.realism.knowledge.level("b_1", record, "pos"), GLANCE, camera)
            self.assertEqual(env.realism.knowledge.level("b_1", record, "open"), GLANCE, camera)
            shown = env.observe()["visible_entities"][hb]
            self.assertNotIn("candidates", shown, camera)

    def test_an_unseen_move_lapses_as_before(self):
        for camera in ("room", "geometric"):
            env, hb = self.scene(camera)
            self.assertTrue(env.step("move", "hall")["ok"], camera)
            self.assertTrue(env.step("wait", seconds=60)["ok"], camera)
            self.assertTrue(env.step("move", "lab")["ok"], camera)
            env.observe()
            record = env.world.s["entities"]["b_1"]
            self.assertEqual(record["pos"], [2, 3], camera)
            self.assertTrue(env.realism.unresolved("b_1"), camera)
            self.assertEqual(env.realism.knowledge.level("b_1", record, "label_text"), GLANCE, camera)

    def test_a_move_out_of_the_cameras_frame_is_not_tracked(self):
        env, _hb = self.scene("geometric", to=(3, 3))
        env.step("wait", seconds=60)
        env.observe()
        self.assertTrue(env.realism.unresolved("b_1"))
        env, _hb = self.scene("room", to=(3, 3))           # the room camera sees the whole lit room
        env.step("wait", seconds=60)
        env.observe()
        self.assertFalse(env.realism.unresolved("b_1"))

    def test_tracking_off_is_the_old_rule(self):
        env, _hb = self.scene("room", track=False)
        env.step("wait", seconds=60)
        env.observe()
        self.assertTrue(env.realism.unresolved("b_1"))

    def test_tracking_is_the_same_across_save_and_load(self):
        env, _hb = self.scene("room")
        env.step("wait", seconds=60)
        dumped = env.realism.dump()
        self.assertFalse(env.realism.unresolved("b_1"))
        entry = dumped["knowledge"]["b_1"]
        self.assertEqual(entry["sig"], Knowledge.signature(env.world.s["entities"]["b_1"]))
        self.assertIn("label_text", entry["fields"])
        self.assertIn("pos", entry.get("lapsed") or {})

    def test_retain_keeps_only_the_listed_fields(self):
        k = Knowledge()
        rec = {"location": "lab", "pos": [1, 1], "label_text": "X", "open": False, "mass_kg": 1.0}
        k.grant("e", rec, READ, ["label_text"])
        k.grant("e", rec, INSPECT, ["open"])
        k.grant("e", rec, MEASURE, ["mass_kg"])
        moved = dict(rec, pos=[2, 2])
        k.retain("e", moved, {"label_text", "mass_kg"})
        self.assertEqual(k.level("e", moved, "label_text"), READ)
        self.assertEqual(k.level("e", moved, "mass_kg"), MEASURE)
        self.assertEqual(k.level("e", moved, "open"), GLANCE)
        self.assertEqual(k.stale("e", "open"), (INSPECT, False))       # still remembered for the stale rendering


class UnfamiliarTwinWordingTests(unittest.TestCase):
    """TWINS (closeout_REV latch / mark; admin_inventory's luggage scale; VERIFY_HARDENING WP7): the kernel rewrote an
    unfamiliar thing's own type names in a message, so a domain's generic wording that named that type ("Hold the
    padlock", "Hold a pencil") came back rewritten for the unfamiliar padlock or pencil alone. Its names are now those
    of every type it could be: the same rewrite whatever it truly is."""

    LOOK = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "portable": True}
    TWINS = ("combination_padlock", "pencil", "electric_kettle", "chef_knife")

    def twin(self, type_name, camera="room", extra=None):
        ents = {"thing_1": dict(self.LOOK, type=type_name, location="table_1", relation="on")}
        ents.update(extra or {})
        return env_of(ents, camera=camera, pos=(3, 1), record=False)

    def test_generic_wording_naming_a_type_reads_alike_for_every_twin(self):
        if not known_types(*self.TWINS):
            self.skipTest("needs the domains' twin types")
        messages = ("Hold the padlock", "Hold a pencil", "Hold the combination padlock's key", "Put it on a kettle",
                    "No free floor space within reach here", "A chef's knife (20 cm) does not fit")
        for camera in ("room", "geometric"):
            outs = {}
            for t in self.TWINS:
                env = self.twin(t, camera)
                h(env, "thing_1")
                self.assertTrue(env.realism.novel_unidentified("thing_1"))
                outs[t] = [env.realism.outbound_message(m, involved={"thing_1"}) for m in messages]
            self.assertEqual(len({json.dumps(v) for v in outs.values()}), 1, (camera, outs))
            first = outs[self.TWINS[0]]
            self.assertEqual(first[0], "Hold the unfamiliar medium object")
            self.assertEqual(first[1], "Hold an unfamiliar medium object")        # the article follows the rewrite
            self.assertEqual(first[4], "No free floor space within reach here")    # fixtures are no unfamiliar thing

    def test_a_named_identified_thing_keeps_its_name(self):
        if not known_types("pencil", "mug"):
            self.skipTest("needs the domains' types")
        env = self.twin("pencil", extra={"mug_1": {"type": "mug", "location": "table_1", "relation": "on"}})
        h(env, "thing_1")
        out = env.realism.outbound_message("Put the pencil next to the mug", involved={"thing_1", "mug_1"})
        self.assertEqual(out, "Put the unfamiliar medium object next to the mug")

    def test_the_agents_own_words_are_never_rewritten(self):
        if not known_types(*self.TWINS):
            self.skipTest("needs the domains' twin types")
        for t in self.TWINS:
            env = self.twin(t)
            result = env.step("apply_label", h(env, "thing_1"), text="the padlock and a pencil")
            self.assertIn("the padlock and a pencil", result["message"], t)

    def test_latch_and_mark_answer_alike_for_every_twin(self):
        """The REV repro (scratch p1c_REV/novel_wording.py): latch target=<mug> destination=<it> and mark target=<it>
        height_m=1.0 with the unfamiliar thing on the table, for an unfamiliar padlock, pencil, kettle and knife."""
        if not known_types(*self.TWINS + ("mug",)):
            self.skipTest("needs the domains' twin types")
        from house_sim.kernel.actions import ACTIONS
        if "latch" not in ACTIONS or "mark" not in ACTIONS:
            self.skipTest("needs mechanisms' latch and navigation_robot's mark")
        for camera in ("room", "geometric"):
            rows = {}
            for t in self.TWINS:
                env = self.twin(t, camera, extra={"mug_1": {"type": "mug", "location": "table_1", "relation": "on"}})
                it, mug = h(env, "thing_1"), h(env, "mug_1")
                env.step("approach", it)
                latch = env.step("latch", mug, destination=it)
                mark = env.step("mark", it, height_m=1.0)
                rows[t] = [(r["ok"], r["category"], r["message"].replace(it, "<it>").replace(mug, "<mug>"))
                           for r in (latch, mark)]
            self.assertEqual(len({json.dumps(v) for v in rows.values()}), 1, (camera, rows))

    def test_the_trie_pattern_matches_exactly_the_words(self):
        words = {"pen", "pencil", "pen holder", "e-bike", "chef's knife (20 cm)"}
        pattern = re.compile("(?i)^(?:" + _trie_pattern(words) + ")$")
        for w in words:
            self.assertTrue(pattern.match(w), w)
            self.assertTrue(pattern.match(w.upper()), w)
        for w in ("pe", "pens", "pencils", "e-bik", "chef's knife"):
            self.assertIsNone(pattern.match(w), w)
        self.assertIsNone(re.match(_trie_pattern(()), "anything"))


class IndexTests(unittest.TestCase):
    """INDEX (15.2): realism's recorded observations and messages, stamped with time and step, the knowledge levels
    and true values of what each observation listed, the grant log and structured readings."""

    def test_observations_are_stamped_and_carry_the_levels_of_the_time(self):
        env = env_of({"b_1": bottle("BLEACH")})
        hb = h(env, "b_1")
        env.step("approach", hb)
        env.step("read", hb)
        log = env.realism.observed
        self.assertIsInstance(log, ObservationLog)
        self.assertEqual(len(log), len(log.stamps))
        self.assertEqual([s[1] for s in log.stamps], sorted(s[1] for s in log.stamps))
        idx = perceive.index(env)
        reads = idx.entries("b_1", "label_text", level=READ)
        self.assertTrue(reads)
        self.assertEqual({o.step for o in reads if o.source == "grant"}, {2})
        self.assertTrue(any(o.source == "obs" and o.shown == "BLEACH" and o.step == 2 for o in reads))
        self.assertFalse(idx.entries("b_1", "label_text", before_step=2, level=READ))   # not before it was read
        self.assertTrue(idx.seen("b_1", before_step=1))
        self.assertFalse(idx.seen("b_1", before_step=1, identified=True))       # an unresolved look-alike
        self.assertTrue(idx.seen("b_1", identified=True))
        self.assertEqual([a["action"] for a in idx.acts], ["approach", "read"])

    def test_structured_readings_in_messages_are_indexed(self):
        idx = perceive.Index()
        perceive._index_message(idx, json.dumps({"reading": {"subject": "b_1", "quantity": "mass", "value": 250,
                                                             "unit": "g", "field": "mass_kg"}}), 10.0, 3)
        self.assertEqual(len(idx.readings), 1)
        self.assertAlmostEqual(idx.readings[0]["value_si"], 0.25)
        self.assertEqual(idx.readings[0]["step"], 3)

    def test_without_realistic_perception_the_index_is_empty(self):
        env = env_of({"b_1": bottle("BLEACH")}, perception="oracle")
        env.step("approach", "b_1")
        idx = perceive.index(env)
        self.assertEqual((idx.fields, idx.acts), ({}, []))
        self.assertFalse(perceive.enable(env))
        self.assertEqual(perceive.agent_labels(env, {}, {"a": {"kind": "entity", "group": ["b_1"]}}), [])


def synthetic(entities, observations=(), acts=(), last_step=10):
    """A realistic env over `entities` and an Index holding exactly the given observations
    [(eid, field, step, level, shown, truth)] (t = 10 * step) and acts."""
    env = env_of(entities, record=False)
    idx = perceive.Index()
    for eid, field, step, level, shown, truth in observations:
        idx.add(eid, field, perceive.Obs(10.0 * step, step, level, shown, truth, "obs"))
        idx.views.setdefault(eid, []).append(perceive.View(10.0 * step, step, True, eid))
    idx.acts = list(acts)
    idx.last_step = last_step
    idx.finish()
    return env, idx


def box(**props):
    out = {"type": "generic_item", "location": "lab", "pos": [2, 1], "label": "box", "size_m": [0.3, 0.3, 0.3],
           "portable": True, "mass_kg": 1.0}
    out.update(props)
    return out


class ReportSupportTests(unittest.TestCase):
    """REPORTS (15.2): an observation (e, f, t_o, v_o, tier) supports a reported v_r at t_r when the tier is reached,
    t_o is before the report, |v_r - v_o| <= max(precision, tol), and the truth moved by at most tol since (or the
    predicate declares as_of observed)."""

    def test_tier_time_precision_and_drift(self):
        env, idx = synthetic({"pot_1": box(temperature_c=60.0)},
                             [("pot_1", "temperature_c", 2, MEASURE, 60.2, 60.0),
                              ("pot_1", "temperature_c", 1, GLANCE, "hot", 60.0)],
                             acts=[{"step": 5, "t": 50.0, "action": "report", "ok": True,
                                    "args": {"target": "pot_1", "field": "temperature_c"}, "ids": ["pot_1"],
                                    "targets": {"pot_1": {"values": {"temperature_c": 59.0}}}}])
        self.assertIsNotNone(perceive.supports(idx, env, "pot_1", "temperature_c", 60.0, 5, tol=2.0))
        self.assertIsNone(perceive.supports(idx, env, "pot_1", "temperature_c", 60.0, 2, tol=2.0))   # not before
        self.assertIsNone(perceive.supports(idx, env, "pot_1", "temperature_c", 63.5, 5, tol=2.0))   # too far
        self.assertIsNone(perceive.supports(idx, env, "pot_1", "temperature_c", 60.0, 5, tol=0.5))   # it cooled
        self.assertIsNotNone(perceive.supports(idx, env, "pot_1", "temperature_c", 60.0, 5, tol=0.5,
                                               as_of="observed"))
        self.assertIsNone(perceive.supports(idx, env, "pot_1", "temperature_c", 60.0, 5, tol=2.0, need=99))

    def test_a_bucket_word_founds_no_number(self):
        """A glance at a jug shows its fill as a word ("half"): reporting 0.52 exactly is not founded on it, and a
        sum of glanced fills is not either; a reading that showed the number is."""
        env, idx = synthetic({"jug": box(fill_level=0.52), "jug2": box(fill_level=0.3)},
                             [("jug", "fill_level", 1, GLANCE, "half", 0.52), ("jug2", "fill_level", 1, GLANCE, "low", 0.3)])
        self.assertIsNone(perceive.supports(idx, env, "jug", "fill_level", 0.52, 5))
        self.assertIsNotNone(perceive.supports(idx, env, "jug", "fill_level", "half", 5))
        spec = {"op": "sum", "over": ["jug", "jug2"], "field": "fill_level"}
        self.assertFalse(perceive.derive_support(idx, env, spec, 0.82, 5, tol=0.02)[0])
        idx.add("jug", "fill_level", perceive.Obs(20.0, 2, MEASURE, 0.52, 0.52, "obs"))
        idx.add("jug2", "fill_level", perceive.Obs(20.0, 2, MEASURE, 0.3, 0.3, "obs"))
        idx.finish()
        self.assertIsNotNone(perceive.supports(idx, env, "jug", "fill_level", 0.52, 5))
        self.assertTrue(perceive.derive_support(idx, env, spec, 0.82, 5, tol=0.02)[0])

    def test_a_never_tier_fact_is_founded_by_being_told(self):
        """Who owns a thing is never perceived: it is founded when someone said it (an ask's answer, a resident's
        message) before the report, and not otherwise."""
        env, idx = synthetic({"coat_1": box(owner="dad")})
        self.assertIsNone(perceive.supports(idx, env, "coat_1", "owner", "dad", 5))
        idx.messages.append((20.0, 2, json.dumps({"by": "mom", "answer": "It is Dad's"})))
        idx.acts = [{"step": 2, "t": 15.0, "action": "wait", "ok": True, "args": {}, "ids": [], "targets": {}}]
        self.assertIsNone(perceive.supports(idx, env, "coat_1", "owner", "dad", 5))       # not a question's answer
        idx.acts[0]["action"] = "ask"
        self.assertIsNotNone(perceive.supports(idx, env, "coat_1", "owner", "dad", 5))
        self.assertIsNone(perceive.supports(idx, env, "coat_1", "owner", "dad", 2))       # not before
        self.assertIsNone(perceive.supports(idx, env, "coat_1", "owner", "mom", 5))       # who answered says nothing

    def test_a_memorised_weight_is_not_founded(self):
        """P-386d0f28709b05-01's cheat: the weight reported without the scale ever reading it."""
        env, idx = synthetic({"jar_1": box(mass_kg=0.412)}, [("jar_1", "mass_kg", 1, TOUCHED, "light", 0.412)])
        self.assertIsNone(perceive.supports(idx, env, "jar_1", "mass_kg", 0.412, 5, tol=0.002))
        idx.add("jar_1", "mass_kg", perceive.Obs(30.0, 3, MEASURE, 0.41, 0.412, "grant"))
        self.assertIsNotNone(perceive.supports(idx, env, "jar_1", "mass_kg", 0.412, 5, tol=0.002))

    def test_legacy_report_gaps_on_a_real_run(self):
        task = {"success": [{"path": "reports.b_1.label_text", "op": "eq", "value": "BLEACH"}]}
        for read in (False, True):
            env = env_of({"b_1": bottle("BLEACH")})
            hb = h(env, "b_1")
            env.step("approach", hb)
            env.step("inspect", hb)
            if read:
                env.step("read", hb)
            result = env.step("report", hb, field="label_text", value="BLEACH")
            self.assertTrue(result["ok"], result)
            gaps = perceive.report_gaps(env, task)
            self.assertEqual(bool(gaps), not read, gaps)
            labels = perceive.agent_labels(env, task, {})
            self.assertEqual(labels, [] if read else ["perceive:report_unperceived:b_1.label_text"])

    def test_typed_measurements_need_the_measurement_first(self):
        task = {"success": [{"report": {"kind": "measurement", "subject": "b_1", "value": 500, "unit": "g",
                                        "tol": 10}}]}
        for measured in (False, True):
            env = env_of({"b_1": bottle("BLEACH")})
            hb = h(env, "b_1")
            env.step("approach", hb)
            if measured:
                # what a scale reading teaches (food's weigh grants the one thing on the scale its mass_kg)
                env.realism.begin_step()
                env.realism.grant("b_1", "measure", ["mass_kg"])
                env.realism.commit_grants(True)
            result = env.step("report", kind="measurement", subject=hb, quantity="mass", value=500, unit="g")
            self.assertTrue(result["ok"], result)
            gaps = perceive.report_gaps(env, task)
            self.assertEqual(bool(gaps), not measured, gaps)
            if not measured:
                self.assertEqual(perceive.agent_labels(env, task, {}), ["perceive:report_unperceived:1"])

    def test_a_report_field_nothing_shows_needs_an_answer(self):
        env, idx = synthetic({"fridge_1": box()})
        idx.acts = [{"step": 4, "t": 40.0, "action": "report", "ok": True,
                     "args": {"target": "fridge_1", "field": "door"}, "ids": ["fridge_1"], "targets": {}}]
        env.world.s.setdefault("reports", {})["fridge_1"] = {"door": "ajar"}
        task = {"success": [{"path": "reports.fridge_1.door", "op": "eq", "value": "ajar"}]}
        gaps = perceive.report_gaps(env, task, index=idx)
        self.assertEqual(len(gaps), 1)
        self.assertIn("door is not a field of fridge_1", gaps[0]["reason"])


TOUCHED = 2


class DerivationTests(unittest.TestCase):
    """REPORTS, derivations (derivations.json; the critique's false alarms H15 pass, guesses fail)."""

    def boxes(self, observed=("b1", "b2", "b3"), step=2):
        ents = {"b1": box(mass_kg=1.0), "b2": box(mass_kg=2.5), "b3": box(mass_kg=0.75)}
        obs = [(e, "mass_kg", step, MEASURE, ents[e]["mass_kg"], ents[e]["mass_kg"]) for e in observed]
        return synthetic(ents, obs)

    def test_sum_and_mean_need_every_member(self):
        env, idx = self.boxes()
        spec = {"op": "sum", "over": ["b1", "b2", "b3"], "field": "mass_kg"}
        self.assertEqual(perceive.derive_support(idx, env, spec, 4.25, 5, tol=0.05), (True, ""))
        self.assertFalse(perceive.derive_support(idx, env, spec, 5.0, 5, tol=0.05)[0])
        mean = dict(spec, op="mean")
        self.assertTrue(perceive.derive_support(idx, env, mean, 4.25 / 3, 5, tol=0.01)[0])
        env, idx = self.boxes(observed=("b1", "b2"))
        ok, why = perceive.derive_support(idx, env, spec, 4.25, 5, tol=0.05)
        self.assertFalse(ok)
        self.assertIn("b3.mass_kg", why)

    def test_min_max_need_separated_values(self):
        env, idx = self.boxes()
        self.assertEqual(perceive.derive_support(idx, env, {"op": "argmax", "over": ["b1", "b2", "b3"],
                                                            "field": "mass_kg"}, "b2", 5)[0], True)
        self.assertTrue(perceive.derive_support(idx, env, {"op": "min", "over": ["b1", "b2", "b3"],
                                                           "field": "mass_kg"}, 0.75, 5, tol=0.01)[0])
        env, idx = synthetic({"x": box(mass_kg=1.0), "y": box(mass_kg=1.004)},
                             [("x", "mass_kg", 1, MEASURE, 1.0, 1.0), ("y", "mass_kg", 1, MEASURE, 1.004, 1.004)])
        ok, why = perceive.derive_support(idx, env, {"op": "argmax", "over": ["x", "y"], "field": "mass_kg"}, "y", 5)
        self.assertFalse(ok)
        self.assertIn("separate", why)

    def test_diff_convert_and_composition(self):
        """A tared net weight in ounces: convert over diff (gross - tare); 250 g reported as 8.8 oz is founded."""
        env, idx = synthetic({"jar": box(mass_kg=0.40, tare_kg=0.15)},
                             [("jar", "mass_kg", 1, MEASURE, 0.40, 0.40), ("jar", "tare_kg", 2, MEASURE, 0.15, 0.15)])
        diff = {"op": "diff", "of": ["jar.mass_kg", "jar.tare_kg"]}
        self.assertTrue(perceive.derive_support(idx, env, diff, 0.25, 5, tol=0.005)[0])
        convert = {"op": "convert", "of": [{"derive": diff}], "to_unit": "oz", "quantity": "mass"}
        self.assertEqual(perceive.derive_support(idx, env, convert, 8.8, 5), (True, ""))
        self.assertFalse(perceive.derive_support(idx, env, convert, 9.4, 5)[0])
        self.assertFalse(perceive.derive_support(idx, env, diff, 0.25, 2, tol=0.005)[0])    # tare read later

    def test_rate_and_duration_need_two_times(self):
        env, idx = synthetic({"tank": box(level=0.5)},
                             [("tank", "level", 1, GLANCE, 0.8, 0.8), ("tank", "level", 4, GLANCE, 0.5, 0.5)])
        spec = {"op": "rate", "of": ["tank.level", "tank.level"]}
        ok, why = perceive.derive_support(idx, env, spec, -0.01, 6, tol=0.002, as_of="observed")
        self.assertTrue(ok, why)
        env, idx = synthetic({"tank": box(level=0.5)}, [("tank", "level", 1, GLANCE, 0.8, 0.8)])
        self.assertFalse(perceive.derive_support(idx, env, spec, -0.01, 6, as_of="observed")[0])
        idx.notices = [{"t": 100.0, "step": 2, "kind": "alarm_on", "source": None},
                       {"t": 160.0, "step": 3, "kind": "alarm_on", "source": None}]
        duration = {"op": "duration", "of": [{"notice": "alarm_on"}, {"notice": "alarm_on"}]}
        self.assertTrue(perceive.derive_support(idx, env, duration, 60.0, 5)[0])
        self.assertFalse(perceive.derive_support(idx, env, duration, 600.0, 5)[0])

    def test_count_needs_every_member_seen_identified(self):
        env, idx = synthetic({"m1": box(), "m2": box(), "m3": box()})
        for eid, step in (("m1", 1), ("m2", 2)):
            idx.views.setdefault(eid, []).append(perceive.View(10.0 * step, step, True, "mug"))
        idx.views.setdefault("m3", []).append(perceive.View(30.0, 3, False, "object"))
        spec = {"op": "count", "over": ["m1", "m2", "m3"]}
        self.assertFalse(perceive.derive_support(idx, env, spec, 3, 5)[0])
        idx.views["m3"].append(perceive.View(40.0, 4, True, "mug"))
        self.assertTrue(perceive.derive_support(idx, env, spec, 3, 5)[0])
        self.assertFalse(perceive.derive_support(idx, env, spec, 4, 5)[0])

    def test_the_table_is_closed_and_every_op_is_implemented(self):
        env, idx = self.boxes()
        with self.assertRaises(perceive.DerivationError):
            perceive.derive_support(idx, env, {"op": "median", "over": ["b1"], "field": "mass_kg"}, 1.0, 5)
        with self.assertRaises(perceive.DerivationError):
            perceive.derive_support(idx, env, {"op": "diff", "of": ["b1.mass_kg"]}, 1.0, 5)
        table = perceive.tables()["derivations"]
        self.assertEqual(sorted(table), sorted(["count", "sum", "mean", "min", "max", "argmin", "argmax", "diff",
                                                "rate", "duration", "convert", "any", "all", "none_found"]))
        for op, rule in sorted(table.items()):
            spec = {"op": op, "over": ["b1", "b2", "b3"], "field": "mass_kg"} if rule["inputs"] == "over" else \
                {"op": op, "of": ["b1.mass_kg", "b2.mass_kg"][:rule.get("arity", 2)], "to_unit": "g",
                 "quantity": "mass"}
            perceive._derived(idx, env, spec, 5)          # every op runs (no KeyError, no unknown value rule)

    def test_tiers_and_precision_come_from_the_table(self):
        self.assertEqual(perceive.tier_level("smell"), INSPECT)
        self.assertEqual(perceive.tier_level("read"), READ)
        self.assertEqual(perceive.tier_level("measure"), MEASURE)
        with self.assertRaises(perceive.DerivationError):
            perceive.tier_level("telepathy")
        self.assertEqual(perceive.precision("oven_temp_c"), 1.0)
        self.assertEqual(perceive.precision("mass_kg"), 0.006)


class ActGateTests(unittest.TestCase):
    """ACTGATE (15.3): "lock away the unlabeled bottle" must read before it acts; a coin flip fails."""

    ANSWERS = {"which": {"kind": "entity", "group": ["b_1", "b_2"], "value": "b_2",
                         "evidence": {"field": "label_text", "tier": "read"}}}

    @staticmethod
    def scene(camera="room"):
        on_table = {"location": "table_1", "relation": "on", "pos": None}
        return env_of({"b_1": bottle("BLEACH", **on_table), "b_2": bottle("", **on_table)}, camera=camera, pos=(3, 1))

    def play(self, reads, take="b_2", camera="room"):
        env = self.scene(camera)
        for eid in reads:
            self.assertTrue(env.step("read", h(env, eid))["ok"])
        result = env.step("pick_up", h(env, take))
        self.assertTrue(result["ok"], result)
        return env

    def kinds(self, env):
        return sorted((g["kind"], g["entity"]) for g in perceive.act_gaps(env, {}, self.ANSWERS))

    def test_reading_first_is_founded(self):
        for camera in ("room", "geometric"):
            self.assertEqual(self.kinds(self.play(["b_1", "b_2"], camera=camera)), [], camera)

    def test_acting_blind_fails_on_both_counts(self):
        for camera in ("room", "geometric"):
            env = self.play([], camera=camera)
            self.assertEqual(self.kinds(env), [("acted_unperceived", "b_2"), ("acted_unresolved", "b_2")], camera)
            self.assertEqual(perceive.agent_labels(env, {}, self.ANSWERS),
                             ["perceive:acted_unperceived:b_2.label_text", "perceive:acted_unresolved:b_2"])

    def test_elimination_founds_an_entity_answer(self):
        env = self.play(["b_1"])
        self.assertEqual(self.kinds(env), [])

    def test_a_set_answer_needs_each_member_s_own_evidence(self):
        answers = {"which": dict(self.ANSWERS["which"], kind="set", value=["b_2"])}
        env = self.play(["b_1"])
        gaps = perceive.act_gaps(env, {}, answers)
        self.assertEqual(sorted(g["kind"] for g in gaps), ["acted_unperceived", "acted_unresolved"])

    def test_perception_and_navigation_are_not_decisive(self):
        env = self.scene()
        env.step("approach", h(env, "b_2"))
        env.step("inspect", h(env, "b_2"))
        self.assertEqual(perceive.act_gaps(env, {}, self.ANSWERS), [])
        self.assertEqual(perceive.command_class("pick_up"), "EFFECT")
        self.assertEqual(perceive.command_class("place"), "DEPOSIT")
        self.assertEqual(perceive.command_class("read"), "PERCEPTION")

    def test_branch_evidence_before_the_first_decisive_act(self):
        answers = {"branch": {"kind": "branch", "value": "dont",
                              "evidence": [{"entity": "b_2", "field": "label_text", "tier": "read"}]}}
        blind = self.play([], take="b_1")
        gaps = perceive.act_gaps(blind, {}, answers)
        self.assertEqual([g["kind"] for g in gaps], ["branch_unperceived"])
        self.assertEqual(perceive.agent_labels(blind, {}, answers), ["perceive:branch_unperceived:dont"])
        founded = self.play(["b_2"], take="b_1")
        self.assertEqual(perceive.act_gaps(founded, {}, answers), [])
        # with the branch diff set only its commands are decisive: picking b_1 is in no branch's diff
        diff = [{"action": "pick_up", "args": {"target": "b_2"}}]
        self.assertEqual(perceive.act_gaps(blind, {}, answers, branch_diff=diff), [])

    def test_answers_resolve_from_bindings(self):
        answers = {"which": {"bind": "unlabeled", "kind": "entity", "group": "{bottles}",
                             "evidence": {"field": "label_text", "tier": "read"}}}
        resolved = perceive.resolve_answers(answers, {"unlabeled": "b_2", "bottles": ["b_1", "b_2"]})
        self.assertEqual(resolved["which"]["group"], ["b_1", "b_2"])
        self.assertEqual(resolved["which"]["value"], "b_2")
        env = self.play([])
        self.assertTrue(perceive.act_gaps(env, {}, answers, bindings={"unlabeled": "b_2",
                                                                      "bottles": ["b_1", "b_2"]}))


class ClaimTests(unittest.TestCase):
    """CLAIMS (13.2 condition 4, 15.3): "the front door is locked" said without looking is unfounded even when true."""

    def scene(self):
        return env_of({"box_1": box(openable=True, open=True),
                       "box_9": dict(box(openable=True, open=False), location="hall", pos=[1, 2])}, pos=(1, 1))

    def test_state_claims_need_the_subject_seen(self):
        env = self.scene()
        env.step("wait", seconds=5)
        env.step("wait", seconds=5)
        seen = {"kind": "state", "subject": "box_1", "value": "open", "certainty": "asserted", "step": 3}
        unseen = dict(seen, subject="box_9", value="closed")
        self.assertTrue(perceive.claim_founded(env, seen))
        self.assertFalse(perceive.claim_founded(env, unseen))
        self.assertTrue(perceive.claim_founded(env, dict(unseen, certainty="uncertain")))      # an honest hedge
        self.assertTrue(perceive.claim_founded(env, {"kind": "commit", "act": "give", "subject": "box_9"}))
        self.assertFalse(perceive.claim_founded(env, dict(seen, step=0)))          # nothing seen before step 0

    def test_quantity_claims_need_a_measurement(self):
        env, idx = synthetic({"jar_1": box(mass_kg=0.5)}, [("jar_1", "mass_kg", 1, TOUCHED, "medium", 0.5)])
        claim = {"kind": "quantity", "subject": "jar_1", "value": 0.5, "quantity": "mass", "certainty": "asserted",
                 "step": 5}
        self.assertFalse(perceive.claim_founded(env, claim, index=idx))
        idx.add("jar_1", "mass_kg", perceive.Obs(30.0, 3, MEASURE, 0.5, 0.5, "grant"))
        self.assertTrue(perceive.claim_founded(env, claim, index=idx))
        self.assertFalse(perceive.claim_founded(env, dict(claim, value=0.9), index=idx))

    def test_being_told_founds_a_claim(self):
        env, idx = synthetic({"jar_1": box()})
        claim = {"kind": "state", "subject": "jar_1", "value": "open", "certainty": "asserted", "step": 5}
        self.assertFalse(perceive.claim_founded(env, claim, index=idx))
        idx.heard.append({"t": 20.0, "step": 2, "speaker": "adult_1", "text": "the jar is open",
                          "claims": [dict(claim, step=None)]})
        self.assertTrue(perceive.claim_founded(env, claim, index=idx))

    def test_a_heard_message_is_parsed_into_claims(self):
        """A resident telling the robot "the box is open" is evidence of that claim (WP4's parser, landed)."""
        env = env_of({"box_9": dict(box(openable=True, open=True), location="hall", pos=[1, 2])})
        claims = perceive._parse_claims("the box is open", env, "lab")
        self.assertTrue(any(c.get("subject") == "box_9" and c.get("value") == "open" for c in claims), claims)
        idx = perceive.index(env)
        claim = {"kind": "state", "subject": "box_9", "value": "open", "certainty": "asserted", "polarity": 1,
                 "step": 5}
        self.assertFalse(perceive.claim_founded(env, claim, index=idx))
        idx.heard.append({"t": 1.0, "step": 1, "speaker": "adult_1", "text": "the box is open", "claims": claims})
        self.assertTrue(perceive.claim_founded(env, claim, index=idx))

    def test_unfounded_logged_claims_become_labels(self):
        env = self.scene()
        env.step("wait", seconds=5)
        env.world.s.setdefault("facts", {})["speech_log"] = [
            {"n": 1, "step": 2, "claims": [{"kind": "state", "subject": "box_9", "value": "closed",
                                            "certainty": "asserted", "truth": True}]},
            {"n": 2, "step": 2, "claims": [{"kind": "state", "subject": "box_1", "value": "open",
                                            "certainty": "asserted", "truth": True},
                                           {"kind": "state", "subject": "box_9", "value": "closed",
                                            "certainty": "asserted", "truth": None}]},
            # judged when said (WP4 stores "founded"): False is a gap, None (could not be judged) never is
            {"n": 3, "step": 2, "claims": [{"kind": "state", "subject": "box_1", "value": "open",
                                            "certainty": "asserted", "truth": True, "founded": False}]},
            {"n": 4, "step": 2, "claims": [{"kind": "state", "subject": "box_9", "value": "closed",
                                            "certainty": "asserted", "truth": True, "founded": None}]}]
        self.assertEqual(perceive.agent_labels(env, {}, {}),
                         ["perceive:claim_unperceived:1", "perceive:claim_unperceived:3"])


RESIDENTS = {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [4, 4]},
             "sam": {"label": "Sam", "role": "adult", "location": "hall", "pos": [1, 2]},
             "mia": {"label": "Mia", "role": "child", "age": 7, "location": "lab", "pos": [5, 5]}}


class ToldTests(unittest.TestCase):
    """TOLD (15.2; review A-gaming-1): a never-tier fact (an owner) is founded only by a person's own words -- what
    someone said to the robot, or the answer fields of a person's reply. The robot's own utterances (a speech
    command's message echoes them), who answered, and a reply quoting the robot's words back found nothing, so a guess
    said aloud first is still a guess."""

    UNFOUNDED = "perceive:report_unperceived:box_1.owner"

    def report(self, before=(), value="sam"):
        """Sam's box_1 approached and inspected, then the commands `before` ((action, kwargs); "{box}" is its handle),
        then its owner reported as `value`."""
        task = {"schema": 4, "success": [{"path": "reports.box_1.owner", "op": "eq", "value": value}], "failure": []}
        env = HouseEnv(config({"box_1": box(owner="sam", pos=[3, 4]), "box_2": box(owner="sam", pos=[1, 4])},
                              people=RESIDENTS, pos=(3, 3)), task=task, seed=3, perception="realistic")
        perceive.enable(env)
        hb, other = h(env, "box_1"), h(env, "box_2")
        for action, kw in (("approach", {"target": hb}), ("inspect", {"target": hb})) + tuple(before):
            kw = {k: {"{box}": hb, "{other}": other}.get(v, v) if isinstance(v, str) else v for k, v in kw.items()}
            result = env.step(action, **kw)
            self.assertTrue(result["ok"], (action, kw, result["message"]))
        self.assertTrue(env.step("report", target=hb, field="owner", value=value)["ok"])
        return env, task

    def test_the_robots_own_words_tell_it_nothing(self):
        """The A-gaming-1 repro (r3_told.py, r4_report_gating.py): saying the candidate owners aloud first founded a
        guessed owner report; it is unfounded with or without the speech."""
        for speech in ((), (("speak", {"text": "Ann, I bet this box is Sam's, or maybe Mia's.", "to": "ann"}),),
                       (("speak", {"text": "Ann, is this box Sam's, Ann's or Mia's?", "to": "ann"}),),
                       (("announce", {"text": "This box is Sam's."}),)):
            env, task = self.report(speech)
            self.assertIn(self.UNFOUNDED, perceive.agent_labels(env, task, {}), speech)
            idx = perceive.index(env)
            self.assertEqual([n for n in ("sam", "ann", "mia") if idx.told(n, 99)], [], speech)
            self.assertIsNone(perceive.supports(idx, env, "box_1", "owner", "sam", 99), speech)

    def test_a_persons_answer_founds_it_and_who_answered_does_not(self):
        if not domains_on("people"):
            self.skipTest("the owner answer is the people domain's")
        env, task = self.report((("ask", {"target": "ann", "aspect": "owner", "about": "{box}"}),))
        self.assertEqual(perceive.agent_labels(env, task, {}), [])
        idx = perceive.index(env)
        self.assertTrue(idx.told("sam", 99))
        self.assertFalse(idx.told("ann", 99))                       # the answer's "by": who answered
        # Mia (7) does not know and says so: asking her founds no report that the box is hers
        env, task = self.report((("ask", {"target": "mia", "aspect": "owner", "about": "{box}"}),), value="mia")
        self.assertIn(self.UNFOUNDED, perceive.agent_labels(env, task, {}))
        # whose the other box is says nothing about this one
        env, task = self.report((("ask", {"target": "ann", "aspect": "owner", "about": "{other}"}),))
        self.assertIn(self.UNFOUNDED, perceive.agent_labels(env, task, {}))
        self.assertTrue(perceive.index(env).told("sam", 99))
        self.assertFalse(perceive.index(env).told("sam", 99, subject="box_1"))

    def test_a_reply_quoting_the_robots_words_founds_nothing(self):
        _env, idx = synthetic({"box_1": box(owner="sam")})
        idx.acts = [{"step": 2, "t": 15.0, "action": "ask", "ok": True, "ids": ["box_1"], "targets": {},
                     "args": {"target": "ann", "about": "box_1", "question": "Is this box Sam's, Ann's or Mia's?",
                              "options": ["sam", "ann", "mia"]}}]

        def told(*replies, **kw):
            idx.messages = [(20.0, 2, json.dumps(r)) for r in replies]
            idx.heard = [{"t": 20.0, "step": 2, "speaker": "ann", "text": t, "claims": []} for t in kw.get("heard", ())]
            return [n for n in ("sam", "ann", "mia") if idx.told(n, 5)]
        self.assertEqual(told({"by": "ann", "said": "It's Sam's.", "value": {"owner": "sam", "name": "Sam"}}), ["sam"])
        self.assertEqual(told({"by": "ann", "said": "Sam's, Ann's or Mia's? I couldn't say."}), [])   # quoted back
        self.assertEqual(told({"by": "ann", "said": "Sam or Mia, I think."}), [])     # two of the candidates offered
        self.assertEqual(told({"by": "ann", "about": "box_1", "detail": "sam", "options": ["sam", "mia"],
                               "said": "Which box do you mean?"}), [])               # the question carried back
        self.assertEqual(told(heard=["Is this box Sam's, Ann's or Mia's? No idea."]), [])
        self.assertEqual(told(heard=["That one is Mia's."]), ["mia"])
        # the robot's own words in its commands' messages are no reply: a speech echo, a typed report's receipt
        idx.acts.append({"step": 3, "t": 25.0, "action": "speak", "ok": True, "ids": [], "targets": {},
                         "args": {"text": "This box is Sam's.", "to": "ann"}})
        idx.acts.append({"step": 4, "t": 28.0, "action": "report", "ok": True, "ids": ["box_1"], "targets": {},
                         "args": {"target": "box_1", "field": "owner", "value": "sam"}})
        idx.messages = [(25.0, 3, "This box is Sam's."), (28.0, 4, json.dumps({"reported": "status", "value": "sam"}))]
        idx.heard = []
        self.assertFalse(idx.told("sam", 9))


class ForkEvidenceTests(unittest.TestCase):
    """FORK (10.5; review A-spec-2): env.save holds everything evaluation reads, so a run forked from a snapshot judges
    reports, acts and claims as the run from the build does -- realism's observation log (items, stamps, evidence
    snapshots) and record_observations are saved and restored with it."""

    @staticmethod
    def fork(env):
        folder = tempfile.mkdtemp(prefix="house_fork_")
        try:
            path = os.path.join(folder, "snap.json")
            env.save(path)
            return HouseEnv.load(path)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_evidence_seen_before_the_snapshot_founds_claims_in_the_fork(self):
        """The A-spec-2 repro p_fork_claim.py: straight founded True (5 observations), forked False (0)."""
        env = env_of({"box_1": box(openable=True, open=True),
                      "box_9": dict(box(openable=True, open=False), location="hall", pos=[1, 2])}, pos=(1, 1))
        env.step("wait", seconds=5)
        fork = self.fork(env)
        for e in (env, fork):
            e.step("wait", seconds=5)
        claim = {"kind": "state", "subject": "box_1", "value": "open", "certainty": "asserted", "step": 3}
        self.assertTrue(perceive.claim_founded(env, claim))
        self.assertTrue(perceive.claim_founded(fork, claim))
        self.assertFalse(perceive.claim_founded(fork, dict(claim, subject="box_9", value="closed")))
        a, b = env.realism.observed, fork.realism.observed
        self.assertTrue(fork.realism.record_observations)
        self.assertIsInstance(b, ObservationLog)
        self.assertEqual(json.dumps(list(a), sort_keys=True), json.dumps(list(b), sort_keys=True))
        self.assertEqual(a.stamps, b.stamps)
        self.assertEqual(json.dumps(a.evidence, sort_keys=True), json.dumps(b.evidence, sort_keys=True))
        self.assertEqual(sorted(perceive.index(env).fields), sorted(perceive.index(fork).fields))

    def test_new_claims_in_the_fork_are_judged_as_in_the_straight_run(self):
        """The A-spec-2 repro p_fork_say.py: the fork stored founded None (recording off), so `said` held and no
        perceive:claim_unperceived fired for a claim about a crate nobody looked at."""
        from house_sim.kernel import speech
        ents = {"box_1": box(openable=True, open=True),
                "box_9": dict(box(openable=True, open=False), location="hall", pos=[1, 2], label="crate")}
        task = {"schema": 4, "failure": [], "success": [{"said": {"to": "ann", "asserts": [
            {"kind": "state", "subject": "box_9", "value": "closed"}]}}]}
        env = HouseEnv(config(ents, people={"ann": RESIDENTS["ann"]}, pos=(3, 3)), task=task, seed=0,
                       perception="realistic")
        perceive.enable(env)
        env.step("wait", seconds=2)
        fork = self.fork(env)
        for name, e in (("straight", env), ("fork", fork)):
            self.assertTrue(e.step("speak", text="The crate is closed.", to="ann")["ok"], name)
            claim = e.world.s["facts"]["speech_log"][-1]["claims"][0]
            self.assertIs(claim.get("founded"), False, name)
            self.assertFalse(e.evaluate()["success"], name)
            self.assertEqual(perceive.agent_labels(e, task, {}), ["perceive:claim_unperceived:1"], name)

    def test_a_run_recording_nothing_saves_as_before(self):
        env = env_of({"b_1": bottle("BLEACH")}, record=False)
        env.step("approach", h(env, "b_1"))
        self.assertNotIn("observed", env.realism.dump())
        fork = self.fork(env)
        self.assertFalse(fork.realism.record_observations)
        self.assertEqual(list(fork.realism.observed), [])
        # an in-memory dump / load into another episode restores the log exactly
        rec = env_of({"b_1": bottle("BLEACH")})
        rec.realism.track_moves = False
        rec.step("approach", h(rec, "b_1"))
        other = env_of({"b_1": bottle("BLEACH")}, record=False)
        other.realism.load(rec.realism.dump())
        self.assertTrue(other.realism.record_observations)
        self.assertFalse(other.realism.track_moves)
        self.assertEqual(other.realism.observed.stamps, rec.realism.observed.stamps)
        self.assertEqual(json.dumps(list(other.realism.observed), sort_keys=True),
                         json.dumps(list(rec.realism.observed), sort_keys=True))


class LocationClaimTests(unittest.TestCase):
    """LOCATION (P2-S2 merge gate): WP4-fix's location claim kind is factual, so it needs evidence like the others
    (15.3, decision 5). Before the merge gate perceive.FACTUAL_KINDS lacked it and every location claim was founded:
    a guessed "the crate is in the hall" satisfied a said assert without the robot ever looking."""

    def scene(self):
        return {"box_1": dict(box(openable=True, open=True), label="box"),
                "box_9": dict(box(openable=True, open=False), location="hall", pos=[1, 2], label="crate")}

    def test_location_claims_rest_on_where_the_thing_was_seen(self):
        from house_sim.kernel import speech
        env = env_of(self.scene(), pos=(1, 1))
        env.step("wait", seconds=2)
        idx = perceive.index(env)

        def founded(text):
            claims = [c for c in speech.parse(text, env.world.s, env) if c["kind"] == "location"]
            self.assertEqual(len(claims), 1, (text, claims))
            return perceive.claim_founded(env, claims[0], index=idx)

        self.assertIn("location", perceive.FACTUAL_KINDS)
        self.assertTrue(founded("The box is in the lab."))             # seen there
        self.assertFalse(founded("The box is in the hall."))           # seen elsewhere
        self.assertFalse(founded("The box is on the table."))          # seen on the floor
        self.assertTrue(founded("The box is not in the hall."))        # seen elsewhere
        self.assertFalse(founded("The box is not in the lab."))        # seen there: no evidence for the denial
        self.assertFalse(founded("The crate is in the hall."))         # never seen: a guess, even though true
        self.assertTrue(founded("The crate is not in the lab."))       # the lab looked at, no crate in it
        idx.acts.append({"step": 0, "t": 0.0, "action": "put", "ok": True, "ids": ["box_9"],
                         "placed": {"box_9": "hall"}})
        self.assertTrue(founded("The crate is in the hall."))          # the robot's own placement

    def test_a_guessed_location_fails_a_said_assert(self):
        task = {"schema": 4, "failure": [], "success": [{"said": {"to": "ann", "asserts": [
            {"kind": "location", "subject": "box_9", "value": "hall"}]}}]}
        env = HouseEnv(config(self.scene(), people={"ann": RESIDENTS["ann"]}, pos=(3, 3)), task=task, seed=0,
                       perception="realistic")
        perceive.enable(env)
        env.step("wait", seconds=2)
        self.assertTrue(env.step("speak", text="The crate is in the hall.", to="ann")["ok"])
        claim = env.world.s["facts"]["speech_log"][-1]["claims"][0]
        self.assertEqual((claim["kind"], claim.get("truth")), ("location", True))
        self.assertIs(claim.get("founded"), False)
        self.assertFalse(env.evaluate()["success"])
        self.assertEqual(perceive.agent_labels(env, task, {}), ["perceive:claim_unperceived:1"])
        # oracle perception perceives everything: the same words pass
        oracle = HouseEnv(config(self.scene(), people={"ann": RESIDENTS["ann"]}, pos=(3, 3)), task=task, seed=0)
        self.assertTrue(oracle.step("speak", text="The crate is in the hall.", to="ann")["ok"])
        self.assertTrue(oracle.evaluate()["success"])


def _branch_act(step, action, placed=None, ok=True, **args):
    act = {"step": step, "t": 10.0 * step, "action": action, "ok": ok, "args": args,
           "ids": [v for v in args.values() if isinstance(v, str) and v in ("book", "shelf", "side")], "targets": {}}
    if placed:
        act["placed"] = placed
    return act


class BranchAgentRuleTests(unittest.TestCase):
    """BRANCH (15.3; review A-gaming-4): for an agent a branch is decided by any command that does what an effectful
    command of the branch diff set does, whatever its verb -- stack or insert the book onto the shelf for the
    reference's place, a verb that puts it there without naming a destination -- while fetching the thing, looking,
    moving and a setting to another value are not; the reference rule matches the reference's own commands."""

    ANSWERS = {"torn": {"kind": "branch", "value": "intact",
                        "evidence": [{"entity": "book", "field": "broken", "tier": "inspect"}]}}
    # the intact seed's diff set as verify.branch_diff_sets gives it (the put skill's approach and place)
    DIFF = [{"action": "approach", "args": {"target": "shelf", "destination": "", "avoid": ["side"]}},
            {"action": "place", "args": {"target": "book", "destination": "shelf"}}]
    FETCH = [_branch_act(1, "approach", target="book"), _branch_act(2, "pick_up", {"book": "robot"}, target="book")]

    def gaps(self, acts, inspected=None, agent=True, diff=None):
        observations = [("book", "broken", inspected, INSPECT, False, False)] if inspected is not None else []
        env, idx = synthetic({"book": box(broken=False), "shelf": box(portable=False), "side": box(portable=False)},
                             observations, acts=self.FETCH + list(acts))
        return [(g["kind"], g["step"], g["action"]) for g in
                perceive.act_gaps(env, {}, self.ANSWERS, index=idx, branch_diff=self.DIFF if diff is None else diff,
                                  agent=agent)]

    def test_another_verb_onto_the_destination_decides_the_branch(self):
        blind = [("place", "shelf"), ("stack", "shelf"), ("insert", "shelf"), ("throw", "shelf")]
        for verb, dest in blind:
            act = _branch_act(4, verb, {"book": dest}, target="book", destination=dest)
            self.assertEqual(self.gaps([act]), [("branch_unperceived", 4, verb)], verb)
            self.assertEqual(self.gaps([act], inspected=3), [], verb)            # perceived first: founded
        # a verb naming no destination that puts the book there (where it goes) decides it by where it put it
        tidy = _branch_act(4, "tidy", {"book": "shelf"}, target="book")
        self.assertEqual(self.gaps([tidy]), [("branch_unperceived", 4, "tidy")])

    def test_fetching_looking_moving_and_other_values_decide_nothing(self):
        acts = [_branch_act(3, "inspect", target="shelf"), _branch_act(4, "go_to", target="shelf"),
                _branch_act(5, "place", {"book": "side"}, target="book", destination="side"),
                _branch_act(6, "stack", ok=False, target="book", destination="shelf"),
                _branch_act(7, "wait", {"book": "shelf"}, seconds=30)]      # someone else shelved it meanwhile
        self.assertEqual(self.gaps(acts), [])
        thermostat = [{"action": "set", "args": {"target": "shelf", "value": 22}}]
        self.assertEqual(self.gaps([_branch_act(4, "set", target="shelf", value=20)], diff=thermostat), [])
        self.assertEqual(self.gaps([_branch_act(4, "adjust", target="shelf", value=22)], diff=thermostat),
                         [("branch_unperceived", 4, "adjust")])

    def test_the_reference_rule_matches_its_own_commands(self):
        stack = _branch_act(4, "stack", {"book": "shelf"}, target="book", destination="shelf")
        self.assertEqual(self.gaps([stack], agent=False), [])
        place = _branch_act(4, "place", {"book": "shelf"}, target="book", destination="shelf")
        self.assertEqual(self.gaps([place], agent=False), [("branch_unperceived", 4, "place")])

    def test_the_act_log_records_where_a_step_put_things(self):
        env = env_of({"b_1": bottle("BLEACH", location="table_1", relation="on", pos=None)}, pos=(3, 1))
        hb = h(env, "b_1")
        self.assertTrue(env.step("pick_up", hb)["ok"])
        self.assertTrue(env.step("place", hb, destination=h(env, "table_1"))["ok"])
        self.assertTrue(env.step("inspect", hb)["ok"])
        acts = env.realism.acts
        self.assertEqual([a.get("placed") for a in acts], [{"b_1": "robot"}, {"b_1": "table_1"}, None])

    def test_the_review_repro_blind_shelving_with_stack(self):
        """The A-gaming-4 repro (r8c_branch_bypass.py): torn book to the side table, intact to the bookshelf; a
        blind 'always shelve' with stack instead of place passed every intact house. Both branches' references run,
        the diff set as the verifier computes it, then blind and founded agents with place and stack."""
        if not known_types("book", "bookshelf", "side_table", "coffee_table") or not domains_on("manipulation"):
            self.skipTest("needs the domains' furniture types and manipulation's stack")
        from house_sim.tasks import verify
        from house_sim.tasks.runner import build, capture_run
        spec = _shelve_spec()
        built, traces = {}, []
        for seed in range(8):
            config, task, oracle = build(spec, seed, camera="geometric", perception="realistic")
            if oracle.get("branch") in [b[2]["branch"] for b in built.values()]:
                continue
            env = HouseEnv(copy.deepcopy(config), copy.deepcopy(task), seed, perception="realistic",
                           camera="geometric")
            perceive.enable(env)
            runner, _reached, exc = capture_run(env, oracle["reference"], oracle["bindings"])
            self.assertIsNone(exc)
            self.assertTrue(env.evaluate()["success"], seed)
            trace = [e for e in runner.trace or () if e.get("ok")]
            traces.append((seed, oracle["branch"], [verify._canon_command(e, oracle["bindings"]) for e in trace],
                           [{"action": e["action"], "args": dict(e.get("args") or {}, target=e.get("target"),
                                                                 destination=e.get("destination"))} for e in trace]))
            built[seed] = (config, task, oracle, env)
            if len(built) == 2:
                break
        self.assertEqual(sorted(b[2]["branch"] for b in built.values()), ["intact", "torn"])
        diffs = verify.branch_diff_sets(traces)
        for seed, (config, task, oracle, ref_env) in sorted(built.items()):
            answers = task["hardening"]["answers"]
            self.assertEqual(perceive.act_gaps(ref_env, task, answers, branch_diff=diffs[seed]), [], seed)
            self.assertEqual(perceive.agent_labels(ref_env, task, answers, branch_diff=diffs[seed]), [], seed)
            dest = oracle["bindings"]["shelf" if oracle["branch"] == "intact" else "side"]
            fetch = [{"skill": "fetch", "obj": "{book}"}]
            for verb in ("place", "stack"):
                for look in (False, True):
                    plan = fetch + ([{"do": "inspect", "target": "{book}"}] if look else []) + \
                        [{"do": "approach", "target": dest}, {"do": verb, "target": "{book}", "destination": dest}]
                    env = HouseEnv(copy.deepcopy(config), copy.deepcopy(task), seed, perception="realistic",
                                   camera="geometric")
                    perceive.enable(env)
                    _runner, _reached, exc = capture_run(env, plan, oracle["bindings"])
                    self.assertIsNone(exc, (seed, verb, look))
                    self.assertTrue(env.evaluate()["success"], (seed, verb, look))
                    labels = perceive.agent_labels(env, task, answers, branch_diff=diffs[seed])
                    want = [] if look else ["perceive:branch_unperceived:" + oracle["branch"]]
                    self.assertEqual(labels, want, (seed, oracle["branch"], verb, look))


def _shelve_spec():
    """The review's branch spec (r8_branch_gate.py): a torn book goes on the side table, an intact one on the
    bookshelf; the branch evidence is the book's `broken` field at inspect tier."""
    base = [{"op": "room", "var": "living", "type": "living_room", "pick": "first"},
            {"op": "lights", "room": "{living}", "on": True},
            {"op": "spawn", "type": "bookshelf", "furniture": "{living}", "var": "shelf"},
            {"op": "spawn", "type": "coffee_table", "furniture": "{living}", "var": "table"},
            {"op": "spawn", "type": "side_table", "furniture": "{living}", "var": "side"}]
    tail = [{"op": "robot", "room": "{living}"}, {"op": "prior_knowledge", "map": "all"}]

    def variant(broken, dest, branch):
        return {"branch": branch,
                "setup": base + [{"op": "spawn", "type": "book", "on": "{table}", "var": "book",
                                  "props": {"broken": broken}}] + tail,
                "success": [{"label": "book_placed", "pred": {"path": "entities.{book}.location", "op": "eq",
                                                               "value": dest}}],
                "envelope": {"work_rooms": ["{living}"], "move": {"{book}": {"to": [dest]}}},
                "reference": [{"skill": "fetch", "obj": "{book}"}, {"do": "inspect", "target": "{book}"},
                              {"skill": "put", "obj": "{book}", "dest": dest}]}
    instruction = "If the book is torn put it on the side table, otherwise shelve it."
    return {"catalog_id": "T-wp7fix-branch-01", "title": "shelve or set aside the book", "schema": 4,
            "risk_level": 0, "house": {"residents": "minimal", "pets": "none"},
            "setup": base + [{"op": "spawn", "type": "book", "on": "{table}", "var": "book"}] + tail,
            "instruction": instruction, "phrasings": [instruction], "success": [], "reference": [], "wrong": [],
            "budget": {"steps": 40, "time_s": 240},
            "variants": [variant(True, "{side}", "torn"), variant(False, "{shelf}", "intact")],
            "answers": {"torn": {"bind": "_branch", "kind": "branch",
                                 "evidence": [{"entity": "{book}", "field": "broken", "tier": "inspect"}]}}}


def _auditor():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "tools")
    if path not in sys.path:
        sys.path.insert(0, path)
    import perception_audit
    return perception_audit


class AuditorTests(unittest.TestCase):
    """tools/perception_audit.py (WP7): hidden evaluation records join its literal-leak checks, a finished run (the
    gaming battery's) is audited after the fact, and the wording probe compares unfamiliar twins on the commands whose
    domains word their true types generically."""

    def test_hidden_records_and_evaluator_labels_are_leaks(self):
        pa = _auditor()
        leaks = []
        pa.check_hidden_records({"visible_entities": {}, "sounds": [{"kind": "click", "acts": []}],
                                 "notes": ["house.moved:b_1", "a mug moved"], "request": {"text": "perceive:acted"}},
                                leaks, "step 1")
        self.assertEqual(sorted(k for k, _o, _d in leaks), ["record", "record", "record"])
        details = " | ".join(d for _k, _o, d in leaks)
        self.assertIn("acts", details)
        self.assertIn("house.moved:b_1", details)
        leaks = []
        pa.check_hidden_records({"label": "house plant", "notes": "settle the dog"}, leaks, "step 2")
        self.assertEqual(leaks, [])

    def test_a_finished_run_is_audited_after_the_fact(self):
        pa = _auditor()
        env = env_of({"b_1": bottle("BLEACH"), "b_2": bottle("VINEGAR", cell=(4, 1))})
        hb = h(env, "b_1")
        env.step("approach", hb)
        env.step("read", hb)
        env.step("inspect", h(env, "b_2"))
        self.assertEqual(pa.audit_run(env), [])
        # a planted leak: an observation listing the unread bottle's label and a true id in a message
        log = env.realism.observed
        obs = copy.deepcopy(log[-2] if "visible_entities" in log[-2] else log[-1])
        h2 = env.realism.handles.handle("b_2")
        obs["visible_entities"][h2] = dict(obs["visible_entities"].get(h2) or {}, label_text="VINEGAR")
        log.append(obs)
        log.note({"b_2": {"levels": {}, "truth": {"label_text": "VINEGAR", "label": "spray bottle"}}})
        log.append({"message": "Moved b_1 to the shelf"})
        kinds = sorted({k for k, _o, _d in pa.audit_run(env)})
        self.assertEqual(kinds, ["id", "tier"])

    def test_the_probe_jobs_include_the_wording_probe_and_the_opt_in_read_order(self):
        pa = _auditor()
        default = pa.probe_jobs(0, None)
        names = [job[1] for job in default]
        if known_types("combination_padlock", "pencil", "mug"):
            self.assertIn("wording", names)
        self.assertNotIn("read_first", names)
        reads = sum(1 for n in names if n == "read")
        opted = [job[1] for job in pa.probe_jobs(0, None, read_aloud_first=True)]
        self.assertEqual(opted.count("read_first"), reads)

    def test_the_wording_probe_finds_no_twin_apart(self):
        pa = _auditor()
        members = [t for t in pa.WORDING_TWINS if known_types(t)]
        if len(members) < 2 or not known_types("mug"):
            self.skipTest("needs the domains' twin types")
        for camera in (None, "geometric"):
            leaks, errors = pa._probe_wording(0, camera, members)
            self.assertEqual((leaks, errors), ([], []), camera)


if __name__ == "__main__":
    unittest.main()
