"""Person-specific reach envelopes (fidelity G), mobility-aid readiness and comfort.

A person reaches an item when it is in their room, outside closed containers, within arm's reach
of their shoulder (horizontal distance <= arm length + 0.15 m torso lean; vertical offset inside the
reach sphere), inside the posture's height band (wheelchair 0.38-1.22 m ADA; elderly 0.6-1.5 m;
lying in bed 0.4-1.1 m), not behind them, not on their weak side, and not balanced within 3 cm
of a surface edge.

For people with track_reach=true the people.reach system keeps derived, observable-to-the-
evaluator fields up to date: reach_items (sorted ids), aid_ready (their own aid usable), and for
track_comfort=true a comfort dict (thermal, seat, light, reach).
"""
import math

from ...kernel import geometry
from ...kernel.perception import light_level
from ...kernel.physics import room_temperature
from ...kernel.plugins import after, hook, system
from . import common as C

TORSO_LEAN_M = 0.15
HIGH_FRICTION_FLOORS = ("carpet", "rubber", "rug", "grass", "wool")


def seat_height(state, p):
    seat = state["entities"].get(p.get("seat") or "")
    if seat is not None:
        return float(seat.get("seat_height_m", seat.get("surface_height_m", 0.45)))
    return 0.45


def person_center(state, p):
    """Seated people sit at their seat's center; otherwise the center of their floor cell."""
    seat = p.get("seat") if p.get("posture") == "sitting" else None
    if seat and seat in state["entities"]:
        room, x, y = C.entity_xy(state, seat)
        if room == p.get("location"):
            return x, y
    return C.person_xy(p)


def facing_deg(state, p):
    if p.get("facing_deg") is not None:
        return float(p["facing_deg"])
    return 90.0


def envelope(state, p):
    """(radius_m, (z_lo, z_hi), shoulder_z) for the person's current posture."""
    r = C.role(p)
    arm = float(p.get("arm_reach_m") or C.ARM_REACH_M.get(r, 0.72))
    if C.is_child(p):
        arm = min(arm, 0.3 + 0.03 * C.age_of(p))
    posture = p.get("posture", "standing")
    mobility = C.abilities(p)["mobility"]
    if posture in ("lying", "sleeping"):
        bed = state["entities"].get(p.get("in_bed") or p.get("seat") or "", {})
        z0 = float(bed.get("surface_height_m", 0.55)) + 0.15
        return C.LYING_REACH_M, (0.4, 1.1), z0
    if posture == "sitting" or mobility in ("wheelchair", "bedbound"):
        sh = seat_height(state, p) + C.SEATED_SHOULDER_ABOVE_SEAT_M
        lo, hi = C.WHEELCHAIR_BAND_M if mobility == "wheelchair" or C.role(p) == "elderly" else (0.3, sh + 0.6)
        return arm + (0.05 if mobility == "wheelchair" else TORSO_LEAN_M), (lo, hi), sh
    sh = C.SHOULDER_STANDING_M.get(r, 1.42)
    if C.is_child(p):
        return arm + TORSO_LEAN_M, (0.0, C.child_reach_m(C.age_of(p))), sh
    if r == "elderly" or mobility in ("cane", "walker", "rollator", "crutches"):
        return arm + TORSO_LEAN_M, C.ELDER_BAND_M, sh
    return arm + TORSO_LEAN_M, (0.05, C.reach_height_m(p)), sh


def _closed_ancestor(state, eid):
    entities = state["entities"]
    loc = entities.get(eid, {}).get("location")
    seen = set()
    while loc in entities and loc not in seen:
        seen.add(loc)
        rec = entities[loc]
        if rec.get("openable") and not rec.get("open") and not rec.get("transparent"):
            return loc
        loc = rec.get("location")
    return None


def reach_check(state, pid, eid):
    """(True, '') when the person can take the item from where they are; else (False, reason)."""
    p = C.person(state, pid)
    rec = state["entities"].get(eid)
    if not p or rec is None or not C.present(p):
        return False, "absent"
    if rec.get("location") == pid:
        return True, ""
    room, x, y = C.entity_xy(state, eid)
    if room != p.get("location"):
        return False, "other_room"
    if rec.get("location") in state["rooms"] and rec.get("relation") in ("under", "behind"):
        return False, "concealed"
    if _closed_ancestor(state, eid):
        return False, "inside_closed"
    radius, (lo, hi), shoulder = envelope(state, p)
    px, py = person_center(state, p)
    d = math.hypot(x - px, y - py)
    if d > radius + 1e-9:
        return False, "too_far"
    z = C.entity_z(state, eid)
    if z < lo - 1e-9 or z > hi + 1e-9:
        return False, "too_low" if z < lo else "too_high"
    vertical = abs(z - shoulder)
    if vertical > radius + 0.25:
        return False, "out_of_sphere"
    face = facing_deg(state, p)
    if d > 0.2:
        ang = C.angle_between(C.facing_vec(face), (x - px, y - py))
        if ang > 110.0:
            return False, "behind"
        strong = C.abilities(p)["strong_side"]
        if strong in ("left", "right"):
            side = C.side_of(face, (px, py), (x, y))
            if side not in (strong, "front"):
                return False, "weak_side"
    edge = C.edge_clearance_m(state, eid)
    if edge is not None and edge < C.EDGE_MARGIN_M - 1e-9:
        return False, "edge"
    return True, ""


def aid_ready(state, pid, w=None):
    """True when the person's own mobility aid is usable: next to them, upright, brakes on, unfolded."""
    p = C.person(state, pid)
    if not p:
        return False
    ids = C.static_ids(w, "mobility_aids") if w is not None else sorted(state["entities"])
    for eid in ids:
        rec = state["entities"][eid]
        if not rec.get("mobility_aid") or rec.get("owner") != pid or rec.get("aid_kind") in ("cushion", "transfer_board"):
            continue
        if rec.get("location") == pid:
            return True
        if rec.get("location") != p.get("location") or rec.get("pos") is None:
            continue
        settings = rec.get("settings") or {}
        if settings.get("folded") or rec.get("orientation") not in (None, "upright"):
            continue
        if "brakes" in settings and settings["brakes"] != "locked":
            continue
        cells = geometry.footprint(rec)
        if min(geometry.chebyshev(tuple(p.get("pos") or (0, 0)), c) for c in cells) <= 1:
            return True
    return False


def comfort(state, pid, w=None):
    p = C.person(state, pid)
    prefs = p.get("comfort") or {}
    room = p.get("location")
    temp = room_temperature(state, room)
    band = prefs.get("temp_c", [21.0, 24.0])
    ents = state["entities"]
    carried = C.portable_ids(w) if w is not None else sorted(ents)
    blanket = any(ents[e].get("location") == pid and (ents[e].get("warmth") or ents[e].get("category") in ("blanket", "throw")) for e in carried)
    thermal = band[0] - (2.0 if blanket else 0.0) <= temp <= band[1] + 0.5
    seat = state["entities"].get(p.get("seat") or "", {})
    seat_ok = bool(seat) and float(seat.get("seat_height_m", seat.get("surface_height_m", 0.45))) >= float(prefs.get("min_seat_m", 0.45)) - 1e-9 \
        and (seat.get("armrests") or "armchair" in seat.get("is_a", []) or seat.get("type") in ("armchair", "wheelchair")) and not seat.get("low_soft")
    lux = C.light_level(w, room) if w is not None else light_level(state, room)
    lamp_near = False
    px, py = C.person_xy(p)
    lights = C.static_ids(w, "lights") if w is not None else sorted(ents)
    for eid in lights:
        rec = ents[eid]
        if rec.get("category") == "light" and rec.get("on") and rec.get("portable"):
            r2, x, y = C.entity_xy(state, eid)
            if r2 == room and math.hypot(x - px, y - py) <= 1.6:
                lamp_near = True
    light_ok = lux >= float(prefs.get("min_light", 0.6)) or lamp_near
    wanted = list(p.get("daily_items") or [])
    reach_ok = all(reach_check(state, pid, e)[0] for e in wanted if e in state["entities"])
    return {"thermal": bool(thermal), "seat": bool(seat_ok), "light": bool(light_ok), "reach": bool(reach_ok),
            "temp_c": round(temp, 1)}


def reach_idle(w):
    """Exact idle probe (kernel v3.1): nobody at home has reach or comfort tracked."""
    return not any((p.get("track_reach") or p.get("track_comfort")) and C.present(p)
                   for p in w.s.get("people", {}).values())


@system("people.reach", order=115, max_dt=30.0, idle=reach_idle)
def reach_system(w, dt, tick):
    """Derived reach/comfort fields for tracked people, recomputed only when something new was touched this step (or
    every 60 s of a long wait): see common.needs_refresh."""
    state = w.s
    for pid in sorted(state.get("people", {})):
        p = state["people"][pid]
        if not (p.get("track_reach") or p.get("track_comfort")) or not C.present(p):
            continue
        if not C.needs_refresh(w, p.get("_reach_at")):
            continue
        mm = w.person_m(pid)      # journal first so the stored stamp already counts this record
        mm["_reach_at"] = {"stamp": C.change_stamp(w), "time_s": C.now_s(state)}
        room = p.get("location")
        items = []
        if p.get("track_reach"):
            for eid in C.room_items(state, room) if room in state["rooms"] else ():   # includes what they hold
                rec = state["entities"][eid]
                if not rec.get("portable") or rec.get("location") == "robot":
                    continue
                ok, _ = reach_check(state, pid, eid)
                if ok:
                    items.append(eid)
            ready = aid_ready(state, pid, w)
            if p.get("reach_items") != items or p.get("aid_ready") != ready:
                m = w.person_m(pid)
                m["reach_items"] = items
                m["aid_ready"] = ready
        if p.get("track_comfort"):
            value = comfort(state, pid, w)
            if p.get("comfort_state") != value:
                w.person_m(pid)["comfort_state"] = value


@hook("episode_start", order=160)
def _reach_at_start(w, env):
    """reach_items / aid_ready / comfort_state exist from the first observation on (before the evaluation baseline)."""
    reach_system(w, 0.0, None)


# ----------------------------------------------------------------------
# leaning aids: a single-tip cane or crutches only stay up leaning on something on a grippy floor

def _lean_support(state, rid, cell):
    cols, rows = geometry.grid_size(state["rooms"][rid])
    if cell[0] in (0, cols - 1) or cell[1] in (0, rows - 1):
        return "wall"
    blocked = geometry.blocked_cells(state, rid, include_agents=False)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if (dx or dy) and (cell[0] + dx, cell[1] + dy) in blocked:
                return blocked[(cell[0] + dx, cell[1] + dy)][0]
    return None


def _rug_under(state, rid, cell):
    for eid in geometry.room_entities(state, rid):
        rec = state["entities"][eid]
        if rec.get("category") == "rug" and tuple(cell) in geometry.footprint(rec):
            return rec
    return None


@after("place", order=130)
def _aid_settles(ctx, result):
    w = ctx.w
    state = w.s
    eid = ctx.args["target"]
    rec = state["entities"].get(eid, {})
    if not rec.get("mobility_aid") or rec.get("location") not in state["rooms"]:
        return
    rid = rec["location"]
    cell = tuple(rec.get("pos") or (0, 0))
    m = w.mut(eid)
    if ctx.args.get("orientation") in ("flat", "on_side"):
        m["orientation"] = ctx.args["orientation"]
        if not rec.get("freestanding") or rec.get("aid_kind") in ("cane", "crutches"):
            m["floor_hazard"] = "trip"
        return
    if rec.get("freestanding"):
        m["orientation"] = "upright"
        m.pop("floor_hazard", None)
        return
    support = _lean_support(state, rid, cell)
    rug = _rug_under(state, rid, cell)
    floor = (rug or {}).get("material") if rug else C.floor_material(state, rid)
    grippy = floor in HIGH_FRICTION_FLOORS or bool(rug)
    if support and grippy:
        m["orientation"] = "upright"
        m["leaning_on"] = support
        m.pop("floor_hazard", None)
        return
    m["orientation"] = "flat"
    m["floor_hazard"] = "trip"
    m.pop("leaning_on", None)
    C.log_event(w, "aid_fell", item=eid, room=rid, reason="no support" if not support else "slid on " + str(floor))
    from . import senses as S
    S.robot_sound(w, 58.0, "cane_clatter")
