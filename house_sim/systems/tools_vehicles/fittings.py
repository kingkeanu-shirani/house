"""Small installable fittings judged on core install/uninstall: furniture leg pads (felt pads, cups,
glides, shims), door/window weather stripping and sweeps (seal geometry, latching, threshold ice),
and filters (water filter twist-lock, pressure and flushing; air filter size/bypass and airflow).

Fidelity G (pad diameter vs leg, seal thickness vs closing clearance, filter nominal size vs slot) with
P-approx flows (filter flush volume, leak volume).
"""
import copy
import math
import re

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook, inspector, system
from . import common as C
from . import fasteners as F


# =============================================================================================
# leg pads

def _leg_slot(slot):
    return isinstance(slot, str) and slot.startswith("pad_") and slot[4:] in F.LEG_KEYS


@before("install", order=95)
def _tilt_for_pads(ctx):
    w = ctx.w
    dest = ctx.args.get("destination")
    rec = w.s["entities"].get(dest) or {}
    slot = ctx.args.get("slot")
    if not rec.get("_legs"):
        return
    if slot is None:
        part = w.s["entities"].get(ctx.args.get("target")) or {}
        if "leg_pad" not in (part.get("tags") or []):
            return
    elif not _leg_slot(slot):
        return
    mass = float(rec.get("mass_kg", 20.0))
    if mass > 30.0 and not C.adults_present(w):
        if ctx.realistic:
            # the robot feels it is too heavy when it starts to tip it, it does not learn the mass
            raise ActionError("It is too heavy to tip safely alone; ask someone to help hold it", "capability")
        raise ActionError("It is too heavy ({:.0f} kg) to tip safely alone; ask someone to help hold it".format(mass), "capability")
    tipped = []
    for child in sorted(w.children(dest)):
        crec = w.ent(child)
        if crec.get("relation") not in ("on", None):
            continue
        tipped.append(child)
        m = w.mut(child)
        room = rec.get("location")
        if crec.get("fragile"):
            m["broken"] = True
            C.add_damage(m, "broken", 1.0)
            w.stat("breakages")
            w.stat("damage")
        if room in w.s["rooms"]:
            for key in ("spos", "shelf"):
                m.pop(key, None)
            m.update(location=room, relation="on", pos=list(geometry.free_floor_cell(w.s, room, near=tuple(rec.get("pos") or (0, 0))) or rec.get("pos") or [0, 0]))
            w.log_move(child, dest, room, by="gravity")
    if tipped:
        C.log(w, "tilted_loaded_furniture", target=dest, fell=tipped)
        ctx.note("Tilting it slid {} off the top".format(", ".join(tipped)))


@hook("part_installed")
def _pad_installed(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    if not rec.get("_legs") or not _leg_slot(slot):
        return
    leg = slot[4:]
    legs = rec["_legs"]
    part = w.ent(part_id)
    leg_d = float((legs.get(leg) or {}).get("diam_mm", 30.0))
    pad_d = float(part.get("diameter_mm", 25.0))
    room = w.root_room(device_id)
    floor = w.s["rooms"].get(room, {}).get("floor_material", "hardwood")
    pm = w.mut(part_id)
    pm["overhang"] = pad_d > leg_d + 4.0
    pm["covers_leg"] = pad_d >= leg_d - 6.0
    dirty = float((legs.get(leg) or {}).get("residue", 0.0)) > 0.1 or float((legs.get(leg) or {}).get("grit", 0.0)) > 0.1
    pm["bond_ok"] = not dirty
    pm["installed_at"] = C.now(w)
    pm["centered"] = True
    if "felt_pad" in (part.get("tags") or []) and floor in ("carpet", "rug"):
        pm["wrong_floor"] = True
    F.refresh(w, device_id)


@hook("part_removed")
def _pad_removed(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    if not rec.get("_legs") or not _leg_slot(slot):
        return
    leg = slot[4:]
    tool = next((h for h in w.held_items() if h != part_id), None)
    trec = w.ent(tool) if tool else {}
    lm = w.mut(device_id)["_legs"].setdefault(leg, {})
    if trec.get("tool_category") in ("screwdriver",) or trec.get("tool_kind") in ("screwdriver", "chef_knife", "utility_knife"):
        lm["gouged"] = True
        C.add_damage(w.mut(device_id), "gouged", 0.3)
        w.stat("damage")
        C.log(w, "leg_gouged", target=device_id, leg=leg, tool=tool)
    elif trec.get("tool_kind") in ("putty_knife", "plastic_scraper") or trec.get("tool_category") == "scraper":
        lm["residue"] = 0.05
    else:
        lm["residue"] = 0.3
    if w.ent(part_id).get("tags") and "felt_pad" in w.ent(part_id)["tags"]:
        w.mut(part_id)["worn"] = True
    F.refresh(w, device_id)


def _has_legs(rec):
    return bool(rec.get("_legs") and rec.get("slots"))


@system("tools_vehicles.pads", order=138, max_dt=60.0, period=300)
def pad_system(w, dt, tick):
    """Every 5 min: overhanging, unbonded or temporary pads come off 6 h / 2 days after installation (timestamps)."""
    t = C.now(w)
    ents = w.s["entities"]
    for eid in C.index(w, "legged", _has_legs):
        rec = ents.get(eid)
        if rec is None or not rec.get("slots"):
            continue
        for slot, info in sorted(rec["slots"].items()):
            item = info.get("item")
            if not _leg_slot(slot) or not item or item not in w.s["entities"]:
                continue
            pad = w.ent(item)
            weak = pad.get("overhang") or not pad.get("bond_ok", True) or pad.get("temporary")
            if weak and t - float(pad.get("installed_at", t)) > (6 * 3600 if not pad.get("temporary") else 2 * 86400):
                m = w.mut(eid)
                m["slots"][slot]["item"] = None
                room = rec.get("location")
                pm = w.mut(item)
                pm.update(location=room if room in w.s["rooms"] else rec.get("location"), relation="on", detached=True)
                pm.pop("slot", None)
                if room in w.s["rooms"]:
                    pm["pos"] = list(rec.get("pos") or [0, 0])
                C.log(w, "pad_came_off", target=eid, slot=slot, pad=item)
                F.refresh(w, eid)


# =============================================================================================
# seals, weather stripping, latching, threshold ice

SEG_SLOT = re.compile(r"^seal_(\w+)$")


def refresh_latch(w, eid):
    rec = w.ent(eid)
    seal = rec.get("_seal")
    if not isinstance(seal, dict):
        return
    clearance = float(seal.get("clearance_mm", 3.0))
    ok = float(seal.get("threshold_ice_mm", 0.0)) < 3.0
    reason = None if ok else "ice at the threshold"
    gap = None
    for slot, info in sorted((rec.get("slots") or {}).items()):
        item = info.get("item")
        if item and item in w.s["entities"] and (SEG_SLOT.match(slot) or slot == "sweep"):
            strip = w.ent(item)
            if slot == "sweep":
                continue
            squeezed = float(strip.get("thickness_mm", 5.0)) * (1.0 - float(strip.get("compression", 0.6)))
            if squeezed > clearance + 0.5:
                ok = False
                # the visible issue carries no hidden measurements (the door's latch_issue is an inspect-tier field);
                # the millimetres stay in the hidden `_latch` record for the oracle message
                reason = "weatherstrip too thick for the gap"
                gap = {"compressed_mm": C.rnd(squeezed, 1), "gap_mm": C.rnd(clearance, 1)}
    if rec.get("latch_ok") != ok or rec.get("latch_issue") != reason or rec.get("_latch") != gap:
        m = w.mut(eid)
        m["latch_ok"] = ok
        m["latch_issue"] = reason
        if gap is not None:
            m["_latch"] = gap
        else:
            m.pop("_latch", None)
        if not rec.get("open"):
            m["latched"] = ok


def seal_failures(rec):
    seal = rec.get("_seal") or {}
    out = []
    for name, seg in sorted((seal.get("segments") or {}).items()):
        if seg.get("state") not in ("ok", None):
            out.append(name)
    if seal.get("caulk") in ("cracked", "missing"):
        out.append("exterior_caulk")
    if seal.get("igu_failed"):
        out.append("glass_unit")
    return out


@after("close", order=125)
def _latch_on_close(ctx, result):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.ent(eid)
    if isinstance(rec.get("_seal"), dict):
        refresh_latch(w, eid)
        rec = w.ent(eid)
        w.mut(eid)["latched"] = bool(rec.get("latch_ok", True))
        if not rec.get("latch_ok", True):
            ctx.note("It closes but does not latch ({})".format(_latch_words(ctx, rec)))


@after("open", order=125)
def _unlatch(ctx, result):
    rec = ctx.w.ent(ctx.args["target"])
    if isinstance(rec.get("_seal"), dict) and rec.get("latched"):
        ctx.w.mut(ctx.args["target"])["latched"] = False


@before("lock", order=95)
def _lock_needs_latch(ctx):
    rec = ctx.w.s["entities"].get(ctx.args["target"]) or {}
    if isinstance(rec.get("_seal"), dict) and not rec.get("open"):
        refresh_latch(ctx.w, ctx.args["target"])
        rec = ctx.w.ent(ctx.args["target"])
        require(rec.get("latch_ok", True), "The door is not latched ({}); it cannot be locked".format(_latch_words(ctx, rec)), "precondition")


def _latch_words(ctx, rec):
    """Why a door does not latch, as said: realistic perception gives the cause without the hidden gap measurements
    (oracle mode adds them from the hidden `_latch` record)."""
    issue = rec.get("latch_issue")
    gap = rec.get("_latch")
    if ctx.realistic or not isinstance(gap, dict) or not isinstance(issue, str):
        return issue
    return "{} ({:.1f} mm compressed vs {:.1f} mm gap)".format(issue, float(gap.get("compressed_mm", 0.0)), float(gap.get("gap_mm", 0.0)))


@hook("part_installed")
def _seal_installed(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    seal = rec.get("_seal")
    if not isinstance(seal, dict):
        return
    seg_name = "sweep" if slot == "sweep" else (SEG_SLOT.match(slot).group(1) if SEG_SLOT.match(slot) else None)
    if not seg_name:
        return
    part = w.ent(part_id)
    m = w.mut(device_id)
    seg = m["_seal"].setdefault("segments", {}).setdefault(seg_name, {})
    need = float(seg.get("length_m", 2.0))
    length = float(part.get("length_m", need))
    frame_soil = m.get("frame_soil", 0.0)
    if isinstance(m.get("soil"), dict):
        frame_soil = max(float(frame_soil), max([float(v) for v in m["soil"].values()] or [0.0]))
    bonded = not part.get("adhesive") or (float(frame_soil) < 0.1 and float(m.get("wetness", 0.0)) < 0.1)
    if length < need - 0.02:
        seg["state"] = "gap"
        seg["gap_mm"] = C.rnd((need - length) * 1000, 1)
    elif not bonded:
        seg["state"] = "ok"
        seg["peel_at"] = C.now(w) + 86400.0
    else:
        seg["state"] = "ok"
        seg.pop("peel_at", None)
    seg["kind"] = part.get("seal_kind")
    seg["installed"] = part_id
    pm = w.mut(part_id)
    pm["installed_at"] = C.now(w)
    refresh_latch(w, device_id)


@hook("part_removed")
def _seal_removed(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    if not isinstance(rec.get("_seal"), dict):
        return
    seg_name = "sweep" if slot == "sweep" else (SEG_SLOT.match(slot).group(1) if SEG_SLOT.match(slot) else None)
    if not seg_name:
        return
    seg = w.mut(device_id)["_seal"].setdefault("segments", {}).setdefault(seg_name, {})
    seg["state"] = "missing"
    seg.pop("installed", None)
    refresh_latch(w, device_id)


def _has_seal(rec):
    return isinstance(rec.get("_seal"), dict)


@system("tools_vehicles.seals", order=139, max_dt=60.0, period=300)
def seal_system(w, dt, tick):
    """Every 5 min: adhesive weatherstrip pressed onto a dusty/wet frame peels at its peel_at time."""
    t = C.now(w)
    ents = w.s["entities"]
    for eid in C.index(w, "sealed_openings", _has_seal):
        rec = ents.get(eid)
        if rec is None or not isinstance(rec.get("_seal"), dict):
            continue
        for name, seg in sorted((rec["_seal"].get("segments") or {}).items()):
            if seg.get("peel_at") is not None and t >= float(seg["peel_at"]):
                m = w.mut(eid)
                s = m["_seal"]["segments"][name]
                s["state"] = "peeling"
                s.pop("peel_at", None)
                C.log(w, "weatherstrip_peeled", target=eid, segment=name)


@inspector("tools_vehicles.seals", order=67)
def inspect_seals(w, eid, record, details, ctx):
    if eid not in w.s["entities"]:
        return
    seal = record.get("_seal")
    if not isinstance(seal, dict):
        return
    closely = ctx.args.get("where") == "closely"
    visible = {}
    for name, seg in sorted((seal.get("segments") or {}).items()):
        state = seg.get("state", "ok")
        if state in ("torn", "missing", "peeling", "gap"):
            visible[name] = state
    if seal.get("caulk") in ("cracked", "missing"):
        visible["exterior_caulk"] = seal["caulk"]
    if float(seal.get("threshold_ice_mm", 0.0)) > 0.5:
        ice = float(seal["threshold_ice_mm"])
        visible["threshold"] = ("thick ice buildup" if ice >= 3.0 else "thin ice buildup") if C.realistic(ctx) else "ice buildup {:.0f} mm".format(ice)
    cond = seal.get("condensation")
    if cond:
        visible["glass"] = "fogged"
    details["seal_visual"] = visible
    if closely:
        if record.get("open"):
            details["seal_test"] = "close it to run the paper-pull and light-gap test"
            return
        if geometry.reach_problem(w.s, eid) is not None:
            details["seal_test"] = "come within reach to run the paper-pull test"
            return
        test = {}
        for name, seg in sorted((seal.get("segments") or {}).items()):
            state = seg.get("state", "ok")
            test[name] = {"ok": "firm drag (sealing)", "compressed": "paper slides out (not sealing)", "torn": "torn",
                          "missing": "missing", "peeling": "peeling away", "gap": "gap at the corner"}.get(state, state)
        details["seal_test"] = test
        if cond:
            details["fog_location"] = "between the panes (seal failed)" if seal.get("igu_failed") else "inside surface (wipes off: room humidity)"
        details["latches"] = bool(record.get("latch_ok", True))
        w.sec("facts").setdefault("tested", {})[eid] = w.s["time_s"]


# =============================================================================================
# filters

def _pressurized(w, host):
    rec = w.ent(host)
    settings = rec.get("settings") or {}
    valve = settings.get("supply_valve")
    if valve is not None and valve != "open":
        return False
    linked = rec.get("supply_valve_id")
    if linked and linked in w.s["entities"]:
        lrec = w.ent(linked)
        if lrec.get("valve_open") is False or (lrec.get("settings") or {}).get("position") in ("closed", "off"):
            return False
    if rec.get("relieved"):
        return False
    return physics.water_available(w, host)


def filter_slot(rec):
    for name, info in sorted((rec.get("slots") or {}).items()):
        if info.get("water_filter") or name == "water_filter":
            return name
    return None


@before("uninstall", order=95)
def _filter_out_guard(ctx):
    w = ctx.w
    host = ctx.args["target"]
    rec = w.s["entities"].get(host) or {}
    slot = ctx.args.get("slot") or filter_slot(rec)
    if not slot or slot != filter_slot(rec):
        return _air_filter_out(ctx, host, rec, ctx.args.get("slot"))
    twist = (rec.get("_fasteners") or {}).get("water_filter")
    if twist and float(twist.get("torque_nm", 0.0)) > 0.01:
        raise ActionError("Twist the filter a quarter turn to unlock it first (loosen fastener water_filter)", "precondition")
    if _pressurized(w, host):
        if rec.get("housing") == "sump":
            m = w.mut(host)
            m["wetness"] = 1.0
            m["leaked_l"] = C.rnd(float(m.get("leaked_l", 0.0)) + 6.0, 2)
            for child in sorted(w.children(rec.get("location") or "")) if rec.get("location") in w.s["entities"] else []:
                cm = w.mut(child)
                cm["wetness"] = 1.0
            w.stat("flood")
            w.stat("spills")
            C.log(w, "housing_opened_under_pressure", host=host)
            ctx.note("Water sprayed out of the pressurized housing")
        else:
            m = w.mut(host)
            m["leaked_l"] = C.rnd(float(m.get("leaked_l", 0.0)) + 0.15, 2)
            w.stat("spills")
            ctx.note("A little water dripped from the filter head")


@hook("part_installed")
def _filter_in(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    if slot == filter_slot(rec):
        m = w.mut(device_id)
        f = m.setdefault("_fasteners", {}).get("water_filter")
        if f is None:
            F.attach_fasteners(m, [{"id": "water_filter", "kind": "filter_twist", "drive": "hand", "size": "1", "role": "filter",
                                    "substrate": "plastic", "spot": "filter head"}])
            f = m["_fasteners"]["water_filter"]
        f.update(torque_nm=0.0, removed=False, cracked=False)
        pm = w.mut(part_id)
        pm.setdefault("flushed_l", 0.0)
        pm["installed_at"] = C.now(w)
        m["water_quality"] = "fines"
        return
    _air_filter_in(ctx, device_id, slot, part_id)


@hook("part_removed")
def _filter_removed(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    if slot == filter_slot(rec):
        m = w.mut(device_id)
        m["water_quality"] = "no_filter"
        if "water_filter" in (m.get("_fasteners") or {}):
            m["_fasteners"]["water_filter"]["torque_nm"] = 0.0


def filter_locked(w, host):
    rec = w.ent(host)
    f = (rec.get("_fasteners") or {}).get("water_filter")
    return bool(f) and F.snug(f)


def _flush_target(w, outlet):
    """Where water from a filtered outlet goes: 'drain', a container id, or None (floor)."""
    rec = w.ent(outlet)
    if rec.get("drain") or rec.get("over_drain"):
        return "drain"
    parent = w.s["entities"].get(rec.get("location"))
    if parent and (parent.get("drain") or parent.get("category") == "sink"):
        return "drain"
    for child in sorted(w.children(outlet)):
        crec = w.ent(child)
        if crec.get("liquid_container") or float(crec.get("capacity_l", 0.0)) > 0.2:
            return child
    return None


def _water_filter_host(rec):
    return filter_slot(rec) is not None


@system("tools_vehicles.water_filters", order=140, max_dt=30.0)
def water_filter_system(w, dt, tick):
    """Per substep (leaks and flushing follow valve/faucet actions within seconds) over the static host index."""
    ents = w.s["entities"]
    for host in C.index(w, "water_filter_hosts", _water_filter_host):
        rec = ents.get(host)
        slot = filter_slot(rec) if rec else None
        if not slot:
            continue
        item = rec["slots"][slot].get("item")
        if not item or item not in w.s["entities"]:
            continue
        pressurized = _pressurized(w, host)
        if pressurized and not filter_locked(w, host):
            rate = 0.5 if rec.get("housing") != "sump" else 2.0
            m = w.mut(host)
            before = float(m.get("leaked_l", 0.0))
            m["leaked_l"] = C.rnd(before + rate * dt / 60.0, 3)
            m["wetness"] = 1.0
            if before < 1.0 <= m["leaked_l"]:
                w.stat("flood")
                w.stat("spills")
                C.log(w, "filter_leak", host=host)
            continue
        outlet = rec.get("flush_outlet")
        if not outlet or outlet not in w.s["entities"] or not pressurized:
            continue
        orec = w.ent(outlet)
        if not (orec.get("on") or orec.get("water_running")):
            continue
        flow = float(orec.get("flow_lpm", rec.get("flow_lpm", 1.9))) * dt / 60.0
        fm = w.mut(item)
        fm["flushed_l"] = C.rnd(float(fm.get("flushed_l", 0.0)) + flow, 3)
        fm["used_l"] = C.rnd(float(fm.get("used_l", 0.0)) + flow, 3)
        hm = w.mut(host)
        hm["water_quality"] = "clear" if fm["flushed_l"] >= float(fm.get("flush_l", 15.0)) else "fines"
        utilities = w.sec("utilities")
        utilities["water_l"] = round(float(utilities.get("water_l", 1e5)) - flow, 3)
        dest = _flush_target(w, outlet)
        if dest == "drain":
            continue
        if dest:
            dm = w.mut(dest)
            cap = float(dm.get("capacity_l", 1.0))
            have = float(dm.get("liquid_l", 0.0))
            room_left = max(0.0, cap - have)
            dm["liquid_l"] = C.rnd(min(cap, have + flow), 3)
            dm["liquid"] = "water"
            if not dm.get("liquid_container"):
                dm["liquid_container"] = True
            overflow = max(0.0, flow - room_left)
        else:
            overflow = flow
        if overflow > 1e-6:
            om = w.mut(outlet)
            om["spilled_l"] = C.rnd(float(om.get("spilled_l", 0.0)) + overflow, 3)
            if om["spilled_l"] >= 0.2 and om["spilled_l"] - overflow < 0.2:
                w.stat("spills")
                room = w.root_room(outlet)
                _, cells = C.anchor_cells(w, outlet)
                if room and cells:
                    rm = w.room_m(room)
                    wet = rm.setdefault("wet_cells", [])
                    cell = [int(cells[0][0]), int(cells[0][1])]
                    if cell not in wet:
                        wet.append(cell)
                C.log(w, "flush_spill", outlet=outlet)


def _air_filter_out(ctx, host, rec, slot):
    if not slot:
        return
    info = (rec.get("slots") or {}).get(slot) or {}
    if not info.get("fit"):
        return
    if _hvac_running(rec):
        ctx.note("The system is still running: it will pull unfiltered air through the open slot")


def _hvac_running(rec):
    machine = rec.get("appliance") or {}
    mode = (rec.get("settings") or {}).get("mode")
    return bool(rec.get("on") or rec.get("running") or machine.get("state") == "running" or (mode not in (None, "off") and rec.get("hvac")))


NOMINAL = re.compile(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$")


def _nominal(s):
    m = NOMINAL.match(str(s or ""))
    return tuple(float(v) for v in m.groups()) if m else None


@before("install", order=96)
def _air_filter_fit(ctx):
    w = ctx.w
    dest = ctx.args.get("destination")
    rec = w.s["entities"].get(dest) or {}
    part = w.s["entities"].get(ctx.args.get("target")) or {}
    spec = part.get("filter_spec")
    if not spec:
        return
    for name, info in sorted((rec.get("slots") or {}).items()):
        if ctx.args.get("slot") not in (None, name) or not info.get("fit"):
            continue
        a, b = _nominal(info["fit"].get("nominal")), _nominal(spec.get("nominal"))
        if a and b and (b[0] > a[0] + 0.01 or b[1] > a[1] + 0.01 or b[2] > a[2] + 0.01):
            if ctx.realistic:
                raise ActionError("That filter is too big for this slot", "precondition")
            raise ActionError("A {} filter does not fit a {} slot".format(spec.get("nominal"), info["fit"].get("nominal")), "precondition")


def _air_filter_in(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.ent(device_id)
    info = (rec.get("slots") or {}).get(slot) or {}
    fit = info.get("fit")
    part = w.ent(part_id)
    spec = part.get("filter_spec")
    if not fit or not spec:
        return
    if _climate_owned(part):
        return  # the climate domain derives bypass_gap_mm / reversed / installed_at for its HVAC filters (single source)
    a, b = _nominal(fit.get("nominal")), _nominal(spec.get("nominal"))
    gap = 0.0
    if a and b:
        gap = ((a[0] - b[0]) + (a[1] - b[1])) * 25.4
    pm = w.mut(part_id)
    pm["bypass_gap_mm"] = C.rnd(max(0.0, gap), 1)
    pm["reversed"] = spec.get("arrow", fit.get("airflow")) != fit.get("airflow", "toward_blower")
    pm["installed_at"] = C.now(w)
    if pm["reversed"]:
        C.log(w, "filter_reversed", host=device_id, part=part_id)


def _flip_arrow(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    spec = rec.get("filter_spec")
    if isinstance(spec, dict) and spec.get("arrow"):
        m = w.mut(target)
        m["filter_spec"]["arrow"] = "away_from_blower" if spec["arrow"] == "toward_blower" else "toward_blower"


for _verb in ("flip", "rotate"):
    after(_verb, order=140)(_flip_arrow)


def _air_filter_host(rec):
    return any((i or {}).get("fit") for i in (rec.get("slots") or {}).values())


def _climate_owned(rec):
    """HVAC equipment and filters the climate domain models (its own duct dust and filter dirtiness)."""
    return bool(rec.get("climate_kind"))


@system("tools_vehicles.air_filters", order=141, max_dt=60.0)
def air_filter_system(w, dt, tick):
    """Per substep while the host runs: filterless / bypass seconds (task predicates read them as they happen).
    Duct dust on climate-modelled equipment is the climate domain's (it accrues the same 1/36000 s rate)."""
    ents = w.s["entities"]
    for host in C.index(w, "air_filter_hosts", _air_filter_host):
        rec = ents.get(host)
        if rec is None or not _hvac_running(rec):
            continue
        for name, info in sorted(rec["slots"].items()):
            if not info.get("fit"):
                continue
            if not info.get("item"):
                m = w.mut(host)
                m["ran_filterless_s"] = C.rnd(float(m.get("ran_filterless_s", 0.0)) + dt, 1)
                if not _climate_owned(rec):
                    m["duct_dust"] = C.rnd(min(1.0, float(m.get("duct_dust", 0.0)) + dt / 36000.0), 4)
            else:
                part = ents.get(info["item"]) or {}
                if float(part.get("bypass_gap_mm", 0.0)) > 6.0 or part.get("reversed"):
                    m = w.mut(host)
                    m["bypass_s"] = C.rnd(float(m.get("bypass_s", 0.0)) + dt, 1)


@system("tools_vehicles.filter_load", order=142, max_dt=600.0, period=600)
def filter_load_system(w, dt, tick):
    """Every 10 min: an installed air filter in a running host loads up (~90 days of run time to full, linear in dt).
    Climate-modelled filters track their own `dirtiness` instead."""
    ents = w.s["entities"]
    for host in C.index(w, "air_filter_hosts", _air_filter_host):
        rec = ents.get(host)
        if rec is None or not _hvac_running(rec):
            continue
        for name, info in sorted(rec["slots"].items()):
            item = info.get("item") if info.get("fit") else None
            part = ents.get(item) if item else None
            if part is None or _climate_owned(part):
                continue
            pm = w.mut(item)
            pm["_load"] = C.rnd(min(1.0, float(part.get("_load", 0.0)) + dt / (90 * 86400.0)), 5)


@inspector("tools_vehicles.filters", order=68)
def inspect_filters(w, eid, record, details, ctx):
    if eid not in w.s["entities"]:
        return
    if record.get("filter_spec") or record.get("filter_model") or "water_filter" in (record.get("tags") or []):
        if ctx.args.get("where") == "closely" or record.get("location") == "robot":
            if "_load" in record:
                load = float(record["_load"])
                # realistic: a look at the media gives a dirt level, not a percentage
                details["loading"] = C.bucket(load, *C.FILTER_LOAD_WORDS) if C.realistic(ctx) else "{}% loaded".format(int(round(100 * load)))
            if "flushed_l" in record:
                details["flushed_l"] = round(float(record["flushed_l"]), 1)
    slot = filter_slot(record)
    if slot:
        details["filter_installed"] = (record.get("slots") or {}).get(slot, {}).get("item")
        details["filter_locked"] = filter_locked(w, eid) if details["filter_installed"] else False
        details["water_quality"] = record.get("water_quality")
        if float(record.get("leaked_l", 0.0)) > 0:
            details["leak"] = "water around the filter head"
