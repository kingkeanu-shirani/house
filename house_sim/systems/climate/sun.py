"""Sun position, solar gain through windows, direct-beam ray geometry (glare) and outdoor shade (fidelity G).

Room frame: x grows east (columns), y grows south (rows), z up; a window's wall follows its
``facing_deg`` (0 north wall y=0, 90 east wall x=W, 180 south wall y=D, 270 west wall x=0) and the
window spans ``size_m[0]`` along that wall at its cell, from ``mount_height_m`` (sill) to sill +
``size_m[2]`` (head). A point is in the direct beam when the ray from it toward the sun leaves the
room through a window aperture (this is how sun patches, screen glare and sun in a viewer's eyes are
decided). Outdoors, a point is shaded when the ray toward the sun hits a shade caster first: a house
wall along a room edge (``room.shade_walls``), a tree canopy or an open patio umbrella (discs).

Solar model: declination/hour-angle sun position at ``clock.latitude`` (default 40 N, solar noon
12:30 clock time, matching the mechanisms domain); clear-sky direct normal irradiance from the
Meinel air-mass model DNI = 1353 * 0.7^(AM^0.678) W/m2; cloud factors reduce it.
"""
import math

from ...kernel import clock
from ...kernel.plugins import HOOKS, after, hook
from . import common as C

SKY = {"clear": 1.0, "wind": 0.9, "cloudy": 0.3, "fog": 0.15, "rain": 0.08, "storm": 0.03, "snow": 0.2}


def latitude(state):
    return C.num((state.get("clock") or {}).get("latitude"), 40.0)


def sun_position(state):
    """(azimuth_deg clockwise from north, altitude_deg)."""
    now = clock.now(state)
    day = now.timetuple().tm_yday
    decl = 23.44 * math.sin(math.radians(360.0 * (284 + day) / 365.0))
    lat_deg = latitude(state)
    if (state.get("clock") or {}).get("hemisphere") == "south" and lat_deg > 0:
        lat_deg = -lat_deg
    lat = math.radians(lat_deg)
    noon = C.num((state.get("clock") or {}).get("solar_noon_h"), 12.5)
    hour_angle = math.radians(15.0 * (clock.hour(state) - noon))
    d = math.radians(decl)
    sin_alt = math.sin(lat) * math.sin(d) + math.cos(lat) * math.cos(d) * math.cos(hour_angle)
    alt = math.degrees(math.asin(C.clamp(sin_alt, -1.0, 1.0)))
    cos_az = (math.sin(d) - math.sin(lat) * sin_alt) / max(1e-6, math.cos(lat) * math.cos(math.radians(alt)))
    az = math.degrees(math.acos(C.clamp(cos_az, -1.0, 1.0)))
    if hour_angle > 0:
        az = 360.0 - az
    return round(az, 2), round(alt, 2)


def sky_factor(state):
    return SKY.get((state.get("weather") or {}).get("condition", "clear"), 0.6)


def dni(state, alt=None):
    """Direct normal irradiance W/m2 reaching the ground (0 at night)."""
    if alt is None:
        alt = sun_position(state)[1]
    if alt <= 0.5:
        return 0.0
    am = 1.0 / max(0.05, math.sin(math.radians(alt)))
    return 1353.0 * 0.7 ** (am ** 0.678) * sky_factor(state)


def diffuse_vertical(state, alt=None):
    """Diffuse sky radiation on a vertical surface, W/m2 (~ 10-15% of the clear-sky beam plus overcast glow)."""
    if alt is None:
        alt = sun_position(state)[1]
    if alt <= 0.0:
        return 0.0
    clear = 1353.0 * 0.7 ** ((1.0 / max(0.05, math.sin(math.radians(alt)))) ** 0.678)
    s = sky_factor(state)
    return clear * (0.07 + 0.08 * (1.0 - s)) * min(1.0, alt / 15.0)


def incidence_cos(az, alt, facing):
    """cos of the angle between the sun ray and a vertical window's outward normal."""
    if facing is None or alt <= 0:
        return 0.0
    return max(0.0, math.cos(math.radians(alt)) * math.cos(math.radians(az - facing)))


def to_sun(az, alt):
    """Horizontal unit components toward the sun (x east, y south) and the vertical rise per metre."""
    a = math.radians(az)
    return math.sin(a), -math.cos(a), math.tan(math.radians(max(0.01, alt)))


def cover_transmission(window, for_light=True):
    """Fraction of the direct beam passing the window's coverings (0..1)."""
    covered = C.clamp(C.num(window.get("covered")), 0.0, 1.0)
    return 1.0 - (0.95 if for_light else 0.55) * covered


def solar_gain_w(state, window, az, alt, beam, diffuse=None):
    """Heat gain W through one window (direct + diffuse), after glass and coverings (diffuse: precomputed
    diffuse_vertical(state, alt) when the caller evaluates many windows)."""
    facing = C.window_facing(window)
    if facing is None or alt <= 0:
        return 0.0
    area = C.window_glass_m2(window)
    direct = beam * incidence_cos(az, alt, facing)
    if diffuse is None:
        diffuse = diffuse_vertical(state, alt)
    return area * C.SHGC * (direct + diffuse) * cover_transmission(window, for_light=False)


# ---------------------------------------------------------------------------------------------
# window apertures and beam hits

def wall_of(facing):
    if facing is None:
        return None
    f = facing % 360.0
    return min(((0.0, "N"), (90.0, "E"), (180.0, "S"), (270.0, "W"), (360.0, "N")), key=lambda t: abs(t[0] - f))[1]


def aperture(state, wid):
    """(wall, along_m, half_width_m, sill_m, head_m, room_w, room_d) for a window, or None."""
    rec = state["entities"].get(wid) or {}
    rid = rec.get("location")
    room = state["rooms"].get(rid)
    if room is None or not rec.get("pos"):
        return None
    wall = wall_of(C.window_facing(rec))
    if wall is None:
        return None
    rw, rd, _ = C.room_dims(room)
    cx, cy = C.cell_center(rec["pos"])
    size = rec.get("size_m") or [1.0, 0.1, 1.2]
    sill = C.num(rec.get("mount_height_m"), 0.9)
    head = sill + C.num(size[2], 1.2)
    along = cx if wall in ("N", "S") else cy
    return wall, along, C.num(size[0], 1.0) / 2.0, sill, head, rw, rd


def beam_hits(state, wid, x, y, z, az, alt, apertures=None):
    """True if the ray from (x, y, z) toward the sun exits through window wid (apertures: optional memo
    {wid: aperture} - window geometry is fixed, so callers evaluating many points may share one)."""
    if apertures is None:
        ap = aperture(state, wid)
    else:
        if wid not in apertures:
            apertures[wid] = aperture(state, wid)
        ap = apertures[wid]
    if ap is None or alt <= 0.5:
        return False
    wall, along, half, sill, head, rw, rd = ap
    sx, sy, rise = to_sun(az, alt)
    if wall == "N":
        if sy >= -1e-6:
            return False
        t = (0.0 - y) / sy
        hit = x + t * sx
    elif wall == "S":
        if sy <= 1e-6:
            return False
        t = (rd - y) / sy
        hit = x + t * sx
    elif wall == "E":
        if sx <= 1e-6:
            return False
        t = (rw - x) / sx
        hit = y + t * sy
    else:
        if sx >= -1e-6:
            return False
        t = (0.0 - x) / sx
        hit = y + t * sy
    if t < 0:
        return False
    zh = z + t * rise
    return abs(hit - along) <= half and sill <= zh <= head


def room_windows(state, rid, w=None):
    """Window ids located in a room (w: a World, to use the static window index instead of a scan)."""
    ids = C.static_ids(w, "climate.windows", C.is_window) if w is not None else sorted(state["entities"])
    return [e for e in ids if (state["entities"].get(e) or {}).get("location") == rid
            and state["entities"][e].get("category") == "window"]


def beam_at(state, rid, x, y, z, az=None, alt=None, windows=None, apertures=None):
    """(intensity 0..1, window id) of direct sun reaching a point inside a room (after coverings)."""
    if az is None:
        az, alt = sun_position(state)
    if alt <= 0.5:
        return 0.0, None
    sky = sky_factor(state)
    if sky < 0.1:
        return 0.0, None
    best, which = 0.0, None
    for wid in (windows if windows is not None else room_windows(state, rid)):
        win = state["entities"][wid]
        if not beam_hits(state, wid, x, y, z, az, alt, apertures):
            continue
        strength = sky * cover_transmission(win, for_light=True) * min(1.0, incidence_cos(az, alt, C.window_facing(win)) * 1.6)
        if strength > best:
            best, which = strength, wid
    return round(best, 3), which


def sunlit_cells(state, rid, z=0.0, w=None):
    """Floor cells (or cells at height z) currently in a direct sun patch (G)."""
    from ...kernel import geometry
    room = state["rooms"].get(rid)
    if room is None:
        return []
    az, alt = sun_position(state)
    if alt <= 0.5 or sky_factor(state) < 0.1:
        return []
    windows = room_windows(state, rid, w)
    if not windows:
        return []
    cols, rows = geometry.grid_size(room)
    out = []
    for yy in range(rows):
        for xx in range(cols):
            x, y = C.cell_center((xx, yy))
            if beam_at(state, rid, x, y, z, az, alt, windows)[0] >= 0.15:
                out.append([xx, yy])
    return out


GLARE_CATEGORIES = {"tv", "monitor", "computer", "laptop", "screen", "tablet"}


def glare_point(state, eid):
    """(room, x, y, z) of a screen's centre for glare checks."""
    rec = state["entities"].get(eid) or {}
    rid, x, y = C.entity_xy(state, eid)
    from ...kernel import geometry
    base = geometry.height_m(state, eid) if eid in state["entities"] else 0.0
    size = rec.get("size_m") or [0.1, 0.1, 0.5]
    return rid, x, y, base + C.num(size[2], 0.5) * 0.5


# ---------------------------------------------------------------------------------------------
# outdoor shade

def _caster_candidate(rec):
    cat = rec.get("category")
    isa = rec.get("is_a") or []
    return isinstance(rec.get("shade"), dict) or cat in ("tree", "umbrella", "patio_umbrella") or "tree" in isa or "patio_umbrella" in isa


def shade_casters(state, rid, w=None):
    """[(kind, params)] of shade casters for an outdoor room (w: a World, to use a static index instead of a scan)."""
    room = state["rooms"].get(rid) or {}
    casters = []
    for wall in room.get("shade_walls") or []:
        casters.append(("wall", wall))
    ids = C.static_ids(w, "climate.shade_casters", _caster_candidate) if w is not None else sorted(state["entities"])
    for eid in ids:
        rec = state["entities"].get(eid)
        if rec is None or C.room_of(state, eid) != rid:
            continue
        shade = rec.get("shade")
        cat = rec.get("category")
        if isinstance(shade, dict) and shade.get("radius_m"):
            if shade.get("needs_open") and not (rec.get("open") or (rec.get("settings") or {}).get("canopy") == "open"):
                continue
            _, x, y = C.entity_xy(state, eid)
            casters.append(("disc", {"x": x + C.num(shade.get("dx")), "y": y + C.num(shade.get("dy")),
                                     "z": C.num(shade.get("height_m"), 2.2), "r": C.num(shade.get("radius_m")),
                                     "opacity": C.num(shade.get("opacity"), 1.0), "id": eid}))
        elif cat == "tree" or "tree" in (rec.get("is_a") or []):
            size = rec.get("size_m") or [4.0, 4.0, 6.0]
            _, x, y = C.entity_xy(state, eid)
            casters.append(("disc", {"x": x, "y": y, "z": C.num(size[2], 6.0) * 0.7, "r": C.num(size[0], 4.0) / 2.0,
                                     "opacity": 0.85, "id": eid}))
        elif cat in ("umbrella", "patio_umbrella") or "patio_umbrella" in (rec.get("is_a") or []):
            if not (rec.get("open") or (rec.get("settings") or {}).get("canopy") == "open"):
                continue
            size = rec.get("size_m") or [2.7, 2.7, 2.4]
            _, x, y = C.entity_xy(state, eid)
            casters.append(("disc", {"x": x, "y": y, "z": C.num(size[2], 2.4) - 0.2, "r": C.num(size[0], 2.7) / 2.0,
                                     "opacity": 1.0, "id": eid}))
    return casters


def shaded_at(state, rid, x, y, z, az, alt, casters=None):
    """Shade fraction 0..1 at an outdoor point (1 = fully shaded)."""
    if alt <= 0.5:
        return 1.0
    room = state["rooms"].get(rid) or {}
    rw, rd, _ = C.room_dims(room)
    sx, sy, rise = to_sun(az, alt)
    best = 0.0
    for kind, p in (casters if casters is not None else shade_casters(state, rid)):
        if kind == "wall":
            edge, height = p.get("edge"), C.num(p.get("height_m"), 3.0)
            lo, hi = C.num(p.get("from_m"), 0.0), C.num(p.get("to_m"), rw if edge in ("N", "S") else rd)
            if edge == "N" and sy < -1e-6:
                t = (0.0 - y) / sy
                along = x + t * sx
            elif edge == "S" and sy > 1e-6:
                t = (rd - y) / sy
                along = x + t * sx
            elif edge == "E" and sx > 1e-6:
                t = (rw - x) / sx
                along = y + t * sy
            elif edge == "W" and sx < -1e-6:
                t = (0.0 - x) / sx
                along = y + t * sy
            else:
                continue
            if t >= 0 and lo <= along <= hi and z + t * rise <= height:
                best = 1.0
        else:
            if p["z"] <= z:
                continue
            t = (p["z"] - z) / rise
            hx, hy = x + t * sx, y + t * sy
            if math.hypot(hx - p["x"], hy - p["y"]) <= p["r"]:
                best = max(best, p.get("opacity", 1.0))
        if best >= 1.0:
            break
    return best


def footprint_points(state, eid, z=0.45):
    """3x3 sample points over an entity's floor footprint (metres)."""
    rec = state["entities"].get(eid) or {}
    pos = rec.get("pos")
    if not pos:
        rid, x, y = C.entity_xy(state, eid)
        return [(x, y, z)]
    size = rec.get("size_m") or [0.5, 0.5, 0.9]
    w, d = C.num(size[0], 0.5), C.num(size[1], 0.5)
    if rec.get("rot", 0) in (90, 270):
        w, d = d, w
    x0, y0 = int(pos[0]) * C.CELL + max(0.0, (C.CELL - w) / 2.0 if w < C.CELL else 0.0), int(pos[1]) * C.CELL + max(0.0, (C.CELL - d) / 2.0 if d < C.CELL else 0.0)
    pts = []
    for fx in (0.15, 0.5, 0.85):
        for fy in (0.15, 0.5, 0.85):
            pts.append((x0 + fx * w, y0 + fy * d, z))
    return pts


def shade_fraction(state, eid, az=None, alt=None, casters=None):
    """Fraction of an outdoor item's footprint in shade now (direct sun blocked)."""
    rid = C.room_of(state, eid)
    if rid is None or not C.is_outdoor(state, rid):
        return 1.0
    if az is None:
        az, alt = sun_position(state)
    if alt <= 0.5 or sky_factor(state) < 0.15:
        return 1.0
    casters = casters if casters is not None else shade_casters(state, rid)
    pts = footprint_points(state, eid)
    return round(sum(shaded_at(state, rid, x, y, z, az, alt, casters) for x, y, z in pts) / len(pts), 3)


def shade_cells(state, rid, w=None):
    """Ground cells of an outdoor room currently in shade (what the robot sees)."""
    from ...kernel import geometry
    room = state["rooms"].get(rid)
    if room is None or not room.get("outdoor"):
        return []
    az, alt = sun_position(state)
    if alt <= 0.5 or sky_factor(state) < 0.15:
        return "all (no direct sun)"
    casters = shade_casters(state, rid, w)
    cols, rows = geometry.grid_size(room)
    out = []
    for yy in range(rows):
        for xx in range(cols):
            x, y = C.cell_center((xx, yy))
            if shaded_at(state, rid, x, y, 0.45, az, alt, casters) >= 0.8:
                out.append([xx, yy])
    return out


# ---------------------------------------------------------------------------------------------
# geometric camera hooks (tier-1 camera adoption: TIER1_CAMERA 6.1-6.2, TIER1_INTEGRATION 6 "climate")
#
# Read only by the geometric camera (kernel/sight.py light_at, bright_sources, glare); room mode never calls them, so
# its outputs are unchanged. The sun's direction is the same model as everywhere in this domain (compass azimuth ->
# the camera's grid angle through sight.grid_deg); a point is in the direct beam by the same window-aperture ray test
# as sun patches and screen glare.
SUN_PATCH_GAIN = 0.3        # a sunlit patch is this much brighter than the room's ambient (TIER1_CAMERA 6.1)
SUN_PATCH_MIN = 0.15        # beam strength from which a spot counts as sunlit (as sun_patches and screen glare)
SUN_FAR_M = 1000.0          # the sun as a bright source: far along its direction ...
SUN_RADIUS_DEG = 0.27       # ... with its angular radius
SUN_FULL_DNI = 900.0        # W/m2 of direct beam that counts as a full beam (a clear midday sun)
# Source levels are in the camera's light units, on the same 0..1 scale as the light at a target (1.0 = a brightly lit
# room; light_at is clamped there too, so a source above 1.0 would backlight every target in front of it, sunlit or
# not). A window lets in the sky's daylight x (1 - covered), the kernel fallback's level, plus while the sun shines
# through it the direct beam, capped at 1.0: a sunlit window outshines a north window in the morning, the evening, in
# winter and behind half-closed blinds. The sun itself outdoors is 1.0 at a full beam (its glare is the sun_beam rule).
SUN_BEAM_LEVEL = 1.0
SUN_LEVEL = 1.0
SOURCE_MAX = 1.0
_SUN_MEMO = {}
_WINDOWS = {}
_CASTERS = {}


def sun_now(state):
    """(azimuth_deg, altitude_deg, sky factor, DNI W/m2) of the current moment: a pure function of the time, the clock
    and the weather condition (memoized: the camera asks for it once per point it lights)."""
    clk = state.get("clock") or {}
    key = (state.get("time_s"), tuple(sorted((k, repr(v)) for k, v in clk.items())),
           (state.get("weather") or {}).get("condition"))
    hit = _SUN_MEMO.get(key)
    if hit is None:
        az, alt = sun_position(state)
        hit = (az, alt, sky_factor(state), dni(state, alt))
        if len(_SUN_MEMO) > 512:
            _SUN_MEMO.clear()
        _SUN_MEMO[key] = hit
    return hit


def sun_vector(az, alt):
    """Unit vector toward the sun in the room frame (x east, y south, z up): compass azimuth to grid degrees through
    sight.grid_deg (the camera convention, 0 = +x east, 90 = +y south)."""
    from ...kernel import sight
    g, a = math.radians(sight.grid_deg(az)), math.radians(alt)
    return (round(math.cos(a) * math.cos(g), 6), round(math.cos(a) * math.sin(g), 6), round(math.sin(a), 6))


def windows_of(state, rid):
    """(window ids of a room, aperture memo) from a per-entity-table index (windows never move; a created or destroyed
    entity changes the table's size or its last id and rebuilds it)."""
    ents = state["entities"]
    key = (len(ents), next(reversed(ents)) if ents else None)
    hit = _WINDOWS.get(id(ents))
    if hit is None or hit[0] != key or hit[1] is not ents:
        rooms = {}
        for eid in sorted(ents):
            rec = ents[eid]
            if rec.get("category") == "window" and rec.get("location") in state["rooms"]:
                rooms.setdefault(rec["location"], []).append(eid)
        if len(_WINDOWS) > 32:
            _WINDOWS.clear()
        hit = _WINDOWS[id(ents)] = (key, ents, rooms, {})
    return hit[2].get(rid, []), hit[3]


def _casters(state, rid):
    """Outdoor shade casters of a room for this moment (memoized per entity table, step and time: an action opens an
    umbrella, a wind gust may close one)."""
    ents = state["entities"]
    key = (id(ents), len(ents), state.get("steps"), state.get("time_s"), rid)
    hit = _CASTERS.get(key)
    if hit is None or hit[0] is not ents:
        if len(_CASTERS) > 64:
            _CASTERS.clear()
        hit = _CASTERS[key] = (ents, shade_casters(state, rid))
    return hit[1]


def direct_sun(state, rid, x, y, z):
    """Strength 0..1 of the direct sun at a point of a room (after coverings; outdoors 1 - shade), 0 without sun."""
    room = state["rooms"].get(rid)
    if room is None:
        return 0.0
    az, alt, sky, _ = sun_now(state)
    if alt <= 0.5 or sky < 0.15:
        return 0.0
    if room.get("outdoor"):
        return round(sky * (1.0 - shaded_at(state, rid, x, y, z, az, alt, _casters(state, rid))), 3)
    windows, apertures = windows_of(state, rid)
    if not windows:
        return 0.0
    return beam_at(state, rid, x, y, z, az, alt, windows, apertures)[0]


@hook("light_at")
def sun_patch_light(state, rid, x, y, z):
    """A spot in the direct sun (a sun patch through a window, an unshaded spot outdoors) is brighter than the room's
    ambient: + SUN_PATCH_GAIN x the beam strength (TIER1_CAMERA 6.1: a local boost on top of the uniform ambient)."""
    b = direct_sun(state, rid, x, y, z)
    return round(SUN_PATCH_GAIN * b, 4) if b >= SUN_PATCH_MIN else None


@hook("sun_beam")
def sun_beam_at_eye(state, rid, x, y, z):
    """The unit vector toward the sun when the point (a camera's eye) stands in the direct beam, else None
    (TIER1_CAMERA 6.2: the sun in frame washes the frame out and hides what lies within 20 degrees of it)."""
    if direct_sun(state, rid, x, y, z) < SUN_PATCH_MIN:
        return None
    az, alt, _, _ = sun_now(state)
    return sun_vector(az, alt)


def _others_answer():
    """True when another domain answers bright_sources (electrical lists lamps and screens and leaves the windows to
    climate): then climate lists only the daylight sources; alone, it also lists the lit lights exactly as the kernel
    fallback does (any answer replaces the whole fallback)."""
    me = __name__.rsplit(".", 1)[0]
    return any(not getattr(fn, "__module__", "").startswith(me) for _, _, fn in HOOKS.get("bright_sources", []))


def _window_point(state, rid, wid, rec):
    """Where a window's light comes from: the middle of its opening on its wall (climate's aperture geometry), at
    mid-height; the kernel fallback's place when it has no facing; None when it has no place."""
    ap = aperture(state, wid)
    if ap is not None:
        wall, along, _, sill, head, rw, rd = ap
        x, y = {"N": (along, 0.0), "S": (along, rd), "E": (rw, along), "W": (0.0, along)}[wall]
        return x, y, (sill + head) / 2.0
    from ...kernel import geometry, sight
    box = sight.entity_box(state, wid)
    if box is None or not rec.get("pos"):
        return None
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    cx, cy, cz = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0, (box.z0 + box.z1) / 2.0
    inward, _ = geometry.wall_inward(room, rec["pos"])
    if inward[0]:
        cx = 0.0 if inward[0] > 0 else cols * C.CELL
    else:
        cy = 0.0 if inward[1] > 0 else rows * C.CELL
    return cx, cy, cz


def _light_sources(state, rid):
    """Lit lights of a room as the kernel fallback lists them (brightness x dim_level at their box centre)."""
    from ...kernel import sight
    from ...kernel.predicates import root_room
    out = []
    grid = (state.get("utilities") or {}).get("power", True)
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("category") != "light" or not rec.get("on") or rec.get("burnt_out") or root_room(state, eid) != rid:
            continue
        if not ((rec.get("powered", True) and grid) or rec.get("battery_powered")):
            continue
        box = sight.entity_box(state, eid)
        if box is None:
            continue
        out.append({"x": round((box.x0 + box.x1) / 2.0, 3), "y": round((box.y0 + box.y1) / 2.0, 3),
                    "z": round((box.z0 + box.z1) / 2.0, 3),
                    "radius_m": round(max(box.x1 - box.x0, box.y1 - box.y0) / 2.0, 3),
                    "level": round(C.num(rec.get("brightness"), 0.8) * C.num(rec.get("dim_level"), 1.0), 3),
                    "kind": "light", "id": eid})
    return out


@hook("bright_sources")
def daylight_sources(state, rid):
    """The daylight sources of a room for glare, backlight and specular reflections (TIER1_CAMERA 6.2): every
    uncovered window at the sky's daylight x (1 - covered), as the kernel fallback, brightened by the direct beam
    when the sun shines through it (SUN_BEAM_LEVEL x DNI / SUN_FULL_DNI x the incidence on the glass x what the
    coverings let through), at most SOURCE_MAX; outdoors the sun itself, far along its direction (SUN_LEVEL x DNI /
    SUN_FULL_DNI)."""
    room = state["rooms"].get(rid)
    if room is None:
        return None
    out = []
    az, alt, sky, beam = sun_now(state)
    sun_up = alt > 0.5 and sky >= 0.15
    if room.get("outdoor"):
        if sun_up:
            vx, vy, vz = sun_vector(az, alt)
            rw, rd, _ = C.room_dims(room)
            out.append({"x": round(rw / 2.0 + SUN_FAR_M * vx, 3), "y": round(rd / 2.0 + SUN_FAR_M * vy, 3),
                        "z": round(SUN_FAR_M * vz, 3),
                        "radius_m": round(SUN_FAR_M * math.tan(math.radians(SUN_RADIUS_DEG)), 3),
                        "level": round(SUN_LEVEL * min(1.0, beam / SUN_FULL_DNI), 3), "kind": "sun", "id": None})
    else:
        daylight = clock.daylight(state)
        windows, _ = windows_of(state, rid)
        for wid in windows:
            rec = state["entities"][wid]
            level = daylight * (1.0 - C.num(rec.get("covered"), 0.0))
            if sun_up:
                inc = incidence_cos(az, alt, C.window_facing(rec))
                if inc > 0.05:
                    level += SUN_BEAM_LEVEL * min(1.0, beam / SUN_FULL_DNI) * min(1.0, inc * 1.6) *                         cover_transmission(rec, for_light=True)
            if level <= 0.0:
                continue
            point = _window_point(state, rid, wid, rec)
            if point is None:
                continue
            size = rec.get("size_m") or [1.0, 0.1, 1.2]
            out.append({"x": round(point[0], 3), "y": round(point[1], 3), "z": round(point[2], 3),
                        "radius_m": round(C.num(size[0], 1.0) / 2.0, 3), "level": round(min(SOURCE_MAX, level), 3),
                        "kind": "window", "id": wid})
    if not _others_answer():
        out.extend(_light_sources(state, rid))
    return out


# ---------------------------------------------------------------------------------------------
# fallback window coverings (generic shades built from core types with ``covers_window``)

def covering_value(rec):
    lowered = (rec.get("settings") or {}).get("lowered")
    if lowered is None:
        lowered = 0.0 if rec.get("open", True) else 1.0
    return C.clamp(C.num(lowered), 0.0, 1.0) * C.clamp(C.num(rec.get("opacity"), 0.9), 0.0, 1.0)


def sync_window_cover(w, wid):
    state = w.s
    if wid not in state["entities"]:
        return
    free = 1.0
    found = False
    for eid in C.static_ids(w, "climate.window_covers", lambda r: "covers_window" in r):
        rec = state["entities"].get(eid) or {}
        if rec.get("covers_window") == wid:
            found = True
            free *= 1.0 - covering_value(rec)
    if not found:
        return
    covered = round(1.0 - free, 3)
    if abs(C.num(state["entities"][wid].get("covered")) - covered) > 1e-6:
        w.mut(wid)["covered"] = covered


@after("set", order=47)
@after("open", order=47)
@after("close", order=47)
def _covering_changed(ctx, result):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    wid = rec.get("covers_window")
    if not wid:
        return
    if ctx.spec.name in ("open", "close"):
        m = ctx.w.mut(ctx.args["target"])
        if isinstance(m.get("settings"), dict) and "lowered" in m["settings"]:
            m["settings"]["lowered"] = 0.0 if ctx.spec.name == "open" else 1.0
    elif ctx.args.get("setting") == "lowered":
        m = ctx.w.mut(ctx.args["target"])
        m["open"] = C.num(ctx.args.get("value")) < 0.5
    sync_window_cover(ctx.w, wid)


@after("place", order=64)
def _crush_check(ctx, result):
    """Furniture set down on a crushable ground item (flower bed, seedlings) crushes it."""
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if rec.get("location") not in w.s["rooms"] or not rec.get("pos"):
        return
    from ...kernel import geometry
    cells = set(geometry.footprint(rec))
    for other in geometry.room_entities(w.s, rec["location"]):
        orec = w.s["entities"][other]
        if other == eid or not orec.get("crushable") or orec.get("crushed"):
            continue
        if cells & set(geometry.footprint(orec)):
            m = w.mut(other)
            m["crushed"] = True
            C.add_damage(m, "crushed", 0.8)
            w.stat("damage")
            C.log(w, "crushed", target=other, by=eid)
