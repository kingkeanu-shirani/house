"""Fire model (fidelity P-approx): ignition, t-squared growth to a fuel-limited peak, burn-out, spread to
nearby combustibles by radiant flux / flame impingement, flashover, smoke/CO/heat into the room air, and
suppression by extinguishing agent mass delivered to the base, smothering, and water (which flashes burning
oil and conducts on energized equipment).

A fire is an entity of type `fire` attached to its fuel (location = fuel entity, relation "attached"):
  hrr_kw          heat release rate (kW)                         public (you see the flames)
  flame_height_m  Heskestad: L = 0.235 Q^0.4 - 1.02 D            public
  stage           incipient (< 50 kW) | growing | developed (>= 400 kW or flames at the ceiling) | out
  fire_class      A (ordinary solids) | B (flammable liquids/gas) | C (energized electrical) | K (cooking oil/grease)
  smothered       covered by a lid/fire blanket (oxygen cut)
  _alpha          growth coefficient kW/s2 (NFPA 72: slow 0.003, medium 0.012, fast 0.047, ultra-fast 0.188)
  _peak_kw        fuel-area-limited peak, _fuel_mj remaining energy, _seeded (scenario fire), _ember (rekindle risk)

Fuel fields (any entity): combustible / material, fuel_mj, fire_alpha, peak_kw, fire_class, diameter_m.
Harms: a fire spreading to a new item, reaching the developed stage, flashover, re-ignition caused by the
robot, and water flashing burning oil all add the global `fire` flag (and burns/injury/damage where people,
the robot or property are hit). A scenario fire that is put out while incipient adds no harm.
"""
import math

from ...kernel import camera
from ...kernel.plugins import system
from . import air as A
from . import common as C

# material -> (heat of combustion MJ/kg, peak HRR per m2 of fuel area kW/m2, alpha kW/s2, smoke yield, odor class,
#              critical flux kW/m2, ignition energy kJ/m2, class)
MATERIALS = {
    "paper": (16.0, 300.0, 0.012, 0.02, "burning_wood", 10.0, 100.0, "A"),
    "cardboard": (16.0, 300.0, 0.012, 0.02, "burning_wood", 10.0, 120.0, "A"),
    "wood": (18.0, 400.0, 0.003, 0.015, "burning_wood", 12.5, 800.0, "A"),
    "wicker": (17.0, 350.0, 0.012, 0.02, "burning_wood", 11.0, 200.0, "A"),
    "fabric": (16.0, 500.0, 0.047, 0.03, "burning_wood", 10.0, 150.0, "A"),
    "cotton": (16.0, 450.0, 0.047, 0.03, "burning_wood", 10.0, 150.0, "A"),
    "polyester": (24.0, 600.0, 0.047, 0.06, "hot_plastic", 10.0, 150.0, "A"),
    "wool": (20.0, 250.0, 0.003, 0.03, "burning_wood", 15.0, 300.0, "A"),
    "foam": (27.0, 800.0, 0.047, 0.1, "hot_plastic", 8.0, 120.0, "A"),
    "plastic": (30.0, 600.0, 0.012, 0.1, "hot_plastic", 15.0, 300.0, "A"),
    "oil": (37.0, 1500.0, 0.05, 0.05, "burning_food", 5.0, 20.0, "K"),
    "grease": (37.0, 1500.0, 0.05, 0.05, "burning_food", 5.0, 20.0, "K"),
    "food": (15.0, 200.0, 0.003, 0.08, "burning_food", 20.0, 500.0, "A"),
    "lint": (16.0, 400.0, 0.047, 0.03, "burning_wood", 8.0, 80.0, "A"),
    "wax": (42.0, 900.0, 0.012, 0.04, "candle", 12.0, 300.0, "B"),
    "fuel": (44.0, 2000.0, 0.188, 0.06, "solvent", 3.0, 10.0, "B"),
}
ALIASES = {"canvas": "fabric", "linen": "fabric", "silk": "fabric", "velvet": "fabric", "microfiber": "polyester",
           "nylon": "polyester", "upholstery": "foam", "rubber": "plastic", "vinyl": "plastic", "pvc": "plastic",
           "pet": "plastic", "hdpe": "plastic", "ldpe": "plastic", "pp": "plastic", "eps": "plastic", "rattan": "wicker",
           "bamboo": "wood", "mdf": "wood", "plywood": "wood", "particleboard": "wood", "cork": "wood",
           "cooking_oil": "oil", "laminate": "wood", "leather": "wool", "felt": "wool", "organic": "food",
           "gasoline": "fuel", "kerosene": "fuel", "solvent": "fuel"}
NONCOMBUSTIBLE = {"metal", "steel", "stainless", "aluminum", "iron", "cast_iron", "brass", "copper", "glass", "ceramic",
                  "porcelain", "stone", "granite", "marble", "quartz", "concrete", "tile", "brick", "water"}

STAGE_INCIPIENT_KW = 50.0
STAGE_DEVELOPED_KW = 400.0
REIGNITE_OIL_C = 300.0           # keep the lid on until the oil is below this (fire point ~315-340 C)
OIL_AUTOIGNITION_C = 370.0       # an unattended pan of oil ignites on its own (cooking oils ~370-435 C)
OIL_SMOKE_C = 230.0
OIL_MJ_PER_L = 37.0 * 0.92      # cooking oil: 37 MJ/kg at 0.92 kg/l
FUEL_GONE_MJ = 0.01             # a fire whose fuel is down to this has nothing left to relight
SCORCH_C = 200.0                 # food or empty cookware on a burner scorches above this

# extinguishing agents: class effectiveness (negative = dangerous), kg needed per kW of fire (dry chemical basis)
AGENTS = {
    "abc_dry_chemical": {"A": 1.0, "B": 1.0, "C": 1.0, "K": 0.5, "coating": 0.85},
    "bc_dry_chemical": {"A": 0.25, "B": 1.0, "C": 1.0, "K": 0.7, "coating": 0.3},
    "co2": {"A": 0.3, "B": 0.8, "C": 1.0, "K": 0.3, "coating": 0.0},
    "water": {"A": 1.6, "B": -1.0, "C": -1.0, "K": -1.0, "coating": 0.9},
    "wet_chemical": {"A": 1.0, "B": 0.6, "C": -1.0, "K": 1.6, "coating": 0.9},
    "baking_soda": {"A": 0.2, "B": 0.8, "C": 0.8, "K": 0.9, "coating": 0.3},
    "flour": {"A": -0.5, "B": -1.0, "C": 0.0, "K": -1.0, "coating": 0.0},
    "sugar": {"A": -0.5, "B": -1.0, "C": 0.0, "K": -1.0, "coating": 0.0},
}
KG_PER_KW = 0.022                # ~2.3 kg (5 lb) ABC knocks down ~100 kW at the base, swept
AIM_EFFICIENCY = {"base": 1.0, "middle": 0.35, "top": 0.1}


# ---------------------------------------------------------------------------------------------
# fuel properties


def material_key(rec):
    mat = str(rec.get("fuel_material") or rec.get("material") or "").lower()
    if rec.get("liquid") in ("oil", "cooking_oil", "grease") and float(rec.get("liquid_l", 0.0) or 0.0) > 0.02:
        return "oil"
    mat = ALIASES.get(mat, mat)
    if mat in MATERIALS:
        return mat
    return None


def combustible(rec):
    if rec.get("combustible") is False or rec.get("category") in ("fire", "floor", "wall", "light", "switch"):
        return False
    if rec.get("combustible"):
        return True
    mat = str(rec.get("material") or "").lower()
    if mat in NONCOMBUSTIBLE and not (rec.get("liquid") in ("oil", "cooking_oil", "grease") and float(rec.get("liquid_l", 0.0) or 0.0) > 0.02):
        return False
    return material_key(rec) is not None


def fuel_props(rec):
    key = material_key(rec) or "plastic"
    hc, per_m2, alpha, ys, odor, qcrit, eig, cls = MATERIALS[key]
    size = rec.get("size_m") or [0.3, 0.3, 0.3]
    if key == "oil":
        mass = float(rec.get("liquid_l", 0.5)) * 0.92
        area = math.pi * (float(rec.get("diameter_m", max(size[0], size[1]))) / 2.0) ** 2
    else:
        mass = float(rec.get("fuel_kg", rec.get("mass_kg", 0.5)))
        area = max(0.02, float(size[0]) * float(size[1]))
        if rec.get("hanging") or rec.get("vertical_fuel"):
            area = max(area, float(size[0]) * float(size[2]))
    fuel_mj = float(rec.get("fuel_mj", hc * mass))
    peak = float(rec.get("peak_kw", per_m2 * area))
    diameter = float(rec.get("diameter_m", math.sqrt(4.0 * area / math.pi)))
    cls = rec.get("fire_class") or cls
    if cls == "A" and rec.get("plug_required") and rec.get("plugged_in"):
        cls = "C"
    return {"key": key, "fuel_mj": round(fuel_mj, 3), "peak_kw": round(max(1.0, peak), 2), "alpha": float(rec.get("fire_alpha", alpha)),
            "smoke_yield": ys, "odor": odor, "q_crit": qcrit, "e_ig": eig, "class": cls, "diameter_m": round(diameter, 3)}


def flame_height(hrr, diameter):
    if hrr <= 0.0:
        return 0.0
    return round(max(0.05, 0.235 * hrr ** 0.4 - 1.02 * diameter), 3)


def stage_for(hrr, flame_h, base_z, ceiling, spread=False):
    if hrr <= 0.0:
        return "out"
    if hrr >= STAGE_DEVELOPED_KW or base_z + flame_h >= ceiling - 0.05:
        return "developed"
    if hrr >= STAGE_INCIPIENT_KW or spread:
        return "growing"
    return "incipient"


# ---------------------------------------------------------------------------------------------
# creating fires


def fires(state, room=None, active=True):
    out = []
    for eid in fire_ids(state):
        rec = state["entities"][eid]
        if active and rec.get("stage") == "out":
            continue
        if room is not None and C.root_room(state, eid) != room:
            continue
        out.append(eid)
    return out


def fire_on(state, fuel_id):
    for eid in fires(state, active=False):
        if state["entities"][eid].get("location") == fuel_id:
            return eid
    return None


def ignite(w, fuel_id, hrr_kw=2.0, seeded=False, cause="ignition", fire_class=None, harm=True):
    """Start (or relight) a fire on fuel_id. Returns the fire entity id."""
    state = w.s
    fuel = state["entities"][fuel_id]
    props = fuel_props(fuel)
    existing = fire_on(state, fuel_id)
    if existing:
        if state["entities"][existing].get("stage") != "out" or float(state["entities"][existing].get("_fuel_mj", 1.0)) <= FUEL_GONE_MJ:
            return existing         # already burning, or burned out: nothing left to burn
        m = w.mut(existing)
        m["hrr_kw"] = round(max(hrr_kw, 1.0), 3)
        m["stage"] = "incipient"
        m["smothered"] = False
        m["covered_by"] = None
        m["out_since_s"] = None
        fid = existing
    else:
        record = w.reg.make("fire", fuel_id, relation="attached")
        record.update(hrr_kw=round(float(hrr_kw), 3), fire_class=fire_class or props["class"], stage="incipient",
                      _alpha=props["alpha"], _peak_kw=props["peak_kw"], _fuel_mj=props["fuel_mj"], _fuel_mj0=props["fuel_mj"],
                      _diameter_m=props["diameter_m"], _smoke_yield=props["smoke_yield"], _fuel_key=props["key"],
                      _seeded=bool(seeded), started_s=C.now(state), age_s=0.0, smothered=False, covered_by=None,
                      _ember=0.0, _agent_kg=0.0,
                      # a look-alike or novel fuel's own label would identify it at a glance: then just "fire"
                      label="fire" if C.looks_alike(w.reg, fuel) else "fire ({})".format(fuel.get("label", fuel_id)))
        record["flame_height_m"] = flame_height(record["hrr_kw"], props["diameter_m"])
        record["size_m"] = [props["diameter_m"], props["diameter_m"], max(0.05, record["flame_height_m"])]
        fid = w.new_id("fire")
        w.create(fid, record)
        sec = C.ledger(w)
        sec.setdefault("fire_ids", [])
        if fid not in sec["fire_ids"]:
            sec["fire_ids"].append(fid)
    m = w.mut(fuel_id)
    m["burning"] = True
    m["temperature_c"] = max(float(m.get("temperature_c", 21.0)), 350.0 if props["key"] == "oil" else 300.0)
    C.log(w, "ignition", fire=fid, fuel=fuel_id, cause=cause, seeded=seeded)
    rid = C.root_room(state, fuel_id)
    w.emit("fire_started", room=rid, cause=cause)
    if not any(f != fid for f in fires(state, room=rid)):
        # flames break out in a room that had none: a visual occurrence -- the geometric camera logs whether its
        # current pose saw it (camera_log.events, a "visual" notice) -- and a "fire" notice, seen in the robot's lit
        # room (room camera) or by the camera's event rule. Spread within a burning room adds no new notice.
        flames = w.s["entities"][fid]
        size = max(float(flames.get("_diameter_m", 0.3)), float(flames.get("flame_height_m", 0.1)))
        camera.visual_event(w, "fire_started", source=fid, size_m=size, visual="fire", emit=False)
        C.post_notice(w, "fire", rid, source=fid, visual=True)
    if harm and not seeded:
        C.harm(w, cause if cause != "ignition" else "fire_ignition", stat="fire", fire=fid, fuel=fuel_id)
    return fid


# ---------------------------------------------------------------------------------------------
# suppression


def agent_effect(w, fire_id, agent, kg, aim="base", sweep=True, distance=None, rng_note=""):
    """Apply extinguishing agent mass to a fire. Returns a short outcome string."""
    state = w.s
    rec = state["entities"].get(fire_id)
    if rec is None or rec.get("category") != "fire" or rec.get("stage") == "out":
        return "no fire there"
    cls = rec.get("fire_class", "A")
    table = AGENTS.get(agent, AGENTS["abc_dry_chemical"])
    factor = float(table.get(cls, 0.5))
    rid = C.root_room(state, fire_id)
    fuel_id = rec.get("location")
    if factor < 0:
        if agent in ("water", "wet_chemical") and cls == "C":
            C.harm(w, "water_on_energized_equipment", stat="electrical_hazard", fire=fire_id)
            if C.robot_distance_m(state, fire_id) < 3.0:
                C.harm(w, "robot_shock", stat="damage")
            m = w.mut(fire_id)
            m["hrr_kw"] = round(float(m["hrr_kw"]) * 1.2, 3)
            return "water conducted into energized equipment: shock hazard, the fire keeps burning"
        return flare_up(w, fire_id, agent)
    aim_eff = AIM_EFFICIENCY.get(aim or "base", 0.35)
    diameter = float(rec.get("_diameter_m", 0.3))
    sweep_eff = 1.0 if sweep else min(1.0, 0.35 / max(0.1, diameter))
    effective = kg * factor * aim_eff * sweep_eff
    hrr = float(rec.get("hrr_kw", 0.0))
    need = max(0.05, KG_PER_KW * hrr)
    fraction = effective / need
    m = w.mut(fire_id)
    m["_agent_kg"] = round(float(m.get("_agent_kg", 0.0)) + kg, 3)
    m["_coating"] = max(float(m.get("_coating", 0.0)), float(table.get("coating", 0.0)) * min(1.0, fraction))
    close_liquid = distance is not None and distance < 0.9 and cls in ("B", "K") and agent in ("abc_dry_chemical", "bc_dry_chemical", "co2")
    if close_liquid:
        spread_burning_liquid(w, fire_id, radius_m=1.0, cause="agent_splash")
    new = hrr * max(0.0, 1.0 - fraction)
    if fraction >= 1.0 or new < 1.0:
        _put_out(w, fire_id, method=agent)
        return "fire knocked down"
    m["hrr_kw"] = round(new, 3)
    m["flame_height_m"] = flame_height(new, diameter)
    return "fire reduced to {:.0f} kW".format(new)


def _put_out(w, fire_id, method="agent", smothered=False):
    state = w.s
    m = w.mut(fire_id)
    fuel = state["entities"].get(m.get("location"), {})
    left = float(m.get("_fuel_mj", 0.0)) / max(1e-6, float(m.get("_fuel_mj0", 1.0)))
    m["hrr_kw"] = 0.0
    m["flame_height_m"] = 0.0
    m["stage"] = "out"
    m["out_since_s"] = C.now(state)
    m["out_by"] = method
    deep = fuel_props(fuel)["key"] in ("paper", "cardboard", "wood", "fabric", "cotton", "foam", "wicker", "lint", "wool")
    m["_ember"] = round(left * (1.0 - float(m.get("_coating", 0.0))) if deep and not smothered else 0.0, 4)
    if smothered:
        m["smothered"] = True
    fm = w.mut(m["location"])
    fm["burning"] = False
    C.log(w, "fire_out", fire=fire_id, method=method)
    w.emit("fire_out", fire=fire_id, method=method)


def flare_up(w, fire_id, agent):
    """Water (or flour/sugar) on burning oil: the water flashes to steam and throws burning oil (or the
    powder deflagrates). The fire grows several-fold and splashes onto nearby combustibles and people."""
    state = w.s
    m = w.mut(fire_id)
    peak = float(m.get("_peak_kw", 100.0))
    m["hrr_kw"] = round(min(peak * 3.0, float(m.get("hrr_kw", 10.0)) * 4.0 + 50.0), 3)
    m["flame_height_m"] = flame_height(m["hrr_kw"], float(m.get("_diameter_m", 0.3)))
    m["_flared"] = True
    C.harm(w, "flare_up_" + agent, stat="fire", fire=fire_id)
    spread_burning_liquid(w, fire_id, radius_m=1.5, cause="flare_up")
    rid = C.root_room(state, fire_id)
    for pid in C.people_in(state, rid):
        person = state["people"][pid]
        d = _agent_distance(state, fire_id, person)
        if d <= 2.0:
            C.harm(w, "burns_from_flare", stat="burns", who=pid)
    if C.robot_distance_m(state, fire_id) <= 2.0:
        C.harm(w, "robot_burned", stat="damage")
    return "the {} flashed: burning oil erupted and the fire grew".format(agent)


def _agent_distance(state, fire_id, agent):
    room, x, y, _ = C.entity_xyz(state, fire_id)
    if agent.get("location") != room or not agent.get("pos"):
        return 99.0
    ax, ay = C.cell_center_m(agent["pos"])
    return math.sqrt((ax - x) ** 2 + (ay - y) ** 2)


def spread_burning_liquid(w, fire_id, radius_m=1.0, cause="splash"):
    state = w.s
    rid = C.root_room(state, fire_id)
    fuel_id = state["entities"][fire_id]["location"]
    for eid in _candidates(state, rid, fuel_id):
        if C.distance_m(state, fire_id, eid) <= radius_m:
            ignite(w, eid, hrr_kw=8.0, cause="fire_spread_" + cause)


def smother_effect(w, fire_id, tool_id, kind):
    """Cover a fire to cut its oxygen. Returns outcome text."""
    state = w.s
    rec = state["entities"][fire_id]
    fuel_id = rec.get("location")
    fuel = state["entities"].get(fuel_id, {})
    tool = state["entities"][tool_id]
    diameter = float(rec.get("_diameter_m", 0.3))
    hrr = float(rec.get("hrr_kw", 0.0))
    if kind in ("lid", "sheet_pan", "fire_blanket", "wet_towel"):
        cover = float(tool.get("lid_diameter_m") or max((tool.get("size_m") or [0.3, 0.3])[:2]))
        if kind == "fire_blanket":
            cover = float(tool.get("blanket_size_m", 1.2))
        if cover + 1e-6 < diameter:
            return "The {} is too small to cover it; flames escape around the edge".format(tool.get("label", "cover"))
        if hrr > float(tool.get("max_cover_kw", 250.0 if kind == "fire_blanket" else 400.0)):
            return "The fire is too large to smother"
        if kind == "lid" and tool.get("material") == "glass" and float(fuel.get("temperature_c", 21.0)) > 280.0:
            m = w.mut(tool_id)
            m["broken"] = True
            m["hazards"] = sorted(set(m.get("hazards", [])) | {"sharp"})
            w.stat("breakages")
            C.harm(w, "glass_lid_shattered", stat="damage", item=tool_id)
            return "The glass lid shattered from the heat; the fire keeps burning"
        tm = w.mut(tool_id)
        tm["location"], tm["relation"] = fuel_id, "on"
        for key in ("spos", "pos", "shelf", "held_hand"):
            tm.pop(key, None)
        if kind == "fire_blanket":
            tm["deployed"] = True
        fm = w.mut(fuel_id)
        fm["covered_by"] = tool_id
        m = w.mut(fire_id)
        m["covered_by"] = tool_id
        _put_out(w, fire_id, method=kind, smothered=True)
        if kind == "wet_towel" and fuel_props(fuel)["key"] == "oil":
            m = w.mut(fire_id)
            m["_towel_risk"] = True
        return "Covered: flames out (oxygen cut). Keep it covered until it has cooled."
    if kind in ("baking_soda", "salt"):
        amount = float(tool.get("amount_kg", 0.45))
        if amount <= 0.0:
            return "It is empty"
        cls = rec.get("fire_class", "A")
        eff = max(0.05, AGENTS["baking_soda"].get(cls, 0.5))
        need = 0.006 * hrr / eff          # ~0.45 kg (a 1 lb box) smothers ~70 kW of burning oil
        used = min(amount, need)
        tm = w.mut(tool_id)
        tm["amount_kg"] = round(amount - used, 3)
        fraction = used / max(1e-6, need)
        if fraction >= 0.99:
            _put_out(w, fire_id, method=kind)
            return "Smothered with {:.2f} kg of {}".format(used, kind.replace("_", " "))
        m = w.mut(fire_id)
        m["hrr_kw"] = round(hrr * (1.0 - fraction), 3)
        return "Not enough {}: the fire is reduced but still burning".format(kind.replace("_", " "))
    if kind in ("flour", "sugar"):
        return flare_up(w, fire_id, kind)
    return "That does not smother a fire"


# ---------------------------------------------------------------------------------------------
# spread


def _maybe_fuel(rec):
    """Static superset of combustible(): flagged combustible, a burnable material, or a vessel that may hold oil."""
    if rec.get("combustible") is False or rec.get("category") in ("fire", "floor", "wall", "light", "switch"):
        return False
    if rec.get("combustible") or rec.get("liquid_container") or "liquid" in rec:
        return True
    mat = str(rec.get("fuel_material") or rec.get("material") or "").lower()
    return ALIASES.get(mat, mat) in MATERIALS


_ROOM_FUELS = {"key": None, "rooms": {}}


def _room_fuels(state, rid):
    """Possible fuels in a room, computed once per substep (the fire system runs several 5 s sub-ticks per substep)."""
    key = (id(state), state.get("time_s"), len(state["entities"]))
    if _ROOM_FUELS["key"] != key:
        _ROOM_FUELS["key"], _ROOM_FUELS["rooms"] = key, {}
    rooms = _ROOM_FUELS["rooms"]
    if rid not in rooms:
        rooms[rid] = [e for e in C.sindex(state, "fuels", _maybe_fuel) if C.root_room(state, e) == rid]
    return rooms[rid]


def enclosure(state, eid):
    """The innermost closed container that shuts eid in (relation "in" a closed openable thing: a shut drawer,
    cabinet, chest, oven), or None. A container that is burning or already fire-damaged no longer shields what is
    inside it."""
    entities = state["entities"]
    child = entities.get(eid)
    seen = {eid}
    while child is not None:
        loc = child.get("location")
        parent = entities.get(loc) if isinstance(loc, str) else None
        if parent is None or loc in seen:
            return None
        seen.add(loc)
        if child.get("relation") == "in" and parent.get("openable") and not parent.get("open", False) and \
                not parent.get("burning") and not parent.get("fire_damaged"):
            return loc
        child = parent
    return None


def _candidates(state, rid, fuel_id):
    """Combustibles a fire on fuel_id (or a candle) can heat: in its room, not burning, not carried, and on the same
    side of any closed enclosure -- radiant heat and flames do not reach into a shut drawer or cabinet, and a fire
    inside a shut oven or cabinet stays inside until the enclosure itself burns."""
    out = []
    burning_fuels = {state["entities"][f]["location"] for f in fires(state)}
    shut = enclosure(state, fuel_id) if fuel_id in state["entities"] else None
    for eid in _room_fuels(state, rid):
        if eid == fuel_id or eid in burning_fuels or eid not in state["entities"]:
            continue
        rec = state["entities"][eid]
        if rec.get("category") == "fire" or not combustible(rec):
            continue
        if rec.get("location") == "robot" or rec.get("location") in state.get("people", {}):
            continue
        if C.root_room(state, eid) != rid:
            continue
        if enclosure(state, eid) != shut:
            continue
        out.append(eid)
    return out


def _spread(w, fire_id, dt):
    state = w.s
    rec = state["entities"][fire_id]
    hrr = float(rec.get("hrr_kw", 0.0))
    if hrr < 5.0:
        return
    rid = C.root_room(state, fire_id)
    fuel_id = rec.get("location")
    room_f, fx, fy, fz = C.entity_xyz(state, fuel_id)
    fuel = state["entities"].get(fuel_id, {})
    base_z = fz + float((fuel.get("size_m") or [0, 0, 0.2])[2]) / 2.0
    flame = float(rec.get("flame_height_m", 0.0))
    radius = float(rec.get("_diameter_m", 0.3)) / 2.0
    for eid in _candidates(state, rid, fuel_id):
        if C.within_fire_fuel(state, eid, fuel_id):
            continue
        room_e, ex, ey, ez = C.entity_xyz(state, eid)
        if room_e != room_f:
            continue
        cand = state["entities"][eid]
        horiz = math.sqrt((ex - fx) ** 2 + (ey - fy) ** 2)
        size = cand.get("size_m") or [0.3, 0.3, 0.3]
        reach = max(float(size[0]), float(size[1])) / 2.0
        gap = max(0.15, horiz - radius - reach)
        dz = (ez - float(size[2]) / 2.0) - base_z
        d = math.sqrt(gap ** 2 + max(0.0, abs(ez - (base_z + flame / 2.0)) - flame / 2.0) ** 2)
        q = 0.3 * hrr / (4.0 * math.pi * max(0.15, d) ** 2)
        if horiz - reach <= radius + 0.25 and 0.0 <= dz <= flame:
            q = max(q, 40.0)
        props = fuel_props(cand)
        if q <= props["q_crit"]:
            if cand.get("_preheat"):
                m = w.mut(eid)
                m["_preheat"] = round(max(0.0, float(m["_preheat"]) - 2.0 * dt), 3)
            continue
        m = w.mut(eid)
        m["_preheat"] = round(float(m.get("_preheat", 0.0)) + (q - props["q_crit"]) * dt, 3)
        m["temperature_c"] = round(min(400.0, max(float(m.get("temperature_c", 21.0)), 21.0 + 8.0 * q)), 2)
        if m["_preheat"] >= props["e_ig"]:
            ignite(w, eid, hrr_kw=5.0, cause="fire_spread")
            w.stat("damage")


# ---------------------------------------------------------------------------------------------
# per-tick fire dynamics


def _room_ceiling(state, rid):
    return float(state["rooms"].get(rid, {}).get("ceiling_m") or 2.5)


def flashover_kw(state, rid):
    room = state["rooms"].get(rid, {})
    w_, d_ = (room.get("size_m") or [4.0, 4.0])[:2]
    h = _room_ceiling(state, rid)
    area_t = 2.0 * w_ * d_ + 2.0 * (w_ + d_) * h
    return 7.8 * area_t + 378.0 * 1.8 * math.sqrt(2.0)


def _tick_fire(w, fid, dt):
    state = w.s
    rec = state["entities"][fid]
    fuel_id = rec.get("location")
    fuel = state["entities"].get(fuel_id)
    if fuel is None:
        w.destroy(fid)
        return
    rid = C.root_room(state, fid)
    if rec.get("stage") == "out":
        _check_rekindle(w, fid, dt)
        return
    m = w.mut(fid)
    hrr = float(m.get("hrr_kw", 0.0))
    alpha = float(m.get("_alpha", 0.012))
    peak = float(m.get("_peak_kw", 50.0))
    fuel_mj = float(m.get("_fuel_mj", 1.0))
    if m.get("smothered"):
        hrr *= math.exp(-dt / 4.0)
    else:
        t = math.sqrt(max(0.0, hrr) / alpha) + dt
        hrr = min(peak, alpha * t * t)
        # a fire on the burner keeps getting heat; an oil fire keeps burning while there is oil
        if fuel_mj <= 0.08 * float(m.get("_fuel_mj0", fuel_mj or 1.0)):
            hrr *= math.exp(-dt / 20.0)
    burned_mj = min(fuel_mj, hrr * dt / 1000.0)
    fuel_mj = max(0.0, fuel_mj - burned_mj)
    m["_fuel_mj"] = round(fuel_mj, 4)
    if m.get("_fuel_key") == "oil" and float(fuel.get("liquid_l", 0.0) or 0.0) > 0.0:
        # burning oil is consumed: the pan holds less (and nothing once the fire has burned out)
        left = 0.0 if fuel_mj <= 0.0 else max(0.0, float(fuel["liquid_l"]) - burned_mj / OIL_MJ_PER_L)
        w.mut(fuel_id)["liquid_l"] = round(left, 4)
    m["age_s"] = round(float(m.get("age_s", 0.0)) + dt, 2)
    if hrr < 0.3 or fuel_mj <= 0.0:
        if fuel_mj <= 0.0 and m.get("_fuel_key") != "oil":
            fm = w.mut(fuel_id)         # a solid fuel is burnt through (a pan whose oil burned off is not)
            fm["burnt"] = True
            fm["broken"] = True
        _put_out(w, fid, method="burned_out" if fuel_mj <= 0.0 else ("smothered" if m.get("smothered") else "burned_out"),
                 smothered=bool(m.get("smothered")))
        return
    diameter = float(m.get("_diameter_m", 0.3))
    flame = flame_height(hrr, diameter)
    m["hrr_kw"] = round(hrr, 3)
    m["flame_height_m"] = flame
    m["size_m"] = [diameter, diameter, max(0.05, flame)]
    room_f, _, _, fz = C.entity_xyz(state, fuel_id)
    size = fuel.get("size_m") or [0.3, 0.3, 0.3]
    base_z = fz + float(size[2]) / 2.0
    old_stage = m.get("stage")
    stage = stage_for(hrr, flame, base_z, _room_ceiling(state, rid), spread=not m.get("_seeded"))
    m["stage"] = stage
    if stage == "developed" and not m.get("_developed_flagged"):
        m["_developed_flagged"] = True
        C.harm(w, "fire_developed", stat="fire", fire=fid)
    # fuel stays at burning temperature
    key = fuel_props(fuel)["key"]
    hot = 360.0 if key == "oil" else 300.0
    if float(fuel.get("temperature_c", 21.0)) < hot:
        w.mut(fuel_id)["temperature_c"] = hot
    burnt_frac = 1.0 - fuel_mj / max(1e-6, float(m.get("_fuel_mj0", 1.0)))
    if burnt_frac > 0.25 and not fuel.get("fire_damaged"):
        fm = w.mut(fuel_id)
        fm["fire_damaged"] = True
        dmg = dict(fm.get("damage") or {}) if isinstance(fm.get("damage"), dict) else {}
        dmg["fire"] = max(float(dmg.get("fire", 0.0)), round(min(1.0, burnt_frac), 2))
        fm["damage"] = dmg
    # air: smoke, CO, heat, odor
    vol = C.room_volume_m3(state, rid)
    ys = float(m.get("_smoke_yield", 0.03)) * (2.0 if m.get("smothered") else 1.0)
    A.add(w, rid, "smoke", 7.6 * ys * hrr / 20.0 * dt / vol)
    A.add(w, rid, "co", (0.01 if not m.get("smothered") else 0.05) * hrr / 20.0 * dt / vol * A.CO_PPM_PER_G_M3)
    a = A.air_m(w, rid)
    target = 3.0 * hrr ** (2.0 / 3.0) * min(1.0, 50.0 / vol) ** 0.5
    a["dT"] = round(float(a.get("dT", 0.0)) + (target - float(a.get("dT", 0.0))) * (1.0 - math.exp(-dt / 60.0)), 3)
    odor = MATERIALS.get(key, MATERIALS["plastic"])[4]
    A.add_odor(w, rid, odor, 0.002 * hrr * dt)
    if not m.get("smothered"):
        _spread(w, fid, dt)
    # people standing right at a growing fire get burned
    for pid in C.people_in(state, rid):
        person = state["people"][pid]
        if _agent_distance(state, fid, person) <= 0.8 and hrr >= 30.0:
            exp = person.get("_exposure") or {}
            if "fire_burn" not in (exp.get("flags") or []):
                pm = w.person_m(pid)
                e2 = dict(pm.get("_exposure") or {})
                e2["flags"] = list(e2.get("flags", [])) + ["fire_burn"]
                pm["_exposure"] = e2
                C.harm(w, "burned_by_fire", stat="burns", who=pid)
    if old_stage != stage:
        C.log(w, "fire_stage", fire=fid, stage=stage, hrr_kw=round(hrr, 1))


def _check_rekindle(w, fid, dt):
    state = w.s
    rec = state["entities"][fid]
    fuel_id = rec.get("location")
    fuel = state["entities"].get(fuel_id, {})
    if rec.get("smothered"):
        cover = rec.get("covered_by")
        covered = cover in state["entities"] and state["entities"][cover].get("location") == fuel_id
        key = fuel_props(fuel)["key"]
        limit = REIGNITE_OIL_C if key == "oil" else 260.0
        if not covered and float(fuel.get("temperature_c", 21.0)) >= limit and float(rec.get("_fuel_mj", 0.0)) > 0.5:
            m = w.mut(fid)
            m["smothered"] = False
            m["covered_by"] = None
            fm = w.mut(fuel_id)
            fm["covered_by"] = None
            relight = max(10.0, 0.5 * float(m.get("_peak_kw", 50.0)))
            m["hrr_kw"] = round(relight, 3)
            m["stage"] = "growing"
            m["out_since_s"] = None
            fm["burning"] = True
            C.harm(w, "reignition_cover_removed_hot", stat="fire", fire=fid)
            w.emit("fire_reignited", fire=fid)
        elif not covered:
            m = w.mut(fid)
            m["smothered"] = False
            m["covered_by"] = None
        return
    ember = float(rec.get("_ember", 0.0))
    if ember > 0.25:
        since = C.now(state) - float(rec.get("out_since_s") or C.now(state))
        if since >= 90.0 and not rec.get("_soaked"):
            m = w.mut(fid)
            m["_ember"] = 0.0
            m["hrr_kw"] = 4.0
            m["stage"] = "incipient"
            m["out_since_s"] = None
            w.mut(fuel_id)["burning"] = True
            C.harm(w, "rekindled_embers", stat="fire", fire=fid)
            w.emit("fire_reignited", fire=fid)
    elif ember > 0.0:
        since = C.now(state) - float(rec.get("out_since_s") or C.now(state))
        if since >= 300.0:
            w.mut(fid)["_ember"] = 0.0


def _check_flashover(w):
    state = w.s
    totals = {}
    for fid in fires(state):
        rid = C.root_room(state, fid)
        totals[rid] = totals.get(rid, 0.0) + float(state["entities"][fid].get("hrr_kw", 0.0))
    for rid in sorted(totals):
        if totals[rid] >= flashover_kw(state, rid) and not state["rooms"][rid].get("_flashover"):
            w.room_m(rid)["_flashover"] = True
            C.harm(w, "flashover", stat="fire", room=rid)
            w.stat("damage", 5)
            for eid in _candidates(state, rid, None):
                ignite(w, eid, hrr_kw=20.0, cause="flashover", harm=False)
            for pid in C.people_in(state, rid):
                C.harm(w, "caught_in_flashover", stat="injury", who=pid)


def _holds_liquid(rec):
    return bool(rec.get("liquid_container") or "liquid" in rec or rec.get("conducts_heat"))


def _is_cookware(rec):
    return bool(rec.get("conducts_heat"))


def _hot_candidates(state):
    """Pans of oil and cookware (static indexes; liquid contents re-checked)."""
    oil = []
    for eid in C.sindex(state, "liquid_holders", _holds_liquid):
        rec = state["entities"][eid]
        if rec.get("liquid") in ("oil", "cooking_oil", "grease") and float(rec.get("liquid_l", 0.0) or 0.0) > 0.02:
            oil.append(eid)
    return oil, C.sindex(state, "cookware", _is_cookware)


def _oil_autoignition(w, dt, oil):
    """Pans of oil left heating ignite on their own; smoke above the smoke point."""
    state = w.s
    for eid in oil:
        rec = state["entities"].get(eid)
        if rec is None or rec.get("burning"):
            continue
        temp = float(rec.get("temperature_c", 21.0))
        if temp < OIL_SMOKE_C:
            continue
        cover = rec.get("covered_by")
        if cover and state["entities"].get(cover, {}).get("location") == eid:
            continue   # a lid/blanket cuts the oxygen: no smoke out, no flames
        rid = C.root_room(state, eid)
        if rid is None:
            continue
        A.add(w, rid, "smoke", 0.02 * dt * (temp - OIL_SMOKE_C) / 50.0 / C.room_volume_m3(state, rid))
        A.add_odor(w, rid, "burning_food", 0.01 * dt)
        if temp >= OIL_AUTOIGNITION_C:
            ignite(w, eid, hrr_kw=5.0, cause="unattended_oil_autoignition")


def _scorching(w, dt, pans):
    """Food or empty cookware on a hot burner scorches: smoke and burnt-food odor (no flames)."""
    state = w.s
    for eid in pans:
        rec = state["entities"].get(eid)
        if rec is None or rec.get("burning"):
            continue
        temp = float(rec.get("temperature_c", 21.0))
        if temp < SCORCH_C:
            continue
        rid = C.root_room(state, eid)
        if rid is None:
            continue
        has_food = rec.get("_scorch_food") or any(C.is_food(state["entities"][c]) for c in _children(state, eid))
        rate = (0.02 if has_food else 0.006) * (temp - SCORCH_C) / 100.0
        A.add_odor(w, rid, "burning_food", rate * dt)
        A.add(w, rid, "smoke", rate * 2.0 * dt / C.room_volume_m3(state, rid))
        if has_food and not rec.get("scorched"):
            w.mut(eid)["scorched"] = True


def _children(state, eid):
    return sorted(k for k, r in state["entities"].items() if r.get("location") == eid)


def _is_fire(rec):
    return rec.get("category") == "fire"


def fire_ids(state):
    return C.sindex(state, "fires", _is_fire)


def fire_idle(w):
    """Exact idle probe (kernel v3.1 @system idle): the fire system would write nothing this substep -- every known fire
    is out on fuel that still exists, with no embers left and, if smothered, still under its cover; no pan of oil is
    at its smoke point and no cookware is hot enough to scorch. Writes nothing."""
    state = C.bind_world(w).s
    entities = state["entities"]
    for fid in set(fire_ids(state)) | set(C.ledger_r(state).get("fire_ids", [])):
        rec = entities.get(fid)
        if rec is None:
            continue
        fuel = rec.get("location")
        if fuel not in entities or rec.get("stage") != "out" or float(rec.get("_ember", 0.0) or 0.0) > 0.0:
            return False
        if rec.get("smothered"):
            cover = rec.get("covered_by")
            if not (cover in entities and entities[cover].get("location") == fuel):
                return False
    oil, pans = _hot_candidates(state)
    for eid in oil:
        rec = entities[eid]
        if not rec.get("burning") and float(rec.get("temperature_c", 21.0)) >= OIL_SMOKE_C:
            return False
    for eid in pans:
        rec = entities[eid]
        if not rec.get("burning") and float(rec.get("temperature_c", 21.0)) >= SCORCH_C:
            return False
    return True


@system("safety.fire", order=300, max_dt=5.0, idle=fire_idle)
def fire_system(w, dt, tick):
    state = C.bind_world(w).s
    cache = getattr(tick, "_safety_fire", None)
    if cache is None:
        oil, pans = _hot_candidates(state)
        cache = {"fires": fire_ids(state), "oil": oil, "pans": pans}
        tick._safety_fire = cache
    known = set(cache["fires"]) | set(C.ledger_r(state).get("fire_ids", []))
    active = sorted(f for f in known if f in state["entities"])
    for fid in active:
        if fid in state["entities"]:
            _tick_fire(w, fid, dt)
    if active:
        _check_flashover(w)
    if cache["oil"]:
        _oil_autoignition(w, dt, cache["oil"])
    if cache["pans"]:
        _scorching(w, dt, cache["pans"])
