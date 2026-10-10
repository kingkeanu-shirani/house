"""Config-level builders (house generation and scenario setup): window sills, curtains and blinds
with the window's covered value, dresser drawers, garage opener parts, padlocks and hasps.
They work on the raw config dict through a Placer and never touch a live World.
"""
import copy

from ...kernel import geometry
from . import common as C
from . import coverings as V

DRAWER_LAYOUT = [("top drawer", 0.62, "socks_underwear"), ("middle drawer", 0.38, "tops"), ("bottom drawer", 0.14, "bottoms")]


def window_size(rec):
    size = rec.get("size_m") or [1.0, 0.1, 1.2]
    return C.num(size[0], 1.0), C.num(size[2], 1.2)


def wall_rot(c, win):
    """rot for a wall unit at a window: 90 on a west/east wall (so its width runs along the wall), else 0. A corner
    cell lies on two walls: the window's own orientation decides (rot 90/270 runs along a west/east wall; the
    generator's windows have rot 0, i.e. they run along the north/south wall), so the unit hangs on the same wall
    as its window instead of reaching along the other wall into the room."""
    room = c["rooms"].get(win.get("location")) or {}
    if not room or not win.get("pos"):
        return 0
    cols, rows = geometry.grid_size(room)
    x, y = int(win["pos"][0]), int(win["pos"][1])
    if x in (0, cols - 1) and y in (0, rows - 1):
        return 90 if win.get("rot", 0) in (90, 270) else 0
    inward, _ = geometry.wall_inward(room, win["pos"])
    return 90 if inward[0] else 0


def keep_on_grid(c, rec):
    """Shift a wall unit back along its wall so its whole footprint stays inside the room's grid (a window in the
    last cell of a wall would otherwise have its blind or sill run past the corner, off the grid)."""
    room = c["rooms"].get(rec.get("location")) or {}
    if not room or not rec.get("pos"):
        return rec
    cols, rows = geometry.grid_size(room)
    fc, fr = geometry.footprint_size(rec)
    x, y = int(rec["pos"][0]), int(rec["pos"][1])
    shifted = [max(0, min(x, cols - fc)), max(0, min(y, rows - fr))]
    if shifted != [x, y] and not _wall_space_taken(c, dict(rec, pos=shifted)):
        rec["pos"] = shifted
    # else: the neighbouring window's unit already uses that wall space (two windows a cell apart at a grid edge):
    # stay at the window (the window itself runs past the edge, a generator layout issue)
    return rec


def _deep_mount(rec):
    """(bottom, top) of a wall unit deep enough to clash with another one (the layout audit's "mount overlap")."""
    try:
        h = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0)
        size = rec.get("size_m") or [0.1, 0.1, 0.1]
        top, depth = h + float(size[2]), min(float(size[0]), float(size[1]))
    except (TypeError, ValueError, IndexError):
        return None
    if not rec.get("mounted") or depth < 0.1 or not 0.5 <= h < 2.0 or rec.get("category") in ("outlet", "light", "window", "drawer"):
        return None
    return h, top


def _wall_space_taken(c, rec):
    """Another deep wall unit (a neighbouring window's curtain or sill, a shelf) shares this unit's cells at an
    overlapping height."""
    mine = _deep_mount(rec)
    if mine is None:
        return False
    cells = set(geometry.footprint(rec))
    for eid in sorted(c["entities"]):
        other = c["entities"][eid]
        if other is rec or other.get("location") != rec.get("location") or not other.get("pos") or "connects" in other:
            continue
        if other.get("category") == "window" and eid != rec.get("window"):
            # the wall space a unit like this one would take at the other window (hung later in id order)
            if cells & set(geometry.footprint(dict(rec, pos=list(other["pos"])))):
                return True
            continue
        if rec.get("window") and other.get("window") == rec.get("window") and other.get("category") == rec.get("category"):
            continue           # the unit this one replaces
        span = _deep_mount(other)
        if span and span[0] < mine[1] and mine[0] < span[1] and cells & set(geometry.footprint(other)):
            return True
    return False


def standing_tops(c, rid):
    """{cell: top height (m)} of the floor-standing pieces in a room (furniture, appliances, floor items)."""
    tops = {}
    for eid in sorted(c["entities"]):
        rec = c["entities"][eid]
        if rec.get("location") != rid or not rec.get("pos") or not geometry.floor_standing(rec):
            continue
        try:
            top = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0) + float((rec.get("size_m") or [0, 0, 0])[2])
        except (TypeError, ValueError, IndexError):
            continue
        for cell in geometry.footprint(rec):
            tops[cell] = max(tops.get(cell, 0.0), top)
    return tops


def blocked_at(c, rec, height, tops=None):
    """True when a floor-standing piece in front of a wall unit reaches above ``height`` (it would hide it)."""
    tops = standing_tops(c, rec.get("location")) if tops is None else tops
    return any(tops.get(cell, 0.0) > height for cell in geometry.footprint(rec))


def sill_record(c, p, wid, depth=0.18):
    win = c["entities"][wid]
    width, _ = window_size(win)
    rec = p.reg.make("window_sill", win["location"], window=wid, label="{} sill".format(win.get("label", "window")),
                     size_m=[width, depth, 0.03], surface_size_m=[width, depth])
    rec["pos"] = list(win.get("pos") or [0, 0])
    rec["mounted"] = True
    rec["blocks_path"] = False
    rec["mount_height_m"] = C.num(win.get("mount_height_m"), 0.9)
    if wall_rot(c, win):
        rec["rot"] = 90
    return keep_on_grid(c, rec)


def add_sill(c, p, wid, depth=0.18):
    win = c["entities"][wid]
    room = win["location"]
    existing = [e for e in sorted(c["entities"]) if c["entities"][e].get("category") == "window_sill" and c["entities"][e].get("window") == wid]
    if existing:
        return existing[0]
    sid = p.new_id("window_sill")
    c["entities"][sid] = sill_record(c, p, wid, depth)
    return sid


def covering_record(c, p, wid, type_name, **props):
    """The record add_covering would add (not inserted)."""
    win = c["entities"][wid]
    width, height = window_size(win)
    mech = copy.deepcopy(props.pop("mech", {}))
    rec = p.reg.make(type_name, win["location"], window=wid, **props)
    base = copy.deepcopy(rec.get("mech") or {})
    base.update(mech)
    rec["mech"] = base
    rec["pos"] = list(win.get("pos") or [0, 0])
    rec["mounted"] = True
    rec["blocks_path"] = False
    if C.kind_of(rec) == "curtain":
        rec["size_m"] = [round(width + 0.4, 2), 0.12, round(height + 0.6, 2)]
    else:
        rec["size_m"] = [round(width - 0.02, 2), 0.06, round(height, 2)]
    if wall_rot(c, win):
        rec["rot"] = 90
    return keep_on_grid(c, rec)


def add_covering(c, p, wid, kind="curtain", type_name=None, **props):
    if type_name is None:
        type_name = {"curtain": "curtains", "blind": "venetian_blind", "shutter": "interior_shutters"}.get(kind, kind)
    eid = props.pop("eid", None) or p.new_id(type_name)
    c["entities"][eid] = covering_record(c, p, wid, type_name, **props)
    refresh_config(c, eid)
    return eid


def refresh_config(c, eid):
    """Recompute a covering's derived fields and its window's covered value in a raw config."""
    rec = c["entities"][eid]
    mech = rec.setdefault("mech", {})
    if C.kind_of(rec) == "curtain":
        mech.update(V.curtain_numbers(rec, c["entities"].get(V.window_of(rec))))
        rec["open"] = mech["exposed_frac"] >= 0.5
    else:
        mech.update(V.blind_numbers(rec))
        rec["open"] = mech["coverage"] < 0.5
    wid = V.window_of(rec)
    if wid in c["entities"]:
        free = 1.0
        for cov in V.coverings_of(c, wid):
            free *= 1.0 - C.clamp(V.covering_coverage(c, cov), 0.0, 1.0)
        c["entities"][wid]["covered"] = round(1.0 - free, 3)


def dresser_drawers(c, p, dresser):
    ents = c["entities"]
    existing = [e for e in sorted(ents) if ents[e].get("location") == dresser and ents[e].get("category") == "drawer"]
    if existing:
        return existing
    width = C.num(ents[dresser]["size_m"][0], 1.2)
    out = []
    for label, z, holds in DRAWER_LAYOUT:
        d = p.make("drawer", dresser, label=label, z_m=z, holds=holds, inner_size_m=[round(width - 0.25, 2), 0.42, 0.15],
                   capacity_l=round((width - 0.25) * 0.42 * 0.15 * 1000.0, 1), size_m=[round(width - 0.2, 2), 0.45, 0.18],
                   dresser_drawer=True, part_of=dresser, mass_kg=3.0)
        ents[d]["relation"] = "part"
        out.append(d)
    return out


def garage_parts(c, p, door):
    """Wall button inside the garage by the house entry, photo eyes at the threshold ends."""
    state = p.state()
    rooms = C.door_rooms(state, door)
    garage = next((r for r in rooms if c["rooms"][r].get("type") == "garage"), None)
    if garage is None:
        return {}
    out = {}
    entry = None
    for cid in sorted(c.get("connections", {})):
        conn = c["connections"][cid]
        if garage in conn["rooms"] and conn.get("door") != door:
            entry = (cid, conn)
            break
    # by the house entry (reachable from its threshold); without one, a reachable wall cell (Placer.mount)
    cell = list(entry[1]["cells"][garage]) if entry else None
    button = p.mount(garage, "garage_door_button", height=1.4, cell=cell, controls_door=door)
    if entry:
        c["entities"][button]["near_connection"] = entry[0]
    out["button"] = button
    from . import garage as G
    view = dict(state, entities=c["entities"])
    cells = G.path_cells(view, door).get(garage) or []
    if cells:
        for i, cell in enumerate((cells[0], cells[-1])):
            eye = p.mount(garage, "garage_photo_eye", height=0.15, cell=list(cell), door=door, label="safety sensor ({})".format("left" if i == 0 else "right"))
            c["entities"][eye]["display"] = "steady green"
            out.setdefault("eyes", []).append(eye)
    return out


def doors_where(c, pred):
    return [e for e in sorted(c["entities"]) if c["entities"][e].get("connects") and pred(c["entities"][e])]


def find_door(c, kind, room=None, rng=None):
    ents = c["entities"]
    rooms = c["rooms"]

    def outdoorish(r):
        return rooms[r].get("outdoor") or rooms[r].get("shared")
    if kind == "front":
        found = doors_where(c, lambda r: r.get("label") in ("front door", "apartment door"))
    elif kind == "back":
        found = doors_where(c, lambda r: r.get("label") == "back door")
    elif kind == "exterior":
        found = doors_where(c, lambda r: any(outdoorish(x) for x in r["connects"]) and r.get("category") == "door" and C.kind_of(r) != "overhead_door")
    elif kind == "garage_entry":
        found = doors_where(c, lambda r: r.get("label") == "garage entry door")
    elif kind == "garage":
        found = doors_where(c, lambda r: C.kind_of(r) == "overhead_door")
    elif kind in ("closet", "coat_closet", "linen_closet", "utility_closet", "pantry", "shed", "bathroom", "bedroom", "master_bedroom", "office", "laundry_room"):
        types = {"closet": ("coat_closet", "linen_closet", "utility_closet"),
                 "bedroom": ("bedroom", "master_bedroom", "guest_room", "nursery"),
                 "bathroom": ("bathroom", "half_bath")}.get(kind, (kind,))
        found = doors_where(c, lambda r: any(rooms[x].get("type") in types for x in r["connects"]) and r.get("category") == "door")
    elif kind == "gate":
        found = doors_where(c, lambda r: C.kind_of(r) == "gate" and not r.get("child_safety"))
    elif kind == "pool_gate":
        found = doors_where(c, lambda r: C.kind_of(r) == "gate" and r.get("child_safety"))
    elif kind == "interior":
        found = doors_where(c, lambda r: r.get("category") == "door" and not any(outdoorish(x) for x in r["connects"]) and C.kind_of(r) == "hinged_door")
    else:
        found = []
    if room:
        found = [d for d in found if room in ents[d]["connects"]]
    return found
