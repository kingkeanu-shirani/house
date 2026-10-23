"""Consequences of the statics layer: falls, impacts, breakage, chips, crushing, shelf and surface collapse, pull-outs,
tears, tipping, knocks, the set-down drop, damage records, harm attribution, the idempotency ledger and the settle laws
(tier-1 physics, docs/design/TIER1_PHYSICS.md 5.2-5.5 and 6.4; fidelity P-approx: no trajectories, no bounces).

Every write goes through the World journal (w.mut / w.sec / w.robot_m), so a refused action rolls back exactly and
strict mode holds. Nothing here refuses or raises ActionError: a consequence happens or it does not.

* **fall(w, eid, ...)** releases the item, picks a landing spot (along an overhang or push: the first free floor cell
  past its support on the line from where it was; otherwise the first free cell around the support, in front of a
  wall-backed one and nearest its middle), applies h_eff = effective_drop_m(h, landing, mass) / (1 + padding / 3 mm),
  breaks, chips or bruises it above its drop tolerance, dents the floor, empties an open container, scatters what was
  on or in it, emits ``phys.fall`` and runs ``phys.fell``. On a hard landing the effective drop is h x hardness; a soft
  one (carpet, a rug, grass, soil, a bed, a sofa, a cushion) first stops the item over its cushion depth (h x 1 mm /
  (1 mm + cushion_m): drop tolerances are drops onto tile, where an item stops within about 1 mm) until the cushion's
  energy (cushion_j) is used up, and the rest lands on the compacted layer (x hardness). Light things are caught (a
  mug dropped 1.5 m onto carpet, an egg dropped on a thick lawn), heavy ones bottom the cushion out.
* **Breakage** follows Phys.break_mode: "shatter" (glass, ceramics: visible ``shattered``; glass and ceramic shards are
  sharp), "crack" (electronics, plastics, wax: broken, which only inspection shows), "chip" (stone, enamel, engineered
  wood: damage.chipped), "bruise" (produce: damage.bruised). Liquid in a broken or emptied container runs down its
  support chain (liquids.deposit_below), so it is conserved.
* **Wrap containment.** A break inside a full wrap (``wrapped`` coverage >= 0.95 of any material, or a plain
  ``wrapped`` flag) or intact factory packaging (``packaged``) is *contained* (``contained(record)``): its pieces stay
  inside, so the item gets the sharp handling hazard but no floor hazard, and ``phys.broke`` hooks and the
  ``phys.break`` event carry ``contained: True``. The contract for every domain: while ``contained(record)`` holds,
  nothing scatters, spreads or spawns the pieces (no shard field, no shards outside the wrap); once the wrap or the
  packaging comes off they are the domain's to scatter. A fallen fully wrapped container keeps what is in it (it is
  jolted, not thrown out). The see-through wraps (``SEE_THROUGH_WRAPS``) contain as well; they only leave the break in
  view (``shattered``).
* **Attribution (6.4).** A consequence is the robot's when the caller says ``by="robot"``, when it happens in the
  action-phase settle, or when the robot arranged the entity: it or a support in its location chain carries the
  ``_robot_moved`` marker (settle writes it on structural changes an action made; opening or closing alone writes
  none). A support failure (collapse, creep, pull-out, tear, crush, stack, overbalanced furniture) is also the robot's
  when the load that made it fail carries the marker. Siblings under the same furniture never count. Only
  robot-attributable consequences raise the global harm flags (``breakages``, ``damage``); ``phys_*`` statistics and
  events count everything.
* **Ledger.** In one settle phase (the action phase, or one substep of a time advance: the scratch restarts when the
  step or the simulated time changes) an entity falls from a given spot, tips, is crushed or starts rolling at most
  once, a broken item stays broken and a collapsed shelf collapsed; every consequence is appended to the phase's
  consequence list ({"kind", "target", "ids", "by", "note", ...}), which settle reads for its notes, its
  64-consequence budget and the undo rule.
* **Laws.** ``LAWS`` lists (law id, settle order, function) for the settle pass; each ``fn(w, eid, env=None,
  cmap=None)`` (the signature liquids.LAWS and thermal.LAWS share) examines one queued entity and returns the
  consequence records it produced; attribution comes from robot_caused (the action phase or _robot_moved).
  ``PROCESS_HANDLERS`` are the timed checks of 5.2 (staged ``creep``, ``handle_creep``) that the physics process queue
  fires as ``fn(w, eid, args)``; the rolling process the slope law starts lives in processes.py.
"""
import math
from collections import namedtuple

from .. import geometry
from ..plugins import run_hooks
from ..predicates import root_room
from . import observe, statics, support

G = statics.G
CUSHION_STOP_M = 0.001        # an item dropped onto tile stops within about 1 mm (the scale of every drop tolerance)
# set-down drop (m) by speed before the support's hardness: an egg (0.03 m) survives a normal set-down on stone and
# breaks on a fast one (TIER1_PHYSICS 5.5)
SETDOWN_DROP_M = {"careful": 0.0, "normal": 0.02, "fast": 0.06}
SCATTER_MIN_M = 0.2           # an open container falling further than this throws out what it holds
SCATTER_MAX_M = 0.6           # ... and those items fall at most this far (they leave it early)
DENT_MIN_KG, DENT_MIN_M, DENT_MIN_SCRATCH = 3.0, 0.3, 0.2    # manipulation's floor-dent rule
TILE_CRACK_J = 40.0           # impact energy that cracks a ceramic floor tile
FLOOR_DAMAGE_J = 100.0        # impact energy that gives a floor mark full severity (a 10 kg box from 1 m)
HANDLE_CREEP_FRACTION = 0.9   # a carried handle at 90-100 % of its rating tears after HANDLE_CREEP_S of carrying
HANDLE_CREEP_S = 120.0
WALL_HOLE_SEVERITY = 0.3      # an anchor pulled out of drywall leaves a hole of this severity
BUMP_CONTACT_S = 0.1          # a pet or person brushing past pushes for about 0.1 s (knock slide distance)
RAY_STEP_M = 0.25             # step of the landing search along a fall direction (half a floor cell)
# material classes whose broken pieces cut (glass and fired ceramics); eggshell and plaster break without sharp edges
SHARP_CLASSES = ("glass", "tempered_glass", "borosilicate", "glass_ceramic", "crystal", "porcelain", "stoneware",
                 "ceramic", "terracotta")
SHARD_GEAR = ("cut_resistant_gloves", "rubber_gloves")       # what makes handling broken glass safe
SEE_THROUGH_WRAPS = ("bubble_wrap", "plastic_wrap", "stretch_wrap", "cling_film", "cellophane")
WRAP_FULL = 0.95              # wrap coverage from which a wrap holds a break's pieces in (and an opaque one hides them)
# fields describing where and how an item rests; a moved item loses them (the last six are manipulation's stack and
# packing bookkeeping, which a fall or collapse ends)
PLACEMENT_KEYS = ("spos", "pos", "shelf", "held_hand", "held_grip", "xy_m", "stack_member", "nested",
                  "stack_offset_m", "pack_seq", "pack_z_m", "pack_on")
# fields that pin a height or a wall mounting: whatever falls, collapses or tears out rests where it lands, so they go
# too (geometry.height_m reads z_m first and mount_height_m for room-located things; support_of skips mounted ones)
MOUNT_KEYS = ("z_m", "mounted", "mount_height_m")


# ----- collaborators in the physics package (lazily imported; tests substitute these) -------------------------------

def _liquids():
    """kernel/phys/liquids.py: container_closed, tip_spill_l and deposit_below (liquid leaving a holder runs down its
    support chain to the floor)."""
    from . import liquids
    return liquids


def _processes():
    """kernel/phys/processes.py: start (the rolling process), queue_add and queued (the timed creep checks)."""
    from . import processes
    return processes


# ----- the step scratch: ledger and consequence list ---------------------------------------------------------------

def _scratch(w):
    """The transient physics scratch (World.phys_scratch, cleared by begin() and rollback()), holding this module's
    ledger and consequence list. Both restart whenever the step number or the simulated time changes, so each settle
    phase has its own: the action phase (the step's start time) and every substep of a long wait (each advances
    time_s before its systems, events and settle run). An item put back upright after a tip can tip again in a later
    substep, and the consequence list never spans more than one phase."""
    scratch = w.phys_scratch
    phase = (w.s.get("steps", 0), w.s.get("time_s", 0.0))
    if scratch.get("ledger_step") != phase or "ledger" not in scratch:
        scratch["ledger_step"] = phase
        scratch["ledger"] = set()
        scratch["consequences"] = []
    return scratch


def once(w, eid, kind):
    """True the first time (eid, kind) happens in this settle phase, False after: the idempotency ledger."""
    ledger = _scratch(w)["ledger"]
    key = (eid, kind)
    if key in ledger:
        return False
    ledger.add(key)
    return True


def _fall_once(w, eid):
    """The fall ledger: eid falls from one resting place at most once per settle phase. A duplicate call (two laws or
    events dropping the same item from the same spot) is skipped; a cascade (shelf 4 to shelf 3, then shelf 3 to the
    floor) is several falls from different places."""
    return once(w, eid, "fall@" + repr(support.support_of(w.s, eid) or w.s["entities"][eid].get("location")))


def consequences(w):
    """This settle phase's consequence records (the action phase's, or the current substep's), in order: {"kind"
    (the event kind), "target", "ids" (what moved or changed), "by", "note", ...}. Notes name ids only and carry no
    numbers (settle turns them into action notes; realistic mode translates the ids)."""
    return _scratch(w)["consequences"]


def _record(w, kind, target, who, note, ids=(), **data):
    entry = {"kind": kind, "target": target, "ids": sorted(set(ids) | {target}), "by": who, "note": note}
    entry.update(data)
    _scratch(w)["consequences"].append(entry)
    return entry


def emit_consequence(w, kind, target, **data):
    """Emit one physics consequence event (kinds phys.*; payloads carry the target, cause, by, room and oracle
    numbers) through phys/observe.emit_consequence, the layer's single route (sounds, and from CAM-RUNTIME and
    TIME-EVAL on camera visual events and notices)."""
    return observe.emit_consequence(w, kind, target, **data)


# ----- attribution --------------------------------------------------------------------------------------------------

def robot_caused(w, eid, cmap=None, load=()):
    """True when a consequence to eid is the robot's (TIER1_DECISIONS, TIER1_PHYSICS 6.4): the action-phase settle is
    running (the robot's action or its motion is the direct cause), or the robot arranged eid: eid itself or a support
    in its location chain (what it rests on or in, up to the top-level furniture) carries the _robot_moved marker that
    settle writes on every structural change an action made (an open / close alone writes none). Siblings under the
    same furniture do not count: a scripted bump of a centrepiece the robot never touched is exogenous although the
    robot set a plate on the same table.

    load: for a support failure (a slot collapsing or creeping, a hanger pulling out, a handle tearing, a body
    crushed, a stack toppling, furniture overbalancing) the ids whose weight made it fail; the consequence is also the
    robot's when any of them, or anything resting on or in them, carries the marker (the failing support carries load
    the robot added)."""
    if _scratch(w).get("phase") == "action":
        return True
    state = w.s
    entities = state["entities"]
    if eid not in entities:
        return False
    if entities[eid].get("_robot_moved"):
        return True
    current, seen = eid, {eid}
    while True:
        loc = entities[current].get("location")
        if loc not in entities or loc in seen:
            break
        if entities[loc].get("_robot_moved"):
            return True
        seen.add(loc)
        current = loc
    if load:
        cmap = support._map(state, cmap)
        for item in load:
            if item not in entities:
                continue
            if entities[item].get("_robot_moved"):
                return True
            if any(entities[sub].get("_robot_moved") for sub in support.descendants(state, item, cmap)):
                return True
    return False


def _by(w, eid, by, cmap=None, load=()):
    """Resolved attribution of a consequence: "robot" or "exogenous" (an explicit by wins; any other label, such as a
    pet or person, is exogenous). load: the ids whose weight made a support fail (robot_caused)."""
    if by == "robot":
        return "robot"
    if by is not None:
        return "exogenous"
    return "robot" if robot_caused(w, eid, cmap, load) else "exogenous"


# ----- damage -------------------------------------------------------------------------------------------------------

def damage_map(value):
    """An entity's damage field in the shared shape {kind: severity 0..1} (domains also write lists of kinds)."""
    if isinstance(value, dict):
        return {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 1.0)
                for k, v in sorted(value.items())}
    if isinstance(value, list):
        out = {}
        for item in value:
            kind = item.get("kind") if isinstance(item, dict) else item
            if isinstance(kind, str):
                out[kind] = 1.0
        return out
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
        return {"general": float(value)}
    return {}


def _damage(w, eid, kind, severity, who):
    """Raise damage[kind] to severity (never lowers it). Returns True when it grew; the first appearance of a kind
    counts in the global damage flag when the robot caused it."""
    rec = w.s["entities"].get(eid)
    if rec is None:
        return False
    current = damage_map(rec.get("damage"))
    old = current.get(kind, 0.0)
    new = round(min(1.0, max(old, support.num(severity))), 4)
    if new <= old:
        return False
    current[kind] = new
    w.mut(eid)["damage"] = current
    if old <= 0.0 and who == "robot":
        w.stat("damage")
    return True


def damage(w, eid, kind, severity, detail="", by=None, cmap=None, event=None, note=None, ids=(), **data):
    """Record damage of a kind (scratch, dent, chipped, cracked, scorch, melted, crushed, collapsed, swollen,
    ring_stain, water_damage, torn, bulged, hole, ...) at a severity level 0..1 on an entity. The severity is the new
    level, not an increment: damage never decreases, and a repeated call at the same level changes nothing. This
    records the field, the global damage flag (robot-caused, first time per kind) and one consequence record.
    Without `event` the caller emits its own event and the record's kind is "phys.damage". With `event` (a phys.*
    kind such as "phys.scratch") the growth is emitted as that event (payload: by, room, damage, severity, detail and
    `data`, oracle numbers allowed) and recorded under that kind, with `note` (ids only) and the other `ids` involved.
    Returns True when the damage grew."""
    who = _by(w, eid, by, cmap)
    if not _damage(w, eid, kind, severity, who):
        return False
    level = w.s["entities"][eid]["damage"][kind]
    note = note or "{} was damaged".format(eid)
    if event is None:
        _record(w, "phys.damage", eid, who, note, ids=ids, damage=kind, severity=level, detail=detail)
        return True
    payload = dict(data, room=root_room(w.s, eid), damage=kind, severity=level, detail=detail)
    emit_consequence(w, event, target=eid, by=who, **payload)
    _record(w, event, eid, who, note, ids=ids, **payload)
    return True


# ----- where things land --------------------------------------------------------------------------------------------

def floor_entity(state, rid):
    """The floor-surface entity of a room (floor_<room> as the generator names it, else the first floor in it)."""
    fid = "floor_" + str(rid)
    entities = state["entities"]
    if fid in entities:
        return fid
    for eid in sorted(entities):
        rec = entities[eid]
        if rec.get("location") == rid and (rec.get("type") == "floor_surface" or rec.get("category") == "floor"):
            return eid
    return None


Landing = namedtuple("Landing", "hardness cushion_m cushion_j")
Landing.__doc__ = """A landing surface: impact hardness (tile 1.0) and, for a soft one, its cushion depth (m) and the
energy (J) it takes before it bottoms out (None, None: a hard landing)."""


def landing(state, rid, cell=None, onto=None):
    """The landing a falling item meets: onto an entity its Phys.hard and cushion (beds, sofas and cushions: 0.15 with
    a 6-15 cm cushion), on a floor its floors.json row at the cell (a rug on a hard floor softens it)."""
    if onto is not None:
        p = support.phys(state, onto)
        return Landing(support.num(p.hard, 1.0), support.finite(getattr(p, "cushion_m", None)),
                       support.finite(getattr(p, "cushion_j", None)))
    row = support.floor_at(state, rid, cell)
    return Landing(support.num(row.get("impact_hardness"), 1.0), support.finite(row.get("cushion_m")),
                   support.finite(row.get("cushion_j")))


def landing_hardness(state, rid, cell=None, onto=None):
    """Impact hardness of a landing (landing(...).hardness)."""
    return landing(state, rid, cell, onto).hardness


def effective_drop_m(drop_m, land, mass_kg):
    """The drop onto tile equivalent to falling drop_m onto land (a Landing, or a bare hardness) with mass_kg falling,
    before the item's own padding. Hard landing: drop x hardness. Soft landing: the cushion stops the first cushion_j
    joules over cushion_m, as a drop of h x 1 mm / (1 mm + cushion_m); the energy beyond it lands on the compacted
    layer, x hardness. Never more than drop x hardness."""
    drop = max(0.0, support.num(drop_m))
    if not isinstance(land, Landing):
        return drop * support.num(land, 1.0)
    depth = support.num(land.cushion_m)
    if depth <= 0:
        return drop * land.hardness
    caught = min(drop, support.num(land.cushion_j) / (max(1e-6, support.num(mass_kg)) * G))
    soft = min(land.hardness, CUSHION_STOP_M / (CUSHION_STOP_M + depth))
    return soft * caught + land.hardness * (drop - caught)


def _unit(direction):
    if direction is None:
        return None
    rad = math.radians(float(direction))
    return math.cos(rad), math.sin(rad)


def _wall_normal(room, cells):
    """Inward normal (dx, dy) of the wall a footprint stands against (in a corner, the wall it runs along for more
    cells), or None when it stands free."""
    cols, rows = geometry.grid_size(room)
    counts = {}
    for x, y in cells:
        for touching, normal in ((y == 0, (0, 1)), (y == rows - 1, (0, -1)), (x == 0, (1, 0)), (x == cols - 1, (-1, 0))):
            if touching:
                counts[normal] = counts.get(normal, 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda normal: counts[normal])


def _ring_search(state, rid, base, ignore=(), direction=None):
    """First free floor cell around the base cells, ring by ring: toward `direction` first, then in front of a
    wall-backed base (beyond its front face), then nearest its middle, then by row and column. The fallback is the
    first base cell."""
    room = state["rooms"][rid]
    base = sorted({tuple(c) for c in base})
    blocked = set(geometry.blocked_cells(state, rid, ignore=ignore, include_agents=False))
    cx = sum(c[0] for c in base) / float(len(base))
    cy = sum(c[1] for c in base) / float(len(base))
    toward = _unit(direction)
    normal = _wall_normal(room, base)
    face = max(c[0] * normal[0] + c[1] * normal[1] for c in base) if normal else None
    own = set(base)

    def key(cell):
        dx, dy = cell[0] - cx, cell[1] - cy
        along = dx * toward[0] + dy * toward[1] if toward else 0.0
        front = 0 if normal is None or cell[0] * normal[0] + cell[1] * normal[1] > face else 1
        return (0 if toward is None or along > 1e-9 else 1, -round(along, 3), front, round(abs(dx) + abs(dy), 3),
                cell[1], cell[0])

    cols, rows = geometry.grid_size(room)
    for radius in range(1, max(cols, rows) + 1):
        ring = [c for c in geometry.near_cells(base, room, radius)
                if c not in own and c not in blocked and min(geometry.chebyshev(c, b) for b in base) == radius]
        if ring:
            return list(min(ring, key=key))
    return list(base[0])


def _ray_cell(state, eid, rid, base, direction):
    """The first floor cell past the footprint `base` on the line from eid's own position along `direction` (when
    furniture stands there, the free cell nearest it); None when eid has no position in rid or the line leaves the
    room first."""
    point = statics.room_point(state, eid)
    if point is None or root_room(state, eid) != rid:
        return None
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    ux, uy = _unit(direction)
    own = set(base)
    for step in range(1, int((cols + rows) * geometry.CELL / RAY_STEP_M) + 2):
        x, y = point[0] + ux * RAY_STEP_M * step, point[1] + uy * RAY_STEP_M * step
        cell = (int(math.floor(x / geometry.CELL)), int(math.floor(y / geometry.CELL)))
        if not geometry.in_grid(room, cell):
            return None
        if cell in own:
            continue
        if cell in geometry.blocked_cells(state, rid, ignore=(eid,), include_agents=False):
            return _ring_search(state, rid, [cell], ignore=(eid,), direction=direction)
        return list(cell)
    return None


def landing_cell(state, eid, rid, prefer=None):
    """Floor cell [col, row] in room rid where eid lands when it falls off what it stands on. With a direction
    `prefer` (room-frame degrees: the overhang or push), the first cell past the support's footprint on the line from
    eid's position that way. Otherwise the first cell not taken by floor-standing furniture in the rings around the
    support's footprint (or the holder's cell): in front of a wall-backed support first, then by row and column. The
    fallback is the support's own anchor cell."""
    base = support.anchor_cells(state, eid, rid)
    if not base:
        robot = state.get("robot") or {}
        if robot.get("room") == rid and robot.get("pos"):
            base = [tuple(robot["pos"])]
        else:
            cols, rows = geometry.grid_size(state["rooms"][rid])
            base = [(cols // 2, rows // 2)]
    if prefer is not None:
        cell = _ray_cell(state, eid, rid, base, prefer)
        if cell is not None:
            return cell
    return _ring_search(state, rid, base, ignore=(eid,), direction=prefer)


def _free_cell(state, eid, rid, cell, direction):
    """The given cell when it is on the grid and free of furniture, else the nearest free cell around it; without a
    cell, landing_cell."""
    if cell is None:
        return landing_cell(state, eid, rid, direction)
    cell = (int(cell[0]), int(cell[1]))
    room = state["rooms"][rid]
    if geometry.in_grid(room, cell) and cell not in geometry.blocked_cells(state, rid, ignore=(eid,), include_agents=False):
        return list(cell)
    return _ring_search(state, rid, [cell], ignore=(eid,), direction=direction)


def _agents_at(state, rid, cells):
    """Sorted ids of the people, pets and robot standing on any of the cells in room rid."""
    cells = {tuple(c) for c in cells if c is not None}
    out = []
    for group in ("people", "pets"):
        for aid, agent in state.get(group, {}).items():
            if agent.get("location") == rid and agent.get("pos") and tuple(agent["pos"]) in cells:
                out.append(aid)
    robot = state.get("robot") or {}
    if robot.get("room") == rid and robot.get("pos") and tuple(robot["pos"]) in cells:
        out.append("robot")
    return sorted(out)


def _relocate(w, eid, location, relation, cmap=None, pos=None, spos=None, shelf=None):
    """Move eid (journaled): out of any hand, its placement fields and any pinned height or wall mounting cleared
    (it rests where it lands), to a new location and relation."""
    state = w.s
    rec = state["entities"][eid]
    old = rec.get("location")
    if old == "robot" and any(v == eid for v in (state["robot"].get("hands") or {}).values()):
        hands = w.robot_m()["hands"]
        for hand in sorted(hands):
            if hands[hand] == eid:
                hands[hand] = None
    m = w.mut(eid)
    for key in PLACEMENT_KEYS + MOUNT_KEYS:
        m.pop(key, None)
    m["location"], m["relation"] = location, relation
    if pos is not None:
        m["pos"] = [int(pos[0]), int(pos[1])]
    if spos is not None:
        m["spos"] = [round(float(spos[0]), 4), round(float(spos[1]), 4)]
    if shelf is not None:
        m["shelf"] = int(shelf)
    support.move_child(cmap, eid, old, location)


def _children(state, eid, cmap):
    """eid's children (on, in, hung, attached, ...), sorted."""
    if cmap is not None:
        return list(cmap.get(eid, ()))
    return sorted(c for c, r in state["entities"].items() if r.get("location") == eid)


def _release_liquid(w, eid, liters, who):
    """liters leave eid (already taken off its liquid_l) and run down its support chain to the floor."""
    if liters > 0:
        rec = w.s["entities"][eid]
        _liquids().deposit_below(w, eid, liters, rec.get("liquid") or "water", source=eid, by=who)


# ----- breaking and impacts -----------------------------------------------------------------------------------------

def break_mode(p):
    """How an item fails at impact: Phys.break_mode ("shatter", "crack", "chip", "bruise"), or "crack" when it has a
    finite drop tolerance but no declared mode; None when it never breaks."""
    if p.break_mode:
        return p.break_mode
    return "crack" if support.finite(p.drop_tol_m) is not None else None


def _break_shows(rec):
    """A brittle break shows at a glance unless an opaque full wrap or factory packaging hides it (manipulation's
    break_shows rule)."""
    wrapped = rec.get("wrapped")
    if isinstance(wrapped, dict):
        if support.num(wrapped.get("coverage")) >= WRAP_FULL and wrapped.get("material") not in SEE_THROUGH_WRAPS:
            return False
    elif wrapped:
        return False
    return not rec.get("packaged")


def contained(rec):
    """True when a break of this record stays inside what covers it: a full wrap of any material (wrapped coverage
    >= WRAP_FULL; a plain truthy wrapped flag counts as a full wrap) or its intact factory packaging (packaged). The
    wrap-containment contract (module docstring): nothing may scatter, spread or spawn the pieces of a contained
    break."""
    wrapped = rec.get("wrapped")
    if isinstance(wrapped, dict):
        if support.num(wrapped.get("coverage")) >= WRAP_FULL:
            return True
    elif wrapped:
        return True
    return bool(rec.get("packaged"))


def _break(w, eid, cause, who):
    state = w.s
    rec = state["entities"][eid]
    if rec.get("broken"):
        return False
    p = support.phys(state, eid)
    mode = break_mode(p) or "crack"
    m = w.mut(eid)
    m["broken"] = True
    m["condition"] = "broken"
    current = damage_map(m.get("damage"))
    current["broken"] = 1.0
    m["damage"] = current
    inside = contained(m)
    if mode == "shatter":
        if _break_shows(m):
            m["shattered"] = True
        if p.cls in SHARP_CLASSES:
            hazards = list(m.get("hazards") or [])
            if "sharp" not in hazards:
                m["hazards"] = hazards + ["sharp"]
            gear = list(m.get("handled_with") or [])
            m["handled_with"] = gear + [g for g in SHARD_GEAR if g not in gear]
            if m.get("location") in state["rooms"] and not inside:
                m["floor_hazard"] = "sharp"     # a contained break's pieces stay in the wrap: no floor hazard
    liters = support.num(m.get("liquid_l"))
    if liters > 0:
        m["liquid_l"] = 0.0
        _release_liquid(w, eid, liters, who)
    w.stat("phys_breaks")
    if who == "robot":
        w.stat("breakages")
        w.stat("damage")
    info = {"cause": cause, "by": who, "room": root_room(state, eid), "mode": mode, "liters": round(liters, 4)}
    if inside:
        info["contained"] = True        # wrap containment: hooks must not scatter its pieces (only set when True)
    emit_consequence(w, "phys.break", target=eid, **info)
    run_hooks("phys.broke", w, eid, dict(info))
    _record(w, "phys.break", eid, who, "{} broke".format(eid), cause=cause, mode=mode)
    return True


def break_entity(w, eid, cause, by=None, cmap=None):
    """Break an item (idempotent: False when it is already broken or gone): broken, condition "broken", damage.broken
    1.0, its liquid run down to the floor; a shattering item also shows ``shattered`` (unless an opaque wrap hides
    it), and glass or ceramic becomes sharp (hazard, shard gear to handle it, a sharp floor hazard on a floor unless
    the break is contained in a full wrap or its packaging). Runs ``phys.broke`` (info: cause, by, room, mode, liters
    and, for a contained break, contained True)."""
    rec = w.s["entities"].get(eid)
    if rec is None or rec.get("broken"):
        return False
    return _break(w, eid, cause, _by(w, eid, by, cmap))


def uncontained(w, eid, by=None, cmap=None):
    """Containment ended (phase-1 red team): the wrap or the packaging of a broken item came off, so the pieces it held
    are out. A shattered sharp break now shows (``shattered``) and, on a floor, gets the sharp floor hazard its break
    did not give it; ``phys.uncontained`` hooks (info: by, room, mode) let a domain scatter what it scatters for an
    open break (the safety domain's shard field), and a ``phys.uncontained`` consequence is recorded. The domain that
    removes the wrap calls it after clearing ``wrapped`` / ``packaged`` (manipulation's unwrap). Returns True when it
    released anything; False when the item is gone, not broken, still contained or its pieces are already out."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or not rec.get("broken") or contained(rec) or rec.get("_pieces_out"):
        return False
    p = support.phys(state, eid)
    mode = break_mode(p) or "crack"
    if mode != "shatter":
        return False                # a crack, a chip or a bruise has no loose pieces to release
    m = w.mut(eid)
    m["_pieces_out"] = True
    m["shattered"] = True
    if p.cls in SHARP_CLASSES and m.get("location") in state["rooms"]:
        m["floor_hazard"] = "sharp"
    who = _by(w, eid, by, cmap)
    info = {"by": who, "room": root_room(state, eid), "mode": mode}
    emit_consequence(w, "phys.uncontained", target=eid, **info)
    run_hooks("phys.uncontained", w, eid, dict(info))
    _record(w, "phys.uncontained", eid, who, "{}'s pieces came out of its wrap".format(eid))
    return True


def _impact(w, eid, h_eff, cause, who):
    """Apply an impact of effective height h_eff (m): "broke", "chipped", "bruised" or None (survived)."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or h_eff <= 0:
        return None
    p = support.phys(state, eid)
    tolerance = support.finite(p.drop_tol_m)
    mode = break_mode(p)
    if tolerance is None or mode is None or h_eff <= tolerance + 1e-9:
        return None
    if mode in ("chip", "bruise"):
        kind = "chipped" if mode == "chip" else "bruised"
        severity = min(1.0, max(0.1, h_eff / tolerance - 1.0)) if tolerance > 0 else 1.0
        if not _damage(w, eid, kind, severity, who):
            return None
        if mode == "chip":          # a bruise has no sound or event of its own: the fall's outcome carries it
            emit_consequence(w, "phys.chip", target=eid, cause=cause, by=who, room=root_room(state, eid),
                             h_eff_m=round(h_eff, 4))
        _record(w, "phys.chip" if mode == "chip" else "phys.damage", eid, who, "{} was {}".format(eid, kind),
                cause=cause, damage=kind)
        return kind
    return "broke" if _break(w, eid, cause, who) else None


def impact(w, eid, h_eff, cause, by=None, cmap=None):
    """An impact equivalent to a drop of h_eff metres onto a hardness-1 surface (hardness and padding already
    applied). Above the item's drop tolerance it breaks (chips or bruises by its break mode). True when it broke."""
    if w.s["entities"].get(eid) is None:
        return False
    return _impact(w, eid, h_eff, cause, _by(w, eid, by, cmap)) == "broke"


# ----- falling --------------------------------------------------------------------------------------------------------

def _floor_damage(w, eid, rid, cell, drop, who, cmap):
    """Heavy drops mark hard floors: 3 kg or more from 0.3 m or more, with no rug, on a floor whose scratch rating
    is 0.2 or more dents it; a ceramic tile floor also cracks above 40 J."""
    state = w.s
    mass = support.subtree_mass(state, eid, cmap)
    if mass < DENT_MIN_KG or drop < DENT_MIN_M or support.floor_at(state, rid, cell).get("rug"):
        return
    floor = floor_entity(state, rid)
    bare = support.floor_at(state, rid, None)
    if floor is None or support.num(bare.get("scratch")) < DENT_MIN_SCRATCH:
        return
    energy = mass * G * drop
    severity = min(1.0, max(0.1, energy / FLOOR_DAMAGE_J))
    kinds = ["dent"] + (["cracked"] if bare.get("material") == "tile" and energy > TILE_CRACK_J else [])
    for kind in kinds:
        if _damage(w, floor, kind, severity, who):
            emit_consequence(w, "phys.dent", target=floor, cause=eid, by=who, room=rid, cell=list(cell), damage=kind,
                             energy_j=round(energy, 2))
            _record(w, "phys.dent", floor, who, "the floor was marked where {} landed".format(eid), ids=[eid],
                    damage=kind)


def _spill_and_scatter(w, eid, drop, land, rid, cell, landed_on, who, cmap):
    """An open container that fell loses its liquid (lying on its side a narrow neck keeps some; landing any other
    way it empties) and, from higher than SCATTER_MIN_M, throws out what is on or in it; a closed one jolts its
    contents."""
    state = w.s
    entities = state["entities"]
    rec = entities[eid]
    liters = support.num(rec.get("liquid_l"))
    children = [c for c in _children(state, eid, cmap) if entities[c].get("relation", "on") in ("on", "in")]
    closed = True
    if liters > 0 or any(entities[c].get("relation") == "in" for c in children):
        closed = bool(_liquids().container_closed(rec))
    # a full wrap or intact packaging keeps loose contents in (wrap containment); liquid soaks through a wrap
    keeps = closed or contained(rec)
    if liters > 0 and not closed and not rec.get("broken"):
        orientation = "on_side" if rec.get("orientation") == "on_side" else "upside_down"
        lost = min(liters, max(0.0, support.num(_liquids().tip_spill_l(rec, orientation))))
        if lost > 0:
            w.mut(eid)["liquid_l"] = round(liters - lost, 4)
            _release_liquid(w, eid, lost, who)
    if drop <= SCATTER_MIN_M:
        return
    for child in children:
        if child not in entities or entities[child].get("location") != eid:
            continue
        crec = entities[child]
        if crec.get("relation", "on") == "on" or not keeps:
            height = min(drop, SCATTER_MAX_M)
            if landed_on:
                fall(w, child, height_m=height, cause="scattered", onto=landed_on, by=who, cmap=cmap)
            else:
                fall(w, child, height_m=height, cause="scattered", at=(rid, cell), by=who, cmap=cmap)
        else:
            h_eff = effective_drop_m(drop, land, support.subtree_mass(state, child, cmap))
            _impact(w, child, h_eff / statics.padding_factor(crec), "jolted", who)


def _land(w, eid, drop, land, cause, who, rid, cell, landed_on, before, cmap):
    """Steps 3-7 of a fall, after eid was moved to where it lands (land: the Landing)."""
    state = w.s
    rec = state["entities"][eid]
    m = w.mut(eid)
    m["fell"] = True
    width, depth, height = support.dims(rec)
    if drop > 0 and rec.get("orientation") in (None, "upright") and height > min(width, depth) + 1e-9:
        m["orientation"] = "on_side"      # an upright item taller than its base ends up lying
    h_eff = effective_drop_m(drop, land, support.subtree_mass(state, eid, cmap)) / statics.padding_factor(rec)
    outcome = _impact(w, eid, h_eff, cause, who) if drop > 0 else None
    if landed_on is None and cell is not None and drop > 0:
        _floor_damage(w, eid, rid, cell, drop, who, cmap)
    _spill_and_scatter(w, eid, drop, land, rid, cell, landed_on, who, cmap)
    info = {"height_m": round(drop, 4), "h_eff_m": round(h_eff, 4), "room": rid,
            "cell": list(cell) if cell is not None else None, "landed_on": landed_on,
            "struck": _agents_at(state, rid, [cell]) if cell is not None else [], "cause": cause, "by": who,
            "outcome": outcome}
    emit_consequence(w, "phys.fall", target=eid, **info)
    w.stat("phys_falls")
    w.log_move(eid, before, state["entities"][eid]["location"], by="gravity")
    run_hooks("phys.fell", w, eid, dict(info))
    note = "{} fell".format(eid) + (" and broke" if outcome == "broke" else "")
    _record(w, "phys.fall", eid, who, note, ids=[landed_on] if landed_on else (), cause=cause, outcome=outcome)
    return dict(info, fell=True)


def fall(w, eid, height_m=None, cause="fall", at=None, onto=None, by=None, direction=None, cmap=None):
    """eid falls: out of any hand, to the floor, or onto `onto` (a bed, a sofa, a lower surface).

    height_m: the drop; default its current height above the floor (or above onto's top). at: (room, cell) to land
    at or next to (a robot drop passes its own cell). direction: room-frame degrees to land toward (default: the side
    it overhangs, when it sticks out past the edge of its surface). Returns {"fell", "height_m", "h_eff_m", "room",
    "cell", "landed_on", "struck", "cause", "by", "outcome"}; {"fell": False} when eid is gone, already fell from this
    spot this step, or rests on the floor (a floor item falls only when dropped from an explicit height_m, in place)."""
    state = w.s
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None:
        return {"fell": False}
    found = support.support_of(state, eid)
    on_floor = found is not None and found[1][0] == "floor" and at is None and onto is None
    if (on_floor and height_m is None) or not _fall_once(w, eid):
        return {"fell": False}          # already down, or already fell from this spot this step
    who = _by(w, eid, by, cmap)
    before = rec.get("location")
    start = geometry.height_m(state, eid)
    if direction is None and before in entities and statics.overhang_frac(state, eid) > 0.0:
        direction = statics.overhang_dir_deg(state, eid)
    if onto is not None and onto in entities:
        rid = root_room(state, onto) or state["robot"].get("room")
        top = geometry.height_m(state, onto) + support.surface_height(entities[onto])
        drop = max(0.0, start - top) if height_m is None else max(0.0, support.num(height_m))
        land = landing(state, rid, onto=onto)
        spot = geometry.surface_spot(state, onto, eid)
        _relocate(w, eid, onto, "on", cmap, spos=spot)
        return _land(w, eid, drop, land, cause, who, rid, None, onto, before, cmap)
    rid, cell = (at[0], at[1]) if at is not None else (root_room(state, eid), found[1][1] if on_floor else None)
    if rid not in state["rooms"]:
        rid = state["robot"].get("room")
    drop = start if height_m is None else max(0.0, support.num(height_m))
    cell = _free_cell(state, eid, rid, cell, direction)
    land = landing(state, rid, cell)
    _relocate(w, eid, rid, "on", cmap, pos=cell)
    return _land(w, eid, drop, land, cause, who, rid, cell, None, before, cmap)


def _drop_beside(w, eid, base, drop, cause, who, cmap, direction=None):
    """eid comes off what it rested on (a toppled stack member, something on a tipping item) and lands lying on its
    side: on base's own support when that is a fixed surface with room beside it, inside the same shelf or container,
    next to it on the floor, and otherwise down to the floor from where it is."""
    state = w.s
    entities = state["entities"]
    if not _fall_once(w, eid):
        return None
    found = support.support_of(state, base)
    before = entities[eid].get("location")
    m = w.mut(eid)
    m["orientation"] = "on_side"
    m["tipped_over"] = True
    rid = root_room(state, base) or state["robot"].get("room")
    if found and found[1] == ("surface",) and not entities[found[0]].get("portable"):
        spot = geometry.surface_spot(state, found[0], eid)
        if spot is not None:
            land = landing(state, rid, onto=found[0])
            _relocate(w, eid, found[0], "on", cmap, spos=spot)
            return _land(w, eid, drop, land, cause, who, rid, None, found[0], before, cmap)
    elif found and found[1][0] in ("shelf", "in"):
        land = landing(state, rid, onto=found[0])
        _relocate(w, eid, found[0], "in", cmap, shelf=found[1][1] if found[1][0] == "shelf" else None)
        return _land(w, eid, drop, land, cause, who, rid, None, found[0], before, cmap)
    elif found and found[1][0] == "floor" and found[1][1] is not None:
        cell = _ring_search(state, rid, [found[1][1]], ignore=(eid,), direction=direction)
        _relocate(w, eid, rid, "on", cmap, pos=cell)
        return _land(w, eid, drop, landing(state, rid, cell), cause, who, rid, cell, None, before, cmap)
    drop = geometry.height_m(state, eid)
    cell = _free_cell(state, eid, rid, None, direction)
    land = landing(state, rid, cell)
    _relocate(w, eid, rid, "on", cmap, pos=cell)
    return _land(w, eid, drop, land, cause, who, rid, cell, None, before, cmap)


# ----- crushing, collapse, pull-out, tearing --------------------------------------------------------------------------

def crush(w, eid, load_kg, by=None, cmap=None):
    """eid gives way under load_kg on top when that exceeds its crush limit (statics.crush_limit_kg; once per step):
    a brittle item breaks; anything else gets damage.crushed of severity min(1, load / limit - 1). Returns the ids
    now bearing the load: a crushed container passes it to what is inside."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or rec.get("broken"):
        return []
    limit = statics.crush_limit_kg(state, eid, cmap)
    if limit is None or load_kg <= limit + 1e-9 or not once(w, eid, "crush"):
        return []
    who = _by(w, eid, by, cmap, load=_children(state, eid, cmap))
    if support.phys(state, eid).brittle:
        _break(w, eid, "crushed", who)
        return []
    severity = min(1.0, load_kg / limit - 1.0) if limit > 0 else 1.0
    if _damage(w, eid, "crushed", severity, who):
        emit_consequence(w, "phys.crush", target=eid, by=who, room=root_room(state, eid), damage="crushed",
                         load_kg=round(load_kg, 4), limit_kg=round(limit, 4))
        _record(w, "phys.crush", eid, who, "{} was crushed".format(eid))
    return support.slot_children(state, eid, ("in",), cmap)


def _wall_of(state, sid):
    """The wall entity a fixture is fastened to (its location when that is a wall), else None."""
    loc = state["entities"][sid].get("location")
    rec = state["entities"].get(loc)
    if rec is not None and (rec.get("category") in ("wall", "wall_surface") or rec.get("wall_material")):
        return loc
    return None


def _mounted(state, sid):
    """A fixture fastened to a wall: mounted in a room, or attached to a wall entity."""
    rec = state["entities"][sid]
    return (bool(rec.get("mounted")) and rec.get("location") in state["rooms"]) or _wall_of(state, sid) is not None


def _unfasten(w, sid):
    """A fixture torn off the wall is no longer anchored (its mounting fields go when it lands: _relocate)."""
    if w.s["entities"][sid].get("anchored"):
        w.mut(sid).pop("anchored", None)


def _pull_out(w, sid, who, cmap, items, cause):
    """A fixture tears out of the wall: its items fall, then it falls itself and lies on the floor, unmounted and
    unanchored; the wall keeps a hole (on a fixture with no wall entity behind it, the fixture records
    damage.pulled_out)."""
    wall = _wall_of(w.s, sid)
    for item in items:
        fall(w, item, cause=cause, by=who, cmap=cmap)
    if wall is not None:
        _damage(w, wall, "hole", WALL_HOLE_SEVERITY, who)
    else:
        _damage(w, sid, "pulled_out", 1.0, who)
    _unfasten(w, sid)
    fall(w, sid, cause=cause, by=who, cmap=cmap)


def collapse(w, sid, slot, by=None, cmap=None):
    """One rated slot of a support gives way (idempotent per shelf and support). Returns the ids that moved.

    * Shelf k: collapsed_shelves gains k and the furniture damage.collapsed 1.0; shelf k's items drop onto the next
      intact shelf below (an impact from the gap) or, from the bottom shelf, to the floor.
    * Surface: a brittle top (glass) breaks; a wall-mounted shelf tears out of the wall with its items; any other
      furniture gets damage.collapsed. The items fall to the floor.
    * In (a fixed container's bottom): damage.collapsed and its contents fall.
    * Hung: the hung items fall; an adhesive hook comes off with them, an anchor in drywall pulls out (a wall hole),
      any other hanger bends (damage.collapsed)."""
    state = w.s
    entities = state["entities"]
    rec = entities.get(sid)
    if rec is None:
        return []
    cmap = support._map(state, cmap)
    items = support.slot_children(state, sid, slot, cmap)
    who = _by(w, sid, by, cmap, load=items)
    room = root_room(state, sid)
    kind = slot[0]
    event, note, failure = "phys.collapse", "{} gave way".format(sid), "collapsed"
    if kind == "shelf":
        k = slot[1]
        done = [int(v) for v in rec.get("collapsed_shelves") or []]
        if k in done:
            return []
        done = sorted(set(done) | {k})
        w.mut(sid)["collapsed_shelves"] = done
        _damage(w, sid, "collapsed", 1.0, who)
        heights = rec.get("shelf_heights") or []
        lower = [j for j in range(min(k, len(heights))) if j not in done]
        note = "a shelf of {} gave way".format(sid)
        if lower:
            j = lower[-1]
            gap = max(0.0, support.num(heights[k]) - support.num(heights[j])) if k < len(heights) else 0.0
            land = landing(state, room, onto=sid)
            for item in items:
                if not _fall_once(w, item):
                    continue
                before = entities[item].get("location")
                _relocate(w, item, sid, "in", cmap, shelf=j)
                _land(w, item, gap, land, "shelf_collapsed", who, room, None, sid, before, cmap)
        else:
            for item in items:
                fall(w, item, cause="shelf_collapsed", by=who, cmap=cmap)
    elif kind == "surface":
        if rec.get("broken") or damage_map(rec.get("damage")).get("collapsed", 0.0) >= 1.0:
            return []
        if break_mode(support.phys(state, sid)) == "shatter":
            failure = "broke"
            _break(w, sid, "overloaded", who)
            for item in items:
                fall(w, item, cause="support_broke", by=who, cmap=cmap)
        elif _mounted(state, sid):
            event, note, failure = "phys.pullout", "{} tore out of the wall".format(sid), "pulled_out"
            _pull_out(w, sid, who, cmap, items, "pulled_out")
        else:
            _damage(w, sid, "collapsed", 1.0, who)
            for item in items:
                fall(w, item, cause="support_collapsed", by=who, cmap=cmap)
    elif kind == "hung":
        adhesive = bool(rec.get("adhesive")) or rec.get("hardware_kind") == "adhesive_hook" or \
            str(rec.get("anchor") or "").startswith("adhesive")
        if adhesive or rec.get("anchor") == "drywall_anchor":
            event, note, failure = "phys.pullout", "{} came off the wall".format(sid), "pulled_out"
            for item in items:
                fall(w, item, cause="hanger_failed", by=who, cmap=cmap)
            if adhesive:
                _unfasten(w, sid)
                fall(w, sid, cause="adhesive_failed", by=who, cmap=cmap)
            else:
                _pull_out(w, sid, who, cmap, [], "anchor_pulled_out")
        else:
            _damage(w, sid, "collapsed", 1.0, who)
            for item in items:
                fall(w, item, cause="hanger_failed", by=who, cmap=cmap)
    elif kind == "in":
        if damage_map(rec.get("damage")).get("collapsed", 0.0) >= 1.0:
            return []
        _damage(w, sid, "collapsed", 1.0, who)
        for item in items:
            fall(w, item, cause="support_collapsed", by=who, cmap=cmap)
    else:
        return []
    slot_out = [slot[0], slot[1]] if kind == "shelf" else [slot[0]]
    info = {"slot": slot_out, "items": list(items), "failure": failure, "by": who, "room": room}
    emit_consequence(w, event, target=sid, **info)
    w.stat("phys_collapses")
    run_hooks("phys.collapsed", w, sid, dict(info))
    _record(w, event, sid, who, note, ids=items, slot=slot_out, failure=failure)
    return list(items)


def tear(w, eid, by=None, cmap=None):
    """A carried bag or box gives way at its handle or bottom (once): damage.torn (and the torn flag domains read);
    it stays in the hand while its contents fall from hand height at the holder's cell and its liquid runs out.
    Returns the ids that fell."""
    state = w.s
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None or rec.get("torn"):
        return []
    who = _by(w, eid, by, cmap, load=_children(state, eid, cmap))
    holder = rec.get("location")
    if holder == "robot":
        rid, cell, height = state["robot"].get("room"), state["robot"].get("pos"), support.CARRY["hand_z_m"]
    elif holder in state.get("people", {}) or holder in state.get("pets", {}):
        agent = state.get("people", {}).get(holder) or state["pets"][holder]
        rid, cell, height = agent.get("location"), agent.get("pos"), geometry.height_m(state, eid)
    else:
        rid, cell, height = root_room(state, eid), None, None
    w.mut(eid)["torn"] = True
    _damage(w, eid, "torn", 1.0, who)
    items = [c for c in _children(state, eid, cmap) if entities[c].get("relation", "on") in ("in", "on")]
    for item in items:
        if cell is not None:
            fall(w, item, height_m=height, cause="container_tore", at=(rid, cell), by=who, cmap=cmap)
        else:
            fall(w, item, height_m=height, cause="container_tore", by=who, cmap=cmap)
    liters = support.num(rec.get("liquid_l"))
    if liters > 0:
        w.mut(eid)["liquid_l"] = 0.0
        _release_liquid(w, eid, liters, who)
    emit_consequence(w, "phys.tear", target=eid, by=who, room=rid, items=items)
    _record(w, "phys.tear", eid, who, "{} tore".format(eid), ids=items)
    return items


# ----- tipping and knocks --------------------------------------------------------------------------------------------

def _front_deg(state, eid, rid):
    """Room-frame direction (degrees) a floor-standing body tips toward when nothing pushes it: away from the wall
    behind it (its front), else +row."""
    cells = support.anchor_cells(state, eid, rid)
    normal = _wall_normal(state["rooms"][rid], cells) if cells else None
    if normal is None:
        return 90.0
    return math.degrees(math.atan2(normal[1], normal[0])) % 360.0


def _fall_cells(state, eid, rid, direction_deg):
    """Floor cells a toppling body sweeps: from its footprint, as far as its height reaches in the tip direction."""
    cells = support.anchor_cells(state, eid, rid)
    if not cells:
        return []
    room = state["rooms"][rid]
    reach = max(1, int(math.ceil(support.dims(state["entities"][eid])[2] / geometry.CELL - 1e-9)))
    ux, uy = _unit(direction_deg)
    step = (int(round(ux)), int(round(uy)))
    if step == (0, 0):
        return []
    swept = set()
    for cell in cells:
        for n in range(1, reach + 1):
            nxt = (cell[0] + step[0] * n, cell[1] + step[1] * n)
            if geometry.in_grid(room, nxt):
                swept.add(nxt)
    return sorted(swept - set(cells))


def _lay_toward(w, eid, frame, direction):
    """Re-anchor a tipped item's spot on its surface: its lying body extends from where it stood toward the direction
    it tipped (centred across it); frame is its upright statics.surface_frame."""
    state = w.s
    _, _, x0, y0, fw, fd = frame
    lw, ld = statics.footprint_wd(state, eid)
    x, y = x0 + (fw - lw) / 2.0, y0 + (fd - ld) / 2.0
    if direction is not None:
        rad = math.radians(float(direction) - statics.world_yaw(state, state["entities"][eid]["location"]))
        ux, uy = math.cos(rad), math.sin(rad)
        if abs(ux) >= abs(uy):
            x = x0 if ux >= 0 else x0 + fw - lw
        else:
            y = y0 if uy >= 0 else y0 + fd - ld
    w.mut(eid)["spos"] = [round(x, 4), round(y, 4)]


def tip_over(w, eid, cause, direction=None, by=None, cmap=None):
    """eid tips onto its side (once per step): orientation "on_side", tipped_over, an impact equivalent to its
    centre of mass dropping to its new resting height, an open container's liquid runs out, and whatever rested on
    it comes off. A floor-standing body (furniture) throws its contents and top items to the floor in the tip
    direction (default: away from the wall behind it) and reports the people, pets or robot in the cells it sweeps
    (``struck``; injuries are the people, pets and safety domains'). An item on a surface that now sticks out past
    the edge falls. Returns {"tipped", "fell", "broke", "struck", "direction"}."""
    state = w.s
    entities = state["entities"]
    rec = entities.get(eid)
    out = {"tipped": False, "fell": False, "broke": False, "struck": [], "direction": None}
    if rec is None or rec.get("orientation") == "on_side" or not once(w, eid, "tip"):
        return out
    cmap = support._map(state, cmap)
    who = _by(w, eid, by, cmap, load=_children(state, eid, cmap) if cause == "overbalanced" else ())
    rid = root_room(state, eid) or state["robot"].get("room")
    found = support.support_of(state, eid)
    on_floor = found is not None and found[1][0] == "floor"
    if direction is None:
        direction = _front_deg(state, eid, rid) if on_floor else statics.overhang_dir_deg(state, eid)
    w_, d_, _ = support.resting_size(state, eid)
    z = support.com_height(state, eid, cmap)
    struck = _agents_at(state, rid, _fall_cells(state, eid, rid, direction)) if on_floor else []
    frame = None if on_floor else statics.surface_frame(state, eid)
    m = w.mut(eid)
    m["orientation"] = "on_side"
    m["tipped_over"] = True
    if frame is not None:
        _lay_toward(w, eid, frame, direction)
    w.stat("phys_tips")
    # the body lands: its centre of mass drops from z to half its new (lying) thickness
    if on_floor:
        land = landing(state, rid, found[1][1])
    elif found is not None and found[0] in entities:
        land = landing(state, rid, onto=found[0])
    else:
        land = Landing(1.0, None, None)
    drop = max(0.0, z - support.resting_size(state, eid)[2] / 2.0)
    h_eff = effective_drop_m(drop, land, support.subtree_mass(state, eid, cmap))
    outcome = _impact(w, eid, h_eff / statics.padding_factor(rec), cause, who)
    out["broke"] = outcome == "broke"
    liters = support.num(rec.get("liquid_l"))
    items = [c for c in _children(state, eid, cmap) if entities[c].get("relation", "on") in ("on", "in")]
    closed = True
    if liters > 0 or any(entities[c].get("relation") == "in" for c in items):
        closed = bool(_liquids().container_closed(rec))
    if liters > 0 and not closed and not rec.get("broken"):
        lost = min(liters, max(0.0, support.num(_liquids().tip_spill_l(rec, "on_side"))))
        if lost > 0:
            w.mut(eid)["liquid_l"] = round(liters - lost, 4)
            _release_liquid(w, eid, lost, who)
    for item in items:
        if item in entities and (entities[item].get("relation", "on") == "on" or not closed):
            if on_floor:
                fall(w, item, cause="tipped_off", by=who, direction=direction, cmap=cmap)
            else:
                _drop_beside(w, item, eid, max(0.0, min(w_, d_)), "tipped_off", who, cmap, direction)
    info = {"cause": cause, "by": who, "room": rid, "direction": None if direction is None else round(direction, 2),
            "struck": struck}
    emit_consequence(w, "phys.tip", target=eid, **info)
    run_hooks("phys.tipped", w, eid, dict(info))
    _record(w, "phys.tip", eid, who, "{} tipped over".format(eid), ids=items, cause=cause)
    out.update(tipped=True, struck=struck, direction=info["direction"])
    margin = None if on_floor else statics.edge_margin_m(state, eid)
    if margin is not None and margin < -1e-9:
        result = fall(w, eid, cause=cause, by=who, direction=direction, cmap=cmap)
        out["fell"] = bool(result.get("fell"))
        out["broke"] = out["broke"] or result.get("outcome") == "broke"
    return out


def slide_distance_m(accel_g, mu, contact_s=BUMP_CONTACT_S):
    """How far (m) an item slides under a push of accel_g lasting contact_s against friction mu: it accelerates at
    (a - mu) g during the push, then decelerates at mu g."""
    net = (accel_g - mu) * G
    if net <= 0:
        return 0.0
    speed = net * contact_s
    coast = speed * speed / (2.0 * mu * G) if mu > 0 else 0.0
    return 0.5 * net * contact_s * contact_s + coast


def knock(w, eid, accel_g, direction=None, cause="bump", by=None, cmap=None):
    """A horizontal jolt of accel_g (g) on eid in a room-frame direction (degrees; None or "toward_edge": toward the
    nearest edge of its surface). Below both thresholds nothing happens; otherwise the smaller wins: it tips over, or
    it slides (its spot shifts by the bump's slide distance) and falls when its centre passes the edge. Only
    free-standing things move (statics.free_standing). Returns {"tipped", "slid_m", "fell", "broke"}."""
    state = w.s
    result = {"tipped": False, "slid_m": 0.0, "fell": False, "broke": False}
    rec = state["entities"].get(eid)
    if rec is None or not statics.free_standing(state, eid):
        return result
    if direction is None or direction == "toward_edge":
        direction = statics.overhang_dir_deg(state, eid)
    lying = rec.get("orientation") == "on_side"
    tip = statics.INF if lying else statics.tip_accel_g(state, eid, axis=direction, cmap=cmap)
    slide = statics.slide_accel_g(state, eid)
    if accel_g <= min(tip, slide) + 1e-9:
        return result
    if tip <= slide:
        out = tip_over(w, eid, cause, direction=direction, by=by, cmap=cmap)
        result.update(tipped=out["tipped"], fell=out["fell"], broke=out["broke"])
        return result
    return _slide(w, eid, accel_g, slide, direction, cause, by, cmap, result)


def _slide(w, eid, accel_g, mu, direction, cause, by, cmap, result):
    """The sliding branch of a knock: the item's spot on its surface shifts by the bump's slide distance toward
    direction; it falls when its centre passes the edge."""
    state = w.s
    rec = state["entities"][eid]
    frame = statics.surface_frame(state, eid)
    if frame is None:
        return result
    distance = slide_distance_m(accel_g, mu)
    rad = math.radians((direction if direction is not None else 0.0) - statics.world_yaw(state, rec["location"]))
    who = _by(w, eid, by, cmap)
    w.mut(eid)["spos"] = [round(frame[2] + distance * math.cos(rad), 4), round(frame[3] + distance * math.sin(rad), 4)]
    result["slid_m"] = round(distance, 4)
    emit_consequence(w, "phys.slide", target=eid, cause=cause, by=who, room=root_room(state, eid),
                     distance_m=round(distance, 4))
    _record(w, "phys.slide", eid, who, "{} slid".format(eid), cause=cause)
    margin = statics.edge_margin_m(state, eid)
    if margin is not None and margin < -1e-9:
        fallen = fall(w, eid, cause=cause, by=who, direction=direction, cmap=cmap)
        result.update(fell=bool(fallen.get("fell")), broke=fallen.get("outcome") == "broke")
    return result


def slide(w, eid, accel_g, direction=None, cause="nudge", by=None, cmap=None):
    """eid slides on its surface under a brief horizontal push of accel_g (g), whatever its tipping threshold (a push
    low on its body that cannot tip it: events.nudge decides tip versus slide from the push height). Nothing happens
    below its friction or for things that are not free-standing. Returns {"tipped", "slid_m", "fell", "broke"}."""
    state = w.s
    result = {"tipped": False, "slid_m": 0.0, "fell": False, "broke": False}
    rec = state["entities"].get(eid)
    if rec is None or not statics.free_standing(state, eid):
        return result
    if direction is None or direction == "toward_edge":
        direction = statics.overhang_dir_deg(state, eid)
    mu = statics.slide_accel_g(state, eid)
    if accel_g <= mu + 1e-9:
        return result
    return _slide(w, eid, accel_g, mu, direction, cause, by, cmap, result)


def effective_speed(state, requested=None):
    """The speed the robot actually moves at (TIER1_INTEGRATION 1.7): body.effective_speed of the requested mode
    (None: robot.speed_mode), so quiet mode turns fast into normal and limp mode or weak legs force careful. The
    rule is idempotent, so passing an already effective mode is safe; an unknown mode is returned unchanged."""
    from .. import body
    try:
        return body.effective_speed(state, requested)[0]
    except ValueError:
        return requested


def setdown(w, eid, speed=None, by="robot", cmap=None):
    """The set-down law (core place): putting eid down at a speed is a drop of SETDOWN_DROP_M[speed] onto what it now
    rests on (x that support's hardness, / padding). speed is the requested mode (None: the robot's speed mode); the
    drop follows the effective speed (body.effective_speed: quiet mode sets down a fast place at normal speed).
    Returns {"h_eff_m", "outcome"} with outcome "broke", "chipped", "bruised" or None."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None:
        return {"h_eff_m": 0.0, "outcome": None}
    speed = effective_speed(state, speed)
    drop = SETDOWN_DROP_M.get(speed, SETDOWN_DROP_M["normal"])
    loc = rec.get("location")
    if drop <= 0 or (loc not in state["entities"] and loc not in state["rooms"]):
        return {"h_eff_m": 0.0, "outcome": None}
    if loc in state["rooms"]:
        land = landing(state, loc, rec.get("pos"))
    else:
        land = landing(state, root_room(state, eid), onto=loc)
    h_eff = effective_drop_m(drop, land, support.subtree_mass(state, eid, cmap)) / statics.padding_factor(rec)
    outcome = _impact(w, eid, h_eff, "setdown", _by(w, eid, by, cmap))
    return {"h_eff_m": round(h_eff, 4), "outcome": outcome}


# ----- the settle laws --------------------------------------------------------------------------------------------------

def _since(w, start):
    return consequences(w)[start:]


def _cue(w, eid, field, marker, on):
    """Write or clear a glance cue (sagging, wobbly) that physics owns through an underscore marker; a domain's own
    value of the field is never touched."""
    rec = w.s["entities"].get(eid)
    if rec is None:
        return
    if on and not rec.get(field):
        m = w.mut(eid)
        m[field] = True
        m[marker] = True
    elif not on and rec.get(marker):
        m = w.mut(eid)
        m.pop(field, None)
        m.pop(marker, None)


def sag_cue(w, sid, env=None, cmap=None):
    """Refresh the sagging cue of a support from its rated slots (any shelf, surface or fixed container loaded above
    0.9 x its rating) without applying a collapse. The overload law writes the cue while it is active; staged supports
    (the stage setup op: "about to give way") get it whatever the law's state, at setup and whenever settle looks at
    them, so an honest glance shows what the creep check will decide (TIER1_DECISIONS: cues are computed lazily for
    staged entities). Returns the cue."""
    state = w.s
    if sid not in state["entities"]:
        return False
    cmap = support._map(state, cmap)
    sagging = False
    for slot in support.slots(state, sid, cmap):
        if slot[0] not in ("shelf", "surface", "in"):
            continue
        status = statics.overload(state, sid, slot, env, cmap)
        if status is not None and status["sagging"]:
            sagging = True
            break
    _cue(w, sid, "sagging", "_phys_sag", sagging)
    return sagging


def orphan_law(w, eid, env=None, cmap=None):
    """orphan (10): something whose support vanished this step (destroyed while it was on or in it) falls to the
    floor of the room the support was in, from the height it rested at."""
    state = w.s
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None:
        return []
    loc = rec.get("location")
    if loc in entities or loc in state["rooms"] or loc == "robot" or loc in state.get("people", {}) or \
            loc in state.get("pets", {}):
        return []
    start = len(consequences(w))
    gone = getattr(w, "_journal", {}).get(("entities", loc))
    view = state
    if isinstance(gone, dict):
        view = dict(state)
        view["entities"] = dict(entities)
        view["entities"][loc] = gone
    rid = root_room(view, eid)
    if rid not in state["rooms"]:
        rid = state["robot"].get("room")
    height = geometry.height_m(view, eid) if view is not state else 0.0
    cell = landing_cell(view, eid, rid)
    fall(w, eid, height_m=height, cause="support_removed", at=(rid, cell), cmap=cmap)
    return _since(w, start)


def overload_law(w, eid, env=None, cmap=None):
    """overload (30): shelves, surfaces and fixed containers of eid carrying more than their limit give way
    (statics.overload); a collapsed shelf's load lands on the shelf below, which is checked again (cascade). Writes or
    clears the sagging cue (above 0.9 x a rating)."""
    state = w.s
    if eid not in state["entities"]:
        return []
    cmap = support._map(state, cmap)
    start = len(consequences(w))
    while True:
        failed, sagging = False, False
        for slot in support.slots(state, eid, cmap):
            if slot[0] not in ("shelf", "surface", "in"):
                continue
            status = statics.overload(state, eid, slot, env, cmap)
            if status is None:
                continue
            if status["fails"] and collapse(w, eid, slot, cmap=cmap):
                failed = True
                break
            sagging = sagging or status["sagging"]
        if not failed:
            break
    _cue(w, eid, "sagging", "_phys_sag", sagging)
    return _since(w, start)


def hang_law(w, eid, env=None, cmap=None):
    """hang (30): hung things heavier than their hanger's limit pull it out (statics.overload on the hung slot)."""
    state = w.s
    if eid not in state["entities"]:
        return []
    start = len(consequences(w))
    status = statics.overload(state, eid, ("hung",), env, cmap)
    if status is not None and status["fails"]:
        collapse(w, eid, ("hung",), cmap=cmap)
    return _since(w, start)


def _passed_load(state, eid, cmap):
    """Load (kg) a crushed container passes to each of its contents: what bore down on it, shared equally."""
    entities = state["entities"]
    found = support.support_of(state, eid)
    if found is None or found[1] != ("in",):
        return 0.0
    box = found[0]
    if damage_map(entities[box].get("damage")).get("crushed", 0.0) <= 0.0:
        return 0.0
    contents = support.slot_children(state, box, ("in",), cmap)
    return (support.load_above(state, box, cmap) + _passed_load(state, box, cmap)) / max(1, len(contents))


def crush_law(w, eid, env=None, cmap=None):
    """crush (40): eid is crushed when the load above it exceeds its crush limit; a crushed container passes the load
    to its contents, which are checked at once."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or rec.get("broken"):
        return []
    cmap = support._map(state, cmap)
    start = len(consequences(w))
    pending, seen = [eid], set()
    while pending:
        current = pending.pop(0)
        if current in seen or current not in state["entities"]:
            continue
        seen.add(current)
        limit = statics.crush_limit_kg(state, current, cmap)
        if limit is None:
            continue
        load = support.load_above(state, current, cmap) + _passed_load(state, current, cmap)
        if load > limit + 1e-9:
            pending.extend(crush(w, current, load, cmap=cmap))
    return _since(w, start)


def handle_law(w, eid, env=None, cmap=None):
    """handle (45): a carried container whose contents exceed its handle rating tears; at 90-100 % of it the
    handle_creep check is queued once, HANDLE_CREEP_S later (it tears then if still carried that heavy)."""
    state = w.s
    if eid not in state["entities"]:
        return []
    start = len(consequences(w))
    status = statics.handle_status(state, eid, cmap)
    if status is not None and status["contents_kg"] > status["limit"] + 1e-9:
        tear(w, eid, cmap=cmap)
    elif status is not None and status["ratio"] >= HANDLE_CREEP_FRACTION:
        processes = _processes()
        if not processes.queued(state, "handle_creep", eid):
            processes.queue_add(w, support.num(state.get("time_s")) + HANDLE_CREEP_S, "handle_creep", eid)
    return _since(w, start)


def edge_law(w, eid, env=None, cmap=None):
    """edge (50): a free-standing item whose centre of mass is past the edge of its surface falls off on that side (a
    secured one is clamped or fastened there)."""
    state = w.s
    if eid not in state["entities"] or not statics.free_standing(state, eid):
        return []
    margin = statics.edge_margin_m(state, eid)
    if margin is None or margin >= -1e-9:
        return []
    start = len(consequences(w))
    fall(w, eid, cause="overhang", direction=statics.overhang_dir_deg(state, eid), cmap=cmap)
    return _since(w, start)


def slope_law(w, eid, env=None, cmap=None):
    """slope (50): on a tilted support or a sloped floor a rolling item starts rolling (the rolling process moves it
    and drops it at the edge), a tall one tips, a slippery one slides off a tilted support (down its tilt_dir_deg
    when set; sliding on a sloped floor is not modelled)."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None:
        return []
    verdict = statics.slope_verdict(state, eid, cmap)
    if verdict is None:
        return []
    start = len(consequences(w))
    loc = rec.get("location")
    downhill = support.finite((state["entities"].get(loc) or {}).get("tilt_dir_deg"))
    if verdict == "roll":
        if once(w, eid, "roll") and _processes().start(w, "rolling", eid):
            w.stat("phys_rolls")        # counted at the start; the process emits phys.roll where the roll ends
            _record(w, "phys.roll", eid, _by(w, eid, None, cmap), "{} started rolling".format(eid))
    elif verdict == "tip":
        tip_over(w, eid, "slope", direction=downhill, cmap=cmap)
    elif loc in state["entities"]:
        fall(w, eid, cause="slid_off", direction=downhill, cmap=cmap)
    return _since(w, start)


def stack_law(w, eid, env=None, cmap=None):
    """stack (55): the stack eid belongs to: unstable members topple (from the top down, onto the stack's own support
    beside it, or to the floor), and every member of a slender stack shows the wobbly cue."""
    state = w.s
    if eid not in state["entities"]:
        return []
    cmap = support._map(state, cmap)
    start = len(consequences(w))
    base = statics.stack_base(state, eid)
    members = statics.stack_chain(state, base, cmap)
    verdict = statics.stack_verdict(state, base, cmap)
    wobbly = {m for m, kind in verdict if kind == "wobbly"}
    topple = [m for m, kind in verdict if kind == "topple"]
    for member in members:
        _cue(w, member, "wobbly", "_phys_wobble", member in wobbly)
    if topple:
        who = _by(w, base, None, cmap, load=members)
        heights, z = {}, 0.0
        for member in members:
            heights[member] = z
            z += support.resting_size(state, member)[2]
        for member in reversed(topple):
            if member in state["entities"] and _drop_beside(w, member, base, heights[member], "stack_toppled", who,
                                                            cmap) is not None:
                w.stat("phys_tips")
                emit_consequence(w, "phys.tip", target=member, cause="stack_toppled", by=who,
                                 room=root_room(state, member), direction=None, struck=[])
                _record(w, "phys.tip", member, who, "{} toppled off the stack".format(member), ids=[base],
                        cause="stack_toppled")
    return _since(w, start)


def furniture_tip_law(w, eid, env=None, cmap=None):
    """furniture_tip (60): floor-standing furniture whose reported front loads (open drawers: phys.tip_loads) outweigh
    its righting moment tips forward. Without such loads a resting body always rights itself: nothing to check."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or rec.get("portable") or rec.get("tipped_over"):
        return []
    found = support.support_of(state, eid)
    if found is None or found[1][0] != "floor" or not statics.tip_loads(state, eid):
        return []
    start = len(consequences(w))
    if statics.tip_margin_nm(state, eid, cmap=cmap) < 0:
        tip_over(w, eid, "overbalanced", cmap=cmap)
    return _since(w, start)


# law id, settle order (equal to laws.json), function; registered with the lawbook by the integration package
LAWS = (("orphan", 10, orphan_law), ("hang", 30, hang_law), ("overload", 30, overload_law), ("crush", 40, crush_law),
        ("handle", 45, handle_law), ("edge", 50, edge_law), ("slope", 50, slope_law), ("stack", 55, stack_law),
        ("furniture_tip", 60, furniture_tip_law))


# ----- timed checks fired by the physics process queue (processes.run) --------------------------------------------------

def schedule_creep(w, sid, slot, due_s):
    """Queue the staged-creep check of one slot at simulated time due_s (the stage setup op: "the shelf is about to
    give way")."""
    _processes().queue_add(w, due_s, "creep", sid, slot=list(slot))


def creep_fire(w, eid, args):
    """creep: at its time a staged slot still loaded above its true rating (no scene allowance) collapses; one that
    was unloaded in time holds. args: {"slot": ["shelf", k] | ["surface"] | ...}."""
    state = w.s
    if eid not in state["entities"]:
        return []
    raw = list((args or {}).get("slot") or ["surface"])
    slot = ("shelf", int(raw[1])) if raw[0] == "shelf" and len(raw) > 1 else (raw[0],)
    rated = support.rating(state, eid, slot)
    start = len(consequences(w))
    if rated is not None and support.load_on(state, eid, slot) > rated + 1e-9:
        collapse(w, eid, slot)
    if eid in state["entities"] and state["entities"][eid].get("_phys_staged"):
        sag_cue(w, eid)                    # what is left on it (or what the robot unloaded in time) decides the cue
    return _since(w, start)


def handle_creep_fire(w, eid, args):
    """handle_creep: a container still carried at 90 % or more of its handle rating 120 s after the handle law
    queued the check tears now."""
    state = w.s
    if eid not in state["entities"]:
        return []
    start = len(consequences(w))
    status = statics.handle_status(state, eid)
    if status is not None and status["ratio"] >= HANDLE_CREEP_FRACTION:
        tear(w, eid)
    return _since(w, start)


# queue kind -> fire(w, eid, args); processes.run dispatches these alongside its own kinds
PROCESS_HANDLERS = {"creep": creep_fire, "handle_creep": handle_creep_fire}
