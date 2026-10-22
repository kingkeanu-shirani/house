"""Camera adoption (kernel v3.0 camera; docs/design/TIER1_CAMERA.md 6.1, 6.2 and 19; TIER1_INTEGRATION 6 "electrical").

Room mode (the default) is unchanged by everything here: the hooks are read by the geometric camera only, and the
geometric branches below run only while the state carries `robot.view`.

  light_at        lamp pools: a lit lamp (a corded light: table, floor, desk, bedside and reading lamps) adds
                  0.3 x b x max(0, 1 - r / 1.5) at a point r metres from its shade (b = brightness x dim_level), on
                  top of the room ambient that already counts the lamp (TIER1_CAMERA 6.1: a local boost).
  bright_sources  the bright sources of a room for glare and backlight (6.2): lit lights (this domain's `lit`: a lamp
                  whose only bulb burnt out is dark; a shaded lamp at half its
                  level over its shade, other fixtures at brightness x dim_level, as the kernel fallback), screens
                  showing a picture (a TV 0.6, a monitor 0.45, a laptop or tablet 0.35, over half the screen width) and,
                  while no other domain answers the hook (climate adds sunlit windows when loaded), the uncovered
                  windows exactly as the kernel fallback lists them (any answer replaces the whole fallback).
  geometric_lux   the camera's exposure estimate of the light where the head camera looks (the electrical.light
                  observer's illuminance_lux in geometric mode): light_at at the aim point, so a lamp pool counts only
                  when it is in view; nothing with the cameras off.
  ir_line_clear   an IR remote works when the line from the robot's hand to the device's receiver passes
                  (sight.los, porous things let at least half through), not a 1.2 m-tall-obstacle cell test.

The index of lights, screens and windows is a memo per entity table (the table itself, its size and its first and last
ids: any creation or removal changes them), re-read for the dynamic fields on every call.
"""
import math

from ...kernel.plugins import HOOKS, hook
from . import common as C

POOL_GAIN = 0.3
POOL_RADIUS_M = 1.5
SHADE_FACTOR = 0.5          # a lampshade spreads the bulb's light: half the luminance, over the shade's radius
SCREEN_LEVEL = {"tv": 0.6, "monitor": 0.45, "computer": 0.35, "tablet": 0.35, "phone": 0.3}
IR_MIN_T = 0.5
_INDEX = {}


def _lamp(rec):
    return rec.get("category") == "light" and bool(rec.get("plug_required"))


def _screen(rec):
    return rec.get("category") in SCREEN_LEVEL or rec.get("type") in ("laptop", "laptop_barrel", "laptop_computer")


def _index(state):
    """(lights, lamps, screens, windows): ids in sorted order (the kernel fallback's order), memoized per entity
    table. The memo keeps the table itself (an identity check: a freed table's memory reused by another house can never
    pass for it) and its size and first and last ids (any creation or removal changes them)."""
    ents = state["entities"]
    key = (len(ents), next(iter(ents)) if ents else None, next(reversed(ents)) if ents else None)
    hit = _INDEX.get(id(ents))
    if hit is not None and hit[2] is ents and hit[0] == key:
        return hit[1]
    lights, lamps, screens, windows = [], [], [], []
    for eid in sorted(ents):
        rec = ents[eid]
        cat = rec.get("category")
        if cat == "light":
            lights.append(eid)
            if _lamp(rec):
                lamps.append(eid)
        elif cat == "window":
            windows.append(eid)
        elif _screen(rec):
            screens.append(eid)
    if len(_INDEX) >= 8:
        _INDEX.clear()
    out = (lights, lamps, screens, windows)
    _INDEX[id(ents)] = (key, out, ents)
    return out


def _powered(state, rec):
    return (rec.get("powered", True) and state.get("utilities", {}).get("power", True)) or rec.get("battery_powered")


def _lit(state, rec):
    return bool(rec.get("on") and not rec.get("burnt_out") and _powered(state, rec) and rec.get("lit", True))


def _room(state, eid):
    from ...kernel.predicates import root_room
    return root_room(state, eid)


def _box(state, eid):
    from ...kernel import sight
    return sight.entity_box(state, eid)


def _shade_point(box):
    """Where a lamp's light comes from: the middle of its box, at the shade (the top fifth of its height)."""
    return ((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, box.z0 + 0.8 * (box.z1 - box.z0))


@hook("light_at")
def lamp_pools(state, rid, x, y, z):
    """Local light from lit lamps near the point (TIER1_CAMERA 6.1)."""
    _, lamps, _, _ = _index(state)
    if not lamps:
        return None
    ents = state["entities"]
    total = 0.0
    for eid in lamps:
        rec = ents.get(eid)
        if rec is None or not _lit(state, rec) or _room(state, eid) != rid:
            continue
        box = _box(state, eid)
        if box is None:
            continue
        sx, sy, sz = _shade_point(box)
        r = math.sqrt((x - sx) ** 2 + (y - sy) ** 2 + (z - sz) ** 2)
        if r < POOL_RADIUS_M:
            b = C.num(rec.get("brightness"), 0.8) * C.num(rec.get("dim_level"), 1.0)
            total += POOL_GAIN * b * (1.0 - r / POOL_RADIUS_M)
    return round(total, 4) if total > 0 else None


def _others_answer():
    """True when another domain registers bright_sources (climate's sunlit windows): then this hook lists only its
    own sources; otherwise it also lists the kernel fallback's windows, which any answer would replace."""
    return any(not getattr(fn, "__module__", "").startswith(__name__.rsplit(".", 1)[0])
               for _, _, fn in HOOKS.get("bright_sources", []))


def _window_sources(state, rid, windows):
    """The kernel fallback's windows (sight.bright_sources without hooks): uncovered windows at daylight x (1 -
    covered), half the window wide, on their wall at mid-height."""
    from ...kernel import clock, geometry, sight
    out = []
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    daylight = clock.daylight(state)
    for eid in windows:
        rec = state["entities"].get(eid)
        if rec is None or _room(state, eid) != rid:
            continue
        box = _box(state, eid)
        if box is None:
            continue
        level = daylight * (1.0 - C.num(rec.get("covered"), 0.0))
        if level <= 0.0 or not rec.get("pos"):
            continue
        cx, cy, cz = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, (box.z0 + box.z1) / 2.0
        inward, _ = geometry.wall_inward(room, rec["pos"])
        if inward[0]:
            cx = 0.0 if inward[0] > 0 else cols * sight.CELL
        else:
            cy = 0.0 if inward[1] > 0 else rows * sight.CELL
        from ...kernel.phys import support
        size = support.resting_size(state, eid)
        out.append({"x": round(cx, 3), "y": round(cy, 3), "z": round(cz, 3),
                    "radius_m": round(max(float(size[0]), float(size[1])) / 2.0, 3),
                    "level": round(level, 3), "kind": "window", "id": eid})
    return out


def _screen_level(rec):
    if C.dead(rec) or rec.get("power_state") not in ("on",):
        return 0.0
    display = rec.get("display")
    if rec.get("category") in ("tv", "monitor") and not (isinstance(display, str) and display.startswith(("showing", "tuner"))):
        return 0.0
    kind = rec.get("category") if rec.get("category") in SCREEN_LEVEL else "computer"
    return SCREEN_LEVEL[kind]


@hook("bright_sources")
def bright_sources(state, rid):
    """Lit lights, bright screens and (alone) the kernel's windows of a room (TIER1_CAMERA 6.2)."""
    lights, _, screens, windows = _index(state)
    ents = state["entities"]
    out = [] if _others_answer() else _window_sources(state, rid, windows)
    for eid in lights:
        rec = ents.get(eid)
        if rec is None or not _lit(state, rec) or _room(state, eid) != rid:
            continue
        box = _box(state, eid)
        if box is None:
            continue
        level = C.num(rec.get("brightness"), 0.8) * C.num(rec.get("dim_level"), 1.0)
        radius = max(box.x1 - box.x0, box.y1 - box.y0) / 2.0
        if _lamp(rec):
            level *= SHADE_FACTOR
            x, y, z = _shade_point(box)
        else:
            x, y, z = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, (box.z0 + box.z1) / 2.0
        out.append({"x": round(x, 3), "y": round(y, 3), "z": round(z, 3), "radius_m": round(radius, 3),
                    "level": round(level, 3), "kind": "lamp" if _lamp(rec) else "light", "id": eid})
    for eid in screens:
        rec = ents.get(eid)
        if rec is None or _room(state, eid) != rid:
            continue
        level = _screen_level(rec)
        if level <= 0.0:
            continue
        box = _box(state, eid)
        if box is None:
            continue
        out.append({"x": round((box.x0 + box.x1) / 2.0, 3), "y": round((box.y0 + box.y1) / 2.0, 3),
                    "z": round((box.z0 + box.z1) / 2.0, 3),
                    "radius_m": round(max(box.x1 - box.x0, box.y1 - box.y0) / 2.0, 3),
                    "level": level, "kind": "screen", "id": eid})
    return out


def geometric(state):
    return isinstance((state.get("robot") or {}).get("view"), dict)


def geometric_lux(state):
    """Geometric camera mode: the exposure meter's reading (lux) of the light where the head camera looks -- the floor
    point its centre ray meets (at most 6 m out), else 3 m ahead at eye height -- or None with the cameras off."""
    from ...kernel import body, camera, geometry, sight
    if not body.imaging(state):
        return None
    rid, x, y, z = camera.eye(state)
    if rid not in state.get("rooms", {}):
        return None
    yaw, tilt = math.radians(camera.head_yaw(state)), camera.head_tilt(state)
    if tilt < -1.0:
        d, pz = min(6.0, max(0.3, z / math.tan(math.radians(-tilt)))), 0.0
    else:
        d, pz = 3.0, z
    cols, rows = geometry.grid_size(state["rooms"][rid])
    px = min(cols * sight.CELL - 0.01, max(0.01, x + d * math.cos(yaw)))
    py = min(rows * sight.CELL - 0.01, max(0.01, y + d * math.sin(yaw)))
    return int(round(sight.light_at(state, rid, (px, py, pz)) * C.LUX_PER_LEVEL))


def ir_line_clear(state, eid, cell=None):
    """Geometric mode: True when the IR beam from the robot's hand (at its cell, or at `cell` of its room) reaches the
    device's receiver (the bottom of its front, 10 % up its box) with at least half its strength (sight.los: solid
    things block, porous ones dim it)."""
    from ...kernel import body, camera, sight
    box = _box(state, eid)
    if box is None:
        return True
    rid, x, y, _ = camera.eye(state)
    if cell is not None:
        x, y = (int(cell[0]) + 0.5) * sight.CELL, (int(cell[1]) + 0.5) * sight.CELL
    hand = (x, y, float(body.profile(state)["carry"]["hand_z_m"]))
    target = ((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, box.z0 + 0.1 * (box.z1 - box.z0))
    held = tuple(h for h in (state["robot"].get("hands") or {}).values() if h)
    return sight.los(state, rid, hand, target, ignore=(eid, "robot") + held) >= IR_MIN_T
