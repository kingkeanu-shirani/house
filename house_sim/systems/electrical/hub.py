"""The smart-home layer (TIER1_SWEEP 15 "Smart-home layer"; kernel v3.1 notice bus): the household's hub or app, its
scenes, automations (schedules) and away mode, the state it shows in the app, and the notifications it sends.

Data-gated: everything here acts only in a house whose state has a `smart_home` section (generation-2 houses of the
basic and connected smart-home tiers, smarthome.py; or the `electrical.smart_home` setup helper). Without one the
system's idle probe is exact and nothing is read or written, so generation-1 houses and older states never change.

    state["smart_home"] = {
      "tier":        "basic" (Wi-Fi devices in a phone app; the app's cloud runs the schedules) |
                     "connected" (a hub on the router; Zigbee bulbs talk only to the hub, which runs the automations),
      "hub":         the smart_hub entity (connected) or None,
      "app":         the phone the devices are set up in (basic) or None,
      "devices":     {eid: {"name": "living room lamp", "room": rid, "kind": plug | bulb | camera | speaker | hub}},
      "scenes":      {name: [action, ...]},
      "automations": [{"id", "name", "trigger", "actions": [action, ...], "enabled": true, "days": [weekday, ...]?,
                       "notify": bool?}],
      "away_mode":   bool,
      "log":         the last 50 automation runs [{"time_s", "automation", "done": [eid...], "failed": [eid...]}],
      "notifications": the app's inbox, the last 50 [{"time_s", "kind", "device"?, "text"}]}

    action   {"device": eid, "setting": s, "value": v}   a device setting through the app (smart plug relay on / off,
                                                          smart bulb power on / off and brightness 1-100)
             {"scene": name}                              every action of a scene
             {"away_mode": true | false}
    trigger  {"time": "HH:MM"}                            the wall clock (daylight saving time when the house keeps it)
             {"sunset": minutes} / {"sunrise": minutes}    offset from the day's sunset / sunrise (kernel clock)
             {"state": predicate}                          the predicate becoming true (a rising edge)
             {"away": true | false}                        away mode switched on / off

The robot reaches the layer through the household app over the network: core `set` with the app control kind on a
device (`relay`, `power`, `brightness`; lighting._smart_offline refuses an offline or unpaired one) or on the hub
(`away_mode`, `scene`: refused while the hub is offline), and the observation `obs.smart_home_app` (devices, online,
on / off, away mode, scenes, automations, the latest notifications), present only while the robot has its network.
Paired devices carry `remote_status` (their glance state is perceivable over the network: waits may watch it).

A device answers the app or the hub only while it is online: a plug whose receptacle is live, a smart bulb whose
fixture is switched on and powered, a camera or speaker that is on and powered; the hub on and powered; Wi-Fi devices
and the app's cloud also need the household router up (devices.network_ok). An automation that cannot reach a device
logs it as failed and posts a `device` notice and an app notification ("<name> is not responding"). Away mode turns
on the security cameras' motion alerts: anyone (a resident, a pet, the robot itself) coming into a camera's room
posts a `device` notice and a notification, at most once per 5 minutes per camera.
"""
import copy

from ...kernel.plugins import after, before, observer, system
from ...kernel.errors import ActionError
from . import common as C
from . import power as P

SECTION = "smart_home"
LOG_MAX = 50
MOTION_COOLDOWN_S = 300.0
APP_SETTINGS = {"plug": ("relay",), "bulb": ("power", "brightness", "cct_k")}
TRIGGERS = ("time", "sunset", "sunrise", "state", "away")


def section(state):
    """The smart-home section, or None in a house without one."""
    sec = state.get(SECTION)
    return sec if isinstance(sec, dict) else None


def device_kind(rec):
    if rec.get("outlet_kind") == "smart_plug":
        return "plug"
    if rec.get("category") == "light_bulb" and rec.get("smart"):
        return "bulb"
    if rec.get("category") == "security":
        return "camera"
    if rec.get("category") == "hub":
        return "hub"
    return "speaker" if rec.get("category") == "audio" else "device"


def default_name(state, eid):
    """The app's name of a device: "<room type> lamp" for a plug feeding a lamp or a bulb in one, else "<room type>
    <label>"."""
    ents = state["entities"]
    rec = ents.get(eid) or {}
    rid = C.room_of(state, eid)
    room = ((state.get("rooms") or {}).get(rid) or {}).get("type") or rid or "home"
    kind = device_kind(rec)
    if kind == "bulb" or (kind == "plug" and any((r.get("outlet") == eid and r.get("category") == "light")
                                                  for r in ents.values())):
        what = "lamp"
    else:
        what = {"plug": "plug", "camera": "camera", "speaker": "speaker"}.get(kind, rec.get("label") or "device")
    return "{} {}".format(str(room).replace("_", " "), what)


def make_section(config, tier, hub=None, app=None, devices=(), scenes=None, automations=None, away_mode=False):
    """The smart_home section of a config (generation 2 or a setup helper): devices named by default_name unless
    `devices` gives {eid: name}."""
    names = devices if isinstance(devices, dict) else {eid: None for eid in devices}
    out = {"tier": tier, "hub": hub, "app": app, "devices": {}, "scenes": dict(scenes or {}),
           "automations": list(automations or ()), "away_mode": bool(away_mode), "log": [], "notifications": []}
    taken = {}
    for eid in sorted(names):
        rec = config["entities"].get(eid)
        if rec is None:
            continue
        name = names[eid] or (rec.get("pairing") or {}).get("name") or default_name(config, eid)
        taken[name] = taken.get(name, 0) + 1
        if taken[name] > 1:
            name = "{} {}".format(name, taken[name])        # a second living-room lamp is "living room lamp 2"
        out["devices"][eid] = {"name": name, "room": C.room_of(config, eid), "kind": device_kind(rec)}
    config[SECTION] = out
    hub_rec = config["entities"].get(hub) if hub else None
    if hub_rec is not None:
        hub_rec["settings"] = dict(hub_rec.get("settings") or {}, away_mode=bool(away_mode))
        options = dict(hub_rec.get("setting_options") or {})
        options["scene"] = ["none"] + sorted(out["scenes"])
        hub_rec["setting_options"] = options
    return out


def register_device(w, eid, host, name=None, room=None):
    """A device paired with the house's hub or app (pair_device) joins the smart-home section."""
    sec = section(w.s)
    if sec is None or host not in (sec.get("hub"), sec.get("app")):
        return
    rec = w.s["entities"][eid]
    entry = {"name": name or default_name(w.s, eid), "room": room or C.room_of(w.s, eid), "kind": device_kind(rec)}
    if (sec.get("devices") or {}).get(eid) != entry:
        w.sec(SECTION).setdefault("devices", {})[eid] = entry


def forget_device(w, eid):
    sec = section(w.s)
    if sec is not None and eid in (sec.get("devices") or {}):
        del w.sec(SECTION)["devices"][eid]


def _router_up(state, w=None):
    from .devices import network_ok
    return network_ok(state, w) is not False


def hub_online(state, w=None):
    """The hub (connected tier) is on and powered, or the app's cloud (basic tier) reaches the house's router."""
    sec = section(state) or {}
    hub = sec.get("hub")
    if hub:
        rec = state["entities"].get(hub)
        return bool(rec is not None and not C.dead(rec) and rec.get("power_state", "on") == "on"
                    and P.supply_ok(state, hub))
    return _router_up(state, w)


def device_online(state, eid, w=None):
    """True when the device answers the hub / app now (see the module doc)."""
    rec = state["entities"].get(eid)
    if rec is None or C.dead(rec) or (rec.get("pairing") or {}).get("state", "paired") != "paired":
        return False
    kind = device_kind(rec)
    if kind == "bulb":
        parent = state["entities"].get(rec.get("location")) or {}
        if not (parent.get("on") and parent.get("powered", True) and parent.get("energized", True)):
            return False
        if rec.get("burnt_out") or rec.get("broken"):
            return False
    elif kind == "plug":
        if not P.supply_ok(state, eid):
            return False
    elif rec.get("power_state", "on") != "on" or not P.supply_ok(state, eid):
        return False
    protocol = (rec.get("pairing") or {}).get("protocol", "wifi")
    if protocol == "wifi" and not _router_up(state, w):
        return False
    sec = section(state) or {}
    if protocol != "wifi" and sec.get("hub") and not hub_online(state, w):
        return False
    return True


def _notify(w, kind, text, device=None, notice=True):
    """An app notification (the residents' phones; the household inbox) and, when notice, a `device` notice to the
    robot over the network."""
    sec = w.sec(SECTION)
    inbox = sec.setdefault("notifications", [])
    entry = {"time_s": w.s.get("time_s", 0.0), "kind": kind, "text": text}
    if device:
        entry["device"] = device
    inbox.append(entry)
    if len(inbox) > LOG_MAX:
        del inbox[:len(inbox) - LOG_MAX]
    if notice:
        C.notice(w, "device", device, remote=True, data={"app": kind, "text": text},
                 room=C.room_of(w.s, device) if device else None)


def _name(state, eid):
    sec = section(state) or {}
    info = (sec.get("devices") or {}).get(eid) or {}
    return info.get("name") or (state["entities"].get(eid) or {}).get("label") or eid


def _valid(rec, setting, value):
    settings = rec.get("settings")
    if not isinstance(settings, dict) or setting not in settings:
        return False
    options = (rec.get("setting_options") or {}).get(setting)
    if isinstance(options, list):
        return value in options
    if isinstance(options, dict):
        return isinstance(value, (int, float)) and not isinstance(value, bool) \
            and options.get("min", float("-inf")) <= value <= options.get("max", float("inf"))
    return True


def apply_actions(w, actions, depth=0):
    """Run a list of actions through the hub / app; returns (done, failed) device ids (sorted, unique)."""
    state = w.s
    done, failed = set(), set()
    for act in actions or ():
        if not isinstance(act, dict):
            continue
        if "scene" in act and depth < 3:
            scene = ((section(state) or {}).get("scenes") or {}).get(act["scene"])
            d, f = apply_actions(w, scene or (), depth + 1)
            done.update(d)
            failed.update(f)
        elif "away_mode" in act:
            set_away(w, bool(act["away_mode"]), depth + 1)
        elif act.get("device") in state["entities"]:
            eid, setting, value = act["device"], act.get("setting"), act.get("value")
            rec = state["entities"][eid]
            if not device_online(state, eid, w) or not _valid(rec, setting, value):
                failed.add(eid)
                continue
            done.add(eid)
            if rec["settings"].get(setting) != value:
                w.mut(eid)["settings"][setting] = copy.deepcopy(value)
        elif act.get("device"):
            failed.add(act["device"])
    if done:
        C.refresh(w)
    return sorted(done), sorted(failed - done)


def set_away(w, on, depth=0):
    """Switch away mode (the app's toggle, an automation or the hub's own setting) and run the away automations."""
    sec = section(w.s)
    if sec is None or bool(sec.get("away_mode")) == bool(on):
        return
    w.sec(SECTION)["away_mode"] = bool(on)
    hub = sec.get("hub")
    if hub in w.s["entities"] and (w.s["entities"][hub].get("settings") or {}).get("away_mode") != bool(on):
        w.mut(hub)["settings"]["away_mode"] = bool(on)
    _notify(w, "away_mode", "Away mode is {}".format("on" if on else "off"), device=hub, notice=False)
    if depth < 3:
        for auto in _automations(w.s):
            trig = auto.get("trigger") or {}
            if "away" in trig and bool(trig["away"]) == bool(on):
                _run(w, auto, depth + 1)


def _automations(state):
    return [a for a in ((section(state) or {}).get("automations") or ()) if isinstance(a, dict) and a.get("enabled", True)]


def _run(w, auto, depth=0):
    state = w.s
    sec = w.sec(SECTION)
    if not hub_online(state, w):
        done, failed = [], sorted({a.get("device") for a in auto.get("actions") or () if isinstance(a, dict) and a.get("device")})
    else:
        done, failed = apply_actions(w, auto.get("actions"), depth)
    log = sec.setdefault("log", [])
    log.append({"time_s": state.get("time_s", 0.0), "automation": auto.get("id"), "done": done, "failed": failed})
    if len(log) > LOG_MAX:
        del log[:len(log) - LOG_MAX]
    C.event(w, "automation_ran", sec.get("hub"), automation=auto.get("id"), failed=failed)
    for eid in failed:
        _notify(w, "not_responding", "{} is not responding ({})".format(_name(state, eid), auto.get("name") or auto.get("id")),
                device=eid if eid in state["entities"] else None)
    if auto.get("notify") and done:
        _notify(w, "automation", "{} ran".format(auto.get("name") or auto.get("id")), device=sec.get("hub"))


def _hm(text):
    try:
        h, m = str(text).split(":")[:2]
        return (int(h) % 24) * 3600 + int(m) * 60
    except (TypeError, ValueError):
        return None


def _crossed(target, now_s, dt):
    """The second-of-day `target` lies in (now - dt, now] (wrapping at midnight)."""
    if target is None or dt <= 0:
        return False
    start = (now_s - dt) % 86400.0
    if dt >= 86400.0:
        return True
    if start < now_s:
        return start < target <= now_s
    return target > start or target <= now_s


def _due(w, auto, dt):
    """True when a timed trigger fell in this substep (and its weekday filter allows today)."""
    from ...kernel import clock
    state = w.s
    trig = auto.get("trigger") or {}
    days = auto.get("days")
    if days and clock.weekday(state).lower() not in [str(d).lower() for d in days]:
        return False
    if "time" in trig:
        return _crossed(_hm(trig["time"]), C.local_seconds(state), dt)
    if "sunset" in trig or "sunrise" in trig:
        rise, set_ = clock.sun_times(state)
        base, offset = (set_, trig["sunset"]) if "sunset" in trig else (rise, trig["sunrise"])
        target = (base * 3600.0 + C.num(offset, 0.0) * 60.0) % 86400.0
        now = clock.now(state)
        return _crossed(target, now.hour * 3600 + now.minute * 60 + now.second, dt)
    return False


def _edges(w):
    """State triggers: the automations whose predicate became true since the last substep (first look: no edge)."""
    from ...kernel.predicates import check
    state = w.s
    sec = section(state)
    known = sec.get("_edges") or {}
    out, now = [], {}
    for auto in _automations(state):
        rule = (auto.get("trigger") or {}).get("state")
        if not isinstance(rule, dict) or not auto.get("id"):
            continue
        try:
            truth = bool(check(state, rule))
        except Exception:      # a malformed predicate never stops the house: it simply never fires
            truth = False
        now[auto["id"]] = truth
        if truth and known.get(auto["id"]) is False:
            out.append(auto)
    if now != known:
        w.sec(SECTION)["_edges"] = now
    return out


def _motion(w):
    """Away mode: someone coming into a security camera's room makes it alert (at most once per cooldown)."""
    state = w.s
    sec = section(state)
    if not sec.get("away_mode"):
        if sec.get("_occupied"):
            w.sec(SECTION)["_occupied"] = {}
        return
    cams = [e for e, info in sorted((sec.get("devices") or {}).items()) if info.get("kind") == "camera"
            and e in state["entities"]]
    if not cams:
        return
    rooms = {}
    for group in ("people", "pets"):
        for pid, rec in sorted((state.get(group) or {}).items()):
            if rec.get("location") in state["rooms"]:
                rooms.setdefault(rec["location"], []).append(pid)
    if state["robot"].get("room"):
        rooms.setdefault(state["robot"]["room"], []).append("robot")
    occupied = {}
    alerts = []
    before = sec.get("_occupied") or {}
    last = sec.get("_alert_s") or {}
    now = state.get("time_s", 0.0)
    for cam in cams:
        rid = C.room_of(state, cam)
        who = sorted(rooms.get(rid) or ())
        occupied[cam] = who
        new = [x for x in who if x not in (before.get(cam) or ())]
        if new and device_online(state, cam, w) and now - C.num(last.get(cam), -1e9) >= MOTION_COOLDOWN_S:
            alerts.append(cam)
    if occupied != before:
        w.sec(SECTION)["_occupied"] = occupied
    for cam in alerts:
        w.sec(SECTION).setdefault("_alert_s", {})[cam] = now
        _notify(w, "motion", "Motion detected by the {}".format(_name(state, cam)), device=cam)


def hub_idle(w):
    """Exact: without a smart-home section the system reads and writes nothing."""
    return section(w.s) is None


@system("electrical.smart_home", order=55, max_dt=30.0, idle=hub_idle)
def smart_home_system(w, dt, tick):
    if section(w.s) is None:
        return
    fired = [a for a in _automations(w.s) if _due(w, a, dt)] + _edges(w)
    for auto in fired:
        _run(w, auto)
    _motion(w)


# ----- the robot's app ----------------------------------------------------------------------------------------------

@before("set", order=43)
def _hub_offline(ctx):
    """Away mode and scenes go through the hub (or the app's cloud): refused while it is offline."""
    state = ctx.w.s
    sec = section(state)
    target = ctx.args.get("target")
    rec = state["entities"].get(target) or {}
    if sec is None or rec.get("category") != "hub" or ctx.args.get("setting") not in ("away_mode", "scene"):
        return
    if not ctx.w.robot.get("network", True):
        raise ActionError("No network connection to reach the hub", "resource")
    if not hub_online(state, ctx.w):
        raise ActionError("The hub is offline (no power)" if sec.get("hub") == target else "The app cannot reach the house",
                          "precondition")


@after("set", order=43)
def _hub_set(ctx, result):
    w = ctx.w
    sec = section(w.s)
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if sec is None or rec.get("category") != "hub":
        return
    setting, value = ctx.args.get("setting"), ctx.args.get("value")
    if setting == "away_mode":
        set_away(w, bool(value))
    elif setting == "scene" and value in (sec.get("scenes") or {}):
        done, failed = apply_actions(w, [{"scene": value}])
        if failed:
            ctx.note("Not responding: " + ", ".join(_name(w.s, e) for e in failed))


def app_view(state, w=None):
    """What the household app shows (see the module doc)."""
    sec = section(state)
    if sec is None:
        return None
    ents = state["entities"]
    devices = []
    for eid, info in sorted((sec.get("devices") or {}).items()):
        rec = ents.get(eid)
        if rec is None:
            continue
        entry = {"name": info.get("name"), "room": info.get("room"), "kind": info.get("kind"),
                 "online": device_online(state, eid, w), "device": eid}
        settings = rec.get("settings") or {}
        for key in APP_SETTINGS.get(info.get("kind"), ()):
            if key in settings:
                entry[key] = settings[key]
        devices.append(entry)
    out = {"away_mode": bool(sec.get("away_mode")), "devices": devices, "scenes": sorted(sec.get("scenes") or {}),
           "automations": [{"name": a.get("name") or a.get("id"), "enabled": bool(a.get("enabled", True)),
                            "trigger": a.get("trigger")} for a in sec.get("automations") or () if isinstance(a, dict)],
           "notifications": [dict(n) for n in (sec.get("notifications") or ())[-5:]]}
    if sec.get("hub"):
        out["hub_online"] = hub_online(state, w)
    return out


@observer("electrical.smart_home_app", order=122)
def _app_observer(env, w, obs):
    """obs.smart_home_app (merged with mechanisms' smart locks and garage doors): only while the robot has its
    network and the house has a smart-home section."""
    if section(w.s) is None or not w.robot.get("network", True):
        return
    view = app_view(w.s, w)
    if view:
        obs.setdefault("smart_home_app", {}).update(view)
