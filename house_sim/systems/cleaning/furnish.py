"""Furnish generated houses with waste bins, liners, cleaning tools and products (order 65: small items).

Every dwelling gets: a lined kitchen trash can and a recycling bin (compost caddy where food scraps
are collected), dish soap/sponge/paper towels by the kitchen sink, everyday cleaners under it, a
lined wastebasket in each bathroom/bedroom/office, a toilet brush per bathroom, and a cleaning
closet (vacuum with bin/filter, a wall-mounted broom-and-mop holder with the broom, flat mop, dustpan
and duster hung on it, mop bucket, dusters, gloves). Houses get outdoor carts (trash, recycling, maybe
yard waste / organics) and possibly a backyard compost bin; apartments get shared carts in the building
corridor. The household's local waste rules are randomized and posted on a recycling guide.

Placement follows the kernel v2.3 rules: floor items go through Placer.floor_item (keep_access: they cut no
hazard-free route or the only access to anything; strict: never in doorway clearance, which covers doorway
cells, their neighbours, hinged leaves and overhead thresholds); the kitchen can and recycling bin are
path-blocking (blocks_path=True passed at creation: a 50 l pedal can is walked around, not over); long
tools hang on Placer.mount-ed holders; parts (liners, vacuum bins and filters) are children of their
container. Placer returns None when a spot is full; every item is optional so any dwelling/seed furnishes
without errors.
"""
from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher
from .helpers import guide_text, mounts_hidden_by, place_floor, random_rules, sign_cell

COUNTER_FINISHES = ["granite", "granite", "quartz", "quartz", "laminate_counter", "marble", "butcher_block"]


def _top(p, type_name, props):
    try:
        return float((p.reg.make(type_name, "x", **props).get("size_m") or [0, 0, 0])[2])
    except (TypeError, ValueError, IndexError):
        return 0.0


def _floor(p, rid, type_name, near=None, **props):
    """A loose floor item by the kernel rule (Placer.floor_item: keep_access, strict for things 0.15 m or taller),
    never standing in front of a wall mount hung lower than its top (a vacuum before the boiler); None when the room
    has no such cell (callers try another room, another storage place, or go without)."""
    hidden = mounts_hidden_by(p.state(), rid, _top(p, type_name, props))
    return p.floor_item(rid, type_name, near=near, avoid=tuple(sorted(hidden)), **props)


def _bin(p, rid, type_name, near=None, **props):
    """A path-blocking bin (blocks_path=True at creation): the kernel keep_access rule first; in a room other
    things' hazards (cords, loose items) already cut, the cell that keeps access no worse (helpers.place_floor)."""
    hidden = mounts_hidden_by(p.state(), rid, _top(p, type_name, props))
    return place_floor(p, rid, type_name, near=near, blocks_path=True, avoid=hidden, **props)


def _pos(p, eid):
    rec = p.c["entities"].get(eid) if eid else None
    return list(rec["pos"]) if rec and rec.get("pos") else None


def _first(p, type_name, room):
    found = p.find(type_name, room)
    return found[0] if found else None


def _install(p, container, part_type, slot, **props):
    if not container or container not in p.c["entities"]:
        return None
    rec = p.c["entities"][container]
    slots = rec.get("slots") or {}
    if slot not in slots or slots[slot] is None:
        return None
    part = p.make(part_type, container, relation="part", slot=slot, **props)
    slots[slot]["item"] = part
    return part


def _lined(p, container, liner_type):
    return _install(p, container, liner_type, "liner", opened=True, folded=False, seated=True, rim_folded=True)


def _place_small(p, surfaces, type_name, **props):
    for surface in surfaces:
        if surface and surface in p.c["entities"]:
            rec = p.c["entities"][surface]
            if rec.get("container") or rec.get("openable"):
                new = p.inside(surface, type_name, **props)
            else:
                new = p.on(surface, type_name, **props)
            if new:
                return new
    return None


def _holder_cell(p, rid, along_wall=False):
    """A wall cell for the tool rail: the tools hang from 1.2 m down to the floor, so never a doorway, never in the
    doorway clearance (a door leaf would swing into the brooms), not behind floor-standing things (they would hang
    into a cabinet) and next to floor the robot can reach. Returns (cell, rot) or None if the room has no such wall.
    along_wall (generation 2): the 0.6 m rail lies flat along the wall of its cell (place.wall_layouts), never
    sticking straight out into the room as the type's own orientation does on a west or east wall; generation 1 keeps
    its pinned layouts."""
    from ...generator.place import wall_layouts
    state = p.state()
    room = p.c["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    taken = set(geometry.doorway_cells(state, rid)) | geometry.door_clearance(state, rid)
    for eid in geometry.room_entities(state, rid):
        if geometry.floor_standing(p.c["entities"][eid]):
            taken.update(geometry.footprint(p.c["entities"][eid]))
    taken.update(geometry.blocked_cells(state, rid))
    reach = geometry.reachable_cells(state, rid)
    rail = p.reg.make("cleaning_tool_holder", rid)
    wall = sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} | {(0, y) for y in range(rows)} |
                  {(cols - 1, y) for y in range(rows)})
    options = []
    for c in wall:
        layouts = wall_layouts(room, rail, c) if along_wall else [(c, rail.get("rot"))]
        for pos, rot in layouts:
            cells = geometry.footprint(dict(rail, pos=list(pos), rot=rot or 0))      # the whole rail, not just its anchor
            if not all(geometry.in_grid(room, x) and x not in taken for x in cells):
                continue
            # reachable: generation 1 keeps its anchor-cell test (pinned layouts); a rail along the wall needs one of
            # its cells next to floor the robot reaches
            near = cells if along_wall else [c]
            if any(geometry.chebyshev(x, r) <= 1 for x in near for r in reach):
                options.append((tuple(pos), rot))
                break
    if not options:
        return None
    pos, rot = p.rng.choice(options)
    return list(pos), rot


def _holder(p, rid, label="broom and mop holder", along_wall=False):
    """A wall-mounted rail for long-handled tools (Placer.mount at an explicit _holder_cell). None if the room has no
    such wall."""
    found = _holder_cell(p, rid, along_wall=along_wall)
    if not found:
        return None
    cell, rot = found
    props = {"rot": rot} if along_wall and rot is not None else {}
    return p.mount(rid, "cleaning_tool_holder", cell=cell, label=label, **props)


def _hang(p, holder, type_name, **props):
    if not holder or holder not in p.c["entities"]:
        return None
    return p.hang(holder, type_name, **props)


def _toilet_cell(p, rid):
    for eid in p.find(room=rid):
        rec = p.c["entities"][eid]
        if (rec.get("toilet") or rec.get("category") == "toilet") and rec.get("pos"):
            return list(rec["pos"])
    return None


@furnisher("cleaning_supplies", order=65)
def furnish_cleaning(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    household = config.setdefault("household", {})
    rules = random_rules(rng)
    from .waste import DEFAULT_RULES
    full = dict(DEFAULT_RULES)
    full.update(rules)
    dwelling = (opts.get("plan") or {}).get("dwelling", "house")
    gen2 = int(opts.get("gen") or 1) >= 2
    if dwelling == "apartment" and full["compost"] == "backyard":
        full["compost"] = "municipal" if rng.random() < 0.5 else "none"
    household.setdefault("waste_rules", full)
    household.setdefault("quiet_hours", [22.0, 7.0])
    household.setdefault("hard_water", rng.random() < 0.6)
    if rng.random() < 0.5:
        household.setdefault("cloth_colors", {"red": "toilet", "yellow": "bathroom", "green": "kitchen", "blue": "glass"})
    rules = household["waste_rules"]
    colors = rules.get("colors") or {}

    # floors: finish detail on existing floor entities
    for eid, rec in sorted(config["entities"].items()):
        if rec.get("category") == "floor":
            material = rec.get("material")
            if material == "hardwood":
                rec["finish"] = "sealed_wood" if rng.random() < 0.9 else "oiled_wood"
            elif material == "stone":
                rec["finish"] = rng.choice(["marble", "travertine", "slate", "natural_stone"])
            elif material == "wood" and rec.get("outdoor"):
                rec["finish"] = "wood_deck"
            elif material == "carpet" and rng.random() < 0.2:
                rec["finish"] = "wool_carpet"
        elif rec.get("type") == "rug":
            rec.setdefault("fringe", rng.random() < 0.4)
            rec.setdefault("pile", rng.choice(["low", "low", "medium", "shag"]))
            if rec.get("material") == "wool" and rng.random() < 0.3:
                rec["delicate"] = True

    kitchens = p.rooms("kitchen")
    for kitchen in kitchens[:1]:
        counters = p.find("counter", kitchen)
        for counter in counters:
            config["entities"][counter].setdefault("finish", rng.choice(COUNTER_FINISHES))
        cabinets = p.find("cabinet", kitchen)
        under_sink = cabinets[0] if cabinets else None
        if under_sink:
            config["entities"][under_sink]["label"] = "under-sink cabinet"
        near = _pos(p, counters[0]) if counters else None
        can = _bin(p, kitchen, "kitchen_trash_can", near=near, color=colors.get("trash", "black"))
        _lined(p, can, "trash_liner_13gal")
        _bin(p, kitchen, "recycling_bin", near=_pos(p, can) or near, color=colors.get("recycling", "blue"))
        if rules.get("compost") in ("backyard", "municipal") and rng.random() < 0.75:
            caddy = _place_small(p, counters, "compost_caddy", color=colors.get("compost", "green"))
            _install(p, caddy, "charcoal_filter", "filter")
        _place_small(p, counters, "dish_soap")
        _place_small(p, counters, "sponge")
        _place_small(p, counters, "paper_towel_roll")
        if rng.random() < 0.6:
            _place_small(p, counters, "dish_drying_rack")
        state = p.state()
        guide = p.reg.make("recycling_guide", kitchen)
        p.mount(kitchen, "recycling_guide", height=1.3, text=guide_text(rules),
                cell=sign_cell(config, state, kitchen, near=_pos(p, can) or near, height=1.3, record=guide))
        store = [under_sink] if under_sink else counters
        for type_name in ("all_purpose_cleaner", "glass_cleaner", "liner_box", "rubber_gloves"):
            new = _place_small(p, store, type_name)
            if type_name == "liner_box" and new:
                for _ in range(3):
                    liner = p.inside(new, "trash_liner_13gal")
                    if liner is None:
                        break
        if rng.random() < 0.6:
            _place_small(p, store, "bleach")
        if rng.random() < 0.5:
            _place_small(p, store, "degreaser")
        drawers = [d for d in p.find("drawer", kitchen) if "towel" in config["entities"][d].get("label", "")]
        for _ in range(2):
            _place_small(p, drawers or store, "microfiber_cloth", color=rng.choice(["blue", "green", "yellow"]))

    for i, bath in enumerate(p.rooms("bathroom") + p.rooms("half_bath")):
        vanity = _first(p, "cabinet", bath)
        toilet = _toilet_cell(p, bath)
        basket = _floor(p, bath, "bathroom_wastebasket", near=_pos(p, vanity) or toilet)
        _lined(p, basket, "trash_liner_4gal")
        _floor(p, bath, "toilet_brush", near=toilet)      # beside the toilet; a bathroom with no free cell goes without
        if vanity:
            if i == 0:
                _place_small(p, [vanity], "toilet_bowl_cleaner")
                _place_small(p, [vanity], "glass_cleaner")
            _place_small(p, [vanity], "trash_liner_4gal")

    for kind in ("bedroom", "master_bedroom", "guest_room", "office", "nursery", "playroom", "laundry_room", "craft_room"):
        for rid in p.rooms(kind):
            desk = _first(p, "desk", rid)
            basket = _floor(p, rid, "wastebasket", near=_pos(p, desk))
            if basket and rng.random() < 0.6:
                _lined(p, basket, "trash_liner_8gal")
            if kind == "office":
                _floor(p, rid, "office_paper_bin", near=_pos(p, desk), color=colors.get("recycling", "blue"))

    closet_rooms = p.rooms("utility_closet") + p.rooms("laundry_room") + p.rooms("garage") + p.rooms("basement")
    closet = closet_rooms[0] if closet_rooms else (kitchens[0] if kitchens else None)
    if closet:
        shelves = p.find("closet_shelving", closet) + p.find("shelf_unit", closet)
        rooms = [closet] + [r for r in closet_rooms[1:2]] + [k for k in kitchens[:1] if k != closet]
        holders = {}

        def holder_in(rid):
            if rid not in holders:
                holders[rid] = _holder(p, rid, along_wall=gen2)
            return holders[rid]

        def floor_somewhere(type_name, **props):
            for rid in rooms:
                new = _floor(p, rid, type_name, **props)
                if new:
                    return new
            return None

        def hang_somewhere(type_name, **props):
            """Long-handled tools hang on the holder (closet first); a full or missing holder means floor space."""
            for rid in rooms:
                new = _hang(p, holder_in(rid), type_name, **props)
                if new:
                    return new
            return floor_somewhere(type_name, **props)

        vac_type = rng.choice(["upright_vacuum", "stick_vacuum", "stick_vacuum"])
        vac = floor_somewhere(vac_type)
        if vac is None:
            # no floor corner left: a stick vacuum hangs on its wall bracket (the holder rail)
            vac = next((v for v in (_hang(p, holder_in(r), "stick_vacuum") for r in rooms) if v), None)
        if vac:
            _install(p, vac, "vacuum_bin", "bin")
            _install(p, vac, rng.choice(["vacuum_filter_foam", "vacuum_filter_hepa", "vacuum_filter_hepa_washable"]), "filter")
        hang_somewhere("broom")
        hang_somewhere("flat_mop")
        if not floor_somewhere("mop_bucket"):
            _place_small(p, shelves, "mop_bucket")
        if not _place_small(p, shelves, "dustpan"):
            hang_somewhere("dustpan")
        for type_name in ("crevice_tool", "dust_brush_attachment", "microfiber_duster", "scrub_brush", "microfiber_cloth"):
            if not _place_small(p, shelves, type_name):
                floor_somewhere(type_name)
        if rng.random() < 0.5:
            hang_somewhere("extendable_duster")
        for type_name, chance in (("floor_cleaner", 0.7), ("wet_floor_sign", 0.3), ("upholstery_tool", 0.5),
                                  ("disinfectant_spray", 0.6), ("stone_cleaner", 0.2), ("wood_floor_cleaner", 0.3)):
            if rng.random() < chance and not _place_small(p, shelves, type_name):
                floor_somewhere(type_name)

    garages = p.rooms("garage") + p.rooms("shed")
    if garages:
        rid = garages[0]
        if not _hang(p, _holder(p, rid, label="broom holder", along_wall=gen2), "push_broom"):
            _floor(p, rid, "push_broom")

    if dwelling == "apartment":
        corridor = (p.rooms("building_corridor") or p.rooms("lobby") or [None])[0]
        if corridor:
            p.furniture(corridor, "trash_cart", label="building trash cart", color=colors.get("trash", "black"))
            p.furniture(corridor, "recycling_cart", label="building recycling cart", color=colors.get("recycling", "blue"))
            if rules.get("compost") == "municipal":
                p.furniture(corridor, "organics_cart", label="building organics cart", color=colors.get("compost", "green"))
    else:
        yard = (p.rooms("driveway") + p.rooms("garage") + p.rooms("front_yard") or [None])[0]
        if yard:
            p.furniture(yard, "trash_cart", color=colors.get("trash", "black"))
            p.furniture(yard, "recycling_cart", color=colors.get("recycling", "blue"))
            if rules.get("compost") == "municipal":
                p.furniture(yard, "organics_cart", color=colors.get("compost", "green"))
            if rng.random() < 0.4:
                p.furniture(yard, "yard_waste_cart", color=colors.get("yard_waste", "brown"))
        if rules.get("compost") == "backyard":
            spot = (p.rooms("garden") + p.rooms("backyard") or [None])[0]
            if spot:
                p.furniture(spot, "backyard_compost_bin", wall=True)

    # stairs: one stair_run per staircase, a walkable ground layer at the bottom of the flight
    for cid, conn in sorted(config.get("connections", {}).items()):
        if conn.get("kind") != "stairs":
            continue
        a, b = conn["rooms"]
        lower = a if config["rooms"][a].get("floor", 0) <= config["rooms"][b].get("floor", 0) else b
        steps = int(conn.get("steps", 14))
        floor_mat = config["entities"].get("floor_" + lower, {}).get("material")
        material = "carpet" if floor_mat == "carpet" or rng.random() < 0.3 else "hardwood"
        new = p.make("stair_run", lower, label="stairs to " + config["rooms"][b if lower == a else a].get("label", "upstairs").lower(),
                     connection=cid, treads=steps, faces=["t{}".format(i) for i in range(1, steps + 1)], material=material,
                     mounted=True, blocks_path=False, ground_layer=True, size_m=[0.5, 0.5, 0.18])
        config["entities"][new]["pos"] = list(conn["cells"][lower])
