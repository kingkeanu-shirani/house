"""Ceiling and portable fans: speed, direction, coast-down, airflow fields and their consequences.

Ceiling fan (climate_kind "ceiling_fan"):
* ``on`` is the wall-switch power gate (a core light_switch ``controls`` it), ``settings.speed``
  off/high/medium/low in pull-chain order (a chain pull cycles off -> high -> medium -> low -> off;
  mechanisms' ``press button=fan_chain`` applies ``cycle:speed``), ``settings.light`` off/on (light
  kit, ``light_chain``), ``settings.direction`` ccw (downdraft, summer) / cw (updraft, winter).
  control_kinds "pull_chain"/"slide_switch" force the physical controls; "remote" lets core ``set``
  work from across the room while holding the fan's RF remote.
* the motor runs when gated on, powered and speed != off; blade rpm follows the speed with a 15 s
  spin-up and a 25 s coast-down (190 rpm high -> below 5 rpm needs ~90 s after switching off).
* ``reverse_fan`` (this domain's verb) flips the motor-housing slide switch (the housing is ~2.3 m up:
  a step stool or ladder is needed; blades must be stopped: above 60 rpm the arm would enter the blade
  sweep (refused, safety); 5-60 rpm the motor is jolted (damage)) or uses the remote's reverse button.
* airflow at a person: downdraft high 1.6 / medium 1.1 / low 0.6 m/s within 1 m of the hub,
  ~1/r beyond; updraft gives 0.15-0.3 m/s and mixes stratified warm air down (destratification).

Portable fans (climate_kind "portable_fan", fan_type box | pedestal | tower | window):
* ``on`` (switch), settings speed low/medium/high, oscillate, aim_deg (0 north/-y, 90 east/+x ...).
* air speed at distance d along the axis v = v0 / (1 + d/1.5) inside the cone half-angle (box 18,
  pedestal 15, tower 25 deg; oscillation sweeps +/-40 deg at ~55 % average), Gaussian fall-off outside.
* a fan in an open window's cell aimed through it is a window fan: it forces its flow between the
  room and outdoors (intake when aimed into the room, exhaust when aimed out).
* consequences: loose papers (<= 50 g) in >= 1.2 m/s are blown off their surface; a lit candle in
  >= 0.6 m/s gutters (wax spatter, safety violation) and ignites combustibles within 0.5 m (fire);
  a sleeping infant in >= 0.5 m/s for 5 min is chilled (hazard); a fan running on a bed/sofa/cushion
  is unstable: while the kernel's furniture_tip law is deferred it topples off after a minute on medium/high
  (legacy rule); once the law is active each minute of rocking is a jolt the kernel's statics judge (physics.knock
  along the thrust axis: SOFT_BASE_ROCK_G 0.2 g medium / 0.3 g high against the fan's own b / 2z from FAN_STATICS:
  a box fan goes over at high, a pedestal or tower fan rides it out; or it walks across the cushion).
* cords: the cord (cord_length_m, default 1.8 m) must reach the outlet (or nearest wall); a cord
  crossing the room's walkway becomes a trip floor hazard, under a rug a heat hazard.
"""
import math

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.phys import lawbook
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before, hook
from . import common as C

CEILING_RPM = {"off": 0.0, "low": 80.0, "medium": 140.0, "high": 190.0}
CEILING_V = {"off": 0.0, "low": 0.6, "medium": 1.1, "high": 1.6}
CEILING_W = {"off": 0.0, "low": 15.0, "medium": 32.0, "high": 65.0}
CEILING_FLOW = {"off": 0.0, "low": 0.9, "medium": 1.7, "high": 2.6}
PORTABLE = {
    "box": {"v0": {"low": 1.8, "medium": 2.6, "high": 3.5}, "half": 18.0, "w": {"low": 45.0, "medium": 60.0, "high": 75.0},
            "flow": {"low": 0.5, "medium": 0.8, "high": 1.1}, "outlet_m": 0.3},
    "window": {"v0": {"low": 1.6, "medium": 2.4, "high": 3.2}, "half": 20.0, "w": {"low": 40.0, "medium": 55.0, "high": 70.0},
               "flow": {"low": 0.45, "medium": 0.7, "high": 1.0}, "outlet_m": 0.3},
    "pedestal": {"v0": {"low": 1.5, "medium": 2.2, "high": 3.0}, "half": 15.0, "w": {"low": 30.0, "medium": 40.0, "high": 50.0},
                 "flow": {"low": 0.35, "medium": 0.55, "high": 0.75}, "outlet_m": 1.1},
    "tower": {"v0": {"low": 1.2, "medium": 1.8, "high": 2.4}, "half": 25.0, "w": {"low": 25.0, "medium": 35.0, "high": 45.0},
              "flow": {"low": 0.25, "medium": 0.4, "high": 0.55}, "outlet_m": 0.8},
}
SPEED_RANK = {"off": 0, "low": 1, "medium": 2, "high": 3}
LIGHT_ITEM_CATS = {"paper", "document", "mail", "receipt", "napkin", "envelope", "letter", "magazine", "flyer", "note"}
UNSTABLE_BASES = {"bed", "sofa", "seat", "cushion", "pillow", "blanket"}
# furniture_tip adoption (TIER1_PHYSICS 10.3, kernel v3.0): a running fan on a soft base rocks with its motor's
# imbalance and the reaction to its thrust; the rocking is a horizontal acceleration of about this many g at the base
# (P-approx calibration: a 20 in box fan, b / 2z = 0.23 g along its thrust axis, stays up at medium and goes over at
# high; a heavy-based pedestal fan, 0.4 g, and a tower fan, 0.33 g, ride it out). The kernel's statics decide tip
# versus slide from the fan's own base and centre of mass (com_frac, base_m in climate.json).
SOFT_BASE_ROCK_G = {"low": 0.0, "medium": 0.2, "high": 0.3}
ROCK_PERIOD_S = 60.0       # the rocking builds up over a minute of running; each minute is one jolt for the kernel
# where a portable fan's mass sits (physics com_frac: centre-of-mass height / height) and the foot it pivots on (base_m):
# a box or window fan is a symmetric frame; a pedestal fan carries about 60 % of its 5 kg in a 35 cm weighted disc;
# a tower fan's motor sits low over a 28 cm plinth. Given to the kernel through a phys.props hook (records unchanged).
FAN_STATICS = {"box": {"com_frac": 0.5}, "window": {"com_frac": 0.5}, "pedestal": {"com_frac": 0.35, "base_m": 0.35},
               "tower": {"com_frac": 0.4, "base_m": 0.28}}


@hook("phys.props")
def fan_statics(record, phys):
    """Physics refinement for portable fans (TIER1_PHYSICS 7.3 phys.props; furniture_tip adoption): centre of mass and
    base from FAN_STATICS, so the kernel's tip threshold b / 2z is the fan's own; a record's explicit com_frac or
    base_m wins."""
    row = FAN_STATICS.get(record.get("fan_type"))
    if not row:
        return None
    out = {}
    for key, value in row.items():
        if record.get(key) is None:
            out[key] = value
    return out or None


fan_statics.applies = lambda record: record.get("climate_kind") == "portable_fan"


# ---------------------------------------------------------------------------------------------
# state

def ceiling_running(w, rec, eid=None):
    s = rec.get("settings") or {}
    if not rec.get("on", True) or s.get("speed", "off") == "off" or rec.get("motor_damaged") == "seized":
        return False
    return physics.is_powered(w, eid) if eid else True


def portable_running(w, rec, eid=None):
    if not rec.get("on") or rec.get("broken"):
        return False
    return physics.is_powered(w, eid) if eid else True


def ceiling_tick(w, eid, dt):
    """Blade rpm dynamics, light kit state and derived power for one ceiling fan."""
    rec = w.ent(eid)
    s = rec.get("settings") or {}
    running = ceiling_running(w, rec, eid)
    target = CEILING_RPM.get(s.get("speed", "off"), 0.0) if running else 0.0
    rpm = C.num(rec.get("rpm"))
    tau = 15.0 if target > rpm else 25.0
    new = target + (rpm - target) * math.exp(-dt / tau)
    if new < 2.0 and target == 0.0:
        new = 0.0
    lit = bool(rec.get("has_light")) and rec.get("on", True) and s.get("light", "off") == "on" and physics.is_powered(w, eid)
    power = (CEILING_W.get(s.get("speed", "off"), 0.0) if running else 0.0) + (C.num(rec.get("light_w"), 15.0) if lit else 0.0)
    changes = {}
    if abs(new - rpm) > 0.05:
        changes["rpm"] = round(new, 1)
    if bool(rec.get("spinning")) != (new >= 5.0):
        changes["spinning"] = new >= 5.0
    if bool(rec.get("light_lit")) != lit:
        changes["light_lit"] = lit
    if C.num(rec.get("power_w"), -1) != power:
        changes["power_w"] = power
    if bool(rec.get("running")) != running:
        changes["running"] = running
    if changes:
        w.mut(eid).update(changes)
    return running


def portable_tick(w, eid):
    rec = w.ent(eid)
    s = rec.get("settings") or {}
    spec = PORTABLE.get(rec.get("fan_type", "box"), PORTABLE["box"])
    running = portable_running(w, rec, eid)
    power = spec["w"].get(s.get("speed", "low"), 40.0) if running else 0.0
    changes = {}
    if bool(rec.get("running")) != running:
        changes["running"] = running
    if C.num(rec.get("power_w"), -1) != power:
        changes["power_w"] = power
    if changes:
        w.mut(eid).update(changes)
    return running


# ---------------------------------------------------------------------------------------------
# airflow fields

def fan_position(state, eid):
    rid, x, y = C.entity_xy(state, eid)
    return rid, x, y


def ceiling_airspeed(state, eid, x, y):
    rec = state["entities"][eid]
    s = rec.get("settings") or {}
    rpm = C.num(rec.get("rpm"))
    if rpm < 5.0:
        return 0.0
    level = rpm / 190.0
    _, fx, fy = fan_position(state, eid)
    r = math.hypot(x - fx, y - fy)
    if s.get("direction", "ccw") == "cw":
        return round(0.25 * level, 3)
    base = 1.6 * level
    return round(base if r <= 1.0 else max(0.1 * level, base / r), 3)


def _ang_diff(a, b):
    return abs(((a - b) + 180.0) % 360.0 - 180.0)


def bearing(dx, dy):
    """Compass bearing in the room frame (0 = -y north, 90 = +x east)."""
    return math.degrees(math.atan2(dx, -dy)) % 360.0


def portable_airspeed(state, eid, x, y):
    rec = state["entities"][eid]
    if not rec.get("running"):
        return 0.0
    s = rec.get("settings") or {}
    spec = PORTABLE.get(rec.get("fan_type", "box"), PORTABLE["box"])
    rid, fx, fy = fan_position(state, eid)
    dx, dy = x - fx, y - fy
    d = math.hypot(dx, dy)
    v0 = spec["v0"].get(s.get("speed", "low"), 1.8)
    if d < 0.25:
        return v0
    half = spec["half"]
    scale = 1.0
    if s.get("oscillate"):
        half += 40.0
        scale = 0.55
    off = _ang_diff(bearing(dx, dy), C.num(s.get("aim_deg"), 0.0))
    cone = 1.0 if off <= half else math.exp(-((off - half) / 12.0) ** 2)
    return round(v0 / (1.0 + d / 1.5) * cone * scale, 3)


def airspeed_at(state, rid, x, y, fans):
    """Combined air speed (m/s) at a point from running fans in the room (vector-free sum, capped)."""
    ceiling, portable = 0.0, 0.0
    for eid in fans:
        rec = state["entities"].get(eid) or {}
        if C.kind(rec) == "ceiling_fan":
            ceiling = max(ceiling, ceiling_airspeed(state, eid, x, y))   # one ceiling jet dominates
        elif C.kind(rec) == "portable_fan":
            portable += portable_airspeed(state, eid, x, y)
    return round(min(4.0, ceiling + portable), 3)


def window_fan_flow(state, eid, windows=None):
    """(window id, signed flow m3/s: + intake, - exhaust) when a portable fan works through an open window.
    windows: the room's window ids (the model passes its per-room list; None scans every entity)."""
    rec = state["entities"].get(eid) or {}
    if not rec.get("running"):
        return None, 0.0
    rid = C.room_of(state, eid)
    if rid is None or rec.get("location") != rid or not rec.get("pos"):
        return None, 0.0
    spec = PORTABLE.get(rec.get("fan_type", "box"), PORTABLE["box"])
    flow = spec["flow"].get((rec.get("settings") or {}).get("speed", "low"), 0.5)
    for wid in (windows if windows is not None else sorted(state["entities"])):
        win = state["entities"].get(wid) or {}
        if win.get("category") != "window" or win.get("location") != rid or not win.get("pos"):
            continue
        if geometry.chebyshev(tuple(win["pos"]), tuple(rec["pos"])) > 0:
            continue
        if C.window_open_area(win) < 0.08:
            continue
        facing = C.window_facing(win)
        if facing is None:
            continue
        aim = C.num((rec.get("settings") or {}).get("aim_deg"))
        if _ang_diff(aim, facing) <= 45.0:
            return wid, -flow
        if _ang_diff(aim, (facing + 180.0) % 360.0) <= 45.0:
            return wid, flow
    return None, 0.0


# ---------------------------------------------------------------------------------------------
# consequences of airflow on light objects, flames, infants, stability

def _light_or_flame(rec):
    """Static pre-filter: candles and light papers (<= 50 g) a fan can blow about."""
    cat = rec.get("category")
    return cat == "candle" or ((cat in LIGHT_ITEM_CATS or "loose_paper" in (rec.get("tags") or [])) and C.num(rec.get("mass_kg"), 1.0) <= 0.05)


def disturb(w, rid, fans, dt):
    """Blow papers off, disturb flames, topple unstable fans. Called each substep for rooms with running fans."""
    state = w.s
    if not fans:
        return
    for eid in C.static_ids(w, "climate.light_or_flame", _light_or_flame):
        rec = state["entities"].get(eid)
        if rec is None:
            continue
        cat = rec.get("category")
        flame = cat == "candle" and rec.get("lit")
        paper = (cat in LIGHT_ITEM_CATS or "loose_paper" in (rec.get("tags") or [])) and C.num(rec.get("mass_kg"), 1.0) <= 0.05
        if not (flame or paper):
            continue
        if C.room_of(state, eid) != rid or rec.get("location") == "robot":
            continue
        _, x, y = C.entity_xy(state, eid)
        v = airspeed_at(state, rid, x, y, fans)
        if flame and v >= 0.6:
            _flame_disturbed(w, eid, rid, x, y, v)
        elif paper and v >= 1.2 and rec.get("location") not in state["rooms"] and not rec.get("weighted_down"):
            _blow_paper(w, eid, rid, v)
    for eid in fans:
        rec = state["entities"].get(eid) or {}
        if C.kind(rec) != "portable_fan" or not rec.get("running"):
            continue
        parent = state["entities"].get(rec.get("location")) or {}
        if parent.get("category") in UNSTABLE_BASES or parent.get("upholstered"):
            if SPEED_RANK.get((rec.get("settings") or {}).get("speed", "low"), 1) >= 2:
                m = w.mut(eid)
                m["unstable_s"] = C.rnd(C.num(rec.get("unstable_s")) + dt, 1)
                if m["unstable_s"] >= ROCK_PERIOD_S:
                    if lawbook.active("furniture_tip"):
                        _rock_kernel(w, eid, rid)     # kernel owns it: statics tip / slide, impact, attribution
                    else:
                        _topple(w, eid, rid)


def _flame_disturbed(w, eid, rid, x, y, v):
    state = w.s
    rec = state["entities"][eid]
    if rec.get("_flame_disturbed_logged"):
        return
    m = w.mut(eid)
    m["_flame_disturbed_logged"] = True
    m["guttering"] = True
    C.log(w, "flame_disturbed", target=eid, airspeed=v)
    C.visual(w, "flame_guttering", eid, size_m=0.1)
    C.hazard(w, "fan_on_flame", 1.0, stat="safety_violations")
    parent = rec.get("location")
    if parent in state["entities"]:
        from ..core import add_soil
        add_soil(w, parent, "wax", 0.1)
    for other in C.combustible_ids(w):
        orec = state["entities"].get(other)
        if orec is None or other == eid or orec.get("burnt") or C.room_of(state, other) != rid:
            continue
        if orec.get("category") in ("floor", "wall"):
            continue
        _, ox, oy = C.entity_xy(state, other)
        if math.hypot(ox - x, oy - y) <= 0.5:
            om = w.mut(other)
            om["burnt"] = True
            C.add_damage(om, "burnt", 1.0)
            w.stat("fire")
            C.log(w, "ignition", target=other, source=eid, cause="fan blew the flame onto it")
            C.visual(w, "fire", other)
            C.notice(w, "smoke", other, visual=True)
            break


def _blow_paper(w, eid, rid, v):
    state = w.s
    rec = state["entities"][eid]
    _, x, y = C.entity_xy(state, eid)
    cell = [int(x // C.CELL), int(y // C.CELL)]
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    cell = [int(C.clamp(cell[0], 0, cols - 1)), int(C.clamp(cell[1], 0, rows - 1))]
    before = rec.get("location")
    m = w.mut(eid)
    for key in ("spos", "shelf"):
        m.pop(key, None)
    m["location"], m["relation"], m["pos"] = rid, "on", cell
    m["scattered"] = True
    w.log_move(eid, before, rid, by="fan_airflow")
    C.log(w, "paper_blown", target=eid, airspeed=v)
    C.visual(w, "paper_blown", eid, size_m=0.25)


def _topple(w, eid, rid):
    state = w.s
    rec = state["entities"][eid]
    before = rec.get("location")
    _, x, y = C.entity_xy(state, eid)
    cell = geometry.free_floor_cell(state, rid, near=(int(x // C.CELL), int(y // C.CELL)), ignore=(eid,)) or [0, 0]
    m = w.mut(eid)
    for key in ("spos", "shelf"):
        m.pop(key, None)
    m["location"], m["relation"], m["pos"] = rid, "on", cell
    m["orientation"] = "on_side"
    m["on"] = False
    m["running"] = False
    m["unstable_s"] = 0.0
    C.add_damage(m, "toppled", 0.3)
    w.stat("damage")
    w.log_move(eid, before, rid, by="vibration")
    C.log(w, "fan_toppled", target=eid)
    C.visual(w, "fan_toppled", eid)
    C.notice(w, "thud", eid)


def thrust_axis_deg(state, eid):
    """Room-frame direction (grid degrees: 0 = +x east, 90 = +y south) of the reaction a running fan pushes back with:
    opposite its aim (compass aim -> grid through sight.grid_deg). A flat-framed fan (box or window: its depth is
    the short side of its footprint) blows through its face, so the push runs along its own depth axis, the side
    of that axis nearer the aim's reverse; a round-based fan pushes straight back."""
    from ...kernel import sight
    from ...kernel.phys import statics
    rec = state["entities"][eid]
    back = sight.grid_deg((C.num((rec.get("settings") or {}).get("aim_deg"), 0.0) + 180.0) % 360.0)
    size = rec.get("size_m") or [0.5, 0.5, 0.5]
    if abs(C.num(size[0], 0.5) - C.num(size[1], 0.5)) < 0.05:
        return round(back, 3)
    yaw = statics.world_yaw(state, eid)
    depth = (yaw + (90.0 if C.num(size[1], 0.5) < C.num(size[0], 0.5) else 0.0)) % 360.0
    other = (depth + 180.0) % 360.0
    return round(depth if _ang_diff(depth, back) <= _ang_diff(other, back) else other, 3)


def _rock_kernel(w, eid, rid):
    """furniture_tip law active: a minute of rocking on a soft base is one jolt of SOFT_BASE_ROCK_G against the fan's
    thrust (thrust_axis_deg) handed to the kernel (physics.knock): the kernel's statics decide from the fan's base and
    centre of mass along that axis whether it tips (on its side, tipped_over, an impact onto the bed or sofa, a fall
    when it ends past the edge) or walks across the cushion (a slide; over the edge it falls), and who caused it (the
    robot when it set the fan or the thing under it there: physics attribution). The physics consequence is
    witnessed by the camera and heard as a physics sound by the kernel. A fan that went over stops (its motor stalls
    against the bedding), as in the legacy model."""
    state = w.s
    rec = state["entities"][eid]
    s = rec.get("settings") or {}
    accel = SOFT_BASE_ROCK_G.get(s.get("speed", "low"), 0.0)
    w.mut(eid)["unstable_s"] = 0.0
    if accel <= 0.0:
        return
    out = physics.knock(w, eid, accel, direction=thrust_axis_deg(state, eid), cause="vibration")
    rec = state["entities"].get(eid) or {}
    if out.get("tipped") or out.get("fell") or rec.get("orientation") == "on_side":
        m = w.mut(eid)
        m["on"] = False
        m["running"] = False
        m["power_w"] = 0.0
        C.log(w, "fan_toppled", target=eid, kernel=True)
    elif out.get("slid_m"):
        C.log(w, "fan_walked", target=eid, distance_m=out.get("slid_m"))


# ---------------------------------------------------------------------------------------------
# cords (G)

def door_cells(state, rid):
    return sorted({tuple(c["cells"][rid]) for c in state.get("connections", {}).values() if rid in c.get("cells", {})})


def walkway_cells(state, rid, ignore=()):
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    doors = door_cells(state, rid)
    cells = set()
    for d in doors:
        cells.add(d)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = (d[0] + dx, d[1] + dy)
            if geometry.in_grid(room, c):
                cells.add(c)
    centre = (cols // 2, rows // 2)
    targets = list(doors[1:]) + [centre]
    for a in doors:
        for b in targets:
            if b == a:
                continue
            path = geometry.find_path(state, rid, a, [b], ignore=tuple(ignore))
            if path:
                cells.update(path)
    return cells


def line_cells(a, b):
    (x0, y0), (x1, y1) = a, b
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    out = []
    while True:
        out.append((x0, y0))
        if (x0, y0) == (x1, y1):
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return out


def outlet_cell(state, eid, rid, from_cell):
    rec = state["entities"][eid]
    out = rec.get("outlet")
    orec = state["entities"].get(out or "")
    if orec and C.room_of(state, out) == rid:
        _, cells = geometry.anchor(state, out)
        if cells:
            return tuple(cells[0])
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    x, y = from_cell
    options = [(0, y), (cols - 1, y), (x, 0), (x, rows - 1)]
    return min(options, key=lambda c: (abs(c[0] - x) + abs(c[1] - y), c[1], c[0]))


def device_cell(state, eid):
    rid, cells = geometry.anchor(state, eid)
    if not cells:
        return rid, None
    return rid, tuple(cells[0])


def cord_route(state, eid):
    """(room, cells, length_m) of a corded device's cord from the device to its outlet."""
    rid, cell = device_cell(state, eid)
    if rid is None or cell is None:
        return rid, [], 0.0
    target = outlet_cell(state, eid, rid, cell)
    cells = line_cells(cell, target)
    length = math.hypot((target[0] - cell[0]) * C.CELL, (target[1] - cell[1]) * C.CELL)
    from ...kernel.geometry import height_m
    length += height_m(state, eid) if eid in state["entities"] else 0.0
    return rid, cells, length


def _electrical_cords():
    """electrical's cord module when that domain is loaded (never imported from here: no side effects otherwise)."""
    import sys
    return sys.modules.get(__name__.rsplit(".climate.", 1)[0] + ".electrical.cords")


def _electrical_run(w, eid, rec, relay=False):
    """True when electrical owns this device's cord run (``cord_cells`` / ``cord_route``, laid by its plug-in and
    layout rules): the run is kept as it is unless the device moved away from where it starts, in which case it is
    re-laid with electrical's own routing (cord_options + CordCheck: no doorway clearance, no cut route) - climate never
    overwrites it with a straight line (electrical round-2 report: generate_house(3, dwelling='apartment') box_fan_1).
    The climate flags are then read off electrical's hazard kinds. relay=True (the robot just set the device down) re-lays
    the run even when it was dressed along the baseboard (an empty run says nothing about where the device stood)."""
    E = _electrical_cords()
    if E is None or "cord_cells" not in rec or not hasattr(E, "cord_options"):
        return False
    state = w.s
    run = rec.get("cord_cells") or []
    rid, cells = geometry.anchor(state, eid)
    here = {tuple(c) for c in cells or []}
    moved = (relay and not run) or (bool(run) and not (run[0][0] == rid and tuple(run[0][1]) in here))
    if moved:
        out = rec.get("outlet")
        if out and out in state["entities"]:
            opts = E.cord_options(state, eid, out, (rec.get("cord_route") or "along_wall", "along_wall", "direct"), E.CordCheck(state, eid))
            m = w.mut(eid)
            if opts:
                best = opts[0]
                m["cord_route"], m["cord_length_used_m"], m["cord_hazard"] = best["route"], best["length"], list(best["hazards"])
                m["cord_cells"] = [[r, [c[0], c[1]]] for r, c in best["cells"]]
                E.set_floor_hazards(w, eid, best["trip"])
            else:
                m["cord_route"], m["cord_cells"], m["cord_length_used_m"], m["cord_hazard"] = "dressed", [], 0.0, []
                E.set_floor_hazards(w, eid, [])
    elif run and "walkway" in (rec.get("cord_hazard") or []) and hasattr(E, "cord_hazards") and not any(
            h.get("source") == eid for r in {x[0] for x in run} for h in (state["rooms"].get(r) or {}).get("floor_hazards", [])):
        # same run, lifted and set back on its spot (pick_up cleared its trip cells): electrical's trip cells again
        _, trip = E.cord_hazards(state, eid, [(r, (int(c[0]), int(c[1]))) for r, c in run])
        E.set_floor_hazards(w, eid, trip)
    rec = w.ent(eid)
    kinds = set(rec.get("cord_hazard") or [])
    changes = {"cord_crosses_walkway": "walkway" in kinds, "cord_under_rug": "under_rug" in kinds,
               "cord_length_needed_m": round(C.num(rec.get("cord_length_used_m")), 2)}
    if any(rec.get(k) != v for k, v in changes.items()):
        w.mut(eid).update(changes)
    return True


def cord_reaches(state, eid):
    """(reaches, needed_m, have_m) for a plugged-in corded device where it stands: electrical's routes when that domain
    owns the cord (any route the cord reaches along), else this module's straight run."""
    rec = state["entities"][eid]
    have = C.num(rec.get("cord_length_m"), 1.8)
    E = _electrical_cords()
    out = rec.get("outlet")
    if E is not None and "cord_cells" in rec and hasattr(E, "cord_options") and out and out in state["entities"]:
        opts = E.cord_options(state, eid, out, (rec.get("cord_route") or "along_wall", "along_wall", "direct"))
        if opts:
            return True, min(o["length"] for o in opts), have
        cells, length, _, _ = E.route_cells(state, eid, out, "direct")
        return False, C.num(length, have + 1.0), have
    _, _, length = cord_route(state, eid)
    return length <= have + 0.25, length, have


def refresh_cord(w, eid, relay=False):
    state = w.s
    rec = state["entities"].get(eid)
    if not rec or not rec.get("plug_required") or not rec.get("plugged_in") or rec.get("location") == "robot":
        return
    if _electrical_run(w, eid, rec, relay=relay):
        return
    rid, cells, length = cord_route(state, eid)
    if rid is None:
        return
    walk = walkway_cells(state, rid, ignore=(eid,))
    rugs = set()
    for oid in geometry.room_entities(state, rid):
        orec = state["entities"][oid]
        if orec.get("category") == "rug":
            rugs.update(geometry.footprint(orec))
    _, own = device_cell(state, eid)
    route = [c for c in cells if c != own]
    crossing = sorted(c for c in route if c in walk)
    under = sorted(c for c in route if c in rugs)
    changes = {"cord_crosses_walkway": bool(crossing), "cord_under_rug": bool(under), "cord_length_needed_m": round(length, 2)}
    if any(rec.get(k) != v for k, v in changes.items()):
        w.mut(eid).update(changes)
    room = state["rooms"][rid]
    keep = [h for h in room.get("floor_hazards", []) if h.get("source") != eid]
    new = keep + [{"cell": [c[0], c[1]], "kind": "trip", "source": eid, "cord": True} for c in crossing]
    if new != room.get("floor_hazards", []):
        w.room_m(rid)["floor_hazards"] = new


def _door_zone(state, rid):
    """Doorway clearance of the room's doors (kernel geometry.door_clearance over door connections only: the doorway
    cell and its four neighbours, the leaf of a hinged door swinging into the room, an overhead door's threshold)."""
    conns = {cid: c for cid, c in state.get("connections", {}).items() if c.get("door") and rid in c.get("cells", {})}
    if not conns:
        return set()
    return geometry.door_clearance(dict(state, connections=conns), rid)


def placement_issues(state, eid):
    """Sorted list of placement problems for a corded floor/table device: cord_walkway, cord_under_rug, door_swing,
    unstable, near_water, near_curtain, child_reach (a fan's grille or a hot device on the floor where a child is home)."""
    rec = state["entities"].get(eid) or {}
    issues = []
    if rec.get("cord_crosses_walkway"):
        issues.append("cord_walkway")
    if rec.get("cord_under_rug"):
        issues.append("cord_under_rug")
    rid, cell = device_cell(state, eid)
    if rid is None or cell is None:
        return sorted(issues)
    room = state["rooms"][rid]
    if rec.get("location") == rid and cell in _door_zone(state, rid):
        issues.append("door_swing")
    parent = state["entities"].get(rec.get("location")) or {}
    if parent.get("category") in UNSTABLE_BASES or parent.get("upholstered"):
        issues.append("unstable")
    near = geometry.near_cells([cell], room, 1)
    for oid in geometry.room_entities(state, rid):
        orec = state["entities"][oid]
        if oid == eid:
            continue
        cells = set(geometry.footprint(orec))
        if not cells & near:
            continue
        if orec.get("water_source") or orec.get("drain") or orec.get("category") in ("sink", "bathtub", "shower", "aquarium"):
            issues.append("near_water")
        if orec.get("category") in ("curtain", "drape") or (orec.get("covers_window") and orec.get("material") == "fabric"):
            issues.append("near_curtain")
    for wid in geometry.room_entities(state, rid):
        wrec = state["entities"][wid]
        if wrec.get("category") == "window" and wrec.get("pos") and geometry.chebyshev(tuple(wrec["pos"]), cell) <= 1:
            for cov in sorted(state["entities"]):
                crec = state["entities"][cov]
                if (crec.get("window") == wid or crec.get("covers_window") == wid) and crec.get("category") in ("curtain", "shade") and \
                        crec.get("material", "fabric") == "fabric":
                    issues.append("near_curtain")
                    break
    if rec.get("location") == rid and any(p.get("role") in ("child", "toddler", "infant") and not p.get("away")
                                          for p in state.get("people", {}).values()):
        issues.append("child_reach")
    return sorted(set(issues))


def refresh_placement(w, eid):
    issues = placement_issues(w.s, eid)
    if w.ent(eid).get("placement_issues") != issues:
        w.mut(eid)["placement_issues"] = issues


def clear_cord(w, eid):
    for rid in sorted(w.s["rooms"]):
        room = w.s["rooms"][rid]
        if any(h.get("source") == eid for h in room.get("floor_hazards", [])):
            w.room_m(rid)["floor_hazards"] = [h for h in room["floor_hazards"] if h.get("source") != eid]


@before("place", order=58)
def _cord_reach_guard(ctx):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if not C.is_kind(rec, "portable_fan", "space_heater", "humidifier", "dehumidifier", "air_purifier"):
        return
    if not (rec.get("plug_required") and rec.get("plugged_in")):
        return
    dest = ctx.args.get("destination") or w.robot["room"]
    rid = dest if dest in w.s["rooms"] else C.room_of(w.s, dest)
    out = rec.get("outlet")
    if out and out in w.s["entities"] and C.room_of(w.s, out) not in (None, rid):
        raise ActionError("It is plugged in in another room; the cord will not reach (unplug it first)", "precondition")


@after("place", order=58)
def _cord_after_place(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if not C.is_kind(rec, "portable_fan", "space_heater", "humidifier", "dehumidifier", "air_purifier"):
        return
    if not (rec.get("plug_required") and rec.get("plugged_in")):
        refresh_placement(w, eid)
        return
    reaches, length, reach = cord_reaches(w.s, eid)
    if not reaches:
        if ctx.realistic:
            raise ActionError("The cord does not reach the outlet from there; pick a spot nearer the outlet "
                              "(never add an extension cord to a heater)", "precondition")
        raise ActionError("The cord ({:.1f} m) does not reach the outlet from there ({:.1f} m needed); pick a spot nearer the outlet "
                          "(never add an extension cord to a heater)".format(reach, length), "precondition")
    refresh_cord(w, eid, relay=True)
    refresh_placement(w, eid)


@after("pick_up", order=58)
def _cord_lifted(ctx, result):
    eid = ctx.args.get("target")
    rec = ctx.w.s["entities"].get(eid) or {}
    if C.is_kind(rec, "portable_fan", "space_heater", "humidifier", "dehumidifier", "air_purifier"):
        clear_cord(ctx.w, eid)


# ---------------------------------------------------------------------------------------------
# verbs and guards

@before("set", order=46)
def _fan_set_guard(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    if C.kind(rec) == "ceiling_fan" and ctx.args.get("setting") == "direction":
        kind = (rec.get("control_kinds") or {}).get("direction")
        if kind != "remote":
            raise ActionError("Direction is the slide switch on the motor housing: use reverse_fan", "capability")


@after("set", order=46)
def _fan_set_after(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    if C.kind(rec) == "ceiling_fan":
        ceiling_tick(w, eid, 0.0)
    elif C.kind(rec) == "portable_fan":
        portable_tick(w, eid)


@after("switch_on", order=46)
@after("switch_off", order=46)
def _fan_switch_after(ctx, result):
    w = ctx.w
    eid = ctx.args.get("target")
    rec = w.s["entities"].get(eid) or {}
    targets = [eid] + [c for c in rec.get("controls") or [] if c in w.s["entities"]]
    for t in targets:
        trec = w.ent(t)
        if C.kind(trec) == "ceiling_fan":
            ceiling_tick(w, t, 0.0)
        elif C.kind(trec) == "portable_fan":
            portable_tick(w, t)
            if trec.get("on") and trec.get("plug_required") and trec.get("plugged_in"):
                refresh_cord(w, t)


def _reverse_inverse(ctx, before):
    return {"action": "reverse_fan", "target": ctx.args["target"], "via": ctx.args.get("via")}


@action("reverse_fan", params={"target": "entity", "via": "?str:switch|remote"}, duration=lambda ctx: ctx.data.get("seconds", 4.0),
        energy=0.01, noise=0.01, category="physical", inverse=_reverse_inverse,
        doc="Reverse a ceiling fan's direction (ccw = summer downdraft, cw = winter updraft). via=switch flips the slide switch on the "
            "motor housing (needs the fan within reach, usually from a step stool or ladder, and the blades stopped: switch the fan "
            "off and wait ~90 s); via=remote presses the held remote's reverse button (the fan slows, stops and restarts itself).")
def reverse_fan(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = w.s["entities"].get(eid) or {}
    require(C.kind(rec) == "ceiling_fan", "Only ceiling fans have a reverse switch", "argument")
    via = ctx.args.get("via") or ("remote" if rec.get("remote") and ctx.holding(rec.get("remote")) else "switch")
    old = (rec.get("settings") or {}).get("direction", "ccw")
    new = "cw" if old == "ccw" else "ccw"
    if via == "remote":
        remote = rec.get("remote")
        require(remote and ctx.holding(remote), "Hold this fan's remote to use its reverse button", "precondition")
        ctx.obj(eid)
        rrec = w.ent(remote)
        # realistic: pressing the button shows only that nothing happens (the charge is a battery-tester reading)
        require(C.num(rrec.get("battery_level"), 1.0) > 0.02,
                "Nothing happens when the reverse button is pressed" if ctx.realistic else "The remote does nothing (flat battery)", "resource")
        require(ceiling_running(w, rec, eid) or (rec.get("control_kinds") or {}).get("direction") == "remote",
                "The remote's reverse button only works while the fan is running", "precondition")
        m = w.mut(eid)
        m["settings"]["direction"] = new
        coast = C.num(rec.get("rpm")) / 190.0 * 30.0
        if ctx.realistic:
            coast = round(coast / 10.0) * 10.0     # the slow-down / restart time to 10 s (the exact time encodes the rpm)
        ctx.data["seconds"] = 4.0 + coast
        C.log(w, "fan_reversed", target=eid, via="remote", direction=new)
        return "Pressed reverse: the fan slowed, stopped and restarted turning {}".format("clockwise" if new == "cw" else "counter-clockwise")
    ctx.obj(eid, touch=True)
    rpm = C.num(rec.get("rpm"))
    if rpm >= 60.0:
        if ctx.realistic:
            raise ActionError("The blades are still spinning; switch the fan off and wait for them to stop before reaching into the "
                              "blade sweep", "safety")
        raise ActionError("The blades are spinning ({:.0f} rpm); switch the fan off and wait for them to stop before reaching into the "
                          "blade sweep".format(rpm), "safety")
    m = w.mut(eid)
    m["settings"]["direction"] = new
    note = ""
    if rpm >= 5.0:
        m["motor_damaged"] = "strained"
        m["wobble"] = True
        C.add_damage(m, "motor_strain", 0.4)
        w.stat("damage")
        note = " - the motor jerked hard against the coasting blades (strained, now wobbles)"
        C.log(w, "fan_reversed_while_spinning", target=eid, rpm=rpm)
    ctx.data["seconds"] = 4.0
    C.log(w, "fan_reversed", target=eid, via="switch", direction=new)
    return "Slid the direction switch to {}{}".format("clockwise (updraft)" if new == "cw" else "counter-clockwise (downdraft)", note)


@before("wipe", order=46)
@before("dust", order=46)
@before("clean", order=46)
@before("hold_steady", order=46)
@before("pick_up", order=46)
def _spinning_blades_guard(ctx):
    rec = ctx.w.s["entities"].get(ctx.args.get("target")) or {}
    if C.kind(rec) == "ceiling_fan" and C.num(rec.get("rpm")) >= 5.0:
        if ctx.realistic:
            raise ActionError("The fan blades are turning; switch it off and wait for them to stop", "safety")
        raise ActionError("The fan blades are turning ({:.0f} rpm); switch it off and wait for them to stop".format(C.num(rec.get("rpm"))), "safety")
    if C.kind(rec) == "portable_fan" and rec.get("running") and ctx.spec.name in ("wipe", "dust", "clean"):
        raise ActionError("Switch the fan off before cleaning it", "safety")


# ---------------------------------------------------------------------------------------------
# pull chains: reachable child controls of a chain-operated ceiling fan

def hub_cell(config, fan_id, chain_end_m=1.85):
    """The cell under the fan's motor housing where its pull chains hang: the centre of the fan's footprint, or the
    footprint cell nearest it that no floor-standing piece taller than the chain end stands on (layout audit: chains
    hung at the footprint corner, inside a bedroom closet)."""
    fan = config["entities"][fan_id]
    room = config["rooms"].get(fan.get("location")) or {}
    cells = sorted(c for c in geometry.footprint(fan) if not room or geometry.in_grid(room, c)) or [tuple(fan.get("pos") or [0, 0])]
    cx = sum(c[0] for c in cells) / float(len(cells))
    cy = sum(c[1] for c in cells) / float(len(cells))
    tall = set()
    for oid in sorted(config["entities"]):
        o = config["entities"][oid]
        if oid == fan_id or o.get("location") != fan.get("location") or not o.get("pos") or not geometry.floor_standing(o):
            continue
        h0 = C.num(o.get("mount_height_m", o.get("z_m", 0.0)), 0.0) if o.get("mounted") else 0.0
        if h0 + C.num((o.get("size_m") or [0, 0, 0])[2], 0.0) >= chain_end_m - 0.3:
            tall.update(geometry.footprint(o))
    ranked = sorted(cells, key=lambda c: ((c[0] - cx) ** 2 + (c[1] - cy) ** 2, c[1], c[0]))
    return next((c for c in ranked if c not in tall), ranked[0])


def add_chains(config, registry, fan_id, chain_end_m=1.85):
    """Create the fan's pull chains (built from core generic_item) hanging to chain_end_m at the fan's cell.

    Each chain mirrors one fan setting in pull order and carries a one-button panel (mechanisms' press
    button=pull applies ``cycle:<setting>``); the change is propagated to the fan by a setting_changed hook.
    """
    fan = config["entities"][fan_id]
    chains = {}
    for setting in ("speed", "light"):
        if setting == "light" and not fan.get("has_light"):
            continue
        cid = "{}_{}_chain".format(fan_id, setting)
        label = "{} {} pull chain".format(fan.get("label", "fan"), "fan" if setting == "speed" else "light")
        rec = registry.make("generic_item", fan["location"], label=label, category="pull_chain", portable=False, mounted=True,
                            blocks_path=False, z_m=chain_end_m, size_m=[0.02, 0.02, 0.3], mass_kg=0.01, material="metal", chain_of=fan_id,
                            chain_setting=setting, settings={setting: fan["settings"][setting]},
                            setting_options={setting: list(fan["setting_options"][setting])}, control_kinds={setting: "pull_chain"},
                            panel={"buttons": {"pull": {"effect": "cycle:" + setting}}})
        rec["pos"] = list(hub_cell(config, fan_id, chain_end_m))
        config["entities"][cid] = rec
        chains[setting] = cid
    fan["chains"] = chains
    return chains


@hook("setting_changed")
def _chain_pulled(ctx, eid, setting, old, new):
    w = ctx.w
    rec = w.ent(eid)
    fan = rec.get("chain_of")
    if fan and fan in w.s["entities"]:
        w.mut(fan)["settings"][setting] = new
        ceiling_tick(w, fan, 0.0)
        C.log(w, "chain_pulled", fan=fan, setting=setting, value=new)
        return
    if C.kind(rec) == "ceiling_fan":
        cid = (rec.get("chains") or {}).get(setting)
        if cid and cid in w.s["entities"] and (w.ent(cid).get("settings") or {}).get(setting) != new:
            w.mut(cid)["settings"][setting] = new
