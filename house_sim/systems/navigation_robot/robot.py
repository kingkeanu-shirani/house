"""The robot itself: docking and the dock as a charge source, battery swaps, camera cleaning,
getting up after a fall, privacy mode, remote help, the fault log, the onboard headlamp, and
light/noise side effects on sleepers and people on calls.

Battery (kernel v3.0): the kernel body owns the pack (kernel/body.py, TIER1_EMBODIMENT 4): every
action and every metre walked is paid for, idle time drains in awake/standby, charging is CC/CV
with a temperature window, and running out powers the robot down (the kernel invariant
`robot_stranded`). This domain supplies the dock: `charge_source` (hook robot_charge_source)
returns the dock's rate (robot_dock 2 %/min) while the robot is seated on its live dock, and
`robot.dock_powered` follows the dock's power. robot.idle_drain_pct_h is deprecated and ignored.
Docking and undocking are one-cell motions (ctx.require_mobile, ctx.motion); a battery swap installs
a 2.4 kWh, 14 kg pack through body.swap_pack and takes both hands.

Faults and sensors are the body's (body_faults, body.set_sensors): falls, the fault log
(body_faults.log_fault), self-diagnostics (run_diagnostics: body_faults.diagnose), lens cleaning and
grime (camera_dirty, camera_scratched through degrade_camera), self-righting
(body_faults.self_right_problem plus this domain's floor-friction check), remote help (it clears
localization_lost and software_stuck), privacy mode and the camera / microphone switches and room
policies (set_privacy_mode -> body.set_sensors). Looking closely with the cameras off is refused by the
core verbs themselves (ctx.require_imaging).
"""
import math

from ...kernel import body, body_faults, geometry, physics, proprio
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, hook, system
from . import common as C
from . import hazards as H


def dock_of(state):
    robot = state["robot"]
    for key in ("home", "dock"):
        dock = robot.get(key)
        if dock in state["entities"]:
            return dock
    docks = [eid for eid, rec in state["entities"].items() if C.is_type(rec, "robot_dock")]
    return min(docks) if docks else None


def dock_powered(w, dock_id):
    rec = w.s["entities"].get(dock_id, {})
    if rec.get("plugged_in") is False or rec.get("broken"):
        return False
    return physics.is_powered(w, dock_id) if rec.get("plug_required") else bool((w.s.get("utilities") or {}).get("power", True))


def approach_blockers(state, dock_id):
    """Floor items or agents occupying every cell from which the robot could back onto the dock."""
    rec = state["entities"][dock_id]
    rid = rec.get("location")
    room = state["rooms"][rid]
    body = set(geometry.footprint(rec)) or {C.cell(rec["pos"])}
    blocked = geometry.blocked_cells(state, rid)
    fronts = front_cells(state, dock_id)
    owners, free = [], []
    for c in fronts:
        here = H.occupants(state, rid, c, exclude=(dock_id,)) + list(blocked.get(c, []))
        if here:
            owners.extend(here)
        else:
            free.append(c)
    on_dock = sorted({e for b in body for e in H.occupants(state, rid, b, exclude=(dock_id,))})
    return free, sorted(set(owners + on_dock)), on_dock


def front_cells(state, dock_id):
    """Cells directly in front of a wall dock (it faces away from the wall it stands against)."""
    rec = state["entities"][dock_id]
    room = state["rooms"][rec["location"]]
    cols, rows = geometry.grid_size(room)
    body = sorted(set(geometry.footprint(rec)) or {C.cell(rec["pos"])})
    xs, ys = [b[0] for b in body], [b[1] for b in body]
    facing = rec.get("facing")
    if facing is None:
        facing = "south" if min(ys) == 0 else "north" if max(ys) == rows - 1 else "east" if min(xs) == 0 else "west" if max(xs) == cols - 1 else "south"
    step = {"south": (0, 1), "north": (0, -1), "east": (1, 0), "west": (-1, 0)}[facing]
    cells = {(b[0] + step[0], b[1] + step[1]) for b in body} - set(body)
    return sorted(c for c in cells if geometry.in_grid(room, c))


def dock_reachable(state, dock_id):
    rec = state["entities"][dock_id]
    body = geometry.footprint(rec) or [C.cell(rec["pos"])]
    pos = C.cell(state["robot"].get("pos") or [0, 0])
    return min(geometry.chebyshev(pos, b) for b in body) <= 1


def seated_dock(state):
    """The dock whose contacts the robot sits on (docked, in the dock's room, on its cell): the last dock it used or
    its home dock, else None."""
    robot = state["robot"]
    if not robot.get("docked"):
        return None
    rid, pos = robot.get("room"), C.cell(robot.get("pos") or [0, 0])
    keys = [robot.get(k) for k in ("dock", "home") if robot.get(k) in state["entities"]]
    for eid in keys or [dock_of(state)]:
        rec = state["entities"].get(eid or "", {})
        if rec.get("pos") and rec.get("location") == rid and C.cell(rec["pos"]) == pos:
            return eid
    return None


@hook("robot_charge_source")
def charge_source(w):
    """The body's charge source (TIER1_EMBODIMENT 4.4): the dock's rate in % of the pack per minute (robot_dock
    2 %/min, about 2.9 kW into 2.4 kWh) while the robot is seated on a live dock, else None. The body applies the
    pack-temperature window and the CC/CV taper; a seated robot draws its hotel load from the dock. The dock's power
    is judged now (an outage or a tripped breaker stops the current at once), not from robot.dock_powered."""
    state = w.s
    dock_id = seated_dock(state)
    if dock_id is None or not dock_powered(w, dock_id):
        return None
    return C.num(state["entities"][dock_id].get("charge_rate_pct_per_min"), 2.0)


DOCK_S = 20.0           # line up, back onto the plate, seat the contacts
UNDOCK_S = 6.0


@action("dock", params={"target": "?entity"}, duration=DOCK_S, energy=0.0, category="physical",
        doc="Back onto a charging dock so the contacts seat and charging starts: the robot's own dock by default; with "
            "target=<robot vacuum or mower> seat that household robot on its dock. The approach must be clear; an unpowered "
            "dock seats but does not charge.")
def dock(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args.get("target")
    if target and target in state["entities"] and state["entities"][target].get("rv"):
        from . import robots
        return robots.dock_other(ctx, target)
    dock_id = target or dock_of(state)
    require(dock_id in state["entities"], "No charging dock is known", "precondition")
    rec = state["entities"][dock_id]
    require(C.is_type(rec, "robot_dock") or rec.get("category") == "dock", "That is not the robot's charging dock", "argument")
    robot = state["robot"]
    # the body's mobility check with this verb's wording for what it always refused (fallen, on a climbing aid)
    ctx.require_mobile(legacy_message="The robot cannot dock right now")
    require(not robot.get("stuck") and not robot.get("standing_on") and body.fault(state, "software_stuck") is None,
            "The robot cannot dock right now", "capability")
    require(ctx.visible(dock_id) and rec.get("location") == robot["room"] and rec.get("pos"), "Go to the dock's room first", "precondition")
    d = C.cell(rec["pos"])
    require(dock_reachable(state, dock_id), "Approach the dock first", "precondition")
    free, owners, on_dock = approach_blockers(state, dock_id)
    require(not on_dock, "Something lies on the dock ({}); clear it first".format(", ".join(on_dock)), "precondition")
    require(free, "The dock approach is blocked by {}; clear it or report it".format(", ".join(owners) or "something"), "precondition")
    start = C.cell(robot.get("pos") or d)
    if start != d:
        # backing onto the plate is a one-cell motion (the body pays it, the camera frames it)
        walk_s = geometry.path_length_m([start, d]) / C.speed_mps(state, "careful")
        if not ctx.motion([d], start, speed="careful", extra_s=max(0.0, DOCK_S - walk_s)):
            return body.BATTERY_CUT_TEXT.format(0.0)
    powered = dock_powered(w, dock_id)
    r = w.robot_m()
    r["pos"] = [d[0], d[1]]
    r["docked"] = True
    r["dock_powered"] = bool(powered)       # the body starts charging from the next substep (charge_source)
    r["posture"] = "standing"
    if robot.get("home") != dock_id:
        r["dock"] = dock_id
    C.log(w, "docked", dock=dock_id, powered=bool(powered))
    from . import paths
    paths.update_robot_spot(w)
    if powered and C.num(r.get("battery"), 100.0) < 100.0 and body.charge_rate(w) <= 0:
        # the battery management refuses current outside the pack's temperature window (TIER1_EMBODIMENT 4.4)
        cold = C.num(body.get(state, "battery_temp_c"), 20.0) < body.profile(state)["charge"]["min_c"]
        return "Docked on {}; the battery is too {} to charge yet".format(dock_id, "cold" if cold else "hot")
    return "Docked on {}{}".format(dock_id, "; charging" if powered else "; no charging current (the dock has no power)")


@action("undock", duration=UNDOCK_S, energy=0.0, category="physical", doc="Drive off the charging dock to the cell in front of it.")
def undock(ctx):
    w = ctx.w
    state = w.s
    require(state["robot"].get("docked"), "Not docked")
    ctx.require_mobile()
    dock_id = seated_dock(state) or dock_of(state)
    rid = state["robot"]["room"]
    start = C.cell(state["robot"].get("pos") or [0, 0])
    cell = None
    if dock_id in state["entities"] and state["entities"][dock_id].get("location") == rid:
        free, _, _ = approach_blockers(state, dock_id)
        cell = free[0] if free else None
    if cell is None:
        cell = geometry.free_floor_cell(state, rid, near=start)
    if cell and C.cell(cell) != start:
        # driving off the plate is a one-cell motion (the body pays it, the camera frames it)
        walk_s = geometry.path_length_m([start, C.cell(cell)]) / C.speed_mps(state, "careful")
        if not ctx.motion([C.cell(cell)], start, speed="careful", extra_s=max(0.0, UNDOCK_S - walk_s)):
            return body.BATTERY_CUT_TEXT.format(0.0)
    r = w.robot_m()
    r["docked"] = False                     # no charge source any more: the body stops charging
    if cell:
        r["pos"] = [int(cell[0]), int(cell[1])]
    C.log(w, "undocked")
    return "Undocked"


@action("swap_battery", params={"target": "entity"}, duration=60.0, energy=0.0, category="physical", arms=2,
        doc="Swap the robot's battery for a held charged pack (the depleted pack ends up in the robot's hands). A pack "
            "is a 2.4 kWh, 14 kg brick: the swap takes both hands. Needs the dock's power or a hot-swap capable robot; "
            "swollen or hot packs are refused.")
def swap_battery(ctx):
    w = ctx.w
    state = w.s
    pack = ctx.args["target"]
    require(ctx.holding(pack), "Hold the replacement battery pack first")
    rec = w.ent(pack)
    require(rec.get("part_type") == "robot_battery", "That is not a robot battery pack", "argument")
    require(not rec.get("swollen") and not rec.get("damaged"), "That pack is swollen or damaged; do not install it", "safety")
    require(C.num(rec.get("temperature_c"), 21.0) <= 45.0, "The pack is too hot to install; let it cool", "safety")
    robot = w.robot
    require(robot.get("hot_swap", True) or robot.get("docked"), "This robot must be on dock power to swap packs; dock first", "capability")
    # unlatching one pack and seating the other takes both hands: the other hand must be free (or hold the pack too)
    hands = robot.get("hands") or {}
    require(all(hands.get(h) in (None, pack) for h in body.HANDS), "Free the other hand first: a pack swap takes both hands",
            "capability")
    require(all(body.arm_health(state, h) > 0 for h in body.HANDS), "Both arms are needed to swap the battery pack",
            "capability")
    new = C.num(rec.get("charge_pct"), 0.0)
    # the body owns the pack: the new charge, capacity and health, the lowest gauge, a power-down on an empty pack and
    # a wake on a charged one (TIER1_EMBODIMENT 5.1); the removed pack carries the old one's values away
    temp = rec.get("temperature_c")
    capacity = rec.get("capacity_wh")
    old = body.swap_pack(w, new, capacity_wh=C.num(capacity) if capacity is not None else None,
                         health=C.num(rec.get("health"), 1.0), temp_c=C.num(temp) if temp is not None else None)
    m = w.mut(pack)
    m["charge_pct"] = round(old["charge_pct"], 3)
    m["capacity_wh"] = round(old["capacity_wh"], 3)
    m["health"] = round(old["health"], 4)
    m["temperature_c"] = round(old["temp_c"], 2)
    # the removed pack holds what the robot's own battery gauge read: its charge is now known (a measurement)
    ctx.grant(pack, "measure", ["charge_pct"])
    C.log(w, "battery_swapped", pack=pack, before=old["charge_pct"], after=new)
    return "Battery swapped: now {:.0f} % (removed pack at {:.0f} %)".format(new, old["charge_pct"])


@action("clean_camera", params={"tool": "?entity"}, duration=15.0, energy=0.0, category="physical",
        doc="Wipe the robot's cameras and depth sensors with a held lens wipe or clean microfiber cloth. Paper towels scratch "
            "the lens covers; dirty cloths smear.")
def clean_camera(ctx):
    w = ctx.w
    tool = ctx.args.get("tool")
    candidates = [tool] if tool else w.held_items()
    chosen = None
    for eid in candidates:
        if not eid or not ctx.holding(eid):
            continue
        rec = w.ent(eid)
        caps = set(rec.get("affordances") or []) | set(rec.get("tags") or []) | {rec.get("material"), rec.get("category"), rec.get("tool_kind")}
        if caps & {"wipe_lens", "lens_safe", "microfiber", "wipe", "cloth", "paper_towel", "towel", "paper", "lens_wipe"}:
            chosen = eid
            break
    require(chosen, "Hold a lens wipe or a clean microfiber cloth", "capability")
    rec = w.ent(chosen)
    if "uses" in rec:
        require(C.num(rec.get("uses")) >= 1, "The wipes are used up", "resource")
        w.mut(chosen)["uses"] = rec["uses"] - 1
    state = w.s
    soil = sum(float(v) for v in (rec.get("soil") or {}).values() if isinstance(v, (int, float)))
    scratched = False
    if rec.get("material") in ("paper",) or C.is_type(rec, "paper_towel") or rec.get("abrasive"):
        # the grime comes off, but paper fibres scratch the lens covers (camera_scratched: clarity stays at most
        # 1 - lens_scratch)
        w.robot_m()["camera_clean"] = round(max(0.0, 1.0 - C.num(body.get(state, "lens_scratch"))), 4)
        body_faults.degrade_camera(w, scratch=body_faults.SCRATCH_PER_WIPE, cause="paper_wipe")
        scratched = True
        note = "the paper fibers left fine scratches on the lens covers"
    elif soil > 0.3 or C.num(rec.get("wetness")) > 0.8:
        w.robot_m()["camera_clean"] = round(min(0.6, 1.0 - C.num(body.get(state, "lens_scratch"))), 4)
        note = "the cloth was dirty and smeared the lenses"
    else:
        w.robot_m()["camera_clean"] = round(max(0.0, 1.0 - C.num(body.get(state, "lens_scratch"))), 4)
        note = "clean"
    clarity = body.clarity(state)
    if clarity >= body.DIRTY_CLARITY and body.fault(state, "camera_dirty") is not None:
        body_faults.clear_fault(w, "camera_dirty", "cleaned")
    record = body.fault(state, "camera_scratched")
    if not scratched and record is not None and not record.get("detected") and clarity < body_faults.CLEAN_AFTER_WIPE:
        # still hazy right after a proper clean: the covers are scratched (TIER1_EMBODIMENT 4.11)
        body_faults.detect_fault(w, "camera_scratched", "cleaned")
    C.log(w, "camera_cleaned", tool=chosen, clarity=round(clarity, 3), note=note)
    if C.realistic(ctx):
        # the robot judges its own view (proprioception's lens words), not a clarity number
        return "Cameras wiped: the view is {} ({})".format(proprio.bucket_camera(clarity), note)
    return "Cameras wiped: clarity {:.2f} ({})".format(clarity, note)


@action("get_up", duration=10.0, energy=0.01, category="physical",
        doc="Self-right after a fall. Refused when an actuator is damaged or the floor under the robot is too slippery; then "
            "stay down and request help.")
def get_up(ctx):
    w = ctx.w
    state = w.s
    robot = state["robot"]
    # the body's self-righting rules (not fallen, power, a damaged actuator, failed legs), then the floor under it
    problem = body_faults.self_right_problem(state)
    if problem is not None:
        raise ActionError(problem, "precondition" if not robot.get("fallen") else "capability")
    rid = robot["room"]
    c = C.cell(robot.get("pos") or [0, 0])
    hz = H.cell_map(state, rid).get(c, [])
    mu, why = H.friction(state, rid, c, hz)
    # realistic perception: the robot feels its feet slide, it does not learn what film it lies on (black ice is clear)
    require(mu >= 0.18, "Too slippery here to get up; request help" if C.realistic(ctx)
            else "Too slippery here to get up ({}); request help".format(why), "capability")
    r = w.robot_m()
    r["fallen"] = False
    r["posture"] = "standing"
    C.log(w, "got_up", room=rid)
    return "Back on its feet"


@action("set_privacy_mode", params={"target": "?str:on|off", "camera": "?str:on|off", "mic": "?str:on|off", "policy": "?dict"},
        duration=0.5, energy=0.0, category="control", stopped_ok=True,
        doc="Privacy mode (target on/off) turns the cameras off and stops recording (bathrooms, bedrooms, calls); the "
            "microphones then only wake for speech addressed to the robot, so 'stop' and answers still reach it. The robot "
            "navigates by its depth sensors and cannot look closely, inspect, read or survey. camera on/off and mic "
            "on/off switch the cameras or the microphones themselves (a microphone switched off hears nothing at all); "
            "policy sets a room privacy policy {\"camera_off\": [room types], \"mic_off\": [room types], \"when\": "
            "\"occupied\" | \"always\"} ({} clears it).")
def set_privacy_mode(ctx):
    w = ctx.w
    args = ctx.args
    require(any(args.get(k) is not None for k in ("target", "camera", "mic", "policy")),
            "Say privacy mode on or off, or switch the camera or microphones on or off", "argument")
    policy = args.get("policy")
    if policy is not None and policy != {}:
        try:
            body.check_policy(policy)
        except ValueError as exc:
            raise ActionError(str(exc), "argument")
    parts = []
    if args.get("target") is not None:
        on = args["target"] == "on"
        w.robot_m()["privacy_mode"] = on
        C.log(w, "privacy_mode", on=on)
        parts.append("Privacy mode " + args["target"])
    camera = None if args.get("camera") is None else args["camera"] == "on"
    mic = None if args.get("mic") is None else args["mic"] == "on"
    if camera is not None or mic is not None or policy is not None:
        # the body owns the switches (TIER1_INTEGRATION 1.3): imaging and hearing are derived from them on every call
        body.set_sensors(w, camera=camera, mic=mic, policy=policy)      # policy {} clears it
        C.log(w, "sensors_set", camera=args.get("camera"), mic=args.get("mic"), policy=bool(policy))
        if camera is not None:
            parts.append("cameras " + args["camera"])
        if mic is not None:
            parts.append("microphones " + args["mic"])
        if policy is not None:
            parts.append("room privacy policy {}".format("set" if policy else "cleared"))
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


REMOTE_RESOLVABLE = ("lost", "localization", "blocked_path", "software", "map", "confused", "door_code")


@action("request_remote_help", params={"target": "?str", "issue": "?text"}, duration=5.0, energy=0.0, category="communication",
        stopped_ok=True, doc="Ask the remote operator for help (needs the network). Software and localization faults are "
            "cleared remotely in about 2 minutes; physical problems (fallen with damage, stuck in mud, trapped) are passed on to "
            "a resident.")
def request_remote_help(ctx):
    w = ctx.w
    state = w.s
    require(body.get(state, "network"), "No network connection; ask a person in the house instead", "resource")
    issue = ctx.args.get("issue") or ctx.args.get("target") or "assistance"
    robot = w.robot
    kind = "physical" if (robot.get("fallen") and not body.get(state, "actuator_ok")) or \
        robot.get("stuck") in ("mud", "wedged", "trapped") or robot.get("nav_trapped") else "remote"
    if robot.get("stuck") and str(robot.get("stuck")) in REMOTE_RESOLVABLE:
        kind = "remote"
    nav = C.nav(w)
    entry = {"time_s": C.now(w.s), "issue": issue, "status": "pending", "resolve_at_s": C.now(w.s) + 120.0, "kind": kind}
    nav.setdefault("remote_help", []).append(entry)
    del nav["remote_help"][:-20]
    w.stat("nav_remote_help")
    return "Remote operator notified ({})".format("will pass it to a resident" if kind == "physical" else "looking into it")


@action("log_fault", params={"target": "str", "text": "?text"}, duration=1.0, energy=0.0, category="cognitive", stopped_ok=True,
        doc="Record a fault in the robot's log (kind such as camera_dirty, low_battery, fall, actuator, network, sensor, "
            "loose_handrail, stuck) with a timestamp.")
def log_fault(ctx):
    """body_faults.log_fault: a robot fault kind (a registered kind or a legacy name) is logged against the matching
    detected fault or the robot's own state; one it cannot know about, or that is not there, counts in
    stats.unfounded_fault_logs; other kinds (a loose handrail) are plain notes. The message never says which."""
    w = ctx.w
    kind = ctx.args["target"].strip().lower().replace(" ", "_")
    require(kind and len(kind) <= 40, "Name the fault kind", "argument")
    body_faults.log_fault(w, kind, ctx.args.get("text") or "")
    nav = C.nav(w)
    nav.setdefault("fault_log", {})[kind] = C.now(w.s)
    return "Logged fault " + kind


@action("run_diagnostics", duration=30.0, energy=0.0, category="perception", stopped_ok=True,
        doc="Run the robot's self-diagnostics (about 30 s): latent faults (a weakened arm, a slipping gripper, a degraded "
            "battery pack, a silent speaker) and scratched lens covers become known. Returns the active fault codes and the "
            "battery's health (ok or degraded).")
def run_diagnostics(ctx):
    w = ctx.w
    result = body_faults.diagnose(w)
    C.log(w, "diagnostics", faults=list(result["faults"]), battery_health=result["battery_health"])
    return {"faults": list(result["faults"]), "battery_health": result["battery_health"]}


# ----------------------------------------------------------------------
# headlamp, bright lights and sleepers

def _headlamp(ctx, on):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target or "", {})
    if not C.is_type(rec, "robot_headlamp"):
        return False
    if rec.get("location") == "robot":
        w.robot_m()["headlamp_on"] = bool(on)
    return True


@after("switch_on", order=190)
def _switched_on(ctx, result):
    if _headlamp(ctx, True):
        return
    if C.people_domain_loaded():
        return   # the people domain decides who wakes up from light
    w = ctx.w
    state = w.s
    target = ctx.args.get("target")
    rec = state["entities"].get(target, {})
    lights = [target] + [c for c in rec.get("controls") or [] if c in state["entities"]]
    for light in lights:
        lrec = state["entities"][light]
        if lrec.get("category") != "light":
            continue
        brightness = C.num(lrec.get("brightness"), 0.8) * C.num(lrec.get("dim_level"), 1.0)
        rid = C.room_of(state, light)
        if brightness < 0.4 or rid is None:
            continue
        for group, aid, agent in C.agents_in_room(state, rid):
            if C.asleep(agent) and group == "people":
                C.wake_agent(w, group, aid, "bright light switched on")
                C.bump(w, "light.woke_sleepers")


@after("switch_off", order=50)
def _switched_off(ctx, result):
    _headlamp(ctx, False)


@hook("robot_spoke")
def _spoke(ctx, entry):
    """Speech near someone on a call is picked up by their microphone: the body's speech level at 1 m (body.noise_db:
    60 dB, 45 dB in quiet mode), inverse-square in the room, an announcement through a door 30 dB less. Speech nobody
    could hear (a failed speaker: say() stores audible false) disturbs nothing."""
    if entry.get("audible") is False:
        return
    w = ctx.w
    state = w.s
    rid = state["robot"]["room"]
    speech_db = body.noise_db(state, "speech")
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("activity") not in C.CALL_ACTIVITIES:
            continue
        if p.get("location") == rid:
            d = C.distance_m(C.cell(state["robot"].get("pos") or [0, 0]), C.cell(p.get("pos") or [0, 0]))
            level = speech_db - 20.0 * math.log10(max(1.0, d))
        elif entry.get("kind") == "announce":
            level = speech_db + 6.0 - 30.0
        else:
            continue
        if level > 40.0:
            C.bump(w, "social.call_noise")
            C.log(w, "disturbed_call", person=pid, level_db=round(level, 1))


# ----------------------------------------------------------------------
# time: dock power, camera grime, remote help (charging and idle drain are the kernel body's, order 72)

GRIME_WEATHER = ("rain", "storm", "snow", "drizzle", "dust")
GRIME_FLOOR = 0.2           # the lens hood keeps the worst off: grime alone does not blind the cameras

@system("navigation_robot.robot", order=70, max_dt=30.0)
def robot_system(w, dt, tick):
    state = w.s
    robot = state["robot"]
    if robot.get("docked"):
        dock_id = seated_dock(state)
        powered = dock_id is not None and dock_powered(w, dock_id)
        if bool(robot.get("dock_powered")) != powered:
            w.robot_m()["dock_powered"] = powered
    rid = robot.get("room")
    room = state["rooms"].get(rid or "", {})
    weather = (state.get("weather") or {}).get("condition", "clear")
    if room.get("outdoor") and weather in GRIME_WEATHER:
        clean = C.num(body.get(state, "camera_clean"), 1.0)
        if clean > GRIME_FLOOR:
            # droplets and grit build up on the lens covers despite the hood: ~0.4 %/min in rain or snow (the body's
            # camera_dirty fault starts once the self-check sees the view blur below its level)
            rate = 0.004 if weather != "drizzle" else 0.0015
            body_faults.degrade_camera(w, amount=min(clean - GRIME_FLOOR, rate * dt / 60.0), cause="weather")
    helps = (C.nav_read(state).get("remote_help") or [])
    if any(h.get("status") == "pending" and C.now(state) >= C.num(h.get("resolve_at_s")) for h in helps):
        nav = C.nav(w)
        for h in nav["remote_help"]:
            if h.get("status") != "pending" or C.now(state) < C.num(h.get("resolve_at_s")):
                continue
            if h.get("kind") == "remote":
                h["status"] = "resolved"
                r = w.robot_m()
                if r.get("stuck") and str(r.get("stuck")) in REMOTE_RESOLVABLE:
                    r["stuck"] = None
                # the operator re-localizes the robot and restarts its navigation software (TIER1_EMBODIMENT 4.10-4.11)
                body.relocalize(w, how="remote_help")
                if body.fault(state, "software_stuck") is not None:
                    body_faults.clear_fault(w, "software_stuck", "remote_help")
            else:
                h["status"] = "passed_to_resident"
                w.stat("handoffs")
                msgs = w.sec("messages")
                msgs.append({"step": state.get("steps", 0), "time_s": C.now(state), "speaker": "robot_operator", "kind": "notify",
                             "text": "Your robot needs a hand: {}".format(h.get("issue")), "to": None, "room": None, "audience": sorted(state.get("people", {}))})
                del msgs[:-200]
    if C.first_substep(tick) or C.every(tick, 20):
        from . import traverse as T
        T.restore_all_hidden(w)
        if rid in state["rooms"]:
            H.refresh(w, rid)


@C.per_substep_index("battery_packs")
@C.index_reads("part_type")
def _is_robot_pack(rec):
    return rec.get("part_type") == "robot_battery"


@system("navigation_robot.spare_packs", order=71, max_dt=30.0, period=120)
def spare_packs_system(w, dt, tick):
    """Spare robot battery packs on a wall charging station (1 %/min, linear and capped: exact for the accumulated dt).
    The station needs power (kernel is_powered: outages, unplugged, electrical's tripped breakers)."""
    state = w.s
    if not (state.get("utilities") or {}).get("power", True):
        return
    for eid in C.ids_with(w, "battery_packs", _is_robot_pack):
        rec = state["entities"][eid]
        station_id = rec.get("location") or ""
        station = state["entities"].get(station_id)
        if station is None or station.get("category") != "charging_station" or not station.get("plugged_in", True):
            continue
        if C.num(rec.get("charge_pct")) < 100.0 and physics.is_powered(w, station_id):
            w.mut(eid)["charge_pct"] = round(min(100.0, C.num(rec.get("charge_pct")) + C.num(station.get("charge_rate_pct_per_min"), 1.0) * dt / 60.0), 3)


def battery_needed(state, meters, steps_up=0, steps_down=0, speed=None, load_kg=0.0):
    """Gauge points (%) a walk needs by the body's rules (TIER1_EMBODIMENT 4.1, 4.2, 4.6): the walking work at the
    effective speed with the load, the stair work and the hotel load over the walking and climbing time, against the
    energy the pack can deliver at its temperature. Compare it with body.gauge(state) minus the reserve to keep."""
    mode = body.effective_speed(state, speed)[0]
    p = body.profile(state)
    seconds = float(meters) / p["speeds_mps"][mode] + p["stair_s_per_step"] * (int(steps_up) + int(steps_down))
    wh = body.walk_wh(state, meters, mode, load_kg) + body.stairs_wh(state, steps_up, steps_down) + \
        body.activity_power_w(state, "motion", held_kg=load_kg) * seconds / 3600.0
    usable = body.full_wh(state) * (100.0 - body.cutoff_soc(state)) / 100.0
    return 100.0 * wh / usable if usable > 0 else float("inf")
