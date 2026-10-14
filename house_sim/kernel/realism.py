"""Realistic (imperfect) symbolic perception: what a camera-and-sensors robot could plausibly know.

HouseEnv(..., perception="realistic") routes every observation, action message and action argument through this
module; perception="oracle" (the default, used by the verification pipeline) is untouched.

  * Opaque handles: entities are exposed as per-episode handles ("obj_7f3a19c2" = short hash of seed and true id).
    Arguments must use handles (true entity ids are refused as "not visible"); results and observations are
    translated back (ids of things destroyed during a step included; JSON results re-serialised in handle order).
    Room, person and pet ids stay readable (the robot knows the house and its residents).
  * Field tiers: every entity field is learnable at "glance" (seen from the room), "inspect" (examined within
    reach), "read" (printed text), "measure" (needs a tool; touch gives a coarse bucket) or "never" (ground truth
    such as owner, home, value, intent, secret and underscore fields). Kernel defaults by field name, overridable
    per type in the type JSON: "perception": {"glance": [...], "inspect": [...], "read": [...],
    "measure": {"field": "tool category"}, "touch": [...], "hidden": [...]}. Unknown fields are inspect-tier.
  * Knowledge: per entity and field, the best level the robot has obtained (glance < inspect < touch < read <
    measure), kept outside world state, granted only by actions that succeeded (ctx.grant) and downgraded when
    the object moves or the value changes (it may have changed since).
  * Quantization and noise: continuous values become buckets at low knowledge and seeded, stable noisy values at
    higher knowledge; uncertainty grows with distance, darkness, a dirty camera, fast motion, glare and steam or
    smoke. Small objects can be missed at a glance (stable per viewpoint); look-alike groups are confused until
    inspected or read; "novel" objects are described only by visual attributes; mirrors look like doorways until
    inspected.

This is symbolic perception: no pixels, no learned detector, no camera geometry. Everything here is a pure
function of (seed, true id, observation context, knowledge level); it never mutates world state.
"""
import copy
import hashlib
import json
import math
import random
import re

from . import geometry
from .errors import ActionError

GLANCE, INSPECT, TOUCH, READ, MEASURE = 0, 1, 2, 3, 4
LEVELS = {"glance": GLANCE, "inspect": INSPECT, "inspected": INSPECT, "touch": TOUCH, "touched": TOUCH,
          "read": READ, "measure": MEASURE, "measured": MEASURE}
TIER_NEED = {"glance": GLANCE, "inspect": INSPECT, "read": READ, "measure": MEASURE}
MISSING = object()
HANDLE_RE = re.compile(r"^obj_[0-9a-f]{8,16}$")
VISITOR_ROLES = {"visitor", "stranger", "solicitor", "contractor", "delivery", "guest", "worker", "gardener", "plumber",
                 "electrician", "technician", "courier", "neighbor_unknown"}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# what generated entity ids look like (type_3, dishwasher_1_basket): such a word in free text is neutralised whether or
# not a thing with that id exists, so the agent's own words never tell which ids exist
ID_WORD_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
ID_NUMBER_RE = re.compile(r"_[0-9]+(?:_|$)")
# in the dark nobody can tell who a shape is: a person or pet named by id is refused (address the shape's handle)
DARK_WHO = "It is too dark to tell who is who here: turn on a light, or address the shape you see"
# hazard kinds of a lying agent (asleep and awake look the same: realistic observations show "lying"; fallen stays
# person_down) and the postures that look like lying down
AGENT_LYING_KINDS = ("sleeping", "person_down", "lying")
LYING_POSTURES = {"people": ("sleeping", "lying"), "pets": ("sleeping", "lying", "resting")}
# floor hazards a camera does not see
HIDDEN_HAZARD_KINDS = {"damp", "film", "black_ice", "residue", "invisible"}


def id_shaped(word):
    """True for a lower-case word shaped like a generated entity id (letters/digits joined by "_" with a numeric part)."""
    return bool(ID_WORD_RE.match(word)) and ID_NUMBER_RE.search(word) is not None


def _typed_id(word, stems):
    """An id-shaped word whose part before one of its numeric parts is a type name in `stems`
    ("dishwasher_1_basket" -> dishwasher; "combination_wrench_1_2in_3" -> combination_wrench_1_2in)."""
    if not id_shaped(word):
        return False
    pos = 0
    while True:
        m = ID_NUMBER_RE.search(word, pos)
        if m is None:
            return False
        if word[:m.start()] in stems:
            return True
        pos = m.start() + 1


def _record_names(record, depth=0):
    """Lower-case dict keys and string values of a record (slot names, burner keys, program names...), a few levels
    deep: words that name parts of that very thing, which a command about it may use."""
    out = set()
    if depth > 4:
        return out
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(key, str):
                out.add(key.lower())
            out |= _record_names(value, depth + 1)
    elif isinstance(record, list):
        for value in record[:64]:
            out |= _record_names(value, depth + 1)
    elif isinstance(record, str) and len(record) <= 64:
        out.add(record.lower())
    return out

# ----- kernel default field tiers ---------------------------------------------------------------------------
DEFAULT_TIERS = {
    "glance": ["label", "category", "color", "relation", "location", "pos", "rot", "spos", "shelf", "open", "openable",
               "container", "surface", "mounted", "portable", "accepts_items", "posture", "held_hand", "connects",
               "near_connection", "covered", "cover_open", "shattered", "smoking", "on_fire", "overflowing", "lit",
               "burning", "steaming", "leaking", "flooded", "hang_slots", "size_class", "shape", "fill_level", "level",
               "liquid_l", "soil", "wetness", "count", "brightness", "dim_level", "surface_size_m", "mount_height_m",
               "tipped_over", "toppled", "fallen", "knocked_over", "shifted", "protrudes", "displaced", "bunched",
               "curled", "overturned", "spilled", "smashed"],
    "inspect": ["material", "damage", "condition", "locked", "plugged_in", "settings", "orientation", "contents",
                "liquid", "state", "broken", "stain", "stains", "wrinkled", "folded", "freshness", "ripeness",
                "fragile", "hazards", "capacity_l", "inner_size_m", "shelf_heights", "parts", "slots", "on",
                "cracked", "worn", "faded", "pilling", "odor", "moisture", "doneness", "cooked", "frozen", "thawed"],
    "read": ["text", "label_text", "expiration", "expires", "date_label", "address", "recipient", "care_label",
             "instructions", "display", "steps", "patient", "prescribed_to", "dosage", "rx", "name_label", "sku",
             "serial", "model", "brand", "title", "author", "barcode", "best_by", "use_by", "warning", "directions"],
    "measure": {"temperature_c": "thermometer", "internal_temp_c": "thermometer", "core_temp_c": "thermometer",
                "mass_kg": "scale", "weight_kg": "scale", "size_m": "tape_measure", "moisture": "moisture_meter",
                "battery_pct": "battery_tester", "charge": "battery_tester", "charge_pct": "battery_tester",
                "voltage": "multimeter", "ph": "test_strip", "pressure_psi": "pressure_gauge", "volume_l": "measuring_cup"},
    # learnable only by asking or inference (unless labeled)
    "hidden": ["owner", "home", "value", "sentimental", "important", "intent", "secret_fields", "private_contents",
               "permissions", "hidden", "is_a", "type", "origin", "belongs_to", "keep", "discard", "decoy",
               "expected", "correct", "target", "goal", "spec", "answer", "truth", "risk", "requires_approval",
               "off_limits", "private", "home_shelf", "assigned_to", "favorite", "gift_for", "purpose",
               # simulation parameters and bookkeeping (never perceivable)
               "heat_w", "thermal_r_free", "thermal_r_blocked", "thermal_tau_s", "max_touch_temp_c", "fuel_kg", "peak_kw",
               "heat_load_w", "ua_w_k", "air_heat_capacity_j_k", "heat_coupling", "contamination", "cont_faces",
               "stream_ok", "in_stream", "relined_dirty", "filled_s", "stale", "potable", "derived_from",
               "contents_from", "liquid_from"],
}
# name patterns of simulation internals (hidden unless a type/domain table says otherwise)
HIDDEN_SUFFIXES = ("_until_s", "_since_s", "_at_s", "_tau_s", "_w_k", "_j_k", "_coeff", "_rate_s")
HIDDEN_PREFIXES = ("cl_", "sim_", "model_", "cont_")
# fields a lit-and-close glance shows only for things with a visible indicator (status light, display, flame)
INDICATOR_CATEGORIES = {"light", "lamp", "tv", "screen", "monitor", "display", "fan", "heater", "stove", "range",
                        "oven", "microwave", "appliance", "computer", "router", "speaker", "candle", "fireplace"}
# what touching gives (coarse) for measure-tier fields
TOUCH_FIELDS = ("temperature_c", "internal_temp_c", "mass_kg", "weight_kg", "moisture", "wetness")
MOOD_CUES = {"sad": "tearful", "distressed": "crying", "upset": "frowning", "angry": "raised voice",
             "frustrated": "sighing", "anxious": "pacing", "scared": "trembling", "afraid": "trembling",
             "pain": "wincing", "hurt": "wincing", "happy": "smiling", "content": "relaxed", "excited": "animated",
             "tired": "yawning", "sleepy": "yawning", "sick": "pale and coughing", "ill": "pale and coughing",
             "bored": "restless", "lonely": "withdrawn", "confused": "looking around"}
# default look-alike groups (types/categories; domains add more with "looks_like" in their type JSON)
DEFAULT_LOOKS_LIKE = {
    "white_powder_jar": {"descriptor": "jar of white powder", "resolve": "read",
                         "members": ["salt", "sugar", "baking_soda", "baking_powder", "flour", "powdered_sugar", "cornstarch"]},
    "pill_bottle": {"descriptor": "amber pill bottle", "resolve": "read", "members": ["pill_bottle", "medicine_bottle", "prescription_bottle"]},
    "key": {"descriptor": "metal key", "resolve": "inspect", "members": ["key"]},
    "remote": {"descriptor": "black remote control", "resolve": "inspect", "members": ["remote", "tv_remote", "remote_control"]},
    "spray_bottle": {"descriptor": "spray bottle of liquid", "resolve": "read",
                     "members": ["spray_bottle", "glass_cleaner", "bleach_spray", "all_purpose_cleaner", "disinfectant_spray"]},
    "clear_liquid_bottle": {"descriptor": "bottle of clear liquid", "resolve": "read", "members": ["water_bottle", "vinegar", "white_vinegar", "rubbing_alcohol"]},
    "sock": {"descriptor": "sock", "resolve": "inspect", "members": ["sock"]},
}
SIZE_CLASSES = ((0.05, "tiny"), (0.2, "small"), (0.6, "medium"), (1.5, "large"))


SKIP_FIELDS = {"label", "type", "category", "is_a"}
LOOKALIKE_FIELDS = {"label", "kind", "candidates", "shape", "material_guess", "color", "size_class", "size_m", "location", "pos",
                    "spos", "shelf", "relation", "rot", "open", "openable", "container", "surface", "portable", "held_hand",
                    "soil", "wetness", "temperature_c", "fill_level", "level", "liquid_l", "broken", "shattered",
                    "label_text", "text"}
# descriptive text fields: never id-translated (a label may coincide with an id in hand-written houses)
DESCRIPTIVE_KEYS = {"label", "kind", "candidates", "descriptor", "category", "material", "color", "shape", "cues",
                    "posture", "activity", "role", "species", "size_class", "material_guess", "label_text", "text"}
# fields with their own rendering (buckets, noise, indicators) whatever their tier
SPECIAL_FIELDS = {"soil", "wetness", "moisture", "temperature_c", "internal_temp_c", "core_temp_c", "fill_level", "level",
                  "liquid_l", "mass_kg", "weight_kg", "size_m", "count", "freshness", "brightness", "dim_level",
                  "battery_pct", "charge", "charge_pct", "on"}
# what a container holds (systems/core.py CONTENT_FIELDS): never a label, so never what identifies a look-alike
CONTENT_FIELDS = {"liquid", "contents", "product", "chemical", "substance", "ingredient", "ingredients", "medication", "drug",
                  "active_ingredient", "species", "variety", "flavor", "kind"}
# fields rendered from their source's identity while it is unresolved (destination records carry contents_from)
PROVENANCE_FIELDS = ("liquid", "contents", "product")
# identity values the kernel renders for unresolved look-alikes (never rewritten in outbound messages)
IDENTITY_KEYS = ("label", "kind", "candidates", "descriptor")
# placeholder type names/labels that say nothing about what a thing is (never rewritten in messages)
GENERIC_NAMES = {"object", "item", "thing", "generic item", "generic_item", "portable object", "portable_object"}
# dict-valued {kind: amount} fields shown as word buckets (never exact floats) unless measured with a tool
WORD_BUCKETS = {"odor": ((0.3, 0.6), ("faint", "noticeable", "strong")),
                "damage": ((0.3, 0.6), ("light", "moderate", "severe"))}
# 0..1 amounts and conditions (besides *_level/*_fraction names and fields whose type default lies in [0, 1])
UNIT_FIELDS = {"remaining", "shine", "wrinkled", "pilling", "faded", "dye_damage", "fullness", "progress", "coverage"}
# JSON message lists under keys naming an order keep their order (others in true-id order are re-sorted by handle)
ORDERED_KEY_WORDS = ("order", "sequence", "path", "route", "stack", "queue", "steps", "history", "trail", "rank")
ID_KEYS = ("id", "object", "item", "entity", "eid", "target")


def _h(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _digest(value):
    try:
        return _h(json.dumps(value, sort_keys=True, default=str))[:16]
    except (TypeError, ValueError):
        return _h(repr(value))[:16]


def _num(value, default=None):
    try:
        if isinstance(value, bool):
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _field_value(record, name):
    """record[name], or the value at a dotted path ("hopper.kg", "slots.0.holds_n") inside dict/list fields."""
    if name in record:
        return record[name]
    if "." not in name:
        return None
    current = record
    for part in name.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def size_class(entity):
    size = entity.get("size_m") or [0.1, 0.1, 0.1]
    try:
        largest = max(float(v) for v in size[:3])
    except (TypeError, ValueError):
        return "small"
    for limit, name in SIZE_CLASSES:
        if largest < limit:
            return name
    return "huge"


def _bucket(value, edges, names):
    for edge, name in zip(edges, names):
        if value < edge:
            return name
    return names[len(edges)]


# bucket schemes: name -> (edges, labels)
SOIL4 = ((0.05, 0.25, 0.6), ("clean", "light", "dirty", "very dirty"))
SOIL6 = ((0.02, 0.08, 0.2, 0.4, 0.7), ("none", "trace", "light", "moderate", "heavy", "caked"))
TEMP6 = ((8.0, 17.0, 26.0, 40.0, 60.0), ("cold", "cool", "room temperature", "warm", "hot", "very hot"))
FILL5 = ((0.03, 0.3, 0.7, 0.97), ("empty", "low", "half", "full", "overflowing"))
WET3 = ((0.05, 0.3), ("dry", "damp", "wet"))
MASS4 = ((0.5, 3.0, 12.0), ("light", "medium", "heavy", "very heavy"))
COUNT3 = ((4, 10), ("a few", "several", "many"))
FRESH4 = ((0.25, 0.5, 0.8), ("spoiled", "stale", "aging", "fresh"))
BRIGHT4 = ((0.05, 0.35, 0.7), ("off", "dim", "medium", "bright"))
CHARGE4 = ((0.1, 0.4, 0.8), ("empty", "low", "half", "full"))


class Handles:
    """Per-episode opaque handles: obj_ + 8 hex digits of sha256(salt, id) (longer on a collision). The salt mixes
    the seed with a digest of the initial house, which agents never see, so handles cannot be computed from guessed
    ids. An unknown handle is resolved by recomputing the handles of the current entities (replays and loaded
    states need no stored map)."""

    def __init__(self, seed, salt=""):
        self.seed, self.salt = seed, salt
        self.by_id, self.by_handle = {}, {}
        self.entities = None   # callable -> iterable of current entity ids (for lazy resolution)

    def handle(self, eid):
        found = self.by_id.get(eid)
        if found:
            return found
        digest = _h("handle", self.seed, self.salt, eid)
        width = 8
        candidate = "obj_" + digest[:width]
        while candidate in self.by_handle and self.by_handle[candidate] != eid:
            width += 2
            candidate = "obj_" + digest[:width]
        self.by_id[eid], self.by_handle[candidate] = candidate, eid
        return candidate

    def resolve(self, text):
        found = self.by_handle.get(text)
        if found is None and self.entities is not None and HANDLE_RE.match(text or ""):
            for eid in sorted(self.entities()):
                if eid not in self.by_id and "obj_" + _h("handle", self.seed, self.salt, eid)[:len(text) - 4] == text:
                    return eid
        return found

    def dump(self):
        return dict(sorted(self.by_id.items()))

    def load(self, data):
        self.by_id, self.by_handle = {}, {}
        for eid, handle in (data or {}).items():
            self.by_id[eid], self.by_handle[handle] = handle, eid


class Knowledge:
    """What the robot has learned: {eid: {"sig": placement signature, "fields": {field: [level, value digest]}}}."""

    def __init__(self):
        self.data = {}

    @staticmethod
    def signature(record):
        return _digest([record.get("location"), record.get("pos"), record.get("spos"), record.get("relation"), record.get("shelf")])

    def grant(self, eid, record, level, fields=None):
        entry = self.data.get(eid)
        sig = self.signature(record)
        if entry is None or entry["sig"] != sig:
            entry = self.data[eid] = {"sig": sig, "fields": {}, "level": GLANCE}
        names = list(fields) if fields is not None else [k for k in record if not k.startswith("_")]
        for name in names:
            old = entry["fields"].get(name)
            raw = _field_value(record, name)       # dotted names ("hopper.kg") are sub-fields of dict fields
            value = _digest(raw)
            if old is None or old[1] != value or old[0] < level:
                entry["fields"][name] = [max(level, old[0]) if old and old[1] == value else level, value,
                                         raw if isinstance(raw, float) else None, copy.deepcopy(raw)]
        if fields is None:
            entry["level"] = max(entry.get("level", GLANCE), level)

    def stale(self, eid, field):
        """(level, remembered value) of a field whose current value no longer matches what was learned."""
        entry = self.data.get(eid) or {}
        known = entry.get("fields", {}).get(field)
        if known is None or len(known) < 4:
            return None
        return known[0], known[3]

    def level(self, eid, record, field):
        entry = self.data.get(eid)
        if entry is None or entry["sig"] != self.signature(record):
            return GLANCE
        known = entry["fields"].get(field)
        if known is None:
            return GLANCE
        current = record[field] if field in record else _field_value(record, field)
        if known[1] != _digest(current):
            # continuous values drift: knowledge holds while within ~tool precision of what was learned
            if len(known) > 2 and isinstance(current, float) and isinstance(known[2], float) and \
                    abs(current - known[2]) <= max(0.5, 0.02 * abs(known[2])):
                return known[0]
            return GLANCE
        return known[0]

    def entity_level(self, eid, record):
        entry = self.data.get(eid)
        if entry is None or entry["sig"] != self.signature(record):
            return GLANCE
        return entry.get("level", GLANCE)

    def dump(self):
        return copy.deepcopy(self.data)

    def load(self, data):
        self.data = copy.deepcopy(data or {})


class Realism:
    def __init__(self, env):
        self.env = env
        self.handles = Handles(env.seed, salt=_digest(getattr(env, "_initial", None) or {}))
        self.handles.entities = lambda: self.env.world.s["entities"].keys()
        self.knowledge = Knowledge()
        self._tiers = {}
        self._groups = None
        self.pending = []          # grants of the step in progress (applied only if it succeeds)
        self._room_ctx = None
        self.observed = []         # optional log of observations/messages (verifier perceivability check)
        self.record_observations = False
        self._group_cache = {}     # (looks_like, type, is_a, category) -> group name (or None)
        self._common = {}          # group -> glance values every unresolved member shows (color, size)
        self._shared = {}          # group -> words every member's type/category/label shares
        self._unit_cache = {}      # (type, field) -> the type default lies in [0, 1]
        self._hook_cache = None
        # every entity id that existed in this episode (ids of things destroyed by a step must not leak either) and
        # what the robot knew of destroyed look-alikes, novel things and portions (their portions/contents need it)
        self._ever = set(env.world.s["entities"]) if getattr(env, "world", None) is not None else set()
        self.gone = {}
        self._step_gone = {}       # records destroyed by the step in progress (their message still names them)
        self._agent_text = ()      # the agent's own free-text arguments of the step in progress (as converted)
        self._names = {}           # look-alike group -> true names of its member types (message substitution)
        # darkness: people/pets in the robot's dark room are shapes keyed by a handle that changes with every dark
        # spell (a spell starts whenever the robot's view of a room turns dark)
        self._spell = 0
        self._last_view = None
        self._dark_now = None
        self._plain_now = None

    # ----- registry-driven tables ------------------------------------------------------------------------
    def groups(self):
        if self._groups is None:
            reg = self.env.registry
            merged = getattr(reg, "lookalike_groups", None)
            if callable(merged):
                self._groups = merged()       # kernel defaults + file-level groups, built once per registry
                return self._groups
            groups = copy.deepcopy(DEFAULT_LOOKS_LIKE)
            for name, spec in sorted(getattr(reg, "looks_like", {}).items()):
                base = groups.setdefault(name, {"descriptor": spec.get("descriptor", name.replace("_", " ")),
                                                "resolve": spec.get("resolve", "inspect"), "members": []})
                base["descriptor"] = spec.get("descriptor", base["descriptor"])
                base["resolve"] = spec.get("resolve", base["resolve"])
                for key in ("kind", "candidates", "label_fields", "portion_descriptor", "color", "size_m", "size_class"):
                    if key in spec:
                        base[key] = copy.deepcopy(spec[key])
                for member in spec.get("members", []):
                    if member not in base["members"]:
                        base["members"].append(member)
            self._groups = groups
        return self._groups

    def group_of(self, record):
        explicit = record.get("looks_like")
        groups = self.groups()
        if isinstance(explicit, str) and explicit in groups:
            return explicit, groups[explicit]
        is_a = record.get("is_a") or ()
        try:
            key = (record.get("type"), tuple(is_a), record.get("category"))
            found = self._group_cache.get(key, MISSING)
        except TypeError:
            key, found = None, MISSING
        if found is MISSING:
            found = None
            perc = self.tiers(record)
            if perc.get("_looks_like") in groups:
                found = perc["_looks_like"]
            else:
                keys = [record.get("type")] + list(is_a) + [record.get("category")]
                for name, spec in groups.items():
                    members = spec.get("members", [])
                    if any(k in members for k in keys if k and isinstance(k, str)):
                        # a group needs at least two plausible members present in the registry or explicit tagging;
                        # single-member groups (keys, socks, remotes) confuse instances of the same kind
                        found = name
                        break
            if key is not None:
                self._group_cache[key] = found
        return (found, groups[found]) if found is not None else (None, None)

    def _group_types(self, name):
        """Registry types realistic perception puts in group `name` (registry.lookalike_types)."""
        index = getattr(self.env.registry, "lookalike_types", None)
        return list(index().get(name, ())) if callable(index) else []

    def group_glance(self, name, group):
        """What every unresolved member of a look-alike group shows at a glance: {"color", "size_m", "size_class"}
        from the group's own keys, else the most common member color and the typical (per-dimension median) member
        size; MISSING where the registry knows no member type (the record's own value is shown then)."""
        found = self._common.get(name)
        if found is None:
            reg = self.env.registry
            types = self._group_types(name)
            props = [reg.resolve(t) for t in types] if types else []
            if "color" in group:
                color = group["color"]
            elif props:
                color = _most_common([p.get("color") for p in props])
            else:
                color = MISSING
            if isinstance(group.get("size_m"), list):
                size = [float(v) for v in group["size_m"][:3]]
            else:
                sizes = [p.get("size_m") for p in props]
                size = _median_size(sizes) if props else None
                size = MISSING if size is None else size
            found = {"color": color, "size_m": size, "size_class": group.get("size_class"),
                     "explicit": {k for k in ("color", "size_m") if k in group}}
            self._common[name] = found
        return found

    def _type_default(self, record, field):
        """The record's type default for a field (MISSING for unknown types or fields)."""
        reg = self.env.registry
        type_name = record.get("type")
        try:
            if type_name and hasattr(reg, "known") and reg.known(type_name):
                return reg.resolve(type_name).get(field, MISSING)
        except Exception:  # noqa: BLE001
            pass
        return MISSING

    def _shared_words(self, name, group):
        """Words of type/category/label/ancestors that every member of group `name` has (they tell no member apart)."""
        found = self._shared.get(name)
        if found is None:
            reg = self.env.registry
            sets = []
            types = set(self._group_types(name))
            for t in sorted(types):
                props = reg.resolve(t)
                chain = reg.ancestors(t) if hasattr(reg, "ancestors") else []
                label = str(props.get("label") or "") if props.get("label") not in (None, "object") else t.replace("_", " ")
                sets.append(_identity_words([t, props.get("category")] + label.split() + list(chain)))
            for member in group.get("members", []):
                if member not in types and not (hasattr(reg, "known") and reg.known(member)):
                    sets.append(_identity_words([member] + member.split("_")))
            found = set.intersection(*sets) if sets else set()
            self._shared[name] = found
        return found

    def tiers(self, record):
        type_name = record.get("type") or "?"
        cached = self._tiers.get(type_name)
        if cached is not None:
            return cached
        table = {}
        for tier in ("glance", "inspect", "read"):
            for field in DEFAULT_TIERS[tier]:
                table[field] = tier
        for field, tool in DEFAULT_TIERS["measure"].items():
            table[field] = "measure"
            table["_tool:" + field] = tool
        for field in DEFAULT_TIERS["hidden"]:
            table[field] = "never"
        reg = self.env.registry
        # domain-wide field tiers (file-level "field_tiers"): fields a domain writes onto any entity
        for spec in getattr(reg, "field_tiers", []):
            self._apply_tier_spec(table, spec)
        chain = []
        if type_name in getattr(reg, "types", {}):
            chain = list(reversed([type_name] + reg.ancestors(type_name)))
        explicit = set()
        for name in chain:
            spec = getattr(reg, "perception", {}).get(name) or {}
            explicit |= self._apply_tier_spec(table, spec)
            if spec.get("looks_like"):
                table["_looks_like"] = spec["looks_like"]
        table["_explicit"] = explicit
        # fields that never take the plain glance path of perceive_record (own renderings or render kinds)
        table["_slow"] = set(SPECIAL_FIELDS) | set(WORD_BUCKETS) | {k[6:] for k in table if k.startswith("_kind:")}
        self._tiers[type_name] = table
        return table

    @staticmethod
    def _apply_tier_spec(table, spec):
        """Apply {"glance"|"inspect"|"read"|"hidden": [fields], "measure": {field: tool}, "touch": [fields],
        "count": [fields]} to a tier table; dotted names ("mech.slams", "buttons.*.holds_n", "leaves.healthy") are
        sub-field tiers of dict/list fields ("*" matches any key or list position). "count" renders an integer as a
        count (a few/several/many at a glance, exact up to 12 on inspection); it is glance-tier unless the spec names
        another tier for it. Returns the fields named."""
        named = set()
        for tier in ("glance", "inspect", "read"):
            for field in spec.get(tier, []):
                table[field] = tier
                named.add(field)
        for field in spec.get("hidden", []):
            table[field] = "never"
            named.add(field)
        measure = spec.get("measure", {})
        for field in (measure if isinstance(measure, dict) else {f: None for f in measure}):
            table[field] = "measure"
            named.add(field)
            if isinstance(measure, dict) and measure[field]:
                table["_tool:" + field] = measure[field]
        for field in spec.get("touch", []):
            table["_touch:" + field] = True
        for field in spec.get("count", []):
            table["_kind:" + field] = "count"
            if field not in named:
                table[field] = "glance"
                named.add(field)
        table.pop("_sub", None)
        return named

    def tier(self, record, field):
        table = self.tiers(record)
        if field.startswith("_"):
            return "never"
        own = record.get("_perception")
        if isinstance(own, dict):
            # per-entity tiers (a domain marking fields it wrote onto this particular entity)
            for tier_name in ("glance", "inspect", "read"):
                if field in own.get(tier_name, ()):
                    return tier_name
            if field in own.get("hidden", ()):
                return "never"
            if field in (own.get("measure") or {}):
                return "measure"
            if field in own.get("count", ()):
                return "glance"
        if field in (record.get("secret_fields") or []) and field not in table.get("_explicit", ()):
            return "never"     # an explicit type tier overrides secret_fields (oracle hides, realistic perceives)
        found = table.get(field)
        if found is not None:
            return found
        if field.endswith(HIDDEN_SUFFIXES) or field.startswith(HIDDEN_PREFIXES):
            return "never"
        return "inspect"

    def _tool(self, record, field):
        own = record.get("_perception")
        if isinstance(own, dict) and isinstance(own.get("measure"), dict) and own["measure"].get(field):
            return own["measure"][field]
        return self.tiers(record).get("_tool:" + field)

    def _kind(self, record, field):
        own = record.get("_perception")
        if isinstance(own, dict) and field in own.get("count", ()):
            return "count"
        return self.tiers(record).get("_kind:" + field)

    def _sub_rules(self, record, field):
        """Rules for parts of a dict/list field: [(path pattern, tier, render kind, dotted name)] from dotted names in
        the type tables and the entity's own _perception (those first). Empty for most fields."""
        table = self.tiers(record)
        subs = table.get("_sub")
        if subs is None:
            subs = {}
            for name, tier in table.items():
                if isinstance(name, str) and "." in name and not name.startswith("_") and isinstance(tier, str):
                    head, _, rest = name.partition(".")
                    subs.setdefault(head, []).append((tuple(rest.split(".")), tier, table.get("_kind:" + name), name))
            table["_sub"] = subs
        rules = subs.get(field) or ()
        own = record.get("_perception")
        if isinstance(own, dict):
            prefix = field + "."
            extra = []
            for tier_name, tier in (("glance", "glance"), ("inspect", "inspect"), ("read", "read"), ("hidden", "never"),
                                    ("measure", "measure"), ("count", "glance")):
                for name in own.get(tier_name) or ():
                    if isinstance(name, str) and name.startswith(prefix):
                        extra.append((tuple(name[len(prefix):].split(".")), tier,
                                      "count" if tier_name == "count" else None, name))
            if extra:
                return extra + list(rules)
        return rules

    def subfield_hidden(self, record, field):
        """Dotted sub-fields of a dict field declared hidden ("mech.slams") -> set of hidden top-level keys (type
        tables and the entity's own _perception). Rendering uses _sub_rules, which also handles wildcards and
        nested levels."""
        return {rule[0][0] for rule in self._sub_rules(record, field) if rule[1] == "never" and len(rule[0]) == 1}

    # ----- observation context ---------------------------------------------------------------------------
    def room_context(self, state):
        """Viewpoint-wide part of the observation context (cached per world version)."""
        from .perception import DARK, flashlight_on, light_level
        if getattr(self, "_room_ctx", None) is not None:
            return self._room_ctx          # inside one observe()/filter call
        robot = state["robot"]
        rid = robot["room"]
        light = light_level(state, rid)
        if flashlight_on(state):
            light = max(light, 0.5)
        room = state["rooms"].get(rid, {})
        m = 1.0
        if light < DARK * 1.5:
            m += 1.5
        elif light < 0.35:
            m += 0.5
        clean = _num(robot.get("camera_clean"), 1.0)
        if clean < 0.7:
            m += 1.5 * (0.7 - clean) / 0.3
        if robot.get("speed_mode") == "fast":
            m += 1.0
        if _num(room.get("steam"), 0) > 0.3 or _num(room.get("smoke"), 0) > 0.2 or _num(room.get("smoke_density"), 0) > 0.2:
            m += 1.0
        if room.get("glare") or self._glare(state, rid):
            m += 0.5
        key = "{}:{}:{}:{}".format(tuple(robot.get("pos") or (0, 0)), int(light * 4), int(clean * 4), robot.get("speed_mode", "normal"))
        return {"light": light, "m": m, "key": key, "rid": rid, "pos": tuple(robot.get("pos") or (0, 0))}

    def context(self, state, eid=None, record=None):
        base = self.room_context(state)
        dist = 0
        if record is not None:
            room, cells = geometry.anchor(state, eid)
            if cells and room == base["rid"]:
                dist = min(geometry.chebyshev(base["pos"], c) for c in cells)
            elif room != base["rid"]:
                dist = 6
        return {"dist": dist, "light": base["light"], "m": base["m"] + 0.12 * max(0, dist - 1), "key": base["key"]}

    def _glare(self, state, rid):
        from . import clock
        try:
            hour = clock.hour(state)
        except Exception:  # noqa: BLE001
            return False
        if not (7 <= hour <= 9 or 16 <= hour <= 18):
            return False
        if clock.daylight(state) <= 0.4:
            return False
        windows = self.env.world.static_index("realism.windows", lambda r: r.get("category") == "window")             if hasattr(self.env.world, "static_index") else [e for e, r in state["entities"].items() if r.get("category") == "window"]
        return any(state["entities"].get(e, {}).get("location") == rid and _num(state["entities"][e].get("covered"), 0.0) < 0.3
                   for e in windows)

    def _rng(self, *parts):
        return _FastRng("{}|{}".format(self.env.seed, "|".join(str(p) for p in parts)))

    def _noisy(self, value, sigma, eid, field, level, ctx, digits=2, lo=None, hi=None):
        """One fixed estimate per (entity, field, knowledge level): walking around or waiting does not redraw it, so
        estimates cannot be averaged. Worse conditions only coarsen how precisely it is read."""
        rng = self._rng(eid, field, level)
        out = value + rng.gauss(0.0, sigma)
        step = (10.0 ** -digits) * max(1.0, round(ctx["m"] * 2.0) / 2.0) ** 2
        out = round(out / step) * step
        if lo is not None:
            out = max(lo, out)
        if hi is not None:
            out = min(hi, out)
        return round(out, digits)

    def _bucketed(self, value, scheme, eid, field, level, ctx):
        edges, names = scheme
        index = names.index(_bucket(value, edges, names))
        p = min(0.45, 0.07 * (ctx["m"] - 1.0))
        if p <= 0:
            return names[index]
        rng = self._rng("bucket", eid, field, level)       # a fixed draw: a slip shows up only in worse conditions
        if rng.random() < p:
            index = max(0, min(len(names) - 1, index + (1 if rng.random() < 0.5 else -1)))
        return names[index]

    # ----- value rendering -------------------------------------------------------------------------------
    def render(self, eid, record, field, value, known, ctx, thermal=True):
        """Perceived value of one field at the robot's knowledge level, or MISSING."""
        tier = self.tier(record, field)
        if tier == "never":
            return MISSING
        need = TIER_NEED[tier]
        if field in self.tiers(record).get("_explicit", ()) and tier in ("read", "glance", "inspect") and field in SPECIAL_FIELDS \
                and field not in ("soil", "wetness", "fill_level", "level", "temperature_c"):
            # the type says how this is perceived (a phone screen shows battery percent; a label prints the weight)
            if known < need:
                return MISSING
            return copy.deepcopy(value)
        if field == "soil" and isinstance(value, dict):
            levels = {k: _num(v, 0.0) for k, v in value.items() if _num(v) is not None}
            if known >= INSPECT:
                return {k: self._bucketed(v, SOIL6, eid, "soil." + k, INSPECT, ctx) for k, v in sorted(levels.items()) if v >= 0.02}
            if ctx["light"] < 0.2:
                return MISSING
            return {"overall": self._bucketed(max(levels.values() or [0.0]), SOIL4, eid, "soil", GLANCE, ctx)}
        if field in ("wetness", "moisture") and _num(value) is not None:
            v = _num(value)
            if known >= MEASURE and need >= MEASURE or (field == "wetness" and known >= MEASURE):
                return round(v, 2)
            if known >= TOUCH:
                return self._noisy(v, 0.04, eid, field, TOUCH, ctx, 2, 0.0, 1.0)
            if known >= INSPECT:
                return self._bucketed(v, WET3, eid, field, INSPECT, ctx)
            if ctx["light"] < 0.2:
                return MISSING
            return "wet" if v >= 0.3 else "looks dry"
        if field in ("temperature_c", "internal_temp_c", "core_temp_c") and _num(value) is not None:
            v = _num(value)
            if known >= MEASURE:
                return self._noisy(v, 0.3, eid, field, MEASURE, ctx, 1)
            if known >= TOUCH or (thermal and field == "temperature_c"):
                return self._bucketed(v, TEMP6, eid, field, max(known, GLANCE), ctx)
            if v >= 90.0:
                return "very hot"
            return MISSING
        if tier == "measure" and field.endswith("_temp_c") and _num(value) is not None and known >= MEASURE and \
                "thermometer" in str(self._tool(record, field) or "thermometer"):
            # any thermometer reading (liquid_temp_c, reservoir_temp_c...) has the thermometer's fixed-draw error
            return self._noisy(_num(value), 0.3, eid, field, MEASURE, ctx, 1)
        if field in ("fill_level", "level", "liquid_l", "contents", "liquid") and self.sealed(record) and known < TOUCH                 and record.get(field) == value:
            return MISSING      # a closed opaque container does not show how full it is or what is inside
        if field in PROVENANCE_FIELDS and record.get("contents_from") and value not in (None, "", [], {}) and \
                value == record.get(field):
            shown = self._provenance(eid, record, field)
            if shown is not None:
                return shown if known >= need else MISSING
        if field in ("fill_level", "level") and _num(value) is not None and 0.0 <= _num(value) <= 1.5:
            v = _num(value)
            if known >= MEASURE:
                return round(v, 2)
            if known >= INSPECT:
                return round(self._noisy(v, 0.04, eid, field, INSPECT, ctx, 3, 0.0, 1.5) * 20) / 20.0
            return self._bucketed(v, FILL5, eid, field, GLANCE, ctx)
        if field == "liquid_l" and _num(value) is not None:
            v = _num(value)
            cap = _num(record.get("capacity_l"), 0.0) or _num(record.get("volume_l"), 0.0) or _inner_volume_l(record)
            if known >= MEASURE:
                return round(v, 2)
            if v <= 0.0:
                return 0.0 if known >= INSPECT else "empty"
            if known >= INSPECT:
                return round(self._noisy(v, 0.08 * (cap or v), eid, field, INSPECT, ctx, 2, 0.0), 1)
            if not cap:
                return "some liquid" if v < 1.0 else "a lot of liquid"
            return self._bucketed(v / cap, FILL5, eid, field, GLANCE, ctx)
        if field in ("mass_kg", "weight_kg") and _num(value) is not None:
            v = _num(value)
            liquid = _num(record.get("liquid_l"), 0.0)
            if liquid > 0.0:
                # what the hand or the scale feels includes the liquid inside (as core carry-mass counts it)
                v += liquid * _num(record.get("liquid_density"), 1.0)
            if known >= MEASURE:
                return round(v, 2)
            if known >= TOUCH:
                return self._bucketed(v, MASS4, eid, field, TOUCH, ctx)
            return MISSING
        if field == "size_m" and isinstance(value, list):
            if known >= MEASURE:
                return copy.deepcopy(value)
            rng = self._rng(eid, field, GLANCE)
            out = []
            for v in value[:3]:
                n = _num(v, 0.1)
                # a camera estimates sizes to about 5 cm (finer only with a tape measure)
                est = n * (1.0 + rng.gauss(0.0, 0.08 * ctx["m"]))
                out.append(max(0.05, round(est * 20.0) / 20.0))
            return out
        if field == "count" and isinstance(value, int) and not isinstance(value, bool):
            return self._count(value, known, eid, field, ctx)
        if field == "freshness" and _num(value) is not None:
            if not _perishable(record):
                return MISSING      # freshness is a food property (every entity carries the default 1.0)
            if known >= INSPECT:
                return self._bucketed(_num(value), FRESH4, eid, field, INSPECT, ctx)
            return MISSING
        if field in ("brightness", "dim_level") and _num(value) is not None:
            return self._bucketed(_num(value), BRIGHT4, eid, field, GLANCE, ctx) if known < MEASURE else value
        if field in ("battery_pct", "charge", "charge_pct") and _num(value) is not None:
            v = _num(value)
            frac = v / 100.0 if v > 1.0 else v
            if known >= MEASURE:
                return round(v, 1)
            if record.get("indicator") or record.get("display") or record.get("category") in INDICATOR_CATEGORIES:
                return self._bucketed(frac, CHARGE4, eid, field, GLANCE, ctx)
            return MISSING
        if field == "on":
            if known >= INSPECT or record.get("indicator") or record.get("display") or record.get("lit") or \
                    record.get("category") in INDICATOR_CATEGORIES:
                return value
            return MISSING
        if isinstance(value, int) and not isinstance(value, bool) and self._kind(record, field) == "count":
            return self._count(value, known, eid, field, ctx) if known >= need else MISSING
        # generic tier logic
        if tier == "measure":
            if known >= MEASURE:
                if isinstance(value, (dict, list)):
                    return self._render_struct(eid, record, field, value, known, ctx)
                n = _num(value)
                return round(n, 2) if n is not None and isinstance(value, float) else copy.deepcopy(value)
            return MISSING
        if known < need:
            return MISSING
        if field in WORD_BUCKETS and isinstance(value, dict):
            return self._word_buckets(eid, record, field, value, known, ctx)
        if isinstance(value, (dict, list)):
            return self._render_struct(eid, record, field, value, known, ctx)
        if tier == "glance" and known < MEASURE and field.endswith("_m") and (isinstance(value, float) or
                                                                         (isinstance(value, list) and value and all(isinstance(v, float) for v in value))):
            return _visual_estimate(value, self._rng(eid, field, GLANCE))      # a camera judges lengths to ~5 cm
        if tier in ("glance", "read") or isinstance(value, (bool, int, str)) or value is None:
            return copy.deepcopy(value)       # printed numbers are exact once read
        if known < MEASURE and field.endswith("_m") and isinstance(value, list) and value and all(isinstance(v, float) for v in value):
            return _visual_estimate(value, self._rng(eid, field, known))
        if isinstance(value, float):
            if known >= MEASURE or value == 0.0:
                return round(value, 3)
            lo = 0.0 if value >= 0 else None
            hi = 1.0 if 0.0 <= value <= 1.0 and (_fraction_field(field) or self._unit_default(record, field)) else None
            return self._noisy(value, 0.04 * max(abs(value), 0.05), eid, field, known, ctx, 3, lo, hi)
        return copy.deepcopy(value)

    def _count(self, value, known, eid, field, ctx):
        """A count: a few/several/many at a glance; exact up to 12 on inspection (noisy above); exact when counted."""
        if known >= MEASURE or (known >= INSPECT and value <= 12):
            return value
        if known >= INSPECT:
            return int(round(self._noisy(value, 0.08 * value, eid, field, INSPECT, ctx, 0, 0)))
        return _bucket(value, *COUNT3)

    def _unit_default(self, record, field):
        """True when the field's type default is a number in [0, 1] (an amount or condition fraction)."""
        type_name = record.get("type")
        key = (type_name, field)
        found = self._unit_cache.get(key)
        if found is None:
            reg = self.env.registry
            default = None
            try:
                if type_name and hasattr(reg, "known") and reg.known(type_name):
                    default = reg.resolve(type_name).get(field)
            except Exception:  # noqa: BLE001
                default = None
            found = isinstance(default, float) and 0.0 <= default <= 1.0
            self._unit_cache[key] = found
        return found

    def _word_buckets(self, eid, record, field, value, known, ctx):
        """{kind: amount} fields (odor, damage) as words: faint/noticeable/strong, light/moderate/severe. Amounts below
        2 % are not perceptible; non-numeric entries pass as they are; hidden sub-fields are dropped."""
        edges, names = WORD_BUCKETS[field]
        rules = self._sub_rules(record, field)
        hidden = {rule[0][0] for rule in rules if rule[1] == "never" and len(rule[0]) == 1}
        wild = any(rule[1] == "never" and rule[0] == ("*",) for rule in rules)
        out = {}
        for key in sorted(value, key=str):
            if str(key).startswith("_") or str(key) in hidden or wild:
                continue
            amount = value[key]
            n = _num(amount)
            if n is None:
                out[key] = _strip_private(amount)
            elif n >= 0.02:
                out[key] = self._bucketed(n, (edges, names), eid, field + "." + str(key), INSPECT, ctx)
        return out

    def _render_struct(self, eid, record, field, value, known, ctx):
        """A dict/list field without underscore keys, with its dotted sub-field rules applied (hidden parts dropped,
        parts of a higher tier dropped until known, counts and estimates rendered)."""
        rules = self._sub_rules(record, field)
        if not rules:
            return _strip_private(value)
        return self._walk(eid, record, field, value, (), rules, known, ctx)

    def _walk(self, eid, record, field, value, path, rules, known, ctx):
        if isinstance(value, dict):
            items, out = [(k, v, str(k)) for k, v in value.items() if not str(k).startswith("_")], {}
        elif isinstance(value, list):
            items, out = [(i, v, str(i)) for i, v in enumerate(value)], []
        else:
            return copy.deepcopy(value)
        for key, item, name in items:
            sub = path + (name,)
            rule, deeper = None, False
            for pattern, tier, kind, full in rules:
                if len(pattern) < len(sub) or not all(p == "*" or p == s for p, s in zip(pattern, sub)):
                    continue
                if len(pattern) > len(sub):
                    deeper = True
                elif rule is None or pattern.count("*") < rule[0].count("*"):
                    rule = (pattern, tier, kind, full)     # the most specific matching rule wins
            if rule is not None:
                shown = self._render_part(eid, record, field, sub, rule, item, known, ctx, rules)
            elif deeper:
                shown = self._walk(eid, record, field, item, sub, rules, known, ctx)
            else:
                shown = _strip_private(item)
            if shown is MISSING:
                continue
            if isinstance(out, dict):
                out[key] = shown
            else:
                out.append(shown)
        return out

    def _render_part(self, eid, record, field, sub, rule, value, known, ctx, rules):
        """One part of a dict/list field under a dotted rule ("buttons.*.holds_n": inspect, "hopper.kg": measure)."""
        _pattern, tier, kind, _full = rule
        if tier == "never":
            return MISSING
        name = field + "." + ".".join(sub)
        if eid in self.knowledge.data:
            known = max(known, self.knowledge.level(eid, record, name))      # dotted grants ("hopper.kg")
        if known < TIER_NEED.get(tier, INSPECT):
            return MISSING
        if isinstance(value, (dict, list)):
            return self._walk(eid, record, field, value, sub, rules, known, ctx)
        if kind == "count" and isinstance(value, int) and not isinstance(value, bool):
            return self._count(value, known, eid, name, ctx)
        if tier == "measure":
            n = _num(value)
            return round(n, 2) if n is not None and isinstance(value, float) else copy.deepcopy(value)
        if isinstance(value, float) and tier == "inspect" and known < MEASURE and value != 0.0:
            lo = 0.0 if value >= 0 else None
            hi = 1.0 if 0.0 <= value <= 1.0 and _fraction_field(sub[-1]) else None
            return self._noisy(value, 0.04 * max(abs(value), 0.05), eid, name, known, ctx, 3, lo, hi)
        return copy.deepcopy(value)

    def _provenance(self, eid, record, field):
        """contents_from: a destination's liquid/contents/product while the source it came from is unresolved for the
        robot (and the destination itself carries no label the robot has read): the source group's portion
        descriptor, else "unidentified liquid/contents from <descriptor>". None when the contents are known."""
        source = record.get("contents_from")
        if not isinstance(source, str) or source == eid:
            return None
        if any(record.get(k) for k in ("label_text", "text", "name_label")) and \
                self.knowledge.entity_level(eid, record) >= READ:
            return None          # its own label, read, says what it holds
        if not self.unresolved(source):
            return None
        src = self.env.world.s["entities"].get(source)
        if src is not None:
            _name, group = self.group_of(src)
        else:
            group = self.groups().get((self.gone.get(source) or {}).get("group") or "")
        what = "liquid" if field == "liquid" else "contents"
        if group:
            return group.get("portion_descriptor") or "unidentified {} from {}".format(what, group.get("descriptor") or "a container")
        return "unidentified " + what

    # ----- records ---------------------------------------------------------------------------------------
    def perceive_record(self, state, eid, record, ctx=None, level_floor=GLANCE, dark=False):
        ctx = ctx or self.context(state, eid, record)
        out = {}
        if dark:
            return {"label": "dark shape", "kind": "unknown", "size_class": size_class(record),
                    "location": record.get("location"), "pos": copy.deepcopy(record.get("pos"))}
        thermal = "thermal" in getattr(self.env, "sensors", ())
        table = self.tiers(record)
        slow = table["_slow"]
        own = record.get("_perception")
        # per-entity tiers and secret_fields need the full tier rule; otherwise the type table (plus name patterns)
        per_entity = isinstance(own, dict) or bool(record.get("secret_fields"))
        own_count = own.get("count") or () if isinstance(own, dict) else ()
        has_knowledge = eid in self.knowledge.data
        hooks = self._value_hooks()
        for field, value in record.items():
            if field in SKIP_FIELDS or field[0] == "_":
                continue
            if per_entity:
                tier = self.tier(record, field)
            else:
                tier = table.get(field)
                if tier is None:
                    tier = "never" if field.endswith(HIDDEN_SUFFIXES) or field.startswith(HIDDEN_PREFIXES) else "inspect"
            if tier == "never":
                continue
            if hooks is not None:
                alt = self._hooked(hooks, state, eid, record, field)
                if alt is not None:
                    value = alt          # a domain's perceivable part of the value (e.g. soil on visible parts only)
            if tier == "glance" and field not in slow and field not in own_count:
                if isinstance(value, (dict, list)):
                    rules = self._sub_rules(record, field)
                    if rules:
                        known = max(level_floor, self.knowledge.level(eid, record, field)) if has_knowledge else level_floor
                        out[field] = self._walk(eid, record, field, value, (), rules, known, ctx)
                    else:
                        out[field] = _strip_private(value)
                else:
                    out[field] = value
                continue
            known = max(level_floor, self.knowledge.level(eid, record, field)) if has_knowledge else level_floor
            if has_knowledge and known < INSPECT and level_floor == GLANCE:
                remembered = self._remembered(eid, record, field, value, ctx, thermal)
                if remembered is not MISSING:
                    out[field] = remembered
                    continue
            if tier == "inspect" and known < INSPECT and field not in SPECIAL_FIELDS:
                continue
            if tier == "read" and known < READ:
                continue
            shown = self.render(eid, record, field, value, known, ctx, thermal=thermal)
            if shown is not MISSING:
                out[field] = shown
        out["size_class"] = size_class(record)
        level = max(level_floor, self.knowledge.entity_level(eid, record))
        ident, gname, group, portion = self._identify(eid, record, level)
        out.update(ident)
        # private things render exactly like anything else (hiding their text or contents would itself tell them
        # apart): privacy is a rule about acting -- reading, inspecting, opening, taking -- enforced by ctx.forbid
        if _unidentified(ident, record):
            # an unresolved look-alike (or unfamiliar thing) shows only what every member of its group shows, so
            # the set of fields cannot give its identity away -- plus what the robot itself felt or measured
            felt = {k for k in out if has_knowledge and self.knowledge.level(eid, record, k) >= TOUCH and self.tier(record, k) == "measure"}
            out = {k: v for k, v in out.items() if k in LOOKALIKE_FIELDS or k in felt}
            if group is not None and not portion:
                self._group_values(eid, record, out, gname, group, ctx, felt, always=True)
        return dict(sorted(out.items()))     # field order must not reveal the type

    def _group_values(self, eid, record, out, name, group, ctx, felt=(), always=False):
        """An unresolved look-alike shows its group's color and size (group keys, else the common member values)
        instead of the ones its type gives it -- a color or size this instance was given itself (a red sock among
        same-type socks) is what the camera sees and stays, and so does a size the robot measured. always=False: only
        keys already in `out`."""
        common = self.group_glance(name, group)

        def typed(field):
            # the value comes from the member's type (so it could tell the type apart), or the group says how it looks
            own = record.get(field, MISSING)
            return field in common["explicit"] or own is MISSING or own == self._type_default(record, field)
        if common["color"] is not MISSING and (always or "color" in out) and typed("color"):
            out["color"] = copy.deepcopy(common["color"])
        size = common["size_m"]
        if size is MISSING or "size_m" in felt or not typed("size_m") or \
                (eid in self.knowledge.data and self.knowledge.level(eid, record, "size_m") >= MEASURE):
            return
        if "size_m" in out:
            shown = self.render(eid, record, "size_m", list(size), GLANCE, ctx)
            if shown is not MISSING:
                out["size_m"] = shown
        if always or "size_class" in out:
            out["size_class"] = common["size_class"] or size_class({"size_m": size})

    def _value_hooks(self):
        """@hook("perceivable_value") functions: (generic, {field: [fns]}) or None. A function may carry a `fields`
        attribute (names it applies to) so it is not called for every field."""
        from .plugins import HOOKS
        fns = HOOKS.get("perceivable_value")
        if not fns:
            return None
        cache = self._hook_cache
        if cache is None or cache[0] is not fns or cache[1] != len(fns) or cache[2] is not fns[-1][2]:
            generic, by_field = [], {}
            for _, _, fn in fns:
                fields = getattr(fn, "fields", None)
                if fields:
                    for name in ([fields] if isinstance(fields, str) else fields):
                        by_field.setdefault(name, []).append(fn)
                else:
                    generic.append(fn)
            cache = self._hook_cache = (fns, len(fns), fns[-1][2], generic, by_field)
        return cache[3], cache[4]

    @staticmethod
    def _hooked(hooks, state, eid, record, field):
        generic, by_field = hooks
        for fn in by_field.get(field, ()):
            value = fn(state, eid, record, field)
            if value is not None:
                return value
        for fn in generic:
            value = fn(state, eid, record, field)
            if value is not None:
                return value
        return None

    def _remembered(self, eid, record, field, value, ctx, thermal):
        """For a field whose learned value has since changed unseen: the remembered rendering, unless the change is
        visible at a glance (then the live glance rendering is shown by the normal path). MISSING otherwise."""
        old = self.knowledge.stale(eid, field)
        if old is None or old[0] < INSPECT:
            return MISSING
        level, remembered = old
        now_glance = self.render(eid, record, field, value, GLANCE, ctx, thermal=thermal)
        then_glance = self.render(eid, dict(record, **{field: remembered}), field, remembered, GLANCE, ctx, thermal=thermal)
        if now_glance != then_glance:
            return MISSING
        return self.render(eid, dict(record, **{field: remembered}), field, remembered, level, ctx, thermal=thermal)

    def identity(self, eid, record, level):
        """Perceived label/kind: the true label unless the object is novel or in an unresolved look-alike group."""
        return self._identify(eid, record, level)[0]

    def _identify(self, eid, record, level):
        """(identity dict, look-alike group name, group spec, is a portion of that group) at entity knowledge `level`.

        A portion (derived_from: a package, bottle or whole it was taken from) is identified when its source is (at
        the source's own knowledge level) or by its own label / an inspect-resolved look at it -- reading a label-less
        portion identifies nothing. Unresolved, it shows the group's portion descriptor (default "contents of
        <descriptor>"); resolved, its own label and category."""
        if record.get("novel"):
            return self._novel(record, level), None, None, False
        name, group = self.group_of(record)
        if group:
            if self._resolved(group, record, level):
                return self._own(record), name, group, False
            return self._unresolved(name, group, record, level, False), name, group, False
        source = record.get("derived_from")
        if isinstance(source, str) and source != eid:
            src = self.env.world.s["entities"].get(source)
            if src is not None:
                if src.get("novel"):
                    return self._novel(record, level), None, None, True
                sname, sgroup = self.group_of(src)
                if sgroup:
                    if self._resolved(sgroup, src, self.knowledge.entity_level(source, src)) or self._resolved(sgroup, record, level):
                        return self._own(record), sname, sgroup, True
                    return self._unresolved(sname, sgroup, record, level, True), sname, sgroup, True
            else:
                gone = self.gone.get(source)
                if gone and gone.get("unresolved"):
                    if gone.get("novel"):
                        return self._novel(record, level), None, None, True
                    sgroup = self.groups().get(gone.get("group") or "")
                    if sgroup and not self._resolved(sgroup, record, level):
                        return self._unresolved(gone["group"], sgroup, record, level, True), gone["group"], sgroup, True
        return self._own(record), None, None, False

    @staticmethod
    def _own(record):
        return {"label": record.get("label"), "kind": record.get("category") or "object"}

    @staticmethod
    def _novel(record, level):
        out = {"label": "unfamiliar {} object".format(size_class(record)), "kind": "unknown", "shape": _shape(record)}
        if level >= INSPECT and record.get("material"):
            out["material_guess"] = record.get("material")
        return out

    def _labeled(self, group, record):
        """Whether reading the record can identify it: a non-empty label field (the group's label_fields, else any
        read-tier field that is not a content field, plus label_text/text/name_label)."""
        blank = ("label_text" in record or "text" in record) and not any(record.get(k) for k in ("label_text", "text", "name_label"))
        if group.get("label_fields"):
            label_fields = group["label_fields"]
        elif blank:
            label_fields = []          # a blank label prints nothing: the item stays unidentified after reading
        else:
            content = _content_fields()
            label_fields = [f for f in record if f not in content and f not in ("tags",) and self.tier(record, f) == "read"] \
                + ["label_text", "text", "name_label"]
        return any(record.get(k) for k in label_fields)

    def _resolved(self, group, record, level):
        mode = group.get("resolve", "inspect")
        resolve = 99 if mode == "never" else LEVELS.get(mode, INSPECT)
        if level < resolve:
            return False
        return resolve <= INSPECT or mode == "inspect" or self._labeled(group, record)

    def _unresolved(self, name, group, record, level, portion):
        # the group's kind (never a member's own category, which could tell members apart)
        cands = sorted(group.get("candidates") or {m.replace("_", " ") for m in group.get("members", [])}) or [record.get("label")]
        kind = group.get("kind") or name.replace("_", " ")
        mode = group.get("resolve", "inspect")
        resolve = 99 if mode == "never" else LEVELS.get(mode, INSPECT)
        label = group.get("descriptor")
        if portion:
            label = group.get("portion_descriptor") or "contents of {}".format(label or "a container")
        if mode == "never" or (resolve >= READ and level >= READ and not (mode == "inspect" or self._labeled(group, record))):
            return {"label": label, "kind": "unidentified " + kind, "candidates": cands}
        return {"label": label, "kind": kind, "candidates": cands}

    def unresolved(self, eid, record=None):
        """True when the robot's current knowledge leaves eid unidentified: an unresolved look-alike (identity has
        candidates), an unidentified derived_from portion, or a novel object. False for unknown ids (a destroyed
        thing answers as it was when it went)."""
        if record is None:
            record = self.env.world.s["entities"].get(eid)
            if record is None:
                return bool((self.gone.get(eid) or {}).get("unresolved"))
        if record.get("novel"):
            return True
        return _unidentified(self._identify(eid, record, self.knowledge.entity_level(eid, record))[0], record)

    def sealed(self, record):
        """A closed opaque container (lidded bin, closed jar, sealed box): its fill and contents are not visible."""
        see_through = record.get("transparent") or str(record.get("material") or "") in ("glass", "clear_plastic", "acrylic")
        return bool(record.get("openable")) and not record.get("open") and not see_through

    _sealed = sealed

    # ----- small-object detection ------------------------------------------------------------------------
    def missed(self, state, eid, record, ctx):
        if record.get("location") == "robot" or record.get("relation") == "held":
            return False
        size = record.get("size_m") or [0.1, 0.1, 0.1]
        try:
            largest = max(float(v) for v in size[:3])
        except (TypeError, ValueError):
            largest = 0.1
        if largest >= 0.15 or ctx["dist"] <= 1:
            return False
        if self.knowledge.entity_level(eid, record) >= INSPECT:
            return False
        parent = record.get("location")
        prec = state["entities"].get(parent)
        if prec is not None and self.knowledge.entity_level(parent, prec) >= INSPECT:
            entry = self.knowledge.data.get(parent, {})
            if entry.get("fields", {}).get("_contents") == [INSPECT, _digest(sorted(self._children(state, parent)))]:
                return False
        clutter = len(self._children(state, parent)) if prec is not None else 0
        p = 0.04 + 0.05 * max(0, ctx["dist"] - 1) + 0.06 * max(0, clutter - 3)
        if ctx["light"] < 0.35:
            p += 0.25
        if largest < 0.05:
            p += 0.1
        p = min(0.85, p * (1.0 + 0.2 * (ctx["m"] - 1.0)))
        rng = self._rng("detect", eid, tuple(state["robot"].get("pos") or (0, 0)))
        return rng.random() < p

    def _chain(self, state, eid):
        out, current = [], eid
        while current in state["entities"] and len(out) < 12:
            out.append(current)
            current = state["entities"][current].get("location")
        return out

    def _children(self, state, eid):
        world = self.env.world
        try:
            return world.children(eid)
        except Exception:  # noqa: BLE001
            return [k for k, e in state["entities"].items() if e.get("location") == eid]

    # ----- knowledge grants (from handlers, applied when the step succeeds) ------------------------------
    def grant(self, eid, level, fields=None):
        level = LEVELS.get(level, level) if isinstance(level, str) else int(level)
        self.pending.append((eid, level, tuple(fields) if fields is not None else None))

    def begin_step(self):
        """Called by the engine before an action: remember what the robot holds (knowledge of things the robot
        itself carries or sets down survives the move) and drop any stray grants."""
        self.pending = []
        state = self.env.world.s
        self._held_before = {v for v in (state["robot"].get("hands") or {}).values() if v}
        self._ever.update(state["entities"])      # ids of things this step destroys still get translated
        self._step_gone = {}

    def _step_changes(self, ok):
        """From the world journal of the step in progress (before it is committed): remember ids created and
        destroyed during the step, what the robot knew of destroyed look-alikes / novel things / portions, and keep
        knowledge of things the robot itself displaced (a "displaced" marker set by this step: it bumped them)."""
        world = self.env.world
        journal = getattr(world, "_journal", None)
        if not isinstance(journal, dict) or not journal:
            return
        entities = world.s["entities"]
        for key, before in list(journal.items()):
            if not (isinstance(key, tuple) and len(key) == 2 and key[0] == "entities"):
                continue
            eid = key[1]
            self._ever.add(eid)
            after = entities.get(eid)
            if not isinstance(before, dict):
                continue
            if after is None:
                self._step_gone[eid] = before
                if before.get("novel") or before.get("derived_from") or self.group_of(before)[1] is not None:
                    ident, gname, _group, _portion = self._identify(eid, before, self.knowledge.entity_level(eid, before))
                    self.gone[eid] = {"group": gname, "unresolved": _unidentified(ident, before),
                                      "novel": bool(before.get("novel")) or (ident.get("kind") == "unknown" and "shape" in ident)}
            elif ok and after.get("displaced") is True and before.get("displaced") is not True:
                entry = self.knowledge.data.get(eid)
                if entry is not None and entry["sig"] == self.knowledge.signature(before):
                    entry["sig"] = self.knowledge.signature(after)      # the robot knows it pushed it there

    def commit_grants(self, ok):
        grants, self.pending = self.pending, []
        state = self.env.world.s
        if ok:
            held_now = {v for v in (state["robot"].get("hands") or {}).values() if v}
            for eid in sorted(getattr(self, "_held_before", set()) | held_now):
                record = state["entities"].get(eid)
                entry = self.knowledge.data.get(eid)
                if record is not None and entry is not None:
                    entry["sig"] = self.knowledge.signature(record)     # it moved with the robot, not unseen
        self._held_before = set()
        self._step_changes(ok)
        if not ok:
            return
        for eid, level, fields in grants:
            record = state["entities"].get(eid)
            if record is None:
                continue
            if fields is None and level in (INSPECT, READ):
                # a close look or a read raises what it can reveal: read-tier text (read) and visible detail
                # (inspect); never touch- or measure-gated values
                names = [f for f in record if not f.startswith("_") and self.tier(record, f) != "measure"]
                inspect_names = [f for f in names if self.tier(record, f) != "read"]
                self.knowledge.grant(eid, record, INSPECT, inspect_names)
                if level == READ:
                    self.knowledge.grant(eid, record, READ, [f for f in names if self.tier(record, f) == "read"])
                entry = self.knowledge.data.get(eid)
                if entry is not None:
                    entry["level"] = max(entry.get("level", GLANCE), level)
            else:
                self.knowledge.grant(eid, record, level, list(fields) if fields is not None else None)
            if level >= INSPECT and fields is None:
                # inspecting a surface/container finds the small things on/in it
                entry = self.knowledge.data.get(eid)
                if entry is not None:
                    entry["fields"]["_contents"] = [INSPECT, _digest(sorted(self._children(state, eid)))]

    # ----- translation -----------------------------------------------------------------------------------
    def _plain_ids(self, state):
        """Ids shown as they are: rooms, people, pets and the robot -- except people and pets that are only shapes in
        the robot's dark room (they get their dark-spell handle instead)."""
        if getattr(self, "_plain_now", None) is not None:
            return self._plain_now
        plain = set(state.get("rooms", {})) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
        dark = self._dark_agents(state)
        return plain - set(dark) if dark else plain

    def _dark_agents(self, state):
        """{person/pet id: shape handle} for the people and pets in the robot's room while it is dark for the robot.
        The handle is issued per dark spell (a new spell starts whenever the robot's view of a room turns dark: it
        entered a dark room, or the light went out), so a shape can be tracked while it stays dark but not recognised
        in a later spell."""
        if self._dark_now is not None:
            return self._dark_now
        robot = state.get("robot") or {}
        rid = robot.get("room")
        dark = bool(rid in state.get("rooms", {}) and not self.env.perception.lit(state, rid))
        view = (rid, dark)
        if dark and self._last_view != view:
            self._spell += 1
        self._last_view = view
        if not dark:
            return {}
        out = {}
        for group in ("people", "pets"):
            for aid, agent in sorted((state.get(group) or {}).items()):
                if agent.get("location") == rid and aid not in out:
                    out[aid] = self.handles.handle("shape:{}:{}".format(aid, self._spell))
        return out

    def _type_stems(self):
        """Lower-case registry type names: an id-shaped word built on one ("fabric_softener_1") is what a generated id
        looks like, so free text neutralises it whether or not such a thing exists."""
        found = getattr(self, "_stems", None)
        if found is None:
            found = self._stems = frozenset(str(t).lower() for t in getattr(self.env.registry, "types", {}) or ())
        return found

    def _resolve_handle(self, text, state, dark):
        eid = self.handles.resolve(text)
        if isinstance(eid, str) and eid.startswith("shape:"):
            pid = eid.split(":")[1]
            if dark.get(pid) == text:
                return pid          # a shape the robot sees now (people/pets in the dark are addressed by their shape)
            raise ActionError("That is not visible here", "perception")
        if eid is None or eid not in state["entities"]:
            raise ActionError("That is not visible here", "perception")
        return eid

    def inbound(self, raw, text_keys=(), entity_keys=()):
        """Agent arguments -> true ids. Handles (exact or inside strings such as predicate paths, any letter case)
        are translated. A true entity id (any letter case) in an entity-valued parameter is refused exactly like an
        invisible object; anywhere else (free text, keys, lists) it is neutralised to "something" -- and so is every
        word shaped like a generated id on a registry type name (fabric_softener_1, dishwasher_1_basket) whether or not
        such a thing exists, unless it names a part of the command's own target (a slot "layer_2") -- so guessing ids
        neither works nor tells anything (the echo of the agent's own text is the same either way). Rooms, people and
        pets keep their ids, but in the dark a person or pet named by id is refused (DARK_WHO): the robot sees shapes,
        addressed by their shape handle. The converted free-text arguments are remembered for outbound_message (the
        agent's own words are never rewritten as if they were the domain's)."""
        state = self.env.world.s
        dark = self._dark_agents(state)
        agents = {a.lower() for group in ("people", "pets") for a in (state.get(group) or {})}
        public = {r.lower() for r in state.get("rooms", {})} | agents | {"robot"}
        lowered = getattr(self, "_lower_ids", None)
        if lowered is None or lowered[0] != (len(state["entities"]), len(self._ever)):
            # ids of things destroyed earlier are neutralised too (free text must not map them to handles either)
            lowered = ((len(state["entities"]), len(self._ever)), {e.lower() for e in state["entities"]} | {e.lower() for e in self._ever})
            self._lower_ids = lowered
        entities = lowered[1]
        stems = self._type_stems()
        names = set()             # names of parts of the command's own targets (slot "layer_2", key "burner_1")

        def conv(value, entity_param=False):
            if isinstance(value, str):
                low_value = value.strip().lower()
                if HANDLE_RE.match(value.lower()):
                    return self._resolve_handle(value.lower(), state, dark)
                if entity_param and dark and low_value in agents:
                    raise ActionError(DARK_WHO, "perception")
                if entity_param and low_value in entities and low_value not in public:
                    raise ActionError("That is not visible here", "perception")

                def tok(match):
                    word = match.group(0)
                    low = word.lower()
                    if HANDLE_RE.match(low):
                        return self._resolve_handle(low, state, dark)
                    if low in public:
                        return word
                    if low in entities or (low not in names and _typed_id(low, stems)):
                        if entity_param:
                            raise ActionError("That is not visible here", "perception")
                        return "something"
                    return word
                return TOKEN_RE.sub(tok, value)
            if isinstance(value, list):
                return [conv(v, entity_param) for v in value]
            if isinstance(value, dict):
                return {conv(k) if isinstance(k, str) else k: conv(v, entity_param) for k, v in value.items()}
            return value
        out = {key: conv(value, True) for key, value in raw.items() if key in entity_keys}
        for value in out.values():
            if isinstance(value, str) and value in state["entities"]:
                names |= _record_names(state["entities"][value]) - entities
        out.update({key: conv(value, False) for key, value in raw.items() if key not in entity_keys})
        out = {key: out[key] for key in raw}
        said = getattr(self, "_agent_said", None)
        if said is None:
            said = self._agent_said = []
        for key, value in out.items():
            if key not in entity_keys and isinstance(value, str) and len(value.strip()) >= 3 and value not in said:
                said.append(value)          # text the agent wrote, possibly stored and shown back later (a label)
        del said[:-500]
        self._agent_text = tuple(sorted({v for k, v in out.items() if k not in entity_keys and isinstance(v, str)
                                         and len(v.strip()) >= 3 and v not in state["entities"]}, key=lambda s: (-len(s), s)))
        return out

    def out_text(self, text, visible=None, issue=True):
        """True ids in a string -> handles. With issue=False only already-issued or visible ids get handles and
        other entity ids become 'something' (sound/smell hints must not identify unseen things). People and pets that
        are shapes in the robot's dark room become their shape handle."""
        state = self.env.world.s
        entities = state["entities"]
        plain = self._plain_ids(state)
        ever, by_id = self._ever, self.handles.by_id
        dark = self._dark_agents(state)
        if dark:
            if text in dark:
                return dark[text]
            text = TOKEN_RE.sub(lambda m: dark.get(m.group(0), m.group(0)), text)

        if text in entities and text not in plain:
            if issue or text in by_id or (visible is not None and text in visible):
                return self.handles.handle(text)
            return "something"
        if "_" not in text and text not in entities and text not in ever:
            return text

        def tok(match):
            word = match.group(0)
            if word in entities:
                if word not in plain:
                    if issue or word in by_id or (visible is not None and word in visible):
                        return self.handles.handle(word)
                    return "something"
            elif (word in ever or word in by_id) and word not in plain:
                return by_id.get(word) or "something"       # destroyed: its handle if the robot had it, else nothing
            return word
        return TOKEN_RE.sub(tok, text)

    def out_obj(self, value, visible=None, issue=True):
        if isinstance(value, str):
            return self.out_text(value, visible, issue)
        if isinstance(value, list):
            return [self.out_obj(v, visible, issue) for v in value]
        if isinstance(value, dict):
            out, moved = {}, False
            for k, v in value.items():
                nk = self.out_text(k, visible, issue) if isinstance(k, str) else k
                moved = moved or nk != k
                out[nk] = v if k in DESCRIPTIVE_KEYS and isinstance(v, (str, list)) else self.out_obj(v, visible, issue)
            if moved and len(out) > 1:
                # an id-keyed dict must not list its entries in true-id order (that ranks look-alikes by type)
                out = dict(sorted(out.items(), key=lambda kv: str(kv[0])))
            return out
        return value

    def to_handles(self, value):
        """Privileged plan arguments (true ids) -> handles (used by the task runner)."""
        state = self.env.world.s
        entities, plain = state["entities"], self._plain_ids(state)
        if isinstance(value, str):
            if value in entities and value not in plain:
                return self.handles.handle(value)
            return TOKEN_RE.sub(lambda m: self.handles.handle(m.group(0)) if m.group(0) in entities and m.group(0) not in plain else m.group(0), value)
        if isinstance(value, list):
            return [self.to_handles(v) for v in value]
        if isinstance(value, dict):
            return {self.to_handles(k) if isinstance(k, str) else k: self.to_handles(v) for k, v in value.items()}
        return value

    def from_handles(self, value):
        if isinstance(value, str):
            return TOKEN_RE.sub(lambda m: self.handles.resolve(m.group(0)) or m.group(0) if HANDLE_RE.match(m.group(0)) else m.group(0), value)
        if isinstance(value, list):
            return [self.from_handles(v) for v in value]
        if isinstance(value, dict):
            return {self.from_handles(k) if isinstance(k, str) else k: self.from_handles(v) for k, v in value.items()}
        return value

    # ----- action results --------------------------------------------------------------------------------
    def filter_details(self, eid, details, level, exact=()):
        """Render an inspect/read result dict for `eid` at `level` (fields of the entity record follow their tiers;
        domain inspector extras are inspect-tier unless the type says otherwise). Keys in `exact` or listed in
        details["_perceived"] are instrument readings or percepts the domain already rendered: passed through.
        Grants made earlier in the same step (e.g. a thermometer reading) already count."""
        state = self.env.world.s
        record = state["entities"].get(eid)
        if record is None:
            return details
        details = dict(details)
        exact = set(exact) | set(details.pop("_perceived", ()) or ())
        pending = {}
        for geid, glevel, gfields in self.pending:
            if geid == eid:
                for f in (gfields or details.keys()):
                    pending[f] = max(pending.get(f, GLANCE), glevel)
        self._room_ctx = None
        ctx = self.context(state, eid, record)
        thermal = "thermal" in getattr(self.env, "sensors", ())
        hooks = self._value_hooks()
        out = {}
        for field, value in details.items():
            if field in ("object", "agent"):
                out[field] = value
                continue
            if field in exact:
                out[field] = value
                continue
            if field in ("label", "type"):
                continue
            if field == "contents" and not (record.get("contents_from") and "contents" in record and value == record["contents"]):
                out[field] = value          # the things on/in it (a substance named "contents" follows provenance)
                continue
            if field in ("under", "behind"):
                out[field] = value
                continue
            if hooks is not None and field in record and value == record[field]:
                alt = self._hooked(hooks, state, eid, record, field)
                if alt is not None:
                    value = alt
            known = max(level, self.knowledge.level(eid, record, field), pending.get(field, GLANCE))
            shown = self.render(eid, record, field, value, known, ctx, thermal=thermal)
            if shown is not MISSING:
                out[field] = shown
        ident, gname, group, portion = self._identify(eid, record, max(level, self.knowledge.entity_level(eid, record)))
        out.update(ident)
        if _unidentified(ident, record):
            # record fields are restricted like observations; a domain inspector's own percepts pass, except extras
            # whose name gives the true type away ("kettle_state" on an unfamiliar object) -- words every member of
            # the group shares ("food") tell nothing apart and do not count
            words = _identity_words([record.get("type"), record.get("category")] + str(record.get("label") or "").split()
                                    + list(record.get("is_a") or []))
            if group is not None:
                words -= self._shared_words(gname, group)
            keep = LOOKALIKE_FIELDS | {"object", "contents", "under", "behind"} | exact
            out = {k: v for k, v in out.items()
                   if k in keep or (k not in record and not any(str(w) in k for w in words))}
            if group is not None and not portion:
                self._group_values(eid, record, out, gname, group, ctx)
        return out

    def outbound_message(self, message, involved=()):
        """Translate a result message token by token: ids of things the robot perceives now or that the command
        named become handles, other entity ids become "something"; true labels of unresolved look-alikes or
        unfamiliar things named in the command become their descriptor."""
        if not isinstance(message, str):
            return message
        state = self.env.world.s
        entities, plain = state["entities"], self._plain_ids(state)
        dark = self._dark_agents(state)
        perception = self.env.perception
        involved = set(involved)
        ever, by_id = self._ever, self.handles.by_id
        sub = self._label_substitution(involved)
        # the agent's own free text echoed back (speak, explain, notes) is protected from the substitution of an
        # unfamiliar thing's true name: rewriting a word the agent wrote would tell it that word is the true name
        spans = [s for s in self._agent_text if s] if sub is not None else []
        span_re = re.compile("|".join(re.escape(s) for s in spans)) if spans else None

        def tok(m):
            word = m.group(0)
            if word in dark:
                return dark[word]
            if word in plain:
                return word
            if word not in entities:
                if word in ever or word in by_id:
                    # destroyed during the step (merged, consumed, pulled): its handle if issued, else "something"
                    return by_id.get(word) or ("something" if word not in involved else self.handles.handle(word))
                return word
            if word in involved or word in self.handles.by_id and perception.visible(state, word) or perception.visible(state, word):
                return self.handles.handle(word)
            return "something"

        said = set(getattr(self, "_agent_said", None) or ()) if sub is not None else set()

        def substitute(text, key):
            if text in said:
                return sub(text, key, protected=True)       # a whole text the agent wrote earlier (a label it wrote)
            if span_re is None:
                return sub(text, key)
            out, pos = [], 0
            for m in span_re.finditer(text):
                out.append(sub(text[pos:m.start()], key))
                out.append(sub(m.group(0), key, protected=True))
                pos = m.end()
            out.append(sub(text[pos:], key))
            return "".join(out)

        def conv(text, key=None, ids_only=False):
            if sub is not None and not ids_only:
                text = substitute(text, key)
            return TOKEN_RE.sub(tok, text)

        stripped = message.strip()
        if stripped[:1] in ("{", "["):
            try:
                data = json.loads(stripped)
            except ValueError:
                data = None
            if isinstance(data, (dict, list)):
                # JSON results: translated value by value and re-serialised with sorted (handle) keys, so neither
                # an id-keyed dict nor a list sorted by true id ranks look-alikes by their true type
                out = self._out_json(data, conv, None, entities, plain)
                return json.dumps(out, sort_keys=True, ensure_ascii=message.isascii())
        return conv(message)

    def _label_substitution(self, involved):
        """fn(text, json key, protected=False) replacing true names of unresolved look-alikes and unfamiliar things the
        command named by their perceived label, in one pass (an inserted descriptor is never re-scanned); the
        kernel's own identity values (perceived label, kind, candidates) are left as they are. None if nothing to do.

        For a look-alike the names are those of the whole group -- every member type's label and type name and every
        in-house member's own label -- so a generic word that happens to equal one member's name is rewritten for
        every member alike (messages stay byte-identical across the group). An unfamiliar thing has no group: its own
        label and type are rewritten, except inside the agent's own echoed words (protected=True), where rewriting
        would confirm a guessed name."""
        entities = self.env.world.s["entities"]
        step_gone = getattr(self, "_step_gone", {})
        group_names, novel_names, labels, kinds, cands = {}, {}, set(), set(), set()
        for eid in sorted(involved):
            rec = entities.get(eid) or step_gone.get(eid)
            if rec is None:
                continue
            ident, gname, _group, _portion = self._identify(eid, rec, self.knowledge.entity_level(eid, rec))
            if not _unidentified(ident, rec):
                continue
            shown = str(ident.get("label") or "object")
            labels.add(shown.lower())
            kind = str(ident.get("kind") or "")
            kinds.update({kind.lower(), kind.lower()[len("unidentified "):] if kind.startswith("unidentified ") else "unidentified " + kind.lower()})
            cands.update(str(c).lower() for c in ident.get("candidates") or ())
            own = [str(rec.get("label") or ""), str(rec.get("type") or "").replace("_", " "), str(rec.get("type") or "")]
            target = group_names if gname else novel_names
            for name in own + (sorted(self._group_names(gname)) if gname else []):
                if len(name) > 2 and name.lower() not in GENERIC_NAMES:
                    target.setdefault(name.lower(), shown)
        if not group_names and not novel_names:
            return None
        names = dict(novel_names)
        names.update(group_names)
        # phrases kept verbatim in free text: the perceived identity strings (unless one is itself a true name)
        keep = {p for p in labels | kinds | cands if p and p not in names}

        def compile_names(found):
            return re.compile(r"(?i)(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(p) for p in sorted(set(found) | keep, key=lambda s: (-len(s), s)))
                              + r")(?![A-Za-z0-9_])")
        pattern = compile_names(names)
        group_pattern = compile_names(group_names) if group_names else None

        def apply(text, key=None, protected=False):
            if key in IDENTITY_KEYS:
                low = text.lower()
                if (key == "kind" and low in kinds) or (key == "candidates" and low in cands) or \
                        (key in ("label", "descriptor") and low in labels):
                    return text       # the kernel's own rendering of an identity value
            if protected:
                if group_pattern is None:
                    return text
                return group_pattern.sub(lambda m: group_names.get(m.group(0).lower(), m.group(0)), text)
            return pattern.sub(lambda m: names.get(m.group(0).lower(), m.group(0)), text)
        return apply

    def _entity_group(self, state, record):
        """The look-alike group a record belongs to for the robot (a portion: its source's), or None (novel things
        and portions of novel things belong to none)."""
        if record is None or record.get("novel"):
            return None
        name, group = self.group_of(record)
        if group:
            return name
        source = record.get("derived_from")
        src = state["entities"].get(source) if isinstance(source, str) else None
        if src is not None and not src.get("novel"):
            sname, sgroup = self.group_of(src)
            return sname if sgroup else None
        return None

    def _group_names(self, name):
        """True names a message could use for any member of look-alike group `name`: labels and type names of its
        member types (and its listed members), plus the labels and types of the in-house things that belong to it."""
        base = self._names.get(name)
        if base is None:
            reg = self.env.registry
            found = set()
            for t in self._group_types(name):
                props = reg.resolve(t)
                for value in (props.get("label"), t.replace("_", " "), t):
                    if value:
                        found.add(str(value))
            for member in (self.groups().get(name) or {}).get("members", []):
                found.update({str(member), str(member).replace("_", " ")})
            base = self._names[name] = frozenset(found)
        out = set(base)
        state = self.env.world.s
        for rec in state["entities"].values():
            if self._entity_group(state, rec) == name:
                for value in (rec.get("label"), str(rec.get("type") or "").replace("_", " "), rec.get("type")):
                    if value:
                        out.add(str(value))
        return out

    def _out_json(self, value, conv, key, entities, plain):
        if isinstance(value, str):
            return conv(value, key)
        if isinstance(value, list):
            out = [self._out_json(v, conv, key, entities, plain) for v in value]
            return self._handle_order(value, out, key, entities, plain)
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                nk = conv(k, None, True) if isinstance(k, str) else k      # keys: ids only, never label rewrites
                base, n = nk, 2
                while nk in out:
                    nk = "{} ({})".format(base, n)       # two unseen things both became "something"
                    n += 1
                out[nk] = self._out_json(v, conv, k, entities, plain)
            return out
        return value

    def _handle_order(self, original, translated, key, entities, plain):
        """A list of entity ids (or of records with an id key) in true-id order is re-sorted by handle; lists under
        keys naming an order (order, sequence, path, stack...) keep theirs."""
        if len(original) < 2 or (isinstance(key, str) and any(w in key.lower() for w in ORDERED_KEY_WORDS)):
            return translated
        ids, out_ids = [], []
        for raw, done in zip(original, translated):
            if isinstance(raw, str):
                rid, tid = raw, done
            elif isinstance(raw, dict) and isinstance(done, dict):
                field = next((k for k in ID_KEYS if isinstance(raw.get(k), str)), None)
                if field is None:
                    return translated
                rid, tid = raw[field], done.get(field)
            else:
                return translated
            if rid in plain or not (rid in entities or rid in self._ever):
                return translated
            ids.append(rid)
            out_ids.append(str(tid))
        if ids != sorted(ids):
            return translated
        return [item for _, _, item in sorted(zip(out_ids, range(len(translated)), translated), key=lambda t: (t[0], t[1]))]

    REFUSAL_PATTERNS = (
        (re.compile(r"(?i)hazardous item \([^)]*\).*"), "Hazardous item: it needs its safe grip or protective gear"),
        (re.compile(r"(?i)explicit scoped human approval required.*"), "This needs explicit approval from an adult who is present"),
        (re.compile(r"(?i)(owner permission is required|this object is off limits|contained private item requires owner permission|contained item is off limits|private inspection requires permission).*"),
         "That needs the household's permission first"),
        (re.compile(r"(?i)contained item requires scoped approval.*"), "This needs explicit approval from an adult who is present"),
    )

    def scrub_refusal(self, message, category):
        """Refusals must not reveal hidden facts (privacy flags, risk levels, hazard classes, owners): generic
        wording, and permission-type refusals share one category."""
        text = message or ""
        for pattern, replacement in self.REFUSAL_PATTERNS:
            text = pattern.sub(replacement, text)
        if category in ("privacy", "authority") or "approval" in text.lower() or "permission" in text.lower():
            return text, "permission"
        return text, category

    # ----- observation -----------------------------------------------------------------------------------
    def observe(self):
        self._room_ctx = self._plain_now = self._dark_now = None
        state = self.env.world.s
        self._room_ctx = self.room_context(state)
        self._dark_now = self._dark_agents(state)
        self._plain_now = self._plain_ids(state)
        try:
            return self._observe()
        finally:
            self._room_ctx = self._plain_now = self._dark_now = None

    def _observe(self):
        env = self.env
        state = env.world.s
        base = env.perception.observe(env, records=False)   # oracle visibility, darkness, privacy, channels
        robot_room = state["robot"]["room"]
        visible = {}
        perceived_ids = set()
        dark = base.get("dark")
        missed, phantoms = set(), []
        for eid in sorted(base["visible_entities"], key=lambda e: len(self._chain(state, e))):
            record = state["entities"].get(eid)
            if record is None:
                continue
            ctx = self.context(state, eid, record)
            if record.get("location") in missed or (not dark and self.missed(state, eid, record, ctx)):
                missed.add(eid)
                continue
            base_rec = base["visible_entities"][eid]
            is_dark_shape = base_rec.get("label") == "dark shape"
            perceived_ids.add(eid)
            visible[eid] = self.perceive_record(state, eid, record, ctx, dark=is_dark_shape)
            for key, value in base_rec.items():
                if key != "label" and key not in visible[eid]:
                    visible[eid][key] = value      # observers' percepts (added to the record) are kept
            if record.get("category") == "mirror" or record.get("reflective"):
                if self.knowledge.entity_level(eid, record) < INSPECT and not dark:
                    # an un-inspected mirror looks like an opening onto this same room: listed with the room's
                    # connections, not as an object (inspecting it, via the "door" handle, shows what it is)
                    visible.pop(eid, None)
                    phantoms.append({"id": "conn_" + self.handles.handle(eid)[4:], "to": robot_room, "kind": "doorway",
                                     "width_m": round(float((record.get("size_m") or [0.8])[0]), 1), "cell": record.get("pos"),
                                     "door": eid})
        obs = base
        # doors: whether a door is locked is learned by trying it or looking closely, not from across the room
        for conn in obs.get("connections", []):
            door = conn.get("door")
            drec = state["entities"].get(door) if door else None
            if drec is not None and self.knowledge.level(door, drec, "locked") < INSPECT:
                conn.pop("locked", None)
        obs["connections"] = list(obs.get("connections", [])) + phantoms
        # listing order must not follow true ids: order by handle
        obs["visible_entities"] = {k: visible[k] for k in sorted(visible, key=lambda e: self.handles.handle(e))}
        hazards = []
        from .plugins import HOOKS
        hidden_kinds = HIDDEN_HAZARD_KINDS
        agents_true = {}
        for group_name in ("people", "pets"):
            agents_true.update({aid: (group_name, a) for aid, a in (state.get(group_name) or {}).items() if aid not in agents_true})
        for item in base["grid"]["hazards"]:
            src = item.get("source")
            if item.get("kind") in AGENT_LYING_KINDS and src in agents_true:
                shown = self._agent_hazard_kind(*agents_true[src])
                if shown is None:
                    continue         # a sleeper sitting up, or dozing standing: sleep is not visible (no marker)
                item = dict(item, kind=shown)
            elif item.get("kind") == "sleeping":
                item = dict(item, kind="lying")      # asleep vs resting is not visible from a distance
            if src in state["entities"] and src not in perceived_ids:
                continue
            srec = state["entities"].get(src) or {}
            if (srec.get("category") == "mirror" or srec.get("reflective")) and self.knowledge.entity_level(src, srec) < INSPECT:
                continue      # an un-inspected mirror still looks like an opening
            if item.get("kind") in hidden_kinds:
                continue
            vetoes = HOOKS.get("visible_floor_hazard", [])
            if vetoes and any(fn(state, robot_room, tuple(item["cell"]), item.get("kind"), src) is False for _, _, fn in vetoes):
                continue
            hazards.append(item)
        if not dark:
            # a pet resting on the floor gets the same marker as a sleeping one (geometry marks only sleep/lying)
            marked = {h.get("source") for h in hazards if h.get("kind") in ("lying", "sleeping", "person_down")}
            vetoes = HOOKS.get("visible_floor_hazard", [])
            for pid in sorted(obs.get("pets") or {}):
                pet = (state.get("pets") or {}).get(pid) or {}
                pos = pet.get("pos")
                if pet.get("posture") != "resting" or not pos or pet.get("location") != robot_room or pid in marked:
                    continue
                cell = [pos[0], pos[1]]
                if vetoes and any(fn(state, robot_room, tuple(cell), "lying", pid) is False for _, _, fn in vetoes):
                    continue
                hazards.append({"cell": cell, "kind": "lying", "source": pid})
        obs["grid"]["hazards"] = hazards
        if isinstance(obs.get("room"), dict):
            for key in ("floor_hazards", "wet_cells", "dirt_cells", "soil_cells"):
                obs["room"].pop(key, None)    # raw room bookkeeping; the perceived list is grid.hazards
        if dark:
            # in the dark people and pets are only shapes, keyed by their dark-spell handle (an id would say who is who)
            # and listed in handle order
            shapes = self._dark_now or {}
            for group_name, word in (("people", "person"), ("pets", "animal")):
                found = {shapes.get(pid, pid): {"label": word, "location": p.get("location"), "pos": p.get("pos")}
                         for pid, p in obs.get(group_name, {}).items()}
                obs[group_name] = {k: found[k] for k in sorted(found)}
        for pid, person in list(obs.get("people", {}).items()) if not dark else []:
            true = state["people"].get(pid, {})
            cue = MOOD_CUES.get(str(true.get("mood") or "neutral"))
            person.pop("mood", None)
            person.pop("awake", None)
            if cue:
                person["cues"] = sorted(set(person.get("cues") or []) | {cue})
            if true.get("posture") in ("sleeping", "lying"):
                person["posture"] = "lying"           # asleep or awake looks the same until inspected
                if person.get("activity") in ("sleeping", "resting", "lying"):
                    person["activity"] = "lying down"
            elif person.get("activity") in ("sleeping", "resting", "lying"):
                person.pop("activity", None)          # dozing in a chair looks like sitting idle (posture kept)
            abilities = person.pop("abilities", None)
            if isinstance(abilities, dict) and abilities.get("mobility") not in (None, "full"):
                person["mobility_aid"] = abilities.get("mobility")      # a cane or walker is visible; diagnoses are not
            if str(true.get("role") or "") in VISITOR_ROLES and not self.knowledge.data.get(pid, {}).get("level"):
                person["label"] = "unfamiliar person"
                person.pop("role", None)
        for pid, pet in list(obs.get("pets", {}).items()) if not dark else []:
            true = state["pets"].get(pid, {})
            pet.pop("mood", None)
            if true.get("posture") in ("sleeping", "lying", "resting"):
                pet["posture"] = "resting"
                if pet.get("activity") in ("sleeping", "resting"):
                    pet["activity"] = "resting"
            elif pet.get("activity") in ("sleeping", "resting"):
                pet.pop("activity", None)             # a pet dozing sitting up keeps its posture; sleep is not visible
        obs["holding"] = list(obs.get("holding", []))
        # channels and messages: ids of things not perceived become 'something'
        channels = {k: v for k, v in obs.items() if k not in ("visible_entities", "room", "connections", "robot", "holding",
                                                               "grid", "people", "pets", "room_id")}
        for key, value in channels.items():
            obs[key] = self.out_obj(value, visible=perceived_ids, issue=False)
        for key in ("visible_entities", "connections", "robot", "holding", "grid", "people", "pets"):
            obs[key] = self.out_obj(obs[key], visible=perceived_ids, issue=True)
        obs["room"] = self.out_obj(self.perceive_room(robot_room, obs.get("room") or {}), visible=perceived_ids, issue=False)
        obs["perception"] = "realistic"
        km = env._known_map.get(robot_room)
        if km is not None:
            env._known_map[robot_room] = self.out_obj(km, visible=perceived_ids, issue=True)
        if self.record_observations:
            self.observed.append(copy.deepcopy(obs))
        return obs

    @staticmethod
    def _agent_hazard_kind(group, agent):
        """The hazard kind a camera shows for a sleeping/lying/fallen marker of a person or pet: "person_down" for
        someone fallen, "lying" for anyone lying down (asleep or awake), None when the agent does not look like lying
        (a sleeper sitting up: sleep itself is not visible) or cannot be seen (concealed)."""
        if agent.get("concealed") and not agent.get("revealed"):
            return None
        posture = agent.get("posture")
        if posture == "fallen":
            return "person_down"
        if posture in LYING_POSTURES.get(group, ()):
            return "lying"
        return None

    def route_hazards(self, state, rid, avoid_kinds, ignore_sources=()):
        """Realistic route planning (geometry.find_path(hazard_filter=...), used by core move/approach/walk_to): the
        cells to keep out of for `avoid_kinds`, judged from what the robot perceives -- never from hidden truth -- and
        the avoid kinds left for path_blocked hooks. Returns (hook_kinds, cells).

          * nothing is avoided in a room that is dark for the robot (it sees no hazards there; people and pets stay
            obstacles as bodies);
          * "sleeping", "person_down" and "lying" all mean anyone who looks like lying down: their cell and the ring
            around it (asleep and awake look the same; a sleeper sitting up, a concealed agent or a seated person gets
            nothing), plus fallen people's markers;
          * object hazards count only when their source is visible, the kind is visible (no black ice, film, damp...)
            and no visible_floor_hazard hook vetoes them;
          * path_blocked hooks get the avoid list without the agent kinds (their rings come from the truth)."""
        kinds = [k for k in (avoid_kinds or ()) if isinstance(k, str)]
        lyingish = any(k in AGENT_LYING_KINDS for k in kinds)
        hook_kinds = tuple(k for k in kinds if k not in AGENT_LYING_KINDS)
        cells = set()
        rooms = state.get("rooms", {})
        if not kinds or rid not in rooms or not self.env.perception.lit(state, rid):
            return hook_kinds, cells
        room = rooms[rid]
        # the approach target's own sources, plus sources a motion in progress plans without (navigation_robot's
        # robot field "_nav_plan_exclude", honoured by its path_blocked hook as well)
        ignore = set(ignore_sources or ()) | set((state.get("robot") or {}).get("_nav_plan_exclude") or ())
        agents = {}
        for group in ("people", "pets"):
            for aid, agent in (state.get(group) or {}).items():
                if agent.get("location") == rid and aid not in agents:
                    agents[aid] = (group, agent)
        from .plugins import HOOKS
        vetoes = HOOKS.get("visible_floor_hazard", [])
        visible = {}
        for cell, items in geometry.hazard_cells(state, rid).items():
            for kind, src in items:
                if src in ignore or kind in HIDDEN_HAZARD_KINDS:
                    continue
                if src in agents:
                    shown = self._agent_hazard_kind(*agents[src]) if kind in AGENT_LYING_KINDS else \
                        (None if agents[src][1].get("concealed") and not agents[src][1].get("revealed") else kind)
                elif src in state["entities"]:
                    if src not in visible:
                        srec = state["entities"][src]
                        visible[src] = self.env.perception.visible(state, src) and not (
                            (srec.get("category") == "mirror" or srec.get("reflective")) and self.knowledge.entity_level(src, srec) < INSPECT)
                    shown = kind if visible[src] else None
                else:
                    shown = "lying" if kind == "sleeping" else kind
                if shown is None:
                    continue
                if not (shown in kinds or (lyingish and shown in ("lying", "person_down"))):
                    continue
                if vetoes and any(fn(state, rid, tuple(cell), shown, src) is False for _, _, fn in vetoes):
                    continue
                cells.add(tuple(cell))
        if lyingish:
            for aid, (group, agent) in sorted(agents.items()):
                if aid in ignore or not agent.get("pos") or self._agent_hazard_kind(group, agent) != "lying":
                    continue
                cells |= geometry.near_cells([tuple(agent["pos"])], room, 1)
        return hook_kinds, cells

    def perceive_room(self, rid, room):
        """Room fields as perceived: ROOM_GLANCE fields plus domain field_tiers "room" glance fields as they are;
        air temperature and humidity as the robot's onboard sensing gives them (buckets); the rest hidden."""
        extra = set()
        hidden = set()
        for spec in getattr(self.env.registry, "field_tiers", []):
            extra |= set((spec.get("room") or {}).get("glance", []))
            hidden |= set((spec.get("room") or {}).get("hidden", []))
        out = {}
        raw = self.env.world.s["rooms"].get(rid, {})
        for key, value in room.items():
            if key in hidden or str(key).startswith("_"):
                continue
            if key not in ROOM_GLANCE and (key not in raw or raw.get(key) != value) and key not in ("floor_hazards", "wet_cells"):
                out[key] = _strip_private(value)      # an observer already rendered (or added) this percept
                continue
            if key in ROOM_GLANCE or key in extra:
                out[key] = _strip_private(value)
            elif key == "temperature_c" and _num(value) is not None:
                out[key] = self.render_room(rid, key, value)
            elif key == "humidity" and _num(value) is not None:
                out[key] = _bucket(_num(value), (0.3, 0.6), ("dry", "comfortable", "humid"))
        if _num(out.get("light_level")) is not None:
            out["light_level"] = _bucket(_num(out["light_level"]), (0.05, 0.2, 0.5, 0.8), ("dark", "dim", "moderate", "bright", "very bright"))
        return out

    def render_room(self, rid, field, value):
        """A room-level value as the robot senses it (temperature: onboard thermometer to 0.5 C)."""
        if field == "temperature_c" and _num(value) is not None:
            return round(_num(value) * 2.0) / 2.0
        if field == "humidity" and _num(value) is not None:
            return _bucket(_num(value), (0.3, 0.6), ("dry", "comfortable", "humid"))
        return MISSING

    def invalidate(self):
        self._room_ctx = None

    # ----- persistence -----------------------------------------------------------------------------------
    def dump(self):
        out = {"handles": self.handles.dump(), "knowledge": self.knowledge.dump()}
        if self.gone:
            out["gone"] = copy.deepcopy(self.gone)
        if self._spell or self._last_view is not None:
            out["dark"] = {"spell": self._spell, "view": list(self._last_view) if self._last_view is not None else None}
        if getattr(self, "_agent_said", None):
            out["said"] = list(self._agent_said)
        return out

    def load(self, data):
        self.handles.load((data or {}).get("handles"))
        self.knowledge.load((data or {}).get("knowledge"))
        self.gone = copy.deepcopy((data or {}).get("gone") or {})
        dark = (data or {}).get("dark") or {}
        self._spell = int(dark.get("spell") or 0)
        self._last_view = tuple(dark["view"]) if dark.get("view") else None
        self._agent_said = list((data or {}).get("said") or [])
        self._ever = set(self.env.world.s["entities"]) | {e for e in self.handles.by_id if not e.startswith("shape:")} | set(self.gone)
        self.invalidate()


class _FastRng:
    """Deterministic uniform/gauss stream from a string key (blake2b; no global state, no hash randomization)."""
    __slots__ = ("key", "n")

    def __init__(self, key):
        self.key, self.n = key, 0

    def random(self):
        self.n += 1
        digest = hashlib.blake2b("{}#{}".format(self.key, self.n).encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") / 18446744073709551616.0

    def gauss(self, mu, sigma):
        u1 = max(self.random(), 1e-12)
        u2 = self.random()
        return mu + sigma * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def read_tier_fields(registry, record):
    """Fields legible by reading for this record (kernel defaults, domain field_tiers and the type's perception
    blocks), usable in both perception modes."""
    fields = set(DEFAULT_TIERS["read"])
    # domain-wide field_tiers are deliberately not used here: printed text belongs to the object's type -- or to
    # this entity, when a domain wrote legible text onto it and said so in its own _perception "read" list
    type_name = record.get("type")
    if type_name in getattr(registry, "types", {}):
        for name in [type_name] + registry.ancestors(type_name):
            fields |= set((getattr(registry, "perception", {}).get(name) or {}).get("read", []))
    own = record.get("_perception")
    if isinstance(own, dict):
        fields |= {f for f in own.get("read") or () if isinstance(f, str) and "." not in f}
    return fields


def _visual_estimate(value, rng):
    def one(v):
        return max(0.0, round(v * (1.0 + rng.gauss(0.0, 0.06)) * 20.0) / 20.0) if v else 0.0
    return [one(v) for v in value] if isinstance(value, list) else one(value)


def _strip_private(value, hidden=()):
    """Deep copy without underscore keys (at every level) and without the given top-level hidden keys."""
    if isinstance(value, dict):
        return {k: _strip_private(v) for k, v in value.items() if not str(k).startswith("_") and k not in hidden}
    if isinstance(value, list):
        return [_strip_private(v) for v in value]
    return copy.deepcopy(value)


ROOM_GLANCE = {"label", "type", "floor", "size_m", "ceiling_m", "floor_material", "terrain", "outdoor", "exits", "private",
               "off_limits", "grid", "light_level", "lit", "slope_deg", "shared", "guest", "escape_zone", "fenced", "blocked"}


def _inner_volume_l(record):
    inner = record.get("inner_size_m")
    try:
        return float(inner[0]) * float(inner[1]) * float(inner[2]) * 1000.0 if inner else 0.0
    except (TypeError, ValueError, IndexError):
        return 0.0


def _fraction_field(field):
    return field.endswith(("_level", "_fill", "_fraction", "_frac", "_ratio")) or field in ("level", "fill_level", "battery_level", "bag_fill") \
        or field in UNIT_FIELDS


def _unidentified(ident, record):
    """An identity (Realism.identity) that leaves the thing unidentified: candidates, or novel / unknown kind."""
    return "candidates" in ident or bool(record.get("novel")) or (ident.get("kind") == "unknown" and "shape" in ident)


def _content_fields():
    """CONTENT_FIELDS plus systems/core.py's list when core is loaded (never imported from here)."""
    import sys
    core = sys.modules.get("house_sim.systems.core")
    extra = getattr(core, "CONTENT_FIELDS", None) if core is not None else None
    return CONTENT_FIELDS | set(extra) if extra else CONTENT_FIELDS


def _identity_words(words):
    """Words (and whole type/category names) longer than two characters: what a domain extra's key may contain."""
    return {str(w) for w in words if w and len(str(w)) > 2}


def _most_common(values):
    """The most common value (ties: the first in the given order)."""
    counts, first = {}, {}
    for i, value in enumerate(values):
        key = json.dumps(value, sort_keys=True, default=str)
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, (i, value))
    best = max(counts, key=lambda k: (counts[k], -first[k][0]))
    return copy.deepcopy(first[best][1])


def _median_size(sizes):
    """Per-dimension median of [x, y, z] sizes (the most common size when most members share one), or None."""
    valid = []
    for size in sizes:
        try:
            dims = [float(v) for v in size[:3]]
        except (TypeError, ValueError, IndexError):
            continue
        if len(dims) == 3:
            valid.append(dims)
    if not valid:
        return None
    out = []
    for d in range(3):
        column = sorted(v[d] for v in valid)
        out.append(column[(len(column) - 1) // 2])     # lower median: always an actual member's value
    return out


PERISHABLE_WORDS = ("food", "produce", "fruit", "vegetable", "meat", "dairy", "bread", "leftover", "milk", "egg", "cheese",
                    "fish", "herb", "flower", "plant", "perishable", "ingredient", "drink", "beverage", "juice")


def _perishable(record):
    words = " ".join(str(x) for x in [record.get("category"), record.get("type")] + list(record.get("is_a") or []))
    return any(w in words for w in PERISHABLE_WORDS) or "perishable" in (record.get("tags") or [])


def _shape(record):
    size = sorted((_num(v, 0.1) for v in (record.get("size_m") or [0.1, 0.1, 0.1])[:3]), reverse=True)
    if size[0] > 3 * size[1]:
        return "long and thin"
    if size[2] < 0.25 * size[1]:
        return "flat"
    if abs(size[0] - size[2]) < 0.2 * size[0]:
        return "roughly cubic or round"
    return "boxy"
