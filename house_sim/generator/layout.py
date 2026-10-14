"""Structural layout: rooms, floors, stairs, doors, windows, lights, switches, floors,
outdoor zones and the robot dock. Furniture and domain items come from furnishers.
"""
import random

from ..kernel import geometry

SIZES = {  # width x depth in meters (jittered)
    "entryway": (2.5, 3.0), "mudroom": (2.5, 3.0), "hallway": (1.5, 6.0), "living_room": (5.0, 6.0),
    "kitchen": (4.0, 5.0), "dining_room": (4.0, 4.5), "bedroom": (3.5, 4.0), "master_bedroom": (4.5, 5.0),
    "nursery": (3.0, 3.5), "guest_room": (3.5, 4.0), "bathroom": (2.5, 3.0), "half_bath": (1.5, 2.0),
    "laundry_room": (2.5, 3.0), "office": (3.0, 3.5), "playroom": (3.5, 4.0), "gym": (3.5, 4.0),
    "craft_room": (3.0, 3.5), "pantry": (1.5, 2.0), "coat_closet": (1.0, 1.5), "linen_closet": (1.0, 1.5),
    "utility_closet": (1.5, 1.5), "basement": (6.0, 8.0), "attic": (5.0, 6.0), "garage": (6.0, 6.5),
    "shed": (2.5, 3.0), "porch": (3.0, 2.0), "front_yard": (10.0, 8.0), "backyard": (12.0, 10.0),
    "patio": (4.0, 4.0), "deck": (4.0, 4.0), "driveway": (3.5, 10.0), "sidewalk": (10.0, 1.5),
    "garden": (6.0, 4.0), "poolside": (8.0, 6.0), "balcony": (3.0, 1.5), "building_corridor": (2.0, 10.0),
    "elevator": (1.5, 1.5), "lobby": (4.0, 4.0), "mail_room": (2.0, 3.0), "shared_laundry": (3.0, 4.0),
}
OUTDOOR = {"porch", "front_yard", "backyard", "patio", "deck", "driveway", "sidewalk", "garden", "poolside", "balcony"}
TERRAIN = {"porch": "wood", "front_yard": "grass", "backyard": "grass", "patio": "stone", "deck": "wood",
           "driveway": "concrete", "sidewalk": "concrete", "garden": "soil", "poolside": "stone", "balcony": "concrete",
           "garage": "concrete", "shed": "wood", "basement": "concrete", "attic": "plywood"}
INDOOR_FLOORS = {"bathroom": "tile", "half_bath": "tile", "kitchen": "tile", "laundry_room": "tile", "entryway": "tile",
                 "mudroom": "tile", "living_room": None, "bedroom": None, "master_bedroom": "carpet", "nursery": "carpet",
                 "guest_room": None, "office": None, "playroom": "carpet", "gym": "rubber", "craft_room": "laminate",
                 "dining_room": "hardwood", "hallway": None, "pantry": "tile", "lobby": "stone", "building_corridor": "carpet",
                 "elevator": "laminate", "mail_room": "tile", "shared_laundry": "tile"}
WINDOWLESS = {"hallway", "pantry", "coat_closet", "linen_closet", "utility_closet", "half_bath", "elevator",
              "building_corridor", "mail_room", "shared_laundry", "garage", "attic"}
PRIVATE_DOOR = {"bedroom", "master_bedroom", "bathroom", "half_bath", "guest_room", "nursery", "office"}
CLOSED_DOOR = {"coat_closet", "linen_closet", "utility_closet", "pantry", "bathroom", "half_bath"}
FEATURES = {"garage", "shed", "pool", "basement", "attic", "patio", "deck", "porch", "garden", "driveway",
            "mudroom", "office", "playroom", "gym", "craft_room", "pantry", "laundry_room", "guest_room", "nursery",
            "dining_room", "elevator", "shared_laundry", "balcony", "stairs", "outdoor", "half_bath", "lobby", "mail_room"}


class Builder:
    def __init__(self, rng, registry):
        self.rng, self.reg = rng, registry
        self.rooms, self.connections, self.entities = {}, {}, {}
        self.counts = {}
        self._perimeter_used = {}

    def add_room(self, room_type, floor, label=None, size=None, **extra):
        self.counts[room_type] = self.counts.get(room_type, 0) + 1
        rid = "{}_{}".format(room_type, self.counts[room_type])
        w, d = size or SIZES.get(room_type, (3.0, 3.0))
        jitter = 0.0 if room_type in ("elevator", "hallway", "coat_closet", "linen_closet") else 0.15
        w = round(max(1.0, w * self.rng.uniform(1 - jitter, 1 + jitter)) * 2) / 2
        d = round(max(1.0, d * self.rng.uniform(1 - jitter, 1 + jitter)) * 2) / 2
        outdoor = room_type in OUTDOOR
        floor_material = TERRAIN.get(room_type) or INDOOR_FLOORS.get(room_type) or self.rng.choice(["hardwood", "carpet", "laminate"])
        room = {"label": label or room_type.replace("_", " ").title(), "type": room_type, "floor": floor,
                "size_m": [w, d], "ceiling_m": None if outdoor else round(self.rng.uniform(2.4, 3.0), 2),
                "floor_material": floor_material, "outdoor": outdoor, "exits": [], "private": False,
                "off_limits": False, "blocked": False, "temperature_c": 21.0, "humidity": 0.45}
        if outdoor:
            room["terrain"] = floor_material
            room["slope_deg"] = self.rng.choice([0, 0, 0, 3, 6]) if room_type in ("front_yard", "backyard", "garden") else 0
        room.update(extra)
        self.rooms[rid] = room
        return rid

    def _wall_cell(self, rid, span=1):
        room = self.rooms[rid]
        cols, rows = geometry.grid_size(room)
        perimeter = [(x, 0) for x in range(cols)] + [(cols - 1, y) for y in range(1, rows)] + \
                    [(x, rows - 1) for x in range(cols - 2, -1, -1)] + [(0, y) for y in range(rows - 2, 0, -1)]
        if span > 1:
            # a wide (overhead) door: its threshold run of `span` cells must fit one wall, clear of the corners
            lo, hi = span // 2, span - span // 2
            fits = [c for c in perimeter if
                    (c[1] in (0, rows - 1) and 1 <= c[0] - lo and c[0] + hi <= cols - 1) or
                    (c[0] in (0, cols - 1) and 1 <= c[1] - lo and c[1] + hi <= rows - 1 and c[1] not in (0, rows - 1))]
            perimeter = fits or perimeter
        used = self._perimeter_used.setdefault(rid, [])
        best, best_gap = None, -1
        for cell in perimeter:
            gap = min([geometry.chebyshev(cell, u) for u in used] or [99])
            if gap > best_gap:
                best, best_gap = cell, gap
        used.append(best)
        return list(best)

    def connect(self, a, b, kind="doorway", door_type=None, width=None, door_props=None, **extra):
        cid = "conn_{}__{}".format(*sorted((a, b)))
        n = 1
        while cid in self.connections:
            n += 1
            cid = "conn_{}__{}_{}".format(*(sorted((a, b)) + [n]))
        span = max(1, int(round((width or 0.85) / geometry.CELL))) if kind == "garage" else 1
        cells = {a: self._wall_cell(a, span), b: self._wall_cell(b, span)}
        conn = {"rooms": [a, b], "kind": kind, "width_m": width or (0.85 if kind != "stairs" else 1.0), "cells": cells, "door": None}
        conn.update(extra)
        self.rooms[a]["exits"].append(b)
        self.rooms[b]["exits"].append(a)
        if door_type:
            door_id = self.unique(door_type if door_type not in ("door",) else "door")
            props = dict(door_props or {})
            record = self.reg.make(door_type, a, **props)
            record["connects"] = [a, b]
            record["pos"] = list(cells[a])
            record["blocks_path"] = False
            if width:
                record["size_m"] = [width, record["size_m"][1], record["size_m"][2]]
            self.entities[door_id] = record
            conn["door"] = door_id
            conn["width_m"] = record["size_m"][0] - 0.05 if kind != "garage" else record["size_m"][0]
        self.connections[cid] = conn
        return cid

    def unique(self, stem):
        n = 1
        while "{}_{}".format(stem, n) in self.entities:
            n += 1
        return "{}_{}".format(stem, n)


def plan_house(rng, opts):
    dwelling = opts.get("dwelling")
    features = set(opts.get("features", ())) | set(opts.get("require", ()))
    unknown = features - FEATURES - set(SIZES)
    if unknown:
        raise ValueError("Unknown house features: " + ", ".join(sorted(unknown)))
    if dwelling is None:
        if features & {"elevator", "shared_laundry", "lobby", "mail_room"}:
            dwelling = "apartment"
        elif features & {"garage", "shed", "pool", "basement", "attic", "driveway", "garden", "deck", "front_yard", "backyard", "porch", "poolside", "stairs"}:
            dwelling = rng.choice(["house", "house", "townhouse"]) if not features & {"pool", "shed", "basement", "attic"} else "house"
        else:
            dwelling = rng.choices(["house", "apartment", "townhouse"], [0.6, 0.25, 0.15])[0]
    if dwelling == "apartment" and features & {"garage", "shed", "pool", "basement", "attic", "driveway", "garden", "front_yard", "backyard", "porch", "poolside", "deck"}:
        raise ValueError("Apartment cannot have " + ", ".join(sorted(features & {"garage", "shed", "pool", "basement", "attic", "driveway", "garden", "front_yard", "backyard", "porch", "poolside", "deck"})))
    if dwelling == "house":
        floors = opts.get("floors") or rng.choices([1, 2, 3], [0.35, 0.5, 0.15])[0]
    elif dwelling == "townhouse":
        floors = opts.get("floors") or rng.choice([2, 3])
    else:
        floors = 1
    if "stairs" in features and floors < 2:
        if dwelling == "apartment":
            raise ValueError("Generated apartments have no internal stairs; use dwelling house or townhouse")
        floors = 2
    bedrooms = opts.get("bedrooms") or (rng.randint(1, 3) if dwelling == "apartment" else rng.randint(2, 4))
    bathrooms = opts.get("bathrooms") or (1 if bedrooms <= 2 else 2)
    plan = {"dwelling": dwelling, "floors": floors, "bedrooms": bedrooms, "bathrooms": bathrooms, "features": set()}
    defaults = {"house": {"garage": 0.7, "porch": 0.8, "patio": 0.55, "deck": 0.3, "shed": 0.4, "pool": 0.15, "garden": 0.5,
                          "basement": 0.3, "attic": 0.25, "mudroom": 0.4, "office": 0.5, "pantry": 0.5, "laundry_room": 0.75,
                          "dining_room": 0.7, "playroom": 0.25, "gym": 0.15, "craft_room": 0.15, "guest_room": 0.3,
                          "nursery": 0.08, "half_bath": 0.4},
                "townhouse": {"garage": 0.5, "porch": 0.6, "patio": 0.7, "garden": 0.3, "office": 0.4, "pantry": 0.3,
                              "laundry_room": 0.6, "dining_room": 0.5, "half_bath": 0.5, "guest_room": 0.15, "playroom": 0.15},
                "apartment": {"balcony": 0.6, "office": 0.3, "shared_laundry": 0.5, "dining_room": 0.3, "pantry": 0.2, "laundry_room": 0.4}}
    for feature, p in defaults[dwelling].items():
        if rng.random() < p:
            plan["features"].add(feature)
    plan["features"] |= features
    if dwelling == "apartment":
        plan["features"] |= {"elevator", "lobby", "mail_room"}
        if "laundry_room" not in plan["features"]:
            plan["features"].add("shared_laundry")
    else:
        plan["features"] |= {"driveway", "outdoor"}
        if "pool" in plan["features"]:
            plan["features"].add("patio")
    if "nursery" in plan["features"]:
        plan["bedrooms"] = max(plan["bedrooms"], 2)
    return plan


def build_structure(seed, opts, registry):
    rng = random.Random("layout:{}".format(seed))
    plan = plan_house(rng, opts)
    b = Builder(rng, registry)
    f = plan["features"]
    top = plan["floors"] - 1
    if plan["dwelling"] == "apartment":
        level = rng.randint(2, 9)
        entry = b.add_room("entryway", level)
        living = b.add_room("living_room", level)
        kitchen = b.add_room("kitchen", level)
        hall = b.add_room("hallway", level)
        b.connect(entry, living)
        b.connect(living, kitchen)
        b.connect(living, hall)
        b.connect(entry, b.add_room("coat_closet", level), door_type="door", door_props={"open": False})
        hub_by_floor = {level: hall}
        ground = level
    else:
        ground = 0
        entry = b.add_room("entryway", 0)
        living = b.add_room("living_room", 0)
        kitchen = b.add_room("kitchen", 0)
        hall = b.add_room("hallway", 0)
        b.connect(entry, living)
        b.connect(entry, hall)
        b.connect(living, kitchen)
        b.connect(entry, b.add_room("coat_closet", 0), door_type="door", door_props={"open": False})
        hub_by_floor = {0: hall}
        for level in range(1, plan["floors"]):
            upper = b.add_room("hallway", level, label="Upstairs hallway" if level == 1 else "Top-floor hallway")
            b.connect(hub_by_floor[level - 1], upper, kind="stairs", width=1.0, steps=14, handrail=True, rise_m=0.18)
            hub_by_floor[level] = upper
    def door_for(room_type):
        props = {"open": room_type not in CLOSED_DOOR}
        if room_type in PRIVATE_DOOR:
            props.update(lockable=True, locked=False, lock_kind="thumb_turn", privacy_lock=True)
        return props
    if "dining_room" in f:
        dining = b.add_room("dining_room", ground)
        b.connect(dining, kitchen)
        b.connect(dining, living)
    bedroom_floor = 1 if plan["floors"] >= 2 else ground
    bath_hub = hub_by_floor[bedroom_floor]
    for i in range(plan["bedrooms"]):
        kind = "master_bedroom" if i == 0 else "bedroom"
        rid = b.add_room(kind, bedroom_floor, label="Primary bedroom" if i == 0 else "Bedroom {}".format(i + 1))
        b.connect(bath_hub, rid, door_type="door", door_props=door_for(kind))
        if i == 0 and plan["bathrooms"] >= 2:
            ensuite = b.add_room("bathroom", bedroom_floor, label="En-suite bathroom")
            b.connect(rid, ensuite, door_type="door", door_props=door_for("bathroom"))
            wardrobe_room = None
    shared_baths = plan["bathrooms"] - (1 if plan["bathrooms"] >= 2 else 0)
    for i in range(max(1, shared_baths)):
        rid = b.add_room("bathroom", bedroom_floor, label="Bathroom" if i == 0 else "Bathroom {}".format(i + 1))
        b.connect(bath_hub, rid, door_type="door", door_props=door_for("bathroom"))
    if "half_bath" in f and bedroom_floor != ground:
        b.connect(hub_by_floor[ground], b.add_room("half_bath", ground, label="Powder room"), door_type="door", door_props=door_for("half_bath"))
    if bedroom_floor != ground or plan["bedrooms"] > 1:
        b.connect(bath_hub, b.add_room("linen_closet", bedroom_floor), door_type="door", door_props={"open": False})
    extra_hub = hub_by_floor[top] if top > (1 if plan["floors"] >= 2 else 0) else bath_hub
    for feature, hub in (("office", hub_by_floor[ground] if rng.random() < 0.5 else bath_hub), ("guest_room", extra_hub),
                         ("nursery", bath_hub), ("playroom", extra_hub), ("gym", extra_hub), ("craft_room", extra_hub)):
        if feature in f:
            rid = b.add_room(feature, b.rooms[hub]["floor"])
            b.connect(hub, rid, door_type="door", door_props=door_for(feature))
    if "pantry" in f:
        b.connect(kitchen, b.add_room("pantry", ground), door_type="door", door_props={"open": False})
    if "laundry_room" in f:
        laundry_hub = bath_hub if rng.random() < 0.3 else hub_by_floor[ground]
        b.connect(laundry_hub, b.add_room("laundry_room", b.rooms[laundry_hub]["floor"]), door_type="door", door_props={"open": True})
    if "mudroom" in f and plan["dwelling"] != "apartment":
        mud = b.add_room("mudroom", 0)
        b.connect(hub_by_floor[0] if rng.random() < 0.5 else kitchen, mud)
    else:
        mud = None
    b.connect(hub_by_floor[ground], b.add_room("utility_closet", ground), door_type="door", door_props={"open": False})
    if "basement" in f:
        base = b.add_room("basement", -1)
        b.connect(hub_by_floor[0], base, kind="stairs", width=0.9, steps=13, handrail=True, rise_m=0.19, door_type="door",
                  door_props={"open": False, "label": "basement door"})
    if "attic" in f:
        attic = b.add_room("attic", plan["floors"], lit=False)
        b.connect(hub_by_floor[top], attic, kind="hatch", width=0.6, door_type="hatch", needs_ladder=True)
    # outdoors and building
    if plan["dwelling"] == "apartment":
        corridor = b.add_room("building_corridor", ground, label="Building corridor", shared=True)
        b.connect(entry, corridor, door_type="exterior_door", door_props={"label": "apartment door", "key": "house_key_1"})
        elevator = b.add_room("elevator", ground, label="Elevator", shared=True)
        b.connect(corridor, elevator, kind="elevator_door", width=0.9)
        lobby = b.add_room("lobby", 0, label="Building lobby", shared=True)
        b.connect(elevator, lobby, kind="elevator_door", width=0.9)
        b.connect(lobby, b.add_room("mail_room", 0, shared=True))
        if "shared_laundry" in f:
            b.connect(lobby, b.add_room("shared_laundry", 0, label="Shared laundry room", shared=True), door_type="door", door_props={"open": False})
        if "balcony" in f:
            b.connect(living, b.add_room("balcony", ground), door_type="sliding_door")
    else:
        if "porch" in f:
            porch = b.add_room("porch", 0)
            b.connect(entry, porch, door_type="exterior_door", door_props={"label": "front door", "key": "house_key_1"})
            front = b.add_room("front_yard", 0)
            b.connect(porch, front)
        else:
            front = b.add_room("front_yard", 0)
            b.connect(entry, front, door_type="exterior_door", door_props={"label": "front door", "key": "house_key_1"})
        drive = b.add_room("driveway", 0)
        b.connect(front, drive)
        walk = b.add_room("sidewalk", 0, label="Sidewalk and curb")
        b.connect(front, walk)
        b.connect(drive, walk)
        back = b.add_room("backyard", 0)
        back_exit = living if rng.random() < 0.5 else kitchen
        if "patio" in f or "deck" in f:
            coin = rng.random() if "deck" in f and "patio" in f else 0.0
            explicit = set(opts.get("features", ())) | set(opts.get("require", ()))
            if {"patio", "deck"} <= explicit:
                kinds = ["patio", "deck"]
            elif "patio" in explicit or "deck" in explicit:
                kinds = ["patio" if "patio" in explicit else "deck"]
            else:
                kinds = ["deck" if "deck" in f and ("patio" not in f or coin < 0.5) else "patio"]
            for n, kind in enumerate(kinds):
                terrace = b.add_room(kind, 0)
                exit_room = back_exit if n == 0 else (kitchen if back_exit == living else living)
                b.connect(exit_room, terrace, door_type="sliding_door", door_props={"label": "back door" if n == 0 else kind + " door"})
                b.connect(terrace, back)
        else:
            b.connect(back_exit if mud is None else mud, back, door_type="exterior_door", door_props={"label": "back door", "key": "house_key_1"})
        b.connect(front, back, kind="gate", door_type="gate", door_props={"label": "side gate", "latched": True})
        if "garage" in f:
            garage = b.add_room("garage", 0)
            b.connect(drive, garage, kind="garage", door_type="garage_door", width=2.5)
            b.connect(mud or hub_by_floor[0], garage, door_type="door", door_props={"label": "garage entry door", "open": False, "lockable": True, "locked": False, "lock_kind": "thumb_turn", "fire_door": True, "self_closing": True})
        if "shed" in f:
            shed = b.add_room("shed", 0)
            b.connect(back, shed, door_type="door", door_props={"label": "shed door", "open": False, "lockable": True, "locked": True, "lock_kind": "key", "key": "shed_key_1", "exterior": True, "material": "wood"})
        if "garden" in f:
            b.connect(back, b.add_room("garden", 0))
        if "pool" in f:
            pool = b.add_room("poolside", 0, label="Pool area")
            b.connect(back, pool, kind="gate", door_type="gate", door_props={"label": "pool gate", "self_closing": True, "self_latching": True, "latched": True, "child_safety": True})
    return b, plan, entry, living, hub_by_floor


def finish_structure(b, plan, registry):
    """Windows, lights, switches, floor surfaces and keys."""
    rng = b.rng
    for rid in sorted(b.rooms):
        room = b.rooms[rid]
        rtype = room["type"]
        cols, rows = geometry.grid_size(room)
        floor_type = "floor_surface"
        floor = registry.make(floor_type, rid, material=room["floor_material"], label="{} {}".format(room["label"], "ground" if room["outdoor"] else "floor"),
                              size_m=[room["size_m"][0], room["size_m"][1], 0.01], outdoor=room["outdoor"])
        floor["pos"] = [0, 0]
        floor["water_safe"] = room["floor_material"] not in ("hardwood_unsealed",)
        b.entities["floor_" + rid] = floor
        if not room["outdoor"] and rtype not in WINDOWLESS and not (rtype == "basement" and rng.random() < 0.5):
            count = 1 if room["size_m"][0] * room["size_m"][1] < 10 else rng.choice([1, 2, 2, 3])
            for _ in range(count):
                wid = b.unique("window")
                record = registry.make("window", rid, window_kind=rng.choice(["sash", "sash", "casement", "sliding"]),
                                       label="{} window".format(room["label"].lower()))
                record["pos"] = b._wall_cell(rid)
                record["mounted"] = True
                geometry.along_wall(room, record)     # runs along its wall, footprint on the grid
                b.entities[wid] = record
        windowed = any(e.get("location") == rid and e.get("category") == "window" for e in b.entities.values())
        if room["outdoor"]:
            if rtype in ("porch", "backyard", "driveway", "patio", "deck", "poolside", "balcony"):
                lid = b.unique("outdoor_light")
                light = registry.make("outdoor_light", rid, label="{} light".format(room["label"].lower()))
                light["pos"] = b._wall_cell(rid)
                light["mounted"] = True
                b.entities[lid] = light
                inside = next((c["rooms"][0] if c["rooms"][1] == rid else c["rooms"][1] for c in b.connections.values()
                               if rid in c["rooms"] and not b.rooms[c["rooms"][0] if c["rooms"][1] == rid else c["rooms"][1]]["outdoor"]), None)
                if inside:
                    sid = b.unique("light_switch")
                    switch = registry.make("light_switch", inside, label="{} light switch".format(room["label"].lower()), controls=[lid], on=False)
                    switch["pos"] = b._wall_cell(inside)
                    switch["mounted"] = True
                    b.entities[sid] = switch
            continue
        lid = b.unique("ceiling_light")
        on = not windowed or rtype in ("kitchen",)
        if rtype == "attic":
            on = False
        light = registry.make("ceiling_light", rid, on=on, label="{} ceiling light".format(room["label"].lower()))
        light["pos"] = [cols // 2, rows // 2]
        light["mounted"] = True
        b.entities[lid] = light
        if rtype != "elevator":
            doorways = [(cid, c) for cid, c in sorted(b.connections.items()) if rid in c["cells"]]
            kind = rng.choice(["toggle", "rocker", "rocker", "dimmer"]) if len(doorways) < 2 else "three_way"
            for n, (cid, conn) in enumerate(doorways or [(None, None)]):
                sid = b.unique("light_switch")
                label = "{} light switch".format(room["label"].lower())
                if len(doorways) > 1:
                    other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
                    label += " (by the {} doorway)".format(b.rooms[other]["label"].lower())
                switch = registry.make("light_switch", rid, label=label, controls=[lid], on=on, switch_kind=kind)
                switch["pos"] = list(conn["cells"][rid]) if conn else [0, 0]
                switch["near_connection"] = cid
                switch["mounted"] = True
                b.entities[sid] = switch
        if not room.get("lit", True) or rtype == "attic":
            room["lit"] = False


def default_residents(rng, opts, rooms, start_room):
    spec = opts.get("residents", "random")
    people = {}
    indoor = sorted(r for r, v in rooms.items() if not v["outdoor"] and not v.get("shared") and v["type"] not in ("coat_closet", "linen_closet", "utility_closet", "pantry", "attic", "elevator"))
    def place(kind):
        options = [r for r in indoor if rooms[r]["type"] in kind] or indoor
        return rng.choice(options)
    if spec == "none":
        return people
    if isinstance(spec, list):
        for item in spec:
            pid = item["id"]
            people[pid] = dict(item)
            people[pid].pop("id")
            people[pid].setdefault("location", place(("living_room", "kitchen")))
        return people
    adults = 1 if spec == "minimal" else rng.choice([1, 2, 2, 2])
    names = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie"]
    rng.shuffle(names)
    for i in range(adults):
        people["resident_{}".format(i + 1)] = {"label": names[i], "role": "adult", "age": rng.randint(28, 60), "authority": 2,
                                                 "location": place(("living_room", "kitchen", "office")), "consent": True,
                                                 "awake": True, "mood": "neutral", "permissions": [], "preferences": {},
                                                 "has_phone": True, "reachable_by_phone": True}
    if spec == "minimal":
        return people
    n = len(people)
    for i in range(rng.choice([0, 0, 1, 2])):
        n += 1
        age = rng.randint(4, 15)
        people["child_{}".format(i + 1)] = {"label": names[n % len(names)] + " (child)", "role": "child", "age": age, "authority": 0,
                                              "location": place(("bedroom", "playroom", "living_room")), "consent": True,
                                              "awake": True, "mood": "neutral", "permissions": [], "preferences": {}}
    if rng.random() < 0.15:
        people["elder_1"] = {"label": "Grandparent", "role": "elderly", "age": rng.randint(72, 90), "authority": 1,
                             "location": place(("living_room", "bedroom")), "consent": True, "awake": True, "mood": "neutral",
                             "permissions": [], "preferences": {}, "abilities": {"mobility": rng.choice(["full", "cane", "walker"]),
                                                                                  "vision": rng.choice(["full", "low"]), "hearing": rng.choice(["full", "reduced"])}}
    return people


def default_pets(rng, opts, rooms):
    spec = opts.get("pets", "random")
    if spec == "none":
        return {}
    if isinstance(spec, list):
        pets = {}
        for item in spec:
            pid = item["id"]
            pets[pid] = dict(item)
            pets[pid].pop("id")
        return pets
    pets = {}
    indoor = sorted(r for r, v in rooms.items() if not v["outdoor"] and not v.get("shared") and v["type"] in ("living_room", "kitchen", "bedroom", "master_bedroom"))
    if rng.random() < 0.35:
        pets["dog_1"] = {"label": rng.choice(["Biscuit", "Max", "Luna", "Rocky"]), "species": "dog", "size": rng.choice(["small", "medium", "large"]),
                         "location": rng.choice(indoor)}
    if rng.random() < 0.3:
        pets["cat_1"] = {"label": rng.choice(["Miso", "Oliver", "Cleo"]), "species": "cat", "size": "small", "location": rng.choice(indoor)}
    return pets
