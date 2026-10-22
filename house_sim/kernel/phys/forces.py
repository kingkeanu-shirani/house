"""Robot capacities, lift plans, pushing, friction and floor scratching (docs/design/TIER1_PHYSICS.md 5.6, with the
binding changes of docs/design/TIER1_INTEGRATION.md 1.2 and 1.5).

    robot_caps(w, eid=None)                  the robot body's capability numbers (the only way physics reads them)
    lift_plan(w, eid, hands=None, helpers=()) how an item would be lifted; never raises (refusals are reason codes)
    push_needed_n(w, eid, floor_id=None)     force to start sliding an item: mu(foot, floor) m g, casters c_rr m g
    max_push_n(w, helpers=())                robot push plus the push of present, awake, consenting helpers
    tips_when_pushed(w, eid, contact_h_m)    pushed at height h it tips before it slides (mu h > b / 2)
    friction(rec_a, rec_b=None, wet=None)    friction coefficient between two records (or one on finished wood)
    scratch(w, floor_id, mover, distance_m, by=None)   drag damage to a floor (manipulation's scratch_risk)

Masses are subtree masses (an item with everything on or in it, liquids included) from support.subtree_mass, the
single implementation (PHYS-STATICS). Nothing here registers anything.
"""
import importlib
import math

from .props import FLOORS, INF, MATERIALS, SUPPORTS, floor_kind, floor_props, props_of

G = 9.81
CAP_KEYS = ("max_hand_kg", "max_mass_kg", "push_n", "pull_n", "grip_n", "pinch_n", "wrist_nm")
TWO_HAND_SPAN_M = 0.7          # core pick_up: an item wider than this needs both hands
MU_CAP = 1.0                   # a foot factor can not raise friction above this (rubber feet; TIER1_PHYSICS 4.5)
SCRATCH_VISIBLE_RISK = 0.3     # manipulation floor_contact_effects: below this risk a drag leaves no visible mark
SCRATCH_SEVERITY_PER_M = 0.1   # damage severity per metre dragged, per unit of risk (TIER1_PHYSICS 5.6)
GRIT_SCRATCH = 0.2             # floor grit (sand, dirt) at or above this adds half the floor's scratch factor
HANDS = ("left", "right")

_FEET = FLOORS["feet"]
_FEET_KINDS = _FEET["kinds"]
_UPHOLSTERED = _FEET["upholstered_legs"]
_MU_REF = float(MATERIALS["classes"][MATERIALS["pair_reference"]["class"]]["mu"])
_STATE_MU = MATERIALS["surface_state_mu"]["values"]
_PEOPLE = SUPPORTS["people"]


def _subtree_mass(state, eid):
    """Mass of eid and everything on or in it (support.subtree_mass, PHYS-STATICS; imported at call time)."""
    return importlib.import_module(__package__ + ".support").subtree_mass(state, eid)


def _impact():
    """kernel/phys/impact.py (PHYS-STATICS; imported at call time)."""
    return importlib.import_module(__package__ + ".impact")


def _add_damage(w, eid, kind, increment, detail, by, **event):
    """Raise eid's damage of a kind by increment (capped at 1.0) through impact.damage, which takes the new level;
    the current level is read with impact.damage_map. event: impact.damage's event, note, ids and payload keywords
    (the scratch emits and records phys.scratch)."""
    impact = _impact()
    level = impact.damage_map(w.s["entities"][eid].get("damage")).get(kind, 0.0) + increment
    impact.damage(w, eid, kind, min(1.0, level), detail=detail, by=by, **event)


def _num(value, default):
    """A finite float, else default (booleans are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return float(value)


# ----------------------------------------------------------------------------------------------------------------
# robot and helpers

def robot_caps(w, eid=None):
    """{"max_hand_kg", "max_mass_kg", "push_n", "pull_n", "grip_n", "pinch_n", "wrist_nm"} of the robot, all from the
    robot body (TIER1_INTEGRATION 1.2; kernel/body.py, imported at call time):

    max_hand_kg   body.capacity_kg(state, None, eid): the stronger single hand, derated for eid's reach (an overhead
                  cell, above the head), a climbing aid, limp mode, arm health and the lift_capacity hooks
    max_mass_kg   body.capacity_kg(state, "both", eid): both hands near the body, with the same derates
    push_n, pull_n, wrist_nm   body.limit (the profile, profile_overrides applied)
    grip_n, pinch_n            body.grip_limit_n of the stronger working hand ("power" / "pinch"; 0 with no arm)

    Physics reads robot capabilities only through this function."""
    body = importlib.import_module("..body", __package__)
    state = w.s
    grip = [body.grip_limit_n(state, hand, "power") for hand in HANDS]
    pinch = [body.grip_limit_n(state, hand, "pinch") for hand in HANDS]
    return {"max_hand_kg": body.capacity_kg(state, None, eid), "max_mass_kg": body.capacity_kg(state, "both", eid),
            "push_n": body.limit(state, "push_n"), "pull_n": body.limit(state, "pull_n"), "grip_n": max(grip),
            "pinch_n": max(pinch), "wrist_nm": body.limit(state, "wrist_nm")}


def _helper_value(person, key):
    """lift_kg or push_n of a person: their own value, else their role's (supports.json people); 0 when their
    mobility is limited. For lifts, manipulation's existing max_lift_kg field counts as the person's own value."""
    abilities = person.get("abilities") or {}
    if abilities.get("mobility", "full") != "full":
        return 0.0
    own = person.get(key)
    if own is None and key == "lift_kg":
        own = person.get("max_lift_kg")
    if _num(own, None) is not None:
        return max(0.0, float(own))
    return float(_role_row(person)[key])


def _role_row(person):
    """The supports.json people row of a person: their role's, else the age band's (age_roles: a guest, visitor,
    contractor or courier of 70 helps like an elderly resident, a 2-year-old guest not at all), else default_role's
    (people's adoption request K-HELPER-ROLES)."""
    roles = _PEOPLE["roles"]
    row = roles.get(person.get("role"))
    if row is not None:
        return row
    age = _num(person.get("age"), None)
    if age is not None:
        for band in _PEOPLE.get("age_roles") or ():
            if age < float(band["below_age"]) and band["role"] in roles:
                return roles[band["role"]]
    return roles[_PEOPLE["default_role"]]


def _helpers(state, helpers, key):
    """[(pid, value)] of the helpers who count: present in the robot's room, awake and consenting, with a positive
    value. Duplicates count once; order as given."""
    room = state["robot"].get("room")
    people = state.get("people") or {}
    out, seen = [], set()
    for pid in helpers:
        person = people.get(pid)
        if pid in seen or person is None:
            continue
        seen.add(pid)
        if person.get("location") != room or person.get("awake") is False or person.get("consent") is False:
            continue
        value = _helper_value(person, key)
        if value > 0:
            out.append((pid, value))
    return out


# ----------------------------------------------------------------------------------------------------------------
# lifting

def lift_plan(w, eid, hands=None, helpers=()):
    """How the robot would lift eid; it never raises (TIER1_INTEGRATION 1.5).

    hands: None (any free hand), "left" or "right" (that hand only), or "both" (also pass "both" for grip=two_hand).
    Two hands are needed when hands is "both", the record is two_handed, the subtree mass exceeds what the stronger
    hand can take as the robot believes it (body.capacity_kg(..., believed=True): a latent weak arm still counts at
    full strength, so check_lift finds it) or the largest of the first two size_m sides exceeds 0.7 m: core
    pick_up's thresholds. With hands None the free working hand whose believed capacity takes the mass is chosen,
    the right one first (a known weak right arm hands a load it cannot take to the left). A two-hand lift over
    max_mass_kg is still ok when present, awake, consenting helpers' lift_kg covers the rest.

    Returns {"ok", "hands" (the hands it would use: ["left", "right"], [hand], or [] when none is free),
    "mass_kg", "reason", "helpers" (ids counted in a team lift)} with reason None, "hands_busy" (the hands it needs
    hold something), "arm_failed" (an arm it needs is out of order) or "over_capacity"."""
    state = w.s
    record = state["entities"][eid]
    robot = state["robot"]
    caps = robot_caps(w, eid)
    mass = _subtree_mass(state, eid)
    held = robot.get("hands") or {}
    arms = robot.get("arms_ok") or {}
    size = record.get("size_m") or [0.1]
    from .. import body
    believed = {hand: body.capacity_kg(state, hand, eid, believed=True) for hand in HANDS}
    two = (hands == "both" or bool(record.get("two_handed")) or mass > max(believed.values()) + 1e-9
           or max(_num(v, 0.1) for v in size[:2]) > TWO_HAND_SPAN_M)
    plan = {"ok": False, "hands": [], "mass_kg": mass, "reason": None, "helpers": []}
    if two:
        plan["hands"] = list(HANDS)
        if not all(arms.get(hand, True) for hand in HANDS):
            plan["reason"] = "arm_failed"
        elif any(held.get(hand) is not None for hand in HANDS):
            plan["reason"] = "hands_busy"
        elif mass > caps["max_mass_kg"] + 1e-9:
            team = _helpers(state, helpers, "lift_kg")
            if mass > caps["max_mass_kg"] + sum(value for _, value in team) + 1e-9:
                plan["reason"] = "over_capacity"
            else:
                plan["helpers"] = [pid for pid, _ in team]
        plan["ok"] = plan["reason"] is None
        return plan
    order = [hands] if hands in HANDS else ["right", "left"]          # core's free_hand prefers the right hand
    free = [hand for hand in order if hand in held and held[hand] is None]
    usable = [hand for hand in free if arms.get(hand, True)]
    if hands not in HANDS:
        # any free hand: the one the robot believes can take it, the right one first on a tie
        usable = [h for h in usable if believed[h] + 1e-9 >= mass] + [h for h in usable if believed[h] + 1e-9 < mass]
    if usable:
        plan.update(ok=True, hands=[usable[0]])
    else:
        plan["reason"] = "arm_failed" if free else "hands_busy"
    return plan


# ----------------------------------------------------------------------------------------------------------------
# friction, feet and pushing

def _surface_state(record):
    """A record's surface state: its surface_state when known, else from wetness (>= 0.3 wet, >= 0.1 damp)."""
    state = record.get("surface_state")
    if state not in _STATE_MU:
        wetness = _num(record.get("wetness"), 0.0)
        state = "wet" if wetness >= 0.3 else "damp" if wetness >= 0.1 else "dry"
    return state


def friction(rec_a, rec_b=None, wet=None):
    """Static friction coefficient of rec_a on rec_b (on finished wood when rec_b is None): mu_a mu_b / mu_wood from
    the material columns (record friction_mu wins), times the surface-state factor of the slipperier surface
    (surface_state, else wetness: >= 0.3 wet, >= 0.1 damp). wet overrides the state: True ("wet"), False ("dry")
    or a state name (dry, damp, wet, soapy, oily, greasy). A class with its own wet friction (soap) uses it when
    wet or soapy. Clamped to [0.02, 1.0]."""
    pa = props_of(rec_a)
    mu, mu_wet = pa.mu, pa.mu_wet
    if rec_b is not None:
        pb = props_of(rec_b)
        mu = mu * pb.mu / _MU_REF
        if pb.mu_wet is not None:
            mu_wet = pb.mu_wet if mu_wet is None else min(mu_wet, pb.mu_wet)
    if wet is None:
        states = [_surface_state(rec_a)] + ([_surface_state(rec_b)] if rec_b is not None else [])
        state = min(states, key=lambda name: _STATE_MU[name])
    elif isinstance(wet, bool):
        state = "wet" if wet else "dry"
    elif wet in _STATE_MU:
        state = wet
    else:
        raise ValueError("wet must be None, a bool or one of {}".format(", ".join(sorted(_STATE_MU))))
    if mu_wet is not None and state in ("wet", "soapy"):
        mu = mu_wet
    else:
        mu *= _STATE_MU[state]
    return max(0.02, min(MU_CAP, mu))


def foot_kind(record):
    """What an item stands on when it is slid (manipulation foot_material, by class): "slider", the pad kind ("felt"),
    "caster", an explicit foot_material, "wood" legs under upholstery, else the item's material class."""
    if record.get("on_sliders"):
        return "slider"
    pads = record.get("pads")
    if isinstance(pads, dict) and _num(pads.get("count"), 0) >= _num(pads.get("legs"), 4) and pads.get("adhered", True):
        return str(pads.get("kind") or "felt")
    if record.get("casters") or record.get("wheeled"):
        return "caster"
    foot = record.get("foot_material")
    if isinstance(foot, str) and foot:
        return foot if foot in _FEET_KINDS else props_of(dict(record, material=foot)).cls
    cls = props_of(record).cls
    if record.get("upholstered") or cls in _UPHOLSTERED["classes"]:
        return _UPHOLSTERED["kind"]
    return cls


def _foot_mu(floor, foot):
    """Friction (or rolling resistance for casters) of a foot kind on a floor row."""
    row = _FEET_KINDS.get(foot) or {}
    if row.get("rolls"):
        return float(floor["c_rr"])
    factor = row.get("mu_factor_soft") if floor.get("soft") and "mu_factor_soft" in row else row.get("mu_factor", 1.0)
    return min(MU_CAP, float(floor["mu"]) * float(factor))


def _floor_of(state, record, floor_id):
    """floors.json row (a dict) for the floor an item stands on: floor_id's material when given (a rug entity is
    "rug"), else the floor of the item's room at its cell (a rug there counts)."""
    if floor_id is not None:
        floor = state["entities"][floor_id]
        kind = "rug" if (floor.get("type") == "rug" or "rug" in (floor.get("is_a") or ())
                         or floor.get("category") == "rug") else floor_kind(floor.get("material"))
        return dict(FLOORS["floors"][kind], material=kind)
    if record.get("location") not in state["rooms"]:
        raise ValueError("{} is not standing on a floor or a support".format(record.get("label") or "the item"))
    return floor_props(state, record["location"], record.get("pos"))


def _contact_mu(state, eid, floor_id):
    """Friction an item must overcome to slide: on another entity object friction, on a floor mu(foot, floor);
    None for anchored or mounted items (they do not move)."""
    record = state["entities"][eid]
    if record.get("anchored") or record.get("mounted"):
        return None
    location = record.get("location")
    if floor_id is None and location in state["entities"]:
        return friction(record, state["entities"][location])
    return _foot_mu(_floor_of(state, record, floor_id), foot_kind(record))


def push_needed_n(w, eid, floor_id=None):
    """Horizontal force (N) that starts eid sliding: mu(foot, floor) x subtree mass x g, casters c_rr m g, an item
    resting on another entity uses object friction; inf for anchored or mounted items. Anchors: a 60 kg sofa on
    hardwood needs 177 N (moves, robot 220 N); a 100 kg loaded dresser needs 294 N (refused; two adults move it)."""
    mu = _contact_mu(w.s, eid, floor_id)
    return INF if mu is None else mu * _subtree_mass(w.s, eid) * G


def max_push_n(w, helpers=()):
    """Sustained push (N) available: the robot's push_n plus each present, awake, consenting helper's push_n."""
    return robot_caps(w)["push_n"] + sum(value for _, value in _helpers(w.s, helpers, "push_n"))


PUSH_DIRECTION_DEG = {"east": 0.0, "south": 90.0, "west": 180.0, "north": 270.0}      # grid degrees (y points south)


def tips_when_pushed(w, eid, contact_h_m, direction=None):
    """True when pushing or pulling eid at contact height h (m) tips it before it slides: mu h > b / 2, with mu its
    sliding friction and b its resting footprint's extent along the push (manipulation tip_check: an unanchored
    bookcase 0.30 m deep on a mu 0.4 floor tips when pulled forward above 0.375 m, pushed sideways only above its
    0.9 m width). direction: the push in grid degrees (0 east, 90 south), a word (east, south, west, north) or a
    (dx, dy) vector; None takes the smaller side (the worst case; manipulation's adoption request 8 added the
    direction). Anchored and mounted items never tip."""
    mu = _contact_mu(w.s, eid, None)
    if mu is None:
        return False
    rec = w.s["entities"][eid]
    size = props_of(rec).size_m
    if direction is None:
        base = min(size[0], size[1])
    else:
        if isinstance(direction, str):
            deg = PUSH_DIRECTION_DEG.get(direction.strip().lower())
            if deg is None:
                raise ValueError("push direction must be east, south, west or north: " + direction)
        elif isinstance(direction, (tuple, list)):
            deg = math.degrees(math.atan2(float(direction[1]), float(direction[0])))
        else:
            deg = float(direction)
        along_x, along_y = float(size[0]), float(size[1])
        if int(round(_num(rec.get("rot"), 0.0))) % 180 == 90:
            along_x, along_y = along_y, along_x               # rot 90 / 270: the footprint's sides swap axes
        rad = math.radians(deg)
        base = abs(along_x * math.cos(rad)) + abs(along_y * math.sin(rad))
    return mu * float(contact_h_m) > base / 2.0 + 1e-9


# ----------------------------------------------------------------------------------------------------------------
# scratching

def _drag_blame(w, mover, by):
    """Who a drag mark is charged to: an explicit by ("robot", or any other label: exogenous), else the mover's
    attribution (impact.robot_caused: the robot is acting or moved it), since dragging the mover makes the mark."""
    if by is not None:
        return "robot" if by == "robot" else "exogenous"
    return "robot" if _impact().robot_caused(w, mover) else "exogenous"


def scratch(w, floor_id, mover, distance_m, by=None):
    """Scratch a floor entity under an item dragged distance_m metres (manipulation scratch_risk, unchanged):
    risk = floor scratch x foot scratch x min(1, m / 4 kg), plus half the floor scratch when the floor's grit is at
    least 0.2. A risk of 0.3 or more leaves a visible scratch of severity risk x 0.1 per metre (felt, sliders and
    casters score 0), added to the floor's scratch damage through impact.damage, which emits and records it as one
    ``phys.scratch`` event (TIER1_PHYSICS 7.7; the sounds observer's "scrape") charged to the drag (by, else the
    mover's attribution). Returns the severity added (0.0 for none)."""
    state = w.s
    floor = state["entities"][floor_id]
    floor_row = _floor_of(state, floor, floor_id)
    floor_s = float(floor_row["scratch"])
    foot = foot_kind(state["entities"][mover])
    foot_s = float((_FEET_KINDS.get(foot) or {}).get("scratch", _FEET["default_scratch"]))
    risk = floor_s * foot_s * min(1.0, _subtree_mass(state, mover) / 4.0)
    soil = floor.get("soil") if isinstance(floor.get("soil"), dict) else {}
    grit = max(_num(soil.get("sand"), 0.0), _num(soil.get("dirt"), 0.0), _num(soil.get("grit"), 0.0),
               _num(soil.get("mud"), 0.0) * 0.5)
    if grit >= GRIT_SCRATCH and floor_s > 0:
        risk += 0.5 * floor_s
    if risk < SCRATCH_VISIBLE_RISK or distance_m <= 0:
        return 0.0
    severity = round(risk * SCRATCH_SEVERITY_PER_M * float(distance_m), 4)
    _add_damage(w, floor_id, "scratch", severity, "{} dragged {:.1f} m ({} feet)".format(mover, distance_m, foot),
                _drag_blame(w, mover, by), event="phys.scratch", note="{} was scratched".format(floor_id),
                ids=[mover], mover=mover, distance_m=round(float(distance_m), 4), foot=foot, added=severity)
    return severity
