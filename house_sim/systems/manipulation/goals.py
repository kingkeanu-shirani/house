"""Geometric layout goals: data-only predicate plugins for arrangement tasks.

A task's setup declares goals in state['manipulation']['goals'][name] (scenario helper
manipulation.goal). The system recomputes every goal from the real poses after each
simulated substep into state['manipulation']['results'][name] (never observed by the
agent). Success predicates then read e.g. {"path": "manipulation.results.row.ok",
"op": "is_true", "value": true}. No action can write these results.

Kinds: poses, row, grid, spacing, settings, free_area, centered, facing, zone_clear,
walkway, stack_order, access, straight, same_as, same_fields, pairs, stack_rules, process (step order from
state effects: the step at which each per-item condition first held), aesthetic
(edge clearance, gaps, balance, tall-at-back, facing the viewer, odd groups).
"""
import math

from ...kernel import geometry
from ...kernel.plugins import system
from . import common as C


def _center(w, eid):
    pose = C.world_pose(w, eid)
    return pose


def _local_center(w, eid, surface):
    pose = C.world_pose(w, eid)
    if pose is None:
        return None
    return C.surface_local(w, surface, pose[1], pose[2])


# ----------------------------------------------------------------------------------

def g_poses(w, g):
    tol = C.num(g.get("pos_tol_m"), 0.05)
    ytol = C.num(g.get("yaw_tol_deg"), 5.0)
    out, count = {}, 0
    for eid, want in sorted((g.get("items") or {}).items()):
        if eid not in w.s["entities"]:
            out[eid] = {"ok": False, "missing": True}
            continue
        rec = w.ent(eid)
        ok = not rec.get("broken")
        info = {}
        if "location" in want and rec.get("location") != want["location"]:
            ok = False
            info["location"] = rec.get("location")
        if "relation" in want and rec.get("relation") != want["relation"]:
            ok = False
        err = 0.0
        if "cell" in want or "xy_m" in want:
            if rec.get("location") in w.s["rooms"]:
                x, y = C.floor_xy(rec)
                tx, ty = (want["xy_m"] if "xy_m" in want else [want["cell"][0] * C.CELL, want["cell"][1] * C.CELL])
                err = math.hypot(x - C.num(tx), y - C.num(ty))
            else:
                err = 99.0
        elif "spos" in want:
            if isinstance(rec.get("spos"), list):
                err = math.hypot(C.num(rec["spos"][0]) - C.num(want["spos"][0]), C.num(rec["spos"][1]) - C.num(want["spos"][1]))
            else:
                err = 99.0
        if "shelf" in want and rec.get("shelf") != want["shelf"]:
            ok = False
        info["pos_err_m"] = round(err, 3)
        if err > tol + 1e-9:
            ok = False
        if "yaw_deg" in want:
            period = C.num(want.get("yaw_period"), 360.0)
            d = (C.yaw_of(rec) - C.num(want["yaw_deg"])) % period
            d = min(d, period - d)
            info["yaw_err_deg"] = round(d, 1)
            if d > ytol + 1e-9:
                ok = False
        for field, value in sorted((want.get("fields") or {}).items()):
            if rec.get(field) != value:
                ok = False
                info.setdefault("fields_off", []).append(field)
        info["ok"] = ok
        count += 1 if ok else 0
        out[eid] = info
    need = int(g.get("min_ok", len(out)))
    return {"ok": count >= need and bool(out), "count_ok": count, "items": out}


def _axis_fit(points):
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    syy = sum((p[1] - my) ** 2 for p in points)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return mx, my, math.cos(angle), math.sin(angle)


def _half_extent(rec, yaw_world, ux, uy):
    w_, d_, _ = C.dims(rec)
    t = math.radians(yaw_world)
    ax, ay, bx, by = math.cos(t), math.sin(t), -math.sin(t), math.cos(t)
    return abs(ux * ax + uy * ay) * w_ / 2.0 + abs(ux * bx + uy * by) * d_ / 2.0


def g_row(w, g):
    items = [e for e in g.get("items", []) if e in w.s["entities"]]
    if len(items) < 2 or len(items) != len(g.get("items", [])):
        return {"ok": False, "reason": "missing items"}
    poses = {e: _center(w, e) for e in items}
    rooms = {p[0] for p in poses.values() if p}
    if len(rooms) != 1 or any(p is None for p in poses.values()):
        return {"ok": False, "reason": "not in one room"}
    if g.get("same_parent", True) and len({w.ent(e).get("location") for e in items}) != 1:
        return {"ok": False, "reason": "not on the same support"}
    pts = [(poses[e][1], poses[e][2]) for e in items]
    mx, my, ux, uy = _axis_fit(pts)
    devs = [abs(-(p[0] - mx) * uy + (p[1] - my) * ux) for p in pts]
    proj = sorted(((p[0] - mx) * ux + (p[1] - my) * uy, e) for p, e in zip(pts, items))
    gaps = []
    for (a, ea), (b, eb) in zip(proj, proj[1:]):
        ga = _half_extent(w.ent(ea), poses[ea][3], ux, uy)
        gb = _half_extent(w.ent(eb), poses[eb][3], ux, uy)
        gaps.append(b - a - ga - gb)
    mean = sum(gaps) / len(gaps)
    std = math.sqrt(sum((x - mean) ** 2 for x in gaps) / len(gaps))
    res = {"max_dev_m": round(max(devs), 3), "gap_std_m": round(std, 3), "min_gap_m": round(min(gaps), 3), "order": [e for _, e in proj]}
    ok = max(devs) <= C.num(g.get("max_dev_m"), 0.03) + 1e-9 and std <= C.num(g.get("max_gap_std_m"), 0.03) + 1e-9 and min(gaps) >= -0.005
    if g.get("min_gap_m") is not None and min(gaps) < C.num(g["min_gap_m"]) - 1e-9:
        ok = False
    if g.get("order"):
        order = [e for _, e in proj]
        if order != list(g["order"]) and order[::-1] != list(g["order"]):
            ok = False
            res["order_ok"] = False
    facing = g.get("facing")
    if facing:
        bad = []
        for e in items:
            if not _faces(w, e, facing):
                bad.append(e)
        res["facing_wrong"] = bad
        if bad:
            ok = False
    if any(w.ent(e).get("broken") for e in items):
        ok = False
    res["ok"] = ok
    return res


def _faces(w, eid, facing):
    feature = facing.get("feature", "front")
    direction = C.feature_dir(w, eid, feature)
    if direction is None:
        return False
    tol = C.num(facing.get("tol_deg"), 30.0)
    if "deg" in facing:
        return C.ang_diff(direction, C.num(facing["deg"])) <= tol
    toward = facing.get("toward")
    if toward is None:
        return False
    tp = _center(w, toward)
    p = _center(w, eid)
    if tp is None or p is None:
        return False
    want = C.bearing(p[1], p[2], tp[1], tp[2])
    if facing.get("away"):
        want = (want + 180.0) % 360.0
    return C.ang_diff(direction, want) <= tol


def g_grid(w, g):
    items = [e for e in g.get("items", []) if e in w.s["entities"]]
    rows, cols = int(g.get("rows", 1)), int(g.get("cols", len(items)))
    surface = g.get("within")
    if len(items) != rows * cols or not surface or surface not in w.s["entities"]:
        return {"ok": False, "reason": "wrong item count or no boundary"}
    sw, sd = C.surface_wd(w.ent(surface))
    pts = []
    for e in items:
        rec = w.ent(e)
        if not w.within(e, surface) or rec.get("broken"):
            return {"ok": False, "reason": "{} outside the boundary".format(e)}
        local = _local_center(w, e, surface)
        if local is None or local[0] < 0 or local[1] < 0 or local[0] > sw or local[1] > sd:
            return {"ok": False, "reason": "{} outside the boundary".format(e)}
        pts.append((local[0], local[1], e))
    pts.sort(key=lambda p: (p[1], p[0]))
    tol = C.num(g.get("tol_m"), 0.03)
    grid = [sorted(pts[r * cols:(r + 1) * cols]) for r in range(rows)]
    row_dev = max(max(p[1] for p in row) - min(p[1] for p in row) for row in grid)
    col_dev = max(max(grid[r][c][0] for r in range(rows)) - min(grid[r][c][0] for r in range(rows)) for c in range(cols))
    overlaps = 0
    for i, a in enumerate(items):
        ra = w.ent(a)
        for b in items[i + 1:]:
            rb = w.ent(b)
            if ra.get("location") == rb.get("location") and isinstance(ra.get("spos"), list) and isinstance(rb.get("spos"), list):
                aw, ad = C.footprint_wd(ra)
                bw, bd = C.footprint_wd(rb)
                if C.rects_overlap((ra["spos"][0], ra["spos"][1], ra["spos"][0] + aw, ra["spos"][1] + ad),
                                   (rb["spos"][0], rb["spos"][1], rb["spos"][0] + bw, rb["spos"][1] + bd)):
                    overlaps += 1
    ok = row_dev <= tol * 2 and col_dev <= tol * 2 and overlaps == 0
    return {"ok": ok, "row_dev_m": round(row_dev, 3), "col_dev_m": round(col_dev, 3), "overlaps": overlaps}


def g_spacing(w, g):
    items = [e for e in g.get("items", []) if e in w.s["entities"]]
    surface = g.get("surface")
    if len(items) < 2 or not surface or surface not in w.s["entities"]:
        return {"ok": False}
    axis = 0 if g.get("axis", "x") == "x" else 1
    sw, sd = C.surface_wd(w.ent(surface))
    lo, hi = (g.get("span") or [0.0, sw if axis == 0 else sd])
    spans = []
    for e in items:
        rec = w.ent(e)
        if rec.get("location") != surface or not isinstance(rec.get("spos"), list):
            return {"ok": False, "reason": "{} not on {}".format(e, surface)}
        size = C.footprint_wd(rec)[axis]
        start = C.num(rec["spos"][axis])
        spans.append((start, start + size, e))
    spans.sort()
    gaps = [b[0] - a[1] for a, b in zip(spans, spans[1:])]
    if g.get("ends") == "equal":
        gaps = [spans[0][0] - C.num(lo)] + gaps + [C.num(hi) - spans[-1][1]]
    mean = sum(gaps) / len(gaps)
    std = math.sqrt(sum((x - mean) ** 2 for x in gaps) / len(gaps))
    ok = std <= C.num(g.get("max_gap_std_m"), 0.01) + 1e-9 and min(gaps) >= -1e-6 and spans[0][0] >= C.num(lo) - 1e-6 and spans[-1][1] <= C.num(hi) + 1e-6
    return {"ok": ok, "gap_std_m": round(std, 4), "gaps_m": [round(x, 3) for x in gaps]}


# ----------------------------------------------------------------------------------
# place settings

TEMPLATES = {
    "casual": ["plate", "fork", "knife", "spoon", "glass", "napkin"],
    "dinner": ["plate", "fork", "knife", "glass", "napkin"],
    "formal": ["plate", "fork", "salad_fork", "knife", "spoon", "glass", "napkin", "bread_plate"],
    "breakfast": ["bowl", "spoon", "cup", "napkin"],
    "soup": ["plate", "bowl", "spoon", "fork", "knife", "glass", "napkin"],
}
CHILD_FORBIDDEN = ("knife", "steak_knife", "wine_glass")
CHILD_SWAP = {"plate": "child_plate", "glass": "child_cup", "cup": "child_cup", "fork": "child_fork", "spoon": "child_spoon", "knife": None, "bowl": "child_bowl"}


def seat_frame(w, table, chair):
    """Anchor on the table edge facing the chair, plus (forward, right) unit vectors."""
    trec = w.ent(table)
    tx0, ty0, tx1, ty1 = C.floor_rect(trec)
    crec = w.ent(chair)
    cx0, cy0, cx1, cy1 = C.floor_rect(crec)
    cx, cy = (cx0 + cx1) / 2.0, (cy0 + cy1) / 2.0
    gaps = {"north": ty0 - cy, "south": cy - ty1, "west": tx0 - cx, "east": cx - tx1}
    side = max(gaps, key=lambda k: gaps[k])
    if side == "north":
        anchor, f = (min(max(cx, tx0), tx1), ty0), (0.0, 1.0)
    elif side == "south":
        anchor, f = (min(max(cx, tx0), tx1), ty1), (0.0, -1.0)
    elif side == "west":
        anchor, f = (tx0, min(max(cy, ty0), ty1)), (1.0, 0.0)
    else:
        anchor, f = (tx1, min(max(cy, ty0), ty1)), (-1.0, 0.0)
    r = (-f[1], f[0])
    return anchor, f, r


def _role(rec):
    return rec.get("setting_role")


def g_settings(w, g):
    table = g.get("table")
    if table not in w.s["entities"]:
        return {"ok": False, "reason": "no table"}
    template = list(g.get("require") or TEMPLATES.get(g.get("template", "casual"), TEMPLATES["casual"]))
    ents = w.s["entities"]
    roled = sorted(e for e, rec in ents.items() if _role(rec) and w.within(e, table))
    items = [e for e in roled if not w.ent(e).get("broken")]
    broken = [e for e in roled if w.ent(e).get("broken")]
    locals_ = {}
    for e in items:
        pose = C.world_pose(w, e)
        locals_[e] = pose
    seats_out = {}
    claimed = set()
    all_ok = not broken
    for seat in g.get("seats", []):
        sid = seat.get("id") or seat.get("chair")
        chair = seat.get("chair")
        if chair not in w.s["entities"]:
            seats_out[sid] = {"ok": False, "reason": "no chair"}
            all_ok = False
            continue
        anchor, f, r = seat_frame(w, table, chair)
        zone = {}
        for e, pose in sorted(locals_.items()):
            dx, dy = pose[1] - anchor[0], pose[2] - anchor[1]
            lx, ly = dx * r[0] + dy * r[1], dx * f[0] + dy * f[1]
            if -0.42 <= lx <= 0.42 and -0.05 <= ly <= 0.48:
                zone[e] = (lx, ly)
        info = {"items": sorted(zone)}
        if not seat.get("diner", True):
            ok = not zone
            info["ok"] = ok
            all_ok = all_ok and ok
            seats_out[sid] = info
            continue
        child = bool(seat.get("child"))
        need = []
        for role in template:
            if child:
                swap = CHILD_SWAP.get(role, role)
                if swap is None:
                    continue
                need.append(swap)
            else:
                need.append(role)
        by_role = {}
        for e, (lx, ly) in sorted(zone.items()):
            by_role.setdefault(_role(w.ent(e)), []).append((e, lx, ly))
        problems = []
        anchor_role = "child_plate" if child and "child_plate" in need else ("plate" if "plate" in need else ("bowl" if "bowl" in need else need[0]))
        base = by_role.get(anchor_role) or []
        if len(base) != 1:
            problems.append("needs exactly one " + anchor_role)
            px, py = 0.0, 0.18
        else:
            px, py = base[0][1], base[0][2]
            if abs(px) > 0.12 or not (0.06 <= py <= 0.32):
                problems.append(anchor_role + " not centred at the seat")
        for role in need:
            if role == anchor_role:
                continue
            got = by_role.get(role) or []
            if not got:
                problems.append("missing " + role)
                continue
            e, lx, ly = got[0]
            rx, ry = lx - px, ly - py
            if role in ("fork", "child_fork", "salad_fork"):
                good = -0.34 <= rx <= -0.08 and abs(ry) <= 0.14
            elif role in ("knife",):
                good = 0.08 <= rx <= 0.32 and abs(ry) <= 0.14
                direction = C.feature_dir(w, e, "blade_edge")
                if good and direction is not None:
                    pp = C.world_pose(w, base[0][0]) if base else None
                    kp = C.world_pose(w, e)
                    if pp is not None and C.ang_diff(direction, C.bearing(kp[1], kp[2], pp[1], pp[2])) > 75.0:
                        good = False
                        problems.append("knife blade faces away from the plate")
            elif role in ("spoon", "child_spoon"):
                knife = (by_role.get("knife") or [None])[0]
                good = 0.08 <= rx <= 0.42 and abs(ry) <= 0.14 and (knife is None or lx > knife[1] - 0.005)
            elif role in ("glass", "child_cup", "cup", "wine_glass"):
                good = 0.02 <= rx <= 0.42 and 0.06 <= ry <= 0.40
            elif role == "napkin":
                fork = (by_role.get("fork") or by_role.get("child_fork") or [None])[0]
                on_plate = w.ent(e).get("location") in [b[0] for b in base]
                good = on_plate or (rx <= -0.08 and (fork is None or lx < fork[1] + 0.005))
            elif role == "bread_plate":
                good = rx <= -0.05 and ry >= 0.1
            elif role in ("bowl", "child_bowl"):
                good = abs(rx) <= 0.12 and abs(ry) <= 0.15
            else:
                good = abs(rx) <= 0.45
            if not good and not any(p.startswith(role) or role in p for p in problems):
                problems.append(role + " misplaced")
        for role, entries in sorted(by_role.items()):
            if role not in need and role != anchor_role:
                problems.append("unexpected " + role)
            if len(entries) > 1 and role in need:
                problems.append("duplicate " + role)
        if child:
            for e in zone:
                rec = w.ent(e)
                if _role(rec) in CHILD_FORBIDDEN or ("sharp" in (rec.get("hazards") or [])) or C.is_fragile(rec) and _role(rec) in ("child_plate", "child_cup", "child_bowl"):
                    problems.append("unsafe item at a child seat: " + e)
        claimed.update(zone)
        ok = not problems
        info.update(ok=ok, problems=problems)
        all_ok = all_ok and ok
        seats_out[sid] = info
    stray = [e for e in items if e not in claimed]
    if stray and g.get("no_strays", True):
        all_ok = False
    return {"ok": all_ok, "seats": seats_out, "stray": stray, "broken": broken}


# ----------------------------------------------------------------------------------

def g_free_area(w, g):
    need_w, need_d = C.num(g.get("w"), 0.5), C.num(g.get("d"), 0.4)
    surface = g.get("surface")
    if surface in w.s["entities"]:
        sw, sd = C.surface_wd(w.ent(surface))
        rects = []
        for e in geometry.surface_items(w.s, surface):
            rec = w.ent(e)
            if isinstance(rec.get("spos"), list):
                iw, idp = C.footprint_wd(rec)
                rects.append((rec["spos"][0], rec["spos"][1], rec["spos"][0] + iw, rec["spos"][1] + idp))
        step = 0.02
        y = 0.0
        while y + need_d <= sd + 1e-9:
            x = 0.0
            while x + need_w <= sw + 1e-9:
                box = (x, y, x + need_w, y + need_d)
                if not any(C.rects_overlap(box, r) for r in rects):
                    return {"ok": True, "spot": [round(x, 2), round(y, 2)]}
                x += step
            y += step
        return {"ok": False}
    room = g.get("room")
    if room in w.s["rooms"]:
        cols, rows = geometry.grid_size(w.s["rooms"][room])
        used = set(geometry.blocked_cells(w.s, room))
        for e in w.children(room):
            rec = w.ent(e)
            if not rec.get("ground_layer") and "connects" not in rec and not rec.get("mounted"):
                used.update(geometry.footprint(rec))
        nc, nr = int(math.ceil(need_w / C.CELL - 1e-9)), int(math.ceil(need_d / C.CELL - 1e-9))
        for r0 in range(rows - nr + 1):
            for c0 in range(cols - nc + 1):
                if all((c, r) not in used for c in range(c0, c0 + nc) for r in range(r0, r0 + nr)):
                    return {"ok": True, "cell": [c0, r0]}
        return {"ok": False}
    return {"ok": False}


def g_centered(w, g):
    eid = g.get("item")
    if eid not in w.s["entities"]:
        return {"ok": False}
    rec = w.ent(eid)
    surface = rec.get("location")
    ref = g.get("ref", "surface")
    tol = C.num(g.get("tol_m"), 0.02)
    pose = C.world_pose(w, eid)
    if ref == "surface":
        if surface not in w.s["entities"]:
            return {"ok": False}
        sp = C.world_pose(w, surface)
        tx, ty = sp[1], sp[2]
    else:
        rp = C.world_pose(w, ref)
        if rp is None:
            return {"ok": False}
        tx, ty = rp[1], rp[2]
    err = math.hypot(pose[1] - tx, pose[2] - ty)
    if g.get("location") and surface != g["location"]:
        return {"ok": False, "err_m": round(err, 3)}
    return {"ok": err <= tol + 1e-9 and not rec.get("broken"), "err_m": round(err, 3)}


def g_facing(w, g):
    bad = [e for e in g.get("items", []) if e not in w.s["entities"] or not _faces(w, e, g)]
    return {"ok": not bad and bool(g.get("items")), "wrong": bad}


def g_zone_clear(w, g):
    room = g.get("room")
    if room not in w.s["rooms"]:
        return {"ok": False}
    x0, y0, x1, y1 = [C.num(v) for v in g.get("rect_m", [0, 0, 0, 0])]
    zone = (x0, y0, x1, y1)
    allowed = set(g.get("except") or [])
    inside = []
    for e in w.children(room):
        rec = w.ent(e)
        if e in allowed or rec.get("ground_layer") or "connects" in rec or rec.get("mounted") or C.is_kind(rec, "floor_surface", "wall_surface", "light", "switch", "window", "robot_dock"):
            continue
        if not (rec.get("portable") or rec.get("movable", False) and C.is_kind(rec, "furniture")):
            continue
        if C.rects_overlap(C.floor_rect(rec), zone, margin=0.01):
            inside.append(e)
    return {"ok": not inside, "inside": inside}


def g_walkway(w, g):
    room = g.get("room")
    if room not in w.s["rooms"]:
        return {"ok": False}
    cells = [tuple(c["cells"][room]) for cid, c in sorted(w.s["connections"].items()) if room in c.get("cells", {})]
    targets = [tuple(c) for c in g.get("cells", [])] or cells
    if len(targets) < 2:
        return {"ok": True}
    floor_items = []
    for e in w.children(room):
        rec = w.ent(e)
        if not rec.get("ground_layer") and not geometry.blocks(rec) and rec.get("pos") and "connects" not in rec and not rec.get("mounted"):
            floor_items.extend(geometry.footprint(rec))
    blocked_targets = []
    start = targets[0]
    for goal in targets[1:]:
        path = geometry.find_path(w.s, room, start, [goal], extra_blocked=floor_items, ignore=tuple(w.s.get("people", {})) + tuple(w.s.get("pets", {})))
        if path is None:
            blocked_targets.append(list(goal))
    return {"ok": not blocked_targets, "blocked": blocked_targets}


def g_stack_order(w, g):
    items = g.get("items") or []
    if not items or any(e not in w.s["entities"] for e in items):
        return {"ok": False}
    ok = True
    base = w.ent(items[0])
    if g.get("base_location") and base.get("location") != g["base_location"]:
        ok = False
    for lower, upper in zip(items, items[1:]):
        rec = w.ent(upper)
        if rec.get("location") != lower or rec.get("relation") != "on":
            ok = False
    if any(w.ent(e).get("broken") for e in items):
        ok = False
    return {"ok": ok}


def g_access(w, g):
    container = g.get("container")
    if container not in w.s["entities"]:
        return {"ok": False}
    from .stacking import contents, items_above
    inside = contents(w, container)
    out, ok = {}, True
    for eid, limit in sorted((g.get("items") or {}).items()):
        if eid not in inside:
            out[eid] = None
            ok = False
            continue
        depth = len(items_above(w, container, eid))
        out[eid] = depth
        if depth > int(limit):
            ok = False
    return {"ok": ok, "depth": out}


def g_straight(w, g):
    eid = g.get("item")
    if eid not in w.s["entities"]:
        return {"ok": False}
    rec = w.ent(eid)
    tol = C.num(g.get("tol_deg"), 2.0)
    ref = g.get("ref", "gravity")
    if ref == "gravity":
        err = abs(C.num(rec.get("tilt_deg")))
    else:
        pose = C.world_pose(w, eid)
        rp = C.world_pose(w, ref) if ref != "surface" else C.world_pose(w, rec.get("location"))
        if pose is None or rp is None:
            return {"ok": False}
        d = (pose[3] - rp[3]) % 180.0
        err = min(d, 180.0 - d)
        if g.get("perpendicular"):
            err = abs(90.0 - err)
    return {"ok": err <= tol + 1e-9, "err_deg": round(err, 2)}


def g_same_as(w, g):
    exemplar = g.get("exemplar") or []
    anchor_role = g.get("anchor_role", "plate")
    tol = C.num(g.get("tol_m"), 0.02)

    def offsets(group):
        anchor = next((e for e in group if e in w.s["entities"] and w.ent(e).get("setting_role") == anchor_role), None)
        if anchor is None:
            return None
        ap = C.world_pose(w, anchor)
        out = {}
        for e in group:
            if e == anchor or e not in w.s["entities"]:
                continue
            p = C.world_pose(w, e)
            if p is None or p[0] != ap[0]:
                return None
            du, dv = C.rotate_vec(p[1] - ap[1], p[2] - ap[2], -ap[3])
            out[w.ent(e).get("setting_role") or e] = (du, dv, (p[3] - ap[3]) % 360.0)
        return out
    ref = offsets(exemplar)
    if ref is None:
        return {"ok": False, "reason": "exemplar incomplete"}
    groups_out, ok = [], True
    for group in g.get("groups") or []:
        got = offsets(group)
        good = got is not None and set(got) == set(ref)
        if good:
            for role, (du, dv, yaw) in ref.items():
                gu, gv, gy = got[role]
                if math.hypot(gu - du, gv - dv) > tol or C.ang_diff(gy, yaw) > C.num(g.get("tol_deg"), 15.0):
                    good = False
        groups_out.append(good)
        ok = ok and good
    return {"ok": ok and bool(groups_out), "groups": groups_out}


def g_stack_rules(w, g):
    """All items in ONE stack (any order the rules allow): larger/heavier below, nothing broken."""
    items = [e for e in g.get("items", []) if e in w.s["entities"]]
    if len(items) != len(g.get("items", [])) or not items:
        return {"ok": False, "reason": "missing items"}
    from .stacking import stack_members
    members = stack_members(w, items[0])
    if sorted(members) != sorted(items) and not set(items) <= set(members):
        return {"ok": False, "reason": "not one stack", "members": members}
    chain = [m for m in members if m in items]
    ok = True
    problems = []
    for lower, upper in zip(chain, chain[1:]):
        lrec, urec = w.ent(lower), w.ent(upper)
        if g.get("larger_below", True) and max(C.dims(urec)[:2]) > max(C.dims(lrec)[:2]) + 0.005:
            ok = False
            problems.append("{} is larger than {} below it".format(upper, lower))
        if g.get("heavier_below") and C.num(urec.get("mass_kg")) > C.num(lrec.get("mass_kg")) + 0.05:
            ok = False
            problems.append("{} is heavier than {} below it".format(upper, lower))
    base = w.ent(members[0])
    if g.get("base_location") and base.get("location") != g["base_location"]:
        ok = False
        problems.append("stack is not on " + g["base_location"])
    if any(w.ent(e).get("broken") or w.ent(e).get("chipped") for e in items):
        ok = False
        problems.append("something broke")
    return {"ok": ok, "order": chain, "problems": problems}


_OPS = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "gt": lambda a, b: a is not None and a > b,
        "gte": lambda a, b: a is not None and a >= b, "lt": lambda a, b: a is not None and a < b,
        "lte": lambda a, b: a is not None and a <= b, "exists": lambda a, b: a is not None, "truthy": lambda a, b: bool(a),
        "in": lambda a, b: a in (b or [])}


def _field(rec, path):
    cur = rec
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def g_process(w, g, prev):
    """Per item, an ordered list of conditions {"field", "op", "value"} on the item (or {"entity"} to
    test another one). Records the step at which each first held; ok when every condition has held
    and in the listed order (same step allowed unless strict)."""
    steps_now = w.s.get("steps", 0)
    first_prev = (prev or {}).get("first") or {}
    first, items_out, ok = {}, {}, True
    for eid, conds in sorted((g.get("items") or {}).items()):
        rec_first = list(first_prev.get(eid) or [None] * len(conds))
        rec_first += [None] * (len(conds) - len(rec_first))
        for i, cond in enumerate(conds):
            if rec_first[i] is not None:
                continue
            target = cond.get("entity", eid)
            if target not in w.s["entities"]:
                continue
            fn = _OPS.get(cond.get("op", "eq"))
            try:
                held = fn is not None and fn(_field(w.ent(target), cond.get("field", "")), cond.get("value"))
            except TypeError:
                held = False
            if held:
                rec_first[i] = steps_now
        first[eid] = rec_first
        done = all(s is not None for s in rec_first)
        ordered = done and all((b > a) if g.get("strict") else (b >= a) for a, b in zip(rec_first, rec_first[1:]))
        broken = eid in w.s["entities"] and w.ent(eid).get("broken")
        items_out[eid] = {"done": done, "ordered": ordered, "ok": ordered and not broken}
        ok = ok and items_out[eid]["ok"]
    return {"ok": ok and bool(items_out), "items": items_out, "first": first}


def g_aesthetic(w, g):
    """A deterministic 'arrange these nicely' rubric over items on one surface."""
    surface = g.get("surface")
    items = [e for e in g.get("items", []) if e in w.s["entities"]]
    if surface not in w.s["entities"] or len(items) != len(g.get("items", [])) or not items:
        return {"ok": False, "reason": "missing items or surface"}
    srec = w.ent(surface)
    sw, sd = C.surface_wd(srec)
    edge = C.num(g.get("edge_clear_m"), 0.03)
    gap = C.num(g.get("min_gap_m"), 0.02)
    rects, problems = {}, []
    for e in items:
        rec = w.ent(e)
        if rec.get("location") != surface or rec.get("relation", "on") != "on" or not isinstance(rec.get("spos"), list):
            problems.append(e + " is not on the surface")
            continue
        iw, idp = C.footprint_wd(rec)
        x, y = C.num(rec["spos"][0]), C.num(rec["spos"][1])
        rects[e] = (x, y, x + iw, y + idp)
        if x < edge - 1e-9 or y < edge - 1e-9 or x + iw > sw - edge + 1e-9 or y + idp > sd - edge + 1e-9:
            problems.append(e + " is too close to the edge")
        if rec.get("broken") or rec.get("chipped") or rec.get("orientation") in ("on_side", "upside_down"):
            problems.append(e + " is damaged or knocked over")
    names = sorted(rects)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ra, rb = rects[a], rects[b]
            dx = max(rb[0] - ra[2], ra[0] - rb[2])
            dy = max(rb[1] - ra[3], ra[1] - rb[3])
            if max(dx, dy) < gap - 1e-9:
                problems.append("{} and {} are crowded".format(a, b))
    score_parts = {}
    if rects:
        area = {e: (r[2] - r[0]) * (r[3] - r[1]) for e, r in rects.items()}
        total = sum(area.values()) or 1.0
        cx = sum(((r[0] + r[2]) / 2.0) * area[e] for e, r in rects.items()) / total
        balance = abs(cx - sw / 2.0) / max(0.01, sw / 2.0)
        score_parts["balance"] = round(1.0 - min(1.0, balance), 3)
        if balance > C.num(g.get("balance_tol"), 0.25):
            problems.append("lopsided: the arrangement's weight sits off to one side")
        front_low = g.get("viewer_side", "front") == "front"
        inversions = 0
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ha, hb = C.dims(w.ent(a))[2], C.dims(w.ent(b))[2]
                if abs(ha - hb) < 0.05:
                    continue
                tall, short = (a, b) if ha > hb else (b, a)
                ty = (rects[tall][1] + rects[tall][3]) / 2.0
                sy = (rects[short][1] + rects[short][3]) / 2.0
                overlap_x = min(rects[tall][2], rects[short][2]) - max(rects[tall][0], rects[short][0])
                if overlap_x > 0 and ((ty < sy) if front_low else (ty > sy)):
                    inversions += 1
        score_parts["tall_back"] = 1.0 if inversions == 0 else 0.0
        if inversions and g.get("tall_back", True):
            problems.append("a taller piece hides a shorter one behind it")
    viewer = g.get("viewer")
    if viewer:
        away = [e for e in items if (w.ent(e).get("features") or {}).get("front") is not None
                and not _faces(w, e, {"toward": viewer, "feature": "front", "tol_deg": C.num(g.get("tol_deg"), 30.0)})]
        if away:
            problems.append("not facing the viewer: " + ", ".join(away))
    if g.get("odd_groups") and len(items) % 2 == 0 and len(items) <= 6:
        problems.append("an even group of {} reads less natural than 3 or 5".format(len(items)))
    return {"ok": not problems, "problems": problems, "score": score_parts}


def g_same_fields(w, g):
    """'Do the rest the same way': every item matches the exemplar on the listed fields (dotted paths;
    numbers within num_tol), while incidental fields (position) may differ."""
    ex = g.get("exemplar")
    if ex not in w.s["entities"]:
        return {"ok": False, "reason": "exemplar missing"}
    erec = w.ent(ex)
    tol = C.num(g.get("num_tol"), 0.01)
    out, ok = {}, True
    for eid in g.get("items") or []:
        if eid not in w.s["entities"]:
            out[eid] = {"ok": False, "missing": True}
            ok = False
            continue
        rec = w.ent(eid)
        bad = []
        for path in g.get("fields") or []:
            a, b = _field(erec, path), _field(rec, path)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
                same = abs(float(a) - float(b)) <= tol + 1e-9
            else:
                same = a == b and a is not None
            if not same:
                bad.append(path)
        good = not bad and not rec.get("broken")
        out[eid] = {"ok": good, "differs": bad}
        ok = ok and good
    return {"ok": ok and bool(out), "items": out}


def g_pairs(w, g):
    """Pairs kept together (shoes, earrings, gloves): each pair adjacent (centres within gap_m + widths),
    parallel, in its assigned location; wet/muddy pairs where wet ones belong."""
    gap = C.num(g.get("max_gap_m"), 0.08)
    out, ok = {}, True
    for name, spec in sorted((g.get("pairs") or {}).items()):
        a, b = spec.get("items", [None, None])[:2]
        if a not in w.s["entities"] or b not in w.s["entities"]:
            out[name] = {"ok": False, "missing": True}
            ok = False
            continue
        ra, rb = w.ent(a), w.ent(b)
        pa, pb = C.world_pose(w, a), C.world_pose(w, b)
        same_place = ra.get("location") == rb.get("location")
        want = spec.get("location")
        if spec.get("wet_location") and (C.num(ra.get("wetness")) >= 0.3 or C.num(rb.get("wetness")) >= 0.3 or
                                         C.num(((ra.get("soil") or {}) if isinstance(ra.get("soil"), dict) else {}).get("mud")) > 0.1):
            want = spec["wet_location"]
        where_ok = want is None or (w.within(a, want) and w.within(b, want))
        near = False
        parallel = True
        if pa and pb and pa[0] == pb[0]:
            width = max(min(C.dims(ra)[:2]), min(C.dims(rb)[:2]))
            near = math.hypot(pa[1] - pb[1], pa[2] - pb[2]) <= width + gap + 1e-9
            parallel = C.ang_diff(pa[3], pb[3]) <= C.num(g.get("tol_deg"), 20.0)
        elif same_place and ra.get("relation") == "in":
            near = True   # together in the same box/compartment
        good = near and parallel and where_ok
        out[name] = {"ok": good, "together": near, "parallel": parallel, "location_ok": where_ok}
        ok = ok and good
    return {"ok": ok and bool(out), "pairs": out}


EVALUATORS = {"stack_rules": g_stack_rules, "poses": g_poses, "row": g_row, "grid": g_grid, "spacing": g_spacing, "settings": g_settings, "free_area": g_free_area,
              "centered": g_centered, "facing": g_facing, "zone_clear": g_zone_clear, "walkway": g_walkway, "stack_order": g_stack_order,
              "access": g_access, "straight": g_straight, "same_as": g_same_as, "aesthetic": g_aesthetic, "pairs": g_pairs, "same_fields": g_same_fields}


def evaluate_goals(w):
    sec = C.section(w)
    goals = sec.get("goals") or {}
    results = {}
    for name in sorted(goals):
        g = goals[name]
        if g.get("kind") == "process":
            results[name] = g_process(w, g, (sec.get("results") or {}).get(name) or {})
            continue
        fn = EVALUATORS.get(g.get("kind"))
        if fn is None:
            results[name] = {"ok": False, "reason": "unknown goal kind"}
            continue
        try:
            results[name] = fn(w, g)
        except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError) as exc:
            results[name] = {"ok": False, "error": "{}: {}".format(type(exc).__name__, exc)}
    return results


def _goals_idle(w):
    """Exact idle probe of manipulation.goals: no goal is declared (with goals the results follow every write, so the
    system evaluates them each substep)."""
    return not C.section(w).get("goals")


@system("manipulation.goals", order=900, max_dt=30.0, idle=_goals_idle)
def goals_system(w, dt, tick):
    sec = C.section(w)
    if not sec.get("goals"):
        return
    results = evaluate_goals(w)
    if results != (sec.get("results") or {}):
        w.sec(C.SECTION)["results"] = results
