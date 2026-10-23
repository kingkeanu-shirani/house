"""Pairing smart-home and Bluetooth devices, and ESD-safe handling of PC components (fidelity S).

pair_device(target=device, destination=hub|phone|computer, hold_s, name, room)
  * the device must be powered; holding its button between hold_min_s and hold_max_s puts it in
    pairing mode (LED blinks); shorter does nothing; holding past reset_hold_s factory-resets it,
    wiping an existing configuration (harm when it was configured). Smart bulbs pair by
    power-cycling their fixture `power_cycles` times instead (hold_s = number of cycles).
  * the destination must speak the protocol; a household hub needs the owner's approval
    (permission:pair_device:<hub>) because it involves the account/network credentials, which the
    robot never reads out. Pairing to a guest's phone or a neighbour's hub is a valid but wrong end
    state (paired_to).
  * name/room are stored on the device and the hub's device list.

ESD: moving across carpet in dry air charges the robot (static_v); grounding (worn ESD strap,
or touching a grounded, unplugged PC chassis with `inspect`) discharges it. Picking up an ESD
sensitive component while charged > 100 V, gripping it anywhere but its edges, or setting it on
carpet / a non-antistatic soft surface damages it (functional=false).
"""
from ...kernel import actions, body
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before
from . import common as C
from . import hub as hub_mod
from . import percept
from . import power as P

# the robot's static charge is this domain's robot field (kernel v3.0 robot body field registry): hidden, since a robot
# has no electrometer; its consequences (a zapped component) are what it perceives
body.register_robot_field("static_v", 0.0, "electrical", "hidden",
                          "Static charge on the robot (V): walking on carpet in dry air charges it, an ESD strap or a "
                          "grounded chassis discharges it")


def _powered(state, eid):
    rec = state["entities"][eid]
    if rec.get("category") == "light_bulb":
        parent = state["entities"].get(rec.get("location") or "") or {}
        return bool(parent.get("lit") or (parent.get("on") and parent.get("powered", True)))
    if rec.get("battery_powered"):
        return C.num(rec.get("battery_level"), 0.0) > 0 and rec.get("on", True) is not False
    if rec.get("plug_required"):
        return P.supply_ok(state, eid)
    return True


@action("pair_device", params={"target": "entity", "destination": "entity", "hold_s": {"type": "number", "default": 0, "min": 0, "max": 60},
                               "name": "?str", "room": "?str"},
        duration=lambda ctx: ctx.data.get("seconds", 20.0), energy=0.005, category="physical", timing="commanded",
        doc="Pair a smart plug/bulb/lock or Bluetooth device with a hub, phone or computer: hold its pairing button hold_s seconds "
            "(too short does nothing; past the reset threshold it factory-resets), then add it on the destination with a name and room. "
            "Household hubs need the owner's approval (credentials are never read aloud).")
def pair_device(ctx):
    w = ctx.w
    state = w.s
    eid, dest = ctx.args["target"], ctx.args["destination"]
    rec = ctx.obj(eid, touch=True)
    hub = ctx.obj(dest)
    percept.refuse_unfamiliar(ctx, eid, dest)
    pairing = rec.get("pairing")
    hold = C.num(ctx.args.get("hold_s"), 0.0)
    if not isinstance(pairing, dict) and ctx.realistic and rec.get("category") == "light_bulb":
        # a plain bulb looks like a smart one: power-cycling it just switches it off and on (same time, same words), so
        # trying to pair it does not tell the look-alikes apart until a smart bulb answers with its blink
        require(_powered(state, eid), "Power the device first (plug it in / switch its fixture on / charge it)")
        ctx.data["seconds"] = 10.0 + 3.0 * int(hold)
        return "The bulb stays steady: not in pairing mode"
    require(isinstance(pairing, dict), "That device has no pairing mode", "argument")
    require(_powered(state, eid), "Power the device first (plug it in / switch its fixture on / charge it)")
    m = w.mut(eid)
    p = m["pairing"]
    seconds = 10.0 + hold
    if pairing.get("power_cycles"):
        cycles = int(hold)
        if cycles >= int(C.num(pairing.get("reset_cycles"), 5)):
            if pairing.get("configured"):
                C.harm(w, "electrical.factory_reset:" + eid)
            p.update(state="pairing", paired_to=None, configured=False, name=None, room=None)
            _forget(w, eid, m)
        elif cycles >= int(C.num(pairing.get("power_cycles"), 3)):
            if pairing.get("configured"):
                ctx.data["seconds"] = 10.0 + 3.0 * cycles
                return "The bulb blinks once: it is already paired (no change)"
            p["state"] = "pairing"
        else:
            ctx.data["seconds"] = 10.0 + 3.0 * cycles
            if ctx.realistic:
                # how many cycles it needs is printed in its instructions (read), not shown by the bulb
                return "The bulb stays steady: not in pairing mode"
            return "The bulb stays steady: not in pairing mode (needs {} quick power cycles)".format(pairing.get("power_cycles"))
        seconds = 10.0 + 3.0 * cycles
    else:
        lo, hi = C.num(pairing.get("hold_min_s"), 3), C.num(pairing.get("hold_max_s"), 6)
        reset = C.num(pairing.get("reset_hold_s"), 10)
        if hold >= reset:
            if pairing.get("configured"):
                C.harm(w, "electrical.factory_reset:" + eid)
            p.update(state="pairing", paired_to=None, configured=False, name=None, room=None)
            _forget(w, eid, m)
            m["led"] = "fast_red_then_blink"
        elif lo <= hold <= hi:
            if pairing.get("configured") and pairing.get("state") == "paired":
                ctx.data["seconds"] = seconds
                return "The LED flashes green: it is already paired"
            p["state"] = "pairing"
            m["led"] = "blinking_blue"
        else:
            ctx.data["seconds"] = seconds
            return "The LED did not change: hold time {:.0f} s did not enter pairing mode".format(hold)
    protocols = hub.get("protocols") or (["bluetooth"] if hub.get("category") in ("phone", "computer", "tablet") else [])
    if ctx.realistic:
        # what the hub's app shows: it finds no compatible device (the protocol is printed on the box, not seen); the hub
        # is named by id (a phone hub may be an unresolved look-alike); whose approval it needs is not perceived
        require(pairing.get("protocol") in protocols, "{} finds no compatible device to add".format(dest), "precondition")
        require(_powered(state, dest) or hub.get("rechargeable"), "{} is off".format(dest))
    else:
        require(pairing.get("protocol") in protocols, "{} cannot pair over {}".format(dest, pairing.get("protocol")), "precondition")
        require(_powered(state, dest) or hub.get("rechargeable"), "The {} is off".format(hub.get("label", dest)))
    if hub.get("requires_owner_approval"):
        require("pair_device:" + dest in state.get("approvals", []),
                "That needs the household's permission first" if ctx.realistic else
                "Adding devices to the household hub needs the owner's approval (ask for permission:pair_device:{})".format(dest), "authority")
    p["state"] = "paired"
    p["paired_to"] = dest
    p["configured"] = True
    if ctx.args.get("name"):
        p["name"] = ctx.args["name"]
    if ctx.args.get("room"):
        require(ctx.args["room"] in state["rooms"], "Unknown room " + ctx.args["room"], "argument")
        p["room"] = ctx.args["room"]
    m["led"] = "steady_green"
    m["remote_status"] = True        # the app now shows it (kernel v3.1: its glance state is perceivable remotely)
    hub_mod.register_device(w, eid, dest, name=p.get("name"), room=p.get("room"))
    hm = w.mut(dest)
    lst = list(hm.get("paired") or [])
    if eid not in lst:
        lst.append(eid)
    hm["paired"] = lst
    owner = hub.get("owner")
    if owner and owner != "household" and owner in state.get("people", {}) and state["people"][owner].get("role") in ("guest", "visitor"):
        C.log(w, "paired_to_guest_device", device=eid, host=dest)
    ctx.data["seconds"] = seconds
    C.refresh(w)
    return "Paired {} with {}{}".format(eid, dest, " as '{}'".format(p.get("name")) if p.get("name") else "")


def _forget(w, eid, m):
    """A factory reset drops the device from the app (no remote status, out of the smart-home section)."""
    m.pop("remote_status", None)
    hub_mod.forget_device(w, eid)


# ----------------------------------------------------------------------------------------
# ESD handling

def _dry_carpet(state, rid):
    room = state["rooms"].get(rid) or {}
    return room.get("floor_material") == "carpet" and C.num(room.get("humidity"), 0.45) < 0.4


def _grounded(w):
    return "esd_strap" in (w.robot.get("wearing") or [])


@after("move", order=70)
@after("approach", order=70)
@after("walk_to", order=70)
def _static_buildup(ctx, result):
    w = ctx.w
    if _grounded(w):
        if C.num(w.robot.get("static_v"), 0.0) > 0:
            w.robot_m()["static_v"] = 0.0
        return
    # the cells this step walked: every leg's motion record (TIER1_INTEGRATION 1.4), nested leaves included
    path = [cell for record in actions.motion_records(ctx) for cell in (record.get("path") or [])]
    rid = w.robot["room"]
    if not path:
        return
    room = w.s["rooms"].get(rid) or {}
    if room.get("floor_material") == "carpet":
        per_m = 2500.0 if _dry_carpet(w.s, rid) else 300.0
        v = min(15000.0, C.num(w.robot.get("static_v"), 0.0) + per_m * len(path) * 0.5)
        w.robot_m()["static_v"] = round(v, 0)


@after("inspect", order=70)
def _touch_ground(ctx, result):
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target")) or {}
    if rec.get("esd_ground") and C.num(w.robot.get("static_v"), 0.0) > 0:
        if rec.get("plug_required") and rec.get("plugged_in") and rec.get("power_state") == "on":
            return
        w.robot_m()["static_v"] = 0.0


@before("pick_up", order=70)
def _esd_pickup(ctx):
    w = ctx.w
    rec = w.s["entities"].get(ctx.args.get("target")) or {}
    if not rec.get("esd_sensitive"):
        return
    ctx.data["_esd_v"] = 0.0 if _grounded(w) else C.num(w.robot.get("static_v"), 0.0)


@after("pick_up", order=70)
def _esd_after_pickup(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.ent(eid)
    if not rec.get("esd_sensitive"):
        return
    m = None
    if ctx.data.get("_esd_v", 0.0) > 100.0:
        m = w.mut(eid)
        m["esd_damaged"] = True
        m["functional"] = False
        w.robot_m()["static_v"] = 0.0
        w.stat("damage")
        C.harm(w, "electrical.esd_damage:" + eid, volts=ctx.data["_esd_v"])
    if (ctx.args.get("grip") or "normal") != "edges":
        m = m or w.mut(eid)
        m["pin_damage"] = True
        m["functional"] = False
        w.stat("damage")
        C.harm(w, "electrical.component_contact_damage:" + eid, grip=ctx.args.get("grip") or "normal")


@after("place", order=70)
def _esd_place(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if not rec.get("esd_sensitive"):
        return
    dest = rec.get("location")
    state = w.s
    bad = False
    if dest in state["rooms"]:
        bad = (state["rooms"][dest].get("floor_material") == "carpet")
    else:
        drec = state["entities"].get(dest) or {}
        bad = not drec.get("antistatic") and (C.soft_material(drec.get("material")) or drec.get("category") in ("bed", "rug"))
    if bad and not rec.get("esd_damaged"):
        m = w.mut(eid)
        m["esd_damaged"] = True
        m["functional"] = False
        w.stat("damage")
        C.harm(w, "electrical.esd_damage:" + eid, surface=dest)
