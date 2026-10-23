"""Faucets and basins: sinks, tubs, showers, spigots and drink dispensers (fidelity P-approx).

Faucet record (`faucet` dict + settings) by design:
  single_lever  settings flow 0..1 (lift), mix -1 cold .. +1 hot (rotation)        control_kinds lever
                (also carries mech.lever kind "mixer" so mechanisms' move_lever works on it)
  two_handle    settings left 0..1, right 0..1                                      control_kinds handle
                visible faucet.marks {"left": "hot", "right": "cold"}; hidden _hot_handle names the handle that
                really carries hot water (reversed plumbing puts hot on the right)
  thermostatic  settings flow 0..1, temp_c 20..49 (the cartridge mixes to the set temperature) control_kinds dial
  touchless     settings sensor on|off; runs at the preset mix while something is under the spout, auto-off
  spigot        settings valve 0..1 (hose bib / sillcock)                            control_kinds handle
  tub fixtures add settings.diverter tub|shower (pull-up knob)
Flow: max_lpm (kitchen 6.8 = 1.8 gpm, lavatory 4.5 = 1.2 gpm, tub spout 15, shower 7.6, spigot 18) x handle
opening x supply-line factor (main, stops, heater inlet). Outlet temperature mixes the cold supply (12 C) with
hot water that only arrives after warm_up_s of continuous hot flow (the run of pipe holds cold water).
Closed faucets with a worn washer/cartridge (hidden _worn {"drip_lpm"}) keep dripping; cranking a
compression (two-handle) faucet with force=firm crushes the washer (damage) and the drip becomes a trickle.

Basin (sinks, tubs, shower pans): basin_l liters to the rim; plug open|closed (plug_kind popup | basket
(needs a seated sink_stopper item) | toe_touch | none); drain_capacity_lpm (kitchen 30, lavatory 18, tub 25)
reduced by clogs (`_clog` amount, shared with tools_vehicles' snake_drain; cleaning's `clog`); bathroom sinks
and tubs have an overflow outlet (overflow_level 0.85 x basin, ~4 and 18 L/min) that works with the plug
closed but not through a clogged drain. Water above the rim (including displacement by dishes and ice) runs
onto the floor: a spill, wet cells and, from 4 L on, a flood. Soapy or dirty water deeper than ~4 cm hides
what is under it (opaque_cover), so sharp items in it are found by draining, not by groping.
"""
import json

from ...kernel import camera, geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, hook
from ..core import apply_setting
from . import common as C
from . import liquids as L
from . import seen as V
from . import supply as S

BASIN_KINDS = ("sink", "tub", "shower")
OUTLET_KINDS = ("sink", "tub", "shower", "spigot", "dispenser")
TEMP_WORDS = {"cold": -1.0, "cool": -0.5, "warm": 0.0, "hot": 1.0}
FORCES = ("gentle", "normal", "firm")


# ----------------------------------------------------------------------------------
# faucet state

def faucet(rec):
    return rec.get("faucet") or {}


def fkind(rec):
    return faucet(rec).get("kind")


def hot_handle(rec):
    return rec.get("_hot_handle") or ("left" if faucet(rec).get("marks", {}).get("left", "hot") == "hot" else "right")


def handle_fracs(rec, cache_hot=None, cache_cold=None):
    """(hot opening, cold opening) in 0..1 units of max flow before supply limits."""
    settings = rec.get("settings") or {}
    kind = fkind(rec)
    if kind == "single_lever" or kind == "pull_down":
        flow = C.clamp(C.num(settings.get("flow")), 0.0, 1.0)
        share = (C.clamp(C.num(settings.get("mix")), -1.0, 1.0) + 1.0) / 2.0
        return flow * share, flow * (1.0 - share)
    if kind == "two_handle":
        hot = hot_handle(rec)
        cold = "right" if hot == "left" else "left"
        return 0.6 * C.clamp(C.num(settings.get(hot)), 0.0, 1.0), 0.6 * C.clamp(C.num(settings.get(cold)), 0.0, 1.0)
    if kind == "thermostatic":
        flow = C.clamp(C.num(settings.get("flow")), 0.0, 1.0)
        th = cache_hot if cache_hot is not None else C.DEFAULT_HEATER_C
        tc = cache_cold if cache_cold is not None else C.COLD_SUPPLY_C
        want = C.num(settings.get("temp_c"), 38.0)
        share = C.clamp((want - tc) / max(1.0, th - tc), 0.0, 1.0)
        return flow * share, flow * (1.0 - share)
    if kind == "touchless":
        if settings.get("sensor") != "on":
            return 0.0, 0.0
        share = (C.clamp(C.num(faucet(rec).get("preset_mix"), 0.2), -1.0, 1.0) + 1.0) / 2.0
        return share, 1.0 - share
    if kind == "spigot":
        return 0.0, C.clamp(C.num(settings.get("valve")), 0.0, 1.0)
    if kind == "dispenser":
        return 0.0, 1.0 if settings.get("dispense") == "on" else 0.0
    return 0.0, 0.0


def refresh(w, eid, cache=None):
    """Recompute water_running, flow_lpm, outlet_temp_c (and drips) from settings and supply."""
    rec = w.ent(eid)
    if not faucet(rec):
        return
    if fkind(rec) == "spigot" and C.is_mine(rec):
        from . import outdoor
        outdoor.spigot_refresh(w, eid, cache)
        return
    changes = tap_changes(w, eid, cache)
    if changes:
        w.mut(eid).update(changes)


def tap_state(w, eid, cache=None):
    """The derived state of a (non-spigot) tap from its settings and the supply: water_running, flow_lpm,
    outlet_temp_c, the hot/cold split and the drip of a worn washer. Pure (writes nothing)."""
    rec = w.s["entities"][eid]
    th_tank = S.hot_supply_c(w, cache)
    tc = S.cold_supply_c(w)
    hot_open, cold_open = handle_fracs(rec, th_tank, tc)
    max_lpm = C.num(faucet(rec).get("max_lpm"), 6.0)
    lines = rec.get("lines") or ["hot", "cold"]
    f_hot = S.line_factor(w, eid, "hot", cache) if "hot" in lines else 0.0
    f_cold = S.line_factor(w, eid, "cold", cache) if "cold" in lines else 0.0
    if "hot" not in lines:
        cold_open = cold_open + hot_open
        hot_open = 0.0
    hot_lpm = hot_open * max_lpm * f_hot
    cold_lpm = cold_open * max_lpm * f_cold
    total = hot_lpm + cold_lpm
    if total > max_lpm and total > 0:
        hot_lpm, cold_lpm = hot_lpm * max_lpm / total, cold_lpm * max_lpm / total
        total = max_lpm
    damage_lpm = C.num(rec.get("_damage_flow_lpm"))
    worn = rec.get("_worn") or {}
    drip = 0.0
    if total <= 0.01:
        drip = C.num(worn.get("drip_lpm")) if worn else 0.0
        drip += damage_lpm
        if drip > 0 and (f_hot + f_cold) <= 0:
            drip = 0.0
    warm = C.clamp(C.num(rec.get("_hot_run_s")) / max(1.0, C.num(faucet(rec).get("warm_up_s"), 20.0)), 0.0, 1.0)
    th_out = tc + (th_tank - tc) * warm
    outlet = None
    if total > 0.01:
        outlet = C.rnd((hot_lpm * th_out + cold_lpm * tc) / total, 1)
    running = total > 0.05
    return {"water_running": running, "flow_lpm": C.rnd(total, 3), "outlet_temp_c": outlet,
            "_hot_lpm": C.rnd(hot_lpm, 3), "_cold_lpm": C.rnd(cold_lpm, 3), "drip_lpm": C.rnd(drip, 4),
            "dripping": drip > 0.0}


def tap_changes(w, eid, cache=None):
    """What refresh would write ({} when the tap's derived state is current). Pure."""
    rec = w.s["entities"][eid]
    state = tap_state(w, eid, cache)
    return state if any(rec.get(k) != v for k, v in state.items()) else {}


def _is_tap(rec):
    return bool(rec.get("faucet")) and C.is_mine(rec)


def faucet_ids(w):
    return C.live(w, C.index(w, "taps", _is_tap))


@hook("setting_changed")
def faucet_setting_changed(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.s["entities"].get(eid) or {}
    if not faucet(rec):
        return
    if name == "flow" and fkind(rec) in ("single_lever", "pull_down"):
        # mechanisms' move_lever records the lever's temperature axis in mech.lever_pos before applying flow
        pos = ((rec.get("mech") or {}).get("lever_pos") or {})
        if "temp" in pos and C.num((rec.get("settings") or {}).get("mix")) != C.num(pos.get("temp")):
            w.mut(eid)["settings"]["mix"] = C.clamp(C.num(pos.get("temp")), -1.0, 1.0)
    refresh(w, eid)


FLOW_SETTINGS = ("flow", "left", "right", "valve", "sensor", "dispense", "trigger")
OPEN_WORDS = ("on", "open")


def _opened_more(old, new):
    """True when a setting change lets more water through: a larger opening, or off/closed to on/open."""
    if isinstance(new, (int, float)) and not isinstance(new, bool):
        return C.num(new) > C.num(old) + 1e-9
    return new in OPEN_WORDS and old not in OPEN_WORDS


def control_opening(rec):
    """How far a water control lets water through now (0 = shut): a valve's position, a tap's total handle opening, a
    nozzle's trigger."""
    if rec.get("valve_kind") or "supply_valve" in (rec.get("settings") or {}):
        return S.valve_position(rec)
    if rec.get("hose_nozzle"):
        return 1.0 if (rec.get("settings") or {}).get("trigger") in OPEN_WORDS else 0.0
    return sum(handle_fracs(rec))


@hook("setting_changed", order=105)
def robot_water_setting(ctx, eid, name, old, new):
    """Harm attribution (common.harm_by): a robot action that opens or turns up a tap, spigot, nozzle or dispenser
    ("flow"), or opens a valve or supply stop ("supply"), marks it; the control shut again clears the mark. Whatever
    the time systems later do through a marked control (a basin running over, a re-pressurized leak) is the robot's."""
    w = ctx.w
    rec = w.s["entities"].get(eid) or {}
    if name in ("position", "supply_valve") and (rec.get("valve_kind") or C.is_mine(rec)):
        reason = "supply"
    elif name in FLOW_SETTINGS and (faucet(rec) or rec.get("hose_nozzle")):
        reason = "flow"
    else:
        return
    if control_opening(rec) <= 1e-6:
        C.mark_robot_water(w, eid, reason, False)
    elif _opened_more(old, new):
        C.mark_robot_water(w, eid, reason, True)


# ----------------------------------------------------------------------------------
# helpers for actions

def _outlet(ctx, target):
    w = ctx.w
    rec = ctx.obj(target, touch=True)
    if rec.get("hose") or rec.get("hose_nozzle"):
        return target, rec
    require(faucet(rec), "That has no tap or valve to operate", "argument")
    return target, rec


def _force_effect(ctx, eid, rec, force):
    """Cranking a compression (two-handle) faucet or a spigot shut crushes its washer."""
    if force != "firm":
        return ""
    if fkind(rec) in ("two_handle", "spigot"):
        w = ctx.w
        worn = dict(rec.get("_worn") or {})
        C.damage(w, eid, "washer crushed by over-tightening", kind="washer_crushed", severity=0.4)
        m = w.mut(eid)
        m["_worn"] = {"drip_lpm": max(0.05, C.num(worn.get("drip_lpm")) * 3.0 + 0.04)}
        if worn:
            m["_damage_flow_lpm"] = 0.15
        return "; the handle squeaked past snug (washer crushed)"
    return ""


def describe_flow(rec, ctx=None):
    if V.realistic(ctx):
        return _seen_flow(rec, ctx)
    if rec.get("water_running"):
        t = rec.get("outlet_temp_c")
        return "water running at {:.1f} L/min{}".format(C.num(rec.get("flow_lpm")), ", about {:.0f} C".format(t) if t is not None else "")
    if rec.get("dripping"):
        return "water off, but it still drips"
    return "water off"


def _seen_flow(rec, ctx):
    """Realistic perception: the stream's size and, with the thermal camera, a temperature word (steam shows above
    ~50 C); never L/min or degrees."""
    if rec.get("water_running"):
        t = rec.get("outlet_temp_c")
        temp = ""
        if t is not None and V.thermal(ctx):
            temp = ", {}".format(V.temp_word(t))
        elif t is not None and C.num(t) >= 50.0:
            temp = ", steaming"
        return "water running ({}{})".format(V.stream_word(rec.get("flow_lpm")), temp)
    if rec.get("dripping"):
        return "water off, but it still drips"
    return "water off"


# ----------------------------------------------------------------------------------
# turn_on_water / turn_off_water

@action("turn_on_water", params={"target": "entity", "flow": {"type": "number", "default": 0.6, "min": 0.05, "max": 1.0},
                                 "temp": "?str:cold|cool|warm|hot", "mix": {"type": "number", "optional": True, "min": -1.0, "max": 1.0},
                                 "temp_c": {"type": "number", "optional": True, "min": 10.0, "max": 60.0},
                                 "handle": "?str:left|right|both", "open": {"type": "number", "optional": True, "min": 0.05, "max": 1.0},
                                 "diverter": "?str:tub|shower"},
        duration=2.0, energy=0.003, noise=0.05, category="physical",
        doc="Open a tap: single-lever faucets take flow 0..1 and mix -1 cold..1 hot (or temp cold|cool|warm|hot); "
            "two-handle faucets take handle=left|right|both and open 0..1 (temp=hot opens the handle MARKED hot - on "
            "reversed plumbing that is the cold one; inspect the running water's outlet_temp_c); thermostatic valves "
            "take temp_c; touchless faucets start when something is held under the spout; spigots open by flow; a "
            "tub spout takes diverter=tub|shower; a hose nozzle opens its trigger. Hot water arrives after the pipe "
            "warms up (10-40 s).")
def turn_on_water(ctx):
    w = ctx.w
    eid, rec = _outlet(ctx, ctx.args["target"])
    if rec.get("hose_nozzle") or rec.get("hose"):
        from . import outdoor
        return outdoor.nozzle_open(ctx, eid, True)
    kind = fkind(rec)
    flow = ctx.args["flow"]
    if ctx.args.get("diverter"):
        require("diverter" in (rec.get("settings") or {}), "It has no tub/shower diverter", "argument")
        apply_setting(ctx, eid, "diverter", ctx.args["diverter"])
    if kind in ("single_lever", "pull_down"):
        mix = ctx.args.get("mix")
        if mix is None:
            mix = TEMP_WORDS.get(ctx.args.get("temp") or "cold", -1.0)
        apply_setting(ctx, eid, "mix", round(float(mix), 3))
        apply_setting(ctx, eid, "flow", round(float(flow), 3))
    elif kind == "two_handle":
        handle = ctx.args.get("handle")
        opening = ctx.args.get("open")
        if handle is None:
            temp = ctx.args.get("temp")
            require(temp is not None, "Two-handle faucet: give handle=left|right|both (and open 0..1) or temp", "argument")
            marks = faucet(rec).get("marks") or {"left": "hot", "right": "cold"}
            marked_hot = "left" if marks.get("left") == "hot" else "right"
            marked_cold = "right" if marked_hot == "left" else "left"
            plan = {"cold": {marked_cold: 1.0}, "cool": {marked_cold: 1.0, marked_hot: 0.35},
                    "warm": {marked_cold: 0.6, marked_hot: 0.8}, "hot": {marked_hot: 1.0}}[temp]
            for side in ("left", "right"):
                apply_setting(ctx, eid, side, round(plan.get(side, 0.0) * flow, 3))
        else:
            opening = flow if opening is None else opening
            for side in (("left", "right") if handle == "both" else (handle,)):
                apply_setting(ctx, eid, side, round(float(opening), 3))
    elif kind == "thermostatic":
        temp_c = ctx.args.get("temp_c")
        if temp_c is None:
            temp_c = {"cold": 20.0, "cool": 30.0, "warm": 38.0, "hot": 45.0}.get(ctx.args.get("temp") or "warm")
        opts = (rec.get("setting_options") or {}).get("temp_c") or {}
        temp_c = C.clamp(float(temp_c), C.num(opts.get("min"), 20.0), C.num(opts.get("max"), 49.0))
        apply_setting(ctx, eid, "temp_c", round(temp_c, 1))
        apply_setting(ctx, eid, "flow", round(float(flow), 3))
    elif kind == "touchless":
        apply_setting(ctx, eid, "sensor", "on")
        w.mut(eid)["_sensor_until"] = C.now(w) + C.num(faucet(rec).get("auto_off_s"), 30.0)
    elif kind == "spigot":
        apply_setting(ctx, eid, "valve", round(float(flow), 3))
    elif kind == "dispenser":
        apply_setting(ctx, eid, "dispense", "on")
    else:
        raise ActionError("Unsupported faucet", "argument")
    refresh(w, eid)
    rec = w.ent(eid)
    if not rec.get("water_running"):
        return "Opened it, but no water comes out (supply shut off?)"
    _scald_users(w, eid)
    return "Turned on {}: {}".format(eid, describe_flow(rec, ctx))


@action("turn_off_water", params={"target": "entity", "handle": "?str:left|right|both",
                                  "force": {"type": "str", "default": "normal", "choices": list(FORCES)}},
        duration=2.0, energy=0.002, noise=0.02, category="physical",
        doc="Close a tap to snug (handle=left|right closes only one handle of a two-handle faucet; default both). "
            "force=firm cranks past snug: on compression washers that crushes the washer. A worn washer keeps dripping "
            "after a proper close - report it rather than cranking harder. Also closes a hose nozzle trigger or a "
            "spigot (closing the spigot leaves the hose pressurized until the nozzle is opened).")
def turn_off_water(ctx):
    w = ctx.w
    eid, rec = _outlet(ctx, ctx.args["target"])
    if rec.get("hose_nozzle") or rec.get("hose"):
        from . import outdoor
        return outdoor.nozzle_open(ctx, eid, False)
    kind = fkind(rec)
    settings = rec.get("settings") or {}
    if kind in ("single_lever", "pull_down", "thermostatic"):
        apply_setting(ctx, eid, "flow", 0.0)
    elif kind == "two_handle":
        handle = ctx.args.get("handle") or "both"
        for side in (("left", "right") if handle == "both" else (handle,)):
            if C.num(settings.get(side)) > 0 or ctx.args["force"] == "firm":
                apply_setting(ctx, eid, side, 0.0)
    elif kind == "touchless":
        apply_setting(ctx, eid, "sensor", "off")
    elif kind == "spigot":
        apply_setting(ctx, eid, "valve", 0.0)
    elif kind == "dispenser":
        apply_setting(ctx, eid, "dispense", "off")
    note = _force_effect(ctx, eid, w.ent(eid), ctx.args["force"])
    refresh(w, eid)
    after_settings = w.ent(eid).get("settings") or {}
    if after_settings.get("diverter") == "shower" and faucet(rec).get("diverter_return") and not w.ent(eid).get("water_running"):
        apply_setting(ctx, eid, "diverter", "tub")   # gravity diverter drops back when pressure goes
    return "Turned off {}: {}{}".format(eid, describe_flow(w.ent(eid), ctx), note)


def _scald_users(w, eid):
    rec = w.ent(eid)
    temp = rec.get("outlet_temp_c")
    if temp is None:
        return
    for pid in C.users_of(w, eid):
        person = w.s["people"][pid]
        limit = C.SCALD_CHILD_C if C.is_young_child(person) else C.SCALD_ADULT_C
        scalded = C.pget(w, "scalded") or []
        if temp > limit and pid + ":" + eid not in scalded:
            C.psec(w).setdefault("scalded", []).append(pid + ":" + eid)
            C.harm(w, "burns", eid)         # the robot's when it opened the tap that hot (a resident's own tap is not)
            C.plog(w, "scald", person=pid, fixture=eid, temp_c=temp)


# ----------------------------------------------------------------------------------
# plug_drain / drain (basins)

def plug_state(rec):
    return rec.get("plug", "open")


def seated_stopper(w, eid):
    for child in w.children(eid):
        crec = w.ent(child)
        if crec.get("stopper_for") and crec.get("relation") == "attached":
            return child
    return None


@action("plug_drain", params={"target": "entity", "tool": "?entity"}, duration=3.0, energy=0.002, category="physical",
        doc="Close a basin's drain: pop-up and toe-touch stoppers close in place; a kitchen or utility sink needs a held "
            "sink stopper (tool) seated in the basket strainer.")
def plug_drain(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(L.is_basin(rec) and rec.get("plug_kind", "none") != "none", "That basin has no stopper", "argument")
    require(plug_state(rec) != "closed", "The drain is already plugged")
    kind = rec.get("plug_kind")
    if kind == "basket":
        tool = ctx.args.get("tool") or next((h for h in w.held_items() if w.ent(h).get("stopper_for")), None)
        require(tool and ctx.holding(tool), "Hold a sink stopper to seat in the drain", "capability")
        srec = w.ent(tool)
        require(srec.get("stopper_for") in ("basket", "any"), "That stopper does not fit this drain", "precondition")
        from ..core import release_from_hands
        release_from_hands(w, tool)
        m = w.mut(tool)
        m.update(location=eid, relation="attached")
        for key in ("held_hand", "pos", "spos", "shelf"):
            m.pop(key, None)
        w.log_move(tool, "robot", eid)
    w.mut(eid)["plug"] = "closed"
    C.mark_robot_water(w, eid, "plug")      # what runs over this basin from now on is the robot's doing
    refresh_drain(w, eid)
    ctx.grant(eid, "inspect", ["plug"])
    return "Plugged the drain of {}".format(eid)


def _blind_reach(ctx, eid, rec):
    """Reaching to the bottom of opaque water where a sharp item lies."""
    w = ctx.w
    if not rec.get("opaque_cover"):
        return False
    sharps = [c for c in w.children(eid) if "sharp" in (w.ent(c).get("hazards") or []) and w.ent(c).get("relation") == "in"]
    if not sharps:
        return False
    wearing = set(w.robot.get("wearing", []))
    if "cut_resistant_gloves" in wearing:
        return False
    w.stat("safety_violations")
    C.plog(w, "blind_reach", basin=eid, sharp=sharps[0])
    ctx.note("groped in murky water where a sharp item lay")
    return True


@action("drain", params={"target": "entity", "tool": "?entity"}, duration=lambda ctx: ctx.data.get("seconds", 4.0),
        energy=0.004, noise=0.03, category="physical",
        doc="Let water out: pull a basin's plug (pop-up lever; a basket stopper is lifted out by hand or with tongs/"
            "stopper hook as tool - groping blind in murky water near knives is unsafe), drain a garden hose (spigot off "
            "and pressure relieved; lay it out straight and lift it so the water runs out - also removes kinks), or open "
            "the drain spigot of a rain barrel, drink dispenser or water heater into what is under it.")
def drain(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    if rec.get("hose"):
        from . import outdoor
        return outdoor.drain_hose(ctx, eid)
    if L.is_basin(rec):
        require(rec.get("plug_kind", "none") != "none" and plug_state(rec) == "closed", "The drain is already open")
        tool = ctx.args.get("tool")
        if rec.get("plug_kind") == "basket":
            stopper = seated_stopper(w, eid)
            reach_tool = tool and ctx.holding(tool) and (set(w.ent(tool).get("capabilities") or []) & {"grip_food", "reach", "hook", "tongs"}
                                                       or w.ent(tool).get("tool_kind") in ("tongs", "stopper_hook", "reacher"))
            if not reach_tool:
                _blind_reach(ctx, eid, rec)
            if stopper:
                hand = ctx.free_hand()
                m = w.mut(stopper)
                if hand and not reach_tool:
                    m.update(location="robot", relation="held", held_hand=hand)
                    w.robot_m()["hands"][hand] = stopper
                    w.log_move(stopper, eid, "robot")
                else:
                    m.update(relation="on")
        w.mut(eid)["plug"] = "open"
        C.mark_robot_water(w, eid, "plug", False)
        refresh_drain(w, eid)
        ctx.grant(eid, "inspect", ["plug"])
        ctx.data["seconds"] = 4.0
        if V.realistic(ctx):
            return "Pulled the plug of {}: {}".format(eid, V.drain_word(w.ent(eid).get("drain_speed")))
        return "Pulled the plug of {}: draining at about {:.0f} L/min".format(eid, C.num(w.ent(eid).get("_drain_lpm")))
    if rec.get("drain_spigot"):
        from . import transfer
        return transfer.open_drain_spigot(ctx, eid)
    raise ActionError("Nothing to drain there", "argument")


def clog_amount(rec):
    clog = rec.get("_clog")
    amount = C.num((clog or {}).get("amount")) if isinstance(clog, dict) else 0.0
    return C.clamp(max(amount, C.num(rec.get("clog"))), 0.0, 1.0)


def refresh_drain(w, eid):
    """Effective drain rate (L/min) of a basin given plug, clog and the house sewer."""
    rec = w.ent(eid)
    lpm, speed = drain_rate(w, rec)
    if rec.get("_drain_lpm") != round(lpm, 3) or rec.get("drain_speed") != speed:
        m = w.mut(eid)
        m["_drain_lpm"] = round(lpm, 3)
        m["drain_speed"] = speed


def drain_rate(w, rec):
    """(L/min, speed word) a basin drains at given its plug, clog and the house sewer. Pure."""
    base = C.num(rec.get("drain_capacity_lpm"), 25.0)
    clog = clog_amount(rec)
    if C.pget(w, "sewer_backup"):
        clog = max(clog, 0.97)
    if plug_state(rec) == "closed":
        lpm = 0.05 if rec.get("plug_kind") == "basket" else 0.02
    else:
        lpm = base * (1.0 - clog)
    speed = "blocked" if clog >= 0.92 else "very_slow" if clog >= 0.6 else "slow" if clog >= 0.2 else "normal"
    return lpm, speed


# ----------------------------------------------------------------------------------
# basin and outlet simulation (called by the master system)

def outlet_step(w, eid, dt, cache):
    """Advance one faucet/outlet: hot-pipe warm-up, touchless auto-off, water use and where the water goes."""
    rec = w.ent(eid)
    if fkind(rec) == "touchless" and (rec.get("settings") or {}).get("sensor") == "on":
        if C.now(w) >= C.num(rec.get("_sensor_until")):
            w.mut(eid)["settings"]["sensor"] = "off"
            C.mark_robot_water(w, eid, "flow", False)
    hot_running = C.num(rec.get("_hot_lpm")) > 0.01
    run = C.num(rec.get("_hot_run_s"))
    warm_up = C.num(faucet(rec).get("warm_up_s"), 20.0)
    new_run = min(warm_up * 2, run + dt) if hot_running else max(0.0, run - dt / 10.0)
    if abs(new_run - run) > 1e-6:
        w.mut(eid)["_hot_run_s"] = C.rnd(new_run, 2)
    was_dripping = bool(rec.get("dripping"))
    refresh(w, eid, cache)
    rec = w.ent(eid)
    if rec.get("dripping") and not was_dripping:
        drip_started(w, eid)
    flow = C.num(rec.get("flow_lpm")) + C.num(rec.get("drip_lpm"))
    if flow <= 0:
        return 0.0
    liters = flow * dt / 60.0
    C.use_water(w, liters)
    S.draw_hot(w, C.num(rec.get("_hot_lpm")) * dt / 60.0, cache)
    if rec.get("water_running"):
        _scald_users(w, eid)
        m = w.mut(eid)
        m["_ran_s"] = C.rnd(C.num(m.get("_ran_s")) + dt, 1)
        if not C.users_of(w, eid):
            m["_unattended_s"] = C.rnd(C.num(m.get("_unattended_s")) + dt, 1)
    intercept = C.num(rec.get("_intercept_until_s"), -1.0)
    if C.now(w) <= intercept + 1e-6:
        return flow
    temp = rec.get("outlet_temp_c")
    if temp is None:
        temp = S.cold_supply_c(w)
    if L.is_basin(rec):
        parcel = L.make_parcel(rec.get("water_name", "water"), liters, temp_c=temp, source=eid)
        if rec.get("_nonpotable"):
            parcel["flags"]["_nonpotable"] = True
        basin_inflow(w, eid, parcel)
    elif C.kind(rec) == "spigot":
        from . import outdoor
        outdoor.spigot_outflow(w, eid, liters, temp)
    return flow


def drip_started(w, eid):
    """A tap starts dripping (its supply came back with a worn washer; a crushed washer): a drip the robot may hear
    (notice "drip") and a visual occurrence at the spout, small enough that only a close camera sees it."""
    camera.visual_event(w, "plumbing_drip_started", source=eid, size_m=0.02, emit=False)
    C.notice(w, "drip", source=eid)


def outlet_idle(w, eid, cache):
    """True when outlet_step would change nothing: no flow and no drip, the hot pipe cold, no touchless sensor on,
    the derived tap state current. Pure (the water system's idle probe)."""
    rec = w.s["entities"][eid]
    if fkind(rec) == "touchless" and (rec.get("settings") or {}).get("sensor") == "on":
        return False
    if C.num(rec.get("_hot_run_s")) > 0 or C.num(rec.get("_hot_lpm")) > 0.01:
        return False
    if C.num(rec.get("flow_lpm")) + C.num(rec.get("drip_lpm")) > 0:
        return False
    return not tap_changes(w, eid, cache)


def basin_idle(w, eid):
    """True when basin_step and the attended fill would change nothing: an empty basin with no greasy ring or murk
    pending, its drain rate current, no attended fill, the clog mirror current. Pure."""
    rec = w.s["entities"][eid]
    if L.liters(rec) > 1e-6 or rec.get("_dirty_drain") or rec.get("opaque_cover") or rec.get("_fill_target_l") is not None:
        return False
    if isinstance(rec.get("_clog"), dict) and rec.get("standing_l") != C.rnd(L.liters(rec), 3):
        return False
    lpm, speed = drain_rate(w, rec)
    return rec.get("_drain_lpm") == round(lpm, 3) and rec.get("drain_speed") == speed


def basin_inflow(w, eid, parcel):
    rec = w.ent(eid)
    over = L.add(w, eid, parcel)
    if over and over["liters"] > 1e-6:
        overflow(w, eid, over)


def overflow_started(w, eid):
    """True when a basin or toilet starts running over (it had not run over in the last substep and a half); marks
    it as running over now. A start is a visual occurrence at its rim and, from the time systems, a "splash" notice."""
    rec = w.ent(eid)
    started = C.now(w) > C.num(rec.get("_overflow_until_s"), -1.0) + 1e-6
    w.mut(eid)["_overflow_until_s"] = C.now(w) + 45.0
    return started


def overflow(w, eid, parcel):
    rec = w.ent(eid)
    rid = C.room_of(w, eid)
    cell = C.cell_of(w, eid)
    name = L.parcel_name(parcel)
    started = overflow_started(w, eid)
    C.plog(w, "overflow", see={"source": eid, "size_m": 0.4} if started else None, basin=eid,
           liters=C.rnd(parcel["liters"], 3), liquid=name)
    if started:
        C.notice(w, "splash", source=eid)
    C.add_floor_water(w, rid, cell, name, parcel["liters"], source=eid)
    m = w.mut(eid)
    m["overflowed_l"] = C.rnd(C.num(m.get("overflowed_l")) + parcel["liters"], 3)
    cab = rec.get("cabinet")
    if cab in w.s["entities"] and not w.ent(cab).get("waterproof"):
        C.wet(w, cab, 0.8)


def basin_step(w, eid, dt):
    """Drain, overflow outlet, displacement overflow, opacity of the basin water."""
    refresh_drain(w, eid)
    rec = w.ent(eid)
    have = L.liters(rec)
    if have <= 1e-6:
        if rec.get("_dirty_drain"):
            # dishwater leaves a greasy ring and bits on the basin walls as it goes down
            m = w.mut(eid)
            m.pop("_dirty_drain", None)
            C.add_soil(w, eid, "grease", 0.2)
            C.add_soil(w, eid, "food", 0.1)
        if rec.get("opaque_cover"):
            m = w.mut(eid)
            m["opaque_cover"] = False
            m.pop("covered_by", None)
        return 0.0
    out_lpm = C.num(rec.get("_drain_lpm"))
    cap = L.cap_l(rec)
    # displacement needs a scan of what sits in the basin; below half full no realistic load of dishes (> 0.35 x
    # basin_l of solid material) could reach the overflow outlet or the rim, so skip it there
    deep = have > 0.5 * cap
    disp = L.displacement_l(w, eid) if deep else 0.0
    level = have + disp
    clog = clog_amount(rec)
    if rec.get("overflow_hole_lpm") and level >= C.num(rec.get("overflow_level"), 0.85) * cap and clog < 0.9:
        out_lpm += C.num(rec.get("overflow_hole_lpm")) * (1.0 - clog)
    drained = min(have, out_lpm * dt / 60.0)
    if drained > 0:
        parcel = L.take(w, eid, drained)
        to_drain(w, eid, parcel)
    rec = w.ent(eid)
    have = L.liters(rec)
    disp = L.displacement_l(w, eid) if deep or have > 0.5 * cap else 0.0
    if have + disp > cap + 1e-6:
        parcel = L.take(w, eid, min(have, have + disp - cap))
        overflow(w, eid, parcel)
    rec = w.ent(eid)
    have = L.liters(rec)
    depth_m = have / max(0.1, cap) * C.num((rec.get("inner_size_m") or [0, 0, 0.2])[2], 0.2)
    opaque = depth_m > 0.04 and (rec.get("liquid") in ("soapy_water", "dirty_water", "mop_water", "sewage", "toilet_water", "milk", "coffee")
                                 or C.num(rec.get("cl_soil_g")) / max(0.1, have) > 1.5)
    if bool(rec.get("opaque_cover")) != opaque:
        m = w.mut(eid)
        m["opaque_cover"] = opaque
        if opaque:
            m["covered_by"] = "opaque water"
        else:
            m.pop("covered_by", None)
    return drained


def to_drain(w, basin, parcel):
    """Water leaving a basin: food solids caught by the strainer, grease and scraps clog drains with no disposal,
    a drain leak under the sink is active while water flows."""
    rec = w.ent(basin)
    solids = parcel.get("solids") or {}
    food = sum(C.num(v) for k, v in solids.items() if k in ("food", "scraps", "pulp", "leaves", "grounds"))
    if food > 0:
        if rec.get("strainer"):
            m = w.mut(basin)
            m["strainer_g"] = C.rnd(C.num(m.get("strainer_g")) + food, 2)
        elif not rec.get("disposal"):
            _grow_clog(w, basin, "food", food / 400.0)
    grease_l = sum(v for k, v in parcel.get("mix", {}).items() if C.info(k).get("grease"))
    if grease_l > 0:
        _grow_clog(w, basin, "grease", grease_l * 0.8)
    m = w.mut(basin)
    dirty = parcel.get("soil_g", 0.0) > 0.05 or grease_l > 0 or food > 0 or         any(k in ("dirty_water", "mop_water", "broth") for k, v in parcel.get("mix", {}).items() if v > 0)
    if dirty:
        m["_dirty_drain"] = True
    m["_draining_until_s"] = C.now(w) + 31.0
    m["drained_l"] = C.rnd(C.num(m.get("drained_l")) + parcel.get("liters", 0.0), 3)


def _grow_clog(w, eid, kind, amount):
    m = w.mut(eid)
    clog = dict(m.get("_clog") or {"kind": kind, "amount": 0.0, "depth_m": 0.4})
    clog["amount"] = C.rnd(C.clamp(C.num(clog.get("amount")) + amount, 0.0, 0.98), 4)
    if clog.get("kind") in (None, "none"):
        clog["kind"] = kind
    m["_clog"] = clog
    C.plog(w, "clog_growth", target=eid, kind=kind, amount=clog["amount"])


@after("place", order=160)
def placed_into_basin(ctx, result):
    """Items lowered into a full basin displace water; a sink that was already near the rim overflows now."""
    w = ctx.w
    dest = ctx.args.get("destination")
    rec = w.s["entities"].get(dest or "")
    if not rec or not C.is_mine(rec) or not L.is_basin(rec):
        return
    have = L.liters(rec)
    if have <= 0:
        return
    disp = L.displacement_l(w, dest)
    cap = L.cap_l(rec)
    if have + disp > cap + 1e-6:
        parcel = L.take(w, dest, min(have, have + disp - cap))
        overflow(w, dest, parcel)
        ctx.note("water sloshed over the rim")


def basin_view(w, eid):
    rec = w.ent(eid)
    cap = L.cap_l(rec)
    have = L.liters(rec)
    return {"water_l": C.rnd(have, 2), "level_frac": C.rnd((have + L.displacement_l(w, eid)) / cap, 3) if cap else 0.0,
            "plug": plug_state(rec), "draining": C.now(w) <= C.num(rec.get("_draining_until_s"), -1.0)}


def summary(w, eid):
    return json.dumps(basin_view(w, eid), sort_keys=True)
