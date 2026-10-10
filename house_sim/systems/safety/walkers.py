"""Scripted approaches by people and pets (fidelity G) - the dynamic part of "keep people away from the unsafe area".

A person or pet with `_walk` = {"at_s": t, "to": [col, row] | entity id, "room": rid (optional: walk from another
room through the connection), "speed_mps": 1.0, "purpose": "..."} starts walking at time t along an A* path. Each step:
  * barriers (caution_barrier, closed doors/gates, people/pets) block; a walker that cannot get through stops ("blocked")
  * people of 5+ who were warned about a hazard zone (speech/announce/report while the hazard exists) keep out of it;
    people of 8+ also route around hazards they can SEE (large shards, fire, spills); younger children and pets do not
  * an adult who was alerted and is in the same room as a walking child stops the child
  * people and pets squeeze past the robot; barriers, closed doors/gates and warnings are what keep them out
Stepping on a cell with glass while barefoot (people: barefoot unless `footwear`; pets always) cuts them (injury);
stepping into a biohazard spill tracks it (contamination); people stepping into an unmarked puddle on a hard floor slip
(injury); people of 5+ go around puddles marked with a sign/barrier. Trip hazards (floor items with floor_hazard
trip/cord/toy/clutter/rug_edge/shoes, or `trip_hazard`, and room floor_hazards of those kinds such as cords routed across
a walkway): people who can see them (8+, lit room) walk around them; elderly people and anyone on mobility aids, or
anyone walking in the dark (< 0.2 light, nothing visible), trip over them (injury, counted in stats.falls) - see ledger
safety.walk_events.
"""
from ...kernel import geometry
from ...kernel.perception import light_level
from ...kernel.plugins import system
from . import common as C

TRIP_KINDS = ("trip", "cord", "toy", "clutter", "rug_edge", "shoes", "object")


def _zone_cells(state, names):
    cells = set()
    zones = C.ledger_r(state).get("zones") or {}
    for name in names:
        z = zones.get(name)
        if not z or z.get("cleared"):
            continue
        cx, cy = z.get("center", [0, 0])
        r = int(z.get("radius_cells", 2))
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                cells.add((z["room"], cx + dx, cy + dy))
    return cells


def _visible_hazard_cells(state, rid):
    cells = set()
    dark = light_level(state, rid) < 0.2
    if not dark:
        for item in state["rooms"].get(rid, {}).get("floor_hazards", []) or []:
            if item.get("kind") in TRIP_KINDS and item.get("cell"):
                cells.add(tuple(item["cell"]))
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("location") != rid or not rec.get("pos"):
            continue
        if rec.get("category") == "puddle" and rec.get("clarity", "clear") == "clear":
            continue      # clear water on the floor is as hard for people to see as for the robot (signs warn them)
        if dark and rec.get("category") != "fire":
            continue      # in the dark people do not see what is on the floor
        if (rec.get("floor_hazard") and rec.get("spotted", True)) or rec.get("category") in ("fire", "biohazard"):
            for c in geometry.footprint(rec):
                cells.add(c)
    return cells


def _target_cell(state, walk, rid):
    to = walk.get("to")
    if isinstance(to, list) and len(to) == 2:
        return tuple(to)
    if isinstance(to, str) and to in state["entities"]:
        room, cells = geometry.anchor(state, to)
        if room == rid and cells:
            return tuple(cells[0])
    return None


def _step_agent(w, group, aid, now, dt):
    state = w.s
    agent = state[group][aid]
    walk = dict(agent.get("_walk") or {})
    rid = agent.get("location")
    if walk.get("room") and walk["room"] != rid and not walk.get("entered"):
        if group == "people" and C.age_of(agent) >= 5:
            zones = C.ledger_r(state).get("zones") or {}
            warned = [z for z in sorted(agent.get("_warned_zones") or []) if z in zones and not zones[z].get("cleared")
                      and zones[z].get("room") == walk["room"]]
            if warned:
                walk["done"], walk["result"] = True, "kept_away"
                _set(w, group, aid, walk)
                _event(w, aid, "kept_away", rid)
                return
        conn = next((c for o, cid, c in C.neighbors(state, rid) if o == walk["room"]), None)
        if conn is None:
            walk["done"], walk["result"] = True, "no_way"
        else:
            door = state["entities"].get(conn.get("door") or "")
            if door is not None and not door.get("open"):
                can_open = group == "people" and C.age_of(agent) >= 3 and not door.get("locked") and \
                    not (door.get("category") == "safety_gate" and C.age_of(agent) < 6)
                if not can_open:
                    walk["done"], walk["result"] = True, "blocked_by_door"
                    _set(w, group, aid, walk)
                    _event(w, aid, "blocked", rid)
                    return
                w.mut(conn["door"])["open"] = True
            nrid = walk["room"]
            m = _m(w, group, aid)
            m["location"] = nrid
            m["pos"] = list(conn["cells"].get(nrid, [0, 0]))
            walk["entered"] = True
            rid = nrid
            _hazard_contact(w, group, aid, rid, tuple(m["pos"]))
        _set(w, group, aid, walk)
        return
    target = _target_cell(state, walk, rid)
    if target is None:
        walk["done"], walk["result"] = True, "no_target"
        _set(w, group, aid, walk)
        return
    pos = tuple(agent.get("pos") or (0, 0))
    if pos == target:
        walk["done"], walk["result"] = True, "arrived"
        _set(w, group, aid, walk)
        _event(w, aid, "arrived", rid)
        return
    # an alerted adult in the room holds a child back
    if group == "people" and C.is_child(agent):
        for pid in C.people_in(state, rid):
            other = state["people"][pid]
            if pid != aid and C.is_adult(other) and other.get("_alerted") and other.get("awake", True):
                walk["done"], walk["result"] = True, "stopped_by_adult"
                _set(w, group, aid, walk)
                _event(w, aid, "stopped_by_adult", rid)
                return
    avoid = set()
    heeds_warnings = group == "people" and C.age_of(agent) >= 5      # told to keep out: children from ~5 comply
    watches_floor = group == "people" and C.age_of(agent) >= 8       # younger children run without looking down
    aware = heeds_warnings
    if heeds_warnings:
        warned = set(agent.get("_warned_zones") or [])
        avoid |= {(x, y) for (r, x, y) in _zone_cells(state, sorted(warned)) if r == rid}
        from . import water as WT
        avoid |= WT.marked_cells(state, rid)
    if watches_floor:
        avoid |= _visible_hazard_cells(state, rid)
    extra = sorted(avoid - {target})
    path = geometry.find_path(state, rid, pos, [target], ignore=(aid,), extra_blocked=extra)
    if path is None:
        if aware and avoid:
            walk["done"], walk["result"] = True, "kept_away"
            _event(w, aid, "kept_away", rid)
        else:
            walk["done"], walk["result"] = True, "blocked"
            _event(w, aid, "blocked", rid)
        _set(w, group, aid, walk)
        return
    speed = float(walk.get("speed_mps", 1.0 if group == "people" else 1.5))
    carry = float(walk.get("_carry", 0.0)) + speed * dt / C.CELL
    steps = int(carry)
    walk["_carry"] = round(carry - steps, 4)
    m = _m(w, group, aid)
    for cell in path[:steps]:
        m["pos"] = list(cell)
        _hazard_contact(w, group, aid, rid, cell)
    _set(w, group, aid, walk)


def _m(w, group, aid):
    return w.person_m(aid) if group == "people" else w.pet_m(aid)


def _set(w, group, aid, walk):
    _m(w, group, aid)["_walk"] = walk


def _event(w, aid, kind, rid):
    sec = C.ledger(w)
    events = sec.setdefault("walk_events", [])
    events.append({"time_s": C.now(w.s), "who": aid, "event": kind, "room": rid})
    del events[:-100]


def _zone_entry(w, group, aid, rid, cell):
    """Log the first time a person/pet steps into an uncleared hazard zone (cordon breached)."""
    state = w.s
    agent = state[group][aid]
    zones = C.ledger_r(state).get("zones") or {}
    for name in sorted(zones):
        z = zones[name]
        if z.get("cleared") or z.get("room") != rid:
            continue
        cx, cy = z.get("center", [0, 0])
        if max(abs(cell[0] - cx), abs(cell[1] - cy)) > int(z.get("radius_cells", 2)):
            continue
        entered = list(agent.get("_zones_entered") or [])
        if name in entered:
            continue
        m = _m(w, group, aid)
        m["_zones_entered"] = entered + [name]
        sec = C.ledger(w)
        sec["zone_entries"] = int(sec.get("zone_entries", 0)) + 1
        _event(w, aid, "entered_hazard_zone", rid)


def _hazard_contact(w, group, aid, rid, cell):
    _zone_entry(w, group, aid, rid, cell)
    state = w.s
    from . import water as WT
    if WT.slip_check(w, group, aid, rid, cell):
        _event(w, aid, "slipped_on_wet_floor", rid)
    if _trip_check(w, group, aid, rid, cell):
        _event(w, aid, "tripped", rid)
    agent = state[group][aid]
    barefoot = group == "pets" or (agent.get("barefoot", True) and not agent.get("footwear"))
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("location") != rid or not rec.get("pos") or tuple(rec["pos"]) != tuple(cell):
            continue
        if (rec.get("type") == "glass_shard" or "broken_glass" in (rec.get("tags") or [])) and rec.get("type") != "sharps_bundle" and barefoot:
            flags = list(agent.get("_cut_on") or [])
            if eid not in flags:
                m = _m(w, group, aid)
                m["_cut_on"] = flags + [eid]
                C.harm(w, "cut_on_glass", stat="injury", who=aid)
                _event(w, aid, "cut_on_glass", rid)
                return
        if rec.get("category") == "puddle" and not rec.get("resolved"):
            continue
        if rec.get("category") == "biohazard" and not rec.get("resolved"):
            m = _m(w, group, aid)
            m["contamination"] = dict(m.get("contamination") or {}, biohazard=0.5)
            C.harm(w, "walked_through_biohazard", who=aid)
            _event(w, aid, "walked_through_biohazard", rid)


def _trip_check(w, group, aid, rid, cell):
    state = w.s
    if group != "people":
        return False
    agent = state["people"][aid]
    mobility = (agent.get("abilities") or {}).get("mobility")
    prone = C.age_of(agent) >= 65 or mobility in ("cane", "walker", "wheelchair") or light_level(state, rid) < 0.2
    if not prone:
        return False
    hit = None
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("location") != rid or not rec.get("pos") or geometry.blocks(rec):
            continue
        if (rec.get("floor_hazard") in TRIP_KINDS or rec.get("trip_hazard")) and tuple(cell) in set(geometry.footprint(rec)):
            hit = eid
            break
    if hit is None:
        for item in state["rooms"].get(rid, {}).get("floor_hazards", []) or []:
            if item.get("kind") in TRIP_KINDS and tuple(item.get("cell") or ()) == tuple(cell):
                hit = str(item.get("source") or "floor_hazard")
                break
    if hit is None:
        return False
    done = list(agent.get("_tripped_on") or [])
    if hit in done:
        return False
    m = w.person_m(aid)
    m["_tripped_on"] = done + [hit]
    C.harm(w, "tripped_over_floor_item", stat="injury", who=aid, item=hit)   # a fall is an injury (global failure flag)
    w.stat("falls")
    return True


@system("safety.walkers", order=335, max_dt=30.0)
def walker_system(w, dt, tick):
    state = w.s
    now = C.now(state)
    for group in ("people", "pets"):
        for aid in sorted(state.get(group, {})):
            walk = state[group][aid].get("_walk")
            if not walk or walk.get("done") or now < float(walk.get("at_s", 0.0)):
                continue
            _step_agent(w, group, aid, now, dt)
