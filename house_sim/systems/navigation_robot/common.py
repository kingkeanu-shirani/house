"""Shared constants and helpers for the navigation_robot domain.

Magnitudes (cited where not obvious):
* Required coefficient of friction (RCOF) for level walking is about 0.17-0.22 at normal pace and
  rises with speed (Redfern et al. 2001, Applied Ergonomics); a slow, short-stride gait needs about 0.15.
* Available friction: dry hard floors 0.5-0.7; wet glazed tile about 0.2 (ANSI A326.3 wet DCOF
  threshold is 0.42 for level interior floors expected to be walked on wet); wet polished stone 0.15;
  oil/grease films about 0.12; wet ice 0.05-0.1.
* The robot is a 60 kg, 1.6 m humanoid (comparable to current general-purpose humanoids) walking at
  0.4 (careful), 0.8 (normal) or 1.2 (fast) m/s on 0.5 m grid cells.
* Battery: about 2.4 kWh, roughly 2.5 h of continuous walking; 0.012 % per meter on level floor.
"""
import copy
import math

from ...kernel import geometry
from ...kernel.errors import ActionError, require  # noqa: F401  (re-exported)
from ...kernel.plugins import ACTIONS
from ...kernel.predicates import root_room

CELL = geometry.CELL
SPEED_MPS = {"careful": 0.4, "normal": 0.8, "fast": 1.2}
RCOF = {"careful": 0.15, "normal": 0.21, "fast": 0.28}
RCOF_LOAD_PER_KG = 0.004          # carried mass shifts the center of mass and raises required friction
STUMBLE_MARGIN = 0.04             # friction shortfall a recovering step can absorb (stumble, no fall)
ROBOT_MASS_KG = 60.0
ROBOT_BODY_WIDTH_M = 0.5          # shoulder width, arms in
ROBOT_SIDEWAYS_WIDTH_M = 0.36     # turned sideways with arms tucked (careful motion profile)
BATTERY_PCT_PER_M = 0.012
BATTERY_PCT_PER_STEP_UP = 0.05
BATTERY_PCT_PER_STEP_DOWN = 0.02
BATTERY_RESERVE_PCT = 15.0

DRY_MU = {"tile": 0.6, "stone": 0.55, "marble": 0.5, "hardwood": 0.55, "laminate": 0.5, "vinyl": 0.6, "carpet": 0.8,
          "rubber": 0.9, "concrete": 0.7, "plywood": 0.6, "wood": 0.6, "grass": 0.6, "soil": 0.55, "gravel": 0.45,
          "lawn": 0.6, "mulch": 0.5, "pavers": 0.65}
WET_MU = {"tile": 0.24, "stone": 0.15, "marble": 0.13, "hardwood": 0.28, "laminate": 0.25, "vinyl": 0.3, "carpet": 0.55,
          "rubber": 0.6, "concrete": 0.45, "plywood": 0.35, "wood": 0.3, "grass": 0.4, "soil": 0.3, "gravel": 0.4,
          "lawn": 0.4, "mulch": 0.35, "pavers": 0.4}
HAZARD_MU = {"slip": 0.12, "oil": 0.12, "ice": 0.07, "black_ice": 0.07, "snow": 0.3, "moss": 0.18, "mud": 0.25,
             "gravel": 0.38, "rug_slip": 0.2, "leaves_wet": 0.25, "soap": 0.1}
TRIP_KINDS = ("trip", "toy", "cord", "rug_curl", "threshold", "slab_edge", "curb", "litter", "shoe", "tools")
HARD_FLOORS = ("tile", "stone", "marble", "hardwood", "laminate", "vinyl", "concrete", "plywood", "wood")
PRIVATE_ACTIVITIES = ("bathing", "showering", "toilet", "dressing", "changing", "intimate")
CALL_ACTIVITIES = ("call", "video_call", "phone_call", "meeting")
SOUND_AT_1M_DB = {"careful": 30.0, "normal": 36.0, "fast": 44.0}   # robot footsteps (same scale as the people domain)
SLEEP_WAKE_DB = {"adult": 55.0, "elderly": 52.0, "teen": 58.0, "child": 55.0, "toddler": 50.0, "infant": 48.0}


def footstep_db_at(speed, cells_away, carrying_kg=0.0):
    """Footstep level at a sleeper: inverse-square from 1 m, +3 dB with a heavy load (heavier footfalls)."""
    import math as _m
    d = max(0.5, cells_away * CELL)
    return SOUND_AT_1M_DB.get(speed, 36.0) - 20.0 * _m.log10(d) + (3.0 if carrying_kg > 10.0 else 0.0)


def wake_threshold_db(person):
    base = SLEEP_WAKE_DB.get(person.get("role", "adult"), 55.0)
    if person.get("sleep_threshold_db") is not None:
        base = num(person["sleep_threshold_db"], base)
    if person.get("light_sleeper"):
        base -= 12.0
    return base
SPEECH_DB = 60.0


# ----------------------------------------------------------------------
# small utilities

def num(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def now(state):
    return float(state.get("time_s", 0.0))


def cell(value):
    return (int(value[0]), int(value[1]))


def ckey(c):
    return "{},{}".format(int(c[0]), int(c[1]))


def is_type(rec, name):
    return rec.get("type") == name or name in (rec.get("is_a") or [])


def room_of(state, eid):
    return root_room(state, eid)


def speed_of(ctx):
    return ctx.args.get("speed") or ctx.w.robot.get("speed_mode", "normal")


def people_domain_loaded():
    """The people domain owns privacy intrusions and startle; avoid double-counting them."""
    return "knock" in ACTIONS


def mechanisms_loaded():
    """The mechanisms domain owns door/cabinet leaves meeting things in their swing (and door closers)."""
    return "latch" in ACTIONS


# ----------------------------------------------------------------------
# realistic perception (kernel v2.4): what this domain's messages, incidents, observers and inspect details may say

def realistic(ctx):
    """True when the action runs under realistic perception (ctx may be None in systems/helpers)."""
    return bool(ctx is not None and getattr(ctx, "realistic", False))


def thing(ctx, eid):
    """How a message or incident cause names an entity: its label in oracle mode (unchanged), its id in realistic mode
    (the kernel turns ids into handles, or "something" for a thing the robot has not seen), so a label never resolves a
    look-alike or names an unseen object."""
    if realistic(ctx):
        return eid
    rec = ctx.w.s["entities"].get(eid, {}) if ctx is not None else {}
    return rec.get("label", eid)


def identified(env, state, eid):
    """Realistic mode: the robot has resolved what eid is (not an unresolved look-alike or novel object)."""
    realism = getattr(env, "realism", None)
    if realism is None:
        return True
    rec = state["entities"].get(eid)
    if rec is None:
        return False
    ident = realism.identity(eid, rec, realism.knowledge.entity_level(eid, rec))
    return "candidates" not in ident and not rec.get("novel")


def knows_field(env, state, eid, field, level="inspect"):
    """Realistic mode: the robot learned this field of eid at `level` or better (a door's lock after inspecting or
    trying it). Always True in oracle mode."""
    realism = getattr(env, "realism", None)
    if realism is None:
        return True
    rec = state["entities"].get(eid)
    if rec is None:
        return False
    from ...kernel.realism import LEVELS
    return realism.knowledge.level(eid, rec, field) >= LEVELS.get(level, 1)


def perceived_locks(env, state):
    """A state view for route planning under realistic perception: doors whose lock the robot has not inspected or
    tried count as unlocked (it plans as if it can pass and finds out at the door). The state itself in oracle mode."""
    if getattr(env, "realism", None) is None:
        return state
    ents = state["entities"]
    changed = {}
    for conn in state.get("connections", {}).values():
        door = conn.get("door")
        rec = ents.get(door) if door else None
        if rec is not None and rec.get("locked") and not knows_field(env, state, door, "locked"):
            changed[door] = dict(rec, locked=False)
    if not changed:
        return state
    view = dict(state)
    view["entities"] = dict(ents)
    view["entities"].update(changed)
    return view


def known_value(ctx, eid, field, value):
    """value in oracle mode; in realistic mode the value only when the robot has identified eid and perceives that
    field at its current knowledge (else None)."""
    if not realistic(ctx):
        return value
    if not identified(ctx.env, ctx.w.s, eid):
        return None
    return ctx.percept_value(eid, field, value, "glance")


def perceives(env, state, eid):
    """Realistic mode: eid is in the robot's room, the room is lit (or the robot's own light is on) and the small-object
    model does not miss it from the robot's current viewpoint. Always True in oracle mode."""
    realism = getattr(env, "realism", None)
    if realism is None:
        return True
    rec = state["entities"].get(eid)
    if rec is None:
        return False
    robot = state["robot"]
    if room_of(state, eid) != robot.get("room"):
        return False
    if light_level(state, robot["room"]) < 0.2 and not robot_has_light(state):
        return False
    try:
        ctx = realism.context(state, eid, rec)
        return not realism.missed(state, eid, rec, ctx)
    except Exception:  # noqa: BLE001  (perception helpers must never break an action)
        return True


def within_touch(state, eid):
    """The robot can touch eid from where it stands (needed to feel a loose rail, a wobbling leg, a rocking machine)."""
    try:
        problem = geometry.reach_problem(state, eid)
    except Exception:  # noqa: BLE001
        return False
    return problem is None or "Crouch" in problem      # crouching to feel a low leg is fine


INCIDENT_IDENTITY_KEYS = ("liquid",)     # what the robot cannot tell by looking (water vs vinegar in a tank)
# exact hidden quantities some incident records carry for the log (a doorway's clearance to the mm, a dropped item's
# mass, how far an appliance walked, a mud depth, a call's sound level, an overreach margin): never shown to the robot
INCIDENT_HIDDEN_KEYS = ("clearance_m", "mass_kg", "walked_mm", "depth_cm", "level_db", "margin_m", "from_angle")
# incidents the robot cannot notice when they happen (seals attacked by the wrong liquid fail later, out of sight)
UNNOTICED_INCIDENTS = ("rv_tank_wrong_liquid",)


def perceived_incident(entry):
    """An incident as the robot perceives it (realistic observers): keys that would name what it cannot identify, or
    give an exact hidden quantity, are dropped (a spilled volume becomes just "spilled"); incidents it cannot notice
    show as nothing; entity ids stay ids (the kernel turns them into handles or "something")."""
    if not isinstance(entry, dict):
        return copy.deepcopy(entry)
    if entry.get("kind") in UNNOTICED_INCIDENTS:
        return None
    out = {k: copy.deepcopy(v) for k, v in entry.items() if k not in INCIDENT_IDENTITY_KEYS and k not in INCIDENT_HIDDEN_KEYS}
    if "spilled_l" in out:
        out["spilled"] = num(out.pop("spilled_l")) > 0.0
    return out


def floor_material(state, rid):
    room = state["rooms"].get(rid, {})
    material = room.get("floor_material") or room.get("terrain")
    if not material:
        floor = state["entities"].get("floor_" + rid, {})
        material = floor.get("material")
    return str(material or "hardwood")


def floor_id(state, rid):
    fid = "floor_" + rid
    return fid if fid in state["entities"] else None


def light_level(state, rid):
    from ...kernel.perception import light_level as _level
    return _level(state, rid)


def robot_has_light(state):
    from ...kernel.perception import flashlight_on
    return flashlight_on(state)


def held_mass(w):
    from ..core import subtree_mass
    return sum(subtree_mass(w, eid) for eid in w.held_items())


def held_width(w):
    from ..core import held_width as _hw
    return _hw(w)


def robot_mass(w):
    return num(w.robot.get("mass_kg"), ROBOT_MASS_KG) + held_mass(w)


def free_hand(w):
    hands = w.robot.get("hands", {})
    arms = w.robot.get("arms_ok", {"left": True, "right": True})
    return any(hands.get(h) is None and arms.get(h, True) for h in ("left", "right"))


def view_blocking_items(w):
    """Held loads carried in front that hide the floor/treads: two-handed boxes, baskets, bins."""
    result = []
    for eid in w.held_items():
        rec = w.ent(eid)
        size = rec.get("size_m") or [0.1, 0.1, 0.1]
        depth, height = max(float(size[0]), float(size[1])), float(size[2])
        if rec.get("held_hand") == "both" and depth >= 0.35 and height >= 0.25 and not rec.get("carry_level"):
            result.append(eid)
        elif rec.get("blocks_view"):
            result.append(eid)
    return result


def open_liquids(w):
    """Held open containers with liquid (they slosh/spill) including drinks on a held tray."""
    result = []
    for eid in w.held_items():
        for item in [eid] + w.descendants(eid):
            rec = w.ent(item)
            if num(rec.get("liquid_l")) > 0 and not rec.get("sealed") and not rec.get("lidded") and not rec.get("covered") \
                    and not (rec.get("cap") or {}).get("on"):
                result.append(item)
    return result


# ----------------------------------------------------------------------
# the domain's state section

def nav(w):
    """Mutable (journaled) domain section."""
    sec = w.sec("nav")
    sec.setdefault("events", [])
    sec.setdefault("counts", {})
    return sec


def nav_read(state):
    return state.get("nav") or {}


def log(w, kind, **data):
    sec = nav(w)
    entry = {"time_s": now(w.s), "step": w.s.get("steps", 0), "kind": kind}
    entry.update(data)
    sec["events"].append(entry)
    del sec["events"][:-200]
    sec["counts"][kind] = sec["counts"].get(kind, 0) + 1
    w.emit("nav." + kind, **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list)) or v is None})
    return entry


def count(state, kind):
    return int(nav_read(state).get("counts", {}).get(kind, 0))


def bump(w, path, amount=1):
    """Increment nav.<a>.<b> counters."""
    sec = nav(w)
    parts = path.split(".")
    node = sec
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = round(node.get(parts[-1], 0) + amount, 4)
    return node[parts[-1]]


def incident(w, kind, **data):
    """A physical incident: logged and shown to the robot as last_incident."""
    entry = log(w, kind, **data)
    nav(w)["last_incident"] = copy.deepcopy(entry)
    return entry


# ----------------------------------------------------------------------
# consequences shared by several mechanics

SPILL_MIN_L = 0.005                  # a few drops (< 5 ml) make no puddle and are not a spill
FILM_FOOTPRINT_MM = 0.03             # water film left by a wet sole (below the damage limit of wood floors)
SLIPPERY_LIQUIDS = ("oil", "cooking_oil", "motor_oil", "vomit", "raw_egg", "soap", "lotion")


def system_present(name):
    from ...kernel.plugins import SYSTEMS
    return any(s[1] == name for s in SYSTEMS)


def cleaning_owns_floor_water():
    """With the cleaning domain loaded, spills (liquid_l evaporation, residue) and room `water_film` (which also
    derives `wet_cells` and dries them) are cleaning's; this domain only adds water to them (CONVENTIONS 5)."""
    return system_present("cleaning.spills") and system_present("cleaning.drying")


def wet_floor_cells(w, rid, cells, mm=FILM_FOOTPRINT_MM):
    """Add water to floor cells: room `wet_cells` (slip hazard) and, when cleaning dries floors, a `water_film`
    entry of at least `mm` so the cells dry over time instead of staying wet forever."""
    if rid not in w.s["rooms"]:
        return []
    room = w.s["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    cells = [[int(c[0]), int(c[1])] for c in cells if 0 <= int(c[0]) < cols and 0 <= int(c[1]) < rows]
    if not cells:
        return []
    rm = w.room_m(rid)
    wet = rm.setdefault("wet_cells", [])
    added = []
    for c in cells:
        if c not in wet:
            wet.append(c)
            added.append(c)
    if cleaning_owns_floor_water():
        film = rm.setdefault("water_film", {})
        for c in cells:
            key = ckey(c)
            if num(film.get(key)) < mm:
                film[key] = round(float(mm), 5)
    return added


CLEAR_LIQUIDS = ("water", "tap_water", "distilled_water", "sparkling_water", "soda_water", "vinegar", "white_vinegar",
                 "rubbing_alcohol", "isopropyl_alcohol", "alcohol", "vodka", "gin", "bleach", "ammonia", "hydrogen_peroxide",
                 "saline", "disinfectant", "sanitizer", "glass_cleaner", "white_wine", "lemonade", "lemon_lime_soda")


def spill_label(liquid):
    """What a spill looks like (its glance label): clear liquids all look alike on the floor ("clear liquid spill":
    water, vinegar and bleach are told apart only up close or by the bottle's label); coloured or oily ones are named."""
    name = str(liquid or "liquid")
    if name in CLEAR_LIQUIDS:
        return "clear liquid spill"
    return "{} spill".format(name.replace("_", " "))


def spawn_spill(w, rid, at, liters, liquid="water", source=None):
    """Spilled liquid becomes a spill entity on the floor (cleaning's `spill` type with `liquid_l` when registered, so
    cleaning evaporates/absorbs it and leaves the drying film) plus, without cleaning, room wet_cells. A few drops
    (< 5 ml) are not a spill (no global harm). Its label says what it looks like (spill_label)."""
    liters = round(max(0.0, float(liters)), 4)
    if liters < SPILL_MIN_L or rid not in w.s["rooms"]:
        return None
    if cleaning_owns_floor_water() and w.reg is not None and w.reg.known("spill"):
        thickness_mm = 2.5 if liquid in SLIPPERY_LIQUIDS else 1.5
        area = max(0.01, liters / thickness_mm)
        side = round(min(3.0, max(0.05, math.sqrt(area))), 3)
        props = {"label": spill_label(liquid), "category": "spill", "pos": list(cell(at)), "relation": "on",
                 "liquid": liquid, "liquid_l": liters, "area_m2": round(area, 4), "size_m": [side, side, 0.002],
                 "floor_hazard": "slip" if liquid in SLIPPERY_LIQUIDS else "wet", "portable": False, "blocks_path": False,
                 "mass_kg": 0.0, "volume_l": 0.0, "source": source}
        if liquid not in ("water",):
            props["residue"] = {"sticky": 0.4}
        if floor_material(w.s, rid) == "carpet":
            props["on_carpet"] = True
        eid = w.spawn("spill", rid, **props)
        w.stat("spills")
        return eid
    room = w.room_m(rid)
    wet = room.setdefault("wet_cells", [])
    cells = [list(cell(at))]
    if liters > 0.3:
        cols, rows = geometry.grid_size(room)
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            c = [at[0] + dx, at[1] + dy]
            if 0 <= c[0] < cols and 0 <= c[1] < rows:
                cells.append(c)
            if len(cells) >= 1 + int(liters / 0.3):
                break
    for c in cells:
        if c not in wet:
            wet.append(c)
    type_name = "spill" if w.reg is not None and w.reg.known("spill") else "generic_item"
    eid = w.spawn(type_name, rid, label=spill_label(liquid), category="spill", pos=list(cell(at)),
                  liquid=liquid, liquid_l=liters, volume_l=0.0, spill_l=liters, portable=False, relation="on",
                  floor_hazard="slip" if liquid in SLIPPERY_LIQUIDS else "wet",
                  soil={"sticky": 0.4} if liquid not in ("water",) else {}, size_m=[0.5, 0.5, 0.005], ground_layer=True,
                  source=source)
    w.stat("spills")
    return eid


def drop_item(w, eid, height_m, rid, at, cause):
    """A held item falls to the floor: fragile items break, open liquids spill."""
    from ..core import release_from_hands
    rec = w.ent(eid)
    release_from_hands(w, eid)
    m = w.mut(eid)
    m["location"], m["relation"], m["pos"] = rid, "on", list(cell(at))
    for key in ("spos", "shelf", "held_hand"):
        m.pop(key, None)
    w.log_move(eid, "robot", rid)
    broke = []
    tolerance = num(rec.get("drop_tolerance_m"), 0.3 if rec.get("fragile") else 1.5)
    for item in [eid] + w.descendants(eid):
        irec = w.ent(item)
        if irec.get("fragile") and not irec.get("broken") and height_m > num(irec.get("drop_tolerance_m"), 0.3):
            w.mut(item)["broken"] = True
            broke.append(item)
        if num(irec.get("liquid_l")) > 0 and not irec.get("sealed") and not irec.get("lidded") and not (irec.get("cap") or {}).get("on"):
            liters = num(irec.get("liquid_l"))
            w.mut(item)["liquid_l"] = 0.0
            spawn_spill(w, rid, at, liters, irec.get("liquid", "water"), source=item)
    if height_m > tolerance and not rec.get("fragile") and num(rec.get("mass_kg")) >= 5.0:
        w.stat("damage")
        log(w, "heavy_drop", item=eid)
    if broke:
        w.stat("breakages", len(broke))
        w.stat("damage")
    log(w, "dropped", item=eid, room=rid, cell=list(cell(at)), cause=cause, broke=broke)
    return broke


def robot_falls(w, cause, rid=None, at=None, damage=False, drop_height=1.0):
    """The robot loses balance: it lies on the floor, held items drop, and the fall is a global harm."""
    robot = w.robot_m()
    rid = rid or robot["room"]
    at = list(cell(at if at is not None else (robot.get("pos") or [0, 0])))
    robot["room"], robot["pos"] = rid, at
    robot["fallen"] = True
    robot["posture"] = "lying"
    robot["docked"] = False
    robot["charging"] = False
    robot["standing_on"] = None
    if damage:
        robot["actuator_ok"] = False
    faults = list(robot.get("faults") or [])
    faults.append({"kind": "fall", "cause": cause, "time_s": now(w.s), "room": rid})
    robot["faults"] = faults[-50:]
    for eid in list(w.held_items()):
        drop_item(w, eid, drop_height, rid, at, cause="robot_fell")
    w.stat("falls")
    w.stat("nav_robot_falls")
    incident(w, "robot_fell", cause=cause, room=rid, cell=at, damaged=bool(damage))


def stumble(w, cause, rid, at):
    """A recovered stumble: no fall, but open liquids slop over and the jolt is logged."""
    spilled = 0.0
    for item in open_liquids(w):
        rec = w.ent(item)
        lost = round(num(rec.get("liquid_l")) * 0.25, 4)
        if lost > 0:
            w.mut(item)["liquid_l"] = round(num(rec.get("liquid_l")) - lost, 4)
            spilled += lost
            spawn_spill(w, rid, at, lost, rec.get("liquid", "water"), source=item)
    w.stat("nav_stumbles")
    incident(w, "stumble", cause=cause, room=rid, cell=list(cell(at)), spilled_l=round(spilled, 4))


def collide(w, what, target=None, rid=None, at=None, damage=False):
    w.stat("collisions")
    w.stat("nav_collisions")
    if damage:
        w.stat("damage")
    incident(w, "collision", what=what, target=target, room=rid, cell=list(cell(at)) if at is not None else None, damaged=bool(damage))


def damage_entity(w, eid, kind, severity):
    """Record physical damage on an entity: `damage` is a dict {kind: severity 0..1} (CONVENTIONS 5; legacy lists are
    converted). Severity only grows."""
    if eid not in w.s["entities"]:
        return
    rec = w.ent(eid)
    old = rec.get("damage")
    damage = dict(old) if isinstance(old, dict) else {str(k): 1.0 for k in (old or [])} if isinstance(old, list) else {}
    level = round(min(1.0, max(num(damage.get(kind)), float(severity))), 3)
    if damage.get(kind) != level or not isinstance(old, dict):
        damage[kind] = level
        w.mut(eid)["damage"] = damage


def break_entity(w, eid, why):
    rec = w.ent(eid)
    if rec.get("broken"):
        return False
    w.mut(eid)["broken"] = True
    damage_entity(w, eid, "broken", 1.0)
    if rec.get("fragile") or rec.get("crushable"):
        w.stat("breakages")
    w.stat("damage")
    log(w, "broke", target=eid, why=why)
    return True


def hurt_person(w, pid, why, fall=False):
    m = w.person_m(pid)
    m["mood"] = "hurt" if fall else "upset"
    if fall:
        m["posture"] = "fallen"
        w.stat("injury")
    log(w, "person_hurt" if fall else "person_bumped", person=pid, why=why)


def hurt_pet(w, pid, why):
    m = w.pet_m(pid)
    m["mood"] = "hurt"
    m["injured"] = True
    m["posture"] = "standing"
    w.stat("injury")
    log(w, "pet_hurt", pet=pid, why=why)


def startle_pet(w, pid, why):
    pet = w.s["pets"][pid]
    m = w.pet_m(pid)
    m["mood"] = "startled"
    m["posture"] = "standing"
    if "awake" in pet:
        m["awake"] = True
    m["anxiety"] = round(min(1.0, num(pet.get("anxiety"), 0.2) + 0.4), 3)
    w.stat("nav_pet_startles")
    log(w, "pet_startled", pet=pid, why=why)


def asleep(agent):
    return bool(agent) and (agent.get("awake") is False or agent.get("posture") == "sleeping")


def wake_agent(w, group, aid, why):
    agent = w.s[group][aid]
    if not asleep(agent):
        return False
    m = w.person_m(aid) if group == "people" else w.pet_m(aid)
    m["awake"] = True
    m["posture"] = "lying" if agent.get("posture") == "sleeping" else agent.get("posture")
    m["mood"] = "groggy" if group == "people" else "startled"
    w.stat("nav_woke_sleepers")
    log(w, "woke", agent=aid, group=group, why=why)
    return True


def agents_in_room(state, rid):
    """[(group, id, record)] for people and pets present in a room, sorted."""
    out = []
    for group in ("people", "pets"):
        for aid in sorted(state.get(group, {})):
            agent = state[group][aid]
            if agent.get("location") == rid and agent.get("pos") and not agent.get("away"):
                out.append((group, aid, agent))
    return out


def connection_for_move(state, a, b, cid=None):
    if cid and cid in state.get("connections", {}):
        return cid
    from ..core import connection_between
    return connection_between(_W(state), a, b)


class _W:
    """Minimal read-only world facade for helpers that only read state."""

    def __init__(self, state):
        self.s = state


def distance_m(a, b):
    return CELL * math.hypot(a[0] - b[0], a[1] - b[1])


def line_cells(a, b):
    """Grid cells on the segment between two cells (Bresenham), endpoints excluded."""
    x0, y0 = int(a[0]), int(a[1])
    x1, y1 = int(b[0]), int(b[1])
    cells = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while (x0, y0) != (x1, y1):
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
        if (x0, y0) != (x1, y1):
            cells.append((x0, y0))
    return cells


def doorway_cells(state, rid, ring=1):
    """Connection cells of a room plus their neighbors (traffic around doors)."""
    room = state["rooms"][rid]
    cells = set()
    for cid in sorted(state.get("connections", {})):
        conn = state["connections"][cid]
        if rid in conn.get("cells", {}):
            c = cell(conn["cells"][rid])
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    p = (c[0] + dx, c[1] + dy)
                    if geometry.in_grid(room, p):
                        cells.add(p)
    return cells


def household(state):
    return state.get("household") or {}


def units_of(state):
    meta = state.get("metadata") or {}
    pref = household(state).get("units") or meta.get("units") or "metric"
    return "imperial" if str(pref).startswith("imp") else "metric"


def ids_with(w, key, pred):
    """Sorted ids of entities satisfying `pred`, a predicate over STATIC fields only (type/category flags set when the
    entity is created: connects, vehicle, rv, leveling, part_type, low_contrast...). Kernel v2.1 `w.static_index`
    caches it until an entity is created or destroyed, so systems never scan every entity per tick; callers re-check
    dynamic fields on the records they get back."""
    ents = w.s["entities"]
    return [eid for eid in w.static_index("navigation_robot." + key, pred) if eid in ents]


def first_substep(tick):
    return getattr(tick, "index", 0) == 0


def every(tick, n):
    return getattr(tick, "index", 0) % n == 0
