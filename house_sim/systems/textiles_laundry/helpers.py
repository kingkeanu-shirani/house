"""Scenario helpers for task specs (setup op {"op": "helper", "name": "textiles_laundry.<name>", "args": {...}}).

textiles_laundry.station      -> washer, dryer, drawer, prewash/main/softener/bleach, lint_screen, drain_filter,
                                 detergent, pod_tub, pods, softener_bottle, bleach_bottle, stain_remover, sheet_box,
                                 dryer_sheets, rack, iron, board, hangers, shelf, basket, clean_basket, laundry_room
                                 (finds the furnished station; installs one if the house has none; need=[...]
                                 adds missing iron/board/rack/hangers)
textiles_laundry.bed          -> bed, fitted, flat, cover, insert, protector, pillows, cases (state made|slept_in|bare)
textiles_laundry.linens       -> loose clean linens for a bed size at a location (fitted, flat, cover, insert, cases...)
textiles_laundry.sock_pile    -> socks, pairs, orphans (pairs of near-identical socks; some inside out or balled)
textiles_laundry.pockets      -> pockets, pocket_items (pocket entities on a garment holding small items)
textiles_laundry.sewing_kit   -> kit, needle, pincushion, spools, threader, snips, buttons
"""
import copy

from ...kernel.errors import SpecError
from ...kernel.plugins import scenario_helper
from ...kernel.predicates import root_room
from . import model as M
from .fold import fold_dims
from .furnish import basket_on_floor, dress_bed, find_station, fit_lint_screen, install_station, place_dryer, stow_folded

SIZE_DIMS = {"crib": ([0.71, 1.33], [1.0, 1.4], [1.0, 1.2]), "twin": ([0.99, 1.9], [1.68, 2.44], [1.73, 2.18]),
             "full": ([1.37, 1.9], [2.03, 2.59], [2.03, 2.18]), "queen": ([1.52, 2.03], [2.29, 2.59], [2.29, 2.18]),
             "king": ([1.93, 2.03], [2.74, 2.59], [2.59, 2.18])}
STATION_KEYS = ("washer", "dryer", "drawer", "prewash", "main", "softener", "bleach", "lint_screen", "drain_filter", "detergent",
                "pod_tub", "softener_bottle", "bleach_bottle", "stain_remover", "sheet_box", "rack", "iron", "board", "shelf",
                "basket", "clean_basket", "laundry_room", "trash")


def _state(config):
    return {"rooms": config["rooms"], "entities": config["entities"], "people": config.get("people", {}),
            "pets": config.get("pets", {}), "connections": config.get("connections", {}), "robot": {"room": None, "hands": {}}}


# rooms that are never used to store laundry gear or set up a rack/board (shared building spaces, circulation)
NO_STORAGE_ROOMS = ("elevator", "lobby", "building_corridor", "mail_room", "stairwell", "stairs", "hallway", "corridor")
# where spare laundry gear is kept when the laundry room is full (garages last: some are reached from outside only)
STORAGE_TYPES = ("laundry_room", "shared_laundry", "utility_room", "utility_closet", "mudroom", "basement", "master_bedroom",
                 "bedroom", "guest_room", "living_room", "dining_room", "office", "kitchen", "entryway", "garage")
# where a rack or ironing board is set up when the laundry room has no clear floor (living spaces before closets)
SETUP_TYPES = ("laundry_room", "shared_laundry", "utility_room", "master_bedroom", "bedroom", "guest_room", "living_room",
               "dining_room", "office", "playroom", "basement", "mudroom", "kitchen", "garage")
CLOSET_TYPES = ("utility_closet", "coat_closet", "linen_closet", "pantry", "closet", "walk_in_closet")


def overflow_rooms(config, first=None, order=STORAGE_TYPES, last=()):
    """Indoor rooms to try in order: `first`, then the room types in `order`, then any other indoor room that is not
    a shared building space or circulation (types in `last` go at the very end)."""
    rooms = config["rooms"]
    out = [first] if first in rooms else []
    for kind in order:
        out += [r for r in sorted(rooms) if rooms[r].get("type") == kind and r not in out]
    out += [r for r in sorted(rooms) if r not in out and rooms[r].get("type") not in last]
    out += [r for r in sorted(rooms) if r not in out]
    return [r for r in out if not rooms[r].get("outdoor") and (r == first or rooms[r].get("type") not in NO_STORAGE_ROOMS)]


def _room(ctx, room):
    if room is None:
        return None
    if room in ctx.config["rooms"]:
        return room
    rooms = ctx.placer.rooms(room)
    if not rooms:
        raise SpecError("No room of type {} (add it to house.require)".format(room))
    return rooms[0]


def setup_rooms(config, first=None):
    """Rooms to set up a rack or ironing board in: the laundry room, then living spaces, closets last."""
    return overflow_rooms(config, first, SETUP_TYPES, last=CLOSET_TYPES)


CIRCULATION = ("hallway", "building_corridor", "lobby", "corridor", "stairwell", "stairs")


def rack_spot(config, item, rooms, set_up=False):
    """Move a folded rack or ironing board to a floor spot where its unfolded footprint fits: inside the grid, on no
    blocking or floor-standing footprint, person, pet or hazard, outside doorway clearance (geometry.door_clearance:
    the doorway, its neighbours and the sweep of a door leaf), and such that the set-up footprint keeps the room's
    access not worse on the hazard-free floor (geometry.access_report / not_worse: doorways stay joined; the dock,
    furniture, fixtures and mounts that were accessible stay accessible) while the item itself is accessible both
    folded (to unfold it) and open (to use it). set_up=True leaves it open and ready (an ironing board set up for
    use: open footprint, blocks_path). Returns the room or None. Hallways and other circulation spaces are skipped
    (a set-up rack or board there blocks egress)."""
    from ...kernel import geometry
    rec = config["entities"][item]
    open_size = list(rec.get("open_size_m") or rec.get("size_m") or [1.0, 0.5, 1.0])
    folded_size = list(rec.get("folded_size_m") or rec.get("size_m") or open_size)
    for rid in rooms:
        room = config["rooms"].get(rid)
        if room is None or room.get("outdoor") or room.get("type") in CIRCULATION:
            continue
        state = _state(config)
        if not geometry.doorway_cells(state, rid):
            continue
        cols, rows = geometry.grid_size(room)
        static = set(geometry.blocked_cells(state, rid, ignore=(item,), include_agents=False))
        taken = set(geometry.blocked_cells(state, rid, ignore=(item,))) | static
        for eid in geometry.room_entities(state, rid):
            other = config["entities"][eid]
            if eid != item and other.get("pos") and geometry.floor_standing(other):
                taken.update(geometry.footprint(other))
        hazards = {c for c, found in geometry.placement_hazards(state, rid).items() if any(src != item for _, src in found)}
        taken |= hazards | geometry.door_clearance(state, rid)
        impassable = static | hazards
        targets = geometry.access_targets(state, rid, level="all", ignore=(item,))
        doors = {k: v for k, v in targets.items() if k[0:1] == ("door",)}
        before = geometry.access_report(state, rid, impassable, targets)
        for rot in (0, 90):
            fc, fr = geometry.footprint_size({"size_m": open_size, "rot": rot})
            for y in range(rows - fr + 1):
                for x in range(cols - fc + 1):
                    cells = [(x + i, y + j) for i in range(fc) for j in range(fr)]
                    if any(c in taken for c in cells):
                        continue
                    opened = dict(targets)
                    opened[("open",)] = (cells, 1, False)
                    after = geometry.access_report(state, rid, impassable | set(cells), opened)
                    if not geometry.not_worse(before, after, new_keys=[("open",)]):
                        continue
                    folded = geometry.footprint({"size_m": folded_size, "rot": rot, "pos": [x, y]})
                    stood = dict(doors)
                    stood[("folded",)] = (folded, 1, False)
                    if ("folded",) not in geometry.access_report(state, rid, impassable | set(folded), stood)["ok"]:
                        continue
                    rec.update(location=rid, relation="on", pos=[x, y], rot=rot)
                    for key in ("spos", "shelf", "stored_upright", "pack_orientation"):
                        rec.pop(key, None)
                    if set_up:
                        rec.update(open=True, size_m=list(open_size), blocks_path=True)
                    else:
                        rec.update(open=False, size_m=list(folded_size), blocks_path=False)
                    return rid
    return None


@scenario_helper("textiles_laundry.closet")
def closet_helper(ctx, room, open=True):
    """The closet or wardrobe of a bedroom (opened for the task unless open=false)."""
    config = ctx.config
    rid = _room(ctx, room)
    ents = config["entities"]
    found = [e for e in sorted(ents) if ents[e].get("location") == rid and ents[e].get("type") in ("closet", "wardrobe")]
    if not found:
        found = [ctx.placer.furniture(rid, "wardrobe")]
        if not found[0]:
            raise SpecError("No closet or room for a wardrobe in " + rid)
    if open:
        ents[found[0]]["open"] = True
    return {"closet": found[0], "closet_room": rid}


MANUAL_WORDS = {"de": {"normal": "Baumwolle", "heavy": "Intensiv", "perm_press": "Pflegeleicht", "delicate": "Feinwaesche", "hand_wash": "Handwaesche",
                        "wool": "Wolle", "bulky": "Bettwaesche", "quick": "Kurz", "sanitize": "Hygiene", "rinse_spin": "Spuelen",
                        "drain_spin": "Abpumpen/Schleudern", "tub_clean": "Trommelreinigung"},
                 "fr": {"normal": "Coton", "heavy": "Intensif", "perm_press": "Synthetique", "delicate": "Delicat", "hand_wash": "Lavage a la main",
                        "wool": "Laine", "bulky": "Couette", "quick": "Rapide", "sanitize": "Hygiene", "rinse_spin": "Rincage",
                        "drain_spin": "Vidange/Essorage", "tub_clean": "Nettoyage tambour"}}
ICONS = {"normal": "T-shirt", "heavy": "T-shirt with three dots", "perm_press": "iron outline", "delicate": "feather", "hand_wash": "hand in basin",
         "wool": "ball of yarn", "bulky": "duvet", "quick": "stopwatch", "sanitize": "thermometer", "rinse_spin": "shower",
         "drain_spin": "spiral", "tub_clean": "drum with sparkles"}


@scenario_helper("textiles_laundry.unfamiliar_washer")
def unfamiliar_washer(ctx, washer, language="de"):
    """Recode a washer's programs as icon codes P1..P12; the meaning is only in the (readable) manual text."""
    config = ctx.config
    rec = config["entities"][washer]
    machine = rec["appliance"]
    kinds = sorted(machine["programs"])
    codes = ["p{}".format(i + 1) for i in range(len(kinds))]   # action arguments are lower-cased
    ctx.rng.shuffle(codes)
    programs, hidden, lines, panel, out = {}, {}, [], {}, {}
    words = MANUAL_WORDS.get(language, MANUAL_WORDS["de"])
    for name, code in sorted(zip(kinds, codes), key=lambda kv: int(kv[1][1:])):
        spec = dict(machine["programs"][name])
        kind = spec.pop("kind", name)
        programs[code] = spec
        hidden[code] = kind
        panel[code] = ICONS.get(kind, "symbol")
        lines.append("{} ({}) = {}".format(code, ICONS.get(kind, "symbol"), words.get(kind, kind)))
        out["code_" + kind] = code
    machine["programs"] = programs
    machine["program"] = out.get("code_normal", codes[0])
    rec["_program_kinds"] = hidden
    rec["panel_symbols"] = panel
    rec["panel_language"] = language
    rec["instructions"] = "Programme: " + "; ".join(lines) + ". Waschmittel in Fach II (main). Tuer bleibt waehrend des Programms verriegelt."
    rec["secret_fields"] = sorted(set(rec.get("secret_fields") or []) | {"instructions"})
    return out


@scenario_helper("textiles_laundry.occupant")
def occupant(ctx, appliance, pet_id="cat_1", species="cat", label="Miso", leave_after_s=120.0):
    """A household pet napping inside a washer/dryer drum (its cell is the appliance's own footprint). The appliance
    must be the dwelling's own: a household pet is not shut in a shared building laundry (use station in_unit)."""
    config = ctx.config
    rec = config["entities"][appliance]
    if rec.get("shared") or (config["rooms"].get(rec.get("location")) or {}).get("shared"):
        raise SpecError("occupant: {} is in a shared building laundry; a household pet naps in the dwelling's own machine "
                        "(station in_unit: true)".format(appliance))
    pets = config.setdefault("pets", {})
    pet = pets.setdefault(pet_id, {"label": label, "species": species})
    pet.update(species=species, label=pet.get("label", label), location=rec["location"], pos=list(rec.get("pos") or [0, 0]),
               inside=appliance, posture="lying", activity="napping", size="small", leave_after_s=float(leave_after_s))
    return {"pet": pet_id}


STATION_ROOM_TYPES = ("laundry_room", "utility_room", "basement", "garage", "bathroom", "kitchen", "living_room")


def _station_candidates(ctx, room, in_unit=False):
    config = ctx.config
    if room:
        return [_room(ctx, room)]
    p = ctx.placer
    # in-unit laundry first (the laundry room, then any other room of the dwelling with a washer), the building's
    # shared laundry last (never with in_unit)
    first = p.rooms("laundry_room")
    shared = [r for r in sorted(config["rooms"]) if config["rooms"][r].get("type") == "shared_laundry" or config["rooms"][r].get("shared")]
    rest = [r for r in sorted(config["rooms"]) if r not in first and r not in shared]
    return first + rest + ([] if in_unit else [r for r in shared if config["rooms"][r].get("type") == "shared_laundry"])


def _find_or_install_station(ctx, room, in_unit=False):
    """A furnished station (washer + dryer), preferring complete ones; a washer without a dryer gets one beside it
    or stacked on it; with no washer at all, one is installed in the first laundry-capable room with space.
    in_unit=True: only the dwelling's own machines (never a shared building laundry)."""
    config = ctx.config
    p = ctx.placer
    partial = []
    for rid in _station_candidates(ctx, room, in_unit):
        if not rid:
            continue
        found = find_station(config, rid)
        if found and found.get("dryer"):
            return found
        if found:
            partial.append(found)
    for found in partial:
        rid = found["laundry_room"]
        dryer, _ = place_dryer(p, rid, found["washer"], allow_swap=False)
        if dryer:
            fit_lint_screen(p, dryer)
            return find_station(config, rid)
    target = _room(ctx, room) if room else None
    kinds = [config["rooms"][target]["type"]] if target else list(STATION_ROOM_TYPES)
    for kind in kinds:
        for rid in p.rooms(kind):
            if target and rid != target:
                continue
            made = install_station(p, rid)
            if made and made.get("dryer"):
                return find_station(config, rid)
            if made:
                partial.append(find_station(config, rid))
    if partial:
        return partial[0]       # a washer with no space anywhere for a dryer: specs that need one fail cleanly
    raise SpecError("No space for a washer anywhere in this house" + (" ({})".format(target) if target else ""))


def _put_small(ctx, found, rid, type_name, level=1):
    """A small laundry item (iron, hanger) on the laundry shelf, else another shelf/surface in the laundry room,
    else a surface or the floor of another room."""
    config = ctx.config
    p = ctx.placer
    ents = config["entities"]
    shelf = found.get("shelf")
    if shelf in ents:
        if ents[shelf].get("shelf_heights"):
            levels = [level] + [i for i in range(len(ents[shelf]["shelf_heights"])) if i != level]
            for lv in levels:
                eid = p.inside(shelf, type_name, shelf=lv)
                if eid:
                    return eid
        else:
            eid = p.on(shelf, type_name)
            if eid:
                return eid
    for room_id in overflow_rooms(config, rid):
        for other in sorted(ents):
            orec = ents[other]
            if other == shelf or orec.get("location") != room_id or orec.get("category") in ("bed", "sofa", "seat", "appliance"):
                continue
            if orec.get("shelf_heights") and orec.get("container") and orec.get("open", True):
                eid = p.inside(other, type_name, shelf=0)
            elif orec.get("surface") and orec.get("accepts_items", True) and not orec.get("portable"):
                eid = p.on(other, type_name)
            else:
                continue
            if eid:
                return eid
        eid = p.floor_item(room_id, type_name)
        if eid:
            return eid
    return None


def _put_floor(ctx, rid, type_name, **props):
    """A floor-standing laundry item in the laundry room, else the nearest storage room with floor space: a folded
    board or rack stood against a wall, a basket or bin against a wall where possible (Placer.floor_item: out of
    doorway clearance, never cutting a hazard-free route or the only access to anything)."""
    stand = type_name in ("drying_rack", "ironing_board")
    for room_id in overflow_rooms(ctx.config, rid):
        if stand:
            eid = stow_folded(ctx.placer, room_id, type_name, closet=False, **props)
        else:
            eid = basket_on_floor(ctx.placer, room_id, type_name, **props)
        if eid:
            return eid
    return None


def _is_trash_bin(rec):
    from .. import core
    return core.is_waste_container(rec) and rec.get("waste_stream") in ("trash", "landfill", None) and         bool(rec.get("container") or rec.get("accepts_items")) and not rec.get("locked")


def _trash_can(ctx, rid):
    """A general-waste bin standing in the laundry room (an existing one first), else a small trash can put on the
    door-clear floor of the laundry room or the nearest storage room."""
    ents = ctx.config["entities"]
    for eid in sorted(ents):
        if ents[eid].get("location") == rid and _is_trash_bin(ents[eid]):
            return eid
    return _put_floor(ctx, rid, "generic_item", label="trash can", category="trash_can", container=True, accepts_items=True,
                      capacity_l=30.0, inner_size_m=[0.3, 0.3, 0.4], size_m=[0.32, 0.32, 0.45], waste_stream="trash", portable=False)


def _household_item(config, type_name):
    """The household's own folded rack or ironing board wherever it is kept (a laundry-room wall, a closet), not one
    in a shared building room."""
    state = _state(config)
    for eid in sorted(config["entities"]):
        rec = config["entities"][eid]
        if rec.get("type") != type_name:
            continue
        rid = root_room(state, eid)
        if rid in config["rooms"] and not config["rooms"][rid].get("shared") and not config["rooms"][rid].get("outdoor"):
            return eid
    return None


@scenario_helper("textiles_laundry.station")
def station(ctx, room=None, need=None, rack_ready=False, iron_on_board=False, in_unit=False):
    config = ctx.config
    found = _find_or_install_station(ctx, room, in_unit=bool(in_unit))
    rid = found["laundry_room"]
    if not found.get("basket"):
        found["basket"] = _put_floor(ctx, rid, "laundry_basket", designated="dirty")
    for key in need or []:
        if found.get(key):
            continue
        if key == "iron":
            found["iron"] = _put_small(ctx, found, rid, "clothes_iron", 1)
        elif key == "board":
            found["board"] = _household_item(config, "ironing_board") or _put_floor(ctx, rid, "ironing_board")
        elif key == "rack":
            found["rack"] = _household_item(config, "drying_rack") or _put_floor(ctx, rid, "drying_rack")
        elif key == "hangers":
            found["hangers"] = [h for h in (_put_small(ctx, found, rid, "plastic_hanger", 0), _put_small(ctx, found, rid, "plastic_hanger", 0)) if h]
        elif key == "clean_basket":
            found["clean_basket"] = _put_floor(ctx, rid, "laundry_basket", label="clean laundry basket", designated="clean")
        elif key == "trash":
            found["trash"] = _trash_can(ctx, rid)
        if not found.get(key):
            raise SpecError("station: could not add " + key)
    if iron_on_board and found.get("iron") and found.get("board"):
        from ...kernel import geometry
        board = found["board"]
        if rack_spot(config, board, setup_rooms(config, rid), set_up=True) is None:
            raise SpecError("station: no floor space to set up the ironing board")
        iron = config["entities"][found["iron"]]
        iron.update(location=board, relation="on", orientation="upright")
        for key in ("pos", "shelf"):
            iron.pop(key, None)
        spot = geometry.surface_spot(_state(config), board, found["iron"], want=[0.9, 0.05])
        iron["spos"] = spot or [0.0, 0.0]
    if rack_ready and found.get("rack"):
        if rack_spot(config, found["rack"], setup_rooms(config, rid)) is None:
            raise SpecError("station: no clear floor to unfold the drying rack")
        found["rack_room"] = config["entities"][found["rack"]]["location"]
    result = {k: found.get(k) for k in STATION_KEYS}
    rack = found.get("rack")
    result["rack_room"] = found.get("rack_room") or (root_room(_state(config), rack) if rack else None)
    result["pods"] = list(found.get("pods") or [])
    result["dryer_sheets"] = list(found.get("dryer_sheets") or [])
    result["hangers"] = list(found.get("hangers") or [])
    for i, pod in enumerate(result["pods"], 1):
        result["pod_{}".format(i)] = pod
    return result


def _clear_bedding(config, bed):
    ents = config["entities"]
    doomed = []
    for eid in sorted(ents):
        rec = ents[eid]
        if rec.get("location") == bed and (rec.get("bed_role") or rec.get("cover_role")):
            doomed.append(eid)
    changed = True
    while changed:
        changed = False
        for eid in sorted(ents):
            if ents[eid].get("location") in doomed and eid not in doomed:
                doomed.append(eid)
                changed = True
    for eid in doomed:
        ents.pop(eid, None)


@scenario_helper("textiles_laundry.bed")
def bed_helper(ctx, bed=None, room=None, state="made", size=None, protector=False, flat=False, pillows=None, owner=None):
    config = ctx.config
    ents = config["entities"]
    if bed is None:
        rid = _room(ctx, room) if room else None
        beds = [e for e in sorted(ents) if ents[e].get("category") == "bed" and (rid is None or ents[e].get("location") == rid)
                and not (ents[e].get("type") == "crib" or "crib" in ents[e].get("is_a", []))]
        if not beds:
            raise SpecError("No bed found")
        bed = beds[0]
    _clear_bedding(config, bed)
    ents[bed].pop("bed_state", None)
    out = {"bed": bed, "bed_room": ents[bed]["location"]}
    if state in ("bare", "stripped"):
        width = float((ents[bed].get("size_m") or [1.6])[0])
        ents[bed]["bed_size"] = size or ("twin" if width < 1.2 else ("full" if width < 1.45 else ("queen" if width < 1.75 else "king")))
        ents[bed].setdefault("mattress_depth_cm", 30.0)
        ents[bed]["made"] = False
        return out
    made = dress_bed(ctx.placer, bed, size=size, protector=protector, flat=flat, made=(state == "made"), pillows=pillows)
    out.update({k: v for k, v in made.items() if k != "bed"})
    for i, pillow in enumerate(made.get("pillows", []), 1):
        out["pillow_{}".format(i)] = pillow
        out["case_{}".format(i)] = made["cases"][i - 1]
    if state == "slept_in":
        for key in ("fitted", "flat", "cover"):
            if made.get(key):
                ents[made[key]]["wrinkled"] = 0.55
        if made.get("cover"):
            ents[made["cover"]]["offset_cm"] = 28.0
            ents[made["cover"]]["fill_evenness"] = 0.7
        for pillow in made.get("pillows", []):
            ents[pillow].update(pose="flat", loft=0.5)
        ents[bed]["made"] = False
    if owner:
        ents[bed]["owner"] = owner
    return out


def _near_surfaces(config, at):
    """Other free-standing surfaces in the same room as `at` (tables, desks, dressers, nightstands, counters), the
    fallback when `at` itself is full because other domains furnish the same furniture."""
    room = at if at in config["rooms"] else root_room(_state(config), at)
    if room not in config["rooms"]:
        return []
    out = []
    for other in sorted(config["entities"]):
        orec = config["entities"][other]
        if other == at or orec.get("location") != room or not orec.get("surface") or orec.get("portable"):
            continue
        if orec.get("category") in ("bed", "floor", "appliance") or orec.get("accepts_items") is False:
            continue
        out.append(other)
    return out


def _put_on(config, p, at, type_name, **props):
    """p.on(at), else another surface in the same room (None when nothing fits)."""
    eid = p.on(at, type_name, **props)
    if eid is not None:
        return eid
    for other in _near_surfaces(config, at):
        eid = p.on(other, type_name, **props)
        if eid is not None:
            return eid
    return None


def _place_linen(config, p, at, type_name, relation, props):
    """Put a linen at `at`; if that spot is full (other domains furnish the same furniture), fall back to another
    surface in the same room, then the room floor, so the helper is robust in any furnished house."""
    if at in config["rooms"]:
        return p.floor_item(at, type_name, **props)
    rec = config["entities"][at]
    eid = None
    if rec.get("container") and relation != "on":
        eid = p.inside(at, type_name, **props)
    if eid is None and relation != "in" and (rec.get("surface") or not rec.get("container")):
        eid = p.on(at, type_name, **props)
    if eid is not None:
        return eid
    room = root_room(_state(config), at)
    if room not in config["rooms"]:
        return None
    for other in sorted(config["entities"]):
        orec = config["entities"][other]
        if other == at or orec.get("location") != room or not orec.get("surface") or orec.get("category") == "bed":
            continue
        eid = p.on(other, type_name, **props)
        if eid is not None:
            return eid
    return p.floor_item(room, type_name, near=rec.get("pos"), **props)


@scenario_helper("textiles_laundry.linens")
def linens(ctx, at, size="queen", items=None, folded=True, relation=None, set_id=None, fitted_pocket_cm=None):
    """Loose clean linens of one bed size at a location (surface/container/room floor)."""
    config = ctx.config
    p = ctx.placer
    fitted_dims, flat_dims, duvet_dims = SIZE_DIMS[size]
    wanted = items or ["fitted", "flat", "case", "case"]
    out = {}
    counters = {}
    for kind in wanted:
        props = {"bed_size": size}
        if set_id:
            props["set_id"] = set_id
        if kind in ("fitted", "protector"):
            type_name = "fitted_sheet" if kind == "fitted" else "mattress_protector"
            props.update(flat_size_m=list(fitted_dims), fold_base_m=list(fitted_dims))
            if fitted_pocket_cm and kind == "fitted":
                props["pocket_depth_cm"] = fitted_pocket_cm
        elif kind == "flat":
            type_name = "flat_sheet"
            props.update(flat_size_m=list(flat_dims), fold_base_m=list(flat_dims))
        elif kind in ("cover", "duvet_cover"):
            type_name = "duvet_cover"
            props.update(flat_size_m=list(duvet_dims), fold_base_m=list(duvet_dims), closure_open=True)
        elif kind in ("insert", "duvet_insert"):
            type_name = "duvet_insert"
            props.update(flat_size_m=list(duvet_dims), fold_base_m=list(duvet_dims))
        elif kind in ("case", "pillowcase"):
            type_name = "king_pillowcase" if size == "king" else "pillowcase"
        elif kind == "pillow_protector":
            type_name = "pillow_protector"
        else:
            type_name = kind
        if folded and kind not in ("insert", "duvet_insert"):
            style = "fitted_nest" if kind in ("fitted", "protector") else ("quarters" if kind in ("flat", "cover", "duvet_cover") else "half_half")
            proto = p.reg.make(type_name, at, **copy.deepcopy(props))
            dims, volume, lumpy = fold_dims(proto, style, 0.6)
            props.update(folded=style, size_m=list(dims), volume_l=volume, fold_quality={"align_cm": 0.6, "lumpy": lumpy, "neat": True},
                         wrinkled=0.05)
        eid = _place_linen(config, p, at, type_name, relation, props)
        if eid is None:
            raise SpecError("linens: no space for {} at {} (or anywhere near it)".format(type_name, at))
        base = {"fitted": "fitted", "protector": "protector", "flat": "flat", "cover": "cover", "duvet_cover": "cover", "insert": "insert",
                "duvet_insert": "insert", "case": "case", "pillowcase": "case", "pillow_protector": "pillow_protector"}.get(kind, kind)
        counters[base] = counters.get(base, 0) + 1
        out[base if counters[base] == 1 else "{}_{}".format(base, counters[base])] = eid
    return out


LOOKALIKES = [
    {"color": "black", "length": "crew", "brand": "Hanes", "size": "L", "size_label": "L 10-13"},
    {"color": "black", "length": "crew", "brand": "Hanes", "size": "S", "size_label": "Kids S"},
    {"color": "black", "length": "ankle", "brand": "Hanes", "size": "L", "size_label": "L 10-13"},
    {"color": "white", "length": "crew", "brand": "Nike", "size": "L", "size_label": "L 8-12", "pattern": "swoosh"},
    {"color": "white", "length": "crew", "brand": "Champion", "size": "L", "size_label": "L 9-12", "pattern": "plain"},
    {"color": "navy", "length": "crew", "brand": "Gap", "size": "M", "size_label": "M 6-10", "pattern": "ribbed"},
    {"color": "grey", "length": "knee", "brand": "Smartwool", "size": "M", "size_label": "M", "fabric": "wool"},
]


@scenario_helper("textiles_laundry.sock_pile")
def sock_pile(ctx, container, pairs=4, orphans=1, owners=None, scramble=True):
    """Pairs of near-identical socks (shared colour, different length/size/brand) dropped into a container or room."""
    config = ctx.config
    p = ctx.placer
    rng = ctx.rng
    owners = owners or sorted(config.get("people", {}))[:2] or [None]
    socks, pair_list, orphan_list = [], [], []
    designs = list(LOOKALIKES)
    rng.shuffle(designs)
    designs = designs[:pairs + orphans]
    for i, design in enumerate(designs):
        owner = owners[0] if design["size"] != "S" else (owners[-1] if len(owners) > 1 else owners[0])
        pair_id = "sockpair_{}".format(i + 1)
        members = []
        for side in (("a", "b") if i < pairs else ("a",)):
            props = dict(design, pair_id=pair_id, owner=owner, color_class="dark" if design["color"] in ("black", "navy") else ("white" if design["color"] == "white" else "light"))
            if scramble and rng.random() < 0.25:
                props["inside_out"] = True
            if scramble and rng.random() < 0.15:
                props["balled"] = True
            if container in config["rooms"]:
                eid = p.floor_item(container, "sock", **props)
            elif config["entities"][container].get("container"):
                eid = p.inside(container, "sock", **props)
            else:
                eid = _put_on(config, p, container, "sock", **props)
            if eid is None and container not in config["rooms"]:
                room = root_room(_state(config), container)
                if room in config["rooms"]:
                    eid = p.floor_item(room, "sock", near=config["entities"][container].get("pos"), **props)
            if eid is None:
                raise SpecError("sock_pile: no space in or near " + container)
            members.append(eid)
            socks.append(eid)
        if i < pairs:
            pair_list.append(members)
        else:
            orphan_list.extend(members)
    out = {"socks": socks, "pairs": pair_list, "orphans": orphan_list}
    for i, (a, b) in enumerate(pair_list, 1):
        out["pair{}_a".format(i)] = a
        out["pair{}_b".format(i)] = b
    for i, o in enumerate(orphan_list, 1):
        out["orphan_{}".format(i)] = o
    return out


@scenario_helper("textiles_laundry.pockets")
def pockets(ctx, garment, contents=None, empty=None):
    """Pocket entities on a garment; contents: [{"pocket": "back_right", "type": "generic_item", "label": "pen", ...}]."""
    config = ctx.config
    p = ctx.placer
    names = sorted(set([c.get("pocket", "front_left") for c in contents or []] + list(empty or [])))
    out = {"pockets": [], "pocket_items": []}
    pocket_ids = {}
    for name in names:
        pid = p.make("garment_pocket", garment, label="{} pocket".format(name.replace("_", " ")), pocket_name=name)
        config["entities"][pid]["relation"] = "part"
        pocket_ids[name] = pid
        out["pockets"].append(pid)
        out["pocket_" + name] = pid
    for i, item in enumerate(contents or [], 1):
        spec = dict(item)
        pocket = pocket_ids[spec.pop("pocket", "front_left")]
        type_name = spec.pop("type", "generic_item")
        var = spec.pop("var", None)
        eid = p.make(type_name, pocket, **spec)
        config["entities"][eid]["relation"] = "in"
        config["entities"][pocket]["looks_empty"] = bool(item.get("flat"))
        out["pocket_items"].append(eid)
        out[var or "pocket_item_{}".format(i)] = eid
    return out


@scenario_helper("textiles_laundry.sewing_kit")
def sewing_kit(ctx, on, thread_colors=("white", "navy", "black"), fine=False, frayed=True, buttons=2, button_size_mm=11,
               button_color="white"):
    config = ctx.config
    p = ctx.placer
    out = {}
    cushion = _put_on(config, p, on, "pincushion")
    if cushion is None:
        raise SpecError("sewing_kit: no space on or near " + on)
    out["pincushion"] = cushion
    needle = p.on(cushion, "fine_needle" if fine else "sewing_needle")
    if needle is None:
        needle = p.make("fine_needle" if fine else "sewing_needle", cushion)
        config["entities"][needle]["relation"] = "on"
    out["needle"] = needle
    spools = []
    for color in thread_colors:
        spool = _put_on(config, p, on, "thread_spool", color=color, frayed=frayed, label="{} thread".format(color))
        if spool:
            spools.append(spool)
            out["thread_" + color] = spool
    out["spools"] = spools
    out["threader"] = _put_on(config, p, on, "needle_threader")
    out["snips"] = _put_on(config, p, on, "thread_snips")
    out["buttons"] = []
    for i in range(buttons):
        b = _put_on(config, p, on, "sewing_button", size_mm=button_size_mm, color=button_color)
        if b:
            out["buttons"].append(b)
            out["button_{}".format(i + 1)] = b
    return out
