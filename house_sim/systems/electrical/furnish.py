"""Furnishers: house wiring (panel, breakers, outlets, detectors), electronics, and a final
hookup pass that plugs every corded device into a nearby outlet and settles derived fields.

Counts are kept modest (about 60-90 entities): grouped branch circuits, 1-3 outlets per room,
detectors per code (every bedroom, each sleeping-area hallway, basement; CO near sleeping
areas), one media setup, network gear, a desk setup, bedside clocks and a few spare cells.
All builders tolerate missing rooms/furniture (Placer returns None). Builders are reused by
scenario helpers (they only need a Placer and the config).
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher, hook
from . import common as C

LIVING = ("living_room",)
SLEEP = ("master_bedroom", "bedroom", "guest_room", "nursery")
OUTLETS = {"living_room": 2, "kitchen": 2, "dining_room": 1, "master_bedroom": 2, "bedroom": 1, "guest_room": 1, "nursery": 1,
           "office": 2, "playroom": 1, "gym": 1, "craft_room": 1, "bathroom": 1, "half_bath": 1, "laundry_room": 1, "garage": 1,
           "hallway": 1, "entryway": 1, "mudroom": 1, "basement": 1, "porch": 1, "patio": 1, "deck": 1, "balcony": 1, "shed": 1}
WET = {"kitchen", "bathroom", "half_bath", "laundry_room", "garage", "porch", "patio", "deck", "balcony", "basement", "shed", "mudroom"}
COUNTER_HEIGHT = {"kitchen": 1.07, "bathroom": 1.05, "half_bath": 1.05, "laundry_room": 1.07, "garage": 1.2, "shed": 1.2}
BULB_PART = {"us": ("bulb_e26", "e26"), "ca": ("bulb_e26", "e26"), "jp": ("bulb_e26", "e26"), "eu": ("bulb_e27", "e27"), "uk": ("bulb_b22", "b22")}
SHARED = {"lobby", "building_corridor", "elevator", "mail_room", "shared_laundry"}


def region_of(config):
    reg = (config.get("metadata") or {}).get("region") or "us"
    return reg if reg in C.REGION else "us"


def plug_props(config, grounded=False, polarized=True):
    g = C.REGION[region_of(config)]
    return {"plug_type": g["plug3"] if grounded else g["plug2"], "plug_polarized": bool(polarized and region_of(config) in ("us", "ca"))}


def bulb_props(config, extra=None):
    part, base = BULB_PART[region_of(config)]
    props = {"part_type": part, "base": base}
    props.update(extra or {})
    return props


# ----------------------------------------------------------------------------------------
# settle: compute derived observable fields on a config (no transition side effects)

def settle(config):
    from ...kernel.world import World
    from . import power as P
    view = dict(config)
    view.setdefault("time_s", 0.0)
    robot = view.get("robot")
    if not isinstance(robot, dict) or not robot.get("room"):
        view["robot"] = {"room": config.get("start_room"), "hands": {}, "pos": None}
    view.setdefault("utilities", {"power": True})
    view.setdefault("clock", {"start": "2026-10-09T10:00:00"})
    w = World(view, None)
    P.SETTLING[0] = True
    try:
        C.refresh(w)
    finally:
        P.SETTLING[0] = False
    return config


@hook("episode_start")
def _settle_at_start(w, env):
    """Kernel v2.1 episode_start: derive every electrical field from the final setup (setup ops that edit plugs,
    breakers or bulbs after the last `electrical.settle` no longer leave stale `energized`/`lit` in the baseline).
    Settling applies no transition consequences (the scenario simply starts this way)."""
    from . import power as P
    from .cords import reroute_moved
    if C.electrified(w.s, w):
        reroute_moved(w)      # cords of devices moved after they were plugged in (layout repair, setup moves)
    P.SETTLING[0] = True
    try:
        C.refresh(w)
    finally:
        P.SETTLING[0] = False


# ----------------------------------------------------------------------------------------
# wiring

def _perimeter_free(p, rid, taken):
    """Wall cells of a room outside its doorway clearance (kernel v2.3 shared door model: doorway cells, their
    neighbours, door leaves swinging into the room, overhead-door thresholds) that are not already taken."""
    from .cords import perimeter
    room = p.c["rooms"][rid]
    clear = geometry.door_clearance(p.state(), rid)
    return [c for c in perimeter(room) if c not in clear and c not in taken]


def reach_radius(height):
    """Cells from which a wall mount at `height` (m) can be reached: one, or two above counter height (0.85 m), the
    kernel's reach rule (reaching over a counter or workbench)."""
    return 2 if C.num(height, 0.3) >= 0.85 else 1


REACH_OVER = ("table", "tv_stand", "nightstand", "side_table", "desk")


def reach_over(rec):
    """Low open furniture a person reaches over or under to a receptacle behind it (nightstand, side table, desk,
    TV stand): at most 0.8 m tall and 0.8 m deep, not an appliance or a closed cabinet."""
    if not geometry.blocks(rec) or rec.get("container") or rec.get("appliance") or rec.get("plug_required"):
        return False
    kinds = {rec.get("type")} | set(rec.get("is_a") or [])
    if not kinds & set(REACH_OVER):
        return False
    size = rec.get("size_m") or [1, 1, 1]
    return C.num(size[2] if len(size) > 2 else 1.0, 1.0) <= 0.8 and min(C.num(size[0], 1.0), C.num(size[1], 1.0)) <= 0.8


def wall_cells(p, rid, height=0.3, taken=(), state=None, hazard_free=False):
    """Wall cells for a receptacle/switch/panel at `height`: outside doorway clearance, not taken, not behind
    floor-standing furniture or appliances when low (< 0.5 m), and reachable: a doorway-reachable floor cell lies
    within reach_radius(height). hazard_free: reachable without stepping on any floor hazard (loose items, cords, dock
    keep-clear zones: geometry.placement_hazards), the access the layout audit asks for. Rooms without doorway cells
    (legacy layouts) accept any free neighbour."""
    state = state or p.state()
    room = p.c["rooms"][rid]
    cells = _perimeter_free(p, rid, set(taken))
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    if hazard_free:
        hz = set(geometry.placement_hazards(state, rid))
        reach = geometry.reachable_cells(state, rid, blocked | hz) - hz
    else:
        reach = geometry.reachable_cells(state, rid, blocked)
    used = set()
    if C.num(height, 0.3) < 0.5:
        # low: not behind furniture or appliances, except low open tables and stands one reaches over or under
        # (nightstands, side tables, desks, TV stands); above counter height it may sit over a counter
        for eid in geometry.room_entities(state, rid):
            rec = state["entities"][eid]
            if geometry.floor_standing(rec) and not reach_over(rec):
                used.update(geometry.footprint(rec))
    radius = reach_radius(height)
    out = []
    for c in cells:
        if c in used:
            continue
        near = geometry.near_cells([c], room, radius) - {c}
        if (near & reach) if reach else any(n not in blocked for n in near):
            out.append(c)
    return out


def spread_cells(p, rid, n, taken=(), height=0.3):
    """n reachable wall cells spread around the room (seeded first pick, then farthest-first); cells reachable
    without stepping on floor hazards when the room has any."""
    cells = wall_cells(p, rid, height, taken, hazard_free=True) or wall_cells(p, rid, height, taken)
    if not cells:
        return []
    chosen = [p.rng.choice(cells)]
    while len(chosen) < n:
        rest = [c for c in cells if c not in chosen]
        if not rest:
            break
        chosen.append(max(rest, key=lambda c: (min(geometry.chebyshev(c, o) for o in chosen), -c[1], -c[0])))
    return chosen


def add_breaker(p, panel, number, label, rating, rooms=(), kind="branch", poles=1, gfci=False, afci=False, eid=None, **props):
    # the visible name is the handle's number; which circuit it feeds is the written directory label (read tier)
    rec_props = {"label": "main breaker" if kind == "main" else "breaker {}".format(number),
                 "label_text": "{} - {}".format(number, label), "rating_a": rating, "poles": poles,
                 "breaker_kind": kind, "gfci_breaker": gfci, "afci": afci, "serves_rooms": sorted(rooms), "panel": panel, "number": number}
    rec_props.update(props)
    bid = p.make("main_breaker" if kind == "main" else "circuit_breaker", panel, eid=eid, **rec_props)
    rec = p.c["entities"][bid]
    rec["relation"] = "part"
    return bid


def panel_room(p):
    for rtype in ("garage", "basement", "laundry_room", "utility_closet", "mudroom", "hallway", "entryway"):
        rooms = p.rooms(rtype)
        if rooms:
            return rooms[0]
    rooms = [r for r in p.rooms(outdoor=False) if p.c["rooms"][r]["type"] not in SHARED]
    return rooms[0] if rooms else None


def build_wiring(p, config, rng):
    region = region_of(config)
    g = C.REGION[region]
    rooms = config["rooms"]
    prid = panel_room(p)
    if prid is None:
        return {}
    taken = {}
    # radius-1 rule: working space in front of the panel (NEC 110.26); never onto another wall mount's spot
    pcell = spread_cells(p, prid, 1, mount_cells_taken(config, prid, 1.1, 1.7))
    panel = p.mount(prid, "breaker_panel", eid="breaker_panel_1", height=1.1, cell=pcell[0] if pcell else None)
    taken.setdefault(prid, []).append(tuple(config["entities"][panel]["pos"]))
    main = add_breaker(p, panel, 0, "MAIN", g["main_a"], kind="main", poles=2, eid="main_breaker_1")
    config["entities"][panel]["main_breaker"] = main
    us = region in ("us", "ca")
    circuits = []
    by_type = {}
    for rid in sorted(rooms):
        room = rooms[rid]
        if room["type"] in SHARED or (room.get("outdoor") and room["type"] not in ("porch", "patio", "deck", "balcony")):
            continue
        by_type.setdefault(room["type"], []).append(rid)
    # grouped circuits (label, rooms, rating, gfci, afci)
    if by_type.get("kitchen"):
        circuits.append(("Kitchen counter (left)", by_type["kitchen"], g["kitchen_a"], not us and region != "jp", us))
        circuits.append(("Kitchen counter (right)", [], g["kitchen_a"], not us and region != "jp", us))
        circuits.append(("Fridge / dishwasher", [], g["kitchen_a"], False, False))
    for rid in by_type.get("bathroom", []) + by_type.get("half_bath", []):
        circuits.append(("{} receptacles".format(rooms[rid]["label"]), [rid], g["wet_a"], region in ("uk", "eu"), False))
    sleep = [r for t in SLEEP + ("office", "playroom", "gym", "craft_room") for r in by_type.get(t, [])]
    for i in range(0, len(sleep), 2):
        names = " + ".join(rooms[r]["label"] for r in sleep[i:i + 2])
        circuits.append((names, sleep[i:i + 2], g["branch_a"], False, us))
    common = [r for t in ("living_room", "dining_room") for r in by_type.get(t, [])]
    if common:
        circuits.append(("Living / dining", common, g["branch_a"], False, us))
    halls = [r for t in ("hallway", "entryway", "mudroom", "coat_closet", "linen_closet", "utility_closet", "pantry", "attic", "basement")
             for r in by_type.get(t, [])]
    if halls:
        circuits.append(("Halls, entry and stairs", halls, g["branch_a"], False, False))
    laundry = by_type.get("laundry_room", [])
    if laundry:
        circuits.append(("Laundry", laundry, g["wet_a"], not us, False))
    garage = by_type.get("garage", []) + by_type.get("shed", [])
    if garage:
        circuits.append(("Garage / shed", garage, g["wet_a"], not us, False))
    outdoor = [r for t in ("porch", "patio", "deck", "balcony") for r in by_type.get(t, [])]
    if outdoor:
        circuits.append(("Outdoor receptacles and lights", outdoor, g["wet_a"], not us, False))
    breakers = {}
    directory = []
    for n, (label, rids, rating, gfci, afci) in enumerate(circuits, 1):
        bid = add_breaker(p, panel, n, label, rating, rooms=rids, gfci=gfci, afci=afci)
        breakers[label] = bid
        directory.append("{}: {}".format(n, label))
        for rid in rids:
            rooms[rid]["circuit"] = bid
    config["entities"][panel]["label_text"] = "Panel directory - " + "; ".join(directory)
    config["entities"][panel]["directory"] = {str(i + 1): c[0] for i, c in enumerate(circuits)}
    # outlets
    outlets = []
    for rid in sorted(rooms):
        room = rooms[rid]
        n = OUTLETS.get(room["type"], 0)
        if not n or room["type"] in SHARED:
            continue
        outdoor = bool(room.get("outdoor"))
        height = COUNTER_HEIGHT.get(room["type"], 0.45 if outdoor else 0.3)
        cells = spread_cells(p, rid, n, set(taken.get(rid, [])) | mount_cells_taken(config, rid, height), height=height)
        circuit = room.get("circuit")
        for i, cell in enumerate(cells):
            wet = room["type"] in WET
            kind = "outdoor_outlet" if outdoor else ("gfci_outlet" if wet and us else "wall_outlet")
            bc = circuit
            if room["type"] == "kitchen" and i % 2 == 1 and breakers.get("Kitchen counter (right)"):
                bc = breakers["Kitchen counter (right)"]
            oid = p.mount(rid, kind, height=height, cell=cell, circuit=bc, socket_type=g["socket"],
                          label="{} outlet".format(room["label"].lower()) if i == 0 else "{} outlet {}".format(room["label"].lower(), i + 1),
                          grounded=region not in ("jp",))
            taken.setdefault(rid, []).append(tuple(cell))
            outlets.append(oid)
    # appliance circuits for big fixed appliances are assigned during hookup
    config.setdefault("metadata", {})["electrical"] = {"panel": panel, "main": main,
                                                      "appliance_circuit": breakers.get("Fridge / dishwasher"),
                                                      "laundry_circuit": breakers.get("Laundry")}
    return {"panel": panel, "main": main, "breakers": breakers, "outlets": outlets}


def add_bulb(p, fixture, slot="bulb", type_name="led_bulb_e26", **props):
    config = p.c
    merged = bulb_props(config, props)
    bid = p.make(type_name, fixture, **merged)
    rec = config["entities"][bid]
    rec["relation"] = "part"
    rec["slot"] = slot
    config["entities"][fixture].setdefault("slots", {}).setdefault(slot, {"accepts": [merged["part_type"]], "item": None,
                                                                         "access": "none", "power_off_first": True})
    config["entities"][fixture]["slots"][slot]["item"] = bid
    return bid


def socket_fixture(p, fixture, count=1, type_name="led_bulb_e26", max_w=60.0, **bulb):
    config = p.c
    rec = config["entities"][fixture]
    part, _ = BULB_PART[region_of(config)]
    rec["slots"] = {("bulb" if count == 1 else "bulb_{}".format(i + 1)): {"accepts": [part], "item": None, "access": "none", "power_off_first": True}
                    for i in range(count)}
    rec["max_bulb_w"] = max_w
    rec["nominal_lumens"] = 800.0 * count
    rec["base_brightness"] = rec.get("brightness", 0.8)
    rec.pop("integrated_led", None)
    out = []
    for slot in sorted(rec["slots"]):
        out.append(add_bulb(p, fixture, slot, type_name, **bulb))
    return out


# ----------------------------------------------------------------------------------------
# detectors

def detector_cell(p, rid, want=None, wall=False):
    """Where an alarm goes: the requested cell (ceiling centre by default, or a wall cell) when someone can stand
    there or right beside it to reach it (with a step stool for ceiling units), else the nearest such cell; never
    over a bed or other blocking furniture the robot could not stand beside."""
    state = p.state()
    room = p.c["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    want = tuple(want) if want is not None else (cols // 2, max(0, rows // 2 - 1))
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    if not reach:
        return list(want)
    if wall:
        from .cords import perimeter
        cand = [c for c in perimeter(room) if c not in geometry.door_clearance(state, rid)]
        ok = [c for c in cand if (geometry.near_cells([c], room) & reach)]
    else:
        ok = [(x, y) for y in range(rows) for x in range(cols) if (x, y) in reach]
    if not ok or want in ok:
        return list(want)
    return list(min(ok, key=lambda c: (geometry.chebyshev(c, want), c[1], c[0])))


def add_detector(p, rid, type_name="smoke_alarm", charge=None, manufactured=None, height=None, cell=None, group=None, eid=None, **props):
    config = p.c
    room = config["rooms"][rid]
    ceiling = room.get("ceiling_m") or 2.4
    h = height if height is not None else round(ceiling - 0.03, 2)
    if cell is None:
        cell = detector_cell(p, rid, wall=h < ceiling - 0.3)
    base = p.mount(rid, "detector_base", height=h, cell=cell)
    props = dict(props)
    if manufactured:
        props["manufactured"] = manufactured
    if group:
        props["interconnect"] = group
    det = p.make(type_name, base, eid=eid, **props)
    drec = config["entities"][det]
    drec["relation"], drec["slot"] = "part", "detector"
    config["entities"][base]["slots"]["detector"]["item"] = det
    cells = []
    from .batteries import battery_slots
    for slot in battery_slots(drec):
        info = drec["slots"][slot]
        pt = info["accepts"][0]
        ctype = {"battery_9v": "nine_volt_battery", "battery_aa": "aa_battery", "battery_aaa": "aaa_battery"}.get(pt, "aa_battery")
        c = p.make(ctype, det, charge=charge if charge is not None else 0.8, orientation=info.get("polarity", "upright"), condition="in use")
        crec = config["entities"][c]
        crec["relation"], crec["slot"] = "part", slot
        info["item"] = c
        cells.append(c)
    if type_name == "smoke_alarm_hardwired":
        drec["hardwired"] = True
    return {"base": base, "detector": det, "cells": cells}


def build_detectors(p, config, rng):
    rooms = config["rooms"]
    hard = config["metadata"].get("dwelling_type") == "apartment" or rng.random() < 0.35
    year = 2026
    out = []
    hall_by_floor = {}
    for rid in sorted(rooms):
        if rooms[rid]["type"] == "hallway":
            hall_by_floor.setdefault(rooms[rid].get("floor", 0), rid)
    sleeping_floors = {rooms[r].get("floor", 0) for r in rooms if rooms[r]["type"] in SLEEP}

    def made():
        return "{}-{:02d}".format(rng.choice([year - 1 - i for i in range(9)] + ([2014, 2015] if rng.random() < 0.25 else [])), rng.randint(1, 12))

    def charge():
        return round(rng.choice([0.9, 0.8, 0.7, 0.55, 0.45]) if rng.random() > 0.08 else 0.16, 2)

    for rid in sorted(rooms):
        if rooms[rid]["type"] in SLEEP:
            kind = "smoke_alarm_hardwired" if hard else rng.choice(["smoke_alarm", "smoke_alarm_sealed"])
            out.append(add_detector(p, rid, kind, charge=charge(), manufactured=made(), group="house" if hard else None))
    for floor in sorted(hall_by_floor):
        rid = hall_by_floor[floor]
        out.append(add_detector(p, rid, "smoke_alarm_hardwired" if hard else "smoke_alarm", charge=charge(), manufactured=made(),
                                group="house" if hard else None))
        if floor in sleeping_floors and (config.get("utilities", {}).get("gas") or p.rooms("garage")):
            cols, rows = geometry.grid_size(rooms[rid])
            out.append(add_detector(p, rid, "co_alarm", charge=charge(), manufactured=made(), height=1.5,
                                    cell=detector_cell(p, rid, [0, rows - 1] if rows > 1 else [0, 0], wall=True)))
    for rid in p.rooms("basement"):
        out.append(add_detector(p, rid, "combo_alarm", charge=charge(), manufactured=made()))
    return out


# ----------------------------------------------------------------------------------------
# electronics

def first(p, type_name, room):
    found = p.find(type_name, room=room)
    return found[0] if found else None


def floor_near(p, rid, near):
    return p.floor_item(rid, "power_strip", near=near)


def add_cells(p, device, condition="in use", charge=None):
    config = p.c
    rec = config["entities"][device]
    from .batteries import battery_slots
    cells = []
    for slot in battery_slots(rec):
        info = rec["slots"][slot]
        pt = info["accepts"][0]
        ctype = {"battery_9v": "nine_volt_battery", "battery_aa": "aa_battery", "battery_aaa": "aaa_battery",
                 "battery_cr2032": "coin_cell_cr2032"}.get(pt, "aa_battery")
        c = p.make(ctype, device, charge=charge if charge is not None else round(p.rng.uniform(0.35, 0.9), 2),
                   orientation=info.get("polarity", "upright"), condition=condition)
        crec = config["entities"][c]
        crec["relation"], crec["slot"] = "part", slot
        info["item"] = c
        cells.append(c)
    return cells


def link(config, cable, end, dev, port):
    rec = config["entities"][cable]
    rec["ends"][end].update(at=dev, port=port, seated=1.0)
    if rec.get("relation") != "attached":
        for key in ("pos", "spos", "shelf"):
            rec.pop(key, None)
        rec["location"], rec["relation"] = dev, "attached"


def build_media(p, config, rng, rid=None, soundbar=None, sources=None):
    rid = rid or (p.rooms("living_room") or [None])[0]
    if rid is None:
        return {}
    stand = first(p, "tv_stand", rid)
    out = {"room": rid}
    if stand is None:
        stand = p.furniture(rid, "tv_stand") or place_furniture(p, rid, "tv_stand")
        if stand is None:
            return {}
    tv = p.on(stand, "television", **plug_props(config))
    if tv is None:
        return {}
    out.update(stand=stand, tv=tv)
    remote = p.on(stand, "remote_control", label="TV remote")
    seat = first(p, "coffee_table", rid) or first(p, "side_table", rid)
    if seat:
        moved = p.on(seat, "remote_control", label="TV remote")
        if moved:
            del config["entities"][remote]
            remote = moved
    if remote:
        add_cells(p, remote, charge=round(rng.uniform(0.5, 0.9), 2))
        config["entities"][tv]["remote"] = remote
        config["entities"][remote]["controls_devices"] = [tv]
        out["remote"] = remote
    srcs = sources if sources is not None else (["streaming_box"] if rng.random() < 0.6 else ["game_console"])
    ports = ["hdmi1", "hdmi3"]
    rng.shuffle(ports)
    for i, stype in enumerate(srcs[:2]):
        dev = p.on(stand, stype, **plug_props(config))
        if dev is None:
            continue
        cable = p.on(stand, "hdmi_cable")
        if cable:
            link(config, cable, "a", dev, "hdmi_out")
            link(config, cable, "b", tv, ports[i])
            config["entities"][cable]["location"] = tv
            out[stype + "_cable"] = cable
        out[stype] = dev
        out[stype + "_port"] = ports[i]
    if soundbar if soundbar is not None else rng.random() < 0.4:
        sb = p.on(stand, "soundbar", **plug_props(config))
        if sb:
            cable = p.on(stand, "hdmi_cable")
            if cable:
                link(config, cable, "a", sb, "hdmi_arc")
                link(config, cable, "b", tv, "hdmi2")
                config["entities"][cable]["location"] = tv
                out["soundbar_cable"] = cable
            out["soundbar"] = sb
    srec = config["entities"][stand]
    strip = p.floor_item(rid, "power_strip", near=srec.get("pos"), **plug_props(config, grounded=True))
    if strip:
        config["entities"][strip]["socket_type"] = C.REGION[region_of(config)]["socket"]
        config["entities"][strip]["plugged_in"] = True
        out["strip"] = strip
        n = 1
        for key in ("tv", "streaming_box", "game_console", "soundbar"):
            if key in out:
                rec = config["entities"][out[key]]
                rec.update(plugged_in=True, outlet=strip, _socket=n, plug_seated=1.0)
                n += 1
    return out


def build_network(p, config, rng):
    rid = (p.rooms("office") or p.rooms("living_room") or [None])[0]
    if rid is None:
        return {}
    surface = first(p, "desk", rid) or first(p, "bookshelf", rid) or first(p, "side_table", rid) or first(p, "tv_stand", rid)
    if surface is None:
        return {}
    place = p.on if not config["entities"][surface].get("container") else (lambda s, t, **kw: p.inside(s, t, shelf=2, **kw))
    out = {}
    router = place(surface, "wifi_router", **plug_props(config, polarized=False))
    if router is None:
        return {}
    out["router"] = router
    if config["metadata"].get("dwelling_type") != "apartment":
        modem = place(surface, "cable_modem", **plug_props(config, polarized=False))
        if modem:
            out["modem"] = modem
            cable = place(surface, "ethernet_cable")
            if cable:
                link(config, cable, "a", modem, "ethernet")
                link(config, cable, "b", router, "wan")
                config["entities"][cable]["location"] = router
    else:
        config["entities"][router]["label"] = "Wi-Fi gateway"
        config["entities"][router]["integrated_modem"] = True
        config["entities"][router]["wan_ok"] = True
    for key in ("router", "modem"):
        if key in out:
            config["entities"][out[key]].update(plugged_in=True, plug_seated=1.0)
    return out


def build_desk(p, config, rng):
    rid = (p.rooms("office") or [r for r in p.rooms("bedroom") if first(p, "desk", r)] or [None])[0]
    if rid is None:
        return {}
    desk = first(p, "desk", rid)
    if desk is None:
        return {}
    out = {"room": rid, "desk": desk}
    if rng.random() < 0.7:
        laptop = p.on(desk, "laptop", battery_level=round(rng.uniform(0.2, 0.9), 2))
        charger = p.on(desk, "laptop_charger", **plug_props(config, polarized=False))
        if laptop and charger:
            config["entities"][charger]["captive_at"] = {"at": laptop, "port": "usb_c1", "seated": 1.0}
            config["entities"][charger]["connected_to"] = laptop
            config["entities"][charger].update(plugged_in=True, plug_seated=1.0)
        out.update(laptop=laptop, laptop_charger=charger)
    else:
        pc = p.on(desk, "desktop_computer", **plug_props(config, grounded=True))
        mon = p.on(desk, "computer_monitor", **plug_props(config))
        kb = p.on(desk, "keyboard")
        mouse = p.on(desk, "computer_mouse")
        if pc and mon:
            cable = p.on(desk, "displayport_cable")
            if cable:
                link(config, cable, "a", pc, "gpu_dp")
                link(config, cable, "b", mon, "dp_in")
            config["entities"][mon]["settings"]["input"] = "dp_in"
            for dev, port in ((kb, "usb1"), (mouse, "usb2")):
                if dev:
                    cap = config["entities"][dev]["ports"]["usb"]
                    cap.update(at=pc, to_port=port, seated=1.0)
            for dev in (pc, mon):
                config["entities"][dev].update(plugged_in=True, plug_seated=1.0, on=True, power_state="on")
        out.update(desktop=pc, monitor=mon, keyboard=kb, mouse=mouse)
    lamp = p.on(desk, "desk_lamp", on=False, **plug_props(config))
    if lamp:
        out["desk_lamp"] = lamp
    return out


def build_bedrooms(p, config, rng):
    out = []
    for rid in p.rooms("master_bedroom") + [r for r in p.rooms("bedroom") if rng.random() < 0.5]:
        stand = first(p, "nightstand", rid)
        if stand is None:
            continue
        clock = p.on(stand, "alarm_clock", **plug_props(config, polarized=False))
        if clock:
            config["entities"][clock]["plugged_in"] = True
            config["entities"][clock]["plug_seated"] = 1.0
            out.append(clock)
    return out


def build_spares(p, config, rng):
    drawer = next((d for d in p.find("drawer") if config["entities"][d].get("label") == "junk drawer"), None)
    if drawer is None:
        return {}
    org = p.inside(drawer, "battery_organizer", open=False)
    if org is None:
        return {}
    out = {"organizer": org, "cells": []}
    for t in ("aa_battery", "aa_battery", "aaa_battery", "aaa_battery", "nine_volt_battery"):
        c = p.inside(org, t, condition="new", charge=1.0)
        if c:
            out["cells"].append(c)
    return out


def build_garage(p, config, rng):
    rid = (p.rooms("garage") or [None])[0]
    if rid is None:
        return {}
    shelf = first(p, "shelf_unit", rid)
    bench = first(p, "workbench", rid)
    out = {}
    if shelf:
        cord = p.inside(shelf, "extension_cord", shelf=1, coiled="loose", **plug_props(config, grounded=True))
        if cord:
            config["entities"][cord]["socket_type"] = C.REGION[region_of(config)]["socket"]
            out["extension_cord"] = cord
    if bench:
        charger = p.on(bench, "tool_battery_charger", **plug_props(config, polarized=False))
        pack = p.on(bench, "tool_battery_pack", charge=round(rng.uniform(0.2, 0.9), 2))
        out.update(tool_charger=charger, tool_pack=pack)
    return out


# ----------------------------------------------------------------------------------------
# hookup

def approachable(state, rid, cells, radius=1, safe=False, ignore_sources=()):
    """True when a doorway-reachable free floor cell lies within `radius` cells of `cells` (core approach can get
    there: not merely a free cell, but one connected to the room's doorways, e.g. not a pocket sealed off by
    furniture). safe: the robot also gets there without stepping on or beside sharp or fragile things, sleeping
    animals or people (the least cautious route a strict core approach accepts, tasks.skills.RELAXED_AVOID[-1]);
    hazards from `ignore_sources` (the target itself) do not count."""
    room = state["rooms"].get(rid)
    if room is None or not cells:
        return True
    cells = [tuple(c) for c in cells]
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    near = geometry.near_cells(cells, room, radius) - set(cells)
    if not reach:      # a room without doorway cells (legacy layouts): any free neighbour will do
        return any(c not in blocked for c in near)
    goals = sorted(c for c in near if c in reach)
    if not goals:
        return False
    if not safe:
        return True
    from ...tasks.skills import RELAXED_AVOID
    kinds = tuple(RELAXED_AVOID[-1])
    for door in sorted(set(geometry.doorway_cells(state, rid))):
        if door in blocked:
            continue
        if geometry.find_path(state, rid, door, goals, avoid_kinds=kinds, ignore_sources=tuple(ignore_sources)) is not None:
            return True
    return False


def outlet_reachable(state, oid, safe=False):
    """Can the robot stand within reach of a receptacle (radius by mount height; plug-in adapters by their outlet)?"""
    rid, cells = C.anchor_cells(state, oid)
    if rid is None or rid not in state["rooms"] or not cells:
        return False
    return approachable(state, rid, cells, radius=reach_radius(geometry.height_m(state, oid)), safe=safe)


def _floor_used(state, rid, ignore=()):
    """Footprints of loose floor items and floor-standing mounts (furniture must not be stood on top of them)."""
    used = set()
    for oid in geometry.room_entities(state, rid):
        if oid in ignore:
            continue
        o = state["entities"][oid]
        if geometry.floor_standing(o):
            used.update(geometry.footprint(o))
    return used


def place_furniture(p, rid, type_name, wall=True, near=None, eid=None, avoid_rugs=True, **props):
    """Stand a piece of furniture (lamp table, stand, desk) in a room so that it never seals anything off.

    Uses the kernel's shared access model (geometry.access_targets / access_report / not_worse, the rule
    Placer.furniture applies): doorways that were connected stay connected and everything that was accessible stays
    accessible (full protection first, then the basic level: doorways, the dock, floor-standing things); the new piece
    gets a doorway-reachable cell within arm's reach that no person/pet stands on; it keeps out of doorway clearance
    (geometry.door_clearance) and off loose floor items, and avoids rugs when it can (a lamp table on a rug would put
    its cord under the rug). Prefers the wall (wall=True), then any spot; closest to `near` [col,row] when given, else
    a seeded random choice among equally good spots. Returns the new id or None."""
    config = p.c
    state = p.state()
    room = config["rooms"][rid]
    gc, gr = geometry.grid_size(room)
    record = p.reg.make(type_name, rid, **props)
    base = set(geometry.blocked_cells(state, rid, include_agents=False))
    agents = set(geometry.blocked_cells(state, rid, include_agents=True)) - base
    used = _floor_used(state, rid)
    clear = geometry.door_clearance(state, rid)
    rugs = set()
    for oid in geometry.room_entities(state, rid):
        if config["entities"][oid].get("category") == "rug":
            rugs.update(geometry.footprint(config["entities"][oid]))
    size = record.get("size_m") or [0.5, 0.5]
    rots = [0, 90] if size[0] != size[1] else [0]
    options = []
    for rot in rots:
        cols, rows = geometry.footprint_size(dict(record, rot=rot))
        for y in range(0, gr - rows + 1):
            for x in range(0, gc - cols + 1):
                cells = frozenset((x + i, y + j) for i in range(cols) for j in range(rows))
                if cells & base or cells & used or cells & clear or cells & agents:
                    continue
                # back against a wall: the long side, as the core placer does (square pieces: any side)
                if cols > rows:
                    touches = y == 0 or y + rows == gr
                elif rows > cols:
                    touches = x == 0 or x + cols == gc
                else:
                    touches = x == 0 or y == 0 or x + cols == gc or y + rows == gr
                if wall is False:
                    touches = True
                on_rug = bool(cells & rugs) if avoid_rugs else False
                dist = min(geometry.chebyshev(c, tuple(near)) for c in cells) if near is not None else 0
                options.append(((not touches, on_rug, dist), (y, x, rot), rot, (x, y), cells))
    if not options:
        return None
    options.sort(key=lambda o: (o[0], o[1]))
    if near is None and p.rng is not None:
        tiers = {}
        for o in options:
            tiers.setdefault(o[0], []).append(o)
        options = []
        for key in sorted(tiers):
            tier = tiers[key]
            p.rng.shuffle(tier)
            options.extend(tier)
    reports = {}

    def keeps(cells, level):
        if level not in reports:
            targets = geometry.access_targets(state, rid, level=level)
            reports[level] = (targets, geometry.access_report(state, rid, base, targets))
        targets, before = reports[level]
        tg = dict(targets)
        tg[("new",)] = (sorted(cells), 1, False)
        after = geometry.access_report(state, rid, base | cells, tg)
        if not geometry.not_worse(before, after, new_keys=[("new",)]):
            return False
        reach = geometry.reachable_cells(state, rid, base | cells)
        return bool((geometry.near_cells(cells, room) - cells - agents) & reach) if reach else True

    pick = None
    for level in ("all", "basic"):
        for opt in options[:400]:
            if keeps(opt[4], level):
                pick = opt
                break
        if pick is not None:
            break
    if pick is None:
        return None
    record["rot"] = pick[2]
    record["pos"] = list(pick[3])
    eid = eid or p.new_id(type_name)
    config["entities"][eid] = record
    return eid


def _outlet_kind(config, rid):
    room = config["rooms"][rid]
    us = region_of(config) in ("us", "ca")
    return "outdoor_outlet" if room.get("outdoor") else ("gfci_outlet" if room["type"] in WET and us else "wall_outlet")


def _outlet_height(config, rid):
    room = config["rooms"][rid]
    return COUNTER_HEIGHT.get(room["type"], 0.45 if room.get("outdoor") else 0.3)


OUTLET_FACE_M = 0.12     # height of a receptacle's cover plate


def _mount_span(rec):
    """(bottom, top) height in metres of a wall mount, or None."""
    try:
        h = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0)
        size = rec.get("size_m") or [0.1, 0.1, 0.1]
        return h, h + float(size[2] if len(size) > 2 else 0.1)
    except (TypeError, ValueError):
        return None


def mount_cells_taken(config, rid, height, top=None, ignore=()):
    """Wall cells of room `rid` holding another wall mount (water valve or meter, thermostat, radiator, switch, panel,
    window...) whose height range overlaps [height, top] (default: a receptacle's cover plate): a receptacle or panel
    never goes onto an occupied wall spot (round-3 report: outlets mounted over plumbing's main valve / water meter)."""
    top = height + OUTLET_FACE_M if top is None else top
    out = set()
    for eid, rec in config["entities"].items():
        if eid in ignore or rec.get("location") != rid or not rec.get("pos") or not rec.get("mounted"):
            continue
        if "connects" in rec or rec.get("ground_layer"):
            continue
        span = _mount_span(rec)
        if span is not None and span[0] < top - 1e-9 and height < span[1] - 1e-9:
            out.update(tuple(c) for c in geometry.footprint(rec))
    return out


def _outlet_cells_taken(p, config, rid, height=None):
    """Wall cells a new receptacle must not use: other receptacles' cells, and (with `height`) cells where another
    wall mount overlaps the receptacle's height range."""
    out = {tuple(config["entities"][o]["pos"]) for o in p.find(None, room=rid)
           if config["entities"][o].get("category") == "outlet" and config["entities"][o].get("pos")}
    if height is not None:
        out |= {c for c in mount_cells_taken(config, rid, C.num(height, 0.3)) if c not in out}
    return out


def _mount_outlet(p, config, rid, cell, kind, height, circuit=None, **props):
    g = C.REGION[region_of(config)]
    room = config["rooms"][rid]
    return p.mount(rid, kind, height=height, cell=list(cell), circuit=circuit or room.get("circuit"), socket_type=g["socket"],
                   label="{} outlet".format(room["label"].lower()), grounded=region_of(config) not in ("jp",), **props)


def mount_outlet_near(p, config, rid, near_cells, kind=None, height=None, circuit=None, safe=False, avoid=(), relaxed=False):
    """Mount a receptacle on the reachable wall cell (wall_cells: out of doorway clearance, not behind furniture when
    low, a doorway-reachable standing cell within reach) nearest `near_cells`. relaxed (a receptacle for a dressed
    cord, which puts nothing on the floor): any reachable wall cell, door clearance included (closets). None when the
    room has no such cell."""
    if rid not in config["rooms"] or config["rooms"][rid]["type"] in SHARED:
        return None
    kind = kind or _outlet_kind(config, rid)
    height = _outlet_height(config, rid) if height is None else height
    state = p.state()
    taken = _outlet_cells_taken(p, config, rid, height) | set(avoid)
    cells = wall_cells(p, rid, height, taken, state, hazard_free=True) or wall_cells(p, rid, height, taken, state)
    if not cells and relaxed:
        from .cords import perimeter
        room = config["rooms"][rid]
        taken = _outlet_cells_taken(p, config, rid, height) | set(avoid) | set(geometry.doorway_cells(state, rid))
        blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
        cells = [c for c in perimeter(room) if c not in taken and c not in blocked
                 and approachable(state, rid, [c], radius=reach_radius(height))]
    if safe:
        cells = [c for c in cells if approachable(state, rid, [c], radius=reach_radius(height), safe=True)]
    if not cells:
        return None
    near_cells = [tuple(c) for c in near_cells] or [cells[0]]
    cells.sort(key=lambda c: (min(geometry.chebyshev(c, d) for d in near_cells), c[1], c[0]))
    return _mount_outlet(p, config, rid, cells[0], kind, height, circuit)


def mount_clear_outlet(p, config, device, rid=None, cord_m=None, max_cells=None, kind=None, height=None, avoid=(),
                       circuit=None, safe=False):
    """Mount a receptacle on a reachable wall cell near `device` where its cord reaches without crossing a walkway,
    doorway, door clearance, rug, water or heat (an electrician puts outlets every few metres; NEC 210.52 wants one
    within 1.8 m of any point along a wall). Returns the outlet id, or None when no such wall cell exists."""
    from .cords import CordCheck, cell_of, cord_options
    state = p.state()
    drid, dcell = cell_of(state, device)
    rid = rid or drid
    if rid is None or dcell is None or rid != drid or config["rooms"][rid]["type"] in SHARED:
        return None
    _, dcells = C.anchor_cells(state, device)
    dcells = dcells or [dcell]
    kind = kind or _outlet_kind(config, rid)
    height = _outlet_height(config, rid) if height is None else height
    taken = _outlet_cells_taken(p, config, rid, height) | set(avoid)
    # only wall cells the robot reaches without stepping on a floor hazard (loose items other furnishers placed earlier,
    # cords, dock keep-clear zones): a new receptacle reachable only over a tower fan is worse than the fallbacks
    # (round-4 report: hookup outlets behind a desk, reachable only from a fan's cell)
    cells = wall_cells(p, rid, height, taken, state, hazard_free=True)
    if safe:
        cells = [c for c in cells if approachable(state, rid, [c], radius=reach_radius(height), safe=True)]
    if max_cells is not None:
        cells = [c for c in cells if min(geometry.chebyshev(c, d) for d in dcells) <= max_cells]
    cells.sort(key=lambda c: (min(geometry.chebyshev(c, d) for d in dcells), c[1], c[0]))
    if not cells:
        return None
    oid = _mount_outlet(p, config, rid, cells[0], kind, height, circuit)
    rec = config["entities"][oid]
    check = CordCheck(p.state(), device)
    for c in cells[:16]:
        rec["pos"] = list(c)
        opts = cord_options(p.state(), device, oid, check=check, cord_m=cord_m)
        if opts and opts[0]["ok"] and opts[0]["n"] == 0:
            return oid
    del config["entities"][oid]
    return None


def route_risk(state, device, outlet, cord_m=None):
    """(hazard count, cord length, route) of the safest acceptable cord run (CordCheck), else None."""
    from .cords import CordCheck, cord_options
    opts = [o for o in cord_options(state, device, outlet, check=CordCheck(state, device), cord_m=cord_m) if o["ok"]]
    return (opts[0]["n"], opts[0]["length"], opts[0]["route"]) if opts else None


def rank_outlets(config, device, candidates=None, reachable=False, safe=False, routes=("along_wall", "direct"), wall_only=True):
    """[(key, outlet, option)] for receptacles in the device's room with a free socket, best first: an acceptable cord
    run (CordCheck), fewest hazards, reachable receptacles first, shortest. reachable=True keeps only receptacles the
    robot can stand at (safe: without stepping beside sharp/fragile things or sleepers). Receptacles the cord does not
    reach are left out. Plug-in-place devices (wall adapters) rank by distance."""
    from . import power as P
    from .cords import CordCheck, cell_of, cord_options
    state = Placer(config, None, None).state()
    rid, cell = cell_of(state, device)
    if rid is None or cell is None:
        return []
    ents = config["entities"]
    wall_plug = ents[device].get("plug_in_place") == "wall"
    check = CordCheck(state, device)
    out = []
    pool = candidates if candidates is not None else sorted(ents)
    for oid in pool:
        o = ents.get(oid)
        if o is None or oid == device or not P.is_provider(o):
            continue
        if wall_only and o.get("category") != "outlet":
            continue
        if C.room_of(state, oid) != rid or not (o.get("pos") or o.get("category") != "outlet"):
            continue
        if P.free_socket(state, oid) is None and not (ents[device].get("outlet") == oid and ents[device].get("plugged_in")):
            continue
        reach_ok = outlet_reachable(state, oid, safe=safe)
        if reachable and not reach_ok:
            continue
        if wall_plug:
            _, ocell = cell_of(state, oid)
            d = C.dist_cells(cell, ocell) if ocell else 99
            out.append(((False, 0, not reach_ok, d), oid, {"ok": True, "n": 0, "route": None, "wall": True}))
            continue
        opts = cord_options(state, device, oid, routes, check)
        if not opts:
            continue
        best = opts[0]
        out.append(((not best["ok"], best["n"], not reach_ok, best["length"]), oid, best))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def _unplug_config(config, device):
    """Take a device's plug out at setup time (no consequences) and clear its cord trip cells."""
    rec = config["entities"][device]
    rec["plugged_in"] = False
    rec["outlet"] = None
    rec.pop("_socket", None)
    rec["plug_seated"] = 0.0
    for key in ("cord_route", "cord_cells", "cord_length_used_m", "cord_hazard"):
        rec.pop(key, None)
    _clear_cord_hazards(config, device)


def _clear_cord_hazards(config, device):
    for room in config["rooms"].values():
        if any(h.get("source") == device and h.get("cord") for h in room.get("floor_hazards", []) or []):
            room["floor_hazards"] = [h for h in room["floor_hazards"] if not (h.get("source") == device and h.get("cord"))]


def _seat(config, device, outlet):
    from . import power as P
    rec = config["entities"][device]
    rec["plugged_in"] = False
    rec.pop("_socket", None)
    state = Placer(config, None, None).state()
    rec["_socket"] = P.free_socket(state, outlet) or 1
    rec["plugged_in"] = True
    rec["outlet"] = outlet
    rec["plug_seated"] = 1.0


def apply_cord(config, device, outlet, opt):
    """Plug `device` into `outlet` at setup time along a computed cord run (a cord_options dict)."""
    rec = config["entities"][device]
    _clear_cord_hazards(config, device)
    _seat(config, device, outlet)
    if rec.get("plug_in_place") == "wall":
        rec["location"], rec["relation"] = outlet, "attached"
        for key in ("pos", "spos", "shelf"):
            rec.pop(key, None)
        return []
    rec["cord_route"] = opt["route"]
    rec["cord_cells"] = [[rr, [c[0], c[1]]] for rr, c in opt["cells"]]
    rec["cord_length_used_m"] = opt["length"]
    rec["cord_hazard"] = list(opt["hazards"])
    for rid, c in opt["trip"]:
        config["rooms"][rid].setdefault("floor_hazards", []).append({"cell": list(c), "kind": "trip", "source": device, "cord": True})
    return list(opt["hazards"])


def dress_at_config(config, device, outlet):
    """Last resort at generation/setup: the cord is clipped along the baseboard and run behind furniture to the
    receptacle (cord_route "dressed": no floor trip cells, no hazards). A device with no receptacle at all in its room
    draws from the room circuit (outlet None) but is still seated."""
    rec = config["entities"][device]
    _clear_cord_hazards(config, device)
    if outlet:
        _seat(config, device, outlet)
    else:
        rec.update(plugged_in=True, outlet=None, plug_seated=1.0)
        rec.pop("_socket", None)
    rec["cord_route"] = "dressed"
    rec["cord_cells"] = []
    rec["cord_length_used_m"] = 0.0
    rec["cord_hazard"] = []


def _loose_on_floor(rec, rid):
    return rec.get("location") == rid and rec.get("pos") and rec.get("portable", True) and not rec.get("blocks_path") \
        and not rec.get("mounted")


def _move_beside(p, config, device, outlet):
    """Stand a loose floor device on a free cell next to the receptacle (keep_access: the move cuts no route).
    Returns the old pos (to restore) or None when nothing fits."""
    from .cords import cell_of
    rec = config["entities"][device]
    rid = rec.get("location")
    if rid not in config["rooms"] or not _loose_on_floor(rec, rid):
        return None
    state = p.state()
    o_room, o_cell = cell_of(state, outlet)
    if o_room != rid or o_cell is None:
        return None
    cell = geometry.free_floor_cell(state, rid, near=tuple(o_cell), ignore=(device,), record=rec, keep_access=True)
    if cell is None or geometry.chebyshev(tuple(cell), tuple(o_cell)) > 2:
        return None
    old = list(rec["pos"])
    rec["pos"] = list(cell)
    return old


def connect(p, config, device, outlet=None, route=None, reachable=False, safe=False, candidates=None, mount=True,
            move=True, obstruct=False, circuit=None, mount_kind=None, mount_height=None, wall_only=True):
    """Plug a corded device in at generation/setup time so that its cord never cuts a route (kernel v2.3 layout
    rule, cords.CordCheck). Returns the receptacle used (or None for room-circuit dressing).

    With an explicit `outlet`: the best acceptable run to it; else a loose floor device moves beside it; else the cord
    is dressed (obstruct=True keeps the raw best run, for setups that seed a cord hazard on purpose).
    Otherwise, in order: an existing receptacle with an acceptable hazard-free run (reachable ones first; reachable=True
    requires the robot to be able to stand at it, safe=True without stepping beside fragile things or sleepers); a
    receptacle mounted on a nearby reachable wall cell with a hazard-free run (mount=True); an existing receptacle with
    an acceptable run that has hazards (rug, water, heat, a walkway crossing that cuts nothing); a loose floor device
    moved beside a reachable receptacle; finally the cord is dressed to the nearest receptacle (mounted if the room has
    none). `candidates` restricts the receptacles considered (e.g. one circuit); `circuit` is given to mounted ones."""
    from . import power as P
    from .cords import CordCheck, cell_of, cord_options
    ents = config["entities"]
    rec = ents[device]
    routes = tuple(dict.fromkeys(([route] if route and route != "dressed" else []) + ["along_wall", "direct"]))
    if rec.get("plugged_in"):
        _unplug_config(config, device)
    state = p.state()
    rid, dcell = cell_of(state, device)
    if rid not in config["rooms"]:
        return None
    if P.is_provider(rec) and P.plugs_in(state, device):
        move = False          # a strip feeding a TV stand or desk stays where those cords reach it
    _, dcells = C.anchor_cells(state, device)
    dcells = dcells or ([dcell] if dcell else [])
    if outlet is not None:
        if rec.get("plug_in_place") == "wall":
            apply_cord(config, device, outlet, {"route": None})
            return outlet
        opts = cord_options(state, device, outlet, routes, None if obstruct else CordCheck(state, device))
        if opts and opts[0]["ok"]:
            apply_cord(config, device, outlet, opts[0])
            return outlet
        reaches = bool(opts)
        if move:
            old = _move_beside(p, config, device, outlet)
            if old is not None:
                st = p.state()
                moved = cord_options(st, device, outlet, routes, CordCheck(st, device))
                if moved and moved[0]["ok"]:
                    apply_cord(config, device, outlet, moved[0])
                    return outlet
                rec["pos"] = old
        if not reaches:
            return None          # the cord does not reach that receptacle (left unplugged)
        dress_at_config(config, device, outlet)
        return outlet
    ranked = rank_outlets(config, device, candidates, reachable=reachable, safe=safe, routes=routes, wall_only=wall_only)
    best = ranked[0] if ranked else None
    if best is not None and best[2]["ok"] and best[2]["n"] == 0 and not best[0][2]:
        apply_cord(config, device, best[1], best[2])
        return best[1]
    if mount and rec.get("plug_in_place") != "wall":
        oid = mount_clear_outlet(p, config, device, kind=mount_kind, height=mount_height, circuit=circuit, safe=safe)
        if oid is not None:
            st = p.state()
            opts = cord_options(st, device, oid, routes, CordCheck(st, device))
            apply_cord(config, device, oid, opts[0])
            return oid
    if best is not None and best[2]["ok"]:
        apply_cord(config, device, best[1], best[2])
        return best[1]
    if move and _loose_on_floor(rec, rid):
        pool = [o for o in (candidates if candidates is not None else sorted(ents)) if ents.get(o, {}).get("category") == "outlet"
                and C.room_of(state, o) == rid and ents[o].get("pos") and outlet_reachable(state, o, safe=safe)]
        pool.sort(key=lambda o: (min(geometry.chebyshev(tuple(ents[o]["pos"]), d) for d in dcells) if dcells else 0, o))
        for oid in pool[:6]:
            if P.free_socket(p.state(), oid) is None:
                continue
            old = _move_beside(p, config, device, oid)
            if old is None:
                continue
            st = p.state()
            opts = cord_options(st, device, oid, routes, CordCheck(st, device))
            if opts and opts[0]["ok"]:
                apply_cord(config, device, oid, opts[0])
                return oid
            rec["pos"] = old
    target = best[1] if best is not None else None
    if target is None and mount:
        # no receptacle the cord reaches: an electrician's receptacle on the nearest reachable wall cell
        target = mount_outlet_near(p, config, rid, dcells, kind=mount_kind, height=mount_height, circuit=circuit, safe=safe)
        if target is not None:
            st = p.state()
            opts = cord_options(st, device, target, routes, CordCheck(st, device))
            if rec.get("plug_in_place") == "wall" or (opts and opts[0]["ok"]):
                apply_cord(config, device, target, opts[0] if opts else {"route": None})
                return target
    if target is None:
        free = [o for o in (candidates if candidates is not None else sorted(ents)) if ents.get(o, {}).get("category") == "outlet"
                and C.room_of(state, o) == rid and ents[o].get("pos") and P.free_socket(state, o) is not None
                and (outlet_reachable(state, o, safe=safe) or not reachable)]
        free.sort(key=lambda o: (not outlet_reachable(state, o), min(geometry.chebyshev(tuple(ents[o]["pos"]), d) for d in dcells)
                                 if dcells else 0, o))
        target = free[0] if free else None
    if target is None and mount:
        target = mount_outlet_near(p, config, rid, dcells, kind=mount_kind, height=mount_height, circuit=circuit, safe=safe,
                                   relaxed=True)
    if rec.get("plug_in_place") == "wall" and target is not None:
        apply_cord(config, device, target, {"route": None})
        return target
    dress_at_config(config, device, target)
    return target


def preplug(config, registry, device, outlet=None, rng=None, route=None):
    """Public generation-time plug-in for other domains' furnishers (round-3 request from navigation_robot: docks
    pre-plugged at furnish time wrote electrical's private `_socket` and cord fields themselves). Seats `device` in
    `outlet` (or the best receptacle by the cord rule: an acceptable, hazard-free run that cuts no route, else a clean
    receptacle mounted nearby, else a dressed cord) and writes every plug/cord field consistently. Returns the
    receptacle used (None when the device draws from its room circuit). electrical.hookup skips devices plugged in
    this way."""
    import random as _random
    p = Placer(config, registry, rng or _random.Random("preplug:{}".format(device)))
    rec = config["entities"][device]
    rec.setdefault("plug_required", True)
    if not rec.get("plug_type"):
        rec.update(plug_props(config, grounded=C.num(rec.get("power_w"), 0) >= 500))
    return connect(p, config, device, outlet=outlet, route=route)


def nearest_outlet(config, eid, cord_m, exclude=(), reachable=False, avoid_hazards=False):
    """Best receptacle in the device's room with a free socket whose cord run reaches (rank_outlets; kept for
    callers that only want the choice). exclude: receptacles to skip."""
    ranked = [t for t in rank_outlets(config, eid, reachable=reachable) if t[1] not in exclude]
    return ranked[0][1] if ranked else None


def plug_at_config(config, device, outlet, route="along_wall", obstruct=False):
    """Setup-time plug-in into a given receptacle along the best acceptable cord run (cords.CordCheck; obstruct=True
    accepts any run the cord reaches). Returns the run's hazard kinds, or None (device left unplugged) when the cord
    does not reach or every run would cut a route / lie in a doorway clearance."""
    from .cords import CordCheck, cord_options
    rec = config["entities"][device]
    if rec.get("plugged_in"):
        _unplug_config(config, device)
    if rec.get("plug_in_place") == "wall":
        return apply_cord(config, device, outlet, {"route": None})
    state = Placer(config, None, None).state()
    routes = tuple(dict.fromkeys(([route] if route else []) + ["along_wall", "direct"]))
    opts = cord_options(state, device, outlet, routes, None if obstruct else CordCheck(state, device))
    if not opts or not opts[0]["ok"]:
        return None
    return apply_cord(config, device, outlet, opts[0])


def _panel_obstructed(config, state, panel):
    """Another deep wall mount (boiler, cabinet, water heater) shares the panel's wall space at overlapping heights,
    or a taller floor-standing piece stands in front of it (the layout audit's "mount overlap" / "hidden mount")."""
    rec = config["entities"][panel]
    span = _mount_span(rec)
    if span is None:
        return False
    cells = {tuple(c) for c in geometry.footprint(rec)}
    for eid in geometry.room_entities(state, rec["location"]):
        if eid == panel:
            continue
        other = config["entities"].get(eid) or {}
        if not other.get("pos") or other.get("ground_layer") or "connects" in other:
            continue
        if not cells & {tuple(c) for c in geometry.footprint(other)}:
            continue
        o_span = _mount_span(other)
        if o_span is None:
            continue
        size = other.get("size_m") or [0.1, 0.1, 0.1]
        try:
            depth = min(float(size[0]), float(size[1]))
        except (TypeError, ValueError, IndexError):
            depth = 0.0
        if geometry.floor_standing(other):
            if o_span[1] > span[0] + 0.05:
                return True
        elif other.get("mounted") and other.get("category") not in ("outlet", "light", "window", "drawer") and depth >= 0.1 \
                and o_span[0] < span[1] and span[0] < o_span[1]:
            return True
    return False


def keep_panel_clear(p, config):
    """Working space in front of the breaker panel (NEC 110.26: ~0.9 m clear): when furniture placed after the
    wiring (workbenches, shelves) blocked every cell beside it, or a later fixture (a boiler, a cabinet) was hung
    over its wall spot, the panel goes to the nearest free wall spot the robot can stand at instead."""
    ents = config["entities"]
    panel = ((config.get("metadata") or {}).get("electrical") or {}).get("panel")
    rec = ents.get(panel or "")
    if not rec or rec.get("location") not in config["rooms"] or not rec.get("pos"):
        return
    rid = rec["location"]
    state = p.state()
    if approachable(state, rid, [rec["pos"]]) and not _panel_obstructed(config, state, panel):
        return
    lo, hi = _mount_span(rec) or (1.1, 1.7)
    overlap = mount_cells_taken(config, rid, lo, hi, ignore=(panel,))
    taken = {tuple(o["pos"]) for e, o in ents.items() if e != panel and o.get("location") == rid and o.get("mounted") and o.get("pos")}
    # a wall spot with no other mount at all, else one whose mounts sit wholly above or below the panel (crowded closets)
    cells = wall_cells(p, rid, 0.3, taken | overlap, state) or wall_cells(p, rid, 0.3, overlap, state)
    if not cells:
        # a closet crammed with a boiler and a water heater: the electrician puts the panel in the next room out
        for other in sorted(config["rooms"][rid].get("exits", [])):
            room = config["rooms"].get(other) or {}
            if room.get("outdoor") or room.get("type") in SHARED:
                continue
            free = wall_cells(p, other, 0.3, mount_cells_taken(config, other, lo, hi, ignore=(panel,)), state)
            if free:
                rec["location"] = other
                rec["pos"] = list(sorted(free, key=lambda c: (c[1], c[0]))[0])
                return
        return
    here = tuple(rec["pos"])
    cells.sort(key=lambda c: (geometry.chebyshev(c, here), c[1], c[0]))
    rec["pos"] = list(cells[0])


def keep_outlets_clear(p, config):
    """Receptacles nothing is plugged into yet that furniture or floor hazards placed after the wiring left without
    hazard-free access (behind a wardrobe, past a robot-vacuum dock zone) are re-sited on the nearest wall cell the
    robot reaches without stepping on anything: the electrician would have put them there."""
    ents = config["entities"]
    plugged = {r.get("outlet") for r in ents.values() if r.get("plugged_in") and r.get("outlet")}
    for oid in sorted(ents):
        rec = ents[oid]
        if rec.get("category") != "outlet" or not rec.get("mounted") or rec.get("floor_box") or oid in plugged:
            continue
        rid = rec.get("location")
        if rid not in config["rooms"] or not rec.get("pos") or config["rooms"][rid]["type"] in SHARED:
            continue
        height = C.num(rec.get("mount_height_m"), 0.3)
        taken = {c for c in _outlet_cells_taken(p, config, rid) if c != tuple(rec["pos"])}
        taken |= mount_cells_taken(config, rid, height, ignore=(oid,))     # never on a valve, meter or radiator
        state = p.state()
        clean = wall_cells(p, rid, height, taken, state, hazard_free=True)
        if not clean or tuple(rec["pos"]) in clean:
            continue
        here = tuple(rec["pos"])
        rec["pos"] = list(min(clean, key=lambda c: (geometry.chebyshev(c, here), c[1], c[0])))


def hookup(config, rng, registry):
    ents = config["entities"]
    region = region_of(config)
    meta = (config.get("metadata") or {}).get("electrical") or {}
    part, base = BULB_PART[region]
    p = Placer(config, registry, rng)
    keep_outlets_clear(p, config)
    for eid in sorted(ents):
        rec = ents[eid]
        if not rec.get("plug_required"):
            continue
        if not rec.get("plug_type"):
            rec.update(plug_props(config, grounded=C.num(rec.get("power_w"), 0) >= 500))
        if rec.get("plug_type") in ("type_a", "type_b") and region in ("uk", "eu"):
            rec.update(plug_props(config, grounded=rec.get("plug_type") == "type_b"))
        if rec.get("outlet_kind") in ("strip", "extension", "smart_plug", "timer") and rec.get("socket_type") == "type_b" and region not in ("us", "ca"):
            rec["socket_type"] = C.REGION[region]["socket"]
        if not rec.get("plugged_in") or rec.get("outlet"):
            continue
        if not rec.get("portable"):
            room = (config["rooms"].get(C.room_of(p.state(), eid) or "") or {})
            if room.get("type") == "kitchen" and meta.get("appliance_circuit"):
                rec["circuit"] = meta["appliance_circuit"]
            elif room.get("type") == "laundry_room" and meta.get("laundry_circuit"):
                rec["circuit"] = meta["laundry_circuit"]
            rec["dedicated_circuit"] = True
            continue
        # a receptacle whose cord run cuts no route (else a clean new one nearby, the device moved, or a dressed cord);
        # every plugged device ends up seated in a receptacle (no "plugged in" device without power)
        connect(p, config, eid)
    for eid in sorted(ents):
        if ents[eid].get("category") == "dock" and not ents[eid].get("circuit"):
            room = config["rooms"].get(C.room_of(p.state(), eid) or "") or {}
            if room.get("circuit"):
                ents[eid]["circuit"] = room["circuit"]
    # bulbs: socket plain core lamps, fill empty bulb slots, match the regional base
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("category") != "light":
            continue
        if rec.get("plug_required") and not rec.get("slots") and rec.get("type") == "lamp":
            socket_fixture(p, eid, 1, "led_bulb_e26", max_w=60.0)
            rec.setdefault("lamp_switch", "rotary")
            continue
        for slot, info in sorted((rec.get("slots") or {}).items()):
            if not any(str(a).startswith("bulb_") for a in info.get("accepts", [])):
                continue
            if info.get("accepts") == ["bulb_e26"] and part != "bulb_e26":
                info["accepts"] = [part]
            if not info.get("item"):
                kind = "three_way_bulb" if rec.get("lamp_switch") == "three_way_socket" else "led_bulb_e26"
                add_bulb(p, eid, slot, kind)
    keep_panel_clear(p, config)
    settle(config)


@furnisher("electrical.wiring", order=25)
def furnish_wiring(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    build_wiring(p, config, rng)
    # a couple of socketed ceiling fixtures (others are integrated LED)
    for rtype in ("kitchen", "hallway", "dining_room"):
        for rid in p.rooms(rtype)[:1]:
            for lid in p.find("ceiling_light", room=rid)[:1]:
                socket_fixture(p, lid, 2 if rtype != "hallway" else 1, "led_bulb_e26", max_w=60.0)
    build_detectors(p, config, rng)


@furnisher("electrical.devices", order=75)
def furnish_devices(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    build_media(p, config, rng)
    build_network(p, config, rng)
    build_desk(p, config, rng)
    build_bedrooms(p, config, rng)
    build_spares(p, config, rng)
    build_garage(p, config, rng)


@furnisher("electrical.hookup", order=97)
def furnish_hookup(config, rng, opts, registry):
    hookup(config, rng, registry)
