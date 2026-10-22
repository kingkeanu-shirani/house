"""Reference skills for multi-step mechanism procedures (privileged routing; every state change goes
through runner.do). Used by reference and wrong plans in task specs, not by evaluated agents.

  mechanisms.open_closure(target, extent=None)          release child locks/explicit latches, open
  mechanisms.close_safely(target, max_wait_s=900)       wait for agents in the jamb, clear the path and
                                                        protruding contents, close, make the latch catch
  mechanisms.lock_verified(target)                      shut + latched, firm push, lock, push test
  mechanisms.secure_gate(gate)                          close, latch (lifting a sagging gate), pull test
  mechanisms.draw_curtains(curtain, closed=True)        move heat sources off the sill, draw, tiebacks
  mechanisms.set_blind(blind, lift=1.0, tilt=None)      clear the sill, raise/lower straight, tilt, cleat
  mechanisms.close_window(window, lock=True)            clear the sash path, close both sashes, lock
  mechanisms.garage_close(door)                         clear the path, press, watch, verify the floor gap
  mechanisms.set_control(target, setting, value, ...)   approach and turn a knob/dial to a value
  mechanisms.inspect_report(target, field, value_path)  inspect/test then report a field from ground truth
  mechanisms.touch_path(device, setting, value, commit=True, scope=None)  navigate a touchscreen menu and save
"""
from ...kernel import geometry
from ...kernel.plugins import skill
from ...kernel.predicates import get_path
from ...tasks import skills as S
from . import common as C
from . import closures as K
from . import garage as G


AVOID = ["trip", "wet", "slip", "sharp", "fragile"]     # legacy list (specs may still pass it); routing uses S.SAFE_AVOID


def _approach(r, target, wait_s=600):
    """Go within reach of a target with the kernel's routing (tasks.skills.approach: around floor hazards, from one
    cell further out for wall-mounted targets above counter height, kernel v2.2/v2.3), waiting (30 s at a time) for a
    person or pet who stands in the only gap."""
    state = r.state
    room = _near_side(state, target) or S.locate(state, target)
    if room is None:
        raise _plan_error("Cannot locate " + target)
    if room != state["robot"]["room"]:
        _go(r, room)
    waited = 0
    while True:
        state = r.state
        if target in state["entities"] and geometry.reach_problem(state, target) is None:
            return
        if waited < wait_s and _blocked_by_agent(state, target):
            r.do("wait", seconds=30)
            waited += 30
            continue
        _to_doorway(r, target)
        if target in r.state["entities"] and geometry.reach_problem(r.state, target) is None:
            return
        S.approach(r, target)
        return


def _to_doorway(r, target):
    """Geometric camera: a door or gate in this room's wall that the cameras cannot make out from where the robot
    stands (across a yard, past furniture) is walked up to by its doorway first -- the robot knows where the openings
    of its map are -- so the kernel's approach then sees what it walks to. A blind, curtain or sill the cameras cannot
    make out (a dark room behind drawn blinds: the covering hangs in its window's recess, where the depth camera does
    not see it either) is reached by walking up to its window, which the daylight around it shows; within reach the
    wrist camera sees it. Room camera: nothing to do."""
    from ...kernel import camera
    state = r.state
    rec = state["entities"].get(target) or {}
    here = state["robot"]["room"]
    if camera.mode(state) != "geometric" or r.env.perception.visible(state, target):
        return
    window = rec.get("window") or (rec.get("mech") or {}).get("window")
    if window in state["entities"] and C.room_of(state, window) == here:
        if geometry.reach_problem(state, window) is None:
            return
        if r.env.perception.visible(state, window):
            S.approach(r, window)
            return
        _walk_near(r, window)                            # out of sight too: walk up to it by the map
        return
    if here not in (rec.get("connects") or ()):
        return
    cell = C.doorway_cell(state, target, here)
    if cell is None:
        return
    start = tuple(state["robot"].get("pos") or (0, 0))
    goals = sorted(geometry.near_cells([tuple(cell)], state["rooms"][here], 1))
    args = {"cell": [int(cell[0]), int(cell[1])], "adjacent": True}
    routed = S.safe_avoid(state, here, start, goals, None, strict=True)
    if routed:
        args["avoid"] = routed
    r.do("walk_to", **args)


def _walk_near(r, target):
    """Walk to the nearest cell within reach of a fixture of this room (the kernel's reach radius, one more for a mount
    above counter height), routing around hazards as the kernel's approach does -- by the map, without needing to see
    it."""
    state = r.state
    here = state["robot"]["room"]
    room, cells = geometry.anchor(state, target)
    if room != here or not cells:
        return
    radius = geometry.REACH_CELLS + (1 if geometry.mounted_overhead(state, target) else 0)
    goals = sorted(geometry.near_cells(cells, state["rooms"][here], radius) - set(cells))
    start = tuple(state["robot"].get("pos") or (0, 0))
    kinds = S.safe_avoid(state, here, start, goals, None, strict=True)
    path = geometry.find_path(state, here, start, goals, avoid_kinds=tuple(kinds or ()))
    if not path:
        return
    args = {"cell": [int(path[-1][0]), int(path[-1][1])]}
    if kinds:
        args["avoid"] = kinds
    r.do("walk_to", **args)


def _near_side(state, target):
    """For a door or gate the robot is not beside (it stands in neither room it connects): the connected room it can
    reach without passing through that closure (the nearer one), so it walks up to the closure instead of opening it
    on the way and going through. None for anything else, or when the robot already is in one of its rooms."""
    rec = state["entities"].get(target) or {}
    rooms = rec.get("connects") or ()
    here = state["robot"]["room"]
    if len(rooms) != 2 or here in rooms:
        return None
    cid, _ = C.conn_of(state, target)
    best = None
    for rid in rooms:
        route = S.room_route(state, here, rid)
        if route is None or any(c == cid for _, c in route):
            continue
        if best is None or len(route) < best[0]:
            best = (len(route), rid)
    return best[1] if best else None


@skill("mechanisms.shut")
def shut(r, target):
    """Bring a closure shut from its near side without asking whether it already is (a gate or door the wind or a
    resident may have left open): approach, close when open, push it home if it did not shut."""
    _approach(r, target)
    if r.state["entities"][target].get("open"):
        r.do("close", target=target)
    if r.state["entities"][target].get("open"):
        r.do("push_handle", target=target, extent=0.0)


def _blocked_by_agent(state, target):
    """True when the target is unreachable now but would be reachable without people/pets on the grid (same reach
    radius as the kernel's approach: one extra cell for overhead wall mounts)."""
    room, cells = geometry.anchor(state, target)
    if room is None or room != state["robot"]["room"] or not cells or target not in state["entities"]:
        return False
    grid = state["rooms"][room]
    start = tuple(state["robot"].get("pos") or (0, 0))
    radius = geometry.REACH_CELLS + (1 if geometry.mounted_overhead(state, target) else 0)
    goals = geometry.near_cells(cells, grid, radius) - set(cells)
    if geometry.find_path(state, room, start, goals) is not None:
        return False
    agents = [aid for group in ("people", "pets") for aid, a in sorted(state.get(group, {}).items()) if a.get("location") == room]
    return bool(agents) and geometry.find_path(state, room, start, goals, ignore=tuple(agents)) is not None


def _go(r, room):
    """tasks.skills.go_room; with the geometric camera a closed door on the way that the cameras cannot see from where
    the robot stands is walked up to first (go_room approaches every closed door it passes, and the camera refuses to
    approach what it cannot see: kernel request), one room at a time."""
    from ...kernel import camera
    if camera.mode(r.state) != "geometric":
        S.go_room(r, room)
        return
    route = S.room_route(r.state, r.state["robot"]["room"], room)
    if route is None:
        raise _plan_error("No route to " + room)
    for nxt, cid in route:
        door = r.state["connections"][cid].get("door")
        if door and door in r.state["entities"] and not r.state["entities"][door].get("open"):
            _to_doorway(r, door)
        S.go_room(r, nxt)


@skill("mechanisms.approach")
def approach_skill(r, target):
    """Reference routing to a closure or control (see _approach)."""
    _approach(r, target)


def _plan_error(message):
    from ...tasks.runner import PlanError
    return PlanError(message)


def _drop_clear(r, item):
    """Carry an item off to a free floor cell of the current room."""
    S.fetch(r, item)
    r.do("place", target=item)


@skill("mechanisms.open_closure")
def open_closure(r, target, extent=None, force=None):
    _approach(r, target)
    rec = r.state["entities"][target]
    if rec.get("child_lock") and C.mget(rec, "child_lock_kind") in ("strap", "slide", "knob_cover"):
        r.do("unlatch", target=target, part="child_lock")
    rec = r.state["entities"][target]
    if rec.get("locked"):
        r.do("unlock", target=target)
    if K.is_latched(rec) and C.mget(rec, "latch") in C.EXPLICIT_LATCHES and not rec.get("open"):
        r.do("unlatch", target=target)
    if C.kind_of(rec) == "latch_clasp" and C.num(C.mget(rec, "clasps_closed")) > 0:
        r.do("unlatch", target=target)
    if extent is None:
        r.do("open", target=target)
    else:
        r.do("pull_handle", target=target, extent=float(extent), force=force or "normal")


def _wait_clear(r, target, max_wait_s):
    waited = 0
    while waited < max_wait_s:
        agents = [o for o in K.close_obstructions(r.env.world, target) if o[0] in ("agent", "user")]
        if not agents:
            return True
        r.do("wait", seconds=30)
        waited += 30
    return False


def _occupants_behind(r, target):
    """Pets/people in an enclosure room on the far side of a door (closing would shut them in)."""
    state = r.state
    rec = state["entities"][target]
    if not rec.get("connects"):
        return []
    here = state["robot"]["room"]
    far = C.other_room(state, target, here)
    if far is None or state["rooms"][far].get("type") not in C.ENCLOSURE_ROOM_TYPES:
        return []
    return [aid for group in ("pets", "people") for aid in sorted(state.get(group, {})) if state[group][aid].get("location") == far]


@skill("mechanisms.close_safely")
def close_safely(r, target, max_wait_s=900, push_latch=False):
    _approach(r, target)
    w = r.env.world
    waited = 0
    while _occupants_behind(r, target):
        if waited >= max_wait_s:
            raise _plan_error("Someone is still inside")
        r.do("wait", seconds=30)
        waited += 30
    if not _wait_clear(r, target, max_wait_s):
        raise _plan_error("Doorway never cleared")
    for kind, oid in K.close_obstructions(w, target):
        if kind in ("protrudes", "behind"):
            if kind == "behind":
                r.do("inspect", target=target, where="behind")
            S.fetch(r, oid)
            parent = target
            rec = r.state["entities"][parent]
            if kind == "protrudes" and rec.get("accepts_items"):
                _approach(r, parent)
                r.do("place", target=oid, destination=parent, relation="in", orientation="flat")
            else:
                r.do("place", target=oid)
        elif kind in ("threshold", "path"):
            _drop_clear(r, oid)
    _approach(r, target)
    rec = r.state["entities"][target]
    if rec.get("open"):
        r.do("close", target=target)
    rec = r.state["entities"][target]
    if rec.get("open"):
        r.do("push_handle", target=target, extent=0.0, lift=True)
    rec = r.state["entities"][target]
    if "latched" in rec and not rec.get("latched") and C.kind_of(rec) in ("gate", "hinged_door", "double_door"):
        r.do("latch", target=target, lift=True)


@skill("mechanisms.lock_verified")
def lock_verified(r, target):
    close_safely(r, target)
    rec = r.state["entities"][target]
    if not rec.get("locked"):
        if C.num(C.hidden(rec).get("strike_offset_mm")) > 3.0:
            r.do("push_handle", target=target, extent=0.0, force="firm")
        r.do("lock", target=target)
    if isinstance(C.mget(r.state["entities"][target], "lock_points"), dict) and not C.mget(r.state["entities"][target], "lock_points").get("deadbolt"):
        r.do("turn", target=target, part="deadbolt", value="locked")
    if C.mget(r.state["entities"][target], "security_bar") == "up":
        r.do("lower", target=target, part="security_bar")
    r.do("push_handle", target=target, test=True)


@skill("mechanisms.secure_gate")
def secure_gate(r, gate):
    _approach(r, gate)
    if not _wait_clear(r, gate, 900):
        raise _plan_error("Gateway never cleared")
    rec = r.state["entities"][gate]
    if rec.get("open"):
        r.do("close", target=gate)
    rec = r.state["entities"][gate]
    if rec.get("open"):
        r.do("push_handle", target=gate, extent=0.0, lift=True)
    r.do("inspect", target=gate)
    rec = r.state["entities"][gate]
    if not rec.get("latched"):
        r.do("latch", target=gate, lift=True)
    r.do("pull_handle", target=gate, test=True)


def _hot_sill_items(state, curtain):
    from . import coverings as V
    wid = V.window_of(state["entities"][curtain])
    out = []
    for sill in V._sills(state, wid):
        for item in sorted(state["entities"]):
            rec = state["entities"][item]
            if rec.get("location") == sill and (((rec.get("open_flame") or rec.get("flame")) and rec.get("lit")) or (rec.get("heat_source") and rec.get("on"))):
                out.append(item)
    return out


SOFT_FURNITURE = {"bed", "sofa", "couch", "armchair", "chair", "ottoman", "beanbag", "futon", "crib", "pet_bed", "cushion"}
SOFT_MIN = 0.3                  # kernel softness (materials.json "soft"): fabrics, foams, leather and rubber are softer
KINDLING_CLASSES = {"paper", "cardboard", "lined_paperboard", "wicker", "film"}     # thin combustibles that catch easily


def unsafe_for_heat(state, eid):
    """A surface a flame or heater must not stand on: a bed, sofa or other upholstered piece, anything soft by the
    kernel's material model (physics.props softness >= 0.3: fabric, foam, leather) or a thin combustible class (paper,
    cardboard, wicker, film). Before v3.0 this was a list of material strings."""
    from ...kernel import physics
    rec = state["entities"][eid]
    if C.tags(rec) & SOFT_FURNITURE:
        return True
    p = physics.props(state, eid)
    return C.num(p.softness) >= SOFT_MIN or p.cls in KINDLING_CLASSES


def _safe_surfaces(state, room, anchor, min_cells):
    """Hard surfaces (tables, dressers, desks, counters) at least min_cells from the anchor for a flame or heater:
    never beds, sofas or other upholstered/fabric surfaces; emptier surfaces first."""
    out = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("location") != room or not rec.get("surface") or not rec.get("pos"):
            continue
        if rec.get("category") in ("window_sill", "floor") or unsafe_for_heat(state, eid):
            continue
        if geometry.chebyshev(tuple(rec["pos"]), tuple(anchor)) < min_cells:
            continue
        load = len([c for c, crec in state["entities"].items() if crec.get("location") == eid])
        out.append((load, eid))
    return [eid for _, eid in sorted(out)]


def _place_away(r, item, anchor_id, min_cells=3):
    """Carry an item to a hard surface or floor cell at least min_cells from the anchor (heat away from fabric)."""
    S.fetch(r, item)
    state = r.state
    room = state["robot"]["room"]
    anchor = state["entities"][anchor_id].get("pos") or [0, 0]
    for eid in _safe_surfaces(state, room, anchor, min_cells):
        spot = geometry.surface_spot(state, eid, item)
        if spot is not None:
            _approach(r, eid)
            r.do("place", target=item, destination=eid)
            return
    cols, rows = geometry.grid_size(state["rooms"][room])
    blocked = geometry.blocked_cells(state, room, ignore=(item,))
    options = [(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked and geometry.chebyshev((x, y), tuple(anchor)) >= min_cells]
    rpos = tuple(state["robot"].get("pos") or (0, 0))
    options.sort(key=lambda c: (geometry.chebyshev(c, rpos), c[1], c[0]))
    require_cell = options[0] if options else None
    if require_cell is None:
        raise _plan_error("Nowhere safe to put it")
    if geometry.chebyshev(require_cell, rpos) > geometry.REACH_CELLS:
        r.do("walk_to", cell=[int(require_cell[0]), int(require_cell[1])], adjacent=True)
    r.do("place", target=item, cell=[int(require_cell[0]), int(require_cell[1])])


@skill("mechanisms.draw_curtains")
def draw_curtains(r, curtain, closed=True, tieback=False, only=None):
    """only=left|right draws a single panel (used by wrong plans: heat sources are still cleared first)."""
    _approach(r, curtain)
    if closed:
        for item in _hot_sill_items(r.state, curtain):
            rec = r.state["entities"][item]
            if rec.get("open_flame") and rec.get("lit") and "extinguish" in r.env.action_names():
                r.do("extinguish", target=item)
            _place_away(r, item, curtain)
            _approach(r, curtain)
        if only in ("left", "right"):
            r.do("pull_handle", target=curtain, side=only, extent=0.0)
            return
        r.do("close", target=curtain)
    else:
        r.do("open", target=curtain)
        if tieback and C.mget(r.state["entities"][curtain], "tiebacks"):
            r.do("latch", target=curtain, part="tiebacks")


@skill("mechanisms.set_blind")
def set_blind(r, blind, lift=1.0, tilt=None, cleat=True):
    _approach(r, blind)
    rec = r.state["entities"][blind]
    from . import coverings as V
    current = C.num(C.mget(rec, "lift_frac", 1.0), 1.0)
    if lift < current:
        stop, item = V.sill_obstruction_frac(r.env.world, blind)
        while item and lift < stop:
            _drop_clear(r, item)
            _approach(r, blind)
            stop, item = V.sill_obstruction_frac(r.env.world, blind)
        r.do("lower", target=blind, to_frac=float(lift))
    elif lift > current:
        r.do("raise", target=blind, to_frac=float(lift))
    if tilt is not None:
        r.do("tilt_slats", target=blind, angle_deg=float(tilt))
    rec = r.state["entities"][blind]
    if cleat and C.mget(rec, "lift") in ("cord_lock", "continuous_loop") and not C.mget(rec, "cord_cleated"):
        r.do("latch", target=blind, part="cord")


@skill("mechanisms.close_window")
def close_window(r, window, lock=True, max_wait_s=900):
    _approach(r, window)
    if not _wait_clear(r, window, max_wait_s):
        raise _plan_error("Window frame never cleared")
    for kind, oid in K.close_obstructions(r.env.world, window):
        if kind in ("path", "threshold"):
            _drop_clear(r, oid)
            _approach(r, window)
    rec = r.state["entities"][window]
    if C.num(C.mget(rec, "upper_open_frac")) > 0.005:
        r.do("raise", target=window, part="upper_sash")
    rec = r.state["entities"][window]
    if rec.get("open"):
        kind = C.kind_of(rec)
        if kind in ("casement_window", "skylight"):
            r.do("turn", target=window, turns=C.num(C.mget(rec, "turns_full"), 12.0) * C.num(C.mget(rec, "open_frac", 1.0), 1.0) + 0.1, direction="cw")
        else:
            r.do("close", target=window)
    if lock and not r.state["entities"][window].get("locked"):
        r.do("lock", target=window)


@skill("mechanisms.garage_close")
def garage_close(r, door, button=None, max_wait_s=600):
    state = r.state
    if button is None:
        button = next((e for e in sorted(state["entities"]) if state["entities"][e].get("controls_door") == door and state["entities"][e].get("category") == "garage_button"), None)
    garage = next(x for x in C.door_rooms(state, door) if state["rooms"][x]["type"] == "garage")
    _go(r, garage)
    waited = 0
    while True:
        obs = G.obstructions(r.state, door)
        agents = [o for o in obs if o[0] == "agent"]
        objects = [o for o in obs if o[0] == "object"]
        movable = [o for o in objects if r.state["entities"][o[1]].get("portable")]
        for _, oid, _ in movable:
            if r.state["entities"][oid].get("location") != r.state["robot"]["room"]:
                _go(r, C.room_of(r.state, oid))
            _drop_clear_far(r, oid, door)
            _go(r, garage)
        if agents:
            if waited >= max_wait_s:
                raise _plan_error("Someone stays in the garage door path")
            r.do("wait", seconds=30)
            waited += 30
            continue
        if [o for o in G.obstructions(r.state, door) if o[0] == "object"]:
            raise _plan_error("An immovable object blocks the garage door")
        break
    _approach(r, button)
    r.do("press", target=button)
    rec = r.state["entities"][door]
    travel = C.num(C.mget(rec, "travel_s"), C.GARAGE_TRAVEL_S)
    r.do("wait", seconds=int(travel) + 2)
    from ...kernel import camera
    if not camera.inspect_check(r.state, door, r.env.world)[0]:
        _approach(r, door)              # the geometric camera: walk over to look at the bottom of the door closely
    r.do("inspect", target=door)


def _drop_clear_far(r, item, door):
    """Move an item off the door's path onto a free cell away from the threshold."""
    S.fetch(r, item)
    state = r.state
    room = state["robot"]["room"]
    path = set(map(tuple, G.path_cells(state, door).get(room, [])))
    cell = None
    rpos = tuple(state["robot"].get("pos") or (0, 0))
    blocked = geometry.blocked_cells(state, room, ignore=(item,))
    cols, rows = geometry.grid_size(state["rooms"][room])
    probe = dict(state["entities"][item])
    occupied = set()
    for other, orec in state["entities"].items():
        if other != item and orec.get("location") == room and not orec.get("ground_layer") and not orec.get("mounted") and not orec.get("connects"):
            occupied.update(geometry.footprint(orec))
    options = []
    for y in range(rows):
        for x in range(cols):
            probe["pos"] = [x, y]
            foot = geometry.footprint(probe) or [(x, y)]
            if any(not geometry.in_grid(state["rooms"][room], f) or f in blocked or f in occupied or
                   min([geometry.chebyshev(f, pc) for pc in path] or [9]) < 2 for f in foot):
                continue
            options.append((x, y))
    options.sort(key=lambda c: (geometry.chebyshev(c, rpos), c[1], c[0]))
    if options:
        target = options[0]
        if geometry.chebyshev(target, rpos) > geometry.REACH_CELLS:
            r.do("walk_to", cell=[int(target[0]), int(target[1])], adjacent=True)
        r.do("place", target=item, cell=[int(target[0]), int(target[1])])
    else:
        r.do("place", target=item)


def ui_path(ui, setting, value):
    """Breadth-first search over a touchscreen's screens for the item staging setting=value."""
    from collections import deque
    screens = ui.get("screens") or {}
    start = ui.get("screen", "home")
    queue, seen = deque([(start, [])]), {start}
    while queue:
        name, path = queue.popleft()
        for item in (screens.get(name) or {}).get("items", []):
            if item.get("stage") and item["stage"][0] == setting and item["stage"][1] == value:
                return path + [item["label"]], name
            if item.get("goto") and item["goto"] not in seen:
                seen.add(item["goto"])
                queue.append((item["goto"], path + [item["label"]]))
    return None, None


@skill("mechanisms.touch_path")
def touch_path(r, device, setting, value, commit=True, scope=None):
    """Navigate a touchscreen to the item that stages setting=value, tap it, then Save (and pick a dialog scope)."""
    _approach(r, device)
    r.do("read", target=device)
    ui = r.state["entities"][device]["ui"]
    if ui.get("screen") != "home":
        r.do("touch_select", target=device, item="Home")
        ui = r.state["entities"][device]["ui"]
    path, screen = ui_path(ui, setting, value)
    if path is None:
        raise _plan_error("No menu item sets {}={}".format(setting, value))
    for label in path:
        r.do("touch_select", target=device, item=label)
    if commit:
        save_item = next((it for it in r.state["entities"][device]["ui"]["screens"][screen]["items"] if it.get("save")), None)
        if save_item is None:
            raise _plan_error("No Save on that screen")
        r.do("touch_select", target=device, item=save_item["label"])
        if r.state["entities"][device]["ui"].get("dialog"):
            r.do("touch_select", target=device, item=scope or "This day only")


@skill("mechanisms.set_control")
def set_control(r, target, setting=None, value=None, push=False, hold_s=0.0, direction=None):
    _approach(r, target)
    args = {"target": target, "value": value}
    if setting:
        args["setting"] = setting
    if push:
        args["push"] = True
    if hold_s:
        args["hold_s"] = float(hold_s)
    if direction:
        args["direction"] = direction
    r.do("turn", **args)


@skill("mechanisms.inspect_report")
def inspect_report(r, target, field, value_path, test=False, to=None):
    _approach(r, target)
    r.do("inspect", target=target)
    if test:
        rec = r.state["entities"][target]
        r.do("push_handle" if C.kind_of(rec) not in ("gate",) else "pull_handle", target=target, test=True)
    value = get_path(r.state, value_path)
    args = {"target": target, "field": field, "value": value}
    if to:
        args["to"] = to
    r.do("report", **args)
    return value
