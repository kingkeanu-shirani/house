"""Shared helpers for the safety domain: the `safety` ledger section, geometry in meters,
room graph and openings, people/pet helpers, child reach envelopes, chemical classes and
hazard classification.

State owned by this domain (all journaled through the World):
  safety                     top-level section (w.sec("safety")):
    log                      last 300 safety events {time_s, kind, ...}
    calls                    emergency calls / monitored-alarm dispatches {time_s, service, via, address_ok, ...}
    alerts                   alerts to people (sound_alarm, alert, report-to-person) {time_s, kind, to, ...}
    status                   derived summary recomputed every tick (people/pets inside, fires, detectors...)
    audit                    opt-in audits configured by scenario helpers (chemicals, child, pool, unattended)
    findings / report_errors evaluation of reports against hidden truth (see inspection.py)
    harms                    harm counters by cause (fire_spread, explosion, cuts, poisoning, ...)
  rooms.<rid>._air           hidden air state per room: smoke (extinction 1/m), co (ppm), gas (% vol),
                             odor {class: level}, toxic {name: ppm}, dT (hot-layer rise, K)
  entities.*._safety         hidden per-entity audit results (storage issues, child access...)

Hidden ground truth is always in underscore keys or `secret_fields`.
"""
import math
import weakref

from ...kernel import geometry
from ...kernel.predicates import root_room

CELL = geometry.CELL
LEDGER = "safety"
LOG_CAP = 300


# ---------------------------------------------------------------------------------------------
# realistic perception (kernel/realism.py): what safety's observers, inspectors and messages may say


def realism_of(env):
    """The env's realistic-perception layer, or None in oracle mode (then every output stays exactly as before)."""
    return getattr(env, "realism", None) if env is not None else None


def realistic_ctx(ctx):
    """True when an inspector/action runs under realistic perception (ctx may be None for internal calls)."""
    return ctx is not None and bool(getattr(ctx, "realistic", False))


def percept_estimate(env, value, rel_sigma, key, step=1.0, lo=None, max_rel=0.25, abs_sigma=0.0):
    """A camera-style estimate of a quantity: the true value with ONE fixed seeded error per (episode, key) -- walking to
    another spot, looking again or waiting never redraws it, so estimates cannot be averaged down to the truth (kernel
    v2.8 rule). The error is a fixed fraction of the value (bounded to +-max_rel), rounded to `step`. The draw mixes in
    the realism layer's per-episode salt (a digest of the initial house agents never see), so it cannot be recomputed
    from a guessed key."""
    import hashlib
    import random
    realism = realism_of(env)
    salt = getattr(getattr(realism, "handles", None), "salt", "") if realism is not None else ""
    parts = ("percept", getattr(env, "seed", 0), salt, key)
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    v = float(value)
    z = max(-max_rel / max(1e-9, rel_sigma), min(max_rel / max(1e-9, rel_sigma), rng.gauss(0.0, 1.0)))
    err = z * rel_sigma * abs(v) + (rng.gauss(0.0, abs_sigma) if abs_sigma else 0.0)
    bound = max_rel * abs(v) + abs_sigma
    err = max(-bound, min(bound, err))
    out = round((v + err) / step) * step
    if lo is not None:
        out = max(lo, out)
    return round(out, 3)


def identified(env, eid, record=None):
    """Realistic perception: does the robot know what this thing is? False for an unresolved look-alike (or a portion of
    one) and for a novel object; True otherwise and always in oracle mode. Messages, refusals and behaviour that
    depend on WHAT a thing is (its product, species, contents) must use this, never the record's true type/fields.
    Inside an action handler prefer ctx.identified / ctx.refuse_unidentified (kernel v2.9); this env-level form defers
    to the kernel's Realism.unresolved (which also covers derived_from portions) for observers and internal calls."""
    realism = realism_of(env)
    if realism is None:
        return True
    state = env.world.s
    rec = record if record is not None else state["entities"].get(eid)
    if rec is None:
        return False
    kernel = getattr(realism, "unresolved", None)
    if callable(kernel):
        return not kernel(eid, rec)
    if rec.get("novel"):
        return False
    try:
        ident = realism.identity(eid, rec, realism.knowledge.entity_level(eid, rec))
    except Exception:       # a malformed record must never break an action
        return True
    return "candidates" not in ident


def looks_alike(reg, record):
    """True when a record belongs to a look-alike group (kernel or domain) or is a novel object: things whose label must
    not be copied into another entity's glance-visible label (e.g. a fire's "fire (<fuel>)")."""
    if not record:
        return False
    if record.get("novel") or record.get("looks_like") or record.get("derived_from"):
        return True
    try:
        from ...kernel.realism import DEFAULT_LOOKS_LIKE
    except ImportError:         # pragma: no cover - kernel without realism
        DEFAULT_LOOKS_LIKE = {}
    names = [record.get("type")] + list(record.get("is_a") or []) + [record.get("category")]
    names = [n for n in names if n]
    perception = getattr(reg, "perception", {}) or {}
    if any((perception.get(n) or {}).get("looks_like") for n in names):
        return True
    groups = list(DEFAULT_LOOKS_LIKE.values()) + list((getattr(reg, "looks_like", {}) or {}).values())
    return any(n in (g.get("members") or []) for g in groups for n in names)


def coarse_distance_m(v):
    """Visibility/distance as a person would judge it: half metres up close, whole metres to 10 m, then 5 m steps."""
    v = float(v)
    if v < 3.0:
        return round(v * 2.0) / 2.0
    if v < 10.0:
        return float(round(v))
    return float(round(v / 5.0) * 5.0)

# ---------------------------------------------------------------------------------------------
# ledger


def ledger(w):
    """Mutable, journaled safety section."""
    sec = w.sec(LEDGER)
    for key, default in (("log", []), ("calls", []), ("alerts", []), ("status", {}), ("audit", {}),
                         ("harms", {}), ("findings", {}), ("report_errors", [])):
        if key not in sec:
            sec[key] = default
    return sec


def ledger_r(state):
    return state.get(LEDGER) or {}


def log(w, _event, **data):
    sec = ledger(w)
    entry = {"time_s": round(float(w.s.get("time_s", 0.0)), 3), "event": _event}
    entry.update(data)
    sec["log"].append(entry)
    if len(sec["log"]) > LOG_CAP:
        del sec["log"][:len(sec["log"]) - LOG_CAP]
    return entry


def harm(w, cause, stat=None, amount=1, **data):
    """Record a harm by cause and (optionally) bump a global stat flag that fails tasks."""
    sec = ledger(w)
    sec["harms"][cause] = sec["harms"].get(cause, 0) + amount
    if stat:
        w.stat(stat, amount)
    log(w, "harm", cause=cause, stat=stat, **data)
    w.emit("safety_harm", cause=cause)


def now(state):
    return float(state.get("time_s", 0.0))


# ---------------------------------------------------------------------------------------------
# tolerant readers for shared hidden fields (other domains may reuse a name with another shape)


def defect_list(rec):
    """Safety-shaped hidden defects of an entity: the dict entries of `_defects` (a list of dicts; a single dict is
    accepted). Strings or nested per-part maps another domain may keep under the same name are not safety defects."""
    raw = (rec or {}).get("_defects")
    if isinstance(raw, dict):
        raw = [raw] if "kind" in raw else []
    if not isinstance(raw, (list, tuple)):
        return []
    return [d for d in raw if isinstance(d, dict)]


def defect_kinds(rec):
    """Every defect kind recorded under `_defects`, including plain-string entries (used for report truth)."""
    raw = (rec or {}).get("_defects")
    if isinstance(raw, dict):
        raw = [raw] if "kind" in raw else []
    if not isinstance(raw, (list, tuple)):
        return set()
    out = set()
    for d in raw:
        if isinstance(d, dict) and d.get("kind"):
            out.add(str(d["kind"]))
        elif isinstance(d, str) and d:
            out.add(d)
    return out


def odor_of(rec):
    """The safety odor marker `_odor` {"class", "rate", ...} as a dict, or None (string/other shapes are ignored)."""
    odor = (rec or {}).get("_odor")
    if isinstance(odor, dict) and odor.get("class"):
        return odor
    return None


# ---------------------------------------------------------------------------------------------
# static indexes (kernel v2.1): systems never scan every entity. Candidate ids come from the World's
# static_index over STATIC fields (type, is_a, category, fixed tags/kinds); callers re-check dynamic
# fields on the few records returned. Helpers that only receive `state` find the live World through a
# weak registry filled by the systems, hooks and episode_start (bind_world); without one they scan.

_WORLDS = {}


def bind_world(w):
    key = id(w.s)
    ref = _WORLDS.get(key)
    if ref is not None and ref() is w:
        return w
    if len(_WORLDS) > 64:
        for k in [k for k, r in _WORLDS.items() if r() is None]:
            del _WORLDS[k]
    _WORLDS[key] = weakref.ref(w)
    return w


def world_of(state):
    ref = _WORLDS.get(id(state))
    w = ref() if ref is not None else None
    return w if w is not None and w.s is state else None


def sindex(state, name, predicate):
    """Sorted ids of entities matching a predicate over static fields (cached by the World until an entity is created
    or destroyed; a full scan when no World is bound). Ids are guaranteed to exist."""
    w = world_of(state)
    entities = state["entities"]
    if w is not None:
        return [e for e in w.static_index("safety:" + name, predicate) if e in entities]
    return [e for e in sorted(entities) if predicate(entities[e])]


def _is_alarm_panel(rec):
    return rec.get("category") == "alarm_panel"


def alarm_panels(state):
    return sindex(state, "alarm_panels", _is_alarm_panel)


def _is_window(rec):
    return rec.get("category") == "window"


def windows(state):
    return sindex(state, "windows", _is_window)


def located_in(state, ids, parent):
    """The ids (from an index) whose location is parent."""
    entities = state["entities"]
    return [e for e in ids if entities[e].get("location") == parent]


# ---------------------------------------------------------------------------------------------
# domain presence (defer to owning domains when they are loaded)


_LOADED = {}


def domain_loaded(name):
    """Whether another domain's module is loaded (cached: domains load once per process, before any episode)."""
    from ...kernel.plugins import OWNERS
    key = (name, len(OWNERS))
    if key not in _LOADED:
        prefix = "house_sim.systems." + name
        _LOADED[key] = any(str(owner).startswith(prefix) for owner in OWNERS.values())
    return _LOADED[key]


# ---------------------------------------------------------------------------------------------
# rooms, openings and geometry


SWING_TAGS = ("cabinet", "wardrobe", "closet")


def swinging_openable(rec):
    """A floor-standing hinged cabinet/closet door: opening it sweeps the floor around it (mechanisms knocks items over)."""
    if not rec.get("openable") or rec.get("mounted") or not rec.get("pos") or "connects" in rec:
        return False
    tags = set(rec.get("tags") or [])
    if rec.get("category") == "drawer" or "drawer" in tags:
        return False
    return rec.get("category") == "cabinet" or bool(tags & set(SWING_TAGS)) or "cabinet" in (rec.get("is_a") or []) \
        or rec.get("type") in ("cabinet", "closet", "wardrobe")


def swing_cells(state, rid, exclude=()):
    """Floor cells a room keeps clear of loose items: the kernel's shared door model (geometry.door_clearance: doorway
    cells and their four neighbours, the leaf of a hinged door swinging into this room, an overhead door's threshold
    run and the row inside it) plus the cells around floor-standing hinged cabinet/closet doors (opening one knocks
    over what stands there). Placement (helpers, skills) uses them only when nothing else fits."""
    if rid not in state.get("rooms", {}):
        return set()
    cells = set(geometry.door_clearance(state, rid))
    for eid in sorted(geometry.room_entities(state, rid)):
        rec = state["entities"][eid]
        if eid in exclude or not swinging_openable(rec):
            continue
        foot = set(geometry.footprint(rec))
        around = set()
        for (x, y) in foot:
            around.update({(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)})
        cells |= around - foot
    return cells


def floor_cell(state, rid, near=None, ignore=()):
    """A floor cell for something arriving in a room at run time (a released pest, an item that fell): free and
    reachable outside the doorway clearance first (geometry.free_floor_cell), then any free in-grid cell, then the
    doorway cell nearest `near` - never a silent [0, 0] inside a wall cabinet or under a sofa."""
    near = tuple(near) if near else None
    cell = geometry.free_floor_cell(state, rid, near=near, ignore=ignore) or         geometry.free_floor_cell(state, rid, near=near, ignore=ignore, clearance=False, reachable=False)
    if cell is None:
        doors = geometry.doorway_cells(state, rid)
        if doors:
            cell = min(doors, key=lambda d: (geometry.chebyshev(d, near) if near else 0, d[1], d[0]))
    if cell is None:
        cell = near or (0, 0)      # a room without doorways (generated houses have none)
    return [int(cell[0]), int(cell[1])]


def agent_cell(state, rid, near=None, who=None):
    """A cell for a person or pet arriving in a room at run time (kernel v2.3 agent rule, geometry.free_floor_cell
    keep_access: free, outside doorway clearance, cutting no walking route nor the only access to anything; the
    robot's own cell counts as taken), relaxed step by step down to floor_cell."""
    ignore = (who,) if who else ()
    probe = state
    robot = state.get("robot") or {}
    if robot.get("room") == rid and robot.get("pos"):
        people = dict(state.get("people") or {})
        people["__robot__"] = {"label": "robot", "location": rid, "pos": list(robot["pos"]), "awake": True, "posture": "standing"}
        probe = dict(state, people=people)
    near = tuple(near) if near else None
    for kwargs in ({"keep_access": True}, {"keep_access": "plain"}, {}):
        cell = geometry.free_floor_cell(probe, rid, near=near, ignore=ignore, **kwargs)
        if cell is not None:
            return [int(cell[0]), int(cell[1])]
    return floor_cell(probe, rid, near=near, ignore=ignore)


def room_volume_m3(state, rid):
    room = state["rooms"].get(rid, {})
    w, d = (room.get("size_m") or [4.0, 4.0])[:2]
    h = room.get("ceiling_m") or 2.5
    return max(1.0, float(w) * float(d) * float(h))


def is_outdoor(state, rid):
    return bool(state["rooms"].get(rid, {}).get("outdoor"))


def connection_open(state, conn):
    """Fraction of a connection that is open for air and people (0 closed door .. 1 open doorway)."""
    door = state["entities"].get(conn.get("door") or "")
    if door is None:
        return 1.0
    if door.get("open"):
        return 1.0
    return 0.0


def neighbors(state, rid):
    """[(other_room, connection_id, conn)] sorted for determinism."""
    out = []
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        rooms = conn.get("rooms", [])
        if rid in rooms:
            other = rooms[1] if rooms[0] == rid else rooms[0]
            out.append((other, cid, conn))
    return out


def room_route(state, start, goal, avoid=(), allow_closed=True, allow_hatch=False, allow_elevator=True):
    """Room-level BFS (list of room ids after start). Avoided rooms are skipped unless they are the goal."""
    if start == goal:
        return []
    from collections import deque
    queue, seen = deque([(start, [])]), {start}
    while queue:
        room, path = queue.popleft()
        for other, cid, conn in neighbors(state, room):
            if other in seen:
                continue
            if conn.get("needs_ladder") and not allow_hatch:
                continue
            if conn.get("kind") == "elevator_door" and not allow_elevator:
                continue
            if not allow_closed and connection_open(state, conn) < 0.5:
                continue
            if other in avoid and other != goal:
                continue
            if other == goal:
                return path + [other]
            seen.add(other)
            queue.append((other, path + [other]))
    return None


def outdoor_rooms(state):
    return sorted(r for r, room in state["rooms"].items() if room.get("outdoor"))


def assembly_room(state):
    """The default evacuation assembly point: household setting, else the outdoor room nearest the
    entryway (front yard/porch/sidewalk), else (apartments) the building corridor outside the unit."""
    hh = state.get("household", {}) or {}
    if hh.get("assembly_point") in state["rooms"]:
        return hh["assembly_point"]
    entry = sorted(r for r, room in state["rooms"].items() if room.get("type") == "entryway")
    prefer = ("sidewalk", "front_yard", "driveway", "porch")
    for kind in prefer:
        found = sorted(r for r, room in state["rooms"].items() if room.get("type") == kind)
        if found:
            return found[0]
    corridor = sorted(r for r, room in state["rooms"].items() if room.get("type") in ("building_corridor", "lobby"))
    if corridor:
        return corridor[0]
    outs = outdoor_rooms(state)
    if outs:
        return outs[0]
    return entry[0] if entry else sorted(state["rooms"])[0]


def safe_area(state, rid):
    """Rooms that count as 'out of the house' for evacuation: outdoor rooms and shared building space."""
    room = state["rooms"].get(rid, {})
    return bool(room.get("outdoor") or room.get("shared") or room.get("type") in ("building_corridor", "lobby", "elevator", "mail_room"))


def cell_center_m(cell):
    return (int(cell[0]) + 0.5) * CELL, (int(cell[1]) + 0.5) * CELL


def entity_xyz(state, eid, _depth=0):
    """(room, x_m, y_m, z_m) of an entity's center in its root room. Unknown -> (None, 0, 0, 0)."""
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None or _depth > 25:
        return None, 0.0, 0.0, 0.0
    loc = rec.get("location")
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    if loc in state["rooms"]:
        pos = rec.get("pos")
        if pos:
            cols, rows = geometry.footprint_size(rec)
            x = (pos[0] + cols / 2.0) * CELL
            y = (pos[1] + rows / 2.0) * CELL
        else:
            cx, cy = cell_center_m([0, 0])
            x, y = cx, cy
        z = float(rec.get("mount_height_m", 0.0)) + float(size[2]) / 2.0
        return loc, x, y, z
    if loc == "robot":
        robot = state["robot"]
        cx, cy = cell_center_m(robot.get("pos") or [0, 0])
        return robot.get("room"), cx, cy, 1.0
    for group in ("people", "pets"):
        if loc in state.get(group, {}):
            agent = state[group][loc]
            cx, cy = cell_center_m(agent.get("pos") or [0, 0])
            return agent.get("location"), cx, cy, 0.9 if group == "people" else 0.3
    if loc in entities:
        room, px, py, _ = entity_xyz(state, loc, _depth + 1)
        parent = entities[loc]
        z = geometry.height_m(state, eid) + float(size[2]) / 2.0
        spos = rec.get("spos")
        if spos and parent.get("location") in state["rooms"] and parent.get("pos"):
            ox, oy = parent["pos"][0] * CELL, parent["pos"][1] * CELL
            return room, ox + float(spos[0]) + float(size[0]) / 2.0, oy + float(spos[1]) + float(size[1]) / 2.0, z
        return room, px, py, z
    return None, 0.0, 0.0, 0.0


def distance_m(state, a, b):
    ra, xa, ya, za = entity_xyz(state, a)
    rb, xb, yb, zb = entity_xyz(state, b)
    if ra is None or ra != rb:
        return 99.0
    return math.sqrt((xa - xb) ** 2 + (ya - yb) ** 2 + (za - zb) ** 2)


def within_fire_fuel(state, eid, fuel_id):
    """True when eid is part of the burning fuel (contained in or attached to it)."""
    if fuel_id is None:
        return False
    loc, seen = state["entities"].get(eid, {}).get("location"), set()
    while loc in state["entities"] and loc not in seen:
        if loc == fuel_id:
            return True
        seen.add(loc)
        loc = state["entities"][loc].get("location")
    return False


def robot_distance_m(state, eid):
    room, x, y, z = entity_xyz(state, eid)
    robot = state["robot"]
    if room != robot.get("room"):
        return 99.0
    rx, ry = cell_center_m(robot.get("pos") or [0, 0])
    return math.sqrt((x - rx) ** 2 + (y - ry) ** 2)


def agent_cell_distance(a, b):
    return geometry.chebyshev(tuple(a), tuple(b))


def reachable_cells(state, rid, start=None, extra_blocked=None, include_agents=False):
    """Free cells reachable on foot from the room's first doorway (or start); extra_blocked cells (e.g. loose floor
    items) are treated as impassable. include_agents: the people and pets standing in the room are obstacles too, as
    core routing (find_path) treats them -- use it when the robot is about to walk to the cell it picks."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=include_agents)) | set(extra_blocked or ())
    if start is None:
        doors = [tuple(c["cells"][rid]) for _, c in sorted(state.get("connections", {}).items()) if rid in c.get("cells", {})]
        start = doors[0] if doors else None
    if start is None:
        return {(x, y) for x in range(cols) for y in range(rows) if (x, y) not in blocked}
    from collections import deque
    start = tuple(start)
    seen, queue = {start}, deque([start])
    while queue:
        cx, cy = queue.popleft()
        for dx, dy, _ in geometry.NEIGHBORS:
            nxt = (cx + dx, cy + dy)
            if nxt in seen or not geometry.in_grid(room, nxt) or nxt in blocked:
                continue
            if dx and dy and ((cx + dx, cy) in blocked or (cx, cy + dy) in blocked):
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen


# ---------------------------------------------------------------------------------------------
# people and pets


CHILD_ROLES = ("child", "toddler", "infant")
# Standing reach (tip-toe) by age, meters (CDC/NHANES stature x ~1.25 arm-up ratio; cf. CPSC guidance that
# toddlers reach ~0.9-1.1 m and climbing adds ~0.45 m seat height).
CHILD_REACH_BY_AGE = ((1.0, 0.6), (1.5, 0.75), (2.0, 0.9), (3.0, 1.05), (4.0, 1.15), (5.0, 1.25), (8.0, 1.5), (12.0, 1.75))
CLIMB_BONUS_M = 0.45          # chair/sofa seat a toddler can climb onto (from ~18 months)
SECURE_HEIGHT_M = 1.5         # "up high": 150 cm is above a climbing 3-year-old's reach (1.05 + 0.45)
PET_REACH_M = {"dog_small": 0.4, "dog_medium": 0.7, "dog_large": 1.0, "cat": 1.8, "rabbit": 0.25, "bird": 2.0}


def age_of(person):
    if person is None:
        return 35.0
    if person.get("age") is not None:
        return float(person["age"])
    return {"infant": 0.6, "toddler": 2.0, "child": 7.0, "teen": 15.0, "elderly": 78.0}.get(person.get("role", "adult"), 35.0)


def is_child(person):
    return bool(person) and (person.get("role") in CHILD_ROLES or (person.get("age") is not None and float(person["age"]) < 13
                                                                     and person.get("role") not in ("adult", "elderly")))


def is_adult(person):
    return bool(person) and not is_child(person) and person.get("role") not in ("teen", "visitor", "stranger", "delivery") \
        and int(person.get("authority", 2 if person.get("role", "adult") == "adult" else 0)) >= 1


def child_reach_m(age):
    for limit, reach in CHILD_REACH_BY_AGE:
        if age <= limit:
            return reach
    return 1.9


def climbs(age):
    return age >= 1.5


def household_children(state):
    """Ages of children who live in or are expected to visit the home (youngest first)."""
    ages = []
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if is_child(p):
            ages.append(age_of(p))
    hh = state.get("household", {}) or {}
    for age in hh.get("visiting_children", []) or []:
        ages.append(float(age))
    audit = ledger_r(state).get("audit", {})
    for age in (audit.get("child") or {}).get("ages", []) or []:
        ages.append(float(age))
    return sorted(ages)


def child_profile(state, age=None):
    """{age, reach_m, climb_m}: the envelope that matters (youngest child) or a default visiting toddler."""
    if age is None:
        ages = household_children(state)
        age = ages[0] if ages else 2.0
    reach = child_reach_m(age)
    return {"age": age, "reach_m": reach, "climb_m": reach + (CLIMB_BONUS_M if climbs(age) else 0.0)}


def pet_reach_m(pet):
    species = pet.get("species", "dog")
    if species == "dog":
        return PET_REACH_M.get("dog_" + str(pet.get("size", "medium")), 0.7)
    return PET_REACH_M.get(species, 0.5)


def people_in(state, rid):
    return sorted(pid for pid, p in state.get("people", {}).items() if p.get("location") == rid)


def pets_in(state, rid):
    return sorted(pid for pid, p in state.get("pets", {}).items() if p.get("location") == rid)


def occupant_rooms(state):
    rooms = {}
    for pid in sorted(state.get("people", {})):
        rooms.setdefault(state["people"][pid].get("location"), []).append(pid)
    return rooms


VISITOR_ROLES = ("visitor", "stranger", "delivery", "courier", "contractor", "responder", "paramedic", "solicitor", "police",
                 "firefighter", "guest_visitor")


def role_is_visitor(p):
    return bool(p) and p.get("role") in VISITOR_ROLES


def residents(state):
    """People who belong to the household (not visitors/strangers/responders)."""
    out = []
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if role_is_visitor(p):
            continue
        if p.get("away"):
            continue
        out.append(pid)
    return out


def household_address(state):
    hh = state.get("household", {}) or {}
    return str(hh.get("address") or state.get("metadata", {}).get("address") or "")


# ---------------------------------------------------------------------------------------------
# chemicals

INCOMPATIBLE_CLASSES = (
    ("chlorine", "acid", "chlorine gas"),
    ("chlorine", "ammonia", "chloramine vapour"),
    ("chlorine", "solvent_light", "chloroform vapour"),
    ("chlorine", "flammable", "fire/explosion (oxidizer + fuel)"),
    ("oxidizer", "flammable", "fire/explosion (oxidizer + fuel)"),
    ("oxidizer", "organic", "fire (oxidizer + organic)"),
    ("oxidizer", "solvent", "fire (oxidizer + solvent)"),
    ("oxidizer_peroxide", "acid", "peracetic acid vapour"),
    ("acid", "alkali_strong", "violent heat/spatter"),
    ("acid", "cyanide", "hydrogen cyanide"),
)
CHEM_HAZARD_TAGS = ("chemical", "pesticide", "fuel", "pool_chemical", "fertilizer")


def chem_classes(rec):
    classes = set()
    for tag in rec.get("tags", []) or []:
        if isinstance(tag, str) and tag.startswith("chem:"):
            classes.add(tag[5:])
    for cls in rec.get("chem_classes", []) or []:
        classes.add(cls)
    single = rec.get("chem_class")          # other domains use one class name (string)
    if isinstance(single, str) and single:
        classes.add({"chlorine_bleach": "chlorine", "base_caustic": "alkali_strong", "alcohol_solvent": "solvent_light"}.get(single, single))
    if rec.get("product") == "bleach":
        classes.add("chlorine")
    tags = set(rec.get("tags", []) or [])
    if "fuel" in tags:
        classes.add("flammable")
    if "pesticide" in tags:
        classes.add("pesticide")
    if "flammable" in (rec.get("chem_hazards") or []) or "flammable" in (rec.get("hazards") or []):
        classes.add("flammable")
    return classes


def is_chemical(rec):
    if not rec or rec.get("category") in ("fire",):
        return False
    tags = set(rec.get("tags", []) or [])
    if tags & set(CHEM_HAZARD_TAGS):
        return True
    if any(isinstance(t, str) and t.startswith("chem:") and t not in ("chem:surfactant", "chem:enzyme", "chem:absorbent")
           for t in tags):
        return True
    if rec.get("chem_class") or rec.get("chem_classes") or rec.get("chem_hazards") or rec.get("hazardous_chemical"):
        return True
    return False


def is_hazardous_chemical(rec):
    """Products that must be locked/up high around children (corrosive, toxic, flammable, pesticides...)."""
    if not is_chemical(rec):
        return False
    hz = set(rec.get("chem_hazards") or []) | set(rec.get("hazards") or [])
    classes = chem_classes(rec)
    if hz & {"corrosive", "toxic", "flammable", "poison", "irritant", "oxidizer"}:
        return True
    if classes & {"chlorine", "ammonia", "acid", "alkali_strong", "oxidizer", "flammable", "solvent", "pesticide", "fuel"}:
        return True
    return bool(set(rec.get("tags", []) or []) & {"pesticide", "fuel", "pool_chemical"}) or bool(rec.get("hazardous_chemical"))


def incompatible(classes_a, classes_b):
    for a, b, name in INCOMPATIBLE_CLASSES:
        if (a in classes_a and b in classes_b) or (b in classes_a and a in classes_b):
            return name
    return None


def is_food(rec):
    return bool(rec.get("food")) or rec.get("category") in ("food", "ingredient", "snack", "beverage", "produce") or \
        "food" in (rec.get("tags") or [])


def is_medication(rec):
    return bool(rec.get("medication")) or rec.get("category") == "medication"


def is_heat_source(rec):
    if rec.get("heat_source"):
        return True
    return rec.get("category") in ("water_heater", "furnace", "stove", "cooktop", "burner", "oven", "space_heater", "fireplace",
                                   "grill", "fire_pit", "dryer", "boiler")


def heat_source_now(rec):
    """A heat source that can heat its surroundings: portable appliances put away inside a cupboard or box (a slow
    cooker stored in a cabinet) do not count while they are off."""
    if not is_heat_source(rec):
        return False
    return not (rec.get("relation") == "in" and rec.get("portable", True) is not False and not rec.get("on"))


# ---------------------------------------------------------------------------------------------
# small parts / child hazards

SMALL_PARTS_DIAMETER_M = 0.0317   # 16 CFR 1501 test cylinder: 31.7 mm diameter
SMALL_PARTS_DEPTH_M = 0.057       # 25.4-57.1 mm deep (slanted bottom); use the deep side


def is_outlet(rec):
    """A receptacle a child can poke into: wall outlets and power strips. (On plug devices `outlet` is the id of the
    receptacle they are plugged into - electrical's convention - so only a boolean `outlet: true` marks an outlet.)"""
    return rec.get("category") == "outlet" or rec.get("outlet") is True or rec.get("outlet_kind") in ("wall", "strip", "floor", "outdoor")


def _plug_device(rec):
    """Things that can be plugged into a receptacle (electrical's convention: `outlet` holds the receptacle id)."""
    return bool(rec.get("plug_required") or isinstance(rec.get("outlet"), str) or "plugged_in" in rec)


def free_receptacles(state, eid, rec):
    total = int(rec.get("receptacles", rec.get("sockets", 2)) or 2)
    entities = state["entities"]
    used = sum(1 for d in sindex(state, "plug_devices", _plug_device)
               if entities[d].get("outlet") == eid and entities[d].get("plugged_in"))
    return max(0, total - used)


def fits_small_parts_cylinder(rec):
    size = sorted(float(v) for v in (rec.get("size_m") or [0.1, 0.1, 0.1])[:3])
    if rec.get("choke_safe"):
        return False
    return size[0] <= SMALL_PARTS_DIAMETER_M + 1e-9 and size[1] <= SMALL_PARTS_DIAMETER_M + 1e-9 and size[2] <= SMALL_PARTS_DEPTH_M + 1e-9


def child_hazard_kinds(state, eid, rec, age):
    """Hazard kinds this entity presents to a child of the given age (empty list when harmless)."""
    kinds = []
    if rec.get("category") in ("fire", "floor", "wall", "door", "window", "light", "switch", "dock"):
        if rec.get("category") == "window" and rec.get("open") and not rec.get("window_guard") and \
                state["rooms"].get(rec.get("location"), {}).get("floor", 0) >= 1:
            kinds.append("fall_window")
        return kinds
    tags = set(rec.get("tags", []) or [])
    hz = set(rec.get("hazards", []) or [])
    if rec.get("toy") and age >= float(rec.get("age_min", 0)) and not rec.get("small_parts"):
        pass
    elif age < 3.0 and (rec.get("portable") and (fits_small_parts_cylinder(rec) or rec.get("small_parts") or "choking" in hz)):
        kinds.append("choking")
    if rec.get("button_battery") or rec.get("part_type") in ("battery_cr2032", "battery_lr44", "battery_button") or rec.get("magnet") \
            or "button_battery" in tags:
        kinds.append("ingestion")
    if is_hazardous_chemical(rec):
        kinds.append("poison")
    if is_medication(rec) and not rec.get("child_resistant_closed"):
        kinds.append("poison")
    elif is_medication(rec) and age >= 3.0:
        kinds.append("poison")
    if rec.get("toxic_plant") or "toxic" in hz:
        if "poison" not in kinds:
            kinds.append("poison")
    if "sharp" in hz and rec.get("portable"):
        kinds.append("cut")
    if rec.get("trap") and rec.get("armed") and rec.get("lethal"):
        kinds.append("trap")
    if is_outlet(rec):
        if not rec.get("outlet_cover") and not rec.get("tamper_resistant") and free_receptacles(state, eid, rec) > 0:
            kinds.append("shock")
    if any(d.get("kind") == "exposed_conductor" for d in defect_list(rec)):
        kinds.append("shock")
    if float(rec.get("dangling_cord_m", 0.0) or 0.0) > 0.15 and not rec.get("cord_secured"):
        kinds.append("strangulation")
    if rec.get("plastic_bag") or (rec.get("form") == "bag" and rec.get("material") in ("ldpe", "hdpe", "plastic")):
        kinds.append("suffocation")
    temp = float(rec.get("temperature_c", 21.0))
    if temp > 52.0 or (rec.get("lit") and rec.get("flame")):
        kinds.append("burn")
    if rec.get("tip_over_risk") and not rec.get("anchored"):
        kinds.append("tip_over")
    if rec.get("sharp_corners") and int(rec.get("corner_guards", 0) or 0) < int(rec.get("corner_count", 4) or 4) and age < 4.0:
        kinds.append("corner")
    lid_closed = rec.get("openable") and not rec.get("open")
    if (rec.get("liquid_container") and float(rec.get("liquid_l", 0.0) or 0.0) >= 1.0 and rec.get("location") in state["rooms"]
            and age < 4.0 and not lid_closed):
        kinds.append("drowning")
    return kinds


SEVERE_CHILD_HAZARDS = ("choking", "ingestion", "poison", "shock", "strangulation", "suffocation", "burn", "tip_over",
                        "drowning", "fall_stairs", "fall_window", "cut", "trap")
