"""Shared textile model: fabrics, care labels, stains, damage bookkeeping and placement context.

Fidelity: lumped-parameter approximations (P-approx) of real laundry behaviour. Magnitudes
come from common care practice (ISO 3758 / ASTM D5489 care symbols, appliance manuals):
    * wash temperatures 20-95 C, wool felts above ~30 C with agitation, cotton shrinks 3-5 %
      at 60 C+, protein stains set above ~40 C, heat sets any remaining stain;
    * spin leaves 35-65 % of saturation, air drying a T-shirt takes ~3 h indoors;
    * damp laundry smells after ~8-12 h bunched or enclosed and grows mildew after ~24 h.
Every function here is pure or mutates only through the journaled World API.
"""
import math

from ...kernel import clock, geometry
from ...kernel.predicates import root_room

DRY = 0.05          # wetness below which a textile counts as dry (fold/store OK)
DAMP = 0.15         # above this, bunched/enclosed textiles start to sour
DRIPPING = 0.65     # above this, hung textiles drip
STAIN_DONE = 0.05   # a stain below this level is invisible

# ---------------------------------------------------------------------------
# fabrics
# wrinkle: propensity 0..1; iron_c: correct soleplate C; scorch_c: damage threshold without a
# press cloth; melts: thermoplastic; felts: wool-type felting; shrink_c / shrink_max: hot-water
# shrinkage; tumble: physical dryer tolerance; dry_factor: relative evaporation speed;
# lint_shed / lint_magnet: lint exchange in a load; water_ok: may be immersed.

FABRICS = {
    "cotton": dict(wrinkle=0.7, iron_c=200, scorch_c=230, shrink_c=60, shrink_max=0.05, tumble="high", lint_shed=0.5, lint_magnet=0.2, dry_factor=1.0),
    "linen": dict(wrinkle=1.0, iron_c=220, scorch_c=245, shrink_c=60, shrink_max=0.04, tumble="medium", lint_shed=0.3, lint_magnet=0.2, dry_factor=1.3),
    "denim": dict(wrinkle=0.4, iron_c=200, scorch_c=230, shrink_c=60, shrink_max=0.04, tumble="high", lint_shed=0.4, lint_magnet=0.3, dry_factor=0.55),
    "canvas": dict(wrinkle=0.4, iron_c=200, scorch_c=230, shrink_c=60, shrink_max=0.04, tumble="medium", lint_shed=0.3, lint_magnet=0.2, dry_factor=0.6),
    "terry": dict(wrinkle=0.2, iron_c=200, scorch_c=230, shrink_c=70, shrink_max=0.04, tumble="high", lint_shed=1.0, lint_magnet=0.2, dry_factor=0.6),
    "flannel": dict(wrinkle=0.5, iron_c=180, scorch_c=220, shrink_c=50, shrink_max=0.06, tumble="medium", lint_shed=0.7, lint_magnet=0.4, dry_factor=0.8),
    "wool": dict(wrinkle=0.3, iron_c=150, scorch_c=175, shine_c=150, felts=True, shrink_c=30, shrink_max=0.25, tumble="none", lint_shed=0.3, lint_magnet=0.6, dry_factor=0.45, knit=True),
    "cashmere": dict(wrinkle=0.3, iron_c=140, scorch_c=165, shine_c=140, felts=True, shrink_c=30, shrink_max=0.3, tumble="none", lint_shed=0.2, lint_magnet=0.6, dry_factor=0.45, knit=True, delicate=True),
    "silk": dict(wrinkle=0.6, iron_c=110, scorch_c=150, water_spots=True, shrink_c=40, shrink_max=0.08, tumble="none", slippery=True, lint_magnet=0.3, dry_factor=1.4, delicate=True),
    "satin": dict(wrinkle=0.4, iron_c=120, scorch_c=160, melts=True, tumble="low", slippery=True, lint_magnet=0.3, dry_factor=1.5, delicate=True),
    "lace": dict(wrinkle=0.2, iron_c=110, scorch_c=150, melts=True, tumble="none", lint_magnet=0.2, dry_factor=1.6, delicate=True),
    "polyester": dict(wrinkle=0.15, iron_c=150, scorch_c=180, melts=True, shrink_c=80, shrink_max=0.02, tumble="medium", lint_magnet=0.4, dry_factor=1.6),
    "nylon": dict(wrinkle=0.2, iron_c=135, scorch_c=170, melts=True, tumble="low", slippery=True, lint_magnet=0.4, dry_factor=1.7),
    "acrylic": dict(wrinkle=0.2, iron_c=135, scorch_c=160, melts=True, tumble="low", pills=True, lint_magnet=0.5, dry_factor=1.3, knit=True),
    "spandex_blend": dict(wrinkle=0.1, iron_c=120, scorch_c=150, melts=True, elastic=True, tumble="low", lint_magnet=0.4, dry_factor=1.5),
    "rayon": dict(wrinkle=0.8, iron_c=150, scorch_c=190, shrink_c=20, shrink_max=0.1, agitation_shrink=True, tumble="none", lint_magnet=0.3, dry_factor=1.0),
    "fleece": dict(wrinkle=0.05, iron_c=110, scorch_c=150, melts=True, tumble="low", lint_shed=0.4, lint_magnet=0.9, dry_factor=1.4),
    "microfiber": dict(wrinkle=0.05, iron_c=110, scorch_c=150, melts=True, tumble="low", lint_magnet=1.0, no_softener=True, dry_factor=1.6),
    "velvet": dict(wrinkle=0.5, iron_c=0, scorch_c=140, nap=True, tumble="low", lint_magnet=0.8, dry_factor=0.8, delicate=True),
    "corduroy": dict(wrinkle=0.4, iron_c=180, scorch_c=220, nap=True, tumble="medium", lint_magnet=0.7, dry_factor=0.7),
    "down": dict(wrinkle=0.0, iron_c=0, scorch_c=120, tumble="low", dry_factor=0.25, heat_damage_c=62),
    "peva": dict(wrinkle=0.1, iron_c=0, scorch_c=90, melts=True, tumble="none", dry_factor=3.0, heat_damage_c=45),
    "vinyl": dict(wrinkle=0.1, iron_c=0, scorch_c=90, melts=True, tumble="none", dry_factor=3.0, heat_damage_c=45),
    "rubber_backed": dict(wrinkle=0.1, iron_c=0, scorch_c=150, tumble="low", lint_shed=0.6, dry_factor=0.6, heat_damage_c=55, bleach_sensitive=True),
    "memory_foam": dict(wrinkle=0.0, iron_c=0, scorch_c=150, tumble="none", dry_factor=0.15, water_ok=False, machine_ok=False, heat_damage_c=50),
    "foam": dict(wrinkle=0.0, iron_c=0, scorch_c=150, tumble="none", dry_factor=0.15, machine_ok=False, heat_damage_c=50, combustible=True),
    "coir": dict(wrinkle=0.0, iron_c=0, scorch_c=200, tumble="none", dry_factor=0.5, water_ok=False, machine_ok=False),
    "jute": dict(wrinkle=0.0, iron_c=0, scorch_c=200, tumble="none", dry_factor=0.5, water_ok=False, machine_ok=False),
    "leather": dict(wrinkle=0.0, iron_c=0, scorch_c=120, tumble="none", dry_factor=0.4, water_ok=False, machine_ok=False, heat_damage_c=50),
    "suede": dict(wrinkle=0.0, iron_c=0, scorch_c=120, tumble="none", dry_factor=0.4, water_ok=False, machine_ok=False, water_marks=True, heat_damage_c=50),
    "patent_leather": dict(wrinkle=0.0, iron_c=0, scorch_c=120, tumble="none", dry_factor=2.0, water_ok=True, machine_ok=False, heat_damage_c=50),
    "mesh_synthetic": dict(wrinkle=0.0, iron_c=0, scorch_c=150, melts=True, tumble="none", dry_factor=1.2, heat_damage_c=55),
    "rubber": dict(wrinkle=0.0, iron_c=0, scorch_c=150, tumble="none", dry_factor=3.0, heat_damage_c=65),
    "pu_membrane": dict(wrinkle=0.1, iron_c=0, scorch_c=120, melts=True, tumble="low", dry_factor=1.0, heat_damage_c=60, bleach_sensitive=True),
    "polypropylene": dict(wrinkle=0.1, iron_c=0, scorch_c=140, melts=True, tumble="none", dry_factor=2.0, heat_damage_c=50, machine_ok=False),
    "insulated": dict(wrinkle=0.1, iron_c=0, scorch_c=120, melts=True, tumble="none", dry_factor=0.8, heat_damage_c=50, machine_ok=False),
}
DEFAULT_FABRIC = "cotton"

TUMBLE_RANK = {"none": 0, "no_heat": 0, "low": 1, "medium": 2, "high": 3}
GENTLE_AGITATION = {0: 1.0, 1: 0.55, 2: 0.3}
HAND_AGITATION = 0.16


LAUNDERABLE_TOOLS = {"microfiber_cloth", "cotton_rag", "cloth", "rag", "dish_cloth", "mop_pad", "steam_mop_pad", "dust_mop_pad"}


def fabric_name(rec):
    name = rec.get("fabric")
    if name in FABRICS:
        return name
    material = rec.get("material")
    if material in FABRICS:
        return material
    return DEFAULT_FABRIC


def fabric(rec):
    return FABRICS[fabric_name(rec)]


def care(rec):
    value = rec.get("care")
    return value if isinstance(value, dict) else {}


def is_textile(rec):
    """Our textiles, plus other domains' launderable things (cleaning cloths, mop pads, flagged bags)."""
    return bool(rec.get("textile") or rec.get("launderable") or rec.get("tool_kind") in LAUNDERABLE_TOOLS or rec.get("category") == "rug")


def is_umbrella(rec):
    return bool(rec.get("umbrella") or rec.get("type") == "rain_umbrella" or "rain_umbrella" in (rec.get("is_a") or []))


DOMAIN = "textiles_laundry"


def dried_elsewhere(rec):
    """Another domain evolves this record's wetness (CONVENTIONS 5 `drying_owner`): admin_inventory's storage system
    dries umbrellas and luggage-category bags, and any record whose declared `drying_owner` is another domain is
    left to it (this domain then only adds drips). Do not double-process them."""
    owner = rec.get("drying_owner")
    if owner is not None and owner != DOMAIN:
        return True
    return rec.get("type") == "rain_umbrella" or "rain_umbrella" in (rec.get("is_a") or []) or rec.get("category") == "luggage" or bool(rec.get("dries_in_air"))


def is_fabric_candidate(rec):
    """STATIC predicate (type/flags fixed at spawn) for the fabric system's index: textiles and umbrellas."""
    return is_textile(rec) or is_umbrella(rec)


def textile_ids(w):
    """Sorted ids of every textile/umbrella (cached by the kernel until an entity is created or destroyed)."""
    return w.static_index("textiles_laundry.textiles", is_fabric_candidate)


HEATER_CATEGORIES = ("space_heater", "heater", "radiator", "fireplace", "fire_pit", "wood_stove")


def is_heater_like(rec):
    """STATIC predicate: things that can heat a textile near them (space heaters, radiators, fireplaces, fire pits,
    wood stoves, burners and other `heat_source` appliances). Whether one is active is dynamic (see heat_sources)."""
    return bool(rec.get("heat_source") or rec.get("category") in HEATER_CATEGORIES)


def heater_active(rec):
    return bool(rec.get("on") or rec.get("lit") or rec.get("burning")
                or (rec.get("category") == "radiator" and float(rec.get("temperature_c", 21) or 21) > 45))


# ---------------------------------------------------------------------------
# odor (CONVENTIONS 5: `odor` is a dict {kind: intensity}). Every domain that reads entity odor now accepts the dict
# (cleaning's room smells and waste system read it with `state.odor_level`), so this domain writes the dict form and
# keeps `odor_kind` (the strongest kind) as a convenience for inspection. Readers here still accept the legacy float
# (+ `odor_kind`) that older scenario setups write; `normalize_odor` converts it at episode start.

DICT_ODOR = True


def odor_level(rec, kind=None):
    value = (rec or {}).get("odor")
    if isinstance(value, dict):
        if kind is not None:
            return float(value.get(kind, 0.0) or 0.0)
        return max([float(v or 0.0) for v in value.values() if isinstance(v, (int, float))] or [0.0])
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if kind is not None and (rec or {}).get("odor_kind") not in (None, kind):
            return 0.0
        return float(value)
    return 0.0


def _odor_dict(rec, default_kind="musty"):
    """The record's odor as a dict (a legacy float is read as its `odor_kind`, else `default_kind`)."""
    old = (rec or {}).get("odor")
    if isinstance(old, dict):
        return {k: float(v) for k, v in old.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if isinstance(old, (int, float)) and not isinstance(old, bool) and float(old) > 1e-4:
        return {(rec.get("odor_kind") or default_kind): float(old)}
    return {}


def _write_odor(m, out):
    m["odor"] = {k: round(out[k], 3) for k in sorted(out)}
    m["odor_kind"] = max(out.items(), key=lambda kv: (kv[1], kv[0]))[0] if out else None


def set_odor(w, eid, kind, value):
    """Set (value > 0) or clear (value 0) one odor kind on an entity (journaled); other kinds are kept."""
    value = round(max(0.0, min(1.0, float(value))), 3)
    m = w.mut(eid)
    out = _odor_dict(m, kind)
    if value > 1e-4:
        out[kind] = value
    else:
        out.pop(kind, None)
    if DICT_ODOR or isinstance(m.get("odor"), dict):
        _write_odor(m, out)
        return
    if value > 1e-4:
        m["odor"] = value
        m["odor_kind"] = kind
    elif m.get("odor_kind") in (None, kind):
        m["odor"] = 0.0
        m["odor_kind"] = None


def clear_odor(w, eid):
    """A thorough wash removes every odor kind."""
    m = w.mut(eid)
    m["odor"] = {} if (DICT_ODOR or isinstance(m.get("odor"), dict)) else 0.0
    m["odor_kind"] = None


def normalize_odor(w, eid):
    """Convert a legacy numeric `odor` (+ `odor_kind`) on one of this domain's records to the CONVENTIONS dict."""
    rec = w.s["entities"].get(eid) or {}
    old = rec.get("odor")
    if DICT_ODOR and isinstance(old, (int, float)) and not isinstance(old, bool):
        _write_odor(w.mut(eid), _odor_dict(rec))


PRODUCT_AGENTS = {"dish_soap": ["surfactant"], "isopropyl_alcohol": ["solvent"], "rubbing_alcohol": ["solvent"], "bleach": ["chlorine"],
                  "oxygen_bleach": ["oxygen"], "vinegar": ["acid"], "enzyme_cleaner": ["enzyme"], "laundry_detergent": ["surfactant", "enzyme"],
                  "all_purpose": ["surfactant"], "degreaser": ["surfactant", "solvent"], "ammonia": ["ammonia"]}
TAG_AGENTS = {"chem:chlorine": "chlorine", "chem:acid": "acid", "chem:surfactant": "surfactant", "chem:solvent": "solvent",
              "chem:solvent_light": "solvent", "chem:enzyme": "enzyme", "chem:ammonia": "ammonia"}


def stain_agents(rec):
    """Stain-treatment chemistry of a product: our stain_agent list, else cleaning's product key / chem: tags."""
    agents = rec.get("stain_agent")
    if agents:
        return [agents] if isinstance(agents, str) else list(agents)
    found = list(PRODUCT_AGENTS.get(rec.get("product") or "", []))
    tags = rec.get("tags") or []
    for tag in tags:
        agent = TAG_AGENTS.get(tag)
        if agent and agent not in found:
            found.append(agent)
    if "chem:oxidizer" in tags and "chlorine" not in found and "oxygen" not in found:
        found.append("oxygen")
    return found


def is_knit(rec):
    if "knit" in rec:
        return bool(rec["knit"])
    return bool(fabric(rec).get("knit"))


def wrinkle_prone(rec):
    return float(rec.get("wrinkle_propensity", fabric(rec).get("wrinkle", 0.5)))


def max_agitation(rec):
    c = care(rec)
    if c.get("wash") == "hand":
        return HAND_AGITATION
    return GENTLE_AGITATION.get(int(c.get("gentle", 0) or 0), 1.0)


def max_tumble_rank(rec):
    """Strictest of the care label and the fabric's physical tolerance."""
    c = care(rec)
    label = c.get("tumble", fabric(rec).get("tumble", "medium"))
    if c.get("dry") in ("flat", "line", "drip") and "tumble" not in c:
        label = "none"
    return min(TUMBLE_RANK.get(label, 2), TUMBLE_RANK.get(fabric(rec).get("tumble", "high"), 3))


# ---------------------------------------------------------------------------
# care labels (ISO/ASTM wording) -- the structured dict stays secret; the text is readable

WASH_WORDS = {20: "COLD 20C/68F", 30: "COLD 30C/86F", 40: "WARM 40C/104F", 50: "WARM 50C/122F", 60: "HOT 60C/140F",
              70: "HOT 70C/158F", 95: "VERY HOT 95C/203F"}


def care_label_text(rec):
    c = care(rec)
    if not c:
        return None
    parts = []
    content = rec.get("fiber_content") or fabric_name(rec).replace("_", " ").upper()
    parts.append(str(content).upper())
    wash = c.get("wash", "machine")
    if wash == "dry_clean":
        parts.append("DRY CLEAN ONLY")
    elif wash == "none":
        parts.append("DO NOT WASH - SPOT CLEAN ONLY")
    else:
        limit = int(c.get("wash_max_c", 40))
        word = WASH_WORDS.get(limit) or "MAX {}C".format(limit)
        parts.append(("HAND WASH " if wash == "hand" else "MACHINE WASH ") + word)
        gentle = int(c.get("gentle", 0) or 0)
        if wash != "hand" and gentle == 1:
            parts.append("PERMANENT PRESS CYCLE")
        elif wash != "hand" and gentle >= 2:
            parts.append("GENTLE/DELICATE CYCLE")
    bleach = c.get("bleach", "non_chlorine")
    parts.append({"any": "BLEACH WHEN NEEDED", "non_chlorine": "ONLY NON-CHLORINE BLEACH",
                  "none": "DO NOT BLEACH"}.get(bleach, "DO NOT BLEACH"))
    tumble = c.get("tumble")
    dry = c.get("dry", "tumble")
    if tumble in (None, "none") or dry in ("flat", "line", "drip"):
        parts.append("DO NOT TUMBLE DRY")
        parts.append({"flat": "DRY FLAT", "line": "LINE DRY", "drip": "DRIP DRY"}.get(dry, "LINE DRY"))
    else:
        parts.append("TUMBLE DRY " + {"high": "NORMAL/HIGH", "medium": "MEDIUM", "low": "LOW"}.get(tumble, "LOW"))
    if c.get("shade"):
        parts.append("DRY IN SHADE")
    iron = c.get("iron", "medium")
    parts.append({"high": "HOT IRON (3 DOTS)", "medium": "WARM IRON (2 DOTS)", "low": "COOL IRON (1 DOT)",
                  "none": "DO NOT IRON"}.get(iron, "DO NOT IRON"))
    if c.get("steam_ok") is False:
        parts.append("DO NOT STEAM")
    if c.get("inside_out"):
        parts.append("WASH AND IRON INSIDE OUT")
    if c.get("separately"):
        parts.append("WASH SEPARATELY - COLOR MAY BLEED")
    elif c.get("like_colors"):
        parts.append("WASH WITH LIKE COLORS")
    for note in c.get("notes", []) or []:
        parts.append(str(note).upper())
    return " | ".join(parts)


def ensure_care_label(w, eid):
    """Fill a readable care-label text from the (secret) care dict when the author omitted it."""
    rec = w.ent(eid)
    if care(rec) and not rec.get("care_label") and rec.get("has_care_label", True):
        w.mut(eid)["care_label"] = care_label_text(rec)


# ---------------------------------------------------------------------------
# stains and soil

STAIN_CLASS = {
    "stain_blood": "protein", "stain_egg": "protein", "stain_milk": "protein", "stain_sweat": "protein",
    "stain_vomit": "protein", "biohazard": "protein", "stain_wine": "tannin", "stain_coffee": "tannin",
    "stain_tea": "tannin", "stain_juice": "tannin", "stain_oil": "grease", "stain_food": "grease",
    "grease": "grease", "food": "grease", "sticky": "grease", "stain_sunscreen": "grease", "stain_makeup": "grease",
    "stain_softener": "grease", "stain_lipbalm": "grease", "stain_ink": "ink", "stain_grass": "grass", "wax": "wax",
    "stain_wax": "wax", "stain_crayon": "wax", "stain_polish": "wax", "stain_dye": "dye", "mud": "particulate",
    "dirt": "particulate", "dust": "particulate", "sand": "particulate", "crumbs": "particulate", "salt": "particulate",
    "ash": "particulate", "pollen": "particulate", "leaves": "particulate", "litter": "particulate", "mildew": "mildew",
    "mold": "mildew", "lint": "fiber", "pet_hair": "fiber", "hair": "fiber", "detergent_residue": "residue",
    "bird_droppings": "protein", "stain_rust": "rust", "rust": "rust", "soap_scum": "residue", "body": "body",
}
VISIBLE_STAIN_CLASSES = {"protein", "tannin", "grease", "ink", "grass", "wax", "dye", "mildew", "rust"}

# agent -> stain class -> fraction removed by a correct pretreatment (before washing)
AGENT_EFFICACY = {
    "enzyme": {"protein": 0.9, "grass": 0.85, "grease": 0.35, "tannin": 0.5, "ink": 0.1, "particulate": 0.5, "dye": 0.1, "body": 0.8},
    "oxygen": {"tannin": 0.92, "protein": 0.5, "grass": 0.6, "mildew": 0.6, "dye": 0.45, "grease": 0.3, "rust": 0.2},
    "surfactant": {"grease": 0.9, "particulate": 0.6, "protein": 0.4, "tannin": 0.4, "grass": 0.4, "ink": 0.2, "body": 0.7, "wax": 0.2},
    "solvent": {"ink": 0.9, "grease": 0.6, "wax": 0.45, "dye": 0.2},
    "chlorine": {"mildew": 0.95, "tannin": 0.9, "protein": 0.7, "grass": 0.7, "dye": 0.8},
    "acid": {"rust": 0.7, "residue": 0.8, "particulate": 0.2},
    "water": {"protein": 0.3, "tannin": 0.25, "particulate": 0.3, "grease": 0.05, "body": 0.3},
}


def stain_class(kind):
    if kind in STAIN_CLASS:
        return STAIN_CLASS[kind]
    if kind.startswith("stain_"):
        return "dye"
    return "particulate"


def is_stain(kind):
    return stain_class(kind) in VISIBLE_STAIN_CLASSES or kind.startswith("stain_")


def soil_level(rec, kind):
    soil = rec.get("soil")
    return float(soil.get(kind, 0.0)) if isinstance(soil, dict) else 0.0


def stains(rec, threshold=STAIN_DONE):
    soil = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
    return sorted(k for k, v in soil.items() if is_stain(k) and float(v) >= threshold)


def set_soil(w, eid, kind, value):
    rec = w.mut(eid)
    soil = rec.get("soil")
    if not isinstance(soil, dict):
        soil = rec["soil"] = {}
    value = round(max(0.0, min(1.0, float(value))), 4)
    if value <= 0.0005:
        soil.pop(kind, None)
    else:
        soil[kind] = value


def add_soil(w, eid, kind, amount):
    rec = w.ent(eid)
    set_soil(w, eid, kind, soil_level(rec, kind) + float(amount))


def soil_total(rec):
    soil = rec.get("soil")
    return sum(float(v) for v in soil.values()) if isinstance(soil, dict) else 0.0


def is_dirty(rec):
    """Worn since the last wash, or visibly soiled."""
    if rec.get("worn"):
        return True
    soil = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
    return any(float(v) >= 0.1 for k, v in soil.items() if k not in ("lint", "pet_hair", "hair"))


def set_stain(w, eid, kinds, why):
    """Heat sets the given stains permanently (they stay in soil, washing no longer removes them)."""
    rec = w.ent(eid)
    already = list(rec.get("set_stains") or [])
    fresh = [k for k in kinds if k not in already and soil_level(rec, k) >= STAIN_DONE]
    if not fresh:
        return []
    w.mut(eid)["set_stains"] = sorted(set(already) | set(fresh))
    add_damage(w, eid, "set_stain", 1.0, why=why)
    return fresh


# ---------------------------------------------------------------------------
# damage

def add_damage(w, eid, kind, amount=1.0, why=None, stat=True):
    """Record textile damage; the first occurrence of a kind on an item counts as household damage."""
    rec = w.mut(eid)
    dmg = rec.get("damage")
    if not isinstance(dmg, dict):
        # CONVENTIONS 5: damage is a dict {kind: severity}; convert a legacy list/string instead of dropping it
        legacy = dmg if isinstance(dmg, (list, tuple)) else ([dmg] if isinstance(dmg, str) and dmg else [])
        dmg = rec["damage"] = {str(k): 1.0 for k in legacy}
    new = kind not in dmg
    dmg[kind] = round(min(1.0, float(dmg.get(kind, 0.0)) + max(0.0, float(amount))), 4)
    if new:
        if stat:
            w.stat("damage")
        w.emit("textile_damage", target=eid, damage=kind, why=why or kind)
    return dmg[kind]


def add_shrink(w, eid, fraction, why):
    if fraction <= 0:
        return 0.0
    rec = w.ent(eid)
    total = round(min(0.4, float(rec.get("shrunk", 0.0) or 0.0) + fraction), 4)
    m = w.mut(eid)
    m["shrunk"] = total
    if total >= 0.03:
        add_damage(w, eid, "shrink", 0.0, why=why)
        m = w.mut(eid)
        m["damage"]["shrink"] = total
    return total


def destroy_textile(w, eid, why):
    add_damage(w, eid, "destroyed", 1.0, why=why)
    w.mut(eid)["broken"] = True


# ---------------------------------------------------------------------------
# fasteners (shared convention with manipulation: zipper {closed_frac}, fasteners {name: {engaged}})

def zipper_open(rec):
    """True when the item's zipper is (mostly) open."""
    z = rec.get("zipper")
    if isinstance(z, dict):
        return float(z.get("closed_frac", 1.0) or 0.0) < 0.9
    return bool(rec.get("has_zipper")) and rec.get("zipped") is False


def zipper_closed(rec):
    z = rec.get("zipper")
    if isinstance(z, dict):
        return float(z.get("closed_frac", 1.0) or 0.0) >= 0.9
    return bool(rec.get("zipped"))


def set_zipper(w, eid, closed):
    m = w.mut(eid)
    m["zipper"] = dict(m.get("zipper") or {}, closed_frac=1.0 if closed else 0.0, meshed=bool(closed))
    m.pop("zipped", None)


def hooks_open(rec):
    """Bra hooks / hook-and-eye left open (snag the load)."""
    f = rec.get("fasteners")
    if isinstance(f, dict) and isinstance(f.get("hooks"), dict):
        return not f["hooks"].get("engaged", True)
    return bool(rec.get("hooks_open") or rec.get("velcro_open"))


def buttons_fastened(rec):
    f = rec.get("fasteners")
    if isinstance(f, dict) and isinstance(f.get("buttons"), dict):
        return bool(f["buttons"].get("engaged"))
    return bool(rec.get("buttons_fastened"))


# ---------------------------------------------------------------------------
# moisture ownership: this domain models textile moisture; see dry.fabric_system

def note_wetness(w, eid):
    """Record that the item's current wetness was produced by this domain's physics (washer, dryer, air drying)."""
    rec = w.ent(eid)
    value = round(float(rec.get("wetness", 0.0) or 0.0), 6)
    if rec.get("_wet_mine") != value or rec.get("_wet_mine_t") is None:
        m = w.mut(eid)
        m["_wet_mine"] = value
        m["_wet_mine_t"] = float(w.s.get("time_s", 0.0))
        if value > 0.0 and m.get("drying_owner") is None:
            m["drying_owner"] = DOMAIN     # CONVENTIONS 5: other domains' drying systems skip it


def foreign_drying(w, eid, rec):
    """True when something other than this domain lowered the item's wetness without the robot acting on it
    (e.g. a generic evaporation model in another domain); such drops are undone so textile drying stays single-sourced."""
    mine = rec.get("_wet_mine")
    if mine is None:
        return False
    wet = float(rec.get("wetness", 0.0) or 0.0)
    if wet >= float(mine) - 1e-4:
        return False
    since = float(rec.get("_wet_mine_t", 0.0))
    times = ((w.s.get("facts") or {}).get("action_times") or {}).get(eid) or {}
    return not any(float(t) >= since - 1e-6 for t in times.values())


# ---------------------------------------------------------------------------
# structure helpers

def now(w):
    return float(w.s.get("time_s", 0.0))


def season(w):
    return clock.season(w.s)


def parent_of(w, eid):
    rec = w.s["entities"].get(eid)
    return rec.get("location") if rec else None


def chain(w, eid):
    """Entity ancestors (nearest first), stopping at rooms/people/pets/robot."""
    result, seen = [], {eid}
    loc = w.s["entities"][eid].get("location")
    while loc in w.s["entities"] and loc not in seen:
        result.append(loc)
        seen.add(loc)
        loc = w.s["entities"][loc].get("location")
    return result


def room_of(w, eid):
    return root_room(w.s, eid)


WOOD_FLOORS = ("hardwood", "hardwood_unsealed", "laminate", "wood", "engineered_wood", "parquet", "bamboo")
WATER_SENSITIVE_FLOORS = WOOD_FLOORS + ("carpet",)


def floor_material(w, rid):
    """A room's floor material: the floor entity's (floor_<rid>, which scenes edit) first, else the room's
    floor_material."""
    floor = w.s["entities"].get("floor_" + rid)
    if floor is not None:
        material = floor.get("material") or floor.get("finish")
        if material and material not in ("fabric",):
            return material
    return (w.s["rooms"].get(rid) or {}).get("floor_material")


def anchor_cell(w, eid):
    """The floor cell under an entity (its top-most room-level ancestor's position)."""
    state = w.s
    current, seen = eid, set()
    while current in state["entities"] and current not in seen:
        seen.add(current)
        rec = state["entities"][current]
        loc = rec.get("location")
        if loc in state["rooms"]:
            return tuple(rec.get("pos") or (0, 0))
        if loc == "robot":
            return tuple(state["robot"].get("pos") or (0, 0))
        for group in ("people", "pets"):
            if loc in state.get(group, {}):
                return tuple(state[group][loc].get("pos") or (0, 0))
        current = loc
    return None


def textile_children(w, parent):
    return [c for c in w.children(parent) if is_textile(w.ent(c))]


def load_of(w, appliance_id):
    """Everything inside an appliance drum except fixed parts (screens, filters) and gasket items."""
    result = []
    stack = [appliance_id]
    while stack:
        current = stack.pop()
        for child in w.children(current):
            rec = w.ent(child)
            if current == appliance_id and rec.get("relation") in ("part", "attached", "behind", "under"):
                continue
            result.append(child)
            stack.append(child)
    return sorted(result)


def is_kind(rec, *names):
    names = set(names)
    return rec.get("type") in names or bool(names & set(rec.get("is_a", []))) or rec.get("textile_kind") in names


def heat_sources(w):
    """Space heaters, radiators, fireplaces, fire pits, burners that are on or burning (static index + dynamic check)."""
    ents = w.s["entities"]
    return [eid for eid in w.static_index("textiles_laundry.heaters", is_heater_like) if eid in ents and heater_active(ents[eid])]


def bed_occupant(w, bed):
    """A person or pet lying in/on a bed (in_bed/on field, or lying on its footprint)."""
    state = w.s
    rec = state["entities"].get(bed, {})
    cells = set(geometry.footprint(rec)) if rec.get("pos") else set()
    for group in ("people", "pets"):
        for aid in sorted(state.get(group, {})):
            agent = state[group][aid]
            if agent.get("in_bed") == bed or agent.get("on") == bed:
                return aid
            if agent.get("location") == rec.get("location") and agent.get("posture") in ("lying", "sleeping") and agent.get("pos") and tuple(agent["pos"]) in cells:
                return aid
    return None


def textile_occupant(w, eid):
    """A pet or person lying on/under a blanket or other textile (on/under field)."""
    state = w.s
    for group in ("people", "pets"):
        for aid in sorted(state.get(group, {})):
            agent = state[group][aid]
            if eid in (agent.get("on"), agent.get("under"), agent.get("covered_by"), agent.get("using")):
                return aid
    return None


def flat_size(rec):
    size = rec.get("flat_size_m") or (rec.get("size_m") or [0.3, 0.3])[:2]
    return float(size[0]), float(size[1])


def person_pref(w, pid, key, default=None):
    person = w.s.get("people", {}).get(pid, {})
    prefs = person.get("preferences") or {}
    return prefs.get(key, default)


# ---------------------------------------------------------------------------
# realistic perception helpers (all False / identity in oracle mode)

SMELL_WORDS = ((0.35, "faint"), (0.7, "noticeable"))


def smell_word(intensity):
    """A nose gives a smell's kind and a rough strength, never a concentration (shared faint/noticeable/strong)."""
    value = float(intensity or 0.0)
    for edge, word in SMELL_WORDS:
        if value < edge:
            return word
    return "strong"


def unresolved(env, eid):
    """Realistic mode: the robot has not identified this thing yet (an unresolved look-alike shows a candidate list,
    a novel object only its looks)."""
    realism = getattr(env, "realism", None)
    if realism is None:
        return False
    rec = env.world.s["entities"].get(eid)
    if rec is None:
        return False
    if rec.get("novel"):
        return True
    return "candidates" in realism.identity(eid, rec, realism.knowledge.entity_level(eid, rec))


def group_varies(env, eid, key):
    """True when the members of eid's look-alike group differ in key(record) (type defaults of every member and its
    sub-types, plus eid's own record): an action whose result depends on key would then tell look-alikes apart, so it
    must wait until the robot has identified the thing."""
    realism = getattr(env, "realism", None)
    if realism is None:
        return False
    rec = env.world.s["entities"].get(eid)
    if rec is None:
        return False
    _, group = realism.group_of(rec)
    if not group or group.get("resolve") == "never":
        return False        # nothing to wait for: a novel thing (or a never-resolving group) is learned by handling it
    reg = env.registry
    names = set()
    for member in group.get("members", []):
        if reg.known(member):
            names.update(reg.names(member))
    seen = {repr(key(rec))}
    for name in sorted(names):
        try:
            seen.add(repr(key(reg.make(name, "nowhere"))))
        except (KeyError, ValueError):
            continue
    return len(seen) > 1


def quantized_seconds(seconds, step=30.0):
    """Durations derived from a hidden continuous quantity (an item's exact flat size, a split's exact length) are
    rounded so the clock does not read the quantity out (realistic mode)."""
    return max(step, round(float(seconds) / step) * step)


def fragrance_free_people(w):
    result = []
    for pid in sorted(w.s.get("people", {})):
        person = w.s["people"][pid]
        prefs = person.get("preferences") or {}
        if prefs.get("fragrance_free") or "fragrance" in (person.get("allergies") or []):
            result.append(pid)
    return result
