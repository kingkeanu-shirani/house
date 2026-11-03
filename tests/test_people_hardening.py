"""Tests for the people domain's verification-hardening package (docs/design/VERIFY_HARDENING.md section 25, row "WP-D
<domain>", and 23.3; catalog/analysis/phase2/handoff_WP-D_people.md): field classes for every property of the people
types, writer classes and causes of the people systems (people as volition), truth mappings for claims about people,
relays scored by claims, structured thermometer readings, attendance only by the robot when asked to watch, a mobility
aid's trip-hazard flag that follows where it is, seeded placement of look-alike members, and the phase-1 carry-over
rows (look-alike and unfamiliar twins answered alike by every people verb).

Run in isolation:   HOUSE_SIM_DOMAINS=core,people python -m unittest tests/test_people_hardening.py
Strict journaling:  HOUSE_SIM_STRICT=1 python -m unittest tests/test_people_hardening.py
"""
import copy
import json
import unittest

from house_sim.generator import generate_house
from house_sim.kernel import ledger, speech
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, BEFORE, SYSTEM_WRITERS, load_domains
from house_sim.kernel.registry import default_registry
from house_sim.tasks.runner import Runner
from house_sim.tasks.setup import apply_setup

load_domains()

PEOPLE_SYSTEMS = {"people.calendar": "physical", "people.behavior": "volition", "people.routine": "volition",
                  "people.wandering": "volition", "people.visitors": "volition", "people.senses": "volition",
                  "people.children": "volition", "people.reach": "physical", "people.care": "physical",
                  "people.watch": "volition", "people.room_metrics": "physical"}


def build(ops, seed=2, residents="minimal", start="2026-10-09T10:00:00", perception="oracle", camera=None, schema=None,
          mutate=None, **house):
    opts = {"residents": residents, "pets": "none", "start": start}
    opts.update(house)
    config = generate_house(seed, **opts)
    spec = {"catalog_id": "test_people_hardening"}
    if schema is not None:
        spec["schema"] = schema
    bindings = apply_setup(config, ops, default_registry(), seed, spec)
    if mutate is not None:
        mutate(config, bindings)
    kw = {"perception": perception}
    if camera:
        kw["camera"] = camera
    env = HouseEnv(config, seed=seed, **kw)
    return env, bindings, Runner(env, bindings)


def ok(test, env, action, target="", destination="", **kw):
    result = env.step(action, target, destination, **kw)
    test.assertTrue(result["ok"], "{} {} failed: {}".format(action, target, result["message"]))
    return result


def hardened(envelope=None, protections=None):
    return {"success": [], "failure": [], "hardening": {"envelope": envelope or {}, "protections": protections or {}}}


def house_labels(env, envelope=None, protections=None):
    return ledger.failures(hardened(envelope, protections), env.world.s, env._baseline, env.world, None)


def person(env, pid):
    return env.world.s["people"][pid]


def plog(env):
    return env.world.s.get("people_log") or {}


def twin_outcome(type_name, verb, seed=2, camera="room", props=None, hold=False, extra=None, **kw):
    """(ok, category, message) of verb on an unresolved or unfamiliar thing of type_name on the living-room coffee
    table (or held), in realistic perception. Handles depend only on the seed and the house, so twins compare whole."""
    reg = default_registry()
    ops = [{"op": "find", "var": "table", "type": "coffee_table", "room": "living_room"},
           {"op": "spawn", "var": "thing", "type": type_name, "on": "{table}", "eid": "thing_1", "props": props or {}},
           {"op": "person", "id": "resident_1", "room": "living_room"},
           {"op": "robot", "room": "living_room"}]
    config = generate_house(seed, residents="minimal", pets="none", start="2026-10-09T10:00:00")
    bindings = apply_setup(config, ops + list(extra or ()), reg, seed, {"catalog_id": "test_people_twins"})
    if hold:
        # held from the start (an unresolved thing that could be hazardous is not picked up before it is identified)
        config["robot"]["hands"] = dict(config["robot"].get("hands") or {"left": None, "right": None}, right="thing_1")
        rec = config["entities"]["thing_1"]
        rec.update(location="robot", relation="held", held_hand="right")
        for key in ("pos", "spos", "shelf"):
            rec.pop(key, None)
    env = HouseEnv(copy.deepcopy(config), seed=seed, perception="realistic", camera=camera)
    handle = env.realism.handles.handle(bindings["thing"])
    if not hold:
        assert env.step("approach", handle)["ok"]
    else:
        assert env.step("approach", env.realism.handles.handle(bindings["table"]))["ok"]     # within reach of the table
    if camera == "geometric":
        env.step("look_at", handle)
    args = dict(kw)
    for key, value in list(args.items()):
        if isinstance(value, str) and value.startswith("<") and value.endswith(">") and value[1:-1] in bindings:
            args[key] = env.realism.handles.handle(bindings[value[1:-1]])
    target = args.pop("target", handle)
    result = env.step(verb, target, args.pop("destination", ""), **args)
    message = result["message"].replace(handle, "<thing>")
    return result["ok"], result["category"], message


NOVEL = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "label": "thing"}


# ----------------------------------------------------------------------
# field classes (6.3, L-WATCH part 2)

class WatchCensusTests(unittest.TestCase):
    def test_every_property_of_every_people_type_is_classified(self):
        """L-WATCH part 2 for the people types: every property of house_sim/data/types/people.json has an explicit or
        kernel class (tools/field_census.py --unclassified --domain people lists none)."""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        types = json.loads((root / "house_sim" / "data" / "types" / "people.json").read_text(encoding="utf-8"))["types"]
        ledger.clear_cache()
        missing = sorted((t, f) for t, rec in types.items() for f in rec.get("props", rec)
                         if not f.startswith("_") and ledger.field_spec(t, f, explicit_only=True) is None)
        self.assertEqual(missing, [])

    def test_the_watch_file_names_known_classes_and_types(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "house_sim" / "data" / "verify" / "watch" / "people.json").read_text(encoding="utf-8"))
        classes = {"placement", "setting", "condition", "identity", "content", "process", "bookkeeping"}
        specs = list(data["fields"].values()) + [v for fields in data["types"].values() for v in fields.values()]
        for spec in specs:
            cls = spec if isinstance(spec, str) else spec["class"]
            self.assertIn(cls, classes, spec)
            if isinstance(spec, dict) and "worse" in spec:
                self.assertIn(spec["worse"], ("true", "false", "up", "down", "change"))
        reg = default_registry()
        for type_name in list(data["types"]) + data["water_sensitive"]:
            self.assertTrue(reg.known(type_name), type_name)
        self.assertIn("hearing_aid", ledger.domain_watch()["water_sensitive"])

    def test_the_classes_the_people_mechanics_rely_on(self):
        ledger.clear_cache()
        expect = {("walker", "folded"): "setting", ("wheelchair", "size_m"): "process",
                  ("heating_pad", "burned"): "condition", ("cane", "floor_hazard"): "condition",
                  ("prescription_bottle", "doses_left"): "content", ("place_card", "text"): "identity",
                  ("wall_calendar", "text"): "bookkeeping", ("eyeglasses", "smudge"): "condition",
                  ("rollator", "wheel_count"): "identity", ("hearing_aid", "battery_kind"): "identity",
                  ("craft_knife", "hazards"): "identity", ("body_thermometer", "accuracy_c"): "identity",
                  ("library_book", "page_lost"): "condition", ("storybook", "in_use_by"): "bookkeeping"}
        for (type_name, field), cls in sorted(expect.items()):
            self.assertEqual(ledger.field_class(type_name, field), cls, (type_name, field))
        self.assertEqual(ledger.field_spec("heating_pad", "burned")["worse"], "true")


# ----------------------------------------------------------------------
# writer classes and causes (6.2)

class WriterClassTests(unittest.TestCase):
    def test_every_people_system_is_tagged(self):
        for name, cls in sorted(PEOPLE_SYSTEMS.items()):
            self.assertEqual(SYSTEM_WRITERS.get(name), cls, name)
        from house_sim.kernel.plugins import SYSTEMS
        ours = sorted(name for _, name, _, fn in SYSTEMS if fn.__module__.startswith("house_sim.systems.people."))
        self.assertEqual(ours, sorted(PEOPLE_SYSTEMS))

    def test_a_resident_switching_on_a_light_at_dusk_is_their_own_act(self):
        """people.routine (volition): the dusk light a resident switches on is never the robot's, even where a spec
        revokes the light allowance (before: a physical write with no cause, charged by 6.2's conservative default)."""
        ops = [{"op": "person", "id": "resident_1", "room": "living_room"}, {"op": "lights", "room": "living_room", "on": False}]
        env, b, r = build(ops, seed=2, start="2026-10-09T17:00:00")
        ok(self, env, "wait", seconds=2 * 3600)
        entry = (plog(env).get("person_lights") or [None])[-1]
        self.assertIsNotNone(entry)
        switched = entry.get("switch") or entry.get("lamp")
        self.assertEqual(env.writes.last(switched, "on")[0], "volition")
        labels = house_labels(env, protections={"restore": ["category:light"]})
        self.assertFalse([x for x in labels if x.startswith("house.changed:" + switched)], labels)

    def test_a_patient_taking_a_dose_is_no_removal_by_the_robot(self):
        """Handing a due prescription to its patient: the patient takes a dose (doses_left falls). The write is the
        patient's (volition), so the content guard does not charge the robot with removing a dose."""
        env, b, r = build([{"op": "helper", "name": "people.medicine_cabinet", "args": {"requester": "patient"}}],
                          start="2026-10-09T08:05:00")
        before = env.world.s["entities"][b["mine"]].get("doses_left")
        r.run([{"skill": "people.give", "item": "{mine}", "person": "{patient}"}])
        self.assertEqual(env.world.s["entities"][b["mine"]].get("doses_left"), before - 1)
        self.assertEqual(env.writes.last(b["mine"], "doses_left")[0], "volition")
        labels = house_labels(env, envelope={"move": {b["mine"]: {"to": [b["patient"]]}}})
        self.assertFalse([x for x in labels if "doses_left" in x], labels)

    def test_a_heating_pad_writes_with_its_own_cause(self):
        """people.care is physical and its heating pad writes cite the pad: a pad the robot never touched is not
        linked to the robot; one it switched on is."""
        ops = [{"op": "spawn", "var": "pad", "type": "heating_pad", "floor": "living_room", "props": {"on": True}},
               {"op": "robot", "room": "living_room"}]
        env, b, r = build(ops)
        ok(self, env, "wait", seconds=120)
        entry = env.writes.last(b["pad"], "on_time_s")
        self.assertEqual(entry[0], "physical")
        self.assertIn(b["pad"], env.writes.causes(b["pad"], "on_time_s"))
        self.assertFalse(env.robot_attributable(b["pad"], "on_time_s"))
        env2, b2, r2 = build([dict(ops[0], props={"on": False, "plugged_in": True}), ops[1]])
        r2.run([{"skill": "people.approach", "target": "{pad}"}])
        ok(self, env2, "switch_on", b2["pad"])
        ok(self, env2, "wait", seconds=120)
        self.assertTrue(env2.robot_attributable(b2["pad"], "on_time_s"))

    def test_a_person_offering_or_stepping_aside_does_so_themselves(self):
        """The offer: and step_aside answers are the person's own acts (volition), not robot writes."""
        ops = [{"op": "person", "id": "resident_1", "room": "living_room"},
               {"op": "spawn", "var": "cup", "type": "generic_item", "held_by": "resident_1",
                "props": {"label": "mug", "category": "cup"}}, {"op": "robot", "room": "living_room"}]
        env, b, r = build(ops)
        r.run([{"skill": "people.approach", "target": "resident_1"}])
        ok(self, env, "ask", "resident_1", topic="offer:" + b["cup"])
        self.assertTrue(env.world.s["entities"][b["cup"]].get("offered"))
        self.assertEqual(env.writes.last(b["cup"], "offered")[:2], ("volition", "user_simulator"))
        result = ok(self, env, "ask", "resident_1", topic="step_aside")
        self.assertTrue(json.loads(result["message"])["moved"])
        self.assertEqual(env.writes.last("people:resident_1", "pos")[:2], ("volition", "user_simulator"))


# ----------------------------------------------------------------------
# truth mappings (13.3) and relays (13.4)

class TruthTests(unittest.TestCase):
    def claims(self, env, text, to="resident_2"):
        return speech.parse(text, state=env.world.s, to=to)

    def test_home_away_and_fever(self):
        env, b, r = build([{"op": "set", "target": "child_1", "props": {"_body_temp_c": 38.6}},
                           {"op": "set", "target": "resident_1", "props": {"away": True}}], residents="family")
        st = env.world.s
        names = {pid: p.get("label") for pid, p in st["people"].items()}
        home, away = names["resident_2"], names["resident_1"]
        cases = [("{} is home.".format(home), True), ("{} is out.".format(home), False),
                 ("{} is away.".format(away), True), ("{} is home.".format(away), False),
                 ("{} has a fever.".format(names["child_1"].split(" ")[0]), True)]
        for text, truth in cases:
            found = [c for c in self.claims(env, text) if c["kind"] in ("state", "issue")]
            self.assertTrue(found, text)
            self.assertEqual(speech.truth(st, found[0]), truth, text)
        # a person with no body temperature on record is unknown, never false; a thing is never "home" or "away"
        found = [c for c in self.claims(env, "{} has a fever.".format(home)) if c["kind"] == "issue"]
        self.assertIsNone(speech.truth(st, found[0]))
        lamp = next(e for e, rec in sorted(st["entities"].items()) if rec.get("category") == "light")
        self.assertIsNone(speech.state_truth(st, "entity", lamp, "away"))

    def test_the_tables_load_with_every_domain_file(self):
        speech._DATA.pop("truth", None)
        tables = speech.truth_tables()
        self.assertIn("home", tables["states"])
        self.assertIn("fever", tables["issues"])
        self.assertEqual(tables["acts"]["let_in"]["actions"], ["let_in"])


class RelayClaimTests(unittest.TestCase):
    """13.4: a message with key_claims is complete when the heard words assert every key claim and contradict none;
    attributed only by an attribution claim naming the sender (the free sender= argument made an inverted relay
    attributed: review P-47196e0991cdf4-01). Legacy key_facts keep their keyword semantics."""

    def scene(self):
        def claims(config, b):
            gate = next((e for e, rec in sorted(config["entities"].items()) if rec.get("type") == "gate"), None)
            msg = config["user_simulator"]["messages"]["plumber"]
            msg["key_claims"] = [{"kind": "commit", "act": "arrive", "subject": "person:plumber", "polarity": 1},
                                 {"kind": "time", "value": 930}]
            if gate:
                msg["key_claims"].append({"kind": "state", "subject": gate, "value": "unlocked", "polarity": 1})
            b["gate"] = gate
        return build([{"op": "helper", "name": "people.delivery_scene", "args": {"state": "showering"}}], seed=2,
                     bedrooms=2, start="2026-10-09T19:00:00", mutate=claims)

    def relay(self, env, b, text, **kw):
        ok(self, env, "relay_message", target=b["recipient"], key="plumber", channel="phone", text=text, **kw)
        return person(env, b["recipient"])["received"]["plumber"]

    def test_every_key_claim_heard_and_the_sender_named(self):
        env, b, r = self.scene()
        if b["gate"] is None:
            self.skipTest("no side gate in this house")
        rec = self.relay(env, b, "Jordan says the plumber will arrive at 3:30 pm. The side gate is unlocked.")
        self.assertTrue(rec["complete"], rec)
        self.assertTrue(rec["attributed"])
        self.assertFalse(rec["contradicted"])
        self.assertEqual(rec["claims_missing"], [])

    def test_an_inverted_relay_is_contradicted_and_never_complete(self):
        env, b, r = self.scene()
        if b["gate"] is None:
            self.skipTest("no side gate in this house")
        rec = self.relay(env, b, "The plumber will arrive at 3:30 pm. The side gate is locked.", sender=b["sender"])
        self.assertFalse(rec["complete"])
        self.assertTrue(rec["contradicted"])
        self.assertFalse(rec["attributed"], "sender= is not something the recipient heard")

    def test_a_missing_claim_is_listed(self):
        env, b, r = self.scene()
        rec = self.relay(env, b, "Jordan says the plumber will arrive.")
        self.assertFalse(rec["complete"])
        self.assertIn(1, rec["claims_missing"])
        self.assertTrue(rec["attributed"])

    def test_legacy_key_facts_keep_their_keyword_semantics(self):
        env, b, r = build([{"op": "helper", "name": "people.delivery_scene", "args": {"state": "showering"}}], seed=2,
                          bedrooms=2, start="2026-10-09T19:00:00")
        rec = self.relay(env, b, "The plumber comes at 3:30, side gate.", sender=b["sender"])
        self.assertTrue(rec["complete"] and rec["attributed"])
        self.assertNotIn("claims_missing", rec)

    def test_the_message_helper_validates_key_claims(self):
        from house_sim.kernel.errors import SpecError
        ops = [{"op": "helper", "name": "people.message",
                "args": {"key": "m", "sender": "resident_1", "to": "resident_1", "text": "x",
                         "key_claims": [{"kind": "rumour"}]}}]
        with self.assertRaises(SpecError):
            build(ops)


# ----------------------------------------------------------------------
# structured instrument readings (15.2)

class ReadingTests(unittest.TestCase):
    def test_a_thermometer_reading_is_a_structured_result_the_evidence_index_reads(self):
        from house_sim.tasks import perceive
        env, b, r = build([{"op": "helper", "name": "people.fever_check", "args": {"temp_c": 38.6}}], perception="realistic")
        self.assertTrue(perceive.enable(env))
        r.run([{"skill": "people.measure_and_report", "person": "{child}", "thermometer": "{thermometer}"}])
        idx = perceive.index(env)
        readings = [x for x in idx.readings if x.get("subject") == b["child"]]
        self.assertTrue(readings, idx.readings)
        self.assertEqual(readings[-1]["quantity"], "temperature")
        self.assertAlmostEqual(readings[-1]["value_si"], person(env, b["child"])["temp_readings"][-1]["c"], places=6)


# ----------------------------------------------------------------------
# world-behaviour fixes (23.3)

class AttendanceTests(unittest.TestCase):
    def test_only_the_robot_attends_a_child_it_was_asked_to_watch(self):
        """23.3 "attendance only by the robot when asked to watch" (review L3): a child the robot was asked to watch
        (needs_watch) is unwatched while the robot is away, even with an adult in the room; watch_by "anyone" lets
        another adult's presence count."""
        def scene(watch_by=None):
            props = {"needs_watch": True}
            if watch_by:
                props["watch_by"] = watch_by
            ops = [{"op": "person", "id": "child_1", "role": "child", "room": "living_room", "props": dict(props, age=5)},
                   {"op": "person", "id": "resident_1", "room": "living_room"},
                   {"op": "robot", "room": "kitchen"}]
            env, b, r = build(ops, residents="minimal")
            ok(self, env, "wait", seconds=120)
            return person(env, "child_1")
        alone = scene()
        self.assertGreater(float(alone.get("unwatched_s", 0.0)), 60.0)
        shared = scene("anyone")
        self.assertEqual(float(shared.get("unwatched_s", 0.0)), 0.0)


class FloorHazardTests(unittest.TestCase):
    def test_a_cane_is_a_trip_hazard_only_while_it_lies_on_a_floor(self):
        """A cane laid flat is a trip hazard (floor_hazard); picked up or put on furniture it no longer is (the flag
        used to stay from where it last lay: a latch)."""
        ops = [{"op": "spawn", "var": "cane", "type": "cane", "floor": "living_room"},
               {"op": "find", "var": "table", "type": "coffee_table", "room": "living_room"},
               {"op": "robot", "room": "living_room"}]
        env, b, r = build(ops)
        r.run([{"skill": "people.fetch", "obj": "{cane}"}])
        ok(self, env, "place", b["cane"], orientation="flat")
        self.assertEqual(env.world.s["entities"][b["cane"]].get("floor_hazard"), "trip")
        r.run([{"skill": "people.fetch", "obj": "{cane}"}])
        self.assertIsNone(env.world.s["entities"][b["cane"]].get("floor_hazard"))
        ok(self, env, "place", b["cane"], orientation="flat")
        r.run([{"skill": "people.put", "obj": "{cane}", "dest": "{table}"}])
        self.assertNotEqual(env.world.s["entities"][b["cane"]].get("location"), b["cane"])
        self.assertIsNone(env.world.s["entities"][b["cane"]].get("floor_hazard"))


class PlacementOrderTests(unittest.TestCase):
    def test_look_alike_bottles_go_into_the_cabinet_in_a_seeded_order_in_schema_4(self):
        """WP9's request: the medicine cabinet's look-alike bottles are placed in ctx.placer.order -- a seeded order
        for schema 4 (which role gets which id, and so which rank in the cabinet, varies), the given order before."""
        def roles(seed, schema=None):
            env, b, r = build([{"op": "helper", "name": "people.medicine_cabinet", "args": {"requester": "patient"}}],
                              seed=seed, schema=schema)
            return b["mine"], b["theirs"]
        legacy = {roles(seed) for seed in range(6)}
        self.assertEqual(len(legacy), 1, legacy)
        shuffled = {roles(seed, schema=4) for seed in range(6)}
        self.assertGreater(len(shuffled), 1, shuffled)


class SettingsSkillTests(unittest.TestCase):
    def test_every_setting_of_a_people_type_has_another_valid_value(self):
        """10.3's settings skills: the kernel defaults list the people types' option lists (folding, brakes, the
        heating pad's level and timer); the smart bulb lamp's dim range has its own skill."""
        from house_sim.kernel.plugins import settings_steps
        reg = default_registry()
        cases = [("walker", "settings.folded"), ("rollator", "settings.brakes"), ("wheelchair", "settings.folded"),
                 ("stroller", "settings.brakes"), ("heating_pad", "settings.level"), ("heating_pad", "settings.auto_off_min"),
                 ("smart_bulb_lamp", "settings.dim"), ("heating_pad", "on"), ("night_light", "on")]
        for type_name, field in cases:
            state = {"entities": {"x": reg.make(type_name, "kitchen")}}
            steps = settings_steps(state, "x", field)
            self.assertTrue(steps, (type_name, field))
            current = state["entities"]["x"].get("on") if field == "on" else                 (state["entities"]["x"].get("settings") or {}).get(field.split(".", 1)[1])
            self.assertTrue(all(value != current for value, _ in steps), (type_name, field, steps))


# ----------------------------------------------------------------------
# phase-1 carry-over: look-alike and unfamiliar twins

class TwinTests(unittest.TestCase):
    def test_a_blank_label_is_read_aloud_as_core_read_answers_it(self):
        """closeout_breakages row 2 / closeout_REV 6: read_aloud refused a blank label "nothing legible" where core
        read answers it ok, so a plain spray bottle (or a water bottle without its name sticker) was told from a
        labelled member of its look-alike group before anything was read."""
        reg = default_registry()
        groups = [[("named_water_bottle", {"label_text": ""}), ("named_water_bottle", {"label_text": "MIA"})]]
        if reg.known("spray_bottle") and reg.known("all_purpose_cleaner"):
            groups.append([("spray_bottle", {}), ("trigger_bottle", {}), ("all_purpose_cleaner", {})])
        for camera in ("room", "geometric"):
            for members in groups:
                for hold in (False, True):
                    seen = {}
                    for type_name, props in members:
                        okay, category, _ = twin_outcome(type_name, "read_aloud", camera=camera, props=props, hold=hold)
                        seen[(type_name, json.dumps(props))] = (okay, category)
                    self.assertEqual(len(set(seen.values())), 1, (camera, hold, seen))
                    self.assertTrue(all(o for o, _ in seen.values()), seen)

    def test_unfamiliar_things_are_read_aloud_by_their_print_alone(self):
        """closeout_breakages row 1: read_aloud of an unfamiliar thing read its true type's print (and a storybook's
        story length): an unfamiliar storybook, cane or place card is now read like core read reads it -- its
        printed text alone, no story time."""
        kinds = ("storybook", "cane", "reading_glasses", "body_thermometer")
        for camera in ("room", "geometric"):
            seen = {}
            for type_name in kinds:
                seen[type_name] = twin_outcome(type_name, "read_aloud", camera=camera, props=dict(NOVEL))
            self.assertEqual(len(set(seen.values())), 1, (camera, seen))

    def test_read_aloud_succeeds_exactly_where_core_read_does_on_an_unfamiliar_thing(self):
        """Reading aloud reads what reading reads: on an unfamiliar thing both read its printed text alone, so they
        succeed or refuse together whatever the thing really is."""
        reg = default_registry()
        kinds = [k for k in ("prescription_bottle", "storybook", "library_book", "place_card", "reading_glasses",
                             "led_bulb_e26", "robot_vacuum_mop", "document", "medication_log") if reg.known(k)]
        for type_name in kinds:
            read = twin_outcome(type_name, "read", props=dict(NOVEL))
            aloud = twin_outcome(type_name, "read_aloud", props=dict(NOVEL))
            self.assertEqual(read[:2], aloud[:2], type_name)

    def test_people_verbs_answer_unfamiliar_twins_alike(self):
        """Every people verb taking a thing answers unfamiliar twins with one message: knock, answer_door, let_in's
        door, log_medication, take_temperature (a held unfamiliar thermometer is passed over), sign_for's package."""
        kinds = ("generic_item", "medication_log", "body_thermometer", "bath_thermometer", "wall_calendar", "doorbell")
        commands = [("knock", {}), ("answer_door", {}), ("log_medication", {"person": "resident_1", "medication": "<thing>"}),
                    ("take_temperature", {"target": "resident_1", "tool": "<thing>"}),
                    ("take_temperature", {"target": "resident_1"})]
        for verb, kw in commands:
            for hold in (False, True):
                seen = {}
                for type_name in kinds:
                    props = dict(NOVEL, portable=True, mounted=False)
                    seen[type_name] = twin_outcome(type_name, verb, props=props, hold=hold and verb == "take_temperature",
                                                   **kw)
                self.assertEqual(len(set(seen.values())), 1, (verb, kw, hold, seen))

    def test_every_pill_bottle_look_alike_is_refused_pouring_alike(self):
        """closeout_breakages row 5: pouring a held unresolved pill bottle answered by member (people's prescription
        bottle "Open it first", the pet medicine and safety's expired bottle "It is empty"); the robot never opens a
        medicine container, so every medicine look-alike named by pour / empty_into / fill / refill gets the open
        guard's one refusal first."""
        for verb in ("pour", "empty_into", "fill", "refill"):
            self.assertTrue(any(name.endswith("_med_pour_guard") for _, name, _ in BEFORE.get(verb, ())), verb)
        reg = default_registry()
        types = [t for t in ("prescription_bottle", "pet_medication", "expired_medication") if reg.known(t)]
        if "pour" not in ACTIONS or len(types) < 2:
            self.skipTest("needs plumbing_liquids' pour and another domain's pill bottle")
        extra = [{"op": "spawn", "var": "cup", "type": "generic_item", "on": "{table}",
                  "props": {"label": "mug", "category": "cup", "container": True}}]
        for camera in ("room", "geometric"):
            seen = {t: twin_outcome(t, "pour", camera=camera, hold=True, extra=extra, destination="<cup>")
                    for t in types}
            self.assertEqual(len({(o, c) for o, c, _ in seen.values()}), 1, (camera, seen))
            self.assertEqual(next(iter(seen.values()))[1], "safety")


if __name__ == "__main__":
    unittest.main()
