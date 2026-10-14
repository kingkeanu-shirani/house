"""Generation 2: personal and household belongings with owners, homes, habits and displacement (docs/design/TIER1_SWEEP.md
section 14.4; data in data/generator/belongings.json).

Each resident gets the items of their role (phone and charger, wallet, keys, bag, outerwear, shoes, earbuds, tablet,
smartwatch, sunglasses; backpacks and lunch boxes for children; slippers in shoes-off homes), each with owner (never
tier), home (its habitual spot, never tier) and, for phones, phone_of. The household gets umbrellas, passports and
scissors. The home depends on the role, the season, the region (shoes at the entry where shoes come off) and the time
of day (a phone charges on the nightstand at night and sits on the kitchen counter or desk by day). An item with
alternatives is out of place with probability base + untidy x (1 - tidiness); the alternative is drawn by weight
among those valid at that time of day (sofa, bed, coat pocket, bag, floor...). Every resident gets
habits {kind: {"home": spot, "alt": [[spot, weight(, period)], ...], "items": [ids]}} (phones also "day" and
"night") and preferences.storage {item: home}, what the people domain's where: answerer reads. Items the domains
already gave a resident (textiles' coat and shoes, admin's backpack) count instead of being duplicated. Missing types
and items that fit nowhere are reported as layout-audit notes, never forced. Generation 1 returns at once.
"""
import datetime
import functools
import json
from pathlib import Path

from ..kernel.plugins import furnisher
from .furnishings import Index, first_known, floor, gen2, note, put, rooms_of, weighted
from .place import Placer
from .profile import age_of, asleep_at

DATA = Path(__file__).resolve().parent.parent / "data" / "generator" / "belongings.json"
OUTERWEAR = ("coat", "puffer_jacket", "light_jacket", "raincoat", "hoodie")
BAGS = ("work_bag", "handbag", "backpack", "bag")
AREAS = {"kitchen": ("kitchen",), "living": ("living_room",), "office": ("office",), "dining": ("dining_room",),
         "coat_closet": ("coat_closet",), "master": ("master_bedroom",), "garage": ("garage",),
         "entry": ("entryway", "mudroom")}
LABELLED = {"junk_drawer": ("drawer", "junk drawer"), "top_drawer": ("drawer", "top drawer"),
            "filing_cabinet": ("cabinet", "filing cabinet")}
LONG_ITEMS_IN_STANDS = ("rain_umbrella",)
PHONE_ROLES = ("adult", "teen", "elderly", "child")


@functools.lru_cache(maxsize=1)
def data():
    """belongings.json, read once per process."""
    return json.loads(DATA.read_text(encoding="utf-8"))


def displacement_probability(tidiness):
    """P(an item with alternatives is out of place) for a resident's tidiness (0..1)."""
    d = data()["displacement"]
    return d["base"] + d["untidy"] * (1.0 - float(tidiness))


def first_name(person, pid):
    """The given name of a resident label ("Sam (teen)" -> "Sam")."""
    return str(person.get("label") or pid).split(" (")[0]


class _Run(object):
    """One house's belongings: spot resolution, placement and the per-resident bookkeeping."""

    def __init__(self, config, rng, opts, registry):
        self.c, self.rng, self.opts, self.reg = config, rng, opts, registry
        self.p, self.idx = Placer(config, registry, rng), Index(config)
        self.people = config.get("people") or {}
        self.season = opts.get("season") or "autumn"
        self.shoes_off = bool((config.get("metadata") or {}).get("shoes_off"))
        self.listed = ((config.get("household") or {}).get("profile") or {}).get("composition") == "list"
        self.start = datetime.datetime.fromisoformat((config.get("clock") or {}).get("start"))
        self.owned = {}
        for eid in sorted(config["entities"]):
            owner = config["entities"][eid].get("owner")
            if owner in self.people:
                self.owned.setdefault(owner, []).append(eid)

    # ---- spots: ("entity", id) | ("floor", room) | ("pocket", garment id) | ("bag", bag id); spot[1] is what a
    # home, a habit or preferences.storage names
    def area_room(self, area, pid):
        """The room an area names: own = the owner's bedroom, else the first room of the area's types."""
        if area == "own":
            home = (self.people.get(pid) or {}).get("home_room")
            return home if home in self.c["rooms"] else None
        return (rooms_of(self.c, *AREAS[area]) or [None])[0]

    def owned_of(self, pid, types):
        """The resident's things of these types (a pair counts once, by its left shoe)."""
        return [e for e in self.owned.get(pid, ()) if self.c["entities"][e].get("type") in types
                and self.c["entities"][e].get("relation") != "attached"]

    def resolve(self, name, pid):
        """A spot name (AREA.WHAT, belongings.json "spots") -> a spot, or None when the house has no such place."""
        area, what = name.split(".", 1)
        if what == "coat_pocket":
            coats = self.owned_of(pid, OUTERWEAR)
            return ("pocket", coats[0]) if coats and self.reg.known("garment_pocket") else None
        if what == "bag":
            bags = self.owned_of(pid, BAGS)
            return ("bag", bags[0]) if bags else None
        room = self.area_room(area, pid)
        if room is None:
            return None
        if what == "floor":
            return ("floor", room)
        found = self.find_spot(room, what, pid)
        if not found and area == "kitchen" and what == "table":
            found = self.find_spot((rooms_of(self.c, "dining_room") or [None])[0], what, pid)
        return ("entity", found[0]) if found else None

    def find_spot(self, room, what, pid):
        """Candidate entities for WHAT in a room, best first (a partner's own nightstand first)."""
        if room is None:
            return []
        if what in LABELLED:
            kind, label = LABELLED[what]
            return self.idx.find(room, (kind,), where=lambda rec: rec.get("label") == label)
        if what == "closet":
            return self.idx.find(room, ("closet", "wardrobe"))
        if what == "table":
            return self.idx.find(room, ("dining_table", "table"), where=lambda rec: rec.get("location") == room
                                 and rec.get("label") in ("kitchen table", "dining table", "table"))
        if what == "bed":
            return self.idx.find(room, ("bed",), where=lambda rec: rec.get("type") != "crib")
        found = self.idx.find(room, (what,))
        if what == "nightstand" and len(found) > 1:
            # partners each have their own side of the bed
            sleepers = sorted(q for q, p in self.people.items() if p.get("home_room") == room and p.get("role") != "infant")
            if pid in sleepers:
                k = sleepers.index(pid) % len(found)
                found = found[k:] + found[:k]
        return found

    def place_at(self, spot, type_name, garment=False, **props):
        """Make the item at a spot (floor, a garment's pocket, an umbrella stand, or any hook/container/surface)."""
        kind, ref = spot
        if kind == "floor":
            return floor(self.p, self.idx, ref, type_name, **props)
        if kind == "pocket":
            eid = put(self.p, self.idx, self._pocket(ref), type_name, **props)
            if eid:
                self.c["entities"][eid]["in_pocket"] = True
            return eid
        if type_name in LONG_ITEMS_IN_STANDS and self.c["entities"][ref].get("type") == "umbrella_stand":
            return self._stand(ref, type_name, **props)
        return put(self.p, self.idx, ref, type_name, garment=garment, **props)

    def _pocket(self, garment):
        """The garment's pocket (textiles_laundry's garment_pocket part), made on first use."""
        for eid in self.idx.find(self.idx.room_of(garment), ("garment_pocket",)):
            if self.c["entities"][eid].get("location") == garment:
                return eid
        eid = self.p.make("garment_pocket", garment, label="pocket", pocket_name="front_right")
        self.c["entities"][eid]["relation"] = "part"
        return self.idx.add(eid)

    def _stand(self, stand, type_name, **props):
        """An umbrella stands upright with its handle above the rim (taller than the stand's inner height, which
        Placer.inside's box fit would refuse); the stand's volume still limits how many fit."""
        item = self.reg.make(type_name, stand, **props)
        used = sum(float(e.get("volume_l", 0)) for e in self.c["entities"].values() if e.get("location") == stand)
        if used + float(item.get("volume_l", 0)) > float(self.c["entities"][stand].get("capacity_l", 0) or 0) + 1e-9:
            return None
        item["relation"] = "in"
        eid = self.p.new_id(type_name)
        self.c["entities"][eid] = item
        return self.idx.add(eid)

    def place_item(self, pid, kind, type_name, homes, alts, tidiness, garment=False, **props):
        """One owned item at its home or, when out of place, at a weighted alternative (never its home). Returns
        (eid, home spot) or (None, None) after a note."""
        spots = [s for s in (self.resolve(name, pid) for name in homes) if s]
        if not spots:
            note(self.opts, "no home for {} ({}) of {}".format(kind, type_name, pid))
            return None, None
        options = self.alternatives(pid, alts, spots[0])
        eid, home = None, spots[0]
        if options and self.rng.random() < displacement_probability(tidiness):
            eid = self.place_at(weighted(self.rng, options), type_name, garment=garment, owner=pid, home=home[1], **props)
        if eid is None:
            for spot in spots:
                eid = self.place_at(spot, type_name, garment=garment, owner=pid, home=spot[1], **props)
                if eid:
                    home = spot
                    break
        if eid is None:
            note(self.opts, "no room for {} ({}) of {}".format(kind, type_name, pid))
            return None, None
        self.owned.setdefault(pid, []).append(eid)
        return eid, home

    def alternatives(self, pid, alts, home):
        """[(spot, weight)] of the alternatives that exist in this house, never the home itself."""
        out = []
        for alt in alts:
            spot = self.resolve(alt[0], pid)
            if spot and spot[1] != home[1] and spot[1] not in [s[1] for s, _ in out]:
                out.append((spot, alt[1]))
        return out

    def place_pair(self, pid, kind, type_name, homes, **props):
        """A pair of shoes in the textiles_laundry convention: the left shoe carries the pair's footprint and the right
        one is attached to it (paired_with both ways)."""
        single = list(self.reg.make(type_name, "nowhere").get("size_m") or [0.11, 0.29, 0.12])
        pair_size = [round(float(single[0]) * 2 + 0.01, 3), single[1], single[2]]
        pair_id = "{}_{}_{}".format(kind, pid, len(self.owned.get(pid, ())))
        left, home = self.place_item(pid, kind, type_name, homes, (), 1.0, side="left", pair_id=pair_id,
                                     size_m=pair_size, single_size_m=single, orientation="upright", **props)
        if left is None:
            return None, None
        right = self.p.make(type_name, left, owner=pid, side="right", pair_id=pair_id, orientation="upright", **props)
        self.c["entities"][right].update(relation="attached", paired_with=left)
        self.c["entities"][left]["paired_with"] = right
        self.idx.add(right)
        return left, home

    def pairs_owned(self, pid):
        """Pairs of footwear the resident already has (textiles_laundry gives everyone one)."""
        return len([e for e in self.owned.get(pid, ()) if self.c["entities"][e].get("category") == "footwear"
                    and self.c["entities"][e].get("relation") != "attached"])

    # ---- per resident
    def types(self, item, person):
        """Candidate types for one item (a seasonal, teen or either-or choice draws one)."""
        if person.get("role") == "teen" and item.get("teen_types"):
            return [weighted(self.rng, item["teen_types"])]
        if item.get("types_by_season"):
            return [weighted(self.rng, item["types_by_season"][self.season])]
        if item.get("types_choice"):
            return [weighted(self.rng, item["types_choice"])]
        return list(item["types"])

    def homes(self, item, person, period):
        """Home spot names in preference order for the role, the time of day and the shoes-off custom (keys pick the
        key bowl first for key_bowl_share of residents when there is one)."""
        role = person.get("role", "adult")
        homes = []
        for key in ("shoes_off_home" if self.shoes_off else None, "{}_home_{}".format(role, period),
                    "home_{}".format(period), "{}_home".format(role), "home"):
            if key and item.get(key):
                homes = list(item[key])
                break
        if item.get("home_choice") == "key_bowl_share" and "entry.key_bowl" in homes:
            if self.resolve("entry.key_bowl", None) is None or self.rng.random() >= data()["key_bowl_share"]["value"]:
                homes.remove("entry.key_bowl")
                homes.append("entry.key_bowl")
        return homes

    def wants(self, item, person, placed):
        """Whether this resident has the item: role probability, minimum age, weekly pattern, shoes-off custom; a
        charger needs a phone; in an explicit resident list a phone follows has_phone."""
        if item.get("needs"):
            return item["needs"] in placed and self.rng.random() < item.get("chance", 1.0)
        chance = (item.get("roles") or {}).get(person.get("role", "adult"))
        if chance is None or age_of(person) < item.get("min_age", 0):
            return False
        if item.get("shoes_off_only") and not self.shoes_off:
            return False
        if item.get("patterns") and (person.get("profile") or {}).get("weekly_pattern") not in item["patterns"]:
            return False
        if item["kind"] == "phone" and self.listed:
            return bool(person.get("has_phone"))        # an explicit resident list says who has a phone
        return self.rng.random() < chance

    def props_for(self, item, person, pid):
        """Extra fields of the item: label, the doors a key ring is for, a printed name, whose phone it is."""
        props = {}
        if item.get("label"):
            props["label"] = item["label"]
        if item.get("opens"):
            props["opens"] = list(item["opens"])
        if item.get("name_label"):
            props["name_label"] = first_name(person, pid)
        if item.get("phone_of"):
            # whose phone it is is never perceived (people.json hides phone_of; the per-entity tier keeps it hidden
            # when the people domain is not loaded)
            props.update(phone_of=pid, _perception={"hidden": ["phone_of"]})
        return props

    def resident(self, pid):
        person = self.people[pid]
        if person.get("role") in ("infant", "toddler"):
            return
        tidiness = (person.get("profile") or {}).get("tidiness", 0.5)
        period = "night" if asleep_at(person.get("schedule") or [], self.start) else "day"
        habits = person.setdefault("habits", {})
        storage = person.setdefault("preferences", {}).setdefault("storage", {})
        placed = {}
        for item in data()["items"]:
            kind = item["kind"]
            if not self.wants(item, person, placed):
                continue
            homes = self.homes(item, person, period)
            props = self.props_for(item, person, pid)
            if item.get("pairs"):
                count = self.rng.randint(*item["pairs"]) - (self.pairs_owned(pid) if kind == "shoes" else 0)
                for _ in range(max(0, count)):
                    wanted = self.types(item, person)
                    type_name = first_known(self.reg, wanted)
                    if type_name is None:
                        note(self.opts, "missing type {} for {}".format(wanted[0], pid))
                        continue
                    if item.get("skip_if_owned") and self.owned_of(pid, (type_name,)):
                        continue
                    eid, home = self.place_pair(pid, kind, type_name, homes, **props)
                    if eid:
                        placed[kind] = eid
                        storage[eid] = home[1]
                        habits.setdefault(kind, {"home": home[1], "alt": [], "items": []})["items"].append(eid)
                continue
            wanted = self.types(item, person)
            type_name = first_known(self.reg, wanted)
            if type_name is None:
                note(self.opts, "missing type {} for {}".format(wanted[0], pid))
                continue
            if item.get("skip_if_owned") and self.owned_of(pid, (type_name,)):
                continue
            alts = [a for a in item.get("alt", ()) if len(a) < 3 or a[2] == period]
            eid, home = self.place_item(pid, kind, type_name, homes, alts, tidiness, garment=type_name in OUTERWEAR, **props)
            if eid is None:
                continue
            placed[kind] = eid
            storage[eid] = home[1]
            habits[kind] = self.habit(pid, item, person, home, eid)
        if person.get("role") in PHONE_ROLES and not self.listed:
            person["has_phone"] = person["reachable_by_phone"] = "phone" in placed

    def habit(self, pid, item, person, home, eid):
        """{"home", "alt": [[spot, weight] or [spot, weight, period]], "items"}; an item whose home depends on the
        time of day also names its "day" and "night" homes."""
        alts = []
        for alt in item.get("alt", ()):
            spot = self.resolve(alt[0], pid)
            if spot and spot[1] != home[1] and spot[1] not in [a[0] for a in alts]:
                alts.append([spot[1]] + list(alt[1:]))
        habit = {"home": home[1], "alt": alts, "items": [eid]}
        if item.get("home_day") or item.get("home_night"):
            for when in ("day", "night"):
                spot = next((s for s in (self.resolve(n, pid) for n in self.homes(item, person, when)) if s), None)
                habit[when] = spot[1] if spot else None
        return habit

    # ---- household
    def household(self):
        members = sorted(self.people)
        for item in data()["household"]:
            if item.get("for_roles") and not any(self.people[p].get("role") in item["for_roles"] for p in members):
                continue
            if item.get("per_resident") and not members:
                continue
            if item.get("chance") is not None and self.rng.random() >= item["chance"]:
                continue
            type_name = first_known(self.reg, item["types"])
            if type_name is None:
                note(self.opts, "missing type {} (household)".format(item["types"][0]))
                continue
            count = self.rng.randint(*item["count"]) if item.get("count") else 1
            for owner in (members if item.get("per_resident") else ["household"]):
                person = self.people.get(owner)
                props = {"name_label": first_name(person, owner)} if item.get("name_label") and person else {}
                spots = [s for s in (self.resolve(name, owner if person else None) for name in item["home"]) if s]
                for _ in range(count):
                    eid = next((e for e in (self.place_at(s, type_name, owner=owner, home=s[1], **props) for s in spots) if e), None)
                    if eid is None:
                        note(self.opts, "no room for {} ({})".format(item["kind"], owner))
                        break


@furnisher("gen.belongings", order=102)
def belongings(config, rng, opts, registry):
    if not gen2(opts):
        return
    run = _Run(config, rng, opts, registry)
    for pid in sorted(run.people):
        run.resident(pid)
    run.household()
