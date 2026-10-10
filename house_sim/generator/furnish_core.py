"""Core furniture for every room type. Domain furnishers add fixtures, appliances and items."""
from ..kernel.plugins import furnisher
from .place import Placer

BOOK_TITLES = ["Field Guide to Birds", "Cooking at Home", "The Long Winter", "Atlas of the World", "Garden Basics",
               "Mystery at Midnight", "Learn to Knit", "Space Explorers", "History of Bridges", "Poems for Every Day"]


@furnisher("core_furniture", order=10)
def furnish_core(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    rooms = config["rooms"]
    has_children = any(v.get("role") == "child" for v in config.get("people", {}).values())
    for rid in sorted(rooms):
        kind = rooms[rid]["type"]
        if kind == "entryway":
            p.furniture(rid, "shoe_rack")
            p.mount(rid, "coat_hooks", eid=p.new_id("coat_hooks"))
            hook = p.mount(rid, "key_hook")
            p.hang(hook, "key", eid="house_key_1", label="house key", opens=["front door", "back door"])
            if any(r["type"] == "shed" for r in rooms.values()):
                p.hang(hook, "key", eid="shed_key_1", label="shed key")
            if rng.random() < 0.5:
                p.furniture(rid, "bench")
        elif kind == "living_room":
            sofa = p.furniture(rid, "sofa")
            table = p.furniture(rid, "coffee_table", wall=False, near=config["entities"][sofa]["pos"] if sofa else None)
            if rng.random() < 0.7:
                p.furniture(rid, "armchair")
            side = p.furniture(rid, "side_table")
            p.furniture(rid, "tv_stand")
            if rng.random() < 0.7:
                shelf = p.furniture(rid, "bookshelf")
                if shelf:
                    for i in range(rng.randint(3, 8)):
                        p.inside(shelf, "book", text=rng.choice(BOOK_TITLES), home=shelf, shelf=rng.randrange(1, 4))
            if side and rng.random() < 0.7:
                p.on(side, "lamp", on=False)
            if rng.random() < 0.6:
                p.floor_item(rid, "rug", near=config["entities"][table]["pos"] if table else None)
            if has_children:
                p.furniture(rid, "toy_box")
            if table:
                for _ in range(rng.randint(0, 2)):
                    p.on(table, "book", text=rng.choice(BOOK_TITLES))
        elif kind == "kitchen":
            # base runs scale with the room: small galley kitchens get one counter run and fewer base cabinets,
            # and a kitchen table only fits where it leaves walking room (appliances still have to go in)
            area = rooms[rid]["size_m"][0] * rooms[rid]["size_m"][1]
            counters = 2 if area < 20 else rng.randint(2, 3)
            cabinets = 2 if area < 21 else rng.randint(2, 3)    # under-sink plus at least one food-safe cupboard
            for _ in range(counters):
                p.furniture(rid, "counter", fallback=False)
            for n in range(cabinets):
                p.furniture(rid, "cabinet", fallback=False, min_free=None if n == 0 else (0.5 if n == 1 else 0.6))
            for _ in range(2):
                p.mount(rid, "upper_cabinet", height=1.45)
            for i in range(3):
                p.mount(rid, "drawer", height=0.75, label=["utensil drawer", "junk drawer", "towel drawer"][i])
            if not any(r["type"] == "dining_room" for r in rooms.values()) and area >= 17:
                table = p.furniture(rid, "table", wall=False, label="kitchen table", min_free=0.62)
                for _ in range(rng.randint(2, 4) if table else 0):
                    p.furniture(rid, "chair", wall=False, near=config["entities"][table]["pos"], min_free=0.6)
        elif kind == "dining_room":
            table = p.furniture(rid, "dining_table", wall=False)
            for _ in range(rng.randint(4, 6)):
                p.furniture(rid, "chair", wall=False, near=config["entities"][table]["pos"] if table else None)
            p.furniture(rid, "cabinet", label="sideboard", size_m=[1.4, 0.45, 0.85])
        elif kind in ("bedroom", "master_bedroom", "guest_room"):
            # the built-in closet (or wardrobe) claims its wall first, then the bed and the rest
            if rng.random() < 0.6:
                p.mount(rid, "closet", height=0.0, label="closet", blocks_path=False, mounted=True)
            else:
                p.furniture(rid, "wardrobe")
            bed = p.furniture(rid, "bed", size_m=[1.6, 2.0, 0.55] if kind != "bedroom" else [1.0, 2.0, 0.55])
            for n in range(2 if kind != "bedroom" else 1):
                p.furniture(rid, "nightstand", near=config["entities"][bed]["pos"] if bed else None, min_free=0.45 if n else None)
            p.furniture(rid, "dresser", min_free=0.45)
            if kind == "bedroom" and rng.random() < 0.5:
                if p.furniture(rid, "desk", min_free=0.5):
                    p.furniture(rid, "chair", wall=False, min_free=0.45)
            if kind == "bedroom" and has_children and rng.random() < 0.6:
                p.furniture(rid, "toy_box", min_free=0.45)
            if kind == "guest_room":
                rooms[rid]["guest"] = True
        elif kind == "nursery":
            p.furniture(rid, "crib")
            p.furniture(rid, "dresser", label="changing dresser")
            p.furniture(rid, "armchair", label="rocking chair")
        elif kind in ("bathroom", "half_bath"):
            p.furniture(rid, "cabinet", label="vanity cabinet", size_m=[0.8, 0.5, 0.85])
            p.mount(rid, "towel_hook")
            if kind == "bathroom":
                p.mount(rid, "upper_cabinet", height=1.4, label="medicine cabinet", size_m=[0.6, 0.15, 0.7], inner_size_m=[0.55, 0.12, 0.65],
                        shelf_heights=[0.02, 0.24, 0.46], capacity_l=20.0)
        elif kind in ("laundry_room", "shared_laundry"):
            p.furniture(rid, "shelf_unit")      # baskets are placed by textiles_laundry beside the washer
        elif kind == "office":
            desk = p.furniture(rid, "desk")
            p.furniture(rid, "chair", wall=False, near=config["entities"][desk]["pos"] if desk else None, label="office chair")
            p.furniture(rid, "bookshelf")
            p.furniture(rid, "cabinet", label="filing cabinet", size_m=[0.5, 0.6, 1.0])
        elif kind == "playroom":
            p.furniture(rid, "toy_box")
            p.furniture(rid, "bookshelf")
            table = p.furniture(rid, "table", label="play table", size_m=[0.9, 0.6, 0.5], surface_height_m=0.5)
            p.furniture(rid, "chair", wall=False, label="small chair", size_m=[0.35, 0.35, 0.55], surface_height_m=0.3)
        elif kind == "gym":
            p.furniture(rid, "bench", label="workout bench")
            p.furniture(rid, "shelf_unit", label="gym rack")
        elif kind == "craft_room":
            p.furniture(rid, "table", label="hobby table")
            p.furniture(rid, "shelf_unit")
            p.furniture(rid, "cabinet")
        elif kind == "pantry":
            p.mount(rid, "closet_shelving", height=0.0, label="pantry shelves")
        elif kind in ("linen_closet", "utility_closet"):
            p.mount(rid, "closet_shelving", height=0.0, label=kind.replace("_", " ") + " shelves")
        elif kind == "coat_closet":
            # a coat closet is a rod with a hat shelf above it (not a floor-standing shelf tower)
            rod = p.mount(rid, "closet_rod", label="coat rod")
            p.mount(rid, "closet_shelving", height=1.75, label="closet shelf", size_m=[1.0, 0.35, 0.05],
                    shelf_heights=[0.02], cell=p.c["entities"][rod]["pos"] if rod else None)
        elif kind == "basement":
            for _ in range(2):
                p.furniture(rid, "shelf_unit")
            if rng.random() < 0.5:
                p.furniture(rid, "workbench")
        elif kind == "attic":
            for _ in range(rng.randint(2, 4)):
                p.floor_item(rid, "storage_bin")
        elif kind == "garage":
            for _ in range(2):
                p.furniture(rid, "shelf_unit")
            p.furniture(rid, "workbench")
        elif kind == "shed":
            p.furniture(rid, "shelf_unit")
        elif kind == "porch":
            p.furniture(rid, "outdoor_chair")
        elif kind in ("patio", "deck"):
            table = p.furniture(rid, "outdoor_table", wall=False)
            for _ in range(rng.randint(2, 4)):
                p.furniture(rid, "outdoor_chair", wall=False, near=config["entities"][table]["pos"] if table else None)
        elif kind == "balcony":
            p.furniture(rid, "outdoor_chair")
        elif kind in ("lobby", "mail_room"):
            p.furniture(rid, "bench")
