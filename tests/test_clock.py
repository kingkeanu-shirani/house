"""The kernel clock's calendar side (kernel v3.1, TIER1_SWEEP 11.1 and 21 "TIME-EVAL" item 5): holidays per region,
DST transitions, parse_when, recurrence expansion, latitude day length, date formats, model 1 unchanged."""
import datetime
import unittest

from house_sim.kernel import clock


def state(region="us", start="2026-10-09T07:00:00", time_s=0.0, model=2, latitude=40.0, dst=True, **extra):
    clk = {"start": start, "hemisphere": "north", "region": region}
    if model is not None:
        clk["model"] = model
    if dst:
        clk["dst"] = True           # a clock that follows DST (the flag the adoption round sets for gen-2 houses)
    if latitude is not None:
        clk["latitude"] = latitude
    out = {"clock": clk, "time_s": time_s}
    out.update(extra)
    return out


class HolidayTests(unittest.TestCase):
    def test_us_federal_holidays_and_weekend_observance(self):
        s = state("us")
        self.assertEqual(clock.holiday(s, "2026-11-26"), "Thanksgiving Day")
        self.assertEqual(clock.holiday(s, "2026-01-19"), "Martin Luther King Jr. Day")
        self.assertEqual(clock.holiday(s, "2026-05-25"), "Memorial Day")
        self.assertEqual(clock.holiday(s, "2026-07-04"), "Independence Day")             # a Saturday
        self.assertEqual(clock.holiday(s, "2026-07-03"), "Independence Day (observed)")  # the Friday before
        self.assertEqual(clock.holiday(s, "2021-12-31"), "New Year's Day (observed)")    # 2022-01-01 was a Saturday
        self.assertIsNone(clock.holiday(s, "2020-06-19"))                                # Juneteenth since 2021
        self.assertIsNone(clock.holiday(s, "2026-11-27"))
        self.assertEqual(len(clock.holidays_between(s, "2026-01-01", "2026-12-31")), 12)

    def test_uk_easter_and_substitute_days(self):
        s = state("uk")
        self.assertEqual(clock.holiday(s, "2026-04-06"), "Easter Monday")
        self.assertEqual(clock.holiday(s, "2026-04-03"), "Good Friday")
        self.assertEqual(clock.holiday(s, "2026-12-28"), "Boxing Day (substitute day)")  # 26th is a Saturday
        self.assertEqual(clock.holiday(s, "2021-12-27"), "Christmas Day (substitute day)")
        self.assertEqual(clock.holiday(s, "2021-12-28"), "Boxing Day (substitute day)")
        self.assertEqual(clock.holiday(s, "2026-08-31"), "Summer bank holiday")

    def test_japan_fixed_dates_equinoxes_substitutes_and_citizens_holiday(self):
        s = state("jp")
        for day, name in (("2026-01-01", "New Year's Day"), ("2026-02-11", "National Foundation Day"),
                          ("2026-02-23", "Emperor's Birthday"), ("2026-04-29", "Showa Day"),
                          ("2026-05-03", "Constitution Memorial Day"), ("2026-05-05", "Children's Day"),
                          ("2026-08-11", "Mountain Day"), ("2026-11-03", "Culture Day"),
                          ("2026-11-23", "Labour Thanksgiving Day"), ("2026-03-20", "Vernal Equinox Day"),
                          ("2026-09-23", "Autumnal Equinox Day")):
            self.assertEqual(clock.holiday(s, day), name, day)
        self.assertEqual(clock.holiday(s, "2026-05-06"), "Substitute Holiday (Constitution Memorial Day)")
        self.assertEqual(clock.holiday(s, "2026-09-22"), "Citizens' Holiday")

    def test_germany_and_ontario(self):
        self.assertEqual(clock.holiday(state("eu"), "2026-10-03"), "Day of German Unity")
        self.assertEqual(clock.holiday(state("eu"), "2026-05-14"), "Ascension Day")
        self.assertIsNone(clock.holiday(state("eu"), "2026-10-05"))                       # no substitute days
        self.assertEqual(clock.holiday(state("ca"), "2026-05-18"), "Victoria Day")
        self.assertEqual(clock.holiday(state("ca"), "2026-02-16"), "Family Day")

    def test_region_resolution(self):
        self.assertEqual(clock.region({"clock": {}, "metadata": {"region": "uk"}}), "uk")
        self.assertEqual(clock.region({"clock": {"region": "jp"}, "metadata": {"region": "uk"}}), "jp")
        self.assertEqual(clock.region({"clock": {}}), "us")
        self.assertEqual(clock.region({"clock": {"region": "mars"}}), "us")

    def test_easter_computus(self):
        for year, day in ((2024, "2024-03-31"), (2025, "2025-04-20"), (2026, "2026-04-05"), (2027, "2027-03-28")):
            self.assertEqual(clock.easter(year).isoformat(), day)


class DstTests(unittest.TestCase):
    def at(self, iso, region="us"):
        start = datetime.datetime.fromisoformat("2026-01-01T00:00:00")
        seconds = (datetime.datetime.fromisoformat(iso) - start).total_seconds()
        return state(region, start="2026-01-01T00:00:00", time_s=seconds)

    def test_us_transitions_2026(self):
        self.assertFalse(clock.dst_active(self.at("2026-03-08T01:59:00")))
        self.assertTrue(clock.dst_active(self.at("2026-03-08T02:00:00")))      # 02:00 standard -> 03:00 wall
        self.assertEqual(clock.wall(self.at("2026-03-08T02:00:00")).isoformat(), "2026-03-08T03:00:00")
        self.assertTrue(clock.dst_active(self.at("2026-11-01T00:59:00")))
        self.assertFalse(clock.dst_active(self.at("2026-11-01T01:00:00")))     # 02:00 daylight -> 01:00 wall
        self.assertEqual(clock.wall(self.at("2026-11-01T01:00:00")).isoformat(), "2026-11-01T01:00:00")

    def test_europe_and_japan(self):
        self.assertTrue(clock.dst_active(self.at("2026-03-29T01:00:00", "uk")))
        self.assertFalse(clock.dst_active(self.at("2026-03-29T00:59:00", "uk")))
        self.assertFalse(clock.dst_active(self.at("2026-03-29T01:00:00", "eu")))
        self.assertTrue(clock.dst_active(self.at("2026-03-29T02:00:00", "eu")))
        self.assertFalse(clock.dst_active(self.at("2026-07-01T12:00:00", "jp")))

    def test_model_1_clocks_keep_standard_time(self):
        s = state("us", start="2026-07-01T12:00:00", model=None)
        self.assertFalse(clock.dst_active(s))
        self.assertEqual(clock.wall(s), clock.now(s))

    def test_describe_shows_the_wall_clock_on_model_2(self):
        s = state("us", start="2026-11-26T09:00:00")
        view = clock.describe(s)
        self.assertEqual(view["datetime"], "2026-11-26T09:00")         # standard time in November
        self.assertEqual(view["holiday"], "Thanksgiving Day")
        self.assertFalse(view["dst"])
        summer = clock.describe(state("us", start="2026-07-01T09:00:00"))
        self.assertEqual(summer["datetime"], "2026-07-01T10:00")
        self.assertTrue(summer["dst"])
        self.assertEqual(summer["hour"], 10.0)


class DstFlagTests(unittest.TestCase):
    """Stage-C fix: DST needs "dst": true besides clock model 2 -- the domains keep standard time (people's schedules,
    the house's clock displays), so a generation-2 house shows the time everything in it keeps."""

    def test_a_model_2_clock_without_the_flag_keeps_standard_time(self):
        s = state("us", start="2026-07-01T09:00:00", dst=False)
        self.assertFalse(clock.dst_active(s))
        self.assertEqual(clock.wall(s), clock.now(s))
        self.assertEqual(clock.describe(s)["datetime"], "2026-07-01T09:00")
        self.assertFalse(clock.describe(s)["dst"])
        self.assertEqual(clock.parse_when(s, "10:00"), 3600.0)
        self.assertEqual(clock.parse_when(state("us", start="2026-07-01T09:00:00"), "10:00"), 0.0)   # with DST

    def test_a_generation_2_house_shows_the_time_its_people_keep(self):
        from house_sim.generator import generate_house
        from house_sim.kernel.engine import HouseEnv
        config = generate_house(0, gen=2)
        self.assertEqual(config["clock"].get("model"), 2)
        env = HouseEnv(config, seed=0, trace=False)
        state = env.world.s
        shown = env.observe()["time"]
        self.assertEqual(shown["hour"], round(clock.hour(state), 2))      # what people's schedules read
        self.assertFalse(shown["dst"])
        self.assertEqual(shown["datetime"], clock.now(state).isoformat(timespec="minutes"))


class ModelOneTests(unittest.TestCase):
    def test_describe_and_daylight_are_unchanged(self):
        s = {"clock": {"start": "2026-10-09T07:00:00", "hemisphere": "north"}, "time_s": 3600.0,
             "weather": {"condition": "cloudy"}}
        self.assertEqual(clock.describe(s), {"datetime": "2026-10-09T08:00", "date": "2026-10-09", "weekday": "Friday",
                                             "hour": 8.0, "part_of_day": "morning", "season": "autumn",
                                             "daylight": 0.7})
        import math
        daylight = 12.0 + 3.0 * math.sin(2 * math.pi * (282 - 80) / 365.0)       # the fixed +-3 h curve (day 282)
        self.assertEqual(clock.sun_times(s), (12.5 - daylight / 2.0, 12.5 + daylight / 2.0))


class LatitudeTests(unittest.TestCase):
    def test_london_day_length_at_the_december_solstice(self):
        length = clock.day_length_h(51.5, datetime.date(2026, 12, 21).timetuple().tm_yday)
        self.assertAlmostEqual(length, 7.6, delta=0.05)
        s = state("uk", start="2026-12-21T12:00:00", latitude=51.5)
        rise, set_ = clock.sun_times(s)
        self.assertAlmostEqual(set_ - rise, length, places=6)
        self.assertAlmostEqual((rise + set_) / 2.0, 12.0, places=6)

    def test_polar_night_and_day(self):
        self.assertEqual(clock.day_length_h(80.0, 355), 0.0)
        self.assertEqual(clock.day_length_h(80.0, 172), 24.0)

    def test_matches_the_generators_formula(self):
        from house_sim.generator import climate
        for day in ("2026-01-15", "2026-06-21", "2026-10-09"):
            doy = datetime.date.fromisoformat(day).timetuple().tm_yday
            number = climate.day_number(datetime.date.fromisoformat(day))
            self.assertAlmostEqual(clock.day_length_h(43.7, doy), climate.day_length_h(43.7, number), places=9)


class ParseWhenTests(unittest.TestCase):
    def test_relative_clock_and_absolute_forms(self):
        s = state("us", start="2026-10-09T07:00:00", time_s=3600.0)      # now: 08:00 standard, 09:00 wall
        self.assertEqual(clock.parse_when(s, "+2h"), 3600.0 + 7200)
        self.assertEqual(clock.parse_when(s, "-30m"), 1800.0)
        self.assertEqual(clock.parse_when(s, "+90s"), 3690.0)
        self.assertEqual(clock.parse_when(s, 125), 125.0)
        # wall-clock times (DST on in October): 10:00 wall = 09:00 standard = 7200 s
        self.assertEqual(clock.parse_when(s, "10:00"), 7200.0)
        self.assertEqual(clock.parse_when(s, "08:30"), 7200.0 + 22.5 * 3600)       # tomorrow
        self.assertEqual(clock.parse_when(s, "tomorrow 07:30"), 23.5 * 3600)        # 06:30 standard on the 10th
        self.assertEqual(clock.parse_when(s, "-1d 18:40"), -(13 * 3600 + 20 * 60))  # 17:40 standard on the 8th
        self.assertEqual(clock.parse_when(s, "day+1 07:30"), 23.5 * 3600)
        self.assertEqual(clock.parse_when(s, "2026-10-12T08:15"), 3 * 86400 + 15 * 60)   # 07:15 standard
        with self.assertRaises(ValueError):
            clock.parse_when(s, "whenever")

    def test_model_1_has_no_dst(self):
        s = state("us", start="2026-10-09T07:00:00", model=None)
        self.assertEqual(clock.parse_when(s, "08:00"), 3600.0)

    def test_calendar_helpers(self):
        s = state("us", start="2026-10-09T23:30:00")             # 23:30 standard = 00:30 wall on the 10th
        self.assertEqual(clock.today(s).isoformat(), "2026-10-10")
        self.assertEqual(clock.tomorrow(s).isoformat(), "2026-10-11")
        self.assertEqual(clock.weekday(s), "Saturday")
        self.assertTrue(clock.is_weekend(s))
        self.assertFalse(clock.is_weekend(s, "2026-10-12"))


class RecurrenceTests(unittest.TestCase):
    def test_weekly_school_days_until_with_exceptions(self):
        event = {"start": "2026-10-12T08:15", "end": "2026-10-12T15:00",
                 "recurrence": {"freq": "weekly", "byday": ["MO", "TU", "WE", "TH", "FR"], "until": "2026-10-23",
                                "except": ["2026-10-14"]}}
        days = [o["start"][:10] for o in clock.occurrences(event, "2026-10-01", "2026-12-31")]
        self.assertEqual(days, ["2026-10-12", "2026-10-13", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20",
                                "2026-10-21", "2026-10-22", "2026-10-23"])
        self.assertEqual(clock.occurrences(event, "2026-10-13", "2026-10-13")[0]["end"], "2026-10-13T15:00")

    def test_interval_count_daily_and_monthly(self):
        biweekly = {"start": "2026-10-10T09:00", "recurrence": {"freq": "weekly", "interval": 2, "count": 3}}
        self.assertEqual([o["start"][:10] for o in clock.occurrences(biweekly, "2026-10-01", "2027-12-31")],
                         ["2026-10-10", "2026-10-24", "2026-11-07"])
        daily = {"start": "2026-10-10T20:00", "recurrence": {"freq": "daily", "interval": 3}}
        self.assertEqual(len(clock.occurrences(daily, "2026-10-10", "2026-10-31")), 8)
        monthly = {"start": "2026-01-31T10:00", "recurrence": {"freq": "monthly", "bymonthday": [31], "count": 4}}
        self.assertEqual([o["start"][:10] for o in clock.occurrences(monthly, "2026-01-01", "2026-12-31")],
                         ["2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31"])
        once = {"start": "2026-10-10T09:00"}
        self.assertEqual(len(clock.occurrences(once, "2026-10-10", "2026-10-10")), 1)
        self.assertEqual(clock.occurrences(once, "2026-10-11", "2026-10-12"), [])


class FormatTests(unittest.TestCase):
    def test_locale_orders(self):
        day = "2026-10-09"
        self.assertEqual(clock.format_date(state("us"), day), "10/09/2026")
        self.assertEqual(clock.format_date(state("uk"), day), "09/10/2026")
        self.assertEqual(clock.format_date(state("eu"), day), "09.10.2026")
        self.assertEqual(clock.format_date(state("jp"), day), "2026/10/09")
        self.assertEqual(clock.format_date(state("us"), day, "long"), "October 9, 2026")
        self.assertEqual(clock.format_date(state("uk"), day, "long"), "9 October 2026")
        self.assertEqual(clock.format_date(state("us"), day, "iso"), "2026-10-09")
        self.assertEqual(clock.format_date(state("us"), day, "DMY"), "09/10/2026")
        with self.assertRaises(ValueError):
            clock.format_date(state("us"), day, "fancy")


if __name__ == "__main__":
    unittest.main()
