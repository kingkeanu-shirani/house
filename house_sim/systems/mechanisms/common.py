"""Shared mechanism model: closure kinds, defaults, mutation helpers, doorway geometry,
agents near closures, room classes, damage/harm helpers and force levels.

State conventions (see docs/domains/mechanisms.md):

* Top-level, observable, shared with other domains: ``open``, ``locked``, ``latched`` (a latch is
  caught), ``child_lock`` (True = an installed child lock is engaged), ``sealed`` (containers).
* ``mech`` (observable dict): the mechanism's own state - kind, open_frac, latch kind, swing side,
  handle kind, lid angle, drawer runner, panels, slats, cords, hasp, smart-lock display ...
  Entities without ``mech`` get defaults from their type (lazy: generated houses are untouched
  until a mechanism changes).
* ``_mech`` (hidden dict, never observed): ground truth the robot must discover - stuck causes,
  gate sag, strike/keeper offsets, lock faults, spring state, vacuum seals, hot-water temperature.
  Keys starting with "_" at top level are never observed (kernel.perception).
"""
import copy
import math

from ...kernel import geometry
from ...kernel.predicates import root_room

# Push/pull force levels (newtons at the handle). A person opens a latched interior door with
# ~20-40 N; a sticky drawer needs 60-80 N; 200 N is a yank that breaks child latches and
# pulls curtain rods out of drywall anchors (~150 N per bracket).
FORCE_N = {"gentle": 20.0, "normal": 40.0, "firm": 80.0, "yank": 200.0}
FORCES = tuple(FORCE_N)
LATCH_TOLERANCE_MM = 6.0       # latch bar vs catch misalignment that still engages
LIFT_COMPENSATION_MM = 15.0    # lifting a sagging gate while latching
CORD_SAFE_HEIGHT_M = 1.5       # lowest cord point out of a small child's reach (CPSC guidance)
DOOR_CLOSER_S = 8.0            # interior self-closer: closes ~8 s after being released
STORM_CLOSER_S = 3.0
GATE_CLOSER_S = 4.0            # spring-hinge pool/garden gates swing shut in ~2-5 s from fully open
GARAGE_TRAVEL_S = 12.0         # residential opener: 7-8 in/s, 2.1 m door -> ~12 s
DRAWER_TIP_FACTOR = 1.0        # unanchored dresser tips when the open-drawer moment exceeds its righting moment

ESCAPE_ROOM_TYPES = {"front_yard", "driveway", "sidewalk", "porch", "building_corridor", "lobby", "elevator", "mail_room"}
FENCED_ROOM_TYPES = {"backyard", "garden", "patio", "deck", "poolside", "balcony"}
ENCLOSURE_ROOM_TYPES = {"coat_closet", "linen_closet", "utility_closet", "pantry", "shed", "attic"}
VISITOR_ROLES = {"visitor", "stranger", "solicitor", "contractor", "delivery", "guest", "worker", "gardener"}
NIGHT_HOURS = (21.5, 6.0)

DOOR_KINDS = {"hinged_door", "sliding_door", "pocket_door", "bifold_door", "bypass_door", "french_door", "storm_door",
              "double_door", "hatch", "overhead_door", "gate"}
WINDOW_KINDS = {"sash_window", "casement_window", "slider_window", "hopper_window", "skylight"}
LID_KINDS = {"snap_lid", "screw_lid", "hinged_lid", "sliding_lid", "press_fit", "flip_top", "latch_clasp", "magnetic_clasp",
             "hidden_button", "tamper_seal", "squeeze_turn", "cr_cap", "tumbler_hatch"}
CABINET_KINDS = {"cabinet_door", "push_open_cabinet", "lift_up_cabinet", "glass_front_cabinet", "lazy_susan", "blind_corner_pullout",
                 "closet_hinged", "closet_bifold", "closet_bypass", "closet_pocket"}
COVERING_KINDS = {"curtain", "blind", "shutter"}
TRACKED_KINDS = DOOR_KINDS | WINDOW_KINDS | LID_KINDS | CABINET_KINDS | COVERING_KINDS | {"drawer", "pet_door", "high_chair"}

# Latches released by the robot's ordinary open motion (thumb press, lever, knob, gravity lift,
# magnetic or roller catch). Others need their own motion first (verb unlatch).
SIMPLE_LATCHES = {None, "knob_latch", "lever_latch", "gravity", "thumb", "magnetic_catch", "roller_catch", "magnetic_top", "none",
                  "push_bar", "sliding_latch"}
EXPLICIT_LATCHES = {"slide_bolt", "two_step", "squeeze_lift", "clasp", "snap_tabs", "barrel_bolt", "hook_eye"}

KIND_DEFAULTS = {
    "hinged_door": {"latch": "knob_latch", "handle": "lever", "closer_speed": "normal"},
    "french_door": {"latch": "lever_latch", "handle": "lever"},
    "storm_door": {"latch": "thumb", "handle": "push_bar", "self_closing": True, "closer_s": STORM_CLOSER_S},
    "sliding_door": {"latch": "sliding_latch", "handle": "recessed_pull", "track": "seated"},
    "pocket_door": {"latch": "none", "handle": "flush_pull", "track": "seated"},
    "bifold_door": {"latch": "roller_catch", "handle": "knob", "track": "seated"},
    "bypass_door": {"latch": "none", "handle": "recessed_pull", "track": "seated", "open_side": "left"},
    "double_door": {"latch": "lever_latch", "handle": "lever"},
    "hatch": {"latch": "none", "handle": "pull_cord"},
    "overhead_door": {"latch": "none", "handle": "opener", "travel_s": GARAGE_TRAVEL_S},
    "gate": {"latch": "gravity", "handle": "latch", "closer_s": GATE_CLOSER_S},
    "sash_window": {"latch": "cam", "handle": "sash_lift", "locks_n": 2, "screen": "in_place"},
    "casement_window": {"latch": "cam", "handle": "crank", "locks_n": 1, "screen": "in_place", "turns_full": 12.0},
    "slider_window": {"latch": "cam", "handle": "pull", "locks_n": 1, "screen": "in_place"},
    "hopper_window": {"latch": "cam", "handle": "lever", "locks_n": 1, "screen": "in_place"},
    "skylight": {"latch": "cam", "handle": "crank", "locks_n": 1, "turns_full": 15.0},
    "drawer": {"runner": "ball", "stop": True, "track": "seated", "soft_close": False},
    "cabinet_door": {"latch": "magnetic_catch", "handle": "pull"},
    "push_open_cabinet": {"latch": "push_latch", "handle": "none"},
    "lift_up_cabinet": {"latch": "magnetic_catch", "handle": "pull", "stay_lift": True},
    "glass_front_cabinet": {"latch": "magnetic_catch", "handle": "knob"},
    "lazy_susan": {"latch": "roller_catch", "handle": "pull", "rotation_deg": 0.0},
    "blind_corner_pullout": {"latch": "magnetic_catch", "handle": "pull"},
    "closet_hinged": {"latch": "knob_latch", "handle": "knob"},
    "closet_bifold": {"latch": "roller_catch", "handle": "knob", "track": "seated"},
    "closet_bypass": {"latch": "none", "handle": "recessed_pull", "track": "seated", "open_side": "left"},
    "closet_pocket": {"latch": "none", "handle": "flush_pull", "track": "seated"},
    "hinged_lid": {"angle_deg": 0.0, "detent_deg": 75.0, "max_deg": 100.0},
    "sliding_lid": {"track": "seated"},
    "snap_lid": {"tabs": 4},
    "screw_lid": {"turns_to_seat": 1.25, "spec_nm": 1.2, "max_nm": 4.0, "lid_radius_m": 0.035},
    "cr_cap": {"turns_to_seat": 1.0, "spec_nm": 0.6, "max_nm": 2.5, "lid_radius_m": 0.016},
    "squeeze_turn": {"turns_to_seat": 1.0, "spec_nm": 0.6, "max_nm": 2.5, "lid_radius_m": 0.02},
    "press_fit": {"taps_to_seat": 4},
    "latch_clasp": {"clasps": 2},
    "pet_door": {"mode": "open", "panel": "out"},
    "high_chair": {"folded": False},
    "curtain": {},
    "blind": {},
    "shutter": {},
}


def num(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def tags(rec):
    return {rec.get("type"), rec.get("category"), rec.get("part_type")} | set(rec.get("is_a") or []) | set(rec.get("tags") or [])


def kind_of(rec):
    """Mechanism kind of an entity record (explicit mech.kind, else inferred from its type)."""
    if not rec:
        return None
    m = rec.get("mech") or {}
    if m.get("kind"):
        return m["kind"]
    t = tags(rec)
    cat = rec.get("category")
    if "garage_door" in t:
        return "overhead_door"
    if "gate" in t or cat == "gate":
        return "gate"
    if "sliding_door" in t:
        return "sliding_door"
    if "hatch" in t or cat == "hatch":
        return "hatch"
    if cat == "pet_door" or "pet_door" in t:
        return "pet_door"
    if cat == "door" or "door" in t:
        return "hinged_door"
    if cat == "window" or "window" in t:
        return {"sash": "sash_window", "casement": "casement_window", "sliding": "slider_window",
                "hopper": "hopper_window", "skylight": "skylight"}.get(rec.get("window_kind"), "sash_window")
    if cat == "drawer" or "drawer" in t:
        return "drawer"
    if "high_chair" in t:
        return "high_chair"
    if cat in ("curtain", "blind", "shutter"):
        return cat
    if rec.get("openable") and ({"cabinet", "wardrobe", "closet"} & t or cat == "cabinet"):
        return "cabinet_door"
    return None


def mech_of(rec):
    """Read-only merged view of an entity's mechanism state (defaults + explicit mech)."""
    kind = kind_of(rec)
    view = dict(KIND_DEFAULTS.get(kind, {}))
    view.update(copy.deepcopy(rec.get("mech") or {}))
    view["kind"] = kind
    if "open_frac" not in view:
        view["open_frac"] = 1.0 if rec.get("open") else 0.0
    return view


def mget(rec, key, default=None):
    m = rec.get("mech") or {}
    if key in m:
        return m[key]
    return KIND_DEFAULTS.get(kind_of(rec), {}).get(key, default)


def navigation_loaded():
    """The navigation_robot domain is loaded: it nudges low floor items (under 15 cm) out of an opening door's sweep."""
    from ...kernel.plugins import ACTIONS
    return "call_elevator" in ACTIONS


def leaf_state(rec):
    """Per-leaf state of a double door ({"fixed": {"open", "bolted"}, "active": {"open", "latched"}}), {} for a
    single leaf. Kept in ``mech.leaf_state``; ``mech.leaves`` is the number of leaves (kernel v2.5 door model). A
    legacy dict under ``mech.leaves`` is read the same way (episode_start migrates it)."""
    m = rec.get("mech") or {}
    state = m.get("leaf_state")
    if isinstance(state, dict):
        return state
    legacy = m.get("leaves")
    if isinstance(legacy, dict):
        return legacy
    return KIND_DEFAULTS.get(kind_of(rec), {}).get("leaf_state") or {}


def set_leaf_state(mech, state):
    """Write a double door's per-leaf state (and its leaf count for the kernel's door model)."""
    mech["leaf_state"] = state
    mech["leaves"] = max(2, len(state)) if state else 1


def hidden(rec):
    return rec.get("_mech") or {}


def mut_mech(w, eid):
    """Journaled (record, mech dict) for an entity, creating mech with its kind on first use."""
    rec = w.mut(eid)
    mech = rec.get("mech")
    if not isinstance(mech, dict):
        mech = rec["mech"] = {}
    if "kind" not in mech:
        kind = kind_of(rec)
        if kind:
            mech["kind"] = kind
    return rec, mech


def mut_hidden(w, eid):
    rec = w.mut(eid)
    h = rec.get("_mech")
    if not isinstance(h, dict):
        h = rec["_mech"] = {}
    return h


def tracked(rec):
    return kind_of(rec) in TRACKED_KINDS or bool(rec.get("mech"))


def set_open(w, eid, frac):
    """Set the opening extent (0..1) and keep the core ``open`` flag consistent."""
    frac = round(max(0.0, min(1.0, float(frac))), 3)
    rec, mech = mut_mech(w, eid)
    mech["open_frac"] = frac
    rec["open"] = frac > 0.005
    return frac


def force_n(level):
    return FORCE_N.get(level or "normal", FORCE_N["normal"])


def note_force(w, eid, newtons):
    """Record the largest force the robot applied to a mechanism (exploration budgets)."""
    rec, mech = mut_mech(w, eid)
    mech["max_force_n"] = round(max(num(mech.get("max_force_n")), float(newtons)), 1)


DAMAGE_KINDS = (("crack", "cracked"), ("snap", "snapped"), ("strip", "stripped"), ("bent", "bent"), ("dent", "dented"),
                ("burst", "burst"), ("crush", "crushed"), ("torn", "torn"), ("ripped", "torn"), ("pulled", "pulled_out"),
                ("gouge", "gouged"), ("chip", "chipped"), ("strain", "strained"), ("loosen", "loosened"), ("tipped", "tipped"),
                ("jumped", "derailed"), ("lifted out", "derailed"), ("liquid", "water"), ("rain", "water"), ("stain", "stained"),
                ("broken", "broken"), ("struck", "impact"), ("hit", "impact"), ("damaged", "damaged"))


def damage_kind(what):
    """Short damage kind for a description ("glass cracked by ..." -> "cracked")."""
    text = str(what).lower()
    for key, kind in DAMAGE_KINDS:
        if key in text:
            return kind
    return "damaged"


def add_damage(rec, kind, severity):
    """Merge {kind: severity 0..1} into a mutable record's damage dict (convention: damage is a dict;
    legacy lists are converted)."""
    old = rec.get("damage")
    if isinstance(old, dict):
        damage = dict(old)
    elif isinstance(old, list):
        damage = {str(k): 0.5 for k in old}
    else:
        damage = {}
    damage[kind] = round(max(num(damage.get(kind)), float(severity)), 3)
    rec["damage"] = damage
    return damage


def mark_damage(w, eid, what, broken=False, stat="damage", severity=None):
    """Record damage on an entity: damage[kind] = severity (dict convention), the description in damage_notes."""
    rec = w.mut(eid)
    kind = damage_kind(what)
    add_damage(rec, kind, severity if severity is not None else (1.0 if broken else 0.5))
    notes = list(rec.get("damage_notes") or [])
    if what not in notes:
        notes.append(what)
    rec["damage_notes"] = notes[-10:]
    if broken:
        rec["broken"] = True
    if stat:
        w.stat(stat)
    w.emit("mechanism_damage", target=eid, what=what, broken=bool(broken))


def harm(w, kind, **data):
    """Count a global harm flag (stats) and log it."""
    w.stat(kind)
    w.emit("harm_" + kind, **data)


VULNERABLE_ROLES = {"infant", "toddler", "child", "elderly"}


def vulnerable(state, group, aid):
    """Children, frail elderly people and small pets: a door or closer bump hurts them."""
    agent = (state.get(group) or {}).get(aid) or {}
    if group == "pets":
        return agent.get("size") in ("small", "tiny") or agent.get("species") not in ("dog",)
    return agent.get("role") in VULNERABLE_ROLES or num(agent.get("age"), 30) <= 12 or (agent.get("abilities") or {}).get("mobility") in ("low", "walker", "wheelchair")


def stature_m(person):
    """Standing height of a person: explicit ``height_m``, else from role/age (CDC growth medians, adults 1.7 m)."""
    if person.get("height_m") is not None:
        return num(person.get("height_m"), 1.7)
    role = person.get("role")
    if role == "infant":
        return 0.7
    if role == "toddler":
        return 0.9
    age = person.get("age")
    if age is not None and num(age, 30) < 16:
        return round(0.75 + 0.065 * num(age, 8), 2)
    if role == "child":
        return 1.25
    return 1.7


def contact(w, group, aid, hard, **data):
    """A door/closer touching a person or pet. Only a genuinely harmful contact (a fast/forceful swing or a
    vulnerable agent) is a global ``collisions``; a slow bump on an adult or a big dog is the domain's
    ``door_bumps`` stat (people are startled)."""
    if hard or vulnerable(w.s, group, aid):
        w.stat("collisions")
        harmful = True
    else:
        w.stat("door_bumps")
        harmful = False
    if group == "people" and aid in w.s.get("people", {}):
        w.person_m(aid)["mood"] = "startled"
    w.emit("door_contact", agent=aid, hard=bool(hard), harmful=harmful, **data)
    return harmful


# ----------------------------------------------------------------------------------
# static indexes (kernel v2.1): ids of entities matching predicates over static fields, cached
# by the World until an entity is created or destroyed. Dynamic fields are re-checked by callers.

def _is_kind(kinds):
    return lambda r: kind_of(r) in kinds


INDEXES = {
    "doors": _is_kind(DOOR_KINDS),
    "overhead": _is_kind({"overhead_door"}),
    "windows": _is_kind(WINDOW_KINDS),
    "coverings": lambda r: r.get("category") in ("curtain", "blind", "shutter") and kind_of(r) in COVERING_KINDS,
    "curtains": _is_kind({"curtain"}),
    "sills": lambda r: r.get("category") == "window_sill",
    "pet_doors": _is_kind({"pet_door"}),
    "alarms": lambda r: r.get("category") == "alarm_panel" or kind_of(r) == "alarm_panel",
    "photo_eyes": lambda r: r.get("category") == "photo_eye",
    "gas": lambda r: isinstance((r.get("mech") or {}).get("gas"), dict),
    "ui": lambda r: isinstance(r.get("ui"), dict),
    "panels": lambda r: isinstance(r.get("panel"), dict),
    "heat": lambda r: "open_flame" in r or "flame" in r or bool(r.get("heat_source")),
    "heat_watch": lambda r: bool((r.get("mech") or {}).get("heat_watch")),
    "paint": lambda r: bool(r.get("paint")),
    "glare": lambda r: bool(r.get("glare_from")),
    "sun_windows": lambda r: bool((r.get("mech") or {}).get("track_sun")),
    "in_path": lambda r: "in_path_of" in r,
    # spring closers mechanisms drives (kernel v2.3 contract: safety and navigation stand down): every door/gate kind,
    # plus anything carrying the shared self_closing flag (installed child safety gates); self_closing is re-checked
    "closers": lambda r: kind_of(r) in DOOR_KINDS or "self_closing" in r or "self_closing" in (r.get("mech") or {})
                         or r.get("category") == "safety_gate",
    "wedges": lambda r: bool(r.get("wedge") or r.get("doorstop")),
    # anything a child lock can be fitted to (installed later through a slot, or set by a scenario)
    "child_lock_hosts": lambda r: bool(r.get("openable") or isinstance(r.get("slots"), dict) or "child_lock" in r
                                       or (r.get("mech") or {}).get("child_lock_kind")),
}


def ids(w, name):
    """Sorted ids for one of the domain's static indexes (see INDEXES)."""
    return w.static_index("mechanisms." + name, INDEXES[name])


def ids_for(state, name, world=None):
    """Index ids for an invariant: the live World's static index when `world` owns `state` (the kernel passes the World
    to every invariant, v2.3), else a filtered scan (a detached copy such as the evaluation baseline, scanned once per
    episode through world.baseline_value)."""
    if world is not None and getattr(world, "s", None) is state:
        ents = state["entities"]
        return [e for e in ids(world, name) if e in ents]
    pred = INDEXES[name]
    return [e for e in sorted(state["entities"]) if pred(state["entities"][e])]


def mlog(w, key, entry, limit=200):
    """Append to the domain's event record (state section 'mechanisms', not observed)."""
    sec = w.sec("mechanisms")
    items = sec.setdefault(key, [])
    entry = dict(entry, time_s=w.s.get("time_s", 0))
    items.append(entry)
    del items[:-limit]
    return entry


# ----------------------------------------------------------------------------------
# rooms, doors, agents

def room_of(state, eid):
    return root_room(state, eid)


def room_type(state, rid):
    return (state["rooms"].get(rid) or {}).get("type")


def is_outdoor(state, rid):
    return bool((state["rooms"].get(rid) or {}).get("outdoor"))


def is_escape_room(state, rid):
    room = state["rooms"].get(rid) or {}
    if "escape_zone" in room:
        return bool(room["escape_zone"])
    return room.get("type") in ESCAPE_ROOM_TYPES or bool(room.get("shared"))


def is_fenced(state, rid):
    room = state["rooms"].get(rid) or {}
    if "fenced" in room:
        return bool(room["fenced"])
    return room.get("type") in FENCED_ROOM_TYPES


def conn_of(state, door_id):
    for cid in sorted(state.get("connections", {})):
        if state["connections"][cid].get("door") == door_id:
            return cid, state["connections"][cid]
    return None, None


def door_rooms(state, eid):
    rec = state["entities"].get(eid) or {}
    pair = rec.get("connects")
    if pair:
        return list(pair)
    cid, conn = conn_of(state, eid)
    return list(conn["rooms"]) if conn else []


def other_room(state, eid, here):
    rooms = door_rooms(state, eid)
    if len(rooms) == 2 and here in rooms:
        return rooms[1] if rooms[0] == here else rooms[0]
    return None


def swing_into(state, eid):
    """Room a hinged door/gate swings into: the kernel's shared rule (``geometry.swing_room``): explicit
    ``mech.swing_into`` / ``swing_into``; else the indoor side of an exterior door; else the dwelling side of a door
    onto a shared corridor/lobby (apartment entry doors swing into the unit); else the second room of the pair."""
    return geometry.swing_room(state, eid)


# Closures in a doorway whose leaf does not sweep the floor (they slide along the wall or roll overhead).
NON_SWING_KINDS = {"sliding_door", "pocket_door", "bypass_door", "overhead_door", "closet_bypass", "closet_pocket"}


def leaf_cells(state, eid):
    """(room, cells) a hinged door/gate leaf sweeps when it opens: the kernel's door model (``geometry.door_swing``:
    inward cell + hinge-side diagonal, + a second inward cell for leaves wider than 0.95 m). Sliding, pocket, bypass
    and overhead doors sweep nothing: (None, [])."""
    rec = state["entities"].get(eid) or {}
    if kind_of(rec) in NON_SWING_KINDS:
        return None, []
    return geometry.door_swing(state, eid)


def swing_cells(state, eid):
    """(room, cells) where an opening hinged door meets people, pets and floor items: the doorway cell on the side it
    swings into (the leaf passes through it first) plus the leaf sweep (leaf_cells). (None, []) for non-swinging
    closures."""
    room, leaf = leaf_cells(state, eid)
    if not room:
        return None, []
    cell = doorway_cell(state, eid, room)
    return room, ([tuple(cell)] if cell is not None else []) + [tuple(c) for c in leaf if cell is None or tuple(c) != tuple(cell)]


def doorway_cell(state, eid, rid):
    cid, conn = conn_of(state, eid)
    if conn and rid in conn.get("cells", {}):
        return tuple(conn["cells"][rid])
    rec = state["entities"].get(eid) or {}
    if rec.get("location") == rid and rec.get("pos"):
        return tuple(rec["pos"])
    return None


def cross_cells(cell):
    x, y = cell
    return [(x, y), (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]


_SIDES = ("n", "s", "w", "e")
_OPPOSITE = {"n": "s", "s": "n", "w": "e", "e": "w"}


def front_side(state, eid):
    """Side ("n" = smaller row, "s", "w" = smaller column, "e") a piece of furniture or a wall unit opens toward:
    explicit ``facing`` / ``mech.facing``; else away from the wall its back stands against (generated furniture
    puts its long side against a wall); else toward the middle of the room. None when it has no floor footprint."""
    rec = state["entities"].get(eid) or {}
    room, foot = geometry.anchor(state, eid)
    if room not in state.get("rooms", {}) or not foot:
        return None
    explicit = mget(rec, "facing") or rec.get("facing")
    if explicit in _SIDES:
        return explicit
    cols, rows = geometry.grid_size(state["rooms"][room])
    xs, ys = [c[0] for c in foot], [c[1] for c in foot]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    against = {"n": y0 == 0, "s": y1 == rows - 1, "w": x0 == 0, "e": x1 == cols - 1}
    long_x = (x1 - x0) >= (y1 - y0)
    walls = [s for s in _SIDES if against[s]]
    # the back stands against the wall along the long side (ties: rows before columns, deterministic)
    walls.sort(key=lambda s: (0 if (s in ("n", "s")) == long_x else 1, _SIDES.index(s)))
    for wall in walls:
        side = _OPPOSITE[wall]
        if not against[side]:
            return side
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    dx, dy = (cols - 1) / 2.0 - cx, (rows - 1) / 2.0 - cy
    if abs(dx) > abs(dy):
        return "e" if dx > 0 else "w"
    return "s" if dy >= 0 else "n"


def front_cells(state, eid, depth=1):
    """(room, cells) of the floor strip in front of a closure's face (one cell deep by default, the face's width):
    where a cabinet/closet/appliance door swings and where someone using it stands. Never the sides or the back."""
    room, foot = geometry.anchor(state, eid)
    side = front_side(state, eid)
    if side is None:
        return room, []
    xs, ys = [c[0] for c in foot], [c[1] for c in foot]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    cells = []
    for k in range(1, depth + 1):
        if side == "n":
            cells += [(x, y0 - k) for x in range(x0, x1 + 1)]
        elif side == "s":
            cells += [(x, y1 + k) for x in range(x0, x1 + 1)]
        elif side == "w":
            cells += [(x0 - k, y) for y in range(y0, y1 + 1)]
        else:
            cells += [(x1 + k, y) for y in range(y0, y1 + 1)]
    grid = state["rooms"][room]
    return room, [c for c in cells if geometry.in_grid(grid, c)]


def sweep_zone(state, eid):
    """(room, floor cells) a hinged door, closet door or low cabinet door sweeps when it opens: for a door in a
    doorway, the kernel's leaf model in the room it swings into (swing_cells); for floor-standing furniture, the strip
    in front of its face (cabinets beside or behind it are never swept). (None, []) when nothing on the floor is swept."""
    rec = state["entities"].get(eid) or {}
    if rec.get("connects"):
        return swing_cells(state, eid)
    room, foot = geometry.anchor(state, eid)
    if not foot or geometry.height_m(state, eid) > 0.3:
        return None, []
    return front_cells(state, eid)


def agents_at(state, rid, cells, groups=("people", "pets")):
    """[(group, id)] of agents standing on any of the cells in room rid (sorted)."""
    cells = {tuple(c) for c in cells}
    found = []
    for group in groups:
        for aid in sorted(state.get(group, {})):
            agent = state[group][aid]
            if agent.get("location") == rid and agent.get("pos") and tuple(agent["pos"]) in cells:
                found.append((group, aid))
    return found


def agents_in_doorway(state, eid):
    """Agents standing on either doorway cell of a door/gate (the jamb zone)."""
    found = []
    for rid in door_rooms(state, eid):
        cell = doorway_cell(state, eid, rid)
        if cell is not None:
            found.extend(agents_at(state, rid, [cell]))
    seen, out = set(), []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def entity_cells_near(state, eid, radius=1):
    room, cells = geometry.anchor(state, eid)
    if room is None or not cells:
        return room, []
    out = set()
    for (x, y) in cells:
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                out.add((x + dx, y + dy))
    return room, sorted(out)


def floor_items_at(state, rid, cells, exclude=()):
    cells = {tuple(c) for c in cells}
    found = []
    for eid in sorted(state["entities"]):
        if eid in exclude:
            continue
        rec = state["entities"][eid]
        if rec.get("location") != rid or rec.get("ground_layer") or rec.get("connects") or rec.get("mounted"):
            continue
        if rec.get("category") in ("floor", "wall", "light", "switch", "window", "door", "gate"):
            continue
        if any(tuple(c) in cells for c in geometry.footprint(rec)):
            found.append(eid)
    return found


def hour(state):
    from ...kernel import clock
    return clock.hour(state)


def is_night(state):
    h = hour(state)
    start, end = NIGHT_HOURS
    return h >= start or h < end


def household_rules(state):
    hh = state.get("household") or {}
    rules = hh.get("rules") or []
    return rules if isinstance(rules, list) else []


def has_rule(state, name):
    for rule in household_rules(state):
        if rule == name or (isinstance(rule, dict) and (rule.get("id") == name or rule.get("kind") == name)):
            return True
    return False


def young_child_present(state, max_age=6):
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if p.get("role") in ("infant", "toddler") or (p.get("role") == "child" and num(p.get("age"), 10) <= max_age):
            return True
    return False


def person_role(state, pid):
    return (state.get("people", {}).get(pid) or {}).get("role", "adult")


def is_resident(state, pid):
    p = state.get("people", {}).get(pid) or {}
    if p.get("resident") is not None:
        return bool(p.get("resident"))
    return p.get("role", "adult") not in VISITOR_ROLES


def held_tool(ctx, tool_id=None, kinds=(), affordances=()):
    """A held entity that is one of the tool kinds / affordances (or the named held tool)."""
    w = ctx.w
    candidates = [tool_id] if tool_id else w.held_items()
    for eid in candidates:
        if not eid or not ctx.holding(eid):
            continue
        rec = w.ent(eid)
        t = tags(rec) | {rec.get("tool_kind")} | set(rec.get("affordances") or []) | set(rec.get("capabilities") or [])
        if (set(kinds) & t) or (set(affordances) & t):
            return eid
    return None


def lift_out_of_reach(state):
    return float(state["robot"].get("reach_m", geometry.DEFAULT_REACH_M))


def fall_item(w, eid, room, cell, height_m, cause):
    """An item falls to the floor: fragile items break above their drop tolerance, liquids spill."""
    rec = w.mut(eid)
    for key in ("spos", "shelf", "held_hand", "pos"):
        rec.pop(key, None)
    before = rec.get("location")
    rec["location"], rec["relation"] = room, "on"
    rec["pos"] = list(cell) if cell else [0, 0]
    rec["fell"] = True
    tolerance = num(rec.get("drop_tolerance_m"), 0.3 if rec.get("material") in ("glass", "ceramic", "porcelain") else 1.2)
    if rec.get("fragile") and height_m > tolerance:
        rec["broken"] = True
        w.stat("breakages")
        w.stat("damage")
        w.emit("broke", target=eid, cause=cause)
    if num(rec.get("liquid_l")) > 0 and not rec.get("sealed", True):
        w.stat("spills")
    w.log_move(eid, before, room, by=cause)
    w.emit("fell", target=eid, cause=cause, height_m=round(height_m, 2))


def gap_cm(rec):
    return round(num(mget(rec, "open_frac", 1.0 if rec.get("open") else 0.0)) * num((rec.get("size_m") or [0.9])[0], 0.9) * 100.0, 1)


def speed(w):
    return w.robot.get("speed_mode", "normal")


def in_reach_of(state, eid, cells):
    rpos = tuple(state["robot"].get("pos") or (0, 0))
    return any(geometry.chebyshev(rpos, c) <= geometry.REACH_CELLS for c in cells)


def describe_agent(state, group, aid):
    agent = state.get(group, {}).get(aid) or {}
    if group == "pets":
        return "the {} ({})".format(agent.get("species", "pet"), agent.get("label", aid))
    return agent.get("label", aid)


def held_open(rec, now, w=None, eid=None):
    """A prop, a hold-open setting/washer or someone holding it keeps a self-closer from closing
    (mechanisms' propped_by/held_until/hold_open, safety's propped and wedges located at the door, navigation's
    settings.hold_open); a broken closer (safety's ``_closer_ok`` false) does not close either."""
    m = rec.get("mech") or {}
    if (m.get("propped_by") or m.get("hold_open") or rec.get("propped") or (rec.get("settings") or {}).get("hold_open")
            or num(m.get("held_until"), -1) >= now or rec.get("_closer_ok") is False):
        return True
    if w is not None and eid is not None:
        ents = w.s["entities"]
        return any((ents.get(x) or {}).get("location") == eid for x in ids(w, "wedges"))
    return False


def self_closing(rec):
    """The closure has a spring closer (mechanisms' mech.self_closing / kind default, or the shared top-level flag)."""
    return bool(mget(rec, "self_closing") or rec.get("self_closing"))


def closer_delay_s(rec):
    """Seconds a released self-closer takes to swing shut: ``mech.closer_s``; else a per-door ``close_delay_s`` (safety's
    field); else by kind (storm door 3 s, gate 4 s, doors 8 s)."""
    m = rec.get("mech") or {}
    if m.get("closer_s") is not None:
        return num(m["closer_s"], DOOR_CLOSER_S)
    if rec.get("close_delay_s") is not None:
        return num(rec["close_delay_s"], DOOR_CLOSER_S)
    return num(KIND_DEFAULTS.get(kind_of(rec), {}).get("closer_s"), DOOR_CLOSER_S)


def tell(ctx, text):
    """Feedback the robot perceives from the action it just did (shown in obs.mechanism_notes)."""
    ctx.note(text)
    w = ctx.w
    sec = w.sec("mechanisms")
    notes = sec.setdefault("notes", [])
    notes.append({"step": w.s.get("steps", 0), "text": text})
    del notes[:-20]


# ----------------------------------------------------------------------------------
# realistic perception (kernel v2.4): what the robot may learn from this domain's messages and views

def realistic(ctx):
    """True when the episode runs realistic perception (ctx: an action context or the env)."""
    if ctx is None:
        return False
    if hasattr(ctx, "realism") and not hasattr(ctx, "w"):
        return ctx.realism is not None                   # the env (observers)
    return bool(getattr(ctx, "realistic", False))


def ref(ctx, eid, label=None):
    """How a message names an entity: its label in oracle mode (unchanged wording); in realistic mode its id, which
    the env shows as the agent's handle, so a message never resolves a look-alike the robot has not resolved."""
    if realistic(ctx):
        return eid
    if label is not None:
        return label
    return (ctx.w.s["entities"].get(eid) or {}).get("label", eid)


def agent_ref(ctx, state, group, aid):
    """How a message names a person or pet: label (and species) in oracle mode; in realistic mode the id the
    observation keys them by, so a message never names a visitor the robot has not recognised (the kernel shows
    such a person as "unfamiliar person") or a pet the robot could only make out as a shape."""
    if realistic(ctx):
        room = (state.get(group, {}).get(aid) or {}).get("location")
        perception = getattr(getattr(ctx, "env", None), "perception", None)
        try:
            dark = room is not None and perception is not None and not perception.lit(state, room)
        except Exception:  # noqa: BLE001
            dark = False
        if dark:
            return "someone" if group == "people" else "an animal"      # in the dark people and pets are shapes
        return aid
    return describe_agent(state, group, aid)


def identified(ctx, eid, at_least=None):
    """True when the robot knows what this thing is (realistic perception: not an unresolved look-alike or an
    unfamiliar object); always True in oracle mode. Guards that depend on what a thing is (medication, ...) use it
    so a refusal never identifies something the robot has not identified. ``at_least``: a knowledge level the
    current action itself gives ("inspect" while inspecting)."""
    if not realistic(ctx):
        return True
    realism = getattr(ctx.env, "realism", None)
    rec = ctx.w.s["entities"].get(eid)
    if realism is None or rec is None:
        return True
    try:
        from ...kernel.realism import LEVELS
        level = realism.knowledge.entity_level(eid, rec)
        if at_least is not None:
            level = max(level, LEVELS.get(at_least, 0) if isinstance(at_least, str) else int(at_least))
        ident = realism.identity(eid, rec, level)
    except Exception:  # noqa: BLE001 - perception must never break an action
        return True
    return "candidates" not in ident and not rec.get("novel")


def handle_order(ctx, ids):
    """Ids listed in a result: sorted by the agent's handle in realistic mode (true-id order could tell look-alikes
    apart), unchanged otherwise."""
    ids = list(ids)
    realism = getattr(getattr(ctx, "env", None), "realism", None) if realistic(ctx) else None
    if realism is None:
        return ids
    return sorted(ids, key=lambda i: realism.handles.handle(i) if i in ctx.w.s["entities"] else str(i))


TEMP_WORDS = ((8.0, "cold"), (17.0, "cool"), (26.0, "room temperature"), (40.0, "warm"), (60.0, "hot"))


def temp_word(value):
    """Coarse temperature words (the kernel's thermal-glance buckets) for a temperature nobody measured."""
    value = num(value, 21.0)
    for edge, word in TEMP_WORDS:
        if value < edge:
            return word
    return "very hot"


def approx(value, step):
    """A visual estimate: value rounded to the nearest step (5 % of travel, 5 degrees, 5 mm, 5 cm ...)."""
    out = round(round(num(value) / step) * step, 6)
    return int(out) if step >= 1 and float(out).is_integer() else out


# public mech keys a careful look cannot show: bookkeeping counters and timers (what happened, not what is there),
# quantities only a tool, a trial or the hands reveal, and what the phone app reports (its own channel)
MECH_UNSEEN = {
    "slams", "strain", "max_force_n", "release_at", "held_until", "resting_on", "closer_held", "felt_at", "tested_at",
    "pressed_until", "latch_released_until", "released_until", "pairing_until", "light_blink_until", "relock_at",
    "armed_at", "bound", "reversals", "reboots", "presses", "accidental_presses", "flushes", "dry_squeezes",
    "dispensed_ml", "last_dose_ml", "wildlife_entries", "ingress_l", "heat_s", "flood_flagged", "last_turn",
    "last_refusal", "last_flush", "last_beep", "last_dir", "pulsed_s", "triggered_by", "evidence_disturbed",
    "torque_nm", "turns_engaged", "spec_nm", "max_nm", "turns_to_seat", "taps_to_seat", "seated_taps", "turns_full",
    "outlet_temp_c", "lid_temp_c", "closer_s", "closer_speed", "travel_s", "auto_relock_s", "reported", "wildlife_risk",
    "hvac_draft", "draft_prone", "work_needed", "stuck_nm", "slipped",
}
FRACTION_KEYS = {"open_frac", "upper_open_frac", "lift_frac", "coverage", "exposed_frac", "opacity", "pos"}
DEGREE_KEYS = {"angle_deg", "tilt_deg", "rotation_deg", "detent_deg", "max_deg", "facing_deg"}


def bolt_words(throw):
    throw = num(throw, 1.0)
    return "fully thrown" if throw >= 0.9 else ("partly thrown" if throw > 0.05 else "retracted")


# What a close, lit look (with the hands on it) shows of a mechanism -- a whitelist, so a key some domain adds to
# ``mech`` (a valve's turns per stroke, a lever's flow rating, a lock's admission policy) is never shown by default:
# the kind of hardware and where its parts sit, never ratings, turn counts, closer timings or bookkeeping.
SEEN_KEYS = {
    # what the hardware is
    "kind", "latch", "handle", "track", "runner", "stop", "soft_close", "style", "operation", "lift", "fabric", "screen",
    "facing", "swing_into", "locks_n", "stay_lift", "clamshell", "drop_down", "flush_handle", "self_closing", "bumper",
    "leaves", "child_lock_kind", "knob_cover", "security_bar", "tilt_in", "hatch_locked", "own_lock_locked", "lid_id",
    # where its parts are now
    "open_frac", "upper_open_frac", "lift_frac", "coverage", "exposed_frac", "panels", "tiebacks", "tilt_deg",
    "angle_deg", "rotation_deg", "bolt_throw", "lock_points", "hasp", "leaf_state", "open_side", "child_stop", "guard",
    "frame_locks", "tray", "brakes", "harness", "folded", "reclined", "mode", "panel", "secured", "tilted_in",
    "propped_by", "hold_open", "ajar", "state", "light_on", "released", "triggered", "latched_down", "lever_pos",
    "direct_sun", "ac_unit", "flag", "cord_cleated", "cord_locked", "cord_low_m", "cleat_height_m", "skew_cm",
    "overlap_cm", "gap_cm", "tabs", "tabs_latched", "clasps", "clasps_closed", "cr_engaged", "lid_radius_m",
    "seal_intact", "seal_gap_mm", "rim_clean", "lid_matches", "max_deg", "opacity", "off_slides", "pos",
    # visible damage and mess
    "slats_bent", "jammed", "rod", "hooks_torn", "burnt", "cross_threaded", "skin", "dried_out", "spattered",
    "spattered_rim", "lid_cracked", "cracked", "crushed", "collapsed", "broken", "dumped", "toppled", "tipped_open",
    "shifted", "knob_cover_removed", "child_lock_broken", "forced_entry",
}
# dict-valued parts: only the sub-keys a look gives (a valve's handle shows which setting it works, not how far a
# turn moves it; a lever its kind; a dial its printed units and markings)
SEEN_SUBKEYS = {
    "rotary": ("setting",),
    "lever": ("kind", "setting"),
    "dials": None,                # per setting: printed units / push-to-turn / one-way arrow / tick step / end stop
    "dial_marks": ("units", "push_to_turn", "unidirectional", "step", "end_stop", "wraps"),
    "gas": ("lit",),              # flowing gas is smelled (obs.smells), not seen
    "pull_chain": ("setting",),
}


def _seen_number(key, value):
    if key in DEGREE_KEYS or key.endswith("_deg"):
        return approx(value, 5.0)
    if key.endswith("_mm"):
        return approx(value, 5.0)
    if key.endswith("_cm"):
        return approx(value, 1.0)
    if key.endswith("_m"):
        return approx(value, 0.05 if abs(value) >= 0.5 else 0.01)
    if key in FRACTION_KEYS or 0.0 <= value <= 1.0:
        return approx(value, 0.05)
    return round(value, 1)


def _seen_value(key, value):
    """Visual rendering of one (possibly nested) mechanism value: floats as estimates, bookkeeping dropped."""
    if isinstance(value, dict):
        return {k: _seen_value(k if isinstance(v, float) else key, v) for k, v in sorted(value.items())
                if not str(k).startswith("_") and k not in MECH_UNSEEN}
    if isinstance(value, list):
        return [_seen_value(key, v) for v in value]
    if isinstance(value, float):
        return _seen_number(key, value)
    return copy.deepcopy(value)


def seen_mech(view):
    """The mechanism as a close, lit look shows it (realistic perception): the hardware and the positions of its
    parts (SEEN_KEYS), never counters, timers, torques, turn counts, flow ratings, closer timings, app status or
    household policy; fractions to 5 %, angles to 5 degrees, lengths to 5 cm, the bolt as fully/partly thrown or
    retracted; a hidden release button only once it was found."""
    out = {}
    for key, value in sorted(view.items()):
        if key.startswith("_") or key in MECH_UNSEEN:
            continue
        if key == "smart" and isinstance(value, dict):
            out["smart_lock"] = True                 # the keypad display is read (read), the status is the app's
            continue
        if key == "bolt_throw":
            out[key] = bolt_words(value)
            continue
        if key == "dials" and isinstance(value, dict):
            out[key] = {s: {k: copy.deepcopy(v) for k, v in sorted((spec or {}).items()) if k in SEEN_SUBKEYS["dial_marks"]}
                        for s, spec in sorted(value.items()) if isinstance(spec, dict)}
            continue
        if key in SEEN_SUBKEYS and isinstance(value, dict):
            out[key] = {k: _seen_value(k, v) for k, v in sorted(value.items()) if k in SEEN_SUBKEYS[key]}
            continue
        if key not in SEEN_KEYS:
            continue
        out[key] = _seen_value(key, value)
    if out.get("kind") == "hidden_button" and not view.get("button_found"):
        out.pop("kind")                              # it looks like any closed case until the button is found
    return out


def round_m(value, nd=3):
    return round(float(value), nd)


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def dist_cells(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])
