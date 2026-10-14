"""Placement helpers for house generation and scenario setup.

    p = Placer(config, registry, rng)
    sofa = p.furniture("living_room_1", "sofa", wall=True)
    p.on(coffee_table, "book")                 # free spot on a surface
    p.inside(cabinet, "plate", shelf=0)        # capacity- and fit-checked
    p.floor_item(rid, "toy", near=[2, 3])      # small object lying on the floor
    p.mount(rid, "smoke_detector", height=2.4) # wall/ceiling mounted, non-blocking
    p.hang(closet, "coat")                     # on a hook/rod (hang_slots)

All helpers return the new entity id, or None when nothing fits (callers decide
whether that is acceptable). Positions follow kernel.geometry conventions.
"""
import copy

from ..kernel import geometry


def wall_layouts(room, record, cell):
    """[(pos, rot)] for a wall-mounted record at perimeter cell `cell`, one per wall that cell is on (west, east,
    north, south; a corner is on two, so is every cell of a room one cell wide): an oblong footprint runs along that
    wall (rot 90 on west/east, 0 on north/south), its start moved back along the wall so the whole footprint stays on
    the grid and its back stays flush with the wall. A square footprint is left as it is ([(cell, its rot)]: the
    caller checks it fits). The first entry is geometry.along_wall's choice. Used by Placer.mount(along_wall=True)."""
    cols, rows = geometry.grid_size(room)
    x, y = int(cell[0]), int(cell[1])
    w, d = geometry.footprint_size(dict(record, rot=0))
    if w == d:
        return [((x, y), record.get("rot"))]
    first = geometry.along_wall(room, dict(record, pos=[x, y]))
    out = [(tuple(first["pos"]), first.get("rot"))]
    sides = [s for s, on in (("west", x == 0), ("east", x == cols - 1), ("north", y == 0), ("south", y == rows - 1)) if on]
    for side in sides:
        rot = (90 if w > d else 0) if side in ("west", "east") else (0 if w > d else 90)
        fc, fr = geometry.footprint_size(dict(record, rot=rot))
        px, py = max(0, min(x, cols - fc)), max(0, min(y, rows - fr))
        if side == "east":
            px = max(0, cols - fc)
        elif side == "south":
            py = max(0, rows - fr)
        option = ((px, py), rot)
        if option not in out:
            out.append(option)
    return out


class Placer:
    def __init__(self, config, registry, rng):
        self.c, self.reg, self.rng = config, registry, rng

    # ----- views -------------------------------------------------------
    def state(self):
        robot = self.c.get("robot") or {}
        return {"rooms": self.c["rooms"], "entities": self.c["entities"], "people": self.c.get("people", {}),
                "pets": self.c.get("pets", {}), "connections": self.c.get("connections", {}),
                "robot": {"room": robot.get("room") or self.c.get("start_room"), "hands": {}, "pos": robot.get("pos")}}

    def rooms(self, room_type=None, outdoor=None):
        result = []
        for rid in sorted(self.c["rooms"]):
            room = self.c["rooms"][rid]
            if room_type is not None and room.get("type") != room_type:
                continue
            if outdoor is not None and bool(room.get("outdoor")) != outdoor:
                continue
            result.append(rid)
        return result

    def find(self, type_name=None, room=None, where=None):
        from ..kernel.predicates import root_room
        state = self.state()
        result = []
        for eid in sorted(self.c["entities"]):
            record = self.c["entities"][eid]
            if type_name and not (record.get("type") == type_name or type_name in record.get("is_a", [])):
                continue
            if room and root_room(state, eid) != room:
                continue
            if where and any(record.get(k) != v for k, v in where.items()):
                continue
            result.append(eid)
        return result

    def new_id(self, stem):
        stem = "".join(ch if ch.isalnum() else "_" for ch in stem.lower()).strip("_") or "item"
        n = 1
        taken = set(self.c["entities"]) | set(self.c["rooms"]) | set(self.c.get("people", {})) | set(self.c.get("pets", {}))
        while "{}_{}".format(stem, n) in taken:
            n += 1
        return "{}_{}".format(stem, n)

    def make(self, type_name, location, eid=None, **props):
        record = self.reg.make(type_name, location, **props)
        eid = eid or self.new_id(type_name)
        if eid in self.c["entities"]:
            raise ValueError("Duplicate entity id " + eid)
        self.c["entities"][eid] = record
        return eid

    # ----- rooms -------------------------------------------------------
    def _occupied(self, rid):
        state = self.state()
        cells = set(geometry.blocked_cells(state, rid, include_agents=True))
        for eid in geometry.room_entities(state, rid):
            rec = self.c["entities"][eid]
            if geometry.floor_standing(rec) or geometry.front_clear(rec) or geometry.access_fixture(rec) or geometry.floor_vent(rec):
                cells.update(geometry.footprint(rec))
        robot = state.get("robot") or {}
        if robot.get("room") == rid and robot.get("pos"):
            cells.add(tuple(robot["pos"]))
        # doorway cells, their four neighbours, hinged leaves swinging into the room, overhead-door thresholds
        cells |= geometry.door_clearance(state, rid)
        return cells

    def _hidden_by(self, rid, top):
        """Wall cells of mounts a floor-standing piece `top` metres tall would hide (windows, blinds, shelves,
        cabinets, radiators, controls; not outlets, which sit behind the appliances they feed)."""
        cells = set()
        if top is None:
            return cells
        for eid in geometry.room_entities(self.state(), rid):
            rec = self.c["entities"][eid]
            if not rec.get("mounted") or rec.get("ground_layer") or "connects" in rec or geometry.floor_standing(rec):
                continue
            if rec.get("category") in ("outlet", "light"):
                continue
            try:
                h = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0)
            except (TypeError, ValueError):
                continue
            if 0.0 < h < top and h < 2.0:
                cells.update(geometry.footprint(rec))
        return cells

    def _free_spot(self, rid, cols, rows, wall=True, near=None, keep_path=True, standable=False, strict=False, top=None):
        room = self.c["rooms"][rid]
        gc, gr = geometry.grid_size(room)
        if cols > gc or rows > gr:
            return None
        occupied = self._occupied(rid) | self._hidden_by(rid, top)
        options = []
        for y in range(0, gr - rows + 1):
            for x in range(0, gc - cols + 1):
                cells = [(x + i, y + j) for i in range(cols) for j in range(rows)]
                if any(c in occupied for c in cells):
                    continue
                if cols > rows:      # long side runs along x: back against the top or bottom wall
                    touches = y == 0 or y + rows == gr
                elif rows > cols:    # long side runs along y: back against the left or right wall
                    touches = x == 0 or x + cols == gc
                else:
                    touches = x == 0 or y == 0 or x + cols == gc or y + rows == gr
                if wall and not touches:
                    continue
                options.append((x, y))
        if not options:
            return None
        if near is not None:
            options.sort(key=lambda p: (geometry.chebyshev(p, near), p[1], p[0]))
            candidates = options[:6]
        else:
            candidates = self.rng.sample(options, len(options))
        if not keep_path:
            return list(candidates[0])
        levels = [("all", True), ("all", False)] + ([] if strict else [("basic", False)])
        for level, hazards in levels:
            check = self._connectivity(rid, level, hazards=hazards)
            for pos in candidates[:80]:
                if check(pos, cols, rows, standable):
                    return list(pos)
        return None

    def _connectivity(self, rid, level, hazards=False):
        """Placement check for a blocking (or standable) footprint: connectivity and access must not get worse
        (geometry.not_worse) and the new piece itself must be accessible. People and pets already standing in the
        room count as obstacles (core routing treats them as walls), so a piece never walls part of the room off
        together with them. With hazards=True the check also treats current floor hazards (loose items, cords,
        keep-out rings) as impassable, so the piece does not leave anything reachable only over hazards."""
        state = self.state()
        room = self.c["rooms"][rid]
        base_blocked = set(geometry.blocked_cells(state, rid, include_agents=True))
        if hazards:
            base_blocked |= set(geometry.placement_hazards(state, rid))
        targets = geometry.access_targets(state, rid, level=level)
        before = geometry.access_report(state, rid, base_blocked, targets)

        def check(pos, cols, rows, standable=False):
            cells = [(pos[0] + i, pos[1] + j) for i in range(cols) for j in range(rows)]
            tg = dict(targets)
            tg[("new",)] = (cells, 0 if standable else 1, standable)
            blocked = base_blocked if standable else base_blocked | set(cells)
            after = geometry.access_report(state, rid, blocked, tg)
            return geometry.not_worse(before, after, new_keys=[("new",)])
        return check

    def _keeps_connected(self, rid, pos, cols, rows, level="basic"):
        """Back-compatible single check (see _connectivity)."""
        return self._connectivity(rid, level)(pos, cols, rows)

    def _all_cells(self, rid):
        gc, gr = geometry.grid_size(self.c["rooms"][rid])
        return [(x, y) for y in range(gr) for x in range(gc)]

    def free_fraction(self, rid, extra=0):
        """Share of a room's floor cells not taken by anything floor-standing (minus `extra` cells)."""
        state = self.state()
        cols, rows = geometry.grid_size(self.c["rooms"][rid])
        taken = set()
        for eid in geometry.room_entities(state, rid):
            if geometry.floor_standing(self.c["entities"][eid]):
                taken.update(c for c in geometry.footprint(self.c["entities"][eid]) if geometry.in_grid(self.c["rooms"][rid], c))
        return (cols * rows - len(taken) - extra) / float(cols * rows)

    def furniture(self, rid, type_name, eid=None, wall=True, near=None, rot=None, fallback=True, min_free=None, strict=False, **props):
        """Furniture against a wall (or anywhere with wall=False) that keeps access not worse (see _connectivity).
        min_free: optional pieces are skipped (None returned) when they would leave less than this share of the
        room's floor free -- real rooms keep walking space. strict: keep every mount (radiators, registers, wall
        cabinets...) accessible too, or return None, instead of falling back to the basic protection level."""
        record = self.reg.make(type_name, rid, **props)
        if min_free is not None:
            cols, rows = geometry.footprint_size(record)
            if self.free_fraction(rid, extra=cols * rows) < min_free:
                return None
        if rot is None:
            rot = self.rng.choice([0, 90]) if record.get("size_m", [1, 1])[0] != record.get("size_m", [1, 1])[1] else 0
        record["rot"] = rot
        standable = geometry.is_standable(record)
        cols, rows = geometry.footprint_size(record)
        try:
            top = float((record.get("size_m") or [0, 0, 0.5])[2])
        except (TypeError, ValueError, IndexError):
            top = None
        hide_ok = [top, None] if not strict else [top]      # prefer spots that hide no window, blind or shelf
        pos = None
        for limit in hide_ok:
            pos = self._free_spot(rid, cols, rows, wall=wall, near=near, standable=standable, strict=strict, top=limit)
            if pos is None and rot in (0, 90):
                record["rot"] = 90 - rot
                cols, rows = geometry.footprint_size(record)
                pos = self._free_spot(rid, cols, rows, wall=wall, near=near, standable=standable, strict=strict, top=limit)
                if pos is None:
                    record["rot"] = rot
                    cols, rows = geometry.footprint_size(record)
            if pos is None and wall and fallback:
                pos = self._free_spot(rid, cols, rows, wall=False, near=near, standable=standable, strict=strict, top=limit)
            if pos is not None:
                break
        if pos is None:
            return None
        record["pos"] = pos
        eid = eid or self.new_id(type_name)
        self.c["entities"][eid] = record
        return eid

    def agent_cell(self, rid, near=None, ring=0):
        """A cell for a person or pet: free, outside doorway clearance where possible, joined to the doorways by
        4-connected floor, and not cutting any walking route or the only access to furniture, fixtures or the robot
        dock -- counting the people and pets already in the room as obstacles, as core routing does (ring=1 for
        someone sleeping or lying down: their keep-out ring must not cut routes either). If the room's hazard-free
        routes are already cut, a cell that cuts no plain route; then any free cell that is not a doorway. None if
        the room has no such cell."""
        state = self.state()
        cell = geometry.free_floor_cell(state, rid, near=near, keep_access=True, ring=ring)
        if cell is None:
            cell = geometry.free_floor_cell(state, rid, near=near, keep_access="plain")
        if cell is None:
            cell = geometry.free_floor_cell(state, rid, near=near, clearance=False, reachable=False)
            if cell is not None and tuple(cell) in set(geometry.doorway_cells(state, rid)):
                cell = None
        return cell

    def floor_item(self, rid, type_name, eid=None, near=None, strict=None, cell=None, keep_access=True, avoid=(),
                   prefer_not=(), plain=True, **props):
        """A loose (or path-blocking, with blocks_path=True in props) item standing on the floor. It never cuts a
        hazard-free walking route or the only access to anything (keep_access=False allows deliberate obstruction);
        items 0.15 m or taller and path-blocking items never stand in a doorway clearance (strict). An explicit cell
        is used as given. Returns None when no cell qualifies."""
        state = self.state()
        record = self.reg.make(type_name, rid, **props)
        eid = eid or self.new_id(type_name)
        self.c["entities"][eid] = record
        if cell is not None:
            record["pos"] = list(cell)
            return eid
        try:
            top = float((record.get("size_m") or [0, 0, 0.1])[2])
        except (TypeError, ValueError, IndexError):
            top = 0.1
        if top > 0.5:
            prefer_not = tuple(prefer_not) + tuple(self._hidden_by(rid, top))   # tall items avoid hiding wall mounts
        cell = geometry.free_floor_cell(state, rid, near=near, ignore=(eid,), record=record, strict=strict, keep_access=keep_access,
                                        avoid=avoid, prefer_not=prefer_not)
        if cell is None and keep_access and plain:
            # the room's hazard-free routes may already be cut by something else: at least cut no plain route
            cell = geometry.free_floor_cell(state, rid, near=near, ignore=(eid,), record=record, strict=strict, keep_access="plain",
                                            avoid=avoid, prefer_not=prefer_not)
        if cell is None:
            del self.c["entities"][eid]
            return None
        record["pos"] = cell
        return eid

    def _mount_cell(self, rid, record, relaxed=False, near=None, clearance=True, orient=False):
        """A wall cell for a mount (see mount). orient=True: an oblong footprint is laid along a wall of each candidate
        cell (wall_layouts) and record["rot"] is set to the orientation of the cell returned; orient=False (default)
        keeps record's rot and never changes the record."""
        state = self.state()
        room = self.c["rooms"][rid]
        gc, gr = geometry.grid_size(room)
        blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
        height = float(record.get("mount_height_m", 0.0) or 0.0)
        low = height < 0.5
        standing = geometry.floor_standing(dict(record, mounted=True, mount_height_m=height))
        taken = set()
        for eid in geometry.room_entities(state, rid):
            other = self.c["entities"][eid]
            if geometry.floor_standing(other):
                taken.update(geometry.footprint(other))
        reach = geometry.reachable_cells(state, rid, blocked)
        door_cells = set(geometry.doorway_cells(state, rid))
        keep_clear = geometry.door_clearance(state, rid) if clearance else set()
        radius = 1 if height < 0.85 else 2
        try:
            depth = min(float(v) for v in (record.get("size_m") or [0.1, 0.1, 0.1])[:2])
            top = height + float((record.get("size_m") or [0, 0, 0.1])[2])
        except (TypeError, ValueError):
            depth, top = 0.1, height + 0.1
        hidden_behind, wall_taken = set(), set()
        hides = self._hidden_by(rid, top) if standing else set()
        for eid in geometry.room_entities(state, rid):
            other = self.c["entities"][eid]
            if not other.get("pos"):
                continue
            try:
                o_h = float(other.get("mount_height_m", other.get("z_m", 0.0)) or 0.0)
                o_top = o_h + float((other.get("size_m") or [0, 0, 0])[2])
                o_depth = min(float(v) for v in (other.get("size_m") or [0.1, 0.1, 0.1])[:2])
            except (TypeError, ValueError):
                continue
            if geometry.floor_standing(other) and o_top > height and record.get("category") != "outlet":
                hidden_behind.update(geometry.footprint(other))      # a mount behind a taller appliance is hidden
            elif other.get("mounted") and depth >= 0.1 and o_depth >= 0.1 and o_h < top and height < o_top:
                wall_taken.update(geometry.footprint(other))         # two deep wall mounts cannot share wall space
        perimeter = sorted({(x, 0) for x in range(gc)} | {(x, gr - 1) for x in range(gc)} | {(0, y) for y in range(gr)} | {(gc - 1, y) for y in range(gr)})
        check = self._connectivity(rid, "basic") if standing and not relaxed else None
        cols, rows = geometry.footprint_size(record)
        options = []
        layouts = (lambda c: wall_layouts(room, record, c)) if orient else (lambda c: [(tuple(c), record.get("rot"))])
        seen = set()
        for cell, (pos, rot) in ((c, o) for c in perimeter for o in layouts(c)):
            if (pos, rot) in seen:
                continue
            seen.add((pos, rot))
            cells = geometry.footprint(dict(record, pos=list(pos), rot=rot))
            if not all(geometry.in_grid(room, c) for c in cells):
                continue
            if (low or standing) and any(c in blocked or c in taken for c in cells):
                continue
            if any(c in blocked for c in cells) and height < 0.85:
                continue
            if not relaxed and not (geometry.near_cells(cells, room, radius) & reach):
                continue
            if (low or standing) and any(c in keep_clear for c in cells):
                continue
            if any(c in door_cells for c in cells) or (not low and any(c in keep_clear for c in cells)):
                continue
            if not low and any(c in hidden_behind or c in wall_taken for c in cells):
                continue
            if standing and not relaxed and any(c in hides for c in cells):
                continue       # a floor-standing closet or furnace must not stand in front of a window or wall mount
            options.append((pos, rot))
        self.rng.shuffle(options)
        if near is not None:
            options.sort(key=lambda o: geometry.chebyshev(o[0], tuple(near)))
        for pos, rot in options:
            if orient:
                cols, rows = geometry.footprint_size(dict(record, rot=rot))
            if check is None or check(pos, cols, rows):
                if orient and rot is not None:
                    record["rot"] = rot        # the orientation along its wall (the caller keeps this record)
                return list(pos)
        return None

    def wall_cell(self, rid):
        gc, gr = geometry.grid_size(self.c["rooms"][rid])
        perimeter = sorted({(x, 0) for x in range(gc)} | {(x, gr - 1) for x in range(gc)} | {(0, y) for y in range(gr)} | {(gc - 1, y) for y in range(gr)})
        return list(self.rng.choice(perimeter))

    def mount(self, rid, type_name, eid=None, height=None, cell=None, strict=False, near=None, clearance=True,
              along_wall=False, **props):
        """Wall/ceiling-mounted entity. Without an explicit cell it picks a wall cell whose footprint fits the room,
        does not overlap furniture or other floor-standing things (for low and floor-standing mounts), stays out of
        doorway clearance, is reachable (adjacent to a doorway-reachable cell; one more cell away for mounts above
        counter height) and, for floor-standing mounts, does not cut access to anything. If nothing qualifies it
        relaxes reachability; strict=True returns None instead of a last-resort cell. along_wall=True (opt-in, not yet
        used by the generator: it changes layouts) lays an oblong footprint along a wall of its cell (wall_layouts)
        instead of keeping the type's orientation, which can run perpendicular into the room."""
        record = self.reg.make(type_name, rid, **props)
        record["mounted"] = props.get("mounted", True)
        record["blocks_path"] = props.get("blocks_path", False)
        if height is not None:
            record["mount_height_m"] = height
        if cell is not None:
            record["pos"] = list(cell)
        else:
            pos = self._mount_cell(rid, record, near=near, clearance=clearance, orient=along_wall) or \
                self._mount_cell(rid, record, relaxed=True, near=near, clearance=clearance, orient=along_wall)
            if pos is None:
                if strict:
                    return None
                pos = self._last_resort_wall_cell(rid, record)
            record["pos"] = pos
        eid = eid or self.new_id(type_name)
        self.c["entities"][eid] = record
        return eid

    def _last_resort_wall_cell(self, rid, record):
        room = self.c["rooms"][rid]
        gc, gr = geometry.grid_size(room)
        perimeter = sorted({(x, 0) for x in range(gc)} | {(x, gr - 1) for x in range(gc)} | {(0, y) for y in range(gr)} | {(gc - 1, y) for y in range(gr)})
        inside = [p for p in perimeter if all(geometry.in_grid(room, c) for c in geometry.footprint(dict(record, pos=list(p))))]
        return list(self.rng.choice(inside or perimeter))

    # ----- surfaces and containers -------------------------------------
    def on(self, surface_id, type_name, eid=None, spos=None, **props):
        record = self.reg.make(type_name, surface_id, **props)
        record["relation"] = "on"
        eid = eid or self.new_id(type_name)
        self.c["entities"][eid] = record
        spot = geometry.surface_spot(self.state(), surface_id, eid, want=spos)
        if spot is None:
            del self.c["entities"][eid]
            return None
        record["spos"] = spot
        return eid

    def inside(self, container_id, type_name, eid=None, shelf=None, **props):
        parent = self.c["entities"][container_id]
        record = self.reg.make(type_name, container_id, **props)
        record["relation"] = "in"
        size = record.get("size_m") or [0.1, 0.1, 0.1]
        inner = parent.get("inner_size_m")
        if not geometry.fits_inside(size, inner):
            return None
        if inner and "pack_orientation" not in record:
            try:
                upright = float(size[0]) <= float(inner[0]) + 1e-9 and float(size[1]) <= float(inner[1]) + 1e-9 and \
                    float(size[2]) <= float(inner[2]) + 1e-9
                upright = upright or (float(size[1]) <= float(inner[0]) + 1e-9 and float(size[0]) <= float(inner[1]) + 1e-9 and
                                      float(size[2]) <= float(inner[2]) + 1e-9)
            except (TypeError, ValueError, IndexError):
                upright = True
            if not upright:
                record["pack_orientation"] = "flat"   # only fits lying down (e.g. a flashlight in a shallow drawer)
        capacity = float(parent.get("capacity_l", 0) or 0)
        if capacity > 0:
            used = sum(float(e.get("volume_l", 0)) for e in self.c["entities"].values() if e.get("location") == container_id and e.get("relation") == "in")
            if used + float(record.get("volume_l", 0)) > capacity + 1e-9:
                return None
        heights = parent.get("shelf_heights")
        if heights:
            record["shelf"] = int(shelf) if shelf is not None else self.rng.randrange(len(heights))
        eid = eid or self.new_id(type_name)
        self.c["entities"][eid] = record
        return eid

    def hang(self, parent_id, type_name, eid=None, **props):
        parent = self.c["entities"][parent_id]
        used = sum(1 for e in self.c["entities"].values() if e.get("location") == parent_id and e.get("relation") == "hung")
        if used >= int(parent.get("hang_slots", 0)):
            return None
        record = self.reg.make(type_name, parent_id, **props)
        record["relation"] = "hung"
        eid = eid or self.new_id(type_name)
        self.c["entities"][eid] = record
        return eid

    def set(self, eid, **props):
        self.c["entities"][eid].update(copy.deepcopy(props))
        return eid
