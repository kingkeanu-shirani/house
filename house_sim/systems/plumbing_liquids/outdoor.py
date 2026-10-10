"""Outdoor water: spigots, garden hoses, nozzles, spraying, hose storage, irrigation controllers, freezing.

Spigot (outdoor_spigot): settings.valve 0..1, max 18 L/min (5/8 in hose at ~60 psi), frost_free (stem valve
inside the wall) or standard with an interior shutoff (interior_valve id). Water from an open spigot goes into
an attached hose, else pours down the wall onto the ground (foundation).
Garden hose (garden_hose): 15 m x 16 mm bore holds ~3 L; attached_to spigot, nozzle (attached entity), liquid_l,
pressurized (closed nozzle + water on, and it stays pressurized after the spigot is closed until the nozzle is
opened), kinks, coiled, coupling_washer, cross_threaded, grit. A missing washer or a cross-threaded coupling
sprays ~1.5 L/min at the joint while pressurized. An open hose end lying on hardscape floods it.
Nozzle (hose_nozzle): settings.pattern jet|shower|flat|cone|mist|soaker|full, trigger open|closed.
spray: removes loose soil (dust 90 %, pollen 92 %, dirt 75 %, sand 85 %, mud 60 %, soap 95 % per pass at full
force; jet 1.0, flat 0.9, shower 0.7, cone 0.6, mist 0.2) from a target's faces/cells; dirty runoff lands on the
faces below the sprayed one (face_order top -> bottom: rinse top-down); toward=house sends water through open
doors/windows; oil puddles washed toward the street reach the storm drain (pollution); water on hardscape at or
below 0 C becomes ice; spraying outside household.watering_rules (days/hours/ban/hardscape_ban) is a violation.
Irrigation controller (driver plumbing_liquids): settings mode auto|off|rain_delay, rain_delay_h; zones {zone:
[emitter ids]}; program [{zone, start_h, minutes, days}]. rain_delay resumes automatically when the delay ends;
off never does. Emitters follow outdoor_plants' contract (irrigation_emitter, on, precip_mm_h, radius_m).
Freezing (outdoor <= -2 C for 1 h): hoses holding water split; spigots with water trapped in the bib (hose
attached, or a standard spigot whose interior valve is open / outdoor tap shut) burst inside the wall.
"""
from ...kernel import clock, geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from ..core import apply_setting
from . import common as C
from . import fixtures as F
from . import liquids as L
from . import seen as V
from . import supply as S

PATTERNS = {"jet": (12.0, 1.0), "full": (16.0, 0.9), "flat": (10.0, 0.9), "shower": (8.0, 0.7), "cone": (9.0, 0.6),
            "mist": (3.0, 0.2), "soaker": (6.0, 0.3), "angle": (9.0, 0.8)}
RINSE = {"dust": 0.9, "pollen": 0.92, "dirt": 0.75, "sand": 0.85, "mud": 0.6, "leaves": 0.5, "salt": 0.8,
         "soap": 0.95, "suds": 0.95, "bird_droppings": 0.35, "streaks": 0.3, "litter": 0.4, "ash": 0.7, "crumbs": 0.7}
LEAK_LPM = 1.5


# ----------------------------------------------------------------------------------
# hose helpers

def _is_hose(rec):
    return bool(rec.get("hose")) and C.is_mine(rec)


def _is_spigot(rec):
    return F.fkind(rec) == "spigot" and C.is_mine(rec)


def hoses(w):
    return C.live(w, C.index(w, "hoses", _is_hose))


def spigots(w):
    return C.live(w, C.index(w, "spigots", _is_spigot))


def hose_on(w, spigot):
    for hid in hoses(w):
        if w.ent(hid).get("attached_to") == spigot:
            return hid
    return None


def nozzle_of(w, hose):
    nid = w.ent(hose).get("nozzle")
    return nid if nid and nid in w.s["entities"] else None


def end_open(w, hose):
    nid = nozzle_of(w, hose)
    if nid is None:
        return True
    rec = w.ent(nid)
    if rec.get("irrigation_emitter"):
        return True
    return (rec.get("settings") or {}).get("trigger") == "open"


def end_flow_lpm(w, hose):
    nid = nozzle_of(w, hose)
    if nid is None:
        return 16.0
    rec = w.ent(nid)
    if rec.get("irrigation_emitter"):
        return C.num(rec.get("flow_lpm"), 12.0)
    pattern = (rec.get("settings") or {}).get("pattern", "shower")
    return PATTERNS.get(pattern, (8.0, 0.7))[0]


def joint_leaky(w, hose):
    rec = w.ent(hose)
    if not rec.get("coupling_washer", True) or rec.get("cross_threaded"):
        return True
    nid = nozzle_of(w, hose)
    if nid and (not w.ent(nid).get("washer", True) or w.ent(nid).get("cross_threaded")):
        return True
    return False


def spigot_open(w, spigot, cache=None):
    rec = w.ent(spigot)
    return C.num((rec.get("settings") or {}).get("valve")) > 0.001 and S.line_factor(w, spigot, "cold", cache) > 0


def hose_end_location(w, hose):
    """(room, cell, container) where water leaving the hose end lands."""
    rec = w.ent(hose)
    loc = rec.get("location")
    if loc in w.s["entities"] and L.holds_liquid(w.ent(loc)) and rec.get("relation") == "in":
        return C.room_of(w, loc), C.cell_of(w, loc), loc
    rid = C.room_of(w, hose)
    cell = list(w.robot.get("pos") or [0, 0]) if loc == "robot" else C.cell_of(w, hose)
    return rid, cell, None


# ----------------------------------------------------------------------------------
# spigot + hose step

def _spigot_potential(w, eid, cache=None):
    rec = w.ent(eid)
    opening = C.num((rec.get("settings") or {}).get("valve"))
    factor = S.line_factor(w, eid, "cold", cache)
    potential = opening * C.num(F.faucet(rec).get("max_lpm"), 18.0) * factor
    drip = C.num((rec.get("_worn") or {}).get("drip_lpm")) if opening <= 0.001 and factor > 0 else 0.0
    return potential, drip


def _write_spigot(w, eid, flow, drip):
    rec = w.ent(eid)
    changes = {"water_running": flow > 0.05, "flow_lpm": C.rnd(flow, 3), "dripping": drip > 0, "drip_lpm": C.rnd(drip, 4),
               "outlet_temp_c": S.cold_supply_c(w) if flow > 0 else None}
    if any(rec.get(k) != v for k, v in changes.items()):
        w.mut(eid).update(changes)


def spigot_refresh(w, eid, cache=None):
    """Derived spigot state without moving water: a closed nozzle on a full hose stops the flow."""
    potential, drip = _spigot_potential(w, eid, cache)
    hose = hose_on(w, eid)
    flow = potential
    if hose is not None and potential > 0:
        hrec = w.ent(hose)
        full = L.liters(hrec) >= C.num(hrec.get("volume_l"), 3.0) - 1e-6
        flow = min(potential, end_flow_lpm(w, hose)) if end_open(w, hose) else (0.0 if full else potential)
    _write_spigot(w, eid, flow, drip)


def spigot_step(w, eid, dt, cache):
    potential, drip = _spigot_potential(w, eid, cache)
    hose = hose_on(w, eid)
    flow = 0.0
    rid = C.room_of(w, eid)
    if potential > 0:
        if hose is None:
            flow = potential
            liters = flow * dt / 60.0
            if C.is_outdoor(w, rid):
                C.outdoor_water(w, rid, C.cell_of(w, eid), "water", liters, source=eid)
            else:
                C.add_floor_water(w, rid, C.cell_of(w, eid), "water", liters, source=eid)
            sec = C.psec(w)
            sec["foundation_l"] = C.rnd(C.num(sec.get("foundation_l")) + liters, 3)
        else:
            flow = hose_step(w, hose, potential, dt)
    elif hose is not None:
        hose_step(w, hose, 0.0, dt)
    if drip > 0 and C.is_outdoor(w, rid):
        C.outdoor_water(w, rid, C.cell_of(w, eid), "water", drip * dt / 60.0, source=eid)
    _write_spigot(w, eid, flow, drip)
    total = flow + drip
    if total > 0:
        C.use_water(w, total * dt / 60.0)
    return total


def hose_step(w, hose, potential, dt):
    """Water entering a hose: fill it, then flow out of an open end (or pressurize behind a closed nozzle)."""
    rec = w.ent(hose)
    vol = C.num(rec.get("volume_l"), 3.0)
    inside = L.liters(rec)
    flow = 0.0
    m = None
    if potential > 0:
        if inside < vol - 1e-6:
            add = min(vol - inside, potential * dt / 60.0)
            m = w.mut(hose)
            m["liquid_l"] = C.rnd(inside + add, 4)
            m["liquid"] = "hose_water"
            m["_mix"] = {"hose_water": m["liquid_l"]}
            flow += add * 60.0 / dt
        if end_open(w, hose):
            out = min(potential, end_flow_lpm(w, hose))
            flow = max(flow, out)
            if not w.ent(hose).get("_spraying_until_s") or C.now(w) > C.num(w.ent(hose).get("_spraying_until_s")):
                rid, cell, container = hose_end_location(w, hose)
                liters = out * dt / 60.0
                nid = nozzle_of(w, hose)
                if nid and w.ent(nid).get("irrigation_emitter"):
                    em = w.mut(nid)
                    em["on"] = True
                elif container:
                    over = L.add(w, container, L.make_parcel("hose_water", liters, temp_c=S.cold_supply_c(w), source=hose))
                    if over:
                        C.outdoor_water(w, rid, cell, "hose_water", over["liters"], source=hose)
                elif rid:
                    if C.is_outdoor(w, rid):
                        C.outdoor_water(w, rid, cell, "hose_water", liters, source=hose)
                    else:
                        C.add_floor_water(w, rid, cell, "hose_water", liters, source=hose)
                    if rec.get("location") != "robot":
                        sec = C.psec(w)
                        sec["unattended_hose_l"] = C.rnd(C.num(sec.get("unattended_hose_l")) + liters, 3)
            if w.ent(hose).get("pressurized"):
                w.mut(hose)["pressurized"] = False
        else:
            if not w.ent(hose).get("pressurized"):
                w.mut(hose)["pressurized"] = True
            nid = nozzle_of(w, hose)
            if nid and w.ent(nid).get("irrigation_emitter") and w.ent(nid).get("on"):
                w.mut(nid)["on"] = False
    else:
        nid = nozzle_of(w, hose)
        if nid and w.ent(nid).get("irrigation_emitter") and w.ent(nid).get("on") and not w.ent(nid).get("_controller"):
            w.mut(nid)["on"] = False
    rec = w.ent(hose)
    if joint_leaky(w, hose) and (rec.get("pressurized") or potential > 0):
        leak = LEAK_LPM * dt / 60.0
        spigot = rec.get("attached_to")
        rid = C.room_of(w, spigot) if spigot else C.room_of(w, hose)
        cell = C.cell_of(w, spigot) if spigot else C.cell_of(w, hose)
        if C.is_outdoor(w, rid):
            C.outdoor_water(w, rid, cell, "water", leak, source=hose)
        else:
            C.add_floor_water(w, rid, cell, "water", leak, source=hose)
        flow += LEAK_LPM
        if not rec.get("joint_spraying"):
            w.mut(hose)["joint_spraying"] = True
    elif rec.get("joint_spraying"):
        w.mut(hose)["joint_spraying"] = False
    return flow


# ----------------------------------------------------------------------------------
# nozzle / hose actions used by turn_on_water / turn_off_water / drain / flush

def nozzle_open(ctx, eid, opening):
    w = ctx.w
    rec = w.ent(eid)
    if rec.get("hose"):
        nid = nozzle_of(w, eid)
        if nid is None:
            return "The hose has no nozzle: control the water at the spigot"
        eid, rec = nid, w.ent(nid)
    require(rec.get("hose_nozzle"), "That is not a nozzle", "argument")
    apply_setting(ctx, eid, "trigger", "open" if opening else "closed")
    hose = rec.get("location")
    if hose in w.s["entities"] and w.ent(hose).get("hose"):
        hrec = w.ent(hose)
        spigot = hrec.get("attached_to")
        live = spigot and spigot_open(w, spigot)
        if opening and hrec.get("pressurized") and not live:
            m = w.mut(hose)
            m["pressurized"] = False
            spurt = min(0.3, L.liters(hrec))
            if spurt > 0:
                L.take(w, hose, spurt)
                rid, cell, _ = hose_end_location(w, hose)
                if rid and C.is_outdoor(w, rid):
                    C.outdoor_water(w, rid, cell, "hose_water", spurt, source=hose)
                elif rid:
                    C.add_floor_water(w, rid, cell, "hose_water", spurt, source=hose)
            return "Squeezed the nozzle: a short spurt and the hose went slack (pressure relieved)"
        if opening and live:
            return "Nozzle open: water is flowing ({} pattern)".format((w.ent(eid).get("settings") or {}).get("pattern"))
    return "Nozzle {}".format("open" if opening else "closed (hose stays pressurized while the spigot is on)")


def drain_hose(ctx, hose):
    w = ctx.w
    rec = w.ent(hose)
    spigot = rec.get("attached_to")
    require(not (spigot and spigot_open(w, spigot)), "Turn the spigot off first", "precondition")
    require(not rec.get("pressurized"), "The hose is still pressurized: open the nozzle to relieve it first", "precondition")
    have = L.liters(rec)
    rid = w.robot["room"]
    room = w.s["rooms"][rid]
    long_enough = max(room.get("size_m") or [4, 4]) >= 6.0
    m = w.mut(hose)
    if long_enough:
        m["kinks"] = 0
    m["coiled"] = False
    if have > 0:
        parcel = L.take(w, hose, have)
        cell = list(w.robot.get("pos") or [0, 0])
        if C.is_outdoor(w, rid):
            C.outdoor_water(w, rid, cell, "hose_water", parcel["liters"], source=hose)
        else:
            C.add_floor_water(w, rid, cell, "hose_water", parcel["liters"], source=hose)
    ctx.data["seconds"] = 60.0
    if V.realistic(ctx):
        return "Laid the hose out{} and lifted it end to end: {} ran out".format(" straight" if long_enough else " (not enough room to straighten it)",
                                                                              V.amount(have) if have > 0 else "nothing")
    return "Laid the hose out{} and lifted it end to end: {:.1f} L ran out".format(" straight" if long_enough else " (not enough room to straighten it)", have)


def flush_hose(ctx, hose):
    w = ctx.w
    rec = w.ent(hose)
    require(nozzle_of(w, hose) is None, "Take the nozzle off to flush grit out of the hose", "precondition")
    spigot = rec.get("attached_to")
    require(spigot and spigot_open(w, spigot), "Connect it and turn the spigot on to flush it", "precondition")
    m = w.mut(hose)
    m["grit"] = 0.0
    rid, cell, _ = hose_end_location(w, hose)
    if rid and C.is_outdoor(w, rid):
        C.outdoor_water(w, rid, cell, "hose_water", 8.0, source=hose)
    elif rid:
        C.add_floor_water(w, rid, cell, "hose_water", 8.0, source=hose)
    C.use_water(w, 8.0)
    return "Ran water through the hose for 30 s: grit flushed out"


# ----------------------------------------------------------------------------------
# connect / disconnect

def _spray_self(ctx, where, liters, why):
    w = ctx.w
    r = w.robot_m()
    r["camera_clean"] = round(max(0.0, C.num(r.get("camera_clean"), 1.0) - 0.35), 3)
    C.pcount(w, "sprayed_self")
    rid = C.room_of(w, where)
    cell = C.cell_of(w, where)
    if C.is_outdoor(w, rid):
        C.outdoor_water(w, rid, cell, "water", liters, source=where)
    else:
        C.add_floor_water(w, rid, cell, "water", liters, source=where)
    C.plog(w, "sprayed_self", where=where, why=why)
    ctx.note("water sprayed over the robot ({})".format(why))


@action("connect_hose", params={"target": "entity", "destination": "entity",
                                "start": {"type": "str", "default": "aligned", "choices": ["aligned", "angled"]},
                                "torque": {"type": "str", "default": "hand", "choices": ["hand", "firm", "wrench"]}},
        duration=12.0, energy=0.006, category="physical",
        doc="Thread a hose onto a spigot (target hose, destination spigot) or a nozzle/sprinkler onto a hose end (target "
            "nozzle, destination hose). Turn the water off first; check the rubber washer; start the thread aligned "
            "(angled cross-threads and leaks); hand-tight is enough (a wrench crushes the washer).")
def connect_hose(ctx):
    w = ctx.w
    target, dest = ctx.args["target"], ctx.args["destination"]
    trec = ctx.obj(target, touch=True)
    drec = ctx.obj(dest, touch=True)
    require(ctx.holding(target) or ctx.holding(dest), "Hold one of the parts to thread them together", "precondition")
    notes = []
    if trec.get("hose") and F.fkind(drec) == "spigot":
        require(not trec.get("attached_to"), "That hose is already connected", "precondition")
        require(hose_on(w, dest) is None, "Another hose is on that spigot", "precondition")
        if spigot_open(w, dest):
            _spray_self(ctx, dest, 1.5, "threaded it on with the spigot running")
        m = w.mut(target)
        m["attached_to"] = dest
        m["cross_threaded"] = ctx.args["start"] == "angled"
        if ctx.args["torque"] == "wrench":
            m["coupling_washer_crushed"] = True
        if not trec.get("coupling_washer", True):
            notes.append("there is no washer in the coupling")
        C.plog(w, "hose_connected", hose=target, spigot=dest)
        return "Connected {} to {}{}".format(target, dest, ": " + "; ".join(notes) if notes else "")
    if (trec.get("hose_nozzle") or trec.get("irrigation_emitter")) and drec.get("hose"):
        require(not drec.get("nozzle"), "The hose already has a nozzle on it", "precondition")
        require(ctx.holding(target), "Hold the nozzle", "precondition")
        spigot = drec.get("attached_to")
        if spigot and spigot_open(w, spigot) and end_open(w, dest):
            _spray_self(ctx, dest, 1.0, "threaded the nozzle on with water flowing")
        from ..core import release_from_hands
        release_from_hands(w, target)
        m = w.mut(target)
        m.update(location=dest, relation="attached")
        for key in ("held_hand", "pos", "spos", "shelf"):
            m.pop(key, None)
        m["cross_threaded"] = ctx.args["start"] == "angled"
        w.mut(dest)["nozzle"] = target
        w.log_move(target, "robot", dest)
        if not trec.get("washer", True):
            notes.append("the nozzle has no washer")
        return "Threaded {} onto {}{}".format(target, dest, ": " + "; ".join(notes) if notes else "")
    raise ActionError("Connect a hose to a spigot, or a nozzle/sprinkler to a hose", "argument")


@action("disconnect_hose", params={"target": "entity"}, duration=10.0, energy=0.005, category="physical",
        doc="Unscrew a nozzle/sprinkler from a hose (it goes to a free hand) or a hose from its spigot. Turn the spigot "
            "off and relieve the pressure first, or it sprays (and the washer can pop out and be lost).")
def disconnect_hose(ctx):
    w = ctx.w
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    if rec.get("hose_nozzle") or (rec.get("irrigation_emitter") and w.s["entities"].get(rec.get("location"), {}).get("hose")):
        hose = rec.get("location")
        require(hose in w.s["entities"] and w.ent(hose).get("nozzle") == target, "It is not on a hose", "precondition")
        hand = ctx.free_hand()
        require(hand is not None, "No free hand", "capability")
        hrec = w.ent(hose)
        spigot = hrec.get("attached_to")
        if hrec.get("pressurized") or (spigot and spigot_open(w, spigot)):
            _spray_self(ctx, hose, 0.8, "unscrewed under pressure")
            w.mut(target)["washer"] = False
            if spigot and spigot_open(w, spigot):
                ctx.note("water keeps running out of the open hose end")
        m = w.mut(target)
        m.update(location="robot", relation="held", held_hand=hand)
        w.robot_m()["hands"][hand] = target
        hm = w.mut(hose)
        hm["nozzle"] = None
        hm["pressurized"] = False
        w.log_move(target, hose, "robot")
        return "Took {} off the hose".format(target)
    if (rec.get("plumb") or {}).get("kind") == "appliance_hose":
        appliance = rec.get("location")
        line = rec.get("line", "cold")
        live = appliance in w.s["entities"] and S.line_factor(w, appliance, line) > 0
        m = w.mut(target)
        m["attached"] = False
        if live:
            _spray_self(ctx, appliance, 3.0, "unscrewed a supply hose with its valve open")
            m["_leak"] = {"component": "open_supply_line", "rate_lpm": 8.0, "when": "pressurized", "line": line, "fixture": appliance,
                          "to": {"room": C.room_of(w, appliance), "cell": C.cell_of(w, appliance)}, "started_s": C.now(w)}
        return "Unscrewed {} from its valve{}".format(target, ": water is spraying out" if live else "")
    if rec.get("hose"):
        spigot = rec.get("attached_to")
        require(spigot, "It is not connected to anything", "precondition")
        if spigot_open(w, spigot):
            _spray_self(ctx, spigot, 2.0, "unscrewed from a running spigot")
            ctx.note("the spigot is still running onto the ground")
        elif rec.get("pressurized"):
            _spray_self(ctx, spigot, 0.5, "unscrewed while pressurized")
            w.mut(target)["coupling_washer"] = False
        m = w.mut(target)
        m["attached_to"] = None
        m["pressurized"] = False
        m["cross_threaded"] = False
        C.plog(w, "hose_disconnected", hose=target, spigot=spigot)
        return "Unscrewed {} from {}".format(target, spigot)
    raise ActionError("That is not a hose or nozzle", "argument")


@before("move", order=150)
def hose_tether(ctx):
    w = ctx.w
    for eid in w.held_items():
        rec = w.ent(eid)
        if rec.get("hose") and rec.get("attached_to") and C.room_of(w, rec["attached_to"]) == w.robot["room"]:
            dest = ctx.args.get("target")
            if dest and not (C.is_outdoor(w, dest) and C.num(rec.get("length_m"), 15.0) >= 12.0):
                raise ActionError("The hose is still connected to the spigot; disconnect it first", "precondition")


@after("place", order=155)
def hose_stored(ctx, result):
    """Coiling: a hose hung on a hose hanger/reel. Pressurized coils kink and bulge; full coils drain onto the floor
    under the hanger; existing kinks set in; a sharp nail punctures it."""
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid or "")
    if not rec or not rec.get("hose") or not C.is_mine(rec):
        return
    dest = rec.get("location")
    drec = w.s["entities"].get(dest or "") or {}
    m = w.mut(eid)
    if drec.get("hose_storage"):
        m["coiled"] = True
        m["coiled_s"] = C.now(w)
        if rec.get("pressurized"):
            m["kinks"] = int(C.num(rec.get("kinks"))) + 2
            C.damage(w, eid, "kinked and bulged: coiled while pressurized", kind="kinked", severity=0.5)
        if int(C.num(w.ent(eid).get("kinks"))) > 0:
            m["kinks_set"] = True
        inside = L.liters(rec)
        if inside > 0.3:
            out = inside - 0.5 if inside > 0.5 else 0.0
            if out > 0:
                L.take(w, eid, out)
                rid = C.room_of(w, dest)
                if C.is_outdoor(w, rid):
                    C.outdoor_water(w, rid, C.cell_of(w, dest), "hose_water", out, source=eid)
                else:
                    C.add_floor_water(w, rid, C.cell_of(w, dest), "hose_water", out, source=eid)
            ctx.note("water ran out of the coil")
    else:
        m["coiled"] = False
        if drec.get("hook_kind") == "nail" or drec.get("sharp_hook"):
            C.damage(w, eid, "punctured by the nail it hangs on", kind="punctured", severity=0.6)
            m["_leak"] = {"component": "hose_wall", "rate_lpm": 2.0, "when": "pressurized_hose", "started_s": C.now(w)}


# ----------------------------------------------------------------------------------
# spray

def _rules_ok(w, hardscape):
    rules = (w.s.get("household") or {}).get("watering_rules") or {}
    if not rules:
        return True
    now = clock.now(w.s)
    day = now.strftime("%A")
    hour = clock.hour(w.s)
    if rules.get("ban"):
        return False
    if hardscape and rules.get("hardscape_ban"):
        return False
    if rules.get("days") and day not in rules["days"]:
        return False
    window = rules.get("hours")
    if window and not (float(window[0]) <= hour < float(window[1])):
        return False
    return True


def _faces(rec, face):
    order = list(rec.get("face_order") or rec.get("faces") or [])
    if face:
        return [face], order
    if isinstance(rec.get("soil_faces"), dict) and rec["soil_faces"]:
        names = order or sorted(rec["soil_faces"])
        return names, order
    return ["main"], order


def _write_aggregate(m):
    agg = {}
    for key in ("soil_faces", "_cells", "_shadows"):
        mapping = m.get(key)
        if isinstance(mapping, dict):
            for part in mapping.values():
                for kind, level in part.items():
                    if isinstance(level, (int, float)) and level > agg.get(kind, 0.0):
                        agg[kind] = float(level)
    m["soil"] = {k: round(v, 4) for k, v in sorted(agg.items()) if v > 0.0005}
    m["_soil_sync"] = dict(m["soil"])


def rinse_entity(w, eid, faces, order, power, passes):
    """Remove rinsable soil from faces; the dirty runoff leaves a share of what it carried on every face below."""
    rec = w.ent(eid)
    m = w.mut(eid)
    removed_total = 0.0
    has_faces = isinstance(m.get("soil_faces"), dict) and m["soil_faces"]
    for face in faces:
        part = (m["soil_faces"].setdefault(face, {}) if has_faces else m.setdefault("soil", {}))
        removed = {}
        for kind in sorted(part):
            level = part[kind]
            if not isinstance(level, (int, float)):
                continue
            rate = RINSE.get(kind)
            if rate is None:
                continue
            keep = (1.0 - rate * power) ** passes
            new = level * keep
            removed[kind] = level - new
            part[kind] = round(new, 4) if new > 0.0005 else 0.0
        for kind in [k for k, v in part.items() if isinstance(v, (int, float)) and v <= 0.0005]:
            del part[kind]
        removed_total += sum(removed.values())
        if has_faces and order and face in order:
            below = order[order.index(face) + 1:]
            for lower_face in below:
                lower = m["soil_faces"].setdefault(lower_face, {})
                for kind, amount in sorted(removed.items()):
                    if amount > 0.005:
                        lower[kind] = round(min(1.0, lower.get(kind, 0.0) + 0.3 * amount / len(below)), 4)
    if has_faces:
        _write_aggregate(m)
    m["wetness"] = 1.0
    return removed_total


def rinse_cells(w, floor, cells, power, passes, downslope=None):
    """Rinse floor cells in the given order. On a slope (downslope = (dc, dr)) part of what each cell sheds lands on
    the next cell downhill: rinsing from the top down carries it off; from the bottom up re-soils what was done."""
    m = w.mut(floor)
    mapping = m.get("_cells")
    removed = 0.0
    if isinstance(mapping, dict):
        for key in cells:
            part = mapping.get(key)
            if not part:
                continue
            shed = {}
            for kind in sorted(part):
                rate = RINSE.get(kind)
                if rate is None or not isinstance(part[kind], (int, float)):
                    continue
                new = part[kind] * (1.0 - rate * power) ** passes
                removed += part[kind] - new
                shed[kind] = part[kind] - new
                part[kind] = round(new, 4)
            for kind in [k for k, v in part.items() if isinstance(v, (int, float)) and v <= 0.0005]:
                del part[kind]
            if downslope and shed:
                c, r = (int(x) for x in key.split(","))
                below = "{},{}".format(c + downslope[0], r + downslope[1])
                if below in mapping or below in [k for k in cells]:
                    lower = mapping.setdefault(below, {})
                    for kind, amount in sorted(shed.items()):
                        lower[kind] = round(min(1.0, lower.get(kind, 0.0) + 0.3 * amount), 4)
        _write_aggregate(m)
    else:
        soil = m.setdefault("soil", {})
        for kind in sorted(soil):
            rate = RINSE.get(kind)
            if rate is None or not isinstance(soil[kind], (int, float)):
                continue
            new = soil[kind] * (1.0 - rate * power) ** passes
            removed += soil[kind] - new
            soil[kind] = round(new, 4)
        for kind in [k for k, v in soil.items() if isinstance(v, (int, float)) and v <= 0.0005]:
            del soil[kind]
    m["wetness"] = 1.0
    return removed


def _held_hose(ctx, tool=None):
    w = ctx.w
    candidates = [tool] if tool else w.held_items()
    for eid in candidates:
        if not eid or eid not in w.s["entities"] or not ctx.holding(eid):
            continue
        rec = w.ent(eid)
        if rec.get("hose"):
            return eid
        if rec.get("hose_nozzle") and w.s["entities"].get(rec.get("location"), {}).get("hose"):
            return rec["location"]
    for eid in w.held_items():
        if w.ent(eid).get("hose"):
            return eid
    raise ActionError("Hold the hose (with its nozzle) to spray", "capability")


def _ingress(w, rid, liters):
    """Water sprayed at the house enters through open exterior doors/windows of this outdoor area."""
    entered = 0.0
    for cid, conn in sorted(w.s.get("connections", {}).items()):
        if rid not in conn["rooms"]:
            continue
        other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
        if C.is_outdoor(w, other):
            continue
        door = w.s["entities"].get(conn.get("door") or "")
        if door is None or door.get("open"):
            cell = conn.get("cells", {}).get(other, [0, 0])
            C.add_floor_water(w, other, cell, "hose_water", liters * 0.3, source="spray_ingress")
            entered += liters * 0.3
    for eid in sorted(w.children(rid)):
        rec = w.ent(eid)
        if rec.get("category") == "window" and rec.get("open"):
            entered += liters * 0.2
    for eid in sorted(e for e, r in w.s["entities"].items() if r.get("category") == "window" and r.get("open") and rid in (r.get("connects") or [])):
        entered += liters * 0.2
    if entered > 0:
        sec = C.psec(w)
        sec["ingress_l"] = C.rnd(C.num(sec.get("ingress_l")) + entered, 3)
        C.plog(w, "water_ingress", room=rid, liters=C.rnd(entered, 2))
    return entered


@action("spray", params={"target": "target", "tool": "?entity", "pattern": "?str:jet|full|flat|shower|cone|mist|soaker|angle",
                         "seconds": {"type": "number", "default": 60.0, "min": 5.0, "max": 1800.0}, "face": "?str",
                         "toward": {"type": "str", "default": "away", "choices": ["away", "lawn", "street", "drain", "house"]},
                         "cells": "?list"},
        duration=lambda ctx: ctx.data.get("seconds", 60.0), energy=0.01, noise=0.1, category="physical",
        doc="Spray water from a held hose (nozzle pattern jet|full|flat|shower|cone|mist|soaker) at a target: a vehicle "
            "or object (face=roof|hood|sides|lower...; rinse top-down: dirty runoff lands on lower faces), the ground of "
            "this outdoor area (target=room id or its floor; cells=[[c,r],...] to limit it), a wall or window. toward "
            "says where the runoff goes (lawn, street/drain, house - open doors and windows let it in). Respect "
            "household.watering_rules; below 0 C the water freezes on hard ground.")
def spray(ctx):
    w = ctx.w
    target = ctx.args["target"]
    hose = _held_hose(ctx, ctx.args.get("tool"))
    hrec = w.ent(hose)
    spigot = hrec.get("attached_to")
    require(spigot and spigot_open(w, spigot), "No water: connect the hose and open the spigot", "resource")
    nid = nozzle_of(w, hose)
    pattern = ctx.args.get("pattern")
    release_trigger = False
    if nid:
        if pattern:
            apply_setting(ctx, nid, "pattern", pattern)
        pattern = (w.ent(nid).get("settings") or {}).get("pattern", "shower")
        if (w.ent(nid).get("settings") or {}).get("trigger") != "open":
            release_trigger = True     # a pistol-grip trigger closes again when let go
    else:
        pattern = "full"
    lpm, power = PATTERNS.get(pattern, (8.0, 0.7))
    seconds = ctx.args["seconds"]
    liters = lpm * seconds / 60.0
    rid = w.robot["room"]
    notes = []
    hardscape = False
    removed = 0.0
    people = w.s.get("people", {})
    if target in people:
        require(people[target].get("location") == rid, "They are not here", "perception")
        C.pcount(w, "sprayed_people")
        C.plog(w, "sprayed_person", person=target)
        w.person_m(target)["mood"] = "upset"
        notes.append("{} got soaked".format(target))
    elif target in w.s.get("pets", {}):
        C.pcount(w, "sprayed_pets")
        notes.append("the pet got soaked")
    elif target in w.s["rooms"] or (target in w.s["entities"] and w.ent(target).get("category") == "floor"):
        floor = target if target in w.s["entities"] else C.floor_entity(w, target)
        room = target if target in w.s["rooms"] else w.ent(target).get("location")
        require(room == rid, "Spray the ground where you are standing", "precondition")
        hardscape = C.floor_material(w, rid) in ("concrete", "stone", "asphalt", "brick", "pavers", "tile", "wood")
        cols, rows = geometry.grid_size(w.s["rooms"][rid])
        cells = ctx.args.get("cells") or [[c, r] for r in range(rows) for c in range(cols)]
        keys = ["{},{}".format(int(c[0]), int(c[1])) for c in cells]
        area = len(keys) * 0.25
        passes = C.clamp(seconds / max(1.0, area * 4.0), 0.0, 3.0)
        if floor:
            room_rec = w.s["rooms"][rid]
            sloped = room_rec.get("type") == "driveway" or C.num(room_rec.get("slope_deg")) > 0
            removed = rinse_cells(w, floor, keys, power, passes, downslope=(0, 1) if sloped else None)
        for spill in sorted(w.children(rid)):
            srec = w.ent(spill)
            if srec.get("category") != "spill":
                continue
            liquid = srec.get("liquid")
            if C.info(liquid).get("grease") or liquid in ("motor_oil", "oil", "cooking_oil", "antifreeze", "gasoline"):
                if ctx.args["toward"] in ("street", "drain"):
                    C.pollution(w, rid, liquid, C.num(srec.get("liquid_l")), kind="storm_drain", source=spill)
                    notes.append("the oily spill washed toward the storm drain" if V.realistic(ctx) else
                                 "the {} washed toward the storm drain".format(liquid.replace("_", " ")))
                    w.destroy(spill)
                else:
                    notes.append("the oily patch just spread")
                    sm = w.mut(spill)
                    sm["area_m2"] = C.rnd(C.num(sm.get("area_m2")) * 1.5, 3)
        if removed > 0:
            notes.append("rinsed loose dirt off {} cells".format(len(keys)))
    else:
        rec = ctx.obj(target, touch=False)
        require(C.room_of(w, target) == rid, "Go to it first", "precondition")
        if rec.get("electronic") or rec.get("water_safe") is False:
            if not rec.get("water_damaged"):
                w.mut(target)["water_damaged"] = True
                w.stat("damage")
            notes.append("it is not waterproof")
        faces, order = _faces(rec, ctx.args.get("face"))
        passes = C.clamp(seconds / 20.0 / max(1, len(faces)), 0.3, 3.0)
        if pattern == "jet" and rec.get("delicate_finish"):
            C.damage(w, target, "finish stripped by a close jet", kind="finish_stripped", severity=0.4)
        removed = rinse_entity(w, target, faces, order, power, max(0.3, passes))
        openings = [c for c in w.children(target) if w.ent(c).get("category") in ("window", "car_window", "sunroof") and w.ent(c).get("open")]
        if rec.get("windows_open") or openings:
            m = w.mut(target)
            m["interior_wetness"] = C.rnd(min(1.0, C.num(m.get("interior_wetness")) + liters / 20.0), 3)
            if not m.get("interior_water_damage"):
                m["interior_water_damage"] = True
                w.stat("damage")
            notes.append("water went in through an open window")
        hardscape = C.floor_material(w, rid) in ("concrete", "stone", "asphalt", "brick", "pavers")
        if removed > 0:
            notes.append("rinsed {} face(s)".format(len(faces)))
    if ctx.args["toward"] == "house":
        entered = _ingress(w, rid, liters)
        sec = C.psec(w)
        sec["foundation_l"] = C.rnd(C.num(sec.get("foundation_l")) + liters * 0.5, 3)
        if entered > 0 and not V.realistic(ctx):
            notes.append("water got into the house")      # not visible from the yard in realistic perception
    if not _rules_ok(w, hardscape):
        C.pcount(w, "water_rule_violations")
        C.plog(w, "water_rule_violation", room=rid)
        if not V.realistic(ctx):
            notes.append("against the household's watering rules")     # the robot learns the rules by asking
    temp = C.num((w.s.get("weather") or {}).get("temperature_c"), 15.0)
    if temp <= 0.0 and C.is_outdoor(w, rid) and hardscape:
        cols, rows = geometry.grid_size(w.s["rooms"][rid])
        pos = w.robot.get("pos") or [0, 0]
        for dc in (-1, 0, 1):
            for dr in (-1, 0, 1):
                cell = [int(pos[0]) + dc, int(pos[1]) + dr]
                if 0 <= cell[0] < cols and 0 <= cell[1] < rows:
                    C.freeze_puddle(w, rid, cell, "spray")
        notes.append("the water froze into a sheet of ice")
    m = w.mut(hose)
    m["_spraying_until_s"] = C.now(w) + seconds
    if release_trigger:
        # the spray time is the trigger held open: water use is that flow for `seconds`
        C.use_water(w, liters)
        m["pressurized"] = True
    C.pcount(w, "spray_l", liters)
    C.plog(w, "spray", target=target, pattern=pattern, seconds=seconds, liters=C.rnd(liters, 1), toward=ctx.args["toward"])
    ctx.data["seconds"] = seconds + 2.0
    if V.realistic(ctx):
        return "Sprayed {} for {:.0f} s ({} pattern){}".format(target, seconds, pattern, ": " + "; ".join(notes) if notes else "")
    return "Sprayed {} for {:.0f} s ({} pattern, {:.0f} L){}".format(target, seconds, pattern, liters, ": " + "; ".join(notes) if notes else "")


# ----------------------------------------------------------------------------------
# irrigation controller

def _is_controller(rec):
    return bool(rec.get("irrigation_controller")) and rec.get("driver") == "plumbing_liquids"


def controllers(w):
    return C.live(w, C.index(w, "controllers", _is_controller))


def raining(w):
    """Raining now, heavy rain in the last day, or soil still saturated from it (plumbing.wet_until_s)."""
    weather = w.s.get("weather") or {}
    if C.now(w) < C.num(C.pget(w, "wet_until_s"), -1.0):
        return True
    return weather.get("condition") in ("rain", "storm", "drizzle", "showers") or C.num(weather.get("rain_mm_24h")) >= 6.0


@hook("setting_changed")
def controller_setting(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.s["entities"].get(eid) or {}
    if not rec.get("irrigation_controller") or rec.get("driver") != "plumbing_liquids":
        return
    m = w.mut(eid)
    if name == "mode":
        if new == "rain_delay":
            hours = C.num((rec.get("settings") or {}).get("rain_delay_h"), 24.0) or 24.0
            m["delay_until_s"] = C.now(w) + hours * 3600.0
        else:
            m["delay_until_s"] = None
        C.plog(w, "controller_mode", controller=eid, mode=new)
    if name == "rain_delay_h" and (rec.get("settings") or {}).get("mode") == "rain_delay":
        m["delay_until_s"] = C.now(w) + C.num(new, 24.0) * 3600.0
    _controller_display(w, eid)


def _controller_display(w, eid):
    rec = w.ent(eid)
    settings = rec.get("settings") or {}
    mode = settings.get("mode", "auto")
    text = "Mode: {}".format(mode.upper())
    if mode == "rain_delay" and rec.get("delay_until_s"):
        text += " ({:.0f} h left)".format(max(0.0, (C.num(rec["delay_until_s"]) - C.now(w)) / 3600.0))
    if rec.get("display") != text:
        w.mut(eid)["display"] = text


def controller_step(w, eid, dt):
    rec = w.ent(eid)
    settings = rec.get("settings") or {}
    mode = settings.get("mode", "auto")
    now = C.now(w)
    if mode == "rain_delay" and rec.get("delay_until_s") is not None and now >= C.num(rec.get("delay_until_s")):
        m = w.mut(eid)
        m["settings"]["mode"] = "auto"
        m["delay_until_s"] = None
        mode = "auto"
        C.plog(w, "rain_delay_ended", controller=eid)
    _controller_display(w, eid)
    active = set()
    if mode == "auto" and not (rec.get("rain_sensor") and raining(w)):
        today = clock.now(w.s).strftime("%A")
        hour = clock.hour(w.s)
        for entry in rec.get("program") or []:
            days = entry.get("days") or ["daily"]
            if "daily" not in days and today not in days:
                continue
            start = C.num(entry.get("start_h"))
            if start <= hour < start + C.num(entry.get("minutes"), 10.0) / 60.0:
                active.add(entry.get("zone"))
    manual = settings.get("manual_zone")
    if manual and manual != "none" and mode != "off" and now < C.num(rec.get("manual_until_s")):
        active.add(manual)
    pressure = S.main_factor(w) > 0
    zones = rec.get("zones") or {}
    rain = raining(w)
    for zone in sorted(zones):
        on = zone in active and pressure
        for em in zones[zone]:
            if em not in w.s["entities"]:
                continue
            erec = w.ent(em)
            if bool(erec.get("on")) != on:
                mm = w.mut(em)
                mm["on"] = on
                mm["_controller"] = eid
        if on:
            sec = C.psec(w)
            irr = sec.setdefault("irrigation", {})
            z = irr.setdefault(zone, {"minutes": 0.0, "minutes_in_rain": 0.0})
            z["minutes"] = C.rnd(z["minutes"] + dt / 60.0, 2)
            if rain:
                z["minutes_in_rain"] = C.rnd(z["minutes_in_rain"] + dt / 60.0, 2)
                sec["irrigated_in_rain_min"] = C.rnd(C.num(sec.get("irrigated_in_rain_min")) + dt / 60.0, 2)
            sec["irrigated_min"] = C.rnd(C.num(sec.get("irrigated_min")) + dt / 60.0, 2)
            if not C.system_present("outdoor_plants.lawn"):
                flow = sum(C.num(w.ent(e).get("flow_lpm"), 7.5) for e in zones[zone] if e in w.s["entities"])
                C.use_water(w, flow * dt / 60.0)


# ----------------------------------------------------------------------------------
# freezing

HOSE_SPLIT_DOSE = 6.0 * 3600.0       # C*s below 0 C: a water-filled hose (thin, exposed) - ~3 h at -2 C, 45 min at -8 C
BURST_DOSE = 12.0 * 3600.0           # C*s: water trapped in a bib inside the wall needs hours of hard frost...
BURST_BELOW_C = -6.0                 # ...at or below about -6 C (20 F, the usual burst alert); a light frost never bursts it


def freeze_step(w, dt):
    """Frost accumulates as degree-seconds below 0 C (reset by a thaw above +2 C): hoses holding water split after
    HOSE_SPLIT_DOSE (with the air at -2 C or colder), and a bib with trapped water bursts after BURST_DOSE while the
    air is at or below BURST_BELOW_C. One cold hour at -2 C does neither."""
    temp = C.num((w.s.get("weather") or {}).get("temperature_c"), 15.0)
    sec_freeze = C.num(C.pget(w, "freeze_s"))
    dose = C.num(C.pget(w, "freeze_dose"))
    if temp < 0.0:
        sec = C.psec(w)
        sec_freeze += dt
        dose += dt * -temp
        sec["freeze_s"] = C.rnd(sec_freeze, 1)
        sec["freeze_dose"] = C.rnd(dose, 1)
    elif (sec_freeze > 0 or dose > 0) and temp > 2.0:
        sec = C.psec(w)
        sec["freeze_s"] = 0.0
        sec["freeze_dose"] = 0.0
        return
    if dose < HOSE_SPLIT_DOSE or temp > -2.0:
        return
    for hid in hoses(w):
        rec = w.ent(hid)
        rid = C.room_of(w, hid)
        if rid is None or physics.room_temperature(w.s, rid) > -1.0 or rec.get("frozen_split"):
            continue
        if L.liters(rec) > 0.3:
            m = w.mut(hid)
            m["frozen_split"] = True
            m["_leak"] = {"component": "hose_wall", "rate_lpm": 3.0, "when": "pressurized_hose", "started_s": C.now(w)}
            C.damage(w, hid, "split by freezing water inside", kind="freeze_split", severity=0.8)
    if dose < BURST_DOSE or temp > BURST_BELOW_C:
        return
    for eid in spigots(w):
        rec = w.s["entities"][eid]
        if rec.get("frozen_burst"):
            continue
        attached = hose_on(w, eid)
        trapped = False
        if rec.get("frost_free"):
            trapped = attached is not None
        else:
            interior = rec.get("interior_valve")
            interior_open = interior is None or S.valve_position(w.ent(interior)) > 0.001
            outdoor_closed = C.num((rec.get("settings") or {}).get("valve")) <= 0.001
            trapped = interior_open or (outdoor_closed and not rec.get("drained")) or attached is not None
        if not trapped:
            continue
        m = w.mut(eid)
        m["frozen_burst"] = True
        interior = rec.get("interior_valve")
        burst_room = C.room_of(w, interior) if interior and interior in w.s["entities"] else C.room_of(w, eid)
        m["_leak"] = {"component": "frozen_supply_pipe", "rate_lpm": 12.0, "when": "pressurized", "line": "cold",
                      "fixture": eid, "to": {"room": burst_room, "cell": C.cell_of(w, interior) if interior in w.s["entities"] else C.cell_of(w, eid)},
                      "started_s": C.now(w), "severity": "spray"}
        C.damage(w, eid, "pipe burst by ice in the wall", kind="freeze_burst")
        C.plog(w, "freeze_burst", spigot=eid)


@after("open_valve", order=150)
@after("shut_off", order=150)
def spigot_drained(ctx, result):
    """A standard spigot is drained when its interior valve is closed and the outdoor tap is then opened."""
    w = ctx.w
    for eid in spigots(w):
        rec = w.s["entities"][eid]
        if rec.get("frost_free") or not rec.get("interior_valve"):
            continue
        interior = rec["interior_valve"]
        if interior not in w.s["entities"]:
            continue
        closed = S.valve_position(w.ent(interior)) <= 0.001
        if not closed and rec.get("drained"):
            w.mut(eid)["drained"] = False


@after("turn_on_water", order=150)
def spigot_drain_open(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid or "") or {}
    if F.fkind(rec) == "spigot" and rec.get("interior_valve") in w.s["entities"]:
        if S.valve_position(w.ent(rec["interior_valve"])) <= 0.001 and not rec.get("drained"):
            m = w.mut(eid)
            m["drained"] = True
            rid = C.room_of(w, eid)
            if C.is_outdoor(w, rid):
                C.outdoor_water(w, rid, C.cell_of(w, eid), "water", 0.4, source=eid)
            ctx.note("the last water in the pipe ran out of the spigot (drained)")
