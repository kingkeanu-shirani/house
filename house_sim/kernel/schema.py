"""Structural validation and normalization of household configurations.

Accepts schema_version 1 (the supplied v2 environment's layouts) and 2.
normalize() never mutates its input; it returns a complete runtime state.
"""
import copy
import math
import re

from . import geometry
from .plugins import VALIDATORS
from .registry import BASE_PROPS
from .world import RESERVED_IDS

ID_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
NONNEG = ("mass_kg", "volume_l", "capacity_l", "value", "uses", "soil_load", "liquid_l", "amount", "count")
UNIT = ("wetness", "freshness")

# The robot record before the body model (kernel v2.9). Kept as the reference that body.apply_profile reproduces value
# for value for the default humanoid_v1 profile; normalize itself builds the robot through body.apply_profile, which
# materializes every registered kernel robot field (kernel v3.0, TIER1_INTEGRATION 1.1).
ROBOT_DEFAULTS = {
    "battery": 100.0, "max_mass_kg": 20.0, "max_hand_kg": 8.0, "reach_m": 1.85, "width_m": 0.5, "height_m": 1.6,
    "camera_clean": 1.0, "network": True, "actuator_ok": True, "arms_ok": {"left": True, "right": True},
    "stopped": False, "quiet": False, "privacy_mode": False, "speed_mode": "normal", "posture": "standing",
    "standing_on": None, "fallen": False, "faults": [], "docked": False, "headlamp_on": False,
}


def _fail(message):
    raise ValueError(message)


def validate(config):
    if not isinstance(config, dict):
        _fail("House must be a JSON object.")
    if config.get("schema_version") not in (1, 2):
        _fail("Expected schema_version 1 or 2.")
    rooms, entities = config.get("rooms"), config.get("entities")
    if not isinstance(rooms, dict) or not rooms:
        _fail("rooms must be a nonempty object.")
    if not isinstance(entities, dict):
        _fail("entities must be an object.")
    people, pets = config.get("people", {}) or {}, config.get("pets", {}) or {}
    ids = list(rooms) + list(entities) + list(people) + list(pets)
    if len(ids) != len(set(ids)):
        _fail("Room, entity, person and pet IDs must be distinct.")
    for key in ids:
        if not isinstance(key, str) or not ID_RE.match(key):
            _fail("IDs must use lowercase letters, numbers and underscores: " + repr(key))
        if key in RESERVED_IDS:
            _fail(key + " is a reserved ID.")
    if config.get("start_room") not in rooms:
        _fail("start_room must refer to a room.")
    connections = config.get("connections")
    for rid, room in rooms.items():
        if not isinstance(room, dict):
            _fail(rid + " must be an object.")
        exits = room.get("exits", [])
        if not isinstance(exits, list) or not all(isinstance(x, str) for x in exits):
            _fail(rid + ".exits must be a list of room IDs.")
        if len(exits) != len(set(exits)):
            _fail(rid + " has duplicate exits.")
        for other in exits:
            if other not in rooms or other == rid:
                _fail("Invalid exit from " + rid)
            if rid not in rooms[other].get("exits", []):
                _fail("Connections must be two-way: {}, {}".format(rid, other))
        size = room.get("size_m")
        if size is not None and (not isinstance(size, list) or len(size) < 2 or not all(isinstance(v, (int, float)) and v > 0 for v in size[:2])):
            _fail(rid + ".size_m must be positive [width, depth]")
    if connections is not None:
        for cid, conn in connections.items():
            pair = conn.get("rooms")
            if not isinstance(pair, list) or len(pair) != 2 or any(r not in rooms for r in pair) or pair[0] == pair[1]:
                _fail("Connection {} must join two rooms".format(cid))
    # reachability over exits (or connections)
    adjacency = {r: set(rooms[r].get("exits", [])) for r in rooms}
    for conn in (connections or {}).values():
        a, b = conn["rooms"]
        adjacency[a].add(b)
        adjacency[b].add(a)
    reachable, frontier = set(), [config["start_room"]]
    while frontier:
        room = frontier.pop()
        if room in reachable:
            continue
        reachable.add(room)
        frontier.extend(adjacency[room])
    if reachable != set(rooms):
        _fail("All rooms must be reachable through the house graph")
    valid_locations = set(rooms) | set(entities) | set(people) | set(pets) | {"robot"}
    for eid, entity in entities.items():
        if not isinstance(entity, dict):
            _fail(eid + " must be an object.")
        loc = entity.get("location")
        if not isinstance(loc, str) or loc not in valid_locations:
            _fail("Invalid location for " + eid)
        if loc in entities and not entities[loc].get("accepts_items") and entity.get("relation") not in ("attached", "under", "behind", "part"):
            _fail(loc + " cannot contain objects (" + eid + ")")
        for flag in ("portable", "accepts_items", "openable", "open"):
            if flag in entity and not isinstance(entity[flag], bool):
                _fail("{}.{} must be boolean.".format(eid, flag))
        for key in NONNEG:
            value = entity.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                _fail("{}.{} must be finite and nonnegative".format(eid, key))
        for key in UNIT:
            value = entity.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1):
                _fail("{}.{} must be in [0, 1]".format(eid, key))
        if "temperature_c" in entity and (isinstance(entity["temperature_c"], bool) or not isinstance(entity["temperature_c"], (int, float)) or not math.isfinite(entity["temperature_c"])):
            _fail(eid + ".temperature_c must be finite")
        if "risk" in entity and (type(entity["risk"]) is not int or entity["risk"] not in range(4)):
            _fail(eid + ".risk must be 0..3")
        for key in ("affordances", "hazards", "tags"):
            if key in entity and (not isinstance(entity[key], list) or not all(isinstance(v, str) for v in entity[key])):
                _fail("{}.{} must be a list of strings".format(eid, key))
        if "connects" in entity:
            pair = entity["connects"]
            if not isinstance(pair, list) or len(pair) != 2 or any(x not in rooms for x in pair) or not (pair[1] in adjacency[pair[0]]):
                _fail("Door must join two adjacent rooms: " + eid)
    for eid in entities:
        seen, loc = {eid}, entities[eid]["location"]
        while loc in entities:
            if loc in seen:
                _fail("Containment cycle involving " + eid)
            seen.add(loc)
            loc = entities[loc]["location"]
    goal = config.get("goal")
    if goal is not None:
        if not isinstance(goal, dict):
            _fail("goal must be an object.")
        obj, dest = goal.get("object"), goal.get("destination")
        if not (isinstance(obj, str) and obj in entities and entities[obj].get("portable")):
            _fail("Goal object must be portable.")
        if not (isinstance(dest, str) and (dest in rooms or (dest in entities and entities[dest].get("accepts_items")))):
            _fail("Goal destination must be a room or storage/surface.")
        if obj == dest:
            _fail("Object cannot be its own destination.")
        location = dest
        while location in entities:
            if location == obj:
                _fail("Goal destination is inside the goal object")
            location = entities[location]["location"]
    for _, fn in VALIDATORS:
        fn(config)


def _default_connections(rooms):
    """Create doorway connections (with cells) for layouts that only list exits."""
    connections, used = {}, {}
    for rid in sorted(rooms):
        for other in rooms[rid].get("exits", []):
            a, b = sorted((rid, other))
            cid = "conn_{}__{}".format(a, b)
            if cid in connections:
                continue
            cells = {}
            for r in (a, b):
                cols, rows = geometry.grid_size(rooms[r])
                n = used.get(r, 0)
                used[r] = n + 1
                perimeter = [(x, 0) for x in range(cols)] + [(cols - 1, y) for y in range(1, rows)] + \
                            [(x, rows - 1) for x in range(cols - 2, -1, -1)] + [(0, y) for y in range(rows - 2, 0, -1)]
                cell = perimeter[(n * 3) % len(perimeter)] if perimeter else (0, 0)
                cells[r] = list(cell)
            connections[cid] = {"rooms": [a, b], "kind": "doorway", "width_m": 0.9, "cells": cells}
    return connections


def normalize(config, registry=None):
    validate(config)
    data = copy.deepcopy(config)
    data["schema_version"] = 2
    data.setdefault("metadata", {})
    data.setdefault("clock", {"start": "2026-10-09T07:00:00", "hemisphere": "north"})
    for rid, room in data["rooms"].items():
        room.setdefault("label", rid.replace("_", " "))
        room.setdefault("type", "room")
        room.setdefault("floor", 0)
        room.setdefault("size_m", [4.0, 4.0])
        room.setdefault("exits", [])
        room.setdefault("lit", True)
        room.setdefault("outdoor", False)
        room.setdefault("private", False)
        room.setdefault("off_limits", False)
        room.setdefault("blocked", False)
        room.setdefault("temperature_c", 21.0)
        room.setdefault("humidity", 0.45)
    if not data.get("connections"):
        data["connections"] = _default_connections(data["rooms"])
    else:
        for conn in data["connections"].values():
            a, b = conn["rooms"]
            if b not in data["rooms"][a]["exits"]:
                data["rooms"][a]["exits"].append(b)
            if a not in data["rooms"][b]["exits"]:
                data["rooms"][b]["exits"].append(a)
            conn.setdefault("kind", "doorway")
            conn.setdefault("width_m", 0.9)
            conn.setdefault("cells", {})
            for r in (a, b):
                conn["cells"].setdefault(r, [0, 0])
    # doors referenced by connections
    for cid, conn in data["connections"].items():
        if conn.get("door") is None:
            for eid, entity in data["entities"].items():
                if entity.get("connects") and set(entity["connects"]) == set(conn["rooms"]):
                    conn["door"] = eid
                    break
    for eid, original in list(data["entities"].items()):
        if registry is not None and original.get("type") and registry.known(original["type"]):
            record = copy.deepcopy(registry.resolve(original["type"]))
            record["is_a"] = registry.ancestors(original["type"])
        else:
            record = copy.deepcopy(BASE_PROPS)
            record.setdefault("is_a", [])
            record["type"] = original.get("type", "object")
        record.update(copy.deepcopy(original))
        if isinstance(record.get("soil"), (int, float)):
            record["soil"] = {"dirt": float(record["soil"])} if record["soil"] else {}
        if record.get("accepts_items") and "container" not in record:
            record["container"] = bool(record.get("openable"))
        if record.get("location") in data["entities"] and "relation" not in original:
            parent = data["entities"][record["location"]]
            record["relation"] = "in" if parent.get("container", parent.get("openable")) else "on"
        data["entities"][eid] = record
    data.setdefault("people", {})
    data.setdefault("pets", {})
    for pid, person in data["people"].items():
        person.setdefault("label", pid.replace("_", " "))
        person.setdefault("role", "adult")
        person.setdefault("authority", 2 if person["role"] == "adult" else 0)
        person.setdefault("consent", True)
        person.setdefault("awake", True)
        person.setdefault("posture", "standing")
        person.setdefault("mood", "neutral")
        person.setdefault("permissions", [])
        person.setdefault("preferences", {})
        person.setdefault("location", data["start_room"])
    for pid, pet in data["pets"].items():
        pet.setdefault("label", pid.replace("_", " "))
        pet.setdefault("species", "dog")
        pet.setdefault("posture", "standing")
        pet.setdefault("location", data["start_room"])
    data.setdefault("utilities", {})
    utilities = {"power": True, "water": True, "gas": True, "main_water_open": True, "main_gas_open": True,
                 "water_l": 100000.0, "detergent_units": 100.0, "wastewater_soil": 0.0, "indoor_temp_c": 21.0}
    utilities.update(data["utilities"])
    data["utilities"] = utilities
    data.setdefault("weather", {"condition": "clear", "temperature_c": 18.0, "wind_mps": 2.0})
    data.setdefault("events", [])
    data.setdefault("household", {})
    data.setdefault("user_simulator", {})
    # the robot body (kernel v3.0): every materialized robot field from its profile default, then the config's own
    # fields, legacy fault strings normalized, setup faults applied, arms_ok mirroring arm_health, and the pack
    # temperature (the start room's), power state and lowest gauge derived (body.apply_profile)
    from . import body
    robot = body.apply_profile(data.get("robot") or {}, data)
    robot.setdefault("room", data["start_room"])
    if robot["room"] not in data["rooms"]:
        robot["room"] = data["start_room"]
    robot.setdefault("hands", {"left": None, "right": None})
    for hand in ("left", "right"):
        robot["hands"].setdefault(hand, None)
    data["robot"] = robot
    # positions: entities directly in rooms, the robot, people and pets
    state_view = {"rooms": data["rooms"], "entities": data["entities"], "people": data["people"], "pets": data["pets"], "robot": robot}
    for eid in sorted(data["entities"]):
        entity = data["entities"][eid]
        if entity.get("location") in data["rooms"] and not entity.get("pos") and "connects" not in entity:
            cell = geometry.free_floor_cell(state_view, entity["location"], ignore=(eid,))
            entity["pos"] = cell or [0, 0]
    for group in ("people", "pets"):
        for aid in sorted(data[group]):
            agent = data[group][aid]
            if not agent.get("pos"):
                agent["pos"] = geometry.free_floor_cell(state_view, agent["location"]) or [0, 0]
    if not robot.get("pos"):
        robot["pos"] = geometry.free_floor_cell(state_view, robot["room"]) or [0, 0]
    held = [eid for eid, e in data["entities"].items() if e.get("location") == "robot" and e.get("relation") != "worn"]
    in_hands = [v for v in robot["hands"].values() if v]
    if sorted(set(held)) != sorted(set(in_hands)):
        _fail("Entities located on the robot must match robot.hands")
    data.setdefault("requester", next(iter(sorted(data["people"])), "resident_1"))
    return data
