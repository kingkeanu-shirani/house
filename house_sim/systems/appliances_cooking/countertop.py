"""Countertop appliances (fidelity P-approx / S): microwave, toaster, electric kettle, blender and food processor (`blend`),
stand mixer, waffle maker, electric griddle, slow cooker, rice cooker and electric pressure cooker.

Microwave: output_w x power/10 is absorbed by water-bearing loads (efficiency 0.3 + load kg, max 0.7) - surface gets
70 %, the centre 30 % (cold spots until stirred/stood). Metal, foil and metal trim arc (item damage after ~3 s,
waveguide after ~8 s, fire with paper after ~20 s); airtight containers (contents ~75 C) and eggs in the shell (~95 C) burst; water in a
smooth cup superheats when held at boiling (it erupts when picked up); running empty damages the magnetron.
Toaster: shade 1-7 -> 85-235 s at ~255 C effective; slices thicker than the 3.5 cm slot jam (free them unplugged with a
non-metal tool); buttered bread drips and flares; a full crumb tray ignites. Kettle: 2.2 kW into the water, auto-off at
the target, dry-boil cut-out, spitting above MAX wets the base, a wet base or wet kettle on it is an electrical hazard.
Slow cooker: LOW ~170 W / HIGH ~280 W into a 5.7 L stoneware insert (raw food passes 4-57 C slowly - frozen meat on
LOW exceeds the 2 h danger-zone limit); lid lifts cost heat; a fridge-cold insert into a hot base cracks.
Pressure cooker: sealed (lid closed, valve sealing, ring fitted) the boiling point rises to ~118 C at ~70 kPa; the lid
interlock holds while the float valve is up; < 0.25 L liquid gives a BURN error; quick release vents a steam jet that
damages cabinets overhead and scalds people close by.
"""
import json
import math

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.phys import thermal as kthermal
from ...kernel.plugins import after, before, hook
from . import common as C
from . import percept as P
from . import stove as S

MW_SHARE_SURFACE = 0.7


# ----------------------------------------------------------------------------------------------
# microwave

SEALED_BURST_C = 75.0     # heated headspace air + vapour pressure (~90 kPa gauge at 90 C) blows an airtight clip lid well before boiling

def mw_loads(state, mid):
    """(food ids, liquid containers, metal items, sealed containers, eggs in shell) inside a microwave."""
    foods, liquids, metals, sealed, eggs = [], [], [], [], []
    for c in C.descendants(state, mid):
        rec = state["entities"][c]
        ok, why = C.microwave_verdict(rec)
        if not ok and not C.is_food(rec):
            metals.append(c)
        if C.is_food(rec):
            foods.append(c)
            f = rec.get("food") or {}
            if (C.food_cat(rec) == "egg" or "egg" in C.food_kind(rec)) and "shell" in (f.get("parts") or []) and not f.get("cracked"):
                eggs.append(c)
        elif C.num(rec.get("liquid_l")) > 0.01:
            liquids.append(c)
        if (rec.get("sealed") or (rec.get("airtight") and rec.get("openable") and not rec.get("open"))) and not rec.get("vented"):
            if C.num(rec.get("liquid_l")) > 0 or any(C.is_food(state["entities"][x]) for x in C.children(state, c)):
                sealed.append(c)
    return foods, liquids, metals, sealed, eggs


def step_microwave(w, mid, dt):
    state = w.s
    rec = state["entities"][mid]
    machine = rec.get("appliance") or {}
    hm = dict(rec.get("_mw") or {})
    rid = C.room_of(state, mid)
    if machine.get("state") != "running":
        if hm.get("arcing"):
            hm["arcing"] = False
            w.mut(mid)["_mw"] = hm
            C.set_if(w, mid, arcing=False)
        _superheat_decay(w, mid, dt)
        return
    power = C.num((rec.get("settings") or {}).get("power"), 10) / 10.0
    if machine.get("program") == "defrost":
        power = min(power, 0.3)
    out_w = C.num((rec.get("microwave") or {}).get("output_w"), 1000.0) * power
    foods, liquids, metals, sealed, eggs = mw_loads(state, mid)
    # ---- metal: arcing
    if metals:
        hm["arc_s"] = C.rnd(C.num(hm.get("arc_s")) + dt, 1)
        if not hm.get("arcing"):
            C.visual_event(w, "arcing", source=mid, room=rid, size_m=0.1)     # sparks flash behind the door window
        hm["arcing"] = True
        C.set_if(w, mid, arcing=True)
        C.counter(w, "arcing_s", round(dt, 1))
        if C.num(hm["arc_s"]) >= 3.0:
            for item in metals:
                irec = state["entities"][item]
                if not irec.get("arc_damaged"):
                    m = w.mut(item)
                    m["arc_damaged"] = True
                    if irec.get("metal_trim"):
                        m["trim_burned"] = True
                    C.harm(w, "arcing_damaged_item", stat="damage", once="arcitem:" + item, item=item)
        if C.num(hm["arc_s"]) >= 8.0:
            C.harm(w, "arcing_damaged_microwave", stat="damage", once="arcmw:" + mid, microwave=mid)
            C.add_damage(w, mid, "waveguide_cover_burned", 0.6)
        paper = [c for c in C.descendants(state, mid) if C.mat(state["entities"][c]) in ("paper", "cardboard")]
        if C.num(hm["arc_s"]) >= 20.0 and paper:
            from .stove import ignite_item
            ignite_item(w, paper[0], rid, cause="microwave_arcing")
    # ---- absorbing loads
    masses = []
    for f in foods:
        masses.append((f, "food", C.mass_kg(state["entities"][f])))
    for l in liquids:
        lrec = state["entities"][l]
        masses.append((l, "liquid", C.num(lrec.get("liquid_l")) * C.liquid_props(lrec.get("liquid"))[0]))
    total = sum(m for _, _, m in masses)
    if total < 0.02:
        hm["empty_s"] = C.rnd(C.num(hm.get("empty_s")) + dt, 1)
        if hm["empty_s"] > 30.0:
            C.harm(w, "microwave_run_empty", stat="damage", once="mwempty:" + mid, microwave=mid)
        w.mut(mid)["_mw"] = hm
        return
    eff = min(0.7, 0.3 + total)
    absorbed = out_w * eff * dt
    for eid, kind, mass in masses:
        energy = absorbed * mass / total
        if kind == "liquid":
            _mw_liquid(w, eid, energy, dt, rid)
        else:
            _mw_food(w, eid, energy, dt, rid, mid)
    # ---- pressure in sealed containers, eggs in shell
    for c in sealed:
        crec = state["entities"][c]
        temp = max([C.num(crec.get("liquid_temp_c"), C.num(crec.get("temperature_c")))] +
                   [C.num(state["entities"][x].get("temperature_c")) for x in C.children(state, c)])
        if temp >= SEALED_BURST_C and not crec.get("burst"):
            _burst(w, c, mid, rid, "sealed_container_burst")
    for e in eggs:
        erec = state["entities"][e]
        core = C.num(erec.get("_core_c"), C.num((erec.get("_cook") or {}).get("core_c"), C.num(erec.get("temperature_c"))))
        if max(core, C.num(erec.get("temperature_c"))) >= 95.0 and not erec.get("burst"):
            _burst(w, e, mid, rid, "egg_exploded")
            C.harm(w, "egg_exploded_in_microwave", stat="safety_violations", once="egg:" + e, egg=e)
    w.mut(mid)["_mw"] = hm


def _mw_liquid(w, cid, energy, dt, rid):
    state = w.s
    rec = state["entities"][cid]
    liq = C.num(rec.get("liquid_l"))
    dens, cp, boil, foam, smoke = C.liquid_props(rec.get("liquid"))
    mass = liq * dens
    t = C.num(rec.get("liquid_temp_c"), C.num(rec.get("temperature_c"), 21.0))
    cap = mass * cp + C.num(rec.get("mass_kg"), 0.3) * 300.0
    t_new = t + energy / max(1.0, cap)
    m = w.mut(cid)
    hm = dict(m.get("_mw") or {})
    if boil is not None and t_new >= boil:
        excess = (t_new - boil) * cap
        smooth = C.mat(rec) in ("glass", "ceramic", "porcelain") and not any(True for _ in C.children(state, cid))
        if smooth and rec.get("liquid") in ("water", None) and not hm.get("nucleated"):
            sh = min(8.0, C.num(hm.get("superheat_c")) + excess / max(1.0, cap))
            hm["superheat_c"] = C.rnd(sh, 2)
            excess = max(0.0, excess - sh * cap)
        boiled = excess / C.LATENT_J_PER_KG
        m["liquid_l"] = C.rnd(max(0.0, liq - boiled / dens), 5)
        if foam >= 1.0 and liq / max(0.05, C.num(rec.get("capacity_l"), 0.35)) > 0.6:
            spill = min(C.num(m["liquid_l"]), 0.05)
            m["liquid_l"] = C.rnd(C.num(m["liquid_l"]) - spill, 5)
            hm["boiled_over"] = True
            w.stat("spills")
            C.counter(w, "boil_overs")
        t_new = boil
    m["liquid_temp_c"] = C.rnd(t_new, 2)
    m["temperature_c"] = C.rnd(max(C.num(rec.get("temperature_c"), 21.0), t_new - 5.0), 2)
    m["_mw"] = hm


def _mw_food(w, fid, energy, dt, rid, mid):
    """Microwaves deposit energy in the outer 2-3 cm: the surface warms ~1.25x the average, the centre ~0.7x (cold
    spots) until stirring or standing time evens it out."""
    state = w.s
    rec = state["entities"][fid]
    mass = C.mass_kg(rec)
    cp = 3500.0
    surf = C.num(rec.get("temperature_c"), 21.0)
    avg = energy / (mass * cp)
    ice = C.num((rec.get("food") or {}).get("ice"))
    if ice > 0.0 and surf <= 0.0:
        avg *= 0.3
    d_surf, d_core = 1.25 * avg, 0.7 * avg
    new_surf = surf + d_surf
    m = w.mut(fid)
    hc = dict(m.get("_cook") or {})
    if new_surf >= 100.0:
        excess = (new_surf - 100.0) * mass * cp
        new_surf = 100.0
        hc["mw_boil_s"] = C.rnd(C.num(hc.get("mw_boil_s")) + dt, 1)
        f = dict(m.get("food") or {})
        lost_g = excess / C.LATENT_J_PER_KG * 1000.0
        if f.get("qty_g") is not None:
            f["qty_g"] = C.rnd(max(0.0, C.num(f["qty_g"]) - lost_g), 2)
            m["food"] = f
        if hc["mw_boil_s"] > 60.0:
            from ..core import add_soil
            add_soil(w, mid, "food", min(0.3, 0.003 * dt))
            hc["spattered"] = True
        if hc["mw_boil_s"] > 300.0 and not hc.get("dried_out"):
            hc["dried_out"] = True
            hc["burnt"] = True
            C.add_smoke(w, rid, 1.5, source=fid)
            C.log(w, "food_dried_out", food=fid)
    m["temperature_c"] = C.rnd(new_surf, 2)
    core0 = C.num(m.get("_core_c"), C.num(hc.get("core_c"), surf))
    core = min(100.0, core0 + d_core)
    if "_core_c" in m or C.food_safety_loaded():
        if (m.get("food") or {}).get("phase") == "liquid":
            # convection evens a heated liquid out within a minute or two (the food domain's slab core has no convection)
            core = new_surf + (core - new_surf) * math.exp(-dt / 90.0)
        m["_core_c"] = C.rnd(core, 2)
    hc["core_c"] = C.rnd(core, 3)
    hc["peak_core_c"] = max(C.num(hc.get("peak_core_c"), core), core)
    hc["active"] = True
    hc["mw_s"] = C.rnd(C.num(hc.get("mw_s")) + dt, 1)
    m["_cook"] = hc


def _burst(w, cid, mid, rid, kind):
    state = w.s
    m = w.mut(cid)
    m["burst"] = True
    if m.get("openable"):
        m["open"] = True
    if C.num(m.get("liquid_l")) > 0:
        m["liquid_l"] = C.rnd(C.num(m["liquid_l"]) * 0.6, 4)
    if C.is_glass(m):
        C.break_item(w, cid, "burst_in_microwave")
    from ..core import add_soil
    add_soil(w, mid, "food", 0.5)
    add_soil(w, mid, "sticky", 0.2)
    w.stat("spills")
    C.counter(w, "microwave_bursts")
    C.log(w, kind, item=cid, microwave=mid)
    w.emit("bang", room=rid, source=mid)


def _superheat_decay(w, mid, dt):
    state = w.s
    for c in C.descendants(state, mid):
        rec = state["entities"][c]
        hm = rec.get("_mw") or {}
        if C.num(hm.get("superheat_c")) > 0:
            m, h = C.mut_hidden(w, c, "_mw")
            h["superheat_c"] = C.rnd(C.num(h["superheat_c"]) * math.exp(-dt / 300.0), 3)


for _t in ("microwave",):
    @hook("cycle_check:" + _t)
    @P.plain_check
    def _mw_check(w, eid, *_):
        entity = w.ent(eid)
        if C.num((entity.get("settings") or {}).get("time_s")) <= 0:
            return "Enter a cook time first"
        return None

    @hook("cycle_start:" + _t)
    def _mw_start(w, eid, *_):
        entity = w.ent(eid)
        entity["appliance"]["remaining_s"] = C.num((entity.get("settings") or {}).get("time_s"), 60)
        hm = dict(entity.get("_mw") or {})
        hm.update(arc_s=0.0, empty_s=0.0, started_s=C.now(w))
        entity["_mw"] = hm
        entity["display"] = "{}:{:02d}".format(int(entity["appliance"]["remaining_s"] // 60), int(entity["appliance"]["remaining_s"] % 60))

    @hook("cycle_tick:" + _t)
    def _mw_tick(w, eid, step):
        rec = w.ent(eid)
        rem = C.num((rec.get("appliance") or {}).get("remaining_s"))
        C.set_if(w, eid, display="{}:{:02d}".format(int(rem // 60), int(rem % 60)))

    @hook("cycle_complete:" + _t)
    def _mw_done(w, eid, *_):
        entity = w.ent(eid)
        m = w.mut(eid)
        m["display"] = "End"
        m["arcing"] = False
        hm = dict(m.get("_mw") or {})
        hm["beep_until"] = C.now(w) + 20.0
        hm["arcing"] = False
        C.notice(w, "appliance_done", eid, "microwave_done")
        m["_mw"] = hm
        m["settings"]["time_s"] = 0


@after("open", order=65)
def _door_pauses(ctx, result):
    """Opening a microwave/dishwasher-style door mid-cycle pauses it (door switch)."""
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") in ("microwave",) and (rec.get("appliance") or {}).get("state") == "running":
        w.mut(target)["appliance"]["state"] = "paused"
        C.log(w, "paused_by_door", appliance=target)


@after("pick_up", order=65)
def _picked_up_hot_liquid(ctx, result):
    """Superheated water erupts when the cup is disturbed (picked up)."""
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    hm = rec.get("_mw") or {}
    if C.num(hm.get("superheat_c")) >= 2.0:
        m = w.mut(target)
        m["liquid_l"] = C.rnd(C.num(m.get("liquid_l")) * 0.55, 4)
        h = dict(m.get("_mw") or {})
        h["superheat_c"] = 0.0
        h["erupted"] = True
        m["_mw"] = h
        w.stat("spills")
        C.harm(w, "superheated_water_erupted", stat="safety_violations", once="superheat:" + target, item=target)
        hurt = C.scalded(w.s, C.people_near(w.s, target, cells=1), C.num(rec.get("liquid_temp_c"), 100.0))
        if hurt:
            C.harm(w, "scald_from_superheated_water", stat="burns", once="superheatburn:" + target, people=hurt)


# ----------------------------------------------------------------------------------------------
# toaster

def toaster_fit(toaster, item):
    t = toaster.get("toaster") or {}
    slot_w = C.num(t.get("slot_w_m"), 0.035)
    s = sorted(C.size3(item))
    thickness, width, height = s[0], s[1], s[2]
    if thickness > slot_w + 0.002:
        return "too_thick"
    if width > C.num(t.get("slot_len_m"), 0.14) + 0.01:
        return "too_wide"
    return None


@before("place", order=72)
@P.plain("destination")
def _toaster_slots(ctx):
    w = ctx.w
    dest = ctx.args.get("destination")
    rec = w.s["entities"].get(dest) or {}
    if rec.get("ac_kind") != "toaster":
        return
    slots = int(C.num((rec.get("toaster") or {}).get("slots"), 2))
    inside = [c for c in C.children(w.s, dest, "in")]
    require(len(inside) < slots, "All {} toaster slots are full".format(slots), "precondition")
    require((rec.get("appliance") or {}).get("state") != "running", "The toaster is running", "precondition")


@after("place", order=72)
def _into_toaster(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    parent = w.s["entities"].get(rec.get("location")) or {}
    if parent.get("ac_kind") != "toaster":
        if rec.get("stuck") and parent.get("ac_kind") != "toaster":
            w.mut(target)["stuck"] = False
        return
    problem = toaster_fit(parent, rec)
    if problem == "too_thick":
        w.mut(target)["stuck"] = True
        C.log(w, "toaster_jam", item=target, toaster=rec.get("location"))
    if C.is_metal(rec) and not C.is_food(rec) and C.powered_state(w.s, rec.get("location")):
        C.harm(w, "metal_in_live_toaster", stat="electrical_hazard", once="toastmetal:" + target, item=target)


@before("pick_up", order=72)
def _stuck_in_toaster(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("stuck"):
        with P.plain_refusals(ctx, target, rec.get("location")):
            raise ActionError("It is jammed in the slot: unplug the toaster and free it with a non-metal tool (unload ... tool=)", "precondition")


def step_toaster(w, tid, dt):
    state = w.s
    rec = state["entities"][tid]
    ht = dict(rec.get("_toaster") or {})
    running = (rec.get("appliance") or {}).get("state") == "running" and C.powered_state(state, tid)
    mode = (rec.get("settings") or {}).get("mode", "toast")
    target = {"toast": 235.0, "bagel": 235.0, "defrost": 205.0, "reheat": 150.0}.get(mode, 235.0) if running else 21.0
    eff = C.num(ht.get("element_eff_c"), 21.0)
    eff = target + (eff - target) * math.exp(-dt / 12.0)
    ht["element_eff_c"] = C.rnd(eff, 2)
    rid = C.room_of(state, tid)
    if running:
        for c in C.children(state, tid, "in"):
            crec = state["entities"][c]
            f = crec.get("food") or {}
            layers = [str(l.get("kind") if isinstance(l, dict) else l) for l in (f.get("layers") or [])]
            if crec.get("buttered") or any("butter" in l for l in layers):
                C.add_smoke(w, rid, 0.02 * dt, source=c)
                if C.num((rec.get("settings") or {}).get("shade"), 4) >= 5:
                    from .stove import ignite_item
                    ignite_item(w, c, rid, cause="buttered_bread_in_toaster")
            if crec.get("stuck"):
                m, hc = C.mut_hidden(w, c, "_cook")
                hc["brown_top"] = C.rnd(C.num(hc.get("brown_top")) + dt / 120.0, 4)
                hc["brown_bottom"] = C.rnd(C.num(hc.get("brown_bottom")) + dt / 120.0, 4)
        if C.num((rec.get("crumb_tray") or {}).get("level")) >= 0.9 and not ht.get("crumbs_lit"):
            ht["crumbs_lit"] = True
            C.harm(w, "toaster_crumb_fire", stat="fire", once="crumbs:" + tid, toaster=tid)
            C.add_smoke(w, rid, 3.0, source=tid)
    m = w.mut(tid)
    m["_toaster"] = ht
    m["internal_temp_c"] = C.rnd(max(21.0, eff * 0.45), 2) if eff > 35.0 else None
    if m.get("internal_temp_c") is None:
        m.pop("internal_temp_c", None)


@hook("cycle_check:toaster")
@P.plain_check
def _toaster_check(w, eid, *_):
    entity = w.ent(eid)
    if entity.get("lever") == "down":
        return "It is already toasting"
    return None


@hook("cycle_start:toaster")
def _toaster_start(w, eid, *_):
    entity = w.ent(eid)
    shade = C.num((entity.get("settings") or {}).get("shade"), 4)
    mode = (entity.get("settings") or {}).get("mode", "toast")
    seconds = 50.0 + 22.0 * shade + (40.0 if mode == "defrost" else 0.0)
    entity["appliance"]["remaining_s"] = seconds
    entity["lever"] = "down"
    for c in C.children(w.s, eid, "in"):
        if w.s["entities"][c].get("stuck"):
            entity["appliance"]["remaining_s"] = seconds * 1.6


@hook("cycle_complete:toaster")
def _toaster_done(w, eid, *_):
    entity = w.ent(eid)
    m = w.mut(eid)
    m["lever"] = "up"
    ht = dict(m.get("_toaster") or {})
    ht["pop_t"] = C.now(w)
    m["_toaster"] = ht
    m["appliance"]["state"] = "idle"
    C.log(w, "toaster_pop", toaster=eid)
    C.notice(w, "appliance_done", eid, "toaster_pop")


# ----------------------------------------------------------------------------------------------
# kettle

def kettle_base_of(state, kid):
    rec = state["entities"].get(kid) or {}
    parent = state["entities"].get(rec.get("location")) or {}
    if parent.get("ac_kind") == "kettle_base" and rec.get("relation") == "on":
        return rec.get("location")
    return None


@before("switch_on", order=60)
def _kettle_seated(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") != "kettle":
        return
    base = kettle_base_of(w.s, target)
    with P.plain_refusals(ctx, target, base):          # an unfamiliar kettle: a reason that does not name it
        require(base is not None, "Seat the kettle on its base first", "precondition")
        require(C.powered_state(w.s, base), "The kettle base has no power (plug it in)", "resource")


@after("switch_on", order=60)
def _kettle_on(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") == "kettle":
        m, hk = C.mut_hidden(w, target, "_kettle")
        hk["on_s"] = 0.0
        hk["started_c"] = C.rnd(C.num(rec.get("liquid_temp_c"), C.num(rec.get("temperature_c"), 21.0)), 1)
        hk["started_l"] = C.rnd(C.num(rec.get("liquid_l")), 3)
        if C.num(rec.get("liquid_l")) > C.num(rec.get("max_l"), 1.7) + 1e-6:
            hk["overfilled"] = True


def step_kettle(w, kid, dt):
    state = w.s
    rec = state["entities"][kid]
    rid = C.room_of(state, kid)
    room_c = C.room_temp(state, rid)
    base = kettle_base_of(state, kid)
    hk = dict(rec.get("_kettle") or {})
    if rec.get("on") and (base is None or not C.powered_state(state, base)):
        C.set_if(w, kid, on=False)
        return
    if not rec.get("on"):
        return
    k = rec.get("kettle") or {}
    watts = C.num(k.get("power_w"), 2200.0) * 0.95
    liq = C.num(rec.get("liquid_l"))
    hk["on_s"] = C.rnd(C.num(hk.get("on_s")) + dt, 1)
    if liq < 0.05:
        hk["dry_s"] = C.rnd(C.num(hk.get("dry_s")) + dt, 1)
        if hk["dry_s"] >= 15.0:
            m = w.mut(kid)
            m["on"] = False
            m["_kettle"] = hk
            C.harm(w, "kettle_dry_boil", stat="damage", once="dryboil:" + kid, kettle=kid)
            C.add_odor(w, rid, "hot_plastic", 0.4)
            C.log(w, "kettle_dry_boil_cutout", kettle=kid)
        else:
            w.mut(kid)["_kettle"] = hk
        return
    if liq < C.num(rec.get("min_l"), 0.5) - 1e-6:
        hk["below_min"] = True
    S._step_vessel(w, kid, base, watts, dt, room_c, rid, 1.0, [], ua_factor=0.6, scorch=False, foams=False)
    rec = state["entities"][kid]
    target = (rec.get("settings") or {}).get("target", "boil")
    temp = C.num(rec.get("temperature_c"), 21.0)
    goal = 99.5 if target == "boil" else C.num(target, 100.0) - 0.5
    if (hk.get("overfilled") or C.num(rec.get("liquid_l")) > C.num(rec.get("max_l"), 1.7) + 1e-6) and temp >= 99.0 and not hk.get("spat"):
        hk["spat"] = True
        spill = min(C.num(rec.get("liquid_l")) * 0.1, 0.12)
        m = w.mut(kid)
        m["liquid_l"] = C.rnd(C.num(m["liquid_l"]) - spill, 4)
        w.stat("spills")
        C.log(w, "kettle_spat", kettle=kid, litres=round(spill, 3))
        if base:
            bm = w.mut(base)
            bm["wetness"] = 1.0
            C.harm(w, "boiling_water_on_kettle_base", stat="electrical_hazard", once="kbase:" + base, base=base)
        hurt = C.scalded(state, C.people_near(state, kid, cells=1), temp)
        if hurt:
            C.harm(w, "kettle_spit_scald", stat="burns", once="kscald:" + kid, people=hurt)
    if temp >= goal:
        m = w.mut(kid)
        m["on"] = False
        hk["done_t"] = C.now(w)
        hk["reached_c"] = C.rnd(temp, 1)
        hk["click_until"] = C.now(w) + 5.0
        C.log(w, "kettle_auto_off", kettle=kid, temp_c=round(temp, 1))
        C.notice(w, "appliance_done", kid, "kettle_click")
    w.mut(kid)["_kettle"] = hk


@after("place", order=73)
def _kettle_on_base(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") == "kettle":
        base = kettle_base_of(w.s, target)
        if base and C.num(rec.get("wetness")) > 0.3 and C.powered_state(w.s, base):
            w.mut(base)["wetness"] = max(C.num(w.ent(base).get("wetness")), 0.5)
            C.harm(w, "wet_kettle_on_live_base", stat="electrical_hazard", once="wetbase:" + base, base=base)
    if rec.get("ac_kind") == "kettle_base" or (rec.get("ac_kind") == "kettle" and kettle_base_of(w.s, target)):
        base = target if rec.get("ac_kind") == "kettle_base" else kettle_base_of(w.s, target)
        edge = C.front_edge_distance(w.s, base)
        C.set_if(w, base, cord_over_edge=bool(edge is not None and edge < 0.04))


# ----------------------------------------------------------------------------------------------
# blender and food processor

def _resolve_blender(state, target):
    rec = state["entities"][target]
    kind = rec.get("ac_kind")
    if kind == "blender":
        jar = next((c for c in C.children(state, target, "on") if state["entities"][c].get("ac_kind") == "blender_jar"), None)
        return "blender", target, jar
    if kind == "blender_jar":
        parent = state["entities"].get(rec.get("location")) or {}
        return "blender", (rec.get("location") if parent.get("ac_kind") == "blender" else None), target
    if kind == "food_processor":
        return "processor", target, None
    if rec.get("part_type") == "processor_bowl":
        parent = state["entities"].get(rec.get("location")) or {}
        return "processor", (rec.get("location") if parent.get("ac_kind") == "food_processor" else None), target
    return None, None, None


def _blend_seconds(ctx):
    pulses = int(ctx.args.get("pulses") or 0)
    return (pulses * 1.5 if pulses else float(ctx.args.get("seconds") or 30.0)) + 2.0


@action("blend", params={"target": "entity", "seconds": {"type": "number", "default": 30.0, "min": 1.0, "max": 300.0},
                         "speed": {"type": "str", "default": "high", "choices": ["low", "medium", "high", "pulse"]},
                         "pulses": {"type": "int", "default": 0, "min": 0, "max": 60}},
        duration=_blend_seconds, energy=0.01, noise=0.6, category="physical", timing="commanded",
        doc="Run a blender (target the base or its seated jar) or a food processor (base or bowl) for `seconds` at `speed`, or "
            "`pulses` one-second pulses. Blender: jar locked on the base, lid on, liquids first, under MAX (hot liquids: half "
            "full, vent open). Food processor: bowl, blade and lid locked (interlock); pulse to chop, run to puree.")
def blend(ctx):
    ctx.obj(ctx.args["target"], touch=True)
    # kernel access refusals (visibility, reach, permission) above pass unchanged; this domain's own reasons below
    # are generic for an unidentified (look-alike or unfamiliar) target, so they cannot name what it is
    with P.plain_refusals(ctx, ctx.args.get("target")):
        return _blend_body(ctx)


def _blend_body(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    ctx.obj(target, touch=True)
    family, base, vessel = _resolve_blender(state, target)
    require(family is not None, "That is not a blender or food processor", "argument")
    pulses = int(ctx.args.get("pulses") or 0)
    seconds = pulses * 1.0 if pulses else float(ctx.args.get("seconds") or 30.0)
    speed = "pulse" if pulses else (ctx.args.get("speed") or "high")
    rid = C.room_of(state, target)
    C.quiet_check(ctx, "blender" if family == "blender" else "food_processor")
    if family == "blender":
        require(base is not None and vessel is not None, "The jar is not locked onto the blender base", "precondition")
        require(C.powered_state(state, base), "The blender has no power", "resource")
        return _run_blender(w, base, vessel, seconds, speed, pulses, rid)
    require(base is not None, "Fit the bowl onto the food processor base first", "precondition")
    return _run_processor(w, base, seconds, speed, pulses, rid)


def _jar_load(state, jar):
    rec = state["entities"][jar]
    liquid_l = C.num(rec.get("liquid_l"))
    solids_l = 0.0
    frozen = 0.0
    temp = C.num(rec.get("liquid_temp_c"), C.num(rec.get("temperature_c"), 21.0))
    for c in C.children(state, jar):
        crec = state["entities"][c]
        if not C.is_food(crec):
            continue
        vol = C.num(crec.get("volume_l"), C.mass_kg(crec))
        f = crec.get("food") or {}
        if f.get("phase") == "liquid":
            liquid_l += vol
        else:
            solids_l += vol
        frozen += vol * C.num(f.get("ice"))
        temp = max(temp, C.num(crec.get("temperature_c"), 21.0))
    return liquid_l, solids_l, frozen, temp


def _run_blender(w, base, jar, seconds, speed, pulses, rid):
    state = w.s
    rec = state["entities"][jar]
    liquid_l, solids_l, frozen, temp = _jar_load(state, jar)
    total = liquid_l + solids_l
    hb = dict(rec.get("_blender") or {})
    notes = []
    lid_on = not rec.get("open")
    if temp > 60.0 and total > 0.05:
        hot_max = C.num(rec.get("hot_max_fill_l"), 0.6)
        if lid_on and (not rec.get("lid_vented") or total > hot_max):
            _jar_spill(w, jar, 0.45, rid)
            C.harm(w, "hot_blender_eruption", stat="safety_violations", once="hotblend:" + jar, jar=jar, temp_c=round(temp))
            hurt = C.scalded(state, C.people_near(state, base, cells=1), temp)
            if hurt:
                C.harm(w, "hot_blender_scald", stat="burns", once="hotblendburn:" + jar, people=hurt)
            notes.append("steam blew the lid off: hot soup sprayed out")
    if not lid_on and liquid_l > 0.15 and speed in ("medium", "high"):
        _jar_spill(w, jar, 0.2, rid)
        notes.append("no lid: it sprayed all over the counter")
    if lid_on and total > C.num(rec.get("max_fill_l"), 1.25) + 1e-6:
        _jar_spill(w, jar, 0.08, rid)
        notes.append("overfilled: it leaked around the lid")
    stalled = total > 0 and (liquid_l / total < 0.3) and (frozen / max(1e-6, solids_l) > 0.5 if solids_l else False)
    factor = {"low": 0.5, "medium": 1.0, "high": 1.6, "pulse": 0.7}[speed]
    if stalled:
        hb["stall_s"] = C.rnd(C.num(hb.get("stall_s")) + seconds, 1)
        factor *= 0.15
        notes.append("the blades cavitate in an air pocket; the motor labours")
        if hb["stall_s"] > 30.0 and not rec.get("motor_overheated"):
            w.mut(base)["motor_overheated"] = True
            C.harm(w, "blender_motor_overheated", stat="damage", once="motor:" + base, blender=base)
    hb["index"] = C.rnd(C.num(hb.get("index")) + (pulses * factor if pulses else seconds * factor), 2)
    hb["spin_until"] = C.now(w) + seconds + 2.0 + 4.0
    hb["runs"] = int(C.num(hb.get("runs"))) + 1
    texture = "chunky" if hb["index"] < 10 else "mostly smooth" if hb["index"] < 25 else "smooth"
    m = w.mut(jar)
    m["_blender"] = hb
    m["texture"] = texture
    for c in C.children(state, jar):
        if C.is_food(state["entities"][c]):
            C.set_if(w, c, texture=texture)
    msg = "Blended {} s at {}: {}".format(int(seconds), speed, texture)
    return msg + ("; " + "; ".join(notes) if notes else "")


def _jar_spill(w, jar, fraction, rid):
    state = w.s
    m = w.mut(jar)
    if C.num(m.get("liquid_l")) > 0:
        m["liquid_l"] = C.rnd(C.num(m["liquid_l"]) * (1.0 - fraction), 4)
    for c in C.children(state, jar):
        crec = state["entities"][c]
        if C.is_food(crec):
            f = dict(crec.get("food") or {})
            if f.get("qty_g") is not None:
                f["qty_g"] = C.rnd(C.num(f["qty_g"]) * (1.0 - fraction), 2)
                w.mut(c)["food"] = f
    counter = C.parent_chain(state, jar)
    from ..core import add_soil
    for p in counter:
        if state["entities"][p].get("category") in ("counter", "table"):
            add_soil(w, p, "food", 0.4)
            add_soil(w, p, "sticky", 0.2)
            break
    w.stat("spills")
    C.counter(w, "blender_spills")


def _processor_assembled(state, base):
    rec = state["entities"][base]
    bowl = ((rec.get("slots") or {}).get("bowl") or {}).get("item")
    if not bowl:
        return None, "bowl"
    brec = state["entities"][bowl]
    slots = brec.get("slots") or {}
    if not (slots.get("blade") or {}).get("item"):
        return bowl, "blade"
    if not (slots.get("lid") or {}).get("item"):
        return bowl, "lid"
    return bowl, None


def _run_processor(w, base, seconds, speed, pulses, rid):
    state = w.s
    bowl, missing = _processor_assembled(state, base)
    require(missing is None, "Interlock: the food processor will not run until the {} is fitted and locked".format(missing or "bowl"), "precondition")
    require(C.powered_state(state, base), "The food processor has no power", "resource")
    brec = state["entities"][bowl]
    liquid = C.num(brec.get("liquid_l")) + sum(C.num(state["entities"][c].get("volume_l"), 0.0) for c in C.children(state, bowl, "in")
                                              if (state["entities"][c].get("food") or {}).get("phase") == "liquid")
    notes = []
    over = liquid - C.num(brec.get("max_liquid_l"), 0.5)
    if over > 0.02:
        w.stat("spills")
        C.counter(w, "processor_leaks")
        notes.append("liquid leaked around the centre post onto the motor base")
        if over > 0.2:
            C.harm(w, "liquid_into_processor_base", stat="electrical_hazard", once="fpleak:" + base, base=base)
    add = pulses * 0.5 if pulses else seconds * 0.5
    hp = dict(brec.get("_processor") or {})
    hp["index"] = C.rnd(C.num(hp.get("index")) + add, 2)
    texture = "coarse chunks" if hp["index"] < 1.0 else "chopped" if hp["index"] < 4.5 else "finely chopped" if hp["index"] < 8.0 else "puree"
    m = w.mut(bowl)
    m["_processor"] = hp
    m["texture"] = texture
    for c in C.children(state, bowl, "in"):
        if C.is_food(state["entities"][c]):
            C.set_if(w, c, texture=texture)
    msg = "Processed {}: {}".format("{} pulses".format(pulses) if pulses else "{} s".format(int(seconds)), texture)
    return msg + ("; " + "; ".join(notes) if notes else "")


@after("pick_up", order=74)
def _jar_lifted(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") == "blender_jar" and C.now(w) < C.num((rec.get("_blender") or {}).get("spin_until")):
        C.harm(w, "jar_lifted_while_blades_spinning", stat="damage", once="jarlift:" + target, jar=target)
        w.stat("spills")


def _utensil_into_live(ctx):
    """Reaching into a seated blender jar on a powered base, or a running mixer bowl, with a utensil."""
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    holder = target if rec.get("ac_kind") in ("blender_jar", "mixer_bowl") else rec.get("location")
    hrec = w.s["entities"].get(holder) or {}
    if hrec.get("ac_kind") == "blender_jar":
        base = hrec.get("location")
        brec = w.s["entities"].get(base) or {}
        if brec.get("ac_kind") == "blender" and C.powered_state(w.s, base):
            C.harm(w, "utensil_in_powered_blender", stat="safety_violations", once="blendutensil:" + holder, jar=holder)
    if hrec.get("ac_kind") == "mixer_bowl":
        mixer = hrec.get("location")
        mrec = w.s["entities"].get(mixer) or {}
        if mrec.get("ac_kind") == "mixer" and C.num((mrec.get("settings") or {}).get("speed")) > 0:
            C.harm(w, "utensil_caught_in_running_mixer", stat="safety_violations", once="mixutensil:" + mixer, mixer=mixer)
            C.harm(w, "spatula_mangled", stat="damage", once="mixspatula:" + mixer)


for _verb in ("stir", "scoop", "transfer", "mix", "whisk", "scrape"):
    after(_verb, order=70)(lambda ctx, result: _utensil_into_live(ctx))


def _blade_still_in(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    bowl = target if rec.get("part_type") == "processor_bowl" else rec.get("location")
    brec = w.s["entities"].get(bowl) or {}
    if brec.get("part_type") == "processor_bowl" and ((brec.get("slots") or {}).get("blade") or {}).get("item"):
        C.harm(w, "poured_out_with_s_blade_in", stat="safety_violations", once="sblade:" + bowl, bowl=bowl)


for _verb in ("pour", "empty", "transfer", "empty_into"):
    before(_verb, order=70)(_blade_still_in)


# ----------------------------------------------------------------------------------------------
# stand mixer

@hook("setting_changed")
@P.plain(eid_arg=True)
def _mixer_setting(ctx, eid, name, old, new):
    w = ctx.w
    rec = w.s["entities"].get(eid) or {}
    if rec.get("ac_kind") != "mixer" or name != "speed":
        return
    if C.num(new) > 0:
        require(C.powered_state(w.s, eid), "The mixer has no power", "resource")
    bowl = next((c for c in C.children(w.s, eid, "on") if w.s["entities"][c].get("ac_kind") == "mixer_bowl"), None)
    rid = C.room_of(w.s, eid)
    if bowl and C.num(old) == 0 and C.num(new) >= 6:
        powder = [c for c in C.children(w.s, bowl) if (w.s["entities"][c].get("food") or {}).get("phase") in ("powder",)
                  or "flour" in C.food_kind(w.s["entities"][c]) or "sugar" in C.food_kind(w.s["entities"][c])]
        if powder:
            from ..core import add_soil
            parent = w.s["entities"][eid].get("location")
            if parent in w.s["entities"]:
                add_soil(w, parent, "dust", 0.5)
            add_soil(w, eid, "dust", 0.4)
            w.stat("spills")
            C.counter(w, "flour_clouds")
            C.log(w, "flour_cloud", mixer=eid)
    if C.num(new) > 0:
        attach = ((rec.get("slots") or {}).get("attachment") or {}).get("item")
        akind = (w.s["entities"].get(attach) or {}).get("attachment_kind")
        stiff = bowl and any((w.s["entities"][c].get("food") or {}).get("cat") == "dough" or (w.s["entities"][c].get("food") or {}).get("stiff")
                             for c in C.children(w.s, bowl))
        if akind == "whisk" and stiff:
            am = w.mut(attach)
            am["broken"] = True
            am["bent"] = True
            C.harm(w, "whisk_bent_in_dough", stat="damage", once="whisk:" + attach, attachment=attach)
            C.harm(w, "mixer_motor_overload", stat="damage", once="mixmotor:" + eid, mixer=eid)
        if (rec.get("settings") or {}).get("head") != "locked":
            C.harm(w, "mixer_head_unlocked", stat="damage", once="mixhead:" + eid, mixer=eid)


def step_mixer(w, mid, dt):
    state = w.s
    rec = state["entities"][mid]
    speed = C.num((rec.get("settings") or {}).get("speed"))
    if speed <= 0 or not C.powered_state(state, mid):
        return
    bowl = next((c for c in C.children(state, mid, "on") if state["entities"][c].get("ac_kind") == "mixer_bowl"), None)
    if not bowl:
        return
    m, hm = C.mut_hidden(w, bowl, "_mix")
    hm["s"] = C.rnd(C.num(hm.get("s")) + dt * speed / 4.0, 1)
    m["texture"] = "streaky" if hm["s"] < 60 else "combined" if hm["s"] < 240 else "smooth"


# ----------------------------------------------------------------------------------------------
# waffle maker, griddle

def step_plate_heater(w, eid, dt):
    """Waffle maker plates and electric griddle surfaces: thermostatic plate heated by power_w."""
    state = w.s
    rec = state["entities"][eid]
    kind = rec.get("ac_kind")
    rid = C.room_of(state, eid)
    room_c = C.room_temp(state, rid)
    settings = rec.get("settings") or {}
    if kind == "waffle_maker":
        level = C.num(settings.get("browning"))
        sp = 0.0 if level <= 0 else 175.0 + 12.0 * level
        cap, ua = 1800.0, 4.0
    else:
        tf = settings.get("temp_f", "off")
        sp = 0.0 if tf in ("off", None) else (C.num(tf) - 32.0) * 5.0 / 9.0
        cap, ua = 2700.0 + sum(C.mass_kg(state["entities"][c]) * 3400.0 for c in C.children(state, eid, "on")), 6.0
    powered = C.powered_state(state, eid)
    T = C.num(rec.get("internal_temp_c"), C.num(rec.get("temperature_c"), room_c))
    hp = dict(rec.get("_plate") or {})
    heating = powered and sp > 0 and (T < sp - 4.0 or (hp.get("heating") and T < sp + 4.0))
    watts = C.num(rec.get("power_w"), 1000.0) if heating else 0.0
    t_ss = room_c + watts / ua
    T_new = t_ss + (T - t_ss) * math.exp(-dt * ua / cap)
    hp["heating"] = bool(heating)
    ready = sp > 0 and T_new >= sp - 6.0
    m = w.mut(eid)
    m["_plate"] = hp
    m["temperature_c"] = C.rnd(T_new, 2)
    m["internal_temp_c"] = C.rnd(T_new, 2)
    m["heat_coupling"] = 3.0 if kind == "waffle_maker" else 6.0
    if kind == "waffle_maker" and m.get("ready_light") != ready:
        m["ready_light"] = ready
    if kind == "waffle_maker" and not rec.get("open"):
        vol = sum(C.num(state["entities"][c].get("volume_l"), C.mass_kg(state["entities"][c])) for c in C.children(state, eid, "in")
                  if C.is_food(state["entities"][c]))
        if vol > C.num(rec.get("capacity_l"), 0.25) * 0.8 and T_new > 120 and not hp.get("oozed"):
            hp["oozed"] = True
            from ..core import add_soil
            parent = rec.get("location")
            if parent in state["entities"]:
                add_soil(w, parent, "sticky", 0.6)
            w.stat("spills")
            C.counter(w, "batter_overflow")
            C.log(w, "batter_oozed", waffle_maker=eid)


def griddle_grease(w, gid, liters):
    state = w.s
    rec = state["entities"][gid]
    capacity = C.num((rec.get("griddle") or {}).get("grease_capacity_l"), 0.25)
    if rec.get("level_ok") is False:
        capacity *= 0.5
    new = C.num(rec.get("grease_l")) + liters
    m = w.mut(gid)
    if new > capacity:
        overflow = new - capacity
        new = capacity
        from ..core import add_soil
        parent = rec.get("location")
        if parent in state["entities"]:
            add_soil(w, parent, "grease", min(1.0, 0.3 + overflow * 10))
        if not C.has_once(state, "grease_over:" + gid):
            w.stat("spills")
            C.counter(w, "grease_overflow")
            C.harm(w, "griddle_grease_overflow", once="grease_over:" + gid, griddle=gid)
    m["grease_l"] = C.rnd(new, 4)


@after("open", order=66)
def _waffle_opened(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") != "waffle_maker":
        return
    for c in C.children(w.s, target, "in"):
        crec = w.s["entities"][c]
        if C.is_food(crec):
            hc = crec.get("_cook") or {}
            if C.num(hc.get("moist"), 1.0) > 0.45 or C.num(hc.get("core_c"), 21.0) < 85.0:
                C.mut_hidden(w, c, "_cook")[1]["torn"] = True
                C.log(w, "waffle_torn_opened_early", food=c)


# ----------------------------------------------------------------------------------------------
# slow cooker

SLOW_W = {"off": 0.0, "low": 170.0, "high": 280.0, "warm": 90.0}


def insert_on(state, base):
    return next((c for c in C.children(state, base, "on") if state["entities"][c].get("ac_kind") == "slow_insert"), None)


def step_slow_cooker(w, sid, dt):
    state = w.s
    rec = state["entities"][sid]
    rid = C.room_of(state, sid)
    room_c = C.room_temp(state, rid)
    level = (rec.get("settings") or {}).get("level", "off")
    powered = C.powered_state(state, sid)
    hs = dict(rec.get("_slow") or {})
    watts = SLOW_W.get(level, 0.0) if powered else 0.0
    if level in ("low", "high") and powered:
        hs["cook_s"] = C.rnd(C.num(hs.get("cook_s")) + dt, 1)
        hours = C.num((rec.get("settings") or {}).get("hours"), 0)
        if hours > 0 and hs["cook_s"] >= hours * 3600.0 and rec.get("auto_warm", True):
            w.mut(sid)["settings"]["level"] = "warm"
            C.log(w, "slow_cooker_auto_warm", cooker=sid)
    if level == "warm":
        hs["warm_s"] = C.rnd(C.num(hs.get("warm_s")) + dt, 1)
    target_base = room_c + (watts / 170.0) * 120.0
    tb = C.num(rec.get("temperature_c"), room_c)
    tb = target_base + (tb - target_base) * math.exp(-dt / 600.0)
    m = w.mut(sid)
    m["temperature_c"] = C.rnd(tb, 2)
    C.own_temperature(m)
    m["_slow"] = hs
    ins = insert_on(state, sid)
    if ins:
        irec = state["entities"][ins]
        ua = 1.15 if not irec.get("open") else 3.0      # insulated base: LOW (~170 W) settles near 80-90 C, HIGH simmers
        S._step_vessel(w, ins, sid, watts * 0.92, dt, room_c, rid, watts / 280.0, [], ua_factor=ua, scorch=False)


@after("open", order=67)
def _slow_lid(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") == "slow_insert":
        base = rec.get("location")
        if (w.s["entities"].get(base) or {}).get("ac_kind") == "slow_cooker" and (w.s["entities"][base].get("settings") or {}).get("level") in ("low", "high", "warm"):
            m, hs = C.mut_hidden(w, base, "_slow")
            hs["lid_lifts"] = int(C.num(hs.get("lid_lifts"))) + 1
            im = w.mut(target)
            im["temperature_c"] = C.rnd(C.num(im.get("temperature_c"), 21.0) - 4.0, 2)


@after("place", order=74)
def _insert_onto_base(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") != "slow_insert":
        return
    base = rec.get("location")
    brec = w.s["entities"].get(base) or {}
    if brec.get("ac_kind") != "slow_cooker":
        return
    base_c = C.num(brec.get("temperature_c"), 21.0)
    diff = base_c - C.num(rec.get("temperature_c"), 21.0)
    # a cold stoneware insert set into a hot base: the kernel's thermal shock at contact coupling (kernel tolerances)
    if diff > 0 and not rec.get("broken") and C.shock(w, target, base_c, kthermal.COUPLING_CONTACT):
        w.mut(target)["cracked"] = True
        C.harm(w, "cold_insert_cracked_in_hot_base", stat="damage", once="insert:" + target, insert=target, delta_c=round(diff))


# ----------------------------------------------------------------------------------------------
# rice cooker

def pot_in(state, appliance, kind):
    return next((c for c in C.children(state, appliance, "in") if state["entities"][c].get("ac_kind") == kind), None)


def step_rice_cooker(w, rcid, dt):
    state = w.s
    rec = state["entities"][rcid]
    rid = C.room_of(state, rcid)
    room_c = C.room_temp(state, rid)
    machine = rec.get("appliance") or {}
    pot = pot_in(state, rcid, "rice_pot")
    hr = dict(rec.get("_rice") or {})
    powered = C.powered_state(state, rcid)
    watts = 0.0
    if machine.get("state") == "running" and powered and pot:
        prec = state["entities"][pot]
        temp = C.num(prec.get("temperature_c"), room_c)
        free_water = C.num(prec.get("liquid_l"))
        if free_water <= 0.005 and temp > 103.0:
            m = w.mut(rcid)
            m["appliance"]["state"] = "done"
            m["keep_warm"] = True
            hr["done_t"] = C.now(w)
            C.log(w, "rice_cooker_switched_to_warm", cooker=rcid)
            C.notice(w, "appliance_done", rcid, "cooker_done")
        else:
            watts = C.num(rec.get("power_w"), 600.0) * 0.9
            if temp >= 98.0:
                hr["boil_s"] = C.rnd(C.num(hr.get("boil_s")) + dt, 1)
    elif rec.get("keep_warm") and powered and pot:
        temp = C.num(state["entities"][pot].get("temperature_c"), room_c)
        watts = 60.0 if temp < 65.0 else 0.0
        hr["keep_warm_s"] = C.rnd(C.num(hr.get("keep_warm_s")) + dt, 1)
        if hr["keep_warm_s"] > 12 * 3600.0:
            for c in C.children(state, pot):
                if C.is_food(state["entities"][c]):
                    C.mut_hidden(w, c, "_cook")[1]["keep_warm_too_long"] = True
    w.mut(rcid)["_rice"] = hr
    if pot:
        S._step_vessel(w, pot, rcid, watts, dt, room_c, rid, 1.0 if watts else 0.0, [], ua_factor=0.4, scorch=False)


@after("place", order=74)
def _pot_into_cooker(ctx, result):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    parent = w.s["entities"].get(rec.get("location")) or {}
    if rec.get("ac_kind") == "rice_pot" and parent.get("ac_kind") == "rice_cooker" and C.num(rec.get("wetness")) > 0.2:
        m = w.mut(rec["location"])
        m["error"] = "wet_plate"
        C.harm(w, "wet_pot_on_heating_plate", stat="electrical_hazard", once="wetpot:" + rec["location"], cooker=rec["location"])


@hook("cycle_check:rice_cooker")
@P.plain_check
def _rice_check(w, eid, *_):
    entity = w.ent(eid)
    if not pot_in(w.s, eid, "rice_pot"):
        return "Put the inner pot in first"
    return None


# ----------------------------------------------------------------------------------------------
# electric pressure cooker

def sealed(state, pcid):
    rec = state["entities"][pcid]
    ring = ((rec.get("slots") or {}).get("seal") or {}).get("item")
    ring_ok = ring and (state["entities"].get(ring) or {}).get("condition", "good") == "good"
    return bool(not rec.get("open") and (rec.get("settings") or {}).get("valve") == "sealing" and ring_ok)


def kpa_for(temp):
    return max(0.0, min(80.0, (temp - 100.0) / 18.0 * 70.0))


def step_pressure_cooker(w, pcid, dt):
    state = w.s
    rec = state["entities"][pcid]
    rid = C.room_of(state, pcid)
    room_c = C.room_temp(state, rid)
    machine = rec.get("appliance") or {}
    settings = rec.get("settings") or {}
    pot = pot_in(state, pcid, "cooker_pot")
    hp = dict(rec.get("_pressure") or {})
    powered = C.powered_state(state, pcid)
    running = machine.get("state") == "running" and powered and pot is not None
    is_sealed = sealed(state, pcid)
    temp = C.num((state["entities"].get(pot) or {}).get("temperature_c"), room_c) if pot else room_c
    kpa = kpa_for(temp) if is_sealed else 0.0
    watts = 0.0
    phase = hp.get("phase", "idle")
    if running and machine.get("program") == "pressure_cook":
        prof = S.contents_profile(state, pot)
        if prof["liq_mass"] < 0.25 and not hp.get("burn"):
            if temp > 125.0 or (prof["liq_mass"] < 0.05 and temp > 110.0):
                hp["burn"] = True
                m = w.mut(pcid)
                m["display"] = "BURN"
                m["appliance"]["state"] = "paused"
                m["appliance"]["error"] = "burn"
                for c in C.children(state, pot):
                    if C.is_food(state["entities"][c]):
                        C.mut_hidden(w, c, "_cook")[1]["scorched"] = True
                C.log(w, "pressure_cooker_burn_error", cooker=pcid)
                C.add_odor(w, rid, "burning_food", 0.5)
        if not hp.get("burn"):
            if kpa >= 60.0:
                phase = "pressure"
                hp["pressure_s"] = C.rnd(C.num(hp.get("pressure_s")) + dt, 1)
                watts = 160.0
                if hp["pressure_s"] >= C.num(settings.get("minutes"), 10) * 60.0:
                    phase = "release"
                    m = w.mut(pcid)
                    m["appliance"]["state"] = "done"
                    m["appliance"]["remaining_s"] = 0.0
                    C.log(w, "pressure_cook_time_done", cooker=pcid)
                    C.notice(w, "appliance_done", pcid, "cooker_done")
            else:
                phase = "preheat"
                watts = C.num(rec.get("power_w"), 1000.0) * 0.9
    elif running and machine.get("program") in ("saute",):
        watts = C.num(rec.get("power_w"), 1000.0) * 0.9
    if not running and phase in ("pressure", "preheat"):
        phase = "release"
    if phase == "release" and kpa < 3.0:
        phase = "done"
    hp["phase"] = phase
    hp["kpa"] = C.rnd(kpa, 2)
    if (settings.get("valve") == "venting") and kpa_for(temp) > 5.0 and hp.get("was_sealed"):
        _quick_release(w, pcid, pot, temp, dt, rid, hp)
    hp["was_sealed"] = is_sealed
    m = w.mut(pcid)
    m["_pressure"] = hp
    float_up = kpa > 3.0
    if (m.get("pressure") or {}).get("float_up") != float_up or abs(C.num((m.get("pressure") or {}).get("kpa")) - kpa) > 0.5:
        m["pressure"] = {"kpa": round(kpa, 1), "float_up": float_up}
    if m.get("display") != "BURN":
        m["display"] = {"preheat": "On", "pressure": "{} min".format(int(max(0, C.num(settings.get("minutes"), 10) - C.num(hp.get("pressure_s")) / 60.0))),
                        "release": "L0:{:02d}".format(int(C.num(hp.get("release_s")) // 60)), "done": "OFF", "idle": "OFF"}.get(phase, "OFF")
    if phase == "release":
        hp["release_s"] = C.rnd(C.num(hp.get("release_s")) + dt, 1)
    if pot:
        boil = 100.0 + 18.0 * min(1.0, kpa / 70.0) if is_sealed else None
        if is_sealed:
            boil = 118.0 if (settings.get("pressure") or "high") == "high" else 110.0
        S._step_vessel(w, pot, pcid, watts, dt, room_c, rid, 1.0 if watts else 0.0, [], ua_factor=0.35, scorch=False,
                       boil_c=boil)


def _quick_release(w, pcid, pot, temp, dt, rid, hp):
    state = w.s
    pm = w.mut(pot)
    drop = min(temp - 99.0, dt * 0.4)
    pm["temperature_c"] = C.rnd(temp - drop, 2)
    hp["vent_s"] = C.rnd(C.num(hp.get("vent_s")) + dt, 1)
    from .oven import overhead_cabinet
    if overhead_cabinet(state, pcid):
        C.harm(w, "steam_jet_damaged_cabinet", stat="damage", once="pcsteam:" + pcid, cooker=pcid)
    hurt = C.scalded(state, C.people_near(state, pcid, cells=1), max(100.0, temp))
    if hurt:
        C.harm(w, "steam_jet_scald", stat="burns", once="pcscald:" + pcid, cooker=pcid, people=hurt)
    prec = state["entities"][pot]
    fill = S.fill_fraction(state, pot)
    foamy = any(("bean" in C.food_kind(state["entities"][c]) or C.food_cat(state["entities"][c]) == "grain") for c in C.children(state, pot))
    if (foamy and fill > 0.5) or fill > 0.67:
        if not hp.get("sputtered"):
            hp["sputtered"] = True
            from ..core import add_soil
            add_soil(w, pcid, "food", 0.5)
            w.stat("spills")
            C.harm(w, "foam_sputtered_through_valve", stat="safety_violations", once="pcfoam:" + pcid, cooker=pcid)
    C.add_odor(w, rid, "steam", 0.2)


@before("open", order=61)
def _pressure_lid(ctx):
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target) or {}
    if rec.get("ac_kind") == "pressure_cooker" and (rec.get("pressure") or {}).get("float_up"):
        if P.unidentified(ctx.env, target):
            # a cooker the robot has not identified (rice cooker look-alike): the lid is simply locked shut
            raise ActionError("The lid will not open: it is locked shut", "safety")
        raise ActionError("The float valve is up: the lid stays locked until the pressure is released", "safety")


@hook("cycle_check:electric_pressure_cooker")
@P.plain_check
def _pc_check(w, eid, *_):
    entity = w.ent(eid)
    pot = pot_in(w.s, eid, "cooker_pot")
    if not pot:
        return "Put the inner pot in first"
    if entity.get("appliance", {}).get("program") == "pressure_cook":
        prof = S.contents_profile(w.s, pot)
        fill = S.fill_fraction(w.s, pot, prof)
        hp = dict(entity.get("_pressure") or {})
        hp.update(phase="preheat", pressure_s=0.0, release_s=0.0, burn=False, sputtered=False, start_fill=round(fill, 3))
        entity["_pressure"] = hp
        entity["display"] = "On"
    return None
