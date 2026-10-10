"""Scenario helpers (setup op {"op": "helper", "name": "mechanisms.<name>", "args": {...}}).

Each returns bindings (entity ids, rooms). All randomness comes from the setup rng. Hidden truth
goes into _mech / _code / _misaligned. See docs/domains/mechanisms.md (section 6) for arguments.
"""
import copy

from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from . import build as B
from . import common as C


# Room types that are the same kind of room for a helper (an apartment's only bedroom is its master bedroom).
ROOM_SYNONYMS = {
    "bedroom": ("bedroom", "master_bedroom", "guest_room", "nursery"),
    "bathroom": ("bathroom", "half_bath"),
    "living_room": ("living_room", "family_room"),
}


def _room(ctx, value, required=True, fallbacks=()):
    """A room id: `value` itself, else a random room of that type (or a synonym type, see ROOM_SYNONYMS), else of
    the first fallback type the house has. SpecError only when the house has none of them."""
    if value in ctx.config["rooms"]:
        return value
    for kind in ROOM_SYNONYMS.get(value, (value,)) + tuple(fallbacks):
        rooms = ctx.placer.rooms(kind)
        if rooms:
            return ctx.rng.choice(rooms)
    if required:
        raise SpecError("mechanisms helper: no room of type {} (add it to house.require)".format(value))
    return None


def _rooms_of(ctx, value):
    """All rooms matching a room id or type (with synonyms), sorted."""
    if value in ctx.config["rooms"]:
        return [value]
    out = []
    for kind in ROOM_SYNONYMS.get(value, (value,)):
        out += ctx.placer.rooms(kind)
    return out


def _merge_mech(rec, mech=None, hidden=None):
    if mech:
        base = copy.deepcopy(rec.get("mech") or {})
        base.update(copy.deepcopy(mech))
        if "kind" not in base and C.kind_of(rec):
            base["kind"] = C.kind_of(rec)
        rec["mech"] = base
    if hidden:
        h = copy.deepcopy(rec.get("_mech") or {})
        h.update(copy.deepcopy(hidden))
        rec["_mech"] = h


def _spawn_items(ctx, items, parent=None, room=None, relation=None):
    out = []
    for spec in items or []:
        spec = dict(spec)
        type_name = spec.pop("type", "generic_item")
        props = dict(spec.pop("props", {}))
        rel = spec.pop("relation", relation)
        eid = spec.pop("eid", None)
        if parent is not None:
            if rel in ("on",):
                new = ctx.placer.on(parent, type_name, eid=eid, spos=spec.get("spos"), **props)
            elif rel in ("behind", "under", "attached"):
                new = ctx.placer.make(type_name, parent, eid=eid, **props)
                ctx.config["entities"][new]["relation"] = rel
            else:
                new = ctx.placer.inside(parent, type_name, eid=eid, shelf=spec.get("shelf"), **props)
        else:
            new = ctx.placer.floor_item(room, type_name, eid=eid, near=spec.get("near"), **props)
        if new is None:
            raise SpecError("mechanisms helper: no room for {}".format(type_name))
        out.append(new)
    return out


def _cell_near(ctx, room, cell, avoid=()):
    """A free cell near `cell` (within two cells) for an agent or a dropped item: not furniture, another agent or a
    loose floor item, preferably one the robot can walk to from the room's doorways."""
    state = ctx.placer.state()
    blocked = set(geometry.blocked_cells(state, room))
    occupied = set()
    for group in ("people", "pets"):
        for aid, agent in ctx.config.get(group, {}).items():
            if agent.get("location") == room and agent.get("pos"):
                occupied.add(tuple(agent["pos"]))
    for eid, rec in ctx.config["entities"].items():
        if rec.get("location") == room and not rec.get("ground_layer") and "connects" not in rec and not rec.get("mounted"):
            occupied.update(geometry.footprint(rec))
    reach = reachable_cells(ctx, room)
    options = sorted(geometry.near_cells([tuple(cell)], ctx.config["rooms"][room], radius=2) - blocked - occupied - set(map(tuple, avoid)),
                     key=lambda c: (0 if c in reach else 1, geometry.chebyshev(c, tuple(cell)), c[1], c[0]))
    return list(options[0]) if options else list(cell)


def reachable_cells(ctx, rid, include_agents=False):
    """Free floor cells reachable from the room's doorways (kernel ``geometry.reachable_cells``; with include_agents
    the people and pets standing in the room count as obstacles too)."""
    state = ctx.placer.state()
    return geometry.reachable_cells(state, rid, blocked=set(geometry.blocked_cells(state, rid, include_agents=include_agents)))


def reachable_entity(ctx, eid):
    rec = ctx.config["entities"][eid]
    rid = rec.get("location")
    if rid not in ctx.config["rooms"]:
        return True
    cells = geometry.footprint(rec) or ([tuple(rec["pos"])] if rec.get("pos") else [])
    near = geometry.near_cells(cells, ctx.config["rooms"][rid]) - set(cells)
    return bool(near & reachable_cells(ctx, rid))


def window_reach_rank(ctx, wid, include_agents=True):
    """0: a free cell next to the window is reachable from the doorways (people/pets count as obstacles);
    1: only from one cell further out (allowed for windows above counter height); 2: not reachable."""
    rec = ctx.config["entities"][wid]
    rid = rec.get("location")
    if rid not in ctx.config["rooms"] or not rec.get("pos"):
        return 2
    room = ctx.config["rooms"][rid]
    cells = [c for c in geometry.footprint(rec) if geometry.in_grid(room, c)] or [tuple(rec["pos"])]
    reach = reachable_cells(ctx, rid, include_agents=include_agents)
    if (geometry.near_cells(cells, room) - set(cells)) & reach:
        return 0
    if C.num(rec.get("mount_height_m"), 0.9) >= 0.85 and (geometry.near_cells(cells, room, radius=2) - set(cells)) & reach:
        return 1
    return 2


def _far_cell(ctx, room, cell):
    """A free cell in room as far as possible from cell (inside a closet, the back corner), preferring cells the robot
    can walk to from the doorways."""
    state = ctx.placer.state()
    cols, rows = geometry.grid_size(ctx.config["rooms"][room])
    blocked = set(geometry.blocked_cells(state, room))
    reach = reachable_cells(ctx, room)
    options = [(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked and (x, y) != tuple(cell)]
    if not options:
        return list(cell)
    options.sort(key=lambda c: (0 if c in reach else 1, -geometry.chebyshev(c, tuple(cell)), c[1], c[0]))
    return list(options[0])


# ----------------------------------------------------------------------------------
# doors

@scenario_helper("mechanisms.door")
def door_helper(ctx, door=None, kind="interior", room=None, mech=None, hidden=None, props=None, person_behind=None,
                pet_near=None, pet_side="inside", swing="far", var=None):
    """Configure a door's hardware/state. kind: front|back|exterior|garage_entry|closet|bedroom|bathroom|shed|interior.
    swing: far (opens away from the robot's start side = 'inside') | near. person_behind: person id placed in the swing on the
    outside; pet_near: pet id placed by the doorway on pet_side."""
    c = ctx.config
    if door is None:
        options = B.find_door(c, kind, room=room)
        if not options:
            raise SpecError("mechanisms.door: no {} door".format(kind))
        door = ctx.rng.choice(options)
    rec = c["entities"][door]
    a, b = rec["connects"]
    rooms = c["rooms"]
    if rooms[a].get("outdoor") and not rooms[b].get("outdoor"):
        inside, outside = b, a
    elif rooms[b].get("outdoor") and not rooms[a].get("outdoor"):
        inside, outside = a, b
    elif rooms[b].get("type") in ("coat_closet", "linen_closet", "utility_closet", "pantry", "shed", "garage", "bedroom", "master_bedroom",
                                   "bathroom", "office", "laundry_room", "half_bath", "nursery", "guest_room"):
        inside, outside = a, b
    else:
        inside, outside = a, b
    _merge_mech(rec, mech, hidden)
    if C.kind_of(rec) in C.NON_SWING_KINDS and rec.get("handle_kind") not in geometry.NON_SWING_HANDLES:
        # a door re-fitted as a slider/pocket door: the kernel's door model (clearance, routing) must not see a leaf
        rec["handle_kind"] = "pocket" if "pocket" in C.kind_of(rec) else "sliding"
    if swing in ("far", "near"):
        rec.setdefault("mech", {})
        rec["mech"].setdefault("kind", C.kind_of(rec))
        rec["mech"]["swing_into"] = outside if swing == "far" else inside
    for key, value in (props or {}).items():
        rec[key] = copy.deepcopy(value)
    if rec.get("open") is False and "latched" not in rec and C.kind_of(rec) in ("hinged_door", "gate", "double_door"):
        rec["latched"] = True
    out = {"door": door, "inside": inside, "outside": outside}
    cid, conn = C.conn_of({"entities": c["entities"], "connections": c.get("connections", {})}, door)
    if conn:
        out["inside_cell"] = list(conn["cells"][inside])
        out["outside_cell"] = list(conn["cells"][outside])
        out["inside_near_cell"] = _cell_near(ctx, inside, conn["cells"][inside], avoid=[conn["cells"][inside]])
        out["far_cell"] = _far_cell(ctx, outside, conn["cells"][outside])
    if person_behind:
        pid = person_behind
        people = c.setdefault("people", {})
        if pid not in people:
            people[pid] = {"label": pid.replace("_", " "), "role": "child", "age": 6, "authority": 0, "consent": True, "awake": True,
                           "permissions": [], "preferences": {}}
        people[pid]["location"] = outside
        people[pid]["pos"] = list(conn["cells"][outside]) if conn else [0, 0]
        out["person_behind"] = pid
    if pet_near:
        pets = c.setdefault("pets", {})
        side = inside if pet_side == "inside" else outside
        if pet_near not in pets:
            pets[pet_near] = {"label": "Biscuit", "species": "dog", "size": "medium"}
        pets[pet_near]["location"] = side
        pets[pet_near].pop("pos", None)
        cell = conn["cells"][side] if conn else [0, 0]
        spot = ctx.placer.agent_cell(side, near=tuple(cell))      # by the door, outside its swing, cutting no route
        if spot is None:
            raise SpecError("mechanisms.door: no free cell for the pet by the {} door".format(side))
        pets[pet_near]["pos"] = list(spot)
        out["pet"] = pet_near
    return out


@scenario_helper("mechanisms.lights_on")
def lights_on_helper(ctx, rooms="indoor"):
    """Residents' evening lighting: switch on ceiling lights (and their switches) in indoor rooms."""
    c = ctx.config
    state = ctx.placer.state()
    from ...kernel.predicates import root_room
    wanted = set(rooms) if isinstance(rooms, list) else {r for r, v in c["rooms"].items() if not v.get("outdoor")}
    for eid in sorted(c["entities"]):
        rec = c["entities"][eid]
        if rec.get("category") == "light" and not rec.get("portable") and root_room(state, eid) in wanted:
            rec["on"] = True
    for eid in sorted(c["entities"]):
        rec = c["entities"][eid]
        if rec.get("category") == "switch" and any(c["entities"].get(x, {}).get("on") for x in rec.get("controls") or []):
            rec["on"] = True
    return {"lit_rooms": sorted(wanted)}


@scenario_helper("mechanisms.door_wedge")
def wedge_helper(ctx, room, near=None):
    room = _room(ctx, room)
    wid = ctx.placer.floor_item(room, "door_wedge", near=near)
    if wid is None:
        raise SpecError("no floor space for a door wedge")
    return {"wedge": wid}


# ----------------------------------------------------------------------------------
# windows and coverings

@scenario_helper("mechanisms.window")
def window_helper(ctx, room="living_room", window=None, mech=None, hidden=None, props=None, sill=False, sill_items=None,
                  in_path=None, ac_unit=False, pick="random", exclude=None):
    c = ctx.config
    if window is None:
        def windows_in(rid_):
            return [e for e in sorted(c["entities"]) if c["entities"][e].get("category") == "window"
                    and c["entities"][e].get("location") == rid_ and e not in (exclude or [])]
        rid = _room(ctx, room)
        options = windows_in(rid)
        if not options or min(window_reach_rank(ctx, e, include_agents=False) for e in options) >= 2:
            # this room's windows are missing or boxed in by furniture: another room of the same type
            others = [r for r in _rooms_of(ctx, room) if r != rid and windows_in(r)
                      and min(window_reach_rank(ctx, e, include_agents=False) for e in windows_in(r)) < 2]
            if others:
                rid = others[0]
                options = windows_in(rid)
        if not options:
            raise SpecError("mechanisms.window: no window in {}".format(rid))
        # prefer windows the robot (and a resident) can actually step up to: other furnishers put sofas,
        # beds and toy boxes under windows
        best = min(window_reach_rank(ctx, e) for e in options)
        if best >= 2 and min(window_reach_rank(ctx, e, include_agents=False) for e in options) >= 2:
            raise SpecError("mechanisms.window: every window of {} is boxed in by furniture in this layout".format(rid))
        options = [e for e in options if window_reach_rank(ctx, e) == best]
        window = options[0] if pick == "first" else ctx.rng.choice(options)
    rec = c["entities"][window]
    for key, value in (props or {}).items():
        rec[key] = copy.deepcopy(value)
    _merge_mech(rec, mech or {"kind": C.kind_of(rec)}, hidden)
    rec.setdefault("mech", {}).setdefault("kind", C.kind_of(rec))
    m = rec["mech"]
    if "open_frac" in m:
        rec["open"] = C.num(m["open_frac"]) > 0.005 or C.num(m.get("upper_open_frac")) > 0.005
        if rec["open"]:
            rec["locked"] = False
    elif rec.get("open"):
        m["open_frac"] = 1.0
    if C.num(m.get("upper_open_frac")) > 0.005:
        rec["open"] = True
        rec["locked"] = False
    out = {"window": window, "room": rec["location"]}
    if sill or sill_items:
        sid = B.add_sill(c, ctx.placer, window)
        out["sill"] = sid
        out["sill_items"] = _spawn_items(ctx, sill_items, parent=sid, relation="on")
        for i, item in enumerate(out["sill_items"], 1):
            out["sill_item_{}".format(i)] = item
    if in_path:
        made = []
        for spec in in_path:
            spec = dict(spec)
            props_ = dict(spec.get("props", {}))
            props_["in_path_of"] = window
            new = ctx.placer.make(spec.get("type", "generic_item"), rec["location"], **props_)
            c["entities"][new]["pos"] = list(rec.get("pos") or [0, 0])
            c["entities"][new]["mounted"] = True
            made.append(new)
        out["in_path"] = made
    if ac_unit:
        unit = ctx.placer.make("window_ac_unit", rec["location"])
        c["entities"][unit]["pos"] = list(rec.get("pos") or [0, 0])
        c["entities"][unit]["mounted"] = True
        m["ac_unit"] = unit
        rec["open"] = True
        m["open_frac"] = max(C.num(m.get("open_frac")), 0.3)
        rec["locked"] = False
        out["ac_unit"] = unit
    return out


@scenario_helper("mechanisms.covering")
def covering_helper(ctx, window, kind="curtain", type=None, mech=None, props=None, replace=True):
    """Hang a curtain/blind on a window (replace=true removes the furnished one first)."""
    c = ctx.config
    if replace:
        from . import coverings as V
        for old in V.coverings_of(c, window):
            del c["entities"][old]
        c["entities"][window]["covered"] = 0.0
    eid = B.add_covering(c, ctx.placer, window, kind, type_name=type, mech=mech or {}, **(props or {}))
    return {"covering": eid}


@scenario_helper("mechanisms.room_windows")
def room_windows_helper(ctx, room, open_frac=None, locked=None, covering=None, covering_type=None, covering_mech=None):
    """All windows of a room (optionally opened / with a covering each). Returns windows list."""
    c = ctx.config
    rid = _room(ctx, room)
    wins = [e for e in sorted(c["entities"]) if c["entities"][e].get("category") == "window" and c["entities"][e].get("location") == rid]
    covers = []
    for wid in wins:
        rec = c["entities"][wid]
        if open_frac is not None:
            rec.setdefault("mech", {"kind": C.kind_of(rec)})
            rec["mech"]["open_frac"] = float(open_frac)
            rec["open"] = float(open_frac) > 0
            if rec["open"]:
                rec["locked"] = False
        if locked is not None and not rec.get("open"):
            rec["locked"] = bool(locked)
        if covering:
            covers.append(B.add_covering(c, ctx.placer, wid, covering, type_name=covering_type, mech=covering_mech or {}))
    out = {"windows": wins, "room": rid, "coverings": covers}
    for i, wid in enumerate(wins, 1):
        out["window_{}".format(i)] = wid
    return out


# ----------------------------------------------------------------------------------
# drawers, cabinets, containers

@scenario_helper("mechanisms.drawer")
def drawer_helper(ctx, room="kitchen", host="kitchen", type="drawer", mech=None, hidden=None, props=None, contents=None,
                  protruding=None, behind=None, dresser_anchored=True, drawer_loads_kg=None, dresser_props=None):
    """host=kitchen: a mounted kitchen drawer; host=dresser: a bedroom dresser with three drawers (returns all)."""
    c = ctx.config
    p = ctx.placer
    out = {}
    if host == "dresser":
        rid = _room(ctx, room if room != "kitchen" else "bedroom")
        dressers = p.find("dresser", room=rid)
        dresser = dressers[0] if dressers else p.furniture(rid, "dresser")
        if dresser is None:
            raise SpecError("no space for a dresser")
        drec = c["entities"][dresser]
        drec["anchored"] = bool(dresser_anchored)
        for key, value in (dresser_props or {}).items():
            drec[key] = copy.deepcopy(value)
        drawers = B.dresser_drawers(c, p, dresser)
        for d in drawers:
            _merge_mech(c["entities"][d], dict(mech or {}, kind="drawer"), None)
            c["entities"][d]["part_of"] = dresser
        for d, kg in zip(drawers, drawer_loads_kg or []):
            if kg:
                item = p.inside(d, "generic_item", label="folded clothes", category="garment_stack", mass_kg=float(kg), size_m=[0.35, 0.3, 0.1], volume_l=6.0)
        out.update(dresser=dresser, drawers=drawers, drawer=drawers[0], room=rid)
        for i, d in enumerate(drawers, 1):
            out["drawer_{}".format(i)] = d
        target = drawers[0]
    else:
        rid = _room(ctx, room)
        height = C.num((props or {}).get("mount_height_m"), 0.75)
        # Placer.mount: a reachable wall cell, not over furniture or loose items, outside the door clearance when low
        target = p.mount(rid, type, height=height, label=(props or {}).get("label", "kitchen drawer"))
        out.update(drawer=target, room=rid)
    rec = c["entities"][target]
    for key, value in (props or {}).items():
        rec[key] = copy.deepcopy(value)
    _merge_mech(rec, dict(mech or {}, kind="drawer"), hidden)
    items = _spawn_items(ctx, contents, parent=target)
    out["items"] = items
    for i, item in enumerate(items, 1):
        out["item_{}".format(i)] = item
    if protruding:
        pid = _spawn_items(ctx, [dict(protruding, props=dict(protruding.get("props", {}), protrudes=True))], parent=target)[0]
        out["protruding"] = pid
    if behind:
        bid = _spawn_items(ctx, [dict(behind, relation="behind")], parent=target)[0]
        out["behind"] = bid
    return out


@scenario_helper("mechanisms.cabinet")
def cabinet_helper(ctx, room="kitchen", type="cabinet", cabinet=None, mech=None, hidden=None, props=None, child_lock=None,
                   contents=None, leaning=None, protruding=None, wall=True):
    c = ctx.config
    p = ctx.placer
    rid = _room(ctx, room)
    if cabinet is None:
        if type in ("upper_cabinet", "lift_up_cabinet", "glass_front_cabinet"):
            cabinet = p.mount(rid, type, height=1.45)
        else:
            cabinet = p.furniture(rid, type, wall=wall)
            if cabinet is None:
                existing = p.find("cabinet", room=rid)
                if not existing:
                    raise SpecError("no space for a cabinet")
                cabinet = existing[0]
    rec = c["entities"][cabinet]
    for key, value in (props or {}).items():
        rec[key] = copy.deepcopy(value)
    _merge_mech(rec, mech, hidden)
    if child_lock:
        rec.setdefault("mech", {}).setdefault("kind", C.kind_of(rec))
        rec["mech"]["child_lock_kind"] = child_lock
        rec["child_lock"] = True
    out = {"cabinet": cabinet, "room": rid}
    items = _spawn_items(ctx, contents, parent=cabinet)
    out["items"] = items
    for i, item in enumerate(items, 1):
        out["item_{}".format(i)] = item
    if leaning:
        lid = _spawn_items(ctx, [dict(leaning, props=dict(leaning.get("props", {}), leans_on_door=True))], parent=cabinet)[0]
        out["leaning"] = lid
    if protruding:
        pid = _spawn_items(ctx, [dict(protruding, props=dict(protruding.get("props", {}), protrudes=True))], parent=cabinet)[0]
        out["protruding"] = pid
    return out


@scenario_helper("mechanisms.container")
def container_helper(ctx, type="snap_lid_tote", on=None, room=None, label=None, mech=None, hidden=None, props=None,
                     contents=None, on_lid=None, open=None):
    c = ctx.config
    p = ctx.placer
    props = dict(props or {})
    if label:
        props["label"] = label
    if on:
        eid = p.on(on, type, **props)
    else:
        eid = p.floor_item(_room(ctx, room or "living_room"), type, **props)
    if eid is None:
        raise SpecError("mechanisms.container: no space")
    rec = c["entities"][eid]
    _merge_mech(rec, mech, hidden)
    rec.setdefault("mech", {}).setdefault("kind", C.kind_of(rec))
    if open is not None:
        rec["open"] = bool(open)
        rec["mech"]["open_frac"] = 1.0 if open else 0.0
    was_open = rec.get("open")
    rec["open"] = True
    items = _spawn_items(ctx, contents, parent=eid)
    rec["open"] = was_open
    out = {"container": eid, "items": items}
    for i, item in enumerate(items, 1):
        out["item_{}".format(i)] = item
    if on_lid:
        rec["accepts_on"] = True
        lids = _spawn_items(ctx, on_lid, parent=eid, relation="on")
        out["on_lid"] = lids
        for i, item in enumerate(lids, 1):
            out["on_lid_{}".format(i)] = item
    return out


# ----------------------------------------------------------------------------------
# gates, sheds, garage, pet doors

@scenario_helper("mechanisms.gate")
def gate_helper(ctx, gate=None, which="gate", mech=None, hidden=None, props=None, padlock=None, code=None, hasp=False):
    c = ctx.config
    if gate is None:
        options = B.find_door(c, which)
        if not options:
            raise SpecError("mechanisms.gate: no {}".format(which))
        gate = ctx.rng.choice(options)
    rec = c["entities"][gate]
    for key, value in (props or {}).items():
        rec[key] = copy.deepcopy(value)
    _merge_mech(rec, dict(mech or {}, kind="gate"), hidden)
    a, b = rec["connects"]
    fenced_a = c["rooms"][a].get("type") in C.FENCED_ROOM_TYPES
    inside, outside = (a, b) if fenced_a else (b, a)
    out = {"gate": gate, "inside": inside, "outside": outside}
    if hasp or padlock:
        rec["mech"]["hasp"] = {"closed": bool(padlock), "padlock": None}
    if padlock:
        side = rec["location"]
        lock = ctx.placer.make("combination_padlock" if code else "padlock", side, label="gate padlock")
        lrec = c["entities"][lock]
        lrec["relation"] = "attached"
        lrec["hanging_on"] = gate
        lrec["mounted"] = True
        cid_, conn_ = C.conn_of({"entities": c["entities"], "connections": c.get("connections", {})}, gate)
        lrec["pos"] = list(conn_["cells"][side]) if conn_ else list(rec.get("pos") or [0, 0])
        if code:
            lrec["_code"] = str(code)
        rec["mech"]["hasp"] = {"closed": True, "padlock": lock, "through_staple": True}
        rec["locked"] = True
        rec["open"] = False
        rec["latched"] = True
        out["padlock"] = lock
    cid, conn = C.conn_of({"entities": c["entities"], "connections": c.get("connections", {})}, gate)
    if conn:
        out["inside_cell"] = list(conn["cells"][inside])
        out["outside_cell"] = list(conn["cells"][outside])
    return out


@scenario_helper("mechanisms.shed")
def shed_helper(ctx, double=False, padlock="keyed", code=None, locked=True, occupant=None, occupant_kind="pet", threshold_item=None,
                key_home="entry_hook"):
    """Shed door with hasp + padlock (keyed: its key hangs on the entryway key hook; combination: code)."""
    c = ctx.config
    p = ctx.placer
    doors = B.find_door(c, "shed")
    if not doors:
        raise SpecError("mechanisms.shed: house has no shed (house.require shed)")
    door = doors[0]
    rec = c["entities"][door]
    shed = next(r for r in rec["connects"] if c["rooms"][r]["type"] == "shed")
    yard = next(r for r in rec["connects"] if r != shed)
    rec["lockable"] = False
    rec["locked"] = False
    rec.pop("key", None)
    rec["lock_kind"] = None
    mech = {"kind": "double_door" if double else "hinged_door", "swing_into": yard, "hasp": {"closed": False, "padlock": None}}
    if double:
        C.set_leaf_state(mech, {"fixed": {"open": False, "bolted": True}, "active": {"open": False, "latched": True}})
        rec["size_m"] = [1.5, 0.05, 1.9]
    _merge_mech(rec, mech)
    rec["open"] = False
    rec["latched"] = True
    out = {"shed_door": door, "shed": shed, "yard": yard}
    lock = p.make("combination_padlock" if padlock == "combination" else "padlock", yard, label="shed padlock")
    lrec = c["entities"][lock]
    if padlock == "combination":
        lrec["_code"] = str(code or "{:04d}".format(ctx.rng.randrange(10000)))
        out["code"] = lrec["_code"]
    else:
        hook = _key_hook(ctx)
        key = p.hang(hook, "key", label="shed padlock key", home=hook)
        if key is None:
            raise SpecError("key hook full")
        lrec["key"] = key
        out.update(key=key, key_hook=hook)
    cid0, conn0 = C.conn_of({"entities": c["entities"], "connections": c.get("connections", {})}, door)
    if locked:
        lrec["relation"] = "attached"
        lrec["hanging_on"] = door
        lrec["mounted"] = True
        lrec["pos"] = list(conn0["cells"][yard]) if conn0 else list(rec.get("pos") or [0, 0])
        rec["mech"]["hasp"] = {"closed": True, "padlock": lock, "through_staple": True}
        rec["locked"] = True
    else:
        lrec["relation"] = "on"
        lrec["shackle_closed"] = False
        # left open on the ground beside the door: outside the door clearance (threshold, its neighbours and the leaf's
        # swing, kernel v2.3), where it would stop the door closing, and cutting no route
        door_cell = list(conn0["cells"][yard]) if conn0 else list(rec.get("pos") or [0, 0])
        lrec["location"] = yard
        lrec.pop("pos", None)
        state = p.state()
        spot = geometry.free_floor_cell(state, yard, near=tuple(door_cell), ignore=(lock,), record=lrec, strict=True, keep_access=True)
        if spot is None:
            raise SpecError("mechanisms.shed: no free ground beside the shed door for the open padlock")
        lrec["pos"] = list(spot)
    out["padlock"] = lock
    cid, conn = C.conn_of({"entities": c["entities"], "connections": c.get("connections", {})}, door)
    if conn:
        spot = p.agent_cell(yard, near=tuple(conn["cells"][yard]))
        out["yard_cell"] = list(spot) if spot is not None else _cell_near(ctx, yard, conn["cells"][yard], avoid=[conn["cells"][yard], lrec.get("pos") or [0, 0]])
    if occupant:
        group = "pets" if occupant_kind == "pet" else "people"
        agents = c.setdefault(group, {})
        if occupant not in agents:
            # a cat that wandered into the shed from the yard is an indoor-outdoor cat (the pets domain treats
            # cats as indoor-only by default, which would make the yard an escape zone for it)
            agents[occupant] = ({"label": "Miso", "species": "cat", "size": "small", "escape_risk": 0.0, "indoor_only": False,
                                 "allowed_outside": True} if group == "pets" else
                                {"label": occupant.replace("_", " "), "role": "adult", "authority": 2, "consent": True, "awake": True,
                                 "permissions": [], "preferences": {}})
        agents[occupant]["location"] = shed
        agents[occupant].pop("pos", None)
        spot = p.agent_cell(shed, near=tuple(conn["cells"][shed]) if conn else None)
        if spot is None:
            raise SpecError("mechanisms.shed: no free cell inside the shed for the occupant")
        agents[occupant]["pos"] = list(spot)
        out["occupant"] = occupant
    if threshold_item and conn:
        item = p.make(threshold_item.get("type", "generic_item"), yard, **threshold_item.get("props", {}))
        c["entities"][item]["pos"] = list(conn["cells"][yard])
        out["threshold_item"] = item
    return out


@scenario_helper("mechanisms.garage")
def garage_helper(ctx, state="open", obstruction=None, sensor_misaligned=False, close_limit_short_m=0.0, remote=False):
    """Garage door with wall button and photo eyes. obstruction: bin|bike|car|pet|child|hose|none."""
    c = ctx.config
    p = ctx.placer
    doors = B.find_door(c, "garage")
    if not doors:
        raise SpecError("mechanisms.garage: no garage (house.require garage)")
    door = doors[0]
    rec = c["entities"][door]
    parts = B.garage_parts(c, p, door)
    if not parts:
        raise SpecError("garage parts could not be placed")
    pos = 1.0 if state == "open" else 0.0
    _merge_mech(rec, {"kind": "overhead_door", "state": state if state in ("open", "closed") else "open", "pos": pos, "open_frac": pos,
                      "reported": state}, {"close_limit_short_m": float(close_limit_short_m)} if close_limit_short_m else None)
    rec["open"] = pos > 0.02
    garage = next(r for r in rec["connects"] if c["rooms"][r]["type"] == "garage")
    drive = next(r for r in rec["connects"] if r != garage)
    out = {"garage_door": door, "button": parts["button"], "eyes": parts.get("eyes", []), "garage": garage, "driveway": drive}
    if sensor_misaligned and parts.get("eyes"):
        c["entities"][parts["eyes"][0]]["_misaligned"] = True
        c["entities"][parts["eyes"][0]]["display"] = "blinking"
        out["bad_eye"] = parts["eyes"][0]
    from . import garage as G
    view = dict(p.state(), entities=c["entities"])
    paths = G.path_cells(view, door)
    cells = paths.get(garage) or []
    _clear_door_path(ctx, door, paths)
    if obstruction and obstruction != "none" and cells:
        mid = list(cells[len(cells) // 2])
        if obstruction in ("bin", "bike", "hose", "box"):
            spec = {"bin": ("trash bin", [0.6, 0.7, 1.05], 12.0), "bike": ("bicycle", [0.6, 1.7, 1.0], 13.0),
                    "hose": ("garden hose", [0.4, 0.4, 0.05], 2.0), "box": ("cardboard box", [0.4, 0.3, 0.3], 2.0)}[obstruction]
            item = p.make("generic_item", garage, label=spec[0], size_m=spec[1], mass_kg=spec[2], category="garage_item")
            pos = _overlap_pos(ctx, garage, item, cells, mid)
            if pos is None:
                raise SpecError("mechanisms.garage: no room for a {} under the door".format(spec[0]))
            c["entities"][item]["pos"] = pos
            out["obstruction"] = item
        elif obstruction == "car":
            # the household's car (furnished by tools_vehicles) is parked too far out; a stand-in when there is none
            cars = [e for e in p.find(room=garage) if c["entities"][e].get("location") == garage and c["entities"][e].get("blocks_path")
                    and (c["entities"][e].get("category") == "vehicle" or "car" in C.tags(c["entities"][e]))]
            car = cars[0] if cars else p.make("generic_item", garage, label="car", size_m=[1.8, 4.6, 1.45], mass_kg=1450.0, portable=False,
                                              category="vehicle", blocks_path=True)
            # parked nose-in but not far enough: its tail sticks out under the door (long axis across the door line)
            along_row = len({cc[1] for cc in cells}) == 1
            old_rot = c["entities"][car].get("rot", 0)
            c["entities"][car]["rot"] = 0 if along_row else 90
            keep = [parts["button"]] + [e for e in sorted(c["entities"]) if c["entities"][e].get("location") == garage
                                        and c["entities"][e].get("blocks_path") and e != car]
            pos = _overlap_pos(ctx, garage, car, cells, mid, keep_reachable=keep)
            if pos is None:
                c["entities"][car]["rot"] = old_rot
                raise SpecError("mechanisms.garage: no room in this garage for a car sticking out under the door (furniture beside the opening)")
            c["entities"][car]["pos"] = pos
            out["obstruction"] = car
        elif obstruction in ("pet", "child"):
            if obstruction == "pet":
                pets = c.setdefault("pets", {})
                pets.setdefault("dog_1", {"label": "Biscuit", "species": "dog", "size": "medium"})
                pets["dog_1"].update(location=garage, pos=mid)
                out["obstruction"] = "dog_1"
            else:
                people = c.setdefault("people", {})
                people.setdefault("child_9", {"label": "Robin (child)", "role": "child", "age": 6, "authority": 0, "consent": True, "awake": True,
                                              "permissions": [], "preferences": {}})
                people["child_9"].update(location=garage, pos=mid)
                out["obstruction"] = "child_9"
    if remote:
        rid = p.hang(_key_hook(ctx), "garage_door_remote", controls_door=door)
        if rid is None:
            raise SpecError("mechanisms.garage: the key hook is full")
        out["remote"] = rid
    return out


ENTRY_ROOMS = ("entryway", "mudroom", "hallway", "living_room", "kitchen")


def _key_hook(ctx):
    """The household key hook (furnished by the entry), else a new one mounted by the main entrance."""
    p = ctx.placer
    hooks = p.find("key_hook")
    if hooks:
        return hooks[0]
    rid = _room(ctx, "entryway", fallbacks=ENTRY_ROOMS[1:])
    return p.mount(rid, "key_hook", height=1.4)


def _overlap_pos(ctx, room, eid, cells, mid, keep_reachable=()):
    """A position for entity eid whose footprint fits the room, covers part of `cells` (as close to `mid` as
    possible) and avoids blocking furniture; for a blocking entity it must also keep the doorways and the
    `keep_reachable` entities reachable from the room's doorways."""
    c = ctx.config
    rec = c["entities"][eid]
    grid = c["rooms"][room]
    cols, rows = geometry.grid_size(grid)
    want = set(map(tuple, cells))
    state = ctx.placer.state()
    blocked = set(geometry.blocked_cells(state, room, ignore=(eid,)))
    fw, fh = geometry.footprint_size(rec)
    original = rec.get("pos")
    options = []
    for y in range(-fh + 1, rows):
        for x in range(-fw + 1, cols):
            foot = geometry.footprint(dict(rec, pos=[x, y]))
            if not all(geometry.in_grid(grid, f) for f in foot) or not (set(foot) & want) or set(foot) & blocked:
                continue
            options.append((min(geometry.chebyshev(f, tuple(mid)) for f in foot), y, x))
    options.sort()
    for _, y, x in options:
        if not rec.get("blocks_path"):
            return [x, y]
        rec["pos"] = [x, y]
        reach = reachable_cells(ctx, room)
        ok = True
        for other in keep_reachable:
            orec = c["entities"].get(other) or {}
            foot = [f for f in geometry.footprint(orec) if geometry.in_grid(grid, f)] or ([tuple(orec["pos"])] if orec.get("pos") else [])
            radius = 2 if geometry.mounted_overhead(state, other) else 1
            if foot and not ((geometry.near_cells(foot, grid, radius) - set(foot)) & reach):
                ok = False
                break
        doors = [tuple(conn["cells"][room]) for conn in c.get("connections", {}).values() if room in conn.get("cells", {})]
        if ok and all(d in reach or d in want for d in doors):
            return [x, y]
    if original is None:
        rec.pop("pos", None)
    else:
        rec["pos"] = original
    return None


def _clear_door_path(ctx, door, paths):
    """Generated houses keep the garage door's threshold clear (kernel v2.3 door clearance); earlier setup ops may
    still have dropped a low loose item (< 0.15 m, not covered by the strict clearance rule) under it. Re-place such
    items with the kernel's placement rule (outside the door clearance, cutting no route)."""
    c = ctx.config
    from . import garage as G
    for kind, oid, h in G.obstructions(ctx.placer.state(), door):
        rec = c["entities"].get(oid)
        room = (rec or {}).get("location")
        if kind != "object" or room not in paths:
            continue
        state = ctx.placer.state()
        near = tuple(rec.get("pos") or (0, 0))
        cell = None
        for access in (True, "plain"):
            cell = geometry.free_floor_cell(state, room, near=near, ignore=(oid,), record=rec, strict=True, keep_access=access)
            if cell is not None:
                break
        if cell is None:
            raise SpecError("mechanisms.garage: nowhere to move the {} lying under the garage door".format(rec.get("label", oid)))
        rec["pos"] = list(cell)


@scenario_helper("mechanisms.pet_door")
def pet_door_helper(ctx, mode="locked", panel="out", wildlife_risk=1.0, registered=None):
    c = ctx.config
    p = ctx.placer
    doors = B.find_door(c, "back") or B.find_door(c, "exterior")
    if not doors:
        raise SpecError("mechanisms.pet_door: no exterior door")
    host = doors[0]
    a, b = c["entities"][host]["connects"]
    inside = a if not c["rooms"][a].get("outdoor") else b
    outside = b if inside == a else a
    pd = p.make("pet_door", inside, label="pet door", connects=None)
    rec = c["entities"][pd]
    rec.pop("connects", None)
    cid_, conn_ = C.conn_of({"entities": c["entities"], "connections": c.get("connections", {})}, host)
    if conn_ and inside in conn_.get("cells", {}):
        rec["pos"] = list(conn_["cells"][inside])          # set into the door leaf, at the doorway
    else:
        rec["pos"] = list(c["entities"][host].get("pos") or [0, 0])
    rec["mounted"] = True
    rec["in_door"] = host
    rec["rooms"] = [inside, outside]
    rec["mech"] = {"kind": "pet_door", "mode": mode, "panel": panel, "wildlife_risk": float(wildlife_risk), "registered": list(registered or [])}
    rec["secured"] = mode == "locked" or panel == "in"
    return {"pet_door": pd, "inside": inside, "outside": outside}


# ----------------------------------------------------------------------------------
# devices

@scenario_helper("mechanisms.device")
def device_helper(ctx, room="kitchen", on=None, type="control_panel_device", label="appliance", settings=None, setting_options=None,
                  control_kinds=None, mech=None, hidden=None, panel=None, ui=None, appliance=None, props=None, mount=False):
    c = ctx.config
    p = ctx.placer
    base = dict(props or {})
    base.update(label=label)
    if on:
        eid = p.on(on, type, **base)
    elif mount:
        eid = p.mount(_room(ctx, room), type, height=C.num(base.get("mount_height_m"), 1.4), **base)
    else:
        rid = _room(ctx, room)
        surfaces = [s for s in p.find("counter", room=rid) + p.find("table", room=rid) if reachable_entity(ctx, s)]
        eid = None
        for s in surfaces:
            eid = p.on(s, type, **base)
            if eid:
                break
        if eid is None:
            shelf = p.mount(rid, "wall_shelf", height=0.9, strict=True, label="shelf")
            if shelf is not None:
                eid = p.on(shelf, type, **base)
        if eid is None:
            eid = p.floor_item(rid, type, **base)
    if eid is None:
        raise SpecError("mechanisms.device: no space")
    rec = c["entities"][eid]
    rec["settings"] = copy.deepcopy(settings or rec.get("settings") or {})
    rec["setting_options"] = copy.deepcopy(setting_options or rec.get("setting_options") or {})
    rec["control_kinds"] = copy.deepcopy(control_kinds or rec.get("control_kinds") or {})
    if panel:
        rec["panel"] = copy.deepcopy(panel)
    if ui:
        rec["ui"] = copy.deepcopy(ui)
        from .controls import render
        rec["display"] = render(rec["ui"])
    if appliance:
        rec["appliance"] = copy.deepcopy(appliance)
    _merge_mech(rec, mech, hidden)
    return {"device": eid}


THERMOSTAT_SYNONYMS = {"Mode": ["Mode", "System", "Heat/Cool"], "Away": ["Away", "Eco", "Leave"], "Settings": ["Settings", "Menu", "Options"],
                       "Setpoint": ["Temperature", "Set temp", "Target"]}


@scenario_helper("mechanisms.touchscreen")
def touchscreen_helper(ctx, room="hallway", preset="thermostat", goal_mode=None, goal_setpoint=None):
    """A wall touchscreen with a randomized menu layout (labels, order, nesting). Returns device and the labels used."""
    rng = ctx.rng
    labels = {k: rng.choice(v) for k, v in sorted(THERMOSTAT_SYNONYMS.items())}
    modes = ["heat", "cool", "auto", "off", "away"]
    setpoints = [16, 17, 18, 19, 20, 21, 22, 23]
    mode_items = [{"label": m.capitalize(), "stage": ["mode", m]} for m in modes]
    rng.shuffle(mode_items)
    sp_items = [{"label": "{} C".format(t), "stage": ["setpoint_c", t]} for t in setpoints]
    save = {"label": "Save", "save": True}
    back = {"label": "Back", "back": True}
    nested = rng.random() < 0.5
    screens = {
        "mode": {"title": labels["Mode"], "items": mode_items + [save, back]},
        "setpoint": {"title": labels["Setpoint"], "items": sp_items + [save, back]},
    }
    home_items = [{"label": labels["Setpoint"], "goto": "setpoint"}, {"label": "Fan", "goto": "fan"}]
    screens["fan"] = {"title": "Fan", "items": [{"label": "Auto", "stage": ["fan", "auto"]}, {"label": "On", "stage": ["fan", "on"]}, save, back]}
    if nested:
        screens["settings"] = {"title": labels["Settings"], "items": [{"label": labels["Mode"], "goto": "mode"}, {"label": "Schedule", "goto": "schedule"}, back]}
        home_items.append({"label": labels["Settings"], "goto": "settings"})
    else:
        home_items.append({"label": labels["Mode"], "goto": "mode"})
        home_items.append({"label": "Schedule", "goto": "schedule"})
    screens["schedule"] = {"title": "Schedule", "items": [{"label": "Sleep 17 C", "stage": ["schedule.sleep", 17]},
                                                         {"label": "Sleep 19 C", "stage": ["schedule.sleep", 19]},
                                                         {"label": "Save", "save": True, "confirm": {"text": "Apply to which days?", "options": [
                                                             {"label": "This day only", "scope": "day"}, {"label": "All days", "scope": "all"},
                                                             {"label": "Cancel", "scope": "cancel"}]}}, back]}
    rng.shuffle(home_items)
    screens["home"] = {"title": "Home", "items": home_items}
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    settings = {"mode": "heat", "setpoint_c": 21, "fan": "auto", "schedule": {d: {"wake": 21, "sleep": 18} for d in days}}
    options = {"mode": modes, "setpoint_c": {"min": 10, "max": 30}, "fan": ["auto", "on"], "schedule": None}
    ui = {"screen": "home", "screens": screens, "staged": {}, "dialog": None, "timeout_s": 45.0, "last_touch_s": 0.0, "day": "fri"}
    room = _room(ctx, room, fallbacks=("hallway", "entryway", "living_room", "dining_room"))
    out = device_helper(ctx, room=room, type="touchscreen_device", label="smart thermostat", settings=settings,
                        setting_options={k: v for k, v in options.items() if v is not None},
                        control_kinds={"mode": "touchscreen", "setpoint_c": "touchscreen", "fan": "touchscreen", "schedule": "touchscreen"},
                        ui=ui, mount=True, props={"mount_height_m": 1.4})
    out.update({"label_" + k.lower(): v for k, v in labels.items()})
    out["nested"] = nested
    return out


@scenario_helper("mechanisms.high_chair")
def high_chair_helper(ctx, room="kitchen", folded=True, stiff_side=None, tray="off"):
    c = ctx.config
    p = ctx.placer
    rid = _room(ctx, room)
    eid = p.floor_item(rid, "high_chair", portable=True, two_handed=True, blocks_path=False, openable=True, open=not folded)
    if eid is None and room in ("kitchen", "dining_room"):
        # a crowded kitchen: the chair stands by the dining table instead
        for other in [r for t in ("dining_room", "living_room") for r in ctx.placer.rooms(t)]:
            eid = p.floor_item(other, "high_chair", portable=True, two_handed=True, blocks_path=False, openable=True, open=not folded)
            if eid is not None:
                rid = other
                break
    if eid is None:
        raise SpecError("no space for a high chair")
    rec = c["entities"][eid]
    rec["mech"] = {"kind": "high_chair", "folded": bool(folded), "frame_locks": {"left": not folded, "right": not folded},
                   "tray": tray, "brakes": False, "harness": "unbuckled"}
    if stiff_side:
        rec["_mech"] = {"stiff_lock": stiff_side}
    return {"high_chair": eid, "room": rid}


@scenario_helper("mechanisms.alarm_panel")
def alarm_helper(ctx, room="entryway", code="2468", pet_immune_kg=0.0):
    c = ctx.config
    p = ctx.placer
    rid = _room(ctx, room, fallbacks=ENTRY_ROOMS[1:])
    eid = p.mount(rid, "alarm_panel", height=1.4)
    rec = c["entities"][eid]
    rec["_code"] = str(code)
    rec["settings"]["pet_immune_kg"] = float(pet_immune_kg)
    return {"alarm_panel": eid}


@scenario_helper("mechanisms.dispenser")
def dispenser_helper(ctx, kind="soap", room="bathroom", on=None, locked=False, primed=True, liquid_l=0.25, props=None):
    c = ctx.config
    p = ctx.placer
    rid = _room(ctx, room)
    surface = on
    if surface is None:
        options = [s for s in p.find("counter", room=rid) + p.find("table", room=rid) + p.find("wall_shelf", room=rid)
                   if c["entities"][s].get("surface") and not c["entities"][s].get("openable") and reachable_entity(ctx, s)]
        if options:
            surface = options[0]
        else:
            surface = p.mount(rid, "wall_shelf", height=0.95, strict=True, label="sink-side shelf", size_m=[0.6, 0.2, 0.03],
                              surface_size_m=[0.6, 0.2])
    per = {"soap": 1.2, "lotion": 1.5, "sanitizer": 1.5, "foaming": 0.8}.get(kind, 1.2)
    liquid = {"soap": "hand_soap", "lotion": "lotion", "sanitizer": "hand_sanitizer", "foaming": "foaming_hand_soap"}.get(kind, "hand_soap")
    base = dict(label="{} pump".format(kind), liquid=liquid, liquid_l=float(liquid_l), pump={"ml_per_stroke": per, "locked": bool(locked),
                                                                                          "primed": bool(primed), "prime_strokes": 3},
                label_text=liquid.replace("_", " ").upper())     # the printed label (pump bottles look alike until read)
    base.update(props or {})
    if surface and c["entities"][surface].get("surface"):
        eid = p.on(surface, "pump_bottle", **base)
    elif surface:
        c["entities"][surface]["accepts_on"] = True
        eid = p.on(surface, "pump_bottle", **base) or p.floor_item(rid, "pump_bottle", **base)
    else:
        eid = p.floor_item(rid, "pump_bottle", **base)
    if eid is None:
        raise SpecError("no space for a dispenser")
    return {"dispenser": eid, "room": rid, "surface": c["entities"][eid]["location"]}
