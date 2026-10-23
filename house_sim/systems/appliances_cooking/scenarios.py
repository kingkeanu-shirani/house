"""Scenario helpers (setup op "helper") for appliance and cooking tasks. All return dicts of bindings.

  appliances_cooking.kitchen      bind (or build) the kitchen's appliances and parts; optional stove kind/unit/brand
  appliances_cooking.cookware     a pot/pan/sheet/dish with realistic material properties (food-domain type when loaded)
  appliances_cooking.food         a cookable food item (food-domain type when loaded, else an equivalent generic item)
  appliances_cooking.pot_on_burner  a vessel with liquid/food on a burner, optionally already hot and lit
  appliances_cooking.place_item   any prop on a surface with fallbacks (trivet, plate, bowl, lidded container)
  appliances_cooking.dishes       a set of dirty dishes (plates, bowls, mugs, glasses, cutlery, pans...) incl. unsafe ones
  appliances_cooking.dishwasher_load  pre-load the dishwasher: clean (finished cycle), dirty_partial, misloaded
  appliances_cooking.fridge_problem   protruding jar / folded gasket / freezer drawer ajar / door left open (+ warm time)
  appliances_cooking.freezer      a standalone freezer with frost, food and hidden 'defrost ok' answer
  appliances_cooking.left_on      a house sweep: forgotten heat sources, intended programs, always-on appliances
  appliances_cooking.gas_smell    an unlit gas burner knob left at LITE and the room already smelling of gas
  appliances_cooking.fault        a hidden appliance fault with its noise signature (plus normal-noise distractors)
  appliances_cooking.timer_running  a resident's timer occupying a kitchen timer channel
"""
import copy

from ...generator.place import Placer
from ...kernel import geometry
from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from ...kernel.predicates import root_room
from . import build as B
from . import common as C

COOKWARE = {
    "saucepan": {"food_type": "saucepan", "label": "2 L stainless saucepan", "material": "stainless_steel", "capacity_l": 2.0,
                 "inner_size_m": [0.18, 0.18, 0.09], "size_m": [0.38, 0.19, 0.1], "mass_kg": 1.2, "openable": True, "open": True},
    "stock_pot": {"food_type": "stock_pot", "label": "8 L stock pot", "material": "stainless_steel", "capacity_l": 8.0,
                  "inner_size_m": [0.26, 0.26, 0.15], "size_m": [0.3, 0.27, 0.17], "mass_kg": 2.5, "openable": True, "open": True, "two_handed": True},
    "frying_pan": {"food_type": "frying_pan", "label": "28 cm stainless frying pan", "material": "stainless_steel", "capacity_l": 2.5,
                   "inner_size_m": [0.26, 0.26, 0.04], "size_m": [0.48, 0.28, 0.05], "mass_kg": 1.3},
    "nonstick_pan": {"food_type": "nonstick_pan", "label": "nonstick frying pan", "material": "aluminum", "coating": "nonstick", "capacity_l": 2.0,
                     "inner_size_m": [0.24, 0.24, 0.04], "size_m": [0.45, 0.26, 0.05], "mass_kg": 0.9, "handle_material": "plastic"},
    "aluminum_pan": {"food_type": None, "label": "aluminum frying pan", "material": "aluminum", "capacity_l": 2.0,
                     "inner_size_m": [0.24, 0.24, 0.04], "size_m": [0.45, 0.26, 0.05], "mass_kg": 0.7},
    "cast_iron_skillet": {"food_type": None, "label": "cast-iron skillet", "material": "cast_iron", "capacity_l": 2.5, "seasoned": True,
                          "inner_size_m": [0.25, 0.25, 0.05], "size_m": [0.45, 0.27, 0.06], "mass_kg": 2.8, "specific_heat": 460.0},
    "dutch_oven": {"food_type": "dutch_oven", "label": "5.5 L dutch oven", "material": "cast_iron", "capacity_l": 5.5,
                   "inner_size_m": [0.26, 0.26, 0.11], "size_m": [0.32, 0.28, 0.15], "mass_kg": 5.2, "openable": True, "open": True, "two_handed": True},
    "baking_sheet": {"food_type": "baking_sheet", "label": "baking sheet", "material": "aluminum", "container": False, "surface": True,
                     "surface_size_m": [0.45, 0.33], "size_m": [0.46, 0.34, 0.025], "mass_kg": 0.8, "category": "bakeware"},
    "cake_pan": {"food_type": "cake_pan", "label": "23 cm round cake pan", "material": "aluminum", "capacity_l": 2.0,
                 "inner_size_m": [0.23, 0.23, 0.05], "size_m": [0.24, 0.24, 0.05], "mass_kg": 0.4, "category": "bakeware"},
    "glass_casserole": {"food_type": "casserole_dish", "label": "glass casserole dish", "material": "glass", "fragile": True, "capacity_l": 3.0,
                        "inner_size_m": [0.3, 0.2, 0.05], "size_m": [0.35, 0.22, 0.06], "mass_kg": 1.8, "specific_heat": 840.0, "category": "bakeware"},
    "broiler_pan": {"food_type": None, "label": "broiler pan", "material": "steel", "capacity_l": 2.0, "inner_size_m": [0.36, 0.26, 0.04],
                    "size_m": [0.38, 0.28, 0.05], "mass_kg": 1.5, "category": "bakeware", "broiler_safe": True},
    "roasting_pan": {"food_type": None, "label": "roasting pan", "material": "stainless_steel", "capacity_l": 6.0,
                     "inner_size_m": [0.38, 0.28, 0.07], "size_m": [0.42, 0.3, 0.08], "mass_kg": 2.0, "category": "bakeware", "two_handed": True},
    "plastic_handle_pan": {"food_type": None, "label": "skillet with plastic handle", "material": "aluminum", "coating": "nonstick",
                           "handle_material": "plastic", "capacity_l": 2.0, "inner_size_m": [0.24, 0.24, 0.04], "size_m": [0.45, 0.26, 0.05], "mass_kg": 0.9},
}
FOODS = {
    "chicken_breast": {"food_type": "chicken_breast", "label": "chicken breast", "size_m": [0.18, 0.09, 0.03], "mass_kg": 0.25,
                       "food": {"kind": "chicken_breast", "cat": "poultry", "phase": "solid", "qty_g": 250.0, "raw": True, "needs_cooking": True}},
    "whole_chicken": {"food_type": "whole_chicken", "label": "whole chicken", "size_m": [0.25, 0.18, 0.12], "mass_kg": 1.8,
                      "food": {"kind": "whole_chicken", "cat": "poultry", "phase": "solid", "qty_g": 1800.0, "raw": True, "needs_cooking": True}},
    "steak": {"food_type": "beef_steak", "label": "beef steak", "size_m": [0.18, 0.1, 0.025], "mass_kg": 0.3,
              "food": {"kind": "beef_steak", "cat": "meat", "phase": "solid", "qty_g": 300.0, "raw": True, "needs_cooking": True}},
    "salmon": {"food_type": "salmon_fillet", "label": "salmon fillet", "size_m": [0.2, 0.1, 0.03], "mass_kg": 0.35,
               "food": {"kind": "salmon_fillet", "cat": "fish", "phase": "solid", "qty_g": 350.0, "raw": True, "needs_cooking": True}},
    "bread_slice": {"food_type": "bread_slice", "label": "slice of bread", "size_m": [0.11, 0.11, 0.012], "mass_kg": 0.034,
                    "food": {"kind": "bread_slice", "cat": "bread", "phase": "solid", "qty_g": 34.0}, "max_touch_temp_c": 130.0},
    "thick_bread": {"food_type": None, "label": "thick slice of sourdough", "size_m": [0.13, 0.11, 0.04], "mass_kg": 0.09,
                    "food": {"kind": "bread_slice", "cat": "bread", "phase": "solid", "qty_g": 90.0}, "max_touch_temp_c": 110.0},
    "cake_batter": {"food_type": None, "label": "cake batter", "size_m": [0.22, 0.22, 0.04], "mass_kg": 0.9, "volume_l": 0.9,
                    "food": {"kind": "cake_batter", "cat": "mixture", "phase": "paste", "qty_g": 900.0}, "cook_profile": "cake"},
    "cookie_dough": {"food_type": None, "label": "cookie dough balls", "size_m": [0.3, 0.2, 0.012], "mass_kg": 0.4,
                     "food": {"kind": "cookie_dough", "cat": "dough", "phase": "solid", "qty_g": 400.0, "pieces": 12, "piece_mm": [40, 40, 12]},
                     "cook_profile": "cookies"},
    "vegetables": {"food_type": None, "label": "cut vegetables", "size_m": [0.3, 0.22, 0.03], "mass_kg": 0.6,
                   "food": {"kind": "mixed_vegetables", "cat": "vegetable", "phase": "pieces", "qty_g": 600.0, "pieces": 30, "piece_mm": [30, 30, 25]}},
    "soup": {"food_type": "soup", "label": "vegetable soup", "size_m": [0.12, 0.12, 0.04], "mass_kg": 0.5, "volume_l": 0.5,
             "food": {"kind": "soup", "cat": "prepared", "phase": "liquid", "qty_g": 500.0, "cooked": True}},
    "casserole": {"food_type": None, "label": "baked pasta casserole", "size_m": [0.28, 0.18, 0.05], "mass_kg": 1.6, "volume_l": 1.6,
                  "food": {"kind": "lasagna", "cat": "prepared", "phase": "solid", "qty_g": 1600.0, "cooked": True}, "cook_profile": "casserole"},
    "pine_nuts": {"food_type": None, "label": "pine nuts", "size_m": [0.12, 0.12, 0.01], "mass_kg": 0.06,
                  "food": {"kind": "pine_nuts", "cat": "nut", "phase": "pieces", "qty_g": 60.0}, "cook_profile": "nuts"},
    "bacon": {"food_type": None, "label": "bacon strips", "size_m": [0.25, 0.14, 0.004], "mass_kg": 0.2,
              "food": {"kind": "bacon", "cat": "meat", "phase": "solid", "qty_g": 200.0, "raw": True, "needs_cooking": True}, "cook_profile": "bacon"},
    "egg_in_shell": {"food_type": "hen_egg", "label": "egg", "size_m": [0.06, 0.045, 0.045], "mass_kg": 0.057,
                     "food": {"kind": "egg", "cat": "egg", "phase": "solid", "qty_g": 57.0, "parts": ["shell"], "raw": True}},
}
DISH_SPECS = {
    "plate": {"food_type": "dinner_plate", "label": "dinner plate", "material": "ceramic", "size_m": [0.27, 0.27, 0.025], "mass_kg": 0.6,
              "fragile": True, "category": "dish", "accepts_items": True, "surface": True, "surface_size_m": [0.26, 0.26]},
    "bowl": {"food_type": "cereal_bowl", "label": "cereal bowl", "material": "ceramic", "size_m": [0.16, 0.16, 0.065], "mass_kg": 0.35,
             "fragile": True, "category": "dish", "accepts_items": True, "container": True, "capacity_l": 0.7, "inner_size_m": [0.15, 0.15, 0.06]},
    "mug": {"food_type": "mug", "label": "mug", "material": "ceramic", "size_m": [0.12, 0.09, 0.1], "mass_kg": 0.35, "category": "dish",
            "accepts_items": True, "container": True, "capacity_l": 0.35, "inner_size_m": [0.08, 0.08, 0.09], "fragile": True},
    "glass": {"food_type": "drinking_glass", "label": "drinking glass", "material": "glass", "size_m": [0.08, 0.08, 0.13], "mass_kg": 0.25,
              "category": "dish", "accepts_items": True, "container": True, "capacity_l": 0.35, "inner_size_m": [0.07, 0.07, 0.12], "fragile": True},
    "fork": {"food_type": "fork", "label": "fork", "material": "stainless_steel", "size_m": [0.19, 0.025, 0.01], "mass_kg": 0.05,
             "category": "kitchen_tool", "tool_kind": "fork"},
    "spoon": {"food_type": "table_spoon", "label": "soup spoon", "material": "stainless_steel", "size_m": [0.19, 0.04, 0.02], "mass_kg": 0.05,
              "category": "kitchen_tool", "tool_kind": "spoon"},
    "table_knife": {"food_type": "table_knife", "label": "table knife", "material": "stainless_steel", "size_m": [0.22, 0.02, 0.01], "mass_kg": 0.06,
                    "category": "kitchen_tool", "tool_kind": "table_knife"},
    "chef_knife": {"food_type": "chef_knife", "label": "chef's knife (20 cm)", "material": "stainless_steel", "size_m": [0.33, 0.05, 0.02],
                   "mass_kg": 0.22, "category": "kitchen_tool", "tool_kind": "chef_knife", "hazards": ["sharp"], "safe_grips": ["handle"],
                   "fine_edge": True, "sharpness": 1.0},
    "cast_iron_pan": {"food_type": None, "label": "cast-iron skillet", "material": "cast_iron", "size_m": [0.45, 0.27, 0.06], "mass_kg": 2.8,
                      "category": "cookware", "conducts_heat": True, "accepts_items": True, "container": True, "capacity_l": 2.5, "seasoned": True,
                      "inner_size_m": [0.25, 0.25, 0.05], "seasoning": 1.0},
    "wooden_board": {"food_type": "cutting_board_wood", "label": "wooden cutting board", "material": "wood", "size_m": [0.45, 0.3, 0.03],
                     "mass_kg": 2.2, "category": "kitchen_tool", "accepts_items": True, "surface": True, "surface_size_m": [0.45, 0.3]},
    "plastic_container": {"food_type": None, "label": "plastic food container", "material": "plastic", "size_m": [0.18, 0.12, 0.07],
                          "mass_kg": 0.1, "category": "food_container", "accepts_items": True, "container": True, "capacity_l": 1.0,
                          "inner_size_m": [0.17, 0.11, 0.06]},
    "saucepan": {"food_type": "saucepan", "label": "2 L saucepan", "material": "stainless_steel", "size_m": [0.38, 0.19, 0.1], "mass_kg": 1.2,
                 "category": "cookware", "conducts_heat": True, "accepts_items": True, "container": True, "capacity_l": 2.0,
                 "inner_size_m": [0.18, 0.18, 0.09]},
    "baking_sheet": {"food_type": "baking_sheet", "label": "baking sheet", "material": "aluminum", "size_m": [0.46, 0.34, 0.025], "mass_kg": 0.8,
                     "category": "bakeware", "conducts_heat": True, "accepts_items": True, "surface": True, "surface_size_m": [0.45, 0.33]},
    "insulated_mug": {"food_type": None, "label": "insulated travel mug", "material": "stainless_steel", "insulated": True,
                      "size_m": [0.09, 0.09, 0.2], "mass_kg": 0.35, "category": "dish", "accepts_items": True, "container": True, "capacity_l": 0.45,
                      "inner_size_m": [0.07, 0.07, 0.18]},
    "wine_glass": {"food_type": "wine_glass", "label": "wine glass", "material": "glass", "size_m": [0.09, 0.09, 0.2], "mass_kg": 0.2,
                   "category": "dish", "accepts_items": True, "container": True, "capacity_l": 0.4, "inner_size_m": [0.08, 0.08, 0.1], "fragile": True},
}
SOIL_BY_DISH = {"plate": {"food": 0.4, "grease": 0.2}, "bowl": {"food": 0.35}, "mug": {"stain_coffee": 0.3}, "glass": {"fingerprints": 0.2, "food": 0.1},
                "fork": {"food": 0.3}, "spoon": {"food": 0.3}, "table_knife": {"food": 0.25, "grease": 0.1}, "chef_knife": {"food": 0.2},
                "cast_iron_pan": {"grease": 0.4, "food": 0.3}, "wooden_board": {"food": 0.3}, "plastic_container": {"food": 0.3, "grease": 0.2},
                "saucepan": {"food": 0.4, "burnt": 0.3}, "baking_sheet": {"grease": 0.4, "burnt": 0.4}, "insulated_mug": {"stain_coffee": 0.4},
                "wine_glass": {"fingerprints": 0.2, "stain_wine": 0.2}}


def _type_or_generic(ctx, spec, cfg_props=None):
    spec = copy.deepcopy(spec)
    food_type = spec.pop("food_type", None)
    props = dict(spec)
    props.update(copy.deepcopy(cfg_props or {}))
    if food_type and ctx.reg.known(food_type):
        keep = {k: v for k, v in props.items() if k in (cfg_props or {}) or k in ("soil", "temperature_c", "wetness", "cook_profile",
                                                                                  "max_touch_temp_c")}
        if "food" in props and "food" in (cfg_props or {}):
            keep["food"] = props["food"]
        return food_type, keep
    return "generic_item", props


def spare_surfaces(ctx, near_surface):
    """Other reachable work surfaces in the same room (counters first, then tables/islands), then tables in the
    adjoining rooms, for when the requested surface is already full (other domains furnish counters too)."""
    state = ctx.state()
    room = root_room(state, near_surface) if near_surface in ctx.config["entities"] else near_surface
    ents = ctx.config["entities"]
    neighbours = sorted({r for conn in ctx.config.get("connections", {}).values() if room in (conn.get("rooms") or [])
                         for r in conn["rooms"] if r != room and not ctx.config["rooms"].get(r, {}).get("outdoor")})
    out = []
    for eid in sorted(ents):
        rec = ents[eid]
        loc = rec.get("location")
        if eid == near_surface or loc not in [room] + neighbours or not rec.get("surface") or rec.get("ac_kind"):
            continue
        t = str(rec.get("type") or "")
        if not any(w in t for w in ("counter", "island", "table", "cart")) or "bedside" in t or "night" in t                 or C.num(rec.get("surface_height_m"), 0.9) < 0.6:
            continue
        out.append((loc != room, (0 if "counter" in t or "island" in t else 1), not C.approachable(state, eid), eid))
    return [e for _, _, _, e in sorted(out)]


def free_area(ctx, surface):
    """Unoccupied top area of a surface (m2)."""
    ents = ctx.config["entities"]
    rec = ents[surface]
    dims = rec.get("surface_size_m") or (rec.get("size_m") or [0.5, 0.5])[:2]
    used = 0.0
    for k in sorted(ents):
        r = ents[k]
        if r.get("location") == surface and r.get("relation") == "on":
            fp = geometry.footprint_m(r)
            used += fp[0] * fp[1]
    return float(dims[0]) * float(dims[1]) - used


def place_on_any(ctx, surfaces, type_name, eid=None, spos=None, **props):
    """Put a new item on the first surface with room: the requested ones, then spare counters/tables in that room."""
    p = ctx.placer
    tried = [s for s in surfaces if s]
    for s in tried:
        new = p.on(s, type_name, eid=eid, spos=spos, **props)
        if new:
            return new
    for s in spare_surfaces(ctx, tried[0]) if tried else []:
        if s in tried:
            continue
        new = p.on(s, type_name, eid=eid, **props)
        if new:
            return new
    return None


def _place(ctx, type_name, props, on=None, into=None, eid=None, shelf=None, floor=None, spos=None):
    p = ctx.placer
    if on:
        return place_on_any(ctx, [on], type_name, eid=eid, spos=spos, **props)
    if into:
        return p.inside(into, type_name, eid=eid, shelf=shelf, **props)
    if floor:
        return p.floor_item(floor, type_name, eid=eid, **props)
    raise SpecError("say where to put it (on/into/floor)")


def _kitchen_room(ctx, room=None):
    if room:
        return room
    rooms = ctx.placer.rooms("kitchen")
    if not rooms:
        raise SpecError("appliances_cooking: the house has no kitchen")
    return rooms[0]


def _remove(ctx, eid):
    doomed = [eid]
    changed = True
    ents = ctx.config["entities"]
    while changed:
        changed = False
        for k, r in list(ents.items()):
            if r.get("location") in doomed and k not in doomed:
                doomed.append(k)
                changed = True
    for k in doomed:
        ents.pop(k, None)


def _first(ctx, kind, room):
    state = ctx.state()
    for eid in sorted(ctx.config["entities"]):
        rec = ctx.config["entities"][eid]
        if rec.get("ac_kind") == kind and root_room(state, eid) == room:
            return eid
    return None


def reachable_stand(ctx, eid):
    """A free floor cell next to an entity's anchor (for robot ops)."""
    state = ctx.state()
    state["robot"] = {"room": None, "hands": {}, "pos": None}
    room, cells = geometry.anchor(state, eid)
    if room is None or not cells:
        return None
    blocked = geometry.blocked_cells(state, room)
    near = sorted(c for c in geometry.near_cells(cells, ctx.config["rooms"][room]) - set(cells) if c not in blocked)
    return list(near[0]) if near else None


# ----------------------------------------------------------------------------------------------
# keeping a furnished kitchen workable with every domain loaded. Kernel v2.3 keeps loose floor items off routes and
# door clearance, and people step out of chokepoints at episode start; pets do not, so a pet lying in the only gap to
# a drawer or the fridge after this helper rebuilt the kitchen moves to a cell that cuts nothing (Placer.agent_cell).

def _settle_pets(ctx, room):
    """Pets of the room standing where they cut a route or the only access to something move to the nearest cell
    that cuts nothing (the kernel's keep_access rule, as the generator's final repair does). Returns [(pet, cell)]."""
    moved = []
    pets = ctx.config.get("pets") or {}
    for pid in sorted(pets):
        pet = pets[pid]
        if pet.get("location") != room or not pet.get("pos"):
            continue
        here = tuple(pet["pos"])
        pet["pos"] = None                          # off the floor while checking its own cell
        state = ctx.state()
        ok = here not in geometry.door_clearance(state, room) and             geometry.free_floor_cell(state, room, near=here, keep_access=True, clearance=False) == list(here)
        cell = list(here) if ok else ctx.placer.agent_cell(room, near=here)
        pet["pos"] = list(cell) if cell else list(here)
        if cell and tuple(cell) != here:
            moved.append((pid, list(cell)))
    return moved


def _container_room_anchor(state, eid):
    """The outermost entity of an item's container chain (the piece of furniture it is stored in or on)."""
    chain = C.parent_chain(state, eid)
    return chain[-1] if chain else eid


def _relocate(ctx, eid, into=None, onto=()):
    """Move an existing setup entity into a container or onto the first surface with room. True when moved."""
    ents = ctx.config["entities"]
    rec = ents[eid]
    saved = {k: copy.deepcopy(rec.get(k)) for k in ("location", "relation", "spos", "shelf", "pos")}

    def restore():
        for k, v in saved.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v

    if into:
        parent = ents[into]
        shelf = B.shelf_with_room(ctx.placer, into, B.config_mass(ents, eid), share=0.8) if parent.get("shelf_heights") else None
        has_room = shelf is not None or not parent.get("shelf_heights")
        if has_room and geometry.fits_inside(rec.get("size_m") or [0.1, 0.1, 0.1], parent.get("inner_size_m")):
            rec["location"], rec["relation"] = into, "in"
            for k in ("spos", "pos", "shelf"):
                rec.pop(k, None)
            if parent.get("shelf_heights"):
                rec["shelf"] = shelf
            return True
    for s in [x for x in onto if x]:
        rec["location"], rec["relation"] = s, "on"
        for k in ("spos", "pos", "shelf"):
            rec.pop(k, None)
        spot = geometry.surface_spot(ctx.state(), s, eid)
        if spot is not None:
            rec["spos"] = spot
            return True
        restore()
    restore()
    return False


def _ensure_reachable(ctx, eid, drawer=None, counters=()):
    """Stored tools the plans fetch (mitts, thermometer, pods) must sit in/on furniture the robot can get to."""
    if not eid or eid not in ctx.config["entities"]:
        return eid
    state = ctx.state()
    anchor = _container_room_anchor(state, eid)
    if C.approachable(state, anchor):
        return eid
    if drawer and drawer != anchor and C.approachable(state, drawer) and _relocate(ctx, eid, into=drawer):
        ctx.config["entities"][eid]["home"] = drawer
        return eid
    surfaces = list(counters) + spare_surfaces(ctx, counters[0]) if counters else []
    if _relocate(ctx, eid, onto=surfaces):
        ctx.config["entities"][eid].pop("home", None)
    return eid


DISH_LOAD_KG = 8.0      # a cabinet that receives dishes from the dishwasher must take ~8 kg more on its shelf


def _shelf_headroom(ctx, cab, shelf=0):
    rec = ctx.config["entities"][cab]
    return B.shelf_rating(rec) - B.shelf_load(ctx.config["entities"], cab, shelf)


def _make_shelf_room(ctx, cab, need_kg=DISH_LOAD_KG):
    """Stored things other furnishers piled on a cabinet's bottom shelf move up to its other shelves (heaviest first)
    so the shelf can take `need_kg` more without exceeding its rating. Returns the headroom left on shelf 0."""
    ents = ctx.config["entities"]
    rec = ents.get(cab) or {}
    heights = rec.get("shelf_heights") or []
    if len(heights) < 2:
        return _shelf_headroom(ctx, cab)
    rating = B.shelf_rating(rec)
    items = sorted((k for k in ents if ents[k].get("location") == cab and ents[k].get("relation") == "in"
                    and int(ents[k].get("shelf", 0) or 0) == 0), key=lambda k: (-B.config_mass(ents, k), k))
    for k in items:
        if _shelf_headroom(ctx, cab) >= need_kg:
            break
        kg = B.config_mass(ents, k)
        for shelf in range(1, len(heights)):
            if B.shelf_load(ents, cab, shelf) + kg <= 0.8 * rating:
                ents[k]["shelf"] = shelf
                break
    return _shelf_headroom(ctx, cab)


def _bound_ids(ctx, extra=()):
    """Entity ids the task's setup has bound so far (they must stay where the setup put them)."""
    ents = ctx.config["entities"]
    ids = set(x for x in extra if isinstance(x, str))
    for v in (getattr(ctx, "bindings", None) or {}).values():
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and x in ents:
                ids.add(x)
    return ids


def _make_counter_room(ctx, counters, type_name, bound=(), eid=None, **props):
    """Put a countertop appliance (or a dish, a prop) on a counter, first moving loose clutter (bowls, boxes, racks
    other domains left there) from the emptiest reachable counter to a table/spare surface or a cabinet when every
    counter is full."""
    p = ctx.placer
    for c in counters:
        new = p.on(c, type_name, eid=eid, **props)
        if new:
            return new
    ents = ctx.config["entities"]
    state = ctx.state()
    spares = [s for s in spare_surfaces(ctx, counters[0]) if s not in counters] if counters else []
    stores = [c for c in p.find("cabinet", room=root_room(state, counters[0])) if C.approachable(state, c)] if counters else []
    for c in sorted(counters, key=lambda k: (not C.approachable(state, k), -free_area(ctx, k), k)):
        clutter = []
        for k in sorted(ents):
            r = ents[k]
            if r.get("location") != c or r.get("relation") != "on" or k in bound or r.get("ac_kind") or not r.get("portable", True):
                continue
            if r.get("category") in ("appliance",) or r.get("_dw_state") or C.is_food(r):
                continue     # appliances, set-out dishes and food stay
            inside = [x for x in C.descendants(state, k)]
            if any(x in bound or ents[x].get("ac_kind") or ents[x].get("category") == "appliance" or ents[x].get("_dw_state")
                   for x in inside):
                continue     # a holder (knife block, fruit bowl, bread box) moves with its contents unless they matter
            fp = geometry.footprint_m(r)
            clutter.append((-(fp[0] * fp[1]), k))
        for _, k in sorted(clutter):
            if not (_relocate(ctx, k, onto=spares) or any(_relocate(ctx, k, into=s) for s in stores)):
                continue
            new = p.on(c, type_name, eid=eid, **props)
            if new:
                return new
    return None


def _build_on_counters(ctx, counters, build, type_name, bound=()):
    """Run a multi-part countertop builder on the first counter with room, else make room on one (loose clutter moves
    to a table or a cabinet) and build there. Returns the builder's dict or None."""
    for c in counters:
        built = build(c)
        if built:
            return built
    if not counters:
        return None
    probe = _make_counter_room(ctx, counters, type_name, bound)
    if probe is None:
        return None
    where = ctx.config["entities"][probe]["location"]
    _remove(ctx, probe)
    return build(where)


@scenario_helper("appliances_cooking.kitchen")
def kitchen(ctx, room=None, stove=None, unit=None, dishwasher=True, brand=None, oven_panel=None, oven_unit=None, microwave=True,
            toaster=False, kettle=False, coffee_maker=False, mitts=True, thermometer=False, timer=False, pods=True, fridge=True,
            auto_reignite=None, dishwasher_rules=None):
    room = _kitchen_room(ctx, room)
    cfg = ctx.config
    ents = cfg["entities"]
    p = ctx.placer
    if stove == "gas":          # a gas range implies a gas service to the house
        util = cfg.setdefault("utilities", {})
        util["gas"] = True
        util["main_gas_open"] = True
    region = cfg.get("metadata", {}).get("region", "us")
    unit = unit or oven_unit or ("F" if region == "us" else "C")
    out = {"kitchen": room}
    rng_id = _first(ctx, "range", room)
    old_spot = None
    if rng_id and stove and (ents[rng_id].get("cooktop") or {}).get("kind") != stove:
        hood = next((k for k, r in ents.items() if r.get("over") == rng_id), None)
        if hood:
            ents.pop(hood, None)
        old_spot = ents[rng_id].get("pos") if ents[rng_id].get("location") == room else None
        _remove(ctx, rng_id)
        rng_id = None
    oven_id = None
    if rng_id is None:
        kind = stove or ("gas" if region in ("jp", "ca") else "induction" if region in ("uk", "eu") else "electric_coil")
        counters = B._counters(p, room)
        # the new range goes where the old one stood (same cells: no route or approach changes after the furnishers ran)
        near = old_spot or (ents[counters[0]]["pos"] if counters else None)
        built = B.build_range(p, room, kind=kind, unit=unit, near=near)
        if built is None:
            raise SpecError("appliances_cooking.kitchen: no room for a cooktop")
        rng_id = built["range"]
    out["range"] = rng_id
    burners = sorted(k for k, r in ents.items() if r.get("location") == rng_id and r.get("ac_kind") == "burner")
    out["burners"] = burners
    for b in burners:
        out["burner_" + str(ents[b].get("burner_pos", "")).lower()] = b
    out["stove_kind"] = (ents[rng_id].get("cooktop") or {}).get("kind")
    if auto_reignite is not None:
        ents[rng_id]["cooktop"] = dict(ents[rng_id].get("cooktop") or {}, auto_reignite=bool(auto_reignite))
    oven_id = next((k for k, r in ents.items() if r.get("ac_kind") == "oven" and (r.get("location") == rng_id or
                                                                                    root_room(ctx.state(), k) == room)), None)
    if oven_id:
        out["oven"] = oven_id
        if unit and (ents[oven_id].get("oven") or {}).get("unit") != unit:
            ents[oven_id]["oven"] = dict(ents[oven_id]["oven"], unit=unit)
            ents[oven_id]["setting_options"]["temp"] = dict(B.OVEN_TEMP_OPTIONS[unit])
            ents[oven_id]["settings"]["temp"] = B.OVEN_DEFAULT_TEMP[unit]
        if oven_panel:
            ents[oven_id]["oven"] = dict(ents[oven_id]["oven"], panel=oven_panel)
        racks = sorted((k for k, r in ents.items() if r.get("location") == oven_id and r.get("ac_kind") == "oven_rack"),
                       key=lambda k: ents[k].get("shelf", 0))
        out["racks"] = racks
        out["rack_low"] = racks[0] if racks else None
        out["rack_mid"] = racks[-1] if racks else None
    out["unit"] = (ents[oven_id].get("oven") or {}).get("unit") if oven_id else unit
    hood = next((k for k, r in ents.items() if r.get("ac_kind") == "hood" and root_room(ctx.state(), k) == room), None)
    if hood:
        out["hood"] = hood
    if fridge:
        f = _first(ctx, "fridge", room)
        if f is None:
            built = B.build_fridge(p, room)
            if built is None:
                raise SpecError("appliances_cooking.kitchen: no room for a refrigerator")
            f = built["fridge"]
        out["fridge"] = f
        z = next((k for k, r in ents.items() if r.get("ac_kind") == "freezer" and r.get("power_from") == f), None)
        out["freezer"] = z
        out["door_bin"] = next((k for k in sorted(ents) if ents[k].get("location") == f and ents[k].get("ac_kind") == "door_bin"), None)
        out["crisper"] = next((k for k in sorted(ents) if ents[k].get("location") == f and ents[k].get("ac_kind") == "crisper"), None)
        out["fridge_gasket"] = next((k for k in sorted(ents) if ents[k].get("location") == f and ents[k].get("ac_kind") == "gasket"), None)
        if z:
            out["freezer_gasket"] = next((k for k in sorted(ents) if ents[k].get("location") == z and ents[k].get("ac_kind") == "gasket"), None)
    if dishwasher:
        dw = _first(ctx, "dishwasher", room)
        if dw is not None and brand and ents[dw].get("brand") != brand:
            _remove(ctx, dw)
            dw = None
        if dw is None:
            built = B.build_dishwasher(p, room, brand=brand or "generic")
            if built is None:
                raise SpecError("appliances_cooking.kitchen: no counter for a dishwasher")
            dw = built["dishwasher"]
        out["dishwasher"] = dw
        for k in sorted(ents):
            r = ents[k]
            if r.get("location") == dw or ents.get(r.get("location"), {}).get("location") == dw:
                kind = r.get("ac_kind")
                if kind == "dw_rack":
                    out["rack_" + r.get("rack_kind", "x") if r.get("rack_kind") != "basket" else "basket"] = k
                elif kind in ("detergent_cup", "rinse_aid_reservoir", "dw_filter", "dw_sump", "gasket"):
                    out[{"detergent_cup": "detergent_cup", "rinse_aid_reservoir": "rinse_aid", "dw_filter": "dw_filter",
                         "dw_sump": "dw_sump", "gasket": "dw_gasket"}[kind]] = k
        out["dw_counter"] = ents[dw].get("location")
        if dishwasher_rules:
            cfg.setdefault("household", {}).setdefault("appliance_rules", {})["dishwasher"] = copy.deepcopy(dishwasher_rules)
        if pods:
            tub = next((k for k in sorted(ents) if ents[k].get("type") == "dishwasher_pod_tub" and root_room(ctx.state(), k) == room), None)
            if tub is None:
                state = ctx.state()
                cabinets = sorted((c for c in p.find("cabinet", room=room) if ents[c].get("type") == "cabinet"),
                                  key=lambda c: (not C.approachable(state, c), bool(ents[c].get("sink_base")), c))
                tub = B.store_in_cabinets(p, cabinets, "dishwasher_pod_tub", extra_kg=0.1, share=0.8)
                if tub is None:
                    counters = B._counters(p, room)
                    tub = place_on_any(ctx, counters[:1], "dishwasher_pod_tub") if counters else None
                if tub:
                    for _ in range(6):
                        p.inside(tub, "dishwasher_pod")
            out["pod_tub"] = tub
            out["pod"] = next((k for k in sorted(ents) if ents[k].get("location") == tub and ents[k].get("type") == "dishwasher_pod"), None) if tub else None
    counters = B._counters(p, room)
    if not counters:
        raise SpecError("appliances_cooking.kitchen: no counters")
    state = ctx.state()
    counters.sort(key=lambda c: (not C.approachable(state, c), -round(free_area(ctx, c), 2), c))
    out["counter"] = counters[0]
    out["counter2"] = counters[1] if len(counters) > 1 else counters[0]
    reach_key = lambda c: (not C.approachable(state, c), c)  # noqa: E731
    # the dish cabinet: reachable, not the sink base, with room on its bottom shelf for a load of dishes
    cabinets = sorted((c for c in p.find("cabinet", room=room) if ents[c].get("type") == "cabinet" and ents[c].get("location") == room),
                      key=lambda c: (not C.approachable(state, c), bool(ents[c].get("sink_base")),
                                     _shelf_headroom(ctx, c) < DISH_LOAD_KG, c))
    out["cabinet"] = cabinets[0] if cabinets else None
    if out["cabinet"]:
        _make_shelf_room(ctx, out["cabinet"])
    uppers = sorted(p.find("upper_cabinet", room=room), key=reach_key)
    out["upper"] = uppers[0] if uppers else None
    out["upper2"] = uppers[1] if len(uppers) > 1 else (uppers[0] if uppers else None)
    drawers = sorted(p.find("drawer", room=room), key=reach_key)
    out["drawer"] = drawers[0] if drawers else None
    def bound():
        ids = _bound_ids(ctx)
        for v in out.values():
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, str) and x in ents:
                    ids.add(x)
        return ids

    if microwave:
        mw = _first(ctx, "microwave", room)
        if mw is None:
            mw = _make_counter_room(ctx, counters, "microwave", bound())
        out["microwave"] = mw
    if toaster:
        t = _first(ctx, "toaster", room)
        if t is None:
            t = _make_counter_room(ctx, counters, "toaster", bound())
        out["toaster"] = t
    if kettle:
        k = _first(ctx, "kettle", room)
        if k is None:
            built = _build_on_counters(ctx, counters, lambda c: B.build_kettle(p, c), "kettle_base", bound())
            k = built["kettle"] if built else None
        out["kettle"] = k
        out["kettle_base"] = ents[k]["location"] if k else None
    if coffee_maker:
        cm = _first(ctx, "coffee_maker", room)
        if cm is None:
            built = _build_on_counters(ctx, counters, lambda c: B.build_coffee_maker(p, c), "drip_coffee_maker", bound())
            cm = built["coffee_maker"] if built else None
        out["coffee_maker"] = cm
        out["carafe"] = next((k for k in sorted(ents) if ents[k].get("location") == cm and ents[k].get("ac_kind") == "carafe"), None) if cm else None
        out["brew_basket"] = next((k for k in sorted(ents) if ents[k].get("location") == cm and ents[k].get("ac_kind") == "brew_basket"), None) if cm else None
    if mitts:
        m = next((k for k in sorted(ents) if ents[k].get("type") == "oven_mitts" and root_room(ctx.state(), k) == room), None)
        if m is None:
            m = place_on_any(ctx, [out["counter2"], out["counter"]], "oven_mitts")
        out["mitts"] = m
    if thermometer:
        t = next((k for k in sorted(ents) if ents[k].get("type") == "instant_read_thermometer" and root_room(ctx.state(), k) == room), None)
        if t is None:
            t = place_on_any(ctx, [out["counter2"], out["counter"]], "instant_read_thermometer")
        out["thermometer"] = t
    if timer:
        t = next((k for k in sorted(ents) if ents[k].get("type") == "kitchen_timer" and root_room(ctx.state(), k) == room), None)
        if t is None:
            t = place_on_any(ctx, [out["counter2"], out["counter"]], "kitchen_timer")
        out["timer"] = t
    # tools and supplies the plans fetch must be in/on furniture the robot can get to (other furnishers fill counters
    # and drawers can sit deep inside a counter run)
    for key in ("mitts", "thermometer", "timer"):
        if out.get(key):
            _ensure_reachable(ctx, out[key], drawer=out.get("drawer"), counters=[out["counter2"], out["counter"]])
    tub = out.get("pod_tub")
    if tub and not C.approachable(ctx.state(), _container_room_anchor(ctx.state(), tub)):
        if not (out.get("cabinet") and C.approachable(ctx.state(), out["cabinet"]) and _relocate(ctx, tub, into=out["cabinet"])):
            _ensure_reachable(ctx, tub, counters=[out["counter2"], out["counter"]])
    for key in ("microwave", "toaster", "kettle_base", "coffee_maker", "fridge"):
        _seat_plug(ctx, out.get(key))
    _settle_pets(ctx, room)
    out["stand"] = reachable_stand(ctx, out["counter"])
    return out


def _seat_plug(ctx, eid):
    """A furnished appliance reported as plugged in (cord dressed behind the counter to the room circuit, no outlet
    record) must have a seated plug: the generator can leave plug_seated 0 after a failed cord route. Deliberately
    loosened or damaged plugs (electrical scenarios, which run after this helper) are not touched."""
    rec = ctx.config["entities"].get(eid or "")
    if not rec or not rec.get("plug_required") or not rec.get("plugged_in") or rec.get("outlet") or rec.get("plug_damage"):
        return
    if C.num(rec.get("plug_seated"), 1.0) < 0.99:
        rec["plug_seated"] = 1.0


@scenario_helper("appliances_cooking.cookware")
def cookware(ctx, kind="saucepan", on=None, into=None, shelf=None, eid=None, props=None, var="pan"):
    spec = COOKWARE.get(kind)
    if spec is None:
        raise SpecError("unknown cookware kind " + str(kind))
    base = {"category": "cookware", "conducts_heat": True, "accepts_items": True, "container": True, "food_safe": True,
            "heat_resistant": True, "max_touch_temp_c": 65.0, "safe_grips": ["handle"], "specific_heat": 500.0}
    merged = dict(base)
    merged.update(copy.deepcopy(spec))
    if merged.get("surface"):
        merged["container"] = False
    type_name, kw = _type_or_generic(ctx, merged, props)
    if type_name == "generic_item":
        kw.update({k: v for k, v in base.items() if k not in kw})
        if spec.get("surface"):
            kw["container"] = False
    new = _place(ctx, type_name, kw, on=on, into=into, eid=eid, shelf=shelf)
    if new is None:
        raise SpecError("no room for the " + kind)
    return {var: new}


@scenario_helper("appliances_cooking.food")
def food(ctx, kind="chicken_breast", on=None, into=None, shelf=None, eid=None, props=None, var="food", count=1):
    spec = FOODS.get(kind)
    if spec is None:
        raise SpecError("unknown food kind " + str(kind))
    out = {}
    made = []
    for i in range(int(count)):
        type_name, kw = _type_or_generic(ctx, spec, props)
        if type_name == "generic_item":
            kw.setdefault("category", "food")
            kw.setdefault("material", "organic")
        new = _place(ctx, type_name, kw, on=on, into=into, eid=(eid if count == 1 else (eid + "_" + str(i + 1) if eid else None)), shelf=shelf)
        if new is None:
            raise SpecError("no room for the " + kind)
        made.append(new)
    out[var] = made[0] if count == 1 else made
    if count > 1:
        for i, m in enumerate(made, 1):
            out["{}_{}".format(var, i)] = m
    return out


@scenario_helper("appliances_cooking.pot_on_burner")
def pot_on_burner(ctx, burner, vessel="saucepan", liquid="water", liquid_l=1.5, temp_c=21.0, heat="off", lit=False, foods=None,
                  handle="side", props=None, var="pot", watcher=None):
    """A pot on a burner. watcher "robot": the household handed the watching of this burner to the robot ("watch this
    while it cooks"), so only the robot's presence attends it (stove._attendance)."""
    if watcher not in (None, "robot"):
        raise SpecError("watcher must be \"robot\" or absent")
    ents = ctx.config["entities"]
    spec = COOKWARE.get(vessel, COOKWARE["saucepan"])
    base = {"category": "cookware", "conducts_heat": True, "accepts_items": True, "container": True, "food_safe": True,
            "heat_resistant": True, "max_touch_temp_c": 65.0, "safe_grips": ["handle"], "specific_heat": 500.0}
    kw = dict(base)
    kw.update({k: v for k, v in spec.items() if k != "food_type"})
    kw.update(copy.deepcopy(props or {}))
    if liquid:
        kw.update(liquid_container=True, liquid=liquid, liquid_l=float(liquid_l), liquid_temp_c=float(temp_c))
    kw["temperature_c"] = float(temp_c)
    kw["handle_dir"] = handle
    pot = ctx.placer.on(burner, "generic_item", **kw)
    if pot is None:
        # another furnisher left a kettle/pan on this burner: it goes to a counter (or a cabinet) first
        room = root_room(ctx.state(), burner)
        counters = B._counters(ctx.placer, room) if room else []
        surfaces = (counters + spare_surfaces(ctx, counters[0])) if counters else []
        stores = [c for c in ctx.placer.find("cabinet", room=room) if ents[c].get("type") == "cabinet"] if room else []
        for k in sorted(e for e in ents if ents[e].get("location") == burner and ents[e].get("relation") == "on"):
            if not (_relocate(ctx, k, onto=surfaces) or any(_relocate(ctx, k, into=c) for c in stores)):
                _remove(ctx, k)
        pot = ctx.placer.on(burner, "generic_item", **kw)
    if pot is None:
        raise SpecError("no room on the burner")
    out = {var: pot}
    if float(temp_c) > 30:
        C.own_temperature(ents[pot])
        ents[pot]["_ac_heat"] = burner
    brec = ents[burner]
    brec["settings"] = dict(brec.get("settings") or {}, heat=heat)
    if watcher:
        brec["_burner"] = dict(brec.get("_burner") or {}, watcher=watcher)
    if lit:
        brec["burner"] = dict(brec["burner"], lit=True, flame="steady blue flame")
        brec["temperature_c"] = 200.0
    for i, fspec in enumerate(foods or []):
        fk = fspec.get("kind", "soup")
        res = food(ctx, kind=fk, into=pot, props=fspec.get("props"), var="food_{}".format(i + 1))
        out.update(res)
        if float(temp_c) > 30:
            ents[res["food_{}".format(i + 1)]]["temperature_c"] = float(temp_c)
    return out


@scenario_helper("appliances_cooking.place_item")
def place_item(ctx, on=None, into=None, shelf=None, type="generic_item", props=None, var="item"):
    """Put a prop (trivet, plate, bowl, container...) on a surface, falling back to other reachable counters/tables in
    the room or a table next door when it is full (other domains furnish counters too)."""
    kw = copy.deepcopy(props or {})
    if into:
        new = ctx.placer.inside(into, type, shelf=shelf, **kw)
    else:
        new = place_on_any(ctx, [on], type, **kw)
        if new is None and on:
            # counters other domains filled: their loose clutter moves to a spare surface or a cabinet first
            new = _make_counter_room(ctx, [on] + [c for c in spare_surfaces(ctx, on) if "counter" in str(ctx.config["entities"][c].get("type"))],
                                     type, bound=_bound_ids(ctx), **kw)
    if new is None:
        raise SpecError("no room for the " + str(kw.get("label") or type))
    return {var: new}


@scenario_helper("appliances_cooking.dishes")
def dishes(ctx, on=None, kinds=None, count=None, soil=None, burnt=None, var="dishes", on2=None):
    kinds = list(kinds or ["plate", "plate", "bowl", "mug", "glass", "fork", "spoon", "table_knife"])
    if count:
        kinds = (kinds * (int(count) // len(kinds) + 1))[:int(count)]
    out = {}
    made = []
    surfaces = [s for s in (on, on2) if s]
    for i, k in enumerate(kinds):
        spec = DISH_SPECS[k]
        props = {"soil": copy.deepcopy((soil or {}).get(k, SOIL_BY_DISH.get(k, {"food": 0.3}))), "_dw_state": "dirty"}
        if burnt and k in burnt:
            props["soil"]["burnt"] = float(burnt[k])
        type_name, kw = _type_or_generic(ctx, spec, props)
        new = place_on_any(ctx, surfaces, type_name, **kw)
        if new is None and surfaces:
            # counters other domains filled: their loose clutter moves to a spare surface or a cabinet first
            new = _make_counter_room(ctx, surfaces, type_name, bound=_bound_ids(ctx, made), **kw)
        if new is None:
            raise SpecError("no room for dish " + k)
        made.append(new)
        out["{}_{}".format(k, sum(1 for x in kinds[:i + 1] if x == k))] = new
    out[var] = made
    return out


def _load_into(ctx, item, rack, pos="middle", orient=None, compartment=None):
    from .dishwasher import placed_height, default_orient
    ents = ctx.config["entities"]
    rec = ents[item]
    rec["location"] = rack
    rec["relation"] = "in"
    for key in ("spos", "pos", "shelf"):
        rec.pop(key, None)
    orient = orient or default_orient(rec)
    meta = {"rack": rack, "pos": pos, "orient": orient, "height_m": round(placed_height(rec, orient), 3)}
    if compartment:
        meta["compartment"] = compartment
    rec["dw"] = meta
    rec["orientation"] = orient


@scenario_helper("appliances_cooking.dishwasher_load")
def dishwasher_load(ctx, dishwasher, state="clean", rack_bottom=None, rack_top=None, basket=None, plates=3, cups=3, bowls=2, cutlery=4,
                    var="dw_items", dish_home=None, cutlery_home=None):
    """state clean: a finished, clean load (status light Clean); dirty_partial: some dirty dishes already loaded properly;
    misloaded: nested bowls, spooned spoons, plastic on the bottom rack, upright cups, a sheet blocking the detergent door."""
    ents = ctx.config["entities"]
    racks = {r.get("rack_kind"): k for k, r in ents.items() if r.get("ac_kind") == "dw_rack" and (r.get("location") == dishwasher or
                                                                                                  ents.get(r.get("location"), {}).get("location") == dishwasher)}
    rb = rack_bottom or racks.get("bottom")
    rt = rack_top or racks.get("top") or rb
    bk = basket or racks.get("basket")
    made = []
    dirty = state != "clean"
    soil_scale = 1.0 if dirty else 0.0

    def mk(kind):
        spec = DISH_SPECS[kind]
        soil = {k: v * soil_scale for k, v in SOIL_BY_DISH.get(kind, {}).items()} if dirty else {}
        props = {"soil": soil, "_dw_state": "dirty" if dirty else "clean"}
        if not dirty:
            props["washed_t"] = 0.0
        home = cutlery_home if kind in ("fork", "spoon", "table_knife") else dish_home
        if home:
            props["home"] = home
        type_name, kw = _type_or_generic(ctx, spec, props)
        new = ctx.placer.make(type_name, rb, **kw)
        made.append(new)
        return new

    if state in ("clean", "dirty_partial"):
        for i in range(int(plates)):
            _load_into(ctx, mk("plate"), rb, pos="middle", orient="upright")
        for i in range(int(cups)):
            _load_into(ctx, mk("mug" if i % 2 == 0 else "glass"), rt, pos="middle", orient="upside_down")
        for i in range(int(bowls)):
            _load_into(ctx, mk("bowl"), rt, pos="back", orient="angled")
        for i in range(int(cutlery)):
            _load_into(ctx, mk("fork" if i % 2 == 0 else "spoon"), bk, orient="handle_down", compartment=1 + i % 6)
        if state == "clean":
            d = ents[dishwasher]
            d["load_state"] = "clean"
            d["status_light"] = "clean"
            d["display"] = "Clean"
            d["appliance"] = dict(d["appliance"], state="done")
            d["_dw"] = dict(d.get("_dw") or {}, completed_cycles=1, completed_t=-1800.0)
            for item in made:
                ents[item]["wetness"] = 0.0
                if ents[item]["dw"]["orient"] in ("upside_down", "angled") and ents[ents[item]["location"]].get("rack_kind") == "top":
                    ents[item]["_dw_pool_ml"] = 8.0
        else:
            ents[dishwasher]["load_state"] = "dirty"
    elif state == "misloaded":
        b1, b2 = mk("bowl"), mk("bowl")
        _load_into(ctx, b1, rt, pos="middle", orient="upside_down")
        ents[b2]["location"] = b1
        ents[b2]["relation"] = "in"
        ents[b2]["dw"] = {"rack": rt, "pos": "middle", "orient": "upright", "height_m": 0.065}
        for i in range(3):
            _load_into(ctx, mk("spoon"), bk, orient="handle_down", compartment=2)
        pc = mk("plastic_container")
        _load_into(ctx, pc, rb, pos="back", orient="upside_down")
        cup = mk("mug")
        _load_into(ctx, cup, rt, pos="front", orient="upright")
        sheet = mk("baking_sheet")
        _load_into(ctx, sheet, rb, pos="front", orient="upright")
        for i in range(int(plates)):
            _load_into(ctx, mk("plate"), rb, pos="middle", orient="upright")
        ents[dishwasher]["load_state"] = "dirty"
    from .dishwasher import load_check
    ents[dishwasher]["_load_check"] = load_check(ctx.state(), dishwasher)
    kind_of_rack = {i: ents.get(ents[i].get("location"), {}).get("rack_kind") for i in made}
    cutlery = [i for i in made if kind_of_rack[i] == "basket"]
    return {var: made, "dw_top": [i for i in made if kind_of_rack[i] == "top" or ents.get(ents[i].get("location"), {}).get("rack_kind") is None
                                  and i not in cutlery and ents[i].get("dw", {}).get("rack") == rt],
            "dw_bottom": [i for i in made if kind_of_rack[i] == "bottom"], "dw_cutlery": cutlery,
            "dw_dishes": [i for i in made if i not in cutlery]}


@scenario_helper("appliances_cooking.fridge_problem")
def fridge_problem(ctx, fridge, kind="protruding", item_label="jar of pickles", warm_c=None, freezer=None, door_bin=None, var="culprit"):
    ents = ctx.config["entities"]
    out = {}
    if kind == "protruding":
        bins = [door_bin] if door_bin else sorted(k for k in ents if ents[k].get("location") == fridge and ents[k].get("ac_kind") == "door_bin")
        props = {"label": item_label, "size_m": [0.1, 0.1, 0.24], "mass_kg": 1.1, "material": "glass", "fragile": True, "volume_l": 1.2,
                 "protrudes": True, "temperature_c": 5.0}
        jar = None
        for b in bins:
            jar = ctx.placer.inside(b, "generic_item", **props)
            if jar:
                break
        if jar is None and bins:
            # condiments other furnishers stocked fill the bin: the bulkiest move to a fridge shelf until the jar fits
            b = bins[0]
            for k in sorted((e for e in ents if ents[e].get("location") == b), key=lambda e: (-C.num(ents[e].get("volume_l")), e)):
                _relocate(ctx, k, into=fridge)
                jar = ctx.placer.inside(b, "generic_item", **props)
                if jar:
                    break
        if jar is None:
            jar = ctx.placer.inside(fridge, "generic_item", shelf=1, **props)
        if jar is None:
            raise SpecError("no room for the protruding item")
        out[var] = jar
        ents[fridge].update(open=False, ajar=True, sealed=False)
    elif kind == "gasket":
        g = next((k for k in sorted(ents) if ents[k].get("location") == fridge and ents[k].get("ac_kind") == "gasket"), None)
        ents[g]["settings"] = dict(ents[g].get("settings") or {}, seated=False)
        ents[fridge].update(open=False, ajar=True, sealed=False)
        out[var] = g
    elif kind == "freezer_ajar":
        z = freezer or next((k for k, r in ents.items() if r.get("ac_kind") == "freezer" and r.get("power_from") == fridge), None)
        bag = ctx.placer.inside(z, "generic_item", label="bag of frozen peas", size_m=[0.25, 0.18, 0.05], mass_kg=0.5, volume_l=0.6,
                                protrudes=True, temperature_c=-15.0, material="plastic")
        ents[z].update(open=False, ajar=True, sealed=False)
        out[var] = bag
        out["freezer"] = z
    elif kind == "open":
        ents[fridge].update(open=True, ajar=False, sealed=False)
        hf = dict(ents[fridge].get("_fridge") or {})
        hf["open_s"] = 300.0
        ents[fridge]["_fridge"] = hf
        ents[fridge]["door_alarm"] = True
        out[var] = None
    if warm_c is not None:
        ents[fridge]["internal_temp_c"] = float(warm_c)
    return out


@scenario_helper("appliances_cooking.freezer")
def freezer(ctx, room=None, type_name="upright_freezer", frost_mm=8.0, frost_free=False, foods=3, defrost_ok=True, var="freezer"):
    p = ctx.placer
    if room:
        candidates = [room]
    else:
        candidates = []
        for rtype in ("garage", "basement", "utility_room", "laundry_room", "pantry", "mudroom", "kitchen"):
            candidates += [r for r in p.rooms(rtype) if r not in candidates and not ctx.config["rooms"][r].get("outdoor")]
    if not candidates:
        raise SpecError("appliances_cooking.freezer: the house has no garage, basement, utility room, pantry or kitchen")
    built = None
    for rid in candidates:
        built = B.build_freezer(p, rid, type_name=type_name, frost_mm=frost_mm, frost_free=frost_free)
        if built:
            room = rid
            break
    if built is None:
        raise SpecError("no room for a freezer in " + ", ".join(candidates))
    z = built["freezer"]
    ents = ctx.config["entities"]
    ents[z]["_defrost_ok"] = bool(defrost_ok)
    made = []
    for i in range(int(foods)):
        label = ["frozen peas", "frozen berries", "ice cream", "frozen chicken thighs", "frozen pizza"][i % 5]
        f = ctx.placer.inside(z, "generic_item", shelf=0 if type_name != "chest_freezer" else None, label=label,
                              size_m=[0.22, 0.16, 0.06], mass_kg=0.6, volume_l=0.8, temperature_c=-18.0, material="plastic",
                              food={"kind": label.replace(" ", "_"), "cat": "frozen_veg" if "pea" in label or "berr" in label else "frozen_meal",
                                    "phase": "solid", "qty_g": 600.0, "ice": 1.0}, storage="freezer", perishable=True)
        if f:
            made.append(f)
    out = {var: z, var + "_foods": made, var + "_room": room, var + "_gasket": built["gasket"]}
    return out


@scenario_helper("appliances_cooking.left_on")
def left_on(ctx, room=None, forgotten=("toaster_oven", "kettle_warm", "curling_iron", "space_heater"), intended=("slow_cooker", "dishwasher"),
            ask_needed=False):
    """A house sweep: forgotten heat sources (hidden _intended False), intended programs (_intended True), always-on units."""
    p = ctx.placer
    ents = ctx.config["entities"]
    kitchen_room = _kitchen_room(ctx, room)
    counters = B._counters(p, kitchen_room)
    out = {"forgotten": [], "intended": []}
    if "toaster_oven" in forgotten:
        to = _make_counter_room(ctx, counters, "toaster_oven") if counters else None
        if to:
            r = ents[to]
            r["settings"] = dict(r["settings"], mode="bake", temp=350)
            r["appliance"] = dict(r["appliance"], state="running", program="bake", remaining_s=86400.0)
            r["internal_temp_c"] = 175.0
            r["_intended"] = False
            r["_intent_obvious"] = True
            out["toaster_oven"] = to
            out["forgotten"].append(to)
    if "kettle_warm" in forgotten:
        k = next((e for e in sorted(ents) if ents[e].get("ac_kind") == "kettle"), None)
        if k is None:
            built = _build_on_counters(ctx, counters, lambda c: B.build_kettle(p, c, liquid_l=0.8), "kettle_base")
            k = built["kettle"] if built else None
        if k:
            ents[k].update(on=True, liquid_l=0.8, temperature_c=70.0, liquid_temp_c=70.0, _intended=False, _intent_obvious=True)
            ents[k]["settings"] = dict(ents[k]["settings"], target="95")
            out["kettle"] = k
            out["forgotten"].append(k)
    bath = (p.rooms("bathroom") or p.rooms("half_bath") or [None])[0]
    if "curling_iron" in forgotten and bath:
        iron = dict(label="curling iron", category="appliance", switchable=True, on=True, plug_required=True, plugged_in=True,
                    power_w=60.0, temperature_c=180.0, thermal_owner=C.OWNER, heat_source=True, material="metal",
                    size_m=[0.3, 0.04, 0.04], mass_kg=0.4, max_touch_temp_c=50.0, _intended=False, _intent_obvious=True)
        # left where it was used: on the vanity top (the floor only in a bathroom without one)
        tops = sorted(e for e in geometry.room_entities(ctx.state(), bath) if ents[e].get("surface") and not ents[e].get("ac_kind")
                      and C.num(ents[e].get("surface_height_m"), 0.0) >= 0.6)
        ci = next((x for x in (p.on(t, "generic_item", **copy.deepcopy(iron)) for t in tops) if x), None) or             p.floor_item(bath, "generic_item", **iron)
        if ci:
            out["curling_iron"] = ci
            out["forgotten"].append(ci)
    bed = (p.rooms("bedroom") + p.rooms("guest_room") + p.rooms("office") or [None])[0]
    if "space_heater" in forgotten and bed:
        sh = p.floor_item(bed, "generic_item", label="space heater", category="appliance", switchable=True, on=True, plug_required=True,
                          plugged_in=True, power_w=1500.0, temperature_c=60.0, thermal_owner=C.OWNER, heat_source=True,
                          size_m=[0.35, 0.2, 0.5], mass_kg=3.5, material="plastic", _intended=False, _intent_obvious=True)
        if sh:
            out["space_heater"] = sh
            out["forgotten"].append(sh)
    if "slow_cooker" in intended:
        built = _build_on_counters(ctx, counters, lambda c: B.build_slow_cooker(p, c), "slow_cooker")
        if built:
            sc = built["slow_cooker"]
            ents[sc]["settings"] = dict(ents[sc]["settings"], level="low", hours=8)
            ents[sc]["_intended"] = True
            ents[sc]["_intent_note"] = "That's dinner - it needs to cook until six."
            ents[sc]["_intent_obvious"] = not ask_needed
            ents[sc]["temperature_c"] = 120.0
            C.own_temperature(ents[sc])
            if built.get("insert"):
                ents[built["insert"]]["temperature_c"] = 70.0
                ents[built["insert"]]["open"] = False
                st = ctx.placer.inside(built["insert"], "generic_item", label="beef stew", size_m=[0.2, 0.15, 0.08], mass_kg=3.0,
                                       volume_l=3.0, temperature_c=70.0, food={"kind": "stew", "cat": "prepared", "phase": "liquid",
                                                                               "qty_g": 3000.0, "cooked": False})
                out["stew"] = st
            out["slow_cooker"] = sc
            out["intended"].append(sc)
    if "dishwasher" in intended:
        dw = next((e for e in sorted(ents) if ents[e].get("ac_kind") == "dishwasher"), None)
        if dw is None:
            built = B.build_dishwasher(p, kitchen_room)
            dw = built["dishwasher"] if built else None
        if dw:
            d = ents[dw]
            d["open"] = False
            d["appliance"] = dict(d["appliance"], state="running", program=d["appliance"].get("program") or "normal",
                                  remaining_s=4200.0, elapsed_s=4200.0, delay_s=0.0, duration_s=8400.0)
            d["_dw"] = dict(d.get("_dw") or {}, phase="main_wash", released=True, detergent_factor=1.0, start_t=-4200.0,
                            canonical="normal", items_at_start=[])
            d["_intended"] = True
            d["_intent_note"] = "I started it before I left - let it finish."
            d["_intent_obvious"] = True
            d["internal_temp_c"] = 60.0
            out["dishwasher"] = dw
            out["intended"].append(dw)
    out["fridge"] = next((e for e in sorted(ents) if ents[e].get("ac_kind") == "fridge"), None)
    return out


@scenario_helper("appliances_cooking.gas_smell")
def gas_smell(ctx, burner, gas_pct=0.25, knob="lite"):
    """A gas knob resting at LITE without a flame, and the kitchen already smelling of gas."""
    ents = ctx.config["entities"]
    b = ents[burner]
    b["settings"] = dict(b.get("settings") or {}, heat=knob)
    b["burner"] = dict(b["burner"], lit=False, flame=None)
    b["_burner"] = dict(b.get("_burner") or {}, inherited_leak=True)
    room = root_room(ctx.state(), burner)
    vol = C.room_volume_m3(ctx.state(), room)
    liters = float(gas_pct) / 100.0 * vol * 1000.0
    sec = ctx.config.setdefault(C.SECTION, {})
    sec.setdefault("air", {})[room] = {"smoke": 0.0, "gas_l": round(liters, 2), "odor": {}}
    ctx.config["rooms"][room]["_air"] = {"smoke": 0.0, "co": 0.0, "gas": float(gas_pct), "odor": {}, "toxic": {}, "dT": 0.0}
    return {"gas_room": room, "leaking_burner": burner}


NOISES = {"dishwasher": ("pump_grinding", "grinding_rattle_from_drain_pump"), "fridge": ("compressor_clicking", "loud_click_every_few_minutes_compressor_fails"),
          "microwave": ("magnetron_buzz", "loud_buzzing_while_heating")}


@scenario_helper("appliances_cooking.fault")
def fault(ctx, appliance, kind=None, smoke=False, running=True):
    ents = ctx.config["entities"]
    rec = ents[appliance]
    akind = rec.get("ac_kind")
    fk, noise = NOISES.get(akind, ("unknown", "odd_noise"))
    rec["_fault"] = {"kind": kind or fk, "noise": noise, "smoke": bool(smoke)}
    if running and isinstance(rec.get("appliance"), dict):
        rec["appliance"] = dict(rec["appliance"], state="running", remaining_s=3600.0, elapsed_s=600.0, delay_s=0.0,
                                duration_s=4200.0)
        if akind == "dishwasher":
            rec["_dw"] = dict(rec.get("_dw") or {}, phase="main_wash", released=True, detergent_factor=1.0, start_t=-600.0, canonical="normal")
            rec["open"] = False
    return {"faulty": appliance, "fault_kind": kind or fk, "noise": noise}


@scenario_helper("appliances_cooking.timer_running")
def timer_running(ctx, timer, owner="resident_1", label="laundry", minutes=12, channels=None):
    rec = ctx.config["entities"][timer]
    if channels is not None:
        rec["timer_channels"] = int(channels)
    timers = list(rec.get("timers") or [])
    timers.append({"label": label, "duration_s": minutes * 60.0 + 600.0, "start_s": -600.0, "ends_s": minutes * 60.0, "owner": owner,
                   "state": "running"})
    rec["timers"] = timers
    return {"their_timer_label": label}
