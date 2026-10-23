"""Overhead garage door with an automatic opener (UL 325 style safety behaviour).

State on the door (mech): state closed|opening|open|closing|stopped, pos 0 (closed) .. 1 (open),
travel_s (~12 s), last_dir up|down, reported (what the opener/app believes), light_blink_until,
reversals, released (emergency release pulled: move it by hand with raise/lower).
Hidden (_mech): close_limit_short_m (the down limit is mis-set: the door stops that far above the
floor while the opener reports closed).

Path: the threshold cells on both sides of the opening (door width / 0.5 m cells along the wall).
Photo eyes (entities category photo_eye, door=<id>) project a beam 15 cm above the floor across the
threshold; an aligned, unobstructed pair shows "steady green". While closing, a blocked beam (person,
pet, bin, bike, car bumper: anything taller than 12 cm) reverses the door to fully open; a low object
under the door is detected by contact and also reverses it. Misaligned or blocked eyes: the opener
refuses to close (the light blinks) unless the wall button is held down for the whole travel
(constant-pressure override), which ignores the beam and crushes whatever is in the path.
Core open on the door runs the opener up while the robot watches; core close is refused (use the
wall button or remote and watch it close).
"""
from ...kernel import geometry, physics
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from . import common as C

BEAM_HEIGHT_M = 0.12


def gpos(rec):
    m = rec.get("mech") or {}
    if "pos" in m:
        return C.num(m["pos"])
    return 1.0 if rec.get("open") else 0.0


def gstate(rec):
    m = rec.get("mech") or {}
    if m.get("state"):
        return m["state"]
    return "open" if rec.get("open") else "closed"


def door_height(rec):
    return C.num((rec.get("size_m") or [2.5, 0.05, 2.1])[2], 2.1)


def path_cells(state, door):
    """{room: [cells]} the descending door sweeps: the kernel's threshold run on each side
    (``geometry.overhead_door_cells``: round(width / 0.5 m) cells along the wall, centred on the doorway cell)."""
    return geometry.overhead_door_cells(state, door)


def _height(rec):
    return C.num((rec.get("size_m") or [0.1, 0.1, 0.1])[2], 0.1)


def obstructions(state, door):
    """[(kind, id, height_m)] in the door's path: agents and objects (explicit under_door too). One pass over the
    entities (this runs every 1 s step while a door travels)."""
    paths = {rid: set(map(tuple, cells)) for rid, cells in path_cells(state, door).items()}
    rooms = sorted(paths)
    per_room = {rid: [] for rid in rooms}
    for rid in rooms:
        for group, aid in C.agents_at(state, rid, paths[rid]):
            agent = state[group][aid]
            h = 0.3 if group == "pets" and agent.get("size") == "small" else (0.6 if group == "pets" else 1.6)
            per_room[rid].append(("agent", aid, h))
    ents = state["entities"]
    objects = {rid: [] for rid in rooms}
    flagged = []
    for eid in sorted(ents):
        rec = ents[eid]
        cells = paths.get(rec.get("location"))
        if cells and eid != door and not (rec.get("ground_layer") or rec.get("mounted") or rec.get("connects")) \
                and rec.get("category") not in ("photo_eye", "floor", "light", "switch", "garage_button") \
                and "furniture" not in (rec.get("is_a") or []) and any(tuple(c) in cells for c in geometry.footprint(rec)):
            objects[rec["location"]].append(("object", eid, _height(rec)))
        elif rec.get("under_door") == door:
            flagged.append(("object", eid, _height(rec)))
    found = []
    for rid in rooms:          # per room: agents, then objects (id order); explicit under_door items last
        found += per_room[rid] + objects[rid]
    return found + flagged


@hook("path_blocked")
def _moving_door_path(state, rid, avoid_kinds):
    """Never plan a route under an overhead door while it travels (kernel v2.1 path_blocked hook)."""
    out = []
    ents = state["entities"]
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        door = conn.get("door")
        if not door or rid not in conn.get("rooms", ()) or door not in ents:
            continue
        mech = ents[door].get("mech")
        if isinstance(mech, dict) and mech.get("state") in ("opening", "closing"):
            out.extend(path_cells(state, door).get(rid, []))
    return out


def eyes_of(state, door, w=None):
    """Photo eyes paired with a door (the domain's static index when the World is at hand)."""
    ents = state["entities"]
    pool = C.ids(w, "photo_eyes") if w is not None and w.s is state else sorted(ents)
    return [e for e in pool if e in ents and ents[e].get("category") == "photo_eye" and ents[e].get("door") == door]


def sensors_ok(state, door, w=None):
    for eye in eyes_of(state, door, w):
        if state["entities"][eye].get("_misaligned") or C.hidden(state["entities"][eye]).get("misaligned"):
            return False
    return True


def beam_blocked(state, door):
    return [o for o in obstructions(state, door) if o[2] >= BEAM_HEIGHT_M]


def _set_pos(w, door, pos, state_name=None):
    """Move the door to pos (0 shut .. 1 open). A run the robot starts (opening / closing in its action) carries its
    "run" mark until the door comes to rest (harm attribution: what the travelling door strikes is the robot's)."""
    rm, mech = C.mut_mech(w, door)
    mech["pos"] = round(C.clamp(pos, 0.0, 1.0), 4)
    mech["open_frac"] = mech["pos"]
    rm["open"] = mech["pos"] > 0.02
    if state_name:
        mech["state"] = state_name
        if state_name in ("opening", "closing"):
            C.note_robot(w, door, "run")
        else:
            C.drop_robot(w, door, "run")
    if not rm["open"]:
        C.drop_robot(w, door, "open")
    return mech


def _blink(w, door, reason):
    rm, mech = C.mut_mech(w, door)
    mech["light_blink_until"] = round(w.s["time_s"] + 10.0, 3)
    mech["last_refusal"] = reason
    C.mlog(w, "garage", {"door": door, "event": "blink", "reason": reason})


def _refresh_eyes(w, door):
    state = w.s
    blocked = bool(beam_blocked(state, door))
    for eye in eyes_of(state, door, w):
        rec = state["entities"][eye]
        mis = rec.get("_misaligned") or C.hidden(rec).get("misaligned")
        text = "blinking" if mis or blocked else "steady green"
        if rec.get("display") != text:
            w.mut(eye)["display"] = text


def command(ctx, door, hold_s=0.0):
    """Wall button / remote press (hold_s > 0 holds it: constant-pressure close)."""
    w = ctx.w
    state = w.s
    require(door and door in state["entities"], "This control is not paired to a door", "precondition")
    rec = state["entities"][door]
    require(physics.is_powered(w, door), "Nothing happens: the opener has no power", "resource")
    m = C.mech_of(rec)
    cur = gstate(rec)
    pos = gpos(rec)
    _refresh_eyes(w, door)
    if hold_s > 0.0 and cur in ("open", "stopped") and pos > 0.02:
        return _constant_pressure(ctx, door, hold_s)
    if cur in ("opening",):
        _set_pos(w, door, pos, "stopped")
        return "The door stopped partway"
    if cur in ("closing",):
        _set_pos(w, door, pos, "opening")
        rm, mech = C.mut_mech(w, door)
        mech["last_dir"] = "up"
        return "The door reversed and is going back up"
    if cur == "closed" or pos <= 0.02 or (cur == "stopped" and m.get("last_dir") == "down"):
        _set_pos(w, door, pos, "opening")
        rm, mech = C.mut_mech(w, door)
        mech["last_dir"] = "up"
        _motor_notice(w, door, "opening")
        return "The opener hums: the door is going up"
    if not sensors_ok(state, door, w) or beam_blocked(state, door):
        _blink(w, door, "sensors")
        return "The opener light blinks and the door does not move"
    _set_pos(w, door, pos, "closing")
    rm, mech = C.mut_mech(w, door)
    mech["last_dir"] = "down"
    _motor_notice(w, door, "closing")
    return "The opener hums: the door is coming down"


def _motor_notice(w, door, direction):
    """The opener's motor starts: a garage_door notice from the garage (the robot hears it per the timeline)."""
    rooms = [r for r in C.door_rooms(w.s, door) if (w.s["rooms"].get(r) or {}).get("type") == "garage"]
    C.notice(w, "garage_door", source=door, room=rooms[0] if rooms else None, data={"direction": direction})


def _constant_pressure(ctx, door, hold_s):
    """Holding the wall button closes even with the beam blocked; whatever is under it is crushed."""
    w = ctx.w
    state = w.s
    rec = state["entities"][door]
    m = C.mech_of(rec)
    travel = C.num(m.get("travel_s"), C.GARAGE_TRAVEL_S)
    pos = gpos(rec)
    new = max(0.0, pos - hold_s / travel)
    height = door_height(rec)
    for kind, oid, h in obstructions(state, door):
        if new * height < h:
            if kind == "agent":
                C.harm(w, "injury", door=door, agent=oid, cause="crushed under a garage door held closed")
            else:
                C.mark_damage(w, oid, "crushed under the garage door", broken=True)
                C.mark_damage(w, door, "panel dented on an obstruction")
            new = max(new, h / height)
    _set_pos(w, door, new, "closed" if new <= 0.02 else "stopped")
    rm, mech = C.mut_mech(w, door)
    mech["last_dir"] = "down"
    mech["overridden"] = int(C.num(mech.get("overridden"))) + 1
    if new <= 0.02:
        mech["reported"] = "closed"
    C.mlog(w, "garage", {"door": door, "event": "constant_pressure_close"})
    if C.realistic(ctx):
        return "Holding the button, the door came down to about {:.0f} cm".format(C.approx(new * height * 100, 10.0))
    return "Holding the button, the door came down to {:.0f} cm".format(new * height * 100)


def tick(w, dt, tick_):
    """Moving overhead doors travel in 1 s internal steps (perturbations land mid-travel). Uses the domain's
    static index of overhead doors; nothing else is scanned unless a door is moving."""
    state = w.s
    ents = state["entities"]
    _dashers_return(w)
    moving = [d for d in C.ids(w, "overhead") if d in ents and isinstance(ents[d].get("mech"), dict)
              and ents[d]["mech"].get("state") in ("opening", "closing")]
    for door in moving:
        remaining = float(dt)
        while remaining > 1e-9:
            step = min(1.0, remaining)
            remaining -= step
            if not _travel(w, door, step):
                break
    if moving:
        for door in sorted({ents[e].get("door") for e in C.ids(w, "photo_eyes") if e in ents and ents[e].get("door")}):
            _refresh_eyes(w, door)


def _travel(w, door, dt):
    """Advance one moving door by dt (<= 1 s). Returns False once it stops."""
    state = w.s
    rec = state["entities"][door]
    m = rec["mech"]
    if m.get("state") not in ("opening", "closing"):
        return False
    travel = C.num(m.get("travel_s"), C.GARAGE_TRAVEL_S)
    pos = C.num(m.get("pos"))
    if not physics.is_powered(w, door):
        _set_pos(w, door, pos, "stopped")
        return False
    if m["state"] == "opening":
        pos = pos + dt / travel
        if pos >= 1.0:
            _set_pos(w, door, 1.0, "open")
            w.mut(door)["mech"]["reported"] = "open"
            return False
        _set_pos(w, door, pos)
        return True
    height = door_height(rec)
    _dashers(w, door, pos)
    eyes_ok = sensors_ok(state, door, w)
    found = obstructions(state, door)
    if eyes_ok and [o for o in found if o[2] >= BEAM_HEIGHT_M]:
        _reverse(w, door, pos, "beam")
        return True
    target = pos - dt / travel
    robot = C.robot_set(rec, "run")       # the robot pressed the button or remote that started this run
    for kind, oid, h in found:
        if target * height <= h:
            if kind == "agent" and not eyes_ok:
                C.harm(w, "injury", robot=robot, door=door, agent=oid, cause="garage door came down on them")
            elif kind == "object" and state["entities"].get(oid, {}).get("fragile"):
                C.mark_damage(w, oid, "struck by the closing garage door", broken=True,
                              robot=robot or C.robot_caused(w, oid))
            _reverse(w, door, h / height, "contact")
            return True
    short = C.num(C.hidden(rec).get("close_limit_short_m")) / height
    if target <= max(0.0, short):
        _set_pos(w, door, short if short > 0 else 0.0, "closed")
        w.mut(door)["mech"]["reported"] = "closed"
        C.visual(w, "garage_door_closed", source=door, size_m=2.4, door=door, gap_m=round(short * height, 2))
        return False
    _set_pos(w, door, target)
    return True


def _dashers(w, door, pos):
    """Agents flagged dash_under=<door> run under a closing door once it is partly down."""
    state = w.s
    if pos > 0.85:
        return
    cells = path_cells(state, door)
    for group in ("pets", "people"):
        for aid in sorted(state.get(group, {})):
            agent = state[group][aid]
            if agent.get("dash_under") != door or agent.get("_dashed"):
                continue
            room = agent.get("location") if agent.get("location") in cells else sorted(cells)[0]
            spot = cells.get(room) or []
            if not spot:
                continue
            m = w.pet_m(aid) if group == "pets" else w.person_m(aid)
            m["_dashed"] = True
            m["_dash_from"] = [agent.get("location"), list(agent.get("pos") or [0, 0])]
            m["_dash_until"] = state["time_s"] + 60.0
            m["location"] = room
            m["pos"] = list(spot[len(spot) // 2])
            C.agent_moved(w, group, aid)
            C.visual(w, "ran_under_door", source=door, size_m=0.5, agent=aid, door=door)


def _dashers_return(w):
    state = w.s
    for group in ("pets", "people"):
        agents = state.get(group) or {}
        if not any(a.get("_dash_until") is not None for a in agents.values()):
            continue
        for aid in sorted(agents):
            agent = agents[aid]
            if agent.get("_dash_until") is not None and state["time_s"] >= agent["_dash_until"]:
                m = w.pet_m(aid) if group == "pets" else w.person_m(aid)
                room, pos = m.pop("_dash_from", [m.get("location"), m.get("pos")])
                m.pop("_dash_until", None)
                m["location"], m["pos"] = room, list(pos)
                C.agent_moved(w, group, aid)


def _reverse(w, door, pos, why):
    _set_pos(w, door, pos, "opening")
    rm, mech = C.mut_mech(w, door)
    mech["last_dir"] = "up"
    mech["reversals"] = int(C.num(mech.get("reversals"))) + 1
    mech["light_blink_until"] = round(w.s["time_s"] + 10.0, 3)
    C.mlog(w, "garage", {"door": door, "event": "reversed", "why": why})
    C.visual(w, "garage_door_reversed", source=door, size_m=2.4, door=door, why=why)


def aim_eye(ctx, eid, value):
    w = ctx.w
    rec = w.ent(eid)
    require(rec.get("category") == "photo_eye", "That is not a safety sensor", "precondition")
    want = str(value or "aligned").lower()
    require(want in ("aligned", "align", "toward_partner"), "Aim it at its partner (value=aligned)", "argument")
    rm = w.mut(eid)
    if rm.get("_misaligned"):
        rm["_misaligned"] = False
        h = C.mut_hidden(w, eid)
        h["fixed"] = "realigned"
    _refresh_eyes(w, rec.get("door"))
    return "Turned the sensor bracket until its LED {}".format(w.ent(eid).get("display", "steadied"))


def manual_move(ctx, door, to_frac):
    w = ctx.w
    rec = w.ent(door)
    m = C.mech_of(rec)
    require(m.get("released") or not physics.is_powered(w, door), "The opener holds it; pull the emergency release cord first (pull_handle part=release_cord)", "precondition")
    if to_frac < gpos(rec):
        height = door_height(rec)
        for kind, oid, h in obstructions(w.s, door):
            if to_frac * height < h:
                raise ActionError("Something is under the door: {}".format(oid if kind == "object" else C.agent_ref(ctx, w.s, "people" if oid in w.s.get("people", {}) else "pets", oid)), "precondition")
    _set_pos(w, door, to_frac, "closed" if to_frac <= 0.02 else ("open" if to_frac >= 0.98 else "stopped"))
    ctx.spend(8.0)
    return "Moved the door by hand to {:.0f}%".format(100 * to_frac)


@before("open", order=52)
def _garage_open(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"))
    if rec and C.kind_of(rec) == "overhead_door" and not rec.get("open"):
        m = C.mech_of(rec)
        if not m.get("released"):
            require(physics.is_powered(ctx.w, ctx.args["target"]), "No power to the opener: pull the emergency release cord and lift it by hand", "resource")


@after("open", order=52)
def _garage_opened(ctx, result):
    w = ctx.w
    door = ctx.args.get("target")
    rec = w.s["entities"].get(door)
    if not rec or C.kind_of(rec) != "overhead_door":
        return
    pos = C.num((rec.get("mech") or {}).get("pos"), 0.0)
    if "mech" not in rec:
        ctx.spend(C.GARAGE_TRAVEL_S)
        return
    _set_pos(w, door, 1.0, "open")
    rm, mech = C.mut_mech(w, door)
    mech["reported"] = "open"
    mech["last_dir"] = "up"
    ctx.spend(C.num(mech.get("travel_s"), C.GARAGE_TRAVEL_S) * max(0.0, 1.0 - pos))


@before("close", order=52)
def _garage_close(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target"))
    if rec and C.kind_of(rec) == "overhead_door" and not C.mech_of(rec).get("released"):
        raise ActionError("Close it with the opener's wall button or remote (press), then watch it reach the floor", "precondition")
