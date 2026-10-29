"""Realistic (imperfect) symbolic perception: what a camera-and-sensors robot could plausibly know.

HouseEnv(..., perception="realistic") routes every observation, action message and action argument through this
module; perception="oracle" (the default, used by the verification pipeline) is untouched.

  * Opaque handles: entities are exposed as per-episode handles ("obj_7f3a19c2" = short hash of seed, initial house,
    episode nonce and true id). Arguments must use handles this episode issued (true entity ids and handles of other
    episodes are refused as "not visible"); results and observations are translated back (ids of things destroyed
    during a step included; JSON results re-serialised in handle order). Room, person and pet ids stay readable (the
    robot knows the house and its residents).
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

This is symbolic perception: no pixels, no learned detector. Everything here is a pure function of (seed, episode
nonce, true id, observation context, knowledge level); it never mutates world state.

K-LEAK (independent review, 2026-10-22): conditions (kernel/timeline.py) read shown_entity / agent_shown, never true
values (L1); the episode nonce (NONCE_KEY) and issued-only handles (I5); id-like words and destroyed things echo alike
(I6); the geometric camera presents unresolved look-alikes at their group's apparent size and view.px follows the
shown size (apparent_view, _shown_px: L2); grant_problem gates inspect and touch grants (I3).

Geometric camera (kernel v3.0; docs/design/TIER1_CAMERA.md 10.6 and 11, TIER1_INTEGRATION 1.12 and 1.14). With
HouseEnv(camera="geometric") the camera's step view decides what is rendered and how much of it: a thing a current
frame shows only as a depth shape is a "dark shape"; at "detect" an "object" with its size class, shape and place
(colour in good light); at "recognize" its category word plus the large-scale states of RECOGNIZE_FIELDS (and a
fill bucket for a container seen at 48 px or more); at "identify" today's full glance rendering. Things in the
10-24 px band are detected by a fixed draw per viewpoint (seed, eye cell, 45-degree frame sector, camera), never
when absent from every frame. Per-target context (light at the target, pixels, occlusion, glare) replaces the
room-wide one; a type's perception "acuity" block raises what a glance field needs; face-gated fields need their
face seen this step. Tracked identity (Knowledge entries' "ident": [level, placement signature]) is updated once
per step by note_step(view) and keeps a thing's identity -- never its condition -- while it has not moved; people and
pets the camera cannot identify are shapes keyed by a handle per sighting. Room mode renders exactly as before.

Robot body (kernel v3.0; TIER1_INTEGRATION 1.3, TIER1_EMBODIMENT 6): the lens clarity in the observation context is
the body's (body.clarity, the sensors_view clarity: camera_clean capped by lens scratches); with imaging off (privacy
mode, cameras off or failed, a room privacy policy, powered down) the robot's room is dark for the cameras whatever
its light, so people and pets there are dark-spell shapes and no hazard is avoided from sight (Perception.seen_dark).
obs.robot arrives already rendered by kernel/proprio.py (integer gauge, estimates, buckets, detected faults only).
"""
import copy
import hashlib
import json
import math
import random
import re

from . import body, geometry, sight
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
# the same with imaging off (the depth camera shows shapes only)
BLIND_WHO = "With the cameras off it is too dark to tell who is who: address the shape you see"
FAR_WHO = "You cannot tell who that is from here; get closer or address the shape you see"   # lit room, unidentified
# a call, a text or an app message reaches a person by name: no need to see who is who (kernel v3.1 stage-C fix)
REMOTE_CHANNELS = ("phone", "text", "app")
UNNAMED_TEXT = "someone"            # a person's id in free text while the robot cannot tell who is who
# hazard kinds of a lying agent (asleep and awake look the same: realistic observations show "lying"; fallen stays
# person_down) and the postures that look like lying down
AGENT_LYING_KINDS = ("sleeping", "person_down", "lying")
LYING_POSTURES = {"people": ("sleeping", "lying"), "pets": ("sleeping", "lying", "resting")}
# floor hazards a camera does not see
HIDDEN_HAZARD_KINDS = {"damp", "film", "black_ice", "residue", "invisible"}
# what a puddle does to a floor (data/physics/liquids.json floor_hazard): a puddle the robot cannot identify yet shows
# its group's word (default "wet") and every avoid list naming one of these keeps out of it (review L4)
LIQUID_HAZARD_KINDS = ("wet", "slip", "sticky")


def id_shaped(word):
    """True for a lower-case word shaped like a generated entity id (letters/digits joined by "_" with a numeric part)."""
    return bool(ID_WORD_RE.match(word)) and ID_NUMBER_RE.search(word) is not None


def _id_like(word):
    """True for a word that could be an entity id, generated or hand-written: it has an underscore or a digit
    (letter_6, m9, main_water_valve). Free text neutralises such words whether or not a thing with that id exists."""
    return "_" in word or any(c.isdigit() for c in word)


def _walked_past(state, eid, motions):
    """True when entity eid lies within arm's reach of a cell of the step's motion records (the start cell and every
    cell walked, in the room walked through), so a contact the walk made is something the robot could learn."""
    room, cells = geometry.anchor(state, eid)
    if room is None or not cells:
        return False
    for record in motions or ():
        if not isinstance(record, dict) or record.get("from_room") != room:
            continue
        walked = [record.get("start")] + list(record.get("path") or [])
        for cell in walked:
            if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
                continue
            if min(geometry.chebyshev(tuple(cell), tuple(c)) for c in cells) <= geometry.REACH_CELLS:
                return True
    return False


def _record_key_words(record, depth=0):
    """The words of a record's dict keys, a few levels deep ("tax_2025" and "pdf" of a printer's file "tax_2025.pdf"):
    names of parts of that very thing that a command about it may write inside a longer value."""
    out = set()
    if depth > 4:
        return out
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(key, str):
                out.update(w.lower() for w in TOKEN_RE.findall(key))
            out |= _record_key_words(value, depth + 1)
    elif isinstance(record, list):
        for value in record[:64]:
            out |= _record_key_words(value, depth + 1)
    return out


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
               "contents_from", "liquid_from",
               # the geometric camera's placement inside a container (TIER1_INTEGRATION 1.14); its per-entity view
               # properties (a "view" dict) are hidden by Realism.tier (outdoor_plants' roof_face keeps a "view" word)
               "inner_spos"],
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
# what an unfamiliar (novel) thing shows while unidentified: how it looks and where it is, never a field whose presence
# or value comes from its type (openable, container, surface, portable, fill, a domain inspector's extras): review I4
NOVEL_FIELDS = {"label", "kind", "shape", "material_guess", "color", "size_class", "size_m", "location", "pos", "spos",
                "shelf", "relation", "rot", "held_hand", "soil", "wetness", "temperature_c", "broken", "shattered",
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

# ----- geometric camera (TIER1_CAMERA 10.6 and 11; TIER1_INTEGRATION 1.12, 1.14) -----------------------------------
# acuity levels of the step view, and what a glance field may need ("inspect": a head view of 160 px in light 0.2, the
# wrist, or an inspect grant)
VIEW_LEVELS = {"shape": 0, "detect": 1, "recognize": 2, "identify": 3}
ACUITY_NEED = {"detect": 1, "recognize": 2, "identify": 3, "inspect": 4}
# large-scale glance states a camera tells at "recognize" (11.1, with the physics cues of TIER1_INTEGRATION 1.12)
RECOGNIZE_FIELDS = frozenset(("open", "lit", "on_fire", "smoking", "steaming", "overflowing", "leaking", "flooded",
                              "tipped_over", "toppled", "fallen", "shattered", "spilled", "covered", "posture",
                              "fell", "collapsed_shelves", "in_motion", "blown_away"))
# what "detect" shows besides the generic identity ("object", "unknown") and the view (11.1): never type-dependent
DETECT_FIELDS = ("size_class", "shape", "location", "relation", "pos", "spos")
FILL_FIELDS = ("fill_level", "level", "liquid_l")
COLOR_LIGHT = 0.15          # colour is seen at detect from this light at the target (not backlit)
FILL_PX = 48.0              # a container's fill shows at recognize from this many pixels across
RANGE_DEPTH_M = tuple(sight.HW["depth"]["range_m"])  # the depth camera ranges (0.2-6 m, 10.6) ...
RANGE_PX = sight.PX["detect_sure"]                     # ... targets at least 24 px across
VIEW_ANGLE_STEP = 5.0       # realistic bearings and elevations, degrees
LIGHT5 = ((0.05, 0.2, 0.5, 0.8), ("dark", "dim", "moderate", "bright", "very bright"))
GLARE_M = {"frame": 1.0, "sun": 1.0, "backlit": 1.5}
VIEW_DIST = ((0.8, "within reach"), (1.5, "near"), (3.0, "mid"), (6.0, "far"))


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


def _size_max(size):
    """The largest of a size_m list's first three dimensions, or None when it is not a usable size."""
    if not isinstance(size, (list, tuple)) or not size:
        return None
    try:
        largest = max(float(v) for v in size[:3])
    except (TypeError, ValueError):
        return None
    return largest if math.isfinite(largest) and largest > 0 else None


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


# The per-episode secret an agent harness puts in the task (task["episode_nonce"], any non-empty string; new_nonce()
# makes one): it is mixed into the handle salt and into every fixed draw (noise, bucket slips, missed small things,
# the detection band, range noise), so two episodes of the same spec and seed share neither handles nor noise, and
# neither can be recomputed offline from the public spec, seed and code. Without one (verification, tests, tools) both
# follow (seed, initial house) exactly as before: deterministic and replayable. Saved states and replays keep the task,
# so they reproduce the episode's nonce (the harness keeps it private, as it keeps a test seed).
NONCE_KEY = "episode_nonce"


def episode_nonce(env):
    """The episode's nonce from its task (task["episode_nonce"]), or "" when the task carries none."""
    task = getattr(env, "_task", None)
    value = task.get(NONCE_KEY) if isinstance(task, dict) else None
    if isinstance(value, bool) or value is None:
        return ""
    return str(value).strip()


def new_nonce():
    """A fresh episode nonce for a harness: 128 bits from the operating system's random source (never seeded, so it
    cannot be guessed from anything an agent sees)."""
    import secrets
    return secrets.token_hex(16)


class Handles:
    """Per-episode opaque handles: obj_ + 8 hex digits of sha256(salt, id) (longer on a collision). The salt mixes
    the seed with a digest of the initial house, which agents never see, and the episode nonce when the task carries
    one, so handles cannot be computed from guessed ids. Agent arguments resolve only handles this episode issued
    (by_handle; Realism._resolve_handle); resolve() also recomputes the handle of any current entity, for tools that
    read a log (replays and loaded states need no stored map)."""

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

    def peek(self, eid):
        """eid's handle without issuing it (a sort key for things the robot may never have been shown: handle() issues,
        and an issued handle is one the agent may act on)."""
        found = self.by_id.get(eid)
        if found:
            return found
        digest = _h("handle", self.seed, self.salt, eid)
        width = 8
        candidate = "obj_" + digest[:width]
        while candidate in self.by_handle and self.by_handle[candidate] != eid:
            width += 2
            candidate = "obj_" + digest[:width]
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
    """What the robot has learned: {eid: {"sig": placement signature, "fields": {field: [level, value digest]}}}.
    Geometric camera mode adds "ident": [view level, placement signature] (tracked identity, TIER1_CAMERA 11.2): the
    best acuity level (1 detect, 2 recognize, 3 identify) at which the robot saw the thing since it last moved."""

    def __init__(self):
        self.data = {}

    @staticmethod
    def signature(record):
        return _digest([record.get("location"), record.get("pos"), record.get("spos"), record.get("relation"), record.get("shelf")])

    def grant(self, eid, record, level, fields=None):
        entry = self.data.get(eid)
        sig = self.signature(record)
        if entry is None or entry["sig"] != sig:
            old = entry
            entry = self.data[eid] = {"sig": sig, "fields": {}, "level": GLANCE}
            if old is not None and "ident" in old:
                entry["ident"] = old["ident"]       # checked against its own signature
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

    def note_ident(self, eid, record, level):
        """Tracked identity (geometric camera): the robot saw eid at view level `level` (1-3) where it is now."""
        sig = self.signature(record)
        entry = self.data.get(eid)
        if entry is None:
            entry = self.data[eid] = {"sig": sig, "fields": {}, "level": GLANCE}
        old = entry.get("ident")
        if old is not None and old[1] == sig:
            if old[0] >= level:
                return
            level = max(level, old[0])
        entry["ident"] = [int(level), sig]

    def ident_level(self, eid, record):
        """The tracked identity level of eid (0 none, 1 detect, 2 recognize, 3 identify): what it was seen at while it
        has not moved since; 3 when an inspection or reading of it still holds (entity knowledge at inspect or more)."""
        entry = self.data.get(eid)
        if entry is None:
            return 0
        sig = self.signature(record)
        if entry["sig"] == sig and entry.get("level", GLANCE) >= INSPECT:
            return VIEW_LEVELS["identify"]
        ident = entry.get("ident")
        return int(ident[0]) if ident and ident[1] == sig else 0

    def holds(self, eid, record, field):
        """True when a grant of `field` (at any level) still matches its current value."""
        entry = self.data.get(eid)
        if entry is None or entry["sig"] != self.signature(record) or field not in entry["fields"]:
            return False
        known = entry["fields"][field]
        current = record[field] if field in record else _field_value(record, field)
        return known[1] == _digest(current)

    def dump(self):
        return copy.deepcopy(self.data)

    def load(self, data):
        self.data = copy.deepcopy(data or {})


class Realism:
    def __init__(self, env):
        self.env = env
        # the episode nonce (NONCE_KEY): mixed into the handle salt and every fixed draw (_rng); "" keeps both exactly
        # as they were without one
        self.nonce = episode_nonce(env)
        salt = _digest(getattr(env, "_initial", None) or {})
        if self.nonce:
            salt = _h("episode", salt, self.nonce)[:16]
        self.handles = Handles(env.seed, salt=salt)
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
        self._named = set()        # ids the agent named by handle in the step in progress (echoed as those handles)
        self._names = {}           # look-alike group -> true names of its member types (message substitution)
        # darkness: people/pets in the robot's dark room are shapes keyed by a handle that changes with every dark
        # spell (a spell starts whenever the robot's view of a room turns dark)
        self._spell = 0
        self._last_view = None
        self._dark_now = None
        self._plain_now = None
        # geometric camera: people and pets the last step's view could not identify (keyed by a shape handle per
        # sighting: _geo_spell counts each one's sightings), and the step view's ViewInfos while one observe() runs
        self._geo_spell = {}
        self._geo_keyed = set()
        self._geo_infos = None
        self._geo_view = None
        # the robot's memory (kernel v3.1, TIER1_SWEEP 7.2): a cognitive action (recall, diff, snapshot) of the step in
        # progress shows every id whose handle was issued earlier, visible or not; the version counts knowledge
        # changes (perception.perceived_ids keys its cache on it); requests: who the robot could tell gave each one
        self._memory_out = False
        self.version = 0
        self._request_from = {}

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
        acuity = spec.get("acuity")
        if isinstance(acuity, dict):
            # geometric camera (TIER1_CAMERA 11.2): the view level a glance field needs ("recognize", "identify" or
            # "inspect"); room mode ignores it
            for field, need in acuity.items():
                if need in ACUITY_NEED:
                    table["_acuity:" + field] = need
        table.pop("_sub", None)
        return named

    def tier(self, record, field):
        table = self.tiers(record)
        if field.startswith("_"):
            return "never"
        if field == "view" and isinstance(record.get("view"), dict):
            return "never"          # the geometric camera's view properties (TIER1_INTEGRATION 1.14)
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
        if isinstance(robot.get("view"), dict):
            return self._geo_room_context(state)
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
        clean = _lens(state)
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
        if record is not None and isinstance(state["robot"].get("view"), dict):
            info = self._view_info(eid)
            if info is not None:
                return self.view_context(state, info)     # geometric camera: the target's own view (11.2)
        base = self.room_context(state)
        dist = 0
        if record is not None:
            room, cells = geometry.anchor(state, eid)
            if cells and room == base["rid"]:
                dist = min(geometry.chebyshev(base["pos"], c) for c in cells)
            elif room != base["rid"]:
                dist = 6
        return {"dist": dist, "light": base["light"], "m": base["m"] + 0.12 * max(0, dist - 1), "key": base["key"]}

    # ----- geometric camera: per-target context (TIER1_CAMERA 11.2) -------------------------------------------
    @staticmethod
    def _geo_global_m(state):
        """The viewpoint-wide part of the uncertainty in geometric mode: today's lens, fast-motion and steam / smoke
        terms (light, distance and glare are per target)."""
        robot = state["robot"]
        room = state["rooms"].get(robot.get("room"), {})
        m = 1.0
        clean = _lens(state)
        if clean < 0.7:
            m += 1.5 * (0.7 - clean) / 0.3
        if robot.get("speed_mode") == "fast":
            m += 1.0
        if _num(room.get("steam"), 0) > 0.3 or _num(room.get("smoke"), 0) > 0.2 or _num(room.get("smoke_density"), 0) > 0.2:
            m += 1.0
        return m

    def _geo_room_context(self, state):
        """room_context in geometric mode: the ambient light at the robot's eye (a lit flashlight or headlamp: at least
        0.5), m = the viewpoint terms plus the light term."""
        from . import camera
        from .perception import flashlight_on
        robot = state["robot"]
        rid = robot["room"]
        world = self.env.world if getattr(self.env.world, "s", None) is state else None
        light = camera.ambient_light(state, rid, world) if rid in state["rooms"] else 0.0
        if flashlight_on(state):
            light = max(light, 0.5)
        m = self._geo_global_m(state) + _light_m(light)
        pos = tuple(robot.get("pos") or (0, 0))
        key = "{}:{}:{}:{}".format(pos, int(light * 4), int(_lens(state) * 4), robot.get("speed_mode", "normal"))
        return {"light": light, "m": m, "key": key, "rid": rid, "pos": pos}

    def view_context(self, state, info):
        """The observation context of one target from its ViewInfo (TIER1_CAMERA 11.2): light = the light at the
        target; dist = range / 0.5 m (cells, kept for compatibility); m = the viewpoint terms + 0.6 log2(128 / px)
        when fewer than 128 px + 0.8 x occluded + 1.5 below light 0.3 (0.5 below 0.45) + glare (frame or sun 1.0,
        backlit 1.5)."""
        m = self._geo_m if getattr(self, "_geo_m", None) is not None else self._geo_global_m(state)
        light = _num(info.get("light"), 0.0)
        px = max(_num(info.get("px"), 0.0), 1e-3)
        occluded = max(0.0, 1.0 - _num(info.get("visible"), 1.0))
        m += 0.6 * max(0.0, math.log(128.0 / px, 2)) + 0.8 * occluded + _light_m(light) + GLARE_M.get(info.get("glare"), 0.0)
        return {"dist": _num(info.get("range_m"), 0.0) / 0.5, "light": light, "m": m, "key": "view"}

    def _view_info(self, eid):
        """The ViewInfo of eid in this step's stationary frames at the current eye (geometric mode): the observation's
        own while one observe() runs, else camera.acuity (memoized per World write); None when not seen."""
        if self._geo_infos is not None:
            return self._geo_infos.get(eid)
        from . import camera
        w = self.env.world
        # the knowledge version too: an unresolved look-alike's apparent size changes once the robot identifies it
        key = (w.s.get("steps", 0), w.s.get("time_s", 0.0), getattr(w, "mutation_count", None), self.version)
        memo = getattr(self, "_acuity_memo", None)
        if memo is None or memo[0] != key:
            memo = self._acuity_memo = (key, {})
        if eid not in memo[1]:
            found = camera.acuity(self.env, eid) if eid in w.s["entities"] else None
            memo[1][eid] = self.apparent_view(w.s, eid, w.s["entities"].get(eid), found["view"]) if found else None
        return memo[1][eid]

    def geo_detected(self, state, eid, record, info):
        """Whether a ViewInfo becomes a perception (TIER1_CAMERA 11.2): shapes, recognize and identify always; detect
        at 24 px or more; in the 10-24 px band by a fixed draw keyed by (seed, eid, the robot's cell, the frame's
        45-degree yaw sector, the camera) with p = (px - 10) / 14 - 0.05 x max(0, siblings - 3). A thing the robot
        inspected (and has not seen move) or found by inspecting what holds it is always detected."""
        level = info.get("level")
        if level != "detect":
            return level in ("shape", "recognize", "identify")
        px = _num(info.get("px"), 0.0)
        if px >= sight.PX["detect_sure"] - 1e-9 or info.get("via") == "held":
            return True
        clutter = 0
        if record is not None:
            if self.knowledge.entity_level(eid, record) >= INSPECT:
                return True
            parent = record.get("location")
            prec = state["entities"].get(parent)
            if prec is not None:
                children = self._children(state, parent)
                if self.knowledge.entity_level(parent, prec) >= INSPECT:
                    entry = self.knowledge.data.get(parent, {})
                    if entry.get("fields", {}).get("_contents") == [INSPECT, _digest(sorted(children))]:
                        return True
                clutter = max(0, len(children) - 1)
        p = sight.detect_probability(px, clutter)
        if p <= 0.0:
            return False
        if p >= 1.0:
            return True
        pos = tuple(state["robot"].get("pos") or (0, 0))
        sector = int((_num(info.get("frame_yaw"), 0.0) % 360.0) // 45.0)
        return self._rng("detect", eid, pos, sector, info.get("via") or "head").random() < p

    # ----- geometric camera: the apparent size of what the robot cannot tell apart (review L2) -----------------------
    def _apparent_size(self, eid, record):
        """(apparent size list, apparent largest dimension, true largest dimension) the camera presents for a thing:
        an unresolved look-alike whose size comes from its type presents its group's typical size (the size its record
        shows, _group_values; a size the instance was given itself, or one the robot measured, is its own); anything
        else its own size. (None, None, None) without a usable size_m."""
        size = record.get("size_m")
        true = _size_max(size)
        if true is None:
            return None, None, None
        own = (list(size[:3]), true, true)
        if self.group_of(record)[1] is None:
            return own          # no look-alike group (cached per type): the common case, without identifying it
        if eid in self.knowledge.data and self.knowledge.level(eid, record, "size_m") >= MEASURE:
            return own
        ident, gname, group, portion = self._identify(eid, record, self.knowledge.entity_level(eid, record))
        if group is None or portion or not _unidentified(ident, record):
            return own
        common = self.group_glance(gname, group)
        typed = "size_m" in common["explicit"] or size == self._type_default(record, "size_m")
        if common["size_m"] is MISSING or not typed:
            return own
        apparent = _size_max(common["size_m"])
        return (list(common["size_m"][:3]), apparent, true) if apparent is not None else own

    def apparent_max(self, eid, record):
        """The largest dimension the cameras judge a thing by (0.1 m without a usable size): _apparent_size's."""
        _size, apparent, _true = self._apparent_size(eid, record)
        return apparent if apparent is not None else 0.1

    def apparent_view(self, state, eid, record, info):
        """The ViewInfo the camera presents for entity eid (review L2): an unresolved look-alike is seen at its
        group's apparent size -- pixels scaled by apparent / true size, the view level re-derived from them with the
        frame's light and glare (sight.acuity_level) -- so neither the level nor the detection band nor the noise term
        tells members of different true size apart. A depth shape, a thing of its own apparent size and anything the
        robot can tell apart keep the camera's view. Idempotent (a returned copy is marked)."""
        if not isinstance(info, dict) or info.get("_apparent") or record is None or info.get("level") == "shape" \
                or info.get("via") == "held":
            return info
        _size, apparent, true = self._apparent_size(eid, record)
        if apparent is None or abs(apparent - true) <= 1e-9:
            return info
        out = dict(info, _apparent=True)
        px = _num(info.get("px"), 0.0) * apparent / true
        out["px"] = round(px, 1)
        light = _num(info.get("light"), 0.0)
        glare = info.get("glare")
        level = sight.acuity_level(px, light, sun_cone=glare == "sun", backlit=glare == "backlit")
        out["level"] = level or "detect"
        out["detected"] = level in ("recognize", "identify") or \
            (level == "detect" and px >= sight.PX["detect_oracle"] - 1e-9)
        if level is None:
            out["px"] = min(out["px"], sight.PX["detect_min"] - 0.1)     # below the band: never detected
        return out

    def _shown_px(self, eid, record, info, ctx):
        """view.px for the size the record shows (review L2): the camera's pixels (at the apparent size) scaled by the
        shown size estimate (the camera's 5 cm estimate, a look-alike's group size estimate, a measured size exactly),
        so px x range / focal gives back no more than size_m does."""
        px = _num(info.get("px"), 0.0)
        if record is None or info.get("via") == "held":
            return px
        size, apparent, _true = self._apparent_size(eid, record)
        if size is None or apparent is None or apparent <= 0:
            return px
        known = self.knowledge.level(eid, record, "size_m") if eid in self.knowledge.data else GLANCE
        shown = self.render(eid, record, "size_m", size, known, ctx)
        shown_max = _size_max(shown) if isinstance(shown, list) else None
        return px * shown_max / apparent if shown_max else px

    def view_dict(self, aid, info, depth=True, px=None):
        """The realistic "view" of a ViewInfo (TIER1_CAMERA 10.6): bearing and elevation to 5 degrees, a distance
        bucket, range_m only within the depth camera's 0.2-6 m for targets of 24 px or more (a fixed draw per target
        with sigma 0.01 + 0.005 d^2, to 0.1 m), pixels to 2 significant figures (px: the pixels to show, by default
        the ViewInfo's), occlusion in quarters, the camera that saw it and the light at it as a word (not for the depth
        camera's dark shapes: it measures no light)."""
        d = _num(info.get("range_m"), 0.0)
        px = _num(info.get("px"), 0.0) if px is None else _num(px, 0.0)
        out = {"bearing_deg": _step_round(_num(info.get("bearing_deg"), 0.0), VIEW_ANGLE_STEP),
               "elev_deg": _step_round(_num(info.get("elev_deg"), 0.0), VIEW_ANGLE_STEP),
               "px": _sig2(px), "occluded": round(max(0.0, 1.0 - _num(info.get("visible"), 1.0)), 2),
               "via": info.get("via")}
        if info.get("via") != "depth":
            out["light"] = _bucket(_num(info.get("light"), 0.0), *LIGHT5)
        estimate = None
        if depth and RANGE_DEPTH_M[0] - 1e-9 <= d <= RANGE_DEPTH_M[1] + 1e-9 and px >= RANGE_PX - 1e-9:
            sigma = 0.01 + 0.005 * d * d
            estimate = max(0.1, round((d + self._rng("range_m", aid).gauss(0.0, sigma)) * 10.0) / 10.0)
            out["range_m"] = estimate
        out["dist"] = _view_dist(estimate if estimate is not None else d)
        return out

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
        if self.nonce:
            # the episode nonce makes every fixed draw episode-specific (and secret): see NONCE_KEY
            return _FastRng("{}|n:{}|{}".format(self.env.seed, self.nonce, "|".join(str(p) for p in parts)))
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
            # a camera estimates sizes to about 5 cm (finer only with a tape measure): one fixed relative error per
            # thing; worse viewing conditions (m) only read it more coarsely, in whole multiples of 5 cm (a step off
            # that grid would show sizes like 7.5 cm exactly). An error that grew with m was a line in the
            # agent-controlled m whose intercept is the true size: a regression over a few viewing conditions recovered
            # it to millimetres (review L8)
            step = 0.05 * max(1, int(round(ctx["m"])))
            out = []
            for v in value[:3]:
                n = _num(v, 0.1)
                est = n * (1.0 + rng.gauss(0.0, 0.08))
                out.append(max(0.05, round(round(est / step) * step, 3)))
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
    def perceive_record(self, state, eid, record, ctx=None, level_floor=GLANCE, dark=False, geo=None):
        """The perceived record of a visible entity. geo (geometric camera, identify level): {"faces": faces seen
        this step, "inspect": the view resolves it for a close look, "decl": its declared faces} -- glance fields that
        need inspect acuity (a type's perception "acuity") and face-gated fields whose face was not seen are dropped
        unless a grant of them still holds."""
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
            if tier == "never" or (field == "view" and isinstance(value, dict)):
                continue
            if geo is not None and self._geo_drops(state, eid, record, field, tier, geo):
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
            shown = NOVEL_FIELDS if group is None and _novel_identity(ident) else LOOKALIKE_FIELDS
            out = {k: v for k, v in out.items() if k in shown or k in felt}
            if group is not None and not portion:
                self._group_values(eid, record, out, gname, group, ctx, felt, always=True)
        return dict(sorted(out.items()))     # field order must not reveal the type

    # ----- geometric camera: rendering by view level (TIER1_CAMERA 11.1) --------------------------------------
    def acuity_need(self, record, field):
        """The view level a glance field needs in geometric mode: the entity's own _perception "acuity", else its
        type's perception "acuity" ({field: "recognize" | "identify" | "inspect"}), else "recognize" for
        RECOGNIZE_FIELDS and "identify" for every other glance field."""
        own = record.get("_perception")
        if isinstance(own, dict) and isinstance(own.get("acuity"), dict) and own["acuity"].get(field) in ACUITY_NEED:
            return own["acuity"][field]
        found = self.tiers(record).get("_acuity:" + field)
        if found:
            return found
        return "recognize" if field in RECOGNIZE_FIELDS else "identify"

    def _geo_drops(self, state, eid, record, field, tier, geo):
        """Identify-level rendering in geometric mode: True to leave `field` out -- a glance field that needs inspect
        acuity when the view does not resolve the thing that well, or a field gated by a declared face (4.6) that no
        current frame saw -- unless a grant of it still holds (the robot read or examined it before)."""
        if tier == "glance" and not geo["inspect"] and self.acuity_need(record, field) == "inspect" \
                and not self.knowledge.holds(eid, record, field):
            return True
        decl = geo["decl"]
        if decl:
            face = sight.gated_face(state, eid, field)
            if face is not None and face in decl and face not in geo["faces"] and \
                    not self.knowledge.holds(eid, record, field):
                return True
        return False

    def perceive_view(self, state, eid, record, info, ctx=None):
        """The perceived record of an entity the camera's step view detected (TIER1_CAMERA 11.1), at the view level
        of its best current frame and its tracked identity: "shape" a dark shape; "detect" an object (size class,
        shape, place; colour in light 0.15 when not backlit); "recognize" adds its category word (a look-alike its
        group descriptor, a novel thing "unfamiliar object") and the large-scale states of RECOGNIZE_FIELDS (and
        fields whose acuity is "recognize"), and a container's fill bucket from 48 px; "identify" the full glance
        rendering. Below identify, identity fields follow the tracked identity (the best level the thing was seen at
        since it last moved) and fields whose grant still holds stay; the field set never depends on the type
        beyond the record's own large-scale states (unresolved look-alikes: LOOKALIKE_FIELDS only). Adds "view"."""
        ctx = ctx or self.view_context(state, info)
        level = VIEW_LEVELS.get(info.get("level"), 0)
        sv_depth = self._geo_view.depth if self._geo_view is not None else True
        if level <= 0:
            out = self.perceive_record(state, eid, record, ctx, dark=True)
        elif level >= VIEW_LEVELS["identify"]:
            decl = _declared_faces(state, eid)
            geo = {"faces": set(info.get("faces") or ()), "decl": decl,
                   "inspect": info.get("via") == "held" or sight.inspect_capable(_num(info.get("px"), 0.0),
                                                                                  _num(info.get("light"), 0.0))}
            out = self.perceive_record(state, eid, record, ctx, geo=geo)
        else:
            out = self._perceive_below(state, eid, record, info, ctx, level)
        out["view"] = self.view_dict(eid, info, depth=sv_depth, px=self._shown_px(eid, record, info, ctx))
        return dict(sorted(out.items()))

    def _perceive_below(self, state, eid, record, info, ctx, level):
        full = self.perceive_record(state, eid, record, ctx)
        has_knowledge = eid in self.knowledge.data
        ident_level = max(level, self.knowledge.ident_level(eid, record)) if has_knowledge else level
        ident, gname, group, portion = self._identify(eid, record, self.knowledge.entity_level(eid, record))
        unresolved = _unidentified(ident, record)
        if ident_level >= VIEW_LEVELS["identify"]:
            out = {k: copy.deepcopy(v) for k, v in ident.items()}        # tracked: who it is, not how it is
        elif ident_level == VIEW_LEVELS["recognize"]:
            out = self._recognized_identity(record, ident, unresolved)
        else:
            out = {"label": "object", "kind": "unknown"}
        keep = set(DETECT_FIELDS)
        light = _num(info.get("light"), 0.0)
        if light >= COLOR_LIGHT - 1e-9 and info.get("glare") != "backlit":
            keep.add("color")
        if level >= VIEW_LEVELS["recognize"]:
            keep |= {k for k in full if k in RECOGNIZE_FIELDS or self.acuity_need(record, k) == "recognize"}
            if _num(info.get("px"), 0.0) >= FILL_PX - 1e-9:
                keep.update(FILL_FIELDS)
        if has_knowledge:
            # what an action let the robot learn about it (an inspection, a reading, a measurement) and still holds
            keep |= {k for k in full if k not in IDENTITY_KEYS and self.knowledge.level(eid, record, k) >= INSPECT}
        if unresolved:
            shown = NOVEL_FIELDS if group is None and _novel_identity(ident) else LOOKALIKE_FIELDS
            keep &= shown | {k for k in full if self.knowledge.level(eid, record, k) >= TOUCH}
        for key in sorted(keep):
            if key in full and key not in out:
                out[key] = full[key]
        out["size_class"] = full.get("size_class") or size_class(record)
        out["shape"] = self._shape_shown(record, gname, group, unresolved and not portion)
        return out

    @staticmethod
    def _recognized_identity(record, ident, unresolved):
        """Identity at recognize: an unresolved look-alike's or portion's group identity, "unfamiliar object" for a
        novel thing, else the category word."""
        if record.get("novel") or (ident.get("kind") == "unknown" and "shape" in ident):
            return {"label": "unfamiliar object", "kind": "unknown"}
        if unresolved:
            return {k: copy.deepcopy(v) for k, v in ident.items() if k in IDENTITY_KEYS}
        category = str(record.get("category") or "object")
        return {"label": category.replace("_", " "), "kind": category}

    def _shape_shown(self, record, gname, group, by_group):
        """The coarse shape word of a thing (from its group's typical size for an unresolved look-alike)."""
        if by_group and group is not None:
            size = self.group_glance(gname, group)["size_m"]
            if size is not MISSING and size:
                return _shape({"size_m": size})
        return _shape(record)

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

    def _label_fields(self, group, record):
        """The fields whose print identifies a look-alike by reading: the group's label_fields, else any read-tier
        field that is not a content field, plus label_text/text/name_label (none for a blank label)."""
        blank = ("label_text" in record or "text" in record) and not any(record.get(k) for k in ("label_text", "text", "name_label"))
        if group.get("label_fields"):
            return list(group["label_fields"])
        if blank:
            return []                  # a blank label prints nothing: the item stays unidentified after reading
        content = _content_fields()
        return [f for f in record if f not in content and f not in ("tags",) and self.tier(record, f) == "read"] \
            + ["label_text", "text", "name_label"]

    def _labeled(self, group, record):
        """Whether reading the record can identify it: a non-empty label field (_label_fields)."""
        return any(record.get(k) for k in self._label_fields(group, record))

    def _read_identifies(self, record, fields):
        """Whether a field-by-field read (the geometric camera's core read grants exactly the fields its camera made
        out) printed a label that tells a read-resolved look-alike apart: a non-empty label field among `fields`. Room
        mode's read grants the whole record and raises the entity's level; this lets the geometric read do the same
        when, and only when, it read the label (people K-READ-LEVEL, safety request 4)."""
        _name, group = self.group_of(record)
        if group is None or group.get("resolve") != "read":
            return False
        wanted = set(fields)
        return any(record.get(k) for k in self._label_fields(group, record) if k in wanted)

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

    def novel_unidentified(self, eid, record=None):
        """True when the robot takes eid for an unfamiliar thing (a novel object, or a portion of one), whatever it is."""
        if record is None:
            record = self.env.world.s["entities"].get(eid)
            if record is None:
                return bool((self.gone.get(eid) or {}).get("novel"))
        ident, gname, _group, _portion = self._identify(eid, record, self.knowledge.entity_level(eid, record))
        return gname is None and _novel_identity(ident)

    def sealed(self, record):
        """A closed opaque container (lidded bin, closed jar, sealed box): its fill and contents are not visible."""
        see_through = record.get("transparent") or str(record.get("material") or "") in ("glass", "clear_plastic", "acrylic")
        return bool(record.get("openable")) and not record.get("open") and not see_through

    _sealed = sealed

    # ----- small-object detection ------------------------------------------------------------------------
    def missed(self, state, eid, record, ctx):
        if record.get("location") == "robot" or record.get("relation") == "held":
            return False
        if isinstance(state["robot"].get("view"), dict):
            # geometric camera: the detection band replaces this rule (11.2); a thing in no current frame is missed
            info = self._view_info(eid)
            return info is None or not self.geo_detected(state, eid, record, info)
        largest = self.apparent_max(eid, record)       # an unresolved look-alike: its group's size (review L2)
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

    # ----- what a condition sees (wait until, watch, guards: kernel/timeline.py; review L1) ----------------------
    def shown_entity(self, state, eid, info=None, remote=False):
        """The record of entity eid as the robot perceives it now, for judging a condition: the geometric camera's
        rendering of ViewInfo `info` (perceive_view), else the room camera's (perceive_record from the robot's
        viewpoint, the context of the observation); remote=True: what a status app or an audible status reports --
        the glance rendering in a near, lit, clear view. None for an unknown id. Never the true values: a condition
        compares these, so a wait answers no more than the observation shows."""
        record = state["entities"].get(eid)
        if record is None:
            return None
        if remote:
            return self.perceive_record(state, eid, record, {"dist": 0, "light": 1.0, "m": 1.0, "key": "remote"})
        if isinstance(info, dict):
            return self.perceive_view(state, eid, record, info)
        return self.perceive_record(state, eid, record)

    @staticmethod
    def agent_shown(group, field, value):
        """A person's or pet's field as a realistic observation shows it: posture "lying" for a person asleep or lying
        awake, "resting" for a pet asleep, lying or resting (observe); everything else as it is."""
        if field == "posture" and value in LYING_POSTURES.get(group, ()):
            return "lying" if group == "people" else "resting"
        return value

    # ----- the grant gate (review I3 / R4) -----------------------------------------------------------------------
    def grant_problem(self, state, eid, level, world=None, motions=()):
        """None when the robot can have learned `level` of entity eid where it is now; else why not. A close look
        (inspect) needs what core inspect needs: the thing held, or the geometric camera's inspect_check (the head
        resolving it to 160 px in light, or the wrist within reach), or -- room camera -- arm's reach, a large
        fixture (1 m or more) or an inspect_from_afar thing in the robot's room; a touch needs arm's reach (or the
        thing held). Either is also granted for a thing within arm's reach of a cell the robot walked this step
        (`motions`: the step's motion records, actions.motion_records: walking into a glass panel settles what it
        is). Read and measure grants are the domains' (a display, a meter) and pass. Rooms, people and pets are not
        gated here."""
        lvl = LEVELS.get(level, level) if isinstance(level, str) else level
        if lvl not in (INSPECT, TOUCH):
            return None
        record = state["entities"].get(eid)
        if record is None:
            return None
        robot = state["robot"]
        if record.get("location") == "robot" or eid in [v for v in (robot.get("hands") or {}).values() if v]:
            return None
        if motions and _walked_past(state, eid, motions):
            return None
        if lvl == INSPECT and isinstance(robot.get("view"), dict):
            from . import camera
            ok, reason, _via = camera.inspect_check(state, eid, world)
            return None if ok else (reason or "not resolved by the cameras")
        problem = geometry.reach_problem(state, eid)
        if problem is None or "Crouch" in problem or (lvl == INSPECT and "Too high" in problem):
            return None
        if lvl == INSPECT and "Approach" in problem and (_size_max(record.get("size_m")) or 0.0) >= 1.0 - 1e-9:
            return None
        if lvl == INSPECT and "Approach" in problem and record.get("inspect_from_afar"):
            return None
        return problem

    # ----- knowledge grants (from handlers, applied when the step succeeds) ------------------------------
    def grant(self, eid, level, fields=None):
        level = LEVELS.get(level, level) if isinstance(level, str) else int(level)
        self.pending.append((eid, level, tuple(fields) if fields is not None else None))

    def begin_step(self):
        """Called by the engine before an action: remember what the robot holds (knowledge of things the robot
        itself carries or sets down survives the move) and drop any stray grants."""
        self.pending = []
        self._memory_out = False
        self._named_plain = set()       # people the command named over a remote channel (kept plain in its message)
        self._named = set()             # things the command named by handle (_resolve_handle)
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
                if eid in self.handles.by_id:
                    # the category the robot perceived of a thing it was shown (a report about it after it is gone
                    # suggests the same issue words as for one out of view: reports._perceived_category)
                    entry = self.gone.setdefault(eid, {})
                    entry["kind"] = "object" if entry.get("unresolved") or self.unresolved(eid, before) else \
                        str(before.get("category") or "object")
            elif ok and after.get("displaced") is True and before.get("displaced") is not True:
                entry = self.knowledge.data.get(eid)
                if entry is not None and entry["sig"] == self.knowledge.signature(before):
                    entry["sig"] = self.knowledge.signature(after)      # the robot knows it pushed it there
                    ident = entry.get("ident")
                    if ident and ident[1] == self.knowledge.signature(before):
                        entry["ident"] = [ident[0], entry["sig"]]       # ... and still knows what it is

    def commit_grants(self, ok):
        grants, self.pending = self.pending, []
        self.version += 1
        state = self.env.world.s
        if ok:
            held_now = {v for v in (state["robot"].get("hands") or {}).values() if v}
            for eid in sorted(getattr(self, "_held_before", set()) | held_now):
                record = state["entities"].get(eid)
                entry = self.knowledge.data.get(eid)
                if record is not None and entry is not None:
                    ident = entry.get("ident")
                    if ident and ident[1] == entry["sig"]:
                        entry["ident"] = [ident[0], self.knowledge.signature(record)]
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
                if level == READ and fields is not None and self._read_identifies(record, fields):
                    entry = self.knowledge.data.get(eid)
                    if entry is not None:
                        entry["level"] = max(entry.get("level", GLANCE), READ)
            if level >= INSPECT and fields is None:
                # inspecting a surface/container finds the small things on/in it
                entry = self.knowledge.data.get(eid)
                if entry is not None:
                    entry["fields"]["_contents"] = [INSPECT, _digest(sorted(self._children(state, eid)))]

    def grant_prior(self, eid, record, level):
        """Knowledge the robot brought into the episode (setup op prior_knowledge, TIER1_SWEEP 7.4): eid as `record`
        showed it then, at `level` ("glance" / "inspect" / "read" / "touch" / "measure"), with the same field rule as
        an action's grant. The signature is the record's of that moment, so it lapses as soon as the thing has moved."""
        lvl = LEVELS.get(level, level) if isinstance(level, str) else int(level)
        if not isinstance(lvl, int) or lvl <= GLANCE:
            return
        names = [f for f in record if not f.startswith("_") and self.tier(record, f) not in ("measure", "never")]
        if lvl in (INSPECT, READ):
            self.knowledge.grant(eid, record, INSPECT, [f for f in names if self.tier(record, f) != "read"])
            if lvl == READ:
                self.knowledge.grant(eid, record, READ, [f for f in names if self.tier(record, f) == "read"])
            entry = self.knowledge.data.get(eid)
            if entry is not None:
                entry["level"] = max(entry.get("level", GLANCE), lvl)
        else:
            self.knowledge.grant(eid, record, lvl, None)
        self.version += 1

    def memory_output(self):
        """The step in progress is a cognitive action reporting the robot's memory (recall, diff, snapshot): its
        message names every thing whose handle was issued earlier, seen now or not (TIER1_SWEEP 7.2); things never
        perceived stay "something"."""
        self._memory_out = True

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
        dark = bool(rid in state.get("rooms", {}) and self.env.perception.seen_dark(state, rid))
        view = (rid, dark)
        if dark and self._last_view != view:
            self._spell += 1
        self._last_view = view
        # geometric camera: people and pets the last step's view saw but could not identify (a depth shape, too far,
        # too dim or hazy) are shapes too, each keyed per sighting (note_step)
        keyed = self._geo_keyed if isinstance(robot.get("view"), dict) else ()
        if not dark and not keyed:
            return {}
        out = {}
        for group in ("people", "pets"):
            for aid, agent in sorted((state.get(group) or {}).items()):
                if agent.get("location") == rid and aid not in out:
                    if dark:
                        out[aid] = self.handles.handle("shape:{}:{}".format(aid, self._spell))
                    elif aid in keyed:
                        out[aid] = self.handles.handle("shape:{}:g{}".format(aid, self._geo_spell.get(aid, 0)))
        return out

    def _resolve_handle(self, text, state, dark, entity_param=True):
        """A handle the agent typed -> the true id. Only handles this episode issued resolve: one remembered from
        another episode of the same house, or computed offline from the public spec and seed, is refused exactly like
        anything out of view (review I5 / R5). In an entity parameter the thing must still exist (else "not visible",
        as for anything out of view); in any other parameter (free text, a memory key, a predicate path, ask's about)
        a thing destroyed since resolves too, so the reply never says that something seen earlier is gone (review
        I6)."""
        eid = self.handles.by_handle.get(text)
        if isinstance(eid, str) and eid.startswith("shape:"):
            pid = eid.split(":")[1]
            if dark.get(pid) == text:
                return pid          # a shape the robot sees now (people/pets in the dark are addressed by their shape)
            raise ActionError("That is not visible here", "perception")
        if eid is None or (eid not in state["entities"] and (entity_param or eid not in self._ever)):
            raise ActionError("That is not visible here", "perception")
        self._named.add(eid)
        return eid

    def _target_part_names(self, state, eid):
        """Words a command about entity eid may use for parts of it (a slot "layer_2", a burner key, a program name),
        kept verbatim by inbound -- built only from what the robot can know of it, never from its true record alone
        (phase-1 red team: free text naming an unresolved look-alike echoed its true type, liquid or product, because an
        id-like word was kept exactly when the true record held it). An identified thing: the keys and short string
        values of its fields that are not hidden (tier "never"), and its type. An unresolved look-alike or unfamiliar
        thing: the identity the robot perceives (label, kind, candidates) and, for a look-alike, the names of every
        member of its group alike -- the outbound substitution shows each of them as the group label, so the echo is
        the same for every member -- and nothing of its own record."""
        record = state["entities"].get(eid)
        if record is None:
            return set()
        if self.unresolved(eid, record):
            ident, gname, _group, _portion = self._identify(eid, record, self.knowledge.entity_level(eid, record))
            found = _record_names(ident)
            if gname:
                found |= {str(name).lower() for name in self._group_names(gname)}
            return found
        shown = {k: v for k, v in record.items()
                 if isinstance(k, str) and k[:1] != "_" and self.tier(record, k) != "never"}
        found = _record_names(shown) | _record_key_words(shown)
        if isinstance(record.get("type"), str):
            found.add(record["type"].lower())
        return found

    def inbound(self, raw, text_keys=(), entity_keys=()):
        """Agent arguments -> true ids. Handles (exact or inside strings such as predicate paths, any letter case)
        are translated. A true entity id (any letter case) in an entity-valued parameter is refused exactly like an
        invisible object; anywhere else (free text, keys, lists) it is neutralised to "something" -- and so, whether
        or not such a thing exists, is every word shaped like a generated id (a numeric part: fabric_softener_1,
        dishwasher_1_basket, letter_6) and, in free text (text parameters), every id-like word (an underscore or a
        digit: main_water_valve, m9), unless it names a part of the command's own target (a slot "layer_2") -- so
        guessing ids neither works nor tells anything (the echo of the agent's own text is the same either way).
        Handles resolve only when this episode issued them (_resolve_handle). Rooms, people and
        pets keep their ids, but in the dark a person or pet named by id is refused (DARK_WHO): the robot sees shapes,
        addressed by their shape handle. That holds for every parameter (about, subject, options, a predicate path...),
        not only the person-typed ones: a typed id never comes back as a shape handle; in free text (text parameters)
        such an id becomes "someone". A command over a remote channel (channel phone, text or app) names people as
        they are -- a call needs no sight of who is who -- and its message keeps them plain. The converted free-text
        arguments are remembered for outbound_message (the agent's own words are never rewritten as if they were the
        domain's)."""
        state = self.env.world.s
        dark = self._dark_agents(state)
        agents = {a.lower() for group in ("people", "pets") for a in (state.get(group) or {})}
        remote = str(raw.get("channel") or "").strip().lower() in REMOTE_CHANNELS
        dark_room = bool(self._last_view and self._last_view[1])
        # who cannot be named by id: while shapes share the robot's dark room everyone (present or not: a refusal
        # must not tell who is there); in a lit room (geometric camera) the people and pets the camera could not
        # identify; nobody over a remote channel
        if remote or not dark:
            unnamed = set()
        else:
            unnamed = agents if dark_room else {a.lower() for a in dark}
        if remote:
            self._named_plain = getattr(self, "_named_plain", set()) | {
                a for group in ("people", "pets") for a in (state.get(group) or {})
                if any(isinstance(v, str) and v.strip().lower() == a.lower() for v in raw.values())}

        def who_refusal():
            if not dark_room:
                # a lit room: the geometric camera saw them too far, backlit or too dim to tell who it is
                return ActionError(FAR_WHO, "perception")
            return ActionError(DARK_WHO if body.imaging(state) else BLIND_WHO, "perception")
        public = {r.lower() for r in state.get("rooms", {})} | agents | {"robot"}
        lowered = getattr(self, "_lower_ids", None)
        if lowered is None or lowered[0] != (len(state["entities"]), len(self._ever)):
            # ids of things destroyed earlier are neutralised too (free text must not map them to handles either)
            lowered = ((len(state["entities"]), len(self._ever)), {e.lower() for e in state["entities"]} | {e.lower() for e in self._ever})
            self._lower_ids = lowered
        entities = lowered[1]
        names = set()             # names of parts of the command's own targets (slot "layer_2", key "burner_1")
        # the key words of the targets' records (a slot "battery_1" of a remote the robot cannot tell from the other
        # remotes): kept in non-text parameters only, where an action naming a part acts on it anyway; free text keeps
        # only `names`, what the robot can know of the target (phase-1 red team)
        struct_names = set()

        def conv(value, entity_param=False, text_param=False):
            if isinstance(value, str):
                low_value = value.strip().lower()
                if HANDLE_RE.match(value.lower()):
                    return self._resolve_handle(value.lower(), state, dark, entity_param)
                if low_value in unnamed and not text_param:
                    raise who_refusal()
                if entity_param and low_value in entities and low_value not in public:
                    raise ActionError("That is not visible here", "perception")

                def tok(match):
                    word = match.group(0)
                    low = word.lower()
                    if HANDLE_RE.match(low):
                        return self._resolve_handle(low, state, dark, entity_param)
                    if low in unnamed:
                        if text_param:
                            return UNNAMED_TEXT
                        raise who_refusal()
                    if low in public:
                        return word
                    # neutralised whether or not such a thing exists, so the echo never tells (review I6): a true id;
                    # in free text every id-like word (an underscore or a digit: letter_6, parcel_1_item_2, m9,
                    # main_water_valve); in other parameters every word shaped like a generated id (a numeric part) --
                    # except the names of parts of the command's own target (a slot "layer_2")
                    if low in entities or (low not in names and (text_param or low not in struct_names) and
                                           (id_shaped(low) or (text_param and _id_like(low)))):
                        if entity_param:
                            raise ActionError("That is not visible here", "perception")
                        return "something"
                    return word
                return TOKEN_RE.sub(tok, value)
            if isinstance(value, list):
                return [conv(v, entity_param, text_param) for v in value]
            if isinstance(value, dict):
                return {conv(k, False, text_param) if isinstance(k, str) else k: conv(v, entity_param, text_param)
                        for k, v in value.items()}
            return value
        out = {key: conv(value, True) for key, value in raw.items() if key in entity_keys}
        for value in out.values():
            if isinstance(value, str) and value in state["entities"]:
                names |= self._target_part_names(state, value) - entities
                struct_names |= _record_key_words(state["entities"][value]) - entities
        out.update({key: conv(value, False, key in text_keys) for key, value in raw.items() if key not in entity_keys})
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
            if group is None and _novel_identity(ident):
                # an unfamiliar thing: a fixed key set whatever it is (review I4: a kettle's fill, a lamp's bulb_hot or a
                # detector's battery_indicator told the types apart), plus the instrument readings passed as exact
                keep = NOVEL_FIELDS | {"object", "under", "behind"} | exact
                out = {k: v for k, v in out.items() if k in keep}
            else:
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

        named_plain = getattr(self, "_named_plain", None) or ()

        named = self._named

        def tok(m):
            word = m.group(0)
            if word in dark and word not in named_plain:
                return dark[word]
            if word in plain or word in named_plain:
                return word
            if word in named and word in by_id:
                # a thing the agent itself named by handle comes back as that handle, whether it is in view, out of
                # view or destroyed since (the echo of the agent's own words must not tell which: review I6)
                return by_id[word]
            if word not in entities:
                if word in ever or word in by_id:
                    # destroyed during the step (merged, consumed, pulled): its handle if issued, else "something"
                    return by_id.get(word) or ("something" if word not in involved else self.handles.handle(word))
                return word
            if self._memory_out and word in by_id:
                return by_id[word]          # the robot's own memory names what it perceived before (TIER1_SWEEP 7.2)
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

        gone_re, gone_handle = self._gone_phrases(named, entities, by_id)

        def tok_descriptive(m):
            # a descriptive value (a label, a kind) of a JSON result: only id-like words are ids there; a plain word
            # that merely equals a hand-written id ("kettle") is the label's own word (appliances_cooking's report:
            # "stovetop kettle" came out as "stovetop obj_..."), as observations already treat DESCRIPTIVE_KEYS
            return tok(m) if _id_like(m.group(0)) else m.group(0)

        def conv(text, key=None, ids_only=False):
            if sub is not None and not ids_only:
                text = substitute(text, key)
            if gone_re is not None:
                # a destroyed thing the agent named, written out as words by a domain that no longer finds it
                # ("apple 3" for apple_3): its handle, as for a thing out of view
                text = gone_re.sub(lambda m: gone_handle[m.group(0).lower()], text)
            return TOKEN_RE.sub(tok_descriptive if key in DESCRIPTIVE_KEYS and not ids_only else tok, text)

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

    @staticmethod
    def _gone_phrases(named, entities, by_id):
        """(pattern, {phrase: handle}) for the destroyed things the command named by handle: their ids as a domain
        writes an unknown id out in words ("apple 3", the dialogue's normalised words), so that a reply about a thing
        the robot saw earlier names it by its handle whether it still exists or not (review I6); (None, None) when the
        command named nothing destroyed."""
        gone = sorted(e for e in named if e not in entities and e in by_id)
        if not gone:
            return None, None
        from .dialogue import words_of
        phrases = {}
        for eid in gone:
            for phrase in (eid.replace("_", " "), " ".join(words_of(eid, stop=False))):
                phrase = phrase.strip().lower()
                if phrase and " " in phrase:
                    phrases.setdefault(phrase, by_id[eid])
        if not phrases:
            return None, None
        pattern = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:" + "|".join(
            re.escape(p) for p in sorted(phrases, key=lambda s: (-len(s), s))) + r")(?![A-Za-z0-9_])")
        return pattern, phrases

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
        self._geo_infos = self._geo_view = self._geo_m = None
        state = self.env.world.s
        if isinstance(state["robot"].get("view"), dict):
            # geometric camera: this step's view (cached by camera.end_step; computed now after reset or an edit)
            from . import camera
            self._geo_view = camera.step_view(self.env)
            self._geo_infos = self._view_infos(self._geo_view)
            for eid, info in self._geo_view.entities.items():
                self._geo_infos[eid] = self.apparent_view(state, eid, state["entities"].get(eid), info)
            self._geo_m = self._geo_global_m(state)
        self._room_ctx = self.room_context(state)
        self._dark_now = self._dark_agents(state)
        self._plain_now = self._plain_ids(state)
        try:
            return self._observe()
        finally:
            self._room_ctx = self._plain_now = self._dark_now = None
            self._geo_infos = self._geo_view = self._geo_m = None

    @staticmethod
    def _view_infos(view):
        out = {}
        if view is not None:
            for table in (view.pets, view.people, view.entities):
                out.update(table)
        return out

    def _observe(self):
        env = self.env
        state = env.world.s
        base = env.perception.observe(env, records=False)   # oracle visibility, darkness, privacy, channels
        robot_room = state["robot"]["room"]
        visible = {}
        perceived_ids = set()
        dark = base.get("dark")
        geo = self._geo_view is not None
        missed, phantoms = set(), []
        for eid in sorted(base["visible_entities"], key=lambda e: len(self._chain(state, e))):
            record = state["entities"].get(eid)
            if record is None:
                continue
            base_rec = base["visible_entities"][eid]
            if geo:
                # geometric camera: what a current frame detected, at its view level (11.1), after the detection band
                info = self.apparent_view(state, eid, record, base_rec.get("_view"))
                if not isinstance(info, dict) or not self.geo_detected(state, eid, record, info):
                    continue
                perceived_ids.add(eid)
                visible[eid] = self.perceive_view(state, eid, record, info)
                if VIEW_LEVELS.get(info.get("level"), 0) >= VIEW_LEVELS["identify"]:
                    for key, value in base_rec.items():
                        if key not in ("label", "_view") and key not in visible[eid]:
                            visible[eid][key] = value      # observers' percepts are kept for what is identified
            else:
                ctx = self.context(state, eid, record)
                if record.get("location") in missed or (not dark and self.missed(state, eid, record, ctx)):
                    missed.add(eid)
                    continue
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
        missed_agents = set()
        if geo:
            # people and pets in the detection band are detected by the same fixed draw as things
            for group_name in ("people", "pets"):
                for aid, rec in list((obs.get(group_name) or {}).items()):
                    info = rec.get("_view") if isinstance(rec, dict) else None
                    if not isinstance(info, dict) or not self.geo_detected(state, aid, None, info):
                        obs[group_name].pop(aid, None)
                        missed_agents.add(aid)
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
            if src in state["entities"] and src not in perceived_ids or src in missed_agents:
                continue
            srec = state["entities"].get(src) or {}
            if (srec.get("category") == "mirror" or srec.get("reflective")) and self.knowledge.entity_level(src, srec) < INSPECT:
                continue      # an un-inspected mirror still looks like an opening
            if item.get("kind") in hidden_kinds:
                continue
            vetoes = HOOKS.get("visible_floor_hazard", [])
            if vetoes and any(fn(state, robot_room, tuple(item["cell"]), item.get("kind"), src) is False for _, _, fn in vetoes):
                continue
            shown_kind = self._liquid_kind_shown(state, src, item.get("kind"))
            if shown_kind != item.get("kind"):
                item = dict(item, kind=shown_kind)
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
        # listing order must not follow the spawn order of the sources (review I11): by cell, then by the handle a
        # source has or would get (peek issues nothing), people and pets by id, then kind
        hazards.sort(key=lambda h: (list(h.get("cell") or ()), self._source_order(state, h.get("source")),
                                    str(h.get("kind"))))
        obs["grid"]["hazards"] = hazards
        if isinstance(obs.get("room"), dict):
            for key in ("floor_hazards", "wet_cells", "dirt_cells", "soil_cells"):
                obs["room"].pop(key, None)    # raw room bookkeeping; the perceived list is grid.hazards
        if dark:
            # in the dark people and pets are only shapes, keyed by their dark-spell handle (an id would say who is who)
            # and listed in handle order
            shapes = self._dark_now or {}
            for group_name, word in (("people", "person"), ("pets", "animal")):
                found = {}
                for pid, p in obs.get(group_name, {}).items():
                    shown = {"label": word, "location": p.get("location"), "pos": p.get("pos")}
                    if geo and isinstance(p.get("_view"), dict):
                        shown["view"] = self.view_dict(pid, p["_view"], self._geo_view.depth)
                    found[shapes.get(pid, pid)] = shown
                obs[group_name] = {k: found[k] for k in sorted(found)}
        elif geo:
            # geometric camera, lit room: the people and pets the view could not identify are shapes keyed by their
            # sighting handle; everyone listed gets the realistic view
            shapes = self._dark_now or {}
            for group_name, word in (("people", "person"), ("pets", "animal")):
                found = {}
                for pid, p in obs.get(group_name, {}).items():
                    info = p.pop("_view", None)
                    shown = p
                    if pid in shapes:
                        shown = {"label": word, "location": p.get("location"), "pos": copy.deepcopy(p.get("pos"))}
                    if isinstance(info, dict):
                        shown["view"] = self.view_dict(pid, info, self._geo_view.depth)
                    found[shapes.get(pid, pid)] = shown
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
        if geo and isinstance(obs.get("camera"), dict):
            obs["camera"] = self._camera_channel(obs["camera"])
        if isinstance(obs.get("request"), dict):
            obs["request"] = self.render_request(obs["request"], state)
        if isinstance(obs.get("messages"), list):
            # a request's own speech names its speaker as obs.request does ("unknown caller", "unfamiliar person", a
            # shape handle): one decision for both
            messages = []
            for message in obs["messages"]:
                if isinstance(message, dict) and message.get("request") is not None:
                    decided = self.request_sender(message["request"], state)
                    if decided is not None and decided.get("from") != message.get("speaker"):
                        message = dict(message, speaker=decided.get("from"))
                messages.append(message)
            obs["messages"] = messages
        if isinstance(obs.get("notices"), list):
            obs["notices"] = [self._render_notice(n) for n in obs["notices"] if isinstance(n, dict)]
        # channels and messages: ids of things not perceived become 'something' (the camera and lidar blocks too)
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
            # issues handles for the current room's door ids in their established order (the map itself is
            # env.mind.map, which observing does not write: kernel v3.1)
            self.out_obj(km, visible=perceived_ids, issue=True)
        if self.record_observations:
            self.observed.append(copy.deepcopy(obs))
        return obs

    @staticmethod
    def _camera_channel(block):
        """obs.camera as the robot knows it (TIER1_CAMERA 10.6): its own pose exactly (proprioception), the light at
        its eye as a word, the room's coverage to 2 decimals, seen events with bearings to 5 degrees (their ids are
        translated like any channel: unperceived things become "something")."""
        out = copy.deepcopy(block)
        if _num(out.get("light")) is not None:
            out["light"] = _bucket(_num(out["light"]), *LIGHT5)
        if _num(out.get("coverage")) is not None:
            out["coverage"] = round(_num(out["coverage"]), 2)
        events = []
        for event in out.get("events") or []:
            if isinstance(event, dict):
                event = dict(event)
                if _num(event.get("bearing_deg")) is not None:
                    event["bearing_deg"] = _step_round(event["bearing_deg"], VIEW_ANGLE_STEP)
            events.append(event)
        out["events"] = events
        return out

    def _source_order(self, state, src):
        """Sort key of a hazard's source that says nothing about spawn order or true ids: a person's or pet's shape
        handle in the dark (else its id, which is public), a room's id (public), and any other source -- an entity, or
        one a room's floor_hazards still names after it is gone -- by the handle it has or would get (peeked, not
        issued: phase-1 red team, second pass; a gone source sorted by its true id)."""
        if isinstance(src, str) and (src in (state.get("people") or {}) or src in (state.get("pets") or {})):
            return (1, str((self._dark_now or {}).get(src, src)))
        if isinstance(src, str) and src in (state.get("rooms") or {}):
            return (1, src)
        if isinstance(src, str):
            return (0, self.handles.peek(src))
        return (2, str(src))

    def _liquid_unidentified(self, state, src, kind):
        """True for a wet/slip/sticky hazard whose source entity the robot cannot identify yet (review L4)."""
        if kind not in LIQUID_HAZARD_KINDS or not isinstance(src, str):
            return False
        record = state["entities"].get(src)
        return record is not None and self.unresolved(src, record)

    def _liquid_kind_shown(self, state, src, kind):
        """The floor-hazard kind the robot sees for a puddle-like source: its own kind once the source is identified,
        but while the robot cannot tell what the source is (an unidentified puddle, the spill of an unresolved jug)
        a wet/slip/sticky kind would name the liquid (chlorine bleach 'wet' vs detergent 'slip'): it shows its group's
        `floor_hazard` word, default "wet" (review L4). Other kinds and non-entity sources are unchanged."""
        if not self._liquid_unidentified(state, src, kind):
            return kind
        record = state["entities"][src]
        name = self._entity_group(state, record)
        group = self.groups().get(name) if name else None
        word = (group or {}).get("floor_hazard")
        return word if word in LIQUID_HAZARD_KINDS else "wet"

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
        if not kinds or rid not in rooms or self.env.perception.seen_dark(state, rid):
            return hook_kinds, cells
        room = rooms[rid]
        # the approach target's own sources, plus sources a motion in progress plans without (navigation_robot's
        # robot field "_nav_plan_exclude", honoured by its path_blocked hook as well)
        ignore = set(ignore_sources or ()) | set((state.get("robot") or {}).get("_nav_plan_exclude") or ())
        agents = {}
        for group in ("people", "pets"):
            for aid, agent in (state.get(group) or {}).items():
                if agent.get("location") == rid and aid not in agents and not agent.get("inside"):
                    agents[aid] = (group, agent)        # one inside an appliance or crate is not on the floor
        from .plugins import HOOKS
        vetoes = HOOKS.get("visible_floor_hazard", [])
        visible = {}
        for cell, items in geometry.hazard_cells(state, rid).items():
            for kind, src in items:
                if src in ignore or kind in HIDDEN_HAZARD_KINDS:
                    continue
                veto_kind = None
                if src in agents:
                    shown = self._agent_hazard_kind(*agents[src]) if kind in AGENT_LYING_KINDS else \
                        (None if agents[src][1].get("concealed") and not agents[src][1].get("revealed") else kind)
                elif src in state["entities"]:
                    if src not in visible:
                        srec = state["entities"][src]
                        visible[src] = self.env.perception.visible(state, src) and not (
                            (srec.get("category") == "mirror" or srec.get("reflective")) and self.knowledge.entity_level(src, srec) < INSPECT)
                    shown = kind if visible[src] else None
                    veto_kind = kind
                    if shown is not None and self._liquid_unidentified(state, src, shown):
                        # an unidentified puddle: any liquid kind in the avoid list keeps out of it, whatever its
                        # true liquid, so route probing does not tell the liquid either (review L4)
                        if not any(k in LIQUID_HAZARD_KINDS for k in kinds):
                            continue
                        shown = next(k for k in kinds if k in LIQUID_HAZARD_KINDS)
                else:
                    shown = "lying" if kind == "sleeping" else kind
                if shown is None:
                    continue
                if not (shown in kinds or (lyingish and shown in ("lying", "person_down"))):
                    continue
                if vetoes and any(fn(state, rid, tuple(cell), veto_kind or shown, src) is False for _, _, fn in vetoes):
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

    # ----- geometric camera: once per step (TIER1_INTEGRATION 1.9 rule 5) -------------------------------------
    def note_step(self, view):
        """The engine's once-per-step update from the camera's step view (after camera.end_step; also after reset and
        at the start of a replay), so nothing here depends on how often observe() runs (C5): tracked identity --
        each perceived entity's view level where it is now (Knowledge.note_ident) -- and which people and pets the
        view saw without identifying them (each such sighting gets a new shape handle). No-op in room mode (view
        None)."""
        if view is None:
            return
        self.version += 1
        state = self.env.world.s
        for eid in sorted(view.entities):
            record = state["entities"].get(eid)
            info = self.apparent_view(state, eid, record, view.entities[eid])
            level = VIEW_LEVELS.get(info.get("level"), 0)
            if record is None or level < VIEW_LEVELS["detect"] or not self.geo_detected(state, eid, record, info):
                continue
            self.knowledge.note_ident(eid, record, level)
        keyed = set()
        for table in (view.people, view.pets):
            for aid in sorted(table):
                info = table[aid]
                if VIEW_LEVELS.get(info.get("level"), 0) < VIEW_LEVELS["identify"] and \
                        self.geo_detected(state, aid, None, info):
                    keyed.add(aid)
        for aid in sorted(keyed - self._geo_keyed):
            self._geo_spell[aid] = self._geo_spell.get(aid, 0) + 1
        self._geo_keyed = keyed

    def perceived_in(self, view):
        """Ids of the entities, people and pets a realistic observation of `view` lists (the step view's ViewInfos
        after the detection band): for the sweep's perception.perceived_ids (TIER1_INTEGRATION 1.12)."""
        if view is None:
            return set()
        state = self.env.world.s
        out = set()
        for table, entity in ((view.entities, True), (view.people, False), (view.pets, False)):
            for aid, info in table.items():
                record = state["entities"].get(aid) if entity else None
                if entity and record is not None:
                    info = self.apparent_view(state, aid, record, info)
                if (not entity or record is not None) and self.geo_detected(state, aid, record, info):
                    out.add(aid)
        return out

    def perceived_room(self, state, base, dark):
        """Room camera: the entity ids a realistic observation lists among `base` (the oracle-visible ids observe
        would render; dark: the cameras give no colour view), applying observe's rules in its order: small things
        missed from this viewpoint (and what they hold) drop out, an un-inspected mirror shows as an opening, not a
        thing (for perception.perceived_ids, TIER1_INTEGRATION 1.12)."""
        entities = state["entities"]
        out, missed = set(), set()
        own_ctx = self._room_ctx is None
        if own_ctx:
            self._room_ctx = self.room_context(state)
        try:
            for eid in sorted(base, key=lambda e: len(self._chain(state, e))):
                record = entities.get(eid)
                if record is None:
                    continue
                ctx = self.context(state, eid, record)
                if record.get("location") in missed or (not dark and self.missed(state, eid, record, ctx)):
                    missed.add(eid)
                    continue
                if (record.get("category") == "mirror" or record.get("reflective")) and not dark and \
                        self.knowledge.entity_level(eid, record) < INSPECT:
                    continue
                out.add(eid)
        finally:
            if own_ctx:
                self._room_ctx = None
        return out

    def render_request(self, request, state):
        """obs.request as the robot can tell it (TIER1_SWEEP 10.2): "from" is the person when the robot can tell who
        it is -- a household member or a person it knows (by face, or by voice in person or over the intercom), a
        known contact on the phone or app -- the shape handle (or "person") while the robot's view of the speaker is
        dark, "unfamiliar person" for a visitor it does not know, "unknown caller" for a stranger on the phone, "note"
        for a written request (with the note's handle). A relayed request is said by the relayer: "from" is decided
        for the relayer, and "claims_from" (the person the relayer says it comes from, as heard) stays as said.
        Decided the first time the request is shown and kept."""
        out = dict(request)
        rid = str(out.get("id"))
        decided = self._request_from.get(rid)
        if decided is None:
            decided = self._request_sender(out, state)
            self._request_from[rid] = decided
        out.update(decided)
        return out

    def request_sender(self, request_id, state):
        """The sender decision of a request by its record id ({"from", "from_label"}, decided once, as obs.request
        shows it), or None for an unknown request: the speaker of the request's own message in obs.messages."""
        decided = self._request_from.get(str(request_id))
        if decided is None:
            record = next((r for r in state.get("requests") or () if isinstance(r, dict)
                           and str(r.get("id")) == str(request_id)), None)
            if record is None:
                return None
            from .perception import request_view
            shown = request_view(dict(state, requests=[record], phase={}))
            decided = self._request_sender(shown or record, state)
            self._request_from[str(request_id)] = decided
        return decided

    def _request_sender(self, request, state):
        pid = request.get("from")
        channel = request.get("channel")
        people = state.get("people") or {}
        person = people.get(pid) if isinstance(pid, str) else None
        if pid is None or person is None:
            return {"from": pid, "from_label": request.get("from_label")}
        known = str(person.get("role") or "adult") not in VISITOR_ROLES or \
            bool(self.knowledge.data.get(pid, {}).get("level"))
        label = person.get("label") or pid
        if channel == "note":
            note = request.get("note")
            out = {"from": "note", "from_label": None}
            if isinstance(note, str) and note in state.get("entities", {}):
                out["note"] = self.handles.handle(note)
            return out
        if channel in ("phone", "app", "text"):
            return {"from": pid, "from_label": label} if known else {"from": "unknown caller", "from_label": None}
        dark = self._dark_now if self._dark_now is not None else self._dark_agents(state)
        if pid in dark:
            return {"from": dark[pid], "from_label": "person"}
        robot_room = (state.get("robot") or {}).get("room")
        if person.get("location") == robot_room and self.env.perception.seen_dark(state, robot_room):
            return {"from": "person", "from_label": "person"}
        if not known:
            return {"from": "unfamiliar person", "from_label": "unfamiliar person"}
        return {"from": pid, "from_label": label}

    @staticmethod
    def _render_notice(notice):
        """A stored notice as the robot reads it: its time in whole seconds (the realistic clock)."""
        out = dict(notice)
        t = out.get("t")
        if isinstance(t, float):
            out["t"] = float(int(t + 0.5)) if t >= 0 else -float(int(-t + 0.5))
        return out

    # ----- persistence -----------------------------------------------------------------------------------
    def dump(self):
        out = {"handles": self.handles.dump(), "knowledge": self.knowledge.dump()}
        if self._request_from:
            out["requests"] = copy.deepcopy(self._request_from)
        if self.gone:
            out["gone"] = copy.deepcopy(self.gone)
        if self._spell or self._last_view is not None:
            out["dark"] = {"spell": self._spell, "view": list(self._last_view) if self._last_view is not None else None}
        if getattr(self, "_agent_said", None):
            out["said"] = list(self._agent_said)
        if self._geo_spell or self._geo_keyed:
            out["geo"] = {"spell": dict(sorted(self._geo_spell.items())), "keyed": sorted(self._geo_keyed)}
        return out

    def load(self, data):
        self.handles.load((data or {}).get("handles"))
        self.knowledge.load((data or {}).get("knowledge"))
        self.gone = copy.deepcopy((data or {}).get("gone") or {})
        dark = (data or {}).get("dark") or {}
        self._spell = int(dark.get("spell") or 0)
        self._last_view = tuple(dark["view"]) if dark.get("view") else None
        self._agent_said = list((data or {}).get("said") or [])
        geo = (data or {}).get("geo") or {}
        self._geo_spell = {str(k): int(v) for k, v in (geo.get("spell") or {}).items()}
        self._geo_keyed = set(geo.get("keyed") or ())
        self._request_from = copy.deepcopy((data or {}).get("requests") or {})
        self._ever = set(self.env.world.s["entities"]) | {e for e in self.handles.by_id if not e.startswith("shape:")} | set(self.gone)
        self.version += 1
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


def _lens(state):
    """Lens clarity as the body reports it (body.sensors_view(state)["clarity"]: camera_clean capped by lens
    scratches)."""
    return body.clarity(state)


def read_tier_fields(registry, record):
    """Fields legible by reading for this record (kernel defaults, domain field_tiers and the type's perception
    blocks), usable in both perception modes."""
    fields = set(DEFAULT_TIERS["read"])
    # domain-wide field_tiers are deliberately not used here: printed text belongs to the object's type -- or to
    # this entity, when a domain wrote legible text onto it and said so in its own _perception "read" list. A type's
    # "hidden" list removes a default read field (a prescription bottle's patient id is the type's hidden truth, not
    # print: people K-READ-HIDDEN); ancestors first, the type last, as realism's tier table applies them
    type_name = record.get("type")
    if type_name in getattr(registry, "types", {}):
        perception = getattr(registry, "perception", {})
        for name in reversed([type_name] + registry.ancestors(type_name)):
            spec = perception.get(name) or {}
            fields |= set(spec.get("read", []))
            fields -= set(spec.get("hidden", []))
    own = record.get("_perception")
    if isinstance(own, dict):
        fields |= {f for f in own.get("read") or () if isinstance(f, str) and "." not in f}
        fields -= {f for f in own.get("hidden") or () if isinstance(f, str)}
    return fields


def _declared_faces(state, eid):
    """Names of an entity's declared faces (view.faces and label_facing; sight 4.6): face gating applies only to
    these."""
    faces = getattr(sight, "_faces", None)
    try:
        return frozenset(faces(state, eid)) if faces is not None else frozenset()
    except Exception:  # noqa: BLE001 -- a malformed view block gates nothing
        return frozenset()


def _light_m(light):
    """The light term of the uncertainty m (TIER1_CAMERA 11.2): 1.5 below 0.3, 0.5 below 0.45, else 0."""
    return 1.5 if light < 0.3 else 0.5 if light < 0.45 else 0.0


def _step_round(value, step):
    """value rounded to a multiple of step (never -0.0)."""
    return float(round(float(value) / step) * step) + 0.0


def _sig2(value):
    """value to 2 significant figures."""
    return float("{:.2g}".format(float(value))) + 0.0


def _view_dist(d):
    for limit, word in VIEW_DIST:
        if d <= limit + 1e-9:
            return word
    return "very far"


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


def _novel_identity(ident):
    """An identity the kernel renders for an unfamiliar thing (Realism._novel): kind "unknown" with a shape word."""
    return ident.get("kind") == "unknown" and "shape" in ident


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
