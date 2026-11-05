"""appliances_cooking domain, phase 2 (VERIFY_HARDENING WP-D appliances_cooking): what the house ledger, the content
guards and the battery need from this domain, and the phase-1 carry-over.

* field classes for every property of the domain's types (L-WATCH part 2) and water-sensitive types;
* causes on the physical writes of the domain's systems and appliance-cycle hooks (6.2);
* resource meters: the energy and gas the heaters draw, a dishwasher's water (6.8);
* mess: liquid a vessel lets out is a measured spill, a boil-over soil on the burner, no flow logged (6.5);
* derived fields _load_check, load_state, power_state (8.2) and the Latched fields table L-LATCH reads;
* settings skills (10.3), truth mappings (data/speech/truth_appliances_cooking.json), structured thermometer
  readings (15.2), invariant labels that start with their registered name, seeded dish placement (9.2);
* closeout_breakages row 3: unload refuses an unfamiliar destination before anything else.

Scenes reuse tests/test_appliances_cooking.py's Kit (a small hand-built house), so the file runs with every domain
loaded and with HOUSE_SIM_DOMAINS=core,appliances_cooking.
"""
import copy
import json
import os
import re
import unittest

from house_sim.kernel import ledger, speech
from house_sim.kernel.plugins import DERIVED_FIELDS, HOOKS, INVARIANTS, load_domains, settings_steps

load_domains()

from house_sim.kernel.engine import HouseEnv  # noqa: E402
from house_sim.kernel.predicates import MISSING  # noqa: E402
from house_sim.systems.appliances_cooking import build as B  # noqa: E402
from house_sim.systems.appliances_cooking import hardening as HD  # noqa: E402
from house_sim.systems.appliances_cooking import status as STATUS  # noqa: E402
from tests.test_appliances_cooking import (SAUCEPAN, Kit, add_dishes, dw_kit, ent, load_all, ok, run_cycle,  # noqa: E402
                                           wait)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANDLE = re.compile(r"obj_[0-9a-f]{8,16}")


def burner_of(kit, kind=None):
    return sorted(e for e, r in kit.c["entities"].items() if r.get("ac_kind") == "burner")[0]


def oven_of(kit):
    return sorted(e for e, r in kit.c["entities"].items() if r.get("ac_kind") == "oven")[0]


def meter(env, resource):
    node = env.world.s
    for key in HD.meter_path(resource):
        node = (node or {}).get(key) if isinstance(node, dict) else None
    return float(node or 0.0)


def flows(env):
    return [f for f in ((env.world.s.get("logs") or {}).get("mess_flows") or []) if f.get("op") == "flow"]


def pot_on(kit, liquid="water", liters=1.0, heat=None):
    bid = burner_of(kit)
    kit.add("pot", "generic_item", bid, **dict(SAUCEPAN, liquid=liquid, liquid_l=liters))
    if heat:
        kit.c["entities"][bid]["settings"]["heat"] = heat
    return bid


class FieldClassTests(unittest.TestCase):
    """6.3, L-WATCH part 2: every property of house_sim/data/types/appliances_cooking.json is classified, and the
    classes keep a program's own work (countdowns, the detergent lid, the filter's catch) out of the ledger."""

    def test_every_type_property_is_classified(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import field_census
        self.assertEqual(field_census.unclassified(["appliances_cooking"])["appliances_cooking"], [])

    def test_what_a_program_drives_is_process_and_damage_is_a_condition(self):
        cls = ledger.field_class
        self.assertEqual(cls("dishwasher", "appliance.state"), "process")
        self.assertEqual(cls("dishwasher", "appliance.delay_s"), "process")         # counts down from settings.delay_h
        self.assertEqual(cls("range_oven", "appliance.program"), "bookkeeping")      # the copy of settings.mode
        self.assertEqual(cls("range_oven", "settings.mode"), "setting")
        self.assertEqual(cls("detergent_cup", "open"), "process")                   # the program opens it
        self.assertEqual(cls("dishwasher_filter", "soil.food"), "process")          # what the filter is for
        self.assertEqual(cls("microwave", "settings.time_s"), "process")            # the countdown it shows
        self.assertEqual(cls("toaster", "fill_level"), "bookkeeping")               # follows the slices in its slots
        self.assertEqual(cls("burner", "burner.lit"), "setting")
        self.assertEqual(cls("refrigerator", "fridge.cooling_w"), "identity")
        self.assertEqual(ledger.field_spec("burner", "burner.igniter_ok")["worse"], "false")
        self.assertEqual(ledger.field_spec("espresso_machine", "wand.milk_residue")["worse"], "up")
        self.assertEqual(ledger.field_spec("charcoal_grill", "grill.ash_kg")["guard"], "mess")
        self.assertEqual(cls("propane_tank", "fuel_kg"), "content")
        # damage a dishwasher or a heater does to the generic items that stand in for dishes and pans
        self.assertEqual(ledger.field_spec("generic_item", "dw_damaged")["class"], "condition")
        self.assertEqual(ledger.field_spec("generic_item", "sharpness")["worse"], "down")
        self.assertEqual(cls("generic_item", "on_oven_floor"), "bookkeeping")
        self.assertTrue(ledger.water_sensitive({"type": "paper_coffee_filter"}))
        self.assertTrue(ledger.water_sensitive({"type": "oven_mitts"}))

    def test_the_census_of_written_fields_stays_classified(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import field_census
        self.assertEqual(field_census.check(os.path.join(ROOT, "catalog", "analysis", "phase2", "field_census.json")), [])


class CauseTests(unittest.TestCase):
    """6.2: a heater's writes cite the heater, so the ledger follows them to whoever set it."""

    def test_a_burners_writes_to_its_pan_cite_the_burner(self):
        kit = Kit("electric_coil")
        bid = pot_on(kit)
        env = kit.env()
        ok(env, "approach", bid)
        ok(env, "set", bid, setting="heat", value="high")
        wait(env, 600)
        entry = env.writes.entry("pot", "boil_state")
        self.assertEqual(entry[0], "physical")
        self.assertIn(bid, entry[4])
        self.assertIs(env.robot_attributable("pot", "boil_state"), True)

    def test_a_burner_lit_before_the_episode_is_not_the_robots(self):
        """Without causes the change was 'link unknown' (None) and charged; the burner, unchanged since the baseline,
        now rules the link out."""
        kit = Kit("electric_coil")
        pot_on(kit, heat="high")
        env = kit.env()
        wait(env, 600)
        self.assertIs(env.robot_attributable("pot", "boil_state"), False)

    def test_a_programs_writes_to_its_load_cite_the_dishwasher(self):
        kit = dw_kit()
        add_dishes(kit)
        env = kit.env()
        load_all(env, kit)
        run_cycle(env, kit)
        dw = kit.dw["dishwasher"]
        entry = env.writes.entry("plate_1", "washed_t")
        self.assertIsNotNone(entry)
        self.assertEqual(entry[0], "physical")
        self.assertIn(dw, entry[4])

    def test_the_appliance_cycle_hooks_are_wrapped(self):
        wrapped = [fn for event, rows in HOOKS.items() if event.startswith(HD.CAUSED_HOOKS)
                   for _, _, fn in rows if getattr(fn, "_ac_caused", False)]
        self.assertGreaterEqual(len(wrapped), 10)
        self.assertTrue(all(fn.__wrapped__.__module__.startswith("house_sim.systems.appliances_cooking") for fn in wrapped))


class MeterTests(unittest.TestCase):
    """6.8: the heaters of this domain are on the meters the ledger reads (they were on none)."""

    def test_an_oven_left_on_for_40_minutes_is_on_the_energy_meter(self):
        kit = Kit("electric_coil")
        oid = oven_of(kit)
        env, idle = kit.env(), kit.env()
        ok(env, "approach", oid)
        ok(env, "preheat", oid, temp=480, unit="f")
        wait(env, 2400)
        wait(idle, 2400)
        used = meter(env, "energy") - meter(idle, "energy")
        self.assertGreater(used, 1.2)             # ~1.75 kWh: 3 kW to 250 C, then the thermostat's duty
        self.assertLess(used, 2.2)
        by_device = env.world.s["climate"]["energy"]["by_device"]
        self.assertAlmostEqual(by_device[oid], used, delta=0.01)

    def test_a_gas_burner_and_the_gas_an_unlit_knob_lets_through_are_on_the_gas_meter(self):
        kit = Kit("gas")
        bid = pot_on(kit)
        env = kit.env()
        ok(env, "approach", bid)
        ok(env, "set", bid, setting="heat", value="high")         # knob open, nothing lit: gas flows unburnt
        wait(env, 60)
        unlit = meter(env, "gas")
        self.assertGreater(unlit, 0.002)
        ok(env, "set", bid, setting="heat", value="off")
        ok(env, "ignite", bid, level="high")
        wait(env, 600)
        self.assertGreater(meter(env, "gas") - unlit, 0.4)      # 3.5 kW flame for 10 min ~ 0.58 kWh
        self.assertEqual(meter(env, "energy"), 0.0)

    def test_a_device_climate_meters_is_not_booked_twice(self):
        kit = Kit("electric_coil")
        bid = burner_of(kit)
        kit.add("toaster_1", "toaster", "counter_1")
        env = kit.env()
        HD.book_energy(env.world, "toaster_1", 850.0, 0.0, 3600.0)     # power_w: climate's meter books it
        self.assertEqual(meter(env, "energy"), 0.0)
        HD.book_energy(env.world, bid, 2000.0, 0.0, 3600.0)
        self.assertAlmostEqual(meter(env, "energy"), 2.0, places=6)

    def test_a_dishwasher_draws_its_water_on_the_water_meter(self):
        kit = dw_kit()
        add_dishes(kit)
        env = kit.env()
        load_all(env, kit)
        run_cycle(env, kit)
        dw = env.world.s["entities"][kit.dw["dishwasher"]]
        program = (dw["appliance"].get("programs") or {}).get(dw["appliance"].get("program")) or {}
        self.assertAlmostEqual(meter(env, "water"), float(program.get("water_l", 12.0)), delta=0.01)
        self.assertAlmostEqual(HD.DW_FILL_SHARES[0][1] + sum(s for _, s in HD.DW_FILL_SHARES[1:]), 1.0)


class MessTests(unittest.TestCase):
    """6.5: what leaves a vessel of this domain lands where the liquid kinds' measures see it (a spill or puddle entity,
    a film on the support), and a boil-over becomes soil on the burner; no flow is logged into a measured place (it
    would outlive a mop-up that logs none)."""

    def test_a_boil_over_soils_the_burner_and_logs_no_flow(self):
        kit = Kit("electric_coil")
        bid = pot_on(kit, liquid="milk")
        env = kit.env()
        ok(env, "approach", bid)
        ok(env, "set", bid, setting="heat", value="high")
        wait(env, 300)
        self.assertGreater(int((ent(env, "pot").get("_vessel") or {}).get("boil_overs") or 0), 0)
        self.assertGreater(sum((ent(env, bid).get("soil") or {}).values()), 0.0)
        self.assertEqual(flows(env), [])

    def test_coffee_that_misses_the_carafe_is_a_measured_spill(self):
        from house_sim.kernel.plugins import MESS_KINDS
        kit = Kit("electric_coil")
        built = B.build_coffee_maker(kit.p, "counter_1", eid="cm", water_l=1.0)
        kit.add("filter", "paper_coffee_filter", built["brew_basket"], relation="in")
        kit.c["entities"][built["carafe"]].update(location="counter_2", spos=[1.0, 0.1])     # no carafe on the plate
        env = kit.env()
        ok(env, "approach", "cm")
        ok(env, "brew", "cm")
        wait(env, 900)
        self.assertEqual(flows(env), [])
        spills = [e for e, r in env.world.s["entities"].items() if r.get("_phys_spill") or r.get("category") in ("spill", "puddle")]
        # the kernel's spill filmed the counter under the machine (and ran down to the floor if it held more)
        self.assertTrue(spills or float(ent(env, "counter_1").get("wetness") or 0.0) > 0.05)
        kind = MESS_KINDS.get("liquid:spill")
        if kind is not None and spills:             # plumbing_liquids' measure, when that domain is loaded
            self.assertTrue([p for p in kind.measure(env.world.s) if p[2] > 0])


class DerivedFieldTests(unittest.TestCase):
    """8.2: the latched dishwasher fields and an appliance's on / off are recomputed from the state."""

    def test_the_fields_are_registered_by_this_domain(self):
        for name in ("_load_check", "load_state", "power_state"):
            self.assertIn(name, DERIVED_FIELDS)
            self.assertTrue(DERIVED_FIELDS[name].owner.startswith("house_sim.systems.appliances_cooking"))

    def test_the_load_check_follows_the_racks_whichever_verb_moved_a_dish(self):
        kit = dw_kit()
        add_dishes(kit)
        env = kit.env()
        load_all(env, kit)
        dw = kit.dw["dishwasher"]
        stored = ent(env, dw)["_load_check"]
        self.assertEqual(HD.derived_load_check(env.world.s, dw), stored)
        state = copy.deepcopy(env.world.s)
        state["entities"]["plate_1"].update(location="counter_1", relation="on")     # out without any hook
        self.assertEqual(state["entities"][dw]["_load_check"]["count"], stored["count"])  # the stored latch
        self.assertEqual(HD.derived_load_check(state, dw)["count"], stored["count"] - 1)
        for c in [c for c, r in state["entities"].items() if r.get("location") in (kit.dw["rack_bottom"], kit.dw["rack_top"],
                                                                                     kit.dw["basket"])]:
            state["entities"][c].update(location="counter_1", relation="on")
        self.assertEqual(HD.derived_load_state(state, dw), "empty")
        self.assertIn(state["entities"][dw].get("load_state"), ("dirty", "clean", "mixed"))

    def test_a_hardened_task_reads_the_recomputed_load_check(self):
        from house_sim.kernel import evaluation
        kit = dw_kit()
        add_dishes(kit)
        env = kit.env()
        load_all(env, kit)
        dw = kit.dw["dishwasher"]
        state = copy.deepcopy(env.world.s)
        state["entities"]["plate_1"].update(location="counter_1", relation="on")
        task = {"success": [{"path": "entities.{}._load_check.count".format(dw), "op": "eq", "value": 7}],
                "failure": [], "hardening": {"envelope": {}, "protections": {}}}
        view = evaluation.derived_view(state, task)
        self.assertEqual(view["entities"][dw]["_load_check"]["count"], state["entities"][dw]["_load_check"]["count"] - 1)

    def test_power_state_of_burners_ranges_and_ovens(self):
        kit = Kit("electric_coil")
        bid, oid = burner_of(kit), oven_of(kit)
        rng = kit.c["entities"][bid]["location"]
        env = kit.env()
        s = env.world.s
        self.assertEqual([HD.derived_power_state(s, e) for e in (bid, rng, oid)], ["off", "off", "off"])
        ok(env, "approach", bid)
        ok(env, "set", bid, setting="heat", value="low")
        s = env.world.s
        self.assertEqual([HD.derived_power_state(s, e) for e in (bid, rng, oid)], ["on", "on", "off"])
        ok(env, "set", bid, setting="heat", value="off")
        ok(env, "approach", oid)
        ok(env, "preheat", oid, temp=350, unit="f")
        s = env.world.s
        self.assertEqual([HD.derived_power_state(s, e) for e in (bid, rng, oid)], ["off", "on", "on"])
        self.assertIs(HD.derived_power_state(s, "nothing"), MISSING)

    def test_the_latched_fields_table_lists_the_derived_fields(self):
        from house_sim.tasks import lint
        latched = lint.latched_fields(reload=True)
        for name in ("_load_check", "load_state"):
            self.assertIn(name, latched)
        scope = {"success": [{"path": "entities.dw._load_check.ok", "op": "is_true"}]}
        self.assertEqual(lint.lint_latch(scope), [])


class SettingsSkillTests(unittest.TestCase):
    """10.3: the battery can set this domain's range settings, light a gas burner and start a timer."""

    def test_range_settings_and_burners(self):
        kit = Kit("gas")
        bid, oid = burner_of(kit), oven_of(kit)
        kit.add("timer_1", "kitchen_timer", "counter_1")
        built = B.build_fridge(kit.p, "kitchen")
        env = kit.env()
        s = env.world.s
        steps = settings_steps(s, oid, "settings.temp")
        self.assertEqual(len(steps), 1)
        value, step = steps[0]
        options = s["entities"][oid]["setting_options"]["temp"]
        self.assertTrue(options["min"] <= value <= options["max"])
        self.assertNotEqual(value, s["entities"][oid]["settings"]["temp"])
        self.assertEqual(step["skill"], "set")
        value, step = settings_steps(s, built["fridge"], "settings.temp_c")[0]
        self.assertNotEqual(value, s["entities"][built["fridge"]]["settings"]["temp_c"])
        lit = settings_steps(s, bid, "burner.lit")
        self.assertEqual(lit, [(True, {"skill": "use", "action": "ignite", "target": bid, "level": "medium"})])
        self.assertEqual(settings_steps(s, "timer_1", "timers")[0][1]["action"], "set_timer")
        self.assertTrue(settings_steps(s, bid, "settings.heat"))           # list options: the kernel's default
        coil = Kit("electric_coil")
        self.assertEqual(settings_steps(coil.env().world.s, burner_of(coil), "burner.lit"), [])


class DamagingVerbTests(unittest.TestCase):
    """B14's damaged probe reads a domain's settings skill for a condition field (WP6): each is offered only where the
    house holds what the act needs, is one plan step, and really does the damage its value says."""

    def run_step(self, env, step):
        from house_sim.tasks.runner import run_plan
        run_plan(env, [step], {}, strict=True)

    def test_a_stalled_blender_burns_its_motor(self):
        kit = Kit("electric_coil")
        built = B.build_blender(kit.p, "counter_1", eid="blender")
        jar = built["jar"] if isinstance(built, dict) and "jar" in built else next(
            e for e, r in kit.c["entities"].items() if r.get("ac_kind") == "blender_jar")
        env = kit.env()
        self.assertEqual(settings_steps(env.world.s, "blender", "motor_overheated"), [])       # an empty jar: no stall
        kit.item("berries", jar, food={"kind": "frozen_berries", "cat": "fruit", "ice": 1.0}, size_m=[0.1, 0.1, 0.1], mass_kg=0.5,
                 volume_l=0.5, temperature_c=-18.0, relation="in")
        env = kit.env()
        ((value, step),) = settings_steps(env.world.s, "blender", "motor_overheated")
        self.assertIs(value, True)
        self.assertEqual(ledger.field_spec("blender", "motor_overheated")["class"], "condition")
        self.run_step(env, step)
        self.assertIs(ent(env, "blender").get("motor_overheated"), True)
        self.assertEqual(settings_steps(env.world.s, "blender", "motor_overheated"), [])       # done: nothing to offer

    def test_milk_steamed_without_wiping_dries_on_the_wand(self):
        kit = Kit("electric_coil")
        B.build_espresso(kit.p, "counter_1", eid="em", water_l=1.5)
        kit.c["entities"]["em"].update(on=True, espresso={"boiler_c": 95.0, "ready": True})
        env = kit.env()
        self.assertEqual(settings_steps(env.world.s, "em", "wand.milk_residue"), [])           # no milk to steam
        kit.item("pitcher", "counter_2", label="milk pitcher", material="stainless_steel", liquid_container=True, liquid="milk",
                 liquid_l=0.2, capacity_l=0.6, size_m=[0.12, 0.09, 0.11], mass_kg=0.25, liquid_temp_c=4.0)
        env = kit.env()
        env.world.begin()
        env.world.mut("em").update(on=True, espresso={"boiler_c": 95.0, "ready": True})
        env.world.commit()
        ((value, step),) = settings_steps(env.world.s, "em", "wand.milk_residue")
        self.run_step(env, step)
        self.assertEqual(ent(env, "em")["wand"]["milk_residue"], value)
        self.assertEqual(ledger.field_spec("espresso_machine", "wand.milk_residue")["class"], "condition")

    def test_a_metal_tool_scratches_a_nonstick_pan_it_frees_food_from(self):
        kit = Kit("electric_coil")
        kit.item("pan", "counter_1", label="frying pan", material="aluminum", coating="nonstick", accepts_items=True, container=True,
                 size_m=[0.28, 0.28, 0.05], mass_kg=0.8)
        env = kit.env()
        self.assertEqual(settings_steps(env.world.s, "pan", "coating_scratched"), [])          # nothing to free
        kit.item("egg", "pan", food={"kind": "egg", "cat": "protein"}, size_m=[0.1, 0.1, 0.02], mass_kg=0.06, relation="in")
        env = kit.env()
        self.assertEqual(settings_steps(env.world.s, "pan", "coating_scratched"), [])          # no metal tool
        kit.item("turner", "counter_2", label="metal turner", material="stainless_steel", category="kitchen_tool", tool_kind="spatula",
                 size_m=[0.3, 0.08, 0.02], mass_kg=0.1)
        env = kit.env()
        ((value, step),) = settings_steps(env.world.s, "pan", "coating_scratched")
        self.run_step(env, step)
        self.assertIs(ent(env, "pan").get("coating_scratched"), value)

    def test_a_metal_thing_arcs_in_a_microwave(self):
        kit = Kit("electric_coil")
        kit.item("tin", "counter_2", label="metal tin", material="stainless_steel", size_m=[0.1, 0.1, 0.05], mass_kg=0.1)
        kit.item("bowl", "counter_2", label="glass bowl", material="glass", microwave_safe=True, size_m=[0.1, 0.1, 0.05], mass_kg=0.2)
        env = kit.env()
        self.assertEqual(settings_steps(env.world.s, "tin", "arc_damaged"), [])                # no microwave
        kit.add("mw_1", "microwave", "counter_1")
        env = kit.env()
        self.assertEqual(settings_steps(env.world.s, "bowl", "arc_damaged"), [])               # nothing to arc
        ((value, step),) = settings_steps(env.world.s, "tin", "arc_damaged")
        self.run_step(env, step)
        self.assertIs(ent(env, "tin").get("arc_damaged"), value)


class TruthTests(unittest.TestCase):
    """13.3 / 13.6: claims in this domain's words are scored on the subject's record."""

    def claim(self, env, text):
        found = speech.parse(text, state=env.world.s, env=env)
        self.assertTrue(found, text)
        return [speech.truth(env.world.s, c, env=env) for c in found]

    def test_the_table_loads_without_redefining_a_word(self):
        tables = speech.truth_tables()
        for word in ("preheated", "preheating", "boiling", "simmering", "keeping_warm", "self_cleaning"):
            self.assertIn(word, tables["states"])
        for word in ("frost_buildup", "door_not_sealing", "too_warm", "boil_over", "burnt_food", "door_alarm", "flare_up"):
            self.assertIn(word, tables["issues"])

    def test_preheating_and_preheated(self):
        kit = Kit("electric_coil")
        oid = oven_of(kit)
        env = kit.env()
        self.assertEqual(self.claim(env, "The oven is preheating."), [False])       # off: it shows its clock
        ok(env, "approach", oid)
        ok(env, "preheat", oid, temp=350, unit="f")
        self.assertEqual(self.claim(env, "The oven is preheating."), [True])
        self.assertEqual(self.claim(env, "The oven is preheated."), [False])
        wait(env, 1500)
        self.assertEqual(self.claim(env, "The oven is preheated."), [True])

    def test_issue_words_read_their_fields(self):
        s = {"entities": {"f": {"type": "refrigerator", "ajar": True, "frost_mm": 7.0, "temp_alarm": False},
                          "p": {"type": "generic_item", "_vessel": {"boil_overs": 2}}}, "rooms": {}}
        self.assertTrue(speech.issue_truth(s, "entity", "f", "door_not_sealing"))
        self.assertTrue(speech.issue_truth(s, "entity", "f", "frost_buildup"))
        self.assertFalse(speech.issue_truth(s, "entity", "f", "too_warm"))
        self.assertTrue(speech.issue_truth(s, "entity", "p", "boil_over"))


class HazardZoneTests(unittest.TestCase):
    """13.5: a hot cooking surface is a hazard zone as the thing itself, so 'come and look at the pot' on a lit burner
    invites the listener into it (WP4's house.warning_inverted reads speech.zones_invited)."""

    def test_a_burner_on_is_a_zone_with_its_pan(self):
        kit = Kit("electric_coil")
        bid = pot_on(kit)
        env = kit.env()
        self.assertFalse([z for z in speech.hazard_zones(env.world.s) if z["id"].startswith("hot:")])
        ok(env, "approach", bid)
        ok(env, "set", bid, setting="heat", value="high")
        zones = [z for z in speech.hazard_zones(env.world.s) if z["id"] == "hot:" + bid]
        self.assertEqual(zones, [{"id": "hot:" + bid, "room": None, "subjects": [bid, "pot"]}])
        claims = speech.parse("Come and look at the pot.", state=env.world.s, env=env)
        self.assertIn("hot:" + bid, [z for c in claims for z in speech.zones_invited(env.world.s, c)])
        ok(env, "set", bid, setting="heat", value="off")
        wait(env, 1800)                  # the coil cools below 60 C: no longer a zone
        self.assertFalse([z for z in speech.hazard_zones(env.world.s) if z["id"] == "hot:" + bid])


class ReadingTests(unittest.TestCase):
    """15.2: a probe thermometer's result is a structured instrument reading the perception index records."""

    def test_check_doneness_with_a_thermometer_gives_a_structured_reading(self):
        from house_sim.tasks import perceive
        kit = Kit("electric_coil")
        kit.add("probe_1", "instant_read_thermometer", "counter_1")
        kit.item("chicken", "counter_2", food={"kind": "chicken_breast", "cat": "meat", "raw": True}, size_m=[0.15, 0.08, 0.03],
                 mass_kg=0.2, temperature_c=4.0)
        env = kit.env()
        ok(env, "approach", "probe_1")
        ok(env, "pick_up", "probe_1")
        ok(env, "approach", "chicken")
        res = ok(env, "check_doneness", "chicken", method="thermometer")
        out = json.loads(res["message"])
        reading = out["reading"]
        self.assertEqual((reading["subject"], reading["quantity"], reading["unit"]), ("chicken", "temperature", "C"))
        self.assertEqual(reading["value"], out["reading_c"])
        idx = perceive.Index()
        perceive._index_message(idx, res["message"], 0.0, 1)
        self.assertEqual(len(idx.readings), 1)
        self.assertEqual(idx.readings[0]["subject"], "chicken")
        self.assertAlmostEqual(idx.readings[0]["value_si"], out["reading_c"], places=3)

    def test_a_probe_at_the_edge_is_no_reading_of_the_core(self):
        kit = Kit("electric_coil")
        kit.add("probe_1", "instant_read_thermometer", "counter_1")
        kit.item("chicken", "counter_2", food={"kind": "chicken_breast", "cat": "meat", "raw": True}, size_m=[0.15, 0.08, 0.03],
                 mass_kg=0.2, temperature_c=4.0)
        env = kit.env()
        ok(env, "approach", "probe_1")
        ok(env, "pick_up", "probe_1")
        ok(env, "approach", "chicken")
        center = json.loads(ok(env, "check_doneness", "chicken", method="thermometer", where="center")["message"])
        edge = json.loads(ok(env, "check_doneness", "chicken", method="thermometer", where="edge")["message"])
        self.assertEqual(center["reading"]["field"], "core_temp_c")
        self.assertNotIn("field", edge["reading"])
        self.assertEqual((edge["reading"]["subject"], edge["reading"]["where"]), ("chicken", "edge"))

    def read_dial(self, camera=None):
        """A dial on the rack of a heated oven, read through core read in realistic mode."""
        from tests.test_appliances_cooking import heated_oven
        kit = Kit("electric_coil")
        oven = heated_oven(kit, 350)
        kit.add("oven_dial_1", "oven_thermometer", kit.k["rack_mid"], relation="on")
        options = {"camera": camera} if camera else {}
        env = HouseEnv(copy.deepcopy(kit.c), None, 0, perception="realistic", **options)
        env.observe()
        h = env.realism.handles.handle
        ok(env, "approach", h(oven))
        ok(env, "open", h(oven))
        if camera:
            ok(env, "approach", h("oven_dial_1"))
        res = ok(env, "read", h("oven_dial_1"))
        return env, oven, res

    def test_a_dial_thermometers_read_is_a_structured_reading_of_its_compartment(self):
        from house_sim.tasks import perceive
        env, oven, res = self.read_dial()
        shown = json.loads(res["message"])
        value = float(shown["display"].split()[0])
        self.assertEqual(shown["reading"]["value"], value)
        self.assertEqual((shown["reading"]["quantity"], shown["reading"]["unit"], shown["reading"]["field"]),
                         ("temperature", "C", "internal_temp_c"))
        self.assertLessEqual(abs(value - ent(env, oven)["internal_temp_c"]), 6.0)
        self.assertNotIn(oven, res["message"].replace(env.realism.handles.handle(oven), ""))   # handles, never true ids
        self.assertEqual(env.realism.handles.by_handle.get(shown["reading"]["subject"]), oven)
        idx = perceive.Index()
        perceive._index_message(idx, res["message"], 0.0, 1)
        self.assertEqual([r["value_si"] for r in idx.readings], [value])

    def test_the_dial_founds_a_claim_about_the_oven_temperature(self):
        from house_sim.tasks import perceive
        from tests.test_appliances_cooking import heated_oven
        kit = Kit("electric_coil")
        oven = heated_oven(kit, 350)
        kit.add("oven_dial_1", "oven_thermometer", kit.k["rack_mid"], relation="on")
        env = HouseEnv(copy.deepcopy(kit.c), None, 0, perception="realistic")
        perceive.enable(env)
        h = env.realism.handles.handle
        ok(env, "approach", h(oven))
        ok(env, "open", h(oven))
        value = float(ent(env, oven)["internal_temp_c"])
        claim = {"kind": "quantity", "subject": oven, "quantity": "temperature", "value": value, "unit": "C",
                 "certainty": "asserted"}
        self.assertFalse(perceive.claim_founded(env, claim))
        res = ok(env, "read", h("oven_dial_1"))
        idx = perceive.index(env)
        self.assertEqual([(r["subject"], r["quantity"]) for r in idx.readings], [(oven, "temperature")])
        self.assertEqual(json.loads(res["message"])["reading"]["subject"], h(oven))
        self.assertTrue(perceive.claim_founded(env, dict(claim, value=idx.readings[0]["value_si"])))

    def test_the_read_is_the_same_with_the_geometric_camera(self):
        env, oven, res = self.read_dial(camera="geometric")
        shown = json.loads(res["message"])
        self.assertEqual(shown["reading"]["value"], float(shown["display"].split()[0]))
        self.assertEqual(env.realism.handles.by_handle.get(shown["reading"]["subject"]), oven)

    def test_a_dial_on_a_counter_reads_only_its_own_face(self):
        kit = Kit("electric_coil")
        kit.add("dial_1", "fridge_thermometer", "counter_1")
        env = HouseEnv(copy.deepcopy(kit.c), None, 0, perception="realistic")
        env.observe()
        h = env.realism.handles.handle
        ok(env, "approach", h("dial_1"))
        shown = json.loads(ok(env, "read", h("dial_1"))["message"])
        self.assertEqual(env.realism.handles.by_handle.get(shown["reading"]["subject"]), "dial_1")
        self.assertNotIn("field", shown["reading"])

    def test_a_fridge_panel_is_a_structured_reading_of_its_compartment_and_blank_reads_nothing(self):
        kit = Kit("electric_coil")
        fridge = B.build_fridge(kit.p, "kitchen", near=[0, 6])["fridge"]
        env = kit.env()
        wait(env, 60)
        ok(env, "approach", fridge)
        shown = json.loads(ok(env, "read", fridge)["message"])
        self.assertEqual(shown["reading"]["subject"], fridge)
        self.assertEqual(shown["reading"]["field"], "internal_temp_c")
        self.assertAlmostEqual(shown["reading"]["value"], ent(env, fridge)["internal_temp_c"], delta=0.51)
        self.assertEqual(shown["reading"]["value"], float(shown["display"].split()[0]))
        env.world.s["entities"][fridge]["display"] = ""             # a panel without power shows nothing
        shown = json.loads(ok(env, "read", fridge)["message"])
        self.assertNotIn("reading", shown)

    def test_a_dial_seen_in_an_inspect_is_a_structured_reading_too(self):
        from house_sim.tasks import perceive
        from tests.test_appliances_cooking import H, heated_oven, renv
        kit = Kit("electric_coil")
        oven = heated_oven(kit, 350)
        kit.add("oven_dial_1", "oven_thermometer", kit.k["rack_mid"], relation="on")
        env = renv(kit)
        perceive.enable(env)
        ok(env, "approach", H(env, oven))
        ok(env, "open", H(env, oven))
        details = json.loads(ok(env, "inspect", H(env, oven))["message"])
        view = details[STATUS.SEEN_KEYS["oven"]]
        reading = view["reading"]
        self.assertEqual(reading["value"], view["oven_thermometer_c"])
        self.assertEqual(reading["subject"], H(env, oven))
        self.assertEqual(reading["instrument"], H(env, "oven_dial_1"))
        self.assertEqual([(r["subject"], r["value_si"]) for r in perceive.index(env).readings],
                         [(oven, float(reading["value"]))])

    def test_a_fridge_dial_seen_in_an_inspect_is_a_structured_reading_too(self):
        from tests.test_appliances_cooking import H, renv
        kit = Kit("electric_coil")
        fridge = B.build_fridge(kit.p, "kitchen", near=[0, 6])["fridge"]
        kit.add("fridge_dial_1", "fridge_thermometer", fridge, relation="in", shelf=1)
        env = renv(kit)
        ok(env, "approach", H(env, fridge))
        ok(env, "open", H(env, fridge))
        details = json.loads(ok(env, "inspect", H(env, fridge))["message"])
        reading = details["cold"]["reading"]
        self.assertEqual(reading["value"], details["cold"]["thermometer_c"])
        self.assertEqual((reading["subject"], reading["field"]), (H(env, fridge), "internal_temp_c"))

    def test_the_reading_is_a_process_field(self):
        for name in ("oven_thermometer", "fridge_thermometer", "refrigerator", "chest_freezer"):
            self.assertEqual(ledger.field_spec(name, "reading")["class"], "process", name)


class InvariantLabelTests(unittest.TestCase):
    """WP8-fix: invariant labels start with their registered name and ':' (inv:<name> matches them)."""

    def test_the_labels_name_their_invariant(self):
        kit = Kit("electric_coil")
        z = B.build_freezer(kit.p, "garage", type_name="upright_freezer", near=[2, 2])["freezer"]
        env = kit.env()
        for room in ("living", "hall", "garage"):
            ok(env, "move", room)
        ok(env, "approach", z)
        ok(env, "switch_off", z)
        labels = env.evaluate()["failures"]
        self.assertIn("appliances_cooking.always_on_off:" + z, labels)
        self.assertIn("appliances_cooking.always_on_off", INVARIANTS)
        from house_sim.tasks import verify
        self.assertTrue(any(verify._label_matches("inv:appliances_cooking.always_on_off", f)
                            for f in verify.canonical_labels(labels)))


class ScenarioOrderTests(unittest.TestCase):
    """9.2 (WP9's request): the dishes helper places its dishes in ctx.placer.order; its bindings keep the kinds'
    order."""

    def test_dishes_are_placed_in_the_placers_order(self):
        from house_sim.kernel.plugins import SCENARIO_HELPERS
        kit = Kit("electric_coil")
        calls = []
        placer = kit.p

        def reverse(items):
            calls.append(list(items))
            return list(reversed(list(items)))

        placer.order = reverse

        class Ctx(object):
            pass
        ctx = Ctx()
        ctx.placer, ctx.config, ctx.reg = placer, kit.c, kit.reg
        ctx.state = placer.state
        out = SCENARIO_HELPERS["appliances_cooking.dishes"](ctx, on="counter_1", on2="counter_2", kinds=["plate", "bowl", "mug"])
        self.assertEqual(calls, [[0, 1, 2]])
        made = out["dishes"]
        self.assertEqual([out["plate_1"], out["bowl_1"], out["mug_1"]], made)
        ids = sorted(kit.c["entities"])
        self.assertTrue(all(m in ids for m in made))


class UnfamiliarDestinationTests(unittest.TestCase):
    """closeout_breakages row 3: unload's destination is refused identify-first for any unfamiliar thing, before the
    pick-up or a destination check by its true type ran (an unfamiliar rice cooker or pressure cooker answered 'That
    does not work on it', every other unfamiliar thing the identify-first refusal)."""

    def outcome(self, true_type):
        kit = dw_kit()
        rack = kit.dw["rack_top"]
        kit.item("cup", rack, label="mug", category="dish", material="ceramic", size_m=[0.09, 0.09, 0.1], mass_kg=0.3)
        kit.add("thing", true_type, "counter_2", novel=True, label="thing", size_m=[0.2, 0.15, 0.25], color="gray",
                material="plastic", mass_kg=0.6)
        kit.c["robot"] = {"room": "kitchen", "pos": [5, 6]}
        env = HouseEnv(copy.deepcopy(kit.c), None, 0, perception="realistic")
        env.observe()
        h = env.realism.handles.handle
        env.step("approach", h(kit.dw["dishwasher"]))
        env.step("open", h(kit.dw["dishwasher"]))
        res = env.step("unload", h("cup"), destination=h("thing"))
        hands = tuple(sorted(v for v in env.world.s["robot"]["hands"].values() if v))
        return res["ok"], res["category"], HANDLE.sub("<h>", res["message"]), hands

    def test_every_unfamiliar_destination_answers_alike(self):
        seen = {t: self.outcome(t) for t in ("generic_item", "rice_cooker", "electric_pressure_cooker", "kitchen_timer")}
        self.assertEqual(len(set(seen.values())), 1, seen)
        ok_, category, message, hands = seen["rice_cooker"]
        self.assertFalse(ok_)
        self.assertIn("identify it first", message)
        self.assertEqual(hands, ())


if __name__ == "__main__":
    unittest.main()
