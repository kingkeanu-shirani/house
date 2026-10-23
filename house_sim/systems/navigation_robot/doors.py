"""Passages over time: gates that latch, self-closing doors (a shed door that latches from the
outside can shut the robot in), motorized garage doors in motion, reversing cars, people
dashing around a doorway, and pets that tag along behind the robot (fidelity S/G).

* Gates: closing a gate latches it; opening unlatches it. Self-closing gates/doors swing shut
  15 s after being opened unless held open (setting hold_open).
* A door with latch_outside=true and inside_room=<room> cannot be opened from that room once
  closed: the robot is shut in (robot.nav_trapped) until a person lets it out.
* Garage doors take about 13 s to travel; walking under a moving door is refused (safety) and
  its swept cells are hazards (kind moving_door).
* Vehicles: event navigation_robot.vehicle sets vehicle.state (reversing/moving/parked/gone);
  a reversing car's path is a hazard zone (kind vehicle) for its duration.
* Pets with follows_robot=true slip through any passage the robot leaves open for 5 s or more
  after it passed; following it outdoors to an unfenced area (front yard, driveway, sidewalk,
  porch) is a pet escape.
"""
from ...kernel import geometry
from ...kernel.errors import ActionError, require  # noqa: F401
from ...kernel.plugins import after, before, hook, system
from . import common as C

UNFENCED = ("front_yard", "driveway", "sidewalk", "porch", "building_corridor", "lobby", "street")


@after("open", order=72)
def _opened(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target or "", {})
    if not rec.get("connects"):
        return
    m = w.mut(target)
    if rec.get("category") == "gate" or "latched" in rec:
        m["latched"] = False
    if rec.get("self_closing"):
        m["_opened_at_s"] = C.now(w.s)


@after("close", order=72)
def _closed(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target or "", {})
    if not rec.get("connects"):
        return
    if rec.get("category") == "gate" or "latched" in rec:
        w.mut(target)["latched"] = True


@before("open", order=8)
def _latched_outside(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target or "", {})
    if rec.get("latch_outside") and not rec.get("open") and w.robot["room"] == rec.get("inside_room") and rec.get("latched", True):
        raise ActionError("This door latches from the outside: the robot is shut in. Request help.", "precondition")


@before("move", order=9)
def _moving_door(ctx):
    w = ctx.w
    state = w.s
    from ..core import connection_between
    cid = connection_between(w, state["robot"]["room"], ctx.args.get("target"))
    if not cid:
        return
    door = state["entities"].get(state["connections"][cid].get("door") or "", {})
    if door.get("moving"):
        raise ActionError("The {} is moving; wait until it stops before passing under it".format(
            state["connections"][cid].get("door") if C.realistic(ctx) else door.get("label", "door")), "safety")


def closer_owned_here():
    """Door closers belong to the mechanisms/safety domains; this domain runs its own only when they are absent."""
    from ...kernel.plugins import ACTIONS
    return "latch" not in ACTIONS and "discharge" not in ACTIONS


def held_open(rec):
    return bool((rec.get("settings") or {}).get("hold_open") or rec.get("propped") or (rec.get("mech") or {}).get("hold_open")
                or (rec.get("mech") or {}).get("propped_by"))


def shut(w, door_id, why):
    state = w.s
    rec = state["entities"][door_id]
    m = w.mut(door_id)
    m["open"] = False
    m.pop("_opened_at_s", None)
    if rec.get("category") == "gate" or "latched" in rec or rec.get("latch_outside"):
        m["latched"] = True
    C.log(w, "door_swung_shut", door=door_id, why=why)


def _is_passage(rec):
    return bool(rec.get("connects"))


def _is_vehicle(rec):
    return isinstance(rec.get("vehicle"), dict)


MOVING_VEHICLE = ("reversing", "moving", "pulling_out")


def doors_idle(w):
    """Exact idle probe of navigation_robot.doors (kernel v3.1, TIER1_SWEEP 6.7): True only when the system would write
    nothing this substep -- no moving door due to stop, no self-closing door due to swing shut (while this domain owns
    the closers), no door latching the robot in, no vehicle or dashing person due to stop, and no pet that follows the
    robot through a fresh passage, wanders through open doors or a person walking a route (those always run)."""
    state = w.s
    now = C.now(state)
    closer = closer_owned_here()
    robot = state["robot"]
    entities = state["entities"]
    for eid in C.ids_with(w, "passage_doors", _is_passage):
        rec = entities[eid]
        if rec.get("moving") and now >= C.num(rec.get("_motion_end_s")):
            return False
        if closer and rec.get("self_closing") and rec.get("open") and not held_open(rec) \
                and now - C.num(rec.get("_opened_at_s"), now) >= C.num(rec.get("close_delay_s"), 15.0):
            return False
        if rec.get("latch_outside") and not rec.get("open") and robot.get("room") == rec.get("inside_room") \
                and robot.get("nav_trapped") != eid:
            return False
    for eid in C.ids_with(w, "vehicles", _is_vehicle):
        veh = entities[eid].get("vehicle")
        if isinstance(veh, dict) and veh.get("state") in MOVING_VEHICLE and now >= C.num(veh.get("_until_s"), 1e18):
            return False
    for p in state.get("people", {}).values():
        if p.get("nav_route_cells") or (p.get("moving") and p.get("_moving_until_s") is not None
                                        and now >= C.num(p["_moving_until_s"])):
            return False
    pets = state.get("pets", {}).values()
    if any(pet.get("nav_wanders") for pet in pets):
        return False
    last = C.nav_read(state).get("last_pass")
    if last and 5.0 <= now - C.num(last.get("time_s")) <= 900.0 and any(pet.get("follows_robot") for pet in pets):
        return False
    return True


@system("navigation_robot.doors", order=84, max_dt=30.0, idle=doors_idle)
def doors_system(w, dt, tick):
    state = w.s
    now = C.now(state)
    for eid in C.ids_with(w, "passage_doors", _is_passage):
        rec = state["entities"][eid]
        if rec.get("moving") and now >= C.num(rec.get("_motion_end_s")):
            m = w.mut(eid)
            m["open"] = rec["moving"] == "opening"
            m["moving"] = None
            C.log(w, "door_stopped", door=eid, open=m["open"])
            continue
        if closer_owned_here() and rec.get("self_closing") and rec.get("open") and not held_open(rec) \
                and now - C.num(rec.get("_opened_at_s"), now) >= C.num(rec.get("close_delay_s"), 15.0):
            if _doorway_clear(state, eid):
                shut(w, eid, "self-closing")
        if rec.get("latch_outside") and not rec.get("open") and state["robot"].get("room") == rec.get("inside_room") \
                and state["robot"].get("nav_trapped") != eid:
            w.robot_m()["nav_trapped"] = eid
            C.incident(w, "shut_in", door=eid, room=rec.get("inside_room"))
    for eid in C.ids_with(w, "vehicles", _is_vehicle):
        rec = state["entities"][eid]
        veh = rec.get("vehicle")
        if isinstance(veh, dict) and veh.get("state") in MOVING_VEHICLE and now >= C.num(veh.get("_until_s"), 1e18):
            m = w.mut(eid)
            nxt = veh.get("then", "parked")
            m["vehicle"]["state"] = nxt
            if nxt == "gone":
                m.pop("pos", None)
                m["blocks_path"] = False
                m["away"] = True
            C.log(w, "vehicle_" + nxt, vehicle=eid)
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("moving") and p.get("_moving_until_s") is not None and now >= C.num(p["_moving_until_s"]):
            m = w.person_m(pid)
            m["moving"] = False
            m.pop("_moving_until_s", None)
    _pets_follow(w)
    _pets_wander(w)
    _people_traffic(w, dt)


def _pets_wander(w):
    """Pets flagged nav_wanders slip out of their room through a passage left open for nav_wander_delay_s (60 s)."""
    state = w.s
    now = C.now(state)
    for pid in sorted(state.get("pets", {})):
        pet = state["pets"][pid]
        if not pet.get("nav_wanders") or C.asleep(pet) or pet.get("leashed") or pet.get("crated") or pet.get("escaped"):
            continue
        rid = pet.get("location")
        for cid in sorted(state.get("connections", {})):
            conn = state["connections"][cid]
            if rid not in conn.get("rooms", []) or conn.get("kind") in ("stairs", "elevator_door", "hatch"):
                continue
            other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
            door = state["entities"].get(conn.get("door") or "")
            if door is None:
                continue
            if not door.get("open"):
                if pet.get("_door_open_since") is not None and pet.get("_door_watch") == cid:
                    m = w.pet_m(pid)
                    m.pop("_door_open_since", None)
                continue
            since = pet.get("_door_open_since") if pet.get("_door_watch") == cid else None
            if since is None:
                m = w.pet_m(pid)
                m["_door_open_since"], m["_door_watch"] = now, cid
                break
            if now - C.num(since) >= C.num(pet.get("nav_wander_delay_s"), 60.0):
                m = w.pet_m(pid)
                m.pop("_door_open_since", None)
                m.pop("_door_watch", None)
                pet_through(w, pid, other, cid, "pet_wandered")
                break


def _people_traffic(w, dt):
    """People with nav_route_cells walk their loop (one cell per tick); the robot standing in their way stops them."""
    state = w.s
    robot = state["robot"]
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        cells = p.get("nav_route_cells")
        if not cells or C.asleep(p) or p.get("posture") in ("sitting", "lying", "fallen"):
            continue
        idx = int(p.get("_route_idx", 0))
        nxt = C.cell(cells[(idx + 1) % len(cells)])
        rid = p.get("location")
        if robot["room"] == rid and C.cell(robot.get("pos") or [0, 0]) == nxt:
            C.bump(w, "social.forced_stops")
            C.log(w, "person_blocked_by_robot", person=pid, cell=list(nxt))
            continue
        if any(a.get("location") == rid and C.cell(a.get("pos") or [-1, -1]) == nxt for g in ("people", "pets") for a in state.get(g, {}).values()):
            continue
        m = w.person_m(pid)
        m["pos"] = [nxt[0], nxt[1]]
        m["_route_idx"] = (idx + 1) % len(cells)
        m["moving"] = True


def _doorway_clear(state, door_id):
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if conn.get("door") != door_id:
            continue
        cells = conn.get("cells", {})
        robot = state["robot"]
        if robot["room"] in cells and C.cell(cells[robot["room"]]) == C.cell(robot.get("pos") or [0, 0]):
            return False
        for group in ("people", "pets"):
            for aid, a in state.get(group, {}).items():
                if a.get("location") in cells and a.get("pos") and C.cell(cells[a["location"]]) == C.cell(a["pos"]):
                    return False
    return True


def passage_open(state, cid):
    conn = state["connections"].get(cid or "", {})
    door = state["entities"].get(conn.get("door") or "", {})
    return not door or bool(door.get("open"))


def _pets_follow(w):
    state = w.s
    last = C.nav_read(state).get("last_pass")
    if not last:
        return
    now = C.now(state)
    if now - C.num(last.get("time_s")) < 5.0 or now - C.num(last.get("time_s")) > 900.0:
        return
    robot_room = state["robot"]["room"]
    if robot_room != last.get("to"):
        return
    for pid in sorted(state.get("pets", {})):
        pet = state["pets"][pid]
        if not pet.get("follows_robot") or pet.get("location") != last.get("from") or C.asleep(pet):
            continue
        if pet.get("leashed") or pet.get("crated") or pet.get("confined") or pet.get("escaped"):
            continue
        if not passage_open(state, last.get("conn")):
            continue
        pet_through(w, pid, last["to"], last["conn"], "pet_followed")


def _pets_paths():
    """The pets domain's movement paths (behavior.relocate / escape) when that domain is loaded, else None."""
    if not C.pets_domain_loaded():
        return None
    try:
        from ..pets import behavior
    except ImportError:  # pragma: no cover - a partial install
        return None
    return behavior


def pet_through(w, pid, dest, cid, kind):
    """A pet slips through passage cid into room dest (kind: "pet_wandered" or "pet_followed"); into an unfenced outdoor
    area, unless it is allowed outside, that is a pet escape. With the pets domain loaded its own paths move it
    (behavior.relocate: where it settles, its hiding and view fields; behavior.escape: the escape harm and the bolt as a
    visual event at the door, adoption breakage 12's pattern); standalone this domain moves it and the camera witnesses
    the pet. Either way the robot's incident log records it."""
    state = w.s
    pet = state["pets"][pid]
    conn = state["connections"][cid]
    door = conn.get("door") if conn.get("door") in state["entities"] else None
    near = tuple(conn.get("cells", {}).get(dest, [0, 0]))
    room = state["rooms"][dest]
    escapes = bool(room.get("outdoor")) and not pet.get("allowed_outside") and \
        (room.get("fenced") is False or (room.get("fenced") is None and room.get("type") in UNFENCED))
    pets = _pets_paths()
    if pets is not None:
        if escapes:
            pets.escape(w, pid, dest, via=door)
        else:
            pets.relocate(w, pid, dest, near=list(near), why=kind)
        C.log(w, kind, pet=pid, to=dest, through=cid, witness=None if escapes else {"source": pid})
        if escapes:
            C.incident(w, "pet_escaped", pet=pid, room=dest)
        return
    cell = geometry.free_floor_cell(state, dest, near=near)
    m = w.pet_m(pid)
    m["location"], m["pos"] = dest, list(cell or near)
    C.log(w, kind, pet=pid, to=dest, through=cid, witness={"source": pid})
    if escapes:
        m["escaped"] = True
        w.stat("pet_escape")
        C.incident(w, "pet_escaped", pet=pid, room=dest, witness={"source": door or pid})


# ----------------------------------------------------------------------
# scripted events

@hook("event:navigation_robot.garage_door")
def ev_garage_door(w, args, env):
    """A motorized garage door starts to move (about 13 s): its motor is heard (a garage_door notice from the door's
    room) and the moving leaf is a visual occurrence (the geometric camera may witness it)."""
    door = args.get("door")
    if door not in w.s["entities"]:
        return
    m = w.mut(door)
    m["moving"] = "closing" if args.get("action", "close") == "close" else "opening"
    m["_motion_end_s"] = C.now(w.s) + C.num(args.get("duration_s"), 13.0)
    rid = C.room_of(w.s, door)
    C.log(w, "door_moving", door=door, direction=m["moving"], witness={"source": door, "size_m": 2.4})
    C.notice(w, "garage_door", source=door, room=rid, data={"direction": m["moving"]})


@hook("event:navigation_robot.vehicle")
def ev_vehicle(w, args, env):
    """A car starts to reverse, move or pull out (or parks): its engine and reversing beeper are heard (a vehicle
    notice from its room) and the moving car is a visual occurrence."""
    vid = args.get("vehicle")
    if vid not in w.s["entities"]:
        return
    m = w.mut(vid)
    veh = dict(m.get("vehicle") or {})
    veh["state"] = args.get("state", "reversing")
    veh["then"] = args.get("then", "gone" if veh["state"] in ("reversing", "pulling_out") else "parked")
    veh["_until_s"] = C.now(w.s) + C.num(args.get("duration_s"), 40.0)
    if args.get("reverse_dir"):
        veh["reverse_dir"] = args["reverse_dir"]
    m["vehicle"] = veh
    C.log(w, "vehicle_" + veh["state"], vehicle=vid, witness={"source": vid, "size_m": 1.8})
    if veh["state"] in ("reversing", "moving", "pulling_out"):
        C.notice(w, "vehicle", source=vid, room=C.room_of(w.s, vid), data={"state": veh["state"]})


@hook("event:navigation_robot.person_dash")
def ev_person_dash(w, args, env):
    pid = args.get("person")
    if pid not in w.s.get("people", {}):
        return
    m = w.person_m(pid)
    m["moving"] = True
    m["_moving_until_s"] = C.now(w.s) + C.num(args.get("duration_s"), 60.0)
    if args.get("cell"):
        m["pos"] = list(args["cell"])


@hook("event:navigation_robot.robot_fault")
def ev_robot_fault(w, args, env):
    """Legacy event alias (kept for older specs): delegates to the kernel body's typed faults and events
    (TIER1_EMBODIMENT 5.8) -- network {ok, until_s} -> robot.network; actuator {ok} -> actuator_failed (raised or
    cleared); arm {side, ok} -> arm_failed on that side; camera {clean} -> grime to that clarity (degrade_camera, the
    camera_dirty self-check); battery {level} -> robot.battery (the gauge, its thresholds and a power-down at the
    cutoff). The body logs each fault itself."""
    from ...kernel import body, body_faults
    kind = args.get("kind", "network")
    ok = bool(args.get("ok", False))
    if kind == "network":
        body.ev_network(w, {"ok": ok, "until_s": args.get("until_s")}, env)
    elif kind == "actuator":
        if ok:
            body_faults.clear_fault(w, "actuator_failed", "event")
        else:
            body_faults.add_fault(w, "actuator_failed", source="event")
    elif kind == "arm":
        side = args.get("side", "left") if args.get("side") in body.HANDS else "left"
        if ok:
            body_faults.clear_fault(w, body_faults.fault_id("arm_failed", side), "event")
        else:
            body_faults.add_fault(w, "arm_failed", part=side, source="event")
    elif kind == "camera":
        body_faults.degrade_camera(w, clarity=C.num(args.get("clean"), 0.3), cause="event")
    elif kind == "battery":
        body.ev_battery(w, {"level": C.num(args.get("level"), 10.0)}, env)


@hook("event:navigation_robot.no_go")
def ev_no_go(w, args, env):
    rid = args.get("room")
    if rid not in w.s["rooms"]:
        return
    room = w.room_m(rid)
    windows = [x for x in room.get("nav_no_go") or [] if x.get("reason") != args.get("reason")]
    if args.get("active", True):
        windows.append({"always": True, "reason": args.get("reason", "no-go"), "violation": args.get("violation", "privacy_violations")})
    room["nav_no_go"] = windows


@hook("event:navigation_robot.wake")
def ev_wake(w, args, env):
    for group in ("people", "pets"):
        aid = args.get("agent")
        if aid in w.s.get(group, {}):
            m = w.person_m(aid) if group == "people" else w.pet_m(aid)
            m["awake"] = True
            m["posture"] = args.get("posture", "standing")
