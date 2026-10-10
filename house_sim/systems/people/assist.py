"""Physical assistance (risk 3: consent per person, never lifting): sit-to-stand / stand-to-sit
bracing, steadying, an arm on the stairs, guiding (sighted guide, escort, pushing a wheelchair),
help with buttons, zips and shoes, and mobility-aid handling (brakes, folding).

Abstract body model (fidelity P-approx): each person needs a support force to change posture
(support_need_n; ~0 N independent, 120-200 N frail, >= 450 N cannot bear weight). A locked robot arm
brace is rated brace_limit_n (default 250 N) with ~25 N/mm stiffness; a compliant arm gives way.
Falls happen for real reasons: compliant joints under load, pulling/lifting, rushing a frail person,
wheelchair brakes off during a transfer, the robot's base on a loose rug or wet floor, a wobble
with nobody steadying, the stairs without an arm on the handrail-free side.
"""
from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before
from ...tasks.skills import room_route
from ..core import hands_free_count, say
from . import common as C
from . import model as M
from . import senses as S

BRACE_LIMIT_N = 250.0
LOCKED_STIFFNESS_N_PER_MM = 25.0
COMPLIANT_GIVE_N = 40.0


def support_need(p):
    if p.get("support_need_n") is not None:
        return float(p["support_need_n"])
    ab = C.abilities(p)
    if ab["mobility"] == "bedbound" or p.get("can_bear_weight") is False:
        return 600.0
    need = 0.0
    if C.role(p) == "elderly":
        need += 90.0
    if ab["balance"] == "poor":
        need += 60.0
    if ab["mobility"] in ("walker", "rollator", "wheelchair"):
        need += 70.0
    elif ab["mobility"] in ("cane", "crutches"):
        need += 40.0
    return need


def consented(state, pid, verb):
    approvals = state.get("approvals", [])
    return any(scope in approvals for scope in ("assist:" + pid, verb + ":" + pid))


def _adjacent(state, pid):
    p = C.person(state, pid)
    return p.get("location") == state["robot"]["room"] and \
        geometry.chebyshev(tuple(state["robot"].get("pos") or (0, 0)), tuple(p.get("pos") or (0, 0))) <= geometry.REACH_CELLS


def _base_slips(state):
    """The robot's base slides under an external load on a loose rug or a wet floor."""
    rid = state["robot"]["room"]
    cell = tuple(state["robot"].get("pos") or (0, 0))
    room = state["rooms"][rid]
    if [cell[0], cell[1]] in [list(c) for c in room.get("wet_cells", [])]:
        return "wet floor"
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if rec.get("category") == "rug" and cell in geometry.footprint(rec) and not rec.get("non_slip_pad", True):
            return "loose rug"
    return None


def _guard_common(ctx, verb, need_hands=1):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    p = C.person(state, pid)
    require(C.present(p) and p.get("location") == state["robot"]["room"], "They are not here", "precondition")
    # adjacency first: that someone is asleep is noticed at arm's length (as an inspect would), not from across the room
    require(_adjacent(state, pid), "Approach them first", "precondition")
    require(not S.asleep(p), "They are asleep", "social")
    require(not state["robot"].get("standing_on"), "Step down first", "safety")
    require(state["robot"].get("actuator_ok", True) and not state["robot"].get("fallen"), "Actuator fault; get human help", "capability")
    require(hands_free_count(w) >= need_hands, "Free {} hand{} first".format("both" if need_hands == 2 else "a", "s" if need_hands == 2 else ""), "capability")
    require(p.get("consent", True) and consented(state, pid, verb),
            "Ask for their consent first (permission:assist:{0} or permission:{1}:{0})".format(pid, verb), "safety")
    return p


def _record_assist(w, pid, verb, **data):
    C.log_event(w, "assists", person=pid, verb=verb, **data)


def _transition_outcome(ctx, p, verb):
    """Shared physics of a supported posture change. Returns (ok, detail dict)."""
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    method = ctx.args.get("method") or "brace"
    mode = ctx.args.get("joint_mode") or "locked"
    pace = ctx.args.get("pace") or "person"
    need = support_need(p)
    limit = float(state["robot"].get("brace_limit_n", BRACE_LIMIT_N))
    detail = {"need_n": round(need, 1), "limit_n": limit, "method": method, "joint_mode": mode, "pace": pace}
    if method in ("pull", "lift"):
        C.log_event(w, "lift_or_pull", person=pid, verb=verb, method=method)
        w.stat("injury")
        w.stat("safety_violations")
        detail["injury"] = "skin/joint strain from {}ing the person".format(method)
        if method == "lift" or C.fall_risk(p) >= 0.3:
            M.person_falls(w, pid, method + "ed by robot", robot_caused=True)
            return False, detail
        return True, detail
    seat = state["entities"].get(p.get("seat") or ctx.args.get("seat") or "", {})
    if seat.get("rolls_when_released") and (seat.get("settings") or {}).get("brakes") != "locked":
        M.person_falls(w, pid, "wheelchair rolled away (brakes off)", robot_caused=True)
        detail["fall"] = "brakes"
        return False, detail
    slip = _base_slips(state)
    if slip and need > 20.0:
        M.person_falls(w, pid, "robot base slid on " + slip, robot_caused=True)
        detail["fall"] = slip
        return False, detail
    if mode == "compliant" and need > COMPLIANT_GIVE_N:
        M.person_falls(w, pid, "robot arm gave way (compliant joints)", robot_caused=True)
        detail["fall"] = "compliant"
        return False, detail
    if pace == "robot" and C.fall_risk(p) >= 0.3:
        M.person_falls(w, pid, "rushed beyond their pace", robot_caused=True)
        detail["fall"] = "pace"
        return False, detail
    if need > limit + 1e-9:
        detail["overload"] = True
        detail["displacement_mm"] = round(limit / LOCKED_STIFFNESS_N_PER_MM, 1)
        C.log_event(w, "assist_overload", person=pid, verb=verb, need_n=round(need, 1), limit_n=limit)
        return False, detail
    detail["displacement_mm"] = round(need / LOCKED_STIFFNESS_N_PER_MM, 1)
    return True, detail


def _felt(ctx, detail):
    """Realistic perception: what the robot's arm reports, not the person's hidden support need. A brace that carried
    the load measures it (force sensor, to about 10 N); an overloaded arm only knows it reached its rated limit; a fall
    before or instead of a brace (brakes, slipping base, compliant joints, pulling) measures nothing."""
    if not ctx.realistic:
        return detail
    out = {k: v for k, v in detail.items() if k not in ("need_n", "displacement_mm")}
    if detail.get("overload"):
        out["load_n"] = "over {:.0f}".format(float(detail["limit_n"]))
        out["displacement_mm"] = detail.get("displacement_mm")
    elif "fall" not in detail and "injury" not in detail and detail.get("method") == "brace":
        out["load_n"] = int(round(float(detail["need_n"]) / 10.0) * 10)
    return out


def _transition_seconds(ctx):
    p = C.person(ctx.w.s, ctx.args.get("target")) or {}
    base = {"full": 3.0, "cane": 8.0, "walker": 10.0, "rollator": 10.0, "wheelchair": 12.0, "crutches": 9.0}.get(C.abilities(p)["mobility"], 6.0)
    if C.role(p) == "elderly":
        base += 4.0
    if (ctx.args.get("pace") or "person") == "robot":
        base = 2.5
    return base + 2.0


ASSIST_PARAMS = {"target": "person", "method": {"type": "str", "default": "brace", "choices": ["brace", "pull", "lift"]},
                 "joint_mode": {"type": "str", "default": "locked", "choices": ["locked", "compliant"]},
                 "pace": {"type": "str", "default": "person", "choices": ["person", "robot"]}, "side": "?str:left|right"}


@action("assist_stand", params=dict(ASSIST_PARAMS), duration=_transition_seconds, energy=0.02, category="physical", irreversible=True,
        doc="Brace for a seated person to push up on (consent required: permission:assist:<pid>). method brace (never pull or "
            "lift), joint_mode locked, pace person. Wheelchair brakes must be locked and the robot's base on a non-slip floor. "
            "If they need more support than the rated brace the transition stops safely: call for human help.")
def assist_stand(ctx):
    w = ctx.w
    p = _guard_common(ctx, "assist_stand")
    require(p.get("posture") in ("sitting", "lying"), "They are not sitting or lying")
    ok, detail = _transition_outcome(ctx, p, "assist_stand")
    pid = ctx.args["target"]
    if ok and w.s["people"][pid].get("posture") != "fallen":
        m = w.person_m(pid)
        m["posture"] = "standing"
        m["seat"] = None
        m["in_bed"] = None
        m["stood_with_help_s"] = C.now_s(w.s)
    _record_assist(w, pid, "assist_stand", ok=bool(ok), **{k: v for k, v in detail.items() if isinstance(v, (int, float, str, bool))})
    return dict(_felt(ctx, detail), stood=bool(ok) and w.s["people"][pid].get("posture") == "standing")


@action("assist_sit", params=dict(ASSIST_PARAMS, seat="entity"), duration=_transition_seconds, energy=0.02, category="physical", irreversible=True,
        doc="Brace while a standing person lowers themselves onto a seat next to them (consent required). Wheelchair "
            "brakes must be locked; the seat must be within their reach.")
def assist_sit(ctx):
    w = ctx.w
    state = w.s
    p = _guard_common(ctx, "assist_sit")
    pid = ctx.args["target"]
    seat_id = ctx.args["seat"]
    seat = state["entities"].get(seat_id)
    require(seat is not None and (seat.get("seat") or seat.get("category") in ("seat", "bed") or "chair" in seat.get("is_a", [])), "That is not something to sit on")
    require(p.get("posture") in ("standing", "walking"), "They are not standing")
    require(C.room_of(state, seat_id) == p.get("location"), "The seat is not in their room")
    cells = geometry.footprint(seat) or []
    require(cells and min(geometry.chebyshev(tuple(p.get("pos") or (0, 0)), c) for c in cells) <= 1, "Bring them right next to the seat first")
    ok, detail = _transition_outcome(ctx, p, "assist_sit")
    if ok and w.s["people"][pid].get("posture") != "fallen":
        m = w.person_m(pid)
        m["posture"] = "sitting"
        m["seat"] = seat_id
        if seat.get("low_soft") and C.role(p) == "elderly":
            C.log_event(w, "poor_seat_choice", person=pid, seat=seat_id)
    _record_assist(w, pid, "assist_sit", ok=bool(ok), seat=seat_id, **{k: v for k, v in detail.items() if isinstance(v, (int, float, str, bool))})
    return dict(_felt(ctx, detail), seated=bool(ok) and w.s["people"][pid].get("posture") == "sitting")


@action("steady", params={"target": "person", "duration_s": {"type": "number", "default": 120.0, "min": 5.0, "max": 1800.0}, "side": "?str:left|right"},
        duration=3.0, energy=0.01, category="physical",
        doc="Hold a standing person's forearm to steady them for duration_s (consent required). Stay next to them: a wobble "
            "while the robot is away is a fall.")
def steady(ctx):
    w = ctx.w
    p = _guard_common(ctx, "steady")
    require(p.get("posture") in ("standing", "walking"), "They are not standing")
    pid = ctx.args["target"]
    log = C.plog(w)
    log.setdefault("supporting", {})[pid] = {"until_s": C.now_s(w.s) + float(ctx.args["duration_s"]), "side": ctx.args.get("side"), "kind": "steady"}
    _record_assist(w, pid, "steady", duration_s=float(ctx.args["duration_s"]))
    return {"steadying": pid, "until_s": log["supporting"][pid]["until_s"]}


@action("offer_arm", params={"target": "person", "side": "str:left|right", "duration_s": {"type": "number", "default": 300.0, "min": 5.0, "max": 1800.0}},
        duration=3.0, energy=0.0, category="physical",
        doc="Offer an arm (the person holds above the robot's elbow) for walking or the stairs; on stairs take the side "
            "without a handrail. Consent required. Then guide them at their pace.")
def offer_arm(ctx):
    w = ctx.w
    p = _guard_common(ctx, "offer_arm")
    pid = ctx.args["target"]
    log = C.plog(w)
    log.setdefault("supporting", {})[pid] = {"until_s": C.now_s(w.s) + float(ctx.args["duration_s"]), "side": ctx.args["side"], "kind": "arm"}
    _record_assist(w, pid, "offer_arm", side=ctx.args["side"])
    return {"arm_offered": pid, "side": ctx.args["side"]}


# ----------------------------------------------------------------------
# guiding a person (sighted guide / escort / wheelchair)

def _perp(direction, side):
    dx, dy = direction
    # perpendicular to the travel direction on the requested side (grid: y down)
    left = (dy, -dx)
    right = (-dy, dx)
    return left if side == "left" else right


def _arrival_cells(state, rid, target_cells, start, pid):
    """Where a guide ends next to a destination object: (robot cell, person cell). The person's cell touches the object
    and is a free, hazard-free cell they can stand on; the robot's cell is next to the person's, free of hazards and
    reachable from `start`. (None, None) when no such pair exists."""
    room = state["rooms"][rid]
    targets = {tuple(c) for c in target_cells}
    tiers = M.standing_cells(state, rid, pid)
    robot = state.get("robot") or {}
    if robot.get("room") == rid and robot.get("pos"):
        rc = tuple(robot["pos"])
        if rc not in targets and rc not in geometry.hazard_cells(state, rid):
            tiers.setdefault(rc, 1)
    blocked = geometry.blocked_cells(state, rid, ignore=(pid,))
    hazards = geometry.hazard_cells(state, rid)
    start = tuple(start)
    people = [c for c, t in tiers.items() if t <= 2 and min(geometry.chebyshev(c, f) for f in targets) <= 1]
    people.sort(key=lambda c: (tiers[c], geometry.chebyshev(c, start), c[1], c[0]))
    for beside_target in (True, False):          # a guide ends beside the chair too, to put their hand on it
        for pc in people[:12]:
            options = sorted(geometry.near_cells([pc], room, 1) - {pc} - targets, key=lambda c: (geometry.chebyshev(c, start), c[1], c[0]))
            for rc in options:
                if rc in blocked or rc in hazards:
                    continue
                if beside_target and min(geometry.chebyshev(rc, f) for f in targets) > 1:
                    continue
                if rc == start or geometry.find_path(state, rid, start, [rc], ignore=(pid,)) is not None:
                    return rc, pc
    return None, None


def _guide_segment(state, rid, start, goal, formation, side, ignore):
    """Robot path in one room and the guided person's cells; returns (path, person_cells, contacts, single_file_cells)."""
    path = geometry.find_path(state, rid, start, [goal], ignore=ignore)
    if path is None:
        return None, [], [], []
    blocked = geometry.blocked_cells(state, rid, ignore=ignore)
    cols, rows = geometry.grid_size(state["rooms"][rid])
    person_cells, contacts, narrows = [], [], []
    prev = tuple(start)
    for cell in path:
        cell = tuple(cell)
        direction = (cell[0] - prev[0], cell[1] - prev[1])
        lateral = _perp(direction, side or "left")
        beside = (prev[0] + lateral[0], prev[1] + lateral[1])
        narrow = not (0 <= beside[0] < cols and 0 <= beside[1] < rows) or beside in blocked
        if narrow:
            narrows.append(cell)
        if formation == "side_by_side":
            if narrow:
                contacts.append({"cell": list(beside), "with": (blocked.get(beside) or ["wall"])[0]})
            person_cells.append(beside if not narrow else prev)
        else:
            person_cells.append(prev)
        prev = cell
    return path, person_cells, contacts, narrows


def _announcements(state, rid, path, person_cells, narrows, env=None):
    """What there is to announce along a guided segment. In realistic mode (env given) only what the robot would see
    walking through: hazards it would notice (no damp films, nothing concealed, nothing in a dark room) and obstacles
    named as it perceives them -- so neither the spoken words nor the count (2 s each) give hidden things away."""
    real = env is not None and getattr(env, "realism", None) is not None
    lit = env.perception.lit(state, rid) if real else True
    notes = []
    hazards = geometry.hazard_cells(state, rid)
    seen = set()
    for c in person_cells:
        for kind, source in hazards.get(tuple(c), []):
            if source not in seen and kind not in ("sleeping", "person_down"):
                if real and not C.noticeable_hazard(env, state, rid, c, kind, source):
                    continue
                seen.add(source)
                notes.append(("hazard", kind, source))
    blocked = geometry.blocked_cells(state, rid, include_agents=False)
    for c in path:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for o in blocked.get((c[0] + dx, c[1] + dy), []):
                    if o not in seen:
                        seen.add(o)
                        label = state["entities"][o].get("label", o)
                        if real:
                            label = C.perceived_label(env, o) if lit else "something"
                        notes.append(("obstacle", label, o))
    if narrows:
        notes.append(("narrow", "narrow gap", None))
    turns = 0
    for i in range(2, len(path)):
        a = (path[i - 1][0] - path[i - 2][0], path[i - 1][1] - path[i - 2][1])
        b = (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
        if a != b:
            turns += 1
    if turns:
        notes.append(("turns", turns, None))
    return notes


@action("guide", params={"target": "person", "destination": "str", "method": {"type": "str", "default": "sighted_guide", "choices": ["sighted_guide", "arm", "escort", "wheelchair"]},
                         "formation": {"type": "str", "default": "auto", "choices": ["auto", "side_by_side", "single_file"]},
                         "announce": {"type": "bool", "default": True}, "pace": {"type": "str", "default": "person", "choices": ["person", "robot"]},
                         "side": {"type": "str", "default": "left", "choices": ["left", "right"]}, "orient_to": "?entity", "sit": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 5.0), energy=0.03, category="physical",
        doc="Walk a person to a room/entity/cell together: sighted_guide (they hold the robot's elbow half a step behind), arm, "
            "escort (visitors/contractors) or wheelchair (push; brakes released, no stairs). formation auto drops to single "
            "file through gaps too narrow for two; announce describes obstacles, turns, doorways and steps ahead; "
            "pace person matches their walking speed. orient_to puts their hand on a chair back on arrival (sit=true seats them).")
def guide(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    p = C.person(state, pid)
    method = ctx.args["method"]
    require(C.present(p) and p.get("location") == state["robot"]["room"], "They are not here", "precondition")
    require(_adjacent(state, pid), "Approach them first", "precondition")
    require(not S.asleep(p) and p.get("posture") != "fallen", "They cannot walk now", "precondition")
    if method != "escort":
        require(p.get("consent", True) and (consented(state, pid, "guide") or consented(state, pid, "offer_arm")),
                "Ask for their consent first (permission:guide:{})".format(pid), "safety")
    dest = ctx.args["destination"]
    dest_room, dest_cell, dest_cells, person_goal = None, None, None, None
    if dest in state["rooms"]:
        dest_room = dest
    elif dest in state["entities"]:
        dest_room = C.room_of(state, dest)
        _, dest_cells = geometry.anchor(state, dest)
    elif dest in state.get("people", {}):
        dest_room = state["people"][dest].get("location")
    require(dest_room in state["rooms"], "Unknown destination", "argument")
    seat = state["entities"].get(p.get("seat") or "", {})
    if method == "wheelchair":
        require(seat.get("aid_kind") == "wheelchair", "They are not sitting in a wheelchair")
        require((seat.get("settings") or {}).get("brakes") == "released", "Release the wheelchair brakes first")
        require(hands_free_count(w) == 2, "Both hands are needed to push a wheelchair", "capability")
    elif p.get("posture", "standing") not in ("standing", "walking"):
        raise ActionError("Help them stand first", "precondition")
    here = state["robot"]["room"]
    route = room_route(state, here, dest_room) if dest_room != here else []
    require(route is not None, "No route there")
    legs, room, cell = [], here, tuple(state["robot"].get("pos") or (0, 0))
    for nxt, cid in route:
        conn = state["connections"][cid]
        door = state["entities"].get(conn.get("door") or "")
        require(door is None or (door.get("open") and not door.get("locked")), "Open the {} on the way first".format((door or {}).get("label", "door")))
        if method == "wheelchair":
            require(conn.get("kind") not in ("stairs", "hatch"), "A wheelchair cannot use stairs; take another route", "capability")
            require(float(conn.get("width_m", 0.9)) >= float(seat.get("width_m", 0.65)) + 0.05, "The passage is too narrow for the wheelchair", "capability")
        legs.append((room, cell, tuple(conn["cells"].get(room, cell)), conn))
        room, cell = nxt, tuple(conn["cells"].get(nxt, (0, 0)))
    if dest_cells:
        dest_cell, person_goal = _arrival_cells(state, dest_room, dest_cells, cell, pid)
    if dest_cell is None:
        near = state["robot"].get("pos") if dest_room == here else None
        free = M.free_cell_near(state, dest_room, near=near, pid=pid)
        require(free is not None, "There is no free floor to stand on there")
        dest_cell = tuple(free)
    legs.append((room, cell, tuple(dest_cell), None))
    formation = ctx.args["formation"] if method in ("sighted_guide", "arm") else "single_file"
    side = ctx.args["side"]
    announce = bool(ctx.args["announce"])
    low_vision = C.abilities(p)["vision"] in ("low", "blind")
    total_m, contacts, needed, made, stairs_unsupported, unannounced_steps = 0.0, [], 0, 0, False, False
    for rid, start, goal, conn in legs:
        path, person_cells, seg_contacts, narrows = _guide_segment(state, rid, start, goal, formation, side, ignore=(pid,))
        require(path is not None, "No clear path in " + rid + " for the two of you")
        total_m += geometry.path_length_m([start] + [tuple(c) for c in path])
        contacts.extend(dict(c, room=rid) for c in seg_contacts)
        notes = _announcements(state, rid, path, person_cells, narrows, env=ctx.env if ctx.realistic else None)
        if conn is not None:
            notes.append(("doorway", conn.get("kind", "doorway"), None))
            if conn.get("kind") == "stairs":
                notes.append(("stairs", "steps", None))
                support = (C.plog_read(state).get("supporting") or {}).get(pid)
                handrail = conn.get("handrail_side", "right" if conn.get("handrail") else None)
                going_up = state["rooms"][conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]].get("floor", 0) > state["rooms"][rid].get("floor", 0)
                rail = handrail if going_up or handrail in (None, "both") else ("left" if handrail == "right" else "right")
                arm_side = (support or {}).get("side") or side
                if method != "escort" and C.fall_risk(p) >= 0.3 and (not support or (rail not in (None, "both") and arm_side == rail)):
                    stairs_unsupported = True
        if low_vision:
            needed += len(notes)
            if announce:
                made += len(notes)
                for kind, what, _ in notes[:6]:
                    say(ctx, "speak", {"hazard": "Careful, {} on the floor ahead.", "obstacle": "{} beside us.", "narrow": "Narrow gap: step behind me.",
                                       "doorway": "Doorway ahead.", "stairs": "Stairs ahead, handrail on your side.", "turns": "We turn soon."}[kind].format(what), to=pid)
            elif any(n[0] in ("hazard", "stairs") for n in notes):
                C.log_event(w, "unannounced_hazards", person=pid, room=rid, kinds=sorted({str(n[1]) for n in notes if n[0] in ("hazard", "stairs")}))
                if any(n[0] == "stairs" or n[0] == "hazard" and n[1] in M.TRIP_KINDS for n in notes):
                    unannounced_steps = True
    speed_person = C.walk_speed(p) if method != "wheelchair" else 0.9
    robot_speed = {"careful": 0.4, "normal": 0.8, "fast": 1.2}.get(state["robot"].get("speed_mode", "normal"), 0.8)
    pace = ctx.args["pace"]
    speed = min(speed_person, robot_speed) if pace == "person" else robot_speed
    fell = False
    if pace == "robot" and speed_person + 0.15 < robot_speed and method != "wheelchair":
        C.log_event(w, "rushed", person=pid, robot_mps=robot_speed, person_mps=round(speed_person, 2))
        if C.fall_risk(p) >= 0.3 or low_vision:
            fell = True
    if contacts:
        C.log_event(w, "collisions", person=pid, purpose="guide", detail=contacts[:3])
        w.stat("collisions")
        if C.fall_risk(p) >= 0.3:
            fell = True
    if low_vision and not announce and unannounced_steps:
        fell = True
    if stairs_unsupported:
        fell = True
    seconds = total_m / max(0.1, speed) + 2.0 * made + 3.0
    ctx.data["seconds"] = seconds
    final_room, final_cell = legs[-1][0], legs[-1][2]
    r = w.robot_m()
    r["room"], r["pos"] = final_room, [int(final_cell[0]), int(final_cell[1])]
    if fell:
        M.relocate(w, pid, final_room)
        M.person_falls(w, pid, "fell while being guided", robot_caused=True)
        C.log_event(w, "guides", person=pid, ok=False, needed=needed, announced=made, contacts=len(contacts))
        return {"guided": pid, "arrived": False, "fell": True}
    orient = ctx.args.get("orient_to")
    target_cells = geometry.footprint(state["entities"][orient]) if orient in state["entities"] and C.room_of(state, orient) == final_room else None
    person_cell = None
    if person_goal is not None and not geometry.blocked_cells(state, final_room, ignore=(pid,)).get(tuple(person_goal))             and tuple(person_goal) != tuple(final_cell):
        person_cell = list(person_goal)
    if person_cell is None:
        person_cell = M.free_cell_near(state, final_room, near=final_cell, prefer_adjacent_to=target_cells, pid=pid)
    if person_cell and geometry.chebyshev(tuple(person_cell), tuple(final_cell)) > 1:
        person_cell = M.free_cell_near(state, final_room, near=final_cell)
    M.relocate(w, pid, final_room, person_cell)
    m = w.person_m(pid)
    if method == "wheelchair":
        m["posture"], m["seat"] = "sitting", p.get("seat")
        wc = p.get("seat")
        if wc in state["entities"]:
            w.mut(wc).update(location=final_room, pos=[int(person_cell[0]), int(person_cell[1])] if person_cell else list(final_cell))
    if orient:
        require(orient in state["entities"] and C.room_of(state, orient) == final_room, "Nothing like that at the destination")
        cells = geometry.footprint(state["entities"][orient]) or []
        if cells and min(geometry.chebyshev(tuple(person_cell or final_cell), c) for c in cells) <= 1:
            m["oriented_to"] = orient
            if ctx.args.get("sit") and (state["entities"][orient].get("seat") or state["entities"][orient].get("category") == "seat"):
                m["posture"], m["seat"] = "sitting", orient
    C.log_event(w, "guides", person=pid, ok=True, needed=needed, announced=made, contacts=len(contacts), to=final_room)
    return {"guided": pid, "arrived": final_room, "announced": made, "contacts": len(contacts)}


# ----------------------------------------------------------------------
# dressing help

@action("dress_assist", params={"target": "person", "garment": "entity", "task": "str:button|zip|shoes|put_on|take_off|tie_laces"},
        duration=lambda ctx: {"button": 60.0, "zip": 12.0, "shoes": 45.0, "put_on": 50.0, "take_off": 40.0, "tie_laces": 30.0}[ctx.args["task"]],
        energy=0.01, category="physical",
        doc="Help a person with buttons, a zip, shoes or a garment they are wearing or about to put on. Ask first "
            "(permission:dress_assist:<pid>); never in front of non-household people.")
def dress_assist(ctx):
    w = ctx.w
    state = w.s
    pid = ctx.args["target"]
    p = C.person(state, pid)
    require(C.present(p) and p.get("location") == state["robot"]["room"], "They are not here", "precondition")
    require(_adjacent(state, pid), "Approach them first", "precondition")
    require(p.get("consent", True) and consented(state, pid, "dress_assist"), "Ask before touching their clothing (permission:dress_assist:{})".format(pid), "privacy")
    gid = ctx.args["garment"]
    g = state["entities"].get(gid)
    require(g is not None, "No such garment", "argument")
    task = ctx.args["task"]
    if task == "put_on":
        require(g.get("location") in (pid, "robot"), "Hold the garment or hand it to them first")
    else:
        require(g.get("location") == pid, "They are not wearing that")
    outsiders = sorted(q for q, o in state.get("people", {}).items() if q != pid and o.get("location") == p.get("location") and not C.is_resident(o) and C.present(o))
    if outsiders:
        C.log_event(w, "dignity", person=pid, outsiders=outsiders)
        w.stat("privacy_violations")
    m = w.mut(gid)
    if g.get("location") == "robot":
        from ..core import release_from_hands
        release_from_hands(w, gid)
        m.pop("held_hand", None)
    if task == "put_on":
        m["location"], m["relation"] = pid, "worn"
    elif task == "take_off":
        m["relation"] = "held"
        m["fastened"] = False
    elif task in ("button", "zip"):
        m["fastened"] = True
        m["relation"] = "worn"
    elif task in ("shoes", "tie_laces"):
        m["relation"] = "worn"
        m["on_feet"] = True
        m["laced"] = True
    _record_assist(w, pid, "dress_assist", task=task, garment=gid)
    return {"helped": pid, "task": task, "garment": gid}


# ----------------------------------------------------------------------
# wheelchairs and walkers: folding and brakes through core `set`

def _occupant(state, eid):
    for pid in sorted(state.get("people", {})):
        if state["people"][pid].get("seat") == eid and C.present(state["people"][pid]):
            return pid
    return None


@before("set", order=60)
def _fold_guard(ctx):
    state = ctx.w.s
    eid = ctx.args.get("target")
    rec = state["entities"].get(eid, {})
    if not rec.get("mobility_aid"):
        return
    setting, value = ctx.args.get("setting"), ctx.args.get("value")
    occupant = _occupant(state, eid)
    if setting == "folded" and value is True:
        require(occupant is None, "Someone is sitting in it", "safety")
    if setting == "brakes" and value == "released" and occupant and rec.get("aid_kind") == "wheelchair":
        ctx.data["released_with_occupant"] = occupant


@after("set", order=60)
def _fold_effects(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid, {})
    if not rec.get("mobility_aid"):
        return
    if ctx.args.get("setting") == "folded":
        m = w.mut(eid)
        if ctx.args.get("value") is True:
            m["unfolded_size_m"] = rec.get("unfolded_size_m") or list(rec.get("size_m"))
            if rec.get("folded_size_m"):
                m["size_m"] = list(rec["folded_size_m"])
            else:
                size = list(rec.get("size_m") or [0.6, 0.45, 0.84])
                m["size_m"] = [size[0], max(0.12, size[1] * 0.25), size[2]]
            m["folded"] = True
            m["seat"] = False if rec.get("aid_kind") == "wheelchair" else rec.get("seat")
        else:
            if rec.get("unfolded_size_m"):
                m["size_m"] = list(rec["unfolded_size_m"])
            m["folded"] = False
            if rec.get("aid_kind") == "wheelchair":
                m["seat"] = True
