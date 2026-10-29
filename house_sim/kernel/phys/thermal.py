"""Heat: time constants, contact heat, wet contact and condensation, thermal shock, freezing and thawing, melting,
burns and heat content (docs/design/TIER1_PHYSICS.md 5.9; fidelity P-approx), and the kernel thermal system's step
with the active-set optimisation.

* Ownership. ``thermal_owner: "<domain>"`` (or its older alias ``fixed_temperature``) makes the kernel leave an
  entity's temperature to that domain; ``owned(rec)`` is also true while the phase law holds a liquid on its freezing
  or melting plateau.
* Time constant (law ``thermal_tau``, deferred): tau = sum(m_i c_p,i) / (h A), h = 10 W/m2K in still air,
  A = 2(wd + wh + dh), clamped to [60 s, 6 h]; an explicit ``thermal_tau_s`` wins. ``kernel_tau_s`` is what the kernel
  thermal system uses: this law when it is active, the legacy 300 sqrt(m) + 60 s otherwise.
* Contact heat (law ``contact_heat``): a hot item resting directly on a support whose contact face is hotter than
  the support's heat limit marks it: a white heat ring in a wood finish below 150 C (``ring_stain``, event
  ``phys.ring``), a scorch (wood above that, laminate, fabric, paper, leather) or a melt mark (plastics, rubber, foam,
  wax): severity min(1, (T_contact - limit) / 100 K) x min(1, contact / 60 s), accrued by the ``contact`` process
  while it stays and stays hot. The contact face of a vessel holding hot liquid is cooler than the liquid
  (``contact_temp_c``: ambient + 0.75 of the excess through a ceramic, glass or plastic wall, 0.95 through metal,
  0.2 insulated), so a mug of 85 C coffee may leave a light ring on a 60 C wood finish but no mug of boiling water
  marks a household plastic (about 100 C). Brittle tops (glass, stone) crack by thermal shock instead. A trivet or
  pad in between is the direct support and protects. ``phys.contact_heat`` hooks may claim the contact (ignition,
  cookware marks).
* Wet contact (law ``wet_contact``): an item resting on a water-sensitive finish (wsens >= 0.5) while wet (wetness
  >= 0.3, leaking or dripping, or sweating below the room's dew point - 1 K) queues a check: wood and leather get
  ``damage.ring_stain`` after 600 s, MDF ``swollen`` and paper, cardboard and electronics ``water_damage`` (plus
  ``phys.wetted``) after 900 s; the check fires only if the item is still there and still wet, and deepens the
  mark for each further period. A coaster, placemat or tray in between protects.
* Thermal shock (law ``thermal_shock``): a body with a shock tolerance breaks (enamel chips) when
  |dT| x coupling > its tolerance; coupling 1.0 for a cold liquid poured into (or a body plunged into) something
  colder than itself (downshock: the hot surface is pulled into tension), 0.6 for a hot liquid poured into (or a
  body put into) a colder one (upshock: the heated inner surface is in compression, which glass bears about 1.7x
  better), 0.6 for a hot item on wet stone or metal (and a hot item heating a brittle top), 0.5 for a cold dish
  into a hot oven, 0.3 for freezer air. Boiling soup into a 4 degC soda-lime jar is 96 K x 0.6 = 58 K > 45 K x 1.15:
  it cracks; boiling water into a 21 degC mug is 47 K < 100 K x 0.85: it never does. The automatic check of a pour
  (``pour_shock_check``, liquids.pour_in / transfer) runs only while the law is active (deferred until the
  appliances_cooking and cleaning adoption, TIER1_PHYSICS 10.2); ``shock_check`` itself is always callable.
* Phase (law ``phase``): a liquid with a freezing point in a container (no thermal owner) is held at T_f while
  ``ice_frac`` advances by dt x coupling x hA x |T_f - T_amb| / (m_liquid x L): the latent heat (334 kJ/kg) leaves
  through the lumped law's conductance hA = 10 W/m2K x A whatever the thermal_tau law's state (the legacy kernel
  time constant is a fit for sensible cooling and made thawing about 11x too fast); sealed rigid containers burst or
  bulge as the ice expands 9 % into too little headspace. The phase process keeps the exact progress in its
  parameters (``ice_frac`` itself is stored to 4 decimals), so small temperature differences, whose per-substep step
  is below 0.0001, still freeze and thaw at the right rate.
* Melting (law ``melt``): above ``melt_c`` a body melts (wax, soap, film, foam plastic) or deforms (other plastics,
  rubber), reaching full severity after 600 s at 10 K or more above its melting point.
* ``burn_seconds`` (Moritz and Henriques adult skin, log-linear; children x0.25) and ``heat_content_j`` (sensible
  plus latent heat to a reference temperature, the freezer heat load) are plain functions for domains.

Law entry points for the settle pass (registered by PHYS-INT through ``LAWS``): ``fn(w, eid, env=None, cmap=None)
-> [consequence records]`` (processes.consequence: the step's shared list, also returned). The ``contact``, ``phase``
and ``melting`` processes and the ``wet_contact`` checks run in ``physics.processes`` (processes.py).
"""
import math

from ..plugins import run_hooks
from ..predicates import root_room
from . import impact, lawbook, liquids, processes, props
from .processes import blame, dims, finite, num


H_STILL_AIR = 10.0              # W/m2K: natural convection plus linearised radiation, still indoor air (5.9)
TAU_MIN_S, TAU_MAX_S = 60.0, 6 * 3600.0
WATER_CP = 4186.0               # J/kg K
ICE_CP = 2100.0                 # J/kg K, ice near 0 degC
LATENT_FUSION_J_KG = 334000.0   # water
ICE_DIGITS = 9                  # the phase process's exact progress (a step at 1 K from T_f is about 1e-4)
# Specific heats (J/kg K) of liquids that are not mostly water (handbook values at 20-25 degC: vegetable oils
# 1.97 kJ, mineral motor oil 1.9, honey 2.25, maple-type syrup 2.6, ethanol 2.44, gasoline 2.22, whole milk 3.93).
# Every other liquid in liquids.json is an aqueous solution and uses water's value.
LIQUID_CP = {"oil": 1970.0, "cooking_oil": 1970.0, "olive_oil": 1970.0, "motor_oil": 1900.0, "honey": 2250.0,
             "syrup": 2600.0, "alcohol": 2440.0, "gasoline": 2220.0, "milk": 3930.0}
SETTLED_K = 0.05                # the kernel thermal system leaves a body this close to its ambient alone
# contact heat (5.9)
CONTACT_SPAN_K = 100.0
CONTACT_RAMP_S = 60.0
MELT_FINISHES = ("plastic", "film", "rubber", "foam", "foam_plastic", "wax")     # finishes that melt, not scorch
# A vessel of hot liquid touches its support through its wall and foot, not with the liquid: the contact face sits
# at ambient + factor x (liquid - ambient). A ceramic, glass or plastic wall (1-1.5 W/m K, 4-8 mm, plus the liquid's
# own convective film) drops about a quarter of the excess: the foot ring of a mug of 85 C coffee runs at about 70 C
# on an insulating wooden top (IR readings of a filled mug's outside wall are 60-70 C); a thin metal pot passes nearly
# all of it; a vacuum or double-walled vessel almost none. A solid hot body (pan, iron, dish from the oven) touches
# with its own temperature.
CONTACT_WALL_FACTOR = 0.75
CONTACT_WALL_FACTOR_METAL = 0.95
CONTACT_WALL_FACTOR_INSULATED = 0.2
# Below about 150 C a hot item does not char a wood finish: lacquer, shellac, oil and wax finishes turn white (the
# same white ring trapped moisture leaves: ring_stain, event phys.ring); finishes brown and blister, and the wood
# chars, only well above it (wood darkens from about 150-200 C and ignites near 300 C).
RING_FINISHES = ("wood", "engineered_wood")
RING_MAX_C = 150.0
MARK_EVENTS = {"scorch": ("phys.scorch", "a scorch mark"), "melted": ("phys.melt", "a melt mark"),
               "ring_stain": ("phys.ring", "a heat mark")}
# wet contact (5.9)
WET_SENSITIVE = 0.5             # finishes at or above this water sensitivity are marked by wet contact
WET_ITEM = 0.3                  # an item this wet (or leaking, dripping, sweating) marks them
SWEAT_MARGIN_K = 1.0
WET_CONTACT_RULES = {"wood": ("ring_stain", 600.0), "leather": ("ring_stain", 600.0),
                     "engineered_wood": ("swollen", 900.0), "granular": None}    # soil and sand just get damp
WET_CONTACT_DEFAULT = ("water_damage", 900.0)       # paper, cardboard, plaster, electronics, soap, organic
WET_MARK_FIRST, WET_MARK_STEP = 0.3, 0.2
CONTACT_SEEP_L = 0.005          # water a wet item passes into paper or electronics under it per check (5 ml)
# thermal shock couplings (5.9). Hot liquid into a colder vessel is an upshock: glass and glazes bear the
# compression on the heated inner surface about 1.7x better than the tension of a downshock (ASTM C149 tests the
# downshock as the severe case), so it couples 0.6 against 1.0 for cold liquid into a hotter vessel.
COUPLING_IMMERSION = 1.0
COUPLING_UPSHOCK = 0.6
COUPLING_CONTACT = 0.6
COUPLING_OVEN = 0.5
COUPLING_FREEZER = 0.3
CONDUCTIVE_TOPS = ("stone", "concrete", "steel", "cast_iron", "aluminum", "soft_metal", "enameled_metal",
                   "ceramic", "porcelain", "stoneware")
# freezing (5.9): water expands 9 % on freezing; a narrow glass neck plugs with ice first and needs 3 % more
ICE_EXPANSION = 0.09
NARROW_NECK_MARGIN = 0.03
CAN_BURST_EXCESS = 0.02         # a metal can bulges up to 2 % of excess expansion, then bursts
SHATTER_CLASSES = ("glass", "tempered_glass", "borosilicate", "glass_ceramic", "crystal", "porcelain", "stoneware",
                   "ceramic", "terracotta")
METAL_CLASSES = ("steel", "cast_iron", "aluminum", "soft_metal", "enameled_metal")
# melting (5.9)
MELT_FULL_S = 600.0
MELT_SPAN_K = 10.0
LIQUEFY_CLASSES = ("wax", "soap", "film", "foam_plastic")
# burns (5.9): time to a burn on adult skin (Moritz and Henriques 1947), log-linear in between
BURN_TABLE = ((44.0, 21600.0), (47.0, 1200.0), (50.0, 180.0), (55.0, 20.0), (60.0, 5.0), (65.0, 2.0), (70.0, 1.0))
CHILD_BURN_FACTOR = 0.25


_KERNEL = []


def _kernel():
    """kernel/physics.py (room temperatures, the parent-chain ambient, the legacy time constant), imported on first
    use: kernel/physics.py imports this package when it wires the layer into the engine."""
    if not _KERNEL:
        from .. import physics
        _KERNEL.append(physics)
    return _KERNEL[0]


# ----- ownership and time constants ---------------------------------------------------------------------------------

def domain_owned(rec):
    """A domain owns this entity's temperature (thermal_owner, or the fixed_temperature alias)."""
    return bool(rec.get("thermal_owner") or rec.get("fixed_temperature"))


def owned(rec):
    """The kernel thermal system leaves rec alone: domain-owned, or held on a phase plateau by the phase process."""
    return bool(rec.get("thermal_owner") or rec.get("fixed_temperature") or rec.get("_phys_phase"))


def body_cp(rec):
    """Specific heat (J/kg K) of an entity's own material (materials.json c_p of its class); None for intangibles."""
    cls = props.material_class(None, rec)
    return finite((props.MATERIALS["classes"].get(cls) or {}).get("c_p"))


def liquid_cp(liquid):
    return LIQUID_CP.get(liquid, WATER_CP)


def heat_capacity_j_k(rec, cp=None):
    """Heat capacity (J/K) of a body and the liquid it holds (the frozen fraction counts as ice)."""
    total = num(rec.get("mass_kg")) * (cp if cp is not None else (body_cp(rec) or 0.0))
    liters = num(rec.get("liquid_l"))
    if liters > 0:
        ice = min(1.0, max(0.0, num(rec.get("ice_frac"))))
        mass = liters * liquids.liquid_density_kg_l(rec)
        total += mass * ((1.0 - ice) * liquid_cp(rec.get("liquid") or "water") + ice * ICE_CP)
    return total


def tau_s(rec, cp=None):
    """thermal_tau law: lumped-capacitance time constant (s) = sum(m c_p) / (h A), h = 10 W/m2K, A = 2(wd + wh + dh),
    clamped to [60 s, 6 h]; an explicit thermal_tau_s wins. cp: the body's specific heat when the caller has it
    (props c_p), else it comes from the material class. Anchors: 0.5 L water bottle about 56 min, mug of coffee about
    50 min, 2.5 kg cast-iron pan about 8 min, 60 W bulb about 90 s."""
    explicit = finite(rec.get("thermal_tau_s"))
    if explicit is not None and explicit > 0:
        return explicit
    w, d, h = dims(rec)
    area = 2.0 * (w * d + w * h + d * h)
    if area <= 0:
        return TAU_MIN_S
    return min(TAU_MAX_S, max(TAU_MIN_S, heat_capacity_j_k(rec, cp) / (H_STILL_AIR * area)))


def kernel_tau_s(rec, lumped=None):
    """The time constant the kernel thermal system uses: tau_s when the thermal_tau law is active (pass lumped to
    avoid asking the lawbook per entity), else the legacy kernel formula (explicit thermal_tau_s, else
    300 sqrt(mass + liquid) + 60 s)."""
    if lumped is None:
        lumped = lawbook.active("thermal_tau")
    return tau_s(rec) if lumped else _kernel().legacy_tau_s(rec)


def relax_c(current, target, coupling, dt, tau):
    """Newton relaxation over dt (closed form), rounded like the kernel thermal system."""
    k = 1.0 - math.exp(-dt * coupling / tau)
    return round(current + (target - current) * k, 3)


def mix_in(w, eid, liters, temp_c, liquid):
    """Liquid at temp_c poured into eid: the bulk temperature becomes the heat-capacity-weighted mean of eid (body and
    liquid already in it) and the incoming liquid. Owned entities keep their temperature (their owner mixes)."""
    rec = w.ent(eid)
    if owned(rec) or liters <= 0:
        return
    rho = num(props.liquid_props(liquid).get("rho"), 1000.0) / 1000.0
    incoming = liters * rho * liquid_cp(liquid)
    mine = heat_capacity_j_k(rec)
    current = num(rec.get("temperature_c"), 21.0)
    w.mut(eid)["temperature_c"] = round((mine * current + incoming * num(temp_c)) / max(1e-9, mine + incoming), 3)


# ----- the kernel thermal system's step -----------------------------------------------------------------------------

def _ambient_at(state, loc, temps, indoor, rooms_of):
    """(target, coupling, live parent) of anything located at loc: kernel physics._source walked once per location.
    An internal_temp_c source or room air gives a fixed target (live None); a conducting or insulated parent gives
    its id, whose temperature_c the caller reads when it needs it (it may change during the pass)."""
    entities = state["entities"]
    pid = loc
    parent = entities.get(loc)
    depth = 0
    while parent is not None and depth < 8:
        if parent.get("internal_temp_c") is not None:
            return float(parent["internal_temp_c"]), float(parent.get("heat_coupling", 1.0)), None
        if parent.get("conducts_heat"):
            return None, float(parent.get("heat_coupling", 1.5)), pid
        if parent.get("insulated"):
            return None, 0.15, pid
        if not (parent.get("container") or parent.get("accepts_items")):
            break
        pid = parent.get("location")
        parent = entities.get(pid)
        depth += 1
    return temps.get(_root_of(state, loc, rooms_of), indoor), 1.0, None


def _root_of(state, loc, memo):
    """Root room of anything located at loc (kernel physics._room_map, memoised per location)."""
    rooms, entities = state["rooms"], state["entities"]
    people, pets = state.get("people", {}), state.get("pets", {})
    chain, current = [], loc
    while True:
        if current in memo:
            result = memo[current]
            break
        if current in rooms:
            result = current
            break
        if current == "robot":
            result = state["robot"].get("room")
            break
        if current in people:
            result = people[current].get("location")
            break
        if current in pets:
            result = pets[current].get("location")
            break
        record = entities.get(current)
        if record is None or current in chain:
            result = None
            break
        chain.append(current)
        current = record.get("location")
    for item in chain:
        memo[item] = result
    return result


def thermal_step(w, dt, tick=None, mut=None, setter=None):
    """The kernel thermal system (kernel/physics.py "thermal", period 120 s) with the active-set optimisation of 5.9:
    the ambient (parent-chain source or root-room air) is resolved once per location instead of once per entity, and
    bodies within 0.05 K of their ambient are skipped before any time constant is computed. Entity order, targets,
    rounding and the legacy time constant are those of physics.thermal_system, so the states are identical while
    thermal_tau, phase and melt are deferred and no entity carries thermal_owner (tests/test_physics_thermal.py
    proves it on generated houses); it is about 40 % faster on a settled house.

    mut: how to get a writable record for a temperature-only write (defaults to w.mut); setter: a field setter
    setter(eid, "temperature_c", value) used instead when given (kernel/physics.py passes World.set_quiet, which
    journals the one field without copying the record and is no touch for the settle pass). Owned entities are skipped. With the phase law active a
    liquid crossing its freezing point stops there and starts the phase process; with the melt law active a body
    rising past its melting point starts the melting process."""
    state = w.s
    entities = state["entities"]
    write = mut or w.mut
    temps = {rid: _kernel().room_temperature(state, rid) for rid in state["rooms"]}
    indoor = float(state.get("utilities", {}).get("indoor_temp_c", 21.0))
    ambients, rooms_of = {}, {}
    lumped = bool(lawbook.active("thermal_tau"))
    phase_on = bool(lawbook.active("phase"))
    melt_on = bool(lawbook.active("melt"))
    for eid, entity in entities.items():
        if "temperature_c" not in entity or entity.get("fixed_temperature") or entity.get("thermal_owner") or \
                entity.get("_phys_phase"):
            continue
        loc = entity.get("location")
        ambient = ambients.get(loc)
        if ambient is None:
            ambient = ambients[loc] = _ambient_at(state, loc, temps, indoor, rooms_of)
        target, coupling, live = ambient
        if live is not None:
            target = float(entities[live].get("temperature_c", 21.0))
        current = float(entity["temperature_c"])
        if abs(target - current) < SETTLED_K:
            continue
        new = relax_c(current, target, coupling, dt, kernel_tau_s(entity, lumped))
        if phase_on:
            new = _phase_gate(w, eid, entity, current, new)
        if setter is not None:
            setter(eid, "temperature_c", new)
        else:
            write(eid)["temperature_c"] = new
        if melt_on and new > current:
            melt_c = finite(props.props(state, eid).melt_c)
            if melt_c is not None and new > melt_c >= current:
                processes.start(w, "melting", eid)


def _phase_gate(w, eid, entity, current, new):
    """A liquid reaching its freezing point stops there and starts freezing (or, frozen, starts thawing)."""
    if num(entity.get("liquid_l")) <= 0 or num(entity.get("capacity_l")) <= 0:
        return new
    freeze = freeze_point_c(entity)
    if freeze is None:
        return new
    ice = num(entity.get("ice_frac"))
    if (new < freeze <= current + 1e-9 and ice < 1.0) or (new > freeze >= current - 1e-9 and ice > 0.0):
        if processes.start(w, "phase", eid):
            return round(freeze, 3)
    return new


# ----- heat limits and contact heat ---------------------------------------------------------------------------------

def heat_limit_c(state, sid):
    """Hottest contact (degC) a support's finish tolerates (props heat_limit_c, variation applied); None: no limit."""
    return finite(props.props(state, sid).heat_limit_c)


def damage_level(rec, kind):
    """Current severity (0-1) of one damage kind on a record, read like impact.damage_map: damage is {kind: severity}
    (a non-numeric value, or a domain's list of kinds, counts as full severity)."""
    damage = rec.get("damage")
    if isinstance(damage, dict):
        value = damage.get(kind)
        if value is None:
            return 0.0
        return num(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 1.0
    if isinstance(damage, list):
        return 1.0 if any(d == kind or (isinstance(d, dict) and d.get("kind") == kind) for d in damage) else 0.0
    return 0.0


def _resting_on(state, item, sid):
    rec = state["entities"].get(item)
    return rec is not None and sid in state["entities"] and rec.get("location") == sid and \
        rec.get("relation", "on") == "on"


def contact_temp_c(state, hot):
    """Temperature (degC) of the face a hot item rests on: its own temperature for a solid body (a pan, an iron, a
    dish from the oven); for a vessel holding liquid, ambient + factor x (T - ambient) with the wall factor 0.75
    (ceramic, glass, plastic), 0.95 (metal) or 0.2 (insulated, vacuum or double-walled)."""
    rec = state["entities"][hot]
    temp = num(rec.get("temperature_c"), 21.0)
    if num(rec.get("liquid_l")) <= 0:
        return temp
    ambient = num(_kernel().ambient(state, hot)[0], 21.0)
    if temp <= ambient:
        return temp
    if rec.get("insulated") or rec.get("vacuum_insulated") or rec.get("double_walled"):
        factor = CONTACT_WALL_FACTOR_INSULATED
    elif props.material_class(None, rec) in METAL_CLASSES:
        factor = CONTACT_WALL_FACTOR_METAL
    else:
        factor = CONTACT_WALL_FACTOR
    return ambient + factor * (temp - ambient)


def contact_mark(cls, contact_c):
    """The mark a too-hot contact leaves on a finish of class cls: "melted" (plastics, film, rubber, foam, wax),
    "ring_stain" (a white heat ring in a wood finish below 150 C), else "scorch"."""
    if cls in MELT_FINISHES:
        return "melted"
    if cls in RING_FINISHES and contact_c < RING_MAX_C:
        return "ring_stain"
    return "scorch"


def contact_claimed(w, hot, support_id, temp, severity):
    """True when a domain's phys.contact_heat hook claims the contact of hot on support_id (a heel rest, a trivet,
    a domain's own heat model): the check contact_check makes before marking anything. Public so that a domain's
    legacy contact-heat code (while the law is deferred) honours every other domain's claims the same way
    (textiles_laundry's adoption request 8: an iron upright on its heel rest scorched the ironing board through
    another domain's legacy rule)."""
    return any(run_hooks("phys.contact_heat", w, hot, support_id, temp, severity))


def contact_check(w, hot, support_id, by=None):
    """contact_heat law: hot rests directly on support_id and its contact face (contact_temp_c: a vessel's wall is
    cooler than its liquid) is above the support's heat limit. Hooks may claim it; a brittle top is shock-checked
    (coupling 0.6) instead; otherwise the contact process starts accruing the mark (contact_mark). Returns "scorch",
    "ring_stain", "melted", "shock" (the top cracked) or None."""
    state = w.s
    if not _resting_on(state, hot, support_id):
        return None
    limit = heat_limit_c(state, support_id)
    temp = contact_temp_c(state, hot)
    if limit is None or temp <= limit:
        return None
    severity = round(min(1.0, (temp - limit) / CONTACT_SPAN_K), 4)
    if contact_claimed(w, hot, support_id, temp, severity):
        return None
    sp = props.props(state, support_id)
    if sp.brittle and finite(sp.shock_k) is not None:
        return "shock" if shock_check(w, support_id, temp, COUPLING_CONTACT, by=by) else None
    kind = contact_mark(sp.cls, temp)
    processes.start(w, "contact", hot, support=support_id, damage=kind, s=0.0, by=by)
    return kind


def contact_begin(w, eid, values):
    """Parameters of a contact accrual: the support, the mark it makes, the contact time so far, attribution."""
    if values.get("support") not in w.s["entities"]:
        return None
    return {"support": values["support"], "damage": values.get("damage") or "scorch",
            "s": round(num(values.get("s")), 3), "by": values.get("by")}


def contact_tick(w, eid, dt):
    """Accrue the contact mark while the hot item stays and stays above the support's limit."""
    state = w.s
    p = processes.params(state, "contact", eid)
    sid = p.get("support")
    if not _resting_on(state, eid, sid):
        return False
    limit = heat_limit_c(state, sid)
    temp = contact_temp_c(state, eid)
    if limit is None or temp <= limit:
        return False
    contact = num(p.get("s")) + dt
    kind = p.get("damage") or "scorch"
    severity = round(min(1.0, (temp - limit) / CONTACT_SPAN_K) * min(1.0, contact / CONTACT_RAMP_S), 4)
    current = damage_level(state["entities"][sid], kind)
    if severity > current + 1e-9:
        impact.damage(w, sid, kind, severity, detail="hot item rested on it", by=p.get("by"))
        if current <= 0:
            event, mark = MARK_EVENTS.get(kind, MARK_EVENTS["scorch"])
            processes.consequence(
                w, event, sid, blame(w, sid, p.get("by")), "{} got {}".format(sid, mark), ids=[sid, eid], hot=eid,
                damage=kind, temp_c=round(temp, 1), limit_c=round(limit, 1), room=processes.floor_cell(state, sid)[0])
    processes.set_params(w, "contact", eid, s=round(contact, 3))
    return True


def contact_end(w, eid):
    """Nothing to clear: the mark stays."""


# ----- condensation and wet contact ---------------------------------------------------------------------------------

def dew_point_c(t_c, rh):
    """Dew point (degC) by the Magnus formula (Sonntag constants 17.62 / 243.12 degC, as climate uses);
    rh is a fraction (0.45 = 45 %). 21 degC at 45 % gives 8.6 degC."""
    rh = min(1.0, max(0.01, num(rh)))
    g = math.log(rh) + 17.62 * t_c / (243.12 + t_c)
    return 243.12 * g / (17.62 - g)


def _air(state, eid):
    """(temperature, relative humidity) of the air around an entity: its room's, or outdoors the weather's (generated
    outdoor rooms carry a nominal humidity of their own; the weather's wins when it has one)."""
    rid = root_room(state, eid)
    room = state["rooms"].get(rid) or {}
    temp = _kernel().room_temperature(state, rid) if rid in state["rooms"] else \
        float(state.get("utilities", {}).get("indoor_temp_c", 21.0))
    rh = (state.get("weather") or {}).get("humidity") if room.get("outdoor") else None
    if rh is None:
        rh = room.get("humidity")
    return temp, num(rh, 0.45)


def sweats(state, eid):
    """The item (and the liquid in it) is colder than the dew point of the air around it minus 1 K: condensation
    forms on it. A 4 degC can sweats at 21 degC and 45 % RH (dew point 8.6 degC)."""
    temp, rh = _air(state, eid)
    item = num(state["entities"][eid].get("temperature_c"), temp)
    return item < dew_point_c(temp, rh) - SWEAT_MARGIN_K


def is_wet(state, eid):
    """Wet enough to mark a finish: wetness >= 0.3, leaking or dripping, or sweating."""
    rec = state["entities"][eid]
    if num(rec.get("wetness")) >= WET_ITEM or rec.get("leaking") or rec.get("dripping"):
        return True
    return sweats(state, eid)


def wet_contact_rule(cls):
    """(damage kind, delay s) wet contact causes on a finish of this material class, None when it causes none."""
    return WET_CONTACT_RULES.get(cls, WET_CONTACT_DEFAULT)


def wet_contact_check(w, item, support_id=None, by=None):
    """wet_contact law: a wet item resting directly on a water-sensitive finish queues the check that marks it (one
    pending check per item and support). Returns the damage kind queued, or None."""
    state = w.s
    rec = state["entities"].get(item)
    if rec is None:
        return None
    support_id = support_id or rec.get("location")
    if not _resting_on(state, item, support_id):
        return None
    sp = props.props(state, support_id)
    rule = wet_contact_rule(sp.cls)
    if rule is None or num(sp.wsens) < WET_SENSITIVE or not is_wet(state, item):
        return None
    if any(entry[4].get("support") == support_id for entry in processes.queued(state, "wet_contact", item)):
        return None
    kind, delay = rule
    processes.queue_add(w, processes.now_s(state) + delay, "wet_contact", item, support=support_id, damage=kind,
                        by=by)
    return kind


def wet_contact_due(w, item, args):
    """The wet-contact check (queue kind wet_contact): still there and still wet marks the finish (0.3, then +0.2
    each further period it stays wet), and paper or electronics also get phys.wetted."""
    state = w.s
    sid, kind = args.get("support"), args.get("damage") or WET_CONTACT_DEFAULT[0]
    if not _resting_on(state, item, sid) or not is_wet(state, item):
        return
    current = damage_level(state["entities"][sid], kind)
    severity = round(min(1.0, current + (WET_MARK_FIRST if current <= 0 else WET_MARK_STEP)), 4)
    impact.damage(w, sid, kind, severity, detail="wet item rested on it", by=args.get("by"))
    if kind == "water_damage":
        liquids.run_wetted(w, sid, CONTACT_SEEP_L, state["entities"][item].get("liquid") or "water", item,
                           num(state["entities"][sid].get("wetness")))
    if current <= 0:
        processes.consequence(w, "phys.ring", sid, blame(w, sid, args.get("by")), "{} got a water mark".format(sid),
                              ids=[sid, item], item=item, damage=kind, room=processes.floor_cell(state, sid)[0])
    rule = wet_contact_rule(props.props(state, sid).cls)
    if severity < 1.0 and rule is not None:
        processes.queue_add(w, processes.now_s(state) + rule[1], "wet_contact", item, support=sid, damage=kind,
                            by=args.get("by"))


# ----- thermal shock ------------------------------------------------------------------------------------------------

def shock_check(w, eid, other_temp_c, coupling, by=None):
    """thermal_shock: eid fails when |T - other| x coupling exceeds its shock tolerance (props shock_k, variation
    applied): it breaks (cause "thermal_shock"), or chips when its break mode is "chip" (enamel, stone). Returns
    True when it failed. Anchor:
    boiling soup into a 4 degC soda-lime jar (96 K > 45 K) cracks it; borosilicate (160 K) survives."""
    rec = w.s["entities"].get(eid)
    if rec is None or rec.get("broken"):
        return False
    p = props.props(w.s, eid)
    limit = finite(p.shock_k)
    if limit is None:
        return False
    delta = abs(num(rec.get("temperature_c"), 21.0) - num(other_temp_c)) * num(coupling)
    if delta <= limit:
        return False
    chips = p.break_mode == "chip"
    processes.consequence(w, "phys.shock", eid, blame(w, eid, by),
                          "{} {}".format(eid, "chipped" if chips else "cracked"), ids=[eid], delta_k=round(delta, 1),
                          limit_k=round(limit, 1), coupling=num(coupling), room=processes.floor_cell(w.s, eid)[0])
    if chips:
        impact.damage(w, eid, "chipped", round(min(1.0, delta / limit - 1.0 + WET_MARK_FIRST), 4),
                      detail="thermal shock", by=by)
    else:
        impact.break_entity(w, eid, "thermal_shock", by=by)
    return True


def liquid_coupling(body_temp_c, liquid_temp_c):
    """Coupling of a liquid against a body it wets all over (poured in, or the body immersed): 1.0 when the liquid is
    colder than the body (downshock), COUPLING_UPSHOCK (0.6) when it is hotter (upshock)."""
    return COUPLING_UPSHOCK if num(liquid_temp_c) > num(body_temp_c, 21.0) else COUPLING_IMMERSION


def pour_shock_check(w, vessel, liquid_temp_c, by=None):
    """The thermal-shock check of a liquid poured into a vessel (liquids.pour_in, so transfer and faucets): only while
    the thermal_shock law is active (it is the law's automatic application; deferred, TIER1_PHYSICS 10.2), with the
    coupling of liquid_coupling against the vessel's own temperature. True when the vessel failed."""
    if not lawbook.active("thermal_shock"):
        return False
    rec = w.s["entities"].get(vessel)
    if rec is None:
        return False
    coupling = liquid_coupling(rec.get("temperature_c", 21.0), liquid_temp_c)
    return shock_check(w, vessel, liquid_temp_c, coupling, by=by)


def shock_exposure(state, eid):
    """(other temperature, coupling) eid is exposed to where it rests, or None: an oven or freezer interior
    (internal_temp_c: 0.5 when hotter than eid, 0.3 when colder), immersion in a holder's liquid (liquid_coupling:
    1.0 into colder liquid, 0.6 into hotter), a wet stone, metal or ceramic top (0.6)."""
    entities = state["entities"]
    rec = entities[eid]
    parent = entities.get(rec.get("location"))
    if parent is None:
        return None
    temp = num(rec.get("temperature_c"), 21.0)
    if parent.get("internal_temp_c") is not None:
        inside = num(parent["internal_temp_c"])
        return inside, COUPLING_OVEN if inside > temp else COUPLING_FREEZER
    relation = rec.get("relation", "on")
    if relation == "in" and num(parent.get("liquid_l")) > 0:
        liquid = num(parent.get("temperature_c"), 21.0)
        return liquid, liquid_coupling(temp, liquid)
    if relation == "on" and num(parent.get("wetness")) >= WET_ITEM and \
            props.props(state, rec["location"]).cls in CONDUCTIVE_TOPS:
        return num(parent.get("temperature_c"), 21.0), COUPLING_CONTACT
    return None


# ----- freezing and thawing -----------------------------------------------------------------------------------------

def freeze_point_c(rec):
    """Freezing point (degC) of the liquid a record holds (liquids.json freeze_c), None when it has none or no
    liquid."""
    if num(rec.get("liquid_l")) <= 0:
        return None
    return finite(props.liquid_props(rec.get("liquid") or "water").get("freeze_c"))


def phase_needed(state, eid):
    """"freeze", "thaw" or None for a liquid in a container (no thermal owner) at its freezing point: freezing while
    the ambient is colder and not all is ice, thawing while it is warmer and some is ice."""
    rec = state["entities"][eid]
    if domain_owned(rec) or num(rec.get("capacity_l")) <= 0:
        return None
    freeze = freeze_point_c(rec)
    if freeze is None:
        return None
    ambient, _ = _kernel().ambient(state, eid)
    temp = num(rec.get("temperature_c"), 21.0)
    ice = num(rec.get("ice_frac"))
    if ambient < freeze and ice < 1.0 and temp <= freeze + 1e-6:
        return "freeze"
    if ambient > freeze and ice > 0.0 and temp >= freeze - 1e-6:
        return "thaw"
    return None


def phase_begin(w, eid, values):
    """Hold the liquid at its freezing point (the plateau) while it freezes or thaws; declines when not needed."""
    rec = w.s["entities"][eid]
    if domain_owned(rec) or num(rec.get("capacity_l")) <= 0:
        return None
    freeze = freeze_point_c(rec)
    if freeze is None:
        return None
    if num(rec.get("temperature_c"), 21.0) != round(freeze, 3):
        w.mut(eid)["temperature_c"] = round(freeze, 3)
    return {"on": True}


def phase_conductance_w_k(rec):
    """Heat conductance hA (W/K) between a container's liquid and its surroundings during freezing or thawing: the
    lumped-capacitance law's own (heat capacity with the liquid all liquid / tau_s of the same record, so h = 10 W/m2K
    over A = 2(wd + wh + dh), with tau_s's clamps and an explicit thermal_tau_s honoured), whatever the thermal_tau
    law's state: the latent heat must leave through the same surface whichever time constant sensible cooling uses."""
    liquid = dict(rec)
    liquid.pop("ice_frac", None)
    return heat_capacity_j_k(liquid) / tau_s(liquid)


def phase_rate_per_s(rec, coupling, delta_k):
    """Fraction of the liquid that freezes (or thaws) per second at |T_f - T_amb| = delta_k: coupling x hA x delta_k /
    (m_liquid x L), L = 334 kJ/kg (water's latent heat of fusion; the household liquids that freeze are aqueous).
    A frozen 0.5 L bottle (6.5 x 6.5 x 20 cm) thaws on a 21 C floor in about 3.7 h: 167 kJ through 0.6 W/K x 21 K."""
    mass = num(rec.get("liquid_l")) * liquids.liquid_density_kg_l(rec)
    if mass <= 0:
        return math.inf
    return num(coupling) * phase_conductance_w_k(rec) * abs(delta_k) / (mass * LATENT_FUSION_J_KG)


def phase_step(w, eid, dt):
    """Advance freezing or thawing by dt: ice_frac moves by dt x phase_rate_per_s (coupling x hA x |T_f - T_amb| /
    (m_liquid x L)) with the temperature held at T_f; freezing re-checks the container for a burst. False when fully
    frozen, fully thawed, broken, emptied or taken over by a domain (the plateau ends and the kernel thermal system
    resumes)."""
    state = w.s
    rec = state["entities"][eid]
    freeze = freeze_point_c(rec)
    if freeze is None or domain_owned(rec) or rec.get("broken"):
        return False
    ambient, coupling = _kernel().ambient(state, eid)
    if ambient == freeze:
        return True
    shown = num(rec.get("ice_frac"))
    exact = finite(processes.params(state, "phase", eid).get("ice"))
    # the process keeps the exact progress; ice_frac (4 decimals) is what everything else reads. Someone else
    # changing ice_frac (a domain, a setup) makes it the truth again.
    ice = exact if exact is not None and abs(round(exact, 4) - shown) < 1e-9 else shown
    rate = dt * phase_rate_per_s(rec, coupling, freeze - ambient)
    ice = min(1.0, ice + rate) if ambient < freeze else max(0.0, ice - rate)
    m = w.mut(eid)
    m["temperature_c"] = round(freeze, 3)
    if round(ice, 4) > 0:
        m["ice_frac"] = round(ice, 4)
    else:
        m.pop("ice_frac", None)
    processes.set_params(w, "phase", eid, ice=round(ice, ICE_DIGITS))
    if ambient < freeze:
        freeze_burst_check(w, eid)
        return ice < 1.0 and not state["entities"][eid].get("broken")
    return ice > 0.0


def phase_end(w, eid):
    """The plateau ends: the _phys_phase marker goes with the process, so the kernel thermal system resumes."""


def freeze_burst_check(w, eid, by=None, fill=None, ice=None):
    """A sealed, rigid container whose ice needs more room than its headspace: 0.09 x fill x ice_frac (+0.03 x
    ice_frac for a narrow glass neck, which plugs with ice first) > 1 - fill. Glass and ceramics burst, a metal can
    bulges and bursts beyond 2 % excess, plastic bulges. Returns "burst", "bulged" or None. Food calls this for its
    own containers. fill / ice: the filled fraction of the capacity and the frozen fraction of the contents when the
    caller keeps them in its own model (food's adoption request 3); default the record's liquid_l / capacity_l and
    ice_frac."""
    state = w.s
    rec = state["entities"].get(eid)
    if rec is None or rec.get("broken"):
        return None
    if fill is None:
        cap, liters = num(rec.get("capacity_l")), num(rec.get("liquid_l"))
        if cap <= 0 or liters <= 0:
            return None
        fill = liters / cap
    fill = min(1.0, max(0.0, num(fill)))
    if fill <= 0 or not liquids.container_closed(rec):
        return None
    p = props.props(state, eid)
    if not p.rigid:
        return None
    ice = min(1.0, max(0.0, num(rec.get("ice_frac") if ice is None else ice)))
    need = ICE_EXPANSION * fill * ice
    if p.cls in SHATTER_CLASSES and liquids.mouth_frac(rec, p.mouth_frac) < liquids.WIDE_MOUTH:
        need += NARROW_NECK_MARGIN * ice
    excess = need - (1.0 - fill)
    if excess <= 1e-9:
        return None
    who = blame(w, eid, by)
    room = processes.floor_cell(state, eid)[0]
    if p.cls in SHATTER_CLASSES or (p.cls in METAL_CLASSES and excess > CAN_BURST_EXCESS):
        processes.consequence(w, "phys.burst", eid, who, "{} burst".format(eid), ids=[eid], outcome="burst",
                              excess=round(excess, 4), room=room)
        impact.break_entity(w, eid, "freeze_burst", by=by)
        return "burst"
    severity = round(min(1.0, excess / CAN_BURST_EXCESS), 4)
    current = damage_level(rec, "bulged")
    if severity > current + 1e-9:
        impact.damage(w, eid, "bulged", severity, detail="frozen contents expanded", by=by)
        if current <= 0:
            processes.consequence(w, "phys.burst", eid, who, "{} bulged".format(eid), ids=[eid], outcome="bulged",
                                  excess=round(excess, 4), room=room)
    return "bulged"


# ----- melting ------------------------------------------------------------------------------------------------------

def melt_kind(cls):
    return "melted" if cls in LIQUEFY_CLASSES else "deformed"


def melt_check(w, eid, dt):
    """melt: above its melting point a body accrues damage.melted (wax, soap, film, foam plastic) or deformed (other
    plastics, rubber): dt / 600 s x min(1, (T - melt_c) / 10 K) per call. Returns the severity now."""
    state = w.s
    rec = state["entities"][eid]
    p = props.props(state, eid)
    melt_c = finite(p.melt_c)
    if melt_c is None:
        return 0.0
    kind = melt_kind(p.cls)
    current = damage_level(rec, kind)
    temp = num(rec.get("temperature_c"), 21.0)
    if temp <= melt_c or current >= 1.0:
        return current
    severity = round(min(1.0, current + dt / MELT_FULL_S * min(1.0, (temp - melt_c) / MELT_SPAN_K)), 4)
    impact.damage(w, eid, kind, severity, detail="softened by heat")
    if current <= 0:
        processes.consequence(w, "phys.melt", eid, blame(w, eid), "{} {}".format(eid, kind), ids=[eid], damage=kind,
                              temp_c=round(temp, 1), melt_c=round(melt_c, 1),
                              room=processes.floor_cell(state, eid)[0])
    return severity


def melt_begin(w, eid, values):
    melt_c = finite(props.props(w.s, eid).melt_c)
    if melt_c is None or num(w.s["entities"][eid].get("temperature_c"), 21.0) <= melt_c:
        return None
    return {"on": True}


def melt_tick(w, eid, dt):
    severity = melt_check(w, eid, dt)
    melt_c = finite(props.props(w.s, eid).melt_c)
    return severity < 1.0 and melt_c is not None and num(w.s["entities"][eid].get("temperature_c"), 21.0) > melt_c


def melt_end(w, eid):
    """Nothing to clear: the damage stays."""


# ----- burns and heat content ---------------------------------------------------------------------------------------

def burn_seconds(t_c, child=False):
    """Contact time (s) until a burn at skin temperature t_c: Moritz and Henriques adult values (44 degC 6 h,
    47 degC 20 min, 50 degC 3 min, 55 degC 20 s, 60 degC 5 s, 65 degC 2 s, 70 degC 1 s), log-linear in between and
    beyond 70 degC; inf below 44 degC. A child's skin burns in a quarter of the time."""
    t_c = num(t_c)
    if t_c < BURN_TABLE[0][0]:
        return math.inf
    for (t0, s0), (t1, s1) in zip(BURN_TABLE, BURN_TABLE[1:]):
        if t_c <= t1 or (t1, s1) == BURN_TABLE[-1]:
            seconds = s0 * (s1 / s0) ** ((t_c - t0) / (t1 - t0))
            break
    return seconds * (CHILD_BURN_FACTOR if child else 1.0)


def _enthalpy_j_kg(temp, freeze, ice, cp_liquid):
    """Specific enthalpy (J/kg) of a freezable liquid relative to solid ice at its freezing point."""
    return (1.0 - ice) * (LATENT_FUSION_J_KG + cp_liquid * (temp - freeze)) + ice * ICE_CP * (temp - freeze)


def heat_content_j(rec, t_ref):
    """Heat (J) to remove to bring a body and its liquid from their temperature to t_ref (negative: to add):
    sensible heat of the body, and of the liquid with its latent heat of fusion when it crosses its freezing point
    (the freezer heat load of an item put in). A 0.5 L bottle from 21 to -18 degC is about 0.23 MJ."""
    temp = num(rec.get("temperature_c"), 21.0)
    t_ref = num(t_ref)
    total = num(rec.get("mass_kg")) * (body_cp(rec) or 0.0) * (temp - t_ref)
    liters = num(rec.get("liquid_l"))
    if liters <= 0:
        return total
    mass = liters * liquids.liquid_density_kg_l(rec)
    cp_liquid = liquid_cp(rec.get("liquid") or "water")
    freeze = freeze_point_c(rec)
    if freeze is None:
        return total + mass * cp_liquid * (temp - t_ref)
    ice = min(1.0, max(0.0, num(rec.get("ice_frac"))))
    ice_ref = 1.0 if t_ref < freeze else 0.0 if t_ref > freeze else ice
    return total + mass * (_enthalpy_j_kg(temp, freeze, ice, cp_liquid) -
                           _enthalpy_j_kg(t_ref, freeze, ice_ref, cp_liquid))


# ----- law entry points (registered by PHYS-INT through LAWS) -------------------------------------------------------

def law_contact_heat(w, eid, env=None, cmap=None):
    """contact_heat (80): a hot item resting on a support above its heat limit starts marking it (the mark accrues
    in the contact process over the following substeps); a brittle top cracks at once."""
    rec = w.s["entities"].get(eid)
    if rec is None or rec.get("location") not in w.s["entities"] or rec.get("relation", "on") != "on":
        return []
    start = len(impact.consequences(w))
    contact_check(w, eid, rec["location"])
    return impact.consequences(w)[start:]


def law_wet_contact(w, eid, env=None, cmap=None):
    """wet_contact (85): a wet item resting on a water-sensitive finish queues its check (nothing happens yet)."""
    rec = w.s["entities"].get(eid)
    if rec is not None and rec.get("location") in w.s["entities"]:
        wet_contact_check(w, eid, rec["location"])
    return []


def law_thermal_shock(w, eid, env=None, cmap=None):
    """thermal_shock (90): a body moved into an oven or freezer, into a liquid or onto a wet conductive top is
    shock-checked against it."""
    if eid not in w.s["entities"]:
        return []
    exposure = shock_exposure(w.s, eid)
    if exposure is None:
        return []
    start = len(impact.consequences(w))
    shock_check(w, eid, exposure[0], exposure[1])
    return impact.consequences(w)[start:]


def law_phase(w, eid, env=None, cmap=None):
    """phase (95): a liquid at its freezing point somewhere colder (or partly frozen somewhere warmer) starts
    freezing (thawing); the kernel thermal step starts the others when their temperature crosses the point."""
    if eid in w.s["entities"] and not processes.is_active(w.s, "phase", eid) and phase_needed(w.s, eid):
        processes.start(w, "phase", eid)
    return []


def law_melt(w, eid, env=None, cmap=None):
    """melt (96): a body above its melting point starts melting (the damage accrues in the melting process)."""
    if eid in w.s["entities"] and not processes.is_active(w.s, "melting", eid):
        processes.start(w, "melting", eid)
    return []


LAWS = (("contact_heat", 80, law_contact_heat), ("wet_contact", 85, law_wet_contact),
        ("thermal_shock", 90, law_thermal_shock), ("phase", 95, law_phase), ("melt", 96, law_melt))
