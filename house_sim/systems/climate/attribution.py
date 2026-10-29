"""Who caused a climate harm: the robot's operation markers and the cause sets of the harm sites (review C8 / M9).

The rule (docs/PHYSICS.md "Attribution", TIER1_DECISIONS, VERIFY_HARDENING_DECISIONS 3) and the helpers that read the
kernel's markers live in common.py (robot_acting, robot_arranged, robot_enabled, robot_touched, charge, hazard robot=).
This module adds what the kernel does not record:

* ROBOT_FIELD markers. An after-hook on every verb that can change how a climate device or a window works (OPERATE_VERBS:
  switch_on / switch_off, set, open / close, plug_in, install / uninstall, the mechanisms controls, cover, refill_reservoir,
  ...) appends the verb to the ``_cl_robot`` list of each climate device or window the action named (its entity
  arguments), and of the devices a named wall switch, pull chain or remote controls. It is an underscore field (never
  observed), written once per verb, and only on climate devices, windows and outside doors (a door whose connection
  leads outdoors), so other records never change. A refused action is rolled back with its marker. The kernel's
  _robot_moved marker already covers placing, moving and filling things.
* The cause sets of the time-driven harms:
    a device's harms (a heater's clearance, a humidifier's mist, a fan on a bed or at a flame, a dehumidifier hose):
        common.robot_enabled on the device (or on the thing it harms, when the robot put that there);
    a window's harms (rain on the floor, a pet at the opening, a child at an upper-floor opening): robot_enabled on it;
    the room's climate (a person's heat or cold stress, an infant in a draft, pollen, a pet's heat, a freezing pipe):
        robot_room(side) -- what the robot did there pushes that way (cold, heat or air): a window or outside door of
        the room it opened and left open (every side); a heater or radiator it switched or turned up (heat) or switched
        off or turned down (cold); a fan it set going (cold, air) or stopped (heat); a mini-split it left cooling (cold),
        heating (heat) or switched off (either); a purifier it stopped or emptied (air); the thermostat it left off or
        cooling or set low (cold) or left heating (heat), the central system switched off (cold), its outdoor unit
        covered (heat); or a running device of that kind it put there. Help the other way (heating switched on for a
        cold resident, a window closed in the frost) takes nothing over;
    the central system's harms (frozen coil, stressed heat exchanger, high static pressure, no or wrapped filter, blocked
        returns, short cycling, a covered condenser): robot_hvac -- the robot switched, set or (un)installed into the
        system (opening and closing its filter door alone does not count), ran it from a thermostat, fitted or opened its
        filter, covered its outdoor unit, shut one of its registers and left it shut, or put something over a register
        or return.
"""
from ...kernel.plugins import after
from . import common as C

# the verbs after which a named climate device or window is the robot's (common.OFF_VERBS only make things safer)
OPERATE_VERBS = ("switch_on", "switch_off", "set", "set_mode", "start", "stop", "open", "close", "open_cover", "close_cover",
                 "lock", "unlock", "install", "uninstall", "press", "hold_press", "move_lever", "turn", "touch_select",
                 "raise", "lower", "tilt_slats", "pull_handle", "push_handle", "latch", "unlatch", "plug_in", "unplug",
                 "cover", "uncover", "wrap", "unwrap", "remove_packing", "attach", "detach", "refill_reservoir",
                 "empty_reservoir", "reverse_fan", "childproof", "bleed_radiator", "reset_breaker")
HOUSE_KINDS = ("thermostat", "hvac_system", "outdoor_condenser")
BLAME_KEY = "climate.blame"     # World.phys_scratch: the robot_room / robot_hvac answers of this substep


def outside_door(state, rec):
    """A door (or gate) whose connection leads outdoors: left open, it lets the weather in."""
    rooms = state.get("rooms") or {}
    return any((rooms.get(r) or {}).get("outdoor") for r in (rec.get("connects") or ()))


def relevant(state, rec):
    """Climate devices (climate_kind), windows and outside doors: the things whose operation decides a climate harm."""
    return bool(rec) and bool(rec.get("climate_kind") or rec.get("category") == "window" or outside_door(state, rec))


def note(w, eid, verb):
    """Remember that the robot used `verb` on eid (a climate device, window or outside door; anything else is left
    unchanged)."""
    rec = w.s["entities"].get(eid) if isinstance(eid, str) else None
    if not relevant(w.s, rec):
        return
    ops = list(rec.get(C.ROBOT_FIELD) or ())
    if verb not in ops:
        ops.append(verb)
        w.mut(eid)[C.ROBOT_FIELD] = ops


def _operated(ctx, result):
    w = ctx.w
    verb = ctx.spec.name
    entities = w.s["entities"]
    named = [v for v in ctx.args.values() if isinstance(v, str) and v in entities]
    for eid in named:
        rec = entities.get(eid) or {}
        note(w, eid, verb)
        # a wall switch, a pull chain or a remote (its paired device): what it controls was operated
        controlled = list(rec.get("controls") or ()) + [rec.get("chain_of"), rec.get("controls_device")]
        for other in controlled:
            if isinstance(other, str) and other in entities:
                note(w, other, verb)


for _verb in OPERATE_VERBS:
    after(_verb, order=190)(_operated)


# ---------------------------------------------------------------------------------------------
# cause sets of the time-driven harms (asked only when a harm condition holds)

def _blame(w):
    """This substep's memo of robot_room / robot_hvac answers (World.phys_scratch is reset with every step; nothing the
    robot does can change them between two substeps of a step, and the action phase is never memoized)."""
    scratch = getattr(w, "phys_scratch", None)
    if scratch is None:
        return {}
    t = w.s.get("time_s", 0.0)
    memo = scratch.get(BLAME_KEY)
    if memo is None or memo.get("t") != t:
        memo = scratch[BLAME_KEY] = {"t": t, "rooms": {}, "hvac": {}}
    return memo


def _running(rec):
    """A device at work now (its derived running flag, or a ceiling fan's blades turning)."""
    return bool(rec.get("running") or C.num(rec.get("rpm")) >= 5.0)


SIDES = ("cold", "heat", "air")


def _house_sides(rec, ops):
    """The sides a thermostat, central system or outdoor unit the robot operated pushes the whole house to."""
    k = rec.get("climate_kind")
    out = set()
    if k == "thermostat":
        from . import hvac
        mode = (rec.get("settings") or {}).get("mode")
        heat_c, cool_c = hvac.setpoints_c(rec)
        if mode not in ("heat", "auto", "emergency_heat") or heat_c < 16.0:
            out.add("cold")            # left the heating off, cooling, or set low
        if mode in ("heat", "emergency_heat") or (mode == "auto" and cool_c > 30.0):
            out.add("heat")            # left the heating on (or the cooling set high)
    elif k == "hvac_system" and not rec.get("on", True):
        out.add("cold")                # its service switch left off
    elif k == "outdoor_condenser" and (rec.get("covered") or rec.get("cover_on") or rec.get("covered_by")):
        out.add("heat")                # covered: the cooling is crippled
    return out


def _device_sides(w, eid, rec, ops):
    """The sides a room device the robot operated (or placed running) pushes its room to."""
    k = rec.get("climate_kind")
    running = _running(rec)
    started = any(v not in C.OFF_VERBS for v in ops) or (running and C.robot_arranged(w, eid))
    stopped = any(v in C.OFF_VERBS for v in ops) or "set" in ops or "press" in ops
    out = set()
    if k == "space_heater":
        if running and started:
            out.add("heat")
        elif not running and stopped:
            out.add("cold")
    elif k == "radiator":
        trv = C.num((rec.get("settings") or {}).get("trv"), 3.0)
        if ops and trv >= 4.0:
            out.add("heat")
        elif ops and trv <= 1.0:
            out.add("cold")
    elif k in ("ceiling_fan", "portable_fan"):
        if running and started:
            out.update(("cold", "air"))
        elif not running and stopped:
            out.add("heat")
    elif k == "mini_split":
        mode = (rec.get("settings") or {}).get("mode")
        if running and started:
            out.add("heat" if mode == "heat" else "cold" if mode in ("cool", "dry") else "air")
        elif not running and stopped:
            out.update(("cold", "heat"))
    elif k == "air_purifier":
        if (not running and stopped) or "uninstall" in ops:
            out.add("air")
    return out


def _opened(rec, ops):
    """A window or outside door the robot opened (an opening verb) that stands open now."""
    open_now = bool(rec.get("open")) or C.num((rec.get("mech") or {}).get("open_frac")) > 0.01
    return open_now and any(v not in C.OFF_VERBS for v in ops)


def _room_scan(w):
    """{side: (house-wide, {rooms})}: where the robot's climate operations push the air (module doc)."""
    state = w.s
    entities = state["entities"]
    house = {side: False for side in SIDES}
    rooms = {side: set() for side in SIDES}
    for door in C.static_ids(w, "climate.connecting", C.connects_rooms):
        rec = entities.get(door)
        if rec is None or not rec.get(C.ROBOT_FIELD) or not outside_door(state, rec):
            continue
        if _opened(rec, rec.get(C.ROBOT_FIELD)):
            for r in rec.get("connects") or ():
                if not (state["rooms"].get(r) or {}).get("outdoor"):
                    for side in SIDES:
                        rooms[side].add(r)
    for wid in C.static_ids(w, "climate.windows", C.is_window):
        rec = entities.get(wid)
        if rec is not None and rec.get(C.ROBOT_FIELD) and _opened(rec, rec.get(C.ROBOT_FIELD)):
            rid = C.room_of(state, wid)
            for side in SIDES:
                rooms[side].add(rid)
    for eid in C.static_ids(w, "climate.kinds", C.has_kind):
        rec = entities.get(eid)
        if rec is None:
            continue
        ops = rec.get(C.ROBOT_FIELD) or ()
        if rec.get("climate_kind") in HOUSE_KINDS:
            for side in (_house_sides(rec, ops) if ops else ()):
                house[side] = True
            continue
        if not ops and not (_running(rec) and C.robot_arranged(w, eid)):
            continue
        sides = _device_sides(w, eid, rec, ops)
        if sides:
            rid = C.room_of(state, eid)
            for side in sides:
                rooms[side].add(rid)
    return {side: (house[side], rooms[side]) for side in SIDES}


def robot_room(w, rid, side):
    """True when what the robot did pushes room rid's air to `side` ("cold", "heat" or "air": draft and pollen; see the
    module doc): an exposure of that kind there is its doing."""
    if C.robot_acting(w):
        return True
    memo = _blame(w)
    if "scan" not in memo:
        memo["scan"] = _room_scan(w)
    house, rooms = memo["scan"][side]
    return house or rid in rooms


def robot_hvac(w, sid):
    """True when the robot operated central system sid or what belongs to it (see the module doc)."""
    if C.robot_acting(w):
        return True
    memo = _blame(w)
    if sid in memo["hvac"]:
        return memo["hvac"][sid]
    memo["hvac"][sid] = out = _hvac_scan(w, sid)
    return out


# looking behind a filter door, or a register opened back up, changes nothing that strains the system
HVAC_PASSIVE = frozenset(("open_cover", "close_cover"))


def _hvac_scan(w, sid):
    state = w.s
    entities = state["entities"]
    rec = entities.get(sid) or {}
    if any(v not in HVAC_PASSIVE for v in C.robot_ops(state, sid)) or C.robot_arranged(w, sid):
        return True             # it switched, set or (un)installed into the system itself
    slot = ((rec.get("slots") or {}).get("filter") or {}).get("item")
    if slot and C.robot_touched(w, slot):
        return True             # the filter in it is one the robot fitted, opened or wrapped
    for eid in C.static_ids(w, "climate.kinds", C.has_kind):
        other = entities.get(eid) or {}
        k = other.get("climate_kind")
        if k == "thermostat" and other.get("controls_hvac") in (None, sid) and other.get(C.ROBOT_FIELD):
            return True         # it ran the system from its thermostat
        if k == "outdoor_condenser" and other.get("serves") == sid and other.get(C.ROBOT_FIELD):
            return True         # it covered (or uncovered) the outdoor unit
        if k in ("register", "return_grille") and other.get("system") in (None, sid):
            damper = C.num((other.get("settings") or {}).get("damper"), 1.0)
            if (other.get(C.ROBOT_FIELD) and damper < 0.95) or _covered_by_robot(w, eid, other):
                return True     # it shut a damper (and left it shut) or put something over a vent
    return False


def _covered_by_robot(w, eid, rec):
    """Something the robot placed sits over or in front of a register or return (on its floor cell, or the thing it is
    under or behind)."""
    state = w.s
    entities = state["entities"]
    if rec.get("relation") in ("under", "behind"):
        return C.robot_arranged(w, rec.get("location"))
    rid = rec.get("location")
    cell = rec.get("pos")
    if rid not in state["rooms"] or not cell:
        return False
    from ...kernel import geometry
    cell = (int(cell[0]), int(cell[1]))
    for other in geometry.room_entities(state, rid):
        if other == eid:
            continue
        orec = entities.get(other) or {}
        if not orec.get("_robot_moved") or not orec.get("pos"):
            continue
        if cell in {tuple(c) for c in geometry.footprint(orec)}:
            return True
    return False
