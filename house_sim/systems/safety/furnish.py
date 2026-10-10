"""Furnish generated houses with safety equipment (order 72: small items, after cabinets/appliances).

Every dwelling: a 5 lb ABC extinguisher on a bracket on the wall beside the kitchen doorway (85 %), a first-aid kit in
the first bathroom's medicine/vanity cabinet, else on its counter or a shelf (85 %). Houses: an outdoor gas meter with
its main shutoff when the home has gas service (plus a wrench on a garage/basement shelf 50 %), a garage extinguisher
beside the door into the house (50 %), a monitored alarm keypad beside the front door (35 %), an emergency go-bag in
the coat/utility closet (35 %), a fire blanket in the kitchen (20 %), an AED (3 %). Every item is optional: a spot that
does not qualify leaves the item out, so any dwelling/seed furnishes without errors and without layout problems.

Placement (kernel v2.3 layout model): "beside the door" mounts (beside_door, also used by the scenario helpers) use
the shared door model - never a doorway (or an overhead door's threshold run), never the hinge side of a door that
swings into the room (the open leaf would cover it), on clear, reachable wall (geometry.door_swing /
overhead_door_cells / reachable_cells); other mounts go through Placer.mount(strict=True) and are kept only when the
robot can reach them; floor items go through Placer.floor_item (keep_access). Smoke/CO detectors, chemicals and
cookware belong to other domains and are furnished by them.
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher


def _reachable_mount(p, rid, eid):
    """A mount the robot can touch: its cell or a cell within reach radius (one more for mounts at counter height and
    above) is doorway-reachable floor."""
    rec = p.c["entities"][eid]
    cells = set(geometry.footprint(rec))
    reach = geometry.reachable_cells(p.state(), rid)
    radius = geometry.access_radius(rec)
    return bool(cells & reach or geometry.near_cells(cells, p.c["rooms"][rid], radius) & reach)


def _wall_clear(p, rid, eid):
    """Nothing else hangs on that stretch of wall at that height: no window, blind or shade, no other wall mount
    (Placer.mount only keeps deep mounts apart; a fire blanket must not hang over a window shade)."""
    rec = p.c["entities"][eid]
    cells = set(geometry.footprint(rec))
    low = float(rec.get("mount_height_m", 0.0) or 0.0)
    high = low + float((rec.get("size_m") or [0, 0, 0.1])[2])
    for oid in geometry.room_entities(p.state(), rid):
        other = p.c["entities"][oid]
        if oid == eid or not other.get("pos") or not other.get("mounted") or other.get("category") == "light" or "connects" in other:
            continue
        o_low = float(other.get("mount_height_m", 0.0) or 0.0)
        o_high = o_low + float((other.get("size_m") or [0, 0, 0.1])[2])
        if o_low < high and low < o_high and cells & set(geometry.footprint(other)):
            return False
    return True


def _mount(p, rid, type_name, height, **props):
    """Placer.mount (strict: no last-resort cell), kept only when the robot can reach it and the wall there is clear."""
    eid = p.mount(rid, type_name, height=height, strict=True, **props)
    if eid is None:
        return None
    if _reachable_mount(p, rid, eid) and _wall_clear(p, rid, eid):
        return eid
    del p.c["entities"][eid]
    return None


def _openings(state, rid):
    """Perimeter cells of a room that are door openings: every doorway cell plus overhead doors' threshold runs."""
    cells = set(geometry.doorway_cells(state, rid))
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if rid in conn.get("cells", {}) and conn.get("door"):
            cells.update(geometry.overhead_door_cells(state, conn["door"]).get(rid, []))
    return cells


def _behind_leaves(state, rid):
    """Wall cells a hinged door that swings into this room covers when it stands open: the hinge side of its doorway
    (derived from geometry.door_swing's hinge-side diagonal)."""
    room = state["rooms"][rid]
    out = set()
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if rid not in conn.get("cells", {}) or not conn.get("door"):
            continue
        srid, leaf = geometry.door_swing(state, conn["door"])
        if srid != rid:
            continue
        x, y = (int(v) for v in conn["cells"][rid])
        inward, _ = geometry.wall_inward(room, (x, y))
        for cx, cy in leaf:
            hx, hy = cx - x - inward[0], cy - y - inward[1]
            if (hx, hy) != (0, 0) and abs(hx) + abs(hy) == 1:
                out.update({(x + hx, y + hy), (x + 2 * hx, y + 2 * hy)})
    return out


def beside_door(p, rid, type_name, height, prefer=None, fallback=True, **props):
    """Mount an item on the wall beside one of the room's doorways (an extinguisher by the kitchen door, the alarm
    keypad by the front door): one, then two cells along the doorway's wall, never in an opening or behind an open
    leaf, on wall with nothing standing in front of it or hanging there at that height, on reachable floor.
    `prefer(conn, other_room)` ranks doorways (lower first). Falls back to _mount (fallback=True); None when nothing
    qualifies. Scenario helpers use it too (detectors, alarm keypads, fixtures)."""
    state = p.state()
    room = p.c["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    record = p.reg.make(type_name, rid, **props)
    top = float(height) + float((record.get("size_m") or [0, 0, 0.3])[2])
    openings = _openings(state, rid)
    behind = _behind_leaves(state, rid)
    reach = geometry.reachable_cells(state, rid)
    standing, hung = set(), set()
    for eid in geometry.room_entities(state, rid):
        other = p.c["entities"][eid]
        if not other.get("pos") or "connects" in other or other.get("ground_layer"):
            continue
        if geometry.floor_standing(other):
            standing.update(geometry.footprint(other))
        elif other.get("mounted") and other.get("category") != "light":
            low = float(other.get("mount_height_m", 0.0) or 0.0)
            high = low + float((other.get("size_m") or [0, 0, 0.1])[2])
            if low < top and high > float(height):
                hung.update(geometry.footprint(other))
    doors = []
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if rid not in conn.get("cells", {}):
            continue
        rooms = list(conn.get("rooms") or [])
        other = rooms[1] if rooms and rooms[0] == rid else (rooms[0] if rooms else None)
        rank = prefer(conn, other) if prefer else 0
        if rank is not None:
            doors.append((rank, cid, tuple(int(v) for v in conn["cells"][rid])))
    options = []
    for rank, cid, (x, y) in sorted(doors):
        inward, lateral = geometry.wall_inward(room, (x, y))
        for k in (1, 2):
            for sign in (1, -1):
                c = (x + sign * k * lateral[0], y + sign * k * lateral[1])
                if not geometry.in_grid(room, c) or c in openings or c in behind or c in standing or c in hung:
                    continue
                if not (c[0] in (0, cols - 1) or c[1] in (0, rows - 1)) or geometry.wall_inward(room, c)[0] != inward:
                    continue          # not on the doorway's own wall (around a corner)
                if c not in reach:
                    continue
                options.append((rank, k, cid, -sign, c))
    if options:
        cell = min(options)[-1]
        return p.mount(rid, type_name, height=height, cell=list(cell), **props)
    return _mount(p, rid, type_name, height, **props) if fallback else None


def _first(p, type_name, room, label_has=None):
    for eid in p.find(type_name, room):
        if label_has is None or label_has in str(p.c["entities"][eid].get("label", "")):
            return eid
    return None


def indoor_door_first(config):
    """Doorway ranking for a garage extinguisher: the door into the house, never beside the overhead door."""
    def rank(conn, other):
        if conn.get("kind") == "garage":
            return None
        return 0 if other and not config["rooms"].get(other, {}).get("outdoor") else 1
    return rank


def front_door_first(config):
    """Doorway ranking for the alarm keypad: inside, beside the door to the outside."""
    def rank(conn, other):
        if conn.get("kind") == "garage":
            return None
        return 0 if other and config["rooms"].get(other, {}).get("outdoor") else 1
    return rank


def _first_aid_kit(p, rid):
    """In the medicine cabinet (or any cabinet/vanity), else on the vanity counter or a shelf; never loose on the floor."""
    holder = _first(p, "upper_cabinet", rid, "medicine") or _first(p, "cabinet", rid)
    if holder and p.inside(holder, "first_aid_kit"):
        return
    for kind in ("counter", "shelf_unit", "shelf", "table"):
        for surface in p.find(kind, rid):
            if p.c["entities"][surface].get("surface") and p.on(surface, "first_aid_kit"):
                return
    closets = p.rooms("linen_closet") + p.rooms("utility_closet")
    holder = next((e for r in closets for e in p.find("closet_shelving", r)), None)
    if holder is not None:
        p.inside(holder, "first_aid_kit", shelf=0)


@furnisher("safety_equipment", order=72)
def furnish_safety(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    dwelling = (opts.get("plan") or {}).get("dwelling") or config.get("metadata", {}).get("dwelling_type", "house")
    for rid in p.rooms("kitchen"):
        if rng.random() < 0.85:
            beside_door(p, rid, "fire_extinguisher", 1.0, label="kitchen fire extinguisher")
        if rng.random() < 0.2:
            # by the kitchen exit like the extinguisher (beside_door keeps it off windows, shades and taller appliances)
            beside_door(p, rid, "fire_blanket", 1.4, label="fire blanket")
    baths = p.rooms("bathroom")
    if baths and rng.random() < 0.85:
        _first_aid_kit(p, baths[0])
    if dwelling != "apartment":
        if config.get("utilities", {}).get("gas", True):
            outs = [r for kind in ("backyard", "front_yard", "side_yard", "driveway") for r in p.rooms(kind)]
            if outs:
                meter = _mount(p, outs[0], "gas_meter", 0.3)
                if meter and rng.random() < 0.5:
                    for kind in ("garage", "basement", "shed"):
                        shelf = next((e for r in p.rooms(kind) for e in p.find("shelf_unit", r)), None)
                        if shelf:
                            p.inside(shelf, "generic_item", label="adjustable wrench", tool_kind="wrench", capabilities=["wrench"],
                                     material="steel", mass_kg=0.6, size_m=[0.3, 0.06, 0.02], shelf=1)
                            break
        for rid in p.rooms("garage"):
            if rng.random() < 0.5:
                beside_door(p, rid, "fire_extinguisher", 1.0, prefer=indoor_door_first(config), label="garage fire extinguisher")
        entries = p.rooms("entryway")
        if entries and rng.random() < 0.35:
            beside_door(p, entries[0], "security_alarm_panel", 1.4, prefer=front_door_first(config), monitored=rng.random() < 0.7)
    closets = p.rooms("coat_closet") + p.rooms("utility_closet")
    if closets and rng.random() < 0.35:
        holder = next((e for r in closets for e in p.find("closet_shelving", r)), None)
        if holder is None or p.inside(holder, "home_emergency_kit", shelf=0) is None:
            p.floor_item(closets[0], "home_emergency_kit")      # a go-bag standing on the closet floor
    if rng.random() < 0.03:
        hub = (p.rooms("hallway") or p.rooms("entryway") or p.rooms("living_room"))
        if hub:
            cab = _mount(p, hub[0], "aed_cabinet", 1.0)
            if cab:
                p.inside(cab, "aed")
