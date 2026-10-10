"""Furnishers: resident profiles (bedrooms, privacy zones, abilities, schedules and preferences as
data, wake and comfort preferences, household contacts/registry) and personal items (glasses,
hearing aids, mobility aids, a few medicines in the medicine cabinet, thermometer, storybooks and
night lights for children, games, doorbell and intercom).

Profiles are data: schedules only drive behaviour when a scenario activates them
(person.follow_schedule or household.people_sim.schedules_active), and household rules are only
enforced when a scenario adds them to household.rules, so other domains' tasks keep working.
"""
from ...generator.place import Placer
from ...kernel.plugins import furnisher

DRUGS = [("Lisinopril 10 mg", "blood pressure", ["08:00"]), ("Metformin 500 mg", "diabetes", ["08:00", "20:00"]),
         ("Atorvastatin 20 mg", "cholesterol", ["21:00"]), ("Levothyroxine 50 mcg", "thyroid", ["07:00"]),
         ("Sertraline 50 mg", "mood", ["08:00"]), ("Amlodipine 5 mg", "blood pressure", ["09:00"])]


def bedroom_owners(config):
    """Same assignment rule as the textiles domain: the primary bedroom for the adults, then one room each."""
    people = config.get("people", {})
    adults = sorted(pid for pid, p in people.items() if p.get("role", "adult") == "adult")
    others = sorted(pid for pid, p in people.items() if p.get("role") in ("child", "teen", "elderly", "infant", "toddler"))
    rooms = config["rooms"]
    masters = sorted(r for r in rooms if rooms[r]["type"] == "master_bedroom")
    singles = sorted(r for r in rooms if rooms[r]["type"] == "bedroom")
    owners = {}
    if masters:
        owners[masters[0]] = adults[:2] or others[:1]
        rest = adults[2:] + (others if adults else others[1:])
    else:
        rest = adults + others
    for rid, pid in zip(singles, rest):
        owners[rid] = [pid]
    return owners


def _home_rooms(config):
    result = {}
    for rid, pids in sorted(bedroom_owners(config).items()):
        for pid in pids:
            result.setdefault(pid, rid)
    return result


def _schedule(role, age, rng, home, office):
    if role in ("child", "toddler", "infant"):
        bed = "19:30" if age < 6 else "20:30"
        blocks = [{"from": bed, "to": "07:00", "activity": "sleeping", "room": home}]
        if age >= 5:
            blocks.append({"from": "08:00", "to": "15:00", "activity": "away", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "what": "school"})
        elif age < 4:
            blocks.append({"from": "13:00", "to": "15:00", "activity": "napping", "room": home})
        return blocks
    if role == "elderly":
        return [{"from": "21:30", "to": "07:00", "activity": "sleeping", "room": home},
                {"from": "13:30", "to": "14:30", "activity": "napping", "room": home}]
    work_from_home = rng.random() < 0.35
    blocks = [{"from": rng.choice(["22:00", "22:30", "23:00"]), "to": rng.choice(["06:00", "06:30", "07:00"]), "activity": "sleeping", "room": home}]
    if work_from_home and office:
        blocks.append({"from": "09:00", "to": "17:00", "activity": "working", "room": office, "days": ["Mon", "Tue", "Wed", "Thu", "Fri"]})
    else:
        blocks.append({"from": "08:30", "to": "17:30", "activity": "away", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "what": "work"})
    return blocks


@furnisher("people_profiles", order=92)
def people_profiles(config, rng, opts, registry):
    people = config.get("people", {})
    homes = _home_rooms(config)
    rooms = config["rooms"]
    offices = sorted(r for r in rooms if rooms[r]["type"] == "office")
    living = config.get("start_room")
    household = config.setdefault("household", {})
    household.setdefault("rules", [])
    routines = household.setdefault("routines", {})
    routines.setdefault("quiet_hours", {"from": "22:00", "to": "07:00", "max_db": 45})
    household.setdefault("address", config.get("metadata", {}).get("address", "100 Example Lane"))
    household.setdefault("registry", {})
    household.setdefault("expected_visitors", [])
    household.setdefault("signature_authorized", False)
    household.setdefault("verification_answer", rng.choice(["Biscuit", "blue door", "Maple Street", "Pickles"]))
    contacts = household.setdefault("contacts", {"emergency": "911", "poison_control": "1-800-222-1222"})
    adults = sorted(pid for pid, p in people.items() if p.get("role", "adult") == "adult")
    for pid in sorted(people):
        p = people[pid]
        role = p.get("role", "adult")
        age = float(p.get("age") or {"child": 8, "elderly": 80}.get(role, 40))
        home = homes.get(pid) or living
        p.setdefault("home_room", home)
        p.setdefault("privacy_zones", [home] if home and home != living else [])
        p.setdefault("facing_deg", 90.0)
        abilities = dict({"mobility": "full", "vision": "full", "hearing": "full", "cognition": "full", "balance": "good",
                          "strong_side": "both", "grip": "normal"}, **(p.get("abilities") or {}))
        if role == "elderly":
            abilities.setdefault("balance", "poor" if abilities.get("mobility") != "full" else "good")
            if abilities.get("mobility") != "full":
                abilities["balance"] = "poor"
        p["abilities"] = abilities
        prefs = p.setdefault("preferences", {})
        prefs.setdefault("wake_method", rng.choice(["voice", "light", "touch"]))
        p.setdefault("comfort", {"temp_c": [22.5, 25.0] if role == "elderly" else [20.0, 23.5], "min_light": 0.6 if role == "elderly" else 0.45,
                                 "min_seat_m": 0.45 if role == "elderly" else 0.35})
        p.setdefault("light_sleeper", rng.random() < 0.3)
        p.setdefault("schedule", _schedule(role, age, rng, home, offices[0] if offices else None))
        if role == "adult":
            p.setdefault("has_phone", True)
            p.setdefault("reachable_by_phone", True)
        elif role == "child" and age >= 12:
            p.setdefault("has_phone", True)
        household["registry"].setdefault(pid, {"face": True, "voice": True})
        if role == "elderly":
            contacts.setdefault("caregiver", {"person": adults[0] if adults else None, "label": "family caregiver"})
            p.setdefault("guardians", [])
    return config


def _first(p, types, room=None):
    for t in types:
        found = p.find(t, room=room) if room else p.find(t)
        if found:
            return found[0]
    return None


def _nightstand(p, rid):
    return _first(p, ["nightstand", "side_table", "dresser", "desk"], rid)


@furnisher("people_items", order=94)
def people_items(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    people = config.get("people", {})
    rooms = config["rooms"]
    homes = _home_rooms(config)
    living = config.get("start_room")
    med_cabinet = next((e for e in p.find("upper_cabinet") if config["entities"][e].get("label") == "medicine cabinet"), None)
    for pid in sorted(people):
        person = people[pid]
        role = person.get("role", "adult")
        age = float(person.get("age") or {"child": 8, "elderly": 80}.get(role, 40))
        home = homes.get(pid)
        stand = _nightstand(p, home) if home else None
        name = str(person.get("label", pid)).split(" (")[0]
        if role in ("adult", "elderly") and (age >= 45 or role == "elderly"):
            target = stand or _first(p, ["side_table", "coffee_table"], living)
            if target:
                p.on(target, "reading_glasses", owner=pid, home=target, color=rng.choice(["black", "tortoiseshell", "red", "clear"]))
        if role == "elderly":
            ab = person.get("abilities") or {}
            if ab.get("hearing") == "reduced" and stand:
                case = p.on(stand, "hearing_aid_case", owner=pid, home=stand)
                if case:
                    p.inside(case, "hearing_aid", owner=pid, home=case, label="{}'s hearing aid".format(name))
            aid = {"cane": "cane", "walker": "walker", "rollator": "rollator"}.get(ab.get("mobility"))
            if aid and person.get("location") in rooms:
                near = person.get("pos")
                kind = aid if aid != "cane" else "quad_cane"
                eid = p.floor_item(person["location"], kind, near=near, owner=pid, home=person["location"])
                if eid and kind == "rollator":
                    config["entities"][eid]["settings"] = {"brakes": "locked", "folded": False}
        if role in ("adult", "elderly") and med_cabinet and rng.random() < (0.9 if role == "elderly" else 0.35):
            drug, purpose, times = DRUGS[rng.randrange(len(DRUGS))]
            p.inside(med_cabinet, "prescription_bottle", owner=pid, patient=pid, drug=drug, home=med_cabinet,
                     label_text="Rx {} - {} - take as directed".format(drug, name), patient_name=name,
                     secret_fields=["patient", "drug", "owner"], purpose=purpose)
            person.setdefault("med_schedule", []).append({"med": drug, "times": times, "window_min": 60})
        if role == "child" and home and home != living:
            spot = stand or _first(p, ["toy_box", "bookshelf", "dresser"], home)
            if spot:
                rec = config["entities"][spot]
                title = rng.choice(["Goodnight Moon", "The Very Hungry Caterpillar", "Where the Wild Things Are", "Guess How Much I Love You", "Owl Babies"])
                if rec.get("container") and not rec.get("surface"):
                    p.inside(spot, "storybook", owner=pid, home=spot, text=title)
                else:
                    p.on(spot, "storybook", owner=pid, home=spot, text=title)
            if age < 9:
                p.mount(home, "night_light", owner=pid, height=0.3)
    if any(v.get("role") == "child" for v in people.values()) and living in rooms:
        shelf = _first(p, ["bookshelf", "cabinet", "tv_stand"], living)
        if shelf:
            rec = config["entities"][shelf]
            if rec.get("container"):
                p.inside(shelf, "board_game", owner="household", home=shelf, shelf=0)
                p.inside(shelf, "playing_cards", owner="household", home=shelf, shelf=0)
            else:
                p.on(shelf, "playing_cards", owner="household", home=shelf)
    vanity = next((e for e in p.find("cabinet") if config["entities"][e].get("label") == "vanity cabinet"), None)
    if vanity:
        p.inside(vanity, "body_thermometer", owner="household", home=vanity, shelf=0)
    front = None
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        if rec.get("type") == "exterior_door" and rec.get("label") in ("front door", "apartment door"):
            front = eid
            break
    if front:
        pair = config["entities"][front].get("connects") or []
        outside = [r for r in pair if rooms.get(r, {}).get("outdoor") or rooms.get(r, {}).get("shared")]
        inside = [r for r in pair if r not in outside]
        if outside:
            kind = "video_doorbell" if rng.random() < 0.4 else "doorbell"
            p.mount(outside[0], kind, height=1.2, door=front, cell=_beside_door(p, config, front, outside[0]))
        if inside and (rooms[inside[0]].get("type") == "entryway") and config.get("metadata", {}).get("dwelling_type") == "apartment":
            p.mount(inside[0], "intercom", height=1.4, door=front, cell=_beside_door(p, config, front, inside[0]))
    return config


def _beside_door(p, config, door, rid):
    """A wall cell right beside the door's doorway cell in rid (a doorbell or intercom sits next to the door frame at
    1.2-1.4 m, above the swing), free of blocking furniture and with a walkable cell in front; None lets the Placer pick."""
    from ...kernel import geometry
    conn = next((c for _, c in sorted(config.get("connections", {}).items()) if c.get("door") == door and rid in c.get("cells", {})), None)
    if conn is None:
        return None
    room = config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    dx, dy = conn["cells"][rid]
    state = p.state()
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    doors = {tuple(c["cells"][rid]) for c in config.get("connections", {}).values() if rid in c.get("cells", {})}
    on_wall = [(x, y) for x, y in ((dx + 1, dy), (dx - 1, dy), (dx, dy + 1), (dx, dy - 1), (dx + 2, dy), (dx - 2, dy), (dx, dy + 2), (dx, dy - 2))
               if 0 <= x < cols and 0 <= y < rows and (x in (0, cols - 1) or y in (0, rows - 1))]
    for cell in on_wall:
        if cell in blocked or cell in doors:
            continue
        if geometry.near_cells([cell], room, 1) & reach:
            return [cell[0], cell[1]]
    return None
