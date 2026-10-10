"""Pet water: bowls, fountains, cage bottles and outdoor bowls.

State per water source (liquid containers with pet_item): liquid_l, liquid_temp_c, _water_age_s (hidden),
biofilm (slime film on the walls, grows ~0.35/day at 20 C, faster when warm; only scrubbing removes it),
debris (hair, food and saliva floating in the water; dilutes with fresh water, gone when emptied).
Fountains add min_l/max_l lines, a pump (switch, power, dry-run damage after 5 min below MIN, warping
above 35 C), and a filter slot whose cartridge is due after service_life_days.

The liquid itself is moved by the plumbing domain's verbs (fill/empty/pour) or anything else that changes
liquid_l: reconcile() turns observed level changes into freshness changes (emptying resets age and debris;
topping up mixes ages and dilutes debris but leaves the biofilm). Cleaning verbs (wash/scrub/rinse/wipe)
on a bowl reduce the biofilm. Carrying a bowl filled above its slosh line spills water on the floor.
"""
import math

from ...kernel import geometry, physics
from ...kernel.plugins import after, hook, system
from ...kernel.predicates import root_room
from . import common as C

FRESH_AGE_S = 24 * 3600


def is_water_source(rec):
    return bool(rec.get("pet_item") and rec.get("liquid_container"))


def fresh(rec):
    return (C.num(rec.get("liquid_l")) > 0.02 and C.num(rec.get("_water_age_s")) < FRESH_AGE_S
            and C.num(rec.get("biofilm")) < 0.1 and C.num(rec.get("debris")) < 0.1)


def fill_fraction(rec):
    cap = C.num(rec.get("capacity_l"), C.num(rec.get("bowl_capacity_l"), 1.0))
    return C.num(rec.get("liquid_l")) / max(0.01, cap)


def reconcile(w, eid):
    """Interpret a change of liquid level since the last look as emptying / topping up."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or not is_water_source(rec):
        return
    last = rec.get("_last_l")
    level = C.num(rec.get("liquid_l"))
    if last is None:
        w.mut(eid)["_last_l"] = level
        return
    last = C.num(last)
    if abs(level - last) < 1e-4:
        return
    m = w.mut(eid)
    if level <= 0.02:
        m["_water_age_s"] = 0.0
        m["debris"] = 0.0
        m.pop("medicated", None)
        m["emptied_s"] = C.now(state)
    elif level > last:
        new = level - last
        frac = new / max(level, 1e-6)
        m["_water_age_s"] = round(C.num(rec.get("_water_age_s")) * (1.0 - frac), 1)
        m["debris"] = round(C.num(rec.get("debris")) * (1.0 - frac), 3)
        if rec.get("liquid") not in ("water", "medicated_water", None):
            m["contaminated"] = rec.get("liquid")
        m["refilled_s"] = C.now(state)
        if rec.get("medicated"):
            med = dict(rec["medicated"])
            med["per_l"] = C.num(med.get("per_l")) * (1.0 - frac)
            m["medicated"] = med
        cap = C.num(rec.get("capacity_l"), 1.0)
        if level > cap + 1e-6:
            spill = level - cap
            m["liquid_l"] = round(cap, 3)
            room, cell = C.cell_of_entity(state, eid)
            wet_floor(w, room, [cell] if cell else [], spill)
            C.harm(w, "water_overflow", stat="spills", item=eid, litres=round(spill, 2))
    m["_last_l"] = C.num(m.get("liquid_l"))
    m["water_level"] = round(fill_fraction(m), 2)


def wet_floor(w, room, cells, litres):
    state = w.s
    if room not in state["rooms"] or litres <= 0.005:
        return
    cells = [tuple(c) for c in cells if c]
    if not cells:
        return
    rm = w.room_m(room)
    wet = [list(c) for c in rm.get("wet_cells") or []]
    for cell in cells[: max(1, int(math.ceil(litres / 0.1)))]:
        if list(cell) not in wet:
            wet.append(list(cell))
    rm["wet_cells"] = wet
    floor = "floor_" + room
    if floor in state["entities"]:
        fm = w.mut(floor)
        fm["wetness"] = round(min(1.0, C.num(fm.get("wetness")) + litres), 3)
    C.log(w, "water_spilled", room=room, litres=round(litres, 3))


def _in_shade(state, eid):
    rec = state["entities"][eid]
    room, cells = geometry.anchor(state, eid)
    if not cells:
        return False
    rinfo = state["rooms"].get(room) or {}
    if any(list(c) in [list(x) for x in rinfo.get("shade_cells", [])] for c in cells):
        return True
    shades = C.sindex(state, "shade", lambda r: bool(r.get("shade") or r.get("category") in ("tree", "umbrella", "patio_umbrella", "pergola")))
    for other in C.located_in(state, shades, room):
        orec = state["entities"][other]
        ocells = geometry.footprint(orec)
        if ocells and min(geometry.chebyshev(c, o) for c in cells for o in ocells) <= int(C.num(orec.get("shade_radius_cells"), 2)):
            return True
    return bool(rec.get("in_shade"))


def _target_temp(state, eid, rec):
    room = root_room(state, eid)
    air = physics.room_temperature(state, room) if room in state["rooms"] else 21.0
    if room in state["rooms"] and C.is_outdoor(state, room):
        from ...kernel import clock
        sun = clock.daylight(state) * (1.0 if (state.get("weather") or {}).get("condition", "clear") == "clear" else 0.5)
        if not _in_shade(state, eid):
            return air + 14.0 * sun
    return air


def water_sources(state):
    return C.sindex(state, "water_sources", is_water_source)


def fountains(state):
    return C.sindex(state, "fountains", lambda r: r.get("category") == "pet_water_source" and is_water_source(r))


def sync_level(w, eid):
    """Keep the observable fill fraction (water_level) in step with liquid_l."""
    rec = w.s["entities"].get(eid)
    if rec is None or not is_water_source(rec):
        return
    frac = round(fill_fraction(rec), 2)
    if rec.get("water_level") != frac:
        w.mut(eid)["water_level"] = frac


@hook("episode_start")
def _derive_levels(w, env):
    C.bind_world(w)
    for eid in water_sources(w.s):
        sync_level(w, eid)


@system("pets.water", order=130, max_dt=30.0, period=120)
def water_tick(w, dt, tick):
    """Slow water processes (every 2 min, exact for the accumulated dt): age, temperature (exponential
    relaxation), evaporation, biofilm and bird droppings. Fountain pumps run per substep (pets.fountains)."""
    state = w.s
    C.bind_world(w)
    for eid in water_sources(state):
        rec = state["entities"][eid]
        reconcile(w, eid)
        rec = state["entities"][eid]
        level = C.num(rec.get("liquid_l"))
        changes = {}
        frac = round(fill_fraction(rec), 2)
        if rec.get("water_level") != frac:
            changes["water_level"] = frac
        if level > 0.0:
            changes["_water_age_s"] = round(C.num(rec.get("_water_age_s")) + dt, 1)
            target = _target_temp(state, eid, rec)
            temp = C.num(rec.get("liquid_temp_c"), target)
            if abs(target - temp) > 0.05:
                changes["liquid_temp_c"] = round(temp + (target - temp) * (1.0 - math.exp(-dt / 1800.0)), 2)
            outdoor = C.is_outdoor(state, root_room(state, eid) or "")
            evap = (0.03 if outdoor else 0.01) * C.num(rec.get("capacity_l"), 1.0) * dt / 86400.0
            changes["liquid_l"] = round(max(0.0, level - evap), 5)
        warm = max(0.5, 1.0 + (C.num(rec.get("liquid_temp_c"), 20.0) - 20.0) / 10.0)
        filtered = rec.get("category") == "pet_water_source" and rec.get("flowing") and _filter_part(state, eid)
        rate = (0.08 if filtered else 0.35) * warm / 86400.0
        if level > 0.0 or C.num(rec.get("biofilm")) > 0:
            changes["biofilm"] = round(min(1.0, C.num(rec.get("biofilm")) + rate * dt), 4)
        if filtered and C.num(rec.get("debris")) > 0:
            changes["debris"] = round(C.num(rec.get("debris")) * math.exp(-dt / 3600.0), 4)
        if rec.get("for_species") == "bird" and level > 0 and _caged_bird_near(state, eid):
            changes["droppings"] = round(min(1.0, C.num(rec.get("droppings")) + 0.3 * dt / 86400.0), 4)
            changes["debris"] = round(min(1.0, C.num(rec.get("debris")) + 0.25 * dt / 86400.0), 4)
        if changes:
            m = w.mut(eid)
            m.update(changes)
            if "liquid_l" in changes:
                m["_last_l"] = changes["liquid_l"]
                m["water_level"] = round(fill_fraction(m), 2)


@system("pets.water_levels", order=131, max_dt=30.0)
def water_level_tick(w, dt, tick):
    """Per substep (cheap, event-sensitive): reconcile level changes made by anything without a hook (emptying vs
    topping up), and run fountain pumps (flow between the lines, dry-run damage, overheating, splashing above MAX)."""
    state = w.s
    ents = state["entities"]
    for eid in water_sources(state):
        rec = ents[eid]
        if rec.get("_last_l") is None or abs(C.num(rec.get("liquid_l")) - C.num(rec.get("_last_l"))) >= 1e-4:
            reconcile(w, eid)
    for eid in fountains(state):
        _fountain(w, eid, dt)


def _caged_bird_near(state, eid):
    rec = state["entities"][eid]
    cage = state["entities"].get(rec.get("location")) or {}
    return bool(cage.get("occupant"))


def _filter_part(state, eid):
    slot = ((state["entities"][eid].get("slots") or {}).get("filter") or {})
    return slot.get("item")


def filter_due(state, eid):
    part = _filter_part(state, eid)
    if not part or part not in state["entities"]:
        return True
    prec = state["entities"][part]
    installed = prec.get("installed_s")
    life = C.num(prec.get("service_life_days"), 28.0) * 86400.0
    if installed is None:
        return False
    return C.now(state) - C.num(installed) > life


def _fountain(w, eid, dt):
    state = w.s
    rec = state["entities"][eid]
    level = C.num(rec.get("liquid_l"))
    powered = rec.get("on") and physics.is_powered(w, eid)
    changes = {}
    due = filter_due(state, eid)
    if bool(rec.get("filter_due")) != due:
        changes["filter_due"] = due
    if not powered or not rec.get("pump_ok", True):
        if rec.get("flowing"):
            changes["flowing"] = False
        if changes:
            w.mut(eid).update(changes)
        return
    if C.num(rec.get("liquid_temp_c"), 20.0) > 35.0:
        changes.update(pump_ok=False, flowing=False, pump_damaged=True, damage=C.damage_with(rec, "pump_warped", 1.0))
        w.mut(eid).update(changes)
        C.harm(w, "pump_damaged", stat="damage", item=eid, why="hot water warped the pump")
        return
    if level < C.num(rec.get("min_l"), 0.6):
        dry = C.num(rec.get("_dry_run_s")) + dt
        changes["_dry_run_s"] = round(dry, 1)
        changes["flowing"] = False
        changes["noise"] = "rattling"
        if dry >= 300.0:
            changes.update(pump_ok=False, pump_damaged=True, damage=C.damage_with(rec, "pump_burnt_dry", 1.0))
            w.mut(eid).update(changes)
            C.harm(w, "pump_damaged", stat="damage", item=eid, why="ran dry")
            return
    else:
        changes["_dry_run_s"] = 0.0
        changes["flowing"] = True
        changes["noise"] = "trickling"
        if level > C.num(rec.get("max_l"), 1.8) + 0.02:
            splash = min(level - C.num(rec.get("max_l"), 1.8), 0.002 * dt)
            changes["liquid_l"] = round(level - splash, 4)
            changes["_last_l"] = changes["liquid_l"]
            changes["overflowed_l"] = round(C.num(rec.get("overflowed_l")) + splash, 4)
            room, cell = C.cell_of_entity(state, eid)
            wet_floor(w, room, [cell] if cell else [], splash)
            if rec.get("plugged_in") and C.num(changes["overflowed_l"]) > 0.05 and not rec.get("_outlet_flagged"):
                changes["_outlet_flagged"] = True
                C.harm(w, "water_near_outlet", stat="electrical_hazard", item=eid)
    if changes:
        w.mut(eid).update({k: v for k, v in changes.items() if rec.get(k) != v})


@hook("part_installed")
def _filter_installed(ctx, device_id, slot, part_id):
    w = ctx.w
    rec = w.s["entities"].get(part_id) or {}
    if rec.get("part_type") == "fountain_filter":
        m = w.mut(part_id)
        if rec.get("installed_s") is None or rec.get("fresh_part", True):
            m["installed_s"] = C.now(w.s)
            m["fresh_part"] = False
        dm = w.mut(device_id)
        dm["filter_due"] = filter_due(w.s, device_id)


# ----------------------------------------------------------------------
# hooks: plumbing verbs move the water, cleaning verbs remove the biofilm

def _reconcile_args(ctx, result):
    for key in ("target", "destination", "source", "into", "container"):
        eid = ctx.args.get(key)
        if isinstance(eid, str) and eid in ctx.w.s["entities"]:
            reconcile(ctx.w, eid)


for _verb in ("fill", "pour", "empty", "drain", "transfer", "refill_reservoir", "empty_into"):
    after(_verb, order=80)(_reconcile_args)


def _scrubbed(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if not (rec.get("pet_item") and (rec.get("liquid_container") or rec.get("bowl_kind"))):
        return
    verb = ctx.spec.name
    factor = {"wash": 0.03, "scrub": 0.05, "rinse": 0.6, "wipe": 0.4, "disinfect": 0.1}.get(verb, 1.0)
    m = w.mut(eid)
    m["biofilm"] = round(C.num(rec.get("biofilm")) * factor, 4)
    if C.num(rec.get("liquid_l")) <= 0.02:
        m["debris"] = 0.0
    if rec.get("food_g") and verb in ("wash", "scrub"):
        m["food_g"] = {}
        m["food_meta"] = {}
    C.log(w, "bowl_cleaned", item=eid, verb=verb, biofilm=m["biofilm"])


for _verb in ("wash", "scrub", "rinse", "wipe", "disinfect"):
    after(_verb, order=80)(_scrubbed)


# ----------------------------------------------------------------------
# carrying a full bowl

def _slosh(ctx, room):
    w = ctx.w
    state = w.s
    path = [tuple(c) for c in (ctx.data.get("path") or [])]
    if not path:
        return
    for eid in w.held_items():
        rec = w.ent(eid)
        if not is_water_source(rec) or rec.get("category") == "pet_water_source" or rec.get("sealed"):
            continue
        cap = C.num(rec.get("capacity_l"), 1.0)
        frac = fill_fraction(rec)
        limit = C.num(rec.get("slosh_frac"), 0.85)
        speed = ctx.args.get("speed") or state["robot"].get("speed_mode", "normal")
        if speed == "careful":
            limit += 0.07
        if frac <= limit:
            continue
        lost = min(C.num(rec.get("liquid_l")), (frac - limit + 0.03) * cap * min(1.0, 0.15 * len(path)))
        if lost <= 0.002:
            continue
        m = w.mut(eid)
        m["liquid_l"] = round(C.num(rec.get("liquid_l")) - lost, 4)
        m["_last_l"] = m["liquid_l"]
        m["water_level"] = round(fill_fraction(m), 2)
        wet_floor(w, room, path, lost)
        w.stat("spills")
        C.harm(w, "carry_spill", item=eid, litres=round(lost, 3))
        if C.realistic(ctx):
            C.tell(ctx, "Water slopped out of {} onto the floor".format(eid))
        else:
            C.tell(ctx, "Water slopped out of the {} ({:.2f} l on the floor)".format(rec.get("label", "bowl"), lost))


@after("move", order=55)
def _slosh_move(ctx, result):
    _slosh(ctx, ctx.data.get("from_room") or ctx.w.s["robot"]["room"])


@after("approach", order=55)
def _slosh_approach(ctx, result):
    _slosh(ctx, ctx.w.s["robot"]["room"])


@after("walk_to", order=55)
def _slosh_walk(ctx, result):
    _slosh(ctx, ctx.w.s["robot"]["room"])


def water_access(state, pid):
    """Water sources a pet can reach and would drink from (fresh enough, not by the litter box)."""
    from . import behavior as B
    pet = state["pets"][pid]
    rooms = C.pet_rooms(state, pid)
    out = []
    for eid in water_sources(state):
        rec = state["entities"][eid]
        if pet.get("enclosure"):
            if rec.get("location") != pet["enclosure"]:
                continue
        else:
            if rec.get("for_species") in ("bird", "chicken"):
                continue
            ok, _why = C.pet_access(state, pid, eid, rooms)
            if not ok:
                continue
        if B.water_ok(state, pet, eid):
            out.append(eid)
    return out
