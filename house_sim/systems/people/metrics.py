"""Programmatic room proxies (fidelity G) for hosting, tidying and arranging tasks.

Rooms with track_metrics=true get rooms.<rid>.metrics kept current by the people.room_metrics
system (computed from geometry, never set by an action):
  seats            seating places (seat furniture `seats`, unfolded folding chairs, wheelchairs excluded)
  private_visible  private/sensitive items (mail, medication, documents, valuables) not inside closed storage
  clutter          loose portable items on the floor or flagged clutter not at their home
  hidden_clutter   clutter items stuffed under/behind furniture or into a non-home container
  breakables_low   fragile items below 1.2 m outside closed storage (toddler reach)
  walkways_clear   every doorway connects to every other with a clear path (furniture, people excluded)
  door_swing_clear no portable item on a door's doorway cell, its neighbours or its leaf's sweep (geometry.door_swing)
  flame_hazards    lit flames within 0.3 m of flammable decor on the same surface
  centerpiece_ok   items flagged centerpiece on tables are under the 0.35 m sightline height
  trash_bins       open waste receptacles in the room
  coasters         coasters on tables
"""
import math

from ...kernel import geometry
from ...kernel.plugins import hook, system
from . import common as C

FRAGILE_MATERIALS = ("glass", "crystal", "porcelain", "ceramic")
PRIVATE_CATEGORIES = ("mail", "medication", "document", "care_record", "jewelry", "wallet")
FLAMMABLE = ("paper", "cardboard", "fabric", "cotton", "wool", "tissue", "polyester", "lace")


def _closed_in(state, eid):
    entities = state["entities"]
    loc = entities.get(eid, {}).get("location")
    seen = set()
    while loc in entities and loc not in seen:
        seen.add(loc)
        rec = entities[loc]
        if rec.get("openable") and not rec.get("open") and not rec.get("transparent"):
            return True
        loc = rec.get("location")
    return False


def compute(state, rid):
    by_loc = C.by_location(state)
    room_items = C.room_items(state, rid, by_loc)
    seats = 0
    private, clutter, hidden, fragile, flames, center_bad, bins, coasters = [], [], [], [], [], [], 0, 0
    for eid in room_items:
        rec = state["entities"][eid]
        if rec.get("location") == rid and (rec.get("seat") or rec.get("category") == "seat") and not rec.get("folded") \
                and rec.get("aid_kind") != "wheelchair":
            seats += int(rec.get("seats", 1) or 1)
        if not rec.get("portable"):
            if rec.get("waste_stream") and rec.get("accepts_items"):
                bins += 1
            continue
        if rec.get("location") == "robot" or rec.get("location") in state.get("people", {}):
            continue
        if (rec.get("private") or rec.get("sensitive") or rec.get("category") in PRIVATE_CATEGORIES or rec.get("mail_piece")
                or rec.get("medication") or float(rec.get("value", 0) or 0) >= 500) and not _closed_in(state, eid):
            private.append(eid)
        on_floor = rec.get("location") == rid and not rec.get("ground_layer") and rec.get("category") not in ("rug", "mobility_aid") \
            and not rec.get("mobility_aid") and not rec.get("_display")
        home = rec.get("home")
        at_home = home is not None and (rec.get("location") == home or C.room_of(state, eid) == home and home in state["rooms"])
        if (on_floor or rec.get("clutter")) and not at_home:
            clutter.append(eid)
        if rec.get("clutter") and not at_home and (rec.get("relation") in ("under", "behind") or _closed_in(state, eid)):
            hidden.append(eid)
        if (rec.get("fragile") or rec.get("material") in FRAGILE_MATERIALS or rec.get("breakable")) and not _closed_in(state, eid):
            try:
                h = geometry.height_m(state, eid)
            except Exception:
                h = 0.0
            if h < 1.2:
                fragile.append(eid)
        if (rec.get("flame") or rec.get("open_flame")) and (rec.get("lit") or rec.get("on")):
            _, fx, fy = C.entity_xy(state, eid)
            for other in room_items:
                o = state["entities"][other]
                if other == eid or o.get("material") not in FLAMMABLE:
                    continue
                if o.get("location") != rec.get("location") and other != rec.get("location"):
                    continue
                if other == rec.get("location") and o.get("material") in FLAMMABLE:
                    flames.append(eid)
                    break
                _, ox, oy = C.entity_xy(state, other)
                if math.hypot(fx - ox, fy - oy) <= 0.3 + 0.5 * max((o.get("size_m") or [0.1])[0], 0.1):
                    flames.append(eid)
                    break
        if rec.get("centerpiece") and float((rec.get("size_m") or [0, 0, 0])[2]) > 0.35:
            center_bad.append(eid)
        if rec.get("category") == "coaster":
            coasters += 1
        if rec.get("waste_stream") and rec.get("accepts_items"):
            bins += 1
    tables = {}
    for eid in room_items:
        rec = state["entities"][eid]
        if rec.get("location") != rid or not (rec.get("surface") and rec.get("accepts_items")) or rec.get("seat"):
            continue
        sw, sd = (rec.get("surface_size_m") or (rec.get("size_m") or [0.5, 0.5])[:2])[:2]
        used = 0.0
        for other in sorted(by_loc.get(eid, ())):
            o = state["entities"][other]
            if o.get("relation", "on") == "on":
                ow, od = geometry.footprint_m(o)
                if not o.get("table_cover") and not o.get("covers_surface"):
                    used += ow * od
        entry = {"free_m2": round(max(0.0, sw * sd - used), 3), "free_fraction": round(max(0.0, 1.0 - used / max(0.01, sw * sd)), 3)}
        if rec.get("station"):
            entry["open_sides"] = open_sides(state, rid, rec)
        tables[eid] = entry
    return {"seats": seats, "private_visible": private, "clutter": clutter, "hidden_clutter": hidden, "breakables_low": fragile, "tables": tables,
            "walkways_clear": walkways_clear(state, rid), "door_swing_clear": door_swing_clear(state, rid), "flame_hazards": flames,
            "centerpiece_ok": not center_bad, "trash_bins": bins, "coasters": coasters}


def open_sides(state, rid, rec):
    """How many sides of a table have a free strip of floor in front of them (a serving station needs two)."""
    cells = geometry.footprint(rec)
    if not cells:
        return 0
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    blocked = geometry.blocked_cells(state, rid, ignore=tuple(state.get("people", {})))
    cols, rows = geometry.grid_size(state["rooms"][rid])
    sides = {"west": [(min(xs) - 1, y) for y in sorted(set(ys))], "east": [(max(xs) + 1, y) for y in sorted(set(ys))],
             "north": [(x, min(ys) - 1) for x in sorted(set(xs))], "south": [(x, max(ys) + 1) for x in sorted(set(xs))]}
    count = 0
    for name, strip in sorted(sides.items()):
        if all(0 <= c[0] < cols and 0 <= c[1] < rows and c not in blocked for c in strip):
            count += 1
    return count


def _call_path_blocked(fn, state, rid):
    """path_blocked hooks take (state, rid, avoid_kinds) or, since kernel v2.3, also ignore_sources."""
    import inspect
    try:
        params = [q for q in inspect.signature(fn).parameters.values() if q.kind in (q.POSITIONAL_ONLY, q.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):
        params = []
    return fn(state, rid, (), ()) if len(params) >= 4 else fn(state, rid, ())


def walkways_clear(state, rid):
    cells = []
    for cid, conn in sorted(state.get("connections", {}).items()):
        if rid in conn.get("cells", {}):
            cells.append(tuple(conn["cells"][rid]))
    if len(cells) < 2:
        return True
    extra = []
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if rec.get("portable") and rec.get("pos") and not rec.get("ground_layer") and not geometry.blocks(rec):
            size = rec.get("size_m") or [0.1, 0.1, 0.1]
            if max(size[:2]) >= 0.3 or rec.get("floor_hazard"):
                extra.extend(geometry.footprint(rec))
    people = tuple(state.get("people", {})) + tuple(state.get("pets", {}))
    # One 4-connected flood fill from the first doorway instead of an A* per doorway: with geometry.find_path's
    # no-corner-cutting rule, diagonal moves never connect more than orthogonal ones, so reachability is the same.
    from ...kernel.plugins import HOOKS
    base = set(geometry.blocked_cells(state, rid, ignore=people))
    for _, _, fn in HOOKS.get("path_blocked", []):
        base |= {tuple(c) for c in (_call_path_blocked(fn, state, rid) or ())}
    start = cells[0]
    walls = (base | set(extra)) - {start}
    cols, rows = geometry.grid_size(state["rooms"][rid])
    seen, stack = {start}, [start]
    while stack:
        x, y = stack.pop()
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nxt[0] < cols and 0 <= nxt[1] < rows and nxt not in walls and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    for goal in cells[1:]:
        if goal == start or goal in seen:
            continue
        if goal in base or not (0 <= goal[0] < cols and 0 <= goal[1] < rows):
            return False
        x, y = goal
        if not any(n in seen for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))):
            return False
    return True


def door_swing_clear(state, rid):
    """No portable item (rugs and doormats excepted) on a door's cells in this room: the doorway cell, its four
    neighbours and, when the door swings into this room, the leaf's sweep (kernel geometry.door_swing)."""
    room = state["rooms"][rid]
    for cid, conn in sorted(state.get("connections", {}).items()):
        if rid not in conn.get("cells", {}) or not conn.get("door"):
            continue
        door = state["entities"].get(conn["door"], {})
        if door.get("category") not in ("door",):
            continue
        cx, cy = conn["cells"][rid]
        zone = {(cx + dx, cy + dy) for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)) if geometry.in_grid(room, (cx + dx, cy + dy))}
        swing_room, leaf = geometry.door_swing(state, conn["door"])
        if swing_room == rid:
            zone.update(leaf)
        for eid in geometry.room_entities(state, rid):
            rec = state["entities"][eid]
            if not rec.get("portable") or rec.get("ground_layer") or rec.get("category") in ("rug", "doormat") or rec.get("doormat"):
                continue
            if set(geometry.footprint(rec)) & zone:
                return False
    return True


@system("people.room_metrics", order=130, max_dt=30.0)
def room_metrics(w, dt, tick):
    """Metrics of track_metrics rooms, recomputed only when something new was touched this step (or every 60 s of a
    long wait): see common.needs_refresh."""
    state = w.s
    for rid in sorted(state["rooms"]):
        room = state["rooms"][rid]
        if not room.get("track_metrics"):
            continue
        if "metrics" in room and not C.needs_refresh(w, room.get("_metrics_at")):
            continue
        value = compute(state, rid)
        m = w.room_m(rid)
        m["_metrics_at"] = {"stamp": C.change_stamp(w), "time_s": C.now_s(state)}
        if room.get("metrics") != value:
            m["metrics"] = value


@hook("episode_start", order=160)
def _metrics_at_start(w, env):
    """Tracked rooms have their metrics from the first observation on (before the evaluation baseline)."""
    room_metrics(w, 0.0, None)
