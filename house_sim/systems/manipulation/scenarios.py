"""Scenario helpers (setup op {"op": "helper", "name": "manipulation.<name>", "args": {...}}).

  manipulation.goal          declare a layout goal evaluated by the manipulation.goals system
  manipulation.furniture     place furniture robustly (wall, then free-standing, then rotated)
  manipulation.home_poses    record current poses as hidden ground truth (+ robot memory, + a poses goal)
  manipulation.displace      move floor items to other free cells (after recording home poses)
  manipulation.make_stack    build a stack bottom->top from already spawned items
  manipulation.seats         chairs around a table (spawning missing ones) for place-setting goals
  manipulation.hold_job / light_job / co_carry_job   cooperative work with a person
  manipulation.tangle        tangle cables/chains together
  manipulation.clear_surface move everything off a surface onto the floor nearby (clean setups)
  manipulation.demonstration a person shows the steps once (observation 'demonstration')
  manipulation.free_cell     floor spot(s) for a footprint (kernel keep_access rule, outside door clearance)
  manipulation.clear_target  a straight, unobstructed push/drag target for a floor item
  manipulation.clear_door_zones  move tall loose floor items out of the kernel door clearance of listed rooms
"""
import copy

from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from . import common as C


def _section(cfg):
    sec = cfg.setdefault(C.SECTION, {})
    sec.setdefault("goals", {})
    sec.setdefault("jobs", {})
    return sec


def _state(ctx):
    return ctx.state()


@scenario_helper("manipulation.goal")
def goal(ctx, name, kind, **spec):
    body = {"kind": kind}
    body.update(copy.deepcopy(spec))
    _section(ctx.config)["goals"][name] = body
    return {}


def _reachable(ctx, eid):
    """Some cell beside the piece can be walked to from a doorway of its room, the piece walls nobody
    in, and its access does not depend on cells next to where a person or pet stands."""
    cfg = ctx.config
    rec = cfg["entities"][eid]
    room = rec["location"]
    state = ctx.state()
    cells = geometry.footprint(rec)
    goals = geometry.near_cells(cells, cfg["rooms"][room]) - set(cells)
    blocked = geometry.blocked_cells(state, room)
    goals = [g for g in goals if g not in blocked]
    doors = geometry.doorway_cells(state, room)
    if not goals or not doors:
        return False
    agents = tuple(cfg.get("people", {})) + tuple(cfg.get("pets", {}))
    if not any(geometry.find_path(state, room, d, goals, ignore=agents) is not None for d in doors):
        return False
    hard = set(geometry.blocked_cells(state, room, include_agents=False))
    if geometry.blocks(rec):
        # nobody may be walled into a corner by the new piece
        before = geometry.reachable_cells(state, room, hard - set(cells))
        after = geometry.reachable_cells(state, room, hard)
        if any(a in before and a not in after for a in _agent_cells(cfg, room)):
            return False
    return _agent_safe(state, room, set(cells), hard)


def _agent_cells(cfg, rid):
    return [tuple(a["pos"]) for group in ("people", "pets") for _, a in sorted(cfg.get(group, {}).items())
            if a.get("location") == rid and a.get("pos")]


def _agent_zone(cfg, rid):
    """Cells people and pets in the room stand on, plus their 8 neighbours: residents settle a step
    out of passages at episode start (people domain) and shuffle about, so a piece whose only access
    runs through such a cell can end up cut off."""
    room = cfg["rooms"][rid]
    zone = set()
    for group in ("people", "pets"):
        for _, agent in sorted(cfg.get(group, {}).items()):
            if agent.get("location") == rid and agent.get("pos"):
                ax, ay = agent["pos"][0], agent["pos"][1]
                zone.update(c for c in ((ax + dx, ay + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)) if geometry.in_grid(room, c))
    return zone


def _agent_safe(state, rid, cells, hard, zone=None):
    """The piece stays reachable from a doorway (kernel reachable_cells) even with every agent zone cell taken."""
    zone = _agent_zone(state, rid) if zone is None else zone
    if not zone:
        return True
    room = state["rooms"][rid]
    blocked = set(hard) | set(cells) | (zone - set(geometry.doorway_cells(state, rid)))
    reach = geometry.reachable_cells(state, rid, blocked)
    return bool((geometry.near_cells(cells, room) - set(cells)) & reach)


@scenario_helper("manipulation.demonstration")
def demonstration(ctx, person, steps, item=None, name="demo", activity=None, ongoing=False):
    """A person shows how they do something once (or is doing it right now when ongoing=true). The robot
    sees the observed steps (action words + key parameters) while it shares the room with them:
    observation field 'demonstration'. Pair with a process/same_as/stack goal on the remaining items."""
    cfg = ctx.config
    if person not in cfg.get("people", {}):
        raise SpecError("demonstration: unknown person " + str(person))
    demo = {"person": person, "item": item, "activity": activity, "ongoing": bool(ongoing),
            "steps": [dict(s) if isinstance(s, dict) else {"action": str(s)} for s in steps]}
    _section(cfg).setdefault("demos", {})[name] = demo
    return {}


@scenario_helper("manipulation.furniture")
def furniture(ctx, room, type, var=None, near=None, props=None, rot=None, fallback_types=None, room_var=None,
              use_existing=None, free_m=None):
    """Place furniture so it can be reached from the room's doorway (retries other spots).

    Placer.furniture first (wall, then free-standing); when it finds nothing (Placer refuses every spot
    once some earlier piece in the room is already cut off), a search of every free spot that keeps the
    doorways connected and leaves every piece that was reachable still reachable (and nobody walled in).
    use_existing: furniture types (type or is_a) already in the room that may stand in for the new piece
    when the room has no space left (a tray can start on a kitchen counter); free_m [w, d] is the clear
    top area such a piece must still offer. fallback_types: room types tried (sorted ids) when `room` is
    too crowded; room_var binds the room used."""
    props = dict(props or {})
    p = ctx.placer
    if room not in ctx.config["rooms"]:
        raise SpecError("manipulation.furniture: unknown room {}".format(room))
    rooms = [room]
    for kind in fallback_types or []:
        rooms += [r for r in sorted(ctx.config["rooms"]) if ctx.config["rooms"][r].get("type") == kind and r not in rooms]

    def bind(eid, rid, existing=False):
        rec = ctx.config["entities"][eid]
        label = str(rec.get("label") or rec.get("type", type)).replace("_", " ")
        rtype = str(ctx.config["rooms"][rid].get("type") or "").replace("_", " ")
        if existing and rtype and rtype not in label:
            label = "{} {}".format(rtype, label)
        out = {"result": eid, (var or "furniture"): eid, (var or "furniture") + "_label": label}
        if room_var:
            out[room_var] = rid
        return out
    for rid in rooms:
        for attempt in range(12):
            eid = p.furniture(rid, type, wall=attempt % 2 == 0, near=near if (attempt < 2 and rid == room) else None, rot=rot, **props)
            if eid is None:
                continue
            if _reachable(ctx, eid):
                return bind(eid, rid)
            del ctx.config["entities"][eid]
        record = ctx.reg.make(type, rid, **props)
        for r in ([rot] if rot is not None else [0, 90] if C.dims(record)[0] != C.dims(record)[1] else [0]):
            record["rot"] = r
            spot = robust_spot(ctx, rid, record, near=near if rid == room else None, wall=True)
            if spot is not None:
                record["pos"] = list(spot)
                eid = p.new_id(type)
                ctx.config["entities"][eid] = record
                return bind(eid, rid)
        for eid in _existing_surfaces(ctx, rid, use_existing or (), free_m):
            return bind(eid, rid, existing=True)
    raise SpecError("manipulation.furniture: no reachable spot for {} in {}".format(type, ", ".join(rooms)))


def _existing_surfaces(ctx, rid, types, free_m=None):
    """Reachable pieces of the given types standing in the room with a clear top area of free_m."""
    cfg = ctx.config
    types = set(types)
    out = []
    for eid in sorted(cfg["entities"]):
        rec = cfg["entities"][eid]
        if rec.get("location") != rid or not rec.get("pos") or rec.get("mounted"):
            continue
        if not ({rec.get("type")} | set(rec.get("is_a") or [])) & types or not rec.get("accepts_items"):
            continue
        if free_m and not _has_room_on(cfg, eid, float(free_m[0]), float(free_m[1])):
            continue
        if _reachable(ctx, eid):
            out.append(eid)
    return out


def _has_room_on(cfg, eid, w, d, step=0.05):
    rec = cfg["entities"][eid]
    sw, sd = (rec.get("surface_size_m") or (rec.get("size_m") or [0.5, 0.5])[:2])[:2]
    rects = []
    for oid in sorted(cfg["entities"]):
        orec = cfg["entities"][oid]
        if orec.get("location") == eid and orec.get("relation", "on") == "on" and orec.get("spos") is not None:
            ow, od = geometry.footprint_m(orec)
            rects.append((orec["spos"], (ow, od)))
    for fw, fd in ((w, d), (d, w)):
        y = 0.0
        while y + fd <= sd + 1e-9:
            x = 0.0
            while x + fw <= sw + 1e-9:
                if not any(geometry.rect_overlap((x, y), (fw, fd), pos, size) for pos, size in rects):
                    return True
                x += step
            y += step
    return False


def robust_spot(ctx, rid, record, near=None, wall=True, exclude=(), ignore=()):
    """A floor position for `record` in any generated layout (None when the room has no such spot).

    Wall spots are preferred when `wall`, then the cell nearest `near`, else a seeded random pick
    among the candidates of spot_options."""
    options = spot_options(ctx, rid, record, wall=wall, exclude=exclude, ignore=ignore, ranked=True)
    if not options:
        return None
    best = min(o[0] for o in options)
    options = [o[1] for o in options if o[0] == best]
    if near is not None:
        options.sort(key=lambda c: (geometry.chebyshev(c, tuple(near)), c[1], c[0]))
        return list(options[0])
    return list(ctx.rng.choice(options))


def spot_options(ctx, rid, record, wall=True, exclude=(), ignore=(), ranked=False):
    """Every floor position (top-left cell) where `record` can stand, judged with the kernel's access model
    (geometry.access_targets / access_report / not_worse: the rule Placer.furniture uses, applied to every cell).

    The footprint must be free (blocking furniture, anything floor-standing, people and pets, the doorway
    clearance: doorway cells and neighbours, door leaves, overhead-door thresholds) and the piece itself
    accessible. A blocking piece must keep connected doorways connected, keep everything accessible now
    accessible (pieces another furnisher already boxed in are not held against it) and wall no person or pet in.
    Targets at level "all" (every mount) first, then "basic". ranked=True returns (rank, cell) pairs: rank 0 =
    against a wall (or wall not wanted), 1 = free-standing; +2 when the only access runs next to where a person or
    pet stands (residents settle a step at episode start); +4 when the piece itself can only be reached over floor
    items; +8 per target and +80 per doorway pair whose hazard-free access it would cut (the only clear passage)."""
    cfg = ctx.config
    room = cfg["rooms"][rid]
    state = ctx.state()
    ignore = tuple(sorted(set(ignore)))
    cols, rows = geometry.footprint_size(record)
    gc, gr = geometry.grid_size(room)
    if cols > gc or rows > gr:
        return []
    occupied = set(geometry.blocked_cells(state, rid, ignore=ignore, include_agents=True))
    for eid in geometry.room_entities(state, rid):
        if eid not in ignore and geometry.floor_standing(cfg["entities"][eid]):
            occupied.update(geometry.footprint(cfg["entities"][eid]))
    occupied |= geometry.door_clearance(state, rid)
    occupied |= {tuple(c) for c in exclude}
    hard = set(geometry.blocked_cells(state, rid, ignore=ignore, include_agents=False))
    hazards = {c for c, items in geometry.placement_hazards(state, rid).items() if any(src not in ignore for _, src in items)}
    blocking = geometry.blocks(record)
    zone = _agent_zone(cfg, rid)
    agents = [(aid, tuple(a["pos"])) for group in ("people", "pets") for aid, a in sorted(cfg.get(group, {}).items())
              if a.get("location") == rid and a.get("pos") and aid not in ignore]
    candidates = []
    for y in range(gr - rows + 1):
        for x in range(gc - cols + 1):
            cells = frozenset((x + i, y + j) for i in range(cols) for j in range(rows))
            if not cells & occupied:
                candidates.append(((x, y), cells))
    options = []
    for level in ("all", "basic"):
        targets = geometry.access_targets(state, rid, level=level, ignore=ignore)
        for aid, cell in agents:
            targets[("agent", aid)] = ([cell], 0, True)
        before = geometry.access_report(state, rid, hard, targets)
        before_clear = geometry.access_report(state, rid, hard | hazards, targets)
        for (x, y), cells in candidates:
            tg = dict(targets)
            tg[("new",)] = (sorted(cells), 1, False)
            after = geometry.access_report(state, rid, (hard | cells) if blocking else hard, tg)
            if not geometry.not_worse(before, after, new_keys=[("new",)]):
                continue
            touches = x == 0 or y == 0 or x + cols == gc or y + rows == gr
            rank = 0 if (touches or not wall) else 1
            if zone and not _agent_safe(state, rid, cells, hard, zone):
                rank += 2
            after_clear = geometry.access_report(state, rid, hard | hazards | cells, tg)
            split = sum(1 for g in before_clear["groups"] if len(g) > 1 and not any(g <= h for h in after_clear["groups"]))
            lost = len(before_clear["ok"] - after_clear["ok"])
            if split or lost:
                rank += 8 * (10 * split + lost)
            elif ("new",) not in after_clear["ok"]:
                rank += 4
            options.append((rank, (x, y)))
        if options:
            break
    if ranked:
        return options
    return [list(o[1]) for o in options]


def best_spots(options):
    """The cells of the best rank among spot_options(..., ranked=True) results."""
    if not options:
        return []
    best = min(o[0] for o in options)
    return [list(o[1]) for o in options if o[0] == best]


def _pose_of(cfg, eid):
    rec = cfg["entities"][eid]
    pose = {"location": rec.get("location"), "relation": rec.get("relation", "on")}
    if rec.get("location") in cfg["rooms"]:
        pos = rec.get("pos") or [0, 0]
        pose["cell"] = [int(pos[0]), int(pos[1])]
        pose["yaw_deg"] = C.yaw_of(rec)
        pose["rot"] = int(rec.get("rot", 0) or 0)
    else:
        if rec.get("spos") is not None:
            pose["spos"] = list(rec["spos"])
        if rec.get("shelf") is not None:
            pose["shelf"] = rec["shelf"]
        pose["yaw_deg"] = C.num(rec.get("yaw_deg", 0))
    return pose


@scenario_helper("manipulation.home_poses")
def home_poses(ctx, ids, goal=None, memory=True, pos_tol_m=0.05, yaw_tol_deg=5.0, yaw_period=360.0, fields=None):
    cfg = ctx.config
    ids = [ids] if isinstance(ids, str) else list(ids)
    out = {}
    items = {}
    for eid in ids:
        if eid not in cfg["entities"]:
            raise SpecError("home_poses: unknown entity " + str(eid))
        pose = _pose_of(cfg, eid)
        cfg["entities"][eid]["_home"] = copy.deepcopy(pose)
        want = {k: v for k, v in pose.items() if k in ("location", "relation", "cell", "spos", "shelf", "yaw_deg")}
        want["yaw_period"] = yaw_period
        if fields:
            want["fields"] = {f: cfg["entities"][eid].get(f) for f in fields}
        items[eid] = want
        if memory:
            cfg.setdefault("memory", {})["home_pose:" + eid] = {"value": copy.deepcopy(pose), "time_s": -1, "scope": "household"}
        out[eid + "_home"] = pose
    if goal:
        _section(cfg)["goals"][goal] = {"kind": "poses", "items": items, "pos_tol_m": pos_tol_m, "yaw_tol_deg": yaw_tol_deg}
    return out


@scenario_helper("manipulation.displace")
def displace(ctx, ids, min_cells=1, keep_room=True, yaw_deg=None):
    cfg = ctx.config
    ids = [ids] if isinstance(ids, str) else list(ids)
    moved = {}
    for eid in ids:
        rec = cfg["entities"][eid]
        rid = rec.get("location")
        if rid not in cfg["rooms"]:
            raise SpecError("displace: {} is not on a room floor".format(eid))
        old = list(rec.get("pos") or [0, 0])
        options = best_spots([o for o in spot_options(ctx, rid, rec, wall=False, ignore=(eid,), ranked=True)
                              if geometry.chebyshev(o[1], old) >= int(min_cells)])
        if not options:
            raise SpecError("displace: no free spot for " + eid)
        pos = ctx.rng.choice(options)
        rec["pos"] = list(pos)
        rec.pop("xy_m", None)
        if yaw_deg is not None:
            rec["yaw_deg"] = float(yaw_deg)
        moved[eid + "_cell"] = list(pos)
    return moved


@scenario_helper("manipulation.make_stack")
def make_stack(ctx, items, nested=False):
    cfg = ctx.config
    items = list(items)
    for lower, upper in zip(items, items[1:]):
        lrec, urec = cfg["entities"][lower], cfg["entities"][upper]
        for key in ("pos", "shelf", "spos", "xy_m"):
            urec.pop(key, None)
        urec["location"], urec["relation"] = lower, "on"
        lrec["accepts_items"] = True
        lrec.setdefault("container", False)
        lw, ld = C.footprint_wd(lrec)
        uw, ud = C.footprint_wd(urec)
        urec["spos"] = [round((lw - uw) / 2.0, 3), round((ld - ud) / 2.0, 3)]
        urec["stack_member"] = True
        urec["nested"] = bool(nested) and C.dims(urec)[0] <= C.dims(lrec)[0] + 1e-6
    base = cfg["entities"][items[0]]
    base["stack_count"] = len(items)
    return {"stack_base": items[0], "stack_top": items[-1]}


@scenario_helper("manipulation.seats")
def seats(ctx, table, count=4, spawn_type="chair"):
    """Chairs facing a table; spawns missing ones on free cells adjacent to the table."""
    cfg = ctx.config
    trec = cfg["entities"][table]
    rid = trec.get("location")
    state = ctx.state()
    tcells = set(geometry.footprint(trec))
    near = geometry.near_cells(list(tcells), cfg["rooms"][rid]) - tcells
    chairs = []
    for eid in sorted(cfg["entities"]):
        rec = cfg["entities"][eid]
        if rec.get("location") == rid and (rec.get("type") == "chair" or "chair" in rec.get("is_a", [])) and set(geometry.footprint(rec)) & near:
            chairs.append(eid)
    p = ctx.placer
    while len(chairs) < count:
        record = ctx.reg.make(spawn_type, rid)
        spots = [(rank, c) for rank, c in spot_options(ctx, rid, record, wall=False, ranked=True)
                 if set(geometry.footprint(dict(record, pos=list(c)))) & near]
        if not spots:
            break
        spots.sort(key=lambda o: (o[0], min(geometry.chebyshev(o[1], t) for t in tcells), o[1][1], o[1][0]))
        record["pos"] = list(spots[0][1])
        new = p.new_id(spawn_type)
        cfg["entities"][new] = record
        chairs.append(new)
    chairs = sorted(chairs)[:count]
    if len(chairs) < count:
        raise SpecError("seats: only {} chairs fit around {}".format(len(chairs), table))
    out = {"chairs": chairs}
    for i, c in enumerate(chairs, 1):
        out["chair_{}".format(i)] = c
    return out


@scenario_helper("manipulation.hold_job")
def hold_job(ctx, person, item, target, start=None, needed_s=90.0, tol_m=0.01, mount_to=None, faces_person=True, job="hold"):
    sec = _section(ctx.config)
    sec["jobs"][job] = {"kind": "hold", "person": person, "item": item, "_target": copy.deepcopy(target),
                        "pose": copy.deepcopy(start or {k: 0.0 for k in target}), "needed_s": float(needed_s), "tol_m": float(tol_m),
                        "faces_person": bool(faces_person), "state": "waiting", "progress_s": 0.0, "corrections": 0,
                        "on_done": {"mount_to": mount_to} if mount_to else {}}
    return {"job": job}


@scenario_helper("manipulation.light_job")
def light_job(ctx, person, item, points, job="light"):
    sec = _section(ctx.config)
    sec["jobs"][job] = {"kind": "light", "person": person, "item": item, "points": copy.deepcopy(points), "state": "waiting",
                        "lit_s": 0.0, "total_s": 0.0, "glare_s": 0.0, "aim": None}
    return {"job": job}


@scenario_helper("manipulation.co_carry_job")
def co_carry_job(ctx, person, item, route, person_lift_m=0.75, job="carry"):
    sec = _section(ctx.config)
    route = list(route)
    sec["jobs"][job] = {"kind": "co_carry", "person": person, "item": item, "route": route, "dest": route[-1] if route else None,
                        "person_lift_m": float(person_lift_m), "state": "waiting", "leg": 0}
    return {"job": job}


@scenario_helper("manipulation.tangle")
def tangle(ctx, ids, crossings=6, knots=1):
    cfg = ctx.config
    ids = list(ids)
    for eid in ids:
        cfg["entities"][eid]["tangle"] = {"crossings": int(crossings), "knots": int(knots), "with": [e for e in ids if e != eid]}
    return {}


@scenario_helper("manipulation.clear_surface")
def clear_surface(ctx, surface, keep=None):
    cfg = ctx.config
    keep = set(keep or [])
    rid = None
    loc = surface
    while loc in cfg["entities"]:
        loc = cfg["entities"][loc].get("location")
    rid = loc
    removed = []
    for eid in sorted(cfg["entities"]):
        rec = cfg["entities"][eid]
        if rec.get("location") == surface and eid not in keep and rec.get("relation", "on") == "on":
            removed.append(eid)
    for eid in removed:
        doomed = [eid]
        changed = True
        while changed:
            changed = False
            for other, rec in list(cfg["entities"].items()):
                if rec.get("location") in doomed and other not in doomed:
                    doomed.append(other)
                    changed = True
        for d in doomed:
            cfg["entities"].pop(d, None)
    return {"removed": removed}


@scenario_helper("manipulation.free_cell")
def free_cell(ctx, room, var="cell", near=None, avoid_doors=True, count=1, size_m=None, height_m=0.4):
    """Free floor spot(s) for a footprint size_m [w, d] (default one cell; height_m only decides the door-clearance
    rule), found with the kernel's placement rule: geometry.free_floor_cell with keep_access and strict - the whole
    footprint is free floor next to the walkable area joined to the doorways, outside the doorway clearance
    (doorway cells and neighbours, door leaves, overhead-door thresholds), and something standing there cuts no
    hazard-free route or anyone's only access. When nothing qualifies (a room whose clear floor is already cut up
    by other things on the floor) the reachable spot that cuts the fewest hazard-free routes and accesses is taken
    (spot_options ranking); avoid_doors=False then also allows the doorway clearance. Without `near` the spot nearest a random cell of the room is taken. count > 1 binds a list of
    spots; each later spot treats the earlier ones as items already standing there. Spawn on a spot with
    "floor": room, "cell": "{var}" (an explicit cell is used as given)."""
    if room not in ctx.config["rooms"]:
        raise SpecError("free_cell: unknown room " + str(room))
    fp = (1, 1)
    if size_m:
        fp = (max(1, int(-(-float(size_m[0]) // C.CELL))), max(1, int(-(-float(size_m[1]) // C.CELL))))
    spots = []
    for _ in range(max(1, int(count))):
        spots.append(_free_cells(ctx, room, near, avoid_doors, taken=spots, fp=fp, height_m=height_m))
    return {var: spots if int(count) > 1 else spots[0]}


def _probe(room, fp, height_m, pos=None):
    """A stand-in loose floor item with a footprint of fp cells (kernel footprint rounding: ceil(size / CELL))."""
    rec = {"type": "portable_object", "is_a": ["object"], "portable": True, "location": room, "relation": "on",
           "size_m": [fp[0] * C.CELL - 0.02, fp[1] * C.CELL - 0.02, float(height_m)], "label": "spot"}
    if pos is not None:
        rec["pos"] = [int(pos[0]), int(pos[1])]
    return rec


def _free_cells(ctx, room, near=None, avoid_doors=True, taken=(), fp=(1, 1), height_m=0.4):
    """Kernel free_floor_cell (keep_access, strict) first; when it finds nothing (the room's clear area is already cut
    up by other things on the floor), the spot_options cell that does the least harm: reachable, outside door
    clearance, cutting as few hazard-free routes and accesses as possible, nearest `near` among equals."""
    cfg = ctx.config
    if near is None:
        cols, rows = geometry.grid_size(cfg["rooms"][room])
        near = (ctx.rng.randrange(cols), ctx.rng.randrange(rows))
    near = (int(near[0]), int(near[1]))
    record = _probe(room, fp, height_m)
    stand_ins = ["_manip_spot_{}".format(i) for i in range(len(taken))]
    for sid, cell in zip(stand_ins, taken):
        cfg["entities"][sid] = _probe(room, fp, height_m, cell)       # earlier spots: items standing there
    try:
        state = ctx.state()
        cell = geometry.free_floor_cell(state, room, near=near, record=record, keep_access=True, strict=True)
        if cell is None:
            options = spot_options(ctx, room, record, wall=False, ranked=True)
            if options:
                best = min(o[0] for o in options)
                cell = min((o[1] for o in options if o[0] == best), key=lambda c: (geometry.chebyshev(c, near), c[1], c[0]))
        if cell is None and not avoid_doors:
            cell = geometry.free_floor_cell(state, room, near=near, record=record, clearance=False)
    finally:
        for sid in stand_ins:
            cfg["entities"].pop(sid, None)
    if cell is None:
        raise SpecError("free_cell: no free spot for a {}x{} footprint in {}".format(fp[0], fp[1], room))
    return [int(cell[0]), int(cell[1])]


def _loose(rec):
    """A portable item standing loose on the floor (not furniture, a mount, a ground layer, a door or docked)."""
    return (bool(rec.get("portable")) and not geometry.blocks(rec) and not rec.get("mounted") and not rec.get("ground_layer")
            and "connects" not in rec and bool(rec.get("pos")) and not rec.get("docked"))


@scenario_helper("manipulation.clear_door_zones")
def clear_door_zones(ctx, rooms, keep=()):
    """Move loose floor items 0.15 m or taller (lower ones pass under a door leaf) out of the kernel's doorway
    clearance of the listed rooms (geometry.door_clearance: doorway cells and neighbours, the leaf sweep of doors
    swinging into the room, overhead-door thresholds), each onto the nearest cell that cuts no hazard-free route
    (geometry.free_floor_cell keep_access, strict). Generated houses already keep tall loose items out of door
    clearance (Placer.floor_item); this is for a spec's own obstruct spawns and moves. Binds `moved_from_doors`."""
    cfg = ctx.config
    rooms = [rooms] if isinstance(rooms, str) else list(rooms)
    keep = set([keep] if isinstance(keep, str) else keep or ())
    moved = []
    for room in rooms:
        if room not in cfg["rooms"]:
            raise SpecError("clear_door_zones: unknown room " + str(room))
        zone = geometry.door_clearance(ctx.state(), room)
        for eid in sorted(cfg["entities"]):
            rec = cfg["entities"][eid]
            if eid in keep or rec.get("location") != room or not _loose(rec) or C.dims(rec)[2] < 0.15:
                continue
            if not set(geometry.footprint(rec)) & zone:
                continue
            state = ctx.state()
            here = tuple(rec["pos"])
            cell = geometry.free_floor_cell(state, room, near=here, ignore=(eid,), record=rec, strict=True, keep_access=True)
            if cell is None:
                cell = geometry.free_floor_cell(state, room, near=here, ignore=(eid,), record=rec, strict=True)
            if cell is None:
                continue
            rec["pos"] = [int(cell[0]), int(cell[1])]
            rec.pop("xy_m", None)
            moved.append(eid)
    return {"moved_from_doors": moved}


def _end_spot_check(ctx, item):
    """fn(cells) -> True if the item may end up on those cells: outside the doorway clearance and, for a blocking
    piece, keeping every doorway pair, everything accessible and every person/pet as reachable as with the item
    gone (kernel access model)."""
    cfg = ctx.config
    rec = cfg["entities"][item]
    room = rec["location"]
    state = ctx.state()
    zone = geometry.door_clearance(state, room)
    if not geometry.blocks(rec):
        return lambda cells: not (set(cells) & zone)
    hard = set(geometry.blocked_cells(state, room, ignore=(item,), include_agents=False))
    targets = geometry.access_targets(state, room, level="basic", ignore=(item,))
    for group in ("people", "pets"):
        for aid, a in sorted(cfg.get(group, {}).items()):
            if a.get("location") == room and a.get("pos"):
                targets[("agent", aid)] = ([tuple(a["pos"])], 0, True)
    before = geometry.access_report(state, room, hard, targets)

    def check(cells):
        cells = set(cells)
        if cells & zone:
            return False
        tg = dict(targets)
        tg[("new",)] = (sorted(cells), 1, False)
        return geometry.not_worse(before, geometry.access_report(state, room, hard | cells, tg), new_keys=[("new",)])
    return check


def _straight_targets(ctx, item, min_m, max_m, clearance_cells, agent_margin):
    """(direction, distance, cell) targets a floor item can slide to in a straight line: no blocking
    furniture, wall or agent in the swept footprint, the end spot outside the doorway clearance (plus
    clearance_cells around doorway cells when > 1) without cutting any route or access (_end_spot_check), and
    (agent_margin) the swept cells at least that many cells from where people and pets stand at setup -
    residents shuffle a step or two at episode start (people settle out of passages) and while the task runs."""
    from ...kernel.world import World
    from .motion import path_cells, sweep_floor
    cfg = ctx.config
    rec = cfg["entities"][item]
    room = rec["location"]
    w = World(cfg)
    x0, y0 = C.floor_xy(rec)
    doors = geometry.doorway_cells(ctx.state(), room)
    agents = [tuple(a["pos"]) for group in ("people", "pets") for _, a in sorted(cfg.get(group, {}).items())
              if a.get("location") == room and a.get("pos")]
    end_ok = _end_spot_check(ctx, item)
    candidates = []
    for direction, (dx, dy) in (("north", (0, -1)), ("south", (0, 1)), ("east", (1, 0)), ("west", (-1, 0))):
        steps = int(round(min_m / C.CELL))
        while steps * C.CELL <= max_m + 1e-9:
            dist = steps * C.CELL
            frac, hits = sweep_floor(w, item, dx * dist, dy * dist)
            if frac < 0.999:
                break
            if agent_margin and agents:
                swept = path_cells(rec, x0, y0, x0 + dx * dist, y0 + dy * dist)
                if any(geometry.chebyshev(c, a) <= agent_margin for c in swept for a in agents):
                    break
            cell = [int(round((x0 + dx * dist) / C.CELL)), int(round((y0 + dy * dist) / C.CELL))]
            cells = geometry.footprint(dict(rec, pos=cell))
            near_door = clearance_cells > 1 and any(geometry.chebyshev(c, d) <= clearance_cells for c in cells for d in doors)
            if not near_door and end_ok(cells):
                candidates.append((direction, dist, cell))
            steps += 1
    return candidates


@scenario_helper("manipulation.clear_target")
def clear_target(ctx, item, var="target", min_m=1.0, max_m=2.5, clearance_cells=1, agent_margin=1, relocate=True):
    """A target cell for a floor item reachable by a straight, unobstructed slide (for push/drag tasks).

    Works in any layout: when the item's spot has no clear straight run (hemmed in by furniture or people),
    the item (just placed by the spec) is moved to another reachable free spot that has one (relocate)."""
    cfg = ctx.config
    if item not in cfg["entities"] or cfg["entities"][item].get("location") not in cfg["rooms"]:
        raise SpecError("clear_target: {} is not standing on a room floor".format(item))
    rec = cfg["entities"][item]
    room = rec["location"]
    candidates = []
    start, start_xy = list(rec.get("pos") or [0, 0]), rec.get("xy_m")
    spots = None
    for margin in ([agent_margin, 0] if agent_margin else [0]):
        candidates = _straight_targets(ctx, item, min_m, max_m, clearance_cells, margin)
        if candidates:
            break
        if not relocate:
            continue
        if spots is None:
            ranked = spot_options(ctx, room, rec, wall=False, ignore=(item,), ranked=True)
            ctx.rng.shuffle(ranked)
            spots = [list(c) for _, c in sorted(ranked, key=lambda o: o[0])]
        rec.pop("xy_m", None)
        for spot in spots:
            rec["pos"] = list(spot)
            candidates = _straight_targets(ctx, item, min_m, max_m, clearance_cells, margin)
            if candidates:
                break
        if candidates:
            break
        rec["pos"] = list(start)
        if start_xy is not None:
            rec["xy_m"] = start_xy
    if not candidates:
        raise SpecError("clear_target: no clear straight path for " + item)
    direction, dist, cell = ctx.rng.choice(candidates)
    return {var: cell, var + "_direction": direction, var + "_distance_m": dist, var + "_from": list(rec["pos"])}
