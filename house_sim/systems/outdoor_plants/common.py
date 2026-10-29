"""Shared helpers for the outdoor_plants domain (geometry in meters, exposure, spills,
household checks, slow-tick scheduling, wind forces).

Conventions used across the package
-----------------------------------
* Room coordinates: +x = east (columns), +y = south (rows); row 0 is the north wall.
* Weather: state["weather"] holds condition, temperature_c, wind_mps, gust_mps,
  wind_from_deg (meteorological: direction the wind comes FROM), rain_mm_h,
  snow_cm_h, uv_index, rain_24h_mm, rain_7d_mm, forecast [...].
* Slow dynamics (plant water use, growth, compost, drying) advance in 600 s
  "slow ticks" detected from simulated-time boundaries, so they are independent
  of how the engine slices time and cost nothing between boundaries.
* Hidden truth lives in secret_fields / underscore keys; evaluation-only derived
  state lives in an entity's "_op_status" dict.
"""
import math

from ...kernel import camera, clock, geometry, physics
from ...kernel.errors import ActionError, require  # noqa: F401  (re-exported)
from ...kernel.phys import environment as kenv
from ...kernel.plugins import hook
from ...kernel.predicates import root_room

CELL = geometry.CELL
SLOW_S = 600.0
RHO_AIR = kenv.RHO_AIR          # the kernel's drag model (TIER1_PHYSICS 5.10): one air density for every wind force
G = 9.81
DAY = 86400.0
HOUR = 3600.0
WOOD = {"wood", "hardwood", "oak", "walnut", "pine", "teak", "bamboo", "wicker", "rattan"}
COMBUSTIBLE = {"plastic", "polyethylene", "paper", "cardboard", "wood", "resin", "recycled_plastic", "fabric", "wicker",
               "nylon", "polyester_vinyl", "polyester_foam", "thin_plastic", "canvas", "rubber", "pvc", "foam"}
ROOFED_ROOMS = {"porch"}
HOUSE_ATTACHED = {"porch", "patio", "deck", "balcony"}


# ---------------------------------------------------------------------------
# realistic perception (kernel v2.4, docs/PERCEPTION.md): every helper below is the identity in oracle mode

def realistic(ctx):
    """True when the action/inspector context runs realistic perception (ctx may be None for internal calls)."""
    return ctx is not None and bool(getattr(ctx, "realistic", False))


def env_realistic(env):
    return getattr(env, "realism", None) is not None


def seen_keys(ctx, details, renames):
    """Realistic mode: an inspector's finding about a never-tier field (species, pests, a nest...) would be stripped
    by the kernel's filter (it keeps no secret field), so it is reported under the `_seen` name instead."""
    if realistic(ctx):
        for old, new in renames.items():
            if old in details:
                details[new] = details.pop(old)
    return details


def drop_keys(ctx, details, keys):
    """Realistic mode: remove result keys the robot could not perceive (evaluation judgments, hidden causes,
    identities of unread look-alikes)."""
    if realistic(ctx):
        for key in keys:
            details.pop(key, None)
    return details


def mass_feel(kg):
    """What lifting tells about a mass (kernel realism MASS4 buckets)."""
    kg = float(kg or 0.0)
    return "light" if kg < 0.5 else "medium" if kg < 3.0 else "heavy" if kg < 12.0 else "very heavy"


def fill_word(frac):
    """How full something looks (kernel realism FILL5 buckets)."""
    frac = float(frac or 0.0)
    return "empty" if frac < 0.03 else "low" if frac < 0.3 else "half" if frac < 0.7 else "full" if frac < 0.97 else "overflowing"


def temp_feel(c):
    """Coarse temperature words (kernel realism TEMP6 buckets)."""
    c = float(c)
    for edge, name in ((8.0, "cold"), (17.0, "cool"), (26.0, "room temperature"), (40.0, "warm"), (60.0, "hot")):
        if c < edge:
            return name
    return "very hot"


def unresolved(ctx, eid):
    """Realistic mode: True when the robot has not identified eid -- an unresolved member of a look-alike group
    (an unread bag of granules, seed packet or spray bottle; uninspected pruners), an unfamiliar (novel) object, or a
    portion (derived_from) taken from such a package. Type- or content-specific guards must not run on such things (a
    refusal would tell the members apart). Always False in oracle mode. The kernel decides (ctx.unresolved, v2.9)."""
    if not realistic(ctx):
        return False
    return bool(ctx.unresolved(eid))


UNIDENTIFIED = "You cannot tell what is in it yet: read its label first"


def require_identified(ctx, *eids):
    """Realistic mode: one refusal, identical for every member of a look-alike group, before any guard that depends
    on what the thing really is (product kind, crop, liquid). The kernel's ctx.refuse_unidentified with the domain's
    wording (category precondition)."""
    if not realistic(ctx):
        return
    for eid in eids:
        ctx.refuse_unidentified(eid, UNIDENTIFIED)


def gone_refs(ctx, eids):
    """Ids of entities this action destroyed (pulled weeds, dug-out roots) can no longer be translated by the
    kernel's outbound pass (they are not entities any more). Realistic mode: report the handle the robot was already
    using for each, "something" otherwise; oracle mode: the ids."""
    realism = getattr(ctx.env, "realism", None) if realistic(ctx) else None
    if realism is None:
        return list(eids)
    return [realism.handles.by_id.get(e, "something") for e in eids]


def approx(ctx, value, step):
    """Realistic mode: a quantity the robot judged by eye or by handling, to `step`; oracle mode: unchanged."""
    if not realistic(ctx) or value is None:
        return value
    q = round(float(value) / step) * step
    return round(q, 3)


def amount_word(kg):
    """How much material came away (leaves raked, debris skimmed, sludge scooped), as a robot handling it judges it."""
    kg = float(kg or 0.0)
    return "none" if kg < 0.01 else "a handful" if kg < 0.3 else "a bucketful" if kg < 3.0 else "a heap" if kg < 15.0 else "a big heap"


def water_word(liters):
    """How much water was seen running off or out of a drainage hole."""
    liters = float(liters or 0.0)
    return "none" if liters < 0.005 else "a trickle" if liters < 0.05 else "some" if liters < 0.5 else "a lot"


def perceived_liters(liters):
    """Volume the robot poured, as judged from the can (about 5 % or 0.05 L)."""
    v = float(liters or 0.0)
    step = 0.05 if v < 2.0 else 0.25 if v < 10.0 else 1.0
    return round(max(step if v > 0 else 0.0, round(v / step) * step), 2)


# ---------------------------------------------------------------------------
# generic record helpers

def is_type(record, name):
    return record.get("type") == name or name in record.get("is_a", [])


def ents(w):
    return w.s["entities"]


def room_of(w, eid):
    return root_room(w.s, eid)


def room_rec(w, rid):
    return w.s["rooms"].get(rid) or {}


def is_outdoor_room(w, rid):
    return bool(room_rec(w, rid).get("outdoor"))


def is_roofed(w, rid):
    room = room_rec(w, rid)
    return bool(room.get("roofed")) or room.get("type") in ROOFED_ROOMS


# ---------------------------------------------------------------------------
# static indexes (kernel World.static_index)
#
# Phase-1 G7 regression: the kernel's step-start read views (independent review M3) made a first index build inside a
# busy substep about three times dearer (0.2-0.45 ms per index on a 900-entity house), and the first slow tick of every
# episode built some twenty of this domain's (own_drying's 25 type tests per record: 3.7 ms). Every index the systems,
# idle probes and invariants look up in an episode is therefore built when the episode starts (prebuild_indexes, before
# any hook writes): with nothing journaled the World evaluates a predicate with declared reads on the plain records.
# An index's content is a pure function of the state (World.static_index), so nothing any system computes changes.

TYPE_READS = ("type", "is_a")
PREBUILT = {}           # index name (without "op:") -> (predicate with .reads, only in houses with an outdoor room)
PREBUILT_TYPES = []     # type-name tuples of the type_index lookups every episode makes
_TYPE_PREDICATES = {}   # frozenset of type names -> its predicate (one object per set)


def index_reads(*fields):
    """Declare every record field a static-index predicate reads (World.static_index reads=; strict mode checks the
    declaration on every evaluation)."""
    def deco(fn):
        fn.reads = tuple(fields)
        return fn
    return deco


def episode_index(name, outdoor=False):
    """Register a static_ids predicate (declared reads) the domain looks up in every episode: prebuild_indexes builds it
    when the episode starts; outdoor: only in houses with an outdoor room."""
    def deco(fn):
        if getattr(fn, "reads", None) is None:
            raise TypeError("episode_index {}: declare the fields it reads (index_reads)".format(name))
        PREBUILT[name] = (fn, bool(outdoor))
        return fn
    return deco


def episode_types(*names):
    """Register a type_index(w, names) lookup every episode makes (the slow systems' type lists) for prebuild_indexes."""
    key = tuple(sorted(set(names)))
    if key not in PREBUILT_TYPES:
        PREBUILT_TYPES.append(key)


def _type_predicate(wanted):
    pred = _TYPE_PREDICATES.get(wanted)
    if pred is None:
        if len(wanted) == 1:
            (only,) = tuple(wanted)

            def pred(r):
                return r.get("type") == only or only in (r.get("is_a") or ())
        else:
            def pred(r):
                return r.get("type") in wanted or not wanted.isdisjoint(r.get("is_a") or ())
        pred.reads = TYPE_READS
        _TYPE_PREDICATES[wanted] = pred
    return pred


def type_index(w, names):
    """Cached sorted ids whose type or ancestors include any of names (kernel static_index over type and is_a, declared
    reads). Do not mutate the result."""
    names = (names,) if isinstance(names, str) else tuple(names)
    wanted = frozenset(names)
    return w.static_index("op:t:" + ",".join(sorted(wanted)), _type_predicate(wanted), TYPE_READS)


def static_ids(w, name, predicate):
    """Domain-namespaced w.static_index (predicate over static fields only; its declared reads, index_reads, when it
    has them)."""
    return w.static_index("op:" + name, predicate, getattr(predicate, "reads", None))


@hook("episode_start", order=-70)
def prebuild_indexes(w, env):
    """Build the domain's episode indexes when the episode starts, after safety's, tools_vehicles', mechanisms' and
    pets' prebuilds (orders -100 to -80, which write nothing) and before any hook writes: with nothing journaled the
    World evaluates their declared-read predicates on the plain records (the cheapest build) instead of through a read
    view per entity inside the first substep or slow tick (see the note above)."""
    outdoor = any(room.get("outdoor") for room in w.s["rooms"].values())
    for name in sorted(PREBUILT):
        predicate, outdoor_only = PREBUILT[name]
        if outdoor or not outdoor_only:
            w.static_index("op:" + name, predicate, predicate.reads)
    for names in PREBUILT_TYPES:
        type_index(w, names)


@episode_index("windows")
@index_reads("category")
def is_window(record):
    return record.get("category") == "window"


def find_types(w, names, room=None):
    """Sorted entity ids whose type or ancestors include any of names (optionally in a root room)."""
    ents = w.s["entities"]
    if room is None:
        return [e for e in type_index(w, names) if e in ents]
    return [e for e in type_index(w, names) if e in ents and root_room(w.s, e) == room]


def kids_of_type(w, parent, names):
    """Children of parent whose type/ancestors include names (sorted; O(entities of those types), not O(all))."""
    ents = w.s["entities"]
    return [e for e in type_index(w, names) if e in ents and ents[e].get("location") == parent]


def outdoor_map(w):
    """{eid: root room} for every entity whose root room is outdoors.

    One pass over the entities, cached on the World object until the robot takes another step, an entity is
    created/destroyed, or 600 s of simulated time pass (agents may carry things in or out during long waits).
    Used by event-driven weather effects (rain, wind) so they never walk every entity on every substep."""
    state = w.s
    key = (state.get("steps", 0), getattr(w, "_version", 0), len(state["entities"]),
           int(float(state.get("time_s", 0.0)) // SLOW_S))
    cache = getattr(w, "_op_outdoor", None)
    if cache is not None and cache[0] == key:
        return cache[1]
    outdoor = {rid for rid, room in state["rooms"].items() if room.get("outdoor")}
    result = {}
    if outdoor:
        from ...kernel.physics import _room_map
        rooms_of = _room_map(state)
        result = {eid: rid for eid, rid in rooms_of.items() if rid in outdoor}
    w._op_outdoor = (key, result)
    return result


def stat(w, name, amount=1):
    w.stat(name, amount)


def fact(w, key, value):
    w.sec("facts")[key] = value


def note(w, event_kind, **data):
    return w.emit("outdoor_plants." + event_kind, **data)


def visual(w, event_kind, source=None, room=None, size_m=0.3, **data):
    """A visual occurrence (an umbrella tips, a cover blows off, a cushion blows across the lawn): the event log entry
    is exactly note()'s; in geometric camera mode the robot's head camera witnesses it when it is in frame, lit and
    big enough (camera.visual_event, TIER1_CAMERA 8.6), and a seen one becomes a "visual" notice."""
    return camera.visual_event(w, "outdoor_plants." + event_kind, source=source, room=room, size_m=size_m, **data)


def seen_cells(w, rid):
    """Geometric camera: the floor cells of room rid the robot's cameras have surveyed this episode (the camera log's
    seen_cells plus this step's frames, camera.seen_floor). None in room mode (the room-wide rules apply)."""
    state = w.s
    if camera.mode(state) != "geometric" or rid not in state["rooms"]:
        return None
    cols, rows = geometry.grid_size(state["rooms"][rid])
    bits = ((state.get("camera_log") or {}).get("seen_cells") or {}).get(rid)
    cells = set()
    if isinstance(bits, str) and len(bits) == cols * rows:
        cells = {(i % cols, i // cols) for i, ch in enumerate(bits) if ch == "1"}
    cells |= {cell for room, cell in camera.seen_floor(state, w) if room == rid}
    return cells


def notice(w, kind, room=None, db=None, source=None, data=None):
    """A perceivable occurrence on the kernel notice bus (w.notice, TIER1_SWEEP 6.1): the timeline decides whether the
    robot hears it and stores it when someone listens. Posted only by the time systems and scripted events."""
    return w.notice(kind, room=room, db=db, source=source, data=data)


def set_status(w, eid, **values):
    """Write evaluation-only derived fields to _op_status, journaling only when they change."""
    record = w.s["entities"][eid]
    status = record.get("_op_status") or {}
    if all(status.get(k) == v for k, v in values.items()):
        return
    m = w.mut(eid)
    st = m.setdefault("_op_status", {})
    st.update(values)


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def slow_periods(w, dt):
    """Number of 600 s boundaries crossed by the substep that just advanced time by dt."""
    t = float(w.s.get("time_s", 0.0))
    return max(0, int(math.floor(t / SLOW_S + 1e-9)) - int(math.floor((t - dt) / SLOW_S + 1e-9)))


# ---------------------------------------------------------------------------
# positions in meters

def footprint_m(record):
    return geometry.footprint_m(record)


def top_left_m(w, eid, _depth=0):
    """Top-left corner of an entity in its root room, in meters."""
    state = w.s
    record = state["entities"].get(eid)
    if record is None or _depth > 30:
        return 0.0, 0.0
    loc = record.get("location")
    if loc in state["rooms"]:
        pos = record.get("pos") or [0, 0]
        return pos[0] * CELL, pos[1] * CELL
    if loc == "robot":
        pos = state["robot"].get("pos") or [0, 0]
        return pos[0] * CELL, pos[1] * CELL
    for group in ("people", "pets"):
        if loc in state.get(group, {}):
            pos = state[group][loc].get("pos") or [0, 0]
            return pos[0] * CELL, pos[1] * CELL
    if loc in state["entities"]:
        px, py = top_left_m(w, loc, _depth + 1)
        if record.get("relation", "on") == "on" and record.get("spos") is not None:
            return px + float(record["spos"][0]), py + float(record["spos"][1])
        cx, cy = center_m(w, loc, _depth + 1)
        fw, fd = footprint_m(record)
        return cx - fw / 2.0, cy - fd / 2.0
    return 0.0, 0.0


def center_m(w, eid, _depth=0):
    record = w.s["entities"].get(eid) or {}
    x, y = top_left_m(w, eid, _depth)
    fw, fd = footprint_m(record)
    if record.get("location") in w.s["rooms"] and record.get("pos"):
        cols, rows = geometry.footprint_size(record)
        return x + cols * CELL / 2.0, y + rows * CELL / 2.0
    return x + fw / 2.0, y + fd / 2.0


def robot_m(w):
    pos = w.s["robot"].get("pos") or [0, 0]
    return pos[0] * CELL + CELL / 2.0, pos[1] * CELL + CELL / 2.0


def cell_center_m(cell):
    return cell[0] * CELL + CELL / 2.0, cell[1] * CELL + CELL / 2.0


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def floor_cell_of(w, eid):
    """The room cell under an entity (for spills)."""
    x, y = center_m(w, eid)
    return [int(x // CELL), int(y // CELL)]


def drop_cell(w, rid, near=None, record=None, ignore=()):
    """Where something set down or falling at run time lands in room rid: the kernel's nearest free reachable
    cell outside door clearance that fits the record's footprint (geometry.free_floor_cell), else any free cell,
    else the cell it fell from (`near`, defaulting to the robot's cell when it is in that room). Never a fixed
    [0, 0] corner that may lie under furniture."""
    room = w.s["rooms"][rid]
    if near is None and w.s["robot"].get("room") == rid and w.s["robot"].get("pos"):
        near = w.s["robot"]["pos"]
    near = tuple(near) if near is not None else None
    cell = geometry.free_floor_cell(w.s, rid, near=near, ignore=ignore, record=record)
    if cell is None:
        cell = geometry.free_floor_cell(w.s, rid, near=near, ignore=ignore, record=record, clearance=False, reachable=False)
    if cell is None and record is not None:
        cell = geometry.free_floor_cell(w.s, rid, near=near, ignore=ignore, clearance=False, reachable=False)
    if cell is None:
        cell = list(near) if near is not None and geometry.in_grid(room, near) else [0, 0]
    return [int(cell[0]), int(cell[1])]


def height_of(w, eid):
    return geometry.height_m(w.s, eid)


def reach_with_tool(w, tool_id=None, default_ext=0.0):
    """Reach height (m) of the robot using tool_id. Kernel v2.1 robot_reach_m already adds the longest held tool's
    reach_extension_m (and a ladder's standing height); a tool without that field contributes default_ext."""
    reach = geometry.robot_reach_m(w.s)
    if tool_id and tool_id in w.s["entities"]:
        ents = w.s["entities"]
        counted = max([float(ents[h].get("reach_extension_m", 0.0) or 0.0) for h in w.held_items() if h in ents] or [0.0])
        ext = float(ents[tool_id].get("reach_extension_m", default_ext) or 0.0)
        reach += max(0.0, ext - counted)
    return reach


# ---------------------------------------------------------------------------
# containment and exposure

def enclosed(w, eid):
    """True if any ancestor is a closed (non-transparent) container."""
    state = w.s
    loc, seen = state["entities"].get(eid, {}).get("location"), set()
    while loc in state["entities"] and loc not in seen:
        seen.add(loc)
        parent = state["entities"][loc]
        if parent.get("openable") and not parent.get("open") and not parent.get("transparent"):
            return loc
        loc = parent.get("location")
    return None


def covering(w, eid):
    """The intact cover over an entity or any of its ancestors (cover.covers == that id)."""
    state = w.s
    chain = [eid]
    loc = state["entities"].get(eid, {}).get("location")
    while loc in state["entities"] and loc not in chain:
        chain.append(loc)
        loc = state["entities"][loc].get("location")
    for anc in chain:
        cover = state["entities"].get(anc, {}).get("covered_by")
        if cover and cover in state["entities"] and cover not in chain:
            crec = state["entities"][cover]
            if not crec.get("torn") and not crec.get("melted") and crec.get("covers") == anc:
                return cover
    return None


def rain_exposed(w, eid):
    """Exposure factor 0..1 of an entity to falling rain/snow."""
    rid = room_of(w, eid)
    if rid is None or not is_outdoor_room(w, rid) or is_roofed(w, rid):
        return 0.0
    record = w.s["entities"].get(eid, {})
    if record.get("location") == "robot":
        return 0.0
    box = enclosed(w, eid)
    if box:
        return 0.15 if w.s["entities"][box].get("leaky") else 0.0
    if covering(w, eid):
        return 0.0
    return 1.0


WEATHER_RATED_FLAGS = ("waterproof", "weatherproof", "outdoor_rated", "weather_rated")
# Outdoor equipment whose types do not declare a rating flag yet (see docs, section 8). The robot mower and its dock,
# EV chargers, outdoor outlets and the pool pump now carry flags, so they are no longer listed here.
WEATHER_RATED_HINTS = ("irrigation", "outdoor_light", "landscape_light", "security_camera", "spigot")


def weather_rated(record):
    """Built for the weather: declared by a rating flag, or a known outdoor-equipment type/category."""
    if any(record.get(k) for k in WEATHER_RATED_FLAGS):
        return True
    names = [str(record.get("type", "")), str(record.get("category", ""))] + [str(n) for n in record.get("is_a") or []]
    return any(h in n for n in names for h in WEATHER_RATED_HINTS)


def water_sensitive(record):
    if any(record.get(k) for k in WEATHER_RATED_FLAGS):
        return False
    if record.get("water_sensitive") is not None:
        return bool(record["water_sensitive"])
    material = str(record.get("material", ""))
    if material in ("paper", "cardboard", "electronics", "leather", "fabric", "wool", "cotton", "suede"):
        return True
    return record.get("category") in ("electronics", "book", "document", "phone", "laptop", "artwork") or bool(record.get("plug_required"))


# ---------------------------------------------------------------------------
# household

def people_in(w, rid):
    return sorted(pid for pid, p in w.s.get("people", {}).items() if p.get("location") == rid)


def pets_in(w, rid):
    return sorted(pid for pid, p in w.s.get("pets", {}).items() if p.get("location") == rid)


def has_children(w):
    return any(p.get("role") in ("child", "infant", "toddler") for p in w.s.get("people", {}).values())


def has_pets(w, species=None):
    return any(species is None or p.get("species") == species for p in w.s.get("pets", {}).values())


def adult_ok(person):
    authority = person.get("authority", 1 if person.get("role") == "adult" else 0)
    return authority >= 1 and person.get("consent", True) and person.get("awake", True)


def adult_present(w, rid):
    return any(adult_ok(p) and p.get("location") == rid for p in w.s.get("people", {}).values())


def adult_on_site(w):
    rooms = w.s["rooms"]
    return any(adult_ok(p) and p.get("location") in rooms and not rooms[p["location"]].get("shared")
               for p in w.s.get("people", {}).values())


def approved(w, *scopes):
    approvals = w.s.get("approvals", [])
    return any(scope in approvals for scope in scopes)


def household_rules(w):
    return w.s.get("household", {})


# ---------------------------------------------------------------------------
# time and climate

def season(w):
    return clock.season(w.s)


def hour(w):
    return clock.hour(w.s)


def weekday(w):
    return clock.now(w.s).strftime("%A")


def daylight(w):
    return clock.daylight(w.s)


def weather(w):
    return w.s.get("weather", {}) or {}


def air_temp(w):
    return float(weather(w).get("temperature_c", 18.0))


def gust(w):
    wx = weather(w)
    return float(wx.get("gust_mps", float(wx.get("wind_mps", 0.0)) * 1.4))


def raining(w):
    return float(weather(w).get("rain_mm_h", 0.0)) > 0.05 or weather(w).get("condition") in ("rain", "storm")


def wind_vector(w):
    """Unit vector the wind blows TOWARD in room coordinates (+x east, +y south)."""
    frm = math.radians(float(weather(w).get("wind_from_deg", 270.0)))
    # from north (0 deg) blows toward +y (south); from west (270) toward +x (east)
    return -math.sin(frm), math.cos(frm)


def drag_force(speed, area_m2, cd=1.2):
    return 0.5 * RHO_AIR * speed * speed * cd * area_m2


def frontal_area(record):
    size = record.get("size_m") or [0.1, 0.1, 0.1]
    return max(float(size[0]), float(size[1])) * float(size[2] if len(size) > 2 else 0.1)


# ---------------------------------------------------------------------------
# spills, damage, spawning

def spawn(w, type_name, location, eid=None, **props):
    """Spawn a registered type, falling back to generic_item when a cross-domain type is absent."""
    if w.reg is not None and not w.reg.known(type_name):
        props.setdefault("label", type_name.replace("_", " "))
        props.setdefault("category", type_name)
        type_name = "generic_item"
    return w.spawn(type_name, location, eid=eid, **props)


# ---------------------------------------------------------------------------
# harm attribution (TIER1_DECISIONS "harm attribution"; review M9 / P1 C8; VERIFY_HARDENING_DECISIONS 3)
#
# The global harm flags (stats.damage, injury, fire, breakages, electrical_hazard, ...) fail every task, so this domain
# raises them only for a harm the robot caused: during its own action (the kernel's action phase), or from a time
# system when the robot set up the cause -- it arranged the thing (the kernel's _robot_moved settle marker on it or
# along its location chain) or a robot action left a mark of the cause on it (ROBOT_MARK: an umbrella it opened,
# de-icer it spread, hot ash it moved into a holder, a decoration it plugged in). A harm the robot only failed to
# prevent (a gale tipping an umbrella the scene left open, embers the scene left in the pit flaring in the wind, the
# scene's indoor cord out in the rain) still happens in the state and the event log (by: exogenous) and is counted in
# state["outdoor_plants"]["exogenous_harms"] {flag: n}; a spec about preventing it says so with its own predicates.

ROBOT_MARK = "_op_robot"        # hidden list of the causes a robot action set up on a thing (never observed)


def robot_acting(w):
    """True while a robot action runs (the kernel's action phase until its settle ends); systems and scripted events
    run in substeps, never in it."""
    return (getattr(w, "phys_scratch", None) or {}).get("phase") == "action"


def mark_robot(w, eid, *marks):
    """A robot action just set up these causes on eid (nothing outside a robot action)."""
    if not robot_acting(w) or eid not in w.s["entities"]:
        return
    have = list(w.s["entities"][eid].get(ROBOT_MARK) or ())
    new = sorted(set(have) | set(marks))
    if new != have:
        w.mut(eid)[ROBOT_MARK] = new


def drop_robot(w, eid, *marks):
    """The cause went away (the umbrella closed, the cord unplugged): the robot's marks no longer explain a harm."""
    rec = w.s["entities"].get(eid)
    if rec is None or not rec.get(ROBOT_MARK):
        return
    left = [m for m in rec[ROBOT_MARK] if m not in marks]
    if left != list(rec[ROBOT_MARK]):
        m = w.mut(eid)
        if left:
            m[ROBOT_MARK] = left
        else:
            m.pop(ROBOT_MARK, None)


def robot_caused(w, source=None, *marks):
    """A harm with this source is the robot's: its action is running, a robot action left one of `marks` (any mark
    when none are named) on the source, or it arranged the source (kernel phys.impact.robot_caused)."""
    if robot_acting(w):
        return True
    rec = w.s["entities"].get(source) if isinstance(source, str) else None
    if rec is None:
        return False
    have = rec.get(ROBOT_MARK) or ()
    if have and (not marks or any(m in have for m in marks)):
        return True
    from ...kernel.phys import impact
    return bool(impact.robot_caused(w, source))


def harm(w, flag, source=None, marks=(), robot=None, amount=1):
    """Raise the global harm flag `flag` when the harm is the robot's (robot_caused, or robot= given); count an
    exogenous one in state["outdoor_plants"]["exogenous_harms"] instead. Returns True for the robot's."""
    by = robot_caused(w, source, *marks) if robot is None else bool(robot)
    if by:
        w.stat(flag, amount)
    else:
        counts = w.sec("outdoor_plants").setdefault("exogenous_harms", {})
        counts[flag] = counts.get(flag, 0) + amount
    return by


def add_spill(w, rid, cell, liters, liquid="water", onto=None, source=None):
    """Liquid the robot let out (an overflowing saucer, water dripping off a carried rain-soaked item) goes through the
    kernel's single spill entry point (physics.spill, by the robot; TIER1_PHYSICS 5.7): below 5 ml it is droplets;
    onto an entity, it and the supports under it catch what they can and the rest reaches the floor under them;
    otherwise it lands on the floor of rid at cell as cleaning's spill (phys.spill_sink) or a kernel puddle, which
    keep the room's wet_cells. A counted spill adds the global `spills`. A wooden or water-unsafe piece it lands on
    also keeps this domain's water mark (damage["water"]). Returns the floor spill's id or None."""
    if liters <= 1e-4:
        return None
    stat(w, "spilled_l", round(liters, 3))
    if onto and onto in w.s["entities"]:
        target = w.s["entities"][onto]
        if not target.get("water_safe", True) or str(target.get("material", "")) in WOOD:
            m = w.mut(onto)
            m["wetness"] = round(min(1.0, float(m.get("wetness", 0.0)) + liters * 2.0), 3)
            m["water_marks"] = int(m.get("water_marks", 0)) + 1
            damage = m.get("damage")
            damage = dict(damage) if isinstance(damage, dict) else {k: 0.5 for k in damage or [] if isinstance(k, str)}
            damage["water"] = round(min(1.0, float(damage.get("water", 0.0)) + 0.2 + liters * 0.3), 3)
            m["damage"] = damage
            stat(w, "damage")
            note(w, "water_damage", target=onto, liters=round(liters, 3))
    if onto in w.s["entities"]:
        return physics.spill(w, liters, liquid, onto=onto, source=source, by="robot")
    if rid is None or rid not in w.s["rooms"]:
        return None
    return physics.spill(w, liters, liquid, at=(rid, list(cell) if cell is not None else None), source=source, by="robot")


# ---------------------------------------------------------------------------
# the robot's grippers (kernel v3.0 body: hand soil goes through body.soil_hand only)

GLOVES = ("garden_gloves", "rubber_gloves", "work_gloves", "cut_resistant_gloves", "nitrile_gloves", "gloves")
MUD_RWC = 0.6           # soil at or above this relative water content smears on the fingers as mud


def gloved(w):
    """True while the robot wears gloves (core wear records the gear kind): they take the soil instead."""
    return bool(set(w.s["robot"].get("wearing") or ()) & set(GLOVES))


def soil_hands(ctx, kind, amount, hand=None):
    """Bare-handed garden work leaves `kind` on the grippers (body.soil_hand): mud makes them slippery (lower grip
    friction and precision; enough of it starts a latent gripper_slip fault) until they are washed (cleaning's wash /
    wipe with target robot_hand); dry soil and plant sap or oils (urushiol from poison ivy) stay on them and ride along
    to whatever is touched next. Gloves worn take it instead. hand: "left" | "right" | "both" (default: the free hand,
    else both)."""
    from ...kernel import body
    w = ctx.w
    if amount <= 0 or gloved(w):
        return
    if hand is None:
        free = [h for h, held in sorted((w.s["robot"].get("hands") or {}).items()) if held is None]
        hand = free[0] if len(free) == 1 else "both"
    body.soil_hand(w, hand, kind, amount)


def soil_from(ctx, holder, amount=0.2, hand=None):
    """Hand work in a pot's, tray's or bed's medium: mud when it is wet (RWC >= MUD_RWC), dry soil otherwise."""
    from .species import rwc
    medium = (holder or {}).get("medium") or {}
    kind = medium.get("kind") or "garden_soil"
    if kind == "none" or float(medium.get("soil_l", 1.0) or 0.0) <= 0:
        return
    wet = rwc(float(medium.get("moisture_top", medium.get("moisture", 0.0)) or 0.0), kind) >= MUD_RWC
    soil_hands(ctx, "mud" if wet else "soil", amount, hand)


def destroy_tree(w, eid):
    for child in sorted(w.descendants(eid), key=lambda e: -len(w.ancestors(e))):
        w.destroy(child)
    w.destroy(eid)


def held_tools(ctx):
    return [e for e in ctx.w.held_items()]


def find_held(ctx, predicate, by_identity=False):
    """First held item matching predicate. by_identity: the predicate tests what the thing is (product kind, liquid);
    realistic mode then skips things the robot has not identified (their identity must not decide the outcome)."""
    for eid in ctx.w.held_items():
        if by_identity and unresolved(ctx, eid):
            continue
        if predicate(ctx.w.ent(eid)):
            return eid
    return None


def held_with_capability(ctx, capability):
    def ok(rec):
        caps = set(rec.get("capabilities", [])) | set(rec.get("affordances", [])) | {rec.get("tool_kind")}
        return capability in caps
    return find_held(ctx, ok)


def reachable(ctx, eid, radius_cells=geometry.REACH_CELLS):
    """True when the robot is in the entity's room within radius (cells) of its footprint/position."""
    state = ctx.w.s
    room, cells = geometry.anchor(state, eid)
    if room != state["robot"]["room"]:
        return False
    if not cells:
        return True
    rpos = tuple(state["robot"].get("pos") or (0, 0))
    return min(geometry.chebyshev(rpos, c) for c in cells) <= radius_cells


def nearby_entity(ctx, predicate, radius_cells=2, by_identity=False):
    """Find a visible entity in the robot's room within radius matching predicate (held items first). by_identity:
    as in find_held (realistic mode ignores things the robot has not identified)."""
    w = ctx.w
    for eid in w.held_items():
        if by_identity and unresolved(ctx, eid):
            continue
        if predicate(w.ent(eid)):
            return eid
    for eid in sorted(w.s["entities"]):
        record = w.s["entities"][eid]
        if not predicate(record):
            continue
        if by_identity and unresolved(ctx, eid):
            continue
        if room_of(w, eid) != w.robot["room"] or not ctx.visible(eid):
            continue
        if reachable(ctx, eid, radius_cells):
            return eid
    return None


def house_wall_edge(w, rid):
    """Which edge of an outdoor room adjoins the house: 'n', 's', 'e' or 'w'."""
    room = room_rec(w, rid)
    cols, rows = geometry.grid_size(room)
    for cid, conn in sorted(w.s.get("connections", {}).items()):
        if rid not in conn["rooms"]:
            continue
        other = conn["rooms"][0] if conn["rooms"][1] == rid else conn["rooms"][1]
        orec = room_rec(w, other)
        if orec.get("outdoor") and orec.get("type") not in HOUSE_ATTACHED:
            continue
        if orec.get("type") in ("garage", "shed"):
            continue
        cell = conn.get("cells", {}).get(rid)
        if cell is None:
            continue
        if cell[1] == 0:
            return "n"
        if cell[1] == rows - 1:
            return "s"
        if cell[0] == 0:
            return "w"
        if cell[0] == cols - 1:
            return "e"
    return "n"


def edge_cells(room, edge):
    cols, rows = geometry.grid_size(room)
    if edge == "n":
        return [[x, 0] for x in range(cols)]
    if edge == "s":
        return [[x, rows - 1] for x in range(cols)]
    if edge == "w":
        return [[0, y] for y in range(rows)]
    return [[cols - 1, y] for y in range(rows)]


def distance_to_edge_m(room, edge, xy):
    cols, rows = geometry.grid_size(room)
    if edge == "n":
        return xy[1]
    if edge == "s":
        return rows * CELL - xy[1]
    if edge == "w":
        return xy[0]
    return cols * CELL - xy[0]


def quiet_hours(w):
    """True during the household's quiet hours (default 21:00-07:00) or when someone sleeps nearby."""
    rules = household_rules(w)
    start, end = rules.get("quiet_hours", [21, 7])
    h = hour(w)
    inside = (h >= start or h < end) if start > end else (start <= h < end)
    return inside


def pinfo(record):
    """The record's garden-product info as a dict; other domains may store product as a plain string."""
    p = record.get("product") if record else None
    if isinstance(p, dict):
        return p
    if isinstance(p, str) and p:
        return {"kind": p}
    return {}


def product_kind(record):
    return pinfo(record).get("kind")
