"""Furnishers: an office corner (printer with paper/ink, stationery, filing folders), mail and
package spots, mailboxes, bathroom and kitchen point-of-use consumables, backstock shelves,
luggage and school bags. Every helper tolerates missing rooms/furniture (Placer returns None).

Kernel v2.3: placement goes through the shared rules only -- Placer.floor_item (keep_access, strict door
clearance for tall items), Placer.mount with a cell chosen from geometry.door_clearance / reachable_cells /
floor_standing (no private door-swing model), geometry.access_report for "can the robot get to it", and
storage.shelf_headroom so stock never pushes a shelf past its load rating.

The builders are reused by scenario helpers (they only need a Placer).
"""
from ...kernel import geometry
from ...kernel.plugins import furnisher
from ...generator.place import Placer
from .storage import shelf_headroom

FOLDER_SETS = [("Utilities", "bill", "Electric bill"), ("Insurance", "insurance", "Home insurance policy"),
               ("Medical", "medical", "Lab results"), ("Taxes", "tax_form", "Tax return"), ("Warranties", "warranty", "Warranty card")]


def _first(p, types, room_types, reachable=False):
    """First entity of `types` in rooms of `room_types` (room types or room ids, tried in order)."""
    for rtype in room_types:
        for rid in ([rtype] if rtype in p.c["rooms"] else p.rooms(rtype)):
            for t in types:
                for eid in p.find(t, room=rid):
                    if not reachable or reachable_from_door(p, eid):
                        return eid
    return None


def _first_reachable(p, types, room_types):
    """The first reachable candidate, else the first candidate at all (its contents stay where a resident would
    keep them; the layout audit reports furniture nothing can reach)."""
    return _first(p, types, room_types, reachable=True) or _first(p, types, room_types)


def reachable_from_door(p, eid):
    """True when the robot can get to eid (standing in a room) from the walkable area joined to the room's
    doorways: the kernel's access rule (geometry.access_report; arm's reach around the footprint, one more cell
    for mounts at 0.85 m and above). Things inside/on other things defer to their parent."""
    rec = p.c["entities"].get(eid) or {}
    rid = rec.get("location")
    if rid not in p.c["rooms"] or not rec.get("pos"):
        return True   # inside/on something else: reachability is the parent's business
    state = p.state()
    room = p.c["rooms"][rid]
    cells = [c for c in geometry.footprint(rec) if geometry.in_grid(room, c)]
    if not cells:
        return False
    targets = {k: v for k, v in geometry.access_targets(state, rid, level="doors").items() if k[0:1] == ("door",)}
    targets[eid] = (cells, geometry.access_radius(rec), geometry.is_standable(rec))
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    return eid in geometry.access_report(state, rid, blocked, targets)["ok"]


def safe_floor_item(p, rid, type_name, surfaces=("bed", "desk", "dresser", "table", "bench"), **props):
    """Placer.floor_item (v2.3: it never cuts a hazard-free route or the only access to anything, and items 15 cm
    or taller never stand in doorway clearance or a door leaf's sweep). A room with no such floor cell gets the
    item on a reachable bed/desk/dresser instead (a backpack dropped on the bed), else nothing."""
    new = p.floor_item(rid, type_name, **props)
    if new is not None:
        return new
    for t in surfaces:
        for surface in p.find(t, room=rid):
            if reachable_from_door(p, surface):
                eid = p.on(surface, type_name, **props)
                if eid is not None:
                    return eid
    return None


def _low_or_standing(record):
    height = float(record.get("mount_height_m", 0.0) or 0.0)
    return height < 0.5 or geometry.floor_standing(dict(record, mounted=True))


def mount_cell(p, rid, record, near=None):
    """A wall cell for a wall-mounted record, chosen with the kernel's shared rules (geometry.door_clearance,
    floor_standing, reachable_cells, access_radius): the footprint is inside the room and does not overlap
    floor-standing things or (below counter height) blocking furniture; a doorway-reachable floor cell is within
    access radius (one more cell for mounts at 0.85 m and above); door clearance (doorway cross, the leaf sweeping
    into the room, overhead thresholds) is required for low/floor-standing mounts and preferred for the rest (a
    shelf beside the frame is where the opened leaf rests). `near`: a cell or list of cells to stay close to (a
    toilet's footprint for its paper holder), else a random qualifying cell. None when no wall cell qualifies --
    never Placer.mount's relaxed-reach or last-resort cells (a holder nobody can reach is not furnished)."""
    state = p.state()
    room = p.c["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    height = float(record.get("mount_height_m", 0.0) or 0.0)
    blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
    taken = set()
    for eid in geometry.room_entities(state, rid):
        if geometry.floor_standing(p.c["entities"][eid]):
            taken.update(geometry.footprint(p.c["entities"][eid]))
    reach = geometry.reachable_cells(state, rid, blocked)
    radius = geometry.access_radius(dict(record, mounted=True, mount_height_m=height))
    clearance = geometry.door_clearance(state, rid)
    low = _low_or_standing(record)
    # v2.5 3-D rules (as Placer._mount_cell and the layout audit): a mount never hangs behind a taller
    # floor-standing piece (shower stall, shrub, shelving), and two deep wall mounts never share wall space at
    # overlapping heights (a shelf over a wall-hung basin, a stacked dryer).
    try:
        depth = min(float(v) for v in (record.get("size_m") or [0.1, 0.1, 0.1])[:2])
        top = height + float((record.get("size_m") or [0, 0, 0.1])[2])
    except (TypeError, ValueError):
        depth, top = 0.1, height + 0.1
    hidden_behind, wall_taken = set(), set()
    for eid in geometry.room_entities(state, rid):
        other = p.c["entities"][eid]
        if not other.get("pos"):
            continue
        try:
            o_h = float(other.get("mount_height_m", other.get("z_m", 0.0)) or 0.0)
            o_top = o_h + float((other.get("size_m") or [0, 0, 0])[2])
            o_depth = min(float(v) for v in (other.get("size_m") or [0.1, 0.1, 0.1])[:2])
        except (TypeError, ValueError):
            continue
        if geometry.floor_standing(other) and o_top > height + 0.05:
            hidden_behind.update(geometry.footprint(other))
        elif other.get("mounted") and depth >= 0.1 and o_depth >= 0.1 and o_h < top and height < o_top:
            wall_taken.update(geometry.footprint(other))
    targets = [tuple(c) for c in near] if near and isinstance(near[0], (list, tuple)) else ([tuple(near)] if near else [])
    perimeter = sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} |
                       {(0, y) for y in range(rows)} | {(cols - 1, y) for y in range(rows)})
    options = []
    for pos in perimeter:
        cells = geometry.footprint(dict(record, pos=list(pos)))
        if not cells or not all(geometry.in_grid(room, c) for c in cells):
            continue
        if (low and any(c in taken for c in cells)) or (height < 0.85 and any(c in blocked for c in cells)):
            continue
        in_clearance = any(c in clearance for c in cells)
        if low and in_clearance:
            continue
        if not low and any(c in hidden_behind or c in wall_taken for c in cells):
            continue
        if not (geometry.near_cells(cells, room, radius) & reach):
            continue
        key = min(geometry.chebyshev(c, t) for c in cells for t in targets) if targets else 0
        options.append((in_clearance, key, pos))
    if not options:
        return None
    best = min(o[0] for o in options)
    options = [o for o in options if o[0] == best]
    if targets:
        options.sort()
        return list(options[0][2])
    return list(p.rng.choice([o[2] for o in options]))


def safe_mount(p, rid, type_name, height=None, near=None, eid=None, **props):
    """Placer.mount at a reachable wall cell (see mount_cell); None when the room has no such cell."""
    record = p.reg.make(type_name, rid, **props)
    record["mounted"] = True
    if height is not None:
        record["mount_height_m"] = height
    cell = mount_cell(p, rid, record, near=near)
    if cell is None:
        return None
    return p.mount(rid, type_name, eid=eid, height=height, cell=cell, **props)


def _room_for(p, container, shelf, kg):
    """True if `kg` more fits the shelf's (or surface's) load rating (unrated furniture always takes it)."""
    headroom = shelf_headroom(p.c, container, shelf)
    return headroom is None or headroom + 1e-9 >= kg


def _stock_shelf(p, container, kg, preferred):
    """The first shelf index (preferred first, then lowest up) whose rating still takes `kg`; None if none does.
    Unshelved containers return `preferred` unchanged."""
    heights = p.c["entities"][container].get("shelf_heights")
    if not heights:
        return preferred
    for shelf in [preferred] + [s for s in range(len(heights)) if s != preferred]:
        if shelf is not None and 0 <= shelf < len(heights) and _room_for(p, container, shelf, kg):
            return shelf
    return None


def _planned_mass(p, type_name, units=(), **props):
    """Mass of an item (plus the unit items it will hold) before it is created."""
    total = float(p.reg.make(type_name, "x", **props).get("mass_kg", 0.0) or 0.0)
    for unit_type, n in units:
        total += n * float(p.reg.make(unit_type, "x").get("mass_kg", 0.0) or 0.0)
    return total


def make_part(p, parent, type_name, **props):
    eid = p.make(type_name, parent, **props)
    rec = p.c["entities"][eid]
    rec["relation"] = "part"
    return eid


def install(p, device, slot, type_name, **props):
    """Create a part and seat it in a device slot (furnishing/setup only)."""
    eid = p.make(type_name, device, **props)
    rec = p.c["entities"][eid]
    rec["relation"] = "part"
    rec["slot"] = slot
    p.c["entities"][device]["slots"][slot]["item"] = eid
    return eid


def add_printer(p, surface, rng, kind="multifunction_printer", sheets=None, ink=None, feeder=True, files=None, eid=None):
    printer = p.on(surface, kind, eid=eid)
    if printer is None:
        return None
    ids = {"printer": printer}
    laser = kind == "laser_printer"
    ids["tray"] = make_part(p, printer, "printer_paper_tray", max_sheets=250 if laser else 100,
                            capacity_l=2.0 if laser else 0.95, inner_size_m=[0.222, 0.302, 0.05 if laser else 0.03])
    count = sheets if sheets is not None else rng.choice([0, 15, 40, 60, 80])
    if count:
        stack = p.make("paper_stack", ids["tray"], sheets_count=count, mass_kg=round(0.0045 * count, 4),
                       volume_l=round(0.0062 * count, 4), size_m=[0.216, 0.279, round(0.0001 * count, 4)])
        p.c["entities"][stack]["relation"] = "in"
        ids["paper"] = stack
    ids["output"] = make_part(p, printer, "printer_output_tray")
    ids["glass"] = make_part(p, printer, "scanner_glass")
    if feeder:
        ids["feeder"] = make_part(p, printer, "document_feeder")
    if laser:
        ids["toner"] = install(p, printer, "ink_black", "toner_cartridge", tape_on=False, clip_on=False,
                               remaining=ink if ink is not None else round(rng.uniform(0.2, 0.9), 2), packaged=False)
    else:
        ids["ink_black"] = install(p, printer, "ink_black", "ink_cartridge", tape_on=False, packaged=False,
                                   remaining=ink if ink is not None else round(rng.uniform(0.15, 0.9), 2))
        ids["ink_color"] = install(p, printer, "ink_color", "ink_cartridge_color", tape_on=False, packaged=False,
                                   remaining=ink if ink is not None else round(rng.uniform(0.15, 0.9), 2))
    rec = p.c["entities"][printer]
    rec["files"] = files if files is not None else {
        "shopping_list.pdf": {"title": "Shopping list", "pages": 1, "owner": None, "private": False},
        "school_permission_slip.pdf": {"title": "Field trip permission slip", "pages": 1, "owner": None, "private": False},
    }
    rec["scan_destinations"] = ["Documents/Scans", "Documents/Taxes", "Documents/School"]
    return ids


def add_stationery(p, surface, rng):
    ids = {}
    cup = p.on(surface, "pen_cup", home=surface)
    if cup:
        ids["pen_cup"] = cup
        for i in range(rng.randint(2, 3)):
            pen = p.inside(cup, "pen", home=cup)
            if pen:
                ids.setdefault("pens", []).append(pen)
        marker = p.inside(cup, "marker_pen", home=cup)
        if marker:
            ids["marker"] = marker
    stapler = p.on(surface, "stapler", home=surface)
    if stapler:
        ids["stapler"] = stapler
        install(p, stapler, "magazine", "staple_strip", count=rng.choice([0, 40, 120, 210]))
    return ids


def add_files(p, cabinet, rng, count=3):
    ids = {"folders": {}, "documents": []}
    chosen = rng.sample(FOLDER_SETS, min(count, len(FOLDER_SETS)))
    for name, kind, title in chosen:
        folder = p.inside(cabinet, "file_folder", label="{} folder".format(name.lower()), label_text=name,
                          label_key=name.lower(), shelf=1 if p.c["entities"][cabinet].get("shelf_heights") else None)
        if folder is None:
            continue
        ids["folders"][name] = folder
        order = []
        for year in sorted(rng.sample([2023, 2024, 2025, 2026], rng.randint(1, 2))):
            key = "{}_{}".format(kind, year)
            doc = p.inside(folder, "document", doc_kind=kind, doc_key=key, title="{} {}".format(title, year),
                           label="{} ({})".format(title.lower(), year), date="{}-03-15".format(year),
                           sheets=[[key, i + 1] for i in range(rng.randint(1, 3))], _file_as=name)
            if doc:
                from .common import refresh_paper
                refresh_paper(p.c["entities"][doc])
                order.append(doc)
                ids["documents"].append(doc)
        p.c["entities"][folder]["order"] = order
    return ids


def add_bathroom_consumables(p, rid, rng):
    ids = {}
    # The paper holder goes on the wall beside the toilet (within a seated arm's reach), not by the door.
    toilet = (p.find("toilet", room=rid) or [None])[0]
    near = None
    if toilet and p.c["entities"][toilet].get("location") == rid and p.c["entities"][toilet].get("pos"):
        near = sorted(geometry.footprint(p.c["entities"][toilet]))
    holder = safe_mount(p, rid, "toilet_paper_holder", height=0.66, near=near)
    if holder:
        ids["tp_holder"] = holder
        remaining = rng.choice([0.0, 0.15, 0.5, 0.9])
        roll = install(p, holder, "roll", "toilet_paper_roll", remaining=remaining)
        if remaining <= 0:
            p.c["entities"][roll].update(waste_stream="recycling", label="empty cardboard core", material="cardboard", mass_kg=0.01)
        p.c["entities"][holder]["roll_orientation"] = rng.choice(["over", "over", "under"])
        ids["tp_roll"] = roll
    # Consumables stand on an open wall shelf (visible and reachable without opening a vanity door). 1.2 m clears
    # a wall-hung basin and its faucet (0.85 m + ~0.2 m); mount_cell keeps it off shower stalls and other mounts.
    vanity = safe_mount(p, rid, "wall_shelf", height=1.2, label="bathroom shelf", size_m=[0.8, 0.2, 0.03], shelf_load_kg=12.0)
    if vanity:
        ids["shelf"] = vanity
        basket = p.on(vanity, "spare_roll_basket")
        if basket:
            ids["spare_basket"] = basket
            for _ in range(rng.randint(0, 2)):
                p.inside(basket, "toilet_paper_roll", home=basket)
        tissue = p.on(vanity, "tissue_box", tissues=rng.choice([0, 40, 120]), open=True, tissue_up=True)
        if tissue:
            ids["tissue_box"] = tissue
        soap = p.on(vanity, "soap_dispenser", liquid_l=round(rng.uniform(0.02, 0.3), 3))
        if soap:
            ids["soap"] = soap
    return ids


def add_kitchen_consumables(p, rid, rng):
    ids = {}
    counter = _first_reachable(p, ["counter"], [rid])
    if counter:
        holder = p.on(counter, "paper_towel_holder")
        if holder:
            ids["pt_holder"] = holder
            remaining = rng.choice([0.0, 0.2, 0.7])
            roll = install(p, holder, "roll", "kitchen_paper_roll", remaining=remaining)
            if remaining <= 0:
                p.c["entities"][roll].update(waste_stream="recycling", label="empty cardboard core", material="cardboard", mass_kg=0.01)
            p.c["entities"][holder]["roll_orientation"] = "over"
            ids["pt_roll"] = roll
        soap = p.on(counter, "soap_dispenser", label="dish soap pump", product_tag="dish_soap", liquid="dish_soap",
                    liquid_l=round(rng.uniform(0.03, 0.3), 3))
        if soap:
            ids["dish_soap"] = soap
    table = _first_reachable(p, ["table"], [rid]) or _first_reachable(p, ["dining_table", "table"], ["dining_room"])
    if table:
        holder = p.on(table, "napkin_holder")
        if holder:
            ids["napkin_holder"] = holder
            n = rng.choice([0, 10, 40])
            if n:
                stack = p.inside(holder, "napkin_stack", napkins=n)
                p.c["entities"][holder]["napkins"] = n if stack else 0
    return ids


def add_backstock(p, rng):
    """Household backstock on closet/pantry/garage shelving the robot can stand beside; every item goes on a shelf
    whose load rating still takes it (storage.shelf_headroom), so the robot's first put-away never collapses a
    shelf other furnishers already filled."""
    ids = {}
    backstock_rooms = ["utility_closet", "linen_closet", "pantry", "garage", "basement", "laundry_room"]
    shelf = _first(p, ["closet_shelving", "shelf_unit"], backstock_rooms, reachable=True)
    if shelf is None:
        shelf = _first(p, ["cabinet"], ["bathroom", "kitchen"], reachable=True)
    if shelf is None:
        shelf = _first(p, ["closet_shelving", "shelf_unit"], backstock_rooms)
    if shelf is None:
        return ids
    ids["backstock_shelf"] = shelf
    heights = p.c["entities"][shelf].get("shelf_heights") or []
    upper = 1 if len(heights) > 1 else 0
    for type_name, key, unit, n, preferred in (("toilet_paper_pack", "tp_pack", "toilet_paper_roll", 4, 0),
                                               ("paper_towel_pack", "pt_pack", "kitchen_paper_roll", 2, 0)):
        props = {"pack_count": 4} if type_name == "toilet_paper_pack" else {}
        level = _stock_shelf(p, shelf, _planned_mass(p, type_name, [(unit, n)], **props), preferred)
        if level is None:
            continue
        pack = p.inside(shelf, type_name, shelf=level, home=shelf, **props)
        if pack:
            ids[key] = pack
            for _ in range(n):
                p.inside(pack, unit)
    for type_name, key in (("tissue_box", "tissues"), ("trash_bag_box", "liners"), ("refill_bottle", "soap_refill")):
        level = _stock_shelf(p, shelf, _planned_mass(p, type_name), upper)
        if level is None:
            continue
        eid = p.inside(shelf, type_name, shelf=level, home=shelf)
        if eid:
            ids[key] = eid
    return ids


@furnisher("admin_inventory.household_admin", order=72)
def furnish_admin(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    # office corner
    desk = _first_reachable(p, ["desk"], ["office", "bedroom", "master_bedroom", "guest_room"])
    if desk:
        add_printer(p, desk, rng)
        add_stationery(p, desk, rng)
        tray = p.on(desk, "inbox_tray", designated_for="action", label="action tray")
    cabinet = None
    for rid in p.rooms("office"):
        found = p.find("cabinet", room=rid, where={"label": "filing cabinet"})
        if found:
            cabinet = next((c for c in found if reachable_from_door(p, c)), found[0])
    if cabinet:
        add_files(p, cabinet, rng, count=rng.randint(2, 3))
        level = _stock_shelf(p, cabinet, _planned_mass(p, "paper_ream"), 0)
        if level is not None:
            p.inside(cabinet, "paper_ream", shelf=level)
    # mail and packages
    for rid in p.rooms("entryway")[:1]:
        safe_mount(p, rid, "mail_organizer", height=1.25)
    for rid in p.rooms("front_yard")[:1]:
        safe_mount(p, rid, "curbside_mailbox", height=1.05)
    for rid in p.rooms("mail_room")[:1]:
        safe_mount(p, rid, "apartment_mailbox", height=1.2)
        hook = (p.find("key_hook") or [None])[0]
        if hook:
            p.hang(hook, "key", eid=p.new_id("mailbox_key"), label="mailbox key", opens=["apartment mailbox"])
    # point of use and backstock
    for rid in p.rooms("bathroom") + p.rooms("half_bath"):
        add_bathroom_consumables(p, rid, rng)
    for rid in p.rooms("kitchen")[:1]:
        add_kitchen_consumables(p, rid, rng)
    add_backstock(p, rng)
    # luggage and bags
    store = _first(p, ["wardrobe", "closet"], ["master_bedroom", "bedroom"], reachable=True)
    stored = None
    if store:
        shelved = bool(config["entities"][store].get("shelf_heights"))
        level = _stock_shelf(p, store, _planned_mass(p, "suitcase"), 0) if shelved else None
        if level is not None or not shelved:
            stored = p.inside(store, "suitcase", shelf=level)
    if stored is None:
        for rid in p.rooms("attic")[:1] + p.rooms("basement")[:1]:
            safe_floor_item(p, rid, "suitcase")
    for pid, person in sorted(config.get("people", {}).items()):
        if person.get("role") == "child":
            rooms = [r for r in p.rooms("bedroom")] or p.rooms("living_room")
            if rooms:
                # whose it is shows on its name tag (read-tier name_label), not in what it looks like
                safe_floor_item(p, rooms[0], "backpack", owner=pid, label="child's backpack",
                                name_label=person.get("label", pid).split(" ")[0])
