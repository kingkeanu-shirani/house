"""Generation 2: infant items, common furnishings and residents in bed (docs/design/TIER1_SWEEP.md section 14.5; data in
data/generator/furnishings.json), plus the placement helpers the belongings furnisher shares.

    gen.bedrooms      (order 60)   a crib for every infant (the nursery's, its own bedroom, else its parents' room)
                                   and a second bed in each shared bedroom: after the domains' fixtures and wall mounts
                                   (so it never seals one), before textiles dresses the beds and cribs (order 75)
    gen.furnishings   (order 101)  living-room textiles and decor, entry mats, key bowl, umbrella stand, garage cooler,
                                   a high chair for an infant (or, 0.6, a toddler) and one material and colour per set
                                   of dining chairs
    gen.belongings    (order 102)  generator/belongings.py
    gen.residents     (order 103)  residents asleep at the start time lie in their bed, infants in their crib

All of them return at once for generation 1 (opts["gen"] < 2), so generation-1 houses are unchanged. Every placement
goes through Placer (keep_access); a type the registry lacks or a piece that fits nowhere is skipped and reported as
a layout-audit note ("note gen2 ..." in the generator's report list), never forced.
"""
import datetime
import functools
import json
from pathlib import Path

from ..kernel import geometry
from ..kernel.plugins import furnisher
from .place import Placer
from .profile import asleep_at

DATA = Path(__file__).resolve().parent.parent / "data" / "generator" / "furnishings.json"
FRONT_DOOR_LABELS = ("front door", "apartment door")


@functools.lru_cache(maxsize=1)
def data():
    """furnishings.json, read once per process."""
    return json.loads(DATA.read_text(encoding="utf-8"))


def gen2(opts):
    """True for a generation-2 house (furnisher opts carry gen; a missing gen is generation 1)."""
    return int(opts.get("gen", 1) or 1) >= 2


def note(opts, text):
    """A layout-audit note for generation-2 placement (not a layout problem; tools/layout_audit.py counts them)."""
    notes = opts.get("notes")
    if isinstance(notes, list):
        notes.append("note gen2 " + text)


def first_known(registry, types):
    """The first of these types the registry knows, or None (the owner domain has not added it yet)."""
    return next((t for t in types if registry.known(t)), None)


def weighted(rng, pairs):
    """One value of [[value, weight], ...] (in list order)."""
    x = rng.random() * sum(float(w) for _, w in pairs)
    for value, weight in pairs:
        x -= float(weight)
        if x < 0:
            return value
    return pairs[-1][0]


class Index(object):
    """Root room of every entity and the entities of each room, built once per furnisher (Placer.find rescans the
    whole house on every call). add() registers entities the furnisher creates."""

    def __init__(self, config):
        self.c = config
        self.root, self.by_room = {}, {}
        for eid in sorted(config["entities"]):
            self.add(eid)

    def room_of(self, eid, depth=0):
        if eid in self.root:
            return self.root[eid]
        rec = self.c["entities"].get(eid) or {}
        loc = rec.get("location")
        if loc in self.c["rooms"]:
            room = loc
        elif loc in self.c["entities"] and depth < 12:
            room = self.room_of(loc, depth + 1)
        else:
            agent = self.c.get("people", {}).get(loc) or self.c.get("pets", {}).get(loc) or {}
            room = agent.get("location")
        self.root[eid] = room
        return room

    def add(self, eid):
        if eid is not None:
            self.by_room.setdefault(self.room_of(eid), []).append(eid)
        return eid

    def find(self, room, kinds, where=None):
        """Entities in a room (at any depth) whose type or ancestry is one of kinds, in id order."""
        out = []
        for eid in self.by_room.get(room, ()):
            rec = self.c["entities"].get(eid)
            if rec is None or not (rec.get("type") in kinds or any(k in kinds for k in rec.get("is_a", ()))):
                continue
            if where is None or where(rec):
                out.append(eid)
        return sorted(out)


def rooms_of(config, *kinds):
    """Room ids of these types, by type in the order given, then by id."""
    return [r for kind in kinds for r in sorted(config["rooms"]) if config["rooms"][r]["type"] == kind]


def front_door(config):
    """(door id, indoor room, outdoor-or-shared room, connection) of the front door, or Nones."""
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        if rec.get("type") == "exterior_door" and rec.get("label") in FRONT_DOOR_LABELS:
            for _, conn in sorted(config.get("connections", {}).items()):
                if conn.get("door") == eid:
                    a, b = conn["rooms"]
                    out = a if config["rooms"][a].get("outdoor") or config["rooms"][a].get("shared") else b
                    return eid, (b if out == a else a), out, conn
    return None, None, None, None


def put(p, idx, spot, type_name, garment=False, shelf=None, **props):
    """Hang, put inside or set on an entity spot (hooks hang; containers take it inside, on their lowest shelf unless
    told; surfaces carry it). A garment is hung in a closet that has rail slots. Returns the new id or None."""
    rec = p.c["entities"][spot]
    eid = None
    if rec.get("hang_slots") and (garment or not rec.get("container")):
        eid = p.hang(spot, type_name, **props)
    elif rec.get("container"):
        heights = rec.get("shelf_heights")
        eid = p.inside(spot, type_name, shelf=(shelf if shelf is not None else 0) if heights else None, **props)
    elif rec.get("surface"):
        eid = p.on(spot, type_name, **props)
    return idx.add(eid)


def floor(p, idx, room, type_name, near=None, **props):
    """On the floor where it cuts no hazard-free route or anyone's access (Placer.floor_item keep_access, without its
    plain-route fallback: an optional item that would only fit across a hazard-free route goes elsewhere)."""
    return idx.add(p.floor_item(room, type_name, near=near, plain=False, **props))


def furniture(p, idx, room, type_name, near=None, min_free=None, **props):
    """A piece of furniture that keeps every route, fixture and mount accessible without crossing floor hazards
    (Placer.furniture strict="hazards"), or None."""
    return idx.add(p.furniture(room, type_name, wall=near is None, near=near, min_free=min_free, strict="hazards", **props))


# ----------------------------------------------------------------------------------------------------------------
# bedrooms: cribs and the beds of shared rooms


def _crib_for(p, idx, config, pid, kind, opts):
    """The infant's crib: a free one in its bedroom or its parents' room (the nursery's), else a new one there, else in
    any other bedroom or the living room (a travel cot)."""
    people = config["people"]
    home = people[pid].get("home_room")
    parents = sorted(q for q, other in people.items() if other.get("role", "adult") == "adult")
    parent_room = people[parents[0]].get("home_room") if parents else None
    rooms = [home, parent_room] + rooms_of(config, "nursery", "master_bedroom", "bedroom", "living_room")
    rooms = [r for n, r in enumerate(rooms) if r in config["rooms"] and r not in rooms[:n]]
    for room in rooms[:2]:
        free = idx.find(room, (kind,), where=lambda rec: rec.get("owner") in (None, pid))
        if free:
            config["entities"][free[0]]["owner"] = pid
            return free[0]
    for room in rooms:
        eid = furniture(p, idx, room, kind, min_free=0.3, owner=pid)
        if eid:
            return eid
    note(opts, "no room for a crib for {}".format(pid))
    return None


def _move_home(config, pid, room):
    """An infant whose crib stands in another room sleeps there: home_room, privacy zone, schedule and the household
    profile follow it."""
    person = config["people"][pid]
    old = person.get("home_room")
    person["home_room"] = room
    if old in (person.get("privacy_zones") or []):
        person["privacy_zones"] = [room]
    for block in person.get("schedule") or []:
        if block.get("room") == old:
            block["room"] = room
    members = ((config.get("household") or {}).get("profile") or {}).get("members") or {}
    if pid in members:
        members[pid]["home_room"] = room


@furnisher("gen.bedrooms", order=60)
def bedrooms(config, rng, opts, registry):
    """Generation 2: a crib for every infant, and a second bed in each shared bedroom (generation-2 sharing happens
    only when a spec fixes too few bedrooms or a household is very large). Order 60 comes after the fixtures and wall
    mounts the domains place (radiators, outlets, coverings up to climate.air at 56), which strict placement then keeps
    accessible, and before textiles_laundry dresses every bed and crib (order 75)."""
    if not gen2(opts):
        return
    people = config.get("people") or {}
    p, idx = Placer(config, registry, rng), Index(config)
    crib = first_known(registry, data()["bedrooms"]["crib"]["types"])
    for pid in sorted(q for q, person in people.items() if person.get("role") == "infant"):
        eid = _crib_for(p, idx, config, pid, crib, opts) if crib else None
        if crib is None:
            note(opts, "missing type crib for {}".format(pid))
        elif eid and idx.room_of(eid) != people[pid].get("home_room"):
            _move_home(config, pid, idx.room_of(eid))
    spec = data()["bedrooms"]["shared_bed"]
    sleepers = {}
    for pid, person in sorted(people.items()):
        if person.get("role") != "infant" and person.get("home_room") in rooms_of(config, "bedroom"):
            sleepers.setdefault(person["home_room"], []).append(pid)
    for room, pids in sorted(sleepers.items()):
        beds = idx.find(room, ("bed",), where=lambda rec: rec.get("type") != "crib" and rec.get("location") == room)
        for _ in range(len(pids) - len(beds)):
            if not furniture(p, idx, room, "bed", min_free=0.3, size_m=list(spec["size_m"]), label="bed"):
                note(opts, "no room for a second bed in {}".format(room))
                break


# ----------------------------------------------------------------------------------------------------------------
# common furnishings


def _spot_items(config, idx, spot, kinds):
    return [e for e in idx.find(idx.room_of(spot), kinds) if config["entities"][e].get("location") == spot]


def _place_item(p, idx, config, item, kind):
    """One furnishing by its placement rule; returns the new id or None."""
    room = (rooms_of(config, item["room"]) or [None])[0] if item.get("room") else None
    props = dict(item.get("props") or {})
    place = item["place"]
    if place in ("outside_front_door", "inside_front_door"):
        _, inside, outside, conn = front_door(config)
        target = outside if place == "outside_front_door" else inside
        if target is None:
            return None
        record = p.reg.make(kind, target, **props)
        record["pos"] = list(conn["cells"][target])
        geometry.along_wall(config["rooms"][target], record)      # lies along the wall, on the grid
        eid = p.new_id(kind)
        config["entities"][eid] = record
        return idx.add(eid)
    if room is None:
        return None
    if place == "furniture":
        anchor = (idx.find(room, (item["near"],)) or [None])[0]
        near = config["entities"][anchor].get("pos") if anchor else None
        return furniture(p, idx, room, kind, near=near, min_free=item.get("min_free"), **props)
    if place == "floor":
        near = None
        if item.get("near") == "front_door":
            _, inside, _, conn = front_door(config)
            near = tuple(conn["cells"][inside]) if inside == room else None
        return floor(p, idx, room, kind, near=near, **props)
    for spot in [e for name in item["spot"] for e in idx.find(room, (name,))]:
        eid = put(p, idx, spot, kind, shelf=item.get("shelf"), **props)
        if eid:
            return eid
    if item.get("fallback") == "floor":
        return floor(p, idx, room, kind, **props)
    return None


def _chair_sets(config, rng, idx):
    variants = [(v, v["weight"]) for v in data()["chair_variants"]["variants"]]
    for room in rooms_of(config, "dining_room", "kitchen"):
        chairs = idx.find(room, ("chair",), where=lambda rec: rec.get("type") == "chair" and rec.get("label") == "chair"
                          and rec.get("location") == room)
        if chairs:
            variant = weighted(rng, variants)
            for eid in chairs:
                config["entities"][eid].update(material=variant["material"], color=variant["color"])


@furnisher("gen.furnishings", order=101)
def furnishings(config, rng, opts, registry):
    if not gen2(opts):
        return
    p, idx = Placer(config, registry, rng), Index(config)
    for item in data()["items"]:
        if rng.random() >= item.get("chance", 1.0) or (item.get("room") and not rooms_of(config, item["room"])):
            continue
        kind = first_known(registry, item["types"])
        if kind is None:
            note(opts, "missing type {} (from {})".format(item["types"][0], item.get("owner", "?")))
            continue
        if item.get("unless_any") or item.get("at_least"):
            room = (rooms_of(config, item["room"]) or [None])[0]
            spots = [e for name in item.get("spot", ()) for e in idx.find(room, (name,))] if room else []
            present = sum(len(_spot_items(config, idx, s, tuple(item.get("unless_any") or item["types"]))) for s in spots)
            if item.get("unless_any") and present:
                continue
            count = max(0, int(item.get("at_least", 1)) - present)
        else:
            lo, hi = item.get("count", [1, 1])
            count = rng.randint(lo, hi)
        for _ in range(count):
            if _place_item(p, idx, config, item, kind) is None:
                note(opts, "no spot for {}".format(item["kind"]))
                break
    _high_chair(config, rng, opts, registry, p, idx)
    _chair_sets(config, rng, idx)


def _high_chair(config, rng, opts, registry, p, idx):
    """One high chair for a household with an infant (always) or a toddler (spec chance), in the dining room or the
    kitchen."""
    roles = {person.get("role") for person in (config.get("people") or {}).values()}
    spec = data()["high_chair"]
    if not ("infant" in roles or ("toddler" in roles and rng.random() < spec["toddler"])):
        return
    kind = first_known(registry, spec["types"])
    if kind is None:
        note(opts, "missing type high_chair")
        return
    for room in rooms_of(config, *spec["rooms"]):
        if idx.find(room, (kind,)) or furniture(p, idx, room, kind, min_free=0.45, owner="household"):
            return
    note(opts, "no room for a high chair")


# ----------------------------------------------------------------------------------------------------------------
# residents in bed


def _bed_cell(config, bed, taken):
    """A cell of the bed nobody else lies on (the people domain's sleeper convention: pos on the bed), the most
    interior first: a sleeper lies in the bed, not on its edge, so their keep-out ring stays on the bed where it can."""
    cells = geometry.footprint(config["entities"][bed])
    inside = set(cells)

    def exposed(cell):
        return sum(1 for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (cell[0] + dx, cell[1] + dy) not in inside)
    for cell in sorted(cells, key=lambda c: (exposed(c), c[1], c[0])):
        if cell not in taken:
            return list(cell)
    return None


@furnisher("gen.residents", order=103)
def residents(config, rng, opts, registry):
    """Infants lie in their crib (asleep if their schedule says so); residents asleep at the start time lie in a bed of
    their bedroom (in_bed, pos on a bed cell, as people.sleeper does). A sleeper without a bed is woken up (noted)."""
    if not gen2(opts):
        return
    people = config.get("people") or {}
    start = datetime.datetime.fromisoformat((config.get("clock") or {}).get("start"))
    idx = Index(config)
    taken = {}
    for pid in sorted(people):
        person = people[pid]
        infant = person.get("role") == "infant"
        if not infant and person.get("awake", True):
            continue
        home = person.get("home_room")
        if infant:
            beds = sorted(e for e in config["entities"] if config["entities"][e].get("owner") == pid
                          and "crib" in [config["entities"][e].get("type")] + list(config["entities"][e].get("is_a", ())))
        else:
            beds = idx.find(home, ("bed",), where=lambda rec: rec.get("type") != "crib" and rec.get("location") == home)
        cell, bed = None, None
        for bed in beds:
            room = idx.room_of(bed)
            cell = _bed_cell(config, bed, taken.setdefault(room, set()))
            if cell:
                break
        if cell is None:
            person.update(awake=True, posture="sitting" if infant else "standing")
            note(opts, "no bed for {} at the start time".format(pid))
            continue
        room = idx.room_of(bed)
        taken[room].add(tuple(cell))
        asleep = asleep_at(person.get("schedule") or [], start)
        person.update(location=room, pos=cell, in_bed=bed, awake=not asleep if infant else False,
                      posture="sleeping" if (asleep or not infant) else "lying")
