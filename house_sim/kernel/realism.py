"""Realistic (imperfect) symbolic perception: what a camera-and-sensors robot could plausibly know.

HouseEnv(..., perception="realistic") routes every observation, action message and action argument through this
module; perception="oracle" (the default, used by the verification pipeline) is untouched.

  * Opaque handles: entities are exposed as per-episode handles ("obj_7f3a19c2" = short hash of seed and true id).
    Arguments must use handles (true entity ids are refused as "not visible"); results and observations are
    translated back. Room, person and pet ids stay readable (the robot knows the house and its residents).
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
               "stream_ok", "in_stream", "relined_dirty", "filled_s", "stale", "potable", "derived_from"],
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
            raw = record.get(name)
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
        current = record.get(field)
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

    # ----- registry-driven tables ------------------------------------------------------------------------
    def groups(self):
        if self._groups is None:
            groups = copy.deepcopy(DEFAULT_LOOKS_LIKE)
            reg = self.env.registry
            for name, spec in sorted(getattr(reg, "looks_like", {}).items()):
                base = groups.setdefault(name, {"descriptor": spec.get("descriptor", name.replace("_", " ")),
                                                "resolve": spec.get("resolve", "inspect"), "members": []})
                base["descriptor"] = spec.get("descriptor", base["descriptor"])
                base["resolve"] = spec.get("resolve", base["resolve"])
                for key in ("kind", "candidates", "label_fields"):
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
        perc = self.tiers(record)
        if perc.get("_looks_like") in groups:
            return perc["_looks_like"], groups[perc["_looks_like"]]
        keys = [record.get("type")] + list(record.get("is_a") or []) + [record.get("category")]
        for name, spec in groups.items():
            members = spec.get("members", [])
            if any(k in members for k in keys if k):
                # a group needs at least two plausible members present in the registry or explicit tagging;
                # single-member groups (keys, socks, remotes) confuse instances of the same kind
                return name, spec
        return None, None

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
        self._tiers[type_name] = table
        return table

    @staticmethod
    def _apply_tier_spec(table, spec):
        """Apply {"glance"|"inspect"|"read"|"hidden": [fields], "measure": {field: tool}, "touch": [fields]} to a
        tier table; dotted names ("mech.slams") are sub-field tiers of dict fields. Returns the fields named."""
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
        if field in (record.get("secret_fields") or []) and field not in table.get("_explicit", ()):
            return "never"     # an explicit type tier overrides secret_fields (oracle hides, realistic perceives)
        found = table.get(field)
        if found is not None:
            return found
        if field.endswith(HIDDEN_SUFFIXES) or field.startswith(HIDDEN_PREFIXES):
            return "never"
        return "inspect"

    def subfield_hidden(self, record, field):
        """Dotted sub-fields of a dict field declared hidden ("mech.slams") -> set of hidden keys."""
        table = self.tiers(record)
        prefix = field + "."
        return {k[len(prefix):] for k, v in table.items() if isinstance(k, str) and k.startswith(prefix) and v == "never"}

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
        if field in ("fill_level", "level", "liquid_l", "contents", "liquid") and self._sealed(record) and known < TOUCH                 and record.get(field) == value:
            return MISSING      # a closed opaque container does not show how full it is or what is inside
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
            if known >= MEASURE or (known >= INSPECT and value <= 12):
                return value
            if known >= INSPECT:
                return int(round(self._noisy(value, 0.08 * value, eid, field, INSPECT, ctx, 0, 0)))
            return _bucket(value, *COUNT3)
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
        # generic tier logic
        if tier == "measure":
            if known >= MEASURE:
                n = _num(value)
                return round(n, 2) if n is not None and isinstance(value, float) else copy.deepcopy(value)
            return MISSING
        if known < need:
            return MISSING
        if isinstance(value, (dict, list)):
            hidden = self.subfield_hidden(record, field) if isinstance(value, dict) else ()
            return _strip_private(value, hidden)
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
            hi = 1.0 if 0.0 <= value <= 1.0 and _fraction_field(field) else None
            return self._noisy(value, 0.04 * max(abs(value), 0.05), eid, field, known, ctx, 3, lo, hi)
        return copy.deepcopy(value)

    # ----- records ---------------------------------------------------------------------------------------
    def perceive_record(self, state, eid, record, ctx=None, level_floor=GLANCE, dark=False):
        ctx = ctx or self.context(state, eid, record)
        out = {}
        if dark:
            return {"label": "dark shape", "kind": "unknown", "size_class": size_class(record),
                    "location": record.get("location"), "pos": copy.deepcopy(record.get("pos"))}
        thermal = "thermal" in getattr(self.env, "sensors", ())
        table = self.tiers(record)
        secret = record.get("secret_fields") or ()
        has_knowledge = eid in self.knowledge.data
        for field, value in record.items():
            if field in SKIP_FIELDS or field[0] == "_" or field in secret:
                continue
            tier = table.get(field, "inspect")
            if tier == "never":
                continue
            if tier == "glance" and field not in SPECIAL_FIELDS:
                if isinstance(value, (dict, list)):
                    hidden = self.subfield_hidden(record, field) if isinstance(value, dict) else ()
                    out[field] = _strip_private(value, hidden)
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
        ident = self.identity(eid, record, level)
        out.update(ident)
        if record.get("private") and "touch:" + eid not in state.get("approvals", []):
            # private things look like anything else; only their printed text and contents stay unread
            out = {k: v for k, v in out.items() if self.tier(record, k) != "read" and k != "contents"}
        if "candidates" in ident or record.get("novel"):
            # an unresolved look-alike (or unfamiliar thing) shows only what every member of its group shows, so
            # the set of fields cannot give its identity away -- plus what the robot itself felt or measured
            felt = {k for k in out if has_knowledge and self.knowledge.level(eid, record, k) >= TOUCH and self.tier(record, k) == "measure"}
            out = {k: v for k, v in out.items() if k in LOOKALIKE_FIELDS or k in felt}
        return dict(sorted(out.items()))     # field order must not reveal the type

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
        if record.get("novel"):
            out = {"label": "unfamiliar {} object".format(size_class(record)), "kind": "unknown", "shape": _shape(record)}
            if level >= INSPECT and record.get("material"):
                out["material_guess"] = record.get("material")
            return out
        name, group = self.group_of(record)
        source = record.get("derived_from")
        if not group and isinstance(source, str) and source in self.env.world.s["entities"]:
            # a portion taken from a package looks like the package until that package is identified
            src = self.env.world.s["entities"][source]
            name, group = self.group_of(src)
            if group:
                level = min(level, self.knowledge.entity_level(source, src)) if level < READ else level
                record = src
        if group:
            mode = group.get("resolve", "inspect")
            resolve = 99 if mode == "never" else LEVELS.get(mode, INSPECT)
            blank = ("label_text" in record or "text" in record) and not any(record.get(k) for k in ("label_text", "text", "name_label"))
            if group.get("label_fields"):
                label_fields = group["label_fields"]
            elif blank:
                label_fields = []          # a blank label prints nothing: the item stays unidentified after reading
            else:
                label_fields = [f for f in record if self.tier(record, f) == "read" and f not in ("tags",)] + ["label_text", "text", "name_label"]
            labeled = any(record.get(k) for k in label_fields) or mode == "inspect"
            resolved = level >= resolve and (labeled or resolve <= INSPECT)
            if not resolved:
                # the group's kind (never a member's own category, which could tell members apart)
                cands = sorted(group.get("candidates") or {m.replace("_", " ") for m in group.get("members", [])}) or [record.get("label")]
                kind = group.get("kind") or name.replace("_", " ")
                if mode == "never" or (resolve >= READ and level >= READ and not labeled):
                    return {"label": group.get("descriptor"), "kind": "unidentified " + kind, "candidates": cands}
                return {"label": group.get("descriptor"), "kind": kind, "candidates": cands}
        return {"label": record.get("label"), "kind": record.get("category") or "object"}

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

    def _sealed(self, record):
        """A closed opaque container (lidded bin, closed jar, sealed box): its fill and contents are not visible."""
        see_through = record.get("transparent") or str(record.get("material") or "") in ("glass", "clear_plastic", "acrylic")
        return bool(record.get("openable")) and not record.get("open") and not see_through

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
        self._held_before = {v for v in (self.env.world.s["robot"].get("hands") or {}).values() if v}

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
        if getattr(self, "_plain_now", None) is not None:
            return self._plain_now
        return set(state.get("rooms", {})) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}

    def inbound(self, raw, text_keys=(), entity_keys=()):
        """Agent arguments -> true ids. Handles (exact or inside strings such as predicate paths, any letter case)
        are translated. A true entity id (any letter case) in an entity-valued parameter is refused exactly like an
        invisible object; anywhere else (free text, keys, lists) it is neutralised to "something", so guessing ids
        neither works nor tells anything."""
        state = self.env.world.s
        plain = {p.lower() for p in self._plain_ids(state)}
        lowered = getattr(self, "_lower_ids", None)
        if lowered is None or lowered[0] != len(state["entities"]):
            lowered = (len(state["entities"]), {e.lower() for e in state["entities"]})
            self._lower_ids = lowered
        entities = lowered[1]

        def conv(value, entity_param=False):
            if isinstance(value, str):
                if HANDLE_RE.match(value.lower()):
                    eid = self.handles.resolve(value.lower())
                    if eid is None or eid not in state["entities"]:
                        raise ActionError("That is not visible here", "perception")
                    return eid
                if entity_param and value.strip().lower() in entities and value.strip().lower() not in plain:
                    raise ActionError("That is not visible here", "perception")

                def tok(match):
                    word = match.group(0)
                    low = word.lower()
                    if HANDLE_RE.match(low):
                        eid = self.handles.resolve(low)
                        if eid is None:
                            raise ActionError("That is not visible here", "perception")
                        return eid
                    if low in entities and low not in plain:
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
        return {key: conv(value, key in entity_keys) for key, value in raw.items()}

    def out_text(self, text, visible=None, issue=True):
        """True ids in a string -> handles. With issue=False only already-issued or visible ids get handles and
        other entity ids become 'something' (sound/smell hints must not identify unseen things)."""
        state = self.env.world.s
        entities = state["entities"]
        plain = self._plain_ids(state)

        if text in entities and text not in plain:
            if issue or text in self.handles.by_id or (visible is not None and text in visible):
                return self.handles.handle(text)
            return "something"
        if "_" not in text and text not in entities:
            return text

        def tok(match):
            word = match.group(0)
            if word in entities and word not in plain:
                if issue or word in self.handles.by_id or (visible is not None and word in visible):
                    return self.handles.handle(word)
                return "something"
            return word
        return TOKEN_RE.sub(tok, text)

    def out_obj(self, value, visible=None, issue=True):
        if isinstance(value, str):
            return self.out_text(value, visible, issue)
        if isinstance(value, list):
            return [self.out_obj(v, visible, issue) for v in value]
        if isinstance(value, dict):
            return {self.out_text(k, visible, issue) if isinstance(k, str) else k:
                    (v if k in DESCRIPTIVE_KEYS and isinstance(v, (str, list)) else self.out_obj(v, visible, issue))
                    for k, v in value.items()}
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
            if field == "contents":
                out[field] = value
                continue
            if field in ("under", "behind"):
                out[field] = value
                continue
            known = max(level, self.knowledge.level(eid, record, field), pending.get(field, GLANCE))
            shown = self.render(eid, record, field, value, known, ctx, thermal=thermal)
            if shown is not MISSING:
                out[field] = shown
        ident = self.identity(eid, record, max(level, self.knowledge.entity_level(eid, record)))
        out.update(ident)
        if "candidates" in ident or record.get("novel"):
            # record fields are restricted like observations; a domain inspector's own percepts pass, except extras
            # whose name gives the true type away ("kettle_state" on an unfamiliar object)
            words = {w for w in [record.get("type"), record.get("category")] + str(record.get("label") or "").split()
                     + list(record.get("is_a") or []) if w and len(str(w)) > 2}
            keep = LOOKALIKE_FIELDS | {"object", "contents", "under", "behind"} | exact
            out = {k: v for k, v in out.items()
                   if k in keep or (k not in record and not any(str(w) in k for w in words))}
        return out

    def outbound_message(self, message, involved=()):
        """Translate a result message token by token: ids of things the robot perceives now or that the command
        named become handles, other entity ids become "something"; true labels of unresolved look-alikes or
        unfamiliar things named in the command become their descriptor."""
        if not isinstance(message, str):
            return message
        state = self.env.world.s
        entities, plain = state["entities"], self._plain_ids(state)
        perception = self.env.perception
        involved = set(involved)
        for eid in list(involved):
            rec = entities.get(eid)
            if rec is None:
                continue
            ident = self.identity(eid, rec, self.knowledge.entity_level(eid, rec))
            if "candidates" in ident or rec.get("novel"):
                for name in sorted({str(rec.get("label") or ""), str(rec.get("type") or "").replace("_", " "),
                                    str(rec.get("type") or "")}, key=len, reverse=True):
                    if len(name) > 2:
                        message = re.sub(r"(?i)\b" + re.escape(name) + r"\b", str(ident.get("label") or "object"), message)

        def tok(m):
            word = m.group(0)
            if word not in entities or word in plain:
                return word
            if word in involved or word in self.handles.by_id and perception.visible(state, word) or perception.visible(state, word):
                return self.handles.handle(word)
            return "something"
        return TOKEN_RE.sub(tok, message)

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
        self._room_ctx = self._plain_now = None
        state = self.env.world.s
        self._room_ctx = self.room_context(state)
        self._plain_now = self._plain_ids(state)
        try:
            return self._observe()
        finally:
            self._room_ctx = self._plain_now = None

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
        hidden_kinds = {"damp", "film", "black_ice", "residue", "invisible"}
        for item in base["grid"]["hazards"]:
            if item.get("kind") == "sleeping":
                item = dict(item, kind="lying")      # asleep vs resting is not visible from a distance
            src = item.get("source")
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
        obs["grid"]["hazards"] = hazards
        if isinstance(obs.get("room"), dict):
            for key in ("floor_hazards", "wet_cells", "dirt_cells", "soil_cells"):
                obs["room"].pop(key, None)    # raw room bookkeeping; the perceived list is grid.hazards
        if dark:
            # in the dark people and pets are only shapes
            obs["people"] = {pid: {"label": "person", "location": p.get("location"), "pos": p.get("pos")} for pid, p in obs.get("people", {}).items()}
            obs["pets"] = {pid: {"label": "animal", "location": p.get("location"), "pos": p.get("pos")} for pid, p in obs.get("pets", {}).items()}
        for pid, person in list(obs.get("people", {}).items()) if not dark else []:
            true = state["people"].get(pid, {})
            cue = MOOD_CUES.get(str(true.get("mood") or "neutral"))
            person.pop("mood", None)
            person.pop("awake", None)
            if cue:
                person["cues"] = sorted(set(person.get("cues") or []) | {cue})
            if true.get("posture") in ("sleeping", "lying") or person.get("activity") in ("sleeping", "resting", "lying"):
                person["posture"] = "lying"           # asleep or awake looks the same until inspected
                if person.get("activity") in ("sleeping", "resting", "lying"):
                    person["activity"] = "lying down"
            abilities = person.pop("abilities", None)
            if isinstance(abilities, dict) and abilities.get("mobility") not in (None, "full"):
                person["mobility_aid"] = abilities.get("mobility")      # a cane or walker is visible; diagnoses are not
            if str(true.get("role") or "") in VISITOR_ROLES and not self.knowledge.data.get(pid, {}).get("level"):
                person["label"] = "unfamiliar person"
                person.pop("role", None)
        for pid, pet in list(obs.get("pets", {}).items()) if not dark else []:
            true = state["pets"].get(pid, {})
            pet.pop("mood", None)
            if true.get("posture") in ("sleeping", "lying", "resting") or pet.get("activity") in ("sleeping", "resting"):
                pet["posture"] = "resting"
                if pet.get("activity") in ("sleeping", "resting"):
                    pet["activity"] = "resting"
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
        return {"handles": self.handles.dump(), "knowledge": self.knowledge.dump()}

    def load(self, data):
        self.handles.load((data or {}).get("handles"))
        self.knowledge.load((data or {}).get("knowledge"))
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
    # domain-wide field_tiers are deliberately not used here: printed text belongs to the object's type
    type_name = record.get("type")
    if type_name in getattr(registry, "types", {}):
        for name in [type_name] + registry.ancestors(type_name):
            fields |= set((getattr(registry, "perception", {}).get(name) or {}).get("read", []))
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
    return field.endswith(("_level", "_fill", "_fraction", "_frac", "_ratio")) or field in ("level", "fill_level", "battery_level", "bag_fill")


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
