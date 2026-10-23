"""Shared constants and helpers for the navigation_robot domain.

Magnitudes (cited where not obvious):
* Required coefficient of friction (RCOF) for level walking is about 0.17-0.22 at normal pace and
  rises with speed (Redfern et al. 2001, Applied Ergonomics); a slow, short-stride gait needs about 0.15.
* Available friction: dry hard floors 0.5-0.7; wet glazed tile about 0.2 (ANSI A326.3 wet DCOF
  threshold is 0.42 for level interior floors expected to be walked on wet); wet polished stone 0.15;
  oil/grease films about 0.12; wet ice 0.05-0.1.
* The robot is the kernel body (kernel/body.py, data/robot/profiles.json; humanoid_v1 by default: 60 kg,
  1.6 m, 0.5 m shoulders, 0.4 / 0.8 / 1.2 m/s careful / normal / fast on 0.5 m grid cells). Its mass,
  width, speeds, carried load, footstep and speech levels are read from the body, never from constants
  here, so a compact humanoid or a mobile manipulator walks, squeezes and is heard as itself.
* Energy is the body's too (TIER1_EMBODIMENT 4): every metre walked and every second is paid from the
  2.4 kWh pack through the motion record (ctx.motion); this domain never writes the battery.
* Consequences go through the kernel (TIER1_PHYSICS 10.3): breaks (impact.break_entity), things falling
  (impact.fall) or tipping over (impact.tip_over), spills (liquids.spill), and the robot's own fall and the
  drops it causes (body_faults.fall, body_faults.drop_held). The carry law (sloshing and toppling what the
  robot carries) is the kernel's once it is active; until then traverse's legacy slosh stays, guarded by
  lawbook.active("carry").
"""
import copy
import math

from ...kernel import body, geometry
from ...kernel.errors import ActionError, require  # noqa: F401  (re-exported)
from ...kernel.plugins import ACTIONS
from ...kernel.predicates import root_room

CELL = geometry.CELL
SPEED_MODES = body.SPEED_MODES
RCOF = {"careful": 0.15, "normal": 0.21, "fast": 0.28}
RCOF_LOAD_PER_KG = 0.004          # carried mass shifts the center of mass and raises required friction
STUMBLE_MARGIN = 0.04             # friction shortfall a recovering step can absorb (stumble, no fall)

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
SLEEP_WAKE_DB = {"adult": 55.0, "elderly": 52.0, "teen": 58.0, "child": 55.0, "toddler": 50.0, "infant": 48.0}
# footfall level (dB at 1 m, body.noise_db) that wakes a sleeping cat or dog the robot passes within one cell: the pets
# domain's resting-startle level (pets/behavior.py STARTLE_RESTING_DB), so both models agree. humanoid_v1: a normal walk
# (36 dB) wakes it, a careful one (30 dB) or a normal walk in quiet mode (32 dB) does not.
PET_WAKE_DB = 33.0


def footstep_db_at(state, speed, cells_away, carrying_kg=0.0):
    """Footstep level at a sleeper: the body's footstep level at 1 m for the pace (body.noise_db: the profile's
    30 / 36 / 44 dB careful / normal / fast for humanoid_v1, 4 dB less in quiet mode), inverse-square from 1 m (at
    least half a metre away), +3 dB with a heavy load (heavier footfalls)."""
    d = max(0.5, cells_away * CELL)
    return body.noise_db(state, "footsteps", speed) - 20.0 * math.log10(d) + (3.0 if carrying_kg > 10.0 else 0.0)


def wake_threshold_db(person):
    base = SLEEP_WAKE_DB.get(person.get("role", "adult"), 55.0)
    if person.get("sleep_threshold_db") is not None:
        base = num(person["sleep_threshold_db"], base)
    if person.get("light_sleeper"):
        base -= 12.0
    return base


def robot_width_m(state):
    """Shoulder width with the arms in (the body: humanoid_v1 0.5 m)."""
    return float(body.get(state, "width_m"))


def sideways_width_m(state):
    """Width turned sideways with the arms tucked (the careful motion profile; humanoid_v1 0.36 m)."""
    return float(body.profile(state)["sideways_width_m"])


def speed_mps(state, speed):
    """Walking speed (m/s) of a pace for this body (its profile's speeds)."""
    return body.speed_mps(state, speed)


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


def pets_domain_loaded():
    """The pets domain owns the startle of pets along a core approach / walk_to (its body-aware _startle_along)."""
    return "calm_pet" in ACTIONS


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


def geometric(state):
    """The run uses the posed geometric camera (kernel v3.0: robot.view exists; camera.mode)."""
    return isinstance((state.get("robot") or {}).get("view"), dict)


def camera_sees(env, state, eid):
    """Geometric camera: the robot's cameras framed eid this step from where the robot stands (camera.in_frame: line
    of sight, light and resolution of a stationary frame at the current eye; held things always), so a passive visual
    cue needs the robot to look. Room camera: perception's room-wide rule."""
    from ...kernel import camera
    try:
        return camera.in_frame(state, eid, getattr(env, "world", None))
    except Exception:  # noqa: BLE001  (perception helpers must never break an action)
        return False


def perceives(env, state, eid):
    """What the robot can tell is there from where it stands. Geometric camera (both perception modes): eid is in the
    robot's room and its cameras framed it this step (camera_sees). Room camera, realistic mode: eid is in the robot's
    room, the room is lit (or the robot's own light is on) and the small-object model does not miss it from the robot's
    current viewpoint. Room camera, oracle mode: always True."""
    rec = state["entities"].get(eid)
    if geometric(state):
        return rec is not None and room_of(state, eid) == state["robot"].get("room") and camera_sees(env, state, eid)
    realism = getattr(env, "realism", None)
    if realism is None:
        return True
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
    """The load the robot's hands bear (body.carried_kg: every held item with its contents and liquid, at its load
    share: the robot's end of a co-carried load, a towed cart's handle)."""
    return body.carried_kg(w)


def held_width(w):
    from ..core import held_width as _hw
    return _hw(w)


def robot_mass(w):
    """The robot and everything it carries (body.total_mass_kg): the load on a floor, a stair, a stool or a ladder."""
    return body.total_mass_kg(w)


def free_hand(w):
    """A hand that holds nothing and whose arm works (Ctx.free_hand's rule: body.arm_health > 0)."""
    hands = w.robot.get("hands", {})
    return any(hands.get(h) is None and body.arm_health(w.s, h) > 0 for h in body.HANDS)


def view_blocking_items(w):
    """Held loads carried in front that hide the floor and the treads. Geometric camera: the camera's carried-load
    occluder (sight.carried_occluder over phys.support.held_envelope: two-hand loads at least 0.35 x 0.25 x 0.25 m
    or declaring blocks_view, never a tray carried level). Room camera: two-handed boxes, baskets and bins at least
    0.35 m across and 0.25 m tall, or anything declaring blocks_view, as before."""
    if geometric(w.s):
        from ...kernel import sight
        occ = sight.carried_occluder(w.s)
        return sorted(e for e in w.held_items() if occ is not None and e in occ["items"])
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


def log(w, kind, *, witness=None, **data):
    """Log a domain event (nav.events, nav.counts) and emit it as "nav.<kind>". witness: a visual occurrence (a car
    reversing, a pet slipping out, a garage door moving, a household robot stuck) -- {"source", "room", "at",
    "size_m"} -- is emitted through camera.visual_event, so in geometric mode the head camera's current pose witnesses
    it (camera_log.events, obs.camera.events, a "visual" notice); the event log entry is the same either way."""
    sec = nav(w)
    entry = {"time_s": now(w.s), "step": w.s.get("steps", 0), "kind": kind}
    entry.update(data)
    sec["events"].append(entry)
    del sec["events"][:-200]
    sec["counts"][kind] = sec["counts"].get(kind, 0) + 1
    w.emit("nav." + kind, **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list)) or v is None})
    if witness is not None and geometric(w.s):
        from ...kernel import camera
        # the event was emitted above, unchanged; the camera only witnesses it (its geometry is never logged)
        camera.visual_event(w, "nav." + kind, emit=False, **{k: witness[k] for k in ("source", "room", "at", "z", "size_m")
                                                              if witness.get(k) is not None})
    return entry


# notices this domain posts (TIER1_SWEEP 6.1-6.3; w.notice: stored only when the notice bus is on or a wait listens)
NOTICE_KINDS = (
    ("elevator", "an elevator chimes at a landing as its doors open", 65.0),
    ("garage_door", "a garage door motor runs (the door opening or closing)", 70.0),
    ("vehicle", "a car starts, moves or reverses nearby (engine, reversing beeper)", 70.0),
    ("household_robot", "a household robot (robot vacuum, mower) beeps or announces its status", 60.0),
)


def _register_notice_kinds():
    from ...kernel import timeline
    for kind, doc, db in NOTICE_KINDS:
        timeline.register_notice_kind(kind, doc, implies=("any_sound",), db=db)


_register_notice_kinds()

# robot fault kinds this domain raises (body_faults: typed records, the fault log, detection; the table is a plain dict)
HEAT_DAMAGE = "heat_damage"


def _register_fault_kinds():
    from ...kernel import body_faults
    if HEAT_DAMAGE not in body_faults.FAULT_KINDS:
        body_faults.register_fault_kind(
            HEAT_DAMAGE, "body", lambda robot, record: None,
            doc="The robot brushed a hot surface at speed or with a wide load: its cover is scorched (cosmetic, felt "
                "at once; navigation_robot)")


_register_fault_kinds()


def notice(w, kind, source=None, room=None, db=None, text=None, data=None, remote=False):
    """Post something perceivable (w.notice): the timeline decides whether the robot hears it (sound propagation from
    `room`, the body's hearing; remote: the network) and stores it when the notice bus is on or a wait listens.
    Cheap when nobody listens. Returns the stored record or None."""
    from ...kernel import timeline
    if not timeline.active(w):
        return None
    return w.notice(kind, room=room, db=db, source=source, remote=remote, text=text, data=data)


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


def incident(w, kind, *, witness=None, **data):
    """A physical incident: logged and shown to the robot as last_incident (witness: see log)."""
    entry = log(w, kind, witness=witness, **data)
    nav(w)["last_incident"] = copy.deepcopy(entry)
    return entry


# ----------------------------------------------------------------------
# consequences shared by several mechanics: the kernel's physics and body (TIER1_PHYSICS 10.3, TIER1_EMBODIMENT 4.12)

FILM_FOOTPRINT_MM = 0.03             # water film left by a wet sole (below the damage limit of wood floors)


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


def spawn_spill(w, rid, at, liters, liquid="water", source=None, cause="spill"):
    """Liquid that left a container (already drained by the caller) lands on the floor at cell `at` of room `rid`
    through the kernel's single spill entry point (liquids.spill, by the robot: a phys.spill_sink hook's entity --
    cleaning's -- or a kernel puddle that grows when the same liquid lands on the same cell, room wet_cells, the
    splash sound and visual event, phys_spills and the global spills flag). Less than the droplet limit (5 ml) is
    droplets: nothing happens. A kernel puddle of a clear liquid looks like any clear puddle (spill_label: water,
    vinegar and bleach are told apart only up close or by the bottle's label). Returns the floor spill's id or None."""
    from ...kernel.phys import liquids
    liters = round(max(0.0, float(liters)), 4)
    if rid not in w.s["rooms"]:
        return None
    liquid = liquid or "water"
    src = source if isinstance(source, str) and source in w.s["entities"] else None
    return _looks(w, liquids.spill(w, liters, liquid, at=(rid, list(cell(at))), source=src, by="robot", cause=cause),
                  liquid)


def _looks(w, sid, liquid):
    """A kernel puddle of a clear liquid is labelled by what it looks like (spill_label), not by what it is."""
    rec = w.s["entities"].get(sid or "")
    if rec is not None and rec.get("_phys_spill") and liquid in CLEAR_LIQUIDS and rec.get("label") != spill_label(liquid):
        w.mut(sid)["label"] = spill_label(liquid)
    return sid


def drain(w, item, liters=None):
    """Take `liters` (default all) out of a container's liquid_l; an emptied container forgets where its contents came
    from (contents_from), as the kernel's own drain does. Returns the litres taken (put them somewhere: spawn_spill)."""
    rec = w.s["entities"].get(item)
    if rec is None:
        return 0.0
    have = num(rec.get("liquid_l"))
    lost = round(have if liters is None else min(have, max(0.0, float(liters))), 4)
    if lost <= 0:
        return 0.0
    left = round(have - lost, 4)
    m = w.mut(item)
    m["liquid_l"] = left
    if left <= 0:
        m.pop("contents_from", None)
    return lost


def slop(w, item, liters, cause="slosh"):
    """`liters` of an open container's liquid slop out of it (a stumble, a hard stop, a sloshing walk) and run down to
    what it rests on -- a held tray's rim catches some -- or to the floor under it (liquids.deposit_below, by the
    robot: puddle, splash, spills flag). Returns the litres that left it."""
    from ...kernel.phys import liquids
    rec = w.s["entities"].get(item)
    if rec is None:
        return 0.0
    liquid = rec.get("liquid") or "water"
    lost = drain(w, item, liters)
    if lost > 0:
        _looks(w, liquids.deposit_below(w, item, lost, liquid, source=item, by="robot", cause=cause), liquid)
    return lost


def break_entity(w, eid, why, by="robot"):
    """Break a thing (impact.break_entity: broken, condition broken, damage.broken; glass and ceramic shatter, show it
    at a glance and become a sharp floor hazard; any liquid runs out; phys.broke hooks; the breakages and damage flags
    when the robot caused it). Logs nav.broke. False when it was already broken or is gone. by: "robot" for what the
    robot's own motion did; a household robot the robot runs is the robot's responsibility too."""
    from ...kernel.phys import impact
    if eid not in w.s["entities"] or w.s["entities"][eid].get("broken"):
        return False
    impact.break_entity(w, eid, why, by=by)
    log(w, "broke", target=eid, why=why)
    return True


def knock_off(w, item, rid, at, why, cause="knocked", height_m=None):
    """A thing comes off what it rests on (or out of the robot's grip) and falls to the floor at cell `at` of room
    `rid` (impact.fall, by the robot: from its height above the floor or `height_m`; breakage by its drop tolerance on
    that floor, an open container's liquid runs out, what it carried scatters, floor dents, the fall's sound and
    visual event). Logs nav.knocked_off. Returns the fall info."""
    from ...kernel.phys import impact
    if item not in w.s["entities"]:
        return {"fell": False}
    before = w.s["entities"][item].get("location")
    info = impact.fall(w, item, height_m=height_m, cause=cause, at=(rid, list(cell(at))), by="robot")
    log(w, "knocked_off", item=item, surface=before, why=why, broke=info.get("outcome") == "broke")
    return info


def robot_falls(w, cause, rid=None, at=None, damage=False, height_m=0.0):
    """The robot loses balance and falls at cell `at` of room `rid` (default where it stands): body_faults.fall owns
    the fall (fallen, posture lying, off any dock or climbing aid, one stats.falls, the robot_fell event and hooks;
    held items drop through body_faults.drop_held -- impact.fall from the carry height plus height_m -- and damage
    raises actuator_failed). height_m: how far the robot itself fell (a stair flight's half, a stool's step).
    damage: True / False whether the fall damages the actuators; None lets the body decide (a fall from 0.4 m or
    more, or onto a carried load of 5 kg or more). This domain adds its own fall statistic and the incident the robot
    perceives."""
    from ...kernel import body_faults
    robot = w.robot
    rid = rid or robot["room"]
    at = list(cell(at if at is not None else (robot.get("pos") or [0, 0])))
    info = body_faults.fall(w, cause, room=rid, cell=at, height_m=float(height_m),
                            damage=None if damage is None else bool(damage))
    if not info:
        return info                     # already lying on the floor: no second fall
    w.stat("nav_robot_falls")
    incident(w, "robot_fell", cause=cause, room=rid, cell=at, damaged=bool(info.get("damage")),
             dropped=list(info.get("dropped") or []))
    return info


def stumble(w, cause, rid, at):
    """A recovered stumble: no fall, but a quarter of every open container's liquid slops out (slop) and the jolt is
    logged."""
    spilled = 0.0
    for item in open_liquids(w):
        spilled += slop(w, item, round(num(w.s["entities"][item].get("liquid_l")) * 0.25, 4))
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
