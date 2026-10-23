"""Shared physical models and world helpers for the manipulation domain.

Fidelity: G (continuous poses on surfaces and room floors, footprints, clearances,
stack geometry) and P-approx (lumped models for grip friction/crush, drop impact,
liquid slosh, floor abrasion, stack load and tipping). Nothing here is a boolean
shortcut: every outcome is derived from masses, sizes, materials, forces and speeds.

Tier-1 adoption (docs/design/TIER1_PHYSICS.md 10.3, TIER1_INTEGRATION.md 6): the physical consequences are the
kernel's. Breaking, falling, spilling, impacts, padding, slosh, carried-load toppling, floor scratches, friction,
masses and crush limits are thin wrappers over kernel/phys (props, impact, liquids, statics, forces, support), so a
glass dropped by a manipulation verb breaks exactly as one knocked off a shelf by a physics law. Where a kernel law
duplicates one of this domain's own rules (crush, stack, edge, slope, carry, wind, hang, handle, contact_heat,
wet_contact, open_spill, furniture_tip, setdown), the domain's copy runs only while the law is deferred
(``kernel_owns(law)`` false) and steps aside once it is active. Robot capabilities (lift capacity, push, pull, wrist
torque, grip force, precision, gripper friction) come from the body (kernel/body.py), never from local constants.

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

from ...kernel import body as kbody, geometry
from ...kernel.errors import ActionError, require
from ...kernel.phys import forces as kforces, impact as kimpact, lawbook, liquids as kliquids, props as kprops
from ...kernel.phys import statics as kstatics, support as ksupport

G = 9.81
CELL = geometry.CELL
SECTION = "manipulation"
HAND_HEIGHT_M = 1.0          # the legacy carry hand height; drops now use body.hand_drop_m (the profile's carry block)


def kernel_owns(law):
    """True while the kernel applies a physics law itself (laws.json state, or lawbook.forced in tests): this
    domain's own copy of that rule then steps aside (TIER1_PHYSICS 10.3 step 2)."""
    return lawbook.active(law)


# ----------------------------------------------------------------------------------
# materials
#
# The tables below are the rows the tier-1 kernel tables were copied from (tests/test_physics_data.py checks they
# stay equal: materials.json mu and surface-state factors, floors.json hardness, scratch and friction, the feet
# kinds). The domain reads the kernel through the wrappers further down; FRAGILE_DEFAULTS keeps only its grip column
# (the squeeze force a two-point grasp crushes at), which the kernel does not model.

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
SPEED_FACTOR = kliquids.SPEED_FACTOR     # gait factor by effective speed (the kernel's carry table: 0.5 / 1.0 / 1.7)
SEATED_HEIGHT_M = 0.45


def push_limit_n(w):
    """Sustained horizontal push the robot can apply (body profile push_n: 220 N for humanoid_v1)."""
    return kbody.limit(w.s, "push_n")


def pull_limit_n(w):
    """Sustained pull while backing up (body profile pull_n: 180 N for humanoid_v1)."""
    return kbody.limit(w.s, "pull_n")


def wrist_limit_nm(w):
    """Wrist torque for twisting caps and screw parts (body profile wrist_nm: 6 N m for humanoid_v1)."""
    return kbody.limit(w.s, "wrist_nm")


def grip_hand(w, eid=None):
    """The hand a grip on eid uses: the hand holding it (the stronger working one when both do), else the first free
    working hand (Ctx.free_hand's order: right, then left), else "right"."""
    hands = w.robot.get("hands") or {}
    holding = [h for h in ("right", "left") if eid is not None and hands.get(h) == eid]
    pool = holding or [h for h in ("right", "left") if hands.get(h) is None and kbody.arm_health(w.s, h) > 0] or ["right"]
    return max(pool, key=lambda h: (kbody.arm_health(w.s, h), h == "right"))


def clamp_force(w, wanted, limit, what):
    """(force applied, note or None): a commanded force above a body limit is clamped to it (TIER1_INTEGRATION 6
    manipulation: force parameters clamped with a note)."""
    wanted = num(wanted)
    if wanted > limit + 1e-9:
        return limit, "{} limited to what the robot can apply".format(what)
    return wanted, None


def commanded_force(ctx, key="force_n", default=30.0, limit="push_n"):
    """The force a command asks for (its force_n), clamped to the body (body.limit push_n for pressing and pulling;
    limit="grip": the hand's power grip force, body.grip_limit_n), with a note when the robot cannot apply it all."""
    w = ctx.w
    wanted = num(ctx.args.get(key), default) if ctx.args.get(key) is not None else default
    cap = kbody.grip_limit_n(w.s, grip_hand(w), "power") if limit == "grip" else kbody.limit(w.s, limit)
    force, note = clamp_force(w, wanted, cap, "Force")
    if note:
        ctx.note(note)
    return force


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
    """Mass of eid and everything on or in it, liquids at their density: the kernel's one subtree mass
    (phys.support.subtree_mass, TIER1_INTEGRATION 1.5)."""
    return ksupport.subtree_mass(w.s, eid)


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
    """The speed the robot really moves at (TIER1_INTEGRATION 1.7): body.effective_speed of the command's speed, else
    the robot's speed mode (quiet mode turns fast into normal; limp mode or weak legs force careful)."""
    speed = ctx.args.get("speed") if isinstance(ctx.args.get("speed"), str) else None
    return effective_speed(ctx.w, speed)


def effective_speed(w, requested=None):
    """body.effective_speed's mode for a requested speed (None: the robot's speed_mode)."""
    return kimpact.effective_speed(w.s, requested or w.robot.get("speed_mode", "normal")) or "normal"


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
WATCH_FIELDS = ("falling", "rolling", "covering", "tilted_on_wheels") + WATCH_TIMED + WATCH_WET


def watch_live(rec):
    """True while a record still carries a dynamic field one of the systems (or the walk-off release of a load
    balanced on its wheels, motion._let_go_when_walked_off) must follow."""
    if rec.get("falling") or rec.get("rolling") or rec.get("covering") or rec.get("tilted_on_wheels"):
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


# Domain occurrences a camera can watch happen (TIER1_INTEGRATION 6, camera: visual occurrences through
# camera.visual_event): in geometric mode the head camera's current pose witnesses them (robot.view.events,
# camera_log.events and, when seen, a "visual" notice); in room mode nothing but the event log entry is written.
# Falls, breaks, tips, tears and spills are kernel consequences and are witnessed by phys/observe.py already.
VISUAL_KINDS = frozenset(("falling", "rolling", "runaway", "slid_off", "stack_toppled", "prop_failed", "case_swung_open",
                          "closure_failed", "handle_tore", "box_bottom_failed", "rack_tipped", "rug_rippled", "blown_away",
                          "recipient_dropped", "hit_person", "struck_person", "contact", "collision", "partner_strained",
                          "partner_yanked", "chains_tangled", "papers_scattered"))
# Domain sounds posted as notices (TIER1_SWEEP 6.1): the clink of hard ware set down or carried in a stack, tape or
# paper ripping, a pushed load banging into something. Levels are dB(A) at 1 m.
NOTICE_KINDS = {"clink": ("hard dishes or glass clink together or on a hard surface", 50.0),
                "rip": ("tape, paper or fabric rips", 55.0),
                "bang": ("something being moved bangs into furniture or a wall", 65.0)}
SOUND_NOTICE = {"set_down": "clink", "clink": "clink", "tape": "rip", "rip": "rip", "collision": "bang"}


def _register_notice_kinds():
    from ...kernel import timeline
    for kind, (doc, db) in sorted(NOTICE_KINDS.items()):
        timeline.register_notice_kind(kind, doc, implies=("any_sound",), db=db)


_register_notice_kinds()


def note_event(w, kind, eid=None, room=None, **data):
    data.pop("kind", None)
    entry = {"room": room or (room_of(w, eid) if eid and eid in w.s["entities"] else w.robot.get("room"))}
    if eid:
        entry["target"] = eid
    entry.update(data)
    logged = w.emit("manip." + kind, **entry)
    if kind in VISUAL_KINDS:
        from ...kernel import camera
        camera.visual_event(w, "manip." + kind, source=eid if eid in w.s["entities"] else None, room=entry["room"],
                            emit=False)
    return logged


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
    kind = SOUND_NOTICE.get(source_kind)
    if kind is not None:
        # a perceivable sound (TIER1_SWEEP 6.1): the timeline decides whether the robot hears it from where it is
        w.notice(kind, room=room, db=db, source=eid if eid in w.s["entities"] else None)


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


class _StateView(object):
    """A w-like view (only .s) for the geometry helpers below."""
    __slots__ = ("s",)

    def __init__(self, s):
        self.s = s


def perceived_size(ctx, eid):
    """The size [w, d, h] the robot sees eid at when that is not its true size, else None: an unresolved look-alike
    (realistic perception) shows its group's common size (realism.group_glance), never the member's own, until it is
    identified or measured."""
    realism = getattr(ctx.env, "realism", None)
    w = ctx.w
    if realism is None or not isinstance(eid, str) or eid not in w.s["entities"] or not ctx.unresolved(eid):
        return None
    rec = w.s["entities"][eid]
    gname, group = realism.group_of(rec)
    if gname is None:
        return None
    size = realism.group_glance(gname, group).get("size_m")
    if not isinstance(size, list) or len(size) < 3:
        return None
    from ...kernel.realism import MEASURE
    if realism.knowledge.level(eid, rec, "size_m") >= MEASURE:
        return None
    size = [float(v) for v in size[:3]]
    return None if size == [num(v) for v in (rec.get("size_m") or [])[:3]] else size


def perceived_view(ctx, *eids):
    """A view of the world in which every listed unresolved look-alike has the size the robot sees (perceived_size):
    for aiming at a reference the robot only knows by eye (rotate face_toward, arrange relative_to / facing, align to a
    reference), so every member of a group gets the same answer. Contact physics (knocks, fits) keeps the true size.
    Returns ctx.w itself when nothing is hidden."""
    sizes = {}
    for eid in eids:
        size = perceived_size(ctx, eid)
        if size is not None:
            sizes[eid] = size
    if not sizes:
        return ctx.w
    entities = dict(ctx.w.s["entities"])
    for eid, size in sizes.items():
        entities[eid] = dict(entities[eid], size_m=size)
    return _StateView(dict(ctx.w.s, entities=entities))


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


def phys(w, eid):
    """The kernel's physical properties of an entity (phys.props: record, rows, material class, per-instance
    variation)."""
    return kprops.props(w.s, eid)


def drop_tolerance(w, eid):
    """Height (m) eid survives dropped onto a hard (tile) floor, the kernel's per-instance value; None when it never
    breaks or chips."""
    p = phys(w, eid)
    if kimpact.break_mode(p) is None:
        return None
    return ksupport.finite(p.drop_tol_m)


def crush_force(w_or_rec, eid=None):
    """Squeeze force (N) at which a two-point grasp crushes or cracks the item, None when grip force cannot hurt it.
    This is grasp mechanics, which the kernel does not model: the record's crush_force_n, else for a fragile item the
    grip column of FRAGILE_DEFAULTS by its kernel material class (glass 40 N, crystal 25 N, porcelain 60 N, eggshell
    25 N...). Accepts (w, eid) or a record."""
    rec = w_or_rec.ent(eid) if eid is not None else w_or_rec
    if rec.get("crush_force_n") is not None:
        return num(rec["crush_force_n"], 1e9)
    if not is_fragile(rec):
        return None
    cls = kprops.material_class(None, rec)
    return (FRAGILE_DEFAULTS.get(cls) or FRAGILE_DEFAULTS.get(material(rec)) or DEFAULT_FRAGILE)[1]


def crush_limit(w, eid):
    """Load (kg) eid bears on top before it is crushed, None when nothing on it can crush it: the kernel's crush
    limit (phys.statics.crush_limit_kg: crush_kg, else a box's max_load_kg, else its material; x(1 + padding) for a
    full wrap, x3 for plates on edge or in dividers, x5 under a stack of its own kind, wet cardboard 0.4)."""
    return kstatics.crush_limit_kg(w.s, eid)


def friction(rec):
    """Sliding friction of an item on a finished reference surface, wet or soapy states included: the kernel's
    (phys.forces.friction; record friction_mu wins)."""
    return kforces.friction(rec)


REF_PAD_MU = 0.8     # the humanoid hand's pad friction (profiles.json grippers.hand.mu): the GRIP_MODES caps' calibration


def grasp_mu(w, eid, hand=None):
    """Friction of the robot's grasp on eid: the item's surface friction scaled by the hand's grasp friction against a
    clean, healthy humanoid hand (body.grip_mu: the pads' mu, lowered by slippery soil on the fingers and by a
    gripper_slip fault; a parallel gripper's pads grip less than a hand's)."""
    hand = hand if hand in ("left", "right") else grip_hand(w, eid)
    return max(0.02, friction(w.ent(eid)) * kbody.grip_mu(w.s, hand) / REF_PAD_MU)


RATED_GRIPS = ("normal", "handle")    # the adaptive one-hand squeeze the kernel's lift plan presumes


def rated_squeeze_n(w, eid, hand, contacts):
    """Squeeze (N) the normal adaptive one-hand grip may reach on eid: what holds the hand's rated load
    (body.hold_capacity_kg, the capacity the kernel's lift plan gives one hand) on a dry surface of eid with a clean
    hand, F = m g s / (n mu_dry). So whatever the lift plan hands to one hand never slips on its mass alone (lift
    monotonicity: an 8.0 kg item held one-handed like a 7.5 kg one, not dropped where an 8.01 kg one is held with both
    hands); a wet, oily or soapy surface, slippery fingers or a gripper_slip fault still make it slip. The hardware
    limit (body.grip_limit_n) clamps it afterwards."""
    rated = kbody.hold_capacity_kg(w.s, hand)
    mu_dry = max(0.02, kforces.friction(w.ent(eid), wet=False) * kbody.gripper(w.s, hand)["mu"] / REF_PAD_MU)
    return rated * G * GRIP_SAFETY / (max(1, contacts) * mu_dry)


def supportable_from_below(rec):
    w, d, _ = dims(rec)
    return min(w, d) <= 0.45 and rec.get("supportable", True)


PINCH_GRIPS = ("pinch", "edges")


def grip_outcome(w, eid, grip, hands):
    """Grip physics for lifting eid with a grip mode.

    Returns dict(slip, crush, applied_n, required_n, damage_part). The required squeeze force
    follows F = m g s / (n mu) (friction grasp with n effective contacts; mu the grasp friction, grasp_mu); support
    grips (handle / from below / two hands under the base) do not rely on friction. A grip mode's force is a commanded
    level clamped to the hand's hardware limit (body.grip_limit_n: power 150 N, pinch 30 N for a humanoid hand).
    """
    rec = w.ent(eid)
    # no grip named: both hands cradle it from below (the "two_hand" grip the pick_up hook records as held_grip),
    # one hand squeezes it ("normal")
    grip = grip or ("two_hand" if hands == 2 else "normal")
    cap, adaptive, contacts, support = GRIP_MODES.get(grip, GRIP_MODES["normal"])
    mass = subtree_mass(w, eid)
    hand = grip_hand(w, eid)
    mu = grasp_mu(w, eid, hand)
    supported = False
    if support == "handle":
        supported = bool(rec.get("has_handle") or rec.get("handles") or rec.get("handle_rating_kg") is not None)
    elif support == "below":
        supported = supportable_from_below(rec) or hands == 2
    if hands == 2 and grip not in ("pinch", "gentle"):
        contacts = max(contacts, 8)
        cap = max(cap, 60.0)
    if hands == 1 and grip in RATED_GRIPS and not supported:
        cap = max(cap, rated_squeeze_n(w, eid, hand, contacts))
    cap = min(cap, kbody.grip_limit_n(w.s, hand, "pinch" if grip in PINCH_GRIPS else "power"))
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
SEE_THROUGH_WRAPS = kimpact.SEE_THROUGH_WRAPS
SHARD_GEAR = kimpact.SHARD_GEAR            # what makes handling broken pieces safe (as safety's shards)


def break_shows(rec):
    """Whether the break of a brittle item shows on it at a glance: its pieces are in view unless a full opaque wrap
    (paper, cloth, foil, a blanket) or its factory packaging hides them (the kernel's rule, phys.impact). A closed
    opaque box hides the whole item, which kernel visibility handles already: the pieces show once the box is open."""
    wrapped = rec.get("wrapped")
    if isinstance(wrapped, dict):
        if num(wrapped.get("coverage")) >= 0.95 and wrapped.get("material") not in SEE_THROUGH_WRAPS:
            return False
    elif wrapped:
        return False        # another domain's plain wrapped flag (burlap, a tarp): treated as an opaque full wrap
    return not rec.get("packaged")


def show_break(w, eid):
    """The glance-visible cue of a brittle break: the kernel's `shattered` (phys.impact.break_entity writes it when the
    break shows; core.hazards_known then counts the item's own sharp hazard as perceived)."""
    if not w.ent(eid).get("shattered"):
        w.mut(eid)["shattered"] = True


def note_hidden_break(w, eid):
    """A shattering break an opaque wrap or the packaging hides is remembered (state.manipulation.unseen_breaks) until
    that comes off (reveal_breaks, run by the manipulation.breaks system)."""
    ids = section(w).get("unseen_breaks") or ()
    if eid not in ids:
        w.sec(SECTION)["unseen_breaks"] = sorted(set(ids) | {eid})


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


def break_item(w, eid, cause, by=None):
    """An object breaks: the kernel's break (phys.impact.break_entity: broken, damage.broken, its liquid run down to
    the floor, a shattering item `shattered` unless a wrap or its packaging hides it, glass and ceramics sharp, the
    phys.broke hooks, the breakage counted when the robot caused it). A hidden break shows once unwrapped
    (the phys.broke hook in world.py remembers it). Returns True when it broke now."""
    rec = w.ent(eid)
    if rec.get("broken"):
        return False
    broke = kimpact.break_entity(w, eid, cause, by=by)
    if broke:
        add_noise(w, 0.4)
    return broke


def make_spill(w, rid, cell, liquid, liters, source=None, by=None):
    """Liquid on the floor of rid at cell (default the robot's cell when it is there): the kernel spill
    (phys.liquids.spill: below 5 ml droplets and nothing else; a phys.spill_sink entity, cleaning's, else a kernel
    puddle of area V / film thickness that joins the room's wet cells). Returns the spill's id or None."""
    if cell is None and rid == w.robot.get("room") and w.robot.get("pos"):
        cell = list(w.robot["pos"])
    at = (rid, list(cell)) if cell is not None else rid
    return kliquids.spill(w, liters, liquid or "water", at=at, source=source, by=by)


def spill_liquid(w, eid, liters, rid=None, cell=None, onto_surface=None, by=None):
    """liters of eid's liquid leave it: onto a surface (it and the supports under it catch what they can, the rest runs
    to the floor) or onto the floor of rid at cell (phys.liquids.spill). Returns the litres that left."""
    rec = w.ent(eid)
    have = kliquids.free_liquid_l(rec)
    liters = max(0.0, min(have, num(liters)))
    if liters <= 0:
        return 0.0
    w.mut(eid)["liquid_l"] = round(num(rec.get("liquid_l")) - liters, 4)
    liquid = rec.get("liquid") or "water"
    if onto_surface and onto_surface in w.s["entities"]:
        kliquids.spill(w, liters, liquid, onto=onto_surface, source=eid, by=by)
    else:
        rid = rid or room_of(w, eid) or w.robot["room"]
        if cell is None and rid == w.robot.get("room") and w.robot.get("pos"):
            cell = list(w.robot["pos"])
        kliquids.spill(w, liters, liquid, at=(rid, list(cell)) if cell is not None else rid, source=eid, by=by)
    return liters


def landing_cell(w, rid, near=None):
    near = tuple(near) if near else tuple(w.robot.get("pos") or (0, 0)) if rid == w.robot["room"] else None
    cell = geometry.free_floor_cell(w.s, rid, near=near)
    return cell or list(near or (0, 0))


def mark_floor_hazard(w, eid):
    """A loose toy, ball or game piece left on a floor is a trip hazard (a broken brittle item already got the kernel's
    sharp floor hazard)."""
    rec = w.ent(eid)
    if rec.get("location") in w.s["rooms"] and not rec.get("ground_layer") and not rec.get("floor_hazard") and \
            is_kind(rec, "toy", "ball", "game_piece", "small_parts"):
        w.mut(eid)["floor_hazard"] = "trip"


def move_to_floor(w, eid, rid, cell=None):
    """Put eid down on the floor of rid without a fall (a cover the wind took, a scenario relocation)."""
    m = w.mut(eid)
    for key in ("spos", "shelf", "held_hand", "xy_m", "pack_seq", "pack_z_m", "pack_on"):
        m.pop(key, None)
    m["location"], m["relation"] = rid, "on"
    m["pos"] = list(cell) if cell else landing_cell(w, rid)
    if m.get("broken") and material(m) in BRITTLE:
        m["floor_hazard"] = "sharp"
    mark_floor_hazard(w, eid)


def impact(w, eid, height_m, hardness=1.0, cause="drop", by=None):
    """An impact where eid is, equal to a drop of height_m onto a surface of the given hardness (its own padding
    applied): the kernel's impact (phys.impact.impact: it breaks, chips or bruises above its per-instance drop
    tolerance). A fall that moves the item is drop_item. Returns True if it broke."""
    rec = w.ent(eid)
    add_noise(w, min(0.8, num(rec.get("mass_kg"), 0.3) * G * max(0.0, height_m) / 25.0))
    h_eff = max(0.0, num(height_m)) * num(hardness, 1.0) / padding_factor(rec)
    return kimpact.impact(w, eid, h_eff, cause, by=by)


def padding_mm(rec):
    """Effective cushioning thickness around an object (full wraps only; hollows must be stuffed; +6 mm in a cell
    divider): the kernel's phys.statics.padding_mm."""
    return kstatics.padding_mm(rec)


def padding_factor(rec):
    """Cushioning lengthens the stopping distance: the survivable drop scales with (1 + t / 3 mm)
    (phys.statics.padding_factor)."""
    return kstatics.padding_factor(rec)


def drop_item(w, eid, height_m, rid=None, cell=None, cause="drop", by=None):
    """eid falls height_m to the floor of rid (default: its room, else the robot's), landing at or next to cell: the
    kernel's fall (phys.impact.fall: out of the hands, a free floor cell, the landing's hardness and cushion and the
    item's padding, breakage, floor dents, an open container's liquid and contents, phys.fall and the phys.fell
    hooks; attributed to the robot in its action or when it arranged the item). Returns True if it broke."""
    rid = rid or room_of(w, eid) or w.robot["room"]
    if rid not in w.s["rooms"]:
        rid = w.robot["room"]
    add_noise(w, min(0.8, num(w.ent(eid).get("mass_kg"), 0.3) * G * max(0.0, num(height_m)) / 25.0))
    info = kimpact.fall(w, eid, height_m=max(0.0, num(height_m)), cause=cause,
                        at=(rid, list(cell) if cell is not None else None), by=by)
    if eid in w.s["entities"]:
        mark_floor_hazard(w, eid)
    return (info or {}).get("outcome") == "broke"


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
    """True if a container's contents (liquid or loose) cannot come out (phys.liquids.container_closed, the rule this
    domain wrote)."""
    return kliquids.container_closed(rec)


def liquid_headroom_m(rec):
    """Empty wall above the liquid (phys.liquids.headroom_m); 1 m for something with no capacity (it holds no liquid)."""
    if num(rec.get("capacity_l")) <= 0:
        return 1.0
    return kliquids.headroom_m(rec)


def slosh_spill_l(rec, motion):
    """Liters sloshed out of an open liquid container for a motion factor: the kernel's carry model
    (phys.liquids.slosh_spill_l, this domain's P-approx model moved to the kernel unchanged)."""
    return kliquids.slosh_spill_l(rec, motion)


def topple_threshold(rec):
    """Motion factor above which an unsecured upright object riding on a carried load tips over
    (phys.liquids.topple_threshold: 2.8 x min(w, d) / h)."""
    return kliquids.topple_threshold(rec)


def tip_spill_l(rec, orientation):
    """Litres an open container loses lying on its side or upside down (phys.liquids.tip_spill_l: a wide mouth loses
    everything on its side, a narrow neck keeps what lies below it; upside down everything)."""
    return kliquids.tip_spill_l(rec, orientation)


def tilt_spill_l(rec, deg):
    """Litres an open container loses tilted by deg from upright (phys.liquids.tilt_spill_l: spill starts at
    tan(a) = 2 headroom / D)."""
    return kliquids.tilt_spill_l(rec, deg)


def scratch_risk(floor_mat, foot_mat, mass_kg, grit=0.0):
    """Floor abrasion risk of a dragged foot (the kernel's floors.json scratch columns: floor x foot x min(1, m / 4 kg),
    plus half the floor's when grit lies under it); 0.3 or more leaves a visible scratch."""
    floor_row = kprops.FLOORS["floors"][kprops.floor_kind(floor_mat)]
    floor_s = num(floor_row.get("scratch"), 0.5)
    feet = kprops.FLOORS["feet"]
    foot_s = num((feet["kinds"].get(FOOT_KIND.get(foot_mat, foot_mat)) or {}).get("scratch"), feet["default_scratch"])
    risk = floor_s * foot_s * min(1.0, num(mass_kg) / 4.0)
    if grit >= kforces.GRIT_SCRATCH and floor_s > 0:
        risk += 0.5 * floor_s
    return risk


# sliding aids and legacy foot words -> floors.json feet kinds
FOOT_KIND = {"towel": "cotton", "blanket": "cotton", "fabric": "cotton", "metal": "steel", "slider": "slider",
             "felt": "felt", "caster": "caster"}


def scratch_floor(w, rid, eid, distance_m, aid_kind=None, by=None):
    """Drag eid distance_m over the floor of rid: the kernel's floor scratch (phys.forces.scratch: risk >= 0.3 adds
    risk x 0.1 per metre to the floor's damage.scratch and emits phys.scratch). With a sliding aid under it (a towel,
    blanket or sliders) the aid's foot kind replaces the item's own feet. Returns the severity added."""
    fid = floor_id(w, rid)
    if not fid or distance_m <= 0:
        return 0.0
    if aid_kind is None:
        return kforces.scratch(w, fid, eid, distance_m, by=by)
    risk = scratch_risk(floor_material(w, rid), aid_kind, subtree_mass(w, eid), floor_grit(w, rid))
    if risk < kforces.SCRATCH_VISIBLE_RISK:
        return 0.0
    severity = round(risk * kforces.SCRATCH_SEVERITY_PER_M * num(distance_m), 4)
    level = kimpact.damage_map(w.ent(fid).get("damage")).get("scratch", 0.0) + severity
    kimpact.damage(w, fid, "scratch", min(1.0, level), detail="{} dragged {:.1f} m on {}".format(eid, distance_m, aid_kind),
                   by=by, event="phys.scratch", note="{} was scratched".format(fid), ids=[eid], mover=eid,
                   distance_m=round(float(distance_m), 4), foot=aid_kind, added=severity)
    return severity


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
    """Free hands with a working arm (body.arm_health > 0; an arm the arms_ok mirror marks out counts as failed)."""
    held = w.robot.get("hands") or {}
    return [h for h in ("right", "left") if held.get(h) is None and kbody.arm_health(w.s, h) > 0]


def take_into_hand(ctx, eid, hand=None, both=False, grip=None):
    """Move eid into the robot's free hand(s) (used by unstack/unpack/extract/untwist...), through the body like core
    pick_up (TIER1_INTEGRATION 1.5): the grasp its size allows (ctx.require_grasp: a pinch for a small item unless a
    grip was given; too small or a power grip on a small item refused) and the derated capacity of the hands used and
    the total carried load (ctx.check_lift: an overload is refused at lift-off, counted and felt; nothing drops).
    Returns the hands used."""
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
    grip = ctx.require_grasp(eid, use[0] if len(use) == 1 else "both", grip)
    ctx.check_lift(eid, use, subtree_mass(w, eid))
    before = w.ent(eid).get("location")
    m = w.mut(eid)
    for key in ("spos", "pos", "shelf", "revealed", "xy_m", "pack_seq", "pack_z_m", "pack_on"):
        m.pop(key, None)
    m["location"], m["relation"] = "robot", "held"
    m["held_hand"] = "both" if len(use) == 2 else use[0]
    if grip:
        m["held_grip"] = grip
    robot = w.robot_m()
    for h in use:
        robot["hands"][h] = eid
    w.log_move(eid, before, "robot")
    grant_heft(ctx, eid)      # realistic: taking it into a hand tells how heavy it feels, as core pick_up does
    return use


def needs_two_hands(ctx, eid):
    """Whether lifting eid takes both hands: the kernel's lift plan (phys.forces.lift_plan: heavier than one hand's
    derated capacity, wider than 0.7 m or two_handed)."""
    return len(ctx.lift_plan(eid)["hands"]) == 2


def bump_room_children(w, eid):
    """Return entities resting on eid or inside it (used for cascades)."""
    return w.descendants(eid)


# where a label stuck on a face points, as a geometric-camera face (sight 4.6: "up", "down" or degrees from the front)
# apply_label face= -> the kernel's label_facing word (sight.LABEL_FACING: the "label" face a camera must see to read
# label_text, TIER1_CAMERA 4.6); flips and lay-downs carry it round
LABEL_FACE_SPEC = {"front": "front", "side": "right", "back": "back", "end": "left", "top": "top", "lid": "top",
                   "bottom": "bottom"}
FLIP_FACE = {"top": "bottom", "bottom": "top", "up": "down", "down": "up"}
SIDE_FACE = {"top": "right", "up": "right", "bottom": "left", "down": "left"}


def geometric(w):
    """True in the geometric camera mode (robot.view exists): camera-only bookkeeping is written then and only then,
    so room-mode states and observations stay exactly as they were."""
    return isinstance(w.robot.get("view"), dict)


def label_face(w, eid):
    """The face a label on eid points at (the kernel's label_facing word), or None."""
    return w.ent(eid).get("label_facing")


def set_label_face(w, eid, face):
    """Geometric camera mode: the label on eid faces `face` (label_facing: "top", "bottom", "front", ...; the camera
    reads label_text only from that side) or the label is gone (None). Room mode: nothing is written."""
    if not geometric(w):
        return
    rec = w.ent(eid)
    if face is None:
        if "label_facing" in rec:
            w.mut(eid).pop("label_facing", None)
    elif rec.get("label_facing") != face:
        w.mut(eid)["label_facing"] = face


def turn_label(w, eid, mapping):
    """An orientation change carries the label's face with it (FLIP_FACE: turned over; SIDE_FACE: laid on its side)."""
    face = label_face(w, eid)
    if face in mapping:
        set_label_face(w, eid, mapping[face])


def walk_robot(ctx, cell, room=None, connection=None, extra_s=0.0):
    """The robot walks as part of a verb (pushing a chair, wheeling a bin into the next room, following a co-carry
    partner): the body's mobility guard (ctx.require_mobile; a stairs crossing needs a body that climbs stairs), a
    route in the current room to `cell` -- or, crossing `connection` into `room`, to that connection's doorway cell --
    and ctx.motion (the walk paid against the battery, the camera's motion frames, TIER1_INTEGRATION 1.4). The robot
    ends on `cell` (in `room` after a crossing). Returns True, or False when the battery cut the walk: the robot then
    stopped at the last cell it reached in its own room and powers down at settlement."""
    w = ctx.w
    state = w.s
    here = state["robot"]["room"]
    start = list(state["robot"].get("pos") or [0, 0])
    stairs = connection is not None and (state["connections"].get(connection) or {}).get("kind") == "stairs"
    ctx.require_mobile(stairs=stairs)
    goal = list((state["connections"][connection].get("cells") or {}).get(here, cell)) if connection else list(cell)
    if goal == start and connection is None:
        return True
    path = geometry.find_path(state, here, start, [tuple(goal)], hazard_filter=ctx.route_filter) if goal != start else []
    if path is None:
        path = [goal]               # hemmed in by what it moves: the last step is alongside the load itself
    walked = ctx.motion(path, start, extra_s=extra_s, connection=connection)
    robot = w.robot_m()
    if ctx.data["motion"]["cut"] is not None:
        robot["pos"] = list(walked[-1]) if walked else start
        return False
    if connection is not None:
        robot["room"] = room
    robot["pos"] = list(cell)
    return True


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
    (its type's hazards, or its own once known: inspected, or visibly shattered); for an unresolved look-alike, every
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
