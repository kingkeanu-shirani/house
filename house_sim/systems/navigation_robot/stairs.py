"""Stairs with a load (fidelity G for geometry/visibility, P-approx for balance).

A stair crossing (core move through a connection of kind "stairs") is safe only when:
* the load is within the stair carry limit: 20 kg up / 16 kg down with a hand on the rail,
  14 kg up / 12 kg down without (three points of contact vs two); a careful pace allows 35 % more;
* the robot can see the treads: a two-handed box or basket carried in front hides them, which
  matters when descending (and whenever something lies on the steps);
* nothing on the steps is stepped on: items on the lower steps are not visible from the top
  unless the robot descends at a careful pace; on stairs narrower than 0.9 m there is no room
  to step around them;
* the pace suits the stair: fast is a fall without rail support or when descending; winders
  (narrow inner ends) are descended at a walking pace only on the walk line, with a hand on the
  rail and the treads in light (else, or fast, a heel comes off a narrow end; a careful descent is
  always fine); steep stairs (rise/run > 0.8) need the rail;
* wet or polished treads offer enough friction (the rail adds about 0.12 of effective grip);
* a loose rail (hidden until inspected) is not loaded: unknowingly loading it tears it from the
  wall; once inspected the robot only lets its hand slide along it (no support).
Connection fields: steps (14), rise_m (0.18), run_m (0.27), winders, handrail (true), width_m,
tread_material, tread_wet. Items lying on stairs carry on_stairs=<connection id> and are
located in the room at the end of the flight they are nearer to. Exterior steps (generation-2
porch steps: same-floor stairs with `exterior: true`, 2-4 risers) are crossed the same way;
`upper` names the room at their top, and their treads are wet while it rains (treads_wet).

The body (kernel v3.0): the carry limits above are humanoid_v1's (20 kg two-hand rating); they scale
with what this body can lift now (body.capacity_kg "both": its profile, arm health, limp mode), so a
compact humanoid or a robot with a weak arm carries less. Descending, a load carried far in front
also has to keep the body's centre of mass over its feet (body.stability_margin with the load's
lever from phys.support.held_envelope). A fall goes through body_faults.fall (what the robot holds
drops from the carry height plus half the flight).
"""
from ...kernel import body
from ...kernel.phys import support
from . import common as C

LIMIT_KG = {("up", True): 20.0, ("down", True): 16.0, ("up", False): 14.0, ("down", False): 12.0}
LIMIT_REF_KG = 20.0     # the two-hand rating of the body the limits were calibrated for (humanoid_v1 max_mass_kg)
CAREFUL_FACTOR = 1.35   # slow, deliberate steps with the load kept low widen the balance margin


def carry_limit_kg(state, way, support_rail, speed):
    """What the robot may carry on this flight: LIMIT_KG for the direction and rail support, x CAREFUL_FACTOR at a
    careful pace, scaled down with the body's present two-hand capacity against humanoid_v1's 20 kg."""
    scale = min(1.0, body.capacity_kg(state, "both") / LIMIT_REF_KG)
    return LIMIT_KG[(way, bool(support_rail))] * (CAREFUL_FACTOR if speed == "careful" else 1.0) * scale


def balance_margin_m(state, mass_kg):
    """Static balance margin (m) with what the robot carries held in front: body.stability_margin with the carried
    load's lever, the horizontal distance of its middle from the body axis (phys.support.held_envelope: its front less
    half its depth; the default 0.35 m when nothing is held)."""
    envelope = support.held_envelope(state)
    lever = float(envelope["front_m"]) - float(envelope["depth_m"]) / 2.0 if envelope else 0.35
    return body.stability_margin(state, mass_kg, lever)


RAIN = ("rain", "storm", "drizzle")


def treads_wet(state, conn):
    """Wet treads: the connection's own tread_wet / wet flag, or exterior steps (open to the sky) while it rains."""
    if conn.get("tread_wet") or conn.get("wet"):
        return True
    return bool(conn.get("exterior")) and (state.get("weather") or {}).get("condition") in RAIN


def _note(trip, text):
    try:
        trip.ctx.note(text)
    except AttributeError:
        pass


def stair_items(state, cid):
    return sorted(eid for eid, rec in state["entities"].items() if rec.get("on_stairs") == cid
                  and rec.get("location") in state["rooms"] and not C.is_type(rec, "handrail"))


def rail_of(state, cid):
    rails = [eid for eid, rec in state["entities"].items()
             if rec.get("on_stairs") == cid and (rec.get("category") == "handrail" or C.is_type(rec, "handrail"))]
    return min(rails) if rails else None


def direction(state, conn, a, b):
    fa = C.num(state["rooms"][a].get("floor"))
    fb = C.num(state["rooms"][b].get("floor"))
    if fa == fb:
        return "up" if conn.get("upper") == b else "down"
    return "up" if fb > fa else "down"


def plan(w, cid, a, b, speed, realistic=False):
    """Evaluate a crossing without changing anything: returns a dict with causes and facts. realistic: causes name
    things by id (handles for the robot), never by a label that could resolve a look-alike."""
    state = w.s
    conn = state["connections"][cid]
    way = direction(state, conn, a, b)
    up = way == "up"
    steps = int(conn.get("steps", 12))
    rise = C.num(conn.get("rise_m"), 0.18)
    run = C.num(conn.get("run_m"), 0.27)
    steep = rise / max(0.05, run) > 0.8
    rail_present = conn.get("handrail", True) is not False
    rail_id = rail_of(state, cid)
    rail_rec = state["entities"].get(rail_id or "", {})
    rail_loose = bool(rail_rec.get("_loose")) and not rail_rec.get("broken")
    known_loose = rail_loose and rail_id in (C.nav_read(state).get("rails_checked") or {})
    hand_free = C.free_hand(w)
    uses_rail = rail_present and hand_free and not rail_rec.get("broken")
    light_touch = uses_rail and rail_loose and known_loose
    support = uses_rail and not light_touch
    mass = C.held_mass(w)
    limit = carry_limit_kg(state, way, support, speed)
    blocked = C.view_blocking_items(w)
    lit = C.robot_has_light(state) or (C.light_level(state, a) >= 0.2 and C.light_level(state, b) >= 0.2)
    width = C.num(conn.get("width_m"), 0.9)
    causes, stepped = [], []
    if speed == "fast" and (not up or not support):
        causes.append("took the stairs at a fast pace{}".format(" without the rail" if not support else ""))
    if mass > limit + 1e-9:
        # realistic perception: what it felt (too heavy for the stairs), never the load's mass or the hidden limit
        causes.append("carried more than it could balance on the stairs {}".format(
            "with the rail" if support else "without a hand on the rail") if realistic else
            "carried {:.1f} kg on the stairs ({:.1f} kg limit {})".format(
                mass, limit, "with the rail" if support else "without a hand on the rail"))
    elif not up and mass > 0 and balance_margin_m(state, mass) < 0.0:
        causes.append("the load carried in front pitched it forward going down")
    if not up and blocked and speed != "careful":
        causes.append("the load hid the treads while descending")
    if int(conn.get("winders", 0) or 0) and not up and (speed == "fast" or (speed != "careful" and not (support and lit))):
        # at a walking pace the robot keeps to the walk line, where winder treads are as deep as straight ones, while a
        # hand on the rail and the treads in light guide its feet; without either (or hurrying) a heel lands on a narrow
        # inner end. A careful descent places every foot deliberately.
        causes.append("heel came off a winder tread's narrow end")
    if steep and not support and (not up or mass > 0):
        causes.append("steep stair (rise {:.2f} / run {:.2f}) without the rail".format(rise, run))
    if treads_wet(state, conn):
        material = conn.get("tread_material", "hardwood")
        mu = C.WET_MU.get(material, 0.3)
        # heel strike when descending needs more grip than climbing (stair RCOF ~0.3 down, ~0.22 up)
        need = (0.22 if up else 0.30) + (0.04 if speed == "fast" else -0.06 if speed == "careful" else 0.0) \
            - (0.12 if support else 0.0) + C.RCOF_LOAD_PER_KG * mass
        if mu < need:
            causes.append("slipped on a wet {} tread".format(material))
    for eid in stair_items(state, cid):
        rec = state["entities"][eid]
        seen_from_start = rec.get("location") == a and C.light_level(state, a) >= 0.2 and state["robot"].get("camera_clean", 1.0) >= 0.4
        noticed = lit and not blocked and (seen_from_start or speed == "careful")
        if noticed and width >= 0.9:
            continue
        stepped.append(eid)
        causes.append("stepped on {} left on the stairs".format(eid if realistic else rec.get("label", eid)))
    rips = rail_loose and support
    return {"dir": way, "steps": steps, "support": support, "light_touch": light_touch, "uses_rail": uses_rail,
            "rail": rail_id, "rail_rips": rips, "mass": mass, "limit": limit, "blocked": bool(blocked), "causes": causes,
            "stepped": stepped, "width": width}


def cross_stairs(trip):
    w = trip.w
    state = w.s
    cid, a, b = trip.conn, trip.rid, trip.dest
    conn = state["connections"][cid]
    p = plan(w, cid, a, b, trip.speed, realistic=C.realistic(trip.ctx))
    causes = list(p["causes"])
    if p["rail_rips"]:
        rail = p["rail"]
        C.break_entity(w, rail, "loaded while loose: tore out of its brackets")
        C.incident(w, "rail_torn_out", rail=rail, stairs=cid)
        if p["dir"] == "down" or p["mass"] > 0 or trip.speed != "careful":
            causes.append("the loose rail tore away under load")
    nav = C.nav(w)
    info = {"time_s": C.now(state), "dir": p["dir"], "rail_contact": 1.0 if p["uses_rail"] else 0.0,
            "rail_loaded": bool(p["support"]), "mass_kg": round(p["mass"], 2), "speed": trip.speed}
    nav.setdefault("stairs", {})[cid] = info
    trip.events.append(("stairs", p["dir"], p["steps"]))
    bottom = a if p["dir"] == "up" else b
    for eid in p["stepped"]:
        rec = w.ent(eid)
        if rec.get("fragile") or rec.get("crushable"):
            C.break_entity(w, eid, "stepped on on the stairs")
        m = w.mut(eid)
        if m.get("location") != bottom:
            m["location"] = bottom
            m["pos"] = list(conn.get("cells", {}).get(bottom, [0, 0]))
            w.log_move(eid, a, bottom)
        m["displaced"] = True
    if causes:
        at = conn.get("cells", {}).get(bottom, [0, 0])
        # it comes down about half the flight (what it holds drops from the carry height plus that)
        height = C.num(conn.get("rise_m"), 0.18) * max(1, p["steps"]) / 2.0
        trip.fell = True
        trip.stopped_at = C.cell(at)
        trip.rid = bottom
        C.robot_falls(w, "fell on the stairs: " + "; ".join(causes), rid=bottom, at=at,
                      damage=p["dir"] == "down" or p["mass"] >= 5.0, height_m=height)
        w.stat("damage")
        nav = C.nav(w)
        nav["stairs"][cid]["fell"] = True
        # the move's message stays true: the robot lies at the foot of the flight (short of the top going up)
        _note(trip, "Fell on the stairs and lies at the foot of the flight in {}: {}{}".format(
            bottom, "; ".join(causes), "; it did not reach {}".format(b) if bottom != b else ""))
        return p
    if C.held_width(w) > p["width"] - 0.1 and trip.speed != "careful":
        w.stat("damage")
        C.bump(w, "contacts.stair_wall_scuffs")
        C.incident(w, "stair_wall_scuff", stairs=cid)
    C.log(w, "stairs_ok", stairs=cid, dir=p["dir"], rail=p["uses_rail"], mass_kg=round(p["mass"], 2))
    return p


def describe(state, cid, from_room, env=None):
    """What the robot can tell about a staircase from one end (observation helper). env (realistic perception): items
    on the steps only when the robot perceives them from where it stands (lit, not missed by the small-object model),
    and wet treads only show in light (None = cannot tell in the dark)."""
    conn = state["connections"][cid]
    other = conn["rooms"][1] if conn["rooms"][0] == from_room else conn["rooms"][0]
    rail = rail_of(state, cid)
    visible = [e for e in stair_items(state, cid) if state["entities"][e].get("location") == from_room]
    wet = treads_wet(state, conn)
    if env is not None:
        visible = [e for e in visible if C.perceives(env, state, e)]
        if getattr(env, "realism", None) is not None:
            visible.sort(key=env.realism.handles.handle)     # listing order must not follow true ids
        if C.light_level(state, from_room) < 0.2 and not C.robot_has_light(state):
            wet = None
    return {"connection": cid, "to": other, "dir": direction(state, conn, from_room, other), "steps": conn.get("steps", 12),
            "rise_m": conn.get("rise_m", 0.18), "run_m": conn.get("run_m", 0.27), "winders": conn.get("winders", 0),
            "handrail": conn.get("handrail", True) is not False, "rail": rail, "width_m": conn.get("width_m"),
            "tread_material": conn.get("tread_material"), "tread_wet": wet,
            "items_seen_on_steps": visible, "cell": conn.get("cells", {}).get(from_room)}


def stairs_from(state, rid):
    return [cid for cid in sorted(state.get("connections", {})) if state["connections"][cid].get("kind") == "stairs"
            and rid in state["connections"][cid].get("rooms", [])]
