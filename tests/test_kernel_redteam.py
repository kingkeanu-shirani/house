"""Regression tests for the phase-1 red team's kernel-side findings (catalog/analysis/phase1/red_leaks.json and
red_defects.json; fixes in catalog/analysis/phase1/FIX2.json). Each test fails on the tree before the fix.

  ECHO      free text naming an unresolved look-alike or unfamiliar thing echoes the same words whatever it is (the
            kept "part names" of the target come from what the robot knows of it, not from its true record)
  ORDER     obs.grid.hazards lists the sources of one cell by handle, not in spawn order (review I11)
  PUDDLE    an unidentified puddle shows one hazard kind and route probing does not tell its liquid (review L4)
  AVERAGE   the glance size estimate has one fixed error; worse conditions only coarsen it (review L8)
  GONE      a thing destroyed out of view is reported and asked about like one out of view (red_leaks 6)
  NOVEL     unfamiliar things show a fixed key set and are not operated (review I4)
  SPREAD    the registry lint flags look-alike members of different true size behind a group size_m (L2 residual)

Scenes with domain types skip when HOUSE_SIM_DOMAINS leaves the domain out; the rest use core types.
"""
import copy
import json
import os
import tempfile
import unittest
import warnings

from house_sim.kernel import registry as registry_module
from house_sim.kernel.actions import NOVEL_OPERATE_MESSAGE
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.registry import default_registry

COLS, ROWS = 12, 10


def config(entities=None, pos=(1, 1), people=None, robot=None, rooms=None):
    """A lit 6 x 5 m kitchen ("lab") with a lit hallway next door."""
    ents = {"light_1": {"type": "ceiling_light", "location": "lab", "pos": [COLS - 1, 0], "on": True, "mounted": True,
                        "mount_height_m": 2.4},
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [1, 1], "on": True, "mounted": True,
                        "mount_height_m": 2.4}}
    ents.update(copy.deepcopy(entities or {}))
    rob = {"room": "lab", "pos": list(pos)}
    rob.update(copy.deepcopy(robot or {}))
    out = {"schema_version": 2, "start_room": "lab",
           "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "kitchen", "exits": ["hall"]},
                     "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
           "entities": ents, "people": copy.deepcopy(people or {}), "pets": {}, "robot": rob}
    if rooms:
        out["rooms"].update(copy.deepcopy(rooms))
    return out


def env_of(entities=None, perception="realistic", seed=0, camera="room", **kw):
    return HouseEnv(config(entities, **kw), task=None, seed=seed, perception=perception, camera=camera)


def handle_of(env, eid):
    obs = env.observe()
    handle = env.realism.handles.by_id.get(eid)
    return handle if handle in (obs.get("visible_entities") or {}) else None


def known_types(*names):
    reg = default_registry()
    return all(reg.known(n) for n in names)


class FreeTextEchoTests(unittest.TestCase):
    """ECHO (red_leaks finding 1): inbound kept an id-like word of free text exactly when the command's target's TRUE
    record held it (its type, liquid, product, any key or short string value) and turned every other id-like word
    into "something"; the kept true type was then rewritten to the group label. Listing every member's type name in
    apply_label's text told which member an unresolved jug was, in one step and without a read."""

    JUGS = ("chlorine_bleach_bottle", "liquid_detergent", "fabric_softener")

    def echo(self, entity, text):
        env = env_of({"thing_1": entity})
        handle = handle_of(env, "thing_1")
        self.assertIsNotNone(handle)
        self.assertTrue(env.realism.unresolved("thing_1"))
        result = env.step("apply_label", handle, text=text)
        self.assertTrue(env.realism.unresolved("thing_1"))
        return result["ok"], result["message"].replace(handle, "<h>")

    def test_member_type_names_echo_alike_for_every_member(self):
        if not known_types(*self.JUGS):
            self.skipTest("the laundry jugs are the cleaning domain's")
        text = " ".join(self.JUGS)
        echoes = {t: self.echo({"type": t, "location": "lab", "pos": [2, 1]}, text) for t in self.JUGS}
        self.assertEqual(len(set(echoes.values())), 1, echoes)
        self.assertTrue(all(ok for ok, _ in echoes.values()), echoes)

    def test_a_hidden_string_value_of_the_target_is_not_kept(self):
        """Core scene: two unresolved members of one look-alike group that differ only in a hidden liquid name."""
        text = "ammonia_mix chlorine_bleach"
        echoes = []
        for liquid in ("ammonia_mix", "chlorine_bleach"):
            entity = {"type": "generic_item", "location": "lab", "pos": [2, 1], "label": "spray bottle",
                      "looks_like": "spray_bottle", "size_m": [0.1, 0.07, 0.25], "liquid": liquid}
            echoes.append(self.echo(entity, text))
        self.assertEqual(echoes[0], echoes[1])
        self.assertNotIn("chlorine_bleach", echoes[1][1])

    def test_unfamiliar_things_echo_alike(self):
        novel = ("electric_kettle", "hair_dryer", "remote_control")
        if not known_types(*novel):
            self.skipTest("needs the appliance types")
        text = " ".join(novel)
        echoes = {t: self.echo({"type": t, "location": "lab", "pos": [2, 1], "novel": True}, text) for t in novel}
        self.assertEqual(len(set(echoes.values())), 1, echoes)

    def test_a_slot_of_an_unresolved_look_alike_is_kept_outside_free_text(self):
        """A part named in a non-text parameter (uninstall's slot "cell_1" of a remote the robot cannot tell from the
        other remotes) still reaches the action -- the action acts on it anyway (T-fd2d3d767911d8-01 failed under
        forced realistic perception while its slot became "something") -- but free text does not keep it."""
        env = env_of({"remote_1": {"type": "generic_item", "location": "lab", "pos": [2, 1], "label": "remote",
                                   "looks_like": "remote", "size_m": [0.05, 0.02, 0.2],
                                   "slots": {"cell_1": {"item": None}}}})
        handle = handle_of(env, "remote_1")
        self.assertTrue(env.realism.unresolved("remote_1"))
        out = env.realism.inbound({"target": handle, "slot": "cell_1"}, entity_keys=("target",))
        self.assertEqual(out["slot"], "cell_1")
        out = env.realism.inbound({"target": handle, "text": "cell_1"}, text_keys=("text",), entity_keys=("target",))
        self.assertEqual(out["text"], "something")

    def test_an_identified_target_keeps_its_own_part_names(self):
        """The rule's purpose stands: a slot or key of an identified target typed in a command is not neutralised."""
        env = env_of({"box_1": {"type": "generic_item", "location": "lab", "pos": [2, 1], "label": "box",
                                "size_m": [0.3, 0.3, 0.3], "slots": {"layer_2": {}}}})
        names = env.realism._target_part_names(env.world.s, "box_1")
        self.assertIn("layer_2", names)
        self.assertIn("generic_item", names)


def floor_thing(cell, label="spray bottle", **props):
    out = {"type": "generic_item", "location": "lab", "pos": list(cell), "label": label, "size_m": [0.1, 0.07, 0.25],
           "portable": True}
    out.update(props)
    return out


class HazardListingTests(unittest.TestCase):
    """ORDER (review I11) and PUDDLE (review L4): obs.grid.hazards listed the sources of one cell in spawn order, and an
    unidentified puddle's hazard kind named its liquid (chlorine bleach 'wet', detergent 'slip')."""

    def hazard_sources(self, order):
        ents = {}
        for eid in order:
            ents[eid] = floor_thing((3, 4), looks_like="spray_bottle")
        env = env_of(ents)
        obs = env.observe()
        back = {env.realism.handles.by_id.get(e): e for e in order}
        return [back.get(h["source"]) for h in obs["grid"]["hazards"] if h.get("cell") == [3, 4]]

    def test_one_cell_lists_its_sources_in_the_same_order_whatever_the_spawn_order(self):
        first = self.hazard_sources(("a_jug", "b_jug"))
        second = self.hazard_sources(("b_jug", "a_jug"))
        self.assertEqual(sorted(first), ["a_jug", "b_jug"])
        self.assertEqual(first, second)

    def puddle_env(self, kind):
        ents = {"puddle_1": floor_thing((3, 4), label="puddle", looks_like="sock", portable=False,
                                        ground_layer=True, floor_hazard=kind, size_m=[0.4, 0.4, 0.002])}
        return env_of(ents)

    def test_an_unidentified_puddle_shows_one_kind_whatever_its_liquid(self):
        shown = {}
        for kind in ("wet", "slip", "sticky"):
            env = self.puddle_env(kind)
            self.assertTrue(env.realism.unresolved("puddle_1"))
            shown[kind] = sorted(h["kind"] for h in env.observe()["grid"]["hazards"] if h.get("cell") == [3, 4])
        self.assertEqual(shown, {"wet": ["wet"], "slip": ["wet"], "sticky": ["wet"]})

    def test_route_probing_of_an_unidentified_puddle_does_not_tell_its_liquid(self):
        for avoid in (["slip"], ["wet"], ["sticky"]):
            cells = {}
            for kind in ("wet", "slip"):
                env = self.puddle_env(kind)
                env.observe()
                cells[kind] = env.realism.route_hazards(env.world.s, "lab", avoid)[1]
            self.assertEqual(cells["wet"], cells["slip"], avoid)
            self.assertIn((3, 4), cells["wet"], avoid)
        env = self.puddle_env("slip")
        env.observe()
        self.assertNotIn((3, 4), env.realism.route_hazards(env.world.s, "lab", ["trip"])[1])

    def test_an_identified_puddle_keeps_its_own_kind(self):
        env = self.puddle_env("slip")
        env.realism.knowledge.grant("puddle_1", env.world.s["entities"]["puddle_1"], 99)
        self.assertFalse(env.realism.unresolved("puddle_1"))
        self.assertEqual([h["kind"] for h in env.observe()["grid"]["hazards"] if h.get("cell") == [3, 4]], ["slip"])


class SizeEstimateTests(unittest.TestCase):
    """AVERAGE (review L8): the glance size estimate's error grew linearly with the viewing-condition term m, which
    the agent controls (distance, speed, light), so a regression over a few conditions recovered the true size. Now one
    fixed relative error per thing; worse conditions only read it more coarsely."""

    def test_estimates_in_worse_conditions_stay_within_the_coarser_step_of_one_value(self):
        ents = {"item_{}".format(i): floor_thing((2 + i, 6), label="cabinet", size_m=[0.43, 0.37, 1.73],
                                                  portable=False) for i in range(6)}
        env = env_of(ents)
        realism = env.realism
        for eid, record in sorted(env.world.s["entities"].items()):
            if not eid.startswith("item_"):
                continue
            base = realism.render(eid, record, "size_m", record["size_m"], 0, {"m": 1.0, "light": 1.0, "dist": 1})
            for m in (1.5, 2.0, 3.0):
                est = realism.render(eid, record, "size_m", record["size_m"], 0, {"m": m, "light": 1.0, "dist": 1})
                step = 0.05 * max(1, int(round(m)))
                for a, b in zip(base, est):
                    self.assertLessEqual(abs(a - b), step / 2.0 + 0.025 + 1e-9, (eid, m, base, est))
                    self.assertAlmostEqual(b * 20.0, round(b * 20.0), places=6)     # on the 5 cm grid


class RememberedSubjectTests(unittest.TestCase):
    """GONE (red_leaks finding 6): a typed report about a thing destroyed out of view was refused ("You have not seen
    that") while one merely out of view was accepted, and ask about it answered "I don't know." instead of the answer an
    out-of-view thing gets -- an existence oracle. Both now resolve a thing the robot saw like one out of view."""

    def run_case(self, perception):
        people = {"ana": {"label": "Ana", "role": "resident", "location": "hall", "pos": [0, 0], "posture": "standing",
                          "has_phone": True, "age": 40}}
        ents = {"apple_a": floor_thing((3, 3), label="apple", category="food", size_m=[0.08, 0.08, 0.08]),
                "apple_b": floor_thing((4, 3), label="apple", category="food", size_m=[0.08, 0.08, 0.08])}
        env = env_of(ents, perception=perception, people=people)
        env.observe()
        if perception == "realistic":
            names = {e: env.realism.handles.by_id[e] for e in ents}
        else:
            names = {e: e for e in ents}
        self.assertTrue(env.step("move", "hall")["ok"])
        w = env.world
        w.begin()
        w.s["entities"].pop("apple_b")          # off screen: someone ate it
        w.commit()
        out = {}
        for eid, name in sorted(names.items()):
            rows = []
            for action, kw in (("report", dict(kind="finding", subject=name, issue="missing")),
                               ("report", dict(kind="finding", subject=name, issue="frobnicated")),
                               ("ask", dict(target="ana", about=name, aspect="where", channel="text")),
                               ("ask", dict(target="ana", about=name, aspect="owner", channel="text"))):
                # aspect owner: people's answerer said "Which one?" for a destroyed thing (fixed in the phase-1
                # finish: people knowledge._known_record answers from the record the household knew)
                result = env.step(action, **kw)
                rows.append((result["ok"], result["category"], str(result["message"]).replace(name, "<subject>")))
            for aspect in self.MORE_ASPECTS:
                result = env.step("ask", target="ana", about=name, aspect=aspect, channel="text")
                rows.append((result["ok"], result["category"], str(result["message"]).replace(name, "<subject>")))
            out[eid] = rows
        return out

    # what is to happen to a thing (manipulation's answerer, manip:<key>:<entity>): "I'm not sure what you mean." for
    # a destroyed thing where one out of view got its answer, until the phase-1 close-out (manipulation
    # world._known_record; finish_breakages.json asked for these aspects here once fixed). Whatever answers them (no
    # domain at all with HOUSE_SIM_DOMAINS=core), both things get the same answer
    MORE_ASPECTS = ("keep", "intended", "how", "status")

    def test_a_destroyed_thing_is_reported_and_asked_about_like_one_out_of_view(self):
        first = 4       # the report and ask rows above MORE_ASPECTS succeed for the thing out of view
        for perception in ("realistic", "oracle"):
            out = self.run_case(perception)
            self.assertTrue(all(ok for ok, _, _ in out["apple_a"][:first]), out["apple_a"])
            self.assertEqual(len(out["apple_a"]), first + len(self.MORE_ASPECTS))
            self.assertEqual(out["apple_a"], out["apple_b"], perception)

    def test_a_thing_never_seen_is_still_refused(self):
        env = env_of({"apple_a": floor_thing((3, 3), label="apple")}, perception="oracle")
        result = env.step("report", kind="finding", subject="apple_zz", issue="missing")
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "You have not seen that")


class UnfamiliarThingTests(unittest.TestCase):
    """NOVEL (review I4): unfamiliar things were told apart by the glance (`openable`, a jug's fill), by inspect extras
    and by the outcome of operating them (switch_on: "That does not work on it" / "No power" / "It has no supported
    switch"; open: "Opened" / "It does not open"). A fixed key set now, and operating one is refused alike."""

    VARIANTS = {
        "lidded": {"openable": True, "open": False, "container": True, "accepts_items": True, "liquid_l": 1.0,
                   "capacity_l": 1.7},
        "switched": {"switchable": True, "on": False, "plug_required": True},
        "plain": {},
        # read-tier fields of its true type (phase-1 finish, food's request: an unfamiliar flour bag read out its
        # allergens and net weight, a knife "nothing legible")
        "printed": {"expiration": "2027-01-15", "care_label": "machine wash cold", "instructions": "use within 3 days"},
    }

    def env_for(self, props):
        entity = floor_thing((2, 1), label="thing", size_m=[0.2, 0.15, 0.25], novel=True, material="plastic",
                             **props)
        return env_of({"thing_1": entity})

    def test_glance_inspect_and_operating_answer_alike(self):
        seen = {}
        for name, props in self.VARIANTS.items():
            env = self.env_for(props)
            handle = handle_of(env, "thing_1")
            self.assertTrue(env.realism.novel_unidentified("thing_1"))
            glance = sorted(env.observe()["visible_entities"][handle])
            inspect = env.step("inspect", handle)
            outcomes = []
            for action in ("open", "close", "switch_on", "switch_off", "set_mode", "start", "read"):
                result = env.step(action, handle)
                outcomes.append((result["ok"], result["category"], result["message"]))
            seen[name] = (glance, sorted(json.loads(inspect["message"])), outcomes)
        self.assertEqual(seen["lidded"], seen["switched"])
        self.assertEqual(seen["lidded"], seen["plain"])
        self.assertEqual(seen["lidded"], seen["printed"])
        self.assertNotIn("openable", seen["lidded"][0])
        self.assertFalse(any(ok for ok, _, _ in seen["lidded"][2]))

    def test_an_unfamiliar_thing_is_still_moved_and_inspected(self):
        env = self.env_for(self.VARIANTS["lidded"])
        handle = handle_of(env, "thing_1")
        self.assertTrue(env.step("inspect", handle)["ok"])
        self.assertTrue(env.step("approach", handle)["ok"])
        result = env.step("pick_up", handle)
        self.assertNotEqual(result["message"], NOVEL_OPERATE_MESSAGE)     # (here: crouch first) -- not refused as novel

    def test_oracle_mode_operates_it(self):
        entity = floor_thing((2, 1), label="thing", novel=True, openable=True, open=False)
        env = env_of({"thing_1": entity}, perception="oracle")
        self.assertTrue(env.step("open", "thing_1")["ok"])


TABLE = {"type": "table", "location": "lab", "pos": [2, 1], "accepts_items": True, "surface": True}
HELD_CUP = {"type": "generic_item", "location": "robot", "relation": "held", "held_hand": "right", "label": "cup",
            "portable": True, "size_m": [0.08, 0.08, 0.1], "mass_kg": 0.2}


class UnfamiliarCloseOutTests(unittest.TestCase):
    """NOVEL, phase-1 close-out (the domains' kernel requests in catalog/analysis/phase1/closeout_*.json): an unfamiliar
    thing was still told apart by a blank label that read {"label_text": ""} (mechanisms), by its print coming back with
    its true name rewritten (pets), as a destination of core place (manipulation), as a held tool of core require_tool
    (navigation_robot), when a nested leaf operated it (manipulation) and by a wrist look inside it (admin_inventory,
    pets). Each case now answers alike for every true kind; oracle mode is unchanged."""

    LOOK = {"label": "thing", "size_m": [0.2, 0.15, 0.25], "novel": True, "material": "plastic", "color": "grey"}
    DESTINATIONS = {"plain": {}, "bowl": {"container": True, "accepts_items": True, "capacity_l": 2.0},
                    "lidded": {"container": True, "accepts_items": True, "openable": True, "open": False},
                    "tray": {"surface": True, "accepts_items": True}}

    def on_table(self, **props):
        out = {"type": "generic_item", "location": "table_1", "relation": "on", "portable": True}
        out.update(self.LOOK)
        out.update(props)
        return out

    @staticmethod
    def row(result):
        return result["ok"], result["category"], result["message"]

    def test_a_blank_label_reads_like_no_label(self):
        seen = {}
        for name, props in (("none", {}), ("blank_label", {"label_text": ""}), ("blank_text", {"text": "  "}),
                            ("both", {"label_text": "", "text": ""})):
            entity = floor_thing((2, 1), **dict(self.LOOK, **props))
            env = env_of({"thing_1": entity})
            handle = handle_of(env, "thing_1")
            glance = sorted(env.observe()["visible_entities"][handle])
            env.step("approach", handle)
            inspect = sorted(json.loads(env.step("inspect", handle)["message"]))
            seen[name] = (glance, inspect, self.row(env.step("read", handle)))
        for name in seen:
            self.assertEqual(seen[name], seen["none"], name)
        self.assertEqual(seen["none"][2], (False, "perception", "There is nothing legible to read on it"))

    def test_the_print_of_an_unfamiliar_thing_reads_as_printed(self):
        printed = "Fish flakes - floating food for surface fish"
        entity = floor_thing((2, 1), **dict(self.LOOK, label="fish flakes", label_text=printed))
        env = env_of({"thing_1": entity})
        handle = handle_of(env, "thing_1")
        env.step("approach", handle)
        result = env.step("read", handle)
        self.assertTrue(result["ok"], result)
        self.assertEqual(json.loads(result["message"]), {"label_text": printed})
        self.assertTrue(env.realism.novel_unidentified("thing_1"))           # reading it identifies nothing

    def test_an_unfamiliar_destination_is_no_receptacle_or_support(self):
        seen = {}
        for name, props in self.DESTINATIONS.items():
            rows = []
            for relation in (None, "in", "on"):
                env = env_of({"thing_1": self.on_table(**props), "table_1": dict(TABLE), "cup_1": dict(HELD_CUP)},
                             robot={"hands": {"right": "cup_1", "left": None}})
                handle = handle_of(env, "thing_1")
                env.step("approach", handle)
                kw = {"relation": relation} if relation else {}
                rows.append(self.row(env.step("place", env.realism.handles.handle("cup_1"), destination=handle, **kw)))
                self.assertEqual(env.world.s["entities"]["cup_1"]["location"], "robot")
            seen[name] = rows
        for name in seen:
            self.assertEqual(seen[name], [(False, "precondition", NOVEL_OPERATE_MESSAGE)] * 3, name)
        env = env_of({"thing_1": self.on_table(**self.DESTINATIONS["bowl"]), "table_1": dict(TABLE),
                      "cup_1": dict(HELD_CUP)}, perception="oracle", robot={"hands": {"right": "cup_1", "left": None}})
        env.step("approach", "thing_1")
        self.assertTrue(env.step("place", "cup_1", destination="thing_1")["ok"])      # oracle: a bowl holds it

    def test_an_unfamiliar_held_thing_is_no_tool(self):
        import random
        from house_sim.kernel.actions import ACTIONS, Ctx
        from house_sim.kernel.errors import ActionError
        from house_sim.systems.core import require_tool

        def attempt(props, perception, named):
            held = dict(HELD_CUP, **dict(self.LOOK, **props))
            env = env_of({"knife_1": held}, perception=perception, robot={"hands": {"right": "knife_1", "left": None}})
            ctx = Ctx(env, ACTIONS["look"], {}, random.Random(0))
            try:
                return require_tool(ctx, "cut", "knife_1" if named else None, consume=False)
            except ActionError as exc:
                return exc.category, str(exc)

        knife = {"affordances": ["cut"], "tool_kind": "knife"}
        for named, expected in ((True, ("precondition", NOVEL_OPERATE_MESSAGE)),
                                (False, ("capability", "Hold a tool that can cut"))):
            self.assertEqual(attempt(knife, "realistic", named), expected)
            self.assertEqual(attempt({}, "realistic", named), expected)
            self.assertEqual(attempt(knife, "oracle", named), "knife_1")

    def test_a_nested_leaf_operates_no_unfamiliar_thing(self):
        from house_sim.kernel.errors import ActionError
        seen = {}
        for name in ("lidded", "plain"):
            env = env_of({"thing_1": self.on_table(**self.DESTINATIONS[name]), "table_1": dict(TABLE)})
            env.step("approach", handle_of(env, "thing_1"))
            env.world.begin()
            try:
                seen[name] = env.perform({"action": "open", "target": "thing_1"})
            except ActionError as exc:
                seen[name] = (exc.category, str(exc))
            env.world.rollback()
        self.assertEqual(seen["lidded"], ("precondition", NOVEL_OPERATE_MESSAGE))
        self.assertEqual(seen["plain"], seen["lidded"])

    def test_a_look_inside_an_unfamiliar_thing_is_refused_alike(self):
        seen = {}
        for name in ("lidded", "bowl", "plain"):
            env = env_of({"thing_1": self.on_table(**self.DESTINATIONS[name]), "table_1": dict(TABLE)},
                         camera="geometric")
            env.step("look")
            handle = handle_of(env, "thing_1")
            self.assertIsNotNone(handle, name)
            env.step("approach", handle)
            seen[name] = [self.row(env.step(action, handle, where="inside")) for action in ("peek", "inspect")]
        for name in seen:
            self.assertEqual(seen[name], [(False, "precondition", NOVEL_OPERATE_MESSAGE)] * 2, name)


SPREAD_TYPES = {
    "domain": "redteam_spread_test",
    "looks_like": {"rt_plunger": {"descriptor": "rubber plunger", "resolve": "inspect", "size_m": [0.15, 0.15, 0.5],
                                  "members": ["rt_cup_plunger", "rt_flange_plunger"]}},
    "types": {
        "rt_cup_plunger": {"is_a": "portable_object", "props": {"label": "cup plunger", "category": "rt_tool",
                                                                 "size_m": [0.15, 0.15, 0.45], "color": "black"}},
        "rt_flange_plunger": {"is_a": "portable_object", "props": {"label": "flange plunger", "category": "rt_tool",
                                                                    "size_m": [0.15, 0.15, 0.55], "color": "black"}},
    },
}


class LookalikeSizeLintTests(unittest.TestCase):
    """SPREAD (red_leaks L2 residual; manipulation's request): a group size_m fixes what the record shows, not what the
    geometric camera's geometry implies, so the registry lint flags members of different true size even then."""

    def test_true_size_spreads_are_flagged_although_the_group_sets_size_m(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "redteam_spread_test.json")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(SPREAD_TYPES, f)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                reg = registry_module.TypeRegistry(extra_files=[path])
        self.assertTrue(any(s.startswith("rt_plunger: size 0.45-0.55 m") for s in reg.lookalike_spreads),
                        reg.lookalike_spreads)
        self.assertFalse(any(s.startswith("rt_plunger") for s in reg.lookalike_warnings))     # the record is aligned
        self.assertTrue(any(issubclass(w.category, registry_module.LookalikeSizeWarning) for w in caught))


if __name__ == "__main__":
    unittest.main()
