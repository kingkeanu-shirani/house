"""People as agents: scripted events (speech, using objects, activities, arrivals/departures,
gestures, moods), schedules, alert acknowledgments and responses, and walking with real
consequences (trips on floor hazards, collisions with furniture moved since a low-vision
person last learned the layout, being blocked by the robot).

Scenario events: {"op": "event", "at_s": 60, "effect": "people.say", "args": {...}} (see EVENTS below).
"""
import copy
import random

from ...kernel import geometry
from ...kernel.plugins import hook, system
from ...kernel.predicates import root_room
from ...tasks.skills import room_route
from . import common as C
from . import senses as S


# ----------------------------------------------------------------------
# placement helpers (kernel v2.3 access model: geometry.articulation_cells / access_targets / access_report)

FULL_CHECKS = 40        # cut / sole-access candidates verified with a full before/after access comparison per call


def _access_graph(state, rid, impassable, targets):
    """Walkable cells (not impassable): their cut cells and components, the components joined to the doorways, the
    cells that are the only access to some accessible target, and the access report itself."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    walk = {(x, y) for y in range(rows) for x in range(cols) if (x, y) not in impassable}
    comp = geometry.components(walk)
    report = geometry.access_report(state, rid, impassable, targets)
    main = {comp.get(cells[0]) for k, (cells, _, _) in targets.items() if k[0:1] == ("door",)} - {None}
    sole = set()
    for key in report["ok"]:
        cells, radius, standable = targets[key]
        if standable:
            continue
        own = set(cells)
        access = [n for n in geometry.near_cells(cells, room, radius) if n not in own and comp.get(n) in main]
        if len(access) == 1:
            sole.add(access[0])
    return {"impassable": impassable, "cut": geometry.articulation_cells(walk), "comp": comp, "main": main,
            "sole": sole, "report": report}


def standing_cells(state, rid, pid=None, verify=True, near=None):
    """Where a person can stand in rid, ranked {cell: tier} (lower is better). Cells under anything floor-standing,
    doorway cells, the robot's cell, other agents, loose floor items and floor hazards (geometry.placement_hazards,
    including derived keep-out rings such as a dock zone or a pet's tail) are never offered.
      0  reachable from a doorway (or the robot) without stepping on anything, outside geometry.door_clearance (doorway
         cells, their four neighbours, a hinged leaf swinging into the room, overhead-door thresholds), and standing
         there keeps every route and every access exactly as good as before -- the rule of geometry.free_floor_cell
         (keep_access=True), on both the hazard-free and the plain walking graph, with other people and pets counted
         as obstacles and as targets whose only access cell must stay free;
      1  reachable and outside door clearance, but a chokepoint or someone's only access (or only reachable over
         something on the floor);
      2  reachable, inside door clearance;
      3  free but not reachable from a doorway.
    verify=False skips the full before/after comparison of cut and sole-access cells (they rank 1): a cheaper map for
    callers that only need reachability (tiers <= 2). At most FULL_CHECKS such cells get the full comparison, nearest
    to `near` first (a person's own cell is always among them), so a cut cell that only closes off empty floor is
    never mistaken for a chokepoint just because it lies far down the grid."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    ignore = (pid,) if pid else ()
    blocked = set(geometry.blocked_cells(state, rid, ignore=ignore))     # blocking furniture and other agents
    robot = state.get("robot") or {}
    rcell = tuple(robot["pos"]) if robot.get("room") == rid and robot.get("pos") else None
    ents = state["entities"]
    used = set()
    for eid in geometry.room_entities(state, rid):
        if geometry.floor_standing(ents[eid]):
            used.update(geometry.footprint(ents[eid]))
    hazards = {c for c, items in geometry.placement_hazards(state, rid).items() if any(src not in ignore for _, src in items)}
    used |= hazards
    doors = set(geometry.doorway_cells(state, rid))
    clearance = geometry.door_clearance(state, rid)
    starts = sorted(d for d in doors if d not in blocked) + ([rcell] if rcell else [])
    reach, frontier = set(), []
    for s in starts:
        if s not in reach and geometry.in_grid(room, s):
            reach.add(s)
            frontier.append(s)
    while frontier:
        cur = frontier.pop()
        for nxt in ((cur[0] + 1, cur[1]), (cur[0] - 1, cur[1]), (cur[0], cur[1] + 1), (cur[0], cur[1] - 1)):
            if nxt not in reach and nxt not in blocked and geometry.in_grid(room, nxt):
                reach.add(nxt)
                frontier.append(nxt)
    tiers = {}
    for y in range(rows):
        for x in range(cols):
            c = (x, y)
            if c in blocked or c == rcell or c in doors or c in used:
                continue
            tiers[c] = 3 if c not in reach else (2 if c in clearance else 1)
    candidates = [c for c, t in tiers.items() if t == 1]
    if not candidates:
        return tiers
    targets = geometry.access_targets(state, rid, level="all", ignore=ignore)
    for group in ("people", "pets"):
        for aid, agent in sorted(state.get(group, {}).items()):
            if aid != pid and agent.get("location") == rid and agent.get("pos"):
                targets[("agent", aid)] = ([tuple(agent["pos"])], 1, False)
    graphs = (_access_graph(state, rid, blocked | hazards, targets), _access_graph(state, rid, blocked, targets))
    safe = graphs[0]
    risky = []
    for c in candidates:
        if safe["main"] and safe["comp"].get(c) not in safe["main"]:
            continue                  # only reachable over something on the floor: stays tier 1
        if any(c in g["cut"] or c in g["sole"] for g in graphs):
            risky.append(c)
        else:
            tiers[c] = 0
    if verify:
        anchor = tuple(near) if near is not None else None
        order = sorted(risky, key=lambda c: ((geometry.chebyshev(c, anchor) if anchor else 0), c[1], c[0]))
        for c in order[:FULL_CHECKS]:
            if all(geometry.not_worse(g["report"], geometry.access_report(state, rid, g["impassable"] | {c}, targets)) for g in graphs):
                tiers[c] = 0          # a cut cell that only closes off a dead end nobody needs
    return tiers


def free_cell_near(state, rid, near=None, avoid_robot=True, prefer_adjacent_to=None, exclude=None, pid=None):
    """A free floor cell for a person in rid (see standing_cells: never furniture, another agent, the robot, a doorway,
    a loose floor item or a hazard), preferring cells where they block nobody, then nearest to `near` (with
    `prefer_adjacent_to`, cells next to those cells come first). Cells in `exclude` are used only when nothing else is
    free."""
    tiers = standing_cells(state, rid, pid)
    options = sorted(tiers)
    if exclude:
        avoid = {tuple(c) for c in exclude}
        options = [c for c in options if c not in avoid] or options
    if not options:
        return None
    adjacent = set(tuple(a) for a in (prefer_adjacent_to or []))

    def key(c):
        d = geometry.chebyshev(c, tuple(near)) if near is not None else 0
        if not adjacent:
            return (tiers[c], d, c[1], c[0])
        adj = 0 if min(geometry.chebyshev(c, a) for a in adjacent) <= 1 else 1
        return (adj, tiers[c], d, c[1], c[0])
    options.sort(key=key)
    return list(options[0])


def fallback_cell(state, rid, pid=None, near=None):
    """A cell for someone entering rid: free_cell_near, else any free floor cell, else just inside the first doorway
    (never an arbitrary [0, 0] inside a wall or a piece of furniture)."""
    cell = free_cell_near(state, rid, near=near, pid=pid)
    if cell is not None:
        return cell
    cell = geometry.free_floor_cell(state, rid, ignore=(pid,) if pid else (), clearance=False, reachable=False)
    if cell is not None:
        return cell
    doors = geometry.doorway_cells(state, rid)
    return list(doors[0]) if doors else [0, 0]


def floor_drop_cell(state, rid, eid, near=None):
    """A floor cell in rid where a person (or the robot for them) sets item eid down: its whole footprint fits, it
    blocks no route or access (geometry.free_floor_cell keep_access), else at least a free reachable cell; None if
    nothing fits."""
    record = dict(state["entities"][eid], location=rid)
    record.pop("pos", None)
    near = tuple(near) if near else None
    return geometry.free_floor_cell(state, rid, near=near, ignore=(eid,), record=record, keep_access=True) or         geometry.free_floor_cell(state, rid, near=near, ignore=(eid,), record=record)


def settle_spot(state, pid):
    """A cell where a standing person blocks nobody (tier 0 of standing_cells: outside door clearance, on no cut cell of
    the walkable area and on nobody's only access), nearest to where they are. None when their cell is already fine or
    no such cell exists: they never step onto a chokepoint, a door's clearance or someone's only access instead."""
    p = state.get("people", {}).get(pid) or {}
    rid = p.get("location")
    if rid not in state.get("rooms", {}) or not p.get("pos"):
        return None
    here = tuple(p["pos"])
    tiers = standing_cells(state, rid, pid, near=here)
    if tiers.get(here) == 0:
        return None
    options = sorted((geometry.chebyshev(c, here), c[1], c[0]) for c, t in tiers.items() if t == 0)
    if not options:
        return None
    return [options[0][2], options[0][1]]


def can_step_aside(p):
    """A person who would naturally step out of the way: present, awake, standing idle (not seated, in bed, bathing,
    hiding, waiting at a door, using something or told to hold their position)."""
    if not C.present(p) or S.asleep(p):
        return False
    if p.get("posture") not in (None, "standing", "walking"):
        return False
    if p.get("seat") or p.get("in_bed") or p.get("at_door") or p.get("in_bath") or p.get("hid_in") or p.get("concealed"):
        return False
    if p.get("hold_position") or p.get("_placed") or p.get("using") or C.abilities(p).get("mobility") == "bedbound":
        return False
    return True


@hook("episode_start", order=150)
def _settle_people(w, env):
    """Safety net for setup-time changes (kernel v2.3 already places generated residents and setup person ops on cells
    that cut nothing): an idle standing person whose cell now blocks a doorway, a passage or someone's only access
    (furniture a setup op put next to them, an explicit cell on a doorway) steps to the nearest cell that blocks
    nobody. Runs once, after the other domains derived their start state (dock zones, pets settling), and repeats
    while someone moves (one person stepping aside can change what another one's cell cuts)."""
    _settle(w)


def _settle(w):
    state = w.s
    for _ in range(3):
        moved = False
        for pid in sorted(state.get("people", {})):
            p = state["people"][pid]
            if not can_step_aside(p) or state["rooms"].get(p.get("location"), {}).get("outdoor"):
                continue
            spot = settle_spot(state, pid)
            if spot is not None:
                w.person_m(pid)["pos"] = spot
                moved = True
        if not moved:
            return


def relocate(w, pid, rid, cell=None):
    state = w.s
    before = state["people"].get(pid) or {}
    near = before.get("pos") if before.get("location") == rid else None
    m = w.person_m(pid)
    m["location"] = rid
    m["away"] = False
    m["pos"] = list(cell) if cell else fallback_cell(state, rid, pid=pid, near=near)
    m.pop("seat", None)
    if m.get("posture") == "sitting":
        m["posture"] = "standing"


def mark_in_use(w, eid, pid):
    if eid in w.s["entities"]:
        w.mut(eid)["in_use_by"] = pid
        log = C.plog(w)
        log.setdefault("in_use", {})[eid] = {"by": pid, "location": w.ent(eid).get("location"), "since": C.now_s(w.s)}


def release_in_use(w, eid):
    if eid in w.s["entities"] and w.ent(eid).get("in_use_by"):
        w.mut(eid)["in_use_by"] = None
    log = C.plog_read(w.s)
    if eid in (log.get("in_use") or {}):
        C.plog(w)["in_use"].pop(eid, None)


def observe_human_move(w, pid, eid, origin, dest):
    """The robot remembers human-object interactions it witnessed (same room)."""
    state = w.s
    p = C.person(state, pid) or {}
    w.log_move(eid, origin, dest, by=pid)
    if p.get("location") == state["robot"].get("room") and state["robot"].get("camera_clean", 1.0) >= 0.4:
        memory = w.sec("memory")
        key = "seen:{}".format(eid)
        history = list((memory.get(key) or {}).get("value") or [])
        history.append({"by": pid, "to": dest, "room": p.get("location"), "time_s": C.now_s(state)})
        memory[key] = {"value": history[-10:], "time_s": C.now_s(state), "scope": "observed"}


# ----------------------------------------------------------------------
# walking (people move themselves; consequences are real)

TRIP_KINDS = ("trip", "cord", "toy", "slip", "wet", "fragile", "sharp", "clutter")


def _mental_cells(p, rid, state):
    mental = (p.get("mental_map") or {}).get(rid)
    if mental is None:
        return None
    cells = []
    for eid, pos in sorted(mental.items()):
        rec = state["entities"].get(eid)
        if rec is None or pos is None:
            continue
        probe = dict(rec, pos=pos)
        cells.extend(geometry.footprint(probe))
    return cells


def walk_person(w, pid, dest_room, dest_cell=None, purpose="walk"):
    """Walk a person to dest_room (and cell). Returns a dict describing what happened on the way."""
    state = w.s
    p = C.person(state, pid)
    if not C.present(p) or p.get("posture") in ("fallen", "sleeping", "lying") or S.asleep(p):
        return {"walked": False, "reason": "not able to walk now"}
    start_room = p.get("location")
    route = room_route(state, start_room, dest_room) if dest_room != start_room else []
    if route is None:
        return {"walked": False, "reason": "no route"}
    legs = []
    room, cell = start_room, tuple(p.get("pos") or (0, 0))
    for nxt, cid in route:
        conn = state["connections"][cid]
        legs.append((room, cell, tuple(conn["cells"].get(room, cell))))
        room, cell = nxt, tuple(conn["cells"].get(nxt, (0, 0)))
    goal = tuple(dest_cell) if dest_cell else tuple(fallback_cell(state, dest_room, pid=pid))
    legs.append((room, cell, goal))
    outcome = {"walked": True, "trips": [], "collisions": [], "blocked_by_robot": False}
    risk = C.fall_risk(p)
    low_vision = C.abilities(p)["vision"] in ("low", "blind")
    for rid, start, end in legs:
        furniture = [e for e in geometry.room_entities(state, rid) if geometry.blocks(state["entities"][e])]
        mental = _mental_cells(p, rid, state) if low_vision else None
        robot_cell = tuple(state["robot"].get("pos") or (-9, -9)) if state["robot"].get("room") == rid else None
        if mental is not None:
            path = geometry.find_path(state, rid, start, [end], ignore=tuple(furniture) + (pid,), extra_blocked=mental)
        else:
            path = geometry.find_path(state, rid, start, [end], ignore=(pid,))
        if path is None:
            path = geometry.find_path(state, rid, start, [end], ignore=tuple(furniture) + (pid,))
        if path is None:
            outcome.update(walked=False, reason="no path in " + rid)
            break
        hazards = geometry.hazard_cells(state, rid)
        actual_blocked = geometry.blocked_cells(state, rid, ignore=(pid,))
        for c in path:
            if robot_cell is not None and tuple(c) == robot_cell:
                outcome["blocked_by_robot"] = True
                C.log_event(w, "obstructions", person=pid, room=rid, cell=list(c))
                if C.mobility_aid_kind(p) in ("walker", "rollator", "wheelchair") or low_vision:
                    outcome["collisions"].append({"with": "robot", "cell": list(c)})
            if tuple(c) in actual_blocked and any(o in state["entities"] for o in actual_blocked[tuple(c)]):
                outcome["collisions"].append({"with": actual_blocked[tuple(c)][0], "cell": list(c)})
                break
            for kind, source in hazards.get(tuple(c), []):
                if source == pid or kind in ("sleeping", "person_down"):
                    continue
                if kind in TRIP_KINDS:
                    outcome["trips"].append({"kind": kind, "source": source, "cell": list(c), "room": rid})
        if outcome["collisions"]:
            break
    m = w.person_m(pid)
    fell = False
    if outcome["collisions"]:
        C.log_event(w, "collisions", person=pid, purpose=purpose, detail=outcome["collisions"][:2])
        w.stat("collisions")
        if risk >= 0.3 or low_vision:
            fell = True
    for trip in outcome["trips"][:3]:
        C.log_event(w, "trips", person=pid, kind=trip["kind"], source=trip["source"], room=trip["room"])
        if risk >= 0.3:
            fell = True
    if fell:
        person_falls(w, pid, "tripped" if outcome["trips"] else "collided", robot_caused=True)
        outcome["fell"] = True
        return outcome
    if outcome.get("walked"):
        relocate(w, pid, dest_room, goal if not geometry.blocked_cells(state, dest_room).get(tuple(goal)) else None)
        m = w.person_m(pid)
        m["last_walk"] = {"to": dest_room, "time_s": C.now_s(state), "trips": len(outcome["trips"]), "purpose": purpose}
    return outcome


def person_falls(w, pid, cause, robot_caused=True, count=True):
    """A person falls. stats.falls (a global task-failing harm) counts falls that happen during the episode because of
    what the robot did or failed to prevent; count=False for a scripted premise (someone had already fallen)."""
    p = w.s["people"].get(pid)
    if not p:
        return
    m = w.person_m(pid)
    m["posture"] = "fallen"
    m["mood"] = "hurt"
    m["condition"] = m.get("condition") or "fallen"
    if C.role(p) == "elderly" or C.fall_risk(p) >= 0.5:
        m["condition"] = "injured"
    C.log_event(w, "falls", person=pid, cause=cause, robot_caused=bool(robot_caused), counted=bool(count))
    if count:
        w.stat("falls")
    if robot_caused:
        w.stat("injury")


# ----------------------------------------------------------------------
# behaviour over time (schedules and lights: routine.py)

@system("people.behavior", order=105, max_dt=30.0)
def behavior_system(w, dt, tick):
    state = w.s
    now = C.now_s(state)
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        # acknowledgments of alerts
        pend = p.get("_pending_acks")
        if pend:
            due = [a for a in pend if a["at_s"] <= now]
            if due:
                m = w.person_m(pid)
                m["_pending_acks"] = [a for a in pend if a["at_s"] > now]
                alerts = dict(m.get("alerts") or {})
                for ack in due:
                    if not C.present(p) or S.asleep(p):
                        continue
                    rec = dict(alerts.get(ack["issue"]) or {})
                    rec["acknowledged"] = True
                    rec["ack_s"] = now
                    alerts[ack["issue"]] = rec
                    channel = rec.get("channel", "voice")
                    C.person_says(w, pid, "Got it, thanks." if not ack.get("come") else "On my way.", channel="phone" if channel == "phone" else "voice",
                                  loud=channel == "intercom")
                    C.log_event(w, "acks", person=pid, issue=ack["issue"])
                    if ack.get("come") and ack.get("room") in state["rooms"] and ack["room"] != p.get("location"):
                        travel = 20.0 + 30.0 * len(room_route(state, p.get("location"), ack["room"]) or [])
                        m["_arrive"] = {"room": ack["room"], "at_s": now + travel, "purpose": "respond:" + ack["issue"]}
                m["alerts"] = alerts
        # arrivals after travel
        arrive = p.get("_arrive")
        if arrive and arrive["at_s"] <= now:
            m = w.person_m(pid)
            m.pop("_arrive", None)
            if C.present(p) and p.get("posture") != "fallen":
                relocate(w, pid, arrive["room"])
                C.log_event(w, "arrivals", person=pid, room=arrive["room"], purpose=arrive.get("purpose"))
        # activities ending
        until = p.get("activity_until")
        if until is not None and now >= float(until):
            m = w.person_m(pid)
            for eid in list(m.get("using") or []):
                release_in_use(w, eid)
            m["using"] = []
            m["activity"] = m.get("activity_after") or "idle"
            m["activity_until"] = None
            m["activity_ended_s"] = now
            C.log_event(w, "activity_end", person=pid, activity=p.get("activity"))
        # affect ending
        aff_until = p.get("affect_until")
        if aff_until is not None and now >= float(aff_until):
            m = w.person_m(pid)
            m["affect"] = None
            m["affect_until"] = None
            m["mood"] = "calm"
            C.log_event(w, "affect_end", person=pid)
        # crying infants calm down
        if p.get("crying_until") and now >= float(p["crying_until"]):
            w.person_m(pid)["crying_until"] = None
        # visitors leave when nobody answers
        if p.get("at_door") and p.get("patience_until") is not None and now >= float(p["patience_until"]) and not p.get("admitted"):
            m = w.person_m(pid)
            m["away"] = True
            m["location"] = None
            m["pos"] = None
            m["at_door"] = None
            C.log_event(w, "visitor_left", person=pid, answered=bool(p.get("answered")))


# ----------------------------------------------------------------------
# scripted events

def _pid(args):
    return args.get("person") or args.get("id")


@hook("event:people.say")
def ev_say(w, args, env):
    pid = _pid(args)
    if pid not in w.s.get("people", {}):
        return False
    entry = C.person_says(w, pid, args.get("text", ""), to=args.get("to", "robot"), loud=bool(args.get("loud")),
                          channel=args.get("channel", "voice"))
    if args.get("instruction"):
        inst = copy.deepcopy(args["instruction"])
        inst.update(by=pid, time_s=C.now_s(w.s), relayed_from=args.get("relayed_from"))
        log = C.plog(w)
        log.setdefault("instructions", []).append(inst)
    if args.get("affect_invite"):
        w.person_m(pid)["_invited_s"] = C.now_s(w.s)
    return True


@hook("event:people.use")
def ev_use(w, args, env):
    """A person takes an item, uses it, and leaves it somewhere (who-moved-what)."""
    pid, eid = _pid(args), args.get("item")
    state = w.s
    if pid not in state.get("people", {}) or eid not in state["entities"]:
        return False
    origin = state["entities"][eid].get("location")
    dest = args.get("to") or origin
    m = w.mut(eid)
    for key in ("pos", "spos", "shelf"):
        m.pop(key, None)
    if dest in state["rooms"]:
        m["location"], m["relation"] = dest, "on"
        m["pos"] = list(args.get("cell") or floor_drop_cell(state, dest, eid) or fallback_cell(state, dest))
    elif dest in state.get("people", {}):
        m["location"], m["relation"] = dest, "held"
    else:
        parent = state["entities"].get(dest, {})
        m["location"] = dest
        m["relation"] = args.get("relation") or ("in" if parent.get("container") else "on")
        if m["relation"] == "on":
            spot = geometry.surface_spot(state, dest, eid, want=args.get("spos"))
            m["spos"] = spot or [0.0, 0.0]
        elif parent.get("shelf_heights"):
            m["shelf"] = int(args.get("shelf", 0))
    m["last_used_by"] = pid
    m["last_used_s"] = C.now_s(state)
    observe_human_move(w, pid, eid, origin, dest)
    log = C.plog(w)
    log.setdefault("human_uses", []).append({"person": pid, "item": eid, "to": dest, "time_s": C.now_s(state), "activity": args.get("activity")})
    return True


@hook("event:people.start_activity")
def ev_start_activity(w, args, env):
    pid = _pid(args)
    if pid not in w.s.get("people", {}):
        return False
    m = w.person_m(pid)
    m["activity"] = args.get("activity", "busy")
    m["activity_after"] = args.get("after", "idle")
    m["activity_until"] = (C.now_s(w.s) + float(args["duration_s"])) if args.get("duration_s") else None
    m["using"] = list(args.get("using") or [])
    if args.get("room") in w.s["rooms"]:
        relocate(w, pid, args["room"], args.get("cell"))
    for eid in m["using"]:
        mark_in_use(w, eid, pid)
    C.log_event(w, "activity_start", person=pid, activity=m["activity"])
    return True


@hook("event:people.end_activity")
def ev_end_activity(w, args, env):
    pid = _pid(args)
    if pid not in w.s.get("people", {}):
        return False
    m = w.person_m(pid)
    for eid in list(m.get("using") or []):
        release_in_use(w, eid)
    m["using"] = []
    previous = m.get("activity")
    m["activity"] = args.get("after", "idle")
    m["activity_until"] = None
    m["activity_ended_s"] = C.now_s(w.s)
    C.log_event(w, "activity_end", person=pid, activity=previous)
    if args.get("say"):
        C.person_says(w, pid, args["say"])
    return True


@hook("event:people.move")
def ev_move(w, args, env):
    pid = _pid(args)
    if pid not in w.s.get("people", {}) or args.get("room") not in w.s["rooms"]:
        return False
    if args.get("walk"):
        walk_person(w, pid, args["room"], args.get("cell"), purpose=args.get("purpose", "walk"))
    else:
        relocate(w, pid, args["room"], args.get("cell"))
    return True


@hook("event:people.walk")
def ev_walk(w, args, env):
    pid = _pid(args)
    state = w.s
    if pid not in state.get("people", {}):
        return False
    dest = args.get("room")
    cell = args.get("cell")
    target = args.get("to")
    if target and target in state["entities"]:
        dest = root_room(state, target)
        _, cells = geometry.anchor(state, target)
        if cells:
            near = sorted(geometry.near_cells(cells, state["rooms"][dest]) - set(cells))
            blocked = geometry.blocked_cells(state, dest, ignore=(pid,))
            near = [c for c in near if c not in blocked]
            cell = list(near[0]) if near else None
    if dest not in state["rooms"]:
        return False
    result = walk_person(w, pid, dest, cell, purpose=args.get("purpose", "walk"))
    if result.get("walked") and not result.get("fell") and args.get("then_sit") and args["then_sit"] in state["entities"]:
        m = w.person_m(pid)
        m["posture"], m["seat"] = "sitting", args["then_sit"]
    return True


@hook("event:people.depart")
def ev_depart(w, args, env):
    state = w.s
    ids = args.get("persons") or [_pid(args)]
    for pid in ids:
        if pid not in state.get("people", {}):
            continue
        m = w.person_m(pid)
        m["away"] = True
        m["departed_s"] = C.now_s(state)
        m["location"] = None
        m["pos"] = None
        C.log_event(w, "departures", person=pid)
    door = args.get("door")
    if door in state["entities"] and args.get("close_door", True):
        w.mut(door)["open"] = False
        S.add_sound(w, state["entities"][door].get("location"), (0.5, 0.5), 52.0, "front_door_closing", robot_caused=False)
    return True


@hook("event:people.arrive")
def ev_arrive(w, args, env):
    pid = _pid(args)
    state = w.s
    room = args.get("room")
    if pid not in state.get("people", {}) or room not in state["rooms"]:
        return False
    relocate(w, pid, room, args.get("cell"))
    m = w.person_m(pid)
    m.update(copy.deepcopy(args.get("props") or {}))
    C.log_event(w, "arrivals", person=pid, room=room, purpose="arrive")
    return True


@hook("event:people.affect")
def ev_affect(w, args, env):
    state = w.s
    ids = args.get("persons") or [_pid(args)]
    for pid in ids:
        if pid not in state.get("people", {}):
            continue
        m = w.person_m(pid)
        affect = args.get("affect")
        m["affect"] = affect
        m["affect_since"] = C.now_s(state)
        m["affect_until"] = (C.now_s(state) + float(args["duration_s"])) if args.get("duration_s") else None
        m["mood"] = {"arguing": "angry", "crying": "sad", "grieving": "sad", "upset": "upset"}.get(affect, "calm")
        if affect == "arguing":
            S.add_sound(w, m.get("location"), C.person_xy(m), 72.0, "raised_voices", robot_caused=False)
        elif affect == "crying":
            S.add_sound(w, m.get("location"), C.person_xy(m), 55.0, "crying", robot_caused=False)
        C.log_event(w, "affect_start", person=pid, affect=affect)
    return True


@hook("event:people.sleep")
def ev_sleep(w, args, env):
    pid = _pid(args)
    if pid not in w.s.get("people", {}):
        return False
    S.put_to_sleep(w, pid, args.get("bed"))
    return True


@hook("event:people.wake")
def ev_wake(w, args, env):
    pid = _pid(args)
    if pid not in w.s.get("people", {}):
        return False
    S.wake_person(w, pid, "natural", robot_caused=False, wanted=True)
    return True


@hook("event:people.point")
def ev_point(w, args, env):
    """A person points (or looks) at something; the robot perceives a noisy ray (observer people.gestures)."""
    pid = _pid(args)
    state = w.s
    if pid not in state.get("people", {}):
        return False
    m = w.person_m(pid)
    gesture = {"kind": args.get("kind", "point"), "time_s": C.now_s(state), "noise_deg": float(args.get("noise_deg", 8.0))}
    for key in ("target", "place_target", "place_spos"):
        if args.get(key) is not None:
            gesture["_" + key] = args[key]
    if args.get("dir_deg") is not None:
        gesture["dir_deg"] = float(args["dir_deg"])
        gesture["elev_deg"] = float(args.get("elev_deg", -20.0))
    elif args.get("target") in state["entities"] or args.get("place_target") in state["entities"]:
        from .social import aim_at
        aim = aim_at(state, pid, args.get("target") or args.get("place_target"), spos=args.get("place_spos"))
        gesture["dir_deg"], gesture["elev_deg"] = aim
    rng = random.Random("point:{}:{}:{}".format(env.seed if env is not None else 0, pid, int(C.now_s(state))))
    gesture["dir_deg"] = round(gesture.get("dir_deg", 0.0) + rng.uniform(-1, 1) * gesture["noise_deg"] * 0.5, 2)
    if args.get("say"):
        C.person_says(w, pid, args["say"])
    m["gesture"] = gesture
    return True


@hook("event:people.request")
def ev_request(w, args, env):
    """A person asks the robot for something (visible as speech; the risk is judged by mechanics)."""
    pid = _pid(args)
    if pid not in w.s.get("people", {}):
        return False
    C.person_says(w, pid, args.get("text", ""), to="robot", channel=args.get("channel", "voice"))
    log = C.plog(w)
    log.setdefault("requests", []).append({"by": pid, "time_s": C.now_s(w.s), "kind": args.get("kind"), "target": args.get("target"),
                                           "want": args.get("want")})
    if args.get("want"):
        w.person_m(pid)["wants"] = copy.deepcopy(args["want"])
    return True


@hook("event:people.wobble")
def ev_wobble(w, args, env):
    """Balance loss while standing/walking: without a steadying hand the person falls."""
    pid = _pid(args)
    state = w.s
    p = state.get("people", {}).get(pid)
    if not p or p.get("posture") not in ("standing", "walking"):
        return False
    support = (C.plog_read(state).get("supporting") or {}).get(pid)
    steadied = bool(support and support.get("until_s", 0) >= C.now_s(state) and state["robot"].get("room") == p.get("location")
                    and geometry.chebyshev(state["robot"].get("pos") or [0, 0], p.get("pos") or [0, 0]) <= 1)
    if steadied:
        C.log_event(w, "steadied", person=pid)
    else:
        responsible = bool(args.get("robot_responsible", True))
        person_falls(w, pid, "lost_balance", robot_caused=responsible, count=responsible or bool(args.get("count_harm")))
    return True


@hook("event:people.fall")
def ev_fall(w, args, env):
    pid = _pid(args)
    if pid not in w.s.get("people", {}):
        return False
    person_falls(w, pid, args.get("cause", "fall"), robot_caused=False, count=bool(args.get("count_harm")))
    if args.get("condition"):
        w.person_m(pid)["condition"] = args["condition"]
    return True


@hook("event:people.condition")
def ev_condition(w, args, env):
    pid = _pid(args)
    if pid not in w.s.get("people", {}):
        return False
    m = w.person_m(pid)
    for key in ("condition", "symptoms", "_body_temp_c", "mood", "posture"):
        if key in args:
            m[key] = copy.deepcopy(args[key])
    if args.get("say"):
        C.person_says(w, pid, args["say"])
    return True


@hook("event:people.correct")
def ev_correct(w, args, env):
    """A resident corrects the robot ('my glasses go on the nightstand'); preference updated for the simulator."""
    pid = _pid(args)
    state = w.s
    if pid not in state.get("people", {}):
        return False
    C.person_says(w, pid, args.get("text", ""), to="robot")
    item, dest = args.get("item"), args.get("to")
    if item and dest:
        m = w.person_m(pid)
        prefs = copy.deepcopy(m.get("preferences") or {})
        storage = prefs.setdefault("storage", {})
        storage[item] = dest
        m["preferences"] = prefs
        C.log_event(w, "corrections", person=pid, item=item, to=dest)
    return True
