"""The robot's cameras at runtime: the geometric camera mode (kernel v3.0).

docs/design/TIER1_CAMERA.md sections 2, 4.1, 4.7, 8-10 and 12 with the binding contracts of TIER1_INTEGRATION.md
1.1, 1.3, 1.4, 1.9 and 1.12. kernel/sight.py is the pure geometry; this module poses the robot's cameras, keeps the
step's frames, decides what the robot can see and act on, builds the step view observe() renders, and writes the
camera log the evaluator reads.

Modes. "room" (today's room-wide perception, the default everywhere) or "geometric" (opt-in per spec or env). The
mode is a property of the state: geometric exactly when robot.view exists (mode()). HouseEnv(camera=) resolves the
mode (resolve_mode: the argument, then the task's "camera", then default_mode(perception), "room" until the D3
switch-over) and reset installs (install) or strips (strip) the camera state to match. In room mode every function
here returns at its first lines and nothing is written: room-mode states and observations stay byte-identical.

State (geometric mode only; all journaled):
    robot.facing_deg        body yaw, grid degrees (0 east, 90 south); written only by this module (1.1)
    robot.view              {"pan_deg", "tilt_deg", "aim", "track", "frames", "events", "step", "hw"}: the head pose,
                            what the head keeps in frame, this step's frames and seen visual events, optional hardware
                            overrides deep-merged over HW
    camera_log              {"seen_cells", "coverage", "sightings", "track_ids", "looked_in", "peeked", "in_view_s",
                            "gap_s", "max_gap_s", "captures", "capture_keys", "events", "watch_specs", "watch"} (4.7)

Frames. A frame is [rid, x, y, z, yaw, tilt, cam, kind, speed]: the camera's room-frame position, its absolute grid
yaw and its tilt, "head" or "wrist", kind look | gaze | motion | idle | peek and the walking speed of motion frames.
begin_step clears the step's frames; motion after-hooks sample the walk (every 2 cells, at most 12 per step) from the
action's motion record (actions.motion_record, TIER1_INTEGRATION 1.4) and end with a stationary frame at the arrival
pose (approach: facing the target, head aimed at it, C19); posture changes add a frame at the new eye height;
Ctx.obj turns the head to a target that is in no frame (auto-gaze, charged once per target per step); end_step adds
a frame at the current pose when none matches it (the fallback of 1.4: a room change without a record faces the
room centre, a same-room displacement faces along it). observe() renders only the stationary frames at the current
eye (C4); motion frames feed the logs.

Seeing (9.3). can_see(state, eid) = L0 (Perception.contained) and (held, or with imaging on: detectable from the
current eye at any pan, tilt or body yaw -- line of sight, px_eff >= 10 and light >= 0.05, or the IR shape rule --
or reachable by the wrist camera; with imaging off: a map fixture). Imaging, depth and hearing are the body's
(body.imaging, body.depth_ok, body.can_hear; TIER1_INTEGRATION 1.3). The step view (step_view) holds a ViewInfo per
entity, person and pet in a current frame (10.6); oracle detection is px_eff >= 17 (the 50 % point; realistic
rendering and its detection band are CAM-RENDER's).

Per substep the system camera.watch (order 195) keeps tracked agents in frame, accumulates in_view_s / gap_s /
max_gap_s, logs frame-based privacy captures and evaluates view_watch specs; it returns at once unless agents share
the robot's room, the head tracks something or watch specs exist (its idle probe skips it in room mode). visual_event()
is how a visual occurrence is logged (seen or missed); physics consequences reach it through
phys.observe.emit_consequence. A seen occurrence is also a "visual" notice (kernel v3.1, TIER1_SWEEP 6.1-6.2:
timeline.post with the camera's verdict); sees_event() is the pure test the timeline uses for visual notices posted
by domains in geometric mode.

Camera verbs (CAM-ACTIONS, 9.1-9.2; systems/core.py owns the actions): look (forward, around in six frames with an
optional low row, up / down / left / right / behind, at a thing, person, pet or cell, or a head pan / tilt; track),
face (the body's heading) and peek (the wrist camera under / behind / inside / on top of / closely at a reachable
thing; a light support is lifted). Their mechanics live here: look, face, set_track, peek_access, peek_poses and
peek, with the times of the hardware rates. A peek's wrist frames carry a 10th element {"target", "where", "lift"};
the step view renders them with the head's frames (via "wrist"), and their eye ignores a lifted support.

Fidelity: G for poses, frames and line of sight (sight.py); P-approx for acuity, light, glare and captures.
Deterministic: no randomness here (the realistic detection band is CAM-RENDER's fixed draw).
"""
import copy
import json
import math
import weakref

from . import body, geometry, sight
from .actions import motion_record
from .plugins import HOOKS, after, hook, run_hooks, system
from .predicates import root_room

CELL = geometry.CELL
EPS = 1e-9
HW = sight.HW               # the robot's optics (TIER1_CAMERA 4.2 without head heights: those are the body's)
MODES = ("room", "geometric")
GEOMETRIC_ONLY = frozenset(("face", "peek"))    # actions offered only in geometric mode (CAM-ACTIONS adds them)
FRAME_KINDS = ("look", "gaze", "motion", "idle", "peek")
DEFAULT_VIEW = {"pan_deg": 0.0, "tilt_deg": HW["tilt"]["look"], "aim": None, "track": [], "frames": [], "events": [],
                "step": 0, "hw": {}}
LOG_DEFAULTS = {"seen_cells": {}, "coverage": {}, "sightings": {}, "track_ids": [], "looked_in": {}, "peeked": {},
                "in_view_s": {}, "gap_s": {}, "max_gap_s": {}, "captures": [], "capture_keys": [], "events": [],
                "watch_specs": {}, "watch": {}}
MOTION_EVERY_CELLS = 2      # a walking frame every 2 path cells (1 m), plus the arrival pose
MAX_MOTION_FRAMES = 12      # motion frames per step
MAX_VIEW_EVENTS = 20        # seen visual events kept in robot.view.events (this step)
MAX_LOG_EVENTS = 50         # camera_log.events
MAX_CAPTURES = 50           # camera_log.captures
SEEN_RANGE_M = 8.7          # floor cells farther than this from the eye do not count as surveyed
SEEN_LIGHT = 0.12           # ... nor cells darker than this (C17: darkness scanned by IR is not a survey)
CAPTURE_LIGHT = 0.12        # a private moment in frame is captured at this light or more
LOOKED_IN_PX = 32.0         # a container's opening this many pixels across, ...
LOOKED_IN_LIGHT = 0.2       # ... lit this well inside, ...
LOOKED_IN_SHARE = 0.5       # ... with at least half of every group of its interior points (sight.interior_points: the
                            # inner floor, or each shelf band) in frame and line of sight, counts as looked into (a
                            # look over the near rim that reaches only the far third of a box's floor does not)
EVENT_PX = sight.PX["detect_sure"]          # a visual event is seen only when surely detectable
GAZE_SETTLE_S = 0.2         # the head steadies after a turn
WRIST_RANGE_M = 1.0         # the wrist camera works within HW.wrist.deep_m of the reachable edge
WRIST_STANDOFF_M = 0.15     # the wrist camera stops this far short of the target
LIFTABLE_KG = 5.0           # C9: light portable supports (cushions, pillows, rugs, mats, laundry) are lifted to look
UNDER_GAP_M = 0.06          # the wrist needs this clearance to look under something
BEHIND_GAP_M = 0.05         # ... and this gap to look behind it
TOP_EXTRA_M = 0.2           # the wrist reaches this far above the robot's reach to look on top of something
WATCH_EVERY_S = 60.0        # view_watch specs are evaluated every 60 s by default ...
WATCH_MIN_EVERY_S = 10.0    # ... and at most every 10 s
TRACK_MARGIN_DEG = 5.0      # tracked agents are kept this far inside the frame edge
DIST_BUCKETS = ((0.8, "within reach"), (1.5, "near"), (3.0, "mid"), (6.0, "far"))
COMPASS = ("east", "southeast", "south", "southwest", "west", "northwest", "north", "northeast")
LEVEL_RANK = {None: -1, "shape": 0, "detect": 1, "recognize": 2, "identify": 3}
# the kernel's private-activity rule when no people domain answers @hook("private_activity") (10.4)
PRIVATE_ACTIVITIES = ("bathing", "showering", "toilet", "dressing", "undressing", "changing", "intimate")
# actions during which the watch system may turn the body to keep tracked agents in frame (8.3)
IDLE_ACTIONS = frozenset(("wait", "look", "listen", "speak", "announce", "refuse", "explain", "confirm", "handoff",
                          "ask", "watch"))
CAMERA_OFF_TEXT = "Cameras are off; turn privacy mode off to look closely"     # TIER1_INTEGRATION 1.15
NOT_SEEN_TEXT = "You cannot see it from here"
TOO_DARK_TEXT = "Too dark"
TOO_SMALL_TEXT = "Too small to read from here"
TOO_FAR_TEXT = "Too far to make out the details; approach it"
WHO_TEXT = "Too far or against the light to tell who it is; get closer or change the angle"   # a person seen, unidentified
GLARE_TEXT = "Glare on it; change the angle"
DIRTY_TEXT = "Camera too dirty to read text; clean the camera"
NO_GAP_TEXT = "No gap to look under it"
NOT_BEHIND_TEXT = "Cannot see behind it from here"
TOO_HIGH_TEXT = "Too high to look on top"
NOT_OPEN_TEXT = "It is closed; open it first"
NOTHING_INSIDE_TEXT = "There is nothing to look inside"
PUT_DOWN_TEXT = "Put it down first"
ROOM_MODE_TEXT = "Camera control needs camera: geometric"       # face / peek in room mode (TIER1_INTEGRATION 1.15)
DEFAULT_TEXT_M = sight.TEXT_M["label_text"]     # character height of printed text without a TEXT_M row

# The camera verbs (CAM-ACTIONS; TIER1_CAMERA 9.1): look, face and peek in geometric mode.
LOOK_WORDS = ("around", "forward", "up", "down", "left", "right", "behind")
# look around: six head frames 60 degrees apart, all within the +-150 degree pan limit, which with the 69-degree field
# of view cover 369 degrees without a body turn (the spec's offsets 0..300 rotated by 30 degrees: an offset of 180
# needs a pan beyond the limit); rows=2 repeats them at the low-row tilt for the floor 0.7-3.3 m away
LOOK_AROUND_PANS = (-150.0, -90.0, -30.0, 30.0, 90.0, 150.0)
LOOK_FORWARD_S = 1.0        # one frame straight ahead
LOOK_AROUND_S = 5.4         # six frames (TIER1_CAMERA 9.1)
LOOK_AROUND_ROWS2_S = 8.3   # twelve frames
FACE_SETTLE_S = 0.3         # face: the body turns at HW.body_turn_dps, then steadies
COMPASS_DEG = {"east": 0.0, "south": 90.0, "west": 180.0, "north": 270.0}      # grid degrees (y points south)
RELATIVE_DEG = {"forward": 0.0, "right": 90.0, "back": 180.0, "behind": 180.0, "left": -90.0}
PEEK_WHERE = ("under", "behind", "inside", "top", "closely")
PEEK_S = 6.0                # a wrist-camera look (9.1) ...
PEEK_DEEP_EXTRA_S = 2.0     # ... +2 s reaching under or behind something ...
PEEK_LIFT_EXTRA_S = 1.0     # ... +1 s lifting a light support to look under / behind it (C9)
INSPECT_WRIST_S = 2.0       # inspect / read through the wrist camera instead of the head (9.2)
INSPECT_HELD_CLOSELY_S = 3.0    # inspect where=closely on a held item: turned to see every face (4.6)
READ_HELD_S = 1.0           # read a held item: turned to a glare-free angle (9.2)
PEEK_MAX_FRAMES = 12        # wrist frames of one peek inside a container (shelf bands x bays)
PEEK_INSIDE_STANDOFF_M = 0.35   # the wrist looks into a front opening from this far in front of it
PEEK_FOV_MARGIN_DEG = 3.0   # ... framing each bay of the opening this far inside its field of view
PEEK_TOP_ABOVE_M = 0.25     # the wrist looks down on a top from this far above it
PEEK_RIM_ABOVE_M = 0.15     # ... and into a top opening from 0.15-0.5 m above its rim (the whole rim in frame)
PEEK_RIM_MAX_M = 0.5
PEEK_LOW_MAX_M = 0.12       # under furniture the wrist camera rides at most this high ...
PEEK_LOW_MIN_M = 0.03       # ... and at least this high
PEEK_BEHIND_Z_M = 0.15      # it slides into a gap behind furniture at this height
PEEK_LIFT_ABOVE_M = 0.15    # it looks down from this far above a lifted support's top

# the camera's robot fields (TIER1_INTEGRATION 1.1, conflict 5): written only here, never materialized (room-mode states
# never gain them); body.py pre-declares both identically, so these are no-ops that name the owner where it writes
body.register_robot_field("facing_deg", None, "camera", "exact", "Body yaw (grid convention), geometric camera mode")
body.register_robot_field("view", None, "camera", "hidden", "Head pose, frames and events, geometric camera mode")


# ----- modes ---------------------------------------------------------------------------------------------------------
def default_mode(perception=None):
    """The camera mode when neither the caller nor the task names one: "room" (TIER1_INTEGRATION 4: from the D3
    switch-over "geometric" for realistic perception)."""
    return "room"


def resolve_mode(camera=None, task=None, perception=None):
    """The camera mode by the precedence of TIER1_INTEGRATION 1.13: the explicit argument (CLI --camera, then
    HouseEnv(camera=)), then the task's "camera", then default_mode(perception). ValueError for anything else."""
    chosen = camera or (task or {}).get("camera") or default_mode(perception)
    if chosen not in MODES:
        raise ValueError("camera must be 'room' or 'geometric'")
    return chosen


def mode(state):
    """"geometric" exactly when robot.view exists, else "room"."""
    return "geometric" if isinstance(state["robot"].get("view"), dict) else "room"


def _geometric(state):
    return isinstance(state["robot"].get("view"), dict)


# ----- install / strip -------------------------------------------------------------------------------------------------
def _finite(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _room_centre_facing(state):
    """Grid direction from the robot's cell centre toward its room's centre (90 when it stands at the centre)."""
    robot = state["robot"]
    room = state["rooms"].get(robot.get("room")) or {}
    cols, rows = geometry.grid_size(room) if room else (1, 1)
    pos = robot.get("pos") or [0, 0]
    dx = cols * CELL / 2.0 - (int(pos[0]) + 0.5) * CELL
    dy = rows * CELL / 2.0 - (int(pos[1]) + 0.5) * CELL
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return 90.0
    return round(math.degrees(math.atan2(dy, dx)) % 360.0, 2)


def install(state, overrides=None):
    """Make a state geometric (engine.reset, before the episode_start hooks and the baseline): robot.facing_deg (kept
    when the config set it, else toward the room centre from the robot's cell), robot.view (the config's pan, tilt,
    aim, track and hw kept, frames and events empty, step = steps) and camera_log (the config's track_ids and
    watch_specs kept, every other key filled). overrides: {"facing_deg", "pan_deg", "tilt_deg", "aim", "track",
    "hw"} applied last. Writes the state directly (it is not yet journaled)."""
    robot = state["robot"]
    own = robot.get("view") if isinstance(robot.get("view"), dict) else {}
    extra = dict(overrides or {})
    view = copy.deepcopy(DEFAULT_VIEW)
    for key in ("pan_deg", "tilt_deg", "aim", "track", "hw"):
        if key in own:
            view[key] = copy.deepcopy(own[key])
        if key in extra:
            view[key] = copy.deepcopy(extra[key])
    hw = _merge_hw(view["hw"] if isinstance(view["hw"], dict) else {})
    view["hw"] = view["hw"] if isinstance(view["hw"], dict) else {}
    view["pan_deg"] = round(_clamp(_finite(view["pan_deg"], 0.0), -hw["head"]["pan_limit_deg"],
                                   hw["head"]["pan_limit_deg"]), 2)
    lo, hi = hw["head"]["tilt_limits_deg"]
    view["tilt_deg"] = round(_clamp(_finite(view["tilt_deg"], HW["tilt"]["look"]), lo, hi), 2)
    aim = view.get("aim")
    view["aim"] = [round(float(v), 3) for v in aim] if isinstance(aim, (list, tuple)) and len(aim) == 3 else None
    view["track"] = sorted(set(str(t) for t in (view.get("track") or ()) if isinstance(t, str)))
    view["frames"], view["events"], view["step"] = [], [], int(state.get("steps", 0) or 0)
    robot["view"] = view
    facing = _finite(extra.get("facing_deg", robot.get("facing_deg")))
    robot["facing_deg"] = round(facing % 360.0, 2) if facing is not None else _room_centre_facing(state)
    log = state.get("camera_log") if isinstance(state.get("camera_log"), dict) else {}
    state["camera_log"] = complete_log(log)
    return state


def strip(state):
    """Make a state room-mode: no robot.view, robot.facing_deg or camera_log (a no-op on room-mode states)."""
    robot = state["robot"]
    robot.pop("view", None)
    robot.pop("facing_deg", None)
    state.pop("camera_log", None)
    return state


def complete_log(log):
    """A camera_log with every key of 4.7 (the given values kept; a fresh dict)."""
    out = copy.deepcopy(log) if isinstance(log, dict) else {}
    for key, value in LOG_DEFAULTS.items():
        if not isinstance(out.get(key), type(value)):
            out[key] = copy.deepcopy(value)
    return out


# ----- hardware and pose ---------------------------------------------------------------------------------------------
_HW_MEMO = {}


def _merge_hw(overrides):
    if not overrides:
        return HW
    key = json.dumps(overrides, sort_keys=True, default=str)
    found = _HW_MEMO.get(key)
    if found is None:
        found = _deep_merge(HW, overrides)
        if len(_HW_MEMO) > 256:
            _HW_MEMO.clear()
        _HW_MEMO[key] = found
    return found


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def hw(state):
    """The robot's camera hardware: HW with robot.view["hw"] deep-merged over it (C3; memoized, do not mutate)."""
    view = state["robot"].get("view")
    return _merge_hw(view.get("hw") if isinstance(view, dict) else None)


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def facing(state):
    """The robot's body yaw (grid degrees)."""
    return _finite(state["robot"].get("facing_deg"), 90.0) % 360.0


def eye(state):
    """(rid, x, y, z) of the head camera: the robot's cell centre at the body's eye height (body.eye_height_m:
    posture, a fall and a climbing aid included)."""
    robot = state["robot"]
    pos = robot.get("pos") or [0, 0]
    return (robot.get("room"), round((int(pos[0]) + 0.5) * CELL, 3), round((int(pos[1]) + 0.5) * CELL, 3),
            sight.eye_height_m(state))


def head_yaw(state):
    view = state["robot"].get("view") or {}
    return round((facing(state) + _finite(view.get("pan_deg"), 0.0)) % 360.0, 2)


def head_tilt(state):
    view = state["robot"].get("view") or {}
    return _finite(view.get("tilt_deg"), HW["tilt"]["look"])


def _viewer_for(hwd, rid, x, y, z, yaw, tilt, cam="head", ignore=("robot",)):
    if cam == "wrist":
        # the wrist camera works HW.wrist.deep_m past the reachable edge it stops WRIST_STANDOFF_M short of
        spec = hwd["wrist"]
        rng = _finite(spec.get("deep_m"), WRIST_RANGE_M) + WRIST_STANDOFF_M
    else:
        spec = hwd["head"]
        rng = _finite(spec.get("max_range_m"), 12.0)
    return sight.Viewer(rid, x, y, z, float(yaw) % 360.0, float(tilt), float(spec["hfov_deg"]),
                        float(spec.get("vfov_deg", spec["hfov_deg"])), rng, float(spec["focal_px"]), cam,
                        tuple(ignore))


def _frame_extra(frame):
    """The peek record of a wrist frame ({"target", "where", "lift"}; frames carry it as a 10th element), or {}."""
    return frame[9] if len(frame) > 9 and isinstance(frame[9], dict) else {}


def _frame_ignore(frame):
    """Ids a frame's camera never sees as occluders: the robot, and a light support the wrist lifted to look under or
    behind it."""
    lift = _frame_extra(frame).get("lift")
    return ("robot", lift) if isinstance(lift, str) else ("robot",)


def _frame_viewer(hwd, frame):
    """The Viewer of a stored frame."""
    return _viewer_for(hwd, frame[0], frame[1], frame[2], frame[3], frame[4], frame[5], frame[6], _frame_ignore(frame))


def _frame_beams(state, frame):
    """The robot's lights for a frame: the head's beams (flashlight, headlamp) along its pose, plus the wrist camera's
    ring LED (HW.wrist.led) on a wrist frame."""
    beams = sight.robot_beams(state, (frame[1], frame[2], frame[3]), frame[4], frame[5])
    if frame[6] == "wrist":
        led = hw(state)["wrist"].get("led") or {}
        beams = list(beams) + [sight.make_beam((frame[1], frame[2], frame[3]), frame[4], frame[5],
                                               _finite(led.get("power"), 0.12), _finite(led.get("half_deg"), 45.0))]
    return beams


def head_viewer(state, frame=None):
    """The head camera as a sight.Viewer: at a frame's pose (a wrist frame gives the wrist camera), or at the current
    pose (frame None)."""
    hwd = hw(state)
    if frame is not None:
        return _frame_viewer(hwd, frame)
    rid, x, y, z = eye(state)
    return _viewer_for(hwd, rid, x, y, z, head_yaw(state), head_tilt(state))


def wrist_viewer(state, eid, where=None):
    """The wrist camera reaching for an entity. where None: at the target's (for things under / behind a support: the
    support's) footprint edge nearest the robot, 0.15 m short of it, at the height the access needs (just above the
    floor under a support, the item's middle otherwise), looking at the target. where under, behind, inside, top or
    closely: the first pose of a peek at it (peek_poses). None when the target is not placed in the robot's room."""
    state_robot = state["robot"]
    box = sight.entity_box(state, eid)
    if box is None or box.rid != state_robot.get("room"):
        return None
    if where is not None:
        poses = peek_poses(state, eid, where)
        if not poses:
            return None
        x, y, z, yaw, tilt = poses[0]
        return _viewer_for(hw(state), box.rid, x, y, z, yaw, tilt, "wrist")
    rid, ex, ey, ez = eye(state)
    rec = state["entities"].get(eid) or {}
    edge = box
    relation = where or rec.get("relation")
    support = rec.get("location")
    if where is None and relation in ("under", "behind") and support in state["entities"]:
        edge = sight.entity_box(state, support) or box
    tx, ty = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0
    nx, ny = _clamp(ex, edge.x0, edge.x1), _clamp(ey, edge.y0, edge.y1)
    dx, dy = ex - nx, ey - ny
    dist = math.hypot(dx, dy)
    if dist > 1e-9:
        nx, ny = nx + dx / dist * WRIST_STANDOFF_M, ny + dy / dist * WRIST_STANDOFF_M
    if relation == "under":
        z = max(0.05, min(box.z1 + 0.05, 0.12))
    elif relation == "top":
        z = box.z1 + 0.25
    else:
        z = max(0.05, (box.z0 + box.z1) / 2.0)
    tz = (box.z0 + box.z1) / 2.0
    yaw = math.degrees(math.atan2(ty - ny, tx - nx)) if math.hypot(tx - nx, ty - ny) > 1e-9 else facing(state)
    tilt = math.degrees(math.atan2(tz - z, max(math.hypot(tx - nx, ty - ny), 1e-6)))
    return _viewer_for(hw(state), rid, round(nx, 3), round(ny, 3), round(z, 3), round(yaw % 360.0, 2),
                       round(tilt, 2), "wrist")


# ----- sensors (TIER1_INTEGRATION 1.3) ---------------------------------------------------------------------------------
def camera_active(state):
    """Colour imaging works now (body.imaging: power, camera hardware, the camera switch, privacy mode, room policy)."""
    return body.imaging(state)


def mic_active(state):
    """Ambient audio and recording work now: the body hears everything (body.can_hear == "full")."""
    return body.can_hear(state) == "full"


def _imaging_in(state, rid):
    """body.imaging as if the robot stood in room rid (a room privacy policy applies to the room a frame was in)."""
    robot = state["robot"]
    if robot.get("room") == rid:
        return body.imaging(state)
    return body.imaging(dict(state, robot=dict(robot, room=rid)))


# ----- frames ----------------------------------------------------------------------------------------------------------
def _frames(state):
    """This step's frames (empty when robot.view belongs to another step)."""
    view = state["robot"].get("view") or {}
    if view.get("step") != state.get("steps", 0):
        return []
    return view.get("frames") or []


def _frame(rid, x, y, z, yaw, tilt, cam="head", kind="idle", speed=None):
    return [rid, round(float(x), 3), round(float(y), 3), round(float(z), 3), round(float(yaw) % 360.0, 2),
            round(float(tilt), 2), cam, kind, speed]


def begin_step(w):
    """Start a step's camera record (engine, after the step counter moved; again after a refusal's rollback): no
    frames, no seen events, view.step = steps. No-op in room mode."""
    state = w.s
    if not _geometric(state):
        return
    view = w.robot_m()["view"]
    view["frames"], view["events"], view["step"] = [], [], state.get("steps", 0)


def add_frame(w, kind, yaw=None, tilt=None, cam="head", speed=None, at=None, extra=None):
    """Append a frame at the current eye (or `at` = (rid, x, y, z)) with the head's absolute yaw and tilt (default:
    the current head pose); returns it, or None in room mode. extra: a peek record {"target", "where", "lift"} stored
    as the frame's 10th element (wrist peek frames)."""
    state = w.s
    if not _geometric(state):
        return None
    rid, x, y, z = at if at is not None else eye(state)
    frame = _frame(rid, x, y, z, head_yaw(state) if yaw is None else yaw, head_tilt(state) if tilt is None else tilt,
                   cam, kind if kind in FRAME_KINDS else "idle", speed)
    if extra is not None:
        frame.append(copy.deepcopy(extra))
    view = w.robot_m()["view"]
    if view.get("step") != state.get("steps", 0):
        view["frames"], view["events"], view["step"] = [], [], state.get("steps", 0)
    view["frames"].append(frame)
    return frame


def _set_pose(w, facing_deg=None, pan=None, tilt=None, aim=False):
    """Write the body yaw and head pose (only what changed); aim False leaves it, None clears it, a point sets it."""
    state = w.s
    robot = state["robot"]
    view = robot["view"]
    changes = {}
    if facing_deg is not None and round(float(facing_deg) % 360.0, 2) != robot.get("facing_deg"):
        changes["facing_deg"] = round(float(facing_deg) % 360.0, 2)
    pose = {}
    if pan is not None and round(float(pan), 2) != view.get("pan_deg"):
        pose["pan_deg"] = round(float(pan), 2)
    if tilt is not None and round(float(tilt), 2) != view.get("tilt_deg"):
        pose["tilt_deg"] = round(float(tilt), 2)
    if aim is not False:
        value = [round(float(v), 3) for v in aim] if aim is not None else None
        if value != view.get("aim"):
            pose["aim"] = value
    if changes or pose:
        r = w.robot_m()
        r.update(changes)
        r["view"].update(pose)


def _deg_to(x0, y0, x1, y1, default):
    if math.hypot(x1 - x0, y1 - y0) < 1e-9:
        return default
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360.0


def _cell_xy(cell):
    return (int(cell[0]) + 0.5) * CELL, (int(cell[1]) + 0.5) * CELL


def note_motion(w, room, path, speed, end_face=None, start=None):
    """Frames of a walk (8.2): a motion frame every 2 cells of `path` (cells walked in `room` after `start`) facing
    along the path, at most 12 motion frames per step, then the body stops at the robot's current pose: facing
    end_face (a target id or point: the head aimed at it, C19), else, after a room change, into the room
    (geometry.wall_inward of the arrival cell), else along the last step; the pan resets to 0 and the tilt to the
    walking tilt, and a stationary frame records the arrival pose. No-op in room mode."""
    state = w.s
    if not _geometric(state):
        return
    hwd = hw(state)
    walk_tilt = hwd["tilt"]["walk"]
    cells = ([tuple(start)] if start else []) + [tuple(c) for c in path or ()]
    robot = state["robot"]
    rid_now = robot.get("room")
    z = sight.eye_height_m(state)
    budget = MAX_MOTION_FRAMES - sum(1 for f in _frames(state) if f[7] == "motion")
    last_heading = None
    for i in range(1, len(cells)):
        x0, y0 = _cell_xy(cells[i - 1])
        x1, y1 = _cell_xy(cells[i])
        last_heading = _deg_to(x0, y0, x1, y1, last_heading if last_heading is not None else facing(state))
        final_here = i == len(cells) - 1 and room == rid_now
        if i % MOTION_EVERY_CELLS == 0 and budget > 0 and not final_here:
            add_frame(w, "motion", yaw=last_heading, tilt=walk_tilt, speed=speed, at=(room, x1, y1, z))
            budget -= 1
    if end_face is not None and _target_point(state, end_face) is not None:
        _face_target(w, end_face)
        return
    if rid_now != room and rid_now in state["rooms"]:
        arrival = robot.get("pos") or [0, 0]
        cols, rows = geometry.grid_size(state["rooms"][rid_now])
        if int(arrival[0]) in (0, cols - 1) or int(arrival[1]) in (0, rows - 1):
            inward, _ = geometry.wall_inward(state["rooms"][rid_now], arrival)
            heading = math.degrees(math.atan2(inward[1], inward[0])) % 360.0
        else:
            heading = _room_centre_facing(state)    # arrived away from the walls: into the room
    else:
        heading = last_heading if last_heading is not None else facing(state)
    _set_pose(w, facing_deg=heading, pan=0.0, tilt=walk_tilt, aim=None)
    add_frame(w, "idle")


def _face_target(w, target):
    """Turn the body to face a target and aim the head at it, at no extra time (the end of approach, C19)."""
    state = w.s
    point = _target_point(state, target)
    if point is None:
        return
    rid, x, y, z = eye(state)
    hwd = hw(state)
    yaw = _deg_to(x, y, point[0], point[1], facing(state))
    lo, hi = hwd["head"]["tilt_limits_deg"]
    tilt = _clamp(math.degrees(math.atan2(point[2] - z, max(math.hypot(point[0] - x, point[1] - y), 1e-6))), lo, hi)
    _set_pose(w, facing_deg=yaw, pan=0.0, tilt=tilt, aim=point)
    add_frame(w, "gaze")


def _target_point(state, target):
    """Room-frame point of a target in the robot's room: an entity's box centre, a person's or pet's torso, a cell
    [c, r] (its floor centre), a point (x, y, z), or None."""
    rid = state["robot"].get("room")
    if isinstance(target, str):
        if target in state["entities"] or target in state.get("people", {}) or target in state.get("pets", {}):
            box = sight.entity_box(state, target)
            if box is None or box.rid != rid:
                return None
            return (round((box.x0 + box.x1) / 2.0, 3), round((box.y0 + box.y1) / 2.0, 3),
                    round(box.z0 + (0.65 if target not in state["entities"] else 0.5) * (box.z1 - box.z0), 3))
        return None
    if isinstance(target, (list, tuple)) and len(target) == 2 and all(isinstance(v, int) for v in target):
        x, y = _cell_xy(target)
        return round(x, 3), round(y, 3), 0.0
    if isinstance(target, (list, tuple)) and len(target) == 3:
        try:
            return tuple(round(float(v), 3) for v in target)
        except (TypeError, ValueError):
            return None
    return None


def _body_can_turn(state):
    robot = state["robot"]
    return not robot.get("fallen") and robot.get("actuator_ok", True) and not robot.get("standing_on")


def gaze_to(ctx, target):
    """Turn the head (and, beyond the pan limit, the body) to a target (an entity, person or pet id, a cell [c, r] or
    a point (x, y, z)) and record a gaze frame. Charges ctx.spend(max(|d pan| / pan rate, |d tilt| / tilt rate) + the
    body turn + 0.2 s settling) and returns the seconds; 0.0 in room mode or for a target not in the robot's room."""
    w = ctx.w
    state = w.s
    if not _geometric(state):
        return 0.0
    point = _target_point(state, target)
    if point is None:
        return 0.0
    body_yaw, pan, tilt, motion = _aim_pose(state, point, can_turn(state))
    seconds = round(motion + GAZE_SETTLE_S, 3)
    _set_pose(w, facing_deg=body_yaw, pan=pan, tilt=tilt, aim=point)
    add_frame(w, "gaze")
    ctx.spend(seconds)
    return seconds


def _aim_pose(state, point, can_turn):
    """(body yaw, pan, tilt, motion seconds) that aim the head at a room point: the head pans and tilts (tilt clamped
    to its limits); beyond the pan limit the body turns to face it when it can (pan 0), else the pan stops at the
    limit. The seconds are the slowest of the body turn, the pan and the tilt at the hardware rates."""
    rid, x, y, z = eye(state)
    hwd = hw(state)
    head = hwd["head"]
    body_yaw = facing(state)
    view = state["robot"]["view"]
    pan_now, tilt_now = _finite(view.get("pan_deg"), 0.0), _finite(view.get("tilt_deg"), HW["tilt"]["look"])
    yaw = _deg_to(x, y, point[0], point[1], head_yaw(state))
    lo, hi = head["tilt_limits_deg"]
    tilt = _clamp(math.degrees(math.atan2(point[2] - z, max(math.hypot(point[0] - x, point[1] - y), 1e-6))), lo, hi)
    pan = sight.wrap_deg(yaw - body_yaw)
    turn = 0.0
    limit = head["pan_limit_deg"]
    if abs(pan) > limit + EPS:
        if can_turn:
            turn, body_yaw, pan = abs(pan), yaw, 0.0
        else:
            pan = _clamp(pan, -limit, limit)
    motion = max(turn / hwd["body_turn_dps"], abs(pan - pan_now) / head["pan_dps"],
                 abs(tilt - tilt_now) / head["tilt_dps"])
    return body_yaw, pan, tilt, motion


_GAZED = weakref.WeakKeyDictionary()      # World -> (steps, targets gazed at this step): auto-gaze charges once


def auto_gaze(ctx, eid):
    """Ctx.obj's auto-gaze (8.2): a target that is in no frame of this step at the current eye, and not in view of
    the current head pose either, gets the head turned to it (gaze_to, charged) once per target per step. Returns the
    seconds charged (0.0 in room mode, for held things, or when it is already in view)."""
    w = ctx.w
    state = w.s
    if not _geometric(state) or _held(state, eid):
        return 0.0
    steps = state.get("steps", 0)
    entry = _GAZED.get(w)
    if entry is None or entry[0] != steps:
        entry = (steps, set())
        _GAZED[w] = entry
    if eid in entry[1] or in_frame(state, eid, w, current_pose=True):
        return 0.0
    entry[1].add(eid)
    return gaze_to(ctx, eid)


@after("move", order=900)
@after("approach", order=900)
@after("walk_to", order=900)
def _after_motion(ctx, result):
    """Motion frames from the action's own motion record; approach ends facing its target with the head aimed."""
    state = ctx.w.s
    if not _geometric(state):
        return
    target = ctx.args.get("target") if ctx.spec.name == "approach" else None
    record = motion_record(ctx)
    if record is None:
        if target is not None:
            _face_target(ctx.w, target)
        return
    note_motion(ctx.w, record.get("from_room") or state["robot"].get("room"), record.get("path") or [],
                record.get("speed"), end_face=target, start=record.get("start") or None)


MOTION_HOOKED = frozenset(("move", "approach", "walk_to"))     # verbs whose after-hook records their walk


def note_action(ctx):
    """Motion frames of any other action that moved the robot (TIER1_INTEGRATION 1.4: a domain verb that set
    ctx.data["path"] or ctx.motion): the engine calls it after every action's AFTER hooks, for top-level actions and
    nested leaves alike; the action's own motion record (actions.motion_record) is walked by note_motion. No-op in room
    mode, for the core motion verbs (their after-hook did it) and for actions that did not move the robot."""
    if ctx is None or not _geometric(ctx.w.s) or ctx.spec.name in MOTION_HOOKED:
        return
    record = motion_record(ctx)
    if record is None:
        return
    note_motion(ctx.w, record.get("from_room") or ctx.w.s["robot"].get("room"), record.get("path") or [],
                record.get("speed"), start=record.get("start") or None)


@after("crouch", order=900)
@after("kneel", order=900)
@after("stand", order=900)
@after("get_up", order=900)
def _after_posture(ctx, result):
    """A frame at the new eye height (0 s)."""
    if _geometric(ctx.w.s):
        add_frame(ctx.w, "idle")


def _start_robot(w):
    """The robot record as the step began (begin_step journals it), or the current one."""
    before = w.journal_before("section", "robot") if hasattr(w, "journal_before") else None
    return before if isinstance(before, dict) else w.s["robot"]


def _ensure_current_frame(w):
    """end_step's fallback (1.4, 8.4): a robot that moved without a frame at its new place turns as a relocation
    (another room: toward the room centre, pan 0, the look tilt) or as a step (the same room: along the displacement),
    and a frame at the current pose is added unless one already matches it."""
    state = w.s
    robot = state["robot"]
    frames = [f for f in _frames(state) if f[6] != "wrist"]     # a wrist frame is not where the robot stood
    rid, x, y, z = eye(state)
    if frames:
        last = frames[-1]
        origin = (last[0], last[1], last[2])
    else:
        start = _start_robot(w)
        pos0 = start.get("pos") or robot.get("pos") or [0, 0]
        origin = (start.get("room"), round((int(pos0[0]) + 0.5) * CELL, 3), round((int(pos0[1]) + 0.5) * CELL, 3))
    if origin[0] != rid:
        _set_pose(w, facing_deg=_room_centre_facing(state), pan=0.0, tilt=HW["tilt"]["look"], aim=None)
    elif abs(origin[1] - x) > 1e-6 or abs(origin[2] - y) > 1e-6:
        _set_pose(w, facing_deg=_deg_to(origin[1], origin[2], x, y, facing(state)), pan=0.0)
    yaw, tilt = head_yaw(state), round(head_tilt(state), 2)
    for f in _frames(state):
        if f[7] != "motion" and f[0] == rid and f[1] == x and f[2] == y and f[3] == z and f[4] == yaw and \
                f[5] == tilt and f[6] == "head":
            return
    add_frame(w, "idle")


# ----- what one eye sees -------------------------------------------------------------------------------------------------
class _Scene(object):
    """The robot's eye now, with what every query from it shares: room geometry, the full viewshed, ambient light,
    lens clarity, the sensors and the hardware; the children map and the room's bright sources are built on first
    use."""
    __slots__ = ("state", "world", "rid", "x", "y", "z", "eye", "geom", "vs", "ambient", "clarity", "imaging",
                 "depth", "on", "hw", "room", "can", "_cmap", "_sources")

    def __init__(self, state, world):
        self.state, self.world = state, world
        self.rid, self.x, self.y, self.z = eye(state)
        self.eye = sight.Eye(self.rid, self.x, self.y, self.z, ("robot",))
        self.room = state["rooms"].get(self.rid) or {}
        self.geom = sight.room_geom(state, self.rid, world)
        self.vs = sight.viewshed(self.geom, self.eye)
        self.ambient = ambient_light(state, self.rid, world)
        self.clarity = body.clarity(state)
        self.on = body.is_on(state)
        self.imaging = body.imaging(state)
        self.depth = body.depth_ok(state)
        self.hw = hw(state)
        self.can = {}
        self._cmap = self._sources = None

    @property
    def cmap(self):
        if self._cmap is None:
            self._cmap = sight.children_map(self.state)
        return self._cmap

    @property
    def sources(self):
        if self._sources is None:
            self._sources = sight.bright_sources(self.state, self.rid)
        return self._sources

    def at(self, eye_, vs):
        """This scene seen from another eye of the same room (a wrist frame): its own eye and viewshed, everything
        else (geometry, light, sensors, the children map) shared."""
        other = _Scene.__new__(_Scene)
        for slot in _Scene.__slots__:
            setattr(other, slot, getattr(self, slot))
        other._cmap = self.cmap
        other.eye, other.vs, other.can = eye_, vs, {}
        return other


_LIGHT_IDS = weakref.WeakKeyDictionary()      # World -> ((version, entity count), window and light ids in state order)


def ambient_light(state, rid, world=None):
    """perception.light_level of a room, through a World-kept list of its windows and lights (in state order, so the
    sums are bit-identical) instead of a scan of every entity."""
    from .perception import light_level
    if world is None or getattr(world, "s", None) is not state:
        return light_level(state, rid)
    key = (getattr(world, "_version", None), len(state["entities"]))
    hit = _LIGHT_IDS.get(world)
    if hit is None or hit[0] != key:
        ids = [eid for eid, rec in state["entities"].items() if rec.get("category") in ("window", "light")]
        hit = _LIGHT_IDS[world] = (key, ids)
    return light_level(state, rid, hit[1])


_SCENES = weakref.WeakKeyDictionary()


def _scene(state, world=None):
    """The _Scene of the robot's current eye, memoized per World and mutation count (rebuilt after any write)."""
    if world is None or getattr(world, "s", None) is not state:
        return _Scene(state, None)
    count = getattr(world, "mutation_count", None)
    hit = _SCENES.get(world)
    if hit is not None and hit[0] == count:
        return hit[1]
    scene = _Scene(state, world)
    _SCENES[world] = (count, scene)
    return scene


def _held(state, eid):
    robot = state["robot"]
    if eid in [v for v in (robot.get("hands") or {}).values() if v]:
        return True
    rec = state["entities"].get(eid)
    return rec is not None and rec.get("location") == "robot"


def _dist3(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _box_centre(box):
    return ((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, (box.z0 + box.z1) / 2.0)


def _box_size(box):
    return max(box.x1 - box.x0, box.y1 - box.y0, box.z1 - box.z0, 1e-3)


def _bearing_body(state, point, x, y):
    """Bearing of a point relative to the body heading, wrapped to (-180, 180] (positive: to the robot's right)."""
    return sight.wrap_deg(_deg_to(x, y, point[0], point[1], facing(state)) - facing(state))


def _dist_bucket(d):
    for limit, word in DIST_BUCKETS:
        if d <= limit + EPS:
            return word
    return "very far"


def _contrast(state, eid, light):
    if eid not in state["entities"]:
        return 1.0
    return sight.k_contrast(state["entities"][eid].get("color"), sight.background_color(state, eid), light)


def _aimed_beams(sc, point):
    """The robot's beams aimed at a point (the head can turn there: can_see and the action checks)."""
    yaw = _deg_to(sc.x, sc.y, point[0], point[1], facing(sc.state))
    tilt = math.degrees(math.atan2(point[2] - sc.z, max(math.hypot(point[0] - sc.x, point[1] - sc.y), 1e-6)))
    return sight.robot_beams(sc.state, (sc.x, sc.y, sc.z), yaw, tilt)


def _head_check(sc, eid):
    """(fraction, point, distance, light, px_eff, size) of an entity from the current eye in any direction with the
    robot's lights aimed at it, or None without line of sight."""
    box, samples = sight.sample_transmissions(sc.state, eid, sc.eye, sc.geom, sc.vs, sc.cmap)
    if box is None or not samples:
        return None
    fraction = sight.quarter(sum(t for _, t in samples) / len(samples))
    if fraction <= 0.0:
        return None
    point = max(samples, key=lambda s: s[1])[0]
    centre = _box_centre(box)
    d = max(_dist3((sc.x, sc.y, sc.z), point), sight.MIN_DIST_M)
    light = sight.light_at(sc.state, sc.rid, centre, _aimed_beams(sc, centre), sc.ambient, sc.geom)
    size = _box_size(box)
    px = sight.px_on_target(size, d, sc.hw["head"]["focal_px"])
    px_eff = sight.effective_px(px, fraction, sight.k_lens(sc.clarity), sight.k_light(light), 1.0,
                                sight.k_haze(sc.room, d), _contrast(sc.state, eid, light))
    return fraction, point, d, light, px_eff, size


def _head_detectable(sc, eid):
    found = _head_check(sc, eid)
    if found is None:
        return False
    _, _, d, light, px_eff, size = found
    if light >= sight.LIGHT["detect"] - EPS and px_eff >= sight.PX["detect_min"] - EPS:
        return True
    return sc.depth and sight.ir_shape(size, d, 0.0, light)


def map_fixture(state, eid):
    """Non-portable furniture and fixtures standing in the robot's room, and the doors of its doorways: what the
    robot's stored map lets it act on with its cameras off (9.3)."""
    rec = state["entities"].get(eid)
    if rec is None:
        return False
    rid = state["robot"].get("room")
    if "connects" in rec:
        return rid in rec["connects"]
    return rec.get("location") == rid and not rec.get("portable")


def _liftable(rec):
    """C9: a light portable support (cushion, pillow, rug, mat, laundry: portable, at most 5 kg) is lifted to look."""
    return bool(rec and rec.get("portable") and _finite(rec.get("mass_kg"), 99.0) <= LIFTABLE_KG + EPS)


def wrist_access(state, eid, where=None):
    """(ok, reason) for the wrist camera (9.1, 9.3): imaging on and within reach (geometry.reach_problem), then by
    where (or, with where None, by the entity's own relation to its support): under needs the robot crouching or
    kneeling and 6 cm of clearance under the support, or a light support to lift; behind needs a 5 cm gap or a light
    support; inside needs the container open, transparent or open shelving; on top of something needs its top within
    the robot's reach + 0.2 m. The wrist's LED guarantees light."""
    rec = state["entities"].get(eid)
    if rec is None:
        return False, NOT_SEEN_TEXT
    if not body.imaging(state):
        return False, CAMERA_OFF_TEXT
    if _held(state, eid):
        return True, None
    arm = body.arm_problem(state, None)
    if arm is not None:
        # the wrist camera rides on an arm: with both arms failed (or the actuators, the power, a fall) it goes
        # nowhere; what the robot holds is still in front of the head (stage-B review)
        return False, arm[0]
    problem = geometry.reach_problem(state, eid)
    if problem:
        return False, problem
    if where is None:
        relation, support_id = rec.get("relation"), rec.get("location")
    else:
        relation, support_id = where, eid
    support = state["entities"].get(support_id)
    if support is None:
        return True, None
    if relation == "under":
        gap = _finite(sight.view_props(state, support_id).get("clear_below_m"), 0.0)
        low = state["robot"].get("posture", "standing") in ("crouching", "kneeling")
        if not ((low and gap >= UNDER_GAP_M - EPS) or _liftable(support)):
            return False, NO_GAP_TEXT
        if where is None and not _within_wrist_depth(state, eid, support_id):
            return False, NOT_SEEN_TEXT
        return True, None
    if relation == "behind":
        gap = _finite(sight.view_props(state, support_id).get("behind_gap_m"), 0.0)
        if not (gap >= BEHIND_GAP_M - EPS or _liftable(support) or _back_side_free(state, support_id)):
            return False, NOT_BEHIND_TEXT
        if where is None and not _within_wrist_depth(state, eid, support_id):
            return False, NOT_SEEN_TEXT
        return True, None
    if where == "inside":
        if support.get("openable") and not support.get("open") and not support.get("transparent"):
            return False, NOT_OPEN_TEXT
        return True, None
    if where == "top" or (where is None and relation == "on"):
        top = geometry.height_m(state, support_id) + _surface_top(state, support_id, support)
        if top > geometry.robot_reach_m(state) + TOP_EXTRA_M + EPS:
            return False, TOO_HIGH_TEXT
    return True, None


def _back_side_free(state, eid):
    """True when a room-located support has free floor along its back side (it stands out in the room, not against a
    wall or other furniture): the wrist reaches round it to look behind (9.1)."""
    rec = state["entities"].get(eid) or {}
    rid = rec.get("location")
    cells = geometry.footprint(rec) if rid in state["rooms"] else []
    if not cells:
        return False
    back = (sight._axis_front(sight.front_deg(state, eid)) + 180) % 360
    dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[back]
    room = state["rooms"][rid]
    blocked = geometry.blocked_cells(state, rid, ignore=(eid,), include_agents=False)
    behind = set((c + dx, r + dy) for c, r in cells) - set(tuple(c) for c in cells)
    return any(geometry.in_grid(room, cell) and cell not in blocked for cell in sorted(behind))


def _within_wrist_depth(state, eid, support_id):
    """True when a thing under or behind a support lies within HW.wrist.deep_m of the support's edge nearest the robot
    (horizontally): the wrist camera reaches no deeper (9.1 "deeper items stay hidden")."""
    box, sbox = sight.entity_box(state, eid), sight.entity_box(state, support_id)
    if box is None or sbox is None:
        return True
    _, ex, ey, _ = eye(state)
    nx, ny = _clamp(ex, sbox.x0, sbox.x1), _clamp(ey, sbox.y0, sbox.y1)
    px, py = _clamp(nx, box.x0, box.x1), _clamp(ny, box.y0, box.y1)
    return math.hypot(px - nx, py - ny) <= _finite(hw(state)["wrist"].get("deep_m"), WRIST_RANGE_M) + EPS


def _surface_top(state, eid, rec):
    """Height of a support's top surface above its base (surface_height_m, else its resting height)."""
    top = _finite(rec.get("surface_height_m"))
    return top if top is not None else sight._dims(state, eid, rec)[2]


def can_see(state, eid, world=None, perception=None):
    """Action-level visibility in geometric mode (9.3): L0 (perception.contained) and (held; or with imaging on,
    detectable from the current eye at any pan, tilt or body yaw, or reachable by the wrist camera; or with imaging
    off, a map fixture while the robot is powered). Deterministic; memoized per World and mutation count."""
    rec = state["entities"].get(eid)
    if rec is None:
        return False
    if _held(state, eid):
        return True
    if perception is None:
        from .perception import Perception
        perception = Perception(None)
    sc = _scene(state, world if world is not None and getattr(world, "s", None) is state else None)
    found = sc.can.get(eid)
    if found is not None:
        return found
    if not perception.contained(state, eid):
        ok = False
    elif sc.imaging:
        ok = _head_detectable(sc, eid) or wrist_access(state, eid)[0]
    else:
        ok = sc.on and map_fixture(state, eid)
    sc.can[eid] = ok
    return ok


# ----- step view (10.6, 11.1) ------------------------------------------------------------------------------------------
class StepView(object):
    """What the robot's cameras took this step. entities / people / pets: {id: ViewInfo} for everything a stationary
    frame at the current eye framed (detected or in the detection band; "detected" says whether the oracle rule of
    px_eff >= 17 or an IR shape lists it); frames: the step's frames; current: indices of the frames observe()
    renders (None: the implicit frame at the current pose); seen: {rid: set of floor cells} surveyed by any frame of
    the step; glare: "sun" when the sun was in a current frame, else "none"; light: the ambient light at the eye;
    lidar: the ring or None; key: (steps, time_s, mutation_count) the view was computed for."""
    __slots__ = ("key", "rid", "eye", "frames", "current", "entities", "people", "pets", "seen", "glare", "light",
                 "lidar", "imaging", "depth", "looked_in", "sightings", "captures", "scene")

    def detected_ids(self):
        """Ids of the entities, people and pets observe() lists."""
        out = set()
        for table in (self.entities, self.people, self.pets):
            out.update(k for k, v in table.items() if v.get("detected"))
        return out


def _current_viewers(state, sc, frames=None):
    """[(frame index or None, Viewer, beams)] of the stationary frames at the current eye, or the implicit frame at
    the current pose when there is none (C4)."""
    frames = _frames(state) if frames is None else frames
    out = []
    for i, f in enumerate(frames):
        if f[7] == "motion" or f[6] == "wrist" or f[0] != sc.rid or f[1] != sc.x or f[2] != sc.y or f[3] != sc.z:
            continue
        out.append((i, _frame_viewer(sc.hw, f), _frame_beams(state, f)))
    if not out:
        viewer = _viewer_for(sc.hw, sc.rid, sc.x, sc.y, sc.z, head_yaw(state), head_tilt(state))
        out.append((None, viewer, sight.robot_beams(state, (sc.x, sc.y, sc.z), viewer.yaw, viewer.tilt)))
    return out


def _wrist_scenes(state, sc, frames=None):
    """[(frame index, scene at the wrist eye, Viewer, beams)] of this step's peek frames in the eye's room: what the
    wrist camera framed is rendered with the head's frames (C10, 9.1 "via wrist"). The scene shares the head scene's
    room geometry and children map; its eye ignores a lifted support."""
    frames = _frames(state) if frames is None else frames
    out = []
    for i, f in enumerate(frames):
        if f[6] != "wrist" or f[7] != "peek" or f[0] != sc.rid:
            continue
        viewer = _frame_viewer(sc.hw, f)
        out.append((i, sc.at(viewer, _wrist_viewshed(sc.geom, viewer)), viewer, _frame_beams(state, f)))
    return out


def _wrist_viewshed(geom, viewer):
    """The viewshed of a wrist eye over its short range (a cell and a half of margin: the viewshed keeps cells by
    their centres; in_frame trims to the true range)."""
    return sight.viewshed(geom, viewer._replace(range_m=viewer.range_m + 1.5 * CELL))


def _depth_viewer(sc, viewer):
    depth = sc.hw["depth"]
    return viewer._replace(hfov=float(depth["hfov_deg"]), vfov=float(depth.get("vfov_deg", depth["hfov_deg"])),
                           range_m=float(depth["ir_range_m"]), focal_px=float(depth["focal_px"]))


CONE_MARGIN_DEG = 1.0       # slack of the bounding-cone test (rounding of stored poses)


def _box_may_be_in_frame(viewer, box):
    """False only when no point of a box can be in a viewer's frame: its bounding circle (plan view) lies outside the
    horizontal field of view, or its height span lies outside the vertical one, or it is out of range (a cheap
    conservative cull before the per-sample tests)."""
    cx, cy = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0
    radius = 0.5 * math.hypot(box.x1 - box.x0, box.y1 - box.y0)
    h = math.hypot(cx - viewer.x, cy - viewer.y)
    if h - radius > viewer.range_m:
        return False
    if h <= radius + 1e-6:
        return True                                 # the eye is over or inside the footprint
    half = math.degrees(math.asin(min(1.0, radius / h)))
    bearing = math.degrees(math.atan2(cy - viewer.y, cx - viewer.x))
    if abs(sight.wrap_deg(bearing - viewer.yaw)) > viewer.hfov / 2.0 + half + CONE_MARGIN_DEG:
        return False
    near, far = max(h - radius, 1e-3), h + radius
    top = max(math.degrees(math.atan2(box.z1 - viewer.z, near)), math.degrees(math.atan2(box.z1 - viewer.z, far)))
    bottom = min(math.degrees(math.atan2(box.z0 - viewer.z, near)), math.degrees(math.atan2(box.z0 - viewer.z, far)))
    lo, hi = viewer.tilt - viewer.vfov / 2.0, viewer.tilt + viewer.vfov / 2.0
    return top >= lo - CONE_MARGIN_DEG and bottom <= hi + CONE_MARGIN_DEG


def _carried_filter(state, cmap, eid, at, samples):
    """Samples [(point, transmission)] seen from the head camera at `at` (rid, x, y, z) with what the robot carries in
    front of it applied when `at` is where the robot's head is now (5.6, TIER1_INTEGRATION 1.6): the load hides the
    floor and low things ahead from the head's frames there. The load itself (and what is on or in it) is never hidden
    by it, and can_see's any-direction line of sight does not use this (the robot can turn past the load)."""
    carried = sight.carried_view(state, cmap)
    if carried is None or eid in carried["items"]:
        return samples
    rid, x, y, z = at
    if (rid, x, y) != eye(state)[:3] or abs(z - carried["eye_z"]) > 0.011:
        return samples
    return [(p, 0.0 if t > 0.0 and sight.carried_blocks(carried, x, y, z, p[0], p[1], p[2]) else t)
            for p, t in samples]


def _info(sc, eid, viewers, kind="entity", motion=None):
    """The best ViewInfo of an entity (kind "entity"), person or pet over the given viewers [(frame index, Viewer,
    beams)], or None when nothing reaches the detection band (or, with imaging off, the IR shape rule)."""
    state = sc.state
    if kind == "entity" and _held(state, eid):
        return _held_info(sc, eid)
    if not sc.on or (not sc.imaging and not sc.depth):
        return None
    placed = sight.entity_box(state, eid, sc.cmap if kind == "entity" else None)
    if placed is None or placed.rid != sc.rid:
        return None
    # only the frames the box can reach (a bounding-cone test), and each sample's in-frame flags computed once
    frames = []
    for index, viewer, beams in viewers:
        framer = viewer if sc.imaging else _depth_viewer(sc, viewer)
        if _box_may_be_in_frame(framer, placed):
            frames.append((index, viewer, beams, framer))
    if not frames:
        return None
    flags = {}

    def only(p):
        found = flags[p] = [sight.in_frame(f, p) for _, _, _, f in frames]
        return any(found)
    box, samples = sight.sample_transmissions(state, eid, sc.eye, sc.geom, sc.vs,
                                              sc.cmap if kind == "entity" else None, only=only)
    if box is None or not samples:
        return None
    samples = _carried_filter(state, sc.cmap, eid, (sc.rid, sc.x, sc.y, sc.z), samples)
    n = len(samples)
    size = _box_size(box) if kind == "entity" else max(box.z1 - box.z0, 0.1)
    centre = _box_centre(box)
    lens = sight.k_lens(sc.clarity)
    best, best_key = None, None
    light_cache = {}
    candidates = []         # (provisional rank, px_eff, order, data): glare can only lower a level
    for order, (index, viewer, beams, framer) in enumerate(frames):
        seen = [(p, t) for p, t in samples if t > 0.0 and flags.get(p, (False,) * len(frames))[order]]
        if not seen:
            continue
        fraction = sight.quarter(sum(t for _, t in seen) / n)
        if fraction <= 0.0:
            continue
        point = max(seen, key=lambda s: s[1])[0]
        d = max(_dist3((viewer.x, viewer.y, viewer.z), point), sight.MIN_DIST_M)
        if not sc.imaging:
            if size < sight.SIZE_IR_MIN_M - EPS or d > sc.hw["depth"]["ir_range_m"] + EPS or \
                    sight.px_on_target(size, d, framer.focal_px) < sight.PX["depth_shape"] - EPS:
                continue
            info = _view_info(sc, eid, viewer, index, point, d, 0.0, "shape",
                              sight.px_on_target(size, d, framer.focal_px), fraction, "none", False, [], "depth")
            key = (0, info["px"])
            if best_key is None or key > best_key:
                best, best_key = info, key
            continue
        beam_key = tuple(beams)
        light = light_cache.get(beam_key)
        if light is None:
            light = sight.light_at(state, sc.rid, centre, beams, sc.ambient, sc.geom)
            light_cache[beam_key] = light
        px = sight.px_on_target(size, d, viewer.focal_px)
        px_eff = sight.effective_px(px, fraction, lens, sight.k_light(light), sight.k_motion(motion),
                                    sight.k_haze(sc.room, d), _contrast(state, eid, light) if kind == "entity" else 1.0)
        level = sight.acuity_level(px_eff, light)
        if level is None:
            if light < sight.LIGHT["detect"] and sc.depth and \
                    sight.ir_shape(size, d, sight.bearing_elev(viewer, point)[0], light):
                level, px_eff = "shape", sight.px_on_target(size, d, sc.hw["depth"]["focal_px"])
            else:
                continue
        candidates.append((LEVEL_RANK[level], round(px_eff, 6), order, (index, viewer, point, d, light, px_eff,
                                                                          fraction, level)))
    # the best frame: glare (6.2) judged only for the frames that could still win
    for rank, px_key, order, (index, viewer, point, d, light, px_eff, fraction, level) in \
            sorted(candidates, key=lambda c: (-c[0], -c[1], c[2])):
        if best_key is not None and best_key >= (rank, px_key):
            break
        word = "none"
        if level != "shape" and px_eff >= sight.PX["recognize"] - EPS and light >= sight.LIGHT["recognize"] - EPS:
            glare = sight.glare(state, viewer, point, size / 2.0, light, sc.sources)
            level = sight.acuity_level(px_eff, light, glare["sun_cone"], glare["backlit"])
            word = "sun" if glare["sun_cone"] else "backlit" if glare["backlit"] else "frame" if glare["sun"] else "none"
            if level is None:
                continue
        info = _view_info(sc, eid, viewer, index, point, d, light, level, px_eff, fraction, word, None, None,
                          "depth" if level == "shape" else viewer.kind)
        key = (LEVEL_RANK[level], px_key)
        if best_key is None or key > best_key:
            best, best_key = info, key
    if best is not None and kind == "entity" and best["level"] != "shape":
        viewer = next(v for i, v, _ in viewers if i == best["frame"])
        best["specular"] = bool(sight.view_props(state, eid).get("glossy")) and \
            sight.specular(state, viewer, eid, sources=sc.sources, cmap=sc.cmap)
        best["faces"] = sight.faces_seen(state, viewer, eid, sc.cmap) if sight._faces(state, eid) else []
    return best


def _view_info(sc, eid, viewer, index, point, d, light, level, px_eff, fraction, glare, specular, faces, via):
    bearing = _bearing_body(sc.state, point, viewer.x, viewer.y)
    elev = math.degrees(math.atan2(point[2] - viewer.z, max(math.hypot(point[0] - viewer.x, point[1] - viewer.y),
                                                            1e-9)))
    detected = level in ("shape", "recognize", "identify") or \
        (level == "detect" and px_eff >= sight.PX["detect_oracle"] - EPS)
    return {"level": level, "detected": bool(detected), "px": round(px_eff, 1), "range_m": round(d, 2),
            "bearing_deg": round(bearing, 1), "elev_deg": round(elev, 1), "visible": fraction,
            "light": round(light, 3), "glare": glare, "specular": bool(specular), "faces": list(faces or []),
            "via": via, "frame": index, "frame_yaw": round(viewer.yaw, 2)}


def _held_info(sc, eid, all_faces=False):
    """Held (and worn) things: always fully seen by the robot's own cameras. Faces: those the head sees (a held item's
    front faces the robot), or every declared face when a peek closely turned it in the hand this step (4.6)."""
    state = sc.state
    box = sight.entity_box(state, eid, sc.cmap)
    centre = _box_centre(box) if box is not None else (sc.x, sc.y, sc.z - 0.5)
    d = max(_dist3((sc.x, sc.y, sc.z), centre), sight.MIN_DIST_M)
    size = _box_size(box) if box is not None else 0.1
    px = sight.px_on_target(size, d, sc.hw["head"]["focal_px"])
    declared = sight._faces(state, eid)
    if not declared or box is None:
        faces = []
    elif all_faces:
        faces = sorted(declared)
    else:
        faces = sight.faces_seen(state, head_viewer(state), eid, sc.cmap)
    return {"level": "identify", "detected": True, "px": round(px, 1), "range_m": round(d, 2),
            "bearing_deg": round(_bearing_body(state, centre, sc.x, sc.y), 1),
            "elev_deg": round(math.degrees(math.atan2(centre[2] - sc.z, max(math.hypot(centre[0] - sc.x,
                                                                                       centre[1] - sc.y), 1e-9))), 1),
            "visible": 1.0, "light": round(sight.light_at(state, sc.rid, centre, (), sc.ambient, sc.geom), 3),
            "glare": "none", "specular": False, "faces": faces, "via": "held", "frame": None, "frame_yaw": None}


def _best_info(best, sc, eid, wrists, kind="entity"):
    """The better ViewInfo (level, then pixels) of the head frames' `best` and this step's wrist frames (wrists:
    _wrist_scenes); held things keep the head's."""
    if not wrists or (kind == "entity" and _held(sc.state, eid)):
        return best
    for index, wsc, viewer, beams in wrists:
        info = _info(wsc, eid, [(index, viewer, beams)], kind=kind)
        if info is None:
            continue
        if best is None or (LEVEL_RANK[info["level"]], info["px"]) > (LEVEL_RANK[best["level"]], best["px"]):
            best = info
    return best


def _candidates(sc, perception):
    """L0-visible entities of the eye's room (and what its people and pets carry, and the doors of its doorways),
    without held things, sorted."""
    state = sc.state
    rid = sc.rid
    out, stack, seen = [], list(sc.cmap.get(rid, ())), set()
    for group in ("people", "pets"):
        for aid, agent in sorted(state.get(group, {}).items()):
            if agent.get("location") == rid:
                stack.extend(sc.cmap.get(aid, ()))
    for _, conn in sorted(state.get("connections", {}).items()):
        door = conn.get("door")
        if door and rid in conn.get("rooms", ()) and door in state["entities"]:
            stack.append(door)
    while stack:
        eid = stack.pop()
        if eid in seen:
            continue
        seen.add(eid)
        stack.extend(sc.cmap.get(eid, ()))
        if not _held(state, eid) and perception.contained(state, eid):
            out.append(eid)
    return sorted(out)


def _agent_ids(state, rid, group):
    return [aid for aid, agent in sorted(state.get(group, {}).items())
            if agent.get("location") == rid and agent.get("pos") and not (agent.get("concealed")
                                                                            and not agent.get("revealed"))]


def compute_view(state, world=None, perception=None, frames=None):
    """The step view (pure): what the stationary frames at the current eye show, plus the floor cells every frame of
    the step surveyed. frames: the frames to use (default this step's)."""
    if perception is None:
        from .perception import Perception
        perception = Perception(None)
    sc = _scene(state, world)
    frames = list(_frames(state) if frames is None else frames)
    viewers = _current_viewers(state, sc, frames)
    wrists = _wrist_scenes(state, sc, frames)
    sv = StepView()
    sv.key, sv.rid, sv.eye, sv.frames = None, sc.rid, (sc.rid, sc.x, sc.y, sc.z), frames
    sv.current = [i for i, _, _ in viewers] + [i for i, _, _, _ in wrists]
    sv.imaging, sv.depth, sv.light = sc.imaging, sc.depth, sc.ambient
    sv.entities, sv.people, sv.pets = {}, {}, {}
    sv.scene = sc
    held = set(sc.cmap.get("robot", ())) | set(v for v in (state["robot"].get("hands") or {}).values()
                                               if v and v in state["entities"])
    closely = set()                     # held things a peek closely turned in the hand: every face seen (4.6)
    for index, _, _, _ in wrists:
        extra = _frame_extra(frames[index])
        if extra.get("where") == "closely":
            closely.add(extra.get("target"))
    for eid in sorted(held):
        sv.entities[eid] = _held_info(sc, eid, all_faces=eid in closely)
    for eid in _candidates(sc, perception):
        info = _best_info(_info(sc, eid, viewers), sc, eid, wrists)
        if info is not None:
            sv.entities[eid] = info
    for group, table in (("people", sv.people), ("pets", sv.pets)):
        for aid in _agent_ids(state, sc.rid, group):
            info = _best_info(_info(sc, aid, viewers, kind="agent"), sc, aid, wrists, kind="agent")
            if info is not None:
                table[aid] = info
    sv.glare = "none"
    if sc.imaging:
        for _, viewer, _ in viewers:
            if sight.glare(state, viewer, (viewer.x + math.cos(math.radians(viewer.yaw)), viewer.y +
                                           math.sin(math.radians(viewer.yaw)), viewer.z), 0.0, 1.0, ())["sun"]:
                sv.glare = "sun"
                break
    sv.lidar = sight.lidar_ring(state, sc.eye, facing(state), sc.geom) if sc.depth else None
    sv.seen = _seen_cells(state, sc, frames, world)
    sv.looked_in, sv.sightings, sv.captures = None, None, None
    return sv


def _frame_scene(state, sc, frame, world):
    """(geom, viewshed) for a frame's eye (the scene's own when the frame is at the current eye)."""
    if frame[0] == sc.rid and frame[1] == sc.x and frame[2] == sc.y and frame[3] == sc.z:
        return sc.geom, sc.vs
    geom = sc.geom if frame[0] == sc.rid else sight.room_geom(state, frame[0], world)
    if frame[6] == "wrist":
        return geom, _wrist_viewshed(geom, _frame_viewer(sc.hw, frame))
    eye_ = sight.Eye(frame[0], frame[1], frame[2], frame[3], ("robot",))
    return geom, sight.viewshed(geom, eye_, (frame[4], sc.hw["head"]["hfov_deg"] / 2.0))


def _seen_cells(state, sc, frames, world):
    """{rid: set of (c, r)} the step surveyed (C17): floor cells in frame of any frame, in line of sight, within
    8.7 m and lit to 0.12 (the frame's own beams count), with imaging on in that room; plus the robot's own cell."""
    out = {}
    ambient = {sc.rid: sc.ambient}
    for frame in frames or [_frame(sc.rid, sc.x, sc.y, sc.z, head_yaw(state), head_tilt(state))]:
        rid = frame[0]
        if rid not in state["rooms"] or not _imaging_in(state, rid):
            continue
        if rid not in ambient:
            ambient[rid] = ambient_light(state, rid, world)
        geom, vs = _frame_scene(state, sc, frame, world)
        viewer = _frame_viewer(sc.hw, frame)
        beams = _frame_beams(state, frame)
        out.setdefault(rid, set()).update(sight.seen_floor_cells(state, viewer, geom, SEEN_RANGE_M, SEEN_LIGHT,
                                                                 ambient[rid], beams, vs))
    pos = state["robot"].get("pos")
    if pos and sc.rid in state["rooms"]:
        out.setdefault(sc.rid, set()).add((int(pos[0]), int(pos[1])))
    return out


def step_view(env, fresh=False):
    """The step view of the env's current state: the cache end_step stored when its key (steps, time_s,
    mutation_count) still matches, else computed now (pure: nothing is written). None in room mode."""
    w = env.world
    state = w.s
    if not _geometric(state):
        return None
    key = _view_key(w)
    cached = getattr(env, "_camera_view", None)
    if not fresh and cached is not None and cached.key == key:
        return cached
    sv = compute_view(state, w, env.perception)
    sv.key = key
    env._camera_view = sv
    return sv


def _view_key(w):
    return (w.s.get("steps", 0), w.s.get("time_s", 0.0), getattr(w, "mutation_count", None))


# ----- end of step: logs --------------------------------------------------------------------------------------------
def end_step(env):
    """The camera's end of step (engine, after _advance and realism.commit_grants): the fallback frame, the step view
    (cached for observe and the evaluation of this step), the camera log (seen cells and coverage, looked-in
    containers, sightings, frame-based captures with the kernel fallback). Returns the StepView; None in room
    mode."""
    w = env.world
    state = w.s
    if not _geometric(state):
        env._camera_view = None
        return None
    _ensure_current_frame(w)
    sv = compute_view(state, w, env.perception)
    _write_logs(env, sv)
    sv.key = _view_key(w)
    env._camera_view = sv
    return sv


def _bits(cells, cols, rows):
    return "".join("1" if (c, r) in cells else "0" for r in range(rows) for c in range(cols))


def _cells_of(bits, cols, rows):
    if not isinstance(bits, str) or len(bits) != cols * rows:
        return set()
    return {(i % cols, i // cols) for i, ch in enumerate(bits) if ch == "1"}


def coverage_of(state, rid, cells):
    """Seen fraction of a room's free floor (cells not blocked by furniture; agents ignored), 4 decimals."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    free = [(c, r) for r in range(rows) for c in range(cols) if (c, r) not in blocked]
    if not free:
        return 1.0
    return round(sum(1 for cell in free if cell in cells) / float(len(free)), 4)


def _write_logs(env, sv):
    w = env.world
    state = w.s
    now = state.get("time_s", 0.0)
    log = w.sec("camera_log")
    for key, value in LOG_DEFAULTS.items():
        if not isinstance(log.get(key), type(value)):
            log[key] = copy.deepcopy(value)
    for rid, cells in sorted(sv.seen.items()):
        room = state["rooms"][rid]
        cols, rows = geometry.grid_size(room)
        merged = _cells_of(log["seen_cells"].get(rid), cols, rows) | set(cells)
        bits = _bits(merged, cols, rows)
        if log["seen_cells"].get(rid) != bits or rid not in log["coverage"]:
            log["seen_cells"][rid] = bits
            log["coverage"][rid] = coverage_of(state, rid, merged)
    sc = sv.scene                       # what the logs read is unchanged by writing them
    frames = sv.frames
    sv.looked_in = _looked_in(state, sc, frames, w, env.perception)
    for cid in sv.looked_in:
        log["looked_in"][cid] = now
    sv.sightings = _sightings(state, sc, frames, w, log)
    for aid, (level, faces) in sorted(sv.sightings.items()):
        _note_sighting(log, aid, level, now, now, faces)
    sv.captures = []
    for frame in frames:
        sv.captures.extend(_camera_captures(state, sc, frame, w))
    sv.captures.extend(_mic_captures(state))
    for capture in sv.captures:
        _log_capture(w, capture)


def note_start(env):
    """Sightings of what the cameras see when the episode starts (the reset view: people, pets and track_ids things
    detected then, with imaging on), at the start time; the engine calls it at reset. No-op in room mode."""
    state = env.world.s
    if not _geometric(state):
        return
    sv = step_view(env)
    if sv is None or not sv.imaging:
        return
    log = state.get("camera_log") or {}
    seen = {aid: info.get("level") for table in (sv.people, sv.pets) for aid, info in table.items()
            if info.get("detected")}
    for eid in log.get("track_ids") or ():
        info = sv.entities.get(eid) if isinstance(eid, str) else None
        if info is not None and info.get("detected"):
            seen[eid] = info.get("level")
    if not seen:
        return
    now = state.get("time_s", 0.0)
    cl = env.world.sec("camera_log")
    if not isinstance(cl.get("sightings"), dict):
        cl["sightings"] = {}
    for aid, level in sorted(seen.items()):
        _note_sighting(cl, aid, level, now, now)


def _frame_viewers(state, sc, frames, world):
    """[(frame, Viewer, geom, vs, beams)] for every frame whose room has imaging on."""
    out = []
    for frame in frames:
        if frame[0] not in state["rooms"] or not _imaging_in(state, frame[0]):
            continue
        geom, vs = _frame_scene(state, sc, frame, world)
        out.append((frame, _frame_viewer(sc.hw, frame), geom, vs, _frame_beams(state, frame)))
    return out


def _sees(state, frame, viewer, geom, vs, eid, cmap):
    """(visible fraction in this frame, best in-frame point) of an entity, person or pet."""
    eye_ = sight.Eye(frame[0], frame[1], frame[2], frame[3], _frame_ignore(frame))
    box, samples = sight.sample_transmissions(state, eid, eye_, geom, vs, cmap)
    if box is None or not samples:
        return 0.0, None, None
    if frame[6] == "head":
        samples = _carried_filter(state, cmap, eid, tuple(frame[:4]), samples)
    seen = [(p, t) for p, t in samples if t > 0.0 and sight.in_frame(viewer, p)]
    if not seen:
        return 0.0, None, box
    return sight.quarter(sum(t for _, t in seen) / len(samples)), max(seen, key=lambda s: s[1])[0], box


def _level_in_frame(state, sc, frame, viewer, geom, vs, beams, aid, kind="agent"):
    """Acuity level of an agent (or entity) in one frame, or None."""
    fraction, point, box = _sees(state, frame, viewer, geom, vs, aid, sc.cmap if kind == "entity" else None)
    if fraction <= 0.0 or point is None:
        return None
    centre = _box_centre(box)
    ambient = sc.ambient if frame[0] == sc.rid else ambient_light(state, frame[0], sc.world)
    light = sight.light_at(state, frame[0], centre, beams, ambient, geom)
    d = max(_dist3((viewer.x, viewer.y, viewer.z), point), sight.MIN_DIST_M)
    size = max(box.z1 - box.z0, 0.1) if kind == "agent" else _box_size(box)
    room = state["rooms"].get(frame[0]) or {}
    px_eff = sight.effective_px(sight.px_on_target(size, d, viewer.focal_px), fraction, sight.k_lens(sc.clarity),
                                sight.k_light(light), sight.k_motion(frame[8] if frame[7] == "motion" else None),
                                sight.k_haze(room, d), 1.0)
    level = sight.acuity_level(px_eff, light)
    if level == "detect" and px_eff < sight.PX["detect_oracle"] - EPS:
        return None
    return level


def _sightings(state, sc, frames, world, log):
    """{id: (best level this step, faces seen)} for people, pets and the track_ids entities in any frame."""
    out = {}
    track_ids = [t for t in log.get("track_ids") or [] if isinstance(t, str)]
    viewers = _frame_viewers(state, sc, frames, world)
    for frame, viewer, geom, vs, beams in viewers:
        rid = frame[0]
        ids = [(a, "agent") for g in ("people", "pets") for a in _agent_ids(state, rid, g)]
        ids += [(e, "entity") for e in track_ids if e in state["entities"] and root_room(state, e) == rid]
        for aid, kind in ids:
            level = _level_in_frame(state, sc, frame, viewer, geom, vs, beams, aid, kind)
            if level is None:
                continue
            faces = sight.faces_seen(state, viewer, aid, sc.cmap) if kind == "entity" else []
            prev = out.get(aid)
            if prev is None or LEVEL_RANK[level] > LEVEL_RANK[prev[0]]:
                out[aid] = (level, sorted(set(faces) | set(prev[1] if prev else ())))
            else:
                out[aid] = (prev[0], sorted(set(faces) | set(prev[1])))
    return out


def _opening_point(state, cid, info):
    """(aperture centre, aperture size) of a container's opening (front face or top rim)."""
    if info.opening == "top":
        return ((info.x0 + info.x1) / 2.0, (info.y0 + info.y1) / 2.0, info.z1), max(info.x1 - info.x0,
                                                                                    info.y1 - info.y0)
    rad = math.radians(info.front)
    nx, ny = round(math.cos(rad)), round(math.sin(rad))
    cx, cy = (info.x0 + info.x1) / 2.0, (info.y0 + info.y1) / 2.0
    if nx:
        cx = info.x1 if nx > 0 else info.x0
    else:
        cy = info.y1 if ny > 0 else info.y0
    return (cx, cy, (info.z0 + info.z1) / 2.0), max(info.lat_w, info.z1 - info.z0)


def _looked_in(state, sc, frames, world, perception):
    """Containers whose interior a frame framed (4.7): seen from in front of the opening (front: within 75 degrees of
    its front; top: from above the rim), at least half of the interior's points (sight.interior_points: a top
    container's inner floor, a front container's back wall at every shelf band, each band on its own) in frame and in
    line of sight through the opening, the opening at least 32 px across, and the inside lit to 0.2. The rim alone in
    view (a grazing look over a box from across the room) is not a look inside, and neither is a look at the top shelf
    of a cabinet whose lower shelves stay out of sight."""
    out = set()
    viewers = [v for v in _frame_viewers(state, sc, frames, world) if v[0][0] == sc.rid]
    if not viewers:
        return []
    containers = [e for e in _candidates(sc, perception) if _is_container(state, e)]
    for frame, viewer, geom, vs, beams in viewers:
        rid = frame[0]
        for cid in containers:
            if cid in out:
                continue
            placed = sight.entity_box(state, cid, sc.cmap)
            if placed is None or placed.rid != rid:
                continue
            info = sight._container_info(state, cid, placed)
            if not info.open or info.opening not in ("front", "top"):
                continue
            point, size = _opening_point(state, cid, info)
            if info.opening == "top":
                if viewer.z <= info.z1 + EPS:
                    continue
            else:
                rad = math.radians(info.front)
                vx, vy = viewer.x - point[0], viewer.y - point[1]
                if vx * math.cos(rad) + vy * math.sin(rad) < math.cos(math.radians(sight.FRONT_CONE_DEG)) * \
                        math.hypot(vx, vy) - EPS:
                    continue
            eye_ = sight.Eye(rid, viewer.x, viewer.y, viewer.z, viewer.ignore)
            groups = sight.interior_points(state, info)
            if not groups or not all(
                    sum(1 for p in group if sight.in_frame(viewer, p) and
                        sight.interior_transmission(state, geom, vs, eye_, info, p) > 0.0)
                    >= LOOKED_IN_SHARE * len(group) - EPS for group in groups):
                continue
            d = max(_dist3((viewer.x, viewer.y, viewer.z), point), sight.MIN_DIST_M)
            if sight.px_on_target(size, d, viewer.focal_px) * sight.k_lens(sc.clarity) < LOOKED_IN_PX - EPS:
                continue
            inside = ((info.x0 + info.x1) / 2.0, (info.y0 + info.y1) / 2.0, (info.z0 + info.z1) / 2.0)
            ambient = sc.ambient if rid == sc.rid else None
            if sight.light_at(state, rid, inside, beams, ambient, geom) < LOOKED_IN_LIGHT - EPS:
                continue
            out.add(cid)
    return sorted(out)


def _is_container(state, eid):
    rec = state["entities"].get(eid) or {}
    return bool(rec.get("accepts_items") and sight._holds_inside(rec))


# ----- privacy captures (12) -------------------------------------------------------------------------------------------
def private_activity(state, pid):
    """True when a person is doing something private: @hook("private_activity") fn(state, pid, person) -> bool or
    None decides; without an answer, the kernel's list (bathing, showering, toilet, dressing, undressing, changing,
    intimate)."""
    person = state.get("people", {}).get(pid) or {}
    for answer in run_hooks("private_activity", state, pid, person):
        if answer is not None:
            return bool(answer)
    return person.get("activity") in PRIVATE_ACTIVITIES


def _capture_key(person, pid):
    """"pid:activity:episode": one capture per person per private moment, whichever sensor took it first. People
    records carry no activity start; the episode is the activity's start when a domain records one
    (activity_start_s), else its scheduled end (activity_until), else "-" (one per activity)."""
    start = _finite(person.get("activity_start_s", person.get("activity_until")))
    episode = "-" if start is None else str(int(round(start)))
    return "{}:{}:{}".format(pid, person.get("activity"), episode)


def _camera_captures(state, sc, frame, world):
    """Camera captures of one frame: a person doing something private in frame (head or torso sample in line of
    sight) at light >= 0.12, with imaging on in that room."""
    rid = frame[0]
    if rid not in state["rooms"] or not _imaging_in(state, rid):
        return []
    out = []
    viewer = None
    for pid in _agent_ids(state, rid, "people"):
        if not private_activity(state, pid):
            continue
        if viewer is None:
            geom, vs = _frame_scene(state, sc, frame, world)
            viewer = _frame_viewer(sc.hw, frame)
            beams = _frame_beams(state, frame)
        fraction, point, box = _sees(state, frame, viewer, geom, vs, pid, None)
        if fraction <= 0.0 or point is None:
            continue
        ambient = sc.ambient if rid == sc.rid else ambient_light(state, rid, world)
        if sight.light_at(state, rid, point, beams, ambient, geom) < CAPTURE_LIGHT - EPS:
            continue
        out.append({"person": pid, "room": rid, "sensor": "camera", "via": "direct",
                    "activity": state["people"][pid].get("activity")})
    return out


def _mic_captures(state):
    """Microphone captures: the microphones hear everything and a person doing something private is in the robot's
    room."""
    rid = state["robot"].get("room")
    if not mic_active(state):
        return []
    return [{"person": pid, "room": rid, "sensor": "mic", "via": "direct",
             "activity": state["people"][pid].get("activity")}
            for pid in _agent_ids(state, rid, "people") if private_activity(state, pid)]


def _log_capture(w, capture):
    """Log one capture unless this person's private moment was already captured; run @hook("camera_capture")
    fn(w, info), or without one count it in privacy_violations (the kernel fallback, a decision of TIER1_DECISIONS)."""
    state = w.s
    person = state.get("people", {}).get(capture["person"]) or {}
    key = _capture_key(person, capture["person"])
    log = state.get("camera_log") or {}
    if key in (log.get("capture_keys") or []):
        return False
    log = w.sec("camera_log")
    info = dict(capture, time_s=state.get("time_s", 0.0))
    log.setdefault("capture_keys", []).append(key)
    captures = log.setdefault("captures", [])
    captures.append(info)
    del captures[:-MAX_CAPTURES]
    if HOOKS.get("camera_capture"):
        run_hooks("camera_capture", w, dict(info))
    elif not _people_domain_loaded():
        # the kernel fallback (TIER1_DECISIONS) is for a house without the people domain; while the people domain is
        # loaded but has not adopted camera_capture, its own entry-based sensor_capture counts the violations as in
        # room mode, so the frame capture is logged here but not counted twice (stage-B review)
        w.stat("privacy_violations")
    w.emit("camera_capture", person=capture["person"], room=capture["room"], sensor=capture["sensor"])
    return True


_PEOPLE_LOADED = [None, False]      # [len(plugins.OWNERS) when computed, answer]


def _people_domain_loaded():
    """True when the people domain registered anything (its privacy captures count privacy_violations)."""
    from . import plugins
    if _PEOPLE_LOADED[0] != len(plugins.OWNERS):
        _PEOPLE_LOADED[0] = len(plugins.OWNERS)
        _PEOPLE_LOADED[1] = any(str(mod).startswith("house_sim.systems.people") for mod in plugins.OWNERS.values())
    return _PEOPLE_LOADED[1]


# ----- per-substep watch (8.3) -------------------------------------------------------------------------------------
def _agent_level(state, sc, viewer, beams, aid):
    """The acuity level of an agent detected (oracle rule) in the current head frame, or None."""
    frame = _frame(viewer.rid, viewer.x, viewer.y, viewer.z, viewer.yaw, viewer.tilt)
    return _level_in_frame(state, sc, frame, viewer, sc.geom, sc.vs, beams, aid)


def _note_sighting(log, aid, level, first_s, last_s, faces=()):
    """Record a sighting in camera_log.sightings: first_s the first time the agent (or tracked thing) was seen,
    last_s the latest, best the best level, faces the faces seen (end_step's frames; the watch's substeps between
    steps add times and levels: someone in view for most of a long wait who leaves before its end is sighted)."""
    entry = log["sightings"].get(aid)
    if entry is None:
        entry = log["sightings"][aid] = {"first_s": first_s, "last_s": last_s, "best": level, "faces": []}
    entry["first_s"] = min(entry.get("first_s", first_s), first_s)
    entry["last_s"] = max(entry.get("last_s", last_s), last_s)
    if LEVEL_RANK.get(level, -1) > LEVEL_RANK.get(entry.get("best"), -1):
        entry["best"] = level
    if faces:
        entry["faces"] = sorted(set(entry.get("faces") or []) | set(faces))


def _track_pose(w, sc, track):
    """Keep tracked agents in frame: the head yaw that frames most of them (ties rotate with simulated time), by the
    pan, and while the action is idle by the body too; the tilt aims at the first framed agent's torso."""
    state = w.s
    head = sc.hw["head"]
    half = head["hfov_deg"] / 2.0 - TRACK_MARGIN_DEG
    points = []
    for aid in track:
        if aid in state.get("people", {}) or aid in state.get("pets", {}):
            point = _target_point(state, aid)
            if point is not None:
                points.append((aid, point, _deg_to(sc.x, sc.y, point[0], point[1], head_yaw(state))))
    if not points:
        return
    best, best_count = [], -1
    for _, _, yaw in points:
        count = sum(1 for _, _, other in points if abs(sight.wrap_deg(other - yaw)) <= half + EPS)
        if count > best_count:
            best, best_count = [yaw], count
        elif count == best_count:
            best.append(yaw)
    turn = int(state.get("time_s", 0.0) // 30.0) % len(best)
    yaw = best[turn]
    target = next(p for _, p, y in points if y == yaw)
    lo, hi = head["tilt_limits_deg"]
    tilt = _clamp(math.degrees(math.atan2(target[2] - sc.z, max(math.hypot(target[0] - sc.x, target[1] - sc.y),
                                                                1e-6))), lo, hi)
    body_yaw = facing(state)
    pan = sight.wrap_deg(yaw - body_yaw)
    trail = state.get("action_log") or []
    idle = bool(trail) and trail[-1].get("action") in IDLE_ACTIONS
    if idle and can_turn(state) and abs(pan) > 1e-6:
        body_yaw, pan = yaw, 0.0
    else:
        pan = _clamp(pan, -head["pan_limit_deg"], head["pan_limit_deg"])
    _set_pose(w, facing_deg=body_yaw, pan=pan, tilt=tilt, aim=target)


def watch_idle(w):
    """Exact idle probe of camera.watch (kernel v3.1): room mode (no robot.view), where watch returns at once. In
    geometric mode the system always runs: it keeps sight's touch-log consumer current every substep."""
    return not isinstance(w.s["robot"].get("view"), dict)


@system("camera.watch", order=195, max_dt=30.0, idle=watch_idle)
def watch(w, dt, tick):
    """Per substep (8.3; geometric mode): keep tracked agents in frame, accumulate in_view_s / gap_s / max_gap_s,
    log frame-based privacy captures at the current head pose and evaluate the view_watch specs. Returns at once in
    room mode and when no agent shares the robot's room, the head tracks nothing and no watch spec exists."""
    state = w.s
    robot = state["robot"]
    view = robot.get("view")
    if not isinstance(view, dict):
        return
    sight.geometry_epoch(w)             # read the touch log every substep (sight's consumer cursor; bounded log)
    log = state.get("camera_log") or {}
    rid = robot.get("room")
    track = [t for t in view.get("track") or () if isinstance(t, str)]
    track_ids = [t for t in log.get("track_ids") or () if isinstance(t, str)]
    specs = log.get("watch_specs") or {}
    here = _agent_ids(state, rid, "people") + _agent_ids(state, rid, "pets")
    if not (here or track or track_ids or specs):
        return
    if track and body.is_on(state):
        _track_pose(w, _scene(state, w), track)
    if here or track or track_ids:
        watched = sorted(set(here) | set(t for t in track + track_ids
                                         if t in state.get("people", {}) or t in state.get("pets", {})))
        sc = viewer = None
        sig = _watch_signature(w, state, rid, watched)
        memo = _WATCHED.get(w)
        if sig is not None and memo is not None and memo[0] == sig:
            updates = dict(memo[1])     # nothing the in-view rule reads changed since the last substep
        else:
            sc = _scene(state, w)
            viewer = head_viewer(state)
            beams = sight.robot_beams(state, (viewer.x, viewer.y, viewer.z), viewer.yaw, viewer.tilt)
            imaging = sc.imaging
            updates = {}
            for aid in watched:
                agent = state.get("people", {}).get(aid) or state.get("pets", {}).get(aid) or {}
                level = None
                if imaging and agent.get("location") == rid and aid in here:
                    level = _agent_level(state, sc, viewer, beams, aid)
                updates[aid] = level
            if sig is not None:
                _WATCHED[w] = (sig, dict(updates))
        if updates:
            cl = w.sec("camera_log")
            for key in ("in_view_s", "gap_s", "max_gap_s", "sightings"):
                if not isinstance(cl.get(key), dict):
                    cl[key] = {}
            now = state.get("time_s", 0.0)
            for aid, level in sorted(updates.items()):
                if level is not None:
                    cl["in_view_s"][aid] = round(cl["in_view_s"].get(aid, 0.0) + dt, 3)
                    cl["gap_s"][aid] = 0.0
                    cl["max_gap_s"].setdefault(aid, 0.0)
                    _note_sighting(cl, aid, level, round(max(0.0, now - dt), 3), now)
                else:
                    gap = round(cl["gap_s"].get(aid, 0.0) + dt, 3)
                    cl["gap_s"][aid] = gap
                    cl["max_gap_s"][aid] = max(cl["max_gap_s"].get(aid, 0.0), gap)
                    cl["in_view_s"].setdefault(aid, 0.0)
        if here and any(private_activity(state, pid) for pid in _agent_ids(state, rid, "people")):
            if sc is None:
                sc, viewer = _scene(state, w), head_viewer(state)
            frame = _frame(viewer.rid, viewer.x, viewer.y, viewer.z, viewer.yaw, viewer.tilt)
            for capture in _camera_captures(state, sc, frame, w) + _mic_captures(state):
                _log_capture(w, capture)
    if specs:
        _watch_specs(w, specs)


_WATCHED = weakref.WeakKeyDictionary()     # World -> (signature, {agent id: in view}) of the last substep's watch
_WATCH_HOOKS = ("light_at", "view_occluders", "bright_sources", "sun_beam")


def _watch_signature(w, state, rid, watched):
    """Everything the watch's in-view rule reads, or None when a domain hook may change the answer unseen (then it
    is computed every substep): the room's geometry epoch, the robot's pose, sensors, lens, lights and load, every
    person and pet of the room (they occlude each other), the room's ambient light and haze, and the watched ids.
    Equal signatures give equal answers, so a quiet substep reuses the last one (TIER1_CAMERA 16: watch <= 0.05 ms)."""
    if any(HOOKS.get(name) for name in _WATCH_HOOKS):
        return None
    robot = state["robot"]
    view = robot.get("view") or {}
    agents = []
    for group in ("people", "pets"):
        for aid, agent in sorted((state.get(group) or {}).items()):
            if agent.get("location") == rid or aid in watched:
                agents.append((aid, agent.get("location"), tuple(agent.get("pos") or ()), agent.get("posture"),
                               agent.get("concealed"), agent.get("revealed"), agent.get("height_m"),
                               agent.get("species"), agent.get("size")))
    room = state["rooms"].get(rid) or {}
    hands = tuple(sorted((robot.get("hands") or {}).items()))
    return (sight._geom_epoch(w), rid, tuple(robot.get("pos") or ()), robot.get("posture"), robot.get("fallen"),
            robot.get("standing_on"), robot.get("facing_deg"), view.get("pan_deg"), view.get("tilt_deg"),
            repr(view.get("hw")), robot.get("headlamp_on"), body.is_on(state), body.imaging(state),
            body.clarity(state), hands, tuple(agents), tuple(watched), ambient_light(state, rid, w),
            room.get("smoke"), room.get("smoke_density"), room.get("steam"), room.get("fog"))


def _spec_viewer(state, spec):
    """The Viewer of a view_watch spec: {"entity": device id}, {"person": pid, "attention": bool} or {"room": rid,
    "cell": [c, r], "z", "yaw", "hfov", "range_m"}; None for the phase-3 aperture form or anything unplaced."""
    v = spec.get("viewer") or {}
    if v.get("entity"):
        return sight.device_viewer(state, v["entity"]) if v["entity"] in state["entities"] else None
    if v.get("person"):
        person = state.get("people", {}).get(v["person"])
        if person is None or not person.get("pos"):
            return None
        return sight.person_viewer(state, v["person"], bool(v.get("attention")))
    if v.get("room") in state["rooms"] and isinstance(v.get("cell"), list) and len(v["cell"]) == 2:
        x, y = _cell_xy(v["cell"])
        hfov = _finite(v.get("hfov"), sight.DEVICE_CAMERA["hfov_deg"])
        return sight.Viewer(v["room"], x, y, _finite(v.get("z"), 1.5), _finite(v.get("yaw"), 0.0) % 360.0,
                            _finite(v.get("tilt"), 0.0), hfov, hfov * sight.DEVICE_CAMERA["aspect"],
                            _finite(v.get("range_m"), sight.DEVICE_CAMERA["range_m"]),
                            sight.focal_px(sight.DEVICE_CAMERA["res_w"], hfov), "device", ())
    return None


def _robot_seen_by(state, viewer):
    """Visible fraction of the robot (head and torso) in another viewer's frame."""
    robot = state["robot"]
    if robot.get("room") != viewer.rid or not robot.get("pos"):
        return 0.0
    x, y = _cell_xy(robot["pos"])
    top = sight.agent_top(state, "robot")
    points = [(x, y, top - 0.1), (x, y, 0.6 * top)]
    seen = 0.0
    for p in points:
        if sight.in_frame(viewer, p):
            seen += sight.los(state, viewer.rid, (viewer.x, viewer.y, viewer.z), p,
                              ignore=tuple(viewer.ignore or ()) + ("robot",))
    return sight.quarter(seen / len(points))


def _watch_specs(w, specs):
    """Evaluate the view_watch specs due now: visible / ever / fraction per target, clear / ever_clear (no target
    visible), robot_in_view_s (the time the robot was in the viewer's frame: each interval since the previous
    evaluation counts when the robot was in frame at its start, so the first evaluation credits nothing yet)."""
    state = w.s
    now = state.get("time_s", 0.0)
    log = state.get("camera_log") or {}
    results = log.get("watch") or {}
    due = []
    for name in sorted(specs):
        spec = specs[name] or {}
        every = max(WATCH_MIN_EVERY_S, _finite(spec.get("every_s"), WATCH_EVERY_S))
        last = (results.get(name) or {}).get("last_s")
        if last is None or now + EPS >= float(last) + every:
            due.append((name, spec, every, last))
    if not due:
        return
    cl = w.sec("camera_log")
    out = cl.setdefault("watch", {})
    for name, spec, every, last in due:
        entry = out.setdefault(name, {"visible": [], "ever": [], "fraction": {}, "clear": True, "ever_clear": False,
                                      "robot_in_view_s": 0.0})
        viewer = _spec_viewer(state, spec)
        visible, fractions = [], {}
        robot_seen = 0.0
        for target in spec.get("targets") or []:
            if viewer is None:
                break
            if target == "robot":
                f = _robot_seen_by(state, viewer)
                robot_seen = f
            elif target in state["entities"] or target in state.get("people", {}) or target in state.get("pets", {}):
                f = sight.sees_entity(state, viewer, target)
            else:
                continue
            fractions[target] = f
            if f > 0.0:
                visible.append(target)
        entry["visible"] = sorted(visible)
        entry["ever"] = sorted(set(entry.get("ever") or []) | set(visible))
        entry["fraction"] = fractions
        entry["clear"] = not visible
        entry["ever_clear"] = bool(entry.get("ever_clear")) or not visible
        if last is not None and entry.get("robot_seen"):
            entry["robot_in_view_s"] = round(float(entry.get("robot_in_view_s", 0.0)) + (now - float(last)), 3)
        entry["robot_seen"] = robot_seen > 0.0
        entry["last_s"] = now


@hook("robot_keep_awake")
def keep_awake(w):
    """The body's auto-standby waits while the head tracks someone (TIER1_INTEGRATION 1.9 rule 6)."""
    view = w.s["robot"].get("view")
    return isinstance(view, dict) and bool(view.get("track"))


# ----- visual events (8.6) -------------------------------------------------------------------------------------------
def _event_point(state, source, room, at, z):
    """(rid, point) of a visual occurrence: a cell `at` [c, r] (or a point [x, y] / [x, y, z]) in `room` (default the
    source's room, else the robot's) at height z (default 0.3 m) when given -- the more specific place (a dent in a
    floor, where a thing landed) --, else its source's box centre (an entity, person or pet); None when unknown."""
    box = None
    if isinstance(source, str) and (source in state["entities"] or source in state.get("people", {}) or
                                    source in state.get("pets", {})):
        box = sight.entity_box(state, source)
    if at is None:
        if box is None:
            return None
        centre = _box_centre(box)
        return box.rid, (centre[0], centre[1], centre[2] if z is None else float(z))
    rid = room if room in state["rooms"] else (box.rid if box is not None else state["robot"].get("room"))
    try:
        if len(at) == 2 and all(isinstance(v, int) for v in at):
            x, y = _cell_xy(at)
            return rid, (x, y, 0.3 if z is None else float(z))
        x, y = float(at[0]), float(at[1])
        pz = float(at[2]) if len(at) > 2 else (0.3 if z is None else float(z))
        return rid, (x, y, pz)
    except (TypeError, ValueError):
        return None


def sees_event(w, source=None, room=None, at=None, z=None, size_m=0.3):
    """The camera's event rule without writing anything (TIER1_SWEEP 6.2, visual notices): True / False whether the
    head camera in its current pose surely detects an occurrence at the source (or room / cell), None in room mode or
    when the occurrence has no place."""
    state = w.s
    if not _geometric(state):
        return None
    found = _event_point(state, source, room, at, z)
    if found is None:
        return None
    return _event_seen(w, state, source, found, size_m)


def _event_seen(w, state, source, found, size_m):
    rid, point = found
    seen = False
    if rid == state["robot"].get("room") and body.imaging(state):
        viewer = head_viewer(state)
        if sight.in_frame(viewer, point):
            sc = _scene(state, w)
            rel = source if isinstance(source, str) and sight.entity_box(state, source) is not None else None
            t = sight.point_transmission(state, sc.geom, sc.vs, sc.eye, point, rel=rel, cmap=None)
            if t > 0.0:
                beams = sight.robot_beams(state, (viewer.x, viewer.y, viewer.z), viewer.yaw, viewer.tilt)
                light = sight.light_at(state, rid, point, beams, sc.ambient, sc.geom)
                d = max(_dist3((viewer.x, viewer.y, viewer.z), point), sight.MIN_DIST_M)
                px_eff = sight.effective_px(sight.px_on_target(max(float(size_m), 0.01), d, viewer.focal_px), 1.0,
                                            sight.k_lens(sc.clarity), sight.k_light(light), 1.0,
                                            sight.k_haze(sc.room, d), 1.0)
                seen = light >= sight.LIGHT["detect"] - EPS and px_eff >= EVENT_PX - EPS
    return bool(seen)


def witness(w, kind, source=None, room=None, at=None, z=None, size_m=0.3, visual=None):
    """Whether the robot's head camera, in its current pose, sees a visual occurrence: in frame, in line of sight,
    lit to 0.05 and at least 24 px across for its size (a sure detection), with imaging on. Seen occurrences go to
    robot.view.events (this step, perceived form) and camera_log.events with seen true, missed ones to the log with
    seen false; a seen one is also posted as a "visual" notice (kernel v3.1). Returns True / False, or None when the
    occurrence has no place (nothing is logged) and in room mode."""
    state = w.s
    if not _geometric(state):
        return None
    found = _event_point(state, source, room, at, z)
    if found is None:
        return None
    rid, point = found
    seen = _event_seen(w, state, source, found, size_m)
    cl = w.sec("camera_log")
    events = cl.setdefault("events", [])
    events.append({"step": state.get("steps", 0), "time_s": state.get("time_s", 0.0), "kind": kind,
                   "source": source if isinstance(source, str) else None, "seen": bool(seen)})
    del events[:-MAX_LOG_EVENTS]
    if seen:
        x, y = _cell_xy(state["robot"].get("pos") or [0, 0])
        d = math.hypot(point[0] - x, point[1] - y)
        view = w.robot_m()["view"]
        if view.get("step") != state.get("steps", 0):
            view["frames"], view["events"], view["step"] = [], [], state.get("steps", 0)
        view["events"].append({"what": visual or kind, "id": source if isinstance(source, str) else None,
                               "bearing_deg": round(_bearing_body(state, point, x, y), 1), "dist": _dist_bucket(d)})
        del view["events"][:-MAX_VIEW_EVENTS]
        _visual_notice(w, kind, source, rid, visual)
    return bool(seen)


def _visual_notice(w, kind, source, rid, visual):
    """A seen visual occurrence as a "visual" notice (TIER1_SWEEP 6.1: camera geometric mode posts its visual events):
    the camera already decided it was seen, so the timeline stores it as perceived."""
    from . import timeline
    if not timeline.active(w):
        return
    state = w.s
    record = {"t": state.get("time_s", 0.0), "step": state.get("steps", 0), "kind": "visual", "room": rid,
              "data": {"what": visual or kind}}
    if isinstance(source, str) and timeline.source_perceived(w, source):
        record["source"] = source
    timeline.post(w, {"kind": "visual"}, perceived=record)


def visual_event(w, kind, *, source=None, room=None, at=None, z=None, size_m=0.3, visual=None, emit=True, **data):
    """A visual occurrence (8.6, C13): with emit, w.emit(kind, **data) exactly as before (the geometry arguments are
    keyword-only and never logged); in geometric mode also witness() it against the current head pose. Returns the
    event-log entry (None without emit)."""
    entry = w.emit(kind, **data) if emit else None
    if _geometric(w.s):
        witness(w, kind, source=source, room=room, at=at, z=z, size_m=size_m, visual=visual)
    return entry


# ----- action checks (9.2) and passive channels ----------------------------------------------------------------------
def in_frame(state, eid, world=None, current_pose=False):
    """True when a stationary frame of this step at the current eye (and, with current_pose, the current head pose)
    has the entity, person or pet in frame with line of sight (the gate of passive visual channels). Held things are
    always in frame. Room mode: perception's room-wide rule."""
    if not _geometric(state):
        agent = state.get("people", {}).get(eid) or state.get("pets", {}).get(eid)
        if agent is not None:
            return agent.get("location") == state["robot"].get("room") and \
                not (agent.get("concealed") and not agent.get("revealed"))
        from .perception import Perception
        return Perception(None).visible(state, eid)
    if _held(state, eid):
        return True
    sc = _scene(state, world)
    viewers = [(i, v) for i, v, _ in _current_viewers(state, sc)]
    if current_pose and any(i is not None for i, _ in viewers):
        viewers.append((None, head_viewer(state)))
    box, samples = sight.sample_transmissions(state, eid, sc.eye, sc.geom, sc.vs, sc.cmap)
    if box is None:
        return False
    samples = _carried_filter(state, sc.cmap, eid, (sc.rid, sc.x, sc.y, sc.z), samples)
    for _, viewer in viewers:
        if any(t > 0.0 and sight.in_frame(viewer, p) for p, t in samples):
            return True
    for _, wsc, viewer, _ in _wrist_scenes(state, sc):       # what a peek framed this step (via wrist)
        _, samples = sight.sample_transmissions(state, eid, wsc.eye, wsc.geom, wsc.vs, wsc.cmap)
        if any(t > 0.0 and sight.in_frame(viewer, p) for p, t in samples):
            return True
    return False


def acuity(env_or_state, eid):
    """{"level", "px", "view"} of an entity, person or pet in this step's stationary frames at the current eye (the
    view is its ViewInfo, 10.6), or None when it is not seen; None in room mode. env: uses the env's World memo."""
    if hasattr(env_or_state, "world"):
        state, world, perception = env_or_state.world.s, env_or_state.world, env_or_state.perception
    else:
        state, world, perception = env_or_state, None, None
    if not _geometric(state):
        return None
    sc = _scene(state, world)
    viewers = _current_viewers(state, sc)
    kind = "entity" if eid in state["entities"] else "agent"
    if kind == "entity":
        if perception is None:
            from .perception import Perception
            perception = Perception(None)
        if not perception.contained(state, eid):
            return None
    info = _best_info(_info(sc, eid, viewers, kind=kind), sc, eid, _wrist_scenes(state, sc), kind=kind)
    if info is None:
        return None
    return {"level": info["level"], "px": info["px"], "view": info}


_SEEN = weakref.WeakKeyDictionary()     # World -> {eid: (signature, acuity)} of entity_view_now


def _chain_signature(state, eid):
    """What containment and placement of a thing (and of everything holding it) the view reads."""
    entities = state["entities"]
    out, current, depth = [], eid, 0
    while current in entities and depth < 16:
        rec = entities[current]
        out.append((current, rec.get("location"), rec.get("relation"), tuple(rec.get("pos") or ()),
                    repr(rec.get("spos")), rec.get("shelf"), rec.get("open"), rec.get("cover_open"),
                    rec.get("covered_by"), rec.get("revealed"), repr(rec.get("size_m"))))
        current, depth = rec.get("location"), depth + 1
    return tuple(out)


def entity_view_now(env, eid):
    """acuity(env, eid) for the timeline's per-substep perceivability of conditions (kernel v3.1, TIER1_INTEGRATION
    1.3), memoized per entity by the watch signature (room geometry epoch, the robot's pose, sensors, lens, lights and
    load, the room's people and pets, ambient light and haze) plus this step's frames and the thing's own placement and
    containment chain: a quiet substep reuses the last answer. Computed every time while a domain view hook is
    registered (it may change the view unseen). None in room mode."""
    w = env.world
    state = w.s
    if not _geometric(state) or eid not in state["entities"]:
        return None
    sig = _watch_signature(w, state, state["robot"].get("room"), ())
    memo = None
    if sig is not None:
        sig = (sig, repr((state["robot"].get("view") or {}).get("frames")), _chain_signature(state, eid))
        memo = _SEEN.setdefault(w, {})
        found = memo.get(eid)
        if found is not None and found[0] == sig:
            return found[1]
    result = acuity(env, eid)
    if memo is not None:
        if len(memo) > 256:
            memo.clear()
        memo[eid] = (sig, result)
    return result


def agent_level_now(env, aid):
    """The level at which the head camera in its current pose sees a person or pet now ("shape" / "detect" /
    "recognize" / "identify"), or None (not in the robot's room, hiding, out of frame, imaging off): the per-substep
    view of the timeline's perceivability rules (kernel v3.1, TIER1_INTEGRATION 1.3). It reuses camera.watch's
    substep memo when nothing its in-view rule reads changed since. None in room mode."""
    w = env.world
    state = w.s
    if not _geometric(state) or not body.imaging(state):
        return None
    rid = state["robot"].get("room")
    here = _agent_ids(state, rid, "people") + _agent_ids(state, rid, "pets")
    if aid not in here:
        return None
    view = state["robot"].get("view") or {}
    log = state.get("camera_log") or {}
    agents = set(state.get("people", {})) | set(state.get("pets", {}))
    track = [t for t in view.get("track") or () if isinstance(t, str)]
    track_ids = [t for t in log.get("track_ids") or () if isinstance(t, str)]
    watched = sorted(set(here) | set(t for t in track + track_ids if t in agents))
    sig = _watch_signature(w, state, rid, watched)
    memo = _WATCHED.get(w)
    if sig is not None and memo is not None and memo[0] == sig and aid in memo[1]:
        return memo[1][aid]
    sc = _scene(state, w)
    viewer = head_viewer(state)
    beams = sight.robot_beams(state, (viewer.x, viewer.y, viewer.z), viewer.yaw, viewer.tilt)
    return _agent_level(state, sc, viewer, beams, aid)


def agent_identified(env, aid):
    """True when this step's frames at the current eye resolve a person or pet well enough to tell who it is
    (TIER1_CAMERA 7, 11.1): level "identify" (64 px, light 0.2, not backlit), the level at which the observation
    names them and shows their activity and cues. Below it the camera sees "a person" or "an animal", whatever the
    inspect asks. (Close-range details stay the domain inspectors' own: safety's responsiveness within 2 cells.)"""
    found = acuity(env, aid)
    return found is not None and found["level"] == "identify"


def inspect_check(state, eid, world=None):
    """(ok, reason, via) for a close look (9.2): the head camera from the current eye (it can turn) resolving the
    target to 160 px or more with line of sight and light 0.2 at it ("head"), else the wrist camera ("wrist", the
    caller adds 2 s). Room mode: (True, None, "room")."""
    if not _geometric(state):
        return True, None, "room"
    if not body.imaging(state):
        return False, CAMERA_OFF_TEXT, None
    if _held(state, eid):
        return True, None, "wrist"
    sc = _scene(state, world)
    found = _head_check(sc, eid) if eid in state["entities"] else None
    reason = NOT_SEEN_TEXT
    if found is not None:
        _, _, _, light, px_eff, _ = found
        if sight.inspect_capable(px_eff, light):
            return True, None, "head"
        reason = TOO_DARK_TEXT if light < sight.LIGHT["inspect"] - EPS else TOO_FAR_TEXT
    ok, why = wrist_access(state, eid)
    if ok:
        return True, None, "wrist"
    return False, reason if found is not None else (why or reason), None


def _text_m(state, eid, field):
    value = sight.text_m(state, eid, field)
    return DEFAULT_TEXT_M if value is None else value


def read_check(state, eid, fields, world=None):
    """(ok, reason, via, readable_fields) for reading printed text (9.2): the head camera (character height x focal /
    distance x lens >= 10 px, the field's face seen, light >= 0.25, no specular glare, lens clarity >= 0.4) reads what
    it can; when it leaves some of `fields` unread and the wrist is within reach (the gated face not down or against a
    solid, the LED lights it, clarity >= 0.4; a glossy item not held is judged for glare 20 degrees off its face
    normal from the robot's side), the wrist reads too and via is "wrist" (the caller adds its time) with what both
    read. readable_fields lists what was read. Room mode: (True, None, "room", fields)."""
    fields = [f for f in fields or []]
    if not _geometric(state):
        return True, None, "room", fields
    if not body.imaging(state):
        return False, CAMERA_OFF_TEXT, None, []
    sc = _scene(state, world)
    if sc.clarity < sight.READ_MIN_CLEAN - EPS:
        return False, DIRTY_TEXT, None, []
    held = _held(state, eid)
    reason = NOT_SEEN_TEXT
    head = []
    if not held:
        found = _head_check(sc, eid) if eid in state["entities"] else None
        if found is not None:
            _, point, d, light, _, _ = found
            viewer = _aimed_viewer(sc, point)
            faces = sight.faces_seen(state, viewer, eid, sc.cmap)
            readable, glare, small = [], False, False
            for field in fields:
                face = sight.gated_face(state, eid, field)
                if face is not None and face not in faces:
                    continue
                if not sight.readable(_text_m(state, eid, field), d, viewer.focal_px, light, sc.clarity):
                    small = small or light >= sight.LIGHT["read"] - EPS
                    continue
                if sight.specular(state, viewer, eid, face=face, sources=sc.sources, cmap=sc.cmap):
                    glare = True
                    continue
                readable.append(field)
            head = readable
            if readable and len(readable) == len(fields):
                return True, None, "head", readable
            if light < sight.LIGHT["read"] - EPS:
                reason = TOO_DARK_TEXT
            elif glare:
                reason = GLARE_TEXT
            elif small:
                reason = TOO_SMALL_TEXT
    ok, why = wrist_access(state, eid)
    if not ok:
        if head:
            return True, None, "head", head
        return False, reason if reason != NOT_SEEN_TEXT else (why or reason), None, []
    readable, glare = [], False
    faces = sight._faces(state, eid)
    for field in fields:
        face = sight.gated_face(state, eid, field)
        spec = faces.get(face) if face is not None else None
        if not held and face is not None:
            # the wrist turns to any face except one facing down onto its support or pressed against a solid
            if spec == "down" or (spec not in ("all", "up", None) and _face_against_solid(state, eid, spec, sc)):
                continue
        if not held and sight.view_props(state, eid).get("glossy"):
            wrist = _off_axis_viewer(state, sc, eid, spec if spec is not None else _default_face(state, eid))
            if wrist is not None and sight.specular(state, wrist, eid, face=face, sources=sc.sources, cmap=sc.cmap):
                glare = True
                continue
        readable.append(field)
    if set(readable) - set(head):
        return True, None, "wrist", [f for f in fields if f in readable or f in head]
    if head:
        return True, None, "head", head
    return False, GLARE_TEXT if glare else NOT_SEEN_TEXT, None, []


def _default_face(state, eid):
    """The face specular() judges by default: "up" for flat things, else the front (0)."""
    box = sight.entity_box(state, eid)
    if box is not None and box.z1 - box.z0 <= 0.3 * max(box.x1 - box.x0, box.y1 - box.y0):
        return "up"
    return 0.0


def _face_against_solid(state, eid, spec, sc):
    """A side face within 5 cm of a wall or a neighbour (sight's blocked-face rule)."""
    normal = sight._face_normal(state, eid, spec)
    placed = sight.entity_box(state, eid, sc.cmap)
    if normal is None or placed is None:
        return False
    return sight._face_blocked(state, eid, state["entities"][eid], placed, normal, sc.cmap)


def _aimed_viewer(sc, point):
    """The head camera at the current eye turned to a point."""
    yaw = _deg_to(sc.x, sc.y, point[0], point[1], facing(sc.state))
    tilt = math.degrees(math.atan2(point[2] - sc.z, max(math.hypot(point[0] - sc.x, point[1] - sc.y), 1e-6)))
    return _viewer_for(sc.hw, sc.rid, sc.x, sc.y, sc.z, yaw, tilt)


OFF_AXIS_DEG = 20.0         # a wrist reading a glossy face looks at it this far off its normal (9.2)


def _off_axis_viewer(state, sc, eid, spec):
    """The wrist camera 0.3 m from a face's centre, 20 degrees off its normal toward the robot, looking back at it
    (glossy reading, 9.2); None for an unplaced entity or a face without a normal."""
    box = sight.entity_box(state, eid, sc.cmap)
    normal = sight._face_normal(state, eid, spec) if spec not in (None, "all") else None
    if box is None or normal is None:
        return None
    cx, cy, cz = _box_centre(box)
    if spec == "up":
        cz = box.z1
    # the direction toward the robot's eye, made perpendicular to the normal
    tx, ty, tz = sc.x - cx, sc.y - cy, sc.z - cz
    dot = tx * normal[0] + ty * normal[1] + tz * normal[2]
    px, py, pz = tx - dot * normal[0], ty - dot * normal[1], tz - dot * normal[2]
    norm = math.sqrt(px * px + py * py + pz * pz)
    if norm < 1e-9:
        px, py, pz, norm = 1.0, 0.0, 0.0, 1.0
    c, s = math.cos(math.radians(OFF_AXIS_DEG)), math.sin(math.radians(OFF_AXIS_DEG))
    vx, vy, vz = c * normal[0] + s * px / norm, c * normal[1] + s * py / norm, c * normal[2] + s * pz / norm
    reach = 2.0 * WRIST_STANDOFF_M
    x, y, z = cx + vx * reach, cy + vy * reach, cz + vz * reach
    yaw = _deg_to(x, y, cx, cy, facing(state))
    tilt = math.degrees(math.atan2(cz - z, max(math.hypot(cx - x, cy - y), 1e-9)))
    return _viewer_for(sc.hw, box.rid, x, y, z, yaw, tilt, "wrist")


def seen_floor(state, world=None):
    """{(rid, (c, r))} of the floor cells this step's frames surveyed (cleaning's floor-soil and wet-floor cues); empty
    in room mode."""
    if not _geometric(state):
        return set()
    sc = _scene(state, world)
    cells = _seen_cells(state, sc, list(_frames(state)), world)
    return {(rid, cell) for rid, found in cells.items() for cell in found}


def specular(state, eid, world=None):
    """True when a glossy entity in a stationary frame of this step at the current eye mirrors a bright source into
    that frame (raking light for streaks and fingerprints, 6.2); False in room mode."""
    if not _geometric(state) or not sight.view_props(state, eid).get("glossy"):
        return False
    sc = _scene(state, world)
    for _, viewer, _ in _current_viewers(state, sc):
        if sight.sees_entity(state, viewer, eid, sc.geom, sc.cmap) > 0.0 and \
                sight.specular(state, viewer, eid, sources=sc.sources, cmap=sc.cmap):
            return True
    return False


def floor_visible_ahead(state, d_m):
    """True when the floor d_m ahead of the robot is not hidden by what it carries (sight.carried_occluder, 5.6)."""
    occ = sight.carried_occluder(state)
    return not sight.carried_hides(occ, sight.eye_height_m(state), 0.0, d_m, 0.0)


def device_frame(state, eid):
    """Ids a device camera (an entity with view.camera: doorbell, webcam, security camera) shows now: entities,
    people and pets of its room in its frame with line of sight, at least 24 px across, lit to 0.05 (or, for an "ir"
    camera in the dark, at least 0.4 m). Sorted; [] without a camera."""
    viewer = sight.device_viewer(state, eid)
    if viewer is None:
        return []
    spec = sight.view_props(state, eid).get("camera") or {}
    ambient = ambient_light(state, viewer.rid)
    geom = sight.room_geom(state, viewer.rid)
    cmap = sight.children_map(state)
    ids = [e for e in sorted(state["entities"]) if e != eid and root_room(state, e) == viewer.rid and
           state["entities"][e].get("location") != "robot"]
    ids += [a for g in ("people", "pets") for a in _agent_ids(state, viewer.rid, g)]
    out = []
    for target in ids:
        fraction = sight.sees_entity(state, viewer, target, geom, cmap)
        if fraction <= 0.0:
            continue
        box = sight.entity_box(state, target, cmap)
        if box is None:
            continue
        centre = _box_centre(box)
        size = _box_size(box)
        d = max(_dist3((viewer.x, viewer.y, viewer.z), centre), sight.MIN_DIST_M)
        light = sight.light_at(state, viewer.rid, centre, (), ambient, geom)
        px = sight.px_on_target(size, d, viewer.focal_px)
        if light >= sight.LIGHT["detect"] and sight.effective_px(px, fraction, 1.0, sight.k_light(light)) >= \
                sight.PX["detect_sure"] - EPS:
            out.append(target)
        elif spec.get("ir") and light < sight.LIGHT["detect"] and size >= sight.SIZE_IR_MIN_M - EPS:
            out.append(target)
    return sorted(out)


def bearing_to(state, xy):
    """Bearing of a room point (x, y) relative to the robot's body heading, wrapped to (-180, 180] (positive: to its
    right): for domain audio channels."""
    rid, x, y, _ = eye(state)
    return round(sight.wrap_deg(_deg_to(x, y, float(xy[0]), float(xy[1]), facing(state)) - facing(state)), 1)


def lidar(state, world=None):
    """The observation's lidar block {"height_m", "sector_deg", "ranges_m"} (5.7), or None without depth sensing
    (power off, depth hardware failed)."""
    if not body.depth_ok(state):
        return None
    sc = _scene(state, world)
    lid = sc.hw["lidar"]
    return {"height_m": lid["height_m"], "sector_deg": round(360.0 / lid["sectors"], 1),
            "ranges_m": sight.lidar_ring(state, sc.eye, facing(state), sc.geom)}


# ----- observation (10.6) ------------------------------------------------------------------------------------------
def compass(deg):
    """The 8-way word of a grid direction (0 east, 90 south)."""
    return COMPASS[int(((float(deg) % 360.0) + 22.5) // 45.0) % 8]


def view_dict(info):
    """The observation's "view" of a ViewInfo (oracle: exact values to 0.1 degree and 0.01 m)."""
    return {"bearing_deg": info["bearing_deg"], "elev_deg": info["elev_deg"], "dist": _dist_bucket(info["range_m"]),
            "range_m": info["range_m"], "px": info["px"], "occluded": round(1.0 - info["visible"], 2),
            "via": info["via"], "light": info["light"]}


def camera_block(state, sv):
    """obs["camera"]: the pose (its cell mirrors obs.robot.pos), this step's frame count, glare, ambient light at the
    eye, the current room's coverage, what the head tracks and the visual events seen this step (no camera, mic or
    lens keys: TIER1_INTEGRATION conflict 9)."""
    robot = state["robot"]
    view = robot.get("view") or {}
    log = state.get("camera_log") or {}
    yaw = facing(state)
    events = copy.deepcopy(view.get("events") or []) if view.get("step") == state.get("steps", 0) else []
    return {"pose": {"cell": copy.deepcopy(robot.get("pos")), "facing_deg": round(yaw, 1), "facing": compass(yaw),
                     "pan_deg": round(_finite(view.get("pan_deg"), 0.0), 1),
                     "tilt_deg": round(_finite(view.get("tilt_deg"), HW["tilt"]["look"]), 1),
                     "height_m": round(sv.eye[3], 2)},
            "frames": len(sv.frames), "glare": sv.glare, "light": round(sv.light, 2),
            "coverage": (log.get("coverage") or {}).get(sv.rid, 0.0),
            "tracking": sorted(t for t in view.get("track") or () if isinstance(t, str)), "events": events}


def lidar_block(sv):
    if sv.lidar is None:
        return None
    lid = HW["lidar"]
    return {"height_m": lid["height_m"], "sector_deg": round(360.0 / lid["sectors"], 1), "ranges_m": list(sv.lidar)}


def note_peek(w, eid, where):
    """Record a peek (camera_log.peeked["<id>:<where>"] = time; inside also looked_in) for the peek action
    (CAM-ACTIONS) and inspect where=."""
    state = w.s
    if not _geometric(state):
        return
    cl = w.sec("camera_log")
    cl.setdefault("peeked", {})["{}:{}".format(eid, where)] = state.get("time_s", 0.0)
    if where == "inside":
        cl.setdefault("looked_in", {})[eid] = state.get("time_s", 0.0)


# ----- camera verbs (CAM-ACTIONS; TIER1_CAMERA 9.1, 9.2) -------------------------------------------------------------
# systems/core.py owns the actions (look, face, peek and the geometric rules of inspect and read); these are their
# camera mechanics: directions and targets, head and body poses with their times, the frames they take, and the wrist
# camera's peeks.

def direction_deg(state, word):
    """Grid yaw of a direction word: north / east / south / west (grid convention: y points south), or forward /
    right / back / behind / left relative to the body's facing; None for any other word."""
    word = str(word or "").strip().lower()
    if word in COMPASS_DEG:
        return COMPASS_DEG[word]
    if word in RELATIVE_DEG:
        return round((facing(state) + RELATIVE_DEG[word]) % 360.0, 2)
    return None


def parse_cell(value):
    """[c, r] of a floor cell given as two integers in a list or as the text "[c, r]" / "c, r"; None otherwise."""
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(type(v) is int for v in value):
        return [value[0], value[1]]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        parts = [p.strip() for p in text.split(",")]
        if len(parts) == 2 and all(p.lstrip("-").isdigit() for p in parts):
            return [int(parts[0]), int(parts[1])]
    return None


def view_target(state, target, perception=None):
    """Where the target of look or face is: (kind, id, point) for a thing ("entity"), person or pet ("agent") in the
    robot's room that no container, cover or concealment hides (perception.contained; it need not be in line of
    sight, 9.1), or a floor cell ("cell", None, its floor centre) of the room; None otherwise."""
    robot = state["robot"]
    rid = robot.get("room")
    cell = parse_cell(target)
    if cell is not None:
        room = state["rooms"].get(rid)
        if room is None or not geometry.in_grid(room, tuple(cell)):
            return None
        return "cell", None, _target_point(state, cell)
    if not isinstance(target, str):
        return None
    agent = state.get("people", {}).get(target) or state.get("pets", {}).get(target)
    if agent is not None:
        if agent.get("location") != rid or not agent.get("pos") or (agent.get("concealed") and
                                                                    not agent.get("revealed")):
            return None
        point = _target_point(state, target)
        return ("agent", target, point) if point is not None else None
    if target in state["entities"]:
        if perception is None:
            from .perception import Perception
            perception = Perception(None)
        if not perception.contained(state, target):
            return None
        point = _target_point(state, target)
        return ("entity", target, point) if point is not None else None
    return None


def agent_visible(state, aid, world=None):
    """True when the robot can make out a person or pet now (geometric mode; the action-level rule of can_see for
    agents): in its room, not concealed, and in line of sight from its eye in some direction, lit to 0.05 with
    imaging on (with imaging off: only as a depth shape). Room mode: in the robot's room and not concealed."""
    agent = state.get("people", {}).get(aid) or state.get("pets", {}).get(aid)
    if agent is None or agent.get("location") != state["robot"].get("room") or not agent.get("pos") or \
            (agent.get("concealed") and not agent.get("revealed")):
        return False
    if not _geometric(state):
        return True
    sc = _scene(state, world)
    if not sc.on:
        return False
    found = _head_check(sc, aid)
    if found is None:
        return False
    _, _, d, light, px_eff, size = found
    if sc.imaging and light >= sight.LIGHT["detect"] - EPS and px_eff >= sight.PX["detect_min"] - EPS:
        return True
    return bool(sc.depth and sight.ir_shape(size, d, 0.0, light))


def can_turn(state):
    """The body may turn in place now: not fallen, actuators working, not standing on a climbing aid, not stopped."""
    return _body_can_turn(state) and not state["robot"].get("stopped")


def look(ctx, target=None, point=None, pan=None, tilt=None, rows=1):
    """Take the frames of a look (9.1, geometric mode) and return its seconds:
      target None / "forward": one frame straight ahead (pan 0, the look tilt; kind "idle", the resting frame), 1.0 s;
      "around": frames at pans -150..150 every 60 degrees at the look tilt (rows=2: also at the low-row tilt), the
               head back straight ahead after, 5.4 s (8.3 s);
      "up" / "down": the tilt to HW.tilt up / down; "left" / "right": the pan to -90 / +90 (the other axis kept);
      "behind": the body turns 180 degrees (when it cannot turn, the head pans to its limit on its side);
      point (x, y, z) (a thing, person, pet or cell: view_target): the head aims at it, the body turning beyond the
               pan limit when it can;
      pan / tilt numbers (no target): the head to that pan (relative to the body; beyond the limit the body turns
               when it can) and tilt (clamped to the limits).
    Aims and turns take the time of the slowest axis at the hardware rates plus HW.capture_s; one frame of kind
    "look" each."""
    w = ctx.w
    state = w.s
    hwd = hw(state)
    head, tilts = hwd["head"], hwd["tilt"]
    capture = _finite(hwd.get("capture_s"), 0.4)
    lo, hi = head["tilt_limits_deg"]
    limit = head["pan_limit_deg"]
    turnable = can_turn(state)
    if point is not None:
        body_yaw, new_pan, new_tilt, motion = _aim_pose(state, point, turnable)
        _set_pose(w, facing_deg=body_yaw, pan=new_pan, tilt=new_tilt, aim=point)
        add_frame(w, "look")
        return round(motion + capture, 3)
    level = _clamp(tilts["look"], lo, hi)
    if target == "around":
        body_yaw = facing(state)
        for row_tilt in [tilts["look"]] + ([tilts["low_row"]] if rows == 2 else []):
            for pan_deg in LOOK_AROUND_PANS:
                add_frame(w, "look", yaw=body_yaw + _clamp(pan_deg, -limit, limit), tilt=_clamp(row_tilt, lo, hi))
        _set_pose(w, pan=0.0, tilt=level, aim=None)
        return LOOK_AROUND_ROWS2_S if rows == 2 else LOOK_AROUND_S
    if target in (None, "", "forward") and pan is None and tilt is None:
        # the resting frame straight ahead: the same "idle" frame end_step records at a pose the step left unframed
        _set_pose(w, pan=0.0, tilt=level, aim=None)
        add_frame(w, "idle")
        return LOOK_FORWARD_S
    view = state["robot"]["view"]
    pan_now, tilt_now = _finite(view.get("pan_deg"), 0.0), _finite(view.get("tilt_deg"), tilts["look"])
    body_yaw, turn = facing(state), 0.0
    new_pan, new_tilt = pan_now, tilt_now
    if target == "up":
        new_tilt = tilts["up"]
    elif target == "down":
        new_tilt = tilts["down"]
    elif target == "left":
        new_pan = -90.0
    elif target == "right":
        new_pan = 90.0
    elif target == "behind":
        if turnable:
            turn, body_yaw, new_pan = 180.0, (body_yaw + 180.0) % 360.0, 0.0
        else:
            new_pan = limit if pan_now >= 0.0 else -limit
    else:
        new_pan = pan_now if pan is None else float(pan)
        new_tilt = tilt_now if tilt is None else float(tilt)
        if abs(new_pan) > limit + EPS and turnable:
            turn, body_yaw, new_pan = abs(new_pan), (body_yaw + new_pan) % 360.0, 0.0
    new_pan, new_tilt = _clamp(new_pan, -limit, limit), _clamp(new_tilt, lo, hi)
    motion = max(turn / hwd["body_turn_dps"], abs(new_pan - pan_now) / head["pan_dps"],
                 abs(new_tilt - tilt_now) / head["tilt_dps"])
    _set_pose(w, facing_deg=body_yaw, pan=new_pan, tilt=new_tilt, aim=None)
    add_frame(w, "look")
    return round(motion + capture, 3)


def face(ctx, yaw, point=None):
    """Turn the body to grid yaw `yaw` (face, 9.1): the head pans back to 0 and, with a target point, tilts to aim at
    it (else the tilt stays and the aim clears); one "look" frame. Returns the seconds: the turn at
    HW.body_turn_dps plus 0.3 s."""
    w = ctx.w
    state = w.s
    hwd = hw(state)
    yaw = round(float(yaw) % 360.0, 2)
    turn = abs(sight.wrap_deg(yaw - facing(state)))
    new_tilt, aim = None, None
    if point is not None:
        _, x, y, z = eye(state)
        lo, hi = hwd["head"]["tilt_limits_deg"]
        new_tilt = _clamp(math.degrees(math.atan2(point[2] - z, max(math.hypot(point[0] - x, point[1] - y), 1e-6))),
                          lo, hi)
        aim = point
    _set_pose(w, facing_deg=yaw, pan=0.0, tilt=new_tilt, aim=aim)
    add_frame(w, "look")
    return round(turn / hwd["body_turn_dps"] + FACE_SETTLE_S, 3)


def set_track(w, ids):
    """robot.view.track = the given people and pets (sorted, unique): the head keeps them in frame (camera.watch).
    Returns the list."""
    state = w.s
    if not _geometric(state):
        return []
    new = sorted(set(str(i) for i in ids if i in state.get("people", {}) or i in state.get("pets", {})))
    if state["robot"]["view"].get("track") != new:
        w.robot_m()["view"]["track"] = new
    return new


def agents_in_frames(state, world=None):
    """People and pets this step's stationary head frames at the current eye detect (the oracle rule), sorted."""
    sc = _scene(state, world)
    viewers = _current_viewers(state, sc)
    out = []
    for group in ("people", "pets"):
        for aid in _agent_ids(state, sc.rid, group):
            info = _info(sc, aid, viewers, kind="agent")
            if info is not None and info["detected"]:
                out.append(aid)
    return sorted(out)


PEEK_RELATION = {"under": "under", "behind": "behind", "inside": "in", "top": "on"}


def peek_access(state, eid, where):
    """(ok, reason, lift) of a peek at eid (9.1): the wrist rules (wrist_access with `where`: imaging, reach, under
    needs crouching or kneeling and a 6 cm gap, behind a 5 cm gap, inside an open, transparent or open-shelved
    container, a top within reach + 0.2 m), and whether the wrist lifts the target -- a light portable support (C9)
    -- to look under or behind it where the posture or the gap would not do (behind: a support standing out in the
    room with free floor at its back needs no lifting either). A held thing has no under or behind."""
    rec = state["entities"].get(eid)
    if rec is None:
        return False, NOT_SEEN_TEXT, False
    if where in ("under", "behind") and _held(state, eid):
        return False, PUT_DOWN_TEXT, False
    if where == "inside" and not (sight._holds_inside(rec) or rec.get("container")):
        return False, NOTHING_INSIDE_TEXT, False
    ok, reason = wrist_access(state, eid, where)
    if not ok:
        return False, reason, False
    lift = False
    if where == "under":
        gap = _finite(sight.view_props(state, eid).get("clear_below_m"), 0.0)
        low = state["robot"].get("posture", "standing") in ("crouching", "kneeling")
        lift = not (low and gap >= UNDER_GAP_M - EPS)
    elif where == "behind":
        gap_ok = _finite(sight.view_props(state, eid).get("behind_gap_m"), 0.0) >= BEHIND_GAP_M - EPS
        lift = not gap_ok and not _back_side_free(state, eid) and _liftable(rec)
    return True, None, lift


def _pose(x, y, z, tx, ty, tz, default_yaw):
    """(x, y, z, yaw, tilt) of a camera at (x, y, z) looking at (tx, ty, tz), rounded like stored frames."""
    flat = math.hypot(tx - x, ty - y)
    yaw = math.degrees(math.atan2(ty - y, tx - x)) if flat > 1e-9 else default_yaw
    tilt = math.degrees(math.atan2(tz - z, max(flat, 1e-6)))
    return round(x, 3), round(y, 3), round(z, 3), round(yaw % 360.0, 2), round(tilt, 2)


def peek_poses(state, eid, where, lift=False):
    """Wrist camera poses [(x, y, z, yaw, tilt)] of a peek at eid (9.1), from the target's footprint edge nearest the
    robot, WRIST_STANDOFF_M short of it:
      under: just above the floor under the support (half its clearance, 3-12 cm), looking straight in;
      behind: slid into the gap behind the support from its open end nearest the robot, at 15 cm, looking along it
              (from above the gap, looking down, when walls close both ends);
      inside: 0.35 m in front of a front opening, per shelf band and bay of the opening, looking in, or above a
              top opening's rim looking down;
      top: 0.25 m above the top, looking at its middle; closely: at mid-height, looking at it;
      lift (under / behind a light support): above the lifted support, looking down where it rests.
    [] when the target is not placed in the robot's room."""
    rid, ex, ey, _ = eye(state)
    box = sight.entity_box(state, eid)
    if box is None or box.rid != rid:
        return []
    face_yaw = facing(state)
    cx, cy, cz = _box_centre(box)
    nx, ny = _clamp(ex, box.x0, box.x1), _clamp(ey, box.y0, box.y1)
    dist = math.hypot(ex - nx, ey - ny)
    if dist > 1e-9:
        ox, oy = (ex - nx) / dist, (ey - ny) / dist
    else:
        ox, oy = -math.cos(math.radians(face_yaw)), -math.sin(math.radians(face_yaw))
    sx, sy = nx + ox * WRIST_STANDOFF_M, ny + oy * WRIST_STANDOFF_M
    deep = _finite(hw(state)["wrist"].get("deep_m"), WRIST_RANGE_M)
    if lift and where in ("under", "behind"):
        if where == "behind":
            _, _, _, _, gx, gy = sight._behind_gap(state, eid, box, _behind_gap_m(state, eid))
            return [_pose(sx, sy, box.z1 + PEEK_LIFT_ABOVE_M, gx, gy, box.z0, face_yaw)]
        return [_pose(sx, sy, box.z1 + PEEK_LIFT_ABOVE_M, cx, cy, box.z0, face_yaw)]
    if where == "under":
        gap = _finite(sight.view_props(state, eid).get("clear_below_m"), 0.0)
        z = box.z0 + _clamp(gap * 0.5, PEEK_LOW_MIN_M, PEEK_LOW_MAX_M)
        return [_pose(sx, sy, z, nx - ox * deep, ny - oy * deep, box.z0 + 0.03, face_yaw)]
    if where == "behind":
        return [_behind_pose(state, eid, box, ex, ey, face_yaw)]
    if where == "inside":
        return _inside_poses(state, eid, box, face_yaw)
    if where == "top":
        return [_pose(sx, sy, box.z1 + PEEK_TOP_ABOVE_M, cx, cy, box.z1, face_yaw)]
    return [_pose(sx, sy, max(0.05, cz), cx, cy, cz, face_yaw)]


def _behind_gap_m(state, eid):
    """The depth of the gap behind a support as sight lays out what is behind it: its behind_gap_m, or the depth of
    the largest thing behind it."""
    gap = _finite(sight.view_props(state, eid).get("behind_gap_m"), 0.0)
    back_y = sight._axis_front(sight.front_deg(state, eid)) in (90, 270)
    for kid in sorted(k for k, r in state["entities"].items() if r.get("location") == eid and
                      r.get("relation") == "behind"):
        w, d, _ = sight._dims(state, kid, state["entities"][kid])
        fw, fd = (d, w) if int(state["entities"][kid].get("rot", 0) or 0) % 180 == 90 else (w, d)
        gap = max(gap, fd if back_y else fw)
    return max(gap, BEHIND_GAP_M)


def _behind_pose(state, eid, box, ex, ey, face_yaw):
    """The wrist pose of a peek behind a support: in the gap's line just past its open end nearest the robot, at
    15 cm, looking along the gap; above the gap looking down when walls close both ends."""
    x0, y0, x1, y1, gx, gy = sight._behind_gap(state, eid, box, _behind_gap_m(state, eid))
    room = state["rooms"][box.rid]
    cols, rows = geometry.grid_size(room)
    width, depth = cols * CELL, rows * CELL
    back = (sight._axis_front(sight.front_deg(state, eid)) + 180) % 360
    if back in (90, 270):          # the gap runs along x
        ends = [((x0 - WRIST_STANDOFF_M, gy), (x1, gy)), ((x1 + WRIST_STANDOFF_M, gy), (x0, gy))]
    else:
        ends = [((gx, y0 - WRIST_STANDOFF_M), (gx, y1)), ((gx, y1 + WRIST_STANDOFF_M), (gx, y0))]
    open_ends = [(start, far) for start, far in ends if 0.0 < start[0] < width and 0.0 < start[1] < depth]
    z = box.z0 + PEEK_BEHIND_Z_M
    if open_ends:
        start, far = min(open_ends, key=lambda e: (math.hypot(e[0][0] - ex, e[0][1] - ey), e[0]))
        return _pose(start[0], start[1], z, far[0], far[1], box.z0 + 0.05, face_yaw)
    return _pose(gx, gy, box.z1 + PEEK_LIFT_ABOVE_M, gx, gy, box.z0, face_yaw)


def _inside_poses(state, eid, box, face_yaw):
    """Wrist poses of a peek inside a container. A top opening: above the middle of the rim, high enough for the whole
    rim to fit the wrist's field of view (at least 0.15 m, at most 0.5 m), looking down. A front opening: in front of
    it at PEEK_INSIDE_STANDOFF_M, one pose per shelf band (its middle height) and per bay (the opening split into the
    widths the wrist frames there), looking straight in at the back; at most PEEK_MAX_FRAMES, spread evenly."""
    info = sight._container_info(state, eid, box)
    half = math.radians(max(5.0, _finite(hw(state)["wrist"].get("hfov_deg"), 87.0) / 2.0 - PEEK_FOV_MARGIN_DEG))
    cx, cy, _ = _box_centre(box)
    if info.opening == "top":
        rim = 0.5 * math.hypot(box.x1 - box.x0, box.y1 - box.y0)
        above = _clamp(rim / math.tan(half), PEEK_RIM_ABOVE_M, PEEK_RIM_MAX_M)
        return [_pose(cx, cy, box.z1 + above, cx, cy, box.z0, face_yaw)]
    rad = math.radians(info.front)
    nx, ny = int(round(math.cos(rad))), int(round(math.sin(rad)))
    lx, ly = ny, -nx                    # along the opening
    px = box.x1 if nx > 0 else box.x0 if nx < 0 else cx
    py = box.y1 if ny > 0 else box.y0 if ny < 0 else cy
    stand = PEEK_INSIDE_STANDOFF_M
    bays = max(1, int(math.ceil(info.lat_w / (2.0 * stand * math.tan(half)) - 1e-9)))
    offsets = [-info.lat_w / 2.0 + (k + 0.5) * info.lat_w / bays for k in range(bays)]
    levels = sorted(set([box.z0, box.z1] + [s for s in info.shelves if box.z0 + 1e-6 < s < box.z1 - 1e-6]))
    poses = []
    for z in [(a + b) / 2.0 for a, b in zip(levels, levels[1:])]:
        for off in offsets:
            poses.append(_pose(px + nx * stand + lx * off, py + ny * stand + ly * off, z,
                               px - nx * info.dep_d + lx * off, py - ny * info.dep_d + ly * off, z, face_yaw))
    if len(poses) > PEEK_MAX_FRAMES:
        poses = [poses[int(round(i * (len(poses) - 1) / float(PEEK_MAX_FRAMES - 1)))] for i in range(PEEK_MAX_FRAMES)]
    return poses


def peek(ctx, eid, where, lift=False):
    """A wrist-camera peek at eid (9.1; geometric mode; the caller checked peek_access): wrist frames of kind "peek"
    (peek_poses) carrying {"target", "where", "lift"}, whose detections the step's observation lists (via "wrist");
    `revealed` set on the things under / behind eid those frames see (within HW.wrist.deep_m of the reachable
    edge: deeper ones stay hidden); camera_log.peeked (and, inside, looked_in). Returns the ids of eid's things
    (by where's relation; closely: eid itself) the frames see, sorted."""
    w = ctx.w
    state = w.s
    if not _geometric(state):
        return []
    rid = state["robot"].get("room")
    extra = {"target": eid, "where": where, "lift": eid if lift else None}
    frames = [list(add_frame(w, "peek", yaw=yaw, tilt=tilt, cam="wrist", at=(rid, x, y, z), extra=extra))
              for x, y, z, yaw, tilt in peek_poses(state, eid, where, lift)]
    seen = []
    relation = PEEK_RELATION.get(where)
    if frames:
        cmap = sight.children_map(state)
        geom = sight.room_geom(state, rid, w)
        hwd = hw(state)
        scenes = []
        for frame in frames:
            viewer = _frame_viewer(hwd, frame)
            scenes.append((frame, viewer, _wrist_viewshed(geom, viewer)))
        if relation is None:
            ids = [eid]
        else:
            ids = [k for k in cmap.get(eid, ()) if state["entities"][k].get("relation", "on") == relation]
        for k in ids:
            if _held(state, k) or any(_sees(state, f, v, geom, vs, k, cmap)[0] > 0.0 for f, v, vs in scenes):
                seen.append(k)
    if relation in ("under", "behind"):
        for k in seen:
            if not state["entities"][k].get("revealed"):
                w.mut(k)["revealed"] = True
    note_peek(w, eid, where)
    return sorted(seen)
