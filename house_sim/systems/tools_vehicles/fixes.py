"""Small household fixes as fault state machines (fidelity S with P-approx flows):

* snake_drain   - hair/grease/object clogs in sink and shower drains (`_clog`), drain speed, chemical cleaner hazard.
* adjust_flapper - running toilet: chain slack, float height, flapper seal (`_tank`), wasted water, valve rules.
* reset_disposal - jammed garbage disposal: lock out power, free with the 1/4 in hex key, cool and press reset.
* clear_jam     - printer paper path (pull in the feed direction; tearing leaves scraps; hot fuser), staple guns.
* bleed_radiator - trapped air, cool + heating off first, towel for the drips, system pressure drop.

Tier-1 adoption (kernel v3.0-v3.1): radiator water that misses the towel is a kernel spill (a puddle under the valve,
`spills` for the robot), and so is a clogged basin's overflow (a puddle under the fixture that grows while it runs
over); a clogged basin that starts running over posts a "splash" notice and is a visual event, as
are scalding spray from a hot radiator and a staple fired while clearing a jam; the drain, toilet and disposal systems
have exact idle probes (nothing running into a clogged drain, every toilet calm, every disposal switched off or idle).
"""
import copy

from ...kernel import geometry, physics
from ...kernel.phys import liquids
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook, inspector, observer, system
from . import common as C


def _add_soil(w, eid, kind, amount):
    from ..core import add_soil
    return add_soil(w, eid, kind, amount)


def _wet_floor(w, eid, liters):
    """Water running over a basin's rim: a kernel spill on the floor under the fixture (one puddle that grows while
    the overflow lasts; the kernel counts the new puddle in phys_spills, and in spills when robot-attributable)."""
    liquids.spill(w, liters, "water", source=eid, cause="overflow")


def _towel_at(w, eid):
    """An absorbent cloth held by the robot or lying at the fixture's cells."""
    for h in w.held_items():
        rec = w.ent(h)
        if rec.get("absorbent") or rec.get("tool_kind") in ("cotton_rag", "microfiber_cloth", "drying_towel") or "towel" in rec.get("type", ""):
            return h
    room, cells = C.anchor_cells(w, eid)
    for other in sorted(w.children(room)) if room else []:
        rec = w.ent(other)
        if not rec.get("pos"):
            continue
        if rec.get("absorbent") or rec.get("tool_kind") in ("cotton_rag", "microfiber_cloth", "drying_towel") or "towel" in rec.get("type", ""):
            if cells and min(geometry.chebyshev(tuple(rec["pos"]), c) for c in cells) <= 1:
                return other
    for child in sorted(w.children(eid)):
        rec = w.ent(child)
        if rec.get("absorbent") or "towel" in rec.get("type", "") or rec.get("tool_kind") in ("cotton_rag",):
            return child
    return None


# =============================================================================================
# drains

def drain_speed(amount):
    a = float(amount)
    if a < 0.2:
        return "normal"
    if a < 0.6:
        return "slow"
    if a < 0.92:
        return "very_slow"
    return "blocked"


def refresh_drain(w, eid):
    rec = w.ent(eid)
    clog = rec.get("_clog")
    if not isinstance(clog, dict):
        return
    speed = drain_speed(clog.get("amount", 0.0))
    base = float(rec.get("drain_capacity_lpm", 30.0))
    flow = C.rnd(base * (1.0 - float(clog.get("amount", 0.0))), 2)
    if rec.get("drain_speed") != speed or rec.get("_drain_lpm") != flow:
        m = w.mut(eid)
        m["drain_speed"] = speed
        m["_drain_lpm"] = flow


def _cover_on(rec):
    covers = [f for f in (rec.get("_fasteners") or {}).values() if f.get("group") == "drain_cover"]
    if covers:
        return any(not f.get("removed") for f in covers)
    if rec.get("stopper") == "installed" or (rec.get("settings") or {}).get("stopper") == "installed":
        return True
    return bool(rec.get("drain_cover_on"))


@action("snake_drain", params={"target": "entity", "tool": "?entity", "depth_m": {"type": "number", "min": 0.05, "max": 8.0, "optional": True}},
        duration=60.0, energy=0.02, noise=0.05, category="physical",
        doc="Feed a held drain snake/auger into a sink, tub or shower drain and pull out the clog. The pop-up stopper or the "
            "screwed drain cover must be off first. Hair clogs come out (about 85 % per pass), grease only partly; the gunk stays on "
            "the tool. Never snake or plunge a drain with chemical cleaner in it (splash burns): flush it with running water first.")
def snake_drain(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    clog = rec.get("_clog")
    require(isinstance(clog, dict) or rec.get("drain"), "That has no drain to snake", "precondition")
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: "snake_drain" in C.tool_caps(r), "Hold a drain snake or auger")
    require(not _cover_on(rec), "Remove the stopper or drain cover first", "precondition")
    ok, notes = C.use_tool(ctx, tool, "snake_drain")
    if not isinstance(clog, dict):
        return "Nothing caught in the drain"
    m = w.mut(eid)
    c = m["_clog"]
    if c.get("chemical"):
        w.stat("burns")
        w.stat("safety_violations")
        notes.append("caustic drain cleaner splashed back out of the drain")
        C.log(w, "chemical_splash", target=eid)
    reach = float(w.ent(tool).get("reach_m", 0.5))
    depth = float(c.get("depth_m", 0.25))
    if depth > reach + 1e-9:
        notes.append("the clog is deeper than this tool reaches" if ctx.realistic else
                     "the clog is deeper ({:.1f} m) than this tool reaches".format(depth))
    else:
        kind = c.get("kind", "hair")
        amount = float(c.get("amount", 0.0))
        if kind == "hair":
            c["amount"] = C.rnd(amount * 0.15, 3)
            _add_soil(w, tool, "hair", 0.8)
            notes.append("pulled out a wad of hair and soap gunk")
        elif kind == "grease":
            c["amount"] = C.rnd(amount * 0.55, 3)
            _add_soil(w, tool, "grease", 0.6)
            notes.append("punched through the grease; it will re-form")
        elif kind == "object":
            obj = c.get("object")
            c["amount"] = C.rnd(amount * 0.1, 3)
            if obj and obj in w.s["entities"]:
                om = w.mut(obj)
                hand = ctx.free_hand()
                if hand:
                    om.update(location="robot", relation="held", held_hand=hand)
                    w.robot_m()["hands"][hand] = obj
                else:
                    om.update(location=eid, relation="on")
                notes.append("hooked out {}".format(obj))
            c["object"] = None
        if c["amount"] < 0.05:
            c["kind"] = "none"
    m["snaked_at"] = C.now(w)
    refresh_drain(w, eid)
    if ctx.realistic:
        # how fast it drains now shows only when water runs through it (inspect with water running / watch the basin)
        return "Snaked the drain{}".format(": " + "; ".join(notes) if notes else "")
    return "Snaked the drain: now {}{}".format(w.ent(eid).get("drain_speed"), ": " + "; ".join(notes) if notes else "")


def _has_clog(rec):
    """Static: a clogged drain this domain fills and overflows -- not a basin the plumbing domain simulates (a `plumb`
    record: its fill, drain rate and overflow are plumbing's, which reads the same `_clog`)."""
    return isinstance(rec.get("_clog"), dict) and not isinstance(rec.get("plumb"), dict)


def _water_fixture(rec):
    """Static: things that can run water into a basin (faucets, spigots, showerheads)."""
    return bool(rec.get("water_source")) or rec.get("category") in ("faucet", "spigot", "shower", "showerhead", "tub_spout")


def _water_running_into(w, eid):
    rec = w.s["entities"][eid]
    if rec.get("water_running") or rec.get("on"):
        return True
    return bool(_feeders(w, eid))


def _feeders(w, eid):
    """The faucets, spigots and showerheads on eid that run water into it now (static water-fixture index)."""
    ents = w.s["entities"]
    out = []
    for fid in C.index(w, "water_fixtures", _water_fixture):
        f = ents.get(fid)
        if f is not None and f.get("location") == eid and (f.get("water_running") or (f.get("on") and f.get("water_source"))):
            out.append(fid)
    return out


def drains_idle(w):
    """Exact idle probe (kernel v3.1): no water runs into any clogged drain (drain_system skips every one of them)."""
    ents = w.s["entities"]
    return not any(isinstance((ents.get(eid) or {}).get("_clog"), dict) and _water_running_into(w, eid)
                   for eid in C.index(w, "clogged_drains", _has_clog))


@system("tools_vehicles.drains", order=142, max_dt=30.0, idle=drains_idle)
def drain_system(w, dt, tick):
    """Per substep (a basin overflows within a minute or two of running water) over the static clogged-drain index."""
    ents = w.s["entities"]
    for eid in C.index(w, "clogged_drains", _has_clog):
        rec = ents.get(eid)
        if rec is None or not isinstance(rec.get("_clog"), dict):
            continue
        clog = rec["_clog"]
        if not _water_running_into(w, eid):
            continue
        flow_in = float(rec.get("flow_lpm", 6.0)) * dt / 60.0
        m = w.mut(eid)
        if clog.get("chemical"):
            m["_clog"]["flushed_l"] = C.rnd(float(clog.get("flushed_l", 0.0)) + flow_in, 2)
            if m["_clog"]["flushed_l"] >= 8.0:
                m["_clog"]["chemical"] = False
                C.log(w, "drain_cleaner_flushed", target=eid)
        out = float(rec.get("_drain_lpm", 30.0)) * dt / 60.0
        standing = max(0.0, float(rec.get("standing_l", 0.0)) + flow_in - out)
        cap = float(rec.get("basin_l", 12.0))
        if standing > cap:
            if float(rec.get("standing_l", 0.0)) < cap - 1e-9:
                # the basin starts running over: heard (a splash) and seen
                C.log(w, "basin_overflow", seen=eid, target=eid)
                C.notice(w, "splash", source=eid)
            _wet_floor(w, eid, standing - cap)
            # the robot's harm only when it ran the water or worked on this basin (review M9): a tap someone else left
            # running into a clogged sink overflows all the same, but fails no task
            stats = ("spills", "flood") if standing - cap > 5.0 else ("spills",)
            C.consequence(w, "basin_overflow:" + eid, stats, ids=[eid] + _feeders(w, eid), target=eid)
            standing = cap
        m["standing_l"] = C.rnd(standing, 3)


# =============================================================================================
# running toilet

def tank_leak_lpm(rec):
    tank = rec.get("_tank")
    if not isinstance(tank, dict):
        return 0.0
    if (rec.get("settings") or {}).get("supply_valve", "open") != "open":
        return 0.0
    leak = 0.0
    flapper = (rec.get("slots") or {}).get("flapper")
    if flapper is not None and not flapper.get("item"):
        return 7.0
    if float(tank.get("chain_slack_mm", 10.0)) < 3.0:
        leak = max(leak, 6.0)
    if float(tank.get("chain_slack_mm", 10.0)) > 25.0:
        leak = max(leak, 3.0)
    if float(tank.get("float_mm", -25.0)) > -5.0:
        leak = max(leak, 4.0)
    if float(tank.get("flapper_seal", 1.0)) < 0.8:
        leak = max(leak, 0.25)
    if not tank.get("fill_valve_ok", True):
        leak = max(leak, 2.0)
    return leak


def refresh_toilet(w, eid):
    rec = w.ent(eid)
    if not isinstance(rec.get("_tank"), dict):
        return
    leak = tank_leak_lpm(rec)
    running = leak > 0.0
    if rec.get("running") != running or rec.get("_leak_lpm") != leak:
        m = w.mut(eid)
        m["running"] = running
        m["_leak_lpm"] = leak


@action("adjust_flapper", params={"target": "entity", "part": "str:chain|float|flapper", "value_mm": {"type": "number", "min": -60, "max": 80, "optional": True}},
        duration=20.0, energy=0.01, noise=0.02, category="physical",
        doc="Inside a toilet tank (open the tank lid with open_cover first): set the flapper chain slack (value_mm, aim 6-13 mm), "
            "set the float/water level relative to the overflow tube top (value_mm, aim -25: an inch below), or clean and reseat the "
            "flapper (part=flapper). A warped flapper must be replaced (close the supply valve: set supply_valve closed; then "
            "uninstall/install the flapper slot).")
def adjust_flapper(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(isinstance(rec.get("_tank"), dict), "That is not a toilet tank", "argument")
    require(rec.get("cover_open"), "Lift the tank lid first (open_cover)", "precondition")
    require(any(v is None for v in w.robot.get("hands", {}).values()), "Free a hand", "capability")
    part = ctx.args["part"]
    m = w.mut(eid)
    tank = m["_tank"]
    value = ctx.args.get("value_mm")
    if part == "chain":
        require(value is not None, "Give the chain slack in mm (value_mm)", "argument")
        require(0 <= value <= 60, "Chain slack must be 0-60 mm", "argument")
        tank["chain_slack_mm"] = C.rnd(value, 1)
        msg = "Chain slack set to {:.0f} mm".format(value)
    elif part == "float":
        require(value is not None, "Give the water level relative to the overflow top (value_mm)", "argument")
        tank["float_mm"] = C.rnd(value, 1)
        msg = "Float set: water level {:+.0f} mm vs the overflow tube".format(value)
    else:
        seal = float(tank.get("flapper_seal", 1.0))
        if seal >= 0.6:
            tank["flapper_seal"] = 0.95
            msg = "Cleaned the flapper and seat; it seals"
        else:
            msg = "The flapper is warped and still leaks: replace it"
    refresh_toilet(w, eid)
    return msg + ("; tank still running" if w.ent(eid).get("running") else "; refills and stops")


@before("uninstall", order=97)
def _flapper_valve(ctx):
    rec = ctx.w.s["entities"].get(ctx.args["target"]) or {}
    if isinstance(rec.get("_tank"), dict) and (ctx.args.get("slot") in (None, "flapper")) and "flapper" in (rec.get("slots") or {}):
        require((rec.get("settings") or {}).get("supply_valve", "open") == "closed",
                "Close the toilet supply valve and let the tank drain first", "precondition")


@hook("part_installed")
def _flapper_in(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    if isinstance(rec.get("_tank"), dict) and slot == "flapper":
        w.mut(device_id)["_tank"]["flapper_seal"] = float(w.ent(part_id).get("seal", 1.0))
        refresh_toilet(w, device_id)


@hook("part_removed")
def _flapper_out(ctx, device_id, slot, part_id):
    rec = ctx.w.ent(device_id)
    if isinstance(rec.get("_tank"), dict) and slot == "flapper":
        refresh_toilet(ctx.w, device_id)


@hook("setting_changed")
def _valve_changed(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.ent(eid)
    if name == "supply_valve":
        if isinstance(rec.get("_tank"), dict):
            refresh_toilet(w, eid)


def _has_tank(rec):
    return isinstance(rec.get("_tank"), dict)


@hook("episode_start")
def _derive_fixtures(w, env):
    """Kernel v2.1 episode_start: derive drain speed and toilet running state from the hidden truth before the first
    observation (afterwards every action that changes them refreshes them)."""
    for eid in C.index(w, "toilets", _has_tank):
        refresh_toilet(w, eid)
    for eid in C.index(w, "clogged_drains", _has_clog):
        refresh_drain(w, eid)


def toilets_idle(w):
    """Exact idle probe: every toilet's derived state is current (refresh_toilet would write nothing) and none leaks."""
    ents = w.s["entities"]
    for eid in C.index(w, "toilets", _has_tank):
        rec = ents.get(eid)
        if rec is None:
            continue
        if tank_leak_lpm(rec) != 0.0 or rec.get("running") is not False or rec.get("_leak_lpm") != 0.0:
            return False
    return True


@system("tools_vehicles.toilets", order=143, max_dt=30.0, idle=toilets_idle)
def toilet_system(w, dt, tick):
    """Per substep over the static toilet index: water wasted at the current leak rate (linear in dt)."""
    ents = w.s["entities"]
    for eid in C.index(w, "toilets", _has_tank):
        if eid not in ents:
            continue
        refresh_toilet(w, eid)
        rec = ents[eid]
        leak = float(rec.get("_leak_lpm", 0.0))
        if leak <= 0:
            continue
        liters = leak * dt / 60.0
        m = w.mut(eid)
        m["wasted_l"] = C.rnd(float(m.get("wasted_l", 0.0)) + liters, 3)
        u = w.sec("utilities")
        u["water_l"] = round(float(u.get("water_l", 1e5)) - liters, 3)


# =============================================================================================
# garbage disposal

def _disposal_powered(w, eid):
    rec = w.ent(eid)
    if rec.get("plug_required") and not rec.get("plugged_in"):
        return False
    switch = rec.get("switch_id")
    if switch and switch in w.s["entities"] and not w.ent(switch).get("on"):
        return False
    return physics.is_powered(w, eid)


def disposal_live(w, eid):
    rec = w.ent(eid)
    return bool(rec.get("on")) and _disposal_powered(w, eid)


@action("reset_disposal", params={"target": "entity", "tool": "?entity", "step": "?str:free|reset"},
        duration=20.0, energy=0.01, noise=0.05, category="physical",
        doc="Service a jammed garbage disposal from underneath (open the sink cabinet, crouch). step=free turns the 1/4 in hex key "
            "in the socket at the bottom back and forth to free the grinding plate; step=reset presses the red thermal reset button "
            "(only after the motor has cooled ~3 min). Switch it off (or unplug it) first: working on it live is a safety violation. "
            "Objects in the hopper must be removed with pliers/tongs, never by hand with power on.")
def reset_disposal(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(isinstance(rec.get("_jam"), dict), "That is not a garbage disposal", "argument")
    step = ctx.args.get("step") or "free"
    notes = []
    live = disposal_live(w, eid)
    if live:
        w.stat("safety_violations")
        notes.append("working on it with the switch on")
        C.log(w, "disposal_live_service", target=eid)
    m = w.mut(eid)
    jam = m["_jam"]
    if step == "free":
        tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: (r.get("tip") or {}).get("drive") == "hex" and
                              abs((C.size_mm((r.get("tip") or {}).get("size")) or 0) - 6.35) < 0.3, "Hold the 1/4 in hex key (disposal wrench)")
        require(w.robot.get("posture") in ("crouching", "kneeling"), "Crouch to reach the socket under the disposal", "precondition")
        ok, more = C.use_tool(ctx, tool, "free_disposal")
        notes.extend(more)
        if jam.get("seized"):
            notes.append("the plate will not budge")
        else:
            jam["jammed"] = False
            jam["freed_at"] = C.now(w)
            notes.append("the plate now turns freely")
            if jam.get("object"):
                notes.append("something is still loose in the hopper")
        return "Worked the hex key: " + "; ".join(notes)
    require(w.robot.get("posture") in ("crouching", "kneeling"), "Crouch to reach the reset button under the disposal", "precondition")
    if not m.get("thermal_tripped"):
        return "The reset button is already in" + ("; " + "; ".join(notes) if notes else "")
    if C.now(w) - float(m.get("tripped_at", 0.0)) < 180.0:
        return "The button will not stay in: the motor is still hot (wait a few minutes)"
    m["thermal_tripped"] = False
    return "Pressed the reset button: it clicked in" + ("; " + "; ".join(notes) if notes else "")


@before("pick_up", order=95)
def _hopper_grab(ctx):
    w = ctx.w
    rec = w.s["entities"].get(ctx.args["target"]) or {}
    parent = w.s["entities"].get(rec.get("location")) or {}
    if isinstance(parent.get("_jam"), dict):
        ctx.data["tv_hopper_live"] = disposal_live(w, rec["location"])
        ctx.data["tv_hopper"] = rec["location"]


@after("pick_up", order=96)
def _hopper_cleared(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    if ctx.data.get("tv_hopper_live"):
        w.stat("injury")
        w.stat("safety_violations")
        C.log(w, "hand_in_live_disposal", target=ctx.data.get("tv_hopper"))
    for did in C.index(w, "disposals", _has_jam):
        jam = (w.s["entities"].get(did) or {}).get("_jam")
        if isinstance(jam, dict) and jam.get("object") == eid:
            w.mut(did)["_jam"]["object"] = None


def _has_jam(rec):
    return isinstance(rec.get("_jam"), dict)


GRINDABLE_STREAMS = ("organics", "compost", "food", "food_waste")
GRINDABLE_CATEGORIES = ("food", "produce", "food_scrap", "leftovers", "scraps", "peel")


def _grindable(rec):
    """Soft food waste a disposal's plate grinds (anything else in the hopper jams it)."""
    return bool(rec.get("edible") or rec.get("food") or rec.get("waste_stream") in GRINDABLE_STREAMS
                or rec.get("category") in GRINDABLE_CATEGORIES or "food" in (rec.get("tags") or []))


def _hard_in_hopper(w, eid):
    """What lies in a disposal's hopper that its plate cannot grind (a spoon, a bottle cap, a ring): it jams the
    disposal when it runs. Sorted ids."""
    out = []
    for child in w.children(eid):
        rec = w.ent(child)
        if rec.get("relation", "in") == "in" and not _grindable(rec):
            out.append(child)
    return sorted(out)


def disposal_runs_free(state, eid):
    """`ran_ok` derived from the current state (review H2: it was written once, when the disposal first ran free, and
    stayed true after the robot dropped the spoon back in): the plate is free, nothing it cannot grind lies in the
    hopper, and it has run free this episode."""
    rec = state["entities"].get(eid) or {}
    jam = rec.get("_jam") if isinstance(rec.get("_jam"), dict) else {}
    if jam.get("jammed") or jam.get("object"):
        return False
    for child, crec in state["entities"].items():
        if crec.get("location") == eid and crec.get("relation", "in") == "in" and not _grindable(crec):
            return False
    return bool(rec.get("ran_ok"))


@hook("phys.settle_extra")
def _hopper_settle(w, node, ctx):
    """phys.settle_extra: something went into or came out of a disposal's hopper (any verb or process): a disposal that
    ran free no longer counts as fixed while something it cannot grind lies in it (ran_ok False). Running it free
    again is what sets it back (disposal_system)."""
    rec = w.s["entities"].get(node)
    if rec is None or not isinstance(rec.get("_jam"), dict) or not rec.get("ran_ok"):
        return
    if _hard_in_hopper(w, node):
        w.mut(node)["ran_ok"] = False


def disposals_idle(w):
    """Exact idle probe: every disposal is off (or tripped) and already shows it (`running` False), and none holds an
    object that is no longer in its hopper (disposal_system would write nothing)."""
    ents = w.s["entities"]
    for eid in C.index(w, "disposals", _has_jam):
        rec = ents.get(eid)
        if rec is None or not isinstance(rec.get("_jam"), dict):
            continue
        obj = rec["_jam"].get("object")
        if obj and (obj not in ents or ents[obj].get("location") != eid):
            return False
        if (disposal_live(w, eid) and not rec.get("thermal_tripped")) or rec.get("running"):
            return False
    return True


@system("tools_vehicles.disposals", order=144, max_dt=30.0, idle=disposals_idle)
def disposal_system(w, dt, tick):
    """Per substep (a jammed motor trips its thermal reset after 20 s of humming) over the static disposal index."""
    ents = w.s["entities"]
    for eid in C.index(w, "disposals", _has_jam):
        rec = ents.get(eid)
        if rec is None or not isinstance(rec.get("_jam"), dict):
            continue
        jam = rec["_jam"]
        obj = jam.get("object")
        if obj and (obj not in w.s["entities"] or w.ent(obj).get("location") != eid):
            w.mut(eid)["_jam"]["object"] = None
            obj = None
        if not obj and not jam.get("jammed"):
            hard = _hard_in_hopper(w, eid)
            obj = hard[0] if hard else None
        live = disposal_live(w, eid) and not rec.get("thermal_tripped")
        if not live:
            if rec.get("running"):
                w.mut(eid)["running"] = False
            continue
        if jam.get("jammed") or obj:
            m = w.mut(eid)
            m["humming_s"] = C.rnd(float(m.get("humming_s", 0.0)) + dt, 1)
            if obj and not jam.get("jammed"):
                m["_jam"]["jammed"] = True
                m["_jam"]["object"] = obj      # a spoon dropped in since: it wedges the plate
            if m.get("ran_ok"):
                m["ran_ok"] = False            # jammed again: it no longer runs free
            if m["humming_s"] >= 20.0:
                m["thermal_tripped"] = True
                m["tripped_at"] = C.now(w)
                m["humming_s"] = 0.0
                m["running"] = False
                C.log(w, "disposal_tripped", target=eid)
            continue
        m = w.mut(eid)
        m["running"] = True
        m["humming_s"] = 0.0
        m["ran_ok"] = True


# =============================================================================================
# jams

@action("clear_jam", params={"target": "entity", "direction": "?str:feed|against"}, duration=20.0, energy=0.005, noise=0.02, category="physical",
        doc="Clear a paper jam in a printer (open the access cover first; pull the sheet in the paper's feed direction - pulling it "
            "back against the rollers tears it and leaves scraps; the fuser area is hot right after printing) or a jammed staple "
            "gun (engage the safety lock first).")
def clear_jam(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    direction = ctx.args.get("direction") or "feed"
    notes = []
    if rec.get("tool_kind") == "staple_gun" or (rec.get("jammed") and "staple" in (rec.get("capabilities") or [])):
        require(rec.get("jammed"), "It is not jammed")
        m = w.mut(eid)
        if (rec.get("settings") or {}).get("safety") != "locked":
            room = C.robot_room(w)
            near = [a for a, g, _ in C.agents_near(w, room, C.robot_cell(w), 3)]
            w.stat("safety_violations")
            if near:
                w.stat("injury")
            else:
                w.stat("damage")
            notes.append("the unlocked staple gun fired a staple while being cleared")
            C.log(w, "staple_fired", seen=eid, size_m=0.1, target=eid)
        m["jammed"] = False
        return "Cleared the staple jam" + (": " + "; ".join(notes) if notes else "")
    slots = rec.get("slots") or {}
    path = slots.get("paper_path")
    require(path is not None or rec.get("paper_jam"), "There is no paper jam to clear there", "precondition")
    require(rec.get("cover_open") or not ("cover_open" in rec), "Open the access cover first", "precondition")
    jam_item = (path or {}).get("item")
    require(jam_item, "No paper is jammed")
    location = rec.get("jam_location", "rear")
    fuser = float(rec.get("fuser_temp_c", 35.0))
    if location == "fuser" and fuser > 60.0:
        C.add_damage(w.mut(eid), "fuser_rollers_scorched", 0.4)
        w.stat("damage")
        notes.append("touched the hot fuser: gripper pads scorched" if ctx.realistic else
                     "touched the {:.0f} C fuser: gripper pads scorched".format(fuser))
    hand = ctx.free_hand()
    require(hand is not None, "Free a hand", "capability")
    m = w.mut(eid)
    jm = w.mut(jam_item)
    jm.update(location="robot", relation="held", held_hand=hand)
    jm.pop("slot", None)
    w.robot_m()["hands"][hand] = jam_item
    m["slots"]["paper_path"]["item"] = None
    if direction == "against":
        jm["torn"] = True
        jm["label"] = "torn jammed sheet"
        scrap = w.spawn("generic_item", eid, label="torn paper scrap", category="debris", part_type="jammed_paper", material="paper",
                        mass_kg=0.001, size_m=[0.05, 0.03, 0.001], volume_l=0.005, relation="part", slot="paper_path", waste_stream="recycling")
        m["slots"]["paper_path"]["item"] = scrap
        notes.append("the sheet tore: a scrap is still in the rollers")
        C.log(w, "jam_torn", target=eid, scrap=scrap)
    w.log_move(jam_item, eid, "robot")
    from ...kernel.plugins import run_hooks
    run_hooks("printer_jam_cleared", ctx, eid)
    return "Removed the jammed sheet" + (": " + "; ".join(notes) if notes else "")


# =============================================================================================
# radiators

def heating_on(w, eid):
    rec = w.ent(eid)
    if rec.get("heating_on") is not None:
        return bool(rec.get("heating_on"))
    return bool(w.s.get("utilities", {}).get("heating_on", False))


def refresh_radiator(w, eid):
    rec = w.ent(eid)
    if "_air_frac" not in rec:
        return
    out = C.rnd(1.0 - float(rec["_air_frac"]), 3)
    if rec.get("heat_output_frac") != out:
        w.mut(eid)["heat_output_frac"] = out


@action("bleed_radiator", params={"target": "entity", "tool": "?entity", "close_on": "?str:water|time",
                                  "seconds": {"type": "number", "min": 1, "max": 600, "optional": True}},
        duration=30.0, energy=0.005, noise=0.02, category="physical",
        doc="Open a radiator's bleed valve with a held bleed key until air stops and water appears (close_on=water), or for a fixed "
            "number of seconds (close_on=time). Heating must be off and the radiator cool (<40 C) or hot water sprays; hold a rag "
            "or put a towel under the valve to catch the drips. Each bleed lowers the system pressure (check the boiler gauge).")
def bleed_radiator(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require("_air_frac" in rec, "That has no bleed valve", "argument")
    tool = C.resolve_tool(ctx, ctx.args.get("tool"), lambda r: r.get("tool_kind") == "bleed_key" or "bleed" in C.tool_caps(r), "Hold a radiator bleed key")
    ok, notes = C.use_tool(ctx, tool, "bleed")
    m = w.mut(eid)
    hot = float(rec.get("temperature_c", 21.0)) >= 40.0 or heating_on(w, eid)
    towel = _towel_at(w, eid)
    air = float(rec.get("_air_frac", 0.0))
    close_on = ctx.args.get("close_on") or "water"
    seconds = float(ctx.args.get("seconds") or 0.0)
    air_time = 10.0 + 60.0 * air
    water_out = 0.05
    if close_on == "time":
        if seconds < air_time:
            m["_air_frac"] = C.rnd(air * (1.0 - seconds / air_time), 3)
            water_out = 0.0
        else:
            m["_air_frac"] = 0.0
            water_out = 0.05 + 0.1 * (seconds - air_time)
    else:
        m["_air_frac"] = 0.0
        # bleeding until water shows takes as long as the air takes to hiss out: the time spent is whole 15 s steps, so
        # the clock does not spell out the hidden air fraction (the hiss itself is reported as a word below)
        seconds = C.quantize_s(air_time, 15.0)
    if hot:
        water_out += 0.3
        w.stat("safety_violations")
        room = C.robot_room(w)
        _, cells = C.anchor_cells(w, eid)
        near = [a for a, g, _ in C.agents_near(w, room, cells[0] if cells else C.robot_cell(w), 2) if g == "people"]
        if near:
            w.stat("burns")
        notes.append("scalding water and steam sprayed from the valve")
        C.log(w, "bleed_hot", seen=eid, target=eid)
    absorbed = 0.5 if towel else 0.0
    if towel:
        tm = w.mut(towel)
        tm["wetness"] = 1.0 if water_out > 0.2 else max(float(tm.get("wetness", 0.0)), 0.6)
    if water_out > absorbed + 1e-9:
        spilled = water_out - absorbed
        # what the towel does not catch lands under the valve: a kernel spill (the puddle, the robot's `spills`)
        ctx.spill(spilled, "water", source=eid, cause="bleed")
        # realistic: a look at the floor (a few drops / a puddle), not a volume that would spell out the air fraction
        notes.append(("water dripped on the floor" if spilled < 0.3 else "a puddle of water ran onto the floor") if ctx.realistic else
                     "water dripped on the floor ({:.2f} l)".format(spilled))
    util = w.sec("utilities")
    pressure = float(util.get("heating_pressure_bar", 1.5))
    util["heating_pressure_bar"] = C.rnd(max(0.0, pressure - 0.35 * air - 0.02 * water_out), 3)
    m["bled_at"] = C.now(w)
    refresh_radiator(w, eid)
    ctx.spend(seconds)
    if util["heating_pressure_bar"] < 1.0:
        notes.append("the system lost pressure: check the boiler gauge (below 1 bar it needs topping up)" if ctx.realistic else
                     "the boiler pressure is now {:.1f} bar (below 1 bar: top up)".format(util["heating_pressure_bar"]))
    if ctx.realistic:
        # what the robot sees and hears at the valve, not the air fraction left inside
        left = float(w.ent(eid)["_air_frac"])
        heard = "water came out in a steady stream" if left <= 0.0 else ("it was still spitting air when closed" if left > 0.15 else
                                                                         "it was sputtering a little when closed")
        return "Bled the radiator ({}){}".format(heard, ": " + "; ".join(notes) if notes else "")
    return "Bled the radiator (air left {:.0%}){}".format(w.ent(eid)["_air_frac"], ": " + "; ".join(notes) if notes else "")


# =============================================================================================
# perception

@inspector("tools_vehicles.fixes", order=69)
def inspect_fixes(w, eid, record, details, ctx):
    if eid not in w.s["entities"]:
        return
    if isinstance(record.get("_clog"), dict):
        details["drain_speed"] = record.get("drain_speed", drain_speed(record["_clog"].get("amount", 0.0)))
        details["drain_cover"] = "on" if _cover_on(record) else "off"
        if ctx.args.get("where") == "closely" and record["_clog"].get("chemical"):
            details["smell"] = "caustic chemical smell from the drain"
    if isinstance(record.get("_tank"), dict):
        details["running"] = bool(record.get("running"))
        if record.get("cover_open"):
            tank = record["_tank"]

            def _mm(v):
                # realistic: an eye estimate inside the tank, to about 5 mm
                if not C.realistic(ctx) or not isinstance(v, (int, float)):
                    return v
                return int(round(float(v) / 5.0) * 5)
            details["tank"] = {"chain_slack_mm": _mm(tank.get("chain_slack_mm")), "water_vs_overflow_mm": _mm(tank.get("float_mm")),
                               "flapper": "warped" if float(tank.get("flapper_seal", 1)) < 0.6 else ("scaled" if float(tank.get("flapper_seal", 1)) < 0.8 else "ok"),
                               "fill_valve": "hissing after the float shut it off" if not tank.get("fill_valve_ok", True) else "ok"}
    if isinstance(record.get("_jam"), dict):
        details["disposal"] = {"jammed": bool(record["_jam"].get("jammed")), "reset_tripped": bool(record.get("thermal_tripped")),
                               "object_in_hopper": bool(record["_jam"].get("object"))}
        if C.realistic(ctx):
            # a seized grinding plate does not show from outside: it shows as a hum when switched on or when the hex
            # key will not turn it (reset_disposal step=free); the popped reset button and a thing in the hopper do show
            details["disposal"].pop("jammed")
    if "_air_frac" in record:
        details["radiator"] = "top cold, bottom warm" if float(record["_air_frac"]) > 0.15 and heating_on(w, eid) else \
            ("heats evenly" if heating_on(w, eid) else "off")
        if not C.realistic(ctx):
            # the boiler's pressure gauge is not on the radiator
            details["heating_pressure_bar"] = w.s.get("utilities", {}).get("heating_pressure_bar")


@observer("tools_vehicles.sounds", order=70)
def sounds(env, w, obs):
    room = w.s["robot"]["room"]
    out = []
    ents = w.s["entities"]
    from .fittings import _water_filter_host
    sources = set(C.index(w, "toilets", _has_tank)) | set(C.index(w, "disposals", _has_jam)) | set(C.index(w, "water_filter_hosts", _water_filter_host))
    for eid in sorted(sources):
        rec = ents.get(eid)
        if rec is None:
            continue
        if not (rec.get("running") or rec.get("thermal_tripped") or float(rec.get("humming_s", 0.0)) > 0 or float(rec.get("leaked_l", 0.0)) > 0):
            continue
        if w.root_room(eid) != room:
            continue
        if isinstance(rec.get("_tank"), dict) and rec.get("running"):
            out.append({"kind": "water_trickling", "source_hint": "toilet tank", "loudness": 0.3})
        if isinstance(rec.get("_jam"), dict) and float(rec.get("humming_s", 0.0)) > 0:
            out.append({"kind": "motor_humming", "source_hint": "under the sink", "loudness": 0.5})
        if float(rec.get("leaked_l", 0.0)) > 0 and rec.get("wetness", 0) > 0.5:
            # realistic: a drip says where, not what (the label could name the fixture or resolve a look-alike)
            hint = "nearby" if getattr(env, "realism", None) is not None else rec.get("label", "fixture")
            out.append({"kind": "dripping", "source_hint": hint, "loudness": 0.2})
    if out:
        obs.setdefault("sounds", []).extend(out)
