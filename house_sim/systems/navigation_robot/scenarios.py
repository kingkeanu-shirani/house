"""Scenario helpers (setup op {"op": "helper", "name": "navigation_robot.<name>", "args": {...}}).

hazard_band     put a hazard across the robot's direct route through a room toward a doorway
                (a detour always exists): wet film, puddle, slip, oil, ice, mud, gravel, waste,
                seeded, bed, loose_board, hole, sprinkler, slab_edge, edge_drop, cord (tethered to a
                laptop on a table), toys (marble/LEGO build/toy car), shards, rug (no pad),
                fragile (floor vase on the route), sleeping_dog, low_obstacle, glass
stairs          decorate the main staircase: items on steps, wet treads, winders, loose rail
dark_room       lights off and blinds drawn in a room; optional sleeper; binds its doorway switch
sleeper         a sleeping person or pet in a room (optionally on a given cell)
dock_setup      robot battery and reserve, dock blocked/unplugged/moved (with a stale memory of the old spot)
home_pose       configure the robot's park pose; optionally occupy it
measure_target  an object/doorway/space with hidden true dimensions; binds truth in mm and inches
high_item       an item on a wall shelf at a height plus the reach aids available
clutter_route   clutter on a route inside one room between two doorways, plus a watched route
watch_route     evaluate a route's clear width every tick (nav.routes.<name>)
watch_doors     evaluate door swing/approach clearance every tick (nav.doors.<door>)
robot_vacuum    a robot vacuum (+ dock) in a state: stuck, bin full, hair, mop tank, schedule
person_route    a hidden walking route for follow tasks
pet_follower    make a pet tag along behind the robot
elevator        enable the building elevator model (apartments)
vehicle         a parked car on the driveway/garage that will reverse at a time
no_go           a time-window no-go rule on a room
leveling        an appliance/furniture on leveling feet over an uneven floor; binds corrective feet
"""
import copy
import math

from ...generator.place import Placer  # noqa: F401
from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from . import common as C
from . import measure as M


# rooms that serve the same purpose when a layout lacks the named type (apartments often have only a master bedroom,
# small houses no office); tried only after every requested type came up empty
SIMILAR_ROOMS = {
    "bedroom": ("master_bedroom", "guest_room", "playroom"),
    "master_bedroom": ("bedroom", "guest_room"),
    "guest_room": ("bedroom", "master_bedroom"),
    "office": ("craft_room", "guest_room", "bedroom", "master_bedroom"),
    "laundry_room": ("utility_closet", "basement", "garage", "mudroom"),
    "garage": ("driveway", "basement", "shed"),
    "hallway": ("entryway", "mudroom", "living_room"),
    "entryway": ("hallway", "mudroom"),
    "living_room": ("dining_room", "playroom", "office"),
    "dining_room": ("kitchen", "living_room"),
    "kitchen": ("pantry", "dining_room"),
    "utility_closet": ("laundry_room", "pantry", "coat_closet"),
    "backyard": ("garden", "patio", "deck", "front_yard"),
    "driveway": ("garage",),
}


def _room_kinds(kinds):
    """The requested room types, then their stand-ins (deduplicated, order kept)."""
    out = []
    for kind in list(kinds) + [alt for kind in kinds for alt in SIMILAR_ROOMS.get(kind, ())]:
        if kind and kind not in out:
            out.append(kind)
    return out


def _room(ctx, room=None, room_type=None, prefer=()):
    if room and room in ctx.config["rooms"]:
        return room
    kinds = [room_type] if room_type else list(prefer)
    for kind in kinds:
        options = ctx.placer.rooms(kind)
        if options:
            return ctx.rng.choice(options)
    if room:
        options = ctx.placer.rooms(room)
        if options:
            return ctx.rng.choice(options)
    for kind in _room_kinds(kinds + ([room] if room else [])):
        options = ctx.placer.rooms(kind)
        if options:
            return ctx.rng.choice(options)
    raise SpecError("navigation_robot: no room of type {} (add house.require)".format(room_type or room or prefer))


def _state(ctx):
    return ctx.placer.state()


def _robot_at(ctx, rid, cell):
    robot = ctx.config.setdefault("robot", {})
    robot["room"] = rid
    robot["pos"] = [int(cell[0]), int(cell[1])]
    robot["docked"] = False
    robot.setdefault("hands", {"left": None, "right": None})


def _connections(ctx, rid, kinds=("doorway",), outdoor=None):
    out = []
    for cid in sorted(ctx.config.get("connections", {})):
        conn = ctx.config["connections"][cid]
        if rid not in conn["rooms"] or conn.get("kind", "doorway") not in kinds:
            continue
        other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
        if outdoor is not None and bool(ctx.config["rooms"][other].get("outdoor")) != outdoor:
            continue
        out.append((cid, other))
    return out


def _free_cells(ctx, rid, avoid_ring=True):
    state = _state(ctx)
    room = ctx.config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid))
    used = set()
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if not rec.get("ground_layer") and "connects" not in rec and not rec.get("mounted"):
            used.update(geometry.footprint(rec))
    ring = set()
    if avoid_ring:
        for cid, conn in ctx.config.get("connections", {}).items():
            if rid in conn.get("cells", {}):
                c = C.cell(conn["cells"][rid])
                ring |= geometry.near_cells([c], room, 1)
    reach = _reachable(ctx, rid)
    return [(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked and (x, y) not in used and (x, y) not in ring and (x, y) in reach]


def _reachable(ctx, rid):
    """Floor cells reachable on foot from the room's doorways (excludes pockets behind furniture)."""
    from collections import deque
    state = _state(ctx)
    room = ctx.config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid))
    starts = [C.cell(conn["cells"][rid]) for cid, conn in sorted(ctx.config.get("connections", {}).items()) if rid in conn.get("cells", {})]
    seen = set(c for c in starts if c not in blocked)
    queue = deque(seen)
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if 0 <= n[0] < cols and 0 <= n[1] < rows and n not in blocked and n not in seen:
                seen.add(n)
                queue.append(n)
    return seen


def _lateral(path, i):
    a = path[max(0, i - 1)]
    b = path[min(len(path) - 1, i + 1)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) >= abs(dy):
        return (0, 1)
    return (1, 0)


def _spawn_at(ctx, rid, cell, type_name, eid=None, **props):
    p = ctx.placer
    rec = p.reg.make(type_name, rid, **props)
    eid = eid or p.new_id(props.get("label", type_name).replace(" ", "_") if "label" in props else type_name)
    rec["pos"] = [int(cell[0]), int(cell[1])]
    rec.setdefault("relation", "on")
    ctx.config["entities"][eid] = rec
    return eid


FALLBACK_BAND_ROOMS = ("kitchen", "living_room", "hallway", "dining_room", "entryway", "mudroom", "office", "playroom",
                       "bedroom", "master_bedroom", "guest_room", "laundry_room", "backyard", "patio", "front_yard", "garden")

HAZARD_ENTRY_KINDS = ("slip", "oil", "ice", "black_ice", "mud", "gravel", "waste", "seeded", "bed", "planted", "loose_board", "hole",
                      "sprinkler", "slab_edge", "curb", "edge_drop", "narrow", "moss", "uneven", "slope")


@scenario_helper("navigation_robot.hazard_band")
def hazard_band(ctx, room=None, room_type=None, kind="wet", exit_to=None, width=1, props=None, robot=True, prefer=("kitchen", "living_room", "hallway"),
                min_path=5, near_start=False):
    candidates = []
    if room and room in ctx.config["rooms"]:
        candidates = [room]
    else:
        kinds = [room_type] if room_type else list(prefer)
        for k in kinds:
            options = ctx.placer.rooms(k)
            ctx.rng.shuffle(options)
            candidates.extend(options)
        if not room_type and not room:
            # every preferred room may be too furnished for a band with a detour: rooms serving the same purpose, then
            # any other indoor living space, are tried in turn (outdoor kinds stay outdoors)
            outdoor = any(ctx.config["rooms"][r].get("outdoor") for r in candidates)
            extra = [r for k in _room_kinds(kinds)[len(kinds):] + list(FALLBACK_BAND_ROOMS) for r in ctx.placer.rooms(k)
                     if r not in candidates and bool(ctx.config["rooms"][r].get("outdoor")) == outdoor
                     and not any(ctx.config["rooms"][r].get(f) for f in ("shared", "private", "off_limits"))]
            candidates.extend(r for n, r in enumerate(extra) if r not in extra[:n])
    if not candidates:
        raise SpecError("hazard_band: no room of type {}".format(room_type or prefer))
    found, device = None, None
    for rid in candidates:
        for band in _bands_in_room(ctx, rid, kind, exit_to, width, min_path, near_start):
            if kind == "cord":
                # the cord runs from a laptop on a small table beside the route: that table needs a spot that keeps the
                # cord on the direct route and a detour around both; otherwise try the next band
                device = _laptop_on_table(ctx, band[0], band[5][0], band[4], band[3])
                if device is None:
                    continue
            found = band
            break
        if found:
            break
    if not found:
        raise SpecError("hazard_band: no route with a detour in {}".format(candidates))
    rid, cid, other, goal, start, chosen = found
    room = ctx.config["rooms"][rid]
    props = dict(props or {})
    made = []
    if kind == "wet":
        wet = room.setdefault("wet_cells", [])
        for c in chosen:
            if [c[0], c[1]] not in wet:
                wet.append([c[0], c[1]])
    elif kind == "puddle":
        # standing water: cleaning's spill type (liquid_l evaporates slowly, ~1.5 mm deep over 0.2 m2) when registered
        spill_type = "spill" if ctx.placer.reg.known("spill") else "generic_item"
        liters = float(props.get("liquid_l", 0.3))
        for c in chosen:
            made.append(_spawn_at(ctx, rid, c, spill_type, label=props.get("label", "puddle of water"), category="spill", floor_hazard="wet",
                                  portable=False, blocks_path=False, liquid=props.get("liquid", "water"), liquid_l=liters, spill_l=liters,
                                  volume_l=0.0, mass_kg=0.0, area_m2=round(liters / 1.5, 3), size_m=[0.45, 0.45, 0.002]))
    elif kind in HAZARD_ENTRY_KINDS:
        hz = room.setdefault("floor_hazards", [])
        for c in chosen:
            entry = {"cell": [c[0], c[1]], "kind": kind, "source": props.get("source", "scenario")}
            entry.update({k: v for k, v in props.items() if k not in ("source",)})
            hz.append(entry)
    elif kind == "cord":
        made.append(_spawn_at(ctx, rid, chosen[0], "generic_item", label=props.get("label", "laptop charging cable"), category="cord",
                              floor_hazard="cord", cord_to=[device], size_m=[0.5, 0.02, 0.01], mass_kg=0.15, low_contrast=bool(props.get("low_contrast"))))
        made.append(device)
    elif kind == "toys":
        specs = [("LEGO build", {"category": "toy", "fragile": True, "crushable": True, "floor_hazard": "fragile", "size_m": [0.3, 0.3, 0.2], "mass_kg": 0.4, "sentimental": True}),
                 ("marbles", {"category": "toy", "rolls": True, "floor_hazard": "trip", "size_m": [0.2, 0.2, 0.02], "mass_kg": 0.1}),
                 ("toy car", {"category": "toy", "rolls": True, "floor_hazard": "trip", "size_m": [0.12, 0.06, 0.05], "mass_kg": 0.08})]
        for n, c in enumerate(chosen):
            label, p = specs[n % len(specs)]
            made.append(_spawn_at(ctx, rid, c, "generic_item", label=label, **p))
    elif kind == "shards":
        for c in chosen:
            made.append(_spawn_at(ctx, rid, c, "generic_item", label="broken glass shards", category="shards", floor_hazard="sharp",
                                  hazards=["sharp"], portable=False, ground_layer=False, size_m=[0.5, 0.5, 0.01], mass_kg=0.2, transparent=True))
    elif kind == "rug":
        made.append(_spawn_at(ctx, rid, chosen[0], "rug", label="unpadded runner", non_slip_pad=False, size_m=[0.5, 0.5, 0.01], **props))
    elif kind == "fragile":
        made.append(_spawn_at(ctx, rid, chosen[0], "generic_item", label=props.get("label", "tall floor vase"), category="decor", fragile=True,
                              topple_prone=True, blocks_path=True, portable=True, material="ceramic", size_m=[0.3, 0.3, 0.9], mass_kg=6.0, value=300.0))
    elif kind in ("sleeping_dog", "sleeping_cat"):
        pid = props.get("id") or ("dog_nav" if kind == "sleeping_dog" else "cat_nav")
        ctx.config.setdefault("pets", {})[pid] = {"label": props.get("label", "Rex" if kind == "sleeping_dog" else "Mittens"),
                                                  "species": "dog" if kind == "sleeping_dog" else "cat", "location": rid,
                                                  "pos": [chosen[0][0], chosen[0][1]], "posture": "sleeping", "awake": False, "facing_deg": 0.0}
        made.append(pid)
    elif kind == "low_obstacle":
        made.append(_spawn_at(ctx, rid, chosen[0], "generic_item", label=props.get("label", "open dishwasher door"), category="appliance_part",
                              low_obstacle=True, floor_hazard="low_obstacle", portable=False, size_m=[0.5, 0.5, 0.25], mass_kg=8.0))
    elif kind == "glass":
        # props.reflective / props.category "mirror": a mirrored or reflective panel that realistic perception shows as an
        # open doorway until it is inspected
        extra = {k: props[k] for k in ("reflective",) if k in props}
        made.append(_spawn_at(ctx, rid, chosen[0], "generic_item", label=props.get("label", "glass partition"), category=props.get("category", "glass_panel"),
                              glass_panel=True, transparent=True, portable=False, material=props.get("material", "glass"), fragile=True,
                              size_m=[0.5, 0.5, 2.0], mass_kg=25.0, **extra))
    else:
        raise SpecError("hazard_band: unknown kind " + str(kind))
    if robot:
        _robot_at(ctx, rid, start)
    out = {"room": rid, "goal_room": other, "conn": cid, "start": [start[0], start[1]], "goal_cell": [goal[0], goal[1]],
           "hazard_cells": [[c[0], c[1]] for c in chosen], "hazards": made, "hazard": made[0] if made else None}
    if kind == "cord":
        out.update(cord=made[0], device=made[1], device_table=ctx.config["entities"][made[1]]["location"])
    return out


def _pre_hazards(state, rid):
    """Cells of hazards other domains already put on this floor that a careful plan routes around."""
    return {c for c, items in geometry.hazard_cells(state, rid).items() if any(k in PRE_EXISTING_AVOID for k, _ in items)}


def _bands_in_room(ctx, rid, kind, exit_to, width, min_path, near_start):
    """Candidate bands (rid, conn, other room, goal cell, start cell, band cells), best first: the band lies on the
    direct route from start to the doorway and a detour around it (and around pre-existing hazards) exists."""
    state = _state(ctx)
    room = ctx.config["rooms"][rid]
    conns = _connections(ctx, rid, kinds=("doorway", "door", "gate", "garage"))
    if exit_to:
        conns = [(cid, o) for cid, o in conns if o == exit_to or ctx.config["rooms"][o].get("type") == exit_to] or conns
    conns = [(cid, o) for cid, o in conns if _usable_exit(ctx, cid, o)]
    margin_kinds = ("fragile", "sleeping_dog", "sleeping_cat")
    blocked_now = geometry.blocked_cells(state, rid)
    # hazards other domains already put on this floor (device cords routed across a walkway, curled mats, loose
    # items, wet cells): the detour must avoid them too, so a careful plan never trades one hazard for another
    pre = _pre_hazards(state, rid)
    for cid, other in conns:
        goal = C.cell(ctx.config["connections"][cid]["cells"][rid])
        starts = sorted((c for c in _free_cells(ctx, rid) if c not in pre), key=lambda c: (-geometry.chebyshev(c, goal), c[1], c[0]))[:6]
        for start in starts:
            path = geometry.find_path(state, rid, start, [goal])
            if not path or len(path) < min_path:
                continue
            order = sorted(range(1, len(path) - 1), key=lambda i: (abs(i - len(path) // 2), i)) if not near_start else list(range(1, len(path) - 1))
            for i in order:
                center = path[i]
                lat = _lateral(path, i)
                cells = [center]
                for k in range(1, int(width)):
                    side = (k + 1) // 2 * (1 if k % 2 else -1)
                    c2 = (center[0] + lat[0] * side, center[1] + lat[1] * side)
                    if geometry.in_grid(room, c2):
                        cells.append(c2)
                if any(c in blocked_now for c in cells):
                    continue
                extra = set(cells)
                if kind in margin_kinds:
                    extra |= set(geometry.near_cells(cells, room, 1))
                extra -= {start, goal}
                if geometry.find_path(state, rid, start, [goal], extra_blocked=sorted(extra | (pre - {start, goal}))) is None:
                    continue
                yield rid, cid, other, goal, start, cells


# kinds a careful plan routes around (the kernel skills' SAFE_AVOID); hazard_band keeps a detour clear of them
PRE_EXISTING_AVOID = ("wet", "slip", "trip", "sharp", "fragile", "sleeping", "person_down", "floor_item", "hot", "spill", "cord")


def _usable_exit(ctx, cid, other):
    """A doorway the robot can simply walk through: no closed/locked door, lit, not private."""
    room = ctx.config["rooms"][other]
    if room.get("off_limits") or room.get("private") or room.get("shared") or room.get("type") in ("coat_closet", "linen_closet", "utility_closet", "elevator"):
        return False
    door = ctx.config["entities"].get(ctx.config["connections"][cid].get("door") or "")
    if door and (not door.get("open") or door.get("locked")):
        return False
    return bool(room.get("lit", True))


def _laptop_on_table(ctx, rid, cord_cell, start, goal):
    """A laptop on a small table beside the route, plugged in by the cord lying across it. The table takes a free,
    unused cell within 2 cells of the cord, the direct route must still cross the cord, and a detour around the table,
    the cord and the floor's pre-existing hazards must remain. None when no cell qualifies."""
    state = _state(ctx)
    room = ctx.config["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid))
    used = set()
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if not rec.get("ground_layer") and "connects" not in rec and not rec.get("mounted"):
            used.update(geometry.footprint(rec))
    pre = _pre_hazards(state, rid) - {C.cell(start), C.cell(goal)}
    doors = geometry.door_clearance(state, rid)
    for c in sorted(geometry.near_cells([cord_cell], room, 2), key=lambda q: (geometry.chebyshev(q, cord_cell), q[1], q[0])):
        if c in blocked or c in used or c in doors or c in (start, goal) or c == cord_cell:
            continue
        if geometry.find_path(state, rid, start, [goal], extra_blocked=sorted(pre | {c, C.cell(cord_cell)})) is None:
            continue
        table = _spawn_at(ctx, rid, c, "side_table", label="laptop table", blocks_path=True)
        direct = geometry.find_path(_state(ctx), rid, start, [goal]) or []
        if C.cell(cord_cell) not in [C.cell(q) for q in direct]:
            del ctx.config["entities"][table]
            continue
        laptop = ctx.placer.on(table, "generic_item", label="laptop", category="laptop", electronic=True, fragile=True, plug_required=True,
                               plugged_in=True, size_m=[0.33, 0.23, 0.02], mass_kg=1.4, value=1200.0)
        if laptop:
            return laptop
        del ctx.config["entities"][table]
    return None


@scenario_helper("navigation_robot.stairs")
def stairs(ctx, items=None, wet=False, winders=0, loose_rail=False, rail=True, width_m=None, run_m=None, rise_m=None, robot="bottom",
           baby_gate=None):
    conns = []
    for cid in sorted(ctx.config.get("connections", {})):
        conn = ctx.config["connections"][cid]
        if conn.get("kind") != "stairs":
            continue
        a, b = conn["rooms"]
        fa, fb = ctx.config["rooms"][a].get("floor", 0), ctx.config["rooms"][b].get("floor", 0)
        conns.append((0 if min(fa, fb) >= 0 else 1, cid))
    if not conns:
        raise SpecError("navigation_robot.stairs: the house has no stairs (add house.require ['stairs'])")
    cid = sorted(conns)[0][1]
    conn = ctx.config["connections"][cid]
    a, b = conn["rooms"]
    bottom, top = (a, b) if ctx.config["rooms"][a].get("floor", 0) < ctx.config["rooms"][b].get("floor", 0) else (b, a)
    conn["handrail"] = bool(rail)
    if wet:
        conn["tread_wet"] = True
        conn.setdefault("tread_material", "hardwood")
    if winders:
        conn["winders"] = int(winders)
    for key, value in (("width_m", width_m), ("run_m", run_m), ("rise_m", rise_m)):
        if value is not None:
            conn[key] = value
    rail_id = None
    for eid in sorted(ctx.config["entities"]):
        if ctx.config["entities"][eid].get("on_stairs") == cid and ctx.config["entities"][eid].get("category") == "handrail":
            rail_id = eid
    if rail and rail_id is None:
        from .furnish import _handrail
        rail_id = _handrail(ctx.placer, ctx.placer.reg, ctx.config, bottom, cid, conn)
    if rail_id:
        ctx.config["entities"][rail_id]["_loose"] = bool(loose_rail)
    made = []
    steps = int(conn.get("steps", 14))
    for n, item in enumerate(items or []):
        step = int(item.get("step", (n + 3) % steps or 2))
        where = bottom if step <= steps // 2 else top
        props = dict(item.get("props") or {})
        props.setdefault("label", item.get("label", "shoe"))
        eid = _spawn_at(ctx, where, conn["cells"][where], item.get("type", "generic_item"), on_stairs=cid, stair_step=step, **props)
        made.append(eid)
    gate = None
    if baby_gate in ("top", "bottom"):
        end = top if baby_gate == "top" else bottom
        other = bottom if end == top else top
        rec = ctx.placer.reg.make("gate", end, label="baby gate", material="steel", size_m=[0.8, 0.05, 0.8], open=False, latched=True,
                                  self_latching=True, child_safety=True, exterior=False)
        rec["connects"] = [end, other]
        rec["pos"] = list(conn["cells"][end])
        rec["blocks_path"] = False
        gate = ctx.placer.new_id("baby_gate")
        ctx.config["entities"][gate] = rec
        conn["door"] = gate
    if robot in ("bottom", "top"):
        rid = bottom if robot == "bottom" else top
        cells = sorted(_free_cells(ctx, rid), key=lambda c: (geometry.chebyshev(c, C.cell(conn["cells"][rid])), c[1], c[0]))
        if cells:
            _robot_at(ctx, rid, cells[min(2, len(cells) - 1)])
    return {"stairs": cid, "bottom": bottom, "top": top, "rail": rail_id, "stair_items": made, "stair_item": made[0] if made else None,
            "baby_gate": gate}


def _switch_for(ctx, rid):
    for eid in sorted(ctx.config["entities"]):
        rec = ctx.config["entities"][eid]
        if rec.get("category") == "switch" and rec.get("location") == rid and any(
                ctx.config["entities"].get(c, {}).get("location") == rid for c in rec.get("controls") or []):
            return eid
    return None


@scenario_helper("navigation_robot.dark_room")
def dark_room(ctx, room=None, room_type=None, prefer=("bedroom", "office", "guest_room"), sleeper=None, sleeper_role="child", lit=False,
              robot_near=True):
    rid = _room(ctx, room, room_type, prefer)
    for eid, rec in sorted(ctx.config["entities"].items()):
        if rec.get("category") == "window" and rec.get("location") == rid:
            rec["covered"] = 1.0
        if rec.get("category") in ("light", "switch") and (rec.get("location") == rid or any(
                ctx.config["entities"].get(c, {}).get("location") == rid for c in rec.get("controls") or [])) and not rec.get("battery_powered"):
            rec["on"] = bool(lit)
            for c in rec.get("controls") or []:
                if c in ctx.config["entities"]:
                    ctx.config["entities"][c]["on"] = bool(lit)
    out = {"room": rid, "switch": _switch_for(ctx, rid)}
    lights = [e for e in sorted(ctx.config["entities"]) if ctx.config["entities"][e].get("category") == "light" and ctx.config["entities"][e].get("location") == rid]
    out["light"] = lights[0] if lights else None
    neighbors = [(cid, o) for cid, o in _connections(ctx, rid, kinds=("doorway", "door")) if not ctx.config["rooms"][o].get("outdoor")]
    if neighbors:
        cid, other = neighbors[0]
        out.update(neighbor=other, door=ctx.config["connections"][cid].get("door"), conn=cid)
        if robot_near:
            cell = C.cell(ctx.config["connections"][cid]["cells"][other])
            cells = sorted((c for c in _free_cells(ctx, other, avoid_ring=False) if c != cell), key=lambda c: (geometry.chebyshev(c, cell), c[1], c[0]))
            if cells:
                _robot_at(ctx, other, cells[0])
    if sleeper:
        out.update(_sleeper(ctx, rid, sleeper, sleeper_role))
    return out


def _agent_cell(ctx, rid):
    """A free cell for a person or pet (kernel Placer.agent_cell: outside doorway clearance, cutting no route); a room
    with none is a setup error, never an agent dropped on cell [0, 0]."""
    cell = ctx.placer.agent_cell(rid)
    if cell is None:
        raise SpecError("navigation_robot: no free floor cell for a person or pet in " + rid)
    return list(cell)


def _sleeper(ctx, rid, kind, role="adult", pid=None, cell=None):
    pos = list(cell) if cell else _agent_cell(ctx, rid)
    if kind == "person":
        pid = pid or ctx.placer.new_id("sleeper")
        ctx.config.setdefault("people", {})[pid] = {"label": "sleeping " + role, "role": role, "age": 7 if role == "child" else 40,
                                                    "authority": 0 if role == "child" else 2, "location": rid, "pos": pos,
                                                    "posture": "sleeping", "awake": False, "consent": True, "permissions": [], "preferences": {}}
    else:
        pid = pid or ("dog_sleeping" if kind == "dog" else "cat_sleeping")
        ctx.config.setdefault("pets", {})[pid] = {"label": "Rex" if kind == "dog" else "Mittens", "species": kind, "location": rid, "pos": pos,
                                                  "posture": "sleeping", "awake": False, "facing_deg": 0.0}
    return {"sleeper": pid}


@scenario_helper("navigation_robot.sleeper")
def sleeper(ctx, room=None, room_type=None, kind="person", role="adult", id=None, cell=None):
    rid = _room(ctx, room, room_type, ("living_room",))
    return dict(_sleeper(ctx, rid, kind, role, pid=id, cell=cell), room=rid)


@scenario_helper("navigation_robot.dock_setup")
def dock_setup(ctx, battery=None, robot_room=None, block=None, unplug=False, move_to=None, reserve=None, idle_drain=None):
    config = ctx.config
    dock = config.get("robot", {}).get("home") or next((e for e in sorted(config["entities"]) if config["entities"][e].get("type") == "robot_dock"), None)
    if not dock:
        # the generator could not fit the robot's dock (a full living room): give the robot one against a free wall
        for kind in ("living_room", "hallway", "office", "dining_room", "entryway", "bedroom", "master_bedroom", "kitchen"):
            for rid in ctx.placer.rooms(kind):
                if any(config["rooms"][rid].get(f) for f in ("shared", "private", "off_limits")):
                    continue
                dock = ctx.placer.furniture(rid, "robot_dock", eid=ctx.placer.new_id("robot_dock"), wall=True)
                if dock:
                    break
            if dock:
                break
    if not dock:
        raise SpecError("dock_setup: no robot dock")
    drec = config["entities"][dock]
    out = {"dock": dock, "dock_room": drec["location"]}
    if move_to:
        old = (drec["location"], list(drec["pos"]))
        saved = config["entities"].pop(dock)
        kinds = [move_to] + ["office", "dining_room", "hallway", "bedroom", "living_room"]
        options = [move_to] if move_to in config["rooms"] else [r for k in kinds for r in ctx.placer.rooms(k) if r != old[0]]
        new = None
        for rid in options:
            new = ctx.placer.furniture(rid, "robot_dock", eid=dock, wall=True)
            if new:
                from .robot import front_cells
                fronts = front_cells(_state(ctx), dock)
                if fronts and all(f in _reachable(ctx, rid) for f in fronts):
                    break
                del config["entities"][dock]
                new = None
        if not new:
            config["entities"][dock] = saved
            raise SpecError("dock_setup: no wall spot for the dock")
        config.setdefault("memory", {})["dock_location"] = {"value": {"room": old[0], "cell": old[1]}, "time_s": -86400, "scope": "robot"}
        drec = config["entities"][dock]
        out.update(dock_room=rid, old_room=old[0], old_cell=old[1])
    if unplug:
        drec["plugged_in"] = False
        drec["plug_required"] = True
    robot = config.setdefault("robot", {})
    robot["home"] = dock
    if battery is not None:
        robot["battery"] = float(battery)
    if reserve is not None:
        robot["battery_reserve_pct"] = float(reserve)
    if idle_drain is not None:
        # kernel v3.0: the body integrates idle drain itself (awake, then parked standby; TIER1_EMBODIMENT 4.4)
        raise SpecError("navigation_robot.dock_setup: idle_drain is gone (the kernel body pays for idle time); set the "
                        "robot's power with {\"op\": \"robot\", \"profile_overrides\": {...}} instead")
    if robot_room:
        rid = _room(ctx, robot_room if robot_room in config["rooms"] else None, None if robot_room in config["rooms"] else robot_room, ("kitchen",))
        cells = _free_cells(ctx, rid)
        if cells:
            _robot_at(ctx, rid, cells[len(cells) // 2])
    if block:
        from .robot import front_cells
        rid = drec["location"]
        state = _state(ctx)
        blocked = set(geometry.blocked_cells(state, rid))
        fronts = [c for c in front_cells(state, dock) if c not in blocked]
        made = []
        for n, c in enumerate(fronts):
            if any(c in geometry.footprint(ctx.config["entities"][e]) for e in made if e in ctx.config["entities"]):
                continue
            if block == "basket":
                made.append(_spawn_at(ctx, rid, c, "laundry_basket", label="laundry basket", rot=0 if fronts[0][1] == fronts[-1][1] else 90))
            elif block == "sleeping_cat":
                ctx.config.setdefault("pets", {})["cat_dock"] = {"label": "Mittens", "species": "cat", "location": rid, "pos": [c[0], c[1]],
                                                                "posture": "sleeping", "awake": False, "facing_deg": 0.0}
                made.append("cat_dock")
                break
            else:
                made.append(_spawn_at(ctx, rid, c, "box", label="delivery box", mass_kg=6.0))
        out["blockers"] = made
        out["blocker"] = made[0] if made else None
        free = _free_cells(ctx, rid)
        far = [c for c in free if min(geometry.chebyshev(c, f) for f in fronts or [c]) >= 3]
        if far:
            # where the (first) blocker can be set down: its whole footprint on free floor, away from the dock
            lead = ctx.config["entities"].get(made[0]) if made and made[0] in ctx.config["entities"] else None
            fits = [c for c in far if lead is None or all(q in set(free) for q in geometry.footprint(dict(lead, pos=[c[0], c[1]])))]
            mid = far[len(far) // 2]
            if fits:
                out["stash_cell"] = list(mid if mid in fits else min(fits, key=lambda c: (abs(far.index(c) - len(far) // 2), c[1], c[0])))
    return out


@scenario_helper("navigation_robot.home_pose")
def home_pose(ctx, room=None, room_type=None, prefer=("living_room", "hallway", "office", "dining_room"), occupied_by=None):
    from .paths import unobtrusive_cell
    state = _state(ctx)
    options = [room] if room in ctx.config["rooms"] else [r for k in ([room_type] if room_type else list(prefer)) for r in ctx.placer.rooms(k)]
    rid, cell = None, None
    for candidate in options:
        view = dict(state, robot={"room": candidate, "pos": None, "hands": {}})
        cell = unobtrusive_cell(view, candidate, near=(0, 0))
        if cell is not None:
            rid = candidate
            break
    if cell is None:
        raise SpecError("home_pose: no unobtrusive cell in {}".format(options))
    robot = ctx.config.setdefault("robot", {})
    robot["home_pose"] = {"room": rid, "cell": [cell[0], cell[1]]}
    out = {"home_room": rid, "home_cell": [cell[0], cell[1]]}
    if occupied_by == "basket":
        out["occupier"] = _spawn_at(ctx, rid, cell, "laundry_basket", label="laundry basket")
    elif occupied_by == "cat":
        ctx.config.setdefault("pets", {})["cat_home"] = {"label": "Mittens", "species": "cat", "location": rid, "pos": [cell[0], cell[1]],
                                                        "posture": "sleeping", "awake": False, "facing_deg": 0.0}
        out["occupier"] = "cat_home"
    return out


@scenario_helper("navigation_robot.measure_target")
def measure_target(ctx, kind="armchair", room=None, room_type=None, prefer=("living_room",), protrusion_mm=None):
    rid = _room(ctx, room, room_type, prefer)
    rng = ctx.rng
    units = C.units_of({"metadata": ctx.config.get("metadata", {}), "household": ctx.config.get("household", {})})
    if kind in ("armchair", "chair", "sofa", "dresser", "desk", "box"):
        body = {"armchair": (0.78, 0.82, 0.92), "chair": (0.46, 0.5, 0.88), "sofa": (1.95, 0.88, 0.84), "dresser": (1.1, 0.48, 0.86),
                "desk": (1.3, 0.65, 0.75), "box": (0.6, 0.4, 0.4)}[kind]
        extra = protrusion_mm if protrusion_mm is not None else rng.choice([28.0, 41.0, 56.0, 73.0])
        ext = [round(body[0] * 1000.0 + rng.uniform(-15, 15) + (extra if kind != "box" else 0.0), 1),
               round(body[1] * 1000.0 + rng.uniform(-10, 10) + (extra * 0.6 if kind in ("armchair", "sofa") else 0.0), 1),
               round(body[2] * 1000.0 + rng.uniform(-10, 10), 1)]
        type_name = {"armchair": "armchair", "chair": "chair", "sofa": "sofa", "dresser": "dresser", "desk": "desk", "box": "box"}[kind]
        size = [round(body[0], 2), round(body[1], 2), round(body[2], 2)]
        rooms = [rid]
        if not room and not room_type:
            # a full living room: any other furnished living space will do
            rooms += [r for k in _room_kinds(list(prefer) + ["living_room", "office", "bedroom", "master_bedroom", "guest_room", "dining_room",
                                                            "playroom", "hallway", "entryway"])
                      for r in ctx.placer.rooms(k) if r != rid and not any(ctx.config["rooms"][r].get(f) for f in ("shared", "private", "off_limits"))]
        eid = None
        for candidate in [r for n, r in enumerate(rooms) if r not in rooms[:n]]:
            eid = ctx.placer.furniture(candidate, type_name, wall=False, label="the {}".format(kind), size_m=size,
                                       _extents_mm=ext, note="legs and arm caps stick out past the upholstered body")
            if not eid:
                eid = ctx.placer.furniture(candidate, type_name, wall=True, label="the {}".format(kind), size_m=size, _extents_mm=ext)
            if eid:
                rid = candidate
                break
        if not eid:
            raise SpecError("measure_target: no room for the " + kind)
        truth = {"width": ext[0], "depth": ext[1], "height": ext[2]}
    elif kind == "space":
        width = round(rng.uniform(700.0, 1100.0), 1)
        narrow = round(width - rng.uniform(12.0, 35.0), 1)
        depth = round(rng.uniform(550.0, 700.0), 1)
        height = round(rng.uniform(2000.0, 2300.0), 1)
        eid = ctx.placer.mount(rid, "generic_item", label="alcove", category="space", portable=False, measure_inside=True, size_m=[width / 1000.0, depth / 1000.0, height / 1000.0],
                               mount_height_m=0.0, inspect_from_afar=True,
                               _measure={"width": {"floor": round(width - 8.0, 1), "mid": width, "top": narrow}, "depth": depth, "height": height})
        truth = {"width": narrow, "depth": depth, "height": height}
    elif kind == "gap":
        # the clear distance between two facing pieces of furniture (sofa to coffee table, bookcase to desk...): the
        # map only says which cells they stand on; the true surface-to-surface gap is hidden (a "to:<id>" span)
        rooms = [rid] + ([r for k in _room_kinds(list(prefer) + ["living_room", "office", "bedroom", "master_bedroom", "guest_room", "dining_room"])
                          for r in ctx.placer.rooms(k) if r != rid and not any(ctx.config["rooms"][r].get(f) for f in ("shared", "private", "off_limits"))]
                         if not room and not room_type else [])
        found = None
        for candidate in [r for n, r in enumerate(rooms) if r not in rooms[:n]]:
            found = _furniture_gap(ctx, candidate)
            if found:
                rid = candidate
                break
        if not found:
            raise SpecError("measure_target: no two facing pieces of furniture for a gap")
        eid, other, cells = found
        gap = round(500.0 * cells + rng.uniform(120.0, 420.0), 1)
        rec = ctx.config["entities"][eid]
        meas = dict(rec.get("_measure") or {})
        meas["to:" + other] = gap
        rec["_measure"] = meas
        truth = {"distance": gap}
    elif kind == "doorway":
        conns = [cid for cid, o in _connections(ctx, rid, kinds=("doorway",))]
        if not conns:
            raise SpecError("measure_target: no doorway in " + rid)
        eid = conns[0]
        conn = ctx.config["connections"][eid]
        clear = round(C.num(conn.get("width_m"), 0.85) * 1000.0 - rng.uniform(25.0, 45.0), 1)
        conn["_clear_width_mm"] = clear
        truth = {"width": clear}
    else:
        raise SpecError("measure_target: unknown kind " + str(kind))
    out = {"target": eid, "room": rid, "units": "in" if units == "imperial" else "mm", "unit_suffix": "in" if units == "imperial" else "mm"}
    if kind == "gap":
        out.update(destination=other, from_label=ctx.config["entities"][eid].get("label"), to_label=ctx.config["entities"][other].get("label"))
    for key, mm in truth.items():
        out["truth_{}_mm".format(key)] = mm
        out["truth_{}_in".format(key)] = round(mm / 25.4, 4)
        out["truth_{}".format(key)] = round(mm / 25.4, 4) if units == "imperial" else mm
    out["tolerance"] = 0.125 if units == "imperial" else 3.0
    return out


GAP_SKIP = ("robot_dock_station", "robot_dock", "plant_saucer", "rug", "vehicle")


def _furniture_gap(ctx, rid, max_cells=3):
    """Two floor-standing pieces (>= 0.3 m tall) facing each other across 0..max_cells free cells along x or y with
    a clear line between them: (a, b, free cells), the narrowest such gap first (ties by id); None if the room has none.
    The robot must be able to stand next to `a` (it measures from there)."""
    state = _state(ctx)
    room = ctx.config["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    pieces = []
    for eid in sorted(ctx.config["entities"]):
        rec = ctx.config["entities"][eid]
        if rec.get("location") != rid or not rec.get("pos") or rec.get("mounted") or not rec.get("blocks_path"):
            continue
        if rec.get("category") in GAP_SKIP or rec.get("rv_dock") or C.num((rec.get("size_m") or [0, 0, 0])[2]) < 0.3:
            continue
        cells = geometry.footprint(rec)
        if cells and all(geometry.in_grid(room, c) for c in cells):
            pieces.append((eid, set(cells)))
    best = None
    for i, (a, ca) in enumerate(pieces):
        if not any(n in reach for n in geometry.near_cells(ca, room, 1) - ca):
            continue
        for b, cb in pieces:
            if b == a:
                continue
            for axis in (0, 1):
                other_axis = 1 - axis
                shared = sorted({c[other_axis] for c in ca} & {c[other_axis] for c in cb})
                if not shared:
                    continue
                hi_a, lo_b = max(c[axis] for c in ca), min(c[axis] for c in cb)
                if hi_a >= lo_b:
                    continue
                g = lo_b - hi_a - 1
                if g > max_cells:
                    continue
                for k in shared:
                    between = [((v, k) if axis == 0 else (k, v)) for v in range(hi_a + 1, lo_b)]
                    if all(c not in blocked for c in between):
                        key = (g, a, b)
                        if best is None or key < best[0]:
                            best = (key, a, b, g)
                        break
    return (best[1], best[2], best[3]) if best else None


@scenario_helper("navigation_robot.high_item")
def high_item(ctx, room=None, room_type=None, prefer=("kitchen", "laundry_room", "utility_closet"), height_m=2.2, mass_kg=0.4, fragile=False,
              aids=("step_stool",), label="cake pan", material="metal", aid_room=None, decoy=None):
    """An item on a wall shelf at height_m plus the reach aids available. decoy="chair" also stands a chair (the tempting
    wrong climbing aid) in the room, clear of the stand/approach cells and the aids (binds `chair`)."""
    def layouts(rid):
        """(spot, stand, approach) triples: a free wall cell for the shelf, a free cell beside it for the aid and a free
        cell next to that to approach from (furnished rooms with every domain loaded are often too full for some)."""
        room = ctx.config["rooms"][rid]
        cols, rows = geometry.grid_size(room)
        free = set(_free_cells(ctx, rid))
        found = []
        for c in [(x, y) for y in range(rows) for x in range(cols) if x in (0, cols - 1) or y in (0, rows - 1)]:
            if c not in free:
                continue
            stands = sorted((n for n in geometry.near_cells([c], room, 1) if n != c and n in free),
                            key=lambda n: (0 if (n[0] == c[0] or n[1] == c[1]) else 1, n[1], n[0]))
            for stand in stands:
                approach = next((n for n in sorted(geometry.near_cells([stand], room, 1)) if n not in (stand, c) and n in free), None)
                if approach is not None:
                    found.append((c, stand, approach))
                    break
        return found

    rid = _room(ctx, room, room_type, prefer)
    options = layouts(rid)
    if not options and not room and not room_type:
        for kind in prefer:
            for other in sorted(ctx.placer.rooms(kind)):
                options = layouts(other) if other != rid else []
                if options:
                    rid = other
                    break
            if options:
                break
    if not options:
        raise SpecError("high_item: no free wall spot with room to stand beside the aid in " + rid)
    room = ctx.config["rooms"][rid]
    spot, stand, approach = ctx.rng.choice(options)
    shelf = ctx.placer.mount(rid, "wall_shelf", label="high shelf", height=float(height_m) - 0.03, cell=list(spot))
    rec = ctx.config["entities"][shelf]
    rec["surface_height_m"] = 0.03
    item = ctx.placer.on(shelf, "generic_item", label=label, mass_kg=float(mass_kg), fragile=bool(fragile), material=material,
                         size_m=[0.25, 0.2, 0.08] if mass_kg < 2 else [0.35, 0.3, 0.2], volume_l=2.0)
    out = {"room": rid, "shelf": shelf, "item": item, "height_m": height_m}
    out["stand_cell"] = [stand[0], stand[1]]
    out["approach_cell"] = [approach[0], approach[1]]
    reserved = {stand, approach}
    where = _room(ctx, aid_room, None, (rid,)) if aid_room else rid
    for aid in aids or ():
        type_name = {"step_stool": "step_stool", "kitchen_stool": "kitchen_step_stool", "ladder": "step_ladder", "reacher": "reacher_grabber"}[aid]
        free = set(_free_cells(ctx, where))
        probe = ctx.placer.reg.make(type_name, where)
        cells = [c for c in sorted(free, key=lambda c: (geometry.chebyshev(c, approach), c[1], c[0]))
                 if all(q in free and q not in reserved for q in geometry.footprint(dict(probe, pos=[c[0], c[1]])))]
        if not cells:
            raise SpecError("high_item: no floor space for the " + aid)
        out[aid] = _spawn_at(ctx, where, cells[0], type_name, eid=ctx.placer.new_id(type_name))
        reserved.update(geometry.footprint(ctx.config["entities"][out[aid]]))
    if decoy:
        # hold the cells the reference plan needs while the decoy is placed (Placer.furniture keeps clear of cells any
        # floor item occupies; the holds do not block, so doorway connectivity checks are unaffected)
        holds = [_spawn_at(ctx, rid, c, "generic_item", eid=ctx.placer.new_id("reserved_cell"), label="reserved", blocks_path=False)
                 for c in sorted(reserved) if c not in geometry.blocked_cells(_state(ctx), rid)]
        try:
            chair = ctx.placer.furniture(rid, "chair", wall=False, label="kitchen chair" if ctx.config["rooms"][rid].get("type") == "kitchen" else "chair")
        finally:
            for h in holds:
                ctx.config["entities"].pop(h, None)
        if not chair:
            raise SpecError("high_item: no floor space for the decoy chair")
        out["chair"] = chair
    return out


def _route_rooms(ctx, rooms):
    if isinstance(rooms, str):
        rooms = [rooms]
    return [r for r in rooms]


@scenario_helper("navigation_robot.watch_route")
def watch_route(ctx, name, rooms, width_m=0.9, from_cell=None, to_cell=None, turn_m=None, clutter=True, rugs=False, doors_open=False,
                robot_counts=False):
    watch = ctx.config.setdefault("household", {}).setdefault("nav_watch", {})
    spec = {"rooms": _route_rooms(ctx, rooms), "width_m": float(width_m), "clutter": bool(clutter), "rugs": bool(rugs), "doors_open": bool(doors_open),
            "robot_counts": bool(robot_counts)}
    if from_cell is not None:
        spec["from_cell"] = list(from_cell)
    if to_cell is not None:
        spec["to_cell"] = list(to_cell)
    if turn_m:
        spec["turn_m"] = float(turn_m)
    watch.setdefault("routes", {})[name] = spec
    return {"route_" + name: name}


@scenario_helper("navigation_robot.watch_area")
def watch_area(ctx, name, room, cells=None, anchor=None, size_cells=None):
    """Keep an area clear (seat pull-out, wheelchair turning circle, stretcher staging): nav.areas.<name>.clear."""
    if cells is None:
        if anchor is None or size_cells is None:
            raise SpecError("watch_area needs cells or anchor + size_cells")
        cells = [[anchor[0] + i, anchor[1] + j] for i in range(int(size_cells[0])) for j in range(int(size_cells[1]))]
    watch = ctx.config.setdefault("household", {}).setdefault("nav_watch", {})
    watch.setdefault("areas", {})[name] = {"room": room, "cells": [list(c) for c in cells]}
    return {"area_" + name: name}


@scenario_helper("navigation_robot.watch_sightlines")
def watch_sightlines(ctx, name, tv, seats):
    """Each seat must see the screen past tall furniture: nav.sightlines.<name>.clear / blocked_seats / distances_m."""
    watch = ctx.config.setdefault("household", {}).setdefault("nav_watch", {})
    watch.setdefault("sightlines", {})[name] = {"tv": tv, "seats": list(seats)}
    return {"sightline_" + name: name}


@scenario_helper("navigation_robot.fit_check")
def fit_check(ctx, object=None, dims_mm=None, space=None, route=None, clearance_mm=0.0, top_clearance_mm=0.0, corner=None, ceiling_m=2.4):
    """Ground truth for 'does it fit (and can it get there)?': binds verdict (fits / does_not_fit / route_blocked) and
    limiting (the binding dimension or doorway). A doorway passes an object whose two smallest dimensions fit the clear
    width and height when tilted on end."""
    from .measure import _extents
    if dims_mm is None:
        dims_mm = _extents(ctx.config["entities"][object])
    dims = sorted(float(v) for v in dims_mm[:3])
    verdict, limiting = "fits", None
    if space:
        rec = ctx.config["entities"][space]
        meas = rec.get("_measure") or {}

        def dim(name):
            v = meas.get(name)
            return min(v.values()) if isinstance(v, dict) else float(v)

        w, d, h = dim("width"), dim("depth"), dim("height")
        ow, od, oh = float(dims_mm[0]), float(dims_mm[1]), float(dims_mm[2])
        need_w, need_d, need_h = ow + 2 * clearance_mm, od + clearance_mm, oh + top_clearance_mm
        if need_w > w:
            verdict, limiting = "does_not_fit", "width"
        elif need_d > d:
            verdict, limiting = "does_not_fit", "depth"
        elif need_h > h:
            verdict, limiting = "does_not_fit", "height"
    if verdict == "fits" and route:
        for i in range(len(route) - 1):
            cid = None
            for c2, conn in sorted(ctx.config.get("connections", {}).items()):
                if set(conn["rooms"]) == {route[i], route[i + 1]}:
                    cid = c2
            if cid is None:
                continue
            conn = ctx.config["connections"][cid]
            width_mm = float(conn.get("_clear_width_mm", float(conn.get("width_m", 0.9)) * 1000.0))
            height_mm = float(conn.get("_height_mm", 2032.0))
            if not (dims[0] <= width_mm and dims[1] <= height_mm):
                verdict, limiting = "route_blocked", cid
                break
    if verdict == "fits" and corner:
        # moving-sofa corner: an object of width a and length L turns flat around a right-angle corner of hall widths
        # w1, w2 when L <= ((w1 - a)^(2/3) + (w2 - a)^(2/3))^(3/2); stood on end it needs L <= ceiling height - 0.1 m.
        w1, w2 = float(corner[0]) * 1000.0, float(corner[1]) * 1000.0
        a, length = dims[0], dims[2]
        flat = ((max(0.0, w1 - a)) ** (2.0 / 3.0) + (max(0.0, w2 - a)) ** (2.0 / 3.0)) ** 1.5
        on_end = length <= float(ceiling_m) * 1000.0 - 100.0 and dims[1] <= min(w1, w2)
        if length > flat and not on_end:
            verdict, limiting = "route_blocked", "corner"
    return {"verdict": verdict, "limiting": limiting or "none", "fits": verdict == "fits"}


@scenario_helper("navigation_robot.watch_doors")
def watch_doors(ctx, doors=None, which="exterior", path_m=0.8):
    watch = ctx.config.setdefault("household", {}).setdefault("nav_watch", {})
    if doors is None:
        doors = [eid for eid in sorted(ctx.config["entities"]) if ctx.config["entities"][eid].get("connects")
                 and ctx.config["entities"][eid].get("category") == "door"
                 and (which == "all" or bool(ctx.config["entities"][eid].get("exterior")) == (which == "exterior"))]
    watch["doors"] = sorted(set(watch.get("doors", [])) | set(doors))
    watch["door_path_m"] = float(path_m)
    return {"watched_doors": doors, "watched_door": doors[0] if doors else None}


CLUTTER = {
    "shoes": ("generic_item", {"label": "pair of shoes", "category": "shoe", "floor_hazard": "trip", "size_m": [0.32, 0.25, 0.12], "mass_kg": 0.9}),
    "toy_bin": ("generic_item", {"label": "toy bin", "category": "toy_bin", "size_m": [0.45, 0.35, 0.3], "mass_kg": 2.5}),
    "box": ("box", {"label": "delivery box", "mass_kg": 4.0, "size_m": [0.45, 0.35, 0.3]}),
    "backpack": ("bag", {"label": "school backpack", "mass_kg": 3.0, "size_m": [0.35, 0.25, 0.45]}),
    "basket": ("basket", {"label": "basket of folded towels", "mass_kg": 2.0, "size_m": [0.45, 0.35, 0.3]}),
    "lego": ("generic_item", {"label": "LEGO castle (in progress)", "category": "toy", "fragile": True, "crushable": True, "in_progress": True,
                              "size_m": [0.45, 0.45, 0.25], "mass_kg": 1.2, "sentimental": True}),
}


def _cross_section(room, blocked, path, i):
    c = path[i]
    a, b = path[max(0, i - 3)], path[min(len(path) - 1, i + 3)]
    horizontal = abs(b[0] - a[0]) > abs(b[1] - a[1])
    cols, rows = geometry.grid_size(room)
    axis = [(c[0], y) for y in range(rows)] if horizontal else [(x, c[1]) for x in range(cols)]
    k = axis.index(c)
    lo = k
    while lo - 1 >= 0 and axis[lo - 1] not in blocked:
        lo -= 1
    hi = k
    while hi + 1 < len(axis) and axis[hi + 1] not in blocked:
        hi += 1
    return axis[lo:hi + 1]


def _clutter_in_room(ctx, rid, width_m, sections, kinds, min_len=8, tidy=False, pinch=False, max_items=8):
    """Clutter on a doorway-to-doorway route of at least min_len cells that is width_m wide without it. With tidy, a
    route kept narrow only by loose things other domains left on the floor is first tidied (_tidy_route); with pinch,
    sections may sit at the route's pinch points when evenly spaced ones do not close it."""
    from .paths import wide_path
    conns = [(cid, o) for cid, o in _connections(ctx, rid, kinds=("doorway", "door", "gate")) if _usable_exit(ctx, cid, o)]
    state = _state(ctx)
    pairs = []
    for i in range(len(conns)):
        for j in range(i + 1, len(conns)):
            a = C.cell(ctx.config["connections"][conns[i][0]]["cells"][rid])
            b = C.cell(ctx.config["connections"][conns[j][0]]["cells"][rid])
            path = geometry.find_path(state, rid, a, [b])
            if not path or len(path) < min_len:
                continue
            wide = wide_path(state, rid, a, b, width_m)[0] is not None
            if wide or tidy:
                pairs.append((len(path), conns[i], conns[j], a, b, path, wide))
    for _, (ca, ra), (cb, rb), a, b, path, wide in sorted(pairs, key=lambda t: (-t[0], t[1][0], t[2][0])):
        moves = []
        if not wide:
            moves = _tidy_route(ctx, rid, a, b, width_m)
            if moves is None:
                continue
        made = _clutter_pair(ctx, rid, a, b, path, width_m, sections, kinds, max_items=max_items)
        if not made and pinch:
            # open rooms: put the sections at the route's natural pinch points (between furniture) instead
            for idx in _pinch_sections(ctx, rid, path, sections):
                made = _clutter_pair(ctx, rid, a, b, path, width_m, sections, kinds, idx=idx, max_items=max_items)
                if made:
                    break
        if made:
            return {"room": rid, "from_room": ra, "to_room": rb, "from_conn": ca, "to_conn": cb, "from_cell": [a[0], a[1]],
                    "to_cell": [b[0], b[1]], "clutter": made, "path_cells": [list(c) for c in path], "tidied": [m[0] for m in moves],
                    "moves": moves}
        _undo_moves(ctx, moves)
    return None


def _pinch_sections(ctx, rid, path, sections, tries=4):
    """Alternative section positions along the route: the narrowest cross-sections (pinch points), spaced >= 3 cells
    apart and away from the end doorways. Up to `tries` index lists, narrowest first."""
    room = ctx.config["rooms"][rid]
    blocked = set(geometry.blocked_cells(_state(ctx), rid))
    widths = sorted((len(_cross_section(room, blocked, path, i)), abs(i - len(path) // 2), i) for i in range(2, len(path) - 2))
    out = []
    for first in widths[:tries]:
        chosen = [first[2]]
        for _, _, i in widths:
            if len(chosen) >= sections:
                break
            if all(abs(i - j) >= 3 for j in chosen):
                chosen.append(i)
        if len(chosen) == sections and sorted(chosen) not in out:
            out.append(sorted(chosen))
    return out


def _clutter_pair(ctx, rid, a, b, path, width_m, sections, kinds, idx=None, max_items=8):
    """Fill `sections` cross-sections of the route with clutter (evenly spaced, or at `idx`); the ids when the result is
    verified (blocked for width_m, passable for the robot, still blocked with any one item removed, clear with all
    removed, at most max_items things to carry away), else None. The robot-width gap is left at the far end of each
    cross-section first; when that leaves no robot route (things on that side wall it off), the other ends are tried."""
    if idx is None:
        idx = [int(len(path) * (n + 1) / (sections + 1)) for n in range(sections)]
    combos = [tuple(((k >> j) & 1) for j in range(len(idx))) for k in range(2 ** min(len(idx), 3))]
    for gaps in combos:
        made = _clutter_sections(ctx, rid, a, b, path, width_m, kinds, idx, gaps, max_items)
        if made:
            return made
    return None


def _clutter_sections(ctx, rid, a, b, path, width_m, kinds, idx, gaps, max_items):
    from . import hazards as H
    from .paths import wide_path
    room = ctx.config["rooms"][rid]
    door_cells = {C.cell(conn["cells"][rid]) for conn in ctx.config["connections"].values() if rid in conn.get("cells", {})}
    blocked = set(geometry.blocked_cells(_state(ctx), rid))
    made, ok = [], True
    n = 0
    for i, gap in zip(idx, gaps):
        run = _cross_section(room, blocked, path, i)
        if len(run) < 2:
            ok = False
            break
        side = run[1:] if gap else run[:-1]
        fill = [c for c in side if c not in door_cells and not geometry.near_cells([c], room, 0) & door_cells]
        if not fill:
            ok = False
            break
        for c in fill:
            st = _state(ctx)
            if any(c in geometry.footprint(st["entities"][e]) for e in H.floor_entities(st, rid)
                   if not st["entities"][e].get("ground_layer") and C.num(st["entities"][e].get("mount_height_m")) < 0.5):
                continue
            type_name, props = CLUTTER[kinds[n % len(kinds)]]
            made.append(_spawn_at(ctx, rid, c, type_name, **props))
            n += 1
    st = _state(ctx)
    if ok and made and len(made) <= max_items:
        blocked_now = wide_path(st, rid, a, b, width_m)[0] is None
        robot_ok = wide_path(st, rid, a, b, 0.5)[0] is not None
        singles = all(wide_path(st, rid, a, b, width_m, ignore=(e,))[0] is None for e in made)
        cleared = wide_path(st, rid, a, b, width_m, ignore=tuple(made))[0] is not None
        if blocked_now and robot_ok and singles and cleared:
            return made
    for e in made:
        ctx.config["entities"].pop(e, None)
    return None


def _loose(rec):
    """A loose thing lying on the floor that a tidy-up may set aside (not furniture, rugs, robots or docks)."""
    return bool(rec.get("portable") and not rec.get("blocks_path") and not rec.get("ground_layer") and not rec.get("rv")
                and not rec.get("rv_dock") and C.num(rec.get("mass_kg")) <= 15.0 and rec.get("pos"))


def _undo_moves(ctx, moves):
    for eid, old in reversed(moves or []):
        if eid in ctx.config["entities"]:
            ctx.config["entities"][eid].update(copy.deepcopy(old))


def _tidy_route(ctx, rid, a, b, width_m):
    """Set aside the loose floor items (other domains' baskets, toys, spare filters...) that keep the route a -> b from
    being width_m wide: each goes to a free reachable floor cell off the route (this room first, then a neighbouring
    room), keeping every doorway of the room reachable at robot width. Returns the moves (to undo), or None."""
    from . import hazards as H
    from .paths import wide_path
    st = _state(ctx)
    loose = [e for e in H.floor_entities(st, rid) if _loose(st["entities"][e])]
    if not loose:
        return None
    anchors, k = wide_path(st, rid, a, b, width_m, ignore=tuple(loose))
    if anchors is None:
        return None
    band = {(p[0] + i, p[1] + j) for p in anchors for i in range(k) for j in range(k)}
    movers = [e for e in loose if set(geometry.footprint(st["entities"][e])) & band]
    neighbors = [o for _, o in _connections(ctx, rid, kinds=("doorway", "door"))
                 if not any(ctx.config["rooms"][o].get(f) for f in ("outdoor", "private", "shared", "off_limits"))]
    doors = [C.cell(conn["cells"][rid]) for _, conn in sorted(ctx.config["connections"].items()) if rid in conn.get("cells", {})]
    # doorways another domain's things already cut off (a toy beside a dock in a corner) need not be reachable after the
    # tidy-up either; the tidy-up itself must not cut off any doorway that was reachable
    doors = [d for d in doors if d == a or wide_path(st, rid, a, d, 0.5)[0] is not None]
    moves = []
    for eid in movers:
        rec = ctx.config["entities"][eid]
        spot = None
        for room_id in [rid] + neighbors:
            room = ctx.config["rooms"][room_id]
            free = set(_free_cells(ctx, room_id))
            for c in sorted(free, key=lambda q: (q[1], q[0])):
                cells = geometry.footprint(dict(rec, pos=[c[0], c[1]]))
                if all(q in free and geometry.in_grid(room, q) and (room_id != rid or q not in band) for q in cells):
                    spot = (room_id, c)
                    break
            if spot:
                break
        if spot is None:
            _undo_moves(ctx, moves)
            return None
        moves.append((eid, {"location": rec["location"], "pos": list(rec["pos"]), "relation": rec.get("relation", "on")}))
        rec.update(location=spot[0], pos=[spot[1][0], spot[1][1]], relation="on")
    st = _state(ctx)
    if wide_path(st, rid, a, b, width_m)[0] is None or any(wide_path(st, rid, a, d, 0.5)[0] is None for d in doors if d != a):
        _undo_moves(ctx, moves)
        return None
    return moves


def _runner_on_route(ctx, out, width_m):
    """A loose, unpadded runner on the cluttered route whose removal (with the clutter) is needed for the route to be
    width_m wide. Returns its id, or None when no floor patch on the route qualifies."""
    from . import hazards as H
    from .paths import wide_path
    rid = out["room"]
    st = _state(ctx)
    taken = set()
    for e in H.floor_entities(st, rid):
        taken.update(geometry.footprint(st["entities"][e]))
    door_cells = {C.cell(conn["cells"][rid]) for conn in ctx.config["connections"].values() if rid in conn.get("cells", {})}
    room = ctx.config["rooms"][rid]
    path = [C.cell(c) for c in out["path_cells"]]
    a, b = C.cell(out["from_cell"]), C.cell(out["to_cell"])
    order = path[len(path) // 2:] + path[:len(path) // 2]
    # a small mat first; a longer runner (either way round) where the walkway is wider than the mat; on the route's
    # cells first, then beside them (a route hugging one wall of a corridor with doorways along it)
    for size, mass in (([0.6, 0.9, 0.01], 1.5), ([0.9, 1.5, 0.01], 2.5), ([1.5, 0.9, 0.01], 2.5)):
        cols, rows = geometry.footprint_size({"size_m": size})
        offsets = [(dx, dy) for dy in range(0, -rows, -1) for dx in range(0, -cols, -1)]
        beside = [(dx, dy) for dy in range(1, -rows - 1, -1) for dx in range(1, -cols - 1, -1) if (dx, dy) not in offsets]
        anchors, seen = [], set()
        for group in (offsets, beside):
            for c in order:
                for dx, dy in group:
                    anchor = (c[0] + dx, c[1] + dy)
                    if anchor not in seen:
                        seen.add(anchor)
                        anchors.append(anchor)
        for anchor in anchors:
            cells = [(anchor[0] + i, anchor[1] + j) for i in range(cols) for j in range(rows)]
            if not all(geometry.in_grid(room, q) and q not in taken and q not in door_cells for q in cells):
                continue
            eid = _spawn_at(ctx, rid, anchor, "rug", label="loose hallway rug", non_slip_pad=False, size_m=list(size),
                            mass_kg=mass, volume_l=5.0, two_handed=False)
            st = _state(ctx)
            blocks = wide_path(st, rid, a, b, width_m, rugs=True, ignore=tuple(out["clutter"]))[0] is None
            if blocks and wide_path(st, rid, a, b, width_m, rugs=True, ignore=tuple(out["clutter"]) + (eid,))[0] is not None:
                return eid
            del ctx.config["entities"][eid]
    return None


@scenario_helper("navigation_robot.clutter_route")
def clutter_route(ctx, room=None, room_type=None, prefer=("hallway", "living_room", "entryway"), width_m=0.9, sections=2,
                  items=("shoes", "toy_bin", "box", "backpack", "basket"), name="path", staging=True, in_progress=False, rug=False,
                  max_items=8):
    """Clutter that leaves only a robot-width gap at two points of a route between two doorways (verified on the grid:
    blocked for width_m, passable for the robot, still blocked after removing any single item, clear without clutter)."""
    options = [room] if room in ctx.config["rooms"] else [r for k in ([room_type] if room_type else list(prefer)) for r in ctx.placer.rooms(k)]
    if not room and not room_type:
        # any other indoor room with two walk-through doorways will do when the preferred ones are too small or full
        extra = [r for k in _room_kinds(list(prefer)) + ["dining_room", "kitchen", "office", "playroom", "mudroom", "bedroom",
                                                          "master_bedroom", "guest_room", "craft_room", "gym"]
                 for r in ctx.placer.rooms(k) if r not in options]
        options += [r for n, r in enumerate(extra) if r not in extra[:n]]
    kinds = list(items) + (["lego"] if in_progress else [])
    out = None
    # long routes first (8+ cells); furnished small houses may only have shorter walk-throughs (6+ cells) that are
    # still 0.9 m wide end to end
    # furnished homes: other domains' baskets and toys may already narrow every route; as a last resort those are set
    # aside first (tidy) so that only this scene's clutter blocks the route
    # compact scenes (at most max_items things to carry) first; open rooms may need up to 14
    # open-plan rooms whose every route crosses the whole room: a single pinch (one cross-section) as the last resort
    passes = [(sec, cap, n, tidy, pinch) for sec in ([int(sections)] + ([1] if int(sections) > 1 else []))
              for cap in (int(max_items), max(int(max_items), 14))
              for n, tidy, pinch in ((8, False, False), (6, False, False), (8, False, True), (6, False, True), (8, True, True), (6, True, True))]
    for sec, cap, min_len, tidy, pinch in passes:
        for rid in options:
            out = _clutter_in_room(ctx, rid, width_m, sec, kinds, min_len, tidy, pinch, cap)
            if out and rug:
                runner = _runner_on_route(ctx, out, width_m)
                if runner is None:
                    for e in out["clutter"]:
                        ctx.config["entities"].pop(e, None)
                    _undo_moves(ctx, out.get("moves"))
                    out = None
                    continue
                out["rug"] = runner
            if out:
                break
        if out:
            break
    if not out:
        raise SpecError("clutter_route: no route to clutter in {}".format(options))
    rid = out["room"]
    out.pop("moves", None)
    out["route_name"] = name
    for n, eid in enumerate(out["clutter"], 1):
        out["clutter_{}".format(n)] = eid
    if staging:
        path_cells = {C.cell(c) for c in out["path_cells"]}
        for side, cid in ((out["from_room"], out["from_conn"]), (out["to_room"], out["to_conn"]), (rid, out["from_conn"])):
            near = C.cell(ctx.config["connections"][cid]["cells"][side])
            cells = sorted((c for c in _free_cells(ctx, side) if geometry.chebyshev(c, near) >= 2 and (side != rid or c not in path_cells)),
                           key=lambda c: (geometry.chebyshev(c, near), c[1], c[0]))
            if side == rid:
                # last resort, in the route room itself: only where the bin keeps the cleared route wide
                from .paths import wide_path
                st = _state(ctx)
                a0, b0 = C.cell(out["from_cell"]), C.cell(out["to_cell"])
                cells = [c for c in cells if wide_path(st, rid, a0, b0, width_m, ignore=tuple(out["clutter"]) + ((out["rug"],) if out.get("rug") else ()),
                                                       extra=[c])[0] is not None]
            if cells:
                # big enough for everything that has to be cleared (clutter and runner), at least a 250 L bin
                load = sum(C.num(ctx.config["entities"][e].get("volume_l")) for e in out["clutter"] + ([out["rug"]] if out.get("rug") else []))
                out["staging"] = _spawn_at(ctx, side, cells[0], "storage_bin", label="staging bin", open=True,
                                           capacity_l=max(250.0, round(load * 1.25, 1)), inner_size_m=[0.9, 0.7, 0.6], size_m=[0.5, 0.5, 0.5])
                out["staging_room"] = side
                break
        if "staging" not in out:
            raise SpecError("clutter_route: no floor spot for the staging bin")
    a, b = C.cell(out["from_cell"]), C.cell(out["to_cell"])
    watch_route(ctx, name, [rid], width_m=width_m, from_cell=a, to_cell=b, rugs=bool(rug))
    exits = []
    for n, (cid, other) in enumerate(_connections(ctx, rid, kinds=("doorway", "door", "gate", "stairs")), 1):
        cell = C.cell(ctx.config["connections"][cid]["cells"][rid])
        if cell in (a, b):
            continue
        watch_route(ctx, "exit_{}".format(n), [rid], width_m=0.5, from_cell=a, to_cell=cell)
        exits.append("exit_{}".format(n))
    out["exits"] = exits
    return out


@scenario_helper("navigation_robot.robot_vacuum")
def robot_vacuum(ctx, room=None, room_type=None, prefer=("living_room",), model="robot_vacuum", state="docked", bin_fill=0.1, brush_hair=0.0,
                 mop_tank_l=0.0, battery=1.0, floor_items=(), schedule=None, auto_empty=False, stuck_reason=None, rugs=()):
    rid = _room(ctx, room, room_type, prefer)
    existing = [e for e in sorted(ctx.config["entities"]) if ctx.config["entities"][e].get("rv") and C.room_of(_state(ctx), e) == rid]
    for e in existing:
        dock = (ctx.config["entities"][e].get("rv") or {}).get("dock")
        ctx.config["entities"].pop(e, None)
        if dock:
            ctx.config["entities"].pop(dock, None)
    dock_type = "auto_empty_dock" if auto_empty else ("robot_mower_dock" if model == "robot_mower" else "robot_vacuum_dock")
    dock = ctx.placer.furniture(rid, dock_type, wall=True)
    if not dock and not room and not room_type:
        # a furnished room may have no free wall left: any other living space the robot would run in
        kinds = ["backyard", "garden", "front_yard"] if model == "robot_mower" else ["living_room", "hallway", "dining_room", "office", "bedroom",
                                                                                  "master_bedroom", "kitchen", "entryway"]
        for other in [r for k in _room_kinds(list(prefer) + kinds) for r in ctx.placer.rooms(k) if r != rid]:
            if any(ctx.config["rooms"][other].get(f) for f in ("shared", "private", "off_limits")):
                continue
            dock = ctx.placer.furniture(other, dock_type, wall=True)
            if dock:
                rid = other
                break
    if not dock:
        raise SpecError("robot_vacuum: no wall spot for the dock")
    drec = ctx.config["entities"][dock]
    rv_id = ctx.placer.new_id(model)
    rec = ctx.placer.reg.make(model, rid)
    rec["rv"]["dock"] = dock
    rec["battery_level"] = float(battery)
    rec["rv"].update(bin_fill=float(bin_fill), brush_hair=float(brush_hair), side_brush_hair=float(brush_hair) * 0.6, mop_tank_l=float(mop_tank_l))
    if schedule:
        rec["rv"]["schedule"] = copy.deepcopy(schedule)
    if state == "docked":
        rec["pos"] = list(drec["pos"])
        rec["docked"] = True
        rec["rv"].update(state="docked", docked=True, charging=True)
    else:
        free = sorted(_free_cells(ctx, rid), key=lambda c: (-geometry.chebyshev(c, C.cell(drec["pos"])), c[1], c[0]))
        rec["pos"] = list(free[len(free) // 3] if free else drec["pos"])
        rec["docked"] = False
        rec["rv"].update(state=state, docked=False, charging=False, stuck_reason=stuck_reason, error="stuck" if state == "stuck" else None,
                         run_room=rid)
    ctx.config["entities"][rv_id] = rec
    out = {"rv": rv_id, "dock": dock, "room": rid}
    made = []
    for kind in floor_items or ():
        props = {"cord": dict(label="phone charging cable", category="cord", floor_hazard="cord", size_m=[0.5, 0.02, 0.01], mass_kg=0.05),
                 "sock": dict(label="sock", category="sock", material="cotton", size_m=[0.25, 0.1, 0.02], mass_kg=0.04, floor_hazard="trip"),
                 "lego": dict(label="LEGO brick", category="toy", size_m=[0.03, 0.016, 0.01], mass_kg=0.003),
                 "earring": dict(label="gold earring", category="jewelry", size_m=[0.015, 0.01, 0.01], mass_kg=0.002, value=250.0)}[kind]
        eid = ctx.placer.floor_item(rid, "generic_item", **props)
        if eid:
            made.append(eid)
            out[kind] = eid
    for kind in rugs or ():
        eid = ctx.placer.floor_item(rid, "rug", label="fringed rug" if kind == "fringe" else "wool rug", fringe=kind == "fringe", material="wool",
                                    water_safe=False, size_m=[1.0, 1.0, 0.01])
        if eid:
            out["rug"] = eid
    out["floor_items"] = made
    return out


@scenario_helper("navigation_robot.person_route")
def person_route(ctx, person=None, legs=None, start_room=None, near_robot=True):
    people = ctx.config.get("people", {})
    pid = person or next(iter(sorted(p for p in people if people[p].get("role", "adult") == "adult")), None)
    if pid is None:
        raise SpecError("person_route: no adult resident")
    p = people[pid]
    robot = ctx.config.setdefault("robot", {})
    if start_room:
        rid = _room(ctx, start_room if start_room in ctx.config["rooms"] else None, None if start_room in ctx.config["rooms"] else start_room, ("living_room",))
        p["location"] = rid
        p["pos"] = _agent_cell(ctx, rid)
    if near_robot:
        rid = p["location"]
        cells = _free_cells(ctx, rid)
        if cells:
            pos = C.cell(p["pos"])
            cells.sort(key=lambda c: (abs(geometry.chebyshev(c, pos) - 3), c[1], c[0]))
            _robot_at(ctx, rid, cells[0])
    resolved = []
    for leg in legs or []:
        leg = dict(leg)
        room = leg.get("room")
        if room not in ctx.config["rooms"]:
            options = ctx.placer.rooms(room)
            public = [r for r in options if any(ctx.config["rooms"][o].get("type") == "hallway" for _, o in _connections(ctx, r, kinds=("doorway", "door")))]
            if not (public or options):
                raise SpecError("person_route: no room of type " + str(room))
            leg["room"] = sorted(public or options)[0]
        resolved.append(leg)
    p["_route"] = resolved
    p["awake"] = True
    p["posture"] = "standing"
    out = {"person": pid, "start_room": p["location"]}
    for n, leg in enumerate(resolved, 1):
        out["leg_{}".format(n)] = leg["room"]
    out["final_room"] = resolved[-1]["room"] if resolved else p["location"]
    from .follow import _route
    hops = _route(ctx.placer.state(), p["location"], out["final_room"]) or []
    if hops:
        last_conn = ctx.config["connections"][hops[-1][1]]
        out["final_door"] = last_conn.get("door")
        out["before_final"] = hops[-2][0] if len(hops) > 1 else p["location"]
    return out


@scenario_helper("navigation_robot.pet_follower")
def pet_follower(ctx, species="dog", room=None, allowed_outside=False):
    pets = ctx.config.setdefault("pets", {})
    robot = ctx.config.get("robot", {})
    rid = room or robot.get("room") or ctx.config["start_room"]
    pid = "dog_follow" if species == "dog" else "cat_follow"
    # a pet that tags along behind the robot (this domain's follows_robot: it slips through a passage the robot leaves
    # open for 5 s or more), not a door-dasher: the pets/mechanisms door-dash models (escape_risk, behaviors.door_dash)
    # and the pets domain's immediate follow-on-move are switched off so the escape depends on how long the gate stays open
    pets[pid] = {"label": "Biscuit" if species == "dog" else "Pixel", "species": species, "location": rid,
                 "pos": _agent_cell(ctx, rid), "posture": "standing", "awake": True, "follows_robot": True,
                 "allowed_outside": bool(allowed_outside), "escape_risk": 0.0,
                 "behaviors": {"door_dash": 0.0, "follows_robot": 0.0, "roams": 0.0}}
    return {"pet": pid}


@scenario_helper("navigation_robot.elevator")
def elevator(ctx, car_floor=None, robot_at="corridor"):
    cars = [r for r in sorted(ctx.config["rooms"]) if ctx.config["rooms"][r].get("type") == "elevator"]
    if not cars:
        raise SpecError("navigation_robot.elevator: no elevator (use house.dwelling apartment)")
    car = cars[0]
    landings = {}
    for cid, other in _connections(ctx, car, kinds=("elevator_door",)):
        landings[other] = int(ctx.config["rooms"][other].get("floor", 0))
    if len(landings) < 2:
        raise SpecError("elevator: fewer than two landings")
    lobby = min(landings, key=lambda r: landings[r])
    upper = max(landings, key=lambda r: landings[r])
    floor = landings[lobby] if car_floor in (None, "lobby") else (landings[upper] if car_floor == "upper" else int(car_floor))
    ctx.config["rooms"][car]["elevator"] = {"landings": landings, "car_floor": floor, "doors": "closed", "doors_close_at_s": 0.0,
                                            "moving_to": None, "arrive_at_s": 0.0, "travel_s_per_floor": 2.5, "door_open_s": 8.0, "held_s": 0.0}
    ctx.config["rooms"][car]["floor"] = floor
    out = {"car": car, "lobby": lobby, "upper": upper, "lobby_floor": landings[lobby], "upper_floor": landings[upper]}
    if robot_at == "corridor":
        conn = next(cid for cid, other in _connections(ctx, car, kinds=("elevator_door",)) if other == upper)
        cell = C.cell(ctx.config["connections"][conn]["cells"][upper])
        room = ctx.config["rooms"][upper]
        near = sorted(c for c in geometry.near_cells([cell], room, 1) if c != cell)
        _robot_at(ctx, upper, near[0] if near else cell)
    inside = [c for c in sorted(geometry.near_cells([(1, 1)], ctx.config["rooms"][car], 0))]
    out["inside_cell"] = list(inside[0]) if inside else [1, 1]
    return out


def _is_car(rec):
    """A car (not a bicycle or scooter, which share the vehicle category)."""
    return rec.get("type") == "car" or "car" in (rec.get("is_a") or []) or (
        rec.get("category") == "vehicle" and C.num(rec.get("mass_kg")) >= 500.0)


@scenario_helper("navigation_robot.vehicle")
def vehicle(ctx, room=None, room_type="driveway", reverse_at_s=None, duration_s=40.0, reverse_dir="south"):
    """A parked car that will reverse: the household's own car (tools_vehicles furnishes one in the garage or on the
    driveway) when one stands in the requested room or the other car room, else a new car where one fits."""
    rid = _room(ctx, room, room_type, ("driveway", "garage"))
    rooms = [rid] + ([r for k in ("driveway", "garage") for r in ctx.placer.rooms(k) if r != rid] if not room else [])
    car = None
    for candidate in rooms:
        own = [e for e in sorted(ctx.config["entities"]) if ctx.config["entities"][e].get("location") == candidate
               and ctx.config["entities"][e].get("pos") and _is_car(ctx.config["entities"][e])]
        if own:
            car, rid = own[0], candidate
            rec = ctx.config["entities"][car]
            rec["vehicle"] = dict(rec.get("vehicle") or {}, state="parked", reverse_dir=reverse_dir)
            break
    if car is None:
        for candidate in rooms:
            car = ctx.placer.furniture(candidate, "furniture", wall=False, label="car", category="vehicle", size_m=[1.8, 4.5, 1.5], mass_kg=1500.0,
                                       material="steel", vehicle={"state": "parked", "reverse_dir": reverse_dir}, rot=0)
            if car:
                rid = candidate
                break
    if not car:
        raise SpecError("vehicle: no room for a car in {}".format(rooms))
    out = {"car": car, "room": rid}
    if reverse_at_s is not None:
        ctx.config.setdefault("events", []).append({"at_s": float(reverse_at_s), "effect": "navigation_robot.vehicle",
                                                    "args": {"vehicle": car, "state": "reversing", "duration_s": float(duration_s), "reverse_dir": reverse_dir}})
    return out


@scenario_helper("navigation_robot.no_go")
def no_go(ctx, room=None, room_type=None, start=None, end=None, from_s=None, until_s=None, reason="nap", violation="privacy_violations", always=False):
    rid = _room(ctx, room, room_type, ("office", "bedroom"))
    window = {"reason": reason, "violation": violation}
    if always:
        window["always"] = True
    if start and end:
        window.update({"from": start, "to": end})
    if from_s is not None:
        window["from_s"] = float(from_s)
    if until_s is not None:
        window["until_s"] = float(until_s)
    ctx.config["rooms"][rid].setdefault("nav_no_go", []).append(window)
    return {"no_go_room": rid}


@scenario_helper("navigation_robot.leveling")
def leveling(ctx, room=None, room_type=None, prefer=("laundry_room", "kitchen", "garage"), label="washing machine", appliance=True):
    rid = _room(ctx, room, room_type, prefer)
    rng = ctx.rng
    floor = {f: round(rng.uniform(-6.0, 6.0), 1) for f in M.FEET}
    feet = {"foot_fl_mm": 0.0, "foot_fr_mm": 0.0, "foot_bl_mm": 0.0, "foot_br_mm": 0.0, "lock_nuts": "loose"}
    feet_options = {"foot_fl_mm": {"min": -10.0, "max": 25.0}, "foot_fr_mm": {"min": -10.0, "max": 25.0},
                    "foot_bl_mm": {"min": -10.0, "max": 25.0}, "foot_br_mm": {"min": -10.0, "max": 25.0},
                    "lock_nuts": ["loose", "locked"]}
    eid = None
    if label in ("washing machine", "washer") and not room and not room_type:
        # the house's own washer (textiles_laundry furnishes one in most laundry rooms) gets uneven feet
        washers = sorted(e for e, r in ctx.config["entities"].items()
                         if (r.get("appliance_kind") == "washer" or r.get("type") == "washing_machine" or "washing_machine" in (r.get("is_a") or []))
                         and r.get("location") in ctx.config["rooms"] and r.get("pos")
                         and not any(ctx.config["rooms"][r["location"]].get(k) for k in ("shared", "private", "off_limits")))
        if washers:
            eid = washers[0]
            rid = ctx.config["entities"][eid]["location"]
            rec = ctx.config["entities"][eid]
            size = rec.get("size_m") or [0.6, 0.6, 0.85]
            rec["leveling"] = {"floor_mm": floor, "span_mm": [round(max(0.3, float(size[0]) - 0.04) * 1000.0, 0)] * 2}
            rec["settings"] = dict(rec.get("settings") or {}, **feet)
            rec["setting_options"] = dict(rec.get("setting_options") or {}, **feet_options)
            control = dict(rec.get("control_kinds") or {})
            for key in feet:
                control.pop(key, None)
            rec["control_kinds"] = control
    if eid is None:
        props = dict(label=label, category="appliance", size_m=[0.6, 0.6, 0.85], mass_kg=70.0, blocks_path=True,
                     leveling={"floor_mm": floor, "span_mm": [560.0, 560.0]}, settings=dict(feet), setting_options=dict(feet_options))
        if appliance:
            props["appliance"] = {"programs": {"spin_test": {"duration_s": 300.0}}, "program": "spin_test", "state": "idle", "remaining_s": 0.0,
                                  "requires_power": False}
        eid = ctx.placer.furniture(rid, "furniture", wall=True, **props)
        if not eid and not room and not room_type:
            for kind in _room_kinds(list(prefer) + ["basement", "utility_closet", "garage", "kitchen", "bathroom", "pantry", "mudroom",
                                                    "hallway", "dining_room", "office", "living_room"]):
                for other in sorted(ctx.placer.rooms(kind)):
                    flags = ctx.config["rooms"][other]
                    if other == rid or any(flags.get(k) for k in ("shared", "private", "off_limits", "outdoor")):
                        continue
                    eid = ctx.placer.furniture(other, "furniture", wall=True, **props)
                    if eid:
                        rid = other
                        break
                if eid:
                    break
    if not eid:
        raise SpecError("leveling: no room for the " + label)
    rec = ctx.config["entities"][eid]
    tilt, wobble = M.compute_level(rec)
    rec["_tilt_deg"], rec["_wobble_mm"] = tilt, wobble
    top = max(floor.values())
    fix = {f: round(top - floor[f], 1) for f in M.FEET}
    out = {"target": eid, "room": rid}
    for f in M.FEET:
        out["fix_{}".format(f)] = fix[f]
    return out
