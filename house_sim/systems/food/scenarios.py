"""Scenario helpers for food task specs (setup op {"op": "helper", "name": "food.<x>", "args": {...}}).

food.kitchen        bind (or build stand-ins for) the kitchen fixtures a food task needs: counter,
                    cutting board, fridge with shelves + high/low-humidity crispers + door bin,
                    freezer, sink, trash and compost bins. Uses appliance/plumbing/cleaning-owned
                    fixtures when the house has them; otherwise core furniture configured with
                    the same fields (internal_temp_c, drain, waste_stream ...).
food.dated          spawn a packaged item with a printed regional date label a given number of
                    days from the scenario start (hidden ISO truth + matching spoilage).
food.groceries      grocery bags on the counter holding frozen / refrigerated / pantry items at
                    realistic just-bought temperatures.
food.eaters         give residents food profiles (age, allergies, diet, dislikes, orders).
food.recipe         a recipe card whose text is rendered from a structured recipe (_recipe).
"""
import copy
import datetime

from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from ...kernel.predicates import root_room
from . import data as D
from . import model as M


def _start(ctx):
    try:
        return datetime.datetime.fromisoformat(ctx.config.get("clock", {}).get("start", "2026-10-09T10:00:00")).date()
    except ValueError:
        return datetime.date(2026, 10, 9)


def _in_room(ctx, eid, room):
    state = ctx.state()
    return root_room(state, eid) == room


def _work_surfaces(state, room):
    out = []
    for eid in geometry.room_entities(state, room):
        rec = state["entities"][eid]
        if rec.get("blocks_path") and (rec.get("accepts_items") or rec.get("openable")) and not rec.get("seat"):
            out.append(eid)
    return sorted(out)


def _unreachable(ctx, room):
    """Work surfaces in the room that the walkable floor joined to its doorways does not reach (the kernel's access
    model, geometry.access_report; people and pets step aside, so they are not obstacles here)."""
    state = ctx.state()
    surfaces = _work_surfaces(state, room)
    if not surfaces or not geometry.doorway_cells(state, room):
        return set()
    targets = {}
    for eid in surfaces:
        rec = state["entities"][eid]
        cells = [c for c in geometry.footprint(rec) if geometry.in_grid(state["rooms"][room], c)]
        if cells:
            targets[eid] = (cells, geometry.access_radius(rec), False)
    blocked = set(geometry.blocked_cells(state, room, include_agents=False))
    ok = geometry.access_report(state, room, blocked, targets)["ok"]
    return set(surfaces) - set(ok)


def _flood(state, room, starts, blocked):
    """Cells 4-connected to any of starts (no corner cutting), avoiding blocked cells."""
    cols, rows = geometry.grid_size(state["rooms"][room])
    seen = set(s for s in starts if s is not None and s not in blocked and 0 <= s[0] < cols and 0 <= s[1] < rows)
    stack = sorted(seen)
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (x + dx, y + dy)
            if 0 <= nxt[0] < cols and 0 <= nxt[1] < rows and nxt not in blocked and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def reachable_cells(ctx, room):
    """Free floor cells connected to a doorway of the room right now (people and pets in the way count)."""
    state = ctx.state()
    return geometry.reachable_cells(state, room, set(geometry.blocked_cells(state, room, include_agents=True)))


def _entry_cell(ctx, room):
    """Where the robot first stands in `room`: the doorway on its route from its start room, or its own cell when it
    already starts there. None when unknown."""
    from ...tasks.skills import room_route
    cfg = ctx.config
    robot = cfg.get("robot") or {}
    start = robot.get("room") or cfg.get("start_room")
    if start == room:
        return tuple(robot["pos"]) if robot.get("pos") else None
    if not start:
        return None
    route = room_route({"connections": cfg.get("connections", {}), "entities": cfg.get("entities", {}),
                        "rooms": cfg.get("rooms", {}), "robot": robot}, start, room)     # the route go_room will take
    if not route:
        return None
    cell = cfg["connections"][route[-1][1]].get("cells", {}).get(room)
    return tuple(cell) if cell else None


def main_region(ctx, room):
    """Floor cells the robot can walk to after entering `room` (the region of its entry doorway); falls back to every
    doorway-reachable cell when the entry is unknown."""
    state = ctx.state()
    blocked = set(geometry.blocked_cells(state, room, include_agents=True))
    entry = _entry_cell(ctx, room)
    if entry is not None:
        region = _flood(state, room, [entry], blocked - {entry})
        if len(region) > 1:
            return region
    return geometry.reachable_cells(state, room, blocked)


def _furniture(ctx, room, type_name, sizes=None, strict_first=False, **props):
    """A free-standing stand-in unit (fridge, freezer, sink, pantry cabinet, work counter) through Placer.furniture,
    which keeps doorway connectivity and every accessible piece of furniture, fixture, mount and the dock accessible
    (kernel v2.3); the first size that fits, else None (the caller falls back or raises SpecError). strict_first (the
    big cold units): every size the strict way before any fallback spot, so a 1.8 m refrigerator never walls the robot's
    entry corner off from the counters when a slimmer unit fits properly."""
    for fallback in ((False, True) if strict_first else (True,)):
        for size in (sizes or [props.get("size_m")]):
            if size:
                props["size_m"] = size
            eid = ctx.placer.furniture(room, type_name, fallback=fallback, **props)
            if eid is not None:
                return eid
    return None


def _free_area(ents, eid):
    """Surface left on a counter/table after what already stands on it (m2)."""
    rec = ents[eid]
    size = rec.get("surface_size_m") or geometry.footprint_m(rec)
    used = 0.0
    for other in ents.values():
        if other.get("location") == eid and other.get("relation", "on") == "on":
            s = other.get("size_m") or [0.1, 0.1]
            used += float(s[0]) * float(s[1])
    return float(size[0]) * float(size[1]) - used


def _find(ctx, room, pred):
    for eid in sorted(ctx.config["entities"]):
        rec = ctx.config["entities"][eid]
        if M.food(rec) is None and pred(rec) and _in_room(ctx, eid, room):
            return eid
    return None


# ----------------------------------------------------------------------
# counter space: other domains crowd kitchen counters (microwave, coffee machine, air fryer, dish soap ...), so the
# work counters are chosen by actually packing what the task will put on them (same first-fit rule as Placer.on)

WORK_KEYS = ("counter", "counter2")


def _probe(ctx, type_name, props=None):
    """(size_m, rot) a spawned item of this type would have."""
    try:
        rec = ctx.reg.make(type_name, "__probe__", **copy.deepcopy(props or {}))
    except (KeyError, TypeError, ValueError):
        rec = ctx.reg.make(type_name, "__probe__")
    return list(rec.get("size_m") or [0.1, 0.1, 0.1]), rec.get("rot")


def space_needs(ctx, room_for=None):
    """[(work key, size_m, rot)] in placement order. room_for = {"counter": [type or {"type", "props", "count"}],
    "counter2": [...]} when given, else read from the task's own setup ops (spawn "on": "{counter}" / "{counter2}",
    food.dated / food.recipe on a counter, food.groceries bags)."""
    out = []

    def add(key, type_name, props=None, count=1):
        if not ctx.reg.known(type_name):
            return
        size, rot = _probe(ctx, type_name, props)
        for _ in range(max(1, int(count))):
            out.append((key, size, rot))
    if room_for is not None:
        for key in WORK_KEYS:
            for item in room_for.get(key, []):
                if isinstance(item, dict):
                    add(key, item["type"], item.get("props"), item.get("count", 1))
                else:
                    add(key, item)
        return out
    keys = {"{" + k + "}": k for k in WORK_KEYS}
    for op in (getattr(ctx, "spec", None) or {}).get("setup", []) or []:
        if not isinstance(op, dict):
            continue
        if op.get("op") == "spawn" and op.get("on") in keys and op.get("type"):
            add(keys[op["on"]], op["type"], op.get("props"), op.get("count", 1))
        elif op.get("op") == "helper":
            args = op.get("args") or {}
            on = args.get("on")
            if on not in keys:
                continue
            if op.get("name") in ("food.dated",) and args.get("type"):
                add(keys[on], args["type"], args.get("props"))
            elif op.get("name") == "food.recipe":
                add(keys[on], "recipe_card")
            elif op.get("name") == "food.groceries":
                add(keys[on], "grocery_bag", None, args.get("bags", 2))
    return out


def _surface_ok(rec):
    return bool(rec.get("accepts_items")) and not rec.get("container") and (rec.get("surface") or rec.get("surface_size_m"))


def fits(ctx, plan, ignore=()):
    """True when every (surface id, size_m, rot) in plan fits, in order, on its surface next to what already stands
    there (minus ignore), using the kernel's first-fit surface_spot exactly as Placer.on will."""
    ents = ctx.config["entities"]
    surfaces = sorted(set(s for s, _, _ in plan))
    if not surfaces:
        return True
    temp = {s: ents[s] for s in surfaces}
    ignore = set(ignore)
    for eid, rec in ents.items():
        if rec.get("location") in temp and eid not in ignore:
            temp[eid] = rec
    state = {"entities": temp}
    for i, (surface, size, rot) in enumerate(plan):
        pid = "__probe_{}".format(i)
        rec = {"location": surface, "relation": "on", "size_m": size}
        if rot is not None:
            rec["rot"] = rot
        temp[pid] = rec
        spot = geometry.surface_spot(state, surface, pid)
        if spot is None:
            return False
        rec["spos"] = spot
    return True


def _plan_for(choice, needs, extra=()):
    c1, c2 = choice[0], choice[1]
    return list(extra) + [({"counter": c1, "counter2": c2}[key], size, rot) for key, size, rot in needs]


def _bound_ids(ctx):
    out = set()
    for value in ctx.bindings.values():
        for v in (value if isinstance(value, list) else [value]):
            if isinstance(v, str):
                out.add(v)
    return out


def _dressing(ctx, surface, keep=()):
    """Food-owned counter dressing from the furnisher (knife block, utensil crock, fruit bowl, bread box, boards, salt
    and pepper ...) that may be put away to free a work area, largest first. Never anything a task already bound."""
    ents = ctx.config["entities"]
    keep = set(keep) | _bound_ids(ctx)
    out = []
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("location") != surface or rec.get("relation", "on") != "on" or eid in keep:
            continue
        if getattr(ctx.reg, "sources", {}).get(rec.get("type"), "") == "food.json":
            w, d = geometry.footprint_m(rec)
            out.append((-w * d, eid))
    return [eid for _, eid in sorted(out)]


def _tree_mass(ents, eid):
    """Mass of an entity with everything inside/on it (kg)."""
    total, stack, seen = 0.0, [eid], set()
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in ents:
            continue
        seen.add(cur)
        total += float(ents[cur].get("mass_kg", 0.0) or 0.0)
        stack.extend(k for k, v in ents.items() if v.get("location") == cur)
    return total


def _stow(ctx, eid, room, keep_off):
    """Put a dressing item away: into a kitchen cabinet/drawer it fits, else onto another free surface, else take it out
    of the scene (with its contents)."""
    ents = ctx.config["entities"]
    rec = ents[eid]
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    vol = float(rec.get("volume_l", 0.0) or 0.0)
    state = ctx.state()
    for cid in sorted(ents):
        c = ents[cid]
        if cid == eid or cid in keep_off or not c.get("container") or not c.get("accepts_items"):
            continue
        if not any(t in (c.get("is_a") or []) + [c.get("type")] for t in ("cabinet", "drawer", "upper_cabinet")):
            continue
        if not _storage_ok(c) or c.get("sink") or "sink" in (c.get("label") or "") or _chemicals_in(ents, cid):
            continue      # never in with the cleaners under the sink
        if root_room(state, cid) != room or not geometry.fits_inside(size, c.get("inner_size_m")):
            continue
        cap = float(c.get("capacity_l", 0) or 0)
        used = sum(float(e.get("volume_l", 0) or 0) for e in ents.values() if e.get("location") == cid and e.get("relation") == "in")
        if cap > 0 and used + vol > cap + 1e-9:
            continue
        shelf = None
        if c.get("shelf_heights"):
            rating, mass = M.shelf_rating(c), _tree_mass(ents, eid)
            shelf = next((s for s in range(len(c["shelf_heights"]))
                          if rating is None or M.shelf_load(ents, cid, s) + mass <= 0.7 * rating), None)
            if shelf is None:
                continue      # every shelf is near its load rating
        rec["location"], rec["relation"] = cid, "in"
        rec.pop("spos", None)
        if shelf is not None:
            rec["shelf"] = shelf
        else:
            rec.pop("shelf", None)
        return cid
    for sid in sorted(ents):
        s = ents[sid]
        kinds = (s.get("is_a") or []) + [s.get("type")]
        if sid in keep_off or sid == eid or not _surface_ok(s) or not geometry.blocks(s) or root_room(state, sid) != room:
            continue
        if not ("counter" in kinds or "table" in kinds) or s.get("appliance") or s.get("heat_source") or s.get("drain"):
            continue      # never onto a cooktop, appliance or sink
        old = (rec.get("location"), rec.get("spos"))
        rec["location"] = sid
        spot = geometry.surface_spot(ctx.state(), sid, eid)
        if spot is not None:
            rec["spos"], rec["relation"] = spot, "on"
            return sid
        rec["location"], rec["spos"] = old
    doomed = [eid]
    i = 0
    while i < len(doomed):
        doomed += sorted(k for k, e in ents.items() if e.get("location") == doomed[i])
        i += 1
    for k in doomed:
        ents.pop(k, None)
    return None


STANDIN_FRIDGE = {"label": "refrigerator", "category": "appliance", "size_m": [0.8, 0.7, 1.8], "inner_size_m": [0.7, 0.6, 0.34],
                  "shelf_heights": [0.3, 0.7, 1.1, 1.45], "capacity_l": 350.0, "internal_temp_c": 3.5, "setpoint_c": 3.5,
                  "cooling_w": 60.0, "ua_w_k": 0.9, "open_ua_w_k": 15.0, "air_heat_capacity_j_k": 20000.0, "food_air_model": True,
                  "tags": ["refrigerator", "food_standin"], "mass_kg": 70.0, "movable": False, "material": "steel",
                  "handle_kind": "pull", "hands_free_operable": False}
STANDIN_SINK = {"label": "kitchen sink", "category": "fixture", "drain": True, "water_source": True, "container": True,
                "accepts_items": True, "openable": False, "open": True, "shelf_heights": None, "inner_size_m": [0.5, 0.4, 0.2],
                "capacity_l": 40.0, "material": "stainless_steel", "tags": ["sink", "food_standin"]}
STANDIN_FREEZER = {"label": "freezer", "category": "appliance", "size_m": [0.6, 0.65, 0.85], "inner_size_m": [0.5, 0.5, 0.3],
                   "shelf_heights": [0.1, 0.45], "capacity_l": 110.0, "internal_temp_c": -18.0, "setpoint_c": -18.0, "cooling_w": 50.0,
                   "ua_w_k": 0.6, "open_ua_w_k": 10.0, "air_heat_capacity_j_k": 12000.0, "food_air_model": True,
                   "tags": ["freezer", "food_standin"], "mass_kg": 40.0, "movable": False, "material": "steel"}


def _standin(ctx, room, what, sizes, props):
    """A stand-in appliance (only when no appliance/plumbing domain provides one) standing on the floor like the real
    thing; SpecError when the kitchen has no spot that keeps every doorway and fixture accessible."""
    eid = _furniture(ctx, room, "cabinet", sizes=sizes, strict_first=True, **copy.deepcopy(props))
    if eid is None:
        raise SpecError("food.kitchen: no floor space in {} for a stand-in {}".format(room, what))
    return eid


def _inset_sink(ctx, room):
    """A sink basin set into a counter run (a kitchen with no floor left for a sink unit): it takes 0.55 x 0.45 m of the
    counter top, which is where real sinks sit. SpecError when no reachable counter has that much free top."""
    ents = ctx.config["entities"]
    reach = main_region(ctx, room)
    rdef = ctx.config["rooms"][room]
    for cid in ctx.placer.find("counter", room=room):
        rec = ents[cid]
        if rec.get("location") != room or not _surface_ok(rec) or rec.get("heat_source") or rec.get("appliance"):
            continue
        cells = geometry.footprint(rec)
        if not (geometry.near_cells(cells, rdef) - set(cells)) & reach:
            continue
        props = copy.deepcopy(STANDIN_SINK)
        props.update(size_m=[0.55, 0.45, 0.2], inner_size_m=[0.5, 0.4, 0.18], inset=True, movable=False, portable=False,
                     blocks_path=False)     # set into the counter top, not standing on the floor
        eid = ctx.placer.on(cid, "cabinet", **props)
        if eid is not None:
            return eid
    raise SpecError("food.kitchen: no floor space or free counter top in {} for a stand-in kitchen sink".format(room))


def _counter_candidates(ctx, room, blocked):
    p = ctx.placer
    ents = ctx.config["entities"]
    counters = [c for c in p.find("counter", room=room) if c not in blocked and _surface_ok(ents[c])]
    tables = [t for t in p.find("table", room=room) if t not in blocked and ents[t].get("location") == room and _surface_ok(ents[t])]
    return counters, tables


def _work_area(ctx, room, blocked, needs, board_size=None, ignore=(), prefer=None, split=True):
    """(counter, counter2, stand cell) where the task's items fit, or None. Preference: a counter plus a second surface
    (counter, else kitchen table/island) that one floor cell reaches; then one counter holding both work areas; then
    two counters reached from different cells. Only floor the robot reaches from its entry doorway counts."""
    ents = ctx.config["entities"]
    counters, tables = _counter_candidates(ctx, room, blocked)
    if not counters:
        counters = list(tables)
    if prefer is not None and prefer in ents:
        counters = [prefer] + [c for c in counters if c != prefer]
    if not counters:
        return None
    rank = {c: i for i, c in enumerate(counters)}
    counters.sort(key=lambda c: (c != prefer, -_free_area(ents, c), c))
    reach = main_region(ctx, room)
    rdef = ctx.config["rooms"][room]
    stands = {}

    def stand_cells(eid):
        if eid not in stands:
            cells = geometry.footprint(ents[eid])
            stands[eid] = set(c for c in geometry.near_cells(cells, rdef) - set(cells) if c in reach)
        return stands[eid]

    def ok(c1, c2):
        extra = []
        if board_size is not None and not any(ents[e].get("location") == c1 and ents[e].get("tool_kind") == "cutting_board"
                                              and e not in ignore for e in ents):
            extra.append((c1, board_size[0], board_size[1]))
        return fits(ctx, _plan_for((c1, c2), needs, extra), ignore)
    seconds = counters + [t for t in tables if t not in counters]
    for c1 in counters:
        near1 = stand_cells(c1)
        if not near1:
            continue
        for c2 in sorted((c for c in seconds if c != c1), key=lambda c: (-_free_area(ents, c), rank.get(c, 99), c)):
            shared = near1 & stand_cells(c2)
            if shared and ok(c1, c2):
                return (c1, c2, sorted(shared)[0])
    for c1 in counters:
        near1 = sorted(stand_cells(c1))
        if near1 and ok(c1, c1):
            return (c1, c1, near1[0])
    if not split:
        return None
    for c1 in counters:
        near1 = sorted(stand_cells(c1))
        if not near1:
            continue
        for c2 in seconds:
            if c2 != c1 and stand_cells(c2) and ok(c1, c2):
                return (c1, c2, near1[0])
    return None


def _make_room(ctx, room, blocked, needs, board_size, split=True):
    """Put food-owned dressing away from the counters (largest first) until the task's work area fits."""
    counters, tables = _counter_candidates(ctx, room, blocked)
    surfaces = counters or tables
    pool = []
    for s in surfaces:
        pool += _dressing(ctx, s)
    ents = ctx.config["entities"]
    pool.sort(key=lambda e: (-geometry.footprint_m(ents[e])[0] * geometry.footprint_m(ents[e])[1], e))
    ignore = []
    for eid in pool:
        ignore.append(eid)
        choice = _work_area(ctx, room, blocked, needs, board_size, ignore=ignore, split=split)
        if choice is not None:
            keep_off = {choice[0], choice[1]}
            for moved in ignore:
                if ents.get(moved, {}).get("location") in keep_off:
                    _stow(ctx, moved, room, keep_off)
            return _work_area(ctx, room, blocked, needs, board_size, split=split) or choice
    return None


def _chemicals_in(ents, cid):
    return any(e.get("location") == cid and M.is_chemical(e) for e in ents.values())


PANTRY_HEADROOM_KG = 6.0      # a task's cans, jars and a flour bag on the low and middle shelves stay under the rating
PANTRY_HEADROOM_MIN_KG = 2.5  # second pass: a few cans


def _shelf_headroom(ents, cid, headroom=PANTRY_HEADROOM_KG):
    rec = ents[cid]
    rating = M.shelf_rating(rec)
    if rating is None:
        return True
    heights = rec.get("shelf_heights") or [0.0]
    return all(M.shelf_load(ents, cid, s) + headroom <= rating for s in sorted({0, min(1, len(heights) - 1)}))


def _storage_ok(rec):
    return not (rec.get("internal_temp_c") is not None or rec.get("drain") or rec.get("water_source") or rec.get("waste_stream")
                or rec.get("appliance") or rec.get("locked") or rec.get("off_limits") or rec.get("private")
                or rec.get("insulated") or M.food(rec) is not None)


PANTRY_MIN_DEPTH_M = 0.35     # room for a can behind another (FIFO by depth); wall units are ~0.30 m deep inside
PANTRY_MIN_HEIGHT_M = 0.30    # cereal boxes, pasta boxes and bottles stand upright


def _unit_reachable(ctx, eid, kitchen):
    """The robot can get within reach of a storage unit (its room's walkable floor; the entry region in the kitchen)."""
    state = ctx.state()
    rid, cells = geometry.anchor(state, eid)
    if rid not in state["rooms"] or not cells:
        return False
    reach = main_region(ctx, rid) if rid == kitchen else reachable_cells(ctx, rid)
    radius = geometry.REACH_CELLS + (1 if geometry.mounted_overhead(state, eid) else 0)
    if not (geometry.near_cells(cells, state["rooms"][rid], radius) - set(cells)) & reach:
        return False
    return rid == kitchen or _leaves_kitchen_toward(ctx, kitchen, rid)


def _leaves_kitchen_toward(ctx, kitchen, rid):
    """The robot, standing in the kitchen's entry region, can walk out toward `rid`: the kitchen-side cell of the first
    doorway on the route is in (or beside) that region."""
    from ...tasks.skills import room_route
    cfg = ctx.config
    route = room_route({"connections": cfg.get("connections", {}), "entities": cfg.get("entities", {}),
                        "rooms": cfg.get("rooms", {}), "robot": cfg.get("robot") or {}}, kitchen, rid)
    if not route:
        return False
    cell = (cfg["connections"].get(route[0][1]) or {}).get("cells", {}).get(kitchen)
    if not cell:
        return True
    region = main_region(ctx, kitchen)
    cell = tuple(cell)
    return cell in region or any((cell[0] + dx, cell[1] + dy) in region for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))


def _pantry(ctx, room, blocked):
    """(dry-goods storage, the room it is in when that is not the kitchen): pantry-room shelving, a kitchen unit tagged
    pantry, a base cabinet, any other roomy cabinet, kitchen shelving; never a cold unit, the under-sink cabinet, a bin,
    a cupboard holding cleaning chemicals (incl. dishwasher detergent), a unit too shallow/low for dry goods, a shelf
    already near its load rating, or a unit the robot cannot reach. A kitchen with none of these (some apartments have
    only wall units and drawers, crowded kitchens keep detergent in every base cabinet) gets a stand-in pantry cabinet
    or shelving unit; a kitchen too cramped for one uses storage in an adjacent room (dining-room sideboard, hall
    shelving). SpecError only when the house has no reachable storage at all."""
    p = ctx.placer
    ents = ctx.config["entities"]

    def usable(eid, headroom):
        rec = ents[eid]
        inner = list(rec.get("inner_size_m") or [0.0, 0.0, 0.0]) + [0.0, 0.0, 0.0]
        roomy = float(inner[1]) >= PANTRY_MIN_DEPTH_M and float(inner[2]) >= PANTRY_MIN_HEIGHT_M
        sinkish = rec.get("sink") or "sink" in (rec.get("label") or "") or "vanity" in (rec.get("label") or "")
        return (eid not in blocked and rec.get("container") and rec.get("accepts_items") and roomy and not sinkish
                and _storage_ok(rec) and not _chemicals_in(ents, eid) and _shelf_headroom(ents, eid, headroom)
                and _unit_reachable(ctx, eid, room))
    candidates = []
    for prid in p.rooms("pantry"):
        candidates += [(e, prid) for e in p.find("closet_shelving", room=prid) + p.find("cabinet", room=prid)]
    kitchen_units = [e for e in sorted(ents) if _in_room(ctx, e, room) and ents[e].get("accepts_items")]
    for tier in ([e for e in kitchen_units if "pantry" in (ents[e].get("tags") or []) or "pantry" in (ents[e].get("label") or "")],
                 [e for e in p.find("cabinet", room=room) if ents[e].get("type") == "cabinet"],
                 [e for e in p.find("cabinet", room=room) if geometry.blocks(ents[e])],
                 p.find("cabinet", room=room),
                 p.find("closet_shelving", room=room)):
        candidates += [(e, None) for e in tier]
    for headroom in (PANTRY_HEADROOM_KG, PANTRY_HEADROOM_MIN_KG):
        for eid, prid in candidates:
            if usable(eid, headroom):
                return eid, prid
    tags = ["pantry", "food_standin"]
    for type_name, sizes, label, extra in (
            ("cabinet", [[0.8, 0.6, 0.9], [0.6, 0.5, 0.9], [0.5, 0.45, 0.9]], "pantry cabinet", {}),
            ("closet_shelving", [[1.0, 0.4, 2.0], [0.6, 0.4, 1.8]], "pantry shelving", {"blocks_path": True})):
        eid = _furniture(ctx, room, type_name, sizes=sizes, label=label, tags=list(tags), **extra)
        if eid is not None:
            return eid, None
    nearby = sorted(set(r for c in ctx.config.get("connections", {}).values() if room in c.get("rooms", [])
                        for r in c["rooms"] if r != room and not ctx.config["rooms"].get(r, {}).get("outdoor")
                        and not ctx.config["rooms"].get(r, {}).get("private") and not ctx.config["rooms"].get(r, {}).get("off_limits")))
    for other in nearby:
        for eid in p.find("cabinet", room=other) + p.find("closet_shelving", room=other):
            if usable(eid, PANTRY_HEADROOM_MIN_KG):
                return eid, other
    # wall-standing shelving on a reachable wall cell (Placer.mount checks footprint, clearance, access and reach)
    eid = p.mount(room, "closet_shelving", label="pantry shelving", tags=list(tags), strict=True)
    if eid is not None and _unit_reachable(ctx, eid, room):
        return eid, None
    if eid is not None:
        ents.pop(eid, None)
    # last resort: any reachable chemical-free cupboard, even a shallow wall unit
    for eid, prid in candidates:
        rec = ents[eid]
        if rec.get("container") and _storage_ok(rec) and not _chemicals_in(ents, eid) and _unit_reachable(ctx, eid, room):
            return eid, prid
    raise SpecError("food.kitchen: no reachable food-safe dry-goods storage in or next to {}".format(room))


@scenario_helper("food.kitchen")
def kitchen(ctx, room=None, need=("fridge", "freezer", "sink", "trash", "compost"), board=True, board_type="cutting_board_wood",
            crispers=True, door_bin=True, room_for=None):
    """Bindings: kitchen, counter, counter2, stand, board, fridge, freezer, crisper, crisper_low, door_bin, fridge_bottom
    (shelf 0), fridge_mid, fridge_top (top shelf index), pantry, pantry_low, pantry_mid, pantry_room, sink, trash, compost.

    The work counters are chosen so that everything the task will put on them fits (room_for, else the task's own
    setup ops; see space_needs), they are reachable from where the robot enters the kitchen, and one floor cell reaches
    both when possible. Crowded counters first lose food-owned dressing (knife block, fruit bowl ...); a kitchen with
    no usable counter gets a free-standing work counter; SpecError only when nothing fits at all."""
    cfg = ctx.config
    ents = cfg["entities"]
    p = ctx.placer
    if room is None:
        rooms = p.rooms("kitchen")
        if not rooms:
            raise SpecError("food.kitchen: the house has no kitchen")
        room = rooms[0]
    out = {"kitchen": room}
    # big stand-in units first (only when no appliance/plumbing domain provides them), then the work spots
    fridge = freezer = None
    for eid in sorted(ents):
        rec = ents[eid]
        t = rec.get("internal_temp_c")
        if t is None or rec.get("insulated") or not (rec.get("accepts_items") or rec.get("container")) or not _in_room(ctx, eid, room):
            continue
        if rec.get("ice_bath") or rec.get("water_bath"):
            continue
        if t <= -5 and freezer is None:
            freezer = eid
        elif -2 <= t <= 8 and fridge is None:
            fridge = eid
    if "fridge" in need and fridge is None:
        fridge = _standin(ctx, room, "refrigerator", [[0.8, 0.7, 1.8], [0.6, 0.65, 1.8]], STANDIN_FRIDGE)
    if "freezer" in need and freezer is None:
        freezer = _standin(ctx, room, "freezer", [[0.6, 0.65, 0.85], [0.5, 0.55, 0.85]], STANDIN_FREEZER)
    out["fridge"], out["freezer"] = fridge, freezer
    if "sink" in need:
        sink = _find(ctx, room, lambda r: r.get("drain") and r.get("water_source"))
        if sink is None:
            sink = _furniture(ctx, room, "cabinet", sizes=[[0.8, 0.6, 0.9], [0.6, 0.55, 0.9]], **copy.deepcopy(STANDIN_SINK))
            if sink is None:
                sink = _inset_sink(ctx, room)
            ents[sink].pop("shelf_heights", None)
        out["sink"] = sink
    blocked = _unreachable(ctx, room)
    needs = space_needs(ctx, room_for)
    compost_bin = None
    if "compost" in need:
        compost_bin = _find(ctx, room, lambda r: r.get("waste_stream") == "compost" and (r.get("container") or r.get("accepts_items")))
    caddy = dict(label="compost caddy", waste_stream="compost", open=True, capacity_l=6.5, inner_size_m=[0.2, 0.15, 0.22],
                 size_m=[0.22, 0.17, 0.25], tags=["food_standin"])
    board_size = _probe(ctx, board_type) if board else None
    # one floor cell reaching both work areas (or one counter for both) beats two counters across the room, even when
    # food's own dressing has to be put away for it; split counters only when nothing else fits
    choice = None
    for split in (False, True):
        choice = _work_area(ctx, room, blocked, needs, board_size, split=split)
        if choice is None:
            choice = _make_room(ctx, room, blocked, needs, board_size, split=split)
        if choice is not None:
            break
    if choice is None:
        # every counter is crowded with other domains' appliances: a free-standing work counter (kitchen cart/island)
        extra = _furniture(ctx, room, "counter", sizes=[[1.2, 0.6, 0.9], [0.9, 0.5, 0.9], [0.6, 0.5, 0.9]],
                           label="kitchen work counter", tags=["food_standin"])
        if extra is not None:
            blocked = _unreachable(ctx, room)
            choice = _work_area(ctx, room, blocked, needs, board_size, prefer=extra)
    if choice is None:
        raise SpecError("food.kitchen: no reachable counter in {} has room for the task's items".format(room))
    out["counter"], out["counter2"] = choice[0], choice[1]
    out["stand"] = list(choice[2])
    if board:
        existing = next((e for e in sorted(ents) if ents[e].get("location") == out["counter"] and ents[e].get("tool_kind") == "cutting_board"), None)
        out["board"] = existing or p.on(out["counter"], board_type)
        if out["board"] is None:
            raise SpecError("food.kitchen: no room for a cutting board on the counter")
    if compost_bin is None and "compost" in need:
        # a countertop caddy only when it leaves the task's work area free; otherwise a floor pail
        if fits(ctx, _plan_for(choice, needs, [(out["counter2"], caddy["size_m"], None)])):
            compost_bin = p.on(out["counter2"], "storage_bin", **copy.deepcopy(caddy))
        if compost_bin is None:
            compost_bin = p.floor_item(room, "storage_bin", **copy.deepcopy(caddy))
    if "compost" in need:
        out["compost"] = compost_bin
    if fridge:
        heights = ents[fridge].get("shelf_heights") or [0.0]
        out["fridge_bottom"], out["fridge_top"] = 0, len(heights) - 1
        out["fridge_mid"] = min(1, len(heights) - 1)
        if crispers:
            hi = next((e for e in sorted(ents) if ents[e].get("location") == fridge and M.sub_zone_of(ents[e]) == "crisper_high"), None)
            if hi is None:
                hi = p.inside(fridge, "drawer", shelf=0, label="crisper drawer (high humidity)", tags=["crisper"], humidity="high",
                              size_m=[0.32, 0.5, 0.18], inner_size_m=[0.3, 0.48, 0.16], capacity_l=22.0, mount_height_m=0.0,
                              open=False, mass_kg=1.4, material="plastic", fragile=False)
            lo = next((e for e in sorted(ents) if ents[e].get("location") == fridge and M.sub_zone_of(ents[e]) == "crisper_low"), None)
            if lo is None:
                lo = p.inside(fridge, "drawer", shelf=0, label="crisper drawer (low humidity)", tags=["crisper"], humidity="low",
                              size_m=[0.32, 0.5, 0.18], inner_size_m=[0.3, 0.48, 0.16], capacity_l=22.0, mount_height_m=0.0,
                              open=False, mass_kg=1.4, material="plastic", fragile=False)
            out["crisper"], out["crisper_low"] = hi, lo
        if door_bin:
            db = next((e for e in sorted(ents) if ents[e].get("location") == fridge and M.sub_zone_of(ents[e]) == "door"), None)
            if db is None:
                db = p.inside(fridge, "basket", shelf=min(2, len(heights) - 1), label="fridge door bin", fridge_zone="door",
                              size_m=[0.6, 0.12, 0.15], inner_size_m=[0.58, 0.1, 0.14], capacity_l=8.0, material="plastic")
            out["door_bin"] = db
    pantry, pantry_room = _pantry(ctx, room, blocked)
    if pantry_room:
        out["pantry_room"] = pantry_room
    out["pantry"] = pantry
    if pantry is not None:
        heights = ents[pantry].get("shelf_heights") or [0.0]
        out["pantry_low"] = 0
        out["pantry_mid"] = min(1, len(heights) - 1)
    if "trash" in need:
        trash = _find(ctx, room, lambda r: r.get("waste_stream") == "trash" and (r.get("container") or r.get("accepts_items")))
        if trash is None:
            trash = p.floor_item(room, "storage_bin", label="kitchen trash can", waste_stream="trash", accepts_any_waste=True,
                                 open=True, capacity_l=50.0, inner_size_m=[0.36, 0.27, 0.6], size_m=[0.4, 0.3, 0.65], tags=["food_standin"])
        out["trash"] = trash
    _check_stand(ctx, room, out)
    return out


def _check_stand(ctx, room, out):
    """The robot's work cell must still connect to its entry doorway and be free of floor items after the stand-ins and
    bins were placed; otherwise pick another cell that reaches both work surfaces (or at least the counter). The
    surfaces themselves stay as chosen (their space was checked)."""
    ents = ctx.config["entities"]
    reach = main_region(ctx, room)
    state = ctx.state()
    hazards = set(geometry.hazard_cells(state, room))
    rdef = ctx.config["rooms"][room]

    def near(eid):
        cells = geometry.footprint(ents[eid])
        return set(c for c in geometry.near_cells(cells, rdef) - set(cells) if c in reach and c not in hazards)
    stand = tuple(out["stand"])
    if stand in reach and stand not in hazards:
        return
    both = near(out["counter"]) & near(out["counter2"])
    if both:
        out["stand"] = list(sorted(both)[0])
        return
    one = sorted(near(out["counter"]))
    if one:
        out["stand"] = list(one[0])


def dated_props(ctx, type_name, days, date_type=None, opened_days_ago=None, fmt=None):
    start = _start(ctx)
    day = start + datetime.timedelta(days=int(days))
    props = ctx.reg.resolve(type_name)
    dtype = date_type or props.get("date_type", "best_before")
    state = {"metadata": dict(ctx.config.get("metadata", {}))}
    if fmt:
        state["metadata"]["region"] = {"MDY": "us", "DMY": "uk", "YMD": "jp"}.get(fmt, fmt)
    life = float(props.get("life_days") or D.CATS.get(props.get("food", {}).get("cat", "other"), D.CATS["other"]).get("life", 7))
    spoil = max(0.0, min(0.9, 1.0 - (int(days) + 1.0) / max(1.0, life))) if props.get("perishable") else 0.05
    out = {"_expires": day.isoformat(), "date_type": dtype, "date_label": M.label_for_date(state, dtype, day), "_spoil": round(spoil, 4)}
    if opened_days_ago is not None:
        out["opened"] = True
        out["opened_on"] = (start - datetime.timedelta(days=int(opened_days_ago))).isoformat()
    return out


@scenario_helper("food.dated")
def dated(ctx, type, days, var="item", into=None, on=None, shelf=None, date_type=None, opened_days_ago=None, fmt=None,
          spos=None, props=None, spoil=None):
    """Spawn a dated package: days from the start date (negative = past); returns {var: id}."""
    extra = dated_props(ctx, type, days, date_type, opened_days_ago, fmt)
    if spoil is not None:
        extra["_spoil"] = float(spoil)
    extra.update(copy.deepcopy(props or {}))
    if spos is not None:
        extra["spos"] = list(spos)
    if into:
        eid = ctx.placer.inside(into, type, shelf=shelf, **extra)
    elif on:
        eid = ctx.placer.on(on, type, spos=spos, **extra)
    else:
        raise SpecError("food.dated needs into or on")
    if eid is None:
        raise SpecError("food.dated: no room for {}".format(type))
    return {var: eid}


GROCERY_TEMPS = {"freezer": -12.0, "fridge": 7.0}


@scenario_helper("food.groceries")
def groceries(ctx, on, items, bags=2, frozen_temp_c=-12.0, chilled_temp_c=7.0):
    """Grocery bags on a counter; items is a list of type names. Returns bags, frozen, chilled, pantry lists and
    one binding per item type (first unit)."""
    p = ctx.placer
    bag_ids = []
    for i in range(int(bags)):
        b = p.on(on, "grocery_bag")
        if b is None:
            b = p.floor_item(root_room(ctx.state(), on), "grocery_bag")
        if b is not None:
            bag_ids.append(b)
    if not bag_ids:
        raise SpecError("food.groceries: no room for a grocery bag on {} or the floor beside it".format(on))
    out = {"bags": bag_ids, "frozen": [], "chilled": [], "pantry": [], "all_groceries": []}
    for i, type_name in enumerate(items):
        props = ctx.reg.resolve(type_name)
        storage = props.get("storage", "pantry")
        temp = frozen_temp_c if storage == "freezer" else chilled_temp_c if storage == "fridge" or props.get("perishable") else 21.0
        extra = dated_props(ctx, type_name, 7 if props.get("perishable") else 180)
        extra.update(temperature_c=temp, _core_c=temp)
        if storage == "freezer":
            f = copy.deepcopy(props.get("food", {}))
            f["ice"] = 1.0
            extra["food"] = f
        bag = bag_ids[i % len(bag_ids)]
        eid = p.inside(bag, type_name, **extra)
        if eid is None:
            for other in bag_ids:
                eid = p.inside(other, type_name, **extra)
                if eid:
                    break
        if eid is None:
            raise SpecError("food.groceries: bags too small for {}".format(type_name))
        group = "frozen" if storage == "freezer" else "chilled" if storage == "fridge" else "pantry"
        out[group].append(eid)
        out["all_groceries"].append(eid)
        out.setdefault(type_name, eid)
    return out


@scenario_helper("food.eaters")
def eaters(ctx, people):
    """people: [{"id": "child_1", "role": "child", "age": 2, "room": "kitchen", "allergies": [...], "diet": [...],
    "dislikes": [...], "orders": {...}}]. Creates or updates residents; returns {id: id}."""
    out = {}
    for spec in people:
        pid = spec["id"]
        rec = ctx.config.setdefault("people", {}).setdefault(pid, {
            "label": spec.get("label", pid.replace("_", " ").title()), "role": spec.get("role", "adult"),
            "authority": 2 if spec.get("role", "adult") == "adult" else 0, "consent": True, "awake": True, "mood": "neutral",
            "permissions": [], "preferences": {}})
        if "role" in spec:
            rec["role"] = spec["role"]
            rec["authority"] = 2 if spec["role"] == "adult" else (1 if spec["role"] in ("elderly", "guest") else 0)
        if "age" in spec:
            rec["age"] = spec["age"]
        for key in ("allergies", "diet"):
            if key in spec:
                rec[key] = list(spec[key])
        prefs = rec.setdefault("preferences", {})
        food_prefs = prefs.setdefault("food", {})
        for key in ("dislikes", "likes", "orders"):
            if key in spec:
                food_prefs[key] = copy.deepcopy(spec[key])
        if "room" in spec:
            room = spec["room"]
            if room not in ctx.config["rooms"]:
                rooms = ctx.placer.rooms(room)
                if not rooms:
                    raise SpecError("food.eaters: no room " + room)
                room = rooms[0]
            rec["location"], rec["pos"] = _agent_cell(ctx, room, pid)
            rec["_placed"] = True        # a deliberately chosen cell: the people domain's episode-start step-aside keeps it
        elif "location" not in rec:
            rec["location"], rec["pos"] = _agent_cell(ctx, ctx.config["start_room"], pid)
        out[pid] = pid
    return out


def _agent_cell(ctx, room, pid):
    """(room, cell) for an eater: a free cell where they block nobody (see _free_agent_cell). Strict first (also off
    every chokepoint of the walkable floor and clear of the robot's work spot), then relaxed. A cramped kitchen may have
    no such cell for a third or fourth person; they then wait in an adjacent room, which is where extra guests stand
    anyway. SpecError (never a dump on cell [0, 0]) when no room nearby has a cell."""
    person = ctx.config["people"][pid]
    for keep_stand, strict in ((True, True), (True, False), (False, False)):
        cell = _free_agent_cell(ctx, room, pid, keep_stand, strict)
        if cell is not None:
            return room, cell
    nearby = sorted(set(r for c in ctx.config.get("connections", {}).values() if room in c.get("rooms", [])
                        for r in c["rooms"] if r != room and not ctx.config["rooms"].get(r, {}).get("outdoor")))
    for strict in (True, False):
        for other in nearby:
            cell = _free_agent_cell(ctx, other, pid, False, strict)
            if cell is not None:
                return other, cell
    cell = ctx.placer.agent_cell(room)
    if cell is None:
        raise SpecError("food.eaters: no free cell for {} in or next to {}".format(person.get("label", pid), room))
    return room, cell


def _side_access(walk, comp, door_comps, cell):
    """True when a 4-neighbour of cell (people step sideways, no corner cutting) is walkable floor joined to a doorway."""
    x, y = cell
    return any(n in walk and comp.get(n) in door_comps for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))


def _free_agent_cell(ctx, room, pid, keep_stand, strict=False):
    """A floor cell for a person that keeps the room's access not worse in the kernel's access model
    (geometry.access_targets / access_report / not_worse: doorways that were joined stay joined, furniture, fixtures,
    mounts, the dock and floor items that were accessible stay accessible) with every other person and pet and the
    robot's work spot counted as targets too; that leaves every agent (the new one included) a side-on free cell joined
    to a doorway, stays out of door clearance, off floor-standing things and hazards, and off the robot's cell. strict
    walks only hazard-free floor, refuses chokepoints of the walkable floor (geometry.articulation_cells) and keeps
    clear of the cells around the work spot: where the people domain's episode-start step-aside would leave anyone."""
    cfg = ctx.config
    person = cfg["people"][pid]
    old = (person.get("location"), person.get("pos"))
    if old[0] == room:
        person["location"] = None          # judge the room without this person's previous cell
    try:
        return _pick_agent_cell(ctx, room, pid, keep_stand, strict)
    finally:
        person["location"], person["pos"] = old
        if old[0] is None:
            person.pop("location", None)
        if old[1] is None:
            person.pop("pos", None)


def _pick_agent_cell(ctx, room, pid, keep_stand, strict):
    state = ctx.state()
    rdef = state["rooms"][room]
    cols, rows = geometry.grid_size(rdef)
    impassable = set(geometry.blocked_cells(state, room, include_agents=True))
    hazards = set(geometry.placement_hazards(state, room))
    if strict:
        impassable |= hazards              # hazard-free walking routes
    used = set(hazards) | set(geometry.doorway_cells(state, room)) | geometry.door_clearance(state, room)
    for eid in geometry.room_entities(state, room):
        if geometry.floor_standing(state["entities"][eid]):
            used.update(geometry.footprint(state["entities"][eid]))
    robot = ctx.config.get("robot") or {}
    if robot.get("room") == room and robot.get("pos"):
        used.add(tuple(robot["pos"]))
    targets = geometry.access_targets(state, room, level="all")
    agents = []
    for group in ("people", "pets"):
        for aid, agent in sorted(state.get(group, {}).items()):
            if aid != pid and agent.get("location") == room and agent.get("pos"):
                targets[(group, aid)] = ([tuple(agent["pos"])], 1, False)
                agents.append(tuple(agent["pos"]))
    stand = ctx.bindings.get("stand") if keep_stand and ctx.bindings.get("kitchen") == room else None
    stand = tuple(stand) if stand else None
    if stand is not None:
        targets[("stand",)] = ([stand], 0, True)
    before = geometry.access_report(state, room, impassable, targets)
    walk = {(x, y) for y in range(rows) for x in range(cols) if (x, y) not in impassable}
    comp = geometry.components(walk)
    door_comps = {comp.get(tuple(c)) for c in geometry.doorway_cells(state, room)} - {None}
    cut = geometry.articulation_cells(walk)
    sole = set()          # the only access cell of something accessible: occupying it always makes access worse
    for key in before["ok"]:
        cells, radius, standable = targets[key]
        if not standable:
            access = [n for n in geometry.near_cells(cells, rdef, radius) if n not in set(cells) and comp.get(n) in door_comps]
            if len(access) == 1:
                sole.add(access[0])
    side_ok = [a for a in agents if _side_access(walk, comp, door_comps, a)]
    reach = sorted(main_region(ctx, room) if ctx.bindings.get("kitchen") == room else reachable_cells(ctx, room))
    for cell in (ctx.rng.sample(reach, len(reach)) if reach else []):
        if cell in used or cell in sole or cell not in walk or (strict and cell in cut):
            continue
        if stand is not None and (cell == stand or (strict and geometry.chebyshev(cell, stand) <= 1)):
            continue      # never on (strict: never beside) the robot's work spot at the counter
        if cell in cut:
            after_walk = walk - {cell}
            after_comp = geometry.components(after_walk)
            after_doors = {after_comp.get(tuple(c)) for c in geometry.doorway_cells(state, room)} - {None}
        else:
            after_walk, after_comp, after_doors = walk - {cell}, comp, door_comps
        if not _side_access(after_walk, after_comp, after_doors, cell):
            continue      # the person must be able to step out (and be approached side-on)
        if not all(_side_access(after_walk, after_comp, after_doors, a) for a in side_ok):
            continue      # nobody already in the room gets boxed in
        if cell in cut and not geometry.not_worse(before, geometry.access_report(state, room, impassable | {cell}, targets)):
            continue      # (a cell that is neither a cut cell nor anyone's sole access cannot make access worse)
        return list(cell)
    return None


def render_recipe(recipe):
    lines = [recipe["name"].upper(), "Serves {}".format(recipe["servings"]), "", "INGREDIENTS"]
    for item in recipe["ingredients"]:
        lines.append("- {} {} {}".format(item.get("text_qty", item["qty"]), item["unit"], item.get("text", item["kind"].replace("_", " "))))
    lines.append("")
    if recipe.get("oven_c"):
        lines.append("Preheat oven to {}".format(recipe.get("oven_text", "{} C".format(recipe["oven_c"]))))
    lines.append("METHOD")
    for i, step in enumerate(recipe["steps"], 1):
        lines.append("{}. {}".format(i, step))
    for note in recipe.get("notes", []):
        lines.append("(margin note: {})".format(note))
    return "\n".join(lines)


@scenario_helper("food.recipe")
def recipe(ctx, on, recipe, var="recipe"):
    """recipe: {"name", "servings", "ingredients": [{"kind","qty","unit","text_qty"?}], "oven_c", "oven_text"?, "steps": [...],
    "notes": [...]}. Spawns a recipe card on a surface with rendered text and hidden structured truth."""
    eid = ctx.placer.on(on, "recipe_card", text=render_recipe(recipe), _recipe=copy.deepcopy(recipe))
    if eid is None:
        raise SpecError("food.recipe: no room for the card")
    return {var: eid}
