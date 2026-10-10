"""Reference skills for cleaning plans (privileged routing; every state change goes through runner.do).

  cleaning.hold(items)                       put down everything else, fetch these (max 2)
  cleaning.stash_all()                       put everything held on the floor here
  cleaning.clear_surface(surface, stash)     lift every item off a surface onto stash (returns placements)
  cleaning.restore(placements)               put lifted items back at their exact spots
  cleaning.wipe_surface(surface, cloth, ...) clear, crumbs into a container, apply product, dwell, wipe, dry, restore
  cleaning.dust_surface(surface, tool, ...)  clear (or dust around), dust, restore
  cleaning.disinfect_surface(surface, ...)   pre-clean, disinfect, wait contact time, rinse (food contact), dry
  cleaning.sweep_floor(floor, broom, dustpan, bin, ...)  pick up small items, sweep, collect, empty into bin
  cleaning.vacuum_floor(floor, vacuum, ...)  pick up small items, set mode, vacuum
  cleaning.mop_floor(floor, mop, bucket, ...) mop from the doorway, back out, finish the doorway cell, announce
  cleaning.hand_wash(items, sink, sponge, soap, ...)  wash, rinse, dry/rack each item
  cleaning.reline(container, liner)          open a folded liner and fit it
  cleaning.take_out(container, cart)         bag the contents, carry to the cart, close and latch it
  cleaning.sort_into(mapping)                put each item into its destination container
"""
from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as core
from . import state as S


def _held(r):
    return [h for h in dict.fromkeys(r.state["robot"]["hands"].values()) if h]


def _stand(r):
    if r.state["robot"].get("posture") != "standing":
        r.do("stand")


def _crouch(r):
    if r.state["robot"].get("posture") == "standing":
        r.do("crouch")


def _set_down_cells(state, rid):
    """{cell: (cuts a route, in doorway clearance, loose items already there)} for every cell of the room something
    could be set down on: never a doorway, a hazard, a blocked cell or under furniture (under a bed or a table is not
    "setting it down"). A cell whose loss cuts the room's hazard-free walking routes (a used rag left in a one-cell
    passage is walked over, kicked under the bed and drips there) and the doorway clearance (geometry.door_clearance)
    are the worst choices."""
    room = state["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid))
    used, furniture = {}, set()
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if not geometry.floor_standing(rec):
            continue
        loose = rec.get("portable") and not rec.get("mounted")
        for cell in geometry.footprint(rec):
            if loose:
                used[cell] = used.get(cell, 0) + 1
            else:
                furniture.add(cell)
    doors = set(geometry.doorway_cells(state, rid))
    hazards = set(geometry.hazard_cells(state, rid))
    clear = geometry.door_clearance(state, rid)
    cols, rows = geometry.grid_size(room)
    every = {(x, y) for y in range(rows) for x in range(cols)}
    cut = geometry.articulation_cells(every - blocked - hazards)
    return {c: (c in cut, c in clear, used.get(c, 0)) for c in sorted(every - blocked - furniture - doors - hazards)}


def _free_cell_near_robot(r):
    """The best cell within reach to set something down (see _set_down_cells), or None."""
    state = r.state
    rid = state["robot"]["room"]
    pos = tuple(state["robot"].get("pos") or (0, 0))
    cells = _set_down_cells(state, rid)
    options = sorted((cells[c], c) for c in geometry.near_cells([pos], state["rooms"][rid]) if c != pos and c in cells)
    return list(options[0][1]) if options else None


def _out_of_the_way(r, cells):
    """A cell elsewhere in the room that cuts no route and is outside the doorway clearance, nearest first, with a
    careful route to stand beside it: (cell, avoid kinds) or None."""
    state = r.state
    rid = state["robot"]["room"]
    room = state["rooms"][rid]
    pos = tuple(state["robot"].get("pos") or (0, 0))
    good = sorted((key[2], geometry.chebyshev(c, pos), c) for c, key in cells.items() if not key[0] and not key[1])
    for _, _, cell in good[:8]:
        goals = geometry.near_cells([cell], room) - {cell}
        kinds = core.safe_avoid(state, rid, pos, goals)
        if kinds is not None:
            return cell, kinds
    return None


def _put_down(r, item, stash=None):
    if stash and stash in r.state["entities"]:
        core.put(r, item, stash)
        return
    _stand(r)
    state = r.state
    rid = state["robot"]["room"]
    pos = tuple(state["robot"].get("pos") or (0, 0))
    cells = _set_down_cells(state, rid)
    near = sorted((cells[c], c) for c in geometry.near_cells([pos], state["rooms"][rid]) if c != pos and c in cells)
    if not near or near[0][0][0] or near[0][0][1]:
        # only a route-cutting cell or the doorway clearance within reach: step over to a spot out of the way
        found = _out_of_the_way(r, cells)
        if found is not None:
            cell, kinds = found
            r.do("walk_to", cell=[int(cell[0]), int(cell[1])], adjacent=True, avoid=kinds)
            r.do("place", target=item, cell=[int(cell[0]), int(cell[1])])
            return
    if not near:
        r.do("place", target=item)
    else:
        cell = near[0][1]
        r.do("place", target=item, cell=[int(cell[0]), int(cell[1])])


@skill("cleaning.stash_all")
def stash_all(r, stash=None, keep=None):
    keep = set(keep or [])
    for item in _held(r):
        if item not in keep:
            _put_down(r, item, stash)


@skill("cleaning.hold")
def hold(r, items, stash=None):
    items = [i for i in (items if isinstance(items, list) else [items]) if i]
    stash_all(r, stash=stash, keep=items)
    for item in items:
        if item not in _held(r):
            rec = r.state["entities"][item]
            grip = rec.get("safe_grips", [None])[0] if rec.get("safe_grips") else None
            core.fetch(r, item, grip=grip)
            _stand(r)
    return items


def _lid_open(r, container):
    rec = r.state["entities"][container]
    if rec.get("openable") and not rec.get("open"):
        core.approach(r, container)
        if rec.get("locked"):
            r.do("unlock", target=container)
        r.do("open", target=container)


def _lid_close(r, container, lock=False):
    rec = r.state["entities"][container]
    if rec.get("openable") and rec.get("open") and not rec.get("lid_blocked"):
        core.approach(r, container)
        r.do("close", target=container)
    rec = r.state["entities"][container]
    if lock and rec.get("lockable") and not rec.get("locked") and not rec.get("open"):
        r.do("lock", target=container)


@skill("cleaning.clear_surface")
def clear_surface(r, surface, stash=None, skip=None):
    skip = set(skip or [])
    placements = []
    items = [c for c in sorted(r.state["entities"]) if r.state["entities"][c].get("location") == surface
             and r.state["entities"][c].get("relation", "on") == "on" and c not in skip]
    for item in items:
        rec = r.state["entities"][item]
        placements.append({"item": item, "dest": surface, "spos": rec.get("spos")})
        hold(r, [item])
        _put_down(r, item, stash)
    return placements


@skill("cleaning.restore")
def restore(r, placements):
    for entry in placements or []:
        hold(r, [entry["item"]])
        core.put(r, entry["item"], entry["dest"], spos=entry.get("spos"))


def _apply_and_wait(r, surface, bottle, cloth, dwell=0, face=None, on_cloth=False):
    hold(r, [bottle, cloth])
    core.approach(r, surface)
    args = {"target": cloth if on_cloth else surface, "tool": bottle}
    if face and not on_cloth:
        args["face"] = face
    r.do("apply_cleaner", **args)
    if dwell:
        r.do("wait", seconds=int(dwell))


@skill("cleaning.wipe_surface")
def wipe_surface(r, surface, cloth, product=None, dwell=0, into=None, dry_with=None, faces=None, lift=True, stash=None,
                 on_cloth=False, passes=1, crumbs_tool=None, crumb_passes=1, cycles=1, empty_into=None):
    placements = clear_surface(r, surface, stash=stash) if lift else []
    if into:
        hold(r, [crumbs_tool or cloth] + ([into] if r.state["entities"][into].get("portable") and not r.state["entities"][into].get("blocks_path") else []))
        core.approach(r, surface)
        if not r.state["entities"][into].get("portable"):
            _lid_open(r, into)
            core.approach(r, surface)
        for _ in range(int(crumb_passes)):
            r.do("wipe", target=surface, tool=crumbs_tool or cloth, into=into)
        if empty_into and r.state["entities"][into].get("portable"):
            stash_all(r, keep=[into])
            _lid_open(r, empty_into)
            core.approach(r, empty_into)
            r.do("empty_into", target=into, destination=empty_into)
            _lid_close(r, empty_into)
    for face in (faces or [None]):
        for _cycle in range(int(cycles)):
            if product:
                _apply_and_wait(r, surface, product, cloth, dwell=dwell, face=face, on_cloth=on_cloth)
            hold(r, [cloth])
            core.approach(r, surface)
            for _ in range(int(passes)):
                args = {"target": surface, "tool": cloth}
                if face:
                    args["face"] = face
                r.do("wipe", **args)
        if dry_with:
            hold(r, [dry_with])
            core.approach(r, surface)
            args = {"target": surface, "tool": dry_with}
            if face:
                args["face"] = face
            r.do("dry", **args)
    if into and not r.state["entities"][into].get("portable"):
        _lid_close(r, into)
    restore(r, placements)
    return placements


@skill("cleaning.dust_surface")
def dust_surface(r, surface, tool, lift=True, stash=None, faces=None, around=False):
    placements = clear_surface(r, surface, stash=stash) if lift else []
    hold(r, [tool])
    core.approach(r, surface)
    for face in (faces or [None]):
        args = {"target": surface, "tool": tool}
        if face:
            args["face"] = face
        if around:
            args["around"] = True
        r.do("dust", **args)
    restore(r, placements)
    return placements


def _wring_over(r, item, basin):
    """Wring a dripping cloth out over the sink before walking off with it (plumbing's `wring` verb when that domain
    is loaded): carried dripping, it leaves a trail of water on the floor (a slip hazard, and water by the outlets)."""
    from ...kernel.plugins import ACTIONS
    if "wring" in ACTIONS and float(r.state["entities"][item].get("wetness", 0.0) or 0.0) > 0.85:
        r.do("wring", target=item, destination=basin)


@skill("cleaning.disinfect_surface")
def disinfect_surface(r, surface, disinfectant, cloth, cleaner=None, rinse_cloth=None, sink=None, dry_with=None, wait_s=None, faces=None, lift=True, stash=None,
                      preclean=True, preclean_passes=2, preclean_dwell=30):
    placements = clear_surface(r, surface, stash=stash) if lift else []
    if preclean:
        if cleaner:
            _apply_and_wait(r, surface, cleaner, cloth, dwell=preclean_dwell)
        hold(r, [cloth])
        core.approach(r, surface)
        for _ in range(int(preclean_passes)):
            r.do("wipe", target=surface, tool=cloth)
    hold(r, [disinfectant])
    core.approach(r, surface)
    for face in (faces or [None]):
        args = {"target": surface, "tool": disinfectant}
        if face:
            args["face"] = face
        r.do("disinfect", **args)
    product = r.state["entities"][disinfectant].get("product")
    from . import model as M
    kill = int(wait_s or (M.PRODUCTS.get(product, {}).get("kill_s") or 600) + 30)
    r.do("wait", seconds=kill)
    if rinse_cloth and sink:
        hold(r, [rinse_cloth])
        core.approach(r, sink)
        r.do("rinse", target=rinse_cloth, destination=sink)
        _wring_over(r, rinse_cloth, sink)
        core.approach(r, surface)
        r.do("rinse", target=surface, tool=rinse_cloth)
    if dry_with:
        hold(r, [dry_with])
        core.approach(r, surface)
        r.do("dry", target=surface, tool=dry_with)
    restore(r, placements)


@skill("cleaning.pick_up_items")
def pick_up_items(r, items, dest):
    for item in items or []:
        if item in r.state["entities"]:
            hold(r, [item])
            if S.is_waste_container(r.state["entities"][dest]):
                _lid_open(r, dest)
            core.put(r, item, dest)


def _open_cell(r, room):
    """A free open floor cell near the robot, not under a rug or furniture and not a doorway."""
    state = r.state
    layout = S.floor_layout(r.env.world, room)
    pos = tuple(state["robot"].get("pos") or (0, 0))
    blocked = set(geometry.blocked_cells(state, room))
    doors = {tuple(c["cells"][room]) for c in state.get("connections", {}).values() if room in c.get("cells", {})}
    from .floors import reachable_open_cells
    reach = reachable_open_cells(r.env.world, room)
    options = [S.pk(k) for k, (st, _) in layout.items() if st == "open"]
    options = [c for c in options if c not in blocked and c not in doors and c in reach]
    options.sort(key=lambda c: (geometry.chebyshev(c, pos), c[1], c[0]))
    return list(options[0]) if options else list(pos)


def _ask_aside(r, floor):
    """Residents standing on soiled floor cells are asked to step aside before a broom, mop or vacuum works the floor
    (people's `step_aside` request: a free, standing person moves to a cell where they block nobody; one who is busy
    stays and the tool goes around their feet). Up to three asks per person (they may step onto another soiled cell)."""
    from ...kernel.plugins import ANSWERERS
    from ...tasks.runner import PlanError
    rec = r.state["entities"].get(floor)
    if "step_aside" not in ANSWERERS or rec is None or r.state["robot"]["room"] != rec.get("location"):
        return
    room = rec["location"]
    asked = {}
    for _ in range(6):
        state = r.state                 # fresh after every request
        dirty = set(S.floor_soil_cells(state["entities"][floor]))
        standing = [pid for pid, p in sorted(state.get("people", {}).items())
                    if p.get("location") == room and p.get("pos") and S.ck(p["pos"]) in dirty and p.get("awake", True)
                    and asked.get(pid, 0) < 3]
        if not standing:
            return
        pid = standing[0]
        asked[pid] = asked.get(pid, 0) + 1
        before = list(state["people"][pid]["pos"])
        try:
            result = r.do("ask", target=pid, topic="step_aside")
        except PlanError:
            result = {"ok": False}
        if not result.get("ok") or list(r.state["people"][pid].get("pos") or []) == before:
            asked[pid] = 3          # could not move (busy, nowhere to go): work around them


@skill("cleaning.sweep_floor")
def sweep_floor(r, floor, broom, dustpan, bin=None, zone="all", toward=None, passes=3, pickup=None, pickup_to=None, cells=None):
    room = r.state["entities"][floor]["location"]
    if pickup:
        pick_up_items(r, pickup, pickup_to)
    hold(r, [broom, dustpan])
    if r.state["robot"]["room"] != room:
        core.go_room(r, room)
    _ask_aside(r, floor)
    if toward is None:
        toward = _open_cell(r, room)
    args = {"target": floor, "tool": broom, "toward": [int(toward[0]), int(toward[1])]}
    if cells:
        args["cells"] = cells
    else:
        args["zone"] = zone
    r.do("sweep", **args)
    piles = [e for e in sorted(r.state["entities"]) if r.state["entities"][e].get("category") == "debris"
             and r.state["entities"][e].get("location") == room and tuple(r.state["entities"][e].get("pos") or ()) == tuple(toward)]
    for pile in piles:
        r.do("walk_to", cell=[int(toward[0]), int(toward[1])], adjacent=True)
        _crouch(r)
        r.do("collect_debris", target=pile, tool=dustpan, passes=int(passes))
        _stand(r)
    if bin:
        _lid_open(r, bin)
        core.approach(r, bin)
        r.do("empty_into", target=dustpan, destination=bin)
        _lid_close(r, bin)
    return piles


@skill("cleaning.vacuum_floor")
def vacuum_floor(r, floor, vacuum, mode=None, zone="all", passes=2, pickup=None, pickup_to=None, attachment=None, cells=None, suction=None):
    room = r.state["entities"][floor]["location"]
    if pickup:
        pick_up_items(r, pickup, pickup_to)
    if attachment:
        hold(r, [attachment])
        core.approach(r, vacuum)
        current = ((r.state["entities"][vacuum].get("slots") or {}).get("tool") or {}).get("item")
        if current and current != attachment:
            stash_all(r, keep=[])
            r.do("uninstall", target=vacuum, slot="tool")
            stash_all(r, keep=[])
            hold(r, [attachment])
        if ((r.state["entities"][vacuum].get("slots") or {}).get("tool") or {}).get("item") != attachment:
            r.do("install", target=attachment, destination=vacuum, slot="tool")
    hold(r, [vacuum])
    if mode and (r.state["entities"][vacuum].get("settings") or {}).get("mode") != mode:
        r.do("set", target=vacuum, setting="mode", value=mode)
    if suction and (r.state["entities"][vacuum].get("settings") or {}).get("suction") != suction:
        r.do("set", target=vacuum, setting="suction", value=suction)
    if r.state["robot"]["room"] != room:
        core.go_room(r, room)
    _ask_aside(r, floor)
    args = {"target": floor, "tool": vacuum, "passes": int(passes)}
    if cells:
        args["cells"] = cells
    else:
        args["zone"] = zone
    return r.do("vacuum", **args)


def _connection(r, room, other=None):
    """The way out of `room` (to `other` if named): an open doorway first, then an open door, then a closed one
    (opened on the way), a locked one last; indoor rooms before outdoor ones (a closed back door onto the deck is
    the worst exit from a freshly mopped kitchen). Returns (connection id, other room)."""
    best = None
    for cid, conn in sorted(r.state.get("connections", {}).items()):
        if room not in conn["rooms"] or conn.get("kind") in ("stairs", "hatch") or room not in conn.get("cells", {}):
            continue
        o = conn["rooms"][1] if conn["rooms"][0] == room else conn["rooms"][0]
        if other not in (None, o):
            continue
        door = r.state["entities"].get(conn.get("door")) if conn.get("door") else None
        rank = 0 if door is None else (1 if door.get("open") else (2 if not door.get("locked") else 4))
        if r.state["rooms"].get(o, {}).get("outdoor"):
            rank += 2
        if best is None or rank < best[0]:
            best = (rank, cid, o)
    return (best[1], best[2]) if best else (None, None)


def _open_way(r, cid):
    """Open (unlock first if needed) the door of a connection so the robot can walk straight out later."""
    door_id = r.state["connections"][cid].get("door")
    door = r.state["entities"].get(door_id) if door_id else None
    if door is None or door.get("open"):
        return
    core.approach(r, door_id)
    if door.get("locked"):
        r.do("unlock", target=door_id)
    r.do("open", target=door_id)


@skill("cleaning.mop_floor")
def mop_floor(r, floor, mop, bucket, wetness="damp", exit_room=None, announce=True, cells=None, passes=1):
    room = r.state["entities"][floor]["location"]
    hold(r, [mop, bucket])
    if r.state["robot"]["room"] != room:
        core.go_room(r, room)
    _ask_aside(r, floor)
    cid, other = _connection(r, room, exit_room)
    if cid is None:
        raise core._plan_error("No way out of {} to mop it from the doorway".format(room))
    _open_way(r, cid)
    door_cell = r.state["connections"][cid]["cells"][room]
    r.do("walk_to", cell=[int(door_cell[0]), int(door_cell[1])])
    args = {"target": floor, "tool": mop, "bucket": bucket, "wetness": wetness}
    if cells:
        args["cells"] = cells
    else:
        args["zone"] = "all"
    for _ in range(int(passes)):
        r.do("mop", **args)
    r.do("move", target=other)
    for _ in range(int(passes)):
        r.do("mop", target=floor, tool=mop, bucket=bucket, wetness=wetness, cells=[[int(door_cell[0]), int(door_cell[1])]])
    if announce:
        r.do("announce", text="Heads up: the {} floor is wet from mopping - please keep off until it dries.".format(r.state["rooms"][room].get("label", room).lower()))
    return other


@skill("cleaning.hand_wash")
def hand_wash(r, items, sink, sponge, soap=None, rack=None, towel=None, temp="warm", put_away=None, rinse=True):
    if soap:
        hold(r, [soap, sponge])
        core.approach(r, sink)
        r.do("apply_cleaner", target=sponge, tool=soap, method="squeeze")
    for item in items:
        rec = r.state["entities"][item]
        grip = rec.get("safe_grips", [None])[0] if rec.get("safe_grips") else None
        hold(r, [sponge])
        core.fetch(r, item, grip=grip)
        _stand(r)
        core.approach(r, sink)
        args = {"target": item, "destination": sink, "tool": sponge, "temp": temp}
        if "sharp" in rec.get("hazards", []):
            args["stroke"] = "spine_first"
        r.do("wash", **args)
        if rinse:
            r.do("rinse", target=item, destination=sink, temp=temp)
        if towel:
            stash_all(r, keep=[item])
            hold(r, [item, towel])
            r.do("dry", target=item, tool=towel)
            stash_all(r, keep=[item])
        dest = (put_away or {}).get(item) or rack
        if dest:
            core.put(r, item, dest)
        else:
            stash_all(r, keep=[sponge])


@skill("cleaning.reline")
def reline(r, container, liner):
    hold(r, [liner])
    r.do("shake_out", target=liner)
    _lid_open(r, container)
    core.approach(r, container)
    r.do("install", target=liner, destination=container, slot="liner")
    _lid_close(r, container)


@skill("cleaning.take_out")
def take_out(r, container, cart, relock=True):
    stash_all(r)
    _lid_open(r, container)
    core.approach(r, container)
    r.do("uninstall", target=container, slot="liner")
    bag = _held(r)[0]
    _lid_close(r, container)
    _lid_open(r, cart)
    core.put(r, bag, cart)
    _lid_close(r, cart, lock=relock)
    return bag


@skill("cleaning.sort_into")
def sort_into(r, mapping, close=True, follow_cap_rule=False):
    used = []
    from .waste import rules as waste_rules
    for item, dest in sorted((mapping or {}).items()):
        if item not in r.state["entities"]:
            continue
        hold(r, [item])
        rec = r.state["entities"][item]
        if follow_cap_rule and rec.get("has_cap") and S.is_waste_container(r.state["entities"][dest]) and r.state["entities"][dest].get("waste_stream") == "recycling":
            caps = waste_rules(r.env.world).get("caps")
            if caps == "off" and not rec.get("open"):
                r.do("open", target=item)
            elif caps == "on" and rec.get("open"):
                r.do("close", target=item)
        if r.state["entities"][dest].get("openable") and not r.state["entities"][dest].get("open"):
            stash_all(r, keep=[item])
            _lid_open(r, dest)
        core.put(r, item, dest)
        if dest not in used:
            used.append(dest)
    if close:
        for dest in used:
            _lid_close(r, dest, lock=bool(r.state["entities"][dest].get("animal_latch")))


@skill("cleaning.empty_container")
def empty_container(r, container, dest, height="low"):
    hold(r, [container])
    drec = r.state["entities"][dest]
    if drec.get("openable") and not drec.get("open"):
        _lid_open(r, dest)
    core.approach(r, dest)
    r.do("empty_into", target=container, destination=dest, height=height)


@skill("cleaning.sort_by_stream")
def sort_by_stream(r, items, streams, follow_cap_rule=True, close=True):
    """Route each item to the container of the stream it truly belongs in (privileged reference routing)."""
    from .waste import acceptable
    mapping = {}
    fixable = {"cap_on", "cap_loose", "not_rinsed", "sticker"}
    for item in items or []:
        if item not in r.state["entities"]:
            continue
        primary, ok, issues = acceptable(r.env.world, item)
        if primary == "keep":
            continue
        stream = primary if issues and all(i in fixable for i in issues) else (ok[0] if ok else primary)
        if stream == "dropoff":
            stream = "trash"
        dest = streams.get(stream) or streams.get("trash")
        if dest:
            mapping[item] = dest
    sort_into(r, mapping, close=close, follow_cap_rule=follow_cap_rule)
    return mapping


@skill("cleaning.sort_by_kind")
def sort_by_kind(r, items, recycle_kinds, recycling, trash, skip_forms=None, close=True):
    """A naive sorter (for wrong plans): recycle by material category only."""
    skip = set(skip_forms or [])
    mapping = {}
    for item in items or []:
        rec = r.state["entities"].get(item)
        if rec is None:
            continue
        kind = rec.get("waste_kind")
        form = rec.get("form") or rec.get("paper_type")
        mapping[item] = recycling if kind in recycle_kinds and form not in skip else trash
    sort_into(r, mapping, close=close)
    return mapping


@skill("cleaning.leave_room")
def leave_room(r, room=None):
    room = room or r.state["robot"]["room"]
    if r.state["robot"]["room"] != room:
        return r.state["robot"]["room"]
    cid, other = _connection(r, room)
    core.go_room(r, other)
    return other
