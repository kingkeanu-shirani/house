"""World-state helpers for the cleaning domain (all mutation goes through the journaled World).

Soil representation (true state, evaluated by tasks):
  rec["soil"]          {kind: level} aggregate = max over every part below (CONVENTIONS: soil dict)
  rec["soil_faces"]    {face: {kind: level}} per face (door sides, plate top/bottom, window inside/outside)
  rec["_shadows"]      {item_id: {kind: level}} soil hidden under an item resting on the surface
  rec["_cells"]        floors only: {"c,r": {kind: level}} per floor cell (incl. under furniture/rugs)
  rec["_soil_sync"]    last aggregate written here; lets external edits of rec["soil"] be folded in
Observations show only the visible part (see perception.py).

Other per-entity fields: damage {kind: severity}, cl_applied [products on faces, with dwell],
_chem_residue {chemical class: amount}, contamination (pathogens; CONVENTIONS), wetness 0..1,
cl_chem {product: ml} + cl_soil_g (liquid in buckets/spray bottles/basins), soil_load (dirty tools).
Global counters live in state["cleaning"] (gas_events, pollution, zone_violations, slips, ...).
"""
import copy

from ...kernel import geometry
from ...kernel.errors import ActionError, require
from . import model as M

SECTION = "cleaning"
COUNTERS = ("gas_events", "pollution", "zone_violations", "slips", "ingested", "stream_errors", "fires",
            "accidental_inputs", "hidden_sharps", "rule_violations")


# ----------------------------------------------------------------------
# basics

def now(w):
    return float(w.s.get("time_s", 0.0))


def gsec(w):
    sec = w.sec(SECTION)
    for key in COUNTERS:
        sec.setdefault(key, 0)
    sec.setdefault("log", [])
    return sec


def gget(w, key, default=0):
    return w.s.get(SECTION, {}).get(key, default)


def glog(w, kind, **data):
    sec = gsec(w)
    entry = {"t": now(w), "kind": kind}
    entry.update(data)
    sec["log"].append(entry)
    del sec["log"][:-200]
    return entry


# ----------------------------------------------------------------------
# watch lists: entities the per-tick systems must visit (kernel v2.1: systems never scan every entity).
# Writers of the dynamic fields call watch(); systems prune ids whose state went quiet. Kept in a small
# journaled top-level section so refused actions roll it back with everything else.

WATCH = "_cleaning_watch"


def watched(w, kind):
    return list((w.s.get(WATCH) or {}).get(kind) or ())


def watch(w, kind, eid):
    current = (w.s.get(WATCH) or {}).get(kind) or ()
    if eid in current:
        return
    sec = w.sec(WATCH)
    ids = set(sec.get(kind) or ())
    ids.add(eid)
    sec[kind] = sorted(ids)


def set_watched(w, kind, ids):
    ids = sorted(set(ids))
    if ids != watched(w, kind):
        w.sec(WATCH)[kind] = ids


def ck(cell):
    return "{},{}".format(int(cell[0]), int(cell[1]))


def pk(key):
    a, b = key.split(",")
    return int(a), int(b)


def rnd(x, n=4):
    return round(float(x), n)


def is_a(rec, name):
    return rec.get("type") == name or name in rec.get("is_a", [])


def type_names(rec):
    return [rec.get("type", "")] + list(rec.get("is_a", []))


def label_has(rec, *words):
    text = " ".join([str(rec.get("label", "")), str(rec.get("type", ""))]).lower()
    return any(word in text for word in words)


BIN_CATEGORIES = ("waste_container", "waste_cart", "compost_bin", "hazwaste_bin", "trash_can", "wastebasket", "recycling_bin",
                  "trash_bin", "waste_bin", "bin")


def is_waste_container(rec):
    """A container for a waste stream. Many domains put `waste_stream` on ITEMS (the stream an egg, a battery,
    an envelope belongs to when discarded), so the field alone does not make something a bin."""
    if not rec or not rec.get("waste_stream"):
        return False
    if rec.get("accepts_any_waste") or rec.get("waste_bin") or rec.get("wheeled_cart"):
        return True
    names = type_names(rec)
    if "waste_container" in names or "waste_cart" in names or "compost_bin" in names:
        return True
    return rec.get("category") in BIN_CATEGORIES and bool(rec.get("container") or rec.get("accepts_items"))


def waste_containers(w):
    return w.static_index("cleaning.waste_containers", is_waste_container)


def room_of(w, eid):
    return w.root_room(eid)


def robot_room(w):
    return w.s["robot"]["room"]


def people_in(w, rid):
    return sorted(pid for pid, p in w.s.get("people", {}).items() if p.get("location") == rid)


def pets_in(w, rid):
    return sorted(pid for pid, p in w.s.get("pets", {}).items() if p.get("location") == rid)


class StateView(object):
    """A read-only stand-in for the World over a bare state, for hooks that receive only the state (perceivable_value,
    visible_floor_hazard): ent, children, root_room and robot; no static_index, so static_ids scans."""

    def __init__(self, state):
        self.s = state

    def ent(self, eid):
        return self.s["entities"][eid]

    def children(self, eid):
        return sorted(e for e, r in self.s["entities"].items() if r.get("location") == eid)

    def root_room(self, eid):
        from ...kernel.predicates import root_room
        return root_room(self.s, eid)

    @property
    def robot(self):
        return self.s["robot"]


# ----------------------------------------------------------------------
# soil parts (faces, shadows, floor cells)

def _part_maps(rec):
    maps = []
    for key in ("soil_faces", "_shadows", "_cells"):
        value = rec.get(key)
        if isinstance(value, dict) and value:
            maps.append((key, value))
    return maps


def has_parts(rec):
    return bool(_part_maps(rec))


def faces_of(rec):
    declared = list(rec.get("faces") or [])
    for face in sorted((rec.get("soil_faces") or {}).keys()):
        if face not in declared:
            declared.append(face)
    return declared


def primary_face(rec):
    faces = faces_of(rec)
    return faces[0] if faces else "main"


def soil_dict(rec):
    soil = rec.get("soil")
    if isinstance(soil, dict):
        return soil
    if isinstance(soil, (int, float)) and soil:
        return {"dirt": float(soil)}
    return {}


def reconcile(w, eid):
    """Fold edits that other domains made directly to rec['soil'] into the parts."""
    rec = w.ent(eid)
    if not has_parts(rec):
        if not isinstance(rec.get("soil"), dict):
            w.mut(eid)["soil"] = soil_dict(rec)
        return
    soil = soil_dict(rec)
    last = rec.get("_soil_sync")
    if last is None:
        # first contact: the parts are the truth; soil above them was added by someone else
        parts = _parts_aggregate(rec)
        m = w.mut(eid)
        for kind in sorted(soil):
            delta = float(soil[kind]) - float(parts.get(kind, 0.0))
            if isinstance(soil[kind], (int, float)) and delta > 1e-9:
                for part in _default_parts(m):
                    part[kind] = rnd(min(1.0, float(part.get(kind, 0.0)) + delta))
        _write_aggregate(m)
        return
    kinds = sorted(set(soil) | set(last))
    changed = [k for k in kinds if abs(float(soil.get(k, 0.0)) - float(last.get(k, 0.0))) > 1e-9]
    if not changed:
        return
    m = w.mut(eid)
    for kind in changed:
        new, old = float(soil.get(kind, 0.0)), float(last.get(kind, 0.0))
        if new > old:
            delta = new - old
            for part in _default_parts(m):
                part[kind] = rnd(min(1.0, float(part.get(kind, 0.0)) + delta))
        else:
            for _, mapping in _part_maps(m):
                for part in mapping.values():
                    if float(part.get(kind, 0.0)) > new:
                        part[kind] = rnd(new)
    _write_aggregate(m)


def _parts_aggregate(rec):
    agg = {}
    for _, mapping in _part_maps(rec):
        for part in mapping.values():
            for kind, level in part.items():
                if isinstance(level, (int, float)) and level > agg.get(kind, 0.0):
                    agg[kind] = float(level)
    return agg


def _default_parts(m):
    if isinstance(m.get("_cells"), dict) and m["_cells"]:
        return [m["_cells"][k] for k in sorted(m["_cells"])]
    faces = m.setdefault("soil_faces", {})
    face = (m.get("faces") or sorted(faces) or ["main"])[0]
    return [faces.setdefault(face, {})]


def _write_aggregate(m):
    agg = {}
    for _, mapping in _part_maps(m):
        for part in mapping.values():
            for kind, level in part.items():
                if isinstance(level, (int, float)) and level > agg.get(kind, 0.0):
                    agg[kind] = float(level)
    m["soil"] = {k: rnd(v) for k, v in sorted(agg.items()) if v > 0.0005}
    m["_soil_sync"] = dict(m["soil"])
    if isinstance(m.get("_cells"), dict):
        counts = {}
        for part in m["_cells"].values():
            for kind, level in part.items():
                if isinstance(level, (int, float)) and level >= M.CLEAN:
                    counts[kind] = counts.get(kind, 0) + 1
        m["_cell_counts"] = dict(sorted(counts.items()))


def sync(w, eid):
    m = w.mut(eid)
    for name, mapping in _part_maps(m):
        for key in list(mapping):
            part = mapping[key]
            for kind in [k for k, v in part.items() if not isinstance(v, (int, float)) or v <= 0.0005]:
                del part[kind]
            if not part and name == "_cells":
                del mapping[key]
    _write_aggregate(m)


def ensure_faces(w, eid):
    """Make sure the entity keeps soil per face (needed once shadows or several faces exist)."""
    reconcile(w, eid)
    rec = w.ent(eid)
    if isinstance(rec.get("soil_faces"), dict) and rec["soil_faces"]:
        return
    m = w.mut(eid)
    faces = list(m.get("faces") or ["main"])
    base = {k: v for k, v in soil_dict(m).items() if isinstance(v, (int, float))}
    m["soil_faces"] = {faces[0]: base}
    for face in faces[1:]:
        m["soil_faces"].setdefault(face, {})
    _write_aggregate(m)


def face_soil(rec, face):
    faces = rec.get("soil_faces")
    if isinstance(faces, dict) and faces:
        return faces.get(face, {})
    if face in (None, "main", primary_face(rec)):
        return soil_dict(rec)
    return {}


def mut_face(w, eid, face):
    ensure_faces(w, eid)
    m = w.mut(eid)
    return m["soil_faces"].setdefault(face, {})


def add_face_soil(w, eid, face, kind, amount):
    part = mut_face(w, eid, face)
    part[kind] = rnd(M.clamp(float(part.get(kind, 0.0)) + float(amount)))
    sync(w, eid)
    return part[kind]


def items_on(w, surface_id):
    """Items resting on a surface, or standing in an open basin/container (sink full of dishes)."""
    rec = w.ent(surface_id)
    rel = ("on", "in") if (rec.get("water_source") or rec.get("basin")) else ("on",)
    return [c for c in w.children(surface_id) if w.ent(c).get("relation", "on") in rel and w.ent(c).get("relation") != "part"]


def ensure_shadows(w, surface_id, face=None):
    """Record soil under items currently resting on a surface (lazily, at cleaning time)."""
    rec = w.ent(surface_id)
    resting = items_on(w, surface_id)
    if not resting:
        return []
    face = face or primary_face(rec)
    free = face_soil(rec, face)
    missing = [i for i in resting if i not in (rec.get("_shadows") or {})]
    if missing and free:
        ensure_faces(w, surface_id)
        m = w.mut(surface_id)
        shadows = m.setdefault("_shadows", {})
        free = m["soil_faces"].get(face, {})
        for item in missing:
            shadows[item] = {k: v for k, v in free.items() if isinstance(v, (int, float))}
        sync(w, surface_id)
    return resting


def exposed_shadows(w, surface_id):
    rec = w.ent(surface_id)
    resting = set(items_on(w, surface_id))
    return sorted(k for k in (rec.get("_shadows") or {}) if k not in resting)


def covered_shadows(w, surface_id):
    rec = w.ent(surface_id)
    resting = set(items_on(w, surface_id))
    return sorted(k for k in (rec.get("_shadows") or {}) if k in resting)


def visible_soil(w, eid):
    """Soil a standing robot can see: faces + exposed shadows (not under items); floors: visible cells."""
    rec = w.ent(eid)
    if isinstance(rec.get("_cells"), dict) and rec["_cells"]:
        vis = floor_visibility(w, eid)
        agg = {}
        for key, part in rec["_cells"].items():
            if vis.get(key) != "visible":
                continue
            for kind, level in part.items():
                agg[kind] = max(agg.get(kind, 0.0), level)
        return {k: rnd(v) for k, v in sorted(agg.items()) if v > 0.0005}
    if not has_parts(rec):
        return dict(soil_dict(rec))
    agg = {}
    for part in (rec.get("soil_faces") or {}).values():
        for kind, level in part.items():
            agg[kind] = max(agg.get(kind, 0.0), level)
    shadows = rec.get("_shadows") or {}
    for item in exposed_shadows(w, eid):
        for kind, level in shadows[item].items():
            agg[kind] = max(agg.get(kind, 0.0), level)
    return {k: rnd(v) for k, v in sorted(agg.items()) if v > 0.0005}


def total_soil(rec, kinds=None):
    soil = soil_dict(rec)
    return sum(v for k, v in soil.items() if isinstance(v, (int, float)) and (kinds is None or k in kinds))


# ----------------------------------------------------------------------
# floors

def is_floor(rec):
    return rec.get("category") == "floor" or "floor_surface" in type_names(rec)


def is_stairs(rec):
    return rec.get("category") == "stairs" or "stair_run" in type_names(rec)


def floor_of(w, rid):
    eid = "floor_" + rid
    if eid in w.s["entities"] and is_floor(w.ent(eid)):
        return eid
    for other in sorted(w.s["entities"]):
        rec = w.s["entities"][other]
        if rec.get("location") == rid and is_floor(rec):
            return other
    return None


def floor_area_m2():
    return geometry.CELL * geometry.CELL


CLEARANCE = {  # gap under furniture (m); measured household typicals
    "dining_table": 0.65, "table": 0.6, "desk": 0.6, "workbench": 0.45, "outdoor_table": 0.6, "coffee_table": 0.12,
    "side_table": 0.35, "nightstand": 0.12, "chair": 0.4, "bench": 0.32, "outdoor_chair": 0.32, "high_chair": 0.45,
    "armchair": 0.08, "sofa": 0.11, "bed": 0.2, "crib": 0.25, "dresser": 0.07, "tv_stand": 0.06, "shoe_rack": 0.05,
    "shelf_unit": 0.1, "closet_shelving": 0.1, "bookshelf": 0.0, "cabinet": 0.0, "wardrobe": 0.04, "closet": 0.0,
    "toy_box": 0.0, "counter": 0.0, "robot_dock": 0.0,
}


def clearance_m(rec):
    if rec.get("clearance_m") is not None:
        return float(rec["clearance_m"])
    for name in type_names(rec):
        if name in CLEARANCE:
            return CLEARANCE[name]
    if rec.get("category") == "appliance" or rec.get("appliance") is not None:
        return 0.02
    return 0.05


def covers_floor(rec):
    """True for things standing on the floor whose footprint hides floor cells."""
    if not rec.get("pos") or rec.get("mounted") or "connects" in rec:
        return False
    if is_floor(rec) or is_stairs(rec):
        return False
    if float(rec.get("mount_height_m", 0.0)) > 0.3:
        return False
    return bool(rec.get("blocks_path")) or rec.get("category") == "rug" or bool(rec.get("ground_layer"))


def static_ids(w, name, predicate):
    """w.static_index when available (a live World); a plain sorted scan for setup-time views."""
    index = getattr(w, "static_index", None)
    if index is not None:
        return index(name, predicate)
    return [e for e in sorted(w.s["entities"]) if predicate(w.s["entities"][e])]


def _may_cover_floor(rec):
    """Static superset of covers_floor: furniture, rugs and anything marked blocks_path / ground_layer."""
    if "connects" in rec or is_floor(rec) or is_stairs(rec):
        return False
    return bool(rec.get("blocks_path") or rec.get("category") == "rug" or rec.get("ground_layer") or not rec.get("portable"))


def floor_layout(w, rid):
    """cell key -> ("open", None) | ("under", fid) | ("blocked", fid) | ("rug", rug_id); plus perimeter info."""
    room = w.s["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    layout = {}
    for y in range(rows):
        for x in range(cols):
            layout["{},{}".format(x, y)] = ("open", None)
    ents = w.s["entities"]
    for eid in static_ids(w, "cleaning.floor_cover_candidates", _may_cover_floor):
        rec = ents.get(eid)
        if rec is None or rec.get("location") != rid or not covers_floor(rec):
            continue
        cells = geometry.footprint(rec)
        if rec.get("category") == "rug" or (rec.get("ground_layer") and rec.get("portable")):
            kind = ("rug", eid)
        elif clearance_m(rec) <= 0.0:
            kind = ("blocked", eid)
        else:
            kind = ("under", eid)
        rank = {"open": 0, "rug": 1, "under": 2, "blocked": 3}
        for cell in cells:
            key = ck(cell)
            if key in layout and rank[kind[0]] > rank[layout[key][0]]:
                layout[key] = kind
    return layout


def perimeter_cells(w, rid):
    cols, rows = geometry.grid_size(w.s["rooms"][rid])
    edge = set()
    for x in range(cols):
        edge.add("{},{}".format(x, 0))
        edge.add("{},{}".format(x, rows - 1))
    for y in range(rows):
        edge.add("{},{}".format(0, y))
        edge.add("{},{}".format(cols - 1, y))
    return edge


def corner_cells(w, rid):
    cols, rows = geometry.grid_size(w.s["rooms"][rid])
    return {"0,0", "{},0".format(cols - 1), "0,{}".format(rows - 1), "{},{}".format(cols - 1, rows - 1)}


OPEN_UNDER_M = 0.35     # furniture standing this high (tables, chairs) does not hide or trap the floor under it


def floor_visibility(w, floor_id):
    """cell key -> 'visible' | 'under:<fid>' | 'rug:<id>' | 'blocked'."""
    rid = w.ent(floor_id)["location"]
    result = {}
    for key, (status, src) in floor_layout(w, rid).items():
        if status == "open" or (status == "under" and clearance_m(w.ent(src)) >= OPEN_UNDER_M):
            result[key] = "visible"
        else:
            result[key] = "{}:{}".format(status, src)
    return result


def cell_soil(rec, key):
    return (rec.get("_cells") or {}).get(key, {})


def mut_cell(w, floor_id, key):
    reconcile(w, floor_id)
    m = w.mut(floor_id)
    cells = m.setdefault("_cells", {})
    return cells.setdefault(key, {})


def add_cell_soil(w, floor_id, key, kind, amount):
    part = mut_cell(w, floor_id, key)
    part[kind] = rnd(M.clamp(float(part.get(kind, 0.0)) + float(amount)))
    sync(w, floor_id)


def floor_soil_cells(rec, kinds=None, threshold=M.CLEAN):
    found = []
    for key, part in sorted((rec.get("_cells") or {}).items()):
        if any(v >= threshold for k, v in part.items() if kinds is None or k in kinds):
            found.append(key)
    return found


# ----------------------------------------------------------------------
# water films and wet floors (room fields: water_film {cell: mm}, wet_cells [[c, r], ...])
#
# `wet_cells` is the shared slip-hazard list (CONVENTIONS 2): any domain may add or remove cells. Cleaning keeps a
# depth per cell in `water_film` and evaporates it. The two are kept in sync both ways (`sync_wet`): a cell another
# domain marked wet without a film joins the film at FOREIGN_WET_MM, so it dries like any wet floor; a cell another
# domain removed from wet_cells after cleaning wrote them (`_cl_wet_sync`: its own mop-up/absorb/evaporation) leaves
# the film. Adopted cells are listed in `_cl_foreign_wet`: their depth is a guess, so cleaning never turns them into harms
# (water damage, resident slips, trapped moisture); the domain that made them owns those consequences. Rooms with
# standing flood water (`_flood_cm`, safety) are left alone: that water is not a film and its level is owned there.

# a wet patch of unknown depth: the writers are leaks, overflows, condensate hoses, bowls and spills, i.e. puddles, so
# the depth of a water spill (floors.SPILL thickness 1.5 mm): ~8 h to dry indoors, ~3 h ventilated, ~1 h in sun outdoors
FOREIGN_WET_MM = 1.5


def _wet_keys(cells):
    out = set()
    for cell in cells or ():
        try:
            out.add(ck(cell))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def foreign_wet(room):
    """Film cells adopted from other domains' wet_cells (never a cleaning harm)."""
    return set((room or {}).get("_cl_foreign_wet") or ())


def _write_wet(room, film, foreign, old_keys=None):
    """wet_cells = the film's cells; with old_keys (the film's cells before this change) wet cells that were not
    film cells stay listed (flooded rooms are never synced, so their flood cells survive cleaning's writes)."""
    keys = set(film)
    if old_keys is not None:
        keys |= _wet_keys(room.get("wet_cells")) - set(old_keys)
    room["wet_cells"] = [list(pk(k)) for k in sorted(keys, key=lambda s: pk(s)[::-1])]
    room["_cl_wet_sync"] = True
    foreign = sorted(k for k in foreign if k in film)
    if foreign:
        room["_cl_foreign_wet"] = foreign
    else:
        room.pop("_cl_foreign_wet", None)


def sync_wet(w, rid):
    """Fold other domains' edits of rooms.<rid>.wet_cells into the water film (see above). True if anything changed."""
    room = w.s["rooms"].get(rid)
    if room is None or float(room.get("_flood_cm", 0.0) or 0.0) > 0.0:
        return False
    film = room.get("water_film") or {}
    wet = _wet_keys(room.get("wet_cells"))
    added = sorted(k for k in wet if k not in film)
    gone = sorted(k for k in film if k not in wet)
    # once cleaning has written wet_cells here, a film cell missing from them was cleared by another domain; before
    # that, it is a film the scene set up without listing wet_cells (list it)
    removed = gone if "_cl_wet_sync" in room else []
    unlisted = [] if "_cl_wet_sync" in room else gone
    if not added and not removed and not unlisted:
        return False
    m = w.room_m(rid)
    film = m.setdefault("water_film", {})
    foreign = foreign_wet(m)
    for key in added:
        film[key] = FOREIGN_WET_MM
        foreign.add(key)
    for key in removed:
        film.pop(key, None)
        foreign.discard(key)
    _write_wet(m, film, foreign)
    return True


def set_film(w, rid, key, mm):
    sync_wet(w, rid)
    room = w.room_m(rid)
    film = room.setdefault("water_film", {})
    foreign = foreign_wet(room)
    before = set(film)
    old = float(film.get(key, 0.0))
    if mm <= 0.002:
        film.pop(key, None)
    else:
        film[key] = rnd(mm, 5)
        if mm > old + 1e-9:
            foreign.discard(key)     # water cleaning's own mechanics added: its consequences are cleaning's
    _write_wet(room, film, foreign, old_keys=before)


def film_of(w, rid, key):
    room = w.s["rooms"][rid]
    film = room.get("water_film") or {}
    if key not in film and key in _wet_keys(room.get("wet_cells")) and float(room.get("_flood_cm", 0.0) or 0.0) <= 0.0:
        return FOREIGN_WET_MM        # marked wet by another domain, not adopted yet
    return float(film.get(key, 0.0))


# ----------------------------------------------------------------------
# damage, contamination, residues

def odor_level(rec, kind=None):
    """CONVENTIONS 5: `odor` is a dict {kind: intensity}; legacy floats read as one unnamed kind."""
    value = (rec or {}).get("odor")
    if isinstance(value, dict):
        if kind is not None:
            return float(value.get(kind, 0.0) or 0.0)
        return max([float(v or 0.0) for v in value.values() if isinstance(v, (int, float))] or [0.0])
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def set_odor(w, eid, kind, value):
    """Write one odor kind (other kinds kept; a legacy float is folded into this kind)."""
    m = w.mut(eid)
    old = m.get("odor")
    out = dict(old) if isinstance(old, dict) else {}
    value = rnd(M.clamp(float(value)))
    if value > 1e-4:
        out[kind] = value
    else:
        out.pop(kind, None)
    m["odor"] = {k: out[k] for k in sorted(out)}
    return value


def damage_of(rec):
    """The entity's damage as a dict {kind: severity} (CONVENTIONS 5); legacy lists/numbers are converted."""
    current = (rec or {}).get("damage")
    if isinstance(current, dict):
        return dict(current)
    if isinstance(current, (list, tuple)):
        out = {}
        for item in current:
            if isinstance(item, str):
                out[item] = max(out.get(item, 0.0), 1.0)
            elif isinstance(item, dict) and item.get("kind"):
                out[str(item["kind"])] = float(item.get("severity", 1.0) or 1.0)
        return out
    if isinstance(current, (int, float)) and not isinstance(current, bool) and current:
        return {"general": float(current)}
    return {}


def damage(w, eid, kind, severity, note=""):
    m = w.mut(eid)
    current = m.get("damage")
    if not isinstance(current, dict):
        current = damage_of(m)
        m["damage"] = current
    before = float(current.get(kind, 0.0))
    current[kind] = rnd(M.clamp(before + float(severity)))
    if before < 0.15 <= current[kind]:
        w.stat("damage")
        w.emit("damage", target=eid, damage=kind, note=note)
        glog(w, "damage", target=eid, damage=kind, note=note)
    return current[kind]


def break_item(w, eid, note="", cause="cleaning", by=None):
    """Break an item through the kernel (TIER1_PHYSICS 10.3: physics.break_entity): broken, condition and
    damage.broken; a brittle item also shows `shattered` and turns sharp; its liquid runs out; the phys.break
    consequence (a note in the step's message, the camera's visual event, the sound notice) and the phys.broke hooks.
    Attribution is the kernel's (by=None: the action phase, or something the robot arranged, is the robot's and counts
    breakages and damage; a scene's own soaking filter is not). Returns True when it broke now."""
    from ...kernel import physics
    if physics.break_entity(w, eid, cause, by=by):
        glog(w, "broken", target=eid, note=note)
        return True
    return False


def add_contamination(w, eid, kind, amount):
    m = w.mut(eid)
    cont = m.get("contamination")
    if not isinstance(cont, dict):
        cont = m["contamination"] = {}
    cont[kind] = rnd(M.clamp(float(cont.get(kind, 0.0)) + float(amount)))
    return cont[kind]


def scale_contamination(w, eid, factor, kinds=("bacteria", "raw_meat", "biohazard"), face=None):
    """Reduce pathogens. With per-face contamination (cont_faces) only that face changes."""
    rec = w.ent(eid)
    faces = rec.get("cont_faces")
    if isinstance(faces, dict) and faces and face is not None and face in faces:
        m = w.mut(eid)
        part = m["cont_faces"][face]
        for kind in kinds:
            if kind in part:
                part[kind] = rnd(float(part[kind]) * factor, 5)
        agg = {}
        for p in m["cont_faces"].values():
            for kind, level in p.items():
                agg[kind] = max(agg.get(kind, 0.0), float(level))
        cont = m.setdefault("contamination", {})
        for kind in sorted(set(agg) | set(k for k in cont if k in kinds)):
            cont[kind] = rnd(agg.get(kind, 0.0), 5)
        return
    cont = rec.get("contamination")
    if not isinstance(cont, dict) or not any(cont.get(k, 0) > 0 for k in kinds):
        return
    m = w.mut(eid)
    for kind in kinds:
        if kind in m["contamination"]:
            m["contamination"][kind] = rnd(float(m["contamination"][kind]) * factor, 5)
    if isinstance(m.get("cont_faces"), dict):
        for part in m["cont_faces"].values():
            for kind in kinds:
                if kind in part:
                    part[kind] = rnd(float(part[kind]) * factor, 5)


def log_product(w, product, target):
    """Every product used (for constraint tasks such as 'clean it without bleach')."""
    sec = gsec(w)
    used = sec.setdefault("products_used", [])
    if product not in used:
        used.append(product)
    on = sec.setdefault("products_on", {})
    lst = on.setdefault(target, [])
    if product not in lst:
        lst.append(product)


def contamination_of(rec):
    cont = rec.get("contamination")
    return cont if isinstance(cont, dict) else {}


def add_residue(w, eid, classes, amount=1.0):
    if not classes:
        return
    m = w.mut(eid)
    res = m.setdefault("_chem_residue", {})
    for cls, power in sorted(classes.items()):
        res[cls] = rnd(M.clamp(float(res.get(cls, 0.0)) + float(power) * float(amount)))


def scale_residue(w, eid, factor):
    rec = w.ent(eid)
    if not rec.get("_chem_residue"):
        return
    m = w.mut(eid)
    for cls in sorted(m["_chem_residue"]):
        m["_chem_residue"][cls] = rnd(float(m["_chem_residue"][cls]) * factor)
        if m["_chem_residue"][cls] < 0.005:
            del m["_chem_residue"][cls]


def residue_of(rec):
    return dict(rec.get("_chem_residue") or {})


# ----------------------------------------------------------------------
# liquids with dissolved cleaners (bucket, spray bottle, sink basin, kettle, toilet bowl)

def norm_solution(w, eid):
    """Scale dissolved chemicals and dirt when other domains removed liquid (pour/empty/drain)."""
    rec = w.ent(eid)
    liters = float(rec.get("liquid_l", 0.0) or 0.0)
    ref = rec.get("_cl_ref_l")
    if ref is None:
        if rec.get("cl_chem") or rec.get("cl_soil_g"):
            w.mut(eid)["_cl_ref_l"] = liters
        return
    ref = float(ref)
    if liters < ref - 1e-9:
        m = w.mut(eid)
        factor = liters / ref if ref > 0 else 0.0
        for product in sorted(m.get("cl_chem") or {}):
            m["cl_chem"][product] = rnd(float(m["cl_chem"][product]) * factor, 3)
            if m["cl_chem"][product] <= 0.001:
                del m["cl_chem"][product]
        if m.get("cl_soil_g"):
            m["cl_soil_g"] = rnd(float(m["cl_soil_g"]) * factor, 3)
        m["_cl_ref_l"] = liters
    elif abs(liters - ref) > 1e-9:
        w.mut(eid)["_cl_ref_l"] = liters


def solution_classes(rec):
    """Chemistry classes of a liquid: {class: strength 0..1.5} (1.0 = label strength)."""
    liters = float(rec.get("liquid_l", 0.0) or 0.0)
    classes = {}
    if liters <= 0:
        return classes
    for product, ml in sorted((rec.get("cl_chem") or {}).items()):
        info = M.PRODUCTS.get(product, {})
        if info.get("concentrate"):
            strength = (float(ml) / liters) / float(info.get("dose_ml_per_l", 10.0))
        else:
            strength = float(ml) / (liters * 1000.0)
        for cls, power in M.product_classes(product).items():
            classes[cls] = max(classes.get(cls, 0.0), power * min(1.5, strength))
    return classes


def strength_of(rec, product):
    liters = float(rec.get("liquid_l", 0.0) or 0.0)
    ml = float((rec.get("cl_chem") or {}).get(product, 0.0))
    if liters <= 0 or ml <= 0:
        return 0.0
    info = M.PRODUCTS.get(product, {})
    if info.get("concentrate"):
        return (ml / liters) / float(info.get("dose_ml_per_l", 10.0))
    return ml / (liters * 1000.0)


def turbidity(rec):
    liters = float(rec.get("liquid_l", 0.0) or 0.0)
    return float(rec.get("cl_soil_g", 0.0) or 0.0) / liters if liters > 0 else 0.0


def is_liquid_container(rec):
    return bool(rec.get("liquid_container")) or rec.get("liquid_l") is not None and rec.get("capacity_l", 0) > 0


# ----------------------------------------------------------------------
# hazards: gas, fumes, pollution

def gas_event(w, rid, hazard, source=None):
    sec = gsec(w)
    sec["gas_events"] += 1
    glog(w, "toxic_gas", room=rid, hazard=hazard, source=source)
    if rid in w.s["rooms"]:
        room = w.room_m(rid)
        air = room.setdefault("cl_air", {})
        air["gas"] = 1.0
        air["hazard"] = hazard
    w.emit("toxic_gas", room=rid, hazard=hazard, source=source)
    exposed = people_in(w, rid) + pets_in(w, rid)
    for other in adjacent_rooms(w, rid):
        exposed += people_in(w, other) + pets_in(w, other)
    w.stat("gas_exposure")


def adjacent_rooms(w, rid):
    return sorted(w.s["rooms"].get(rid, {}).get("exits", []))


def add_fumes(w, rid, amount):
    if rid not in w.s["rooms"] or amount <= 0:
        return
    room = w.room_m(rid)
    air = room.setdefault("cl_air", {})
    air["fumes"] = rnd(M.clamp(float(air.get("fumes", 0.0)) + amount, 0.0, 3.0))


def pollute(w, what, where, amount=1.0, source=None):
    sec = gsec(w)
    sec["pollution"] += 1
    glog(w, "pollution", what=what, where=where, amount=amount, source=source)
    if where in w.s["entities"]:
        m = w.mut(where)
        m["cl_pollution"] = rnd(float(m.get("cl_pollution", 0.0)) + amount)
        m.setdefault("cl_pollutants", [])
        if what not in m["cl_pollutants"]:
            m["cl_pollutants"].append(what)
    w.emit("pollution", what=what, where=where)


def _vent_source(rec):
    """Static test: things that can ventilate a room (windows, exhaust fans/hoods, exterior doors)."""
    return (rec.get("category") == "window" or bool(rec.get("exhaust_fan")) or label_has(rec, "exhaust fan", "range hood")
            or (bool(rec.get("exterior")) and rec.get("category") == "door"))


def _fan_source(rec):
    return bool(rec.get("rotating")) or label_has(rec, "fan")


def vent_sources(w):
    return w.static_index("cleaning.vent_sources", _vent_source)


def fan_sources(w):
    return w.static_index("cleaning.fan_sources", _fan_source)


def fan_running_in(w, rid):
    ents = w.s["entities"]
    for eid in fan_sources(w):
        rec = ents.get(eid)
        if rec is not None and rec.get("on") and rec.get("location") == rid:
            return True
    return False


def ventilated(w, rid):
    """Open window, running exhaust fan or open exterior door in the room."""
    ents = w.s["entities"]
    for eid in vent_sources(w):
        rec = ents.get(eid)
        if rec is None:
            continue
        if rec.get("location") != rid and rid not in (rec.get("connects") or []):
            continue
        if rec.get("category") == "window" and rec.get("open"):
            return True
        if (rec.get("exhaust_fan") or label_has(rec, "exhaust fan", "range hood")) and rec.get("on"):
            return True
        if rec.get("exterior") and rec.get("category") == "door" and rec.get("open"):
            return True
    return bool(w.s["rooms"].get(rid, {}).get("outdoor"))


# ----------------------------------------------------------------------
# tools

def tool_kind(rec):
    kind = rec.get("tool_kind")
    if kind in M.TOOLS:
        return kind
    for name in type_names(rec):
        if name in M.TOOLS:
            return name
    text = "{} {}".format(rec.get("label", ""), rec.get("type", "")).lower()
    if rec.get("mop_bucket") or any(word in text for word in ("bucket", "holder", "hook", "towel bar", "towel ring", "caddy")):
        return None     # a mop bucket, a broom holder or a towel bar is not the tool its label names
    for word, kind in (("paper towel", "paper_towel"), ("towel", "towel"), ("rag", "cotton_rag"), ("pillowcase", "pillowcase"),
                       ("sponge", "sponge"), ("brush", "scrub_brush"), ("cloth", "microfiber_cloth"), ("mop", "flat_mop"),
                       ("broom", "broom"), ("duster", "microfiber_duster"), ("wipe", "disinfecting_wipe")):
        if word in text:
            return kind
    return None


def tool_spec(rec, side=None, angle=None):
    kind = tool_kind(rec)
    spec = dict(M.TOOLS.get(kind, {"mech": 0.3, "abrasion": 0.2, "capture": 0.5, "absorb_l": 0.05, "lint": 0.2, "kind": "other"}))
    spec["tool_kind"] = kind
    if side == "scrub" and spec.get("scrub_side"):
        spec.update(spec["scrub_side"])
    if angle == "steep" and spec.get("abrasion_steep") is not None:
        spec["abrasion"] = spec["abrasion_steep"]
    for key in ("mech", "abrasion", "capture", "absorb_l", "lint", "head_m", "reach_m"):
        if rec.get("cl_" + key) is not None:
            spec[key] = float(rec["cl_" + key])
    wear = float(rec.get("wear", 0.0) or 0.0)
    if wear > 0.7:
        spec["mech"] = spec.get("mech", 0.3) * (1.0 - 0.5 * (wear - 0.7))
    return spec


def held_tool(ctx, param="tool", kinds=None, predicate=None, required=True, message=None):
    """Resolve the tool argument: an explicitly named held item, else a held item that fits."""
    w = ctx.w
    explicit = ctx.args.get(param)
    held = w.held_items()
    if explicit:
        require(explicit in held, "Hold the {} first".format(explicit if realistic(ctx) else w.s["entities"].get(explicit, {}).get("label", explicit)),
                "precondition")
        rec = w.ent(explicit)
        if kinds is not None:
            spec = tool_spec(rec)
            ok = spec.get("tool_kind") in kinds or spec.get("kind") in kinds or (predicate and predicate(rec))
            require(ok, message or "That is not a suitable tool for this", "capability")
        return explicit
    for eid in held:
        rec = w.ent(eid)
        spec = tool_spec(rec)
        if kinds is None and predicate is None:
            return eid
        if (kinds is not None and (spec.get("tool_kind") in kinds or spec.get("kind") in kinds)) or (predicate and predicate(rec)):
            return eid
    if required:
        raise ActionError(message or "Hold a suitable cleaning tool", "capability")
    return None


def hand_of(w, eid):
    """The robot hand holding eid ("left" / "right"), or None."""
    for hand, held in sorted((w.s["robot"].get("hands") or {}).items()):
        if held == eid:
            return hand
    return None


def zone_of_surface(w, eid):
    rec = w.ent(eid)
    if rec.get("cl_zone"):
        return rec["cl_zone"]
    if label_has(rec, "toilet", "urinal", "litter box", "plunger"):
        return "toilet"
    if is_floor(rec) or is_stairs(rec):
        return "floor"
    if rec.get("pet_item") or label_has(rec, "pet bowl", "dog bowl", "cat bowl"):
        return "pet"
    if is_waste_container(rec) or label_has(rec, "trash", "garbage", "compost"):
        return "waste"
    rid = w.root_room(eid)
    rtype = w.s["rooms"].get(rid, {}).get("type", "")
    if rtype in ("bathroom", "half_bath"):
        return "bathroom"
    if rtype in ("kitchen", "dining_room", "pantry") or rec.get("food_contact"):
        return "kitchen"
    return "general"


ZONE_RISK = {  # tool used in zone A, then on a surface of zone B -> bacteria transferred
    "toilet": {"default": 0.6},
    "waste": {"default": 0.4, "waste": 0.0, "toilet": 0.0, "floor": 0.1},
    "floor": {"kitchen": 0.35, "bathroom": 0.1, "general": 0.1, "pet": 0.2, "default": 0.0},
    "bathroom": {"kitchen": 0.35, "pet": 0.1, "default": 0.0, "toilet": 0.0, "floor": 0.0},
    "pet": {"kitchen": 0.25, "default": 0.0},
}


def zone_transfer(w, tool_id, surface_id):
    """Cross-contamination by a tool that carries the dirt of another zone (colour-code rules)."""
    tool = w.ent(tool_id)
    used = list(tool.get("cl_used_zones") or [])
    zone = zone_of_surface(w, surface_id)
    worst = 0.0
    for prior in used:
        table = ZONE_RISK.get(prior)
        if not table or prior == zone:
            continue
        worst = max(worst, table.get(zone, table.get("default", 0.0)))
    if worst > 0:
        add_contamination(w, surface_id, "bacteria", worst)
        if "toilet" in used:
            add_contamination(w, surface_id, "biohazard", worst * 0.5)
        gsec(w)["zone_violations"] += 1
        glog(w, "cross_contamination", tool=tool_id, surface=surface_id, zones=used, zone=zone)
    color_rule = (w.s.get("household", {}).get("cloth_colors") or {})
    color = tool.get("color")
    if color and color in color_rule and color_rule[color] not in (zone, "general") and zone != "floor":
        gsec(w)["rule_violations"] += 1
        glog(w, "color_code_violation", tool=tool_id, surface=surface_id, color=color, zone=zone)
    # contamination carried on the tool itself (raw meat juice on a cloth, etc.)
    tcont = contamination_of(tool)
    for kind in ("raw_meat", "bacteria", "biohazard"):
        if float(tcont.get(kind, 0.0)) > 0.05:
            add_contamination(w, surface_id, kind, float(tcont[kind]) * 0.5)
    scont = contamination_of(w.ent(surface_id))
    m = w.mut(tool_id)
    for kind in ("raw_meat", "bacteria", "biohazard"):
        if float(scont.get(kind, 0.0)) > 0.05:
            cont = m.setdefault("contamination", {})
            cont[kind] = rnd(M.clamp(float(cont.get(kind, 0.0)) + float(scont[kind]) * 0.5))
    if zone not in used:
        m["cl_used_zones"] = sorted(set(used) | {zone})


def sanitize_tool(w, tool_id):
    m = w.mut(tool_id)
    m["cl_used_zones"] = []
    if isinstance(m.get("contamination"), dict):
        for kind in list(m["contamination"]):
            m["contamination"][kind] = 0.0


def load_tool(w, tool_id, amount):
    m = w.mut(tool_id)
    m["soil_load"] = rnd(M.clamp(float(m.get("soil_load", 0.0) or 0.0) + amount))
    m["wear"] = rnd(M.clamp(float(m.get("wear", 0.0) or 0.0) + 0.004))


# ----------------------------------------------------------------------
# misc

def household(w):
    return w.s.get("household", {}) or {}


def in_quiet_hours(w):
    from ...kernel import clock
    rules = household(w).get("quiet_hours") or [22.0, 7.0]
    hour = clock.hour(w.s)
    start, end = float(rules[0]), float(rules[1])
    return hour >= start or hour < end if start > end else start <= hour < end


def copy_soil(soil):
    return {k: float(v) for k, v in (soil or {}).items() if isinstance(v, (int, float))}


def deep(value):
    return copy.deepcopy(value)


# ----------------------------------------------------------------------
# realistic perception (kernel v2.4): what action messages and inspect results may say. Every helper returns the
# oracle text unchanged when the env runs oracle perception (ctx.realistic is False), so oracle output is
# byte-for-byte what it was.

def realistic(ctx):
    """True when the action runs under realistic perception."""
    return bool(ctx is not None and getattr(ctx, "realistic", False))


def knows(ctx, eid, level="read"):
    """Oracle: always True. Realistic: the robot's knowledge of the entity as a whole is at least `level` (a read
    label, an inspection); knowledge lapses when the object moves or changes."""
    realism = getattr(getattr(ctx, "env", None), "realism", None)
    if realism is None:
        return True
    from ...kernel.realism import LEVELS
    rec = ctx.w.s["entities"].get(eid)
    if rec is None:
        return False
    return realism.knowledge.entity_level(eid, rec) >= LEVELS.get(level, 1)


def label_known(ctx, bottle):
    """Oracle: True. Realistic: the bottle carries a printed label the robot has read and still knows (so its
    product, dilution and contact time are known); an unread or blank-labelled bottle is just a bottle."""
    if not realistic(ctx):
        return True
    rec = ctx.w.s["entities"].get(bottle) if bottle else None
    return bool(rec and rec.get("label_text") and knows(ctx, bottle, "read"))


def product_text(ctx, product, bottle=None):
    """How a message names the product that came out of a bottle: its name (oracle, or a labeled bottle whose label
    the robot has read and still knows); otherwise the bottle itself (its id becomes a handle), so a message never
    identifies an unread or unlabeled look-alike bottle."""
    name = str(product or "water").replace("_", " ")
    if not realistic(ctx):
        return name
    if bottle and bottle in ctx.w.s["entities"]:
        brec = ctx.w.s["entities"][bottle]
        if brec.get("label_text") and knows(ctx, bottle, "read"):
            return name
        return "what is in " + bottle
    return "the product"


def removed_text(ctx, removed, empty="no change"):
    """Soil taken off by a pass: exact per-kind amounts (oracle) or the kinds that visibly lessened (realistic)."""
    removed = removed or {}
    if not realistic(ctx):
        return ", ".join("{} -{:.2f}".format(k, v) for k, v in sorted(removed.items())) or empty
    if not removed:
        return empty
    kinds = [k.replace("_", " ") for k, v in sorted(removed.items()) if float(v) >= 0.01]
    return ("less " + ", ".join(kinds)) if kinds else "no visible change"


def liters_text(ctx, liters, digits=2):
    """A liquid quantity: exact litres (oracle) or what a camera sees (realistic)."""
    liters = float(liters or 0.0)
    if not realistic(ctx):
        return "{:.{}f} l".format(liters, digits)
    if liters <= 0.003:
        return "none"
    return "a little" if liters < 0.05 else ("some" if liters < 0.3 else "a lot")


def grams_text(ctx, grams, fmt="{:.1f} g"):
    """A debris mass: exact grams (oracle) or a visual amount (realistic)."""
    grams = float(grams or 0.0)
    if not realistic(ctx):
        return fmt.format(grams)
    if grams < 0.5:
        return "hardly anything"
    return "a pinch" if grams < 5.0 else ("a handful" if grams < 60.0 else "a lot")


def wet_text(ctx, eid, value, label="wetness"):
    """A wetness after touching it (drying, blotting): exact (oracle) or the touch reading (realistic: about +-0.04,
    kernel rendering at touch level)."""
    if not realistic(ctx):
        return "{} {:.2f}".format(label, float(value or 0.0))
    shown = ctx.percept_value(eid, "wetness", float(value or 0.0), "touch")
    if isinstance(shown, (int, float)) and not isinstance(shown, bool):
        return "{} about {:.2f}".format(label, shown)
    return "{} {}".format(label, shown if shown is not None else "unknown")


def level_word(value, edges=(0.05, 0.3, 0.6), names=("none", "faint", "noticeable", "strong")):
    """Bucket a 0..1 intensity (odor, smell, residue) into words."""
    value = float(value or 0.0)
    for edge, name in zip(edges, names):
        if value < edge:
            return name
    return names[len(edges)]
