"""Shared helpers for the admin_inventory domain (documents, mail, packages, stock, storage).

Nothing here mutates state except through the World passed in (journaled).
"""
import datetime
import re

from ...kernel import body, camera, clock, geometry, timeline
from ...kernel.errors import ActionError, require

SLUG_RE = re.compile(r"[^a-z0-9]+")

# Reach heights (top of what a child can grab, including tiptoe and pulling on shelf edges).
# Sources: anthropometric stature tables (CDC) * ~1.25 overhead reach factor, plus toddler climbing.
CHILD_REACH_BY_AGE = ((1, 0.55), (3, 1.05), (5, 1.25), (8, 1.5), (12, 1.75))
ADULT_REACH_M = 1.9
SEATED_REACH_M = 1.35       # wheelchair user, forward reach (ADA 48 in)
ELDER_NO_CLIMB_M = 1.7       # elderly resident who should not use a stool
TODDLER_AGE = 3
SMALL_PART_M = 0.045         # CPSC small-parts cylinder ~3.17 cm dia x 5.7 cm; use max dim < 4.5 cm

INCOMPATIBLE_CHEM = {("chlorine_bleach", "ammonia"), ("chlorine_bleach", "acid"), ("oxidizer_pool", "acid"),
                     ("oxidizer_pool", "fuel"), ("chlorine_bleach", "alcohol_solvent"), ("oxidizer_pool", "fertilizer"),
                     ("acid", "base_caustic"), ("oxidizer", "fuel")}
WASTE_BIN_WORDS = ("bin", "trash", "recycl", "compost", "garbage", "waste", "cart")
NOT_BIN_FLAGS = ("packing_fill", "shred_pile", "multipack", "stock_item", "mail_piece", "envelope", "package")


def slug(text):
    return SLUG_RE.sub("_", str(text or "").strip().lower()).strip("_")


def requester(state):
    return state.get("requester")


def person(state, pid):
    return state.get("people", {}).get(pid)


def authority(state, pid):
    p = person(state, pid)
    if not p:
        return 0
    return int(p.get("authority", 2 if p.get("role") == "adult" else (1 if p.get("role") in ("elderly", "guest") else 0)))


def is_child(p):
    return p.get("role") in ("child", "infant", "toddler") or (p.get("age") is not None and float(p.get("age")) < 13)


def child_reach_m(age):
    age = float(age if age is not None else 6)
    for limit, reach in CHILD_REACH_BY_AGE:
        if age <= limit:
            return reach
    return ADULT_REACH_M


def person_reach_m(p):
    """Comfortable reach height for a resident (used for 'within the owner's reach')."""
    if p is None:
        return ADULT_REACH_M
    if p.get("reach_m"):
        return float(p["reach_m"])
    if is_child(p):
        return child_reach_m(p.get("age"))
    mobility = (p.get("abilities") or {}).get("mobility")
    if mobility in ("wheelchair", "seated"):
        return SEATED_REACH_M
    if p.get("role") == "elderly" or mobility in ("cane", "walker"):
        return ELDER_NO_CLIMB_M
    return ADULT_REACH_M


def children(state):
    return sorted(pid for pid, p in state.get("people", {}).items() if is_child(p))


def youngest_child_age(state):
    ages = [float(p.get("age", 6)) for p in state.get("people", {}).values() if is_child(p)]
    return min(ages) if ages else None


def ancestors(state, eid):
    chain, seen = [], {eid}
    entities = state["entities"]
    location = entities.get(eid, {}).get("location")
    while location in entities and location not in seen:
        chain.append(location)
        seen.add(location)
        location = entities[location].get("location")
    return chain


def is_waste_bin(record):
    if not record or not record.get("accepts_items"):
        return False
    if record.get("waste_bin"):
        return True
    if any(record.get(flag) for flag in NOT_BIN_FLAGS):
        return False
    if not record.get("waste_stream"):
        return False
    words = " ".join(str(record.get(k, "")) for k in ("type", "category", "label")).lower()
    return any(w in words for w in WASTE_BIN_WORDS) or bool(record.get("container"))


def in_waste(state, eid):
    """True if an entity sits (at any depth) inside a trash/recycling/compost bin."""
    entities = state["entities"]
    for anc in ancestors(state, eid):
        if is_waste_bin(entities.get(anc)):
            return anc
    return None


def in_donation(state, eid):
    entities = state["entities"]
    for anc in ancestors(state, eid):
        if entities.get(anc, {}).get("donation"):
            return anc
    return None


def height_of(state, eid):
    try:
        return geometry.height_m(state, eid)
    except Exception:  # defensive: malformed chains in other domains' setups
        return 0.0


def enclosing_locked(state, eid):
    """A locked or child-locked closed container (or room off-limits) shields eid from children."""
    entities = state["entities"]
    for anc in ancestors(state, eid):
        rec = entities.get(anc, {})
        closed = rec.get("openable") and not rec.get("open")
        if closed and (rec.get("locked") or rec.get("child_lock") or rec.get("latched")):
            return True
        if rec.get("child_resistant_closed"):
            return True
    room = geometry_room(state, eid)
    if room and state["rooms"].get(room, {}).get("child_locked"):
        return True
    return False


def geometry_room(state, eid):
    from ...kernel.predicates import root_room
    return root_room(state, eid)


def today(state):
    """The household's date by its wall clock (clock.wall: daylight saving time where the clock keeps it)."""
    return clock.wall(state).date()


def parse_date(text):
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def weekday(state):
    return clock.wall(state).strftime("%A")


def held_with(ctx, capability):
    """Id of a held item offering capability (affordances/capabilities/tool_kind), or None."""
    w = ctx.w
    for eid in w.held_items():
        rec = w.ent(eid)
        caps = set(rec.get("affordances", [])) | set(rec.get("capabilities", [])) | {rec.get("tool_kind")}
        if capability in caps:
            return eid
    return None


def need_tool(ctx, capability, tool_id=None, message=None):
    w = ctx.w
    if tool_id:
        from .perceive import name_of     # by id (a handle) in realistic mode: never the label of a look-alike
        require(ctx.holding(tool_id), "Hold the {} first".format(name_of(ctx.env, tool_id)), "precondition")
        rec = w.ent(tool_id)
        caps = set(rec.get("affordances", [])) | set(rec.get("capabilities", [])) | {rec.get("tool_kind")}
        require(capability in caps, "That tool cannot {}".format(capability.replace("_", " ")), "capability")
        found = tool_id
    else:
        found = held_with(ctx, capability)
        require(found is not None, message or "Hold a tool that can {}".format(capability.replace("_", " ")), "capability")
    rec = w.ent(found)
    require(not rec.get("broken"), "That tool is broken", "safety")
    if rec.get("battery_powered"):
        require(float(rec.get("battery_level", 1.0)) > 0.02, "The tool's battery is flat", "resource")
    return found


# ----------------------------------------------------------------------
# what the robot can notice (kernel v3.1 notices, TIER1_SWEEP 6.1; kernel v3.0 visual events, TIER1_CAMERA 8.6)

NOTICE_KINDS = (
    ("device_beep", "an office machine beeps for attention (a printer jammed, out of paper or ink)", 55.0),
    ("collection_truck", "the waste collection truck works at the curb (engine, hydraulic lift, bins tipping)", 80.0),
)
for _kind, _doc, _db in NOTICE_KINDS:
    timeline.register_notice_kind(_kind, _doc, implies=("any_sound",), db=_db)
del _kind, _doc, _db


def notice(w, kind, room=None, db=None, source=None, remote=False, text=None, data=None):
    """Post a perceivable occurrence (w.notice): the timeline decides whether the robot hears or sees it and stores it
    when the episode listens (schema-3 specs, a listening wait). The source is an entity only when it still exists."""
    src = source if isinstance(source, str) and (source in w.s["entities"] or source in w.s.get("people", {})) else None
    return w.notice(kind, room=room, db=db, source=src, remote=remote, text=text, data=data)


def visual_event(w, kind, source=None, room=None, size_m=0.3):
    """A visible occurrence for the geometric camera (camera.visual_event without its event-log write: callers keep
    their own w.emit, so room-mode states stay as before); seen events reach the robot as "visual" notices."""
    return camera.visual_event(w, kind, source=source if isinstance(source, str) and source in w.s["entities"] else None,
                               room=room, size_m=size_m, emit=False)


def doorbell_of(state, room=None):
    """The household's doorbell (any domain's category "doorbell"; the one wired to a door of `room` first), or None."""
    bells = [e for e in sorted(state["entities"]) if state["entities"][e].get("category") == "doorbell"]
    if room is not None:
        near = [e for e in bells if geometry_room(state, e) == room or
                any(room in (conn.get("rooms") or ()) and conn.get("door") == state["entities"][e].get("door")
                    for conn in state.get("connections", {}).values())]
        bells = near or bells
    return bells[0] if bells else None


def inside_of(state, room):
    """The indoor room a door of an outdoor room opens into (sorted connections; the room itself when indoors)."""
    rooms = state["rooms"]
    if room in rooms and not rooms[room].get("outdoor"):
        return room
    for _, conn in sorted(state.get("connections", {}).items()):
        pair = conn.get("rooms") or ()
        if room in pair:
            other = next((r for r in pair if r != room), None)
            if other in rooms and not rooms[other].get("outdoor"):
                return other
    return None


def ring_doorbell(w, room):
    """Someone at the door of `room` rings: a "doorbell" notice inside at the doorbell's chime (its chime_db, else the
    kernel's 75 dB), and, for a connected video doorbell, a "device" notification over the robot's network (as the
    people domain's ring_doorbell). Nothing posted says who rang."""
    state = w.s
    bell = doorbell_of(state, room)
    inside = inside_of(state, room)
    rec = state["entities"].get(bell) or {}
    if inside is not None:
        notice(w, "doorbell", room=inside, db=rec.get("chime_db"), source=bell)
    if bell and rec.get("remote_status") and not rec.get("broken"):
        notice(w, "device", remote=True, source=bell, text="Someone is at the front door", data={"device": "doorbell"})


def doorbell_alert(w, room, eid, text):
    """A connected video doorbell at the door of `room` whose own camera frames `eid` now (camera.device_frame: its
    lens, line of sight, light or infrared) sends a "device" notification over the robot's network ("Package
    detected at the front door", "Motion at the front door"), as home doorbells do. Nothing when no one listens
    (no notice bus, no listening wait), without such a doorbell or when its frame misses the thing."""
    if not timeline.active(w):
        return None
    state = w.s
    bell = doorbell_of(state, room)
    rec = state["entities"].get(bell) or {}
    if not bell or not rec.get("remote_status") or rec.get("broken") or eid not in camera.device_frame(state, bell):
        return None
    return notice(w, "device", remote=True, source=bell, text=text, data={"device": "doorbell", "package": True})


def printed_face_down(record):
    """True when a paper's own view override turns its printed side (the type's label face, up) down."""
    return ((record.get("view") or {}).get("faces") or {}).get("label") == "down"


def set_face(w, eid, face):
    """A paper's printed side up or down: `face` and, with the geometric camera, the camera's label face (sight 4.6).
    Documents, envelopes and periodicals declare the printed side as their "label" face, up, at type level (records
    stay as generated); a face-down paper carries its own view override {"faces": {"label": "down"}}, so the cameras
    cannot read it until it is turned over (view is hidden in realistic perception). The room camera has no faces, so
    a room-mode state keeps only `face` (room-mode observations are unchanged)."""
    m = w.mut(eid)
    m["face"] = face
    if camera.mode(w.s) != "geometric":
        return
    view = dict(m.get("view") or {})
    faces = dict(view.get("faces") or {})
    if face == "down":
        faces["label"] = "down"
    else:
        faces.pop("label", None)
    if faces:
        view["faces"] = faces
    else:
        view.pop("faces", None)
    if view:
        m["view"] = view
    else:
        m.pop("view", None)


def camera_category(reason):
    """Failure category of a camera refusal (as core's): perception for what the cameras cannot make out, capability
    for reach, precondition for where the robot or the thing has to be first."""
    if reason in (camera.CAMERA_OFF_TEXT, camera.NOT_SEEN_TEXT, camera.TOO_DARK_TEXT, camera.GLARE_TEXT,
                  camera.DIRTY_TEXT, camera.TOO_SMALL_TEXT, "That is not visible here"):
        return "perception"
    return "capability" if reason and "reach" in reason.lower() else "precondition"


def _lens_clear(state):
    """The lens is clean enough to read text (perception's 0.4; the body field, default clean)."""
    clean = body.get(state, "camera_clean")
    return float(1.0 if clean is None else clean) >= 0.4


def readable(ctx, eid=None, fields=None):
    """True when printed text is legible now (require_readable without refusing)."""
    try:
        require_readable(ctx, eid, fields, spend=False)
    except ActionError:
        return False
    return True


def require_readable(ctx, eid=None, fields=None, spend=True):
    """Printed text is legible now; returns the fields that can be read. The colour cameras must work
    (ctx.require_imaging, TIER1_INTEGRATION 1.3 / 1.15: "Cameras are off; turn privacy mode off to look closely" with
    privacy mode, a switch or a room policy; "Camera fault: request help"). Room camera: the robot's room is lit and
    the lens clean enough. Geometric camera (kernel v3.0): with eid, the cameras' read rule for those printed fields
    of it (ctx.read_check: about 10 px per character of the field's print, the face it is printed on in view, light
    0.25, no glare, a clean lens; else the wrist camera within reach, +2 s); without eid (text the robot handles up
    close as it works: page numbers of a stack in hand, a label it writes, date labels of stock it moves), the lens
    clean enough -- the wrist camera brings its own light."""
    ctx.require_imaging()
    state = ctx.w.s
    if ctx.camera_mode == "geometric":
        if eid is None:
            require(_lens_clear(state), camera.DIRTY_TEXT, "perception")
            return list(fields or [])
        ok, reason, via, readable_fields = ctx.read_check(eid, list(fields or ["label_text"]))
        require(ok, reason or camera.NOT_SEEN_TEXT, camera_category(reason))
        if spend and via == "wrist" and not ctx.holding(eid):
            ctx.spend(camera.INSPECT_WRIST_S)
        elif spend and ctx.holding(eid):
            ctx.spend(camera.READ_HELD_S)
        return list(readable_fields)
    require(ctx.env.perception.lit(state, state["robot"]["room"]), "Too dark to read", "perception")
    require(_lens_clear(state), "Camera too dirty to read text; clean the camera", "perception")
    return list(fields or [])


def admin_facts(w):
    """Mutable facts.admin section (journaled through facts)."""
    facts = w.sec("facts")
    return facts.setdefault("admin", {})


def household(w):
    return w.sec("household")


DEFAULT_SURNAME = "Rivera"     # household surname when the setup names none (helpers._surname uses the same)


def roster(state):
    """pid -> list of normalized names (label, aliases, maiden names) for the household."""
    hh = state.get("household", {})
    names = {}
    configured = hh.get("roster") or {}
    surname = hh.get("surname") or DEFAULT_SURNAME
    for pid, p in sorted(state.get("people", {}).items()):
        if p.get("role") in ("guest", "visitor"):
            continue
        entry = configured.get(pid, {})
        first = str(p.get("label", pid)).split(" (")[0]
        # mail and parcels print "First Surname" (helpers.mail_batch / helpers.package): that is the resident too
        values = [p.get("label", pid), first, "{} {}".format(first, surname)] + list(entry.get("names", [])) + list(p.get("aliases", []))
        names[pid] = sorted({slug(v) for v in values if v})
    for pid, entry in sorted(configured.items()):
        if pid not in names:
            names[pid] = sorted({slug(v) for v in entry.get("names", [])})
    return names


def resolve_name(state, printed):
    """Map a printed addressee to a resident id, 'household', 'former_occupant' or None (unknown)."""
    key = slug(printed)
    if not key:
        return None
    hh = state.get("household", {})
    if key in {slug(v) for v in hh.get("household_names", ["current resident", "the household"])}:
        return "household"
    for pid, names in roster(state).items():
        if key in names:
            return pid
        # surname-only family addressing ("The Ortiz Family")
    for former in hh.get("former_occupants", []):
        if key == slug(former):
            return "former_occupant"
    return None


def address_matches(state, printed):
    hh = state.get("household", {})
    home = hh.get("address") or state.get("metadata", {}).get("address")
    if not home or not printed:
        return False
    return slug(printed) == slug(home) or slug(home) in slug(printed)


def units_metric(state):
    return state.get("metadata", {}).get("units", "metric") != "imperial"


def mass_text(kg, metric, resolution_kg=0.001):
    if metric:
        if kg < 1.0:
            return "{} g".format(int(round(kg * 1000)))
        return "{:.2f} kg".format(round(kg / resolution_kg) * resolution_kg)
    oz = kg * 35.274
    if oz < 16:
        return "{:.1f} oz".format(oz)
    return "{:.2f} lb".format(kg * 2.20462)


def deny(message, category="precondition"):
    raise ActionError(message, category)


def entity_room(state, eid):
    return geometry_room(state, eid)


def sheets_of(record):
    sheets = record.get("sheets") or []
    return [list(s) for s in sheets]


def sheet_count(record):
    if record.get("sheets"):
        return len(record["sheets"])
    return int(record.get("sheets_count", 0))


def doc_keys(record):
    keys = []
    for key, _ in sheets_of(record):
        if key not in keys:
            keys.append(key)
    return keys


def in_order(record):
    sheets = sheets_of(record)
    if not sheets:
        return True
    keys = doc_keys(record)
    expected = []
    for key in keys:
        pages = sorted(p for k, p in sheets if k == key)
        expected.extend([key, p] for p in pages)
    # each document's pages contiguous and ascending
    return sheets == expected


FOLD_LAYERS = {"z_fold": 3, "letter_fold": 3, "half_fold": 2}
BOOKLETS = ("passport", "manual")


def refresh_paper(record):
    """Recompute derived document fields after its sheets or folds changed (record must be mutable)."""
    sheets = sheets_of(record)
    n = len(sheets)
    record["sheets"] = sheets
    record["sheet_count"] = n
    # what reading the stack shows: the page numbers in stack order (the internal document keys stay hidden);
    # pages without printed numbers show no order at all (per-entity tier: in_order is not legible on them)
    if record.get("numbered", True):
        record["page_order"] = [page for _, page in sheets]
    else:
        record.pop("page_order", None)
        own = dict(record.get("_perception") or {})
        hidden = sorted(set(own.get("hidden") or []) | {"in_order"})
        if own.get("hidden") != hidden:
            own["hidden"] = hidden
            record["_perception"] = own
    record["in_order"] = in_order(record)
    record["mixed"] = len(doc_keys(record)) > 1
    if record.get("doc_kind") in BOOKLETS or not n:
        return record
    size = list(record.get("size_m") or [0.216, 0.279, 0.0002])
    if "flat_size_m" not in record:
        record["flat_size_m"] = [size[0], size[1]]
    flat = record["flat_size_m"]
    layers = FOLD_LAYERS.get(record.get("fold_style"), 1)
    record["size_m"] = [flat[0], round(flat[1] / layers, 4), round(max(0.0001, 0.0001 * n * layers), 5)]
    grammage = 0.075 if record.get("material", "paper") == "paper" else 0.2   # kg/m2 (20 lb bond vs cardstock)
    area = float(flat[0]) * float(flat[1])
    record["mass_kg"] = round(grammage * area * n, 5)
    record["volume_l"] = round(area * 0.0001 * n * 1000.0, 5)
    return record


def new_eid(w, stem):
    return w.new_id(stem)


# ----------------------------------------------------------------------
# cached lookups (kernel v2.1 static indexes): systems and hot helpers never scan every entity

def indexed(w, name, predicate):
    """Ids (sorted) of entities matching a predicate over STATIC fields, cached by the kernel until an
    entity is created or destroyed. Callers re-check dynamic fields on the records."""
    ents = w.s["entities"]
    return [e for e in w.static_index("admin_inventory:" + name, predicate) if e in ents]


def kids(w, parent, name, predicate):
    """Children of parent among an indexed class (cheap replacement for w.children + a static filter)."""
    ents = w.s["entities"]
    return [e for e in w.static_index("admin_inventory:" + name, predicate) if e in ents and ents[e].get("location") == parent]


DOMAIN_SECTION = "_admin_inventory"   # hidden top-level state section (never observed): small registries


def domain_section(w):
    """Mutable (journaled) hidden section for this domain's registries, e.g. label_peel {eid: due_s}."""
    return w.sec(DOMAIN_SECTION)


FLAT_M = 0.012   # papers, envelopes, label sheets, staples: thin things can lie on top of each other (a pile)


def flat_spot(state, surface_id, size_m, step=0.05):
    """[x, y] on a surface where a thin item (<= FLAT_M thick) fits, allowed to overlap other thin items
    (paper piles) but not solid ones; None if none. Busy desks from other domains rarely have bare spots."""
    surface = state["entities"][surface_id]
    sw, sd = (surface.get("surface_size_m") or (surface.get("size_m") or [0.5, 0.5])[:2])[:2]
    iw, idp = float(size_m[0]), float(size_m[1])
    solid = []
    for other in sorted(geometry.surface_items(state, surface_id)):
        orec = state["entities"][other]
        if orec.get("spos") is None or float((orec.get("size_m") or [0, 0, 0.1])[2]) <= FLAT_M:
            continue
        solid.append((orec["spos"], geometry.footprint_m(orec)))
    y = 0.0
    while y + idp <= float(sd) + 1e-9:
        x = 0.0
        while x + iw <= float(sw) + 1e-9:
            if not any(geometry.rect_overlap((x, y), (iw, idp), opos, osize) for opos, osize in solid):
                return [round(x, 3), round(y, 3)]
            x += step
        y += step
    return None


def drop_cell(w, room, near, item):
    """Floor cell where something that falls/is dropped near `near` ends up: the nearest free reachable cell, else
    any free cell, else the spot itself (never a fixed corner cell)."""
    near = tuple(near) if near else tuple(w.robot.get("pos") or (0, 0))
    return (geometry.free_floor_cell(w.s, room, near=near, ignore=(item,))
            or geometry.free_floor_cell(w.s, room, near=near, ignore=(item,), clearance=False, reachable=False)
            or list(near))
