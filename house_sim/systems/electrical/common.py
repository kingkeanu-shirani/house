"""Shared constants and helpers for the electrical domain.

All "pure" helpers take a raw state dict (so invariants can evaluate them on the episode
baseline too); mutating helpers take the journaled World `w`.

State the domain keeps outside entities lives in the top-level section "electrical":
    harms   {label: {"time_s":..., ...}}   first occurrence of each domain harm (read by invariants)
    tests   {detector_id: [...]}            detector test log
    traced  {device_id: time_s}             cords traced through bundles (inspect where=behind)
    events  [...]                           short domain log (restarts, trips, data loss)
"""
import math

from ...kernel.predicates import root_room

SEC = "electrical"

# --- regional grid ---------------------------------------------------------------------
# Branch ratings follow common practice: NEMA 15/20 A at 120 V (US/CA), 20 A radials at
# 230 V (UK), 16 A at 230 V (EU), 20 A at 100 V (JP).
REGION = {
    "us": {"voltage": 120, "socket": "type_b", "plug2": "type_a", "plug3": "type_b", "branch_a": 15, "wet_a": 20, "kitchen_a": 20, "main_a": 200},
    "ca": {"voltage": 120, "socket": "type_b", "plug2": "type_a", "plug3": "type_b", "branch_a": 15, "wet_a": 20, "kitchen_a": 20, "main_a": 200},
    "uk": {"voltage": 230, "socket": "type_g", "plug2": "type_g", "plug3": "type_g", "branch_a": 20, "wet_a": 20, "kitchen_a": 32, "main_a": 100},
    "eu": {"voltage": 230, "socket": "type_f", "plug2": "type_c", "plug3": "type_f", "branch_a": 16, "wet_a": 16, "kitchen_a": 16, "main_a": 63},
    "jp": {"voltage": 100, "socket": "type_a_jp", "plug2": "type_a", "plug3": "type_a", "branch_a": 20, "wet_a": 20, "kitchen_a": 20, "main_a": 60},
}
# Which plug shapes each socket accepts (IEC plug type letters).
SOCKET_ACCEPTS = {
    "type_b": ["type_a", "type_b"],          # NEMA 5-15R: 2-prong and grounded plugs
    "type_a": ["type_a"],                    # old NEMA 1-15R 2-slot: no ground hole
    "type_a_jp": ["type_a"],                 # JIS C 8303 two-slot
    "type_g": ["type_g"],                    # BS 1363
    "type_f": ["type_c", "type_e", "type_f"],  # Schuko accepts Europlug
    "type_e": ["type_c", "type_e", "type_f"],
}
GROUNDED_PLUGS = {"type_b", "type_g", "type_e", "type_f"}
ONE_WAY_PLUGS = {"type_b", "type_g"}          # grounded pin / sleeved pins fix the orientation

CONNECTOR_NAMES = {"hdmi": "HDMI", "displayport": "DisplayPort", "usb_a": "USB-A", "usb_c": "USB-C", "micro_usb": "micro-USB",
                   "lightning": "Lightning", "rj45": "Ethernet (RJ45)", "barrel_4.5": "4.5 mm barrel", "barrel_5.5": "5.5 mm barrel"}
# Connectors that only go in one way (reversible ones: USB-C, Lightning; RJ45 is keyed).
KEYED = {"hdmi", "displayport", "usb_a", "micro_usb", "rj45"}

HIGH_DRAW_W = 1000.0
DARK = 0.2
LUX_PER_LEVEL = 500.0


def num(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def region(state):
    reg = (state.get("metadata") or {}).get("region") or "us"
    return reg if reg in REGION else "us"


def grid(state):
    return REGION[region(state)]


def voltage(state):
    meta = state.get("metadata") or {}
    return num(meta.get("voltage"), grid(state)["voltage"]) or 120.0


def room_of(state, eid):
    if eid in state.get("rooms", {}):
        return eid
    return root_room(state, eid)


def ents(state):
    return state["entities"]


def is_type(rec, name):
    return rec.get("type") == name or name in rec.get("is_a", [])


def label(state, eid):
    rec = state["entities"].get(eid) or {}
    return rec.get("label") or eid


def storage_device(rec):
    """A USB storage device (external drive, flash drive): `storage: true` or the storage_device category. Other
    domains use `storage` for where a thing belongs (food: "pantry", "fridge"), which is not a drive (round-4 report)."""
    return rec.get("storage") is True or rec.get("usb_storage") is True or rec.get("category") == "storage_device"


# --- domain section ---------------------------------------------------------------------

def section(state):
    return state.get(SEC) or {}


def sec(w):
    s = w.sec(SEC)
    for key, default in (("harms", {}), ("tests", {}), ("traced", {}), ("events", [])):
        if key not in s:
            s[key] = default
    return s


def harm(w, label_, **info):
    """Record a domain harm once (invariants read section.harms against the baseline)."""
    s = sec(w)
    if label_ not in s["harms"]:
        entry = {"time_s": w.s.get("time_s", 0.0)}
        entry.update(info)
        s["harms"][label_] = entry
        w.emit("electrical_harm", label=label_)
    return label_


def log(w, kind, **data):
    s = sec(w)
    entry = {"time_s": w.s.get("time_s", 0.0), "kind": kind}
    entry.update(data)
    s["events"].append(entry)
    del s["events"][:-200]
    return entry


def event(w, kind, eid=None, **data):
    room = room_of(w.s, eid) if eid else None
    return w.emit(kind, target=eid, room=room, **data)


# --- people ---------------------------------------------------------------------------

def people_in(state, rid):
    return sorted(pid for pid, p in state.get("people", {}).items() if p.get("location") == rid)


def asleep(person):
    return person.get("posture") == "sleeping" or person.get("awake") is False


def awake_people_in(state, rid):
    return [pid for pid in people_in(state, rid) if not asleep(state["people"][pid])]


def sleeping_people_in(state, rid):
    return [pid for pid in people_in(state, rid) if asleep(state["people"][pid])]


def anyone_asleep(state):
    return sorted(pid for pid, p in state.get("people", {}).items() if asleep(p))


def children_present(state):
    return any(p.get("role") in ("child", "infant", "toddler") or num(p.get("age"), 30) < 6 for p in state.get("people", {}).values())


def adult_present(state, rid):
    for pid in people_in(state, rid):
        p = state["people"][pid]
        if p.get("authority", 2 if p.get("role") == "adult" else 0) >= 1 and p.get("consent", True) and not asleep(p):
            return pid
    return None


# --- geometry-ish helpers ------------------------------------------------------------

def anchor_cells(state, eid):
    from ...kernel import geometry
    rid, cells = geometry.anchor(state, eid)
    return rid, [tuple(c) for c in (cells or [])]


def chain(state, eid, limit=12):
    """Locations above an entity (parents first), entity ids only."""
    out, loc, seen = [], state["entities"].get(eid, {}).get("location"), set()
    while loc in state["entities"] and loc not in seen and len(out) < limit:
        out.append(loc)
        seen.add(loc)
        loc = state["entities"][loc].get("location")
    return out


SOFT_MATERIALS = {"fabric", "cotton", "wool", "foam", "down", "polyester", "linen", "velvet", "leather", "plush"}


FLAMMABLE = SOFT_MATERIALS | {"cardboard", "paper", "wicker"}


def on_soft_surface(state, eid, flammable=False, w=None):
    """True if the item sits on/in/under bedding, upholstery or cushions (poor heat dissipation)."""
    rec = state["entities"].get(eid) or {}
    if rec.get("relation") == "under":
        return True
    for parent in chain(state, eid, 4):
        p = state["entities"][parent]
        if p.get("category") in ("bed", "pillow", "cushion", "blanket", "bedding") or p.get("upholstered") or p.get("soft_surface"):
            return True
        if p.get("material") in (FLAMMABLE if flammable else SOFT_MATERIALS) and not p.get("antistatic"):
            return True
        if p.get("surface") or p.get("category") in ("table", "counter", "furniture", "charger"):
            if p.get("material") in ("wood", "stone", "metal", "glass", "plastic", "laminate", "ceramic"):
                return False
    # covered by something soft (pillow/blanket placed on top of it)
    candidates = ids(w, "soft") if w is not None else sorted(state["entities"])
    for other in candidates:
        o = state["entities"].get(other) or {}
        if o.get("covers") == eid and o.get("material") in SOFT_MATERIALS:
            return True
    return False


def parse_hhmm(text):
    """'6:30', '06:30', '18:05' -> (h, m) or None."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        return None
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def fmt_hhmm(seconds_of_day):
    s = int(round(seconds_of_day)) % 86400
    return "{:02d}:{:02d}".format(s // 3600, (s % 3600) // 60)


def local_seconds(state):
    from ...kernel import clock
    now = clock.now(state)
    return now.hour * 3600 + now.minute * 60 + now.second


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def rnd(x, n=3):
    return round(float(x), n)


def dist_cells(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


_HAS_SYSTEM = {}


def has_system(name):
    """True when another domain's system with this name is registered (e.g. safety.detectors)."""
    from ...kernel.plugins import SYSTEMS
    key = (name, len(SYSTEMS))
    if key not in _HAS_SYSTEM:
        _HAS_SYSTEM[key] = any(entry[1] == name for entry in SYSTEMS)
    return _HAS_SYSTEM[key]


def has_hook(action_name, module_prefix, when="after"):
    from ...kernel.plugins import AFTER, BEFORE
    table = AFTER if when == "after" else BEFORE
    return any(label.startswith(module_prefix) for _, label, _ in table.get(action_name, []))


# --- cross-module refresh registry ----------------------------------------------------
# Modules register functions fn(w) that recompute derived, observable fields after any
# electrical change (powered flags, lit lights, TV displays, charging links...).
REFRESHERS = []


def refresher(order=100):
    def deco(fn):
        REFRESHERS.append((order, fn.__module__ + "." + fn.__name__, fn))
        REFRESHERS.sort(key=lambda t: (t[0], t[1]))
        return fn
    return deco


MARKERS = ("breaker_panel", "breaker", "outlet")


# --- static indexes (kernel v2.1 w.static_index) -------------------------------------
# Systems and refreshers never scan every entity: they iterate these id lists (sorted, cached by
# the kernel until an entity is created or destroyed) and re-check dynamic fields on the records.
# Predicates read only fields fixed at creation (type/is_a, category, ports, slots, plug_required,
# power_w, clock dicts...). Pure helpers that also run on raw state dicts (invariants, settle,
# inspectors) take an optional id list and fall back to a scan.

def _is_marker(rec):
    return rec.get("category") in MARKERS or "energized" in rec


def _is_powerable(rec):
    """Anything whose supply the domain tracks: panel parts, outlets/providers, corded and hardwired loads, lights."""
    return bool(rec.get("category") in ("breaker", "outlet", "light") or rec.get("outlet_kind") is not None
                or rec.get("plug_required") or rec.get("circuit") or rec.get("hardwired") or "energized" in rec)


def _charge_source(rec):
    return bool(rec.get("ports") or rec.get("captive_connector") or rec.get("wireless") or rec.get("dock_for")
                or rec.get("bud_slots") is not None or rec.get("charges_chemistry") is not None or rec.get("platform"))


def _is_metered(rec):
    return "power_w" in rec or "_load_w" in rec or "standby_w" in rec or _charge_source(rec)


PREDICATES = {
    "markers": _is_marker,
    "powerable": _is_powerable,
    "metered": _is_metered,
    "breakers": lambda r: r.get("category") == "breaker",
    "providers": lambda r: r.get("category") == "outlet" or (r.get("outlet_kind") is not None and num(r.get("receptacles"), 0) > 0),
    "outlets": lambda r: r.get("category") == "outlet",
    "plug_devices": lambda r: bool(r.get("plug_required")),
    "backup": lambda r: num(r.get("backup_battery_wh"), 0.0) > 0,
    "lights": lambda r: r.get("category") == "light",
    "switches": lambda r: r.get("category") == "switch",
    "detectors": lambda r: r.get("category") == "detector",
    "clocks": lambda r: isinstance(r.get("clock"), dict),
    "electronics": lambda r: "power_state" in r or storage_device(r)
    or r.get("category") in ("tv", "monitor", "audio", "media_source", "computer", "network"),
    "network": lambda r: r.get("type") in ("wifi_router", "cable_modem") or r.get("category") == "network",
    "displays": lambda r: r.get("category") in ("tv", "monitor"),
    "tvs": lambda r: r.get("category") == "tv",
    "cells": lambda r: r.get("category") == "battery",
    "tool_packs": lambda r: str(r.get("part_type", "")).startswith("tool_pack"),
    "soft": lambda r: r.get("material") in SOFT_MATERIALS,
    "workstation": lambda r: r.get("category") == "monitor" or r.get("type") in ("laptop", "laptop_barrel", "computer_mouse", "keyboard"),
}


def ids(w, name, predicate=None):
    """Sorted ids of entities in a named static index (see PREDICATES; modules may pass their own predicate)."""
    return w.static_index("electrical." + name, predicate or PREDICATES[name])


def electrified(state, w=None):
    """True when the house has electrical infrastructure (generated houses always do; bare legacy layouts do not).

    Derived fields are only maintained on electrified houses, so bare fixtures stay untouched by the domain."""
    if w is not None:
        return bool(ids(w, "markers"))
    for rec in state["entities"].values():
        if _is_marker(rec):
            return True
    return False


def refresh(w):
    if not electrified(w.s, w):
        return
    for _, _, fn in REFRESHERS:
        fn(w)


_MISSING = object()


def set_if(w, eid, key, value):
    """Journaled assignment only when the value actually changes."""
    rec = w.s["entities"].get(eid)
    if rec is None:
        return False
    if rec.get(key, _MISSING) != value:
        if value is None and key not in rec:
            return False
        w.mut(eid)[key] = value
        return True
    return False
