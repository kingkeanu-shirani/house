"""Generation-1 houses stay byte-identical (docs/design/TIER1_SWEEP.md section 14.1).

tests/data/gen1_digests.json was recorded before any generator work (tier-1 A0): the digest of generate_house(seed,
**house) for seeds 0-19 with default options and for every distinct `house` block of the authored specs on seeds 0, 1,
2, 41 and 97. This file checks every default entry and one entry per distinct house block (86 houses, about 35 s);
HOUSE_SIM_GEN1_ALL=1 checks all 340 (tools/orchestration/tier1_gate.py G5 does that on every gate).

Run: python -m unittest tests/test_generator_compat.py   (also with HOUSE_SIM_STRICT=1)
"""
import copy
import json
import os
import random
import unittest
from pathlib import Path

from house_sim import generator
from house_sim.generator import generate_house
from house_sim.kernel.plugins import FURNISHERS, load_domains
from house_sim.kernel.predicates import digest
from house_sim.kernel.registry import default_registry

DIGESTS = Path(__file__).resolve().parent / "data" / "gen1_digests.json"
GEN2_FURNISHERS = ("gen.bedrooms", "gen.furnishings", "gen.belongings", "gen.residents")


def _entries():
    data = json.loads(DIGESTS.read_text(encoding="utf-8"))
    defaults = [{"seed": e["seed"], "house": {}, "digest": e["digest"]} for e in data["defaults"]]
    if os.environ.get("HOUSE_SIM_GEN1_ALL") == "1":
        return data, defaults + list(data["spec_houses"])
    first = {}
    for entry in data["spec_houses"]:
        first.setdefault(json.dumps(entry["house"], sort_keys=True), entry)
    return data, defaults + [first[k] for k in sorted(first)]


class GenerationOneDigests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.data, cls.entries = _entries()

    def test_file_is_the_a0_recording(self):
        self.assertEqual(self.data["format"], "house-gen1-digests-v1")
        self.assertEqual(len(self.data["defaults"]), 20)
        self.assertEqual(len(self.data["spec_houses"]), 320)
        self.assertEqual(self.data["history"][0]["by"], "A0")

    def test_generation_one_houses_are_unchanged(self):
        changed = []
        for entry in self.entries:
            if digest(generate_house(entry["seed"], **entry["house"])) != entry["digest"]:
                changed.append((entry["seed"], entry["house"]))
        self.assertEqual(changed, [], "generation-1 houses changed (re-record only per TIER1_SWEEP 14.1)")

    def test_explicit_gen_1_is_the_default(self):
        for entry in self.entries[::17]:
            config = generator._generate(entry["seed"], gen=1, **entry["house"])
            self.assertEqual(digest(config), entry["digest"], (entry["seed"], entry["house"]))
            self.assertNotIn("gen", config["metadata"])
            self.assertNotIn("profile", config["household"])

    def test_generation_two_furnishers_leave_generation_one_alone(self):
        """Registered for every house (1.16 allows it), they return at once unless opts["gen"] >= 2."""
        load_domains()
        registry = default_registry()
        config = generate_house(3)
        before = digest(config)
        for _, name, fn in FURNISHERS:
            if name in GEN2_FURNISHERS:
                for opts in ({}, {"gen": 1}):
                    fn(config, random.Random("x"), dict(opts, plan={}, region="us"), registry)
        self.assertEqual(digest(config), before)
        self.assertEqual(sorted(n for _, n, _ in FURNISHERS if n.startswith("gen.")), sorted(GEN2_FURNISHERS))

    def test_unknown_generation_is_refused(self):
        for gen in (0, 3, "2"):
            with self.assertRaises(ValueError):
                generator._generate(0, gen=gen)

    def test_cached_houses_are_copies(self):
        a = generate_house(1)
        b = generate_house(1)
        a["rooms"].clear()
        self.assertTrue(b["rooms"])
        self.assertEqual(digest(generate_house(1)), digest(copy.deepcopy(b)))


if __name__ == "__main__":
    unittest.main()
