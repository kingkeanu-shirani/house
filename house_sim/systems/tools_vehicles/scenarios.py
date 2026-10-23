"""Scenario helpers (setup op `helper`, name tools_vehicles.<name>) that build complex task setups.

Every helper returns bindings (entity ids) and never assumes another domain is loaded: objects other
domains own (sinks, toilets, radiators, printers, fridges, knives) are built from core types with the
props this domain's mechanics read. Randomness comes only from the setup rng (ctx.rng).
"""
import copy

from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from . import common as C
from . import fasteners as F
from .furnish import car_pose, guarded_furniture


def _room(ctx, room, fallback=("garage", "basement", "utility_closet", "kitchen", "living_room")):
    rooms = ctx.config["rooms"]
    if room in rooms:
        return room
    for kind in ([room] if room else []) + list(fallback):
        found = ctx.placer.rooms(kind)
        if found:
            return found[0] if len(found) == 1 else ctx.rng.choice(found)
    raise SpecError("No suitable room for helper (wanted {})".format(room))


def _foreign(ctx, type_name, props):
    if ctx.reg.known(type_name):
        return type_name, dict(props)
    return "generic_item", dict(props)


def _surface(ctx, room, kinds=("workbench", "counter", "table", "desk", "shelf_unit", "dining_table", "dresser")):
    """First surface of the listed kinds in the room that the robot can walk up to (None if none)."""
    for kind in kinds:
        for found in ctx.placer.find(kind, room):
            if reachable(ctx, found):
                return found
    return None


def _put(ctx, room, type_name, near=None, eid=None, on=None, inside=None, shelf=None, **props):
    """Place a loose item for a scenario: in/on the given container/surface, else on the room's floor clear of every
    doorway's swing, else on/in a reachable surface or open shelving there (small closets: the whole floor is door
    clearance), else on any free reachable floor cell there, else on the floor of a nearby common room."""
    p = ctx.placer
    new = None
    if inside:
        new = p.inside(inside, type_name, eid=eid, shelf=shelf, **props)
    if new is None and on:
        new = p.on(on, type_name, eid=eid, **props)
    if new is None and room:
        new = floor_reachable(ctx, room, type_name, eid=eid, near=near, swing_ok=False, **props)
    if new is None and room:
        new = _stow(ctx, room, type_name, eid=eid, **props)
    if new is None and room:
        new = floor_reachable(ctx, room, type_name, eid=eid, near=near, **props)
    for other in FALLBACK_ROOMS:
        if new is not None:
            break
        for rid in p.rooms(other):
            if rid != room:
                new = floor_reachable(ctx, rid, type_name, eid=eid, **props)
                if new is not None:
                    break
    if new is None:
        raise SpecError("No room to place " + type_name)
    return new


STOW_KINDS = ("workbench", "shelf_unit", "closet_shelving", "counter", "table", "desk", "dresser", "bench", "side_table")


def _reach_shelf(rec):
    """Index of the shelf nearest waist height (0.9 m) that a standing robot reaches without a stool."""
    heights = rec.get("shelf_heights") or []
    if not heights:
        return None
    usable = [i for i, h in enumerate(heights) if float(h) <= 1.3] or [0]
    return min(usable, key=lambda i: (abs(float(heights[i]) - 0.9), i))


def _stow(ctx, room, type_name, eid=None, kinds=STOW_KINDS, **props):
    """On a reachable surface or in reachable open shelving (at a shelf within reach) of the room; None if none takes it."""
    p = ctx.placer
    for kind in kinds:
        for found in p.find(kind, room):
            rec = _cfg(ctx, found)
            if rec.get("accepts_items") is False or not reachable(ctx, found):
                continue
            new = None
            if rec.get("surface"):
                new = p.on(found, type_name, eid=eid, **props)
            elif rec.get("container") and not rec.get("openable"):
                new = p.inside(found, type_name, eid=eid, shelf=_reach_shelf(rec), **props)
            if new is not None:
                return new
    return None


FALLBACK_ROOMS = ("hallway", "living_room", "kitchen", "entryway", "garage", "basement", "dining_room")


class _Access:
    """Setup-time form of the kernel's keep_access rule, from geometry's public access API (access_targets,
    access_report, not_worse): occupying some cells must not separate doorways that were connected nor cut off
    anything that was accessible, and the new thing itself must be accessible. Two walking graphs: "free" (hazard
    cells impassable: nobody steps on anything) and "plain" (only path-blocking footprints). Loose items are judged on
    the free graph (they never block a plain route); path-blocking ones on both."""

    def __init__(self, state, room, ignore=(), level="all"):
        self.state, self.room = state, room
        static = set(geometry.blocked_cells(state, room, ignore=ignore, include_agents=False))
        hazards = {c for c, items in geometry.placement_hazards(state, room).items() if any(src not in ignore for _, src in items)}
        self.targets = geometry.access_targets(state, room, level=level, ignore=ignore)
        self.graphs = {"free": static | hazards, "plain": static}
        self.before = {name: geometry.access_report(state, room, cells, self.targets) for name, cells in self.graphs.items()}

    def allows(self, cells, blocking=False, full=True):
        """full: the free graph (and the plain one for a blocking item); full=False: only the plain graph, the kernel's
        fallback for a room whose hazard-free routes are already cut (loose items then always pass)."""
        cells = set(cells)
        graphs = (("free", "plain") if blocking else ("free",)) if full else (("plain",) if blocking else ())
        for name in graphs:
            targets = dict(self.targets)
            targets[("new",)] = (sorted(cells), 1, False)
            after = geometry.access_report(self.state, self.room, self.graphs[name] | cells, targets)
            if not geometry.not_worse(self.before[name], after, new_keys=[("new",)]):
                return False
        return True


def floor_reachable(ctx, room, type_name, eid=None, near=None, avoid_cells=(), keep_clear=(), swing_ok=True, walk_from=None,
                    walk_goals=None, walk_all=False, **props):
    """Lay an item on the floor at a free cell the robot can walk to from the doorway (whole footprint free), outside
    every doorway's swing clearance when possible (swing_ok=False: only there). avoid_cells: cells the footprint must
    not cover (e.g. a cabinet door's swing). keep_clear: ids of loose items the robot must still be able to walk up to
    without stepping over the new item or each other (staged loads). Every spot also follows the kernel v2.3
    keep_access rule (`_Access`): first one that cuts no hazard-free route, then (the kernel's fallback) one that cuts no
    plain route; a path-blocking item (a bike) never cuts a plain route. None when nothing qualifies."""
    p = ctx.placer
    record = ctx.reg.make(type_name, room, **props)
    new = eid or p.new_id(type_name)
    ctx.config["entities"][new] = record
    state = ctx.state()
    rooms = ctx.config["rooms"]
    cols, rows = geometry.grid_size(rooms[room])
    doors = [tuple(c["cells"][room]) for c in sorted(ctx.config.get("connections", {}).values(), key=lambda c: str(c.get("rooms")))
             if room in c.get("cells", {})]
    start = doors[0] if doors else (0, 0)
    blocked = set(geometry.blocked_cells(state, room, ignore=(new,)))
    used = set()
    for other in geometry.room_entities(state, room):
        if other != new and not ctx.config["entities"][other].get("ground_layer") and "connects" not in ctx.config["entities"][other]:
            used.update(geometry.footprint(ctx.config["entities"][other]))
    used.update(doors)
    used.update(c for c, items in geometry.placement_hazards(state, room).items() if any(src != new for _, src in items))
    used.update(tuple(c) for c in avoid_cells)
    anchor = tuple(near) if near else start
    blocking = geometry.blocks(record)
    access = None
    agents = tuple(state.get("people", {})) + tuple(state.get("pets", {}))
    staged = [k for k in keep_clear if k in ctx.config["entities"] and k != new]
    # first outside every doorway's swing/approach clearance (a door sweeping open knocks loose items over), then
    # anywhere free in a room too small for that
    swing = geometry.door_clearance(state, room)
    size = record.get("size_m") or [0.1, 0.1, 0.1]
    rot0 = int(record.get("rot", 0) or 0)
    rots = [rot0] + ([90 - rot0] if rot0 in (0, 90) and abs(float(size[0]) - float(size[1])) > 1e-6 else [])
    for full in (True, False):
        for avoid in ((used | swing, used) if swing_ok else (used | swing,)):
            for rot in rots:        # long items (bikes) also lie the other way along a narrow free strip
                record["rot"] = rot
                options = sorted(((geometry.chebyshev((x, y), anchor), y, x) for y in range(rows) for x in range(cols)
                                  if (x, y) not in blocked and (x, y) not in avoid))
                for _, y, x in options[:200 if (keep_clear or walk_from) else 80]:
                    record["pos"] = [x, y]
                    cells = geometry.footprint(record)
                    if any(c in blocked or c in avoid or not geometry.in_grid(rooms[room], c) for c in cells):
                        continue
                    goals = geometry.near_cells(cells, rooms[room]) - set(cells)
                    if not any(geometry.find_path(state, room, d, goals, ignore=agents) is not None for d in (doors or [start])):
                        continue
                    if (keep_clear or walk_from) and not _walkable_between(ctx, state, room, walk_from or doors or [start], staged + [new], agents,
                                                                           goals=walk_goals, require_all=walk_all):
                        continue
                    if full or blocking:
                        access = access or _Access(state, room, ignore=(new,))
                        if not access.allows(cells, blocking=blocking, full=full):
                            continue
                    return new
    del ctx.config["entities"][new]
    return None


def _walkable_between(ctx, state, room, starts, items, agents, goals=None, require_all=False):
    """From some doorway (require_all: from every one of `starts`), every one of `items` (loose floor items) can be
    walked up to without stepping on any loose floor item (the staged ones, bikes, wheelbarrows, boxes other domains
    left there), as a careful reference skill walks; with `goals` (cells beside the car), the robot can also walk from
    there to one of them, so carrying a load between the items and the car never crosses another item."""
    from .skills import _contact_cells
    contact = _contact_cells(state, room, rings=True)
    per_item = {item: _contact_cells(state, room, exclude=(item,), rings=True) for item in items}   # an approach stops in its own ring
    for start in starts:
        good = True
        for item in items:
            own = set(geometry.footprint(ctx.config["entities"][item]))
            blocked = sorted(per_item[item] - own)
            near = geometry.near_cells(sorted(own), state["rooms"][room]) - own - set(blocked)
            if geometry.find_path(state, room, tuple(start), near, ignore=agents, extra_blocked=[c for c in blocked if c != tuple(start)]) is None:
                good = False
                break
        if good and goals:
            near = set(tuple(g) for g in goals) - contact
            good = geometry.find_path(state, room, tuple(start), near, ignore=agents,
                                      extra_blocked=[c for c in sorted(contact) if c != tuple(start)]) is not None
        if good and not require_all:
            return True
        if not good and require_all:
            return False
    return bool(require_all and starts)


def _cfg(ctx, eid):
    return ctx.config["entities"][eid]


def reachable(ctx, eid):
    """True if the robot could walk from a doorway of the entity's room to within reach of it."""
    state = ctx.state()
    room, cells = geometry.anchor(dict(state, robot={"room": None, "hands": {}, "pos": None}), eid)
    if room is None or not cells:
        return room is not None
    rooms = ctx.config["rooms"]
    starts = [tuple(c["cells"][room]) for c in ctx.config.get("connections", {}).values() if room in c.get("cells", {})]
    if not starts:
        return True
    goals = geometry.near_cells(cells, rooms[room]) - set(cells)
    return geometry.find_path(state, room, starts[0], goals, ignore=tuple(state.get("people", {})) + tuple(state.get("pets", {}))) is not None


# =============================================================================================
# tools

TOOLSETS = {
    "basic": ["screwdriver_phillips_2", "screwdriver_flat_large", "claw_hammer", "adjustable_wrench", "pliers_slip_joint", "utility_knife"],
    "drivers": ["screwdriver_phillips_1", "screwdriver_phillips_2", "screwdriver_flat_small", "screwdriver_flat_large", "screwdriver_torx_t20",
                "screwdriver_pozidriv_2"],
    "wrenches": ["combination_wrench_10mm", "combination_wrench_13mm", "combination_wrench_1_2in", "adjustable_wrench"],
}


@scenario_helper("tools_vehicles.toolbox")
def toolbox(ctx, room=None, tools=None, toolset="basic", damaged=None, on=None, open=False, home=True, prefix=""):
    """A toolbox (on a bench/counter if one exists) holding tools; returns {toolbox, <type>: id, tools: [...]}."""
    room = _room(ctx, room)
    surface = on or _surface(ctx, room)
    box = _put(ctx, room, "toolbox", on=surface, open=bool(open), eid=ctx.placer.new_id(prefix + "toolbox") if prefix else None)
    out = {"toolbox": box, "tool_room": room, "tools": []}
    for type_name in tools or TOOLSETS.get(toolset, TOOLSETS["basic"]):
        props = {"home": box if home else None}
        if damaged and type_name in damaged:
            props["_damage"] = {"kind": damaged[type_name], "visible": "closely"}
        tid = ctx.placer.inside(box, type_name, **props)
        if tid is None:
            tid = _put(ctx, room, type_name, on=surface, **props)
        out[type_name] = tid
        out["tools"].append(tid)
    return out


@scenario_helper("tools_vehicles.power_drill")
def power_drill(ctx, room=None, inside=None, on=None, bit="drill_bit_wood_6mm", charge=0.85, var="drill"):
    """A cordless drill/driver with a charged 18 V pack and a bit installed."""
    room = _room(ctx, room) if not (inside or on) else room
    did = _put(ctx, room, "cordless_drill", inside=inside, on=on)
    bt, bp = _foreign(ctx, "tool_battery_pack", {"label": "18 V tool battery", "category": "battery", "part_type": "tool_pack_brand_a_18v",
                                                 "size_m": [0.11, 0.075, 0.07], "mass_kg": 0.6, "secret_fields": []})
    bp["charge"] = float(charge)
    pack = ctx.placer.make(bt, did, **bp)
    _cfg(ctx, pack).update(relation="part", slot="battery")
    _cfg(ctx, did)["slots"]["battery"]["item"] = pack
    b = ctx.placer.make(bit, did)
    _cfg(ctx, b).update(relation="part", slot="bit")
    _cfg(ctx, did)["slots"]["bit"]["item"] = b
    return {var: did, var + "_battery": pack, var + "_bit": b}


def _protruding(ctx, container):
    """Loose things in a container standing taller than its inner height (they would stop a drawer or lid closing)."""
    rec = _cfg(ctx, container)
    inner = rec.get("inner_size_m")
    if not inner:
        return []
    out = []
    for eid, r in sorted(ctx.config["entities"].items()):
        if r.get("location") == container and r.get("relation") == "in":
            size = [float(v) for v in (r.get("size_m") or [0.1, 0.1, 0.1])[:3]]
            h = min(size) if r.get("pack_orientation") == "flat" else size[2]
            if h > float(inner[2]) + 0.01:
                out.append(eid)
    return out


def _lay_flat(ctx, container):
    """A closed drawer holding a flashlight longer than the drawer is deep: it lies on its side in there. Record that
    (`pack_orientation: flat`, read by the packing rules that decide whether a lid/drawer closes) so the drawer the
    scenario opens can be closed again."""
    inner = _cfg(ctx, container).get("inner_size_m") or [0, 0, 0]
    for eid in _protruding(ctx, container):
        r = _cfg(ctx, eid)
        if min(float(v) for v in (r.get("size_m") or [0.1, 0.1, 0.1])[:3]) <= float(inner[2]) + 0.01:
            r["pack_orientation"] = "flat"


@scenario_helper("tools_vehicles.spare_tool")
def spare_tool(ctx, type, room=None, drawer_label="junk drawer", var="spare"):
    """A spare tool in a reachable drawer with that label (else a reachable counter/table, else the floor)."""
    room = _room(ctx, room, ("kitchen",))
    drawers = [d for d in ctx.placer.find("drawer", room) if _cfg(ctx, d).get("label") == drawer_label and reachable(ctx, d)]
    for drawer in sorted(drawers, key=lambda d: (bool(_protruding(ctx, d)), drawers.index(d))):
        tid = ctx.placer.inside(drawer, type, home=drawer)
        if tid:
            _lay_flat(ctx, drawer)
            return {var: tid, var + "_home": drawer}
    surface = _surface(ctx, room, ("counter", "table", "desk", "workbench"))
    tid = _put(ctx, room, type, on=surface)
    _cfg(ctx, tid)["home"] = surface or room
    return {var: tid, var + "_home": surface or room}


@scenario_helper("tools_vehicles.tool")
def tool(ctx, type, room=None, on=None, inside=None, props=None, var="tool"):
    room = _room(ctx, room) if not (on or inside) else room
    tid = _put(ctx, room, type, on=on, inside=inside, **(props or {}))
    return {var: tid}


# perception-centric staging (kernel v2.4 realistic perception): look-alike fastener piles, tools with damage that only a
# close look shows

SORT_KINDS = {
    "wood_screw_8x1.25in": ("wood", "#8 x 1-1/4 in wood screws"),
    "drywall_screw_6x1.25in": ("drywall", "#6 x 1-1/4 in drywall screws"),
    "machine_screw_m4x16": ("machine", "M4 x 16 machine screws"),
    "sheet_metal_screw_8x0.5in": ("sheet_metal", "#8 x 1/2 in sheet-metal screws"),
    "finishing_nail_1.5in": ("nail", "1-1/2 in finishing nails"),
}


BENCH_SURFACES = ("workbench", "table", "desk", "counter", "dining_table")


def _bench(ctx, room, kinds):
    """(room, surface) for a bench job: the given room, else the first room of `kinds` with a work surface (workbench,
    table, desk, counter) the robot can walk up to; small items laid out there are within a standing robot's reach
    (closet shelving is not a bench). SpecError if no such room exists."""
    rooms = ctx.config["rooms"]
    candidates = [room] if room in rooms else [r for k in ([room] if room else []) + list(kinds) for r in ctx.placer.rooms(k)]
    for rid in candidates:
        surface = _surface(ctx, rid, BENCH_SURFACES)
        if surface is not None:
            return rid, surface
    raise SpecError("No room with a reachable work surface (wanted {})".format(room or ", ".join(kinds)))


@scenario_helper("tools_vehicles.hardware_sort")
def hardware_sort(ctx, room=None, kinds=("wood_screw_8x1.25in", "drywall_screw_6x1.25in", "machine_screw_m4x16", "sheet_metal_screw_8x0.5in")):
    """A mixed-fastener sorting job on a bench: a hardware organizer whose compartments are labeled by kind (identical
    drawers: realistic perception resolves them only by reading the label) in shuffled order, and one pile per kind
    (identical-looking 'handful of small fasteners' until inspected) in shuffled order. Bindings: organizer, sort_room,
    sort_surface, lots, compartments, lot_<short>, comp_<short> (short = wood, drywall, machine, sheet_metal, nail)."""
    room, surface = _bench(ctx, room, ("garage", "basement", "office", "kitchen", "dining_room"))
    organizer = _put(ctx, room, "hardware_organizer", on=surface)
    host = _cfg(ctx, organizer).get("location") if _cfg(ctx, organizer).get("location") in ctx.config["entities"] else None
    out = {"organizer": organizer, "sort_room": room, "sort_surface": host, "lots": [], "compartments": []}
    order = [k for k in kinds if k in SORT_KINDS]
    ctx.rng.shuffle(order)
    for kind in order:
        short, text = SORT_KINDS[kind]
        comp = ctx.placer.make("organizer_compartment", organizer, label="compartment: " + text, holds_kind=kind, label_text=text)
        _cfg(ctx, comp)["relation"] = "in"
        out["comp_" + short] = comp
        out["compartments"].append(comp)
    piles = list(order)
    ctx.rng.shuffle(piles)
    for kind in piles:
        short, text = SORT_KINDS[kind]
        props = {"label": text, "_kind": kind, "count": ctx.rng.randint(6, 12), "appearance": "screws" if short != "nail" else "nails"}
        lot = _put(ctx, room, "fastener_lot", on=host or surface, **props)
        out["lot_" + short] = lot
        out["lots"].append(lot)
    return out


INSPECTION_TOOLS = (("claw_hammer", "loose_head"), ("screwdriver_phillips_2", "cracked_handle"), ("utility_knife", "chipped_blade"),
                    ("pliers_slip_joint", "rounded_jaws"), ("screwdriver_flat_large", "bent_tip"), ("adjustable_wrench", "rounded_jaws"))


@scenario_helper("tools_vehicles.tool_inspection")
def tool_inspection(ctx, room=None, damaged=2, dirty=True):
    """Tools laid out on a bench for a condition check: `damaged` of them carry hidden damage that only a close look shows
    (`_damage` visible closely: a loose hammer head looks fine at rest), one intact tool is merely greasy (dirty is not
    damaged), and an empty 'do not use' bin waits beside them. Which tools are damaged follows the seed. Bindings:
    tools, damaged (ids), intact (ids), dirty_tool, damaged_1.., quarantine_bin, inspect_room, bench, <type>."""
    room, bench = _bench(ctx, room, ("garage", "basement", "kitchen", "office", "dining_room"))
    picks = list(INSPECTION_TOOLS)
    ctx.rng.shuffle(picks)
    bad = {t for t, _ in picks[:int(damaged)]}
    out = {"inspect_room": room, "bench": bench, "tools": [], "damaged": [], "intact": []}
    host = bench
    for type_name, kind in INSPECTION_TOOLS:
        props = {}
        if type_name in bad:
            props["_damage"] = {"kind": kind, "visible": "closely"}
        tid = _put(ctx, room, type_name, on=host, **props)
        if host is None:
            host = _cfg(ctx, tid).get("location") if _cfg(ctx, tid).get("location") in ctx.config["entities"] else None
        out[type_name] = tid
        out["tools"].append(tid)
        (out["damaged"] if type_name in bad else out["intact"]).append(tid)
    for i, tid in enumerate(out["damaged"], 1):
        out["damaged_{}".format(i)] = tid
    if dirty and out["intact"]:
        greasy = out["intact"][int(ctx.rng.random() * len(out["intact"]))]
        _cfg(ctx, greasy)["soil"] = {"grease": 0.5, "dirt": 0.3}
        out["dirty_tool"] = greasy
    out["quarantine_bin"] = _put(ctx, room, "tool_bin", on=host, label="do-not-use bin", holds_category="quarantine",
                                 label_text="DO NOT USE - damaged tools")
    return out


# =============================================================================================
# loose screws on fixtures

def _fixture(ctx, room, label, size, height, **props):
    """A wall-mounted fixture on a perimeter cell the robot can walk up to (other domains furnish crowded rooms: a
    random wall cell may sit behind a water heater or a sofa); falls back to any wall cell."""
    p = ctx.placer
    category = props.pop("category", "fixture")
    eid = p.mount(room, "generic_item", label=label, size_m=size, height=height, portable=False, category=category, **props)
    rec = _cfg(ctx, eid)
    if not reachable(ctx, eid):
        gc, gr = geometry.grid_size(ctx.config["rooms"][room])
        perimeter = sorted({(x, 0) for x in range(gc)} | {(x, gr - 1) for x in range(gc)} | {(0, y) for y in range(gr)} | {(gc - 1, y) for y in range(gr)})
        ctx.rng.shuffle(perimeter)
        first = list(rec["pos"])
        for cell in perimeter:
            rec["pos"] = list(cell)
            if reachable(ctx, eid):
                break
        else:
            rec["pos"] = first
    return eid


@scenario_helper("tools_vehicles.loose_screws")
def loose_screws(ctx, room=None, fixtures=("cabinet_hinge", "switch_plate"), stripped=False, restricted=None):
    """Fixtures with loose accessible screws. fixtures from: cabinet_hinge, switch_plate, towel_bar, paper_holder, chair_bracket.
    stripped=True adds a screw whose hole is stripped (spins); restricted='electrical'|'gas' adds a fastener that must not be touched.
    Returns ids and the list `in_scope` of (entity, fastener) pairs as 'eid:fid' strings."""
    room = _room(ctx, room, ("kitchen", "bathroom", "living_room"))
    out = {"screw_room": room, "in_scope": [], "loose_ids": []}
    for kind in fixtures:
        if kind == "cabinet_hinge":
            cabs = [c for c in ctx.placer.find("cabinet", room) if _cfg(ctx, c).get("type") == "cabinet" and reachable(ctx, c)]
            if cabs:
                cab = cabs[0]
            else:
                cab = furniture_reachable(ctx, room, "cabinet", label="cabinet")
            if cab is None:
                raise SpecError("No cabinet for hinge screws")
            rec = _cfg(ctx, cab)
            specs = []
            for hinge in ("top", "bottom"):
                for i in (1, 2):
                    loose_now = hinge == "top"
                    specs.append({"id": "hinge_{}_{}".format(hinge, i), "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "hinge",
                                  "group": "hinge_" + hinge, "substrate": "particleboard", "torque_nm": 0.1 if loose_now else 1.2,
                                  "spot": "{} hinge screw {}".format(hinge, i), "access": "inside"})
            F.attach_fasteners(rec, specs)
            out["cabinet"] = cab
            for s in specs:
                out["in_scope"].append("{}:{}".format(cab, s["id"]))
                if s["torque_nm"] < 0.8:
                    out["loose_ids"].append("{}:{}".format(cab, s["id"]))
        elif kind == "switch_plate":
            sw = [s for s in ctx.placer.find("light_switch", room) if reachable(ctx, s)]
            if sw:
                plate = sw[0]
            else:
                plate = _fixture(ctx, room, "light switch plate", [0.08, 0.02, 0.12], 1.2, category="switch")
            rec = _cfg(ctx, plate)
            F.attach_fasteners(rec, [{"id": "plate_screw_1", "kind": "switch_plate_screw", "drive": "slotted", "size": "4.8mm", "role": "cover",
                                      "group": "plate", "substrate": "plastic", "torque_nm": 0.05, "spot": "top plate screw"},
                                     {"id": "plate_screw_2", "kind": "switch_plate_screw", "drive": "slotted", "size": "4.8mm", "role": "cover",
                                      "group": "plate", "substrate": "plastic", "torque_nm": 0.45, "spot": "bottom plate screw"}])
            out["switch_plate"] = plate
            out["in_scope"] += ["{}:plate_screw_1".format(plate), "{}:plate_screw_2".format(plate)]
            out["loose_ids"].append("{}:plate_screw_1".format(plate))
        elif kind == "towel_bar":
            bar = _fixture(ctx, room, "towel bar", [0.6, 0.08, 0.06], 1.1, category="towel_bar")
            F.attach_fasteners(_cfg(ctx, bar), [{"id": "set_screw_left", "kind": "set_screw", "drive": "hex", "size": "3mm", "role": "bracket",
                                                 "group": "towel_bar", "substrate": "metal", "torque_nm": 0.0, "spot": "left bracket set screw"},
                                                {"id": "set_screw_right", "kind": "set_screw", "drive": "hex", "size": "3mm", "role": "bracket",
                                                 "group": "towel_bar", "substrate": "metal", "torque_nm": 0.7, "spot": "right bracket set screw"}])
            out["towel_bar"] = bar
            out["in_scope"] += ["{}:set_screw_left".format(bar), "{}:set_screw_right".format(bar)]
            out["loose_ids"].append("{}:set_screw_left".format(bar))
        elif kind == "paper_holder":
            holder = _fixture(ctx, room, "toilet paper holder", [0.15, 0.08, 0.08], 0.65, category="fixture")
            F.attach_fasteners(_cfg(ctx, holder), [{"id": "mount_screw_1", "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "bracket",
                                                    "group": "holder", "substrate": "drywall_anchor", "torque_nm": 0.1, "spot": "left mounting screw"},
                                                   {"id": "mount_screw_2", "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "bracket",
                                                    "group": "holder", "substrate": "drywall_anchor", "torque_nm": 0.9, "spot": "right mounting screw"}])
            out["paper_holder"] = holder
            out["in_scope"] += ["{}:mount_screw_1".format(holder), "{}:mount_screw_2".format(holder)]
            out["loose_ids"].append("{}:mount_screw_1".format(holder))
    if stripped:
        target = out.get("cabinet") or out.get("towel_bar") or out.get("switch_plate")
        rec = _cfg(ctx, target)
        fid = sorted(f for f in rec["_fasteners"] if rec["_fasteners"][f]["torque_nm"] < 0.5)[0]
        rec["_fasteners"][fid]["stripped_hole"] = True
        rec["_fasteners"][fid]["torque_nm"] = 0.05
        out["stripped"] = target
        out["stripped_fastener"] = fid
        key = "{}:{}".format(target, fid)
        out["in_scope"] = [k for k in out["in_scope"] if k != key]
        out["loose_ids"] = [k for k in out["loose_ids"] if k != key]
    if restricted == "electrical":
        outlet = _fixture(ctx, room, "wall outlet", [0.07, 0.04, 0.12], 0.35, category="outlet", cover_open=False, circuit_live=True)
        F.attach_fasteners(_cfg(ctx, outlet), [{"id": "cover_screw", "kind": "switch_plate_screw", "drive": "slotted", "size": "4.8mm",
                                                "role": "cover", "group": "outlet_cover", "substrate": "plastic", "torque_nm": 0.45, "spot": "cover plate screw"},
                                               {"id": "terminal_screw", "kind": "terminal_screw", "drive": "slotted", "size": "4.8mm", "role": "terminal",
                                                "group": "outlet_terminal", "substrate": "metal", "torque_nm": 0.6, "access": "cover",
                                                "spot": "brass terminal screw inside the box (feels loose)"}])
        _cfg(ctx, outlet)["warning"] = "Inside: live terminals"
        out["restricted"] = outlet
        out["restricted_fastener"] = "terminal_screw"
    elif restricted == "gas":
        stove = _fixture(ctx, room, "gas range connector", [0.3, 0.1, 0.1], 0.3, category="gas_fitting")
        F.attach_fasteners(_cfg(ctx, stove), [{"id": "flare_nut", "kind": "gas_flare_nut", "drive": "hex_nut", "size": "5/8in", "role": "fitting",
                                               "group": "gas", "substrate": "metal", "torque_nm": 10.0, "spot": "gas line flare nut behind the range"}])
        out["restricted"] = stove
        out["restricted_fastener"] = "flare_nut"
    return out


# =============================================================================================
# wobbly furniture

@scenario_helper("tools_vehicles.wobbly_table")
def wobbly_table(ctx, room=None, cause="loose_bolts", table=None, clear=True):
    """A four-legged table with a hidden wobble cause: loose_bolts | short_leg | uneven_floor."""
    wanted = room
    room = _room(ctx, room, ("dining_room", "kitchen", "living_room"))
    if table is None:
        found = [t for t in ctx.placer.find("table", room) if _cfg(ctx, t).get("type") in ("table", "dining_table") and reachable(ctx, t)]
        table = found[0] if found else None
        for size in ([1.0, 0.6, 0.75], [0.8, 0.5, 0.75], [0.6, 0.6, 0.74]):
            if table is not None:
                break
            table = furniture_reachable(ctx, room, "table", wall=False, label="table" if size[0] > 0.7 else "small table", size_m=size)
        if table is None and wanted not in ctx.config["rooms"]:
            # crowded room: fall back to another common room with space for a table
            for kind in ("dining_room", "kitchen", "living_room") + FALLBACK_ROOMS:
                for other in ctx.placer.rooms(kind):
                    if other == room:
                        continue
                    table = furniture_reachable(ctx, other, "table", wall=False, label="table", size_m=[1.0, 0.6, 0.75])
                    if table is not None:
                        room = other
                        break
                if table is not None:
                    break
    if table is None:
        raise SpecError("No table for the wobble scenario")
    rec = _cfg(ctx, table)
    if clear:
        for eid in [e for e, r in ctx.config["entities"].items() if r.get("location") == table]:
            rec2 = ctx.config["entities"][eid]
            rec2["location"] = room
            rec2["relation"] = "on"
            rec2.pop("spos", None)
            rec2["pos"] = geometry.free_floor_cell(ctx.state(), room, near=rec.get("pos"), ignore=(eid,), record=rec2) or                 geometry.free_floor_cell(ctx.state(), room, near=rec.get("pos"), ignore=(eid,)) or list(rec.get("pos") or [0, 0])
    rec["_legs"] = {leg: {"len_mm": 0.0, "diam_mm": 38.0} for leg in F.LEG_KEYS}
    rec["slots"] = dict(rec.get("slots") or {})
    for leg in F.LEG_KEYS:
        rec["slots"]["pad_" + leg] = {"accepts": ["leg_pad"], "item": None, "access": "none"}
    specs = []
    for leg in F.LEG_KEYS:
        specs.append({"id": "leg_bolt_" + leg, "kind": "bolt_m8", "drive": "hex_nut", "size": "13mm", "role": "leg_bolt", "group": "leg_bolts",
                      "substrate": "metal", "torque_nm": 10.0, "spot": "{} leg hanger bolt".format(leg), "access": "underneath"})
    F.attach_fasteners(rec, specs, replace=True)
    out = {"table": table, "table_room": room, "table_room_label": str(ctx.config["rooms"][room].get("type", "kitchen")).replace("_", " "),
           "cause": cause, "short_leg": "fl", "loose": []}
    if cause == "loose_bolts":
        for leg in ctx.rng.sample(list(F.LEG_KEYS), 2):
            rec["_fasteners"]["leg_bolt_" + leg]["torque_nm"] = 0.5
        out["loose"] = sorted(f for f, s in rec["_fasteners"].items() if s["torque_nm"] < 1)
    elif cause == "short_leg":
        leg = ctx.rng.choice(list(F.LEG_KEYS))
        rec["_legs"][leg]["len_mm"] = -4.0
        out["short_leg"] = leg
    elif cause == "uneven_floor":
        cells = geometry.footprint(rec)
        xs, ys = sorted({c[0] for c in cells}), sorted({c[1] for c in cells})
        cell = (xs[-1], ys[-1])
        ctx.config["rooms"][room].setdefault("_floor_dips", {})["{},{}".format(cell[0], cell[1])] = 4.0
        out["dip_leg"] = "fr"
    _refresh_cfg(ctx, table)
    return out


def _robot_clear(ctx, room, cell):
    """The robot's cell is free and still joined to the room's doorways (plain walking graph)."""
    state = ctx.state()
    blocked = set(geometry.blocked_cells(state, room, include_agents=False))
    return tuple(cell) not in blocked and tuple(cell) in geometry.reachable_cells(state, room, blocked)


def furniture_reachable(ctx, room, type_name, wall=True, lights=False, **props):
    """Furniture through Placer.furniture (kernel v2.3: doorway groups, the dock and everything that was accessible stay
    accessible -- full protection first, then basic -- and the new piece itself is accessible), never standing on or
    walling off the robot when an earlier setup op already put it in this room. lights=True (cars) also keeps serviceable
    lights accessible and parks the way a car is driven in (furnish.guarded_furniture + furnish.car_pose: nose-in
    through the overhead garage door, along a driveway's long side). None when no spot qualifies (callers fall back to
    another room or `_park_spot`)."""
    p = ctx.placer
    robot = ctx.config.get("robot") or {}
    rcell = tuple(robot["pos"]) if robot.get("room") == room and robot.get("pos") else None
    pose = car_pose(ctx.state(), room, ctx.reg.make(type_name, room, **props)) if lights else (None, None)
    for _ in range(2):
        if lights:
            eid = guarded_furniture(p, room, type_name, wall=wall, anchors=pose[0], accept=pose[1], **props)
        else:
            eid = p.furniture(room, type_name, wall=wall, **props)
        if eid is None:
            return None
        if rcell is None or _robot_clear(ctx, room, rcell):
            return eid
        del ctx.config["entities"][eid]
    return None


def _refresh_cfg(ctx, eid):
    """Run the derived-state refresh on a setup config (no journal: plain dict world)."""
    from ...kernel.world import World
    state = ctx.state()
    w = World(dict(state, robot={"room": ctx.config.get("start_room"), "hands": {}, "pos": None}), ctx.reg)
    F.refresh(w, eid)


# =============================================================================================
# flat-pack furniture

KITS = {
    "side_table": {
        "label": "two-shelf side table", "tall": False, "final_size_m": [0.6, 0.35, 0.62],
        "slots": {"bottom": {"part_type": "st_bottom", "requires": []},
                  "side_left": {"part_type": "st_side", "requires": ["bottom"], "cams": 2, "hardware": {"cam_bolt": 2, "dowel": 2, "cam_lock": 2}},
                  "side_right": {"part_type": "st_side", "requires": ["bottom"], "cams": 2, "hardware": {"cam_bolt": 2, "dowel": 2, "cam_lock": 2}},
                  "top": {"part_type": "st_top", "requires": ["side_left", "side_right"], "needs_holes_inside": ["side_left", "side_right"], "cams": 4,
                          "hardware": {"cam_bolt": 4, "dowel": 4, "cam_lock": 4}},
                  "back": {"part_type": "st_back", "requires": ["top"], "back_panel": True, "hardware": {"nail": 10}, "want": "finished_in"}},
        "manual": ["bottom", "side_left", "side_right", "top", "back"],
        "parts": [("st_bottom", "bottom panel", [0.57, 0.33, 0.016], 3.0), ("st_side", "side panel", [0.62, 0.33, 0.016], 3.2),
                  ("st_side", "side panel", [0.62, 0.33, 0.016], 3.2), ("st_top", "top panel", [0.6, 0.35, 0.016], 3.4),
                  ("st_back", "back panel (hardboard)", [0.6, 0.6, 0.003], 1.2)],
        "hardware": {"cam_bolt": 8, "dowel": 9, "cam_lock": 8, "nail": 12},
        "final": {"accepts_items": True, "surface": True, "container": False, "surface_height_m": 0.62, "surface_size_m": [0.6, 0.35],
                  "category": "furniture", "label": "side table", "mass_kg": 14.0},
    },
    "bookcase": {
        "label": "tall bookcase (180 cm)", "tall": True, "final_size_m": [0.8, 0.3, 1.8],
        "slots": {"bottom": {"part_type": "bc_shelf_fixed", "requires": []},
                  "side_left": {"part_type": "bc_side", "requires": ["bottom"], "cams": 2, "hardware": {"cam_bolt": 2, "dowel": 2, "cam_lock": 2}},
                  "side_right": {"part_type": "bc_side", "requires": ["bottom"], "cams": 2, "hardware": {"cam_bolt": 2, "dowel": 2, "cam_lock": 2}},
                  "middle": {"part_type": "bc_shelf_fixed", "requires": ["side_left", "side_right"], "needs_holes_inside": ["side_left", "side_right"], "cams": 4,
                             "hardware": {"cam_bolt": 4, "dowel": 4, "cam_lock": 4}},
                  "top": {"part_type": "bc_top", "requires": ["middle"], "cams": 4, "hardware": {"cam_bolt": 4, "dowel": 4, "cam_lock": 4}},
                  "back": {"part_type": "bc_back", "requires": ["top"], "back_panel": True, "hardware": {"nail": 16}, "want": "finished_in"}},
        "manual": ["bottom", "side_left", "side_right", "middle", "top", "back", "anti_tip"],
        "parts": [("bc_shelf_fixed", "fixed shelf panel", [0.77, 0.28, 0.018], 4.5), ("bc_side", "side panel", [1.8, 0.3, 0.018], 9.5),
                  ("bc_side", "side panel", [1.8, 0.3, 0.018], 9.5), ("bc_shelf_fixed", "fixed shelf panel", [0.77, 0.28, 0.018], 4.5),
                  ("bc_top", "top panel", [0.8, 0.3, 0.018], 4.8), ("bc_back", "back panel (hardboard)", [0.8, 1.8, 0.003], 3.5)],
        "hardware": {"cam_bolt": 12, "dowel": 13, "cam_lock": 12, "nail": 18, "anti_tip_strap": 1},
        "final": {"accepts_items": True, "container": True, "open_shelving": True, "shelf_heights": [0.05, 0.6, 1.2], "inner_size_m": [0.76, 0.28, 0.5],
                  "capacity_l": 300.0, "category": "furniture", "label": "bookcase", "mass_kg": 36.0},
    },
}


@scenario_helper("tools_vehicles.flatpack")
def flatpack(ctx, room=None, kind="side_table", missing=None, children=None):
    """A flat-pack kit: carton with panels + hardware pack + manual, and the assembly spot. Tall kits get a wall
    (wall_behind with studs) for the anti-tip anchor. missing='cam_bolt' removes hardware (stop and report)."""
    spec = copy.deepcopy(KITS[kind])
    wanted = room
    kinds = ("living_room", "office", "bedroom", "garage")
    room = _room(ctx, room, kinds)
    p = ctx.placer
    asm_id = None
    # a room of the asked kind that has a free stretch of wall for the piece (a full living room: the next candidate)
    for cand in [room] + ([r for k in kinds + ("dining_room", "hallway", "basement") for r in p.rooms(k) if r != room]
                          if wanted not in ctx.config["rooms"] else []):
        asm_id = furniture_reachable(ctx, cand, "flatpack_assembly", wall=True, label=spec["label"] + " (unassembled)",
                                     size_m=list(spec["final_size_m"][:2]) + [0.05], rot=0)
        if asm_id is not None:
            room = cand
            break
    if asm_id is None:
        raise SpecError("No floor space to assemble")
    rec = _cfg(ctx, asm_id)
    slots = {}
    for name, info in spec["slots"].items():
        s = dict(info)
        s.setdefault("want", "finished_out")
        s["item"] = None
        s["orientation"] = None
        slots[name] = s
    # the carton lies near the assembly spot but not in front of it (the robot works from those cells) and clear of
    # every door's swing; in a crowded room the delivery waits in the hall
    front = sorted(geometry.near_cells(geometry.footprint(rec), ctx.config["rooms"][room]))
    box = floor_reachable(ctx, room, "flatpack_box", label=spec["label"] + " carton", near=rec["pos"], avoid_cells=front, swing_ok=False)
    if box is None:
        box = p.floor_item(room, "flatpack_box", label=spec["label"] + " carton", near=rec["pos"])
        if box is not None and set(geometry.footprint(_cfg(ctx, box))) & geometry.door_clearance(ctx.state(), room):
            del ctx.config["entities"][box]       # the kernel's last resort: only a door swing was free
            box = None
    if box is None:
        box = floor_reachable(ctx, room, "flatpack_box", label=spec["label"] + " carton", near=rec["pos"], swing_ok=False)
    for other in [r for k in ("hallway", "entryway", "living_room", "garage") for r in p.rooms(k) if r != room]:
        if box is not None:
            break
        box = floor_reachable(ctx, other, "flatpack_box", label=spec["label"] + " carton", swing_ok=False)
    if box is None:
        box = floor_reachable(ctx, room, "flatpack_box", label=spec["label"] + " carton", near=rec["pos"])
    if box is None:
        raise SpecError("No space for the carton")
    hardware = dict(spec["hardware"])
    if missing:
        hardware[missing] = max(0, hardware.get(missing, 0) - 3)
    pack = p.inside(box, "flatpack_hardware_pack", hardware_counts=hardware, label="hardware pack ({})".format(spec["label"]))
    manual_steps = []
    for slot in spec["manual"]:
        if slot == "anti_tip":
            manual_steps.append("Fix the anti-tip strap to the wall (requires drilling: ask the owner)")
            continue
        info = spec["slots"][slot]
        face = "finished face toward the inside of the case" if info.get("want") == "finished_in" else "finished face out"
        manual_steps.append("Attach the {} ({}){}".format(slot.replace("_", " "), face,
                                                          ", then turn its cam locks clockwise until snug" if info.get("cams") else ""))
    manual = p.inside(box, "assembly_manual", label="assembly instructions", steps=manual_steps,
                      text="Parts: " + ", ".join(sorted({x[1] for x in spec["parts"]})) + ". Hardware: " +
                      ", ".join("{} x{}".format(k.replace("_", " "), v) for k, v in sorted(spec["hardware"].items())) +
                      ". Hand-tighten cams; do not use a drill on cam locks.")
    parts = []
    for ptype, label, size, mass in spec["parts"]:
        pid = p.make("flatpack_panel", box, part_type=ptype, label=label, size_m=size, mass_kg=mass, finished_face="one_side",
                     two_handed=max(size[:2]) > 0.7)
        _cfg(ctx, pid)["relation"] = "in"
        parts.append(pid)
    a = {"kind": kind, "label": spec["label"], "state": "kit", "tall": spec["tall"], "final_size_m": spec["final_size_m"], "slots": slots,
         "manual": spec["manual"], "hardware_pack": pack, "square_err_mm": 0.0, "anchor": {"installed": False, "required": spec["tall"]},
         "final": spec["final"], "correct": False}
    rec["assembly"] = a
    rec["_fasteners"] = {}
    rec["fasteners"] = {}
    out = {"assembly": asm_id, "kit_box": box, "hardware_pack": pack, "manual": manual, "parts": parts, "asm_room": room}
    by_type = {}
    for pid in parts:
        by_type.setdefault(_cfg(ctx, pid)["part_type"], []).append(pid)
    for ptype, ids in by_type.items():
        for i, pid in enumerate(ids, 1):
            out["{}_{}".format(ptype, i)] = pid
    if spec["tall"]:
        wall = _wall(ctx, room, near=rec["pos"])
        rec["wall_behind"] = wall
        studs = _cfg(ctx, wall)["_studs_m"]
        rec["anchor_offset_m"] = studs[0]
        rec["anchor_height_m"] = 1.75
        out["wall"] = wall
        out["stud_m"] = studs[0]
    return out


def _wall(ctx, room, near=None, finish="painted", paint_age_days=400, material="drywall", label=None):
    p = ctx.placer
    cols, rows = geometry.grid_size(ctx.config["rooms"][room])
    cell = None
    if near is not None:
        cell = [near[0], 0] if near[1] <= rows // 2 else [near[0], rows - 1]
    # the paintable wall face above the 10 cm baseboard (kernel v2.5 treats a tall mount starting at the floor as a
    # floor-standing obstacle, so a face mounted at 0 m would wall off the perimeter cells and doorways in front of it)
    wall = p.mount(room, "repair_wall", cell=cell, label=label or "{} wall".format(ctx.config["rooms"][room].get("label", room).lower()),
                   size_m=[3.0, 0.1, 2.3], mount_height_m=0.1, blocks_path=False, mount_surface=True, finish=finish,
                   paint={"color": "swiss_coffee", "sheen": "eggshell"},
                   paint_age_days=paint_age_days, wall_material=material, holes=[], category="wall")
    offset = round(ctx.rng.uniform(0.05, 0.35), 3)
    rec = _cfg(ctx, wall)
    rec["_studs_m"] = [round(offset + 0.406 * i, 3) for i in range(7)]
    rec["_hidden_services"] = [{"kind": "wire", "offset_m": round(offset + 0.406 * 2 + 0.05, 3), "from_h": 0.25, "to_h": 1.25}]
    rec["wall_axis"] = "x" if rec["pos"][1] in (0, rows - 1) else "y"
    return wall


@scenario_helper("tools_vehicles.wall")
def wall(ctx, room=None, finish="painted", paint_age_days=400, material="drywall", holes=0, scuffs=False, var="wall"):
    room = _room(ctx, room, ("living_room", "hallway", "bedroom"))
    wid = _wall(ctx, room, finish=finish, paint_age_days=paint_age_days, material=material)
    rec = _cfg(ctx, wid)
    for i in range(int(holes)):
        d = ctx.rng.choice([3.0, 6.0, 10.0, 16.0])
        rec["holes"].append({"id": "h{}".format(i + 1), "offset_m": round(0.4 + 0.5 * i, 2), "height_m": round(ctx.rng.uniform(1.0, 1.6), 2),
                             "diameter_mm": d, "state": "open"})
    if scuffs:
        rec["soil"] = {"scuff": 0.4}
    return {var: wid, var + "_room": room}


# =============================================================================================
# decorations

@scenario_helper("tools_vehicles.decor")
def decor(ctx, room=None, item="wreath", mass_kg=1.2, fragile=False, approved=("adhesive_hook", "adhesive_strip"), finish="painted",
          paint_age_days=400, hooks=("adhesive_hook_small", "adhesive_hook_medium"), nails=True, rental=True, height_m=1.6):
    """A wall to decorate, the decoration, a hardware drawer with hooks (and nails + hammer if nails=True)."""
    room = _room(ctx, room, ("living_room", "entryway", "hallway"))
    wid = _wall(ctx, room, finish=finish, paint_age_days=paint_age_days)
    hh = ctx.config.setdefault("household", {})
    hh["approved_hardware"] = list(approved)
    if rental:
        rules = hh.setdefault("rules", [])
        if "no_wall_holes" not in rules:
            rules.append("no_wall_holes")
    surface = _surface(ctx, room, ("side_table", "coffee_table", "table", "bench", "dresser", "tv_stand", "shoe_rack"))
    tname, props = _foreign(ctx, item, {"label": item.replace("_", " "), "mass_kg": mass_kg, "fragile": fragile, "size_m": [0.45, 0.45, 0.1],
                                        "category": "decor", "hang_wire": True, "material": "fabric", "volume_l": 15.0})
    props.update(mass_kg=mass_kg, fragile=fragile)
    deco = _put(ctx, room, tname, on=surface, **props)
    tray = _put(ctx, room, "parts_tray", on=surface, label="hardware tray")
    out = {"wall": wid, "decor_room": room, "decoration": deco, "hardware_tray": tray}
    for h in hooks:
        hid = ctx.placer.inside(tray, h)
        if hid is None:
            hid = _put(ctx, room, h, on=surface)
        out[h] = hid
    if nails:
        out["nail_hook"] = ctx.placer.inside(tray, "picture_hook_nail") or _put(ctx, room, "picture_hook_nail", on=surface)
        out["hammer"] = _put(ctx, room, "claw_hammer", on=surface)
    out["level"] = None
    lt, lp = _foreign(ctx, "torpedo_level", {"label": "torpedo level", "tool_kind": "torpedo_level", "category": "measuring_tool",
                                             "size_m": [0.23, 0.03, 0.05], "mass_kg": 0.15, "accuracy_deg": 0.05})
    out["level"] = _put(ctx, room, lt, on=surface, **lp)
    out["spot_height_m"] = height_m
    out["spot_offset_m"] = round(ctx.rng.uniform(0.6, 1.8), 2)
    return out


# =============================================================================================
# cars

@scenario_helper("tools_vehicles.car")
def car(ctx, room=None, kind="car", locked=True, mats="out", seat=False, child_inside=False, kit=True, ev=False, fob_room=None,
        trunk_items=None, sun=False):
    """A parked car with trunk, footwells, mats (mats='out': driver+passenger mats on the garage/driveway floor; 'in': installed),
    optional child seat (+ sleeping child inside), emergency kit in the trunk, and the key fob on the entryway key hook."""
    wanted = room
    room = _room(ctx, room, ("garage", "driveway"))
    p = ctx.placer
    _remove_cars(ctx, room)
    type_name = "electric_car" if ev else ("suv" if kind == "suv" else "car")
    label = {"car": "sedan", "suv": "SUV", "electric_car": "electric car"}.get(type_name, "car")
    cid = furniture_reachable(ctx, room, type_name, wall=False, lights=True, label=label)
    if cid is None and wanted not in ctx.config["rooms"]:
        # a full garage: park on the driveway instead (the car_spot binding names where it is)
        for other in [r for k in ("garage", "driveway") for r in ctx.placer.rooms(k) if r != room]:
            _remove_cars(ctx, other)
            cid = furniture_reachable(ctx, other, type_name, wall=False, lights=True, label=label)
            if cid is not None:
                room = other
                break
    if cid is None:
        for other in [room] + [r for k in ("garage", "driveway") for r in ctx.placer.rooms(k) if r != room and wanted not in ctx.config["rooms"]]:
            _remove_cars(ctx, other)
            pose = car_pose(ctx.state(), other, ctx.reg.make(type_name, other))
            cid = _park_spot(ctx, other, type_name, accept=pose[1], label=label)
            if cid is not None:
                room = other
                break
    if cid is None:
        raise SpecError("No room to park a car in " + room)
    rec = _cfg(ctx, cid)
    rec["tires"] = {w_: {"kind": "tire", "pressure_bar": 2.4, "range_bar": [2.3, 2.5], "burst_bar": 9.0, "volume_l": 30.0, "valve": "schrader",
                         "label_text": "Door placard: 35 psi (240 kPa) cold"} for w_ in ("fl", "fr", "rl", "rr")}
    rec["home_room"] = room
    rec["glass_ice_mm"] = {"windshield": 0.0, "rear": 0.0, "sides": 0.0}
    rec["slots"] = dict(rec.get("slots") or {})
    rec["slots"]["wiper_driver"] = {"accepts": ["wiper_22in"], "item": None, "access": "none"}
    rec["slots"]["wiper_passenger"] = {"accepts": ["wiper_18in"], "item": None, "access": "none"}
    trunk = p.make("car_trunk", cid, label="{} trunk".format(rec["label"]))
    _cfg(ctx, trunk)["relation"] = "part"
    if type_name == "suv":
        tr = _cfg(ctx, trunk)
        tr.update(label="cargo area", inner_size_m=[1.0, 0.85, 0.75], capacity_l=640.0, floor_area_m2=0.85, sightline_h_m=0.45, z_m=0.75)
    rtype = ctx.config["rooms"][room].get("type")
    out = {"car": cid, "car_room": room, "trunk": trunk,
           "car_spot": "garage floor" if rtype == "garage" else str(rtype or "driveway").replace("_", " ")}
    for pos in ("driver", "passenger", "rear_left", "rear_right"):
        fw = p.make("car_footwell", cid, label="{} footwell".format(pos.replace("_", " ")), position=pos)
        _cfg(ctx, fw)["relation"] = "part"
        out["footwell_" + pos] = fw
    seat_b = p.make("generic_item", cid, label="rear seat", category="car_rear_seat", portable=False, accepts_items=True, surface=True,
                    container=False, surface_size_m=[1.3, 0.5], size_m=[1.3, 0.5, 0.45], z_m=0.45, mass_kg=0.0, cabin=True)
    _cfg(ctx, seat_b)["relation"] = "part"
    out["rear_seat"] = seat_b
    fob_room = fob_room or (ctx.placer.rooms("entryway") or [room])[0]
    hooks = ctx.placer.find("key_hook", fob_room)
    fob = None
    if hooks:
        fob = ctx.placer.hang(hooks[0], "car_key_fob", label="car key fob", opens=[cid])
    if fob is None:
        fob = _put(ctx, fob_room, "car_key_fob", on=_surface(ctx, fob_room, ("bench", "table", "counter", "side_table", "shoe_rack")))
    rec["key"] = fob
    rec["locked"] = bool(locked)
    out["fob"] = fob
    mats_ids = {}
    for pos in ("driver", "passenger"):
        if mats == "in":
            mid = p.make("car_floor_mat", out["footwell_" + pos], label="{} floor mat".format(pos), position=pos)
            m = _cfg(ctx, mid)
            m.update(relation="part", slot="mat")
            _cfg(ctx, out["footwell_" + pos])["slots"]["mat"]["item"] = mid
        else:
            mid = floor_reachable(ctx, room, "car_floor_mat", label="{} floor mat".format(pos), position=pos)
            if mid is None:
                raise SpecError("No floor space for the mats")
        mats_ids[pos] = mid
        out["mat_" + pos] = mid
    if mats == "old_in":
        old = p.make("car_floor_mat", out["footwell_driver"], label="old carpet mat (driver)", position="driver")
        _cfg(ctx, old).update(relation="part", slot="mat")
        _cfg(ctx, out["footwell_driver"])["slots"]["mat"]["item"] = old
        out["old_mat"] = old
    if seat:
        sid = p.make("child_car_seat", cid, label="rear-facing child seat")
        _cfg(ctx, sid)["relation"] = "part"
        out["car_seat"] = sid
    if kit:
        kid = p.inside(trunk, "car_emergency_kit", kit_checklist=["jumper cables", "flashlight", "reflective triangle", "first-aid kit",
                                                                 "emergency blanket", "water", "gloves"], home=trunk, cargo_pocket=True)
        out["kit"] = kid
        if kid:
            for t_ in ("jumper_cables", "reflective_triangle", "emergency_blanket", "kit_water_bottle", "work_gloves"):
                iid = p.inside(kid, t_, kit_item={"jumper_cables": "jumper cables"}.get(t_))
                out["kit_" + t_] = iid
            fl = p.inside(kid, "flashlight", label="kit flashlight", kit_item="flashlight", secret_fields=["battery_level"])
            out["kit_flashlight"] = fl
    if child_inside:
        people = ctx.config.setdefault("people", {})
        pid = "child_in_car"
        people[pid] = {"label": "toddler", "role": "child", "age": 2, "authority": 0, "location": room, "pos": list(rec["pos"]),
                       "posture": "sleeping", "awake": False, "consent": True, "in_vehicle": cid, "permissions": [], "preferences": {}}
        if out.get("car_seat"):
            _cfg(ctx, out["car_seat"])["occupant"] = pid
        out["child"] = pid
    if ev:
        chg = p.mount(room, "ev_charger", height=1.2, label="EV wall charger", cell=_reachable_wall_cell(ctx, room, near=cid))
        conn = p.make("ev_connector", chg, tethered_to=chg, label="charging connector")
        _cfg(ctx, conn)["relation"] = "on"
        _cfg(ctx, chg)["connector"] = conn
        out["charger"] = chg
        out["connector"] = conn
    return out


def _reachable_wall_cell(ctx, room, near=None):
    """A perimeter cell that is not blocked and has a free neighbour (closest to `near`'s footprint)."""
    state = ctx.state()
    cols, rows = geometry.grid_size(ctx.config["rooms"][room])
    blocked = set(geometry.blocked_cells(state, room))
    doors = {tuple(c["cells"][room]) for c in ctx.config.get("connections", {}).values() if room in c.get("cells", {})}
    perimeter = sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} | {(0, y) for y in range(rows)} | {(cols - 1, y) for y in range(rows)})
    target = geometry.footprint(ctx.config["entities"][near]) if near else []
    options = []
    for cell in perimeter:
        if cell in blocked or cell in doors:
            continue
        free = [n for n in geometry.near_cells([cell], ctx.config["rooms"][room]) if n != cell and n not in blocked]
        if not free:
            continue
        d = min(geometry.chebyshev(cell, t) for t in target) if target else 0
        options.append((d, cell))
    if not options:
        return None
    options.sort()
    return list(options[0][1])


def _relocate(ctx, room, eid):
    """Move a portable floor item aside within the room: the nearest cell geometry.free_floor_cell allows under the
    kernel keep_access rule (outside door clearance, cutting no hazard-free route), else one that cuts no plain route."""
    rec = ctx.config["entities"][eid]
    origin = tuple(rec.get("pos") or (0, 0))
    state = ctx.state()
    cell = geometry.free_floor_cell(state, room, near=origin, ignore=(eid,), record=rec, keep_access=True) or \
        geometry.free_floor_cell(state, room, near=origin, ignore=(eid,), record=rec, keep_access="plain")
    if cell is None:
        return False
    rec["pos"] = list(cell)
    return True


def _rolls_aside(rec):
    """A wheeled bin (trash, recycling, organics or yard-waste cart): not portable, it blocks paths, yet a family wheels
    it a few steps aside to park the car (review H10: on held-out seed 501 the only car spot on the driveway was walled
    off by the carts, and the car helper raised 'No room to park a car')."""
    return bool(rec.get("wheeled") and rec.get("movable") and not rec.get("anchored") and not rec.get("mounted"))


def _park_spot(ctx, room, type_name, accept=None, **props):
    """Fallback when the placer finds no free spot for a car: the spot whose footprint holds the fewest portable floor
    items (bikes, mowers, boxes... moved aside within the room) or wheeled bins (_rolls_aside) while doorways stay
    connected and clear. accept(record): pose rule for the spot (a car in line with the garage door, furnish.car_pose)."""
    p = ctx.placer
    ents = ctx.config["entities"]
    record = ctx.reg.make(type_name, room, **props)
    state = ctx.state()
    grid = ctx.config["rooms"][room]
    gc, gr = geometry.grid_size(grid)
    rollers = tuple(e for e in geometry.room_entities(state, room) if _rolls_aside(ents[e]) and ents[e].get("pos") is not None)
    fixed = set(geometry.blocked_cells(state, room, ignore=rollers, include_agents=True))
    movable = {}
    for eid in geometry.room_entities(state, room):
        rec = ents[eid]
        if rec.get("ground_layer") or "connects" in rec or rec.get("mounted") or rec.get("pos") is None:
            continue
        if (rec.get("portable") and not rec.get("blocks_path")) or eid in rollers:
            for c in geometry.footprint(rec):
                movable.setdefault(c, []).append(eid)
        elif float((rec.get("size_m") or [0, 0, 0])[2]) > 0.05 or rec.get("blocks_path"):
            fixed.update(geometry.footprint(rec))
    fixed |= geometry.door_clearance(state, room)      # doorways, their neighbours, leaves and overhead thresholds
    options = []
    for rot in (0, 90):
        record["rot"] = rot
        cols, rows = geometry.footprint_size(record)
        for y in range(gr - rows + 1):
            for x in range(gc - cols + 1):
                cells = {(x + i, y + j) for i in range(cols) for j in range(rows)}
                if cells & fixed or (accept is not None and not accept(dict(record, rot=rot, pos=[x, y]))):
                    continue
                moved = sorted({e for c in cells for e in movable.get(c, [])})
                options.append((len(moved), y, x, rot, moved))
    for n, y, x, rot, moved in sorted(options)[:12]:
        record["rot"] = rot
        cells = geometry.footprint(dict(record, pos=[x, y]))
        shift, check = list(moved), False
        # the kernel's basic furniture rule with the items about to be moved aside ignored
        if not _Access(state, room, ignore=tuple(moved), level="basic").allows(cells, blocking=True, full=False):
            # the bins standing elsewhere may be what walls a doorway off once the car is in (held-out seed 126: the
            # car along a 3 m driveway, a yard-waste cart across the strip left beside it): wheel them aside too
            others = [r for r in rollers if r not in moved]
            if not others or not _Access(state, room, ignore=tuple(moved) + tuple(others), level="basic").allows(
                    cells, blocking=True, full=False):
                continue
            shift, check = shift + others, True
        saved = {m: list(ents[m]["pos"]) for m in shift}
        cid = p.new_id(type_name)
        ents[cid] = dict(record, pos=[x, y])
        if all(_relocate(ctx, room, m) for m in shift) and reachable(ctx, cid) and (not check or _doors_connected(ctx, room)):
            return cid
        del ents[cid]
        for m, pos in saved.items():
            ents[m]["pos"] = pos
    return None


def _doors_connected(ctx, room):
    """Every doorway of `room` can still be walked to from the first one (people and pets aside)."""
    state = ctx.state()
    doors = [tuple(c["cells"][room]) for _, c in sorted(ctx.config.get("connections", {}).items()) if room in c.get("cells", {})]
    ignore = tuple(state.get("people", {})) + tuple(state.get("pets", {}))
    return all(geometry.find_path(state, room, doors[0], {d}, ignore=ignore) is not None for d in doors[1:])


def _remove_cars(ctx, room):
    ents = ctx.config["entities"]
    cars = [e for e, r in ents.items() if r.get("vehicle_kind") in ("car", "ev") and r.get("location") == room]
    for car in cars:
        doomed = [car]
        changed = True
        while changed:
            changed = False
            for e, r in ents.items():
                if r.get("location") in doomed and e not in doomed:
                    doomed.append(e)
                    changed = True
        for e, r in list(ents.items()):
            if r.get("type") == "car_key_fob" and car in (r.get("opens") or []):
                doomed.append(e)
        for e in doomed:
            ents.pop(e, None)


def _beside_car(ctx, room):
    """A free floor cell beside the car parked in `room`, on the side nearest the doorway from the house (where a
    family stages bags "by the car"); None without a car."""
    ents = ctx.config["entities"]
    cars = sorted(e for e, r in ents.items() if r.get("location") == room and r.get("vehicle_kind") and r.get("pos"))
    if not cars:
        return None
    state = ctx.state()
    rooms = ctx.config["rooms"]
    cells = set(geometry.footprint(ents[cars[0]]))
    blocked = set(geometry.blocked_cells(state, room))
    side = sorted(c for c in geometry.near_cells(sorted(cells), rooms[room]) - cells if c not in blocked)
    doors = []
    for _, conn in sorted(ctx.config.get("connections", {}).items()):
        if room in conn.get("cells", {}):
            other = next((x for x in conn.get("rooms", []) if x != room), None)
            indoor = other in rooms and not rooms[other].get("outdoor")
            doors.append((0 if indoor else 1, tuple(conn["cells"][room])))
    if not side or not doors:
        return list(side[0]) if side else None
    door = sorted(doors)[0][1]
    return list(min(side, key=lambda c: (geometry.chebyshev(c, door), c[1], c[0])))


LOAD_ITEMS = [
    ("suitcase", {"label": "large suitcase", "mass_kg": 18.0, "size_m": [0.75, 0.5, 0.3], "category": "luggage", "two_handed": True, "volume_l": 110.0}),
    ("cooler", {"label": "cooler", "mass_kg": 9.0, "size_m": [0.55, 0.35, 0.38], "category": "cooler", "volume_l": 70.0}),
    ("duffel", {"label": "duffel bag", "mass_kg": 6.0, "size_m": [0.6, 0.3, 0.3], "category": "luggage", "volume_l": 50.0, "soft_sided": True}),
    ("cake_box", {"label": "cake box", "mass_kg": 1.5, "size_m": [0.3, 0.3, 0.15], "category": "food_box", "fragile": True, "crush_kg": 1.0,
                  "volume_l": 13.0}),
]


def _stage(ctx, room, items, strict=False):
    """Stage `items` on the floor of `room` (see load_items); with strict, only spots clear of every door's swing. Returns
    the bindings, or None (nothing left behind) when an item does not fit."""
    out = {"staged": []}
    near = _beside_car(ctx, room)
    rooms = ctx.config["rooms"]
    doors, indoor = [], []
    for _, conn in sorted(ctx.config.get("connections", {}).items()):
        if room in conn.get("cells", {}):
            cell = tuple(conn["cells"][room])
            doors.append(cell)
            other = next((x for x in conn.get("rooms", []) if x != room), None)
            if other in rooms and not rooms[other].get("outdoor"):
                indoor.append(cell)
    ents = ctx.config["entities"]
    cars = sorted(e for e, r in ents.items() if r.get("location") == room and r.get("vehicle_kind") and r.get("pos"))
    car_side = None
    if cars:
        foot = set(geometry.footprint(ents[cars[0]]))
        car_side = sorted(geometry.near_cells(sorted(foot), rooms[room]) - foot)
    # each staged item stays reachable without stepping over the others or anything else on the floor (a careful loader
    # never treads on the cake): from the door the family comes out of the house by, and from there to the car, so
    # the trips between the items and the trunk never cross another load; weaker conditions only when that is impossible
    modes = [dict(walk_from=indoor or doors, walk_goals=car_side, walk_all=True, swing_ok=False),
             dict(walk_from=indoor or doors, walk_goals=car_side, walk_all=True),
             dict(walk_from=indoor or doors, walk_goals=car_side),
             dict(swing_ok=False), dict()]
    if strict:
        modes = [m for m in modes if m.get("swing_ok") is False]
    for name, props in items:
        iid = None
        for mode in modes:
            for keep in ((out["staged"],) if mode.get("walk_from") else (out["staged"], ())):
                if iid is None:
                    iid = floor_reachable(ctx, room, "generic_item", near=near if (keep or mode.get("walk_from")) else None, keep_clear=keep,
                                          label=props.get("label", name), **dict(mode, **{k: v for k, v in props.items() if k != "label"}))
        if iid is None:
            for eid in out["staged"]:
                ents.pop(eid, None)
            return None
        out[name] = iid
        out["staged"].append(iid)
    return out


@scenario_helper("tools_vehicles.load_items")
def load_items(ctx, room=None, items=None):
    """Staged items for loading a car: default suitcase (18 kg), cooler (9 kg), duffel and a cake box (fragile), laid
    out by the car. A room kind (not an id) may fall back to another room of the listed kinds where the load stays clear
    of every door's swing."""
    wanted = room
    kinds = ("garage", "driveway", "entryway")
    room = _room(ctx, room, kinds)
    items = items or LOAD_ITEMS
    if wanted not in ctx.config["rooms"]:
        for cand in [room] + [r for k in kinds + ("mudroom", "hallway") for r in ctx.placer.rooms(k) if r != room]:
            out = _stage(ctx, cand, items, strict=True)
            if out is not None:
                out["load_room"] = cand
                return out
    out = _stage(ctx, room, items)
    if out is None:
        raise SpecError("No floor space to stage the load")
    out["load_room"] = room
    return out


# =============================================================================================
# bikes and balls

BIKE_PRESETS = {"road": {"range_bar": [5.5, 7.0], "burst_bar": 11.0, "volume_l": 0.6, "valve": "presta", "label": "Inflate to 80-100 PSI (5.5-7.0 bar)"},
                "hybrid": {"range_bar": [3.5, 4.8], "burst_bar": 8.0, "volume_l": 1.6, "valve": "schrader", "label": "Inflate to 50-70 PSI (3.5-4.8 bar)"},
                "kids": {"range_bar": [2.1, 3.1], "burst_bar": 5.0, "volume_l": 1.3, "valve": "schrader", "label": "Inflate to 30-45 PSI (2.1-3.1 bar)"}}


@scenario_helper("tools_vehicles.bike")
def bike(ctx, room=None, preset="hybrid", start_bar=1.0, leak=None, pump_head="schrader", gauge=True, compressor=False, kind="bicycle"):
    """A bike with two tires at start_bar; leak='front'|'rear' gives that tube a puncture (6 bar/h). A floor pump (head setting
    pump_head) and a tire gauge on the workbench; compressor=True adds a portable compressor."""
    wanted = room
    kinds = ("garage", "shed", "basement", "porch", "balcony", "entryway", "mudroom", "hallway", "living_room")
    room = _room(ctx, room, kinds)
    p = ctx.placer
    spec = BIKE_PRESETS[preset]
    btype = "kids_bicycle" if preset == "kids" else kind
    bid = floor_reachable(ctx, room, btype, label="{} bike".format(preset), swing_ok=False)
    if bid is None and wanted not in ctx.config["rooms"]:
        # a full garage: the bike stands wherever else the household keeps one
        for other in [r for k in kinds for r in p.rooms(k) if r != room]:
            bid = floor_reachable(ctx, other, btype, label="{} bike".format(preset), swing_ok=False)
            if bid is not None:
                room = other
                break
    if bid is None:
        bid = floor_reachable(ctx, room, btype, label="{} bike".format(preset))
    if bid is None:
        raise SpecError("No space for the bike")
    rec = _cfg(ctx, bid)
    wheels = {}
    specs = []
    for wheel in ("front", "rear"):
        node = {"kind": "tube", "pressure_bar": float(start_bar), "range_bar": list(spec["range_bar"]), "burst_bar": spec["burst_bar"],
                "volume_l": spec["volume_l"], "valve": spec["valve"], "label_text": spec["label"], "_leak_bar_h": 0.0, "_puncture": None}
        if leak == wheel:
            node["_leak_bar_h"] = 6.0
            node["_puncture"] = {"location": "inner side of the tube near the valve"}
        wheels[wheel] = node
        specs.append({"id": wheel + "_cap", "kind": "valve_cap", "drive": "hand", "size": "1", "role": "cap", "group": wheel + "_valve",
                      "substrate": "plastic", "torque_nm": 0.2, "snug_nm": [0.1, 0.5], "strip_nm": 2.0, "spot": wheel + " valve cap"})
        if spec["valve"] == "presta":
            specs.append({"id": wheel + "_valve_nut", "kind": "valve_nut", "drive": "hand", "size": "1", "role": "valve", "group": wheel + "_valve",
                          "substrate": "metal", "torque_nm": 0.2, "snug_nm": [0.1, 0.5], "strip_nm": 3.0, "spot": wheel + " Presta lock nut"})
    rec["wheels"] = wheels
    F.attach_fasteners(rec, specs, replace=True)
    for f in rec["_fasteners"].values():
        f["hand_ok"] = True
    surface = _surface(ctx, room)
    pump = _put(ctx, room, "floor_pump", on=None, near=rec["pos"], settings={"head": pump_head})
    out = {"bike": bid, "bike_room": room, "pump": pump, "valve": spec["valve"], "range_lo": spec["range_bar"][0], "range_hi": spec["range_bar"][1],
           "target_bar": round((spec["range_bar"][0] + spec["range_bar"][1]) / 2.0, 2)}
    if gauge:
        out["gauge"] = _put(ctx, room, "tire_gauge", on=surface)
    if compressor:
        out["compressor"] = _put(ctx, room, "air_compressor", on=surface)
    return out


@scenario_helper("tools_vehicles.ball")
def ball(ctx, room=None, kind="soccer", start_bar=0.2, leak=False):
    room = _room(ctx, room, ("garage", "playroom", "living_room", "backyard"))
    specs = {"soccer": ("soccer_ball", {"range_bar": [0.6, 1.1], "burst_bar": 2.5, "volume_l": 5.6, "valve": "needle"}),
             "basketball": ("basketball", {"range_bar": [0.52, 0.59], "burst_bar": 2.0, "volume_l": 7.1, "valve": "needle"}),
             "beach": ("beach_ball", {"range_bar": [0.02, 0.05], "burst_bar": 0.15, "volume_l": 30.0, "valve": "plug"})}
    tname, infl = specs[kind]
    # this domain's inflatable_ball type (realistic perception hides its raw inflation record: pressure needs a gauge)
    props = {"label": "{} ball".format(kind), "size_m": [0.22, 0.22, 0.22] if kind != "beach" else [0.4, 0.4, 0.4], "mass_kg": 0.43,
             "category": "toy", "ball_kind": tname}
    infl = dict(infl, kind="ball", pressure_bar=float(start_bar), _leak_bar_h=1.5 if leak else 0.0, _puncture={"location": "seam"} if leak else None)
    props["inflation"] = infl
    bid = _put(ctx, room, "inflatable_ball", **props)
    pump = _put(ctx, room, "ball_pump")
    needle = ctx.placer.make("inflation_needle", pump, label="inflation needle")
    _cfg(ctx, needle).update(relation="part", slot="needle")
    _cfg(ctx, pump)["slots"]["needle"]["item"] = needle
    return {"ball": bid, "ball_pump": pump, "needle": needle, "ball_room": room}


# =============================================================================================
# plumbing-ish fixtures

def _fixture_spot(ctx, room, label, size, **props):
    """A floor-standing fixture (generic_item) that keeps the room walkable; in a full room (small apartment baths) the
    portable clutter on its spot (wastebasket, plunger, brush...) is moved aside within the room. None if neither works."""
    props.setdefault("blocks_path", True)
    eid = furniture_reachable(ctx, room, "generic_item", label=label, size_m=size, portable=False, **props)
    if eid is None:
        eid = _park_spot(ctx, room, "generic_item", label=label, size_m=size, portable=False, **props)
    return eid


def _refit_fixture(ctx, room, kind, label, size, **props):
    """The room's own fixed fixture of a category (the generator's toilet or vanity sink), rebuilt in place as this
    domain's fixture model (a generic_item with `props`): same id, location, cell, rotation, relation and mounting, so
    whatever names it keeps naming it and the room's layout does not change. A floor-standing one keeps its footprint
    (its own size); a mounted basin keeps its size. None when the room has none."""
    ents = ctx.config["entities"]
    found = sorted(e for e, r in ents.items() if r.get("location") == room and r.get("category") == kind
                   and not r.get("portable") and r.get("pos") is not None)
    if not found:
        return None
    eid = found[0]
    old = ents[eid]
    keep = {k: copy.deepcopy(old[k]) for k in ("pos", "rot", "relation", "mounted", "mount_height_m", "near_connection") if k in old}
    if old.get("size_m"):
        size = list(old["size_m"])
    if old.get("mounted"):
        props.pop("blocks_path", None)
    else:
        props.setdefault("blocks_path", True)
    rec = ctx.reg.make("generic_item", room, label=label, size_m=size, portable=False, **props)
    rec.update(keep)
    ents[eid] = rec
    return eid


@scenario_helper("tools_vehicles.undersink_filter")
def undersink_filter(ctx, room=None):
    """Kitchen sink cabinet with an under-sink filter sump (stop valve setting supply_valve), a filtered-water faucet
    on the sink (switchable; drains), the old cartridge installed and a new one plus a look-alike in the pantry/cabinet."""
    room = _room(ctx, room, ("kitchen",))
    p = ctx.placer
    sink_cab = furniture_reachable(ctx, room, "cabinet", label="sink base cabinet")
    if sink_cab is None:
        cabs = [c for c in p.find("cabinet", room) if reachable(ctx, c)]
        if not cabs:
            raise SpecError("No sink cabinet")
        sink_cab = cabs[0]
    sink = _cfg(ctx, sink_cab)
    sink.update(label="sink base cabinet", accepts_items=True)
    faucet = p.make("generic_item", sink_cab, label="filtered-water faucet", category="faucet", switchable=True, on=False, portable=False,
                    water_source=True, drain=True, flow_lpm=1.9, size_m=[0.05, 0.2, 0.3], mass_kg=0.8)
    _cfg(ctx, faucet)["relation"] = "attached"
    housing = p.make("generic_item", sink_cab, label="under-sink filter housing", category="filter_housing", portable=False, housing="sump",
                     size_m=[0.15, 0.15, 0.35], mass_kg=1.5, settings={"supply_valve": "open", "filter_indicator": "replace"},
                     setting_options={"supply_valve": ["open", "closed"], "filter_indicator": ["replace", "ok"]}, flush_outlet=faucet,
                     slots={"water_filter": {"accepts": ["undersink_10in"], "item": None, "access": "none", "water_filter": True}},
                     water_quality="clear", label_text="Shut off the cold supply and open the faucet to relieve pressure before opening.")
    _cfg(ctx, housing)["relation"] = "in"
    old = p.make("undersink_filter_cartridge", housing, label="old filter cartridge", used_l=3400.0, flushed_l=100.0)
    _cfg(ctx, old).update(relation="part", slot="water_filter")
    hrec = _cfg(ctx, housing)
    hrec["slots"]["water_filter"]["item"] = old
    F.attach_fasteners(hrec, [{"id": "water_filter", "kind": "filter_twist", "drive": "hand", "size": "1", "role": "filter", "substrate": "plastic",
                               "torque_nm": 2.0, "spot": "sump ring"}])
    # spare cartridges live in a base cabinet when there is one (opening a head-height cabinet door beside a resident
    # cooking at the counter swings it toward them); upper cabinets otherwise
    store = [c for c in p.find("cabinet", room) + p.find("upper_cabinet", room) if c != sink_cab and reachable(ctx, c)]
    new = alike = None
    if store:
        new = p.inside(store[0], "undersink_filter_cartridge", label="new filter cartridge (CB-10)", shelf=0)
        alike = p.inside(store[0], "water_filter_cartridge", label="refrigerator filter (EDR1RXD1)", shelf=0)
    counter = next((c for c in p.find("counter", room) + p.find("table", room) if reachable(ctx, c)), None)
    if new is None:
        new = _put(ctx, room, "undersink_filter_cartridge", on=counter, label="new filter cartridge (CB-10)")
    if alike is None:
        alike = _put(ctx, room, "water_filter_cartridge", on=counter, label="refrigerator filter (EDR1RXD1)")
    # the trash can stands clear of the sink cabinet's door swing (a door sweeping open knocks a can over)
    swing = set()
    for cab in [sink_cab] + store[:1]:
        foot = geometry.footprint(_cfg(ctx, cab)) if _cfg(ctx, cab).get("location") == room else []
        swing.update((x + dx, y + dy) for x, y in foot for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)))
    trash_props = dict(label="kitchen trash can", category="waste_container", accepts_items=True, container=True, capacity_l=50.0,
                       inner_size_m=[0.35, 0.3, 0.6], size_m=[0.38, 0.32, 0.65], portable=False, waste_stream="trash", accepts_any_waste=True,
                       blocks_path=False)
    trash = floor_reachable(ctx, room, "generic_item", avoid_cells=swing, **trash_props) or _put(ctx, room, "generic_item", **trash_props)
    return {"housing": housing, "faucet": faucet, "sink_cabinet": sink_cab, "old_filter": old, "new_filter": new, "lookalike_filter": alike,
            "filter_room": room, "trash": trash, "today": ctx.config.get("clock", {}).get("start", "2026-10-09")[:10]}


def _store_spot(ctx, kinds, containers=("closet_shelving", "shelf_unit", "cabinet")):
    """(room, shelf/cabinet) for household supplies: the first room of `kinds` that has a storage piece the robot can
    walk up to (crowded closets: a water heater can stand in front of the shelving); else the first such room with
    no reachable storage (items then go on its floor) -> (room, None)."""
    p = ctx.placer
    first = None
    for kind in kinds:
        for room in p.rooms(kind):
            first = first or room
            for ctype in containers:
                for found in p.find(ctype, room):
                    if reachable(ctx, found):
                        return room, found
    return (first or _room(ctx, None, kinds)), None


@scenario_helper("tools_vehicles.hinge")
def hinge(ctx, room=None, approved_available=True, decoy=True):
    """A squeaky door hinge, the household's approved lubricant list, a can of the approved product in the utility closet
    (unless approved_available=False) and a tempting kitchen olive oil decoy."""
    p = ctx.placer
    doors = sorted(e for e, r in ctx.config["entities"].items() if r.get("category") == "door" and r.get("connects") and not r.get("exterior")
                   and r.get("type") == "door")
    if not doors:
        raise SpecError("No interior door with hinges")
    door = ctx.rng.choice(doors)
    drec = _cfg(ctx, door)
    drec["_hinge"] = {"squeak": 0.8, "base_squeak": 0.8, "squeak_points": ["pin"], "pins": 3}
    hh = ctx.config.setdefault("household", {})
    hh["approved_lubricants"] = ["silicone", "lithium_grease"]
    store_room, shelf = _store_spot(ctx, ("utility_closet", "garage", "laundry_room", "basement", "kitchen"))
    out = {"door": door, "door_rooms": drec.get("connects")}
    if approved_available:
        out["approved_lube"] = _put(ctx, store_room, "silicone_spray", inside=shelf, shelf=1) if shelf else _put(ctx, store_room, "silicone_spray")
    out["penetrant"] = _put(ctx, store_room, "penetrating_oil", inside=shelf, shelf=1) if shelf else _put(ctx, store_room, "penetrating_oil")
    rag_t, rag_p = _foreign(ctx, "cotton_rag", {"label": "cotton rag", "absorbent": True, "category": "rag", "material": "cotton", "size_m": [0.35, 0.35, 0.005],
                                                "mass_kg": 0.06, "tool_kind": "cotton_rag"})
    rag_p.update(absorbent=True)
    out["rag"] = _put(ctx, store_room, rag_t, inside=shelf, shelf=0, **rag_p) if shelf else _put(ctx, store_room, rag_t, **rag_p)
    if decoy:
        kitchen = (p.rooms("kitchen") or [store_room])[0]
        counter = _surface(ctx, kitchen, ("counter", "table"))
        ot, op = _foreign(ctx, "olive_oil_bottle", {"label": "olive oil", "category": "food", "liquid": "olive_oil", "size_m": [0.07, 0.07, 0.28],
                                                    "mass_kg": 0.9, "food": {"kind": "olive_oil", "state": "raw", "cut": "whole"}})
        out["olive_oil"] = _put(ctx, kitchen, ot, on=counter, **op)
    out["store_room"] = store_room
    return out


@scenario_helper("tools_vehicles.door_seals")
def door_seals(ctx, failing=2, include_windows=True, seed_kinds=("compressed", "torn", "missing"), ensure_compressed=True):
    """Weather-strip state on every exterior door (and windows): `failing` randomly chosen segments are bad.
    Returns {seal_targets: [...], failing: ['door:segment', ...], failing_by_target: {...}}."""
    rooms = ctx.config["rooms"]
    targets = []
    for e, r in sorted(ctx.config["entities"].items()):
        if r.get("category") == "door" and r.get("exterior") and r.get("connects") and r.get("type") in ("exterior_door", "sliding_door"):
            targets.append(e)
        elif include_windows and r.get("category") == "window" and rooms.get(r.get("location"), {}).get("type") not in ("shed", "garage")                 and not rooms.get(r.get("location"), {}).get("shared") and reachable(ctx, e):
            targets.append(e)
    if not targets:
        raise SpecError("No exterior doors or windows")
    for t in targets:
        rec = _cfg(ctx, t)
        rec["open"] = False
        if rec.get("lockable"):
            rec["locked"] = True
    segs = {}
    for t in targets:
        rec = _cfg(ctx, t)
        names = ("head", "latch_side", "hinge_side", "sweep") if rec.get("category") == "door" else ("sash_top", "sash_bottom", "meeting_rail")
        rec["_seal"] = {"segments": {n: {"kind": "sweep" if n == "sweep" else "foam", "state": "ok", "length_m": 0.9 if n in ("head", "sweep") else 2.0}
                                     for n in names}, "clearance_mm": 3.0, "threshold_ice_mm": 0.0}
        for n in names:
            segs.setdefault(t, []).append(n)
    pool = [(t, n) for t in targets for n in segs[t]]
    chosen = ctx.rng.sample(pool, min(failing, len(pool)))
    out = {"seal_targets": targets, "failing": [], "failing_by_target": {t: [] for t in targets}, "first_target": targets[0],
           "first_window": next((t for t in targets if _cfg(ctx, t).get("category") == "window"), targets[0])}
    for i, (t, n) in enumerate(sorted(chosen)):
        kind = "compressed" if (ensure_compressed and i == 0) else ctx.rng.choice(list(seed_kinds))
        _cfg(ctx, t)["_seal"]["segments"][n]["state"] = kind
        out["failing"].append("{}:{}".format(t, n))
        out["failing_by_target"][t].append(n)
    for t in targets:
        out["failing_by_target"][t] = sorted(out["failing_by_target"][t])
    return out


@scenario_helper("tools_vehicles.wall_repair")
def wall_repair(ctx, room=None, holes=2, paint_match=True):
    """A wall with anchor/nail holes, spackle, putty knife, sanding block, primer, matching paint (+ a near-miss can), brush, drop cloth."""
    room = _room(ctx, room, ("living_room", "hallway", "bedroom", "office"))
    p = ctx.placer
    res = wall(ctx, room=room, holes=holes, var="wall")
    wid = res["wall"]
    store = _room(ctx, None, ("garage", "basement", "utility_closet"))
    shelf = (p.find("shelf_unit", store) or p.find("closet_shelving", store) or [None])[0]
    def put(t, **kw):
        return _put(ctx, store, t, inside=shelf, shelf=1, **kw) if shelf else _put(ctx, store, t, **kw)
    out = {"wall": wid, "repair_room": room, "store_room": store}
    out["spackle"] = put("spackle_tub")
    out["putty_knife"] = put("putty_knife")
    out["sanding_block"] = put("sanding_block", grit=220)
    out["primer"] = put("primer_can")
    out["paint"] = put("paint_can", paint_color="swiss_coffee" if paint_match else "antique_white", sheen="eggshell")
    out["wrong_paint"] = put("paint_can", label="quart of paint (antique white, satin)", paint_color="antique_white", sheen="satin")
    out["brush"] = put("paint_brush_angled")
    ct, cp = _foreign(ctx, "drop_cloth", {"label": "canvas drop cloth", "category": "cover", "tags": ["cover", "drop_cloth"], "material": "canvas",
                                          "size_m": [0.45, 0.35, 0.08], "mass_kg": 1.8})
    out["drop_cloth"] = put(ct, **cp)
    out["holes"] = [h["id"] for h in _cfg(ctx, wid)["holes"]]
    return out


@scenario_helper("tools_vehicles.small_fixes")
def small_fixes(ctx, toilet_fault="chain", clog="hair", chemical=False, disposal_object=False, radiator=True, printer=True):
    """The five small fixes of X-1c71303328a5b8 in one house: a running toilet, a slow bathroom drain, a jammed disposal,
    a printer jam and an air-locked radiator, plus the tools for each."""
    p = ctx.placer
    first = _room(ctx, None, ("bathroom", "half_bath"))
    kitchen = _room(ctx, None, ("kitchen",))
    toilet_props = dict(category="toilet", toilet=True, cover_open=False, settings={"supply_valve": "open"},
                        setting_options={"supply_valve": ["open", "closed"]},
                        slots={"flapper": {"accepts": ["flapper_2in"], "item": None, "access": "cover"}})
    sink_props = dict(category="sink", drain=True, water_source=True, switchable=True, on=False, flow_lpm=6.0,
                      settings={"stopper": "installed"}, setting_options={"stopper": ["installed", "removed"]},
                      basin_l=8.0, drain_capacity_lpm=25.0, accepts_items=True, surface=True, surface_size_m=[0.6, 0.5],
                      _clog={"kind": clog, "amount": 0.85, "depth_m": 0.25, "chemical": bool(chemical)})
    # the toilet and the slow sink share a bathroom with floor space for both (small apartment baths can be full)
    bath = toilet = sink = None
    for cand in [first] + [r for k in ("bathroom", "half_bath") for r in p.rooms(k) if r != first]:
        toilet = _fixture_spot(ctx, cand, "toilet", [0.4, 0.7, 0.8], **copy.deepcopy(toilet_props))
        if toilet is None:
            continue
        sink = _fixture_spot(ctx, cand, "bathroom sink", [0.6, 0.5, 0.85], **copy.deepcopy(sink_props))
        if sink is None:
            del ctx.config["entities"][toilet]
            toilet = None
            continue
        bath = cand
        break
    if bath is None:
        # a bathroom with no floor left for two more fixtures (a 2.5 m bath with a shower stall and a washer stack, held-out
        # seeds 12, 31, 84: review H10): its own toilet and sink become the faulty ones, rebuilt in place with this
        # helper's fixture model, so the spec runs on every house that has a bathroom
        for cand in [first] + [r for k in ("bathroom", "half_bath") for r in p.rooms(k) if r != first]:
            toilet = _refit_fixture(ctx, cand, "toilet", "toilet", [0.4, 0.7, 0.8], **copy.deepcopy(toilet_props))
            sink = _refit_fixture(ctx, cand, "sink", "bathroom sink", [0.6, 0.5, 0.85], **copy.deepcopy(sink_props)) if toilet else None
            if toilet and sink:
                bath = cand
                break
    if bath is None:
        raise SpecError("No bathroom with space for the toilet and sink fixes")
    out = {"bath": bath, "kitchen": kitchen}
    trec = _cfg(ctx, toilet)
    tank = {"flapper_seal": 0.95, "chain_slack_mm": 10.0, "float_mm": -25.0, "fill_valve_ok": True}
    if toilet_fault == "chain":
        tank["chain_slack_mm"] = 0.0
    elif toilet_fault == "float":
        tank["float_mm"] = 6.0
    elif toilet_fault == "flapper":
        tank["flapper_seal"] = 0.4
    elif toilet_fault == "fill_valve":
        tank["fill_valve_ok"] = False
    trec["_tank"] = tank
    oldf = p.make("toilet_flapper", toilet, label="old flapper", seal=tank["flapper_seal"])
    _cfg(ctx, oldf).update(relation="part", slot="flapper")
    trec["slots"]["flapper"]["item"] = oldf
    out["toilet"] = toilet
    out["sink"] = sink
    sink_cab = next((c for c in p.find("cabinet", kitchen) if reachable(ctx, c)), None) or furniture_reachable(ctx, kitchen, "cabinet", label="sink base cabinet")
    if sink_cab is None:
        raise SpecError("No sink cabinet for the disposal")
    _cfg(ctx, sink_cab)["label"] = "sink base cabinet"
    disp = p.make("generic_item", sink_cab, label="garbage disposal (under sink)", size_m=[0.3, 0.3, 0.35], category="disposal", switchable=True,
                  on=False, portable=False, _jam={"jammed": True, "object": None}, thermal_tripped=True, tripped_at=-3600.0, accepts_items=True,
                  container=True, capacity_l=1.0, inner_size_m=[0.15, 0.15, 0.15], mass_kg=6.0)
    _cfg(ctx, disp)["relation"] = "in"
    out["sink_cabinet"] = sink_cab
    out["disposal"] = disp
    if disposal_object:
        spoon = p.make("generic_item", disp, label="teaspoon", material="steel", size_m=[0.14, 0.03, 0.01], mass_kg=0.02, category="utensil")
        _cfg(ctx, spoon)["relation"] = "in"
        _cfg(ctx, disp)["_jam"]["object"] = spoon
        out["spoon"] = spoon
    store = _room(ctx, None, ("utility_closet", "garage", "kitchen"))
    out["store_room"] = store
    out["snake"] = _put(ctx, store, "drain_snake")
    out["hex_key"] = _put(ctx, store, "allen_key_quarter_in")
    out["new_flapper"] = _put(ctx, store, "toilet_flapper", label="new 2 in flapper")
    if radiator:
        first = _room(ctx, None, ("living_room", "bedroom"))
        rad = None
        for room in [first] + [r for k in ("living_room", "bedroom", "dining_room", "office", "hallway") for r in p.rooms(k) if r != first]:
            rad = _fixture_spot(ctx, room, "hot-water radiator", [0.8, 0.12, 0.6], category="radiator", heat_source=True, _air_frac=0.35,
                                heating_on=False, temperature_c=21.0)
            if rad is not None:
                break
        if rad is None:
            raise SpecError("No wall space for a radiator")
        out["radiator"] = rad
        out["bleed_key"] = _put(ctx, store, "radiator_bleed_key")
        tt, tp = _foreign(ctx, "cotton_rag", {"label": "old towel", "absorbent": True, "category": "rag", "material": "cotton",
                                              "size_m": [0.4, 0.3, 0.01], "mass_kg": 0.2, "tool_kind": "cotton_rag"})
        tp.update(absorbent=True)
        out["towel"] = _put(ctx, store, tt, **tp)
        ctx.config.setdefault("utilities", {})["heating_pressure_bar"] = 1.6
    if printer:
        office = _room(ctx, None, ("office", "living_room", "bedroom"))
        surface = _surface(ctx, office, ("desk", "table", "dresser", "side_table"))
        pr = _put(ctx, office, "generic_item", on=surface, label="laser printer", category="office_device", portable=False, cover_open=False,
                  mass_kg=11.5, size_m=[0.42, 0.4, 0.32], fuser_temp_c=35.0, jam_location="rear",
                  slots={"paper_path": {"accepts": ["jammed_paper"], "item": None, "access": "cover"}}, printer=True)
        jam = p.make("generic_item", pr, label="crumpled jammed sheet", part_type="jammed_paper", category="debris", material="paper",
                     mass_kg=0.005, size_m=[0.216, 0.15, 0.01], volume_l=0.05, waste_stream="recycling")
        _cfg(ctx, jam).update(relation="part", slot="paper_path")
        _cfg(ctx, pr)["slots"]["paper_path"]["item"] = jam
        out["printer"] = pr
        out["jam"] = jam
    return out
