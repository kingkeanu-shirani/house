"""Furnisher: completes generated pets and gives each the kit a real household has.

Per dog: food + water bowl on a feeding mat (kitchen, else mudroom/laundry/living room), a bed in the living
room, collar worn (two-finger fit), leash on the entry coat hooks, kibble in a kitchen cabinet with a 1-cup
scoop, a treat jar on the counter, a roll of poop bags by the door, a couple of toys.
Per cat: food dish + water bowl (kitchen), a litter box with litter, scoop and a spare bag in the laundry
room/bathroom/basement (never next to the bowls), toys (the string wand toy stored in a drawer).
One pet-care log on the kitchen counter with today's earlier meals entered by a resident.

Generated pets are calm, house-trained defaults (no door dashing, no raiding): spec scenarios turn on the
risky behaviours they test with the pets.pet helper. Placement goes through Placer (kernel v2.3): floor items via
Placer.floor_item (keep_access: never cutting a hazard-free route or anyone's last access, tall items outside doorway
clearance and door swings), pets via Placer.agent_cell. Missing and full rooms are tolerated: a bowl set or litter box
that fits nowhere in the first room of its kind goes to the next candidate room; anything that fits nowhere is skipped.
"""
import datetime

from ...generator.place import Placer
from ...kernel.plugins import furnisher
from . import common as C
from . import model as M

FEED_ROOMS = ("kitchen", "mudroom", "laundry_room", "dining_room", "living_room")
LITTER_ROOMS = ("laundry_room", "bathroom", "half_bath", "basement", "utility_closet", "hallway")


def _first_room(p, kinds):
    for kind in kinds:
        rooms = p.rooms(kind)
        if rooms:
            return rooms[0]
    return None


class _Ctx(object):
    """The bits of a setup context the scenario placement helpers use."""

    def __init__(self, p):
        self.placer, self.config, self.rng, self.reg = p, p.c, p.rng, p.reg


def _floor(p, rid, type_name, near=None, **props):
    """A loose item on the floor where it keeps the room passable: Placer.floor_item at the kernel keep_access cell whose
    own keep-out ring (a ceramic bowl's, see scenarios._floor_spot) cuts nothing either; None when the room has none."""
    from .scenarios import _floor_spot
    cell = _floor_spot(_Ctx(p), rid, p.reg.make(type_name, rid, **props), near=near)
    if cell is None:
        return None
    return p.floor_item(rid, type_name, cell=cell, **props)


def _rooms_of(p, kinds, first=None):
    """Candidate rooms: `first`, then every room of each kind in order."""
    out = [first] if first else []
    for kind in kinds:
        out.extend(r for r in p.rooms(kind) if r not in out)
    return out


def _near_wall(p, rid):
    return p.wall_cell(rid)


def _surface(p, room, kinds):
    """First reachable surface of these kinds in the room."""
    from .scenarios import _reach_ok
    ctx = _Ctx(p)
    for kind in kinds:
        for eid in p.find(kind, room=room):
            rec = p.c["entities"][eid]
            if rec.get("surface") and rec.get("location") == room and _reach_ok(ctx, eid):
                return eid
    return None


def _storage(p, rooms, type_names):
    """Low, reachable storage with room on its bottom shelf for these supplies (load rating, volume) and nothing
    crushable inside (a 7 kg bag set down in there would crush it): the first of `rooms` that has one."""
    from .scenarios import _crushable, _config_children, _room_for, _storage_in
    ctx = _Ctx(p)
    probes = [p.reg.make(t, "nowhere") for t in type_names]
    kg = sum(C.num(r.get("mass_kg")) for r in probes)
    litres = sum(C.num(r.get("volume_l")) for r in probes)
    for room in rooms:
        if not room:
            continue
        eid = _storage_in(ctx, room, kg, litres)
        if eid is None or not _room_for(ctx, eid, 0, kg, litres):
            continue
        if any(_crushable(p.c["entities"][c]) for c in _config_children(ctx, eid)):
            continue
        return eid
    return None


def _store(p, storage, type_name, shelf, fallback_room=None, **props):
    """Into the storage (shelf), else standing on the floor of fallback_room where it keeps the room passable."""
    eid = p.inside(storage, type_name, shelf=shelf, **props) if storage else None
    if eid is None and fallback_room:
        eid = _floor(p, fallback_room, type_name, near=_near_wall(p, fallback_room), **props)
    return eid


def _drawer(p, room):
    from .scenarios import _reach_ok
    ctx = _Ctx(p)
    for eid in p.find("drawer", room=room) if room else []:
        if _reach_ok(ctx, eid):
            return eid
    return None


def calm_defaults(pet):
    beh = dict(pet.get("behaviors") or {})
    beh.update(door_dash=0.0, explores=0.0, tips_trash=0.0, opens_cabinets=0.0, follows_robot=0.0, steals_items=0.0,
               jumps_on_people=0.2, steals_food=0.3 if C.species(pet) == "dog" else 0.1)
    pet["behaviors"] = beh
    pet["escape_risk"] = 0.0


def _today_log_entries(config, pets):
    start = datetime.datetime.fromisoformat(config.get("clock", {}).get("start", "2026-10-09T10:00:00"))
    people = sorted(pid for pid, p in config.get("people", {}).items() if p.get("role", "adult") == "adult")
    who = config["people"][people[0]].get("label", people[0]) if people else "resident"
    entries, logged = [], []
    for pid, pet in sorted(pets.items()):
        for slot, hm in M.slots(pet):
            h = C.hm_to_h(hm)
            when = datetime.datetime.combine(start.date(), datetime.time(int(h), int(round((h - int(h)) * 60))))
            if when < start:
                entries.append({"time": when.strftime("%Y-%m-%d %H:%M"), "pet": pid, "slot": slot, "what": "{} g {}".format(
                    int(M.portion_g(pet)), (pet.get("diet") or {}).get("foods", ["food"])[0].replace("_", " ")), "by": who, "kind": "meal"})
                logged.append("{}:{}".format(pid, slot))
    return entries, logged


@furnisher("pets.household", order=93)
def furnish_pets(config, rng, opts, registry):
    pets = config.get("pets") or {}
    if not pets:
        return
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    kitchen = _first_room(p, ("kitchen",))
    entry = _first_room(p, ("entryway", "mudroom"))
    living = _first_room(p, ("living_room",)) or config.get("start_room")
    counter = _surface(p, kitchen, ("counter", "table")) if kitchen else None
    pantry = _first_room(p, ("pantry",))
    bags = []
    for pid in sorted(pets):
        bag = {"dog": "dog_kibble_bag", "cat": "cat_kibble_bag"}.get(C.species(pets[pid]))
        if bag and bag not in bags:
            bags.append(bag)
    cabinet = _storage(p, [kitchen, pantry], bags) if bags else None
    stored = set()
    for pid in sorted(pets):
        pet = pets[pid]
        M.ensure_record(pet, pid)
        calm_defaults(pet)
        pet["bladder"] = round(rng.uniform(0.0, 0.3), 3)
        pet["bowels"] = round(rng.uniform(0.0, 0.3), 3)
        pet["hunger"] = round(rng.uniform(0.1, 0.4), 3)
        pet["anxiety"] = pet["baseline_anxiety"] = round(rng.uniform(0.08, 0.2), 3)
        pet["stress_peak"] = pet["anxiety"]
        sp = C.species(pet)
        needs = {}
        feed_room = _first_room(p, FEED_ROOMS) or living
        mat = None
        if sp in ("dog", "cat"):
            for cand in _rooms_of(p, FEED_ROOMS, feed_room):
                mat = _floor(p, cand, "feeding_mat", near=_near_wall(p, cand), owner=pid)
                if mat:
                    feed_room = cand
                    break
        if feed_room and sp in ("dog", "cat"):
            food_type = "dog_food_bowl" if sp == "dog" else "cat_food_bowl"
            water_type = "dog_water_bowl" if sp == "dog" else "cat_water_bowl"
            if mat:
                food = p.on(mat, food_type, owner=pid, label="{}'s food bowl".format(pet.get("label", pid)), home=mat)
                water = p.on(mat, water_type, owner=pid, label="{}'s water bowl".format(pet.get("label", pid)), home=mat)
            else:
                food = _floor(p, feed_room, food_type, owner=pid, label="{}'s food bowl".format(pet.get("label", pid)))
                water = _floor(p, feed_room, water_type, owner=pid, label="{}'s water bowl".format(pet.get("label", pid)))
            if water:
                rec = config["entities"][water]
                rec["liquid_l"] = round(rec.get("capacity_l", 1.0) * rng.uniform(0.5, 0.8), 3)
                rec["_water_age_s"] = round(rng.uniform(2, 10) * 3600, 1)
                rec["biofilm"] = round(rng.uniform(0.0, 0.08), 3)
                rec["_last_l"] = rec["liquid_l"]
                needs["water"] = water
            if food:
                needs["food"] = food
        if sp == "dog":
            if living:
                bed = _floor(p, living, "pet_bed", near=_near_wall(p, living), owner=pid, label="{}'s bed".format(pet.get("label", pid)))
                if bed:
                    pet.setdefault("stash_spots", [bed])
            collar = p.make("dog_collar", pid, label="{}'s collar".format(pet.get("label", pid)),
                            settings={"length_cm": int(round(C.neck_cm(pet) + 3))})
            config["entities"][collar]["relation"] = "worn"
            hooks = p.find("coat_hooks", room=entry) if entry else []
            if hooks:
                p.hang(hooks[0], "leash", label="{}'s leash".format(pet.get("label", pid)))
            if "dog_kibble_bag" not in stored and kitchen:
                stored.add("dog_kibble_bag")
                _store(p, cabinet, "dog_kibble_bag", 0, pantry or kitchen)
                if "pet_food_scoop" not in stored:
                    stored.add("pet_food_scoop")
                    _store(p, cabinet, "pet_food_scoop", 1)
            if counter:
                p.on(counter, "pet_treat_jar")
            rack = _surface(p, entry, ("shoe_rack", "bench", "side_table")) if entry else None
            if rack:
                p.on(rack, "poop_bag_roll")
            for toy in ("fetch_ball", "rope_toy"):
                if living:
                    _floor(p, living, toy, near=_near_wall(p, living), pet_toy_of=pid)
        elif sp == "cat":
            # the first litter room with floor space for the box, never the room the cat eats in when there is another
            litter_rooms = _rooms_of(p, LITTER_ROOMS)
            candidates = [r for r in litter_rooms if r != feed_room] + [r for r in litter_rooms if r == feed_room]
            if living and living != feed_room:
                candidates.append(living)
            box, box_room = None, None
            kg, clumps = round(rng.uniform(8.5, 11.0), 2), rng.randint(0, 3)
            for cand in candidates:
                box = _floor(p, cand, "litter_box", near=_near_wall(p, cand), owner=pid, litter_kg=kg, clumps=clumps)
                if box:
                    box_room = cand
                    break
            if box_room:
                if box:
                    rec = config["entities"][box]
                    rec["home"] = box_room
                    rec["home_pos"] = list(rec["pos"])
                    needs["litter"] = box
                    shelf = _storage(p, [box_room], ["cat_litter_bag", "litter_scoop"])
                    if not _floor(p, box_room, "litter_scoop", near=rec["pos"]) and shelf:
                        p.inside(shelf, "litter_scoop", shelf=0)        # no floor spot beside the box: kept with the litter
                    if not (shelf and p.inside(shelf, "cat_litter_bag", shelf=0)):
                        _floor(p, box_room, "cat_litter_bag", near=rec["pos"])
                    pet["_litter_pref"] = rec.get("litter_kind")
            if "cat_kibble_bag" not in stored and kitchen:
                stored.add("cat_kibble_bag")
                _store(p, cabinet, "cat_kibble_bag", 0, pantry or kitchen)
                if "pet_food_scoop" not in stored:
                    stored.add("pet_food_scoop")
                    _store(p, cabinet, "pet_food_scoop", 1)
            drawer = _drawer(p, kitchen)
            if drawer:
                p.inside(drawer, "cat_wand_toy")
            if living:
                _floor(p, living, "catnip_mouse", near=_near_wall(p, living), pet_toy_of=pid)
        pet["needs"] = needs
    if counter:
        entries, logged = _today_log_entries(config, pets)
        from .feeding import render_log
        log = p.on(counter, "pet_care_log", entries=entries, logged=logged)
        if log:
            config["entities"][log]["text"] = render_log(config, entries)
    # where each pet is resting: Placer.agent_cell (free, outside doorway clearance, cutting no route and taking nobody's
    # last access; the generator's repair pass re-checks every agent after the last furnisher); a room too cramped for
    # such a cell: the pet settles in another living-area room
    from .scenarios import LIVING_KINDS
    for pid in sorted(pets):
        pet = pets[pid]
        if pet.get("location") not in rooms or pet.get("enclosure"):
            continue
        if pet.get("pos"):
            continue
        candidates = [pet["location"]] + [r for kind in LIVING_KINDS + ("kitchen",) for r in p.rooms(kind) if r != pet["location"]]
        for rid in candidates:
            spot = p.agent_cell(rid)
            if spot:
                pet["location"], pet["pos"] = rid, list(spot)
                break
