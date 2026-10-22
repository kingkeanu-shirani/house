"""What the robot can sense about electrical things.

Observers
  electrical.sounds   detector chirps (intermittent: only those during the last action), alarms
                      (house-wide), an alarm clock ringing, buzzing arcing outlets, TV/soundbar
                      audio. Sources in other rooms are given only as a direction (the next room
                      toward them, or upstairs/downstairs) and a loudness falling 0.45x per room.
  electrical.smells   hot/burning plastic from overheating cords, strips, outlets or fixtures.
  electrical.light    illuminance_lux of the current room.
Inspectors (inspect target)
  outlets / strips    receptacles, the plug in each socket (look only), live state, GFCI state,
                      warmth/scorch/buzz, wetness;
  device where=behind trace its cord to "<outlet> socket N" (recorded as traced) and list the
                      device's port wiring (what each HDMI/USB port is connected to);
  cords               cord condition (scuff/fray/exposed/crushed...) when within reach;
  detectors           LED, chirp pattern, display, manufacture date when within reach or held,
                      battery type; breakers: handle position, label, rating.
  clocks              (realistic mode) the alarm settings shown on the clock, without whose alarm it is.
Realistic perception (env.realism is not None; oracle output is byte-for-byte unchanged): the field tiers in
data/types/electrical.json decide what an inspect shows (receptacle `live` needs a held voltage tester, displays and
printed ratings need `read`, cable data lines are never visible); battery charge shows as the device's own screen
percentage or LED bucket; receptacles list the plug look of corded devices (which device needs a trace) and the
wall adapter itself (by id) for plug-in-place adapters; room light is a camera estimate (percept.lux_estimate).
"""
from collections import deque

from ...kernel import geometry
from ...kernel.plugins import hook, inspector, observer
from . import cables as K
from . import common as C
from . import percept
from . import power as P
from . import sightlines
from .lighting import lux_here


def _route(state, src, dst):
    """(hops, first room after src toward dst) through room exits."""
    if src == dst:
        return 0, None
    rooms = state["rooms"]
    queue, seen = deque([(src, 0, None)]), {src}
    while queue:
        rid, d, first = queue.popleft()
        for nxt in sorted(rooms.get(rid, {}).get("exits", [])):
            if nxt in seen:
                continue
            f = first or nxt
            if nxt == dst:
                return d + 1, f
            seen.add(nxt)
            queue.append((nxt, d + 1, f))
    return 99, None


def _hint(state, here, there):
    hops, first = _route(state, here, there)
    if hops == 0:
        return 0, "here"
    f_here = (state["rooms"].get(here) or {}).get("floor", 0)
    f_there = (state["rooms"].get(there) or {}).get("floor", 0)
    if f_there > f_here:
        return hops, "upstairs"
    if f_there < f_here:
        return hops, "downstairs"
    return hops, "toward " + str(first)


@hook("visible_veto")
def _panel_door_hides_breakers(state, eid, entity):
    """Kernel v2.1 hides only `in` children of a closed container; breakers are `part`s of the load center, but its
    closed steel door still covers them (open the panel to read handles and labels)."""
    if entity.get("category") != "breaker":
        return None
    panel = state["entities"].get(entity.get("location") or "")
    if panel and panel.get("openable") and not panel.get("open") and not panel.get("transparent"):
        return True
    return None


@observer("electrical.sounds", order=110)
def sounds(env, w, obs):
    state = w.s
    here = obs.get("room_id")
    steps = state.get("steps", 0)
    heard = []
    for entry in (state.get("event_log") or [])[-120:]:
        if entry.get("step") != steps:
            continue
        kind = entry.get("kind")
        room = entry.get("room")
        if kind == "detector_chirp" and room:
            hops, hint = _hint(state, here, room)
            loud = 0.6 * (0.45 ** hops)
            if loud >= 0.04:
                item = {"kind": "high_pitched_chirp", "source_hint": hint, "loudness": round(loud, 2), "pattern": "single chirp, repeating"}
                if hops == 0:
                    item["source"] = entry.get("target")
                heard.append(item)
        elif kind == "alarm_sounding" and room and C.has_system("safety.detectors"):
            continue
        elif kind in ("detector_test_alarm", "alarm_sounding") and room:
            hops, hint = _hint(state, here, room)
            item = {"kind": "smoke_co_alarm_sounding" if kind == "alarm_sounding" else "alarm_test_tone", "source_hint": hint,
                    "loudness": round(min(1.0, 1.0 * (0.7 ** hops)), 2)}
            if hops == 0:
                item["source"] = entry.get("target")
            if kind == "alarm_sounding":
                item["pattern"] = "T4 (4 beeps, pause) - carbon monoxide" if entry.get("cause") == "co" else "T3 (3 long beeps, pause) - smoke"
            heard.append(item)
        elif kind == "alarm_clock_ringing" and room:
            hops, hint = _hint(state, here, room)
            loud = 0.7 * (0.45 ** hops)
            if loud >= 0.04:
                heard.append({"kind": "alarm_clock_beeping", "source_hint": hint, "loudness": round(loud, 2)})
    ents = state["entities"]
    for eid in sorted(set(C.ids(w, "outlets")) | set(C.ids(w, "clocks")) | set(C.ids(w, "tvs"))):
        rec = ents.get(eid)
        if rec is None:
            continue
        rid = None
        if rec.get("category") == "outlet" and rec.get("_fault") == "arcing" and C.num(rec.get("_arc_s"), 0.0) > 0:
            rid = C.room_of(state, eid)
            if rid == here and any(P.draw_w(state, d) > 0 for _, d in P.plugs_in(state, eid, C.ids(w, "plug_devices"))):
                heard.append({"kind": "electrical_buzzing", "source_hint": "here", "loudness": 0.2, "source": eid})
        elif rec.get("category") == "clock" and rec.get("ringing"):
            rid = C.room_of(state, eid)
            hops, hint = _hint(state, here, rid)
            heard.append({"kind": "alarm_clock_beeping", "source_hint": hint, "loudness": round(0.7 * 0.45 ** hops, 2)})
        elif rec.get("category") == "tv" and C.num(rec.get("loudness_db"), 0) > 0:
            rid = C.room_of(state, eid)
            hops, hint = _hint(state, here, rid)
            loud = (C.num(rec.get("loudness_db"), 0) - 30) / 60.0 * (0.45 ** hops)
            if loud >= 0.05:
                # realistic: a microphone's rough level, not the sound meter's dB (loudness_db is measure tier)
                item = {"kind": "tv_audio", "source_hint": hint,
                        "loudness": max(0.1, round(loud, 1)) if percept.active(env) else round(loud, 2)}
                if hops == 0:
                    item["source"] = rec.get("audio_output") if rec.get("audio_output") not in (None, "tv_speakers") else eid
                heard.append(item)
    if heard:
        obs.setdefault("sounds", []).extend(heard)


@observer("electrical.smells", order=111)
def smells(env, w, obs):
    state = w.s
    here = obs.get("room_id")
    found = []
    from .charging import _chargeable
    from .cables import _linkable
    candidates = set(C.ids(w, "providers")) | set(C.ids(w, "lights")) | set(C.ids(w, "chargeables", _chargeable)) | set(C.ids(w, "linkables", _linkable))
    for eid in sorted(candidates):
        rec = state["entities"].get(eid)
        if rec is None:
            continue
        hot = C.num(rec.get("temperature_c"), 21.0) >= 60 and (P.is_provider(rec) or rec.get("category") in ("outlet", "cable"))
        burnt = rec.get("scorched") or rec.get("socket_scorched") or rec.get("thermal_runaway") or rec.get("ignited")
        if not (hot or burnt):
            continue
        rid = C.room_of(state, eid)
        if rid is None:
            continue
        hops, hint = _hint(state, here, rid)
        intensity = (0.6 if burnt else 0.35) * (0.5 ** hops)
        if intensity >= 0.05:
            found.append({"kind": "hot_plastic" if not burnt else "burning_plastic", "intensity": round(intensity, 2), "source_hint": hint})
    if found:
        obs.setdefault("smells", []).extend(found[:3])


@observer("electrical.light", order=112)
def light(env, w, obs):
    """illuminance_lux: room mode, the room's light level (a camera exposure estimate in realistic perception);
    geometric camera mode, the exposure of what the head camera looks at (sightlines.geometric_lux: a lamp's pool
    counts only in view), absent with the cameras off."""
    if sightlines.geometric(w.s):
        lux = sightlines.geometric_lux(w.s)
        if lux is None:
            return
    else:
        lux = lux_here(w.s, obs.get("room_id"))
    obs["illuminance_lux"] = percept.lux_estimate(env, obs.get("room_id"), lux) if percept.active(env) else lux


# ----------------------------------------------------------------------------------------
# inspectors

def _near(state, eid):
    return geometry.reach_problem(state, eid) is None or state["entities"].get(eid, {}).get("location") == "robot"


def voltage_tester_held(w):
    """A held non-contact voltage tester (tool_kind voltage_tester / affordance test_voltage), or None."""
    for h in w.held_items():
        r = w.ent(h)
        if r.get("tool_kind") == "voltage_tester" or "test_voltage" in (r.get("affordances") or []):
            return h
    return None


def _voltage_test(w, eid, details, ctx, field, live, where="here"):
    """Held voltage tester: the only perception of whether a conductor is live (measure tier)."""
    if voltage_tester_held(w) is None:
        return
    if live:
        details["voltage_tester"] = "beeps and glows red: voltage present " + where
    else:
        details["voltage_tester"] = "stays silent: no voltage detected " + where
    if ctx is not None:
        ctx.grant(eid, "measure", [field])
        if percept.active(ctx):
            percept.passthrough(details, "voltage_tester")


@inspector("electrical.outlets", order=55)
def _inspect_outlets(w, eid, rec, details, ctx):
    state = w.s
    real = ctx is not None and percept.active(ctx)
    if rec.get("category") == "outlet" or P.is_provider(rec):
        plugs = P.plugs_in(state, eid)
        from .cords import plug_look
        details["receptacles"] = {str(i): None for i in range(1, int(C.num(rec.get("receptacles"), 0)) + 1)}
        for socket, dev in plugs:
            details["receptacles"][str(socket)] = plug_look(state, dev, realistic=real)
        details["live"] = bool(rec.get("live"))      # measure tier: realistic views drop it unless measured
        _voltage_test(w, eid, details, ctx, "live", bool(rec.get("live")), "at the receptacle")
        if real and rec.get("outlet_kind") == "strip" and rec.get("switchable"):
            details["power_light"] = "lit" if rec.get("on") and rec.get("live") else "dark"
        if rec.get("gfci"):
            details["gfci"] = "tripped (RESET button out)" if rec.get("gfci_tripped") else "ok"
        if rec.get("outlet_kind") in ("strip", "extension"):
            details["rating"] = "{:.0f} W".format(C.num(rec.get("rating_w"), 1875))
            if rec.get("has_breaker"):
                details["overload_button"] = "tripped" if rec.get("tripped") else "ok"
        if rec.get("scorched") or rec.get("_fault") == "arcing" and C.num(rec.get("_arc_s"), 0) > 120:
            details["condition"] = "discolored, warm to the touch" + (", scorch marks" if rec.get("scorched") else "")
        if C.num(rec.get("wetness"), 0.0) > 0.1:
            details["condition"] = "wet"
        if rec.get("outlet_kind") == "timer":
            details["timer"] = dict(rec.get("settings") or {})
    if rec.get("category") == "breaker":
        details["handle"] = rec.get("handle", "on" if rec.get("on") else "off")
        details["rating_a"] = rec.get("rating_a")
        if rec.get("label_text"):
            details["label_text"] = rec["label_text"]
        if rec.get("_fault") == "short" or rec.get("scorched"):
            details["condition"] = "scorch mark around the terminal"
    if rec.get("category") == "breaker_panel" and rec.get("open"):
        details["breakers"] = [b for b in w.children(eid) if (state["entities"].get(b) or {}).get("category") == "breaker"]


@inspector("electrical.devices", order=56)
def _inspect_devices(w, eid, rec, details, ctx):
    state = w.s
    where = ctx.args.get("where") if ctx is not None else None
    real = ctx is not None and percept.active(ctx)
    # oracle: where=behind/closely inspects cords from anywhere; realistic: a close look or a trace needs arm's reach
    close = _near(state, eid) or (not real and where in ("behind", "closely"))
    trace = where == "behind" and (not real or _near(state, eid))
    if rec.get("category") == "light" and "powered" in rec:
        _voltage_test(w, eid, details, ctx, "powered", bool(rec.get("powered")), "at the lamp socket")
    if rec.get("plug_required"):
        details["plugged_in"] = bool(rec.get("plugged_in"))
        if close:
            dmg = rec.get("_cord_damage", "none")
            details["cord_condition"] = {"none": "intact", "scuff": "scuffed jacket (cosmetic)", "fray": "frayed jacket, inner wires visible",
                                         "exposed": "bare copper exposed", "crushed": "crushed flat", "scorched": "melted / scorched",
                                         "chewed": "chewed through", "strain": "pulled from the plug (strain relief torn)"}.get(dmg, dmg)
            if rec.get("plug_damage"):
                details["plug_condition"] = rec["plug_damage"]
            details["plug"] = {"type": rec.get("plug_type"), "polarized": bool(rec.get("plug_polarized")),
                               "grounded": rec.get("plug_type") in C.GROUNDED_PLUGS}
            if real and rec.get("plugged_in"):
                # how far the plug sits in the socket is seen up close (the raw plug_seated fraction is not)
                details["plug"]["seated"] = "fully in" if C.num(rec.get("plug_seated"), 1.0) >= 0.99 else "partly out, prongs exposed"
            if real:
                # the rating plate is printed text (read): it shows on a close look only once the robot has read the
                # item, never with the product name (which could resolve a look-alike power brick)
                if C.num(rec.get("power_w"), 0.0) > 0 and percept.was_read(ctx, eid):
                    details["rating_label"] = "rated {:.0f} W".format(C.num(rec.get("power_w"), 0))
                    percept.passthrough(details, "rating_label")
            else:
                details["rating_label"] = "{} {:.0f} W".format(rec.get("label", ""), C.num(rec.get("power_w"), 0))
        if trace and rec.get("plugged_in"):
            out = rec.get("outlet")
            if out:
                details["cord_runs_to"] = "{} socket {}".format(out, rec.get("_socket"))
                C.sec(w)["traced"][eid] = state["time_s"]
    if trace and (rec.get("ports") or K.captive_end(rec)):
        wiring = {}
        for port, info in sorted((rec.get("ports") or {}).items()):
            link = (rec.get("_links") or {}).get(port)
            wiring[info.get("label", port)] = None if not link else "{} ({}){}".format(
                link["to"], ((state["entities"].get(link["to"]) or {}).get("ports") or {}).get(link["to_port"], {}).get("label", link["to_port"]),
                "" if link["quality"] == "ok" else ", " + link["quality"])
            if info.get("bent"):
                wiring[info.get("label", port)] = "bent pins (damaged)"
        details["port_wiring"] = wiring
        for port, link in sorted((rec.get("_links") or {}).items()):
            C.sec(w)["traced"]["{}:{}".format(eid, port)] = state["time_s"]
    if K.is_cable(rec):
        details.update(K.cable_inspect_details(state, eid, rec))
    # printed output ratings (read): realistic views quote them only after the robot read the label (a look-alike brick
    # would otherwise resolve on a close look)
    printed = not real or percept.was_read(ctx, eid)
    if rec.get("captive_connector") and printed:
        details["output"] = "{} V, {:.0f} W, {} plug".format(rec.get("output_v", 5), C.num(rec.get("source_w"), 0), rec.get("captive_connector"))
        if real:
            percept.passthrough(details, "output")
    if rec.get("category") == "charger" and rec.get("ports") and printed:
        details["outputs"] = {info.get("label", p): "{:.0f} W".format(C.num(info.get("source_w"), 0)) for p, info in sorted(rec["ports"].items())}
        if real:
            percept.passthrough(details, "outputs")
    if rec.get("rechargeable") and "battery_level" in rec:
        if real:
            details.update(percept.battery_view(rec, C.num(rec.get("battery_level"), 0)))   # screen % or LED bucket
        else:
            details["battery_pct"] = int(round(C.num(rec.get("battery_level"), 0) * 100))
        details["charging"] = bool(rec.get("charging"))
        if rec.get("charge_status"):
            details["charge_status"] = rec.get("charge_status")
    if rec.get("category") in ("computer",):
        details["screen"] = {"power_state": rec.get("power_state"), "job": rec.get("job"),
                             "unsaved_documents": bool(rec.get("unsaved_work")) if rec.get("power_state") == "on" else None}
    if rec.get("pairing"):
        if not real:
            details["pairing"] = {k: rec["pairing"].get(k) for k in ("state", "name", "room", "protocol")}
            details["led"] = rec.get("led")
        elif rec.get("category") != "light_bulb" and rec.get("led"):
            # the status LED is what the device shows; the name/room it was given live in the hub's app and the protocol
            # on its box. A smart bulb has no LED (a key only it had would pick it out of look-alike LED bulbs)
            details["led"] = rec.get("led")


@inspector("electrical.detectors", order=57)
def _inspect_detectors(w, eid, rec, details, ctx):
    state = w.s
    if rec.get("category") != "detector":
        return
    details["led"] = rec.get("led")
    details["chirping"] = bool(rec.get("chirping"))
    if rec.get("detector_kind") in ("co", "combo"):
        details["display"] = rec.get("display")
        details["peak_ppm"] = rec.get("co_peak_ppm")
    if _near(state, eid):
        details["label_text"] = "MFG {} - replace 10 years after manufacture".format(rec.get("manufactured"))
        details["manufactured"] = rec.get("manufactured")
        details["battery_type"] = rec.get("battery_type")
        details["sealed"] = bool(rec.get("sealed"))
    details["alarm_state"] = rec.get("alarm_state")
    details["mounted"] = bool(rec.get("mounted"))


@inspector("electrical.clocks", order=58)
def _inspect_clocks(w, eid, rec, details, ctx):
    """Realistic mode only: the alarm times a close look at the clock shows (the clock's internal offset and whose
    alarm is whose stay hidden). Oracle inspect output is unchanged (the record carries `alarms`)."""
    if ctx is None or not percept.active(ctx) or not isinstance(rec.get("clock"), dict):
        return
    alarms = rec.get("alarms")
    if isinstance(alarms, dict) and alarms:
        details["alarm_settings"] = {name: {k: a.get(k) for k in ("time", "enabled", "days") if k in a}
                                     for name, a in sorted(alarms.items()) if isinstance(a, dict)}
