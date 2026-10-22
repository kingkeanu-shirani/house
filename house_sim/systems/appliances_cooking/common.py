"""Shared helpers for the appliances_cooking domain (constants, logging/harms, material rules, people, air fallbacks,
the tier-1 physics, camera and notice adapters).

Everything here is pure or mutates only through the journaled World. Domain state that is not attached to an
entity lives in the top-level section ``state["appliances_cooking"]``:

  log       [{t, kind, ...}]           recent domain events (capped)
  harms     [{t, kind, stat, ...}]     consequences that were recorded (with the global stat they raised)
  counters  {name: n}                  boil_overs, boil_dry, smoke_events, smoke_alarm, arcing, unlit_gas_l, ...
  once      [key]                      harm de-duplication keys
  air       {room: {smoke, gas_l, odor: {kind: level}}}   fallback air model used only when the safety domain
                                       (which owns smoke/gas concentration) is not loaded

Kernel v3.0 / v3.1 adoption (docs/design/TIER1_INTEGRATION.md 6): temperatures this domain drives carry
``thermal_owner: "appliances_cooking"`` (own_temperature / release_temperature; the older ``fixed_temperature`` alias
is still read), breaks, spills and thermal shock go through the kernel's physics functions (break_item, spill,
shock), visual occurrences through ``camera.visual_event`` (visual_event) and perceivable chimes and alarms through
``w.notice`` (notice).
"""
import math
import sys

from ...kernel import geometry, physics
from ...kernel.phys import lawbook, props as kprops, thermal as kthermal
from ...kernel.plugins import SYSTEMS
from ...kernel.predicates import root_room

SECTION = "appliances_cooking"
OWNER = SECTION          # thermal_owner value of everything whose temperature this domain drives (CONVENTIONS 6)
LOG_MAX = 300
CEILING_M = 2.4
AMBIENT_C = 21.0

# ----------------------------------------------------------------------------------------------
# heat levels: knob words / numbers -> fraction of the element's rated power
LEVEL_FRAC = {"off": 0.0, "lite": 0.9, "simmer": 0.08, "low": 0.15, "med_low": 0.3, "medium_low": 0.3, "medium": 0.5,
              "med": 0.5, "med_high": 0.72, "medium_high": 0.72, "high": 1.0, "boost": 1.3, "max": 1.0, "warm": 0.05}
WORD_LEVELS = ["off", "low", "med_low", "medium", "med_high", "high"]
GAS_LEVELS = ["off", "lite", "high", "med_high", "medium", "med_low", "low"]      # order the knob passes when turned
NUMBER_LEVELS = ["off", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
INDUCTION_LEVELS = NUMBER_LEVELS + ["boost"]


def level_frac(value):
    """Fraction of rated power for a knob/zone level ('off', 'low', 'medium', '6', 'boost', 0.0-1.0 numbers)."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return max(0.0, min(1.3, v / 9.0 if v > 1.0 else v))
    text = str(value).strip().lower()
    if text in LEVEL_FRAC:
        return LEVEL_FRAC[text]
    if text.isdigit():
        return max(0.0, min(1.0, int(text) / 9.0))
    return 0.0


# ----------------------------------------------------------------------------------------------
# liquids: density kg/l, specific heat J/kgK, boiling C (None = does not boil in a kitchen), foam factor, smoke point
LIQUIDS = {
    "water": (1.0, 4186.0, 100.0, 0.08, None),
    "salted_water": (1.01, 4100.0, 100.4, 0.08, None),
    "pasta_water": (1.01, 4100.0, 100.3, 0.8, None),
    "stock": (1.02, 4050.0, 100.5, 0.3, None),
    "broth": (1.02, 4050.0, 100.5, 0.3, None),
    "soup": (1.04, 3900.0, 100.5, 0.35, None),
    "stew": (1.08, 3700.0, 101.0, 0.4, None),
    "sauce": (1.1, 3600.0, 101.0, 0.5, None),
    "tomato_sauce": (1.1, 3600.0, 101.0, 0.5, None),
    "milk": (1.03, 3930.0, 100.2, 2.2, None),
    "cream": (1.0, 3800.0, 100.4, 2.0, None),
    "hot_chocolate": (1.05, 3800.0, 100.5, 1.8, None),
    "coffee": (1.0, 4180.0, 100.0, 0.1, None),
    "tea": (1.0, 4180.0, 100.0, 0.05, None),
    "bean_water": (1.02, 4100.0, 100.4, 1.0, None),
    "oil": (0.92, 2000.0, None, 0.0, 230.0),
    "cooking_oil": (0.92, 2000.0, None, 0.0, 230.0),
    "vegetable_oil": (0.92, 2000.0, None, 0.0, 230.0),
    "canola_oil": (0.92, 2000.0, None, 0.0, 205.0),
    "olive_oil": (0.91, 1970.0, None, 0.0, 190.0),
    "extra_virgin_olive_oil": (0.91, 1970.0, None, 0.0, 190.0),
    "peanut_oil": (0.92, 2000.0, None, 0.0, 230.0),
    "avocado_oil": (0.92, 2000.0, None, 0.0, 270.0),
    "frying_oil": (0.92, 2000.0, None, 0.0, 230.0),
    "grease": (0.9, 2100.0, None, 0.0, 200.0),
    "butter": (0.91, 2300.0, None, 0.3, 150.0),
    "rinse_aid": (1.05, 3800.0, 100.0, 1.5, None),
    "dish_soap": (1.03, 3900.0, 100.0, 6.0, None),
    "dishwasher_gel": (1.1, 3600.0, 100.0, 0.4, None),
    "descaler": (1.04, 4000.0, 100.0, 0.2, None),
    "vinegar": (1.01, 4100.0, 100.0, 0.1, None),
}
OIL_KINDS = {k for k, v in LIQUIDS.items() if v[2] is None and v[4] is not None}
OIL_AUTOIGNITION_C = 370.0       # cooking oils self-ignite ~370-435 C
OIL_FLASH_C = 315.0
LATENT_J_PER_KG = 2.26e6


def liquid_props(name):
    name = str(name or "water").lower()
    if name in LIQUIDS:
        return LIQUIDS[name]
    if "oil" in name:
        return LIQUIDS["oil"]
    if "milk" in name or "cream" in name:
        return LIQUIDS["milk"]
    if "soup" in name or "stock" in name or "broth" in name:
        return LIQUIDS["soup"]
    return LIQUIDS["water"]


def is_oil(name):
    name = str(name or "").lower()
    return name in OIL_KINDS or name.endswith("_oil") or name == "grease"


# ----------------------------------------------------------------------------------------------
# section, log, harms

def now(w):
    return float(w.s.get("time_s", 0.0))


def section(w):
    s = w.sec(SECTION)
    for key, empty in (("log", []), ("harms", []), ("counters", {}), ("once", [])):
        if key not in s:
            s[key] = empty
    return s


def defer_destroy(w, eid):
    """Remove an entity at the start of this domain's next tick instead of now (a dissolved pod). Kernel v2.1's appliance
    pass now skips entities that vanish mid-pass, but a hook may still read the pod later in the same pass (and other
    domains' tags on it are re-parented by flush_destroyed), so the one-tick deferral is kept."""
    if eid not in w.s["entities"]:
        return
    s = section(w)
    queue = s.setdefault("destroy_queue", [])
    if eid not in queue:
        queue.append(eid)
    m = w.mut(eid)
    m["dissolved"] = True
    if m.get("powder_g"):
        m["powder_g"] = 0.0
    m["detergent"] = dict(m.get("detergent") or {}, kind="dissolved")


def flush_destroyed(w):
    queue = read_section(w.s).get("destroy_queue")
    if not queue:
        return
    s = section(w)
    for eid in list(s["destroy_queue"]):
        if eid not in w.s["entities"]:
            continue
        rec = w.s["entities"][eid]
        for child in children(w.s, eid):        # anything attached to it (another domain's tag/hose) is left behind
            cm = w.mut(child)
            cm["location"] = rec.get("location")
            cm["relation"] = rec.get("relation") or "in"
        w.destroy(eid)
    s["destroy_queue"] = []


def read_section(state):
    return state.get(SECTION) or {}


def log(w, kind, **data):
    s = section(w)
    entry = {"t": now(w), "kind": kind}
    entry.update(data)
    s["log"].append(entry)
    if len(s["log"]) > LOG_MAX:
        del s["log"][:len(s["log"]) - LOG_MAX]
    return entry


def counter(w, name, amount=1):
    s = section(w)
    c = s["counters"]
    c[name] = round(float(c.get(name, 0)) + amount, 4) if isinstance(amount, float) else c.get(name, 0) + amount
    return c[name]


def get_counter(state, name):
    return read_section(state).get("counters", {}).get(name, 0)


def has_once(state, key):
    return key in read_section(state).get("once", [])


def harm(w, kind, stat=None, once=None, **data):
    """Record a consequence. stat raises a global flag (fire, burns, damage, injury, safety_violations, ...).
    once: de-duplication key (a harm with the same key is recorded only once per episode)."""
    if once is not None and has_once(w.s, once):
        return False
    s = section(w)
    if once is not None:
        s["once"].append(once)
    entry = {"t": now(w), "kind": kind}
    if stat:
        entry["stat"] = stat
    entry.update(data)
    s["harms"].append(entry)
    if len(s["harms"]) > 200:
        del s["harms"][:len(s["harms"]) - 200]
    if stat:
        w.stat(stat)
    w.emit("appliance_harm", harm=kind, **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))})
    return True


def harms_of(state, kind=None):
    return [h for h in read_section(state).get("harms", []) if kind is None or h.get("kind") == kind]


# ----------------------------------------------------------------------------------------------
# tier-1 adapters (TIER1_INTEGRATION 6): temperature ownership, kernel physics, camera events, notices

def own_temperature(m):
    """This domain drives the record's temperature (a vessel on a burner or a heated base, a pan in an oven, a hot
    appliance base): the kernel thermal system and the food model leave it alone (thermal_owner, CONVENTIONS 6,
    TIER1_PHYSICS 5.9). m is a writable record (w.mut)."""
    m["thermal_owner"] = OWNER
    m.pop("fixed_temperature", None)


def release_temperature(m):
    """Give a temperature this domain drove back to the kernel / food thermal models (another domain's thermal_owner
    is left alone; the older fixed_temperature alias goes)."""
    if m.get("thermal_owner") == OWNER:
        m.pop("thermal_owner", None)
    m.pop("fixed_temperature", None)


def owns_temperature(rec):
    rec = rec or {}
    return rec.get("thermal_owner") == OWNER or bool(rec.get("fixed_temperature"))


def law_active(name):
    """The kernel applies this physics law itself (laws.json state; lawbook.forced in tests): a legacy copy of it in
    this domain then returns at once (adoption protocol, TIER1_PHYSICS 10.3)."""
    return lawbook.active(name)


def break_item(w, eid, cause, by="robot"):
    """A break through the kernel (impact.break_entity): broken, condition and damage.broken; glass and ceramics also
    shattered and sharp (a sharp floor hazard when it lies on a floor); what liquid it held runs down; phys.broke
    hooks; stats.phys_breaks, and the global breakages flag when it is the robot's. True when it broke now."""
    return physics.break_entity(w, eid, cause, by=by)


def spill(w, liters, liquid="water", onto=None, at=None, source=None, by="robot", cause="spill"):
    """Liquid that leaves its vessel, through the kernel's single spill entry (liquids.spill): onto a support it films
    the top (wetness) and runs down to the floor (a cleaning spill or a kernel puddle and the room's wet cells); at a
    (room, cell) it lands on that floor. cause "leak" or "drip" counts only a portion that starts a new puddle (a
    trickle of several substeps). Returns the floor spill's id, or None."""
    return physics.spill(w, liters, liquid=liquid, at=at, onto=onto, source=source, by=by, cause=cause)


def shock(w, eid, other_temp_c, coupling, by="robot"):
    """Thermal shock through the kernel (thermal.shock_check): eid cracks (break_entity, cause thermal_shock) or chips
    when |T - other| x coupling exceeds its shock tolerance (materials.json shock_k with the per-instance variation:
    soda-lime glass 45 K, ceramic 100 K, stoneware 100 K, borosilicate 160 K...). True when it failed."""
    return physics.shock_check(w, eid, other_temp_c, coupling, by=by)


# sound level (dB(A) at 1 m) of the loud machines the robot runs with its own verbs: countertop blenders and food
# processors 85-95 dB, burr grinders 75-85 dB, an espresso machine's steam wand 70-80 dB
APPLIANCE_DB = {"blender": 88.0, "food_processor": 85.0, "grinder": 80.0, "espresso": 75.0}


def quiet_check(ctx, kind):
    """Quiet mode (core set_quiet): a machine louder than the body's quiet limit (55 dB at 1 m) is not started, with
    the kernel's own wording (TIER1_INTEGRATION 6, appliances_cooking body item: the noise is the appliance's, so it is
    no ActionSpec noise_db)."""
    from ...kernel import body
    level = APPLIANCE_DB.get(kind)
    if level is not None and body.get(ctx.w.s, "quiet") and level > body.quiet_limit_db(ctx.w.s):
        from ...kernel.errors import ActionError
        raise ActionError(body.QUIET_TEXT, "social")


SPLASH_CONTACT_S = 2.0    # hot liquid or steam on skin runs off or is pulled away within about 2 s


def scalded(state, people_ids, temp_c, contact_s=SPLASH_CONTACT_S):
    """The people among people_ids whose skin a splash or steam at temp_c burns within contact_s: the kernel's burn
    time (thermal.burn_seconds, Moritz and Henriques; a child's skin, 12 or younger, burns in a quarter of the time).
    Hot oil, boiling water and steam always do; a 60 C splash burns a child but not an adult."""
    group = people(state)
    return [pid for pid in people_ids
            if physics.burn_seconds(temp_c, child=is_child(group.get(pid), 12.0)) <= contact_s]


def visual_event(w, kind, source=None, room=None, size_m=0.3):
    """A visual occurrence (a flare-up, smoke, a boil-over, sparks) witnessed by the geometric camera's current head
    pose (camera.visual_event without its event-log write: callers keep their own w.emit, so room-mode states stay as
    they were). Room mode: nothing."""
    if "view" not in (w.s.get("robot") or {}):
        return None
    from ...kernel import camera
    return camera.visual_event(w, kind, source=source, room=room, size_m=size_m, emit=False)


# the chimes and alarms posted as notices: (dB at 1 m, what a listener hears). Typical kitchen-appliance end-of-cycle
# chimes measure 55-70 dB at 1 m (a dishwasher's is the softest), a kettle's thermostat click about 50 dB, a toaster's
# pop about 60 dB; refrigerator door alarms 65-70 dB; kitchen timers 80-85 dB (UL 217 asks 85 dB at 3 m of a smoke
# alarm, which is the safety domain's when it is loaded). The heard word never names the appliance.
NOTICE_SOUNDS = {"oven_ready": (65.0, "chime"), "cycle_done": (62.0, "beeps"), "microwave_done": (65.0, "beeps"),
                 "dishwasher_done": (55.0, "chime"), "kettle_click": (50.0, "click"), "toaster_pop": (60.0, "pop"),
                 "coffee_done": (60.0, "beeps"), "cooker_done": (62.0, "beeps"), "timer": (80.0, "beeping"),
                 "door_alarm": (68.0, "beeping"), "temp_alarm": (72.0, "beeping"), "smoke_alarm": (85.0, "siren")}


def notice(w, kind, source, what, room=None):
    """A perceivable chime, beep or alarm on the kernel's notice bus (TIER1_SWEEP 6.1): kind "appliance_done" or
    "alarm"; `what` picks the sound's level and the word a listener hears (NOTICE_SOUNDS). The timeline decides whether
    the robot hears it and names the source only when it perceives it; nothing is stored while nobody listens."""
    db, heard = NOTICE_SOUNDS.get(what, (None, "beeps"))
    return w.notice(kind, room=room, db=db, source=source, data={"sound": heard})


# ----------------------------------------------------------------------------------------------
# entity helpers

def kind_of(rec):
    return (rec or {}).get("ac_kind")


def is_kind(rec, *kinds):
    return kind_of(rec) in kinds


def tags(rec):
    return set((rec or {}).get("tags") or [])


def mat(rec):
    return str((rec or {}).get("material") or "").lower()


def type_names(rec):
    return [str((rec or {}).get("type") or "")] + list((rec or {}).get("is_a") or [])


def label(rec, default="it"):
    return str((rec or {}).get("label") or default)


def hidden(rec, key):
    value = (rec or {}).get(key)
    return value if isinstance(value, dict) else {}


def mut_hidden(w, eid, key):
    m = w.mut(eid)
    if not isinstance(m.get(key), dict):
        m[key] = {}
    return m, m[key]


def set_if(w, eid, **fields):
    """Write fields only when they change (avoids journaling churn in systems)."""
    rec = w.ent(eid)
    changed = {k: v for k, v in fields.items() if rec.get(k) != v}
    if changed:
        m = w.mut(eid)
        m.update(changed)
    return bool(changed)


def num(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def rnd(x, n=3):
    return round(float(x), n)


def room_of(state, eid):
    return root_room(state, eid)


def room_volume_m3(state, rid):
    size = (state["rooms"].get(rid, {}).get("size_m") or [4.0, 4.0])
    return max(6.0, float(size[0]) * float(size[1]) * CEILING_M)


def room_temp(state, rid):
    if rid is None or rid not in state.get("rooms", {}):
        return float(state.get("utilities", {}).get("indoor_temp_c", AMBIENT_C))
    return physics.room_temperature(state, rid)


def is_outdoor(state, rid):
    return bool(state.get("rooms", {}).get(rid, {}).get("outdoor"))


def powered(w, eid):
    return physics.is_powered(w, eid)


def held_by_robot(state, eid):
    return eid in [v for v in state["robot"].get("hands", {}).values() if v]


# A location index shared by everything this domain's tick does (one pass over the entities, built lazily and only when
# an active appliance needs its contents). It lives only while the tick runs: tick_index_begin/end bracket the system.
# Reads re-validate each id (it still exists and is still located there); entity creation/destruction (the World's
# version) rebuilds it. Nothing in the tick moves an existing entity into a new parent, so it stays exact. Outside the
# tick (actions, hooks, setup) children()/descendants() scan as before.
_TICK = {"state": None, "world": None, "version": None, "index": None}


def tick_index_begin(w):
    _TICK.update(state=w.s, world=w, version=None, index=None)


def tick_index_end():
    _TICK.update(state=None, world=None, version=None, index=None)


def _tick_index(state):
    if _TICK["state"] is not state:
        return None
    w = _TICK["world"]
    version = (getattr(w, "_version", None), len(state["entities"]))
    if _TICK["index"] is None or _TICK["version"] != version:
        index = {}
        ents = state["entities"]
        for k in sorted(ents):
            index.setdefault(ents[k].get("location"), []).append(k)
        _TICK.update(index=index, version=version)
    return _TICK["index"]


def children(state, eid, relation=None):
    index = _tick_index(state)
    if index is not None:
        ents = state["entities"]
        out = []
        for k in index.get(eid, ()):
            r = ents.get(k)
            if r is not None and r.get("location") == eid and (relation is None or r.get("relation", "on") == relation):
                out.append(k)
        return out
    return sorted(k for k, r in state["entities"].items()
                  if r.get("location") == eid and (relation is None or r.get("relation", "on") == relation))


def descendants(state, eid):
    out, stack = [], [eid]
    index = _tick_index(state)
    if index is None:
        index = {}
        for k, r in state["entities"].items():
            index.setdefault(r.get("location"), []).append(k)
        for v in index.values():
            v.sort()
    ents = state["entities"]
    while stack:
        cur = stack.pop()
        for c in index.get(cur, ()):
            r = ents.get(c)
            if r is None or r.get("location") != cur:
                continue
            out.append(c)
            stack.append(c)
    return out


def add_damage(w, eid, kind, severity=1.0):
    """Conventions 5: `damage` is a dict {kind: severity 0..1} (legacy lists are converted); keeps the worst severity."""
    m = w.mut(eid)
    raw = m.get("damage")
    if isinstance(raw, dict):
        dmg = dict(raw)
    elif isinstance(raw, (list, tuple)):
        dmg = {str(k): 1.0 for k in raw}
    else:
        dmg = {}
    dmg[kind] = round(max(num(dmg.get(kind)), float(severity)), 3)
    m["damage"] = dmg
    return m


def parent_chain(state, eid, limit=12):
    chain, loc, seen = [], state["entities"].get(eid, {}).get("location"), {eid}
    while loc in state["entities"] and loc not in seen and len(chain) < limit:
        chain.append(loc)
        seen.add(loc)
        loc = state["entities"][loc].get("location")
    return chain


def find_kind(state, kind, room=None):
    out = []
    for eid in sorted(state["entities"]):
        rec = state["entities"][eid]
        if rec.get("ac_kind") == kind and (room is None or root_room(state, eid) == room):
            out.append(eid)
    return out


def appliance_ids(state):
    return [eid for eid in sorted(state["entities"]) if state["entities"][eid].get("ac_kind")]


def is_food(rec):
    return isinstance((rec or {}).get("food"), dict)


def food_kind(rec):
    f = (rec or {}).get("food") or {}
    return str(f.get("kind") or (rec or {}).get("type") or "").lower()


def food_cat(rec):
    return str(((rec or {}).get("food") or {}).get("cat") or "").lower()


def mass_kg(rec):
    f = (rec or {}).get("food") or {}
    if isinstance(f, dict) and f.get("qty_g") is not None:
        return max(0.001, num(f.get("qty_g")) / 1000.0)
    return max(0.001, num((rec or {}).get("mass_kg"), 0.3))


def size3(rec):
    s = (rec or {}).get("size_m") or [0.1, 0.1, 0.1]
    s = list(s) + [0.1] * (3 - len(s))
    return [max(0.001, num(v, 0.1)) for v in s[:3]]


def is_cookware(rec):
    rec = rec or {}
    if rec.get("category") in ("cookware", "bakeware") or "cookware" in tags(rec):
        return True
    return bool(rec.get("conducts_heat") and rec.get("portable") and (rec.get("container") or rec.get("surface")))


def base_diameter_m(rec):
    if rec.get("base_diameter_m"):
        return num(rec["base_diameter_m"], 0.2)
    inner = rec.get("inner_size_m")
    if inner:
        return min(num(inner[0], 0.2), num(inner[1], 0.2))
    s = size3(rec)
    return min(s[0], s[1])


_LOADED = {"n": -1, "names": frozenset()}


def system_loaded(name):
    """True when a system with this name is registered (cached; refreshed when more systems register)."""
    if _LOADED["n"] != len(SYSTEMS):
        _LOADED.update(n=len(SYSTEMS), names=frozenset(s[1] for s in SYSTEMS))
    return name in _LOADED["names"]


def food_safety_loaded():
    return system_loaded("food.thermal")


def safety_loaded():
    return system_loaded("safety.air")


def safety_module(name):
    """The safety domain's module (air, fire, gas) when that domain is loaded, else None. Never imports it."""
    if not safety_loaded():
        return None
    return sys.modules.get("house_sim.systems.safety." + name)


# ----------------------------------------------------------------------------------------------
# material rules (realistic compatibility; explicit fields on an item always win)

FERROMAGNETIC = {"cast_iron", "enameled_cast_iron", "carbon_steel", "enameled_steel", "iron", "steel", "black_steel",
                 "stainless_steel", "stainless", "magnetic_stainless"}
NON_INDUCTION = {"aluminum", "aluminium", "copper", "glass", "ceramic", "stoneware", "clay", "porcelain", "plastic",
                 "silicone", "anodized_aluminum", "nonstick", "titanium"}
METALS = {"metal", "steel", "stainless_steel", "stainless", "aluminum", "aluminium", "cast_iron", "enameled_cast_iron",
          "carbon_steel", "iron", "copper", "brass", "tin", "silver", "chrome", "chrome_steel", "foil", "aluminum_foil"}
PLASTICS = {"plastic", "polypropylene", "pp", "polyethylene", "pe", "hdpe", "ldpe", "polystyrene", "eps", "pet", "acrylic",
            "melamine", "silicone_plastic", "abs", "nylon"}
GLASSES = {"glass", "crystal", "tempered_glass", "borosilicate", "soda_lime_glass"}


def is_metal(rec):
    return mat(rec) in METALS or mat(rec).endswith("steel")


def is_plastic(rec):
    return mat(rec) in PLASTICS


def is_glass(rec):
    return mat(rec) in GLASSES


def induction_ok(rec):
    for key in ("induction_ok", "ferromagnetic"):
        if key in (rec or {}):
            return bool(rec[key])
    m = mat(rec)
    if (rec or {}).get("induction_base"):
        return True
    if m in NON_INDUCTION:
        return False
    return m in FERROMAGNETIC


def oven_safe_c(rec):
    rec = rec or {}
    if rec.get("oven_safe_c") is not None:
        return num(rec["oven_safe_c"])
    if rec.get("oven_safe") is False:
        return 0.0
    if str(rec.get("handle_material") or "").lower() in ("plastic", "bakelite", "phenolic", "silicone_coated"):
        return 175.0
    if is_plastic(rec) or mat(rec) in ("paper", "cardboard", "wood", "bamboo", "fabric", "cotton"):
        return 0.0
    if str(rec.get("coating") or "") == "nonstick":
        return 230.0
    if rec.get("oven_safe") is True or is_metal(rec) or is_glass(rec) or mat(rec) in ("ceramic", "stoneware", "porcelain"):
        return 260.0
    return 0.0


def broiler_safe(rec):
    rec = rec or {}
    if "broiler_safe" in rec:
        return bool(rec["broiler_safe"])
    if is_glass(rec) or mat(rec) in ("ceramic", "stoneware", "porcelain"):
        return False
    if str(rec.get("coating") or "") == "nonstick":
        return False
    return is_metal(rec) and oven_safe_c(rec) >= 250


def microwave_verdict(rec):
    """(ok, reason). Metal arcs; metal trim burns; foil sparks; plastics not marked safe may leach/melt."""
    rec = rec or {}
    if rec.get("microwave_safe") is not None:
        return bool(rec["microwave_safe"]), "label"
    if rec.get("metal_trim"):
        return False, "metal_trim"
    if rec.get("metal_handle") or rec.get("metal_parts"):
        return False, "metal_parts"
    m = mat(rec)
    if m in ("foil", "aluminum_foil"):
        return False, "foil"
    if is_metal(rec):
        return False, "metal"
    if m in ("melamine",):
        return False, "melamine"
    if m in ("paper", "cardboard") and rec.get("waxed"):
        return False, "wax"
    return True, "material"


def dishwasher_verdict(rec):
    """(ok, consequence, top_rack_only). consequence names what a cycle does to an unsuitable item."""
    rec = rec or {}
    explicit = rec.get("dishwasher_safe")
    m = mat(rec)
    if explicit is False:
        return False, rec.get("dw_damage") or _dw_damage(rec), False
    if explicit == "top_rack":
        return True, None, True
    if explicit is True:
        return True, None, is_plastic(rec)
    if rec.get("electronic") or rec.get("motor_base"):
        return False, "water_damage", False
    if m in ("cast_iron", "carbon_steel", "iron"):
        return False, "rust_seasoning", False
    if m in ("wood", "bamboo"):
        return False, "warp_crack", False
    if rec.get("insulated") or rec.get("vacuum_insulated"):
        return False, "vacuum_seal", False
    if "sharp" in (rec.get("hazards") or []) and (rec.get("fine_edge") or rec.get("tool_kind") in
                                                  ("chef_knife", "paring_knife", "bread_knife", "carving_knife", "boning_knife",
                                                   "fillet_knife", "santoku")):
        return False, "dull_edge", False
    if m == "crystal" or rec.get("crystal"):
        return False, "etch", False
    if rec.get("metal_trim") in ("gold", "silver", True):
        return False, "trim_fade", False
    if m == "copper":
        return False, "tarnish", False
    if m in ("aluminum", "aluminium") and str(rec.get("coating") or "") != "nonstick" and rec.get("category") in ("cookware", "bakeware"):
        return False, "discolor", False
    return True, None, is_plastic(rec)


def _dw_damage(rec):
    ok, consequence, _ = dishwasher_verdict(dict(rec, dishwasher_safe=None))
    return consequence or "damage"


def surface_heat_limit_c(state, sid, rec=None):
    """Hottest contact (C) a support's finish tolerates without a mark, melt or scorch: the kernel's heat limit
    (thermal.heat_limit_c: materials.json `heat` or the record's own heat_limit_c, at least 400 C for a heat_resistant
    top such as a trivet or rack, then the +-15 % per-instance variation) for an entity; for a bare room floor (no floor entity)
    the kernel's value for `rec`, its material. None: no limit. (The domain's own table went in the adoption round:
    TIER1_PHYSICS 10.3.)"""
    if sid in state.get("entities", {}):
        return kthermal.heat_limit_c(state, sid)
    value = kprops.props_of(dict(rec or {})).heat_limit_c
    return None if value is None or value != value or value == float("inf") else float(value)


def combustible(rec):
    if is_food(rec):
        return False
    m = mat(rec)
    return m in ("paper", "cardboard", "fabric", "cotton", "linen", "polyester", "wool", "wood", "bamboo", "wicker", "foam",
                 "plastic", "felt") or rec.get("flammable") or "flammable" in (rec.get("hazards") or [])


def ignition_c(rec):
    m = mat(rec)
    if rec.get("ignition_c") is not None:
        return num(rec["ignition_c"])
    if m in ("paper", "cardboard"):
        return 230.0
    if m in ("cotton", "linen", "fabric", "felt"):
        return 250.0
    if m in ("polyester", "wool"):
        return 400.0
    if m in ("wood", "bamboo", "wicker"):
        return 300.0
    if m in PLASTICS:
        return 350.0
    return 300.0


def melt_c(rec):
    if is_food(rec):
        return None
    if rec.get("melt_c") is not None:
        return num(rec["melt_c"])
    if is_plastic(rec):
        return 130.0
    if mat(rec) == "silicone":
        return 260.0
    return None


# ----------------------------------------------------------------------------------------------
# people

def people(state):
    return state.get("people", {}) or {}


def age_of(p):
    if p is None:
        return 35.0
    if p.get("age") is not None:
        return num(p.get("age"), 35.0)
    return {"infant": 0.5, "toddler": 2.0, "child": 7.0, "teen": 15.0, "elderly": 78.0}.get(p.get("role", "adult"), 35.0)


def is_child(p, max_age=10.0):
    return bool(p) and (p.get("role") in ("child", "infant", "toddler") and age_of(p) < max_age + 0.01 or age_of(p) < max_age)


def present(p):
    return bool(p) and p.get("location") is not None and not p.get("away")


def children_home(state, max_age=10.0):
    return [pid for pid in sorted(people(state)) if is_child(people(state)[pid], max_age) and present(people(state)[pid])]


def people_in_room(state, rid):
    return [pid for pid in sorted(people(state)) if people(state)[pid].get("location") == rid]


def adult_in_room(state, rid, awake=True):
    for pid in people_in_room(state, rid):
        p = people(state)[pid]
        if p.get("role", "adult") in ("adult", "elderly", "guest") and int(p.get("authority", 2 if p.get("role", "adult") == "adult" else 1)) >= 1 \
                and (p.get("awake", True) or not awake):
            return pid
    return None


def adults_home(state):
    return [pid for pid in sorted(people(state)) if people(state)[pid].get("role", "adult") in ("adult", "elderly")
            and present(people(state)[pid])]


def people_near(state, eid, cells=1):
    """People within `cells` (Chebyshev) of an entity's anchor cells in the same room."""
    rid, anchor_cells = geometry.anchor(state, eid)
    if rid is None:
        return []
    anchor_cells = anchor_cells or []
    out = []
    for pid in people_in_room(state, rid):
        pos = people(state)[pid].get("pos")
        if pos and anchor_cells and min(geometry.chebyshev(pos, c) for c in anchor_cells) <= cells:
            out.append(pid)
    return out


def robot_in_room(state, rid):
    return state["robot"].get("room") == rid


def birds_home(state):
    return [pid for pid in sorted(state.get("pets", {}) or {}) if state["pets"][pid].get("species") in ("bird", "parrot", "budgie",
                                                                                                    "cockatiel", "canary")]


# ----------------------------------------------------------------------------------------------
# air fallbacks (smoke, odors, unlit gas) when the safety domain is not loaded

SMOKE_ALARM_LEVEL = 0.6          # fallback obscuration units (roughly 1/m) at which a kitchen smoke alarm sounds
GAS_SMELL_PCT = 0.05
GAS_LEL_PCT = 5.0


def air_fallback(state, rid):
    return (read_section(state).get("air") or {}).get(rid) or {}


def _air_m(w, rid):
    s = section(w)
    air = s.setdefault("air", {})
    return air.setdefault(rid, {"smoke": 0.0, "gas_l": 0.0, "odor": {}})


def add_smoke(w, rid, amount, odor="burning_food", source=None):
    """amount ~ grams of smoke particulate. Routed to the safety air model when loaded."""
    if rid is None or amount <= 0:
        return
    vol = room_volume_m3(w.s, rid)
    air = safety_module("air")
    if air is not None:
        air.add(w, rid, "smoke", amount * 2.0 / vol)
        if odor:
            air.add_odor(w, rid, odor, amount * 0.5)
        return
    a = _air_m(w, rid)
    a["smoke"] = rnd(float(a.get("smoke", 0.0)) + amount * 2.0 / vol, 5)
    if odor:
        a["odor"][odor] = rnd(min(5.0, float(a["odor"].get(odor, 0.0)) + amount * 0.5), 4)
    if a["smoke"] >= SMOKE_ALARM_LEVEL and not a.get("alarm"):
        a["alarm"] = True
        counter(w, "smoke_alarm")
        log(w, "smoke_alarm", room=rid, source=source)
        w.emit("smoke_alarm", room=rid)
        notice(w, "alarm", None, "smoke_alarm", room=rid)


def add_odor(w, rid, odor, amount):
    if rid is None or amount <= 0:
        return
    air = safety_module("air")
    if air is not None:
        air.add_odor(w, rid, odor, amount)
        return
    a = _air_m(w, rid)
    a["odor"][odor] = rnd(min(5.0, float(a["odor"].get(odor, 0.0)) + amount), 4)


def add_gas_l(w, rid, liters):
    """Unlit natural gas/propane released into a room (fallback concentration model)."""
    if rid is None or liters <= 0:
        return
    a = _air_m(w, rid)
    a["gas_l"] = rnd(float(a.get("gas_l", 0.0)) + liters, 4)


def gas_pct(state, rid):
    air = safety_module("air")
    if air is not None:
        try:
            return float(air.air(state, rid).get("gas", 0.0))
        except Exception:      # pragma: no cover - defensive: never let another domain break ours
            return 0.0
    gas_l = float(air_fallback(state, rid).get("gas_l", 0.0))
    return gas_l / 1000.0 / room_volume_m3(state, rid) * 100.0


def smoke_level(state, rid):
    air = safety_module("air")
    if air is not None:
        try:
            return float(air.air(state, rid).get("smoke", 0.0))
        except Exception:      # pragma: no cover
            return 0.0
    return float(air_fallback(state, rid).get("smoke", 0.0))


def _is_vent(rec):
    return rec.get("category") == "window" or "exhaust_m3s" in rec or rec.get("ac_kind") == "hood"


def decay_air(w, dt):
    """Fallback air: smoke and gas leave through ventilation (open windows, running hoods) and leakage."""
    state = w.s
    air = read_section(state).get("air") or {}
    if not air or safety_loaded():
        return
    for rid in sorted(air):
        a = air[rid]
        if not (a.get("smoke", 0) > 1e-4 or a.get("gas_l", 0) > 1e-3 or any(v > 1e-3 for v in (a.get("odor") or {}).values())):
            continue
        vol = room_volume_m3(state, rid)
        rate = 0.5 / 3600.0                       # background leakage ~0.5 air changes per hour (1/s)
        for eid in w.static_index("appliances_cooking.vents", _is_vent):
            rec = state["entities"].get(eid)
            if rec is None or rec.get("location") != rid:
                continue
            if rec.get("category") == "window" and rec.get("open"):
                rate += 0.10 / vol                # an open window exchanges ~0.1 m3/s
            if rec.get("exhaust_m3s") and rec.get("on"):
                rate += float(rec["exhaust_m3s"]) / vol
        k = math.exp(-dt * rate)
        m = _air_m(w, rid)
        m["smoke"] = rnd(float(m.get("smoke", 0.0)) * k, 5)
        m["gas_l"] = rnd(float(m.get("gas_l", 0.0)) * k, 4)
        for key in list(m.get("odor", {})):
            m["odor"][key] = rnd(float(m["odor"][key]) * k, 4)
        if m.get("alarm") and m["smoke"] < SMOKE_ALARM_LEVEL * 0.5:
            m["alarm"] = False


# ----------------------------------------------------------------------------------------------
# geometry helpers

def surface_dims(rec):
    size = rec.get("surface_size_m") or (rec.get("size_m") or [0.5, 0.5])[:2]
    return num(size[0], 0.5), num(size[1], 0.5)


def front_edge_distance(state, eid):
    """Meters from an item on a surface to the surface's front edge. Convention (documented): on counters and other
    wall-placed surfaces spos y = 0 is the back (wall) edge and y = depth the front (room) edge; on tables, islands and
    carts every edge is a front edge."""
    rec = state["entities"].get(eid) or {}
    parent = state["entities"].get(rec.get("location")) or {}
    spos = rec.get("spos")
    if not spos or rec.get("relation") != "on":
        return None
    sw, sd = surface_dims(parent)
    iw, idp = geometry.footprint_m(rec)
    front = sd - (num(spos[1]) + idp)
    if parent.get("category") == "counter" and not parent.get("island"):
        return max(0.0, front)
    return max(0.0, min(front, num(spos[1]), num(spos[0]), sw - (num(spos[0]) + iw)))


def surface_height(state, eid):
    rec = state["entities"].get(eid) or {}
    return geometry.height_m(state, eid) + num(rec.get("surface_height_m"), num(size3(rec)[2]))


def powered_state(state, eid):
    """Mains power reaches a (possibly built-in part) appliance: utility power, its own plug, its parent's plug,
    and an explicit power_from (a freezer drawer runs off its refrigerator)."""
    rec = state["entities"].get(eid) or {}
    if not state.get("utilities", {}).get("power", True):
        return False
    if rec.get("battery_powered"):
        return float(rec.get("battery_level", 1.0)) > 0
    if rec.get("plug_required") and not rec.get("plugged_in"):
        return False
    src = rec.get("power_from")
    if src and src in state["entities"] and src != eid:
        return powered_state(state, src)
    if rec.get("relation") == "part":
        parent = state["entities"].get(rec.get("location"))
        if parent and parent.get("plug_required") and not parent.get("plugged_in"):
            return False
    return True


# ---------------------------------------------------------------------------------------------------------------------
# floor access (kernel v2.3 geometry: 4-connected reach from the doorways, which is exactly the set the kernel's
# no-corner-cutting path moves can reach; mounted things above counter height are approachable from 2 cells away)
# ---------------------------------------------------------------------------------------------------------------------
STORAGE_SKIP = ("switch", "window", "light", "outlet", "hooks", "register", "vent", "detector", "sensor", "thermostat",
                "blind", "shade", "curtain", "shutter", "sill", "covering", "guide", "sign", "art", "frame", "clock", "hood",
                "extinguisher", "blanket")


def approachable(state, eid, reach=None):
    """True when the robot can get within reach of the entity's floor anchor from a doorway (people and pets ignored:
    they move). `reach` may pass a precomputed geometry.reachable_cells set for the entity's room."""
    room, cells = geometry.anchor(state, eid)
    if room is None or not cells:
        return False
    cells = {tuple(c) for c in cells}
    reach = geometry.reachable_cells(state, room) if reach is None else reach
    radius = 2 if geometry.mounted_overhead(state, eid) else 1
    return bool((geometry.near_cells(cells, state["rooms"][room], radius) - cells) & reach)


def wall_storage(state, rid):
    """Wall-mounted storage of a room the robot must be able to open (drawer fronts, wall/upper cabinets, shelves)."""
    out = []
    for eid in sorted(geometry.room_entities(state, rid)):
        rec = state["entities"][eid]
        if not rec.get("mounted") or rec.get("pos") is None or rec.get("connects"):
            continue
        t = (str(rec.get("type") or "") + " " + str(rec.get("category") or "")).lower()
        if any(w in t for w in STORAGE_SKIP) or not (rec.get("container") or rec.get("surface") or rec.get("openable")):
            continue
        out.append(eid)
    return out
