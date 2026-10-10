"""Furnisher: manipulation-domain objects in generated houses.

Runs after core furniture (order 70: small items). Counts stay modest (roughly 10-35 items
per house) and every placement tolerates a full room (Placer returns None).
"""
import random

from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.plugins import furnisher

TOYS = ["toy_block", "toy_car", "doll", "stuffed_animal", "ball", "toy_block"]


# Garage tie-downs and tarps are outdoor_plants types; with that domain absent they are built
# from core types with the props this domain reads.
BUNGEE = {"label": "bungee cord", "category": "strap", "material": "rubber", "mass_kg": 0.12,
          "size_m": [0.6, 0.02, 0.02], "volume_l": 0.1, "hold_kg": 20.0, "tags": ["strap", "tie_down"]}
TARP = {"label": "tarp", "category": "tarp", "material": "polyethylene", "cover_area_m2": 7.2, "breathable": False,
        "mass_kg": 1.2, "size_m": [0.4, 0.3, 0.08], "volume_l": 8.0, "grommets": 12, "wind_lift_mps": 6.0, "tags": ["cover"]}


def _foreign(registry, type_name, props):
    """(type, props) for an item another domain owns: its type when loaded, else portable_object."""
    try:
        if registry.known(type_name):
            extra = {k: v for k, v in props.items() if k not in ("label", "category", "material", "size_m", "volume_l", "mass_kg")}
            return type_name, extra
    except Exception:
        pass
    return "portable_object", dict(props)


def _floor_item(p, rid, type_name, wall=False, **props):
    """A loose item on the floor through Placer.floor_item (kernel v2.3 keep_access rule): its whole footprint is
    free floor next to the walkable area joined to the doorways, it cuts no hazard-free route and nobody's only
    access, and (strict) it never stands in a doorway clearance, a hinged door's leaf sweep or an overhead door's
    threshold. Bulky items (wall=True) are put nearest a wall cell drawn from a private per-house rng, so the
    furnisher's other random choices stay put. Returns None (item left out) when the room has no such spot."""
    near = None
    if wall:
        cols, rows = geometry.grid_size(p.c["rooms"][rid])
        perimeter = sorted({(x, 0) for x in range(cols)} | {(x, rows - 1) for x in range(cols)} |
                           {(0, y) for y in range(rows)} | {(cols - 1, y) for y in range(rows)})
        rng = random.Random("manipulation.floor:{}:{}:{}".format(rid, type_name, len(p.c["entities"])))
        near = rng.choice(perimeter) if perimeter else None
    return p.floor_item(rid, type_name, near=near, strict=True, keep_access=True, **props)


def _first(p, type_name, room):
    found = p.find(type_name, room)
    return found[0] if found else None


def _surface_in(p, room, types):
    for t in types:
        found = p.find(t, room)
        if found:
            return found[0]
    return None


@furnisher("manipulation.items", order=72)
def furnish_manipulation(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    kids = sorted(pid for pid, person in config.get("people", {}).items() if person.get("role") in ("child", "infant", "toddler"))
    for rid in sorted(rooms):
        kind = rooms[rid].get("type")
        if kind == "living_room":
            stand = _surface_in(p, rid, ("tv_stand", "side_table", "coffee_table"))
            if stand:
                p.on(stand, "vase" if rng.random() < 0.6 else "ceramic_vase")
            shelf = _first(p, "bookshelf", rid)
            if shelf:
                p.inside(shelf, "picture_frame", shelf=2, sentimental=rng.random() < 0.4)
                if rng.random() < 0.5:
                    p.inside(shelf, "figurine", shelf=3)
            if kids:
                box = _first(p, "toy_box", rid)
                for _ in range(rng.randint(1, 3)):
                    _floor_item(p, rid, rng.choice(TOYS), wall=True, owner=rng.choice(kids))
                if box:
                    for _ in range(rng.randint(2, 4)):
                        p.inside(box, rng.choice(TOYS), owner=rng.choice(kids))
        elif kind == "kitchen":
            drawers = [d for d in p.find("drawer", rid) if config["entities"][d].get("label") == "junk drawer"]
            if drawers:
                junk = drawers[0]
                for t in ("chip_clip", "chip_clip", "clear_tape", "rubber_bands", "twine_spool"):
                    p.inside(junk, t)
        elif kind in ("master_bedroom", "bedroom", "guest_room"):
            top = _surface_in(p, rid, ("dresser", "nightstand"))
            if top and rng.random() < 0.7:
                p.on(top, "picture_frame", sentimental=True)
            if kind == "master_bedroom" and top and rng.random() < 0.6:
                box = p.on(top, "jewelry_box")
                if box:
                    owner = next((pid for pid, person in sorted(config.get("people", {}).items()) if person.get("role") == "adult"), None)
                    config["entities"][box]["owner"] = owner
                    p.inside(box, "ring", owner=owner)
                    p.inside(box, "earrings_pair", owner=owner)
            if kind == "bedroom" and kids:
                owner = rng.choice(kids)
                for _ in range(rng.randint(0, 2)):
                    _floor_item(p, rid, rng.choice(TOYS), wall=True, owner=owner)
                bed = _first(p, "bed", rid)
                if bed:
                    p.on(bed, "stuffed_animal", owner=owner)
        elif kind == "office":
            desk = _first(p, "desk", rid)
            if desk:
                p.on(desk, "clear_tape")
                p.on(desk, "label_sheet")
        elif kind == "gym":
            rack = p.furniture(rid, "dumbbell_rack")
            if rack:
                rec = config["entities"][rack]
                for weight, slots in ((5.0, ("saddle_5kg_a", "saddle_5kg_b")), (10.0, ("saddle_10kg_a", "saddle_10kg_b"))):
                    pair = "pair_{}kg".format(int(weight))
                    for slot in slots:
                        eid = p.make("dumbbell", rack, label="{:g} kg dumbbell".format(weight), mass_kg=weight, weight_kg=weight,
                                     label_text="{:g} kg".format(weight), pair_id=pair)
                        config["entities"][eid].update(relation="in", inserted={"slot": slot, "seated": True, "latched": False}, seated=True)
                        rec["receptacles"][slot]["item"] = eid
            p.furniture(rid, "weight_bench", wall=False)
            _floor_item(p, rid, "yoga_mat", wall=True)
            hooks = p.mount(rid, "band_hooks", height=1.5)
            for color in ("yellow", "red", "blue"):
                p.hang(hooks, "resistance_band", color=color, resistance={"yellow": "light", "red": "medium", "blue": "heavy"}[color])
            gear = _first(p, "shelf_unit", rid)
            if gear:
                p.inside(gear, "kettlebell", shelf=0)
                p.inside(gear, "foam_roller", shelf=1)
                p.inside(gear, "jump_rope", shelf=2)
        elif kind == "garage":
            shelves = p.find("shelf_unit", rid)
            if shelves:
                p.inside(shelves[0], "furniture_slider_set", shelf=1)
                p.inside(shelves[0], "packing_tape", shelf=1)
                kind, props = _foreign(registry, "bungee_cord", BUNGEE)
                p.inside(shelves[0], kind, shelf=2, **props)
                kind, props = _foreign(registry, "tarp", TARP)
                p.inside(shelves[0], kind, shelf=0, **props)
                if len(shelves) > 1:
                    p.inside(shelves[1], "painters_tape", shelf=1)
                    p.inside(shelves[1], "drop_cloth", shelf=0)
            _floor_item(p, rid, "hand_truck", wall=True)
            if kids:
                _floor_item(p, rid, rng.choice(["basketball", "soccer_ball"]), wall=True)
            for _ in range(rng.randint(1, 2)):
                _floor_item(p, rid, "moving_box_small", wall=True)
        elif kind == "craft_room":
            table = _first(p, "table", rid)
            shelf = _first(p, "shelf_unit", rid)
            if shelf:
                p.inside(shelf, "gift_wrap_roll", shelf=0)
                p.inside(shelf, "ribbon_spool", shelf=1)
                p.inside(shelf, "tissue_paper", shelf=1)
                p.inside(shelf, "gift_box", shelf=2)
            if table:
                p.on(table, "clear_tape")
                p.on(table, "label_sheet")
        elif kind == "playroom":
            shelf = _first(p, "bookshelf", rid)
            if shelf:
                p.inside(shelf, "game_box", shelf=1)
                p.inside(shelf, "jigsaw_puzzle", shelf=2)
            for _ in range(rng.randint(2, 4)):
                _floor_item(p, rid, rng.choice(TOYS), wall=True, owner=rng.choice(kids) if kids else None)
            _floor_item(p, rid, "toy_bin", wall=True)
        elif kind == "attic":
            bins = p.find("storage_bin", rid)
            if bins:
                config["entities"][bins[0]]["label_text"] = "Winter decorations"
                tray = p.inside(bins[0], "ornament_tray")
                if tray:
                    for _ in range(4):
                        p.inside(tray, "glass_ornament")
                p.inside(bins[0], "wreath")
        elif kind == "basement":
            shelves = p.find("shelf_unit", rid)
            if shelves:
                p.inside(shelves[0], "moving_blanket", shelf=0)
                p.inside(shelves[0], "furniture_cover_sheet", shelf=1)
        elif kind == "utility_closet":
            shelves = p.find("closet_shelving", rid)
            if shelves:
                p.inside(shelves[0], "painters_tape", shelf=1)
                p.inside(shelves[0], "plastic_sheeting", shelf=0)
        elif kind == "dining_room":
            board = [c for c in p.find("cabinet", rid) if config["entities"][c].get("label") == "sideboard"]
            if board:
                for _ in range(2):
                    p.inside(board[0], "trivet", shelf=0)
                p.inside(board[0], "table_centerpiece", shelf=1)
        elif kind in ("backyard", "patio", "deck") and kids and rng.random() < 0.6:
            _floor_item(p, rid, rng.choice(["ball", "frisbee", "beach_ball"]), wall=True)
