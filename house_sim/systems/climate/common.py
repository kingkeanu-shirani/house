"""Shared helpers, physical constants and psychrometrics for the climate domain.

House-level bookkeeping lives in the top-level state section ``climate`` (never observed directly):
  climate.energy      {"kwh", "gas_kwh", "cost_usd", "hvac_kwh", "by_device": {eid: kwh}}  energy ledger
  climate.tariff      {"peak_from": "16:00", "peak_to": "21:00", "peak_usd_kwh", "offpeak_usd_kwh", "gas_usd_kwh"}
  climate.hazards     {label: seconds or count}  accumulated hazard exposure read by the invariants
  climate.trackers    per-task trackers (open-window-in-rain seconds, hvac-with-open-windows seconds, ...)
  climate.log         recent domain events (bounded)
  climate.sun         {"azimuth_deg", "altitude_deg", "dni_w_m2"} refreshed every substep
  climate.outdoor     {"diurnal": {"min_c", "max_c"}} optional outdoor temperature cycle

Magnitudes are cited where not obvious; fidelity is P-approx (lumped rooms) unless noted.
"""
import datetime
import math
from collections.abc import Mapping

from ...kernel import clock

SECTION = "climate"

# ---------------------------------------------------------------------------------------------
# air and building physics (SI)
RHO_CP = 1206.0              # J/(m3 K): air density 1.2 kg/m3 x cp 1005 J/(kg K)
LATENT_J_PER_G = 2450.0      # J per gram of water evaporated/condensed (2.45 MJ/kg)
ROOM_C_PER_M2 = 55000.0      # J/K per m2 floor: air + furnishings + interior surfaces (lumped, ~10 h house time constant)
ENVELOPE_UA_PER_M2 = 0.8     # W/K per m2 floor for exterior walls/roof share of an insulated room
WINDOW_U = 2.8               # W/(m2 K) double glazing (single pane 5.8)
SHGC = 0.6                   # solar heat gain coefficient, clear double glazing
INFILTRATION_ACH = 0.3       # air changes per hour through cracks (ASHRAE typical existing house 0.3-0.5)
MOISTURE_BUFFER = 3.0        # hygric buffering: furnishings/drywall store ~2-4x the vapour held by the air
DOOR_OPEN_Q = 0.03           # m3/s bulk exchange through an open interior doorway (people/HVAC mixing)
DOOR_CLOSED_Q = 0.002        # m3/s through the undercut of a closed door
STAIRS_Q = 0.05              # m3/s up/down an open stairwell (stack-driven)
OUTDOOR_CO2_PPM = 420.0
PERSON_SENSIBLE_W = 75.0     # seated adult (ASHRAE: 70-75 W sensible, ~55 g/h vapour)
PERSON_VAPOUR_G_H = 55.0
PERSON_CO2_LPS = 0.0052      # 0.31 L/min CO2 at rest (sleeping ~0.004)
PET_W = {"dog": 40.0, "cat": 15.0, "bird": 3.0, "rabbit": 8.0}
# indoor air above which a resting pet with water is at heat-stroke risk (veterinary guidance: dogs ~32 C, cats
# tolerate a little more, rabbits / guinea pigs from ~28-30 C); other species default to 30 C
PET_HEAT_C = {"dog": 32.0, "cat": 33.0, "rabbit": 29.0, "guinea_pig": 29.0, "chinchilla": 27.0, "hamster": 30.0, "bird": 32.0,
              "parrot": 32.0, "chicken": 32.0}
PET_HEAT_DEFAULT_C = 30.0
CELL = 0.5
DEFAULT_CEILING_M = 2.5

CONDITIONED_EXCLUDE = {"garage", "attic", "shed", "basement", "elevator", "building_corridor", "lobby", "mail_room",
                       "shared_laundry"}
PLUMBED_ROOMS = {"kitchen", "bathroom", "half_bath", "laundry_room", "basement", "utility_closet", "mudroom"}
COMBUSTIBLE_MATERIALS = {"fabric", "paper", "cotton", "wool", "cardboard", "polyester", "linen", "silk", "foam",
                         "wicker", "down", "synthetic", "nylon", "felt"}
COMBUSTIBLE_CATEGORIES = {"curtain", "blanket", "throw", "bed", "sofa", "seat", "paper", "document", "mail", "clothing",
                          "garment", "towel", "pillow", "cushion", "book", "box", "laundry", "rug", "bedding", "toy",
                          "magazine", "newspaper", "plant"}
HEAT_GAIN_CATEGORIES = {"tv", "computer", "monitor", "game_console", "lamp", "light", "oven", "stove", "cooktop", "range",
                        "toaster", "toaster_oven", "kettle", "coffee_maker", "iron", "hair_dryer", "microwave", "printer",
                        "router", "speaker", "audio", "aquarium", "fridge", "refrigerator", "freezer", "dishwasher",
                        "slow_cooker", "rice_cooker", "air_fryer", "griddle", "waffle_maker"}
FANS = ("ceiling_fan", "portable_fan")
DEVICE_KINDS = ("thermostat", "hvac_system", "register", "return_grille", "radiator", "mini_split", "ceiling_fan",
                "portable_fan", "space_heater", "humidifier", "dehumidifier", "air_purifier", "outdoor_condenser",
                "hygrometer")


def num(value, default=0.0):
    """float(value), or float(default) for None, booleans, non-numbers and non-finite values (hot path: plain floats)."""
    t = type(value)
    if t is float:
        return value if value - value == 0.0 else float(default)
    try:
        if t is int:
            return float(value)
        if value is None or t is bool:
            return float(default)
        out = float(value)
        return out if math.isfinite(out) else float(default)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def rnd(x, n=3):
    return round(float(x), n)


def kind(rec):
    return (rec or {}).get("climate_kind")


def is_kind(rec, *kinds):
    return kind(rec) in kinds


# ---------------------------------------------------------------------------------------------
# psychrometrics (Magnus formula, Alduchov & Eskridge 1996)

def sat_vp_pa(t_c):
    return 611.2 * math.exp(17.62 * t_c / (243.12 + t_c))


def abs_humidity(t_c, rh):
    """Water vapour density g/m3 at temperature t_c and relative humidity rh (0..1)."""
    return clamp(rh, 0.0, 1.0) * sat_vp_pa(t_c) / (461.5 * (t_c + 273.15)) * 1000.0


def rel_humidity(t_c, ah_g_m3):
    return ah_g_m3 / 1000.0 * 461.5 * (t_c + 273.15) / sat_vp_pa(t_c)


def dew_point_c(t_c, rh):
    rh = clamp(rh, 0.01, 1.0)
    g = math.log(rh) + 17.62 * t_c / (243.12 + t_c)
    return 243.12 * g / (17.62 - g)


# ---------------------------------------------------------------------------------------------
# units

def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def to_c(value, units):
    return f_to_c(num(value)) if units == "F" else num(value)


def from_c(value_c, units):
    return c_to_f(value_c) if units == "F" else value_c


def household_units(state):
    units = (state.get("metadata") or {}).get("units")
    return "F" if units == "imperial" else "C"


# ---------------------------------------------------------------------------------------------
# state access

def sec(w):
    """Journaled climate section (create on first use)."""
    s = w.sec(SECTION)
    for key, default in (("energy", {}), ("hazards", {}), ("trackers", {}), ("log", [])):
        if not isinstance(s.get(key), type(default)):
            s[key] = default
    return s


def section(state):
    return state.get(SECTION) or {}


def log(w, kind_, **data):
    s = sec(w)
    entry = {"time_s": w.s.get("time_s", 0.0), "kind": kind_}
    entry.update(data)
    s["log"].append(entry)
    if len(s["log"]) > 300:
        del s["log"][:len(s["log"]) - 300]
    return entry


def hazard(w, label, amount=1.0, stat=None):
    """Accumulate hazard exposure (seconds or count) for invariants; optionally bump a global stat once."""
    s = sec(w)
    before = num(s["hazards"].get(label))
    s["hazards"][label] = rnd(before + amount, 3)
    if stat and before <= 0:
        w.stat(stat)
    return s["hazards"][label]


def tracker(w, name, amount):
    s = sec(w)
    s["trackers"][name] = rnd(num(s["trackers"].get(name)) + amount, 4)
    return s["trackers"][name]


def room_of(state, eid):
    from ...kernel.predicates import root_room
    return root_room(state, eid)


class RoomIndex:
    """Lazy entity id -> root room lookup (same answers as kernel physics._room_map, but only for the ids
    asked for: systems never walk every entity). Valid for one substep (entities may move between substeps)."""
    __slots__ = ("state", "memo")

    def __init__(self, state):
        self.state = state
        self.memo = {}

    def get(self, eid, default=None):
        memo = self.memo
        if eid in memo:
            return memo[eid]
        state = self.state
        entities, rooms = state["entities"], state["rooms"]
        if eid not in entities:
            return default
        people, pets = state.get("people", {}), state.get("pets", {})
        chain, current = [], eid
        while True:
            if current in memo:
                result = memo[current]
                break
            if current in rooms:
                result = current
                break
            if current == "robot":
                result = state["robot"].get("room")
                break
            if current in people:
                result = people[current].get("location")
                break
            if current in pets:
                result = pets[current].get("location")
                break
            record = entities.get(current)
            if record is None or current in chain:
                result = None
                break
            chain.append(current)
            current = record.get("location")
        for item in chain:
            memo[item] = result
        return result


def static_ids(w, name, predicate):
    """Sorted ids of entities matching a predicate over static fields (kernel static index when a World is
    at hand; a sorted scan for plain setup-time states). Callers re-check dynamic fields."""
    if hasattr(w, "static_index"):
        return w.static_index(name, predicate)
    ents = w["entities"]
    return [eid for eid in sorted(ents) if predicate(ents[eid])]


def is_window(rec):
    return rec.get("category") == "window"


def add_damage(m, kind_, severity=0.5):
    """Merge a damage entry into a mutable record's ``damage`` dict {kind: severity 0..1} (CONVENTIONS 5).
    Legacy list values are converted (each listed text becomes a key at severity 0.5)."""
    cur = m.get("damage")
    if isinstance(cur, dict):
        out = dict(cur)
    elif isinstance(cur, (list, tuple)):
        out = {str(k): 0.5 for k in cur}
    else:
        out = {}
    out[kind_] = round(max(num(out.get(kind_)), clamp(num(severity, 0.5), 0.0, 1.0)), 3)
    m["damage"] = out
    return out


def room_type(state, rid):
    return (state["rooms"].get(rid) or {}).get("type")


def is_outdoor(state, rid):
    return bool((state["rooms"].get(rid) or {}).get("outdoor"))


def conditioned(state, rid):
    room = state["rooms"].get(rid) or {}
    if room.get("outdoor"):
        return False
    if "conditioned" in room:
        return bool(room["conditioned"])
    return room.get("type") not in CONDITIONED_EXCLUDE


def room_dims(room):
    size = room.get("size_m") or [4.0, 4.0]
    w, d = num(size[0], 4.0), num(size[1], 4.0)
    h = num(room.get("ceiling_m"), DEFAULT_CEILING_M) or DEFAULT_CEILING_M
    return w, d, h


def room_volume(room):
    w, d, h = room_dims(room)
    return max(1.0, w * d * h)


def room_area(room):
    w, d, _ = room_dims(room)
    return max(0.5, w * d)


def outdoor_temp(state):
    return num((state.get("weather") or {}).get("temperature_c"), 18.0)


def outdoor_rh(state):
    wx = state.get("weather") or {}
    if wx.get("humidity") is not None:
        return clamp(num(wx.get("humidity"), 0.6), 0.05, 1.0)
    cond = wx.get("condition", "clear")
    return {"rain": 0.92, "storm": 0.95, "fog": 0.97, "snow": 0.85, "cloudy": 0.7}.get(cond, 0.6)


def raining(state):
    wx = state.get("weather") or {}
    return wx.get("condition") in ("rain", "storm") or num(wx.get("rain_mm_h")) > 0.05


def rain_rate(state):
    wx = state.get("weather") or {}
    rate = num(wx.get("rain_mm_h"))
    if rate <= 0 and wx.get("condition") in ("rain", "storm"):
        rate = 15.0 if wx.get("condition") == "storm" else 4.0
    return rate


def aqi_to_pm25(aqi):
    """US EPA AQI -> PM2.5 ug/m3 (piecewise linear breakpoints, 2012 standard)."""
    aqi = num(aqi, 35.0)
    bps = [(0, 0.0), (50, 12.0), (100, 35.4), (150, 55.4), (200, 150.4), (300, 250.4), (500, 500.4)]
    for (a0, c0), (a1, c1) in zip(bps, bps[1:]):
        if aqi <= a1:
            return c0 + (aqi - a0) * (c1 - c0) / (a1 - a0)
    return 500.0


def now_dt(state):
    return clock.now(state)


def hour(state):
    return clock.hour(state)


def iso(state, seconds_from_now=0.0):
    return (clock.now(state) + datetime.timedelta(seconds=float(seconds_from_now))).isoformat(timespec="minutes")


def hhmm_to_h(text):
    try:
        hh, mm = str(text).split(":")
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h + m / 60.0


def people_in(state, rid, awake_only=False):
    out = []
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("location") != rid or p.get("away"):
            continue
        if awake_only and (not p.get("awake", True) or p.get("posture") == "sleeping"):
            continue
        out.append(pid)
    return out


def pets_in(state, rid):
    return [pid for pid in sorted(state.get("pets", {})) if state["pets"][pid].get("location") == rid and not state["pets"][pid].get("escaped")]


def pet_confined(state, pet):
    """True when a pet is shut inside a closed cage, crate or carrier (pets domain fields: ``enclosure``, ``crate``
    with ``crated``, ``inside``): it cannot get to a window. An open (or missing) holder does not confine it."""
    holders = [pet.get("enclosure"), pet.get("inside")]
    if pet.get("crated"):
        holders.append(pet.get("crate"))
    for hid in holders:
        if hid and hid in state["entities"] and not state["entities"][hid].get("open"):
            return True
    return False


def cell_center(cell):
    return (int(cell[0]) + 0.5) * CELL, (int(cell[1]) + 0.5) * CELL


def entity_xy(state, eid):
    """(room, x, y) metres of an entity's footprint centre in its room (walks up containers)."""
    from ...kernel import geometry
    room, cells = geometry.anchor(state, eid)
    if room is None:
        return None, 0.0, 0.0
    rec = state["entities"].get(eid) or {}
    if rec.get("location") in state["rooms"] and rec.get("pos"):
        cols, rows = geometry.footprint_size(rec)
        return room, (int(rec["pos"][0]) + cols / 2.0) * CELL, (int(rec["pos"][1]) + rows / 2.0) * CELL
    if cells:
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        x = (min(xs) + max(xs) + 1) / 2.0 * CELL
        y = (min(ys) + max(ys) + 1) / 2.0 * CELL
        spos = rec.get("spos")
        parent = state["entities"].get(rec.get("location")) or {}
        if spos and parent.get("pos"):
            px, py = int(parent["pos"][0]) * CELL, int(parent["pos"][1]) * CELL
            size = rec.get("size_m") or [0.1, 0.1]
            x, y = px + num(spos[0]) + num(size[0]) / 2.0, py + num(spos[1]) + num(size[1]) / 2.0
        return room, x, y
    return room, 0.0, 0.0


def agent_xy(agent):
    pos = agent.get("pos") or [0, 0]
    return cell_center(pos)


def combustible_ids(w):
    """Entities that can catch fire (static: combustible flag, category, material)."""
    return static_ids(w, "climate.combustible", lambda r: is_combustible(r) and r.get("category") not in ("floor", "wall", "window", "light", "switch"))


# the kernel material classes (data/physics/materials.json) that a space-heater grille (70-85 C, within 0.3 m for
# minutes) or a candle flame blown onto them sets alight: fibres, fabrics, paper and card, foams and woven cane. The
# kernel's other combustible classes (wood, engineered wood, laminate, rigid plastic, film, rubber, leather, cork, wax,
# electronics, food and plant matter) char, melt or only burn once something else is burning at these exposures.
IGNITABLE_CLASSES = frozenset(("cotton", "wool", "synthetic_fabric", "coated_fabric", "paper", "cardboard",
                               "lined_paperboard", "foam", "foam_plastic", "closed_cell_foam", "wicker"))


def is_combustible(rec):
    """Whether a record can catch fire from a heater grille or a blown candle flame (readily ignitable): its own
    ``combustible`` flag, else the soft furnishing / paper categories (a bed or sofa burns whatever its frame is made
    of), else its material through the kernel physics data (tier-1 adoption: the material's kernel class must be
    ``combustible`` in materials.json and one of IGNITABLE_CLASSES). The climate copy of the material list
    (COMBUSTIBLE_MATERIALS) answers only for material strings the kernel does not map ("down", "synthetic")."""
    if not rec:
        return False
    if rec.get("combustible") is not None:
        return bool(rec.get("combustible"))
    if rec.get("category") in COMBUSTIBLE_CATEGORIES:
        return True
    material = rec.get("material")
    if not isinstance(material, str) or not material:
        return False
    flag = material_ignitable(material.strip().lower())
    return (material in COMBUSTIBLE_MATERIALS) if flag is None else flag


_MATERIAL_FLAGS = {}


def material_ignitable(material):
    """Readily ignitable by the kernel physics data (phys.props: the material string's class and that class's ``comb``
    column, which an alias row may override -- fiberglass cloth is a synthetic_fabric that does not burn), or None
    when the kernel does not map the string (memoized: a pure function of the loaded physics data)."""
    if material in _MATERIAL_FLAGS:
        return _MATERIAL_FLAGS[material]
    from ...kernel.phys import props as P
    out = None
    if P.class_known(material):
        cls = P.material_class(None, {"material": material})
        alias = P.MATERIALS["aliases"].get(material)
        row = P.MATERIALS["classes"].get(cls) or {}
        comb = alias.get("comb", row.get("comb")) if isinstance(alias, Mapping) else row.get("comb")
        out = bool(comb) and cls in IGNITABLE_CLASSES
    _MATERIAL_FLAGS[material] = out
    return out


# ---------------------------------------------------------------------------------------------
# tier-1 adoption: geometric camera witnesses, notices

def geometric(state):
    """True in the geometric camera mode (the state carries robot.view); room mode is the default."""
    return isinstance((state.get("robot") or {}).get("view"), dict)


def visual(w, kind, eid=None, room=None, at=None, z=None, size_m=0.3):
    """A visual occurrence (a fan toppling, water on the floor, a flame guttering) for the geometric camera: the head
    camera, in its current pose, witnesses it (robot.view.events, camera_log.events, a "visual" notice when the bus is
    on). Climate logs its own occurrences in the ``climate`` section, so nothing is emitted twice (emit=False); a no-op
    in room mode, whose outputs stay identical."""
    if not hasattr(w, "s") or not geometric(w.s):
        return
    from ...kernel import camera
    camera.visual_event(w, kind, source=eid, room=room, at=at, z=z, size_m=size_m, emit=False)


# notice kinds this domain posts (kernel v3.1 notice bus, TIER1_SWEEP 6.1); a kind registered twice must match
NOTICE_KINDS = (
    ("appliance_beep", "an appliance beeps for attention (a dehumidifier whose bucket is full)", 60.0),
    ("pipe_burst", "water sprays from a burst pipe", 70.0),
)


def _register_notice_kinds():
    from ...kernel import timeline
    for kind_, doc, db in NOTICE_KINDS:
        timeline.register_notice_kind(kind_, doc, implies=("any_sound",), db=db)


_register_notice_kinds()


def notice(w, kind_, eid=None, room=None, db=None, visual=False, data=None):
    """Post a perceivable occurrence at entity `eid` (its room unless `room` is given). w.notice stores a record only
    in episodes that carry the notice bus (schema-3 tasks) or for a listening wait, so schema-2 states never change;
    the timeline decides whether the robot hears or sees it. Returns the stored record or None."""
    if not hasattr(w, "notice"):
        return None
    rid = room if room is not None else (room_of(w.s, eid) if eid else None)
    return w.notice(kind_, room=rid, db=db, source=eid, visual=visual, data=data)


def child_present(state):
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("role") in ("child", "infant", "toddler") and not p.get("away"):
            return True
    return False


def window_facing(rec):
    f = rec.get("facing_deg")
    if f is None:
        f = (rec.get("mech") or {}).get("facing_deg")
    return None if f is None else num(f) % 360.0


def window_open_frac(rec):
    mech = rec.get("mech") or {}
    if "open_frac" in mech:
        frac = num(mech.get("open_frac"))
        frac = max(frac, num(mech.get("upper_open_frac")))
    else:
        frac = 1.0 if rec.get("open") else 0.0
    if rec.get("child_stop") and not rec.get("child_stop_released"):
        frac = min(frac, num(rec.get("child_stop_frac"), 0.1))
    return clamp(frac, 0.0, 1.0)


def window_open_area(rec):
    """Clear opening in m2 (sash windows open half the frame; casements all of it)."""
    mech = rec.get("mech") or {}
    if "clear_opening_m2" in mech and mech.get("open_frac") is not None:
        return max(0.0, num(mech.get("clear_opening_m2")))
    frac = window_open_frac(rec)
    size = rec.get("size_m") or [1.0, 0.1, 1.2]
    width, height = num(size[0], 1.0), num(size[2], 1.2)
    portion = {"sash": 0.5, "sliding": 0.5, "casement": 0.9, "hopper": 0.4, "awning": 0.5}.get(rec.get("window_kind"), 0.5)
    return width * height * portion * frac


def window_screened(rec):
    mech = rec.get("mech") or {}
    if "screen" in mech:
        return mech.get("screen") == "in_place"
    return bool(rec.get("has_screen", True)) and not rec.get("screen_removed")


def window_glass_m2(rec):
    size = rec.get("size_m") or [1.0, 0.1, 1.2]
    return max(0.1, num(size[0], 1.0) * num(size[2], 1.2) * 0.85)


def requester(state):
    return state.get("requester")
