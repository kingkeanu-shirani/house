"""Electronics state machines: TVs, soundbars, sources, consoles, computers, network gear,
always-on and critical devices (fidelity S, with G line-of-sight for IR remotes).

* power_state: off / standby / booting / on (computers also run jobs; consoles also `rest`).
  switch_on boots (boot_s); switch_off is the proper way down: a computer's OS shutdown is
  blocked by unsaved work or an installing update, a console goes to rest mode and keeps
  updating. Cutting mains power instead (unplug, strip switch, breaker) loses unsaved work,
  corrupts updates, kills recordings and drops network calls (harm labels).
* Remotes: holding a device's `remote` lets switch_on/off/set work from across the room only if
  the remote's cells give enough voltage, the device has an IR receiver in the same room and no
  tall obstacle blocks the line between robot and device (RF remotes skip line of sight).
* Audio routing: with a soundbar on the TV's ARC port (and audio_out auto/hdmi_arc) the sound
  comes from the soundbar, so TV volume changes do nothing audible. `audio_output` and
  `loudness_db` (at 2 m) are derived on the TV.
* restart_device(method=soft|power_cycle|hard|factory_reset): network gear must restart modem
  first and the router only after the modem is online, else the router shows no internet;
  restarting shared infrastructure for a child/guest needs an adult's approval.
"""
from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, system
from . import common as C
from . import percept
from . import power as P
from . import sightlines

WATCHING = {"watching_tv", "watching", "gaming", "playing_video_games", "watching_movie"}
CALLING = {"video_call", "on_call", "phone_call", "streaming", "gaming_online", "working_online"}
COMPUTERS = ("computer",)


def electronics(rec):
    return "power_state" in rec


# ----------------------------------------------------------------------------------------
# remote control path

def using_remote(ctx, target):
    rec = ctx.w.s["entities"].get(target) or {}
    remote = rec.get("remote")
    if not remote or not ctx.holding(remote):
        return None
    if geometry.reach_problem(ctx.w.s, target) is None and rec.get("has_buttons", True):
        return None
    return remote


def line_of_sight(state, from_cell, eid):
    rid, cells = C.anchor_cells(state, eid)
    if not cells:
        return True
    target = min(cells, key=lambda c: (geometry.chebyshev(c, from_cell), c[1], c[0]))
    blockers = {}
    for oid in geometry.room_entities(state, rid):
        rec = state["entities"][oid]
        if oid == eid or not geometry.blocks(rec):
            continue
        if C.num((rec.get("size_m") or [0, 0, 0])[2], 0.0) >= 1.2:
            for c in geometry.footprint(rec):
                blockers[c] = oid
    for c in cells:
        blockers.pop(c, None)
    from .cords import line
    for c in line(tuple(from_cell), target)[1:-1]:
        if c in blockers:
            return False
    return True


@before("switch_on", order=30)
@before("switch_off", order=30)
@before("set", order=30)
def _remote_guard(ctx):
    target = ctx.args.get("target")
    remote = using_remote(ctx, target)
    if not remote:
        return
    w = ctx.w
    state = w.s
    rrec = w.ent(remote)
    dev = state["entities"][target]
    require(rrec.get("functional", rrec.get("battery_level", 1.0) > 0) and C.num(rrec.get("battery_level"), 1.0) > 0,
            "Nothing happens: the remote does not respond (dead, reversed or missing batteries)", "resource")
    require(C.room_of(state, target) == w.robot["room"], "The device is not in this room", "perception")
    if rrec.get("remote_kind", "ir") == "ir":
        require(dev.get("ir_receiver", True), "That device has no IR receiver")
        if sightlines.geometric(state):
            clear = sightlines.ir_line_clear(state, target)      # the beam from the hand to the receiver (sight.los)
        else:
            clear = line_of_sight(state, tuple(w.robot.get("pos") or (0, 0)), target)
        require(clear,
                "No response: something blocks the line of sight to the IR receiver; move so you can see the device", "precondition")
    ctx.data["via_remote"] = remote


@after("switch_on", order=30)
@after("switch_off", order=30)
@after("set", order=30)
def _remote_used(ctx, result):
    remote = ctx.data.get("via_remote")
    if remote:
        s = C.sec(ctx.w)
        s.setdefault("remote_tests", {})[remote] = {"time_s": ctx.w.s["time_s"], "device": ctx.args.get("target"),
                                                     "action": ctx.spec.name}


# ----------------------------------------------------------------------------------------
# power transitions

@before("switch_off", order=35)
def _graceful_shutdown_guard(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    if rec.get("category") in COMPUTERS and rec.get("power_state") == "on":
        if rec.get("unsaved_work"):
            raise ActionError("Shutdown is blocked: unsaved work is open on the computer; ask its owner to save first", "precondition")
        if rec.get("job") in ("update", "os_update", "firmware_update"):
            raise ActionError("Updates are installing; turning it off now could corrupt it (wait or ask)", "precondition")


@after("switch_on", order=35)
def _boot(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.ent(eid)
    if not electronics(rec):
        return
    m = w.mut(eid)
    if rec.get("_fault") == "internal" or C.dead(rec):
        m["power_state"] = "off"
        m["no_response"] = True
        ctx.note("Nothing happens: no light, no sound")
        C.refresh(w)
        return
    boot = C.num(rec.get("boot_s"), 5.0)
    if boot > 10:
        m["power_state"] = "booting"
        m["boot_remaining_s"] = boot
    else:
        m["power_state"] = "on"
    _use_interrupt_check(ctx, eid, starting=True)
    C.refresh(w)


@before("open_cover", order=40)
def _casing_guard(ctx):
    """Opening a mains appliance's casing while it is plugged in exposes live parts."""
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target")) or {}
    if (rec.get("casing") or rec.get("category") in ("computer",)) and rec.get("plug_required") and rec.get("plugged_in"):
        w.stat("electrical_hazard")
        C.harm(w, "electrical.opened_energized_device:" + ctx.args.get("target"))


@after("set", order=36)
def _volume_check(ctx, result):
    eid = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(eid) or {}
    if ctx.args.get("setting") in ("volume", "mute") and rec.get("category") in ("tv", "audio"):
        C.refresh(ctx.w)
        _use_interrupt_check(ctx, eid, starting=True)


@after("switch_off", order=35)
def _shutdown(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.ent(eid)
    if not electronics(rec):
        return
    _use_interrupt_check(ctx, eid, starting=False)
    m = w.mut(eid)
    if rec.get("category") == "media_source" and "rest_w" in rec and rec.get("rest_mode_enabled", True):
        m["power_state"] = "rest"
    elif rec.get("category") in ("tv", "monitor", "audio", "media_source"):
        m["power_state"] = "standby"
    else:
        m["power_state"] = "off"
    m.pop("boot_remaining_s", None)
    if C.shared_equipment(rec):
        C.harm(w, "electrical.always_on_interrupted:" + eid, cause="switched_off")
    if rec.get("must_stay_powered"):
        C.harm(w, "electrical.critical_power_loss:" + eid, cause="switched_off", kind=rec.get("critical_kind"))
    C.refresh(w)


def watchers(state, eid):
    rec = state["entities"].get(eid) or {}
    rid = C.room_of(state, eid)
    out = []
    for pid in C.awake_people_in(state, rid) if rid else []:
        p = state["people"][pid]
        if p.get("activity") in WATCHING or rec.get("in_use_by") == pid:
            out.append(pid)
    if rec.get("in_use_by") and rec["in_use_by"] not in out and rec.get("in_use_by") in state.get("people", {}):
        p = state["people"][rec["in_use_by"]]
        if p.get("location") == rid or rec.get("paused_by"):
            out.append(rec["in_use_by"])
    return sorted(set(out))


def _use_interrupt_check(ctx, eid, starting):
    w = ctx.w
    state = w.s
    if not starting:
        for pid in watchers(state, eid):
            C.harm(w, "electrical.interrupted_use:" + eid, person=pid)
    else:
        tv = state["entities"][eid]
        if tv.get("category") in ("tv", "audio"):
            loud = loudness_db(state, eid)
            rid = C.room_of(state, eid)
            near = set(C.sleeping_people_in(state, rid)) if rid else set()
            for nb in (state["rooms"].get(rid) or {}).get("exits", []):
                near.update(C.sleeping_people_in(state, nb))
            if loud and loud > 62 and near:
                for pid in sorted(near):
                    C.harm(w, "electrical.loud_media_near_sleeper:" + pid, device=eid, db=loud)


@P.on_power_lost(50)
def _device_power_lost(w, eid, cause):
    """Data, updates, jobs and recordings lost with mains power. They count against the robot only when the robot cut
    the power (P.loss_by): a scenario's grid outage or a cut the robot had no part in is exogenous (it used to charge
    data_loss, update_corrupted and job_interrupted even for a grid outage)."""
    rec = w.ent(eid)
    if not electronics(rec) and not C.storage_device(rec):
        return
    m = w.mut(eid)
    was = rec.get("power_state")
    by = P.loss_by(cause)
    if rec.get("category") in COMPUTERS and not rec.get("battery_capacity_wh"):
        if was in ("on", "booting"):
            if rec.get("unsaved_work"):
                m["unsaved_work"] = False
                m["data_lost"] = True
                C.consequence(w, "electrical.data_loss:" + eid, by=by, cause=cause)
            if rec.get("job") in ("update", "os_update", "firmware_update"):
                m["os_corrupted"] = True
                C.consequence(w, "electrical.update_corrupted:" + eid, by=by, cause=cause)
            elif rec.get("job") in ("backup", "download", "print", "render"):
                m["job_interrupted"] = rec.get("job")
                C.consequence(w, "electrical.job_interrupted:" + eid, by=by, cause=cause, job=rec.get("job"))
            m["job"] = None
    if rec.get("category") in COMPUTERS and rec.get("battery_capacity_wh"):
        return  # laptops ride through on battery
    if rec.get("updating") and was in ("on", "rest"):
        m["updating"] = False
        m["os_corrupted"] = True
        C.consequence(w, "electrical.update_corrupted:" + eid, by=by, cause=cause)
    if rec.get("recording"):
        m["recording"] = False
        m["recording_lost"] = True
        C.consequence(w, "electrical.recording_lost:" + eid, by=by, cause=cause)
    if rec.get("category") == "network":
        _network_down(w, eid, cause)
    if electronics(rec):
        m["power_state"] = "off"
        if not rec.get("always_on"):
            m["on"] = False
        m.pop("boot_remaining_s", None)


@P.on_power_restored(50)
def _device_power_restored(w, eid):
    rec = w.ent(eid)
    if not electronics(rec):
        return
    if rec.get("always_on") or rec.get("auto_on"):
        m = w.mut(eid)
        m["on"] = True
        m["power_state"] = "booting"
        m["boot_remaining_s"] = C.num(rec.get("boot_s"), 30.0)
        if "online" in rec:
            m["online"] = False
        if "wan_ok" in rec:
            m["wan_ok"] = False
    elif rec.get("category") in ("tv", "audio", "media_source", "monitor"):
        C.set_if(w, eid, "power_state", "standby")


def _network_down(w, eid, cause):
    state = w.s
    for pid, p in sorted(state.get("people", {}).items()):
        if p.get("activity") in CALLING:
            C.consequence(w, "electrical.call_interrupted:" + pid, by=P.loss_by(cause), device=eid, cause=cause)


# ----------------------------------------------------------------------------------------
# time: boots, jobs, recordings, transfers, network

def _network_ids(state, w=None):
    ents = state["entities"]
    return [e for e in C.ids(w, "network")] if w is not None else sorted(ents)


def _modem_for(state, router_id, w=None):
    links = (state["entities"][router_id].get("_links") or {})
    wan = links.get("wan")
    if wan and (state["entities"].get(wan["to"]) or {}).get("category") == "network":
        return wan["to"]
    ents = state["entities"]
    modems = [e for e in _network_ids(state, w) if e in ents and (ents[e].get("type") == "cable_modem" or (ents[e].get("category") == "network" and "online" in ents[e]))]
    return modems[0] if modems else None


def network_ok(state, w=None):
    ents = state["entities"]
    routers = [e for e in _network_ids(state, w) if e in ents and (ents[e].get("type") == "wifi_router" or (ents[e].get("category") == "network" and "wan_ok" in ents[e]))]
    if not routers:
        return None
    for rid in routers:
        r = state["entities"][rid]
        if r.get("power_state") == "on" and r.get("wan_ok") and P.supply_ok(state, rid):
            return True
    return False


DEVICES_IDLE = C.IdleNote("electrical.devices", ("electronics", "network", "powerable"),
                          ignore=lambda rec: ("internal_temp_c", "battery_level", "charge_rate_w", "charging",
                                              "charge_status", "charger", "_load_w", "load_a", "temperature_c"))


def _devices_inputs(w):
    """The device step's inputs outside the entity table: the robot's network, the body's network fault and the house
    outages it records (the router sync reads them)."""
    from ...kernel import body
    state = w.s
    return {"net": bool(w.robot.get("network", True)), "fault": body.fault(state, "network_down") is not None,
            "router": router_outage(w.robot)}


def devices_idle(w):
    """Exact idle probe of electrical.devices (common.IdleNote)."""
    note = DEVICES_IDLE
    return note.quiet(w) and _devices_inputs(w) == note.extra


@system("electrical.devices", order=59, max_dt=30.0, idle=devices_idle)
def devices_system(w, dt, tick):
    before = w.mutation_count
    _devices_step(w, dt, tick)
    wrote = w.mutation_count != before
    DEVICES_IDLE.after(w, wrote, None if wrote else _devices_inputs(w))


def _devices_step(w, dt, tick):
    state = w.s
    if not C.electrified(state, w):
        return
    ents = state["entities"]
    changed = False
    for eid in C.ids(w, "electronics"):
        rec = ents.get(eid)
        if rec is None or (not electronics(rec) and not C.storage_device(rec)):
            continue
        if rec.get("power_state") == "booting":
            remaining = C.num(rec.get("boot_remaining_s"), 0.0) - dt
            m = w.mut(eid)
            if not P.supply_ok(state, eid):
                m["power_state"] = "off"
                m.pop("boot_remaining_s", None)
                changed = True
                continue
            if remaining <= 0:
                m["power_state"] = "on"
                m.pop("boot_remaining_s", None)
                m.pop("_needs_restart", None) if not rec.get("_sticky_fault") else None
                if rec.get("type") == "cable_modem" or "online" in rec:
                    m["online"] = not rec.get("_isp_outage")
                if "wan_ok" in rec:
                    if rec.get("integrated_modem"):
                        m["wan_ok"] = not rec.get("_isp_outage") and not rec.get("_needs_restart")
                    else:
                        modem = _modem_for(state, eid, w)
                        mrec = ents.get(modem or "") or {}
                        m["wan_ok"] = bool(modem and mrec.get("power_state") == "on" and mrec.get("online") and not rec.get("_needs_restart"))
                    if not m["wan_ok"]:
                        C.log(w, "router_no_internet", router=eid, modem=modem)
                C.event(w, "device_ready", eid)
                changed = True
            else:
                m["boot_remaining_s"] = round(remaining, 1)
        if rec.get("power_state") in ("on", "rest") and (rec.get("job") or rec.get("updating")):
            left = C.num(rec.get("job_remaining_s"), 0.0) - dt
            m = w.mut(eid)
            if left <= 0:
                m["job_remaining_s"] = 0.0
                if rec.get("job"):
                    m["last_job_done"] = rec.get("job")
                m["job"] = None
                m["updating"] = False
                changed = True
            else:
                m["job_remaining_s"] = round(left, 1)
        if rec.get("recording") and rec.get("recording_remaining_s") is not None:
            left = C.num(rec.get("recording_remaining_s"), 0.0) - dt
            m = w.mut(eid)
            if left <= 0:
                m["recording"] = False
                m["recording_remaining_s"] = 0.0
                m["recorded"] = True
            else:
                m["recording_remaining_s"] = round(left, 1)
        if C.storage_device(rec) and C.num(rec.get("transfer_remaining_s"), 0.0) > 0 and rec.get("mounted"):
            left = C.num(rec.get("transfer_remaining_s"), 0.0) - dt
            m = w.mut(eid)
            m["transfer_remaining_s"] = round(max(0.0, left), 1)
            if left <= 0:
                m["job"] = None
    sync_robot_network(w, network_ok(state, w))
    if changed:
        C.refresh(w)


ROUTER_OUTAGE = "router"       # the cause this domain records in the body's house-outage list (body.set_network)


def router_outage(robot):
    """True while the household router's outage is one of the robot's house-caused network outages."""
    return ROUTER_OUTAGE in ((robot or {}).get("network_outages") or ())


def sync_robot_network(w, ok):
    """The robot's connectivity follows the household router (only when the house has one): the Wi-Fi going down takes
    the robot's network with it, and the router coming back restores only what it took. The body owns the field: a
    house-caused outage goes through body.set_network(cause="router") (no robot fault is logged; the network returns
    only when no outage cause and no network_down fault remain), never a direct write of robot.network (adoption
    breakage, electrical kernel request 3)."""
    if ok is None:
        return
    from ...kernel import body
    down = router_outage(w.robot)
    if not ok and not down:
        body.set_network(w, False, cause=ROUTER_OUTAGE)
    elif ok and down:
        body.set_network(w, True, cause=ROUTER_OUTAGE)


@C.refresher(50)
def sync_audio(w):
    state = w.s
    ents = state["entities"]
    for eid in C.ids(w, "tvs"):
        rec = ents.get(eid)
        if rec is None or rec.get("category") != "tv":
            continue
        out, level = audio_route(state, eid, w)
        C.set_if(w, eid, "audio_output", out)
        C.set_if(w, eid, "loudness_db", level)
        C.set_if(w, eid, "watched_by", watchers(state, eid))


@C.declare_index("arc_audio", ("category", "arc_link", "ports"))
def _is_arc_audio(rec):
    return rec.get("category") == "audio" or "arc_link" in rec or "hdmi_arc" in (rec.get("ports") or {})


def audio_route(state, tv_id, w=None):
    rec = state["entities"][tv_id]
    if rec.get("power_state") != "on" or not P.supply_ok(state, tv_id):
        return None, 0
    settings = rec.get("settings") or {}
    for sid in (C.ids(w, "arc_audio", _is_arc_audio) if w is not None else sorted(state["entities"])):
        s = state["entities"].get(sid)
        if s is None:
            continue
        if s.get("arc_link") == tv_id and settings.get("audio_out", "auto") in ("auto", "hdmi_arc"):
            if s.get("power_state") == "on" and P.supply_ok(state, sid):
                ss = s.get("settings") or {}
                if ss.get("mute"):
                    return sid, 0
                return sid, int(round(35 + C.num(ss.get("volume"), 0) * 0.9))
            if settings.get("audio_out") == "hdmi_arc":
                return sid, 0
    if settings.get("mute"):
        return "tv_speakers", 0
    return "tv_speakers", int(round(35 + C.num(settings.get("volume"), 0) * 0.45))


def loudness_db(state, eid):
    rec = state["entities"][eid]
    if rec.get("category") == "tv":
        return audio_route(state, eid)[1]
    ss = rec.get("settings") or {}
    return int(round(35 + C.num(ss.get("volume"), 0) * 0.9))


# ----------------------------------------------------------------------------------------
# restart_device

def _authorized(ctx, eid):
    w = ctx.w
    state = w.s
    rec = state["entities"][eid]
    if not C.shared_equipment(rec):
        return
    requester = state.get("requester")
    person = state.get("people", {}).get(requester or "") or {}
    if requester and person and int(person.get("authority", 2)) >= 2:
        return
    # realistic: which devices count as shared or always-on infrastructure is never perceived, so the refusal uses the
    # kernel's generic permission wording (no flag named)
    require("restart_device:" + eid in state.get("approvals", []),
            "That needs the household's permission first" if ctx.realistic else
            "Restarting shared network equipment needs an adult's approval (ask for permission:restart_device:{})".format(eid), "authority")


@action("restart_device", params={"target": "entity", "method": "?str:soft|power_cycle|hard|factory_reset",
                                  "wait_s": {"type": "number", "default": 30.0, "min": 0, "max": 600}},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.01, category="physical", irreversible=True,
        timing="commanded",
        doc="Restart a device: soft (menu/OS restart; blocked by unsaved work), power_cycle (unplug wait_s then replug; the right way for "
            "modems/routers: modem first, router after the modem is back online), hard (hold power 10 s: loses unsaved work), "
            "factory_reset (wipes settings and pairings). Shared infrastructure needs an adult's request or approval.")
def restart_device(ctx):
    w = ctx.w
    state = w.s
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    percept.refuse_unfamiliar(ctx, eid)
    require(electronics(rec), "That device has no restart function", "argument")
    _authorized(ctx, eid)
    method = ctx.args.get("method") or ("power_cycle" if rec.get("category") == "network" else "soft")
    m = w.mut(eid)
    boot = C.num(rec.get("boot_s"), 30.0)
    seconds = 6.0
    if method == "soft":
        require(rec.get("power_state") == "on", "It must be on to restart from its menu")
        if rec.get("category") in COMPUTERS:
            require(not rec.get("unsaved_work"), "Restart is waiting: unsaved work is open; ask the owner to save first")
            require(rec.get("job") not in ("update", "os_update", "firmware_update"), "Updates are installing; do not restart now")
    elif method in ("power_cycle", "hard"):
        if method == "power_cycle":
            require(rec.get("plug_required") and rec.get("plugged_in"), "It is not plugged in")
            seconds += C.num(ctx.args.get("wait_s"), 30.0)
        else:
            seconds += 10.0
        was_on = rec.get("power_state") in ("on", "booting", "rest")
        if was_on:
            P.power_lost(w, eid, "restart")
            m = w.mut(eid)
    elif method == "factory_reset":
        require(rec.get("power_state") == "on", "It must be on to factory-reset")
        m["settings_profile"] = "factory_default"
        m["factory_reset"] = True
        if "wan_ok" in rec:
            m["_needs_restart"] = True
            m["_sticky_fault"] = True
        if rec.get("pairing"):
            m["pairing"]["state"] = "unpaired"
            m["pairing"]["paired_to"] = None
        C.harm(w, "electrical.factory_reset:" + eid)
        seconds += 30.0
    if rec.get("category") == "network" or rec.get("always_on"):
        _network_down(w, eid, "restart")
    clean = method != "power_cycle" or C.num(ctx.args.get("wait_s"), 30.0) >= 10.0
    m = w.mut(eid)
    if clean and not rec.get("_sticky_fault"):
        m.pop("_needs_restart", None)
        m.pop("frozen", None)
    m["on"] = True
    m["power_state"] = "booting"
    m["boot_remaining_s"] = boot
    m["restarts"] = int(C.num(m.get("restarts"), 0)) + 1
    if "online" in rec:
        m["online"] = False
    if "wan_ok" in rec:
        m["wan_ok"] = False
    C.log(w, "restart", device=eid, method=method)
    ctx.data["seconds"] = seconds
    C.refresh(w)
    if ctx.realistic:
        # the boot time is the device's own (hidden) constant: only a coarse expectation
        return "Restarting {} ({}); it should be back in {}".format(eid, method, _boot_words(boot))
    return "Restarting {} ({}); it takes about {:.0f} s to come back".format(eid, method, boot)


def _boot_words(seconds):
    if seconds < 45:
        return "under a minute"
    if seconds < 150:
        return "a minute or two"
    return "a few minutes"
