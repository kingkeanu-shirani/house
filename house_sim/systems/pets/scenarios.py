"""Scenario helpers for pet task specs (setup op {"op": "helper", "name": "pets.<name>", "args": {...}}).

Each returns bindings (entity / pet ids and hidden numbers the spec's predicates may reference). Objects
owned by other domains (sinks, bins, fridges, toilets, fans, scales...) are taken from the generated house
when present, otherwise built from core types with explicit props (stand_in), so specs work with only the
pets domain loaded and after integration.

  pets.pet            create/complete a pet (species, size, room, behaviors, diet, hidden fields)
  pets.kit            bowls / bed / collar / leash / litter for a pet
  pets.stand_in       any cross-domain fixture (sink, trash_can, outdoor_trash, recycling_bin, compost_bin, toilet,
                      fridge, ceiling_fan, power_strip, bucket, trash_bag, kitchen_scale, towel, cleaner, ...)
  pets.feeding        bowls + food + scoop/scale + log for pets, with the meal branch (due | fed)
  pets.water          water bowls / fountain in given states
  pets.litter         a litter box (waste, buried clumps, abnormal output), litter, scoop, bags, bins, toilet
  pets.yard_waste     visible and concealed dog waste in a yard, bags, scooper, outdoor bins
  pets.toys           pet toys vs children's toys, damaged toys, a toy in play, the toy basket
  pets.hazards        pet-toxic / chewable items and safe places, for hide / pet-proof tasks
  pets.aquarium       tank, stock, heater/filter/light on a power strip, food, conditioner, siphon, bucket, log
  pets.terrarium      reptile enclosure, lamp (maybe burnt out), heat emitter, thermometer, IR gun
  pets.bird           cage with a bird, seed cup (maybe all hulls), water cup, tray and liner, fan, window
  pets.small_animal   small-animal cage with substrate, nest stash, bedding bags, carrier, trash bag
  pets.coop           coop, hens, nest box with eggs, waterer, basket, predator time
  pets.trapped        a pet shut in a room/closet/appliance, accidental or deliberate
  pets.medication     prescription, carriers, log, due/already-given branch
  pets.health_case    urgent / mild / normal presentation with evidence
  pets.stressor       storm / fireworks / vacuum / visitor and calming resources
  pets.walk           front-door walk setup (gear, kit, route)
  pets.arrival        a person arriving through a door at a set time
"""
import copy
import math

from ...kernel import clock, geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from ...kernel.predicates import root_room
from . import common as C
from . import model as M


# ----------------------------------------------------------------------
# small utilities

def _room(ctx, value, fallbacks=()):
    rooms = ctx.config["rooms"]
    if value and value in rooms:
        return value
    kinds = ([value] if value else []) + list(fallbacks)
    for kind in kinds:
        found = ctx.placer.rooms(kind)
        if found:
            return found[0]
    if kinds:
        return None
    return ctx.config.get("start_room")


def _need_room(ctx, value, fallbacks=()):
    rid = _room(ctx, value, fallbacks)
    if rid is None:
        raise SpecError("pets helper: no room among {}".format([value] + list(fallbacks)))
    return rid


def _ent(ctx, eid):
    return ctx.config["entities"][eid]


def _reachable_cells(ctx, room):
    """Floor cells the robot can stand on, joined to a doorway of the room (people/pets ignored: they move)."""
    return geometry.reachable_cells(ctx.placer.state(), room)


def _root_entity(state, eid):
    """The entity standing in the room that carries eid (eid itself when it stands there)."""
    seen = set()
    while eid in state["entities"] and eid not in seen:
        seen.add(eid)
        loc = state["entities"][eid].get("location")
        if loc in state["rooms"] or loc not in state["entities"]:
            return eid
        eid = loc
    return eid


def _reach_ok(ctx, eid):
    """The robot can get within arm's reach of it from the floor joined to the room's doorways (the kernel's access
    radius: one cell, two for overhead wall mounts) and it is not above the robot's reach."""
    state = ctx.placer.state()
    room, cells = geometry.anchor(state, eid)
    if room is None:
        return False
    if not cells:
        return True
    if geometry.height_m(state, eid) > geometry.DEFAULT_REACH_M:
        return False
    reach = _reachable_cells(ctx, room)
    root = state["entities"].get(_root_entity(state, eid)) or {}
    radius = geometry.access_radius(root)
    near = geometry.near_cells(cells, state["rooms"][room], radius) - set(cells)
    return bool(near & reach) or bool(set(cells) & reach)


def _agent_state(ctx, room, exclude=()):
    """Placement view in which the robot (when it already stands in the room) occupies its cell like an agent."""
    state = ctx.placer.state()
    robot = ctx.config.get("robot") or {}
    if robot.get("room") == room and robot.get("pos"):
        state["people"] = dict(state["people"], __robot__={"location": room, "pos": list(robot["pos"])})
    return state


def _agent_spot(ctx, room, near=None, exclude=()):
    """A free cell for a person/pet the robot can walk up to cleanly, outside doorway clearance, cutting no route and
    taking nobody's last access (geometry.free_floor_cell keep_access, the rule the generator's repair pass and
    Placer.agent_cell use). Without `near` a random spot of the room is preferred. None if the room has none."""
    state = _agent_state(ctx, room, exclude)
    if near is None:
        cols, rows = geometry.grid_size(state["rooms"][room])
        near = (ctx.rng.randrange(cols), ctx.rng.randrange(rows))
    return geometry.free_floor_cell(state, room, near=tuple(near), ignore=tuple(exclude), keep_access=True)


def _agent_cell(ctx, room, near=None, exclude=()):
    """_agent_spot, else any free reachable cell that is not a doorway cell (a cramped room: the least bad spot),
    else SpecError (kernel v2.3: agents are never dropped on a fallback cell such as [0, 0])."""
    cell = _agent_spot(ctx, room, near=near, exclude=exclude)
    if cell is not None:
        return list(cell)
    state = _agent_state(ctx, room, exclude)
    doors = set(geometry.doorway_cells(state, room))
    for kwargs in ({"clearance": False}, {"clearance": False, "reachable": False}):
        cell = geometry.free_floor_cell(state, room, near=tuple(near) if near else None, ignore=tuple(exclude), **kwargs)
        if cell is not None and tuple(cell) not in doors:
            return list(cell)
    raise SpecError("pets helper: no free cell for {} in {}".format(list(exclude) or "an agent", room))


def _free_area(ctx, surface):
    """Free top area of a surface (m2): its area minus the footprints of what stands on it."""
    rec = _ent(ctx, surface)
    used = 0.0
    for child in _config_children(ctx, surface):
        if _ent(ctx, child).get("relation", "on") == "on":
            w, d = geometry.footprint_m(_ent(ctx, child))
            used += w * d
    return geometry.surface_area_m2(rec) - used


def _neighbor_rooms(ctx, room):
    """Rooms one doorway away (indoor rooms first when the room is indoors), nearest doorway order."""
    out = []
    for cid, conn in sorted(ctx.config.get("connections", {}).items()):
        rooms = conn.get("rooms") or []
        if room in rooms:
            for other in rooms:
                if other != room and other in ctx.config["rooms"] and other not in out:
                    out.append(other)
    outdoor = bool(ctx.config["rooms"][room].get("outdoor"))
    return sorted(out, key=lambda r: (bool(ctx.config["rooms"][r].get("outdoor")) != outdoor, out.index(r)))


def _item_state(ctx, room):
    """Placement view for a loose item in `room`: people, pets and the robot move, so they are not route obstacles
    (as for Placer.furniture; geometry.free_floor_cell would otherwise take a resident standing in a galley kitchen's
    corridor as a wall and accept a cell that cuts the route once they step aside), but each one's cell stays free and
    reachable (a stand-in target there). A fresh view: the config is not touched."""
    state = ctx.placer.state()
    ents = dict(state["entities"])
    for group in ("people", "pets"):
        kept = {}
        for aid, agent in state.get(group, {}).items():
            if agent.get("location") == room and agent.get("pos") and not agent.get("enclosure"):
                ents["__agent_" + aid] = {"type": "agent_spot", "label": aid, "location": room, "pos": list(agent["pos"]),
                                          "size_m": [0.4, 0.4, 1.0], "portable": False, "blocks_path": False}
            else:
                kept[aid] = agent
        state[group] = kept
    robot = ctx.config.get("robot") or {}
    if robot.get("room") == room and robot.get("pos"):
        ents["__agent_robot"] = {"type": "agent_spot", "label": "robot", "location": room, "pos": list(robot["pos"]),
                                 "size_m": [0.4, 0.4, 1.0], "portable": False, "blocks_path": False}
    state["entities"] = ents
    return state


def _ring_cuts(state, room, eid):
    """Does the keep-out ring this floor item itself sources (geometry.placement_hazards: navigation's ring round a
    fragile or topple-prone floor item) cut a hazard-free route or someone's only hazard-free access? The kernel's
    keep_access check cannot see it: the ring only exists once the item stands there."""
    hazards = geometry.placement_hazards(state, room)
    foot = set(geometry.footprint(state["entities"][eid]))
    ring = {c for c, items in hazards.items() if c not in foot and items and all(src == eid for _, src in items)}
    if not ring:
        return False
    static = set(geometry.blocked_cells(state, room, include_agents=False))
    targets = geometry.access_targets(state, room, "all", ignore=(eid,))
    before = geometry.access_report(state, room, static | (set(hazards) - ring), targets)
    after = geometry.access_report(state, room, static | set(hazards), targets)
    return not geometry.not_worse(before, after)


RING_TRIES = 30


def _floor_spot(ctx, room, record, near=None, mode=True):
    """A floor cell for `record` in `room` by the kernel rule (geometry.free_floor_cell keep_access=mode: True =
    hazard-free routes, "plain" = plain walking graph, on the _item_state view) whose own derived keep-out ring cuts
    nothing either; None if there is none."""
    state = _item_state(ctx, room)
    ents = state["entities"]
    near = tuple(near) if near is not None else None
    first = geometry.free_floor_cell(state, room, near=near, record=record, keep_access=mode)
    if first is None:
        return None
    probe = "__pets_probe__"
    ents[probe] = dict(record, pos=list(first))
    if not _ring_cuts(state, room, probe):
        return first
    # the item's ring cuts a route there: the nearest cell (by the kernel rule) where it does not
    roomrec = ctx.config["rooms"][room]
    cols, rows = geometry.grid_size(roomrec)
    origin = near or tuple(first)
    tried = 0
    for cell in sorted(((x, y) for y in range(rows) for x in range(cols)), key=lambda c: (geometry.chebyshev(c, origin), c[1], c[0])):
        if tried >= RING_TRIES:
            break
        if not all(geometry.in_grid(roomrec, c) for c in geometry.footprint(dict(record, pos=list(cell)))):
            continue
        del ents[probe]
        ok = geometry.free_floor_cell(state, room, near=cell, record=record, keep_access=mode) == list(cell)
        ents[probe] = dict(record, pos=list(cell))
        if not ok:
            continue
        tried += 1
        if not _ring_cuts(state, room, probe):
            return list(cell)
    return None


SUPPLY_SURFACES = ("counter", "table", "workbench", "shelf_unit", "dryer", "washer", "side_table", "desk", "dresser")


def _place_floor(ctx, room, type_name, near=None, spill=True, surfaces=True, **props):
    """A loose item on the floor that keeps every room passable, in this order: a cell of the room by the kernel
    keep_access rule whose own keep-out ring cuts nothing either (_floor_spot); small supplies (surfaces=True) on a
    reachable surface there; (spill) the same in a room one doorway away (a crammed closet: the spare bag of litter
    stands in the hallway outside; never a shared corridor or the street, never across the indoor/outdoor line); last,
    a cell of the room that cuts no plain walking route. SpecError when nothing qualifies."""
    try:
        return _place_floor_in(ctx, room, type_name, near=near, surfaces=surfaces, **props)
    except SpecError:
        if spill:
            outdoor = bool(ctx.config["rooms"][room].get("outdoor"))
            for other in _neighbor_rooms(ctx, room):
                orec = ctx.config["rooms"][other]
                if orec.get("shared") or orec.get("type") in C.ESCAPE_ROOM_TYPES or bool(orec.get("outdoor")) != outdoor:
                    continue
                try:
                    return _place_floor_in(ctx, other, type_name, near=None, surfaces=surfaces, **props)
                except SpecError:
                    continue
        return _place_floor_in(ctx, room, type_name, near=near, mode="plain", surfaces=False, **props)


def _place_floor_in(ctx, room, type_name, near=None, mode=True, surfaces=True, **props):
    """One room of _place_floor: the floor (Placer.floor_item at the _floor_spot cell), else small supplies onto a
    reachable surface there; SpecError when neither works."""
    record = ctx.reg.make(type_name, room, **props)
    cell = _floor_spot(ctx, room, record, near=near, mode=mode)
    if cell is not None:
        eid = ctx.placer.floor_item(room, type_name, cell=cell, **props)
        if _reach_ok(ctx, eid):
            return eid
        del ctx.config["entities"][eid]          # dropped behind the washer: no use to anyone
    if surfaces and not record.get("blocks_path") and C.num(record.get("mass_kg"), 1.0) <= 15.0:
        # a crowded room: small supplies go on any reachable surface there instead
        for kind in SUPPLY_SURFACES:
            for other in ctx.placer.find(kind, room=room):
                orec = _ent(ctx, other)
                if orec.get("location") != room or not orec.get("accepts_items") or not _reach_ok(ctx, other):
                    continue
                eid = None
                if orec.get("surface"):
                    eid = ctx.placer.on(other, type_name, **props)
                elif orec.get("container") and kind == "shelf_unit":
                    eid = ctx.placer.inside(other, type_name, shelf=0, **props)
                if eid is not None:
                    return eid
    raise SpecError("pets helper: no floor space for {} in {}".format(type_name, room))


def _on(ctx, surface, type_name, **props):
    """On the surface (into a shelf unit or cabinet given instead); if it is full, on another reachable surface in the
    same room, else on the floor nearby."""
    srec = _ent(ctx, surface)
    if srec.get("container") and not srec.get("surface"):
        heights = srec.get("shelf_heights") or [0.0]
        probe = ctx.reg.make(type_name, surface, **props)
        for shelf in range(len(heights)):
            if _room_for(ctx, surface, shelf, C.num(probe.get("mass_kg")), C.num(probe.get("volume_l")), probe.get("size_m")):
                eid = ctx.placer.inside(surface, type_name, shelf=shelf if srec.get("shelf_heights") else None, **props)
                if eid is not None:
                    return eid
    else:
        eid = ctx.placer.on(surface, type_name, **props)
        if eid is not None:
            return eid
    room = root_room(ctx.placer.state(), surface)
    if room in ctx.config["rooms"]:
        for kind in ("counter", "table", "dining_table", "desk", "side_table", "coffee_table", "dresser", "tv_stand", "workbench", "shelf_unit"):
            for other in ctx.placer.find(kind, room=room):
                rec = _ent(ctx, other)
                if other == surface or not rec.get("surface") or rec.get("location") != room or not _reach_ok(ctx, other):
                    continue
                eid = ctx.placer.on(other, type_name, **props)
                if eid is not None:
                    return eid
        return _place_floor(ctx, room, type_name, **props)
    raise SpecError("pets helper: no space on {} for {}".format(surface, type_name))


def _enclosed(ctx, eid):
    """Inside a closed container (not visible until opened)?"""
    ents = ctx.config["entities"]
    loc = ents[eid].get("location")
    while loc in ents:
        rec = ents[loc]
        if rec.get("openable") and not rec.get("open"):
            return True
        loc = rec.get("location")
    return False


def _set_out(ctx, eid, surface):
    """Move an existing tool that sits shut away in a cupboard onto a work surface (the household keeps it out)."""
    if eid is None or not _enclosed(ctx, eid):
        return eid
    rec = _ent(ctx, eid)
    saved = {k: copy.deepcopy(rec.get(k)) for k in ("location", "relation", "spos", "shelf", "pos")}
    room = root_room(ctx.placer.state(), surface)
    candidates = [surface] + [s for kind in ("counter", "table", "dining_table", "side_table", "shelf_unit")
                              for s in ctx.placer.find(kind, room=room) if s != surface and _ent(ctx, s).get("surface")]
    for target in candidates:
        for key in ("spos", "shelf", "pos"):
            rec.pop(key, None)
        rec["location"], rec["relation"] = target, "on"
        spot = geometry.surface_spot(ctx.placer.state(), target, eid)
        if spot is not None:
            rec["spos"] = spot
            return eid
    for key, value in saved.items():
        if value is None:
            rec.pop(key, None)
        else:
            rec[key] = value
    return eid


SURFACE_KINDS = ("counter", "table", "dining_table", "desk", "side_table", "coffee_table", "dresser", "tv_stand", "workbench")
MIN_FREE_M2 = 0.06       # a work spot of ~25 x 25 cm: room to set down a bowl, a can or the log


def _surface_in(ctx, room, kinds=SURFACE_KINDS):
    """A reachable work surface of the first kind present: the one with the most free top area (a counter crowded with
    a microwave, a dish rack and a knife block leaves no room to set a bowl down), moving on to the next kind when
    every surface of a kind is full."""
    fallback = None
    for kind in kinds:
        found = [e for e in ctx.placer.find(kind, room=room) if _ent(ctx, e).get("surface") and _ent(ctx, e).get("location") == room
                 and _reach_ok(ctx, e)]
        if not found:
            continue
        best = sorted(found, key=lambda e: (-round(_free_area(ctx, e), 4), e))[0]
        if _free_area(ctx, best) >= MIN_FREE_M2:
            return best
        if fallback is None:
            fallback = best
    return fallback


def _surface_or_new(ctx, room, label="side table"):
    """A reachable surface in the room; else a new side table (Placer.furniture keeps every route and access); else, in
    a room packed wall to wall (a laundry closet), the washer/dryer top or a shelf unit there, else a surface in a room
    one doorway away."""
    surface = _surface_in(ctx, room)
    if surface and _free_area(ctx, surface) >= MIN_FREE_M2:
        return surface
    eid = _furniture(ctx, room, "side_table", label=label)
    if eid is not None and not _reach_ok(ctx, eid):
        del ctx.config["entities"][eid]
        eid = None
    if eid is not None:
        return eid
    if surface:
        return surface
    surface = _surface_in(ctx, room, ("washer", "dryer", "washing_machine", "clothes_dryer", "vanity", "bench", "shelf_unit"))
    if surface:
        return surface
    for kind in ("shelf_unit", "cabinet", "closet_shelving", "bookshelf"):
        for other in ctx.placer.find(kind, room=room):
            if _ent(ctx, other).get("container") and _ent(ctx, other).get("location") == room and _reach_ok(ctx, other):
                return other          # _on puts things inside on the bottom shelf
    for other in _neighbor_rooms(ctx, room):
        orec = ctx.config["rooms"][other]
        if orec.get("outdoor") or orec.get("shared") or orec.get("type") in C.ESCAPE_ROOM_TYPES:
            continue
        surface = _surface_in(ctx, other)
        if surface:
            return surface
    raise SpecError("pets helper: no surface in " + room)


def _crushable(rec):
    """Would a 7 kg food bag set down beside it be taken as resting on it (fragile, crushable, low load rating)?"""
    hazards = rec.get("hazards") or []
    return bool(rec.get("fragile") or rec.get("crushable") or "fragile" in hazards
                or rec.get("max_load_kg") is not None and C.num(rec.get("max_load_kg"), 1e9) < 10.0)


FURNITURE_TRIES = 6


def _access_reports(ctx, room):
    """(plain, hazard-free) access reports of a room over every target (geometry.access_targets level "all"), agents
    ignored (they move)."""
    state = ctx.placer.state()
    static = set(geometry.blocked_cells(state, room, include_agents=False))
    targets = geometry.access_targets(state, room, "all")
    hazards = set(geometry.placement_hazards(state, room))
    return (geometry.access_report(state, room, static, targets), geometry.access_report(state, room, static | hazards, targets))


def _furniture(ctx, room, type_name, strict=True, **props):
    """Placer.furniture (kernel v2.3: doorway connectivity, the robot dock and every piece of furniture, fixture,
    mount and loose floor item that was accessible stays accessible, trying full protection first, then basic). A
    helper's furniture is extra furniture in a lived-in house, so strict also demands the full level and the
    hazard-free routes not worse (a new shelf must not leave a laundry basket as the only way to the sink, or seal a
    radiator) and the new piece itself reachable cleanly; None when no such spot turns up (callers fall back). People
    and pets move (they settle at episode start)."""
    if not strict:
        return ctx.placer.furniture(room, type_name, **props)
    plain0, safe0 = _access_reports(ctx, room)
    for _attempt in range(FURNITURE_TRIES):
        eid = ctx.placer.furniture(room, type_name, **props)
        if eid is None:
            return None
        plain1, safe1 = _access_reports(ctx, room)
        if geometry.not_worse(plain0, plain1, new_keys=[eid]) and geometry.not_worse(safe0, safe1, new_keys=[eid]):
            return eid
        del ctx.config["entities"][eid]
    return None


def _furniture_sure(ctx, room, type_name, **props):
    """Strict placement first, else Placer.furniture's own (basic) guarantee."""
    return _furniture(ctx, room, type_name, **props) or _furniture(ctx, room, type_name, strict=False, **props)


LIVING_KINDS = ("living_room", "dining_room", "office", "playroom", "craft_room", "bedroom", "master_bedroom", "basement")


def _room_candidates(ctx, value, fallbacks=(), indoor_any=False):
    """Rooms to try in order: the named room (id or type: every room of that type), the fallback types, then (with
    indoor_any) any other room of the dwelling's living area."""
    rooms = ctx.config["rooms"]
    out = []
    if value and value in rooms:
        out.append(value)
    for kind in ([value] if value and value not in rooms else []) + list(fallbacks):
        for rid in ctx.placer.rooms(kind):
            if rid not in out:
                out.append(rid)
    if indoor_any:
        for kind in LIVING_KINDS:
            for rid in ctx.placer.rooms(kind):
                if rid not in out:
                    out.append(rid)
    return out


def _furniture_any(ctx, value, fallbacks, type_name, what, indoor_any=True, **props):
    """(eid, room) of a new piece of furniture in the first candidate room that has space for it; SpecError only when
    no such room exists in the house."""
    candidates = _room_candidates(ctx, value, fallbacks, indoor_any=indoor_any)
    if not candidates:
        raise SpecError("pets helper: no room among {} for the {}".format([value] + list(fallbacks), what))
    for strict in (True, False):           # a room where it keeps every access first, else Placer's basic guarantee
        for rid in candidates:
            eid = _furniture(ctx, rid, type_name, strict=strict, **props)
            if eid is not None:
                return eid, rid
    raise SpecError("pets helper: no space for the {} in {}".format(what, candidates))


STORAGE_KINDS = ("cabinet", "closet_shelving", "shelf_unit", "bookshelf")
# Supplies are kept at <= 80 % of a shelf's rated load. The rating is the kernel's (TIER1_PHYSICS 4.6, the overload
# law): the record's shelf_rating_kg / shelf_load_kg, else supports.json (base cabinet 30 kg, wall cabinet 20, wire
# closet shelving and particleboard bookcases 25, wardrobes 40, steel shelving units 90), with its per-instance
# variation, so a filled shelf stays under the very limit the kernel enforces once the robot puts a bag back.
SHELF_USE = 0.8


def _shelf_rating(ctx, container):
    """The kernel's effective per-shelf rating (kg) of a storage piece, or None when its shelves are unrated."""
    from ...kernel import physics
    rating = physics.props(ctx.placer.state(), container).shelf_rating_kg
    return None if rating is None or rating != rating or rating == float("inf") else float(rating)


def _config_children(ctx, eid):
    return [c for c, r in sorted(ctx.config["entities"].items()) if r.get("location") == eid]


def _config_mass(ctx, eid):
    """Mass of eid and everything in or on it: the kernel's one subtree mass (liquids at their liquids.json density)."""
    from ...kernel import physics
    return physics.subtree_mass(ctx.placer.state(), eid)


def _shelf_items(ctx, container, shelf):
    return [c for c in _config_children(ctx, container)
            if _ent(ctx, c).get("relation") == "in" and int(_ent(ctx, c).get("shelf", 0) or 0) == int(shelf)]


def _shelf_load(ctx, container, shelf):
    return sum(_config_mass(ctx, c) for c in _shelf_items(ctx, container, shelf))


def _used_l(ctx, container):
    return sum(C.num(_ent(ctx, c).get("volume_l")) for c in _config_children(ctx, container) if _ent(ctx, c).get("relation") == "in")


def _room_for(ctx, container, shelf, kg, litres, size=None):
    """Can `kg` / `litres` (an item of `size`) go onto this shelf without overloading it or overfilling the box?"""
    rec = _ent(ctx, container)
    if size is not None and not geometry.fits_inside(size, rec.get("inner_size_m")):
        return False
    cap = C.num(rec.get("capacity_l"))
    if cap > 0 and _used_l(ctx, container) + litres > cap + 1e-9:
        return False
    rating = _shelf_rating(ctx, container)
    if rating is not None and _shelf_load(ctx, container, shelf) + kg > SHELF_USE * rating + 1e-9:
        return False
    return True


def _storage_in(ctx, room, incoming_kg=0.0, incoming_l=0.0):
    """Low storage the robot can reach. Prefers a cabinet holding nothing crushable (a heavy food bag goes in), with
    room on its bottom shelf for `incoming_kg` / `incoming_l`."""
    options = []
    for rank, kind in enumerate(STORAGE_KINDS):
        for e in ctx.placer.find(kind, room=room):
            rec = _ent(ctx, e)
            if rec.get("location") != room or not rec.get("container") or not _reach_ok(ctx, e) \
                    or geometry.height_m(ctx.placer.state(), e) >= 1.0:
                continue
            crush = sum(1 for c in _config_children(ctx, e) if _crushable(_ent(ctx, c)))
            full = not _room_for(ctx, e, 0, incoming_kg, incoming_l)
            options.append((full, crush > 0, rank, crush, e))
    if not options:
        return None
    options.sort()
    return options[0][-1]


def _rehome(ctx, item, room, exclude):
    """Move a stored item out of `exclude` to other storage in the room (dishes to a wall cabinet, the rest to any
    shelf with room), else onto a work surface. False when nothing takes it."""
    rec = _ent(ctx, item)
    kg, litres, size = _config_mass(ctx, item), C.num(rec.get("volume_l")), rec.get("size_m") or [0.1, 0.1, 0.1]
    crush = _crushable(rec)
    options = []
    # only the pet-food cupboard gets heavy things put into it during a task, so dishes are safe in any other one
    for rank, kind in enumerate(("upper_cabinet",) + STORAGE_KINDS):
        for e in ctx.placer.find(kind, room=room):
            erec = _ent(ctx, e)
            if e == exclude or erec.get("location") != room or not erec.get("container") or not _reach_ok(ctx, e):
                continue
            heights = erec.get("shelf_heights") or [0.0]
            for shelf in range(len(heights)):
                if _room_for(ctx, e, shelf, kg, litres, size):
                    options.append((0 if crush == (kind == "upper_cabinet") else 1, rank, _shelf_load(ctx, e, shelf), e, shelf))
    saved = {k: copy.deepcopy(rec.get(k)) for k in ("location", "relation", "spos", "shelf", "pos")}
    if options:
        options.sort()
        _, _, _, target, shelf = options[0]
        for key in ("spos", "pos"):
            rec.pop(key, None)
        rec["location"], rec["relation"] = target, "in"
        if _ent(ctx, target).get("shelf_heights"):
            rec["shelf"] = shelf
        else:
            rec.pop("shelf", None)
        return True
    for kind in ("counter", "table", "dining_table", "side_table", "shelf_unit"):
        for surface in ctx.placer.find(kind, room=room):
            srec = _ent(ctx, surface)
            if not srec.get("surface") or srec.get("location") != room or not _reach_ok(ctx, surface):
                continue
            for key in ("spos", "shelf", "pos"):
                rec.pop(key, None)
            rec["location"], rec["relation"] = surface, "on"
            spot = geometry.surface_spot(ctx.placer.state(), surface, item)
            if spot is not None:
                rec["spos"] = spot
                return True
    for key, value in saved.items():
        if value is None:
            rec.pop(key, None)
        else:
            rec[key] = value
    return False


def _make_room(ctx, storage, incoming_kg, incoming_l, keep=()):
    """The household keeps its pet food in this cupboard: dishes and other crushable things are kept elsewhere (a
    7 kg food bag goes in next to them), and the bottom shelf has room for the food (load rating, volume)."""
    room = _ent(ctx, storage).get("location")
    if room not in ctx.config["rooms"]:
        return
    for item in _config_children(ctx, storage):
        if item not in keep and _crushable(_ent(ctx, item)):
            _rehome(ctx, item, room, storage)
    heights = _ent(ctx, storage).get("shelf_heights") or [0.0]
    for _ in range(60):
        if _room_for(ctx, storage, 0, incoming_kg, incoming_l):
            break
        movable = sorted((-_config_mass(ctx, c), c) for c in _shelf_items(ctx, storage, 0) if c not in keep)
        if not movable:
            break
        item = movable[0][1]
        rec = _ent(ctx, item)
        moved = False
        for shelf in range(1, len(heights)):
            if _room_for(ctx, storage, shelf, _config_mass(ctx, item), 0.0, rec.get("size_m")):
                rec["shelf"] = shelf
                moved = True
                break
        if not moved and not _rehome(ctx, item, room, storage):
            break


def _state(ctx):
    return {"clock": ctx.config.get("clock", {}), "time_s": 0.0, "rooms": ctx.config["rooms"], "entities": ctx.config["entities"],
            "pets": ctx.config.get("pets", {}), "people": ctx.config.get("people", {}), "robot": {"room": ctx.config.get("start_room"), "hands": {}}}


# ----------------------------------------------------------------------
# stand-ins for other domains' fixtures

STAND_INS = {
    "sink": {"types": ("kitchen_sink", "sink", "utility_sink", "bathroom_sink"), "where": {"water_source": True},
             "make": ("mount", "generic_item", {"label": "sink", "category": "sink", "water_source": True, "drain": True, "portable": False,
                                                "accepts_items": True, "container": True, "capacity_l": 30.0, "inner_size_m": [0.55, 0.45, 0.22],
                                                "size_m": [0.6, 0.5, 0.25], "mass_kg": 15.0, "flow_lpm": 6.0}, 0.85)},
    "trash_can": {"types": ("kitchen_trash_can",), "where": {"waste_stream": "trash"}, "indoor": True,
                  "make": ("floor", "generic_item", {"label": "kitchen trash can", "category": "trash_can", "waste_stream": "trash", "portable": False,
                                                     "accepts_items": True, "container": True, "capacity_l": 50.0, "inner_size_m": [0.36, 0.27, 0.6],
                                                     "size_m": [0.4, 0.3, 0.65], "mass_kg": 3.5}, None)},
    "outdoor_trash": {"types": ("trash_cart",), "where": {"waste_stream": "trash"}, "outdoor": True,
                      "make": ("floor", "generic_item", {"label": "outdoor trash bin", "category": "trash_can", "waste_stream": "trash", "portable": False,
                                                         "accepts_items": True, "container": True, "capacity_l": 240.0, "inner_size_m": [0.55, 0.6, 0.9],
                                                         "size_m": [0.6, 0.7, 1.05], "mass_kg": 12.0, "openable": True, "open": False,
                                                         "label_text": "TRASH"}, None)},
    "recycling_bin": {"types": ("recycling_bin",), "where": {"waste_stream": "recycling"},
                      "make": ("floor", "generic_item", {"label": "recycling bin", "category": "trash_can", "waste_stream": "recycling", "portable": False,
                                                         "accepts_items": True, "container": True, "capacity_l": 60.0, "inner_size_m": [0.35, 0.3, 0.45],
                                                         "size_m": [0.38, 0.32, 0.5], "mass_kg": 1.5, "label_text": "RECYCLING", "color": "blue"}, None)},
    "compost_bin": {"types": ("compost_bin",), "where": {"waste_stream": "compost"},
                    "make": ("floor", "generic_item", {"label": "compost bin", "category": "trash_can", "waste_stream": "compost", "portable": False,
                                                       "accepts_items": True, "container": True, "capacity_l": 300.0, "inner_size_m": [0.7, 0.7, 0.8],
                                                       "size_m": [0.75, 0.75, 0.9], "mass_kg": 10.0, "label_text": "COMPOST", "color": "green"}, None)},
    "toilet": {"types": ("toilet",), "where": {"toilet": True},
               "make": ("floor", "generic_item", {"label": "toilet", "category": "toilet", "toilet": True, "portable": False, "accepts_items": True,
                                                  "container": True, "capacity_l": 6.0, "inner_size_m": [0.35, 0.3, 0.2], "size_m": [0.4, 0.65, 0.75],
                                                  "mass_kg": 30.0, "drain": True}, None)},
    "fridge": {"types": ("refrigerator", "fridge"), "where": {"category": "fridge"},
               "make": ("furniture", "openable_storage", {"label": "refrigerator", "category": "fridge", "internal_temp_c": 4.0, "size_m": [0.7, 0.7, 1.8],
                                                         "inner_size_m": [0.6, 0.55, 0.5], "shelf_heights": [0.3, 0.7, 1.1], "capacity_l": 300.0,
                                                         "material": "steel", "mass_kg": 70.0}, None)},
    "ceiling_fan": {"types": ("ceiling_fan",), "where": {"category": "ceiling_fan"},
                    "make": ("mount", "generic_item", {"label": "ceiling fan", "category": "ceiling_fan", "switchable": True, "on": False, "portable": False,
                                                       "size_m": [1.2, 1.2, 0.3], "mass_kg": 6.0, "noise_db": 40}, 2.3)},
    "power_strip": {"types": ("power_strip",), "where": {"category": "power_strip"},
                    "make": ("floor", "generic_item", {"label": "power strip", "category": "power_strip", "outlets": 6, "portable": True,
                                                       "size_m": [0.3, 0.06, 0.04], "mass_kg": 0.4}, None)},
    "bucket": {"types": ("bucket", "mop_bucket"), "where": {"category": "bucket"},
               "make": ("floor", "generic_item", {"label": "bucket", "category": "bucket", "liquid_container": True, "liquid": None, "liquid_l": 0.0,
                                                  "capacity_l": 10.0, "size_m": [0.3, 0.3, 0.3], "mass_kg": 0.6, "portable": True}, None)},
    "trash_bag": {"types": (), "where": {},
                  "make": ("floor", "generic_item", {"label": "trash bag (50 l)", "category": "bag", "accepts_items": True, "container": True,
                                                     "soft_sided": True, "capacity_l": 50.0, "inner_size_m": [0.5, 0.3, 0.6], "size_m": [0.3, 0.2, 0.05],
                                                     "mass_kg": 0.05, "waste_stream": None, "portable": True}, None)},
    "kitchen_scale": {"types": ("kitchen_scale",), "where": {"tool_kind": "kitchen_scale"},
                      "make": ("on", "generic_item", {"label": "digital kitchen scale", "category": "kitchen_tool", "tool_kind": "kitchen_scale",
                                                      "capacity_g": 5000.0, "size_m": [0.22, 0.18, 0.025], "mass_kg": 0.6,
                                                      "affordances": ["weigh"]}, None)},
    "towel": {"types": (), "where": {},
              "make": ("on", "generic_item", {"label": "old towel", "category": "towel", "material": "cotton", "size_m": [0.4, 0.3, 0.05],
                                              "mass_kg": 0.3, "wetness": 0.0}, None)},
    "white_noise_machine": {"types": (), "where": {"white_noise": True},
                            "make": ("on", "generic_item", {"label": "white noise machine", "category": "sound_machine", "white_noise": True,
                                                            "switchable": True, "on": False, "size_m": [0.12, 0.12, 0.08], "mass_kg": 0.3}, None)},
    "vacuum": {"types": (), "where": {"category": "vacuum"},
               "make": ("floor", "generic_item", {"label": "upright vacuum", "category": "vacuum", "switchable": True, "on": True, "noise_db": 75,
                                                  "size_m": [0.35, 0.3, 1.1], "mass_kg": 6.5}, None)},
}


BIN_KINDS = ("trash_can", "outdoor_trash", "recycling_bin", "compost_bin")


def stand_in(ctx, kind, room, label=None, **props):
    spec = STAND_INS[kind]
    # bins must be real receptacles: other things carry a waste_stream too (a spent HVAC filter says "trash")
    fits = (lambda rec: C.is_bin(rec)) if kind in BIN_KINDS else (lambda rec: True)
    for type_name in spec["types"]:
        if ctx.reg.known(type_name):
            found = [f for f in ctx.placer.find(type_name, room=room) if fits(_ent(ctx, f))]
            if found:
                return next((f for f in found if _reach_ok(ctx, f)), found[0])
    if spec["where"]:
        state = ctx.placer.state()
        for eid in sorted(ctx.config["entities"]):
            rec = ctx.config["entities"][eid]
            if root_room(state, eid) == room and all(rec.get(k) == v for k, v in spec["where"].items()) and fits(rec):
                return eid
    how, type_name, base, height = spec["make"]
    record = dict(copy.deepcopy(base))
    record.update(props)
    if label:
        record["label"] = label
    if how == "mount":
        return ctx.placer.mount(room, type_name, height=height, **record)
    if how == "furniture":
        eid = _furniture_sure(ctx, room, type_name, **record)
        if eid is None:
            raise SpecError("pets.stand_in: no room for {} in {}".format(kind, room))
        return eid
    if how == "on":
        surface = _surface_or_new(ctx, room)
        return _on(ctx, surface, type_name, **record)
    return _place_floor(ctx, room, type_name, near=ctx.placer.wall_cell(room), **record)


CLEANERS = {"mild_soap": ("unscented dish soap", "mild_soap"), "ammonia": ("ammonia glass cleaner", "ammonia"),
            "bleach": ("chlorine bleach", "bleach"), "pine": ("pine oil cleaner", "phenol"), "enzymatic": ("enzymatic pet cleaner", "enzymatic")}


def cleaner(ctx, surface, kind):
    label, ckind = CLEANERS[kind]
    return _on(ctx, surface, "generic_item", label=label, category="cleaner", cleaner_kind=ckind, product=ckind,
               hazards=[] if ckind in ("mild_soap", "enzymatic") else ["toxic"], safe_grips=["normal"] if ckind not in ("mild_soap", "enzymatic") else [],
               handled_with=["rubber_gloves"], size_m=[0.08, 0.06, 0.25], mass_kg=0.6, volume_l=0.8)


@scenario_helper("pets.stand_in")
def h_stand_in(ctx, kind, room=None, label=None, props=None):
    rid = _need_room(ctx, room, ("kitchen",))
    eid = stand_in(ctx, kind, rid, label=label, **(props or {}))
    return {"result": eid, kind: eid}


# ----------------------------------------------------------------------
# pets and their kit

@scenario_helper("pets.pet")
def h_pet(ctx, id, species="dog", size=None, room=None, label=None, behaviors=None, diet=None, portion_g=None, props=None,
          hidden=None, near=None, owner=None, posture=None, awake=True):
    pets = ctx.config.setdefault("pets", {})
    rid = _need_room(ctx, room, ("living_room", "kitchen"))
    record = pets.get(id) or {}
    record.update({"species": species, "label": label or record.get("label") or id.replace("_", " ").title()})
    if size:
        record["size"] = size
    record["location"] = rid
    record["pos"] = _agent_cell(ctx, rid, near, exclude=(id,))
    record["awake"] = awake
    if posture:
        record["posture"] = posture
    if owner:
        record["owner"] = owner
    M.ensure_record(record, id)
    record["behaviors"].update(behaviors or {})
    record["escape_risk"] = C.behavior(record, "door_dash")
    if diet:
        record["diet"].update(diet)
    if portion_g is not None:
        record["_diet"]["portion_g"] = float(portion_g)
    record.update(copy.deepcopy(props or {}))
    for key, value in (hidden or {}).items():
        record[key] = copy.deepcopy(value)
    record["baseline_anxiety"] = min(C.num(record.get("baseline_anxiety"), 0.15), 0.2)
    record["stress_peak"] = C.num(record.get("anxiety"), 0.15)
    pets[id] = record
    return {"result": id, "pet": id, "pet_room": rid, "portion": record["_diet"]["portion_g"]}


def _collar(ctx, pid, slack_cm=3.0, kind="dog_collar", buckled=True):
    pet = ctx.config["pets"][pid]
    if kind == "dog_collar":
        props = {"settings": {"length_cm": int(round(C.neck_cm(pet) + slack_cm))}, "buckled": buckled}
    else:
        props = {"settings": {"girth_cm": int(round(C.chest_cm(pet) + slack_cm))}, "buckles_closed": buckled}
    eid = ctx.placer.make(kind, pid, label="{}'s {}".format(pet.get("label", pid), "collar" if kind == "dog_collar" else "harness"), **props)
    ctx.config["entities"][eid]["relation"] = "worn"
    return eid


@scenario_helper("pets.kit")
def h_kit(ctx, pet, items=("bowls",), room=None, collar_slack_cm=3.0, water_l=None, near=None):
    """items: bowls, mat, bed, collar, harness, leash, litter, crate, carrier, toys."""
    record = ctx.config["pets"][pet]
    sp = C.species(record)
    out = {}
    rid = _need_room(ctx, room, ("kitchen", "living_room")) if room or "bowls" in items else None
    if "bowls" in items:
        mat = _place_floor(ctx, rid, "feeding_mat", near=near or ctx.placer.wall_cell(rid), surfaces=False, owner=pet)
        food = _on(ctx, mat, "dog_food_bowl" if sp == "dog" else "cat_food_bowl", owner=pet, label="{}'s food bowl".format(record["label"]), home=mat)
        wat = _on(ctx, mat, "dog_water_bowl" if sp == "dog" else "cat_water_bowl", owner=pet, label="{}'s water bowl".format(record["label"]), home=mat)
        wrec = _ent(ctx, wat)
        wrec["liquid_l"] = round(wrec["capacity_l"] * 0.75 if water_l is None else water_l, 3)
        wrec["_last_l"] = wrec["liquid_l"]
        out.update(mat=mat, food_bowl=food, water_bowl=wat)
        record.setdefault("needs", {}).update(food=food, water=wat)
    if "collar" in items:
        out["collar"] = _collar(ctx, pet, collar_slack_cm)
    if "harness" in items:
        out["harness"] = _collar(ctx, pet, collar_slack_cm, kind="dog_harness" if sp == "dog" else "cat_harness")
    if "leash" in items:
        entry = _room(ctx, None, ("entryway", "mudroom")) or record["location"]
        hooks = ctx.placer.find("coat_hooks", room=entry)
        if hooks:
            out["leash"] = ctx.placer.hang(hooks[0], "leash", label="{}'s leash".format(record["label"]))
        if not out.get("leash"):
            out["leash"] = _on(ctx, _surface_or_new(ctx, entry), "leash", label="{}'s leash".format(record["label"]))
    if "bed" in items:
        living = _need_room(ctx, None, ("living_room",))
        out["bed"] = _place_floor(ctx, living, "pet_bed", near=ctx.placer.wall_cell(living), surfaces=False, owner=pet,
                                  label="{}'s bed".format(record["label"]))
    if "crate" in items:
        crate, _croom = _furniture_any(ctx, room, ("living_room", "bedroom", "master_bedroom", "kitchen"), "pet_crate", "crate",
                                       crate_size="large" if C.size_of(record) == "large" else "medium")
        out["crate"] = crate
    if "carrier" in items:
        croom = _need_room(ctx, room, ("living_room", "bedroom"))
        out["carrier"] = _place_floor(ctx, croom, "pet_carrier", surfaces=False)
    if "litter" in items:
        lroom = _need_room(ctx, None, ("laundry_room", "bathroom", "basement", "utility_closet", "hallway"))
        box = _place_floor(ctx, lroom, "litter_box", near=ctx.placer.wall_cell(lroom), surfaces=False, owner=pet)
        _ent(ctx, box)["home"] = lroom
        _ent(ctx, box)["home_pos"] = list(_ent(ctx, box)["pos"])
        record.setdefault("needs", {})["litter"] = box
        record["_litter_pref"] = _ent(ctx, box).get("litter_kind")
        out["litter_box"] = box
    if "toys" in items:
        troom = record["location"]
        out["toy"] = _place_floor(ctx, troom, "fetch_ball" if sp == "dog" else "catnip_mouse", surfaces=False, pet_toy_of=pet)
    return out


# ----------------------------------------------------------------------
# feeding

def _slot_key(ctx, pet):
    state = _state(ctx)
    key, slot, start = M.current_slot(state, pet)
    return key, slot


@scenario_helper("pets.feeding")
def h_feeding(ctx, pets, room="kitchen", branch="due", separate=False, with_scale=True, log=True, note=False, store="cabinet",
              rx_pet=None, wet_for=None, treats=False):
    """Bowls, food sources and the log for each pet. branch: due (meal not given yet) | fed (a resident already fed it
    this slot; the log says so). separate=true puts the cat's dish on the counter... no: on a raised shelf (1.4 m), out of a dog's
    reach, and the dog's bowl on its mat. rx_pet gets a prescription diet bag. wet_for = pet id served wet food (a can)."""
    rid = _need_room(ctx, room, ("kitchen", "dining_room", "living_room"))
    counter = _surface_or_new(ctx, rid, "counter")
    out = {"feed_room": rid, "counter": counter}
    # what goes onto the cupboard's bottom shelf: one bag per food kind (+ a can for wet food)
    stored = []
    for pid in pets:
        sp = C.species(ctx.config["pets"][pid])
        bag_type = "rx_kibble_bag" if pid == rx_pet else ("dog_kibble_bag" if sp == "dog" else "cat_kibble_bag")
        if bag_type not in stored:
            stored.append(bag_type)
        if pid == wet_for:
            stored.append("cat_wet_food_can" if sp == "cat" else "dog_wet_food_can")
    probes = [ctx.reg.make(t, rid) for t in stored]
    incoming_kg = sum(C.num(p.get("mass_kg")) for p in probes)
    incoming_l = sum(C.num(p.get("volume_l")) for p in probes)
    storage = _storage_in(ctx, rid, incoming_kg, incoming_l) if store == "cabinet" else None
    if store == "cabinet" and storage is None:
        storage = _furniture_sure(ctx, rid, "cabinet")
    if storage is not None:
        _make_room(ctx, storage, incoming_kg, incoming_l)
    out["scoop"] = (ctx.placer.inside(storage, "pet_food_scoop", shelf=1) if storage else None) or _on(ctx, counter, "pet_food_scoop")
    if with_scale:
        out["scale"] = _set_out(ctx, stand_in(ctx, "kitchen_scale", rid), counter)
    entries, logged = [], []
    by = sorted(pid for pid, p in ctx.config.get("people", {}).items() if p.get("role", "adult") == "adult")
    who = ctx.config["people"][by[0]].get("label", by[0]) if by else "Sam"
    for pid in pets:
        pet = ctx.config["pets"][pid]
        sp = C.species(pet)
        label = pet.get("label", pid)
        if separate and sp == "cat":
            shelf = ctx.placer.mount(rid, "wall_shelf", height=1.6, label="raised cat feeding shelf",
                                     size_m=[0.6, 0.3, 0.03], surface_size_m=[0.6, 0.3])
            bowl = _on(ctx, shelf, "cat_food_bowl", owner=pid, label="{}'s food dish".format(label), home=shelf)
            out["cat_shelf"] = shelf
        else:
            mat = _place_floor(ctx, rid, "feeding_mat", near=ctx.placer.wall_cell(rid), surfaces=False, owner=pid)
            bowl = _on(ctx, mat, "dog_food_bowl" if sp == "dog" else "cat_food_bowl", owner=pid, label="{}'s food bowl".format(label), home=mat)
        out["bowl_" + pid] = bowl
        pet.setdefault("needs", {})["food"] = bowl
        if pid == rx_pet:
            kind = "rx_renal_kibble"
            bag_type = "rx_kibble_bag"
            pet["diet"]["foods"] = [kind]
            pet["diet"]["no_treats"] = True
        else:
            kind = "dog_kibble" if sp == "dog" else "cat_kibble"
            bag_type = "dog_kibble_bag" if sp == "dog" else "cat_kibble_bag"
            pet["diet"]["foods"] = [kind]
        if pid == wet_for:
            can_type = "cat_wet_food_can" if sp == "cat" else "dog_wet_food_can"
            can = (ctx.placer.inside(storage, can_type, shelf=0) if storage else None) or _on(ctx, counter, can_type)
            out["can_" + pid] = can
            wet_kind = _ent(ctx, can)["pet_food"]["kind"]
            pet["diet"]["foods"] = [kind, wet_kind]
        existing = [b for b in out.values() if isinstance(b, str) and b in ctx.config["entities"] and _ent(ctx, b).get("type") == bag_type]
        if existing:
            bag = existing[0]
        else:
            bag = (ctx.placer.inside(storage, bag_type, shelf=0) if storage else None) or _place_floor(ctx, rid, bag_type)
        out["food_" + pid] = bag
        key, slot = _slot_key(ctx, pet)
        out["slot_" + pid] = key
        given = dict(pet.get("meals_given") or {})
        if branch == "fed":
            given[key] = 1
            hm = dict(M.slots(pet)).get(slot, "07:00")
            entries.append({"time": "{} {}".format(key.split(":")[0], hm), "pet": pid, "slot": slot,
                            "what": "{} g {}".format(int(M.portion_g(pet)), kind.replace("_", " ")), "by": who, "kind": "meal"})
            logged.append("{}:{}".format(pid, slot))
        else:
            given[key] = 0
        pet["meals_given"] = given
        pet["hunger"] = 0.85 if branch == "due" else 0.2
        out["portion_" + pid] = M.portion_g(pet)
        out["tol_" + pid] = round(0.05 * M.portion_g(pet), 2)
        out["tol10_" + pid] = round(0.10 * M.portion_g(pet), 2)
        hidden = pet.get("_diet") or {}
        if hidden.get("wet_g") is not None:
            out["wet_" + pid] = float(hidden["wet_g"])
            out["wettol_" + pid] = round(0.05 * float(hidden["wet_g"]), 2)
            net = C.num(_ent(ctx, out["can_" + pid]).get("remaining_g"), 85.0) if out.get("can_" + pid) else 85.0
            out["wetcans_" + pid] = round(float(hidden["wet_g"]) / net, 3)
        if hidden.get("dry_g") is not None:
            out["dry_" + pid] = float(hidden["dry_g"])
            out["drytol_" + pid] = round(0.05 * float(hidden["dry_g"]), 2)
        bag = _ent(ctx, out["food_" + pid])
        guide = {"dog_kibble": {"small": 160, "medium": 270, "large": 370}, "cat_kibble": {"small": 60}}.get(kind, {})
        out["bagguide_" + pid] = round(guide.get(C.size_of(pet), guide.get("small", M.portion_g(pet) * 2.4)) / 2.0, 1)
    if log:
        from .feeding import render_log
        logid = _on(ctx, counter, "pet_care_log", entries=entries, logged=logged)
        _ent(ctx, logid)["text"] = render_log(ctx.config, entries)
        out["log"] = logid
    if treats:
        out["treat_jar"] = _on(ctx, counter, "pet_treat_jar")
    if note:
        lines = []
        for pid in pets:
            pet = ctx.config["pets"][pid]
            hidden = pet.get("_diet") or {}
            part = "{}: {} g of {} per meal".format(pet.get("label", pid), int(M.portion_g(pet)), pet["diet"]["foods"][0].replace("_", " "))
            if hidden.get("wet_g"):
                part += " ({} g wet + {} g dry)".format(int(hidden["wet_g"]), int(hidden.get("dry_g", 0)))
            lines.append(part)
        out["note"] = _on(ctx, counter, "pet_care_note", label="vet feeding instructions",
                          text="Feeding (vet instructions, ignore the bag guide): " + "; ".join(lines) + ". Meals at 07:00 and 18:00.")
    return out


@scenario_helper("pets.water")
def h_water(ctx, pet, kind="bowl", state="stale", room="kitchen", near_litter=False, outdoor=False):
    """state: fresh | low | stale (age 2 days, slime) | dirty (food/hair floating) | empty. kind: bowl | fountain | bottle."""
    rec = ctx.config["pets"][pet]
    sp = C.species(rec)
    rid = _need_room(ctx, "backyard" if outdoor else room, ("kitchen", "laundry_room", "living_room"))
    out = {"water_room": rid}
    if kind == "fountain":
        surface = _place_floor(ctx, rid, "feeding_mat", near=ctx.placer.wall_cell(rid), surfaces=False, owner=pet)
        eid = _on(ctx, surface, "pet_water_fountain", owner=pet, label="{}'s water fountain".format(rec.get("label", pet)))
        filt = ctx.placer.make("fountain_filter", eid, installed_s=-40 * 86400.0 if state == "stale" else -5 * 86400.0)
        ctx.config["entities"][filt]["relation"] = "part"
        ctx.config["entities"][filt]["slot"] = "filter"
        ctx.config["entities"][eid]["slots"]["filter"]["item"] = filt
        out["filter"] = filt
    else:
        type_name = "outdoor_water_bowl" if outdoor else ("dog_water_bowl" if sp == "dog" else "cat_water_bowl")
        eid = _place_floor(ctx, rid, type_name, near=ctx.placer.wall_cell(rid), surfaces=False, owner=pet,
                           label="{}'s water bowl".format(rec.get("label", pet)))
    w = _ent(ctx, eid)
    cap = C.num(w.get("capacity_l"), 1.0)
    level = {"fresh": 0.8, "low": 0.15, "stale": 0.5, "dirty": 0.6, "empty": 0.0}.get(state, 0.5)
    if kind == "fountain":
        level = {"fresh": 1.4, "low": 0.5, "stale": 1.0, "dirty": 1.0, "empty": 0.2}.get(state, 1.0) / cap
    w["liquid_l"] = round(cap * level, 3)
    w["_last_l"] = w["liquid_l"]
    w["_water_age_s"] = {"fresh": 3600.0, "low": 20 * 3600.0, "stale": 50 * 3600.0, "dirty": 10 * 3600.0, "empty": 0.0}.get(state, 3600.0)
    w["biofilm"] = {"stale": 0.55, "dirty": 0.2}.get(state, 0.02)
    w["debris"] = {"dirty": 0.6, "stale": 0.15}.get(state, 0.0)
    if near_litter:
        box = _place_floor(ctx, rid, "litter_box", near=w["pos"], surfaces=False, owner=pet)
        out["litter_box"] = box
    rec.setdefault("needs", {})["water"] = eid
    out["water"] = eid
    out["sink"] = stand_in(ctx, "sink", _need_room(ctx, "kitchen", ("laundry_room", "bathroom")))
    return out


# ----------------------------------------------------------------------
# litter and waste

def _litter_text(kind):
    """The printed label of a litter bag (realistic perception: litter bags look alike until the label is read)."""
    words = kind.replace("_", " ")
    return "Cat litter: {}. Keep dry; top up to 5-7 cm.".format(words[:1].upper() + words[1:])


@scenario_helper("pets.litter")
def h_litter(ctx, pet=None, room=None, clumps=4, feces=1, buried=2, abnormal=None, depth_cm=5.5, kind="clumping_clay_unscented",
             spare_kind=None, bins=True, toilet=True, roll=True, trash_bag=False, wet=False, residue=None, washroom=None):
    kinds = ("laundry_room", "bathroom", "half_bath", "basement", "utility_closet", "hallway", "kitchen")
    rid = _need_room(ctx, room, kinds)
    if not room or room not in ctx.config["rooms"]:
        # the first room of the usual kinds with reachable floor space for the box (a laundry room can be wall to
        # wall machines and baskets)
        box_rec = ctx.reg.make("litter_box", rid)
        for other in _room_candidates(ctx, room, kinds):
            if _floor_spot(ctx, other, box_rec, near=ctx.placer.wall_cell(other)) is not None:
                rid = other
                break
    near = ctx.placer.wall_cell(rid)
    box = _place_floor(ctx, rid, "litter_box", near=near, surfaces=False, owner=pet, litter_kind=kind, clumps=clumps, feces=feces, _buried=buried,
                       _abnormal=dict(abnormal or {}), wet=1.0 if wet else 0.0, residue=dict(residue or {}))
    rec = _ent(ctx, box)
    from .litter import kg_for_depth
    rec["litter_kg"] = round(kg_for_depth(rec, depth_cm), 3)
    rec["litter_mix"] = [kind]
    rid = rec["location"]
    rec["home"] = rid
    rec["home_pos"] = list(rec["pos"])
    out = {"box": box, "litter_room": rid}
    if pet:
        ctx.config["pets"][pet].setdefault("needs", {})["litter"] = box
        ctx.config["pets"][pet]["_litter_pref"] = kind
    surface = _surface_in(ctx, rid) or _furniture(ctx, rid, "shelf_unit", label="shelf")
    if surface is None:
        try:
            surface = _surface_or_new(ctx, rid)
        except SpecError:
            surface = None              # a crowded laundry room: supplies go on the floor next to the box
    if surface is None:
        put = (lambda t, **p: None)
    elif _ent(ctx, surface).get("container"):
        put = (lambda t, **p: ctx.placer.inside(surface, t, shelf=0, **p))
    else:
        put = (lambda t, **p: ctx.placer.on(surface, t, **p))
    out["scoop"] = put("litter_scoop") or _place_floor(ctx, rid, "litter_scoop", near=rec["pos"])
    out["litter_bag"] = put("cat_litter_bag", litter_kind=kind, label="bag of {}".format(kind.replace("_", " ")), label_text=_litter_text(kind)) or \
        _place_floor(ctx, rid, "cat_litter_bag", litter_kind=kind, label="bag of {}".format(kind.replace("_", " ")), label_text=_litter_text(kind))
    if spare_kind:
        out["other_litter"] = put("cat_litter_bag", litter_kind=spare_kind, label="bag of {}".format(spare_kind.replace("_", " ")),
                                  label_text=_litter_text(spare_kind)) or \
            _place_floor(ctx, rid, "cat_litter_bag", litter_kind=spare_kind, label="bag of {}".format(spare_kind.replace("_", " ")),
                         label_text=_litter_text(spare_kind))
    if roll:
        out["roll"] = put("poop_bag_roll") or _place_floor(ctx, rid, "poop_bag_roll")
    if trash_bag:
        out["trash_bag"] = stand_in(ctx, "trash_bag", rid)
    if bins:
        yard = _room(ctx, None, ("garage", "backyard", "driveway", "patio", "side_yard"))
        if yard:
            out["outdoor_trash"] = stand_in(ctx, "outdoor_trash", yard)
            out["recycling"] = stand_in(ctx, "recycling_bin", yard)
            out["bin_room"] = yard
        kitchen = _room(ctx, "kitchen")
        if kitchen:
            out["kitchen_trash"] = stand_in(ctx, "trash_can", kitchen)
    if toilet:
        bath = _room(ctx, None, ("bathroom", "half_bath"))
        if bath:
            out["toilet"] = stand_in(ctx, "toilet", bath)
    if washroom:
        wash = _need_room(ctx, washroom, ("laundry_room", "bathroom", "kitchen"))
        out["sink"] = stand_in(ctx, "sink", wash)
        out["wash_room"] = wash
        shelf = _surface_or_new(ctx, wash)
        out["soap"] = cleaner(ctx, shelf, "mild_soap")
        out["ammonia"] = cleaner(ctx, shelf, "ammonia")
        out["bleach"] = cleaner(ctx, shelf, "bleach")
        out["towel"] = stand_in(ctx, "towel", wash)
    return out


@scenario_helper("pets.yard_waste")
def h_yard_waste(ctx, room="backyard", visible=2, concealed=2, wet=False, compost=True, soft=0):
    rid = _need_room(ctx, room, ("backyard", "front_yard", "garden"))
    cols, rows = geometry.grid_size(ctx.config["rooms"][rid])
    piles = []
    state = ctx.placer.state()
    taken = set()
    for i in range(visible + concealed):
        hidden = i >= visible
        for _ in range(40):
            if hidden:
                edge = ctx.rng.choice(["top", "bottom", "left", "right"])
                x = ctx.rng.randrange(cols) if edge in ("top", "bottom") else (0 if edge == "left" else cols - 1)
                y = ctx.rng.randrange(rows) if edge in ("left", "right") else (0 if edge == "top" else rows - 1)
            else:
                x, y = ctx.rng.randrange(1, max(2, cols - 1)), ctx.rng.randrange(1, max(2, rows - 1))
            cell = (x, y)
            if cell in taken or cell in geometry.blocked_cells(state, rid):
                continue
            if any(geometry.chebyshev(tuple(c["cells"].get(rid, (-9, -9))), cell) <= 2 for c in ctx.config.get("connections", {}).values()):
                continue
            break
        taken.add(cell)
        props = {"label": "dog poop", "laid_s": -3600.0 * (i + 1), "soft": i < soft}
        if hidden:
            props.update(concealed=True, revealed=False, floor_hazard=None, _floor_hazard="waste", label="dog poop (in tall grass)")
        eid = ctx.placer.make("dog_waste", rid, **props)
        ctx.config["entities"][eid]["pos"] = [cell[0], cell[1]]
        piles.append(eid)
    if wet:
        ctx.config["rooms"][rid]["muddy"] = True
    out = {"piles": piles, "yard": rid, "pile_count": len(piles)}
    for i, eid in enumerate(piles, 1):
        out["pile_{}".format(i)] = eid
    entry = _room(ctx, None, ("garage", "mudroom", "entryway", "patio", "porch")) or rid
    surface = _surface_in(ctx, entry) or _surface_or_new(ctx, entry)
    out["roll"] = _on(ctx, surface, "poop_bag_roll", count=20)
    bins_room = _room(ctx, None, ("driveway", "garage", "side_yard")) or rid
    out["outdoor_trash"] = stand_in(ctx, "outdoor_trash", bins_room)
    out["recycling"] = stand_in(ctx, "recycling_bin", bins_room)
    if compost:
        out["compost"] = stand_in(ctx, "compost_bin", rid if rid in ctx.config["rooms"] else bins_room)
    out["bin_room"] = bins_room
    return out


# ----------------------------------------------------------------------
# toys and hazards

@scenario_helper("pets.toys")
def h_toys(ctx, pet, room=None, pet_toys=3, child_toys=2, damaged=1, in_play=False, under=None, string_out=False):
    record = ctx.config["pets"][pet]
    rid = _need_room(ctx, room or record["location"], ("living_room",))
    out = {"toy_room": rid}
    kinds = ["fetch_ball", "rope_toy", "squeaky_toy", "chew_toy", "plush_dog_toy"] if C.species(record) == "dog" else ["catnip_mouse", "catnip_mouse"]
    toys = []
    for i in range(pet_toys):
        toys.append(_place_floor(ctx, rid, kinds[i % len(kinds)], surfaces=False, pet_toy_of=pet))
    damaged_ids = []
    for i in range(damaged):
        t = _place_floor(ctx, rid, "squeaky_toy" if i == 0 else "plush_dog_toy", surfaces=False, damaged=True,
                         damage={"exposed_squeaker": 0.7} if i == 0 else {"torn_plush": 0.6}, hazards=["choking"], toxin="small_part",
                         label="torn squeaky toy (squeaker showing)" if i == 0 else "torn plush toy (stuffing out)", pet_toy_of=pet)
        damaged_ids.append(t)
    childs = []
    for i in range(child_toys):
        props = {"label": ["stuffed bunny", "toy truck", "doll"][i % 3], "category": "toy", "toy_owner": "child", "owner": "child_1",
                 "tags": ["toy"], "size_m": [0.2, 0.12, 0.15], "mass_kg": 0.2, "volume_l": 1.0}
        childs.append(_place_floor(ctx, rid, "toy" if ctx.reg.known("toy") else "generic_item", surfaces=False, **props))
    basket = _place_floor(ctx, rid, "pet_toy_basket", near=ctx.placer.wall_cell(rid), surfaces=False)
    out.update(toys=toys, child_toys=childs, damaged=damaged_ids, basket=basket, toy_count=len(toys))
    for i, t in enumerate(toys, 1):
        out["toy_{}".format(i)] = t
    for i, t in enumerate(childs, 1):
        out["child_toy_{}".format(i)] = t
    for i, t in enumerate(damaged_ids, 1):
        out["damaged_{}".format(i)] = t
    if in_play and toys:
        playing = toys[0]
        rec = _ent(ctx, playing)
        rec["location"], rec["relation"] = pet, "held"
        rec.pop("pos", None)
        record["playing_with"] = playing
        record["activity"] = "playing"
        record["awake"] = True
        out["in_play"] = playing
        out["toys_free"] = toys[1:]
    else:
        out["toys_free"] = list(toys)
    if under:
        sofa = (ctx.placer.find("sofa", room=rid) or ctx.placer.find("armchair", room=rid) or [None])[0]
        if sofa and out["toys_free"]:
            moved = out["toys_free"][-1]
            rec = _ent(ctx, moved)
            rec["location"], rec["relation"] = sofa, "under"
            rec.pop("pos", None)
            out["under_toy"] = moved
            out["sofa"] = sofa
    if string_out:
        out["wand"] = _place_floor(ctx, rid, "cat_wand_toy", surfaces=False)
    return out


HAZARD_ITEMS = {
    "chocolate": {"label": "chocolate bar", "food": {"kind": "chocolate", "state": "raw", "quantity": 100, "unit": "g"}, "toxin": "chocolate",
                  "wrapped": True, "size_m": [0.16, 0.08, 0.015], "mass_kg": 0.1, "volume_l": 0.2, "category": "food"},
    "grapes": {"label": "bunch of grapes", "food": {"kind": "grapes", "state": "raw", "quantity": 300, "unit": "g"}, "toxin": "grapes",
               "size_m": [0.2, 0.12, 0.08], "mass_kg": 0.3, "volume_l": 0.6, "category": "food"},
    "gum": {"label": "pack of sugar-free gum (xylitol)", "food": {"kind": "gum", "state": "raw", "quantity": 10, "unit": "count"}, "toxin": "xylitol",
            "wrapped": True, "size_m": [0.08, 0.03, 0.02], "mass_kg": 0.03, "volume_l": 0.05, "category": "food"},
    "ibuprofen": {"label": "bottle of ibuprofen", "toxin": "nsaid_human", "size_m": [0.05, 0.05, 0.1], "mass_kg": 0.08, "volume_l": 0.15,
                  "category": "medicine", "hazards": ["toxic"], "safe_grips": ["normal"]},
    "onions": {"label": "bag of onions", "food": {"kind": "onion", "state": "raw", "quantity": 1000, "unit": "g"}, "toxin": "onion",
               "size_m": [0.25, 0.2, 0.12], "mass_kg": 1.0, "volume_l": 2.0, "category": "food"},
    "sock": {"label": "sock", "toxin": "sock", "size_m": [0.1, 0.08, 0.02], "mass_kg": 0.04, "volume_l": 0.1, "category": "clothing",
             "chewable": True, "pet_appeal": {"dog": 0.4}, "swallowable": True},
    "charger": {"label": "phone charger cable", "category": "cord", "chewable": True, "pet_appeal": {"dog": 0.25, "cat": 0.2},
                "size_m": [0.1, 0.05, 0.02], "mass_kg": 0.05, "volume_l": 0.05},
    "lily": {"label": "vase of lilies", "toxin": "lily", "size_m": [0.15, 0.15, 0.5], "mass_kg": 1.2, "volume_l": 2.0, "category": "decor", "fragile": True},
}


def _tidy_food(ctx, keep=()):
    """Put away any other loose food / pet-toxic items lying around (closed storage), so the scenario's items are the only ones."""
    state = ctx.placer.state()
    for eid in sorted(ctx.config["entities"]):
        rec = ctx.config["entities"][eid]
        if eid in keep or not rec.get("portable"):
            continue
        if not (C.is_edible(rec) or C.toxin_of(rec)) or rec.get("pet_food"):
            continue
        location, closed = rec.get("location"), False
        while location in ctx.config["entities"]:
            parent = ctx.config["entities"][location]
            if parent.get("openable") and not parent.get("open") and not parent.get("transparent"):
                closed = True
            location = parent.get("location")
        if not closed:
            stack = [eid]
            doomed = []
            while stack:
                cur = stack.pop()
                doomed.append(cur)
                stack.extend(e for e, r in ctx.config["entities"].items() if r.get("location") == cur)
            for d in doomed:
                ctx.config["entities"].pop(d, None)


@scenario_helper("pets.hazards")
def h_hazards(ctx, pet, items=("chocolate",), room="kitchen", on=None, spos_edge=True, cabinet=True, high_shelf=True, tidy=True):
    if tidy:
        _tidy_food(ctx)
    rid = _need_room(ctx, room, ("kitchen", "living_room"))
    surface = on or _surface_in(ctx, rid, ("counter", "coffee_table", "table", "dining_table", "side_table")) or _surface_or_new(ctx, rid)
    out = {"hazard_room": rid, "hazard_surface": surface}
    ids = []
    for i, kind in enumerate(items):
        props = copy.deepcopy(HAZARD_ITEMS[kind])
        eid = _on(ctx, surface, "generic_item", **props)
        ids.append(eid)
        out["hazard_{}".format(i + 1)] = eid
        out[kind] = eid
    out["hazards"] = ids
    if cabinet:
        cab = _storage_in(ctx, rid)
        if cab is None or not _ent(ctx, cab).get("openable"):
            cab = _furniture_sure(ctx, rid, "cabinet", label="cabinet")
        out["cabinet"] = cab
    if high_shelf:
        shelf = ctx.placer.mount(rid, "upper_cabinet", height=1.5, label="high cabinet")
        out["high_cabinet"] = shelf
    return out


# ----------------------------------------------------------------------
# habitats

@scenario_helper("pets.aquarium")
def h_aquarium(ctx, room=None, double_fed=False, light_on=True, desired_light=None, bottom_feeders=True, water_change=False,
               dirty_filter=False, chlorine=False, log=True, at=None):
    stand, rid = _furniture_any(ctx, room, ("living_room", "office", "bedroom", "master_bedroom"), "table", "aquarium stand",
                                label="aquarium stand", size_m=[0.8, 0.4, 0.75], surface_height_m=0.75, surface_size_m=[0.8, 0.4])
    stock = [{"species": "neon tetra", "count": 10, "level": "mid", "food": "flakes", "ration_g": 0.004, "mass_g": 0.5, "health": 1.0},
             {"species": "guppy", "count": 4, "level": "surface", "food": "flakes", "ration_g": 0.006, "mass_g": 0.8, "health": 1.0}]
    if bottom_feeders:
        stock.append({"species": "corydoras", "count": 4, "level": "bottom", "food": "sinking", "ration_g": 0.012, "mass_g": 3.0, "health": 1.0})
    tank = _on(ctx, stand, "aquarium", stock=stock, feeding_times={"morning": "08:00"}, portable=False)
    _ent(ctx, tank)["spos"] = [0.02, 0.04]
    out = {"tank": tank, "stand": stand, "tank_room": rid}
    state = _state(ctx)
    key, _slot, _start = M.current_slot(state, {"diet": {"meals": {"morning": "08:00"}}})
    out["fish_slot"] = key
    rec = _ent(ctx, tank)
    rec["fed_slots"] = {key: 1 if double_fed else 0}
    heater = ctx.placer.make("aquarium_heater", tank, serves=tank)
    filt = ctx.placer.make("aquarium_filter", tank, serves=tank)
    for eid in (heater, filt):
        ctx.config["entities"][eid]["relation"] = "in"
    for slot, part_type in (("sponge", "filter_sponge"), ("bio_media", "bio_media"), ("cartridge", "filter_cartridge")):
        part = ctx.placer.make(part_type, filt, clog=0.8 if dirty_filter else 0.3)
        ctx.config["entities"][part].update(relation="part", slot=slot)
        ctx.config["entities"][filt]["slots"][slot]["item"] = part
        out[slot] = part
    if ctx.reg.known("aquarium_light"):
        light = ctx.placer.make("aquarium_light", tank, serves=tank, on=light_on, purpose="aquarium", plugged_in=True)
    else:
        light = ctx.placer.make("lamp", tank, label="aquarium light", purpose="aquarium", serves=tank, on=light_on, brightness=0.15,
                                size_m=[0.6, 0.1, 0.03], fragile=False, aquarium_device="light")
    ctx.config["entities"][light]["relation"] = "on"
    ctx.config["entities"][light]["spos"] = [0.0, 0.0]
    rec["_desired_light"] = desired_light
    strip = stand_in(ctx, "power_strip", rid, label="aquarium power strip")
    for eid in (heater, filt, light):
        ctx.config["entities"][eid]["outlet"] = strip
    out.update(heater=heater, filter=filt, light=light, power_strip=strip)
    shelf = _surface_in(ctx, rid, ("side_table", "tv_stand", "desk", "coffee_table", "table")) or _surface_or_new(ctx, rid)
    if shelf == stand:
        shelf = _surface_or_new(ctx, rid)
    out["flakes"] = _on(ctx, shelf, "fish_food")
    if bottom_feeders:
        out["wafers"] = _on(ctx, shelf, "sinking_pellets")
    out["conditioner"] = ctx.placer.on(shelf, "water_conditioner") or _place_floor(ctx, rid, "water_conditioner")
    out["net"] = ctx.placer.on(shelf, "fish_net") or _place_floor(ctx, rid, "fish_net")
    out["siphon"] = _place_floor(ctx, rid, "gravel_siphon")
    out["strips"] = ctx.placer.on(shelf, "aquarium_test_strips") or _place_floor(ctx, rid, "aquarium_test_strips")
    import math as _m
    upper = sum(g["count"] * g["ration_g"] for g in stock if g["level"] != "bottom")
    lower = sum(g["count"] * g["ration_g"] for g in stock if g["level"] == "bottom")
    out["ration_total"] = round(upper + lower, 4)
    out["ration_lo"] = round(0.8 * (upper + lower), 4)
    out["ration_hi"] = round(1.6 * (upper + lower), 4)
    out["flake_pinches"] = int(_m.ceil(upper / 0.04))
    out["wafer_pinches"] = int(_m.ceil(lower / 0.06)) if lower else 0
    out["bucket"] = stand_in(ctx, "bucket", rid)
    if water_change or chlorine:
        out["sink"] = stand_in(ctx, "sink", _need_room(ctx, "kitchen", ("bathroom", "laundry_room")))
    if chlorine:
        rec["water"]["chlorine_ppm"] = 0.0
    if log:
        from .feeding import render_log
        entries = [{"time": "{} 08:00".format(key.split(":")[0]), "pet": tank, "slot": "morning", "what": "1 pinch flakes", "by": "Sam", "kind": "fish"}] if double_fed else []
        surface = _surface_or_new(ctx, rid)
        logid = _on(ctx, surface, "pet_care_log", label="aquarium feeding log", entries=entries, logged=["{}:morning".format(tank)] if double_fed else [])
        _ent(ctx, logid)["text"] = render_log(ctx.config, entries)
        out["log"] = logid
    return out


@scenario_helper("pets.terrarium")
def h_terrarium(ctx, room=None, burnt=False, room_temp_c=None, open_lid=False):
    stand, rid = _furniture_any(ctx, room, ("office", "bedroom", "master_bedroom", "living_room"), "table", "terrarium stand",
                                label="terrarium stand", size_m=[1.25, 0.65, 0.75], surface_size_m=[1.25, 0.65])
    enc = _on(ctx, stand, "reptile_enclosure", open=open_lid, latched=not open_lid, portable=False)
    lamp = ctx.placer.make("basking_lamp", enc, serves=enc, burnt_out=burnt)
    che = ctx.placer.make("ceramic_heat_emitter", enc, serves=enc)
    therm = ctx.placer.make("terrarium_thermometer", enc, serves=enc)
    for eid in (lamp, che, therm):
        ctx.config["entities"][eid]["relation"] = "in"
    if room_temp_c is not None:
        ctx.config["rooms"][rid]["temperature_c"] = float(room_temp_c)
    ctx.placer.c.setdefault("pets", {})
    pid = "dragon_1"
    h_pet(ctx, pid, species="reptile", room=rid, label="Spike", props={"enclosure": enc, "breed": "bearded dragon"})
    ctx.config["pets"][pid]["pos"] = list(_ent(ctx, stand)["pos"])
    _ent(ctx, enc)["occupant"] = pid
    shelf = _surface_or_new(ctx, rid)
    if shelf == stand:
        shelf = _furniture(ctx, rid, "side_table") or stand
    gun = _on(ctx, shelf, "ir_thermometer")
    room_t = C.num(ctx.config["rooms"][rid].get("temperature_c"), 21.0)
    rise = 4.0 if burnt else 22.0
    warm = max(room_t + (0.0 if burnt else 10.0), min(31.0, room_t + 12.0))
    zones = {"basking": {"surface_c": room_t + rise, "air_c": warm + 3}, "warm": {"surface_c": warm + 1.5, "air_c": warm},
             "cool": {"surface_c": room_t + 0.5 + 0.1 * (warm - room_t), "air_c": room_t + 2 + 0.15 * (warm - room_t)}}
    _ent(ctx, enc)["zones"] = zones
    ranges = _ent(ctx, enc)["species_ranges"]
    out_range = sorted(name for name, (zone, key) in (("basking_surface", ("basking", "surface_c")), ("warm_air", ("warm", "air_c")),
                                                       ("cool_air", ("cool", "air_c")))
                       if not (ranges[name][0] - 0.5 <= zones[zone][key] <= ranges[name][1] + 0.5))
    return {"enclosure": enc, "lamp": lamp, "heat_emitter": che, "thermometer": therm, "ir_gun": gun, "reptile": pid, "terrarium_room": rid,
            "exp_basking": round(zones["basking"]["surface_c"], 2), "exp_warm_air": round(zones["warm"]["air_c"], 2),
            "exp_cool_air": round(zones["cool"]["air_c"], 2), "exp_out_of_range": out_range}


@scenario_helper("pets.bird")
def h_bird(ctx, room=None, hulls=True, fan_on=False, window_open=False, liner_soiled=True, flighty=0.6, seed_g=None):
    cage, rid = _furniture_any(ctx, room, ("living_room", "dining_room", "office", "bedroom", "master_bedroom"), "bird_cage", "bird cage")
    pid = "bird_1"
    h_pet(ctx, pid, species="bird", room=rid, label="Kiwi", behaviors={"door_dash": flighty}, props={"enclosure": cage, "breed": "cockatiel"})
    ctx.config["pets"][pid]["pos"] = list(_ent(ctx, cage)["pos"])
    _ent(ctx, cage)["occupant"] = pid
    seed = ctx.placer.inside(cage, "bird_seed_dish", owner=pid)
    if hulls:
        _ent(ctx, seed)["food_g"] = {"bird_seed": 2.0}
        _ent(ctx, seed)["hull_g"] = 9.0
    else:
        _ent(ctx, seed)["food_g"] = {"bird_seed": C.num(seed_g, 20.0)}
    _ent(ctx, seed)["food_meta"] = {"bird_seed": {"wet": False, "served_s": -86400.0, "density": 0.6}}
    water = ctx.placer.inside(cage, "bird_water_dish", owner=pid, liquid_l=0.04, droppings=0.4, debris=0.3, _water_age_s=30 * 3600.0, biofilm=0.2)
    _ent(ctx, water)["_last_l"] = 0.04
    tray = ctx.placer.make("cage_tray", cage)
    ctx.config["entities"][tray]["relation"] = "part"
    liner = ctx.placer.make("cage_liner", tray, soil={"bird_droppings": 0.7, "crumbs": 0.5} if liner_soiled else {})
    ctx.config["entities"][liner].update(relation="part", slot="liner")
    ctx.config["entities"][tray]["slots"]["liner"]["item"] = liner
    shelf = _surface_or_new(ctx, rid)
    out = {"cage": cage, "bird": pid, "seed_dish": seed, "water_dish": water, "tray": tray, "old_liner": liner, "bird_room": rid}
    out["seed_bag"] = _on(ctx, shelf, "bird_seed")
    out["new_liner"] = _on(ctx, shelf, "cage_liner")
    out["scented_liner"] = _on(ctx, shelf, "scented_cage_liner")
    out["trash"] = stand_in(ctx, "trash_can", _need_room(ctx, "kitchen", (rid,)))
    if fan_on:
        out["fan"] = stand_in(ctx, "ceiling_fan", rid, on=True)
    windows = [e for e in ctx.placer.find("window", room=rid)]
    if windows:
        out["window"] = windows[0]
        if window_open:
            _ent(ctx, windows[0])["open"] = True
            _ent(ctx, windows[0])["locked"] = False
            _ent(ctx, windows[0])["has_screen"] = False
    return out


@scenario_helper("pets.small_animal")
def h_small(ctx, room=None, species="hamster", soiled=0.8, stash_g=12.0, kind="paper"):
    cage, rid = _furniture_any(ctx, room, ("bedroom", "master_bedroom", "living_room", "office"), "small_animal_cage", "small-animal cage")
    pid = "hamster_1" if species == "hamster" else species + "_1"
    h_pet(ctx, pid, species=species, room=rid, label="Peanut", props={"enclosure": cage})
    ctx.config["pets"][pid]["pos"] = list(_ent(ctx, cage)["pos"])
    rec = _ent(ctx, cage)
    rec["occupant"] = pid
    rec["substrate"] = {"kind": kind, "depth_cm": 15.0, "soiled": soiled, "nest_g": 40.0}
    rec["_stash_g"] = stash_g
    shelf = _surface_or_new(ctx, rid)
    out = {"cage": cage, "animal": pid, "small_room": rid}
    out["paper_bedding"] = _on(ctx, shelf, "bedding_bag")
    out["cedar_bedding"] = _on(ctx, shelf, "bedding_bag", label="cedar shavings (aromatic)", bedding_kind="cedar",
                               label_text="Cedar shavings - natural aromatic wood bedding. 56 l expanded.")
    out["carrier"] = _place_floor(ctx, rid, "pet_carrier", surfaces=False, label="small animal carrier", crate_size="small")
    out["trash_bag"] = stand_in(ctx, "trash_bag", rid)
    out["outdoor_trash"] = stand_in(ctx, "outdoor_trash", _need_room(ctx, None, ("garage", "driveway", "backyard", "kitchen")))
    return out


@scenario_helper("pets.coop")
def h_coop(ctx, room="backyard", eggs=3, cracked=0, broody=False, predator_at_s=None, door_open=True, hens=4):
    coop, rid = _furniture_any(ctx, room, ("backyard", "garden"), "chicken_coop", "chicken coop", indoor_any=False,
                               hens=hens, open=door_open, _predator_s=predator_at_s)
    box = ctx.placer.make("nest_box", coop, broody=broody)
    ctx.config["entities"][box]["relation"] = "in"
    egg_ids = []
    egg_type = "hen_egg" if ctx.reg.known("hen_egg") else "coop_egg"
    for i in range(eggs):
        props = {"laid_s": -3600.0 * (i + 1), "cracked": i < cracked, "category": "egg"}
        eid = ctx.placer.make(egg_type, box, **props)
        ctx.config["entities"][eid]["relation"] = "in"
        egg_ids.append(eid)
    waterer = ctx.placer.make("coop_waterer", coop, liquid_l=1.0, _water_age_s=40 * 3600.0, biofilm=0.3)
    ctx.config["entities"][waterer]["relation"] = "in"
    entry = _room(ctx, None, ("mudroom", "garage", "kitchen", "entryway")) or rid
    out = {"coop": coop, "nest_box": box, "eggs": egg_ids, "egg_count": eggs, "good_eggs": eggs - cracked, "waterer": waterer, "coop_room": rid}
    out["basket"] = _on(ctx, _surface_or_new(ctx, entry), "egg_basket")
    return out


# ----------------------------------------------------------------------
# confinement, health, medication

@scenario_helper("pets.trapped")
def h_trapped(ctx, pet, where="closet", deliberate=False, reason="resting after surgery - keep the door shut", appliance=None, note=None):
    """A pet shut in a room (closet / bedroom / garage) or an appliance. deliberate=True: an owner confinement with a
    'note on the door' on a surface outside. note="text": a note with that text outside the door in either case (for
    realistic-perception tasks the robot must read it: an unrelated note looks the same at a glance)."""
    record = ctx.config["pets"][pet]
    out = {}
    if where == "appliance":
        if appliance is None:
            kinds = ("laundry_room", "kitchen", "garage", "basement", "utility_closet", "mudroom", "bathroom")
            try:
                dryer, rid = _furniture_any(ctx, None, kinds, "openable_storage", "clothes dryer", indoor_any=False,
                                            label="clothes dryer", category="appliance", size_m=[0.7, 0.7, 0.9],
                                            inner_size_m=[0.6, 0.55, 0.6], capacity_l=120.0, open=False,
                                            appliance={"programs": {"normal": {"duration_s": 3600}}, "program": "normal", "state": "idle",
                                                       "requires_power": False})
            except SpecError:
                # no floor space for one more machine: the cat crawled into the household's own dryer or washer
                dryer, rid = None, None
                for cand in _room_candidates(ctx, None, kinds):
                    for eid in geometry.room_entities(ctx.placer.state(), cand):
                        rec = _ent(ctx, eid)
                        words = " ".join(str(x) for x in [rec.get("type"), rec.get("category")] + list(rec.get("is_a") or []))
                        if ("dryer" in words or "washer" in words or "washing_machine" in words) and rec.get("openable")                                 and rec.get("container") and _reach_ok(ctx, eid):
                            dryer, rid = eid, cand
                            break
                    if dryer:
                        break
                if dryer is None:
                    raise
                _ent(ctx, dryer)["open"] = False
        else:
            dryer = appliance
            rid = root_room(ctx.placer.state(), dryer)
        record["location"] = rid
        record["pos"] = list(_ent(ctx, dryer)["pos"])
        record["inside"] = dryer
        record["trapped"] = True
        out.update(appliance=dryer, trap_room=rid)
        return out
    kinds = {"closet": ("coat_closet", "linen_closet", "utility_closet", "pantry"),
             "bedroom": ("bedroom", "guest_room", "office", "master_bedroom", "craft_room", "playroom"),
             "garage": ("garage", "shed")}[where]
    rid, door = None, None
    for cand in _room_candidates(ctx, None, kinds):
        door = next((c.get("door") for c in sorted(ctx.config["connections"].values(), key=lambda c: str(c.get("door")))
                     if cand in c["rooms"] and c.get("door") and geometry.door_swing(ctx.placer.state(), c["door"])[0] is not None), None)
        if door is not None:
            rid = cand
            break
    if rid is None:
        raise SpecError("pets.trapped: no {} with a door ({})".format(where, list(kinds)))
    _ent(ctx, door)["open"] = False
    record["location"] = rid
    record["pos"] = _agent_cell(ctx, rid, exclude=(pet,))
    record["trapped"] = True
    other = [r for r in _ent(ctx, door)["connects"] if r != rid][0]
    out["outside_room"] = other
    text = note
    if deliberate and text is None:
        text = "{} is {}. Do NOT let {} out until tomorrow.".format(record.get("label", pet), reason.split(" - ")[0], record.get("label", pet))
    if text:
        surface = _surface_in(ctx, other)
        if surface:
            out["note"] = _on(ctx, surface, "pet_care_note", label="note on the door", text=text)
        else:
            # no table or shelf outside (a bare hallway): the note lies by the door (or on a surface one room away)
            out["note"] = _place_floor(ctx, other, "pet_care_note", label="note on the door", text=text)
    if deliberate:
        record["confinement"] = {"intent": "deliberate", "reason": reason, "where": rid}
        # a spill-proof steel bowl for a pet shut in a small room (a ceramic one on the floor of a closet is a breakage
        # hazard the robot would have to step around)
        water = _place_floor(ctx, rid, "cat_water_bowl" if C.species(record) == "cat" else "dog_water_bowl", surfaces=False, owner=pet,
                             liquid_l=0.4, material="stainless_steel", fragile=False)
        _ent(ctx, water)["_last_l"] = 0.4
        out["confined_water"] = water
    out.update(trap_room=rid, trap_door=door)
    return out


@scenario_helper("pets.health_case")
def h_health(ctx, pet, case="urgent_toxin", evidence=True):
    """case: urgent_toxin (chocolate eaten 3 h ago: vomiting, restless, rapid breathing; wrapper on the floor),
    limp (front-left limp from a burr in the paw), lethargic (mild: off food), normal (sleeping)."""
    record = ctx.config["pets"][pet]
    rid = record["location"]
    out = {"case": case}
    conds = []
    if case == "urgent_toxin":
        conds.append({"kind": "toxin", "toxin": "chocolate", "severity": "severe", "since_s": -3 * 3600, "onset_s": -600,
                      "signs": ["vomiting", "restlessness", "rapid_breathing"], "urgency": "urgent"})
        record["activity"] = "pacing"
        record["posture"] = "standing"
        if evidence:
            out["evidence"] = _place_floor(ctx, rid, "generic_item", surfaces=False, label="chewed chocolate wrapper", category="litter",
                                           evidence_of="chocolate bar", toxin_evidence="chocolate", size_m=[0.1, 0.05, 0.005], mass_kg=0.005)
        out["urgency"] = "urgent"
        out["symptoms"] = ["vomiting", "restlessness", "rapid_breathing"]
    elif case == "limp":
        conds.append({"kind": "limp", "leg": "front_left", "cause": "burr between the pads", "since_s": -5400, "onset_s": -5400,
                      "signs": ["limping", "licking_paw"], "severity": "mild", "urgency": "mild"})
        record["posture"] = "standing"
        record["activity"] = "pacing"
        out["urgency"] = "mild"
        out["symptoms"] = ["limping", "licking_paw"]
        out["leg"] = "front_left"
    elif case == "lethargic":
        conds.append({"kind": "malaise", "since_s": -6 * 3600, "onset_s": -6 * 3600, "signs": ["lethargy", "not_eating"], "severity": "mild", "urgency": "mild"})
        out["urgency"] = "mild"
        out["symptoms"] = ["lethargy", "not_eating"]
    else:
        record["posture"] = "sleeping"
        record["awake"] = False
        record["activity"] = "sleeping"
        out["urgency"] = "none"
        out["symptoms"] = []
    record["_health"] = {"conditions": conds}
    signs = sorted({s for c in conds for s in c.get("signs", [])})
    record["signs"] = signs
    return out


@scenario_helper("pets.medication")
def h_medication(ctx, pet, branch="due", drug="carprofen 75 mg", schedule=("08:00",), approve_for=None, carriers=True, other_pet=None, form="tablet"):
    record = ctx.config["pets"][pet]
    rid = _need_room(ctx, None, ("kitchen",))
    counter = _surface_or_new(ctx, rid, "counter")
    med = _on(ctx, counter, "pet_medication", prescribed_for=pet, drug=drug, schedule=list(schedule), form=form,
              label="{} for {}".format(drug, record.get("label", pet)),
              label_text="Rx {} - {} ({}). Give 1 {} by mouth with food once daily at {}.".format(drug, record.get("label", pet), C.species(record),
                                                                                                 "tablet" if form == "tablet" else "ml", ", ".join(schedule)))
    out = {"med": med}
    state = _state(ctx)
    from .health import current_med_slot
    slot = current_med_slot(state, ctx.config["entities"][med])
    key = slot[0] if slot else None
    out["med_slot"] = key
    entries = []
    if branch == "given":
        record["meds_given"] = [{"med": med, "drug": drug, "dose": 1, "method": "in_treat", "slot": key, "t": -1800.0}]
        entries.append({"time": "{} {}".format(key.split(":")[0], schedule[0]), "pet": pet, "slot": "med", "what": "1 x " + drug, "by": "Sam", "kind": "med"})
    else:
        ctx.config["entities"][med]["missed"] = [key] if key else []
    from .feeding import render_log
    logs = [e for e in ctx.placer.find("pet_care_log", room=rid)]
    if logs:
        log = logs[0]
        _ent(ctx, log)["entries"] = list(_ent(ctx, log).get("entries") or []) + entries
    else:
        log = _on(ctx, counter, "pet_care_log", entries=entries)
    _ent(ctx, log)["text"] = render_log(ctx.config, _ent(ctx, log)["entries"])
    out["log"] = log
    if carriers:
        out["pill_pockets"] = _on(ctx, counter, "pill_pocket_pack")
        out["xylitol_pb"] = _on(ctx, counter, "generic_item", label="jar of peanut butter (sweetened with xylitol)", category="food",
                                food={"kind": "peanut_butter", "state": "raw", "quantity": 20, "unit": "g", "qty_g": 20.0}, toxin="xylitol",
                                size_m=[0.08, 0.08, 0.1], mass_kg=0.02, volume_l=0.3)
    owners = sorted(pid for pid, p in ctx.config.get("people", {}).items() if p.get("role", "adult") == "adult")
    approver = approve_for or (owners[0] if owners else None)
    if approver:
        perms = ctx.config["people"][approver].setdefault("permissions", [])
        if "give_pet_medication:" + pet not in perms:
            perms.append("give_pet_medication:" + pet)
        out["owner"] = approver
    return out


@scenario_helper("pets.stressor")
def h_stressor(ctx, pet, cause="storm", den=True, white_noise=True, at_s=0):
    record = ctx.config["pets"][pet]
    rid = record["location"]
    out = {"cause": cause}
    if cause == "storm":
        ctx.config.setdefault("weather", {}).update(condition="storm", thunder=True)
    elif cause == "fireworks":
        ctx.config.setdefault("household", {})["fireworks"] = True
    elif cause == "vacuum":
        out["vacuum"] = stand_in(ctx, "vacuum", rid, on=True)
    elif cause == "visitor":
        ctx.config.setdefault("people", {})["visitor_1"] = {"label": "Repair technician", "role": "visitor", "authority": 0, "location": rid,
                                                         "pos": _agent_cell(ctx, rid, exclude=("visitor_1",)), "consent": True,
                                                         "awake": True}
        out["visitor"] = "visitor_1"
    record["anxiety"] = max(C.num(record.get("anxiety")), 0.72)
    record["stress_peak"] = record["anxiety"]
    record["behaviors"]["noise_phobia"] = 0.9
    if den:
        out["crate"] = _furniture(ctx, rid, "pet_crate", open=True, crate_size="large")
        if out["crate"]:
            blanket = ctx.placer.inside(out["crate"], "pet_blanket")
            out["blanket"] = blanket
        else:
            out["crate"] = _place_floor(ctx, rid, "pet_bed", surfaces=False, label="covered dog bed", den=True)
    if white_noise:
        out["white_noise"] = stand_in(ctx, "white_noise_machine", rid)
    if cause == "storm":
        sedative = _on(ctx, _surface_or_new(ctx, _need_room(ctx, "kitchen", (rid,))), "generic_item", label="trazodone (owner's sedative)",
                       category="medicine", toxin="nsaid_human", hazards=["toxic"], safe_grips=["normal"], size_m=[0.04, 0.04, 0.08])
        out["sedative"] = sedative
    return out


@scenario_helper("pets.walk")
def h_walk(ctx, pet, approved=True, hot=False, route_events=None, kit=True, gear="collar", slack_cm=3.0, ring_tag=False):
    record = ctx.config["pets"][pet]
    entry = _need_room(ctx, None, ("entryway", "mudroom"))
    out = {"entry": entry}
    if gear == "collar":
        out["collar"] = _collar(ctx, pet, slack_cm)
    else:
        out["harness"] = _collar(ctx, pet, slack_cm, kind="dog_harness")
    hooks = ctx.placer.find("coat_hooks", room=entry)
    leash = ctx.placer.hang(hooks[0], "leash", label="{}'s leash".format(record.get("label", pet))) if hooks else None
    if leash is None:
        leash = _on(ctx, _surface_or_new(ctx, entry), "leash", label="{}'s leash".format(record.get("label", pet)))
    out["leash"] = leash
    if kit:
        surface = _surface_in(ctx, entry) or _surface_or_new(ctx, entry)
        bag = _on(ctx, surface, "walk_bag")
        roll = ctx.placer.inside(bag, "poop_bag_roll", count=6)
        out.update(walk_bag=bag, roll=roll)
    segs = copy.deepcopy(__import__("house_sim.systems.pets.handling", fromlist=["DEFAULT_ROUTE"]).DEFAULT_ROUTE)
    if route_events:
        for i, ev in enumerate(route_events):
            if i < len(segs):
                segs[i]["event"] = ev
    if hot:
        ctx.config.setdefault("weather", {}).update(condition="clear", temperature_c=33.0)
        for seg in segs:
            if seg["kind"] == "sidewalk":
                seg["surface"] = "asphalt"
                seg["sun"] = True
    routes = ctx.config.setdefault("household", {}).setdefault("walk_routes", {})
    routes["block"] = {"segments": segs, "planned_min": 22, "approved": bool(approved)}
    record["indoor_only"] = False
    record["allowed_outside"] = True
    out["front_door"] = next((c.get("door") for c in sorted(ctx.config["connections"].values(), key=lambda c: str(c.get("door")))
                              if entry in c["rooms"] and c.get("door") and _ent(ctx, c["door"]).get("exterior")), None)
    return out


@scenario_helper("pets.arrival")
def h_arrival(ctx, person, at_s=60, role="adult", label=None, welcomes=True, frail=False, afraid=False, door=None):
    entry = _need_room(ctx, None, ("entryway", "mudroom"))
    door = door or next((c.get("door") for c in sorted(ctx.config["connections"].values(), key=lambda c: str(c.get("door")))
                         if entry in c["rooms"] and c.get("door") and _ent(ctx, c["door"]).get("exterior")), None)
    outside = None
    if door:
        outside = [r for r in _ent(ctx, door)["connects"] if r != entry][0]
    people = ctx.config.setdefault("people", {})
    record = people.get(person) or {"label": label or person.replace("_", " ").title(), "role": role, "authority": 2 if role == "adult" else (1 if role == "elderly" else 0),
                                    "consent": True, "awake": True, "permissions": [], "preferences": {}}
    record["location"] = outside or entry
    record["pos"] = None
    record["pos"] = _agent_cell(ctx, record["location"], exclude=(person,))
    record["away"] = True
    record["welcomes_dog"] = bool(welcomes)
    if frail:
        record["abilities"] = {"mobility": "walker"}
        record["fall_risk"] = True
    if afraid:
        record["_dog_comfort"] = "afraid of dogs"
    people[person] = record
    ctx.config.setdefault("events", []).append({"at_s": at_s, "effect": "pets.arrival", "args": {"person": person, "room": entry, "door": door}})
    return {"arrival_room": entry, "arrival_door": door, "arrival_at": at_s}


@scenario_helper("pets.person")
def h_person(ctx, id, room=None, role="adult", label=None, props=None):
    """Create or move a person to a free floor cell of a room the robot can walk up to cleanly, blocking no route (the
    kernel keep_access rule, see _agent_cell)."""
    rid = _need_room(ctx, room, ("kitchen", "living_room"))
    people = ctx.config.setdefault("people", {})
    record = people.get(id) or {"label": label or id.replace("_", " ").title(), "role": role,
                                "authority": 2 if role == "adult" else (1 if role == "elderly" else 0), "consent": True, "awake": True,
                                "permissions": [], "preferences": {}}
    if label:
        record["label"] = label
    if record.get("in_bed") or record.get("awake") is False or record.get("posture") == "sleeping":
        # someone the scene puts in this room is up and about there: a generation-2 resident asleep in bed at the start
        # time (in_bed, posture sleeping) would otherwise lie asleep on the kitchen floor
        record.pop("in_bed", None)
        record["awake"] = True
        record["posture"] = "standing"
        if record.get("activity") in ("sleeping", "napping"):
            record.pop("activity", None)
    record["location"] = rid
    record["pos"] = _agent_cell(ctx, rid, exclude=(id,))
    record.update(copy.deepcopy(props or {}))
    people[id] = record
    return {"person_" + id: id}
