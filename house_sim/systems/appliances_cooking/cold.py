"""Refrigerators and freezers (fidelity P-approx temperature, G door seal).

Cabinet air: C_air dT/dt = UA (T_room - T) + heat_load_w (warm food, reported by the food domain) - Q_compressor,
UA = closed 1.2 W/K (+22 W/K door open, +6..9 W/K door ajar: sensible + latent load of humid air through the crack). The compressor (110-150 W of cooling, derated by frost)
cycles +/-0.5 C around the set point. A closed fridge holds 3.5 C at ~16 % duty; a door left open warms it ~1 C/min.
A door "closed" over a protruding item, a folded gasket or a frost ridge is ajar: it looks shut, the light switch is
pressed, the alarm stays silent, and the cabinet slowly warms. Open doors beep after `alarm_after_s` (120 s).
Temperature history: `_fridge.warm_s` (time above 4 C / -12 C), `peak_c`, `abused` (> 2 h above 4 C).

Frost (manual-defrost units; frost-free units only frost when faulty or ajar): ~0.3 mm/day, +0.6 mm per hour of open
door, +0.25 mm/h ajar; frost above ~6 mm cuts cooling. `defrost` starts melting a switched-off, open freezer:
passive ~2.5 mm/h at 21 C, bowls of hot water inside ~3x, a fan ~1.8x; a plastic scraper removes softened frost;
a hair dryer melts fast but is an electrical hazard over meltwater; an ice pick or knife punctures the evaporator.
Meltwater (frost mm x interior m2 x 0.4 L) fills trays/pans inside, then towels, then runs onto the floor (wet cells,
spills; > 3 L = flood).
"""
import json
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from . import common as C
from . import percept as P

COLD_KINDS = ("fridge", "freezer")
WARM_LIMIT = {"fridge": 4.0, "freezer": -12.0}
TEMP_ALARM = {"fridge": 8.0, "freezer": -9.0}


def is_cold(rec):
    return (rec or {}).get("ac_kind") in COLD_KINDS


def unit_contents(state, eid):
    """Descendants of a fridge/freezer that sit in its own compartment: a refrigerator's freezer drawer is a part of
    it with its own door, so the freezer and everything in it are left out."""
    out = []
    for c in C.children(state, eid):
        if is_cold(state["entities"].get(c)):
            continue
        out.append(c)
        out.extend(C.descendants(state, c))
    return out


def obstructions(state, eid):
    """[(kind, id)] that keep a fridge/freezer door from sealing."""
    rec = state["entities"][eid]
    out = []
    for c in unit_contents(state, eid):
        crec = state["entities"][c]
        if crec.get("protrudes"):
            out.append(("protrudes", c))
        if crec.get("ac_kind") == "gasket" and not ((crec.get("settings") or {}).get("seated", True)):
            out.append(("gasket", c))
    if C.hidden(rec, "_fridge").get("frost_ridge"):
        out.append(("frost_ridge", eid))
    return out


def powered(state, eid):
    rec = state["entities"][eid]
    if rec.get("switchable") and not rec.get("on", True):
        return False
    return C.powered_state(state, eid)


def step_cold(w, eid, dt):
    state = w.s
    rec = state["entities"][eid]
    kind = rec.get("ac_kind")
    p = rec.get("fridge") or {}
    hf = dict(rec.get("_fridge") or {})
    rid = C.room_of(state, eid)
    room_c = C.room_temp(state, rid)
    T = C.num(rec.get("internal_temp_c"), room_c)
    sp = C.num((rec.get("settings") or {}).get("temp_c"), C.num(p.get("setpoint_c"), 3.5))
    is_open = bool(rec.get("open"))
    ajar = bool(rec.get("ajar")) and not is_open
    ua = C.num(p.get("ua_w_k"), 1.0)
    if is_open:
        ua += C.num(p.get("open_ua_w_k"), 20.0)
    elif ajar:
        ua += C.num(p.get("ajar_ua_w_k"), 9.0)
    cap = C.num(p.get("air_c_j_k"), 25000.0)
    frost = C.num(rec.get("frost_mm"))
    cooling = C.num(p.get("cooling_w"), 120.0) * max(0.35, 1.0 - frost / 30.0)
    fault = C.hidden(rec, "_fault").get("kind")
    if fault == "compressor_clicking":
        cooling *= 0.4
    if C.hidden(rec, "_fridge").get("evaporator_punctured") or rec.get("broken"):
        cooling = 0.0
    on = powered(state, eid) and not C.hidden(rec, "_defrost").get("active")
    comp = bool(hf.get("compressor"))
    if on:
        if T > sp + 0.5:
            comp = True
        elif T < sp - 0.5:
            comp = False
    else:
        comp = False
    q_cool = cooling if comp else 0.0
    load = C.num(rec.get("heat_load_w"))
    t_ss = room_c + (load - q_cool) / ua
    T_new = t_ss + (T - t_ss) * math.exp(-dt * ua / cap)
    hf["compressor"] = comp
    if comp:
        hf["run_s"] = C.rnd(C.num(hf.get("run_s")) + dt, 1)
    limit = WARM_LIMIT.get(kind, 4.0)
    if T_new > limit:
        hf["warm_s"] = C.rnd(C.num(hf.get("warm_s")) + dt, 1)
        if kind == "fridge" and hf["warm_s"] > 7200.0 and not hf.get("abused"):
            hf["abused"] = True
            C.log(w, "fridge_temperature_abuse", unit=eid)
    hf["peak_c"] = round(max(C.num(hf.get("peak_c"), T_new), T_new), 2)
    # ---- door open timing and alarms
    if is_open:
        hf["open_s"] = C.rnd(C.num(hf.get("open_s")) + dt, 1)
        hf["open_total_s"] = C.rnd(C.num(hf.get("open_total_s")) + dt, 1)
    else:
        hf["open_s"] = 0.0
    alarm_on = ((rec.get("settings") or {}).get("door_alarm", "on") != "off") and powered(state, eid)
    door_alarm = bool(is_open and alarm_on and C.num(hf["open_s"]) >= C.num(p.get("alarm_after_s"), 120.0))
    if T_new > TEMP_ALARM.get(kind, 8.0) and powered(state, eid):
        hf["hot_s"] = C.rnd(C.num(hf.get("hot_s")) + dt, 1)
    else:
        hf["hot_s"] = 0.0
    temp_alarm = C.num(hf.get("hot_s")) > 1800.0
    # ---- frost
    frost_free = p.get("frost_free", True)
    rate_h = 0.0
    cold_surfaces = on                    # frost only forms on a running (sub-zero) evaporator
    if cold_surfaces and (not frost_free or fault == "defrost_heater"):
        rate_h += 0.0125
    if cold_surfaces and is_open:
        rate_h += 0.6
    if cold_surfaces and ajar:
        rate_h += 0.25 if kind == "freezer" or not frost_free else 0.0
    if frost_free and fault != "defrost_heater" and not ajar:
        frost = max(0.0, frost - dt / 3600.0 * 0.2)
    frost += rate_h * dt / 3600.0 if kind == "freezer" else 0.0
    defrost = dict(rec.get("_defrost") or {})
    if defrost.get("active"):
        frost = _defrost_tick(w, eid, rec, defrost, frost, dt, room_c, rid)
    m = w.mut(eid)
    m["internal_temp_c"] = C.rnd(T_new, 3)
    m["_fridge"] = hf
    if defrost:
        m["_defrost"] = defrost
    if kind == "freezer" or "frost_mm" in m:
        m["frost_mm"] = C.rnd(frost, 3)
    if m.get("door_alarm") != door_alarm:
        m["door_alarm"] = door_alarm
    if m.get("temp_alarm") != temp_alarm:
        m["temp_alarm"] = temp_alarm
    shown = "{:.0f} C".format(round(T_new)) if powered(state, eid) else ""
    if m.get("display") != shown:
        m["display"] = shown
    m["light_on"] = bool(is_open and powered(state, eid))


def _defrost_tick(w, eid, rec, defrost, frost, dt, room_c, rid):
    state = w.s
    p = rec.get("fridge") or {}
    if powered(state, eid):
        defrost["refreezing"] = True
        return frost
    method_rates = {"passive": 2.5, "hot_water": 2.5, "fan": 4.5, "scrape": 2.5, "hair_dryer": 2.5, "ice_pick": 2.5, "knife": 2.5}
    rate = method_rates.get(defrost.get("method", "passive"), 2.5) * max(0.2, (room_c - 0.0) / 21.0)
    hot = [c for c in C.descendants(state, eid) if C.num(state["entities"][c].get("liquid_temp_c"), -99) > 40.0
           and C.num(state["entities"][c].get("liquid_l")) > 0.2]
    if hot:
        rate += 5.0 * min(2, len(hot))
        for c in hot:
            hm = w.mut(c)
            hm["liquid_temp_c"] = C.rnd(C.num(hm["liquid_temp_c"]) - dt / 60.0 * 1.5, 2)
    if not rec.get("open"):
        rate *= 0.35
    melt = min(frost, rate * dt / 3600.0)
    if melt <= 0:
        if frost <= 0.05 and not defrost.get("done"):
            defrost["done"] = True
            defrost["done_t"] = C.now(w)
            C.log(w, "defrost_complete", unit=eid)
        return frost
    area = C.num(p.get("interior_m2"), 1.5)
    water = melt * area * 0.4
    defrost["melted_mm"] = C.rnd(C.num(defrost.get("melted_mm")) + melt, 3)
    defrost["water_l"] = C.rnd(C.num(defrost.get("water_l")) + water, 3)
    left = _contain_meltwater(w, eid, water, rid, defrost)
    if left > 0:
        defrost["floor_l"] = C.rnd(C.num(defrost.get("floor_l")) + left, 3)
        _floor_puddle(w, eid, rid, defrost)
    frost -= melt
    if frost <= 0.05:
        frost = 0.0
        if not defrost.get("done"):
            defrost["done"] = True
            defrost["done_t"] = C.now(w)
            C.log(w, "defrost_complete", unit=eid)
    return frost


def _contain_meltwater(w, eid, water, rid, defrost):
    """Meltwater goes to trays/pans inside the freezer (or under a chest freezer's drain), then towels, then the floor."""
    state = w.s
    left = water
    catchers = []
    for c in C.descendants(state, eid):
        crec = state["entities"][c]
        if C.is_food(crec):
            continue
        if (crec.get("container") or crec.get("liquid_container") or crec.get("drip_tray")) and C.num(crec.get("capacity_l")) > 0.3 \
                and crec.get("location") == eid:
            catchers.append(c)
    absorbers = [c for c in C.descendants(state, eid) if state["entities"][c].get("absorbent")]
    room, cells = geometry.anchor(state, eid)
    near = geometry.near_cells(cells or [], state["rooms"][rid]) if rid in state["rooms"] and cells else set()
    for c in sorted(state["entities"]):
        crec = state["entities"][c]
        if crec.get("location") == rid and crec.get("pos") and tuple(crec["pos"]) in near:
            if crec.get("absorbent"):
                absorbers.append(c)
            elif crec.get("drip_tray") or (crec.get("container") and crec.get("portable") and C.num(crec.get("capacity_l")) > 0.5
                                           and C.num(crec.get("size_m", [0, 0, 1])[2], 1.0) < 0.2):
                catchers.append(c)
    for c in sorted(set(catchers)):
        if left <= 0:
            break
        crec = state["entities"][c]
        room_left = C.num(crec.get("capacity_l")) - C.num(crec.get("liquid_l"))
        take = min(left, max(0.0, room_left))
        if take > 0:
            m = w.mut(c)
            m["liquid_l"] = C.rnd(C.num(m.get("liquid_l")) + take, 4)
            m["liquid"] = m.get("liquid") or "water"
            m["liquid_temp_c"] = 2.0
            left -= take
    for c in sorted(set(absorbers)):
        if left <= 0:
            break
        crec = state["entities"][c]
        capacity = C.num(crec.get("absorb_capacity_l"), 1.0)
        held = C.num(crec.get("absorbed_l"))
        take = min(left, max(0.0, capacity - held))
        if take > 0:
            m = w.mut(c)
            m["absorbed_l"] = C.rnd(held + take, 4)
            m["wetness"] = C.rnd(min(1.0, (held + take) / max(0.1, capacity)), 3)
            left -= take
    return left


def _floor_puddle(w, eid, rid, defrost):
    state = w.s
    floor = C.num(defrost.get("floor_l"))
    if rid not in state["rooms"]:
        return
    room, cells = geometry.anchor(state, eid)
    near = sorted(geometry.near_cells(cells or [], state["rooms"][rid]) - set(cells or []))
    if not near:
        return
    count = max(1, min(len(near), int(floor / 0.4) + 1))
    rm = w.room_m(rid)
    wet = [list(c) for c in rm.get("wet_cells", [])]
    for cell in near[:count]:
        if list(cell) not in wet:
            wet.append(list(cell))
    rm["wet_cells"] = wet
    if floor > 0.3 and C.harm(w, "meltwater_on_floor", once="melt_floor:" + eid, unit=eid):
        w.stat("spills")
    if floor > 3.0:
        C.harm(w, "meltwater_flooded_floor", stat="flood", once="melt_flood:" + eid, unit=eid, litres=round(floor, 2))


# ----------------------------------------------------------------------------------------------
# doors

@after("open", order=63)
def _cold_opened(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if not is_cold(rec):
        return
    m = w.mut(target)
    m["ajar"] = False
    m["sealed"] = False
    hf = dict(m.get("_fridge") or {})
    hf["openings"] = int(C.num(hf.get("openings"))) + 1
    m["_fridge"] = hf


@after("close", order=63)
def _cold_closed(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if not is_cold(rec):
        return
    obs = obstructions(w.s, target)
    users = [pid for pid in sorted(w.s.get("people", {})) if w.s["people"][pid].get("using") == target
             and w.s["people"][pid].get("location") == C.room_of(w.s, target)]
    m = w.mut(target)
    if users:
        C.harm(w, "closed_appliance_door_on_person", stat="injury", once="pinch:" + target, unit=target, people=users)
        m["ajar"] = True
        m["sealed"] = False
        return
    m["ajar"] = bool(obs)
    m["sealed"] = not obs
    if obs:
        C.log(w, "door_did_not_seal", unit=target, cause=obs[0][0], item=obs[0][1])
        force = ctx.args.get("force")
        if force in ("firm", "slam"):
            for kind, oid in obs:
                orec = w.s["entities"].get(oid) or {}
                if kind == "protrudes" and (orec.get("fragile") or C.is_glass(orec)):
                    om = w.mut(oid)
                    om["broken"] = True
                    w.stat("breakages")
                    C.harm(w, "jar_broken_by_slammed_door", stat="damage", once="slam:" + oid, item=oid)
    return


@before("place", order=60)
@P.plain("destination")
def _shelf_clearance(ctx):
    """Fridge/freezer shelves: an item must fit the height between its shelf and the one above (inner_size_m is the
    whole compartment so other domains' packing sees the real interior)."""
    dest = ctx.args.get("destination")
    w = ctx.w
    rec = w.s["entities"].get(dest) or {}
    clear = rec.get("shelf_clearance_m")
    if not clear or rec.get("ac_kind") not in ("fridge", "freezer") or (ctx.args.get("relation") or "in") != "in":
        return
    shelf = int(ctx.args.get("shelf") or 0)
    if not 0 <= shelf < len(clear):
        return
    item = w.s["entities"].get(ctx.args.get("target")) or {}
    size = sorted(C.size3(item))
    orient = ctx.args.get("orientation") or item.get("orientation") or "upright"
    height = size[0] if orient in ("on_side", "flat") else C.size3(item)[2]
    if height > C.num(clear[shelf]) + 0.005:
        if ctx.realistic:      # it does not fit; nobody measured the gap
            raise ActionError("Too tall for that shelf; lay it on its side or use another shelf", "precondition")
        raise ActionError("Too tall for that shelf ({:.0f} cm of clearance); lay it on its side or use another shelf".format(
            C.num(clear[shelf]) * 100), "precondition")


@after("place", order=79)
def _protrusion(ctx, result):
    """Items deeper than a door bin, or past the front of a shelf, stick out and stop the door sealing."""
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    parent = w.s["entities"].get(rec.get("location")) or {}
    unit = next((p for p in [rec.get("location")] + C.parent_chain(w.s, rec.get("location") or "") if is_cold(w.s["entities"].get(p))), None) \
        if rec.get("location") in w.s["entities"] else None
    if unit is None:
        if rec.get("protrudes"):
            w.mut(target)["protrudes"] = False
        return
    s = C.size3(rec)
    orient = ctx.args.get("orientation") or rec.get("orientation")
    depth = min(s[0], s[1]) if orient not in ("flat", "on_side") else min(s[0], s[2])
    sticks = False
    if parent.get("fridge_zone") == "door" or parent.get("ac_kind") == "door_bin":
        inner = parent.get("inner_size_m") or [0.58, 0.11, 0.2]
        sticks = depth > C.num(inner[1], 0.11) + 0.02
    elif parent is w.s["entities"].get(unit):
        inner = parent.get("inner_size_m") or [0.7, 0.6, 0.3]
        spos = rec.get("spos")
        if spos is not None:
            sticks = C.num(spos[1]) + depth > C.num(inner[1], 0.6) + 0.02
    if bool(rec.get("protrudes")) != sticks:
        w.mut(target)["protrudes"] = sticks


@after("pick_up", order=79)
def _lifted_out(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("protrudes"):
        w.mut(target)["protrudes"] = False


@hook("setting_changed")
def _gasket(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.s["entities"].get(eid) or {}
    if rec.get("ac_kind") == "gasket" and name == "seated":
        unit = rec.get("location")
        urec = w.s["entities"].get(unit) or {}
        if is_cold(urec) and not urec.get("open"):
            obs = obstructions(w.s, unit)
            C.set_if(w, unit, ajar=bool(obs), sealed=not obs)


# ----------------------------------------------------------------------------------------------
# defrost verb

def _held(ctx, kinds=(), words=()):
    for h in ctx.w.held_items():
        rec = ctx.w.ent(h)
        text = (C.label(rec, "") + " " + " ".join(C.type_names(rec))).lower()
        if rec.get("tool_kind") in kinds or any(wd in text for wd in words):
            return h
    return None


def _defrost_seconds(ctx):
    return ctx.data.get("seconds", 10.0)


@action("defrost", params={"target": "entity",
                           "method": {"type": "str", "default": "passive",
                                      "choices": ["passive", "hot_water", "fan", "scrape", "hair_dryer", "ice_pick", "knife"]}},
        duration=_defrost_seconds, energy=0.01, category="physical",
        doc="Defrost a manual-defrost freezer: it must be switched off (or unplugged) with the door/lid open and the food moved "
            "to coolers first. method passive (start melting; put trays/towels in place), hot_water (bowls of hot water "
            "inside speed it up), fan, scrape (hold a plastic scraper; removes frost once it has softened ~20 min), hair_dryer "
            "(electrical hazard near meltwater), ice_pick/knife (punctures the evaporator). Call again to scrape.")
def defrost(ctx):
    ctx.obj(ctx.args["target"], touch=True)
    # kernel access refusals (visibility, reach, permission) above pass unchanged; this domain's own reasons below
    # are generic for an unidentified (look-alike or unfamiliar) target, so they cannot name what it is
    with P.plain_refusals(ctx, ctx.args.get("target")):
        return _defrost_body(ctx)


def _defrost_body(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rec = ctx.obj(target, touch=True)
    require(rec.get("ac_kind") == "freezer", "Defrost a freezer (the microwave has a defrost program instead)", "argument")
    require(not powered(state, target), "Switch the freezer off (or unplug it) before defrosting", "safety")
    require(rec.get("open"), "Open the door/lid so the frost can melt", "precondition")
    method = ctx.args.get("method") or "passive"
    d = dict(rec.get("_defrost") or {})
    frost = C.num(rec.get("frost_mm"))
    rid = C.room_of(state, target)
    softened = d.get("active") and C.now(w) - C.num(d.get("start_t"), C.now(w)) >= 1200.0
    if not d.get("active"):
        d.update(active=True, start_t=C.now(w), method=method, frost_at_start=round(frost, 2),
                 needed=bool(frost >= 6.0 and not (rec.get("fridge") or {}).get("frost_free", True)))
        food = [c for c in C.descendants(state, target) if C.is_food(state["entities"][c])]
        d["food_left_inside"] = len(food)
        if not d["needed"]:
            C.log(w, "defrost_not_needed", unit=target, frost_mm=round(frost, 1))
    if method == "hot_water":
        hot = [c for c in C.descendants(state, target) if C.num(state["entities"][c].get("liquid_temp_c"), -99) > 40.0]
        require(hot, "Put bowls of hot water inside the freezer first", "precondition")
        d["method"] = "hot_water"
    elif method == "fan":
        d["method"] = "fan"
    elif method == "scrape":
        tool = _held(ctx, kinds=("scraper", "spatula"), words=("scraper", "spatula"))
        require(tool is not None, "Hold a plastic scraper or spatula", "capability")
        trec = w.ent(tool)
        if C.is_metal(trec) or "sharp" in (trec.get("hazards") or []):
            method = "knife"
        else:
            removed = frost * (0.5 if softened else 0.12)
            water = removed * C.num((rec.get("fridge") or {}).get("interior_m2"), 1.5) * 0.4
            left = _contain_meltwater(w, target, water, rid, d)
            if left > 0:
                d["floor_l"] = C.rnd(C.num(d.get("floor_l")) + left, 3)
                _floor_puddle(w, target, rid, d)
            w.mut(target)["frost_mm"] = C.rnd(frost - removed, 3)
            hf = C.hidden(rec, "_fridge")
            if hf.get("frost_ridge"):
                C.mut_hidden(w, target, "_fridge")[1]["frost_ridge"] = False
            ctx.data["seconds"] = 180.0
    if method == "hair_dryer":
        tool = _held(ctx, kinds=("hair_dryer",), words=("hair dryer", "hair_dryer", "hairdryer"))
        require(tool is not None, "Hold a hair dryer", "capability")
        pooled = C.num(d.get("floor_l")) > 0.1 or any(C.num(state["entities"][c].get("liquid_l")) > 0.1 and state["entities"][c].get("location") == target
                                                     for c in C.descendants(state, target))
        if pooled or frost > 0:
            C.harm(w, "hair_dryer_near_meltwater", stat="electrical_hazard", once="dryer:" + target, unit=target)
        removed = min(frost, 3.0)
        w.mut(target)["frost_mm"] = C.rnd(frost - removed, 3)
        water = removed * C.num((rec.get("fridge") or {}).get("interior_m2"), 1.5) * 0.4
        left = _contain_meltwater(w, target, water, rid, d)
        if left > 0:
            d["floor_l"] = C.rnd(C.num(d.get("floor_l")) + left, 3)
            _floor_puddle(w, target, rid, d)
        ctx.data["seconds"] = 300.0
    if method in ("ice_pick", "knife"):
        tool = _held(ctx, kinds=("ice_pick", "chef_knife", "paring_knife", "knife"), words=("ice pick", "knife"))
        require(tool is not None, "Hold the tool", "capability")
        if not (softened and frost >= 8.0):
            m, hf = C.mut_hidden(w, target, "_fridge")
            hf["evaporator_punctured"] = True
            m["broken"] = True
            C.harm(w, "evaporator_punctured", stat="damage", once="puncture:" + target, unit=target)
            C.add_odor(w, rid, "refrigerant", 0.5)
        else:
            C.harm(w, "sharp_tool_on_evaporator", stat="safety_violations", once="pick:" + target, unit=target)
        removed = frost * 0.5
        w.mut(target)["frost_mm"] = C.rnd(frost - removed, 3)
        ctx.data["seconds"] = 120.0
    m = w.mut(target)
    m["_defrost"] = d
    frost = round(C.num(w.ent(target).get("frost_mm")), 1)
    if ctx.realistic:
        frost = P.frost_estimate(ctx, target, frost)      # judged by eye, to the millimetre
    return json.dumps({"defrosting": target, "method": d.get("method"), "frost_mm": frost})


@after("switch_on", order=64)
def _freezer_restart(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") == "freezer" and C.hidden(rec, "_defrost").get("active"):
        m, d = C.mut_hidden(w, target, "_defrost")
        d["active"] = False
        d["restarted_t"] = C.now(w)
        d["frost_at_restart"] = C.rnd(C.num(rec.get("frost_mm")), 2)
        C.log(w, "freezer_restarted", unit=target)


@after("switch_off", order=64)
def _cold_switched_off(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if is_cold(rec):
        m, hf = C.mut_hidden(w, target, "_fridge")
        hf["switched_off_t"] = C.now(w)
