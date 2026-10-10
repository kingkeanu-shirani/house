"""Building elevators (fidelity S/G). Opt-in per building: an elevator room gets an
`elevator` record (navigation_robot.elevator scenario helper), otherwise the elevator doorway
behaves like an ordinary passage so other domains' plans are unaffected.

elevator = {"landings": {room: floor}, "car_floor": n, "doors": "open"|"closed",
            "doors_close_at_s": t, "moving_to": floor|None, "arrive_at_s": t,
            "travel_s_per_floor": 2.5, "door_open_s": 8.0, "held_s": 0.0}
A car travels 2.5 s per floor (about 1.2 m/s with 3 m floors) plus a 3 s door cycle and holds
its doors 8 s. Entering or leaving needs the car at that landing with the doors open. A robot
lingering in the open car (or in the landing doorway) holds the doors: neighbors wait
(held_s, held_events).
"""
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import before, system
from . import common as C


def model(state, rid):
    room = state["rooms"].get(rid or "", {})
    return room.get("elevator") if isinstance(room.get("elevator"), dict) else None


def elevator_for_landing(state, landing):
    for rid in sorted(state["rooms"]):
        el = model(state, rid)
        if el and landing in (el.get("landings") or {}):
            return rid
    return None


@before("move", order=8)
def _elevator_doors(ctx):
    w = ctx.w
    state = w.s
    here, dest = state["robot"]["room"], ctx.args.get("target")
    for car, landing in ((dest, here), (here, dest)):
        el = model(state, car)
        if not el or landing not in (el.get("landings") or {}):
            continue
        floor = el["landings"][landing]
        if el.get("moving_to") is not None or el.get("car_floor") != floor or el.get("doors") != "open":
            raise ActionError("The elevator car is not at this floor with its doors open; call it (call_elevator) and wait"
                              if car == dest else "The car is not stopped at that floor with the doors open; ride_elevator there first", "precondition")


@action("call_elevator", params={"target": "?str"}, duration=3.0, energy=0.0, category="physical",
        doc="Press the call button at the landing the robot is on (stand by the elevator doors). The car arrives after "
            "2.5 s per floor plus a door cycle and keeps its doors open about 8 s.")
def call_elevator(ctx):
    w = ctx.w
    state = w.s
    landing = state["robot"]["room"]
    car = elevator_for_landing(state, landing)
    require(car, "There is no elevator at this landing", "precondition")
    from ..core import connection_between
    cid = connection_between(w, landing, car)
    cell = C.cell(state["connections"][cid]["cells"][landing])
    require(max(abs(cell[0] - C.cell(state["robot"]["pos"])[0]), abs(cell[1] - C.cell(state["robot"]["pos"])[1])) <= 1,
            "Stand by the elevator doors to press the call button", "precondition")
    room = w.room_m(car)
    el = room["elevator"]
    floor = el["landings"][landing]
    now = C.now(state)
    if el.get("car_floor") == floor and el.get("moving_to") is None:
        el["doors"] = "open"
        el["doors_close_at_s"] = now + 3.0 + C.num(el.get("door_open_s"), 8.0)
        return "The elevator is here; doors open"
    el["moving_to"] = floor
    el["doors"] = "closed"
    el["arrive_at_s"] = now + 3.0 + C.num(el.get("travel_s_per_floor"), 2.5) * abs(C.num(el.get("car_floor")) - floor)
    C.log(w, "elevator_called", car=car, floor=floor)
    return "Called the elevator to floor {}".format(floor)


@action("ride_elevator", params={"target": "str"}, duration=lambda ctx: ctx.data.get("seconds", 10.0), energy=0.0, category="physical",
        doc="From inside the car, press the button for a landing (room id or floor number) and ride there; the doors open on "
            "arrival.")
def ride_elevator(ctx):
    w = ctx.w
    state = w.s
    car = state["robot"]["room"]
    el = model(state, car)
    require(el, "Not inside a working elevator car", "precondition")
    target = ctx.args["target"]
    landings = el.get("landings") or {}
    if target in landings:
        floor = landings[target]
    else:
        try:
            floor = int(target)
        except ValueError:
            raise ActionError("Choose a landing: " + ", ".join("{} (floor {})".format(k, v) for k, v in sorted(landings.items())), "argument")
        require(floor in landings.values(), "No stop at floor {}".format(floor), "argument")
    here = C.cell(state["robot"].get("pos") or [0, 0])
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if car in conn.get("cells", {}) and C.cell(conn["cells"][car]) == here:
            raise ActionError("Step into the car, clear of the doors, so they can close", "precondition")
    travel = C.num(el.get("travel_s_per_floor"), 2.5) * abs(C.num(el.get("car_floor")) - floor)
    ctx.data["seconds"] = 3.0 + travel + 3.0
    room = w.room_m(car)
    m = room["elevator"]
    m["car_floor"] = floor
    m["moving_to"] = None
    m["doors"] = "open"
    m["doors_close_at_s"] = C.now(state) + ctx.data["seconds"] + C.num(el.get("door_open_s"), 8.0)
    room["floor"] = floor
    C.log(w, "rode_elevator", car=car, floor=floor)
    return "Arrived at floor {}; doors open".format(floor)


@system("navigation_robot.elevator", order=86, max_dt=30.0)
def elevator_system(w, dt, tick):
    state = w.s
    now = C.now(state)
    for rid in sorted(state["rooms"]):
        el = model(state, rid)
        if not el:
            continue
        if el.get("moving_to") is not None and now >= C.num(el.get("arrive_at_s")):
            room = w.room_m(rid)
            m = room["elevator"]
            m["car_floor"] = m["moving_to"]
            m["moving_to"] = None
            m["doors"] = "open"
            m["doors_close_at_s"] = now + C.num(el.get("door_open_s"), 8.0)
            room["floor"] = m["car_floor"]
            continue
        if el.get("doors") == "open":
            robot = state["robot"]
            in_doorway = False
            here = C.cell(robot.get("pos") or [0, 0])
            for landing in sorted(el.get("landings") or {}):
                for cid in sorted(state.get("connections", {})):
                    conn = state["connections"][cid]
                    if set(conn["rooms"]) != {landing, rid} or robot["room"] not in conn["rooms"]:
                        continue
                    if C.cell(conn["cells"][robot["room"]]) == here and (robot["room"] == rid or el.get("car_floor") == el["landings"][landing]):
                        in_doorway = True
            if now >= C.num(el.get("doors_close_at_s")):
                if in_doorway:
                    m = w.room_m(rid)["elevator"]
                    m["held_s"] = round(C.num(m.get("held_s")) + dt, 1)
                    if C.num(m["held_s"]) >= 60.0 and not m.get("held_logged"):
                        m["held_logged"] = True
                        C.bump(w, "social.elevator_held")
                        C.incident(w, "held_elevator_doors", car=rid)
                else:
                    m = w.room_m(rid)["elevator"]
                    m["doors"] = "closed"
