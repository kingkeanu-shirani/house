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

Stage A bridges. Eye heights come from body.eye_height_m and the carried load from phys.support.held_envelope when
those modules exist; until then local copies of the frozen contracts (TIER1_INTEGRATION 1.2 and 1.6) stand in.

Fidelity: G for line of sight and occlusion at cell-and-box resolution; P-approx for light, glare and acuity (closed
forms, not calibrated against a real detector). Caches are keyed by content and never change results.
"""
import collections
import hashlib
import heapq
import importlib
import json
import math
import weakref

from . import clock, geometry
from .plugins import run_hooks
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

# Head-camera eye height by body profile and posture key (TIER1_INTEGRATION 1.2: a head camera at 94 % of a 1.6 m
# humanoid's height; "kneeling" is the low look-under posture). Stands in for body.eye_height_m until kernel/body.py
# lands. TIER1-STUB(CAM-RUNTIME): drop with the bridge once body.eye_height_m exists.
EYE_HEIGHT_M = {
    "humanoid_v1": {"standing": 1.50, "crouching": 0.95, "kneeling": 0.60, "sitting": 0.95, "lying": 0.30},
    "humanoid_compact": {"standing": 1.20, "crouching": 0.75, "kneeling": 0.50, "sitting": 0.75, "lying": 0.25},
    "mobile_manipulator": {"standing": 1.30, "crouching": 0.90, "kneeling": 0.90, "sitting": 0.90, "lying": 0.30},
}
AID_STANDING_M = 0.4        # step-stool top when the aid record has no standing_height_m (TIER1_INTEGRATION 1.2)
# humanoid_v1 carry geometry (TIER1_INTEGRATION 1.2 / 1.6): one-hand loads hang centred on a 1.0 m hand, two-hand
# loads sit on the hips at 0.85 m, 0.35 m in front of the body axis. Stands in for phys.support.held_envelope.
# TIER1-STUB(CAM-RUNTIME): drop with the bridge once phys.support.held_envelope exists.
CARRY = {"hand_z_m": 1.0, "hip_z_m": 0.85, "front_m": 0.35}
# A carried load hides the view when held in both hands and at least 0.35 x 0.25 m across and 0.25 m tall (5.6;
# navigation's and manipulation's heuristics, unified); trays carried level are excluded.
CARRIED_MIN_M = {"wide": 0.35, "deep": 0.25, "tall": 0.25}

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
# how a point relates to the geometry around it: kind, the ids that never occlude it, its support (x0, y0, x1, y1,
# spans) tested exactly, and for things in a container its _Container and its neighbours' boxes
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


_BRIDGES = {}


def _bridge(module, attr):
    """attr of house_sim.kernel.<module> when that stage-A sibling exists, else None (cached per process)."""
    key = module + "." + attr
    if key not in _BRIDGES:
        try:
            found = getattr(importlib.import_module("." + module, __package__), attr, None)
        except ImportError:
            found = None
        _BRIDGES[key] = found
    return _BRIDGES[key]


def _eye_of(eye):
    """(rid, x, y, z, ignore) of an Eye, a Viewer or a plain (rid, x, y, z[, ignore]) sequence."""
    if hasattr(eye, "rid"):
        return eye.rid, float(eye.x), float(eye.y), float(eye.z), tuple(eye.ignore or ())
    return eye[0], float(eye[1]), float(eye[2]), float(eye[3]), tuple(eye[4]) if len(eye) > 4 else ()


def _robot_viewer(ignore):
    return "robot" in ignore


# ----- body and physics bridges ----------------------------------------------------------------------------------------
def eye_height_m(state, posture=None):
    """Height of the robot's head camera above its floor: the body profile's eye height for the posture ("lying" when
    fallen) plus the standing height of the aid it stands on. posture: the height for that posture on the floor
    instead of the current one. Delegates to body.eye_height_m when kernel/body.py exists."""
    robot = state["robot"]
    if posture is not None:
        robot = dict(robot, posture=posture, fallen=False, standing_on=None)
        state = dict(state, robot=robot)
    fn = _bridge("body", "eye_height_m")
    if fn is not None:
        return round(float(fn(state)), 3)
    table = EYE_HEIGHT_M.get(robot.get("body_profile") or "humanoid_v1", EYE_HEIGHT_M["humanoid_v1"])
    key = "lying" if robot.get("fallen") else (robot.get("posture") or "standing")
    z = table.get(key, table["standing"])
    aid = robot.get("standing_on")
    if aid:
        z += _num((state["entities"].get(aid) or {}).get("standing_height_m"), AID_STANDING_M)
    return round(z, 3)


def _held_envelope(state):
    fn = _bridge("phys.support", "held_envelope")
    return fn(state) if fn is not None else _local_held_envelope(state)


def _local_held_envelope(state):
    """The fields of phys.support.held_envelope (TIER1_INTEGRATION 1.6) sight reads, with the same rules and the
    humanoid_v1 carry constants: items, two_handed, bottom_m, top_m, front_m, width_m, depth_m, height_m,
    blocks_view. A two-hand load (not a level-carried tray) rides on the hips, others hang centred on the hand; a load
    is carried with its smaller horizontal side as its depth and loads in the two hands sit side by side (their widths
    add); blocks_view is the camera rule (5.6): a two-hand load at least 0.35 x 0.25 x 0.25 m or anything declaring
    blocks_view, except a tray. TIER1-STUB(CAM-RUNTIME): delete once phys.support.held_envelope exists."""
    entities = state["entities"]
    hands = state["robot"].get("hands") or {}
    held = sorted(set(v for v in hands.values() if v and v in entities))
    if not held:
        return None
    cmap = children_map(state)
    items, loads = [], []
    for eid in held:
        rec = entities[eid]
        both = _two_handed(state, eid)
        w, d, h = _dims(state, eid, rec)
        tray = bool(rec.get("carry_level"))
        bottom = CARRY["hip_z_m"] if both and not tray else CARRY["hand_z_m"] - h / 2.0
        top = _load_top(state, eid, bottom, cmap, items)
        width, depth = max(w, d), min(w, d)
        blocks = not tray and (bool(rec.get("blocks_view")) or (
            both and width >= CARRIED_MIN_M["wide"] - EPS and depth >= CARRIED_MIN_M["deep"] - EPS
            and top - bottom >= CARRIED_MIN_M["tall"] - EPS))
        loads.append((bottom, top, width, depth, both, blocks))
    bottom, top = min(load[0] for load in loads), max(load[1] for load in loads)
    depth = max(load[3] for load in loads)
    return {"items": sorted(set(items)), "two_handed": any(load[4] for load in loads), "bottom_m": round(bottom, 4),
            "top_m": round(top, 4), "front_m": round(CARRY["front_m"] + depth / 2.0, 4),
            "width_m": round(sum(load[2] for load in loads), 4), "depth_m": round(depth, 4),
            "height_m": round(top - bottom, 4), "blocks_view": any(load[5] for load in loads)}


def _load_top(state, eid, base_z, cmap, items):
    """Highest point of eid and what rests on, in or hangs from it, eid's base at base_z (collects the ids)."""
    entities = state["entities"]
    rec = entities[eid]
    items.append(eid)
    h = _dims(state, eid, rec)[2]
    top = base_z + h
    for child in cmap.get(eid, ()):
        crec = entities[child]
        child_h = _dims(state, child, crec)[2]
        relation = crec.get("relation", "on")
        if relation == "on":
            offset = _num(rec.get("surface_height_m"), h)
        elif relation == "in":
            shelves, k = rec.get("shelf_heights"), int(_num(crec.get("shelf"), 0))
            offset = _num(shelves[k]) if shelves and 0 <= k < len(shelves) else _num(rec.get("inner_floor_m"), 0.05)
        elif relation == "hung":
            offset = max(0.0, _num(rec.get("hang_height_m"), h) - child_h)
        else:
            items.append(child)
            continue
        top = max(top, _load_top(state, child, base_z + offset, cmap, items))
    return top


def _two_handed(state, eid):
    hands = state["robot"].get("hands") or {}
    return state["entities"][eid].get("held_hand") == "both" or list(hands.values()).count(eid) > 1


def _dims(state, eid, rec):
    """(w, d, h) of an entity as it rests, in its own frame (phys.support.resting_size when it exists: orientation
    applied, rot not). Locally the same rule: lying on its side swaps the height with the longer footprint side."""
    fn = _bridge("phys.support", "resting_size")
    if fn is not None:
        size = fn(state, eid)
        return float(size[0]), float(size[1]), float(size[2])
    # TIER1-STUB(CAM-RUNTIME): the local rule below goes once phys.support.resting_size exists
    size = rec.get("size_m") or (0.1, 0.1, 0.1)
    w = max(0.0, _num(size[0], 0.1)) if len(size) > 0 else 0.1
    d = max(0.0, _num(size[1], w)) if len(size) > 1 else w
    h = max(0.0, _num(size[2], 0.1)) if len(size) > 2 else 0.1
    if rec.get("orientation") == "on_side":
        if w >= d:
            w, h = h, w
        else:
            d, h = h, d
    return w, d, h


class _Snapshot(dict):
    """children_map() result: {parent id: [child ids]} plus a memo of placements and container layouts, valid for the
    state it was built from while that state does not change."""
    __slots__ = ("state_id", "memo")


def children_map(state):
    """{parent id: [child ids, sorted]} over every entity: pass it as `cmap` to share one scan (and the placements and
    container layouts it memoizes) across many sight calls on an unchanged state; build a new one after any change."""
    out = _Snapshot()
    out.state_id, out.memo = id(state), {"place": {}, "layout": {}}
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
        z0 = geometry.height_m(state, eid)
        box = Box(loc, _r3(x0), _r3(y0), _r3(x0 + fw), _r3(y0 + fd), _r3(z0), _r3(z0 + h), yaw)
        return box, _Rel("room", eid, frozenset([eid]))
    if loc == "robot":
        robot = state["robot"]
        if not robot.get("pos") or robot.get("room") not in state["rooms"]:
            return None
        env = _held_envelope(state) or {}
        reach = _num(env.get("front_m"), CARRY["front_m"]) - _num(env.get("depth_m"), 0.0) / 2.0
        rad = math.radians(_robot_facing(state))
        cx = (robot["pos"][0] + 0.5) * CELL + math.cos(rad) * reach
        cy = (robot["pos"][1] + 0.5) * CELL + math.sin(rad) * reach
        z0 = CARRY["hip_z_m"] if _two_handed(state, eid) else CARRY["hand_z_m"] - h / 2.0
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
    support = (pbox.x0, pbox.y0, pbox.x1, pbox.y1, _spans(view_props(state, loc), pbox))
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
        return box, _Rel("behind", eid, skip, (x0, y0, x1, y1, support[4]), None)
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
    exact boxes (x0, y0, x1, y1, z_lo, z_hi, porosity, source, key) for segment tests, and per-cell shade regions."""
    __slots__ = ("rid", "sig", "cols", "rows", "spans", "boxes", "rects", "shade")

    def __init__(self, rid, sig, cols, rows):
        self.rid, self.sig, self.cols, self.rows = rid, sig, cols, rows
        self.spans = [()] * (cols * rows)
        self.boxes = []
        self.rects = {}
        self.shade = [()] * (cols * rows)

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
    size = rec.get("size_m") or ()
    big = any(_num(v) >= OCCLUDER_MIN_M - EPS for v in size[:3])
    return big or bool(rec.get("accepts_items") or rec.get("openable"))


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


def _sources(state, rid, world=None):
    """Ids of a room's occluder and container candidates that reach it through "on" relations, sorted. One pass sorts
    every candidate into its room; with a World it uses the static index and keeps the pass per mutation_count."""
    count = getattr(world, "mutation_count", None) if world is not None else None
    memo = _SOURCES_MEMO.get(world) if count is not None else None
    if memo is not None and memo[0] == count:
        return memo[1].get(rid, [])
    entities = state["entities"]
    if world is not None and hasattr(world, "static_index"):
        ids = [eid for eid in world.static_index("sight.sources", _source_candidate) if eid in entities]
    else:
        ids = sorted(eid for eid, rec in entities.items() if _source_candidate(rec))
    by_room = {}
    for eid in ids:
        room = root_room(state, eid)
        if room is not None and _on_chain(state, eid):
            by_room.setdefault(room, []).append(eid)
    if count is not None:
        _SOURCES_MEMO[world] = (count, by_room)
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
    (and the chain it rests on), the doors of its doorways and the @hook("view_occluders") output. With a World that
    has a mutation_count it is memoized per (room, count)."""
    count = getattr(world, "mutation_count", None) if world is not None else None
    if count is not None:
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
    digest.update(repr(_hook_occluders(state, rid)).encode("utf-8"))
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


def room_geom(state, rid, world=None):
    """The RoomGeom of a room for its current occ_sig (cached by content, 64 rooms)."""
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
            for k, (z_lo, z_hi, porosity) in enumerate(_spans(props, box)):
                add(eid, k, box.x0, box.y0, box.x1, box.y1, z_lo, z_hi, porosity)
                if porosity < 0.5 and z_lo > FLAT_MAX_M:
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
        idx = [int(c[1]) * cols + int(c[0]) for c in cells]
        add(door_id, 0, min(c[0] for c in cells) * CELL, min(c[1] for c in cells) * CELL,
            (max(c[0] for c in cells) + 1) * CELL, (max(c[1] for c in cells) + 1) * CELL, 0.0, height, porosity, idx)
    for k, (cells, z_lo, z_hi, porosity, source) in enumerate(_hook_occluders(state, rid)):
        inside = [c for c in cells if 0 <= c[0] < cols and 0 <= c[1] < rows]
        if not inside or z_hi - z_lo < 1e-6 or porosity >= 0.999:
            continue
        for c in inside:
            add(source, k, c[0] * CELL, c[1] * CELL, (c[0] + 1) * CELL, (c[1] + 1) * CELL, z_lo, z_hi, porosity,
                [c[1] * cols + c[0]])
    geom.spans = [tuple(s) for s in cell_spans]
    geom.shade = [tuple(sorted(s, key=lambda r: -r[7])) for s in cell_shade]
    return geom


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
        hit = _ray_rect(ex, ey, ux, uy, support[0], support[1], support[2], support[3])
        if hit is not None and hit[1] > 0.0 and hit[0] < d - 1e-9:
            d_in, d_out = max(1e-3, hit[0]), min(hit[1], d)
            for z_lo, z_hi, porosity in support[4]:
                if _blocks(z_lo, z_hi, d_in, d_out, ez, s):
                    if porosity < 0.5:
                        return 0.0
                    t *= porosity
    for x0, y0, x1, y1, top, porosity, aid in agents:
        if aid in skip:
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
        info = info._replace(skip=info.skip | skip)
    return _sample_transmission(geom, vs, p, info, _agents(state, rid, info.skip))


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
    rel = rel._replace(skip=rel.skip | frozenset(ignore))
    agents = _agents(state, rid, rel.skip)
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
    placed = _place(state, eid, cmap)
    if placed is None or placed[0].rid != viewer.rid:
        return 0.0
    box, rel = placed
    ignore = tuple(viewer.ignore or ())
    if rel.kind == "held" and _robot_viewer(ignore):
        return 1.0
    geom = geom or room_geom(state, viewer.rid)
    vs = viewshed(geom, viewer, (viewer.yaw, viewer.hfov / 2.0))
    rel = rel._replace(skip=rel.skip | frozenset(ignore))
    agents = _agents(state, viewer.rid, rel.skip)
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
    agents = _agents(state, viewer.rid, skip)
    out = set()
    for r in range(geom.rows):
        for c in range(geom.cols):
            p = ((c + 0.5) * CELL, (r + 0.5) * CELL, z)
            if in_frame(viewer, p) and _transmission(geom, vs, vs.x, vs.y, vs.z, p[0], p[1], p[2], skip, None,
                                                     agents) > 0.0:
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
        out.append(make_beam(eye, yaw_deg, tilt_deg, BEAMS["headlamp"]["power"], BEAMS["headlamp"]["half_deg"]))
    return out


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
    factor, plus @hook("light_at") fn(state, rid, x, y, z) boosts and the beams, clamped to [0, 1]."""
    level = (_ambient(state, rid) if ambient is None else float(ambient)) * \
        _shade(state, geom or room_geom(state, rid), p)
    for extra in run_hooks("light_at", state, rid, p[0], p[1], p[2]):
        if isinstance(extra, (int, float)):
            level += extra
    for beam in beams:
        level += beam_light(beam, p)
    return round(max(0.0, min(1.0, level)), 3)


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


def glare(state, viewer, p, radius_m=0.0, light=None):
    """Directional glare at a target point (6.2): "sun" (the sun, from @hook("sun_beam") at the eye, within hfov/2 +
    10 degrees, + 20 x lens_scratch for the robot's cameras, of the camera axis), "sun_cone" (the target within 20
    degrees of the sun while the sun is in frame) and "backlit" (a source of level >= 0.5 behind the target overlapping
    it by angular radius, with the target's light <= 0.6 x level). radius_m: the target's radius; light: its light
    (computed when None)."""
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
    for src in bright_sources(state, viewer.rid):
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


def specular(state, viewer, eid, face=None):
    """True if a glossy entity mirrors a bright source into the viewer (6.2): the view ray reflected about the face
    normal passes within 10 degrees (plus the source's angular radius) of a source in front of the face. face: a face
    name; default the "label" face, else "up" for flat things (height <= 0.3 x width), else the front."""
    if not view_props(state, eid).get("glossy"):
        return False
    placed = _place(state, eid)
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
    for src in bright_sources(state, viewer.rid):
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
        hit = _lidar_ray(geom, ex, ey, math.cos(rad), math.sin(rad), lid["height_m"], lid["range_m"], agent_cells,
                         openings)
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
