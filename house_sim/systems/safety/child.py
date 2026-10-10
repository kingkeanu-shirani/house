"""Child hazards (fidelity G): reach/climb envelopes by age, rooms a child can get into, hazard classification, and a
deterministic curious-toddler agent that contacts every hazard it can reach.

Reach (tip-toe, no climbing): 1 y 0.6 m, 2 y 0.9 m, 3 y 1.05 m, 4 y 1.15 m, 5 y 1.25 m, 8 y 1.5 m; from 18 months a
child climbs chairs/sofas/beds/open drawers (+0.45 m) when the room has something climbable.
Rooms a child can enter: through open doorways/doors; a closed door with a round knob stops under-3s (levers do not);
a closed, latched safety gate stops everyone small. Ungated stairs from those rooms are a fall hazard (under 3).
Hazard kinds (common.child_hazard_kinds): choking (fits the 31.7 mm small-parts cylinder, under 3), ingestion (button
batteries, magnets), poison (hazardous chemicals, medicines without a closed child-resistant cap, toxic plants), cut,
shock (uncovered outlets, exposed conductors), strangulation (blind cords > 15 cm), suffocation (plastic bags), burn
(> 52 C, lit candles), tip_over (unanchored tall furniture), corner (sharp corners, minor), drowning (open containers
of water >= 1 l on the floor, under 4), fall_stairs, fall_window (open upper-floor window without a stop).

Audit (opt-in safety.audit.child = {"rooms": [...], "age": a}) -> safety.status.child_hazards {room: n} and
child_hazard_list [{"id", "kinds"}] (ground truth for evaluation). With "fragile": true the audit also lists breakables
(fragile flag, or portable glass/ceramic/porcelain/crystal) inside the reach/climb envelope in status.fragile_in_reach.
Explorer (opt-in per person: people.<pid>._explore = {"every_s": 45, "until_s": t, "rooms": "reachable"|"own"}):
every interval the child goes to the nearest unexplored thing it can reach and handles it; a hazardous contact is a
harm (injury/burns/electrical_hazard/falls) logged in safety.child_contacts. With "fragile": true it also handles
breakables in reach and knocks them down: glass/ceramic shatters into a shard field at the child's feet (damage), other
fragile items break (damage).
"""
from ...kernel import geometry
from ...kernel.plugins import system
from . import chemicals as CH
from . import common as C

FRAGILE_MATERIALS = ("glass", "ceramic", "porcelain", "crystal", "stoneware", "earthenware")


def is_fragile(rec):
    if rec.get("broken") or rec.get("shattered") or rec.get("category") in ("window", "fire", "mirror") or rec.get("mounted"):
        return False
    if rec.get("fragile"):
        return True
    return bool(rec.get("portable")) and str(rec.get("material", "")).lower() in FRAGILE_MATERIALS


HARM_STATS = {"choking": "injury", "ingestion": "injury", "poison": "injury", "cut": "injury", "shock": "electrical_hazard",
              "strangulation": "injury", "suffocation": "injury", "burn": "burns", "tip_over": "injury", "drowning": "injury",
              "fall_stairs": "injury", "fall_window": "injury", "corner": None, "trap": "injury"}


def child_rooms(state, start, age):
    """Rooms a child of this age can reach from start, and the stairs connections it can reach ungated."""
    from collections import deque
    seen, queue, stairs = {start}, deque([start]), []
    while queue:
        rid = queue.popleft()
        for other, cid, conn in C.neighbors(state, rid):
            door = state["entities"].get(conn.get("door") or "")
            passable = True
            if door is not None and not door.get("open"):
                if door.get("category") in ("safety_gate",) or door.get("child_safety") or door.get("device_kind") == "stair_gate":
                    passable = False
                elif door.get("locked") or door.get("latched"):
                    passable = False
                elif age < 3.0 and door.get("handle_kind", "lever") in ("knob", "pull_cord", "opener", "sliding"):
                    passable = False
                elif door.get("knob_cover"):
                    passable = False
            if conn.get("kind") == "stairs":
                if passable and not _stairs_gated_protective(state, cid, rid):
                    stairs.append((cid, rid))
                continue
            if conn.get("kind") in ("hatch", "elevator_door"):
                continue
            if passable and other not in seen:
                seen.add(other)
                queue.append(other)
    return seen, stairs


def _stairs_gated_protective(state, cid, from_room):
    conn = state["connections"][cid]
    gate = state["entities"].get(conn.get("door") or "")
    if gate is None or gate.get("open"):
        return False
    if gate.get("mount") == "pressure":
        # a pressure gate at the TOP of the stairs can be pushed out
        other = conn["rooms"][1] if conn["rooms"][0] == from_room else conn["rooms"][0]
        if state["rooms"][from_room].get("floor", 0) > state["rooms"][other].get("floor", 0):
            return False
    return True


_WALK_CACHE = {}


def _blocker(rec):
    return bool(rec.get("blocks_path"))


def _walkable(state, rid):
    """Reachable floor of a room, cached on its furniture layout (blocking pieces and where they stand) so the per-tick
    child audit does not redo the flood fill while nothing moved."""
    entities = state["entities"]
    layout = tuple((e, tuple(entities[e].get("pos") or ()), entities[e].get("rot"), tuple(entities[e].get("size_m") or ()))
                   for e in C.sindex(state, "blockers", _blocker) if entities[e].get("location") == rid and entities[e].get("blocks_path"))
    doors = tuple(tuple(c["cells"][rid]) for _, c in sorted(state.get("connections", {}).items()) if rid in c.get("cells", {}))
    layout = (tuple(state["rooms"][rid].get("size_m") or ()), doors, layout)
    key = (id(state), rid)
    hit = _WALK_CACHE.get(key)
    if hit is not None and hit[0] == layout:
        return hit[1]
    if len(_WALK_CACHE) > 256:
        _WALK_CACHE.clear()
    reach = C.reachable_cells(state, rid)
    _WALK_CACHE[key] = (layout, reach)
    return reach


def _wall_spot_reachable(state, rid, rec, cache):
    """A wall fixture (outlet) is within a child's reach unless heavy furniture walls it off: its cell or a neighbouring
    one must be open floor the child can walk to (an outlet boxed in behind a sofa is not)."""
    pos = rec.get("pos")
    if rec.get("location") != rid or not pos:
        return True
    key = ("walk", rid)
    if key not in cache:
        cache[key] = _walkable(state, rid)
    reach = cache[key]
    x, y = int(pos[0]), int(pos[1])
    return (x, y) in reach or any((x + dx, y + dy) in reach for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))


HOT_CATEGORIES = ("iron", "kettle", "space_heater", "curling_iron", "hair_straightener", "candle", "toaster", "oven", "stove",
                  "cooktop", "burner", "griddle", "hot_plate", "radiator", "fireplace", "mug", "cup")


def _child_candidate(rec):
    """Static superset of what child_hazard_kinds/is_fragile can flag (dynamic state is re-checked every audit)."""
    if rec.get("category") in ("floor", "wall", "door", "light", "switch", "dock"):
        return False
    if rec.get("category") in ("window", "fire") or rec.get("portable") or rec.get("fragile"):
        return True        # small parts, chemicals, medicine, sharps, bags, breakables, hot mugs, traps are all portable
    return bool(C.is_outlet(rec) or rec.get("_defects") or "dangling_cord_m" in rec or rec.get("tip_over_risk")
                or rec.get("sharp_corners") or rec.get("liquid_container") or rec.get("trap") or C.is_chemical(rec)
                or C.is_medication(rec) or rec.get("toxic_plant") or set(rec.get("hazards") or []) & {"sharp", "toxic", "hot"}
                or rec.get("heat_source") or rec.get("heat_w") or rec.get("conducts_heat") or "lit" in rec
                or rec.get("category") in HOT_CATEGORIES or float(rec.get("temperature_c", 21.0) or 21.0) > 52.0)


def room_hazards(state, rid, profile, climb_cache=None, fragile=False):
    """[(eid, kinds)] reachable hazards for a child in room rid (entities located in the room tree)."""
    out = []
    climb_cache = {} if climb_cache is None else climb_cache
    for eid in C.sindex(state, "child_candidates", _child_candidate):
        rec = state["entities"][eid]
        if C.root_room(state, eid) != rid:
            continue
        kinds = C.child_hazard_kinds(state, eid, rec, profile["age"])
        if fragile and is_fragile(rec):
            kinds = list(kinds) + ["fragile"]
        if not kinds:
            continue
        if rec.get("category") == "window":
            out.append((eid, kinds))
            continue
        if C.is_outlet(rec):
            h = float(rec.get("mount_height_m", 0.3))
            if h <= profile["climb_m"] and _wall_spot_reachable(state, rid, rec, climb_cache):
                out.append((eid, kinds))
            continue
        if rec.get("tip_over_risk") and rec.get("location") in state["rooms"]:
            out.append((eid, kinds))
            continue
        if rec.get("sharp_corners") and rec.get("location") in state["rooms"]:
            out.append((eid, kinds))
            continue
        if float(rec.get("dangling_cord_m", 0.0) or 0.0) > 0.15 and not rec.get("cord_secured"):
            bottom = float(rec.get("cord_bottom_m", 0.6))
            if bottom <= profile["climb_m"]:
                out.append((eid, kinds))
            continue
        if CH.child_can_reach(state, eid, profile, climb_cache):
            out.append((eid, kinds))
    return out


def audit(w):
    state = w.s
    cfg = (C.ledger_r(state).get("audit") or {}).get("child")
    if not cfg:
        return
    profile = C.child_profile(state, cfg.get("age"))
    rooms = cfg.get("rooms") or []
    counts, listing, breakables = {}, [], []
    cache = {}
    for rid in rooms:
        if rid not in state["rooms"]:
            continue
        everything = room_hazards(state, rid, profile, cache, fragile=bool(cfg.get("fragile")))
        breakables.extend(e for e, k in everything if "fragile" in k)
        found = [(e, k) for e, k in everything if any(x in C.SEVERE_CHILD_HAZARDS for x in k) or cfg.get("include_minor")]
        reach, stairs = child_rooms(state, rid, profile["age"])
        if profile["age"] < 3.0:
            for cid, from_room in stairs:
                if from_room == rid:      # this room opens straight onto ungated stairs
                    found.append((cid, ["fall_stairs"]))
        counts[rid] = len(found)
        listing.extend({"id": e, "kinds": list(k), "room": rid} for e, k in found)
    status = C.ledger(w)["status"]
    if cfg.get("fragile") and status.get("fragile_in_reach") != sorted(breakables):
        status["fragile_in_reach"] = sorted(breakables)
    total = sum(counts.values())
    if status.get("child_hazards") != counts or status.get("child_hazard_list") != listing:
        status["child_hazards"] = counts
        status["child_hazard_list"] = listing
        status["child_hazard_total"] = total


# ---------------------------------------------------------------------------------------------
# curious toddler


def _target_cell(state, eid, rid):
    if eid in state.get("connections", {}):
        cell = state["connections"][eid].get("cells", {}).get(rid)
        return tuple(cell) if cell else None
    room, cells = geometry.anchor(state, eid)
    if room != rid or not cells:
        return None
    return tuple(cells[0])


def _explore_step(w, pid, cfg):
    state = w.s
    p = state["people"][pid]
    rid = p.get("location")
    if rid not in state["rooms"]:
        return
    profile = C.child_profile(state, C.age_of(p))
    explored = list(cfg.get("explored", []))
    candidates = []
    pos = tuple(p.get("pos") or (0, 0))
    for eid, kinds in room_hazards(state, rid, profile, fragile=bool(cfg.get("fragile"))):
        if eid in explored:
            continue
        cell = _target_cell(state, eid, rid)
        candidates.append((geometry.chebyshev(pos, cell) if cell else 9, eid, kinds, cell))
    if profile["age"] < 3.0:
        _, stairs = child_rooms(state, rid, profile["age"])
        for cid, from_room in stairs:
            if from_room == rid and cid not in explored:
                cell = _target_cell(state, cid, rid)
                candidates.append((geometry.chebyshev(pos, cell) if cell else 9, cid, ["fall_stairs"], cell))
    if not candidates:
        return
    candidates.sort(key=lambda t: (t[0], t[1]))
    dist, eid, kinds, cell = candidates[0]
    m = w.person_m(pid)
    ex = dict(m.get("_explore") or {})
    ex["explored"] = explored + [eid]
    m["_explore"] = ex
    if cell is not None:
        near = geometry.free_floor_cell(state, rid, near=cell)
        if near:
            m["pos"] = list(near)
    severe = [k for k in kinds if HARM_STATS.get(k)]
    sec = C.ledger(w)
    contacts = sec.setdefault("child_contacts", [])
    contacts.append({"time_s": C.now(state), "child": pid, "item": eid, "kinds": list(kinds)})
    del contacts[:-100]
    for kind in severe:
        C.harm(w, "child_" + kind, stat=HARM_STATS[kind], child=pid, item=eid)
        if kind.startswith("fall_"):
            w.stat("falls")
        if kind == "tip_over" and eid in state["entities"]:
            fm = w.mut(eid)
            fm["orientation"] = "tipped"
            fm["tipped_over"] = True
        break
    if not severe and "fragile" in kinds and eid in state["entities"]:
        knock_down(w, eid, pid)


def knock_down(w, eid, pid):
    """A child grabs/bumps a breakable within reach: it falls and breaks."""
    state = w.s
    rec = state["entities"][eid]
    child = state["people"][pid]
    cell = tuple(child.get("pos") or (0, 0))
    C.harm(w, "child_broke_fragile", stat="damage", child=pid, item=eid)
    if str(rec.get("material", "")).lower() in FRAGILE_MATERIALS:
        from . import glass as G
        rid = child.get("location")
        m = w.mut(eid)
        m["location"], m["relation"], m["pos"] = rid, "on", list(cell)
        m.pop("spos", None)
        G.shatter(w, eid, impact_cell=cell)
    else:
        w.mut(eid)["broken"] = True


@system("safety.child", order=345, max_dt=30.0)
def child_system(w, dt, tick):
    state = C.bind_world(w).s
    now = C.now(state)
    for pid in sorted(state.get("people", {})):
        cfg = state["people"][pid].get("_explore")
        if not cfg or not state["people"][pid].get("awake", True):
            continue
        if cfg.get("until_s") is not None and now > float(cfg["until_s"]):
            continue
        if cfg.get("from_s") is not None and now < float(cfg["from_s"]):
            continue
        nxt = float(cfg.get("next_s", cfg.get("from_s", 0.0) or 0.0))
        if now < nxt:
            continue
        _explore_step(w, pid, cfg)
        m = w.person_m(pid)
        ex = dict(m.get("_explore") or {})
        ex["next_s"] = now + float(cfg.get("every_s", 45.0))
        m["_explore"] = ex
    audit(w)
