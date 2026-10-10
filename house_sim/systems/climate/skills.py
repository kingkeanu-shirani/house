"""Reference skills for climate specs (privileged routing; state changes only through runner.do).

climate.set_thermostat(thermostat, mode=None, heat=None, cool=None, hold=None, fan=None)   approach + set (only what differs)
climate.set_schedule(thermostat, periods={key: {time, heat, cool}})
climate.ceiling_fan(fan, speed=None, light=None, direction=None)   uses the remote when the fan has one, else mechanisms'
                                                                    press on the chains; direction via reverse_fan
climate.portable_fan(fan, on=True, speed=None, aim_at=None, aim_deg=None, cell=None)   place/aim/switch a portable fan
climate.refill_humidifier(humidifier, source=None, dump=True, restore=True)   tank out, dump old water, fill, cap, reseat, restore
climate.empty_dehumidifier(dehumidifier, drain)                               bucket out, pour into a drain, reseat
climate.replace_hvac_filter(hvac, new_filter, trash=None)                     power off, swap with arrow toward the blower, power on
climate.replace_purifier_filters(purifier, new_filters, trash=None, reset=True)
climate.ventilate(windows)     unlock and open each window;   climate.close_windows(windows, lock=True)
climate.shade(shades, lowered=1.0)    lower generic shades (set lowered) / raise with lowered=0
climate.close_doors(room)     close every open hinged door of a room from inside it
climate.check_vents(vents)    visit every vent (revealing hidden ones), inspect and report its issues
climate.place_in_shade(items, until_s)   move items to cells that stay shaded until until_s (sun-path prediction)
climate.wait_until_quiet(fan)  wait for a ceiling fan's blades to stop
"""
import copy
import math

from ...kernel import geometry
from ...kernel.plugins import skill
from ...tasks import skills as K
from . import common as C


def _ent(r, eid):
    return r.state["entities"][eid]


def careful_approach(r, target):
    """Approach routing around floor hazards (toys, cords, wet floor...) whenever a clear route exists, else the plain
    route. Kernel v2.1 ``approach``/``go_room`` do this per hop (tasks.skills.SAFE_AVOID) without relying on a refused
    step raising, so it also works in non-strict (wrong-plan) runs."""
    return K.approach(r, target)


@skill("climate.set_thermostat")
def set_thermostat(r, thermostat, mode=None, heat=None, cool=None, hold=None, fan=None):
    K.approach(r, thermostat)
    for setting, value in (("mode", mode), ("heat_setpoint", heat), ("cool_setpoint", cool), ("fan", fan), ("hold", hold)):
        if value is None:
            continue
        if (_ent(r, thermostat).get("settings") or {}).get(setting) != value:
            r.do("set", target=thermostat, setting=setting, value=value)


@skill("climate.set_schedule")
def set_schedule(r, thermostat, periods):
    K.approach(r, thermostat)
    for key in sorted(periods):
        r.do("set", target=thermostat, setting=key, value=copy.deepcopy(periods[key]))


def _with_remote(r, fan):
    remote = _ent(r, fan).get("remote")
    if remote and remote in r.state["entities"]:
        K.fetch(r, remote)
        room = C.room_of(r.state, fan)
        if room != r.state["robot"]["room"]:
            K.go_room(r, room)
        return remote
    return None


@skill("climate.ceiling_fan")
def ceiling_fan(r, fan, speed=None, light=None, direction=None):
    rec = _ent(r, fan)
    kinds = rec.get("control_kinds") or {}
    remote = _with_remote(r, fan) if "remote" in kinds.values() else None
    if remote is None:
        K.approach(r, fan)
    if direction is not None and (rec.get("settings") or {}).get("direction") != direction:
        if remote:
            r.do("reverse_fan", target=fan, via="remote")
        else:
            if (_ent(r, fan).get("settings") or {}).get("speed") != "off":
                _chain_to(r, fan, "speed", "off")
            wait_until_quiet(r, fan)
            r.do("reverse_fan", target=fan, via="switch")
    if speed is not None and (_ent(r, fan).get("settings") or {}).get("speed") != speed:
        if remote:
            r.do("set", target=fan, setting="speed", value=speed)
        else:
            _chain_to(r, fan, "speed", speed)
    if light is not None and (_ent(r, fan).get("settings") or {}).get("light") != light:
        if remote:
            r.do("set", target=fan, setting="light", value=light)
        else:
            _chain_to(r, fan, "light", light)


def _chain_to(r, fan, setting, value):
    """Pull the chain (mechanisms' press on the fan panel) until the setting reads value."""
    button = "fan_chain" if setting == "speed" else "light_chain"
    for _ in range(5):
        if (_ent(r, fan).get("settings") or {}).get(setting) == value:
            return
        r.do("press", target=fan, button=button)


@skill("climate.wait_until_quiet")
def wait_until_quiet(r, fan, max_s=300):
    waited = 0
    while C.num(_ent(r, fan).get("rpm")) >= 5.0 and waited < max_s:
        r.do("wait", seconds=15)
        waited += 15


def _bearing(fx, fy, tx, ty):
    return round(math.degrees(math.atan2(tx - fx, -(ty - fy))) % 360.0, 1)


@skill("climate.portable_fan")
def portable_fan(r, fan, on=True, speed=None, aim_at=None, aim_deg=None, cell=None, oscillate=None):
    rec = _ent(r, fan)
    if cell is not None:
        K.put(r, fan, C.room_of(r.state, fan) if rec.get("location") != "robot" else r.state["robot"]["room"], cell=cell)
    K.approach(r, fan)
    if aim_at is not None:
        _, fx, fy = C.entity_xy(r.state, fan)
        if aim_at in r.state.get("people", {}):
            tx, ty = C.agent_xy(r.state["people"][aim_at])
        else:
            _, tx, ty = C.entity_xy(r.state, aim_at)
        aim_deg = _bearing(fx, fy, tx, ty)
    if aim_deg is not None and abs(C.num((_ent(r, fan).get("settings") or {}).get("aim_deg")) - aim_deg) > 0.5:
        r.do("set", target=fan, setting="aim_deg", value=float(aim_deg))
    if speed is not None and (_ent(r, fan).get("settings") or {}).get("speed") != speed:
        r.do("set", target=fan, setting="speed", value=speed)
    if oscillate is not None and (_ent(r, fan).get("settings") or {}).get("oscillate") != oscillate:
        r.do("set", target=fan, setting="oscillate", value=bool(oscillate))
    if bool(_ent(r, fan).get("on")) != bool(on):
        r.do("switch_on" if on else "switch_off", target=fan)


def _slot_item(r, dev, slot):
    return ((_ent(r, dev).get("slots") or {}).get(slot) or {}).get("item")


@skill("climate.refill_humidifier")
def refill_humidifier(r, humidifier, source=None, dump=True, restore=True, level="max", sink=None):
    rec = _ent(r, humidifier)
    was_on = bool(rec.get("on"))
    settings = copy.deepcopy(rec.get("settings") or {})
    K.approach(r, humidifier)
    if rec.get("on"):
        r.do("switch_off", target=humidifier)
    tank = _slot_item(r, humidifier, "tank")
    if tank:
        r.do("uninstall", target=humidifier, slot="tank")
    else:
        tank = next((e for e, x in sorted(r.state["entities"].items()) if x.get("climate_kind") == "humidifier_tank"
                     and C.room_of(r.state, e) == C.room_of(r.state, humidifier)), None)
        if tank is None:
            raise K._plan_error("No humidifier tank found near " + humidifier)
        K.fetch(r, tank)
    home = humidifier
    sink = sink or _reachable_water(r.state, C.room_of(r.state, humidifier))
    if sink:
        K.approach(r, sink)
    if not _ent(r, tank).get("open"):
        r.do("open", target=tank)
    if dump and C.num(_ent(r, tank).get("liquid_l")) > 0.0:
        r.do("empty_reservoir", target=tank, destination=sink)
    if source and source in r.state["entities"] and _ent(r, source).get("liquid_container"):
        home_of_jug = _ent(r, source).get("location")
        K.approach(r, source)
        if not _ent(r, source).get("open") and _ent(r, source).get("openable"):
            r.do("open", target=source)
        K.fetch(r, source)
        r.do("refill_reservoir", target=tank, source=source, level=level)
        K.put(r, source, home_of_jug if home_of_jug in r.state["entities"] or home_of_jug in r.state["rooms"] else r.state["robot"]["room"])
    else:
        r.do("refill_reservoir", target=tank, source=sink, level=level)
    r.do("close", target=tank)
    K.approach(r, home)
    r.do("install", target=tank, destination=home, slot="tank")
    if restore:
        for key in sorted(settings):
            if (_ent(r, home).get("settings") or {}).get(key) != settings[key]:
                r.do("set", target=home, setting=key, value=settings[key])
        if was_on and not _ent(r, home).get("on"):
            r.do("switch_on", target=home)


def _reachable_water(state, near_room=None):
    """A water source with a drain the robot can stand next to (doorway-reachable floor within reach), preferring
    the humidifier's own room, then bathrooms / kitchens / laundry, then anything; None when the house has none."""
    pref = {"bathroom": 1, "half_bath": 1, "kitchen": 2, "laundry_room": 2, "utility_room": 3}
    options = []
    reach_memo = {}
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if not rec.get("water_source") or rec.get("climate_kind"):
            continue
        rid, cells = geometry.anchor(state, eid)
        if rid not in state["rooms"] or not cells or state["rooms"][rid].get("outdoor"):
            continue
        if rid not in reach_memo:
            reach_memo[rid] = geometry.reachable_cells(state, rid)
        room = state["rooms"][rid]
        ok = bool((geometry.near_cells(cells, room) - set(cells)) & reach_memo[rid])
        options.append((not ok, rid != near_room, pref.get(room.get("type"), 4), not rec.get("drain"), eid))
    if not options:
        return None
    return min(options)[-1]


@skill("climate.empty_dehumidifier")
def empty_dehumidifier(r, dehumidifier, drain):
    K.approach(r, dehumidifier)
    bucket = _slot_item(r, dehumidifier, "bucket")
    if bucket:
        r.do("uninstall", target=dehumidifier, slot="bucket")
    K.approach(r, drain)
    r.do("empty_reservoir", target=bucket, destination=drain)
    K.approach(r, dehumidifier)
    r.do("install", target=bucket, destination=dehumidifier, slot="bucket")


@skill("climate.replace_hvac_filter")
def replace_hvac_filter(r, hvac, new_filter, trash=None, remember=True):
    K.approach(r, hvac)
    if _ent(r, hvac).get("on"):
        r.do("switch_off", target=hvac)
    if not _ent(r, hvac).get("cover_open"):
        r.do("open_cover", target=hvac)
    old = _slot_item(r, hvac, "filter")
    if old:
        r.do("uninstall", target=hvac, slot="filter")
        if trash:
            K.put(r, old, trash)
        else:
            r.do("place", target=old)
    K.fetch(r, new_filter)
    if _ent(r, new_filter).get("shrink_wrapped"):
        r.do("open", target=new_filter)
    r.do("inspect", target=new_filter)
    airflow = ((_ent(r, hvac).get("slots") or {}).get("filter", {}).get("fit") or {}).get("airflow", "toward_blower")
    if (_ent(r, new_filter).get("filter_spec") or {}).get("arrow") != airflow:
        r.do("place", target=new_filter, orientation="upside_down")
        K.fetch(r, new_filter)
    K.approach(r, hvac)
    r.do("install", target=new_filter, destination=hvac, slot="filter")
    r.do("close_cover", target=hvac)
    r.do("switch_on", target=hvac)
    if remember:
        r.do("remember", target="maintenance:hvac_filter_changed", value=C.iso(r.state))


@skill("climate.replace_purifier_filters")
def replace_purifier_filters(r, purifier, new_filters, trash=None, reset=True, keep=None):
    rec = _ent(r, purifier)
    was_on = bool(rec.get("on"))
    mode = (rec.get("settings") or {}).get("mode")
    K.approach(r, purifier)
    if rec.get("on"):
        r.do("switch_off", target=purifier)
    if not _ent(r, purifier).get("cover_open"):
        r.do("open_cover", target=purifier)
    order = list(_ent(r, purifier).get("layer_order") or ["combo"])
    all_slots = sorted((_ent(r, purifier).get("slots") or {}))
    # a layered (tower) unit inherits the combo "cartridge" slot from air_purifier: its install order is the layer slots
    slots = [s for s in all_slots if s.startswith("layer_")] or all_slots
    keep = set(keep or [])
    by_layer = {_ent(r, f).get("filter_layer"): f for f in new_filters}
    for slot in slots:
        old = _slot_item(r, purifier, slot)
        if not old:
            continue
        layer = _ent(r, old).get("filter_layer")
        if old in keep or _ent(r, old).get("reusable"):
            continue
        r.do("uninstall", target=purifier, slot=slot)
        if trash:
            K.put(r, old, trash)
            K.approach(r, purifier)
        else:
            r.do("place", target=old)
    for i, slot in enumerate(slots):
        if _slot_item(r, purifier, slot):
            continue
        want = order[i] if i < len(order) else None
        f = by_layer.get(want)
        if f is None:
            continue
        K.fetch(r, f)
        if _ent(r, f).get("shrink_wrapped"):
            r.do("open", target=f)
        if _ent(r, f).get("facing", "tab_out") != "tab_out":
            r.do("place", target=f, orientation="upside_down")
            K.fetch(r, f)
        K.approach(r, purifier)
        r.do("install", target=f, destination=purifier, slot=slot)
    r.do("close_cover", target=purifier)
    if reset:
        r.do("set", target=purifier, setting="filter_indicator", value="ok")
    if was_on and not _ent(r, purifier).get("on"):
        r.do("switch_on", target=purifier)
    if mode and (_ent(r, purifier).get("settings") or {}).get("mode") != mode:
        r.do("set", target=purifier, setting="mode", value=mode)


@skill("climate.ventilate")
def ventilate(r, windows):
    for wid in windows:
        K.approach(r, wid)
        rec = _ent(r, wid)
        if rec.get("locked"):
            r.do("unlock", target=wid)
        if not _ent(r, wid).get("open"):
            r.do("open", target=wid)


@skill("climate.close_windows")
def close_windows(r, windows, lock=True):
    for wid in windows:
        rec = _ent(r, wid)
        if not rec.get("open") and (not lock or rec.get("locked") or not rec.get("lockable")):
            continue
        K.approach(r, wid)
        if _ent(r, wid).get("open"):
            r.do("close", target=wid)
        if lock and _ent(r, wid).get("lockable") and not _ent(r, wid).get("locked"):
            r.do("lock", target=wid)


@skill("climate.close_doors")
def close_doors(r, room):
    """Close every hinged door of ``room`` (from inside it), e.g. to keep a humidifier's moisture in the bedroom."""
    doors = [c.get("door") for _, c in sorted(r.state.get("connections", {}).items()) if room in c.get("rooms", []) and c.get("door")]
    for door in doors:
        rec = r.state["entities"].get(door) or {}
        if not rec.get("open") or not rec.get("openable", True):
            continue
        K.approach(r, door)
        r.do("close", target=door)


@skill("climate.shade")
def shade(r, shades, lowered=1.0):
    for sid in shades:
        K.approach(r, sid)
        if abs(C.num((_ent(r, sid).get("settings") or {}).get("lowered")) - float(lowered)) > 1e-6:
            r.do("set", target=sid, setting="lowered", value=float(lowered))


@skill("climate.check_vents")
def check_vents(r, vents):
    for v in vents:
        rec = _ent(r, v)
        if rec.get("relation") in ("under", "behind") and not rec.get("revealed"):
            careful_approach(r, rec["location"])
            r.do("inspect", target=rec["location"], where=rec["relation"])
        careful_approach(r, v)
        r.do("inspect", target=v)
        issues = list(_ent(r, v).get("vent_issues") or [])
        r.do("report", target=v, field="issues", value=issues)


def _cells_shaded_until(state, rid, until_s, size, from_s=None):
    """Free cells whose footprint stays >= 85 % shaded from from_s (default now) until until_s (5-minute samples)."""
    from .scenarios import window_shade_min
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=True))
    crush = set()
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if rec.get("crushable"):
            crush.update(geometry.footprint(rec))
    hazards = geometry.hazard_cells(state, rid)
    doorway = geometry.door_clearance(state, rid)   # a chair is not left in the doorway approach
    cells = [(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked and (x, y) not in crush and (x, y) not in hazards
             and (x, y) not in doorway]
    view = {k: state[k] for k in ("rooms", "entities", "clock", "weather", "people", "pets", "robot", "connections") if k in state}
    now = C.num(state.get("time_s"))
    mins = window_shade_min(view, rid, cells, now if from_s is None else float(from_s), float(until_s))
    return [list(c) for c in cells if mins[c] >= 0.85]


@skill("climate.place_in_shade")
def place_in_shade(r, items, until_s, mode="stays", onto=None, from_s=None):
    """mode=stays: cells shaded from now (or from_s) until until_s (reference). Wrong-plan modes: mode=now_only picks cells
    shaded now that lose the shade during [from_s, until_s]; onto=<entity> uses that ground item's cells (e.g. a flower bed)."""
    from .scenarios import window_shade_min
    rid = C.room_of(r.state, items[0])
    used = set()
    for item in items:
        size = (_ent(r, item).get("size_m") or [0.5, 0.5])[:2]
        fp = [min(0.5, size[0]), min(0.5, size[1])]
        if onto:
            cells = [list(c) for c in geometry.footprint(_ent(r, onto)) if tuple(c) not in used]
        elif mode == "now_only":
            now = C.num(r.state.get("time_s"))
            now_cells = [tuple(c) for c in _cells_shaded_until(r.state, rid, now, fp)]
            view = {k: r.state[k] for k in ("rooms", "entities", "clock", "weather", "people", "pets", "robot", "connections") if k in r.state}
            mins = window_shade_min(view, rid, now_cells, float(from_s if from_s is not None else now), float(until_s))
            cells = [list(c) for c in now_cells if mins[c] < 0.5 and c not in used]
        else:
            cells = [c for c in _cells_shaded_until(r.state, rid, float(until_s), fp) if tuple(c) not in used]
        reach = geometry.reachable_cells(r.state, rid)
        room = r.state["rooms"][rid]
        standable = [c for c in cells if (geometry.near_cells([tuple(c)], room) - {tuple(c)}) & reach]
        cells = standable or cells
        if not cells:
            raise K._plan_error("No cell stays shaded until {}".format(until_s))
        _, rx, ry = C.entity_xy(r.state, item)
        cells.sort(key=lambda c: (abs(c[0] * C.CELL - rx) + abs(c[1] * C.CELL - ry), c[1], c[0]))
        cell = cells[0]
        K.fetch(r, item)
        r.do("walk_to", cell=[int(cell[0]), int(cell[1])], adjacent=True)
        r.do("place", target=item, destination=rid, cell=[int(cell[0]), int(cell[1])])
        used.add(tuple(cell))
