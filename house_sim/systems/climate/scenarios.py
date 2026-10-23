"""Scenario helpers for climate task specs (setup op ``helper``). Each returns a dict of bindings.

climate.hvac            rebuild the house's HVAC: system furnace_ac|heat_pump|boiler|mini_split, mode, units, setpoints,
                        hold, schedule overrides, thermostat type/room, setback; -> thermostat, hvac, thermo_room, units,
                        registers, returns, radiators, filter, spare, condenser, mini_splits
climate.rooms           set room temperature/RH/CO2/odour (by id, room type, "conditioned" or "all")
climate.weather         outdoor temperature/humidity/condition/wind/AQI/pollen, diurnal cycle, latitude
climate.occupant        place/update a person with a comfort band and hidden climate preferences (_climate)
climate.ceiling_fan     a ceiling fan (pull_chain | remote) with speed/direction/light, optional wall switch
climate.portable_fan    a portable fan at a cell or next to an entity, aimed, optional outlet
climate.space_heater    a space heater near something, with outlet kind and tip-switch state
climate.humidifier      humidifier + tank (water, age, contamination), a sink and an optional distilled-water jug
climate.dehumidifier    dehumidifier + bucket (+ hose and floor drain)
climate.purifier        purifier (combo | layered) with loaded filters, matching spares and a wrong-model spare, trash
climate.hvac_filter_job air handler with a loaded filter, matching spare, wrong-size spare, trash can
climate.vents           register states: closed / hidden under a rug / behind a sofa / dusty, household-closed ones
climate.windows         pick or create windows in a room (facing, open, screen, child stop)
climate.shades          generic window shades (covers_window) for every window of a room
climate.glare_scene     TV in the direct beam of exactly one sunlit window, shades on every window, lights off
climate.patio_shade     outdoor patio with house wall + tree shade, chairs in the sun, a flower bed, shade_watch window
climate.sink            ensure a water source / drain fixture in a room (built from core generic_item)
climate.energy_house    metered devices (essentials, vampire loads, a dishwasher job) and a time-of-use tariff
climate.baseline        run the configured house idle for N hours and return energy/temperature figures (LAST op)
"""
import copy
import datetime
import math

from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from . import furnish, hvac
from . import common as C
from . import sun as S

HVAC_KINDS = ("hvac_system", "thermostat", "register", "return_grille", "radiator", "mini_split", "outdoor_condenser")
SCRIPTED_WEATHER_S = 1.0e9     # weather.override_until_s of a scripted weather (about 32 years: past any episode)
GUST_FACTOR = 1.5              # gusts / mean wind, as the generation-2 weather process (generator/climate.py)


def _room(ctx, value, required=True):
    cfg = ctx.config
    if value is None:
        return None
    if value in cfg["rooms"]:
        return value
    rooms = ctx.placer.rooms(value)
    if not rooms:
        if required:
            raise SpecError("climate helper: no room of type {}".format(value))
        return None
    return rooms[0] if len(rooms) == 1 else ctx.rng.choice(rooms)


def _remove(cfg, eids):
    doomed = list(eids)
    changed = True
    while changed:
        changed = False
        for eid, rec in list(cfg["entities"].items()):
            if rec.get("location") in doomed and eid not in doomed:
                doomed.append(eid)
                changed = True
    for eid in doomed:
        cfg["entities"].pop(eid, None)
    for rec in cfg["entities"].values():
        for info in (rec.get("slots") or {}).values():
            if isinstance(info, dict) and info.get("item") in doomed:
                info["item"] = None
        for key in ("remote", "condenser", "hose", "outlet"):
            if rec.get(key) in doomed:
                rec[key] = None


def _units(ctx, units):
    if units in ("F", "C"):
        return units
    return "F" if (ctx.config.get("metadata") or {}).get("units") == "imperial" else "C"


def _start(ctx):
    return datetime.datetime.fromisoformat((ctx.config.get("clock") or {}).get("start", "2026-10-09T10:00:00"))


def _sun_now(ctx):
    view = {"clock": ctx.config.get("clock") or {}, "time_s": 0.0, "weather": ctx.config.get("weather") or {}}
    return S.sun_position(view)


# ---------------------------------------------------------------------------------------------
# HVAC

@scenario_helper("climate.hvac")
def hvac_helper(ctx, system="furnace_ac", mode=None, units=None, heat_c=21.0, cool_c=24.5, hold="none", thermostat_type="thermostat",
                thermostat_room=None, schedule=None, setpoints=None, setback_c=0.0, night_c=1.5, register_kind="supply_register",
                filter_dirt=0.3, merv=8, nominal=None, spare=True, power_source="c_wire", battery_charge=1.0, zone_rooms=None,
                room_temp_c=None, condenser=True, schedule_enabled=None):
    cfg = ctx.config
    _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("climate_kind") in HVAC_KINDS or
                  (r.get("climate_kind") == "hvac_filter" and r.get("location") in cfg["entities"]
                   and cfg["entities"][r["location"]].get("climate_kind") in HVAC_KINDS)])
    units = _units(ctx, units)
    troom = _room(ctx, thermostat_room) if thermostat_room else None
    out = furnish.build_hvac(cfg, ctx.placer, ctx.rng, system, units, mode, heat_c, cool_c, thermostat_type, troom, register_kind,
                             condenser, merv, filter_dirt, spare, nominal, set_temps=room_temp_c is None, setback_c=setback_c, night_c=night_c)
    tid = out.get("thermostat")
    if tid:
        rec = cfg["entities"][tid]
        s = rec["settings"]
        if schedule_enabled is not None:
            rec["schedule_enabled"] = bool(schedule_enabled)
        for key, val in sorted((schedule or {}).items()):
            s[key] = copy.deepcopy(val)
        furnish.sync_period(cfg, tid)
        for key, val in sorted((setpoints or {}).items()):
            s[key] = val
        if hold and hold != "none":
            s["hold"] = hold
            if hold == "temporary":
                start = _start(ctx)
                nxt, key = hvac.next_period_start(s, start)
                rec["hold_until_s"] = round((nxt - start).total_seconds(), 1) if nxt else None
                rec["hold_until"] = nxt.isoformat(timespec="minutes") if nxt else None
        if power_source == "battery":
            rec["power_source"] = "battery"
            rec["cover_open"] = False
            rec["slots"] = {"cell_1": {"accepts": ["battery_aa"], "item": None, "access": "cover"},
                            "cell_2": {"accepts": ["battery_aa"], "item": None, "access": "cover"}}
            for slot in ("cell_1", "cell_2"):
                b = ctx.placer.make("generic_item", tid, label="AA battery", category="battery", part_type="battery_aa", charge=battery_charge,
                                    size_m=[0.014, 0.014, 0.05], mass_kg=0.023)
                cfg["entities"][b]["relation"] = "part"
                cfg["entities"][b]["slot"] = slot
                rec["slots"][slot]["item"] = b
        if zone_rooms:
            rec["zone_rooms"] = [(_room(ctx, z)) for z in zone_rooms]
        out["mode"] = s.get("mode")
        out["heat_setpoint"] = s.get("heat_setpoint")
        out["cool_setpoint"] = s.get("cool_setpoint")
    if room_temp_c is not None:
        for rid, room in cfg["rooms"].items():
            if not room.get("outdoor"):
                room["temperature_c"] = float(room_temp_c)
    # wide wall devices run along their wall inside the room; a register/grille/radiator/thermostat walled off by
    # furniture is moved to a reachable wall cell
    furnish.fit_wall_items(cfg)
    furnish.resite_inaccessible(cfg, ctx.placer, ctx.rng)
    return out


@scenario_helper("climate.rooms")
def rooms_helper(ctx, temps=None, rh=None, co2=None, odor=None, pm25=None):
    cfg = ctx.config

    def targets(key):
        if key == "all":
            return [r for r in sorted(cfg["rooms"]) if not cfg["rooms"][r].get("outdoor")]
        if key == "conditioned":
            return [r for r in sorted(cfg["rooms"]) if not cfg["rooms"][r].get("outdoor") and cfg["rooms"][r]["type"] not in furnish.NO_HVAC]
        if key in cfg["rooms"]:
            return [key]
        return [r for r in sorted(cfg["rooms"]) if cfg["rooms"][r]["type"] == key]
    for field, values, cast in (("temperature_c", temps, float), ("humidity", rh, float), ("co2_ppm", co2, float), ("odor", odor, float),
                                ("pm25", pm25, float)):
        for key in sorted(values or {}, key=lambda k: (k not in ("all", "conditioned"), k)):
            for rid in targets(key):
                cfg["rooms"][rid][field] = cast(values[key])
    return {}


@scenario_helper("climate.tidy_doorways")
def tidy_doorways_helper(ctx):
    """Deprecated since kernel v2.3 (generated houses keep loose floor items out of doorway clearance and off every
    route; the climate examples no longer use it): moves tall loose floor items out of door clearance."""
    return {"tidied": furnish.tidy_doorways(ctx.config)}


@scenario_helper("climate.weather")
def weather_helper(ctx, temperature_c=None, humidity=None, condition=None, wind_mps=None, wind_from_deg=None, aqi=None, pollen=None,
                   rain_mm_h=None, diurnal=None, latitude=None, tariff=None, hardness_ppm=None):
    """The episode's outdoor conditions. On a generation-2 house (``weather.process``: the stateless weather process of
    TIER1_SWEEP 13.2) the values given are a scripted override for the whole episode: ``weather.override_until_s`` is
    set past any episode (SCRIPTED_WEATHER_S), so a process that runs yields to them (13.2: "scripted weather events set
    weather.override_until_s and the process yields while it lasts"); a diurnal cycle is scripted the same way."""
    cfg = ctx.config
    wx = cfg.setdefault("weather", {})
    given = False
    for key, val in (("temperature_c", temperature_c), ("humidity", humidity), ("condition", condition), ("wind_mps", wind_mps),
                     ("wind_from_deg", wind_from_deg), ("aqi", aqi), ("pollen", pollen), ("rain_mm_h", rain_mm_h)):
        if val is not None:
            wx[key] = val
            given = True
    if wx.get("process") and (given or diurnal):
        wx["override_until_s"] = SCRIPTED_WEATHER_S
    # a generation-2 weather carries the process's gust and snowfall too: keep them consistent with what is scripted
    if wind_mps is not None and "gust_mps" in wx:
        wx["gust_mps"] = round(C.num(wind_mps) * GUST_FACTOR, 1)
    if condition is not None and condition != "snow" and "snow_cm_h" in wx:
        wx["snow_cm_h"] = 0.0
    if condition in ("rain", "storm") and rain_mm_h is None:
        wx["rain_mm_h"] = 15.0 if condition == "storm" else 4.0
    if condition and condition not in ("rain", "storm") and rain_mm_h is None:
        wx["rain_mm_h"] = 0.0
    sec = cfg.setdefault(C.SECTION, {})
    if diurnal:
        sec.setdefault("outdoor", {})["diurnal"] = dict(diurnal)
    if latitude is not None:
        cfg.setdefault("clock", {})["latitude"] = latitude
    if tariff:
        sec["tariff"] = dict(tariff)
    if hardness_ppm is not None:
        cfg.setdefault("utilities", {})["water_hardness_ppm"] = hardness_ppm
    return {"outdoor_c": wx.get("temperature_c")}


@scenario_helper("climate.occupant")
def occupant_helper(ctx, id="resident_1", room=None, role=None, comfort=None, target_c=None, posture=None, activity=None, awake=None,
                    hidden=None, near=None, label=None, authority=None, allergies=None):
    cfg = ctx.config
    people = cfg.setdefault("people", {})
    p = people.setdefault(id, {"label": label or id.replace("_", " ").title(), "role": role or "adult", "consent": True, "awake": True,
                               "mood": "neutral", "permissions": [], "preferences": {}})
    if role:
        p["role"] = role
        p.setdefault("authority", 2 if role == "adult" else (1 if role == "elderly" else 0))
    if authority is not None:
        p["authority"] = authority
    if label:
        p["label"] = label
    if comfort:
        p.setdefault("comfort", {})["temp_c"] = [float(comfort[0]), float(comfort[1])]
    elif target_c is not None:
        p.setdefault("comfort", {})["temp_c"] = [float(target_c) - 1.0, float(target_c) + 1.0]
    for key, val in (("posture", posture), ("activity", activity), ("awake", awake)):
        if val is not None:
            p[key] = val
    if allergies is not None:
        p["allergies"] = list(allergies)
    if hidden:
        p["_climate"] = copy.deepcopy(hidden)
    p.pop("away", None)
    if room:
        rid = _room(ctx, room)
        p["location"] = rid
        p["pos"] = None
        cell = None
        state = ctx.state()
        if near and near in cfg["entities"]:
            _, cells = geometry.anchor(dict(state, robot={"room": rid, "hands": {}, "pos": None}), near)
            # beside the thing (kernel agent rule: free, outside doorway clearance, cuts no route)
            for c in sorted(geometry.near_cells(cells or [], cfg["rooms"][rid]) - set(cells or [])):
                if geometry.free_floor_cell(state, rid, near=c, keep_access=True) == list(c):
                    cell = list(c)
                    break
            if cell is None and cells:
                cell = ctx.placer.agent_cell(rid, near=tuple(cells[0]))
        cell = cell or ctx.placer.agent_cell(rid)
        if cell is None:
            raise SpecError("climate.occupant: no free cell for {} in {}".format(id, rid))
        p["pos"] = list(cell)
    return {"person": id, "person_room": p.get("location")}


# ---------------------------------------------------------------------------------------------
# fans, heaters

def _make_remote(ctx, rid, device, kind="rf", surface=None):
    p = ctx.placer
    props = dict(label="ceiling fan remote" if kind == "rf" else "AC remote", category="remote", remote_kind=kind, controls_device=device,
                 battery_powered=True, battery_level=1.0, size_m=[0.05, 0.02, 0.15], mass_kg=0.08)
    rid_ = None
    if surface and surface in ctx.config["entities"]:
        rid_ = p.on(surface, "generic_item", **props)
    if rid_ is None:
        surf = furnish._surface_in(p, rid, ("nightstand", "side_table", "coffee_table", "desk", "dresser", "table", "tv_stand"))
        rid_ = p.on(surf, "generic_item", **props) if surf else None
    if rid_ is None:
        rid_ = p.floor_item(rid, "generic_item", **props)
    return rid_


@scenario_helper("climate.ceiling_fan")
def ceiling_fan_helper(ctx, room="living_room", control="pull_chain", speed="off", direction="ccw", light="off", has_light=True,
                       wall_switch=False, gate_on=True, remote_surface=None, mount_height_m=2.3, cell=None, replace=True):
    cfg = ctx.config
    rid = _room(ctx, room)
    if replace:
        _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("climate_kind") == "ceiling_fan" and r.get("location") == rid])
        _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("category") == "remote" and r.get("controls_device") not in cfg["entities"]
                      and r.get("label") == "ceiling fan remote"])
    cols, rows = geometry.grid_size(cfg["rooms"][rid])
    fid = ctx.placer.mount(rid, "remote_ceiling_fan" if control == "remote" else "ceiling_fan", height=mount_height_m,
                           cell=cell or [max(0, cols // 2 - 1), max(0, rows // 2 - 1)], has_light=bool(has_light),
                           label="{} ceiling fan".format(cfg["rooms"][rid]["label"].lower()))
    rec = cfg["entities"][fid]
    rec["settings"].update(speed=speed, direction=direction, light=light)
    rec["on"] = bool(gate_on)
    out = {"fan": fid, "fan_room": rid, "remote": None, "fan_switch": None, "fan_chain": None, "light_chain": None}
    if control != "remote":
        from .fans import add_chains
        chains = add_chains(cfg, ctx.reg, fid)
        out["fan_chain"] = chains.get("speed")
        out["light_chain"] = chains.get("light")
    if control == "remote":
        rem = _make_remote(ctx, rid, fid, "rf", remote_surface)
        rec["remote"] = rem
        out["remote"] = rem
    if wall_switch:
        sw = ctx.placer.mount(rid, "light_switch", height=1.2, cell=furnish._free_wall_cell(ctx.placer, rid, ctx.rng), label="fan and light switch",
                              controls=[fid], on=bool(gate_on))
        out["fan_switch"] = sw
    return out


@scenario_helper("climate.portable_fan")
def portable_fan_helper(ctx, room="bedroom", fan_type="box_fan", on=False, speed="low", aim_deg=0.0, oscillate=False, cell=None, near=None,
                        outlet=True, label=None):
    cfg = ctx.config
    rid = _room(ctx, room)
    props = {"settings": {"speed": speed, "oscillate": bool(oscillate), "aim_deg": float(aim_deg)}, "on": bool(on)}
    if label:
        props["label"] = label
    near_cell = None
    if near and near in cfg["entities"]:
        state = ctx.state()
        _, cells = geometry.anchor(dict(state, robot={"room": rid, "hands": {}, "pos": None}), near)
        near_cell = cells[0] if cells else None
    fid = ctx.placer.floor_item(rid, fan_type, near=near_cell, cell=cell, **props)
    if fid is None:
        raise SpecError("climate.portable_fan: no floor space in " + rid)
    out = {"fan": fid, "fan_room": rid, "outlet": None}
    if outlet:
        pos = cfg["entities"][fid]["pos"]
        cols, rows = geometry.grid_size(cfg["rooms"][rid])
        wall = _outlet_cell(ctx, rid, pos)
        oid = ctx.placer.mount(rid, "generic_item", height=0.3, cell=wall, label="wall outlet", category="outlet", outlet_kind="wall",
                               receptacles=2, portable=False, size_m=[0.07, 0.03, 0.12], mass_kg=0.1)
        cfg["entities"][fid]["outlet"] = oid
        out["outlet"] = oid
    return out


def _outlet_cell(ctx, rid, pos):
    """Wall cell for a device's outlet: the nearest perimeter cell to pos (straight to the closest wall first) within a
    cord's reach that the robot can stand next to (plugging / unplugging), off doorway cells; else the closest wall."""
    cfg = ctx.config
    state = ctx.state()
    room = cfg["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    x, y = int(pos[0]), int(pos[1])
    straight = min([(y, [x, 0]), (rows - 1 - y, [x, rows - 1]), (x, [0, y]), (cols - 1 - x, [cols - 1, y])])[1]
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    reach = geometry.reachable_cells(state, rid, blocked)
    doors = set(geometry.doorway_cells(state, rid))
    perimeter = sorted({(i, 0) for i in range(cols)} | {(i, rows - 1) for i in range(cols)} | {(0, j) for j in range(rows)} |
                       {(cols - 1, j) for j in range(rows)})
    options = []
    for c in perimeter:
        if c in doors or c in blocked or math.hypot(c[0] - x, c[1] - y) * C.CELL > 1.5:
            continue   # within a 1.8 m cord's reach (the cord also climbs to the plug)
        if not ((geometry.near_cells([c], room) - {c}) & reach):
            continue
        options.append((list(c) != straight, math.hypot(c[0] - x, c[1] - y), c[1], c[0]))
    if not options:
        return straight
    best = min(options)
    return [best[3], best[2]]


def _candidate_rooms(ctx, room):
    """Room ids for a room argument that may be a list of room ids / room types (in order, duplicates dropped)."""
    if not isinstance(room, (list, tuple)):
        return [_room(ctx, room)]
    out = []
    for item in room:
        ids = [item] if item in ctx.config["rooms"] else ctx.placer.rooms(item)
        for rid in ids:
            if rid not in out:
                out.append(rid)
    if not out:
        raise SpecError("climate helper: none of the rooms {} exists".format(room))
    return out


@scenario_helper("climate.space_heater")
def space_heater_helper(ctx, room="bedroom", heater_type="ceramic_heater", near=None, cell=None, on=False, outlet_kind="wall", tip_ok=True,
                        aim_deg=None, heat="high", dial=4, clutter=None, approval=False):
    """room may be a list of rooms / room types: with clutter, the first room offering a fully clear spot is used."""
    cfg = ctx.config
    candidates = _candidate_rooms(ctx, room)
    failure = None
    for rid in candidates:
        near_cell = None
        if near and near in cfg["entities"]:
            state = ctx.state()
            _, cells = geometry.anchor(dict(state, robot={"room": rid, "hands": {}, "pos": None}), near)
            near_cell = cells[0] if cells else None
        hid = ctx.placer.floor_item(rid, heater_type, near=near_cell, cell=cell, on=bool(on), _tip_switch_ok=bool(tip_ok),
                                    settings={"heat": heat, "thermostat": dial, "aim_deg": float(aim_deg if aim_deg is not None else 0.0)})
        if hid is None:
            failure = SpecError("climate.space_heater: no floor space in " + rid)
            continue
        if clutter:
            try:
                _heater_spot(ctx, hid, rid, CLUTTER.get(clutter, CLUTTER["clothes"]))
            except SpecError as exc:
                _remove(cfg, [hid])
                failure = exc
                continue
        break
    else:
        raise failure or SpecError("climate.space_heater: no room")
    rec = cfg["entities"][hid]
    if aim_deg is None and not clutter:
        cols, rows = geometry.grid_size(cfg["rooms"][rid])
        x, y = rec["pos"]
        cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
        rec["settings"]["aim_deg"] = round(math.degrees(math.atan2(cx - x, -(cy - y))) % 360.0, 1)
    pos = rec["pos"]
    cols, rows = geometry.grid_size(cfg["rooms"][rid])
    wall = _outlet_cell(ctx, rid, pos)
    oid = ctx.placer.mount(rid, "generic_item", height=0.3, cell=wall, label="wall outlet", category="outlet", outlet_kind="wall", receptacles=2,
                           portable=False, size_m=[0.07, 0.03, 0.12], mass_kg=0.1)
    out = {"heater": hid, "heater_room": rid, "wall_outlet": oid, "strip": None, "clutter": None}
    neighbours = sorted({r for c in cfg.get("connections", {}).values() if rid in c["rooms"] for r in c["rooms"]
                         if r != rid and not cfg["rooms"][r].get("outdoor")},
                        key=lambda r: (cfg["rooms"][r]["type"] in ("bathroom", "half_bath", "coat_closet", "linen_closet"),
                                       cfg["rooms"][r]["type"] != "hallway", -C.room_area(cfg["rooms"][r]), r))
    out["clear_room"] = neighbours[0] if neighbours else rid
    if approval:
        rec["requires_approval"] = True
    if clutter:
        aim = math.radians(C.num(rec["settings"].get("aim_deg")))
        cx, cy = rec["pos"][0] + round(math.sin(aim)), rec["pos"][1] - round(math.cos(aim))
        # the deliberate obstruction: right in front of the grille, on the cell _heater_spot checked cuts no route
        cid = ctx.placer.floor_item(rid, "generic_item", cell=[cx, cy], **dict(CLUTTER.get(clutter, CLUTTER["clothes"])))
        out["clutter"] = cid
        out["clear_cell"] = _drop_cell(ctx, out["clear_room"], rid)
        if out["clear_cell"] is None:
            raise SpecError("climate.space_heater: nowhere to set the clutter down in " + out["clear_room"])
    if outlet_kind in ("strip", "extension"):
        sid = ctx.placer.floor_item(rid, "generic_item", near=pos, label="power strip" if outlet_kind == "strip" else "extension cord",
                                    category="power_strip" if outlet_kind == "strip" else "extension_cord", outlet_kind=outlet_kind,
                                    receptacles=6 if outlet_kind == "strip" else 1, plug_required=True, plugged_in=True, outlet=oid,
                                    size_m=[0.3, 0.06, 0.04], mass_kg=0.4, rated_w=1875.0)
        rec["outlet"] = sid
        out["strip"] = sid
    else:
        rec["outlet"] = oid
    return out


# ---------------------------------------------------------------------------------------------
# water fixtures, humidifiers, dehumidifiers

def _combustible_within_cells(state, hid, rid, cell, radius):
    for oid in sorted(state["entities"]):
        if oid == hid:
            continue
        orec = state["entities"][oid]
        if orec.get("category") in ("floor", "wall", "window", "light", "switch") or not C.is_combustible(orec):
            continue
        if C.room_of(state, oid) != rid or orec.get("relation") in ("part",):
            continue
        r_, cells = geometry.anchor(state, oid)
        if cells and min(geometry.chebyshev(tuple(cell), c) for c in cells) <= radius:
            return True
    return False


CLUTTER = {"clothes": dict(label="pile of clothes", category="clothing", material="cotton", size_m=[0.45, 0.4, 0.2], mass_kg=2.0),
           "box": dict(label="cardboard box of papers", category="box", material="cardboard", size_m=[0.4, 0.3, 0.3], mass_kg=3.0),
           "blanket": dict(label="throw blanket", category="blanket", material="wool", size_m=[0.5, 0.4, 0.1], mass_kg=1.2)}


def _heater_spot(ctx, hid, rid, clutter_props):
    """Move the heater to a cell (and aim) where every combustible is more than 0.95 m away (the 0.9 m / 3 ft
    clearance the model enforces, measured the same way, plus a 5 cm margin) and the cell in front of the grille can
    take the scenario's clutter. Both are kernel placement cells (geometry.free_floor_cell keep_access: free, outside
    doorway clearance, reachable, cutting no route). Cells far from combustibles are tried first, so the spot is robust
    to what other domains furnish."""
    from . import heaters as H
    cfg = ctx.config
    rec = cfg["entities"][hid]
    room = cfg["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    state = ctx.state()
    combustibles = [e for e in sorted(cfg["entities"]) if e != hid and C.is_combustible(cfg["entities"][e])
                    and cfg["entities"][e].get("category") not in ("floor", "wall", "window", "light", "switch")
                    and C.room_of(state, e) == rid]
    orig_pos, orig_aim = list(rec.get("pos") or [0, 0]), rec["settings"].get("aim_deg")
    probe = dict(clutter_props, type="generic_item", portable=True, location=rid)
    blocked = set(geometry.blocked_cells(state, rid, ignore=(hid,), include_agents=True))
    options = []
    for y in range(rows):
        for x in range(cols):
            if (x, y) in blocked:
                continue
            rec["pos"] = [x, y]
            near = H.combustibles_near(state, hid, rid, combustibles)
            clearance = min([d for _, d, _ in near] + [9.0])
            if clearance > 0.95:
                options.append((-min(clearance, 1.5), y, x))
    for _, y, x in sorted(options):
        rec.pop("pos", None)
        if geometry.free_floor_cell(state, rid, near=(x, y), ignore=(hid,), record=rec, keep_access=True) != [x, y]:
            continue
        rec["pos"] = [x, y]
        for aim in (0.0, 90.0, 180.0, 270.0):
            fx, fy = x + round(math.sin(math.radians(aim))), y - round(math.cos(math.radians(aim)))
            if not geometry.in_grid(room, (fx, fy)):
                continue
            if geometry.free_floor_cell(state, rid, near=(fx, fy), record=probe, strict=True, keep_access=True) == [fx, fy]:
                rec["settings"]["aim_deg"] = aim
                return True
    rec["pos"], rec["settings"]["aim_deg"] = orig_pos, orig_aim
    raise SpecError("climate.space_heater: no clear spot for the heater in " + rid)


def _drop_cell(ctx, room_id, from_room):
    """A free floor cell near where the robot arrives in room_id from from_room, for setting something down (kernel
    placement rule: outside doorway clearance, reachable, cutting no route); None when the room has none."""
    cfg = ctx.config
    state = ctx.state()
    conn = next((c for c in sorted(cfg.get("connections", {}).items()) if set(c[1]["rooms"]) == {room_id, from_room}), None)
    door = tuple(conn[1]["cells"][room_id]) if conn else None
    return geometry.free_floor_cell(state, room_id, near=door, keep_access=True)


def _floor_item_near(ctx, rid, type_name, **props):
    """Placer.floor_item in rid, else in the rooms connected to it (kernel placement: reachable, outside doorway
    clearance, cutting no route)."""
    cfg = ctx.config
    rooms = [rid] + sorted(set(r for c in cfg.get("connections", {}).values() if rid in c["rooms"] for r in c["rooms"]
                               if r != rid and not cfg["rooms"][r].get("outdoor")))
    for r in rooms:
        eid = ctx.placer.floor_item(r, type_name, **props)
        if eid:
            return eid
    raise SpecError("climate helper: no reachable floor space for {} near {}".format(type_name, rid))


def _reachable_sink(ctx, rid):
    """A water source in rid the robot can stand next to (a sink at the far end of a vanity between a tub and a wall
    is useless for filling a tank)."""
    cfg = ctx.config
    state = ctx.state()
    for eid in sorted(cfg["entities"]):
        rec = cfg["entities"][eid]
        if not rec.get("water_source") or C.room_of(state, eid) != rid:
            continue
        _, cells = geometry.anchor(dict(state, robot={"room": rid, "hands": {}, "pos": None}), eid)
        if not cells or furnish.can_reach(state, rid, cells, geometry.access_radius(rec)):
            return eid
    return None


@scenario_helper("climate.sink")
def sink_helper(ctx, room="bathroom"):
    cfg = ctx.config
    if room not in cfg["rooms"]:
        for cand in ctx.placer.rooms(room):
            sid = _reachable_sink(ctx, cand)
            if sid:
                return {"sink": sid, "sink_room": cand}
    rid = _room(ctx, room)
    sid = _reachable_sink(ctx, rid)
    if sid:
        return {"sink": sid, "sink_room": rid}
    sid = ctx.placer.furniture(rid, "generic_item", wall=True, label="sink", category="sink", water_source=True, drain=True, portable=False,
                               accepts_items=True, container=True, capacity_l=12.0, inner_size_m=[0.45, 0.35, 0.18], size_m=[0.6, 0.5, 0.85],
                               surface_height_m=0.85, blocks_path=True, flow_lpm=6.0)
    if sid is None:
        sid = ctx.placer.mount(rid, "generic_item", height=0.85, label="sink", category="sink", water_source=True, drain=True, portable=False,
                               accepts_items=True, container=True, capacity_l=12.0, inner_size_m=[0.45, 0.35, 0.18], size_m=[0.6, 0.5, 0.2], flow_lpm=6.0)
    return {"sink": sid, "sink_room": rid}


@scenario_helper("climate.humidifier")
def humidifier_helper(ctx, room="bedroom", htype="ultrasonic_humidifier", surface=None, on=False, target_rh=45, output="high", tank_l=0.0,
                      water="water", water_age_h=0.0, bacteria=0.0, seated=True, rule=None, jug=False, jug_room=None, sink_room="bathroom",
                      floor=False, child=False, mat=False):
    cfg = ctx.config
    rid = _room(ctx, room)
    p = ctx.placer
    surf = surface if surface in cfg["entities"] else (furnish._surface_in(p, rid, (surface,)) if surface else
                                                       furnish._surface_in(p, rid, ("dresser", "nightstand", "desk", "table", "side_table")))
    props = {"on": bool(on), "settings": {"output": output, "target_rh": target_rh}}
    if rule == "tap_ok":
        props["water_rule"] = "tap_ok"
        props["label_text"] = "Ultrasonic humidifier with demineralization cartridge. Tap water OK. Empty and refill daily."
    if mat and surf and not floor:
        tray = p.on(surf, "tray", label="waterproof humidifier mat", size_m=[0.4, 0.35, 0.02], material="silicone", water_resistant=True)
        surf = tray or surf
    hid = None if floor or not surf else p.on(surf, htype, **props)
    if hid is None:
        hid = p.floor_item(rid, htype, **props)
    if hid is None:
        raise SpecError("climate.humidifier: no space in " + rid)
    tank = p.make("humidifier_tank", hid if seated else rid, liquid_l=float(tank_l), liquid=water, water_age_h=float(water_age_h),
                  contamination={"bacteria": float(bacteria)} if bacteria else {})
    trec = cfg["entities"][tank]
    if seated:
        trec["relation"] = "part"
        trec["slot"] = "tank"
        cfg["entities"][hid]["slots"]["tank"]["item"] = tank
        cfg["entities"][hid]["base_water_l"] = min(0.3, float(tank_l))
    else:
        trec["relation"] = "on"
        trec["pos"] = _floor_cell(ctx, rid, tank)
    out = {"humidifier": hid, "tank": tank, "humid_room": rid, "jug": None, "surface": cfg["entities"][hid].get("location")}
    out.update(sink_helper(ctx, sink_room))
    if jug:
        jrid = _room(ctx, jug_room) if jug_room else rid
        jsurf = furnish._surface_in(p, jrid, ("counter", "shelf_unit", "dresser", "table", "desk", "nightstand"))
        jprops = dict(label="distilled water jug", category="jug", liquid_container=True, liquid="distilled_water", liquid_l=3.8, capacity_l=3.8,
                      size_m=[0.15, 0.15, 0.28], mass_kg=0.15, label_text="Distilled water - 1 gallon (3.78 L)", openable=True, open=False)
        jid = p.on(jsurf, "generic_item", **jprops) if jsurf else None
        if jid is None:
            jid = p.floor_item(jrid, "generic_item", **jprops)
        out["jug"] = jid
    return out


@scenario_helper("climate.dehumidifier")
def dehumidifier_helper(ctx, room="basement", on=False, bucket_l=0.0, drain="bucket", hose=False, floor_drain=True, target_rh=50, rh=None,
                        seated=True):
    cfg = ctx.config
    rid = _room(ctx, room)
    p = ctx.placer
    _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("climate_kind") == "dehumidifier" and r.get("location") == rid])
    did = p.floor_item(rid, "dehumidifier", on=bool(on), settings={"target_rh": target_rh, "fan": "high", "drain": drain})
    if did is None:
        raise SpecError("climate.dehumidifier: no floor space")
    b = p.make("dehumidifier_bucket", did if seated else rid, liquid_l=float(bucket_l))
    if seated:
        cfg["entities"][b]["relation"] = "part"
        cfg["entities"][b]["slot"] = "bucket"
        cfg["entities"][did]["slots"]["bucket"]["item"] = b
    else:
        cfg["entities"][b]["pos"] = _floor_cell(ctx, rid, b)
    out = {"dehumidifier": did, "bucket": b, "dehu_room": rid, "hose": None, "floor_drain": None}
    if floor_drain:
        fd = p.floor_item(rid, "generic_item", near=cfg["entities"][did]["pos"], label="floor drain", category="floor_drain", drain=True,
                          portable=False, accepts_items=True, container=True, capacity_l=2.0, inner_size_m=[0.1, 0.1, 0.1],
                          size_m=[0.15, 0.15, 0.01], ground_layer=True)
        out["floor_drain"] = fd
    if hose:
        hid = p.floor_item(rid, "drain_hose", near=cfg["entities"][did]["pos"], tethered_to=did)
        cfg["entities"][did]["hose"] = hid
        out["hose"] = hid
    if rh is not None:
        cfg["rooms"][rid]["humidity"] = float(rh)
    return out


def _floor_cell(ctx, rid, eid):
    """Kernel placement cell (keep_access) for an existing loose record standing on the room's floor."""
    rec = ctx.config["entities"][eid]
    rec.pop("pos", None)
    for keep in (True, "plain"):
        cell = geometry.free_floor_cell(ctx.state(), rid, ignore=(eid,), record=rec, keep_access=keep)
        if cell is not None:
            return cell
    raise SpecError("climate helper: no floor space for {} in {}".format(eid, rid))


# ---------------------------------------------------------------------------------------------
# filters and purifiers

def _trash(ctx, rid, near=None, fits=None):
    """A trash receptacle in rid that the old filter(s) fit into (``fits``: list of size_m) and the robot can stand
    next to; else a 40 L trash can placed on a free reachable cell."""
    p = ctx.placer
    state = ctx.state()
    view = dict(state, robot={"room": rid, "hands": {}, "pos": None})
    reach = None
    for eid in sorted(ctx.config["entities"]):
        rec = ctx.config["entities"][eid]
        if not (rec.get("waste_stream") == "trash" and rec.get("accepts_items")) or C.room_of(state, eid) != rid:
            continue
        if fits and not all(geometry.fits_inside(s, rec.get("inner_size_m")) for s in fits):
            continue   # a bathroom wastebasket does not take a 16x25 in filter
        _, cells = geometry.anchor(view, eid)
        if reach is None:
            reach = geometry.reachable_cells(state, rid)
        if not cells or not furnish.can_reach(state, rid, cells, reach=reach):
            continue
        return eid
    # a 40 L kitchen-style can: a loose (movable) floor item, like cleaning's kitchen_trash_can
    return p.floor_item(rid, "generic_item", near=near, label="trash can", category="trash_can", waste_stream="trash", accepts_items=True,
                        container=True, capacity_l=40.0, inner_size_m=[0.7, 0.7, 0.5], size_m=[0.4, 0.4, 0.6], mass_kg=2.0, portable=True)


@scenario_helper("climate.hvac_filter_job")
def hvac_filter_job(ctx, system="furnace_ac", nominal="16x25x1", old_dirt=0.85, merv=8, wrong="20x20x1", spare_reversed=False,
                    trash_room=None, mode=None, units=None):
    out = hvac_helper(ctx, system=system, mode=mode, units=units, filter_dirt=old_dirt, merv=merv, nominal=nominal, spare=False)
    cfg = ctx.config
    sid = out["hvac"]
    rid = C.room_of(ctx.state(), sid)
    p = ctx.placer
    rooms_near = [rid] + sorted(set(r for c in cfg.get("connections", {}).values() if rid in c["rooms"] for r in c["rooms"]
                                    if r != rid and not cfg["rooms"][r].get("outdoor")))

    def drop(type_name, **props):
        for r in rooms_near:
            eid = p.floor_item(r, type_name, **props)
            if eid:
                return eid
        raise SpecError("climate.hvac_filter_job: no floor space for " + type_name)
    new = drop("hvac_filter", **furnish._filter_props(nominal, merv))
    if spare_reversed:
        cfg["entities"][new]["filter_spec"]["arrow"] = "away_from_blower"
    out["new_filter"] = new
    out["old_filter"] = out["filter"]
    out["wrong_filter"] = None
    if wrong:
        out["wrong_filter"] = drop("hvac_filter", **furnish._filter_props(wrong, merv))
    if trash_room:
        out["trash"] = _trash(ctx, _room(ctx, trash_room), fits=[cfg["entities"][new]["size_m"]])
    else:
        out["trash"] = None
        for r in rooms_near:
            t = _trash(ctx, r, fits=[cfg["entities"][new]["size_m"]])
            if t:
                out["trash"] = t
                break
    out["equip_room"] = rid
    return out


@scenario_helper("climate.purifier")
def purifier_helper(ctx, room="master_bedroom", model="combo", dirt=0.9, indicator="replace", on=True, mode="auto", spares=True,
                    wrong_model=True, hours=None):
    cfg = ctx.config
    rid = _room(ctx, room)
    p = ctx.placer
    _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("climate_kind") == "air_purifier" and r.get("location") == rid])
    tname = "air_purifier" if model == "combo" else "tower_air_purifier"
    pid = p.floor_item(rid, tname, on=bool(on), settings={"mode": mode, "filter_indicator": indicator})
    if pid is None:
        raise SpecError("climate.purifier: no floor space")
    rec = cfg["entities"][pid]
    rec["filter_hours"] = float(hours if hours is not None else (4400.0 if indicator == "replace" else 1200.0))
    old = {}
    layers = ["combo"] if model == "combo" else ["prefilter", "carbon", "hepa"]
    types = {"combo": "purifier_filter", "prefilter": "purifier_prefilter", "carbon": "purifier_carbon_filter", "hepa": "purifier_hepa_filter"}
    slots = ["cartridge"] if model == "combo" else ["layer_1", "layer_2", "layer_3"]
    for slot, layer in zip(slots, layers):
        f = p.make(types[layer], pid, shrink_wrapped=False, openable=False, dirtiness=float(dirt) * (1.0 if layer != "carbon" else 0.6))
        cfg["entities"][f]["relation"] = "part"
        cfg["entities"][f]["slot"] = slot
        rec["slots"][slot]["item"] = f
        old[layer] = f
    out = {"purifier": pid, "purifier_room": rid, "old_filters": [old[k] for k in layers], "trash": _trash(ctx, rid, near=rec.get("pos"),
                                                                                         fits=[cfg["entities"][f]["size_m"] for f in old.values()])}
    for layer in layers:
        out["old_" + layer] = old[layer]
    if spares:
        box_surface = furnish._surface_in(p, rid, ("dresser", "nightstand", "desk", "table"))
        new = []
        for layer in layers:
            if layer == "prefilter":
                continue
            f = p.on(box_surface, types[layer]) if box_surface else None
            if f is None:
                f = _floor_item_near(ctx, rid, types[layer])
            new.append(f)
            out["new_" + layer] = f
        out["new_filters"] = new
        if wrong_model:
            # the other model's filter looks like this unit's own (realistic perception: the purifier_cartridge or, for a
            # panel, the purifier_panel look-alike group) until its label is read
            extra = {} if model == "combo" else {"looks_like": "purifier_panel"}
            # a panel is built on the HEPA panel's type (its weight, feel and bulk), so only its print and its slightly
            # smaller frame differ from this unit's panels: nothing but reading (or measuring) tells it apart
            base = "purifier_filter" if model == "combo" else "purifier_hepa_filter"
            wf = _floor_item_near(ctx, rid, base, label="AP-200 replacement filter (other model)", filter_model="AP-200",
                              size_m=[0.18, 0.18, 0.22] if model == "combo" else [0.33, 0.21, 0.04],
                              filter_layer="combo" if model == "combo" else "hepa",
                              label_text="AP-200 3-in-1 filter - fits AP-200 purifiers only." if model == "combo" else
                              "AP-200 HEPA panel - fits AP-200 purifiers only.", **extra)
            out["wrong_filter"] = wf
    return out


# ---------------------------------------------------------------------------------------------
# vents and windows

@scenario_helper("climate.vents")
def vents_helper(ctx, closed=0, closed_rooms=None, preference_closed=None, under_rug=False, behind_sofa=False, dusty=0, room=None):
    """Adjust the existing registers (run climate.hvac with a ducted system first)."""
    cfg = ctx.config
    state = ctx.state()
    regs = [e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("climate_kind") == "register"]
    if not regs:
        raise SpecError("climate.vents: no registers (use climate.hvac with a ducted system first)")
    out = {"registers": regs, "returns": [e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("climate_kind") == "return_grille"],
           "closed": [], "hidden": [], "pref_closed": [], "dusty": []}
    rng = ctx.rng
    only = _room(ctx, room) if room is not None else None
    pool = [r for r in regs if only is None or C.room_of(state, r) == only]
    if closed_rooms:
        targets = [r for r in regs if cfg["rooms"][C.room_of(state, r)]["type"] in closed_rooms or C.room_of(state, r) in closed_rooms]
    else:
        targets = rng.sample(pool, min(int(closed), len(pool)))
    for r in targets:
        cfg["entities"][r]["settings"]["damper"] = 0.0
        out["closed"].append(r)
    for key in preference_closed or []:
        rid = _room(ctx, key)
        for r in regs:
            if C.room_of(state, r) == rid:
                cfg["entities"][r]["settings"]["damper"] = 0.0
                out["pref_closed"].append(r)
    if under_rug:
        floor_regs = [r for r in regs if cfg["entities"][r].get("register_kind") == "floor" and cfg["entities"][r].get("location") in cfg["rooms"]
                      and r not in out["closed"]]
        if floor_regs:
            # a register whose room also has somewhere to put the lifted rug aside (a packed 2 x 3 m laundry room
            # has none: there the careless-robot wrong plan could not even set the rug down)
            order = rng.sample(floor_regs, len(floor_regs))
            for k, r in enumerate(order):
                rid = cfg["entities"][r]["location"]
                rug = ctx.placer.make("rug", rid, size_m=[1.5, 1.0, 0.01], label="area rug")
                cfg["entities"][rug]["pos"] = _rug_anchor(ctx, rid, rug, tuple(cfg["entities"][r]["pos"]))
                aside = _rug_aside_cell(ctx, rid, rug)
                if aside is None and k < len(order) - 1:
                    del cfg["entities"][rug]
                    continue
                rec = cfg["entities"][r]
                rec["floor_cell"] = list(rec["pos"])
                rec.pop("pos", None)
                rec["location"], rec["relation"] = rug, "under"
                out["hidden"].append(r)
                out["rug"] = rug
                out["rug_aside_cell"] = aside
                break
    if behind_sofa:
        sofas = [e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("type") in ("sofa", "armchair", "dresser")]
        cands = [r for r in regs if cfg["entities"][r].get("location") in cfg["rooms"] and r not in out["closed"] and r not in out["hidden"]
                 and any(C.room_of(state, s) == cfg["entities"][r]["location"] for s in sofas)]
        if cands:
            r = rng.choice(cands)
            rid = cfg["entities"][r]["location"]
            sofa = next(s for s in sofas if C.room_of(state, s) == rid)
            rec = cfg["entities"][r]
            rec["floor_cell"] = list(rec["pos"])
            rec.pop("pos", None)
            rec["location"], rec["relation"] = sofa, "behind"
            out["hidden"].append(r)
            out["sofa"] = sofa
    for r in rng.sample(regs, min(int(dusty), len(regs))):
        cfg["entities"][r]["soil"] = {"dust": 0.5}
        out["dusty"].append(r)
    out["all_vents"] = out["registers"] + out["returns"]
    out["visible_vents"] = [v for v in out["all_vents"] if v not in out["hidden"]]
    for key in ("closed", "hidden", "pref_closed", "dusty"):
        for i, eid in enumerate(out[key], 1):
            out["{}_{}".format(key, i)] = eid
    out.setdefault("rug", None)
    out.setdefault("rug_aside_cell", None)
    out.setdefault("sofa", None)
    return out


def _vent_cells(cfg, rid):
    cells = set()
    for e, r in cfg["entities"].items():
        if r.get("climate_kind") in ("register", "return_grille"):
            if r.get("location") == rid and r.get("pos"):
                cells.update(geometry.footprint(r))
            elif r.get("floor_cell") and C.room_of(cfg, e) == rid:
                cells.add(tuple(r["floor_cell"]))
    return cells


def _rug_anchor(ctx, rid, rug, reg_cell):
    """Anchor for a rug covering reg_cell: the register cell itself when the rug's whole footprint fits the room
    clear of path-blocking furniture (the original layout), else the in-room position covering the register with
    the fewest furniture cells and other vents underneath."""
    cfg = ctx.config
    room = cfg["rooms"][rid]
    rec = cfg["entities"][rug]
    fw, fd = geometry.footprint_size(rec)
    blocked = set(geometry.blocked_cells(ctx.state(), rid, include_agents=False))
    vents = _vent_cells(cfg, rid) - {reg_cell}
    options = []
    for x0 in range(reg_cell[0] - fw + 1, reg_cell[0] + 1):
        for y0 in range(reg_cell[1] - fd + 1, reg_cell[1] + 1):
            cells = [(x0 + i, y0 + j) for i in range(fw) for j in range(fd)]
            if not all(geometry.in_grid(room, c) for c in cells):
                continue
            options.append((sum(c in blocked for c in cells), sum(c in vents for c in cells), (x0, y0) != reg_cell,
                            abs(x0 - reg_cell[0]) + abs(y0 - reg_cell[1]), y0, x0))
    if not options:
        return list(reg_cell)
    best = min(options)
    return [best[-1], best[-2]]


def _rug_aside_cell(ctx, rid, rug):
    """Where a careless robot would leave the rug after lifting it: the nearest spot of the same room where the
    whole rug fits off its original footprint (so it really ends up moved), clear of furniture, loose items,
    vents and door sweeps, with a hazard-free standing cell beside its corner (walk_to adjacent + place cell)."""
    cfg = ctx.config
    state = ctx.state()
    room = cfg["rooms"][rid]
    rec = cfg["entities"][rug]
    fw, fd = geometry.footprint_size(rec)
    orig = set(geometry.footprint(rec))
    blocked = set(geometry.blocked_cells(state, rid, ignore=(rug,), include_agents=True))
    hazards = set(geometry.hazard_cells(state, rid))
    loose = set()
    for e in geometry.room_entities(state, rid):
        r = cfg["entities"][e]
        if e != rug and not r.get("ground_layer") and "connects" not in r and not r.get("mounted"):
            loose.update(geometry.footprint(r))
    vents = _vent_cells(cfg, rid)
    sweep = geometry.door_clearance(state, rid)
    # hazard-free floor joined to the doorways (where the robot can stand to set the rug down)
    walk_blocked = set(geometry.blocked_cells(state, rid, ignore=(rug,), include_agents=True)) | hazards
    reach = geometry.reachable_cells(state, rid, walk_blocked)
    cols, rows = geometry.grid_size(room)
    ox, oy = rec["pos"]
    options = []
    for y in range(rows - fd + 1):
        for x in range(cols - fw + 1):
            cells = {(x + i, y + j) for i in range(fw) for j in range(fd)}
            if (x, y) == (ox, oy) or cells & blocked or cells & loose:
                continue
            if not ((geometry.near_cells([(x, y)], room) - {(x, y)}) & reach):
                continue
            # preference: clear of the old spot, then of door sweeps (a flat rug does not stop a door, but it is
            # untidy there), then of other vents; a narrow hallway may only offer a shifted runner
            options.append((bool(cells & vents), bool(cells & orig), bool(cells & sweep), max(abs(x - ox), abs(y - oy)), y, x))
    if not options:
        return None
    best = min(options)
    return [best[-1], best[-2]]


WINDOW_REACH = 2   # windows are mounted at 0.9 m: reachable from one extra cell away (kernel access_radius)


def _window_cell(ctx, rid, facing, preferred, taken):
    """A cell on the wall facing ``facing`` for a new window: reachable (a free neighbour the robot can get to), clear of
    doorways and other windows, nearest the preferred cell."""
    cfg = ctx.config
    cols, rows = geometry.grid_size(cfg["rooms"][rid])
    if facing == 0.0:
        cells = [(x, 0) for x in range(cols)]
    elif facing == 90.0:
        cells = [(cols - 1, y) for y in range(rows)]
    elif facing == 180.0:
        cells = [(x, rows - 1) for x in range(cols)]
    else:
        cells = [(0, y) for y in range(rows)]
    state = ctx.state()
    doors = geometry.door_clearance(state, rid)
    reach = geometry.reachable_cells(state, rid)
    near_taken = {(t[0] + dx, t[1] + dy) for t in taken for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    order = sorted(cells, key=lambda c: (abs(c[0] - preferred[0]) + abs(c[1] - preferred[1]), c[1], c[0]))
    for c in order:
        if c in doors or c in near_taken:
            continue
        if furnish.can_reach(state, rid, [c], WINDOW_REACH, reach):
            return [c[0], c[1]]
    return list(preferred)


@scenario_helper("climate.windows")
def windows_helper(ctx, room="living_room", count=None, facing=None, open=None, screen=None, child_stop=None, locked=None, create=True):
    cfg = ctx.config
    rid = _room(ctx, room)
    state = ctx.state()
    reach = geometry.reachable_cells(state, rid)
    wins = [e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("category") == "window" and cfg["entities"][e].get("location") == rid
            and cfg["entities"][e].get("pos") and furnish.can_reach(state, rid, [cfg["entities"][e]["pos"]],
                                                                    geometry.access_radius(cfg["entities"][e]), reach)]
    need = int(count) if count else 0
    cols, rows = geometry.grid_size(cfg["rooms"][rid])
    walls = {0.0: lambda i: [min(cols - 1, 1 + 2 * i), 0], 90.0: lambda i: [cols - 1, min(rows - 1, 1 + 2 * i)],
             180.0: lambda i: [min(cols - 1, 1 + 2 * i), rows - 1], 270.0: lambda i: [0, min(rows - 1, 1 + 2 * i)]}
    facings = list(facing) if isinstance(facing, list) else ([facing] * need if facing is not None else [])
    if facings and create:
        # facing given: the room's glazing is rebuilt (every window, reachable or not, with its coverings and sill)
        old = [e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("category") == "window" and cfg["entities"][e].get("location") == rid]
        _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("window") in old or r.get("covers_window") in old] + old)
        wins = []
        counts = {}
        taken = set()
        for f in facings:
            f = float(f) % 360.0
            i = counts.get(f, 0)
            counts[f] = i + 1
            cell = _window_cell(ctx, rid, f, walls[f](i), taken)
            taken.add(tuple(cell))
            wid = ctx.placer.mount(rid, "window", height=0.9, cell=cell, facing_deg=f, label="{} window".format(cfg["rooms"][rid]["label"].lower()),
                                   window_kind=ctx.rng.choice(["sash", "casement", "sliding"]))
            wins.append(wid)
    while create and len(wins) < need:
        cell = furnish._free_wall_cell(ctx.placer, rid, ctx.rng)
        cols_, rows_ = geometry.grid_size(cfg["rooms"][rid])
        f = 0.0 if cell[1] == 0 else (180.0 if cell[1] == rows_ - 1 else (270.0 if cell[0] == 0 else 90.0))
        wid = ctx.placer.mount(rid, "window", height=0.9, cell=cell, facing_deg=f, label="{} window".format(cfg["rooms"][rid]["label"].lower()))
        wins.append(wid)
    for wid in wins:
        rec = cfg["entities"][wid]
        if open is not None:
            rec["open"] = bool(open)
            if open:
                rec["locked"] = False
        if screen is not None:
            rec["has_screen"] = bool(screen)
        if child_stop is not None:
            rec["child_stop"] = bool(child_stop)
        if locked is not None:
            rec["locked"] = bool(locked)
    out = {"windows": wins, "window_room": rid}
    for i, wid in enumerate(wins, 1):
        out["window_{}".format(i)] = wid
    return out


@scenario_helper("climate.shades")
def shades_helper(ctx, room="living_room", lowered=0.0, opacity=0.9, windows=None):
    cfg = ctx.config
    rid = _room(ctx, room)
    wins = windows or [e for e in sorted(cfg["entities"]) if cfg["entities"][e].get("category") == "window" and cfg["entities"][e].get("location") == rid]
    _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("window") in wins and r.get("category") in ("curtain", "blind", "shutter")])
    shades = []
    for wid in wins:
        wrec = cfg["entities"][wid]
        sid = ctx.placer.mount(rid, "generic_item", height=1.3, cell=list(wrec["pos"]), label=(wrec.get("label") or "window") + " shade",
                               category="shade", tags=["window_covering"], covers_window=wid, openable=True, open=float(lowered) < 0.5,
                               settings={"lowered": float(lowered)}, setting_options={"lowered": {"min": 0.0, "max": 1.0}}, opacity=float(opacity),
                               size_m=[0.9, 0.05, 0.1], mass_kg=1.2, portable=False, material="fabric")
        wrec["covered"] = round(float(lowered) * float(opacity), 3)
        shades.append(sid)
    out = {"shades": shades}
    for wid, sid in zip(wins, shades):
        out["shade_of_" + wid] = sid
    return out


def _first_room(ctx, room, build, *args):
    """Run build(ctx, rid, *args) for each candidate room of ``room`` (a room id / type or a list of them) until one
    works; a failed attempt's changes to the entities and that room are rolled back."""
    cfg = ctx.config
    failure = None
    for rid in _candidate_rooms(ctx, room):
        saved_ents, saved_room = copy.deepcopy(cfg["entities"]), copy.deepcopy(cfg["rooms"][rid])
        try:
            return build(ctx, rid, *args)
        except SpecError as exc:
            cfg["entities"].clear()
            cfg["entities"].update(saved_ents)
            cfg["rooms"][rid] = saved_room
            failure = exc
    raise failure or SpecError("climate helper: no room among {}".format(room))


@scenario_helper("climate.glare_scene")
def glare_scene(ctx, room="living_room", lights_off=True, hold_s=1800.0):
    """Exactly one sunlit window's beam hits a TV screen now; another window gets no direct sun. ``room`` may be a
    list of rooms / room types (the first that can host the scene is used)."""
    return _first_room(ctx, room, _glare_scene_room, lights_off, hold_s)


def _glare_scene_room(ctx, rid, lights_off, hold_s):
    cfg = ctx.config
    az, alt = _sun_now(ctx)
    if alt < 8.0:
        raise SpecError("climate.glare_scene: the sun is too low at the start time")
    sun_wall = min((0.0, 90.0, 180.0, 270.0), key=lambda f: abs(((az - f) + 180.0) % 360.0 - 180.0))
    dark_wall = (sun_wall + 180.0) % 360.0
    out = windows_helper(ctx, room=rid, facing=[sun_wall, dark_wall], create=True)
    sunny, other = out["windows"]
    _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("category") in ("tv",) and C.room_of(ctx.state(), e) == rid])
    view = dict(ctx.state(), clock=cfg.get("clock") or {}, time_s=0.0, weather=cfg.get("weather") or {})
    times = [0.0, hold_s / 3.0, 2.0 * hold_s / 3.0, hold_s]
    suns = [S.sun_position(dict(view, time_s=t)) for t in times]
    # the sunny window where windows_helper put it first, then along the same wall (reachable, off doorway clearance):
    # the first position whose beam reaches a TV spot the kernel placement rule accepts
    tv = None
    wrec = cfg["entities"][sunny]
    state = ctx.state()
    clear = geometry.door_clearance(state, rid)
    reach = geometry.reachable_cells(state, rid)
    first = tuple(wrec["pos"])
    wall = [c for c in _wall_cells(cfg["rooms"][rid], sun_wall) if c not in clear and furnish.can_reach(state, rid, [c], WINDOW_REACH, reach)]
    for cell in [first] + sorted((c for c in wall if c != first), key=lambda c: (abs(c[0] - first[0]) + abs(c[1] - first[1]), c[1], c[0])):
        wrec["pos"] = [cell[0], cell[1]]
        tv = _tv_in_beam(ctx, rid, sunny, suns)
        if tv is not None:
            break
    if tv is None:
        wrec["pos"] = list(first)
        raise SpecError("climate.glare_scene: could not place the TV in the beam")
    shades = shades_helper(ctx, room=rid, opacity=0.95)
    if lights_off:
        for eid, rec in cfg["entities"].items():
            if rec.get("category") in ("light", "switch") and C.room_of(ctx.state(), eid) == rid and not rec.get("battery_powered"):
                rec["on"] = False
    out.update({"tv": tv, "glare_window": sunny, "dark_window": other, "glare_shade": shades["shade_of_" + sunny],
                "other_shade": shades["shade_of_" + other], "shades": shades["shades"], "glare_room": rid})
    return out


def _wall_cells(room, facing):
    cols, rows = geometry.grid_size(room)
    if facing == 0.0:
        return [(x, 0) for x in range(cols)]
    if facing == 90.0:
        return [(cols - 1, y) for y in range(rows)]
    if facing == 180.0:
        return [(x, rows - 1) for x in range(cols)]
    return [(0, y) for y in range(rows)]


TV_PROPS = dict(label="television", category="tv", size_m=[0.9, 0.2, 0.55], mass_kg=12.0, portable=False, blocks_path=True, z_m=0.6,
                screen=True, power_w=90.0, standby_w=0.5, on=True)


def _tv_in_beam(ctx, rid, window, suns):
    """A TV on its low stand (path-blocking, 0.9 m screen centred 0.88 m up) whose screen centre stays in ``window``'s
    direct beam at every sun position in ``suns``: footprints (rot 0 / 90) clear of furniture and doorway clearance,
    nearest the walls first, placed only where the kernel placement rule accepts it (keep_access: cuts no route,
    keeps everything accessible). Returns the TV id or None."""
    cfg = ctx.config
    p = ctx.placer
    room_rec = cfg["rooms"][rid]
    cols, rows = geometry.grid_size(room_rec)
    state = ctx.state()
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    clear = geometry.door_clearance(state, rid)
    probe = p.reg.make("generic_item", rid, **TV_PROPS)
    cfg["entities"]["_tv_probe"] = probe
    candidates = []
    try:
        pview = dict(ctx.state(), clock=cfg.get("clock") or {}, time_s=0.0, weather=cfg.get("weather") or {})
        apertures = {}
        for rot in (0, 90):
            for yy in range(rows):
                for xx in range(cols):
                    probe["pos"], probe["rot"] = [xx, yy], rot
                    cells = geometry.footprint(probe)
                    if any(not geometry.in_grid(room_rec, c) or c in blocked or c in clear for c in cells):
                        continue
                    _, x, y, z = S.glare_point(pview, "_tv_probe")
                    if all(S.beam_hits(pview, window, x, y, z, a, h, apertures) for a, h in suns):
                        candidates.append((-abs(x - room_rec["size_m"][0] / 2.0) - abs(y - room_rec["size_m"][1] / 2.0), rot, yy, xx))
    finally:
        del cfg["entities"]["_tv_probe"]
    for _, rot, yy, xx in sorted(candidates):
        rec = dict(p.reg.make("generic_item", rid, **TV_PROPS), rot=rot)
        if geometry.free_floor_cell(ctx.state(), rid, near=(xx, yy), record=rec, keep_access=True) == [xx, yy]:
            return p.floor_item(rid, "generic_item", cell=[xx, yy], rot=rot, **TV_PROPS)
    return None


def window_shade_min(view, rid, cells, t0, t1, step=300.0, z=0.45):
    """{cell: minimum shade fraction over [t0, t1]} for 0.5 m cells of an outdoor room (3x3 points per cell)."""
    out = {c: 1.0 for c in cells}
    t = t0
    while t <= t1 + 1e-6:
        v = dict(view, time_s=t)
        az, alt = S.sun_position(v)
        casters = S.shade_casters(v, rid)
        for c in cells:
            if out[c] <= 0.0:
                continue
            pts = [(c[0] * C.CELL + fx * C.CELL, c[1] * C.CELL + fy * C.CELL) for fx in (0.15, 0.5, 0.85) for fy in (0.15, 0.5, 0.85)]
            frac = sum(S.shaded_at(v, rid, x, y, z, az, alt, casters) for x, y in pts) / len(pts)
            out[c] = min(out[c], frac)
        t += step
    return out


@scenario_helper("climate.patio_shade")
def patio_shade(ctx, room="patio", chairs=2, tree=True, flower_bed=True, sit_from_s=0.0, sit_minutes=60, umbrella=False):
    """Chairs start in cells that will be sunny during the sitting window; at least chairs+1 cells stay shaded through it.
    ``room`` may be a list of rooms / room types (e.g. ["patio", "deck"]: the generator can turn a required patio
    into a deck): the first one where such a shade layout exists is used."""
    return _first_room(ctx, room, _patio_shade_room, chairs, tree, flower_bed, sit_from_s, sit_minutes)


def _patio_shade_room(ctx, rid, chairs, tree, flower_bed, sit_from_s, sit_minutes):
    cfg = ctx.config
    room_rec = cfg["rooms"][rid]
    p = ctx.placer
    cols, rows = geometry.grid_size(room_rec)
    az, alt = _sun_now(ctx)
    if not room_rec.get("shade_walls"):
        conn = next((c for c in cfg.get("connections", {}).values() if rid in c["rooms"]), None)
        cell = (conn or {}).get("cells", {}).get(rid, [0, 0])
        edge = "N" if cell[1] == 0 else ("S" if cell[1] == rows - 1 else ("W" if cell[0] == 0 else "E"))
        room_rec["shade_walls"] = [{"edge": edge, "height_m": 3.4, "from_m": 0.0, "to_m": room_rec["size_m"][0] if edge in ("N", "S") else room_rec["size_m"][1]}]
    _remove(cfg, [e for e, r in cfg["entities"].items() if r.get("location") == rid and r.get("type") in ("outdoor_chair", "outdoor_table")])
    start, until = float(sit_from_s), float(sit_from_s) + float(sit_minutes) * 60.0
    sx, sy, rise = S.to_sun(az, alt)
    configs = []
    if tree:
        for dist, radius in ((3.0, 2.0), (4.0, 2.0), (3.5, 1.5), (5.0, 2.5), (2.5, 1.2)):
            configs.append((dist, radius))
    configs.append(None)
    out = {"patio": rid, "chairs": [], "tree": None, "flower_bed": None}
    chosen = None
    for conf in configs:
        tid = None
        if conf:
            dist, radius = conf
            tx, ty = (cols / 2.0) * C.CELL + sx * dist, (rows / 2.0) * C.CELL + sy * dist
            cell = [int(C.clamp(tx / C.CELL, 0, cols - 1)), int(C.clamp(ty / C.CELL, 0, rows - 1))]
            tid = p.floor_item(rid, "generic_item", near=cell, label="maple tree", category="tree", portable=False, blocks_path=True,
                               size_m=[0.5, 0.5, 7.0], mass_kg=500.0, shade={"radius_m": radius, "height_m": 4.5, "opacity": 0.9})
            if tid is None:
                continue
        view = dict(ctx.state(), clock=cfg.get("clock") or {}, time_s=0.0, weather=cfg.get("weather") or {})
        blocked = set(geometry.blocked_cells(view, rid, include_agents=True))
        doors = geometry.door_clearance(view, rid)   # chairs and the flower bed stay out of the doorway clearance
        cells = [(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked]
        mins = window_shade_min(view, rid, cells, start, until)
        good = [c for c in cells if mins[c] >= 0.85 and c not in doors]
        bad = [c for c in cells if mins[c] <= 0.3 and c not in doors]
        walk = geometry.reachable_cells(view, rid)
        reach = [c for c in good if furnish.can_reach(view, rid, [c], reach=walk)]
        if len(reach) >= int(chairs) + 2 and len(bad) >= int(chairs):
            chosen = (tid, good, bad)
            break
        if tid:
            del cfg["entities"][tid]
    if chosen is None:
        raise SpecError("climate.patio_shade: no shade layout gives both lasting shade and sun in " + rid)
    tid, good, bad = chosen
    out["tree"] = tid
    if flower_bed and good:
        # a planted bed (ground layer, crushable) in the lasting shade, outside the doorway clearance
        fb = p.floor_item(rid, "generic_item", cell=list(good[len(good) // 2]), label="flower bed", category="flower_bed", portable=False,
                          ground_layer=True, crushable=True, size_m=[0.5, 0.5, 0.25], mass_kg=0.0, material="soil")
        out["flower_bed"] = fb
    used = set()
    chair_props = dict(portable=True, blocks_path=True, size_m=[0.5, 0.5, 0.85], mass_kg=4.0, shade_watch={"from_s": start, "to_s": until})
    probe = dict(p.reg.make("outdoor_chair", rid, **chair_props))
    for i in range(int(chairs)):
        options = [c for c in bad if c not in used]
        start_at = (i * 5) % max(1, len(options))
        for cell in options[start_at:] + options[:start_at]:
            # a sunny cell the kernel placement rule accepts for a path-blocking chair (keeps every doorway, the
            # flower bed and the other chairs accessible; the chair itself reachable)
            if geometry.free_floor_cell(ctx.state(), rid, near=cell, record=probe, keep_access=True) != list(cell):
                continue
            cid = p.floor_item(rid, "outdoor_chair", cell=list(cell), **chair_props)
            out["chairs"].append(cid)
            used.add(cell)
            break
    if len(out["chairs"]) < int(chairs):
        raise SpecError("climate.patio_shade: could not place the chairs in the sun")
    for i, cid in enumerate(out["chairs"], 1):
        out["chair_{}".format(i)] = cid
    out["sit_from_s"] = start
    out["sit_until_s"] = until
    return out


# ---------------------------------------------------------------------------------------------
# energy and counterfactual baseline

@scenario_helper("climate.energy_house")
def energy_house(ctx, room="living_room", kitchen="kitchen", bedroom="master_bedroom", cpap=True, aquarium=False, console=True,
                 chargers=2, dishwasher=True):
    """Metered loads for energy tasks. The kitchen's own refrigerator / dishwasher (appliances domain) are reused when the
    house has them (marked essential / given a metered program), else simple metered stand-ins are placed as furniture
    (connectivity-checked); small loads go on free reachable floor cells. Missing rooms only drop their loads."""
    cfg = ctx.config
    p = ctx.placer
    out = {"essentials": [], "vampires": [], "fridge": None, "cpap": None, "strip": None, "console": None, "cable_box": None,
           "dishwasher": None}
    k = _room(ctx, kitchen, required=False)
    lr = _room(ctx, room, required=False)
    br = _room(ctx, bedroom, required=False)
    state = ctx.state()

    def existing(rid, kinds):
        for eid in sorted(cfg["entities"]):
            rec = cfg["entities"][eid]
            if (rec.get("category") in kinds or rec.get("type") in kinds) and not rec.get("climate_kind") and C.room_of(state, eid) == rid:
                return eid
        return None

    def put(rid, label, **props):
        return p.floor_item(rid, "generic_item", label=label, **props)

    def furniture(rid, label, **props):
        eid = p.furniture(rid, "generic_item", label=label, **props)
        return eid if eid is not None else put(rid, label, **props)
    if k:
        f = existing(k, ("refrigerator", "fridge"))
        if f:
            frec = cfg["entities"][f]
            frec["essential"] = True
            frec.setdefault("power_w", 150.0)
        else:
            f = furniture(k, "refrigerator", category="refrigerator", essential=True, on=True, power_w=150.0, standby_w=0.0, plug_required=True,
                          plugged_in=True, size_m=[0.8, 0.7, 1.8], portable=False, blocks_path=True)
        out["fridge"] = f
        if f:
            out["essentials"].append(f)
    if br and cpap:
        c = put(br, "CPAP machine", category="medical_device", essential=True, critical=True, on=True, power_w=40.0, plug_required=True,
                plugged_in=True, size_m=[0.2, 0.15, 0.12])
        out["cpap"] = c
        if c:
            out["essentials"].append(c)
    if lr and console:
        strip = put(lr, "switched power strip", category="power_strip", outlet_kind="strip", receptacles=6, switchable=True, on=True, controls=[],
                    plug_required=True, plugged_in=True, size_m=[0.3, 0.06, 0.04])
        if strip:
            g = put(lr, "game console", category="game_console", on=False, power_w=150.0, standby_w=12.0, plug_required=True, plugged_in=True,
                    outlet=strip, size_m=[0.3, 0.25, 0.08])
            tvbox = put(lr, "cable box", category="audio", on=False, power_w=25.0, standby_w=18.0, plug_required=True, plugged_in=True, outlet=strip,
                        size_m=[0.25, 0.2, 0.05])
            cfg["entities"][strip]["controls"] = [e for e in (g, tvbox) if e]
            out.update(strip=strip, console=g, cable_box=tvbox)
            out["vampires"] += [e for e in (g, tvbox) if e]
    for i in range(int(chargers)):
        if lr:
            ch = put(lr, "phone charger", category="charger", on=False, standby_w=0.5, plug_required=True, plugged_in=True, size_m=[0.05, 0.05, 0.05])
            if ch:
                out["vampires"].append(ch)
    if k and dishwasher:
        d = existing(k, ("dishwasher",))
        if d is None:
            d = furniture(k, "dishwasher", category="dishwasher", portable=False, blocks_path=True, size_m=[0.6, 0.6, 0.85], power_w=1800.0,
                          standby_w=1.0, plug_required=True, plugged_in=True, openable=True, open=False,
                          appliance={"programs": {"normal": {"duration_s": 7200}, "eco": {"duration_s": 10800}}, "program": "normal", "state": "idle",
                                     "requires_power": True, "requires_closed": True})
        out["dishwasher"] = d
    return out


@scenario_helper("climate.baseline")
def baseline_helper(ctx, hours=8.0, room=None, watch_rooms=None, target_fraction=0.85, margin_c=0.5):
    """Run the configured house idle for ``hours`` (all loaded domains) and report the counterfactual. Use as the LAST setup op."""
    from ...kernel.engine import HouseEnv
    cfg = ctx.config
    rooms = list(watch_rooms or [])
    if room:
        rooms.append(_room(ctx, room))
    sec = cfg.setdefault(C.SECTION, {})
    sec["watch_rooms"] = sorted(set(sec.get("watch_rooms", []) + rooms))
    env = HouseEnv(copy.deepcopy(cfg), None, ctx.seed, trace=False, observe_on_step=False)
    remaining = int(float(hours) * 3600)
    while remaining > 0:
        chunk = min(3600, remaining)
        env.step("wait", seconds=chunk)
        remaining -= chunk
    st = env.world.s
    e = (st.get(C.SECTION) or {}).get("energy") or {}
    tr = (st.get(C.SECTION) or {}).get("trackers") or {}
    out = {"baseline_cost_usd": round(C.num(e.get("cost_usd")), 4), "baseline_kwh": round(C.num(e.get("kwh")), 4),
           "baseline_gas_kwh": round(C.num(e.get("gas_kwh")), 4),
           "baseline_cost_target_usd": round(C.num(e.get("cost_usd")) * float(target_fraction), 4)}
    for rid in rooms:
        out["baseline_max_c"] = tr.get("max_c:" + rid)
        out["baseline_max_c__" + rid] = tr.get("max_c:" + rid)
        out["baseline_max_target_c"] = round(C.num(tr.get("max_c:" + rid)) - float(margin_c), 3)
        out["baseline_end_c__" + rid] = round(C.num(st["rooms"][rid].get("temperature_c")), 3)
    return out
