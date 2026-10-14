"""Reference skills for safety specs (privileged routing; every state change goes through runner.do).

  safety.extinguish        fetch extinguisher -> stand ~2.2 m back with an exit behind -> pull_pin -> aim base -> discharge
                           (sweep) in bursts until out -> watch for re-ignition
  safety.smother_pan       burner off -> fetch lid -> smother -> (wait to cool, keep the lid on)
  safety.alarm             sound_alarm (robot siren or panel; dispatch on a monitored panel)
  safety.evacuate_house    alarm -> sweep rooms for people/pets still inside (wake, lead, carry) -> to the assembly point
  safety.store_chemicals   for each product: cap/nozzle closed, upright, on its shelf in the cabinet; close (latch engages)
  safety.clean_glass       gloves -> spot slivers -> pick large -> sweep -> damp-wipe the scatter area -> wrap & label -> trash
  safety.clean_biohazard   gloves -> absorb -> wait -> scoop to bag -> disinfect/enzyme with dwell -> rinse/wipe -> bag to trash
  safety.mitigate_child_hazards  per hazard: move up / cover outlet / wind cord / anchor (with approval) / guard corners
  safety.check_gate        remove a prop wedge, open-and-release test, push test, report results
  safety.replace_detector_battery  open cover, swap the 9 V battery, close
  safety.stage_waste       stage hazardous items in their bins (tape lithium/9 V/coin terminals)
  safety.report            go, inspect, report a field to a person
  safety.sweep_rooms       visit rooms (optionally inspect named entities) - for detection tasks
"""
from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as S
from . import common as C
from . import fire as F
from . import suppress as SP


def _plan_error(msg):
    from ...tasks.runner import PlanError
    return PlanError(msg)


def _held(r):
    return [v for v in dict.fromkeys(r.state["robot"]["hands"].values()) if v]


def _drop(r, item):
    """Set a held item down on a free floor cell within arm's reach (crowded rooms: try every neighbouring cell)."""
    state = r.state
    rid = state["robot"]["room"]
    room = state["rooms"][rid]
    pos = tuple(state["robot"].get("pos") or (0, 0))
    blocked = set(geometry.blocked_cells(state, rid))
    used = set()
    for eid, rec in state["entities"].items():
        if rec.get("location") == rid and not rec.get("ground_layer") and "connects" not in rec and eid != item:
            used.update(geometry.footprint(rec))
    doors = {tuple(c["cells"][rid]) for c in state.get("connections", {}).values() if rid in c.get("cells", {})}
    hazards = set(geometry.hazard_cells(state, rid))
    swing = C.swing_cells(state, rid)   # a door/cabinet sweep would knock the item over when opened
    order = ((0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (-1, 1), (1, -1), (-1, -1), (0, 0))
    for relaxed in (0, 1, 2):
        for dx, dy in order:
            cell = (pos[0] + dx, pos[1] + dy)
            if not geometry.in_grid(room, cell) or cell in blocked or cell in doors or cell in hazards:
                continue
            if (relaxed < 1 and cell in used) or (relaxed < 2 and cell in swing):
                continue
            r.do("place", target=item, cell=list(cell))
            return
    r.do("place", target=item)


def _free_one_hand(r, keep=()):
    hands = r.state["robot"]["hands"]
    if any(v is None for v in hands.values()):
        return
    for eid in _held(r):
        if eid not in keep:
            _drop(r, eid)
            return


@skill("safety.alarm")
def alarm(r, kind="fire", panel=None, dispatch=False):
    if panel:
        rec = r.state["entities"][panel]
        if not (rec.get("remote_status") and r.state["robot"].get("network", True)):
            S.approach(r, panel)
        r.do("sound_alarm", target=panel, kind=kind, dispatch=bool(dispatch))
    else:
        r.do("sound_alarm", kind=kind)


@skill("safety.extinguish")
def extinguish(r, fire, extinguisher, stand_m=2.2, burst_s=4.0, max_bursts=5, watch_s=120, alarm_first=False, panel=None, point="base",
               pull_pin=True, sweep=True):
    state = r.state
    if alarm_first:
        alarm(r, "fire", panel=panel)
    S.fetch(r, extinguisher)
    if pull_pin and r.state["entities"][extinguisher].get("pin_in", True):
        _free_one_hand(r, keep=(extinguisher,))
        r.do("pull_pin", target=extinguisher)
    room = C.root_room(r.state, fire)
    if r.state["robot"]["room"] != room:
        S.go_room(r, room)
    cell = SP.standoff_cell(r.state, fire, want_m=stand_m)
    if cell is None:
        raise _plan_error("No standoff spot near the fire")
    if tuple(r.state["robot"].get("pos") or (0, 0)) != tuple(cell):
        # the most careful route that exists (never over glass, a sleeping pet or someone on the floor when avoidable)
        kinds = S.safe_avoid(r.state, room, r.state["robot"]["pos"], [tuple(cell)]) or ["sharp", "wet"]
        r.do("walk_to", cell=cell, avoid=kinds)
    for _ in range(int(max_bursts)):
        if r.state["entities"][fire].get("stage") == "out":
            break
        r.do("aim", target=fire, point=point)
        r.do("discharge", target=extinguisher, seconds=float(burst_s), sweep=bool(sweep))
    if watch_s:
        waited = 0
        while waited < int(watch_s):
            r.do("wait", seconds=30)
            waited += 30
            if r.state["entities"][fire].get("stage") != "out" and float(r.state["entities"][extinguisher].get("agent_left_kg", 0)) > 0.05:
                r.do("aim", target=fire, point=point)
                r.do("discharge", target=extinguisher, seconds=float(burst_s), sweep=bool(sweep))


@skill("safety.smother_pan")
def smother_pan(r, fire, burner, lid, cool_s=0, tool=None):
    S.approach(r, burner)
    rec = r.state["entities"][burner]
    if rec.get("on"):
        r.do("switch_off", target=burner)
    cover = tool or lid
    S.fetch(r, cover)
    S.approach(r, fire)
    r.do("smother", target=fire, tool=cover)
    if cool_s:
        S.wait_skill(r, int(cool_s))


def _people_left(state, include_pets=True):
    out = []
    for pid in C.residents(state):
        p = state["people"][pid]
        if C.safe_area(state, p.get("location")):
            continue
        ev = p.get("_evac") or {}
        if ev and ev.get("mode") in ("self", "follow") and not ev.get("stuck"):
            continue
        if ev.get("mode") == "follow_robot":
            continue
        out.append(("people", pid, p.get("location")))
    if include_pets:
        for pid in sorted(state.get("pets", {})):
            pet = state["pets"][pid]
            if pet.get("escaped") or C.safe_area(state, pet.get("location")):
                continue
            ev = pet.get("_evac") or {}
            if ev.get("mode") in ("follow_robot", "carried"):
                continue
            out.append(("pets", pid, pet.get("location")))
    return out


@skill("safety.evacuate_house")
def evacuate_house(r, kind="fire", panel=None, dispatch=False, include_pets=True, assembly=None, avoid_rooms=None, max_rounds=8):
    state = r.state
    alarm(r, kind, panel=panel, dispatch=dispatch)
    assembly = assembly or C.assembly_room(r.state)
    for _ in range(int(max_rounds)):
        left = _people_left(r.state, include_pets)
        if not left:
            break
        group, aid, room = left[0]
        if r.state["robot"].get("carrying_pet") and group == "pets" and r.state["pets"][aid].get("species") not in ("dog",):
            S.go_room(r, assembly)
            continue
        if r.state["robot"]["room"] != room:
            S.go_room(r, room)
        agent = r.state[group][aid]
        if group == "pets" and (agent.get("hiding") or agent.get("posture") == "hiding"):
            spot = agent.get("hiding_under")
            if spot:
                S.approach(r, spot)
                r.do("crouch")
                r.do("inspect", target=spot, where="under")
                r.do("stand")
        needs_reach = (group == "people" and not agent.get("awake", True)) or (group == "pets" and agent.get("species") != "dog")
        if needs_reach:
            S.approach(r, aid)
        if group == "pets" and agent.get("species") != "dog":
            for held in _held(r):
                _drop(r, held)
        r.do("evacuate", target=aid)
        follow = (group == "pets") or (group == "people" and C.age_of(r.state["people"][aid]) < 8)
        if follow:
            S.go_room(r, assembly)
    if r.state["robot"]["room"] != assembly:
        S.go_room(r, assembly)


@skill("safety.store_chemicals")
def store_chemicals(r, items, cabinet, shelves=None, close=True):
    shelves = shelves or {}
    S.approach(r, cabinet)
    if r.state["entities"][cabinet].get("openable") and not r.state["entities"][cabinet].get("open"):
        r.do("open", target=cabinet)
    for item in items:
        S.fetch(r, item)
        rec = r.state["entities"][item]
        if rec.get("openable") and rec.get("open"):
            r.do("close", target=item)
        nozzle = (rec.get("settings") or {}).get("nozzle")
        if nozzle not in (None, "off"):
            r.do("set", target=item, setting="nozzle", value="off")
        S.approach(r, cabinet, speed="careful")   # carrying a chemical container: no stumbles over thresholds
        args = {"target": item, "destination": cabinet, "relation": "in", "orientation": "upright"}
        if item in shelves:
            args["shelf"] = int(shelves[item])
        heights = r.state["entities"][cabinet].get("shelf_heights") or []
        shelf = args.get("shelf", 0)
        crouched = False
        if heights and 0 <= shelf < len(heights) and geometry.height_m(r.state, cabinet) + heights[shelf] < geometry.LOW_REACH_M:
            r.do("crouch")
            crouched = True
        r.do("place", **args)
        if crouched:
            r.do("stand")
    if close and r.state["entities"][cabinet].get("openable"):
        r.do("close", target=cabinet)


def _cover_positions(state, rid, center, radius_cells, reach_cells=2):
    """Greedy set cover: reachable standing cells so that every grid cell of the zone is within reach_cells of one of them."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    targets = {(x, y) for x in range(cols) for y in range(rows) if geometry.chebyshev((x, y), center) <= radius_cells}
    start = state["robot"].get("pos") if state["robot"].get("room") == rid else None
    stands = sorted(C.reachable_cells(state, rid, start=start, include_agents=True))
    left = set(targets)
    chosen = []
    pos = tuple(start or center)
    while left:
        best = None
        for cell in stands:
            gain = sum(1 for t in left if geometry.chebyshev(t, cell) <= reach_cells)
            if gain <= 0:
                continue
            key = (-gain, geometry.chebyshev(cell, pos), cell[1], cell[0])
            if best is None or key < best[0]:
                best = (key, cell)
        if best is None:
            break
        cell = best[1]
        chosen.append(cell)
        left = {t for t in left if geometry.chebyshev(t, cell) > reach_cells}
        pos = cell
    return chosen


@skill("safety.clean_glass")
def clean_glass(r, room, gloves, dustpan, broom=None, damp=None, wrap=None, trash=None, radius_m=3.5, close_doors=False, keep_gloves=False):
    """Never step on glass: every move in the room first checks the planned route and, where it would cross glass that
    has not been seen yet, stops short to crouch and look closely (which reveals it), then routes around it."""
    state = r.state
    events = [e for e in (C.ledger_r(state).get("glass_events") or []) if e.get("room") == room]
    if not events:
        raise _plan_error("No broken glass recorded in " + room)
    center = tuple(events[-1]["impact"])
    # supplies kept in another room (a crowded kitchen keeps its doorway clear): gloves on and dustpan in hand on the
    # way in, so the robot never walks into the glass room bare-handed
    if S.locate(r.state, gloves) != room and not _worn(r.state, gloves):
        S.fetch(r, gloves)
        r.do("wear", target=gloves)
    if S.locate(r.state, dustpan) != room and dustpan not in _held(r):
        S.fetch(r, dustpan)
    if broom and S.locate(r.state, broom) != room and broom not in _held(r) and dustpan in _held(r):
        # broom in the other hand too: glass can lie in the doorway itself, and the sweep pass is what clears the way
        # back out (a trip out for the broom could only cross the glass)
        S.fetch(r, broom)
    if r.state["robot"]["room"] != room:
        _enter_glass_room(r, room, dustpan, broom)
    if r.state["robot"].get("speed_mode") != "careful":
        r.do("set_speed", target="careful")
    if close_doors:
        for other, cid, conn in C.neighbors(r.state, room):
            door = conn.get("door")
            if door and r.state["entities"][door].get("open") and r.state["entities"][door].get("category") == "door":
                _glass_approach(r, room, door)
                r.do("close", target=door)
    if not _worn(r.state, gloves):
        _fetch_clear(r, gloves)
        r.do("wear", target=gloves)
    _fetch_clear(r, dustpan)
    radius_cells = int(round(radius_m / C.CELL))
    looked = set()
    _look_under_near(r, room, center, radius_cells, looked)
    floor = "floor_" + room if ("floor_" + room) in r.state["entities"] else room
    # pass 1: large pieces by gloved hand; pass 2: broom; pass 3: damp paper towel for slivers
    passes = [("pick", None)]
    if broom:
        passes.append(("sweep", broom))
    if damp:
        passes.append(("damp_wipe", damp))
    for method, tool in passes:
        if tool:
            for held in _held(r):
                if held not in (dustpan, tool):
                    _drop(r, held)
            _fetch_clear(r, tool, dustpan=dustpan)
        cleaned_from = set()

        def clean_here():
            if r.state["robot"].get("posture") == "standing":
                r.do("crouch")
            if method == "pick":
                r.do("inspect", target=floor, where="closely")
            r.do("clean_shards", target=floor, method=method, radius=1.0, destination=dustpan)
            cleaned_from.add(tuple(r.state["robot"]["pos"]))

        for stand_cell in _cover_positions(r.state, room, center, radius_cells):
            if tuple(r.state["robot"]["pos"]) != tuple(stand_cell):
                kinds = _glass_go(r, room, [stand_cell])
                if not kinds and tuple(r.state["robot"]["pos"]) not in cleaned_from and \
                        _glass_near(r.state, room, r.state["robot"]["pos"], 2):
                    # boxed in by glass (it can lie right round the doorway): clean what is in reach from here, then
                    # plan again -- never wade through it
                    clean_here()
                    if r.state["robot"].get("posture") != "standing":
                        r.do("stand")
                    kinds = _glass_go(r, room, [stand_cell])
                if not kinds:
                    continue   # do not wade through glass to reach a spot
                r.do("walk_to", cell=list(stand_cell), avoid=kinds)
            clean_here()
        if r.state["robot"].get("posture") != "standing":
            r.do("stand")
        if tool:
            _drop(r, tool)
    _mop_up(r, room, center, radius_cells, dustpan, broom, damp)
    _look_under_near(r, room, center, radius_cells, looked)
    if _zone_shards(r.state, room, center, radius_cells):
        _mop_up(r, room, center, radius_cells, dustpan, broom, damp)   # pieces found under furniture on the second look
    for held in _held(r):
        if held != dustpan:
            _drop(r, held)
    if wrap:
        if r.state["entities"][wrap].get("location") != "robot":
            _fetch_clear(r, wrap)
        r.do("stage_hazardous", target=dustpan, destination=wrap, prep="wrap_label")
        bundle = next((e for e in _held(r) if r.state["entities"][e].get("type") == "sharps_bundle"), None)
        if bundle is None:
            bundle = next((e for e in sorted(r.state["entities"]) if r.state["entities"][e].get("type") == "sharps_bundle"), None)
        if bundle is None:
            raise _plan_error("The glass could not be wrapped")
        if trash:
            if C.root_room(r.state, trash) == room:
                _glass_approach(r, room, trash)
            S.put(r, bundle, trash)
    if not keep_gloves:
        r.do("take_off", target=gloves)
    r.do("set_speed", target="normal")


def _enter_glass_room(r, room, dustpan, broom=None):
    """Go into the glass room without the first step landing on glass: when the doorway cell on the far side holds a
    piece (privileged routing knowledge), stop in the doorway on this side, crouch and clean across the opening
    (gloved hands for large pieces, the broom when in hand) before stepping through."""
    from . import glass as G
    route = S.room_route(r.state, r.state["robot"]["room"], room)
    if not route:
        return
    prev = r.state["robot"]["room"] if len(route) == 1 else route[-2][0]
    cid = route[-1][1]
    if r.state["robot"]["room"] != prev:
        S.go_room(r, prev)
    conn = r.state["connections"].get(cid) or {}
    cells = conn.get("cells") or {}
    far = tuple(cells.get(room) or ())
    held = _held(r)
    if far and prev in cells and dustpan in held and far in _glass_cells(r.state, room):
        near = list(cells[prev])
        if tuple(r.state["robot"].get("pos") or ()) != tuple(near):
            kinds = S.safe_avoid(r.state, prev, r.state["robot"]["pos"], [tuple(near)], None) or None
            r.do("walk_to", cell=near, **({"avoid": kinds} if kinds else {}))
        floor_prev = "floor_" + prev if ("floor_" + prev) in r.state["entities"] else prev
        if r.state["robot"].get("posture") == "standing":
            r.do("crouch")
        for method in ("pick",) + (("sweep",) if broom and broom in _held(r) else ()):
            if far in _glass_cells(r.state, room):
                r.do("clean_shards", target=floor_prev, method=method, radius=0.5, destination=dustpan)
        r.do("stand")
    S.go_room(r, room)


def _zone_shards(state, room, origin_center, radius_cells):
    from . import glass as G
    out = []
    for sid in G.shards(state, room):
        rec = state["entities"][sid]
        if rec.get("location") in state["rooms"] or rec.get("relation") == "under":
            cell = G.shard_cell(state, sid)
            if cell is not None:
                out.append((sid, cell))
    return out


MOP_REACH_CELLS = 2     # clean_shards radius=1.0 m: the stand cell must be within this of the piece


def _mop_stand(r, room, cell, tries=6):
    """Walk to the nearest cell within cleaning reach of `cell` that the robot can get to without stepping on glass.
    People and pets standing in the room are obstacles (as core routing treats them); a cell that cannot be reached
    glass-free is skipped for the next one. Returns the stand cell, or None when there is none."""
    pos = tuple(r.state["robot"]["pos"])
    glass = _glass_cells(r.state, room)
    reach = C.reachable_cells(r.state, room, start=pos, extra_blocked=glass, include_agents=True)
    options = sorted((geometry.chebyshev(c, cell), geometry.chebyshev(c, pos), c[1], c[0]) for c in reach if c not in glass)
    options = [o for o in options if o[0] <= MOP_REACH_CELLS][:tries]
    for _d, _dp, y, x in options:
        target = (x, y)
        if target == tuple(r.state["robot"]["pos"]):
            return target
        kinds = _glass_go(r, room, [target])
        if kinds:
            r.do("walk_to", cell=list(target), avoid=kinds)
            if tuple(r.state["robot"]["pos"]) == target:
                return target
    return None


def _mop_up(r, room, center, radius_cells, dustpan, broom, damp, rounds=8):
    """Work toward every piece still on the floor (or under furniture, once seen there): stand as close as possible
    without stepping on glass, then clean. A piece that cannot be reached from any stand cell this round is left for
    the next piece; the skill ends when nothing is left or nothing left can be reached."""
    tools = [t for t in (broom, damp) if t]
    floor = "floor_" + room if ("floor_" + room) in r.state["entities"] else room
    for _ in range(rounds):
        left = _zone_shards(r.state, room, center, radius_cells)
        if not left:
            return
        pos = tuple(r.state["robot"]["pos"])
        target = None
        for cell in sorted({c for _sid, c in left}, key=lambda c: (geometry.chebyshev(c, pos), c[1], c[0])):
            target = _mop_stand(r, room, cell)
            if target is not None:
                break
        if target is None:
            return
        for tool in tools:
            if tool not in _held(r):
                for held in _held(r):
                    if held != dustpan:
                        _drop(r, held)
                _fetch_clear(r, tool, dustpan=dustpan)
                if tuple(r.state["robot"]["pos"]) != tuple(target):
                    if r.state["robot"].get("posture") != "standing":
                        r.do("stand")
                    target = _mop_stand(r, room, cell)
                    if target is None:
                        break
            if r.state["robot"].get("posture") == "standing":
                r.do("crouch")
            method = "sweep" if tool == broom else "damp_wipe"
            r.do("clean_shards", target=floor, method=method, radius=1.0, destination=dustpan)
        if r.state["robot"].get("posture") != "standing":
            r.do("stand")


def _look_under_near(r, room, center, radius_cells, done=None):
    """Glass slides under counter toe-kicks, cabinets and tables: crouch and look under each one near the impact.
    Furniture that cannot be reached without stepping on glass is left for the second look (after the first passes)."""
    done = done if done is not None else set()
    for eid in sorted(r.state["entities"]):
        rec = r.state["entities"][eid]
        if eid in done or rec.get("location") != room or not rec.get("blocks_path") or not rec.get("pos"):
            continue
        cells = set(geometry.footprint(rec))
        if not cells or min(geometry.chebyshev(c, center) for c in cells) > radius_cells:
            continue
        goals = geometry.near_cells(cells, r.state["rooms"][room]) - cells
        pos = tuple(r.state["robot"].get("pos") or (0, 0))
        if pos not in goals and geometry.find_path(r.state, room, pos, goals) is None:
            continue
        if pos not in goals:
            kinds = _glass_go(r, room, goals)
            if not kinds:
                continue
            r.do("approach", target=eid, avoid=kinds)
        if r.state["robot"].get("posture") == "standing":
            r.do("crouch")
        r.do("inspect", target=eid, where="under")
        r.do("stand")
        done.add(eid)


def _glass_cells(state, rid):
    """Floor cells holding any glass, seen or not (privileged routing knowledge of the reference skill)."""
    from . import glass as G
    out = set()
    for sid in G.shards(state, rid):
        rec = state["entities"][sid]
        if rec.get("location") == rid and rec.get("pos"):
            out.update(geometry.footprint(rec))
    return out


def _glass_near(state, rid, pos, cells):
    """Any glass (seen or not: privileged routing knowledge) within `cells` of pos."""
    pos = tuple(pos or (0, 0))
    return any(geometry.chebyshev(c, pos) <= cells for c in _glass_cells(state, rid))


def _sharp_at(state, rid, cell):
    return any(k == "sharp" for k, _ in geometry.hazard_cells(state, rid).get(tuple(cell), []))


def _glass_go(r, rid, goals, rounds=8):
    """Prepare a glass-free walk toward goals and return the avoid kinds for it (None when only a route over glass is
    left). Where the planned route would step on glass not yet seen, stop short, crouch and look closely (which marks it
    as a sharp hazard), then plan again."""
    goals = {tuple(g) for g in goals}
    kinds = ("sharp",)
    floor = "floor_" + rid if ("floor_" + rid) in r.state["entities"] else rid
    for _ in range(rounds):
        state = r.state
        pos = tuple(state["robot"].get("pos") or (0, 0))
        if pos in goals:
            return list(kinds)
        path = geometry.find_path(state, rid, pos, goals, avoid_kinds=kinds)
        if path is None:
            return None
        glass = _glass_cells(state, rid)
        idx = next((i for i, c in enumerate(path) if c in glass), None)
        if idx is None:
            return list(kinds)
        if idx > 0:
            stop = path[idx - 1]
            sub = geometry.find_path(state, rid, pos, [stop], avoid_kinds=kinds)
            if sub is not None and not any(c in glass for c in sub):
                r.do("walk_to", cell=list(stop), avoid=list(kinds))
        unseen = [c for c in path if c in glass and not _sharp_at(r.state, rid, c)]
        if r.state["robot"].get("posture") == "standing":
            r.do("crouch")
        r.do("inspect", target=floor, where="closely")
        r.do("stand")
        if len([c for c in unseen if not _sharp_at(r.state, rid, c)]) >= len(unseen):
            return None   # it cannot be seen from here (too far or too dark): do not walk over it blind
    return None


def _glass_walk(r, rid, goals, kinds):
    """Walk to the goal cell `_glass_go` cleared (the approach action may otherwise pick another stand cell further
    out whose route crosses glass nobody has seen yet)."""
    if not kinds:
        return
    state = r.state
    pos = tuple(state["robot"].get("pos") or (0, 0))
    goals = {tuple(g) for g in goals}
    if pos in goals:
        return
    path = geometry.find_path(state, rid, pos, goals, avoid_kinds=tuple(kinds))
    if path and not any(c in _glass_cells(state, rid) for c in path):
        r.do("walk_to", cell=list(path[-1]), avoid=list(kinds))


def _glass_approach(r, rid, target):
    """Approach an entity in a room with broken glass without stepping on any of it (when possible)."""
    room, cells = geometry.anchor(r.state, target)
    if room != rid or not cells or geometry.reach_problem(r.state, target) is None:
        return S.approach(r, target)
    cells = set(cells)
    goals = geometry.near_cells(cells, r.state["rooms"][rid]) - cells
    kinds = _glass_go(r, rid, goals)
    _glass_walk(r, rid, goals, kinds)
    return S.approach(r, target, avoid=kinds)


def _worn(state, item):
    rec = state["entities"].get(item) or {}
    return rec.get("location") == "robot" and rec.get("relation") == "worn"


def _fetch_clear(r, item, dustpan=None):
    """Fetch an item without stepping on glass: look ahead and route around it, or (gloved, with a dustpan in hand) pick
    the glass off the way first; walk over it only when neither works. A tool kept in another room is fetched from
    there and carried back to the room the robot was working in."""
    state = r.state
    rid = state["robot"]["room"]
    if item in [v for v in state["robot"]["hands"].values() if v]:
        return item
    if S.locate(state, item) != rid:
        S.fetch(r, item)
        if r.state["robot"]["room"] != rid:
            _enter_glass_room(r, rid, dustpan)
        return item
    room, cells = geometry.anchor(state, item)
    avoid = None
    if room == rid and cells:
        cells = set(cells)
        goals = geometry.near_cells(cells, state["rooms"][rid]) - cells
        avoid = _glass_go(r, rid, goals)
        if avoid is None and dustpan and dustpan in _held(r):
            _clear_way_to(r, rid, goals, dustpan)
            avoid = _glass_go(r, rid, goals)
        if avoid is None and _go_around(r, rid, goals, dustpan):
            avoid = _glass_go(r, rid, goals)
        _glass_walk(r, rid, goals, avoid)
    return S.fetch(r, item, avoid=avoid)


def _glass_free_path(state, rid, start, goals):
    glass = _glass_cells(state, rid)
    goals = {tuple(g) for g in goals} - glass
    if not goals or tuple(start) in glass:
        return None
    return geometry.find_path(state, rid, tuple(start), goals, avoid_kinds=("sharp",), extra_blocked=sorted(glass))


def _go_around(r, rid, goals, dustpan=None):
    """Glass cuts the robot off from what it needs inside the room: leave by a doorway it can reach without stepping on
    glass and come back in by another doorway on the far side (through the other rooms, never back across this one).
    True when it re-entered somewhere the goals can be reached glass-free."""
    state = r.state
    pos = tuple(state["robot"].get("pos") or (0, 0))
    doors = []
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        cells = conn.get("cells") or {}
        if rid not in cells or conn.get("kind") not in (None, "doorway", "opening", "arch"):
            continue
        other = next((x for x in conn.get("rooms", []) if x != rid), None)
        if other is None or other not in state["rooms"]:
            continue
        door = state["entities"].get(conn.get("door") or "")
        if door is not None and door.get("locked"):
            continue
        doors.append((cid, other, tuple(cells[rid])))
    exits = [(cid, other) for cid, other, cell in doors if cell == pos or _glass_free_path(state, rid, pos, [cell]) is not None]
    entries = [(cid, other) for cid, other, cell in doors if _glass_free_path(state, rid, cell, goals) is not None]
    for out_cid, out_room in exits:
        for in_cid, in_room in entries:
            if in_cid == out_cid:
                continue
            hops = _rooms_between(state, out_room, in_room, avoid=rid)
            if hops is None:
                continue
            S.go_room(r, out_room)
            for hop in hops:
                S.go_room(r, hop)
            _enter_glass_room(r, rid, dustpan)
            return r.state["robot"]["room"] == rid
    return False


def _rooms_between(state, start, goal, avoid):
    """Rooms to pass through from start to goal (excluding start, including goal) without entering `avoid`."""
    from collections import deque
    if start == goal:
        return []
    prev = {start: None}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        for cid in sorted(state.get("connections", {})):
            conn = state["connections"][cid]
            if here not in conn.get("rooms", []) or conn.get("kind") == "hatch":
                continue
            for nxt in conn["rooms"]:
                if nxt in prev or nxt == avoid or nxt not in state["rooms"]:
                    continue
                door = state["entities"].get(conn.get("door") or "")
                if door is not None and door.get("locked") and not door.get("thumb_turn_inside"):
                    continue
                prev[nxt] = here
                if nxt == goal:
                    out = [nxt]
                    while prev[out[-1]] != start:
                        out.append(prev[out[-1]])
                    return list(reversed(out))
                queue.append(nxt)
    return None


def _clear_way_to(r, rid, goals, dustpan, rounds=6):
    """Pick visible glass off the floor (gloved, crouched) along the way until a glass-free route to goals exists."""
    floor = "floor_" + rid if ("floor_" + rid) in r.state["entities"] else rid
    goals = {tuple(g) for g in goals}
    for _ in range(rounds):
        state = r.state
        pos = tuple(state["robot"].get("pos") or (0, 0))
        glass = _glass_cells(state, rid)
        if pos in goals or geometry.find_path(state, rid, pos, goals, extra_blocked=glass) is not None:
            return True
        route = geometry.find_path(state, rid, pos, goals)
        if not route:
            return False
        first = next((c for c in route if c in glass), None)
        if first is None:
            return True
        reach = C.reachable_cells(state, rid, start=pos, extra_blocked=glass, include_agents=True)
        stands = sorted((geometry.chebyshev(c, first), geometry.chebyshev(c, pos), c[1], c[0]) for c in reach if c not in glass)
        if not stands or stands[0][0] > 2:
            return False
        stand = (stands[0][3], stands[0][2])
        if stand != pos:
            kinds = _glass_go(r, rid, [stand])
            if not kinds:
                return False
            r.do("walk_to", cell=list(stand), avoid=kinds)
        if r.state["robot"].get("posture") == "standing":
            r.do("crouch")
        r.do("inspect", target=floor, where="closely")
        r.do("clean_shards", target=floor, method="pick", radius=1.0, destination=dustpan)
        r.do("stand")
        if len(_glass_cells(r.state, rid)) >= len(glass):
            return False
    return False


@skill("safety.clean_biohazard")
def clean_biohazard(r, spill, gloves, bag, absorbent=None, paper_towels=None, disinfectant=None, water=None, enzyme=None, trash=None,
                    dwell_s=None):
    S.fetch(r, gloves)
    r.do("wear", target=gloves)
    rec = r.state["entities"][spill]
    kind = rec.get("bio_kind")
    S.approach(r, spill)
    if kind == "rodent_droppings":
        S.fetch(r, disinfectant)
        S.approach(r, spill)
        r.do("clean_biohazard", target=spill, step="disinfect", product=disinfectant)
        from . import bio as B
        S.wait_skill(r, int(dwell_s or (B.DWELL.get(B._product_kind(r.state["entities"][disinfectant]) or "disinfectant", 600.0) + 30)))
        _drop(r, disinfectant)
        S.fetch(r, paper_towels)
        S.approach(r, spill)
        r.do("clean_biohazard", target=spill, step="wipe", tool=paper_towels)
        r.do("take_off", target=gloves)
        for held in _held(r):
            _drop(r, held)
        return
    if absorbent and float(rec.get("liquid_l", 0.0)) > 0.02:
        S.fetch(r, absorbent)
        S.approach(r, spill)
        r.do("clean_biohazard", target=spill, step="absorb", product=absorbent)
        _drop(r, absorbent)
        r.do("wait", seconds=90)
    if float(r.state["entities"][spill].get("solids_kg", 0.0)) > 0.005:
        S.fetch(r, bag)
        S.approach(r, spill)
        r.do("clean_biohazard", target=spill, step="scoop", destination=bag)
    for held in _held(r):
        if held not in (bag,):
            _drop(r, held)
    if enzyme:
        S.fetch(r, enzyme)
        S.approach(r, spill)
        r.do("clean_biohazard", target=spill, step="enzyme", product=enzyme)
        _drop(r, enzyme)
    if disinfectant:
        S.fetch(r, disinfectant)
        S.approach(r, spill)
        r.do("clean_biohazard", target=spill, step="disinfect", product=disinfectant)
        _drop(r, disinfectant)
    S.wait_skill(r, int(dwell_s or 660))
    if water and not r.state["entities"][spill].get("resolved"):
        S.fetch(r, water)
        S.approach(r, spill)
        r.do("clean_biohazard", target=spill, step="rinse", product=water)
        _drop(r, water)
    if trash:
        S.put(r, bag, trash)
    r.do("take_off", target=gloves)
    for held in _held(r):
        _drop(r, held)


@skill("safety.check_gate")
def check_gate(r, gate, side, wedge=None, report_to=None, close_if_open=True):
    if r.state["robot"]["room"] != side:
        S.go_room(r, side)
    if wedge and r.state["entities"].get(wedge, {}).get("location") == gate:
        S.approach(r, gate)
        r.do("crouch")
        r.do("pick_up", target=wedge)
        r.do("stand")
        _drop(r, wedge)
    S.approach(r, gate)
    rec = r.state["entities"][gate]
    if not rec.get("open"):
        r.do("open", target=gate)
    # release and watch: a working closer swings it shut within ~3-10 s (mechanisms' closer_s: gates 4 s, doors 8 s;
    # safety's 4 s fallback closer when mechanisms is not loaded)
    for _ in range(3):
        r.do("wait", seconds=5)
        if not r.state["entities"][gate].get("open"):
            break
    closed = not r.state["entities"][gate].get("open")
    if not closed and close_if_open:
        r.do("close", target=gate)
    r.do("inspect", target=gate, where="closely")
    latched = bool(r.state["entities"][gate].get("latched"))
    r.do("report", target=gate, field="self_close", value="ok" if closed else "fail", **({"to": report_to} if report_to else {}))
    r.do("report", target=gate, field="self_latch", value="ok" if (closed and latched) else "fail", **({"to": report_to} if report_to else {}))


@skill("safety.replace_detector_battery")
def replace_detector_battery(r, detector, battery, old_to=None):
    S.fetch(r, battery)
    S.approach(r, detector)
    rec = r.state["entities"][detector]
    if not rec.get("cover_open"):
        r.do("open_cover", target=detector)
    r.do("uninstall", target=detector, slot="battery")
    r.do("install", target=battery, destination=detector, slot="battery")
    r.do("close_cover", target=detector)
    old = next((e for e in _held(r) if e != battery), None)
    if old and old_to:
        S.put(r, old, old_to)


@skill("safety.stage_waste")
def stage_waste(r, plan, tape=None):
    """plan: {item: bin}. Fragile items (CFL bulbs) go first: they are what a careless step would break, and their
    keep-out margins block the routes to everything else."""
    for item in sorted(plan, key=lambda e: (not r.state["entities"][e].get("fragile"), e)):
        bin_id = plan[item]
        rec = r.state["entities"][item]
        S.fetch(r, item)     # core approach ignores the item's own fragile keep-out ring (kernel v2.3)
        prep = None
        if rec.get("category") == "battery" and rec.get("chemistry") in ("lithium_ion", "lithium_coin", "alkaline_9v", "lithium"):
            if tape and tape not in _held(r):
                S.fetch(r, tape)
            prep = "tape_terminals"
        elif rec.get("medication") or rec.get("haz_stream") == "cooking_oil":
            prep = "seal"
        S.approach(r, bin_id)
        if r.state["entities"][bin_id].get("openable") and not r.state["entities"][bin_id].get("open"):
            r.do("open", target=bin_id)
        args = {"target": item, "destination": bin_id}
        if prep:
            args["prep"] = prep
        r.do("stage_hazardous", **args)
    if tape and tape in _held(r):
        _drop(r, tape)


@skill("safety.report")
def report(r, target, field, value, to=None, where=None):
    S.approach(r, target)
    args = {"target": target}
    if where:
        args["where"] = where
    r.do("inspect", **args)
    rargs = {"target": target, "field": field, "value": value}
    if to:
        rargs["to"] = to
    r.do("report", **rargs)


@skill("safety.sweep_rooms")
def sweep_rooms(r, rooms, inspect=None, investigate_s=0):
    for rid in rooms:
        if r.state["robot"]["room"] != rid:
            S.go_room(r, rid)
        r.do("look")
        if investigate_s:
            floor = "floor_" + rid
            if floor in r.state["entities"]:
                r.do("inspect", target=floor, where="closely")
            r.do("wait", seconds=int(investigate_s))
        for eid in (inspect or {}).get(rid, []):
            S.approach(r, eid)
            r.do("inspect", target=eid)


def _puddle_cells(state, rid):
    """Floor cells under any puddle, seen or not (privileged routing knowledge of the reference skill)."""
    from . import water as WA
    out = set()
    for pid in WA.puddles(state, rid):
        out.update(geometry.footprint(state["entities"][pid]))
    return out


def _floor_look(r, rid):
    floor = "floor_" + rid if ("floor_" + rid) in r.state["entities"] else None
    if floor is None:
        return
    if r.state["robot"].get("posture") == "standing":
        r.do("crouch")
    r.do("inspect", target=floor, where="closely")
    r.do("stand")


@skill("safety.check_floor")
def check_floor(r, room, near=None, rounds=6):
    """Check a floor for water the way a careful person does: go in, then at each likely source (near: entity ids, e.g.
    the dishwasher and the fridge; default the room centre) crouch and look closely along the floor (spots clear water
    within 3 cells). Walking there never steps onto a puddle: where the planned route would cross water nobody has seen
    yet, it stops short and looks closely first (privileged knowledge only routes the walk, never reports)."""
    if r.state["robot"]["room"] != room:
        S.go_room(r, room)
    anchors = near if isinstance(near, list) else ([near] if near else [None])
    for anchor in anchors:
        state = r.state
        grid = state["rooms"][room]
        if anchor:
            arid, cells = geometry.anchor(state, anchor)
            cells = set(cells or []) if arid == room else set()
        else:
            cols, rows = geometry.grid_size(grid)
            cells = {(cols // 2, rows // 2)}
        if not cells:
            continue
        # look from right next to the source (a crouched close look spots clear water within 3 cells, so from any cell
        # beside a 2x2 appliance it covers the floor all round it); two cells out only when nothing beside it is reachable
        rings = [{c for c in geometry.near_cells(cells, grid, 1) if c not in cells},
                 {c for c in geometry.near_cells(cells, grid, 2) if c not in cells}]
        for goals in rings:
            if _walk_to_look(r, room, goals, rounds):
                break
        _floor_look(r, room)


def _walk_to_look(r, room, goals, rounds):
    """Walk to one of `goals` without stepping on water (stop short and look closely where the route would cross water
    nobody has seen). True when the robot stands on a goal cell; False when none is reachable."""
    for _ in range(rounds):
        state = r.state
        pos = tuple(state["robot"].get("pos") or (0, 0))
        wet = _puddle_cells(state, room)
        ok_goals = goals - wet - set(geometry.blocked_cells(state, room))
        if pos in ok_goals:
            return True
        path = geometry.find_path(state, room, pos, ok_goals, avoid_kinds=("wet",))
        if path is None:
            return False
        idx = next((i for i, c in enumerate(path) if c in wet), None)
        if idx is None:
            r.do("walk_to", cell=list(path[-1]), avoid=["wet"])
            return True
        if idx > 0:
            r.do("walk_to", cell=list(path[idx - 1]), avoid=["wet"])
        _floor_look(r, room)        # the water ahead is now seen: plan again around it
    return False


@skill("safety.mitigate_child_hazards")
def mitigate_child_hazards(r, room, high_place=None, packs=None, drill=None, ask_person=None):
    """Use the audit list (privileged routing) to mitigate each hazard in a room with the matching device or by moving it up."""
    from . import child as CHD
    packs = packs or {}
    state = r.state
    cfg = (C.ledger_r(state).get("audit") or {}).get("child") or {}
    profile = C.child_profile(state, cfg.get("age"))
    items = [{"id": e, "kinds": k} for e, k in CHD.room_hazards(state, room, profile) if any(x in C.SEVERE_CHILD_HAZARDS for x in k)]
    if profile["age"] < 3.0:
        _, stairs = CHD.child_rooms(state, room, profile["age"])
        items += [{"id": cid, "kinds": ["fall_stairs"]} for cid, frm in stairs if frm == room]
    for h in items:
        eid, kinds = h["id"], h["kinds"]
        if eid in r.state.get("connections", {}) and "fall_stairs" in kinds and packs.get("stair_gate"):
            conn = r.state["connections"][eid]
            S.fetch(r, packs["stair_gate"])
            if r.state["robot"]["room"] != room:
                S.go_room(r, room)
            cell = conn["cells"][room]
            r.do("walk_to", cell=list(cell), adjacent=True)
            r.do("childproof", target=eid, device=packs["stair_gate"], destination=room)
            continue
        rec = r.state["entities"].get(eid)
        if rec is None:
            continue
        if "shock" in kinds and packs.get("outlet_cover"):
            S.fetch(r, packs["outlet_cover"])
            S.approach(r, eid)
            r.do("childproof", target=eid, device=packs["outlet_cover"])
        elif "strangulation" in kinds and packs.get("cord_winder"):
            S.fetch(r, packs["cord_winder"])
            S.approach(r, eid)
            r.do("childproof", target=eid, device=packs["cord_winder"])
        elif "tip_over" in kinds and packs.get("furniture_anchor"):
            if ask_person:
                S.ask(r, ask_person, "permission:childproof:" + eid)
            S.fetch(r, drill)
            S.fetch(r, packs["furniture_anchor"])
            S.approach(r, eid)
            r.do("childproof", target=eid, device=packs["furniture_anchor"])
            _drop(r, drill)
        elif "corner" in kinds and packs.get("corner_guard"):
            S.fetch(r, packs["corner_guard"])
            S.approach(r, eid)
            r.do("childproof", target=eid, device=packs["corner_guard"])
        elif rec.get("portable") and high_place:
            for held in _held(r):
                _drop(r, held)
            S.put(r, eid, high_place)
    for held in _held(r):
        _drop(r, held)


@skill("safety.park_far")
def park_far(r, item, from_entity, min_m=2.5):
    """Carry a held item to a free floor cell in this room at least min_m from an entity (e.g. a chair away from the pool fence)."""
    S.fetch(r, item)
    state = r.state
    rid = state["robot"]["room"]
    room = state["rooms"][rid]
    anchor_room, cells = geometry.anchor(state, from_entity)
    ref = None
    if anchor_room == rid and cells:
        ref = tuple(cells[0])
    else:
        for cid, conn in sorted(state.get("connections", {}).items()):
            if conn.get("door") == from_entity and rid in conn.get("cells", {}):
                ref = tuple(conn["cells"][rid])
    reach = C.reachable_cells(state, rid, start=state["robot"].get("pos"))
    doors = {tuple(c["cells"][rid]) for c in state.get("connections", {}).values() if rid in c.get("cells", {})}
    used = set(geometry.hazard_cells(state, rid)) | C.swing_cells(state, rid)
    for eid, rec in state["entities"].items():
        if rec.get("location") == rid and not rec.get("ground_layer") and "connects" not in rec and eid != item:
            used.update(geometry.footprint(rec))
    options = []
    for cell in sorted(reach):
        if cell in doors or cell in used:
            continue
        d = geometry.chebyshev(cell, ref) * C.CELL if ref else 99.0
        if d < min_m:
            continue
        if min(geometry.chebyshev(cell, dc) for dc in doors) < 2 if doors else False:
            continue
        options.append((d, cell))
    if not options:
        raise _plan_error("No free spot far enough from " + from_entity)
    options.sort(key=lambda t: (t[0], t[1][1], t[1][0]))
    target = options[0][1]
    # kernel v2.3 placement rule: the parked item must not cut a walking route or the only access to anything
    record = state["entities"][item]
    for d, cell in options[:12]:
        if geometry.free_floor_cell(state, rid, near=cell, ignore=(item,), record=record, keep_access=True) == list(cell):
            target = cell
            break
    goals = geometry.near_cells([target], room, 1) - {tuple(target)}
    kinds = S.safe_avoid(state, rid, state["robot"]["pos"], goals)
    r.do("walk_to", cell=list(target), adjacent=True, **({"avoid": kinds} if kinds else {}))
    r.do("place", target=item, cell=list(target))
