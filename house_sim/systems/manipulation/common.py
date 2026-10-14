"""Shared physical models and world helpers for the manipulation domain.

Fidelity: G (continuous poses on surfaces and room floors, footprints, clearances,
stack geometry) and P-approx (lumped models for grip friction/crush, drop impact,
liquid slosh, floor abrasion, stack load and tipping). Nothing here is a boolean
shortcut: every outcome is derived from masses, sizes, materials, forces and speeds.

Coordinate conventions
----------------------
* Room frame: x = column direction (east), y = row direction (south, down the grid),
  meters from the room's top-left corner. Angles: 0 deg = +x, 90 deg = +y.
* Floor items: ``pos`` [col, row] (kernel cell) and optional continuous ``xy_m``
  [x, y] (top-left corner of the footprint). Missing ``xy_m`` means pos * CELL.
* Surface items: ``spos`` [x, y] meters from the surface's corner (kernel).
* ``yaw_deg`` is an object's heading relative to its parent frame; ``features``
  maps functional features (handle, front, blade_edge, label, zipper ...) to an
  angle offset from the object's own +x axis.
"""
import math

from ...kernel import geometry
from ...kernel.errors import ActionError, require

G = 9.81
CELL = geometry.CELL
SECTION = "manipulation"
HAND_HEIGHT_M = 1.0

# ----------------------------------------------------------------------------------
# materials

FRAGILE_DEFAULTS = {   # material -> (drop_tolerance_m onto a hard floor, crush_force_n, crush_kg load on top)
    "glass": (0.10, 40.0, 3.0), "crystal": (0.05, 25.0, 1.5), "porcelain": (0.12, 60.0, 6.0),
    "ceramic": (0.15, 150.0, 10.0), "stoneware": (0.20, 200.0, 12.0), "terracotta": (0.20, 200.0, 15.0),
    "eggshell": (0.03, 25.0, 1.0), "electronics": (0.50, 120.0, 5.0), "plaster": (0.15, 60.0, 3.0),
    "paper": (5.0, 15.0, 2.0), "cardboard": (5.0, 60.0, 12.0), "plastic": (1.0, 120.0, 8.0),
}
DEFAULT_FRAGILE = (0.20, 50.0, 5.0)
FRICTION = {"glass": 0.4, "crystal": 0.4, "ceramic": 0.5, "porcelain": 0.5, "stoneware": 0.55, "plastic": 0.45,
            "metal": 0.4, "steel": 0.4, "cast_iron": 0.45, "aluminum": 0.4, "wood": 0.55, "paper": 0.5,
            "cardboard": 0.55, "rubber": 0.9, "fabric": 0.6, "leather": 0.6, "terracotta": 0.6, "foam": 0.8}
SURFACE_STATE_MU = {"dry": 1.0, "damp": 0.6, "wet": 0.4, "soapy": 0.12, "oily": 0.1, "greasy": 0.15}

# grip mode -> (force cap N, adaptive controller, effective contacts, support kind)
GRIP_MODES = {
    "gentle": (10.0, True, 4, None), "normal": (50.0, True, 4, None), "firm": (90.0, False, 4, None),
    "pinch": (8.0, True, 2, None), "edges": (25.0, True, 2, None), "handle": (50.0, True, 4, "handle"),
    "underneath": (12.0, True, 4, "below"), "scoop": (12.0, True, 4, "below"), "two_hand": (60.0, True, 8, "below"),
}
GRIP_SAFETY = 1.2

FLOOR_HARDNESS = {"tile": 1.0, "stone": 1.0, "concrete": 1.0, "marble": 1.0, "hardwood": 0.85, "wood": 0.8,
                  "laminate": 0.85, "vinyl": 0.75, "plywood": 0.7, "rubber": 0.45, "carpet": 0.35, "rug": 0.45,
                  "grass": 0.3, "soil": 0.25, "mulch": 0.25, "sand": 0.25}
FLOOR_SCRATCH = {"hardwood": 1.0, "hardwood_unsealed": 1.0, "laminate": 0.8, "vinyl": 0.7, "wood": 0.7, "plywood": 0.3,
                 "tile": 0.25, "stone": 0.2, "marble": 0.5, "rubber": 0.1, "concrete": 0.0, "carpet": 0.0,
                 "grass": 0.0, "soil": 0.0}
FOOT_SCRATCH = {"metal": 1.0, "steel": 1.0, "cast_iron": 1.0, "stone": 1.0, "glass": 0.9, "ceramic": 0.9,
                "terracotta": 0.9, "wood": 0.8, "plastic": 0.5, "wicker": 0.4, "cardboard": 0.2, "leather": 0.2,
                "rubber": 0.15, "fabric": 0.1, "foam": 0.05, "felt": 0.0, "slider": 0.0, "caster": 0.0}
FLOOR_FRICTION = {"hardwood": 0.3, "laminate": 0.3, "vinyl": 0.35, "tile": 0.35, "stone": 0.4, "concrete": 0.55,
                  "carpet": 0.6, "rubber": 0.8, "grass": 0.7, "soil": 0.7, "wood": 0.35, "plywood": 0.4}
SPEED_FACTOR = {"careful": 0.5, "normal": 1.0, "fast": 1.7}
ROBOT_PUSH_N = 220.0          # sustained horizontal force a humanoid can apply safely
ROBOT_WRIST_TORQUE_NM = 6.0   # torque available for twisting caps and screw parts
SEATED_HEIGHT_M = 0.45


# ----------------------------------------------------------------------------------
# small utilities

def num(value, default=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def dims(rec):
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    w = num(size[0], 0.1)
    d = num(size[1], 0.1) if len(size) > 1 else w
    h = num(size[2], 0.1) if len(size) > 2 else 0.1
    return w, d, h


def footprint_wd(rec):
    w, d, _ = dims(rec)
    if int(rec.get("rot", 0) or 0) % 180 == 90:
        w, d = d, w
    return w, d


def surface_wd(rec):
    size = rec.get("surface_size_m") or (rec.get("size_m") or [0.5, 0.5])[:2]
    return num(size[0], 0.5), num(size[1], 0.5)


def yaw_of(rec):
    if "yaw_deg" in rec:
        return num(rec.get("yaw_deg")) % 360.0
    return num(rec.get("rot", 0)) % 360.0


def ang_diff(a, b):
    d = (num(a) - num(b)) % 360.0
    return min(d, 360.0 - d)


def rot_for_yaw(yaw):
    return int(round(num(yaw) / 90.0)) % 4 * 90


def material(rec):
    return str(rec.get("material") or "plastic").lower()


def is_kind(rec, *names):
    kinds = {rec.get("type"), rec.get("category")} | set(rec.get("is_a", [])) | set(rec.get("tags", []))
    return any(n in kinds for n in names)


def subtree_mass(w, eid):
    total = 0.0
    for item in [eid] + w.descendants(eid):
        record = w.ent(item)
        total += num(record.get("mass_kg", 0.0)) + num(record.get("liquid_l", 0.0)) * num(record.get("liquid_density", 1.0), 1.0)
    return total


def room_of(w, eid):
    return w.root_room(eid)


def floor_id(w, rid):
    fid = "floor_" + str(rid)
    return fid if fid in w.s["entities"] else None


def floor_material(w, rid):
    room = w.s["rooms"].get(rid, {})
    fid = floor_id(w, rid)
    if fid:
        return str(w.ent(fid).get("material") or room.get("floor_material") or "wood").lower()
    return str(room.get("floor_material") or room.get("terrain") or "wood").lower()


def robot_speed(ctx):
    speed = ctx.args.get("speed") if isinstance(ctx.args.get("speed"), str) else None
    return speed or ctx.w.robot.get("speed_mode", "normal")


def section(w):
    """Read-only view of the domain section (goals, jobs, records)."""
    return w.s.get(SECTION) or {}


def msec(w):
    sec = w.sec(SECTION)
    sec.setdefault("goals", {})
    sec.setdefault("jobs", {})
    return sec


# ----------------------------------------------------------------------------------
# watch list: entities whose DYNAMIC manipulation fields the time systems follow, so no
# system scans every entity each tick (static props use w.static_index instead).

WATCH_TIMED = ("label_peels_at_s", "pads_fall_off_at_s")
WATCH_WET = ("stacked_wet_since_s", "rolled_wet_since_s", "moisture_trap_since_s", "sealed_wet_since_s")
WATCH_FIELDS = ("falling", "rolling", "covering") + WATCH_TIMED + WATCH_WET


def watch_live(rec):
    """True while a record still carries a dynamic field one of the systems must follow."""
    if rec.get("falling") or rec.get("rolling") or rec.get("covering"):
        return True
    if any(rec.get(k) is not None for k in WATCH_TIMED):
        return True
    return not rec.get("mildewed") and any(rec.get(k) is not None for k in WATCH_WET)


def watch(w, eid):
    """Register eid in state.manipulation.watch (journaled; call whenever one of WATCH_FIELDS is set)."""
    ids = section(w).get("watch") or ()
    if eid in ids:
        return
    sec = w.sec(SECTION)
    sec["watch"] = sorted(set(ids) | {eid})


def watched(w):
    """Watched ids that still exist (sorted)."""
    ents = w.s["entities"]
    return [e for e in (section(w).get("watch") or ()) if e in ents]


def prune_watch(w):
    """Drop ids whose dynamic fields are all settled (one journaled write, only when needed)."""
    ids = section(w).get("watch") or ()
    ents = w.s["entities"]
    keep = [e for e in ids if e in ents and watch_live(ents[e])]
    if len(keep) != len(ids):
        w.sec(SECTION)["watch"] = keep


def note_event(w, kind, eid=None, room=None, **data):
    data.pop("kind", None)
    entry = {"room": room or (room_of(w, eid) if eid and eid in w.s["entities"] else w.robot.get("room"))}
    if eid:
        entry["target"] = eid
    entry.update(data)
    return w.emit("manip." + kind, **entry)


def add_noise(w, amount):
    if amount > 0:
        w.stat("noise", round(float(amount), 3))


HARD_CLINK = ("ceramic", "porcelain", "glass", "stoneware", "crystal", "metal", "steel", "aluminum", "cast_iron", "earthenware")
HARD_SURFACE = ("stone", "marble", "granite", "quartz", "ceramic", "glass", "metal", "steel", "porcelain", "tile", "concrete",
                "stoneware", "laminate")
HAND_GAP_M = 0.09   # narrowest gap the robot hand (with wrist) fits through


def sound_db(w, eid, db, source_kind, room=None, stat=0.0):
    """A sound with an estimated level at 1 m (dB A). Keeps the episode peak in
    state.manipulation.noise {"peak_db", "rooms": {room: peak}} and emits manip.noise
    (people can wake sleepers / judge quietness from it)."""
    room = room or (room_of(w, eid) if eid and eid in w.s["entities"] else w.robot.get("room"))
    db = round(float(db), 1)
    noise = msec(w).setdefault("noise", {})
    rooms = noise.setdefault("rooms", {})
    if db > num(rooms.get(room)):
        rooms[room] = db
    if db > num(noise.get("peak_db")):
        noise["peak_db"] = db
    add_noise(w, stat)
    note_event(w, "noise", eid, room=room, db=db, source_kind=source_kind)


# ----------------------------------------------------------------------------------
# poses

def floor_xy(rec):
    if isinstance(rec.get("xy_m"), list) and len(rec["xy_m"]) == 2:
        return num(rec["xy_m"][0]), num(rec["xy_m"][1])
    pos = rec.get("pos") or [0, 0]
    return num(pos[0]) * CELL, num(pos[1]) * CELL


def floor_rect(rec):
    x, y = floor_xy(rec)
    w, d = footprint_wd(rec)
    return x, y, x + w, y + d


def set_floor_xy(w, eid, x, y, yaw=None):
    """Move a room-located entity to continuous coordinates and keep pos consistent."""
    m = w.mut(eid)
    m["xy_m"] = [round(float(x), 3), round(float(y), 3)]
    m["pos"] = [int(math.floor(float(x) / CELL + 1e-6)), int(math.floor(float(y) / CELL + 1e-6))]
    if yaw is not None:
        m["yaw_deg"] = round(float(yaw) % 360.0, 2)
        m["rot"] = rot_for_yaw(yaw)
    return m


def rotate_vec(u, v, deg):
    t = math.radians(deg)
    return u * math.cos(t) - v * math.sin(t), u * math.sin(t) + v * math.cos(t)


def world_pose(w, eid, _depth=0):
    """(room, x, y, yaw) of an entity's center in its room frame (None if unplaced)."""
    s = w.s
    if _depth > 25:
        return None
    if eid in s.get("people", {}) or eid in s.get("pets", {}):
        agent = s["people"].get(eid) or s["pets"].get(eid)
        pos = agent.get("pos") or [0, 0]
        return agent.get("location"), (pos[0] + 0.5) * CELL, (pos[1] + 0.5) * CELL, num(agent.get("facing_deg", 0))
    if eid == "robot":
        pos = s["robot"].get("pos") or [0, 0]
        return s["robot"]["room"], (pos[0] + 0.5) * CELL, (pos[1] + 0.5) * CELL, num(s["robot"].get("facing_deg", 0))
    rec = s["entities"].get(eid)
    if rec is None:
        return None
    loc = rec.get("location")
    if loc in s["rooms"]:
        x0, y0, x1, y1 = floor_rect(rec)
        return loc, (x0 + x1) / 2.0, (y0 + y1) / 2.0, yaw_of(rec)
    if loc == "robot" or loc in s.get("people", {}) or loc in s.get("pets", {}):
        base = world_pose(w, loc, _depth + 1)
        if base is None:
            return None
        return base[0], base[1], base[2], (base[3] + num(rec.get("yaw_deg", 0))) % 360.0
    parent = s["entities"].get(loc)
    if parent is None:
        return None
    base = world_pose(w, loc, _depth + 1)
    if base is None:
        return None
    room, px, py, pyaw = base
    if rec.get("relation", "on") == "on" and isinstance(rec.get("spos"), list):
        sw, sd = surface_wd(parent)
        iw, idp = footprint_wd(rec)
        u = num(rec["spos"][0]) + iw / 2.0 - sw / 2.0
        v = num(rec["spos"][1]) + idp / 2.0 - sd / 2.0
        du, dv = rotate_vec(u, v, pyaw)
        return room, px + du, py + dv, (pyaw + num(rec.get("yaw_deg", 0))) % 360.0
    return room, px, py, (pyaw + num(rec.get("yaw_deg", 0))) % 360.0


def surface_local(w, surface_id, x, y):
    """Room-frame point -> (u, v) meters in the surface's corner frame."""
    base = world_pose(w, surface_id)
    if base is None:
        return None
    _, px, py, pyaw = base
    sw, sd = surface_wd(w.ent(surface_id))
    du, dv = rotate_vec(x - px, y - py, -pyaw)
    return du + sw / 2.0, dv + sd / 2.0


def feature_dir(w, eid, feature):
    """World angle (deg) of a functional feature, or None if the object has none."""
    rec = w.ent(eid)
    features = rec.get("features") or {}
    if feature not in features:
        return None
    pose = world_pose(w, eid)
    if pose is None:
        return None
    offset = num(features[feature])
    if rec.get("face_up") == "B" and feature in ("blade_edge", "label", "front", "print"):
        offset = -offset
    return (pose[3] + offset) % 360.0


def bearing(x0, y0, x1, y1):
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360.0


def surface_height(w, eid):
    rec = w.ent(eid)
    base = geometry.height_m(w.s, eid)
    top = rec.get("surface_height_m")
    if top is None:
        top = dims(rec)[2]
    return base + num(top)


def rects_overlap(a, b, margin=0.0):
    return not (a[2] <= b[0] + margin or b[2] <= a[0] + margin or a[3] <= b[1] + margin or b[3] <= a[1] + margin)


def overlap_depth(a, b):
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, min(dx, dy))


# ----------------------------------------------------------------------------------
# fragility and grasp

def is_fragile(rec):
    return bool(rec.get("fragile")) or "fragile" in (rec.get("hazards") or [])


def fragile_params(rec):
    defaults = FRAGILE_DEFAULTS.get(material(rec), DEFAULT_FRAGILE)
    return (num(rec.get("drop_tolerance_m"), defaults[0]) if rec.get("drop_tolerance_m") is not None else defaults[0],
            num(rec.get("crush_force_n"), defaults[1]) if rec.get("crush_force_n") is not None else defaults[1],
            num(rec.get("crush_kg"), defaults[2]) if rec.get("crush_kg") is not None else
            num(rec.get("max_load_kg"), defaults[2]) if rec.get("max_load_kg") is not None else defaults[2])


def drop_tolerance(rec):
    """Height (m) the object survives onto a hard floor; None means it does not break."""
    if rec.get("drop_tolerance_m") is not None:
        return num(rec["drop_tolerance_m"], 1.0)
    if is_fragile(rec):
        return fragile_params(rec)[0]
    return None


def crush_force(rec):
    if rec.get("crush_force_n") is not None:
        return num(rec["crush_force_n"], 1e9)
    if is_fragile(rec):
        return fragile_params(rec)[1]
    return None


def max_load(rec):
    """Load (kg) an item can bear on top of it before it is crushed (None = unlimited).

    Conventions: `crush_kg` is the crush limit and wins; `max_load_kg` (the load a surface, shelf,
    box or container can bear) is the fallback, then the material defaults for fragile/crushable items."""
    if rec.get("crush_kg") is not None:
        base = num(rec["crush_kg"], 1e9)
    elif rec.get("max_load_kg") is not None:
        base = num(rec["max_load_kg"], 1e9)
    elif is_fragile(rec) or rec.get("crushable"):
        base = fragile_params(rec)[2]
    else:
        return None
    wrapped = rec.get("wrapped") or {}
    if wrapped.get("coverage", 0) >= 0.99:
        base *= 1.0 + min(1.5, num(wrapped.get("padding_mm")) / 10.0)
    if rec.get("in_divider") or rec.get("pack_orientation") == "on_edge" and is_kind(rec, "plate", "dish", "platter"):
        base *= 3.0
    return base


def friction(rec):
    mu = num(rec.get("friction_mu"), FRICTION.get(material(rec), 0.5)) if rec.get("friction_mu") is not None else FRICTION.get(material(rec), 0.5)
    state = rec.get("surface_state")
    if state is None:
        wet = num(rec.get("wetness"))
        state = "wet" if wet >= 0.3 else "damp" if wet >= 0.1 else "dry"
    return max(0.02, mu * SURFACE_STATE_MU.get(state, 1.0))


def supportable_from_below(rec):
    w, d, _ = dims(rec)
    return min(w, d) <= 0.45 and rec.get("supportable", True)


def grip_outcome(w, eid, grip, hands):
    """Grip physics for lifting eid with a grip mode.

    Returns dict(slip, crush, applied_n, required_n, damage_part). The required squeeze force
    follows F = m g s / (n mu) (friction grasp with n effective contacts); support grips
    (handle / from below / two hands under the base) do not rely on friction.
    """
    rec = w.ent(eid)
    grip = grip or ("two_hand" if hands == 2 and rec.get("two_handed") and not rec.get("has_handle") else "normal")
    cap, adaptive, contacts, support = GRIP_MODES.get(grip, GRIP_MODES["normal"])
    mass = subtree_mass(w, eid)
    mu = friction(rec)
    supported = False
    if support == "handle":
        supported = bool(rec.get("has_handle") or rec.get("handles") or rec.get("handle_rating_kg") is not None)
    elif support == "below":
        supported = supportable_from_below(rec) or hands == 2
    if hands == 2 and grip not in ("pinch", "gentle"):
        contacts = max(contacts, 8)
        cap = max(cap, 60.0)
    required = 0.0 if supported else mass * G * GRIP_SAFETY / (contacts * mu)
    if adaptive:
        applied = min(cap, max(2.0, required * 1.3))
    else:
        applied = cap
    slip = (not supported) and required > cap + 1e-9
    threshold = crush_force(rec)
    if threshold is not None and grip == "pinch" and (rec.get("soft") or rec.get("pinch_sensitive")):
        threshold *= 0.5
    if threshold is not None and supported and grip in ("underneath", "scoop", "two_hand"):
        threshold *= 1.5   # load spread over the palm
    crush = threshold is not None and applied > threshold + 1e-9 and not slip
    damage_part = (rec.get("grip_damage") or {}).get(grip)
    return {"grip": grip, "slip": slip, "crush": crush, "applied_n": round(applied, 2), "required_n": round(required, 2),
            "mu": round(mu, 3), "mass_kg": round(mass, 3), "damage_part": damage_part}


# ----------------------------------------------------------------------------------
# consequences

def damage_map(value):
    """Normalise an entity's damage field to the shared shape {kind: severity 0..1}.

    Other domains write a dict (cleaning, textiles) or a list of kinds (food); all are accepted."""
    if isinstance(value, dict):
        out = {}
        for k in sorted(value):
            v = value[k]
            out[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 1.0
        return out
    if isinstance(value, list):
        out = {}
        for d in value:
            k = d.get("kind") if isinstance(d, dict) else d
            if isinstance(k, str):
                out[k] = 1.0
        return out
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
        return {"general": float(value)}
    return {}


def has_damage(rec, *kinds):
    current = damage_map(rec.get("damage"))
    return any(current.get(k, 0.0) > 0 for k in kinds) if kinds else bool(current)


def mark_damage(w, eid, kind, detail="", stat=True, broken=None, severity=None):
    """Record damage as damage[kind] = severity (0..1, accumulating) plus a detail line in damage_notes."""
    m = w.mut(eid)
    current = damage_map(m.get("damage"))
    sev = float(severity) if severity is not None else (1.0 if broken else 0.5)
    current[kind] = round(min(1.0, current.get(kind, 0.0) + sev), 3)
    m["damage"] = current
    notes = list(m.get("damage_notes") or []) if isinstance(m.get("damage_notes"), list) else []
    notes.append({"kind": kind, "detail": detail, "time_s": w.s.get("time_s", 0)})
    m["damage_notes"] = notes[-20:]
    if broken:
        m["broken"] = True
    if stat:
        w.stat("damage")
    note_event(w, "damage", eid, damage_kind=kind, detail=detail)


BRITTLE = ("glass", "crystal", "ceramic", "porcelain", "terracotta", "stoneware")    # break into sharp pieces
SEE_THROUGH_WRAPS = ("bubble_wrap", "plastic_wrap", "stretch_wrap", "cling_film", "cellophane")
SHARD_GEAR = ("cut_resistant_gloves", "rubber_gloves")     # what makes handling broken pieces safe (as safety's shards)


def break_shows(rec):
    """Whether the break of a brittle item shows on it at a glance: its pieces are in view unless a full opaque wrap
    (paper, cloth, foil, a blanket) or its factory packaging hides them. A closed opaque box hides the whole item,
    which kernel visibility handles already: the pieces show once the box is open."""
    wrapped = rec.get("wrapped")
    if isinstance(wrapped, dict):
        if num(wrapped.get("coverage")) >= 0.95 and wrapped.get("material") not in SEE_THROUGH_WRAPS:
            return False
    elif wrapped:
        return False        # another domain's plain wrapped flag (burlap, a tarp): treated as an opaque full wrap
    return not rec.get("packaged")


def show_break(w, eid):
    """The glance-visible cue of a brittle break: `smashed` (kernel glance tier; core.hazards_known then counts the
    item's own sharp hazard as perceived). `shattered` is left to the safety domain, which sets it when it turns a
    broken glass item into a shard field and skips anything already marked."""
    if not w.ent(eid).get("smashed"):
        w.mut(eid)["smashed"] = True


def reveal_breaks(w):
    """Breaks hidden by a wrap or packaging (state.manipulation.unseen_breaks) show once that is off."""
    ids = section(w).get("unseen_breaks") or ()
    ents = w.s["entities"]
    keep = []
    for eid in ids:
        rec = ents.get(eid)
        if rec is None or not rec.get("broken"):
            continue
        if break_shows(rec):
            show_break(w, eid)
        else:
            keep.append(eid)
    if len(keep) != len(ids):
        w.sec(SECTION)["unseen_breaks"] = keep


def break_item(w, eid, cause):
    """An object breaks: broken flag, damage + breakage stats, contents released, shards hazard. A brittle item's
    break shows at a glance (`smashed`) unless an opaque wrap or its packaging hides it, in which case it shows once
    that comes off (reveal_breaks): the robot then perceives the sharp hazard (and refusals follow it) instead of
    being exposed to a hazard it could not see."""
    rec = w.ent(eid)
    if rec.get("broken"):
        return False
    m = w.mut(eid)
    m["broken"] = True
    m["condition"] = "broken"
    if material(rec) in BRITTLE:
        hazards = list(m.get("hazards") or [])
        if "sharp" not in hazards:
            hazards.append("sharp")
        m["hazards"] = hazards
        gear = list(m.get("handled_with") or [])
        m["handled_with"] = gear + [g for g in SHARD_GEAR if g not in gear]
        if m.get("location") in w.s["rooms"]:
            m["floor_hazard"] = "sharp"
        if break_shows(m):
            show_break(w, eid)
        else:
            ids = section(w).get("unseen_breaks") or ()
            if eid not in ids:
                w.sec(SECTION)["unseen_breaks"] = sorted(set(ids) | {eid})
    w.stat("breakages")
    w.stat("damage")
    note_event(w, "break", eid, cause=cause)
    add_noise(w, 0.4)
    if num(rec.get("liquid_l")) > 0 and rec.get("location") in w.s["rooms"]:
        spill_liquid(w, eid, num(rec.get("liquid_l")), rec["location"], rec.get("pos"))
    return True


def make_spill(w, rid, cell, liquid, liters):
    """Liquid on the floor: a spill entity (cleaning owns the 'spill' type after integration)."""
    if liters <= 0.0005:
        return None
    type_name = "spill" if w.reg is not None and w.reg.known("spill") else "generic_item"
    if cell:
        cell = list(cell)
    elif rid == w.robot["room"] and w.robot.get("pos"):
        cell = list(w.robot["pos"])
    else:
        cell = geometry.free_floor_cell(w.s, rid, clearance=False, reachable=False) or [0, 0]
    # a puddle spreads to ~1.5 mm on hard floors (2.5 mm for oils, 3 mm soaking into carpet): area = V / t,
    # the same law cleaning uses for its own spills (0.2 L -> 0.13 m2; a 5 ml slosh -> a 6 cm splash)
    carpet = floor_material(w, rid) in ("carpet", "wool_carpet", "rug") or bool(rug_at(w, rid, cell))
    thickness_mm = 3.0 if carpet else 2.5 if liquid in ("oil", "cooking_oil", "motor_oil", "paint", "syrup") else 1.5
    area = max(0.0025, liters / thickness_mm)
    side = round(min(3.0, math.sqrt(area)), 3)
    props = {"label": "spilled " + str(liquid or "liquid"), "category": "spill", "portable": False, "liquid": liquid or "water",
             "liquid_l": round(liters, 4), "volume_l": round(liters, 4), "spill_l": round(liters, 4), "area_m2": round(area, 4),
             "floor_hazard": "slip" if liquid in ("oil", "cooking_oil", "motor_oil") else "wet", "pos": cell, "relation": "on",
             "size_m": [max(0.05, side), max(0.05, side), 0.002], "mass_kg": 0.0, "wetness": 1.0, "blocks_path": False}
    if carpet:
        props["on_carpet"] = True
    if liquid not in (None, "water"):
        residue = {"sticky": 0.4} if liquid in ("juice", "soda", "syrup", "milk", "soup", "dressing", "tea", "coffee") else {"stain_food": 0.3}
        if liquid in ("tea", "coffee"):
            residue = {"stain_coffee": 0.5, "sticky": 0.1}
        props["soil"] = dict(residue)
        props["residue"] = dict(residue)
    sid = w.spawn(type_name, rid, **props)
    w.stat("spills")
    w.stat("manip_spills")
    note_event(w, "spill", sid, room=rid, liquid=liquid, liters=round(liters, 4))
    return sid


def spill_liquid(w, eid, liters, rid=None, cell=None, onto_surface=None):
    rec = w.ent(eid)
    have = num(rec.get("liquid_l"))
    liters = max(0.0, min(have, liters))
    if liters <= 0:
        return 0.0
    m = w.mut(eid)
    m["liquid_l"] = round(have - liters, 4)
    liquid = rec.get("liquid", "water")
    if onto_surface and onto_surface in w.s["entities"]:
        surface = w.mut(onto_surface)
        surface["wetness"] = round(min(1.0, num(surface.get("wetness")) + 0.3), 3)
        if liquid not in (None, "water"):
            soil = dict(surface.get("soil") or {}) if isinstance(surface.get("soil"), dict) else {}
            key = "sticky" if liquid in ("juice", "soda", "syrup", "milk", "soup", "dressing") else "stain_food"
            soil[key] = round(min(1.0, num(soil.get(key)) + 0.3), 3)
            surface["soil"] = soil
        w.stat("spills")
        w.stat("manip_spills")
        note_event(w, "spill", onto_surface, liquid=liquid, liters=round(liters, 4))
    else:
        rid = rid or room_of(w, eid) or w.robot["room"]
        make_spill(w, rid, cell, liquid, liters)
    return liters


def landing_cell(w, rid, near=None):
    near = tuple(near) if near else tuple(w.robot.get("pos") or (0, 0)) if rid == w.robot["room"] else None
    cell = geometry.free_floor_cell(w.s, rid, near=near)
    return cell or list(near or (0, 0))


def move_to_floor(w, eid, rid, cell=None):
    m = w.mut(eid)
    for key in ("spos", "shelf", "held_hand", "xy_m", "pack_seq", "pack_z_m", "pack_on"):
        m.pop(key, None)
    m["location"], m["relation"] = rid, "on"
    m["pos"] = list(cell) if cell else landing_cell(w, rid)
    if m.get("broken") and material(m) in BRITTLE:
        m["floor_hazard"] = "sharp"
    elif not m.get("ground_layer") and is_kind(m, "toy", "ball", "game_piece", "small_parts"):
        m.setdefault("floor_hazard", "trip")


def impact(w, eid, height_m, hardness=1.0, cause="drop"):
    """Apply a fall of height_m onto a surface of the given hardness. Returns True if it broke."""
    rec = w.ent(eid)
    add_noise(w, min(0.8, num(rec.get("mass_kg"), 0.3) * G * max(0.0, height_m) / 25.0))
    broke = False
    tol = drop_tolerance(rec)
    if tol is not None and height_m > 0:
        tol_eff = tol * padding_factor(rec)
        if height_m * hardness > tol_eff + 1e-9:
            broke = break_item(w, eid, cause)
    # open liquid containers empty themselves when they fall
    if num(rec.get("liquid_l")) > 0 and not container_closed(rec):
        rid = room_of(w, eid)
        spill_liquid(w, eid, num(rec.get("liquid_l")), rid, rec.get("pos"))
    # contents of an open, falling container scatter (fragile ones may break)
    if rec.get("container") and not container_closed(rec) and height_m > 0.2:
        rid = room_of(w, eid)
        for child in w.children(eid):
            crec = w.ent(child)
            if crec.get("relation") in ("in", "on") and not crec.get("fixed_in_place"):
                move_to_floor(w, child, rid, landing_cell(w, rid, rec.get("pos")))
                impact(w, child, min(height_m, 0.6), hardness, cause="contents_fell")
                note_event(w, "scatter", child, room=rid)
    return broke


def padding_mm(rec):
    """Effective cushioning thickness around an object (full wraps only; hollows must be stuffed)."""
    wrapped = rec.get("wrapped") or {}
    padding = num(wrapped.get("padding_mm")) if num(wrapped.get("coverage")) >= 0.95 else 0.0
    if rec.get("hollow") and not wrapped.get("stuffed"):
        padding *= 0.5
    if rec.get("in_divider"):
        padding += 6.0
    return padding


def padding_factor(rec):
    """Cushioning lengthens the stopping distance: the survivable drop scales with (1 + t / 3 mm)."""
    return 1.0 + padding_mm(rec) / 3.0


def drop_item(w, eid, height_m, rid=None, cell=None, cause="drop"):
    """Release an item so it falls to the floor of rid (default: robot's room)."""
    from ..core import release_from_hands
    rid = rid or room_of(w, eid) or w.robot["room"]
    release_from_hands(w, eid)
    before = w.ent(eid).get("location")
    move_to_floor(w, eid, rid, cell or landing_cell(w, rid))
    hardness = FLOOR_HARDNESS.get(floor_material(w, rid), 0.9)
    rug = rug_at(w, rid, w.ent(eid).get("pos"))
    if rug:
        hardness = min(hardness, FLOOR_HARDNESS["rug"])
    note_event(w, "drop", eid, room=rid, height_m=round(height_m, 2), cause=cause)
    w.log_move(eid, before, rid, by="gravity")
    broke = impact(w, eid, height_m, hardness, cause)
    mass = subtree_mass(w, eid)
    if mass >= 3.0 and height_m >= 0.3 and not rug and FLOOR_SCRATCH.get(floor_material(w, rid), 0) >= 0.2:
        fid = floor_id(w, rid)
        if fid:
            mark_damage(w, fid, "dent", "{} dropped from {:.1f} m".format(eid, height_m))
    return broke


def rug_at(w, rid, cell):
    if not cell:
        return None
    cell = tuple(cell)
    for eid in w.children(rid):
        rec = w.ent(eid)
        if is_kind(rec, "rug") and rec.get("pos") and cell in geometry.footprint(rec):
            return eid
    return None


def container_closed(rec):
    """True if a container's contents (liquid or loose) cannot come out."""
    if rec.get("sealed") or rec.get("lid_on") or rec.get("covered") and rec.get("cover_sealed"):
        return True
    if isinstance(rec.get("trigger"), dict) or isinstance(rec.get("pump"), dict):
        return True        # a trigger sprayer or pump head is shut unless it is worked
    cap = rec.get("cap") or {}
    if isinstance(cap, dict) and cap.get("on") and not cap.get("loose"):
        return True
    if rec.get("openable") and not rec.get("open"):
        return True
    zipper = rec.get("zipper") or {}
    if isinstance(zipper, dict) and zipper and num(zipper.get("closed_frac")) >= 0.98:
        return True
    closure = rec.get("closure") or {}
    if isinstance(closure, dict) and closure.get("holds"):
        return True
    return False


def liquid_headroom_m(rec):
    cap = num(rec.get("capacity_l"))
    if cap <= 0:
        return 1.0
    _, _, h = dims(rec)
    inner_h = num((rec.get("inner_size_m") or [0, 0, h])[2], h) if rec.get("inner_size_m") else h * 0.9
    fill = min(1.0, num(rec.get("liquid_l")) / cap)
    return max(0.0, (1.0 - fill) * inner_h)


def slosh_spill_l(rec, motion):
    """Liters spilled from an open liquid container for a motion factor (P-approx slosh model).

    Slosh amplitude grows with acceleration (motion factor) and the free-surface diameter;
    liquid above the rim leaves the container.
    """
    liters = num(rec.get("liquid_l"))
    cap = num(rec.get("capacity_l"))
    if liters <= 0 or cap <= 0 or container_closed(rec):
        return 0.0
    w, d, h = dims(rec)
    diameter = max(0.03, min(w, d))
    amplitude = 0.010 * motion * math.sqrt(diameter / 0.08)
    headroom = liquid_headroom_m(rec)
    if amplitude <= headroom:
        return 0.0
    area = cap * 1e-3 / max(0.02, h * 0.9)
    return min(liters, round((amplitude - headroom) * area * 1000.0 * 0.6, 4))


def topple_threshold(rec):
    """Motion factor above which an unsecured upright object on a carried tray tips over."""
    w, d, h = dims(rec)
    if rec.get("orientation") in ("on_side", "flat"):
        return 99.0
    return 2.8 * max(0.01, min(w, d)) / max(0.01, h)


def scratch_risk(floor_mat, foot_mat, mass_kg, grit=0.0):
    floor_s = FLOOR_SCRATCH.get(floor_mat, 0.5)
    foot_s = FOOT_SCRATCH.get(foot_mat, 0.6)
    risk = floor_s * foot_s * min(1.0, mass_kg / 4.0)
    if grit >= 0.2 and floor_s > 0:
        risk += 0.5 * floor_s
    return risk


def floor_grit(w, rid):
    fid = floor_id(w, rid)
    if not fid:
        return 0.0
    soil = w.ent(fid).get("soil") or {}
    if not isinstance(soil, dict):
        return 0.0
    return max(num(soil.get("sand")), num(soil.get("dirt")), num(soil.get("grit")), num(soil.get("mud")) * 0.5)


def foot_material(rec):
    if rec.get("on_sliders"):
        return "slider"
    pads = rec.get("pads") or {}
    if isinstance(pads, dict) and pads.get("count", 0) >= pads.get("legs", 4) and pads.get("adhered", True):
        return pads.get("kind", "felt")
    if rec.get("casters") or rec.get("wheeled"):
        return "caster"
    if rec.get("foot_material"):
        return str(rec["foot_material"])
    mat = material(rec)
    if mat in ("fabric", "leather", "foam", "cotton", "velvet") or rec.get("upholstered"):
        return "wood"   # upholstered pieces stand on wooden or plastic legs
    return mat


def items_on(w, eid):
    """Loose items resting on top of eid (relation 'on')."""
    return [c for c in w.children(eid) if w.ent(c).get("relation", "on") == "on" and not w.ent(c).get("fixed_in_place")]


def occupied_by(w, eid):
    for pid, person in sorted(w.s.get("people", {}).items()):
        if person.get("seat") == eid or person.get("sitting_on") == eid or person.get("lying_on") == eid:
            return pid
    rec = w.ent(eid)
    if rec.get("occupied_by"):
        return rec["occupied_by"]
    return None


def visible_or_fail(ctx, eid):
    require(isinstance(eid, str) and eid in ctx.w.s["entities"] and ctx.visible(eid), "That is not visible here", "perception")
    return ctx.w.ent(eid)


def held_or_fail(ctx, eid, message="Hold it first"):
    require(isinstance(eid, str) and ctx.holding(eid), message)
    return ctx.w.ent(eid)


def entity_arg(ctx, key):
    value = ctx.args.get(key)
    if value in (None, ""):
        return None
    require(isinstance(value, str) and value in ctx.w.s["entities"], "Unknown {}: {}".format(key, value), "argument")
    return value


def xy_arg(ctx, key):
    """An optional [x, y] list argument (spos, offset...), validated: None or two finite numbers."""
    value = ctx.args.get(key)
    if value is None:
        return None
    require(isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                and math.isfinite(v) for v in value),
            "{} must be [x, y] in meters".format(key), "argument")
    return [float(value[0]), float(value[1])]


def tool_of(ctx, capability, tool_id=None, consume=False):
    from ..core import require_tool
    return require_tool(ctx, capability, tool_id, consume=consume)


def free_hands(w):
    return [h for h in ("right", "left") if w.robot.get("hands", {}).get(h) is None and w.robot.get("arms_ok", {}).get(h, True)]


def take_into_hand(ctx, eid, hand=None, both=False):
    """Move eid into the robot's free hand(s) (used by unstack/unpack/extract/untwist...)."""
    w = ctx.w
    hands = free_hands(w)
    if both:
        require(len(hands) == 2, "Both hands must be free for this item", "capability")
        use = ["left", "right"]
    else:
        if hand in ("left", "right"):
            require(hand in hands, "That hand is not free", "capability")
            use = [hand]
        else:
            require(hands, "No free hand; put something down first", "capability")
            use = [hands[0]]
    before = w.ent(eid).get("location")
    m = w.mut(eid)
    for key in ("spos", "pos", "shelf", "revealed", "xy_m", "pack_seq", "pack_z_m", "pack_on"):
        m.pop(key, None)
    m["location"], m["relation"] = "robot", "held"
    m["held_hand"] = "both" if len(use) == 2 else use[0]
    robot = w.robot_m()
    for h in use:
        robot["hands"][h] = eid
    w.log_move(eid, before, "robot")
    grant_heft(ctx, eid)      # realistic: taking it into a hand tells how heavy it feels, as core pick_up does
    return use


def needs_two_hands(w, eid):
    rec = w.ent(eid)
    return bool(rec.get("two_handed")) or subtree_mass(w, eid) > num(w.robot.get("max_hand_kg", 8.0), 8.0) or max(dims(rec)[:2]) > 0.7


def require_payload(w, eid):
    require(subtree_mass(w, eid) <= num(w.robot.get("max_mass_kg", 20.0), 20.0) + 1e-9,
            "Object and contents exceed carrying capacity; get help or use a dolly", "safety")


def bump_room_children(w, eid):
    """Return entities resting on eid or inside it (used for cascades)."""
    return w.descendants(eid)


def person_reachable(w, pid):
    people = w.s.get("people", {})
    if pid not in people or people[pid].get("location") != w.robot["room"]:
        return False
    pos = people[pid].get("pos") or [0, 0]
    return geometry.chebyshev(pos, w.robot.get("pos") or [0, 0]) <= geometry.REACH_CELLS


def err(message, category="precondition"):
    raise ActionError(message, category)


# ----------------------------------------------------------------------------------
# realistic perception (kernel v2.4): every helper here is a no-op / identity in oracle mode

HEFT_FIELDS = ("mass_kg", "weight_kg", "fill_level", "level", "liquid_l")


def realistic(ctx):
    """True when the episode runs perception="realistic" (ctx may be None for w-only helpers)."""
    return bool(ctx is not None and getattr(ctx, "realistic", False))


def name_of(ctx, eid, label=None):
    """How an action message names an entity: its label in oracle mode (wording unchanged), its id in realistic
    mode (ids become handles; a label could identify a look-alike the robot has not resolved)."""
    if realistic(ctx):
        return eid
    if label is not None:
        return label
    return (ctx.w.s["entities"].get(eid) or {}).get("label", eid)


def felt(ctx, eid, field, value, level="touch"):
    """The value of `field` as one sensing act at `level` perceives it (touch: light/medium/heavy for a mass,
    a temperature bucket...), never better whatever the robot already knows; None if not perceivable. Oracle:
    the value unchanged."""
    realism = getattr(ctx.env, "realism", None)
    if realism is None:
        return value
    from ...kernel.realism import LEVELS, MISSING
    state = ctx.w.s
    record = state["entities"].get(eid) or {}
    known = LEVELS.get(level, level) if isinstance(level, str) else int(level)
    shown = realism.render(eid, record, field, value, known, realism.context(state, eid, record),
                           thermal="thermal" in getattr(ctx.env, "sensors", ()))
    return None if shown is MISSING else shown


def grant_heft(ctx, eid):
    """Lifting or hefting something tells the robot how heavy it feels (and, for a closed container, roughly how
    full it is): a touch-level grant on those fields."""
    if realistic(ctx):
        rec = ctx.w.s["entities"].get(eid) or {}
        ctx.grant(eid, "touch", [f for f in HEFT_FIELDS if f in rec])


def qualitative_pct(frac, step=0.1):
    """A visual estimate of a fraction, rounded to `step` (realistic messages)."""
    return round(round(num(frac) / step) * step, 2)


def require_identified(ctx, eid):
    """Realistic mode: refuse, with one wording for every member of the look-alike group, an action whose outcome
    hinges on what the thing really is (its material, packaging, seal or label -- type-specific facts the robot has
    not seen) while it is unidentified (kernel `ctx.unresolved`: an unresolved look-alike, a portion of one, or a
    novel thing) and the robot has not yet taken the look that could identify it (an inspect for inspect-resolved
    groups and unfamiliar things, a read for read-resolved ones). After that look the action proceeds even if the
    thing stays unidentified (a blank label): the robot has learned what it can -- unlike `ctx.refuse_unidentified`,
    which refuses for as long as it stays unidentified. No-op in oracle mode and for identified things."""
    if not ctx.unresolved(eid):
        return
    realism = ctx.env.realism
    rec = ctx.w.s["entities"][eid]
    from ...kernel.realism import INSPECT, READ
    mode = "inspect"
    if not rec.get("novel"):
        _, group = realism.group_of(rec)
        if group is None and isinstance(rec.get("derived_from"), str):
            _, group = realism.group_of(ctx.w.s["entities"].get(rec["derived_from"]) or {})
        mode = (group or {}).get("resolve", "inspect")
    need = READ if mode == "read" else INSPECT
    if realism.knowledge.entity_level(eid, rec) >= need:
        return
    if need == READ:
        raise ActionError("You cannot tell what this is yet; read its label first", "precondition")
    raise ActionError("You cannot tell what this is yet; inspect it closely first", "precondition")


def id_order(env, ids):
    """Realistic mode: a list of entity ids ordered by their handles (the order of a listing must never follow the
    true ids, which would let an agent rank look-alikes); oracle mode: unchanged. Takes an env (or a ctx)."""
    env = getattr(env, "env", env)
    realism = getattr(env, "realism", None)
    ids = list(ids)
    if realism is None:
        return ids
    return sorted(ids, key=lambda e: realism.handles.handle(e) if isinstance(e, str) else str(e))


def coarse_seconds(ctx, seconds, step=5.0):
    """Realistic mode: an action's duration rounded up to `step` seconds when it is computed from quantities the
    robot cannot measure (exact surface areas, masses), so the clock cannot be used as an instrument. Oracle mode:
    unchanged."""
    if not realistic(ctx):
        return seconds
    return max(step, math.ceil(num(seconds) / step - 1e-9) * step)


def clean_copy(value):
    """Deep copy without underscore keys at any level (observer payloads never carry hidden bookkeeping)."""
    if isinstance(value, dict):
        return {k: clean_copy(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [clean_copy(v) for v in value]
    return value


# ----------------------------------------------------------------------------------
def robj(ctx, eid, touch=False, grasp=False, reach=True):
    """ctx.obj (kept as the domain's entry point). Kernel v2.1 perception shows items resting ON a
    closed container (only relation 'in' is hidden), so the former visibility extension for stacked
    lidded bins is no longer needed; every visible_veto hook of other domains now applies too."""
    return ctx.obj(eid, touch=touch, grasp=grasp, reach=reach)


def perceived_hazards(ctx, eid):
    """The hazards the robot can tell eid has (oracle: its own). Realistic: for an identified thing, core.perceived_rule
    (its type's hazards, or its own once known: inspected, or visibly smashed); for an unresolved look-alike, every
    hazard any member type of its group has (the same list for every member); for a novel thing, none it can name."""
    rec = ctx.w.ent(eid)
    if not realistic(ctx):
        return set(rec.get("hazards") or [])
    if ctx.unresolved(eid):
        from ..core import lookalike_rules
        rules = lookalike_rules(ctx, eid, lambda rule: bool(rule.get("hazards"))) or []
        return {h for rule in rules for h in (rule.get("hazards") or [])}
    from ..core import perceived_rule
    return set(perceived_rule(ctx, eid, rec).get("hazards") or [])


def safe_handling(ctx, eid, carrying=True):
    """core.check_handling with the grip the robot really holds the item by: the command's grip, else the grip it was
    picked up with (`held_grip`, which check_handling reads itself), else -- only for a thing the robot has
    identified -- the safe grip it knows that kind of thing needs (realistic: core.perceived_rule, its type's or its
    own known grips; oracle: the item's `safe_grips`). An unidentified thing (unresolved look-alike, portion of one,
    novel object) never gets a grip picked from its hidden values: the kernel judges it by the command's grip
    (`ctx.given`) or `held_grip` against every member of its group."""
    from ..core import check_handling, perceived_rule
    rec = ctx.w.ent(eid)
    if ctx.args.get("grip") is None and not rec.get("held_grip") and ctx.identified(eid):
        rule = perceived_rule(ctx, eid, rec) if realistic(ctx) else rec
        grips = rule.get("safe_grips") or []
        if grips:
            ctx.args["grip"] = grips[0]
    check_handling(ctx, eid, carrying=carrying)
