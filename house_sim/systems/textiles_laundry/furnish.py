"""Furnishers: put laundry appliances, linens, clothes and shoes into generated houses.

order 45  textiles_laundry.appliances  washer + dryer (+ detergent drawer and compartments, lint screen,
                                       drain filter), products on the laundry shelf (bleach on the top shelf),
                                       drying rack, ironing board and iron, a clean-laundry basket.
                                       Rooms: laundry_room, shared_laundry; else basement, garage, bathroom
                                       or kitchen (compact apartments). US houses get top-loaders with
                                       cold/warm/hot dials; others front-loaders with C dials.
order 75  textiles_laundry.linens      bed sizes and dressed beds (fitted sheet, duvet in cover, cased
                                       pillows; crib: fitted crib sheet only), dresser drawers (3 parts),
                                       a hamper per occupied bedroom, a few folded/hung clothes per
                                       occupant, bathroom towels and bath mat, a linen-closet stock,
                                       one pair of shoes per resident at the entry, coats on hooks,
                                       a folded throw and two cushions on the sofa.
Counts are kept modest (~60-90 entities in a family house). Every helper tolerates missing rooms
and Placer returning None.
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher
from . import model as M
from .fold import fold_dims

DRAWER_LAYOUT = [("top drawer", 0.62, "socks_underwear"), ("middle drawer", 0.38, "tops"), ("bottom drawer", 0.14, "bottoms")]
COMPARTMENTS = [("prewash", 0.12, 0.12), ("main", 0.2, 0.2), ("softener", 0.12, 0.09), ("bleach", 0.12, 0.1)]


def _region(config, opts):
    return opts.get("region") or (config.get("metadata") or {}).get("region")


def _set(config, eid, **props):
    if eid and eid in config["entities"]:
        config["entities"][eid].update(props)


def _fold(config, eid, style):
    rec = config["entities"][eid]
    dims, volume, lumpy = fold_dims(rec, style, 0.6)
    rec.update(folded=style, size_m=dims, volume_l=volume, fold_quality={"align_cm": 0.6, "lumpy": lumpy, "neat": not lumpy},
               wrinkled=0.05)
    return eid


def wall_cells(room):
    gc, gr = geometry.grid_size(room)
    return sorted({(x, 0) for x in range(gc)} | {(x, gr - 1) for x in range(gc)} | {(0, y) for y in range(gr)} |
                  {(gc - 1, y) for y in range(gr)})


def wall_floor_item(p, rid, type_name, near=None, tries=10, **props):
    """Placer.floor_item standing against a wall (its footprint touches the room's edge): out of doorway clearance
    (items 0.15 m or taller), never cutting a hazard-free route or the only access to anything (keep_access), and
    itself reachable. Wall cells nearest `near` first (shuffled without `near`). None when no wall cell qualifies."""
    room = p.c["rooms"].get(rid)
    if room is None:
        return None
    wall = wall_cells(room)
    if near is not None:
        order = sorted(wall, key=lambda c: (geometry.chebyshev(c, near), c[1], c[0]))
    else:
        order = p.rng.sample(wall, len(wall))
    ruled_out = set()
    for cell in order:
        if cell in ruled_out:
            continue
        if tries <= 0:
            break
        tries -= 1
        eid = p.floor_item(rid, type_name, near=cell, **props)
        if eid is None:
            return None                       # nothing fits anywhere in the room
        rec = p.c["entities"][eid]
        if set(geometry.footprint(rec)) & set(wall):
            return eid
        # the nearest acceptable cell is inside the room: every wall cell closer to `cell` than that is unacceptable
        dist = geometry.chebyshev(tuple(rec["pos"]), cell)
        ruled_out.update(c for c in wall if geometry.chebyshev(c, cell) < dist)
        del p.c["entities"][eid]
    return None


def spare_floor(p, rid):
    """Free floor cells of a room outside doorway clearance: not blocked, not taken by anything floor-standing and
    not a (placement) hazard. Laundry extras are only stored on the floor while enough of it stays free."""
    state = p.state()
    room = p.c["rooms"][rid]
    gc, gr = geometry.grid_size(room)
    taken = set(geometry.blocked_cells(state, rid, include_agents=False)) | set(geometry.placement_hazards(state, rid))
    taken |= geometry.door_clearance(state, rid)
    for eid in geometry.room_entities(state, rid):
        rec = p.c["entities"][eid]
        if rec.get("pos") and geometry.floor_standing(rec):
            taken.update(geometry.footprint(rec))
    return sum(1 for y in range(gr) for x in range(gc) if (x, y) not in taken)


FLOOR_RESERVE = 0.3   # share of a room's cells (at least 4) kept free when laundry extras are stored on its floor
EXTRA_REACH = 2       # cells from the washer within which an extra (clean-laundry) basket may stand


def roomy(p, rid, type_name):
    """True if a standing item of this type can go on the room's floor and leave the floor reserve free (small laundry
    rooms stay walkable for the robot and for whatever the household keeps there too)."""
    room = p.c["rooms"][rid]
    gc, gr = geometry.grid_size(room)
    cols, rows = geometry.footprint_size(p.reg.make(type_name, rid))
    return spare_floor(p, rid) - cols * rows >= max(4, int(FLOOR_RESERVE * gc * gr + 0.999))


CLOSET_ROOMS = ("guest_room", "master_bedroom", "bedroom", "office", "entryway")


def stow_in_closet(p, type_name, rooms=None, **props):
    """Inside (on the floor of) a closet or wardrobe: where folded racks, ironing boards and hampers go when the
    laundry room has no wall space for them. None if no closet takes it."""
    rooms = rooms if rooms is not None else [r for kind in CLOSET_ROOMS for r in p.rooms(kind)]
    for rid in rooms:
        for closet in p.find("closet", room=rid) + p.find("wardrobe", room=rid):
            rec = p.c["entities"][closet]
            if rec.get("location") != rid or not rec.get("container"):
                continue
            eid = p.inside(closet, type_name, shelf=0, **props)
            if eid:
                return eid
    return None


SMALL_MOUNTS = ("switch", "outlet", "valve", "light", "thermostat", "sensor", "detector")


def hook_cell(p, rid, near=None):
    """A wall cell for laundry wall hooks, from which a folded board and rack hang down flat to just above the floor:
    its floor is free (nothing blocking or floor-standing, no hazard, nobody standing there), it is outside doorway
    clearance (no door leaf sweeps the hanging board), reachable from the doorways, and no window, radiator, shelf or
    other wall fixture below 2 m shares it. Nearest `near` first. None when the room has no such wall."""
    state = p.state()
    room = p.c["rooms"][rid]
    taken = set(geometry.blocked_cells(state, rid)) | set(geometry.placement_hazards(state, rid)) | geometry.door_clearance(state, rid)
    for eid in geometry.room_entities(state, rid):
        rec = p.c["entities"][eid]
        if not rec.get("pos"):
            continue
        if geometry.floor_standing(rec):
            taken.update(geometry.footprint(rec))
        elif rec.get("mounted") and rec.get("category") not in SMALL_MOUNTS and float(rec.get("mount_height_m", 0.0) or 0.0) < 2.0:
            taken.update(geometry.footprint(rec))
    reach = geometry.reachable_cells(state, rid)
    options = [c for c in wall_cells(room) if c not in taken and c in reach]
    if not options:
        return None
    if near is not None:
        options.sort(key=lambda c: (geometry.chebyshev(c, near), c[1], c[0]))
    return list(options[0])


def hang_on_wall_hooks(p, rid, type_name, near=None, **props):
    """Hang a folded rack or ironing board on the room's laundry wall hooks (mounting a pair where the wall allows).
    Returns its id or None."""
    hooks = [h for h in p.find("laundry_wall_hooks", room=rid)
             if sum(1 for e in p.c["entities"].values() if e.get("location") == h and e.get("relation") == "hung") <
             int(p.c["entities"][h].get("hang_slots", 0))]
    if not hooks:
        cell = hook_cell(p, rid, near=near)
        if cell is None:
            return None
        hooks = [p.mount(rid, "laundry_wall_hooks", cell=cell)]
    return p.hang(hooks[0], type_name, **props)


def stow_folded(p, rid, type_name, near=None, closet=True, hooks=True, **props):
    """A folded drying rack or ironing board where a household keeps it: hung flat on laundry wall hooks (takes no
    floor; hooks=True), else stood upright against a wall (one floor cell, only while the floor reserve stays free),
    else (closet=True) in a closet. Returns its id or None."""
    eid = hang_on_wall_hooks(p, rid, type_name, near=near, **props) if hooks and rid in p.c["rooms"] else None
    if eid is None and rid in p.c["rooms"] and roomy(p, rid, type_name):
        eid = wall_floor_item(p, rid, type_name, near=near, **props)
        if eid:
            p.c["entities"][eid]["stored_upright"] = True
    if eid is None and closet:
        eid = stow_in_closet(p, type_name, **props)
        if eid:
            p.c["entities"][eid]["stored_upright"] = True
    return eid


def basket_on_floor(p, rid, type_name, near=None, extra=False, **props):
    """A laundry basket or hamper on the floor (Placer.floor_item keep_access), against a wall where possible.
    extra=True only while the floor reserve stays free, only against a wall and only within EXTRA_REACH cells of
    `near` (the washer)."""
    if rid not in p.c["rooms"] or (extra and not roomy(p, rid, type_name)):
        return None
    eid = wall_floor_item(p, rid, type_name, near=near, **props)
    if eid is None and not extra:
        eid = p.floor_item(rid, type_name, near=near, **props)
    if eid is not None and extra and near is not None and             min(geometry.chebyshev(c, tuple(near)) for c in geometry.footprint(p.c["entities"][eid])) > EXTRA_REACH:
        del p.c["entities"][eid]      # only beside the machines: across a full room it ends up boxed in
        eid = None
    return eid      # an extra basket never stands out in the middle of the floor, where later pieces box it in


def stack_dryer(p, rid, washer, **props):
    """A dryer stacked on a front-loading washer (compact laundry rooms and closets): it shares the washer's floor
    cells and stands on it (`mounted` at the washer's height, `stacked_on`), so its drum opens at ~1.3 m."""
    config = p.c
    wrec = config["entities"][washer]
    rec = p.reg.make("clothes_dryer", rid, **props)
    rec.update(pos=list(wrec["pos"]), rot=wrec.get("rot", 0), mounted=True, blocks_path=False,
               mount_height_m=round(float((wrec.get("size_m") or [0.6, 0.6, 0.85])[2]), 2), stacked_on=washer)
    eid = p.new_id("clothes_dryer")
    config["entities"][eid] = rec
    wrec["stacked_under"] = eid
    return eid


STACK_DRYER_H = 0.85      # a stacked dryer adds this much height above the washer


def mounts_below(p, rid, top):
    """Wall cells of the room's wall mounts that start below `top` metres (windows, blinds, cabinets, shelves,
    radiators, panels, valves; not outlets or lights, which sit behind the appliances they feed): a floor-standing
    piece that tall would hide them, and a deep piece mounted across that height would share their wall space."""
    state = p.state()
    cells = set()
    for eid in geometry.room_entities(state, rid):
        rec = p.c["entities"][eid]
        if not rec.get("mounted") or not rec.get("pos") or rec.get("ground_layer") or "connects" in rec:
            continue
        if geometry.floor_standing(rec) or rec.get("stacked_on") or rec.get("category") in ("outlet", "light"):
            continue
        try:
            h = float(rec.get("mount_height_m", rec.get("z_m", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if 0.0 < h < top and h < 2.0:
            cells.update(geometry.footprint(rec))
    return cells


def stack_top(p, washer):
    return float((p.c["entities"][washer].get("size_m") or [0.6, 0.6, 0.85])[2]) + STACK_DRYER_H


def stack_blocked(p, rid, washer):
    """True if a dryer stacked on the washer where it stands would hide a wall mount or collide with one."""
    return bool(set(geometry.footprint(p.c["entities"][washer])) & mounts_below(p, rid, stack_top(p, washer)))


def _restack_washer(p, rid, washer):
    """Move a washer (no parts yet) to a wall spot where a stacked dryer would hide no wall mount and access stays
    not worse. Returns True if it moved."""
    config = p.c
    wrec = config["entities"][washer]
    top = stack_top(p, washer)
    size = list(wrec.get("size_m") or [0.6, 0.6, 0.85])
    del config["entities"][washer]
    try:
        probe = p.furniture(rid, wrec["type"], wall=True, near=wrec.get("pos"), fallback=False,
                            size_m=[size[0], size[1], round(top, 2)])
        if probe is None:
            return False
        prec = config["entities"].pop(probe)
        if set(geometry.footprint(prec)) & mounts_below(p, rid, top):
            return False         # Placer's last resort hides something: no clean spot
        wrec["pos"], wrec["rot"] = list(prec["pos"]), prec.get("rot", 0)
        return True
    finally:
        config["entities"][washer] = wrec


def place_dryer(p, rid, washer, label="clothes dryer", allow_swap=True, strict_stack=False):
    """Put a dryer beside the washer (any free wall or floor spot in the room that keeps doorways and furniture
    reachable); when the room has no space for a second appliance, stack it on the washer - a top-loader is
    swapped for a stackable front-loader in the same spot (only while installing: `allow_swap=False` for a washer
    that already has its drawer and parts). A stack is ~1.7 m tall: when it would hide or collide with a wall mount
    (window, wall cabinet, shelf, panel, radiator) a freshly installed washer is moved to a wall spot where it hides
    nothing; strict_stack=True (house generation) then leaves the washer without a dryer rather than stacking over a
    mount (the station helper adds one on demand). Returns (dryer id or None, washer id)."""
    config = p.c
    wrec = config["entities"][washer]
    dryer = p.furniture(rid, "clothes_dryer", wall=True, near=wrec["pos"], label=label)
    if dryer is not None and not boxes_in_mounts(p, rid, dryer):
        return dryer, washer
    if dryer is not None:
        del config["entities"][dryer]     # beside the washer it would wall off a radiator, register or panel: stack it
    if wrec.get("stacked_under") in config["entities"]:
        return wrec["stacked_under"], washer
    if wrec.get("loader", "front") != "front":
        if not allow_swap:
            return None, washer
        keep = {k: wrec[k] for k in ("label", "shared", "owner") if k in wrec}
        new = p.reg.make("front_load_washer", rid, **keep)
        new.update(pos=list(wrec["pos"]), rot=wrec.get("rot", 0))
        if wrec.get("setting_options", {}).get("temp") and isinstance(wrec["setting_options"]["temp"][0], str):
            new["setting_options"]["temp"] = ["tap_cold", "cold", "warm", "hot"]
            new["settings"]["temp"] = "warm"
        cells_old = set(geometry.footprint(wrec))
        cells_new = set(geometry.footprint(new))
        if not cells_new <= cells_old:
            return None, washer
        del config["entities"][washer]
        washer = p.new_id("front_load_washer")
        config["entities"][washer] = new
    if stack_blocked(p, rid, washer):
        moved = allow_swap and _restack_washer(p, rid, washer)
        if not moved and strict_stack:
            return None, washer
    return stack_dryer(p, rid, washer, label=label), washer


def boxes_in_mounts(p, rid, eid):
    """True if a freshly placed appliance cuts off access to a wall mount that was accessible without it (Placer
    falls back to protecting only doorways and floor-standing things when nothing better fits; a second appliance
    must not take that fallback). Outlets are exempt: the machine plugs into the one behind it."""
    state = p.state()
    cells = set(geometry.footprint(p.c["entities"][eid]))
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    targets = geometry.access_targets(state, rid, level="all", ignore=(eid,))
    targets = {k: v for k, v in targets.items() if k[0:1] == ("door",) or p.c["entities"][k].get("category") != "outlet"}
    before = geometry.access_report(state, rid, blocked - cells, targets)
    after = geometry.access_report(state, rid, blocked | cells, targets)
    return not geometry.not_worse(before, after)


def fit_lint_screen(p, dryer):
    """The dryer's lint screen (made, lightly linted, when its slot is empty). Returns its id."""
    config = p.c
    slot = ((config["entities"][dryer].get("slots") or {}).get("lint_screen") or {})
    if slot.get("item") in config["entities"]:
        return slot["item"]
    screen = p.make("lint_screen", dryer, soil={"lint": round(p.rng.uniform(0.0, 0.25), 2)})
    config["entities"][screen].update(relation="part", slot="lint_screen")
    config["entities"][dryer].setdefault("slots", {}).setdefault("lint_screen", {})["item"] = screen
    return screen


def _wall_spot_bad(p, rid, eid, cells):
    """Why a wall mount (eid) at these cells would be badly placed: off the grid, in a doorway or its clearance,
    behind a floor-standing piece taller than its mounting height (a shower stall, wardrobe, appliance), or sharing
    wall space with another deep wall mount at overlapping heights (a stacked dryer, a wall cabinet)."""
    ents = p.c["entities"]
    state = p.state()
    room = p.c["rooms"][rid]
    rec = ents[eid]
    if not all(geometry.in_grid(room, c) for c in cells):
        return "offgrid"
    if set(cells) & (geometry.door_clearance(state, rid) | set(geometry.doorway_cells(state, rid))):
        return "doorway"
    h = float(rec.get("mount_height_m", 0.0) or 0.0)
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    top, depth = h + float(size[2]), min(float(size[0]), float(size[1]))
    for oid in geometry.room_entities(state, rid):
        other = ents[oid]
        if oid == eid or not other.get("pos") or other.get("ground_layer") or "connects" in other:
            continue
        if not set(geometry.footprint(other)) & set(cells):
            continue
        try:
            o_h = float(other.get("mount_height_m", other.get("z_m", 0.0)) or 0.0)
            o_size = other.get("size_m") or [0.1, 0.1, 0.1]
            o_top, o_depth = o_h + float(o_size[2]), min(float(o_size[0]), float(o_size[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if geometry.floor_standing(other) and o_top > h + 0.05:
            return "hidden behind " + oid
        if other.get("mounted") and not geometry.floor_standing(other) and other.get("category") not in ("outlet", "light", "window", "drawer") \
                and depth >= 0.1 and o_depth >= 0.1 and o_h < top and h < o_top:
            return "overlaps " + oid
    return None


def _clear_of_stack(p, rid, eid, dryer):
    """Keep a wall mount placed by Placer's last resort (laundry wall shelving) on a wall where it hides behind
    nothing, shares no wall space with a stacked dryer or another deep mount, and stays reachable."""
    ents = p.c["entities"]
    rec = ents[eid]
    if not _wall_spot_bad(p, rid, eid, geometry.footprint(rec)):
        return
    state = p.state()
    room = p.c["rooms"][rid]
    reach = geometry.reachable_cells(state, rid)
    for pos in wall_cells(room):
        cells = geometry.footprint(dict(rec, pos=list(pos)))
        if not _wall_spot_bad(p, rid, eid, cells) and geometry.near_cells(cells, room, 2) & reach:
            rec["pos"] = list(pos)
            return


COMPACT_ROOMS = ("bathroom", "half_bath", "kitchen", "utility_closet")   # laundry hookups in a room with another job
LAUNDRY_ROOMS = ("laundry_room", "shared_laundry", "utility_room")       # rooms that are there for laundry


def install_station(p, rid, style="front_load", shared=False, us=False, strict_stack=False):
    """Washer, dryer, drawer/compartments, parts and products in room rid. Returns a dict of ids or None.
    In a bathroom or kitchen (COMPACT_ROOMS) only the machines, products and a dirty-laundry basket go in - the
    rack, ironing board and clean basket are not stored there (the station helper adds them elsewhere on demand)."""
    config = p.c
    compact = config["rooms"][rid].get("type") in COMPACT_ROOMS
    washer_type = "top_load_washer" if style == "top_load" else "front_load_washer"
    washer = p.furniture(rid, washer_type, wall=True, label="washing machine" + (" (shared)" if shared else ""))
    if washer is None:
        return None
    dryer, washer = place_dryer(p, rid, washer, label="clothes dryer" + (" (shared)" if shared else ""), strict_stack=strict_stack)
    wrec = config["entities"][washer]
    if shared:
        wrec["shared"] = True
    if us and wrec.get("loader") == "front":
        wrec["setting_options"]["temp"] = ["tap_cold", "cold", "warm", "hot"]
        wrec["settings"]["temp"] = "warm"
    # the detergent drawer is a part of the washer (front panel, top left, ~7 cm below the top; under the lid of a
    # top-loader), so it is reached wherever the washer is reached
    height = float((wrec.get("size_m") or [0.6, 0.6, 0.85])[2])
    drawer = p.make("washer_dispenser", washer, appliance_of=washer, label="detergent drawer",
                    z_m=round(height - (0.07 if wrec.get("loader", "front") == "front" else 0.12), 2))
    config["entities"][drawer]["relation"] = "part"
    comps = {}
    for role, cap, line in COMPARTMENTS:
        cid = p.make("dispenser_compartment", drawer, compartment=role, capacity_l=cap, max_line_l=line,
                     label="{} compartment".format(role))
        config["entities"][cid]["relation"] = "in"
        comps[role] = cid
    wrec["dispenser"] = drawer
    result = {"washer": washer, "drawer": drawer, "laundry_room": rid}
    result.update(comps)
    if "drain_filter" in (wrec.get("slots") or {}):
        filt = p.make("washer_drain_filter", washer)
        config["entities"][filt].update(relation="part", slot="drain_filter")
        wrec["slots"]["drain_filter"]["item"] = filt
        result["drain_filter"] = filt
    if dryer:
        drec = config["entities"][dryer]
        if shared:
            drec["shared"] = True
        result.update(dryer=dryer, lint_screen=fit_lint_screen(p, dryer))
    shelves = p.find("shelf_unit", room=rid)
    shelf = shelves[0] if shelves else None
    if shelf is None:
        # open wall shelving above counter height (no floor needed): four shelves from 0.95 to 1.75 m
        shelf = p.mount(rid, "shelf_unit", height=0.95, label="laundry wall shelving", size_m=[0.8, 0.3, 0.85],
                        shelf_heights=[0.0, 0.27, 0.54, 0.8], inner_size_m=[0.76, 0.28, 0.25], capacity_l=160.0, mass_kg=8.0)
        _clear_of_stack(p, rid, shelf, dryer)

    def put(type_name, level, **props):
        if config["entities"][shelf].get("shelf_heights"):
            return p.inside(shelf, type_name, shelf=level, **props)
        return p.on(shelf, type_name, **props)

    result["shelf"] = shelf
    result["detergent"] = put("liquid_detergent", 2)
    tub = put("pod_tub", 2)
    result["pod_tub"] = tub
    pods = []
    if tub:
        for _ in range(2):
            pod = p.inside(tub, "detergent_pod")
            if pod:
                pods.append(pod)
    result["pods"] = pods
    result["softener_bottle"] = put("fabric_softener", 2)
    result["bleach_bottle"] = put("chlorine_bleach_bottle", 3)
    result["stain_remover"] = put("stain_remover_spray", 1)
    box = put("dryer_sheet_box", 1)
    result["sheet_box"] = box
    result["dryer_sheets"] = [s for s in [p.inside(box, "dryer_sheet")] if s] if box else []
    # floor items, most needed first: the dirty-laundry basket, then (only while the floor reserve stays free) the
    # folded rack and ironing board stood against a wall - else in a closet - and a clean-laundry basket
    baskets = p.find("laundry_basket", room=rid)
    for basket in baskets:
        config["entities"][basket].setdefault("designated", "dirty")
    result["basket"] = baskets[0] if baskets else basket_on_floor(p, rid, "laundry_basket", near=wrec["pos"], designated="dirty")
    if not compact:
        result["rack"] = stow_folded(p, rid, "drying_rack", near=wrec["pos"], closet=not shared)
    if not shared:
        if not compact:
            result["board"] = stow_folded(p, rid, "ironing_board", near=wrec["pos"])
        result["iron"] = put("clothes_iron", 1)
        result["hangers"] = [h for h in (put("plastic_hanger", 0), put("plastic_hanger", 0)) if h]
    if config["rooms"][rid].get("type") in LAUNDRY_ROOMS:
        # a second (clean-laundry) basket only in a room that is there for laundry, and only on spare floor
        result["clean_basket"] = basket_on_floor(p, rid, "laundry_basket", near=wrec["pos"], extra=True, label="clean laundry basket",
                                                 designated="clean")
    return result


def find_station(config, rid):
    """Ids of an already-installed station in room rid (or None)."""
    ents = config["entities"]
    state = {"rooms": config["rooms"], "entities": ents, "people": config.get("people", {}), "pets": config.get("pets", {}), "robot": {"room": None}}
    from ...kernel.predicates import root_room
    washers = [e for e in sorted(ents) if M.is_kind(ents[e], "washing_machine") and ents[e].get("location") == rid]
    if not washers:
        return None
    # a furnished station's washer has a detergent drawer (other domains may add bare washers to the room)
    washer = next((e for e in washers if ents[e].get("dispenser") in ents), washers[0])
    result = {"washer": washer, "laundry_room": rid, "drawer": ents[washer].get("dispenser")}
    if result["drawer"]:
        for c in sorted(ents):
            if ents[c].get("location") == result["drawer"] and ents[c].get("compartment"):
                result[ents[c]["compartment"]] = c
    slots = ents[washer].get("slots") or {}
    if (slots.get("drain_filter") or {}).get("item"):
        result["drain_filter"] = slots["drain_filter"]["item"]
    dryer = ents[washer].get("stacked_under") if ents[washer].get("stacked_under") in ents else None
    dryer = dryer or next((e for e in sorted(ents) if M.is_kind(ents[e], "clothes_dryer") and ents[e].get("location") == rid), None)
    if dryer:
        result["dryer"] = dryer
        result["lint_screen"] = ((ents[dryer].get("slots") or {}).get("lint_screen") or {}).get("item")
    lookup = {"detergent": "liquid_detergent", "pod_tub": "pod_tub", "softener_bottle": "fabric_softener", "bleach_bottle": "chlorine_bleach_bottle",
              "stain_remover": "stain_remover_spray", "sheet_box": "dryer_sheet_box", "rack": "drying_rack", "board": "ironing_board",
              "iron": "clothes_iron", "shelf": "shelf_unit"}
    for key, type_name in lookup.items():
        found = [e for e in sorted(ents) if (ents[e].get("type") == type_name or type_name in ents[e].get("is_a", [])) and root_room(state, e) == rid]
        if found:
            result[key] = found[0]
    if "shelf" not in result:
        shelves = [e for e in sorted(ents) if ents[e].get("type") == "wall_shelf" and ents[e].get("location") == rid]
        if shelves:
            result["shelf"] = shelves[0]
    result["pods"] = [e for e in sorted(ents) if ents[e].get("pod") and ents[e].get("location") == result.get("pod_tub")]
    result["dryer_sheets"] = [e for e in sorted(ents) if ents[e].get("dryer_sheet") and ents[e].get("location") == result.get("sheet_box")]
    baskets = [e for e in sorted(ents) if ents[e].get("type") == "laundry_basket" and ents[e].get("location") == rid]
    result["basket"] = next((b for b in baskets if ents[b].get("designated") != "clean"), baskets[0] if baskets else None)
    result["clean_basket"] = next((b for b in baskets if ents[b].get("designated") == "clean"), None)
    result["hangers"] = [e for e in sorted(ents) if ents[e].get("category") == "hanger" and not ents[e].get("holding_garment")
                         and root_room(state, e) == rid]
    return result


FALLBACK_ROOMS = ("basement", "garage", "bathroom", "kitchen")


@furnisher("textiles_laundry.appliances", order=45)
def furnish_laundry(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    region = _region(config, opts)
    from ...generator import REGIONS
    style = (REGIONS.get(region) or {}).get("washer", "front_load")
    us = region == "us"
    in_unit = p.rooms("laundry_room")
    rooms = in_unit + p.rooms("shared_laundry")
    fallback = [r for kind in FALLBACK_ROOMS for r in p.rooms(kind)]
    if not rooms:
        rooms = fallback[:1]
    made = []
    for rid in rooms[:2]:
        shared = config["rooms"][rid].get("type") == "shared_laundry"
        if install_station(p, rid, style, shared=shared, us=us, strict_stack=True):
            made.append(rid)
    if in_unit and not any(r in in_unit for r in made):
        # the dwelling was built with in-unit laundry, but its laundry room is too full of other fixtures (air
        # handler, water heater, sink) for a washer: the hookups go in the next utility space instead
        for rid in fallback:
            if install_station(p, rid, style, us=us, strict_stack=True):
                break


# ---------------------------------------------------------------------------
# linens, clothes and shoes

def _bedroom_owners(config):
    people = config.get("people", {})
    adults = sorted(pid for pid, p in people.items() if p.get("role", "adult") == "adult")
    others = sorted(pid for pid, p in people.items() if p.get("role") in ("child", "teen", "elderly", "infant"))
    rooms = config["rooms"]
    masters = sorted(r for r in rooms if rooms[r]["type"] == "master_bedroom")
    singles = sorted(r for r in rooms if rooms[r]["type"] == "bedroom")
    owners = {}
    if masters:
        owners[masters[0]] = adults[:2] or others[:1]
        rest = adults[2:] + (others if adults else others[1:])
    else:
        rest = adults + others
    for rid, pid in zip(singles, rest):
        owners[rid] = [pid]
    return owners


def dress_bed(p, bed, size=None, protector=False, flat=False, made=True, pillows=None):
    """Dress a bed in-place during generation/setup. Returns a dict of the created bedding ids."""
    config = p.c
    brec = config["entities"][bed]
    crib = brec.get("type") == "crib" or "crib" in brec.get("is_a", [])
    width = float((brec.get("size_m") or [1.6])[0])
    size = size or ("crib" if crib else ("twin" if width < 1.2 else ("full" if width < 1.45 else ("queen" if width < 1.75 else "king"))))
    brec["bed_size"] = size
    brec.setdefault("mattress_depth_cm", 30.0 if not crib else 15.0)
    dims = {"crib": [0.71, 1.33], "twin": [0.99, 1.9], "full": [1.37, 1.9], "queen": [1.52, 2.03], "king": [1.93, 2.03]}[size]
    flat_dims = {"crib": [1.0, 1.4], "twin": [1.68, 2.44], "full": [2.03, 2.59], "queen": [2.29, 2.59], "king": [2.74, 2.59]}[size]
    duvet_dims = {"crib": [1.0, 1.2], "twin": [1.73, 2.18], "full": [2.03, 2.18], "queen": [2.29, 2.18], "king": [2.59, 2.18]}[size]
    out = {"bed": bed}
    stack = 0

    def layer(type_name, **props):
        nonlocal stack
        stack += 1
        eid = p.make(type_name, bed, **props)
        rec = config["entities"][eid]
        fw, fl = rec.get("flat_size_m") or [1, 2]
        rec.update(relation="on", spread_on=bed, stack_pos=stack, face_up=props.get("face_up", "finished"), orientation="lengthwise",
                   offset_cm=0.0, size_m=[fw, fl, float(rec.get("layer_thickness_m", 0.002))], on_bed=bed, wrinkled=0.05 if made else 0.4)
        return eid

    if protector:
        out["protector"] = layer("mattress_protector", bed_size=size, flat_size_m=list(dims), fold_base_m=list(dims), corners_seated=4)
    fitted_type = "crib_sheet" if crib else "fitted_sheet"
    out["fitted"] = layer(fitted_type, bed_size=size, flat_size_m=list(dims), fold_base_m=list(dims), corners_seated=4)
    if crib:
        brec["bed_state"] = None
        return out
    if flat:
        out["flat"] = layer("flat_sheet", bed_size=size, flat_size_m=list(flat_dims), fold_base_m=list(flat_dims), face_up="back", head="wide_hem")
    stack += 1
    cover = p.make("duvet_cover", bed, bed_size=size, flat_size_m=list(duvet_dims), fold_base_m=list(duvet_dims))
    insert = p.make("duvet_insert", cover, bed_size=size, flat_size_m=list(duvet_dims), fold_base_m=list(duvet_dims))
    crec, irec = config["entities"][cover], config["entities"][insert]
    irec.update(relation="in", part_of_set=True)
    crec.update(relation="on", spread_on=bed, stack_pos=stack, filled_with=insert, corners_matched=4, ties_tied=True, fill_evenness=0.95,
                closure_open=False, size_m=[duvet_dims[0], duvet_dims[1], 0.05], face_up="finished", orientation="lengthwise", offset_cm=0.0,
                on_bed=bed, wrinkled=0.05 if made else 0.45, duvet_style="spread")
    out.update(cover=cover, insert=insert)
    count = pillows if pillows is not None else (1 if size == "twin" else 2)
    out["pillows"], out["cases"] = [], []
    for i in range(count):
        pillow = p.on(bed, "bed_pillow", spos=[round(0.05 + i * 0.55, 3), 0.02])
        if pillow is None:
            pillow = p.make("bed_pillow", bed)
            config["entities"][pillow]["relation"] = "on"
        case = p.make("pillowcase", pillow, covers=pillow, corners_filled=True)
        config["entities"][case]["relation"] = "attached"
        config["entities"][pillow].update(case=case, pose="upright" if made else "flat", zipper_hidden=True, arranged_index=i)
        out["pillows"].append(pillow)
        out["cases"].append(case)
    brec["made"] = bool(made)
    return out


def _owner_garments(p, owner, person, drawers, closet, style):
    child = person.get("role") in ("child", "infant", "teen")
    size = "8" if child else p.rng.choice(["S", "M", "L"])
    made = []
    top_drawer, mid_drawer, low_drawer = drawers if len(drawers) == 3 else (drawers + [None, None, None])[:3]
    if mid_drawer:
        tee = p.inside(mid_drawer, "t_shirt", owner=owner, size=size, color=p.rng.choice(["navy", "grey", "white"]),
                       color_class="dark", home=mid_drawer)
        if tee:
            if p.c["entities"][tee]["color"] == "white":
                p.c["entities"][tee]["color_class"] = "white"
            elif p.c["entities"][tee]["color"] == "grey":
                p.c["entities"][tee]["color_class"] = "light"
            _fold(p.c, tee, style.get("top", "retail"))
            made.append(tee)
    if low_drawer:
        bottom = p.inside(low_drawer, "shorts" if child else "jeans", owner=owner, size=size, home=low_drawer)
        if bottom:
            _fold(p.c, bottom, "half_thirds")
            made.append(bottom)
    if top_drawer:
        pair_id = "pair_{}_{}".format(owner, len(made))
        left = p.inside(top_drawer, "sock", owner=owner, size=size, pair_id=pair_id, color="black", home=top_drawer)
        if left:
            right = p.make("sock", left, owner=owner, size=size, pair_id=pair_id, color="black", home=top_drawer)
            config = p.c
            config["entities"][right].update(relation="attached", paired_with=left)
            lrec = config["entities"][left]
            lrec["paired_with"] = right
            lrec["single_size_m"] = list(lrec["size_m"])
            _fold(config, left, style.get("sock", "pair_fold"))
            made += [left, right]
    if closet:
        kind = "hoodie" if child else "dress_shirt"
        shirt = p.hang(closet, kind, owner=owner, size=size, home=closet)
        if shirt:
            hanger = p.make("plastic_hanger", shirt, holding_garment=shirt)
            p.c["entities"][hanger]["relation"] = "attached"
            p.c["entities"][shirt].update(on_hanger=hanger, hanger_kind="plastic", hang_method="hem", wrinkled=0.08)
            made.append(shirt)
    return made


@furnisher("textiles_laundry.linens", order=75)
def furnish_linens(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    ents = config["entities"]
    people = config.get("people", {})
    household = config.setdefault("household", {})
    style = household.setdefault("fold_style", {"top": rng.choice(["retail", "file"]), "towel": rng.choice(["thirds_half", "half_half"]),
                                                "sock": rng.choice(["pair_fold", "ball"]), "bottom": "half_thirds"})
    # beds
    for bed in sorted(e for e in ents if ents[e].get("category") == "bed" and ents[e].get("location") in config["rooms"]):
        rtype = config["rooms"][ents[bed]["location"]]["type"]
        dress_bed(p, bed, protector=rtype in ("master_bedroom", "guest_room"), flat=rtype == "guest_room", made=rng.random() < 0.6)
    owners = _bedroom_owners(config)
    for rid in sorted(owners):
        occupants = owners[rid]
        if not occupants:
            continue
        dressers = p.find("dresser", room=rid)
        drawers = []
        if dressers:
            dresser = dressers[0]
            _set(config, dresser, owner=occupants[0] if len(occupants) == 1 else None, owners=list(occupants))
            existing = [e for e in sorted(ents) if ents[e].get("location") == dresser and ents[e].get("category") == "drawer"]
            if existing:
                drawers = existing[:3]
            else:
                width = float(ents[dresser]["size_m"][0])
                for label, z, holds in DRAWER_LAYOUT:
                    d = p.make("drawer", dresser, label=label, z_m=z, holds=holds, inner_size_m=[round(width - 0.25, 2), 0.42, 0.15],
                               capacity_l=round((width - 0.25) * 0.42 * 0.15 * 1000.0, 1), size_m=[width - 0.2, 0.45, 0.18],
                               owner=occupants[0] if len(occupants) == 1 else None, dresser_drawer=True)
                    ents[d]["relation"] = "part"
                    drawers.append(d)
        closets = p.find("closet", room=rid) + p.find("wardrobe", room=rid)
        closet = closets[0] if closets else None
        if closet:
            _set(config, closet, owner=occupants[0] if len(occupants) == 1 else None, owners=list(occupants))
            if len(occupants) == 2:
                _set(config, closet, sides={"left": occupants[0], "right": occupants[1]})
        # the hamper stands against a wall near the closet or dresser, else inside the closet
        hamper_props = {"owner": occupants[0], "label": "{} hamper".format(people.get(occupants[0], {}).get("label", occupants[0]))}
        near = ents[closet]["pos"] if closet and ents[closet].get("pos") else (ents[dressers[0]].get("pos") if dressers else None)
        if not wall_floor_item(p, rid, "hamper", near=near, **hamper_props):
            stow_in_closet(p, "hamper", rooms=[rid], **hamper_props)
        for pid in occupants:
            _owner_garments(p, pid, people.get(pid, {}), drawers, closet, style)
    # bathrooms
    for rid in p.rooms("bathroom") + p.rooms("half_bath"):
        hooks = p.find("towel_hook", room=rid)
        if hooks:
            p.hang(hooks[0], "bath_towel" if config["rooms"][rid]["type"] == "bathroom" else "hand_towel", used=False)
            if config["rooms"][rid]["type"] == "bathroom":
                p.hang(hooks[0], "hand_towel", used=False)
        if config["rooms"][rid]["type"] == "bathroom":
            mat = p.floor_item(rid, "bath_mat")
            if mat:
                ents[mat]["size_m"] = list(ents[mat].get("flat_size_m", [0.5, 0.8])) + [0.012]
    # linen closet
    for rid in p.rooms("linen_closet")[:1]:
        shelves = p.find("closet_shelving", room=rid)
        if not shelves:
            continue
        shelf = shelves[0]
        for i, kind in enumerate(("bath_towel", "bath_towel", "hand_towel")):
            towel = p.inside(shelf, kind, shelf=1, home=shelf)
            if towel:
                _fold(config, towel, style["towel"])
        set_id = "linen_set_spare"
        for kind in ("fitted_sheet", "flat_sheet"):
            sheet = p.inside(shelf, kind, shelf=2, set_id=set_id, home=shelf)
            if sheet:
                _fold(config, sheet, "fitted_nest" if kind == "fitted_sheet" else "quarters")
        case = p.inside(shelf, "pillowcase", shelf=2, set_id=set_id, home=shelf)
        if case:
            _fold(config, case, "half_half")
    # entry: shoes and coats
    entries = p.rooms("entryway")
    if entries:
        rid = entries[0]
        racks = p.find("shoe_rack", room=rid)
        hooks = p.find("coat_hooks", room=rid)
        for n, pid in enumerate(sorted(people)):
            person = people[pid]
            child = person.get("role") in ("child", "infant")
            if racks:
                kind = "sneaker" if child or n % 2 else "dress_shoe"
                pair_id = "shoes_{}".format(pid)
                size = rng.choice([1, 2, 3]) if child else rng.choice([7, 8, 9, 10, 11])
                left = p.on(racks[0], kind, owner=pid, side="left", pair_id=pair_id, size=size, orientation="upright")
                if left:
                    right = p.make(kind, left, owner=pid, side="right", pair_id=pair_id, size=size, orientation="upright")
                    ents[right].update(relation="attached", paired_with=left)
                    lrec = ents[left]
                    lrec["paired_with"] = right
                    lrec["single_size_m"] = list(lrec["size_m"])
                    lrec["size_m"] = [round(lrec["size_m"][0] * 2 + 0.01, 3), lrec["size_m"][1], lrec["size_m"][2]]
                    spot = geometry.surface_spot(p.state(), racks[0], left)
                    if spot is None:
                        del ents[right]
                        del ents[left]
                    else:
                        lrec["spos"] = spot
            if hooks and person.get("role", "adult") == "adult":
                p.hang(hooks[0], "coat" if rng.random() < 0.5 else "raincoat", owner=pid)
    # living room
    for rid in p.rooms("living_room")[:1]:
        sofas = p.find("sofa", room=rid)
        if sofas:
            throw = p.on(sofas[0], "throw_blanket", home=sofas[0])
            if throw:
                _fold(config, throw, "thirds_half")
                spot = geometry.surface_spot(p.state(), sofas[0], throw)
                if spot is not None:
                    ents[throw]["spos"] = spot
            for _ in range(2):
                p.on(sofas[0], "decorative_pillow", home=sofas[0], zipper_hidden=True)
