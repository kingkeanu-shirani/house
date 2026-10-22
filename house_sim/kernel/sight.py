"""Geometric sight: line of sight, occlusion, light, glare, faces and acuity inside one room (pure; never writes).

This is the geometry library of the geometric camera (docs/design/TIER1_CAMERA.md sections 4-7 and 10.1) shared by
every viewer: the robot's head and wrist cameras (through kernel/camera.py), people and device cameras. It reads state
and returns numbers; it writes nothing and registers nothing, and room-mode simulation never calls it.

Frames and units. Metres in the room frame of climate/sun.py and manipulation.world_pose: x east along the columns,
y south along the rows, z up; cell (c, r) has its centre at ((c + 0.5) * CELL, (r + 0.5) * CELL). Angles use the
grid convention deg = atan2(dy, dx) (0 = east, 90 = south); compass fields convert with grid_deg(). Relative bearings
are wrapped to (-180, 180], positive to the viewer's right; elevation is positive up. An eye is (rid, x, y, z) or an
Eye / Viewer; its `ignore` ids never occlude for it.

Occluders. Every entity whose largest dimension is >= 0.25 m and that stands on the floor, hangs on a wall at least
0.25 m deep, or rests (through "on" relations) on a room-located surface while 0.25 m tall and wide, becomes vertical
spans (z_lo, z_hi, porosity) over the grid cells its box covers; open hinged door leaves and @hook("view_occluders")
results join them. Spans with porosity < 0.5 are solid; the others attenuate. People, pets and (for viewers other
than the robot) the robot are dynamic occluders tested per ray. room_geom() builds the spans once per occlusion
signature (occ_sig) and caches them by content; viewshed() casts one Amanatides-Woo ray per cell from an eye and
stores, per cell, the slope interval each crossed source blocks (one interval per source span, so a ray that passes
under a table and then over a low box stays visible).

Relation rules (5.4). A sample point is tested against the viewshed with its own entity and its support or container
skipped. The support is then tested exactly against the ray: the near-edge rule for tops above the eye, the clearance
rule under furniture and the line over the back edge for things behind it all follow from one exact span test along
the ray segment that lies between the eye and the point. Items inside containers must enter through the front or top
opening and pass their neighbours.

Robot numbers. Eye heights come from body.eye_height_m, the carry geometry of held items from the body profile's
"carry" block, the carried load from phys.support.held_envelope and resting sizes from phys.support.resting_size
(TIER1_INTEGRATION 1.2 and 1.6: one source for each). Tests may replace those siblings through _BRIDGES.

Doorway and window apertures (5.8; camera phase 3). Rooms are separate grids, joined for the eye only through an
opening: an open doorway (a closed glazed door passes its porosity) places the next room beyond the wall by a rigid
transform (doorway_views, doorway_aperture, to_room / from_room), and through_transmission traces a point there
through both rooms' occluders (the eye's room by its viewshed, the next one by an opening viewshed from the eye's
image outside its grid). aperture_viewer stands outside a window or door looking in (its coverings and the glass's
daylight reflection apply); sees_entity and aperture_transmission see through the opening only.

Caches. Room geometry is keyed by its occlusion signature (occ_sig), which a World memoizes per room epoch and the
view_occluders hook output: a room's epoch changes when a source of that room (an occluder or container candidate),
a thing it rests on or one of its doors was written in place (judged lazily, only when that room is asked about),
and a global epoch when something was created, destroyed, moved to another place or became or stopped being a
source, on a rollback or when track of the World's touch log was lost (_judge). light_epoch does the same for the
camera's ambient-light memo. Caches are keyed by content and never change results.

Fidelity: G for line of sight and occlusion at cell-and-box resolution; P-approx for light, glare and acuity (closed
forms, not calibrated against a real detector).
"""
import collections
import hashlib
import heapq
import importlib
import json
import marshal
import math
import weakref

from . import clock, geometry
from .plugins import HOOKS, run_hooks
from .predicates import root_room
from .registry import default_registry

CELL = geometry.CELL
EPS = 1e-9
INF = float("inf")
NEG_INF = float("-inf")
MIN_DIST_M = 0.05           # nearer than this a point counts as touching the lens (in_frame minimum)


def focal_px(res_w, hfov_deg):
    """Focal length in pixels of a pinhole camera: res_w / (2 tan(hfov / 2))."""
    return res_w / (2.0 * math.tan(math.radians(hfov_deg) / 2.0))


# The robot's optics (TIER1_CAMERA 4.2): an RGB-D head camera of the Intel RealSense D435 class (RGB 69 x 42 degrees
# used at 1280 x 720; depth 87 x 58 degrees at 848 x 480, useful 0.2-6 m, active IR to about 4 m in the dark), a
# D405-class wrist camera (87 degrees at 640 x 480, 7 cm minimum focus) with a ring LED, and a low 2D lidar
# (RPLIDAR-class, 8 m used, read in 24 sectors). Head pan/tilt rates are those of humanoid neck servos (90-120
# degrees/s); a humanoid turns in place at about 90 degrees/s. Eye heights are the body profile's (TIER1_INTEGRATION
# 1.2). kernel/camera.py uses this table as camera.HW.
HW = {
    "head": {"hfov_deg": 69.0, "vfov_deg": 42.0, "res_px": [1280, 720], "focal_px": round(focal_px(1280, 69.0), 1),
             "pan_dps": 120.0, "tilt_dps": 90.0, "pan_limit_deg": 150.0, "tilt_limits_deg": [-60.0, 35.0],
             "max_range_m": 12.0},
    "depth": {"hfov_deg": 87.0, "vfov_deg": 58.0, "res_px": [848, 480], "focal_px": round(focal_px(848, 87.0), 1),
              "range_m": [0.2, 6.0], "ir_range_m": 4.0},
    "wrist": {"hfov_deg": 87.0, "vfov_deg": 58.0, "res_px": [640, 480], "focal_px": round(focal_px(640, 87.0), 1),
              "min_focus_m": 0.07, "led": {"power": 0.12, "half_deg": 45.0}, "deep_m": 1.0},
    "lidar": {"height_m": 0.25, "range_m": 8.0, "sectors": 24, "step_m": 0.25},
    "body_turn_dps": 90.0, "capture_s": 0.4,
    "tilt": {"look": -10.0, "walk": -15.0, "low_row": -45.0, "up": 20.0, "down": -45.0},
}

# Pixels across a target (section 7). Johnson-style criteria; detectors are unreliable on COCO "small" objects
# (< 32 px), so detection is sure only from 24 px and probabilistic from 10 px (the oracle uses the 50 % point, 17).
PX = {"detect_min": 10.0, "detect_oracle": 17.0, "detect_sure": 24.0, "recognize": 32.0, "identify": 64.0,
      "inspect": 160.0, "read_char": 10.0, "depth_shape": 24.0}
# Light at the target, in the scalar units of perception.light_level (1.0 = a brightly lit room, 0.2 = DARK).
LIGHT = {"detect": 0.05, "recognize": 0.12, "identify": 0.2, "inspect": 0.2, "read": 0.25, "contrast": 0.45}
READ_MIN_CLEAN = 0.4        # perception's dirty-lens limit: below it no text is readable
K_MOTION = {"careful": 1.0, "normal": 0.8, "fast": 0.55}    # motion-blur factor of frames taken while walking
SIZE_IR_MIN_M = 0.4         # IR "dark shape" size floor (C11: today's darkness rule)

OCCLUDER_MIN_M = 0.25       # largest dimension (and, on surfaces, height and width) of anything that occludes
FLAT_MAX_M = 0.03           # flat things (mats, puddles, papers) never occlude
WEDGE_MARGIN_DEG = 15.0     # a single-frame viewshed also computes cells this far outside the frame (cell width)
WEDGE_NEAR_M = 1.5          # ... and every cell this close, whatever its centre bearing
FRONT_CONE_DEG = 75.0       # an eye sees into a front opening only from within this angle of its front
FACE_SEEN_COS = math.cos(math.radians(80.0))    # a side face is seen within 80 degrees of its normal
FACE_UP_MARGIN_M = 0.05     # the "up" face needs the eye this far above the top
FACE_BLOCK_M = 0.05         # a face this close to a wall or a neighbour is blocked for every camera
FACE_COVER_FRAC = 0.5       # things covering half the top hide the "up" face (stacked boxes)
BEHIND_SLOT_M = 0.3         # a space up to a hand's width between a piece and the wall is a shaded slot
SEAT_BACK_M = 0.2           # a seat's back (sofa, armchair) stands this deep along its back side above the seat ...
SEAT_MIN_DROP_M = 0.1       # ... when its surface (surface_height_m) lies at least this far under its top
GEOM_CACHE_SIZE = 64        # rooms
VIEWSHED_CACHE_SIZE = 2048  # 64 rooms x 32 eyes

# View properties (4.3): every entity has these; the category row, the type's "view" block (registry) and the
# record's own "view" dict override them in that order.
VIEW_DEFAULTS = {"porosity": 0.0, "clear_below_m": 0.0, "below_porosity": 1.0, "slab_m": None,
                 "behind_gap_m": 0.05, "opening": None, "interior_light": False, "front_deg": None, "faces": {},
                 "text_m": {}, "glossy": False, "camera": None, "beam": None, "height_m": None}
TRANSPARENT_MATERIALS = frozenset(["glass", "clear_plastic", "acrylic"])    # porosity 0.9 (frames, reflections)
GLOSSY_MATERIALS = frozenset(["glass", "clear_plastic", "acrylic", "stainless_steel", "chrome_steel", "enamel",
                              "porcelain", "porcelain_enamel", "granite", "marble", "glass_ceramic"])

# Category defaults (4.4), keyed by category; CATEGORY_NAMES maps type, ancestor and category names to a row, and the
# nearest name of [type] + is_a + [category] wins (a sofa is_a chair but is a sofa; a nightstand is_a table but is a
# cabinet). Sources: bed frames and slatted bases leave 18-30 cm under the rails (under-bed boxes are sold 15-18 cm
# tall); sofa and armchair legs are 8-15 cm; a dining chair seat is at about 0.45 m and its back and frame hide about
# 40 % of what is behind; table tops are 2.5-4 cm thick and legs plus apron hide about 15 % of the band below; a desk's
# modesty panel and pedestal hide about 60 %; refrigerator and freezer makers ask 2.5-5 cm behind for the condenser
# (0.08 m with the coil standoff), washers, dryers and dishwashers 5-10 cm for hoses and vents; foliage covers about
# half of a plant's silhouette; wire cages and crates are mostly open (2-3 mm wire on 2.5 cm pitch, plus tray and
# frame: 0.7); sheer curtains pass about 60 % of the view; a pool's top is at ground level.
CATEGORY_DEFAULTS = {
    "bed": {"clear_below_m": 0.25},
    "sofa": {"clear_below_m": 0.10},
    "chair": {"clear_below_m": 0.42, "porosity": 0.6},
    "table": {"slab_m": 0.04, "below_porosity": 0.85},
    "desk": {"slab_m": 0.04, "below_porosity": 0.4},
    "cabinet": {"opening": "front"},
    "shelving": {"opening": "front"},
    "fridge": {"opening": "front", "interior_light": True, "behind_gap_m": 0.08},
    "chest_freezer": {"opening": "top", "interior_light": True, "behind_gap_m": 0.08},
    "appliance": {"opening": "front", "behind_gap_m": 0.08},
    "top_loader": {"opening": "top", "behind_gap_m": 0.08},
    "plant": {"porosity": 0.5},
    "cage": {"opening": "front", "porosity": 0.7},
    "container": {"opening": "top"},
    "sheer": {"porosity": 0.6},
    "sunken": {"height_m": 0.0},
}
CATEGORY_NAMES = {
    "bed": "bed",
    "sofa": "sofa", "armchair": "sofa", "recliner": "sofa", "loveseat": "sofa",
    "chair": "chair", "stool": "chair", "bench": "chair", "high_chair": "chair", "outdoor_chair": "chair",
    "table": "table", "desk": "desk",
    "nightstand": "cabinet", "dresser": "cabinet", "vanity": "cabinet", "counter": "cabinet", "tv_stand": "cabinet",
    "storage_furniture": "cabinet",
    "bookshelf": "shelving", "shelf_unit": "shelving", "closet_shelving": "shelving", "shoe_rack": "shelving",
    "firewood_rack": "shelving", "plate_tree": "shelving",
    "refrigerator": "fridge", "upright_freezer": "fridge", "freezer_drawer": "fridge",
    "chest_freezer": "chest_freezer",
    "appliance": "appliance", "cooking_appliance": "appliance", "washing_machine": "appliance",
    "clothes_dryer": "appliance",
    "top_load_washer": "top_loader",
    "plant": "plant",
    "bird_cage": "cage", "pet_crate": "cage", "small_animal_cage": "cage", "chicken_coop": "cage",
    "carry_container": "container", "drawer": "container", "toy_box": "container", "storage_chest": "container",
    "deck_box": "container", "compost_bin": "container", "aquarium": "container", "bathtub": "container",
    "sink": "container", "rain_barrel": "container",
    "sheer_curtains": "sheer",
    "pool": "sunken",
}

# Colours and floors a camera loses in dim light (reflectance under about 10 %, Munsell value 3 or less).
DARK_COLORS = frozenset(["black", "charcoal", "navy", "indigo", "ebony", "espresso", "graphite", "slate",
                         "dark_brown", "dark brown", "dark_grey", "dark grey", "dark_gray", "dark gray",
                         "dark_green", "dark green", "dark_blue", "dark blue"])
DARK_FLOOR_MATERIALS = frozenset(["slate", "dark_wood", "black_tile", "asphalt"])

# Agents as viewers and occluders (C7: the people model's eyes; a body's top is 0.12 m above its eyes; a lying body
# is 0.25 m thick with the eyes 0.2 m above the surface). Children: eye 0.6 + 0.07 x age m up to the adult value
# (CDC growth charts give about 1.15 m of eye height at 8); a seated eye scales with the standing one (1.15 / 1.55).
PERSON_EYE = {"standing": 1.55, "sitting": 1.15, "lying_above_m": 0.2, "child_base_m": 0.6, "child_per_year_m": 0.07,
              "top_above_eye_m": 0.12, "lying_top_m": 0.25}
CHILD_ROLES = ("child", "toddler", "infant")
ROLE_AGES = {"infant": 0.5, "toddler": 2.0, "child": 7.0, "teen": 15.0, "elderly": 78.0}
SEATED_POSTURES = ("sitting", "kneeling", "crouching")
LYING_POSTURES = ("lying", "sleeping", "fallen")
# Pet heights (withers/back; AKC breed standards: small dogs under 0.4 m, medium 0.4-0.55, large 0.6-0.7; cats 0.25-0.3)
PET_HEIGHT = {"dog": {"small": 0.35, "medium": 0.55, "large": 0.7}, "cat": 0.3, "other": 0.2}
AGENT_POROSITY = {"person": 0.3, "pet": 0.5, "robot": 0.4}
# Human viewers: about 200 degrees of awareness, 120 of attention, 1 arcminute acuity (20/20 vision: 3438 px/rad).
PERSON_VIEW = {"hfov_awareness_deg": 200.0, "hfov_attention_deg": 120.0, "vfov_deg": 120.0, "range_m": 15.0,
               "focal_px": 3438.0}
# Device cameras without explicit optics: a 78-degree 1080p webcam or doorbell lens, 16:9, 6 m useful range.
DEVICE_CAMERA = {"hfov_deg": 78.0, "aspect": 9.0 / 16.0, "res_w": 1920, "range_m": 6.0, "tilt_deg": 0.0}

# Character height of read-tier fields (4.5): reading needs >= 10 px per character. Label and date print is about
# 4 mm, body text 3 mm (10-12 pt), care labels and serial plates 2.5 mm, appliance displays and keypads 10 mm digits,
# spine titles 8 mm, signs and screen text 30 mm.
TEXT_M = {}
for _fields, _height in ((("label_text", "address", "recipient", "expiration", "expires", "best_by", "use_by",
                           "date_label", "warning"), 0.004),
                         (("text", "instructions", "directions", "dosage", "steps", "patient", "prescribed_to"), 0.003),
                         (("care_label", "serial", "sku", "barcode", "model"), 0.0025),
                         (("display", "keypad"), 0.010),
                         (("title", "name_label", "brand"), 0.008),
                         (("sign", "poster", "screen"), 0.030)):
    for _field in _fields:
        TEXT_M[_field] = _height
del _fields, _height, _field
# admin_inventory's label_facing words as faces (4.6) and the fields a "label" face gates
LABEL_FACING = {"top": "up", "up": "up", "bottom": "down", "down": "down", "front": 0.0, "out": 0.0,
                "back": 180.0, "in": 180.0, "wall": 180.0, "left": 270.0, "right": 90.0}
LABEL_GATED_FIELDS = ("label_text", "recipient", "address", "barcode", "order_id")

# Light (6.1): shading by place, robot beams (a ~100 lm flashlight reaches 0.2 at 3 m; a headlamp 0.2 at 1.5 m).
SHADE = {"under": 0.3, "inside": 0.6, "inside_lit": 1.0, "behind": 0.4}
BEAMS = {"flashlight": {"power": 1.8, "half_deg": 15.0}, "headlamp": {"power": 0.45, "half_deg": 30.0}}
BEAM_MIN_R = 0.2
# Glare (6.2)
SUN_FRAME_MARGIN_DEG = 10.0
SUN_SCRATCH_DEG = 20.0      # per unit of lens_scratch
SUN_CONE_DEG = 20.0
BACKLIGHT_LEVEL = 0.5
BACKLIGHT_RATIO = 0.6
SPECULAR_DEG = 10.0

Box = collections.namedtuple("Box", "rid x0 y0 x1 y1 z0 z1 yaw")
Viewer = collections.namedtuple("Viewer", "rid x y z yaw tilt hfov vfov range_m focal_px kind ignore")
Eye = collections.namedtuple("Eye", "rid x y z ignore")
Eye.__new__.__defaults__ = ((),)
Beam = collections.namedtuple("Beam", "x y z ux uy uz power half_deg")
# how a point relates to the geometry around it: kind, the ids that never occlude it, its support tested exactly as
# parts ((x0, y0, x1, y1, spans), ...) (a seat's back strip, the furniture under a thing it rests on), and for things
# in a container its _Container and its neighbours' boxes
# ((x0, y0, z0, x1, y1, z1, porosity), ...) for the opening rule
_Rel = collections.namedtuple("_Rel", "kind eid skip support container siblings")
_Rel.__new__.__defaults__ = (None, None, ())
_Container = collections.namedtuple("_Container", "cid opening x0 y0 x1 y1 z0 z1 front lat_w dep_d porosity open "
                                                  "shelves")


# ----- small helpers -------------------------------------------------------------------------------------------------
def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def wrap_deg(deg):
    """deg wrapped to (-180, 180]."""
    d = math.fmod(float(deg), 360.0)
    if d <= -180.0:
        d += 360.0
    elif d > 180.0:
        d -= 360.0
    return d


def grid_deg(compass_deg):
    """Grid-convention angle of a compass bearing (0 = north): (compass - 90) % 360."""
    return (float(compass_deg) - 90.0) % 360.0


def quarter(fraction):
    """A fraction rounded to the nearest quarter, half up, within [0, 1]."""
    return max(0.0, min(1.0, math.floor(float(fraction) * 4.0 + 0.5 + 1e-9) / 4.0))


def _r3(v):
    return round(v, 3)


class _LRU(object):
    """A small content-keyed cache (results never depend on whether it hits)."""

    def __init__(self, size):
        self.size, self.data = size, collections.OrderedDict()

    def get(self, key):
        value = self.data.get(key)
        if value is not None:
            self.data.move_to_end(key)
        return value

    def put(self, key, value):
        self.data[key] = value
        self.data.move_to_end(key)
        while len(self.data) > self.size:
            self.data.popitem(last=False)


# Test overrides of the sibling functions sight reads robot and load numbers from ("body.eye_height_m",
# "phys.support.held_envelope", "phys.support.resting_size"): a callable here replaces the sibling; None or no entry
# means the sibling itself.
_BRIDGES = {}
_SIBLINGS = {}


def _bridge(module, attr):
    """house_sim.kernel.<module>.<attr> (imported lazily: body and phys.support import nothing of sight), or the
    callable a test put into _BRIDGES under "<module>.<attr>"."""
    key = module + "." + attr
    override = _BRIDGES.get(key)
    if override is not None:
        return override
    found = _SIBLINGS.get(key)
    if found is None:
        found = getattr(importlib.import_module("." + module, __package__), attr)
        _SIBLINGS[key] = found
    return found


def _eye_of(eye):
    """(rid, x, y, z, ignore) of an Eye, a Viewer or a plain (rid, x, y, z[, ignore]) sequence."""
    if hasattr(eye, "rid"):
        return eye.rid, float(eye.x), float(eye.y), float(eye.z), tuple(eye.ignore or ())
    return eye[0], float(eye[1]), float(eye[2]), float(eye[3]), tuple(eye[4]) if len(eye) > 4 else ()


def _robot_viewer(ignore):
    return "robot" in ignore


# ----- body and physics numbers ------------------------------------------------------------------------------------------
def eye_height_m(state, posture=None):
    """Height of the robot's head camera above its floor (body.eye_height_m: the body profile's eye height for the
    posture, "lying" when fallen, plus the standing height of the aid it stands on). posture: the height for that
    posture on the floor instead of the current one."""
    if posture is not None:
        state = dict(state, robot=dict(state["robot"], posture=posture, fallen=False, standing_on=None))
    return round(float(_bridge("body", "eye_height_m")(state)), 3)


def carry_geometry(state):
    """The body profile's carry geometry {"hand_z_m", "hip_z_m", "front_m"} (TIER1_INTEGRATION 1.2): where held
    items ride. One-hand loads hang centred on the hand, two-hand loads (not level-carried trays) sit on the hips,
    front_m ahead of the body axis."""
    return _bridge("body", "profile")(state)["carry"]


def _held_envelope(state, cmap=None):
    """phys.support.held_envelope: the box of everything the robot carries, with blocks_view following the camera
    rule (5.6), or None with empty hands."""
    fn = _bridge("phys.support", "held_envelope")
    return fn(state) if cmap is None or _BRIDGES.get("phys.support.held_envelope") is not None else fn(state, cmap)


def _two_handed(state, eid):
    hands = state["robot"].get("hands") or {}
    return state["entities"][eid].get("held_hand") == "both" or list(hands.values()).count(eid) > 1


def _dims(state, eid, rec):
    """(w, d, h) of an entity as it rests, in its own frame (phys.support.resting_size: orientation applied, rot
    not)."""
    size = _bridge("phys.support", "resting_size")(state, eid)
    return float(size[0]), float(size[1]), float(size[2])


class _Snapshot(dict):
    """children_map() result: {parent id: [child ids]} plus a memo of placements and container layouts, valid for the
    state it was built from while that state does not change."""
    __slots__ = ("state_id", "memo")


def children_map(state):
    """{parent id: [child ids, sorted]} over every entity: pass it as `cmap` to share one scan (and the placements and
    container layouts it memoizes) across many sight calls on an unchanged state; build a new one after any change."""
    out = _Snapshot()
    out.state_id, out.memo = id(state), {"place": {}, "layout": {}, "parts": {}, "carried": {}}
    for eid in sorted(state["entities"]):
        loc = state["entities"][eid].get("location")
        if loc is not None:
            out.setdefault(loc, []).append(eid)
    return out


def _memo(state, cmap, kind):
    """The snapshot memo of one kind for this state, or None (no snapshot, or one built from another state)."""
    if isinstance(cmap, _Snapshot) and cmap.state_id == id(state):
        return cmap.memo[kind]
    return None


def _children(state, parent, cmap):
    if cmap is not None:
        return cmap.get(parent, ())
    return sorted(eid for eid, rec in state["entities"].items() if rec.get("location") == parent)


# ----- view properties and fronts --------------------------------------------------------------------------------------
_VIEW_MEMO = {}


def _category_row(rec):
    for name in [rec.get("type")] + list(rec.get("is_a") or []) + [rec.get("category")]:
        key = CATEGORY_NAMES.get(name)
        if key is not None:
            return CATEGORY_DEFAULTS[key]
    return None


def _merge_view(base, extra):
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def view_props(state, eid):
    """View properties of an entity (4.3): VIEW_DEFAULTS (glass, clear plastic and acrylic: porosity 0.9; glass,
    stainless steel, enamel, polished stone: glossy), then its CATEGORY_DEFAULTS row, the type's "view" block merged
    along is_a, and the record's own "view" dict. An unset opening becomes "top" for portable things and "front" for
    the rest. Memoized by content; do not mutate the result."""
    rec = state["entities"].get(eid)
    if rec is None:
        return VIEW_DEFAULTS
    reg = default_registry()
    own = rec.get("view")
    key = (reg, rec.get("type"), tuple(rec.get("is_a") or ()), rec.get("category"), rec.get("material"),
           bool(rec.get("transparent")), bool(rec.get("portable")),
           json.dumps(own, sort_keys=True, default=str) if own else None)
    found = _VIEW_MEMO.get(key)
    if found is None:
        found = dict(VIEW_DEFAULTS)
        material = str(rec.get("material") or "").lower()
        if rec.get("transparent") or material in TRANSPARENT_MATERIALS:
            found["porosity"] = 0.9
        found["glossy"] = material in GLOSSY_MATERIALS
        row = _category_row(rec)
        if row:
            found.update(row)
        if rec.get("type") and reg.known(rec["type"]):
            found = _merge_view(found, reg.view_props(rec["type"]))
        if isinstance(own, dict):
            found = _merge_view(found, own)
        if found["opening"] is None:
            found["opening"] = "top" if rec.get("portable") else "front"
        if len(_VIEW_MEMO) > 4096:
            _VIEW_MEMO.clear()
        _VIEW_MEMO[key] = found
    return found


def _yaw(rec):
    return _num(rec.get("yaw_deg"), 0.0) if "yaw_deg" in rec else _num(rec.get("rot", 0), 0.0)


def _robot_facing(state):
    return _num(state["robot"].get("facing_deg"), 90.0)


def front_deg(state, eid, _depth=0):
    """Grid direction an entity's front faces (3): an explicit view.front_deg; else, in or on a parent, the parent's
    front plus its own yaw; else, standing against the room's perimeter, the inward normal of that wall (at a corner,
    the wall along the longer footprint side); else toward the room centre (90 at the centre). People and pets face
    their facing_deg; a held item faces the robot."""
    for group in ("people", "pets"):
        agent = state.get(group, {}).get(eid)
        if agent is not None:
            return round(_num(agent.get("facing_deg"), 90.0) % 360.0, 2)
    rec = state["entities"].get(eid)
    if rec is None or _depth > 20:
        return 90.0
    explicit = view_props(state, eid).get("front_deg")
    if explicit is not None:
        return round(_num(explicit) % 360.0, 2)
    loc = rec.get("location")
    if loc == "robot":
        return round((_robot_facing(state) + 180.0) % 360.0, 2)
    for group in ("people", "pets"):
        if loc in state.get(group, {}):
            return round((front_deg(state, loc) + _yaw(rec)) % 360.0, 2)
    if loc in state["entities"]:
        return round((front_deg(state, loc, _depth + 1) + _yaw(rec)) % 360.0, 2)
    placed = _place(state, eid)
    if placed is None:
        return 90.0
    box = placed[0]
    room = state["rooms"][box.rid]
    cols, rows = geometry.grid_size(room)
    c0, r0 = int(math.floor(box.x0 / CELL + 1e-6)), int(math.floor(box.y0 / CELL + 1e-6))
    c1, r1 = int(math.ceil(box.x1 / CELL - 1e-6)) - 1, int(math.ceil(box.y1 / CELL - 1e-6)) - 1
    walls_ns = [deg for touch, deg in ((r0 <= 0, 90.0), (r1 >= rows - 1, 270.0)) if touch]
    walls_we = [deg for touch, deg in ((c0 <= 0, 0.0), (c1 >= cols - 1, 180.0)) if touch]
    if walls_ns or walls_we:
        along_x = (box.x1 - box.x0) >= (box.y1 - box.y0)
        pick = (walls_ns or walls_we) if along_x else (walls_we or walls_ns)
        return pick[0]
    w_m, d_m = room.get("size_m", [cols * CELL, rows * CELL])[:2]
    dx, dy = _num(w_m) / 2.0 - (box.x0 + box.x1) / 2.0, _num(d_m) / 2.0 - (box.y0 + box.y1) / 2.0
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return 90.0
    return round(math.degrees(math.atan2(dy, dx)) % 360.0, 2)


def _axis_front(deg):
    """A front snapped to the nearest grid axis (containers and faces are axis-aligned boxes)."""
    return (int(math.floor((float(deg) % 360.0) / 90.0 + 0.5)) % 4) * 90


# ----- placement: boxes, relations, containers ---------------------------------------------------------------------------
def entity_box(state, eid, cmap=None):
    """Box(rid, x0, y0, x1, y1, z0, z1, yaw) of an entity, person or pet in its room frame (5.3), or None when it is
    not placed. Room-located: its footprint (xy_m or pos; rot 90/270 swaps the sides) from geometry.height_m up; on a
    surface: the parent's corner plus spos (axes swapped for a parent at rot 90/270, as people.aim_at does), centred
    when spos is absent; in a container: its inner_spos or the deterministic row packing (5.4e); under or behind a
    support: centred under it / in its back gap; hung: below the hook; part/attached: centred in the parent; held: in
    front of the robot at carry height; carried by a person or pet: at their cell. cmap: children_map(state)."""
    placed = _place(state, eid, cmap)
    return placed[0] if placed else None


def _place(state, eid, cmap=None, _depth=0):
    """(Box, _Rel) or None (memoized in a children_map snapshot)."""
    memo = _memo(state, cmap, "place")
    if memo is None:
        return _place_new(state, eid, cmap, _depth)
    if eid not in memo:
        memo[eid] = _place_new(state, eid, cmap, _depth)
    return memo[eid]


CEILING_MOUNT_M = 2.3       # a fixture mounted this high or higher hangs from the ceiling (lights, detectors)


def _wall_mounted(rec):
    """True for a room-located fixture fastened to a wall (switches, outlets, vents, hooks, blinds, curtains, valves,
    thermostats, windows, wall cabinets, sinks), not hanging from the ceiling."""
    if not (rec.get("mounted") or rec.get("wall_mounted")):
        return False
    words = " ".join(str(v) for v in [rec.get("type"), rec.get("category")] + list(rec.get("is_a") or []))
    if "ceiling" in words or "detector" in words:
        return False
    return _num(rec.get("mount_height_m"), 0.0) < CEILING_MOUNT_M


def _wall_snap(room, rec, x0, y0, fw, fd):
    """(x0, y0) of a wall-mounted fixture on the room's south or east wall moved flush against that wall. The grid
    keeps a fixture in its perimeter cell(s) and a box starts at the cell's corner, which is the wall on the north and
    west walls but up to a cell short of it on the south and east ones (a switch floating 0.47 m out in the room).
    At a corner the wall along the longer footprint side wins (both for a square one: it sits in the corner)."""
    cols, rows = geometry.grid_size(room)
    cw, ch = geometry.footprint_size(rec)
    px, py = int(rec["pos"][0]), int(rec["pos"][1])
    east, south = px + cw >= cols, py + ch >= rows
    west, north = px <= 0, py <= 0
    if east and not west and (fd >= fw or not (north or south)):
        x0 = max(x0, cols * CELL - fw)
    if south and not north and (fw >= fd or not (east or west)):
        y0 = max(y0, rows * CELL - fd)
    return x0, y0


def _place_new(state, eid, cmap, _depth):
    if eid in state.get("people", {}) or eid in state.get("pets", {}):
        return _agent_place(state, eid)
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None or _depth > 20:
        return None
    loc = rec.get("location")
    w, d, h = _dims(state, eid, rec)
    yaw = _yaw(rec)
    if "connects" in rec:
        return _door_place(state, eid, rec, w, d, h)
    if loc in state["rooms"]:
        if isinstance(rec.get("xy_m"), list) and len(rec["xy_m"]) == 2:
            x0, y0 = _num(rec["xy_m"][0]), _num(rec["xy_m"][1])
        elif rec.get("pos") is not None:
            x0, y0 = _num(rec["pos"][0]) * CELL, _num(rec["pos"][1]) * CELL
        else:
            return None
        fw, fd = (d, w) if int(rec.get("rot", 0) or 0) % 180 == 90 else (w, d)
        if rec.get("pos") is not None and _wall_mounted(rec):
            x0, y0 = _wall_snap(state["rooms"][loc], rec, x0, y0, fw, fd)
        z0 = geometry.height_m(state, eid)
        box = Box(loc, _r3(x0), _r3(y0), _r3(x0 + fw), _r3(y0 + fd), _r3(z0), _r3(z0 + h), yaw)
        return box, _Rel("room", eid, frozenset([eid]))
    if loc == "robot":
        robot = state["robot"]
        if not robot.get("pos") or robot.get("room") not in state["rooms"]:
            return None
        carry = carry_geometry(state)
        env = _held_envelope(state, cmap) or {}
        reach = _num(env.get("front_m"), carry["front_m"]) - _num(env.get("depth_m"), 0.0) / 2.0
        rad = math.radians(_robot_facing(state))
        cx = (robot["pos"][0] + 0.5) * CELL + math.cos(rad) * reach
        cy = (robot["pos"][1] + 0.5) * CELL + math.sin(rad) * reach
        tray = bool(rec.get("carry_level"))
        z0 = carry["hip_z_m"] if _two_handed(state, eid) and not tray else carry["hand_z_m"] - h / 2.0
        box = _centred_box(robot["room"], cx, cy, max(w, d), min(w, d), z0, h, yaw)
        return box, _Rel("held", eid, frozenset([eid, "robot"]))
    for group in ("people", "pets"):
        agent = state.get(group, {}).get(loc)
        if agent is not None:
            if not agent.get("pos") or agent.get("location") not in state["rooms"]:
                return None
            cx, cy = (agent["pos"][0] + 0.5) * CELL, (agent["pos"][1] + 0.5) * CELL
            z0 = geometry.height_m(state, eid)
            box = _centred_box(agent["location"], cx, cy, w, d, z0, h, yaw)
            return box, _Rel("carried", eid, frozenset([eid, loc]))
    parent = entities.get(loc)
    if parent is None:
        return None
    placed = _place(state, loc, cmap, _depth + 1)
    if placed is None:
        return None
    pbox, prel = placed
    relation = rec.get("relation", "on")
    skip = frozenset([eid, loc])
    chain = ()
    if prel.kind == "on" and relation in ("on", "under", "behind"):
        # resting on (or under) something that itself rests on furniture -- a cushion on a sofa, a tray on a table --
        # the furniture below is tested exactly (its seat, back or top, as the parent's support test) instead of
        # cell by cell: a sofa's back strip hides the cushion's things from behind, never from the front (C9)
        skip = skip | prel.skip
        if prel.support is not None:
            chain = prel.support
    if prel.kind in ("held", "carried"):
        # things on or in a carried load travel with it: always in the carrier's hands
        cx, cy = (pbox.x0 + pbox.x1) / 2.0, (pbox.y0 + pbox.y1) / 2.0
        z0 = pbox.z1 if relation == "on" else pbox.z0
        return _centred_box(pbox.rid, cx, cy, w, d, z0, h, yaw), prel._replace(eid=eid, skip=prel.skip | skip)
    if relation == "in":
        info = _container_info(state, loc, pbox)
        boxes = _inner_layout(state, info, cmap)
        x0, y0, z0, x1, y1, z1, _ = boxes[eid]
        siblings = tuple(b for k, b in sorted(boxes.items()) if k != eid)
        return Box(pbox.rid, x0, y0, x1, y1, z0, z1, yaw), _Rel("in", eid, skip, None, info, siblings)
    props = view_props(state, loc)
    memo = _memo(state, cmap, "parts")
    parts = memo.get(loc) if memo is not None else None
    if parts is None:
        parts = _body_parts(state, loc, parent, props, pbox)
        if memo is not None:
            memo[loc] = parts
    support = tuple(parts) + chain
    fw, fd = (d, w) if int(rec.get("rot", 0) or 0) % 180 == 90 else (w, d)
    if relation == "under":
        cx, cy = _surface_point(rec, parent, pbox, fw, fd)
        box = _centred_box(pbox.rid, cx, cy, fw, fd, geometry.height_m(state, eid), h, yaw)
        return box, _Rel("under", eid, skip, support, None)
    if relation == "behind":
        # a support with something behind it stands at least that thing's depth off the wall
        back_y = _axis_front(front_deg(state, loc)) in (90, 270)
        gap = max(_num(view_props(state, loc)["behind_gap_m"], 0.0), fd if back_y else fw)
        x0, y0, x1, y1, gx, gy = _behind_gap(state, loc, pbox, gap)
        box = _centred_box(pbox.rid, gx, gy, fw, fd, geometry.height_m(state, eid), h, yaw)
        return box, _Rel("behind", eid, skip, ((x0, y0, x1, y1, _spans(props, pbox)),) + chain, None)
    if relation == "hung":
        cx, cy = (pbox.x0 + pbox.x1) / 2.0, (pbox.y0 + pbox.y1) / 2.0
        hook_z = geometry.height_m(state, eid)
        box = _centred_box(pbox.rid, cx, cy, fw, fd, max(0.0, hook_z - h), min(h, hook_z), yaw)
        return box, _Rel("hung", eid, skip, None, None)
    if relation in ("part", "attached"):
        cx, cy = (pbox.x0 + pbox.x1) / 2.0, (pbox.y0 + pbox.y1) / 2.0
        z0 = max(pbox.z0, (pbox.z0 + pbox.z1) / 2.0 - h / 2.0)
        box = _centred_box(pbox.rid, cx, cy, fw, fd, z0, h, yaw)
        return box, _Rel("part", eid, skip, None, None)
    if int(parent.get("rot", 0) or 0) % 180 == 90:
        fw, fd = fd, fw
    cx, cy = _surface_point(rec, parent, pbox, fw, fd)
    box = _centred_box(pbox.rid, cx, cy, fw, fd, geometry.height_m(state, eid), h, yaw)
    return box, _Rel("on", eid, skip, support, None)


def _centred_box(rid, cx, cy, w, d, z0, h, yaw):
    return Box(rid, _r3(cx - w / 2.0), _r3(cy - d / 2.0), _r3(cx + w / 2.0), _r3(cy + d / 2.0), _r3(z0), _r3(z0 + h),
               yaw)


def _surface_point(rec, parent, pbox, fw, fd):
    """Room-frame centre of something on (or under) a parent: the parent's corner plus spos and half the item, with
    the surface axes swapped for a parent at rot 90/270 (the convention of people/common.entity_xy); the parent's
    centre when there is no spos."""
    spos = rec.get("spos")
    if isinstance(spos, (list, tuple)) and len(spos) >= 2:
        sx, sy = _num(spos[0]), _num(spos[1])
        if int(parent.get("rot", 0) or 0) % 180 == 90:
            sx, sy = sy, sx
        return pbox.x0 + sx + fw / 2.0, pbox.y0 + sy + fd / 2.0
    return (pbox.x0 + pbox.x1) / 2.0, (pbox.y0 + pbox.y1) / 2.0


def _door_place(state, eid, rec, w, d, h):
    """Doors, gates and windows between rooms: a thin slab on the wall side of their doorway cell in the robot's room
    (else their own room), floor to top."""
    rid = state["robot"].get("room")
    if rid not in (rec.get("connects") or ()):
        rid = rec.get("location") if rec.get("location") in state["rooms"] else (rec.get("connects") or [None])[0]
    if rid not in state["rooms"]:
        return None
    cell = None
    for _, conn in sorted(state.get("connections", {}).items()):
        if conn.get("door") == eid and rid in conn.get("cells", {}):
            cell = conn["cells"][rid]
            break
    if cell is None:
        cell = rec.get("pos") if rec.get("location") == rid else None
    if cell is None:
        return None
    room = state["rooms"][rid]
    inward, _ = geometry.wall_inward(room, cell)
    x0, y0 = int(cell[0]) * CELL, int(cell[1]) * CELL
    x1, y1 = x0 + CELL, y0 + CELL
    t = max(0.02, min(w, d, CELL))
    if inward[0] > 0:
        x1 = x0 + t
    elif inward[0] < 0:
        x0 = x1 - t
    elif inward[1] > 0:
        y1 = y0 + t
    else:
        y0 = y1 - t
    z0 = _num(rec.get("mount_height_m"), 0.0)
    return Box(rid, _r3(x0), _r3(y0), _r3(x1), _r3(y1), _r3(z0), _r3(z0 + h), _yaw(rec)), \
        _Rel("room", eid, frozenset([eid]), None, None)


def _agent_place(state, aid):
    agent = state.get("people", {}).get(aid) or state.get("pets", {}).get(aid)
    if not agent.get("pos") or agent.get("location") not in state["rooms"]:
        return None
    x0, y0 = int(agent["pos"][0]) * CELL, int(agent["pos"][1]) * CELL
    base = _agent_base(state, aid)
    box = Box(agent["location"], _r3(x0), _r3(y0), _r3(x0 + CELL), _r3(y0 + CELL), _r3(base),
              _r3(agent_top(state, aid)), _num(agent.get("facing_deg"), 90.0))
    return box, _Rel("agent", aid, frozenset([aid]), None, None)


def _behind_gap(state, sid, sbox, gap):
    """(effective support rect, gap centre) for things behind a support: the gap lies behind its back face (opposite
    its front, snapped to an axis); a support standing closer than `gap` to the wall gives up the difference of its
    depth to it (the grid puts wall-side furniture flush with the wall)."""
    back = (_axis_front(front_deg(state, sid)) + 180) % 360
    x0, y0, x1, y1 = sbox.x0, sbox.y0, sbox.x1, sbox.y1
    room = state["rooms"][sbox.rid]
    cols, rows = geometry.grid_size(room)
    width, depth = cols * CELL, rows * CELL
    if back == 0:
        x1 = min(x1, width - gap)
        gx, gy = x1 + gap / 2.0, (y0 + y1) / 2.0
    elif back == 180:
        x0 = max(x0, gap)
        gx, gy = x0 - gap / 2.0, (y0 + y1) / 2.0
    elif back == 90:
        y1 = min(y1, depth - gap)
        gx, gy = (x0 + x1) / 2.0, y1 + gap / 2.0
    else:
        y0 = max(y0, gap)
        gx, gy = (x0 + x1) / 2.0, y0 - gap / 2.0
    return x0, y0, x1, y1, gx, gy


def _container_info(state, cid, cbox):
    rec = state["entities"][cid]
    props = view_props(state, cid)
    front = _axis_front(front_deg(state, cid))
    w, d = cbox.x1 - cbox.x0, cbox.y1 - cbox.y0
    lat_w, dep_d = (w, d) if front in (90, 270) else (d, w)
    base = geometry.height_m(state, cid)
    shelves = tuple(sorted(base + _num(z) for z in (rec.get("shelf_heights") or ())))
    opened = bool(rec.get("open")) or not rec.get("openable") or props["porosity"] >= 0.5
    return _Container(cid, props["opening"], cbox.x0, cbox.y0, cbox.x1, cbox.y1, cbox.z0, cbox.z1, front, lat_w,
                      dep_d, props["porosity"], opened, shelves)


INTERIOR_INSET_M = 0.03     # interior test points sit this far inside the walls and above the inner floor or board


def interior_points(state, info):
    """The points that show a container's interior (TIER1_CAMERA 4.7 "interior framed"), in groups that must each be
    seen: a top opening's inner floor on a 3 x 3 grid (one group); a front opening's back wall just above each place
    things stand (every shelf board, else the inner floor), at three places across (one group per band). Seeing the
    opening's rim says nothing about what lies inside; seeing these does."""
    rec = state["entities"][info.cid]
    inset = INTERIOR_INSET_M
    floor = info.z0 + max(0.0, _num(rec.get("inner_floor_m"), 0.0)) + inset
    if info.opening == "top":
        grid = []
        for fx in (1.0 / 6.0, 0.5, 5.0 / 6.0):
            for fy in (1.0 / 6.0, 0.5, 5.0 / 6.0):
                grid.append((info.x0 + (info.x1 - info.x0) * fx, info.y0 + (info.y1 - info.y0) * fy, floor))
        return [grid]
    boards = [s + inset for s in info.shelves if s < info.z1 - inset]
    out = []
    for z in boards or [floor]:
        band = []
        for fu in (1.0 / 6.0, 0.5, 5.0 / 6.0):
            x, y = _container_point(info, info.lat_w * fu, max(inset, info.dep_d - inset))
            band.append((x, y, z))
        out.append(band)
    return out


def interior_transmission(state, geom, vs, eye, info, p):
    """Fraction of the view from an eye that reaches a point inside a container: through its opening (or its porous
    walls) and past every occluder of the room, the container's own body excepted (its walls are the opening rule)."""
    rid, ex, ey, ez, ignore = _eye_of(eye)
    factor = _opening_factor(info, ex, ey, ez, p)
    if factor <= 0.0:
        return 0.0
    skip = frozenset(ignore) | {info.cid}
    return factor * _transmission(geom, vs, ex, ey, ez, p[0], p[1], p[2], skip, None, _occluders(state, eye, skip))


def _container_point(info, u, v):
    """Room point at lateral offset u (from the left edge as seen from the front) and depth v (from the opening)."""
    if info.front == 90:
        return info.x0 + u, info.y1 - v
    if info.front == 270:
        return info.x1 - u, info.y0 + v
    if info.front == 0:
        return info.x1 - v, info.y1 - u
    return info.x0 + v, info.y0 + u


_LAYOUT_CACHE = _LRU(256)


LAYOUT_FIELDS = ("shelf", "size_m", "rot", "orientation", "inner_spos", "z_m", "type", "material", "transparent",
                 "view")


def _inner_layout(state, info, cmap):
    """{eid: (x0, y0, z0, x1, y1, z1, porosity)} room boxes of the things in a container (5.4e). An explicit
    inner_spos [u, v] is an item's front-left corner (u across from the left edge as seen from the front, v back from
    the opening); the others, per shelf, are ordered by blake2b(eid) and packed left to right in rows from the opening
    back (a new row when the next item would pass the inner width; rows past the back stay at the back). Heights are
    geometry.height_m's (shelf boards or the inner floor). Cached by the container's box and its contents' fields."""
    memo = _memo(state, cmap, "layout")
    if memo is not None and info in memo:
        return memo[info]
    entities = state["entities"]
    container = entities[info.cid]
    kids = [k for k in _children(state, info.cid, cmap) if entities[k].get("relation") == "in"]
    key = (info, repr(container.get("inner_floor_m")), repr(container.get("shelf_heights")),
           tuple((k,) + tuple(repr(entities[k].get(f)) for f in LAYOUT_FIELDS) for k in kids))
    found = _LAYOUT_CACHE.get(key)
    if found is None:
        lateral_x = info.front in (90, 270)
        centres, groups, sizes = {}, {}, {}
        for k in kids:
            rec = entities[k]
            w, d, h = _dims(state, k, rec)
            fw, fd = (d, w) if int(rec.get("rot", 0) or 0) % 180 == 90 else (w, d)
            iw, idp = (fw, fd) if lateral_x else (fd, fw)
            sizes[k] = (fw, fd, h)
            spos = rec.get("inner_spos")
            if isinstance(spos, (list, tuple)) and len(spos) >= 2:
                centres[k] = (_num(spos[0]) + iw / 2.0, _num(spos[1]) + idp / 2.0)
            else:
                digest = hashlib.blake2b(k.encode("utf-8"), digest_size=8).hexdigest()
                groups.setdefault(repr(rec.get("shelf")), []).append((digest, k, iw, idp))
        for _, items in sorted(groups.items()):
            u = v = row_d = 0.0
            for _, k, iw, idp in sorted(items):
                if u > 0.0 and u + iw > info.lat_w + 1e-9:
                    u, v, row_d = 0.0, v + row_d, 0.0
                centres[k] = (min(u + iw / 2.0, max(iw / 2.0, info.lat_w - iw / 2.0)),
                              min(v + idp / 2.0, max(idp / 2.0, info.dep_d - idp / 2.0)))
                u += iw
                row_d = max(row_d, idp)
        found = {}
        for k, (u, v) in centres.items():
            fw, fd, h = sizes[k]
            cx, cy = _container_point(info, u, v)
            z0 = geometry.height_m(state, k)
            found[k] = (_r3(cx - fw / 2.0), _r3(cy - fd / 2.0), _r3(z0), _r3(cx + fw / 2.0), _r3(cy + fd / 2.0),
                        _r3(z0 + h), view_props(state, k)["porosity"])
        _LAYOUT_CACHE.put(key, found)
    if memo is not None:
        memo[info] = found
    return found


def _container_uv(info, x0, y0, x1, y1):
    """(u0, u1, v0, v1) of a room-frame rectangle in a container's frame (u across from the left edge as seen from
    the front, v back from the opening): the inverse of _container_point."""
    if info.front == 90:
        return x0 - info.x0, x1 - info.x0, info.y1 - y1, info.y1 - y0
    if info.front == 270:
        return info.x1 - x1, info.x1 - x0, y0 - info.y0, y1 - info.y0
    if info.front == 0:
        return info.y1 - y1, info.y1 - y0, info.x1 - x1, info.x1 - x0
    return y0 - info.y0, y1 - info.y0, x0 - info.x0, x1 - info.x0


def row_spos(state, cid, eid, row):
    """inner_spos [u, v] that puts eid (already "in" container cid) in its shelf's front row (v = 0, at the opening)
    or back row (against the back wall): the leftmost lateral place in that row where it overlaps none of the other
    things on the same shelf as they are laid out now (explicit inner_spos and the automatic packing alike); None when
    the container is not placed or the row is full. Setup's spawn "row" (CAM-ACTIONS) writes the result."""
    entities = state["entities"]
    rec = entities.get(eid)
    placed = _place(state, cid)
    if rec is None or placed is None or row not in ("front", "back"):
        return None
    info = _container_info(state, cid, placed[0])
    w, d, _ = _dims(state, eid, rec)
    fw, fd = (d, w) if int(rec.get("rot", 0) or 0) % 180 == 90 else (w, d)
    iw, idp = (fw, fd) if info.front in (90, 270) else (fd, fw)
    if iw > info.lat_w + 1e-9 or idp > info.dep_d + 1e-9:
        return None
    v0 = 0.0 if row == "front" else max(0.0, info.dep_d - idp)
    # lay the shelf out with eid out of the automatic packing (an explicit place never moves the others)
    probe = dict(state, entities=dict(entities))
    probe["entities"][eid] = dict(rec, inner_spos=[0.0, v0])
    boxes = _inner_layout(probe, info, None)
    taken = []
    for k, box in sorted(boxes.items()):
        if k == eid or repr(entities[k].get("shelf")) != repr(rec.get("shelf")):
            continue
        u0, u1, b0, b1 = _container_uv(info, box[0], box[1], box[3], box[4])
        if b0 < v0 + idp - 1e-9 and b1 > v0 + 1e-9:
            taken.append((u0, u1))
    for u in sorted(set([0.0] + [max(0.0, u1) for _, u1 in taken])):
        if u + iw > info.lat_w + 1e-9:
            continue
        if not any(a < u + iw - 1e-9 and b > u + 1e-9 for a, b in taken):
            return [_r3(u), _r3(v0)]
    return None


def _seat_back(state, eid, rec, props, box):
    """(x0, y0, x1, y1, seat_z) of the back of a seat, or None: a solid entity that holds nothing inside and whose
    surface (surface_height_m) lies at least SEAT_MIN_DROP_M under its top -- a sofa, an armchair -- is solid only up
    to its seat, and above the seat only along a SEAT_BACK_M strip on its back side (opposite its front). Porous seats
    (open-frame chairs) keep their attenuating box."""
    seat = rec.get("surface_height_m")
    if seat is None or _holds_inside(rec) or _num(props.get("porosity"), 0.0) >= 0.5:
        return None
    seat_z = box.z0 + _num(seat)
    if seat_z <= box.z0 + EPS or box.z1 - seat_z < SEAT_MIN_DROP_M - EPS:
        return None
    back = (_axis_front(front_deg(state, eid)) + 180) % 360
    x0, y0, x1, y1 = box.x0, box.y0, box.x1, box.y1
    depth = min(SEAT_BACK_M, 0.5 * ((x1 - x0) if back in (0, 180) else (y1 - y0)))
    if back == 0:
        x0 = x1 - depth
    elif back == 180:
        x1 = x0 + depth
    elif back == 90:
        y0 = y1 - depth
    else:
        y1 = y0 + depth
    return _r3(x0), _r3(y0), _r3(x1), _r3(y1), _r3(seat_z)


def _body_parts(state, eid, rec, props, box):
    """[(x0, y0, x1, y1, spans)] of an entity's occluding body: its box with its spans; a seat with a back (_seat_back)
    is its box up to the seat plus the back strip above it."""
    seat = _seat_back(state, eid, rec, props, box)
    if seat is None:
        return [(box.x0, box.y0, box.x1, box.y1, _spans(props, box))]
    x0, y0, x1, y1, seat_z = seat
    porosity = _num(props.get("porosity"), 0.0)
    back = ((seat_z, round(box.z1, 3), porosity),) if porosity < 0.999 and box.z1 - seat_z > 1e-6 else ()
    return [(box.x0, box.y0, box.x1, box.y1, _spans(props, box._replace(z1=seat_z))), (x0, y0, x1, y1, back)]


def _spans(props, box):
    """Occluder spans ((z_lo, z_hi, porosity), ...) of an entity's box with its view properties: a top slab over a
    porous band (tables), or the body above its clearance over a porous band (legs); fully clear parts are dropped."""
    z0, z1 = box.z0, box.z1
    if props.get("height_m") is not None:
        z1 = z0 + _num(props["height_m"])
    if z1 - z0 < 1e-6:
        return ()
    porosity = _num(props.get("porosity"), 0.0)
    below = _num(props.get("below_porosity"), 1.0)
    slab = props.get("slab_m")
    if slab is not None:
        cut = max(z0, z1 - _num(slab))
    else:
        cut = min(z1, z0 + _num(props.get("clear_below_m"), 0.0))
    out = []
    if porosity < 0.999 and z1 - cut > 1e-6:
        out.append((round(cut, 3), round(z1, 3), porosity))
    if below < 0.999 and cut - z0 > 1e-6:
        out.append((round(z0, 3), round(cut, 3), below))
    return tuple(out)


# ----- agents ------------------------------------------------------------------------------------------------------------
def _is_child(person):
    role = person.get("role", "adult")
    if role in CHILD_ROLES:
        return True
    return person.get("age") is not None and _num(person["age"], 99.0) < 13 and role not in ("adult", "elderly")


def _person_age(person):
    if person.get("age") is not None:
        return _num(person["age"], 35.0)
    return ROLE_AGES.get(person.get("role", "adult"), 35.0)


def _agent_base(state, aid):
    """Height of the surface a lying person lies on (their bed's top, else the floor); 0 for everyone else."""
    agent = state.get("people", {}).get(aid)
    if agent is None or agent.get("posture") not in LYING_POSTURES:
        return 0.0
    bed = agent.get("in_bed") or agent.get("bed")
    if isinstance(bed, str) and bed in state["entities"] and agent.get("posture") != "fallen":
        rec = state["entities"][bed]
        return geometry.height_m(state, bed) + _num(rec.get("surface_height_m"), _dims(state, bed, rec)[2])
    return 0.0


def person_eye(state, pid):
    """(x, y, z) of a person's eyes (C7): 1.55 m standing, 1.15 m seated (kneeling and crouching count as seated),
    0.2 m above the surface lying down; a child's standing eye is 0.6 + 0.07 x age m (at most the adult's) and seats
    scale with it."""
    person = state["people"][pid]
    x, y = (person["pos"][0] + 0.5) * CELL, (person["pos"][1] + 0.5) * CELL
    posture = person.get("posture", "standing")
    if posture in LYING_POSTURES:
        return x, y, round(_agent_base(state, pid) + PERSON_EYE["lying_above_m"], 3)
    standing = PERSON_EYE["standing"]
    if _is_child(person):
        standing = min(standing, PERSON_EYE["child_base_m"] + PERSON_EYE["child_per_year_m"] * _person_age(person))
    if posture in SEATED_POSTURES:
        return x, y, round(standing * PERSON_EYE["sitting"] / PERSON_EYE["standing"], 3)
    return x, y, round(standing, 3)


def agent_top(state, aid):
    """Height of the top of a person (eyes + 0.12 m; lying: surface + 0.25 m), a pet (by species and size, or its
    view.height_m / height_m) or the robot (its height, scaled with its eye height by posture)."""
    if aid == "robot":
        robot = state["robot"]
        standing = eye_height_m(state, "standing")
        return round(_num(robot.get("height_m"), 1.6) * eye_height_m(state) / max(standing, 1e-6), 3)
    person = state.get("people", {}).get(aid)
    if person is not None:
        if person.get("posture") in LYING_POSTURES:
            return round(_agent_base(state, aid) + PERSON_EYE["lying_top_m"], 3)
        return round(person_eye(state, aid)[2] + PERSON_EYE["top_above_eye_m"], 3)
    pet = state.get("pets", {}).get(aid) or {}
    override = (pet.get("view") or {}).get("height_m", pet.get("height_m"))
    if override is not None:
        return _num(override, PET_HEIGHT["other"])
    species = pet.get("species", "dog")
    height = PET_HEIGHT.get(species, PET_HEIGHT["other"])
    if isinstance(height, dict):
        height = height.get(pet.get("size") or "medium", height["medium"])
    return height


def _occluders(state, eye, skip, cmap=None):
    """_agents plus, for a frame of the robot's own head camera taken where the robot stands (a Viewer of kind "head"
    that ignores the robot, at its current head eye), the load it carries in front of it (carried_occluder, 5.6;
    TIER1_INTEGRATION 1.6): an entry (None, view, ...) that _transmission tests by bearing and slope from the eye, so
    the floor the load hides is hidden in those frames. Bare Eyes (the any-direction line of sight of can_see: the
    robot can turn its body and look past the load) and wrist, motion and other viewers are unaffected."""
    out = _agents(state, _eye_of(eye)[0], skip)
    view = _carried_view(state, eye, cmap)
    if view is not None:
        out.append((None, view, None, None, None, None, "carried"))
    return out


def carried_view(state, cmap=None):
    """{"occ", "eye_z", "yaw", "items"} of the robot's carried load as its head camera sees it from where it stands
    (carried_occluder; yaw: the body's facing, which the load sits in front of; items: the load's own ids, never
    hidden by it), or None when it carries nothing that blocks the view."""
    robot = state.get("robot") or {}
    if not any((robot.get("hands") or {}).values()):
        return None
    memo = _memo(state, cmap, "carried")
    if memo is not None and "view" in memo:
        return memo["view"]
    occ = carried_occluder(state)
    found = None if occ is None else {"occ": occ, "eye_z": eye_height_m(state), "items": frozenset(occ["items"]),
                                      "yaw": _num(robot.get("facing_deg"), 0.0)}
    if memo is not None:
        memo["view"] = found
    return found


def _carried_view(state, eye, cmap=None):
    rid, ex, ey, ez, ignore = _eye_of(eye)
    robot = state.get("robot") or {}
    if getattr(eye, "kind", None) != "head" or not _robot_viewer(ignore) or robot.get("room") != rid:
        return None
    pos = robot.get("pos")
    if not pos or abs(ex - (int(pos[0]) + 0.5) * CELL) > 1e-3 or abs(ey - (int(pos[1]) + 0.5) * CELL) > 1e-3:
        return None
    found = carried_view(state, cmap)
    if found is None or abs(ez - found["eye_z"]) > 0.011:
        return None
    return found


def carried_blocks(view, ex, ey, ez, px, py, pz):
    """True when the robot's carried load (carried_view) hides point p from its head eye at (ex, ey, ez):
    carried_hides at the point's bearing from the body's facing and its horizontal distance."""
    d = math.hypot(px - ex, py - ey)
    rel = (math.degrees(math.atan2(py - ey, px - ex)) - view["yaw"] + 180.0) % 360.0 - 180.0
    return carried_hides(view["occ"], ez, rel, d, pz)


def _agents(state, rid, skip):
    """Dynamic occluders of a room: (x0, y0, x1, y1, top, porosity, id) for people, pets and the robot."""
    out = []
    for group, kind in (("people", "person"), ("pets", "pet")):
        for aid, agent in sorted(state.get(group, {}).items()):
            if aid in skip or agent.get("location") != rid or not agent.get("pos"):
                continue
            x0, y0 = int(agent["pos"][0]) * CELL, int(agent["pos"][1]) * CELL
            out.append((x0, y0, x0 + CELL, y0 + CELL, agent_top(state, aid), AGENT_POROSITY[kind], aid))
    robot = state["robot"]
    if "robot" not in skip and robot.get("room") == rid and robot.get("pos"):
        x0, y0 = int(robot["pos"][0]) * CELL, int(robot["pos"][1]) * CELL
        out.append((x0, y0, x0 + CELL, y0 + CELL, agent_top(state, "robot"), AGENT_POROSITY["robot"], "robot"))
    return out


# ----- sample points ------------------------------------------------------------------------------------------------------
def sample_points(state, eid, eye=None, cmap=None):
    """Sample points of an entity, person or pet (5.3): people and pets at head and torso; things 0.3 m and larger at
    top centre, centre, the two upper corners of the side facing the eye and the middle of the far upper edge (both at
    0.9 h); 0.1-0.3 m at top centre and centre; smaller things at their top centre (the point rule d tests). Without an
    eye the front side faces it."""
    placed = _place(state, eid, cmap)
    return _samples(state, eid, placed[0], placed[1], eye) if placed else []


def _samples(state, eid, box, rel, eye):
    if rel.kind == "agent":
        cx, cy = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0
        head = person_eye(state, eid)[2] if eid in state.get("people", {}) else box.z1 - 0.05
        torso = box.z0 + 0.65 * (box.z1 - box.z0)
        return [(_r3(cx), _r3(cy), _r3(head)), (_r3(cx), _r3(cy), _r3(torso))]
    if eye is not None:
        _, ex, ey, _, _ = _eye_of(eye)
        vx, vy = ex - (box.x0 + box.x1) / 2.0, ey - (box.y0 + box.y1) / 2.0
    else:
        rad = math.radians(front_deg(state, eid))
        vx, vy = math.cos(rad), math.sin(rad)
    return _box_samples(box, vx, vy)


def _box_samples(box, vx, vy):
    w, d, h = box.x1 - box.x0, box.y1 - box.y0, box.z1 - box.z0
    cx, cy = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0
    top = (_r3(cx), _r3(cy), _r3(box.z1))
    size = max(w, d, h)
    if size < 0.1:
        return [top]
    centre = (_r3(cx), _r3(cy), _r3((box.z0 + box.z1) / 2.0))
    if size < 0.3:
        return [top, centre]
    z = _r3(box.z0 + 0.9 * h)
    ix, iy = min(0.02, w / 4.0), min(0.02, d / 4.0)
    if abs(vx) * d >= abs(vy) * w:      # the eye faces the east or west side
        near_x, far_x = (box.x1 - ix, box.x0 + ix) if vx > 0 else (box.x0 + ix, box.x1 - ix)
        near = [(_r3(near_x), _r3(box.y0 + iy), z), (_r3(near_x), _r3(box.y1 - iy), z)]
        far = (_r3(far_x), _r3(cy), z)
    else:
        near_y, far_y = (box.y1 - iy, box.y0 + iy) if vy > 0 else (box.y0 + iy, box.y1 - iy)
        near = [(_r3(box.x0 + ix), _r3(near_y), z), (_r3(box.x1 - ix), _r3(near_y), z)]
        far = (_r3(cx), _r3(far_y), z)
    return [top, centre] + near + [far]


# ----- room geometry ---------------------------------------------------------------------------------------------------
class RoomGeom(object):
    """Static occluders of one room: per-cell spans (z_lo, z_hi, porosity, source, key), the same spans with their
    exact boxes (x0, y0, x1, y1, z_lo, z_hi, porosity, source, key) for segment tests, per-cell shade regions, and the
    thin occluders tested exactly on every ray instead of per cell (``leaves``: open door leaves, (x0, y0, x1, y1,
    z_lo, z_hi, porosity, source); a 4 cm slab registered in its cells would block the whole 0.5 m cell)."""
    __slots__ = ("rid", "sig", "cols", "rows", "spans", "boxes", "rects", "shade", "leaves")

    def __init__(self, rid, sig, cols, rows):
        self.rid, self.sig, self.cols, self.rows = rid, sig, cols, rows
        self.spans = [()] * (cols * rows)
        self.boxes = []
        self.rects = {}
        self.shade = [()] * (cols * rows)
        self.leaves = ()

    def cell_index(self, x, y):
        c = min(self.cols - 1, max(0, int(math.floor(x / CELL))))
        r = min(self.rows - 1, max(0, int(math.floor(y / CELL))))
        return r * self.cols + c

    def _cells(self, x0, y0, x1, y1):
        c0, r0 = max(0, int(math.floor(x0 / CELL + 1e-6))), max(0, int(math.floor(y0 / CELL + 1e-6)))
        c1 = min(self.cols - 1, int(math.ceil(x1 / CELL - 1e-6)) - 1)
        r1 = min(self.rows - 1, int(math.ceil(y1 / CELL - 1e-6)) - 1)
        return [r * self.cols + c for r in range(r0, max(r0, r1) + 1) for c in range(c0, max(c0, c1) + 1)]


GEOM_FIELDS = ("type", "location", "relation", "pos", "xy_m", "rot", "yaw_deg", "size_m", "spos", "z_m",
               "mount_height_m", "surface_height_m", "surface_size_m", "hang_height_m", "inner_floor_m", "shelf",
               "orientation", "open", "openable", "mounted", "ground_layer", "view", "material", "transparent",
               "portable", "category", "held_hand", "interior_light", "powered", "accepts_items")
DOOR_FIELDS = ("open", "hinge", "size_m", "category", "type", "handle_kind", "mech", "material", "transparent", "view",
               "connects", "swing_into")
_GEOM_CACHE = _LRU(GEOM_CACHE_SIZE)
_SIG_MEMO = weakref.WeakKeyDictionary()
_SOURCES_MEMO = weakref.WeakKeyDictionary()


def _source_candidate(rec):
    """Static part of the occluder/container test (spec 5.1: largest dimension >= 0.25 m, not a ground layer, wall,
    floor or ceiling surface); containers of any size join for their interior shading."""
    if rec.get("ground_layer") or "connects" in rec or rec.get("category") in ("wall", "floor", "ceiling"):
        return False
    for v in (rec.get("size_m") or ())[:3]:
        if _num(v) >= OCCLUDER_MIN_M - EPS:
            return True
    return bool(rec.get("accepts_items") or rec.get("openable"))


def _on_chain(state, eid):
    """True if an entity reaches its room through "on" relations only (or is room-located)."""
    entities, seen = state["entities"], set()
    cur = eid
    while cur in entities and cur not in seen:
        seen.add(cur)
        rec = entities[cur]
        loc = rec.get("location")
        if loc in state["rooms"]:
            return True
        if rec.get("relation", "on") != "on" or loc not in entities:
            return False
        cur = loc
    return False


class _Epoch(object):
    """Geometry epoch of one World: the touch log it last read (the list object, the absolute position, the list
    index and the last id read there) and the World's version then; the global epoch number n (a new set of sources:
    something created, destroyed, moved to another place or turned into or out of a source or a door; a rollback; a
    lost log), a counter per room (room_n: a source of that room, a thing it rests on or one of its doors changed in
    place) and of every change (total); what the current signatures depend on: deps (id -> _dep_sig when last
    judged; None: unknown), where each dep rests (anchor: id -> (location, relation)), whether it is a source
    candidate (cand) and the rooms that read it (rooms); the distinct ids touched since they were last looked at
    (pending), the deps written in place whose rooms were not asked about yet (later: room -> ids; they rest where
    they did, so only their own rooms can change) and whether track of the log was lost (dirty); and for the
    camera's ambient-light memo the ids it watches (light_ids: every window and light and what they rest on; None
    until asked), the World version they were taken at (light_version) and a counter of writes to them (light_n)."""
    __slots__ = ("log", "pos", "idx", "last", "version", "n", "deps", "pending", "dirty", "anchor", "cand", "rooms",
                 "room_n", "total", "later", "light_n", "light_ids", "light_version")

    def __init__(self, log, n, version):
        self.log, self.pos, self.idx, self.last, self.version, self.n = log, 0, 0, None, version, n
        self.deps, self.pending, self.dirty = None, set(), False
        self.anchor, self.cand, self.rooms, self.room_n, self.total, self.later = {}, {}, {}, {}, 0, {}
        self.light_n, self.light_ids, self.light_version = 0, None, None


_EPOCHS = weakref.WeakKeyDictionary()


def _dep_sig(rec):
    """A snapshot of the fields of an entity occlusion depends on (a door's door fields)."""
    values = tuple(rec.get(f) for f in (DOOR_FIELDS if "connects" in rec else GEOM_FIELDS))
    try:
        return marshal.dumps(values)
    except ValueError:              # a value marshal cannot take (records are JSON-like; never in practice)
        return repr(values)


def _anchor(rec):
    """Where a record rests: (location, relation) -- what decides its room and whether it reaches it on "on"s."""
    return rec.get("location"), rec.get("relation")


def _geom_relevant(state, eid, deps):
    """True if a write to eid may have changed occlusion: it no longer exists, the fields occlusion reads changed on
    an occluder or container source, a thing one rests on or a door (deps: id -> _dep_sig when the epoch began), or
    it was none of those and is a source (a candidate reaching its room through "on" relations) or a door now."""
    rec = state["entities"].get(eid)
    if rec is None:
        return True
    sig = deps.get(eid)
    if sig is not None:
        return _dep_sig(rec) != sig
    return "connects" in rec or (_source_candidate(rec) and _on_chain(state, eid))


TOUCH_CONSUMER = "sight"     # the touch-log consumer name of the geometry epoch (geometric camera mode only)


def _lost(ep):
    """Track of the touch log was lost (a rollback, a shrunk or over-trimmed log): everything is unknown."""
    ep.dirty = True
    ep.light_n += 1


def _read_touches(world):
    """Read the World's touch log up to now into its _Epoch (the distinct ids touched, or dirty when track was
    lost), as the "sight" consumer in geometric camera mode; returns the _Epoch, or None when the World keeps no
    touch log."""
    log = getattr(world, "_touched", None)
    if log is None or not hasattr(world, "touched_since"):
        return None
    state = world.s
    version = getattr(world, "_version", 0)
    end = world.touched_since(1 << 62)[1]
    ep = _EPOCHS.get(world)
    if ep is not None and ep.log is log and end == ep.pos and version == ep.version:
        return ep                       # nothing written since the last read (and the consumer cursor is there)
    if ep is None:
        ep = _EPOCHS[world] = _Epoch(log, 0, version)
        ep.pos = end - len(log)
    elif ep.log is not log:
        # a begin() (the step committed: nothing rolled back) or a rollback (the version moved) replaced the log
        old, i = ep.log, ep.idx
        if version != ep.version or i > len(old) or (i and old[i - 1] != ep.last):
            _lost(ep)
        else:
            _note_fresh(ep, old[i:])
        ep.log, ep.version, ep.pos = log, version, end - len(log)
    if end != ep.pos:
        if end < ep.pos or ep.pos < end - len(log):
            _lost(ep)                   # a savepoint rollback shrank the log, or a trim passed the last read
        else:
            _note_fresh(ep, world.touched_since(ep.pos)[0])
        ep.pos = end
    if isinstance(state["robot"].get("view"), dict):
        world.touched_since(end, TOUCH_CONSUMER)    # read up to here: the trim may drop it
    ep.version = version
    ep.idx, ep.last = len(log), (log[-1] if log else None)
    return ep


def _note_fresh(ep, ids):
    if not ids:
        return
    ep.pending.update(ids)
    if ep.light_ids is None or not ep.light_ids.isdisjoint(ids):
        ep.light_n += 1


def _global_bump(ep):
    ep.n += 1
    ep.total += 1
    ep.deps = None
    ep.anchor, ep.cand, ep.rooms, ep.room_n, ep.later = {}, {}, {}, {}, {}
    ep.pending.clear()
    ep.dirty = False


def _judge(world, ep, rid=None):
    """Judge the touches of an _Epoch, for one room (rid) or for every room (None). Exact: a dep (a source, what it
    rests on, a door) that still rests where it did is compared with its snapshot (_dep_sig) -- when one of the rooms
    that read it is asked about; until then it waits in `later` -- and, changed, bumps the counters of those rooms (a
    global epoch when it turned into or out of a source candidate); a dep that rests somewhere else, something
    destroyed, a new source or door, a rollback or a lost log start a new global epoch. Anything else touched cannot
    change occlusion and is dropped."""
    if ep.dirty or (ep.pending and ep.deps is None):
        _global_bump(ep)            # track lost, or nothing to compare with yet
        return
    state = world.s
    entities, rooms = state["entities"], state["rooms"]
    deps, anchors, rooms_of, later = ep.deps, ep.anchor, ep.rooms, ep.later
    if ep.pending:
        for eid in ep.pending:
            rec = entities.get(eid)
            if rec is None:
                _global_bump(ep)
                return
            anchor = anchors.get(eid)
            if anchor is None:          # not a dep (every dep has an anchor)
                if "connects" in rec:
                    _global_bump(ep)
                    return
                # _on_chain's first hop, inlined: inside or part of an entity (not "on" it), the thing is no source
                # whatever its fields; the domains write such parts (freezers, ovens, things in drawers) every substep
                if rec.get("relation", "on") != "on" and rec.get("location") not in rooms:
                    continue
                if _source_candidate(rec) and _on_chain(state, eid):
                    _global_bump(ep)
                    return
                continue
            if rec.get("location") != anchor[0] or rec.get("relation") != anchor[1]:
                _global_bump(ep)
                return
            for room in rooms_of.get(eid, ()):
                waiting = later.get(room)
                if waiting is None:
                    waiting = later[room] = set()
                waiting.add(eid)
        ep.pending.clear()
    if not later:
        return
    for room in (list(ep.later) if rid is None else [rid]):
        for eid in sorted(ep.later.pop(room, ())):
            rec = entities.get(eid)
            if rec is None:
                _global_bump(ep)
                return
            new = _dep_sig(rec)
            if new == deps[eid]:
                continue
            if _source_candidate(rec) != ep.cand.get(eid):
                _global_bump(ep)
                return
            deps[eid] = new
            for other in ep.rooms.get(eid, ()):
                ep.room_n[other] = ep.room_n.get(other, 0) + 1
                if other != room:
                    ep.later.get(other, set()).discard(eid)
            ep.total += 1


def _geom_epoch(world):
    """A token that stays equal while nothing that can change occlusion changed in a World, or None without one.
    Entity writes, creations and destructions come from the World's touch log (TIER1_INTEGRATION 1.8): a write that
    changed what occlusion reads (_judge), a rollback (the World's version moved), a log that shrank (a savepoint
    rollback) or was trimmed past the last read change it; temperature-only quiet writes, section writes and writes
    to other fields never do. While a view_occluders hook is registered (it may read room fields) every mutation
    counts here (occ_sig and room_epoch instead key on the hook's own output). In geometric camera mode the reads are
    a named touch-log consumer ("sight"), so the engine's trim keeps what has not been read; the camera's watch
    system reads every substep (geometry_epoch), which keeps the log bounded. Elsewhere the reads are anonymous and a
    trim past the last read starts a new epoch."""
    count = getattr(world, "mutation_count", None)
    if count is None:
        return None
    ep = _read_touches(world)
    if ep is None or HOOKS.get("view_occluders"):
        return ("count", count)
    _judge(world, ep)
    return ("epoch", ep.total)


def room_epoch(world, rid):
    """A token that stays equal while nothing that can change the occlusion of room rid changed (its sources, what
    they rest on, the doors of its doorways; anything structural anywhere), or None without a World. Writes to the
    deps of other rooms are judged when those rooms are asked about, so the camera's per-substep watch judges only
    the robot's room (TIER1_INTEGRATION 3.4). A World without a touch log gives its mutation count. The
    view_occluders hook output is not part of it: occ_sig keys on it."""
    if world is None or getattr(world, "mutation_count", None) is None:
        return None
    ep = _read_touches(world)
    if ep is None:
        return ("count", world.mutation_count)
    _judge(world, ep, rid)
    return ("epoch", ep.n, ep.room_n.get(rid, 0))


def geometry_epoch(world):
    """Read the World's touch log up to now (the geometric camera's watch system calls it every substep, so the log
    stays bounded); the epoch itself is judged when a room's occlusion signature is next needed."""
    _read_touches(world)


def light_epoch(world, ids_fn):
    """A token that stays equal while no window, light or thing they rest on was written (the touch log), nothing
    was created or destroyed (the World's version) and track of the log was kept, or None without a touch log.
    ids_fn() -> the ids to watch (the camera's ambient-light memo: every window and light and their location
    chains), called when they are unknown or the World's version moved."""
    ep = _read_touches(world) if world is not None else None
    if ep is None:
        return None
    version = getattr(world, "_version", 0)
    if ep.light_ids is None or ep.light_version != version:
        ep.light_ids, ep.light_version = frozenset(ids_fn()), version
        ep.light_n += 1
    return ("light", ep.light_n)


def _sources(state, rid, world=None):
    """Ids of a room's occluder and container candidates that reach it through "on" relations, sorted. One pass sorts
    every candidate into its room; with a World it uses the static index and keeps the pass per global geometry
    epoch, snapshotting each source and what it rests on, and the doors, for the per-room judgement."""
    ep = epoch = None
    if world is not None and getattr(world, "mutation_count", None) is not None:
        ep = _read_touches(world)
        if ep is None:
            epoch = ("count", world.mutation_count)
        else:
            _judge(world, ep, rid)
            epoch = ("epoch", ep.n)
    memo = _SOURCES_MEMO.get(world) if epoch is not None else None
    if memo is not None and memo[0] == epoch:
        return memo[1].get(rid, [])
    entities = state["entities"]
    if world is not None and hasattr(world, "static_index"):
        ids = [eid for eid in world.static_index("sight.sources", _source_candidate) if eid in entities]
    else:
        ids = sorted(eid for eid, rec in entities.items() if _source_candidate(rec))
    by_room = {}
    chain = {}
    for eid in ids:
        room = root_room(state, eid)
        if room is not None and _on_chain(state, eid):
            by_room.setdefault(room, []).append(eid)
            # a source and what it rests on: their fields are what occ_sig digests (_geom_key)
            cur, depth = eid, 0
            while cur in entities and cur not in chain and depth < 20:
                chain[cur] = room
                cur, depth = entities[cur].get("location"), depth + 1
    if epoch is not None:
        _SOURCES_MEMO[world] = (epoch, by_room)
        if ep is not None and ep.deps is None:
            deps, anchor, cand, rooms = {}, {}, {}, {}
            for eid, room in chain.items():
                rec = entities[eid]
                deps[eid], anchor[eid], cand[eid], rooms[eid] = (_dep_sig(rec), _anchor(rec), _source_candidate(rec),
                                                                 (room,))
            for _, conn in sorted(state.get("connections", {}).items()):
                door = conn.get("door")
                if door in entities:
                    rec = entities[door]
                    deps[door], anchor[door], cand[door] = _dep_sig(rec), _anchor(rec), _source_candidate(rec)
                    rooms[door] = tuple(sorted(set(rooms.get(door, ())) | set(conn.get("rooms") or ())))
            ep.deps, ep.anchor, ep.cand, ep.rooms = deps, anchor, cand, rooms
    return by_room.get(rid, [])


def _geom_key(state, eid):
    """The geometric fields of an entity and of the chain it rests on, as one string."""
    entities, out, cur, depth = state["entities"], [], eid, 0
    while cur in entities and depth < 20:
        rec = entities[cur]
        out.append((cur, [rec.get(f) for f in GEOM_FIELDS]))
        cur = rec.get("location")
        depth += 1
    return repr(out)


def occ_sig(state, rid, world=None):
    """Occlusion signature of a room: a digest of the room grid, the geometry of every occluder and container source
    (and the chain it rests on), the doors of its doorways and the @hook("view_occluders") output. With a World it is
    memoized per (room, room_epoch, the hook output): the hook may read anything, so its output itself is the key."""
    hooked = repr(_hook_occluders(state, rid))
    count = room_epoch(world, rid) if world is not None else None
    if count is not None:
        count = (count, hooked)
        memo = _SIG_MEMO.get(world)
        hit = memo.get(rid) if memo is not None else None
        if hit is not None and hit[0] == count:
            return hit[1]
    room = state["rooms"][rid]
    digest = hashlib.blake2b(digest_size=12)
    digest.update(repr((rid, geometry.grid_size(room), _registry_token(default_registry()))).encode("utf-8"))
    for eid in _sources(state, rid, world):
        digest.update(_geom_key(state, eid).encode("utf-8"))
    for cid, conn in sorted(state.get("connections", {}).items()):
        if rid in conn.get("rooms", ()):
            door = state["entities"].get(conn.get("door") or "") or {}
            digest.update(repr((cid, conn.get("door"), conn.get("cells"), conn.get("rooms"),
                                tuple(repr(door.get(f)) for f in DOOR_FIELDS))).encode("utf-8"))
    digest.update(hooked.encode("utf-8"))
    sig = digest.hexdigest()
    if count is not None:
        _SIG_MEMO.setdefault(world, {})[rid] = (count, sig)
    return sig


_REG_TOKENS = weakref.WeakKeyDictionary()


def _registry_token(reg):
    """Content digest of a registry's type view blocks (what view_props reads from it), computed once per registry."""
    token = _REG_TOKENS.get(reg)
    if token is None:
        token = hashlib.blake2b(json.dumps(reg.view, sort_keys=True, default=str).encode("utf-8"),
                                digest_size=8).hexdigest()
        _REG_TOKENS[reg] = token
    return token


def _hook_occluders(state, rid):
    out = []
    for result in run_hooks("view_occluders", state, rid):
        for cells, z_lo, z_hi, porosity, source in result or ():
            out.append((tuple(tuple(int(v) for v in c) for c in cells), _num(z_lo), _num(z_hi), _num(porosity),
                        str(source)))
    return out


def room_geom(state, rid, world=None, sig=None):
    """The RoomGeom of a room for its current occ_sig (cached by content, 64 rooms). sig: the room's occ_sig when the
    caller has just computed it (nothing written since), which spares a second signature."""
    if sig is None:
        sig = occ_sig(state, rid, world)
    geom = _GEOM_CACHE.get(sig)
    if geom is None:
        geom = _build_geom(state, rid, sig, world)
        _GEOM_CACHE.put(sig, geom)
    return geom


def _holds_inside(rec):
    """True for things that hold others inside (cabinets, shelving, appliances, boxes, bins), not on top (tables,
    beds, seats)."""
    return bool(rec.get("openable") or rec.get("shelf_heights") or (_category_row(rec) or {}).get("opening"))


def _occludes(state, eid, rec, rel, box):
    """Occluder test of 5.1 on a placed source."""
    w, d, h = box.x1 - box.x0, box.y1 - box.y0, box.z1 - box.z0
    if h < FLAT_MAX_M or max(w, d, h) < OCCLUDER_MIN_M - EPS:
        return False
    if rel.kind == "room":
        if geometry.floor_standing(rec):
            return True
        return bool(rec.get("mounted")) and min(w, d) >= OCCLUDER_MIN_M - EPS
    if rel.kind == "on":
        return h >= OCCLUDER_MIN_M - EPS and max(w, d) >= OCCLUDER_MIN_M - EPS
    return False


def _build_geom(state, rid, sig, world):
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    geom = RoomGeom(rid, sig, cols, rows)
    cell_spans = [[] for _ in range(cols * rows)]
    cell_shade = [[] for _ in range(cols * rows)]
    leaves = []
    cmap = children_map(state)

    def add(src, k, x0, y0, x1, y1, z_lo, z_hi, porosity, cells=None):
        key = (src, k)
        geom.boxes.append((x0, y0, x1, y1, z_lo, z_hi, porosity, src, key))
        geom.rects[key] = (x0, y0, x1, y1)
        for i in (cells if cells is not None else geom._cells(x0, y0, x1, y1)):
            cell_spans[i].append((z_lo, z_hi, porosity, src, key))

    def shade(x0, y0, x1, y1, z0, z1, kind, rank, cid=None):
        for i in geom._cells(x0, y0, x1, y1):
            cell_shade[i].append((x0, y0, x1, y1, z0, z1, kind, rank, cid))

    entities = state["entities"]
    for eid in _sources(state, rid, world):
        placed = _place(state, eid, cmap)
        if placed is None or placed[0].rid != rid:
            continue
        box, rel = placed
        rec = entities[eid]
        props = view_props(state, eid)
        if _occludes(state, eid, rec, rel, box):
            k = 0
            for part, (x0, y0, x1, y1, spans) in enumerate(_body_parts(state, eid, rec, props, box)):
                for z_lo, z_hi, porosity in spans:
                    add(eid, k, x0, y0, x1, y1, z_lo, z_hi, porosity)
                    k += 1
                    if part == 0 and porosity < 0.5 and z_lo > FLAT_MAX_M:
                        shade(box.x0, box.y0, box.x1, box.y1, 0.0, z_lo, "under", 1)
            if rel.kind == "room" and geometry.floor_standing(rec):
                gap = _num(props.get("behind_gap_m"), 0.0)
                if gap > 0:
                    slot = _behind_slot(state, eid, box, gap)
                    if slot is not None:
                        shade(slot[0], slot[1], slot[2], slot[3], box.z0, box.z1, "behind", 2)
        if _holds_inside(rec):
            shade(box.x0, box.y0, box.x1, box.y1, box.z0, box.z1, "inside", 3, eid)
    for cid, conn in sorted(state.get("connections", {}).items()):
        door_id = conn.get("door")
        door = entities.get(door_id or "")
        if not door or not door.get("open"):
            continue
        srid, cells = geometry.door_swing(state, door_id)
        if srid != rid or not cells:
            continue
        porosity = min(view_props(state, door_id)["porosity"], 0.6)    # a glazed leaf keeps its frame and rails
        height = _num((door.get("size_m") or [0.9, 0.05, 2.0])[2], 2.0)
        for k, (x0, y0, x1, y1) in enumerate(_open_leaves(door, conn["cells"][rid], cells, cols, rows)):
            geom.boxes.append((x0, y0, x1, y1, 0.0, height, porosity, door_id, (door_id, k)))
            geom.rects[(door_id, k)] = (x0, y0, x1, y1)
            leaves.append((x0, y0, x1, y1, 0.0, height, porosity, door_id))
    for k, (cells, z_lo, z_hi, porosity, source) in enumerate(_hook_occluders(state, rid)):
        inside = [c for c in cells if 0 <= c[0] < cols and 0 <= c[1] < rows]
        if not inside or z_hi - z_lo < 1e-6 or porosity >= 0.999:
            continue
        for c in inside:
            add(source, k, c[0] * CELL, c[1] * CELL, (c[0] + 1) * CELL, (c[1] + 1) * CELL, z_lo, z_hi, porosity,
                [c[1] * cols + c[0]])
    geom.spans = [tuple(s) for s in cell_spans]
    geom.shade = [tuple(sorted(s, key=lambda r: -r[7])) for s in cell_shade]
    geom.leaves = tuple(leaves)
    return geom


LEAF_T_M = 0.04             # an interior door leaf is about 35-45 mm thick


def _open_leaves(door, doorway, cells, cols, rows):
    """The rectangles (x0, y0, x1, y1) of an open hinged door's leaves in the swing room. geometry.door_swing sweeps
    the inward cell and the diagonal on the lateral side, so the hinge is on the opposite (-lateral) jamb of the
    doorway cell ("far" mirrors it), unless the doorway is the last cell of its wall on the lateral side (the closed
    leaf could not reach across the opening that way: it then hangs at that corner); the leaf stands open at 90
    degrees at its hinge jamb, perpendicular to the wall,
    LEAF_T_M thick, reaching its width into the room (a double door: two half-width leaves, one at each jamb). A leaf
    whose hinge jamb is a corner lies flat against the side wall. door_swing's cells stay the navigation rule; the
    camera sees the leaf where it stands, not a solid block over its swept area."""
    x, y = int(doorway[0]), int(doorway[1])
    inward, lateral = geometry.wall_inward({"size_m": [cols * CELL, rows * CELL]}, (x, y))
    if door.get("hinge") == "far":
        lateral = (-lateral[0], -lateral[1])
    double = (x + inward[0] - lateral[0], y + inward[1] - lateral[1]) in [tuple(c) for c in cells]
    if double:
        sides = [(-lateral[0], -lateral[1]), lateral]
    elif 0 <= x + lateral[0] < cols and 0 <= y + lateral[1] < rows:
        sides = [(-lateral[0], -lateral[1])]
    else:
        sides = [lateral]           # the doorway ends the wall on that side: the leaf can only open from that corner
    width = _num((door.get("size_m") or [0.85])[0], 0.85)
    reach = max(LEAF_T_M, width / len(sides))
    limit_x, limit_y = cols * CELL, rows * CELL
    out = []
    for side in sides:
        along_x = inward[0] != 0                 # the leaf runs along x (west / east wall), its thickness along y
        if along_x:
            wall = x * CELL if inward[0] > 0 else (x + 1) * CELL
            a0, a1 = (wall, wall + reach) if inward[0] > 0 else (wall - reach, wall)
            jamb, limit, step = ((y + 1) * CELL if side[1] > 0 else y * CELL), limit_y, side[1]
        else:
            wall = y * CELL if inward[1] > 0 else (y + 1) * CELL
            a0, a1 = (wall, wall + reach) if inward[1] > 0 else (wall - reach, wall)
            jamb, limit, step = ((x + 1) * CELL if side[0] > 0 else x * CELL), limit_x, side[0]
        b0, b1 = (jamb, jamb + LEAF_T_M) if step > 0 else (jamb - LEAF_T_M, jamb)
        if b0 < 0.0:                             # a corner: the leaf lies against the side wall, inside the room
            b0, b1 = 0.0, LEAF_T_M
        elif b1 > limit:
            b0, b1 = limit - LEAF_T_M, limit
        a0, a1 = max(0.0, a0), min(limit_x if along_x else limit_y, a1)
        if a1 - a0 <= 1e-6:
            continue
        rect = (a0, b0, a1, b1) if along_x else (b0, a0, b1, a1)
        out.append(tuple(_r3(v) for v in rect))
    return out


def _behind_slot(state, eid, box, gap):
    """The shaded slot between a floor-standing piece's back face and the wall (x0, y0, x1, y1), when it is at most
    BEHIND_SLOT_M deep (a flush piece gives up `gap` of its depth to it); None for pieces standing out in the room."""
    x0, y0, x1, y1, _, _ = _behind_gap(state, eid, box, gap)
    room = state["rooms"][box.rid]
    cols, rows = geometry.grid_size(room)
    back = (_axis_front(front_deg(state, eid)) + 180) % 360
    if back == 0:
        slot = (x1, y0, cols * CELL, y1)
    elif back == 180:
        slot = (0.0, y0, x0, y1)
    elif back == 90:
        slot = (x0, y1, x1, rows * CELL)
    else:
        slot = (x0, 0.0, x1, y0)
    depth = (slot[2] - slot[0]) if back in (0, 180) else (slot[3] - slot[1])
    return slot if 1e-6 < depth <= BEHIND_SLOT_M + 1e-9 else None


# ----- viewshed ---------------------------------------------------------------------------------------------------------
class Viewshed(object):
    """Per-cell blocked slope intervals from one eye: data[i] is None (out of range or wedge) or a tuple of
    (lo, hi, porosity, source) per crossed source span; entry[i] is the distance at which the ray to the cell centre
    enters the cell (E(c))."""
    __slots__ = ("x", "y", "z", "cell", "data", "entry", "wedge", "range_m")


_VS_CACHE = _LRU(VIEWSHED_CACHE_SIZE)


def viewshed(geom, eye, wedge=None):
    """Interval viewshed (5.2) of an eye over a room: one Amanatides-Woo ray from the eye to the centre of each cell
    within range (the eye's range_m, else the head camera's 12 m); wedge = (yaw_deg, half_deg) limits it to cells whose
    centre bearing is within half_deg + 15 degrees of yaw, plus every cell within 1.5 m. Inputs are rounded (x, y to
    1 mm, z to 1 cm, angles to 0.01 degree) before computing, so the content-keyed cache never changes a result."""
    _, ex, ey, ez, _ = _eye_of(eye)
    ex, ey, ez = round(ex, 3), round(ey, 3), round(ez, 2)
    range_m = float(getattr(eye, "range_m", HW["head"]["max_range_m"]))
    if wedge is not None:
        wedge = (round(float(wedge[0]) % 360.0, 2), round(float(wedge[1]), 2))
        if wedge[1] + WEDGE_MARGIN_DEG >= 180.0:
            wedge = None
    full = _VS_CACHE.get((geom.sig, ex, ey, ez, range_m, None))
    if full is not None:
        return full
    key = (geom.sig, ex, ey, ez, range_m, wedge)
    found = _VS_CACHE.get(key) if wedge is not None else None
    if found is None:
        found = _compute_viewshed(geom, ex, ey, ez, wedge, range_m)
        _VS_CACHE.put(key, found)
    return found


_REL = 4096                 # flat key of a cell offset (di, dj) from the eye cell: dj * _REL + di
_TEMPLATES = {}             # (di, dj) -> ray template from a cell-centred eye (pure geometry, never invalid)


def _walk(fx, fy, di, dj):
    """Amanatides-Woo walk from an eye at (fx, fy) inside cell (0, 0) to the centre of cell (di, dj): (crossed cell
    keys, {key: (d_in, d_out)}, entry distance into the target). A ray through a lattice corner steps both axes, so
    cells it only touches are not crossed."""
    dx, dy = (di + 0.5) * CELL - fx, (dj + 0.5) * CELL - fy
    length = math.hypot(dx, dy)
    if dx > 0:
        sx, tdx, tmx = 1, CELL / dx, (CELL - fx) / dx
    elif dx < 0:
        sx, tdx, tmx = -1, -CELL / dx, -fx / dx
    else:
        sx, tdx, tmx = 0, INF, INF
    if dy > 0:
        sy, tdy, tmy = 1, CELL / dy, (CELL - fy) / dy
    elif dy < 0:
        sy, tdy, tmy = -1, -CELL / dy, -fy / dy
    else:
        sy, tdy, tmy = 0, INF, INF
    i = j = 0
    t = 0.0
    dists = {}
    for _ in range(abs(di) + abs(dj) + 2):
        if tmx < tmy - 1e-12:
            t, tmx, i = tmx, tmx + tdx, i + sx
        elif tmy < tmx - 1e-12:
            t, tmy, j = tmy, tmy + tdy, j + sy
        else:
            t, tmx, tmy, i, j = tmx, tmx + tdx, tmy + tdy, i + sx, j + sy
        if i == di and j == dj:
            break
        # an eye on a cell boundary enters the next cell at distance 0: keep slopes finite
        d_in = max(t * length, 1e-3)
        dists[j * _REL + i] = (d_in, max((tmx if tmx < tmy else tmy) * length, d_in))
    return frozenset(dists), dists, t * length


def _compute_viewshed(geom, ex, ey, ez, wedge, range_m):
    cols, rows = geom.cols, geom.rows
    n = cols * rows
    data, entry = [None] * n, [0.0] * n
    eix = min(cols - 1, max(0, int(math.floor(ex / CELL))))
    eiy = min(rows - 1, max(0, int(math.floor(ey / CELL))))
    fx, fy = ex - eix * CELL, ey - eiy * CELL
    centred = abs(fx - CELL / 2.0) < 1e-9 and abs(fy - CELL / 2.0) < 1e-9
    spans_at = {(idx // cols - eiy) * _REL + (idx % cols - eix): sp for idx, sp in enumerate(geom.spans) if sp}
    span_keys = frozenset(spans_at)
    r2 = range_m * range_m
    use_wedge = wedge is not None
    if use_wedge:
        cos_lim = math.cos(math.radians(wedge[1] + WEDGE_MARGIN_DEG))
        cos2 = cos_lim * cos_lim
        wx, wy = math.cos(math.radians(wedge[0])), math.sin(math.radians(wedge[0]))
        near2 = WEDGE_NEAR_M * WEDGE_NEAR_M
    eye_idx = eiy * cols + eix
    for cj in range(rows):
        dy = (cj + 0.5) * CELL - ey
        for ci in range(cols):
            idx = cj * cols + ci
            dx = (ci + 0.5) * CELL - ex
            l2 = dx * dx + dy * dy
            if l2 > r2:
                continue
            if idx == eye_idx:
                data[idx] = ()
                continue
            if use_wedge and l2 > near2:
                # outside the wedge: dot < cos_lim * |d|, compared squared (no square root per cell)
                dot = dx * wx + dy * wy
                if (dot < 0.0 and (cos_lim >= 0.0 or dot * dot > cos2 * l2)) or \
                        (dot >= 0.0 and cos_lim > 0.0 and dot * dot < cos2 * l2):
                    continue
            offset = (ci - eix, cj - eiy)
            template = _TEMPLATES.get(offset) if centred else None
            if template is None:
                template = _walk(fx, fy, offset[0], offset[1])
                if centred:
                    _TEMPLATES[offset] = template
            crossed, dists, entry[idx] = template
            if crossed.isdisjoint(span_keys):
                data[idx] = ()
                continue
            hits = {}
            for key in crossed & span_keys:
                d_in, d_out = dists[key]
                for z_lo, z_hi, porosity, src, k in spans_at[key]:
                    hi = (z_hi - ez) / (d_out if z_hi <= ez else d_in)
                    lo = NEG_INF if z_lo <= 0.0 else (z_lo - ez) / (d_in if z_lo <= ez else d_out)
                    prev = hits.get(k)
                    if prev is None:
                        hits[k] = [lo, hi, porosity, src]
                    else:
                        if lo < prev[0]:
                            prev[0] = lo
                        if hi > prev[1]:
                            prev[1] = hi
            data[idx] = tuple(sorted((v[0], v[1], v[2], v[3]) for v in hits.values()))
    vs = Viewshed()
    vs.x, vs.y, vs.z, vs.cell, vs.data, vs.entry, vs.wedge, vs.range_m = ex, ey, ez, eye_idx, data, entry, wedge,         range_m
    return vs


# ----- transmission -----------------------------------------------------------------------------------------------------
def _ray_rect(ex, ey, ux, uy, x0, y0, x1, y1):
    """(t_in, t_out) distances at which the horizontal ray eye + t (ux, uy) is inside a rectangle, or None."""
    t0, t1 = NEG_INF, INF
    for e, u, lo, hi in ((ex, ux, x0, x1), (ey, uy, y0, y1)):
        if abs(u) < 1e-12:
            if e < lo - 1e-12 or e > hi + 1e-12:
                return None
            continue
        a, b = (lo - e) / u, (hi - e) / u
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
    return (t0, t1) if t0 <= t1 else None


def _blocks(z_lo, z_hi, d_in, d_out, ez, s):
    """True if slope s lies in the interval a span (z_lo, z_hi) blocks between horizontal distances d_in and d_out
    (EPS-inclusive)."""
    hi = (z_hi - ez) / (d_out if z_hi <= ez else d_in)
    if s > hi + EPS:
        return False
    if z_lo <= 0.0:
        return True
    return s >= (z_lo - ez) / (d_in if z_lo <= ez else d_out) - EPS


def _transmission(geom, vs, ex, ey, ez, px, py, pz, skip, support, agents):
    idx = geom.cell_index(px, py)
    data = vs.data[idx]
    if data is None:
        return 0.0
    dx, dy = px - ex, py - ey
    d = math.sqrt(dx * dx + dy * dy)
    if d < MIN_DIST_M:
        return 1.0
    s = (pz - ez) / d
    t = 1.0
    for lo, hi, porosity, src in data:
        if src in skip or s < lo - EPS or s > hi + EPS:
            continue
        if porosity < 0.5:
            return 0.0
        t *= porosity
    own = geom.spans[idx]
    if own:
        d_in = max(1e-3, min(vs.entry[idx], d))
        for z_lo, z_hi, porosity, src, _ in own:
            if src not in skip and _blocks(z_lo, z_hi, d_in, d, ez, s):
                if porosity < 0.5:
                    return 0.0
                t *= porosity
    ux, uy = dx / d, dy / d
    if support is not None:
        for x0, y0, x1, y1, spans in support:
            hit = _ray_rect(ex, ey, ux, uy, x0, y0, x1, y1)
            if hit is not None and hit[1] > 0.0 and hit[0] < d - 1e-9:
                d_in, d_out = max(1e-3, hit[0]), min(hit[1], d)
                for z_lo, z_hi, porosity in spans:
                    if _blocks(z_lo, z_hi, d_in, d_out, ez, s):
                        if porosity < 0.5:
                            return 0.0
                        t *= porosity
    for x0, y0, x1, y1, z_lo, z_hi, porosity, src in geom.leaves:
        if src in skip:
            continue
        hit = _ray_rect(ex, ey, ux, uy, x0, y0, x1, y1)
        if hit is not None and hit[1] > 0.0 and hit[0] < d - 1e-9:
            if _blocks(z_lo, z_hi, max(1e-3, hit[0]), min(hit[1], d), ez, s):
                if porosity < 0.5:
                    return 0.0
                t *= porosity
    for x0, y0, x1, y1, top, porosity, aid in agents:
        if aid in skip:
            continue
        if x0 is None:                  # the robot's carried load, from its own head camera (_occluders)
            if skip.isdisjoint(y0["items"]) and carried_blocks(y0, ex, ey, ez, px, py, pz):
                return 0.0
            continue
        hit = _ray_rect(ex, ey, ux, uy, x0, y0, x1, y1)
        if hit is not None and hit[1] > 0.0 and hit[0] < d - 1e-9:
            if _blocks(0.0, top, max(1e-3, hit[0]), min(hit[1], d), ez, s):
                t *= porosity
    return t


def _segment_box(p0, p1, x0, y0, x1, y1, z0, z1):
    """True if the open segment p0-p1 passes through the box (touching a face or an endpoint does not count)."""
    t0, t1 = 0.0, 1.0
    for a, b, lo, hi in ((p0[0], p1[0], x0, x1), (p0[1], p1[1], y0, y1), (p0[2], p1[2], z0, z1)):
        delta = b - a
        if abs(delta) < 1e-12:
            if a <= lo + 1e-9 or a >= hi - 1e-9:
                return False
            continue
        u, v = (lo - a) / delta, (hi - a) / delta
        if u > v:
            u, v = v, u
        t0, t1 = max(t0, u), min(t1, v)
        if t0 >= t1 - 1e-9:
            return False
    return t0 < 1.0 - 1e-9 and t1 > 1e-9


def _sample_transmission(geom, vs, p, rel, agents):
    """Transmission to one sample point under its relation rule."""
    ex, ey, ez = vs.x, vs.y, vs.z
    if rel.kind == "in":
        factor = _opening_factor(rel.container, ex, ey, ez, p)
        for x0, y0, z0, x1, y1, z1, porosity in rel.siblings:
            if factor <= 0.0:
                break
            if x0 + EPS < p[0] < x1 - EPS and y0 + EPS < p[1] < y1 - EPS and z0 + EPS < p[2] < z1 - EPS:
                # the sample lies inside this neighbour's box: an overfull container's packing overlaps them (rows
                # past the back stay at the back), and a thing cannot be in front of a point inside itself; without
                # this, two things packed into one box would hide each other from every eye (B3 merge)
                continue
            if _segment_box((ex, ey, ez), p, x0, y0, x1, y1, z0, z1):
                factor = factor * porosity if porosity >= 0.5 else 0.0
        if factor <= 0.0:
            return 0.0
        return factor * _transmission(geom, vs, ex, ey, ez, p[0], p[1], p[2], rel.skip, None, agents)
    return _transmission(geom, vs, ex, ey, ez, p[0], p[1], p[2], rel.skip, rel.support, agents)


def _opening_factor(info, ex, ey, ez, p):
    """How much of the view of a point inside a container passes its walls (5.4e): 1 through the opening (front: the
    eye within 75 degrees of the front and the ray crossing the front plane inside the opening, between the point's
    shelf board and the next; top: the eye above the rim and the ray crossing the rim plane inside it), the container's
    porosity through its walls when that is >= 0.5 (glass), else 0."""
    through = info.porosity if info.porosity >= 0.5 else 0.0
    if not info.open:
        return through
    if info.opening == "top":
        if p[2] >= info.z1 - 1e-9:
            return 1.0          # sticks out above the rim
        if ez <= info.z1 + 1e-9:
            return through      # the eye is not above the rim
        t = (ez - info.z1) / (ez - p[2])
        x, y = ex + (p[0] - ex) * t, ey + (p[1] - ey) * t
        inside = info.x0 - 1e-9 <= x <= info.x1 + 1e-9 and info.y0 - 1e-9 <= y <= info.y1 + 1e-9
        return 1.0 if inside else through
    if info.opening != "front":
        return through
    rad = math.radians(info.front)
    nx, ny = round(math.cos(rad)), round(math.sin(rad))
    cx, cy = (info.x0 + info.x1) / 2.0, (info.y0 + info.y1) / 2.0
    vx, vy = ex - cx, ey - cy
    if vx * nx + vy * ny < math.cos(math.radians(FRONT_CONE_DEG)) * math.hypot(vx, vy) - EPS:
        return through
    plane = {0: info.x1, 180: info.x0, 90: info.y1, 270: info.y0}[info.front]
    e_n, p_n = (ex, p[0]) if nx else (ey, p[1])
    if abs(e_n - p_n) < 1e-12 or (e_n - plane) * (nx or ny) <= 0.0:
        return through
    t = (e_n - plane) / (e_n - p_n)
    lateral = (ey + (p[1] - ey) * t) if nx else (ex + (p[0] - ex) * t)
    lo, hi = (info.y0, info.y1) if nx else (info.x0, info.x1)
    z = ez + (p[2] - ez) * t
    floor = max([s for s in info.shelves if s <= p[2] + 1e-6] or [info.z0])
    ceiling = min([s for s in info.shelves if s > p[2] + 1e-6] or [info.z1])
    if lo - 1e-9 <= lateral <= hi + 1e-9 and floor - 1e-9 <= z <= ceiling + 1e-9:
        return 1.0
    return through


def _with_ignore(state, eid, rel, ignore):
    """An entity's placement relation as seen by an eye that ignores `ignore`: those ids never occlude it, and when its
    support is among them (a light cushion the wrist lifts to look under, kernel v3.0 CAM-ACTIONS) the support's exact
    test is dropped too."""
    if not ignore:
        return rel
    skip = rel.skip | frozenset(ignore)
    if rel.support is not None and (state["entities"].get(eid) or {}).get("location") in ignore:
        return rel._replace(skip=skip, support=None)
    return rel._replace(skip=skip)


def point_transmission(state, geom, vs, eye, p, rel=None, cmap=None):
    """Fraction of the view from an eye that reaches point p (5.2, 5.4): 0 when a solid span blocks it, else the
    product of the porous spans and agents it passes. rel: None for a free point (a floor cell, an event), or the id of
    the entity (or person / pet) the point belongs to, whose relation rule then applies (its own spans and its support
    or container never occlude it)."""
    rid, _, _, _, ignore = _eye_of(eye)
    skip = frozenset(ignore)
    if rel is None:
        info = _Rel("point", None, skip, None, None)
    else:
        placed = _place(state, rel, cmap)
        if placed is None:
            return 0.0
        info = placed[1]
        if info.kind == "held" and _robot_viewer(ignore):
            return 1.0
        info = _with_ignore(state, rel, info, ignore)
    return _sample_transmission(geom, vs, p, info, _occluders(state, eye, info.skip, cmap))


def visible_fraction(state, eid, eye, geom=None, vs=None, cmap=None):
    """(fraction, best_point): the mean transmission of an entity's sample points from an eye in any direction
    (heading-independent line of sight), rounded to a quarter, and its best-seen sample (None when nothing is seen).
    Held items are always fully visible to the robot's own cameras; things in other rooms are not visible."""
    placed = _place(state, eid, cmap)
    rid, _, _, _, ignore = _eye_of(eye)
    if placed is None or placed[0].rid != rid:
        return 0.0, None
    box, rel = placed
    if rel.kind == "held" and _robot_viewer(ignore):
        return 1.0, (_r3((box.x0 + box.x1) / 2.0), _r3((box.y0 + box.y1) / 2.0), _r3((box.z0 + box.z1) / 2.0))
    geom = geom or room_geom(state, rid)
    vs = vs or viewshed(geom, eye)
    rel = _with_ignore(state, eid, rel, ignore)
    agents = _occluders(state, eye, rel.skip, cmap)
    points = _samples(state, eid, box, rel, eye)
    best, best_t, total = None, 0.0, 0.0
    for p in points:
        t = _sample_transmission(geom, vs, p, rel, agents)
        total += t
        if t > best_t + EPS:
            best, best_t = p, t
    return quarter(total / len(points)) if points else 0.0, best


def los(state, rid, p0, p1, ignore=(), geom=None):
    """Transmission along the segment p0-p1 in a room, tested exactly against every occluder box and agent (no
    viewshed): 0 when a solid span crosses it, else the product of the porous ones."""
    geom = geom or room_geom(state, rid)
    skip = frozenset(ignore)
    t = 1.0
    for x0, y0, x1, y1, z_lo, z_hi, porosity, src, _ in geom.boxes:
        if src in skip or not _segment_box(p0, p1, x0, y0, x1, y1, z_lo, z_hi):
            continue
        if porosity < 0.5:
            return 0.0
        t *= porosity
    for x0, y0, x1, y1, top, porosity, aid in _agents(state, rid, skip):
        if _segment_box(p0, p1, x0, y0, x1, y1, 0.0, top):
            t *= porosity
    return t


# ----- viewers and frames -----------------------------------------------------------------------------------------------
_FRUSTA = {}


def _frustum(viewer):
    key = (viewer.yaw, viewer.tilt, viewer.hfov, viewer.vfov)
    found = _FRUSTA.get(key)
    if found is None:
        half = viewer.hfov / 2.0
        lo, hi = viewer.tilt - viewer.vfov / 2.0, viewer.tilt + viewer.vfov / 2.0
        found = (math.cos(math.radians(viewer.yaw)), math.sin(math.radians(viewer.yaw)),
                 -1.0 if half >= 180.0 else math.cos(math.radians(half)),
                 NEG_INF if lo <= -90.0 else math.tan(math.radians(lo)),
                 INF if hi >= 90.0 else math.tan(math.radians(hi)), lo <= -90.0, hi >= 90.0)
        if len(_FRUSTA) > 4096:
            _FRUSTA.clear()
        _FRUSTA[key] = found
    return found


def in_frame(viewer, p):
    """True if point p is inside a viewer's frame: |bearing - yaw| <= hfov/2, |elevation - tilt| <= vfov/2 and
    0.05 m <= distance <= range (decided with dot products and slopes against precomputed cos/tan)."""
    dx, dy, dz = p[0] - viewer.x, p[1] - viewer.y, p[2] - viewer.z
    h2 = dx * dx + dy * dy
    d2 = h2 + dz * dz
    if d2 < MIN_DIST_M * MIN_DIST_M or d2 > viewer.range_m * viewer.range_m:
        return False
    fx, fy, cos_h, tan_lo, tan_hi, down, up = _frustum(viewer)
    h = math.sqrt(h2)
    if h < 1e-9:
        return up if dz > 0 else down
    if dx * fx + dy * fy < cos_h * h - EPS:
        return False
    slope = dz / h
    return tan_lo - EPS <= slope <= tan_hi + EPS


def bearing_elev(viewer, p):
    """(bearing relative to the viewer's yaw in (-180, 180], positive right; elevation, positive up) in degrees."""
    dx, dy, dz = p[0] - viewer.x, p[1] - viewer.y, p[2] - viewer.z
    bearing = wrap_deg(math.degrees(math.atan2(dy, dx)) - viewer.yaw)
    return round(bearing, 2), round(math.degrees(math.atan2(dz, math.hypot(dx, dy))), 2)


def sees_entity(state, viewer, eid, geom=None, cmap=None):
    """Visible fraction of an entity in one viewer's frame: line of sight x field of view x range per sample point,
    rounded to a quarter (0.0 when it is in another room or out of frame)."""
    if str(getattr(viewer, "kind", "")).startswith("aperture:"):
        return aperture_sees(state, viewer, eid, cmap)      # a viewer outside, looking in through an opening (5.8)
    placed = _place(state, eid, cmap)
    if placed is None or placed[0].rid != viewer.rid:
        return 0.0
    box, rel = placed
    ignore = tuple(viewer.ignore or ())
    if rel.kind == "held" and _robot_viewer(ignore):
        return 1.0
    geom = geom or room_geom(state, viewer.rid)
    vs = viewshed(geom, viewer, (viewer.yaw, viewer.hfov / 2.0))
    rel = _with_ignore(state, eid, rel, ignore)
    agents = _occluders(state, viewer, rel.skip, cmap)
    points = _samples(state, eid, box, rel, viewer)
    total = 0.0
    for p in points:
        if in_frame(viewer, p):
            total += _sample_transmission(geom, vs, p, rel, agents)
    return quarter(total / len(points)) if points else 0.0


def view_cells(state, viewer, z=0.02, geom=None):
    """Floor cells (c, r) of the viewer's room whose centre point at height z is in frame, within range and in line of
    sight (any transmission)."""
    geom = geom or room_geom(state, viewer.rid)
    vs = viewshed(geom, viewer, (viewer.yaw, viewer.hfov / 2.0))
    skip = frozenset(viewer.ignore or ())
    agents = _occluders(state, viewer, skip)
    out = set()
    for r in range(geom.rows):
        for c in range(geom.cols):
            p = ((c + 0.5) * CELL, (r + 0.5) * CELL, z)
            if in_frame(viewer, p) and _transmission(geom, vs, vs.x, vs.y, vs.z, p[0], p[1], p[2], skip, None,
                                                     agents) > 0.0:
                out.add((c, r))
    return out


def sample_transmissions(state, eid, eye, geom=None, vs=None, cmap=None, only=None):
    """(box, [(point, transmission), ...]): the sample points of an entity, person or pet (5.3) with the transmission
    from an eye in any direction to each under its relation rule (5.4), or (None, []) when it is not placed in the
    eye's room. A held item seen by the robot's own cameras is one fully visible sample at its centre. The visible
    fraction in one frame is quarter(sum of the in-frame transmissions / number of samples); visible_fraction and
    sees_entity are this with every sample, or the frame's, counted. only: fn(point) -> bool; samples it rejects get
    transmission 0.0 without being traced (a caller that counts only in-frame samples)."""
    placed = _place(state, eid, cmap)
    rid, _, _, _, ignore = _eye_of(eye)
    if placed is None or placed[0].rid != rid:
        return None, []
    box, rel = placed
    if rel.kind == "held" and _robot_viewer(ignore):
        return box, [((_r3((box.x0 + box.x1) / 2.0), _r3((box.y0 + box.y1) / 2.0), _r3((box.z0 + box.z1) / 2.0)), 1.0)]
    points = _samples(state, eid, box, rel, eye)
    if only is not None:
        keep = [only(p) for p in points]
        if not any(keep):
            return box, [(p, 0.0) for p in points]
    geom = geom or room_geom(state, rid)
    vs = vs or viewshed(geom, eye)
    rel = _with_ignore(state, eid, rel, ignore)
    agents = _occluders(state, eye, rel.skip, cmap)
    if only is not None:
        return box, [(p, _sample_transmission(geom, vs, p, rel, agents) if k else 0.0) for p, k in zip(points, keep)]
    return box, [(p, _sample_transmission(geom, vs, p, rel, agents)) for p in points]


def seen_floor_cells(state, viewer, geom=None, max_m=None, min_light=None, ambient=None, beams=(), vs=None):
    """Floor cells (c, r) of a viewer's room that one frame surveys: the cell centre at 2 cm in frame, within max_m
    (3D; default the viewer's range) and in line of sight (any transmission), and, when min_light is given, lit to at
    least min_light there (light_at with `beams`; `ambient`: the room's light_level when the caller has it). vs: a
    viewshed from the viewer's eye when the caller has one (full or with this frame's wedge)."""
    geom = geom or room_geom(state, viewer.rid)
    vs = vs or viewshed(geom, viewer, (viewer.yaw, viewer.hfov / 2.0))
    skip = frozenset(viewer.ignore or ())
    agents = _occluders(state, viewer, skip)
    reach = viewer.range_m if max_m is None else min(float(max_m), viewer.range_m)
    reach2 = reach * reach
    check_light = min_light is not None
    if check_light:
        ambient = _ambient(state, viewer.rid) if ambient is None else float(ambient)
        # every place is lit enough when even the deepest shade keeps the ambient above the bar (hooks and beams only
        # add light)
        check_light = ambient * min(SHADE.values()) < float(min_light) - EPS
    out = set()
    for r in range(geom.rows):
        y = (r + 0.5) * CELL
        for c in range(geom.cols):
            p = ((c + 0.5) * CELL, y, 0.02)
            dx, dy, dz = p[0] - viewer.x, p[1] - viewer.y, p[2] - viewer.z
            if dx * dx + dy * dy + dz * dz > reach2 or not in_frame(viewer, p):
                continue
            if _transmission(geom, vs, vs.x, vs.y, vs.z, p[0], p[1], p[2], skip, None, agents) <= 0.0:
                continue
            if check_light and light_at(state, viewer.rid, p, beams, ambient, geom) < float(min_light) - EPS:
                continue
            out.add((c, r))
    return out


def person_viewer(state, pid, attention=False):
    """A person's eyes as a Viewer: 200 degrees of awareness (120 of attention), 120 vertical, 15 m, level gaze along
    their facing_deg; the person never occludes their own view."""
    x, y, z = person_eye(state, pid)
    person = state["people"][pid]
    hfov = PERSON_VIEW["hfov_attention_deg"] if attention else PERSON_VIEW["hfov_awareness_deg"]
    return Viewer(person["location"], x, y, z, _num(person.get("facing_deg"), 90.0) % 360.0, 0.0, hfov,
                  PERSON_VIEW["vfov_deg"], PERSON_VIEW["range_m"], PERSON_VIEW["focal_px"], "person", (pid,))


def device_viewer(state, eid):
    """A device camera (doorbell, webcam, security camera) as a Viewer from its view.camera block {hfov_deg, vfov_deg,
    range_m, focal_px, facing_deg (grid; default its front), tilt_deg, height_m, ir}; None without one. The device and
    what carries it never occlude it."""
    spec = view_props(state, eid).get("camera")
    placed = _place(state, eid)
    if not isinstance(spec, dict) or placed is None:
        return None
    box = placed[0]
    hfov = _num(spec.get("hfov_deg"), DEVICE_CAMERA["hfov_deg"])
    vfov = _num(spec.get("vfov_deg"), math.degrees(2.0 * math.atan(math.tan(math.radians(hfov) / 2.0)
                                                                  * DEVICE_CAMERA["aspect"])))
    focal = _num(spec.get("focal_px"), focal_px(DEVICE_CAMERA["res_w"], hfov))
    yaw = _num(spec.get("facing_deg"), front_deg(state, eid)) % 360.0
    z = _num(spec.get("height_m"), (box.z0 + box.z1) / 2.0)
    return Viewer(box.rid, (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, z, yaw,
                  _num(spec.get("tilt_deg"), DEVICE_CAMERA["tilt_deg"]), hfov, vfov,
                  _num(spec.get("range_m"), DEVICE_CAMERA["range_m"]), focal, "device", (eid,))


# ----- doorway and window apertures (5.8; camera phase 3) ----------------------------------------------------------------
# Rooms are separate grids. A view crosses from one into another only through an opening in their shared wall: an open
# doorway (no door, or its door open; a closed glazed door passes its glass porosity) or a window. A doorway view places
# room B against room A by the rigid transform that abuts their doorway cells across the wall (B's inward normal onto
# the opposite of A's; B's doorway cell one cell beyond A's), so a ray from an eye in A that crosses the opening goes
# on into B: A's occluders up to the wall (A's viewshed), B's after it (an opening viewshed of B from the eye's image
# outside B's grid, kept to the rays that enter through the opening). A viewer outside a window (aperture_viewer) sees
# into the window's room the same way, through the window's glass and coverings and past the glass's daylight
# reflection. Connections between floors (stairs, hatches) are not openings.
APERTURE_KINDS = ("doorway", "gate", "garage", "elevator_door")     # connections a camera sees through when open
DOOR_HEIGHT_M = 2.0         # an opening without a door record (or a height) is 2.0 m high
DOORWAY_WIDTH_M = 0.85      # ... and 0.85 m wide (geometry's doorway width)
COVER_BLOCK = 0.95          # a fully covered window still passes 5 %
REFLECT_RATIO = 2.0         # glass reflects in daylight: an outdoor light above 2 x the target's light ...
REFLECT_DEPTH_M = 1.0       # ... hides what stands more than 1 m inside from a viewer outside (at night it shows)
APERTURE_VIEW = {"hfov_deg": 120.0, "vfov_deg": 120.0, "range_m": 15.0, "focal_px": PERSON_VIEW["focal_px"]}
OPENING_VS_CACHE_SIZE = 512
# The opening as the room seen into has it: its inward normal (nx, ny), the wall line (x when nx else y), the lateral
# range along the wall (y when nx else x) and the height range, in that room's frame
Opening = collections.namedtuple("Opening", "room nx ny wall lo hi z0 z1")
# One opening seen from room rid into room other (a doorway view): the opening in rid's frame (nx, ny inward, wall, lo,
# hi, z0, z1), what it passes (factor), the transform of other's frame into rid's (a quarter-turn rotation cos / sin
# and other's doorway cell centre bx, by landing on ax, ay) and the opening in other's frame (into); id: the connection
Aperture = collections.namedtuple("Aperture", "id rid other nx ny wall lo hi z0 z1 factor cos sin bx by ax ay into door")
_QUARTERS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def _wall_line(cell, inward):
    """The wall coordinate of a perimeter cell's wall side: x for a west / east wall, y for a north / south one."""
    if inward[0] > 0:
        return int(cell[0]) * CELL
    if inward[0] < 0:
        return (int(cell[0]) + 1) * CELL
    if inward[1] > 0:
        return int(cell[1]) * CELL
    return (int(cell[1]) + 1) * CELL


def to_room(ap, x, y):
    """A point of the room an Aperture looks into (ap.other's frame) in the frame of the room it looks from."""
    dx, dy = x - ap.bx, y - ap.by
    return ap.ax + ap.cos * dx - ap.sin * dy, ap.ay + ap.sin * dx + ap.cos * dy


def from_room(ap, x, y):
    """A point of the room an Aperture looks from in the frame of the room it looks into (the inverse of to_room)."""
    dx, dy = x - ap.ax, y - ap.ay
    return ap.bx + ap.cos * dx + ap.sin * dy, ap.by - ap.sin * dx + ap.cos * dy


def to_room_deg(ap, deg):
    """A grid direction of the room looked into, in the frame of the room looked from."""
    return (float(deg) + math.degrees(math.atan2(ap.sin, ap.cos))) % 360.0


def doorway_aperture(state, cid, rid):
    """The Aperture of connection cid seen from room rid, or None: not a see-through kind (APERTURE_KINDS), rooms on
    different floors, a cell missing, its door closed and opaque (a closed glazed door passes its porosity), or the
    rooms' walls not overlapping at the doorway. The opening is the connection's width_m (else the door's width, else
    0.85 m) centred on the doorway cell, clipped to both rooms' walls, from the floor to the door's height (2.0 m)."""
    conn = (state.get("connections") or {}).get(cid) or {}
    rooms = state["rooms"]
    pair = list(conn.get("rooms") or ())
    if rid not in pair or len(pair) != 2 or conn.get("kind", "doorway") not in APERTURE_KINDS:
        return None
    other = pair[1] if pair[0] == rid else pair[0]
    ra, rb = rooms.get(rid), rooms.get(other)
    cells = conn.get("cells") or {}
    ca, cb = cells.get(rid), cells.get(other)
    if ra is None or rb is None or ca is None or cb is None or other == rid:
        return None
    if ra.get("floor") is not None and rb.get("floor") is not None and ra.get("floor") != rb.get("floor"):
        return None
    door_id = conn.get("door")
    door = state["entities"].get(door_id or "")
    factor, height = 1.0, DOOR_HEIGHT_M
    width = _num(conn.get("width_m"), 0.0)
    if door is not None:
        size = door.get("size_m") or ()
        if len(size) > 2 and _num(size[2], 0.0) > 0.0:
            height = _num(size[2])
        if width <= 0.0 and len(size) > 0:
            width = _num(size[0], 0.0)
        if not door.get("open"):
            factor = _num(view_props(state, door_id).get("porosity"), 0.0)
            if factor < 0.5:
                return None             # a closed opaque door
    if width <= 0.0:
        width = DOORWAY_WIDTH_M
    na, _ = geometry.wall_inward(ra, ca)
    nb, _ = geometry.wall_inward(rb, cb)
    for cos, sin in _QUARTERS:
        if (cos * nb[0] - sin * nb[1], sin * nb[0] + cos * nb[1]) == (-na[0], -na[1]):
            break
    bx, by = (int(cb[0]) + 0.5) * CELL, (int(cb[1]) + 0.5) * CELL
    ax, ay = (int(ca[0]) + 0.5 - na[0]) * CELL, (int(ca[1]) + 0.5 - na[1]) * CELL
    wall = _wall_line(ca, na)
    cols_a, rows_a = geometry.grid_size(ra)
    cols_b, rows_b = geometry.grid_size(rb)
    centre = (int(ca[1]) + 0.5) * CELL if na[0] else (int(ca[0]) + 0.5) * CELL
    lo, hi = max(0.0, centre - width / 2.0), min((rows_a if na[0] else cols_a) * CELL, centre + width / 2.0)
    wall_b = _wall_line(cb, nb)
    ends = ((wall_b, 0.0), (wall_b, rows_b * CELL)) if nb[0] else ((0.0, wall_b), (cols_b * CELL, wall_b))
    probe = Aperture(cid, rid, other, na[0], na[1], wall, lo, hi, 0.0, height, factor, cos, sin, bx, by, ax, ay,
                     None, door_id)
    lat = sorted((to_room(probe, x, y)[1] if na[0] else to_room(probe, x, y)[0]) for x, y in ends)
    lo, hi = max(lo, lat[0]), min(hi, lat[1])
    if hi - lo < 1e-6:
        return None
    ends_b = [from_room(probe, wall, v) if na[0] else from_room(probe, v, wall) for v in (lo, hi)]
    lat_b = sorted((y if nb[0] else x) for x, y in ends_b)
    into = Opening(other, nb[0], nb[1], wall_b, _r3(lat_b[0]), _r3(lat_b[1]), 0.0, round(height, 3))
    return probe._replace(lo=_r3(lo), hi=_r3(hi), z1=round(height, 3), into=into)


def doorway_views(state, eye):
    """The doorway Apertures an eye's room sees through into its neighbours now (5.8; camera phase 3), in connection-id
    order: open doorways (and closed glazed doors) of see-through kinds between rooms of one floor. aperture_wedge
    gives each one's angular extent from the eye, to_room / from_room its transform."""
    rid = _eye_of(eye)[0]
    out = []
    for cid, conn in sorted((state.get("connections") or {}).items()):
        if rid in (conn.get("rooms") or ()):
            ap = doorway_aperture(state, cid, rid)
            if ap is not None:
                out.append(ap)
    return out


def aperture_wedge(ap, eye):
    """(yaw_deg, half_deg) of the horizontal wedge an opening covers from an eye in the room it is seen from (the
    directions to its two ends)."""
    _, ex, ey, _, _ = _eye_of(eye)
    ends = [(ap.wall, ap.lo), (ap.wall, ap.hi)] if ap.nx else [(ap.lo, ap.wall), (ap.hi, ap.wall)]
    a = [math.degrees(math.atan2(y - ey, x - ex)) for x, y in ends]
    span = wrap_deg(a[1] - a[0])
    return round((a[0] + span / 2.0) % 360.0, 2), round(abs(span) / 2.0, 2)


def _crossing(op, ex, ey, ez, px, py, pz):
    """(t, lateral, z) where the segment from an eye outside an Opening's room to a point inside crosses the opening's
    wall line, or None when it does not cross it from outside to inside."""
    e_n, p_n = (ex, px) if op.nx else (ey, py)
    sign = op.nx or op.ny
    if (e_n - op.wall) * sign > 1e-9 or (p_n - op.wall) * sign <= 0.0:
        return None
    t = (op.wall - e_n) / (p_n - e_n)
    lateral = (ey + t * (py - ey)) if op.nx else (ex + t * (px - ex))
    return t, lateral, ez + t * (pz - ez)


def _through(op, ex, ey, ez, px, py, pz):
    """True when the segment from an eye outside an Opening's room to a point inside passes through the opening."""
    found = _crossing(op, ex, ey, ez, px, py, pz)
    if found is None:
        return False
    _, lateral, z = found
    return op.lo - EPS <= lateral <= op.hi + EPS and op.z0 - EPS <= z <= op.z1 + EPS


_OPENING_VS = _LRU(OPENING_VS_CACHE_SIZE)


def opening_viewshed(geom, op, eye, range_m=None):
    """The interval viewshed (5.2) of an Opening's room from an eye outside its grid: per cell whose centre ray enters
    through the opening (laterally; heights are judged per point) the slope intervals of the spans it crosses from the
    wall on, with horizontal distances from the eye; None for the other cells. Rounded inputs, content-keyed cache."""
    _, ex, ey, ez, _ = _eye_of(eye)
    ex, ey, ez = round(ex, 3), round(ey, 3), round(ez, 2)
    range_m = float(range_m if range_m is not None else getattr(eye, "range_m", HW["head"]["max_range_m"]))
    key = (geom.sig, ex, ey, ez, op, round(range_m, 2))
    found = _OPENING_VS.get(key)
    if found is None:
        found = _compute_opening_viewshed(geom, op, ex, ey, ez, range_m)
        _OPENING_VS.put(key, found)
    return found


def _compute_opening_viewshed(geom, op, ex, ey, ez, range_m):
    cols, rows = geom.cols, geom.rows
    n = cols * rows
    data, entry = [None] * n, [0.0] * n
    spans = geom.spans
    r2 = range_m * range_m
    e_n, e_l = (ex, ey) if op.nx else (ey, ex)
    sign = op.nx or op.ny
    if (e_n - op.wall) * sign <= 1e-9:
        for cj in range(rows):
            cy = (cj + 0.5) * CELL
            for ci in range(cols):
                cx = (ci + 0.5) * CELL
                dx, dy = cx - ex, cy - ey
                l2 = dx * dx + dy * dy
                if l2 > r2:
                    continue
                c_n, c_l = (cx, cy) if op.nx else (cy, cx)
                if abs(c_n - e_n) < 1e-12:
                    continue
                t0 = (op.wall - e_n) / (c_n - e_n)
                lateral = e_l + t0 * (c_l - e_l)
                if not 0.0 <= t0 < 1.0 or lateral < op.lo - EPS or lateral > op.hi + EPS:
                    continue
                walked = _walk_in(ex, ey, dx, dy, t0, ci, cj, cols, rows)
                if walked is None:
                    continue
                crossed, t_in = walked
                length = math.sqrt(l2)
                idx = cj * cols + ci
                entry[idx] = max(t_in * length, 1e-3)
                hits = {}
                for k, (ta, tb) in crossed.items():
                    if not spans[k]:
                        continue
                    d_in = max(ta * length, 1e-3)
                    d_out = max(tb * length, d_in)
                    for z_lo, z_hi, porosity, src, key in spans[k]:
                        hi = (z_hi - ez) / (d_out if z_hi <= ez else d_in)
                        lo = NEG_INF if z_lo <= 0.0 else (z_lo - ez) / (d_in if z_lo <= ez else d_out)
                        prev = hits.get(key)
                        if prev is None:
                            hits[key] = [lo, hi, porosity, src]
                        else:
                            prev[0], prev[1] = min(prev[0], lo), max(prev[1], hi)
                data[idx] = tuple(sorted((v[0], v[1], v[2], v[3]) for v in hits.values()))
    vs = Viewshed()
    vs.x, vs.y, vs.z, vs.cell, vs.data, vs.entry, vs.wedge, vs.range_m = ex, ey, ez, -1, data, entry, None, range_m
    return vs


def _walk_in(ex, ey, dx, dy, t0, ti, tj, cols, rows):
    """The cells a ray eye + t (dx, dy) crosses from t0 (where it enters the grid through a wall) until it enters cell
    (ti, tj): ({cell index: (t_in, t_out)}, t at the target's entry), or None when it leaves the grid first."""
    length = math.hypot(dx, dy)
    nudge = t0 + 1e-9 / max(length, 1e-9)
    i = min(cols - 1, max(0, int(math.floor((ex + nudge * dx) / CELL))))
    j = min(rows - 1, max(0, int(math.floor((ey + nudge * dy) / CELL))))
    if dx > 0:
        sx, tdx, tmx = 1, CELL / dx, ((i + 1) * CELL - ex) / dx
    elif dx < 0:
        sx, tdx, tmx = -1, -CELL / dx, (i * CELL - ex) / dx
    else:
        sx, tdx, tmx = 0, INF, INF
    if dy > 0:
        sy, tdy, tmy = 1, CELL / dy, ((j + 1) * CELL - ey) / dy
    elif dy < 0:
        sy, tdy, tmy = -1, -CELL / dy, (j * CELL - ey) / dy
    else:
        sy, tdy, tmy = 0, INF, INF
    t = t0
    out = {}
    for _ in range(cols + rows + 4):
        if i == ti and j == tj:
            return out, t
        nxt = tmx if tmx < tmy else tmy
        out[j * cols + i] = (t, nxt)
        if tmx < tmy - 1e-12:
            tmx, i = tmx + tdx, i + sx
        elif tmy < tmx - 1e-12:
            tmy, j = tmy + tdy, j + sy
        else:
            tmx, tmy, i, j = tmx + tdx, tmy + tdy, i + sx, j + sy
        t = nxt
        if not (0 <= i < cols and 0 <= j < rows):
            return None
    return None


def opening_transmission(state, op, geom, vs, eye, p, rel):
    """Fraction of the view from an eye outside an Opening's room (in that room's frame) that reaches point p inside:
    0 unless the segment passes through the opening; else the relation rule (5.4) of p (rel: a _Rel, or None for a
    free point) against the room's occluders after the wall (vs: opening_viewshed of the eye) and its agents."""
    _, ex, ey, ez, ignore = _eye_of(eye)
    if not _through(op, ex, ey, ez, p[0], p[1], p[2]):
        return 0.0
    if rel is None:
        rel = _Rel("point", None, frozenset(ignore), None, None)
    agents = _agents(state, op.room, rel.skip)
    return _sample_transmission(geom, vs, p, rel, agents)


def _eye_into(ap, eye):
    """An eye of the room an Aperture is seen from, as an Eye of the room it looks into (its image outside that
    room's grid)."""
    rid, ex, ey, ez, ignore = _eye_of(eye)
    x, y = from_room(ap, ex, ey)
    return Eye(ap.other, x, y, ez, ignore)


def through_transmission(state, ap, eye, p, rel=None, geom=None, vs=None, cmap=None, world=None):
    """Fraction of the view from an eye in room ap.rid that reaches point p of room ap.other (in ap.other's frame)
    through a doorway Aperture: ap.factor x the transmission in the eye's room from the eye to the opening (its
    viewshed, agents and, for the robot's head camera, its carried load) x the transmission in the other room from the
    opening on (opening_transmission with p's relation rule: rel None for a free point, else the id of the entity,
    person or pet p belongs to). 0 when the segment misses the opening. geom / vs: the eye room's geometry and
    viewshed when the caller has them; world: the World whose geometry memo to use."""
    rid, ex, ey, ez, ignore = _eye_of(eye)
    qx, qy = to_room(ap, p[0], p[1])
    flipped = Opening(rid, -ap.nx, -ap.ny, ap.wall, ap.lo, ap.hi, ap.z0, ap.z1)     # the wall seen from inside
    found = _crossing(flipped, ex, ey, ez, qx, qy, p[2])
    if found is None:
        return 0.0
    t, lateral, z = found
    if not (ap.lo - EPS <= lateral <= ap.hi + EPS and ap.z0 - EPS <= z <= ap.z1 + EPS):
        return 0.0
    if rel is None:
        info = _Rel("point", None, frozenset(ignore), None, None)
    else:
        placed = _place(state, rel, cmap)
        if placed is None or placed[0].rid != ap.other:
            return 0.0
        info = _with_ignore(state, rel, placed[1], ignore)
    geom_b = room_geom(state, ap.other, world)
    eye_b = _eye_into(ap, eye)
    vs_b = opening_viewshed(geom_b, ap.into, eye_b, getattr(eye, "range_m", None))
    t_b = opening_transmission(state, ap.into, geom_b, vs_b, eye_b, p, info)
    if t_b <= 0.0:
        return 0.0
    # the eye's room up to the wall: the point 1 mm short of the crossing on the way from the eye
    s = max(0.0, t - 1e-3 / max(math.hypot(qx - ex, qy - ey), 1e-6))
    cx, cy, cz = ex + s * (qx - ex), ey + s * (qy - ey), ez + s * (p[2] - ez)
    geom = geom or room_geom(state, rid, world)
    vs = vs or viewshed(geom, eye)
    skip = frozenset(ignore)
    t_a = _transmission(geom, vs, ex, ey, ez, cx, cy, cz, skip, None, _occluders(state, eye, skip, cmap))
    return ap.factor * t_a * t_b


def _is_window(rec):
    words = [rec.get("category"), rec.get("type")] + list(rec.get("is_a") or [])
    return any(w == "window" for w in words)


def aperture_opening(state, aperture_id):
    """(Opening, factor) of something a viewer outside can look in through, in the frame of the room it opens into,
    or (None, 0.0): a window (its footprint along its wall from its sill to its top; factor 1 - 0.95 x covered: glass
    passes the view, coverings do not) or a door / connection (into the indoor room of an exterior one, else the
    door's own room; the doorway's opening, factor 1 open, its glass porosity closed, nothing when closed and
    opaque)."""
    entities = state["entities"]
    rec = entities.get(aperture_id)
    if rec is not None and _is_window(rec) and "connects" not in rec:
        rid = rec.get("location")
        room = state["rooms"].get(rid)
        placed = _place(state, aperture_id)
        if room is None or not rec.get("pos") or placed is None:
            return None, 0.0
        box = placed[0]
        inward, _ = geometry.wall_inward(room, rec["pos"])
        lo, hi = (box.y0, box.y1) if inward[0] else (box.x0, box.x1)
        covered = max(0.0, min(1.0, _num(rec.get("covered"), 0.0)))
        return Opening(rid, inward[0], inward[1], _wall_line(rec["pos"], inward), lo, hi, box.z0, box.z1), \
            round(1.0 - COVER_BLOCK * covered, 4)
    cid = aperture_id if aperture_id in (state.get("connections") or {}) else None
    if cid is None and rec is not None:
        cid = next((c for c, conn in sorted((state.get("connections") or {}).items())
                    if conn.get("door") == aperture_id), None)
    if cid is None:
        return None, 0.0
    pair = list(state["connections"][cid].get("rooms") or ())
    if len(pair) != 2:
        return None, 0.0
    rooms = state["rooms"]
    outdoor = [bool((rooms.get(r) or {}).get("outdoor")) for r in pair]
    if outdoor[0] != outdoor[1]:
        into = pair[1] if outdoor[0] else pair[0]
    else:
        door = entities.get(state["connections"][cid].get("door") or "") or {}
        into = door.get("location") if door.get("location") in pair else pair[0]
    other = pair[1] if into == pair[0] else pair[0]
    ap = doorway_aperture(state, cid, other)
    if ap is None:
        return None, 0.0
    return ap.into, ap.factor


def aperture_viewer(state, aperture_id, outside_m=3.0, eye_z=1.6):
    """A viewer standing outside_m outside a window or a glass or open door (aperture_opening), level at eye_z, looking
    straight in (human attention: 120 x 120 degrees, 15 m, 1 arcminute), in the frame of the room it opens into:
    kind "aperture:<id>"; sees_entity and aperture_transmission see through the opening only. None when there is no
    such opening (a closed opaque door, an unplaced window)."""
    op, factor = aperture_opening(state, aperture_id)
    if op is None or factor <= 0.0:
        return None
    mid = (op.lo + op.hi) / 2.0
    out = max(0.0, float(outside_m))
    x, y = (op.wall - op.nx * out, mid) if op.nx else (mid, op.wall - op.ny * out)
    yaw = math.degrees(math.atan2(op.ny, op.nx)) % 360.0
    view = APERTURE_VIEW
    return Viewer(op.room, round(x, 3), round(y, 3), float(eye_z), round(yaw, 2), 0.0, view["hfov_deg"],
                  view["vfov_deg"], view["range_m"], view["focal_px"], "aperture:" + str(aperture_id), ())


def viewer_opening(state, viewer):
    """(Opening, factor) of an aperture viewer (kind "aperture:<id>"), or (None, 0.0) for any other viewer."""
    kind = str(getattr(viewer, "kind", "") or "")
    if not kind.startswith("aperture:"):
        return None, 0.0
    return aperture_opening(state, kind[len("aperture:"):])


def _reflects(state, op, p, light):
    """Glass reflects in daylight (5.8): an outdoor light above REFLECT_RATIO x the light at a point that stands more
    than REFLECT_DEPTH_M inside the wall hides it from a viewer outside."""
    depth = ((p[0] if op.nx else p[1]) - op.wall) * (op.nx or op.ny)
    return depth > REFLECT_DEPTH_M + EPS and clock.daylight(state) > REFLECT_RATIO * light + EPS


def aperture_transmission(state, viewer, p, rel=None, cmap=None, world=None, ignore=()):
    """Fraction of an aperture viewer's view that reaches point p of the room it looks into: in its frame, through
    the opening (opening_transmission with p's relation rule: rel None for a free point, else the id p belongs to;
    ignore: ids that never occlude a free point, e.g. the robot whose own head is p), times what the opening passes,
    and 0 when the glass reflects (_reflects). 0 for any other viewer."""
    op, factor = viewer_opening(state, viewer)
    if op is None or factor <= 0.0 or not in_frame(viewer, p):
        return 0.0
    if rel is None:
        info = _Rel("point", None, frozenset(viewer.ignore or ()) | frozenset(ignore), None, None)
    else:
        placed = _place(state, rel, cmap)
        if placed is None or placed[0].rid != op.room:
            return 0.0
        info = _with_ignore(state, rel, placed[1], viewer.ignore)
    if _reflects(state, op, p, light_at(state, op.room, p)):
        return 0.0
    geom = room_geom(state, op.room, world)
    vs = opening_viewshed(geom, op, viewer, viewer.range_m)
    return factor * opening_transmission(state, op, geom, vs, viewer, p, info)


def aperture_sees(state, viewer, eid, cmap=None, world=None):
    """Visible fraction of an entity, person or pet in an aperture viewer's frame (sees_entity's rule through the
    opening): the in-frame samples' transmissions through it, times what it passes, rounded to a quarter; 0 when its
    middle stands deeper than 1 m behind daylit glass (_reflects) or in another room."""
    op, factor = viewer_opening(state, viewer)
    placed = _place(state, eid, cmap)
    if op is None or factor <= 0.0 or placed is None or placed[0].rid != op.room:
        return 0.0
    box, rel = placed
    centre = ((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, (box.z0 + box.z1) / 2.0)
    if _reflects(state, op, centre, light_at(state, op.room, centre)):
        return 0.0
    rel = _with_ignore(state, eid, rel, viewer.ignore or ())
    geom = room_geom(state, op.room, world)
    vs = opening_viewshed(geom, op, viewer, viewer.range_m)
    points = _samples(state, eid, box, rel, viewer)
    total = 0.0
    for p in points:
        if in_frame(viewer, p):
            total += opening_transmission(state, op, geom, vs, viewer, p, rel)
    return quarter(factor * total / len(points)) if points else 0.0


# ----- light -------------------------------------------------------------------------------------------------------------
def make_beam(origin, yaw_deg, tilt_deg, power, half_deg):
    """A light beam from origin (x, y, z) along (yaw, tilt) with power and half-angle (6.1)."""
    yaw, tilt = math.radians(yaw_deg), math.radians(tilt_deg)
    return Beam(origin[0], origin[1], origin[2], math.cos(tilt) * math.cos(yaw), math.cos(tilt) * math.sin(yaw),
                math.sin(tilt), float(power), float(half_deg))


def beam_light(beam, p):
    """Light a beam adds at p: power / max(r, 0.2)^2 within its half-angle, else 0 (no shadows)."""
    vx, vy, vz = p[0] - beam.x, p[1] - beam.y, p[2] - beam.z
    r = math.sqrt(vx * vx + vy * vy + vz * vz)
    if r > 1e-9 and vx * beam.ux + vy * beam.uy + vz * beam.uz < math.cos(math.radians(beam.half_deg)) * r - EPS:
        return 0.0
    return beam.power / max(r, BEAM_MIN_R) ** 2


def robot_beams(state, eye, yaw_deg, tilt_deg):
    """The robot's lit beams for a head pose: a held, lit flashlight along the head aim (its view.beam or 1.8 / 15
    degrees) and the headlamp along the head pose (0.45 / 30 degrees). eye: (x, y, z) of the head camera."""
    out = []
    entities = state["entities"]
    for eid in sorted(set(v for v in (state["robot"].get("hands") or {}).values() if v)):
        rec = entities.get(eid) or {}
        if rec.get("category") == "flashlight" and rec.get("on"):
            spec = view_props(state, eid).get("beam") or BEAMS["flashlight"]
            out.append(make_beam(eye, yaw_deg, tilt_deg, _num(spec.get("power"), BEAMS["flashlight"]["power"]),
                                 _num(spec.get("half_deg"), BEAMS["flashlight"]["half_deg"])))
    if state["robot"].get("headlamp_on"):
        spec = _worn_headlamp_beam(state) or BEAMS["headlamp"]
        out.append(make_beam(eye, yaw_deg, tilt_deg, _num(spec.get("power"), BEAMS["headlamp"]["power"]),
                             _num(spec.get("half_deg"), BEAMS["headlamp"]["half_deg"])))
    return out


def _worn_headlamp_beam(state):
    """The view.beam of the headlamp the robot wears (navigation_robot's robot_headlamp: location robot, relation worn,
    gear_kind headlamp), or None (then BEAMS["headlamp"]): a data change to the worn lamp's beam takes effect
    (navigation_robot's adoption request 4). Scanned only while the headlamp is on."""
    found = None
    for eid, rec in state["entities"].items():
        if rec.get("location") == "robot" and rec.get("relation") == "worn" and rec.get("gear_kind") == "headlamp" \
                and (found is None or eid < found):
            found = eid
    beam = view_props(state, found).get("beam") if found is not None else None
    return beam if isinstance(beam, dict) else None


def _ambient(state, rid):
    from .perception import light_level
    return light_level(state, rid)


def _powered(state, rec):
    return (rec.get("powered", True) and state.get("utilities", {}).get("power", True)) or rec.get("battery_powered")


def _shade(state, geom, p):
    """Shading factor of a point (6.1): inside a container (1.0 when it has an interior light, is open and powered),
    in a behind gap, under a covering span, else 1.0 (the most specific region wins)."""
    for x0, y0, x1, y1, z0, z1, kind, _, cid in geom.shade[geom.cell_index(p[0], p[1])]:
        if not (x0 - 1e-9 <= p[0] <= x1 + 1e-9 and y0 - 1e-9 <= p[1] <= y1 + 1e-9 and z0 - 1e-9 <= p[2] < z1):
            continue
        if kind == "inside":
            rec = state["entities"].get(cid) or {}
            lit = view_props(state, cid).get("interior_light") and rec.get("open") and _powered(state, rec)
            return SHADE["inside_lit"] if lit else SHADE["inside"]
        return SHADE[kind]
    return 1.0


def light_at(state, rid, p, beams=(), ambient=None, geom=None):
    """Light at a point (6.1): room ambient (perception.light_level, or `ambient` when the caller has it) x the shade
    factor, plus @hook("light_at") fn(state, rid, x, y, z) boosts and the beams, clamped to [0, 1]. A light_at hook
    answers a boost (a number >= 0, or None): local light on top of the uniform ambient (sun patches, lamp pools)."""
    level = (_ambient(state, rid) if ambient is None else float(ambient)) * \
        _shade(state, geom or room_geom(state, rid), p)
    for extra in run_hooks("light_at", state, rid, p[0], p[1], p[2]):
        if isinstance(extra, (int, float)):
            level += extra
    for beam in beams:
        level += beam_light(beam, p)
    return round(max(0.0, min(1.0, level)), 3)


LIGHT_SATURATED = 0.36      # k_light is exactly 1 (0.35 and a margin for float rounding) and every light bar is met


def acuity_light(state, rid, p, beams=(), ambient=None, geom=None):
    """light_at for a judgement that reads the light only through k_light and the light bars of acuity_level and the
    capture rule (no contrast, no reading): light_at itself, except that when the light without the light_at hooks
    already reaches LIGHT_SATURATED the hooks are not asked -- their answers are boosts (>= 0), so the level, the
    pixels and every bar come out the same. It spares the domains' per-point light hooks (sun patches, lamp pools)
    in the camera's per-substep watch whenever the room is well lit (TIER1_INTEGRATION 3.4)."""
    level = (_ambient(state, rid) if ambient is None else float(ambient)) * \
        _shade(state, geom or room_geom(state, rid), p)
    for beam in beams:
        level += beam_light(beam, p)
    level = round(max(0.0, min(1.0, level)), 3)
    if level >= LIGHT_SATURATED:
        return level            # the hooks could only add: light_at >= this (float sums and rounding are monotone)
    return light_at(state, rid, p, beams, ambient, geom)


def bright_sources(state, rid):
    """Bright sources of a room: [{"x", "y", "z", "radius_m", "level", "kind", "id"}] from @hook("bright_sources")
    fn(state, rid); when no hook answers, uncovered windows (daylight x (1 - covered), half the window wide, on the
    wall at mid-height) and lit lights (brightness x dim_level at their centre)."""
    answers = [r for r in run_hooks("bright_sources", state, rid) if r is not None]
    if answers:
        return [dict(s) for r in answers for s in r]
    out = []
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    daylight = clock.daylight(state)
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        category = rec.get("category")
        if category not in ("window", "light") or root_room(state, eid) != rid:
            continue
        placed = _place(state, eid)
        if placed is None:
            continue
        box = placed[0]
        cx, cy, cz = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, (box.z0 + box.z1) / 2.0
        if category == "window":
            level = daylight * (1.0 - _num(rec.get("covered"), 0.0))
            if level <= 0.0 or not rec.get("pos"):
                continue
            inward, _ = geometry.wall_inward(room, rec["pos"])
            if inward[0]:
                cx = 0.0 if inward[0] > 0 else cols * CELL
            else:
                cy = 0.0 if inward[1] > 0 else rows * CELL
            out.append({"x": _r3(cx), "y": _r3(cy), "z": _r3(cz), "radius_m": _r3(max(_dims(state, eid, rec)[:2]) / 2.0),
                        "level": round(level, 3), "kind": "window", "id": eid})
        elif rec.get("on") and not rec.get("burnt_out") and _powered(state, rec):
            level = _num(rec.get("brightness"), 0.8) * _num(rec.get("dim_level"), 1.0)
            out.append({"x": _r3(cx), "y": _r3(cy), "z": _r3(cz), "radius_m": _r3(max(box.x1 - box.x0, box.y1 - box.y0) / 2.0),
                        "level": round(level, 3), "kind": "light", "id": eid})
    return out


def _unit(x, y, z):
    n = math.sqrt(x * x + y * y + z * z)
    return (x / n, y / n, z / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def _angle_deg(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))))


def glare(state, viewer, p, radius_m=0.0, light=None, sources=None):
    """Directional glare at a target point (6.2): "sun" (the sun, from @hook("sun_beam") at the eye, within hfov/2 +
    10 degrees, + 20 x lens_scratch for the robot's cameras, of the camera axis), "sun_cone" (the target within 20
    degrees of the sun while the sun is in frame) and "backlit" (a source of level >= 0.5 behind the target overlapping
    it by angular radius, with the target's light <= 0.6 x level). radius_m: the target's radius; light: its light
    (computed when None); sources: the room's bright_sources when the caller already has them."""
    out = {"sun": False, "sun_cone": False, "backlit": False}
    eye = (viewer.x, viewer.y, viewer.z)
    to_p = (p[0] - eye[0], p[1] - eye[1], p[2] - eye[2])
    dist_p = math.sqrt(sum(v * v for v in to_p))
    dir_p = _unit(*to_p)
    sun = next((v for v in run_hooks("sun_beam", state, viewer.rid, eye[0], eye[1], eye[2]) if v), None)
    if sun is not None:
        sun = _unit(*sun)
        yaw, tilt = math.radians(viewer.yaw), math.radians(viewer.tilt)
        axis = (math.cos(tilt) * math.cos(yaw), math.cos(tilt) * math.sin(yaw), math.sin(tilt))
        scratch = _num(state["robot"].get("lens_scratch"), 0.0) if viewer.kind in ("head", "wrist") else 0.0
        if _angle_deg(axis, sun) <= viewer.hfov / 2.0 + SUN_FRAME_MARGIN_DEG + SUN_SCRATCH_DEG * scratch:
            out["sun"] = True
            out["sun_cone"] = _angle_deg(dir_p, sun) <= SUN_CONE_DEG
    if light is None:
        light = light_at(state, viewer.rid, p)
    for src in bright_sources(state, viewer.rid) if sources is None else sources:
        level = _num(src.get("level"))
        if level < BACKLIGHT_LEVEL or light > BACKLIGHT_RATIO * level + EPS:
            continue
        to_s = (src["x"] - eye[0], src["y"] - eye[1], src["z"] - eye[2])
        dist_s = math.sqrt(sum(v * v for v in to_s))
        if dist_s <= dist_p:
            continue
        reach = math.degrees(math.atan2(_num(src.get("radius_m")), dist_s)) + \
            math.degrees(math.atan2(float(radius_m), max(dist_p, 1e-6)))
        if _angle_deg(_unit(*to_s), dir_p) <= reach:
            out["backlit"] = True
            break
    return out


def _face_normal(state, eid, face):
    """Unit world normal of a named face ("up", "down", or a local yaw relative to the front), or None for "all"."""
    if face == "up":
        return (0.0, 0.0, 1.0)
    if face == "down":
        return (0.0, 0.0, -1.0)
    if face == "all" or face is None:
        return None
    rad = math.radians(front_deg(state, eid) + _num(face))
    return (math.cos(rad), math.sin(rad), 0.0)


def _faces(state, eid):
    """{name: spec} of an entity's faces: view.faces plus its label_facing as the "label" face."""
    faces = dict(view_props(state, eid).get("faces") or {})
    rec = state["entities"].get(eid) or {}
    facing = rec.get("label_facing")
    if facing in LABEL_FACING and "label" not in faces:
        faces["label"] = LABEL_FACING[facing]
    return faces


def specular(state, viewer, eid, face=None, sources=None, cmap=None):
    """True if a glossy entity mirrors a bright source into the viewer (6.2): the view ray reflected about the face
    normal passes within 10 degrees (plus the source's angular radius) of a source in front of the face. face: a face
    name; default the "label" face, else "up" for flat things (height <= 0.3 x width), else the front. sources: the
    room's bright_sources when the caller already has them."""
    if not view_props(state, eid).get("glossy"):
        return False
    placed = _place(state, eid, cmap)
    if placed is None or placed[0].rid != viewer.rid:
        return False
    box = placed[0]
    if face is None:
        faces = _faces(state, eid)
        if "label" in faces:
            face = faces["label"]
        else:
            flat = box.z1 - box.z0 <= 0.3 * max(box.x1 - box.x0, box.y1 - box.y0)
            face = "up" if flat else 0.0
    elif face in _faces(state, eid):
        face = _faces(state, eid)[face]
    normal = _face_normal(state, eid, face)
    if normal is None:
        return False
    point = ((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, box.z1 if face == "up" else (box.z0 + box.z1) / 2.0)
    v = _unit(point[0] - viewer.x, point[1] - viewer.y, point[2] - viewer.z)
    dot = v[0] * normal[0] + v[1] * normal[1] + v[2] * normal[2]
    if dot >= 0.0:
        return False        # the viewer is behind the face
    r = (v[0] - 2 * dot * normal[0], v[1] - 2 * dot * normal[1], v[2] - 2 * dot * normal[2])
    for src in bright_sources(state, viewer.rid) if sources is None else sources:
        to_s = (src["x"] - point[0], src["y"] - point[1], src["z"] - point[2])
        dist = math.sqrt(sum(c * c for c in to_s))
        if dist < 1e-6 or sum(a * b for a, b in zip(to_s, normal)) <= 0.0:
            continue
        reach = SPECULAR_DEG + math.degrees(math.atan2(_num(src.get("radius_m")), dist))
        if _angle_deg(r, _unit(*to_s)) <= reach:
            return True
    return False


# ----- faces and text ---------------------------------------------------------------------------------------------------
def faces_seen(state, viewer, eid, cmap=None):
    """Names of the declared faces of an entity a viewer's position sees (4.6; orientation only, line of sight is the
    caller's): "all" always; "up" from more than 0.05 m above the top unless things on it cover half its top; "down"
    never while it rests on something; a side face within 80 degrees of the direction to the eye, unless it is within
    0.05 m of a wall or a neighbour."""
    faces = _faces(state, eid)
    placed = _place(state, eid, cmap)
    if not faces or placed is None:
        return []
    box, rel = placed
    rec = state["entities"].get(eid) or {}
    cx, cy, cz = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, (box.z0 + box.z1) / 2.0
    out = []
    for name, spec in sorted(faces.items()):
        if spec == "all":
            out.append(name)
        elif spec == "up":
            if viewer.z > box.z1 + FACE_UP_MARGIN_M and not _top_covered(state, eid, box, cmap):
                out.append(name)
        elif spec == "down":
            if rel.kind in ("hung", "held", "carried") and viewer.z < box.z0 - FACE_UP_MARGIN_M:
                out.append(name)
        else:
            normal = _face_normal(state, eid, spec)
            v = (viewer.x - cx, viewer.y - cy, viewer.z - cz)
            dist = math.sqrt(sum(c * c for c in v))
            if dist > 1e-9 and (v[0] * normal[0] + v[1] * normal[1]) / dist > FACE_SEEN_COS and \
                    not _face_blocked(state, eid, rec, box, normal, cmap):
                out.append(name)
    return out


def _top_covered(state, eid, box, cmap):
    area = max(1e-9, (box.x1 - box.x0) * (box.y1 - box.y0))
    covered = 0.0
    for child in _children(state, eid, cmap):
        if state["entities"][child].get("relation", "on") == "on":
            cbox = entity_box(state, child, cmap)
            if cbox is not None:
                covered += max(0.0, min(cbox.x1, box.x1) - max(cbox.x0, box.x0)) * \
                    max(0.0, min(cbox.y1, box.y1) - max(cbox.y0, box.y0))
    return covered >= FACE_COVER_FRAC * area - EPS


def _face_blocked(state, eid, rec, box, normal, cmap):
    """A side face against a wall or a neighbour within 0.05 m (axis-aligned normals only)."""
    nx, ny = round(normal[0]), round(normal[1])
    if abs(normal[0] - nx) > 0.02 or abs(normal[1] - ny) > 0.02:
        return False
    room = state["rooms"][box.rid]
    cols, rows = geometry.grid_size(room)
    if (nx > 0 and box.x1 >= cols * CELL - FACE_BLOCK_M) or (nx < 0 and box.x0 <= FACE_BLOCK_M) or \
            (ny > 0 and box.y1 >= rows * CELL - FACE_BLOCK_M) or (ny < 0 and box.y0 <= FACE_BLOCK_M):
        return True
    if nx > 0:
        strip = (box.x1, box.y0, box.x1 + FACE_BLOCK_M, box.y1)
    elif nx < 0:
        strip = (box.x0 - FACE_BLOCK_M, box.y0, box.x0, box.y1)
    elif ny > 0:
        strip = (box.x0, box.y1, box.x1, box.y1 + FACE_BLOCK_M)
    else:
        strip = (box.x0, box.y0 - FACE_BLOCK_M, box.x1, box.y0)
    for sib in _children(state, rec.get("location"), cmap):
        if sib == eid or state["entities"][sib].get("relation", "on") != rec.get("relation", "on"):
            continue
        sbox = entity_box(state, sib, cmap)
        if sbox is not None and sbox.x0 < strip[2] - 1e-9 and sbox.x1 > strip[0] + 1e-9 and \
                sbox.y0 < strip[3] - 1e-9 and sbox.y1 > strip[1] + 1e-9 and \
                sbox.z0 < box.z1 - 1e-9 and sbox.z1 > box.z0 + 1e-9:
            return True
    return False


def gated_face(state, eid, field):
    """The face a read-tier field needs to be seen through (4.6): the type perception block's {"faces": {field:
    face}} (nearest type on the is_a chain), else "label" for label_text / recipient / address / barcode / order_id
    when the entity has a label face (or label_facing); None means no gating (wrap-around text)."""
    rec = state["entities"].get(eid) or {}
    reg = default_registry()
    for name in [rec.get("type")] + list(rec.get("is_a") or []):
        mapping = (reg.perception.get(name) or {}).get("faces")
        if isinstance(mapping, dict) and field in mapping:
            return mapping[field]
    if field in LABEL_GATED_FIELDS and "label" in _faces(state, eid):
        return "label"
    return None


def text_m(state, eid, field):
    """Character height (m) of a read-tier field: the entity's view.text_m, else TEXT_M by field; None for a field
    that is not text."""
    own = (view_props(state, eid).get("text_m") or {}).get(field)
    return _num(own) if own is not None else TEXT_M.get(field)


def read_range_m(text_height_m, focal=None):
    """Farthest distance at which characters text_height_m tall cover 10 px with a lens of `focal` px (default the
    head camera)."""
    return (HW["head"]["focal_px"] if focal is None else float(focal)) * float(text_height_m) / PX["read_char"]


# ----- acuity (section 7) -----------------------------------------------------------------------------------------------
def px_on_target(size_m, distance_m, focal):
    """Pixels across a target of size_m (largest dimension, face or character height) at a 3D distance."""
    return float(size_m) * float(focal) / max(float(distance_m), MIN_DIST_M)


def k_lens(camera_clean):
    """Lens factor 0.25 + 0.75 x camera_clean^2 (camera_clean already includes scratches)."""
    return 0.25 + 0.75 * max(0.0, min(1.0, float(camera_clean))) ** 2


def k_light(light):
    """Exposure factor clamp((L - 0.05) / 0.30, 0, 1)^0.5."""
    return max(0.0, min(1.0, (float(light) - 0.05) / 0.30)) ** 0.5


def k_motion(speed=None):
    """Motion-blur factor: 1 for stationary frames, else K_MOTION by walking speed."""
    return 1.0 if speed is None else K_MOTION.get(speed, K_MOTION["normal"])


def k_haze(room, distance_m):
    """Haze factor exp(-(0.6 smoke + 0.4 steam + 0.3 fog) x d) from the room's smoke (or smoke_density), steam and
    fog levels (booleans count as 1)."""
    def level(*names):
        for name in names:
            if name in room:
                value = room[name]
                return 1.0 if value is True else max(0.0, _num(value, 0.0))
        return 0.0
    density = 0.6 * level("smoke", "smoke_density") + 0.4 * level("steam") + 0.3 * level("fog")
    return math.exp(-density * max(0.0, float(distance_m)))


def background_color(state, eid):
    """Colour behind an entity: its support's colour, else the room's floor_color, else "dark" on a dark floor
    material, else None."""
    rec = state["entities"].get(eid) or {}
    parent = state["entities"].get(rec.get("location"))
    if parent is not None and parent.get("color"):
        return parent["color"]
    rid = root_room(state, eid)
    room = state["rooms"].get(rid) or {}
    if room.get("floor_color"):
        return room["floor_color"]
    return "dark" if room.get("floor_material") in DARK_FLOOR_MATERIALS else None


def agent_background_color(state, agent):
    """Colour behind a person or pet: the furniture they are on (on / seat / bed / in_bed naming an entity with a
    colour), else their room's floor_color, else "dark" on a dark floor material, else None."""
    for key in ("on", "seat", "bed", "in_bed"):
        ref = agent.get(key)
        if isinstance(ref, str) and (state["entities"].get(ref) or {}).get("color"):
            return state["entities"][ref]["color"]
    room = state["rooms"].get(agent.get("location")) or {}
    if room.get("floor_color"):
        return room["floor_color"]
    return "dark" if room.get("floor_material") in DARK_FLOOR_MATERIALS else None


def k_contrast(target_color, back_color, light):
    """0.5 when a dark target sits on a dark background in light below 0.45 (a black cable on a slate floor, a dark
    cat at dusk), else 1."""
    dark = DARK_COLORS | {"dark"}
    if light < LIGHT["contrast"] and str(target_color or "").lower() in dark and str(back_color or "").lower() in dark:
        return 0.5
    return 1.0


def effective_px(px, visible=1.0, lens=1.0, light=1.0, motion=1.0, haze=1.0, contrast=1.0):
    """px x k_lens x k_light x k_motion x k_haze x k_contrast x sqrt(visible fraction)."""
    return float(px) * lens * light * motion * haze * contrast * math.sqrt(max(0.0, float(visible)))


def acuity_level(px_eff, light, sun_cone=False, backlit=False, detect_px=None):
    """Best level a target reaches (section 7): "identify" (>= 64 px, light >= 0.2, not backlit or in the sun cone),
    "recognize" (>= 32 px, light >= 0.12, not in the sun cone), "detect" (>= detect_px, light >= 0.05) or None.
    detect_px: 10 (the start of the probabilistic band, the default), 17 for oracle geometric mode, 24 for sure."""
    detect_px = PX["detect_min"] if detect_px is None else float(detect_px)
    if px_eff >= PX["identify"] and light >= LIGHT["identify"] and not backlit and not sun_cone:
        return "identify"
    if px_eff >= PX["recognize"] and light >= LIGHT["recognize"] and not sun_cone:
        return "recognize"
    if px_eff >= detect_px and light >= LIGHT["detect"]:
        return "detect"
    return None


def detect_probability(px_eff, clutter=0):
    """Chance of detecting a target in the 10-24 px band (11.2): (px - 10) / 14 - 0.05 x max(0, clutter - 3), in
    [0, 1]; clutter = siblings on the same support."""
    return max(0.0, min(1.0, (float(px_eff) - PX["detect_min"]) / (PX["detect_sure"] - PX["detect_min"])
                        - 0.05 * max(0, int(clutter) - 3)))


def inspect_capable(px_eff, light):
    """True if the head camera resolves a target well enough to inspect it (>= 160 px at light >= 0.2)."""
    return px_eff >= PX["inspect"] and light >= LIGHT["inspect"]


def ir_shape(size_m, distance_m, rel_bearing_deg, light):
    """True if the depth camera's active IR shows a dark shape (C11): light < 0.05, size >= 0.4 m, within 4 m inside
    the 87-degree depth field and >= 24 depth px."""
    depth = HW["depth"]
    return light < LIGHT["detect"] and size_m >= SIZE_IR_MIN_M - EPS and distance_m <= depth["ir_range_m"] + EPS \
        and abs(float(rel_bearing_deg)) <= depth["hfov_deg"] / 2.0 + EPS \
        and px_on_target(size_m, distance_m, depth["focal_px"]) >= PX["depth_shape"]


def readable(text_height_m, distance_m, focal, light, camera_clean=1.0, motion=1.0):
    """True if text of character height text_height_m is readable (section 7): >= 10 px per character after the lens
    and motion factors, light >= 0.25 and camera_clean >= 0.4 (face and glare are the caller's)."""
    char = px_on_target(text_height_m, distance_m, focal) * k_lens(camera_clean) * motion
    return char >= PX["read_char"] - EPS and light >= LIGHT["read"] and camera_clean >= READ_MIN_CLEAN


# ----- carried loads (5.6) ----------------------------------------------------------------------------------------------
def carried_occluder(state):
    """The robot's carried load as a view occluder (5.6), or None: a thin adapter over phys.support.held_envelope
    (its blocks_view is the camera rule: held in both hands and at least 0.35 x 0.25 m across and 0.25 m tall, or
    declaring blocks_view; never a tray carried level). {"items", "z_bottom", "z_top", "x_front", "half_w",
    "half_angle_deg", "floor_hidden_m"}: the load's front is x_front ahead of the body axis and half_w to each side;
    floor_hidden_m is how far ahead the floor is hidden from the current eye height (None: all of it)."""
    env = _held_envelope(state)
    if not env or not env.get("blocks_view"):
        return None
    half_w = env["width_m"] / 2.0
    eye_z = eye_height_m(state)
    top, front = env["top_m"], env["front_m"]
    return {"items": list(env["items"]), "z_bottom": env["bottom_m"], "z_top": top, "x_front": front,
            "half_w": round(half_w, 3), "half_angle_deg": round(math.degrees(math.atan2(half_w, front)), 2),
            "floor_hidden_m": round(eye_z * front / (eye_z - top), 3) if top < eye_z else None}


def carried_hides(occ, eye_z, rel_bearing_deg, distance_m, z):
    """True if a carried occluder hides a point at a body-relative bearing, horizontal distance and height: within its
    half-angle and below the line from the eye over the load's top front edge."""
    if occ is None or abs(float(rel_bearing_deg)) > occ["half_angle_deg"] + EPS:
        return False
    d = max(float(distance_m), 1e-6)
    return (float(z) - eye_z) / d <= (occ["z_top"] - eye_z) / occ["x_front"] + EPS


# ----- lidar (5.7) --------------------------------------------------------------------------------------------------------
def lidar_ring(state, eye, facing_deg, geom=None):
    """Free distance per 15-degree sector (sector 0 straight ahead, then clockwise) at 0.25 m: to the first solid span
    covering that height, an agent at least that tall, or the room wall (open doorways let the beam out), rounded to
    0.25 m; None when nothing is within 8 m. One ray per sector at its centre; independent of light and privacy."""
    rid, ex, ey, _, ignore = _eye_of(eye)
    lid = HW["lidar"]
    geom = geom or room_geom(state, rid)
    agent_cells = set()
    for x0, y0, _, _, top, _, _ in _agents(state, rid, frozenset(ignore) | {"robot"}):
        if top >= lid["height_m"]:
            agent_cells.add((int(round(x0 / CELL)), int(round(y0 / CELL))))
    openings = set()
    for _, conn in sorted(state.get("connections", {}).items()):
        cell = conn.get("cells", {}).get(rid)
        door = state["entities"].get(conn.get("door") or "")
        if cell is not None and (door is None or door.get("open")):
            openings.add((int(cell[0]), int(cell[1])))
    out = []
    for k in range(lid["sectors"]):
        rad = math.radians(float(facing_deg) + k * 360.0 / lid["sectors"])
        ux, uy = math.cos(rad), math.sin(rad)
        hit = _lidar_ray(geom, ex, ey, ux, uy, lid["height_m"], lid["range_m"], agent_cells, openings)
        for x0, y0, x1, y1, z_lo, z_hi, porosity, src in geom.leaves:      # thin occluders: exact (RoomGeom.leaves)
            if porosity < 0.5 and src not in ignore and z_lo - 1e-9 <= lid["height_m"] <= z_hi + 1e-9:
                span = _ray_rect(ex, ey, ux, uy, x0, y0, x1, y1)
                if span is not None and span[1] > 0.0:
                    hit = min(hit, max(0.0, span[0]))
        out.append(None if hit > lid["range_m"] + EPS else math.floor(hit / lid["step_m"] + 0.5) * lid["step_m"])
    return out


def _lidar_ray(geom, ex, ey, ux, uy, height, range_m, agent_cells, openings):
    """Distance along a horizontal ray to the first solid span covering `height` (exact entry into its box), agent
    cell or wall; INF when it leaves through an open doorway cell or passes range_m."""
    cols, rows = geom.cols, geom.rows
    i = min(cols - 1, max(0, int(math.floor(ex / CELL))))
    j = min(rows - 1, max(0, int(math.floor(ey / CELL))))
    if ux > 1e-12:
        sx, tdx, tmx = 1, CELL / ux, ((i + 1) * CELL - ex) / ux
    elif ux < -1e-12:
        sx, tdx, tmx = -1, -CELL / ux, (i * CELL - ex) / ux
    else:
        sx, tdx, tmx = 0, INF, INF
    if uy > 1e-12:
        sy, tdy, tmy = 1, CELL / uy, ((j + 1) * CELL - ey) / uy
    elif uy < -1e-12:
        sy, tdy, tmy = -1, -CELL / uy, (j * CELL - ey) / uy
    else:
        sy, tdy, tmy = 0, INF, INF
    while True:
        last = (i, j)
        if tmx <= tmy:
            t, tmx, i = tmx, tmx + tdx, i + sx
        else:
            t, tmy, j = tmy, tmy + tdy, j + sy
        if t > range_m:
            return INF
        if not (0 <= i < cols and 0 <= j < rows):
            return INF if last in openings else t
        best = t if (i, j) in agent_cells else INF
        for z_lo, z_hi, porosity, _, key in geom.spans[j * cols + i]:
            if porosity < 0.5 and z_lo - 1e-9 <= height <= z_hi + 1e-9:
                x0, y0, x1, y1 = geom.rects[key]
                span = _ray_rect(ex, ey, ux, uy, x0, y0, x1, y1)
                if span is not None:
                    best = min(best, max(span[0], t))
        if best < INF:
            return best


# ----- viewpoints ---------------------------------------------------------------------------------------------------------
def best_viewpoints(state, rid, eid, k=3, eye_z=None):
    """Up to k free floor cells of a room, nearest by walking cost from the robot (else from the room's doorways),
    from which an eye at eye_z (default: the robot's standing eye height) sees the entity: visible fraction >= 0.25
    and at least 24 px across with the head camera (a sure detection in good light)."""
    placed = _place(state, eid)
    if placed is None or placed[0].rid != rid:
        return []
    box = placed[0]
    eye_z = eye_height_m(state, "standing") if eye_z is None else float(eye_z)
    geom = room_geom(state, rid)
    room = state["rooms"][rid]
    blocked = set(geometry.blocked_cells(state, rid))
    robot = state["robot"]
    if robot.get("room") == rid and robot.get("pos"):
        starts = [tuple(robot["pos"])]
        blocked.discard(starts[0])
    else:
        starts = [c for c in geometry.doorway_cells(state, rid) if c not in blocked]
    cost = _walk_costs(room, blocked, starts)
    size = max(box.x1 - box.x0, box.y1 - box.y0, box.z1 - box.z0)
    tx, ty, tz = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, (box.z0 + box.z1) / 2.0
    out = []
    for c, r in sorted(cost, key=lambda cell: (cost[cell], cell[1], cell[0])):
        ex, ey = (c + 0.5) * CELL, (r + 0.5) * CELL
        dist = math.sqrt((tx - ex) ** 2 + (ty - ey) ** 2 + (tz - eye_z) ** 2)
        if px_on_target(size, dist, HW["head"]["focal_px"]) < PX["detect_sure"]:
            continue
        eye = Eye(rid, ex, ey, eye_z, ("robot",))
        half = math.degrees(math.atan2(size, max(math.hypot(tx - ex, ty - ey), 1e-6)))
        vs = viewshed(geom, eye, (math.degrees(math.atan2(ty - ey, tx - ex)), half))
        if visible_fraction(state, eid, eye, geom, vs)[0] >= 0.25:
            out.append((c, r))
            if len(out) >= k:
                break
    return out


def _walk_costs(room, blocked, starts):
    """Dijkstra walking cost (8-connected, no corner cutting, diagonal 1.4142) from the start cells over free cells."""
    cost = {}
    frontier = [(0.0, tuple(s)) for s in starts if geometry.in_grid(room, tuple(s))]
    heapq.heapify(frontier)
    while frontier:
        g, cell = heapq.heappop(frontier)
        if cell in cost:
            continue
        cost[cell] = round(g, 4)
        for dx, dy, step in geometry.NEIGHBORS:
            nxt = (cell[0] + dx, cell[1] + dy)
            if nxt in cost or nxt in blocked or not geometry.in_grid(room, nxt):
                continue
            if dx and dy and ((cell[0] + dx, cell[1]) in blocked or (cell[0], cell[1] + dy) in blocked):
                continue
            heapq.heappush(frontier, (g + step, nxt))
    return cost
