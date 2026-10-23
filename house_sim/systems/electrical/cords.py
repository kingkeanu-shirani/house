"""Plugs and cords: plug_in / unplug (fidelity G for plug geometry and cord routing).

plug_in(target=device, destination=outlet|strip|extension cord|smart plug|timer|adapter)
  * plug shape must fit the socket (IEC type letters, see common.SOCKET_ACCEPTS); a grounded
    plug never goes into a 2-slot socket; polarized / grounded / sleeved plugs only go in one
    way (orientation=reversed forces it: bent blade, damage, no power);
  * seat=partial leaves the blades exposed (no power, electrical hazard label);
  * the cord must reach along the chosen route (direct | along_wall). A cord whose cells cross
    the room's walkway (door-to-door / door-to-centre paths) becomes a trip floor hazard;
    under a rug it is a heat/fire hazard; through a doorway it is both;
  * damaged cords (fray/exposed/chewed/scorched) energized -> electrical_hazard; a crushed cord
    shorts and trips the breaker; wet hands or a wet outlet -> shock check (GFCI trips);
  * a strip or extension cord plugged into another strip/cord is daisy-chaining; a
    wall_outlet_only appliance (space heater, kettle) through a strip/cord breaks the maker's rule.
  Plugging a device that is already plugged into the same place with a new route re-dresses
  its cord without interrupting power (optionally bundling it with a held cable tie).
unplug(target=device [grip=plug_head|cord] | target=strip/outlet socket=n)
  * on a crowded strip (3+ look-alike plugs) the device's cord must be traced first
    (inspect device where=behind); socket=n pulls whatever is in that socket;
  * grip=cord damages the strain relief; unplugging running computers, critical or always-on
    devices has consequences handled by the device models (data loss, harms).
"""
from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after
from . import common as C
from . import percept
from . import power as P

SEVERE = {"exposed", "chewed", "scorched", "fray"}


# ----------------------------------------------------------------------------------------
# geometry

def door_cells(state, rid):
    """Doorway cells of a room (kernel v2.3 shared door model), sorted and unique."""
    return sorted(set(geometry.doorway_cells(state, rid)))


def walkway_cells(state, rid):
    """Cells people walk through: paths between doorways and from each doorway to the room centre."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    doors = door_cells(state, rid)
    cells = set()
    for d in doors:
        cells.add(d)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = (d[0] + dx, d[1] + dy)
            if geometry.in_grid(room, c):
                cells.add(c)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    centre = (cols // 2, rows // 2)
    if centre in blocked:
        free = [(x, y) for y in range(rows) for x in range(cols) if (x, y) not in blocked]
        if free:
            centre = min(free, key=lambda c: (abs(c[0] - cols // 2) + abs(c[1] - rows // 2), c[1], c[0]))
    targets = list(doors[1:]) + [centre]
    for i, a in enumerate(doors):
        for b in [t for t in targets if t != a]:
            path = geometry.find_path(dict(state, people={}, pets={}), rid, a, [b])
            if path:
                cells.update(path)
    return cells


def perimeter(room):
    cols, rows = geometry.grid_size(room)
    if cols == 1 or rows == 1:
        return [(x, y) for y in range(rows) for x in range(cols)]
    ring = [(x, 0) for x in range(cols)] + [(cols - 1, y) for y in range(1, rows)] + \
           [(x, rows - 1) for x in range(cols - 2, -1, -1)] + [(0, y) for y in range(rows - 2, 0, -1)]
    return ring


def line(a, b):
    """Bresenham cells from a to b inclusive."""
    x0, y0 = a
    x1, y1 = b
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    out = []
    while True:
        out.append((x0, y0))
        if (x0, y0) == (x1, y1):
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return out


def cell_of(state, eid, toward=None):
    """Floor cell under an entity (nearest footprint cell to `toward`), and its room."""
    rid, cells = C.anchor_cells(state, eid)
    if not cells:
        if rid == state["robot"].get("room"):
            return rid, tuple(state["robot"].get("pos") or (0, 0))
        return rid, None
    if toward is None:
        return rid, cells[0]
    return rid, min(cells, key=lambda c: (geometry.chebyshev(c, toward), c[1], c[0]))


def _arc(ring, i, j, step):
    out, k = [], i
    while True:
        out.append(ring[k])
        if k == j:
            break
        k = (k + step) % len(ring)
    return out


def route_cells(state, device, outlet, route):
    """(cells, length_m, hazards, rooms) of a cord from device to outlet."""
    o_room, o_cell = cell_of(state, outlet)
    d_room, d_cell = cell_of(state, device, toward=o_cell)
    if o_cell is None or d_cell is None or d_room is None or o_room is None:
        return [], 0.0, [], []
    vertical = geometry.height_m(state, device) + geometry.height_m(state, outlet) if device in state["entities"] else 0.5
    if d_room != o_room:
        conn = next((c for _, c in sorted(state.get("connections", {}).items())
                     if set(c["rooms"]) == {d_room, o_room}), None)
        if conn is None:
            return None, 0.0, ["no_path"], [d_room, o_room]
        a = line(d_cell, tuple(conn["cells"][d_room]))
        b = line(tuple(conn["cells"][o_room]), o_cell)
        cells = [(d_room, c) for c in a] + [(o_room, c) for c in b]
        length = (len(a) + len(b)) * geometry.CELL + vertical
        return cells, round(length, 2), ["doorway"], [d_room, o_room]
    room = state["rooms"][d_room]
    if route == "along_wall":
        ring = perimeter(room)
        start = min(range(len(ring)), key=lambda k: (geometry.chebyshev(ring[k], d_cell), k))
        end = min(range(len(ring)), key=lambda k: (geometry.chebyshev(ring[k], o_cell), k))
        doors = set(door_cells(state, d_room))
        options = []
        for step in (1, -1):
            arc = _arc(ring, start, end, step)
            options.append((any(c in doors for c in arc), len(arc), step, arc))
        options.sort(key=lambda t: (t[0], t[1], t[2]))
        arc = options[0][3]
        cells = line(d_cell, ring[start])[:-1] + arc + line(ring[end], o_cell)[1:]
    else:
        cells = line(d_cell, o_cell)
    dedup = []
    for c in cells:
        if not dedup or dedup[-1] != c:
            dedup.append(c)
    if device in state["entities"] and outlet in state["entities"]:
        # a cord that never touches the floor (it runs along the counter, desk or stand top the device stands on, to a
        # receptacle above or just behind it) only covers the height difference; otherwise it drops to the floor and
        # climbs back up to the receptacle
        blocked = set(geometry.blocked_cells(state, d_room, include_agents=False))
        if dedup and all(c in blocked for c in dedup):
            vertical = abs(geometry.height_m(state, device) - geometry.height_m(state, outlet)) + 0.1
    length = max(0, len(dedup) - 1) * geometry.CELL + vertical
    return [(d_room, c) for c in dedup], round(length, 2), [], [d_room]


PORTABLE_HEATERS = ("heater", "space_heater")
FIXED_HEAT = ("radiator", "stove", "fireplace", "baseboard_heater", "wood_stove")


def _heat_source(rec):
    """Portable heaters always (they are meant to be switched on); fixed radiators/stoves/fireplaces while they are
    firing or hot (climate's radiators sit at ~65 C when heating, room temperature otherwise); anything above 80 C."""
    if rec.get("category") in PORTABLE_HEATERS:
        return True
    if C.num(rec.get("internal_temp_c"), 0.0) > 80 or C.num(rec.get("temperature_c"), 21.0) > 80:
        return True
    if rec.get("category") in FIXED_HEAT or rec.get("climate_kind") == "radiator":
        return bool(rec.get("heating_on") or rec.get("burning") or rec.get("lit") is True
                    or C.num(rec.get("temperature_c"), 21.0) >= 40 or C.num(rec.get("internal_temp_c"), 0.0) >= 40)
    return False


def cord_hazards(state, device, cells):
    """Hazard kinds for routed cord cells [(room, cell)]. The device's own floor footprint is not part of its cord (the
    CordCheck rule): a heater standing in a walkway or on a rug is itself the obstacle there, not a trip-cord or a cord
    under the rug. Nor is the device a heat source for its own cord: a space heater's cord leaves the heater by design
    (a cord near another heater, or near a radiator, still counts)."""
    hazards, trip = set(), []
    by_room = {}
    drec = state["entities"].get(device) or {}
    wet_rated = P.weather_rated(drec)   # outdoor/wet-location cords may run by water
    own = set(geometry.footprint(drec)) if drec.get("location") in state.get("rooms", {}) else set()
    for rid, c in cells:
        if rid == drec.get("location") and tuple(c) in own:
            continue
        by_room.setdefault(rid, []).append(c)
    for rid, cs in sorted(by_room.items()):
        walk = walkway_cells(state, rid)
        blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
        doors = set(door_cells(state, rid))
        rugs = set()
        water = set()
        heat = set()
        for eid in geometry.room_entities(state, rid):
            rec = state["entities"][eid]
            if rec.get("category") == "rug":
                rugs.update(geometry.footprint(rec))
            if rec.get("water_source") or rec.get("drain") or rec.get("category") in ("sink", "bathtub", "shower"):
                for fc in geometry.footprint(rec):
                    water.update(geometry.near_cells([fc], state["rooms"][rid]))
            if eid != device and _heat_source(rec):
                for fc in geometry.footprint(rec):
                    heat.update(geometry.near_cells([fc], state["rooms"][rid]))
        for c in cs:
            if c in doors:
                hazards.add("doorway")
            if c in walk and c not in blocked:
                hazards.add("walkway")
                trip.append([rid, [c[0], c[1]]])
            if c in rugs:
                hazards.add("under_rug")
            if c in water and not wet_rated:
                hazards.add("near_water")
            if c in heat:
                hazards.add("near_heat")
    return sorted(hazards), trip


class CordCheck:
    """Generation/setup rule for a cord run (kernel v2.3 layout guarantees): the cord's floor cells stay out of every
    doorway clearance (geometry.door_clearance: doorway cells, their neighbours, door leaves, overhead thresholds) and
    its trip cells never cut a hazard-free route between doorways or the only hazard-free access to anything
    (geometry.access_report / not_worse with the room's other hazards, geometry.placement_hazards, as obstacles).
    The device's own earlier cord cells are ignored (re-routing); its own floor footprint is not part of its cord.
    One instance evaluates many candidate runs of one device (per-room facts are cached)."""

    def __init__(self, state, device):
        self.state, self.device = state, device
        self._rooms = {}

    def _room(self, rid):
        hit = self._rooms.get(rid)
        if hit is not None:
            return hit
        st, dev = self.state, self.device
        room = st["rooms"][rid]
        static = set(geometry.blocked_cells(st, rid, include_agents=False))
        own = {tuple(h["cell"]) for h in room.get("floor_hazards", []) if h.get("source") == dev and h.get("cord")}
        rec = st["entities"].get(dev) or {}
        fp = set(geometry.footprint(rec)) if rec.get("location") == rid else set()
        base = set(static)
        for cell, items in geometry.placement_hazards(st, rid).items():
            if cell in own and cell not in fp:
                items = [(k, s) for k, s in items if not (s == dev and k == "trip")]
            if items:
                base.add(cell)
        targets = geometry.access_targets(st, rid, level="all")
        before = geometry.access_report(st, rid, base, targets)
        hit = (static, base, targets, before, geometry.door_clearance(st, rid), fp)
        self._rooms[rid] = hit
        return hit

    def ok(self, cells, trip):
        """True when a cord laid along `cells` [(room, cell)] with trip cells `trip` [[room, [c, r]]] is acceptable."""
        by_room, trips = {}, {}
        for rid, c in cells or []:
            by_room.setdefault(rid, set()).add((int(c[0]), int(c[1])))
        for rid, c in trip or []:
            trips.setdefault(rid, set()).add((int(c[0]), int(c[1])))
        for rid in sorted(set(by_room) | set(trips)):
            if rid not in self.state["rooms"]:
                return False
            static, base, targets, before, clear, fp = self._room(rid)
            floor = {c for c in by_room.get(rid, set()) | trips.get(rid, set()) if c not in static and c not in fp}
            if floor & clear:
                return False
            new = trips.get(rid, set()) - base - fp
            if new and not geometry.not_worse(before, geometry.access_report(self.state, rid, base | new, targets)):
                return False
        return True


def cord_options(state, device, outlet, routes=("along_wall", "direct"), check=None, cord_m=None):
    """Candidate cord runs from device to outlet, one per route the cord reaches along: dicts with ok (CordCheck
    verdict, True without a check), n (hazard kinds), length, route, cells, hazards, trip. Sorted best first:
    acceptable, fewest hazards, then the order of `routes` (preferred route first; along the wall by default)."""
    rec = state["entities"][device]
    have = C.num(rec.get("cord_length_m"), 1.8) if cord_m is None else cord_m
    out = []
    for i, r in enumerate(dict.fromkeys(routes)):
        cells, length, hazards, _ = route_cells(state, device, outlet, r)
        if cells is None or length is None or length > have + 1e-9:
            continue
        more, trip = cord_hazards(state, device, cells)
        kinds = sorted(set(hazards or []) | set(more))
        ok = check.ok(cells, trip) if check is not None else True
        out.append({"ok": ok, "n": len(kinds), "length": length, "route": r, "cells": cells, "hazards": kinds, "trip": trip, "i": i})
    out.sort(key=lambda o: (not o["ok"], o["n"], o["i"], o["length"]))
    return out


def reroute_moved(w):
    """Episode start: a corded device whose cord run no longer starts where the device stands (moved by the layout
    repair pass or a setup op after it was plugged in) gets a fresh run to the same receptacle by the CordCheck rule,
    else its cord is dressed along the baseboard (no floor trip cells). Devices that did not move keep their run, so
    deliberate cord hazards seeded by a setup stay."""
    state = w.s
    for eid in C.ids(w, "plug_devices"):
        rec = state["entities"].get(eid)
        if not rec or not rec.get("plugged_in") or not rec.get("cord_cells") or rec.get("plug_in_place") == "wall":
            continue
        first = rec["cord_cells"][0]
        rid, cells = C.anchor_cells(state, eid)
        if not cells or (rid == first[0] and (int(first[1][0]), int(first[1][1])) in set(cells)):
            continue
        out = rec.get("outlet")
        if not out or out not in state["entities"]:
            continue
        opts = cord_options(state, eid, out, (rec.get("cord_route") or "along_wall", "along_wall", "direct"), CordCheck(state, eid))
        m = w.mut(eid)
        if opts and opts[0]["ok"]:
            best = opts[0]
            m["cord_route"], m["cord_length_used_m"], m["cord_hazard"] = best["route"], best["length"], list(best["hazards"])
            m["cord_cells"] = [[r, [c[0], c[1]]] for r, c in best["cells"]]
            set_floor_hazards(w, eid, best["trip"])
        else:
            m["cord_route"], m["cord_cells"], m["cord_length_used_m"], m["cord_hazard"] = "dressed", [], 0.0, []
            set_floor_hazards(w, eid, [])


def set_floor_hazards(w, device, trip_cells):
    """Replace this device's cord trip cells in room floor_hazards."""
    state = w.s
    rooms = sorted({rid for rid, room in state["rooms"].items()
                    if any(h.get("source") == device for h in room.get("floor_hazards", []))} | {t[0] for t in trip_cells})
    for rid in rooms:
        keep = [h for h in state["rooms"][rid].get("floor_hazards", []) if h.get("source") != device]
        new = keep + [{"cell": list(c), "kind": "trip", "source": device, "cord": True} for r, c in trip_cells if r == rid]
        if new != state["rooms"][rid].get("floor_hazards", []):
            w.room_m(rid)["floor_hazards"] = new


# ----------------------------------------------------------------------------------------
# plug compatibility

def plug_fits(plug, socket):
    return plug in C.SOCKET_ACCEPTS.get(socket, [socket])


def polarized_one_way(rec):
    plug = rec.get("plug_type")
    return plug in C.ONE_WAY_PLUGS or (plug == "type_a" and rec.get("plug_polarized", True))


def plug_look(state, eid, realistic=False):
    """What a plug in a receptacle looks like. realistic: a plug-in-place adapter is the adapter itself (its id, which
    the robot sees in the socket) rather than its product label (which could resolve a look-alike)."""
    rec = state["entities"][eid]
    look = rec.get("plug_look")
    if look:
        return look
    if rec.get("plug_in_place") == "wall":
        return eid if realistic else rec.get("label", "adapter")
    return "{} {}-prong plug".format(rec.get("cord_color", "black"), 3 if rec.get("plug_type") in C.GROUNDED_PLUGS else 2)


def needs_trace(state, device):
    """Several look-alike plugs on one strip: the device's cord must be traced first."""
    rec = state["entities"][device]
    out = rec.get("outlet")
    if not out or rec.get("plug_in_place") == "wall":
        return False
    plugs = P.plugs_in(state, out)
    if len(plugs) < 3:
        return False
    mine = plug_look(state, device)
    same = [d for _, d in plugs if plug_look(state, d) == mine]
    if len(same) < 2 and not rec.get("cord_bundled_with"):
        return False
    return device not in C.section(state).get("traced", {})


# ----------------------------------------------------------------------------------------
# plug_in

def _target_provider(state, dest):
    rec = state["entities"].get(dest)
    require(rec is not None and P.is_provider(rec), "That is not an outlet, power strip, extension cord or plug adapter", "argument")
    return rec


@action("plug_in", params={"target": "entity", "destination": "?entity", "socket": "?int",
                           "orientation": "?str:correct|reversed", "seat": "?str:full|partial",
                           "route": "?str:direct|along_wall", "deploy": "?str:uncoiled|coiled",
                           "tie": "?entity", "tension": "?str:snug|tight"},
        duration=lambda ctx: ctx.data.get("seconds", 4.0), energy=0.01, noise=0.02, category="physical",
        inverse=lambda ctx, before: None if ctx.data.get("reroute") else {"action": "unplug", "target": ctx.args["target"]},
        doc="Plug a device's mains plug into an outlet, power strip, extension cord, smart plug, timer or plug adapter "
            "(destination). Low outlets need a crouch. orientation=reversed forces a one-way plug (damage); seat=partial leaves "
            "blades exposed; route=direct|along_wall chooses the cord path (walkway/rug/doorway crossings become hazards); "
            "deploy=coiled leaves an extension cord coiled (it heats under load). Re-plugging an already plugged device to the "
            "same destination re-dresses its cord route (tie=<held cable tie>, tension=snug|tight bundles it) without cutting power.")
def plug_in(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    dev = ctx.obj(target)
    percept.refuse_unfamiliar(ctx, target, ctx.args.get("destination"))
    require(dev.get("plug_required"), "It has no mains plug", "argument")
    dest = ctx.args.get("destination") or (dev.get("outlet") if dev.get("plugged_in") else None)
    require(dest, "Name the outlet, strip or cord to plug into", "argument")
    require(dest != target, "Cannot plug something into itself", "argument")
    prov = _target_provider(state, dest)
    require(not (prov.get("child_cover") or prov.get("cover_installed") or prov.get("outlet_cover")),
            "A child-safety cover blocks the receptacle; remove the cover first (and refit it after)", "precondition")
    # re-dressing an existing cord
    if dev.get("plugged_in"):
        require(dev.get("outlet") == dest, "It is already plugged in elsewhere; unplug it first")
        require(ctx.args.get("route") or ctx.args.get("tie"), "It is already plugged in there")
        ctx.obj(target, touch=True)
        ctx.data["reroute"] = True
        route = ctx.args.get("route") or dev.get("cord_route") or "direct"
        if route != "dressed":          # a cord dressed along the baseboard keeps its run when only a tie is added
            _route(ctx, target, dest, route)
        _bundle(ctx, target)
        ctx.data["seconds"] = 12.0
        C.refresh(w)
        return "Re-dressed the cord of {} ({})".format(target, w.ent(target).get("cord_route"))
    # reach the receptacle (low outlets need a crouch) and hold/handle the plug
    ctx.obj(dest, touch=True, grasp=geometry.height_m(state, dest) < geometry.LOW_REACH_M)
    if dev.get("plug_in_place") == "wall":
        require(ctx.holding(target), "Pick it up first: it plugs directly into the receptacle")
    else:
        require(ctx.holding(target) or ctx.free_hand() is not None, "You need a free hand to handle the plug", "capability")
        if not ctx.holding(target):
            ctx.obj(target)  # visible
    require(P.free_socket(state, dest) is not None or ctx.args.get("socket"), "Every receptacle there is in use")
    socket = ctx.args.get("socket") or P.free_socket(state, dest)
    require(1 <= socket <= int(C.num(prov.get("receptacles"), 0)), "No such receptacle", "argument")
    require(socket not in {s for s, _ in P.plugs_in(state, dest)}, "That receptacle is already in use")
    plug, sock = dev.get("plug_type", "type_b"), prov.get("socket_type", "type_b")
    if not plug_fits(plug, sock):
        if plug in C.GROUNDED_PLUGS and sock in ("type_a", "type_a_jp") and plug in ("type_b",):
            raise ActionError("The grounding pin does not fit this 2-slot outlet; never defeat the ground pin", "safety")
        raise ActionError("A {} plug does not fit a {} socket (use the right outlet or a plug adapter)".format(plug, sock), "precondition")
    m = w.mut(target)
    seconds = 4.0
    orientation = ctx.args.get("orientation") or "correct"
    seat = ctx.args.get("seat") or "full"
    if orientation == "reversed" and polarized_one_way(dev):
        m["plug_damage"] = "bent_prong"
        w.stat("damage")
        C.harm(w, "electrical.forced_plug:" + target)
        seat = "partial"
    m["plugged_in"] = True
    m["outlet"] = dest
    m["_socket"] = int(socket)
    m["plug_seated"] = 1.0 if seat == "full" else 0.5
    if dev.get("plug_in_place") == "wall":
        from ...systems.core import release_from_hands
        release_from_hands(w, target)
        m["location"], m["relation"] = dest, "attached"
        for key in ("pos", "spos", "shelf", "held_hand"):
            m.pop(key, None)
        w.log_move(target, "robot", dest)
    else:
        _route(ctx, target, dest, ctx.args.get("route") or "direct")
        if prov.get("outlet_kind") == "extension" or dev.get("outlet_kind") == "extension":
            pass
    if dev.get("outlet_kind") == "extension":
        m["coiled"] = "tight" if ctx.args.get("deploy") == "coiled" else "uncoiled"
    if seat == "partial" and orientation != "reversed":
        C.harm(w, "electrical.exposed_prongs:" + target)
    # rules and physical consequences
    if P.is_provider(dev) and dev.get("outlet_kind") in ("strip", "extension") and prov.get("outlet_kind") in ("strip", "extension"):
        C.harm(w, "electrical.daisy_chain:" + target, into=dest)
    if dev.get("wall_outlet_only") and prov.get("outlet_kind") in ("strip", "extension"):
        C.harm(w, "electrical.high_draw_on_strip:" + target, into=dest)
    damage = dev.get("_cord_damage", "none")
    if damage in SEVERE and m["plug_seated"] >= 0.99:
        w.stat("electrical_hazard")
        C.harm(w, "electrical.damaged_cord_energized:" + target, damage=damage)
    if damage == "crushed":
        m["_short"] = "always"
    if P.robot_wet(w):
        w.stat("electrical_hazard")
        C.harm(w, "electrical.wet_hands:" + target)
    if C.num(prov.get("wetness"), 0.0) > 0.3:
        P.shock_check(w, dest, "plug_into_wet_outlet")
    if prov.get("_fault") == "arcing":
        C.harm(w, "electrical.used_faulty_outlet:" + dest)
    _voltage_check(w, target)
    ctx.data["seconds"] = seconds
    C.refresh(w)
    bid = P.circuit_of(state, target)
    if bid and bid in state["entities"] and P._circuit_short(state, bid):
        P.trip_breaker(w, bid, "short_circuit")
        w.stat("electrical_hazard")
        C.harm(w, "electrical.closed_onto_fault:" + bid, device=target)
        C.refresh(w)
    rec = w.ent(target)
    status = "energized" if rec.get("energized") else "no power"
    if ctx.realistic:
        # whether power arrived shows only through the device itself (a lit lamp, a screen, a power LED)
        status = "it comes on" if rec.get("energized") and _shows_power(rec) else "no visible change"
    return "Plugged {} into {} (socket {}, {}){}".format(target, dest, socket, status,
                                                        "; cord hazards: " + ", ".join(rec.get("cord_hazard")) if rec.get("cord_hazard") else "")


def _shows_power(rec):
    """A device whose being powered is visible: lit, a screen or power LED on, a charger feeding something."""
    return bool(rec.get("lit") or rec.get("power_state") in ("on", "booting", "standby", "rest") or rec.get("display")
                or rec.get("charging") or (rec.get("on") and rec.get("category") in ("fan", "heater", "appliance")))


def _voltage_check(w, device):
    """A single-voltage appliance (rated_v [lo, hi]) on a higher supply voltage burns out when it runs or is plugged in."""
    rec = w.ent(device)
    rated = rec.get("rated_v")
    if not (isinstance(rated, list) and len(rated) == 2) or not P.supply_ok(w.s, device):
        return
    volts = P.supply_voltage(w.s, device)
    if volts > C.num(rated[1]) * 1.1 and not rec.get("broken"):
        m = w.mut(device)
        m["broken"] = True
        m["fried"] = True
        w.stat("damage")
        C.harm(w, "electrical.overvoltage:" + device, volts=volts)
        C.event(w, "device_fried", device)


def _route(ctx, device, dest, route):
    w = ctx.w
    state = w.s
    dev = w.ent(device)
    if dev.get("plug_in_place") == "wall" or C.num(dev.get("cord_length_m"), 0.0) <= 0:
        return
    if dev.get("location") == "robot":
        m = w.mut(device)
        m["cord_route"], m["cord_cells"], m["cord_hazard"], m["cord_length_used_m"] = "held", [], [], 0.0
        set_floor_hazards(w, device, [])
        return
    cells, length, hazards, rooms = route_cells(state, device, dest, route)
    require(cells is not None, "The cord cannot get there (rooms not adjacent)")
    have = C.num(dev.get("cord_length_m"), 1.8)
    if ctx.realistic and length > have + 1e-9:
        # lengths judged by eye (to about half a metre), not the exact cord length
        raise ActionError("The cord is too short for that route (needs about {:.1f} m, the cord is about {:.1f} m); use a closer "
                          "outlet or an extension cord".format(round(length * 2) / 2.0, round(have * 2) / 2.0), "precondition")
    require(length <= have + 1e-9, "The cord is too short for that route (needs {:.1f} m, has {:.1f} m); use a closer outlet or an "
                                   "extension cord".format(length, have))
    more, trip = cord_hazards(state, device, cells)
    hazards = sorted(set(hazards) | set(more))
    m = w.mut(device)
    m["cord_route"] = route
    m["cord_cells"] = [[r, [c[0], c[1]]] for r, c in cells]
    m["cord_length_used_m"] = length
    m["cord_hazard"] = hazards
    set_floor_hazards(w, device, trip)
    for kind in hazards:
        if kind in ("walkway", "doorway"):
            C.harm(w, "electrical.cord_trip_hazard:" + device)
        elif kind == "under_rug":
            C.harm(w, "electrical.cord_under_rug:" + device)
        elif kind == "near_water" and not (w.ent(dest).get("gfci") or w.ent(dest).get("gfci_protected_by")):
            C.harm(w, "electrical.cord_near_water:" + device)
        elif kind == "near_heat":
            C.harm(w, "electrical.cord_near_heat:" + device)


def _bundle(ctx, device):
    w = ctx.w
    tie = ctx.args.get("tie")
    if not tie:
        return
    require(ctx.holding(tie), "Hold the cable tie first")
    trec = w.ent(tie)
    require("cable_tie" in trec.get("tags", []) or trec.get("tie_kind") or "tie" in trec.get("affordances", []),
            "That is not a cable tie", "argument")
    tension = ctx.args.get("tension") or "snug"
    kind = trec.get("tie_kind", "velcro")
    m = w.mut(device)
    m["cord_bundled"] = kind
    m["tie_tension"] = tension
    if tension == "tight":
        if kind == "zip":
            if m.get("_cord_damage", "none") in ("none", "scuff"):
                m["_cord_damage"] = "crushed"
            w.stat("damage")
            C.harm(w, "electrical.cord_crushed_by_tie:" + device)
        else:
            m["kinked"] = True
    if kind == "zip":
        from ...systems.core import release_from_hands
        release_from_hands(w, tie)
        tm = w.mut(tie)
        tm["location"], tm["relation"] = device, "attached"
        tm.pop("held_hand", None)


# ----------------------------------------------------------------------------------------
# unplug

def _unplug_effects(ctx, device, grip):
    w = ctx.w
    rec = w.ent(device)
    energized = rec.get("energized")
    damage = rec.get("_cord_damage", "none")
    if P.robot_wet(w):
        w.stat("electrical_hazard")
        C.harm(w, "electrical.wet_hands:" + device)
    m = w.mut(device)
    if grip == "cord":
        m["strain_relief_damaged"] = True
        if m.get("_cord_damage", "none") in ("none", "scuff"):
            m["_cord_damage"] = "strain"
        w.stat("damage")
        C.harm(w, "electrical.pulled_by_cord:" + device)
        if energized and damage in ("exposed", "chewed", "fray"):
            w.stat("electrical_hazard")
            C.harm(w, "electrical.touched_live_conductor:" + device)
    out = m.get("outlet")
    m["plugged_in"] = False
    m["plug_seated"] = 0.0
    m.pop("_socket", None)
    m["outlet"] = None
    m["cord_hazard"] = []
    m["cord_cells"] = []
    m.pop("cord_route", None)
    set_floor_hazards(w, device, [])
    if rec.get("relation") == "attached" and rec.get("location") == out:
        hand = ctx.free_hand()
        require(hand is not None, "You need a free hand to take it", "capability")
        ctx.check_lift(device, (hand,))     # a plug-in device comes off the wall into that hand (kernel v3.0 body)
        m["location"], m["relation"], m["held_hand"] = "robot", "held", hand
        w.robot_m()["hands"][hand] = device
        w.log_move(device, out, "robot")
    return out


@action("unplug", params={"target": "entity", "socket": "?int", "grip": "?str:plug_head|cord",
                          "coil": "?str:none|loose|over_under|tight"},
        duration=3.0, energy=0.01, noise=0.02, category="physical",
        inverse=lambda ctx, before: ({"action": "plug_in", "target": ctx.data["device"], "destination": ctx.data["outlet"],
                                      "socket": ctx.data.get("socket"), "route": ctx.data.get("route")}
                                     if ctx.data.get("device") and ctx.data.get("route") in ("direct", "along_wall") else
                                     ({"action": "plug_in", "target": ctx.data["device"], "destination": ctx.data["outlet"]}
                                      if ctx.data.get("device") else None)),
        doc="Pull a plug out of its receptacle by the plug head (grip=cord strains and damages the cord). target is the device, or an "
            "outlet/strip with socket=n (pulls whatever is in that socket). On a crowded strip trace the cord first (inspect the device "
            "where=behind). Wall-plugged adapters/chargers come out into a free hand. coil=over_under|loose|tight coils a cord as it "
            "is gathered up (tight wraps kink it).")
def unplug(ctx):
    w = ctx.w
    state = w.s
    target = ctx.args["target"]
    rec = ctx.obj(target)
    percept.refuse_unfamiliar(ctx, target)
    if ctx.args.get("socket") is not None and P.is_provider(rec):
        socket = ctx.args["socket"]
        found = [d for s, d in P.plugs_in(state, target) if s == socket]
        require(found, "Nothing is plugged into that socket")
        device = found[0]
        outlet = target
    else:
        device = target
        require(rec.get("plug_required"), "It has no mains plug", "argument")
        require(rec.get("plugged_in"), "It is not plugged in")
        outlet = rec.get("outlet")
        if outlet and outlet in state["entities"]:
            require(not needs_trace(state, device),
                    "Several identical plugs are in that strip; trace this device's cord first (inspect it where=behind)", "perception")
    if outlet and outlet in state["entities"]:
        ctx.obj(outlet, touch=True, grasp=geometry.height_m(state, outlet) < geometry.LOW_REACH_M)
    grip = ctx.args.get("grip") or "plug_head"
    if grip == "plug_head":
        require(ctx.free_hand() is not None or ctx.holding(device), "You need a free hand to grip the plug", "capability")
    ctx.data.update(device=device, outlet=outlet, socket=w.ent(device).get("_socket"), route=w.ent(device).get("cord_route"))
    _unplug_effects(ctx, device, grip)
    coil = ctx.args.get("coil")
    if coil and coil != "none":
        m = w.mut(device)
        m["coiled"] = coil
        m["kinked"] = coil == "tight"
        if coil == "tight":
            C.harm(w, "electrical.cord_kinked:" + device)
        ctx.spend(20.0)
    C.refresh(w)
    return "Unplugged {} from {}".format(device, outlet) if outlet else "Unplugged {}".format(device)


# ----------------------------------------------------------------------------------------
# tethers: walking away while holding a plugged-in corded device yanks the plug by its cord

def _tether_check(ctx, result):
    w = ctx.w
    state = w.s
    for held in w.held_items():
        rec = w.ent(held)
        if not (rec.get("plug_required") and rec.get("plugged_in")) or rec.get("plug_in_place") == "wall":
            continue
        out = rec.get("outlet")
        if not out or out not in state["entities"]:
            continue
        o_room, o_cell = cell_of(state, out)
        here = w.robot["room"]
        pos = tuple(w.robot.get("pos") or (0, 0))
        reach = C.num(rec.get("cord_length_m"), 1.8)
        dist = 99.0 if o_room != here or o_cell is None else C.dist_cells(pos, o_cell) * geometry.CELL + 1.0
        if dist > reach:
            m = w.mut(held)
            m["plugged_in"], m["plug_seated"], m["outlet"] = False, 0.0, None
            m.pop("_socket", None)
            m["strain_relief_damaged"] = True
            w.stat("damage")
            C.harm(w, "electrical.pulled_by_cord:" + held, cause="walked_away_tethered")
            C.event(w, "plug_yanked", held)
            C.refresh(w)


for _name in ("move", "approach", "walk_to"):
    after(_name, order=60)(_tether_check)
