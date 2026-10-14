"""Generation 2: household composition, resident records, bedrooms, profiles and schedules (docs/design/TIER1_SWEEP.md
sections 12, 14.2 and 14.3; data in data/generator/households.json).

    composition = compose(Random("profile:{seed}"), opts)      before the structure: it fixes the bedrooms needed
    people = people_records(house_rng, composition, rooms, start_room)
    pets = pet_records(house_rng, composition, rooms)
    apply_profiles(Random("profile:{seed}:people"), config, composition, start, season, region)

Residents keep the generation-1 id scheme (resident_N householders, child_N minors oldest first, elder_N
grandparents) and the people domain's roles (adult, elderly, teen, child, toddler, infant): everyone aged 70 or more is
"elderly" (households.json roles.elderly_from_age), householders included, with abilities drawn by age band (mobility
devices, low vision, reduced hearing); an elderly householder keeps authority 2 and shares the primary bedroom with a
partner. Bedrooms follow the people domain's rule (people/furnish.bedroom_owners: the primary bedroom for the first two
adults, then one bedroom each), with householders of any role counted as its adults; roommates sleep apart, an infant
takes the nursery when there is one, and minors share only when bedrooms run out (a spec that fixes the count, or a
household above MAX_BEDROOMS).
Profiles, preferences and schedules are written before the furnishers run, so the people and textiles domains'
setdefault keeps them (their own draws fill only what is missing). Caged pets are recorded in household.profile
pets_pending for the pets domain's generation-2 furnisher (habitats are its follow-up, TIER1_INTEGRATION section 6).
Generation 1 never calls this module.
"""
import copy
import datetime
import functools
import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "generator" / "households.json"
MINOR_ROLES = ("infant", "toddler", "child", "teen")
HOUSEHOLDER_RELATIONS = ("self", "partner", "roommate")      # the people who keep the house (not grandparents)
ROLE_AGE = {"infant": 0.5, "toddler": 2.0, "child": 7.0, "teen": 15.0, "elderly": 78.0}   # people.common.age_of
AUTHORITY = {"adult": 2, "elderly": 1}                                                     # people.common.authority
LABEL_SUFFIX = {"infant": "baby", "toddler": "toddler", "child": "child", "teen": "teen", "elderly": "grandparent"}
# 5+ bedroom homes are about 4 % of US housing (ACS 2022); bigger households share before the house grows further
MAX_BEDROOMS = 5
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
SHIFT_DAYS = ["Wed", "Thu", "Fri", "Sat", "Sun"]
# where awake residents are when the episode starts (the generation-1 rooms; teens and children in their rooms)
AWAKE_ROOMS = {"adult": ("living_room", "kitchen", "office"), "elderly": ("living_room", "bedroom"),
               "teen": ("bedroom", "living_room"), "child": ("bedroom", "playroom", "living_room"),
               "toddler": ("living_room", "playroom", "kitchen"), "infant": ("nursery", "bedroom", "master_bedroom")}
HIDDEN_ROOM_TYPES = ("coat_closet", "linen_closet", "utility_closet", "pantry", "attic", "elevator")


def _check_weights(name, key, bounds, weights):
    lo, hi = bounds
    if hi > lo and (weights is None or len(weights) != hi - lo + 1 or abs(sum(weights) - 1.0) > 1e-9):
        raise ValueError("households.json: {}.{} needs {} weights summing to 1".format(name, key, hi - lo + 1))


@functools.lru_cache(maxsize=1)
def households():
    """households.json, checked once per process."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    table = {k: v for k, v in data["presets"].items() if isinstance(v, dict)}
    if abs(sum(p["weight"] for p in table.values()) - 1.0) > 1e-9:
        raise ValueError("households.json: preset weights must sum to 1")
    for name, p in table.items():
        counted = "children" if "children" in p else "adults"
        _check_weights(name, counted, p.get(counted, [1, 1]), p.get("count_weights"))
        _check_weights(name, "elderly", p.get("elderly", [0, 0]), p.get("elderly_count_weights"))
    for name in ("diet", "bed_style", "pillow_layout"):
        if abs(sum(v for k, v in data["profile"][name].items() if k != "doc") - 1.0) > 1e-9:
            raise ValueError("households.json: profile.{} must sum to 1".format(name))
    return data


def presets():
    """{name: preset} of households.json (the doc and source entries left out)."""
    return {k: v for k, v in households()["presets"].items() if isinstance(v, dict)}


def _weighted(rng, table):
    """One key of {key: weight}, in the table's order (keys starting with doc or source are skipped)."""
    items = [(k, float(v)) for k, v in table.items() if not k.startswith(("doc", "source"))]
    x = rng.random() * sum(w for _, w in items)
    for key, weight in items:
        x -= weight
        if x < 0:
            return key
    return items[-1][0]


def _count(rng, bounds, weights=None):
    lo, hi = bounds
    if lo == hi:
        return lo
    return lo + int(_weighted(rng, {str(i): w for i, w in enumerate(weights)})) if weights else rng.randint(lo, hi)


def role_of_age(age):
    """The people domain's role for a minor's age in years."""
    if age < 1:
        return "infant"
    if age < 4:
        return "toddler"
    if age < 13:
        return "child"
    return "teen"


def age_of(person):
    """A person's age in years, else the people domain's default for the role."""
    try:
        return float(person["age"])
    except (KeyError, TypeError, ValueError):
        return ROLE_AGE.get(person.get("role", "adult"), 35.0)


def householder(person, relation=None):
    """An adult of the household, whatever their role: role adult, or an elderly resident who is self, partner or
    roommate (relation: the member's relation when the record no longer carries it)."""
    role = person.get("role", "adult")
    rel = relation if relation is not None else person.get("relation")
    return role == "adult" or (role == "elderly" and rel in HOUSEHOLDER_RELATIONS)


def elderly_abilities(rng, age):
    """Abilities of an elderly resident by age band (households.json roles.elderly_abilities): mobility, vision and
    hearing drawn independently from the band's shares."""
    bands = households()["roles"]["elderly_abilities"]["bands"]
    band = bands[0]
    for row in bands:
        if age >= row["from"]:
            band = row
    return {key: _weighted(rng, band[key]) for key in ("mobility", "vision", "hearing")}


# ----------------------------------------------------------------------------------------------------------------
# composition


def bedrooms_needed(members, relation=None, share=False):
    """Bedrooms a household needs. Default: the people domain's rule (the primary bedroom for the first two adults,
    one bedroom for everybody else; roommates one each). share=True: the minimum when bedrooms are scarce (minors two
    to a room, infants in their parents' room)."""
    roles = ["adult" if householder(m) else m.get("role", "adult") for m in members]
    if not roles:
        return 0
    adults = roles.count("adult")
    others = len(roles) - adults
    minors = sum(1 for r in roles if r in MINOR_ROLES and r != "infant")
    if not share:
        if relation == "roommates":
            return adults + others
        return 1 + max(0, adults - 2) + (others if adults else others - 1)
    paired = int(math.ceil(minors / 2.0))
    if relation == "roommates":
        return adults + roles.count("elderly") + paired
    lone_infants = 1 if not adults and not minors and "infant" in roles else 0
    return (1 if adults else 0) + max(0, adults - 2) + roles.count("elderly") + paired + lone_infants


class _Names(object):
    """Distinct given names for one household, drawn from the region's pool."""

    def __init__(self, rng, region):
        pool = households()["names"]
        self.names = list(pool.get(region) or pool["us"])
        rng.shuffle(self.names)
        self.i = 0

    def next(self):
        name = self.names[self.i % len(self.names)]
        self.i += 1
        return name


def _draw_counts(rng, name, fixed):
    p = presets()[name]
    for _ in range(25):
        adults = _count(rng, p.get("adults", [1, 1]), None if "children" in p else p.get("count_weights"))
        kids = _count(rng, p.get("children", [0, 0]), p.get("count_weights") if "children" in p else None)
        elders = _count(rng, p.get("elderly", [0, 0]), p.get("elderly_count_weights"))
        trial = [{"role": "adult"}] * adults + [{"role": "child"}] * kids + [{"role": "elderly"}] * elders
        if fixed is None or bedrooms_needed(trial, p.get("relation"), share=True) <= fixed:
            return adults, kids, elders
    raise ValueError("household composition {!r} does not fit {} bedrooms".format(name, fixed))


def _draw_preset(rng, name, region, fixed=None):
    """Members of a named preset: adults first, then minors oldest first, then grandparents. Anyone aged
    elderly_from_age or more is elderly, with abilities by age band."""
    p = presets()[name]
    roles = households()["roles"]
    relation = p.get("relation", "self")
    adults, kids, elders = _draw_counts(rng, name, fixed)
    lo, hi = p.get("child_age", [0, 17])
    child_ages = sorted((rng.randint(lo, hi) for _ in range(kids)), reverse=True)
    child_ages = [round(rng.randint(2, 11) / 12.0, 2) if a == 0 else float(a) for a in child_ages]
    a_lo, a_hi = p.get("adult_age", [24, 64])
    if child_ages:
        gap = roles["parent_gap_years"]
        first = min(64, int(child_ages[0]) + rng.randint(gap[0], gap[1]))
    else:
        first = rng.randint(a_lo, a_hi)
    if elders:
        first = min(first, 58)
    names = _Names(rng, region)
    members = []
    for i in range(adults):
        if relation == "roommates":
            age, rel = (first if i == 0 else rng.randint(a_lo, a_hi)), "roommate"
        elif i == 0:
            age, rel = first, "self"
        else:
            g = roles["partner_age_gap_years"]
            age, rel = max(20, min(90, first + rng.randint(g[0], g[1]))), "partner"
        member = {"role": "adult", "age": age, "label": names.next(), "relation": rel}
        if age >= roles["elderly_from_age"]:
            member.update(role="elderly", abilities=elderly_abilities(rng, age))
        members.append(member)
    for age in child_ages:
        role = role_of_age(age)
        members.append({"role": role, "age": age, "label": "{} ({})".format(names.next(), LABEL_SUFFIX[role]), "relation": "child"})
    e_lo, e_hi = p.get("elderly_age", [70, 92])
    gap = roles["grandparent_gap_years"]
    for _ in range(elders):
        age = max(e_lo, min(e_hi, first + rng.randint(gap[0], gap[1])))
        members.append({"role": "elderly", "age": age, "label": "{} ({})".format(names.next(), LABEL_SUFFIX["elderly"]),
                        "relation": "grandparent", "abilities": elderly_abilities(rng, age)})
    return members, relation


def _assign_ids(members):
    counters = {}
    for m in members:
        stem = "resident" if householder(m) else ("elder" if m["role"] == "elderly" else "child")
        counters[stem] = counters.get(stem, 0) + 1
        m["id"] = "{}_{}".format(stem, counters[stem])
    return members


def _draw_pets(rng, region):
    data = households()["pets"]
    pets, pending = [], []
    for species, row in data["species"].items():
        if rng.random() >= float(row.get(region, row["us"])):
            continue
        count = _count(rng, [1, len(row["count_weights"])], row["count_weights"]) if "count_weights" in row else 1
        for _ in range(count):
            if row.get("caged"):
                kind = rng.choice(row["kinds"]) if row.get("kinds") else species
                pending.append({"species": kind, "habitat": row["habitat"], "needs": row.get("needs")})
            else:
                rec = {"species": species, "label": rng.choice(data["names"][species])}
                if species == "dog":
                    rec["size"] = _weighted(rng, data["dog_size_weights"])
                pets.append(rec)
    for group in (pets, pending):
        counters = {}
        for rec in group:
            counters[rec["species"]] = counters.get(rec["species"], 0) + 1
            rec["id"] = "{}_{}".format(rec["species"], counters[rec["species"]])
    return pets, pending


def compose(rng, opts):
    """The household composition for generation 2, drawn with rng = Random("profile:{seed}") (sweep 14.2-14.3).

    opts: residents "random" (preset weights) | "minimal" (one adult) | "none" | {"composition": preset} | [records]
    (kept as given); pets "random" | "none" | [records]; region (pet shares and names; default "us"); bedrooms (a fixed
    count restricts random draws to households that fit when minors share). Returns {"preset", "relation", "people":
    [{"id", "role", "age", "label", "relation", ...}], "pets": [records], "pets_pending": [{"id", "species", "habitat",
    "needs"}], "bedrooms": the count to build (at least 1), "fixed_bedrooms": the spec's count or None}."""
    region = opts.get("region") or "us"
    fixed = opts.get("bedrooms")
    spec = opts.get("residents", "random")
    relation = "self"
    if spec == "none":
        preset, people = "none", []
    elif isinstance(spec, list):
        preset, people = "list", [copy.deepcopy(item) for item in spec]
        adults = [m for m in people if m.get("role", "adult") == "adult"]
        relation = "partners" if len(adults) == 2 else "self"
    elif spec == "minimal":
        # one adult in the generation-1 age range, so "minimal" spec houses keep the same household
        preset = "minimal"
        people = [{"role": "adult", "age": rng.randint(28, 60), "label": _Names(rng, region).next(), "relation": "self"}]
    elif isinstance(spec, dict) and "composition" in spec:
        preset = spec["composition"]
        if preset not in presets():
            raise ValueError("Unknown household composition {!r} (known: {})".format(preset, ", ".join(sorted(presets()))))
        people, relation = _draw_preset(rng, preset, region, fixed)
    elif spec == "random":
        table = {name: p["weight"] for name, p in sorted(presets().items()) if p["weight"] > 0}
        if fixed is not None:
            def fits(p):
                least = ([{"role": "adult"}] * p.get("adults", [1, 1])[0] + [{"role": "child"}] * p.get("children", [0, 0])[0]
                         + [{"role": "elderly"}] * p.get("elderly", [0, 0])[0])
                return bedrooms_needed(least, p.get("relation"), share=True) <= fixed
            table = {name: w for name, w in table.items() if fits(presets()[name])}
        preset = _weighted(rng, table)
        people, relation = _draw_preset(rng, preset, region, fixed)
    else:
        raise ValueError("residents must be random, minimal, none, a list or {\"composition\": name}")
    if preset != "list":
        _assign_ids(people)
    pets_spec = opts.get("pets", "random")
    if pets_spec == "none":
        pets, pending = [], []
    elif isinstance(pets_spec, list):
        pets, pending = [copy.deepcopy(p) for p in pets_spec], []
    elif pets_spec == "random":
        pets, pending = _draw_pets(rng, region)
    else:
        raise ValueError("pets must be random, none or a list")
    needed = bedrooms_needed(people, relation)
    if needed > MAX_BEDROOMS:
        needed = max(MAX_BEDROOMS, bedrooms_needed(people, relation, share=True))
    return {"preset": preset, "relation": relation, "people": people, "pets": pets, "pets_pending": pending,
            "bedrooms": fixed if fixed is not None else max(1, needed), "fixed_bedrooms": fixed}


# ----------------------------------------------------------------------------------------------------------------
# records


def _indoor(rooms):
    return sorted(r for r, v in rooms.items() if not v["outdoor"] and not v.get("shared") and v["type"] not in HIDDEN_ROOM_TYPES)


def people_records(rng, composition, rooms, start_room):
    """Person records with the generation-1 fields (profiles come later). rng is the house rng, which picks the room
    each resident is in, as in generation 1. Explicit lists are kept as given (only a missing location is drawn)."""
    indoor = _indoor(rooms)
    people = {}
    for member in composition["people"]:
        drawn = composition["preset"] != "list"
        record = {k: copy.deepcopy(v) for k, v in member.items() if k != "id" and not (drawn and k == "relation")}
        if drawn:
            authority = 2 if householder(member) else AUTHORITY.get(record["role"], 0)    # an elderly householder: 2
            record.update(authority=authority, consent=True, awake=True, mood="neutral", permissions=[],
                          preferences={})
        if "location" not in record:
            kinds = AWAKE_ROOMS.get(record.get("role", "adult"), AWAKE_ROOMS["adult"])
            options = [r for r in indoor if rooms[r]["type"] in kinds] or indoor or [start_room]
            record["location"] = rng.choice(options)
        people[member["id"]] = record
    return people


def pet_records(rng, composition, rooms):
    """Dog and cat records (and explicit pet lists, kept as given), placed like generation 1."""
    indoor = sorted(r for r, v in rooms.items() if not v["outdoor"] and not v.get("shared")
                    and v["type"] in ("living_room", "kitchen", "bedroom", "master_bedroom"))
    pets = {}
    for item in composition["pets"]:
        record = {k: copy.deepcopy(v) for k, v in item.items() if k != "id"}
        if "location" not in record and indoor:
            record["location"] = rng.choice(indoor)
        pets[item["id"]] = record
    return pets


def assign_bedrooms(composition, people, rooms, start_room):
    """Bedrooms by the people domain's rule (first two adults in the primary bedroom, then one bedroom each in sorted
    order; householders of any role count as its adults, so an older couple shares) except that roommates sleep apart,
    an infant takes the nursery when there is one, and when bedrooms run out minors share (two to a room, closest in
    age) and an infant sleeps in its parents' room. Returns (homes {pid: room}, sharing [[pids]] outside the primary
    bedroom, overflow [pids] left with only the start room)."""
    relations = {m["id"]: m.get("relation") for m in composition.get("people", ()) if "id" in m}
    adults = sorted(pid for pid, p in people.items() if householder(p, relations.get(pid)))
    others = sorted(pid for pid, p in people.items() if pid not in adults and
                    p.get("role") in ("child", "teen", "elderly", "infant", "toddler"))
    masters = sorted(r for r in rooms if rooms[r]["type"] == "master_bedroom")
    free = sorted(r for r in rooms if rooms[r]["type"] == "bedroom")
    nurseries = sorted(r for r in rooms if rooms[r]["type"] == "nursery")
    if composition.get("relation") == "roommates":
        first, queue = adults[:1], adults[1:] + others
    else:
        first, queue = adults[:2] or others[:1], adults[2:] + (others if adults else others[1:])
    homes = {}
    if masters:
        homes.update((pid, masters[0]) for pid in first)
    else:
        queue = first + queue
    if nurseries:
        for pid in [q for q in queue if people[q].get("role") == "infant"]:
            homes[pid] = nurseries[0]
            queue.remove(pid)
    overflow = []
    for pid in queue:
        if free:
            homes[pid] = free.pop(0)
            continue
        role = people[pid].get("role", "adult")
        if role == "infant" and adults and adults[0] in homes:
            homes[pid] = homes[adults[0]]
            continue
        if role in MINOR_ROLES:
            age = age_of(people[pid])
            options = sorted((abs(age_of(people[other]) - age), room) for other, room in homes.items()
                             if people[other].get("role") in MINOR_ROLES and people[other].get("role") != "infant"
                             and room not in masters and list(homes.values()).count(room) < 2)
            if options:
                homes[pid] = options[0][1]
                continue
        homes[pid] = start_room
        overflow.append(pid)
    groups = {}
    for pid, room in homes.items():
        groups.setdefault(room, []).append(pid)
    sharing = sorted(sorted(pids) for room, pids in groups.items() if len(pids) > 1 and room not in masters)
    return homes, sharing, overflow


# ----------------------------------------------------------------------------------------------------------------
# profiles and schedules


def _age_band(age, bands):
    """The band name ("under_25", "25_to_54", "65_plus") an age falls in (half-open ranges)."""
    for name in bands:
        if name.startswith("under_") and age < float(name.split("_")[1]):
            return name
        if name.endswith("_plus") and age >= float(name.split("_")[0]):
            return name
        if "_to_" in name:
            lo, hi = (float(x) for x in name.split("_to_"))
            if lo <= age < hi + 1:
                return name
    raise ValueError("no age band for {} in {}".format(age, bands))


def weekly_pattern(rng, person):
    """office | remote | shift | home | retired | school (sweep 12)."""
    role, age = person.get("role", "adult"), age_of(person)
    if role in MINOR_ROLES:
        return "school" if age >= 5 else "home"
    if role == "elderly" and age >= 75:
        return "retired"            # participation is under 10 % from 75 (BLS); younger elderly follow the 65+ row
    table = households()["profile"]["weekly_pattern"]
    return _weighted(rng, table[_age_band(age, [k for k in table if k != "doc"])])


def profile_person(rng, person, region, household_diet=None):
    """The profile of one resident (sweep 12): tidiness, chronotype, diet, allergies, drinks, fold/bed/pillow styles,
    tech level and weekly pattern. Minors follow the household's first adult's diet with probability 0.9."""
    data = households()["profile"]
    role, age = person.get("role", "adult"), age_of(person)
    minor = role in MINOR_ROLES
    a, b = data["tidiness"].get(role, data["tidiness"]["adult"])
    tidiness = round(rng.betavariate(a, b), 2)
    chronotype = _weighted(rng, data["chronotype"].get(role, data["chronotype"]["adult"]))
    diet = household_diet if household_diet and minor and rng.random() < 0.9 else _weighted(rng, data["diet"])
    allergies = [name for name, p in data["allergies"]["child" if minor else "adult"].items() if rng.random() < p]
    hot = "none" if minor else _weighted(rng, data["hot_drink"].get(region, data["hot_drink"]["us"]))
    cold = _weighted(rng, data["cold_drink"]["minor" if minor else "adult"])
    fold = {k: rng.choice(v) for k, v in data["fold_style"].items() if k != "doc"}
    bands = [k for k in data["tech_level"] if k != "doc"]
    tech = "none" if role in ("infant", "toddler") else _weighted(rng, data["tech_level"][_age_band(age, bands)])
    return {"tidiness": tidiness, "chronotype": chronotype, "diet": diet, "allergies": allergies,
            "drinks": {"hot": None if hot == "none" else hot, "cold": cold}, "fold_style": fold,
            "bed_style": _weighted(rng, data["bed_style"]), "pillow_layout": _weighted(rng, data["pillow_layout"]),
            "tech_level": tech, "weekly_pattern": weekly_pattern(rng, person)}


def _hm(text):
    h, m = text.split(":")
    return int(h) + int(m) / 60.0


def sleep_window(rng, person, profile):
    """(bed, wake) as HH:MM: young children by age (the people domain's rule: under 6 in bed by 19:30, older children
    20:30, up at 07:00), teens and adults by chronotype, the elderly 21:30-07:00 like the people domain."""
    role, age = person.get("role", "adult"), age_of(person)
    data = households()["profile"]["sleep"]
    if role in ("infant", "toddler", "child"):
        return ("19:30" if age < 6 else "20:30"), "07:00"
    if role == "elderly":
        return "21:30", "07:00"
    return tuple(rng.choice(data["teen" if role == "teen" else profile["chronotype"]]))


def schedule(rng, person, profile, home, work_room):
    """Time blocks in the people domain's schedule format (people/furnish._schedule): sleeping, away (work or
    school), working (remote, at work_room), napping (under 4 and the elderly)."""
    bed, wake = sleep_window(rng, person, profile)
    blocks = [{"from": bed, "to": wake, "activity": "sleeping", "room": home}]
    pattern = profile["weekly_pattern"]
    if pattern == "school":
        blocks.append({"from": "08:00", "to": "15:00", "activity": "away", "days": list(WEEKDAYS), "what": "school"})
    elif pattern == "office" or (pattern == "remote" and not work_room):
        blocks.append({"from": "08:30", "to": "17:30", "activity": "away", "days": list(WEEKDAYS), "what": "work"})
    elif pattern == "remote":
        blocks.append({"from": "09:00", "to": "17:00", "activity": "working", "room": work_room, "days": list(WEEKDAYS)})
    elif pattern == "shift":
        blocks.append({"from": "15:00", "to": "23:00", "activity": "away", "days": list(SHIFT_DAYS), "what": "work"})
    role = person.get("role", "adult")
    if role in ("infant", "toddler"):
        blocks.append({"from": "13:00", "to": "15:00", "activity": "napping", "room": home})
    elif role == "elderly":
        blocks.append({"from": "13:30", "to": "14:30", "activity": "napping", "room": home})
    return blocks


def asleep_at(blocks, when):
    """True when a sleeping or napping block covers the datetime (a block without days applies every day; a block
    crossing midnight belongs to the day it starts)."""
    h = when.hour + when.minute / 60.0
    today = when.strftime("%a")
    yesterday = (when - datetime.timedelta(days=1)).strftime("%a")
    for block in blocks:
        if block.get("activity") not in ("sleeping", "napping"):
            continue
        start, end = _hm(block["from"]), _hm(block["to"])
        days = block.get("days")
        if start <= end:
            if start <= h < end and (not days or today in days):
                return True
        elif (h >= start and (not days or today in days)) or (h < end and (not days or yesterday in days)):
            return True
    return False


def apply_profiles(rng, config, composition, start, season, region, beds=True):
    """Profiles, matching preferences, bedrooms (home_room, privacy_zones), schedules, the smart-home tier and
    household.profile, written before the furnishers run. With beds (the house is furnished), a drawn resident who is
    asleep at the start time is put in their bedroom (awake false, posture sleeping) and the gen.residents furnisher
    lays them in their bed. Explicit resident lists keep every given field (only missing ones are filled) and are
    never put to bed."""
    rooms, people = config["rooms"], config.get("people", {})
    living = config.get("start_room")
    homes, sharing, overflow = assign_bedrooms(composition, people, rooms, living)
    work_rooms = sorted(r for r in rooms if rooms[r]["type"] == "office") or \
        sorted(r for r in rooms if rooms[r]["type"] in ("dining_room", "kitchen"))
    when = datetime.datetime.fromisoformat(start)
    relations = {m["id"]: m.get("relation", "self") for m in composition["people"] if "id" in m}
    drawn = composition["preset"] != "list"
    household = config.setdefault("household", {})
    first_diet = None
    members = {}
    for pid in sorted(people):
        person = people[pid]
        prof = person.setdefault("profile", {})
        for key, value in profile_person(rng, person, region, first_diet).items():
            prof.setdefault(key, value)
        prof.setdefault("relation", relations.get(pid, "self"))
        if first_diet is None and householder(person, prof["relation"]):
            first_diet = prof["diet"]
        home = person.setdefault("home_room", homes.get(pid, living))
        person.setdefault("privacy_zones", [home] if home != living else [])
        if "schedule" not in person:
            person["schedule"] = schedule(rng, person, prof, home, work_rooms[0] if work_rooms else None)
        prefs = person.setdefault("preferences", {})
        prefs.setdefault("storage", {})
        prefs.setdefault("diet", prof["diet"])
        if prof["allergies"]:
            prefs.setdefault("allergies", list(prof["allergies"]))
        if prof["drinks"]["hot"]:
            prefs.setdefault("drink", prof["drinks"]["hot"])
        prefs.setdefault("cold_drink", prof["drinks"]["cold"])
        prefs.setdefault("fold_style", dict(prof["fold_style"]))
        prefs.setdefault("bed_style", prof["bed_style"])
        prefs.setdefault("pillow_layout", prof["pillow_layout"])
        if beds and drawn and home != living and asleep_at(person["schedule"], when):
            person.update(location=home, awake=False, posture="sleeping")
        members[pid] = {"role": person.get("role", "adult"), "age": person.get("age"), "relation": prof["relation"],
                        "home_room": home}
    adults = [pid for pid in sorted(people) if householder(people[pid], people[pid]["profile"]["relation"])]
    if adults:
        household.setdefault("fold_style", dict(people[adults[0]]["profile"]["fold_style"]))
    backyard = any(r["type"] == "backyard" for r in rooms.values())
    pending = [{k: v for k, v in p.items() if k != "needs"} for p in composition["pets_pending"]
               if p.get("needs") != "backyard" or backyard]
    household["profile"] = {
        "gen": 2, "composition": composition["preset"], "relation": composition.get("relation", "self"),
        "members": members, "sharing": sharing, "overflow": overflow, "season": season,
        "hemisphere": (config.get("clock") or {}).get("hemisphere", "north"), "region": region,
        "smart_home": _weighted(rng, households()["smart_home"]["tiers"]), "pets_pending": pending}
    return household["profile"]
