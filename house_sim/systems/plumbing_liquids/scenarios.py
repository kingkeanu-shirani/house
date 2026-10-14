"""Scenario helpers for plumbing_liquids task specs (setup op "helper"; every helper returns bindings).

  plumbing_liquids.kitchen_sink(room)            -> sink, sink_cabinet, hot_stop, cold_stop, trap, counter, kitchen
  plumbing_liquids.counter_items(type, count, var, props, room, counter, required)
                                                 -> var (id, or list + var_1 ...): loose things on a kitchen counter
                                                 with room (the bound counter first, turned if need be, dressing put
                                                 away as a last resort) - use it instead of "spawn on {counter}"
  plumbing_liquids.bathroom(room, faucet, worn, reversed, nonpotable, running)
                                                 -> bath_sink, vanity, bath_hot, bath_cold, toilet, tub, bathroom
  plumbing_liquids.water_service(valve_kind, seized, heater_c)
                                                 -> main, meter, heater, heater_inlet, service_room
  plumbing_liquids.toilet_trouble(kind)          kind paper|rising|object|chemical|sewer -> toilet, plunger, cup_plunger,
                                                 gloves, toy, cleaner, bathroom
  plumbing_liquids.burst_pipe(rate_lpm, line)    -> pipe, service_room (an exposed run along a wall spraying until the main closes)
  plumbing_liquids.hidden_leak(source, rate_lpm, preleak_l, witness)
                                                 source kitchen_supply|toilet_supply|heater_relief|bath_trap ->
                                                 leak_source, leak_fixture, leak_valve, puddle, leak_room, decoy
  plumbing_liquids.yard_hose(attached, nozzle, spigot_on, pressurized, water_l, kinks, frost_free, lying, storage_room)
                                                 -> spigot, hose, nozzle, hanger, interior_valve, yard, garage_hanger
  plumbing_liquids.irrigation(start_in_h, minutes, rain)      -> controller, zone, heads, irrigation_yard
  plumbing_liquids.freezer(room)                 -> freezer, peas
  plumbing_liquids.drinkware(room)               -> glass, plastic_cup, dirty_glass, cup_cabinet
  plumbing_liquids.dirty_bucket(room, liters, chemical)       -> bucket, bucket_room
  plumbing_liquids.clear_door_sweeps(rooms, fronts)           -> cleared_items
All placements tolerate crowded rooms and any generated layout: floor furniture goes where it and everything else in
the room stays reachable (furnish.furniture_keeping_reach), floor items on cells that cut no route, outside door
clearance and reachable without stepping on anything (furnish.place_floor_item; or the room next door), wall controls
are moved along the walls when boxed in or hidden, supply pipes run along a wall (furnish.lay_pipe). SpecError only
when the house lacks the room (counter_items: when no kitchen surface has room even with the dressing put away).
"""
from ...kernel import clock, geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper

WATER_STAIN = "water_stain"


def _ents(ctx):
    return ctx.config["entities"]


def _room(ctx, room, *kinds):
    if room and room in ctx.config["rooms"]:
        return room
    for kind in ((room,) if room else ()) + kinds:
        found = ctx.placer.rooms(kind)
        if found:
            return found[0]
    return None


def _first(ctx, type_name, room=None, where=None):
    found = ctx.placer.find(type_name, room=room, where=where)
    return found[0] if found else None


def _floor_spot(ctx, rid, type_name, **props):
    """Place floor furniture so it is reachable and nothing reachable before is cut off (every spot of the room is
    tried, wall spots first)."""
    from .furnish import furniture_keeping_reach, reachable
    eid = furniture_keeping_reach(ctx.placer, rid, type_name, wall=True, **props)
    if eid is not None and not reachable(ctx.placer, eid):
        _remove(ctx, eid)
        return None
    return eid


def _reachable_cell(ctx, rid, eid):
    """A free floor cell next to an entity that the robot can walk to from a doorway (where a leak's drips land)."""
    state = ctx.state()
    _, cells = geometry.anchor(dict(state, robot={"room": rid, "hands": {}, "pos": None}), eid)
    if not cells:
        return None
    blocked = set(geometry.blocked_cells(state, rid))
    reach = geometry.reachable_cells(state, rid)
    near = sorted(geometry.near_cells(cells, ctx.config["rooms"][rid]) - set(cells) - blocked)
    for cell in [c for c in near if c in reach] + near:
        return list(cell)
    return None


def derive(ctx, *eids):
    """Refresh derived fields (water_running, flow_lpm, outlet_temp_c, dripping, drain rate) on the setup config."""
    from ...kernel.world import World
    from . import fixtures as F
    state = ctx.state()
    w = World(dict(state, robot={"room": ctx.config.get("start_room"), "hands": {}, "pos": None}, utilities=ctx.config.get("utilities", {}),
                   time_s=0.0), ctx.reg)
    for eid in eids:
        if eid in ctx.config["entities"] and F.faucet(ctx.config["entities"][eid]):
            F.refresh(w, eid)
            if ctx.config["entities"][eid].get("basin_l"):
                F.refresh_drain(w, eid)


def _free_area(ctx, sid):
    """Top area of a surface not covered by what already stands on it (m2)."""
    ents = _ents(ctx)
    rec = ents[sid]
    size = rec.get("surface_size_m") or (rec.get("size_m") or [0.5, 0.5])[:2]
    used = sum(float((ents[c].get("size_m") or [0.1, 0.1])[0]) * float((ents[c].get("size_m") or [0.1, 0.1])[1])
               for c in sorted(ents) if ents[c].get("location") == sid and ents[c].get("relation", "on") == "on")
    return float(size[0]) * float(size[1]) - used


def _roomiest(ctx, surfaces):
    """The surface with the most free area (other domains may have crowded the counters)."""
    best, best_free = None, -1.0
    for sid in surfaces:
        free = _free_area(ctx, sid)
        if free > best_free:
            best, best_free = sid, free
    return best


# ----------------------------------------------------------------------------------------------------------------
# loose items on the kitchen counters

def _spot(ctx, surface, size, rot=None):
    """Where an item of `size` (and rot) would go on surface next to what stands there now: the kernel's first-fit
    geometry.surface_spot, exactly as Placer.on places it. None when it does not fit."""
    ents = _ents(ctx)
    view = {k: v for k, v in ents.items() if k == surface or v.get("location") == surface}
    probe = {"location": surface, "relation": "on", "size_m": list(size)}
    if rot:
        probe["rot"] = rot
    view["__probe__"] = probe
    return geometry.surface_spot({"entities": view}, surface, "__probe__")


def _work_surfaces(ctx, rid, first=None):
    """Reachable surfaces of a kitchen to set loose things on: `first` (the bound counter) if it is one, then the
    other counters by free area, then other work surfaces of the room (tables, islands) by free area. Never a
    cooktop, an appliance, a sink or a window sill."""
    ents = _ents(ctx)

    def ok(sid):
        rec = ents.get(sid) or {}
        kinds = set(rec.get("is_a") or []) | {rec.get("type")}
        if not rec.get("accepts_items") or rec.get("container") or not (rec.get("surface") or rec.get("surface_size_m")):
            return False
        if rec.get("appliance") or rec.get("heat_source") or rec.get("drain") or rec.get("category") in ("window_sill", "appliance"):
            return False
        return bool(kinds & {"counter", "surface_furniture"}) and _reachable(ctx, sid)
    counters = [c for c in ctx.placer.find("counter", room=rid) if ok(c)]
    others = [s for s in ctx.placer.find("surface_furniture", room=rid) if s not in counters and ok(s)]
    order = sorted(counters, key=lambda s: (-_free_area(ctx, s), s)) + sorted(others, key=lambda s: (-_free_area(ctx, s), s))
    if first in order:
        order.remove(first)
        order.insert(0, first)
    return order


def _bound_ids(ctx):
    out = set()
    for value in ctx.bindings.values():
        for v in (value if isinstance(value, list) else [value]):
            if isinstance(v, str):
                out.add(v)
    return out


def _clutter(ctx, surface):
    """Loose counter dressing that may be put away to free a work area (largest first): small portable things that
    are not countertop appliances, hold nothing a task bound and are not bound themselves."""
    ents = _ents(ctx)
    keep = _bound_ids(ctx)
    out = []
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("location") != surface or rec.get("relation", "on") != "on" or eid in keep:
            continue
        kinds = set(rec.get("is_a") or []) | {rec.get("type")}
        if not rec.get("portable") or rec.get("appliance") or "countertop_appliance" in kinds or rec.get("category") == "appliance":
            continue
        if float(rec.get("mass_kg", 0.0) or 0.0) > 5.0 or any(ents[k].get("location") == eid for k in keep if k in ents):
            continue
        w, d = geometry.footprint_m(rec)
        out.append((-w * d, eid))
    return [eid for _, eid in sorted(out)]


def _stow(ctx, eid, rid):
    """Put a dressing item away in a kitchen cabinet or drawer it fits (never the sink base, where the cleaners
    live). Returns the undo record, or None when no cabinet takes it."""
    ents = _ents(ctx)
    rec = ents[eid]
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    vol = float(rec.get("volume_l", 0.0) or 0.0)
    for cid in ctx.placer.find("openable_storage", room=rid):
        c = ents[cid]
        if not c.get("container") or not c.get("accepts_items") or c.get("sink") or c.get("sink_base") or c.get("locked"):
            continue
        if "sink" in str(c.get("label") or "") or not geometry.fits_inside(size, c.get("inner_size_m")):
            continue
        cap = float(c.get("capacity_l", 0) or 0)
        used = sum(float(e.get("volume_l", 0) or 0) for e in ents.values() if e.get("location") == cid and e.get("relation") == "in")
        if cap > 0 and used + vol > cap + 1e-9:
            continue
        undo = {k: rec.get(k) for k in ("location", "relation", "spos", "shelf")}
        rec["location"], rec["relation"] = cid, "in"
        rec.pop("spos", None)
        if c.get("shelf_heights"):
            rec["shelf"] = 0
        else:
            rec.pop("shelf", None)
        return undo
    return None


def _unstow(ctx, eid, undo):
    rec = _ents(ctx)[eid]
    for key, value in undo.items():
        if value is None:
            rec.pop(key, None)
        else:
            rec[key] = value


def place_on_counters(ctx, rid, type_name, count=1, props=None, first=None, required=True):
    """Set `count` loose items of type_name on the kitchen's work surfaces: each on the first surface of
    _work_surfaces (the bound counter first) where it fits as the type lies, else turned 90 degrees; when no surface
    has room, counter dressing (salt and pepper, a fruit bowl, a bread box ...) is put away in a kitchen cabinet until
    it fits, as a person clears a work area. SpecError when even that fails. required=False: an optional item is
    skipped instead of clearing anything away for it.
    Generated counters vary in length (a dishwasher shortens a run) and other furnishers crowd them, so a spec must
    not assume the bound counter has room."""
    props = dict(props or {})
    probe = ctx.reg.make(type_name, rid, **props)
    size = list(probe.get("size_m") or [0.1, 0.1, 0.1])
    turns = [props.get("rot")] if props.get("rot") is not None or abs(float(size[0]) - float(size[1])) < 1e-9 else [None, 90]
    created = []
    for _ in range(int(count)):
        surfaces = _work_surfaces(ctx, rid, first)
        new = None
        for sid in surfaces:
            rot = next((r for r in turns if _spot(ctx, sid, size, r) is not None), "none")
            if rot != "none":
                new = ctx.placer.on(sid, type_name, **dict(props, **({"rot": rot} if rot else {})))
                break
        for sid in surfaces if new is None and required else []:
            stowed = []
            for item in _clutter(ctx, sid):
                undo = _stow(ctx, item, rid)
                if undo is None:
                    continue
                stowed.append((item, undo))
                rot = next((r for r in turns if _spot(ctx, sid, size, r) is not None), "none")
                if rot != "none":
                    new = ctx.placer.on(sid, type_name, **dict(props, **({"rot": rot} if rot else {})))
                    break
            if new is not None:
                break
            for item, undo in reversed(stowed):
                _unstow(ctx, item, undo)
        if new is None:
            if required:
                raise SpecError("No counter space for {} in {}".format(type_name, rid))
            continue
        created.append(new)
    return created


@scenario_helper("plumbing_liquids.counter_items")
def counter_items(ctx, type="generic_item", count=1, var="items", props=None, room=None, counter=None, required=True):
    """Loose things to start on the kitchen counters (place_on_counters): binds `var` to the id (count 1) or the
    list of ids plus `var_1`, `var_2` ... like the spawn op."""
    rid = _room(ctx, room, "kitchen")
    if rid is None and counter in _ents(ctx):
        from ...kernel.predicates import root_room
        rid = root_room(ctx.state(), counter)
    if rid is None:
        raise SpecError("House has no kitchen")
    created = place_on_counters(ctx, rid, type, count=count, props=props, first=counter, required=required)
    if int(count) == 1:
        return {var: created[0] if created else None}
    out = {var: created}
    for i, eid in enumerate(created, 1):
        out["{}_{}".format(var, i)] = eid
    return out


def _reachable(ctx, eid):
    from .furnish import reachable
    return reachable(ctx.placer, eid)


def _fix_reach(ctx, *eids):
    """Bound wall controls must be usable: relocate unreachable ones (or ones a floor-standing piece now hides)."""
    from .furnish import relocate_reachable
    for eid in eids:
        if eid and eid in _ents(ctx):
            relocate_reachable(ctx.placer, eid)


def _neighbour_rooms(ctx, rid):
    out = []
    for _, conn in sorted(ctx.config.get("connections", {}).items()):
        rooms = conn.get("rooms", [])
        if rid not in rooms or len(rooms) != 2:
            continue
        other = rooms[1] if rooms[0] == rid else rooms[0]
        if other in ctx.config["rooms"] and bool(ctx.config["rooms"][other].get("outdoor")) == bool(ctx.config["rooms"][rid].get("outdoor")) \
                and other not in out:
            out.append(other)
    return out


def _floor_item(ctx, rid, type_name, near=None, required=False, neighbours=True, **props):
    """A loose floor item on a cell that cuts no hazard-free route or only access, outside door clearance and
    reachable without stepping on anything (furnish.place_floor_item: Placer.floor_item on a keep_access cell). A room
    with no such cell (a tiny half bath or closet) passes it to the room next door; with required=True the last
    resort is Placer.floor_item's own fallback in the room itself (cuts no plain route). None when nothing works."""
    from .furnish import place_floor_item
    rooms = [rid] + (_neighbour_rooms(ctx, rid) if neighbours and rid in ctx.config["rooms"] else [])
    eid = place_floor_item(ctx.placer, rooms, type_name, near=near, **props)
    if eid is None and required and rid in ctx.config["rooms"]:
        eid = ctx.placer.floor_item(rid, type_name, near=near, **props)
    return eid


def _remove(ctx, eid):
    ents = _ents(ctx)
    doomed = [eid] + [e for e in sorted(ents) if ents[e].get("location") == eid]
    for e in doomed:
        ents.pop(e, None)


def _sink_for(ctx, rid, sink_type):
    ents = _ents(ctx)
    sink = next((s for s in ctx.placer.find(sink_type, room=rid) if _reachable(ctx, s)), None)
    if sink:
        return sink
    for old in ctx.placer.find(sink_type, room=rid):
        for v in [e for e in sorted(ents) if old in (ents[e].get("feeds") or [])]:
            ents.pop(v, None)
        cab = ents[old].get("cabinet")
        if cab in ents:
            ents[cab].pop("sink", None)
            ents[cab].pop("sink_base", None)
        ents.pop(old, None)
    from .furnish import _sink_on, _floor_cabinet
    cab = _floor_cabinet(ctx.placer, rid, ("sink", "vanity"))
    if cab is None:
        for _ in range(6):
            cab = _floor_spot(ctx, rid, "cabinet", label="sink base cabinet")
            if cab is None or _reachable(ctx, cab):
                break
            _remove(ctx, cab)
            cab = None
    if cab is None:
        raise SpecError("No room for a sink in " + rid)
    sink = _sink_on(ctx.placer, rid, cab, sink_type, ctx.rng)
    if sink is None:
        raise SpecError("Could not mount a sink in " + rid)
    return sink


def _stops(ctx, sink):
    ents = _ents(ctx)
    cab = ents[sink].get("cabinet")
    hot = cold = None
    for eid in sorted(ents):
        rec = ents[eid]
        if sink in (rec.get("feeds") or []) and rec.get("valve_kind"):
            if rec.get("line") == "hot":
                hot = eid
            elif rec.get("line") == "cold":
                cold = eid
    return cab, hot, cold


@scenario_helper("plumbing_liquids.kitchen_sink")
def kitchen_sink(ctx, room=None):
    rid = _room(ctx, room, "kitchen")
    if rid is None:
        raise SpecError("House has no kitchen")
    sink = _sink_for(ctx, rid, "kitchen_sink")
    cab, hot, cold = _stops(ctx, sink)
    ents = _ents(ctx)
    for valve in (hot, cold):
        if valve:
            ents[valve].pop("_seized", None)
    trap = next((e for e in sorted(ents) if ents[e].get("location") == cab and ents[e].get("type") == "drain_trap"), None)
    counter = _roomiest(ctx, [c for c in ctx.placer.find("counter", room=rid) if _reachable(ctx, c)])
    if counter is None:
        for _ in range(6):
            counter = _floor_spot(ctx, rid, "counter", size_m=[1.2, 0.6, 0.9])
            if counter is None or _reachable(ctx, counter):
                break
            _remove(ctx, counter)
            counter = None
    if counter is None:
        raise SpecError("No reachable counter in " + rid)
    ents[sink].pop("_worn", None)
    return {"sink": sink, "sink_cabinet": cab, "hot_stop": hot, "cold_stop": cold, "trap": trap, "counter": counter, "kitchen": rid}


@scenario_helper("plumbing_liquids.bathroom")
def bathroom(ctx, room=None, faucet=None, worn=None, reversed=None, nonpotable=None, running=None, tub=False):
    rid = _room(ctx, room, "bathroom", "half_bath")
    if rid is None:
        raise SpecError("House has no bathroom")
    sink = _sink_for(ctx, rid, "bathroom_sink")
    ents = _ents(ctx)
    rec = ents[sink]
    if faucet == "two_handle":
        rec["faucet"] = dict(rec["faucet"], kind="two_handle", marks={"left": "hot", "right": "cold"})
        rec["settings"] = {"left": 0.0, "right": 0.0}
        rec["setting_options"] = {"left": {"min": 0.0, "max": 1.0}, "right": {"min": 0.0, "max": 1.0}}
        rec["control_kinds"] = {"left": "handle", "right": "handle"}
        rec.pop("mech", None)
        rec["_hot_handle"] = "left"
    elif faucet == "single_lever":
        rec["faucet"] = dict(rec["faucet"], kind="single_lever")
        rec["settings"] = {"flow": 0.0, "mix": 0.0}
        rec["setting_options"] = {"flow": {"min": 0.0, "max": 1.0}, "mix": {"min": -1.0, "max": 1.0}}
        rec["control_kinds"] = {"flow": "lever", "mix": "lever"}
        rec["mech"] = {"lever": {"kind": "mixer", "max_lpm": 4.5}}
        rec.pop("_hot_handle", None)
    if reversed is not None and rec["faucet"].get("kind") == "two_handle":
        rec["_hot_handle"] = "right" if reversed else "left"
    if worn is not None:
        if worn:
            rec["_worn"] = {"drip_lpm": 0.012}
        else:
            rec.pop("_worn", None)
    if nonpotable is not None:
        if nonpotable:
            rec["_nonpotable"] = True
        else:
            rec.pop("_nonpotable", None)
    if running:
        if rec["faucet"].get("kind") == "two_handle":
            rec["settings"].update(left=0.5, right=0.5)
        else:
            rec["settings"].update(flow=0.6, mix=0.0)
    cab, hot, cold = _stops(ctx, sink)
    for valve in (hot, cold):
        if valve:
            ents[valve].pop("_seized", None)
    derive(ctx, sink)
    toilet = _first(ctx, "toilet", room=rid)
    if toilet is None:
        toilet = _floor_spot(ctx, rid, "toilet")
    tub_id = _first(ctx, "bathtub", room=rid)
    if tub and tub_id is None:
        tub_id = _floor_spot(ctx, rid, "bathtub")
    return {"bath_sink": sink, "vanity": cab, "bath_hot": hot, "bath_cold": cold, "toilet": toilet, "tub": tub_id, "bathroom": rid}


@scenario_helper("plumbing_liquids.water_service")
def water_service(ctx, valve_kind=None, seized=None, heater_c=None):
    ents = _ents(ctx)
    main = next((e for e in sorted(ents) if ents[e].get("main")), None)
    room = None
    if main is None:
        from .furnish import SERVICE_ROOMS
        for kind in SERVICE_ROOMS:
            found = ctx.placer.rooms(kind)
            if found:
                room = found[0]
                break
        if room is None:
            raise SpecError("No room for the main shutoff")
        main = ctx.placer.mount(room, "main_shutoff_valve", height=0.6, eid="main_water_valve")
    room = ents[main]["location"]
    if valve_kind == "ball":
        ents[main].update(valve_kind="ball", turns_to_close=0.25, mech={})
    elif valve_kind == "gate":
        ents[main].update(valve_kind="gate", turns_to_close=6.0,
                          mech={"rotary": {"setting": "position", "per_turn": 0.1667, "cw": "decrease", "max_turns_past_stop": 0.25}})
    if seized is not None:
        ents[main]["_seized"] = bool(seized)
    meter = next((e for e in sorted(ents) if ents[e].get("type") == "water_meter"), None)
    heater = next((e for e in sorted(ents) if ents[e].get("type") == "water_heater"), None)
    inlet = next((e for e in sorted(ents) if ents[e].get("feeds_hot")), None)
    if heater is None:
        heater = _floor_spot(ctx, room, "water_heater", eid="water_heater_1")
    if heater is None:
        # no floor space: a wall-hung tankless unit
        from .furnish import TANKLESS, mount_clear
        heater = mount_clear(ctx.placer, room, "water_heater", height=1.2, eid="water_heater_1", **TANKLESS)
    if heater and inlet is None:
        hroom = ents[heater]["location"]
        inlet = ctx.placer.mount(hroom, "shutoff_valve", height=1.6, eid="water_heater_inlet_valve", label="water heater cold inlet valve",
                                 line="cold", feeds_hot=True, valve_kind="gate", turns_to_close=5.0, serves_label="water heater")
    if heater and heater_c is not None:
        ents[heater]["settings"]["temp_c"] = float(heater_c)
        ents[heater]["tank_temp_c"] = float(heater_c)
    _fix_reach(ctx, main, meter, inlet)
    return {"main": main, "meter": meter, "heater": heater, "heater_inlet": inlet, "service_room": room}


@scenario_helper("plumbing_liquids.toilet_trouble")
def toilet_trouble(ctx, kind="paper", room=None):
    out = bathroom(ctx, room=room, tub=(kind == "sewer"))
    ents = _ents(ctx)
    toilet = out["toilet"]
    if toilet is None:
        raise SpecError("No toilet could be placed")
    rid = out["bathroom"]
    rec = ents[toilet]
    rec["open"] = True
    rec["_supply_seized"] = False
    rec["_tank"] = dict(rec.get("_tank") or {}, fill_valve_ok=True)
    clog = {"kind": "paper", "amount": round(ctx.rng.uniform(0.8, 0.9), 3), "depth_m": 0.15}
    rec["bowl_l"] = round(ctx.rng.uniform(3.2, 3.8), 2)
    toy = cleaner = None
    if kind == "rising":
        clog["amount"] = 0.95
        rec["bowl_l"] = 5.6
        rec["_tank"]["fill_valve_ok"] = False
        rec["_tank"]["stuck_lpm"] = 1.5
    elif kind == "object":
        toy = ctx.placer.make("generic_item", toilet, label="toy car", mass_kg=0.05, size_m=[0.07, 0.04, 0.03], volume_l=0.08, material="plastic",
                              portable=True, owner=None)
        ents[toy]["relation"] = "in"
        clog = {"kind": "object", "amount": 0.92, "object": toy, "object_visible": True, "depth_m": 0.1}
        rec["bowl_l"] = 3.0
    elif kind == "chemical":
        clog["chemical"] = True
        cleaner = _floor_item(ctx, rid, "generic_item", required=True, label="drain cleaner (sodium hydroxide)", hazards=["corrosive", "toxic"],
                                        product="drain_cleaner", amount_ml=200.0, size_m=[0.1, 0.07, 0.25], mass_kg=0.6,
                                        label_text="DRAIN CLEANER. Corrosive. Never plunge or snake after use: splash burns.")
    elif kind == "sewer":
        clog = {"kind": "sewer", "amount": 0.97, "depth_m": 6.0}
        rec["bowl_l"] = 4.2
        tub = out.get("tub")
        if tub:
            ents[tub]["liquid"] = "sewage"
            ents[tub]["liquid_l"] = 1.5
            ents[tub]["_mix"] = {"sewage": 1.5}
    rec["_clog"] = clog
    near = rec.get("pos")
    # the household's plunger when it stands on the floor; else a new one beside the toilet (or the one under the vanity)
    plunger = next((e for e in ctx.placer.find("flange_plunger", room=rid) if ents[e].get("location") == rid), None)
    _fix_reach(ctx, plunger)
    if plunger is None or not _reachable(ctx, plunger):
        plunger = _floor_item(ctx, rid, "flange_plunger", near=near, required=True)
        if plunger is None:
            plunger = next((e for e in ctx.placer.find("flange_plunger", room=rid)), None)
    if plunger is None:
        raise SpecError("No space for a plunger in " + rid)
    cup = _floor_item(ctx, rid, "cup_plunger", near=near, required=True)
    gloves = _floor_item(ctx, rid, "generic_item", near=near, required=True, label="rubber gloves", wearable=True, gear_kind="rubber_gloves",
                         size_m=[0.2, 0.12, 0.02], mass_kg=0.1)
    return {"toilet": toilet, "plunger": plunger, "cup_plunger": cup, "gloves": gloves, "toy": toy, "cleaner": cleaner, "bathroom": rid,
            "bath_sink": out["bath_sink"], "tub": out.get("tub")}


@scenario_helper("plumbing_liquids.burst_pipe")
def burst_pipe(ctx, rate_lpm=20.0, line="cold"):
    svc = water_service(ctx)
    room = svc["service_room"]
    ents = _ents(ctx)
    pipe = next((e for e in ctx.placer.find("water_pipe", room=room)), None)
    if pipe is None:
        pipe = ctx.placer.mount(room, "water_pipe", height=2.1, label="{} water supply pipe".format(line))
    from .furnish import lay_pipe
    lay_pipe(ctx.placer, pipe)         # along its wall, on the grid, reachable
    # the spray comes down on open floor near the split (not neatly into a bucket someone left standing there)
    cell = geometry.free_floor_cell(ctx.state(), room, near=ents[pipe]["pos"], ignore=(pipe,), clearance=False) or ents[pipe]["pos"]
    ents[pipe]["line"] = line
    ents[pipe]["_leak"] = {"component": "split_pipe", "rate_lpm": float(rate_lpm), "when": "pressurized", "line": line, "fixture": pipe,
                           "to": {"room": room, "cell": list(cell)}, "severity": "spray", "main_only": True}
    ents[pipe]["supplied_by"] = "main" if line == "cold" else "heater"
    out = dict(svc)
    out.update(pipe=pipe)
    return out


LEAK_SOURCES = ("kitchen_supply", "toilet_supply", "heater_relief", "bath_trap")


@scenario_helper("plumbing_liquids.hidden_leak")
def hidden_leak(ctx, source=None, rate_lpm=None, preleak_l=0.8, witness=True):
    """One hidden leak with only downstream evidence (puddle downhill of the source, damp cabinet)."""
    source = source or ctx.rng.choice(LEAK_SOURCES)
    ents = _ents(ctx)
    out = {"leak_kind": source}
    if source == "kitchen_supply":
        ks = kitchen_sink(ctx)
        src, fixture, valve = ks["cold_stop"], ks["sink"], ks["cold_stop"]
        rid = ks["kitchen"]
        leak = {"component": "cold_supply_compression_fitting", "rate_lpm": rate_lpm or 0.06, "when": "pressurized", "line": "cold",
                "fixture": fixture, "to": {"entity": ks["sink_cabinet"]}}
        ents[ks["sink_cabinet"]]["cabinet_water_l"] = 0.8
        ents[ks["sink_cabinet"]]["moisture"] = "wet"
        puddle_near = fixture
    elif source == "toilet_supply":
        b = bathroom(ctx)
        src = fixture = b["toilet"]
        valve = b["toilet"]
        ents[src]["_supply_seized"] = False
        rid = b["bathroom"]
        drip = _reachable_cell(ctx, rid, fixture) or list(ents[fixture]["pos"])
        leak = {"component": "supply_line", "rate_lpm": rate_lpm or 0.05, "when": "pressurized", "line": "cold", "fixture": fixture,
                "to": {"room": rid, "cell": drip}}
        puddle_near = fixture
    elif source == "heater_relief":
        svc = water_service(ctx)
        src = fixture = svc["heater"]
        if src is None:
            return hidden_leak(ctx, "kitchen_supply", rate_lpm, preleak_l, witness)
        valve = None
        rid = ents[src]["location"]
        drip = _reachable_cell(ctx, rid, src) or list(ents[src]["pos"])
        leak = {"component": "temperature_pressure_relief_valve", "rate_lpm": rate_lpm or 0.03, "when": "always", "fixture": fixture,
                "to": {"room": rid, "cell": drip}}
        puddle_near = fixture
    else:
        b = bathroom(ctx)
        sink = b["bath_sink"]
        cab = b["vanity"]
        trap = next((e for e in sorted(ents) if ents[e].get("location") == cab and ents[e].get("type") == "drain_trap"), None)
        if trap is None:
            trap = ctx.placer.inside(cab, "drain_trap", shelf=0)
        src, fixture, valve = trap, sink, None
        rid = b["bathroom"]
        out["vanity"] = cab
        leak = {"component": "p_trap_slip_nut", "rate_lpm": rate_lpm or 0.4, "when": "draining", "fixture": sink, "to": {"entity": cab}}
        ents[cab]["moisture"] = "damp"
        ents[cab]["water_damage"] = 0.12
        puddle_near = sink
    ents[src]["_leak"] = leak
    ents[src]["_leaked_l"] = float(preleak_l)
    # evidence downstream: a puddle that migrated a cell or two (floors are rarely level)
    room = ctx.config["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    state = ctx.state()
    _, cells = geometry.anchor(dict(state, robot={"room": rid, "hands": {}, "pos": None}), puddle_near)
    base = list(cells[0]) if cells else [cols // 2, rows // 2]
    cell = geometry.free_floor_cell(state, rid, near=(min(cols - 1, base[0] + 2), min(rows - 1, base[1] + 1))) or base
    puddle = None
    if source != "bath_trap" or preleak_l > 0:
        puddle = ctx.placer.make("generic_item", rid, label="puddle of water", category="spill", liquid="water", liquid_l=round(preleak_l * 0.5, 3),
                                 floor_hazard="wet", portable=False, blocks_path=False, mass_kg=0.0, volume_l=0.0, area_m2=0.35,
                                 size_m=[0.6, 0.6, 0.002])
        ents[puddle]["pos"] = list(cell)
        room.setdefault("wet_cells", []).append(list(cell))
    # a decoy that looks wet but is not a leak: a sweating cold pipe or a damp dog bowl, when available
    decoy = next((e for e in sorted(ents) if ents[e].get("condensation_prone")), None)
    if witness:
        adults = sorted(pid for pid, p in ctx.config.get("people", {}).items() if p.get("role", "adult") == "adult")
        if adults:
            hints = ctx.config.setdefault("plumbing", {}).setdefault("hints", {})
            hints.setdefault(adults[0], {})["where_noticed"] = "I saw a puddle in the {}".format(room.get("label", rid).lower())
    catcher = None
    for cand in [rid] + [r for kind in ("utility_closet", "laundry_room", "garage", "basement", "kitchen", "hallway") for r in ctx.placer.rooms(kind)]:
        catcher = _floor_item(ctx, cand, "utility_bucket", label="empty bucket")
        if catcher:
            break
    out.update(leak_source=src, leak_fixture=fixture, leak_valve=valve, puddle=puddle, leak_room=rid, decoy=decoy, catcher=catcher)
    return out


@scenario_helper("plumbing_liquids.yard_hose")
def yard_hose(ctx, attached=True, nozzle=True, spigot_on=False, pressurized=False, water_l=0.0, kinks=0, frost_free=None, lying=True,
              storage_room=None, room=None):
    rid = _room(ctx, room, "backyard", "front_yard", "garden", "patio")
    if rid is None:
        raise SpecError("House has no yard (add backyard to house.require)")
    ents = _ents(ctx)
    spigot = next((e for e in ctx.placer.find("outdoor_spigot", room=rid)), None)
    if spigot is None:
        spigot = ctx.placer.mount(rid, "outdoor_spigot", height=0.45)
    srec = ents[spigot]
    if frost_free is not None:
        srec["frost_free"] = bool(frost_free)
    interior = srec.get("interior_valve")
    if not srec.get("frost_free") and not interior:
        from .furnish import _service_room
        inside = _service_room(ctx.placer)
        if inside:
            interior = ctx.placer.mount(inside, "shutoff_valve", height=1.5, label="interior shutoff for the outdoor spigot", line="cold",
                                        feeds=[spigot], serves_label="outdoor spigot")
            srec["interior_valve"] = interior
    if srec.get("frost_free"):
        srec.pop("interior_valve", None)
        interior = None
    srec["settings"]["valve"] = 1.0 if spigot_on else 0.0
    for old in [e for e in sorted(ents) if ents[e].get("hose") and ents[e].get("plumb")]:
        for child in [c for c in sorted(ents) if ents[c].get("location") == old]:
            del ents[child]
        del ents[old]
    hanger = next((e for e in ctx.placer.find("hose_hanger", room=rid)), None)
    if hanger is None:
        hanger = ctx.placer.mount(rid, "hose_hanger", height=1.0, cell=srec["pos"])
    near = srec["pos"]
    props = dict(attached_to=spigot if attached else None, kinks=int(kinks), coiled=not lying, pressurized=bool(pressurized))
    if water_l:
        props.update(liquid="hose_water", liquid_l=float(water_l), _mix={"hose_water": float(water_l)})
    hose = _floor_item(ctx, rid, "garden_hose", near=[min(near[0] + 2, geometry.grid_size(ctx.config["rooms"][rid])[0] - 1), near[1]],
                       required=True, neighbours=False, **props)
    if hose is None:
        raise SpecError("No space for the hose")
    noz = None
    if nozzle:
        noz = ctx.placer.make("hose_nozzle", hose)
        ents[noz]["relation"] = "attached"
        ents[hose]["nozzle"] = noz
    # daytime with people home: the doors between the yard and the house are unlocked
    for conn in ctx.config.get("connections", {}).values():
        door = conn.get("door")
        if door in ents and ents[door].get("locked") and any(ctx.config["rooms"][r].get("outdoor") for r in conn["rooms"]):
            ents[door]["locked"] = False
    store = None
    if storage_room:
        srid = _room(ctx, storage_room, storage_room)
        if srid:
            store = next((e for e in ctx.placer.find("hose_hanger", room=srid)), None) or ctx.placer.mount(srid, "hose_hanger", height=1.0)
            _fix_reach(ctx, store)
    return {"spigot": spigot, "hose": hose, "nozzle": noz, "hanger": hanger, "interior_valve": interior, "yard": rid, "store_hanger": store}


@scenario_helper("plumbing_liquids.irrigation")
def irrigation(ctx, start_in_h=1.0, minutes=20, room=None, rain=False):
    rid = _room(ctx, room, "backyard", "front_yard", "garden")
    if rid is None:
        raise SpecError("House has no yard (add backyard to house.require)")
    ents = _ents(ctx)
    ctrl = next((e for e in sorted(ents) if ents[e].get("irrigation_controller")), None)
    if ctrl is None:
        host = _room(ctx, None, "garage", "laundry_room", "utility_closet", "basement", "kitchen")
        ctrl = ctx.placer.mount(host, "irrigation_controller", height=1.4, eid="sprinkler_controller")
    _fix_reach(ctx, ctrl)
    heads = [h for h in (ctx.placer.floor_item(rid, "sprinkler_head") for _ in range(2)) if h]
    start = clock.start_of({"clock": ctx.config.get("clock", {})})
    hour = start.hour + start.minute / 60.0 + float(start_in_h)
    ents[ctrl]["zones"] = {"zone_1": heads}
    ents[ctrl]["program"] = [{"zone": "zone_1", "start_h": round(hour % 24, 3), "minutes": int(minutes), "days": ["daily"]}]
    ents[ctrl]["settings"] = {"mode": "auto", "rain_delay_h": 24, "manual_zone": "none"}
    if rain:
        ctx.config.setdefault("weather", {}).update(condition="rain", rain_mm_24h=round(ctx.rng.uniform(12, 30), 1), temperature_c=14.0)
        ctx.config.setdefault("plumbing", {})["wet_until_s"] = 86400.0      # the lawn stays saturated for a day
    else:
        ctx.config.setdefault("weather", {}).update(condition="clear", rain_mm_24h=0.0, temperature_c=round(ctx.rng.uniform(24, 31), 1))
    return {"controller": ctrl, "zone": "zone_1", "heads": heads, "irrigation_yard": rid}


@scenario_helper("plumbing_liquids.freezer")
def freezer(ctx, room=None):
    rid = _room(ctx, room, "kitchen")
    ents = _ents(ctx)
    in_room = set(ctx.placer.find(None, room=rid))
    frz = next((e for e in sorted(in_room) if ents[e].get("internal_temp_c") is not None and float(ents[e]["internal_temp_c"]) <= -10
                and ents[e].get("container")), None)
    props = dict(label="upright freezer", category="freezer", portable=False, blocks_path=True, accepts_items=True, container=True,
                 openable=True, open=False, internal_temp_c=-18.0, capacity_l=200.0, inner_size_m=[0.6, 0.55, 1.2], size_m=[0.7, 0.7, 1.75],
                 shelf_heights=[0.3, 0.7, 1.1], mass_kg=60.0)
    candidates = [rid] + [r for kind in ("pantry", "dining_room", "laundry_room", "garage", "basement", "mudroom", "living_room", "hallway")
                          for r in ctx.placer.rooms(kind) if r != rid]
    for cand in candidates if frz is None else []:
        frz = _floor_spot(ctx, cand, "generic_item", **props)
        if frz is not None:
            break
    if frz is None:
        raise SpecError("No room for a freezer")
    peas = ctx.placer.inside(frz, "generic_item", label="bag of frozen peas", shelf=1, size_m=[0.3, 0.2, 0.06], mass_kg=0.5, volume_l=0.6,
                             temperature_c=-18.0, accepts_items=True, surface=True, soft=True, food_safe=True)
    return {"freezer": frz, "peas": peas}


@scenario_helper("plumbing_liquids.drinkware")
def drinkware(ctx, room=None):
    ks = kitchen_sink(ctx, room)
    rid = ks["kitchen"]
    ents = _ents(ctx)
    cab = next((e for e in ctx.placer.find("upper_cabinet", room=rid) if _reachable(ctx, e)), None)
    if cab is None:
        cab = next((e for e in ctx.placer.find("cabinet", room=rid) if not ents[e].get("sink_base") and _reachable(ctx, e)), None)
    common = dict(category="dish", accepts_items=True, container=True, serving=True, food_safe=True, portable=True, drinking=True)
    glass = plastic = None
    if cab:
        glass = ctx.placer.inside(cab, "generic_item", label="drinking glass", material="glass", fragile=True, capacity_l=0.35,
                                  inner_size_m=[0.07, 0.07, 0.12], size_m=[0.08, 0.08, 0.13], mass_kg=0.25, volume_l=0.5, shelf=0, **common)
        plastic = ctx.placer.inside(cab, "generic_item", label="plastic cup", material="plastic", fragile=False, capacity_l=0.3,
                                    inner_size_m=[0.07, 0.07, 0.1], size_m=[0.08, 0.08, 0.11], mass_kg=0.04, volume_l=0.4, shelf=0, **common)
    counter = ks["counter"]
    if glass is None and counter:
        # no cupboard: the glasses stand on a counter with room (place_on_counters), the bound one first
        glass = place_on_counters(ctx, rid, "generic_item", first=counter, props=dict(label="drinking glass", material="glass", fragile=True,
                                  capacity_l=0.35, size_m=[0.08, 0.08, 0.13], mass_kg=0.25, **common))[0]
        plastic = place_on_counters(ctx, rid, "generic_item", first=counter, props=dict(label="plastic cup", material="plastic", fragile=False,
                                    capacity_l=0.3, size_m=[0.08, 0.08, 0.11], mass_kg=0.04, **common))[0]
    dirty = ctx.placer.make("generic_item", ks["sink"], label="used glass", material="glass", fragile=True, capacity_l=0.35,
                            size_m=[0.08, 0.08, 0.13], mass_kg=0.25, soil={"food": 0.3, "sticky": 0.4}, **common)
    ents[dirty]["relation"] = "in"
    return {"glass": glass, "plastic_cup": plastic, "dirty_glass": dirty, "cup_cabinet": cab, "sink": ks["sink"], "kitchen": rid,
            "counter": counter}


@scenario_helper("plumbing_liquids.dirty_bucket")
def dirty_bucket(ctx, room=None, liters=10.0, chemical=True):
    rid = _room(ctx, room, "kitchen")
    product = "bleach" if chemical else None
    props = dict(liquid="mop_water" if chemical else "dirty_water", liquid_l=float(liters), _mix={"dirty_water": float(liters)}, cl_soil_g=40.0)
    if product:
        props["cl_chem"] = {product: round(liters * 8.0, 1)}
    if rid is None:
        raise SpecError("House has no kitchen")
    bucket = _floor_item(ctx, rid, "utility_bucket", required=True, **props)
    if bucket is None:
        raise SpecError("No floor space for the bucket")
    return {"bucket": bucket, "bucket_room": _ents(ctx)[bucket]["location"]}


@scenario_helper("plumbing_liquids.clear_door_sweeps")
def clear_door_sweeps(ctx, rooms=None, fronts=None):
    """Household premise "nothing stands behind the doors": free-standing floor items at least 0.15 m tall standing in
    a room's door clearance (geometry.door_clearance: doorway cells + neighbours, hinged leaves, overhead thresholds)
    move to the nearest cell that cuts no route (furnish.floor_cell; in a closet too small for that, to the next room).
    Since kernel v2.3 generated houses keep tall loose items out of door clearance, so this only matters for items a
    spec or an older furnisher put there. `fronts`: floor cabinets the plan opens (the sink base): mechanisms sweeps
    every side of a low cabinet's doors, so the floor cells around them are cleared too. Tasks about doors or cabinet
    doors themselves should not use this."""
    from .furnish import door_sweep_cells, floor_cell
    ents = _ents(ctx)
    wanted = set(rooms) if rooms else set(ctx.config["rooms"])
    extra = {}
    for front in (fronts if isinstance(fronts, list) else [fronts] if fronts else []):
        if front not in ents:
            continue
        frid, foot = geometry.anchor(dict(ctx.state(), robot={"room": None, "hands": {}, "pos": None}), front)
        if frid and foot:
            cells = {(x + dx, y + dy) for x, y in foot for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))} - set(foot)
            extra.setdefault(frid, set()).update(cells)
            wanted.add(frid)

    def avoid(room):
        return door_sweep_cells(ctx.config, room) | extra.get(room, set())

    def clear(eid):
        rec = ents[eid]
        return not set(geometry.footprint(rec) or [tuple(rec["pos"])]) & avoid(rec["location"])

    moved = []
    for eid in sorted(ents):
        rec = ents[eid]
        rid = rec.get("location")
        if rid not in wanted or rid not in ctx.config["rooms"] or not rec.get("pos"):
            continue
        if rec.get("mounted") or geometry.blocks(rec) or rec.get("ground_layer") or "connects" in rec or rec.get("toppled") or rec.get("docked"):
            continue
        if rec.get("category") in ("spill", "floor", "wall", "light", "switch", "window", "door", "gate"):
            continue
        size = rec.get("size_m") or [0.1, 0.1, 0.1]
        if len(size) < 3 or float(size[2] or 0.0) < 0.15:
            continue        # flat things (mats, drains) are not knocked over by a door
        if clear(eid):
            continue
        before = list(rec["pos"])
        options = [rid] + [r for r in _neighbour_rooms(ctx, rid) if not ctx.config["rooms"][r].get("shared")]
        for room in options:
            cell = floor_cell(ctx.placer, room, rec, near=tuple(before) if room == rid else None, ignore=(eid,), avoid=extra.get(room, ()))
            if cell is not None:
                rec["location"], rec["pos"] = room, list(cell)
                moved.append(eid)
                break
    return {"cleared_items": moved}
