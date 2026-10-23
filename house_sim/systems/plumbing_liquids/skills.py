"""Reference skills (privileged routing only; every state change goes through runner.do).

  plumbing_liquids.goto(target)                      approach, stepping around wet/slippery cells when possible
  plumbing_liquids.set_tap_temp(fixture, target_c, tol=2, flow=1)   open a tap and adjust it by measuring the water
  plumbing_liquids.fill_sink(sink, stopper, target_c, level, soap, soap_ml)
  plumbing_liquids.shut_tap(fixture, report_to=None) close to snug, watch 60 s, report a persistent drip
  plumbing_liquids.isolate(fixture)                  open its cabinet and close the stops that feed it
  plumbing_liquids.close_valve(valve)                go to a valve and close it fully
  plumbing_liquids.test_tap(fixture)                 open/close a tap to confirm there is (no) water
  plumbing_liquids.unclog_toilet(toilet, plunger, strokes=20, test=True)
  plumbing_liquids.retrieve_from_toilet(toilet, item, gloves, into)
  plumbing_liquids.store_hose(hose, spigot, hanger, disconnect=False, drain=True)
  plumbing_liquids.fill_at(container, source, amount_l=None, to_frac=None, until=None, temp="cold", flow=None)
  plumbing_liquids.serve_water(person, vessel, source, amount_l)
  plumbing_liquids.dump(container, dest)            carry a container and empty it into a destination
  plumbing_liquids.fill_ice_tray(tray, sink, freezer, frac=0.9, shelf=1)
  plumbing_liquids.inspect_report(target, field, value, to=None, where=None)
  plumbing_liquids.contain_leak(source, catcher)    put a catch container where a leak's water lands
"""
import json

from ...kernel import camera, geometry, sight
from ...kernel.plugins import skill
from ...tasks import skills as base
from . import common as C
from . import fixtures as F
from . import liquids as L

WET_KINDS = ("wet", "slip", "ice")


def _err(message):
    from ...tasks.runner import PlanError
    return PlanError(message)


def _held(r):
    return [h for h in dict.fromkeys(r.state["robot"]["hands"].values()) if h]


def _goals(state, target):
    """(room, goal cells, ignore_sources) for approaching target in the robot's room, like the core approach action:
    within one cell, two for overhead mounts; hazard rings the target itself sources never block."""
    rid = state["robot"]["room"]
    room, cells = geometry.anchor(state, target)
    if room != rid or not cells:
        return None, None, ()
    radius = geometry.REACH_CELLS + (1 if geometry.mounted_overhead(state, target) else 0)
    goals = geometry.near_cells(cells, state["rooms"][rid], radius) - set(cells)
    return rid, goals, tuple(geometry.target_sources(state, target))


def _wet_here(state, rid):
    return any(kind in WET_KINDS for items in geometry.hazard_cells(state, rid).values() for kind, _ in items)


def _avoid(state, target):
    if target not in state["entities"] or not _wet_here(state, state["robot"]["room"]):
        return None
    rid, goals, sources = _goals(state, target)
    if not goals:
        return None
    start = tuple(state["robot"].get("pos") or (0, 0))
    return _route_avoid(state, rid, start, goals, sources)


def go_room(r, room):
    """Room-to-room route that steps around wet/slippery cells whenever a dry path to the next doorway exists."""
    route = base.room_route(r.state, r.state["robot"]["room"], room)
    if route is None:
        raise _err("No route to " + room)
    for nxt, cid in route:
        state = r.state
        here = state["robot"]["room"]
        conn = state["connections"][cid]
        door = conn.get("door")
        if door and not state["entities"][door].get("open") and all(state["robot"]["hands"].values()):
            # both hands full: set the load down, open the door, pick it back up
            held = _held(r)
            for eid in held:
                r.do("place", target=eid)
            base.approach(r, door)
            if r.state["entities"][door].get("locked"):
                r.do("unlock", target=door)
            r.do("open", target=door)
            for eid in held:
                low = _crouch_for(r, eid)
                base.approach(r, eid)
                low = _crouch_for(r, eid) or low
                rec = r.state["entities"][eid]
                r.do("pick_up", target=eid, **({"hand": "both"} if rec.get("two_handed") or C.num(rec.get("mass_kg")) + L.liters(rec) > 8 else {}))
                if low:
                    _stand(r)
            state = r.state
        if door and not state["entities"][door].get("open"):
            base.approach(r, door)
            if r.state["entities"][door].get("locked"):
                r.do("unlock", target=door)
            r.do("open", target=door)
            state = r.state
        cell = conn.get("cells", {}).get(here)
        start = tuple(state["robot"].get("pos") or (0, 0))
        avoid = _route_avoid(state, here, start, [tuple(cell)]) if cell is not None else None
        dry = avoid is not None
        wet_here = any(kind in WET_KINDS for items in geometry.hazard_cells(state, here).values() for kind, _ in items)
        wet_next = any(kind in WET_KINDS for items in geometry.hazard_cells(state, nxt).values() for kind, _ in items)
        speed = "careful" if (wet_here and not dry) or wet_next else None
        if conn.get("kind") == "stairs" or conn.get("stairs"):
            speed = "careful"     # winders, a carried load and items on the treads all need a careful pace on stairs
        if door and C.num(state["entities"][door].get("open_angle_deg"), 90.0) < 75.0:
            speed = "careful"     # something stops the door half open: squeeze through instead of slamming it
        base.go_room(r, nxt, avoid=avoid, speed=speed)


def _route_avoid(state, rid, start, goals, ignore_sources=()):
    """The most cautious hazard list (core SAFE_AVOID, then its relaxed lists) that also keeps the robot off wet or
    icy cells and still has a route; None when no dry route exists (core routing then picks its own list).
    Avoiding only the wet kinds would walk the robot over robot vacuums, cords and toys."""
    lists = [list(getattr(base, "SAFE_AVOID", []))] + [list(k) for k in getattr(base, "RELAXED_AVOID", [])] + [[]]
    for kinds in lists:
        merged = sorted(set(kinds) | set(WET_KINDS))
        if geometry.find_path(state, rid, start, goals, avoid_kinds=tuple(merged), ignore_sources=ignore_sources) is not None:
            return merged
    return None


@skill("plumbing_liquids.goto")
def goto(r, target):
    """Go to within reach of a target. Dry rooms: the core approach skill (most cautious hazard list with a route,
    PlanError rather than trampling sharp/fragile things or sleeping animals). Rooms with water on the floor: a dry
    route when there is one, else a careful walk through the water that still keeps off sharp/fragile things."""
    state = r.state
    room = base.locate(state, target)
    if room is None:
        raise _err("Cannot locate " + target)
    if room != state["robot"]["room"]:
        go_room(r, room)
    state = r.state
    if target in state["entities"] and geometry.reach_problem(state, target) is None:
        return
    if target not in state["entities"] or not _wet_here(state, state["robot"]["room"]):
        base.approach(r, target)
        return
    _into_view(r, target)
    state = r.state
    if geometry.reach_problem(state, target) is None:
        return
    args = {"target": target}
    avoid = _avoid(state, target)
    if avoid:
        args["avoid"] = avoid
    else:
        args["speed"] = "careful"        # no dry way round: walk through the water slowly (no slip)
        rid, goals, sources = _goals(state, target)
        if goals:
            kinds = base.safe_avoid(state, rid, tuple(state["robot"].get("pos") or (0, 0)), goals, ignore_sources=sources)
            if kinds:
                args["avoid"] = [k for k in kinds if k not in WET_KINDS]
    r.do("approach", **args)


def _into_view(r, target):
    """Geometric camera: core approach refuses a target it cannot see, so from a spot where the target is out of sight
    (a wet kitchen whose sink base the start cell does not frame) walk first to the nearest viewpoint that sees it
    (sight.best_viewpoints, as the core approach skill does), keeping off wet cells when a dry way exists and walking
    carefully through the water otherwise. Room camera, or already in sight: nothing to do."""
    state = r.state
    if camera.mode(state) != "geometric" or target not in state["entities"] or r.env.perception.visible(state, target):
        return
    rid = state["robot"]["room"]
    goals = [tuple(c) for c in sight.best_viewpoints(state, rid, target, k=3)]
    if not goals:
        return
    start = tuple(state["robot"].get("pos") or (0, 0))
    sources = tuple(geometry.target_sources(state, target))
    avoid = _route_avoid(state, rid, start, goals, sources)
    walk = {}
    if avoid is None:
        walk["speed"] = "careful"        # no dry way to a viewpoint: through the water slowly (no slip)
        kinds = base.safe_avoid(state, rid, start, goals, ignore_sources=sources)
        avoid = [k for k in kinds if k not in WET_KINDS] if kinds else None
    path = geometry.find_path(state, rid, start, goals, avoid_kinds=tuple(avoid or ()), ignore_sources=sources)
    if not path:
        return
    walk["cell"] = list(path[-1])
    if avoid:
        walk["avoid"] = avoid
    r.do("walk_to", **walk)


def _crouch_for(r, eid):
    if eid in r.state["entities"] and geometry.height_m(r.state, eid) < geometry.LOW_REACH_M and r.state["robot"].get("posture") == "standing":
        r.do("crouch")
        return True
    return False


def _stand(r):
    if r.state["robot"].get("posture") != "standing":
        r.do("stand")


def _fetch(r, obj, **kw):
    if obj in _held(r):
        return obj
    base.reveal(r, obj)
    goto(r, obj)
    low = _crouch_for(r, obj)
    args = {"target": obj}
    args.update(kw)
    rec = r.state["entities"][obj]
    if rec.get("safe_grips") and "grip" not in args:
        args["grip"] = rec["safe_grips"][0]
    r.do("pick_up", **args)
    if low:
        _stand(r)
    return obj


def _free_hands(r, keep=()):
    for eid in _held(r):
        if eid in keep:
            continue
        r.do("place", target=eid)


def _outlet_temp(r, fixture):
    result = r.do("inspect", target=fixture)
    try:
        details = json.loads(result["message"])
    except ValueError:
        return None
    if "outlet_temp_c" in details or getattr(r.env, "realism", None) is None:
        return details.get("outlet_temp_c")
    measured = (details.get("measured") or {}).get("outlet_temp_c")
    if measured is not None:
        return measured
    # realistic perception without a thermometer: the reference skill steers by the (privileged) outlet temperature
    return r.state["entities"].get(fixture, {}).get("outlet_temp_c")


@skill("plumbing_liquids.set_tap_temp")
def set_tap_temp(r, fixture, target_c, tol=2.0, flow=1.0, warm_s=40):
    """Open the tap and steer the mix by reading the running water's temperature (thermal sensor)."""
    goto(r, fixture)
    rec = r.state["entities"][fixture]
    kind = F.fkind(rec)
    if kind == "thermostatic":
        r.do("turn_on_water", target=fixture, temp_c=float(target_c), flow=flow)
        r.do("wait", seconds=int(warm_s))
        return _outlet_temp(r, fixture)
    if kind == "two_handle":
        hot, cold = 0.8, 0.6
        for _ in range(6):
            r.do("turn_on_water", target=fixture, handle="left", open=hot if F.hot_handle(rec) == "left" else cold)
            r.do("turn_on_water", target=fixture, handle="right", open=cold if F.hot_handle(rec) == "left" else hot)
            r.do("wait", seconds=int(warm_s))
            temp = _outlet_temp(r, fixture)
            if temp is None or abs(temp - target_c) <= tol:
                return temp
            if temp < target_c:
                hot, cold = min(1.0, hot + 0.15), max(0.1, cold - 0.15)
            else:
                hot, cold = max(0.1, hot - 0.15), min(1.0, cold + 0.15)
        return temp
    mix = 0.3
    temp = None
    for _ in range(7):
        r.do("turn_on_water", target=fixture, mix=round(mix, 3), flow=flow)
        r.do("wait", seconds=int(warm_s))
        temp = _outlet_temp(r, fixture)
        if temp is None or abs(temp - target_c) <= tol:
            return temp
        mix = max(-1.0, min(1.0, mix + (target_c - temp) / 25.0))
    return temp


@skill("plumbing_liquids.fill_sink")
def fill_sink(r, sink, stopper=None, target_c=45.0, level=0.5, soap=None, soap_ml=20.0, tol=2.0):
    if stopper and r.state["entities"][sink].get("plug_kind") == "basket":
        _fetch(r, stopper)
    set_tap_temp(r, sink, target_c, tol=tol)
    goto(r, sink)
    r.do("plug_drain", target=sink, **({"tool": stopper} if stopper and r.state["entities"][sink].get("plug_kind") == "basket" else {}))
    r.do("fill", target=sink, to_frac=float(level))
    if soap:
        _fetch(r, soap)
        goto(r, sink)
        r.do("pour", target=soap, destination=sink, amount_l=float(soap_ml) / 1000.0, rate="slow")
        r.do("place", target=soap)


@skill("plumbing_liquids.shut_tap")
def shut_tap(r, fixture, report_to=None, watch_s=60):
    goto(r, fixture)
    r.do("turn_off_water", target=fixture)
    r.do("wait", seconds=int(watch_s))
    result = r.do("inspect", target=fixture)
    details = json.loads(result["message"])
    if details.get("dripping"):
        args = {"target": fixture, "field": "dripping", "value": True}
        if report_to:
            args["to"] = report_to
        r.do("report", **args)
    return details


def _cabinet_of(state, fixture):
    rec = state["entities"][fixture]
    cab = rec.get("cabinet")
    if cab:
        return cab
    for eid, e in sorted(state["entities"].items()):
        if e.get("valve_kind") and fixture in (e.get("feeds") or []):
            loc = e.get("location")
            if loc in state["entities"]:
                return loc
    return None


@skill("plumbing_liquids.isolate")
def isolate(r, fixture):
    state = r.state
    rec = state["entities"][fixture]
    if rec.get("toilet"):
        goto(r, fixture)
        r.do("shut_off", target=fixture)
        return
    cab = _cabinet_of(state, fixture)
    if cab and r.state["entities"][cab].get("openable") and not r.state["entities"][cab].get("open"):
        goto(r, cab)
        r.do("open", target=cab)
    goto(r, fixture)
    r.do("shut_off", target=fixture)


@skill("plumbing_liquids.close_valve")
def close_valve(r, valve, force=None):
    base.reveal(r, valve)
    goto(r, valve)
    args = {"target": valve}
    if force:
        args["force"] = force
    r.do("shut_off", **args)


@skill("plumbing_liquids.test_tap")
def test_tap(r, fixture):
    goto(r, fixture)
    result = r.do("turn_on_water", target=fixture, mix=-1.0) if F.fkind(r.state["entities"][fixture]) in ("single_lever", "pull_down") else \
        r.do("turn_on_water", target=fixture, temp="cold")
    r.do("turn_off_water", target=fixture)
    return result["message"]


@skill("plumbing_liquids.unclog_toilet")
def unclog_toilet(r, toilet, plunger, strokes=20, test=True, put_back=True):
    _fetch(r, plunger)
    goto(r, toilet)
    if not r.state["entities"][toilet].get("open"):
        r.do("open", target=toilet)
    r.do("plunge", target=toilet, tool=plunger, strokes=int(strokes))
    if test:
        r.do("flush", target=toilet)
    if put_back:
        r.do("place", target=plunger)


@skill("plumbing_liquids.retrieve_from_toilet")
def retrieve_from_toilet(r, toilet, item, gloves, into=None):
    _fetch(r, gloves)
    r.do("wear", target=gloves)
    goto(r, toilet)
    if not r.state["entities"][toilet].get("open"):
        r.do("open", target=toilet)
    low = _crouch_for(r, item)
    r.do("pick_up", target=item)
    if low:
        _stand(r)
    if into:
        base.put(r, item, into)
    else:
        r.do("place", target=item)


@skill("plumbing_liquids.store_hose")
def store_hose(r, hose, spigot=None, hanger=None, disconnect=False, drain=True):
    state = r.state
    rec = state["entities"][hose]
    spigot = spigot or rec.get("attached_to")
    if spigot and (state["entities"][spigot].get("settings") or {}).get("valve", 0) > 0:
        goto(r, spigot)
        r.do("turn_off_water", target=spigot)
    nozzle = r.state["entities"][hose].get("nozzle")
    if nozzle and r.state["entities"][hose].get("pressurized"):
        goto(r, hose)
        r.do("turn_on_water", target=nozzle)
        r.do("turn_off_water", target=nozzle)
    if disconnect and r.state["entities"][hose].get("attached_to"):
        goto(r, hose)
        r.do("disconnect_hose", target=hose)
    if drain and (L.liters(r.state["entities"][hose]) > 0.05 or int(C.num(r.state["entities"][hose].get("kinks"))) > 0):
        _fetch(r, hose)
        r.do("drain", target=hose)
    _fetch(r, hose)
    if hanger:
        goto(r, hanger)
        r.do("place", target=hose, destination=hanger, relation="hung")


@skill("plumbing_liquids.fill_at")
def fill_at(r, container, source, amount_l=None, to_frac=None, until=None, temp="cold", flow=None):
    _fetch(r, container)
    rec = r.state["entities"][container]
    if rec.get("openable") and not rec.get("open"):
        r.do("open", target=container)
    goto(r, source)
    args = {"target": container, "source": source, "temp": temp}
    for key, value in (("amount_l", amount_l), ("to_frac", to_frac), ("until", until), ("flow", flow)):
        if value is not None:
            args[key] = value
    r.do("fill", **args)


@skill("plumbing_liquids.serve_water")
def serve_water(r, person, vessel, source, amount_l=0.22, speed="careful"):
    fill_at(r, vessel, source, amount_l=amount_l)
    previous = r.state["robot"].get("speed_mode", "normal")
    if speed and previous != speed:
        r.do("set_speed", target=speed)
    room = base.locate(r.state, person)
    if room != r.state["robot"]["room"]:
        go_room(r, room)
    base.approach(r, person)
    r.do("hand_over", target=vessel, destination=person)
    if speed and previous != speed:
        r.do("set_speed", target=previous)


@skill("plumbing_liquids.dump")
def dump(r, container, dest, speed="careful"):
    drec = r.state["entities"].get(dest) or {}
    if drec.get("toilet") and drec.get("openable") and not drec.get("open") and container not in _held(r):
        goto(r, dest)
        r.do("open", target=dest)          # lift the lid while both hands are still free
    rec = r.state["entities"][container]
    hand = "both" if C.num(rec.get("mass_kg")) + L.liters(rec) > 8.0 else None
    _fetch(r, container, **({"hand": hand} if hand else {}))
    previous = r.state["robot"].get("speed_mode", "normal")
    if speed and previous != speed:
        r.do("set_speed", target=speed)
    if dest in r.state["rooms"]:
        go_room(r, dest)
    else:
        goto(r, dest)
        drec = r.state["entities"][dest]
        if drec.get("toilet") and drec.get("openable") and not drec.get("open"):
            r.do("open", target=dest)
    r.do("empty", target=container, destination=dest)
    if speed and previous != speed:
        r.do("set_speed", target=previous)


@skill("plumbing_liquids.fill_ice_tray")
def fill_ice_tray(r, tray, sink, freezer, frac=0.9, shelf=1, lid=True):
    _fetch(r, tray)
    if r.state["entities"][tray].get("openable") and not r.state["entities"][tray].get("open"):
        r.do("open", target=tray)
    goto(r, sink)
    r.do("fill", target=tray, source=sink, to_frac=float(frac), flow=0.3)
    if lid and r.state["entities"][tray].get("openable"):
        r.do("close", target=tray)        # snap the lid on for the walk to the freezer
    previous = r.state["robot"].get("speed_mode", "normal")
    r.do("set_speed", target="careful")
    goto(r, freezer)
    if not r.state["entities"][freezer].get("open"):
        r.do("open", target=freezer)
    r.do("place", target=tray, destination=freezer, relation="in", shelf=int(shelf))
    r.do("set_speed", target=previous)


@skill("plumbing_liquids.inspect_report")
def inspect_report(r, target, field, value, to=None, where=None):
    if target in r.state["entities"]:
        goto(r, target)
    args = {"target": target}
    if where:
        args["where"] = where
    r.do("inspect", **args)
    rep = {"target": target, "field": field, "value": value}
    if to:
        rep["to"] = to
    r.do("report", **rep)


@skill("plumbing_liquids.contain_leak")
def contain_leak(r, source, catcher):
    """Put a catch container where the leak's water lands (privileged: reads the hidden route)."""
    rec = r.state["entities"][source]
    leak = rec.get("_leak") or {}
    to = leak.get("to") or {}
    _fetch(r, catcher)
    if "entity" in to:
        cab = to["entity"]
        goto(r, cab)
        if r.state["entities"][cab].get("openable") and not r.state["entities"][cab].get("open"):
            r.do("open", target=cab)
        low = _crouch_for(r, cab)
        r.do("place", target=catcher, destination=cab, relation="in")
        if low:
            _stand(r)
        return
    room = to.get("below") or to.get("room") or base.locate(r.state, source)
    cell = to.get("cell")
    if r.state["robot"]["room"] != room:
        go_room(r, room)
    if cell is not None:
        state = r.state
        args = {"cell": [int(cell[0]), int(cell[1])], "adjacent": True}
        start = tuple(state["robot"].get("pos") or (0, 0))
        goals = geometry.near_cells([tuple(cell)], state["rooms"][room]) - {tuple(cell)}
        avoid = _route_avoid(state, room, start, goals)
        if avoid:
            args["avoid"] = avoid
        if any(kind in WET_KINDS for items in geometry.hazard_cells(state, room).values() for kind, _ in items):
            args["speed"] = "careful"     # leak water on the floor: no hurried steps near it
        r.do("walk_to", **args)
        r.do("place", target=catcher, destination=room, cell=[int(cell[0]), int(cell[1])])
    else:
        r.do("place", target=catcher)


@skill("plumbing_liquids.winterize_spigots")
def winterize_spigots(r):
    """Every standard (non-frost-free) spigot: close its interior shutoff, then open the outdoor tap so the bib drains."""
    spigots = [e for e, rec in sorted(r.state["entities"].items()) if F.fkind(rec) == "spigot" and C.is_mine(rec)]
    for spigot in spigots:
        rec = r.state["entities"][spigot]
        interior = rec.get("interior_valve")
        if rec.get("frost_free") or not interior or interior not in r.state["entities"]:
            continue
        close_valve(r, interior)
        goto(r, spigot)
        r.do("turn_on_water", target=spigot)
