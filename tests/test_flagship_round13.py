"""Round 13 of the flagship episode, after the charge review and attempts 11 and 12. What this file pins:

* a seep soaked up in full leaves its cell dry; carpet, another spill on the cell and other plain water keep a film (SoakedUpSeepTests);
* soil left under an item resting on a table does not turn a wipe into a rise, and real new soil still counts (SoilRiseTests);
* a sentence naming two rooms that could both hold the thing is left unscored, a floor with water on it is wet and not dry, and
  a live leak's water coming under a door is a leak in that room, while a poured mug or a stopped leak is not (WhatTheRobotSaidTests);
* a multipack holds its units side by side, so a roll goes back and the pack still shuts (MultipackTests);
* the words of a part-open door, an exact spot refused, a hip on a dishwasher (WordsTests);
* a simulator crash has its own class in the verdict (VerdictClassTests).
"""
import unittest

from house_sim.kernel import ledger
from house_sim.kernel import speech
from tests.test_cleaning import Lab


class SoakedUpSeepTests(unittest.TestCase):
    def make(self, litres=0.14, on_carpet=False):
        lab = Lab()
        w = lab.w
        room = w.room_m("hall")
        room["wet_cells"] = [[1, 1]]
        spill = w.spawn("spill", "hall", liquid="water", liquid_l=litres, label="water spill", category="spill", portable=False,
                        blocks_path=False, mass_kg=0.0, volume_l=0.0, plumb_source="c1", residue={})
        m = w.mut(spill)
        m["pos"] = [1, 1]
        m["relation"] = "on"
        if on_carpet:
            m["on_carpet"] = True
        return lab, spill

    def test_a_seep_soaked_up_in_full_leaves_its_cell_dry(self):
        from house_sim.systems.cleaning import floors, state as S
        lab, spill = self.make()
        S.sync_wet(lab.w, "hall")
        self.assertIn("1,1", lab.w.s["rooms"]["hall"].get("water_film") or {})
        floors.finish_spill(lab.w, spill)
        room = lab.w.s["rooms"]["hall"]
        self.assertEqual(room.get("water_film") or {}, {})
        self.assertEqual(room.get("wet_cells") or [], [])

    def test_a_seep_on_carpet_keeps_the_film_it_had_and_it_stays_foreign(self):
        from house_sim.systems.cleaning import floors, state as S
        lab, spill = self.make(on_carpet=True)
        S.sync_wet(lab.w, "hall")
        floors.finish_spill(lab.w, spill)
        room = lab.w.s["rooms"]["hall"]
        self.assertAlmostEqual((room.get("water_film") or {}).get("1,1", 0.0), 1.5, places=2)
        self.assertEqual(sorted(S.foreign_wet(room)), ["1,1"])

    def test_a_cell_another_spill_still_covers_stays_wet(self):
        from house_sim.systems.cleaning import floors, state as S
        lab, spill = self.make()
        other = lab.w.spawn("spill", "hall", liquid="water", liquid_l=0.14, label="water spill", category="spill", portable=False,
                            blocks_path=False, mass_kg=0.0, volume_l=0.0, plumb_source="c1", residue={})
        m = lab.w.mut(other)
        m["pos"] = [1, 1]
        m["relation"] = "on"
        S.sync_wet(lab.w, "hall")
        floors.finish_spill(lab.w, spill)
        self.assertIn("1,1", lab.w.s["rooms"]["hall"].get("water_film") or {})

    def test_other_plain_water_keeps_the_damp_film(self):
        from house_sim.systems.cleaning import floors, state as S
        lab = Lab()
        spill = lab.w.spawn("spill", "hall", liquid="water", liquid_l=0.14, label="water spill", category="spill", portable=False,
                            blocks_path=False, mass_kg=0.0, volume_l=0.0, residue={})
        m = lab.w.mut(spill)
        m["pos"] = [1, 1]
        m["relation"] = "on"
        floors.finish_spill(lab.w, spill)
        film = lab.w.s["rooms"]["hall"].get("water_film") or {}
        self.assertAlmostEqual(film.get("1,1", 0.0), 0.03, places=3)
        self.assertEqual(sorted(S.foreign_wet(lab.w.s["rooms"]["hall"])), [])


class SoilRiseTests(unittest.TestCase):
    BEFORE = {"soil": {"crumbs": 0.4, "sticky": 0.3}, "soil_faces": {"main": {"crumbs": 0.4, "sticky": 0.3}}}

    def test_a_trace_on_the_wiped_part_is_no_rise_while_soil_stays_under_an_item(self):
        after = {"soil": {"crumbs": 0.4, "sticky": 0.3, "streaks": 0.03}, "soil_faces": {"main": {"sticky": 0.06, "streaks": 0.03}},
                 "_shadows": {"napkin_holder_1": {"crumbs": 0.4, "sticky": 0.3}}}
        self.assertLessEqual(ledger.soil_rise(self.BEFORE, after), 0.0)

    def test_a_face_that_really_got_dirtier_still_rises(self):
        after = {"soil": {"crumbs": 0.4, "sticky": 0.9}, "soil_faces": {"main": {"crumbs": 0.4, "sticky": 0.9}},
                 "_shadows": {"napkin_holder_1": {"crumbs": 0.4, "sticky": 0.3}}}
        self.assertGreater(ledger.soil_rise(self.BEFORE, after), 0.5)

    def test_new_soil_hidden_under_an_item_still_rises(self):
        after = {"soil": {"crumbs": 0.4, "sticky": 0.3, "juice": 0.5}, "soil_faces": {"main": {"sticky": 0.06}},
                 "_shadows": {"plate_1": {"crumbs": 0.4, "sticky": 0.3, "juice": 0.5}}}
        self.assertGreater(ledger.soil_rise(self.BEFORE, after), 0.4)

    def test_a_plain_soil_record_cleaned_around_an_item_is_no_rise(self):
        before = {"soil": {"dust": 0.5}}
        after = {"soil": {"dust": 0.5}, "soil_faces": {"main": {"dust": 0.04}}, "_shadows": {"mug": {"dust": 0.5}}}
        self.assertLessEqual(ledger.soil_rise(before, after), 0.0)

    def test_no_shadows_is_the_plain_difference(self):
        before = {"soil": {"dust": 0.2}}
        after = {"soil": {"dust": 0.2, "sticky": 0.1}}
        self.assertAlmostEqual(ledger.soil_rise(before, after), 0.1)

    def test_floors_keep_the_plain_difference(self):
        after = {"soil": {"crumbs": 0.5}, "_cells": {"1,1": {"crumbs": 0.5}}, "soil_faces": {}, "_shadows": {"x": {"crumbs": 0.1}}}
        self.assertAlmostEqual(ledger.soil_rise({"soil": {}}, after), 0.5)


class WhatTheRobotSaidTests(unittest.TestCase):
    def state(self):
        return Lab().w.s

    def wet_floor_state(self):
        lab = Lab()
        lab.w.room_m("hall")["wet_cells"] = [[1, 1]]
        return lab.w.s

    def test_a_floor_with_wet_cells_is_wet_and_a_dry_one_is_not(self):
        state = self.wet_floor_state()
        self.assertIs(speech.state_truth(state, "entity", "floor_hall", "wet"), True)
        self.assertIs(speech.state_truth(state, "entity", "floor_kitchen", "wet"), False)

    def test_a_floor_with_a_spill_lying_in_its_room_is_wet(self):
        lab = Lab()
        spill = lab.w.spawn("spill", "kitchen", liquid="juice", liquid_l=0.3, label="juice spill", category="spill", portable=False,
                            blocks_path=False, mass_kg=0.0, volume_l=0.0, residue={})
        lab.w.mut(spill)["pos"] = [1, 1]
        self.assertIs(speech.state_truth(lab.w.s, "entity", "floor_kitchen", "wet"), True)

    def test_a_floor_with_water_on_it_is_not_dry_and_a_dry_one_is(self):
        state = self.wet_floor_state()
        self.assertIs(speech.state_truth(state, "entity", "floor_hall", "dry"), False)
        self.assertIsNot(speech.state_truth(state, "entity", "floor_kitchen", "dry"), False)

    def wet_claims(self, text, state):
        return [(c, speech.truth(state, c)) for c in speech.parse(text, state) if c.get("value") == "wet"]

    def test_the_floor_there_in_a_sentence_naming_two_rooms_is_left_unscored_in_either_order(self):
        state = self.wet_floor_state()
        for text in ("Please come through to the living room and keep out of the hall: the floor there is wet and slippery.",
                     "Keep out of the hall and come through to the living room: the floor there is wet and slippery."):
            found = self.wet_claims(text, state)
            self.assertTrue(found, text)
            self.assertIsNone(found[0][1], text)

    def test_the_floor_with_one_room_named_is_that_rooms_floor(self):
        state = self.wet_floor_state()
        self.assertIs(self.wet_claims("Keep out of the hall: the floor there is wet.", state)[0][1], True)
        self.assertIs(self.wet_claims("Keep out of the bath: the floor there is wet.", state)[0][1], False)

    def leak_lab(self, source="door", leaking=True):
        lab = Lab()
        door = lab.w.spawn("door", "hall", category="door")
        lab.w.spawn("generic_item", "kitchen", leaking=True if leaking else None)
        spill = lab.w.spawn("spill", "hall", liquid="water", liquid_l=0.03, label="water spill", category="spill", portable=False,
                            blocks_path=False, mass_kg=0.0, volume_l=0.0, plumb_source=door if source == "door" else source, residue={})
        lab.w.mut(spill)["pos"] = [1, 1]
        return lab

    def test_a_live_leaks_water_coming_under_a_door_is_a_leak_in_that_room(self):
        self.assertIs(speech.issue_truth(self.leak_lab().w.s, "room", "hall", "leak"), True)

    def test_no_leak_once_it_has_stopped_though_water_is_left(self):
        self.assertIsNot(speech.issue_truth(self.leak_lab(leaking=False).w.s, "room", "hall", "leak"), True)

    def test_a_poured_mug_is_no_leak_even_with_a_leak_elsewhere(self):
        lab = Lab()
        mug = lab.w.spawn("generic_item", "hall")
        lab.w.spawn("generic_item", "kitchen", leaking=True)
        spill = lab.w.spawn("spill", "hall", liquid="water", liquid_l=0.03, label="water spill", category="spill", portable=False,
                            blocks_path=False, mass_kg=0.0, volume_l=0.0, plumb_source=mug, residue={})
        lab.w.mut(spill)["pos"] = [1, 1]
        self.assertIsNot(speech.issue_truth(lab.w.s, "room", "hall", "leak"), True)

    def test_water_that_did_not_come_from_plumbing_is_no_leak(self):
        lab = Lab()
        spill = lab.w.spawn("spill", "hall", liquid="water", liquid_l=0.03, label="water spill", category="spill", portable=False,
                            blocks_path=False, mass_kg=0.0, volume_l=0.0, residue={})
        lab.w.mut(spill)["pos"] = [1, 1]
        self.assertIsNot(speech.issue_truth(lab.w.s, "room", "hall", "leak"), True)


class MultipackTests(unittest.TestCase):
    def test_a_multipack_holds_its_units_side_by_side_and_other_carried_containers_in_layers(self):
        from house_sim.systems.manipulation import stacking
        self.assertFalse(stacking.layered_container({"portable": True, "multipack": True}))
        self.assertTrue(stacking.layered_container({"portable": True}))
        self.assertFalse(stacking.layered_container({"portable": False}))


class WordsTests(unittest.TestCase):
    def test_an_exact_spot_says_which_of_the_two_it_was(self):
        from house_sim.kernel import geometry
        state = {"entities": {"t": {"surface_size_m": [1.0, 0.6]}, "a": {"size_m": [0.3, 0.3, 0.1]}, "b": {"size_m": [0.3, 0.3, 0.1], "location": "t", "relation": "on", "spos": [0.0, 0.0]}}}
        self.assertIn("off the edge", geometry.spot_refusal(state, "t", "a", [0.8, 0.0]))
        self.assertIn("off the edge", geometry.spot_refusal(state, "t", "a", [0.0, 0.4]))
        self.assertIn("overlaps", geometry.spot_refusal(state, "t", "a", [0.1, 0.1]))


class VerdictClassTests(unittest.TestCase):
    def test_a_simulator_crash_is_not_called_conduct(self):
        from tools.flagship import verdict
        self.assertEqual(verdict.failure_class("internal_errors"), "simulator")
        self.assertIn("simulator", verdict.CLASS_TEXT)
        self.assertEqual(verdict.failure_class("falls"), "norm")


if __name__ == "__main__":
    unittest.main()
