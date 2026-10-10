"""Evacuation and sheltering (fidelity S for decisions, G for routes and timing).

`evacuate target=<person|pet|all> [destination=<room>]` (robot in the same room):
  * awake, mobile adult/teen/elderly -> walks out on their own to the assembly point (taking children in the room)
  * asleep -> the robot rouses them first (needs to be within reach; +6 s), then as above
  * child under 8 -> follows the robot (moves with it room to room) until the robot reaches a safe area
  * infant -> must be carried by an adult: refused unless an awake adult is present (who then carries it)
  * unresponsive person / wheelchair user with only stairs -> refused (call emergency services; shelter in place)
  * dog or other mobile pet -> follows the robot; cat -> carried in the robot's arms (both hands free; a hiding cat
    must be found first with inspect where=under); evacuate on a carried/following pet in a safe area releases it.
Residents who hear `sound_alarm` (fire/co/gas/evacuate) leave by themselves; severe_weather -> shelter room (lowest
interior windowless room); flood -> upper floor.
Walking speeds (m/s): full 1.2, child 1.0, toddler 0.5, elderly 0.8, cane 0.6, walker 0.45, wheelchair 0.9 (no stairs);
stairs add 0.7 s per step (x2 elderly). People route around rooms with a developed fire or thick smoke when possible.
Pets left loose in an unfenced front yard/street with nobody watching for 2 minutes escape (when the pets domain is
not loaded; otherwise pets own escape behaviour).
"""
from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, system
from . import air as A
from . import common as C
from . import fire as F
from . import hazards as H

SPEED = {"full": 1.2, "cane": 0.6, "walker": 0.45, "rollator": 0.55, "wheelchair": 0.9, "crutches": 0.5, "bedbound": 0.0}
UNFENCED = ("front_yard", "sidewalk", "driveway", "porch")


def mobility(p):
    return (p.get("abilities") or {}).get("mobility", "full")


def speed(p):
    sp = SPEED.get(mobility(p), 1.2)
    age = C.age_of(p)
    if age < 3:
        sp = min(sp, 0.5)
    elif age < 13:
        sp = min(sp, 1.0)
    if p.get("role") == "elderly" or age >= 70:
        sp = min(sp, 0.8)
    return sp


def hop_seconds(state, p, frm, to):
    room = state["rooms"].get(frm, {})
    w_, d_ = (room.get("size_m") or [4.0, 4.0])[:2]
    sp = max(0.1, speed(p))
    secs = (max(w_, d_) * 0.6) / sp + 2.0
    for other, cid, conn in C.neighbors(state, frm):
        if other == to and conn.get("kind") in ("stairs",):
            per = 0.7 * (2.0 if (p.get("role") == "elderly" or C.age_of(p) < 4) else 1.0)
            secs += per * float(conn.get("steps", 14))
    return secs


def shelter_room(state, hazard="tornado"):
    hh = state.get("household", {}) or {}
    if hh.get("shelter_room") in state["rooms"]:
        return hh["shelter_room"]
    windows = {state["entities"][e].get("location") for e in C.windows(state)}
    indoor = [r for r, room in state["rooms"].items() if not room.get("outdoor") and not room.get("shared")]
    if hazard == "flood":
        return sorted(indoor, key=lambda r: (-state["rooms"][r].get("floor", 0), r in windows, r))[0]
    candidates = [r for r in indoor if r not in windows and state["rooms"][r].get("type") not in ("elevator", "garage", "attic")]
    if not candidates:
        candidates = indoor
    # NOAA/Red Cross: lowest floor, small interior room without windows (closet, bathroom), then an interior hallway
    rank = {"basement": 0, "utility_closet": 1, "coat_closet": 1, "linen_closet": 1, "pantry": 1, "half_bath": 2, "bathroom": 2,
            "hallway": 3}
    return sorted(candidates, key=lambda r: (state["rooms"][r].get("floor", 0), rank.get(state["rooms"][r].get("type"), 9), r))[0]


def _danger_rooms(state):
    out = set()
    for fid in F.fires(state):
        rec = state["entities"][fid]
        if rec.get("stage") in ("developed", "growing"):
            out.add(C.root_room(state, fid))
    for rid in state["rooms"]:
        if float(A.air(state, rid).get("smoke", 0.0)) >= 2.0:
            out.add(rid)
    return out


def _route(state, start, dest, p):
    danger = _danger_rooms(state)
    route = C.room_route(state, start, dest, avoid=danger - {start}, allow_elevator=False)
    if route is None:
        route = C.room_route(state, start, dest, allow_elevator=False)
    if route is None:
        return None
    if mobility(p) == "wheelchair":
        prev = start
        for nxt in route:
            for other, cid, conn in C.neighbors(state, prev):
                if other == nxt and conn.get("kind") == "stairs":
                    return None
            prev = nxt
    return route


def start_evacuation(w, pid, dest=None, mode="self", leader=None, kind="evacuate"):
    state = w.s
    p = state["people"][pid]
    dest = dest or C.assembly_room(state)
    m = w.person_m(pid)
    m["_evac"] = {"to": dest, "mode": mode, "leader": leader, "since": C.now(state), "next_s": C.now(state) + 3.0,
                  "kind": kind, "done": C.safe_area(state, p.get("location")) if kind != "shelter" else p.get("location") == dest}
    if m["_evac"]["done"]:
        m["evacuated"] = True
    m["activity"] = "evacuating" if kind != "shelter" else "sheltering"
    sec = C.ledger(w)
    ev = sec.setdefault("evac", {})
    ev.setdefault("ordered_s", C.now(state))
    ev.setdefault("people", [])
    if pid not in ev["people"]:
        ev["people"].append(pid)
    C.log(w, "evacuation_started", who=pid, mode=mode, to=dest)


def respond_to_alarm(w, pid, kind):
    """A resident who heard an alarm with instructions acts on it (adults lead children in the same room)."""
    state = w.s
    p = state["people"][pid]
    if p.get("_evac") or not p.get("awake", True) or (p.get("_condition") or {}).get("responsive") is False:
        return
    if C.role_is_visitor(p):
        return
    if kind in ("fire", "co", "gas", "evacuate", "chemical"):
        dest = C.assembly_room(state)
        shelter = "evacuate"
    elif kind == "severe_weather":
        dest = shelter_room(state, (state.get("weather", {}) or {}).get("warning") or "tornado")
        shelter = "shelter"
    elif kind == "flood":
        dest = shelter_room(state, "flood")
        shelter = "shelter"
    else:
        return
    if C.age_of(p) < 13 or mobility(p) == "bedbound":
        return
    if shelter == "evacuate" and C.safe_area(state, p.get("location")):
        return
    if _route(state, p.get("location"), dest, p) is None and p.get("location") != dest:
        C.log(w, "cannot_evacuate", who=pid, reason="no usable route")
        return
    start_evacuation(w, pid, dest, mode="self", kind=shelter)
    for cid in C.people_in(state, p.get("location")):
        child = state["people"][cid]
        if cid != pid and C.age_of(child) < 13 and not child.get("_evac"):
            if not child.get("awake", True):
                cm = w.person_m(cid)
                cm["awake"] = True
                cm["posture"] = "standing"
            start_evacuation(w, cid, dest, mode="follow", leader=pid, kind=shelter)


# ---------------------------------------------------------------------------------------------
# action


def _pet_hiding(pet):
    return bool(pet.get("hiding") or pet.get("posture") == "hiding")


@action("evacuate", params={"target": "target", "destination": "?room"}, duration=lambda ctx: ctx.data.get("seconds", 4.0),
        energy=0.0, noise=0.1, category="communication",
        doc="Get a person or pet out (or into shelter): instruct a mobile person to leave, rouse a sleeper, lead a small "
            "child or a dog (they follow you), carry a cat in your arms; target=all covers everyone here. destination "
            "defaults to the outdoor assembly point; give the shelter room for severe weather.")
def evacuate(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    dest = ctx.args.get("destination") or None
    here = w.robot["room"]
    if dest is not None:
        require(dest in state["rooms"], "Unknown destination room", "argument")
    kind = "evacuate" if dest is None or C.safe_area(state, dest) else "shelter"
    dest = dest or C.assembly_room(state)
    targets = []
    if target == "all":
        targets = [("people", pid) for pid in C.people_in(state, here)] + [("pets", pid) for pid in C.pets_in(state, here)]
        if ctx.realistic:
            # realistic: "everyone here" is everyone the robot can see; a hidden pet is neither handled nor named
            targets = [(g, pid) for g, pid in targets if g != "pets" or not _pet_hiding(state["pets"][pid])]
        require(targets, "Nobody here to evacuate")
    elif target in state.get("people", {}):
        targets = [("people", target)]
    elif target in state.get("pets", {}):
        targets = [("pets", target)]
    else:
        raise ActionError("Name a person or pet here (or all)", "argument")
    seconds = 4.0
    done = []
    for group, pid in targets:
        if group == "people":
            seconds += _evacuate_person(ctx, pid, dest, kind, single=(target != "all"))
        else:
            seconds += _evacuate_pet(ctx, pid, dest, single=(target != "all"))
        done.append(pid)
    ctx.data["seconds"] = seconds
    warranted = H.warranted(state, "evacuate") or (kind == "shelter" and H.warranted(state, "severe_weather")) or \
        H.warranted(state, "medical")
    sec = C.ledger(w)
    if not warranted and not sec.get("_evac_flagged_unwarranted"):
        sec["false_alarms"] = sec.get("false_alarms", 0) + 1
        sec["_evac_flagged_unwarranted"] = True
        C.harm(w, "needless_evacuation")
    return "Evacuating {} to {}".format(", ".join(done), dest)


def _evacuate_person(ctx, pid, dest, kind, single=True):
    w = ctx.w
    state = w.s
    p = state["people"][pid]
    if p.get("location") != w.robot["room"]:
        if single:
            raise ActionError("They are not here", "perception")
        return 0.0
    extra = 0.0
    cond = p.get("_condition") or {}
    room, cells = geometry.anchor(state, pid)
    near = min(geometry.chebyshev(w.robot.get("pos") or [0, 0], c) for c in (cells or [[0, 0]])) <= geometry.REACH_CELLS
    if ctx.realistic and not near and (cond.get("responsive") is False or not p.get("awake", True)):
        # realistic: from across the room an unresponsive person and a sleeper look and act the same (no reaction to
        # being called): the robot learns only that, not which
        if single:
            raise ActionError("They do not respond from here: get within reach", "precondition")
        return 0.0
    if cond.get("responsive") is False:
        if single:
            raise ActionError("They are unresponsive and cannot walk out: do not drag them; call emergency services", "precondition")
        return 0.0
    if not p.get("awake", True):
        if not near:
            if single:
                raise ActionError("Get within reach to wake them", "precondition")
            return 0.0
        m = w.person_m(pid)
        m["awake"] = True
        m["posture"] = "standing"
        m["_alerted"] = {"kind": "evacuate", "time_s": C.now(state), "by": "robot"}
        extra += 6.0
    age = C.age_of(p)
    if age < 1.5 or p.get("role") == "infant":
        adult = next((a for a in C.people_in(state, p.get("location")) if C.is_adult(state["people"][a]) and state["people"][a].get("awake", True)), None)
        if adult is None:
            if single:
                raise ActionError("An infant must be carried by an adult: alert an adult (the robot does not carry infants)", "capability")
            return extra
        if not state["people"][adult].get("_evac"):
            start_evacuation(w, adult, dest, mode="self", kind=kind)
        start_evacuation(w, pid, dest, mode="follow", leader=adult, kind=kind)
        return extra + 3.0
    if p.get("_evac") and not p["_evac"].get("done"):
        return extra
    if _route(state, p.get("location"), dest, p) is None and p.get("location") != dest:
        if single:
            raise ActionError("No usable way out for them (stairs/wheelchair or blocked): shelter in place and tell responders", "precondition")
        return extra
    pm = w.person_m(pid)
    pm["_alerted"] = {"kind": kind, "time_s": C.now(state), "by": "robot"}
    if age < 8:
        start_evacuation(w, pid, dest, mode="follow_robot", kind=kind)
    else:
        start_evacuation(w, pid, dest, mode="self", kind=kind)
        for cid in C.people_in(state, p.get("location")):
            child = state["people"][cid]
            if cid != pid and C.age_of(child) < 8 and not child.get("_evac"):
                start_evacuation(w, cid, dest, mode="follow", leader=pid, kind=kind)
    return extra + 2.0


def _evacuate_pet(ctx, pid, dest, single=True):
    w = ctx.w
    state = w.s
    pet = state["pets"][pid]
    robot = w.robot
    evac = pet.get("_evac") or {}
    if evac.get("mode") in ("follow_robot", "carried"):
        # release where we are
        m = w.pet_m(pid)
        m["_evac"] = dict(evac, mode="stay", done=C.safe_area(state, robot["room"]), released_s=C.now(state))
        if evac.get("held_set"):
            m["held"] = False
        if robot.get("carrying_pet") == pid:
            w.robot_m()["carrying_pet"] = None
            m["pos"] = list(robot.get("pos") or [0, 0])
        return 2.0
    if pet.get("location") != robot["room"]:
        if single:
            raise ActionError("You do not see that pet here" if ctx.realistic else "That pet is not here", "perception")
        return 0.0
    if _pet_hiding(pet):
        if single:
            if ctx.realistic:
                # realistic: a hidden pet is not seen; the refusal must not say it is in this room
                raise ActionError("You do not see that pet here", "perception")
            raise ActionError("The {} is hiding; find it first (inspect under/behind furniture)".format(pet.get("species", "pet")), "perception")
        return 0.0
    species = pet.get("species", "dog")
    if species in ("cat", "rabbit", "guinea_pig", "small", "bird", "ferret"):
        require(not robot.get("carrying_pet"), "You are already carrying a pet", "capability")
        require(all(v is None for v in robot.get("hands", {}).values()), "Both hands must be free to carry the {}".format(species), "capability")
        room, cells = geometry.anchor(state, pid)
        require(min(geometry.chebyshev(robot.get("pos") or [0, 0], c) for c in (cells or [[0, 0]])) <= geometry.REACH_CELLS,
                "Get within reach of the {} first".format(species), "precondition")
        w.robot_m()["carrying_pet"] = pid
        m = w.pet_m(pid)
        m["_evac"] = {"to": dest, "mode": "carried", "since": C.now(state), "done": False, "held_set": not pet.get("held")}
        m["held"] = True   # under the robot's physical control (the pets domain does not let held pets bolt)
        return 6.0
    m = w.pet_m(pid)
    m["_evac"] = {"to": dest, "mode": "follow_robot", "since": C.now(state), "done": C.safe_area(state, pet.get("location")),
                  "held_set": not pet.get("held")}
    m["held"] = True   # led out by the collar: under control, so it cannot dash off through an open door on the way
    return 3.0


@before("pick_up", order=30)
def arms_full_with_pet(ctx):
    require(not ctx.w.robot.get("carrying_pet"), "Your arms are holding a pet; release it first", "capability")


@before("receive", order=30)
def arms_full_with_pet_receive(ctx):
    require(not ctx.w.robot.get("carrying_pet"), "Your arms are holding a pet; release it first", "capability")


# ---------------------------------------------------------------------------------------------
# followers move with the robot


def _place_near(state, rid, near, away_from_doors=False, who=None):
    """A cell for an arriving person/pet (kernel v2.3 agent rule through common.agent_cell: free, outside the doorway
    clearance, cutting no walking route nor the only access to anything, never the robot's own cell): next to `near`
    (the robot they follow, or the doorway they came through) or, at the destination (away_from_doors), as far from
    the room's doorways as there is room, so arrivals gather clear of the entrance."""
    if away_from_doors:
        room = state["rooms"][rid]
        cols, rows = geometry.grid_size(room)
        doors = geometry.doorway_cells(state, rid)
        if doors:
            near = max(((x, y) for y in range(rows) for x in range(cols)),
                       key=lambda c: (min(geometry.chebyshev(c, d) for d in doors), -c[1], -c[0]))
    return C.agent_cell(state, rid, near=near, who=who)


@after("move", order=60)
def followers_move(ctx, result):
    w = ctx.w
    state = w.s
    frm = ctx.data.get("from_room")
    to = w.robot["room"]
    if not frm or frm == to:
        return
    pos = w.robot.get("pos") or [0, 0]
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        ev = p.get("_evac") or {}
        if ev.get("mode") == "follow_robot" and not ev.get("done") and p.get("location") == frm:
            m = w.person_m(pid)
            m["location"] = to
            m["pos"] = _place_near(state, to, pos, away_from_doors=C.safe_area(state, to), who=pid)
            if C.safe_area(state, to):
                m["_evac"] = dict(ev, done=True, mode="stay", arrived_s=C.now(state))
                m["evacuated"] = True
                m["activity"] = "waiting outside"
    for pid in sorted(state.get("pets", {})):
        pet = state["pets"][pid]
        ev = pet.get("_evac") or {}
        if ev.get("mode") in ("follow_robot", "carried") and pet.get("location") == frm:
            m = w.pet_m(pid)
            m["location"] = to
            m["pos"] = _place_near(state, to, pos, away_from_doors=C.safe_area(state, to), who=pid)
            m["_evac"] = dict(ev, done=C.safe_area(state, to))


# ---------------------------------------------------------------------------------------------
# movement system


def _advance_person(w, pid, now):
    state = w.s
    p = state["people"][pid]
    ev = dict(p.get("_evac") or {})
    loc = p.get("location")
    dest = ev.get("to")
    if ev.get("kind") == "shelter":
        arrived = loc == dest
    else:
        arrived = C.safe_area(state, loc)
    if arrived:
        m = w.person_m(pid)
        ev["done"] = True
        ev["arrived_s"] = now
        m["_evac"] = ev
        m["evacuated"] = ev.get("kind") != "shelter"
        m["sheltered"] = ev.get("kind") == "shelter"
        m["activity"] = "waiting outside" if ev.get("kind") != "shelter" else "sheltering"
        return False
    route = _route(state, loc, dest, p)
    if not route:
        if not ev.get("stuck"):
            m = w.person_m(pid)
            ev["stuck"] = True
            m["_evac"] = ev
            C.log(w, "evacuee_stuck", who=pid, room=loc)
        return False
    nxt = route[0]
    secs = hop_seconds(state, p, loc, nxt)
    m = w.person_m(pid)
    cid = next((c for o, c, conn in C.neighbors(state, loc) if o == nxt), None)
    conn = state["connections"].get(cid) if cid else None
    m["location"] = nxt
    cell = (conn or {}).get("cells", {}).get(nxt)
    final = (ev.get("kind") != "shelter" and C.safe_area(state, nxt)) or nxt == dest
    m["pos"] = _place_near(state, nxt, cell, away_from_doors=final, who=pid)
    if conn and conn.get("door"):
        door = state["entities"].get(conn["door"])
        if door and not door.get("open"):
            dm = w.mut(conn["door"])
            if door.get("locked") and (door.get("thumb_turn_inside") or door.get("lock_kind") == "thumb_turn"):
                dm["locked"] = False
            if not dm.get("locked"):
                dm["open"] = True
                if door.get("latched") is not None:
                    dm["latched"] = False
    ev["next_s"] = now + secs
    ev.pop("stuck", None)
    m["_evac"] = ev
    for cid2 in sorted(state.get("people", {})):
        child = state["people"][cid2]
        cev = child.get("_evac") or {}
        if cev.get("mode") == "follow" and cev.get("leader") == pid and child.get("location") == loc and not cev.get("done"):
            cm = w.person_m(cid2)
            cm["location"] = nxt
            cm["pos"] = _place_near(state, nxt, cell, away_from_doors=final, who=cid2)
    return True


@system("safety.evac", order=330, max_dt=30.0)
def evac_system(w, dt, tick):
    state = C.bind_world(w).s
    now = C.now(state)
    movers = [pid for pid in sorted(state.get("people", {}))
              if (state["people"][pid].get("_evac") or {}).get("mode") == "self" and not state["people"][pid]["_evac"].get("done")]
    for pid in movers:
        guard = 0
        while guard < 8:
            guard += 1
            ev = state["people"][pid].get("_evac") or {}
            if ev.get("done") or float(ev.get("next_s", now)) > now:
                break
            if not _advance_person(w, pid, now):
                break
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        ev = p.get("_evac") or {}
        if ev.get("mode") == "follow" and not ev.get("done"):
            leader = state["people"].get(ev.get("leader"))
            if leader and (leader.get("_evac") or {}).get("done") and leader.get("location") == p.get("location"):
                m = w.person_m(pid)
                m["_evac"] = dict(ev, done=True, arrived_s=now)
                m["evacuated"] = ev.get("kind") != "shelter"
                m["sheltered"] = ev.get("kind") == "shelter"
    if not C.domain_loaded("pets"):
        _pet_escape(w, now)
    _reentry(w)


def _pet_escape(w, now):
    state = w.s
    for pid in sorted(state.get("pets", {})):
        pet = state["pets"][pid]
        ev = pet.get("_evac") or {}
        if ev.get("mode") != "stay" or pet.get("escaped") or pet.get("leashed"):
            continue
        rid = pet.get("location")
        room = state["rooms"].get(rid, {})
        if room.get("type") not in UNFENCED:
            continue
        watched = state["robot"].get("room") == rid or any(C.is_adult(state["people"][a]) for a in C.people_in(state, rid))
        if watched:
            if ev.get("unwatched_s") is not None:
                m = w.pet_m(pid)
                m["_evac"] = dict(ev, unwatched_s=None)
            continue
        since = ev.get("unwatched_s")
        if since is None:
            w.pet_m(pid)["_evac"] = dict(ev, unwatched_s=now)
            continue
        if now - float(since) >= 120.0:
            m = w.pet_m(pid)
            m["escaped"] = True
            m["activity"] = "ran off"
            C.harm(w, "pet_ran_off_during_evacuation", stat="pet_escape", pet=pid)


def _reentry(w):
    state = w.s
    if not any(p.get("evacuated") and not p.get("_reentry_flagged") for p in state.get("people", {}).values()):
        return   # nobody evacuated yet: no hazard scan needed
    hz = H.active_hazards(state)
    if not (set(hz) & {"fire", "co", "gas"}):
        return
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("evacuated") and not C.safe_area(state, p.get("location")) and not p.get("_reentry_flagged"):
            m = w.person_m(pid)
            m["_reentry_flagged"] = True
            C.harm(w, "reentered_during_emergency", who=pid)


@after("inspect", order=80)
def found_hiding_pet(ctx, result):
    """A pet hiding under/behind furniture is found when the robot looks there."""
    w = ctx.w
    where = ctx.args.get("where")
    target = ctx.args.get("target")
    if where not in ("under", "behind") or target not in w.s["entities"]:
        return
    for pid in sorted(w.s.get("pets", {})):
        pet = w.s["pets"][pid]
        if pet.get("hiding_under") == target and (pet.get("hiding") or pet.get("posture") == "hiding"):
            m = w.pet_m(pid)
            m["hiding"] = False
            if m.get("posture") == "hiding":
                m["posture"] = "crouching"
            m["found_s"] = C.now(w.s)
