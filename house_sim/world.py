"""Procedural topology, rich symbolic properties, and structural validation."""
import copy
import math
import random
import re

from .legacy import make_house as make_legacy_house, validate as validate_legacy

DEFAULT_OBJECT = {
    "category": "object", "material": "plastic", "mass_kg": 0.4,
    "volume_l": 0.5, "capacity_l": 50.0, "size_m": [0.1, 0.1, 0.1],
    "fragile": False, "temperature_c": 21.0, "wetness": 0.0,
    "soil": 0.0, "owner": "resident_1", "home": None,
    "value": 1.0, "sentimental": False, "freshness": 1.0,
    "hazards": [], "affordances": [], "private": False,
    "broken": False, "risk": 0, "water_safe": True,
    "requires_approval": False, "portable": False, "openable": False,
    "accepts_items": False, "locked": False, "open": False,
    "permissions": {},
}


def object_record(label, location, **values):
    data = copy.deepcopy(DEFAULT_OBJECT)
    data.update(label=label, location=location)
    data.update(values)
    return data


def validate(config):
    validate_legacy(config)
    rooms, entities = config["rooms"], config["entities"]
    reachable, frontier = set(), [config["start_room"]]
    while frontier:
        room = frontier.pop()
        if room in reachable: continue
        reachable.add(room)
        frontier.extend(rooms[room]["exits"])
    if reachable != set(rooms):
        raise ValueError("All rooms must be reachable through the house graph")
    for eid, obj in entities.items():
        for key in ("mass_kg", "volume_l", "capacity_l", "soil", "value", "uses", "soil_load"):
            if key in obj and (isinstance(obj[key], bool) or not isinstance(obj[key], (int, float)) or not math.isfinite(obj[key]) or obj[key] < 0):
                raise ValueError(eid + "." + key + " must be finite and nonnegative")
        for key in ("wetness", "freshness"):
            if key in obj and (isinstance(obj[key], bool) or not isinstance(obj[key], (int, float)) or not 0 <= obj[key] <= 1):
                raise ValueError(eid + "." + key + " must be in [0, 1]")
        if "temperature_c" in obj and (isinstance(obj["temperature_c"], bool) or not isinstance(obj["temperature_c"], (int, float)) or not math.isfinite(obj["temperature_c"])):
            raise ValueError(eid + ".temperature_c must be finite")
        if "risk" in obj and (type(obj["risk"]) is not int or obj["risk"] not in range(4)):
            raise ValueError(eid + ".risk must be 0..3")
        for key in ("affordances", "hazards"):
            if key in obj and (not isinstance(obj[key], list) or not all(isinstance(v, str) for v in obj[key])):
                raise ValueError(eid + "." + key + " must be a list of strings")
        if "connects" in obj:
            a = obj["connects"]
            if not isinstance(a, list) or len(a) != 2 or any(x not in rooms for x in a) or a[1] not in rooms[a[0]]["exits"]:
                raise ValueError("Door must join two adjacent rooms: " + eid)
    goal = config.get("goal")
    if goal:
        # A portable container cannot be stored inside its own descendant.
        location = goal["destination"]
        while location in entities:
            if location == goal["object"]:
                raise ValueError("Goal destination is inside the goal object")
            location = entities[location]["location"]


def make_house(room_count=5, floors=1, seed=0, outdoors=False, dwelling="house"):
    config = make_legacy_house(room_count, floors, seed)
    rng = random.Random(seed + 1769)
    config["name"] = "Generated {}-room {}".format(room_count, dwelling)
    config["metadata"].update(dwelling_type=dwelling, fidelity="S", units="metric",
                              geography="unspecified", season=rng.choice(["spring", "summer", "autumn", "winter"]))
    for rid, room in config["rooms"].items():
        room.update(lit=True, private=False, floor_material=rng.choice(["tile", "wood", "carpet"]),
                    ceiling_m=round(rng.uniform(2.3, 3.2), 2), blocked=False)
    if outdoors:
        previous = "room_1"
        for name in ("yard", "garage", "shed", "poolside", "driveway", "curb", "mailbox_area"):
            config["rooms"][name] = {"label": name.replace("_", " "), "type": name, "floor": 0,
                                    "exits": [previous], "outdoor": name not in ("garage", "shed"),
                                    "lit": True, "private": False, "area_m2": 20, "blocked": False}
            config["rooms"][previous]["exits"].append(name)
            previous = name
    for eid, e in list(config["entities"].items()):
        config["entities"][eid] = object_record(e["label"], e["location"], **{k: v for k, v in e.items() if k not in ("label", "location")})
    for i, rid in enumerate(config["rooms"], 1):
        config["entities"]["light_" + str(i)] = object_record("room light", rid, category="light", switchable=True, on=True)
    config["entities"]["sink"].update(category="sink", water_source=True, drain=True, capacity_l=20)
    config["people"] = {"resident_1": {"label": "Resident", "role": "adult", "location": "room_1",
                                       "consent": True, "mood": "neutral", "preferences": {},
                                       "permissions": [], "quiet": False, "schedule": []}}
    config["pets"] = {}
    config["utilities"] = {"power": True, "water": True, "gas": True,
                           "water_l": 1000.0, "detergent_units": 100.0, "wastewater_soil": 0.0}
    config["weather"] = {"condition": rng.choice(["clear", "cloudy"]), "temperature_c": rng.randint(10, 30)}
    config["robot"] = {"battery": 100.0, "max_mass_kg": 15.0, "quiet": False,
                       "camera_clean": True, "network": True, "actuator_ok": True}
    config["events"] = []
    return config


def normalize(config):
    validate(config)
    data = copy.deepcopy(config)
    for eid, original in list(data["entities"].items()):
        record = copy.deepcopy(DEFAULT_OBJECT)
        record.update(original)
        data["entities"][eid] = record
    data.setdefault("people", {})
    data.setdefault("pets", {})
    data.setdefault("utilities", {"power": True, "water": True, "gas": True, "water_l": 1000.0, "detergent_units": 100.0, "wastewater_soil": 0.0})
    data.setdefault("weather", {"condition": "clear", "temperature_c": 21.0})
    data.setdefault("events", [])
    robot = {"battery": 100.0, "max_mass_kg": 15.0, "quiet": False, "camera_clean": True,
             "network": True, "actuator_ok": True}
    robot.update(data.get("robot", {}))
    robot.update(room=data["start_room"], holding=None, stopped=False)
    data["robot"] = robot
    data.setdefault("requester", "resident_1")
    return data
