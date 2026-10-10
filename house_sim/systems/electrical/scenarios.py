"""Scenario helpers for task setup (setup op {"op": "helper", "name": "electrical.<name>", "args": {...}}).

Every helper returns a dict of bindings and leaves derived fields settled. Objects owned by other
domains (space heaters, fridges, aquariums, sinks...) are built from core types with explicit
props, e.g. {"type": "generic_item", "props": {"label": "space heater", "power_w": 1500,
"plug_required": true, "wall_outlet_only": true, "high_draw": true}}.

  electrical.settle            recompute derived fields after setup edits (call last)
  electrical.furniture         stand a lamp table/stand on any layout (never seals anything off) -> var
  electrical.outlet            find/mount an outlet in a room (near an entity, hazard-free cord) -> outlet, breaker
  electrical.plug              plug a device into an outlet/strip at setup time          -> outlet
  electrical.breaker_of        the breaker feeding an entity or room                     -> breaker, panel
  electrical.devices           spawn corded devices (list of {type, props, var, on/in/floor}) and plug them
  electrical.overload          plug devices on one circuit, switch them on, trip it      -> breaker, panel, outlet
  electrical.crowded_strip     strip with several look-alike plugs                       -> strip, outlet, devices
  electrical.media             TV + sources + soundbar wired with shuffled ports          -> tv, remote, ports...
  electrical.detector          a smoke/CO alarm on a base with cells                     -> base, detector, cells
  electrical.cells             fill a device's battery slots (charge, orientation)        -> cells
  electrical.lamp              a lamp with bulb(s) on a surface, plugged in              -> lamp, bulbs, outlet
  electrical.socket_fixture    give a ceiling light bulb sockets and bulbs               -> bulbs
  electrical.three_way         turn a room light into a 3-way circuit with a 2nd switch  -> switch_a, switch_b, light
  electrical.trip              trip a breaker / GFCI / strip at setup time
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from . import common as C
from . import furnish as F
from . import power as P
from .cords import cell_of


def _state(ctx):
    return ctx.placer.state()


def _room(ctx, value):
    rooms = ctx.config["rooms"]
    if value in rooms:
        return value
    found = ctx.placer.rooms(value)
    if not found:
        raise SpecError("electrical: no room of type {}".format(value))
    return found[0]


def _place(ctx, type_name, props, spec):
    p = ctx.placer
    props = dict(props or {})
    if "on" in spec:
        return p.on(spec["on"], type_name, spos=spec.get("spos"), **props)
    if "in" in spec:
        return p.inside(spec["in"], type_name, shelf=spec.get("shelf"), **props)
    if "floor" in spec:
        return p.floor_item(_room(ctx, spec["floor"]), type_name, near=spec.get("near"), **props)
    if "furniture" in spec:
        return F.place_furniture(p, _room(ctx, spec["furniture"]), type_name, wall=spec.get("wall", True),
                                 near=spec.get("near"), **props)
    if "mount" in spec:
        return p.mount(_room(ctx, spec["mount"]), type_name, height=spec.get("height"), cell=spec.get("cell"), **props)
    raise SpecError("electrical: device spec needs on/in/floor/furniture/mount")


@scenario_helper("electrical.settle")
def h_settle(ctx, **_):
    F.settle(ctx.config)
    return {}


@scenario_helper("electrical.furniture")
def h_furniture(ctx, room, type="side_table", wall=True, near=None, var="furniture", props=None):
    """Stand a piece of furniture (lamp table, nightstand, desk...) in `room` on any generated layout.

    The core first-fit can give up in crowded rooms (or wall a corner off); this placement keeps every doorway and
    every reachable piece reachable, leaves the new piece a free cell within arm's reach, stays out of door swings
    and off loose floor items and rugs, and prefers the wall. near=<entity|[col,row]> keeps it close to that.
    Raises SpecError only when the room has no such spot at all."""
    rid = _room(ctx, room)
    near_cell = None
    if isinstance(near, (list, tuple)):
        near_cell = tuple(near)
    elif near:
        _, near_cell = cell_of(_state(ctx), near)
    eid = F.place_furniture(ctx.placer, rid, type, wall=wall, near=near_cell, **dict(props or {}))
    if eid is None:
        raise SpecError("electrical.furniture: no free, reachable spot for a {} in {}".format(type, rid))
    return {var: eid}


@scenario_helper("electrical.outlet")
def h_outlet(ctx, room, near=None, kind=None, height=None, free=1, new=False, gfci=None, max_cells=1, var="outlet", share_cell=False):
    """An outlet in `room` with `free` receptacles; near=<entity> keeps it within max_cells of it (mounts one if needed).

    With `near`, outlets whose cord route from `near` is free of hazards (walkway, doorway, rug, water, heat) win;
    when none of the existing ones is, a receptacle is mounted on a wall cell that gives a hazard-free route."""
    rid = _room(ctx, room)
    state = _state(ctx)
    cell = None
    if near:
        _, cells_near = C.anchor_cells(state, near)
        wall = F._perimeter_free(ctx.placer, rid, set())
        if cells_near and wall:
            cell = min(cells_near, key=lambda c: (min(geometry.chebyshev(c, w) for w in wall), c[1], c[0]))
        else:
            _, cell = cell_of(state, near)
    ents = ctx.config["entities"]
    outs = [o for o in ctx.placer.find(None, room=rid) if ents[o].get("category") == "outlet" and ents[o].get("pos")]
    if gfci is not None:
        outs = [o for o in outs if bool(ents[o].get("gfci")) == bool(gfci)]
    outs = [o for o in outs if len(P.plugs_in(state, o)) <= int(C.num(ents[o].get("receptacles"), 2)) - int(free)]
    # only outlets the robot can stand at (not behind counters or furniture), preferably without stepping beside
    # fragile things or sleepers (a strict core approach refuses that route)
    outs = [o for o in outs if F.outlet_reachable(state, o)]
    safe_outs = [o for o in outs if F.outlet_reachable(state, o, safe=True)]
    outs = safe_outs or outs
    if cell:
        outs = [o for o in outs if geometry.chebyshev(tuple(ents[o]["pos"]), cell) <= max_cells]
        outs.sort(key=lambda o: (geometry.chebyshev(tuple(ents[o]["pos"]), cell), o))
    if near and near in ents and C.room_of(state, near) == rid:
        cord = C.num(ents[near].get("cord_length_m"), 0.0) or None
        clean = [o for o in outs if (F.route_risk(state, near, o, cord) or (1,))[0] == 0]
        if clean and not new:
            outs = clean
        elif not share_cell:
            g = C.REGION[F.region_of(ctx.config)]
            oid = F.mount_clear_outlet(ctx.placer, ctx.config, near, rid=rid, cord_m=cord, max_cells=max_cells,
                                       kind=kind or ("gfci_outlet" if gfci else "wall_outlet"),
                                       height=height if height is not None else 0.3, safe=True)
            if oid is not None:
                ents[oid]["socket_type"] = g["socket"]
                return {var: oid, var + "_breaker": ents[oid].get("circuit") or ctx.config["rooms"][rid].get("circuit")}
    if outs and not new:
        oid = outs[0]
    else:
        g = C.REGION[F.region_of(ctx.config)]
        taken = [] if share_cell else [tuple(ents[o]["pos"]) for o in ctx.placer.find(None, room=rid)
                                         if ents[o].get("category") == "outlet" and ents[o].get("pos")]
        h = height if height is not None else 0.3
        cells = F.wall_cells(ctx.placer, rid, h, set(taken))
        safe_cells = [c for c in cells if F.approachable(_state(ctx), rid, [c], radius=F.reach_radius(h), safe=True)]
        cells = safe_cells or cells or F._perimeter_free(ctx.placer, rid, set(taken))
        kind = kind or ("gfci_outlet" if gfci else "wall_outlet")
        if cell:
            cells.sort(key=lambda c: (geometry.chebyshev(c, cell), c[1], c[0]))
        if cell and (not cells or geometry.chebyshev(cells[0], cell) > max_cells):
            # furniture away from the walls: a floor box (flush receptacle in the floor) beside it
            st = _state(ctx)
            blocked = set(geometry.blocked_cells(st, rid, include_agents=False))
            room = ctx.config["rooms"][rid]
            reach = geometry.reachable_cells(st, rid, blocked)
            swing = geometry.door_clearance(st, rid)
            # prefer a spot the robot can walk to, out of the door swing
            spots = sorted((c for c in geometry.near_cells([cell], room) if c not in blocked and c != cell),
                           key=lambda c: (c not in reach, c in swing, geometry.chebyshev(c, cell), c[1], c[0]))
            if not spots:
                raise SpecError("electrical.outlet: no floor space beside " + str(near))
            oid = ctx.placer.mount(rid, kind, height=0.0, cell=list(spots[0]), circuit=ctx.config["rooms"][rid].get("circuit"),
                                   socket_type=g["socket"], label="floor outlet", floor_box=True, size_m=[0.12, 0.12, 0.02])
        else:
            if not cells:
                raise SpecError("electrical.outlet: no wall space in " + rid)
            oid = ctx.placer.mount(rid, kind, height=height if height is not None else 0.3, cell=list(cells[0]),
                                   circuit=ctx.config["rooms"][rid].get("circuit"), socket_type=g["socket"])
    return {var: oid, var + "_breaker": ents[oid].get("circuit") or ctx.config["rooms"][rid].get("circuit")}


def _connect(ctx, device, outlet=None, route=None, obstruct=False, **kw):
    """Plug a device at setup time (furnish.connect): a receptacle the robot can stand at whose cord run is hazard-free
    and cuts no route, else a clean receptacle mounted nearby (GFCI in wet rooms, outdoor type outside), else the best
    acceptable run, else a dressed cord. With an explicit outlet, the device moves beside it when its cord is short."""
    used = F.connect(ctx.placer, ctx.config, device, outlet=outlet, route=route, reachable=True, safe=True, obstruct=obstruct, **kw)
    if not ctx.config["entities"][device].get("plugged_in"):
        if outlet is not None:
            raise SpecError("electrical: the cord of {} does not reach {}".format(device, outlet))
        raise SpecError("electrical: no reachable outlet for " + device)
    return used


def _outlet_for(ctx, device):
    """Choose (and plug into) an outlet for a device (see _connect); returns the receptacle."""
    return _connect(ctx, device)


def _plug(ctx, device, outlet, route, obstruct=False):
    return _connect(ctx, device, outlet=outlet, route=route, obstruct=obstruct)


@scenario_helper("electrical.plug")
def h_plug(ctx, device, outlet=None, route="along_wall", var="outlet", obstruct=False):
    """Plug a device in at setup time. obstruct=true keeps the requested cord run even when it crosses a walkway or
    cuts a route (a deliberate cord hazard for cable-management tasks); otherwise the cord never cuts a route."""
    outlet = _plug(ctx, device, outlet, route, obstruct=obstruct)
    F.settle(ctx.config)
    return {var: outlet}


@scenario_helper("electrical.breaker_of")
def h_breaker_of(ctx, target, var="breaker"):
    state = _state(ctx)
    if target in ctx.config["rooms"]:
        bid = ctx.config["rooms"][target].get("circuit")
    else:
        bid = P.circuit_of(state, target) or P.room_circuit(state, target)
    if not bid:
        raise SpecError("electrical.breaker_of: no circuit for " + str(target))
    panel = ctx.config["entities"][bid].get("panel")
    return {var: bid, "panel": panel, "panel_room": C.room_of(state, panel) if panel else None,
            "main": (ctx.config.get("metadata") or {}).get("electrical", {}).get("main")}


@scenario_helper("electrical.devices")
def h_devices(ctx, devices, outlet=None, route="along_wall"):
    out = {}
    for spec in devices:
        props = dict(spec.get("props") or {})
        eid = _place(ctx, spec.get("type", "generic_item"), props, spec)
        if eid is None:
            raise SpecError("electrical.devices: no room for {}".format(spec.get("var")))
        rec = ctx.config["entities"][eid]
        if rec.get("plug_required") and spec.get("plug", True):
            _plug(ctx, eid, spec.get("outlet") or outlet, spec.get("route", route), obstruct=bool(spec.get("obstruct")))
        if spec.get("var"):
            out[spec["var"]] = eid
    F.settle(ctx.config)
    return out


@scenario_helper("electrical.overload")
def h_overload(ctx, room, devices, trip=True, outlet=None):
    """Plug devices (ids) into receptacles of one branch circuit in `room`, switch them on and (optionally) trip the
    breaker. Receptacles mounted for them join that circuit; floor appliances may stand beside the receptacle they use."""
    rid = _room(ctx, room)
    oid = outlet or h_outlet(ctx, rid, free=0)["outlet"]
    bid = ctx.config["entities"][oid].get("circuit") or ctx.config["rooms"][rid].get("circuit")
    ents = ctx.config["entities"]
    for dev in devices:
        candidates = [o for o in sorted(ents) if ents[o].get("category") == "outlet" and (ents[o].get("circuit") or bid) == bid
                      and C.room_of(_state(ctx), o) == rid]
        _connect(ctx, dev, candidates=candidates, circuit=bid)
        used = ents[dev].get("outlet")
        if used and (ents[used].get("circuit") or bid) != bid:
            ents[used]["circuit"] = bid
        ents[dev]["on"] = True
    if trip and bid:
        b = ents[bid]
        b.update(tripped=True, on=True, handle="tripped", trip_count=1, last_trip={"time_s": -60.0, "reason": "overload"})
    F.settle(ctx.config)
    panel = ents[bid].get("panel") if bid else None
    return {"breaker": bid, "panel": panel, "outlet": oid, "panel_room": C.room_of(_state(ctx), panel) if panel else None}


@scenario_helper("electrical.crowded_strip")
def h_crowded_strip(ctx, room, devices, near=None, look="black 2-prong plug", strip_props=None, var="strip"):
    rid = _room(ctx, room)
    state = _state(ctx)
    near_cell = None
    if near:
        _, near_cell = cell_of(state, near)
    props = dict(strip_props or {})
    props.setdefault("socket_type", C.REGION[F.region_of(ctx.config)]["socket"])
    props.update(F.plug_props(ctx.config, grounded=True))
    strip = ctx.placer.floor_item(rid, "power_strip", near=list(near_cell) if near_cell else None, **props)
    if strip is None:
        raise SpecError("electrical.crowded_strip: no floor space")
    wall = _connect(ctx, strip)
    out = {var: strip, var + "_outlet": wall}
    socket = 1
    for spec in devices:
        eid = _place(ctx, spec.get("type", "generic_item"), spec.get("props") or {}, spec)
        if eid is None:
            raise SpecError("electrical.crowded_strip: no room for {}".format(spec.get("var")))
        rec = ctx.config["entities"][eid]
        rec.setdefault("plug_required", True)
        rec["plug_look"] = spec.get("look", look)
        rec.update(plugged_in=True, outlet=strip, _socket=socket, plug_seated=1.0)
        socket += 1
        if spec.get("var"):
            out[spec["var"]] = eid
    F.settle(ctx.config)
    return out


@scenario_helper("electrical.media")
def h_media(ctx, room="living_room", sources=("streaming_box", "game_console"), soundbar=False, tv_on=False, var_prefix=""):
    rid = _room(ctx, room)
    for eid in sorted(ctx.config["entities"]):
        rec = ctx.config["entities"].get(eid)
        if rec and C.room_of(_state(ctx), eid) == rid and rec.get("type") in ("television", "streaming_box", "game_console", "soundbar", "remote_control", "hdmi_cable", "power_strip", "cable_box"):
            for child in [c for c, r in ctx.config["entities"].items() if r.get("location") == eid]:
                ctx.config["entities"].pop(child, None)
            ctx.config["entities"].pop(eid, None)
    for r in ctx.config["rooms"].values():
        r["floor_hazards"] = [h for h in r.get("floor_hazards", []) if h.get("source") in ctx.config["entities"]]
    out = F.build_media(ctx.placer, ctx.config, ctx.rng, rid=rid, soundbar=soundbar, sources=list(sources))
    if not out.get("tv"):
        raise SpecError("electrical.media: could not place a TV in " + rid)
    if out.get("strip"):
        out["strip_outlet"] = _connect(ctx, out["strip"])
    if tv_on:
        ctx.config["entities"][out["tv"]].update(on=True, power_state="on")
    F.settle(ctx.config)
    return {var_prefix + k: v for k, v in out.items()}


@scenario_helper("electrical.detector")
def h_detector(ctx, room, type="smoke_alarm", charge=0.8, manufactured="2022-03", height=None, cell=None, group=None, var="detector", props=None):
    rid = _room(ctx, room)
    out = F.add_detector(ctx.placer, rid, type, charge=charge, manufactured=manufactured, height=height, cell=cell, group=group, **(props or {}))
    F.settle(ctx.config)
    return {var: out["detector"], var + "_base": out["base"], var + "_cells": out["cells"],
            var + "_cell": out["cells"][0] if out["cells"] else None}


@scenario_helper("electrical.cells")
def h_cells(ctx, device, charge=0.05, condition="in use", reversed_slots=(), var="cells"):
    rec = ctx.config["entities"][device]
    for slot, info in sorted((rec.get("slots") or {}).items()):
        if info.get("item"):
            ctx.config["entities"].pop(info["item"], None)
            info["item"] = None
    cells = F.add_cells(ctx.placer, device, condition=condition, charge=charge)
    for c in cells:
        crec = ctx.config["entities"][c]
        if crec.get("slot") in reversed_slots:
            crec["orientation"] = "upside_down" if crec.get("orientation") == "upright" else "upright"
    F.settle(ctx.config)
    out = {var: cells}
    for i, c in enumerate(cells, 1):
        out["{}_{}".format(var, i)] = c
    return out


@scenario_helper("electrical.lamp")
def h_lamp(ctx, type="table_lamp", bulb="led_bulb_e26", bulb_props=None, plugged=True, outlet=None, route="along_wall", var="lamp", props=None, **spec):
    props = dict(props or {})
    props.update({k: v for k, v in F.plug_props(ctx.config).items() if k not in props})
    props.setdefault("on", False)
    lamp = _place(ctx, type, props, spec)
    if lamp is None:
        raise SpecError("electrical.lamp: no space for the lamp")
    rec = ctx.config["entities"][lamp]
    part, _ = F.BULB_PART[F.region_of(ctx.config)]
    for slot, info in sorted((rec.get("slots") or {}).items()):
        info["accepts"] = [part]
    bulbs = []
    if bulb:
        for slot in sorted(rec.get("slots") or {}):
            bulbs.append(F.add_bulb(ctx.placer, lamp, slot, bulb, **(bulb_props or {})))
    rec["plugged_in"] = False
    out = {var: lamp, var + "_bulb": bulbs[0] if bulbs else None, var + "_bulbs": bulbs}
    if plugged:
        out[var + "_outlet"] = _connect(ctx, lamp, outlet=outlet, route=route)
    F.settle(ctx.config)
    return out


@scenario_helper("electrical.socket_fixture")
def h_socket_fixture(ctx, light, count=1, bulb="led_bulb_e26", max_w=60.0, bulb_props=None, var="bulbs"):
    rec = ctx.config["entities"][light]
    for slot, info in sorted((rec.get("slots") or {}).items()):
        if info.get("item"):
            ctx.config["entities"].pop(info["item"], None)
    bulbs = F.socket_fixture(ctx.placer, light, count, bulb, max_w=max_w, **(bulb_props or {}))
    F.settle(ctx.config)
    out = {var: bulbs}
    for i, b in enumerate(bulbs, 1):
        out["{}_{}".format(var, i)] = b
    return out


@scenario_helper("electrical.bulbs")
def h_bulbs(ctx, fixture, bulbs, on=None, **spec):
    """Spare bulbs whose base matches `fixture`'s sockets: bulbs=[{"type":..., "var":..., "props":{...}}], placed on/in like devices."""
    rec = ctx.config["entities"][fixture]
    accepts = None
    for slot, info in sorted((rec.get("slots") or {}).items()):
        acc = [a for a in info.get("accepts", []) if str(a).startswith("bulb_")]
        if acc:
            accepts = acc[0]
            break
    if accepts is None:
        accepts = F.BULB_PART[F.region_of(ctx.config)][0]
    base = accepts.split("_", 1)[1] if "_" in accepts else accepts
    out = {}
    where = dict(spec)
    if on is not None:
        where["on"] = on
    for item in bulbs:
        props = dict(item.get("props") or {})
        props.setdefault("part_type", accepts)
        props.setdefault("base", base)
        eid = _place(ctx, item.get("type", "led_bulb_e26"), props, item if any(k in item for k in ("on", "in", "floor")) else where)
        if eid is None:
            raise SpecError("electrical.bulbs: no space for a spare bulb")
        if item.get("var"):
            out[item["var"]] = eid
    return out


@scenario_helper("electrical.three_way")
def h_three_way(ctx, room, light=None, light_on=None, switch_a_up=None):
    """Make a room light a 3-way circuit: the existing doorway switch plus a second switch at another wall."""
    rid = _room(ctx, room)
    lights = [l for l in ctx.placer.find("ceiling_light", room=rid)] if light is None else [light]
    if not lights:
        raise SpecError("electrical.three_way: no ceiling light in " + rid)
    light = lights[0]
    switches = [s for s, r in sorted(ctx.config["entities"].items()) if r.get("category") == "switch" and light in (r.get("controls") or [])]
    if not switches:
        raise SpecError("electrical.three_way: the light has no switch")
    a = switches[0]
    if len(switches) >= 2:
        # a light with two doorway switches already is a 3-way circuit: wire the pair (extra switches drop the light)
        b = switches[1]
        for extra in switches[2:]:
            ctx.config["entities"][extra]["controls"] = [c for c in ctx.config["entities"][extra].get("controls") or [] if c != light]
    else:
        taken = [tuple(ctx.config["entities"][a]["pos"])] if ctx.config["entities"][a].get("pos") else []
        # the far wall from the existing switch, at a spot someone can walk up to (not behind a bookcase)
        reachable_wall = (F.wall_cells(ctx.placer, rid, 1.2, set(taken), hazard_free=True)
                          or F.wall_cells(ctx.placer, rid, 1.2, set(taken)))
        if reachable_wall and taken:
            cell = max(reachable_wall, key=lambda c: (geometry.chebyshev(c, taken[0]), -c[1], -c[0]))
        else:
            cells = F.spread_cells(ctx.placer, rid, 2, taken)
            cell = cells[-1] if cells else [0, 0]
        b = ctx.placer.mount(rid, "three_way_switch", height=1.2, cell=list(cell), controls=[light],
                             label="{} 3-way switch (far wall)".format(ctx.config["rooms"][rid]["label"].lower()))
    arec, brec, lrec = ctx.config["entities"][a], ctx.config["entities"][b], ctx.config["entities"][light]
    arec["switch_kind"] = brec["switch_kind"] = "three_way"
    arec["three_way_with"], brec["three_way_with"] = b, a
    up_a = bool(ctx.rng.random() < 0.5) if switch_a_up is None else bool(switch_a_up)
    on = bool(lrec.get("on")) if light_on is None else bool(light_on)
    phase = bool(ctx.rng.random() < 0.5)
    # choose b so that (a != b) != phase == on
    up_b = up_a != (on != phase)
    arec["on"], brec["on"] = up_a, up_b
    arec.setdefault("_mech", {})["phase"] = phase
    if isinstance(brec.get("_mech"), dict):
        brec["_mech"]["phase"] = False
    lrec["on"] = on
    F.settle(ctx.config)
    return {"switch_a": a, "switch_b": b, "light": light}


@scenario_helper("electrical.trip")
def h_trip(ctx, target, reason="overload"):
    rec = ctx.config["entities"][target]
    if rec.get("category") == "breaker":
        rec.update(tripped=True, on=True, handle="tripped", trip_count=int(C.num(rec.get("trip_count"), 0)) + 1,
                   last_trip={"time_s": -60.0, "reason": reason})
    elif rec.get("gfci"):
        rec["gfci_tripped"] = True
    elif rec.get("has_breaker"):
        rec["tripped"] = True
    else:
        raise SpecError("electrical.trip: nothing to trip on " + target)
    F.settle(ctx.config)
    return {}
