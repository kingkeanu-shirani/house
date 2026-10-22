"""Stock generated kitchens: tools, cookware, dishes, pantry staples, fridge/freezer contents.

Order 72: after core furniture (10), fixtures and appliances (20-60) so that a refrigerator or
freezer from the appliances domain (any container with internal_temp_c) gets realistic
contents. Without one, only shelf-stable food is stocked. Every placement is capacity- and
fit-checked by Placer and silently skipped when there is no room. Shelves are load-checked too (food stays under
FILL of the shelf rating behind admin_inventory's collapse rule, other domains' things included), cookware and pantry
staples avoid the under-sink cabinet and any cupboard holding cleaning chemicals.

Generation 2 (opts["gen"] >= 2, docs/GENERATOR.md; generation-1 houses are unchanged): the kitchen is stocked for the
household that lives there (household_stock: config["household"]["profile"] and each resident's profile) -- no meat in
a vegetarian or vegan household's freezer, no peanut butter where someone has a peanut allergy, gluten-free bread
where someone avoids gluten, a plant milk where someone is allergic to milk or vegan, infant formula (and purees from
six months) for a baby, juice boxes and yogurt cups for children, and a second milk carton for four or more people.
"""
import datetime

from ...generator.place import Placer
from ...kernel.plugins import furnisher
from . import model as M


def _start_date(config):
    try:
        return datetime.datetime.fromisoformat(config.get("clock", {}).get("start", "2026-10-09T10:00:00")).date()
    except ValueError:
        return datetime.date(2026, 10, 9)


def _date_props(config, rng, type_name, registry, days_min, days_max, date_type=None):
    """Printed date fields for a package: hidden ISO truth + regional label text + matching hidden spoilage."""
    start = _start_date(config)
    days = rng.randint(days_min, days_max)
    day = start + datetime.timedelta(days=days)
    props = registry.resolve(type_name)
    dtype = date_type or props.get("date_type", "best_before")
    fmt_state = {"metadata": config.get("metadata", {})}
    life = float(props.get("life_days") or M.D.CATS.get(props.get("food", {}).get("cat", "other"), M.D.CATS["other"]).get("life", 7))
    spoil = max(0.0, min(0.85, 1.0 - (days + 1.0) / max(1.0, life))) if props.get("perishable") else 0.05
    return {"_expires": day.isoformat(), "date_type": dtype, "date_label": M.label_for_date(fmt_state, dtype, day), "_spoil": round(spoil, 4)}


def _cold_units(config, kitchen):
    """(fridge, freezer) container ids in a room (appliance-owned or not)."""
    state = {"rooms": config["rooms"], "entities": config["entities"]}
    from ...kernel.predicates import root_room
    fridge = freezer = None
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        t = rec.get("internal_temp_c")
        if t is None or rec.get("insulated") or not (rec.get("accepts_items") or rec.get("container")):
            continue
        if root_room(dict(state, people={}, pets={}, robot={"room": None}), eid) != kitchen:
            continue
        if t <= -5 and freezer is None:
            freezer = eid
        elif -2 <= t <= 8 and fridge is None:
            fridge = eid
    return fridge, freezer


COOKWARE = ("stock_pot", "saucepan", "frying_pan", "nonstick_pan", "mixing_bowl", "small_mixing_bowl", "colander", "baking_sheet",
            "cutting_board_plastic", "kitchen_scale")
FILL = 0.7      # leave ~30 % of a shelf's rated load for what tasks and residents add later
AREA_FILL = 0.6 # and ~40 % of its floor: cupboards are shared with other domains' things and tasks put items back


def _pile_key(rec):
    """Items that share one footprint on a shelf, as a cook stores them: plates, boards, sheets and bags lie in flat
    piles (one per category), pots nest inside each other, frying pans stack, bowls and colanders nest, empty food
    storage containers nest. None for anything that needs its own floor (appliances, jars, boxes, glasses)."""
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    height = float(size[2]) if len(size) > 2 else 0.1
    if rec.get("stackable") or height <= 0.03:
        return ("flat", rec.get("category"))
    kind, cat = rec.get("type") or "", rec.get("category")
    if cat == "cookware":
        return ("pots",) if height > 0.08 else ("pans",)
    if cat == "dish" and ("bowl" in kind or kind == "colander"):
        return ("bowls",)
    if cat == "food_container" and M.food(rec) is None:
        return ("containers",)
    return None


def _fileable(rec):
    """Sheet pans and cutting boards are filed on edge along a cupboard side (a 46 x 34 cm sheet takes 46 x 2.5 cm of
    shelf floor and 34 cm of height) where the shelf above leaves room."""
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    thin = len(size) > 2 and float(size[2]) <= 0.03
    return thin and (rec.get("tool_kind") == "cutting_board" or rec.get("category") == "bakeware")


def _clearance(parent, shelf):
    """Free height above one shelf (m): up to the next shelf, or the unit's inside top."""
    heights = sorted(float(h) for h in (parent.get("shelf_heights") or [0.0]))
    inner = parent.get("inner_size_m") or [0, 0, 0]
    here = float((parent.get("shelf_heights") or [0.0])[int(shelf)])
    above = [h for h in heights if h > here + 1e-9]
    return (above[0] if above else float(inner[2]) if len(inner) > 2 else 0.3) - here


def _footprint(rec):
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    if rec.get("pack_orientation") == "on_edge":
        return float(size[0]) * max(0.03, float(size[2]))
    return float(size[0]) * float(size[1])


def _shelf_area(entities, container, shelf):
    """Floor area (m2) already taken on one shelf: footprints of the items standing there, each nesting/stacking pile
    (see _pile_key) counted once with its largest footprint."""
    area, piles = 0.0, {}
    for rec in entities.values():
        if rec.get("location") != container or rec.get("relation") != "in" or int(rec.get("shelf", 0) or 0) != int(shelf):
            continue
        footprint = _footprint(rec)
        key = _pile_key(rec)
        if key is not None:
            piles[key] = max(piles.get(key, 0.0), footprint)
        else:
            area += footprint
    return area + sum(piles.values())


def _added_area(entities, container, shelf, record):
    """Extra shelf floor a new item takes: nothing beyond the pile's current footprint when it joins a pile."""
    footprint = _footprint(record)
    key = _pile_key(record)
    if key is None:
        return footprint
    largest = 0.0
    for rec in entities.values():
        if rec.get("location") == container and rec.get("relation") == "in" and int(rec.get("shelf", 0) or 0) == int(shelf) \
                and _pile_key(rec) == key:
            largest = max(largest, _footprint(rec))
    return max(0.0, footprint - largest)


def _chemical_shelf(entities, container, shelf):
    return any(rec.get("location") == container and rec.get("relation") == "in" and int(rec.get("shelf", 0) or 0) == int(shelf)
               and M.is_chemical(rec) for rec in entities.values())


def _put(p, config, registry, rng, container, type_name, shelf=None, strict=False, **props):
    """Placer.inside on shelved furniture with a load check (rated shelves stay under FILL of their rating) and an even
    spread: without a requested shelf the item goes to the least crowded shelf (by floor area), so tall things other
    domains keep on the bottom shelf (kibble bags, appliances) still find floor space instead of being stacked; with
    one, that shelf first, then (unless strict) the least crowded others. None when no shelf can take the mass."""
    parent = config["entities"][container]
    heights = parent.get("shelf_heights")
    if not heights:
        return p.inside(container, type_name, shelf=shelf, **props)
    rating = M.shelf_rating(parent)
    ents = config["entities"]
    resolved = registry.resolve(type_name)
    mass = float(props.get("mass_kg", resolved.get("mass_kg", 0.5)) or 0.5)
    probe = dict(resolved, **{k: v for k, v in props.items() if k in ("size_m", "category", "stackable")})
    probe["type"] = type_name
    inner = parent.get("inner_size_m")
    floor = float(inner[0]) * float(inner[1]) if inner else None
    by_room = sorted(range(len(heights)), key=lambda s: (round(_shelf_area(ents, container, s), 3), -s))
    if shelf is not None:
        order = [int(shelf)] + ([] if strict else [s for s in by_room if s != int(shelf)])
    else:
        order = by_room
    size = probe.get("size_m") or [0.1, 0.1, 0.1]
    for level in order:
        if _chemical_shelf(ents, container, level):
            continue      # never on a shelf with cleaners or detergent
        if rating is not None and M.shelf_load(ents, container, level) + mass > FILL * rating:
            continue
        extra = {}
        if _fileable(probe) and "pack_orientation" not in props and _clearance(parent, level) >= float(size[1]) + 0.005:
            extra["pack_orientation"] = "on_edge"
        probe.pop("pack_orientation", None)
        probe.update(extra)
        if floor is not None and _shelf_area(ents, container, level) + _added_area(ents, container, level, probe) > AREA_FILL * floor:
            continue      # keep shelf floor for what other domains and residents put there (kibble bags, appliances)
        return p.inside(container, type_name, shelf=level, **dict(props, **extra))
    return None


def _food_safe_cupboard(config, eid):
    """Not the under-sink/vanity cabinet (cleaners live there; _put also skips any shelf holding chemicals)."""
    rec = config["entities"][eid]
    return not (rec.get("sink") or "sink" in (rec.get("label") or "") or "vanity" in (rec.get("label") or ""))


def _chemical_free(config, eid):
    return not any(e.get("location") == eid and M.is_chemical(e) for e in config["entities"].values())


def _sub(config, unit, zone):
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        if rec.get("location") == unit and M.sub_zone_of(rec) == zone:
            return eid
    return None


MEATLESS = ("vegetarian", "vegan")


def household_stock(config, opts):
    """Generation 2: {"subs": {type: replacement type or None}, "pantry": [types], "fridge": [types]} for the
    household's diets, allergies and members; empty for generation 1 (so the generation-1 draws are untouched)."""
    out = {"subs": {}, "pantry": [], "fridge": []}
    if int(opts.get("gen") or 1) < 2:
        return out
    people = config.get("people") or {}
    members = ((config.get("household") or {}).get("profile") or {}).get("members") or {}
    diets, allergies, ages = [], set(), []
    for pid in sorted(set(people) | set(members)):
        person = people.get(pid) or {}
        prof = person.get("profile") if isinstance(person.get("profile"), dict) else {}
        member = members.get(pid) or {}
        diet = prof.get("diet") or person.get("diet") or (person.get("preferences") or {}).get("diet")
        if isinstance(diet, str):
            diets.append(diet)
        allergies |= set(prof.get("allergies") or person.get("allergies") or [])
        age = member.get("age", person.get("age"))
        if isinstance(age, (int, float)) and not isinstance(age, bool):
            ages.append(float(age))
    subs = out["subs"]
    if diets and all(d in MEATLESS for d in diets if d not in ("pescatarian",)) and \
            not any(d == "omnivore" for d in diets):
        subs["frozen_chicken_pack"] = "veggie_patties_box"
    if "peanut" in allergies:
        subs["peanut_butter_jar"] = "jam_jar"
    if "gluten" in allergies or "wheat" in allergies or any(d == "gluten_free" for d in diets):
        subs["bread_loaf"] = "gluten_free_bread_loaf"
    if "milk" in allergies or any(d == "vegan" for d in diets):
        subs["milk_carton"] = "oat_milk_carton"
    if "egg" in allergies or any(d == "vegan" for d in diets):
        subs["egg_carton"] = None
    infants = [a for a in ages if a < 1.0]
    if infants:
        out["pantry"].append("baby_formula_can")
        if max(infants) >= 0.5:
            out["pantry"] += ["baby_puree_jar"] * 3          # solids from about six months
    if any(1.0 <= a < 13.0 for a in ages):
        out["pantry"] += ["juice_box"] * 3
        if "milk" not in allergies and "vegan" not in diets:
            out["fridge"] += ["yogurt_snack_cup"] * 4
    if len(people) >= 4 and "milk_carton" not in subs:
        out["fridge"].append("milk_carton")
    return out


def _on_first(p, surfaces, type_name, **props):
    """Put the item on the first surface with room for it (None when none has)."""
    for surface in surfaces:
        eid = p.on(surface, type_name, **props)
        if eid:
            return eid
    return None


@furnisher("food.kitchen", order=72)
def furnish_food(config, rng, opts, registry):
    p = Placer(config, registry, rng)
    ents = config["entities"]
    stock = household_stock(config, opts)
    subs = stock["subs"]
    for kitchen in p.rooms("kitchen"):
        counters = p.find("counter", room=kitchen)
        all_lowers = [c for c in p.find("cabinet", room=kitchen) if ents[c].get("type") == "cabinet"]
        lowers = [c for c in all_lowers if _food_safe_cupboard(config, c)] or all_lowers
        uppers = [c for c in p.find("upper_cabinet", room=kitchen)]
        drawers = p.find("drawer", room=kitchen)
        utensil_drawer = next((d for d in drawers if "utensil" in (ents[d].get("label") or "")), drawers[0] if drawers else None)
        # ---- counter top (prep things on the first counter, serving things on the last; a counter crowded by other
        # domains' appliances and supplies passes the item on to the next counter, so every kitchen gets its knives)
        if counters:
            prep, serve = list(counters), list(reversed(counters))
            _on_first(p, prep, "cutting_board_wood")
            block = _on_first(p, prep, "knife_block")
            if block:
                for knife in ("chef_knife", "paring_knife", "bread_knife", "carving_knife"):
                    p.inside(block, knife)
            bowl = _on_first(p, serve, "fruit_bowl")
            if bowl:
                for item in rng.sample(["apple", "orange", "banana", "lemon", "pear"], 3):
                    p.inside(bowl, item, temperature_c=21.0)
            _on_first(p, serve, "salt_shaker")
            _on_first(p, serve, "pepper_mill")
            crock = _on_first(p, prep, "utensil_crock")
            if crock:
                for tool in ("wooden_spoon", "silicone_spatula", "ladle", "kitchen_tongs", "whisk"):
                    p.inside(crock, tool)
            if rng.random() < 0.6:
                box = _on_first(p, serve, "bread_box")
                loaf = subs.get("bread_loaf", "bread_loaf")
                if box and loaf:
                    p.inside(box, loaf, **_date_props(config, rng, loaf, registry, 2, 5))
        # ---- utensil drawer
        if utensil_drawer:
            for tool in ("vegetable_peeler", "can_opener", "measuring_spoon_tbsp", "measuring_spoon_tsp", "measuring_cup_1",
                         "measuring_cup_half", "table_spoon", "table_spoon", "fork", "fork", "table_knife", "teaspoon",
                         "corkscrew", "box_grater", "bench_scraper"):
                p.inside(utensil_drawer, tool)
        # ---- lower cabinets: cookware and prep
        pantry_cab = None
        homeless = []         # cookware with no room in a base cabinet: wall units or a drawer take it after the staples
        if lowers:
            pots = lowers[0]
            for item in COOKWARE:
                if _put(p, config, registry, rng, pots, item, shelf=0 if item in ("stock_pot", "saucepan") else None) is None:
                    homeless.append(item)
        else:
            homeless = list(COOKWARE)
        # food-contact storage never goes in with the cleaners: a second food-safe cabinet, else a wall unit or drawer
        safe = [c for c in all_lowers if _food_safe_cupboard(config, c)]
        if safe:
            store = safe[-1] if len(safe) > 1 else safe[0]
        else:
            store = next((u for u in reversed(uppers) if _food_safe_cupboard(config, u)), None)
            if store is None:
                store = next((d for d in drawers if d != utensil_drawer and _food_safe_cupboard(config, d)), None)
        if store is not None:
            for item in ("storage_container_medium", "storage_container_medium", "storage_container_small", "storage_container_large",
                         "freezer_bag", "zip_top_bag"):
                _put(p, config, registry, rng, store, item)
        if len(lowers) > 1 and not p.rooms("pantry") and _food_safe_cupboard(config, lowers[-1]) and _chemical_free(config, lowers[-1]):
            pantry_cab = lowers[-1]
            ents[pantry_cab]["label"] = "pantry cabinet"
            ents[pantry_cab]["tags"] = sorted(set(ents[pantry_cab].get("tags", [])) | {"pantry"})
        # ---- upper cabinets: dishes
        if uppers:
            for item in ("dinner_plate", "dinner_plate", "dinner_plate", "side_plate", "side_plate", "cereal_bowl", "cereal_bowl",
                         "soup_bowl", "mug", "mug", "drinking_glass", "drinking_glass", "serving_bowl"):
                _put(p, config, registry, rng, uppers[0], item)
            if len(uppers) > 1:
                for item in ("wine_glass", "platter", "prep_bowl", "prep_bowl"):
                    _put(p, config, registry, rng, uppers[1], item)
        # ---- pantry staples: pantry-room shelving, the pantry cabinet, then any other food-safe cupboard (base units
        # before wall units); heavy bags stay on a bottom shelf, and a full unit passes the item on to the next one
        shelves = []
        for pantry_room in p.rooms("pantry"):
            shelves += [s for s in p.find("closet_shelving", room=pantry_room) if _food_safe_cupboard(config, s)]
        if pantry_cab:
            shelves.append(pantry_cab)
        shelves += [c for c in all_lowers if c not in shelves and _food_safe_cupboard(config, c)]
        shelves += [u for u in reversed(uppers) if u not in shelves and _food_safe_cupboard(config, u)]
        if shelves:
            staples = [("all_purpose_flour", 120, 300), ("sugar_bag", 400, 900), ("salt_canister", 600, 1200), ("rice_bag", 200, 500),
                       ("pasta_box", 200, 600), ("canned_beans", 300, 700), ("canned_tomatoes", 300, 700), ("peanut_butter_jar", 60, 200),
                       ("olive_oil_bottle", 120, 400), ("cereal_box", 40, 200), ("crackers_box", 30, 150), ("honey_jar", 300, 900),
                       ("baking_soda_box", 200, 500), ("baking_powder_can", 120, 400), ("ground_cumin_jar", 200, 700),
                       ("cinnamon_jar", 200, 700), ("vinegar_bottle", 300, 900)]
            # the household's own needs (formula for a baby, children's juice) get shelf room before the generic staples
            staples = [(item, 120, 400) for item in stock["pantry"]] + \
                [(subs.get(item, item), lo, hi) for item, lo, hi in staples if subs.get(item, item)]
            primary = shelves[:max(1, len(shelves) - len([u for u in uppers if u in shelves]))]   # wall units only as overflow
            for i, (item, lo, hi) in enumerate(staples):
                heavy = registry.resolve(item).get("mass_kg", 0.5) > 1.5
                props = _date_props(config, rng, item, registry, lo, hi)
                start_at = i % len(primary)
                for unit in primary[start_at:] + primary[:start_at] + [s for s in shelves if s not in primary]:
                    heights = ents[unit].get("shelf_heights") or [0.0]
                    level = 0 if heavy else rng.randrange(min(3, len(heights)))
                    if _put(p, config, registry, rng, unit, item, shelf=level, strict=heavy, **props):
                        break
        # ---- cookware overflow: another food-safe base unit, a wall unit, a drawer (small kitchens have one base cabinet,
        # usually under the sink with the cleaners)
        spare = [c for c in all_lowers if _food_safe_cupboard(config, c) and c not in lowers[:1]]
        spare += [u for u in reversed(uppers) if _food_safe_cupboard(config, u)]
        spare += [d for d in drawers if d != utensil_drawer and _food_safe_cupboard(config, d)]
        for item in homeless:
            for unit in spare:
                if _put(p, config, registry, rng, unit, item) is not None:
                    break
        # ---- fridge and freezer (appliance-owned; skipped when absent)
        fridge, freezer = _cold_units(config, kitchen)
        if fridge:
            heights = ents[fridge].get("shelf_heights") or [0.0]
            top = len(heights) - 1
            carton = None
            if subs.get("egg_carton", "egg_carton"):
                carton = p.inside(fridge, "egg_carton", shelf=min(1, top),
                                  **_date_props(config, rng, "egg_carton", registry, 15, 30))
            if carton:
                exp = ents[carton].get("_expires")
                for _ in range(rng.randint(6, 10)):
                    p.inside(carton, "hen_egg", _expires=exp, temperature_c=4.0)
            chilled = [("milk_carton", 4, 9, min(1, top)), ("butter_unsalted", 30, 60, top),
                       ("cheddar_block", 20, 60, top), ("yogurt_tub", 6, 18, min(1, top)),
                       ("orange_juice_carton", 4, 10, min(1, top))]
            chilled = [(subs.get(item, item), lo, hi, shelf) for item, lo, hi, shelf in chilled if subs.get(item, item)]
            chilled += [(item, 5, 14, min(1, top)) for item in stock["fridge"]]
            for item, lo, hi, shelf in chilled:
                p.inside(fridge, item, shelf=shelf, temperature_c=4.0, _core_c=4.0, **_date_props(config, rng, item, registry, lo, hi))
            door = _sub(config, fridge, "door")
            for item in ("ketchup_bottle", "mustard_bottle", "mayonnaise_jar"):
                props = _date_props(config, rng, item, registry, 60, 240)
                props.update(opened=True, opened_on=(_start_date(config) - datetime.timedelta(days=rng.randint(3, 25))).isoformat(),
                             temperature_c=4.0)
                p.inside(door or fridge, item, shelf=None if door else top, **props)
            crisper = _sub(config, fridge, "crisper_high")
            for item in ("lettuce", "carrot", "carrot", "bell_pepper"):
                p.inside(crisper or fridge, item, shelf=None if crisper else 0, temperature_c=4.0, _core_c=4.0)
            box = p.inside(fridge, "storage_container_medium", shelf=top)
            if box:
                made = (_start_date(config) - datetime.timedelta(days=rng.randint(1, 2))).isoformat()
                p.inside(box, "chili", food={"qty_g": 900.0, "made_on": made}, mass_kg=0.9, volume_l=0.9, temperature_c=4.0,
                         _core_c=4.0, size_m=[0.19, 0.13, 0.05])
        if freezer:
            for item in ("frozen_peas_bag", "ice_cream_tub", "frozen_chicken_pack"):
                item = subs.get(item, item)
                if item:
                    p.inside(freezer, item, **_date_props(config, rng, item, registry, 60, 240))
