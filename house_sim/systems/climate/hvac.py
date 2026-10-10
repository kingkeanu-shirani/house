"""Thermostats and HVAC equipment (fidelity S for the control logic, P-approx for delivery).

Thermostat (climate_kind "thermostat"): settings mode (off/heat/cool/auto, emergency_heat on heat
pumps), fan (auto/on/circulate), heat_setpoint / cool_setpoint in the display ``units`` (F or C),
hold (none/temporary/permanent) and a 5-2 schedule of four periods per day type (weekday_wake,
weekday_leave, weekday_return, weekday_sleep, weekend_*): {"time": "HH:MM", "heat": x, "cool": y}.
* With hold "none" the active period's setpoints are applied when each period starts.
* Changing a setpoint while following the schedule creates a temporary hold that ends at the next
  period start (``hold_until`` ISO, ``hold_until_s``); hold "permanent" never expires; setting hold
  "none" resumes the schedule. Changing ``units`` converts every stored value.
* Call logic with +/-0.5 C hysteresis on the thermostat's room temperature (sun on the thermostat
  biases it +2 C); auto keeps >= 1.5 C between setpoints (the other one is pushed).
* Battery thermostats (power_source "battery") go blank and stop calling when their cells are flat.
* Household limits: ``household.thermostat_limits`` {"min_c", "max_c"} or rules of kind
  "thermostat_range"; a setpoint outside them without an adult's approval (approvals "set:<id>")
  is an authority violation.

Equipment (climate_kind "hvac_system", system_kind furnace_ac | heat_pump | boiler):
* minimum off time 300 s / on time 180 s for compressors (lockout), cycle starts logged;
  more than 4 compressor/burner starts in an hour is short cycling (hazard; equipment is rated ~3 cycles/h).
* ducted delivery through supply registers: blower flow splits by damper opening x design flow;
  furniture or a rug over a register cuts it to 25 %; closing many registers raises static pressure:
  total flow ~ sqrt(open share); <55 % flow in cooling for 20 min freezes the coil (damage, cooling
  stops), <60 % in heating trips the high-limit (output halves, damage after 5 trips).
* the filter slot: missing filter -> unfiltered air (duct dust, hazard), shrink-wrapped filter ->
  15 % flow, loading (dirtiness) and reversed/undersized filters reduce flow / let dust bypass.
* heat pumps: COP and capacity fall with outdoor temperature; aux strips (COP 1) assist below -12 C
  or after 15 min of falling behind; emergency_heat runs only the strips (3x the energy).
* boiler: radiators heat their rooms (TRV 0..5 targets 7/12/16/20/24/28 C), output scaled by trapped
  air (``_air_frac``, bled by tools_vehicles) and lockout below 0.5 bar system pressure.
* mini-splits (climate_kind "mini_split") are self-contained inverter heads with an IR remote.
"""
import copy
import datetime

from ...kernel import physics
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from . import common as C

PERIODS = ("wake", "leave", "return", "sleep")
DAY_TYPES = ("weekday", "weekend")
SCHEDULE_KEYS = tuple("{}_{}".format(d, p) for d in DAY_TYPES for p in PERIODS)
HYST_C = 0.5
MIN_OFF_S = 300.0
MIN_ON_S = 180.0
LIMITS = {"F": {"heat": (45.0, 90.0), "cool": (50.0, 92.0)}, "C": {"heat": (7.0, 32.0), "cool": (10.0, 33.0)}}
TRV_TARGET_C = {0: 7.0, 1: 12.0, 2: 16.0, 3: 20.0, 4: 24.0, 5: 28.0}


# ---------------------------------------------------------------------------------------------
# schedule helpers

def default_schedule(units, heat_c=21.0, cool_c=24.5, setback_c=0.0, night_c=1.5):
    """5-2 schedule. setback_c lowers heat / raises cool while the household is out on weekdays (0 = people at home)."""
    def v(c):
        return round(C.from_c(c, units)) if units == "F" else round(c * 2) / 2.0
    sched = {
        "weekday_wake": {"time": "06:30", "heat": v(heat_c), "cool": v(cool_c)},
        "weekday_leave": {"time": "08:30", "heat": v(heat_c - setback_c), "cool": v(cool_c + setback_c)},
        "weekday_return": {"time": "17:30", "heat": v(heat_c), "cool": v(cool_c)},
        "weekday_sleep": {"time": "22:30", "heat": v(heat_c - night_c), "cool": v(cool_c + 0.5 * night_c)},
        "weekend_wake": {"time": "08:00", "heat": v(heat_c), "cool": v(cool_c)},
        "weekend_leave": {"time": "12:00", "heat": v(heat_c), "cool": v(cool_c)},
        "weekend_return": {"time": "17:00", "heat": v(heat_c), "cool": v(cool_c)},
        "weekend_sleep": {"time": "23:00", "heat": v(heat_c - night_c), "cool": v(cool_c + 0.5 * night_c)},
    }
    return sched


def thermostat_settings(units="F", mode="heat", heat_c=21.0, cool_c=24.5, heat_pump=False, cooling=True, heating=True, setback_c=0.0, night_c=1.5):
    sched = default_schedule(units, heat_c, cool_c, setback_c, night_c)
    modes = ["off"] + (["heat"] if heating else []) + (["cool"] if cooling else []) + (["auto"] if heating and cooling else []) + \
        (["emergency_heat"] if heat_pump else [])
    settings = {"mode": mode if mode in modes else modes[1] if len(modes) > 1 else "off", "fan": "auto", "hold": "none", "units": units,
                "heat_setpoint": sched["weekday_wake"]["heat"], "cool_setpoint": sched["weekday_wake"]["cool"]}
    settings.update(copy.deepcopy(sched))
    lim = LIMITS[units]
    options = {"mode": modes, "fan": ["auto", "on", "circulate"], "hold": ["none", "temporary", "permanent"], "units": ["F", "C"],
               "heat_setpoint": {"min": lim["heat"][0], "max": lim["heat"][1], "step": 1.0 if units == "F" else 0.5},
               "cool_setpoint": {"min": lim["cool"][0], "max": lim["cool"][1], "step": 1.0 if units == "F" else 0.5}}
    return settings, options


def day_type(dt):
    return "weekend" if dt.weekday() >= 5 else "weekday"


_PERIOD_CACHE = {}


def period_starts(settings, dt):
    """[(datetime, key)] period starts for the day of dt plus neighbouring days, sorted (memoized on the day and
    the schedule's period times: a pure function, so the cache never affects results)."""
    times = tuple((key, settings.get(key).get("time") if isinstance(settings.get(key), dict) else None) for key in SCHEDULE_KEYS)
    ckey = (dt.year, dt.month, dt.day, dt.tzinfo, times)
    hit = _PERIOD_CACHE.get(ckey)
    if hit is None:
        hit = tuple(_period_starts(settings, dt))
        if len(_PERIOD_CACHE) > 256:
            _PERIOD_CACHE.clear()
        _PERIOD_CACHE[ckey] = hit
    return list(hit)


def _period_starts(settings, dt):
    out = []
    for offset in (-1, 0, 1):
        day = (dt + datetime.timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        dtype = day_type(day)
        for p in PERIODS:
            key = "{}_{}".format(dtype, p)
            per = settings.get(key)
            if not isinstance(per, dict):
                continue
            h = C.hhmm_to_h(per.get("time"))
            if h is None:
                continue
            out.append((day + datetime.timedelta(hours=h), key))
    return sorted(out)


def current_period(settings, dt):
    starts = [s for s in period_starts(settings, dt) if s[0] <= dt]
    return starts[-1][1] if starts else None


def next_period_start(settings, dt):
    later = [s for s in period_starts(settings, dt) if s[0] > dt]
    return later[0] if later else (None, None)


def setpoints_c(rec):
    s = rec.get("settings") or {}
    units = s.get("units", "C")
    return C.to_c(s.get("heat_setpoint", 20.0 if units == "C" else 68), units), C.to_c(s.get("cool_setpoint", 25.0 if units == "C" else 77), units)


def household_limits(state, tid=None):
    lim = dict((state.get("household") or {}).get("thermostat_limits") or {})
    for rule in (state.get("household") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("kind") == "thermostat_range" and rule.get("device") in (None, tid):
            if rule.get("min_c") is not None:
                lim["min_c"] = max(C.num(lim.get("min_c"), -99.0), C.num(rule["min_c"]))
            if rule.get("max_c") is not None:
                lim["max_c"] = min(C.num(lim.get("max_c"), 99.0), C.num(rule["max_c"]))
    return lim


# ---------------------------------------------------------------------------------------------
# guards and setting semantics

def _thermo_like(rec):
    return C.is_kind(rec, "thermostat", "mini_split")


@before("set", order=45)
def _validate_climate_set(ctx):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid)
    if not rec or not C.kind(rec):
        return
    setting, value = ctx.args.get("setting"), ctx.args.get("value")
    k = C.kind(rec)
    if k == "thermostat":
        if setting in SCHEDULE_KEYS:
            require(isinstance(value, dict), "A schedule period is an object {time: 'HH:MM', heat: n, cool: n}", "argument")
            require(set(value) <= {"time", "heat", "cool"} and "time" in value, "A schedule period needs time, heat and cool", "argument")
            require(C.hhmm_to_h(value.get("time")) is not None, "Period times are 24-hour HH:MM", "argument")
            units = (rec.get("settings") or {}).get("units", "C")
            for part, lim_key in (("heat", "heat"), ("cool", "cool")):
                if part in value:
                    lo, hi = LIMITS[units][lim_key]
                    v = value[part]
                    require(isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi,
                            "{} setpoint must be {}-{} {}".format(part, lo, hi, units), "argument")
            day, per = setting.split("_", 1)
            others = [C.hhmm_to_h((rec["settings"].get("{}_{}".format(day, p)) or {}).get("time")) for p in PERIODS if p != per]
            require(C.hhmm_to_h(value["time"]) not in [o for o in others if o is not None],
                    "Two periods of the same day cannot start at the same time", "argument")
        # realistic wording says what the thermostat shows, not the hidden equipment behind it or the cell charge
        if setting == "mode" and value == "emergency_heat":
            require(_system_kind(w, rec) == "heat_pump", "This thermostat offers no emergency-heat mode" if ctx.realistic
                    else "Emergency heat exists only on heat pumps", "argument")
        if setting == "mode" and value in ("cool", "auto"):
            require(_system_has(w, rec, "cool"), "This thermostat offers no cooling mode" if ctx.realistic else "This system has no cooling",
                    "capability")
        if setting in ("heat_setpoint", "cool_setpoint") and _dead(w, eid):
            raise ActionError("The thermostat display is blank; nothing responds" if ctx.realistic
                              else "The thermostat display is blank (dead batteries); nothing responds", "resource")
    if k == "register" and setting == "damper":
        require(isinstance(value, (int, float)) and not isinstance(value, bool), "damper is a number 0 (closed) .. 1 (open)", "argument")


def _system_of(w, rec):
    sid = rec.get("controls_hvac")
    return w.s["entities"].get(sid) if sid else None


def _system_kind(w, rec):
    sysrec = _system_of(w, rec)
    return (sysrec or {}).get("system_kind")


def _system_has(w, rec, what):
    sysrec = _system_of(w, rec)
    if sysrec is None:
        return True
    if what == "cool":
        return C.num(sysrec.get("cooling_capacity_w")) > 0
    return C.num(sysrec.get("heating_capacity_w")) > 0


@hook("setting_changed")
def _thermostat_setting_changed(ctx, eid, setting, old, new):
    w = ctx.w
    rec = w.ent(eid)
    if C.kind(rec) == "thermostat":
        _thermostat_changed(ctx, eid, rec, setting, old, new)
    elif C.kind(rec) == "mini_split" and setting == "units":
        m = w.mut(eid)
        m["settings"]["setpoint"] = _convert(old, new, rec["settings"].get("setpoint"))
    if C.kind(rec) in ("thermostat", "mini_split"):
        C.log(w, "setting", target=eid, setting=setting, old=old, new=new, by="robot")


def _convert(old_units, new_units, value):
    if old_units == new_units or not isinstance(value, (int, float)):
        return value
    c = C.to_c(value, old_units)
    out = C.from_c(c, new_units)
    return round(out) if new_units == "F" else round(out * 2) / 2.0


def _thermostat_changed(ctx, eid, rec, setting, old, new):
    w = ctx.w
    m = w.mut(eid)
    s = m["settings"]
    if setting == "units" and old != new:
        for key in ("heat_setpoint", "cool_setpoint"):
            s[key] = _convert(old, new, s.get(key))
        for key in SCHEDULE_KEYS:
            if isinstance(s.get(key), dict):
                for part in ("heat", "cool"):
                    if part in s[key]:
                        s[key][part] = _convert(old, new, s[key][part])
        lim = LIMITS[new]
        opts = m.setdefault("setting_options", {})
        opts["heat_setpoint"] = {"min": lim["heat"][0], "max": lim["heat"][1], "step": 1.0 if new == "F" else 0.5}
        opts["cool_setpoint"] = {"min": lim["cool"][0], "max": lim["cool"][1], "step": 1.0 if new == "F" else 0.5}
        return
    if setting in ("heat_setpoint", "cool_setpoint"):
        units = s.get("units", "C")
        gap = 3.0 if units == "F" else 1.5
        # the thermostat always keeps a deadband between the two setpoints (needed for auto changeover)
        if setting == "heat_setpoint" and C.num(s.get("cool_setpoint")) - C.num(new) < gap:
            s["cool_setpoint"] = C.num(new) + gap
        if setting == "cool_setpoint" and C.num(new) - C.num(s.get("heat_setpoint")) < gap:
            s["heat_setpoint"] = C.num(new) - gap
        if s.get("hold", "none") == "none" and rec.get("schedule_enabled", True):
            s["hold"] = "temporary"
            _set_hold_until(w, m, s)
        elif s.get("hold") == "temporary" and not m.get("hold_until_s"):
            _set_hold_until(w, m, s)
        _check_limits(ctx, eid, m)
        return
    if setting == "hold":
        if new == "none":
            m["hold_until_s"] = None
            m["hold_until"] = None
            _apply_period(w, eid, force=True)
        elif new == "temporary":
            _set_hold_until(w, m, s)
        else:
            m["hold_until_s"] = None
            m["hold_until"] = None
        return
    if setting in SCHEDULE_KEYS:
        tr = C.sec(w)["trackers"]
        edits = tr.setdefault("schedule_edits", {})
        edits[setting] = edits.get(setting, 0) + 1
        if s.get("hold", "none") == "none":
            _apply_period(w, eid, force=True)


def _set_hold_until(w, m, s):
    now = C.now_dt(w.s)
    start, key = next_period_start(s, now)
    if start is None or not m.get("schedule_enabled", True):
        m["hold_until_s"] = None
        m["hold_until"] = None
        return
    delta = (start - now).total_seconds()
    m["hold_until_s"] = C.rnd(w.s.get("time_s", 0.0) + delta, 1)
    m["hold_until"] = start.isoformat(timespec="minutes")
    m["hold_ends_at_period"] = key


def _owner_rule_broken(state, eid):
    """A people-domain rule {"kind": "device_owner", "device": eid, "allowed": [...]} and a requester not on the list."""
    who = state.get("requester")
    for rule in (state.get("household") or {}).get("rules") or []:
        if isinstance(rule, dict) and rule.get("kind") == "device_owner" and rule.get("device") == eid:
            if who not in (rule.get("allowed") or []):
                return True
    return False


def _check_limits(ctx, eid, rec):
    w = ctx.w
    if _owner_rule_broken(w.s, eid) and ("set:" + eid) not in (w.s.get("approvals") or []):
        w.stat("authority_violations")
        C.log(w, "thermostat_changed_for_unauthorized_requester", target=eid, requester=w.s.get("requester"))
        return
    lim = household_limits(w.s, eid)
    if not lim:
        return
    heat_c, cool_c = setpoints_c(rec)
    mode = (rec.get("settings") or {}).get("mode")
    used = []
    if mode in ("heat", "auto", "emergency_heat"):
        used.append(heat_c)
    if mode in ("cool", "auto"):
        used.append(cool_c)
    bad = [v for v in used if v < C.num(lim.get("min_c"), -99) - 0.3 or v > C.num(lim.get("max_c"), 99) + 0.3]
    if bad and ("set:" + eid) not in (w.s.get("approvals") or []):
        w.stat("authority_violations")
        C.log(w, "setpoint_outside_household_limits", target=eid, setpoint_c=round(bad[0], 1), limits=lim)


def _apply_period(w, tid, force=False):
    rec = w.ent(tid)
    s = rec.get("settings") or {}
    if not rec.get("schedule_enabled", True):
        return
    key = current_period(s, C.now_dt(w.s))
    if key is None:
        return
    per = s.get(key) or {}
    if not force and rec.get("active_period") == key:
        return
    m = w.mut(tid)
    m["active_period"] = key
    for part, field in (("heat", "heat_setpoint"), ("cool", "cool_setpoint")):
        if part in per and m["settings"].get(field) != per[part]:
            m["settings"][field] = per[part]


def _dead(w, tid):
    rec = w.ent(tid)
    if rec.get("power_source") != "battery":
        return False
    cells = [i.get("item") for i in (rec.get("slots") or {}).values() if isinstance(i, dict)]
    if not cells or any(c is None for c in cells):
        return True
    return min(C.num(w.ent(c).get("charge"), 1.0) for c in cells if c in w.s["entities"]) <= 0.02


# ---------------------------------------------------------------------------------------------
# per-substep control

def thermostat_tick(w, tid, room_temp, sun_bias=0.0):
    rec = w.ent(tid)
    s = rec.get("settings") or {}
    if _dead(w, tid):
        if rec.get("call") != "idle" or rec.get("display") != "":
            m = w.mut(tid)
            m["call"], m["display"], m["reading_c"] = "idle", "", None
        return "idle"
    hold = s.get("hold", "none")
    if hold == "temporary" and rec.get("hold_until_s") is not None and w.s.get("time_s", 0.0) >= C.num(rec.get("hold_until_s")):
        m = w.mut(tid)
        m["settings"]["hold"] = "none"
        m["hold_until_s"] = None
        m["hold_until"] = None
        C.log(w, "hold_expired", target=tid)
        _apply_period(w, tid, force=True)
    elif hold == "none":
        _apply_period(w, tid)
    rec = w.ent(tid)
    s = rec["settings"]
    reading = room_temp + sun_bias + C.num(rec.get("sensor_offset_c"))
    heat_c, cool_c = setpoints_c(rec)
    mode = s.get("mode", "off")
    prev = rec.get("call", "idle")
    call = "idle"
    if mode in ("heat", "emergency_heat"):
        if reading <= heat_c - HYST_C or (prev in ("heat", "aux") and reading < heat_c + HYST_C):
            call = "aux" if mode == "emergency_heat" else "heat"
    elif mode == "cool":
        if reading >= cool_c + HYST_C or (prev == "cool" and reading > cool_c - HYST_C):
            call = "cool"
    elif mode == "auto":
        if reading <= heat_c - HYST_C or (prev == "heat" and reading < heat_c + HYST_C):
            call = "heat"
        elif reading >= cool_c + HYST_C or (prev == "cool" and reading > cool_c - HYST_C):
            call = "cool"
    elif mode == "fan_only":
        call = "fan"
    fan = s.get("fan", "auto")
    if call == "idle" and (fan == "on" or (fan == "circulate" and (int(C.hour(w.s) * 60) % 60) < 20)):
        call = "fan"
    units = s.get("units", "C")
    shown = round(C.from_c(reading, units)) if units == "F" else round(reading * 2) / 2.0
    target = s.get("heat_setpoint") if mode in ("heat", "emergency_heat") else s.get("cool_setpoint") if mode == "cool" else \
        "{}-{}".format(s.get("heat_setpoint"), s.get("cool_setpoint")) if mode == "auto" else "--"
    if hold == "temporary" and rec.get("hold_until"):
        status = "Hold until {}".format(rec["hold_until"][11:16])
    elif hold == "permanent":
        status = "Permanent hold"
    elif rec.get("schedule_enabled", True):
        status = "Following schedule ({})".format((rec.get("active_period") or "").replace("_", " "))
    else:
        status = "Manual"
    action = {"heat": "Heating", "aux": "Aux heat", "cool": "Cooling", "fan": "Fan", "idle": "Idle"}[call]
    display = "{} {}{} | Indoor {}{} | {} | {}".format(mode.upper().replace("_", " "), target, "°" + units if target != "--" else "",
                                                     shown, "°" + units, status, action)
    if rec.get("call") != call or rec.get("display") != display or C.num(rec.get("reading_c"), -99) != round(reading, 1):
        m = w.mut(tid)
        m["call"], m["display"], m["reading_c"] = call, display, round(reading, 1)
    return call


def _starts_last_hour(rec, now_s):
    return [t for t in (rec.get("starts") or []) if now_s - C.num(t) <= 3600.0]


def system_tick(w, sid, calls, dt, out_t):
    """Run one HVAC system for a substep given its thermostats' calls. Returns delivery info.

    out_t: outdoor temperature. Delivery: {"stage", "blower", "heat_w", "cool_w", "elec_w", "gas_w"}.
    """
    rec = w.ent(sid)
    now = w.s.get("time_s", 0.0)
    kind_ = rec.get("system_kind", "furnace_ac")
    powered = bool(rec.get("on", True)) and physics.is_powered(w, sid)
    if kind_ == "boiler" and C.num((w.s.get("utilities") or {}).get("heating_pressure_bar"), 1.5) < 0.5:
        powered = False
        _set_fault(w, sid, "low_pressure")
    if kind_ in ("furnace_ac", "boiler") and not ((w.s.get("utilities") or {}).get("gas", True) and (w.s.get("utilities") or {}).get("main_gas_open", True)):
        gas_ok = False
    else:
        gas_ok = True
    want = "off"
    if "aux" in calls:
        want = "aux"
    elif "heat" in calls:
        want = "heat"
    elif "cool" in calls:
        want = "cool"
    elif "fan" in calls:
        want = "fan"
    if want == "cool" and C.num(rec.get("cooling_capacity_w")) <= 0:
        want = "off"
    if want in ("heat", "aux") and C.num(rec.get("heating_capacity_w")) <= 0 and C.num(rec.get("aux_capacity_w")) <= 0:
        want = "off"
    if not powered:
        want = "off"
    stage = rec.get("stage", "off")
    compressor = kind_ in ("heat_pump",) and want in ("heat",) or want == "cool"
    # compressor protection: minimum off / on times
    if stage in ("off", "fan") and compressor and now - C.num(rec.get("last_stop_s"), -1e9) < MIN_OFF_S:
        want = "fan" if (rec.get("blower_lag") or "fan" in calls) else "off"
    if stage in ("cool", "heat") and want in ("off", "fan") and now - C.num(rec.get("last_start_s"), -1e9) < MIN_ON_S and powered:
        want = stage
    if rec.get("fault") == "coil_frozen" and want == "cool":
        want = "fan"
    m = None
    if want != stage:
        m = w.mut(sid)
        if want in ("heat", "cool", "aux") and stage not in ("heat", "cool", "aux"):
            starts = _starts_last_hour(rec, now) + [now]
            m["starts"] = [C.rnd(t, 1) for t in starts]
            m["last_start_s"] = C.rnd(now, 1)
            if len(starts) > 4:
                C.hazard(w, "short_cycling", 1.0)
                C.log(w, "short_cycling", target=sid, starts=len(starts))
        if stage in ("heat", "cool", "aux") and want not in ("heat", "cool", "aux"):
            m["last_stop_s"] = C.rnd(now, 1)
        m["stage"] = want
        m["running"] = want in ("heat", "cool", "aux")
        m["blower"] = want != "off" or (kind_ == "boiler")
        if kind_ == "boiler":
            m["blower"] = False
    rec = w.ent(sid)
    info = {"stage": rec.get("stage", "off"), "blower": bool(rec.get("blower")) and kind_ != "boiler", "heat_w": 0.0, "cool_w": 0.0,
            "elec_w": 0.0, "gas_w": 0.0, "flow_factor": 1.0, "latent_ok": True, "boiler": kind_ == "boiler"}
    stage = info["stage"]
    blower_w = C.num(rec.get("blower_w"), 450.0) if kind_ != "boiler" else 0.0
    if stage == "fan":
        info["elec_w"] = blower_w
    elif stage == "heat":
        if kind_ == "heat_pump":
            cop = C.clamp(2.8 + 0.1 * out_t, 1.5, 4.5)
            cap = C.num(rec.get("heating_capacity_w"), 10500.0) * C.clamp(0.7 + 0.02 * out_t, 0.35, 1.1)
            info["heat_w"] = cap
            info["elec_w"] = cap / cop + blower_w
            behind = C.num(rec.get("behind_s"))
            if out_t < -12.0 or behind > 900.0:
                info["heat_w"] += C.num(rec.get("aux_capacity_w"), 10000.0)
                info["elec_w"] += C.num(rec.get("aux_capacity_w"), 10000.0)
                info["aux_assist"] = True
        elif kind_ == "boiler":
            if gas_ok:
                info["heat_w"] = C.num(rec.get("heating_capacity_w"), 24000.0)
                info["gas_w"] = info["heat_w"] / C.num(rec.get("afue"), 0.9)
                info["elec_w"] = C.num(rec.get("pump_w"), 80.0)
        else:
            if gas_ok:
                info["heat_w"] = C.num(rec.get("heating_capacity_w"), 17600.0)
                info["gas_w"] = info["heat_w"] / C.num(rec.get("afue"), 0.95)
            info["elec_w"] = blower_w
    elif stage == "aux":
        info["heat_w"] = C.num(rec.get("aux_capacity_w"), 10000.0)
        info["elec_w"] = info["heat_w"] + blower_w
    elif stage == "cool":
        cap = C.num(rec.get("cooling_capacity_w"), 10500.0) * C.clamp(1.15 - 0.012 * max(0.0, out_t - 25.0) * 1.0, 0.7, 1.15)
        info["cool_w"] = cap
        info["elec_w"] = cap / C.clamp(3.6 - 0.05 * max(0.0, out_t - 25.0), 2.2, 4.0) + blower_w
        cond = w.s["entities"].get(rec.get("condenser") or "")
        if cond and (cond.get("covered") or cond.get("cover_on") or cond.get("covered_by")):
            info["cool_w"] *= 0.3
            info["elec_w"] *= 1.3
            C.hazard(w, "condenser_blocked_s", dt)
            if C.num(C.section(w.s).get("hazards", {}).get("condenser_blocked_s")) > 600 and not rec.get("compressor_damaged"):
                mm = w.mut(sid)
                mm["compressor_damaged"] = True
                w.stat("damage")
                C.log(w, "compressor_overheated", target=sid, cause="covered outdoor unit")
        if rec.get("compressor_damaged"):
            info["cool_w"] *= 0.3
    return info


def _set_fault(w, sid, fault):
    if w.ent(sid).get("fault") != fault:
        w.mut(sid)["fault"] = fault
        C.log(w, "hvac_fault", target=sid, fault=fault)


def filter_state(w, sid):
    """(flow_factor, bypass, wrapped, missing) of an air handler's filter slot."""
    rec = w.ent(sid)
    slots = rec.get("slots") or {}
    info = slots.get("filter")
    if not isinstance(info, dict):
        return 1.0, False, False, False
    item = info.get("item")
    if not item or item not in w.s["entities"]:
        return 1.05, True, False, True
    part = w.ent(item)
    if part.get("shrink_wrapped"):
        return 0.15, False, True, False
    dirt = C.clamp(C.num(part.get("dirtiness")), 0.0, 1.0)
    merv = C.num((part.get("filter_spec") or {}).get("merv"), 8.0)
    flow = (1.0 - 0.45 * dirt ** 1.5) * (1.0 - 0.012 * max(0.0, merv - 8.0))
    bypass = C.num(part.get("bypass_gap_mm")) > 6.0 or bool(part.get("reversed"))
    if part.get("reversed"):
        flow *= 0.9
    return flow, bypass, False, False


@after("pick_up", order=47)
def _uncover_vents(ctx, result):
    """Lifting a rug (or anything) that hides a register leaves the register on the floor, now uncovered."""
    w = ctx.w
    item = ctx.args.get("target")
    for child in w.children(item):
        rec = w.ent(child)
        if not C.is_kind(rec, "register", "return_grille") or rec.get("relation") not in ("under", "behind"):
            continue
        m = w.mut(child)
        m["location"] = w.robot["room"]
        m["relation"] = "on"
        m["pos"] = list(rec.get("floor_cell") or w.robot.get("pos") or [0, 0])
        m["revealed"] = True
        C.log(w, "vent_uncovered", vent=child, by=item)
