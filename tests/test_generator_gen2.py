"""Generator generation 2 (docs/design/TIER1_SWEEP.md sections 12-14, docs/GENERATOR.md): household composition,
profiles and schedules, belongings with homes and habits, common furnishings, infant items, sleepers in bed, exterior
steps, the weather process and clock model 2.

Run: python -m unittest tests/test_generator_gen2.py   (also with HOUSE_SIM_STRICT=1)
"""
import datetime
import json
import math
import os
import random
import subprocess
import sys
import time
import unittest
from collections import Counter
from pathlib import Path

from house_sim import generator
from house_sim.generator import belongings, climate, generate_house, profile
from house_sim.kernel import clock, geometry
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import FURNISHERS, load_domains
from house_sim.kernel.predicates import digest
from house_sim.kernel.registry import default_registry

ROOT = Path(__file__).resolve().parents[1]
WINTER = {"coat", "puffer_jacket"}
FRIDAY_10 = "2026-10-09T10:00:00"


def within(test, observed, expected, n, sigmas=4.0, msg=""):
    """A binomial share within `sigmas` standard errors (and 0.01) of its expectation."""
    se = math.sqrt(max(expected * (1 - expected), 1e-6) / n)
    test.assertLessEqual(abs(observed - expected), sigmas * se + 0.01, "{}: {:.3f} vs {:.3f}".format(msg, observed, expected))


def generated(seed, **opts):
    report = []
    config = generator._generate(seed, gen=2, report=report, **opts)
    return config, report


def owned(config, pid):
    return sorted(e for e, rec in config["entities"].items() if rec.get("owner") == pid)


# ----------------------------------------------------------------------------------------------------------------
# composition


class Composition(unittest.TestCase):
    def test_random_composition_shares_track_the_preset_weights(self):
        n = 3000
        counts = Counter(profile.compose(random.Random("profile:{}".format(s)), {})["preset"] for s in range(n))
        for name, preset in profile.presets().items():
            within(self, counts[name] / float(n), preset["weight"], n, msg=name)

    def test_members_have_consistent_ids_roles_and_ages(self):
        for s in range(400):
            comp = profile.compose(random.Random("profile:{}".format(s)), {"region": "uk"})
            people = comp["people"]
            adults = [m for m in people if profile.householder(m)]           # adults and elderly householders
            minors = [m for m in people if m["role"] in profile.MINOR_ROLES]
            self.assertEqual([m["id"] for m in adults], ["resident_{}".format(i + 1) for i in range(len(adults))])
            self.assertEqual([m["id"] for m in minors], ["child_{}".format(i + 1) for i in range(len(minors))])
            self.assertEqual([m["age"] for m in minors], sorted((m["age"] for m in minors), reverse=True))
            for m in minors:
                self.assertEqual(m["role"], profile.role_of_age(m["age"]))
                self.assertTrue(m["age"] > 0, "an infant's age is in months, never 0 (people.common.age_of)")
                self.assertTrue(m["label"].endswith("({})".format(profile.LABEL_SUFFIX[m["role"]])))
            if minors and comp["preset"] != "multigenerational":
                self.assertGreaterEqual(adults[0]["age"], int(minors[0]["age"]) + 22)
            for m in people:
                if m["role"] == "elderly":
                    self.assertGreaterEqual(m["age"], 70)
                    self.assertEqual(set(m["abilities"]), {"mobility", "vision", "hearing"})
                if m["role"] not in profile.MINOR_ROLES:
                    self.assertEqual(m["role"] == "elderly", m["age"] >= 70, m)       # 70 and over is elderly
                if m["role"] == "elderly" and m["relation"] == "grandparent":
                    self.assertTrue(m["id"].startswith("elder_"))
            self.assertEqual(len({m["label"].split(" (")[0] for m in people}), len(people), "names repeat")
            self.assertGreaterEqual(comp["bedrooms"], 1)

    def test_minimal_none_named_and_listed_households(self):
        rng = random.Random("profile:7")
        minimal = profile.compose(rng, {"residents": "minimal", "pets": "none"})
        self.assertEqual([(m["id"], m["role"]) for m in minimal["people"]], [("resident_1", "adult")])
        self.assertTrue(28 <= minimal["people"][0]["age"] <= 60)
        self.assertEqual((minimal["pets"], minimal["pets_pending"]), ([], []))
        self.assertEqual(profile.compose(rng, {"residents": "none", "pets": "none"})["people"], [])
        infant = profile.compose(rng, {"residents": {"composition": "couple_with_infant"}})
        self.assertEqual(sorted(m["role"] for m in infant["people"]), ["adult", "adult", "infant"])
        listed = [{"id": "resident_1", "label": "Sam", "role": "adult", "age": 34}, {"id": "child_1", "role": "toddler", "age": 2}]
        kept = profile.compose(rng, {"residents": listed})
        self.assertEqual(kept["people"], listed)
        self.assertEqual(kept["preset"], "list")
        with self.assertRaises(ValueError):
            profile.compose(rng, {"residents": {"composition": "commune"}})
        with self.assertRaises(ValueError):
            profile.compose(rng, {"residents": "lots"})

    def test_residents_from_70_are_elderly_with_abilities_by_age(self):
        """Review finding: residents 65+ outside multigenerational homes were role 'adult' with no abilities (an
        85-year-old counted as a 23 kg / 200 N helper, no heat vulnerability or fall risk), and elderly mobility was
        drawn uniformly (a walker for 1 in 3; NHATS: about 1 in 9 people 65+ use one, about half of those 85+ use a
        cane or walker)."""
        n, members = 4000, []
        for s in range(n):
            comp = profile.compose(random.Random("profile:{}".format(s)), {"region": "us"})
            members.extend(m for m in comp["people"] if m["role"] not in profile.MINOR_ROLES)
        self.assertFalse([m for m in members if m["role"] == "adult" and m["age"] >= 70])
        elderly = [m for m in members if m["role"] == "elderly"]
        self.assertGreater(len([m for m in elderly if m["relation"] != "grandparent"]), 300)   # householders too
        bands = profile.households()["roles"]["elderly_abilities"]["bands"]
        for i, band in enumerate(bands):
            hi = bands[i + 1]["from"] if i + 1 < len(bands) else 200
            group = [m for m in elderly if band["from"] <= m["age"] < hi]
            if len(group) < 100:
                continue
            for key in ("mobility", "vision", "hearing"):
                for value, share in band[key].items():
                    observed = sum(m["abilities"][key] == value for m in group) / float(len(group))
                    within(self, observed, share, len(group), msg="{} {} {}".format(band["from"], key, value))
        walkers = sum(m["abilities"]["mobility"] == "walker" for m in elderly) / float(len(elderly))
        self.assertLess(walkers, 0.2)

    def test_an_elderly_householder_keeps_the_house(self):
        single = profile.compose(random.Random("profile:3"), {"residents": {"composition": "single_senior"}})
        old = [m for m in [profile.compose(random.Random("senior:{}".format(s)),
                                           {"residents": {"composition": "single_senior"}})["people"][0]
                           for s in range(60)] if m["age"] >= 80][0]
        self.assertEqual((old["id"], old["role"], old["relation"]), ("resident_1", "elderly", "self"))
        rooms = _rooms("master_bedroom", "bedroom", "living_room")
        people = profile.people_records(random.Random("h"), {"preset": "single_senior", "people": [old]}, rooms,
                                        "living_room_1")
        self.assertEqual(people["resident_1"]["authority"], 2)                  # the homeowner, not a dependant
        self.assertEqual(profile.weekly_pattern(random.Random("w"), old), "retired")
        couple = [{"id": "resident_1", "role": "elderly", "age": 82, "relation": "self"},
                  {"id": "resident_2", "role": "elderly", "age": 79, "relation": "partner"}]
        self.assertEqual(profile.bedrooms_needed(couple, "partners"), 1)          # an older couple shares
        homes, sharing, overflow = profile.assign_bedrooms({"relation": "partners", "people": couple},
                                                           {m["id"]: dict(m) for m in couple}, rooms, "living_room_1")
        self.assertEqual((homes, overflow), ({"resident_1": "master_bedroom_1", "resident_2": "master_bedroom_1"}, []))
        self.assertTrue(single["people"])

    def test_a_fixed_bedroom_count_limits_the_household(self):
        for s in range(300):
            comp = profile.compose(random.Random("profile:{}".format(s)), {"bedrooms": 1})
            self.assertLessEqual(profile.bedrooms_needed(comp["people"], comp["relation"], share=True), 1, comp["preset"])
            self.assertEqual(comp["bedrooms"], 1)
        with self.assertRaises(ValueError):
            profile.compose(random.Random("x"), {"residents": {"composition": "roommates"}, "bedrooms": 1})

    def test_bedrooms_follow_the_people_domain_rule_and_sharing(self):
        a, c, e, i = {"role": "adult"}, {"role": "child"}, {"role": "elderly"}, {"role": "infant"}
        self.assertEqual(profile.bedrooms_needed([a]), 1)
        self.assertEqual(profile.bedrooms_needed([a, a, c, c, e]), 4)
        self.assertEqual(profile.bedrooms_needed([a, a, a], "roommates"), 3)
        self.assertEqual(profile.bedrooms_needed([a, a, c, c, c, i], share=True), 3)        # 3 children in 2 rooms
        self.assertEqual(profile.bedrooms_needed([e, c]), 2)                                # no adult: first in the primary
        big = [{"id": "resident_1", "role": "adult"}, {"id": "resident_2", "role": "adult"}] + \
            [{"id": "child_{}".format(k), "role": "child", "age": 6 + k} for k in range(1, 6)]
        comp = profile.compose(random.Random("x"), {"residents": big})
        self.assertLessEqual(comp["bedrooms"], profile.MAX_BEDROOMS)

    def test_pet_shares_follow_the_region(self):
        n = 2000
        for region, dog in (("us", 0.38), ("jp", 0.09)):
            dogs = sum(any(p["species"] == "dog" for p in profile.compose(random.Random("p{}:{}".format(region, s)), {"region": region})["pets"])
                       for s in range(n))
            within(self, dogs / float(n), dog, n, msg="dog households " + region)

    def test_caged_pets_wait_for_the_pets_domain(self):
        species = Counter()
        for s in range(1500):
            comp = profile.compose(random.Random("profile:{}".format(s)), {"region": "us"})
            self.assertTrue(all(p["species"] in ("dog", "cat") for p in comp["pets"]))
            for p in comp["pets_pending"]:
                self.assertIn(p["habitat"], ("aquarium", "bird_cage", "small_animal_cage", "terrarium", "chicken_coop"))
                species[p["species"]] += 1
        self.assertTrue({"fish", "bird", "reptile", "chicken"} <= set(species))
        self.assertTrue(set(species) & {"hamster", "guinea_pig", "rabbit"})

    def test_composition_is_deterministic(self):
        for s in (0, 5, 9):
            self.assertEqual(profile.compose(random.Random("profile:{}".format(s)), {"region": "eu"}),
                             profile.compose(random.Random("profile:{}".format(s)), {"region": "eu"}))


# ----------------------------------------------------------------------------------------------------------------
# profiles, bedrooms, schedules


def _rooms(*types):
    rooms, counts = {}, Counter()
    for t in types:
        counts[t] += 1
        rooms["{}_{}".format(t, counts[t])] = {"type": t, "outdoor": False, "size_m": [4.0, 4.0]}
    return rooms


class Profiles(unittest.TestCase):
    def test_profile_fields_and_vocabularies(self):
        data = profile.households()["profile"]
        rng = random.Random("p")
        for role, age in (("adult", 40), ("elderly", 80), ("teen", 15), ("child", 8), ("toddler", 2)):
            prof = profile.profile_person(rng, {"role": role, "age": age}, "uk")
            self.assertTrue(0.0 <= prof["tidiness"] <= 1.0)
            self.assertIn(prof["chronotype"], ("early", "intermediate", "late"))
            self.assertIn(prof["diet"], [k for k in data["diet"] if k != "doc"])
            self.assertTrue(set(prof["allergies"]) <= set(data["allergies"]["adult"]))
            self.assertEqual(set(prof["fold_style"]), {"top", "towel", "sock", "bottom"})
            self.assertIn(prof["bed_style"], ("spread", "folded_foot"))
            self.assertIn(prof["weekly_pattern"], ("office", "remote", "shift", "home", "retired", "school"))
            if role in profile.MINOR_ROLES:
                self.assertIsNone(prof["drinks"]["hot"])

    def test_children_mostly_share_the_household_diet(self):
        rng = random.Random("diet")
        same = sum(profile.profile_person(rng, {"role": "child", "age": 8}, "us", "vegan")["diet"] == "vegan" for _ in range(2000))
        within(self, same / 2000.0, 0.9 + 0.1 * 0.03, 2000, msg="child diet")

    def test_weekly_patterns_by_age(self):
        rng = random.Random("w")
        self.assertEqual(profile.weekly_pattern(rng, {"role": "child", "age": 8}), "school")
        self.assertEqual(profile.weekly_pattern(rng, {"role": "toddler", "age": 3}), "home")
        self.assertEqual(profile.weekly_pattern(rng, {"role": "elderly", "age": 85}), "retired")
        retired = sum(profile.weekly_pattern(rng, {"role": "adult", "age": 70}) == "retired" for _ in range(2000)) / 2000.0
        within(self, retired, 0.82, 2000, msg="retired at 70")

    def test_schedule_blocks_use_the_people_domain_format(self):
        rng = random.Random("s")
        person = {"role": "adult", "age": 40}
        prof = dict(profile.profile_person(rng, person, "us"), weekly_pattern="office", chronotype="early")
        blocks = profile.schedule(rng, person, prof, "master_bedroom_1", "office_1")
        self.assertEqual(blocks[0]["activity"], "sleeping")
        self.assertEqual(blocks[0]["room"], "master_bedroom_1")
        self.assertEqual(blocks[1], {"from": "08:30", "to": "17:30", "activity": "away", "days": profile.WEEKDAYS, "what": "work"})
        toddler = profile.schedule(rng, {"role": "toddler", "age": 2}, dict(prof, weekly_pattern="home"), "bedroom_1", None)
        self.assertEqual([b["activity"] for b in toddler], ["sleeping", "napping"])
        self.assertEqual((toddler[0]["from"], toddler[0]["to"]), ("19:30", "07:00"))

    def test_asleep_at_handles_midnight_days_and_naps(self):
        blocks = [{"from": "23:00", "to": "07:00", "activity": "sleeping"}, {"from": "13:00", "to": "15:00", "activity": "napping"},
                  {"from": "08:30", "to": "17:30", "activity": "away", "days": profile.WEEKDAYS}]
        at = lambda text: profile.asleep_at(blocks, datetime.datetime.fromisoformat(text))     # noqa: E731
        self.assertTrue(at("2026-10-09T23:30:00"))
        self.assertTrue(at("2026-10-10T03:00:00"))
        self.assertFalse(at("2026-10-10T07:00:00"))
        self.assertTrue(at("2026-10-10T14:00:00"))
        self.assertFalse(at("2026-10-09T10:00:00"))
        weekdays = [{"from": "22:00", "to": "06:00", "activity": "sleeping", "days": ["Fri"]}]
        self.assertTrue(profile.asleep_at(weekdays, datetime.datetime(2026, 10, 10, 2, 0)))     # Friday night's block
        self.assertFalse(profile.asleep_at(weekdays, datetime.datetime(2026, 10, 11, 2, 0)))

    def test_bedroom_assignment_matches_the_people_domain_when_rooms_suffice(self):
        load_domains()
        from house_sim.systems.people.furnish import bedroom_owners
        rooms = _rooms("master_bedroom", "bedroom", "bedroom", "bedroom", "living_room")
        people = {"resident_1": {"role": "adult"}, "resident_2": {"role": "adult"}, "child_1": {"role": "teen", "age": 15},
                  "child_2": {"role": "child", "age": 9}, "elder_1": {"role": "elderly", "age": 80}}
        homes, sharing, overflow = profile.assign_bedrooms({"relation": "partners"}, people, rooms, "living_room_1")
        theirs = {pid: rid for rid, pids in bedroom_owners({"people": people, "rooms": rooms}).items() for pid in pids}
        self.assertEqual(homes, theirs)
        self.assertEqual((sharing, overflow), ([], []))

    def test_roommates_sleep_apart_and_minors_share_when_rooms_run_out(self):
        rooms = _rooms("master_bedroom", "bedroom", "living_room")
        mates = {"resident_1": {"role": "adult"}, "resident_2": {"role": "adult"}}
        homes, _, _ = profile.assign_bedrooms({"relation": "roommates"}, mates, rooms, "living_room_1")
        self.assertEqual(homes, {"resident_1": "master_bedroom_1", "resident_2": "bedroom_1"})
        family = {"resident_1": {"role": "adult"}, "child_1": {"role": "teen", "age": 14}, "child_2": {"role": "child", "age": 12},
                  "child_3": {"role": "infant", "age": 0.5}}
        homes, sharing, overflow = profile.assign_bedrooms({"relation": "self"}, family, rooms, "living_room_1")
        self.assertEqual(homes["child_1"], homes["child_2"])
        self.assertEqual(homes["child_3"], "master_bedroom_1")
        self.assertEqual((sharing, overflow), ([["child_1", "child_2"]], []))
        nursery = _rooms("master_bedroom", "nursery", "living_room")
        homes, _, _ = profile.assign_bedrooms({}, {"resident_1": {"role": "adult"}, "child_1": {"role": "infant", "age": 0.3}}, nursery, "living_room_1")
        self.assertEqual(homes["child_1"], "nursery_1")


# ----------------------------------------------------------------------------------------------------------------
# weather and clock


class Climate(unittest.TestCase):
    def test_normals_match_the_published_anchors(self):
        # sweep 13.1 anchors: London January about 8/2 C and July about 23/14 C; Toronto January about -1/-8 C;
        # Tokyo August about 31/24 C
        for region, month, high, low in (("uk", 1, 8, 2), ("uk", 7, 23, 14), ("ca", 1, -1, -8), ("jp", 8, 31, 24)):
            row = climate.normals()["regions"][region]
            self.assertAlmostEqual(row["high_c"][month - 1], high, delta=1.0)
            self.assertAlmostEqual(row["low_c"][month - 1], low, delta=1.0)
        self.assertEqual({r: climate.latitude(r) for r in climate.regions()},
                         {"us": 40.0, "uk": 51.5, "eu": 50.0, "jp": 35.7, "ca": 43.7})
        self.assertEqual(sorted(climate.regions()), sorted(generator.REGIONS))

    def test_long_run_statistics_track_the_normals(self):
        days = [climate.day_number(datetime.date(y, 7, d)) for y in range(2001, 2031) for d in range(10, 21)]
        for region in ("uk", "ca"):
            rows = [climate.day_weather("weather:3", region, d) for d in days]
            high = sum(r["high_c"] for r in rows) / len(rows)
            self.assertAlmostEqual(high, climate.normal_value(region, "high_c", days[5]), delta=1.0)
            wet = sum(r["wet"] for r in rows) / float(len(rows))
            within(self, wet, climate.normal_value(region, "wet_days", days[5]), len(rows) // 3, msg="wet days " + region)
            wind = sum(r["wind_mps"] for r in rows) / len(rows)
            factor = 1.0 / (1.0 + 0.3 / math.pi)        # day_weather's wind is the daily base, before the afternoon boost
            self.assertAlmostEqual(wind, climate.normal_value(region, "wind_mps", days[5]) * factor, delta=0.25)

    def test_monthly_normals_are_reproduced_by_the_interpolated_curve(self):
        """Review finding: linear interpolation between the monthly normals damped the annual cycle (Philadelphia's
        January ran 0.6 C warm). The knots are solved so each month's average of the curve is its normal."""
        for region in climate.regions():
            row = climate.normals()["regions"][region]
            for field in ("high_c", "low_c", "wet_days", "precip_mm", "wind_mps", "sunshine_frac", "temp_sd_c"):
                for m in range(12):
                    days = [climate.day_number(datetime.date(2001, m + 1, dd)) for dd in range(1, climate.MONTH_DAYS[m] + 1)]
                    mean = sum(climate.normal_value(region, field, d) for d in days) / len(days)
                    target = row[field][m] / (climate.MONTH_DAYS[m] if field in ("wet_days", "precip_mm") else 1.0)
                    self.assertAlmostEqual(mean, target, delta=0.01 * max(1.0, abs(target)), msg=(region, field, m))
        jan = climate.normal_value("us", "high_c", climate.day_number(datetime.date(2001, 1, 16)))
        self.assertLess(jan, 5.2)                                  # the curve dips below January's mean at mid-month

    def test_wet_days_carry_their_offset_and_dry_days_the_opposite_share(self):
        key = climate.seed_key(9)
        seen = set()
        for d in range(climate.day_number(datetime.date(2026, 1, 1)), climate.day_number(datetime.date(2026, 2, 1))):
            dw = climate.day_weather(key, "us", d)
            f = climate.normal_value("us", "wet_days", d)
            offset = climate.normal_value("us", "wet_day_offset_c", d)
            shift = offset if dw["wet"] else -offset * f / (1.0 - f)
            self.assertAlmostEqual(dw["high_c"], climate.normal_value("us", "high_c", d) + dw["anomaly_c"] + shift,
                                   places=9)
            seen.add(dw["wet"])
        self.assertEqual(seen, {True, False})
        self.assertEqual(climate.normals()["regions"]["uk"]["wet_day_offset_c"], [0.0] * 12)

    def test_snow_days_follow_each_regions_normal_under_its_own_definition(self):
        """Fix-pass note: snow days differed from the normals by up to 2x. With snow at or below 1.0 C (the 50 %
        rain-snow air temperature over land) and the calibrated wet-day offsets, December-March snow days follow the
        normals counted by each source's rule: NOAA >= 0.1 in of snowfall (0.25 mm of water), ECCC >= 0.2 cm, DWD and
        JMA any snow; London stays under the Met Office count, which includes sleet and flurries on days below 1 mm."""
        rules = {"us": 0.254, "ca": 0.2, "eu": None, "jp": None, "uk": None}
        bands = {"us": (0.9, 1.25), "ca": (0.9, 1.15), "eu": (0.85, 1.1), "jp": (0.75, 1.1), "uk": (0.6, 0.9)}
        thr = climate._const("snow_at_or_below_c")
        self.assertEqual(thr, 1.0)
        for region, rule in rules.items():
            total = 0
            for s in range(20):
                key = climate.seed_key("snow-check:{}".format(s))
                for y in range(2003, 2013):
                    for m in (11, 0, 1, 2):
                        for dd in range(1, climate.MONTH_DAYS[m] + 1):
                            d = climate.day_number(datetime.date(y, m + 1, dd))
                            if not (climate.day_weather(key, region, d)["wet"] or
                                    climate.day_weather(key, region, d - 1)["wet"]):
                                continue                                   # no rain can fall on this day
                            hours = [climate.precipitation_mm_h(key, region, d, h + 0.5) for h in range(24)]
                            snow = [r for h, r in enumerate(hours)
                                    if r >= 0.05 and climate.temperature_c(key, region, d, h + 0.5) <= thr]
                            total += bool(snow) and (rule is None or sum(snow) >= rule)
            row = climate.normals()["regions"][region]["snow_days"]
            ratio = total / 200.0 / (row[11] + row[0] + row[1] + row[2])
            lo, hi = bands[region]
            self.assertTrue(lo <= ratio <= hi, "{}: {:.2f} x the normal".format(region, ratio))

    def test_daily_anomalies_are_autocorrelated(self):
        series = [climate.day_weather("weather:11", "us", d)["anomaly_c"] for d in range(7000, 10000)]
        mean = sum(series) / len(series)
        var = sum((x - mean) ** 2 for x in series) / len(series)
        lag1 = sum((a - mean) * (b - mean) for a, b in zip(series, series[1:])) / (len(series) - 1) / var
        self.assertAlmostEqual(lag1, 0.7, delta=0.06)

    def test_diurnal_curve_peaks_at_three_and_bottoms_at_sunrise(self):
        d = climate.day_number(datetime.date(2026, 4, 20))
        today = climate.day_weather("weather:2", "eu", d)
        self.assertAlmostEqual(climate.temperature_c("weather:2", "eu", d, 15.0), today["high_c"], places=9)
        self.assertAlmostEqual(climate.temperature_c("weather:2", "eu", d, today["sunrise_h"]), today["low_c"], places=9)
        hours = [climate.temperature_c("weather:2", "eu", d, h / 4.0) for h in range(96)]
        self.assertEqual(max(range(96), key=lambda i: hours[i]), 60)
        self.assertLessEqual(max(hours), today["high_c"] + 1e-9)

    def test_weather_is_a_pure_function_of_key_region_and_time(self):
        when = datetime.datetime(2027, 2, 3, 17, 30)
        first = climate.weather_at("weather:5", "ca", when)
        for cached in (climate.day_weather, climate._wet, climate._z_temp):
            cached.cache_clear()
        climate.weather_at("weather:5", "ca", when + datetime.timedelta(days=3))
        self.assertEqual(climate.weather_at("weather:5", "ca", when.isoformat()), first)
        self.assertNotEqual(climate.weather_at("weather:6", "ca", when), first)

    def test_conditions_rain_snow_and_storms(self):
        seen = Counter()
        for d in range(9000, 9400):
            for h in (3, 9, 15, 21):
                w = climate.weather_at("weather:1", "ca", datetime.datetime.combine(climate._date_of(d), datetime.time(h)))
                seen[w["condition"]] += 1
                self.assertIn(w["condition"], climate.CONDITIONS)
                self.assertTrue(w["rain_mm_h"] == 0 or w["snow_cm_h"] == 0)
                if w["condition"] == "snow":
                    self.assertLessEqual(w["temperature_c"], 1.0)       # the 50 % rain-snow temperature
                    self.assertGreater(w["snow_cm_h"], 0)
                if w["condition"] in ("rain", "storm"):
                    self.assertEqual(w["condition"] == "storm", w["rain_mm_h"] >= 15.0)
                self.assertAlmostEqual(w["gust_mps"], round(w["wind_mps"] * 1.5, 1), delta=0.11)
        self.assertTrue({"clear", "cloudy", "rain", "snow"} <= set(seen))

    def test_day_length_and_season(self):
        solstice = climate.day_number(datetime.date(2026, 12, 21))
        self.assertAlmostEqual(climate.day_length_h(51.5, solstice), 7.6, delta=0.05)   # London, geometric formula
        self.assertAlmostEqual(climate.day_length_h(0.0, solstice), 12.0, delta=1e-9)
        self.assertEqual(climate.season("2026-01-15T10:00:00"), "winter")
        self.assertEqual(climate.season("2026-07-15T10:00:00"), "summer")
        self.assertEqual(climate.season("2026-07-15T10:00:00", "south"), "winter")

    def test_apply_sets_weather_clock_and_outdoor_rooms(self):
        config = {"clock": {"start": "2026-01-15T14:00:00", "hemisphere": "north"},
                  "rooms": {"yard": {"outdoor": True, "temperature_c": 21.0}, "hall": {"outdoor": False, "temperature_c": 21.0}}}
        season = climate.apply(config, 4, "ca", "2026-01-15T14:00:00")
        self.assertEqual(season, "winter")
        self.assertEqual(config["clock"], {"start": "2026-01-15T14:00:00", "hemisphere": "north", "model": 2, "region": "ca",
                                           "latitude": 43.7})
        key = climate.seed_key(4)
        expected = climate.weather_at(key, "ca", "2026-01-15T14:00:00")
        self.assertEqual({k: v for k, v in config["weather"].items() if k != "process"}, expected)
        self.assertEqual(config["weather"]["process"], {"model": "normals_v1", "region": "ca", "seed_key": key})
        self.assertRegex(key, r"^wx-[0-9a-f]{16}$", "an opaque key: state.weather is observed whole, the seed must not show")
        self.assertNotEqual(climate.seed_key(5), key)
        self.assertEqual(config["rooms"]["yard"]["temperature_c"], expected["temperature_c"])
        self.assertEqual(config["rooms"]["hall"]["temperature_c"], 21.0)
        with self.assertRaises(ValueError):
            climate.weather_at("weather:4", "au", "2026-01-15T14:00:00")


# ----------------------------------------------------------------------------------------------------------------
# belongings decisions (no house needed)


class _Decisions(belongings._Run):
    """A belongings run whose placements always succeed without touching the house: isolates the decisions."""

    def place_at(self, spot, type_name, garment=False, **props):
        self.placed.append((spot, type_name))
        return "fake_{}".format(len(self.placed))

    def place_item(self, pid, *args, **kwargs):
        owned = {k: list(v) for k, v in self.owned.items()}
        try:
            return super(_Decisions, self).place_item(pid, *args, **kwargs)
        finally:
            self.owned = owned          # fake ids never join the owner's things


class BelongingDecisions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.config, _ = generated(4, residents={"composition": "couple"}, pets="none")
        cls.registry = default_registry()

    def run_for(self, seed, **opts):
        run = _Decisions(self.config, random.Random(seed), dict({"season": "autumn", "gen": 2}, **opts), self.registry)
        run.placed = []
        return run

    def test_displacement_rate_tracks_tidiness(self):
        self.assertAlmostEqual(belongings.displacement_probability(1.0), 0.05)
        self.assertAlmostEqual(belongings.displacement_probability(0.0), 0.40)
        homes = ["entry.key_bowl", "entry.bench", "own.dresser", "own.nightstand", "kitchen.counter"]
        alts = [["kitchen.counter", 3], ["own.coat_pocket", 2], ["own.bag", 2], ["living.sofa", 1]]
        n = 1500
        for tidiness in (0.0, 0.5, 1.0):
            run = self.run_for("t{}".format(tidiness))
            home = next(s for s in (run.resolve(h, "resident_1") for h in homes) if s)
            away = 0
            for _ in range(n):
                run.placed = []
                _, chosen = run.place_item("resident_1", "wallet", "wallet", homes, alts, tidiness)
                away += run.placed[0][0] != home
                self.assertEqual(chosen, home)
            within(self, away / float(n), belongings.displacement_probability(tidiness), n, msg="tidiness {}".format(tidiness))

    def test_role_probabilities(self):
        run = self.run_for("roles")
        person = {"role": "adult", "age": 40, "profile": {"weekly_pattern": "office"}}
        items = {item["kind"]: item for item in belongings.data()["items"]}
        n = 3000
        for kind, expected in (("phone", 0.97), ("keys", 0.95), ("wallet", 0.9), ("tablet", 0.4), ("smartwatch", 0.25)):
            got = sum(run.wants(items[kind], person, {}) for _ in range(n)) / float(n)
            within(self, got, expected, n, msg=kind)
        self.assertFalse(run.wants(items["handbag"], person, {}), "office workers carry a work bag")
        self.assertFalse(run.wants(items["backpack"], {"role": "child", "age": 4}, {}), "backpacks from age 5")
        self.assertFalse(run.wants(items["charger"], person, {}), "no phone, no charger")

    def test_seasonal_outerwear_and_shoes(self):
        items = {item["kind"]: item for item in belongings.data()["items"]}
        adult = {"role": "adult", "age": 40}
        winter, summer = self.run_for("w", season="winter"), self.run_for("s", season="summer")
        coats = Counter(winter.types(items["outerwear"], adult)[0] for _ in range(400))
        self.assertEqual(set(coats), WINTER)
        within(self, coats["coat"] / 400.0, 0.6, 400, msg="winter coats")
        self.assertEqual({summer.types(items["outerwear"], adult)[0] for _ in range(100)}, {"light_jacket"})
        teen = {winter.types(items["outerwear"], {"role": "teen", "age": 15})[0] for _ in range(200)}
        self.assertEqual(teen, {"hoodie", "light_jacket"})
        boots = Counter(winter.types(items["shoes"], adult)[0] for _ in range(400))
        self.assertTrue(set(boots) <= {"boot", "snow_boot", "sneaker"} and "snow_boot" in boots)

    def test_phone_homes_follow_the_time_of_day(self):
        items = {item["kind"]: item for item in belongings.data()["items"]}
        run = self.run_for("h")
        adult = {"role": "adult", "age": 40}
        self.assertEqual(run.homes(items["phone"], adult, "night")[0], "own.nightstand")
        self.assertEqual(run.homes(items["phone"], adult, "day")[0], "kitchen.counter")
        self.assertEqual(run.homes(items["phone"], {"role": "teen", "age": 15}, "day")[0], "own.desk")
        self.assertEqual(run.homes(items["phone"], {"role": "elderly", "age": 80}, "day")[0], "living.side_table")
        run.shoes_off = True
        self.assertEqual(run.homes(items["shoes"], adult, "day")[0], "entry.shoe_rack")


# ----------------------------------------------------------------------------------------------------------------
# whole houses


class Houses(unittest.TestCase):
    CASES = {"s0": (0, {}), "s1": (1, {}), "s2": (2, {}), "s5": (5, {}), "s8": (8, {}),
             "infant": (3, {"residents": {"composition": "couple_with_infant"}}),
             "toddler": (6, {"residents": {"composition": "couple_with_toddler"}}),
             "winter_night": (7, {"start": "2026-01-14T03:00:00", "region": "ca"}),
             "summer": (7, {"start": "2026-07-15T13:00:00", "region": "ca"}),
             "minimal": (9, {"residents": "minimal", "pets": "none"})}

    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.houses = {name: generated(seed, **opts) for name, (seed, opts) in cls.CASES.items()}

    def test_houses_are_marked_and_profiled(self):
        for name, (config, _) in self.houses.items():
            prof = config["household"]["profile"]
            self.assertEqual((config["metadata"]["gen"], prof["gen"]), (2, 2), name)
            self.assertEqual(set(prof["members"]), set(config["people"]), name)
            self.assertIn(prof["smart_home"], ("none", "basic", "connected"))
            self.assertEqual(prof["season"], clock.season({"clock": config["clock"], "time_s": 0}))
            for pid, person in config["people"].items():
                self.assertIn(person["home_room"], config["rooms"])
                self.assertEqual(set(person["profile"]) >= {"tidiness", "chronotype", "diet", "weekly_pattern", "relation"}, True)
                self.assertEqual(person["preferences"]["diet"], person["profile"]["diet"])
                expected = 2 if profile.householder(person, person["profile"]["relation"]) else \
                    {"adult": 2, "elderly": 1}.get(person["role"], 0)
                self.assertEqual(person["authority"], expected)

    def test_belongings_have_owners_homes_habits_and_storage(self):
        for name, (config, _) in self.houses.items():
            ents = config["entities"]
            for pid, person in config["people"].items():
                storage = person["preferences"]["storage"]
                for eid, home in storage.items():
                    rec = ents[eid]
                    self.assertEqual((rec["owner"], rec["home"]), (pid, home), (name, eid))
                    self.assertTrue(home in ents or home in config["rooms"], (name, eid, home))
                for kind, habit in person.get("habits", {}).items():
                    self.assertTrue(habit["home"] in ents or habit["home"] in config["rooms"], (name, kind))
                    for alt in habit["alt"]:
                        self.assertTrue((alt[0] in ents or alt[0] in config["rooms"]) and alt[1] > 0)
                        self.assertIn(alt[2:], ([], ["day"], ["night"]))
                    self.assertTrue(set(habit["items"]) <= set(storage), (name, kind))
                phones = [e for e in owned(config, pid) if ents[e]["type"] == "smartphone"]
                if person["role"] in ("adult", "teen", "elderly", "child"):
                    self.assertEqual(person["has_phone"], bool(phones), (name, pid))
                    self.assertEqual(person["reachable_by_phone"], bool(phones))
                for phone in phones:
                    self.assertEqual(ents[phone]["phone_of"], pid)
                    self.assertIn("night", person["habits"]["phone"])

    def test_most_adults_carry_phone_keys_and_wallet(self):
        adults, kinds = 0, Counter()
        for config, _ in self.houses.values():
            for pid, person in config["people"].items():
                if person["role"] == "adult":
                    adults += 1
                    kinds.update({config["entities"][e]["type"] for e in owned(config, pid)})
        self.assertGreater(adults, 12)
        for kind in ("smartphone", "key", "wallet"):
            self.assertGreaterEqual(kinds[kind] / float(adults), 0.75, kind)

    def test_displaced_items_sit_at_one_of_their_habit_alternatives(self):
        moved = 0
        for config, _ in self.houses.values():
            ents = config["entities"]
            for pid, person in config["people"].items():
                for kind, habit in person.get("habits", {}).items():
                    for eid in habit["items"]:
                        home = person["preferences"]["storage"][eid]
                        spot = ents[eid]["location"]
                        if ents.get(spot, {}).get("type") == "garment_pocket":
                            spot = ents[spot]["location"]
                        if spot in (home, habit.get("day"), habit.get("night")):
                            continue
                        moved += 1
                        self.assertIn(spot, [a[0] for a in habit["alt"]], (pid, eid, kind))
        self.assertGreater(moved, 0, "nothing was out of place in ten houses")

    def test_outerwear_follows_the_season(self):
        for name, allowed in (("winter_night", WINTER | {"raincoat", "hoodie"}), ("summer", {"light_jacket", "raincoat", "hoodie"})):
            config, _ = self.houses[name]
            for pid, person in config["people"].items():
                storage = person["preferences"]["storage"]
                coats = [e for e in storage if config["entities"][e]["type"] in belongings.OUTERWEAR]
                for eid in coats:
                    self.assertIn(config["entities"][eid]["type"], allowed, (name, pid))

    def test_infants_sleep_in_a_crib_and_get_a_high_chair(self):
        config, _ = self.houses["infant"]
        ents = config["entities"]
        infant = next(pid for pid, p in config["people"].items() if p["role"] == "infant")
        person = config["people"][infant]
        crib = person["in_bed"]
        self.assertEqual((ents[crib]["type"], ents[crib]["owner"]), ("crib", infant))
        self.assertIn(tuple(person["pos"]), geometry.footprint(ents[crib]))
        self.assertEqual(person["home_room"], ents[crib]["location"])
        self.assertIn(person["posture"], ("lying", "sleeping"))
        self.assertTrue(any(rec["type"] == "high_chair" for rec in ents.values()))

    def test_toddlers_often_get_a_high_chair(self):
        chairs = 0
        for seed in range(6):
            config, _ = generated(seed, residents={"composition": "couple_with_toddler"}, pets="none")
            chairs += any(rec["type"] == "high_chair" for rec in config["entities"].values())
        self.assertTrue(1 <= chairs <= 6)

    def test_sleepers_are_in_bed_at_night_and_awake_by_day(self):
        night, _ = self.houses["winter_night"]
        for pid, person in night["people"].items():
            self.assertFalse(person["awake"], pid)
            self.assertEqual(person["posture"], "sleeping")
            bed = night["entities"][person["in_bed"]]
            self.assertEqual(person["location"], bed["location"])
            self.assertIn(tuple(person["pos"]), geometry.footprint(bed))
        cells = [(p["location"], tuple(p["pos"])) for p in night["people"].values()]
        self.assertEqual(len(cells), len(set(cells)))
        day, _ = self.houses["s0"]
        self.assertTrue(all(p["awake"] and not p.get("in_bed") for p in day["people"].values() if p["role"] != "infant"))

    def test_an_unfurnished_house_has_nobody_asleep_without_a_bed(self):
        config = generator._generate(7, gen=2, furnish=False, start="2026-01-14T03:00:00", region="ca")
        self.assertTrue(config["people"])
        self.assertTrue(all(p.get("awake", True) and not p.get("in_bed") for p in config["people"].values()))
        self.assertEqual(config["household"]["profile"]["gen"], 2)

    def test_exterior_steps_join_the_porch_to_its_yard(self):
        porches = 0
        for name, (config, _) in self.houses.items():
            steps = [c for c in config["connections"].values() if c.get("exterior")]
            porch = [r for r in config["rooms"] if config["rooms"][r]["type"] == "porch"]
            self.assertEqual(len(steps), len(porch), name)
            for conn in steps:
                porches += 1
                self.assertEqual(conn["kind"], "stairs")
                self.assertEqual(sorted(config["rooms"][r]["type"] for r in conn["rooms"]), ["front_yard", "porch"])
                self.assertEqual(conn["upper"], porch[0])
                self.assertTrue(2 <= conn["steps"] <= 4)
                self.assertEqual((conn["rise_m"], conn["run_m"], conn["winders"]), (0.17, 0.28, 0))
                self.assertEqual(conn["handrail"], conn["steps"] >= 4)
                self.assertEqual(len({config["rooms"][r]["floor"] for r in conn["rooms"]}), 1)
            self.assertEqual(self._reachable(config), set(config["rooms"]), name)
        self.assertGreater(porches, 0)

    @staticmethod
    def _reachable(config):
        seen, todo = {config["start_room"]}, [config["start_room"]]
        while todo:
            room = todo.pop()
            for other in config["rooms"][room]["exits"]:
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
        return seen

    def test_weather_and_clock_match_region_and_date(self):
        for name, (config, _) in self.houses.items():
            region = config["metadata"]["region"]
            self.assertEqual((config["clock"]["model"], config["clock"]["region"]), (2, region))
            self.assertEqual(config["clock"]["latitude"], climate.latitude(region))
            weather = dict(config["weather"])
            process = weather.pop("process")
            self.assertEqual(weather, climate.weather_at(process["seed_key"], region, config["clock"]["start"]))
            for room in config["rooms"].values():
                if room["outdoor"]:
                    self.assertEqual(room["temperature_c"], weather["temperature_c"])
        self.assertLess(self.houses["winter_night"][0]["weather"]["temperature_c"], self.houses["summer"][0]["weather"]["temperature_c"])

    def test_common_furnishings_and_chair_sets(self):
        types = Counter()
        for config, _ in self.houses.values():
            types.update(rec["type"] for rec in config["entities"].values())
            for room in [r for r in config["rooms"] if config["rooms"][r]["type"] == "dining_room"]:
                chairs = [rec for rec in config["entities"].values() if rec["type"] == "chair" and rec.get("location") == room
                          and rec.get("label") == "chair"]
                self.assertEqual(len({(c["material"], c.get("color")) for c in chairs}), 1 if chairs else 0)
        for kind in ("coir_doormat", "decorative_pillow", "rain_umbrella", "passport", "tool_scissors"):
            self.assertGreater(types[kind], 0, kind)

    def test_missing_types_are_noted_never_forced(self):
        registry = default_registry()
        for name, (config, report) in self.houses.items():
            problems = [line for line in report if not line.startswith("note gen2 ")]
            self.assertEqual(problems, [], name)
            for kind in ("sofa_cushion", "coaster", "candle", "ottoman", "handbag", "stroller"):
                if not registry.known(kind):
                    self.assertFalse(any(rec["type"] == kind for rec in config["entities"].values()))
        notes = [line for _, report in self.houses.values() for line in report]
        if not registry.known("sofa_cushion"):
            self.assertTrue(any("missing type sofa_cushion" in line for line in notes))

    def test_pending_pets_are_housed_by_the_pets_furnisher_or_noted(self):
        """The generator writes dogs and cats and leaves the caged species in profile.pets_pending; the pets domain's
        generation-2 furnisher (pets.habitats, the adoption round) builds each one's habitat and record, or leaves a
        'note gen2 pets' line naming why it could not. Without that furnisher the houses hold dogs and cats only."""
        habitats = ("aquarium", "bird_cage", "small_animal_cage", "chicken_coop", "terrarium")
        housing = any(name == "pets.habitats" for _, name, _ in FURNISHERS)
        for name, (config, report) in self.houses.items():
            prof = config["household"]["profile"]
            pending = sorted(str(p["id"]) for p in prof.get("pets_pending") or [])
            if not housing:
                self.assertTrue(all(p.get("species", "dog") in ("dog", "cat") for p in config["pets"].values()), name)
                self.assertFalse(any(rec["type"] in habitats for rec in config["entities"].values()), name)
                continue
            housed = prof.get("pets_housed") or []
            skipped = [line for line in report if line.startswith("note gen2 pets ")]
            if pending:
                self.assertTrue(sorted(housed) == pending or skipped, (name, housed, pending))
            caged = {pid: rec for pid, rec in config["pets"].items() if rec.get("species", "dog") not in ("dog", "cat")}
            self.assertTrue(set(caged) <= set(pending), (name, sorted(caged)))
            for pid, rec in config["pets"].items():
                if rec.get("enclosure"):
                    self.assertEqual(config["entities"][rec["enclosure"]].get("occupant"), pid, (name, pid))

    def test_gen2_houses_run_in_both_perception_modes(self):
        """Valid configs (normalize), steps without errors (strict mode under HOUSE_SIM_STRICT=1), and realistic
        observations show none of the never-tier fields generation 2 writes."""
        hidden_entity = ("owner", "home", "phone_of", "in_pocket")
        hidden_person = ("profile", "habits", "preferences", "schedule", "home_room")
        for name in ("s0", "infant", "winter_night"):
            seed, opts = self.CASES[name]
            for perception in ("oracle", "realistic"):
                env = HouseEnv(generate_house(seed, gen=2, **opts), seed=seed, perception=perception)
                env.reset()
                self.assertTrue(env.step("wait", seconds=300)["ok"], (name, perception))
                result = env.step("look")
                self.assertTrue(result["ok"], (name, perception, result["message"]))
                if perception != "realistic":
                    continue
                obs = result["observation"]
                for handle, record in obs["visible_entities"].items():
                    self.assertFalse(set(hidden_entity) & set(record), (name, handle))
                for handle, record in obs["people"].items():
                    self.assertFalse(set(hidden_person) & set(record), (name, handle))

    def test_explicit_resident_lists_are_kept(self):
        listed = [{"id": "resident_1", "label": "Jordan", "role": "adult", "age": 45, "has_phone": False}]
        config, _ = generated(2, residents=listed, pets="none", start="2026-10-09T03:00:00")
        person = config["people"]["resident_1"]
        self.assertEqual((person["label"], person["age"], person["has_phone"]), ("Jordan", 45, False))
        self.assertTrue(person.get("awake", True) and not person.get("in_bed"), "listed residents are never put to bed")
        self.assertFalse(any(rec["type"] == "smartphone" for rec in config["entities"].values()))
        self.assertIn("tidiness", person["profile"])

    def test_generation_two_is_deterministic_across_processes(self):
        code = ("import json, sys; sys.path.insert(0, {!r}); from house_sim.generator import generate_house; "
                "from house_sim.kernel.predicates import digest; "
                "print(json.dumps([digest(generate_house(s, gen=2)) for s in (0, 8)]))").format(str(ROOT))
        env = dict(os.environ, PYTHONHASHSEED="12345")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]), [digest(generate_house(s, gen=2)) for s in (0, 8)])
        self.assertEqual(digest(self.houses["s0"][0]), digest(generate_house(0, gen=2)))
        self.assertNotEqual(digest(generate_house(0, gen=2)), digest(generate_house(0)))

    def test_generation_two_costs_at_most_a_little_more(self):
        """Budget (TIER1_INTEGRATION 3.4): generate_house(gen=2) <= +20 % over generation 1; this test fails at 3x the
        budget (+60 %) so it is not flaky; the gate measures the budget itself (G9)."""
        best = {}
        for gen in (1, 2):
            for _ in range(2):
                start = time.perf_counter()
                for seed in (10, 11, 12):
                    generator._generate(seed, gen=gen)
                spent = time.perf_counter() - start
                best[gen] = min(best.get(gen, spent), spent)
        self.assertLessEqual(best[2] / best[1], 1.6, best)


if __name__ == "__main__":
    unittest.main()
