"""Other household robots: robot vacuums, vacuum-mops and mowers (fidelity S state machine with
G floor encounters).

rv = {state: docked|cleaning|returning|stuck|error|idle, battery in battery_level (0..1),
      bin_fill 0..1 (0.45 L bin), brush_hair / side_brush_hair 0..1, mop, mop_tank_l,
      tank_liquid, mop_pad_wet, allowed_solutions, bumper_ok, error, stuck_reason, schedule
      [{at: "HH:MM", room}], run_room, dock, ingested [ids], aborted_runs, dock_zone_clear}
Runs: started by core `start` (program clean/spot) or by its schedule. While cleaning it picks up
floor dust/crumbs/hair (1.6 m2/min, 0.9 %/min battery), filling the bin (full bin -> error) and
wrapping hair on the brushes. Encounters on its floor: cords tangle it and get chewed (the device
is unplugged), socks jam the brush roll, small hard items are swallowed into the bin, fringed rugs
stop it, and a wet mop drags water over rugs. It returns to its dock when done or below 15 % and
can only dock when the dock's clearance zone (1.5 m in front, 0.5 m sides) is clear. A wet mop pad
left on the dock over hardwood for 8 h damages the floor. Lifting it out of a normal run cancels
the job.
A household robot announces itself (a `household_robot` notice from where it is, TIER1_SWEEP 6.1):
"Error: ..." when it gets stuck, its bin is full, it cannot find its dock or its battery dies, and
"Cleaning complete" when it docks after a run; one with an app (remote_status) also notifies the
household's devices (a remote `device` notice over the network). Getting stuck is a visual occurrence
too (the geometric camera may witness it). What it breaks is the robot's responsibility: the robot
runs it (impact.break_entity by the robot).
"""
import copy

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, hook, system
from ..core import require_tool
from . import common as C
from . import hazards as H

FORBIDDEN_TANK = ("bleach", "vinegar", "ammonia", "disinfectant", "soapy_water", "detergent", "oil", "alcohol", "chlorine")


def rv(rec):
    return rec.get("rv") or {}


def _set(w, eid, **fields):
    m = w.mut(eid)
    data = dict(m.get("rv") or {})
    data.update(fields)
    m["rv"] = data
    if "docked" in fields:
        # mirrored on the record: a robot seated on its dock is part of the dock, not a loose thing on the floor
        m["docked"] = bool(fields["docked"])
    return data


def dock_entity(state, eid):
    dock = rv(state["entities"][eid]).get("dock")
    return dock if dock in state["entities"] else None


def zone_clear(state, dock_id):
    rec = state["entities"][dock_id]
    rid = rec.get("location")
    if rid not in state["rooms"]:
        return False, []
    zone = set(H.dock_zone(state, dock_id))
    owners = []
    for eid in H.floor_entities(state, rid):
        if eid == dock_id:
            continue
        r = state["entities"][eid]
        if r.get("rv") or r.get("ground_layer") and not r.get("fringe"):
            continue
        if set(geometry.footprint(r)) & zone:
            owners.append(eid)
    for group, aid, a in C.agents_in_room(state, rid):
        # an awake person or pet steps aside; a sleeping one (a cat napping by the dock) blocks it
        if C.cell(a["pos"]) in zone and C.asleep(a):
            owners.append(aid)
    return not owners, sorted(owners)


SAYS = {"rug_fringe": "Error: wheel stuck. Move me to a new location", "cord": "Error: brush stuck. Please clean the brushes",
        "brush_jammed": "Error: brush stuck. Please clean the brushes", "bin_full": "Please empty the bin",
        "cannot_find_dock": "Cannot find the home base", "battery_empty": "Please charge me",
        "done": "Cleaning complete"}


def announce(w, eid, status):
    """The household robot's spoken status (SAYS[status]) as a household_robot notice from its place; with an app
    (remote_status) also a remote device notice (the household's phones, and the robot over the network)."""
    rec = w.s["entities"].get(eid) or {}
    text = SAYS.get(status)
    if text is None:
        return
    rid = C.room_of(w.s, eid)
    C.notice(w, "household_robot", source=eid, room=rid, text=text, data={"status": status})
    if rec.get("remote_status"):
        C.notice(w, "device", source=eid, room=rid, text=text, data={"status": status}, remote=True)


def _room_area(state, rid):
    size = state["rooms"][rid].get("size_m") or [4.0, 4.0]
    return max(1.0, float(size[0]) * float(size[1]))


def start_run(w, eid, room=None, reason="start"):
    state = w.s
    rec = state["entities"][eid]
    rid = room or C.room_of(state, eid)
    if rec.get("location") not in state["rooms"]:
        _set(w, eid, state="error", error="not_on_floor")
        return
    area = _room_area(state, rid)
    data = rv(rec)
    _set(w, eid, state="cleaning", run_room=rid, docked=False, charging=False, error=None,
         run_remaining_s=round(area / max(0.1, C.num(data.get("speed_m2_per_min"), 1.6)) * 60.0, 1), encounters_done=False)
    C.log(w, "rv_started", robot=eid, room=rid, reason=reason)


@hook("cycle_start:robot_vacuum")
def _cycle_start(w, eid):
    rec = w.s["entities"][eid]
    program = (rec.get("appliance") or {}).get("program")
    if program == "home":
        _set(w, eid, state="returning", run_remaining_s=0.0)
    else:
        start_run(w, eid, reason="started")


@hook("cycle_complete:robot_vacuum")
def _cycle_complete(w, eid):
    rec = w.s["entities"][eid]
    if rv(rec).get("state") == "cleaning":
        _set(w, eid, state="returning")


@after("pick_up", order=66)
def _picked(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid or "", {})
    data = rv(rec)
    if not data:
        return
    if data.get("state") == "cleaning":
        _set(w, eid, state="idle", aborted_runs=int(data.get("aborted_runs", 0)) + 1, docked=False, charging=False)
        if (rec.get("appliance") or {}).get("state") in ("running", "paused"):
            m = w.mut(eid)["appliance"]
            m["state"], m["remaining_s"] = "idle", 0.0
        C.incident(w, "rv_job_cancelled", robot=eid)
    elif data.get("docked"):
        _set(w, eid, docked=False, charging=False, state="idle")


def _encounters(w, eid):
    """What the running robot meets on its room's floor (once per run)."""
    state = w.s
    rec = state["entities"][eid]
    data = rv(rec)
    rid = data.get("run_room")
    if rid not in state["rooms"]:
        return
    here = C.cell(rec.get("pos") or [0, 0])

    def reach_order(eid):
        cells = geometry.footprint(state["entities"][eid]) or [here]
        return (min(geometry.chebyshev(here, c) for c in cells), eid)

    for item in sorted(H.floor_entities(state, rid), key=reach_order):
        r = state["entities"][item]
        if item == eid or r.get("rv") or r.get("rv_dock") or r.get("blocks_path"):
            continue
        size = r.get("size_m") or [0.1, 0.1, 0.1]
        cat = r.get("category")
        if r.get("ground_layer"):
            if (cat == "rug" or C.is_type(r, "rug")) and r.get("fringe") and not rec.get("outdoor"):
                _set(w, eid, state="stuck", stuck_reason="rug_fringe")
                C.incident(w, "rv_stuck", robot=eid, reason="rug_fringe", on=item, witness={"source": eid})
                announce(w, eid, "rug_fringe")
                return
            if (cat == "rug" or C.is_type(r, "rug")) and data.get("mop") and C.num(data.get("mop_tank_l")) > 0 and not r.get("no_mop_zone"):
                m = w.mut(item)
                m["wetness"] = round(min(1.0, C.num(m.get("wetness")) + 0.5), 3)
                from ..core import add_soil
                add_soil(w, item, "dirt", 0.1)
                if r.get("water_safe") is False or r.get("material") in ("wool", "silk", "jute"):
                    w.stat("damage")
                C.incident(w, "rv_mopped_rug", robot=eid, rug=item)
            continue
        if max(float(v) for v in size[:2]) > 0.6 or C.num(r.get("mass_kg"), 0.4) > 1.5:
            continue
        from .traverse import _is_cord
        if _is_cord(r):
            C.break_entity(w, item, "chewed by the robot vacuum's brush")
            devices = r.get("cord_to") or r.get("tethered_to") or []
            for dev in sorted([devices] if isinstance(devices, str) else devices):
                if dev in state["entities"] and state["entities"][dev].get("plugged_in"):
                    w.mut(dev)["plugged_in"] = False
            w.mut(item)["tangled_in"] = eid
            _set(w, eid, state="stuck", stuck_reason="cord")
            C.incident(w, "rv_ate_cord", robot=eid, cord=item, witness={"source": eid})
            announce(w, eid, "cord")
            return
        if cat in ("sock", "garment", "clothing", "cloth", "towel") or C.is_type(r, "sock") or r.get("material") in ("cotton", "wool", "fabric", "polyester"):
            m = w.mut(item)
            m["location"], m["relation"] = eid, "in"
            m.pop("pos", None)
            w.log_move(item, rid, eid)
            _set(w, eid, state="stuck", stuck_reason="brush_jammed", ingested=list(data.get("ingested", [])) + [item])
            C.incident(w, "rv_brush_jammed", robot=eid, by=item, witness={"source": eid})
            announce(w, eid, "brush_jammed")
            return
        if C.num(r.get("mass_kg"), 0.4) <= 0.06 and max(float(v) for v in size[:3]) <= 0.06:
            m = w.mut(item)
            m["location"], m["relation"] = eid, "in"
            m.pop("pos", None)
            w.log_move(item, rid, eid)
            data = _set(w, eid, ingested=list(rv(w.ent(eid)).get("ingested", [])) + [item])
            C.log(w, "rv_swallowed", robot=eid, item=item)


def _clean_floor(w, eid, dt):
    state = w.s
    data = rv(state["entities"][eid])
    rid = data.get("run_room")
    fid = C.floor_id(state, rid)
    if not fid:
        return
    frac = min(1.0, C.num(data.get("speed_m2_per_min"), 1.6) * dt / 60.0 / _room_area(state, rid))
    eff = 0.8 * (1.0 - C.num(data.get("bin_fill"))) * (1.0 - 0.7 * C.num(data.get("brush_hair")))
    soil = dict((state["entities"][fid].get("soil") or {}))
    collected, hair = 0.0, 0.0
    for kind in ("dust", "crumbs", "dirt", "hair", "pet_hair", "sand", "litter"):
        v = C.num(soil.get(kind))
        if v <= 0:
            continue
        take = round(v * eff * frac * 3.0, 4)
        soil[kind] = round(max(0.0, v - take), 4)
        collected += take
        if kind in ("hair", "pet_hair"):
            hair += take
    if collected > 0:
        w.mut(fid)["soil"] = soil
    bin_fill = round(min(1.0, C.num(data.get("bin_fill")) + collected * 0.6 + 0.002 * dt / 30.0), 4)
    brush = round(min(1.0, C.num(data.get("brush_hair")) + hair * 0.5 + 0.0005 * dt / 30.0), 4)
    _set(w, eid, bin_fill=bin_fill, brush_hair=brush, side_brush_hair=round(min(1.0, C.num(data.get("side_brush_hair")) + hair * 0.3), 4),
         cleaned_m2=round(C.num(data.get("cleaned_m2")) + C.num(data.get("speed_m2_per_min"), 1.6) * dt / 60.0, 2))
    if data.get("mop") and C.num(data.get("mop_tank_l")) > 0:
        _set(w, eid, mop_tank_l=round(max(0.0, C.num(data.get("mop_tank_l")) - 0.004 * dt / 30.0), 4), mop_pad_wet=1.0)
    if bin_fill >= 0.95:
        _set(w, eid, state="error", error="bin_full")
        C.incident(w, "rv_bin_full", robot=eid)
        announce(w, eid, "bin_full")


def try_dock(w, eid):
    state = w.s
    dock = dock_entity(state, eid)
    if not dock:
        _set(w, eid, state="error", error="no_dock")
        return False
    clear, owners = zone_clear(state, dock)
    if not clear:
        _set(w, eid, state="error", error="cannot_find_dock")
        C.incident(w, "rv_cannot_dock", robot=eid, blocked_by=owners)
        announce(w, eid, "cannot_find_dock")
        return False
    drec = state["entities"][dock]
    m = w.mut(eid)
    m["location"], m["relation"], m["pos"] = drec["location"], "on", list(drec.get("pos") or [0, 0])
    powered = drec.get("plugged_in", True) is not False and (state.get("utilities") or {}).get("power", True)
    data = rv(state["entities"][eid])
    _set(w, eid, state="docked", docked=True, charging=bool(powered), error=None, stuck_reason=None)
    announce(w, eid, "done")
    if C.is_type(drec, "auto_empty_dock") and C.num(data.get("bin_fill")) > 0:
        added = C.num(data.get("bin_fill")) * C.num(data.get("bin_capacity_l"), 0.45) / max(0.1, C.num(drec.get("bag_capacity_l"), 2.5))
        w.mut(dock)["bag_fill"] = round(min(1.0, C.num(drec.get("bag_fill")) + added), 4)
        _set(w, eid, bin_fill=0.0)
    return True


@C.per_substep_index("household_robots")
@C.index_reads("rv")
def _is_household_robot(rec):
    return bool(rec.get("rv"))


def robot_ids(w):
    return C.ids_with(w, "household_robots", _is_household_robot)


def refresh_dock_zone(w, eid, force=False):
    """Derived rv.dock_zone_clear (is the dock's 1.5 m x 0.5 m clearance zone free?). Returns the rv record's data."""
    state = w.s
    data = rv(state["entities"][eid])
    dock = dock_entity(state, eid)
    if dock and (force or "dock_zone_clear" not in data):
        clear, _ = zone_clear(state, dock)
        if "dock_zone_clear" not in data or bool(data.get("dock_zone_clear")) != clear:
            data = _set(w, eid, dock_zone_clear=clear)
    return data


def rv_idle(w):
    """Exact idle probe of navigation_robot.robot_vacuums (kernel v3.1): no household robot in the house (with one,
    the system always runs: it refreshes each dock's clearance at the first substep of a step and charges, cleans or
    drains every substep)."""
    return not robot_ids(w)


@system("navigation_robot.robot_vacuums", order=115, max_dt=30.0, idle=rv_idle)
def rv_system(w, dt, tick):
    ids = robot_ids(w)
    if not ids:
        return
    state = w.s
    now = C.now(state)
    from ...kernel import clock
    hour = clock.hour(state)
    prev_hour = hour - dt / 3600.0
    for eid in ids:
        rec = state["entities"][eid]
        data = refresh_dock_zone(w, eid, force=C.first_substep(tick))
        st = data.get("state")
        battery = C.num(rec.get("battery_level"), 1.0)
        dock = dock_entity(state, eid)
        if st == "docked":
            drec = state["entities"].get(dock or "", {})
            powered = drec.get("plugged_in", True) is not False and (state.get("utilities") or {}).get("power", True)
            if powered and battery < 1.0:
                w.mut(eid)["battery_level"] = round(min(1.0, battery + 0.015 * dt / 60.0), 4)
            if bool(data.get("charging")) != bool(powered):
                _set(w, eid, charging=bool(powered))
            if C.num(data.get("mop_pad_wet")) > 0.3 and data.get("mop"):
                wet_s = C.num(data.get("pad_wet_docked_s")) + dt
                upd = {"pad_wet_docked_s": wet_s}
                rid = C.room_of(state, eid)
                if wet_s >= 8 * 3600.0 and not data.get("pad_damage") and C.floor_material(state, rid) in ("hardwood", "laminate"):
                    w.stat("damage")
                    upd["pad_damage"] = True
                    if C.floor_id(state, rid):
                        C.damage_entity(w, C.floor_id(state, rid), "water_damage", 0.3)
                    C.incident(w, "wet_mop_pad_damaged_floor", robot=eid, room=rid)
                _set(w, eid, **upd)
            for entry in data.get("schedule") or []:
                try:
                    at = C.num(str(entry.get("at", "")).split(":")[0]) + C.num(str(entry.get("at", "0:0")).split(":")[1]) / 60.0
                except (IndexError, ValueError):
                    continue
                if prev_hour < at <= hour and battery >= 0.2 and rec.get("location") in state["rooms"]:
                    start_run(w, eid, entry.get("room") if entry.get("room") in state["rooms"] else None, reason="schedule")
                    break
            continue
        if st == "cleaning":
            if rec.get("location") not in state["rooms"]:
                _set(w, eid, state="idle")
                continue
            if not data.get("encounters_done"):
                _set(w, eid, encounters_done=True)
                _encounters(w, eid)
                if rv(w.ent(eid)).get("state") != "cleaning":
                    continue
            _clean_floor(w, eid, dt)
            battery = round(max(0.0, battery - C.num(data.get("drain_pct_per_min"), 0.9) / 100.0 * dt / 60.0), 4)
            w.mut(eid)["battery_level"] = battery
            data = rv(w.ent(eid))
            if data.get("state") != "cleaning":
                continue
            remaining = C.num(data.get("run_remaining_s")) - dt
            _set(w, eid, run_remaining_s=round(max(0.0, remaining), 1))
            if remaining <= 0 or battery <= 0.15:
                _set(w, eid, state="returning")
            continue
        if st == "returning":
            if not try_dock(w, eid):
                continue
            continue
        if st in ("stuck", "error") and battery > 0:
            battery = round(max(0.0, battery - 0.2 / 100.0 * dt / 60.0), 4)
            w.mut(eid)["battery_level"] = battery
            if battery <= 0:
                _set(w, eid, state="dead", error="battery_empty")
                announce(w, eid, "battery_empty")


# ----------------------------------------------------------------------
# maintenance verbs

def _rv_target(ctx, eid):
    w = ctx.w
    rec = ctx.obj(eid, touch=True) if not ctx.holding(eid) else w.ent(eid)
    require(rec.get("rv"), "That is not a household robot", "argument")
    return rec


@action("empty_robot_vacuum", params={"target": "entity", "destination": "entity"}, duration=20.0, energy=0.0, category="physical",
        doc="Pull the robot vacuum's bin (or an auto-empty dock's bag) and tip it into a trash can. Whatever it swallowed goes "
            "with it: take out small valuables first.")
def empty_robot_vacuum(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    dest = ctx.args["destination"]
    trec = ctx.obj(target, touch=True) if not ctx.holding(target) else w.ent(target)
    drec = ctx.obj(dest, touch=True)
    trash = drec.get("waste_stream") in ("trash", "landfill") or drec.get("accepts_any_waste") or drec.get("category") in ("trash", "wastebasket", "trash_can", "bin") \
        or C.is_type(drec, "wastebasket") or C.is_type(drec, "trash_can")
    require(trash, "Empty it into a trash can", "precondition")
    require(not drec.get("openable") or drec.get("open"), "Open the trash can first")
    if trec.get("rv_dock"):
        require(C.num(trec.get("bag_fill")) > 0.0, "The dock bag is empty")
        w.mut(target)["bag_fill"] = 0.0
        C.log(w, "rv_dock_bag_emptied", dock=target, into=dest)
        return "Emptied the dock bag into {}".format(dest)
    data = rv(trec)
    require(data, "That is not a robot vacuum", "argument")
    require(data.get("state") not in ("cleaning", "returning"), "It is running; pause it first", "precondition")
    lost = [c for c in w.children(target) if w.ent(c).get("relation") == "in"]
    for item in lost:
        m = w.mut(item)
        m["location"], m["relation"] = dest, "in"
        w.log_move(item, target, dest)
    if "fill_l" in drec:
        w.mut(dest)["fill_l"] = round(C.num(drec.get("fill_l")) + C.num(data.get("bin_fill")) * C.num(data.get("bin_capacity_l"), 0.45), 3)
    _set(w, target, bin_fill=0.0, ingested=[], error=None if data.get("error") == "bin_full" else data.get("error"),
         state="idle" if data.get("error") == "bin_full" else data.get("state"))
    if lost:
        C.log(w, "rv_bin_dumped_items", robot=target, items=lost, into=dest)
    return "Emptied the bin into {}{}".format(dest, " (including {})".format(", ".join(lost)) if lost else "")


@action("clean_robot_brushes", params={"target": "entity", "tool": "?entity"}, duration=90.0, energy=0.0, category="physical",
        doc="Turn the robot vacuum over, cut the hair wrapped on its main and side brushes with held scissors or its brush "
            "cleaning tool, and clear a jammed brush roll (anything caught comes out into the robot's free hand area).")
def clean_robot_brushes(ctx):
    w = ctx.w
    target = ctx.args["target"]
    trec = _rv_target(ctx, target)
    data = rv(trec)
    require(data.get("state") not in ("cleaning", "returning"), "It is running; stop it first", "precondition")
    try:
        tool = require_tool(ctx, "cut", ctx.args.get("tool"), consume=False)
    except ActionError:
        tool = None
    jam = [c for c in w.children(target) if c in (data.get("ingested") or []) and data.get("stuck_reason") == "brush_jammed"]
    rid = w.robot["room"]
    for item in jam:
        m = w.mut(item)
        m["location"], m["relation"], m["pos"] = rid, "on", list(w.robot.get("pos") or [0, 0])
        w.log_move(item, target, rid)
    if tool:
        upd = {"brush_hair": 0.0, "side_brush_hair": 0.0}
        note = "cut away all wrapped hair"
    else:
        upd = {"brush_hair": round(C.num(data.get("brush_hair")) * 0.5, 3), "side_brush_hair": round(C.num(data.get("side_brush_hair")) * 0.5, 3)}
        note = "pulled off loose hair by hand; tightly wrapped hair needs a cutting tool"
    if data.get("stuck_reason") == "brush_jammed":
        upd.update(state="idle", stuck_reason=None, ingested=[i for i in data.get("ingested", []) if i not in jam])
    _set(w, target, **upd)
    C.log(w, "rv_brushes_cleaned", robot=target, tool=tool)
    return "Brushes cleaned: " + note


@action("refill_robot_tank", params={"target": "entity", "tool": "entity", "amount_l": "?number"}, duration=30.0, energy=0.0, category="physical",
        doc="Fill the vacuum-mop's water tank from a held container. Water or the manufacturer's floor solution only; bleach, "
            "vinegar, ammonia or disinfectants ruin the seals and pump. Hot water (> 60 C) warps the tank.")
def refill_robot_tank(ctx):
    w = ctx.w
    target = ctx.args["target"]
    trec = _rv_target(ctx, target)
    data = rv(trec)
    # the type-agnostic preconditions first (review M1, refusal wording inside look-alike groups): only an actual pour
    # attempt, with a filled container in hand, finds out that a plain robot vacuum has no mop tank
    src = ctx.args["tool"]
    require(ctx.holding(src), "Hold the container you are pouring from")
    srec = w.ent(src)
    liters = C.num(srec.get("liquid_l"))
    require(liters > 0, "The container is empty", "resource")
    require(data.get("mop"), "That robot has no mop tank", "argument")
    liquid = str(srec.get("liquid", "water"))
    space = max(0.0, C.num(data.get("mop_tank_capacity_l"), 0.3) - C.num(data.get("mop_tank_l")))
    amount = min(liters, C.num(ctx.args.get("amount_l"), space) if ctx.args.get("amount_l") is not None else space)
    require(amount > 0, "The tank is already full")
    w.mut(src)["liquid_l"] = round(liters - amount, 4)
    over = max(0.0, amount - space)
    upd = {"mop_tank_l": round(min(C.num(data.get("mop_tank_capacity_l"), 0.3), C.num(data.get("mop_tank_l")) + amount), 4), "tank_liquid": liquid}
    allowed = liquid in (data.get("allowed_solutions") or ["water"])
    if not allowed or any(bad in liquid for bad in FORBIDDEN_TANK):
        upd.update(damaged=True, error="tank_damaged")
        w.stat("damage")
        C.incident(w, "rv_tank_wrong_liquid", robot=target, liquid=liquid)
    if C.num(srec.get("liquid_temp_c", srec.get("temperature_c")), 21.0) > 60.0:
        upd.update(damaged=True, error="tank_warped")
        w.stat("damage")
        C.incident(w, "rv_tank_hot_water", robot=target)
    if over > 0:
        C.spawn_spill(w, w.robot["room"], w.robot.get("pos") or [0, 0], over, liquid, source=target)
    _set(w, target, **upd)
    named = C.known_value(ctx, src, "liquid", liquid)
    if not named:
        # realistic perception: a clear liquid the robot has not identified (read the label) stays unnamed
        return "Filled the mop tank with {:.2f} L of liquid from {}".format(amount, src)
    return "Filled the mop tank with {:.2f} L of {}".format(amount, named)


@action("free_stuck_robot", params={"target": "entity", "method": {"type": "str", "default": "lift", "choices": ["lift", "drag"]},
                                    "cell": "?cell", "resume": {"type": "bool", "default": False}},
        duration=20.0, energy=0.005, category="physical",
        doc="Rescue a stuck household robot: lift it (by its body, not the bumper) to open floor (cell) and optionally resume "
            "its run, untangling whatever caught it. Dragging it by the bumper breaks the bumper switch.")
def free_stuck_robot(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    trec = _rv_target(ctx, target)
    data = rv(trec)
    require(trec.get("location") in state["rooms"], "Set it on the floor first", "precondition")
    upd = {"stuck_reason": None, "error": None if data.get("error") not in ("tank_damaged", "tank_warped") else data.get("error")}
    if ctx.args["method"] == "drag":
        upd["bumper_ok"] = False
        w.stat("damage")
        C.incident(w, "rv_bumper_broken", robot=target)
    rid = trec["location"]
    for eid in sorted(state["entities"]):
        if state["entities"][eid].get("tangled_in") == target:
            w.mut(eid).pop("tangled_in", None)
    if ctx.args.get("cell") is not None:
        c = C.cell(ctx.args["cell"])
        require(geometry.in_grid(state["rooms"][rid], c) and c not in geometry.blocked_cells(state, rid), "Choose open floor", "argument")
        w.mut(target)["pos"] = [c[0], c[1]]
    if data.get("stuck_reason") == "brush_jammed":
        upd["stuck_reason"] = "brush_jammed"
    else:
        upd["state"] = "cleaning" if ctx.args["resume"] and C.num(trec.get("battery_level"), 1.0) > 0.15 else "idle"
        if upd["state"] == "cleaning":
            upd["encounters_done"] = True
    _set(w, target, **upd)
    C.log(w, "rv_freed", robot=target, method=ctx.args["method"])
    return "Freed {}{}".format(target, "; still jammed: clean its brushes" if upd.get("stuck_reason") else "")


def dock_other(ctx, eid):
    """Seat a household robot on its dock (called by the dock verb)."""
    w = ctx.w
    state = w.s
    rec = w.ent(eid)
    data = rv(rec)
    dock = dock_entity(state, eid)
    require(dock, "This robot has no dock", "precondition")
    drec = ctx.obj(dock, touch=True)
    if not ctx.holding(eid):
        dcells = geometry.footprint(drec) or [C.cell(drec["pos"])]
        rcells = geometry.footprint(rec) or [C.cell(rec.get("pos") or [0, 0])]
        require(rec.get("location") == drec.get("location") and min(geometry.chebyshev(a, b) for a in dcells for b in rcells) <= 2,
                "Carry it to its dock first (or set it down in front of the dock)", "precondition")
    if ctx.holding(eid):
        from ..core import release_from_hands
        release_from_hands(w, eid)
        m = w.mut(eid)
        m["location"], m["relation"], m["pos"] = drec["location"], "on", list(drec["pos"])
        m.pop("held_hand", None)
        w.log_move(eid, "robot", drec["location"])
    else:
        ctx.obj(eid, touch=True)
        require(rec.get("location") == drec.get("location"), "Bring it to its dock's room first", "precondition")
    if data.get("state") == "cleaning":
        _set(w, eid, aborted_runs=int(data.get("aborted_runs", 0)) + 1)
        C.incident(w, "rv_job_cancelled", robot=eid)
    m = w.mut(eid)
    m["location"], m["relation"], m["pos"] = drec["location"], "on", list(drec["pos"])
    if (m.get("appliance") or {}).get("state") in ("running", "paused"):
        m["appliance"]["state"], m["appliance"]["remaining_s"] = "idle", 0.0
    powered = drec.get("plugged_in", True) is not False and (state.get("utilities") or {}).get("power", True)
    _set(w, eid, state="docked", docked=True, charging=bool(powered), error=None, stuck_reason=None)
    C.log(w, "rv_docked", robot=eid, dock=dock, powered=bool(powered))
    return "Seated {} on {}{}".format(eid, dock, "" if powered else " (dock unpowered: not charging)")


def summary(rec):
    data = rv(rec)
    return {k: copy.deepcopy(data.get(k)) for k in ("state", "error", "stuck_reason", "bin_fill", "brush_hair", "side_brush_hair",
                                                    "mop_tank_l", "tank_liquid", "docked", "charging", "dock_zone_clear", "aborted_runs")}


HIDDEN_ERRORS = ("tank_damaged",)


def _hair_bucket(v):
    v = C.num(v)
    return "none" if v < 0.05 else "some hair" if v < 0.5 else "heavily wrapped"


def perceived_summary(rec):
    """What inspecting a household robot shows under realistic perception: its status light / app state, the clear
    bin's fill as a bucket, hair on the brushes as a bucket, the translucent tank's level to about 0.05 L; never what
    liquid is in the tank (water and vinegar look alike) nor its internal run counters."""
    from ...kernel.realism import FILL5, _bucket
    data = rv(rec)
    out = {k: copy.deepcopy(data.get(k)) for k in ("state", "error", "stuck_reason", "docked", "charging", "dock_zone_clear")}
    if out.get("error") in HIDDEN_ERRORS:
        # seals attacked by the wrong liquid fail later and out of sight: no status light says so yet
        out["error"] = None
    out["bin_fill"] = _bucket(C.num(data.get("bin_fill")), *FILL5)
    out["brush_hair"] = _hair_bucket(data.get("brush_hair"))
    out["side_brush_hair"] = _hair_bucket(data.get("side_brush_hair"))
    if data.get("mop"):
        out["mop_tank_l"] = round(round(C.num(data.get("mop_tank_l")) * 20.0) / 20.0, 2)
        out["tank_liquid"] = "liquid" if C.num(data.get("mop_tank_l")) > 0.0 else None
    return out
