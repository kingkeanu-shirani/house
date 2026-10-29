"""Cables and ports (fidelity G for connector keying/seating and cable reach; S for signal logic).

Devices declare `ports` {name: {connector, dir, label, ...}}; dir is one of
  in (display input), out (video source), arc (soundbar ARC), host (USB host / source power),
  device (USB device / charge input), upstream (hub/dock to computer), source (charger port),
  lan / wan (Ethernet), dc_in (barrel power input).
Cables have two ends {connector, at, port, seated}; peripherals and chargers with a captive
plug act as the cable themselves (port flagged captive / charger captive_connector).

connect_cable(target=cable|captive device, destination=device, port=..., end=, orientation=,
              seat=, force=)
  wrong connector type or upside-down keyed plug is refused unless force=true (bent pins,
  damage); seat=partial gives an intermittent link; the cable must reach between devices.
disconnect_cable(target=cable|device, destination=device whose end to pull, port=, grip=,
                 release_latch=, eject_first=)
  pull by the connector body; grip=cable yanks (strain damage, drags light devices off their
  surface); Ethernet/DisplayPort latches snap unless released; storage must be ejected first
  (pulled mid-transfer it is corrupted).

Links (recomputed after every change) drive observable state: TV/monitor `display`,
soundbar `audio_source`, peripherals `recognized`, storage `mounted`, Ethernet `network_link`,
and the charge sources used by charging.py. The port map itself (`_links`) is hidden: inspect
a device where=behind (or read the TV's input labels) to learn what is wired where.
"""
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from . import common as C
from . import percept
from . import power as P

LATCHING = {"rj45", "displayport"}


def ports(rec):
    return rec.get("ports") or {}


def is_cable(rec):
    return rec.get("category") == "cable" and isinstance(rec.get("ends"), dict)


def captive_end(rec):
    """Name of the captive plug port of a peripheral, or 'captive' for chargers."""
    for name, p in sorted(ports(rec).items()):
        if p.get("captive"):
            return name
    if rec.get("captive_connector"):
        return "captive"
    return None


def end_info(rec, end):
    if is_cable(rec):
        return (rec.get("ends") or {}).get(end) or {}
    if end == "captive":
        return {"connector": rec.get("captive_connector"), "at": (rec.get("captive_at") or {}).get("at"),
                "port": (rec.get("captive_at") or {}).get("port"), "seated": (rec.get("captive_at") or {}).get("seated", 0.0)}
    p = ports(rec).get(end) or {}
    return {"connector": p.get("connector"), "at": p.get("at"), "port": p.get("to_port"), "seated": p.get("seated", 0.0)}


def plugged_into(state, dev, port):
    """(cable_or_captive_id, end) occupying a device port, or (None, None)."""
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if is_cable(rec):
            for end in ("a", "b"):
                e = rec["ends"].get(end) or {}
                if e.get("at") == dev and e.get("port") == port:
                    return eid, end
        else:
            ce = captive_end(rec)
            if ce:
                info = end_info(rec, ce)
                if info.get("at") == dev and info.get("port") == port:
                    return eid, ce
    return None, None


def _set_end(w, cid, end, at, port, seated, **extra):
    rec = w.mut(cid)
    if is_cable(rec):
        e = rec["ends"].setdefault(end, {})
        e.update(at=at, port=port, seated=seated)
        e.update(extra)
    elif end == "captive":
        if at is None:
            rec["captive_at"] = None
        else:
            rec["captive_at"] = {"at": at, "port": port, "seated": seated}
            rec["captive_at"].update(extra)
        rec["connected_to"] = at
    else:
        p = rec["ports"][end]
        p.update(at=at, to_port=port, seated=seated)
        p.update(extra)


def _anchor_cell(state, eid):
    rid, cells = C.anchor_cells(state, eid)
    if not cells and rid == state["robot"].get("room"):
        return rid, tuple(state["robot"].get("pos") or (0, 0))
    return rid, (cells[0] if cells else None)


def _reach_ok(state, a, b, length):
    ra, ca = _anchor_cell(state, a)
    rb, cb = _anchor_cell(state, b)
    if ra is None or rb is None:
        return True
    if ra != rb:
        return False
    if ca is None or cb is None:
        return True
    dist = C.dist_cells(ca, cb) * geometry.CELL + abs(geometry.height_m(state, a) - geometry.height_m(state, b))
    return dist <= length + 0.3


@action("connect_cable", params={"target": "entity", "destination": "entity", "port": "str", "end": "?str:a|b",
                                 "orientation": "?str:correct|reversed", "seat": "?str:full|partial", "force": "?bool"},
        duration=4.0, energy=0.005, category="physical",
        inverse=lambda ctx, before: {"action": "disconnect_cable", "target": ctx.args["target"], "destination": ctx.args["destination"]},
        doc="Plug one end of a cable (or a peripheral's / charger's captive plug) into a named port of a device. The connector must match "
            "the port and go in the right way up (orientation=reversed or a mismatched connector needs force=true and bends pins); "
            "seat=partial leaves an intermittent link. The cable must reach between the two devices.")
def connect_cable(ctx):
    w = ctx.w
    state = w.s
    cid, dev, port = ctx.args["target"], ctx.args["destination"], ctx.args["port"]
    crec = ctx.obj(cid)
    drec = ctx.obj(dev, touch=True)
    percept.refuse_unfamiliar(ctx, cid, dev)
    dangling = is_cable(crec) and crec.get("relation") == "attached"
    if not (ctx.holding(cid) or dangling or not crec.get("portable")):
        if not (crec.get("usb_device") or crec.get("captive_connector")):
            ctx.obj(cid, touch=True)
    require(port in ports(drec), "No port named {} on {} (ports: {})".format(port, dev, ", ".join(sorted(ports(drec)))), "argument")
    require(ctx.free_hand() is not None or ctx.holding(cid), "You need a free hand to handle the connector", "capability")
    pinfo = ports(drec)[port]
    require(not pinfo.get("captive"), "That is the device's own plug, not a socket")
    occupant, _ = plugged_into(state, dev, port)
    require(occupant is None, "That port is already in use (by {})".format(occupant) if occupant != cid else "Already connected there")
    # choose the end
    if is_cable(crec):
        ends = [ctx.args["end"]] if ctx.args.get("end") else ["a", "b"]
        free = [e for e in ends if not (crec["ends"].get(e) or {}).get("at")]
        require(free, "Both ends of that cable are already plugged in")
        matching = [e for e in free if (crec["ends"].get(e) or {}).get("connector") == pinfo.get("connector")]
        end = matching[0] if matching else free[0]
        other = "b" if end == "a" else "a"
        other_at = (crec["ends"].get(other) or {}).get("at")
        length = C.num(crec.get("length_m"), 1.0)
    else:
        end = captive_end(crec)
        require(end, "It has no plug or cable to connect", "argument")
        require(not end_info(crec, end).get("at"), "Its plug is already connected; disconnect it first")
        other_at = cid
        length = C.num(crec.get("captive_length_m"), C.num(crec.get("cable_length_m"), 1.5))
    connector = end_info(crec, end).get("connector")
    force = bool(ctx.args.get("force"))
    if connector != pinfo.get("connector"):
        if ctx.realistic:
            # the plug just does not go in; its exact kind (a 4.5 vs 5.5 mm barrel) would identify a look-alike brick
            require(force, "That plug does not fit the {} port".format(pinfo.get("label", port)), "precondition")
        require(force, "A {} plug does not fit the {} port ({})".format(C.CONNECTOR_NAMES.get(connector, connector),
                C.CONNECTOR_NAMES.get(pinfo.get("connector"), pinfo.get("connector")), pinfo.get("label", port)), "precondition")
        _bend(w, dev, port, cid, "forced a mismatched connector")
        if ctx.realistic:
            return "Forced the plug into {} {}: pins bent, no connection".format(dev, port)
        return "Forced the {} plug into {} {}: pins bent, no connection".format(connector, dev, port)
    if (ctx.args.get("orientation") or "correct") == "reversed" and connector in C.KEYED:
        require(force, "It will not go in that way up; flip the connector", "precondition")
        _bend(w, dev, port, cid, "forced upside down")
        return "Forced the plug in upside down: pins bent, no connection"
    if other_at and other_at != dev:
        require(_reach_ok(state, other_at, dev, length), "The cable is too short to reach between {} and {}".format(other_at, dev))
    seated = 1.0 if (ctx.args.get("seat") or "full") == "full" else 0.5
    extra = {}
    if connector == "rj45":
        extra["tab_ok"] = end_info(crec, end).get("tab_ok", True)
    _set_end(w, cid, end, dev, port, seated, **extra)
    if is_cable(crec) and crec.get("location") == "robot":
        # one end in: the cable now hangs from the device it is plugged into
        if not other_at:
            from ...systems.core import release_from_hands
            release_from_hands(w, cid)
            m = w.mut(cid)
            m["location"], m["relation"] = dev, "attached"
            for key in ("pos", "spos", "shelf", "held_hand"):
                m.pop(key, None)
            w.log_move(cid, "robot", dev)
    _overvoltage_check(w, cid)
    C.refresh(w)
    return "Connected {} to {} {}{}".format(cid, dev, pinfo.get("label", port), " (not fully seated)" if seated < 1 else "")


def _bend(w, dev, port, cid, why):
    m = w.mut(dev)
    m["ports"][port]["bent"] = True
    w.stat("damage")
    C.harm(w, "electrical.port_damaged:{}:{}".format(dev, port), why=why, by=cid)
    C.event(w, "port_damaged", dev, port=port)


def _overvoltage_check(w, cid):
    """A look-alike barrel plug with too high a voltage fries the device when energized."""
    state = w.s
    rec = state["entities"][cid]
    if not rec.get("captive_connector"):
        return
    at = (rec.get("captive_at") or {}).get("at")
    if not at:
        return
    dev = state["entities"][at]
    want = C.num(dev.get("input_v"), 0.0)
    have = C.num(rec.get("output_v"), 0.0)
    if want and have > want * 1.1 and P.supply_ok(state, cid) and not dev.get("broken"):
        m = w.mut(at)
        m["broken"] = True
        m["fried"] = True
        w.stat("damage")
        C.harm(w, "electrical.overvoltage:" + at, adapter=cid, volts=have)
        C.event(w, "device_fried", at)


@action("disconnect_cable", params={"target": "entity", "destination": "?entity", "port": "?str", "grip": "?str:connector|cable",
                                    "release_latch": "?bool", "eject_first": "?bool"},
        duration=lambda ctx: 3.0 + ctx.data.get("wait", 0.0), energy=0.005, category="physical",
        inverse=lambda ctx, before: ({"action": "connect_cable", "target": ctx.data["cable"], "destination": ctx.data["device"],
                                      "port": ctx.data["port"]} if ctx.data.get("cable") else None),
        doc="Unplug one end of a cable (target=cable with destination=the device to pull it from, or target=device with port=...). "
            "Grip the connector body (grip=cable yanks it: strain, and a light device at the other end may be dragged off its surface). "
            "Latching Ethernet/DisplayPort plugs need the latch released (default) or the tab snaps. eject_first=true ejects a "
            "storage device through its computer first (waits for a short transfer); pulling a drive mid-write corrupts it.")
def disconnect_cable(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    trec = ctx.obj(target)
    percept.refuse_unfamiliar(ctx, target, ctx.args.get("destination"))
    if not (trec.get("captive_connector") or is_cable(trec) and trec.get("relation") == "attached"):
        ctx.obj(target, touch=True)   # captive plugs and dangling cable ends are handled at the device end
    port = ctx.args.get("port")
    dest = ctx.args.get("destination")
    if is_cable(trec) or captive_end(trec) and not port:
        cid = target
        crec = trec
        if is_cable(crec):
            plugged = [e for e in ("a", "b") if (crec["ends"].get(e) or {}).get("at")]
            if dest:
                plugged = [e for e in plugged if crec["ends"][e].get("at") == dest]
            require(plugged, "That cable is not plugged into {}".format(dest) if dest else "That cable is not plugged in")
            require(len(plugged) == 1 or dest, "Both ends are plugged in; say which device to unplug it from (destination)", "argument")
            end = plugged[0]
        else:
            end = captive_end(crec)
            require(end_info(crec, end).get("at"), "It is not connected")
        dev = end_info(crec, end)["at"]
        port = end_info(crec, end)["port"]
    else:
        require(port, "Name the port to unplug (port=...)", "argument")
        dev = target
        cid, end = plugged_into(state, dev, port)
        require(cid, "Nothing is plugged into that port")
        crec = state["entities"][cid]
    ctx.obj(dev, touch=True)
    connector = end_info(crec, end).get("connector")
    grip = ctx.args.get("grip") or "connector"
    release = ctx.args.get("release_latch")
    release = True if release is None else release
    storage = _storage_at(state, cid, end, dev)
    ctx.data.update(cable=cid, device=dev, port=port)
    if storage:
        _storage_removal(ctx, storage, dev, cid)
    if connector in LATCHING and not release:
        if connector == "rj45":
            _set_end(w, cid, end, dev, port, end_info(crec, end).get("seated", 1.0), tab_ok=False)
            m = w.mut(cid)
            m["damaged"] = True
            w.stat("damage")
            C.harm(w, "electrical.latch_snapped:" + cid)
        else:
            _bend(w, dev, port, cid, "pulled without pressing the latch")
    if grip == "cable":
        m = w.mut(cid)
        m["damaged"] = True
        m["strain_damage"] = True
        w.stat("damage")
        C.harm(w, "electrical.cable_yanked:" + cid)
        _drag_other_end(ctx, cid, end)
    _set_end(w, cid, end, None, None, 0.0)
    crec = w.ent(cid)
    still = [e for e in (("a", "b") if is_cable(crec) else ()) if (crec["ends"].get(e) or {}).get("at")]
    if is_cable(crec) and crec.get("relation") == "attached" and crec.get("location") == dev:
        if still:
            other_dev = crec["ends"][still[0]]["at"]
            m = w.mut(cid)
            m["location"] = other_dev
            w.log_move(cid, dev, other_dev)
        else:
            hand = ctx.free_hand()
            require(hand is not None, "You need a free hand to take the cable", "capability")
            ctx.check_lift(cid, (hand,))     # the freed cable stays in that hand (kernel v3.0 body)
            m = w.mut(cid)
            m["location"], m["relation"], m["held_hand"] = "robot", "held", hand
            w.robot_m()["hands"][hand] = cid
            w.log_move(cid, dev, "robot")
    C.refresh(w)
    return "Disconnected {} from {} {}".format(cid, dev, port)


def _storage_at(state, cid, end, dev):
    """The storage device involved in this unplug (the drive itself or the drive at the cable's other end)."""
    ents = state["entities"]
    candidates = [dev, cid]
    crec = ents[cid]
    if is_cable(crec):
        other = "b" if end == "a" else "a"
        candidates.append((crec["ends"].get(other) or {}).get("at"))
    for c in candidates:
        if c and C.storage_device(ents.get(c, {})):
            return c
    return None


def _storage_removal(ctx, drive, dev, cid):
    w = ctx.w
    rec = w.ent(drive)
    if not rec.get("mounted"):
        return
    remaining = C.num(rec.get("transfer_remaining_s"), 0.0)
    if ctx.args.get("eject_first"):
        host = rec.get("host")
        hrec = w.s["entities"].get(host or "") or {}
        require(host and hrec.get("power_state") == "on", "Eject needs the host computer on (its screen shows the eject option)")
        if remaining > 0:
            if ctx.realistic:
                # the copy dialog's estimate is coarse, and the robot checks it every half minute: neither the message
                # nor the time spent waiting gives the exact transfer time left
                require(remaining <= 300, "The drive is busy (the copy dialog says {}); wait for it to finish before "
                                          "ejecting".format(_minutes_left(remaining)))
                ctx.data["wait"] = math.ceil(remaining / 30.0) * 30.0
            else:
                require(remaining <= 300, "The drive is busy ({:.0f} s of transfer left); wait for it to finish before ejecting".format(remaining))
                ctx.data["wait"] = remaining
            m = w.mut(drive)
            m["transfer_remaining_s"] = 0.0
            m["job"] = None
        m = w.mut(drive)
        m["mounted"] = False
        m["ejected"] = True
        return
    m = w.mut(drive)
    if remaining > 0 or rec.get("job") == "firmware_update":
        m["corrupted"] = True
        m["transfer_remaining_s"] = 0.0
        C.harm(w, "electrical.data_corruption:" + drive, cause="unplugged_mid_transfer")
        C.event(w, "data_corrupted", drive)
    else:
        m["unsafe_removal"] = True


def _minutes_left(seconds):
    """A file-copy dialog's coarse estimate."""
    if seconds <= 60:
        return "less than a minute left"
    if seconds <= 600:
        return "about {} minutes left".format(int(math.ceil(seconds / 60.0)))
    return "more than 10 minutes left"


def _drag_other_end(ctx, cid, end):
    """Yanking a cable drags a light device at the far end; off a raised surface it falls."""
    w = ctx.w
    state = w.s
    crec = state["entities"][cid]
    other = None
    if is_cable(crec):
        other = (crec["ends"].get("b" if end == "a" else "a") or {}).get("at")
    if not other or other not in state["entities"]:
        return
    orec = state["entities"][other]
    if not orec.get("portable") or C.num(orec.get("mass_kg"), 1.0) > 3.0 or orec.get("relation") != "on":
        return
    parent = orec.get("location")
    if parent not in state["entities"]:
        return
    h = geometry.height_m(state, other)
    if h < 0.3:
        return
    rid = C.room_of(state, other)
    # it comes off its table toward the robot that pulled it: the kernel's fall (kernel v3.0 physics) lands it on a free
    # floor cell next to that and decides from its drop tolerance whether it breaks (phys.broke: physical.py marks a
    # broken device dead); the breakage and damage flags follow the kernel's robot attribution
    _, here = C.anchor_cells(state, parent)
    toward = tuple(w.robot.get("pos") or (here[0] if here else (0, 0)))
    landed = ctx.fall(other, height_m=h, at=(rid, toward), cause="cable_yanked")
    if not landed.get("fell"):
        return
    C.harm(w, "electrical.device_dragged_off:" + other, height_m=round(h, 2))
    C.event(w, "device_fell", other)


# ----------------------------------------------------------------------------------------
# link graph

@C.declare_index("linkables", ("ports", "captive_connector", "usb_device", "storage", "usb_storage", "hub",
                                 "category", "ends"))
def _linkable(rec):
    """Static index predicate: cables, devices with ports or captive plugs, USB devices, storage, hubs."""
    return bool(rec.get("ports") or rec.get("captive_connector") or rec.get("usb_device") or C.storage_device(rec)
                or rec.get("hub") or (rec.get("category") == "cable" and isinstance(rec.get("ends"), dict)))


def linkable_ids(state, w=None):
    return C.ids(w, "linkables", _linkable) if w is not None else sorted(state["entities"])


def all_links(state, w=None):
    """[{a, pa, b, pb, cable, quality}] for every two-sided connection (cables and captive plugs)."""
    ents = state["entities"]
    out = []
    for cid in linkable_ids(state, w):
        rec = ents.get(cid)
        if rec is None:
            continue
        if is_cable(rec):
            ea, eb = rec["ends"].get("a") or {}, rec["ends"].get("b") or {}
            if ea.get("at") and eb.get("at"):
                q = min(C.num(ea.get("seated"), 0), C.num(eb.get("seated"), 0))
                if rec.get("damaged") and rec.get("strain_damage"):
                    q = min(q, 0.5)
                out.append({"a": ea["at"], "pa": ea.get("port"), "b": eb["at"], "pb": eb.get("port"), "cable": cid, "quality": q,
                            "data": bool(rec.get("data_capable", True)), "max_w": C.num(rec.get("max_w"), 60.0)})
        else:
            ce = captive_end(rec)
            if ce:
                info = end_info(rec, ce)
                if info.get("at"):
                    out.append({"a": cid, "pa": ce, "b": info["at"], "pb": info.get("port"), "cable": cid,
                                "quality": C.num(info.get("seated"), 0), "data": True, "max_w": C.num(rec.get("source_w"), 999.0)})
    for link in out:
        for side in ("a", "b"):
            dev = ents.get(link[side]) or {}
            p = ports(dev).get(link["p" + side]) or {}
            if p.get("bent"):
                link["quality"] = 0.0
    return out


def port_dir(state, dev, port):
    if port == "captive":
        return "charger_out"
    return (ports(state["entities"].get(dev) or {}).get(port) or {}).get("dir")


def device_on(state, eid):
    rec = state["entities"].get(eid) or {}
    if C.dead(rec):
        return False
    if rec.get("plug_required") and not P.supply_ok(state, eid):
        return False
    if rec.get("battery_powered") and C.num(rec.get("battery_level"), 1.0) <= 0:
        return False
    if rec.get("rechargeable") and not rec.get("plug_required") and C.num(rec.get("battery_level"), 1.0) <= 0.0 and not rec.get("_ext_power"):
        return False
    if "power_state" in rec:
        return rec.get("power_state") == "on"
    return bool(rec.get("on", True))


def _video_ok(state, src, port):
    rec = state["entities"].get(src) or {}
    p = ports(rec).get(port) or {}
    if p.get("video") is False:
        return False
    if port == "mb_hdmi" and rec.get("discrete_gpu"):
        return False
    return True


@C.refresher(40)
def sync_links(w):
    state = w.s
    ents = state["entities"]
    linkables = [e for e in linkable_ids(state, w) if e in ents]
    links = all_links(state, w)
    link_by_cable = {l["cable"]: l for l in links}
    by_dev = {}
    for link in links:
        by_dev.setdefault(link["a"], []).append((link["pa"], link["b"], link["pb"], link))
        by_dev.setdefault(link["b"], []).append((link["pb"], link["a"], link["pa"], link))
    for eid in linkables:
        rec = ents[eid]
        if not ports(rec) and not captive_end(rec):
            continue
        conns = {}
        for mine, other, oport, link in sorted(by_dev.get(eid, []), key=lambda t: (str(t[0]), t[1])):
            conns[mine] = {"to": other, "to_port": oport, "cable": link["cable"], "quality": "ok" if link["quality"] >= 0.99 else ("intermittent" if link["quality"] > 0 else "none")}
        if conns != (rec.get("_links") or {}):
            w.mut(eid)["_links"] = conns
    # displays (TVs, monitors)
    for eid in C.ids(w, "displays"):
        rec = ents.get(eid)
        if rec is None or rec.get("category") not in ("tv", "monitor"):
            continue
        display, source = _display_of(state, eid, rec)
        C.set_if(w, eid, "display", display)
        C.set_if(w, eid, "display_source", source)
    # ARC audio, USB recognition (hubs first: a device behind a hub needs the hub itself connected), Ethernet
    order = [e for e in linkables if ents[e].get("hub")] + [e for e in linkables if not ents[e].get("hub")]
    for eid in order:
        rec = ents[eid]
        links_here = (ents[eid].get("_links") or {})
        if rec.get("category") == "audio" and "hdmi_arc" in ports(rec):
            link = links_here.get("hdmi_arc")
            arc_tv = None
            if link and link["quality"] == "ok":
                tv = ents.get(link["to"]) or {}
                if (ports(tv).get(link["to_port"]) or {}).get("arc"):
                    arc_tv = link["to"]
            C.set_if(w, eid, "arc_link", arc_tv)
        if rec.get("usb_device") or C.storage_device(rec) or rec.get("hub"):
            host = None
            for mine, link in sorted(links_here.items()):
                d = port_dir(state, link["to"], link["to_port"])
                if d in ("host",) and link["quality"] != "none":
                    partner = ents.get(link["to"]) or {}
                    if partner.get("hub") and not partner.get("recognized"):
                        continue        # an unconnected hub has no upstream host
                    host = link["to"]
            dlink = next((link_by_cable[v["cable"]] for _, v in sorted(links_here.items()) if v["cable"] in link_by_cable), None)
            data_ok = bool(dlink and dlink["data"])
            recog = bool(host and device_on(state, host) and data_ok)
            old_host = rec.get("host")
            C.set_if(w, eid, "recognized", recog)
            C.set_if(w, eid, "host", host if recog else None)
            if C.storage_device(rec):
                if recog and not rec.get("ejected") and not rec.get("mounted"):
                    w.mut(eid)["mounted"] = True
                elif not recog and rec.get("mounted"):
                    m = w.mut(eid)
                    m["mounted"] = False
                    if C.num(m.get("transfer_remaining_s"), 0.0) > 0:
                        m["corrupted"] = True
                        m["transfer_remaining_s"] = 0.0
                        # the robot's when it cut the host or the link (its action, or a cut it caused): a grid outage
                        # or a resident's doing mid-copy is exogenous (common.consequence)
                        ids = {eid, old_host} | (P.cut_ids(state, old_host) if old_host in ents else set())
                        by = "exogenous" if not C.grid_on(state) and not C.robot_acting(w) else None
                        C.consequence(w, "electrical.data_corruption:" + eid, ids, by=by, cause="host_lost")
                if not host and rec.get("ejected"):
                    w.mut(eid)["ejected"] = False
        if any((p or {}).get("connector") == "rj45" for p in ports(rec).values()) and rec.get("category") != "network":
            ok = False
            for mine, link in sorted(links_here.items()):
                if (ports(rec).get(mine) or {}).get("connector") == "rj45" and link["quality"] == "ok":
                    other = ents.get(link["to"]) or {}
                    if other.get("category") == "network" and device_on(state, link["to"]):
                        ok = bool(other.get("wan_ok", True))
            C.set_if(w, eid, "network_link", ok)


def _display_of(state, eid, rec):
    if not device_on(state, eid):
        return ("off" if not (rec.get("plug_required") and P.supply_ok(state, eid)) else "standby"), None
    if rec.get("power_state") == "booting":
        return "starting", None
    settings = rec.get("settings") or {}
    active = settings.get("input")
    if active == "tuner":
        return "tuner", None
    link = (rec.get("_links") or {}).get(active)
    if not link:
        return "no_signal", None
    src = link["to"]
    if port_dir(state, src, link["to_port"]) not in ("out", "host"):
        return "no_signal", None
    if not device_on(state, src) or not _video_ok(state, src, link["to_port"]):
        return "no_signal", None
    if link["quality"] != "ok":
        return "intermittent", src
    srec = state["entities"][src]
    return "showing " + str(srec.get("label", src)), src


def cable_inspect_details(state, eid, rec):
    out = {}
    if is_cable(rec):
        out["ends"] = {e: {"connector": (rec["ends"].get(e) or {}).get("connector"), "plugged_into": (rec["ends"].get(e) or {}).get("at"),
                           "port": (rec["ends"].get(e) or {}).get("port"),
                           "seated": (rec["ends"].get(e) or {}).get("seated")} for e in ("a", "b")}
        out["data_capable"] = rec.get("data_capable", True)
        out["max_w"] = rec.get("max_w")
        out["damaged"] = bool(rec.get("damaged"))
    return out


@C.refresher(45)
def sync_workstation(w):
    """Ergonomic geometry: monitor screen-top height and which side of the keyboard the mouse is on."""
    state = w.s
    ents = state["entities"]
    station = [e for e in C.ids(w, "workstation") if e in ents]
    for eid in station:
        rec = ents[eid]
        if rec.get("category") == "monitor" or rec.get("type") in ("laptop", "laptop_barrel"):
            top = geometry.height_m(state, eid) + C.num((rec.get("size_m") or [0, 0, 0.3])[2], 0.3)
            if rec.get("type") in ("laptop", "laptop_barrel"):
                top = geometry.height_m(state, eid) + 0.22
            C.set_if(w, eid, "screen_top_m", round(top, 2))
        if rec.get("type") == "computer_mouse" and rec.get("spos") is not None:
            kb = next((k for k in station if ents[k].get("type") == "keyboard" and ents[k].get("location") == rec.get("location")
                       and ents[k].get("spos") is not None), None)
            side = None
            if kb:
                kx = ents[kb]["spos"][0] + C.num(ents[kb]["size_m"][0], 0.44) / 2
                side = "right" if rec["spos"][0] > kx else "left"
            C.set_if(w, eid, "side_of_keyboard", side)
