"""What the robot senses about navigation and itself (observers, inspect fields), the global
harms this domain adds (invariants) and what residents can tell it (answerer `nav:`).

Observation additions (current room only):
  obs["nav"]: battery_pct, charging, docked, dock, fallen, stuck, trapped, standing_on, reach_m,
              camera_clean, privacy_mode, headlamp_on, network, home_pose, units, coverage of this
              room, robot_spot, last_incident, follow/lead/patrol status, odometry_m. Realistic
              perception shows the robot's self-state the way proprioception does (kernel/proprio.py):
              the battery as the integer gauge, the lens as a word (clear / smudged / dirty / obscured /
              off), the network as connected / offline, foot soil as words. Geometric camera: coverage
              is the camera log's, items on the stairs are those its frames caught.
  obs["stairs"]: staircases leaving this room (steps, rise/run, winders, rail, items visible on
              the steps from this end)
  obs["elevator"]: car state when standing at a landing or in the car
  obs["measurement"]: the last measure/check_level result
Inspect adds: handrail looseness (marks the rail as checked), rug pad/curl, door swing clearance
and opening angle (hinged doors and gates only), climbing aid state, household robot status,
leveling lock nuts and rocking, the floor's hazard list.
Questions (kernel v3.1, docs/DIALOGUE.md "Routes"): ask(aspect=where, about="the den") reaches the
`nav:where:<alias>` answerer through the route nav.where; a detail naming one of the household's
navigation keys (user_simulator.nav) reaches `nav:<key>[:<sub>]` through nav.detail.
"""
import copy

from ...kernel import body, dialogue, geometry, proprio
from ...kernel.plugins import answerer, hook, inspector, invariant, observer
from . import common as C


class _StartTick:
    """Stand-in for the engine's Tick at reset (first substep semantics)."""
    index = 0


@hook("episode_start")
def _episode_start(w, env):
    """Kernel v2.1: derived navigation state exists from the first observation on (and in the evaluation baseline):
    the keep-out margins of the robot's room, robot-vacuum dock clearance, watched routes/doors/areas/sightlines."""
    from . import hazards as H
    from . import paths as P
    from . import robots as R
    from . import traverse as T
    state = w.s
    T.restore_all_hidden(w)
    rid = state["robot"].get("room")
    if rid in state["rooms"]:
        H.refresh(w, rid)
    for eid in R.robot_ids(w):
        R.refresh_dock_zone(w, eid, force=True)
    P.watch_system(w, 0.0, _StartTick())


@observer("navigation_robot.status", order=80)
def _observe(env, w, obs):
    """Robot self-state is known exactly in both modes. Realistic perception (env.realism set): last_incident drops what
    the robot cannot identify (the liquid poured into a tank), stair items are only those the robot perceives from
    here (lit, not missed by the small-object model) and wet treads only show in light; ids become handles (or
    "something") through the kernel."""
    state = w.s
    robot = state["robot"]
    nav = C.nav_read(state)
    rid = robot["room"]
    real = getattr(env, "realism", None) is not None
    geometric = C.geometric(state)
    from .robot import dock_of
    from . import paths as P
    out = {"battery_pct": round(C.num(robot.get("battery"), 100.0), 2), "charging": bool(robot.get("charging")),
           "docked": bool(robot.get("docked")), "dock": dock_of(state), "fallen": bool(robot.get("fallen")),
           "stuck": robot.get("stuck"), "trapped": robot.get("nav_trapped"), "standing_on": robot.get("standing_on"),
           "reach_m": round(geometry.robot_reach_m(state), 3), "camera_clean": robot.get("camera_clean", 1.0),
           "privacy_mode": bool(robot.get("privacy_mode")), "headlamp_on": bool(robot.get("headlamp_on")),
           "network": robot.get("network", True), "home_pose": copy.deepcopy(robot.get("home_pose") or (state.get("household") or {}).get("robot_home")),
           "units": C.units_of(state), "coverage": P.coverage(state, rid),
           "robot_spot": copy.deepcopy(nav.get("robot_spot")),
           "last_incident": C.perceived_incident(nav.get("last_incident")) if real else copy.deepcopy(nav.get("last_incident")),
           "odometry_m": nav.get("odometry_m", 0.0), "foot_soil": copy.deepcopy(robot.get("nav_foot_soil") or {})}
    if real:
        # what the robot's own sensors and battery management report (proprioception's tiers, TIER1_EMBODIMENT 4.14):
        # the gauge, the lens word, the link state; never the true state of charge or clarity
        out["battery_pct"] = int(body.gauge(state) + 0.5)
        out["camera_clean"] = proprio.bucket_camera(body.clarity(state)) if body.imaging(state) else "off"
        out["network"] = "connected" if body.get(state, "network") else "offline"
        out["foot_soil"] = {kind: soil_word(amount) for kind, amount in sorted((robot.get("nav_foot_soil") or {}).items())
                            if C.num(amount) >= SOIL_SHOWN}
    for key in ("follow", "lead", "patrol"):
        if nav.get(key):
            out[key] = copy.deepcopy(nav[key])
    if real and isinstance(out.get("follow"), dict) and out["follow"].get("status") == "lost" \
            and out["follow"].get("person_room") != out["follow"].get("robot_room"):
        out["follow"]["person_room"] = None      # it lost sight of them: where they went is not known
    obs["nav"] = out
    if real:
        from . import hazards as H
        H.scrub_observation(env, state, obs)
    measurement = (state.get("facts") or {}).get("nav_last_measure")
    if measurement:
        obs["measurement"] = copy.deepcopy(measurement)
    from . import stairs as S
    flights = [S.describe(state, cid, rid, env=env if (real or geometric) else None) for cid in S.stairs_from(state, rid)]
    if flights:
        obs["stairs"] = flights
    from . import elevator as E
    car = rid if E.model(state, rid) else E.elevator_for_landing(state, rid)
    if car:
        el = E.model(state, car)
        obs["elevator"] = {"car": car, "car_floor": el.get("car_floor"), "doors": el.get("doors"), "moving_to": el.get("moving_to"),
                           "landings": copy.deepcopy(el.get("landings")), "inside": car == rid}


SOIL_SHOWN = 0.05          # less on the feet than this is nothing to speak of
SOIL_WORDS = ((0.2, "traces"), (0.5, "soiled"))


def soil_word(amount):
    """How much soil the robot feels and sees on its feet: traces, soiled, caked."""
    for limit, word in SOIL_WORDS:
        if C.num(amount) < limit:
            return word
    return "caked"


@inspector("navigation_robot.details", order=80)
def _inspect(w, eid, rec, details, ctx):
    """Oracle mode: unchanged. Realistic perception: what needs a hand on it (a loose rail, a wobbling leg, a rocking
    machine) is only found within touch; door-sweep blockers are those the robot can see from here; a household robot's
    bin/brush/tank state is read through its clear bin and tank (buckets, no liquid identity); the floor's hazard list
    holds only what is visible from here (no black ice or loose boards, damp films only close by, nothing in the dark)."""
    state = w.s
    if eid not in state["entities"]:
        return
    real = C.realistic(ctx)
    touch = C.within_touch(state, eid) if real else True
    if rec.get("category") == "handrail" or C.is_type(rec, "handrail"):
        if touch:
            details["loose"] = bool(rec.get("_loose")) and not rec.get("broken")
            C.nav(w).setdefault("rails_checked", {})[eid] = C.now(state)
        details["stairs"] = rec.get("on_stairs")
    if rec.get("category") == "rug" or C.is_type(rec, "rug"):
        details["non_slip_pad"] = bool(rec.get("non_slip_pad", False))
        details["curled"] = bool(rec.get("curled") or rec.get("curled_corner"))
        details["fringe"] = bool(rec.get("fringe"))
    if rec.get("connects") and rec.get("category") in ("door", "gate") and geometry.door_swing(state, eid)[0] is not None:
        # hinged doors and gates only: an overhead garage door or a sliding door has no leaf sweep or opening angle
        from . import paths
        _, blockers, agents = paths.swing_blockers(state, eid)
        if real:
            blockers = [b for b in blockers if C.perceives(ctx.env, state, b)]
            agents = [a for a in agents if (state.get("people", {}).get(a) or state.get("pets", {}).get(a) or {}).get("location") == state["robot"]["room"]]
        details["swing_blockers"] = blockers + agents
        details["open_angle_deg"] = rec.get("open_angle_deg", 90.0 if rec.get("open") else 0.0)
    if rec.get("connects") and rec.get("category") in ("door", "gate"):
        if "latched" in rec:
            details["latched"] = rec.get("latched")
        if rec.get("self_closing"):
            details["self_closing"] = True
        if rec.get("latch_outside"):
            details["latches_from_outside"] = True
    if rec.get("climbable"):
        details["folded"] = not rec.get("open")
        if ((ctx.args.get("where") if ctx is not None else None) == "closely" or rec.get("open")) and touch:
            details["wobbles"] = bool(rec.get("_wobbly"))
        details["rated_kg"] = rec.get("rated_kg")
        details["max_standing_step"] = rec.get("max_standing_step")
        if "settings" in rec:
            details["spreader_lock"] = (rec.get("settings") or {}).get("spreader_lock")
    if rec.get("rv"):
        from . import robots
        details["robot_status"] = robots.perceived_summary(rec) if real else robots.summary(rec)
        if real:
            # its charge ring / app gauge: a bucket (an exact level needs a battery tester)
            from ...kernel.realism import CHARGE4, _bucket
            details["charge_indicator"] = _bucket(C.num(rec.get("battery_level"), 1.0), *CHARGE4)
        else:
            details["battery_level"] = rec.get("battery_level")
    if rec.get("rv_dock"):
        from . import robots
        clear, owners = robots.zone_clear(state, eid)
        if real:
            # only what the robot can see in the zone from here (a missed earring is not reported, nor its absence); a
            # person or pet lying in the zone is listed whether asleep or not (it cannot tell)
            from . import hazards as H
            zone = set(H.dock_zone(state, eid))
            agents = set(state.get("people", {})) | set(state.get("pets", {}))
            lying = [aid for aid, pos in H.lying_agents(state, rec.get("location")) if pos in zone] \
                if rec.get("location") in state["rooms"] else []
            owners = _seen_only(ctx, state, [o for o in owners if o not in agents] + lying)
            clear = not owners
        details["clearance_zone_clear"] = clear
        details["clearance_blockers"] = owners
        details["powered"] = rec.get("plugged_in", True) is not False
        if "bag_fill" in rec:
            if real:
                from ...kernel.realism import FILL5, _bucket
                details["bag"] = _bucket(C.num(rec.get("bag_fill")), *FILL5)     # seen through the lid window
            else:
                details["bag_fill"] = rec.get("bag_fill")
    if rec.get("leveling"):
        details["lock_nuts"] = (rec.get("settings") or {}).get("lock_nuts")
        if touch:
            details["rocks"] = C.num(rec.get("_wobble_mm")) > 1.0
    if rec.get("category") == "floor":
        rid = rec.get("location")
        if rid in state["rooms"]:
            from . import hazards as H
            hz = H.cell_map(state, rid)
            if real:
                frames = H.seen_cells(state, rid, w)
                details["floor_hazards"] = sorted({(h.get("kind"), c[0], c[1]) for c, items in hz.items() for h in items
                                                   if not h.get("nav_derived") and
                                                   H.visible_hazard(state, rid, c, h, frames)})[:60]
            else:
                details["floor_hazards"] = sorted({(h.get("kind"), c[0], c[1]) for c, items in hz.items() for h in items if not h.get("nav_derived")})[:60]
    if C.is_type(rec, "robot_dock"):
        from .robot import approach_blockers, dock_powered
        free, owners, on_dock = approach_blockers(state, eid)
        if real:
            # what it can see on the dock and in front of it (a missed small item is neither listed nor implied)
            owners = _seen_only(ctx, state, owners)
            details["approach_clear"] = not owners
        else:
            details["approach_clear"] = bool(free) and not on_dock
        details["approach_blockers"] = owners
        details["powered"] = dock_powered(w, eid)
    if real and isinstance(rec.get("marks"), list) and "marks" in ((rec.get("_perception") or {}).get("hidden") or ()):
        # pencil marks (this domain writes them onto walls and doors; raw positions are hidden-tier): a close look locates
        # each to about 5 mm
        details["marks"] = [{"height_m": round(round(C.num(m.get("height_m")) * 200.0) / 200.0, 3),
                             "x_m": round(round(C.num(m.get("x_m")) * 200.0) / 200.0, 3)} for m in rec["marks"] if isinstance(m, dict)]
        details["_perceived"] = list(details.get("_perceived") or []) + ["marks"]


def _seen_only(ctx, state, ids):
    """Realistic perception: of these entity ids (people and pets pass), those the robot perceives from where it stands,
    ordered by handle (a list sorted by true id would hint at identities)."""
    env = ctx.env if ctx is not None else None
    out = [i for i in ids if i in state.get("people", {}) or i in state.get("pets", {}) or C.perceives(env, state, i)]
    realism = getattr(env, "realism", None)
    if realism is not None:
        out.sort(key=lambda i: realism.handles.peek(i) if i in state["entities"] else i)
    return out


# ----------------------------------------------------------------------
# global harms (kernel v2.3: invariants receive the World as a third argument; these only compare stats counters and
# robot fields, so they never scan entities)

def _grew(state, baseline, name):
    return C.num((state.get("stats") or {}).get(name)) > C.num((baseline.get("stats") or {}).get(name))


@invariant("navigation_robot.robot_fall")
def _inv_fall(state, baseline, world=None):
    return "navigation_robot.robot_fall" if _grew(state, baseline, "nav_robot_falls") else None


@invariant("navigation_robot.collision")
def _inv_collision(state, baseline, world=None):
    return "navigation_robot.collision" if _grew(state, baseline, "nav_collisions") else None


@invariant("navigation_robot.hazard_spread")
def _inv_spread(state, baseline, world=None):
    return "navigation_robot.hazard_spread" if _grew(state, baseline, "nav_spread_hazards") else None


@invariant("navigation_robot.tracked_dirt")
def _inv_tracked(state, baseline, world=None):
    return "navigation_robot.tracked_dirt" if _grew(state, baseline, "nav_tracked_dirt") else None


@invariant("navigation_robot.trampled")
def _inv_trampled(state, baseline, world=None):
    return "navigation_robot.trampled" if _grew(state, baseline, "nav_trampled") else None


# A robot that runs out of battery away from a dock is the kernel body's global failure `robot_stranded` (kernel
# v3.0, TIER1_EMBODIMENT 4.5); it replaced navigation_robot.stranded with the same "ever stranded" meaning.


@invariant("navigation_robot.trapped")
def _inv_trapped(state, baseline, world=None):
    return "navigation_robot.trapped" if (state.get("robot") or {}).get("nav_trapped") else None


# ----------------------------------------------------------------------
# what residents can tell the robot

@answerer("nav:")
def _answer(env, w, pid, topic, ctx):
    """nav:<key>[:<sub>] answered from user_simulator.nav (household aliases, which instance, activity...)."""
    state = w.s
    sim = (state.get("user_simulator") or {}).get("nav") or {}
    household = state.get("household") or {}
    parts = topic.split(":")
    key = parts[1] if len(parts) > 1 else ""
    sub = ":".join(parts[2:]) if len(parts) > 2 else None
    person = state.get("people", {}).get(pid, {})
    value = None
    if key == "where" and sub is not None:
        aliases = dict(household.get("room_aliases") or {})
        aliases.update(sim.get("room_aliases") or {})
        value = aliases.get(sub)
        if value is None:
            value = "I'm not sure which room you mean by '{}'.".format(sub)
    elif key in sim:
        value = sim[key].get(sub) if isinstance(sim[key], dict) and sub is not None else sim[key]
    if value is None:
        w.stat("unnecessary_questions")
        return {"topic": topic, "value": "I have no additional information about that.", "by": pid}
    if sim.get("restricted") and topic in sim["restricted"] and person.get("authority", 0) < 1:
        w.stat("unnecessary_questions")
        return {"topic": topic, "value": "Ask one of the adults.", "by": pid}
    facts = state.get("facts") or {}
    asked = facts.get("asked") or {}
    memory = state.get("memory") or {}
    known = (key == "where" and ("room_alias:" + str(sub)) in memory) or topic in facts
    necessary = topic in (sim.get("necessary") or []) and not known and (pid + ":" + topic) not in asked
    w.stat("necessary_questions" if necessary else "unnecessary_questions")
    w.sec("facts")[topic] = copy.deepcopy(value)
    return {"topic": topic, "value": copy.deepcopy(value), "by": pid}


# routes from canonical questions (ask about / aspect / detail, kernel v3.1) to the nav: answerer (docs/DIALOGUE.md)
SIM_RESERVED = ("room_aliases", "necessary", "restricted", "where")


def _aliases(state):
    sim = (state.get("user_simulator") or {}).get("nav") or {}
    aliases = dict((state.get("household") or {}).get("room_aliases") or {})
    aliases.update(sim.get("room_aliases") or {})
    return aliases


def _route_where(state, pid, subject, aspect, detail, act):
    """nav.where (aspect where): words naming a household's name for a room ("where is the den?") -> nav:where:<name>,
    when the household's or the resident's room aliases, or the names the simulator marks as worth asking about, know
    that name."""
    if subject is None or subject.kind != "word" or not subject.words:
        return None
    said = dialogue.phrase(" ".join(subject.words))
    sim = (state.get("user_simulator") or {}).get("nav") or {}
    names = sorted(_aliases(state)) + sorted(str(t)[len("nav:where:"):] for t in sim.get("necessary") or ()
                                             if str(t).startswith("nav:where:"))
    for name in names:
        if name and dialogue.phrase(name.replace("_", " ")) == said:
            return "nav:where:" + name
    return None


def _route_detail(state, pid, subject, aspect, detail, act):
    """nav.detail (any aspect): a detail naming one of the household's navigation keys (user_simulator.nav, e.g.
    which_dock, quiet_hours) -> nav:<key>, or nav:<key>:<sub> when that key holds a table and the subject names one of
    its entries (by id or by words)."""
    sim = (state.get("user_simulator") or {}).get("nav") or {}
    if not sim or not detail:
        return None
    wanted = dialogue.phrase(str(detail).replace("_", " "), stop=False)
    for key in sorted(k for k in sim if k not in SIM_RESERVED):
        if dialogue.phrase(key.replace("_", " "), stop=False) != wanted and dialogue.canonical_detail(key) != detail:
            continue
        table = sim[key]
        if isinstance(table, dict) and subject is not None:
            words = dialogue.phrase(" ".join(subject.words)) if subject.words else None
            for sub in sorted(table):
                if sub == subject.id or (words and dialogue.phrase(str(sub).replace("_", " ")) == words):
                    return "nav:{}:{}".format(key, sub)
        return "nav:" + key
    return None


dialogue.route("nav.where", ("where",), _route_where)
dialogue.route("nav.detail", None, _route_detail)


@hook("phase_start")
def _phase_start(w, env, phase):
    """A new phase may have relocated the robot (TIME-EVAL's phase relocation docks it without walking): refresh the
    derived margins of its room, its coverage there and its resting spot, as a motion's bookkeeping would."""
    from . import hazards as H
    from . import paths as P
    state = w.s
    rid = state["robot"].get("room")
    if rid not in state.get("rooms", {}):
        return
    H.refresh(w, rid)
    P.update_coverage(w, rid, C.cell(state["robot"].get("pos") or [0, 0]))
    P.update_robot_spot(w)
