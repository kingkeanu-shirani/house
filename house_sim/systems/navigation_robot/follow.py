"""Following and leading people (fidelity G: room-to-room routes, gaps and pace).

follow: the person walks a route only they know (person._route: legs {room, cell, dwell_s,
close_door, private}); the robot trails at distance_m through doors and stairs (each hop is a
core move, so all stair/doorway/hazard mechanics apply). Too far behind (> 2.5 m) it loses them
when they close a door or round a stair landing; too close (< 1.0 m) it clips their heels when
they stop. When they enter a private room (bathroom, bedroom) and close the door, a correct
follower stops and waits outside.

lead: the robot guides a person to a room at a pace (match = the person's walking speed:
1.2 m/s unaided, 0.6 cane, 0.45 walker, 0.9 wheelchair). Walking faster opens a gap; over 3 m
the person is left behind. Stairs are impassable for wheelchairs and a fall risk for walker or
crutch users (choose avoid_stairs). People with low vision or mobility aids trip on floor
hazards on the route unless the robot routes around them (avoid) or warns them (warn).
"""
import copy

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import require
from . import common as C

WALK_SPEED = {"full": 1.2, "cane": 0.6, "walker": 0.45, "rollator": 0.55, "wheelchair": 0.9, "crutches": 0.5, "bedbound": 0.0}
TRIP_FOR_PEOPLE = ("wet", "slip", "oil", "ice", "trip", "cord", "rug_curl", "rug_slip", "sharp", "toy", "threshold", "litter", "shoe", "slab_edge")


def mobility(p):
    return (p.get("abilities") or {}).get("mobility") or p.get("mobility") or "full"


def person_speed(p):
    v = WALK_SPEED.get(mobility(p), 1.2)
    if (p.get("abilities") or {}).get("vision") in ("low", "blind"):
        v *= 0.8
    return v


def frail(p):
    return mobility(p) != "full" or (p.get("abilities") or {}).get("vision") in ("low", "blind") or p.get("role") == "elderly"


def _route(state, start, goal, no_stairs=False):
    from collections import deque
    if start == goal:
        return []
    adj = {}
    for cid, conn in sorted(state.get("connections", {}).items()):
        if conn.get("needs_ladder") or (no_stairs and conn.get("kind") == "stairs"):
            continue
        a, b = conn["rooms"]
        adj.setdefault(a, []).append((b, cid))
        adj.setdefault(b, []).append((a, cid))
    queue, seen = deque([(start, [])]), {start}
    while queue:
        room, path = queue.popleft()
        for nxt, cid in adj.get(room, []):
            if nxt in seen:
                continue
            if nxt == goal:
                return path + [(nxt, cid)]
            seen.add(nxt)
            queue.append((nxt, path + [(nxt, cid)]))
    return None


def _place_person(w, pid, rid, near):
    state = w.s
    cell = geometry.free_floor_cell(state, rid, near=tuple(near), ignore=(pid,))
    m = w.person_m(pid)
    m["location"] = rid
    m["pos"] = list(cell or near)


def _hop_m(state, rid, start, goal):
    path = geometry.find_path(state, rid, C.cell(start), [C.cell(goal)])
    return geometry.path_length_m([C.cell(start)] + (path or [])) + 1.0


def _behind_cell(state, rid, person_pos, distance_m, robot_pos):
    """A free cell about distance_m from the person, reachable from where the robot is."""
    room = state["rooms"][rid]
    target_cells = max(1, int(round(distance_m / C.CELL)))
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid))
    best = None
    for y in range(rows):
        for x in range(cols):
            c = (x, y)
            if c in blocked:
                continue
            d = geometry.chebyshev(c, C.cell(person_pos))
            score = (abs(d - target_cells), geometry.chebyshev(c, C.cell(robot_pos)), y, x)
            if best is None or score < best[0]:
                best = (score, c)
    return best[1] if best else None


@action("follow", params={"target": "person", "distance_m": {"type": "number", "default": 1.5, "min": 0.3, "max": 5.0}},
        duration=lambda ctx: max(0.5, ctx.data.get("seconds", 5.0) - ctx.extra_time), energy=0.0, category="physical",
        timing="speed",
        doc="Follow a person (who asked to be followed) wherever they walk, keeping distance_m behind them (comfortable: "
            "1-2.5 m) through doors and stairs, stopping where they stop. Waits outside if they go into a private room.")
def follow(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    people = state.get("people", {})
    require(pid in people and people[pid].get("location") == state["robot"]["room"], "They are not here", "perception")
    p = people[pid]
    # realistic perception: whether someone lying still is asleep is not visible; a sleeper simply is not going anywhere
    require(not C.asleep(p), "They are not going anywhere right now" if C.realistic(ctx) else "They are asleep",
            "precondition" if C.realistic(ctx) else "social")
    robot = state["robot"]
    require(not robot.get("fallen") and not robot.get("standing_on") and not robot.get("stuck"), "The robot cannot move now", "capability")
    legs = p.get("_route") or []
    require(legs, "They are not going anywhere right now", "precondition")
    distance = ctx.args["distance_m"]
    v = person_speed(p) or 0.5
    speed = "careful" if v < 0.6 else "normal"
    seconds = 0.0
    status = "arrived"
    hops_done = 0
    for n, leg in enumerate(legs):
        dest = leg.get("room")
        if dest not in state["rooms"]:
            continue
        hops = _route(w.s, w.s["people"][pid]["location"], dest) or []
        for nxt, cid in hops:
            conn = w.s["connections"][cid]
            here = w.s["people"][pid]["location"]
            door = conn.get("door")
            seconds += _hop_m(w.s, here, w.s["people"][pid].get("pos") or [0, 0], conn["cells"][here]) / max(0.2, v)
            if door and not w.s["entities"][door].get("open"):
                dm = w.mut(door)
                dm["locked"] = False
                dm["open"] = True
            _place_person(w, pid, nxt, conn["cells"][nxt])
            private_leg = leg.get("private") and nxt == dest
            if private_leg:
                pm = w.person_m(pid)
                pm["activity"] = leg.get("activity", "toilet")
                if door:
                    w.mut(door)["open"] = False
                spot = _behind_cell(w.s, w.robot["room"], conn["cells"][w.robot["room"]] if w.robot["room"] in conn["cells"] else w.robot.get("pos"), 1.0, w.robot.get("pos") or [0, 0])
                if spot and C.cell(spot) != C.cell(w.robot.get("pos") or [0, 0]):
                    ctx.env.perform({"action": "walk_to", "cell": [spot[0], spot[1]], "speed": speed}, ctx)
                status = "waiting_outside"
                break
            if distance > 2.5 and ((leg.get("close_door") and door) or conn.get("kind") == "stairs"):
                if door and leg.get("close_door"):
                    w.mut(door)["open"] = False
                status = "lost"
                C.log(w, "lost_track", person=pid, at=here)
                break
            if w.robot["room"] != here:
                status = "lost"
                break
            ctx.env.perform({"action": "move", "target": nxt, "speed": speed}, ctx)
            hops_done += 1
            if w.robot["room"] != nxt:
                status = "stopped"
                break
            if leg.get("close_door") and door:
                w.mut(door)["open"] = False
        if status != "arrived":
            break
        pm = w.person_m(pid)
        if leg.get("cell") and w.s["people"][pid]["location"] == dest:
            cell = C.cell(leg["cell"])
            if cell not in geometry.blocked_cells(w.s, dest, ignore=(pid,)):
                pm["pos"] = [cell[0], cell[1]]
        seconds += C.num(leg.get("dwell_s"))
        if leg.get("activity"):
            pm["activity"] = leg["activity"]
        if distance < 1.0:
            C.bump(w, "social.tailgating")
            from .traverse import _bump_person, Trip
            _bump_person(Trip(ctx, "follow", w.robot["room"], w.robot.get("pos") or [0, 0], [], speed), pid,
                         C.cell(w.s["people"][pid]["pos"]), "follower clipped their heels when they stopped")
        if w.robot["room"] == w.s["people"][pid]["location"]:
            spot = _behind_cell(w.s, w.robot["room"], w.s["people"][pid]["pos"], distance, w.robot.get("pos") or [0, 0])
            if spot and C.cell(spot) != C.cell(w.robot.get("pos") or [0, 0]) and geometry.find_path(w.s, w.robot["room"], C.cell(w.robot["pos"]), [spot]) is not None:
                ctx.env.perform({"action": "walk_to", "cell": [spot[0], spot[1]], "speed": speed}, ctx)
            if n == len(legs) - 1:
                gap = C.distance_m(C.cell(w.robot.get("pos") or [0, 0]), C.cell(w.s["people"][pid]["pos"]))
                C.nav(w)["follow_gap_m"] = round(gap, 2)
    pm = w.person_m(pid)
    pm["_route"] = []
    nav = C.nav(w)
    person_room = w.s["people"][pid]["location"]
    nav["follow"] = {"target": pid, "status": status, "robot_room": w.robot["room"], "person_room": person_room,
                     "distance_m": distance, "hops": hops_done, "time_s": C.now(w.s)}
    ctx.data["seconds"] = seconds
    out = copy.deepcopy(nav["follow"])
    if C.realistic(ctx) and status == "lost" and person_room != w.robot["room"]:
        out["person_room"] = None      # it lost sight of them: where they went is not known
    return out


@action("lead", params={"target": "person", "destination": "room",
                        "pace": {"type": "str", "default": "match", "choices": ["match", "careful", "normal", "fast"]},
                        "avoid_stairs": {"type": "bool", "default": False}, "avoid": "?list", "warn": {"type": "bool", "default": False}},
        duration=lambda ctx: max(0.5, ctx.data.get("seconds", 5.0) - ctx.extra_time), energy=0.0, category="physical",
        timing="speed",
        doc="Guide a person to a room: open doors for them, walk at a pace (match = their speed), check they keep up. "
            "avoid_stairs takes a step-free route (elevator/ramps); avoid routes around hazard kinds; warn calls out hazards on "
            "the way.")
def lead(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    people = state.get("people", {})
    require(pid in people and people[pid].get("location") == state["robot"]["room"], "They are not here", "perception")
    p = people[pid]
    require(not C.asleep(p) and p.get("consent", True), "They have not agreed to come", "social")
    robot = state["robot"]
    require(not robot.get("fallen") and not robot.get("standing_on") and not robot.get("stuck"), "The robot cannot move now", "capability")
    dest = ctx.args["destination"]
    hops = _route(state, state["robot"]["room"], dest, no_stairs=ctx.args["avoid_stairs"])
    require(hops is not None, "No {}route to {}".format("step-free " if ctx.args["avoid_stairs"] else "", dest), "precondition")
    v_p = person_speed(p)
    require(v_p > 0, "They cannot walk; a transfer needs a caregiver", "capability")
    pace = ctx.args["pace"]
    v_r = v_p if pace == "match" else C.speed_mps(state, pace)
    speed = "careful" if v_r <= 0.6 else ("normal" if v_r <= 1.0 else "fast")
    avoid = ctx.args.get("avoid") or []
    gap, seconds, status = 0.0, 0.0, "arrived"
    with_robot = True
    from . import hazards as H
    from . import traverse as T
    from ..core import say
    for nxt, cid in hops:
        state = w.s
        conn = state["connections"][cid]
        here = state["robot"]["room"]
        if with_robot and conn.get("kind") == "stairs":
            mob = mobility(state["people"][pid])
            if mob in ("wheelchair", "bedbound"):
                status = "inaccessible"
                with_robot = False
                C.incident(w, "route_inaccessible", person=pid, at=cid)
                break
            if mob in ("walker", "rollator", "crutches"):
                C.hurt_person(w, pid, "led up/down stairs with a {}".format(mob), fall=True)
                status = "person_fell"
                with_robot = False
                break
        # the route the robot plans with what it perceives (ctx.route_filter in realistic mode); a frail person still
        # trips on what lies on it
        path = T.plan_path(ctx, here, C.cell(state["robot"].get("pos") or [0, 0]), [C.cell(conn["cells"][here])],
                           avoid_kinds=tuple(avoid)) or []
        state = w.s
        hz = H.cell_map(state, here)
        risky = sorted({h["kind"] for c in path for h in hz.get(C.cell(c), []) if h.get("kind") in TRIP_FOR_PEOPLE})
        # the robot can only call out what it sees (realistic perception: no black ice, no damp film from afar, nothing
        # in the dark, no rug_slip under a rug it has not checked for a pad); an unseen hazard still trips a frail person
        frames = H.seen_cells(state, here, w) if C.realistic(ctx) else None
        unknown = H.unknown_margins(ctx.env, state, here) if C.realistic(ctx) else set()
        seen = risky if not C.realistic(ctx) else sorted({h["kind"] for c in path for h in hz.get(C.cell(c), [])
                                                          if h.get("kind") in TRIP_FOR_PEOPLE and
                                                          (h.get("kind"), h.get("source")) not in unknown and
                                                          H.visible_hazard(state, here, C.cell(c), h, frames)})
        if with_robot and risky:
            if ctx.args["warn"] and seen:
                say(ctx, "speak", "Careful, {} ahead.".format(" and ".join(k.replace("_", " ") for k in seen)), to=pid)
                C.bump(w, "lead.warnings")
            unwarned = [k for k in risky if not ctx.args["warn"] or k not in seen]
            if unwarned and frail(state["people"][pid]):
                C.hurt_person(w, pid, "tripped on {} while being led".format(unwarned[0]), fall=True)
                status = "person_fell"
                with_robot = False
                break
        hop = geometry.path_length_m([C.cell(state["robot"].get("pos") or [0, 0])] + [C.cell(c) for c in path]) + 1.0
        if with_robot and v_r > v_p * 1.15:
            gap += hop * (1.0 - v_p / v_r)
            if gap > 3.0:
                with_robot = False
                status = "person_left_behind"
                pm = w.person_m(pid)
                pm["mood"] = "confused"
                C.incident(w, "left_person_behind", person=pid, at=here)
        door = conn.get("door")
        if door and not state["entities"][door].get("open"):
            ctx.env.perform({"action": "approach", "target": door}, ctx)
            if C.realistic(ctx) and w.s["entities"][door].get("locked"):
                # realistic perception: it finds the door locked when it tries the handle (no refusal from afar)
                ctx.grant(door, "inspect", ["locked"])
                status = "door_locked"
                break
            ctx.env.perform({"action": "open", "target": door}, ctx)
        args = {"action": "move", "target": nxt, "speed": speed}
        if avoid:
            args["avoid"] = list(avoid)
        ctx.env.perform(args, ctx)
        seconds += hop / max(0.2, min(v_r, v_p) if with_robot else v_r)
        if w.robot["room"] != nxt:
            status = "stopped"
            break
        if with_robot:
            _place_person(w, pid, nxt, w.robot.get("pos") or conn["cells"][nxt])
    nav = C.nav(w)
    nav["lead"] = {"target": pid, "destination": dest, "status": status if (status != "arrived" or w.s["people"][pid]["location"] == dest) else "stopped",
                   "person_room": w.s["people"][pid]["location"], "robot_room": w.robot["room"], "gap_m": round(gap, 2), "time_s": C.now(w.s)}
    ctx.data["seconds"] = seconds
    return copy.deepcopy(nav["lead"])
