"""Derived support graph, loads, ratings, baseline allowance, composite centre of mass and the carried-load envelope
(tier-1 physics, docs/design/TIER1_PHYSICS.md 5.1 and TIER1_INTEGRATION.md 1.6; fidelity G for the graph, P-approx
for loads).

Nothing in this module writes state or caches anything that could change an outcome: every value is a pure function
of the state (or of the episode baseline), so replays, other processes and strict mode see the same numbers.

* ``support_of(state, eid)`` names what holds an entity up and in which slot:

      (parent, ("shelf", k))      relation "in" and the parent has shelf_heights (k = the record's shelf, default 0)
      (parent, ("in",))           any other relation "in"
      (parent, ("surface",))      relation "on" onto an entity
      (parent, ("hung",))         relation "hung"
      (room,   ("floor", cell))   located in a room and not mounted (cell = (col, row), or None without a pos)
      (holder, ("held",))         held by the robot, a person or a pet
      None                        attached / part / under / behind / worn / mounted / doors: rigid with its parent,
                                  or not carried by it

* Masses: ``subtree_mass`` is the kernel's only implementation (core and the body delegate to it). Liquid adds
  ``liquid_l * density``: the record's ``liquid_density`` (kg/L), else liquids.json by the liquid's name, else water.
* Loads: ``load_on(sid, slot)`` sums the subtree masses resting in one slot; ``rating(sid, slot)`` is the effective
  rated load (per-instance variation already applied by props); ``baseline_load(env, sid, slot)`` is what the slot
  carried at episode start (the scene allowance of the overload law).
* ``com_height`` is the composite centre-of-mass height of a body and everything it carries, above its own base.
* ``held_envelope`` is the one geometry of what the robot carries (camera occlusion, body balance and stairs).

Every physical property comes through four accessors at the top (``phys``, ``liquid_rho``, ``floor_at``,
``contact_mu``), which call the data layer (kernel/phys/props.py and forces.py) lazily: this module imports without
it, and tests substitute those four functions.
"""
import math
from bisect import insort

from .. import geometry

# Carry geometry of humanoid_v1 (TIER1_INTEGRATION 1.2): one-hand loads hang from the hand at 1.0 m, two-hand bulky
# loads ride on the hips with their bottom at 0.85 m, 0.35 m in front of the body axis (half the torso depth plus
# forearm clearance).
# TIER1-STUB(BODY-ENGINE): body.profile(state)["carry"] replaces these local humanoid_v1 defaults.
CARRY = {"hand_z_m": 1.0, "hip_z_m": 0.85, "front_m": 0.35}
# camera 5.6: a two-hand load at least this big (horizontal x horizontal x height, m) blocks the forward view
VIEW_BLOCK_W_M, VIEW_BLOCK_D_M, VIEW_BLOCK_H_M = 0.35, 0.25, 0.25
INNER_FLOOR_M = 0.05        # base of contents in a container without shelves (geometry.height_m uses the same default)
BASELINE_MARGIN_KG = 0.5    # a slot holds what it held at episode start plus this, before its own rating applies
DEFAULT_SIZE_M = (0.1, 0.1, 0.1)
HOLDER_RELATIONS_RIGID = ("worn",)


# ----- accessors to the data layer (lazily imported; tests replace these four functions) ----------------------------

def phys(state, eid):
    """Resolved physical properties of an entity: props.props, a Phys record with per-instance variation applied.
    Every threshold, rating and coefficient of this layer comes through here."""
    from .props import props
    return props(state, eid)


def liquid_rho(name):
    """Density (kg/m3) of a named liquid: the liquids.json row (props.liquid_props), the default row when unknown."""
    from .props import liquid_props
    return float(liquid_props(name)["rho"])


def floor_at(state, rid, cell=None):
    """The floors.json row of a room's floor at a cell (props.floor_props: impact_hardness, scratch, mu, c_rr, ...)."""
    from .props import floor_props
    return floor_props(state, rid, cell)


def contact_mu(state, eid):
    """Dry-or-wet friction coefficient between an entity and what it rests on (forces.friction; on the floor the
    floors.json mu)."""
    entities = state["entities"]
    rec = entities[eid]
    loc = rec.get("location")
    if loc in entities:
        from .forces import friction
        return float(friction(rec, entities[loc]))
    if loc in state["rooms"]:
        return float(floor_at(state, loc, rec.get("pos"))["mu"])
    return float(phys(state, eid).mu)


# ----- small numeric helpers ---------------------------------------------------------------------------------------

def num(value, default=0.0):
    """value as a finite float, else default (records written by many domains are not always clean)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def finite(value):
    """value as a float when it is a finite number, else None (props uses inf for 'no limit')."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def dims(rec):
    """(w, d, h) of a record's size_m in its own frame (defaults for missing or malformed sizes)."""
    size = rec.get("size_m") or DEFAULT_SIZE_M
    w = num(size[0], DEFAULT_SIZE_M[0]) if len(size) > 0 else DEFAULT_SIZE_M[0]
    d = num(size[1], w) if len(size) > 1 else w
    h = num(size[2], DEFAULT_SIZE_M[2]) if len(size) > 2 else DEFAULT_SIZE_M[2]
    return max(0.0, w), max(0.0, d), max(0.0, h)


def yaw_of(rec):
    """An entity's own yaw in degrees (yaw_deg when a domain set one, else the grid rot)."""
    if "yaw_deg" in rec:
        return num(rec.get("yaw_deg")) % 360.0
    return num(rec.get("rot", 0)) % 360.0


# ----- the support graph -------------------------------------------------------------------------------------------

def _shelf_index(rec):
    try:
        return int(rec.get("shelf") or 0)
    except (TypeError, ValueError):
        return 0


def support_of(state, eid):
    """(support id, slot) holding eid up, or None (see the module docstring for the slots)."""
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None:
        return None
    loc = rec.get("location")
    relation = rec.get("relation", "on")
    if loc in entities:
        if relation == "in":
            if entities[loc].get("shelf_heights"):
                return loc, ("shelf", _shelf_index(rec))
            return loc, ("in",)
        if relation == "on":
            return loc, ("surface",)
        if relation == "hung":
            return loc, ("hung",)
        return None
    if loc in state["rooms"]:
        if rec.get("mounted") or "connects" in rec:
            return None
        pos = rec.get("pos")
        cell = (int(pos[0]), int(pos[1])) if isinstance(pos, (list, tuple)) and len(pos) >= 2 else None
        return loc, ("floor", cell)
    if loc == "robot" or loc in state.get("people", {}) or loc in state.get("pets", {}):
        if relation in HOLDER_RELATIONS_RIGID:
            return None
        return loc, ("held",)
    return None


def children_map(state):
    """{location: [entity ids, sorted]} for every entity (one O(N) pass). Settle builds one per phase and passes it
    down; consequences keep it current through ``move_child``."""
    entities = state["entities"]
    cmap = {}
    for eid in sorted(entities):
        cmap.setdefault(entities[eid].get("location"), []).append(eid)
    return cmap


def move_child(cmap, eid, old_location, new_location):
    """Keep a children map current after eid moved (no-op without a map)."""
    if cmap is None or old_location == new_location:
        return
    siblings = cmap.get(old_location)
    if siblings and eid in siblings:
        siblings.remove(eid)
    insort(cmap.setdefault(new_location, []), eid)


def _map(state, cmap):
    return children_map(state) if cmap is None else cmap


def slot_children(state, sid, slot, cmap=None):
    """Ids resting in one slot of a support, sorted."""
    cmap = _map(state, cmap)
    return [c for c in cmap.get(sid, ()) if support_of(state, c) == (sid, slot)]


def slots(state, sid, cmap=None):
    """{slot: [child ids]} of everything sid supports, slots in sorted order (shelves by index)."""
    cmap = _map(state, cmap)
    out = {}
    for child in cmap.get(sid, ()):
        found = support_of(state, child)
        if found is not None and found[0] == sid:
            out.setdefault(found[1], []).append(child)
    return {slot: out[slot] for slot in sorted(out, key=_slot_key)}


def _slot_key(slot):
    """Sort key of a slot: its kind, then the shelf index or the floor cell."""
    rest = slot[1] if len(slot) > 1 else None
    if isinstance(rest, int):
        return slot[0], rest, ()
    return slot[0], 0, tuple(rest) if isinstance(rest, tuple) else ()


def top_support(state, eid):
    """The highest entity in eid's location chain: the piece of furniture (or the held item, or eid itself) whose
    whole subtree moves and fails together. Attribution and the settle roots use it."""
    entities = state["entities"]
    current, seen = eid, {eid}
    while True:
        loc = entities.get(current, {}).get("location")
        if loc not in entities or loc in seen:
            return current
        seen.add(loc)
        current = loc


def descendants(state, eid, cmap=None):
    """eid's descendants in the order World.descendants returns them (depth-first from the end of each sorted child
    list), so sums over them match core's float results bit for bit."""
    cmap = _map(state, cmap)
    out, stack, seen = [], [eid], {eid}
    while stack:
        current = stack.pop()
        for child in cmap.get(current, ()):
            if child in seen:
                continue
            seen.add(child)
            out.append(child)
            stack.append(child)
    return out


# ----- mass and loads ----------------------------------------------------------------------------------------------

def liquid_density(rec):
    """kg per litre of the liquid a record holds: its liquid_density, else liquids.json by name, else water."""
    explicit = finite(rec.get("liquid_density"))
    if explicit is not None:
        return explicit
    return liquid_rho(rec.get("liquid") or "water") / 1000.0


def own_mass(rec):
    """An entity's own mass plus the liquid it holds (kg); the data layer is consulted only for liquids."""
    mass = num(rec.get("mass_kg"), 0.0)
    liters = num(rec.get("liquid_l"), 0.0)
    if liters > 0:
        return mass + liters * liquid_density(rec)
    return mass


def subtree_mass(state, eid, cmap=None):
    """Mass (kg) of eid and everything in, on, hung from or attached to it, liquids included. The summation order is
    World.descendants', so it reproduces core's historical totals exactly for water and explicit densities."""
    entities = state["entities"]
    if eid not in entities:
        return 0.0
    total = 0.0
    for item in [eid] + descendants(state, eid, cmap):
        total += own_mass(entities[item])
    return total


def load_on(state, sid, slot, cmap=None):
    """Load (kg) resting in one slot of a support."""
    cmap = _map(state, cmap)
    return sum(subtree_mass(state, c, cmap) for c in slot_children(state, sid, slot, cmap))


def load_above(state, eid, cmap=None):
    """Mass (kg) bearing down on eid: everything resting on it (recursively, through the subtree masses) plus the ids
    the ``phys.items_above`` hooks report (manipulation's packing order inside containers)."""
    from ..plugins import run_hooks
    cmap = _map(state, cmap)
    above = slot_children(state, eid, ("surface",), cmap)
    counted = set(above)
    for extra in run_hooks("phys.items_above", state, eid):
        for other in sorted(extra or ()):
            if other in state["entities"] and other not in counted and other != eid:
                counted.add(other)
                above.append(other)
    return sum(subtree_mass(state, c, cmap) for c in above)


def rating(state, sid, slot):
    """Effective rated load (kg) of one slot, or None when the slot never fails: floors, the robot's hands (the body
    owns its capacity) and the surface or bottom of a portable support at rest (a carried container fails by the
    handle law instead). Values come from props with per-instance variation applied; inf means unlimited."""
    kind = slot[0]
    if kind in ("floor", "held"):
        return None
    rec = state["entities"].get(sid)
    if rec is None:
        return None
    if kind in ("surface", "in") and rec.get("portable"):
        return None
    p = phys(state, sid)
    value = {"shelf": p.shelf_rating_kg, "surface": p.rating_kg, "hung": p.hang_kg, "in": p.rating_kg}.get(kind)
    return finite(value)


def baseline_load(env, sid, slot):
    """Load the slot carried at episode start (generator and setup), from env._baseline with its own children map;
    0 when there is no baseline or sid did not exist then. Memoised per episode on the env object (never stored)."""
    baseline = getattr(env, "_baseline", None) if env is not None else None
    if not baseline or sid not in baseline.get("entities", {}):
        return 0.0
    memo = getattr(env, "_phys_baseline_memo", None)
    if memo is None or memo[0] is not baseline:
        memo = (baseline, children_map(baseline), {})
        env._phys_baseline_memo = memo
    loads = memo[2]
    key = (sid, slot)
    if key not in loads:
        loads[key] = load_on(baseline, sid, slot, memo[1])
    return loads[key]


# ----- geometry of resting bodies ----------------------------------------------------------------------------------

def resting_size(state, eid):
    """[w, d, h] of an entity as it rests, in its own frame: lying on its side swaps the height with the longer
    footprint side (a bottle on its side is long and low)."""
    w, d, h = dims(state["entities"][eid])
    if state["entities"][eid].get("orientation") == "on_side":
        if w >= d:
            w, h = h, w
        else:
            d, h = h, d
    return [w, d, h]


def surface_height(rec, h=None):
    """Height of a body's top surface above its base (surface_height_m, else its height)."""
    top = finite(rec.get("surface_height_m"))
    if top is not None:
        return top
    return dims(rec)[2] if h is None else h


def hang_height(rec, h=None):
    """Height of a hanger's hook line above its base (hang_height_m, else the top), as geometry.height_m uses it."""
    hook = finite(rec.get("hang_height_m"))
    if hook is not None:
        return hook
    return dims(rec)[2] if h is None else h


def child_base_m(parent, parent_h, child, child_h):
    """Height of a child's base above its parent's base, by relation (None for children the parent does not carry:
    under / behind)."""
    relation = child.get("relation", "on")
    if relation == "on":
        return surface_height(parent, parent_h)
    if relation == "in":
        heights = parent.get("shelf_heights")
        if heights:
            k = _shelf_index(child)
            if 0 <= k < len(heights):
                return num(heights[k])
        floor = finite(parent.get("inner_floor_m"))
        return INNER_FLOOR_M if floor is None else floor
    if relation == "hung":
        return max(0.0, hang_height(parent, parent_h) - child_h)
    if relation in ("under", "behind"):
        return None
    return 0.0          # part / attached: rigid with the body; their mass sits inside it


def _liquid_com(rec, h):
    """(liquid mass kg, its COM height above the body's base) for a container holding liquid: the liquid fills the
    container from the bottom to fill x h, so its centre is at half that."""
    liters = num(rec.get("liquid_l"))
    if liters <= 0:
        return 0.0, 0.0
    capacity = num(rec.get("capacity_l"))
    fill = min(1.0, liters / capacity) if capacity > 0 else 1.0
    return liters * liquid_density(rec), fill * h / 2.0


def com_height(state, eid, cmap=None, _seen=None):
    """Composite centre-of-mass height of eid and everything it carries, above eid's own base (m):
    z = (m_body * com_frac * h + sum m_i * z_i) / m_total, with z_i the carried item's base (shelf height, top,
    inner floor, hook line minus its height) plus its own composite COM. A body lying on its side has com_frac 0.5.
    A location cycle in a malformed state (a in b, b in a) is cut where it closes: a revisited body counts its own
    mass at its own COM (as subtree_mass and descendants count it once)."""
    cmap = _map(state, cmap)
    entities = state["entities"]
    rec = entities[eid]
    _, _, h = resting_size(state, eid)
    frac = 0.5 if rec.get("orientation") == "on_side" else num(phys(state, eid).com_frac, 0.5)
    body = num(rec.get("mass_kg"), 0.0)
    moment, total = body * frac * h, body
    liquid, z_liquid = _liquid_com(rec, h)
    moment += liquid * z_liquid
    total += liquid
    seen = {eid} if _seen is None else _seen | {eid}
    for child in cmap.get(eid, ()):
        if child in seen:
            continue
        crec = entities[child]
        child_h = resting_size(state, child)[2]
        base = child_base_m(rec, h, crec, child_h)
        if base is None:
            continue
        mass = subtree_mass(state, child, cmap)
        moment += mass * (base + com_height(state, child, cmap, seen))
        total += mass
    if total <= 0:
        return frac * h
    return moment / total


# ----- the carried load --------------------------------------------------------------------------------------------

def _load_top(state, eid, base_z, cmap, _seen=None):
    """Highest point (m above the floor) of eid and everything resting on or in it, eid's base at base_z (a location
    cycle in a malformed state is cut where it closes)."""
    entities = state["entities"]
    rec = entities[eid]
    h = resting_size(state, eid)[2]
    top = base_z + h
    seen = {eid} if _seen is None else _seen | {eid}
    for child in cmap.get(eid, ()):
        if child in seen:
            continue
        crec = entities[child]
        child_h = resting_size(state, child)[2]
        offset = child_base_m(rec, h, crec, child_h)
        if offset is None or crec.get("relation") not in ("on", "in", "hung"):
            continue
        top = max(top, _load_top(state, child, base_z + offset, cmap, seen))
    return top


def _load_moment(state, eid, base_z, cmap, _seen=None):
    """(mass, mass x COM height above the floor) of eid's carried subtree with eid's base at base_z. Each body uses
    its own record's com_frac when it declares one, else its middle: the data layer is consulted only for liquid
    densities, so geometric observations stay cheap. A location cycle in a malformed state is cut where it closes
    (each body counted once)."""
    entities = state["entities"]
    rec = entities[eid]
    h = resting_size(state, eid)[2]
    frac = 0.5 if rec.get("orientation") == "on_side" else num(rec.get("com_frac"), 0.5)
    mass = num(rec.get("mass_kg"), 0.0)
    moment = mass * (base_z + frac * h)
    liquid, z_liquid = _liquid_com(rec, h)
    mass += liquid
    moment += liquid * (base_z + z_liquid)
    seen = {eid} if _seen is None else _seen | {eid}
    for child in cmap.get(eid, ()):
        if child in seen:
            continue
        crec = entities[child]
        offset = child_base_m(rec, h, crec, resting_size(state, child)[2])
        if offset is None:
            continue
        m, mz = _load_moment(state, child, base_z + offset, cmap, seen)
        mass += m
        moment += mz
    return mass, moment


def held_envelope(state, cmap=None):
    """The box of everything the robot holds (held items plus what is on or in them), or None when its hands are
    empty: {"items", "two_handed", "bottom_m", "top_m", "front_m", "width_m", "depth_m", "height_m", "mass_kg",
    "com_m", "blocks_view"}. Heights are above the floor; front_m is the distance from the body axis to the load's
    front face; com_m is the height of the carried mass's centre.

    Two-hand loads (other than level-carried trays) ride on the hips with their bottom at CARRY hip_z_m; other loads
    hang centred on the hand at CARRY hand_z_m. A load is carried with its longer horizontal side across the body (its
    smaller side is the depth), its front at front_m + depth / 2; loads in the two hands sit side by side, so their
    widths add. blocks_view follows the camera rule (5.6): a two-hand load at least 0.35 x 0.25 x 0.25 m, or anything
    declaring blocks_view, except a level-carried tray."""
    entities = state["entities"]
    robot = state.get("robot") or {}
    hands = robot.get("hands") or {}
    held = sorted({v for v in hands.values() if v and v in entities})
    if not held:
        return None
    cmap = _map(state, cmap)
    items, loads = [], []
    for eid in held:
        rec = entities[eid]
        both = rec.get("held_hand") == "both" or sum(1 for v in hands.values() if v == eid) > 1
        w, d, h = resting_size(state, eid)
        width, depth = max(w, d), min(w, d)
        tray = bool(rec.get("carry_level"))
        bottom = CARRY["hip_z_m"] if both and not tray else CARRY["hand_z_m"] - h / 2.0
        top = _load_top(state, eid, bottom, cmap)
        weighed, moment = _load_moment(state, eid, bottom, cmap)
        blocks = not tray and (bool(rec.get("blocks_view")) or (both and width >= VIEW_BLOCK_W_M - 1e-9 and
                                                              depth >= VIEW_BLOCK_D_M - 1e-9 and
                                                              top - bottom >= VIEW_BLOCK_H_M - 1e-9))
        loads.append({"both": both, "bottom": bottom, "top": top, "width": width, "depth": depth,
                      "front": CARRY["front_m"] + depth / 2.0, "mass": subtree_mass(state, eid, cmap),
                      "weighed": weighed, "moment": moment, "blocks": blocks})
        items.append(eid)
        items.extend(descendants(state, eid, cmap))
    mass = sum(load["mass"] for load in loads)
    weighed = sum(load["weighed"] for load in loads)
    bottom = min(load["bottom"] for load in loads)
    top = max(load["top"] for load in loads)
    com = sum(load["moment"] for load in loads) / weighed if weighed > 0 else (bottom + top) / 2.0
    return {"items": sorted(set(items)), "two_handed": any(load["both"] for load in loads),
            "bottom_m": round(bottom, 4), "top_m": round(top, 4),
            "front_m": round(max(load["front"] for load in loads), 4),
            "width_m": round(sum(load["width"] for load in loads), 4),
            "depth_m": round(max(load["depth"] for load in loads), 4),
            "height_m": round(top - bottom, 4), "mass_kg": round(mass, 4), "com_m": round(com, 4),
            "blocks_view": any(load["blocks"] for load in loads)}


# ----- positions -----------------------------------------------------------------------------------------------------

def anchor_cells(state, eid, rid):
    """Floor cells of the thing eid ultimately stands on in room rid (its top support's footprint, the holder's cell,
    eid's own cell), or [] when it is elsewhere."""
    room, cells = geometry.anchor(state, eid)
    if room != rid or not cells:
        return []
    return [tuple(c) for c in cells]
