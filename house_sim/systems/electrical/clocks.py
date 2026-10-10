"""Clocks, alarm clocks and appliance clocks (fidelity S).

Any entity with a `clock` dict {offset_s, format_24h, auto_sync, volatile, battery_backup,
running, blinking} shows local time + offset_s (normalized to +-12 h). Alarm clocks also hold
`alarms` {name: {time "HH:MM" 24 h, enabled, days daily|weekdays|weekends, owner, sound, volume}}.

* set_time(target, time, ampm, mode=clock|alarm, alarm, enabled, days, format): AM/PM is
  required on 12-hour clocks for hours 1-12. A clock with ui "unfamiliar" swaps the time-set and
  alarm-set button sequences unless its instructions/manual were read first (core read), so a
  guessed sequence changes the wrong thing. Auto-sync clocks re-sync daily at 03:00; a manual
  shift on them stays wrong until then.
* Power loss: volatile clocks without battery backup stop, then restart at a blinking 12:00 and
  lose their alarms; battery wall clocks stop when their cell is flat (offset drifts by the
  outage length) and must be reset after a battery change.
* Alarms ring when the clock's own displayed time passes the alarm time on a matching day
  (so a wrong clock time or PM instead of AM makes the alarm ring at the wrong moment).
"""
from ...kernel import clock as kclock
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import system
from . import common as C
from . import power as P


def has_clock(rec):
    return isinstance(rec.get("clock"), dict)


def norm(offset):
    offset = float(offset) % 86400.0
    return offset - 86400.0 if offset > 43200.0 else offset


def displayed_seconds(state, rec):
    return (C.local_seconds(state) + C.num((rec.get("clock") or {}).get("offset_s"), 0.0)) % 86400


def display_text(state, rec):
    clk = rec.get("clock") or {}
    if rec.get("power_state") == "off" and rec.get("plug_required") and not clk.get("battery_backup"):
        return ""
    s = int(displayed_seconds(state, rec))
    h, m = s // 3600, (s % 3600) // 60
    if clk.get("format_24h"):
        text = "{:02d}:{:02d}".format(h, m)
    else:
        text = "{}:{:02d} {}".format(12 if h % 12 == 0 else h % 12, m, "AM" if h < 12 else "PM")
    return text + (" (blinking)" if clk.get("blinking") else "")


def _day_matches(state, days, rec):
    now = kclock.now(state)
    shown = displayed_seconds(state, rec)
    import datetime
    day = (now + datetime.timedelta(seconds=shown - C.local_seconds(state))).weekday()
    if days == "weekdays":
        return day < 5
    if days == "weekends":
        return day >= 5
    return True


@system("electrical.clocks", order=66, max_dt=30.0)
def clocks_system(w, dt, tick):
    state = w.s
    if not C.electrified(state, w):
        return
    ents = state["entities"]
    clocks = C.ids(w, "clocks")
    if not clocks:
        return
    now_local = C.local_seconds(state)
    for eid in clocks:
        rec = ents.get(eid)
        if rec is None or not has_clock(rec):
            continue
        clk = rec["clock"]
        running = _running(state, eid, rec)
        if not running:
            m = w.mut(eid)
            m["clock"]["offset_s"] = round(norm(C.num(clk.get("offset_s"), 0.0) - dt), 1)
            if clk.get("running", True):
                m["clock"]["running"] = False
            continue
        if not clk.get("running", True):
            w.mut(eid)["clock"]["running"] = True
        if clk.get("auto_sync") and C.num(clk.get("offset_s"), 0.0) != 0.0:
            prev = (now_local - dt) % 86400
            if prev <= 3 * 3600 < prev + dt or (prev > now_local and now_local >= 3 * 3600 and prev - 86400 + dt >= 3 * 3600):
                w.mut(eid)["clock"]["offset_s"] = 0.0
        # alarms
        alarms = rec.get("alarms") or {}
        shown_now = displayed_seconds(state, rec)
        shown_prev = (shown_now - dt) % 86400
        for name in sorted(alarms):
            alarm = alarms[name]
            if not alarm.get("enabled"):
                continue
            hm = C.parse_hhmm(alarm.get("time", ""))
            if hm is None:
                continue
            t = hm[0] * 3600 + hm[1] * 60
            crossed = (shown_prev < t <= shown_now) if shown_prev <= shown_now else (t > shown_prev or t <= shown_now)
            if crossed and _day_matches(state, alarm.get("days", "daily"), rec):
                m = w.mut(eid)
                m["ringing"] = True
                m["ringing_alarm"] = name
                m["ring_until_s"] = state["time_s"] + 600.0
                m.setdefault("rang", []).append({"alarm": name, "time_s": state["time_s"], "shown": C.fmt_hhmm(t)})
                C.event(w, "alarm_clock_ringing", eid, alarm=name)
        if rec.get("ringing") and C.num(rec.get("ring_until_s"), 0) <= state["time_s"]:
            w.mut(eid)["ringing"] = False
    sync_clock_display(w)


def _running(state, eid, rec):
    clk = rec.get("clock") or {}
    if rec.get("battery_powered") and not rec.get("plug_required"):
        return bool(rec.get("functional", C.num(rec.get("battery_level"), 1.0) > 0))
    if rec.get("plug_required") or rec.get("hardwired") or rec.get("appliance") is not None or clk.get("volatile"):
        if P.supply_ok(state, eid):
            return True
        return bool(clk.get("battery_backup"))
    return True


@P.on_power_lost(60)
def _clock_power_lost(w, eid, cause):
    rec = w.ent(eid)
    clk = rec.get("clock")
    if not isinstance(clk, dict) or clk.get("battery_backup") or clk.get("auto_sync"):
        return
    if clk.get("volatile", True):
        m = w.mut(eid)
        m["clock"]["blinking"] = True
        m["clock"]["lost_power_s"] = w.s["time_s"]
        for name in sorted(m.get("alarms") or {}):
            if m["alarms"][name].get("enabled"):
                m["alarms"][name]["enabled"] = False
                m["alarms"][name]["lost_in_outage"] = True


@P.on_power_restored(60)
def _clock_power_restored(w, eid):
    rec = w.ent(eid)
    clk = rec.get("clock")
    if not isinstance(clk, dict) or clk.get("battery_backup") or clk.get("auto_sync"):
        return
    if clk.get("blinking"):
        m = w.mut(eid)
        m["clock"]["offset_s"] = round(norm(-C.local_seconds(w.s)), 1)   # restarts at 12:00 AM
        m["clock"]["running"] = True


def parse_time(text, ampm, format_24h):
    hm = C.parse_hhmm(text)
    require(hm is not None, "Give the time as H:MM or HH:MM", "argument")
    h, m = hm
    if ampm:
        require(1 <= h <= 12, "With am/pm use an hour from 1 to 12", "argument")
        h = (0 if h == 12 else h) if ampm == "am" else (12 if h == 12 else h + 12)
    elif not format_24h and 1 <= h <= 12:
        raise ActionError("This clock shows AM/PM: say am or pm", "argument")
    return h * 3600 + m * 60


def _manual_known(state, eid, rec):
    if rec.get("ui", "standard") != "unfamiliar":
        return True
    read = state.get("facts", {}).get("read", {})
    if eid in read:
        return True
    manual = rec.get("manual")
    return bool(manual and manual in read)


@action("set_time", params={"target": "entity", "time": "?str", "ampm": "?str:am|pm", "mode": "?str:clock|alarm", "alarm": "?str",
                            "enabled": "?bool", "days": "?str:daily|weekdays|weekends", "format": "?str:12h|24h"},
        duration=lambda ctx: ctx.data.get("seconds", 15.0), energy=0.005, category="physical",
        doc="Set a clock: mode=clock sets the displayed time; mode=alarm sets alarm `alarm` (default the first) to time, and "
            "enabled/days switch it on or off. 12-hour clocks need ampm. format=12h|24h changes the display. Unfamiliar clocks "
            "need their instructions read first, or the button sequence changes the wrong setting.")
def set_time(ctx):
    w = ctx.w
    state = w.s
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(has_clock(rec), "It has no settable clock", "argument")
    mode = ctx.args.get("mode") or "clock"
    clk = rec["clock"]
    if ctx.args.get("format"):
        w.mut(eid)["clock"]["format_24h"] = ctx.args["format"] == "24h"
        clk = w.ent(eid)["clock"]
    known = _manual_known(state, eid, rec)
    effective = mode if known else ("alarm" if mode == "clock" else "clock")
    if ctx.args.get("time") is None and ctx.args.get("enabled") is None and ctx.args.get("days") is None:
        require(ctx.args.get("format"), "Give a time, enabled or days", "argument")
        return "Display format set to " + ctx.args["format"]
    seconds = 10.0
    message = []
    if ctx.args.get("time") is not None:
        target_s = parse_time(ctx.args["time"], ctx.args.get("ampm"), clk.get("format_24h"))
        if effective == "clock":
            offset = norm(target_s - C.local_seconds(state))
            m = w.mut(eid)
            m["clock"]["offset_s"] = round(offset, 1)
            m["clock"]["blinking"] = False
            message.append("clock set to " + C.fmt_hhmm(target_s))
        else:
            alarms = rec.get("alarms")
            require(isinstance(alarms, dict) and alarms, "This clock has no alarm", "argument")
            name = ctx.args.get("alarm") or sorted(alarms)[0]
            require(name in alarms, "No alarm named " + name, "argument")
            m = w.mut(eid)
            m["alarms"][name]["time"] = C.fmt_hhmm(target_s)
            m["alarms"][name].pop("lost_in_outage", None)
            message.append("{} set to {}".format(name, C.fmt_hhmm(target_s)))
        seconds += 10.0
    if ctx.args.get("enabled") is not None or ctx.args.get("days") is not None:
        alarms = w.ent(eid).get("alarms")
        require(isinstance(alarms, dict) and alarms, "This clock has no alarm", "argument")
        name = ctx.args.get("alarm") or sorted(alarms)[0]
        require(name in alarms, "No alarm named " + name, "argument")
        m = w.mut(eid)
        if ctx.args.get("enabled") is not None:
            m["alarms"][name]["enabled"] = bool(ctx.args["enabled"])
            if not ctx.args["enabled"] and m.get("ringing_alarm") == name:
                m["ringing"] = False
            message.append("{} {}".format(name, "on" if ctx.args["enabled"] else "off"))
        if ctx.args.get("days"):
            m["alarms"][name]["days"] = ctx.args["days"]
            message.append("{} days {}".format(name, ctx.args["days"]))
    if not known:
        C.log(w, "unfamiliar_clock_buttons", clock=eid, intended=mode, changed=effective)
    ctx.data["seconds"] = seconds
    C.refresh(w)
    return "; ".join(message) or "No change"


@C.refresher(60)
def sync_clock_display(w):
    state = w.s
    for eid in C.ids(w, "clocks"):
        rec = state["entities"].get(eid)
        if rec is not None and has_clock(rec):
            C.set_if(w, eid, "display_time", display_text(state, rec))
