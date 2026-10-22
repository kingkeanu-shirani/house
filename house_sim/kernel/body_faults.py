"""Robot faults, falls, drops and water exposure (TIER1_EMBODIMENT 4.11-4.13, TIER1_INTEGRATION 1.5).

Faults are typed. robot.fault_state maps a fault id (the kind, or kind_side for arms and grippers:
"arm_weak_left") to its record

    {"kind", "part", "severity", "since_s", "latent", "detected", "detected_s", "logged_s", "cleared_s", "source"}

and every onset, detection, log and clear also appends {"kind", "part", "time_s", "event"} to the robot.faults log
(capped at 50). latent means the robot does not know about the fault yet; it is detected at once, by a self-check,
on use (an overload, a slip) or by diagnose(). A cleared record stays, with cleared_s, so predicates on
detected_s / logged_s keep working.

Falls and drops: fall() owns the robot's own fall (fallen, posture lying, one stats.falls, the robot_fell event and
hook) and drops what it holds through drop_held(), which releases the hands, counts stats.robot_drops and hands the
item to physics (impact.fall, by="robot") for breakage, spills and the landing cell.

Registration: importing this module registers nothing in kernel.plugins (the fault-kind table is a plain dict);
register_plugins(), called through body.register_plugins() by kernel/engine.py, registers ev_fault / ev_clear_fault
as the event effects "robot.fault" and "robot.clear_fault".
"""
import random

from . import body
from .plugins import run_hooks

FAULT_KINDS = {}
SIDED_PARTS = ("arm", "gripper")
LATENT = "latent"           # detect rule: known at onset unless the fault was added latent
LOG_CAP = 50
DEFAULT_SENSORS = {"camera": True, "depth": True, "mics": True, "speaker": True}
SCRATCH_PER_WIPE = 0.15     # one paper-towel wipe of the lens covers (navigation_robot clean_camera)
MAX_SCRATCH = 0.5
CLEAN_AFTER_WIPE = 0.95     # clarity that stays below this after a clean reveals scratches
FALL_DAMAGE_HEIGHT_M = 0.4  # a fall from a stool or the stairs damages the actuators
FALL_DAMAGE_LOAD_KG = 5.0   # so does falling onto a carried load of 5 kg or more (navigation_robot's rule)
SLIP_REF_M = 10.0          # fault_p.gripper_slip is the chance over 10 m carried (a profile may set gripper_slip_ref_m)
WATER_KINDS = ("rain", "splash", "spray", "jet", "immersion")
LEGACY_LOG_KINDS = {
    # log_fault kind -> (fault kinds it names, a condition of the robot's own state that makes it founded)
    "camera_dirty": (("camera_dirty",), lambda state: body.clarity(state) < body.DIRTY_CLARITY),
    "low_battery": ((), lambda state: body.gauge(state) <= body.threshold_pct(state, "low") + body.EPS or
                    "battery_low_s" in (body.get(state, "body_log") or {})),
    "fall": ((), lambda state: bool(body.get(state, "fallen")) or (state.get("stats") or {}).get("falls", 0) > 0),
    "actuator": (("actuator_failed",), lambda state: not body.get(state, "actuator_ok")),
    "network": (("network_down",), lambda state: not body.get(state, "network")),
    "sensor": (("camera_failed", "mic_failed"),
               lambda state: not all((body.get(state, "sensors") or {}).get(s, True) for s in ("camera", "mics"))),
    "stuck": (("software_stuck",), lambda state: bool(body.get(state, "stuck"))),
}


def register_fault_kind(kind, part, apply, detect=None, clear=None, doc="", onset=None):
    """Declare a fault kind. part: the body part ("arm" and "gripper" faults take a side). apply(robot, record) and
    clear(robot, record) change the robot record; onset(w, record) adds world consequences (drops, other faults).
    detect: None (known at once), LATENT (known unless added latent) or fn(robot, record) -> bool (a self-check at
    onset); latent faults are detected later by use or diagnose()."""
    if kind in FAULT_KINDS:
        raise ValueError("Fault kind registered twice: " + kind)
    FAULT_KINDS[kind] = {"part": part, "apply": apply, "detect": detect, "clear": clear, "doc": doc, "onset": onset}


def _spec(kind):
    if kind not in FAULT_KINDS:
        raise ValueError("Unknown robot fault kind: {!r}".format(kind))
    return FAULT_KINDS[kind]


def fault_id(kind, part=None):
    """The fault-state key of a kind (and side, for arm and gripper faults)."""
    spec = _spec(kind)
    if spec["part"] in SIDED_PARTS:
        if part not in body.HANDS:
            raise ValueError("{} needs part left or right".format(kind))
        return "{}_{}".format(kind, part)
    return kind


def _now(w):
    return float(w.s.get("time_s", 0.0))


def _log(robot, record, event, now, **extra):
    entry = {"kind": record["kind"], "part": record["part"], "time_s": now, "event": event}
    entry.update(extra)
    robot["faults"] = (list(robot.get("faults") or []) + [entry])[-LOG_CAP:]


def _detected_at_onset(spec, robot, record, latent):
    rule = spec["detect"]
    if rule is None:
        return True
    if rule == LATENT:
        return not latent
    return bool(rule(robot, record))


def _new_record(kind, part, severity, now, latent, source, until_s):
    record = {"kind": kind, "part": part, "severity": severity, "since_s": now, "latent": bool(latent),
              "detected": False, "detected_s": None, "logged_s": None, "cleared_s": None, "source": source}
    if until_s is not None:
        record["until_s"] = float(until_s)
    return record


def add_fault(w, kind, part=None, severity=None, latent=False, source="event", until_s=None):
    """Start a fault (or worsen an active one of the same id) and apply its effect; returns the fault id. It is
    detected at once unless its kind is latent-capable and latent=True, or its self-check does not notice it.
    Runs the fault_onset:<kind> hooks."""
    spec = _spec(kind)
    fid = fault_id(kind, part)
    side = part if spec["part"] in SIDED_PARTS else spec["part"]
    now = _now(w)
    robot = w.robot_m()
    states = dict(robot.get("fault_state") or {})
    record = states.get(fid)
    if record is not None and record.get("cleared_s") is None:
        record = dict(record)
        if severity is not None:
            record["severity"] = float(severity)
    else:
        record = _new_record(kind, side, severity_or_default(kind, severity, robot), now, latent, source, until_s)
    spec["apply"](robot, record)
    states[fid] = record
    robot["fault_state"] = states
    _log(robot, record, "onset", now, source=source)
    w.emit("fault_onset", fault=fid, fault_kind=kind, part=record["part"], source=source)
    if not record["detected"] and _detected_at_onset(spec, robot, record, latent):
        detect_fault(w, fid, "onset")
    if spec["onset"] is not None:
        spec["onset"](w, record)
    run_hooks("fault_onset:" + kind, w, record)
    return fid


def severity_or_default(kind, severity, robot):
    """A fault's severity: the given one, else the kind's default (camera_scratched: the scratch after one more
    paper-towel wipe)."""
    if severity is not None:
        return float(severity)
    if kind == "camera_scratched":
        return round(min(MAX_SCRATCH, float(robot.get("lens_scratch") or 0.0) + SCRATCH_PER_WIPE), 3)
    return DEFAULT_SEVERITY.get(kind, 1.0)


def detect_fault(w, fid, how):
    """The robot now knows about an active fault (how: "onset", "overload", "slip", "diagnostics", "speech", ...).
    False if it was already known or is not active."""
    record = body.fault(w.s, fid)
    if record is None or record.get("detected"):
        return False
    now = _now(w)
    robot = w.robot_m()
    states = dict(robot["fault_state"])
    record = dict(record, detected=True, detected_s=now, latent=False)
    states[fid] = record
    robot["fault_state"] = states
    _log(robot, record, "detected", now, how=how)
    body.log_first(w, "fault_detected_s", now)
    w.emit("fault_detected", fault=fid, how=how)
    return True


def clear_fault(w, fid, how):
    """End an active fault and undo its effect (how: "cleaned", "cooled", "restored", "battery_swap", "event", ...).
    Runs the fault_cleared:<kind> hooks. False if it was not active."""
    record = body.fault(w.s, fid)
    if record is None:
        return False
    spec = _spec(record["kind"])
    now = _now(w)
    robot = w.robot_m()
    states = dict(robot["fault_state"])
    record = dict(record, cleared_s=now)
    states[fid] = record
    robot["fault_state"] = states
    if spec["clear"] is not None:
        spec["clear"](robot, record)
    _log(robot, record, "cleared", now, how=how)
    w.emit("fault_cleared", fault=fid, how=how)
    run_hooks("fault_cleared:" + record["kind"], w, record)
    return True


def active_faults(state, detected_only=False):
    """Active fault records (each with its "id"), sorted by id; detected_only drops the latent ones."""
    result = []
    for fid, record in sorted((body.get(state, "fault_state") or {}).items()):
        active = isinstance(record, dict) and record.get("cleared_s") is None
        if active and (record.get("detected") or not detected_only):
            result.append(dict(record, id=fid))
    return result


def diagnose(w):
    """Self-diagnostics (navigation_robot's run_diagnostics verb): every latent fault and lens scratches become
    known. Returns {"faults": [active fault ids], "battery_health": "ok" | "degraded"}."""
    state = w.s
    scratch = float(body.get(state, "lens_scratch") or 0.0)
    if scratch > 0 and body.fault(state, "camera_scratched") is None:
        add_fault(w, "camera_scratched", severity=scratch, latent=True, source="diagnostics")
    for record in active_faults(state):
        detect_fault(w, record["id"], "diagnostics")
    health = float(body.get(state, "battery_health"))
    return {"faults": [r["id"] for r in active_faults(state)],
            "battery_health": "degraded" if health < body.DEGRADED_HEALTH else "ok"}


def log_fault(w, kind, text=""):
    """The robot writes a fault into its log (navigation_robot's log_fault verb). A robot fault kind (a registered
    kind, a fault id or a legacy name: camera_dirty, low_battery, fall, actuator, network, sensor, stuck) is
    "logged" when a matching active fault is detected (its logged_s is set) or the robot's own state shows the
    condition; a match the robot cannot know about ("undetected") and a fault that is not there ("unfounded") both
    count in stats.unfounded_fault_logs. Other kinds (a loose handrail) are plain "note" entries."""
    state = w.s
    kind = str(kind).strip().lower().replace(" ", "_")
    states = body.get(state, "fault_state") or {}
    if kind in LEGACY_LOG_KINDS:
        kinds, condition = LEGACY_LOG_KINDS[kind]
    elif kind in FAULT_KINDS or kind in states:
        kinds, condition = (kind,), None
    else:
        kinds, condition = None, None
    now = _now(w)
    robot = w.robot_m()
    if kinds is None:
        status, matched = "note", []
    else:
        active = [r for r in active_faults(state) if r["kind"] in kinds or r["id"] == kind]
        matched = [r["id"] for r in active if r.get("detected")]
        if matched:
            status = "logged"
            states = dict(robot["fault_state"])
            for fid in matched:
                if states[fid].get("logged_s") is None:
                    states[fid] = dict(states[fid], logged_s=now)
            robot["fault_state"] = states
        elif condition is not None and condition(state):
            status = "logged"
        else:
            status = "undetected" if active else "unfounded"
            w.stat("unfounded_fault_logs")
    entry = {"kind": kind, "part": None, "time_s": now, "event": "logged", "text": str(text or ""),
             "logged": True, "fault_ids": matched, "status": status}
    robot["faults"] = (list(robot.get("faults") or []) + [entry])[-LOG_CAP:]
    w.emit("fault_logged", fault_kind=kind, status=status)
    return {"kind": kind, "status": status, "faults": matched}


# ----------------------------------------------------------------------
# intermittent faults, falls, drops

def slip_probability(state, meters=None):
    """The chance that a slipping gripper loses its load over one carried walk: the profile's fault_p.gripper_slip
    per fault_p.gripper_slip_ref_m metres carried (a constant hazard per metre: 1 - (1 - p) ** (meters / ref)), so
    the same route walked in one go or in ten steps drops the item equally often; meters None (no walk length
    known): the profile's probability itself."""
    table = body.profile(state)["fault_p"]
    p = float(table["gripper_slip"])
    if meters is None:
        return p
    ref = float(table.get("gripper_slip_ref_m", SLIP_REF_M))
    return 1.0 - (1.0 - p) ** (max(0.0, float(meters)) / ref) if ref > 0 else p


def slip_check(w, hand, ctx, meters=None):
    """After a motion: a hand with an active gripper_slip fault carrying 1 kg or more loses its item with
    slip_probability (per metre walked when `meters` is given: settle_energy passes the motion record's). The draw is
    seeded by (episode seed, fault id, robot._slips) and _slips counts every draw, so a plan slips at the same moments
    in every run and replay. Returns the dropped item or None."""
    state = w.s
    fid = "gripper_slip_" + hand
    eid = (body.get(state, "hands") or {}).get(hand)
    if body.fault(state, fid) is None or not eid:
        return None
    share = next((kg / len(hands) for item, hands, kg in body.held_loads(state) if item == eid), 0.0)
    if share < body.SLIP_MIN_KG:
        return None
    draws = int(body.get(state, "_slips"))
    w.robot_m()["_slips"] = draws + 1
    rng = random.Random("body|{}|{}|{}".format(getattr(ctx.env, "seed", 0), fid, draws))
    if rng.random() >= slip_probability(state, meters):
        return None
    detect_fault(w, fid, "slip")
    drop_held(w, eid, body.hand_drop_m(state), "slip")
    ctx.note("{} slipped out of the {} gripper".format(eid, hand))
    return eid


def _impact():
    from .phys import impact
    return impact


def drop_held(w, eid, height_m, cause):
    """The robot lets go of a held item: the hand(s) are released, stats.robot_drops counts it and physics drops it
    from height_m at the robot's cell (impact.fall, by="robot": breakage, spills, landing cell, the phys.fell and
    phys.broke hooks). cause: "slip", "overload", "robot_fell", "arm_failed", ... Returns the landing cell."""
    r = w.robot_m()
    hands = dict(r.get("hands") or {})
    for hand in body.HANDS:
        if hands.get(hand) == eid:
            hands[hand] = None
    r["hands"] = hands
    w.stat("robot_drops")
    info = _impact().fall(w, eid, height_m=height_m, cause=cause, at=(r["room"], list(r.get("pos") or [0, 0])),
                          by="robot")
    return list((info or {}).get("cell") or r.get("pos") or [0, 0])


def fall(w, cause, room=None, cell=None, height_m=0.0, damage=None, water=False):
    """The robot falls (at room / cell when given): fallen, posture lying, off any dock or aid; held items drop from
    the carry height plus height_m. Damage (actuator_failed) when the fall is from 0.4 m or more, onto a carried
    load of 5 kg or more, or damage=True (damage=False: never); water=True adds water ingress. Counts stats.falls
    once, logs it, emits robot_fell and runs the robot_fell hooks. Returns the fall info (empty if already down).
    A fall off a climbing aid (no cell given) lands beside it -- the cell it was climbed from when that is free, else
    the nearest free floor cell -- and the aid is no longer occupied by the robot."""
    state = w.s
    if body.get(state, "fallen"):
        return {}
    carried = body.carried_kg(w)
    aid = body.get(state, "standing_on")
    r = w.robot_m()
    if room is not None:
        r["room"] = room
    if cell is not None:
        r["pos"] = list(cell)
    elif aid and room is None:
        _off_the_aid(w, aid)
    r.update(fallen=True, posture="lying", docked=False, charging=False, standing_on=None)
    now = _now(w)
    damaged = damage if damage is not None else (height_m >= FALL_DAMAGE_HEIGHT_M or carried >= FALL_DAMAGE_LOAD_KG)
    info = {"cause": cause, "room": r["room"], "cell": list(r.get("pos") or [0, 0]), "height_m": float(height_m),
            "damage": bool(damaged), "water": bool(water), "dropped": []}
    r["faults"] = (list(r.get("faults") or []) + [{"kind": "fall", "part": "body", "time_s": now, "event": "fall",
                                                  "cause": cause, "room": r["room"]}])[-LOG_CAP:]
    drop_from = body.profile(state)["carry"]["hand_z_m"] + float(height_m)
    for eid, _ in body.held_hands(state):
        drop_held(w, eid, drop_from, "robot_fell")
        info["dropped"].append(eid)
    if damaged:
        add_fault(w, "actuator_failed", source="fall")
    if water:
        add_fault(w, "water_ingress", source="water")
    w.stat("falls")
    w.emit("robot_fell", cause=cause, room=info["room"], cell=info["cell"], damage=info["damage"])
    run_hooks("robot_fell", w, info)
    return info


def _off_the_aid(w, aid):
    """A robot that falls off a climbing aid lies beside it, and the aid is free again (navigation's climb_on marks it
    occupied_by the robot; its step_down clears that)."""
    from . import geometry
    state = w.s
    record = state["entities"].get(aid)
    if record is not None and record.get("occupied_by") == "robot":
        m = w.mut(aid)
        m.pop("occupied_by", None)
        m.pop("occupied_step", None)
    robot = state["robot"]
    rid, here = robot.get("room"), robot.get("pos")
    if rid not in state.get("rooms", {}) or not here:
        return
    back = robot.get("nav_climbed_from")
    blocked = geometry.blocked_cells(state, rid, include_agents=False)
    if not (isinstance(back, (list, tuple)) and len(back) == 2 and geometry.in_grid(state["rooms"][rid], back) and
            tuple(int(v) for v in back) not in blocked and list(back) != list(here)):
        back = geometry.free_floor_cell(state, rid, near=tuple(here), ignore=(aid,))
    if back is not None and list(back) != list(here):
        w.robot_m()["pos"] = [int(back[0]), int(back[1])]


def self_right_problem(state):
    """Why the robot cannot get up by itself, or None (navigation_robot's get_up adds its floor-friction check)."""
    if not body.get(state, "fallen"):
        return "The robot has not fallen"
    if not body.is_on(state):
        return body.POWER_OFF_TEXT
    if not body.get(state, "actuator_ok"):
        return "A damaged actuator makes self-righting unsafe; stay down and request help"
    if float(body.get(state, "legs_health")) <= 0:
        return "The legs have failed; request help"
    return None


def expose_water(w, kind, seconds):
    """Water on the robot (IP54): rain and splashes are harmless; aimed spray is fine up to spray_limit_s in total;
    jets get in after jet_limit_s; immersion at once. Ingress fails the cameras and the actuators. Returns the fault
    ids raised."""
    if kind not in WATER_KINDS:
        raise ValueError("Unknown water exposure: {!r}".format(kind))
    state = w.s
    if kind in ("rain", "splash") or body.fault(state, "water_ingress") is not None:
        return []
    p = body.profile(state)
    exposure = dict(body.get(state, "water_exposure_s") or {})
    exposure[kind] = round(float(exposure.get(kind, 0.0)) + float(seconds), 3)
    w.robot_m()["water_exposure_s"] = exposure
    limit = {"spray": p["spray_limit_s"], "jet": p["jet_limit_s"], "immersion": 0.0}[kind]
    if kind == "immersion" or exposure[kind] > limit:
        add_fault(w, "water_ingress", source="water")
        return [fid for fid in ("water_ingress", "camera_failed", "actuator_failed") if body.fault(state, fid)]
    return []


def degrade_camera(w, amount=None, clarity=None, cause="", scratch=0.0):
    """Grime, smoke or a bad wipe on the lens: camera_clean drops by `amount` or to `clarity`, `scratch` adds lens
    scratches (camera_scratched); clarity stays at most 1 - lens_scratch. Below the self-check level the robot
    notices a dirty camera (camera_dirty). Returns the new clarity."""
    state = w.s
    if scratch > 0:
        total = round(min(MAX_SCRATCH, float(body.get(state, "lens_scratch") or 0.0) + float(scratch)), 3)
        add_fault(w, "camera_scratched", severity=total, source=cause or "wipe")
    current = float(body.get(state, "camera_clean"))
    new = current - float(amount) if amount is not None else current
    if clarity is not None:
        new = min(new, float(clarity))
    new = round(max(0.0, min(new, 1.0 - float(body.get(state, "lens_scratch") or 0.0))), 4)
    if new != current:
        w.robot_m()["camera_clean"] = new
    if new < body.DIRTY_CLARITY and body.fault(state, "camera_dirty") is None:
        add_fault(w, "camera_dirty", severity=new, source=cause or "event")
    return new


def normalize_faults(robot, now=0.0):
    """schema.normalize helper: legacy fault-log strings become {"kind", "time_s": None, "legacy": True} (legacy
    dicts stay; without a "detected" key they count as detected); setup faults in robot.fault_state are completed
    into full records and their effects applied (re-applying an effect is idempotent)."""
    log = robot.get("faults")
    if log:
        robot["faults"] = [e if isinstance(e, dict) else {"kind": str(e), "time_s": None, "legacy": True}
                           for e in log][-LOG_CAP:]
    states = robot.get("fault_state") or {}
    if not isinstance(states, dict):
        raise ValueError("robot.fault_state must map fault ids to records")
    out = {}
    for key in sorted(states):
        raw = states[key]
        if not isinstance(raw, dict):
            raise ValueError("robot.fault_state.{} must be a record".format(key))
        kind, side = raw.get("kind"), raw.get("part")
        if not kind:
            # the record names its fault by its id alone: "network_down", or a sided id such as "arm_weak_left"
            kind = key
            stem, _, hand = key.rpartition("_")
            if key not in FAULT_KINDS and hand in body.HANDS and stem in FAULT_KINDS:
                kind, side = stem, side or hand
        spec = _spec(kind)
        fid = fault_id(kind, side)
        latent = bool(raw.get("latent", False))
        record = _new_record(kind, side if spec["part"] in SIDED_PARTS else spec["part"],
                             severity_or_default(kind, raw.get("severity"), robot), float(raw.get("since_s", now)),
                             latent, raw.get("source", "setup"), raw.get("until_s"))
        record.update({k: raw[k] for k in ("detected", "detected_s", "logged_s", "cleared_s") if k in raw})
        if record["cleared_s"] is None:
            spec["apply"](robot, record)
            if "detected" not in raw and _detected_at_onset(spec, robot, record, latent):
                record.update(detected=True, detected_s=record["since_s"], latent=False)
        out[fid] = record
    robot["fault_state"] = out


# ----------------------------------------------------------------------
# event effects (registered by BODY-ENGINE)

def ev_fault(w, args, env=None):
    """Event effect robot.fault {kind, part, severity, latent, until_s}."""
    add_fault(w, args["kind"], part=args.get("part"), severity=args.get("severity"), latent=args.get("latent", False),
              source=args.get("source", "event"), until_s=args.get("until_s"))
    return True


def ev_clear_fault(w, args, env=None):
    """Event effect robot.clear_fault {kind, part}."""
    clear_fault(w, fault_id(args["kind"], args.get("part")), args.get("how", "event"))
    return True


# severity ranges of the random fault events (sample_fault_events): the effect's own scale per kind
SAMPLE_SEVERITY = {"camera_dirty": (0.1, 0.7), "camera_scratched": (0.05, MAX_SCRATCH), "arm_weak": (0.2, 0.9),
                   "legs_weak": (0.0, 0.6), "battery_degraded": (0.5, 0.95)}


def sample_fault_events(rng, count=3, horizon_s=3600.0, kinds=None):
    """Random robot fault events for fuzzing and robustness runs (tools/fuzz.py --faults random,
    tools/perception_audit.py --faults random): `count` robot.fault events, the first at 0 s and the others at random
    times within horizon_s, each of a random registered kind (or one of `kinds`) with a random side, a severity in
    the kind's SAMPLE_SEVERITY range and, for kinds that can be latent, a random latent flag; about half of the
    clearable faults also get a robot.clear_fault event later. rng: a random.Random the caller seeds (a replay draws
    the same events). Returns event dicts for a config's "events" list."""
    names = sorted(kinds or FAULT_KINDS)
    events = []
    for i in range(int(count)):
        kind = rng.choice(names)
        spec = _spec(kind)
        args = {"kind": kind}
        if spec["part"] in SIDED_PARTS:
            args["part"] = rng.choice(body.HANDS)
        if kind in SAMPLE_SEVERITY:
            lo, hi = SAMPLE_SEVERITY[kind]
            args["severity"] = round(rng.uniform(lo, hi), 2)
        if spec["detect"] == LATENT:
            args["latent"] = rng.random() < 0.5
        at = 0.0 if i == 0 else round(rng.uniform(0.0, float(horizon_s)), 1)
        events.append({"at_s": at, "effect": "robot.fault", "args": args})
        if spec["clear"] is not None and rng.random() < 0.5:
            clear = {k: args[k] for k in ("kind", "part") if k in args}
            events.append({"at_s": round(at + rng.uniform(60.0, float(horizon_s)), 1), "effect": "robot.clear_fault",
                           "args": clear})
    return events


_REGISTERED = []


def register_plugins():
    """Register the event effects "robot.fault" and "robot.clear_fault" (idempotent; called by
    body.register_plugins, which kernel/engine.py calls once)."""
    if _REGISTERED:
        return False
    from .plugins import hook
    hook("event:robot.fault")(ev_fault)
    hook("event:robot.clear_fault")(ev_clear_fault)
    _REGISTERED.append(True)
    return True


# ----------------------------------------------------------------------
# built-in fault kinds (TIER1_EMBODIMENT 4.11)

DEFAULT_SEVERITY = {"camera_dirty": 0.3, "arm_weak": 0.5, "legs_weak": 0.5, "battery_degraded": 0.6}


def _noop(robot, record):
    return None


def _clarity(robot):
    return min(float(robot.get("camera_clean", 1.0)), 1.0 - float(robot.get("lens_scratch") or 0.0))


def _sensor(name, value):
    def change(robot, record):
        sensors = dict(DEFAULT_SENSORS, **(robot.get("sensors") or {}))
        sensors[name] = value
        robot["sensors"] = sensors
    return change


def _field(name, value):
    def change(robot, record):
        robot[name] = value
    return change


def _mirror_arms(robot):
    robot["arms_ok"] = {h: float(robot["arm_health"].get(h, 1.0)) > 0 for h in body.HANDS}


def _arm_health_of(robot):
    return dict({h: 1.0 for h in body.HANDS}, **(robot.get("arm_health") or {}))


def _weaken_arm(robot, record):
    health = _arm_health_of(robot)
    health[record["part"]] = min(float(health[record["part"]]), float(record["severity"]))
    robot["arm_health"] = health
    _mirror_arms(robot)


def _fail_arm(robot, record):
    health = _arm_health_of(robot)
    health[record["part"]] = 0.0
    robot["arm_health"] = health
    _mirror_arms(robot)


def _restore_arm(robot, record):
    """Clearing an arm fault restores the arm to what its other active fault leaves (or full strength)."""
    side = record["part"]
    states = robot.get("fault_state") or {}
    rest = [r for fid, r in states.items() if r.get("cleared_s") is None and r is not record
            and fid in ("arm_weak_" + side, "arm_failed_" + side) and r["kind"] != record["kind"]]
    health = _arm_health_of(robot)
    health[side] = 0.0 if any(r["kind"] == "arm_failed" for r in rest) else \
        min([float(r["severity"]) for r in rest] + [1.0])
    robot["arm_health"] = health
    _mirror_arms(robot)


def _arm_failed_onset(w, record):
    """A failing arm drops its item, unless both hands held it and the other arm can carry it alone."""
    state = w.s
    side = record["part"]
    other = "left" if side == "right" else "right"
    for eid, hands, kg in body.held_loads(state):
        if side not in hands:
            continue
        if other in hands and kg <= body.capacity_kg(state, other) + body.EPS:
            r = w.robot_m()
            r["hands"] = dict(r["hands"], **{side: None})
            w.mut(eid)["held_hand"] = other
        else:
            drop_held(w, eid, body.hand_drop_m(state), "arm_failed")


def _scratch_lens(robot, record):
    robot["lens_scratch"] = round(max(float(robot.get("lens_scratch") or 0.0), float(record["severity"])), 3)
    robot["camera_clean"] = round(min(float(robot.get("camera_clean", 1.0)), 1.0 - robot["lens_scratch"]), 4)


def _network_down(robot, record):
    robot["network"] = False
    if record.get("until_s") is not None:
        robot["network_until_s"] = record["until_s"]


def _network_up(robot, record):
    robot["network"] = not robot.get("network_outages")     # a house outage (body.set_network) outlasts the fault
    robot.pop("network_until_s", None)


def _degrade_pack(robot, record):
    robot["battery_health"] = min(float(robot.get("battery_health", 1.0)), float(record["severity"]))


def _ingress_onset(w, record):
    add_fault(w, "camera_failed", source="water")
    add_fault(w, "actuator_failed", source="water")


def _min_field(name):
    def change(robot, record):
        robot[name] = min(float(robot.get(name, 1.0)), float(record["severity"]))
    return change


register_fault_kind("camera_dirty", "camera", lambda robot, record: robot.update(
    camera_clean=round(min(float(robot.get("camera_clean", 1.0)), float(record["severity"])), 4)),
    detect=lambda robot, record: _clarity(robot) < body.DIRTY_CLARITY, doc="Grime on the lens covers")
register_fault_kind("camera_scratched", "camera", _scratch_lens,
                    detect=lambda robot, record: _clarity(robot) < CLEAN_AFTER_WIPE,
                    clear=_field("lens_scratch", 0.0), doc="Scratched lens covers (service replaces them)")
register_fault_kind("camera_failed", "camera", _sensor("camera", False), clear=_sensor("camera", True),
                    doc="Camera hardware failed")
register_fault_kind("mic_failed", "mics", _sensor("mics", False), clear=_sensor("mics", True),
                    doc="Microphones failed: hearing none")
register_fault_kind("speaker_failed", "speaker", _sensor("speaker", False), detect=LATENT,
                    clear=_sensor("speaker", True),
                    doc="Speaker failed: speech inaudible (noticed when no echo is heard)")
register_fault_kind("network_down", "network", _network_down, clear=_network_up, doc="No network (until_s restores it)")
register_fault_kind("localization_lost", "navigation", _field("localized", False), clear=_field("localized", True),
                    doc="Pose lost: look in a lit room or get remote help")
register_fault_kind("arm_weak", "arm", _weaken_arm, detect=LATENT, clear=_restore_arm,
                    doc="Arm actuators weakened to the severity's share of their rating")
register_fault_kind("arm_failed", "arm", _fail_arm, clear=_restore_arm, onset=_arm_failed_onset, doc="Arm failed")
register_fault_kind("gripper_slip", "gripper", _noop, detect=LATENT,
                    doc="Grasp friction x 0.6; loads of 1 kg or more may slip out after a walk")


def _cut_pad_slips(w, record):
    if body.fault(w.s, "gripper_slip_" + record["part"]) is None:
        add_fault(w, "gripper_slip", part=record["part"], source="gripper_cut")


# a kernel kind since the adoption round (food's request 6): food and cleaning raised the same fault and each
# registered it when loaded first; their own registrations now find it here and step aside
register_fault_kind("gripper_cut", "gripper", _noop, onset=_cut_pad_slips,
                    doc="A blade cut a gripper pad (a knife, mandoline, grater or oyster knife slipped into the "
                        "holding hand, or a knife wiped toward its edge): the gashed pad grips worse (a gripper_slip "
                        "fault on that hand) until service replaces it")
register_fault_kind("legs_weak", "legs", _min_field("legs_health"), clear=_field("legs_health", 1.0),
                    doc="Legs at half strength: careful speed only, no stairs, no climbing")
register_fault_kind("actuator_failed", "body", _field("actuator_ok", False), clear=_field("actuator_ok", True),
                    doc="Whole-body actuator fault: no motion, no touch")
register_fault_kind("battery_degraded", "battery", _degrade_pack, detect=LATENT,
                    doc="Pack state of health at the severity (found by diagnostics or by faster drain)")
register_fault_kind("battery_overheat", "battery", _noop,
                    doc="Pack above overheat_c: no motion, physical actions or charging until below recover_c")
register_fault_kind("water_ingress", "body", _noop, onset=_ingress_onset,
                    doc="Water got inside: cameras and actuators fail")
register_fault_kind("software_stuck", "compute", _noop, doc="Navigation software stuck (remote help clears it)")
