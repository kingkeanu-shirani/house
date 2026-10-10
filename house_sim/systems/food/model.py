"""Food model helpers shared by food actions, systems, hooks, skills and other domains.

Representation (see docs/domains/food.md):
* Every piece of food is an entity with a ``food`` dict (kind, cat, phase, qty_g, count, cut,
  pieces, parts, composition, mix state ...). Packages (milk carton, flour bag, can) are food
  entities themselves (``package`` + ``tare_kg``); food taken out of them becomes a new
  unpackaged entity inside the destination vessel.
* Liquids that are food (milk, stock, batter, juice, soup) are entities with phase "liquid".
  Plain water poured by plumbing stays in the vessel's ``liquid``/``liquid_l`` fields and is
  absorbed into liquid food in the same vessel when they are mixed.
* Hidden truth is kept in top-level underscore keys (_spoil, _status, _time_out_s, _core_c,
  _expires, _rotten, _weak_yolk ...) or in ``secret_fields``.
"""
import copy
import datetime
import math

from ...kernel import clock, geometry
from ...kernel.errors import ActionError, require
from ...kernel.predicates import root_room
from . import data as D

HANDS = "hands"
SYS = "_food_sys"       # food-owned bookkeeping section: thermal_full_t, air (runtime ice/water baths), labeled


def register(w, key, eid):
    """Remember a runtime-made food structure (key "air": an ice/water bath, "labeled": a handwritten label) so the
    food systems find it without scanning every entity."""
    current = (w.s.get(SYS) or {}).get(key) or []
    if eid not in current:
        w.sec(SYS)[key] = sorted(set(current) | {eid})


def add_damage(m, kind, severity):
    """Merge a damage kind into a mutable record's `damage` dict {kind: severity 0..1} (legacy lists converted)."""
    old = m.get("damage")
    dmg = dict(old) if isinstance(old, dict) else {k: 1.0 for k in old} if isinstance(old, list) else {}
    dmg[kind] = round(min(1.0, max(float(dmg.get(kind, 0.0)), float(severity))), 3)
    m["damage"] = dmg
    return dmg


def registered(state, key):
    ents = state["entities"]
    return [e for e in (state.get(SYS) or {}).get(key) or [] if e in ents]


# ----------------------------------------------------------------------
# basic access

def food(rec):
    value = rec.get("food") if isinstance(rec, dict) else None
    return value if isinstance(value, dict) else None


def is_food(state, eid):
    rec = state["entities"].get(eid)
    return rec is not None and food(rec) is not None


def kind(rec):
    f = food(rec)
    return f.get("kind") if f else None


def cat(rec):
    f = food(rec)
    return (f.get("cat") or "other") if f else None


def catinfo(rec):
    return D.CATS.get(cat(rec) or "other", D.CATS["other"])


def qty(rec):
    f = food(rec)
    return float(f.get("qty_g", 0.0)) if f else 0.0


def density(rec):
    f = food(rec) or {}
    return max(0.05, float(f.get("density", 1.0)))


def volume_ml(rec):
    return qty(rec) / density(rec)


def tare_kg(rec):
    return float(rec.get("tare_kg", 0.0))


def is_liquid(rec):
    return (food(rec) or {}).get("phase") == "liquid"


def is_raw_animal(rec):
    f = food(rec) or {}
    return bool(catinfo(rec).get("raw_animal")) and not f.get("cooked") and f.get("raw", True)


def is_rte(rec):
    f = food(rec) or {}
    return bool(f.get("rte", False))


CHEMICAL_CATEGORIES = ("cleaning_product", "detergent", "laundry_detergent", "chemical", "pesticide", "paint", "solvent",
                       "automotive_fluid", "pool_chemical", "fertilizer")
CHEMICAL_TAGS = ("chemical", "detergent", "pesticide", "solvent", "bleach")


def is_chemical(rec):
    """A household chemical that must not share a food shelf: toxic/corrosive hazards, a cleaning/detergent/paint-type
    product, a hazardous-waste chemical, or a chemical tag (cleaning tags its products 'chemical' / 'chem:<class>')."""
    if food(rec) is not None:
        return False
    if set(rec.get("hazards") or []) & {"toxic", "corrosive"}:
        return True
    if rec.get("category") in CHEMICAL_CATEGORIES or rec.get("haz_stream") == "chemicals":
        return True
    return any(t in CHEMICAL_TAGS or str(t).startswith("chem:") for t in (rec.get("tags") or []))


# Per-shelf load ratings (kg) behind admin_inventory's shelf-collapse rule: particleboard bookcase ~25, closet wire
# shelving ~25, wall cabinet ~15, base cabinet ~30, wardrobe/closet ~40, steel shelf unit ~90. Food furnishes and picks
# pantries within them so that a task's few cans never "collapse" a shelf the generator had already overloaded.
SHELF_RATINGS = (("shelf_unit", 90.0), ("bookshelf", 25.0), ("closet_shelving", 25.0), ("upper_cabinet", 15.0),
                 ("filing_cabinet", 30.0), ("cabinet", 30.0), ("wardrobe", 40.0), ("closet", 40.0))


def shelf_rating(rec):
    if rec.get("shelf_load_kg") is not None:
        return float(rec["shelf_load_kg"])
    for type_name, value in SHELF_RATINGS:
        if rec.get("type") == type_name or type_name in (rec.get("is_a") or []):
            return value
    return None


def shelf_load(entities, container, shelf):
    """Mass (kg, with contents) standing on one shelf of a container (records in a config or state dict)."""
    children = {}
    for eid, rec in entities.items():
        children.setdefault(rec.get("location"), []).append(eid)

    def tree(eid, seen):
        if eid in seen:
            return 0.0
        seen.add(eid)
        return float(entities[eid].get("mass_kg", 0.0) or 0.0) + sum(tree(c, seen) for c in children.get(eid, []))
    total, seen = 0.0, set()
    for eid in children.get(container, []):
        rec = entities[eid]
        if rec.get("relation") == "in" and int(rec.get("shelf", 0) or 0) == int(shelf):
            total += tree(eid, seen)
    return total


def is_package(rec):
    return bool(rec.get("package"))


def set_qty(w, eid, grams):
    """Set the food mass of an entity and keep mass/volume/count consistent."""
    m = w.mut(eid)
    f = m["food"]
    grams = max(0.0, round(float(grams), 3))
    old = float(f.get("qty_g", 0.0))
    f["qty_g"] = grams
    if f.get("unit_g") and f.get("count") is not None and f.get("discrete", True):
        f["count"] = int(round(grams / float(f["unit_g"]))) if grams > 0 else 0
    comp = f.get("composition")
    if isinstance(comp, dict) and comp and old > 0:
        scale = grams / old
        f["composition"] = {k: round(v * scale, 3) for k, v in sorted(comp.items())}
    m["mass_kg"] = round(tare_kg(m) + grams / 1000.0, 4)
    m["volume_l"] = round(float(m.get("tare_volume_l", 0.0)) + grams / density(m) / 1000.0, 4)
    if grams > 0:
        conform_to_vessel(w, eid)     # what is left in a pot or box sinks lower
    return grams


def today(state):
    return clock.now(state).date()


def parse_iso(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value[:10])
    except ValueError:
        return None


def iso(day):
    return day.isoformat()


def format_date(day, fmt):
    if fmt == "MDY":
        return "{:02d}/{:02d}/{:02d}".format(day.month, day.day, day.year % 100)
    if fmt == "DMY":
        return "{:02d}/{:02d}/{:04d}".format(day.day, day.month, day.year)
    if fmt == "YMD_CA":
        return "{} {} {:02d}".format(day.year, ["JA", "FE", "MR", "AL", "MA", "JN", "JL", "AU", "SE", "OC", "NO", "DE"][day.month - 1], day.day)
    return day.isoformat()


def region_date_format(state):
    region = state.get("metadata", {}).get("region", "us")
    fmt = D.DATE_FORMATS.get(region, "YMD")
    return "YMD_CA" if region == "ca" else fmt


def label_for_date(state, date_type, day):
    words = {"use_by": "USE BY", "best_before": "BEST BEFORE", "sell_by": "SELL BY", "packed_on": "PACKED ON"}
    return "{} {}".format(words.get(date_type, "EXP"), format_date(day, region_date_format(state)))


def expires(rec):
    return parse_iso(rec.get("_expires") or rec.get("expiration"))


# ----------------------------------------------------------------------
# entity creation, splitting and merging

PACKAGE_KEYS = ("package", "openable", "open", "date_label", "label_text", "tare_kg", "tare_volume_l", "sealed",
                "opener_required", "lid", "food_label", "_bulging", "_loose_lid", "liner_closed", "expiration",
                "storage_opened", "opened_life_days", "opened", "opened_on", "spos", "pos", "shelf", "held_hand",
                "_fifo_ok", "_report_check", "product")


def _new_id(w, stem):
    return w.new_id(stem or "food")


def portion_record(state, src, grams, units=None):
    """A copy of src describing an unpackaged portion of it (not yet in the world)."""
    rec = copy.deepcopy(src)
    for key in PACKAGE_KEYS:
        rec.pop(key, None)
    rec.pop("derived_from", None)          # take() sets it for portions of the same food
    f = rec["food"]
    if src.get("package"):
        rec["type"] = "food_item"
        rec["is_a"] = ["portable_object", "object"]
        rec["label"] = f.get("kind", "food").replace("_", " ")
        rec["accepts_items"] = bool(f.get("layerable", False))
        rec["container"] = False
        if src.get("opened_on") and not rec.get("made_on"):
            rec["_opened_on"] = src.get("opened_on")
        if src.get("opened_life_days"):
            rec["_opened_life_days"] = src.get("opened_life_days")
        if src.get("_expires") or src.get("expiration"):
            rec["_expires"] = src.get("_expires") or src.get("expiration")
        # what was printed on the package stays with the package: a scoop of it carries no label to read
        if src.get("date_type"):
            rec["_date_type"] = src.get("date_type")
        for key in ("date_type", "full_g", "reseal_needed"):
            rec.pop(key, None)
    total = float(src["food"].get("qty_g", 0.0))
    f["qty_g"] = round(float(grams), 3)
    if not f.get("composition"):
        f["composition"] = {f.get("kind", "food"): total}
    if isinstance(f.get("composition"), dict) and total > 0:
        f["composition"] = {k: round(v * grams / total, 3) for k, v in sorted(f["composition"].items())}
    if f.get("unit_g") and units is not None:
        f["count"] = int(units)
    elif f.get("unit_g") and f.get("count") is not None:
        f["count"] = int(round(grams / float(f["unit_g"])))
    rec["mass_kg"] = round(grams / 1000.0, 4)
    rec["volume_l"] = round(grams / density(rec) / 1000.0, 4)
    rec["size_m"] = portion_size(rec)
    rec["relation"] = "in"
    return rec


def portion_size(rec):
    """Footprint of a portion: bulk food spreads into a low mound/pile."""
    f = food(rec) or {}
    vol_m3 = max(1e-6, qty(rec) / density(rec) / 1e6)
    if f.get("phase") in ("liquid",):
        side = min(0.25, (vol_m3 / 0.03) ** 0.5)
        return [round(side, 3), round(side, 3), 0.03]
    if f.get("count") and f.get("unit_g") and f.get("cut", "whole") == "whole" and f.get("unit_size_m"):
        size = list(f["unit_size_m"])
        n = max(1, int(f["count"]))
        cols = int(math.ceil(n ** 0.5))
        return [round(min(0.4, size[0] * cols), 3), round(min(0.4, size[1] * cols), 3), size[2]]
    height = 0.04 if vol_m3 > 2e-4 else 0.025
    side = min(0.3, max(0.03, (vol_m3 / height) ** 0.5))
    return [round(side, 3), round(side, 3), height]


FLOWING_PHASES = ("liquid", "paste", "pieces", "granular", "powder")


def vessel_size(rec, parent):
    """size_m of bulk food lying in a vessel, or None: a liquid or paste fills the vessel floor wall to wall, a pile of
    pieces/powder spreads up to the walls (its natural mound footprint clipped to the floor, the whole floor when the
    mound would otherwise rise above the rim), and the height follows from the volume. Packaged food, solids and dough
    keep their own shape. Without this a 1.6 L scoop of chili kept a 20 x 20 cm mound footprint in a 22 x 16 cm box
    and stood 20 cm tall on its side, so the lid could not close."""
    f = food(rec) or {}
    inner = (parent or {}).get("inner_size_m")
    if not inner or f.get("phase") not in FLOWING_PHASES or rec.get("package") or rec.get("relation", "in") != "in":
        return None
    try:
        iw, idp, ih = float(inner[0]), float(inner[1]), float(inner[2])
    except (TypeError, ValueError, IndexError):
        return None
    if iw <= 0 or idp <= 0:
        return None
    vol = max(1e-6, qty(rec) / density(rec) / 1e6)
    if f.get("phase") in ("liquid", "paste"):
        x, y = iw, idp
    else:
        mound = portion_size(rec)
        x, y = min(float(mound[0]), iw), min(float(mound[1]), idp)
        if vol / (x * y) > ih:
            x, y = iw, idp
    return [round(x, 3), round(y, 3), round(max(0.002, vol / (x * y)), 4)]


def conform_to_vessel(w, eid):
    """Re-shape a food entity to the vessel it lies in (see vessel_size); no-op elsewhere."""
    rec = w.s["entities"].get(eid)
    if rec is None:
        return
    size = vessel_size(rec, w.s["entities"].get(rec.get("location")))
    if size is not None and rec.get("size_m") != size:
        w.mut(eid)["size_m"] = size


def spawn_food(w, kind_name, grams, location, relation="in", type_name=None, spos=None, **props):
    """Create a food entity (a typed kind when the registry knows it, else a generic food_item)."""
    tname = type_name or (kind_name if w.reg is not None and w.reg.known(kind_name) else "food_item")
    record = w.reg.make(tname, location, **props)
    f = record.setdefault("food", {})
    f.setdefault("kind", kind_name)
    f["qty_g"] = round(float(grams), 3)
    if not f.get("composition"):
        f["composition"] = {kind_name: round(float(grams), 3)}
    record["mass_kg"] = round(tare_kg(record) + grams / 1000.0, 4)
    record["volume_l"] = round(grams / density(record) / 1000.0, 4)
    if "size_m" not in props:
        record["size_m"] = portion_size(record)
    record["relation"] = relation
    record["label"] = props.get("label") or kind_name.replace("_", " ")
    eid = _new_id(w, kind_name)
    w.create(eid, record)
    if "size_m" not in props:
        conform_to_vessel(w, eid)
    if spos is not None:
        w.mut(eid)["spos"] = spos
    return eid


def place_new(w, eid, location, relation=None):
    """Put a freshly created entity into a location with a valid relation/position."""
    state = w.s
    m = w.mut(eid)
    m["location"] = location
    for key in ("spos", "pos", "shelf", "held_hand"):
        m.pop(key, None)
    if location in state["rooms"]:
        # food that falls lands at the robot's feet: the nearest free cell, else right where the robot stands (never a
        # far corner of the room)
        robot = state.get("robot") or {}
        here = tuple(robot["pos"]) if robot.get("room") == location and robot.get("pos") else None
        m["relation"] = "on"
        cell = geometry.free_floor_cell(state, location, near=here, ignore=(eid,))
        if cell is None:
            cell = geometry.free_floor_cell(state, location, near=here, ignore=(eid,), clearance=False, reachable=False)
        if cell is None and here is not None:
            cell = here
        if cell is None:
            doors = sorted(tuple(c) for c in geometry.doorway_cells(state, location))
            cell = doors[0] if doors else (0, 0)
        m["pos"] = [int(cell[0]), int(cell[1])]
        return
    parent = state["entities"][location]
    if relation is None:
        relation = "in" if parent.get("container") else "on"
    m["relation"] = relation
    if relation == "on":
        spot = geometry.surface_spot(state, location, eid)
        if spot is None:
            spot = [0.0, 0.0]
            m["stacked_loose"] = True
        m["spos"] = spot


def merge_into(w, dst_id, src):
    """Merge a food record (dict) or entity id into an existing food entity."""
    state = w.s
    src_rec = state["entities"][src] if isinstance(src, str) else src
    d = w.mut(dst_id)
    df, sf = d["food"], src_rec["food"]
    g_d, g_s = float(df.get("qty_g", 0.0)), float(sf.get("qty_g", 0.0))
    total = g_d + g_s
    if total <= 0:
        return dst_id
    comp = dict(df.get("composition") or {df.get("kind", "food"): g_d})
    for k, v in sorted((sf.get("composition") or {sf.get("kind", "food"): g_s}).items()):
        comp[k] = round(comp.get(k, 0.0) + v, 3)
    df["composition"] = comp
    t_d = float(d.get("temperature_c", 21.0))
    t_s = float(src_rec.get("temperature_c", 21.0))
    d["temperature_c"] = round((t_d * g_d + t_s * g_s) / total, 3)
    d["_core_c"] = round((float(d.get("_core_c", t_d)) * g_d + float(src_rec.get("_core_c", t_s)) * g_s) / total, 3)
    for key in ("_time_out_s", "_abuse_s", "_spoil"):
        d[key] = round(max(float(d.get(key, 0.0)), float(src_rec.get(key, 0.0))), 3)
    for key in ("_unsafe", "_abused", "_rotten"):
        if src_rec.get(key):
            d[key] = True
    contam = dict(d.get("contamination") or {})
    for k, v in sorted((src_rec.get("contamination") or {}).items()):
        contam[k] = round(max(contam.get(k, 0.0), float(v) * g_s / total if k.startswith("raw") or k == "bacteria" else float(v)), 4)
    for k, v in sorted(inherent_contam(src_rec).items()):
        if k.startswith("allergen_") or k in ("raw_meat", "kosher_meat", "kosher_dairy", "capsaicin"):
            contam[k] = round(max(contam.get(k, 0.0), v * (1.0 if not k.startswith("raw") else g_s / total)), 4)
    if contam:
        d["contamination"] = contam
        ensure_secret(d, "contamination")
    d["allergens"] = sorted(set(d.get("allergens", [])) | set(src_rec.get("allergens", [])))
    d["diet_tags"] = sorted(set(d.get("diet_tags", [])) | set(src_rec.get("diet_tags", [])))
    if src_rec.get("_trace_allergens"):
        d["_trace_allergens"] = sorted(set(d.get("_trace_allergens", [])) | set(src_rec["_trace_allergens"]))
    df["rte"] = bool(df.get("rte")) and bool(sf.get("rte"))
    if sf.get("raw") and catinfo(src_rec).get("raw_animal"):
        df["raw"] = True
        df["has_raw_animal"] = True
    for key in ("shell_fragments", "yolk_fat_g", "seeds"):
        if sf.get(key):
            df[key] = round(float(df.get(key, 0)) + float(sf[key]), 4)
    if df.get("count") is not None and sf.get("count") is not None and df.get("kind") == sf.get("kind"):
        df["count"] = int(df.get("count", 0)) + int(sf.get("count", 0))
    df["qty_g"] = round(total, 3)
    d["mass_kg"] = round(tare_kg(d) + total / 1000.0, 4)
    d["volume_l"] = round(float(d.get("tare_volume_l", 0.0)) + total / density(d) / 1000.0, 4)
    if not d.get("package"):
        d["size_m"] = portion_size(d)
        conform_to_vessel(w, dst_id)
    hist = list(df.get("history", []))
    hist.extend(sf.get("history", []))
    if hist:
        df["history"] = hist[-60:]
    # combined contents expire with the OLDEST part; a printed date on the keeper container may now be too late
    e_d, e_s = expires(d), expires(src_rec)
    if e_s is not None and (e_d is None or e_s < e_d):
        d["_expires"] = iso(e_s)
        if d.get("date_label") and e_d is not None:
            d["_date_label_late"] = True
    o_d, o_s = parse_iso(d.get("opened_on")), parse_iso(src_rec.get("opened_on") or src_rec.get("_opened_on"))
    if o_s is not None and (o_d is None or o_s < o_d):
        d["opened_on"] = iso(o_s)
    return dst_id


def take(w, src_id, grams=None, units=None):
    """Remove a portion from a food entity; returns the portion record (not in the world).

    The whole entity is NOT moved; callers place the returned record. grams/units are clamped
    to what is available.
    """
    src = w.ent(src_id)
    f = src["food"]
    available = float(f.get("qty_g", 0.0))
    if units is not None and f.get("unit_g"):
        units = max(0, min(int(units), int(f.get("count", 0))))
        grams = units * float(f["unit_g"])
    grams = max(0.0, min(available, float(grams if grams is not None else available)))
    rec = portion_record(w.s, src, grams, units)
    rec["derived_from"] = derivation_root(w.s, src_id)
    set_qty(w, src_id, available - grams)
    return rec, grams


def derivation_root(state, src_id):
    """The package (or whole item) a portion ultimately came from: a portion looks like that source until the robot
    has identified it (kernel realism identity via the hidden derived_from field)."""
    src = state["entities"].get(src_id) or {}
    root = src.get("derived_from")
    if isinstance(root, str) and root in state["entities"] and kind(state["entities"][root]) == kind(src):
        return root
    return src_id


def create_from_record(w, rec, location, relation="in", stem=None):
    rec = copy.deepcopy(rec)
    rec["location"] = location
    rec["relation"] = relation
    eid = _new_id(w, stem or (food(rec) or {}).get("kind", "food"))
    w.create(eid, rec)
    if location in w.s["rooms"] or relation == "on":
        place_new(w, eid, location, relation)
    else:
        conform_to_vessel(w, eid)
    return eid


def mergeable(a, b):
    fa, fb = food(a) or {}, food(b) or {}
    if fa.get("kind") != fb.get("kind") or fa.get("phase") != fb.get("phase"):
        return False
    if fa.get("cut", "whole") != fb.get("cut", "whole") or a.get("package") or b.get("package"):
        return False
    if fa.get("phase") not in ("liquid", "granular", "powder", "paste", "pieces", "dough"):
        return False
    return True


def deposit(w, rec, vessel, relation=None, merge=True):
    """Put a portion record into a vessel/surface, merging with matching bulk food there."""
    state = w.s
    if relation is None:
        parent = state["entities"].get(vessel)
        relation = "in" if parent is None or parent.get("container") or vessel in state["rooms"] else "on"
    if merge and vessel in state["entities"]:
        for other in w.children(vessel):
            orec = state["entities"][other]
            if food(orec) and orec.get("relation") == relation and mergeable(orec, rec):
                merge_into(w, other, rec)
                return other
    return create_from_record(w, rec, vessel, relation)


def whole_items(state, vessel):
    """How many whole, countable food items (eggs, potatoes, cans: food.count units of uncut solids) sit in a vessel."""
    total = 0
    for eid in vessel_foods(state, vessel):
        f = food(state["entities"][eid]) or {}
        if f.get("phase", "solid") == "solid" and f.get("cut", "whole") == "whole":
            total += max(1, int(f.get("count", 1) or 1))
    return total


def vessel_foods(state, vessel, relation=None):
    return [c for c in sorted(state["entities"]) if state["entities"][c].get("location") == vessel and food(state["entities"][c])
            and (relation is None or state["entities"][c].get("relation") == relation)]


def vessel_food_g(state, vessel):
    return sum(qty(state["entities"][c]) for c in vessel_foods(state, vessel))


def vessel_food_ml(state, vessel):
    total = sum(volume_ml(state["entities"][c]) for c in vessel_foods(state, vessel))
    total += float(state["entities"].get(vessel, {}).get("liquid_l", 0.0)) * 1000.0
    return total


def capacity_ml(rec):
    cap = float(rec.get("capacity_l", 0.0) or 0.0) * 1000.0
    if cap <= 0 and rec.get("inner_size_m"):
        s = rec["inner_size_m"]
        cap = s[0] * s[1] * s[2] * 1e6
    return cap


def update_fill(w, vessel):
    state = w.s
    rec = state["entities"].get(vessel)
    if rec is None:
        return
    cap = capacity_ml(rec)
    if cap <= 0:
        return
    fill = round(min(1.5, vessel_food_ml(state, vessel) / cap), 3)
    if rec.get("fill_level") != fill:
        w.mut(vessel)["fill_level"] = fill


def destroy_food(w, eid):
    for child in w.children(eid):
        destroy_food(w, child)
    w.destroy(eid)


def ensure_secret(m, key):
    secrets = list(m.get("secret_fields", []))
    if key not in secrets:
        secrets.append(key)
        m["secret_fields"] = secrets


# ----------------------------------------------------------------------
# contamination and contact

LIQUID_KINDS = ("water", "stock", "broth", "chicken_stock", "vegetable_stock", "milk", "whole_milk", "cream", "heavy_cream",
                "oat_milk", "almond_milk", "coconut_milk", "tomato_sauce", "wine")


def inherent_contam(rec):
    """Contamination a food item carries by its nature (sources, not stored tags)."""
    out = {}
    f = food(rec)
    if f is None:
        if rec.get("category") in ("trash", "litter", "waste", "waste_container") or rec.get("used") and rec.get("gear_kind"):
            out["bacteria"] = 0.8
        if set(rec.get("hazards", [])) & {"toxic", "corrosive"}:
            out["chemical"] = 1.0
        return out
    for allergen in rec.get("allergens", []):
        out["allergen_" + allergen] = 1.0
    if is_raw_animal(rec) or f.get("has_raw_animal") and not f.get("cooked"):
        out["raw_meat"] = 1.0
    if f.get("cat") == "egg" and f.get("raw", True) and not f.get("cooked") and f.get("phase") != "solid":
        out["raw_meat"] = max(out.get("raw_meat", 0.0), 0.3)   # Salmonella on/in raw egg
    if rec.get("_spoil", 0.0) >= 1.0 or rec.get("_rotten"):
        out["bacteria"] = 1.0
    tags = set(rec.get("diet_tags", []))
    if tags & {"meat", "poultry"}:
        out["kosher_meat"] = 1.0
    if "dairy" in tags:
        out["kosher_dairy"] = 1.0
    if f.get("capsaicin") and (f.get("cut", "whole") != "whole" or "seeds" not in f.get("parts", ["seeds"])):
        out["capsaicin"] = 1.0
    if f.get("cat") == "spice" and f.get("phase") in ("powder", "granular"):
        out["flavor_" + f.get("kind", "spice")] = 1.0
    return out


def stored_contam(state, target):
    if target == HANDS:
        return dict(state["robot"].get("hand_contamination") or {})
    return dict(state["entities"].get(target, {}).get("contamination") or {})


def effective_contam(state, target):
    """Stored tags plus what the item carries by nature; a closed package only shows its outside."""
    eff = stored_contam(state, target)
    if target != HANDS:
        rec = state["entities"].get(target, {})
        if rec.get("package") and rec.get("openable") and not rec.get("open"):
            return eff
        for k, v in inherent_contam(rec).items():
            eff[k] = max(eff.get(k, 0.0), v)
    return eff


def hands_target(state):
    """The robot's food-contact surface: worn disposable gloves, else its bare grippers."""
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("location") == "robot" and rec.get("relation") == "worn" and rec.get("gear_kind") == "disposable_gloves":
            return eid
    return HANDS


def _store(w, target, contam):
    contam = {k: round(min(1.0, v), 4) for k, v in sorted(contam.items()) if v > 1e-4}
    if target == HANDS:
        r = w.robot_m()
        r["hand_contamination"] = contam
        return
    m = w.mut(target)
    if contam or m.get("contamination"):
        m["contamination"] = contam
        ensure_secret(m, "contamination")


def add_contam(w, target, key, amount):
    contam = stored_contam(w.s, target)
    contam[key] = min(1.0, contam.get(key, 0.0) + float(amount))
    _store(w, target, contam)


def clear_contam(w, target, keys=None):
    contam = stored_contam(w.s, target)
    for key in list(contam):
        if keys is None or key in keys or any(key.startswith(p) for p in keys if p.endswith("_")):
            contam.pop(key, None)
    _store(w, target, contam)


def contact(w, a, b, k=0.4):
    """Two surfaces touch: contamination tags move from the dirtier to the cleaner side."""
    if a == b or a is None or b is None:
        return
    state = w.s
    ea, eb = effective_contam(state, a), effective_contam(state, b)
    sa, sb = stored_contam(state, a), stored_contam(state, b)
    changed_a = changed_b = False
    for key in sorted(set(ea) | set(eb)):
        va, vb = ea.get(key, 0.0), eb.get(key, 0.0)
        if va > vb + 1e-6:
            sb[key] = min(1.0, sb.get(key, 0.0) + k * (va - vb))
            changed_b = True
            if key in sa:
                sa[key] = max(0.0, sa[key] - 0.25 * k * (va - vb))
                changed_a = True
        elif vb > va + 1e-6:
            sa[key] = min(1.0, sa.get(key, 0.0) + k * (vb - va))
            changed_a = True
            if key in sb:
                sb[key] = max(0.0, sb[key] - 0.25 * k * (vb - va))
                changed_b = True
    if changed_a:
        _store(w, a, sa)
    if changed_b:
        _store(w, b, sb)
    if (a == HANDS or b == HANDS or state["entities"].get(a, {}).get("gear_kind") == "disposable_gloves"
            or state["entities"].get(b, {}).get("gear_kind") == "disposable_gloves"):
        other = b if a in (HANDS,) or state["entities"].get(a, {}).get("gear_kind") == "disposable_gloves" else a
        orec = state["entities"].get(other)
        if orec is not None and food(orec) and is_rte(orec):
            hand = HANDS if HANDS in (a, b) else (a if other == b else b)
            dirty = effective_contam(state, hand)
            bad = {k: v for k, v in dirty.items() if v >= D.CONTAM_THRESHOLD and (k in ("raw_meat", "bacteria", "pet", "chemical") or k.startswith("allergen_"))}
            log = w.sec("food_log")
            entry = {"time_s": state["time_s"], "item": other, "dirty": sorted(bad)}
            log.setdefault("rte_contacts", []).append(entry)
            del log["rte_contacts"][:-200]
            if bad:
                log["dirty_rte_contacts"] = log.get("dirty_rte_contacts", 0) + 1


def contaminated(state, eid, key, threshold=D.CONTAM_THRESHOLD):
    return stored_contam(state, eid).get(key, 0.0) >= threshold


def kill_pathogens(w, eid):
    contam = stored_contam(w.s, eid)
    changed = False
    for key in ("raw_meat", "bacteria"):
        if key in contam:
            contam.pop(key)
            changed = True
    if changed:
        _store(w, eid, contam)


# ----------------------------------------------------------------------
# thermal environment

def ambient(state, eid):
    """(ambient C, heat-transfer coefficient W/m2K, enclosure factor, heat sink id) for a food entity.

    Walks up the containment chain (the kernel only looks at the direct parent): a lidded
    container inside a fridge still sits in the fridge's air.
    """
    entities = state["entities"]
    rec = entities.get(eid, {})
    enclosed = 1.0
    if rec.get("package") and not rec.get("open"):
        enclosed *= 0.8
    if rec.get("covered") or (food(rec) or {}).get("covered"):
        enclosed *= 0.85
    location, seen, depth = rec.get("location"), {eid}, 0
    while location is not None and location not in seen and depth < 12:
        depth += 1
        seen.add(location)
        parent = entities.get(location)
        if parent is None:
            break
        if parent.get("internal_temp_c") is not None:
            t = float(parent["internal_temp_c"])
            if parent.get("ice_bath"):
                return t, D.H_WATER, enclosed, location
            if t >= 60:
                return t, D.H_OVEN * float(parent.get("heat_coupling", 1.0)), enclosed, location
            if t <= D.FREEZER_TEMP_MAX:
                return t, D.H_FREEZER, enclosed, location
            if t <= D.COLD_FRIDGE_MAX:
                return t, D.H_FRIDGE, enclosed, location
            return t, D.H_STILL_AIR * max(1.0, float(parent.get("heat_coupling", 1.0))), enclosed, location
        if parent.get("conducts_heat") and depth == 1 and _actively_heated(state, location) and not _cold_unit_part(state, parent):
            return float(parent.get("temperature_c", 21.0)), D.H_CONTACT, 1.0, None
        if parent.get("rack") and depth == 1:
            enclosed *= 1.6         # air reaches the underside on a cooling rack
        if parent.get("openable") and not parent.get("open") or parent.get("covered"):
            enclosed *= D.ENCLOSED_FACTOR
        location = parent.get("location")
    from ...kernel.physics import room_temperature
    rid = root_room(state, eid)
    if rid is None or rid not in state["rooms"]:
        return float(state.get("utilities", {}).get("indoor_temp_c", 21.0)), D.H_STILL_AIR, enclosed, None
    t = room_temperature(state, rid)
    room = state["rooms"][rid]
    h = D.H_STILL_AIR
    if room.get("outdoor"):
        h = D.H_STILL_AIR + 2.0 * float(state.get("weather", {}).get("wind_mps", 1.0))
        if state.get("weather", {}).get("condition") == "clear" and clock.daylight(state) > 0.6 and not rec.get("in_shade"):
            t += 8.0     # direct sun on food raises its effective ambient (solar gain)
    return t, h, enclosed, None


def _actively_heated(state, vessel):
    """A pot/pan on a burner, in an oven/fridge or in a water bath drives its contents; on a counter it does not."""
    rec = state["entities"].get(vessel, {})
    outer = state["entities"].get(rec.get("location"))
    if outer is None:
        return False
    return outer.get("internal_temp_c") is not None or bool(outer.get("conducts_heat")) or bool(outer.get("heat_source"))


def _cold_unit_part(state, rec):
    """A plastic bin/drawer that belongs to a refrigerator or freezer (appliance crisper drawers and door bins carry
    conducts_heat so the kernel moves their non-food contents with them). Food inside sits in the unit's air, it is
    not in contact with a heated/cooled pan."""
    outer = state["entities"].get(rec.get("location"))
    if outer is None or outer.get("internal_temp_c") is None or float(outer["internal_temp_c"]) >= 20.0:
        return False
    return rec.get("category") == "appliance_part" or rec.get("material") in ("plastic", "polypropylene", "acrylic")


def passive_vessel(state, vessel):
    rec = state["entities"].get(vessel, {})
    return bool(rec.get("conducts_heat")) and not _actively_heated(state, vessel)


def exposed_area_m2(rec):
    f = food(rec) or {}
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    a, b, c = [max(0.003, float(v)) for v in size[:3]]
    area = 2 * (a * b + a * c + b * c)
    pieces = int(f.get("pieces", 1) or 1)
    if pieces > 1 and f.get("phase") == "pieces":
        # a pile of pieces exposes roughly a third of the pieces' total surface
        pm = f.get("piece_mm") or [20, 20, 20]
        pa = 2 * ((pm[0] * pm[1]) + (pm[0] * pm[2]) + (pm[1] * pm[2])) / 1e6
        area = max(area, 0.33 * pieces * pa)
    return max(0.002, area)


def half_thickness_m(rec):
    f = food(rec) or {}
    if f.get("pieces", 1) and f.get("piece_mm") and int(f.get("pieces", 1)) > 1:
        return max(0.002, min(f["piece_mm"]) / 2000.0)
    size = rec.get("size_m") or [0.1, 0.1, 0.1]
    return max(0.002, min(float(v) for v in size[:3]) / 2.0)


# ----------------------------------------------------------------------
# storage zones

PANTRY_WORDS = ("pantry",)
KITCHEN_ROOMS = ("kitchen", "pantry", "dining_room")


def _tags(rec):
    return set(rec.get("tags", []))


def sub_zone_of(rec):
    zone = rec.get("fridge_zone")
    if zone:
        return zone
    tags = _tags(rec)
    label = (rec.get("label") or "").lower()
    if "crisper" in tags or "crisper" in label:
        hum = rec.get("humidity_setting") or rec.get("humidity") or (rec.get("settings") or {}).get("humidity") or "high"
        return "crisper_low" if hum in ("low", 0, "0") else "crisper_high"
    if "door_bin" in tags or "door" in label and "bin" in label:
        return "door"
    if "meat_drawer" in tags or "deli" in label or "meat drawer" in label:
        return "meat_drawer"
    return None


def zone_of(state, eid):
    """Where an item is stored, as a food-safety zone description."""
    entities = state["entities"]
    rec = entities.get(eid)
    info = {"zone": "room", "sub": None, "unit": None, "level": None, "contained": False, "covered": False,
            "container": None}
    if rec is None:
        return info
    f = food(rec) or {}
    info["covered"] = bool(rec.get("covered") or f.get("covered") or (rec.get("package") and not rec.get("open")))
    info["contained"] = bool(rec.get("package") and not rec.get("open") and not rec.get("_leaking"))
    location, seen, chain = rec.get("location"), {eid}, []
    while location is not None and location not in seen:
        seen.add(location)
        if location == "robot":
            info["zone"] = "robot"
            return info
        if location in state.get("people", {}):
            info["zone"] = "person"
            return info
        if location in state["rooms"]:
            break
        parent = entities.get(location)
        if parent is None:
            break
        chain.append(location)
        if info["container"] is None:
            info["container"] = location
        if parent.get("drip_catch") or (parent.get("food_safe") and parent.get("container")) or parent.get("category") in ("food_container", "bag"):
            info["contained"] = True
        closed = (parent.get("openable") and not parent.get("open")) or parent.get("covered")
        if closed and parent.get("internal_temp_c") is None and parent.get("category") not in ("appliance", "furniture"):
            info["covered"] = True
        sub = sub_zone_of(parent)
        if sub and info["sub"] is None:
            info["sub"] = sub
        if parent.get("internal_temp_c") is not None and not parent.get("insulated"):
            t = float(parent["internal_temp_c"])
            if t <= D.FREEZER_TEMP_MAX:
                info["zone"] = "freezer"
            elif t <= D.COLD_FRIDGE_MAX:
                info["zone"] = "fridge"
            else:
                info["zone"] = "hot" if t >= 50 else "room"
            info["unit"] = location
            info["level"] = round(geometry.height_m(state, eid) - geometry.height_m(state, location), 3)
            return info
        if parent.get("insulated"):
            info["zone"] = "lunchbox" if parent.get("category") == "lunch_box" else "cooler"
            info["unit"] = location
            return info
        if parent.get("waste_stream") and food(parent) is None:
            info["zone"] = "compost" if parent["waste_stream"] == "compost" else "trash"
            info["unit"] = location
            return info
        if parent.get("food_zone"):
            info["zone"] = parent["food_zone"]
            info["unit"] = location
            info["level"] = round(geometry.height_m(state, eid), 3)
            return info
        location = parent.get("location")
    rid = root_room(state, eid)
    room = state["rooms"].get(rid, {})
    if room.get("outdoor"):
        info["zone"] = "outdoor"
        return info
    labels = " ".join((entities[c].get("label") or "") for c in chain).lower()
    if room.get("type") == "pantry" or any(w in labels for w in PANTRY_WORDS) or any("pantry" in _tags(entities[c]) for c in chain):
        info["zone"] = "pantry"
        info["unit"] = chain[-1] if chain else None
        info["level"] = round(geometry.height_m(state, eid), 3)
        return info
    for c in chain:
        crec = entities[c]
        if "bread_box" in _tags(crec) or crec.get("type") == "bread_box":
            info["zone"] = "bread_box"
            return info
        if "fruit_bowl" in _tags(crec) or crec.get("type") == "fruit_bowl":
            info["zone"] = "fruit_bowl"
            return info
    if any(entities[c].get("type") in ("cabinet", "upper_cabinet", "drawer", "closet_shelving", "shelf_unit") or
           "cabinet" in entities[c].get("is_a", []) for c in chain) and room.get("type") in KITCHEN_ROOMS + ("basement", "garage", "utility_closet"):
        info["zone"] = "cupboard"
        info["unit"] = chain[-1]
        info["level"] = round(geometry.height_m(state, eid), 3)
        return info
    if chain and room.get("type") in KITCHEN_ROOMS:
        top = entities[chain[-1]]
        if top.get("surface") or top.get("category") in ("counter",) or "table" in top.get("is_a", []):
            info["zone"] = "counter"
            return info
    return info


def allowed_zones(state, rec):
    f = food(rec) or {}
    rules = (state.get("household") or {}).get("food_storage") or {}
    k = f.get("kind")
    if k in rules:
        return {rules[k]} if isinstance(rules[k], str) else set(rules[k])
    storage = rec.get("storage") or "pantry"
    if (rec.get("opened") or rec.get("_opened_on")) and rec.get("storage_opened"):
        storage = rec["storage_opened"]
    if not rec.get("package") and rec.get("storage_opened") and f.get("phase") in ("liquid", "paste"):
        storage = rec["storage_opened"]
    allowed = set(D.STORAGE_ALLOWED.get(storage, {storage}))
    if storage == "freezer" and f.get("cat") not in ("frozen_dessert", "ice"):
        allowed.add("fridge")       # thawing in the refrigerator is the safe method
    if rec.get("fridge_ok"):
        allowed.add("fridge")
    if rec.get("perishable") and storage not in ("freezer",):
        allowed = {z for z in allowed if z in ("fridge", "freezer", "cooler", "lunchbox")} or {"fridge"}
    if f.get("freezable") and storage == "fridge" and rec.get("freezer_ok"):
        allowed.add("freezer")
    return allowed


def unit_groups(state, zones=None):
    """cold unit id -> {"raw": [(level, eid)], "rte": [(level, eid)]} for fridge zone checks."""
    groups = {}
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if not food(rec):
            continue
        z = zones[eid] if zones is not None and eid in zones else zone_of(state, eid)
        if z["zone"] != "fridge" or z["level"] is None:
            continue
        g = groups.setdefault(z["unit"], {"raw": [], "rte": [], "zones": {}})
        g["zones"][eid] = z
        if is_raw_animal(rec):
            g["raw"].append((z["level"], eid))
        elif is_rte(rec):
            g["rte"].append((z["level"], eid))
    return groups


def children_index(state):
    """parent -> sorted child ids (one pass over the entity table; each child list sorted)."""
    index = {}
    for eid, rec in state["entities"].items():
        index.setdefault(rec.get("location"), []).append(eid)
    for kids in index.values():
        kids.sort()
    return index


def storage_check(state, eid, groups=None, zinfo=None, index=None):
    """(ok, issue) for an item's current storage place against food-safety storage rules."""
    rec = state["entities"][eid]
    index = index if index is not None else children_index(state)
    f = food(rec) or {}
    z = zinfo or zone_of(state, eid)
    allowed = allowed_zones(state, rec)
    zone = z["zone"]
    if zone in ("trash", "compost"):
        return True, None
    if zone not in allowed:
        return False, "wrong_zone:{}".format(zone)
    if zone == "fridge":
        groups = groups if groups is not None else unit_groups(state)
        g = groups.get(z["unit"], {"raw": [], "rte": []})
        level = z["level"] if z["level"] is not None else 0.0
        if is_raw_animal(rec):
            if any(lv < level - 0.05 for lv, other in g["rte"]):
                return False, "raw_above_ready_to_eat"
            if not z["contained"]:
                return False, "raw_meat_not_contained"
        elif is_rte(rec):
            if any(lv > level + 0.05 for lv, other in g["raw"]):
                return False, "below_raw_meat"
        if rec.get("no_door") and z["sub"] == "door":
            return False, "warm_door_zone"
        hum = f.get("humidity")
        if hum and z["sub"] in ("crisper_high", "crisper_low") and z["sub"] != D.HUMIDITY_CLASS.get(hum):
            return False, "wrong_crisper"
        if hum and rec.get("needs_crisper") and z["sub"] not in ("crisper_high", "crisper_low") and _unit_has_crisper(state, z["unit"], index):
            return False, "not_in_crisper"
        if f.get("ethylene") in ("sensitive", "producer") and z["sub"] in ("crisper_high", "crisper_low"):
            drawer = _sub_container(state, eid, z["sub"])
            clash = "producer" if f.get("ethylene") == "sensitive" else "sensitive"
            for other in index.get(drawer, []) if drawer else []:
                if (food(state["entities"][other]) or {}).get("ethylene") == clash:
                    return False, "ethylene_exposure"
        if f.get("cat") in ("prepared",) and not z["covered"]:
            return False, "uncovered"
        if f.get("wet", 0.0) > 0.3 and f.get("cat") in ("berry", "leafy_green", "herb"):
            return False, "stored_wet"
    if zone in ("pantry", "cupboard"):
        if float(rec.get("mass_kg", 0)) > 1.5 and z["level"] is not None and z["level"] > 1.5:
            return False, "heavy_item_high"
        apart = f.get("store_apart")
        siblings = index.get(z["container"], []) if z["container"] else []
        if apart:
            for other in siblings:
                orec = state["entities"][other]
                if other != eid and orec.get("shelf") == rec.get("shelf") and kind(orec) in apart:
                    return False, "stored_with_" + kind(orec)
        for other in siblings:
            orec = state["entities"][other]
            if orec.get("shelf") == rec.get("shelf") and is_chemical(orec):
                return False, "chemical_on_food_shelf"
        if rec.get("package") and rec.get("open") and rec.get("reseal_needed", True):
            return False, "left_open"
    if zone == "freezer" and f.get("phase") == "liquid":
        parent = state["entities"].get(rec.get("location"), {})
        if float(parent.get("fill_level", 0) or 0) > 0.9:
            return False, "no_headspace"
    if zone == "fridge" and z["container"] and rec.get("spos") is not None:
        parent = state["entities"][z["container"]]
        depth = shelf_inner(parent)[1]
        if parent.get("internal_temp_c") is not None and rec["spos"][1] + geometry.footprint_m(rec)[1] > depth - 0.03:
            return False, "blocks_rear_vent"
    if z["container"] and state["entities"][z["container"]].get("leaks"):
        return False, "container_not_sealed"
    if rec.get("served_to") and rec.get("eaten"):
        return False, "plate_scraps_saved"
    return True, None


def _unit_has_crisper(state, unit, index=None):
    ids = index.get(unit, []) if index is not None else [e for e in sorted(state["entities"]) if state["entities"][e].get("location") == unit]
    return any(sub_zone_of(state["entities"][e]) in ("crisper_high", "crisper_low") for e in ids)


def _sub_container(state, eid, sub):
    location, seen = state["entities"][eid].get("location"), set()
    while location in state["entities"] and location not in seen:
        seen.add(location)
        if sub_zone_of(state["entities"][location]) == sub:
            return location
        location = state["entities"][location].get("location")
    return None


# ----------------------------------------------------------------------
# dates, spoilage status

def opened_on(rec):
    return parse_iso(rec.get("opened_on") or rec.get("_opened_on"))


def status(state, rec, day=None):
    """Ground-truth food status: class in unsafe | quality_expired | expiring_soon | ok."""
    f = food(rec) or {}
    reasons = []
    day = day if day is not None else today(state)
    exp = expires(rec)
    date_type = rec.get("date_type") or rec.get("_date_type") or "best_before"
    days_left = None
    if exp is not None:
        days_left = (exp - day).days
    spoil = float(rec.get("_spoil", 0.0))
    if spoil >= 1.0 or rec.get("_rotten"):
        reasons.append("spoiled")
    if rec.get("_bulging") or rec.get("_leaking"):
        reasons.append("damaged_can")
    if rec.get("_abused") or rec.get("_unsafe"):
        reasons.append("time_temperature_abuse")
    if exp is not None and days_left < 0 and date_type == "use_by":
        reasons.append("past_use_by")
    oo = opened_on(rec)
    life = rec.get("opened_life_days") or rec.get("_opened_life_days")
    if oo is not None and life:
        left = (oo + datetime.timedelta(days=int(life)) - day).days
        days_left = left if days_left is None else min(days_left, left)
        if left < 0:
            reasons.append("past_opened_life")
    made = parse_iso(f.get("made_on") or rec.get("made_on") or rec.get("stored_on"))
    if made is not None and f.get("cat") in ("prepared", "mixture", "baby"):
        left = (made + datetime.timedelta(days=D.LEFTOVER_DAYS) - day).days
        days_left = left if days_left is None else min(days_left, left)
        if left < 0:
            reasons.append("leftover_too_old")
    window = int(((state.get("household") or {}).get("food_rules") or {}).get("expiring_window_days", 3))
    if reasons:
        klass = "unsafe"
    elif exp is not None and days_left is not None and days_left < 0 and date_type in ("best_before", "sell_by"):
        if rec.get("perishable") and date_type == "sell_by" and days_left < -5:
            klass = "unsafe"
            reasons.append("sell_by_long_past")
        else:
            klass = "quality_expired"
    elif days_left is not None and days_left <= window:
        klass = "expiring_soon"
    else:
        klass = "ok"
    return {"class": klass, "reasons": reasons, "days_left": days_left}


def appearance(rec):
    f = food(rec) or {}
    info = catinfo(rec)
    spoil = float(rec.get("_spoil", 0.0))
    words = []
    if f.get("ice", 0.0) > 0.8:
        words.append("frozen solid")
    elif f.get("ice", 0.0) > 0.2:
        words.append("partly frozen")
    if spoil >= 1.0 and info.get("mold"):
        words.append("fuzzy mold spots")
    if spoil >= 1.0 and info.get("slime"):
        words.append("slimy surface, grey patches")
    if info.get("wilt") and spoil >= 0.7:
        words.append("wilted, yellowing leaves")
    if f.get("browning", 0.0) > 0.4:
        words.append("browned cut surfaces")
    if f.get("soggy", 0.0) > 0.5:
        words.append("soggy")
    if f.get("bruise", 0.0) > 0.3 or f.get("crushed"):
        words.append("bruised/crushed")
    if rec.get("_bulging"):
        words.append("bulging lid")
    if f.get("burnt", 0.0) > 0.3:
        words.append("burnt")
    ripe = f.get("ripeness")
    if ripe is not None and f.get("ripens"):
        words.append("unripe, hard and green" if ripe < 0.5 else "ripe" if ripe < 0.95 else "overripe, very soft")
    if f.get("melted", 0.0) > 0.3:
        words.append("melted and runny")
    if not words:
        words.append("looks fresh" if spoil < 0.8 else "looks dull")
    return ", ".join(words)


def odor(rec):
    f = food(rec) or {}
    info = catinfo(rec)
    if rec.get("_rotten"):
        return "sulfurous rotten-egg smell"
    spoil = float(rec.get("_spoil", 0.0))
    if spoil >= 0.9 and info.get("odor"):
        return "sour, off odor" if f.get("cat") in ("dairy", "cheese_soft", "baby", "prepared") else "putrid, ammonia-like odor"
    if spoil >= 1.0:
        return "musty odor"
    return "normal"


# ----------------------------------------------------------------------
# eaters

def profile(state, pid):
    person = state.get("people", {}).get(pid, {})
    diet = person.get("diet") or []
    if isinstance(diet, str):
        diet = [diet]
    prefs = person.get("preferences") or {}
    fprefs = prefs.get("food", {}) if isinstance(prefs.get("food", {}), dict) else {}
    dislikes = set(fprefs.get("dislikes", [])) | set(person.get("dislikes", []) if isinstance(person.get("dislikes"), list) else [])
    texture = None
    for d in diet:
        if isinstance(d, str) and d.startswith("texture:"):
            texture = d.split(":", 1)[1]
    vulnerable = bool(person.get("pregnant") or person.get("immunocompromised")) or "vulnerable" in set(d for d in diet if isinstance(d, str))
    age = person.get("age", 35 if person.get("role", "adult") == "adult" else 8)
    if age is not None and (age < 2 or age >= 70):
        vulnerable = True
    return {"age": age, "vulnerable": vulnerable,
            "role": person.get("role", "adult"),
            "allergies": set(person.get("allergies", []) or []),
            "diet": set(d for d in diet if isinstance(d, str)),
            "dislikes": dislikes, "texture": texture,
            "sodium_mg": fprefs.get("sodium_limit_mg"), "carb_g": fprefs.get("carb_limit_g")}


def food_tags(rec):
    f = food(rec) or {}
    tags = set(rec.get("diet_tags", []))
    tags |= set(rec.get("allergens", []))
    tags.add(f.get("kind", ""))
    tags.add(f.get("cat", ""))
    tags |= set((f.get("composition") or {}).keys())
    return tags


def choking_issue(rec, age):
    f = food(rec) or {}
    k = f.get("kind", "")
    if age is None or age >= D.CHOKING_AGE:
        return None
    if k in D.ALWAYS_HAZARD_UNDER_4 or f.get("choking") == "always":
        return "choking:" + k
    piece = f.get("piece_mm")
    if f.get("round") or f.get("choking") == "round":
        if f.get("cut", "whole") == "whole":
            return "choking:whole_" + k
        if f.get("round_pieces", False) and piece and sorted(piece)[1] >= D.ROUND_HAZARD_MM:
            return "choking:round_pieces_" + k
    if f.get("hard") and not f.get("cooked") and f.get("phase") not in ("paste", "liquid", "granular", "powder"):
        dims = piece or [v * 1000 for v in (rec.get("size_m") or [0.05, 0.05, 0.05])]
        if f.get("cut") not in ("grated", "shredded", "minced", "mashed", "puree", "thin_sliced") and sorted(dims)[1] >= D.HARD_HAZARD_MM:
            return "choking:hard_" + k
    if f.get("bones", 0) or "bone" in f.get("parts", []) or "pin_bones" in f.get("parts", []) or rec.get("_pin_bones"):
        return "choking:bones_" + k
    if "pit" in f.get("parts", []):
        return "choking:pit_" + k
    return None


def eater_issues(state, pid, food_ids):
    """Hard-constraint problems if person pid eats these food entities."""
    p = profile(state, pid)
    issues = []
    for fid in food_ids:
        rec = state["entities"].get(fid)
        if rec is None or not food(rec):
            continue
        f = food(rec)
        tags = food_tags(rec)
        contam = stored_contam(state, fid)
        for allergen in sorted(p["allergies"]):
            if allergen in tags or contam.get("allergen_" + allergen, 0.0) >= 0.01 or allergen in rec.get("_trace_allergens", []):
                issues.append("allergen:{}:{}".format(allergen, fid))
            if allergen == "tree_nut" and tags & {"almond", "walnut", "cashew", "pecan", "hazelnut", "pistachio"}:
                issues.append("allergen:{}:{}".format(allergen, fid))
        for diet in sorted(p["diet"]):
            forbidden = D.DIET_FORBIDDEN.get(diet, set())
            hit = forbidden & tags
            if hit:
                issues.append("diet:{}:{}:{}".format(diet, sorted(hit)[0], fid))
            if diet == "gluten_free" and contam.get("allergen_wheat", 0.0) + contam.get("allergen_gluten", 0.0) >= 0.01:
                issues.append("diet:gluten_free:cross_contact:{}".format(fid))
            if diet == "kosher" and ("meat" in tags or "poultry" in tags) and ("dairy" in tags or contam.get("kosher_dairy", 0) >= 0.05):
                issues.append("diet:kosher:meat_with_dairy:{}".format(fid))
            if diet == "kosher" and "dairy" in tags and contam.get("kosher_meat", 0) >= 0.05:
                issues.append("diet:kosher:dairy_with_meat:{}".format(fid))
            if diet in ("vegetarian", "vegan") and contam.get("kosher_meat", 0) >= 0.05 and not tags & {"meat", "poultry"}:
                issues.append("diet:{}:meat_contact:{}".format(diet, fid))
        if rec.get("_spoil", 0.0) >= 1.0 or rec.get("_rotten") or rec.get("_abused") or rec.get("_unsafe"):
            issues.append("unsafe_food:{}".format(fid))
        if is_raw_animal(rec) and not f.get("intended_raw"):
            issues.append("raw_animal:{}".format(fid))
        if p["vulnerable"] and (f.get("cat") == "egg" or set(f.get("composition") or {}) & {"egg", "raw_egg", "egg_yolk", "egg_white"}
                                or f.get("kind") in ("raw_egg", "beaten_egg", "scrambled_eggs")) \
                and float(rec.get("_peak_core_c", -50)) < 71.0 and not f.get("pasteurized"):
            issues.append("vulnerable_runny_egg:{}".format(fid))
        if p["vulnerable"] and f.get("intended_raw"):
            issues.append("vulnerable_raw:{}".format(fid))
        if rec.get("served_to") and rec.get("served_to") != pid and rec.get("eaten"):
            issues.append("plate_scraps:{}".format(fid))
        if is_rte(rec) and contam.get("raw_meat", 0.0) >= D.CONTAM_THRESHOLD:
            issues.append("pathogen:{}".format(fid))
        safe = D.SAFE_CORE_C.get(f.get("cat"))
        if f.get("needs_cooking") and safe and float(rec.get("_peak_core_c", -50)) < safe:
            issues.append("undercooked:{}".format(fid))
        choke = choking_issue(rec, p["age"])
        if choke:
            issues.append("{}:{}".format(choke, fid))
        if p["age"] is not None and p["age"] < 1 and tags & D.INFANT_FORBIDDEN:
            issues.append("infant_forbidden:{}".format(fid))
        limit = D.INFANT_SERVE_MAX_C if p["age"] is not None and p["age"] < 2 else D.CHILD_SERVE_MAX_C if p["age"] is not None and p["age"] < 10 else D.ADULT_SERVE_MAX_C
        if float(rec.get("temperature_c", 21.0)) > limit:
            issues.append("too_hot:{}".format(fid))
        if "alcohol" in tags and p["age"] is not None and p["age"] < 18:
            issues.append("alcohol_minor:{}".format(fid))
        if p["texture"] in D.TEXTURE_MAX_MM and f.get("phase") not in ("liquid", "paste"):
            limit_mm = D.TEXTURE_MAX_MM[p["texture"]]
            piece = f.get("piece_mm") or [v * 1000 for v in (rec.get("size_m") or [0.05, 0.05, 0.05])]
            if max(piece) > limit_mm + 1e-6:
                issues.append("texture:{}:{}".format(p["texture"], fid))
    if p["sodium_mg"] or "low_sodium" in p["diet"]:
        limit = float(p["sodium_mg"] or D.LOW_SODIUM_MG_PER_MEAL)
        salt = sum(float((food(state["entities"][f]) or {}).get("composition", {}).get("salt", 0.0)) for f in food_ids if f in state["entities"])
        if salt * D.SODIUM_MG_PER_G_SALT > limit:
            issues.append("sodium:{:.0f}mg".format(salt * D.SODIUM_MG_PER_G_SALT))
    return issues


def preference_issues(state, pid, food_ids):
    p = profile(state, pid)
    person = state.get("people", {}).get(pid, {})
    fprefs = ((person.get("preferences") or {}).get("food") or {})
    out = []
    for fid in food_ids:
        rec = state["entities"].get(fid)
        if rec and food(rec) and food_tags(rec) & p["dislikes"]:
            out.append("dislike:{}:{}".format(sorted(food_tags(rec) & p["dislikes"])[0], fid))
        if rec and food(rec) and "crust" in fprefs.get("remove", []) and "crust" in (food(rec).get("parts") or []):
            out.append("crust_left:{}".format(fid))
    if fprefs.get("no_touching"):
        recs = [(f, state["entities"][f]) for f in food_ids if f in state["entities"]]
        for fid, rec in recs:
            if rec.get("relation") == "on" and food(state["entities"].get(rec.get("location"), {}) or {}):
                out.append("touching:{}".format(fid))
        placed = [(f, r) for f, r in recs if r.get("spos") is not None and not food(state["entities"].get(r.get("location"), {}) or {})]
        for i, (a, ra) in enumerate(placed):
            for b, rb in placed[i + 1:]:
                if ra.get("location") != rb.get("location"):
                    continue
                pa, sa = ra["spos"], geometry.footprint_m(ra)
                pb, sb = rb["spos"], geometry.footprint_m(rb)
                if geometry.rect_overlap(pa, sa, [pb[0] - 0.005, pb[1] - 0.005], [sb[0] + 0.01, sb[1] + 0.01]):
                    out.append("touching:{}:{}".format(a, b))
    return out


def policy_issues(state, container, food_ids):
    """Container rules: nut_free (school lunches), carry_on_liquids (flight bags: gels/liquids <= 100 ml)."""
    rec = state["entities"].get(container, {})
    out = []
    for rule in rec.get("policy", []) or []:
        for fid in food_ids:
            frec = state["entities"].get(fid, {})
            tags = food_tags(frec)
            if rule == "nut_free" and (tags & {"peanut", "tree_nut", "almond"} or set(frec.get("_trace_allergens", [])) & {"peanut", "tree_nut"}):
                out.append("nut_free:{}".format(fid))
            if rule == "carry_on_liquids" and (food(frec) or {}).get("phase") in ("liquid", "paste") and volume_ml(frec) > 100.0:
                out.append("carry_on_liquids:{}".format(fid))
            if rule == "no_glass" and frec.get("material") == "glass":
                out.append("no_glass:{}".format(fid))
    return out


def recipe_check(state, vessel, recipe):
    """Compare what went into a vessel (its _mix_log) with a recipe: amounts per ingredient (+/- tolerance,
    functional substitutes allowed) and ordering constraints [[before_kind, after_kind], ...] where "mix"
    can name a mixing step: ["cream:butter", "egg"] means butter was creamed before the egg was added."""
    logs = state["entities"].get(vessel, {}).get("_mix_log", []) or []
    added, order, creamed = {}, [], set()
    for i, entry in enumerate(logs):
        if entry.get("op") == "add":
            k = entry.get("kind")
            added[k] = added.get(k, 0.0) + float(entry.get("g", 0.0))
            order.append((i, k))
        elif entry.get("op") in ("mix", "whisk") and entry.get("method") == "cream":
            for k in entry.get("kinds", []):
                creamed.add(k)
                order.append((i, "cream:" + k))
    out = {"amounts_ok": True, "order_ok": True, "missing": [], "wrong_amount": [], "substituted": []}
    subs = D.SUBSTITUTES
    for item in recipe.get("ingredients", []):
        k, grams = item["kind"], float(item.get("g", 0.0))
        have = added.get(k, 0.0)
        if have <= 0 and k in subs:
            for alt in subs[k]:
                parts = alt["parts"]
                if all(added.get(pk, 0.0) > 0 for pk in parts):
                    ratio_ok = all(abs(added[pk] - grams * frac) <= max(2.0, 0.1 * grams * frac) for pk, frac in parts.items())
                    if ratio_ok:
                        out["substituted"].append(k)
                        have = grams
                    break
        if have <= 0:
            out["missing"].append(k)
            out["amounts_ok"] = False
        elif grams and abs(have - grams) > max(1.0, float(item.get("tol", 0.05)) * grams):
            out["wrong_amount"].append(k)
            out["amounts_ok"] = False
    for before, after_ in recipe.get("order", []):
        ib = [i for i, k in order if k == before]
        ia = [i for i, k in order if k == after_]
        if ib and ia and min(ib) > min(ia):
            out["order_ok"] = False
        if not ib and ia:
            out["order_ok"] = False
    out["ok"] = out["amounts_ok"] and out["order_ok"]
    return out


def label_check(state, container, index=None):
    """Does a container's written label match what is inside (contents, cook/freeze date, unambiguous)?"""
    rec = state["entities"].get(container, {})
    lab = rec.get("food_label")
    if not lab:
        return None
    kids = index.get(container, []) if index is not None else         [f for f in sorted(state["entities"]) if state["entities"][f].get("location") == container]
    foods = [f for f in kids if food(state["entities"][f])]
    kinds = {food(state["entities"][f]).get("kind") for f in foods}
    dishes = {state["entities"][f].get("label", "").lower().replace(" ", "_") for f in foods}
    contents = (lab.get("contents") or "")
    contents_ok = bool(contents) and any(contents == k or contents in k or k in contents for k in kinds | dishes if k)
    made = sorted({food(state["entities"][f]).get("made_on") or state["entities"][f].get("made_on") for f in foods} - {None})
    date_ok = (not made) or bool(set(made) & set(lab.get("dates", [])))
    return {"contents_ok": contents_ok, "date_ok": date_ok, "unambiguous": not lab.get("ambiguous_date"),
            "attached": not lab.get("fell_off"), "ok": contents_ok and date_ok and not lab.get("ambiguous_date") and not lab.get("fell_off")}


def dish_foods(state, root, index=None):
    index = index if index is not None else children_index(state)
    ids = []
    if root in state["entities"] and food(state["entities"][root]):
        ids.append(root)
    stack, seen = [root], set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for child in index.get(cur, []):
            if food(state["entities"][child]):
                ids.append(child)
            stack.append(child)
    return sorted(set(ids))


# ----------------------------------------------------------------------
# spills, scraps

SPILL_HARM_G = 5.0        # ~1 teaspoon: smaller food losses soil the surface but are not counted as a spill harm


def spill(w, near, kind_name, grams, phase="solid"):
    """Food lands outside its vessel: soils the surface below (or the floor) and counts a spill."""
    state = w.s
    grams = round(float(grams), 2)
    if grams <= 0.5:
        return None
    soil_kind = {"liquid": "food", "granular": "crumbs", "powder": "dust", "paste": "food", "oil": "grease"}.get(phase, "food")
    if kind_name in ("oil", "olive_oil", "vegetable_oil", "bacon_grease", "fat"):
        soil_kind = "grease"
    if kind_name in ("sugar", "honey", "syrup", "juice", "soda", "jam"):
        soil_kind = "sticky"
    target, seen = state["entities"].get(near, {}).get("location"), set()
    while target in state["entities"] and target not in seen:
        seen.add(target)
        rec = state["entities"][target]
        if rec.get("surface") or rec.get("category") in ("counter", "furniture", "floor") or rec.get("ground_layer"):
            break
        target = rec.get("location")
    from ..core import add_soil
    if target in state["entities"]:
        add_soil(w, target, soil_kind, min(0.6, 0.02 + grams / 300.0))
    else:
        rid = target if target in state["rooms"] else root_room(state, near)
        floor = "floor_" + rid if rid and "floor_" + rid in state["entities"] else None
        if floor:
            add_soil(w, floor, soil_kind, min(0.6, 0.02 + grams / 300.0))
        if phase == "liquid" and rid in state["rooms"]:
            room = w.room_m(rid)
            cell = list(state["robot"].get("pos") or [0, 0])
            if cell not in room.setdefault("wet_cells", []):
                room["wet_cells"].append(cell)
    if grams >= SPILL_HARM_G:
        w.stat("spills")      # a global harm flag since kernel v2.1: crumbs and drips below a teaspoon only soil
    w.emit("food_spill", food=kind_name, grams=grams)
    return target


def add_scraps(w, where, label, grams, stream, source=None):
    """Add inedible trimmings to the scraps pile on a board (or into a scrap bowl/bin)."""
    state = w.s
    grams = round(float(grams), 2)
    if grams <= 0:
        return None
    rel = "in" if state["entities"].get(where, {}).get("container") else "on"
    for other in w.children(where):
        orec = state["entities"][other]
        if orec.get("type") == "prep_scraps" and orec.get("waste_stream") == stream and orec.get("relation") == rel:
            m = w.mut(other)
            m["food"]["qty_g"] = round(m["food"]["qty_g"] + grams, 2)
            m["food"]["composition"][label] = round(m["food"]["composition"].get(label, 0.0) + grams, 2)
            m["mass_kg"] = round(m["food"]["qty_g"] / 1000.0, 4)
            m["volume_l"] = round(m["food"]["qty_g"] / 600.0, 4)
            if source is not None:
                contact(w, source, other, 0.6)
            return other
    eid = spawn_food(w, "scraps", grams, where, relation=rel, type_name="prep_scraps",
                     label="{} scraps".format(label.replace("_", " ")), waste_stream=stream)
    m = w.mut(eid)
    m["food"]["composition"] = {label: grams}
    if rel == "on":
        spot = geometry.surface_spot(state, where, eid)
        if spot is None:
            m["spos"] = [0.0, 0.0]
            m["stacked_loose"] = True
        else:
            m["spos"] = spot
    if source is not None:
        contact(w, source, eid, 0.6)
    return eid


# ----------------------------------------------------------------------
# shelf positions (front/back) for FIFO

def shelf_inner(rec):
    inner = rec.get("inner_size_m") or rec.get("size_m") or [0.5, 0.4, 0.3]
    return float(inner[0]), float(inner[1])


def shelf_spot(state, container, item, shelf=None, want=None, step=0.02):
    """[x, depth] on a shelf level inside a container; depth 0 is the front edge."""
    parent = state["entities"][container]
    width, depth = shelf_inner(parent)
    rec = state["entities"][item]
    iw, idp = geometry.footprint_m(rec)
    if iw > width + 1e-9 or idp > depth + 1e-9:
        return None
    others = []
    for other in sorted(state["entities"]):
        orec = state["entities"][other]
        if other == item or orec.get("location") != container or orec.get("relation") != "in" or orec.get("spos") is None:
            continue
        if orec.get("shelf") != shelf:
            continue
        others.append((orec["spos"], geometry.footprint_m(orec)))

    def fits(pos):
        if pos[0] < -1e-9 or pos[1] < -1e-9 or pos[0] + iw > width + 1e-9 or pos[1] + idp > depth + 1e-9:
            return False
        return not any(geometry.rect_overlap(pos, (iw, idp), opos, osize) for opos, osize in others)
    if want is not None:
        want = [round(float(want[0]), 3), round(float(want[1]), 3)]
        return want if fits(want) else None
    y = 0.0
    while y + idp <= depth + 1e-9:
        x = 0.0
        while x + iw <= width + 1e-9:
            if fits((x, y)):
                return [round(x, 3), round(y, 3)]
            x += step
        y += step
    return None


def product_key(rec):
    return rec.get("product") or kind(rec)


def fifo_flags(state, container, shelf):
    """{eid: ok} for food on one shelf level: earlier-expiring units must sit in front of later ones."""
    items = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("location") == container and rec.get("relation") == "in" and rec.get("shelf") == shelf and food(rec) and rec.get("spos") is not None:
            exp = expires(rec)
            if exp is not None:
                items.append((eid, rec, exp))
    flags = {}
    for eid, rec, exp in items:
        ok = True
        for oid, orec, oexp in items:
            if oid == eid or product_key(orec) != product_key(rec):
                continue
            # the soonest-expiring unit of a product must be at least as far forward as the later ones
            if oexp < exp and orec["spos"][1] > rec["spos"][1] + 0.01:
                ok = False
            if oexp > exp and orec["spos"][1] < rec["spos"][1] - 0.01:
                ok = False
        flags[eid] = ok
    return flags


def stack_fifo_ok(state, eid):
    """For stacked units (an item resting on another unit of the same product), older must be on top."""
    rec = state["entities"][eid]
    below = state["entities"].get(rec.get("location"))
    if below is None or not food(below) or product_key(below) != product_key(rec):
        return True
    a, b = expires(rec), expires(below)
    if a is None or b is None:
        return True
    return a <= b


# ----------------------------------------------------------------------
# labels

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_label(text, state=None):
    """Structured reading of a handwritten food label: contents, dates, ambiguity, owner."""
    import re
    out = {"text": text or "", "contents": None, "dates": [], "ambiguous_date": False, "owner": None}
    if not text:
        return out
    lowered = text.lower()
    for match in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", text):
        try:
            out["dates"].append(datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat())
        except ValueError:
            pass
    for match in re.finditer(r"(\d{1,2})\s+([a-z]{3})[a-z]*\.?\s+(\d{4})", lowered):
        mon = MONTHS.get(match.group(2))
        if mon:
            try:
                out["dates"].append(datetime.date(int(match.group(3)), mon, int(match.group(1))).isoformat())
            except ValueError:
                pass
    for match in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text):
        a, b = int(match.group(1)), int(match.group(2))
        if a <= 12 and b <= 12 and a != b:
            out["ambiguous_date"] = True
    m = re.search(r"(?:contents?|food|item)\s*[:=]\s*([a-z _\-]+)", lowered)
    if m:
        out["contents"] = m.group(1).strip().replace(" ", "_").replace("-", "_")
    else:
        first = re.split(r"[;,\n]", lowered)[0].strip()
        first = re.sub(r"\d{4}-\d{2}-\d{2}", "", first).strip()
        out["contents"] = first.replace(" ", "_") or None
    m = re.search(r"(?:for|owner)\s*[:=]?\s*([a-z0-9_]+)", lowered)
    if m:
        out["owner"] = m.group(1)
    return out
