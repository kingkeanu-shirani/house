"""Floor hazard map: what lies on each cell of a room, the friction it offers, and the
derived keep-out margins the robot needs (fragile/topple-prone items, sleeping people and
pets, hot surfaces, occupied ladders, reversing vehicles, glass panels, overhangs, rugs).

Derived margins are written into room["floor_hazards"] (tagged nav_derived) for the robot's
current room before every planned motion, so core move/approach/walk_to can route around
them with `avoid=[kind, ...]`. The `path_blocked` hook recomputes them for whatever room is
being planned, so a skill's privileged route check sees the same margins the motion will
(no stale rings from before a pet fell asleep or a pan heated up).

The approach target's own keep-out ring (a sleeping pet's, a hot device's, a fragile vase's,
an occupied ladder's, a dock's clearance) is where an approach is meant to stop: an approach
plans without it (avoid=['sleeping'] still keeps clear of every OTHER sleeper), and arriving
there carefully is no breach. Social zones (TV sightlines, conversations, video-call camera
frames, personal space) are NOT written there; they are evaluated as consequences and planned
around with go_to(avoid=[...]) or walk_to waypoints.
"""
import math

from ...kernel import body, geometry
from ...kernel.plugins import hook
from . import common as C

DERIVED_KINDS = ("fragile", "hot", "overhang", "occupied_ladder", "vehicle", "rug_slip", "rug_curl", "low_obstacle",
                 "glass", "sleeping", "tail", "moving_door", "dock_zone")
SOCIAL_KINDS = ("sightline", "conversation", "camera_frame", "personal_space")
SKIP_CATEGORIES = ("floor", "wall", "door", "gate", "window", "light", "switch", "hatch", "hooks", "elevator_panel", "handrail")
# margins drawn AROUND a source (the cells next to it); an approach to that source plans without them
RING_KINDS = ("fragile", "hot", "occupied_ladder", "sleeping", "dock_zone")
PLAN_EXCLUDE = "_nav_plan_exclude"     # robot field: sources whose rings the motion in progress plans without


def ring(cells, room, radius=1):
    base = {C.cell(c) for c in cells}
    return sorted(geometry.near_cells(base, room, radius) - base)


def floor_entities(state, rid):
    """Entities lying directly on a room's floor (not furniture contents)."""
    out = []
    for eid, rec in state["entities"].items():
        if rec.get("location") != rid or not rec.get("pos") or "connects" in rec:
            continue
        if rec.get("category") in SKIP_CATEGORIES or rec.get("mounted") or rec.get("on_stairs"):
            continue
        out.append(eid)
    out.sort()
    return out


def approach_exclusions(state, target):
    """Sources whose keep-out ring IS the reach ring of an approach to `target`: the target itself and what it stands
    on or sits in (approaching a pot on a hot stove means standing beside the stove)."""
    if not isinstance(target, str) or not target:
        return ()
    if target in state.get("people", {}) or target in state.get("pets", {}):
        return (target,)
    out, eid, seen = [], target, set()
    ents = state["entities"]
    while eid in ents and eid not in seen:
        seen.add(eid)
        out.append(eid)
        eid = ents[eid].get("location")
    if eid in state.get("people", {}) or eid in state.get("pets", {}):
        out.append(eid)      # held or worn by someone: the approach stops beside them
    return tuple(sorted(out))


@hook("path_blocked")
def fresh_margins(state, rid, avoid_kinds, ignore_sources=()):
    """Planning with `avoid` sees the derived margins as they are NOW in the room being planned (the written
    floor_hazards are refreshed only for the robot's room, before each motion and every 10 min), minus the rings of the
    approach target in progress. Margins already written to room.floor_hazards reach the planner through the kernel's
    hazard_cells WITH their source, so find_path's ignore_sources (v2.3: an approach, or a skill's safe_avoid check,
    ignores its target's own ring) applies to them; only margins that appeared since the last refresh are returned
    here as plain blocked cells (never rings of `ignore_sources`). Ground-layer floor hazards (puddles laid flat) are the
    kernel's since v2.2. avoid=["lying"] (the kind realistic observations show for anyone lying down, asleep or not)
    keeps clear of every lying person's or pet's cell and ring."""
    if not avoid_kinds or rid not in state.get("rooms", {}):
        return ()
    exclude = tuple(sorted(set((state.get("robot") or {}).get(PLAN_EXCLUDE) or ()) | set(ignore_sources or ())))
    out = []
    if LYING_KIND in avoid_kinds:
        out.extend(sorted(lying_cells(state, rid, ignore=exclude), key=lambda c: (c[1], c[0])))
    kinds = set(avoid_kinds) & set(DERIVED_KINDS)
    if not kinds:
        return out
    hidden = set((state.get("robot") or {}).get(PLAN_HIDDEN) or ())
    written = {(int(e["cell"][0]), int(e["cell"][1]), e.get("kind"), e.get("source"))
               for e in state["rooms"][rid].get("floor_hazards") or () if e.get("nav_derived")}
    return out + [tuple(e["cell"]) for e in derived(state, rid, exclude, kinds=kinds)
                  if (e["cell"][0], e["cell"][1], e["kind"], e["source"]) not in written and e["source"] not in hidden]


# derived margins a careful robot routes around (kernel SAFE_AVOID kinds): generation and setup placement must see them
PLACEMENT_KINDS = ("fragile", "hot", "sleeping", "tail", "dock_zone")


@hook("derived_hazards")
def placement_margins(state, rid):
    """Kernel v2.3 placement hazards: the keep-out margins this domain only derives at run time (a vacuum dock's
    keep-clear zone, rings around fragile/topple-prone floor items, hot surfaces and sleepers, pets' tails), so
    generation/setup placement (geometry.placement_hazards) keeps loose things out of them and never lets them cut the
    only hazard-free route. Wall-hung decor is left out: it takes no floor and only matters for wide loads."""
    if rid not in state.get("rooms", {}):
        return ()
    return [(tuple(e["cell"]), e["kind"], e["source"]) for e in derived(state, rid, kinds=PLACEMENT_KINDS) if not e.get("wall")]


def _vehicle_zone(rec, room):
    veh = rec.get("vehicle") or {}
    if veh.get("zone_cells"):
        return [C.cell(c) for c in veh["zone_cells"] if geometry.in_grid(room, C.cell(c))]
    cells = geometry.footprint(rec)
    if not cells:
        return []
    xs, ys = [c[0] for c in cells], [c[1] for c in cells]
    direction = veh.get("reverse_dir", "south")
    depth = int(veh.get("zone_cells_deep", 6))
    zone = []
    if direction in ("south", "north"):
        rows = range(max(ys) + 1, max(ys) + 1 + depth) if direction == "south" else range(min(ys) - depth, min(ys))
        for y in rows:
            for x in range(min(xs) - 1, max(xs) + 2):
                zone.append((x, y))
    else:
        cols = range(max(xs) + 1, max(xs) + 1 + depth) if direction == "east" else range(min(xs) - depth, min(xs))
        for x in cols:
            for y in range(min(ys) - 1, max(ys) + 2):
                zone.append((x, y))
    return [c for c in zone if geometry.in_grid(room, c)]


def brush_fragile(rec):
    """A floor item that a passing body can knock over or break (a floor vase, a floor lamp, a LEGO build): fragile or
    topple-prone, not already broken, and free-standing. Rooted plants (trees, shrubs, hedges), anchored fixtures and
    other immovable things are not toppled by a brush, however fragile their twigs are."""
    if not (rec.get("topple_prone") or rec.get("fragile")) or rec.get("broken") or rec.get("toppled") \
            or rec.get("tipped_over"):
        return False
    if rec.get("rooted") or rec.get("anchored") or rec.get("mounted"):
        return False
    return rec.get("portable") is not False or bool(rec.get("topple_prone"))


def tail_cell(agent):
    if agent.get("tail_cell"):
        return C.cell(agent["tail_cell"])
    pos = agent.get("pos")
    # any pet lying down (asleep, lying or resting) has its tail out on the floor; realistic perception shows all three
    # postures as "resting", so the tail must not exist for only some of them
    if not pos or agent.get("posture") not in ("lying", "sleeping", "resting"):
        return None
    if agent.get("species") not in ("cat", "dog"):
        return None
    facing = C.num(agent.get("facing_deg"), 180.0)
    rad = math.radians(facing + 180.0)
    return (int(pos[0] + round(math.cos(rad))), int(pos[1] + round(math.sin(rad))))


def derived(state, rid, exclude=(), kinds=None):
    """Derived hazard entries for one room (sorted, deterministic). `exclude`: sources whose rings are left out (the
    approach target's); `kinds`: only compute these kinds (planning)."""
    room = state["rooms"][rid]
    out = []

    def add(cells, kind, source, **extra):
        if (kinds is not None and kind not in kinds) or (kind in RING_KINDS and source in exclude):
            return
        for c in cells:
            if geometry.in_grid(room, c):
                entry = {"cell": [int(c[0]), int(c[1])], "kind": kind, "source": source, "nav_derived": True}
                entry.update(extra)
                out.append(entry)

    hard = C.floor_material(state, rid) in C.HARD_FLOORS
    # one pass over the entities: loose floor items, and wall-hung fragile decor (pictures, mirrors, sconces) that is
    # brushed from the cell in front of it
    floor_ids, wall_ids = [], []
    for eid, rec in state["entities"].items():
        if rec.get("location") != rid or not rec.get("pos"):
            continue
        if rec.get("mounted"):
            if (rec.get("fragile") or rec.get("wall_decor")) and 0.5 <= C.num(rec.get("mount_height_m"), 1.5) <= 2.0 \
                    and rec.get("category") not in ("window", "light", "switch") and not rec.get("broken"):
                wall_ids.append(eid)
            continue
        if "connects" in rec or rec.get("category") in SKIP_CATEGORIES or rec.get("on_stairs"):
            continue
        floor_ids.append(eid)
    floor_ids.sort()
    wall_ids.sort()
    for eid in floor_ids:
        rec = state["entities"][eid]
        cells = geometry.footprint(rec)
        if not cells:
            continue
        if brush_fragile(rec):
            add(ring(cells, room, max(1, int(rec.get("nav_margin", 1)))), "fragile", eid)
        if C.num(rec.get("temperature_c"), 21.0) >= 60.0 and not rec.get("contained_heat"):
            add(ring(cells, room), "hot", eid)
        overhang = rec.get("overhang")
        if isinstance(overhang, dict) and overhang.get("cells"):
            add([C.cell(c) for c in overhang["cells"]], "overhang", eid, height_m=C.num(overhang.get("height_m"), 1.0))
        if rec.get("occupied_by") and rec.get("category") in ("ladder", "step_stool"):
            add(ring(cells, room), "occupied_ladder", eid)
        veh = rec.get("vehicle")
        if isinstance(veh, dict) and veh.get("state") in ("reversing", "moving", "pulling_out"):
            add(_vehicle_zone(rec, room), "vehicle", eid)
        if isinstance(veh, dict) and veh.get("door_opening_cells"):
            add([C.cell(c) for c in veh["door_opening_cells"]], "vehicle", eid, door=True)
        if rec.get("category") == "rug" or C.is_type(rec, "rug"):
            if hard and not rec.get("non_slip_pad", False) and not rec.get("anchored"):
                add(cells, "rug_slip", eid)
            if rec.get("curled_corner") or rec.get("curled"):
                corner = C.cell(rec["curled_corner"]) if isinstance(rec.get("curled_corner"), list) else cells[0]
                add([corner], "rug_curl", eid)
        if rec.get("low_obstacle"):
            zone = [C.cell(c) for c in rec.get("zone_cells") or []] or cells
            add(zone, "low_obstacle", eid, height_m=C.num(rec.get("obstacle_height_m"), 0.25))
        if rec.get("glass_panel") or rec.get("transparent_obstacle"):
            add(cells, "glass", eid)
        if rec.get("rv_dock"):
            add(dock_zone(state, eid), "dock_zone", eid)
    for eid in wall_ids:
        add([C.cell(state["entities"][eid]["pos"])], "fragile", eid, wall=True)
    for group, aid, agent in C.agents_in_room(state, rid):
        pos = C.cell(agent["pos"])
        if C.asleep(agent):
            add(ring([pos], room), "sleeping", aid)
        tail = tail_cell(agent) if group == "pets" else None
        if tail and geometry.in_grid(room, tail):
            add([tail], "tail", aid)
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        door = state["entities"].get(conn.get("door") or "", {})
        if rid in conn.get("cells", {}) and door.get("moving"):
            c = C.cell(conn["cells"][rid])
            add([c] + ring([c], room), "moving_door", conn.get("door"))
    unique, seen = [], set()
    for entry in out:
        key = (entry["cell"][0], entry["cell"][1], entry["kind"], entry["source"])
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    unique.sort(key=lambda e: (e["kind"], e["source"] or "", e["cell"][1], e["cell"][0]))
    return unique


def dock_zone(state, dock_id):
    """Clearance zone of a robot-vacuum dock: 1.5 m in front, 0.5 m to the sides."""
    rec = state["entities"][dock_id]
    rid = rec.get("location")
    if rid not in state["rooms"] or not rec.get("pos"):
        return []
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    x, y = C.cell(rec["pos"])
    front = int(math.ceil(C.num(rec.get("clearance_front_m"), 1.5) / C.CELL - 1e-9))
    side = int(math.ceil(C.num(rec.get("clearance_side_m"), 0.5) / C.CELL - 1e-9))
    facing = rec.get("facing")
    if facing is None:
        # docks stand against a wall and face into the room
        facing = "south" if y == 0 else "north" if y == rows - 1 else "east" if x == 0 else "west" if x == cols - 1 else "south"
    cells = []
    for d in range(1, front + 1):
        for s in range(-side, side + 1):
            if facing == "south":
                cells.append((x + s, y + d))
            elif facing == "north":
                cells.append((x + s, y - d))
            elif facing == "east":
                cells.append((x + d, y + s))
            else:
                cells.append((x - d, y + s))
    for s in range(1, side + 1):
        if facing in ("south", "north"):
            cells += [(x - s, y), (x + s, y)]
        else:
            cells += [(x, y - s), (x, y + s)]
    return sorted(c for c in cells if geometry.in_grid(room, c))


def refresh(w, rid, exclude=()):
    """Rewrite the derived entries of room.floor_hazards when they changed (without the rings of `exclude`)."""
    if rid not in w.s["rooms"]:
        return
    room = w.s["rooms"][rid]
    current = room.get("floor_hazards") or []
    keep = [h for h in current if not h.get("nav_derived")]
    fresh = derived(w.s, rid, exclude)
    old = [h for h in current if h.get("nav_derived")]
    if old == fresh:
        return
    w.room_m(rid)["floor_hazards"] = keep + fresh


def cell_map(state, rid):
    """cell -> list of hazard dicts {kind, source, ...} (room entries, wet cells, floor items)."""
    room = state["rooms"][rid]
    out = {}
    for c in room.get("wet_cells", []) or []:
        out.setdefault(C.cell(c), []).append({"kind": "wet", "source": rid, "film": True})
    for entry in room.get("floor_hazards", []) or []:
        try:
            c = C.cell(entry["cell"])
        except (KeyError, TypeError, IndexError):
            continue
        out.setdefault(c, []).append(dict(entry))
    for eid in floor_entities(state, rid):
        rec = state["entities"][eid]
        kind = rec.get("floor_hazard")
        if not kind or rec.get("blocks_path"):
            continue
        for c in geometry.footprint(rec):
            out.setdefault(c, []).append({"kind": kind, "source": eid, "entity": True,
                                          "puddle": rec.get("category") == "spill" or C.num(rec.get("spill_l")) > 0})
    return out


# floor hazards a camera cannot see (realistic perception): black ice is clear, a loose board looks like the others
INVISIBLE_KINDS = ("black_ice", "loose_board")
FILM_SEEN_CELLS = 2      # a damp film (no puddle) shows its sheen only close by, at a low angle
SEEN_CELLS = 10          # 5 m: farther floor detail is not resolved


PLAN_HIDDEN = "_nav_plan_hidden"   # robot field: sources whose margins the motion in progress cannot plan around

# this domain's transient robot fields (kernel v3.0 field registry; underscore fields are never observed)
body.register_robot_field(PLAN_EXCLUDE, None, "navigation_robot", "hidden",
                          "Sources whose keep-out rings the motion in progress plans without (an approach's target)")
body.register_robot_field(PLAN_HIDDEN, None, "navigation_robot", "hidden",
                          "Mirrors and reflective panels the motion in progress takes for openings (realistic)")


def looks_like_opening(rec):
    """A mirror or reflective glass panel: realistic perception shows it as an open doorway until it is inspected."""
    return rec.get("category") == "mirror" or bool(rec.get("reflective"))


def unperceived_glass(env, state, rid):
    """Realistic perception: sources of this room's derived "glass" margins that the robot still takes for an opening
    (mirrors and reflective panels it has not inspected). Empty in oracle mode."""
    realism = getattr(env, "realism", None)
    if realism is None or rid not in state.get("rooms", {}):
        return set()
    from ...kernel.realism import INSPECT
    out = set()
    for h in state["rooms"][rid].get("floor_hazards") or ():
        src = h.get("source")
        rec = state["entities"].get(src) if isinstance(src, str) else None
        if h.get("nav_derived") and h.get("kind") == "glass" and rec is not None and looks_like_opening(rec)                 and realism.knowledge.entity_level(src, rec) < INSPECT:
            out.add(src)
    return out


LYING_POSTURES = {"people": ("sleeping", "lying"), "pets": ("sleeping", "lying", "resting")}
LYING_KIND = "lying"       # the kernel's realistic name for a lying agent's hazard (asleep or awake look the same)


def lying_agents(state, rid, ignore=()):
    """[(id, cell)] of people and pets lying down in a room (asleep or awake: from a distance they look the same)."""
    out = []
    for group, aid, agent in C.agents_in_room(state, rid):
        if aid not in ignore and agent.get("posture") in LYING_POSTURES[group]:
            out.append((aid, C.cell(agent["pos"])))
    return out


def lying_cells(state, rid, ignore=()):
    """Each lying agent's cell and keep-out ring (what a careful robot gives anyone lying on the floor or in bed)."""
    room = state["rooms"][rid]
    cells = set()
    for _, pos in lying_agents(state, rid, ignore):
        cells |= set(geometry.near_cells([pos], room, 1))
    return cells


def scrub_observation(env, state, obs):
    """Realistic perception (navigation_robot observer): drop from obs.grid.hazards and obs.room.floor_hazards what a
    camera cannot see that this domain put there - black ice and loose boards, and the glass margins of mirrors and
    reflective panels the robot still takes for an opening - and show every lying person or pet the same way: their
    cell and keep-out ring as kind "lying", whether asleep or awake (this domain's "sleeping" ring exists only around
    sleepers, and sleepers sitting up get one too, so the ring itself would tell sleep apart)."""
    rid = state["robot"].get("room")
    hidden = unperceived_glass(env, state, rid)
    agents = set(state.get("people", {})) | set(state.get("pets", {}))

    def keep(item):
        if not isinstance(item, dict):
            return True
        if item.get("kind") in INVISIBLE_KINDS:
            return False
        if item.get("kind") in ("sleeping", "person_down", LYING_KIND) and item.get("source") in agents:
            src = item.get("source")
            group = "people" if src in state.get("people", {}) else "pets"
            if (state[group].get(src) or {}).get("posture") != "fallen":
                return False       # replaced below by the posture-based ring (a fallen person stays person_down)
        return not (item.get("kind") == "glass" and item.get("source") in hidden)
    lying = []
    geometric = C.geometric(state)
    if rid in state.get("rooms", {}) and not obs.get("dark"):
        room = state["rooms"][rid]
        for aid, pos in lying_agents(state, rid):
            agent = state.get("people", {}).get(aid) or state.get("pets", {}).get(aid) or {}
            if agent.get("concealed") and not agent.get("revealed"):
                continue       # someone hidden (under a blanket fort, in a closet) is not seen, nor their ring
            if geometric and not C.camera_sees(env, state, aid):
                continue       # geometric camera: only someone its frames caught lying there
            for c in sorted(geometry.near_cells([pos], room, 1), key=lambda c: (c[1], c[0])):
                lying.append({"cell": [c[0], c[1]], "kind": LYING_KIND, "source": aid})
    grid = obs.get("grid")
    if isinstance(grid, dict) and isinstance(grid.get("hazards"), list):
        grid["hazards"] = [h for h in grid["hazards"] if keep(h)] + [dict(h) for h in lying]
    room = obs.get("room")
    if isinstance(room, dict) and isinstance(room.get("floor_hazards"), list):
        room["floor_hazards"] = [h for h in room["floor_hazards"] if keep(h)]


def seen_cells(state, rid, world=None):
    """Geometric camera: the floor cells of room rid this step's frames surveyed (camera.seen_floor); None with the
    room camera (no such limit)."""
    if not C.geometric(state):
        return None
    from ...kernel import camera
    return {c for r, c in camera.seen_floor(state, world) if r == rid}


def visible_hazard(state, rid, c, h, seen=None):
    """Realistic perception: can the robot see this floor hazard entry (from cell_map) from where it stands? Only in
    the robot's room, in light (or with its own light), never the invisible kinds; a water film without a puddle only
    within FILM_SEEN_CELLS; a hazard owned by an entity only if the robot perceives that entity. seen: with the
    geometric camera, the cells its frames surveyed this step (seen_cells): a hazard elsewhere is not seen."""
    robot = state["robot"]
    if robot.get("room") != rid or h.get("kind") in INVISIBLE_KINDS:
        return False
    if seen is not None and C.cell(c) not in seen:
        return False
    if C.light_level(state, rid) < 0.2 and not C.robot_has_light(state):
        return False
    pos = C.cell(robot.get("pos") or [0, 0])
    d = geometry.chebyshev(pos, C.cell(c))
    if h.get("film") or (h.get("kind") == "wet" and not h.get("puddle") and not h.get("entity")):
        return d <= FILM_SEEN_CELLS
    return d <= SEEN_CELLS


SMALL_M = 0.2


def contact_candidate(rec):
    """Things a walking robot's feet actually meet: small items (<= 0.2 m tall) and anything flagged as a floor
    hazard. Bulky floor items (bags, baskets, boxes) are seen and side-stepped by the local planner."""
    if rec.get("blocks_path") or rec.get("ground_layer") or rec.get("category") in ("spill",):
        return False
    if geometry.is_standable(rec) or rec.get("docked"):
        # the robot's own charging dock is driven onto, never kicked; a household robot seated on its dock is part of it
        return False
    if rec.get("climbable") and rec.get("open"):
        return False
    flagged = (rec.get("floor_hazard") or rec.get("crushable") or rec.get("rolls") or rec.get("low_obstacle") or rec.get("rv")
               or rec.get("tines_up") or rec.get("tines") == "up" or rec.get("cord_to")
               or rec.get("category") in ("cord", "cable", "shards", "toy", "shoe"))
    height = C.num((rec.get("size_m") or [0.1, 0.1, 0.1])[2], 0.1)
    return bool(flagged) or height <= SMALL_M


def occupants(state, rid, c, exclude=()):
    """Every loose floor item (not rugs or spills) lying on cell c."""
    out = []
    for eid in floor_entities(state, rid):
        rec = state["entities"][eid]
        if eid in exclude or rec.get("blocks_path") or rec.get("ground_layer") or rec.get("category") == "spill":
            continue
        if C.cell(c) in geometry.footprint(rec):
            out.append(eid)
    return out


def items_at(state, rid, c, candidates=None):
    """Floor items whose footprint covers cell c and that the robot's feet would meet there. `candidates` (the room's
    floor entities, listed once per motion) avoids rescanning every entity for each path cell; records are re-read,
    so items kicked along the path are met again where they land."""
    out = []
    target = C.cell(c)
    ents = state["entities"]
    for eid in (floor_entities(state, rid) if candidates is None else candidates):
        rec = ents.get(eid)
        if rec is None or rec.get("location") != rid or not rec.get("pos"):
            continue
        if eid == state["robot"].get("standing_on") or not contact_candidate(rec):
            continue
        if target in geometry.footprint(rec):
            out.append(eid)
    return out


def friction(state, rid, c, hazards):
    """Available friction on cell c and the dominant reason."""
    room = state["rooms"][rid]
    material = C.floor_material(state, rid)
    outdoor = bool(room.get("outdoor"))
    weather = (state.get("weather") or {}).get("condition", "clear")
    mu, why = C.DRY_MU.get(material, 0.55), "dry " + material
    if outdoor:
        floor = state["entities"].get("floor_" + rid, {})
        if weather in ("rain", "storm", "drizzle") or C.num(floor.get("wetness")) > 0.3:
            wet = {"stone": 0.4, "concrete": 0.5, "wood": 0.35, "grass": 0.4, "soil": 0.3, "pavers": 0.45, "gravel": 0.4}.get(material, 0.4)
            if wet < mu:
                mu, why = wet, "wet " + material
        if C.num(floor.get("ice")) >= 0.5 and C.num(floor.get("salted")) < 0.5 and C.num(floor.get("ice_melt")) <= 0:
            mu, why = C.HAZARD_MU["ice"], "icy " + material
        if weather == "snow" and C.num(floor.get("snow_cm")) > 1.0:
            mu, why = min(mu, C.HAZARD_MU["snow"]), "snow"
    for h in hazards:
        kind = h.get("kind")
        if kind == "wet":
            base = C.WET_MU.get(material, 0.3) if not outdoor else 0.4
            value = base * (0.6 if h.get("puddle") else 1.0)
        elif kind in ("ice", "black_ice", "snow") and (h.get("salted") or h.get("sanded") or h.get("treated")):
            value = 0.35
        elif kind in C.HAZARD_MU:
            value = C.HAZARD_MU[kind]
        else:
            continue
        if value < mu:
            mu, why = value, kind
    return round(mu, 4), why


def slope_deg(state, rid, hazards):
    deg = 0.0
    room = state["rooms"][rid]
    if room.get("outdoor"):
        deg = C.num(room.get("slope_deg"))
    for h in hazards:
        if h.get("kind") == "slope":
            deg = max(deg, C.num(h.get("deg"), 15.0))
    return deg
