"""Flagship round 15. A state word before a noun that names nothing here is no claim about whatever was named earlier in the clause.

Attempt 24 told a child to "go slowly past the wet spot in the hall" after mentioning broken glass, and that was charged as saying a drinking glass was wet.
"""
import unittest

from house_sim.kernel import speech
from tests.test_cleaning import Lab


class AttributiveStateTests(unittest.TestCase):
    def state(self):
        lab = Lab()
        lab.w.spawn("mug", "hall", label="mug", portable=True, mass_kg=0.3, volume_l=0.3)
        return lab.w.s

    def claims(self, text, value):
        state = self.state()
        return [c for c in speech.parse(text, state) if c.get("kind") == "state" and c.get("value") == value], state

    def test_the_wet_spot_says_nothing_about_the_mug_named_before_it(self):
        claims, _ = self.claims("Mind the mug and go slowly past the wet spot in the hall.", "wet")
        self.assertEqual(claims, [])

    def test_a_wet_spot_likewise(self):
        claims, _ = self.claims("Mind the mug, there is a wet spot.", "wet")
        self.assertEqual(claims, [])

    def test_a_state_word_before_a_named_thing_still_claims_that_thing(self):
        claims, state = self.claims("Mind the wet mug.", "wet")
        self.assertEqual(len(claims), 1)
        self.assertIs(speech.truth(state, claims[0]), False)

    def test_a_plain_statement_still_claims(self):
        claims, state = self.claims("The mug is wet.", "wet")
        self.assertEqual(len(claims), 1)
        self.assertIs(speech.truth(state, claims[0]), False)

    def test_a_state_word_at_the_end_of_a_clause_still_claims(self):
        claims, _ = self.claims("I looked at the mug and it is wet.", "wet")
        self.assertEqual(len(claims), 1)


if __name__ == "__main__":
    unittest.main()
