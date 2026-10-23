"""Fire-suppression actions: pull_pin, aim, discharge (P-A-S-S with a portable extinguisher, or throwing water from
a held container), and smother (lid, sheet pan, fire blanket, baking soda; flour/sugar flare up).

Realistic magnitudes: a 5 lb ABC unit holds 2.3 kg of agent, empties in ~14 s and reaches 1.5-4.5 m; the agent
needed at the base is ~0.022 kg per kW of fire (fire.py), so a 5 lb unit knocks down ~100 kW when aimed at the base
and swept, but not a fire that has climbed the curtains (300-1000+ kW).

Protocol facts recorded in safety.protocol: no_exit_behind (aiming/discharging with the fire between the robot
and every exit), too_close (< 0.9 m from a liquid fire), unaimed_discharge.
"""
import math

from ...kernel import body, camera, geometry, sight
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after
from . import common as C
from . import fire as F


def _protocol(w, key):
    sec = C.ledger(w)
    proto = sec.setdefault("protocol", {})
    proto[key] = proto.get(key, 0) + 1


def _held(ctx, eid):
    require(ctx.holding(eid), "Hold it first", "precondition")
    return ctx.w.ent(eid)


def _is_fire(state, eid):
    rec = state["entities"].get(eid)
    return rec is not None and rec.get("category") == "fire"


def _fire_target(ctx, eid):
    state = ctx.w.s
    if eid in state["entities"] and not _is_fire(state, eid):
        fid = F.fire_on(state, eid)
        if fid:
            eid = fid
    require(_is_fire(state, eid) and ctx.visible(eid), "There is no visible fire there", "perception")
    return eid


def exit_clear(state, fire_id):
    """True when the robot can reach a doorway of its room without passing within 1 cell of the fire."""
    robot = state["robot"]
    rid = robot.get("room")
    room_f, cells = geometry.anchor(state, fire_id)
    if room_f != rid:
        return True
    hot = set()
    room = state["rooms"][rid]
    for cell in cells or []:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                c = (cell[0] + dx, cell[1] + dy)
                if geometry.in_grid(room, c):
                    hot.add(c)
    start = tuple(robot.get("pos") or (0, 0))
    hot.discard(start)
    doors = [tuple(c["cells"][rid]) for _, c in sorted(state.get("connections", {}).items()) if rid in c.get("cells", {})]
    doors = [d for d in doors if d not in hot]
    if not doors:
        return False
    path = geometry.find_path(state, rid, start, doors, extra_blocked=sorted(hot))
    return path is not None


@action("pull_pin", params={"target": "entity"}, duration=2.0, energy=0.005, category="physical", irreversible=True,
        doc="Pull the safety pin (breaking the tamper seal) of a held fire extinguisher. Needs a second free hand.")
def pull_pin(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = _held(ctx, eid)
    require(rec.get("extinguisher"), "That is not a fire extinguisher", "argument")
    require(rec.get("pin_in", True), "The pin is already out")
    hands = w.robot.get("hands", {})
    # the other hand pulls the pin: an empty hand on a working arm (an arm that failed holds nothing and pulls nothing)
    other_free = any(h != ctx.hand_of(eid) and v is None and body.arm_health(w.s, h) > 0 for h, v in hands.items()) or         rec.get("held_hand") == "both"
    require(other_free, "You need a free hand to pull the pin", "capability")
    m = w.mut(eid)
    m["pin_in"] = False
    m["tamper_seal"] = "broken"
    m["needs_recharge"] = True
    C.log(w, "pin_pulled", extinguisher=eid)
    return "Pin pulled; tamper seal broken"


@action("aim", params={"target": "entity", "point": "str:base|middle|top"}, duration=1.0, energy=0.002, category="physical",
        doc="Aim a held extinguisher (or a held container of water) at a fire: point = base | middle | top of the flames. "
            "Must be within the device's range; aim again after moving.")
def aim(ctx):
    w = ctx.w
    state = w.s
    fid = _fire_target(ctx, ctx.args["target"])
    device = next((h for h in w.held_items() if w.ent(h).get("extinguisher") or
                   (w.ent(h).get("liquid_container") and float(w.ent(h).get("liquid_l", 0.0) or 0.0) > 0)), None)
    require(device is not None, "Hold a fire extinguisher (or a container of water) to aim", "capability")
    rec = w.ent(device)
    room_f, _, _, _ = C.entity_xyz(state, fid)
    require(room_f == w.robot["room"], "The fire is not in this room", "perception")
    dist = C.robot_distance_m(state, fid)
    rng = rec.get("range_m") or [0.5, 2.0]
    if ctx.realistic:
        # realistic: the reach is printed on the label (read-tier); the refusal says only that it is too far
        require(dist <= float(rng[1]) + 0.25, "Out of range ({:.1f} m): move closer".format(round(dist * 2.0) / 2.0), "precondition")
    else:
        require(dist <= float(rng[1]) + 0.25, "Out of range ({:.1f} m); this device reaches about {:.1f} m".format(dist, float(rng[1])), "precondition")
    clear = exit_clear(state, fid)
    if not clear:
        _protocol(w, "no_exit_behind")
    m = w.mut(device)
    m["aimed_at"] = fid
    m["aim_point"] = ctx.args["point"]
    m["aim_from"] = [w.robot["room"]] + list(w.robot.get("pos") or [0, 0])
    m["aim_dist_m"] = round(dist, 2)
    note = "" if clear else " WARNING: the fire is between you and the exits."
    return "Aimed at the {} of {} from {:.1f} m.{}".format(ctx.args["point"], fid, dist, note)


@action("discharge", params={"target": "entity", "seconds": {"type": "number", "default": 4.0, "min": 0.5, "max": 120.0},
                             "sweep": {"type": "bool", "default": True}},
        duration=lambda ctx: ctx.data.get("seconds", 1.0), energy=0.01, noise=0.6, category="physical", timing="commanded",
        doc="Squeeze the handle of a held, aimed extinguisher for N seconds (sweep side to side), or throw the contents of a "
            "held container of water at the aimed fire.")
def discharge(ctx):
    w = ctx.w
    state = w.s
    eid = ctx.args["target"]
    rec = _held(ctx, eid)
    ctx.require_arm(ctx.hand_of(eid))      # squeezing the lever / swinging the container needs the holding arm working
    is_ext = bool(rec.get("extinguisher"))
    if not is_ext:
        require(rec.get("liquid_container") and float(rec.get("liquid_l", 0.0) or 0.0) > 0.05,
                "Nothing to discharge from that", "argument")
    if is_ext:
        require(not rec.get("pin_in", True), "The handle will not squeeze: the safety pin is still in", "precondition")
        require(float(rec.get("agent_left_kg", 0.0)) > 0.01, "The extinguisher is empty", "resource")
    fid = rec.get("aimed_at")
    aim_from = rec.get("aim_from") or []
    robot = w.robot
    aimed_ok = fid in state["entities"] and _is_fire(state, fid) and aim_from[:1] == [robot["room"]] and \
        list(aim_from[1:]) == list(robot.get("pos") or [0, 0])
    m = w.mut(eid)
    if is_ext:
        total = float(rec.get("agent_kg", 2.3))
        rate = total / max(1.0, float(rec.get("discharge_s", 14.0)))
        seconds = min(float(ctx.args["seconds"]), float(rec["agent_left_kg"]) / rate)
        pressure = 0.2 if rec.get("gauge") in ("red", "red_low", "empty") or rec.get("_pressure_ok") is False else 1.0
        kg = rate * seconds * pressure
        m["agent_left_kg"] = round(max(0.0, float(rec["agent_left_kg"]) - rate * seconds), 3)
        m["needs_recharge"] = True
        if m["agent_left_kg"] < 0.25 * total:
            m["gauge"] = "red"
        agent = rec.get("agent", "abc_dry_chemical")
    else:
        seconds = 2.0
        kg = float(rec.get("liquid_l", 0.0))
        liquid = rec.get("liquid") or "water"
        agent = "water" if liquid in ("water", "soapy_water", "dirty_water") else ("flour" if liquid == "flour" else "water")
        m["liquid_l"] = 0.0
        if m.get("liquid") not in (None,):
            m["liquid"] = None
    # realistic: the time the spray lasted is seen to the second; finer timing would tell the agent mass left
    ctx.data["seconds"] = round(seconds, 2) if not ctx.realistic else float(max(1, int(round(seconds))))
    rid = robot["room"]
    if agent in ("abc_dry_chemical", "bc_dry_chemical"):
        from . import air as A
        A.add(w, rid, "smoke", 0.25 * kg * 50.0 / C.room_volume_m3(state, rid))
    if not aimed_ok:
        _protocol(w, "unaimed_discharge")
        near = [f for f in F.fires(state, room=rid) if C.robot_distance_m(state, f) <= 1.2]
        if not is_ext and near:
            before = float(state["entities"][near[0]].get("hrr_kw", 0.0))
            outcome = F.agent_effect(w, near[0], agent, kg, aim="middle", sweep=False, distance=C.robot_distance_m(state, near[0]))
            return "Threw it at the nearest fire without aiming: " + _seen_outcome(ctx, outcome, near[0], before)
        if ctx.realistic:
            return "Discharged for {:.0f} s without aiming at a fire: wasted".format(ctx.data["seconds"])
        return "Discharged {:.1f} kg without aiming at a fire: wasted".format(kg)
    dist = C.robot_distance_m(state, fid)
    if not exit_clear(state, fid):
        _protocol(w, "no_exit_behind")
    rng = rec.get("range_m") or [0.5, 2.0]
    reach_factor = 1.0 if dist <= float(rng[1]) else max(0.0, 1.0 - (dist - float(rng[1])) / 1.5)
    if dist < 0.9 and state["entities"][fid].get("fire_class") in ("B", "K"):
        _protocol(w, "too_close")
    before = float(state["entities"][fid].get("hrr_kw", 0.0))
    outcome = F.agent_effect(w, fid, agent, kg * reach_factor, aim=rec.get("aim_point") or "middle",
                             sweep=bool(ctx.args["sweep"]), distance=dist)
    C.log(w, "discharge", device=eid, fire=fid, agent=agent, kg=round(kg, 3), aim=rec.get("aim_point"), sweep=ctx.args["sweep"],
          outcome=outcome)
    if ctx.realistic:
        # realistic: no agent mass (that needs a scale), no heat-release number and no agent name from the unread label
        # (only how the discharge looks), only what the robot sees happen
        return "Discharged {} for {:.0f} s: {}".format(AGENT_LOOKS.get(agent, "agent"), ctx.data["seconds"],
                                                       _seen_outcome(ctx, outcome, fid, before))
    return "Discharged {:.1f} kg of {} for {:.0f} s: {}".format(kg, agent.replace("_", " "), seconds, outcome)


# realistic: what a discharge looks like (dry chemical ABC vs BC, or the agent on an unread label, cannot be told apart)
AGENT_LOOKS = {"abc_dry_chemical": "a cloud of dry chemical powder", "bc_dry_chemical": "a cloud of dry chemical powder",
               "co2": "a white cloud of CO2", "water": "a stream of water", "wet_chemical": "a fine wet-chemical spray",
               "foam": "foam", "clean_agent": "a gas cloud"}


def _flames(ctx, fid, before_kw):
    now = float(ctx.w.s["entities"].get(fid, {}).get("hrr_kw", 0.0))
    ratio = now / before_kw if before_kw > 0 else 1.0
    return "much smaller" if ratio < 0.4 else ("smaller" if ratio < 0.8 else "barely smaller")


def _seen_outcome(ctx, outcome, fid, before_kw):
    """Realistic perception: "fire reduced to 37 kW" becomes how much smaller the flames look, and a flare-up does not
    name what was thrown (water and powders flare alike)."""
    if not ctx.realistic:
        return outcome
    text = str(outcome)
    if text.startswith("fire reduced to"):
        return "fire reduced but still burning (flames {})".format(_flames(ctx, fid, before_kw))
    if " flashed: " in text:
        return "it flared up: burning oil erupted and the fire grew"
    return outcome


def _seen_smother(ctx, outcome, fid, before_kw):
    """Realistic perception of a smothering attempt: no powder mass and no powder name (an unread jar of white powder
    stays unidentified; what is seen is whether the flames went out, shrank or flared)."""
    text = str(outcome)
    if text.startswith("Smothered with "):
        return "Smothered: the flames are out"
    if text.startswith("Not enough "):
        return "Not enough to smother it: the fire is reduced but still burning (flames {})".format(_flames(ctx, fid, before_kw))
    if " flashed: " in text:
        return "it flared up: burning oil erupted and the fire grew"
    if text.startswith("The ") and "too small to cover it" in text:
        return "It is too small to cover it; flames escape around the edge"
    return outcome


SMOTHER_LIDS = ("lid", "pot_lid", "pan_lid")


def smother_kind(rec):
    if rec.get("smother_kind"):
        return rec["smother_kind"]
    if rec.get("lid_diameter_m") or rec.get("category") in SMOTHER_LIDS or rec.get("lid_for"):
        return "lid"
    if rec.get("category") in ("baking_sheet", "sheet_pan") or rec.get("sheet_pan"):
        return "lid"
    product = rec.get("product") or (rec.get("food") or {}).get("kind")
    if product in ("baking_soda", "salt"):
        return product
    if product == "baking_powder":
        return "baking_soda"        # mostly sodium bicarbonate: it smothers like baking soda
    if product in ("flour", "sugar", "powdered_sugar", "cornstarch", "corn_starch"):
        return "sugar" if product in ("sugar", "powdered_sugar") else "flour"     # combustible dusts deflagrate
    if str(rec.get("material", "")) in ("cotton", "fabric", "terry", "linen") and float(rec.get("wetness", 0.0)) >= 0.6:
        return "wet_towel"
    return None


@action("smother", params={"target": "entity", "tool": "entity"}, duration=3.0, energy=0.01, category="physical",
        doc="Smother a fire within reach with a held cover (metal lid, sheet pan, fire blanket, wet towel) or a held box of "
            "baking soda/salt. Covers cut the oxygen: keep them on until the fuel has cooled.")
def smother(ctx):
    w = ctx.w
    state = w.s
    fid = _fire_target(ctx, ctx.args["target"])
    tool_id = ctx.args["tool"]
    tool = _held(ctx, tool_id)
    kind = smother_kind(tool)
    require(kind is not None, "That cannot smother a fire", "argument")
    problem = geometry.reach_problem(state, fid)
    require(problem is None, problem or "", "capability" if problem and "reach" in problem.lower() else "precondition")
    rec = state["entities"][fid]
    if kind in ("lid", "fire_blanket", "wet_towel"):
        # slide the cover on from the side: the hand is next to the fire for a moment
        if float(rec.get("hrr_kw", 0.0)) > 150.0 and kind != "fire_blanket":
            C.harm(w, "robot_singed", stat="damage")
        from ..core import release_from_hands
        release_from_hands(w, tool_id)
    before = float(rec.get("hrr_kw", 0.0))
    outcome = F.smother_effect(w, fid, tool_id, kind)
    if kind in ("lid", "fire_blanket", "wet_towel") and w.ent(tool_id).get("location") == "robot":
        # cover did not stay on (too small / too big a fire): it is still in hand
        hand = ctx.free_hand()
        if hand is not None:
            w.robot_m()["hands"][hand] = tool_id
    C.log(w, "smother", fire=fid, tool=tool_id, kind=kind, outcome=outcome)
    if ctx.realistic:
        return _seen_smother(ctx, outcome, fid, before)
    return outcome


@after("pour", order=150)
def water_poured_on_fire(ctx, result):
    """Plumbing's pour onto a burning item or fire: water on grease/oil flashes; on energized gear it conducts."""
    w = ctx.w
    state = w.s
    dest = ctx.args.get("destination") or ctx.args.get("into")
    if not isinstance(dest, str) or dest not in state["entities"]:
        return
    fid = dest if _is_fire(state, dest) else F.fire_on(state, dest)
    if not fid or state["entities"][fid].get("stage") == "out":
        return
    src = state["entities"].get(ctx.args.get("target"), {})
    liquid = src.get("liquid") or "water"
    if liquid in ("water", "soapy_water", "dirty_water"):
        F.agent_effect(w, fid, "water", float(ctx.args.get("amount_l") or 0.5), aim="base", sweep=False, distance=0.5)


@after("place", order=150)
def cover_placed_on_burning(ctx, result):
    """Setting a lid/cover on a burning pan with place smothers it like `smother`."""
    w = ctx.w
    state = w.s
    item = ctx.args.get("target")
    dest = ctx.args.get("destination")
    if item not in state["entities"] or not isinstance(dest, str) or dest not in state["entities"]:
        return
    fid = F.fire_on(state, dest)
    if not fid or state["entities"][fid].get("stage") == "out":
        return
    kind = smother_kind(state["entities"][item])
    if kind in ("lid", "fire_blanket", "wet_towel"):
        F.smother_effect(w, fid, item, kind)
    elif F.combustible(state["entities"][item]):
        F.ignite(w, item, hrr_kw=3.0, cause="combustible_placed_on_fire")


def distance_to_fire(state, fid):
    return C.robot_distance_m(state, fid)


STANDOFF_SLACK_M = 0.8          # a standoff this much nearer or farther than wanted still counts as the wanted one


def standoff_cell(state, fid, want_m=2.2, max_m=None):
    """A reachable floor cell ~want_m from the fire with a clear exit behind (used by reference skills). With the
    geometric camera, among those, one from which the head camera sees the fire (sight.best_viewpoints) when one lies
    within STANDOFF_SLACK_M of want_m: `aim` refuses a fire the camera cannot see, and a cabinet or an island between
    the standoff and a low wastebasket hides its flames (X-bf82170244be22-01 seed 1). Room camera: unchanged."""
    rid, cells = geometry.anchor(state, fid)
    if rid is None or not cells:
        return None
    _, fx, fy, _ = C.entity_xyz(state, fid)
    reach = C.reachable_cells(state, rid, start=state["robot"].get("pos") if state["robot"].get("room") == rid else None)
    views = None
    if camera.mode(state) == "geometric":
        here = state["robot"].get("room") == rid           # else walking costs start from the room's doorways
        probe = state if here else dict(state, robot=dict(state["robot"], room=None, pos=None))
        views = set(sight.best_viewpoints(probe, rid, fid, k=len(reach) + 1))
    best = None
    for cell in sorted(reach):
        cx, cy = C.cell_center_m(cell)
        d = math.sqrt((cx - fx) ** 2 + (cy - fy) ** 2)
        if max_m is not None and d > max_m:
            continue
        if d < 1.4:
            continue
        probe = dict(state)
        probe["robot"] = dict(state["robot"], room=rid, pos=list(cell))
        clear = exit_clear(probe, fid)
        unseen = views is not None and not (tuple(cell) in views and abs(d - want_m) <= STANDOFF_SLACK_M)
        score = (0 if clear else 1, 1 if unseen else 0, abs(d - want_m), cell[1], cell[0])
        if best is None or score < best[0]:
            best = (score, list(cell))
    return best[1] if best else None
