"""Shared helpers for the plumbing_liquids domain (all state changes go through the journaled World).

Domain bookkeeping lives in the top-level state section ``plumbing`` (never observed directly):
  plumbing.log            recent domain events (spills, overflows, flushes, plunges, sprays, disposals)
  plumbing.meter_l        cumulative liters through the house water meter
  plumbing.pollution      list of pollution events (chemicals/oil/fertilizer to storm drains or gardens)
  plumbing.drinking       non-potable or alcohol-to-minor events
  plumbing.irrigation     zone run records {zone: [{start_s, end_s, rain}]}
  plumbing.freeze_s       seconds of continuous sub-zero outdoor weather
  plumbing.freeze_dose    frost dose: degree-seconds below 0 C since the last thaw (hose splits, bib bursts)
  plumbing.hints          per-person hints (where they noticed water ...), read by the plumbing answerer

Magnitudes are cited where not obvious; fidelity is P-approx (lumped volumes and flows) unless noted.
"""
import math

from ...kernel import geometry, physics
from ...kernel.plugins import AFTER, SYSTEMS

SECTION = "plumbing"
COLD_SUPPLY_C = 12.0          # municipal cold water, autumn (seasonal 5-20 C)
DEFAULT_HEATER_C = 49.0       # 120 F tank setpoint recommended for scald safety
SCALD_ADULT_C = 49.0          # 2nd degree burn in ~10 min at 49 C, ~3 s at 60 C
SCALD_CHILD_C = 43.0          # paediatric bath guidance: 37-38 C, never above 43 C
FLOOD_L = 4.0                 # liters of water on a floor from one source before it counts as a flood
WATERLIKE = ("water", "filtered_water", "distilled_water", "ice_water", "tap_water")
# realistic perception: liquid the robot poured out of something it had not identified (an unread jug of a look-alike
# group). Wherever that liquid goes (a bucket, a basin, a puddle) its name stays unknown to the robot until the
# receiving thing is emptied: the flag travels with parcels (liquids.take/add) and hides the "liquid" field there.
UNIDENTIFIED = "_unidentified_liquid"
UNIDENTIFIED_HIDES = ("liquid", "product", "hazards")

# name: density kg/L, viscosity class (pour-rate factor), potable, drinkable, alcohol, foam factor (straight pour),
# rank (contamination severity), soil left behind when it dries (soil kind: level per mm), floor hazard of a spill.
LIQUIDS = {
    "water": dict(density=1.0, flow=1.0, potable=True, drink=True),
    "filtered_water": dict(density=1.0, flow=1.0, potable=True, drink=True),
    "distilled_water": dict(density=1.0, flow=1.0, potable=True, drink=True),
    "ice_water": dict(density=1.0, flow=1.0, potable=True, drink=True),
    "soapy_water": dict(density=1.0, flow=1.0, potable=False, rank=2, residue={"streaks": 0.2}),
    "dirty_water": dict(density=1.01, flow=1.0, potable=False, rank=4, residue={"dirt": 0.3, "streaks": 0.3}),
    "mop_water": dict(density=1.01, flow=1.0, potable=False, rank=5, chemical=True, residue={"dirt": 0.3, "streaks": 0.3}),
    "condensate": dict(density=1.0, flow=1.0, potable=False, rank=3),
    "hose_water": dict(density=1.0, flow=1.0, potable=False, rank=1),
    "rainwater": dict(density=1.0, flow=1.0, potable=False, rank=1),
    "toilet_water": dict(density=1.0, flow=1.0, potable=False, rank=8, biohazard=True, residue={"biohazard": 0.6}),
    "sewage": dict(density=1.02, flow=0.9, potable=False, rank=9, biohazard=True, residue={"biohazard": 0.9, "dirt": 0.5}),
    "milk": dict(density=1.03, flow=0.9, potable=True, drink=True, residue={"food": 0.3, "sticky": 0.2}, perishable=True),
    "oat_milk": dict(density=1.02, flow=0.9, potable=True, drink=True, residue={"food": 0.3}, perishable=True),
    "juice": dict(density=1.04, flow=0.9, potable=True, drink=True, sugary=True, residue={"sticky": 0.7}),
    "orange_juice": dict(density=1.04, flow=0.85, potable=True, drink=True, sugary=True, residue={"sticky": 0.7}, solids="pulp"),
    "apple_juice": dict(density=1.04, flow=0.9, potable=True, drink=True, sugary=True, residue={"sticky": 0.7}),
    "lemonade": dict(density=1.04, flow=0.9, potable=True, drink=True, sugary=True, residue={"sticky": 0.6}),
    "iced_tea": dict(density=1.01, flow=1.0, potable=True, drink=True, sugary=True, residue={"sticky": 0.3, "stain_coffee": 0.2}),
    "tea": dict(density=1.0, flow=1.0, potable=True, drink=True, residue={"stain_coffee": 0.3}, solids="leaves"),
    "coffee": dict(density=1.0, flow=1.0, potable=True, drink=True, residue={"stain_coffee": 0.6}),
    "soda": dict(density=1.04, flow=1.0, potable=True, drink=True, sugary=True, foam=0.6, residue={"sticky": 0.8}),
    "sparkling_water": dict(density=1.0, flow=1.0, potable=True, drink=True, foam=0.3),
    "beer": dict(density=1.01, flow=1.0, potable=True, drink=True, alcohol=True, foam=1.0, residue={"sticky": 0.3}),
    "wine": dict(density=0.99, flow=1.0, potable=True, drink=True, alcohol=True, residue={"stain_wine": 0.8, "sticky": 0.2}),
    "sparkling_wine": dict(density=0.99, flow=1.0, potable=True, drink=True, alcohol=True, foam=1.5, residue={"sticky": 0.3}),
    "sangria": dict(density=1.02, flow=1.0, potable=True, drink=True, alcohol=True, sugary=True, residue={"stain_wine": 0.6, "sticky": 0.5}),
    "broth": dict(density=1.01, flow=1.0, potable=True, residue={"food": 0.3, "grease": 0.2}),
    "cooking_oil": dict(density=0.92, flow=0.35, potable=False, grease=True, residue={"grease": 0.9}, hazard="slip"),
    "grease": dict(density=0.9, flow=0.3, potable=False, grease=True, residue={"grease": 1.0}, hazard="slip"),
    "dish_soap": dict(density=1.03, flow=0.15, potable=False, product=True, surfactant=True, residue={"sticky": 0.4}),
    "hand_soap": dict(density=1.02, flow=0.12, potable=False, product=True, surfactant=True, residue={"sticky": 0.4}),
    "foaming_hand_soap": dict(density=1.0, flow=0.9, potable=False, product=True, surfactant=True, residue={"sticky": 0.2}),
    "laundry_detergent": dict(density=1.05, flow=0.2, potable=False, product=True, surfactant=True, residue={"sticky": 0.5}),
    "fabric_softener": dict(density=1.0, flow=0.25, potable=False, product=True, residue={"sticky": 0.4}),
    "bleach": dict(density=1.1, flow=1.0, potable=False, product=True, chemical=True, rank=6, hazard="wet"),
    "drain_cleaner": dict(density=1.3, flow=0.6, potable=False, product=True, chemical=True, rank=7, caustic=True),
    "liquid_fertilizer": dict(density=1.2, flow=0.5, potable=False, product=True, fertilizer=True, rank=3),
    "fertilizer_solution": dict(density=1.0, flow=1.0, potable=False, fertilizer=True, rank=2),
    "vinegar": dict(density=1.01, flow=1.0, potable=True, product=True),
    "rinse_aid": dict(density=1.0, flow=0.5, potable=False, product=True),
    "mixed": dict(density=1.02, flow=0.9, potable=False, rank=1, residue={"sticky": 0.3}),
}
CONTAMINANTS = ("sewage", "toilet_water", "mop_water", "dirty_water", "condensate", "hose_water", "rainwater", "soapy_water")
DRINK_VESSEL_CATEGORIES = ("dish",)


def num(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def rnd(value, nd=4):
    return round(float(value), nd)


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def info(liquid):
    return LIQUIDS.get(liquid or "", LIQUIDS["mixed"] if liquid else {})


def now(w):
    return float(w.s.get("time_s", 0.0))


# ----------------------------------------------------------------------------------
# domain section and logs

def psec(w):
    """Mutable (journaled) plumbing section."""
    sec = w.sec(SECTION)
    sec.setdefault("log", [])
    return sec


def pget(w, key, default=None):
    return (w.s.get(SECTION) or {}).get(key, default)


def plog(w, event, limit=300, **data):
    sec = psec(w)
    entry = {"t": now(w), "event": event}
    entry.update(data)
    sec["log"].append(entry)
    del sec["log"][:-limit]
    w.emit("plumbing_" + event, **{k: v for k, v in data.items() if (isinstance(v, (str, int, float, bool)) or v is None) and k != "kind"})
    return entry


def pcount(w, key, amount=1.0):
    sec = psec(w)
    counts = sec.setdefault("counts", {})
    counts[key] = rnd(num(counts.get(key)) + amount, 4)
    return counts[key]


def is_mine(rec):
    return isinstance(rec.get("plumb"), dict)


def kind(rec):
    return (rec.get("plumb") or {}).get("kind")


# ----------------------------------------------------------------------------------
# cached id lists (kernel v2.1 static indexes: recomputed only when entities are created/destroyed;
# callers re-check dynamic fields on the records). Never mutate the returned lists.

def index(w, name, predicate):
    return w.static_index("plumbing_liquids." + name, predicate)


def mine_ids(w):
    """Sorted ids of entities this domain simulates (the `plumb` marker is a static type prop)."""
    return w.static_index("plumbing_liquids.mine", is_mine)


_KIND_PREDICATES = {}


def kind_ids(w, kind_):
    """Sorted ids of this domain's entities of one plumb.kind (sink, toilet, water_heater, water_meter...)."""
    pred = _KIND_PREDICATES.get(kind_)
    if pred is None:
        def pred(rec, wanted=kind_):
            return isinstance(rec.get("plumb"), dict) and rec["plumb"].get("kind") == wanted
        _KIND_PREDICATES[kind_] = pred
    return w.static_index("plumbing_liquids.kind." + kind_, pred)


def live(w, ids):
    """Ids from a cached list that still exist (an entity may be destroyed mid-tick)."""
    entities = w.s["entities"]
    return [eid for eid in ids if eid in entities]


# ----------------------------------------------------------------------------------
# system context: floor water produced by the time systems (pre-existing leaks, a running tap the scenario left on)
# is accounted as flood/floor_water per source, but it is not a harm the robot caused, so it never counts in the
# global `spills` flag (kernel v2.1: spills is task-failing; only genuinely harmful events count).

_CONTEXT = {"system": 0}


class system_context(object):
    def __enter__(self):
        _CONTEXT["system"] += 1
        return self

    def __exit__(self, *exc):
        _CONTEXT["system"] -= 1
        return False


def in_system():
    return _CONTEXT["system"] > 0


# ----------------------------------------------------------------------------------
# rooms, cells, agents

def room_of(w, eid):
    if eid in w.s["rooms"]:
        return eid
    return w.root_room(eid)


def cell_of(w, eid):
    """Floor cell under an entity (its anchor footprint), else the robot's cell."""
    if eid in w.s["rooms"]:
        return list(w.robot.get("pos") or [0, 0])
    room, cells = geometry.anchor(w.s, eid)
    if cells:
        return [int(cells[0][0]), int(cells[0][1])]
    return list(w.robot.get("pos") or [0, 0])


def is_outdoor(w, rid):
    return bool((w.s["rooms"].get(rid) or {}).get("outdoor"))


def floor_material(w, rid):
    room = w.s["rooms"].get(rid) or {}
    if room.get("floor_material"):
        return room["floor_material"]
    for eid in sorted(w.children(rid)):
        rec = w.ent(eid)
        if rec.get("category") == "floor":
            return rec.get("material", "wood")
    return "tile"


def floor_entity(w, rid):
    for eid in sorted(w.children(rid)):
        if w.ent(eid).get("category") == "floor":
            return eid
    return None


def people_in(w, rid):
    return sorted(pid for pid, p in w.s.get("people", {}).items() if p.get("location") == rid)


def using(person, eid):
    value = person.get("using")
    if isinstance(value, list):
        return eid in value
    return value == eid


def users_of(w, eid):
    return sorted(pid for pid, p in w.s.get("people", {}).items() if using(p, eid))


def is_minor(person):
    if person.get("role") in ("child", "toddler", "infant", "teen"):
        return True
    age = person.get("age")
    return isinstance(age, (int, float)) and age < 21


def is_young_child(person):
    if person.get("role") in ("child", "toddler", "infant"):
        age = person.get("age")
        return not isinstance(age, (int, float)) or age < 10
    age = person.get("age")
    return isinstance(age, (int, float)) and age < 6


_PRESENT = {}


def system_present(name):
    key = (name, len(SYSTEMS))
    if key not in _PRESENT:
        _PRESENT[key] = any(s[1] == name for s in SYSTEMS)
    return _PRESENT[key]


def after_hook_present(action, module_prefix):
    return any(label.startswith(module_prefix) for _, label, _ in AFTER.get(action, []))


def household_rules(w):
    household = w.s.get("household") or {}
    rules = household.get("rules")
    names = set()
    if isinstance(rules, list):
        for r in rules:
            if isinstance(r, str):
                names.add(r)
            elif isinstance(r, dict) and r.get("name"):
                names.add(r["name"])
    elif isinstance(rules, dict):
        names |= {k for k, v in rules.items() if v}
    return names


# ----------------------------------------------------------------------------------
# water accounting

def use_water(w, liters, meter=True):
    """Draw liters from the municipal supply (utilities.water_l) and count them on the meter."""
    liters = max(0.0, float(liters))
    if liters <= 0:
        return 0.0
    u = w.sec("utilities")
    u["water_l"] = round(max(0.0, num(u.get("water_l"), 1e5) - liters), 3)
    if meter:
        sec = psec(w)
        sec["meter_l"] = rnd(num(sec.get("meter_l")) + liters, 3)
    return liters


def supply_ok(w):
    u = w.s.get("utilities", {})
    return bool(u.get("water", True)) and bool(u.get("main_water_open", True)) and num(u.get("water_l"), 1e9) > 0


# ----------------------------------------------------------------------------------
# spills, wet floors, damage

def wet_cell(w, rid, cell):
    if rid not in w.s["rooms"] or cell is None:
        return
    room = w.s["rooms"][rid]
    key = [int(cell[0]), int(cell[1])]
    if not geometry.in_grid(room, key):
        return
    if key in (room.get("wet_cells") or []):
        return
    rm = w.room_m(rid)
    rm.setdefault("wet_cells", []).append(key)


def mark_unidentified(w, eid, on=True):
    """Flag (or clear) liquid of unknown identity in eid: per-entity perception tiers (kernel v2.7 `_perception`)
    hide its liquid/product name and hazard classes from the robot. Oracle mode ignores `_perception`."""
    rec = w.ent(eid)
    own = rec.get("_perception") if isinstance(rec.get("_perception"), dict) else {}
    hidden = [f for f in (own.get("hidden") or []) if f not in UNIDENTIFIED_HIDES]
    if on:
        hidden += list(UNIDENTIFIED_HIDES)
    new = dict(own)
    if hidden:
        new["hidden"] = hidden
    else:
        new.pop("hidden", None)
    if bool(rec.get(UNIDENTIFIED)) == bool(on) and new == own:
        return
    m = w.mut(eid)
    if on:
        m[UNIDENTIFIED] = True
    else:
        m.pop(UNIDENTIFIED, None)
    if new:
        m["_perception"] = new
    else:
        m.pop("_perception", None)


def spill_props(liquid):
    data = info(liquid)
    props = {"floor_hazard": data.get("hazard", "wet")}
    residue = data.get("residue")
    if residue:
        props["residue"] = dict(residue)
    if data.get("biohazard"):
        props["hazards"] = ["biohazard"]
        props["handled_with"] = ["rubber_gloves", "disposable_gloves"]
        props["contamination"] = {"bacteria": 0.8, "biohazard": 0.8}
    if data.get("chemical"):
        props.setdefault("hazards", []).append("toxic")
    return props


def resize(w, spill):
    rec = w.ent(spill)
    liters = num(rec.get("liquid_l"))
    thickness = 2.5 if info(rec.get("liquid")).get("grease") else 1.5   # mm film on a hard floor
    if rec.get("location") in w.s["entities"]:
        thickness = 1.0
    area = max(0.01, liters / thickness)
    side = round(min(3.0, math.sqrt(area)), 3)
    m = w.mut(spill)
    m["area_m2"] = round(area, 4)
    m["size_m"] = [side, side, 0.002]


def make_spill(w, location, liquid, liters, cell=None, source=None, surface=None, count=True, unidentified=False, origin=None):
    """Liquid lands on a floor cell or a surface. Merges with an existing spill of the same liquid there.

    Uses cleaning's `spill` type when it is registered (cleaning evaporates/absorbs it), else a generic item with
    category "spill". Returns the spill id (or None for negligible amounts). unidentified: the robot poured it out
    of something it had not identified - the puddle is a plain "spill" whose liquid stays unknown to it. origin:
    the container the liquid came out of (a parcel's origin): recorded as the spill's contents_from (kernel v2.9)."""
    liters = float(liters)
    if liters < 0.002:
        return None
    rid = location if location in w.s["rooms"] else room_of(w, location)
    target = surface or location
    if target in w.s["rooms"]:
        if cell is None:
            cell = list(w.robot.get("pos") or [0, 0])
        cell = [int(cell[0]), int(cell[1])]
        room = w.s["rooms"][target]
        if not geometry.in_grid(room, cell):
            cols, rows = geometry.grid_size(room)
            cell = [int(clamp(cell[0], 0, cols - 1)), int(clamp(cell[1], 0, rows - 1))]
    existing = None
    for eid in sorted(w.children(target)):
        rec = w.ent(eid)
        if rec.get("category") != "spill" or rec.get("liquid") != liquid:
            continue
        if target in w.s["rooms"]:
            pos = rec.get("pos")
            if pos and geometry.chebyshev(pos, cell) <= 1:
                existing = eid
                break
        else:
            existing = eid
            break
    if existing:
        m = w.mut(existing)
        m["liquid_l"] = rnd(num(m.get("liquid_l")) + liters, 4)
        spill = existing
    else:
        label = "spill" if unidentified else "{} spill".format(str(liquid).replace("_", " "))
        props = {"liquid": liquid, "liquid_l": rnd(liters, 4), "label": label,
                 "category": "spill", "portable": False, "blocks_path": False, "mass_kg": 0.0, "volume_l": 0.0,
                 "plumb_source": source}
        props.update(spill_props(liquid))
        if w.reg is not None and w.reg.known("spill"):
            spill = w.spawn("spill", target, **props)
        else:
            spill = w.spawn("generic_item", target, **props)
        m = w.mut(spill)
        if target in w.s["rooms"]:
            m["pos"] = cell
            m["relation"] = "on"
            fm = floor_material(w, target)
            if fm in ("carpet", "rug"):
                m["on_carpet"] = True
        else:
            m["relation"] = "on" if not w.ent(target).get("container") else "in"
    if unidentified:
        mark_unidentified(w, spill, True)
    if origin:
        from ..core import mark_contents_from
        mark_contents_from(w, spill, origin)
    resize(w, spill)
    if target in w.s["rooms"] and not info(liquid).get("grease"):
        wet_cell(w, target, cell)
    if count and not in_system():
        w.stat("spills")
    plog(w, "spill", liquid=liquid, liters=rnd(liters, 3), where=target, source=source)
    return spill


def add_floor_water(w, rid, cell, liquid, liters, source=None, unidentified=False, origin=None):
    """Water reaching a floor: a spill, a wet cell, and flood accounting per source."""
    if liters <= 0:
        return None
    if is_outdoor(w, rid):
        return outdoor_water(w, rid, cell, liquid, liters, source, unidentified=unidentified, origin=origin)
    spill = make_spill(w, rid, liquid, liters, cell=cell, source=source, unidentified=unidentified, origin=origin)
    sec = psec(w)
    floods = sec.setdefault("floor_water", {})
    key = source or rid
    before = num(floods.get(key))
    floods[key] = rnd(before + liters, 3)
    if before < FLOOD_L <= floods[key]:
        w.stat("flood")
        plog(w, "flood", source=source, room=rid, liters=rnd(floods[key], 2))
    return spill


def outdoor_water(w, rid, cell, liquid, liters, source=None, unidentified=False, origin=None):
    """Water on outdoor ground: grass/soil absorbs it; hardscape gets a puddle (ice when freezing)."""
    ground = floor_material(w, rid)
    data = info(liquid)
    if data.get("chemical") or data.get("fertilizer") or data.get("grease") or liquid in ("mop_water", "soapy_water", "sewage"):
        pollution(w, rid, liquid, liters, kind="ground", source=source)
    if ground in ("grass", "soil", "mulch"):
        sec = psec(w)
        soak = sec.setdefault("ground_soak_l", {})
        soak[rid] = rnd(num(soak.get(rid)) + liters, 3)
        return None
    spill = make_spill(w, rid, liquid, liters, cell=cell, source=source, count=False, unidentified=unidentified, origin=origin)
    temp = num(w.s.get("weather", {}).get("temperature_c"), 15.0)
    if temp <= 0.0 and spill:
        freeze_puddle(w, rid, cell, spill)
    return spill


def freeze_puddle(w, rid, cell, spill):
    rm = w.room_m(rid)
    hazards = rm.setdefault("floor_hazards", [])
    entry = {"cell": [int(cell[0]), int(cell[1])], "kind": "ice", "source": spill}
    if entry not in hazards:
        hazards.append(entry)
        sec = psec(w)
        sec["ice_sheets"] = int(num(sec.get("ice_sheets"))) + 1
        plog(w, "ice_sheet", room=rid, cell=entry["cell"])


def pollution(w, rid, liquid, liters, kind="storm_drain", source=None):
    sec = psec(w)
    events = sec.setdefault("pollution", [])
    events.append({"t": now(w), "room": rid, "liquid": liquid, "liters": rnd(liters, 3), "into": kind, "source": source})
    del events[:-100]
    sec["pollution_l"] = rnd(num(sec.get("pollution_l")) + liters, 3)
    plog(w, "pollution", room=rid, liquid=liquid, liters=rnd(liters, 3), into=kind)


def damage_dict(record):
    """CONVENTIONS 5: `damage` is a dict {kind: severity 0..1}; legacy lists become {item: 1.0}."""
    dmg = record.get("damage")
    if isinstance(dmg, dict):
        return dict(dmg)
    if isinstance(dmg, list):
        return {str(k): 1.0 for k in dmg}
    if isinstance(dmg, str) and dmg:
        return {dmg: 1.0}
    return {}


def damage(w, eid, what, stat=True, broken=False, kind=None, severity=1.0):
    """Record physical damage: damage[kind] = severity (merged, never lowered); `what` is the readable note."""
    key = kind or "_".join(str(what).split("(")[0].split()[:3]).strip("_").lower() or "damage"
    rec = w.mut(eid)
    dmg = damage_dict(rec)
    dmg[key] = rnd(max(num(dmg.get(key)), clamp(float(severity), 0.0, 1.0)), 3)
    rec["damage"] = dmg
    rec["damage_note"] = what
    if broken:
        rec["broken"] = True
    if stat:
        w.stat("damage")
    plog(w, "damage", target=eid, what=what, kind=key)


def add_soil(w, eid, kind_, amount):
    from ..core import add_soil as core_add
    return core_add(w, eid, kind_, amount)


def contaminate(w, eid, kind_, amount):
    m = w.mut(eid)
    cont = m.get("contamination")
    if not isinstance(cont, dict):
        cont = m["contamination"] = {}
    cont[kind_] = rnd(min(1.0, num(cont.get(kind_)) + amount), 4)


def wet(w, eid, level=1.0):
    rec = w.ent(eid)
    if num(rec.get("wetness")) < level:
        w.mut(eid)["wetness"] = rnd(min(1.0, level), 3)


def hazard_sound(obs, entry):
    obs.setdefault("sounds", []).append(entry)


def water_ok_at(w, eid):
    return physics.water_available(w, eid)
