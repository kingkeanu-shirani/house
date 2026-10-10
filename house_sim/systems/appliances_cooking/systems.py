"""The domain's time systems.

`appliances_cooking.tick` (order 55, every <= 30 s substep, after the kernel thermal pass 50 and food.thermal 52):
burners and their vessels, ovens/toaster ovens/air fryers, fridges/freezers, microwaves, toasters, kettles, plate
heaters (waffle maker, griddle), slow/rice/pressure cookers, mixers, dishwashers (idle cooling), grills,
coffee/espresso machines, timers, then the cooking model for every food item that is hot or inside an active heater.

Cost (integration round): no per-tick scan of all entities. One kernel static index (`w.static_index`) lists the
entities this domain can ever act on - appliances and their parts (`ac_kind`), vessels (conducts_heat /
liquid_container / cookware / liquid_l), food (`food` dict) and timer holders - and is regrouped only when entities
are created or destroyed. Each appliance is first checked by a cheap idle predicate on its own record (knob off,
cold, closed, no running program, nothing hot sitting on it); only active ones run their sub-model. Contents lookups
during the tick share one lazily built location index (common.tick_index_*), which is never built while everything
is idle.

`appliances_cooking.drying` (period 300 s): fallback air-drying of dishwasher-washed items, used only when the cleaning
domain (which owns generic evaporation, conventions 5: drying_owner) is not loaded; exact exponential decay for any dt.
The fallback smoke/gas/odour ventilation (only without the safety domain) runs at the end of the tick.
"""
from ...kernel.plugins import system
from . import coffee, cold, common as C, cook, countertop, dishwasher, grill, oven, stove, timers

GROUP_OF = {"burner": "burner", "oven": "oven", "toaster_oven": "oven", "air_fryer": "oven", "fridge": "cold",
            "freezer": "cold", "microwave": "microwave", "toaster": "toaster", "kettle": "kettle", "waffle_maker": "plate",
            "griddle": "plate", "slow_cooker": "slow", "rice_cooker": "rice", "pressure_cooker": "pressure", "mixer": "mixer",
            "dishwasher": "dishwasher", "grill": "grill", "coffee_maker": "coffee", "espresso": "espresso"}
GROUPS = ("burner", "oven", "cold", "microwave", "toaster", "kettle", "plate", "slow", "rice", "pressure", "mixer",
          "dishwasher", "grill", "coffee", "espresso", "timer", "vessel", "food")
TIMER_KINDS = ("oven", "toaster_oven", "microwave")
NEAR_C = 1.0          # an idle appliance within this of the room temperature has nothing left to relax


def _is_vessel(rec):
    return bool(rec.get("conducts_heat") or rec.get("liquid_container") or "liquid_l" in rec or rec.get("ac_kind")
                or C.is_cookware(rec))


def _relevant(rec):
    """Static predicate (type-level fields only) for everything the tick can touch."""
    return bool(rec.get("ac_kind") or isinstance(rec.get("food"), dict) or _is_vessel(rec)
                or rec.get("timer_channels") is not None or "timers" in rec)


_CACHE = {"src": None, "groups": None}


def groups_of(w):
    """{group: [ids]} from the static index; recomputed only when the index itself is rebuilt."""
    ids = w.static_index("appliances_cooking.relevant", _relevant)
    if _CACHE["src"] is ids:
        return _CACHE["groups"]
    ents = w.s["entities"]
    g = {k: [] for k in GROUPS}
    for eid in ids:
        rec = ents.get(eid)
        if rec is None:
            continue
        kind = rec.get("ac_kind")
        if kind in GROUP_OF:
            g[GROUP_OF[kind]].append(eid)
        if rec.get("timer_channels") is not None or "timers" in rec or kind in TIMER_KINDS:
            g["timer"].append(eid)
        if _is_vessel(rec):
            g["vessel"].append(eid)
        if isinstance(rec.get("food"), dict):
            g["food"].append(eid)
    _CACHE.update(src=ids, groups=g)
    return g


# ----------------------------------------------------------------------------------------------
# idle predicates: True when stepping the appliance would change nothing that matters (cheap: own record only)

def _room_c(state, eid):
    return C.room_temp(state, C.room_of(state, eid))


def _near(value, target, tol=NEAR_C):
    return value is None or abs(C.num(value, target) - target) <= tol


class _Ctx:
    """Per-tick lazily computed helpers shared by the idle predicates."""

    def __init__(self, w, g):
        self.w, self.s, self.g = w, w.s, g
        self._on = None
        self._food = None

    def vessels_at(self, loc):
        if self._on is None:
            ents = self.s["entities"]
            self._on = {}
            for v in self.g["vessel"]:
                rec = ents.get(v)
                if rec is not None:
                    self._on.setdefault(rec.get("location"), []).append(v)
        return self._on.get(loc, ())

    def foods_at(self, loc):
        if self._food is None:
            ents = self.s["entities"]
            self._food = {}
            for f in self.g["food"]:
                rec = ents.get(f)
                if rec is not None:
                    self._food.setdefault(rec.get("location"), []).append(f)
        return self._food.get(loc, ())


COOL_C = 40.0         # a vessel on a switched-off heater at or below this (or near room temperature) is left to the
                      # kernel/food thermal relaxation: no boiling, foam, scorching or scald risk remains


def _cool_vessel(state, vid, room_c):
    rec = state["entities"][vid]
    t = rec.get("temperature_c")
    return ((t is None or C.num(t) <= max(room_c + NEAR_C, COOL_C)) and C.num((rec.get("_vessel") or {}).get("foam")) < 1e-3
            and rec.get("foam_level") in (None, "none"))


def release_vessel(w, vid):
    """Give a cool vessel on a switched-off heater back to the kernel/food thermal models (it is re-owned the moment
    its heater delivers heat again)."""
    m = w.mut(vid)
    m.pop("_ac_heat", None)
    m.pop("fixed_temperature", None)
    m.pop("foam_level", None)
    if m.get("boil_state") not in (None, "cold", "off_heat"):
        m["boil_state"] = "cold"


def burner_idle(ctx, bid):
    state = ctx.s
    rec = state["entities"][bid]
    b = rec.get("burner") or {}
    if C.level_frac((rec.get("settings") or {}).get("heat")) > 0 or b.get("lit") or b.get("error") or b.get("residual_hot"):
        return False
    if rec.get("_gas_leak") or rec.get("_co_source"):
        return False
    hb = rec.get("_burner") or {}
    if hb.get("unlit_s") or hb.get("no_pan_s") or hb.get("inherited_leak"):
        return False
    room_c = None
    for v in ctx.vessels_at(bid):
        vrec = state["entities"][v]
        if vrec.get("relation", "on") != "on":
            continue
        room_c = _room_c(state, bid) if room_c is None else room_c
        if not _cool_vessel(state, v, room_c):
            return False
    t = rec.get("temperature_c")
    if t is not None and C.num(t) > (_room_c(state, bid) if room_c is None else room_c) + 2.0:
        return False
    return True


def oven_idle(ctx, oid):
    state = ctx.s
    rec = state["entities"][oid]
    if oven.running(rec) or (rec.get("open") and rec.get("openable")) or rec.get("preheated") or rec.get("locked"):
        return False
    ho = rec.get("_oven") or {}
    if ho.get("heating") or C.num(ho.get("episode_open_s")):
        return False
    room_c = _room_c(state, oid)
    if not _near(rec.get("internal_temp_c"), room_c) or rec.get("hot_light"):
        return False
    transparent = (rec.get("settings") or {}).get("light") == "on" or rec.get("ac_kind") == "toaster_oven"
    if rec.get("transparent") != transparent or rec.get("display") != oven._display(rec, False, room_c, 0.0):
        return False
    return True


def microwave_idle(ctx, mid):
    rec = ctx.s["entities"][mid]
    hm = rec.get("_mw") or {}
    return (rec.get("appliance") or {}).get("state") != "running" and not hm.get("arcing") and not hm.get("sh_watch")


def toaster_idle(ctx, tid):
    rec = ctx.s["entities"][tid]
    ht = rec.get("_toaster") or {}
    return ((rec.get("appliance") or {}).get("state") != "running" and abs(C.num(ht.get("element_eff_c"), 21.0) - 21.0) < 0.005
            and "internal_temp_c" not in rec)


def plate_idle(ctx, eid):
    state = ctx.s
    rec = state["entities"][eid]
    settings = rec.get("settings") or {}
    if rec.get("ac_kind") == "waffle_maker":
        on = C.num(settings.get("browning")) > 0
    else:
        on = settings.get("temp_f", "off") not in ("off", None)
    if on or (rec.get("_plate") or {}).get("heating") or rec.get("ready_light"):
        return False
    room_c = _room_c(state, eid)
    return _near(rec.get("internal_temp_c"), room_c) and _near(rec.get("temperature_c"), room_c)


def _contents_cold(ctx, appliance, room_c):
    state = ctx.s
    for v in ctx.vessels_at(appliance):
        if not _cool_vessel(state, v, room_c):
            return False
    return True


def slow_idle(ctx, sid):
    state = ctx.s
    rec = state["entities"][sid]
    if (rec.get("settings") or {}).get("level", "off") not in ("off", None):
        return False
    room_c = _room_c(state, sid)
    t = rec.get("temperature_c")
    return (t is None or C.num(t) <= max(room_c + NEAR_C, COOL_C)) and _contents_cold(ctx, sid, room_c)


def rice_idle(ctx, rcid):
    state = ctx.s
    rec = state["entities"][rcid]
    if (rec.get("appliance") or {}).get("state") == "running" or rec.get("keep_warm"):
        return False
    return _contents_cold(ctx, rcid, _room_c(state, rcid))


def pressure_idle(ctx, pcid):
    state = ctx.s
    rec = state["entities"][pcid]
    hp = rec.get("_pressure") or {}
    if (rec.get("appliance") or {}).get("state") == "running" or hp.get("phase", "idle") not in ("idle", "done"):
        return False
    if C.num(hp.get("kpa")) > 0 or (rec.get("pressure") or {}).get("float_up") or rec.get("display") not in ("OFF", "BURN"):
        return False
    return _contents_cold(ctx, pcid, _room_c(state, pcid))


def grill_idle(ctx, gid):
    state = ctx.s
    rec = state["entities"][gid]
    g = rec.get("grill") or {}
    if g.get("lit") or g.get("coals_lit") or rec.get("lit") or rec.get("flare_up"):
        return False
    if any(k.startswith("burner_") and v != "off" for k, v in (rec.get("settings") or {}).items()):
        return False
    amb = _room_c(state, gid)
    return _near(g.get("grate_c"), amb) and (g.get("ash_c") is None or C.num(g.get("ash_c")) <= amb)


def espresso_idle(ctx, eid):
    state = ctx.s
    rec = state["entities"][eid]
    if rec.get("on"):
        return False
    esp = rec.get("espresso") or {}
    return not esp.get("ready") and _near(esp.get("boiler_c"), _room_c(state, eid), 0.01)


def dishwasher_idle(ctx, eid):
    state = ctx.s
    rec = state["entities"][eid]
    return (rec.get("appliance") or {}).get("state") == "running" or _near(rec.get("internal_temp_c"), _room_c(state, eid), 0.5)


def timer_idle(ctx, eid):
    return not any(t.get("state") in ("running", "ringing") for t in (ctx.s["entities"][eid].get("timers") or []))


# ----------------------------------------------------------------------------------------------
# food

def _active_holder(state, eid):
    rec = state["entities"][eid]
    kind = rec.get("ac_kind")
    if kind == "burner":
        return stove.burner_active(state, eid) or C.num(rec.get("temperature_c"), 21.0) > 45.0
    if kind in ("oven", "toaster_oven", "air_fryer", "waffle_maker"):
        return C.num(rec.get("internal_temp_c"), 21.0) > 40.0
    if kind == "griddle":
        return C.num(rec.get("temperature_c"), 21.0) > 40.0
    if kind == "toaster":
        return (rec.get("appliance") or {}).get("state") == "running" or C.num((rec.get("_toaster") or {}).get("element_eff_c"), 21.0) > 40.0
    if kind == "microwave":
        return (rec.get("appliance") or {}).get("state") == "running"
    if kind == "grill":
        return C.num((rec.get("grill") or {}).get("grate_c"), 15.0) > 40.0
    if kind in ("slow_cooker", "rice_cooker", "pressure_cooker"):
        return True
    return False


def _hot_food(rec):
    """cook.needs_update with a fast reject for the common cold, never-cooked item."""
    t = rec.get("temperature_c")
    if (t is None or t <= 38.0) and not rec.get("_cook"):
        return False
    return cook.needs_update(rec)


def foods_to_step(ctx, active_holders, idle_cookers=()):
    """Hot food anywhere, food inside an active heater, and food in a (switched-off) slow/rice/pressure cooker's pot,
    which keeps its danger-zone clock running as it always did."""
    state, g = ctx.s, ctx.g
    ents = state["entities"]
    found = set()
    for fid in g["food"]:
        rec = ents.get(fid)
        if rec is not None and _hot_food(rec):
            found.add(fid)
    for hid in active_holders:
        if hid not in ents or not _active_holder(state, hid):
            continue
        for c in C.descendants(state, hid):
            if C.is_food(ents[c]):
                found.add(c)
    for cid in idle_cookers:
        found.update(ctx.foods_at(cid))
        for v in ctx.vessels_at(cid):
            found.update(ctx.foods_at(v))
    return sorted(found)


# ----------------------------------------------------------------------------------------------
# the tick

def _heated_vessels(state, g):
    ents = state["entities"]
    return [v for v in g["vessel"] if v in ents and ents[v].get("_ac_heat")]


def _step_active(w, ctx, g, dt):
    state = w.s
    ents = state["entities"]
    idle_heaters = set()
    burners = []
    for b in g["burner"]:
        if b in ents:
            (idle_heaters.add(b) if burner_idle(ctx, b) else burners.append(b))
    heated = _heated_vessels(state, g)
    stove.step_burners(w, dt, burners, heated)
    holders = list(burners)
    plan = (("oven", oven_idle, oven.step_oven), ("cold", None, cold.step_cold),
            ("microwave", microwave_idle, None), ("toaster", toaster_idle, countertop.step_toaster),
            ("kettle", lambda c, e: not c.s["entities"][e].get("on"), countertop.step_kettle),
            ("plate", plate_idle, countertop.step_plate_heater), ("slow", slow_idle, countertop.step_slow_cooker),
            ("rice", rice_idle, countertop.step_rice_cooker), ("pressure", pressure_idle, countertop.step_pressure_cooker),
            ("mixer", lambda c, e: C.num((c.s["entities"][e].get("settings") or {}).get("speed")) <= 0, countertop.step_mixer),
            ("dishwasher", dishwasher_idle, dishwasher.step_dishwasher), ("grill", grill_idle, grill.step_grill),
            ("coffee", lambda c, e: not c.s["entities"][e].get("on"), coffee.step_coffee_maker),
            ("espresso", espresso_idle, coffee.step_espresso), ("timer", timer_idle, timers.step_timers))
    idle_cookers = []
    for group, idle, step in plan:
        for eid in g[group]:
            if eid not in ents:
                continue
            if idle is not None and idle(ctx, eid):
                if group in ("slow", "rice", "pressure"):
                    idle_cookers.append(eid)
                if group in ("oven", "slow", "rice", "pressure"):
                    idle_heaters.add(eid)
                continue
            if group == "microwave":
                _step_microwave(w, eid, dt)
            else:
                step(w, eid, dt)
            if group in ("oven", "toaster", "microwave", "plate", "slow", "rice", "pressure", "grill"):
                holders.append(eid)
    for sid in idle_cookers:
        if ents[sid].get("fixed_temperature") and ents[sid].get("ac_kind") == "slow_cooker":
            w.mut(sid).pop("fixed_temperature", None)      # a switched-off, cool base relaxes like any object
    for vid in heated:
        rec = ents.get(vid)
        if rec is not None and rec.get("_ac_heat") in idle_heaters:
            heater = rec["_ac_heat"]
            if (ents.get(heater) or {}).get("ac_kind") in stove.OVEN_HEATERS or _cool_vessel(state, vid, _room_c(state, vid)):
                release_vessel(w, vid)
    for fid in foods_to_step(ctx, holders, idle_cookers):
        if fid in ents:
            cook.step_food(w, fid, dt)
    C.decay_air(w, dt)


def _step_microwave(w, mid, dt):
    """The microwave sub-model plus a watch on superheated liquid left inside after a run (it decays while inside)."""
    rec = w.s["entities"][mid]
    running = (rec.get("appliance") or {}).get("state") == "running"
    countertop.step_microwave(w, mid, dt)
    rec = w.s["entities"][mid]
    hm = rec.get("_mw") or {}
    if running:
        if not hm.get("sh_watch"):
            C.mut_hidden(w, mid, "_mw")[1]["sh_watch"] = True
    elif hm.get("sh_watch"):
        ents = w.s["entities"]
        if not any(C.num((ents[c].get("_mw") or {}).get("superheat_c")) > 0 for c in C.descendants(w.s, mid)):
            C.mut_hidden(w, mid, "_mw")[1]["sh_watch"] = False


@system("appliances_cooking.tick", order=55, max_dt=30.0)
def tick(w, dt, tk):
    C.flush_destroyed(w)
    g = groups_of(w)
    C.tick_index_begin(w)
    try:
        _step_active(w, _Ctx(w, g), g, dt)
    finally:
        C.tick_index_end()


# ----------------------------------------------------------------------------------------------
# slow fallbacks (periodic)

@system("appliances_cooking.drying", order=76, max_dt=300.0, period=300.0)
def drying(w, dt, tk):
    """Fallback air-drying of washed dishes when no generic drying domain is loaded (cleaning.drying owns evaporation
    of everything it is not told to skip). Periodic, exact exponential decay; nothing runs in an integrated engine."""
    if C.system_loaded("cleaning.drying"):
        return
    ents = w.s["entities"]
    wet = [e for e in sorted(ents) if ents[e].get("washed_t") is not None and C.num(ents[e].get("wetness")) > 0.0
           and ents[e].get("drying_owner") in (None, C.SECTION) and ents[e].get("_wet_mine") is None]
    if wet:
        dishwasher.step_drying(w, wet, dt)
