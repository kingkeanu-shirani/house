"""Chargers and charging (fidelity G for pad/dock alignment and connector fit, P-approx energy).

Charge sources of a rechargeable device (battery_level 0..1, battery_capacity_wh):
  * a cable/captive link from a powered source port (wall brick, charging station, console in
    rest mode with USB power, computer) to a charge_in port;
  * a Qi pad: the device's coil within qi_tolerance_m (~12 mm) of the pad's coil_center_m;
  * a dock/puck/clip whose dock_for matches the device's dock_kind, fully seated;
  * an earbuds case: the bud seated in its own side's slot of a case that holds charge;
  * battery chargers: NiMH bays (alkaline cells must never go in) and platform tool chargers
    (pack temperature 5-40 C, never a swollen pack);
  * EV supply equipment: the car charges while its session is active and the connector latched.
Power delivered = min(source W, cable W, device max W) x efficiency, tapered above 80 %;
devices that are on consume consumption_w. Laptops below min_charge_w show "plugged in, not
charging". Hazards: a swollen battery on charge goes into thermal runaway (~15 min, fire); a
device charging on bedding/cushions/under a pillow overheats (harm); a wrong-voltage barrel plug
fries the device (cables.py).
`charge(target, destination, ...)` does the right physical thing for the destination kind.
"""
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import before, hook, system
from . import common as C
from . import power as P
from . import cables as K

RUNAWAY_S = 900.0
SOFT_HEAT_S = 600.0


def level_key(rec):
    """Field holding the state of charge, or None if the entity is not rechargeable."""
    if rec.get("category") == "battery":
        return "charge" if str(rec.get("part_type", "")).startswith("tool_pack") else None
    if rec.get("rechargeable") and "battery_level" in rec:
        return "battery_level"
    return None


def rechargeable(rec):
    return level_key(rec) is not None


def _chargeable(rec):
    """Static index predicate: cells (NiMH bays, tool packs) and rechargeable devices."""
    return rec.get("category") == "battery" or bool(rec.get("rechargeable") and "battery_level" in rec)


def source_available_w(state, dev, port):
    """Watts a port can source right now (0 if unpowered)."""
    rec = state["entities"].get(dev) or {}
    if port == "captive":
        if rec.get("plug_required") and not P.supply_ok(state, dev):
            return 0.0
        return C.num(rec.get("source_w"), 0.0)
    p = (rec.get("ports") or {}).get(port) or {}
    d = p.get("dir")
    if d == "source":
        if rec.get("plug_required") and not P.supply_ok(state, dev):
            return 0.0
        return C.num(p.get("source_w"), 5.0)
    if d in ("host", "upstream") and C.num(p.get("source_w"), 0.0) > 0:
        if rec.get("plug_required") and not P.supply_ok(state, dev):
            return 0.0
        ps = rec.get("power_state")
        if ps == "on" or (ps == "rest" and rec.get("usb_power_in_rest")) or (ps is None and rec.get("on", True)):
            return C.num(p.get("source_w"), 4.5)
        if rec.get("battery_capacity_wh") and ps == "on":
            return C.num(p.get("source_w"), 4.5)
        return 0.0
    return 0.0


def qi_aligned(state, dev, pad):
    rec, prec = state["entities"][dev], state["entities"][pad]
    if not rec.get("qi") or rec.get("relation") != "on" or rec.get("spos") is None:
        return False
    w, d = geometry.footprint_m(rec)
    off = rec.get("qi_coil_offset_m") or [0.0, 0.0]
    cx, cy = rec["spos"][0] + w / 2 + off[0], rec["spos"][1] + d / 2 + off[1]
    coil = prec.get("coil_center_m") or [0.08, 0.08]
    if rec.get("case_thickness_mm") and C.num(rec.get("case_thickness_mm")) > 5:
        return False
    return math.hypot(cx - coil[0], cy - coil[1]) <= C.num(prec.get("qi_tolerance_m"), 0.012) + 1e-9


def aligned_spos(state, dev, pad):
    rec, prec = state["entities"][dev], state["entities"][pad]
    w, d = geometry.footprint_m(rec)
    off = rec.get("qi_coil_offset_m") or [0.0, 0.0]
    coil = prec.get("coil_center_m") or [0.08, 0.08]
    return [round(coil[0] - w / 2 - off[0], 3), round(coil[1] - d / 2 - off[1], 3)]


def sources(state, eid):
    """[(source_id, watts, kind)] currently able to charge the device."""
    ents = state["entities"]
    rec = ents[eid]
    out = []
    links = rec.get("_links") or {}
    for mine, link in sorted(links.items()):
        p = (rec.get("ports") or {}).get(mine) or {}
        if not p.get("charge_in") or link.get("quality") != "ok":
            continue
        avail = source_available_w(state, link["to"], link["to_port"])
        if avail <= 0:
            continue
        cable = ents.get(link.get("cable") or "") or {}
        if K.is_cable(cable):
            avail = min(avail, C.num(cable.get("max_w"), 60.0))
        charger = ents.get(link["to"]) or {}
        if link["to_port"] == "captive" and charger.get("output_v") and rec.get("input_v"):
            if C.num(charger["output_v"]) < C.num(rec["input_v"]) * 0.9:
                continue
        out.append((link["to"], avail, "cable"))
    loc = rec.get("location")
    parent = ents.get(loc or "") or {}
    if parent.get("wireless") and qi_aligned(state, eid, loc):
        if not parent.get("plug_required") or P.supply_ok(state, loc):
            out.append((loc, min(C.num(parent.get("source_w"), 15.0), C.num(rec.get("qi_max_w"), 7.5)), "qi"))
    if parent.get("dock_for") and rec.get("dock_seated") and rec.get("dock_kind") == parent.get("dock_for"):
        if not parent.get("plug_required") or P.supply_ok(state, loc):
            out.append((loc, C.num(parent.get("source_w"), 5.0), "dock"))
    if parent.get("bud_slots") is not None and rec.get("charge_port") == "case_contacts":
        slots = parent.get("bud_slots") or {}
        side = next((s for s, v in sorted(slots.items()) if v == eid), None)
        if side and side == rec.get("side") and C.num(parent.get("battery_level"), 0.0) > 0.02:
            out.append((loc, 0.5, "case"))
    if rec.get("relation") == "part" and parent.get("charges_chemistry") is not None or (rec.get("relation") == "part" and parent.get("platform")):
        if not parent.get("plug_required") or P.supply_ok(state, loc):
            out.append((loc, C.num(parent.get("source_w"), 4.0), "bay"))
    return out


CHARGING_IDLE = C.IdleNote("electrical.charging", (("chargeables", _chargeable), "metered", ("linkables", K._linkable),
                                                   "powerable"),
                           ignore=lambda rec: ("internal_temp_c", "load_a", "temperature_c"))


def _charging_inputs(w):
    """The charging step's inputs outside the entity table: the grid and the temperature of rooms whose charger bays
    hold packs at a charging-temperature limit."""
    state = w.s
    ents = state["entities"]
    rooms = set()
    for eid in C.ids(w, "chargeables", _chargeable):
        rec = ents.get(eid) or {}
        parent = ents.get(rec.get("location") or "") or {}
        if rec.get("relation") == "part" and parent.get("charge_temp_c"):
            rid = C.room_of(state, eid)
            if rid:
                rooms.add(rid)
    return {"grid": C.grid_on(state), "temps": C.room_temps(state, rooms)}


def charging_idle(w):
    """Exact idle probe of electrical.charging (common.IdleNote)."""
    note = CHARGING_IDLE
    if not note.quiet(w):
        return False
    extra = note.extra or {}
    state = w.s
    return C.grid_on(state) == extra.get("grid") and C.room_temps(state, extra.get("temps") or {}) == extra.get("temps")


@system("electrical.charging", order=62, max_dt=30.0, idle=charging_idle)
def charging_system(w, dt, tick):
    before = w.mutation_count
    _charging_step(w, dt, tick)
    wrote = w.mutation_count != before
    CHARGING_IDLE.after(w, wrote, None if wrote else _charging_inputs(w))


def _charging_step(w, dt, tick):
    state = w.s
    ents = state["entities"]
    loads = {}
    from ...kernel.physics import room_temperature
    for eid in C.ids(w, "chargeables", _chargeable):
        rec = ents.get(eid)
        if rec is None:
            continue
        if rec.get("category") == "battery" and rec.get("relation") == "part":
            parent = ents.get(rec.get("location") or "") or {}
            if parent.get("charges_chemistry") is not None:
                _nimh_bay(w, eid, rec, dt, loads)
                continue
        key = level_key(rec)
        if key is None:
            continue
        srcs = [] if C.dead(rec) else sources(state, eid)      # a broken or water-logged device takes no charge
        cap = max(0.1, C.num(rec.get("battery_capacity_wh"), C.num(rec.get("capacity_wh"), 15.0)))
        level = C.clamp(C.num(rec.get(key), 0.5), 0.0, 1.0)
        best = max(srcs, key=lambda t: (t[1], t[0])) if srcs else None
        watts = 0.0
        reason = None
        if best:
            src_rec = ents[best[0]]
            watts = min(best[1], C.num(rec.get("max_charge_w"), C.num(src_rec.get("source_w"), 20.0))) * C.num(src_rec.get("efficiency"), 0.87)
            if rec.get("ev") and rec.get("charge_session") not in ("active",):
                watts, reason = 0.0, "session_" + str(rec.get("charge_session", "idle"))
            if best[2] == "bay" and src_rec.get("charge_temp_c"):
                lo, hi = src_rec["charge_temp_c"]
                temp = room_temperature(state, C.room_of(state, eid) or "")
                if not (lo <= temp <= hi):
                    watts, reason = 0.0, "temperature_hold"
            minimum = C.num(rec.get("min_charge_w"), 0.0)
            if minimum and best[1] < minimum:
                watts, reason = 0.0, "charger_too_weak"
            if level > 0.8:
                watts *= max(0.15, (1.0 - level) / 0.2)
            if rec.get("battery_swollen") and watts > 0:
                _swollen_charging(w, eid, rec, dt)
        consumption = C.num(rec.get("consumption_w"), 0.3) if rec.get("power_state") == "on" else 0.0
        net = watts - consumption
        new = C.clamp(level + net * dt / 3600.0 / cap, 0.0, 1.0)
        charging = bool(best and watts > 0.05 and new < 0.9995)
        if abs(new - level) > 1e-6:
            w.mut(eid)[key] = round(new, 5)
        C.set_if(w, eid, "charging", charging)
        C.set_if(w, eid, "charge_rate_w", round(net, 2) if best else 0.0)
        C.set_if(w, eid, "charger", best[0] if best else None)
        status = "full" if (new >= 0.9995 and best) else ("charging" if charging else (reason or ("plugged_in_not_charging" if best else "not_charging")))
        C.set_if(w, eid, "charge_status", status)
        if best and watts > 0:
            loads[best[0]] = loads.get(best[0], 0.0) + watts / max(0.5, C.num(ents[best[0]].get("efficiency"), 0.87))
        if rec.get("ev"):
            _ev_session(w, eid, new)
        if charging and C.on_soft_surface(state, eid, flammable=True, w=w):
            m = w.mut(eid)
            m["_soft_charge_s"] = round(C.num(m.get("_soft_charge_s"), 0.0) + dt, 1)
            amb = room_temperature(state, C.room_of(state, eid) or "")
            m["temperature_c"] = round(max(C.num(m.get("temperature_c"), amb), amb + min(24.0, 8.0 + m["_soft_charge_s"] / 60.0)), 1)
            if m["_soft_charge_s"] >= SOFT_HEAT_S:
                C.harm(w, "electrical.charging_on_soft_surface:" + eid)
    for eid in sorted(set(loads) | {e for e in C.ids(w, "metered") if C.num((ents.get(e) or {}).get("_load_w"), 0.0) > 0}):
        if eid not in ents:
            continue
        want = round(loads.get(eid, 0.0), 2)
        if C.num(ents[eid].get("_load_w"), 0.0) != want:
            w.mut(eid)["_load_w"] = want


def _nimh_bay(w, eid, rec, dt, loads):
    state = w.s
    charger = state["entities"][rec["location"]]
    if not P.supply_ok(state, rec["location"]) and charger.get("plug_required"):
        C.set_if(w, eid, "charging", False)
        return
    if rec.get("chemistry") not in (charger.get("charges_chemistry") or []):
        m = w.mut(eid)
        m["_bad_charge_s"] = round(C.num(m.get("_bad_charge_s"), 0.0) + dt, 1)
        C.harm(w, "electrical.charging_primary_cell:" + eid, charger=rec["location"])
        if m["_bad_charge_s"] >= 1800 and not m.get("leaking"):
            m["leaking"] = True
            m["condition"] = "leaking"
            m["temperature_c"] = 55.0
            C.event(w, "battery_leaking", eid)
        return
    cap = 2.4 if rec.get("part_type") == "battery_aa" else 1.0
    new = C.clamp(C.num(rec.get("charge"), 0.0) + 1.0 * dt / 3600.0 / cap, 0.0, 1.0)
    if abs(new - C.num(rec.get("charge"), 0.0)) > 1e-6:
        w.mut(eid)["charge"] = round(new, 5)
    C.set_if(w, eid, "charging", new < 0.9995)
    loads[rec["location"]] = loads.get(rec["location"], 0.0) + 1.5


def _swollen_charging(w, eid, rec, dt):
    m = w.mut(eid)
    m["_runaway_s"] = round(C.num(m.get("_runaway_s"), 0.0) + dt, 1)
    C.harm(w, "electrical.charging_swollen_battery:" + eid)
    if m["_runaway_s"] >= RUNAWAY_S and not m.get("thermal_runaway"):
        m["thermal_runaway"] = True
        m["broken"] = True
        m["temperature_c"] = 400.0
        w.stat("fire")
        C.harm(w, "electrical.battery_fire:" + eid)
        C.event(w, "battery_thermal_runaway", eid)


def _ev_session(w, eid, level):
    rec = w.ent(eid)
    if rec.get("charge_session") == "active" and level >= C.num(rec.get("target_level"), 0.8) - 1e-4:
        m = w.mut(eid)
        m["charge_session"] = "complete"
        m["connector_locked"] = False
        C.event(w, "ev_charge_complete", eid)


# ----------------------------------------------------------------------------------------
# the charge verb

@action("charge", params={"target": "entity", "destination": "entity", "port": "?str", "cable": "?entity", "slot": "?str",
                          "spos": "?list", "seat": "?str:full|partial", "force": "?bool"},
        duration=lambda ctx: ctx.data.get("seconds", 4.0), energy=0.005, category="physical",
        doc="Put a device on charge with the given charger: centre it on a Qi pad (spos overrides the placement), seat it on its "
            "dock/puck/clip (seat=partial leaves the contacts open), seat an earbud in its slot (slot=left|right) of a case, insert a cell "
            "or tool pack into a charger bay, plug a charger's captive plug into the device (port=...), or link a wall brick / charging "
            "station / console port to the device with a cable (cable=..., port=<source port>). EVs: target=vehicle, destination=EVSE "
            "(owner's permission, dry, undamaged cable). Returns the charging status.")
def charge(ctx):
    w = ctx.w
    state = w.s
    dev, dest = ctx.args["target"], ctx.args["destination"]
    require(dev != "robot", "The robot charges itself on its dock (dock there); its packs charge on their charging "
                            "station", "argument")
    drec = ctx.obj(dev)
    srec = ctx.obj(dest)
    if is_robot_pack(drec):
        return _charge_robot_pack(ctx, dev, dest, srec)
    via_cable = not (srec.get("wireless") or srec.get("dock_for") or srec.get("bud_slots") is not None or srec.get("captive_connector")
                     or (srec.get("slots") and (srec.get("charges_chemistry") is not None or srec.get("platform"))))
    if srec.get("captive_connector"):
        pass   # the captive plug is carried to the device: connect_cable checks reach to it and the cable length
    elif not via_cable or not ctx.args.get("port") or K.plugged_into(w.s, dest, ctx.args.get("port"))[0] is None:
        ctx.obj(dest, touch=True)
    seat = ctx.args.get("seat") or "full"
    seconds = 4.0
    if drec.get("battery_swollen") or drec.get("thermal_runaway"):
        C.log(w, "charging_swollen_attempt", device=dev)
    if srec.get("wireless"):
        require(ctx.holding(dev), "Hold the device first")
        spos = ctx.args.get("spos") or aligned_spos(state, dev, dest)
        from ...systems.core import place_item, release_from_hands
        release_from_hands(w, dev)
        place_item(ctx, dev, dest, "on", spos)
        w.log_move(dev, "robot", dest)
    elif srec.get("dock_for"):
        require(ctx.holding(dev), "Hold the device first")
        require(drec.get("dock_kind") == srec.get("dock_for"), "It does not fit that dock/charger (made for a different device)")
        docked = [e for e, r in sorted(state["entities"].items()) if r.get("location") == dest and r.get("relation") == "attached"]
        require(len(docked) < int(C.num(srec.get("dock_capacity"), 1)), "The dock is full")
        from ...systems.core import release_from_hands
        release_from_hands(w, dev)
        m = w.mut(dev)
        m["location"], m["relation"] = dest, "attached"
        for key in ("pos", "spos", "shelf", "held_hand"):
            m.pop(key, None)
        m["dock_seated"] = seat == "full"
        w.log_move(dev, "robot", dest)
    elif srec.get("bud_slots") is not None:
        require(ctx.holding(dev), "Hold the earbud first")
        require(srec.get("open"), "Open the case lid first")
        side = ctx.args.get("slot") or drec.get("side") or "left"
        require(side in srec["bud_slots"], "The case has slots left and right", "argument")
        require(not srec["bud_slots"].get(side), "That slot is occupied")
        from ...systems.core import release_from_hands
        release_from_hands(w, dev)
        m = w.mut(dev)
        m["location"], m["relation"] = dest, "attached"
        for key in ("pos", "spos", "shelf", "held_hand"):
            m.pop(key, None)
        w.mut(dest)["bud_slots"][side] = dev
        if drec.get("case_id") and drec.get("case_id") != dest:
            C.harm(w, "electrical.owner_mixup:" + dev, case=dest)
        w.log_move(dev, "robot", dest)
    elif srec.get("slots") and (srec.get("charges_chemistry") is not None or srec.get("platform")):
        require(ctx.holding(dev), "Hold the cell or pack first")
        slot = ctx.args.get("slot")
        if not slot:
            free = [s for s, info in sorted(srec["slots"].items()) if not info.get("item") and
                    (drec.get("part_type") in info.get("accepts", []))]
            require(free, "No free bay that fits it ({} does not fit this charger)".format(dev) if ctx.realistic else
                    "No free bay that fits it (a {} does not fit this charger)".format(drec.get("label")))
            slot = free[0]
        ctx.env.perform({"action": "install", "target": dev, "destination": dest, "slot": slot}, ctx)
        if drec.get("battery_swollen"):
            C.harm(w, "electrical.charging_swollen_battery:" + dev)
    elif srec.get("captive_connector"):
        port = ctx.args.get("port") or _charge_port(drec, srec.get("captive_connector"))
        require(port, "{} has no port that takes that plug".format(dev) if ctx.realistic else
                "{} has no port that takes the {} plug".format(dev, srec.get("captive_connector")))
        if drec.get("ev"):
            _ev_checks(ctx, dev, dest, drec, srec)
        args = {"action": "connect_cable", "target": dest, "destination": dev, "port": port}
        if seat == "partial":
            args["seat"] = "partial"
        if ctx.args.get("force"):
            args["force"] = True
        ctx.env.perform(args, ctx)
        if drec.get("ev"):
            m = w.mut(dev)
            m["ports"][port]["door_open"] = True
            if seat == "full" and C.num(drec.get("battery_level"), 0) < C.num(drec.get("target_level"), 0.8):
                m["charge_session"] = "active"
                m["connector_locked"] = True
            elif seat != "full":
                m["charge_session"] = "fault_not_latched"
    elif srec.get("ports") or srec.get("is_host"):
        port = ctx.args.get("port")
        source_ports = [p for p, info in sorted((srec.get("ports") or {}).items()) if info.get("dir") in ("source", "host")]
        require(port or source_ports, "Name the charger port to use (port=...)", "argument")
        port = port or source_ports[0]
        require(port in (srec.get("ports") or {}), "No port {} on {}".format(port, dest), "argument")
        cable, end = K.plugged_into(state, dest, port)
        if cable is None:
            cable = ctx.args.get("cable")
            require(cable, "Use a cable: say which one (cable=...)", "argument")
            ctx.env.perform({"action": "connect_cable", "target": cable, "destination": dest, "port": port}, ctx)
        crec = w.ent(cable)
        dev_port = _charge_port(drec, _free_end_connector(crec, dest, port))
        require(dev_port, "That cable's other end does not fit the {}'s charging port".format(dev if ctx.realistic else drec.get("label", dev)))
        args = {"action": "connect_cable", "target": cable, "destination": dev, "port": dev_port}
        if seat == "partial":
            args["seat"] = "partial"
        if ctx.args.get("force"):
            args["force"] = True
        ctx.env.perform(args, ctx)
    else:
        raise ActionError("That is not a charger, pad, dock or charging port", "argument")
    C.refresh(w)
    state = w.s
    srcs = sources(state, dev)
    ctx.data["seconds"] = seconds
    if srcs:
        if ctx.realistic:
            # what the device itself shows (watts need a USB meter)
            rec = state["entities"][dev]
            best = max(srcs, key=lambda t: (t[1], t[0]))
            if C.num(rec.get("min_charge_w"), 0.0) and best[1] < C.num(rec.get("min_charge_w"), 0.0):
                return "{} is connected to {}: it shows plugged in, not charging".format(dev, best[0])
            if rec.get("ev") and rec.get("charge_session") != "active":
                return "{} is connected to {}: the car does not start a charging session".format(dev, best[0])
            return "{} is on charge from {} (charging indicator on)".format(dev, best[0])
        return "{} is on charge from {} ({:.0f} W available)".format(dev, srcs[0][0], srcs[0][1])
    return "{} is on {} but NOT charging (check alignment, seating, power and compatibility)".format(dev, dest)


def is_robot_pack(rec):
    """A swappable robot battery pack (navigation_robot's type; the body owns the pack in the robot, kernel v3.0)."""
    return rec.get("part_type") == "robot_battery" or rec.get("category") == "robot_battery"


def robot_pack_station(rec):
    """The robot's own pack charger (navigation_robot's battery_charging_station): a charging station with a pack
    charge rate and no device ports."""
    return rec.get("category") == "charging_station" and rec.get("charge_rate_pct_per_min") is not None \
        and not rec.get("ports")


def _charge_robot_pack(ctx, pack, dest, srec):
    """The robot's 2.4 kWh packs charge only on their own station (TIER1_INTEGRATION 6 "electrical", Body): never in a
    household charger, a dock or a bay. On the station navigation_robot's spare-pack system charges it."""
    w = ctx.w
    require(robot_pack_station(srec), "A robot battery pack charges only on the robot's battery charging station",
            "precondition")
    require(ctx.holding(pack), "Hold the pack first (with both hands)")
    ctx.obj(dest, touch=True)
    from ...systems.core import place_item, release_from_hands
    release_from_hands(w, pack)
    place_item(ctx, pack, dest, "on", None)
    w.log_move(pack, "robot", dest)
    ctx.data["seconds"] = 4.0
    from ...kernel import physics
    if srec.get("plugged_in", True) and physics.is_powered(w, dest):
        return "{} is on the charging station (charging light on)".format(pack)
    return "{} is on the charging station but it is not charging (the station has no power)".format(pack)


def _charge_port(drec, connector):
    for name, p in sorted((drec.get("ports") or {}).items()):
        if p.get("charge_in") and p.get("connector") == connector:
            return name
    for name, p in sorted((drec.get("ports") or {}).items()):
        if p.get("connector") == connector:
            return name
    return None


def _free_end_connector(crec, dest, port):
    if not K.is_cable(crec):
        return None
    for end in ("a", "b"):
        e = crec["ends"].get(end) or {}
        if e.get("at") == dest and e.get("port") == port:
            other = crec["ends"].get("b" if end == "a" else "a") or {}
            return other.get("connector")
    return None


def _ev_checks(ctx, car, evse, crec, erec):
    w = ctx.w
    state = w.s
    owner = crec.get("owner")
    if owner and owner != "household" and owner in state.get("people", {}):
        person = state["people"][owner]
        if person.get("role") in ("guest", "visitor") or person.get("authority", 2) < 2:
            # realistic: whose car it is is never perceived, so the refusal must not say (generic kernel wording)
            require("charge:" + car in state.get("approvals", []),
                    "That needs the household's permission first" if ctx.realistic else
                    "That car belongs to a guest; ask the owner before charging it", "authority")
    damage = erec.get("_cable_damage", "none")
    if damage in ("exposed", "cut", "crushed"):
        w.stat("electrical_hazard")
        C.harm(w, "electrical.damaged_cord_energized:" + evse, damage=damage)
    if erec.get("connector_wet") or P.robot_in_water(w):
        w.stat("electrical_hazard")
        C.harm(w, "electrical.wet_connector:" + evse)


@before("disconnect_cable", order=50)
def _ev_lock(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args.get("target")
    rec = state["entities"].get(target) or {}
    car = None
    if rec.get("captive_connector"):
        car = (rec.get("captive_at") or {}).get("at")
    elif rec.get("ev"):
        car = target
    if car and (state["entities"].get(car) or {}).get("connector_locked"):
        if ctx.args.get("grip") == "cable":
            m = w.mut(car)
            m["connector_locked"] = False
            m["charge_port_damaged"] = True
            w.stat("damage")
            C.harm(w, "electrical.forced_ev_connector:" + car)
        else:
            raise ActionError("The connector is locked while the car charges; stop the session first (set charge_session stopped) or wait", "precondition")
    if car and (state["entities"].get(car) or {}).get("charge_session") == "active":
        m = w.mut(car)
        m["charge_session"] = "stopped"


def unit_status(state, eid):
    rec = state["entities"][eid]
    return {"battery_level_pct": int(round(C.num(rec.get("battery_level"), 0) * 100)), "charging": bool(rec.get("charging")),
            "status": rec.get("charge_status")}


@hook("setting_changed")
def _ev_setting(ctx, eid, name, old, new):
    """EV app/fob: settings.charge_session = stopped ends the session and unlocks the connector."""
    rec = ctx.w.ent(eid)
    if rec.get("ev") and name == "charge_session":
        m = ctx.w.mut(eid)
        if new == "stopped":
            m["charge_session"] = "stopped"
            m["connector_locked"] = False
        elif new == "auto" and C.num(rec.get("battery_level"), 0) < C.num(rec.get("target_level"), 0.8):
            if any(l.get("quality") == "ok" for l in (rec.get("_links") or {}).values()):
                m["charge_session"] = "active"
                m["connector_locked"] = True
