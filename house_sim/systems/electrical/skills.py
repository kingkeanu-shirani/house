"""Reference skills for electrical procedures (used by reference/wrong plans in task specs).

They route with house_sim.tasks.skills and change state only through runner.do, so every step
is a real, guarded env.step. Agents under evaluation do not get these.

  electrical.plug_in(device, outlet, route, ...)         approach, crouch for low outlets, plug, stand
  electrical.unplug(device, grip, coil, trace)           trace the cord on crowded strips, unplug by the head
  electrical.reset_breaker(breaker, supervisor, cause)   remove the cause, get approval, open panel, reset, close
  electrical.replace_cells(device, cells, recycle, surface, test)  swap every cell with correct polarity, recycle old
  electrical.replace_bulb(fixture, bulb, switch, disposal)          power off, let cool, swap, power on
  electrical.test_detectors(detectors, tool, announce)   warn, test each, log results with reports
  electrical.set_alarm(clock, time, ampm, ...)           read an unfamiliar clock's instructions, set alarm, enable
  electrical.set_clock(clock, time, ampm)                set the displayed time
  electrical.charge(device, charger, ...)                fetch and put a device on its charger
  electrical.connect(cable, device, port)                plug a cable end into a port
  electrical.select_input(tv, input, remote, source)     power the source, aim the remote, pick the input
  electrical.switch_light(light, on)                     flip a controlling switch until the light is in state
  electrical.restart_network(modem, router)              power-cycle modem, wait online, then router
  electrical.inspect_report(target, field, value)        inspect closely then report
"""
from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as base
from . import common as C


def _ent(r, eid):
    return r.state["entities"][eid]


def _go_room(r, room, speed=None):
    """Kernel v2.3 go_room: routes around floor hazards with the most cautious avoid list that still has a clear route
    (tasks.skills.safe_avoid, strict: never over sharp/fragile things, sleeping animals or people)."""
    base.go_room(r, room, speed=speed)


def _approach(r, target, speed=None):
    """Kernel v2.3 approach: same routing rule; ignores the target's own hazard ring; wall-mounted targets above
    counter height are reached from one cell further out (kernel reach rule)."""
    base.approach(r, target, speed=speed)


def _walk_to(r, cell):
    """Walk to a viewing cell routing around hazards when a clear route exists (non-strict: else straight there)."""
    state = r.state
    avoid = base.safe_avoid(state, state["robot"]["room"], tuple(state["robot"].get("pos") or (0, 0)), [tuple(cell)])
    args = {"cell": list(cell)}
    if avoid:
        args["avoid"] = list(avoid)
    else:
        args["speed"] = "careful"
    r.do("walk_to", **args)


def _fetch(r, obj, **kw):
    # core fetch/approach already route around tasks.skills.SAFE_AVOID when a clear route exists (kernel v2.1)
    return base.fetch(r, obj, **kw)


def _put(r, obj, dest, **kw):
    return base.put(r, obj, dest, **kw)


def _low(r, eid):
    return geometry.height_m(r.state, eid) < geometry.LOW_REACH_M


def _crouch(r):
    if r.state["robot"].get("posture") == "standing":
        r.do("crouch")


def _stand(r):
    base._stand(r)


def _wait_until(r, fn, max_s=1800, step=30):
    waited = 0
    while not fn() and waited < max_s:
        r.do("wait", seconds=step)
        waited += step


def _best_route(r, device, outlet):
    """The cord run a careful person picks: one that cuts no walking route and stays out of door clearance
    (cords.CordCheck, the rule generated houses follow), with the fewest hazards, along the wall on ties."""
    from .cords import CordCheck, cord_options
    opts = cord_options(r.state, device, outlet, ("along_wall", "direct"), CordCheck(r.state, device))
    return opts[0]["route"] if opts else "along_wall"


@skill("electrical.plug_in")
def plug_in(r, device, outlet, route="auto", socket=None, orientation=None, seat=None, deploy=None):
    rec = _ent(r, device)
    if route == "auto":
        route = _best_route(r, device, outlet) if rec.get("location") != "robot" else "direct"
    if rec.get("plug_in_place") == "wall":
        _fetch(r, device)
    _approach(r, outlet)
    low = _low(r, outlet)
    if low:
        _crouch(r)
    args = {"target": device, "destination": outlet}
    if rec.get("plug_in_place") != "wall" and route:
        args["route"] = route
    for key, value in (("socket", socket), ("orientation", orientation), ("seat", seat), ("deploy", deploy)):
        if value is not None:
            args[key] = value
    r.do("plug_in", **args)
    if low:
        _stand(r)


@skill("electrical.unplug")
def unplug(r, device, grip="plug_head", coil=None, trace=True, outlet=None, socket=None):
    from .cords import needs_trace
    target = device
    if outlet is not None and socket is not None:
        _approach(r, outlet)
        target = outlet
    else:
        if trace and needs_trace(r.state, device):
            _approach(r, device)
            r.do("inspect", target=device, where="behind")
        outlet = _ent(r, device).get("outlet") or outlet
        if outlet and outlet in r.state["entities"]:
            _approach(r, outlet)
    low = outlet is not None and outlet in r.state["entities"] and _low(r, outlet)
    if low:
        _crouch(r)
    args = {"target": target, "grip": grip}
    if socket is not None and target == outlet:
        args["socket"] = socket
    if coil:
        args["coil"] = coil
    r.do("unplug", **args)
    if low:
        _stand(r)


@skill("electrical.reset_breaker")
def reset_breaker(r, breaker, supervisor=None, cause=None, cause_off=None):
    if cause_off:
        _approach(r, cause_off)
        if _ent(r, cause_off).get("on"):
            r.do("switch_off", target=cause_off)
    if cause:
        unplug(r, cause)
    panel = _ent(r, breaker)["location"]
    if supervisor:
        where = r.state["people"][supervisor].get("location")
        if where and where != r.state["robot"]["room"]:
            _go_room(r, where)
        base.ask(r, supervisor, "permission:reset_breaker:" + breaker)
    _approach(r, panel)
    if not _ent(r, panel).get("open"):
        r.do("open", target=panel)
    r.do("inspect", target=breaker)
    r.do("reset_breaker", target=breaker)
    r.do("close", target=panel)


def _cell_surface(r, surface):
    return surface


@skill("electrical.replace_cells")
def replace_cells(r, device, cells, recycle=None, surface=None, test=None, home=None, flip=True):
    """Swap every cell of a battery device; old cells go to `recycle`, new ones are oriented to the marks."""
    from .batteries import battery_slots
    _fetch(r, device)
    rec = _ent(r, device)
    if any((rec["slots"][s] or {}).get("access") == "cover" for s in battery_slots(rec)) and not rec.get("cover_open"):
        r.do("open_cover", target=device)
    queue = list(cells)
    for slot in battery_slots(_ent(r, device)):
        old = (_ent(r, device)["slots"][slot] or {}).get("item")
        if old:
            r.do("uninstall", target=device, slot=slot)
            if recycle:
                _put(r, old, recycle)
            elif surface:
                _put(r, old, surface)
            else:
                r.do("place", target=old)
    for slot in battery_slots(_ent(r, device)):
        if not queue:
            break
        cell = queue.pop(0)
        polarity = _ent(r, device)["slots"][slot].get("polarity", "upright")
        if flip and (_ent(r, cell).get("orientation") or "upright") != polarity:
            _put(r, cell, surface, orientation=polarity)
        _fetch(r, cell)
        if not r.env.world.is_held(device):
            _approach(r, device)
        r.do("install", target=cell, destination=device, slot=slot)
    if _ent(r, device).get("cover_open") and any((_ent(r, device)["slots"][s] or {}).get("access") == "cover" for s in battery_slots(_ent(r, device))):
        r.do("close_cover", target=device)
    if test:
        remote_test(r, device, test)
    if home:
        _put(r, device, home)


def _viewing_cell(r, device):
    """A free floor cell 2-4 cells from the device with an unobstructed line to it."""
    from .devices import line_of_sight
    state = r.state
    rid, cells = C.anchor_cells(state, device)
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid))
    pos = tuple(state["robot"].get("pos") or (0, 0))
    options = []
    for y in range(rows):
        for x in range(cols):
            c = (x, y)
            if c in blocked or not cells:
                continue
            d = min(geometry.chebyshev(c, k) for k in cells)
            if 2 <= d <= 5 and line_of_sight(state, c, device):
                options.append((geometry.chebyshev(c, pos), d, y, x))
    if not options:
        return None
    options.sort()
    return [options[0][3], options[0][2]]


def remote_test(r, remote, device):
    """Operate the device from across the room with its remote, leaving its power state as it was."""
    if r.state["robot"]["room"] != C.room_of(r.state, device):
        _go_room(r, C.room_of(r.state, device))
    cell = _viewing_cell(r, device)
    if cell:
        _walk_to(r, cell)
    _fetch(r, remote)
    if _ent(r, device).get("on"):
        r.do("switch_off", target=device)
        r.do("switch_on", target=device)
    else:
        r.do("switch_on", target=device)
        r.do("switch_off", target=device)


@skill("electrical.remote_test")
def remote_test_skill(r, remote, device):
    remote_test(r, remote, device)


@skill("electrical.replace_bulb")
def replace_bulb(r, fixture, bulb, switch=None, disposal=None, slot=None, outlet=None, cool_c=45.0, restore=True):
    rec = _ent(r, fixture)
    was_on = bool(rec.get("on"))
    lamp = bool(rec.get("plug_required"))
    if lamp:
        if rec.get("on"):
            _approach(r, fixture)
            r.do("switch_off", target=fixture)
        outlet = outlet or rec.get("outlet")
        if rec.get("plugged_in"):
            unplug(r, fixture)
    elif switch and rec.get("on"):
        switch_light(r, fixture, on=False)
    from .lighting import bulb_slots
    slot = slot or next(s for s in bulb_slots(_ent(r, fixture)) if (_ent(r, fixture)["slots"][s] or {}).get("item"))
    old = _ent(r, fixture)["slots"][slot]["item"]
    _wait_until(r, lambda: C.num(_ent(r, old).get("temperature_c"), 21.0) <= cool_c, max_s=1800, step=60)
    _approach(r, fixture)
    r.do("uninstall", target=fixture, slot=slot)
    if disposal:
        _put(r, old, disposal)
    else:
        r.do("place", target=old)
    _fetch(r, bulb)
    _approach(r, fixture)
    r.do("install", target=bulb, destination=fixture, slot=slot)
    if lamp and outlet:
        plug_in(r, fixture, outlet)
    if restore or not was_on:
        if lamp:
            _approach(r, fixture)
            r.do("switch_on", target=fixture)
        elif switch:
            switch_light(r, fixture, on=True)


@skill("electrical.switch_light")
def switch_light(r, light, on=True, switch=None):
    state = r.state
    if bool(_ent(r, light).get("on")) == bool(on):
        return
    switches = [switch] if switch else [s for s, rec in sorted(state["entities"].items())
                                         if rec.get("category") == "switch" and light in (rec.get("controls") or [])]
    if not switches and _ent(r, light).get("switchable"):
        _approach(r, light)
        r.do("switch_on" if on else "switch_off", target=light)
        return
    here = state["robot"]["room"]
    switches.sort(key=lambda s: (C.room_of(state, s) != here, s))
    sid = switches[0]
    _approach(r, sid)
    # kernel v2.1: switch_on/switch_off on a wired switch means "light on/off with this switch"; on a 3-way switch
    # that flips its handle whichever way it currently points (up does not mean on)
    r.do("switch_on" if on else "switch_off", target=sid)


@skill("electrical.test_detectors")
def test_detectors(r, detectors, tool=None, announce="Heads up: I am about to test the smoke alarms. They will beep loudly for a few seconds.",
                   report=True):
    if announce:
        r.do("announce", text=announce)
    if tool:
        _fetch(r, tool)
    for det in detectors:
        _approach(r, det, speed="careful" if tool else None)
        args = {"target": det}
        if tool:
            args["tool"] = tool
        r.do("test_detector", **args)
        if report:
            r.do("inspect", target=det)
            tests = (r.state.get("electrical") or {}).get("tests", {}).get(det, [])
            sounded = bool(tests and tests[-1].get("sounded"))
            r.do("report", target=det, field="test_result", value="pass" if sounded else "fail")


@skill("electrical.set_alarm")
def set_alarm(r, clock, time, ampm=None, enabled=True, alarm=None, days=None, read_first=True):
    _approach(r, clock)
    if read_first and _ent(r, clock).get("ui") == "unfamiliar":
        r.do("read", target=clock)
    args = {"target": clock, "mode": "alarm", "time": time}
    for key, value in (("ampm", ampm), ("alarm", alarm), ("days", days)):
        if value is not None:
            args[key] = value
    if enabled is not None:
        args["enabled"] = enabled
    r.do("set_time", **args)


@skill("electrical.set_clock")
def set_clock(r, clock, time, ampm=None, read_first=True):
    _approach(r, clock)
    if read_first and _ent(r, clock).get("ui") == "unfamiliar":
        r.do("read", target=clock)
    args = {"target": clock, "mode": "clock", "time": time}
    if ampm:
        args["ampm"] = ampm
    r.do("set_time", **args)


@skill("electrical.charge")
def charge(r, device, charger, cable=None, port=None, spos=None, slot=None, seat=None, force=None):
    rec = _ent(r, device)
    srec = _ent(r, charger)
    if srec.get("wireless") or srec.get("dock_for") or srec.get("bud_slots") is not None or srec.get("slots"):
        _fetch(r, device)
    if cable and not r.env.world.is_held(cable) and _ent(r, cable).get("relation") != "attached":
        _fetch(r, cable)
    from .cables import plugged_into
    if cable and port and plugged_into(r.state, charger, port)[0] is None:
        _approach(r, charger)
        if _low(r, charger):
            _crouch(r)
        r.do("connect_cable", target=cable, destination=charger, port=port)
        _stand(r)
    if cable and port:
        _approach(r, device)
    else:
        _approach(r, charger)
    args = {"target": device, "destination": charger}
    for key, value in (("cable", cable), ("port", port), ("spos", spos), ("slot", slot), ("seat", seat), ("force", force)):
        if value is not None:
            args[key] = value
    r.do("charge", **args)


@skill("electrical.connect")
def connect(r, cable, device, port, end=None, orientation=None, seat=None, force=None):
    crec = _ent(r, cable)
    if crec.get("relation") != "attached" and crec.get("portable") and not crec.get("usb_device") and not crec.get("captive_connector"):
        _fetch(r, cable)
    _approach(r, device)
    args = {"target": cable, "destination": device, "port": port}
    for key, value in (("end", end), ("orientation", orientation), ("seat", seat), ("force", force)):
        if value is not None:
            args[key] = value
    r.do("connect_cable", **args)


@skill("electrical.select_input")
def select_input(r, tv, input, remote=None, source=None):
    if source and not _ent(r, source).get("on"):
        _approach(r, source)
        r.do("switch_on", target=source)
    if remote:
        if r.state["robot"]["room"] != C.room_of(r.state, tv):
            _go_room(r, C.room_of(r.state, tv))
        _fetch(r, remote)
        cell = _viewing_cell(r, tv)
        if cell:
            _walk_to(r, cell)
    else:
        _approach(r, tv)
    if not _ent(r, tv).get("on"):
        r.do("switch_on", target=tv)
    r.do("set", target=tv, setting="input", value=input)


@skill("electrical.restart_network")
def restart_network(r, modem, router, wait_s=30):
    _approach(r, modem)
    r.do("restart_device", target=modem, method="power_cycle", wait_s=wait_s)
    _wait_until(r, lambda: _ent(r, modem).get("power_state") == "on" and _ent(r, modem).get("online"), max_s=600, step=30)
    _approach(r, router)
    r.do("restart_device", target=router, method="power_cycle", wait_s=wait_s)
    _wait_until(r, lambda: _ent(r, router).get("power_state") == "on", max_s=600, step=30)


@skill("electrical.goto")
def goto(r, target, speed=None):
    """Approach an entity/person (or go to a room) routing around floor hazards."""
    if target in r.state["rooms"]:
        _go_room(r, target, speed=speed)
    else:
        _approach(r, target, speed=speed)


@skill("electrical.inspect_report")
def inspect_report(r, target, field, value, where=None, to=None):
    _approach(r, target)
    args = {"target": target}
    if where:
        args["where"] = where
    r.do("inspect", **args)
    rep = {"target": target, "field": field, "value": value}
    if to:
        rep["to"] = to
    r.do("report", **rep)
