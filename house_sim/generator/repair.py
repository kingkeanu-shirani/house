"""Final layout pass for generated houses: repair what can be repaired, report what cannot.

Every furnisher places through Placer (which never makes access worse), but furnishers also set positions
directly, flip blocks_path after placing, or run before later pieces box things in. This pass runs after all of
them and guarantees, per room:

  * the robot's dock (and so the robot's start cell) is in the walkable area joined to the room's doorways,
  * no loose floor item cuts a hazard-free walking route between doorways or the only hazard-free access to
    furniture, fixtures or the dock (such items are moved to a cell that cuts nothing),
  * every person and pet stands on a free cell outside doorway clearance that cuts no route, and no two agents
    share a cell.

What it cannot fix is reported (when a report list is passed): rooms whose doorways are split by furniture,
entities nothing can reach, overlapping floor-standing footprints, footprints off the grid, and tall things standing
in a door's swing or doorway clearance. tools/layout_audit.py sweeps seeds and attributes these to furnishers.
"""
from ..kernel import geometry


def _score(rep):
    return (len(rep["ok"]), sum(len(g) * len(g) for g in rep["groups"]))


def _loose(record):
    """A portable item standing loose on the floor (not a fixture, mount, ground layer, door, docked device or
    plugged-in device whose cord route belongs to it)."""
    return (bool(record.get("portable")) and not geometry.blocks(record) and not record.get("mounted")
            and not record.get("ground_layer") and "connects" not in record and bool(record.get("pos"))
            and not record.get("docked") and not record.get("plugged_in") and not record.get("cord_cells"))


def _tall(record, config=None, eid=None):
    """0.15 m or taller, counting what stands on it (a saucer carrying a potted plant is tall)."""
    try:
        own = float((record.get("size_m") or [0, 0, 0.1])[2])
    except (TypeError, ValueError, IndexError):
        return True
    if own >= 0.15 or config is None or eid is None:
        return own >= 0.15
    return own + _stack_above(config, eid, 0) >= 0.15


def _stack_top(record, config, eid):
    try:
        own = float((record.get("size_m") or [0, 0, 0.1])[2])
    except (TypeError, ValueError, IndexError):
        own = 0.1
    return own + _stack_above(config, eid, 0)


def _stack_above(config, eid, depth):
    if depth > 6:
        return 0.0
    best = 0.0
    for cid, child in config["entities"].items():
        if child.get("location") == eid and child.get("relation", "on") in ("on", "in"):
            try:
                h = float((child.get("size_m") or [0, 0, 0.1])[2])
            except (TypeError, ValueError, IndexError):
                h = 0.1
            best = max(best, h + _stack_above(config, cid, depth + 1))
    return best


def _relocate_dock(config, placer, dock):
    record = config["entities"][dock]
    rid = record["location"]
    old = record.pop("pos", None)
    cols, rows = geometry.footprint_size(record)
    rooms = [rid] + [r for r in placer.rooms() if r != rid and not config["rooms"][r].get("outdoor")]
    for room in rooms:
        pos = placer._free_spot(room, cols, rows, wall=True, standable=True) or \
            placer._free_spot(room, cols, rows, wall=False, standable=True)
        if pos is not None:
            record["location"], record["pos"] = room, pos
            robot = config.get("robot") or {}
            if robot.get("home") == dock:
                robot["room"], robot["pos"] = room, list(pos)
            return True
    record["pos"] = old
    return False


def _fix_loose_items(config, placer, rid, report):
    """If loose items cut hazard-free routes in a room, take them all off the floor and put them back one at a time,
    nearest to where they were, each on a cell that cuts nothing (geometry.free_floor_cell keep_access); items with
    no such cell overflow into a connected room of the same kind."""
    state = placer.state()
    static_blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    targets = geometry.access_targets(state, rid, level="all")
    static = geometry.access_report(state, rid, static_blocked, targets)
    hazards = geometry.placement_hazards(state, rid)
    safe = geometry.access_report(state, rid, static_blocked | set(hazards), targets)
    if geometry.not_worse(static, safe):
        return
    loose = [eid for eid in sorted(geometry.room_entities(state, rid)) if _loose(config["entities"][eid])]
    if not loose:
        return
    saved = {eid: config["entities"][eid].pop("pos") for eid in loose}
    # first put back every item whose own spot cuts nothing (well-placed things stay where they were)
    deferred = []
    for eid in loose:
        record = config["entities"][eid]
        spot = geometry.free_floor_cell(placer.state(), rid, near=tuple(saved[eid]), ignore=(eid,), record=record,
                                        strict=_tall(record, config, eid), keep_access=True)
        if spot is not None and list(spot) == list(saved[eid]):
            record["pos"] = list(spot)
        else:
            deferred.append(eid)
    order = sorted(deferred, key=lambda e: (-len(geometry.footprint(dict(config["entities"][e], pos=saved[e]))), e))
    for eid in order:
        record = config["entities"][eid]
        tall_top = _stack_top(record, config, eid)
        strict = _tall(record, config, eid)

        def hides(room_id):
            # wall cells of mounts a tall item (over 0.5 m with what stands on it) would stand in front of
            return tuple(sorted(placer._hidden_by(room_id, tall_top))) if tall_top > 0.5 else ()

        def here(avoid=()):
            return geometry.free_floor_cell(placer.state(), rid, near=tuple(saved[eid]), ignore=(eid,), record=record,
                                            strict=strict, keep_access=True, avoid=avoid)

        def overflow(hide_free):
            for other in _neighbour_rooms(config, rid):
                record["location"] = other
                found = geometry.free_floor_cell(placer.state(), other, ignore=(eid,), record=record, strict=strict,
                                                 keep_access=True, avoid=hides(other) if hide_free else ())
                if found is not None:
                    return found
                record["location"] = rid
            return None
        # a cell here that hides no wall mount, else one in a neighbour room that hides none there; only then a cell
        # here (or next door) in front of a mount -- still better than cutting a route
        cell = here(avoid=hides(rid))
        if cell is None:
            cell = overflow(hide_free=True)
        if cell is None and hides(rid):
            cell = here()
            if cell is None:
                cell = overflow(hide_free=False)
        if cell is None:
            # nowhere that cuts nothing: the free cell nearest its old spot (reported; the furnisher should fix this)
            cell = geometry.free_floor_cell(placer.state(), rid, near=tuple(saved[eid]), ignore=(eid,), record=record,
                                            strict=False, reachable=False) or saved[eid]
            if report is not None:
                report.append("chokepoint {}: {} at {} cuts a hazard-free route and has no other spot".format(rid, eid, cell))
        record["pos"] = list(cell)


def _neighbour_rooms(config, rid):
    outdoor = bool(config["rooms"][rid].get("outdoor"))
    out = []
    for cid, conn in sorted(config.get("connections", {}).items()):
        if rid in conn["rooms"] and not conn.get("needs_ladder") and conn.get("kind") != "stairs":
            other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
            room = config["rooms"].get(other) or {}
            if bool(room.get("outdoor")) == outdoor and not room.get("shared") and other not in out:
                out.append(other)
    return out


def _place_agents(config, placer, report):
    taken = set()
    for group in ("people", "pets"):
        for aid in sorted(config[group]):
            agent = config[group][aid]
            rid = agent.get("location")
            if rid not in config["rooms"]:
                continue
            pos = tuple(agent["pos"]) if agent.get("pos") else None
            if pos is not None:
                agent["pos"] = None
                state = placer.state()
                ok = (rid, pos) not in taken and geometry.in_grid(config["rooms"][rid], pos) and \
                    pos not in geometry.door_clearance(state, rid)
                # free_floor_cell returns `pos` itself only if it is free, reachable and cuts no route
                ok = ok and geometry.free_floor_cell(state, rid, near=pos, keep_access=True, clearance=False) == list(pos)
                if ok:
                    agent["pos"] = list(pos)
                    taken.add((rid, pos))
                    continue
            cell = placer.agent_cell(rid, near=pos)
            if cell is None:
                cell = list(pos) if pos else None
                if report is not None:
                    report.append("agent {}: no free cell in {}".format(aid, rid))
            agent["pos"] = cell
            if cell:
                taken.add((rid, tuple(cell)))


def _audit(config, placer, report):
    state = placer.state()
    for rid in sorted(config["rooms"]):
        room = config["rooms"][rid]
        static_blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
        targets = geometry.access_targets(state, rid, level="all")
        static = geometry.access_report(state, rid, static_blocked, targets)
        doors = [k for k in targets if k[0:1] == ("door",)]
        if len(doors) > 1 and not any(len(g) == len(doors) for g in static["groups"]):
            report.append("split {}: doorways not mutually reachable {}".format(rid, sorted(sorted(k[1] for k in g) for g in static["groups"])))
        for key in sorted(k for k in targets if k not in static["ok"] and k[0:1] != ("door",)):
            if config["entities"][key].get("category") in ("outlet",):   # outlets behind the appliance they feed are normal
                continue
            sealers = []
            for eid in sorted(geometry.room_entities(state, rid)):
                rec = config["entities"][eid]
                if eid != key and geometry.blocks(rec):
                    trial = geometry.access_report(state, rid, static_blocked - set(geometry.footprint(rec)), targets)
                    if key in trial["ok"]:
                        sealers.append(eid)
            report.append("unreachable {}: {}{}".format(rid, key, " sealed by {}".format(sealers) if sealers else ""))
        hazards = geometry.placement_hazards(state, rid)
        safe = geometry.access_report(state, rid, static_blocked | set(hazards), targets)
        lost = []
        for key in sorted(k for k in static["ok"] - safe["ok"] if k[0:1] != ("door",)):
            # a target may be approached through its own keep-clear ring (or its dock's), as the core approach does
            own = set(geometry.target_sources(state, key))
            rec = config["entities"].get(key) or {}
            if rec.get("docked"):
                cells_here = set(geometry.footprint(rec))
                own |= {e for e in geometry.room_entities(state, rid)
                        if e != key and set(geometry.footprint(config["entities"][e])) & cells_here}
            mine = {c for c, items in hazards.items() if all(src in own for _, src in items)}
            trial = geometry.access_report(state, rid, static_blocked | (set(hazards) - mine), targets)
            if key not in trial["ok"]:
                lost.append(key)
        split = not geometry.not_worse(static, dict(safe, ok=static["ok"]))
        if lost or split:
            sources = sorted({src for items in hazards.values() for _, src in items})
            blame = []
            for src in sources:
                cells = {c for c, items in hazards.items() if all(s == src for _, s in items)}
                trial = geometry.access_report(state, rid, static_blocked | (set(hazards) - cells), targets)
                if _score(trial) > _score(safe):
                    cord = any(h.get("source") == src and h.get("cord") for h in room.get("floor_hazards", []))
                    blame.append(src + (" (cord)" if cord else ""))
            if not blame:
                blame = _sources_on_routes(state, rid, static_blocked, hazards, targets, lost)
            report.append("hazard-only access {}: {} targets{} only reachable over hazards from {}".format(
                rid, len(lost), " and doorways" if split else "", blame or sources))
        owner = {}
        clearance = geometry.door_clearance(state, rid)
        for eid in sorted(geometry.room_entities(state, rid)):
            record = config["entities"][eid]
            if not record.get("pos"):
                continue
            cells = geometry.footprint(record)
            if not geometry.floor_standing(record):
                continue
            if any(not geometry.in_grid(room, c) for c in cells):
                report.append("offgrid {}: {} {}".format(rid, eid, cells))
            for c in cells:
                if c in owner and not (record.get("docked") or config["entities"][owner[c]].get("docked")):
                    report.append("overlap {}: {} and {} at {}".format(rid, owner[c], eid, c))
                    break
                owner[c] = eid
            if _tall(record, config, eid) and set(cells) & clearance:
                report.append("door clearance {}: {} at {}".format(rid, eid, sorted(set(cells) & clearance)))
        _audit_mounts(config, state, rid, report)
        dock = (config.get("robot") or {}).get("home")
        if dock and config["entities"].get(dock, {}).get("location") == rid and dock not in static["ok"]:
            report.append("robot dock sealed {}: {}".format(rid, dock))


def _audit_mounts(config, state, rid, report):
    """3-D overlaps: a wall mount hidden behind a taller floor-standing piece, or two deep wall mounts sharing wall
    space at overlapping heights (outlets behind the appliance they feed are normal)."""
    mounts, standing = [], []
    for eid in sorted(geometry.room_entities(state, rid)):
        rec = config["entities"][eid]
        if not rec.get("pos") or rec.get("ground_layer") or "connects" in rec:
            continue
        try:
            h = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0) if rec.get("mounted") else 0.0
            size = rec.get("size_m") or [0.1, 0.1, 0.1]
            top, depth = h + float(size[2]), min(float(size[0]), float(size[1]))
        except (TypeError, ValueError, IndexError):
            continue
        cells = set(geometry.footprint(rec))
        if geometry.floor_standing(rec):
            standing.append((eid, cells, top))
        elif rec.get("mounted") and rec.get("category") not in ("outlet", "light", "window", "drawer") and 0.5 <= h < 2.0:
            mounts.append((eid, cells, h, top, depth))     # drawers are fronts of the counter run; ceiling items aside
    for eid, cells, h, top, depth in mounts:
        for sid, scells, stop in standing:
            if cells & scells and stop > h + 0.05:
                report.append("hidden mount {}: {} behind {}".format(rid, eid, sid))
                break
    for i, (a, ac, ah, at, ad) in enumerate(mounts):
        for b, bc, bh, bt, bd in mounts[i + 1:]:
            if ac & bc and ad >= 0.1 and bd >= 0.1 and ah < bt and bh < at:
                report.append("mount overlap {}: {} and {}".format(rid, a, b))


def _sources_on_routes(state, rid, blocked, hazards, targets, keys):
    """Hazard sources lying on the shortest plain routes from the doorways to the given targets' access cells
    (attribution when no single hazard source is to blame)."""
    from collections import deque
    room = state["rooms"][rid]
    starts = [cells[0] for k, (cells, _, _) in targets.items() if k[0:1] == ("door",) and cells[0] not in blocked]
    prev, queue = {c: None for c in starts}, deque(starts)
    while queue:
        cur = queue.popleft()
        for nxt in ((cur[0] + 1, cur[1]), (cur[0] - 1, cur[1]), (cur[0], cur[1] + 1), (cur[0], cur[1] - 1)):
            if nxt not in prev and nxt not in blocked and geometry.in_grid(room, nxt):
                prev[nxt] = cur
                queue.append(nxt)
    found = set()
    for key in keys or [k for k in targets if k[0:1] == ("door",)]:
        cells, radius, standable = targets[key]
        goals = set(cells) if standable else geometry.near_cells(cells, room, radius) - set(cells)
        reach = [c for c in goals if c in prev]
        if not reach:
            continue
        cur = min(reach, key=lambda c: (_depth(prev, c), c))
        while cur is not None:
            for _, src in hazards.get(cur, []):
                found.add(src)
            cur = prev[cur]
    return sorted(found)


def _depth(prev, cell):
    n = 0
    while prev[cell] is not None:
        cell, n = prev[cell], n + 1
    return n


def repair_layout(config, placer, report=None):
    robot = config.get("robot") or {}
    dock = robot.get("home")
    if dock and dock in config["entities"]:
        state = placer.state()
        rid = config["entities"][dock]["location"]
        targets = geometry.access_targets(state, rid, level="basic")
        rep = geometry.access_report(state, rid, set(geometry.blocked_cells(state, rid, include_agents=False)), targets)
        if dock not in rep["ok"]:
            _relocate_dock(config, placer, dock)
    elif robot.get("room") and not robot.get("pos"):
        robot["pos"] = geometry.free_floor_cell(placer.state(), robot["room"], keep_access=True)
    for rid in sorted(config["rooms"]):
        _fix_loose_items(config, placer, rid, report)
    _place_agents(config, placer, report)
    if report is not None:
        _audit(config, placer, report)
    return config
