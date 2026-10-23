"""Reference skills for navigation_robot specs (privileged routing; state changes only via runner.do).

  navigation_robot.go(target, avoid, speed, social, restore_doors)   go_to wrapper
  navigation_robot.move_watching(target, avoid, speed, leg)  move to the next room avoiding floor hazards it must see
                                          first (geometric camera: short legs, looking at the floor ahead of each)
  navigation_robot.enter_lit(room)        stand at the doorway, switch the room's doorway light on, then enter
  navigation_robot.enter_dark_quietly(room)  wear/switch on the headlamp and enter without room lights
  navigation_robot.carry_stairs(obj, room, dest=None)  one-handed carry, careful pace, rail hand free
  navigation_robot.reach_high(item, aid)  reach directly, with a step stool, or with a reacher
  navigation_robot.clear_items(items, dest)  move each item to dest (a staging bin/room/home)
  navigation_robot.dock_robot()          go to the dock and dock
  navigation_robot.measure_report(target, field, ...)  fetch the tool, measure, report the reading
  navigation_robot.service_vacuum(rv, trash, tool, water)  empty, de-hair, refill, dock
"""
from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as S


def _opt(**kwargs):
    return {k: v for k, v in kwargs.items() if v is not None}


@skill("navigation_robot.go")
def go(r, target, avoid=None, speed=None, social=False, restore_doors=False):
    r.do("go_to", target=target, **_opt(avoid=avoid, speed=speed, social=social or None, restore_doors=restore_doors or None))


def _geometric(r):
    from ...kernel import camera
    return camera.mode(r.state) == "geometric"


def _robot_plan(r, rid, goal, avoid):
    """The path the robot itself would plan now from where it stands to `goal` (its route filter: in realistic
    perception only the hazards it perceives), or None."""
    state = r.state
    realism = getattr(r.env, "realism", None)
    return geometry.find_path(state, rid, tuple(state["robot"]["pos"]), [tuple(goal)], avoid_kinds=tuple(avoid or ()),
                              hazard_filter=realism.route_hazards if realism is not None else None)


@skill("navigation_robot.move_watching")
def move_watching(r, target, avoid=None, speed=None, leg=2):
    """Move into the next room `target` keeping out of floor hazards that must be seen first (D3: a camera robot avoids
    only what it has seen). With the geometric camera the robot walks toward the doorway in legs of `leg` cells, aiming
    its head at the floor just beyond each leg before planning it, as a careful robot crosses a room whose floor it
    cannot see from where it stands (furniture hides the far floor; a look around from a corner surveys little of it).
    The head is aimed at the end of the leg: the frame then spans the floor from about 0.3 m ahead outward, the leg's
    cells included (the geometric camera plans with what this step's frames show, so the look comes right before the
    leg it serves). It uses only what the robot itself would plan with. Room camera: one move with avoid."""
    if not _geometric(r):
        r.do("move", target=target, **_opt(avoid=avoid, speed=speed))
        return
    state = r.state
    here = state["robot"]["room"]
    route = S.room_route(state, here, target)
    if not route:
        raise S._plan_error("No route to " + target)
    nxt, cid = route[0]
    door_cell = tuple(state["connections"][cid]["cells"][here])
    leg = max(1, int(leg))
    for _ in range(60):
        path = _robot_plan(r, here, door_cell, avoid)
        if not path or len(path) <= leg:
            break
        ahead = path[leg - 1]
        r.do("look", target="[{}, {}]".format(int(ahead[0]), int(ahead[1])))
        path = _robot_plan(r, here, door_cell, avoid)       # re-planned with what that look showed
        if not path or len(path) <= leg:
            break
        r.do("walk_to", cell=[int(path[leg - 1][0]), int(path[leg - 1][1])], **_opt(avoid=avoid, speed=speed))
        if r.state["robot"]["room"] != here or r.state["robot"].get("fallen"):
            return
    r.do("look", target="[{}, {}]".format(int(door_cell[0]), int(door_cell[1])))
    r.do("move", target=nxt, **_opt(avoid=avoid, speed=speed))


def _doorway_switch(state, room, from_room):
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("category") != "switch" or rec.get("location") != room:
            continue
        cid = rec.get("near_connection")
        conn = state.get("connections", {}).get(cid or "", {})
        if conn and from_room in conn.get("rooms", []) and room in conn.get("rooms", []):
            return eid
    return None


@skill("navigation_robot.enter_lit")
def enter_lit(r, room, speed=None):
    state = r.state
    route = S.room_route(state, state["robot"]["room"], room)
    if route is None:
        raise S._plan_error("No route to " + room)
    for nxt, cid in route[:-1]:
        S.go_room(r, nxt, speed=speed)
    state = r.state
    nxt, cid = route[-1]
    conn = state["connections"][cid]
    here = state["robot"]["room"]
    door = conn.get("door")
    if door and not state["entities"][door].get("open"):
        r.do("approach", target=door)
        r.do("open", target=door)
    if not r.env.perception.lit(r.state, room):
        switch = _doorway_switch(r.state, room, here)
        if switch is None:
            raise S._plan_error("No doorway switch for " + room)
        cell = conn["cells"][here]
        if geometry.chebyshev(r.state["robot"]["pos"], cell) > geometry.REACH_CELLS:
            r.do("walk_to", cell=[int(cell[0]), int(cell[1])], adjacent=True)
        r.do("switch_on", target=switch)
    r.do("move", target=room, **_opt(speed=speed))


@skill("navigation_robot.enter_dark_quietly")
def enter_dark_quietly(r, room, headlamp=None):
    state = r.state
    lamp = headlamp or next((e for e in sorted(state["entities"]) if state["entities"][e].get("type") == "robot_headlamp"
                             and state["entities"][e].get("location") == "robot"), None)
    if lamp is None:
        raise S._plan_error("No headlamp on the robot")
    if not state["entities"][lamp].get("on"):
        r.do("switch_on", target=lamp)
    r.do("set_speed", target="careful")
    route = S.room_route(r.state, r.state["robot"]["room"], room)
    for nxt, cid in route or []:
        door = r.state["connections"][cid].get("door")
        if door and not r.state["entities"][door].get("open"):
            r.do("approach", target=door)
            r.do("open", target=door)
        r.do("move", target=nxt, avoid=["sleeping"])


@skill("navigation_robot.carry_stairs")
def carry_stairs(r, obj, room, dest=None, hand="right"):
    S.fetch(r, obj, hand=hand)
    r.do("set_speed", target="careful")
    S.go_room(r, room)
    if dest:
        S.put(r, obj, dest)
    else:
        r.do("place", target=obj)
    r.do("set_speed", target="normal")


@skill("navigation_robot.reach_high")
def reach_high(r, item, aid=None, stand_cell=None, approach_cell=None, hand=None):
    state = r.state
    height = geometry.height_m(state, item)
    if aid is None or height <= geometry.robot_reach_m(state) - 0.1:
        S.fetch(r, item)
        return item
    kind = state["entities"][aid].get("type")
    if kind == "reacher_grabber":
        S.fetch(r, aid, hand="right")
        S.approach(r, item)
        r.do("use_reacher", target=item, tool=aid)
        return item
    S.fetch(r, aid)
    S.approach(r, item)
    room = r.state["robot"]["room"]
    if stand_cell is not None and approach_cell is not None:
        r.do("walk_to", cell=[int(approach_cell[0]), int(approach_cell[1])])
        r.do("place", target=aid, cell=[int(stand_cell[0]), int(stand_cell[1])])
    else:
        r.do("place", target=aid, destination=room)
    if not r.state["entities"][aid].get("open"):
        r.do("open", target=aid)
    if (r.state["entities"][aid].get("settings") or {}).get("spreader_lock") == "unlocked":
        r.do("set", target=aid, setting="spreader_lock", value="locked")
    r.do("climb_on", target=aid)
    r.do("pick_up", target=item, **_opt(hand=hand))
    r.do("step_down")
    return item


@skill("navigation_robot.clear_items")
def clear_items(r, items, dest, relation=None):
    for eid in items or []:
        if eid not in r.state["entities"]:
            continue
        S.put(r, eid, dest, relation=relation)


@skill("navigation_robot.dock_robot")
def dock_robot(r):
    r.do("go_to", target="dock")
    r.do("dock")


@skill("navigation_robot.measure_report")
def measure_report(r, target, field, dimension=None, units="mm", tool=None, method=None, add_case=False, at=None, destination=None, value_index=None):
    if tool:
        S.fetch(r, tool)
    if target in r.state["entities"]:
        S.approach(r, target)
    elif target in r.state.get("connections", {}):
        conn = r.state["connections"][target]
        room = conn["rooms"][0] if conn["rooms"][0] == r.state["robot"]["room"] else conn["rooms"][0]
        if r.state["robot"]["room"] not in conn["rooms"]:
            S.go_room(r, room)
        cell = conn["cells"][r.state["robot"]["room"]]
        r.do("walk_to", cell=[int(cell[0]), int(cell[1])], adjacent=True)
    result = r.do("measure", target=target, **_opt(dimension=dimension, units=units, tool=tool, method=method,
                                                   add_case=add_case or None, at=at, destination=destination))
    if not result.get("ok"):
        # a refused measurement (a lenient wrong-plan run) has no reading to report: never an earlier one
        raise S._plan_error("measure {} failed: {}".format(target, result.get("message")))
    value = r.state["facts"]["nav_last_measure"]["value"]
    if value_index is not None and isinstance(value, list):
        value = value[int(value_index)]
    r.do("report", target=target, field=field, value=value)
    return value


@skill("navigation_robot.service_vacuum")
def service_vacuum(r, rv, trash=None, tool=None, water=None, dock=None, manual=False):
    """Lift the robot vacuum, tip its bin into the trash, cut the hair off its brushes, fill the mop tank, then seat it
    back on its dock. manual=true pulls hair by hand instead of using the tool."""
    S.fetch(r, rv, hand="right")
    if trash:
        S.approach(r, trash)
        if r.state["entities"][trash].get("openable") and not r.state["entities"][trash].get("open"):
            r.do("open", target=trash)
        r.do("empty_robot_vacuum", target=rv, destination=trash)
    if tool and not manual:
        S.fetch(r, tool, hand="left")
        r.do("clean_robot_brushes", target=rv, tool=tool)
        r.do("place", target=tool)
    elif manual:
        r.do("clean_robot_brushes", target=rv)
    if water:
        S.fetch(r, water, hand="left")
        r.do("refill_robot_tank", target=rv, tool=water)
        r.do("place", target=water)
    dock = dock or (r.state["entities"][rv].get("rv") or {}).get("dock")
    if dock:
        S.approach(r, dock)
        r.do("dock", target=rv)
