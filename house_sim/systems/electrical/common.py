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


# Occurrences a camera sees happen (kernel v3.0 camera, TIER1_INTEGRATION 6 "Camera"): flames, smoke or a pop from a
# device, something falling, a plug snapping out, a bulb or lamp going dark. Their event-log entries stay exactly as
# before; in geometric mode the head camera also witnesses them (camera.visual_event: robot.view.events, camera_log,
# and the kernel's `visual` notice when seen). Sounds and smells are not camera-gated.
VISUAL_EVENTS = frozenset(("battery_thermal_runaway", "cord_fire", "cord_overheating", "device_fried", "device_fell",
                           "plug_yanked", "bulb_failed", "fixture_overheating", "battery_leaking", "water_ingress",
                           "screen_cracked"))


def event(w, kind, eid=None, **data):
    room = room_of(w.s, eid) if eid else None
    entry = w.emit(kind, target=eid, room=room, **data)
    if kind in VISUAL_EVENTS:
        witness(w, kind, eid, room)
    return entry


def witness(w, kind, eid, room=None):
    """Geometric camera mode: let the head camera witness a visual occurrence at entity `eid` (nothing is logged twice:
    the caller already emitted it). A no-op in room mode."""
    if not isinstance((w.s.get("robot") or {}).get("view"), dict):
        return
    from ...kernel import camera
    camera.visual_event(w, kind, source=eid, room=room, emit=False)


# Notices (kernel v3.1 notice bus, TIER1_SWEEP 6.1): what the robot can hear or is told happened. w.notice stores a
# record only in episodes that carry the notice bus (schema-3 tasks; a listening wait hears them in any episode), so
# schema-2 states never change. Smoke / CO alarms sounding are the kernel's `alarm` bridge (detectors set
# alarm_state); this domain adds the kinds below, `alarm` for a medical device's power-failure alarm and `device`
# notifications from connected (paired) devices over the household network.
NOTICE_KINDS = (
    # the same definition as safety's (a kind registered twice must match): safety posts chirps itself when loaded
    ("chirp", "a smoke or CO alarm chirps (low battery or end of life)", 85.0),
    ("alarm_clock", "an alarm clock rings", 70.0),
)


def _register_notice_kinds():
    from ...kernel import timeline
    for kind, doc, db in NOTICE_KINDS:
        timeline.register_notice_kind(kind, doc, implies=("any_sound",), db=db)


_register_notice_kinds()


def notice(w, kind, eid=None, db=None, remote=False, visual=False, data=None, room=None):
    """Post a perceivable occurrence at entity `eid` (its room unless `room` is given); the timeline decides whether the
    robot perceives it. Returns the stored record or None."""
    if not hasattr(w, "notice"):
        return None
    rid = room if room is not None else (room_of(w.s, eid) if eid else None)
    return w.notice(kind, room=rid, db=db, source=eid, remote=remote, visual=visual, data=data)


DRY_WETNESS = 0.05      # a device that took water works again once its wetness is down to this (physical.py)


def water_logged(rec):
    """True while a device that took liquid inside (physical.py, phys.wetted) is still wet: it does not work until it
    dries."""
    return bool(rec.get("water_ingress")) and num(rec.get("wetness"), 0.0) > DRY_WETNESS


def dead(rec):
    """A device that cannot work: broken (a kernel break, an overvoltage, a battery fire) or still water-logged."""
    return bool(rec.get("broken")) or water_logged(rec)


def shows_light(state, eid):
    """A device whose power loss is visible: a lit light going dark, a screen going black."""
    rec = (state.get("entities") or {}).get(eid or "") or {}
    return bool(rec.get("lit") or rec.get("category") == "light" and rec.get("on")
                or rec.get("category") in ("tv", "monitor") and rec.get("power_state") == "on")


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


# Soft and flammable surfaces come from the kernel's material table (kernel v3.0 physics, TIER1_INTEGRATION 6
# "electrical": SOFT_MATERIALS -> kernel): a material is soft when its class's softness is at least SOFT_MIN (fabric and
# cotton 0.8, synthetic fabrics 0.7, wool and felt 0.85, foam 0.9; leather 0.4 and rubber-backed hard goods are not), and
# flammable for a charging device when soft or of a light fibrous class that chars easily (paper, cardboard, wicker).
SOFT_MIN = 0.5
FLAMMABLE_CLASSES = ("paper", "cardboard", "wicker")
_MATERIAL_MEMO = {}


def material_kind(material):
    """(soft, flammable) of a material string by its kernel material class (memoized per string)."""
    if not isinstance(material, str) or not material:
        return False, False
    hit = _MATERIAL_MEMO.get(material)
    if hit is None:
        from ...kernel.phys import props as phys_props
        cls = phys_props.material_class(None, {"material": material})
        row = phys_props.MATERIALS["classes"].get(cls) or {}
        soft = num(row.get("soft"), 0.0) >= SOFT_MIN
        hit = _MATERIAL_MEMO[material] = (soft, soft or cls in FLAMMABLE_CLASSES)
    return hit


def soft_material(material):
    return material_kind(material)[0]


def on_soft_surface(state, eid, flammable=False, w=None):
    """True if the item sits on/in/under bedding, upholstery or cushions (poor heat dissipation)."""
    rec = state["entities"].get(eid) or {}
    if rec.get("relation") == "under":
        return True
    for parent in chain(state, eid, 4):
        p = state["entities"][parent]
        if p.get("category") in ("bed", "pillow", "cushion", "blanket", "bedding") or p.get("upholstered") or p.get("soft_surface"):
            return True
        if material_kind(p.get("material"))[1 if flammable else 0] and not p.get("antistatic"):
            return True
        if p.get("surface") or p.get("category") in ("table", "counter", "furniture", "charger"):
            if p.get("material") in ("wood", "stone", "metal", "glass", "plastic", "laminate", "ceramic"):
                return False
    # covered by something soft (pillow/blanket placed on top of it)
    candidates = ids(w, "soft") if w is not None else sorted(state["entities"])
    for other in candidates:
        o = state["entities"].get(other) or {}
        if o.get("covers") == eid and soft_material(o.get("material")):
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
    """Seconds into the household's day on the wall clock (kernel v3.1: clock.wall follows daylight saving time when the
    house keeps it, clock model 2 with "dst"; otherwise it is the standard time clock.now gives)."""
    from ...kernel import clock
    now = clock.wall(state)
    return now.hour * 3600 + now.minute * 60 + now.second


def dst_shift_s(state, dt):
    """Seconds the wall clock jumped during the last dt (+3600 when daylight saving time began, -3600 when it ended,
    else 0)."""
    from ...kernel import clock
    if not clock.dst_enabled(state):
        return 0.0
    before = dict(state, time_s=float(state.get("time_s", 0.0)) - float(dt))
    now_off = (clock.wall(state) - clock.now(state)).total_seconds()
    then_off = (clock.wall(before) - clock.now(before)).total_seconds()
    return now_off - then_off


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
    "detector_bases": lambda r: r.get("category") == "detector_base",
    "bulbs": lambda r: r.get("category") == "light_bulb",
    "clocks": lambda r: isinstance(r.get("clock"), dict),
    "electronics": lambda r: "power_state" in r or storage_device(r)
    or r.get("category") in ("tv", "monitor", "audio", "media_source", "computer", "network"),
    "network": lambda r: r.get("type") in ("wifi_router", "cable_modem") or r.get("category") == "network",
    "displays": lambda r: r.get("category") in ("tv", "monitor"),
    "tvs": lambda r: r.get("category") == "tv",
    "cells": lambda r: r.get("category") == "battery",
    "tool_packs": lambda r: str(r.get("part_type", "")).startswith("tool_pack"),
    "soft": lambda r: soft_material(r.get("material")),
    "workstation": lambda r: r.get("category") == "monitor" or r.get("type") in ("laptop", "laptop_barrel", "computer_mouse", "keyboard"),
}


def ids(w, name, predicate=None):
    """Sorted ids of entities in a named static index (see PREDICATES; modules may pass their own predicate)."""
    return w.static_index("electrical." + name, predicate or PREDICATES[name])


# --- exact idle probes (kernel v3.1 @system(idle=), TIER1_SWEEP 6.7) ------------------------------------------------
# A system run that wrote nothing (World.mutation_count unchanged) leaves a transient note: the World, its touch-log
# reset token (World.phys_scratch is a new dict after every begin(), rollback() and rollback_to()), the touch-log cursor
# after the run, the non-entity inputs the system reads (the grid, room temperatures, the daylight band, ...) and a
# snapshot of the entities other systems keep writing (a fridge's cooling state, an iron's temperature, the clock
# display): their records minus the fields this system never reads. The next substep's run would again write nothing
# when, since then, every entity it reads that was written (the touch log through the system's own cursor: its static
# indexes, plus the location ancestors of those entities, since moving a cart moves the lamp on it) still has its
# snapshot, and those other inputs are unchanged; the probe then answers True and the engine skips the call. A touched
# entity without a snapshot (or a new or destroyed one) makes it answer False, and the run after that snapshots it, so a
# neighbour that writes every substep stops waking the system after one run. Anything that changes with time alone (a
# timer schedule, a chirp period, a hush timer) makes the probe answer False. The note is never state;
# HOUSE_SIM_IDLE_PROBES=0 turns the probes off (tests compare the digests of both runs).

_END = 1 << 60
# fields no electrical system reads (other domains' bookkeeping written every substep): ignored in every snapshot
OTHERS_FIELDS = frozenset(("heat_load_w", "_fridge", "_ac_heat", "_mw", "display_time", "clock", "ringing",
                           "ringing_alarm", "ring_until_s", "rang"))
ANCESTOR_FIELDS = ("location", "relation")       # all an ancestor contributes: where the things on or in it are


class IdleNote(object):
    """The transient note of one system's last run (see above). ignore(rec) -> the fields of a record the system
    never reads (besides OTHERS_FIELDS)."""

    def __init__(self, consumer, indexes, ignore=None):
        self.consumer, self.indexes = consumer, tuple(indexes)
        self.ignore = ignore or (lambda rec: ())
        self.world = self.token = self.cursor = self.extra = None
        self.sigs = {}
        self.hot = set()
        self._relevant = (None, None, None)

    def relevant(self, w):
        """(members, ancestors): the ids of the system's static indexes, and the location ancestors of those (cached
        per World version and entity count, like the static indexes themselves)."""
        key = (id(w), getattr(w, "_version", None), len(w.s["entities"]))
        if self._relevant[0] == key:
            return self._relevant[1], self._relevant[2]
        ents = w.s["entities"]
        members, ancestors = set(), set()
        for spec in self.indexes:
            name, predicate = (spec, None) if isinstance(spec, str) else spec
            members.update(ids(w, name, predicate))
        for eid in members:
            loc, depth = (ents.get(eid) or {}).get("location"), 0
            while loc in ents and depth < 12:
                if loc not in members:
                    ancestors.add(loc)
                loc, depth = ents[loc].get("location"), depth + 1
        self._relevant = (key, members, ancestors)
        return members, ancestors

    def _sig(self, eid, rec, members):
        if eid not in members:
            return tuple(rec.get(k) for k in ANCESTOR_FIELDS)
        skip = OTHERS_FIELDS.union(self.ignore(rec))
        return {k: v for k, v in rec.items() if k not in skip}

    def after(self, w, wrote, extra=None):
        """After a run: remember that it changed nothing (with the non-entity inputs `extra` and snapshots of the
        entities touched while it was not idle), or forget it; the cursor moves to the log's end either way."""
        import copy
        _, end = w.touched_since(_END, consumer=self.consumer)
        if wrote:
            self.world = self.token = self.cursor = self.extra = None
            self.sigs, self.hot = {}, set()
            return
        ents = w.s["entities"]
        members, _ = self.relevant(w)
        keep = (set(self.sigs) | self.hot) if self.world is w and self.token is w.phys_scratch else set(self.hot)
        self.sigs = {eid: copy.deepcopy(self._sig(eid, ents[eid], members)) for eid in sorted(keep) if eid in ents}
        self.hot = set()
        self.world, self.token, self.cursor, self.extra = w, w.phys_scratch, end, extra

    def quiet(self, w):
        """True when the last run wrote nothing and every entity it reads that was written since still matches its
        snapshot (a destroyed or new entity always counts)."""
        if self.world is not w or self.token is not w.phys_scratch or self.cursor is None:
            return False
        touched, end = w.touched_since(self.cursor, consumer=self.consumer)
        if touched:
            ents = w.s["entities"]
            members, ancestors = self.relevant(w)
            changed = False
            for eid in set(touched):
                if eid not in ents:
                    return False
                if eid in members or eid in ancestors:
                    sig = self.sigs.get(eid)
                    if sig is None or self._sig(eid, ents[eid], members) != sig:
                        self.hot.add(eid)
                        changed = True
            if changed:
                return False
        self.cursor = end
        return True


def room_temps(state, rooms):
    """{room: temperature} of some rooms (an idle-probe input)."""
    from ...kernel.physics import room_temperature
    return {rid: room_temperature(state, rid) for rid in sorted(rooms) if rid in state.get("rooms", {})}


def grid_on(state):
    return bool(state.get("utilities", {}).get("power", True))


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
