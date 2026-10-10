"""Shared constants and helpers for the pets domain.

Species data use real magnitudes (cited where not obvious):
* Maintenance energy ~ 1.6 x RER for dogs, 1.2 x RER for neutered indoor cats, RER = 70 * kg**0.75 kcal/day.
* Water need ~ 55 ml/kg/day for dogs, ~45 ml/kg/day for cats, rising ~4 %/C above 24 C.
* Reach envelope = how high a pet gets with its mouth/paws standing on hind legs (dogs) or jumping (cats);
  a dog can sweep a surface whose height is <= reach - 0.3 m ("counter-surfing").
* Kibble bulk density 0.42-0.45 g/ml (a level 237 ml cup holds ~100-105 g).

Everything here is deterministic and only reads state, except the small mutation helpers
(move_pet, log, harm) which go through the journaled World API.
"""
import math
import weakref

from ...kernel import clock, geometry
from ...kernel.errors import ActionError, require
from ...kernel.predicates import root_room

SECTION = "pet_care"


# ----------------------------------------------------------------------
# static indexes (kernel v2.1). Helpers that only get `state` find the live World through a weak
# registry filled by the systems/hooks (bind_world); without one they fall back to a full scan.

_WORLDS = {}


def bind_world(w):
    key = id(w.s)
    ref = _WORLDS.get(key)
    if ref is not None and ref() is w:
        return
    if len(_WORLDS) > 64:
        for k in [k for k, r in _WORLDS.items() if r() is None or r().s is None]:
            del _WORLDS[k]
    _WORLDS[key] = weakref.ref(w)


def world_of(state):
    ref = _WORLDS.get(id(state))
    w = ref() if ref is not None else None
    return w if w is not None and w.s is state else None


def sindex(state, name, predicate):
    """Sorted ids of entities matching a predicate over STATIC fields (cached by the World until an entity is
    created or destroyed). Callers re-check dynamic fields; ids are guaranteed to exist."""
    w = world_of(state)
    entities = state["entities"]
    if w is not None:
        return [e for e in w.static_index("pets:" + name, predicate) if e in entities]
    return [e for e in sorted(entities) if predicate(entities[e])]


def _is_pet_door(rec):
    return rec.get("category") == "pet_door" or (rec.get("mech") or {}).get("kind") == "pet_door"


def _is_window(rec):
    return rec.get("category") == "window" or rec.get("type") == "window" or "window" in (rec.get("is_a") or [])


def pet_doors(state):
    return sindex(state, "pet_doors", _is_pet_door)


def windows(state):
    return sindex(state, "windows", _is_window)


def litter_boxes(state):
    return sindex(state, "litter_boxes", lambda r: r.get("category") == "litter_box")


def located_in(state, ids, parent):
    """Subset of ids whose location is parent (cheap replacement for w.children on a static candidate list)."""
    entities = state["entities"]
    return [e for e in ids if entities[e].get("location") == parent]


def damage_with(rec, kind, severity):
    """CONVENTIONS 5: `damage` is a dict {kind: severity 0..1}; merge (keep the worse severity), converting legacy values."""
    old = (rec or {}).get("damage")
    if isinstance(old, dict):
        out = dict(old)
    elif isinstance(old, (list, tuple)):
        out = {str(k): 1.0 for k in old}
    elif isinstance(old, str) and old:
        out = {old: 1.0}
    else:
        out = {}
    out[kind] = round(max(num(out.get(kind)), max(0.0, min(1.0, num(severity)))), 3)
    return {k: out[k] for k in sorted(out)}


def add_damage(w, eid, kind, severity):
    m = w.mut(eid)
    m["damage"] = damage_with(m, kind, severity)
    return m


def carried_by(state, pid):
    """Entities located on a pet (held in the mouth, worn gear): scans only portable things, cached statically."""
    ents = state["entities"]
    return [e for e in sindex(state, "portable", lambda r: bool(r.get("portable") or r.get("pet_gear"))) if ents[e].get("location") == pid]


def is_bin(rec):
    """A waste bin/cart/can (any domain): a receptacle that accepts thrown-away items, as opposed to a bag one holds
    or an item that merely says which stream it belongs to (a spent HVAC filter carries waste_stream "trash")."""
    if not rec:
        return False
    if rec.get("toilet") or rec.get("category") == "toilet":
        return True
    try:
        from ..core import is_waste_container
        if is_waste_container(rec):
            return True
    except ImportError:          # pragma: no cover - core is always loaded first
        pass
    if rec.get("category") in ("trash", "trash_can", "waste_container", "compost_bin", "recycling_bin") or \
            "waste_container" in (rec.get("is_a") or []):
        return True
    return bool(rec.get("waste_stream")) and not rec.get("portable") and bool(rec.get("accepts_items") or rec.get("container"))


# ----------------------------------------------------------------------
# odor (CONVENTIONS 5: a dict {kind: intensity}; legacy floats written by older code are read as one unnamed kind).
# Writers go through odor_with(), which always writes the dict form (cleaning, textiles and safety read dicts); a
# legacy float found on a record is folded into the named kind on the next write.

DICT_ODOR = True          # the only form written (kept as a marker other domains/tests can read)

def odor_level(rec, kind=None):
    value = (rec or {}).get("odor")
    if isinstance(value, dict):
        if kind is not None:
            return num(value.get(kind))
        return max([num(v) for v in value.values()] or [0.0])
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def odor_with(rec, kind, value):
    """New odor dict with `kind` set to value (other kinds kept; legacy float folded into `kind`)."""
    old = (rec or {}).get("odor")
    value = round(max(0.0, min(1.0, num(value))), 3)
    out = dict(old) if isinstance(old, dict) else {}
    if value > 1e-4:
        out[kind] = value
    else:
        out.pop(kind, None)
    return {k: out[k] for k in sorted(out)}

DOG_SIZES = {
    #          kg    reach  neck chest eat g/s bladder_h shoulder
    "toy": (3.0, 0.40, 22, 38, 0.4, 5.0, 0.25),
    "small": (8.0, 0.60, 30, 50, 0.8, 6.0, 0.32),
    "medium": (20.0, 1.05, 40, 68, 1.6, 8.0, 0.50),
    "large": (34.0, 1.45, 50, 85, 2.5, 9.0, 0.65),
}
CAGED = ("bird", "hamster", "guinea_pig", "rabbit", "reptile", "fish", "chicken")
SMALL_ANIMALS = ("hamster", "guinea_pig", "rabbit")

SPECIES = {
    "dog": {"water_ml_kg_day": 55.0, "meal_interval_h": 11.0, "indoor_only": False, "fetch": True, "reach_in_m": 0.45},
    "cat": {"weight_kg": 4.5, "reach_m": 1.95, "neck_cm": 22, "chest_cm": 34, "eat_g_s": 0.12, "bladder_h": 8.0,
            "water_ml_kg_day": 45.0, "meal_interval_h": 11.0, "indoor_only": True, "fetch": False, "reach_in_m": 2.0,
            "jumps_gates": True},
    "bird": {"weight_kg": 0.09, "reach_m": 0.0, "water_ml_kg_day": 150.0, "indoor_only": True, "seed_g_day": 12.0},
    "hamster": {"weight_kg": 0.12, "reach_m": 0.0, "water_ml_kg_day": 100.0, "indoor_only": True},
    "guinea_pig": {"weight_kg": 1.0, "reach_m": 0.0, "water_ml_kg_day": 100.0, "indoor_only": True},
    "rabbit": {"weight_kg": 2.0, "reach_m": 0.0, "water_ml_kg_day": 100.0, "indoor_only": True},
    "reptile": {"weight_kg": 0.45, "reach_m": 0.0, "water_ml_kg_day": 10.0, "indoor_only": True},
}

ESCAPE_ROOM_TYPES = {"front_yard", "driveway", "sidewalk", "porch", "building_corridor", "lobby", "elevator", "mail_room", "street"}
FENCED_ROOM_TYPES = {"backyard", "garden", "patio", "deck", "poolside", "balcony"}
ENTRY_ROOM_TYPES = {"entryway", "mudroom"}

# pet-toxic substances: severity per species, onset (s) and the signs that appear after onset
TOXINS = {
    "chocolate": {"dog": "severe", "cat": "moderate", "onset_s": 7200, "signs": ["vomiting", "restlessness", "rapid_breathing"]},
    "grapes": {"dog": "severe", "cat": "moderate", "onset_s": 6 * 3600, "signs": ["vomiting", "lethargy"]},
    "raisins": {"dog": "severe", "cat": "moderate", "onset_s": 6 * 3600, "signs": ["vomiting", "lethargy"]},
    "xylitol": {"dog": "severe", "onset_s": 1800, "signs": ["weakness", "staggering", "collapse"]},
    "onion": {"dog": "moderate", "cat": "severe", "onset_s": 24 * 3600, "signs": ["lethargy", "pale_gums"]},
    "garlic": {"dog": "moderate", "cat": "severe", "onset_s": 24 * 3600, "signs": ["lethargy", "pale_gums"]},
    "macadamia": {"dog": "moderate", "onset_s": 12 * 3600, "signs": ["weakness", "tremors"]},
    "caffeine": {"dog": "moderate", "cat": "moderate", "onset_s": 3600, "signs": ["restlessness", "rapid_breathing"]},
    "alcohol": {"dog": "severe", "cat": "severe", "onset_s": 1800, "signs": ["staggering", "lethargy"]},
    "avocado": {"bird": "severe", "onset_s": 3600, "signs": ["labored_breathing"]},
    "lily": {"cat": "severe", "onset_s": 2 * 3600, "signs": ["vomiting", "drooling", "lethargy"]},
    "sago_palm": {"dog": "severe", "cat": "severe", "onset_s": 3600, "signs": ["vomiting", "drooling"]},
    "nsaid_human": {"dog": "severe", "cat": "severe", "onset_s": 2 * 3600, "signs": ["vomiting", "black_stool"]},
    "acetaminophen": {"dog": "severe", "cat": "severe", "onset_s": 2 * 3600, "signs": ["labored_breathing", "swollen_face"]},
    "nsaid_vet": {"cat": "severe", "onset_s": 3 * 3600, "signs": ["vomiting"]},
    "antifreeze": {"dog": "severe", "cat": "severe", "onset_s": 1800, "signs": ["staggering", "vomiting"]},
    "rodenticide": {"dog": "severe", "cat": "severe", "onset_s": 24 * 3600, "signs": ["lethargy", "bleeding_gums"]},
    "cleaning_chemical": {"dog": "moderate", "cat": "moderate", "bird": "severe", "onset_s": 900, "signs": ["drooling", "vomiting"]},
    "detergent_pod": {"dog": "severe", "cat": "severe", "onset_s": 900, "signs": ["drooling", "vomiting", "labored_breathing"]},
    "cooked_bone": {"dog": "moderate", "onset_s": 4 * 3600, "signs": ["vomiting", "lethargy"]},
    "string": {"cat": "severe", "onset_s": 12 * 3600, "signs": ["vomiting", "not_eating"]},
    "sock": {"dog": "moderate", "onset_s": 6 * 3600, "signs": ["retching", "vomiting"]},
    "small_part": {"dog": "moderate", "cat": "moderate", "onset_s": 4 * 3600, "signs": ["retching"]},
    "live_cord": {"dog": "severe", "cat": "severe", "onset_s": 0, "signs": ["mouth_burn", "collapse"]},
    "essential_oil": {"cat": "severe", "dog": "moderate", "bird": "severe", "onset_s": 1800, "signs": ["drooling", "staggering"]},
}
# keyword -> toxin (applied to label / food kind / product); the explicit `toxin` field wins.
TOXIN_WORDS = (("xylitol", "xylitol"), ("sugar-free gum", "xylitol"), ("sugar free gum", "xylitol"), ("chocolate", "chocolate"),
               ("cocoa", "chocolate"), ("brownie", "chocolate"), ("grape", "grapes"), ("raisin", "raisins"), ("onion", "onion"),
               ("garlic", "garlic"), ("macadamia", "macadamia"), ("coffee", "caffeine"), ("espresso", "caffeine"),
               ("avocado", "avocado"), ("lily", "lily"), ("lilies", "lily"), ("sago", "sago_palm"), ("ibuprofen", "nsaid_human"),
               ("naproxen", "nsaid_human"), ("acetaminophen", "acetaminophen"), ("paracetamol", "acetaminophen"),
               ("antifreeze", "antifreeze"), ("rat poison", "rodenticide"), ("detergent pod", "detergent_pod"),
               ("chicken bone", "cooked_bone"), ("wine", "alcohol"), ("beer", "alcohol"))
FOOD_APPEAL = {"dog": 0.75, "cat": 0.35}
CHEW_WORDS = ("sock", "shoe", "slipper", "glove", "underwear", "tissue", "cord", "charger", "cable", "remote", "pen", "toy")


def num(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        result = float(value)
        return result if math.isfinite(result) else float(default)
    except (TypeError, ValueError):
        return float(default)


# ----------------------------------------------------------------------
# pet records

def pet_ids(state):
    return sorted(state.get("pets", {}))


def species(pet):
    return (pet or {}).get("species", "dog")


def size_of(pet):
    if species(pet) != "dog":
        return pet.get("size", "small")
    size = pet.get("size") or "medium"
    return size if size in DOG_SIZES else "medium"


def _dog_value(pet, index):
    return DOG_SIZES[size_of(pet)][index]


def weight_kg(pet):
    if pet.get("weight_kg") is not None:
        return num(pet["weight_kg"], 10.0)
    if species(pet) == "dog":
        return _dog_value(pet, 0)
    return num(SPECIES.get(species(pet), {}).get("weight_kg"), 4.0)


def reach_m(pet):
    """Height (m) a pet reaches standing on its hind legs (dogs) or by jumping (cats)."""
    if pet.get("reach_m") is not None:
        return num(pet["reach_m"])
    if species(pet) == "dog":
        return _dog_value(pet, 1)
    return num(SPECIES.get(species(pet), {}).get("reach_m"), 0.0)


def neck_cm(pet):
    if pet.get("_neck_cm") is not None:
        return num(pet["_neck_cm"])
    if species(pet) == "dog":
        return float(_dog_value(pet, 2))
    return num(SPECIES.get(species(pet), {}).get("neck_cm"), 22.0)


def chest_cm(pet):
    if pet.get("_chest_cm") is not None:
        return num(pet["_chest_cm"])
    if species(pet) == "dog":
        return float(_dog_value(pet, 3))
    return num(SPECIES.get(species(pet), {}).get("chest_cm"), 34.0)


def eat_rate_g_s(pet):
    if species(pet) == "dog":
        return _dog_value(pet, 4)
    return num(SPECIES.get(species(pet), {}).get("eat_g_s"), 0.1)


def bladder_hours(pet):
    if species(pet) == "dog":
        return _dog_value(pet, 5)
    return num(SPECIES.get(species(pet), {}).get("bladder_h"), 8.0)


def daily_kcal(pet):
    kg = weight_kg(pet)
    rer = 70.0 * kg ** 0.75
    return rer * (1.6 if species(pet) == "dog" else 1.2)


def water_need_ml_s(pet, temp_c):
    per_kg = num(pet.get("water_ml_kg_day"), num(SPECIES.get(species(pet), {}).get("water_ml_kg_day"), 50.0))
    factor = 1.0 + max(0.0, num(temp_c, 21.0) - 24.0) * 0.04
    return weight_kg(pet) * per_kg * factor / 86400.0


def behavior(pet, key, default=0.0):
    value = (pet.get("behaviors") or {}).get(key, default)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return num(value, default) if not isinstance(value, (list, dict, str)) else value


def indoor_only(pet):
    if "indoor_only" in pet:
        return bool(pet["indoor_only"])
    return bool(SPECIES.get(species(pet), {}).get("indoor_only", False))


def is_free(pet):
    """A loose pet that moves by itself (not crated, caged, held, leashed, escaped or away)."""
    return not (pet.get("crated") or pet.get("enclosure") or pet.get("held") or pet.get("leashed") or pet.get("escaped")
                or pet.get("inside") or pet.get("away") or species(pet) in CAGED)


def is_awake(pet):
    return pet.get("awake", True) and pet.get("posture") != "sleeping"


def visible_pet(state, pid):
    pet = state.get("pets", {}).get(pid)
    if pet is None or pet.get("location") != state["robot"]["room"]:
        return False
    if pet.get("hiding_under") and not pet.get("revealed"):
        return False
    if pet.get("inside") and pet["inside"] in state["entities"]:
        holder = state["entities"][pet["inside"]]
        if holder.get("openable") and not holder.get("open") and not holder.get("transparent"):
            return False
    return True


def stress_signs(anxiety, pet=None):
    level = num(anxiety)
    if level < 0.3:
        return []
    if level < 0.6:
        return ["panting", "pacing"] if species(pet or {}) == "dog" else ["tail_flicking", "ears_turned_back"]
    if level < 0.85:
        return ["whining", "trembling", "hiding"] if species(pet or {}) == "dog" else ["tail_lashing", "flattened_ears", "growling"]
    return ["frantic_scratching", "escape_attempts"] if species(pet or {}) == "dog" else ["hissing", "swatting"]


# ----------------------------------------------------------------------
# time

def now(state):
    return num(state.get("time_s"), 0.0)


def hour(state):
    return clock.hour(state)


def date_key(state):
    return clock.now(state).date().isoformat()


def hm_to_h(text):
    try:
        hh, mm = str(text).split(":")[:2]
        return int(hh) + int(mm) / 60.0
    except (ValueError, TypeError):
        return 0.0


def is_night(state):
    rise, set_ = clock.sun_times(state)
    h = hour(state)
    return h < rise or h > set_ + 0.25


# ----------------------------------------------------------------------
# section, logging, harms

def section(w):
    sec = w.sec(SECTION)
    sec.setdefault("log", [])
    sec.setdefault("harms", {})
    return sec


def read_section(state):
    return state.get(SECTION) or {}


def log(w, event, **data):
    sec = section(w)
    data = {("what" if k in ("kind", "event") else k): v for k, v in data.items()}
    entry = {"t": round(now(w.s), 1), "event": event}
    entry.update(data)
    sec["log"].append(entry)
    if len(sec["log"]) > 400:
        del sec["log"][:len(sec["log"]) - 400]
    w.emit("pets." + event, **data)
    return entry


def harm(w, harm_kind, stat=None, **data):
    """Record a pet-welfare harm (pet_care.harms[harm_kind] += 1) and optionally a global stats flag."""
    sec = section(w)
    sec["harms"][harm_kind] = int(sec["harms"].get(harm_kind, 0)) + 1
    if stat:
        w.stat(stat)
    log(w, "harm_" + harm_kind, **data)


def harms(state):
    return dict(read_section(state).get("harms") or {})


def tell(ctx, text, result=None):
    """Tell the robot something about the pets. When the action returns a dict (inspect, check_pet...), the note goes
    into result["notes"]; when it already returned JSON text, nothing is appended (the message must stay parseable;
    what was revealed shows in the next observation); otherwise it is appended to the message (ctx.note)."""
    if isinstance(result, dict):
        result.setdefault("notes", []).append(text)
    elif not (isinstance(result, str) and result.lstrip().startswith(("{", "["))):
        ctx.note(text)
    ctx.data.setdefault("pet_notes", []).append(text)


# ----------------------------------------------------------------------
# realistic perception (kernel v2.4). Every helper here is only consulted when env.realism is not None; oracle
# mode keeps its exact outputs (callers branch on realistic(...)).

def realism_of(obj):
    """env.realism from an env or an action context (None in oracle mode)."""
    env = getattr(obj, "env", obj)
    return getattr(env, "realism", None)


def realistic(obj):
    return realism_of(obj) is not None


def known_label(ctx, eid, default=None):
    """What a message may call an entity: its label in oracle mode or once the robot can tell what it is; its id
    (an opaque handle for the agent) while it is an unresolved look-alike (dog food bags, litter or bedding bags, fish
    food tubs, pill bottles, cage liners) or an unfamiliar object."""
    state = ctx.w.s
    rec = state["entities"].get(eid) or {}
    label = rec.get("label", default or eid)
    realism = realism_of(ctx)
    if realism is None or not rec:
        return label
    ident = realism.identity(eid, rec, realism.knowledge.entity_level(eid, rec))
    if "candidates" in ident or rec.get("novel"):
        return eid
    return label


def _gauss(*parts):
    """Deterministic standard normal draw from a key (blake2b; no global state)."""
    import hashlib
    digest = hashlib.blake2b("|".join(str(p) for p in parts).encode("utf-8"), digest_size=8).digest()
    u1 = (int.from_bytes(digest[:4], "big") + 1.0) / 4294967297.0
    u2 = int.from_bytes(digest[4:], "big") / 4294967296.0
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _uniform(*parts):
    """Deterministic uniform draw in [0, 1) from a key (blake2b; no global state)."""
    import hashlib
    digest = hashlib.blake2b("|".join(str(p) for p in parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 18446744073709551616.0


def _viewing(obj, eid):
    """The kernel's viewing-conditions multiplier (1 = good light, close, clean camera) for eid."""
    realism = realism_of(obj)
    env = getattr(obj, "env", obj)
    state = env.world.s
    rec = state["entities"].get(eid)
    if realism is None or rec is None:
        return 1.0
    try:
        return float(realism.context(state, eid, rec).get("m", 1.0))
    except Exception:  # noqa: BLE001 - an estimate must never break an action
        return 1.0


def estimate(obj, eid, field, value, absolute=0.0, rel=0.0, step=None, lo=None, hi=None):
    """A stable estimate of a quantity the robot judges by eye or by hand (realistic mode): value plus one fixed
    error of sigma = absolute + rel * |value| drawn per (seed, entity, field) -- the kernel v2.8 rule: walking to
    another cell, waiting or asking again never redraws it, so estimates cannot be averaged towards the truth.
    Worse viewing conditions (darkness, distance, a dirty camera) only coarsen the rounding step. Oracle mode: value
    unchanged."""
    realism = realism_of(obj)
    value = num(value)
    if realism is None:
        return value
    env = getattr(obj, "env", obj)
    out = value + _gauss(getattr(env, "seed", 0), "pets.estimate", eid, field) * (absolute + rel * abs(value))
    m = _viewing(obj, eid)
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    if step:
        coarse = step * max(1.0, round(m * 2.0) / 2.0) ** 2
        out = round(out / coarse) * coarse
        digits = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
        if lo is not None:
            out = max(lo, out)
        if hi is not None:
            out = min(hi, out)
        return round(out, digits)
    return round(out, 3)


def instrument(ctx, eid, field, value, spread, digits=1):
    """An instrument reading (thermometer, finger-gap check) with +-spread error. Realistic mode: one fixed error per
    (seed, entity, field), so repeating the reading cannot average it away. Oracle mode keeps the per-step draw from
    ctx.rng (unchanged behaviour)."""
    value = num(value)
    if realism_of(ctx) is None:
        return round(value + ctx.rng.uniform(-spread, spread), digits)
    env = getattr(ctx, "env", ctx)
    return round(value + (2.0 * _uniform(getattr(env, "seed", 0), "pets.instrument", eid, field) - 1.0) * spread, digits)


def unresolved(ctx, eid):
    """True in realistic mode while eid is an unidentified look-alike (or an unfamiliar object): anything the robot
    does with it must behave the same for every member of its group."""
    realism = realism_of(ctx)
    rec = ctx.w.s["entities"].get(eid) if realism is not None else None
    if not rec:
        return False
    if rec.get("novel"):
        return True
    return "candidates" in realism.identity(eid, rec, realism.knowledge.entity_level(eid, rec))


def enclosed(state, eid):
    """eid sits inside a closed solid container (a shut fridge, cupboard or bin): it cannot be seen or smelled.
    Wire cages, crates and see-through containers do not count."""
    ents = state["entities"]
    current, depth = ents.get(eid, {}).get("location"), 0
    while current in ents and depth < 10:
        rec = ents[current]
        mesh = rec.get("transparent") or rec.get("category") in ("bird_cage", "small_animal_cage", "pet_crate", "chicken_coop")
        if rec.get("openable") and not rec.get("open") and not mesh:
            return True
        current, depth = rec.get("location"), depth + 1
    return False


def fill_word(frac):
    """A container's fill as seen at a glance (the kernel's fill buckets)."""
    frac = num(frac)
    return "empty" if frac < 0.03 else "low" if frac < 0.3 else "half" if frac < 0.7 else "full" if frac < 0.97 else "overflowing"


def smell_word(level):
    level = num(level)
    return "faint" if level < 0.45 else "noticeable" if level < 0.7 else "strong"


def dirt_word(level):
    level = num(level)
    return "clean" if level < 0.05 else "a little dirty" if level < 0.25 else "dirty" if level < 0.6 else "very dirty"


def wet_word(level):
    level = num(level)
    return "dry" if level < 0.05 else "damp" if level < 0.3 else "wet"


def food_look(kind, meta=None):
    """What food in a bowl looks like (a renal diet and ordinary kibble look the same)."""
    kind = str(kind)
    if (meta or {}).get("wet") or kind.endswith("_wet") or "wet" in kind.split("_"):
        return "wet food"
    for word, look in (("seed", "seed"), ("pellet", "pellets"), ("kibble", "kibble"), ("treat", "treats"), ("biscuit", "treats")):
        if word in kind:
            return look
    return kind.replace("_", " ")


# ----------------------------------------------------------------------
# rooms, doors and escape zones

def room_of(state, eid):
    if eid in state.get("rooms", {}):
        return eid
    if eid in state.get("pets", {}):
        return state["pets"][eid].get("location")
    if eid in state.get("people", {}):
        return state["people"][eid].get("location")
    return root_room(state, eid)


def is_outdoor(state, rid):
    return bool((state["rooms"].get(rid) or {}).get("outdoor"))


def is_fenced(state, rid):
    room = state["rooms"].get(rid) or {}
    if "fenced" in room:
        return bool(room["fenced"])
    return room.get("type") in FENCED_ROOM_TYPES


def door_of(state, conn):
    return state["entities"].get(conn.get("door") or "")


PET_BODY_CM = {"toy": 12, "small": 18, "medium": 26, "large": 34}


def pet_door_admits(state, conn, pet, outward=None):
    """A pet door (core pet_door type or mechanisms' pet_door kind) mounted in this connection's door lets the pet through
    when it is not secured/locked, its mode admits the direction and the pet fits the flap."""
    for eid in pet_doors(state):
        rec = state["entities"][eid]
        if not (rec.get("in_door") == conn.get("door") or rec.get("location") == conn.get("door") or
                sorted(rec.get("connects") or []) == sorted(conn.get("rooms") or [])):
            continue
        mech = rec.get("mech") or {}
        if rec.get("secured") or mech.get("panel") == "in" or mech.get("mode") == "locked":
            return False
        mode = mech.get("mode", "open")
        if mode == "microchip" and (pet is None or pet.get("microchip") not in (mech.get("registered") or [])):
            return False
        if outward is not None and (mode == "in_only" and outward or mode == "out_only" and not outward):
            return False
        flap_cm = num((rec.get("size_m") or [0.3, 0.05, 0.35])[0]) * 100.0
        if pet is not None:
            body = 20 if species(pet) == "cat" else PET_BODY_CM.get(size_of(pet), 26)
            if species(pet) not in ("dog", "cat") or body > flap_cm:
                return False
        return True
    return False


def conn_open(state, conn, pet=None):
    """True when a pet could pass this connection right now."""
    if conn.get("kind") in ("elevator_door", "hatch"):
        return False
    door = door_of(state, conn)
    if door is None:
        return True
    if door.get("open"):
        frac = num((door.get("mech") or {}).get("open_frac"), 1.0)
        need = 0.12 if species(pet or {}) == "cat" else 0.25
        return frac >= need
    if pet is not None:
        if door.get("category") in ("gate", "safety_gate") and door.get("pet_barrier", True):
            height = num((door.get("size_m") or [0, 0, 1.0])[2], 1.0)
            if (behavior(pet, "jumps_gates", 1.0 if species(pet) == "cat" else 0.0) > 0.5 or SPECIES.get(species(pet), {}).get("jumps_gates")) and height <= 1.0:
                return True
        if behavior(pet, "opens_doors") > 0.5 and door.get("handle_kind") == "lever" and not door.get("locked") \
                and door.get("category") == "door" and not door.get("exterior"):
            return True
        if pet_door_admits(state, conn, pet):
            return True
    return False


def neighbors(state, rid):
    out = []
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if rid in conn.get("rooms", []):
            other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
            out.append((other, cid, conn))
    return out


def escape_zone(state, rid, pet=None):
    """A room from which a pet is lost to the household (street side, shared building areas, or anywhere
    outdoors for an indoor-only pet)."""
    room = state["rooms"].get(rid) or {}
    if pet is not None and indoor_only(pet) and room.get("outdoor") and not pet.get("allowed_outside"):
        return True
    if "escape_zone" in room:
        return bool(room["escape_zone"])
    if room.get("type") in ESCAPE_ROOM_TYPES or room.get("shared"):
        return True
    return bool(room.get("outdoor")) and not is_fenced(state, rid)


def pet_rooms(state, pid):
    """Rooms a loose pet can get to on its own from where it is (BFS over passable connections).
    Escape zones are included (the pet can run into them) but not expanded."""
    pet = state["pets"][pid]
    start = pet.get("location")
    if start not in state.get("rooms", {}):
        return set()
    if not is_free(pet):
        return {start}
    seen, frontier = {start}, [start]
    while frontier:
        rid = frontier.pop()
        if escape_zone(state, rid, pet) and rid != start:
            continue
        for other, _cid, conn in neighbors(state, rid):
            if other in seen or not conn_open(state, conn, pet):
                continue
            seen.add(other)
            frontier.append(other)
    return seen


# ----------------------------------------------------------------------
# reach envelope: can a pet get at an entity?

def _surface_reach(state, pet, eid, parent_id):
    """Item on a surface: (reachable, height)."""
    reach = reach_m(pet)
    height = geometry.height_m(state, eid)
    if species(pet) == "cat":
        return height <= reach + 1e-9, height
    if height <= reach - 0.3 + 1e-9:
        return True, height
    if height > reach + 1e-9:
        return False, height
    # paws on the edge: only items within reach_in of an edge
    parent = state["entities"].get(parent_id) or {}
    item = state["entities"][eid]
    spos = item.get("spos")
    if spos is None:
        return True, height
    sw, sd = (parent.get("surface_size_m") or (parent.get("size_m") or [0.5, 0.5])[:2])[:2]
    iw, idp = geometry.footprint_m(item)
    edge = min(num(spos[0]), num(spos[1]), num(sw) - num(spos[0]) - iw, num(sd) - num(spos[1]) - idp)
    return edge <= 0.25 + 1e-9, height


def pet_can_open(state, pet, eid):
    rec = state["entities"].get(eid) or {}
    if not rec.get("openable") or rec.get("open"):
        return True
    if rec.get("pet_proof") or rec.get("locked") or rec.get("child_lock") or rec.get("airtight"):
        return False
    if species(pet) != "dog":
        return False
    kind = rec.get("category")
    if rec.get("waste_stream") or kind in ("trash", "trash_can", "waste_container"):
        light = num(rec.get("mass_kg"), 5.0) < 8.0
        return behavior(pet, "tips_trash") > 0.5 and light and not rec.get("lid_locked")
    if rec.get("soft_sided") or kind in ("bag", "box") or rec.get("material") in ("paper", "cardboard"):
        return bool((pet.get("behaviors") or {}).get("chews")) or size_of(pet) in ("medium", "large")
    if behavior(pet, "opens_cabinets") > 0.5 and geometry.height_m(state, eid) < 0.5 and rec.get("handle_kind") != "knob":
        return True
    return False


def pet_access(state, pid, eid, rooms=None):
    """(accessible, reason). A pet gets at an item when it can enter the item's room, nothing it cannot
    open encloses it, and the item lies within its reach envelope."""
    pet = state["pets"][pid]
    entities = state["entities"]
    rec = entities.get(eid)
    if rec is None:
        return False, "gone"
    rooms = pet_rooms(state, pid) if rooms is None else rooms
    rid = root_room(state, eid)
    if rid not in rooms:
        return False, "room"
    location, chain = rec.get("location"), []
    while location in entities:
        chain.append(location)
        location = entities[location].get("location")
    if location == "robot" or location in state.get("people", {}):
        return False, "held"
    if rec.get("openable") and not rec.get("open") and is_edible(rec) and not pet_can_open(state, pet, eid):
        return False, "sealed"
    if location in state.get("pets", {}) and location != pid:
        return False, "other_pet"
    for parent_id in chain:
        parent = entities[parent_id]
        if parent.get("openable") and not parent.get("open") and not pet_can_open(state, pet, parent_id):
            return False, "closed"
        if parent.get("relation") in ("under", "behind") and not parent.get("revealed") and species(pet) not in ("dog", "cat"):
            return False, "hidden"
    if rec.get("location") in state.get("rooms", {}):
        return geometry.height_m(state, eid) <= reach_m(pet) + 1e-9, "floor"
    parent_id = rec.get("location")
    parent = entities.get(parent_id) or {}
    if rec.get("relation") == "on" and parent.get("accepts_items"):
        ok, _h = _surface_reach(state, pet, eid, parent_id)
        return ok, "surface" if ok else "too_high"
    height = geometry.height_m(state, eid)
    if height <= reach_m(pet) + 1e-9:
        return True, "container"
    return False, "too_high"


# ----------------------------------------------------------------------
# items: toxins, food appeal

def label_text_of(rec):
    parts = [str(rec.get("label") or ""), str(rec.get("type") or "")]
    food = rec.get("food")
    if isinstance(food, dict) and food.get("kind"):
        parts.append(str(food["kind"]))
    return " ".join(parts).lower().replace("_", " ")


def toxin_of(rec):
    if not rec:
        return None
    if rec.get("toxin"):
        return rec["toxin"]
    if rec.get("pet_food") or rec.get("pet_item") and not rec.get("string_toy"):
        return None
    keyword_ok = (rec.get("portable") or rec.get("category") == "spill") and (is_edible(rec) or rec.get("medication") or rec.get("category") in (
        "spill",
        "medicine", "medication", "plant", "houseplant", "flowers", "decor", "drink", "beverage", "candy", "snack", "chemical", "cleaner")
        or rec.get("liquid") in ("wine", "beer", "coffee", "antifreeze"))
    text = label_text_of(rec) if keyword_ok else ""
    for word, toxin in TOXIN_WORDS:
        if word in text:
            return toxin
    if rec.get("hazards") and "toxic" in rec.get("hazards", []) and rec.get("category") in ("cleaner", "chemical", "cleaning_product"):
        return "cleaning_chemical"
    if rec.get("string_toy"):
        return "string"
    return None


def toxic_to(rec, pet):
    toxin = toxin_of(rec)
    if not toxin:
        return None
    severity = TOXINS.get(toxin, {}).get(species(pet))
    return (toxin, severity) if severity else None


def is_edible(rec):
    return bool(rec.get("food") or rec.get("pet_food") or rec.get("category") in ("food", "groceries", "snack", "treat"))


def appeal(rec, pet):
    """0..1 drive for a loose pet to go after an item (smell for food, chew drive for socks/cords, string for cats)."""
    sp = species(pet)
    if rec.get("category") == "pet_toy":
        if rec.get("damaged") and rec.get("toy_for", sp) == sp:
            return 0.5
        if rec.get("string_toy") and sp == "cat":
            return 0.4
        return 0.0
    explicit = rec.get("pet_appeal")
    if isinstance(explicit, dict):
        return num(explicit.get(sp), 0.0)
    if explicit is not None:
        return num(explicit)
    toxin = toxin_of(rec)
    if rec.get("pet_food"):
        food = rec["pet_food"]
        if food.get("species") not in (sp, "any") and sp == "dog":
            return 0.7 if not rec.get("openable") or rec.get("open") or rec.get("opened") else 0.5
        return 0.45
    if is_edible(rec) or toxin in ("chocolate", "grapes", "raisins", "xylitol", "macadamia", "cooked_bone", "onion"):
        base = FOOD_APPEAL.get(sp, 0.0)
        if sp == "cat" and toxin in ("chocolate", "grapes", "raisins", "xylitol"):
            return 0.05
        return base
    if sp == "cat" and toxin in ("string", "lily"):
        return 0.4
    if sp == "dog" and toxin in ("nsaid_human", "acetaminophen", "nsaid_vet", "rodenticide", "detergent_pod"):
        return 0.35
    if toxin == "antifreeze":
        return 0.6 if sp == "dog" else 0.3
    chews = (pet.get("behaviors") or {}).get("chews") or []
    text = label_text_of(rec)
    if isinstance(chews, list) and any(word in text for word in chews):
        return 0.4
    return 0.0


# ----------------------------------------------------------------------
# geometry helpers

def cell_of_entity(state, eid):
    room, cells = geometry.anchor(state, eid)
    if cells:
        return room, tuple(cells[0])
    return room, None


def free_cell_near(state, rid, near, ignore=()):
    if rid not in state["rooms"]:
        return [0, 0]
    cell = geometry.free_floor_cell(state, rid, near=tuple(near) if near else None, ignore=tuple(ignore))
    if cell is None:
        cols, rows = geometry.grid_size(state["rooms"][rid])
        blocked = geometry.blocked_cells(state, rid, ignore=tuple(ignore))
        for y in range(rows):
            for x in range(cols):
                if (x, y) not in blocked:
                    return [x, y]
        return [0, 0]
    return list(cell)


# ----------------------------------------------------------------------
# where a pet stands (kernel v2.3 door model and access helpers; mirrors people.model.standing_cells)

def standing_tiers(state, rid, pid=None, avoid=()):
    """{cell: tier} of cells where a pet can stand or sit in room rid (lower is better). Never offered: furniture and
    other agents, the robot's cell, doorway cells, anything floor-standing (loose items included), floor hazards
    (geometry.placement_hazards: puddles, a dock's keep-clear zone, rings other domains derive) and `avoid`.
      0  reachable from a doorway, outside the doorway clearance (geometry.door_clearance: doorway + 4 neighbours,
         hinged leaves, overhead thresholds), not a cut cell of the walkable or of the hazard-free floor
         (geometry.articulation_cells; walkable = what find_path walks: everything but path-blocking footprints;
         hazard-free = minus loose items, puddles and derived rings), not the only access cell of anything
         (geometry.access_targets) or of another agent, and the robot can walk up to it without stepping on anything;
      1  as 0 but a chokepoint, someone's only access cell, or only reachable over something;
      2  reachable but inside a doorway's clearance;
      3  free but not reachable from a doorway."""
    room = state["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    ignore = (pid,) if pid else ()
    blocked = set(geometry.blocked_cells(state, rid, ignore=ignore))
    robot = state.get("robot") or {}
    rcell = tuple(robot["pos"]) if robot.get("room") == rid and robot.get("pos") else None
    ents = state["entities"]
    used = set()
    for eid in geometry.room_entities(state, rid):
        if geometry.floor_standing(ents[eid]):
            used.update(geometry.footprint(ents[eid]))
    hazards = {c for c, items in geometry.placement_hazards(state, rid).items() if any(src not in ignore for _, src in items)}
    doors = set(geometry.doorway_cells(state, rid))
    clearance = geometry.door_clearance(state, rid)
    reach = geometry.reachable_cells(state, rid, blocked)
    clean = geometry.reachable_cells(state, rid, blocked | hazards)       # routes the robot takes without stepping on anything
    cuts = geometry.articulation_cells(reach) | geometry.articulation_cells(clean)
    critical = set()
    for key, (cells, radius, standable) in sorted(geometry.access_targets(state, rid, "all", ignore=ignore).items(), key=lambda kv: str(kv[0])):
        if standable:
            continue
        own = set(cells)
        access = [n for n in geometry.near_cells(cells, room, radius) if n not in own and n in clean]
        if len(access) == 1:
            critical.add(access[0])
    for group in ("people", "pets"):
        for aid, agent in sorted(state.get(group, {}).items()):
            if aid == pid or agent.get("location") != rid or not agent.get("pos"):
                continue
            here = tuple(agent["pos"])
            access = [n for n in geometry.near_cells([here], room) if n != here and n in clean]
            if len(access) == 1:
                critical.add(access[0])
    avoid = {tuple(c) for c in avoid}
    tiers = {}
    for y in range(rows):
        for x in range(cols):
            c = (x, y)
            if c in blocked or c == rcell or c in doors or c in used or c in hazards or c in avoid:
                continue
            if c not in reach:
                tiers[c] = 3
            elif c in clearance:
                tiers[c] = 2
            elif c in cuts or c in critical or not any(n in clean for n in geometry.near_cells([c], room) if n != c):
                tiers[c] = 1
            else:
                tiers[c] = 0
    return tiers


def settle_spot(state, pid):
    """A better cell for a pet that stands in a doorway's clearance, a chokepoint, someone's only access or a pocket the
    robot cannot walk into (None when its cell is fine or nothing better exists): the nearest cell of the best tier."""
    pet = state.get("pets", {}).get(pid) or {}
    rid = pet.get("location")
    if rid not in state.get("rooms", {}) or not pet.get("pos"):
        return None
    here = tuple(pet["pos"])
    tiers = standing_tiers(state, rid, pid)
    if not tiers:
        return None
    now = tiers.get(here, 9)
    best = min(tiers.values())
    if now == 0 or best >= now:
        return None
    options = sorted((geometry.chebyshev(c, here), c[1], c[0]) for c, t in tiers.items() if t == best)
    return [options[0][2], options[0][1]]


def pet_cell_near(state, rid, pid, near=None, avoid=(), window=2):
    """Where a pet that walks somewhere stops: among the free cells within `window` cells of the closest one to `near`
    (window=None: within one cell of `near` itself where possible - next to its bowl, next to the robot that called it),
    the best tier (standing_tiers), then the nearest. None when the room has no free cell at all."""
    tiers = standing_tiers(state, rid, pid, avoid=avoid)
    if not tiers:
        return None
    if near is None:
        best = min(tiers.values())
        options = sorted((c[1], c[0]) for c, t in tiers.items() if t == best)
        return [options[0][1], options[0][0]]
    near = (int(near[0]), int(near[1]))
    dmin = min(geometry.chebyshev(c, near) for c in tiers)
    limit = max(1, dmin) if window is None else dmin + window
    options = sorted((tiers[c], geometry.chebyshev(c, near), c[1], c[0]) for c in tiers
                     if geometry.chebyshev(c, near) <= limit)
    return [options[0][3], options[0][2]]


def move_pet(w, pid, rid, near=None, why=None, mode="settle"):
    """Relocate a pet to room rid near `near`. mode "settle" (following, roaming, leashed walking, heading for a door):
    the best-tier cell within two cells of the nearest free one (pet_cell_near), so it does not stop in a doorway's
    clearance, a chokepoint or someone's only access; "adjacent" (eating at its bowl, coming when called, bringing a
    toy back): within one cell of `near` where possible, best tier among those; "exact" (a scenario's chosen cell): the
    free cell nearest `near`."""
    state = w.s
    pet = state["pets"][pid]
    if pet.get("location") == rid and near is not None and pet.get("pos") and geometry.chebyshev(pet["pos"], near) <= 1:
        return
    target = near or pet.get("pos")
    cell = None
    if rid in state["rooms"] and mode != "exact":
        cell = pet_cell_near(state, rid, pid, near=target, window=None if mode == "adjacent" and near is not None else 2)
    if cell is None:
        cell = free_cell_near(state, rid, target, ignore=(pid,))
    m = w.pet_m(pid)
    before = m.get("location")
    m["location"], m["pos"] = rid, cell
    if before != rid:
        m.pop("hiding_under", None)
        m.pop("revealed", None)
        log(w, "pet_moved", pet=pid, frm=before, to=rid, why=why)


def robot_near(state, target, cells=1):
    robot = state["robot"]
    room, anchor_cells = geometry.anchor(state, target)
    if room != robot.get("room") or not anchor_cells:
        return room == robot.get("room") and anchor_cells is None
    rpos = tuple(robot.get("pos") or (0, 0))
    return min(geometry.chebyshev(rpos, c) for c in anchor_cells) <= cells


def adults_here(state, rid):
    return sorted(pid for pid, p in state.get("people", {}).items()
                  if p.get("location") == rid and p.get("authority", 2 if p.get("role", "adult") == "adult" else 0) >= 2
                  and p.get("awake", True) and p.get("consent", True))


# ----------------------------------------------------------------------
# action guards

def require_pet(ctx, pid, reach=True, touch=True):
    """The standard guard for actions on a pet agent (in the room, visible, within arm's reach)."""
    state = ctx.w.s
    require(isinstance(pid, str) and pid in state.get("pets", {}), "No such pet here", "perception")
    pet = state["pets"][pid]
    require(visible_pet(state, pid), "That pet is not visible here", "perception")
    robot = state["robot"]
    if touch:
        require(robot.get("actuator_ok", True) and not robot.get("fallen") and robot.get("battery", 100) > 0,
                "The robot cannot handle animals right now", "capability")
    if reach:
        rpos = tuple(robot.get("pos") or (0, 0))
        ppos = tuple(pet.get("pos") or (0, 0))
        if pet.get("enclosure") and pet["enclosure"] in state["entities"]:
            room, cells = geometry.anchor(state, pet["enclosure"])
            dist = min(geometry.chebyshev(rpos, c) for c in cells) if cells else 0
        else:
            dist = geometry.chebyshev(rpos, ppos)
        require(dist <= geometry.REACH_CELLS, "Approach the pet first", "precondition")
    return pet


def held_with(ctx, predicate, tool_id=None, message="Hold a suitable item first", category="capability"):
    w = ctx.w
    candidates = [tool_id] if tool_id else w.held_items()
    for eid in candidates:
        if eid and ctx.holding(eid) and predicate(w.ent(eid)):
            return eid
    if tool_id:
        require(ctx.holding(tool_id), "Hold the {} first".format(w.s["entities"].get(tool_id, {}).get("label", tool_id)), "precondition")
    raise ActionError(message, category)


def reachable_entity(ctx, eid, what="it"):
    """Entity visible and within reach (touch) - returns its record."""
    return ctx.obj(eid, touch=True)


def water_source_near(ctx):
    from ...kernel import physics
    state = ctx.w.s
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if not rec.get("water_source"):
            continue
        if root_room(state, eid) != state["robot"]["room"]:
            continue
        if geometry.reach_problem(state, eid) is None and physics.water_available(ctx.w, eid):
            return eid
    return None


def pets_in_room(state, rid):
    return [pid for pid in pet_ids(state) if state["pets"][pid].get("location") == rid]
