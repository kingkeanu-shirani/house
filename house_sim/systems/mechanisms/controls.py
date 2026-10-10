"""Physical controls: turn (knobs, dials, valves, cranks, lock parts, lids, combinations, pages),
press / hold_press (data-defined button panels, garage buttons, flip-top caps, hidden catches,
press-fit lids), touch_select (touchscreen UI state machines) and move_lever (mixer faucets,
latching toaster levers, spring flush levers, recliners, louvers and generic levers).

Settings change only through core apply_setting, so the owning domain's
@hook("setting_changed") reacts (washer program, thermostat setpoint, oven temperature...).

Data contracts on a device record (all optional; see docs/domains/mechanisms.md):

* ``control_kinds`` {setting: "knob"|"dial"|"handle"|"lever"|"button"|"touchscreen"} (convention)
* ``mech.dials`` {setting: {"unidirectional": bool, "push_to_turn": bool, "units": "F"|"C",
  "step": 25, "end_stop": true}}
* ``mech.rotary`` {"setting": name, "per_turn": 0.25, "cw": "decrease"|"increase", "max_turns_past_stop": 0.25}
* ``mech.gas`` {"setting": "flame", "off": "off", "lit": false, "hold_needed_s": 2.0, "igniter_ok": true}
* ``panel`` {"buttons": {name: {"effect": E, "holds": [[min_s, E2], ...]}}, "hold_to_start_s": 0,
  "child_lock": false, "lock_button": "stop", "factory_settings": {...}}
  Effects: start, pause, cancel, resume, toggle_power, on, off, set:<setting>=<value>,
  cycle:<setting>, up:<setting>, down:<setting>, toggle_child_lock, pair, reboot, factory_reset,
  pulse, light, garage, none.
* ``ui`` (touchscreen) {"screen": "home", "screens": {name: {"title": str, "items": [{"label": str,
  "goto": screen} | {"label": str, "stage": [setting, value]} | {"label": str, "save": true,
  "confirm": {"text": str, "options": [{"label": str, "scope": "day"|"all"}]}} | {"label": "Back",
  "back": true} | {"label": str, "cancel": true}], "swipe_left": screen, "swipe_right": screen}},
  "staged": {}, "dialog": null, "timeout_s": 45}
* ``mech.lever`` {"kind": "mixer"|"latching"|"spring"|"recliner"|"setting", ...}
"""
import copy
import json

from ...kernel import physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import ACTIONS, run_hooks
from ..core import apply_setting
from . import common as C
from . import lids as L

LOCK_PARTS = {"deadbolt", "knob_lock", "knob", "thumb_turn", "sash_lock", "lever_lock", "smart_lock", "all"}


# ----------------------------------------------------------------------------------
# turn

def _options(rec, setting):
    return (rec.get("setting_options") or {}).get(setting)


def _dial_spec(rec, setting):
    return dict((C.mget(rec, "dials") or {}).get(setting) or {})


def knob_turn(ctx, eid, setting, value, direction, push, hold_s, force):
    w = ctx.w
    rec = w.ent(eid)
    settings = rec.get("settings") or {}
    require(setting in settings, "No control named {} here (controls: {})".format(setting, ", ".join(sorted(settings)) or "none"), "argument")
    kind = (rec.get("control_kinds") or {}).get(setting)
    require(kind in ("knob", "dial", "handle", "crank", "valve", None), "Use the {} for {}".format(kind, setting), "capability")
    spec = _dial_spec(rec, setting)
    if spec.get("push_to_turn") and not push:
        raise ActionError("The knob will not turn unless it is pushed in first (push=true)", "precondition")
    options = _options(rec, setting)
    current = settings.get(setting)
    if spec.get("unidirectional"):
        if isinstance(options, list) and current in options and value in options:
            i, j = options.index(current), options.index(value)
            backwards = j < i
        else:
            backwards = C.num(value) < C.num(current)
        if direction == "ccw":
            C.mark_damage(w, eid, "timer mechanism stripped by turning it backwards")
            return "It resisted, then something clicked inside: it only turns clockwise (the timer is damaged)"
        if backwards and not spec.get("wraps", False):
            raise ActionError("It only turns clockwise; it cannot go back to {} from {}".format(value, current), "precondition")
    if isinstance(options, dict) and isinstance(value, (int, float)) and not isinstance(value, bool):
        step = C.num(options.get("step") or spec.get("step"), 0.0)
        if step > 0:
            resolution = step / 5.0
            value = round(round(float(value) / resolution) * resolution, 3)
    previous = apply_setting(ctx, eid, setting, value)
    gas = C.mget(rec, "gas")
    note = ""
    if isinstance(gas, dict) and gas.get("setting", setting) == setting:
        note = _gas_after_turn(ctx, eid, setting, value, hold_s)
    _refresh_display(w, eid)
    return "Turned {} from {} to {}{}".format(setting, previous, value, note)


def _gas_after_turn(ctx, eid, setting, value, hold_s):
    w = ctx.w
    rec = w.ent(eid)
    gas = dict(C.mget(rec, "gas") or {})
    rm, mech = C.mut_mech(w, eid)
    off = gas.get("off", "off")
    utilities = w.s.get("utilities", {})
    supply = utilities.get("gas", True) and utilities.get("main_gas_open", True) and not gas.get("valve_closed")
    if value == off:
        gas.update(flow=False, lit=False, unlit_s=0.0)
        mech["gas"] = gas
        return " (gas off)"
    gas["flow"] = bool(supply)
    if not gas.get("lit"):
        ok = supply and gas.get("igniter_ok", True) and hold_s >= C.num(gas.get("hold_needed_s"), 2.0)
        gas["lit"] = bool(ok)
        if ok:
            gas["unlit_s"] = 0.0
    ctx.spend(hold_s)
    mech["gas"] = gas
    if gas["lit"]:
        return " (the burner lit with a soft whoomp)"
    return " (clicking, but no flame: gas is flowing unlit)" if supply else " (no gas supply)"


def rotary_turn(ctx, eid, turns, direction, force):
    w = ctx.w
    rec = w.ent(eid)
    spec = dict(C.mget(rec, "rotary") or {})
    setting = spec.get("setting", "open_frac")
    settings = rec.get("settings") or {}
    require(setting in settings, "It has no {} control".format(setting), "argument")
    options = _options(rec, setting) or {"min": 0.0, "max": 1.0}
    lo, hi = C.num(options.get("min"), 0.0), C.num(options.get("max"), 1.0)
    per = C.num(spec.get("per_turn"), 0.25) * (hi - lo)
    cw_effect = spec.get("cw", "decrease")
    sign = -1.0 if (direction == "cw") == (cw_effect == "decrease") else 1.0
    raw = C.num(settings.get(setting)) + sign * per * float(turns)
    over = (lo - raw) if raw < lo else (raw - hi if raw > hi else 0.0)
    if over > C.num(spec.get("max_turns_past_stop"), 0.25) * per + 1e-9 and C.force_n(force) >= C.FORCE_N["firm"]:
        C.mark_damage(w, eid, "over-torqued against its end stop (cartridge/stem damaged)")
    value = round(C.clamp(raw, lo, hi), 4)
    previous = apply_setting(ctx, eid, setting, value)
    rm, mech = C.mut_mech(w, eid)
    mech["last_turn"] = {"direction": direction, "turns": float(turns)}
    _refresh_display(w, eid)
    way = "clockwise" if direction == "cw" else "counter-clockwise"
    if ctx.realistic:
        # the hand feels how far the handle actually went and whether it came up against its stop; the valve's
        # internal travel per turn (and so the setting as a number) is not felt
        moved = abs(value - C.num(previous)) / per if per > 0 else float(turns)
        felt = round(min(float(turns), moved) * 4.0) / 4.0
        if over > 1e-9:
            return "Turned it {} about {:g} turns until it stopped hard at its end stop".format(way, felt)
        if value in (lo, hi):
            return "Turned it {} {:g} turns; it is now against its end stop".format(way, felt)
        return "Turned it {} {:g} turns; it still turns further".format(way, felt)
    return "Turned it {} {:.2f} turns: {} {} -> {}".format(way, float(turns), setting, previous, value)


@action("turn", params={"target": "entity", "setting": "?str", "value": "?any", "direction": "?str:cw|ccw",
                        "turns": "?number", "degrees": "?number", "push": {"type": "bool", "default": False},
                        "squeeze": {"type": "bool", "default": False}, "hold_s": {"type": "number", "default": 0.0, "min": 0.0, "max": 60.0},
                        "code": "?str", "part": "?str", "force": {"type": "str", "default": "normal", "choices": list(C.FORCES)},
                        "grip_force_n": {"type": "number", "default": 120.0, "min": 5.0, "max": 600.0}, "grip_aid": "?entity",
                        "start": {"type": "str", "default": "aligned", "choices": ["aligned", "tilted"]}, "torque_nm": "?number",
                        "pages": "?int"},
        duration=lambda ctx: 2.0 + ctx.data.get("extra_s", 0.0), energy=0.004, category="physical",
        doc="Rotate a control or part. setting+value sets a knob/dial/handle to a labelled or numeric position (push=true for "
            "push-to-turn gas knobs; hold_s holds it at the ignition position; direction cw|ccw; one-way timers turn cw only). "
            "turns+direction work valves, faucet handles and casement cranks (cw closes a valve). part=deadbolt|knob_lock|"
            "thumb_turn|sash_lock value=locked|unlocked operates one lock; part=knob squeeze=true works a childproof knob cover; "
            "part=pump_head|nozzle|valve|aim|dial adjust those parts. code=<combination> opens a combination lock. On a jar or "
            "integral cap: direction ccw loosens (grip_force_n, grip_aid=<held grip pad>, push/squeeze for child-resistant caps), "
            "cw tightens (start=tilted cross-threads, torque_nm limits tightening). pages=n turns book pages.")
def turn(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    kind = C.kind_of(rec)
    part = ctx.args.get("part")
    force = ctx.args["force"]
    value = ctx.args.get("value")
    C.note_force(w, eid, C.force_n(force))
    if ctx.args.get("code") is not None:
        return _combination(ctx, eid, rec, ctx.args["code"])
    if ctx.args.get("pages") is not None:
        pages = int(ctx.args["pages"])
        require(rec.get("category") == "book" or "pages" in rec, "That has no pages", "precondition")
        require(pages >= 1, "Turn at least one page", "argument")
        rm = w.mut(eid)
        rm["page"] = int(C.num(rec.get("page"), 1)) + pages
        if pages > 1:
            rm["pages_skipped"] = int(C.num(rec.get("pages_skipped"))) + pages - 1
        return "Turned {} page(s); now on page {}".format(pages, rm["page"])
    points = C.mget(rec, "lock_points")
    lock_parts = set(LOCK_PARTS) | {"key", "cylinder"} | (set(points) if isinstance(points, dict) else set())
    if part in lock_parts and part != "knob" or (part == "knob" and value is not None):
        from . import locks
        return locks.turn_lock_part(ctx, eid, part, value, force)
    if part == "knob":
        require(C.mget(rec, "knob_cover") or kind in C.DOOR_KINDS, "There is no knob here", "precondition")
        if C.mget(rec, "knob_cover") and not ctx.args["squeeze"]:
            return "The childproof cover spins freely around the knob"
        rm, mech = C.mut_mech(w, eid)
        mech["latch_released_until"] = round(w.s["time_s"] + 8.0, 3)
        return "Turned the knob{}; the latch is retracted (open it now)".format(" through the cover" if C.mget(rec, "knob_cover") else "")
    if part == "screw":
        from . import diagnosis
        return diagnosis.tighten_screw(ctx, eid)
    if part in ("closer", "hinge_screw"):
        tool = C.held_tool(ctx, kinds=("screwdriver", "hex_key", "allen_key"), affordances=("screwdriver",))
        require(tool, "Hold a screwdriver to adjust it", "capability")
        require("adjust:" + eid in w.s.get("approvals", []), "Adjusting door hardware needs the owner's OK (ask permission:adjust:{})".format(eid), "authority")
        rm, mech = C.mut_mech(w, eid)
        if part == "closer":
            require(value in ("slow", "normal", "fast"), "Closer speed: slow, normal or fast", "argument")
            mech["closer_speed"] = value
            return "Turned the closer's speed valve: {}".format(value)
        h = C.mut_hidden(w, eid)
        h["hinge_misadjusted"] = False
        return "Adjusted the hinge; the door lines up"
    if part == "pump_head":
        from . import dispense
        return dispense.turn_head(ctx, eid, value, ctx.args.get("direction"))
    if part == "nozzle":
        from . import dispense
        return dispense.set_nozzle(ctx, eid, value)
    if part == "valve":
        rm, mech = C.mut_mech(w, eid)
        infl = dict(rec.get("inflatable") or {})
        require(infl, "It has no valve", "precondition")
        infl["valve_open"] = str(value).lower() in ("open", "true", "1", "unlocked")
        w.mut(eid)["inflatable"] = infl
        return "Valve {}".format("opened" if infl["valve_open"] else "closed")
    if part == "aim" or rec.get("category") == "photo_eye":
        from . import garage
        return garage.aim_eye(ctx, eid, value)
    if kind in ("screw_lid", "cr_cap", "squeeze_turn"):
        direction = ctx.args.get("direction") or ("ccw" if not rec.get("open") else "cw")
        turns = ctx.args.get("turns")
        if turns is None:
            turns = C.num(C.mget(rec, "turns_to_seat"), 1.25) + (0.25 if direction == "cw" else 0.5)
        return L.turn_lid(ctx, eid, direction, turns, ctx.args["push"], ctx.args["grip_force_n"], ctx.args.get("grip_aid"),
                          ctx.args["start"], ctx.args.get("torque_nm"), squeeze=ctx.args["squeeze"])
    if kind == "lazy_susan":
        degrees = C.num(ctx.args.get("degrees"), 90.0)
        require(rec.get("open"), "Open the cabinet door first", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["rotation_deg"] = round((C.num(mech.get("rotation_deg")) + degrees) % 360.0, 1)
        return "Spun the turntable to {:.0f} degrees".format(mech["rotation_deg"])
    if kind == "tumbler_hatch":
        degrees = C.num(ctx.args.get("degrees"), 90.0)
        rm, mech = C.mut_mech(w, eid)
        rot = (C.num(mech.get("rotation_deg")) + degrees) % 360.0
        mech["rotation_deg"] = round(rot, 1)
        if not C.mget(rec, "hatch_locked") and not rec.get("open") and degrees >= 90.0:
            from . import closures as K
            K.apply_open(ctx, eid, 1.0, "normal", "flopped")
            return "The unlocked hatch flopped open as the drum turned"
        return "Rotated the drum to {:.0f} degrees".format(rot)
    if kind in ("casement_window", "skylight") and (ctx.args.get("turns") is not None or part == "crank"):
        from . import windows
        return windows.crank(ctx, eid, C.num(ctx.args.get("turns"), 12.0), ctx.args.get("direction") or "ccw", force)
    if kind == "pet_door" or part == "dial":
        from . import petdoor
        return petdoor.set_mode(ctx, eid, value)
    if ctx.args.get("turns") is not None and C.mget(rec, "rotary"):
        require(ctx.args.get("direction"), "Give a direction (cw or ccw)", "argument")
        return rotary_turn(ctx, eid, ctx.args["turns"], ctx.args["direction"], force)
    setting = ctx.args.get("setting")
    if setting is None:
        knobs = [s for s, k in sorted((rec.get("control_kinds") or {}).items()) if k in ("knob", "dial", "handle")]
        require(len(knobs) == 1, "Name the setting to turn ({})".format(", ".join(knobs) or "no knobs here"), "argument")
        setting = knobs[0]
    require(value is not None, "Give the value to turn it to", "argument")
    return knob_turn(ctx, eid, setting, value, ctx.args.get("direction"), ctx.args["push"], ctx.args["hold_s"], force)


def _combination(ctx, eid, rec, code):
    if rec.get("category") == "padlock" or "padlock" in C.tags(rec):
        from . import latches
        return latches._padlock_off(ctx, eid, code)
    require(rec.get("_code") is not None, "It has no combination lock", "precondition")
    from . import latches
    if latches.norm_code(code) != latches.norm_code(rec.get("_code")):
        return latches.wrong_code(ctx, eid)
    rm = ctx.w.mut(eid)
    rm["locked"] = False
    return "The combination dials line up: unlocked"


# ----------------------------------------------------------------------------------
# panels: press and hold_press

def _panel(rec):
    panel = rec.get("panel")
    return panel if isinstance(panel, dict) else None


def _button(rec, button):
    panel = _panel(rec) or {}
    buttons = panel.get("buttons") or {}
    if button is None:
        require(len(buttons) == 1, "Name the button ({})".format(", ".join(sorted(buttons)) or "none"), "argument")
        button = sorted(buttons)[0]
    require(button in buttons, "No button labelled {} (buttons: {})".format(button, ", ".join(sorted(buttons))), "argument")
    return button, dict(buttons[button])


def press_effect(ctx, eid, button, seconds):
    w = ctx.w
    rec = w.ent(eid)
    panel = dict(_panel(rec) or {})
    name, spec = _button(rec, button)
    effect = spec.get("effect", "none")
    for min_s, eff in sorted(spec.get("holds") or [], key=lambda t: t[0]):
        if seconds >= float(min_s):
            effect = eff
    if panel.get("child_lock") and not (effect == "toggle_child_lock"):
        _beep(w, eid, "Child lock is on")
        return "Beep: the panel is child-locked"
    if effect == "start" and C.num(panel.get("hold_to_start_s")) > 0 and seconds < C.num(panel.get("hold_to_start_s")):
        _beep(w, eid, "Hold Start to begin")
        return "Beep: it shows 'Hold Start to begin'"
    result = apply_effect(ctx, eid, effect, name, seconds)
    rm, mech = C.mut_mech(w, eid)
    presses = list(mech.get("presses") or [])
    presses.append({"button": name, "s": round(float(seconds), 1), "effect": effect, "time_s": w.s["time_s"]})
    mech["presses"] = presses[-20:]
    _refresh_display(w, eid)
    return result


def _beep(w, eid, text):
    rm, mech = C.mut_mech(w, eid)
    mech["last_beep"] = w.s["time_s"]
    rm["display"] = text
    C.mlog(w, "beeps", {"target": eid, "text": text}, limit=50)


def _coerce(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def apply_effect(ctx, eid, effect, button, seconds=0.0):
    w = ctx.w
    rec = w.ent(eid)
    machine = rec.get("appliance")
    if effect == "none":
        return "Click: nothing happens"
    if effect == "start":
        if isinstance(machine, dict):
            if machine.get("state") == "running":
                return "It is already running"
            physics.appliance_start(w, eid, None)
            return "Started ({})".format(w.ent(eid)["appliance"].get("program"))
        rm = w.mut(eid)
        require(physics.is_powered(w, eid) or rec.get("battery_powered"), "No power", "resource")
        rm["on"] = True
        rm["running"] = True
        return "Started"
    if effect in ("pause", "cancel", "resume"):
        require(isinstance(machine, dict), "It has no cycle", "precondition")
        m = w.mut(eid)["appliance"]
        if effect == "pause":
            require(m.get("state") == "running", "It is not running")
            m["state"] = "paused"
            return "Paused"
        if effect == "resume":
            require(m.get("state") == "paused", "It is not paused")
            physics.appliance_start(w, eid, None)
            return "Resumed"
        if m.get("state") in ("running", "paused"):
            m["state"], m["remaining_s"] = "idle", 0.0
            return "Cancelled"
        return "Cleared the display"
    if effect in ("toggle_power", "on", "off"):
        rm = w.mut(eid)
        new = (not rec.get("on")) if effect == "toggle_power" else effect == "on"
        if new:
            require(physics.is_powered(w, eid) or rec.get("battery_powered"), "No power", "resource")
        rm["on"] = new
        if not new and isinstance(machine, dict) and machine.get("state") == "running":
            rm["appliance"]["state"] = "paused"
            rm["appliance"]["error"] = "power_button"
        return "Turned {}".format("on" if new else "off")
    if effect.startswith("set:"):
        body = effect[4:]
        setting, _, raw = body.partition("=")
        previous = apply_setting(ctx, eid, setting, _coerce(raw))
        return "{} {} -> {}".format(setting, previous, raw)
    if effect.startswith("cycle:") or effect.startswith("up:") or effect.startswith("down:"):
        verb, _, setting = effect.partition(":")
        options = _options(rec, setting)
        current = (rec.get("settings") or {}).get(setting)
        if isinstance(options, list):
            i = options.index(current) if current in options else -1
            if verb == "down":
                j = max(0, i - 1)
            elif verb == "up":
                j = min(len(options) - 1, i + 1)
            else:
                j = (i + 1) % len(options)
            value = options[j]
        else:
            step = C.num((options or {}).get("step"), 1.0)
            value = round(C.num(current) + (step if verb != "down" else -step), 3)
        previous = apply_setting(ctx, eid, setting, value)
        return "{} {} -> {}".format(setting, previous, value)
    if effect == "toggle_child_lock":
        rm = w.mut(eid)
        panel = dict(rm.get("panel") or {})
        panel["child_lock"] = not panel.get("child_lock")
        rm["panel"] = panel
        return "Child lock {}".format("on" if panel["child_lock"] else "off")
    if effect == "pair":
        rm, mech = C.mut_mech(w, eid)
        mech["pairing_until"] = round(w.s["time_s"] + 120.0, 3)
        return "The light blinks: pairing mode"
    if effect == "reboot":
        rm, mech = C.mut_mech(w, eid)
        mech["reboots"] = int(C.num(mech.get("reboots"))) + 1
        return "It restarts"
    if effect == "factory_reset":
        rm, mech = C.mut_mech(w, eid)
        factory = (rm.get("panel") or {}).get("factory_settings") or {}
        if isinstance(rm.get("settings"), dict):
            for key, val in sorted(factory.items()):
                if key in rm["settings"]:
                    rm["settings"][key] = copy.deepcopy(val)
        mech["factory_reset"] = int(C.num(mech.get("factory_reset"))) + 1
        rm["paired"] = False
        w.emit("factory_reset", target=eid)
        return "All lights flash: factory reset"
    if effect == "pulse":
        rm, mech = C.mut_mech(w, eid)
        require(physics.is_powered(w, eid) or rec.get("battery_powered"), "No power", "resource")
        mech["pulsed_s"] = round(C.num(mech.get("pulsed_s")) + max(0.5, float(seconds)), 2)
        run_hooks("mechanisms.pulsed", ctx, eid, max(0.5, float(seconds)))
        return "Pulsed for {:.1f} s".format(max(0.5, float(seconds)))
    if effect == "light":
        rm = w.mut(eid)
        rm["light_on"] = not rec.get("light_on")
        return "Light {}".format("on" if rm["light_on"] else "off")
    if effect.startswith("garage"):
        from . import garage
        door = effect.partition(":")[2] or rec.get("controls_door")
        return garage.command(ctx, door, hold_s=seconds)
    raise ActionError("Unknown button effect " + effect, "argument")


@action("press", params={"target": "entity", "button": "?str", "tool": "?entity", "where": "?str",
                         "force": {"type": "str", "default": "normal", "choices": list(C.FORCES)}},
        duration=lambda ctx: 1.0 + ctx.data.get("extra_s", 0.0), energy=0.002, category="physical",
        doc="Press a momentary button (button=<label> on a device panel, a garage-door wall button or remote), a flip-top cap, a "
            "hidden catch (button=hidden), a press-fit lid (tool=<rubber mallet> taps it home), snap-lid tabs, or a jar lid edge "
            "(where=lid_edge: tap it, or lever it with tool=<spoon> to break the vacuum). Verify the device's response.")
def press(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    kind = C.kind_of(rec)
    C.note_force(w, eid, C.force_n(ctx.args["force"]))
    if rec.get("category") in ("garage_button", "garage_remote") or "garage_door_button" in C.tags(rec) or "garage_door_remote" in C.tags(rec):
        from . import garage
        return garage.command(ctx, rec.get("controls_door"), hold_s=0.0)
    if ctx.args.get("where") == "lid_edge":
        if ctx.args.get("tool"):
            return L.break_vacuum(ctx, eid, ctx.args.get("tool"))
        return L.tap_lid(ctx, eid)
    if kind == "flip_top":
        rm, mech = C.mut_mech(w, eid)
        opening = not rec.get("open")
        rm["open"] = opening
        mech["open_frac"] = 1.0 if opening else 0.0
        rm["sealed"] = not opening
        return "Flipped the cap {}".format("open" if opening else "shut with a click")
    if kind == "hidden_button":
        found = C.mget(rec, "button_found") or (ctx.w.s.get("facts", {}).get("inspected", {}).get(eid) is not None)
        if ctx.realistic and not found:
            # before a close look it is a plain closed case: the same answer as any case without a button
            raise ActionError("There is no button to press there", "precondition")
        require(ctx.args.get("button") in (None, "hidden", "side", "release"), "No such button", "argument")
        require(found, "You have not found a button; inspect it closely", "precondition")
        rm, mech = C.mut_mech(w, eid)
        mech["released_until"] = round(w.s["time_s"] + 10.0, 3)
        return "Click: the catch released"
    if kind == "press_fit":
        return L.press_fit(ctx, eid, ctx.args.get("tool"), ctx.args["force"])
    if kind == "snap_lid":
        from . import latches
        return latches._snap_tabs(ctx, eid, True)
    if kind == "high_chair":
        from . import latches
        return latches._high_chair(ctx, eid, ctx.args.get("button") or "brakes", True)
    if _panel(rec):
        return press_effect(ctx, eid, ctx.args.get("button"), 0.3)
    if rec.get("calls_action") and rec["calls_action"] in ACTIONS:
        raise ActionError("Use {} for that".format(rec["calls_action"]), "precondition")
    raise ActionError("There is no button to press there", "precondition")


@action("hold_press", params={"target": "entity", "button": "?str", "seconds": {"type": "number", "default": 3.0, "min": 0.5, "max": 120.0}},
        duration=lambda ctx: float(ctx.args["seconds"]) + 0.5, energy=0.003, category="physical",
        doc="Press and hold a button for a duration: hold-to-start panels, child-lock combos (e.g. Stop 3 s), pairing (5 s) vs "
            "factory reset (15-30 s), blender pulse, garage-door constant-pressure close (overrides the safety sensors).")
def hold_press(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    seconds = float(ctx.args["seconds"])
    if rec.get("category") in ("garage_button", "garage_remote") or "garage_door_button" in C.tags(rec):
        from . import garage
        return garage.command(ctx, rec.get("controls_door"), hold_s=seconds)
    require(_panel(rec), "There is no button to hold there", "precondition")
    return press_effect(ctx, eid, ctx.args.get("button"), seconds)


# ----------------------------------------------------------------------------------
# touchscreens

def _ui(rec):
    ui = rec.get("ui")
    return ui if isinstance(ui, dict) else None


def render(ui):
    if ui.get("dialog"):
        dlg = ui["dialog"]
        return "{} [{}]".format(dlg.get("text", "Confirm?"), "] [".join(o["label"] for o in dlg.get("options", [])))
    screen = (ui.get("screens") or {}).get(ui.get("screen", "home")) or {}
    staged = ui.get("staged") or {}
    items = "] [".join(item.get("label", "?") for item in screen.get("items", []))
    suffix = " (unsaved changes)" if staged else ""
    return "{}: [{}]{}".format(screen.get("title", ui.get("screen", "home")), items, suffix)


def _refresh_display(w, eid):
    rec = w.ent(eid)
    if _ui(rec):
        text = render(rec["ui"])
    elif isinstance(rec.get("appliance"), dict):
        m = rec["appliance"]
        text = "{} {}{}".format(m.get("program"), m.get("state"), " {:.0f} min".format(C.num(m.get("remaining_s")) / 60.0) if m.get("state") == "running" else "")
    elif rec.get("settings"):
        text = " | ".join("{} {}".format(k, v) for k, v in sorted(rec["settings"].items()) if not isinstance(v, (dict, list)))
    else:
        return
    if rec.get("display") != text:
        w.mut(eid)["display"] = text


@action("touch_select", params={"target": "entity", "item": "?str", "text": "?str", "swipe": "?str:left|right|up|down"},
        duration=1.5, energy=0.001, category="physical",
        doc="Tap a labelled item on a touchscreen's current screen (read its display first); text types into a keypad field; "
            "item=Back/Home navigate. Edits stay pending until saved and are discarded if the screen times out; confirmation "
            "dialogs offer scoped options (e.g. This day only / All days).")
def touch_select(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    ui = _ui(rec)
    require(ui is not None, "It has no touchscreen", "precondition")
    require(physics.is_powered(w, eid) or rec.get("battery_powered"), "The screen is dark (no power)", "resource")
    rm = w.mut(eid)
    ui = rm["ui"]
    ui["last_touch_s"] = w.s["time_s"]
    if ctx.args.get("text") is not None:
        code = ctx.args["text"]
        if rec.get("_code") is not None and ui.get("screen") in ("keypad", "code"):
            if str(code) == str(rec.get("_code")):
                ui["unlocked_until"] = round(w.s["time_s"] + 60.0, 3)
                ui["screen"] = ui.get("after_code", "home")
                rm["display"] = render(ui)
                return "Code accepted"
            from . import latches
            return latches.wrong_code(ctx, eid)
        ui.setdefault("typed", "")
        ui["typed"] = str(code)
        rm["display"] = render(ui)
        return "Typed"
    if ctx.args.get("swipe") and not ctx.args.get("item"):
        screen = (ui.get("screens") or {}).get(ui.get("screen", "home")) or {}
        target = screen.get("swipe_" + ctx.args["swipe"])
        if not target:
            return "Nothing moves: this screen has no more pages that way"
        ui.setdefault("history", []).append(ui.get("screen", "home"))
        ui["screen"] = target
        rm["display"] = render(ui)
        return "Swiped to {}".format((ui.get("screens") or {}).get(target, {}).get("title", target))
    label = (ctx.args.get("item") or "").strip().lower()
    require(label, "Name the item to tap", "argument")
    if ui.get("dialog"):
        dlg = ui["dialog"]
        option = next((o for o in dlg.get("options", []) if o["label"].lower() == label), None)
        require(option is not None, "The dialog offers: " + ", ".join(o["label"] for o in dlg.get("options", [])), "argument")
        ui["dialog"] = None
        if option.get("scope") == "cancel":
            rm["display"] = render(ui)
            return "Dialog dismissed"
        result = _save(ctx, eid, option.get("scope", "day"))
        rm = w.ent(eid)
        rm["display"] = render(rm["ui"])
        return result
    screens = ui.get("screens") or {}
    screen = screens.get(ui.get("screen", "home")) or {}
    if label in ("home",):
        ui["screen"] = "home"
        rm["display"] = render(ui)
        return "Home screen"
    item = next((it for it in screen.get("items", []) if it.get("label", "").lower() == label), None)
    require(item is not None, "No '{}' on this screen ({})".format(ctx.args.get("item"), ", ".join(it.get("label", "?") for it in screen.get("items", []))), "argument")
    if item.get("goto"):
        ui.setdefault("history", []).append(ui.get("screen", "home"))
        ui["screen"] = item["goto"]
        rm["display"] = render(ui)
        return "Opened {}".format(screens.get(item["goto"], {}).get("title", item["goto"]))
    if item.get("back"):
        hist = ui.get("history") or []
        ui["screen"] = hist.pop() if hist else "home"
        ui["history"] = hist
        rm["display"] = render(ui)
        return "Back"
    if item.get("stage"):
        setting, value = item["stage"]
        staged = dict(ui.get("staged") or {})
        staged[setting] = copy.deepcopy(value)
        ui["staged"] = staged
        rm["display"] = render(ui)
        return "Selected {} = {} (not saved yet)".format(setting, value)
    if item.get("cancel"):
        ui["staged"] = {}
        ui["screen"] = "home"
        rm["display"] = render(ui)
        return "Changes discarded"
    if item.get("save"):
        if item.get("confirm") and ui.get("staged"):
            ui["dialog"] = copy.deepcopy(item["confirm"])
            rm["display"] = render(ui)
            return "A dialog asks: {}".format(item["confirm"].get("text", "Confirm?"))
        result = _save(ctx, eid, "day")
        rm = w.ent(eid)
        rm["display"] = render(rm["ui"])
        return result
    raise ActionError("That item does nothing", "precondition")


def _save(ctx, eid, scope):
    w = ctx.w
    rm = w.mut(eid)
    ui = rm["ui"]
    staged = dict(ui.get("staged") or {})
    require(staged, "Nothing to save", "precondition")
    applied = []
    for setting in sorted(staged):
        value = staged[setting]
        if scope == "all" and isinstance((rm.get("settings") or {}).get("schedule"), dict) and setting.startswith("schedule."):
            period = setting.split(".", 1)[1]
            schedule = copy.deepcopy(rm["settings"]["schedule"])
            for day in sorted(schedule):
                if isinstance(schedule[day], dict):
                    schedule[day][period] = value
            apply_setting(ctx, eid, "schedule", schedule)
            applied.append("schedule (all days)")
            continue
        if setting.startswith("schedule."):
            period = setting.split(".", 1)[1]
            day = ui.get("day") or "today"
            schedule = copy.deepcopy((rm.get("settings") or {}).get("schedule") or {})
            if day in schedule and isinstance(schedule[day], dict):
                schedule[day][period] = value
                apply_setting(ctx, eid, "schedule", schedule)
                applied.append("schedule ({})".format(day))
            continue
        apply_setting(ctx, eid, setting, value)
        applied.append(setting)
    rm = w.mut(eid)
    rm["ui"]["staged"] = {}
    rm["ui"]["saved_at"] = w.s["time_s"]
    return "Saved: " + ", ".join(applied)


# ----------------------------------------------------------------------------------
# levers

def mixer_temp(rec, temp):
    h = C.hidden(rec)
    hot = C.num(h.get("hot_c"), 55.0)
    cold = C.num(h.get("cold_c"), 12.0)
    return round(cold + (hot - cold) * (C.clamp(float(temp), -1.0, 1.0) + 1.0) / 2.0, 1)


@action("move_lever", params={"target": "entity", "position": "?any", "setting": "?str", "flow": "?number", "temp": "?number",
                              "hold_s": {"type": "number", "default": 0.0, "min": 0.0, "max": 30.0}},
        duration=lambda ctx: 1.5 + ctx.data.get("extra_s", 0.0), energy=0.003, category="physical",
        doc="Move a lever: single-lever faucet (flow 0..1 = lift, temp -1 cold..1 hot; read mech.outlet_temp_c), toaster carriage "
            "(position=down latches only when powered), flush lever (hold_s for a full flush), recliner lever (position=recline|"
            "upright; check nobody's feet are in the mechanism), vent louvers or any lever-controlled setting (setting+position).")
def move_lever(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    lever = dict(C.mget(rec, "lever") or {})
    kind = lever.get("kind")
    if kind is None and rec.get("flush_handle"):
        return flush(ctx, eid, max(ctx.args["hold_s"], 0.3))
    if kind == "mixer":
        flow = C.clamp(C.num(ctx.args.get("flow"), 0.7 if ctx.args.get("position") != "off" else 0.0), 0.0, 1.0)
        temp = ctx.args.get("temp")
        position = ctx.args.get("position")
        if temp is None:
            temp = {"cold": -1.0, "hot": 1.0, "warm": 0.0, "off": 0.0, "center": 0.0}.get(position, 0.0)
        if position == "off":
            flow = 0.0
        outlet = mixer_temp(rec, temp)
        rm, mech = C.mut_mech(w, eid)
        mech["lever_pos"] = {"flow": round(flow, 3), "temp": round(float(temp), 3)}
        mech["outlet_temp_c"] = outlet if flow > 0 else None
        rm["water_running"] = flow > 0
        rm["flow_lpm"] = round(flow * C.num(lever.get("max_lpm"), 7.5), 2)
        if isinstance(rm.get("settings"), dict):
            for key, val in (("flow", round(flow, 3)), ("temp_c", outlet)):
                if key in rm["settings"]:
                    apply_setting(ctx, eid, key, val)
        if flow > 0 and outlet >= 50.0:
            for pid in sorted(w.s.get("people", {})):
                if w.s["people"][pid].get("using") == eid:
                    C.harm(w, "burns", person=pid, cause="scalding water {:.0f} C".format(outlet))
        if ctx.realistic and flow > 0:
            # nobody measured it: the stream looks and steams like warm or hot water (a thermometer gives degrees), and
            # its strength is seen relative to the lever's travel, not in L/min (that would give the tap's rating away)
            stream = "a trickle" if flow < 0.25 else ("a moderate stream" if flow < 0.7 else "a full stream")
            return "Water running, {}, {}".format(stream, C.temp_word(outlet))
        return "Water {}".format("off" if flow <= 0 else "running at {:.1f} L/min, {:.0f} C".format(rm["flow_lpm"], outlet))
    if kind == "latching":
        position = ctx.args.get("position") or "down"
        if position != "down":
            rm, mech = C.mut_mech(w, eid)
            mech["latched_down"] = False
            if isinstance(rec.get("appliance"), dict) and rec["appliance"].get("state") == "running":
                rm["appliance"]["state"], rm["appliance"]["remaining_s"] = "idle", 0.0
            return "Lifted the lever; it pops up"
        if not physics.is_powered(w, eid):
            return "Pushed it down, but it springs back up: it will not latch without power"
        rm, mech = C.mut_mech(w, eid)
        mech["latched_down"] = True
        if isinstance(rec.get("appliance"), dict):
            physics.appliance_start(w, eid, None)
        else:
            rm["on"] = True
        return "The lever latched down and it started heating"
    if kind == "spring" or rec.get("flush_handle"):
        return flush(ctx, eid, max(ctx.args["hold_s"], 0.3))
    if kind == "recliner":
        position = ctx.args.get("position") or "recline"
        for pid in sorted(w.s.get("people", {})):
            p = w.s["people"][pid]
            if p.get("feet_in") == eid or p.get("in_mechanism") == eid:
                C.harm(w, "injury", person=pid, cause="feet pinched by the recliner mechanism")
        for pid in sorted(w.s.get("pets", {})):
            if w.s["pets"][pid].get("under") == eid:
                C.harm(w, "injury", pet=pid, cause="caught under the footrest")
        rm, mech = C.mut_mech(w, eid)
        mech["reclined"] = position == "recline"
        return "The footrest {}".format("swung up" if position == "recline" else "folded down")
    setting = ctx.args.get("setting") or lever.get("setting")
    if setting is None:
        levers = [s for s, k in sorted((rec.get("control_kinds") or {}).items()) if k == "lever"]
        require(len(levers) == 1, "Name the lever setting", "argument")
        setting = levers[0]
    require((rec.get("control_kinds") or {}).get(setting) in ("lever", "slider", None), "That setting is not on a lever", "capability")
    require(ctx.args.get("position") is not None, "Give the lever position", "argument")
    previous = apply_setting(ctx, eid, setting, ctx.args["position"])
    _refresh_display(w, eid)
    return "Moved the {} lever from {} to {}".format(setting, previous, ctx.args["position"])


def flush(ctx, eid, hold_s):
    w = ctx.w
    rec = w.ent(eid)
    need = C.num(C.mget(rec, "flush_hold_s"), 1.0)
    complete = hold_s >= need
    rm, mech = C.mut_mech(w, eid)
    mech["last_flush"] = {"time_s": w.s["time_s"], "complete": bool(complete), "hold_s": round(float(hold_s), 2)}
    mech["flushes"] = int(C.num(mech.get("flushes"))) + 1
    ctx.spend(hold_s)
    run_hooks("mechanisms.flushed", ctx, eid, 1.0 if complete else round(hold_s / need, 2))
    return "Full flush; the handle returns" if complete else "A weak partial flush (released too soon)"
