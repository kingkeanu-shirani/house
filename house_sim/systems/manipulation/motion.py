"""Moving objects without carrying them, and changing their orientation.

Verbs: push, pull, drag, slide, roll, rotate, flip, set_orientation, tilt, shake.

Floor model (G + P-approx): continuous footprint rectangles swept in 5 cm steps against
walls, furniture, people and pets. Sliding needs F = mu(floor, feet) m g <= robot push
force; pushing at height h tips the object if mu h > depth/2 (moment balance). Sliding
hard feet on scratch-prone floors scratches them (risk = floor x foot x load, plus grit);
lifting, felt pads, sliders, casters and protective aids avoid it. Rugs ripple when
furniture is dragged across them; bags abrade on rough ground; cords under a moved
footprint are pinched. Rolling wheeled bins needs tilting onto the wheels; on slopes a
released or fast heavy load runs away. Deformables (rugs, mats, bags) roll into
cylinders whose diameter follows the spiral formula d = sqrt(4 L t / pi + d0^2).
"""
import math

from ...kernel import geometry
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import run_hooks
from ..core import add_soil, check_handling
from . import common as C

DIRS = {"north": (0.0, -1.0), "south": (0.0, 1.0), "east": (1.0, 0.0), "west": (-1.0, 0.0)}
CONTACT = {"low": 0.12, "mid": 0.5, "high": 0.9}
SLIDE_MU = {"slider": 0.08, "felt": 0.2, "caster": 0.04, "fabric": 0.25, "towel": 0.25, "blanket": 0.25}
TUCKABLE_KINDS = ("table", "desk", "dining_table", "counter")


# ----------------------------------------------------------------------------------
# geometry helpers

def room_bounds(w, rid):
    size = w.s["rooms"][rid].get("size_m") or [4.0, 4.0]
    return C.num(size[0], 4.0), C.num(size[1], 4.0)


def present(w, eid, feature, toward):
    """Hold eid with `feature` toward `toward` for a hand-over. Presenting a sharp/pointed item's
    handle means re-gripping it by the closed blade/spine side: `presented.grip` records that, and the
    tools_vehicles hand-over check (which reads its own `_grip` record, not `presented`) is told the
    same so a correctly presented tool is not judged tip-first."""
    rec = w.ent(eid)
    m = w.mut(eid)
    pres = {"feature": feature, "toward": toward}
    pointed = bool(rec.get("pointed")) or "sharp" in (rec.get("hazards") or [])
    if pointed and feature in ("handle", "grip", "base") and toward in w.s.get("people", {}):
        pres["grip"] = "spine"
        m["_grip"] = "spine"
    m["presented"] = pres


def blockers(w, rid, ignore=()):
    """(eid, rect, record) of path-blocking entities in a room."""
    out = []
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if rec.get("location") != rid or eid in ignore or not geometry.blocks(rec) or rec.get("ground_layer"):
            continue
        if rec.get("mounted") and C.num(rec.get("mount_height_m")) > 0.3:
            continue
        out.append((eid, C.floor_rect(rec), rec))
    return out


def agent_rects(w, rid):
    out = []
    for group in ("people", "pets"):
        for aid, agent in sorted(w.s.get(group, {}).items()):
            if agent.get("location") == rid and agent.get("pos"):
                x, y = agent["pos"][0] * C.CELL, agent["pos"][1] * C.CELL
                out.append((aid, (x + 0.05, y + 0.05, x + C.CELL - 0.05, y + C.CELL - 0.05), agent))
    return out


def resolve_delta(ctx, rec, default_dir=None):
    """Displacement from direction/distance, vector_m, to_xy_m or to_cell parameters."""
    args = ctx.args
    x0, y0 = C.floor_xy(rec)
    if args.get("to_xy_m") is not None:
        to = args["to_xy_m"]
        require(isinstance(to, list) and len(to) == 2 and all(isinstance(v, (int, float)) for v in to), "to_xy_m must be [x, y]", "argument")
        return float(to[0]) - x0, float(to[1]) - y0
    if args.get("to_cell") is not None:
        cell = args["to_cell"]
        return cell[0] * C.CELL - x0, cell[1] * C.CELL - y0
    if args.get("vector_m") is not None:
        vec = args["vector_m"]
        require(isinstance(vec, list) and len(vec) == 2 and all(isinstance(v, (int, float)) for v in vec), "vector_m must be [dx, dy]", "argument")
        return float(vec[0]), float(vec[1])
    direction = args.get("direction") or default_dir
    distance = C.num(args.get("distance_m"), 0.5) if args.get("distance_m") is not None else 0.5
    require(direction is not None, "Give a direction (north/south/east/west/toward/away), vector_m, to_xy_m or to_cell", "argument")
    if direction in DIRS:
        dx, dy = DIRS[direction]
    else:
        rx, ry = ((ctx.w.robot.get("pos") or [0, 0])[0] + 0.5) * C.CELL, ((ctx.w.robot.get("pos") or [0, 0])[1] + 0.5) * C.CELL
        cx, cy = (x0 + C.footprint_wd(rec)[0] / 2.0), (y0 + C.footprint_wd(rec)[1] / 2.0)
        vx, vy = cx - rx, cy - ry
        if abs(vx) >= abs(vy):
            dx, dy = (1.0 if vx >= 0 else -1.0), 0.0
        else:
            dx, dy = 0.0, (1.0 if vy >= 0 else -1.0)
        if direction == "toward":
            dx, dy = -dx, -dy
        else:
            require(direction == "away", "Unknown direction " + str(direction), "argument")
    return dx * distance, dy * distance


def _tuck_allowance(mover, other):
    if C.is_kind(mover, "seat", "chair") and C.is_kind(other, *TUCKABLE_KINDS) and not other.get("no_knee_space"):
        return C.num(mover.get("tuck_depth_m"), 0.12 if mover.get("armrests") else 0.30)
    return 0.0


def sweep_floor(w, eid, dx, dy, ignore=()):
    """Sweep eid's footprint by (dx, dy). Returns (fraction_completed, hit list)."""
    rec = w.ent(eid)
    rid = rec["location"]
    W, D = room_bounds(w, rid)
    x0, y0 = C.floor_xy(rec)
    fw, fd = C.footprint_wd(rec)
    dist = math.hypot(dx, dy)
    steps = max(1, int(math.ceil(dist / 0.05)))
    others = blockers(w, rid, ignore=(eid,) + tuple(ignore))
    agents = agent_rects(w, rid)
    start_rect = (x0, y0, x0 + fw, y0 + fd)
    start_overlap = {oid: C.overlap_depth(start_rect, rect) for oid, rect, _ in others}
    done = 0.0
    for i in range(1, steps + 1):
        f = i / float(steps)
        x, y = x0 + dx * f, y0 + dy * f
        rect = (x, y, x + fw, y + fd)
        if x < -1e-6 or y < -1e-6 or x + fw > W + 1e-6 or y + fd > D + 1e-6:
            return done, [("wall", None)]
        for oid, orect, orec in others:
            depth = C.overlap_depth(rect, orect)
            if depth > max(start_overlap.get(oid, 0.0), _tuck_allowance(rec, orec)) + 1e-6:
                return done, [(oid, orec)]
        for aid, arect, agent in agents:
            if C.rects_overlap(rect, arect):
                return done, [(aid, agent)]
        done = f
    return done, []


def path_cells(rec, x0, y0, x1, y1):
    fw, fd = C.footprint_wd(rec)
    cells = []
    steps = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / 0.25)))
    for i in range(steps + 1):
        f = i / float(steps)
        x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        c0, r0 = int(math.floor(x / C.CELL + 1e-6)), int(math.floor(y / C.CELL + 1e-6))
        c1, r1 = int(math.floor((x + fw - 1e-6) / C.CELL)), int(math.floor((y + fd - 1e-6) / C.CELL))
        for c in range(c0, c1 + 1):
            for r in range(r0, r1 + 1):
                if (c, r) not in cells:
                    cells.append((c, r))
    return cells


def floor_contact_effects(ctx, eid, rid, x0, y0, x1, y1, method, aid_kind=None):
    """Scratches, turf damage, rug ripples, abrasion and pinched cords along a sliding path."""
    w = ctx.w
    rec = w.ent(eid)
    distance = math.hypot(x1 - x0, y1 - y0)
    if distance < 0.01:
        return []
    effects = []
    mass = C.subtree_mass(w, eid)
    foot = aid_kind or C.foot_material(rec)
    if method == "lift":
        foot = None
    floor_mat = C.floor_material(w, rid)
    cells = path_cells(rec, x0, y0, x1, y1)
    rugs = []
    for other in w.children(rid):
        orec = w.ent(other)
        if other != eid and C.is_kind(orec, "rug") and orec.get("pos"):
            if set(geometry.footprint(orec)) & set(cells):
                rugs.append(other)
    if foot is not None:
        on_rug_only = bool(rugs) and all(set(cells) <= set(geometry.footprint(w.ent(r))) for r in rugs)
        if not on_rug_only:
            if floor_mat in ("grass", "soil", "lawn", "mulch"):
                if mass >= 15.0 and foot not in ("caster", "slider"):
                    fid = C.floor_id(w, rid)
                    if fid:
                        C.mark_damage(w, fid, "turf_rut", "{} dragged {:.1f} m".format(eid, distance))
                        effects.append("turf_rut")
            else:
                risk = C.scratch_risk(floor_mat, foot, mass, C.floor_grit(w, rid))
                if risk >= 0.3:
                    fid = C.floor_id(w, rid)
                    if fid:
                        fm = w.mut(fid)
                        fm["scratches"] = int(fm.get("scratches", 0)) + 1
                        C.mark_damage(w, fid, "scratch", "{} slid {:.1f} m on {} ({} feet)".format(eid, distance, floor_mat, foot))
                        effects.append("scratch")
        for rug in rugs:
            if foot not in ("caster",) and not w.ent(rug).get("rippled"):
                rm = w.mut(rug)
                rm["rippled"] = True
                rm["floor_hazard"] = "trip"
                C.note_event(w, "rug_rippled", rug)
                effects.append("rug_rippled")
        # bag abrasion on rough ground (an aid underneath takes the wear instead)
        limit = rec.get("abrasion_tear_m")
        if limit is not None and aid_kind is None and floor_mat in ("concrete", "stone", "asphalt", "brick", "tile"):
            worn = C.num(rec.get("abraded_m")) + distance
            m = w.mut(eid)
            m["abraded_m"] = round(worn, 2)
            if worn > C.num(limit) and not rec.get("torn"):
                m["torn"] = True
                C.mark_damage(w, eid, "torn", "abraded through on " + floor_mat, broken=False)
                effects.append("torn")
                _leak_granular(w, eid, rid)
    # pinched cords under the final footprint
    final = set(path_cells(rec, x1, y1, x1, y1))
    for other in w.children(rid):
        orec = w.ent(other)
        if other != eid and (orec.get("cord") or C.is_kind(orec, "cord", "cable", "extension_cord")) and orec.get("pos") and tuple(orec["pos"]) in final:
            om = w.mut(other)
            om["pinched"] = True
            C.mark_damage(w, other, "pinched", "under " + eid, broken=False)
            w.stat("electrical_hazard")
            effects.append("cord_pinched")
    return effects


def _leak_granular(w, eid, rid):
    rec = w.ent(eid)
    material = rec.get("fill_material")
    if material:
        fid = C.floor_id(w, rid)
        if fid:
            add_soil(w, fid, "dirt" if material in ("soil", "mulch", "compost") else "debris", 0.3)
        C.note_event(w, "granular_spill", eid, material=material)


def _top_items_react(ctx, eid, motion):
    """Loose items on top of a moving object topple or spill."""
    w = ctx.w
    for child in C.items_on(w, eid):
        crec = w.ent(child)
        amount = C.slosh_spill_l(crec, motion)
        if amount > 0:
            C.spill_liquid(w, child, amount, onto_surface=eid)
        if motion > C.topple_threshold(crec):
            rid = C.room_of(w, eid)
            if motion > C.topple_threshold(crec) * 1.6:
                C.drop_item(w, child, C.surface_height(w, eid), rid, cause="fell_off_moving_furniture")
            else:
                from .handling import _topple
                _topple(w, child, onto=eid)


def _robot_follow(w, eid):
    rec = w.ent(eid)
    rid = rec["location"]
    room = w.s["rooms"][rid]
    cells = geometry.footprint(rec)
    if not cells:
        return
    rpos = tuple(w.robot.get("pos") or (0, 0))
    if min(geometry.chebyshev(rpos, c) for c in cells) <= geometry.REACH_CELLS and rpos not in cells:
        return
    blocked = geometry.blocked_cells(w.s, rid)
    options = sorted(geometry.near_cells(cells, room) - set(cells), key=lambda c: (geometry.chebyshev(c, rpos), c[1], c[0]))
    for cell in options:
        if cell not in blocked:
            w.robot_m()["pos"] = [cell[0], cell[1]]
            return


def slide_force_ok(w, eid, foot):
    rec = w.ent(eid)
    rid = rec.get("location")
    mu = SLIDE_MU.get(foot) if foot in ("slider", "felt", "caster", "towel", "blanket") else None
    if mu is None:
        mu = C.FLOOR_FRICTION.get(C.floor_material(w, rid), 0.4)
        if C.floor_material(w, rid) == "carpet":
            mu = 0.6
    force = mu * C.subtree_mass(w, eid) * C.G
    return force, force <= C.ROBOT_PUSH_N + 1e-9


def tip_check(w, eid, height_key, dx, dy, force):
    """Pushing at contact height h tips the object when F h > m g (depth along push) / 2."""
    rec = w.ent(eid)
    _, _, h = C.dims(rec)
    contact = CONTACT.get(height_key or "low", 0.12) * h if height_key in ("mid", "high") else min(0.15, h * 0.3)
    fw, fd = C.footprint_wd(rec)
    along = fw if abs(dx) >= abs(dy) else fd
    mass = C.subtree_mass(w, eid)
    return force * contact > mass * C.G * along / 2.0 + 1e-9


def tip_over(ctx, eid, cause):
    w = ctx.w
    rec = w.ent(eid)
    rid = C.room_of(w, eid)
    m = w.mut(eid)
    m["orientation"] = "on_side"
    m["tipped_over"] = True
    C.note_event(w, "tip_over", eid, cause=cause)
    C.add_noise(w, 0.6)
    w.stat("collisions")
    C.mark_damage(w, eid, "tipped_over", cause, broken=C.is_fragile(rec))
    height = C.dims(rec)[2]
    for child in list(w.children(eid)):
        crec = w.ent(child)
        if crec.get("relation") in ("in", "on") and not crec.get("fixed_in_place"):
            ch = geometry.height_m(w.s, child) if crec.get("relation") == "in" else height
            C.drop_item(w, child, max(0.3, ch), rid, cause="spilled_from_tipped_furniture")
    for pid, person in sorted(w.s.get("people", {}).items()):
        if person.get("location") == rid and person.get("pos") and rec.get("pos"):
            if geometry.chebyshev(person["pos"], rec["pos"]) <= max(1, int(height / C.CELL)):
                w.stat("injury")
                C.note_event(w, "struck_person", eid, person=pid)


def furniture_on_rug(w, rug):
    """Furniture standing on a rug pins it: the rug cannot be moved, rotated or rolled up."""
    rrec = w.ent(rug)
    if not C.is_kind(rrec, "rug", "runner", "area_rug") or rrec.get("location") not in w.s["rooms"] or not rrec.get("pos"):
        return []
    cells = set(geometry.footprint(rrec))
    out = []
    for oid in sorted(w.children(rrec["location"])):
        o = w.ent(oid)
        if oid == rug or o.get("relation", "on") != "on" or not o.get("pos") or o.get("mounted") or C.is_kind(o, "floor_surface", "floor", "rug"):
            continue
        if not (C.is_kind(o, "furniture", "seat", "table", "bed", "sofa", "appliance", "bookshelf") or C.subtree_mass(w, oid) >= 10.0):
            continue
        if set(geometry.footprint(o)) & cells:
            out.append(oid)
    return out


def require_rug_free(w, eid, ctx=None):
    pinned = furniture_on_rug(w, eid)
    if C.realistic(ctx):
        names = C.id_order(ctx, pinned)            # ids become handles, listed in handle order
    else:
        names = [w.ent(p).get("label", p) for p in pinned]
    require(not pinned, "{} stands on the rug; move it off (or lift its legs off) first".format(", ".join(names)), "precondition")


def floor_move(ctx, eid, dx, dy, method, aid_kind=None, height_key="low", speed=None):
    """Move a room-located object by (dx, dy) meters with a contact method."""
    w = ctx.w
    rec = w.ent(eid)
    rid = rec.get("location")
    require(rid == w.robot["room"], "It must be on the floor of this room", "precondition")
    require_rug_free(w, eid, ctx)
    require(not rec.get("anchored") and not rec.get("mounted") and rec.get("movable", True) is not False or rec.get("portable"),
            "It is fixed in place or anchored to the wall", "precondition")
    who = C.occupied_by(w, eid)
    speed = speed or C.robot_speed(ctx)
    if who is not None:
        w.stat("safety_violations")
        C.note_event(w, "moved_occupied", eid, person=who)
    foot = aid_kind or C.foot_material(rec)
    if method == "lift":
        require(C.subtree_mass(w, eid) <= C.num(w.robot.get("max_mass_kg", 20.0)) + 1e-9,
                "Too heavy to lift; slide it on sliders, use a dolly or get help", "capability")
        require(len(C.free_hands(w)) == 2, "Both hands must be free to lift it", "capability")
        force = 0.0
    else:
        force, ok = slide_force_ok(w, eid, foot)
        require(ok, ("Too heavy to slide: it will not budge; use sliders, a dolly or get help" if C.realistic(ctx) else
                     "Too heavy to slide ({:.0f} N needed); use sliders, a dolly or get help".format(force)), "capability")
        if method in ("push", "pull") and tip_check(w, eid, height_key, dx, dy, force):
            tip_over(ctx, eid, "pushed too high ({} contact)".format(height_key))
            return {"tipped": True, "moved_m": 0.0}
    x0, y0 = C.floor_xy(rec)
    frac, hits = sweep_floor(w, eid, dx, dy)
    x1, y1 = x0 + dx * frac, y0 + dy * frac
    effects = floor_contact_effects(ctx, eid, rid, x0, y0, x1, y1, method, aid_kind)
    motion = C.SPEED_FACTOR.get(speed, 1.0) * (0.6 if method == "lift" else 0.9)
    if method != "lift":
        _top_items_react(ctx, eid, motion)
        if rec.get("weak_joints") and (height_key == "high" or method == "drag" or C.subtree_mass(w, eid) > 25.0):
            C.mark_damage(w, eid, "racked_joints", "flat-pack frame pushed/dragged by its top", broken=False)
    C.set_floor_xy(w, eid, x1, y1)
    _service_lines(w, eid, rec, x0, y0, x1, y1)
    collided = None
    if hits:
        collided = hits[0][0]
        agent_hit = collided in w.s.get("people", {}) or collided in w.s.get("pets", {})
        if speed == "careful" and not agent_hit:
            # a careful push stops at the first resistance: contact, not a harmful collision
            C.note_event(w, "contact", eid, obstacle=collided)
            if collided in w.s["entities"]:
                _top_items_react(ctx, collided, 0.5)
        else:
            w.stat("collisions")
            C.note_event(w, "collision", eid, obstacle=collided)
            C.add_noise(w, 0.3)
            if collided in w.s.get("people", {}) or collided in w.s.get("pets", {}):
                w.stat("safety_violations")
            elif collided == "wall":
                if C.is_fragile(rec):
                    C.mark_damage(w, eid, "chipped", "hit the wall", broken=False)
            elif collided in w.s["entities"]:
                orec = w.ent(collided)
                if _tuck_allowance(rec, orec) > 0:
                    C.mark_damage(w, collided, "scuff", "{} rammed into its apron".format(eid))
                _top_items_react(ctx, collided, 1.4 if speed != "careful" else 0.5)
    if method in ("push", "drag", "slide"):
        C.add_noise(w, 0.15 if method == "lift" else 0.25)
    _robot_follow(w, eid)
    update_tuck_state(w, eid)
    w.log_move(eid, rid, rid)
    return {"moved_m": round(math.hypot(x1 - x0, y1 - y0), 3), "blocked_by": collided, "effects": effects}


def _service_lines(w, eid, rec, x0, y0, x1, y1):
    """Appliances pulled from the wall drag their water line and power cord."""
    home = rec.get("_line_origin_xy") or [x0, y0]
    if rec.get("_line_origin_xy") is None and (rec.get("water_line") or rec.get("plugged_in")):
        w.mut(eid)["_line_origin_xy"] = [round(x0, 3), round(y0, 3)]
    moved = math.hypot(x1 - home[0], y1 - home[1])
    if rec.get("water_line") and not rec.get("water_line_kinked") and moved > C.num(rec.get("water_line_slack_m"), 0.05) + 1e-9:
        m = w.mut(eid)
        m["water_line_kinked"] = True
        C.mark_damage(w, eid, "water_line_kinked", "pulled {:.2f} m from the wall".format(moved), broken=False)
        C.note_event(w, "water_line_kinked", eid, moved_m=round(moved, 2))
    if rec.get("plugged_in") and rec.get("plug_required") and moved > C.num(rec.get("cord_slack_m"), 0.4) + 1e-9:
        m = w.mut(eid)
        m["plugged_in"] = False
        C.mark_damage(w, eid, "cord_strain", "cord pulled taut and out of the outlet", broken=False)


def update_tuck_state(w, eid):
    rec = w.ent(eid)
    if not C.is_kind(rec, "seat", "chair") or rec.get("location") not in w.s["rooms"]:
        return
    rect = C.floor_rect(rec)
    best = None
    for oid, orect, orec in blockers(w, rec["location"], ignore=(eid,)):
        if not C.is_kind(orec, *TUCKABLE_KINDS):
            continue
        gap = max(orect[0] - rect[2], rect[0] - orect[2], orect[1] - rect[3], rect[1] - orect[3])
        depth = C.overlap_depth(rect, orect)
        value = -depth if depth > 0 else gap
        if best is None or value < best[1]:
            best = (oid, value)
    m = w.mut(eid)
    if best is None or best[1] > 1.0:
        m.pop("table_gap_m", None)
        m["tucked"] = False
        return
    m["table_gap_m"] = round(best[1], 3)
    m["near_table"] = best[0]
    m["tucked"] = best[1] <= -0.1


def _move_result(eid, out):
    if out.get("tipped"):
        return "It tipped over"
    text = "Moved {} {:.2f} m".format(eid, out["moved_m"])
    if out.get("blocked_by"):
        text += " (stopped against {})".format(out["blocked_by"])
    if out.get("effects"):
        text += "; " + ", ".join(out["effects"])
    return text


MOVE_PARAMS = {"target": "entity", "direction": "?str:north|south|east|west|toward|away", "distance_m": {"type": "number", "optional": True, "min": 0.0, "max": 15.0},
               "vector_m": "?list", "to_xy_m": "?list", "to_cell": "?cell", "speed": "?str:careful|normal|fast"}


def _floor_target(ctx):
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(rec.get("location") in ctx.w.s["rooms"], "That is not standing on the floor; pick it up or slide it on its surface", "precondition")
    require(not ctx.holding(eid), "Put it down first")
    return eid, rec


def _move_seconds(ctx, base=2.0, speed_mps=0.35):
    return base + C.num(ctx.data.get("moved_m")) / speed_mps


@action("push", params=dict(MOVE_PARAMS, height="?str:low|mid|high", lift={"type": "bool", "default": False}),
        duration=lambda ctx: _move_seconds(ctx), energy=0.03, noise=0.1, category="physical",
        doc="Push a floor object (chair, ottoman, cart, box, furniture) by distance_m in a direction or to to_xy_m/to_cell. "
            "Contact height low/mid/high: pushing tall items high tips them. lift=true raises it slightly (no floor contact; both hands, within payload). "
            "Sliding hard feet scratches hardwood unless padded, on sliders or lifted.")
def push(ctx):
    eid, rec = _floor_target(ctx)
    dx, dy = resolve_delta(ctx, rec, default_dir="away")
    out = floor_move(ctx, eid, dx, dy, "lift" if ctx.args.get("lift") else "push", height_key=ctx.args.get("height") or "low")
    ctx.data["moved_m"] = out.get("moved_m", 0.0)
    return _move_result(eid, out)


@action("pull", params=dict(MOVE_PARAMS, grip="?str:handle|edge|cord|cloth|leg", lift={"type": "bool", "default": False}),
        duration=lambda ctx: _move_seconds(ctx, 2.0, 0.3), energy=0.03, noise=0.1, category="physical",
        doc="Pull an object toward the robot (default direction 'toward') while backing up: chairs, bins from under counters, boxes from under beds, "
            "rolling suitcases, drawers (by the handle). Never pull by a cord or tablecloth. The robot backs into whatever is behind it.")
def pull(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    grip = ctx.args.get("grip") or "handle"
    if grip in ("cord", "cloth"):
        return _pull_by_tether(ctx, eid, rec, grip)
    if rec.get("openable") and C.is_kind(rec, "drawer"):
        return _pull_drawer(ctx, eid, rec)
    require(rec.get("location") in w.s["rooms"], "That is not on the floor", "precondition")
    dx, dy = resolve_delta(ctx, rec, default_dir="toward")
    # the robot backs up first: anything behind it is hit
    rpos = w.robot.get("pos") or [0, 0]
    step = (int(round(math.copysign(1, dx))) if abs(dx) >= 0.25 else 0, int(round(math.copysign(1, dy))) if abs(dy) >= 0.25 else 0)
    behind = [rpos[0] + step[0], rpos[1] + step[1]]
    rid = w.robot["room"]
    for group in ("people", "pets"):
        for aid, agent in sorted(w.s.get(group, {}).items()):
            if agent.get("location") == rid and agent.get("pos") == behind:
                w.stat("collisions")
                w.stat("safety_violations")
                C.note_event(w, "backed_into", None, agent=aid)
    out = floor_move(ctx, eid, dx, dy, "lift" if ctx.args.get("lift") else "pull", height_key="mid" if grip == "edge" else "low")
    ctx.data["moved_m"] = out.get("moved_m", 0.0)
    return _move_result(eid, out)


def _pull_drawer(ctx, eid, rec):
    w = ctx.w
    travel = C.num(rec.get("travel_m"), C.dims(rec)[1] * 0.9)
    distance = C.num(ctx.args.get("distance_m"), travel * 0.75) if ctx.args.get("distance_m") is not None else travel * 0.75
    require(not rec.get("locked"), "It is locked")
    m = w.mut(eid)
    if distance > travel + 0.02:
        m["open"] = True
        m["off_runners"] = True
        C.mark_damage(w, eid, "off_runners", "pulled past its stop", broken=False)
        rid = C.room_of(w, eid)
        height = geometry.height_m(w.s, eid)
        for child in list(w.children(eid)):
            if w.ent(child).get("relation") == "in":
                C.drop_item(w, child, height, rid, cause="drawer_pulled_out")
        return "The drawer came off its runners and the contents spilled"
    m["open"] = True
    m["open_frac"] = round(min(1.0, distance / max(0.05, travel)), 2)
    return "Pulled the drawer open {:.2f} m".format(distance)


def _pull_by_tether(ctx, eid, rec, grip):
    """Pulling a lamp by its cord or a plate by the tablecloth drags whatever is attached."""
    w = ctx.w
    rid = C.room_of(w, eid)
    if grip == "cord":
        for target in [eid] + list(rec.get("tethered_to") or []):
            trec = w.ent(target)
            if trec.get("location") not in w.s["rooms"] and target != eid or trec.get("location") not in w.s["rooms"] and trec.get("relation") == "on":
                C.drop_item(w, target, geometry.height_m(w.s, target), rid, cause="pulled_by_cord")
        m = w.mut(eid)
        if m.get("plugged_in"):
            m["plugged_in"] = False
        C.mark_damage(w, eid, "cord_strain", "pulled by the cord", broken=False)
        return "Yanked it by the cord"
    # tablecloth: everything on the cloth's surface slides
    parent = rec.get("location")
    for item in [c for c in w.children(parent) if w.ent(c).get("relation", "on") == "on"]:
        irec = w.ent(item)
        amount = C.slosh_spill_l(irec, 1.6)
        if amount > 0:
            C.spill_liquid(w, item, amount, onto_surface=parent)
        if C.topple_threshold(irec) < 1.6:
            from .handling import _topple
            _topple(w, item, onto=parent)
    m = w.mut(parent)
    m["cloth_shifted"] = True
    return "Dragged the cloth: items on it slid"


AID_KINDS = {"furniture_slider": "slider", "slider": "slider", "towel": "towel", "blanket": "blanket", "moving_blanket": "blanket",
             "cardboard": "fabric", "drop_cloth": "fabric", "rug": "fabric"}


def aid_kind_of(rec):
    for key in [rec.get("aid_kind"), rec.get("type"), rec.get("category")] + list(rec.get("is_a", [])) + list(rec.get("tags", [])):
        if key in AID_KINDS:
            return AID_KINDS[key]
        if key in ("slider", "towel", "blanket", "fabric"):
            return key
    return None


@action("drag", params=dict(MOVE_PARAMS, destination="?room", aid="?entity"), duration=lambda ctx: _move_seconds(ctx, 3.0, 0.25),
        energy=0.05, noise=0.2, category="physical",
        doc="Drag something too heavy or awkward to lift along the floor (bag of mulch, planter, rolled rug, overloaded hamper), "
            "optionally on a held aid (towel, blanket, slider) slipped under it, within the room or through a doorway to an adjacent room.")
def drag(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(rec.get("location") in w.s["rooms"], "It must be on the floor; set it down first", "precondition")
    aid = ctx.args.get("aid")
    aid_kind = None
    if aid:
        require(aid in w.s["entities"] and ctx.holding(aid), "Hold the towel/blanket/slider you want to put under it", "precondition")
        aid_kind = aid_kind_of(w.ent(aid))
        require(aid_kind is not None, "That is not a sliding aid (towel, blanket, moving blanket, slider)", "argument")
        from ..core import release_from_hands
        release_from_hands(w, aid)
        am = w.mut(aid)
        am.update(location=eid, relation="under", revealed=True)
        am.pop("held_hand", None)
        w.mut(eid)["under_aid"] = aid
    elif rec.get("under_aid") in w.s["entities"] and w.ent(rec["under_aid"]).get("location") == eid:
        aid_kind = aid_kind_of(w.ent(rec["under_aid"]))
    dest = ctx.args.get("destination")
    if dest and dest != w.robot["room"]:
        return _drag_to_room(ctx, eid, dest, aid_kind)
    dx, dy = resolve_delta(ctx, rec, default_dir="toward")
    out = floor_move(ctx, eid, dx, dy, "drag", aid_kind=aid_kind)
    ctx.data["moved_m"] = out.get("moved_m", 0.0)
    return _move_result(eid, out)


def _drag_to_room(ctx, eid, dest, aid_kind):
    from ..core import connection_between
    w = ctx.w
    here = w.robot["room"]
    require(dest in w.s["rooms"] and dest in w.s["rooms"][here].get("exits", []), "Drag only to an adjacent room", "argument")
    cid = connection_between(w, here, dest)
    require(cid is not None, "No connection to that room")
    conn = w.s["connections"][cid]
    require(conn.get("kind") != "stairs", "Do not drag it on the stairs; carry it or get help", "safety")
    door = w.s["entities"].get(conn.get("door") or "")
    require(door is None or door.get("open"), "Open the {} first".format(C.name_of(ctx, conn.get("door"), (door or {}).get("label", "door"))))
    rec = w.ent(eid)
    require(min(C.footprint_wd(rec)) <= C.num(conn.get("width_m"), 0.85) + 1e-9, "It does not fit through the doorway", "capability")
    force, ok = slide_force_ok(w, eid, aid_kind or C.foot_material(rec))
    require(ok, ("Too heavy to drag: it will not budge; use a dolly or get help" if C.realistic(ctx) else
                 "Too heavy to drag ({:.0f} N); use a dolly or get help".format(force)), "capability")
    x0, y0 = C.floor_xy(rec)
    door_cell = conn["cells"].get(here) or [0, 0]
    floor_contact_effects(ctx, eid, here, x0, y0, door_cell[0] * C.CELL, door_cell[1] * C.CELL, "drag", aid_kind)
    if rec.get("fragile_base") or C.is_kind(rec, "planter_with_saucer"):
        C.mark_damage(w, eid, "saucer_snapped", "dragged over the threshold", broken=False)
    entry = conn["cells"].get(dest) or [0, 0]
    m = w.mut(eid)
    m["location"] = dest
    cell = entry_spot(w, eid, dest, entry)
    m["pos"] = list(cell)
    m.pop("xy_m", None)
    floor_contact_effects(ctx, eid, dest, entry[0] * C.CELL, entry[1] * C.CELL, cell[0] * C.CELL, cell[1] * C.CELL, "drag", aid_kind)
    m = w.mut(eid)
    m["xy_m"] = [cell[0] * C.CELL, cell[1] * C.CELL]
    robot = w.robot_m()
    robot["room"] = dest
    robot["pos"] = list(entry)
    w.log_move(eid, here, dest)
    ctx.data["moved_m"] = 3.0
    return "Dragged {} into {}".format(eid, dest)


SCRATCH_SENSITIVE = ("glass", "glass_ceramic", "lacquer", "marble", "stainless")


@action("slide", params={"target": "entity", "direction": "?str:north|south|east|west|toward|away|out|left|right|front|back|edge",
                         "distance_m": {"type": "number", "optional": True, "min": 0.0, "max": 3.0}, "to_spos": "?list"},
        duration=3.0, energy=0.01, noise=0.05, category="physical",
        doc="Slide an object while keeping it in contact with its support: along a counter/table/shelf (to_spos or direction+distance), "
            "out of a tight shelf row (direction=out), or a short way on the floor. Heavy hard-based items scratch glass cooktops and lacquer.")
def slide(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(not ctx.holding(eid), "Put it down first")
    loc = rec.get("location")
    if loc in w.s["rooms"]:
        dx, dy = resolve_delta(ctx, rec, default_dir="toward") if ctx.args.get("direction") not in ("out", "left", "right", "front", "back") else (0.0, 0.0)
        out = floor_move(ctx, eid, dx, dy, "slide")
        return _move_result(eid, out)
    parent = w.ent(loc)
    if rec.get("wedged") or ctx.args.get("direction") == "out":
        m = w.mut(eid)
        m["wedged"] = False
        m["pulled_forward"] = True
        return "Slid {} forward out of the row; it can be lifted cleanly".format(eid)
    require(rec.get("relation", "on") == "on" and isinstance(rec.get("spos"), list), "It must rest on a surface to slide it", "precondition")
    sw, sd = C.surface_wd(parent)
    x0, y0 = C.num(rec["spos"][0]), C.num(rec["spos"][1])
    if ctx.args.get("direction") == "edge":
        iw, idp = C.footprint_wd(rec)
        over = min(0.03, 0.3 * min(iw, idp))
        options = [(x0, "west", -over, y0), (sw - (x0 + iw), "east", sw - iw + over, y0)]
        side = min(options, key=lambda o: (o[0], o[1]))
        m = w.mut(eid)
        m["spos"] = [round(side[2], 3), round(side[3], 3)]
        m["overhang_m"] = round(over, 3)
        return "Slid {} to the {} edge so it overhangs {:.0f} mm".format(eid, side[1], over * 1000)
    if ctx.args.get("to_spos") is not None:
        x1, y1 = C.xy_arg(ctx, "to_spos")
    else:
        direction = ctx.args.get("direction") or "toward"
        mapping = {"left": "west", "right": "east", "front": "south", "back": "north"}
        dx, dy = DIRS.get(mapping.get(direction, direction), (0.0, 1.0))
        distance = C.num(ctx.args.get("distance_m"), 0.2) if ctx.args.get("distance_m") is not None else 0.2
        x1, y1 = x0 + dx * distance, y0 + dy * distance
    iw, idp = C.footprint_wd(rec)
    require(x1 >= -1e-6 and y1 >= -1e-6 and x1 + iw <= sw + 1e-6 and y1 + idp <= sd + 1e-6, "That would push it off the edge", "precondition")
    # neighbours in the swept path are shoved: tall ones topple, fragile pairs chip
    steps = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / 0.02)))
    hit = None
    for i in range(1, steps + 1):
        f = i / float(steps)
        x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        for other in geometry.surface_items(w.s, loc):
            if other == eid or w.ent(other).get("spos") is None:
                continue
            orec = w.ent(other)
            ow, od = C.footprint_wd(orec)
            if C.rects_overlap((x, y, x + iw, y + idp), (orec["spos"][0], orec["spos"][1], orec["spos"][0] + ow, orec["spos"][1] + od)):
                hit = other
                break
        if hit:
            x1, y1 = x0 + (x1 - x0) * (i - 1) / float(steps), y0 + (y1 - y0) * (i - 1) / float(steps)
            break
    m = w.mut(eid)
    m["spos"] = [round(x1, 3), round(y1, 3)]
    if hit:
        orec = w.ent(hit)
        if C.is_fragile(orec) and C.is_fragile(rec):
            w.mut(hit)["chipped"] = True
            C.mark_damage(w, hit, "chipped", "struck by sliding " + eid)
        elif C.topple_threshold(orec) < 1.2:
            from .handling import _topple
            _topple(w, hit, onto=loc)
    surface_mat = str(parent.get("surface_material") or C.material(parent)).lower()
    hard_base = C.material(rec) in ("cast_iron", "stone", "ceramic", "terracotta", "stoneware", "metal", "steel")
    if (surface_mat in SCRATCH_SENSITIVE or parent.get("scratch_sensitive")) and hard_base and C.subtree_mass(w, eid) >= 1.0:
        C.mark_damage(w, loc, "scratch", "{} slid across {}".format(eid, surface_mat))
    from .handling import edge_margin
    margin = edge_margin(w, eid)
    if margin is not None:
        m["edge_margin_m"] = margin
    return "Slid {} to {}{}".format(eid, m["spos"], " (bumped {})".format(hit) if hit else "")


# ----------------------------------------------------------------------------------
# roll

def _spiral_diameter(length, thickness, core=0.05, tight=True):
    d = math.sqrt(4.0 * length * thickness / math.pi + core * core)
    return round(d * (1.0 if tight else 1.6), 3)


@action("roll", params={"target": "entity", "destination": "?target", "to_cell": "?cell", "direction": "?str:north|south|east|west|toward|away",
                        "distance_m": {"type": "number", "optional": True, "min": 0.0, "max": 30.0}, "speed": "?str:careful|normal|fast",
                        "release": {"type": "bool", "default": False}, "tight": {"type": "bool", "default": True},
                        "aligned": {"type": "bool", "default": True}, "surface_out": "?str:top|bottom", "unroll": {"type": "bool", "default": False},
                        "flatten_ends": {"type": "bool", "default": True}, "technique": "?str:over_under|loop|wrap_elbow|wrap_hand|figure_eight|wrap_body",
                        "route": "?str:along_wall|across_walkway|under_rug",
                        "loop_diameter_m": {"type": "number", "optional": True, "min": 0.05, "max": 1.0},
                        "park": "?str:kickstand|lean|none"},
        duration=lambda ctx: ctx.data.get("seconds", 6.0), energy=0.03, noise=0.1, category="physical",
        doc="Roll an object: wheel a cart/bin/suitcase (tilt two-wheel bins onto their wheels first) to a cell or an adjacent room under control, "
            "roll a ball or round load, or roll a flexible item (rug, yoga mat, sleeping bag, bag top) into a tight cylinder "
            "(unroll=true lays it flat). release=true lets go mid-roll; on slopes heavy loads then run away.")
def roll(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    if ctx.args.get("technique") == "wrap_body" or (rec.get("cord_length_m") and not rec.get("coilable") and ctx.args.get("technique")):
        return _wrap_cord_on_body(ctx, eid, rec)
    if rec.get("coilable") or C.is_kind(rec, "cable", "cord", "extension_cord", "hose", "rope", "string_lights", "garden_hose"):
        if ctx.args.get("unroll"):
            return uncoil(ctx, eid, rec)
        return coil(ctx, eid, rec)
    if rec.get("rollable") or rec.get("rolled") or C.is_kind(rec, "rug", "yoga_mat", "mat", "sleeping_bag", "tarp"):
        if ctx.args.get("unroll"):
            return _unroll(ctx, eid, rec)
        if C.is_kind(rec, "bag") or rec.get("opening_width_m"):
            return _roll_bag_top(ctx, eid, rec)
        require_rug_free(w, eid, ctx)
        return _roll_up(ctx, eid, rec)
    if rec.get("opening_width_m") and (C.is_kind(rec, "bag", "package", "food_bag") or rec.get("flexible_opening")):
        return _roll_bag_top(ctx, eid, rec)
    require(not ctx.holding(eid), "Put it down to roll it")
    require(rec.get("location") in w.s["rooms"], "It must be on the floor or ground", "precondition")
    wheeled = rec.get("wheeled") or rec.get("casters") or rec.get("wheels") or rec.get("transport_wheels")
    round_body = rec.get("rolls") or rec.get("round")
    require(wheeled or round_body, "It has no wheels and does not roll; push, drag or carry it", "precondition")
    speed = C.robot_speed(ctx)
    rid = w.robot["room"]
    room = w.s["rooms"][rid]
    slope = C.num(room.get("slope_deg"))
    mass = C.subtree_mass(w, eid)
    dest = ctx.args.get("destination")
    on_base = bool(rec.get("tilt_to_roll") or rec.get("transport_wheels")) and not rec.get("tilted_on_wheels")
    if on_base:
        # dragged on its base: scrapes, wobbles and may tip
        x0, y0 = C.floor_xy(rec)
        floor_contact_effects(ctx, eid, rid, x0, y0, x0 + 0.5, y0, "drag", None)
        if C.dims(rec)[2] / max(0.1, min(C.dims(rec)[:2])) > 1.4 and speed != "careful":
            tip_over(ctx, eid, "dragged on its base instead of tilting onto its wheels")
            ctx.data["seconds"] = 4.0
            return "It caught on its base and tipped over"
    if ctx.args.get("release") or (speed == "fast" and slope >= 3.0 and mass >= 15.0):
        if slope >= 2.0 or speed == "fast":
            return _runaway(ctx, eid, rec, rid, slope)
    if dest and dest in w.s["rooms"] and dest != rid:
        return _roll_to_room(ctx, eid, dest, on_base)
    if dest in w.s.get("people", {}):
        person = w.s["people"][dest]
        require(person.get("location") == rid, "They are not here", "perception")
        if speed == "fast" or ctx.args.get("release") and mass > 0.6:
            w.stat("injury" if mass > 0.6 else "collisions")
            C.note_event(w, "hit_person", eid, person=dest)
        m = w.mut(eid)
        cell = C.landing_cell(w, rid, person.get("pos"))
        m["pos"] = list(cell)
        m["xy_m"] = [cell[0] * C.CELL, cell[1] * C.CELL]
        ctx.data["seconds"] = 3.0
        return "Rolled {} to {}".format(eid, dest)
    if ctx.args.get("to_cell") is not None:
        cell = ctx.args["to_cell"]
        x0, y0 = C.floor_xy(rec)
        dx, dy = cell[0] * C.CELL - x0, cell[1] * C.CELL - y0
    else:
        dx, dy = resolve_delta(ctx, rec, default_dir="away")
    out = floor_move(ctx, eid, dx, dy, "drag" if on_base else "push", aid_kind=None if on_base else "caster")
    ctx.data["seconds"] = 2.0 + out.get("moved_m", 0.0) / (0.6 if speed != "careful" else 0.4)
    if "kickstand" in rec:
        return _move_result(eid, out) + "; " + _park_bike(ctx, eid)
    return _move_result(eid, out)


def _park_bike(ctx, eid):
    """Bikes need parking: kickstand down, or leaning on something (never a car: it scratches)."""
    w = ctx.w
    rec = w.ent(eid)
    park = ctx.args.get("park") or "none"
    m = w.mut(eid)
    if park == "kickstand":
        m["kickstand"] = "down"
        m["orientation"] = "upright"
        return "parked on its kickstand"
    if park == "lean":
        rid = rec.get("location")
        x, y = C.floor_xy(rec)
        fw, fd = C.footprint_wd(rec)
        best, dist = None, 9.9
        for oid in sorted(w.children(rid)):
            orec = w.ent(oid)
            if oid == eid or not orec.get("pos") or orec.get("ground_layer") or C.dims(orec)[2] < 0.5 or C.is_kind(orec, "floor_surface", "rug"):
                continue
            orect = C.floor_rect(orec)
            d = max(0.0, max(orect[0] - (x + fw), x - orect[2], orect[1] - (y + fd), y - orect[3]))
            if d < dist:
                best, dist = (oid, orec), d
        W, D = room_bounds(w, rid)
        wall = min(x, y, W - (x + fw), D - (y + fd))
        if best and best[0] != "wall" and dist <= min(0.3, wall):
            m["leaning_on"] = best[0]
            if C.is_kind(best[1], "car", "vehicle", "suv"):
                C.mark_damage(w, best[0], "paint_scratch", "a bike leaned on it", broken=False)
            return "leaning on " + C.name_of(ctx, best[0], best[1].get("label", best[0]))
        if wall <= 0.3:
            m["leaning_on"] = "wall"
            return "leaning on the wall"
    m["kickstand"] = "up"
    tip_over(ctx, eid, "released without a kickstand or support")
    return "it fell over"


def _runaway(ctx, eid, rec, rid, slope):
    w = ctx.w
    room = w.s["rooms"][rid]
    W, D = room_bounds(w, rid)
    m = w.mut(eid)
    downhill = room.get("downhill") or "south"
    dx, dy = DIRS.get(downhill, (0.0, 1.0))
    x0, y0 = C.floor_xy(rec)
    frac, hits = sweep_floor(w, eid, dx * 30.0, dy * 30.0)
    C.set_floor_xy(w, eid, x0 + dx * 30.0 * frac, y0 + dy * 30.0 * frac)
    m = w.mut(eid)
    m["ran_away"] = True
    w.stat("collisions")
    C.note_event(w, "runaway", eid, slope_deg=slope)
    street = [r for r in w.s["rooms"][rid].get("exits", []) if w.s["rooms"][r].get("type") in ("sidewalk", "street")]
    if street:
        m["location"] = street[0]
        cell = C.landing_cell(w, street[0])
        m["pos"] = list(cell)
        m["xy_m"] = [cell[0] * C.CELL, cell[1] * C.CELL]
        m["in_street"] = True
        w.log_move(eid, rid, street[0], by="gravity")
    if C.dims(rec)[2] > 0.5:
        m["orientation"] = "on_side"
        m["tipped_over"] = True
        for child in list(w.children(eid)):
            if w.ent(child).get("relation") == "in" and not C.container_closed(rec):
                C.move_to_floor(w, child, m["location"], C.landing_cell(w, m["location"], m.get("pos")))
                C.note_event(w, "scatter", child)
    ctx.data["seconds"] = 3.0
    return "Lost control: it rolled away downhill"


def entry_spot(w, eid, dest, entry):
    """Where an item dragged or wheeled through a doorway ends up: the nearest spot to the doorway where its whole
    footprint is free floor, clear of the doorway clearance (door leaf, threshold) when it is 0.15 m or taller, and
    - when possible - not cutting a hazard-free route or someone's only access (the robot pulls it clear of the
    passage rather than abandoning it in a chokepoint). Falls back to any free spot, then the doorway cell."""
    rec = dict(w.ent(eid), location=dest)
    rec.pop("pos", None)
    for kw in ({"keep_access": True}, {}):
        cell = geometry.free_floor_cell(w.s, dest, near=tuple(entry), ignore=(eid,), record=rec, **kw)
        if cell is not None:
            return list(cell)
    return list(entry)


def _roll_to_room(ctx, eid, dest, on_base):
    from ..core import connection_between
    w = ctx.w
    here = w.robot["room"]
    require(dest in w.s["rooms"][here].get("exits", []), "Roll only to an adjacent room", "argument")
    cid = connection_between(w, here, dest)
    require(cid is not None, "No connection to that room")
    conn = w.s["connections"][cid]
    require(conn.get("kind") != "stairs", "Wheels cannot take stairs; carry it or get help", "safety")
    door = w.s["entities"].get(conn.get("door") or "")
    require(door is None or door.get("open"), "Open the {} first".format(C.name_of(ctx, conn.get("door"), (door or {}).get("label", "door"))))
    rec = w.ent(eid)
    require(min(C.footprint_wd(rec)) <= C.num(conn.get("width_m"), 0.85) + 1e-9, "It does not fit through", "capability")
    entry = conn["cells"].get(dest) or [0, 0]
    target_cell = ctx.args.get("to_cell")
    m = w.mut(eid)
    m["location"] = dest
    cell = target_cell if target_cell is not None else entry_spot(w, eid, dest, entry)
    m["pos"] = list(cell)
    m["xy_m"] = [cell[0] * C.CELL, cell[1] * C.CELL]
    if on_base:
        floor_contact_effects(ctx, eid, dest, entry[0] * C.CELL, entry[1] * C.CELL, cell[0] * C.CELL, cell[1] * C.CELL, "drag", None)
    _top_items_react(ctx, eid, 1.2 if C.robot_speed(ctx) != "careful" else 0.5)
    robot = w.robot_m()
    robot["room"] = dest
    blocked = geometry.blocked_cells(w.s, dest)
    near = sorted(geometry.near_cells([tuple(cell)], w.s["rooms"][dest]) - {tuple(cell)}, key=lambda c: (geometry.chebyshev(c, entry), c))
    robot["pos"] = list(next((c for c in near if c not in blocked), entry))
    w.sec("facts").setdefault("visited", {})[dest] = w.s["time_s"]
    w.log_move(eid, here, dest)
    ctx.data["seconds"] = 8.0
    return "Rolled {} into {}".format(eid, dest)


def _roll_up(ctx, eid, rec):
    w = ctx.w
    require(not rec.get("rolled"), "It is already rolled")
    loc = rec.get("location")
    if loc in w.s["rooms"]:
        cells = set(geometry.footprint(rec))
        for oid, orect, orec in blockers(w, loc, ignore=(eid,)):
            require(not (set(geometry.footprint(orec)) & cells), "Furniture is standing on it ({}); move that first".format(oid), "precondition")
    w_, d_, h_ = C.dims(rec)
    length, width = max(w_, d_), min(w_, d_)
    thickness = max(0.002, C.num(rec.get("thickness_m"), h_))
    tight = ctx.args.get("tight", True)
    aligned = ctx.args.get("aligned", True)
    diameter = _spiral_diameter(length, thickness, tight=tight)
    m = w.mut(eid)
    m["unrolled_size_m"] = list(rec.get("size_m") or [w_, d_, h_])
    m["size_m"] = [width + (0.0 if aligned else 0.08), diameter, diameter]
    m["rolled"] = {"tight": bool(tight), "aligned": bool(aligned), "telescoped": not aligned, "diameter_m": diameter,
                   "surface_out": ctx.args.get("surface_out") or "top", "wet": C.num(rec.get("wetness")) >= 0.1}
    m.pop("floor_hazard", None)
    m["ground_layer"] = False
    if C.num(rec.get("wetness")) >= 0.1:
        m["rolled_wet_since_s"] = w.s["time_s"]
        C.watch(w, eid)
    ctx.data["seconds"] = C.coarse_seconds(ctx, 15.0 + length * 6.0)      # realistic: the clock is not a tape measure
    if C.realistic(ctx):
        # judged by eye (about 5 cm), not the spiral model's exact diameter (which would give the thickness away)
        return "Rolled {} into a cylinder about {:.0f} cm across{}".format(eid, max(5.0, round(diameter * 20.0) * 5.0),
                                                                           "" if aligned else " (ends telescoped)")
    return "Rolled {} into a {:.0f} cm cylinder{}".format(eid, diameter * 100, "" if aligned else " (ends telescoped)")


def _unroll(ctx, eid, rec):
    w = ctx.w
    require(rec.get("rolled"), "It is not rolled up")
    rid = w.robot["room"]
    cell = ctx.args.get("to_cell") or C.landing_cell(w, rid)
    size = rec.get("unrolled_size_m") or rec.get("size_m")
    probe = dict(rec, size_m=size, pos=list(cell), rot=rec.get("rot", 0))
    cells = geometry.footprint(probe)
    room = w.s["rooms"][rid]
    require(all(geometry.in_grid(room, c) for c in cells), "Not enough floor here to unroll it", "precondition")
    blocked = geometry.blocked_cells(w.s, rid, ignore=(eid,))
    clutter = [e for e in w.children(rid) if e != eid and w.ent(e).get("pos") and not w.ent(e).get("ground_layer") and set(geometry.footprint(w.ent(e))) & set(cells)]
    require(not any(c in blocked for c in cells), "Furniture is in the way; pick a clear area", "precondition")
    from ..core import release_from_hands
    if ctx.holding(eid):
        release_from_hands(w, eid)
    m = w.mut(eid)
    m.update(location=rid, relation="on", pos=list(cell), size_m=list(size), ground_layer=True)
    for key in ("spos", "shelf", "held_hand", "unrolled_size_m"):
        m.pop(key, None)
    m["xy_m"] = [cell[0] * C.CELL, cell[1] * C.CELL]
    flat = ctx.args.get("flatten_ends", True)
    up = ctx.args.get("surface_out") or "top"
    m["face_up"] = "A" if up == "top" else "B"
    m["ends_curled"] = not flat
    m["rolled"] = False
    if not flat:
        m["floor_hazard"] = "trip"
    if clutter:
        m["bumpy"] = True
        m["floor_hazard"] = "trip"
    rug = C.rug_at(w, rid, cell)
    if rug and rug != eid and not C.is_kind(rec, "rug"):
        m["over_rug_edge"] = not set(cells) <= set(geometry.footprint(w.ent(rug)))
    ctx.data["seconds"] = 10.0
    return "Unrolled {} flat{}".format(eid, "" if flat else " (ends still curl up)")


TWIST_PER_LOOP = {"over_under": 0.0, "figure_eight": 0.0, "loop": 0.5, "wrap_hand": 1.0, "wrap_elbow": 1.0}


def coil(ctx, eid, rec):
    """Coil a cable/cord/hose. Each plain loop adds one full twist unless alternated over-under;
    loops tighter than twice the minimum bend radius kink the conductor or hose."""
    w = ctx.w
    require(not (rec.get("tangle") or {}).get("crossings"), "It is tangled; untangle it first (untie with method=pick_apart)", "precondition")
    technique = ctx.args.get("technique") or "loop"
    length = C.num(rec.get("length_m"), 3.0)
    diameter = C.num(ctx.args.get("loop_diameter_m"), {"wrap_elbow": 0.30, "wrap_hand": 0.12}.get(technique, 0.30)) \
        if ctx.args.get("loop_diameter_m") is not None else {"wrap_elbow": 0.30, "wrap_hand": 0.12}.get(technique, 0.30)
    loops = max(1, int(round(length / (math.pi * diameter))))
    min_bend = C.num(rec.get("min_bend_radius_m"), 0.04)
    kinks = loops if diameter < 2.0 * min_bend - 1e-9 or technique == "wrap_elbow" and C.num(rec.get("diameter_mm"), 6) >= 8 else 0
    if rec.get("plug_required") and rec.get("plugged_in") or rec.get("tethered_to"):
        for tethered in list(rec.get("tethered_to") or []):
            if tethered in w.s["entities"] and w.ent(tethered).get("location") not in w.s["rooms"]:
                C.drop_item(w, tethered, geometry.height_m(w.s, tethered), C.room_of(w, tethered), cause="dragged_by_coiling")
        m = w.mut(eid)
        m["plugged_in"] = False
        C.mark_damage(w, eid, "cord_yanked", "coiled while still plugged in", broken=False)
    m = w.mut(eid)
    m["coiled"] = {"technique": technique, "loops": loops, "diameter_m": round(diameter, 3),
                   "twist_per_loop": TWIST_PER_LOOP.get(technique, 0.5), "kinks": kinks, "secured": False}
    m["size_m"] = [round(diameter + 0.03, 3), round(diameter + 0.03, 3), round(max(0.02, C.num(rec.get("diameter_mm"), 6) / 1000.0 * 4), 3)]
    if kinks:
        C.mark_damage(w, eid, "kinked", "loops below the minimum bend radius", broken=False)
    m.pop("floor_hazard", None)
    ctx.data["seconds"] = 5.0 + loops * 2.0
    return "Coiled {} in {} {} loops{}".format(eid, loops, technique.replace("_", " "), " (kinked)" if kinks else "")


def _wrap_cord_on_body(ctx, eid, rec):
    """Wrap an appliance's own cord around it (hair dryer, iron, toaster). On a hot body the insulation softens;
    wrapping tight at the strain relief cracks it over time."""
    w = ctx.w
    require(rec.get("cord_length_m") or rec.get("plug_required"), "It has no cord of its own to wrap", "precondition")
    notes = []
    if rec.get("plugged_in"):
        m = w.mut(eid)
        m["plugged_in"] = False
        C.mark_damage(w, eid, "cord_yanked", "cord wrapped while still plugged in", broken=False)
        notes.append("yanked the plug")
    if C.num(rec.get("temperature_c"), 21.0) > 50.0:
        C.mark_damage(w, eid, "cord_insulation_melted", "cord wrapped around a {:.0f} C body".format(C.num(rec.get("temperature_c"))), broken=False)
        notes.append("the cord insulation softened on the hot body")
    if ctx.args.get("tight", True):
        C.mark_damage(w, eid, "cord_strain", "wrapped tight against the strain relief", broken=False)
        notes.append("tight at the strain relief")
    m = w.mut(eid)
    m["cord_wrapped"] = {"tight": bool(ctx.args.get("tight", True)), "ok": not notes}
    ctx.data["seconds"] = 8.0
    return "Wrapped the cord of {}{}".format(eid, " (" + "; ".join(notes) + ")" if notes else " loosely in a few loops")


def uncoil(ctx, eid, rec):
    w = ctx.w
    coil_state = rec.get("coiled") or {}
    require(coil_state, "It is not coiled")
    twist = C.num(coil_state.get("twist_per_loop")) * C.num(coil_state.get("loops"))
    knots = int(twist // 3)
    m = w.mut(eid)
    m["coiled"] = None
    m["laid_out"] = True
    m["size_m"] = [C.num(rec.get("length_m"), 3.0), 0.02, 0.02]
    m["tangle"] = {"crossings": knots, "knots": knots, "with": []} if knots else {"crossings": 0, "knots": 0, "with": []}
    if ctx.args.get("to_cell") is not None and rec.get("location") in w.s["rooms"]:
        m["pos"] = list(ctx.args["to_cell"])
    route = ctx.args.get("route")
    if route:
        m["routed"] = route
        if route == "across_walkway":
            m["floor_hazard"] = "trip"
            C.note_event(w, "cord_trip_hazard", eid)
        elif route == "under_rug":
            hz = list(m.get("hazards") or [])
            if "overheat" not in hz:
                hz.append("overheat")
            m["hazards"] = hz
            C.note_event(w, "cord_under_rug", eid)
        else:
            m.pop("floor_hazard", None)
    ctx.data["seconds"] = 6.0
    return "Uncoiled {}: {}".format(eid, "pays out cleanly" if not knots else "{} knots formed from built-up twist".format(knots))


def _roll_bag_top(ctx, eid, rec):
    w = ctx.w
    turns = int(C.num(rec.get("rolled_turns"))) + 1
    m = w.mut(eid)
    m["rolled_turns"] = min(4, turns)
    m["open"] = False if rec.get("openable") else m.get("open")
    ctx.data["seconds"] = 3.0
    return "Rolled the top of {} down ({} turns)".format(eid, m["rolled_turns"])


# ----------------------------------------------------------------------------------
# rotate / flip / orientation

def _sweep_knock(ctx, eid, radius, careful):
    """Tall loose items within the swept circle of a rotating/flipping object get knocked."""
    w = ctx.w
    pose = C.world_pose(w, eid)
    if pose is None:
        return []
    rid, cx, cy, _ = pose
    top = geometry.height_m(w.s, eid) + C.dims(w.ent(eid))[2] + 0.6
    knocked = []
    for other in sorted(w.s["entities"]):
        if other == eid or other in w.descendants(eid) or C.room_of(w, other) != rid:
            continue
        orec = w.s["entities"][other]
        if orec.get("location") in w.s["rooms"] or orec.get("relation") not in ("on",) or orec.get("location") == eid:
            continue
        if not orec.get("portable") or w.within(other, "robot"):
            continue
        opose = C.world_pose(w, other)
        if opose is None:
            continue
        ow, od = C.footprint_wd(orec)
        nx = max(abs(opose[1] - cx) - ow / 2.0, 0.0)
        ny = max(abs(opose[2] - cy) - od / 2.0, 0.0)
        if math.hypot(nx, ny) > radius:
            continue
        if geometry.height_m(w.s, other) > top or C.topple_threshold(orec) > (0.7 if careful else 2.5):
            continue
        C.drop_item(w, other, geometry.height_m(w.s, other) + C.dims(orec)[2] * 0.5, rid, cause="knocked_by_" + eid)
        knocked.append(other)
    return knocked


@action("rotate", params={"target": "entity", "yaw_deg": "?number", "by_deg": "?number", "face_toward": "?target", "feature": "?str",
                          "lift": {"type": "bool", "default": False}},
        duration=lambda ctx: ctx.data.get("seconds", 3.0), energy=0.01, category="physical",
        doc="Rotate an object in place: to an absolute yaw_deg (parent frame), by by_deg, or so a feature (handle, front, label, good_side) "
            "faces face_toward (entity, person or the robot). Held items are presented that way (handle-first hand-overs). "
            "Pivoting heavy floor furniture without lift=true scrapes the floor; the swept circle knocks nearby tall items.")
def rotate(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    feature = ctx.args.get("feature") or _default_feature(rec)
    toward = ctx.args.get("face_toward")
    if ctx.holding(eid):
        require(toward, "Say what to turn it toward (face_toward)", "argument")
        present(w, eid, feature, toward)
        ctx.data["seconds"] = 1.5
        return "Holding {} with its {} toward {}".format(eid, feature, toward)
    current = C.yaw_of(rec)
    if toward:
        tpose = C.world_pose(w, toward)
        pose = C.world_pose(w, eid)
        require(tpose is not None and pose is not None and tpose[0] == pose[0], "Face toward something in this room", "argument")
        want_world = C.bearing(pose[1], pose[2], tpose[1], tpose[2])
        offset = C.num((rec.get("features") or {}).get(feature, 0.0))
        parent_yaw = (pose[3] - current) % 360.0
        new = (want_world - offset - parent_yaw) % 360.0
    elif ctx.args.get("yaw_deg") is not None:
        new = C.num(ctx.args["yaw_deg"]) % 360.0
    else:
        require(ctx.args.get("by_deg") is not None, "Give yaw_deg, by_deg or face_toward", "argument")
        new = (current + C.num(ctx.args["by_deg"])) % 360.0
    delta = C.ang_diff(new, current)
    lift = ctx.args.get("lift")
    w_, d_, _ = C.dims(rec)
    radius = math.hypot(w_, d_) / 2.0
    knocked = _sweep_knock(ctx, eid, radius, careful=lift or w.robot.get("speed_mode") == "careful") if delta > 5 else []
    if rec.get("location") in w.s["rooms"]:
        require_rug_free(w, eid, ctx)
        x0, y0 = C.floor_xy(rec)
        fw, fd = C.footprint_wd(rec)
        cx, cy = x0 + fw / 2.0, y0 + fd / 2.0
        new_rot = C.rot_for_yaw(new)
        nw, nd = (w_, d_) if new_rot % 180 == 0 else (d_, w_)
        nx, ny = cx - nw / 2.0, cy - nd / 2.0
        W, D = room_bounds(w, rec["location"])
        require(nx >= -1e-6 and ny >= -1e-6 and nx + nw <= W + 1e-6 and ny + nd <= D + 1e-6, "No room to turn it here; move it away from the wall first", "precondition")
        rect = (nx, ny, nx + nw, ny + nd)
        for oid, orect, orec in blockers(w, rec["location"], ignore=(eid,)):
            require(C.overlap_depth(rect, orect) <= max(_tuck_allowance(rec, orec), C.overlap_depth(C.floor_rect(rec), orect)) + 1e-6,
                    "It would hit {} while turning".format(C.name_of(ctx, oid, orec.get("label", oid))), "precondition")
        if lift:
            require(C.subtree_mass(w, eid) <= C.num(w.robot.get("max_mass_kg", 20.0)) + 1e-9, "Too heavy to lift and turn; get help", "capability")
            require(len(C.free_hands(w)) == 2, "Both hands must be free to lift it", "capability")
        elif delta > 5:
            floor_mat = C.floor_material(w, rec["location"])
            if C.scratch_risk(floor_mat, C.foot_material(rec), C.subtree_mass(w, eid), C.floor_grit(w, rec["location"])) >= 0.3:
                fid = C.floor_id(w, rec["location"])
                if fid:
                    C.mark_damage(w, fid, "scratch", "{} pivoted on its feet on {}".format(eid, floor_mat))
            for rug in [c for c in w.children(rec["location"]) if C.is_kind(w.ent(c), "rug") and set(geometry.footprint(w.ent(c))) & set(geometry.footprint(rec))]:
                rm = w.mut(rug)
                rm["rippled"] = True
                rm["floor_hazard"] = "trip"
        m = w.mut(eid)
        m["yaw_deg"] = round(new, 2)
        m["rot"] = new_rot
        C.set_floor_xy(w, eid, nx, ny)
        update_tuck_state(w, eid)
    else:
        m = w.mut(eid)
        m["yaw_deg"] = round(new, 2)
        if rec.get("head_end") and delta >= 150:
            m["head_end"] = "B" if rec.get("head_end") == "A" else "A"
    ctx.data["seconds"] = 2.0 + delta / 90.0 * (2.0 if C.subtree_mass(w, eid) > 10 else 1.0)
    return "Rotated {} to {:.0f} deg{}".format(eid, new, "; knocked " + ", ".join(C.id_order(ctx, knocked)) if knocked else "")


def _default_feature(rec):
    features = rec.get("features") or {}
    for name in ("handle", "front", "good_side", "label", "face"):
        if name in features:
            return name
    return "front"


@action("flip", params={"target": "entity", "axis": "?str:long|short"}, duration=lambda ctx: ctx.data.get("seconds", 4.0), energy=0.02,
        category="physical",
        doc="Turn an object over about a horizontal axis (cushion, mattress, doormat, cutting board, document, food with a spatula). "
            "Contents of open containers fall out; one-sided items (pillow-top mattress, one-sided cushion) are damaged; "
            "the swept volume knocks nearby tall items unless moving carefully.")
def flip(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    require(rec.get("portable") or rec.get("flippable") or C.is_kind(rec, "mattress", "cushion"), "It cannot be turned over")
    require(not rec.get("location") in w.s["rooms"] or C.subtree_mass(w, eid) <= C.num(w.robot.get("max_mass_kg", 20.0)) * 1.5,
            "Too heavy to turn over alone; get help", "capability")
    if rec.get("food") or C.is_kind(rec, "food"):
        hot = C.num(rec.get("temperature_c"), 21.0) > 50.0 or C.num(w.ent(rec["location"]).get("temperature_c"), 21.0) > 50.0 if rec.get("location") in w.s["entities"] else False
        if hot:
            C.tool_of(ctx, "flip") if any("flip" in (w.ent(h).get("affordances") or []) for h in w.held_items()) else C.tool_of(ctx, "spatula")
    if C.subtree_mass(w, eid) > 15.0 and rec.get("location") not in w.s["rooms"]:
        require(len(C.free_hands(w)) == 2 or ctx.holding(eid), "Both hands are needed to turn it over", "capability")
    rid = C.room_of(w, eid)
    w_, d_, h_ = C.dims(rec)
    knocked = _sweep_knock(ctx, eid, max(w_, d_) / 2.0, careful=w.robot.get("speed_mode") == "careful")
    # contents fall out of open containers
    if not C.container_closed(rec):
        height = geometry.height_m(w.s, eid)
        for child in list(w.children(eid)):
            crec = w.ent(child)
            if crec.get("relation") in ("in", "on") and not crec.get("fixed_in_place"):
                C.drop_item(w, child, max(0.1, height), rid, cause="fell_out_when_flipped")
        if C.num(rec.get("liquid_l")) > 0:
            C.spill_liquid(w, eid, C.num(rec["liquid_l"]), rid)
    m = w.mut(eid)
    m["face_up"] = "B" if rec.get("face_up", "A") == "A" else "A"
    m["flipped_at_s"] = w.s["time_s"]
    if rec.get("one_sided") or rec.get("no_flip"):
        C.mark_damage(w, eid, "flipped_one_sided", "a one-sided item was turned upside down", broken=False)
    if C.num((rec.get("soil") or {}).get("dirt") if isinstance(rec.get("soil"), dict) else 0) > 0 and C.is_kind(rec, "doormat", "mat", "rug"):
        if not w.s["rooms"].get(rid, {}).get("outdoor"):
            fid = C.floor_id(w, rid)
            if fid:
                add_soil(w, fid, "dirt", C.num(rec["soil"].get("dirt")) * 0.5)
    run_hooks("manipulation.flipped", ctx, eid)
    ctx.data["seconds"] = 3.0 + (8.0 if C.subtree_mass(w, eid) > 15 else 0.0)
    return "Flipped {} (now side {} up){}".format(eid, m["face_up"], "; knocked " + ", ".join(C.id_order(ctx, knocked)) if knocked else "")


@action("set_orientation", params={"target": "entity", "orientation": "?str:upright|upside_down|on_side|on_edge|flat|point_down",
                                   "toward": "?target", "feature": "?str", "method": "?str:pivot_base|lift_top"},
        duration=2.5, energy=0.01, category="physical",
        doc="Set an object upright, upside down, on its side/edge or flat (righting a tipped bottle, inverting a glass to drain), "
            "point_down for carrying a held knife/scissors past people, "
            "or present a held object with a feature (handle) toward a person. Inverting an open container dumps its contents; "
            "righting by lifting from the top pours out more of a leaking bottle than pivoting on its base.")
def set_orientation(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    orientation = ctx.args.get("orientation")
    toward = ctx.args.get("toward")
    if toward:
        require(ctx.holding(eid), "Hold it to present it toward someone")
        # the default feature follows what the robot can tell: a sharp hazard it perceives (C.perceived_hazards: for an
        # unidentified look-alike, any member type's), never the item's hidden instance hazards
        feature = ctx.args.get("feature") or ("handle" if rec.get("has_handle") or "sharp" in C.perceived_hazards(ctx, eid) or C.num(rec.get("temperature_c"), 21) > 50 else _default_feature(rec))
        present(w, eid, feature, toward)
        if not orientation:
            return "Presenting {} {} first toward {}".format(eid, feature, toward)
    require(orientation, "Name an orientation", "argument")
    current = rec.get("orientation") or "upright"
    rid = C.room_of(w, eid)
    m = w.mut(eid)
    if orientation in ("upside_down",) or orientation == "on_side" and current == "upright":
        if not C.container_closed(rec):
            if C.num(rec.get("liquid_l")) > 0:
                spill = C.num(rec["liquid_l"]) if orientation == "upside_down" else C.num(rec["liquid_l"]) * 0.6
                C.spill_liquid(w, eid, spill, rid, onto_surface=rec.get("location") if rec.get("location") in w.s["entities"] else None)
            if orientation == "upside_down":
                for child in list(w.children(eid)):
                    crec = w.ent(child)
                    if crec.get("relation") in ("in", "on") and not crec.get("fixed_in_place"):
                        C.drop_item(w, child, max(0.05, geometry.height_m(w.s, eid)), rid, cause="dumped_when_inverted")
        if rec.get("this_side_up") and orientation == "upside_down":
            for child in w.descendants(eid):
                crec = w.ent(child)
                wrapped = crec.get("wrapped") or {}
                if C.is_fragile(crec) and C.num(wrapped.get("padding_mm")) < 5.0:
                    C.break_item(w, child, "box_inverted_against_arrows")
    if orientation == "upright" and current in ("on_side", "upside_down") and C.num(rec.get("liquid_l")) > 0 and not C.container_closed(rec):
        if ctx.args.get("method") == "lift_top":
            C.spill_liquid(w, eid, C.num(rec["liquid_l"]) * 0.3, rid, onto_surface=rec.get("location") if rec.get("location") in w.s["entities"] else None)
    m["orientation"] = orientation
    if orientation == "upright":
        m.pop("tipped_over", None)
    return "{} is now {}".format(eid, orientation.replace("_", " "))


# ----------------------------------------------------------------------------------
# tilt and shake

@action("tilt", params={"target": "entity", "angle_deg": {"type": "number", "min": 0.0, "max": 180.0}, "hold_s": {"type": "number", "default": 2.0, "min": 0.0, "max": 600.0}},
        duration=lambda ctx: 2.0 + C.num(ctx.args.get("hold_s"), 2.0), energy=0.01, category="physical",
        doc="Tilt an object by a controlled angle: a held container (liquid spills once the free surface reaches the rim: tan(a) = 2 headroom / diameter), "
            "a wheeled bin onto its wheels (>=30 deg) before rolling, or a piece of furniture onto two legs to reach its feet "
            "(loose contents slide out above ~15 deg; it tips past atan(depth/height)). angle_deg=0 sets it back down.")
def tilt(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    rec = ctx.obj(eid, touch=True)
    angle = C.num(ctx.args["angle_deg"])
    rid = C.room_of(w, eid)
    m = w.mut(eid)
    if ctx.holding(eid):
        if C.num(rec.get("liquid_l")) > 0 and not C.container_closed(rec):
            w_, d_, h_ = C.dims(rec)
            diameter = max(0.03, min(w_, d_))
            headroom = C.liquid_headroom_m(rec)
            critical = math.degrees(math.atan2(2.0 * headroom, diameter))
            if angle > critical + 1e-6:
                frac = min(1.0, (angle - critical) / max(1.0, 90.0 - critical))
                C.spill_liquid(w, eid, C.num(rec["liquid_l"]) * frac, rid, w.robot.get("pos"))
        if angle > 60 and not C.container_closed(rec):
            for child in list(w.children(eid)):
                if w.ent(child).get("relation") in ("in", "on"):
                    C.drop_item(w, child, C.HAND_HEIGHT_M, rid, cause="slid_out_when_tilted")
        if C.num(rec.get("level_check_s")) == 0:
            m["level_checked"] = True
        return "Tilted {} to {:.0f} deg".format(eid, angle)
    require(rec.get("location") in w.s["rooms"] or rec.get("wheeled"), "Tilt furniture or bins standing on the floor, or hold the item", "precondition")
    if rec.get("wheeled") or rec.get("tilt_to_roll"):
        require(C.subtree_mass(w, eid) <= 120.0, "Too heavy to tilt; get help", "capability")
        m["tilted_on_wheels"] = angle >= 30.0
        m["tilt_deg"] = round(angle, 1)
        if angle > 70:
            tip_over(ctx, eid, "tilted past its balance point")
            return "It went over"
        return "Tilted {} {}".format(eid, "onto its wheels" if angle >= 30 else "slightly")
    if angle <= 0.5:
        m["tilt_deg"] = 0.0
        m["legs_accessible"] = False
        return "Set {} back down on all feet".format(eid)
    require(C.subtree_mass(w, eid) <= C.num(w.robot.get("max_mass_kg", 20.0)) * 2.0, "Too heavy to tilt safely; get help", "capability")
    w_, d_, h_ = C.dims(rec)
    tip_angle = math.degrees(math.atan2(min(w_, d_), max(0.1, h_)))
    if angle > 15.0:
        for child in list(w.children(eid)):
            crec = w.ent(child)
            if crec.get("relation") in ("in", "on") and not crec.get("fixed_in_place"):
                C.drop_item(w, child, max(0.2, geometry.height_m(w.s, child)), rid, cause="slid_off_tilted_furniture")
    if angle > tip_angle + 1e-6:
        tip_over(ctx, eid, "tilted {:.0f} deg past its {:.0f} deg tipping angle".format(angle, tip_angle))
        return "It tipped over"
    m["tilt_deg"] = round(angle, 1)
    m["legs_accessible"] = math.sin(math.radians(angle)) * min(w_, d_) >= 0.02
    return "Tilted {} {:.0f} deg; its raised feet are accessible".format(eid, angle)


@action("shake", params={"target": "entity", "seconds": {"type": "number", "default": 5.0, "min": 1.0, "max": 120.0}},
        duration=lambda ctx: C.num(ctx.args.get("seconds"), 5.0) + 1.0, energy=0.01, noise=0.1, category="physical",
        doc="Shake a held object: mix a 'shake well' bottle (cap must be tight), shake debris out of a doormat, rug or cover "
            "(do it outdoors), or check contents. Shaking an open/loose container sprays it; shaking a carbonated drink builds pressure.")
def shake(ctx):
    w = ctx.w
    eid = ctx.args["target"]
    C.held_or_fail(ctx, eid, "Hold it to shake it")
    rec = w.ent(eid)
    rid = w.robot["room"]
    seconds = C.num(ctx.args.get("seconds"), 5.0)
    m = w.mut(eid)
    notes = []
    if C.num(rec.get("liquid_l")) > 0 and not C.container_closed(rec):
        C.spill_liquid(w, eid, C.num(rec["liquid_l"]) * 0.5, rid, w.robot.get("pos"))
        notes.append("liquid sprayed out")
    if C.is_kind(rec, "deck", "card_deck", "playing_cards", "cards"):
        m["shuffled"] = seconds >= 4.0
        m["order"] = "random" if seconds >= 4.0 else rec.get("order", "sorted")
        notes.append("shuffled" if seconds >= 4.0 else "barely mixed")
    real = C.realistic(ctx)
    see_through = rec.get("transparent") or C.material(rec) in ("glass", "clear_plastic", "acrylic")
    if rec.get("separated") and C.container_closed(rec):
        mixed = C.num(rec.get("mixed_s")) + seconds
        m["mixed_s"] = mixed
        if mixed >= C.num(rec.get("mix_needed_s"), 8.0):
            m["separated"] = False
            m["emulsified"] = True
            if not real or see_through:
                notes.append("now mixed")        # visible only through a clear bottle
    if rec.get("carbonated"):
        m["agitation"] = round(min(1.0, C.num(rec.get("agitation")) + seconds / 10.0), 2)
        if not real:
            notes.append("fizz builds up")       # pressure inside a closed can is not something the robot can see
    soil = rec.get("soil") if isinstance(rec.get("soil"), dict) else {}
    loose = {k: v for k, v in sorted(soil.items()) if k in ("dust", "dirt", "crumbs", "sand", "leaves", "hair", "pet_hair", "debris", "paint_chips", "mud")}
    if loose:
        outdoor = w.s["rooms"].get(rid, {}).get("outdoor")
        new = dict(soil)
        for kind, amount in loose.items():
            new[kind] = round(max(0.0, C.num(amount) * 0.2), 4)
            if not outdoor:
                fid = C.floor_id(w, rid)
                if fid:
                    add_soil(w, fid, kind if kind in ("dust", "dirt", "crumbs", "sand", "hair", "pet_hair", "leaves", "mud") else "dirt", C.num(amount) * 0.6)
        m["soil"] = new
        notes.append("debris shaken out " + ("outdoors" if outdoor else "onto the floor here"))
    for child in list(w.children(eid)):
        crec = w.ent(child)
        if C.is_fragile(crec) and crec.get("relation") == "in" and not (crec.get("wrapped") or {}).get("coverage"):
            C.break_item(w, child, "shaken")
    now = w.ent(eid)
    if real and C.container_closed(now):
        # checking contents by feel: a closed container rattles, sloshes or feels empty (coarse, by touch)
        inside = [c for c in w.children(eid) if w.ent(c).get("relation") == "in"]
        if C.num(now.get("liquid_l")) > 0 or C.num(now.get("fill_level")) > 0.03:
            notes.append("liquid sloshes inside")
        elif any(w.ent(c).get("broken") for c in inside):
            notes.append("broken pieces clink inside")
        elif inside:
            notes.append("something shifts inside")
        elif now.get("container") or now.get("openable"):
            notes.append("it feels empty")
    if real:
        C.grant_heft(ctx, eid)       # shaking it in the hand tells how heavy it is, as lifting does
    return "Shook {}{}".format(eid, ": " + "; ".join(notes) if notes else "")
