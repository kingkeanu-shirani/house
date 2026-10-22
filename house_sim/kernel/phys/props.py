"""Physical properties of entities (tier-1 physics; docs/design/TIER1_PHYSICS.md sections 4.1-4.9).

Data lives in house_sim/data/physics/ (materials, liquids, floors, supports; laws.json belongs to lawbook) and is
loaded once per process into read-only mappings: MATERIALS, LIQUIDS, FLOORS, SUPPORTS.

props(state, eid) -> Phys resolves every field from, most specific first:

1. the entity record: the type's props copied at creation plus setup and generator overrides, under the field
   names of TIER1_PHYSICS 4.8 and their existing aliases (RECORD_FIELDS: max_stack_load_kg is a crush limit,
   hang_load_kg and hook_rating_kg are hang ratings, rated_kg is a hang rating on hanging hardware and a load
   rating elsewhere, max_load_kg also rates a portable container's handles, heat_rating_c / surface_heat_limit_c
   are heat limits, specific_heat is c_p, opening_width_m is opening_m);
2. the supports.json rows of the type (type, is_a nearest first, head noun of the type name, category, tags; per
   field the most specific row that sets it);
3. the material class, chosen through the materials.json alias map; when the type chain declares no material (the
   record carries BASE_PROPS' default "plastic"), through the row's `material`, then `category_material`;
4. the global default.

Then the `phys.props` hooks refine the result (fn(record, phys_dict) -> dict of field updates or None, with an
optional static filter fn.applies(record) -> bool; a hook that changes `shape` gets `rollable` recomputed from it
unless it sets `rollable` itself, and sets any other derived field -- rigid, break_mode, crushable -- that its change
affects), and the per-instance variation scales the strength thresholds
(vary: drop_tol_m, crush_kg, rating_kg, shelf_rating_kg, hang_kg, handle_kg, shock_k, heat_limit_c) unless the
record sets `phys_exact`. Unbreakable or unlimited thresholds are float("inf"); missing ratings are None.

Calibration rule for the data: an anchor that says an item *survives* a load or a drop holds for the whole +-15 %
band (nominal >= anchor / 0.85: a phone's 1.2 m survives every 1 m drop), and one that says it *fails* holds for the
whole band (nominal x 1.15 below the load: a tumbler's 0.5 m always breaks from 0.9 m); "about" anchors are nominal.

Caching is pure: the per-type part (the class and every field's row and class default) is memoised by the record
fields it depends on, variation factors by (house salt, entity id, type). Nothing here writes state or registers
anything.
"""
import hashlib
import json
import math
import zlib
from collections import namedtuple
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from .. import geometry
from ..plugins import HOOKS

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "physics"
INF = float("inf")
BASE_MATERIAL = "plastic"             # registry BASE_PROPS default: a chain that declares nothing carries it
SHAPES = ("box", "cylinder", "sphere", "flat", "long")
VARY_SPREAD = 0.15                    # per-instance threshold spread, +-15 % (TIER1_PHYSICS 4.7, decided ON)
VARIED = ("drop", "crush", "rating", "hang", "handle", "shock", "heat")   # vary() law names
_MEMO_MAX = 8192                      # per-type memo entries before the memo is cleared (bounded memory)
_VARY_MAX = 65536


def _freeze(value):
    """Read-only view of loaded JSON: dicts become mapping proxies, lists tuples."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _load(name):
    with open(DATA_DIR / name, encoding="utf-8") as handle:
        return _freeze(json.load(handle))


MATERIALS = _load("materials.json")
LIQUIDS = _load("liquids.json")
FLOORS = _load("floors.json")
SUPPORTS = _load("supports.json")

_CLASSES = MATERIALS["classes"]
_ALIASES = MATERIALS["aliases"]
_CATEGORY_MATERIAL = MATERIALS["category_material"]
_FRAGILE = MATERIALS["fragile_default"]
_HEAT_RESISTANT_C = float(MATERIALS["heat_resistant_c"]["value"])
_CRUSHABLE_CLASSES = frozenset(MATERIALS["crushable"]["classes"])
_CRUSHABLE_DEFAULT_KG = float(MATERIALS["crushable"]["default_crush_kg"])
_UPHOLSTERED_CLASSES = frozenset(MATERIALS["upholstered"]["classes"])
_UPHOLSTERED_ABS = float(MATERIALS["upholstered"]["absorb_l_per_kg"])
_ABSORB_VOLUME_CAP = float(MATERIALS["absorb_volume_cap"]["value"])
_SHAPE_VOLUME = {"cylinder": math.pi / 4.0, "sphere": math.pi / 6.0}     # of the bounding box
WATER_KG_M3 = 1000.0                  # fresh water: what floats is lighter than the water it displaces
_ROW_LEVELS = (SUPPORTS["types"], SUPPORTS["nouns"], SUPPORTS["categories"], SUPPORTS["tags"])
_FLOOR_ROWS = FLOORS["floors"]
_FLOOR_ALIASES = FLOORS["aliases"]
_ROW_META = ("source", "doc", "anchor")

Phys = namedtuple("Phys", (
    "cls", "mass_kg", "size_m", "shape", "com_frac",
    "brittle", "break_mode", "rigid", "fragile", "drop_tol_m", "crush_kg", "hard", "wet_strength",
    "mu", "mu_wet", "waterproof", "absorb_l", "softness", "wsens", "combustible",
    "c_p", "heat_limit_c", "melt_c", "shock_k",
    "rating_kg", "shelf_rating_kg", "hang_kg", "handle_kg", "rim_m",
    "mouth_frac", "opening_m", "nest_group", "nest_frac", "compressible",
    "rollable", "floats", "anchored", "secured",
    "base_m", "crushable", "drag_cd", "cushion_m", "cushion_j"), defaults=(None, False, None, None, None))
Phys.__doc__ = """Resolved physical properties of one entity (TIER1_PHYSICS 4.9; variation already applied).

cls material class; mass_kg own mass; size_m resting [w, d, h] (on its side the height swaps with the longest
footprint side); shape box|cylinder|sphere|flat|long; com_frac centre-of-mass height / height.
brittle shatters into sharp pieces; break_mode shatter|chip|crack|bruise|None; rigid; fragile (record flag);
drop_tol_m survivable drop onto hardness 1.0 (inf: unbreakable); crush_kg load on top (inf: unlimited); hard impact
hardness as a landing surface; wet_strength strength factor when soaked.
mu dry friction against finished wood; mu_wet friction when wet (None: generic wet factor); waterproof; absorb_l
litres it can hold; softness 0-1; wsens water sensitivity 0-1; combustible.
c_p J/(kg K); heat_limit_c as a support finish (inf: none); melt_c (inf: none); shock_k thermal shock (inf: none).
rating_kg surface or container load, shelf_rating_kg per shelf, hang_kg, handle_kg (None: unrated); rim_m.
mouth_frac mouth / inner width (1.0 open top); opening_m declared opening (number: round mouth diameter, (w, d):
rectangle, None); nest_group / nest_frac nesting; compressible textile volume.
rollable sphere or cylinder on its side; floats it stays afloat in fresh water: a sealed hollow item (a closed
waterproof container with its liquid, a ball, an inflatable) when its mass is less than the water its outline displaces,
anything else when its material is lighter than water once its pores have flooded (wood, cork, wax, polyethylene and
polypropylene, foams: materials.json "floats"; a knife, a hammer or a towel sinks); anchored (anchored or mounted);
secured.
base_m width of the foot an upright item stands on when it is narrower than its bounding box (a wine glass's foot
under its bowl; None: the footprint); crushable whether a load on top can crush it at all (it declares a crush or
load limit, is fragile or crushable, a supports.json row gives it a crush_kg, or its class crushes by nature:
cardboard, cartons, eggshell); drag_cd its wind drag coefficient (None: the environment's bluff-body default);
cushion_m / cushion_j a soft landing's cushion (beds, sofas, cushions: the depth that stops a falling item and the
energy it takes before it bottoms out; None: none, impact.effective_drop_m)."""
_FI = {name: i for i, name in enumerate(Phys._fields)}
_VARIED_FIELDS = tuple((_FI[field], VARIED.index(law)) for field, law in (
    ("drop_tol_m", "drop"), ("crush_kg", "crush"), ("rating_kg", "rating"), ("shelf_rating_kg", "rating"),
    ("hang_kg", "hang"), ("handle_kg", "handle"), ("shock_k", "shock"), ("heat_limit_c", "heat")))

# record fields that set a numeric Phys field, highest priority first (the absorption entry is per kg of dry mass)
_NUMERIC = tuple((_FI[field], keys) for field, keys in (
    ("drop_tol_m", ("drop_tolerance_m",)), ("crush_kg", ("crush_kg", "max_stack_load_kg")),
    ("mu", ("friction_mu",)), ("absorb_l", ("absorb_l_per_kg",)), ("softness", ("softness",)),
    ("c_p", ("specific_heat",)), ("heat_limit_c", ("heat_limit_c", "surface_heat_limit_c", "heat_rating_c")),
    ("melt_c", ("melt_c",)), ("shock_k", ("thermal_shock_c",)),
    ("rating_kg", ("max_load_kg", "load_rating_kg", "load_capacity_kg")), ("shelf_rating_kg", ("shelf_load_kg",)),
    ("hang_kg", ("hook_rating_kg", "hang_load_kg")), ("handle_kg", ("handle_rating_kg",)), ("rim_m", ("rim_m",)),
    ("mouth_frac", ("mouth_frac",)), ("nest_frac", ("nest_frac",)), ("com_frac", ("com_frac",)),
    ("base_m", ("base_m",)), ("drag_cd", ("drag_cd",)), ("cushion_m", ("cushion_m",)), ("cushion_j", ("cushion_j",))))
_FLAGS = tuple((_FI[field], field) for field in ("brittle", "waterproof", "combustible", "compressible", "rigid"))
_CRUSH_KEYS = ("crush_kg", "max_stack_load_kg")
RECORD_FIELDS = frozenset(key for _, keys in _NUMERIC for key in keys) | frozenset(
    field for _, field in _FLAGS) | frozenset(("soft", "rated_kg", "heat_resistant", "shape", "nest_group",
                                               "opening_m", "opening_width_m", "crushable", "floats", "airtight"))

# cls, the per-field defaults, and the flotation inputs: row_floats (a row's explicit floats, else None), airtight (a
# row says sealed hollow: an inflatable), ball (balls are sealed hollow or solid: their bulk density decides either
# way) and rho (the class's density, for the volume of a container's own walls)
_TypePart = namedtuple("_TypePart", "cls defaults row_floats airtight ball rho")
_TYPE_MEMO = {}
_DECLARES = {}
_SALTS = {}
_VARY = {}


# ----------------------------------------------------------------------------------------------------------------
# small value helpers

def _num(value, default=None):
    """A finite float, else default (booleans are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _limit(value):
    """A data threshold: None (no limit) becomes inf."""
    return INF if value is None else float(value)


def _dims(size):
    """[w, d, h] as positive floats; a missing or invalid entry is 0.1 m (BASE_PROPS)."""
    out = [0.1, 0.1, 0.1]
    if isinstance(size, (list, tuple)):
        for i, value in enumerate(size[:3]):
            value = _num(value)
            if value is not None and value > 0:
                out[i] = value
    return out


def _first(record, keys):
    """The first numeric record value among keys, else None."""
    for key in keys:
        value = _num(record.get(key))
        if value is not None:
            return value
    return None


def _opening(value):
    """A valid opening: a positive number (round mouth diameter) or a (w, d) pair of positive numbers, else None."""
    number = _num(value)
    if number is not None:
        return number if number > 0 else None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        pair = (_num(value[0]), _num(value[1]))
        if pair[0] is not None and pair[1] is not None and pair[0] > 0 and pair[1] > 0:
            return pair
    return None


# ----------------------------------------------------------------------------------------------------------------
# per-type part: rows, class and the defaults of every field

def _alias(material):
    """(class or None, alias row or None) for a material string; (None, None) when the string is unmapped."""
    if not isinstance(material, str):
        return None, None
    entry = _ALIASES.get(material.strip().lower())
    if entry is None:
        return None, None
    if isinstance(entry, str):
        return entry, None
    return entry.get("class"), entry


def _declares_material(type_name, is_a):
    """True when the type or an ancestor sets `material` in the registry's type files (memoised per chain)."""
    key = (type_name,) + tuple(is_a)
    if key not in _DECLARES:
        from ..registry import default_registry
        types = default_registry().types
        _DECLARES[key] = any("material" in ((types.get(name) or {}).get("props") or {}) for name in key)
    return _DECLARES[key]


def _head_noun(type_name):
    return type_name.rsplit("_", 1)[-1] if isinstance(type_name, str) else None


def _rows(type_name, is_a, category, tags):
    """supports.json rows for a type, most specific first."""
    types, nouns, categories, tag_rows = _ROW_LEVELS
    found = [types.get(name) for name in (type_name,) + tuple(is_a) if isinstance(name, str)]
    found.append(nouns.get(_head_noun(type_name)))
    found.append(categories.get(category) if isinstance(category, str) else None)
    found.extend(tag_rows.get(tag) for tag in tags if isinstance(tag, str))
    return [row for row in found if row]


def _class_of(type_name, is_a, category, material, row):
    """(class, alias overrides) per TIER1_PHYSICS 4.2 step 3."""
    cls, overrides = _alias(material)
    if cls is not None and not (material == BASE_MATERIAL and not _declares_material(type_name, is_a)):
        return cls, overrides
    if row.get("material") is not None:
        cls, overrides = _alias(row["material"])
        if cls is not None:
            return cls, overrides
    for key in (type_name,) + tuple(is_a) + (category,):
        hit = _CATEGORY_MATERIAL.get(key) if isinstance(key, str) else None
        if hit:
            return hit, None
    return BASE_MATERIAL, None


def _defaults(cls, cols, row):
    """Every Phys field resolved from the rows and the class alone (record-dependent fields are placeholders; the
    absorption entry is per kg; hang_kg may be a {"per_m": kg} row)."""
    def pick(key, column, default=None):
        value = _num(row.get(key))
        if value is not None:
            return value
        value = _num(cols.get(column)) if column else None
        return default if value is None else value

    def flag(key, column):
        value = row.get(key)
        return value if isinstance(value, bool) else bool(cols.get(column))

    brittle = flag("brittle", "brittle")
    mode = cols["break_mode"]
    hang = row.get("hang_kg")
    shape = row.get("shape")
    out = [None] * len(Phys._fields)
    out[_FI["cls"]] = cls
    out[_FI["shape"]] = shape if shape in SHAPES else "box"
    out[_FI["com_frac"]] = pick("com_frac", None, 0.5)
    out[_FI["brittle"]] = brittle
    out[_FI["break_mode"]] = "shatter" if brittle else ("crack" if mode == "shatter" else mode)
    out[_FI["drop_tol_m"]] = _limit(pick("drop_tolerance_m", "drop_tol_m"))
    out[_FI["crush_kg"]] = _limit(pick("crush_kg", "crush_kg"))
    out[_FI["hard"]] = pick("hard", "hard", 0.0)
    out[_FI["wet_strength"]] = pick("wet_strength", "wet_strength", 1.0)
    out[_FI["mu"]] = pick("friction_mu", "mu", 0.5)
    out[_FI["mu_wet"]] = _num(cols.get("mu_wet"))
    out[_FI["waterproof"]] = flag("waterproof", "wp")
    out[_FI["absorb_l"]] = pick("absorb_l_per_kg", "abs", 0.0)
    out[_FI["softness"]] = pick("softness", "soft", 0.0)
    out[_FI["wsens"]] = pick("wsens", "wsens", 0.0)
    out[_FI["combustible"]] = flag("combustible", "comb")
    out[_FI["c_p"]] = pick("specific_heat", "c_p", 0.0)
    out[_FI["heat_limit_c"]] = _limit(pick("heat_limit_c", "heat"))
    out[_FI["melt_c"]] = _limit(pick("melt_c", "melt"))
    out[_FI["shock_k"]] = _limit(pick("thermal_shock_c", "shock"))
    out[_FI["rating_kg"]] = pick("rating_kg", None)
    out[_FI["shelf_rating_kg"]] = pick("shelf_rating_kg", None)
    scaled = isinstance(hang, Mapping) and any(_num(hang.get(key)) is not None for key in ("per_m", "per_slot"))
    out[_FI["hang_kg"]] = hang if scaled else _num(hang)
    out[_FI["handle_kg"]] = pick("handle_kg", None)
    out[_FI["rim_m"]] = pick("rim_m", None, 0.0)
    out[_FI["mouth_frac"]] = pick("mouth_frac", None, 1.0)
    out[_FI["opening_m"]] = _opening(row.get("opening_m"))
    out[_FI["nest_group"]] = row.get("nest_group")
    out[_FI["nest_frac"]] = pick("nest_frac", None, 1.0)
    out[_FI["compressible"]] = flag("compressible", "compressible")
    out[_FI["rigid"]] = row.get("rigid") if isinstance(row.get("rigid"), bool) else None
    base = _num(row.get("base_m"))
    out[_FI["base_m"]] = base if base is not None and base > 0 else None
    out[_FI["crushable"]] = _num(row.get("crush_kg")) is not None or cls in _CRUSHABLE_CLASSES
    out[_FI["drag_cd"]] = _num(row.get("drag_cd"))
    out[_FI["cushion_m"]] = _num(row.get("cushion_m"))
    out[_FI["cushion_j"]] = _num(row.get("cushion_j"))
    out[_FI["floats"]] = bool(cols.get("floats"))          # the material's own: lighter than water once flooded
    return tuple(out)


def _build_part(type_name, is_a, category, tags, material, tempered):
    row = {}
    for found in _rows(type_name, is_a, category, tags):
        for key, value in found.items():
            if key not in _ROW_META and key not in row:
                row[key] = value
    cls, overrides = _class_of(type_name, is_a, category, material, row)
    if cls == "glass" and (tempered or row.get("tempered") is True):
        cls, overrides = "tempered_glass", None
    cols = dict(_CLASSES[cls])
    if overrides:
        cols.update((key, value) for key, value in overrides.items() if key not in ("class",) + _ROW_META)
    furniture = category == "furniture" or "furniture" in is_a
    if furniture and cls in _UPHOLSTERED_CLASSES and _num(row.get("absorb_l_per_kg")) is None:
        row["absorb_l_per_kg"] = _UPHOLSTERED_ABS        # upholstered: only cushions and cover soak (materials.json)
    floats = row.get("floats")
    ball = category == "ball" or "ball" in is_a
    return _TypePart(cls, _defaults(cls, cols, row), floats if isinstance(floats, bool) else None,
                     row.get("airtight") is True, ball, _num(cols.get("rho")) or WATER_KG_M3)


def _type_part(record):
    material, category, tags = record.get("material"), record.get("category"), record.get("tags")
    key = (record.get("type"), tuple(record.get("is_a") or ()), category if isinstance(category, str) else None,
           tuple(tags) if isinstance(tags, (list, tuple)) else (), material if isinstance(material, str) else None,
           record.get("tempered") is True)
    part = _TYPE_MEMO.get(key)
    if part is None:
        if len(_TYPE_MEMO) >= _MEMO_MAX:
            _TYPE_MEMO.clear()
        part = _TYPE_MEMO[key] = _build_part(*key)
    return part


def material_class(state_or_none, record):
    """Material class of a record (or of an entity id when a state is given), e.g. "glass", "cotton"."""
    if isinstance(record, str):
        record = state_or_none["entities"][record]
    return _type_part(record).cls


def class_known(material):
    """True when a material string maps to a class or to the category fallback (False: unmapped, an audit gap)."""
    return isinstance(material, str) and material.strip().lower() in _ALIASES


# ----------------------------------------------------------------------------------------------------------------
# variation (TIER1_PHYSICS 4.7)

def _salt(state):
    """crc32 of the sorted (room id, width, depth) triples. Memoised per rooms dict (held, so its id is never reused)
    and its room count: rooms and their sizes are fixed for an episode (no action or system resizes a room)."""
    rooms = state["rooms"]
    entry = _SALTS.get(id(rooms))
    if entry is not None and entry[0] is rooms and entry[1] == len(rooms):
        return entry[2]
    parts = []
    for rid in sorted(rooms):
        size = rooms[rid].get("size_m") or [4.0, 4.0]
        parts.append("{}:{!r}:{!r}".format(rid, float(size[0]), float(size[1])))
    salt = zlib.crc32(";".join(parts).encode("utf-8"))
    if len(_SALTS) >= 32:
        _SALTS.pop(next(iter(_SALTS)))
    _SALTS[id(rooms)] = (rooms, len(rooms), salt)
    return salt


def _unit(key):
    """A uniform number in [0, 1) from a key string: the first 32 bits of its BLAKE2b digest. A non-linear hash, so
    sibling ids (mug_1, mug_2) and the laws of one entity get independent factors: crc32 is linear over GF(2), which
    made keys of equal length differ by a constant XOR mask whatever the house (correlations near -0.98)."""
    return int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()[:4], "big") / 4294967296.0


def _factors(state, eid, type_name):
    """The variation factors of an entity, in VARIED order."""
    salt = _salt(state)
    key = (salt, eid, type_name)
    factors = _VARY.get(key)
    if factors is None:
        if len(_VARY) >= _VARY_MAX:
            _VARY.clear()
        prefix = "{}|{}|{}|".format(salt, eid, type_name)
        factors = _VARY[key] = tuple(1.0 + VARY_SPREAD * (2.0 * _unit(prefix + law) - 1.0) for law in VARIED)
    return factors


def vary(state, eid, law):
    """Deterministic per-instance factor in [0.85, 1.15) for a threshold law (one of VARIED): 1 + 0.15 (2u - 1) with
    u = blake2b(salt|eid|type|law) / 2**32 (first 32 bits) and salt the crc32 of the house's sorted room ids and sizes.
    1.0 when the entity sets phys_exact. A pure function of state: replays and other processes get the same factor."""
    record = state["entities"][eid]
    if record.get("phys_exact"):
        return 1.0
    try:
        index = VARIED.index(law)
    except ValueError:
        raise ValueError("vary law must be one of {}: {!r}".format(", ".join(VARIED), law))
    return _factors(state, eid, record.get("type"))[index]


# ----------------------------------------------------------------------------------------------------------------
# props

def _hanging(record):
    """A hook, rail, rod or other hanging support (rated_kg and max_load_kg are then hang ratings)."""
    return ("hanging_hardware" in (record.get("is_a") or ()) or record.get("type") == "hanging_hardware"
            or record.get("category") == "hooks" or (_num(record.get("hang_slots")) or 0.0) > 0)


def _watertight(record):
    """A container closed watertight: sealed, its lid on or its cap screwed on (a closed door, hinged lid or flap,
    liquids.container_closed's openable-and-shut, keeps things in but lets water in)."""
    if record.get("sealed") or record.get("lid_on") or record.get("capped"):
        return True
    cap = record.get("cap")
    return isinstance(cap, Mapping) and bool(cap.get("on")) and not cap.get("loose")


def _floats(record, part, values, mass, volume_m3):
    """Phys.floats: an explicit record or row value; a sealed hollow waterproof item floats when it is lighter than the
    water it displaces: a closed container its capacity plus its walls (mass / class density; a bottle's outline would
    count its neck as full width), with its liquid; a ball or an inflatable its outline. Anything else floats when its
    material does (materials.json "floats": an open bowl or jar floods, a knife, a hammer or a towel sinks)."""
    explicit = record.get("floats")
    if isinstance(explicit, bool):
        return explicit
    if part.row_floats is not None:
        return part.row_floats
    if mass <= 0:
        return False
    capacity = _num(record.get("capacity_l")) or 0.0
    container = capacity > 0 or record.get("container") is True
    hollow = part.airtight or record.get("airtight") is True or record.get("inflatable") is True or part.ball
    if values[_FI["waterproof"]] and (hollow or (container and _watertight(record))):
        liters = _num(record.get("liquid_l")) or 0.0
        load = 0.0
        if liters > 0:
            load = liters * (_num(liquid_props(record.get("liquid") or "water").get("rho")) or WATER_KG_M3) / 1000.0
        displaced = volume_m3
        if container and capacity > 0 and not hollow:
            displaced = min(volume_m3, capacity / 1000.0 + mass / part.rho)
        return mass + load < WATER_KG_M3 * displaced
    return bool(values[_FI["floats"]])


def _overlay(record, values, present):
    """Apply the record's own physics fields (present: the RECORD_FIELDS it carries) to values in place."""
    explicit = set()
    for index, keys in _NUMERIC:
        if not present.isdisjoint(keys):
            value = _first(record, keys)
            if value is not None:
                values[index] = value
                explicit.add(index)
    for index, key in _FLAGS:
        if key in present and isinstance(record[key], bool):
            values[index] = record[key]
            if index == _FI["brittle"]:
                mode = values[_FI["break_mode"]]
                values[_FI["break_mode"]] = "shatter" if record[key] else ("crack" if mode == "shatter" else mode)
    if "soft" in present and _FI["softness"] not in explicit and isinstance(record["soft"], bool):
        values[_FI["softness"]] = 0.8 if record["soft"] else 0.1
    if record.get("heat_resistant") is True and _FI["heat_limit_c"] not in explicit:
        values[_FI["heat_limit_c"]] = max(values[_FI["heat_limit_c"]], _HEAT_RESISTANT_C)
    if "rated_kg" in present or "max_load_kg" in present:
        hanging = _hanging(record)
        rated = _first(record, ("rated_kg",))
        if rated is not None:
            field = _FI["hang_kg"] if hanging else _FI["rating_kg"]
            if field not in explicit:
                values[field] = rated
                explicit.add(field)
        max_load = _first(record, ("max_load_kg",))
        if max_load is not None and hanging and _FI["hang_kg"] not in explicit:
            values[_FI["hang_kg"]] = max_load
        if max_load is not None and record.get("portable") and _FI["handle_kg"] not in explicit:
            values[_FI["handle_kg"]] = max_load
        if max_load is not None and record.get("portable") and _FI["crush_kg"] not in explicit:
            values[_FI["crush_kg"]] = max_load      # manipulation's max_load: what a box or container bears on top
    if "shape" in present and record["shape"] in SHAPES:
        values[_FI["shape"]] = record["shape"]
    if record.get("nest_group"):
        values[_FI["nest_group"]] = record["nest_group"]
    for key in ("opening_m", "opening_width_m"):
        opening = _opening(record.get(key))
        if opening is not None:
            values[_FI["opening_m"]] = opening
            break


def _apply_hooks(record, values, hooks):
    data = dict(zip(Phys._fields, values))
    for _, name, fn in hooks:
        applies = getattr(fn, "applies", None)
        if applies is not None and not applies(record):
            continue
        update = fn(record, dict(data))
        if update:
            unknown = sorted(set(update) - set(_FI))
            if unknown:
                raise ValueError("phys.props hook {} returned unknown fields {}".format(name, unknown))
            if "shape" in update and "rollable" not in update:
                # rollable follows the shape the hook gave (food's adoption request 7: produce turned into a sphere
                # by its ripeness hook rolled only when the hook also set rollable); the other derived fields (rigid,
                # break_mode, crushable) are the hook's to set with the inputs it changes
                update = dict(update, rollable=update["shape"] == "sphere" or
                              (update["shape"] == "cylinder" and record.get("orientation") == "on_side"))
            data.update(update)
    return [data[field] for field in Phys._fields]


def props_of(record, state=None, eid=None):
    """Phys for an entity record. With state and eid (an entity of that state) the per-instance variation applies;
    without them (a record not yet in a state, e.g. a filler being planned) the thresholds are nominal."""
    part = _type_part(record)
    values = list(part.defaults)
    get = record.get
    present = RECORD_FIELDS.intersection(record)
    if present:
        _overlay(record, values, present)
    mass = _num(get("mass_kg"), 0.0)
    mass = mass if mass > 0 else 0.0
    w, d, h = _dims(get("size_m"))
    orientation = get("orientation")
    if orientation == "on_side":
        w, d, h = (h, d, w) if w >= d else (w, h, d)
    fragile = bool(get("fragile")) or "fragile" in (get("hazards") or ())
    if fragile:
        if values[_FI["drop_tol_m"]] == INF:
            values[_FI["drop_tol_m"]] = float(_FRAGILE["drop_tol_m"])
        if values[_FI["crush_kg"]] == INF:
            values[_FI["crush_kg"]] = float(_FRAGILE["crush_kg"])
    # crushable (manipulation's max_load rule): an explicit crush or stack limit, a portable box's max_load_kg, the
    # fragile or crushable flags, a row's crush_kg or a class that crushes by nature; crushable: false opts out
    declared = bool(present) and _first(record, _CRUSH_KEYS) is not None
    flag = get("crushable")
    if declared or fragile or flag is True or (get("portable") and _num(get("max_load_kg")) is not None):
        values[_FI["crushable"]] = True
    elif flag is False:
        values[_FI["crushable"]] = False
    if values[_FI["crushable"]] and values[_FI["crush_kg"]] == INF:
        values[_FI["crush_kg"]] = _CRUSHABLE_DEFAULT_KG
    if values[_FI["break_mode"]] is None and values[_FI["drop_tol_m"]] != INF:
        values[_FI["break_mode"]] = _FRAGILE["break_mode"]
    softness = min(1.0, max(0.0, values[_FI["softness"]]))
    hang = values[_FI["hang_kg"]]
    if isinstance(hang, Mapping):
        if _num(hang.get("per_m")) is not None:
            values[_FI["hang_kg"]] = float(hang["per_m"]) * max(w, d)   # a rod's rating grows with its length
        else:                                                           # a rail's with its hooks
            values[_FI["hang_kg"]] = float(hang["per_slot"]) * max(1, int(_num(get("hang_slots")) or 1))
    shape = values[_FI["shape"]]
    volume_m3 = w * d * h * _SHAPE_VOLUME.get(shape, 1.0)
    values[_FI["mass_kg"]] = mass
    values[_FI["size_m"]] = [w, d, h]
    values[_FI["fragile"]] = fragile
    values[_FI["softness"]] = softness
    # litres held: per kg of dry mass, at most the pores of its outline (materials.json absorb_volume_cap)
    values[_FI["absorb_l"]] = min(values[_FI["absorb_l"]] * mass, _ABSORB_VOLUME_CAP * volume_m3 * 1000.0)
    if values[_FI["rigid"]] is None:
        values[_FI["rigid"]] = not get("soft_sided") and softness < 0.3
    values[_FI["rollable"]] = shape == "sphere" or (shape == "cylinder" and orientation == "on_side")
    values[_FI["floats"]] = _floats(record, part, values, mass, volume_m3)
    values[_FI["anchored"]] = bool(get("anchored")) or bool(get("mounted"))
    values[_FI["secured"]] = bool(get("secured"))
    hooks = HOOKS.get("phys.props")
    if hooks:
        values = _apply_hooks(record, values, hooks)
    if state is not None and eid is not None and not get("phys_exact"):
        factors = _factors(state, eid, get("type"))
        for index, law in _VARIED_FIELDS:
            value = values[index]
            if value is not None and value != INF:
                values[index] = value * factors[law]
    return Phys._make(values)


def props(state, eid):
    """Phys of an entity of a state, per-instance variation applied (TIER1_PHYSICS 4.2)."""
    return props_of(state["entities"][eid], state, eid)


# ----------------------------------------------------------------------------------------------------------------
# liquids and floors

def liquid_props(name):
    """Columns of a liquid (liquids.json), aliases resolved, unknown names get the default row; a fresh dict with
    "name" set to the canonical row name ("default" when unknown). rho is kg/m3."""
    rows, aliases = LIQUIDS["liquids"], LIQUIDS["aliases"]
    key = name.strip().lower() if isinstance(name, str) else None
    extra = None
    if key not in rows:
        alias = aliases.get(key)
        if isinstance(alias, str):
            key = alias
        elif alias is not None:
            key, extra = alias["liquid"], alias
    out = dict(LIQUIDS["default"])
    if key in rows:
        out.update(rows[key])
    if extra:
        out.update((k, v) for k, v in extra.items() if k != "liquid")
    for meta in _ROW_META:
        out.pop(meta, None)
    out["name"] = key if key in rows else "default"
    return out


def floor_kind(material):
    """floors.json row name for a floor material string (aliases resolved; unknown or missing -> "default")."""
    key = material.strip().lower() if isinstance(material, str) else None
    if key in _FLOOR_ROWS:
        return key
    return _FLOOR_ALIASES.get(key, "default")


def _is_rug(record):
    return record.get("type") == "rug" or "rug" in (record.get("is_a") or ()) or record.get("category") == "rug"


def rug_at(state, rid, cell):
    """Id of a rug lying on the floor of room rid that covers cell (the smallest id if several), else None."""
    cell = (int(cell[0]), int(cell[1]))
    found = [eid for eid, record in state["entities"].items()
             if record.get("location") == rid and record.get("pos") and _is_rug(record)
             and record.get("relation", "on") == "on" and cell in geometry.footprint(record)]
    return min(found) if found else None


def floor_props(state, rid, cell=None):
    """The floors.json row of room rid's floor (a fresh dict) plus "material" (the row name) and "rug" (the rug id
    covering cell, or None). The floor material is the floor_<rid> entity's, else the room's floor_material, else
    its terrain. With a cell, a rug on a hard floor turns the row into "rug"."""
    room = state["rooms"][rid]
    floor = state["entities"].get("floor_" + rid) or {}
    name = floor_kind(floor.get("material") or room.get("floor_material") or room.get("terrain"))
    rug = None
    if cell is not None and not _FLOOR_ROWS[name]["soft"]:
        rug = rug_at(state, rid, cell)
        if rug is not None:
            name = "rug"
    out = dict(_FLOOR_ROWS[name])
    out.pop("source", None)
    out["material"], out["rug"] = name, rug
    return out
