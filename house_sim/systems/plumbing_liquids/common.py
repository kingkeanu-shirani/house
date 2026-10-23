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

Tier-1 physics (kernel v3.0, docs/PHYSICS.md): densities, viscosities and freezing points come from
house_sim/data/physics/liquids.json (density_kg_l, flow_factor); the table below keeps only what liquids.json has no
column for (potability, drinkability, alcohol, foam, residues, contamination rank) and a pour factor for the few
liquids liquids.json has no row for yet. Perceivable occurrences of the time systems are posted as notices
(notice(); kinds "drip" and "water_spray" registered here) and visual ones go through camera.visual_event (plog's
`see`), so the geometric camera logs whether the robot saw them; room-mode outputs are unchanged.
"""
import math

from ...kernel import camera, geometry, physics, timeline
from ...kernel.phys import liquids as PL
from ...kernel.phys import props as PP
from ...kernel.plugins import SYSTEMS

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

# name: potable, drinkable, alcohol, foam factor (straight pour), rank (contamination severity), soil left behind when
# it dries (soil kind: level per mm), floor hazard of a spill. Densities and viscosities are liquids.json's
# (density_kg_l, flow_factor); `flow` is the pour factor of a liquid liquids.json has no row for (drain-cleaner gel,
# grease, fertilizer concentrate, sewage, ...) and is ignored for the others.
LIQUIDS = {
    "water": dict(flow=1.0, potable=True, drink=True),
    "filtered_water": dict(flow=1.0, potable=True, drink=True),
    "distilled_water": dict(flow=1.0, potable=True, drink=True),
    "ice_water": dict(flow=1.0, potable=True, drink=True),
    "soapy_water": dict(flow=1.0, potable=False, rank=2, residue={"streaks": 0.2}),
    "dirty_water": dict(flow=1.0, potable=False, rank=4, residue={"dirt": 0.3, "streaks": 0.3}),
    "mop_water": dict(flow=1.0, potable=False, rank=5, chemical=True, residue={"dirt": 0.3, "streaks": 0.3}),
    "condensate": dict(flow=1.0, potable=False, rank=3),
    "hose_water": dict(flow=1.0, potable=False, rank=1),
    "rainwater": dict(flow=1.0, potable=False, rank=1),
    "toilet_water": dict(flow=1.0, potable=False, rank=8, biohazard=True, residue={"biohazard": 0.6}),
    "sewage": dict(flow=0.9, potable=False, rank=9, biohazard=True, residue={"biohazard": 0.9, "dirt": 0.5}),
    "milk": dict(flow=0.9, potable=True, drink=True, residue={"food": 0.3, "sticky": 0.2}, perishable=True),
    "oat_milk": dict(flow=0.9, potable=True, drink=True, residue={"food": 0.3}, perishable=True),
    "juice": dict(flow=0.9, potable=True, drink=True, sugary=True, residue={"sticky": 0.7}),
    "orange_juice": dict(flow=0.85, potable=True, drink=True, sugary=True, residue={"sticky": 0.7}, solids="pulp"),
    "apple_juice": dict(flow=0.9, potable=True, drink=True, sugary=True, residue={"sticky": 0.7}),
    "lemonade": dict(flow=0.9, potable=True, drink=True, sugary=True, residue={"sticky": 0.6}),
    "iced_tea": dict(flow=1.0, potable=True, drink=True, sugary=True, residue={"sticky": 0.3, "stain_coffee": 0.2}),
    "tea": dict(flow=1.0, potable=True, drink=True, residue={"stain_coffee": 0.3}, solids="leaves"),
    "coffee": dict(flow=1.0, potable=True, drink=True, residue={"stain_coffee": 0.6}),
    "soda": dict(flow=1.0, potable=True, drink=True, sugary=True, foam=0.6, residue={"sticky": 0.8}),
    "sparkling_water": dict(flow=1.0, potable=True, drink=True, foam=0.3),
    "beer": dict(flow=1.0, potable=True, drink=True, alcohol=True, foam=1.0, residue={"sticky": 0.3}),
    "wine": dict(flow=1.0, potable=True, drink=True, alcohol=True, residue={"stain_wine": 0.8, "sticky": 0.2}),
    "sparkling_wine": dict(flow=1.0, potable=True, drink=True, alcohol=True, foam=1.5, residue={"sticky": 0.3}),
    "sangria": dict(flow=1.0, potable=True, drink=True, alcohol=True, sugary=True, residue={"stain_wine": 0.6, "sticky": 0.5}),
    "broth": dict(flow=1.0, potable=True, residue={"food": 0.3, "grease": 0.2}),
    "cooking_oil": dict(flow=0.35, potable=False, grease=True, residue={"grease": 0.9}, hazard="slip"),
    "grease": dict(flow=0.3, potable=False, grease=True, residue={"grease": 1.0}, hazard="slip"),
    "dish_soap": dict(flow=0.15, potable=False, product=True, surfactant=True, residue={"sticky": 0.4}),
    "hand_soap": dict(flow=0.12, potable=False, product=True, surfactant=True, residue={"sticky": 0.4}),
    "foaming_hand_soap": dict(flow=0.9, potable=False, product=True, surfactant=True, residue={"sticky": 0.2}),
    "laundry_detergent": dict(flow=0.2, potable=False, product=True, surfactant=True, residue={"sticky": 0.5}),
    "fabric_softener": dict(flow=0.25, potable=False, product=True, residue={"sticky": 0.4}),
    "bleach": dict(flow=1.0, potable=False, product=True, chemical=True, rank=6, hazard="wet"),
    "drain_cleaner": dict(flow=0.6, potable=False, product=True, chemical=True, rank=7, caustic=True),
    "liquid_fertilizer": dict(flow=0.5, potable=False, product=True, fertilizer=True, rank=3),
    "fertilizer_solution": dict(flow=1.0, potable=False, fertilizer=True, rank=2),
    "vinegar": dict(flow=1.0, potable=True, product=True),
    "rinse_aid": dict(flow=0.5, potable=False, product=True),
    "mixed": dict(flow=0.9, potable=False, rank=1, residue={"sticky": 0.3}),
}

# notice kinds this domain posts (TIER1_SWEEP 6.1-6.3; wait(interrupt_on=[...]) accepts them). Levels at 1 m: a tap
# dripping into a basin or onto a cabinet floor is 40-50 dB (45); a burst pipe, a snapped stem or a split hose hisses
# at 60-70 dB (65). The kernel's "splash" (45 dB) is used for a basin or toilet running over onto the floor.
timeline.register_notice_kind("drip", "water starts dripping (a tap, a leak, a ceiling stain)", ("any_sound",), 45.0)
timeline.register_notice_kind("water_spray", "water sprays or hisses out (a burst pipe, a snapped stem, a split hose)",
                              ("any_sound",), 65.0)
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


def liquid_row(liquid):
    """The liquids.json row of a liquid (aliases resolved), or None when liquids.json has no row for it."""
    row = PP.liquid_props(liquid or "water")
    return None if row.get("name") == "default" else row


def density_kg_l(liquid):
    """kg per litre (liquids.json; unknown liquids weigh as water, the kernel's default row)."""
    return num(PP.liquid_props(liquid or "water").get("rho"), 1000.0) / 1000.0


def flow_factor(liquid):
    """Pour-rate factor of a liquid's viscosity: the kernel's min(1, sqrt(20 cP / viscosity)) from liquids.json
    (TIER1_PHYSICS 5.7: honey about 22x slower than water, oil about 1.7x, dish soap about 4x); a liquid liquids.json
    has no row for keeps this domain's factor (LIQUIDS `flow`)."""
    row = liquid_row(liquid)
    if row is not None:
        return min(1.0, math.sqrt(PL.REFERENCE_VISC_CP / max(1e-6, num(row.get("visc_cp"), 1.0))))
    return num(info(liquid).get("flow"), 1.0)


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


def plog(w, event, limit=300, see=None, **data):
    """Log a domain event (plumbing.log) and emit it as "plumbing_<event>". see: where a visual occurrence happened
    ({"source", "room", "at", "z", "size_m"}, camera.visual_event's geometry): the geometric camera then logs whether
    the robot saw it (and a seen one becomes a "visual" notice); room mode is unchanged (the event log is the same)."""
    sec = psec(w)
    entry = {"t": now(w), "event": event}
    entry.update(data)
    sec["log"].append(entry)
    del sec["log"][:-limit]
    w.emit("plumbing_" + event, **{k: v for k, v in data.items() if (isinstance(v, (str, int, float, bool)) or v is None) and k != "kind"})
    if see:
        camera.visual_event(w, "plumbing_" + event, emit=False, **see)
    return entry


def notice(w, kind, source=None, room=None, db=None, data=None):
    """A perceivable occurrence of the time systems (a tap starts dripping, a pipe bursts, a basin runs over) as a
    notice (TIER1_SWEEP 6.1): the timeline decides whether the robot hears it and stores it when someone listens.
    Only from the systems (in_system()): an action's own outcome is in its message."""
    if not in_system():
        return None
    return w.notice(kind, room=room, db=db, source=source, data=data)


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
# harm attribution (TIER1_DECISIONS harm attribution, docs/PHYSICS.md "Attribution", VERIFY_HARDENING_DECISIONS 3:
# the robot's harms fail a task, exogenous ones - a pre-existing leak, a scenario's running tap, a freeze, a resident
# at a tap - do not by themselves; a spec about preventing one says so with its own predicates)

ROBOT_WATER = "_robot_water"     # {reason: True}: water controls a robot action opened (never observed: underscore)
WATER_REASONS = ("flow", "plug", "supply")


def robot_acting(w):
    """True while a robot action runs (the kernel's action phase, TIER1_PHYSICS 6.4), not in this domain's time
    systems (which run after the action's settle)."""
    return w.phys_scratch.get("phase") == "action" and not in_system()


def mark_robot_water(w, eid, reason, on=True):
    """Record (on) or clear (off) that a robot action set a water control of eid so it lets water go where it may do
    harm: reason "flow" (it opened or turned up a tap, spigot, nozzle or dispenser, or flushed), "plug" (it closed a
    basin's drain) or "supply" (it opened a valve or supply stop). A harm the time systems produce through a marked
    control is the robot's (harm_by). Only a robot action sets a mark; a control shut by anyone (or a flush's refill
    done) clears it."""
    if eid not in w.s["entities"] or on and not robot_acting(w):
        return
    rec = w.s["entities"][eid]
    marks = dict(rec.get(ROBOT_WATER) or {})
    if bool(marks.get(reason)) == bool(on):
        return
    if on:
        marks[reason] = True
    else:
        marks.pop(reason, None)
    m = w.mut(eid)
    if marks:
        m[ROBOT_WATER] = marks
    else:
        m.pop(ROBOT_WATER, None)


def water_controls(w, source):
    """[(id, reasons)] of the water controls whose setting decides whether source lets water out: for a leak, the
    fixture it is on (its tap only for a drain-side, running or flushing leak; a supply-side leak runs whatever the
    tap does), that fixture's stop valves, the heater inlet for a hot line and the house main; for a basin, toilet,
    tap or spigot the same without a leak; for a hose its spigot (and the spigot's interior valve). A leak that always
    runs has no control."""
    from . import supply as S
    entities = w.s["entities"]
    rec = entities.get(source) or {}
    leak = rec.get("_leak") if isinstance(rec.get("_leak"), dict) else {}
    when = leak.get("when")
    if when == "always":
        return []
    fixture = leak.get("fixture") or source
    frec = entities.get(fixture) or {}
    out = []
    if frec.get("hose") and frec.get("attached_to") in entities:
        out.append((fixture, WATER_REASONS))
        fixture = frec["attached_to"]
        frec = entities[fixture]
    supply_side = when in ("pressurized",) and not frec.get("hose")
    out.append((fixture, ("supply",) if supply_side else WATER_REASONS))
    if frec.get("interior_valve") in entities:
        out.append((frec["interior_valve"], ("supply",)))
    line = leak.get("line")
    for vid in S.valves(w):
        vrec = entities[vid]
        if vid == fixture:
            continue
        if vrec.get("main") or vrec.get("feeds_hot") and line in (None, "hot", "both") or fixture in (vrec.get("feeds") or []):
            out.append((vid, ("supply",)))
    return out


def harm_by(w, source=None):
    """"robot" or "exogenous": who a harm this domain raises is charged to. Whatever a robot action does is the robot's.
    A consequence of the time systems is the robot's when the robot set up its cause: a robot action made the leak
    (`_leak.by`), arranged the source (the kernel's _robot_moved marker on it or on what it rests in: a cold drink it
    set on the wood, a hose it hung up full of water), or opened, turned up or plugged a water control that feeds it
    (ROBOT_WATER on water_controls). Anything else is exogenous."""
    if robot_acting(w):
        return "robot"
    entities = w.s["entities"]
    if source not in entities:
        return "exogenous"
    leak = entities[source].get("_leak")
    if isinstance(leak, dict) and leak.get("by") == "robot":
        return "robot"
    from ...kernel.phys import impact
    if impact.robot_caused(w, source):
        return "robot"
    for eid, reasons in water_controls(w, source):
        marks = (entities.get(eid) or {}).get(ROBOT_WATER) or {}
        if any(marks.get(r) for r in reasons):
            return "robot"
    return "exogenous"


def harm(w, flag, source=None, amount=1):
    """Raise a global harm flag (damage, flood, burns) when the harm is the robot's (harm_by); an exogenous one is
    counted only in plumbing.exogenous_harms {flag: n} (the domain's record, like the kernel's phys_* statistics).
    Returns "robot" or "exogenous"."""
    who = harm_by(w, source)
    if who == "robot":
        w.stat(flag, amount)
    else:
        counts = psec(w).setdefault("exogenous_harms", {})
        counts[flag] = counts.get(flag, 0) + amount
    return who


def leak_landing(w, eid):
    """Where a leak on eid sends its water (a leak record's "to"): into the cabinet it sits in (a stop under a sink),
    else onto the floor cell under it."""
    rec = w.ent(eid)
    loc = rec.get("location")
    if loc in w.s["entities"] and rec.get("relation") == "in" and w.ent(loc).get("container"):
        return {"entity": loc}
    return {"room": room_of(w, eid), "cell": cell_of(w, eid)}


def robot_leak(w, leak):
    """A leak record a robot action makes (a snapped stem, an appliance hose unscrewed live or strained by a pull, a
    hose punctured on the nail it was hung on) carries by: robot, so the water it later lets out is the robot's harm
    (harm_by)."""
    if robot_acting(w):
        leak["by"] = "robot"
    return leak


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
        new = False
    else:
        new = True
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
    see = {"source": spill, "size_m": max(0.05, num((w.ent(spill).get("size_m") or [0.1])[0], 0.1))} if new else None
    plog(w, "spill", see=see, liquid=liquid, liters=rnd(liters, 3), where=target, source=source)
    return spill


def add_floor_water(w, rid, cell, liquid, liters, source=None, unidentified=False, origin=None, cause=None):
    """Water reaching a floor: a spill, a wet cell, and flood accounting per source (plumbing.floor_water). The flood
    flag counts the robot's water (harm_by of cause, default the source) from 4 L per source on."""
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
    # the flood flag counts the robot's water only (harm_by per portion): a pipe that burst before the robot came is
    # exogenous, the same pipe after the robot opened the main again is the robot's from then on
    if harm_by(w, cause or source) == "robot":
        mine = sec.setdefault("robot_floor_water", {})
        own_before = num(mine.get(key))
        mine[key] = rnd(own_before + liters, 3)
        if own_before < FLOOD_L <= mine[key]:
            w.stat("flood")
            plog(w, "flood", source=source, room=rid, liters=rnd(floods[key], 2), by="robot")
    elif before < FLOOD_L <= floods[key]:
        counts = sec.setdefault("exogenous_harms", {})
        counts["flood"] = counts.get("flood", 0) + 1
        plog(w, "flood", source=source, room=rid, liters=rnd(floods[key], 2), by="exogenous")
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


def damage(w, eid, what, stat=True, broken=False, kind=None, severity=1.0, cause=None):
    """Record physical damage: damage[kind] = severity (merged, never lowered); `what` is the readable note. stat: count
    it in the global damage flag when it is the robot's (harm with cause, default eid itself: a freeze splitting a hose
    the robot hung up full is the robot's, a pipe bursting in a wall it never touched is not)."""
    key = kind or "_".join(str(what).split("(")[0].split()[:3]).strip("_").lower() or "damage"
    rec = w.mut(eid)
    dmg = damage_dict(rec)
    dmg[key] = rnd(max(num(dmg.get(key)), clamp(float(severity), 0.0, 1.0)), 3)
    rec["damage"] = dmg
    rec["damage_note"] = what
    if broken:
        rec["broken"] = True
    if stat:
        harm(w, "damage", cause or eid)
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
