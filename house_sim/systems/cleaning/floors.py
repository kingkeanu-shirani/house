"""Floors, stairs, spills and wet floors (fidelity G for coverage/reach, P-approx for liquids).

Floor soil lives per 0.5 m cell in floor["_cells"]; which cells a tool can work depends on the
room layout: open cells, cells under furniture (reachable only if the tool head fits under the
furniture's clearance and reaches deep enough, or after the furniture was moved), cells under
rugs (clean the rug instead) and cells under fixed cabinets (no floor). Loose debris swept with a
broom is transported into a debris_pile entity at the `toward` cell (mass conserved); a dustpan
collects piles; vacuums ingest small objects lying in their path. Mops leave a water film
(room water_film/wet_cells) that dries over time and damages water-sensitive floors.
"""
import math

from ...kernel import geometry, physics
from ...kernel.actions import action
from ...kernel.errors import ActionError, require
from ...kernel.plugins import after, before
from . import engine as E
from . import model as M
from . import state as S

CELL_AREA = 0.25


# ----------------------------------------------------------------------
# cell selection and reach

def floor_material(rec):
    return M.material_key(rec)


def is_carpet(rec):
    key = floor_material(rec)
    return key in ("carpet", "wool_carpet") or M.material(rec).get("textile", False) and S.is_floor(rec)


def reachable_open_cells(w, rid):
    """Open floor cells the robot can stand on and reach from its current cell (BFS)."""
    room = w.s["rooms"][rid]
    cols, rows = geometry.grid_size(room)
    blocked = set(geometry.blocked_cells(w.s, rid, include_agents=True))
    start = tuple(w.robot.get("pos") or (0, 0)) if w.robot["room"] == rid else None
    if start is None:
        return set()
    seen, frontier = {start}, [start]
    while frontier:
        cell = frontier.pop()
        for dx, dy, _ in geometry.NEIGHBORS:
            nxt = (cell[0] + dx, cell[1] + dy)
            if nxt in seen or not (0 <= nxt[0] < cols and 0 <= nxt[1] < rows) or nxt in blocked:
                continue
            seen.add(nxt)
            frontier.append(nxt)
    return seen


def select_cells(ctx, floor_id, spec, zone=None, cells=None, for_mop=False):
    """Return (cells worked, skipped {cell: reason}) for a floor tool with head_m/reach_m."""
    w = ctx.w
    rec = w.ent(floor_id)
    rid = rec["location"]
    layout = S.floor_layout(w, rid)
    edges = S.perimeter_cells(w, rid)
    corners = S.corner_cells(w, rid)
    room = w.s["rooms"][rid]
    if cells:
        wanted = []
        for cell in cells:
            require(isinstance(cell, list) and len(cell) == 2 and all(type(v) is int for v in cell), "cells must be [[col,row], ...]", "argument")
            require(geometry.in_grid(room, cell), "Cell {} is outside the room".format(cell), "argument")
            wanted.append(S.ck(cell))
    else:
        zone = zone or "all"
        wanted = []
        for key, (status, src) in sorted(layout.items(), key=lambda kv: S.pk(kv[0])[::-1]):
            if zone in ("all", "floor"):
                ok = status in ("open", "under")
            elif zone == "open":
                ok = status == "open"
            elif zone == "edges":
                ok = status in ("open", "under") and key in edges
            elif zone == "corners":
                ok = status in ("open", "under") and key in corners
            elif zone == "under":
                ok = status == "under"
            elif zone.startswith("under:"):
                ok = status == "under" and src == zone[6:]
            elif zone.startswith("rug:"):
                ok = status == "rug" and src == zone[4:]
            else:
                raise ActionError("Unknown zone {} (all, open, edges, corners, under, under:<id>)".format(zone), "argument")
            if ok:
                wanted.append(key)
    if w.robot["room"] != rid:
        return across_doorway(ctx, rid, wanted)
    reach = reachable_open_cells(w, rid)
    robot_cell = S.ck(w.robot.get("pos") or (0, 0))
    head = float(spec.get("head_m", 0.1))
    depth_reach = float(spec.get("reach_m", 0.4))
    agents = {S.ck(a["pos"]) for g in ("people", "pets") for a in w.s.get(g, {}).values() if a.get("location") == rid and a.get("pos")}
    chosen, skipped = [], {}
    for key in wanted:
        status, src = layout.get(key, ("open", None))
        cell = S.pk(key)
        if key in agents:
            skipped[key] = "occupied"
            continue
        if for_mop and key == robot_cell:
            skipped[key] = "robot standing here"
            continue
        if status == "blocked":
            skipped[key] = "under fixed " + str(src)
            continue
        if status == "rug" and not cells:
            skipped[key] = "under rug " + str(src)
            continue
        if status == "under":
            frec = w.ent(src)
            if head > S.clearance_m(frec) + 1e-9:
                skipped[key] = "tool too thick for the gap under " + src
                continue
            dist = min([geometry.chebyshev(cell, r) for r in reach] or [99]) * geometry.CELL - 0.25
            if dist > depth_reach + 1e-9:
                skipped[key] = "too deep under " + src
                continue
            chosen.append(key)
            continue
        if cell not in reach and not any(geometry.chebyshev(cell, r) <= 1 for r in reach):
            skipped[key] = "unreachable"
            continue
        chosen.append(key)
    return chosen, skipped


def across_doorway(ctx, rid, wanted):
    """Finish a floor from the adjacent room's doorway (backing out of a freshly mopped room)."""
    w = ctx.w
    here = w.robot["room"]
    cid = None
    for c, conn in sorted(w.s.get("connections", {}).items()):
        if set(conn["rooms"]) == {here, rid}:
            cid = c
            break
    require(cid is not None, "Go to that room first", "precondition")
    conn = w.s["connections"][cid]
    door = w.s["entities"].get(conn.get("door") or "")
    require(door is None or door.get("open"), "Open the door first", "precondition")
    mine = tuple(conn["cells"][here])
    require(geometry.chebyshev(tuple(w.robot.get("pos") or (0, 0)), mine) <= 1, "Stand at the doorway to reach into that room", "precondition")
    theirs = tuple(conn["cells"][rid])
    chosen = [k for k in wanted if geometry.chebyshev(S.pk(k), theirs) <= 2]
    skipped = {k: "beyond reach from the doorway" for k in wanted if k not in chosen}
    return chosen, skipped


def floor_target(ctx, target):
    """Visibility for floors: the robot's room floor, or an adjacent floor reached across a doorway."""
    w = ctx.w
    rec = w.s["entities"].get(target)
    require(rec is not None, "That is not visible here", "perception")
    if rec.get("location") == w.robot["room"]:
        return ctx.obj(target, touch=False)
    require(S.is_floor(rec) and rec.get("location") in w.s["rooms"].get(w.robot["room"], {}).get("exits", []),
            "That is not visible here", "perception")
    return rec


def work_cells(ctx, floor_id, cells, verb, sources_for, mech, wetness, kinds=None, tool=None, force="normal"):
    """Apply one cleaning pass to floor cells; returns total removed {kind: level-sum}."""
    w = ctx.w
    rec = w.ent(floor_id)
    S.reconcile(w, floor_id)
    removed = {}
    for key in cells:
        soil = dict(S.cell_soil(w.ent(floor_id), key))
        if not soil:
            continue
        sources = sources_for(key)
        softened = float((rec.get("_softened") or {}).get(key, 0.0))
        new = dict(soil)
        for kind, level in sorted(soil.items()):
            if kinds is not None and kind not in kinds:
                continue
            chem = M.chem_power(kind, sources, softened)
            frac = M.removal_fraction(kind, mech * M.FORCE.get(force, 1.0), wetness, chem, action=verb, softened=softened)
            took = level * frac
            new[kind] = S.rnd(level - took)
            removed[kind] = removed.get(kind, 0.0) + took
        part = S.mut_cell(w, floor_id, key)
        part.clear()
        part.update({k: v for k, v in new.items() if v > 0.0005})
    applied = rec.get("cl_applied") or []
    if applied and verb in ("wipe", "scrub", "mop", "rinse", "dry"):
        faces = {"cell:" + k for k in cells}
        w.mut(floor_id)["cl_applied"] = [a for a in applied if a.get("face") not in faces]
    S.sync(w, floor_id)
    if tool:
        S.load_tool(w, tool, sum(removed.values()) * 0.05)
    return removed


def floor_sources(w, floor_id, bucket=None, tool=None, contact=E.CONTACT_S):
    rec = w.ent(floor_id)
    t = S.now(w)
    tool_classes = []
    if tool:
        for product, ml in sorted((w.ent(tool).get("cl_chem") or {}).items()):
            if float(ml) > 0:
                tool_classes += [(c, p, contact) for c, p in M.product_classes(product).items()]
    bucket_classes = []
    if bucket:
        bucket_classes = [(c, p, contact) for c, p in S.solution_classes(w.ent(bucket)).items()]

    def sources_for(key):
        out = list(tool_classes) + list(bucket_classes)
        for entry in rec.get("cl_applied") or []:
            if entry.get("face") in ("cell:" + key, "all"):
                elapsed = max(0.0, min(t, float(entry.get("wet_until", t))) - float(entry.get("t", t))) + contact
                out += [(c, p * float(entry.get("s", 1.0)), elapsed) for c, p in M.product_classes(entry.get("p")).items()]
        return out
    return sources_for


# ----------------------------------------------------------------------
# spills

def make_spill(w, location, liquid, liters, near=None, cell=None, surface=None, eid=None, **props):
    """Create a spill entity (other domains may import this). Returns its id."""
    liters = max(0.001, float(liters))
    record = {"liquid": liquid, "liquid_l": round(liters, 4), "label": "{} spill".format(str(liquid).replace("_", " "))}
    record.update(SPILL_PROPS.get(liquid, {}))
    record.update(props)
    if surface:
        rid = surface
    else:
        rid = location
    spill = w.spawn("spill", rid, eid=eid, **record)
    m = w.mut(spill)
    if location in w.s["rooms"] and not surface:
        if cell is None:
            anchor = None
            if near and near in w.s["entities"]:
                _, cells = geometry.anchor(w.s, near)
                anchor = cells[0] if cells else None
            anchor = anchor or tuple(w.robot.get("pos") or (0, 0))
            cell = geometry.free_floor_cell(w.s, location, near=tuple(anchor), ignore=(spill,)) or list(anchor)
        m["pos"] = [int(cell[0]), int(cell[1])]
        m["relation"] = "on"
        floor = S.floor_of(w, location)
        if floor and is_carpet(w.ent(floor)):
            m["on_carpet"] = True
    resize_spill(w, spill)
    S.glog(w, "spill", spill=spill, liquid=liquid, liters=liters)
    return spill


SPILL_PROPS = {
    "water": {"residue": {}, "floor_hazard": "wet"},
    "juice": {"residue": {"sticky": 0.7, "stain_juice": 0.3}, "floor_hazard": "wet"},
    "soda": {"residue": {"sticky": 0.8}, "floor_hazard": "wet"},
    "milk": {"residue": {"protein": 0.5, "sticky": 0.2}, "floor_hazard": "wet", "odor_source": True},
    "wine": {"residue": {"stain_wine": 0.8, "sticky": 0.2}, "floor_hazard": "wet"},
    "coffee": {"residue": {"stain_coffee": 0.7, "sticky": 0.2}, "floor_hazard": "wet"},
    "cooking_oil": {"residue": {"grease": 0.9, "stain_oil": 0.4}, "floor_hazard": "slip"},
    "oil": {"residue": {"grease": 0.9, "stain_oil": 0.4}, "floor_hazard": "slip"},
    "motor_oil": {"residue": {"stain_oil": 0.9, "grease": 0.6}, "floor_hazard": "slip", "hazards": ["toxic"], "hazardous_waste": True},
    "antifreeze": {"residue": {"sticky": 0.4}, "floor_hazard": "wet", "hazards": ["toxic"], "toxic_to_pets": True, "hazardous_waste": True},
    "gasoline": {"residue": {"stain_oil": 0.3}, "floor_hazard": "slip", "hazards": ["flammable", "toxic"], "hazardous_waste": True, "volatile": True},
    "urine": {"residue": {"urine": 0.8}, "contamination": {"bacteria": 0.6, "biohazard": 0.6}, "hazards": ["biohazard"], "handled_with": ["rubber_gloves", "disposable_gloves"], "floor_hazard": "wet"},
    "vomit": {"residue": {"biohazard": 0.8, "food": 0.4}, "contamination": {"bacteria": 0.9, "biohazard": 0.9}, "hazards": ["biohazard"], "handled_with": ["rubber_gloves", "disposable_gloves"], "floor_hazard": "slip"},
    "raw_meat_juice": {"residue": {"protein": 0.4}, "contamination": {"raw_meat": 0.9, "bacteria": 0.6}, "floor_hazard": "wet"},
    "raw_egg": {"residue": {"protein": 0.8}, "contamination": {"bacteria": 0.5}, "floor_hazard": "slip"},
    "paint": {"residue": {"paint_acrylic": 0.9}, "floor_hazard": "slip"},
    "bleach": {"residue": {}, "chem_classes": {"chlorine": 1.0}, "hazards": ["corrosive"], "floor_hazard": "wet"},
    "mud_water": {"residue": {"mud": 0.7}, "floor_hazard": "wet"},
    "dirty_water": {"residue": {"dirt": 0.3, "streaks": 0.3}, "floor_hazard": "wet"},
    "cleaning_solution": {"residue": {"streaks": 0.2}, "floor_hazard": "wet"},
    "garbage_juice": {"residue": {"garbage_juice": 0.7}, "contamination": {"bacteria": 0.5}, "floor_hazard": "wet", "odor_source": True},
}


def resize_spill(w, spill):
    rec = w.ent(spill)
    liters = float(rec.get("liquid_l", 0.0))
    thickness_mm = 3.0 if rec.get("on_carpet") else (2.5 if rec.get("liquid") in ("cooking_oil", "motor_oil", "paint", "vomit") else 1.5)
    area = max(0.01, liters / thickness_mm) * float(rec.get("spread_factor", 1.0))
    side = round(min(3.0, math.sqrt(area)), 3)
    m = w.mut(spill)
    m["area_m2"] = round(area, 4)
    m["size_m"] = [max(0.05, side), max(0.05, side), 0.002]


def spill_cells(w, spill):
    rec = w.ent(spill)
    return [S.ck(c) for c in geometry.footprint(rec)] if rec.get("pos") else []


def finish_spill(w, spill, residue_scale=1.0):
    """Liquid gone: leave residue on the floor cells or the surface it was on, then remove the spill."""
    rec = w.ent(spill)
    residue = rec.get("residue") or {}
    loc = rec.get("location")
    if loc in w.s["rooms"]:
        floor = S.floor_of(w, loc)
        if floor:
            for key in spill_cells(w, spill):
                for kind, level in sorted(residue.items()):
                    S.add_cell_soil(w, floor, key, kind, level * residue_scale)
            for kind, level in sorted((rec.get("contamination") or {}).items()):
                S.add_contamination(w, floor, kind, level * residue_scale * 0.5)
            for key in spill_cells(w, spill):
                S.set_film(w, loc, key, max(S.film_of(w, loc, key), 0.03 * residue_scale))
    elif loc in w.s["entities"]:
        face = S.primary_face(w.ent(loc))
        for kind, level in sorted(residue.items()):
            S.add_face_soil(w, loc, face, kind, level * residue_scale)
        for kind, level in sorted((rec.get("contamination") or {}).items()):
            S.add_contamination(w, loc, kind, level * residue_scale)
    w.destroy(spill)


def wipe_spill(ctx, spill, verb):
    """wipe/dry/rinse targeting a spill: wipe it up with a cloth/towel/paper/sponge."""
    w = ctx.w
    rec = ctx.obj(spill, touch=True)
    tool = S.held_tool(ctx, kinds=("cloth", "towel", "paper", "wipe", "sponge", "mop", "pad"), message="Hold a cloth, towel or paper towels")
    direction = ctx.args.get("direction") or "inward"
    return soak_up(ctx, spill, tool, method="wipe", direction=direction)


def soak_up(ctx, spill, tool, method="blot", direction="inward"):
    w = ctx.w
    rec = w.ent(spill)
    trec = w.ent(tool)
    spec = S.tool_spec(trec)
    if "biohazard" in rec.get("hazards", []) and not set(w.robot.get("wearing", [])) & {"rubber_gloves", "disposable_gloves"}:
        w.stat("safety_violations")
        S.glog(w, "biohazard_without_gloves", spill=spill)
    capacity = max(0.01, float(spec.get("absorb_l", 0.1)))
    if spec.get("tool_kind") == "paper_towel":
        sheets = int(trec.get("sheets", 40))
        use = min(sheets, int(ctx.args.get("sheets") or 4))
        capacity = 0.05 * use
        w.mut(tool)["sheets"] = sheets - use
        disposable = True
    else:
        disposable = spec.get("disposable", False)
    wet = float(trec.get("wetness", 0.0) or 0.0)
    room_left = capacity * max(0.0, 1.0 - wet)
    liters = float(rec.get("liquid_l", 0.0))
    efficiency = 0.92 if method == "blot" else 0.75
    if rec.get("on_carpet"):
        efficiency *= 0.85 if method == "blot" else 0.55
    taken = min(room_left, liters * efficiency)
    if taken <= 0.0005:
        S.glog(w, "saturated_tool", tool=tool)
        if wet >= 0.95:
            spread(w, spill, 1.15)
        ctx.data["seconds"] = 6.0
        return "The {} is saturated; it just pushes the liquid around".format(tool if S.realistic(ctx) else trec.get("label", tool))
    m = w.mut(spill)
    m["liquid_l"] = S.rnd(max(0.0, liters - taken), 4)
    tm = w.mut(tool)
    if not disposable:
        tm["wetness"] = S.rnd(M.clamp(wet + taken / capacity))
        for kind, level in sorted((rec.get("contamination") or {}).items()):
            S.add_contamination(w, tool, kind, level)
        if rec.get("hazardous_waste") or rec.get("liquid") in ("motor_oil", "gasoline", "antifreeze"):
            tm["hazardous_waste"] = True
            tm["cl_soaked_with"] = rec.get("liquid")
    else:
        if rec.get("contamination") or "biohazard" in rec.get("hazards", []) or rec.get("hazardous_waste"):
            soiled_waste(w, spill, rec)
    if method == "rub" or direction == "outward":
        spread(w, spill, 1.45)
        if rec.get("on_carpet"):
            m["soaked_l"] = S.rnd(float(m.get("soaked_l", 0.0)) + taken * 0.5, 4)
    if rec.get("on_carpet") and method == "blot":
        m["soaked_l"] = S.rnd(float(m.get("soaked_l", 0.0)) + taken * 0.05, 4)
    resize_spill(w, spill)
    ctx.data["seconds"] = 6.0 + 25.0 * taken
    if S.realistic(ctx):
        # pressing the towel down takes as long as the towel can drink, not as long as the (unseen) litres it took
        ctx.data["seconds"] = 6.0 + 25.0 * room_left
    if float(w.ent(spill)["liquid_l"]) <= 0.003:
        scale = 1.0 if method == "rub" else 0.7
        if rec.get("on_carpet"):
            scale += float(w.ent(spill).get("soaked_l", 0.0)) * 4.0
        finish_spill(w, spill, residue_scale=min(1.5, scale))
        return "Soaked up the spill; residue may remain"
    if S.realistic(ctx):
        return "Soaked up {} of the spill; {} left".format(S.liters_text(ctx, taken), S.liters_text(ctx, float(w.ent(spill)["liquid_l"])))
    return "Soaked up {:.2f} l; {:.2f} l left".format(taken, float(w.ent(spill)["liquid_l"]))


def soiled_waste(w, spill, rec):
    """Used disposables that touched biohazard/oil become an item to bag and bin."""
    loc = rec.get("location")
    props = {"label": "used paper towels", "waste_kind": "soiled_disposable", "contamination": dict(rec.get("contamination") or {}),
             "hazards": list(rec.get("hazards", [])), "handled_with": list(rec.get("handled_with", [])), "_disposition": "trash"}
    if rec.get("hazardous_waste"):
        props["_disposition"] = "hazardous"
        props["hazardous_waste"] = True
    if loc in w.s["rooms"]:
        eid = w.spawn("waste_item", loc, **props)
        cell = geometry.free_floor_cell(w.s, loc, near=tuple(rec.get("pos") or (0, 0)), ignore=(eid,))
        w.mut(eid)["pos"] = cell or list(rec.get("pos") or [0, 0])
    else:
        eid = w.spawn("waste_item", loc, **props)
        w.mut(eid)["relation"] = "on"
    return eid


def spread(w, spill, factor):
    m = w.mut(spill)
    m["spread_factor"] = S.rnd(min(4.0, float(m.get("spread_factor", 1.0)) * factor), 3)
    resize_spill(w, spill)
    S.glog(w, "spill_spread", spill=spill, factor=factor)


# ----------------------------------------------------------------------
# floor cell helpers used by surface actions

def floor_cells_param(ctx, floor_id, spec=None):
    cells = ctx.args.get("cells")
    zone = ctx.args.get("zone")
    require(cells or zone, "Name the floor cells (cells=[[c,r],...]) or a zone", "argument")
    chosen, skipped = select_cells(ctx, floor_id, spec or {"head_m": 0.02, "reach_m": 0.6}, zone=zone, cells=cells)
    return chosen, skipped


def clean_floor_cells(ctx, floor_id, verb):
    w = ctx.w
    rec = floor_target(ctx, floor_id)
    if S.is_stairs(rec):
        return stairs_pass(ctx, floor_id, verb)
    tool = S.held_tool(ctx, message="Hold a cloth, sponge, brush or towel")
    spec = S.tool_spec(w.ent(tool), side=ctx.args.get("side"))
    if spec.get("kind") not in ("mop",):
        require(w.robot.get("posture") in ("crouching", "kneeling"), "Crouch or kneel to work the floor by hand", "precondition")
    chosen, skipped = floor_cells_param(ctx, floor_id, {"head_m": 0.02, "reach_m": 0.6})
    require(chosen, "None of those cells can be reached ({})".format(", ".join(sorted(set(skipped.values()))) or "none"), "precondition")
    rid = rec["location"]
    wet = float(w.ent(tool).get("wetness", 0.0) or 0.0)
    if w.ent(tool).get("cl_chem"):
        wet = max(wet, 0.3)
    if verb == "dry":
        cap = max(0.02, float(spec.get("absorb_l", 0.2))) * max(0.0, 1.0 - wet)
        took = 0.0
        for key in chosen:
            film = S.film_of(w, rid, key)
            liters = film * CELL_AREA
            got = min(liters, max(0.0, cap - took))
            took += got
            S.set_film(w, rid, key, max(0.0, film - got / CELL_AREA))
        w.mut(tool)["wetness"] = S.rnd(M.clamp(wet + took / max(0.02, float(spec.get("absorb_l", 0.2)))))
        ctx.data["seconds"] = 4.0 + 3.0 * len(chosen)
        if S.realistic(ctx):
            return "Dried {} floor cells ({} taken up)".format(len(chosen), S.liters_text(ctx, took))
        return "Dried {} floor cells ({:.2f} l taken up)".format(len(chosen), took)
    mech = float(spec.get("mech", 0.3))
    if verb == "rinse":
        mech = 0.3
    sources = floor_sources(w, floor_id, tool=tool)
    if is_carpet(rec) and wet >= 0.4 and verb in ("scrub", "wipe"):
        for key in chosen:
            mud = float(S.cell_soil(w.ent(floor_id), key).get("mud", 0.0))
            if mud > 0.15:
                S.add_cell_soil(w, floor_id, key, "dirt", mud * 0.5)
                S.add_cell_soil(w, floor_id, key, "stain_food", mud * 0.3)
                S.glog(w, "ground_wet_mud_into_carpet", cell=key)
    removed = work_cells(ctx, floor_id, chosen, verb if verb != "degrease" else "scrub", sources, mech, max(wet, 0.5 if verb == "rinse" else wet),
                         tool=tool, force=ctx.args.get("force") or "normal")
    E.check_abrasion(w, floor_id, float(spec.get("abrasion", 0.0)), ctx.args.get("force") or "normal")
    if wet > 0.6:
        for key in chosen:
            S.set_film(w, rid, key, max(S.film_of(w, rid, key), 0.05 if wet < 0.9 else 0.2))
    S.zone_transfer(w, tool, floor_id)
    ctx.data["seconds"] = 5.0 + 6.0 * len(chosen)
    return "{} {} floor cells ({})".format(verb.capitalize(), len(chosen), S.removed_text(ctx, removed))


def apply_to_floor(ctx, floor_id, product, amount, strength, classes, bottle=None):
    w = ctx.w
    rec = floor_target(ctx, floor_id)
    chosen, _ = select_cells(ctx, floor_id, {"head_m": 0.02, "reach_m": 1.0}, zone=ctx.args.get("zone") or ("all" if not ctx.args.get("cells") else None),
                             cells=ctx.args.get("cells"))
    require(chosen, "No reachable floor cells there", "precondition")
    t = S.now(w)
    pinfo = M.PRODUCTS.get(product, {})
    m = w.mut(floor_id)
    applied = m.setdefault("cl_applied", [])
    S.watch(w, "chem", floor_id)
    per_cell = amount / max(1, len(chosen))
    for key in chosen:
        applied.append({"p": product, "face": "cell:" + key, "t": t, "wet_until": t + float(pinfo.get("wet_s", 300.0)),
                        "s": S.rnd(strength, 3), "ml": S.rnd(per_cell, 2)})
    E.check_chemicals(w, floor_id, classes, product)
    S.add_residue(w, floor_id, classes, 0.2)
    if pinfo.get("oily"):
        room = w.room_m(rec["location"])
        hazards = room.setdefault("floor_hazards", [])
        for key in chosen:
            hazards.append({"cell": list(S.pk(key)), "kind": "slip", "source": floor_id})
        w.mut(floor_id)["slippery"] = True
    if pinfo.get("granular"):
        absorb_with_granules(ctx, floor_id, chosen, amount)
    ctx.data["seconds"] = 4.0 + 1.5 * len(chosen)
    return "Applied {} to {} floor cells".format(S.product_text(ctx, product, bottle), len(chosen))


def absorb_with_granules(ctx, floor_id, cells, amount_ml):
    """Oil absorbent / cat litter spread over spills in those cells -> saturated granule piles."""
    w = ctx.w
    rid = w.ent(floor_id)["location"]
    keys = set(cells)
    capacity_l = amount_ml / 1000.0 * 0.8
    for spill in sorted(e for e, r in w.s["entities"].items() if r.get("category") == "spill" and r.get("location") == rid):
        if not keys & set(spill_cells(w, spill)):
            continue
        rec = w.ent(spill)
        liters = float(rec.get("liquid_l", 0.0))
        taken = min(liters, capacity_l)
        capacity_l -= taken
        pile = add_pile(w, rid, tuple(rec["pos"]), {"oily_absorbent" if rec.get("liquid") in ("motor_oil", "cooking_oil", "oil", "gasoline") else "wet_absorbent": amount_ml * 0.6 + taken * 900.0})
        pm = w.mut(pile)
        pm["hazardous_waste"] = bool(rec.get("hazardous_waste"))
        pm["absorbed"] = rec.get("liquid")
        if rec.get("toxic_to_pets"):
            pm["toxic_to_pets"] = True
        m = w.mut(spill)
        m["liquid_l"] = S.rnd(liters - taken, 4)
        if m["liquid_l"] <= 0.003:
            finish_spill(w, spill, residue_scale=0.5)
        else:
            resize_spill(w, spill)


def disinfect_floor(ctx, target, product, strength, tool, bottle=None):
    w = ctx.w
    rec = w.ent(target)
    if rec.get("category") == "spill":
        ctx.obj(target, touch=True)
        S.glog(w, "disinfectant_on_bulk_spill", spill=target)
        m = w.mut(target)
        cont = m.get("contamination") or {}
        for kind in sorted(cont):
            cont[kind] = S.rnd(float(cont[kind]) * 0.7)
        m.setdefault("cl_applied", []).append({"p": product, "face": "main", "t": S.now(w), "wet_until": S.now(w) + 600, "s": strength})
        S.watch(w, "chem", target)
        ctx.data["seconds"] = 4.0
        return "Sprayed disinfectant onto the bulk spill (organic load shields the germs; remove it first)"
    floor_target(ctx, target)
    chosen, _ = select_cells(ctx, target, {"head_m": 0.02, "reach_m": 1.0}, zone=ctx.args.get("zone"), cells=ctx.args.get("cells") or None) \
        if (ctx.args.get("cells") or ctx.args.get("zone")) else (sorted(S.floor_soil_cells(rec, threshold=0.0) or []), {})
    if S.realistic(ctx) and not (ctx.args.get("cells") or ctx.args.get("zone")):
        # the soiled cells the robot can see (not ones under rugs or low furniture: their count would leak them)
        vis = S.floor_visibility(w, target)
        chosen = [k for k in chosen if vis.get(k) == "visible"]
    require(chosen, "Name the floor cells to disinfect (cells=[[c,r],...])", "argument")
    pinfo = M.PRODUCTS[product]
    t = S.now(w)
    m = w.mut(target)
    S.watch(w, "chem", target)
    for key in chosen:
        m.setdefault("cl_applied", []).append({"p": product, "face": "cell:" + key, "t": t, "wet_until": t + float(pinfo.get("wet_s", 600)),
                                               "s": S.rnd(strength, 3), "kill_s": pinfo["kill_s"]})
    classes = {c: p * strength for c, p in M.product_classes(product).items()}
    E.check_chemicals(w, target, classes, product)
    S.add_residue(w, target, classes, 0.3)
    ctx.data["seconds"] = 3.0 + 1.0 * len(chosen)
    return "Applied {} to {} floor cells".format(S.product_text(ctx, product, bottle), len(chosen))


def treat_carpet_cells(ctx, floor_id, tool):
    w = ctx.w
    rec = floor_target(ctx, floor_id)
    require(w.robot.get("posture") in ("crouching", "kneeling"), "Crouch or kneel to blot the carpet", "precondition")
    chosen, _ = floor_cells_param(ctx, floor_id)
    method = ctx.args.get("method") or "blot"
    classes = {}
    for product, ml in sorted((w.ent(tool).get("cl_chem") or {}).items()):
        for cls, power in M.product_classes(product).items():
            classes[cls] = max(classes.get(cls, 0.0), power)
    E.check_chemicals(w, floor_id, classes, "spot cleaner")
    sources = floor_sources(w, floor_id, tool=tool, contact=60.0)
    kinds = tuple(M.STAINS) + ("urine", "biohazard", "mud", "food", "sticky", "grease", "protein")
    removed = work_cells(ctx, floor_id, chosen, "wipe", sources, 0.3 if method == "blot" else 0.45, 0.5, kinds=kinds, tool=tool)
    if method == "rub":
        rid = rec["location"]
        room = w.s["rooms"][rid]
        for key in list(chosen):
            soil = S.cell_soil(w.ent(floor_id), key)
            for kind in kinds:
                if float(soil.get(kind, 0.0)) > 0.02:
                    for cell in sorted(geometry.near_cells([S.pk(key)], room)):
                        S.add_cell_soil(w, floor_id, S.ck(cell), kind, float(soil[kind]) * 0.3)
    ctx.data["seconds"] = 20.0 + 10.0 * len(chosen)
    return "Treated {} carpet cells ({})".format(len(chosen), S.removed_text(ctx, removed, empty=""))


def scrape_floor(ctx, floor_id, tool):
    w = ctx.w
    floor_target(ctx, floor_id)
    require(w.robot.get("posture") in ("crouching", "kneeling"), "Crouch or kneel to scrape the floor", "precondition")
    chosen, _ = floor_cells_param(ctx, floor_id)
    spec = S.tool_spec(w.ent(tool), angle=ctx.args.get("angle"))
    removed = work_cells(ctx, floor_id, chosen, "scrub", lambda k: [], 0.9, 0.3, kinds=("gum", "wax", "paint_acrylic", "paint_oil", "glue", "mud", "food", "burnt"), tool=tool)
    E.check_abrasion(w, floor_id, float(spec.get("abrasion", 0.3)))
    drop_to_floor(w, w.ent(floor_id)["location"], {"crumbs": sum(removed.values()) * 20.0})
    ctx.data["seconds"] = 10.0 + 8.0 * len(chosen)
    return "Scraped {} floor cells".format(len(chosen))


def drop_to_floor(w, rid, composition):
    if rid not in w.s["rooms"] or not composition:
        return
    floor = S.floor_of(w, rid)
    if not floor:
        return
    key = S.ck(w.robot.get("pos") or (0, 0)) if w.robot["room"] == rid else "0,0"
    for kind, grams in sorted(composition.items()):
        if grams > 0:
            S.add_cell_soil(w, floor, key, kind, M.level_from_grams(kind, grams, CELL_AREA))


def wet_cells_near(w, floor_id, source, mm):
    rid = w.ent(floor_id)["location"]
    _, cells = geometry.anchor(w.s, source)
    room = w.s["rooms"][rid]
    for cell in sorted(geometry.near_cells(cells or [tuple(w.robot.get("pos") or (0, 0))], room)):
        S.set_film(w, rid, S.ck(cell), max(S.film_of(w, rid, S.ck(cell)), mm))


# ----------------------------------------------------------------------
# debris piles

def add_pile(w, rid, cell, composition, location=None, relation=None):
    """Merge debris (grams by kind) into a pile at a floor cell (or under a rug/furniture)."""
    composition = {k: v for k, v in composition.items() if v > 0.01}
    if not composition:
        return None
    loc = location or rid
    pile = None
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if rec.get("category") == "debris" and rec.get("location") == loc and (loc != rid or tuple(rec.get("pos") or ()) == tuple(cell)):
            pile = eid
            break
    if pile is None:
        pile = w.spawn("debris_pile", loc, composition={})
        m = w.mut(pile)
        if loc == rid:
            m["pos"] = [int(cell[0]), int(cell[1])]
            m["relation"] = "on"
        else:
            m["relation"] = relation or "under"
            m.pop("pos", None)
    m = w.mut(pile)
    comp = m.setdefault("composition", {})
    for kind, grams in sorted(composition.items()):
        comp[kind] = S.rnd(float(comp.get(kind, 0.0)) + grams, 2)
    refresh_pile(w, pile)
    return pile


def refresh_pile(w, pile):
    m = w.mut(pile)
    comp = m.get("composition") or {}
    mass = sum(comp.values())
    m["mass_g"] = S.rnd(mass, 2)
    m["soil"] = {k: S.rnd(M.clamp(v / 50.0)) for k, v in sorted(comp.items()) if v > 0.01}
    m["volume_l"] = S.rnd(mass / 400.0, 4)
    m["floor_hazard"] = "slip" if any(k in comp for k in ("sand", "salt", "leaves", "cat_litter", "oily_absorbent")) else None
    if mass < 0.5:
        w.destroy(pile)
        return None
    return pile


def piles_in(w, rid):
    return sorted(e for e, r in w.s["entities"].items() if r.get("category") == "debris" and w.root_room(e) == rid)


# ----------------------------------------------------------------------
# sweep

def wind_to(w):
    weather = w.s.get("weather", {})
    frm = str(weather.get("wind_from", "W")).upper()
    vectors = {"N": (0, 1), "S": (0, -1), "E": (-1, 0), "W": (1, 0), "NE": (-1, 1), "NW": (1, 1), "SE": (-1, -1), "SW": (1, -1)}
    return vectors.get(frm, (1, 0)), float(weather.get("wind_mps", 0.0))


def special_destination(w, rid, cell):
    """Storm drains, pools, floor vents or drains at a cell."""
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if rec.get("location") != rid or not rec.get("pos"):
            continue
        if not (rec.get("storm_drain") or rec.get("water_body") or rec.get("floor_vent") or rec.get("floor_drain")):
            continue
        if tuple(cell) in geometry.footprint(rec) or tuple(cell) == tuple(rec["pos"]):
            return eid
    return None


def cover_at(w, rid, key):
    status, src = S.floor_layout(w, rid).get(key, ("open", None))
    if status == "under" and S.clearance_m(w.ent(src)) >= S.OPEN_UNDER_M:
        return None
    if status in ("rug", "under", "blocked"):
        return src
    return None


def items_on_cells(w, rid, keys, max_size=0.15):
    found = []
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if rec.get("location") != rid or not rec.get("portable") or rec.get("category") in ("debris", "spill", "rug", "floor"):
            continue
        if rec.get("ground_layer") or not rec.get("pos"):
            continue
        size = rec.get("size_m") or [0.1, 0.1, 0.1]
        if max(float(v) for v in size[:3]) > max_size:
            continue
        if S.ck(rec["pos"]) in keys:
            found.append(eid)
    return found


def items_under(w, furniture_ids, max_size=0.15):
    found = []
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if rec.get("location") in furniture_ids and rec.get("relation") == "under" and rec.get("portable"):
            size = rec.get("size_m") or [0.1, 0.1, 0.1]
            if max(float(v) for v in size[:3]) <= max_size:
                found.append(eid)
    return found


@action("sweep", params={"target": "entity", "tool": "?entity", "zone": "?str", "cells": "?list", "toward": "?cell",
                         "out_to": "?str", "face": "?str", "order": "?str:top_down|bottom_up", "into": "?entity"},
        duration=lambda ctx: ctx.data.get("seconds", 30.0), energy=0.03, noise=0.05, category="physical",
        doc="Sweep loose debris on a hard floor (or stairs) with a held broom into one pile at `toward` [c,r] (default: a free "
            "cell near you). zone: all/open/edges/corners/under/under:<id>; cells for spots. Small objects in the way are "
            "pushed into the pile. out_to=<adjacent room> or 'edge' sweeps it out of the room/off the edge. Brooms smear wet "
            "spills, barely work on carpet, and lose fine dust into the air. Stairs: treads are swept top-down onto the next tread.")
def sweep(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=("broom",), message="Hold a broom or hand brush")
    rec = floor_target(ctx, target)
    if S.is_stairs(rec):
        return stairs_pass(ctx, target, "sweep", tool)
    require(S.is_floor(rec), "Sweep floors and stairs (use wipe for furniture)", "argument")
    rid = rec["location"]
    room = w.s["rooms"][rid]
    key = floor_material(rec)
    require(key not in ("grass", "soil") and room.get("terrain") not in ("grass", "soil"), "That ground needs a rake, not a broom", "capability")
    spec = S.tool_spec(w.ent(tool))
    chosen, skipped = select_cells(ctx, target, spec, zone=ctx.args.get("zone"), cells=ctx.args.get("cells"))
    require(chosen, "Nothing reachable to sweep ({})".format(", ".join(sorted(set(skipped.values()))) or "empty zone"), "precondition")
    surface_factor = 0.15 if is_carpet(rec) else (0.85 if key in ("concrete", "pavers", "brick", "asphalt", "wood_deck") else 1.0)
    edges, corners = S.perimeter_cells(w, rid), S.corner_cells(w, rid)
    toward = ctx.args.get("toward")
    out_to = ctx.args.get("out_to")
    if toward is None:
        free = geometry.free_floor_cell(w.s, rid, near=tuple(w.robot.get("pos") or (0, 0)))
        toward = free or list(w.robot.get("pos") or [0, 0])
    require(geometry.in_grid(room, toward), "toward must be a cell in this room", "argument")
    wind, speed = wind_to(w)
    centroid = (sum(S.pk(k)[0] for k in chosen) / len(chosen), sum(S.pk(k)[1] for k in chosen) / len(chosen))
    upwind = room.get("outdoor") and speed >= 3.0 and ((toward[0] - centroid[0]) * wind[0] + (toward[1] - centroid[1]) * wind[1]) < -0.5
    moved = {}
    airborne = 0.0
    S.sync_wet(w, rid)
    room = w.s["rooms"][rid]
    wet_keys = set(room.get("water_film", {}))
    spills = [e for e in sorted(w.s["entities"]) if w.s["entities"][e].get("category") == "spill" and w.s["entities"][e].get("location") == rid]
    for spill in spills:
        if set(spill_cells(w, spill)) & set(chosen):
            srec = w.ent(spill)
            if srec.get("liquid") in ("cooking_oil", "motor_oil", "oil", "gasoline", "paint"):
                spread(w, spill, 1.5)
                for cell in sorted(geometry.near_cells([S.pk(k) for k in spill_cells(w, spill)], room)):
                    S.add_cell_soil(w, target, S.ck(cell), "stain_oil", 0.3)
                S.glog(w, "swept_through_oil", spill=spill)
            else:
                wet_keys |= set(spill_cells(w, spill))
    S.reconcile(w, target)
    biohazard_cells = [k for k in chosen if float(S.cell_soil(w.ent(target), k).get("biohazard", 0.0)) > 0.1]
    if biohazard_cells:
        w.stat("safety_violations")
        S.glog(w, "dry_swept_biohazard", cells=biohazard_cells[:5])
        settle_room_dust(w, rid, "dust", 2.0)
    for key in chosen:
        soil = dict(S.cell_soil(w.ent(target), key))
        if not soil:
            continue
        part = S.mut_cell(w, target, key)
        edge_factor = 1.0
        if key in corners:
            edge_factor = float(spec.get("edge", 0.6)) * 0.85
        elif key in edges:
            edge_factor = float(spec.get("edge", 0.6))
        for kind, level in sorted(soil.items()):
            if kind not in M.FLOOR_LOOSE:
                continue
            if key in wet_keys:
                part["mud"] = S.rnd(M.clamp(part.get("mud", 0.0) + level * 0.5))
                part[kind] = S.rnd(level * 0.6)
                continue
            eff = float(spec.get("transport", 0.85)) * surface_factor * edge_factor
            if kind in ("pet_hair", "hair"):
                eff *= 0.8
            if kind == "glitter":
                eff *= 0.55
            took = level * eff
            grams = M.grams(kind, took, CELL_AREA)
            if M.SOILS[kind].get("fine") or kind == "dust":
                lost = 0.3 if M.SOILS[kind].get("fine") else 0.12
                airborne += grams * lost
                grams *= (1.0 - lost)
            if upwind and kind in ("leaves", "dust", "pollen", "litter", "glitter", "sawdust", "drywall_dust"):
                back = took * 0.4
                took -= back
                grams *= 0.6
            part[kind] = S.rnd(level - took)
            moved[kind] = moved.get(kind, 0.0) + grams
        S.sync(w, target)
    pushed = items_on_cells(w, rid, set(chosen))
    under_ids = sorted({src for key in chosen for (st, src) in [S.floor_layout(w, rid).get(key, ("open", None))] if st == "under"})
    pushed += items_under(w, under_ids)
    if airborne > 0:
        settle_room_dust(w, rid, "drywall_dust" if any(M.SOILS.get(k, {}).get("fine") for k in moved) else "dust", airborne)
    dest = deliver_debris(ctx, rid, toward, out_to, moved, pushed)
    ctx.data["seconds"] = 10.0 + len(chosen) * CELL_AREA / 0.2
    note = []
    if skipped:
        note.append("skipped {} cells ({})".format(len(skipped), ", ".join(sorted(set(skipped.values())))))
    if upwind:
        note.append("wind blew debris back")
    return "Swept {} cells into {}{}".format(len(chosen), dest, ("; " + "; ".join(note)) if note else "")


def settle_room_dust(w, rid, kind, grams):
    """Airborne dust settles on surfaces in the room."""
    targets = [e for e in sorted(w.s["entities"]) if w.s["entities"][e].get("location") == rid and
               (w.s["entities"][e].get("accepts_items") and w.s["entities"][e].get("surface")) and not S.is_floor(w.s["entities"][e])]
    if not targets:
        return
    each = grams / len(targets)
    for eid in targets:
        rec = w.ent(eid)
        S.add_face_soil(w, eid, S.primary_face(rec), kind, M.level_from_grams(kind, each, E.area_of(rec)))
    S.glog(w, "dust_cloud", room=rid, grams=round(grams, 2))


def deliver_debris(ctx, rid, toward, out_to, moved, pushed):
    w = ctx.w
    room = w.s["rooms"][rid]
    if out_to:
        if out_to == "edge":
            require(room.get("outdoor"), "Only outdoor areas have an edge to sweep off", "argument")
            organic = {k: v for k, v in moved.items() if k in M.ORGANIC_DEBRIS}
            litter = {k: v for k, v in moved.items() if k not in M.ORGANIC_DEBRIS}
            neighbour = next((r for r in sorted(room.get("exits", [])) if w.s["rooms"][r].get("outdoor") and w.s["rooms"][r].get("terrain") in ("grass", "soil")), None)
            if litter or pushed:
                target_room = neighbour or rid
                cell = geometry.free_floor_cell(w.s, target_room) or [0, 0]
                pile = add_pile(w, target_room, cell, litter or {"litter": 1.0})
                for item in pushed:
                    m = w.mut(item)
                    m["location"], m["pos"] = target_room, list(cell)
                S.glog(w, "litter_into_landscaping", room=target_room)
            S.glog(w, "organic_to_landscaping", grams=round(sum(organic.values()), 1))
            return "the landscaping off the edge"
        require(out_to in room.get("exits", []), "{} is not an adjacent room".format(out_to), "argument")
        cid = next((c for c, conn in sorted(w.s["connections"].items()) if set(conn["rooms"]) == {rid, out_to}), None)
        cell = w.s["connections"][cid]["cells"][out_to]
        other = w.s["rooms"][out_to]
        if other.get("outdoor") and other.get("terrain") in ("grass", "soil") and all(k in M.ORGANIC_DEBRIS for k in moved) and not pushed:
            S.glog(w, "organic_to_landscaping", grams=round(sum(moved.values()), 1))
            return "the {} (dispersed)".format(out_to)
        add_pile(w, out_to, cell, moved)
        for item in pushed:
            m = w.mut(item)
            m["location"], m["pos"] = out_to, list(cell)
        S.glog(w, "debris_swept_into_other_room", room=out_to)
        return "the doorway of " + out_to
    key = S.ck(toward)
    special = special_destination(w, rid, toward)
    if special:
        srec = w.ent(special)
        what = "debris"
        if srec.get("storm_drain") or srec.get("water_body"):
            S.pollute(w, what, special, sum(moved.values()) / 100.0, source="sweeping")
        elif srec.get("floor_vent"):
            m = w.mut(special)
            m["vent_debris_g"] = S.rnd(float(m.get("vent_debris_g", 0.0)) + sum(moved.values()), 1)
            S.glog(w, "debris_into_vent", vent=special)
        else:
            m = w.mut(special)
            m["clog"] = S.rnd(M.clamp(float(m.get("clog", 0.0)) + sum(moved.values()) / 500.0))
        for item in pushed:
            m = w.mut(item)
            m["location"], m["relation"] = special, "in"
            m.pop("pos", None)
            S.glog(w, "item_lost_down_drain", item=item)
        return special
    cover = cover_at(w, rid, key)
    if cover:
        pile = add_pile(w, rid, toward, moved, location=cover, relation="under")
        for item in pushed:
            m = w.mut(item)
            m["location"], m["relation"] = cover, "under"
            m.pop("pos", None)
            m.pop("revealed", None)
        S.glog(w, "debris_hidden_under", cover=cover)
        return "a pile under " + cover
    pile = add_pile(w, rid, toward, moved)
    for item in pushed:
        m = w.mut(item)
        m["pos"] = [int(toward[0]), int(toward[1])]
        m["relation"] = "on"
    return pile or "nothing (no loose debris)"


# ----------------------------------------------------------------------
# stairs

def tread_order(rec, faces, order):
    treads = [f for f in S.faces_of(rec) if f.startswith("t")]
    treads.sort(key=lambda f: int(f[1:]) if f[1:].isdigit() else 0)
    chosen = [f for f in treads if f in faces] if faces else treads
    return chosen if order != "bottom_up" else list(reversed(chosen))


def stairs_access(ctx, rec):
    w = ctx.w
    conn = w.s["connections"].get(rec.get("connection") or "")
    if conn:
        here = w.robot["room"]
        require(here in conn["rooms"], "Go to the top or bottom of those stairs", "precondition")
        require(geometry.chebyshev(tuple(w.robot.get("pos") or (0, 0)), tuple(conn["cells"][here])) <= 1,
                "Stand at the stairs first (approach them)", "precondition")


def stairs_pass(ctx, stairs, verb, tool=None):
    w = ctx.w
    rec = w.ent(stairs)
    stairs_access(ctx, rec)
    tool = tool or S.held_tool(ctx)
    faces = [f.strip() for f in str(ctx.args.get("face") or "").split(",") if f.strip()] or None
    order = ctx.args.get("order") or "top_down"
    treads = tread_order(rec, faces, order)
    require(treads, "Those stairs have no treads to clean", "argument")
    spec = S.tool_spec(w.ent(tool))
    into = ctx.args.get("into")
    S.ensure_faces(w, stairs)
    landing_debris = {}
    allt = tread_order(rec, None, "top_down")
    wetness = float(w.ent(tool).get("wetness", 0.0) or 0.0)
    for face in treads:
        soil = dict(S.face_soil(w.ent(stairs), face))
        part = S.mut_face(w, stairs, face)
        if verb in ("sweep", "vacuum"):
            eff = 0.9 if verb == "sweep" else 0.8
            moved = {}
            for kind, level in sorted(soil.items()):
                if kind in M.FLOOR_LOOSE:
                    took = level * eff
                    part[kind] = S.rnd(level - took)
                    moved[kind] = M.grams(kind, took, 0.25)
            S.sync(w, stairs)
            if verb == "sweep" and moved:
                if into:
                    from .surfaces import deposit_waste
                    deposit_waste(w, into, moved, source=stairs)
                else:
                    idx = allt.index(face)
                    if idx + 1 < len(allt):
                        below = allt[idx + 1]
                        for kind, grams in sorted(moved.items()):
                            S.add_face_soil(w, stairs, below, kind, M.level_from_grams(kind, grams, 0.25))
                    else:
                        for kind, grams in moved.items():
                            landing_debris[kind] = landing_debris.get(kind, 0.0) + grams
            elif verb == "vacuum" and moved:
                vacuum_collect(w, tool, moved)
        else:
            E.clean_face(ctx, stairs, face, "mop" if verb == "mop" else verb, tool=tool, wetness=max(wetness, 0.4), mech=float(spec.get("mech", 0.4)))
            if verb == "mop":
                m = w.mut(stairs)
                m.setdefault("cl_wet_treads", {})[face] = S.now(w) + 900.0
    if verb == "mop" and order == "bottom_up":
        S.gsec(w)["slips"] += 1
        S.glog(w, "stood_on_wet_tread", stairs=stairs)
        for face in treads:
            S.add_face_soil(w, stairs, face, "streaks", 0.3)
    if landing_debris:
        lower = rec.get("location")
        conn = w.s["connections"].get(rec.get("connection") or "")
        cell = conn["cells"][lower] if conn else (w.robot.get("pos") or [0, 0])
        add_pile(w, lower, cell, landing_debris)
    if verb == "vacuum" and ctx.args.get("vacuum_on") == "tread" and not S.label_has(w.ent(tool), "handheld") and S.tool_spec(w.ent(tool)).get("kind") != "handheld":
        S.damage(w, tool, "fell_down_stairs", 0.6, "vacuum balanced on a tread tumbled")
        w.stat("breakages")
    ctx.data["seconds"] = 15.0 + 20.0 * len(treads)
    return "{} {} treads ({})".format(verb.capitalize(), len(treads), order)


# ----------------------------------------------------------------------
# dustpan

@action("collect_debris", params={"target": "entity", "tool": "?entity", "passes": {"type": "int", "default": 1, "min": 1, "max": 5},
                                  "cells": "?list"},
        duration=lambda ctx: ctx.data.get("seconds", 8.0), energy=0.01, noise=0.02, category="physical",
        doc="Collect a debris pile (or loose debris on floor cells) into a held dustpan, with the broom or hand brush in the "
            "other hand. Each pass leaves the fine line at the pan lip; small objects in the pile go into the pan too.")
def collect_debris(ctx):
    w = ctx.w
    target = ctx.args["target"]
    pan = S.held_tool(ctx, predicate=lambda r: S.is_a(r, "dustpan") or r.get("dustpan"), message="Hold a dustpan")
    brush = next((h for h in w.held_items() if h != pan and S.tool_spec(w.ent(h)).get("kind") == "broom"), None)
    rec = w.s["entities"].get(target)
    require(rec is not None and ctx.visible(target), "That is not visible here", "perception")
    passes = ctx.args["passes"]
    eff = 0.85 if brush else 0.55
    capacity_g = float(w.ent(pan).get("capacity_g", 900.0))
    if rec.get("category") == "debris":
        if rec.get("relation") == "under":
            cover = w.ent(rec["location"])
            require(cover.get("category") == "rug" or S.clearance_m(cover) >= 0.08,
                    "The pile is under {}; sweep it out first".format(rec["location"] if S.realistic(ctx) else cover.get("label", rec["location"])),
                    "precondition")
        ctx.obj(target, touch=True)
        crouch_ok = w.robot.get("posture") in ("crouching", "kneeling") or float(w.ent(pan).get("handle_m", 0.0)) >= 0.8
        require(crouch_ok, "Crouch to hold the dustpan lip on the floor (or use a long-handled pan)", "precondition")
        comp = dict(rec.get("composition") or {})
        loaded = float(w.ent(pan).get("cl_load_g", 0.0))
        taken = {}
        for _ in range(passes):
            for kind in sorted(comp):
                grams = comp[kind] * eff
                room_left = max(0.0, capacity_g - loaded - sum(taken.values()))
                grams = min(grams, room_left)
                comp[kind] -= grams
                taken[kind] = taken.get(kind, 0.0) + grams
        from .surfaces import deposit_waste
        deposit_waste(w, pan, taken, source=target)
        if rec.get("hazardous_waste"):
            w.mut(pan)["hazardous_waste"] = True
        items = []
        if rec.get("pos"):
            items = items_on_cells(w, rec["location"], {S.ck(rec["pos"])}, max_size=0.2)
        elif rec.get("relation") == "under":
            items = items_under(w, [rec["location"]], max_size=0.2)
        for item in items:
            m = w.mut(item)
            m["location"], m["relation"] = pan, "in"
            m.pop("pos", None)
            w.log_move(item, rec.get("location"), pan)
        if target in w.s["entities"]:
            m = w.mut(target)
            m["composition"] = {k: S.rnd(v, 2) for k, v in comp.items() if v > 0.005}
            m["line"] = True
            refresh_pile(w, target)
        ctx.data["seconds"] = 5.0 + 4.0 * passes
        left = w.s["entities"].get(target)
        if S.realistic(ctx):
            return "Collected {} into the dustpan{}{}".format(S.grams_text(ctx, sum(taken.values())), "; a fine line is left" if left else "",
                                                             "; also scooped " + ", ".join(items) if items else "")
        return "Collected {:.1f} g into the dustpan{}{}".format(sum(taken.values()), "; {:.1f} g line left".format(left["mass_g"]) if left else "",
                                                             "; also scooped " + ", ".join(items) if items else "")
    require(S.is_floor(rec) and ctx.args.get("cells"), "Collect a debris pile, or name floor cells", "argument")
    chosen, _ = select_cells(ctx, target, {"head_m": 0.02, "reach_m": 0.3}, cells=ctx.args["cells"])
    taken = {}
    for key in chosen:
        soil = dict(S.cell_soil(w.ent(target), key))
        part = S.mut_cell(w, target, key)
        for kind, level in sorted(soil.items()):
            if kind in M.FLOOR_LOOSE:
                took = level * (1 - (1 - eff) ** passes)
                part[kind] = S.rnd(level - took)
                taken[kind] = taken.get(kind, 0.0) + M.grams(kind, took, CELL_AREA)
        S.sync(w, target)
    from .surfaces import deposit_waste
    deposit_waste(w, pan, taken, source=target)
    ctx.data["seconds"] = 5.0 + 4.0 * passes * len(chosen)
    return "Collected {} from {} cells".format(S.grams_text(ctx, sum(taken.values())), len(chosen))


# ----------------------------------------------------------------------
# vacuum

def vacuum_state(w, vac):
    rec = w.ent(vac)
    slots = rec.get("slots") or {}
    head = None
    tool_part = (slots.get("tool") or {}).get("item")
    if tool_part:
        head = w.ent(tool_part).get("head_kind") or w.ent(tool_part).get("type")
    if head not in M.VACUUM_HEADS:
        head = rec.get("default_head") or ("handheld_nozzle" if rec.get("handheld") else "floor_head")
    wand = bool((slots.get("wand") or {}).get("item"))
    container = (slots.get("bin") or {}).get("item") or (slots.get("bag") or {}).get("item")
    filt = (slots.get("filter") or {}).get("item")
    return head, wand, container, filt


def vacuum_suction(w, vac):
    rec = w.ent(vac)
    head, wand, container, filt = vacuum_state(w, vac)
    suction = {"low": 0.75, "medium": 0.9, "high": 1.0}.get((rec.get("settings") or {}).get("suction", "high"), 1.0)
    if container:
        crec = w.ent(container)
        fill = float(crec.get("cl_load_g", 0.0)) / max(1.0, float(crec.get("capacity_g", 250.0)))
        if fill > 0.7:
            suction *= max(0.2, 1.0 - (fill - 0.7) / 0.3 * 0.8)
        if crec.get("bag_seal") is False:
            suction *= 0.7
    if filt:
        suction *= 1.0 - 0.6 * float(w.ent(filt).get("clog", 0.0))
    wrap = float(rec.get("brushroll_wrap", 0.0) or 0.0)
    if wrap > 1.0:
        suction *= 0.7
    return suction


def vacuum_collect(w, vac, composition):
    head, wand, container, filt = vacuum_state(w, vac)
    if container:
        crec = w.ent(container)
        m = w.mut(container)
        load = m.setdefault("cl_load", {})
        for kind, grams in sorted(composition.items()):
            load[kind] = S.rnd(float(load.get(kind, 0.0)) + grams, 2)
        m["cl_load_g"] = S.rnd(sum(load.values()), 2)
        m["fill_level"] = S.rnd(M.clamp(m["cl_load_g"] / max(1.0, float(crec.get("capacity_g", 250.0)))), 3)
    if filt:
        fine = sum(g for k, g in composition.items() if k in ("dust", "drywall_dust", "pollen", "ash"))
        rate = 0.004 if w.ent(vac).get("vacuum_kind") != "shop" else 0.001
        if any(k in composition for k in ("drywall_dust",)) and w.ent(vac).get("vacuum_kind") != "shop":
            rate = 0.03
        if w.ent(vac).get("vacuum_kind") == "shop" and w.ent(filt).get("filter_kind") == "foam" and composition.get("drywall_dust", 0) + composition.get("dust", 0) > 1:
            S.glog(w, "fine_dust_through_wet_filter", vacuum=vac)
            return "exhaust"
        fm = w.mut(filt)
        fm["clog"] = S.rnd(M.clamp(float(fm.get("clog", 0.0)) + fine * rate))
    return None


def _ingest_text(ctx, outcome, item):
    """What the robot notices when the vacuum takes something: the item (oracle); realistic: a sound and a feel, not
    what it was - small things are often not seen before the head reaches them."""
    if not S.realistic(ctx):
        return "{} {}".format(outcome, item)
    return "something got caught in the brush roll (jammed)" if outcome == "jammed" else "something small rattled up the hose"


def ingest(w, vac, item, brushroll_on):
    rec = w.ent(item)
    label = "{} {} {}".format(rec.get("label", ""), rec.get("type", ""), rec.get("category", "")).lower()
    entangles = rec.get("entangles") or any(word in label for word in ("sock", "cable", "cord", "charger", "string", "ribbon", "shoelace", "hair tie"))
    head, wand, container, filt = vacuum_state(w, vac)
    if entangles:
        m = w.mut(vac)
        m["jammed"] = True
        S.damage(w, item, "chewed", 0.5, "caught in vacuum brush roll" if brushroll_on else "sucked into the nozzle")
        im = w.mut(item)
        im["location"], im["relation"] = vac, "in"
        im.pop("pos", None)
        S.glog(w, "vacuum_jammed", vacuum=vac, item=item)
        return "jammed"
    dest = container or vac
    im = w.mut(item)
    im["location"], im["relation"] = dest, "in"
    im.pop("pos", None)
    im["ingested"] = True
    S.gsec(w)["ingested"] += 1
    S.glog(w, "ingested", vacuum=vac, item=item)
    w.log_move(item, rec.get("location"), dest)
    return "ingested"


def noise_event(ctx, rid, db):
    """Loud cleaning noise: wakes sleepers (unless the people domain handles noise) and stresses pets."""
    from ...kernel.plugins import HOOKS, run_hooks
    w = ctx.w
    w.stat("noise", db / 100.0)
    S.glog(w, "noise", room=rid, db=db)
    if HOOKS.get("noise_event"):
        run_hooks("noise_event", w, rid, db)
    else:
        listeners = [(rid, db)]
        for other in S.adjacent_rooms(w, rid):
            cid = next((c for c, conn in sorted(w.s["connections"].items()) if set(conn["rooms"]) == {rid, other}), None)
            door = w.s["entities"].get(w.s["connections"][cid].get("door") or "") if cid else None
            listeners.append((other, db - (10 if door is None or door.get("open") else 25)))
        for room, level in listeners:
            for pid in S.people_in(w, room):
                person = w.s["people"][pid]
                if not person.get("awake", True) and level >= 55:
                    p = w.person_m(pid)
                    p["awake"] = True
                    p["mood"] = "annoyed"
                    p["woken_by"] = "cleaning noise"
                    S.glog(w, "woke_person", person=pid, db=level)
                    w.emit("woke", person=pid, by="cleaning noise")
                elif person.get("activity") in ("call", "meeting", "studying", "napping") and level >= 60:
                    p = w.person_m(pid)
                    p["mood"] = "annoyed"
                    S.glog(w, "disturbed_person", person=pid)
    for pid in S.pets_in(w, rid):
        pet = w.pet_m(pid)
        pet["anxiety"] = S.rnd(M.clamp(float(pet.get("anxiety", 0.0) or 0.0) + (0.4 if db >= 65 else 0.15)))
        if pet.get("species") == "cat" and db >= 65:
            pet["activity"] = "hiding"


@action("vacuum", params={"target": "entity", "tool": "?entity", "zone": "?str", "cells": "?list", "face": "?str",
                          "passes": {"type": "int", "default": 1, "min": 1, "max": 4}, "vacuum_on": "?str:landing|tread", "order": "?str:top_down|bottom_up"},
        duration=lambda ctx: ctx.data.get("seconds", 60.0), energy=0.04, noise=0.0, category="physical",
        doc="Vacuum a floor zone/cells, a rug, upholstery (faces: seat, crevices, back, under_cushions), a mattress, curtains, "
            "vents, stairs or a vehicle interior with a held vacuum. The fitted head/attachment (install crevice_tool, "
            "upholstery_tool, dust_brush, pet_tool, low_profile_head; settings: mode carpet|hard_floor, suction low|medium|high) "
            "decides reach under furniture, edges and fabrics. Small objects in the path are sucked up; cords and socks jam it.")
def vacuum(ctx):
    w = ctx.w
    target = ctx.args["target"]
    vac = S.held_tool(ctx, predicate=lambda r: bool(r.get("vacuum_kind")) or S.is_a(r, "vacuum_cleaner"), message="Hold a vacuum cleaner")
    vrec = w.ent(vac)
    require(not vrec.get("jammed"), "The vacuum is jammed; clear the brush roll first", "precondition")
    # realistic perception: a motor that cut out on heat and a broken one look the same from outside - it won't start
    require(float(vrec.get("thermal_cutoff_until", -1)) <= S.now(w),
            "The vacuum won't start" if S.realistic(ctx) else "The vacuum's motor overheated; let it cool", "resource")
    require(not vrec.get("broken"), "The vacuum won't start" if S.realistic(ctx) else "The vacuum is broken",
            "resource" if S.realistic(ctx) else "capability")
    if vrec.get("battery_powered"):
        require(float(vrec.get("battery_level", 1.0)) > 0.03, "The vacuum's battery is flat; charge it", "resource")
    elif vrec.get("plug_required"):
        require(physics.is_powered(w, vac), "The vacuum has no power (plug it in)", "resource")
    settings = vrec.get("settings") or {}
    suction_setting = settings.get("suction", "high")
    if w.robot.get("quiet"):
        require(suction_setting == "low", "Quiet mode is on: set suction low or use a broom/dust mop", "precondition")
    head, wand, container, filt = vacuum_state(w, vac)
    hspec = dict(M.VACUUM_HEADS.get(head, M.VACUUM_HEADS["floor_head"]))
    if wand:
        hspec["reach_m"] = hspec.get("reach_m", 0.35) + 0.6
    mode = settings.get("mode", "carpet")
    brushroll = mode == "carpet" and head in ("floor_head", "low_profile_head")
    rec = w.s["entities"].get(target)
    require(rec is not None, "That is not visible here", "perception")
    passes = ctx.args["passes"]
    rid = w.root_room(target) or w.robot["room"]
    db = {"low": 62, "medium": 68, "high": 74}.get(suction_setting, 72) + (3 if vrec.get("vacuum_kind") == "shop" else 0)
    consequences = []
    if not container:
        S.damage(w, vac, "motor_dust", 0.5, "run without bin or bag")
        consequences.append("no bin/bag: dust blew through the motor")
        drop_to_floor(w, w.robot["room"], {"dust": 30.0})
    if not filt and (vrec.get("slots") or {}).get("filter") is not None:
        S.damage(w, vac, "motor_dust", 0.4, "run without filter")
        consequences.append("no filter: motor exposed to dust")
    suction = vacuum_suction(w, vac)
    collected = {}
    if S.is_floor(rec) or S.is_stairs(rec):
        floor_target(ctx, target)
        if S.is_stairs(rec):
            result = stairs_pass(ctx, target, "vacuum", vac)
            noise_event(ctx, rid, db)
            return result
        chosen, skipped = select_cells(ctx, target, hspec, zone=ctx.args.get("zone"), cells=ctx.args.get("cells"))
        require(chosen, "Nothing reachable to vacuum ({})".format(", ".join(sorted(set(skipped.values()))) or "empty zone"), "precondition")
        carpet = is_carpet(rec)
        edges, corners = S.perimeter_cells(w, rid), S.corner_cells(w, rid)
        S.sync_wet(w, rid)
        room = w.s["rooms"][rid]
        wet_keys = set(room.get("water_film", {}))
        for spill in [e for e in sorted(w.s["entities"]) if w.s["entities"][e].get("category") == "spill" and w.s["entities"][e].get("location") == rid]:
            if set(spill_cells(w, spill)) & set(chosen):
                wet_keys |= set(spill_cells(w, spill))
        if wet_keys & set(chosen) and not vrec.get("wet_dry"):
            S.damage(w, vac, "water_in_motor", 0.7, "vacuumed water")
            if vrec.get("plug_required"):
                w.stat("electrical_hazard")
            consequences.append("sucked up water")
        S.reconcile(w, target)
        layout = S.floor_layout(w, rid)
        base = hspec["carpet"] * (0.72 if mode == "carpet" else 0.3) if carpet else hspec["hard"] * (0.92 if mode == "hard_floor" else 0.7)
        for key in chosen:
            soil = dict(S.cell_soil(w.ent(target), key))
            part = S.mut_cell(w, target, key)
            factor = base * suction
            narrow = head in ("crevice_tool", "dust_brush")
            if key in corners:
                factor = (0.85 * hspec["edge"] * suction) if narrow else factor * hspec["edge"] * 0.7
            elif key in edges:
                factor = (0.85 * hspec["edge"] * suction) if narrow else factor * hspec["edge"]
            for kind, level in sorted(soil.items()):
                if kind not in M.FLOOR_LOOSE and kind not in ("dirt",):
                    continue
                eff = factor * (0.8 if kind == "pet_hair" and head != "pet_tool" else 1.0)
                left = level * (1.0 - eff) ** passes
                part[kind] = S.rnd(left)
                collected[kind] = collected.get(kind, 0.0) + M.grams(kind, level - left, CELL_AREA)
                if not carpet and mode == "carpet" and kind in ("crumbs", "sand", "litter", "cat_litter"):
                    scatter = (level - left) * 0.15
                    neighbours = sorted(geometry.near_cells([S.pk(key)], room) - {S.pk(key)})
                    if neighbours:
                        S.mut_cell(w, target, S.ck(neighbours[0]))[kind] = S.rnd(M.clamp(S.cell_soil(w.ent(target), S.ck(neighbours[0])).get(kind, 0.0) + scatter))
            if not carpet and mode == "carpet" and float(soil.get("sand", 0.0)) > 0.15 and M.material(rec).get("abrasion", 1.0) < 0.3:
                S.damage(w, target, "scratched", 0.2, "beater bar dragged grit over the finish")
            S.sync(w, target)
        small = items_on_cells(w, rid, set(chosen), max_size=0.4)
        under_ids = sorted({layout[k][1] for k in chosen if layout.get(k, ("open",))[0] == "under"})
        small += items_under(w, under_ids, max_size=0.4)
        for item in small:
            size = w.ent(item).get("size_m") or [0.1, 0.1, 0.1]
            if max(float(v) for v in size[:3]) <= 0.06 or w.ent(item).get("entangles") or S.label_has(w.ent(item), "sock", "cable", "cord", "charger"):
                outcome = ingest(w, vac, item, brushroll)
                consequences.append(_ingest_text(ctx, outcome, item))
        for pid in S.pets_in(w, rid):
            pet = w.s["pets"][pid]
            hidden_under = pet.get("hiding_under")
            if hidden_under and hidden_under in under_ids:
                w.stat("safety_violations")
                w.pet_m(pid)["anxiety"] = 1.0
                consequences.append("something hiding under {} scrambled away".format(hidden_under) if S.realistic(ctx)
                                    else "poked the pet hiding under " + hidden_under)
        area = len(chosen) * CELL_AREA
        hair = collected.get("hair", 0.0) + collected.get("pet_hair", 0.0)
        if brushroll and hair > 0:
            m = w.mut(vac)
            m["brushroll_wrap"] = S.rnd(float(m.get("brushroll_wrap", 0.0) or 0.0) + hair / 25.0, 3)
        if any(k in collected for k in ("drywall_dust",)) and vrec.get("vacuum_kind") != "shop":
            S.damage(w, vac, "motor_dust", 0.4, "fine construction dust in a household vacuum")
            settle_room_dust(w, rid, "drywall_dust", collected["drywall_dust"] * 0.3)
        ctx.data["seconds"] = 15.0 + area / 0.25 * passes
        exhaust = vacuum_collect(w, vac, collected)
        if exhaust:
            settle_room_dust(w, rid, "dust", sum(collected.values()) * 0.3)
            consequences.append("fine dust blew out of the exhaust")
        summary = "Vacuumed {} cells x{} ({} {})".format(len(chosen), passes, head, mode)
        if skipped:
            summary += "; skipped {} ({})".format(len(skipped), ", ".join(sorted(set(skipped.values()))))
    else:
        summary = vacuum_object(ctx, vac, target, head, mode, brushroll, suction, passes, consequences)
    wrap = float(w.ent(vac).get("brushroll_wrap", 0.0) or 0.0)
    if wrap > 2.0:
        m = w.mut(vac)
        m["thermal_cutoff_until"] = S.now(w) + 1800.0
        S.damage(w, vac, "motor_overheated", 0.25, "hair-wrapped brush roll")
        consequences.append("motor overheated and cut out")
    if vrec.get("battery_powered"):
        m = w.mut(vac)
        m["battery_level"] = S.rnd(max(0.0, float(m.get("battery_level", 1.0)) - ctx.data.get("seconds", 60.0) / 2400.0), 3)
    noise_event(ctx, rid, db)
    return summary + ("; " + "; ".join(consequences) if consequences else "")


def vacuum_object(ctx, vac, target, head, mode, brushroll, suction, passes, consequences):
    """Rugs, upholstery, mattresses, curtains, vents, baseboards, shelves, vehicle interiors."""
    w = ctx.w
    rec = E.reach_target(ctx, target, vac)
    hspec = M.VACUUM_HEADS.get(head, M.VACUUM_HEADS["floor_head"])
    info = M.material(rec)
    faces = E.parse_faces(ctx, rec, ctx.args.get("face"), "vacuum")
    textile = info.get("textile") or rec.get("upholstered") or rec.get("category") in ("rug", "upholstery", "mattress", "curtain")
    delicate = rec.get("delicate") or info.get("delicate") or rec.get("pile") == "shag"
    collected = {}
    is_rug = rec.get("category") == "rug"
    for face in faces:
        E.check_face_access(ctx, target, face)
        if face in ("under_cushions",):
            cushions = [c for c in w.children(target) if w.ent(c).get("cushion") and w.ent(c).get("relation") in ("on", "part")]
            require(not cushions, "Lift the cushions off first", "precondition")
        crevice = face in ("crevices", "seams", "edges", "vents")
        coupling = hspec["crevice"] if crevice else (hspec["fabric"] if textile and not is_rug else hspec["carpet" if is_rug else "hard"])
        if is_rug:
            coupling *= 0.72 if mode == "carpet" else 0.35
        if textile and not is_rug and head == "floor_head":
            S.add_face_soil(w, target, face, "dirt", 0.25)
            consequences.append("dirty floor head smeared dirt onto the fabric")
            S.damage(w, target, "pilled", 0.3 if delicate else 0.15, "beater bar on upholstery")
        if delicate and brushroll and head in ("floor_head",):
            S.damage(w, target, "fiber_damage", 0.4, "beater bar on a delicate rug")
        if is_rug and rec.get("fringe") and brushroll and face in ("fringe", "main", "top") and not ctx.args.get("face"):
            S.damage(w, target, "fringe_tangled", 0.4, "brush roll over the fringe")
            consequences.append("fringe tangled in the brush roll")
        if is_rug and float(rec.get("mass_kg", 4.0)) < 1.5 and not rec.get("non_slip_pad") and (w.ent(vac).get("settings") or {}).get("suction", "high") == "high":
            w.mut(target)["rippled"] = True
            w.mut(target)["floor_hazard"] = "trip"
            consequences.append("lightweight rug bunched up")
        if rec.get("sheer") and (w.ent(vac).get("settings") or {}).get("suction", "high") != "low":
            S.damage(w, target, "sucked_in", 0.4, "sheer fabric sucked into the nozzle")
        if face == "interior" and (rec.get("electronic") or rec.get("vents")):
            S.damage(w, target, "static_discharge", 0.4, "vacuum inside electronics")
        soil = dict(S.face_soil(w.ent(target), face))
        part = S.mut_face(w, target, face)
        for kind, level in sorted(soil.items()):
            if M.soil_info(kind)["cls"] != "loose" and kind != "dirt":
                continue
            eff = coupling * suction * (1.25 if kind == "pet_hair" and head == "pet_tool" else (0.6 if kind == "pet_hair" else 1.0))
            eff = min(0.95, eff)
            left = level * (1.0 - eff) ** passes
            part[kind] = S.rnd(left)
            collected[kind] = collected.get(kind, 0.0) + M.grams(kind, level - left, E.area_of(rec))
        S.sync(w, target)
        contents = [c for c in w.children(target) if w.ent(c).get("relation") in ("in",) and w.ent(c).get("portable")]
        for item in contents:
            size = w.ent(item).get("size_m") or [0.1, 0.1, 0.1]
            if crevice and max(float(v) for v in size[:3]) <= 0.06:
                consequences.append(_ingest_text(ctx, ingest(w, vac, item, brushroll), item))
    vacuum_collect(w, vac, collected)
    area = E.area_of(rec)
    ctx.data["seconds"] = 20.0 + len(faces) * area / 0.2 * passes
    return "Vacuumed {} ({}; {})".format(target, ", ".join(faces), head)


# ----------------------------------------------------------------------
# mop

def find_bucket(ctx):
    w = ctx.w
    explicit = ctx.args.get("bucket")
    if explicit:
        rec = ctx.obj(explicit, touch=True) if explicit not in w.held_items() else w.ent(explicit)
        require(float(rec.get("liquid_l", 0.0) or 0.0) > 0.2, "The bucket is (nearly) empty", "resource")
        return explicit
    for eid in w.held_items():
        rec = w.ent(eid)
        if (S.is_a(rec, "mop_bucket") or rec.get("mop_bucket")) and float(rec.get("liquid_l", 0.0) or 0.0) > 0.2:
            return eid
    rid = w.robot["room"]
    for eid in sorted(w.s["entities"]):
        rec = w.s["entities"][eid]
        if (S.is_a(rec, "mop_bucket") or rec.get("mop_bucket")) and w.root_room(eid) == rid and float(rec.get("liquid_l", 0.0) or 0.0) > 0.2:
            if geometry.reach_problem(w.s, eid) is None:
                return eid
    return None


@action("mop", params={"target": "entity", "tool": "?entity", "zone": "?str", "cells": "?list", "bucket": "?entity",
                       "wetness": "?str:dry|barely_damp|damp|wet", "face": "?str", "order": "?str:top_down|bottom_up",
                       "parked_s": {"type": "number", "min": 0, "max": 600, "optional": True}},
        duration=lambda ctx: ctx.data.get("seconds", 60.0), energy=0.04, noise=0.03, category="physical",
        doc="Mop floor cells/zone (or stairs, or a rug if you insist) with a held mop. Wet mops are dipped and wrung in a "
            "bucket within reach (bucket=...) to the chosen wetness; the solution's chemistry acts on film/sticky soils; loose "
            "debris turns to mud; dirty water redeposits; the cell you stand on is left for last (finish from the doorway of "
            "the next room). Leaves a water film that must dry. Dust mops collect dry dust and hair; steam mops heat.")
def mop(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, kinds=("mop",), message="Hold a mop")
    trec = w.ent(tool)
    spec = S.tool_spec(trec)
    rec = w.s["entities"].get(target)
    require(rec is not None, "That is not visible here", "perception")
    if S.is_stairs(rec):
        floor_target(ctx, target)
        return stairs_pass(ctx, target, "mop", tool)
    if rec.get("category") == "rug" or (M.material(rec).get("textile") and not S.is_floor(rec)):
        ctx.obj(target, touch=True)
        m = w.mut(target)
        m["wetness"] = 0.85
        S.damage(w, target, "soaked", 0.45, "mopped a rug")
        ctx.data["seconds"] = 20.0
        return "Mopped the rug; it is soaked"
    floor_target(ctx, target)
    rid = rec["location"]
    dry_mop = spec.get("dry") or ctx.args.get("wetness") == "dry"
    steam = bool(spec.get("steam"))
    wetness_name = ctx.args.get("wetness") or ("dry" if dry_mop else ("barely_damp" if steam else "damp"))
    chosen, skipped = select_cells(ctx, target, spec, zone=ctx.args.get("zone"), cells=ctx.args.get("cells"), for_mop=not dry_mop)
    require(chosen, "Nothing reachable to mop ({})".format(", ".join(sorted(set(skipped.values()))) or "empty zone"), "precondition")
    key_mat = floor_material(rec)
    info = M.material(rec)
    if dry_mop:
        taken = {}
        for key in chosen:
            soil = dict(S.cell_soil(w.ent(target), key))
            part = S.mut_cell(w, target, key)
            for kind, level in sorted(soil.items()):
                if kind in ("dust", "hair", "pet_hair", "lint", "crumbs", "sand", "drywall_dust", "pollen"):
                    eff = 0.88 if kind not in ("crumbs", "sand") else 0.5
                    part[kind] = S.rnd(level * (1 - eff))
                    taken[kind] = taken.get(kind, 0.0) + M.grams(kind, level * eff, CELL_AREA)
                if kind in ("sticky",) and level > 0.1:
                    part["streaks"] = S.rnd(M.clamp(part.get("streaks", 0.0) + 0.2))
            S.sync(w, target)
        m = w.mut(tool)
        load = m.setdefault("cl_load", {})
        for kind, grams in sorted(taken.items()):
            load[kind] = S.rnd(float(load.get(kind, 0.0)) + grams, 2)
        m["cl_load_g"] = S.rnd(sum(load.values()), 2)
        ctx.data["seconds"] = 10.0 + len(chosen) * CELL_AREA / 0.3
        return "Dust-mopped {} cells ({} in the head)".format(len(chosen), S.grams_text(ctx, sum(taken.values())))
    bucket = None
    wet = M.WETNESS_LEVEL.get(wetness_name, 0.4)
    film = M.MOP_FILM_MM.get(wetness_name, 0.035)
    consequences = []
    if steam:
        require(physics.is_powered(w, tool), "The steam mop has no power", "resource")
        require(float(trec.get("water_l", trec.get("liquid_l", 0.0)) or 0.0) > 0.02, "Fill the steam mop's tank", "resource")
        film = 0.012
        if info.get("steam") is False or key_mat in ("laminate", "unsealed_wood", "oiled_wood", "cork", "hardwood", "sealed_wood") or rec.get("unsealed"):
            S.damage(w, target, "warped", 0.45, "steam on a moisture-sensitive floor")
            consequences.append("steam warped the floor")
        if float(ctx.args.get("parked_s") or 0.0) > 5.0:
            S.damage(w, target, "heat_mark", 0.4, "steam mop parked on the floor")
        tm = w.mut(tool)
        tm["temperature_c"] = 90.0
        pad = ((trec.get("slots") or {}).get("pad") or {}).get("item")
        if pad:
            w.mut(pad)["temperature_c"] = 90.0
    else:
        bucket = find_bucket(ctx)
        if bucket is None:
            pad_water = float(trec.get("wetness", 0.0) or 0.0)
            require(pad_water >= 0.15, "No bucket of water within reach and the mop is dry", "resource")
            wet = min(wet, pad_water)
        else:
            S.norm_solution(w, bucket)
    sources = floor_sources(w, target, bucket=bucket, tool=tool)
    mech = float(spec.get("mech", 0.45)) * (1.15 if steam else 1.0)
    removed_total = {}
    room = w.s["rooms"][rid]
    turb = S.turbidity(w.ent(bucket)) if bucket else 0.0
    dirty_mop = float(trec.get("soil_load", 0.0) or 0.0) > 0.8 and not bucket
    pasted = []
    for key in chosen:
        soil = dict(S.cell_soil(w.ent(target), key))
        crumbs = sum(v for k, v in soil.items() if k in ("crumbs", "cat_litter", "glitter", "sawdust"))
        grit = sum(v for k, v in soil.items() if k in ("dirt", "sand", "salt", "leaves", "litter", "drywall_dust"))
        if crumbs + grit > 0.1:
            # wet mop over loose debris: it clumps, smears into paste/mud and clogs the pad
            part = S.mut_cell(w, target, key)
            if crumbs:
                part["food"] = S.rnd(M.clamp(part.get("food", 0.0) + crumbs * 0.8))
            if grit:
                part["mud"] = S.rnd(M.clamp(part.get("mud", 0.0) + grit * 0.8))
            part["streaks"] = S.rnd(M.clamp(part.get("streaks", 0.0) + 0.4))
            for kind in [k for k in part if k in M.FLOOR_LOOSE]:
                part[kind] = S.rnd(part[kind] * 0.5)
            S.sync(w, target)
            pasted.append(key)
    if pasted:
        tm = w.mut(tool)
        tm["soil_load"] = S.rnd(M.clamp(float(tm.get("soil_load", 0.0) or 0.0) + 0.3 * len(pasted)))
        consequences.append("loose debris turned to paste/mud under the mop ({} cells) - sweep first".format(len(pasted)))
    removed = work_cells(ctx, target, chosen, "mop", sources, mech, wet, tool=tool,
                         kinds=[k for k in M.SOILS if k not in M.FLOOR_LOOSE] + ["dust", "hair", "pet_hair", "lint"])
    for kind, amount in removed.items():
        removed_total[kind] = removed_total.get(kind, 0.0) + amount
    if turb > 12.0 or dirty_mop:
        for key in chosen:
            S.add_cell_soil(w, target, key, "streaks", 0.25)
            S.add_cell_soil(w, target, key, "dirt", min(0.3, turb / 100.0))
        consequences.append("dirty water redeposited soil")
    over = 0.0
    if bucket:
        bucket_classes = S.solution_classes(w.ent(bucket))
        E.check_chemicals(w, target, bucket_classes, "mop solution")
        for product in sorted(w.ent(bucket).get("cl_chem") or {}):
            strength = S.strength_of(w.ent(bucket), product)
            if M.PRODUCTS.get(product, {}).get("concentrate") and strength > 2.0:
                over = strength
        if over:
            for key in chosen:
                S.add_cell_soil(w, target, key, "sticky", 0.15 * min(over, 5.0))
            consequences.append("over-concentrated solution left a sticky film")
    for key in chosen:
        S.set_film(w, rid, key, S.film_of(w, rid, key) + film)
    limit = float(info.get("water", 0.5))
    if is_carpet(rec):
        S.damage(w, target, "soaked_carpet", 0.5, "mopped carpet")
        m = w.mut(target)
        m["moisture_inner"] = 0.8
        m["moisture_since"] = S.now(w)
    elif film > limit * 1.5:
        S.damage(w, target, "water_damage", 0.25, "too much water for {}".format(key_mat))
        consequences.append("too wet for {}".format("this floor" if S.realistic(ctx) else key_mat))
    area = len(chosen) * CELL_AREA
    if bucket:
        bm = w.mut(bucket)
        used = film * area * 1.3 + 0.1
        bm["liquid_l"] = S.rnd(max(0.0, float(bm.get("liquid_l", 0.0)) - used), 4)
        grams = sum(M.grams(k, v, CELL_AREA) for k, v in removed_total.items())
        bm["cl_soil_g"] = S.rnd(float(bm.get("cl_soil_g", 0.0) or 0.0) + grams, 2)
        S.norm_solution(w, bucket)
        bm = w.mut(bucket)
        if float(bm.get("liquid_l", 0.0)) <= 0:
            bm["cl_chem"] = {}
        tm = w.mut(tool)
        tm["wetness"] = S.rnd(wet)
        tm["soil_load"] = S.rnd(M.clamp(float(tm.get("soil_load", 0.0) or 0.0) * 0.3 + turb / 60.0))
    else:
        tm = w.mut(tool)
        tm["wetness"] = S.rnd(max(0.0, float(tm.get("wetness", 0.0) or 0.0) - area * 0.1))
    S.zone_transfer(w, tool, target)
    ctx.data["seconds"] = 15.0 + area / 0.2 + (8.0 * math.ceil(area / 4.0) if bucket else 0.0)
    summary = "Mopped {} cells {} ({})".format(len(chosen), wetness_name, S.removed_text(ctx, removed_total, empty="no soil"))
    if skipped:
        summary += "; left {} cells ({})".format(len(skipped), ", ".join(sorted(set(skipped.values()))))
    return summary + ("; " + "; ".join(consequences) if consequences else "")


# ----------------------------------------------------------------------
# absorb

@action("absorb", params={"target": "entity", "tool": "?entity", "method": "?str:blot|rub", "sheets": {"type": "int", "min": 1, "max": 20, "optional": True},
                          "cells": "?list"},
        duration=lambda ctx: ctx.data.get("seconds", 10.0), energy=0.01, category="physical",
        doc="Soak up a liquid spill (or water film on floor cells, or moisture in upholstery/rugs) with a held towel, rag, "
            "paper towels (sheets), sponge or mop by blotting; or pour/spread absorbent granules from a held container over oil "
            "or antifreeze (then sweep and bag them). Rubbing spreads the spill and drives it into carpet.")
def absorb(ctx):
    w = ctx.w
    target = ctx.args["target"]
    tool = S.held_tool(ctx, predicate=lambda r: S.tool_spec(r).get("kind") in ("towel", "cloth", "paper", "sponge", "mop", "wipe") or bool(r.get("product") == "oil_absorbent" or r.get("absorbent")),
                       message="Hold an absorbent towel, paper towels, sponge, mop or absorbent granules")
    trec = w.ent(tool)
    rec = w.s["entities"].get(target)
    require(rec is not None and ctx.visible(target), "That is not visible here", "perception")
    method = ctx.args.get("method") or "blot"
    if trec.get("protected") or trec.get("good_towel"):
        S.gsec(w)["rule_violations"] += 1
        S.glog(w, "used_protected_textile", tool=tool, on=target)
        if rec.get("category") == "spill" and (rec.get("residue") or {}):
            S.damage(w, tool, "stained", 0.5, "good towel used on a spill")
    if trec.get("product") == "oil_absorbent" or trec.get("absorbent"):
        if rec.get("category") == "spill":
            ctx.obj(target, touch=True)
            amount = min(float(trec.get("amount_ml", 1500.0)), max(250.0, float(rec.get("liquid_l", 0.2)) * 1300.0))
            w.mut(tool)["amount_ml"] = S.rnd(max(0.0, float(trec.get("amount_ml", 1500.0)) - amount), 1)
            loc = rec["location"]
            floor = S.floor_of(w, loc) if loc in w.s["rooms"] else None
            require(floor is not None, "Spread granules on floor spills", "precondition")
            absorb_with_granules(ctx, floor, spill_cells(w, target), amount)
            ctx.data["seconds"] = 20.0
            return "Covered the spill with absorbent; sweep up the granules"
        raise ActionError("Spread granules over a spill", "argument")
    if rec.get("category") == "spill":
        ctx.obj(target, touch=True)
        return soak_up(ctx, target, tool, method=method)
    if S.is_floor(rec):
        return clean_floor_cells(ctx, target, "dry")
    ctx.obj(target, touch=True)
    moisture = float(rec.get("moisture_inner", 0.0) or 0.0)
    wetness = float(rec.get("wetness", 0.0) or 0.0)
    spec = S.tool_spec(trec)
    capacity = max(0.02, float(spec.get("absorb_l", 0.2))) * max(0.0, 1.0 - float(trec.get("wetness", 0.0) or 0.0))
    m = w.mut(target)
    if moisture > 0:
        m["moisture_inner"] = S.rnd(max(0.0, moisture - capacity * 1.5))
    m["wetness"] = S.rnd(max(0.0, wetness - capacity * 2.0))
    tm = w.mut(tool)
    tm["wetness"] = S.rnd(M.clamp(float(tm.get("wetness", 0.0) or 0.0) + 0.5))
    if method == "rub" and M.material(rec).get("textile"):
        for face in S.faces_of(rec) or ["main"]:
            for kind in M.STAINS:
                level = float(S.face_soil(w.ent(target), face).get(kind, 0.0))
                if level > 0.02:
                    S.add_face_soil(w, target, face, kind, level * 0.3)
    ctx.data["seconds"] = 15.0
    if S.realistic(ctx):
        # pressing a towel into it is touching it: the surface wetness to about +-0.04, the padding only as a feel
        ctx.grant(target, "touch", ["wetness"])
        inner = float(w.ent(target).get("moisture_inner", 0.0) or 0.0)
        return "Blotted {} ({}; padding feels {})".format(target, S.wet_text(ctx, target, float(w.ent(target).get("wetness", 0.0) or 0.0)),
                                                          "dry" if inner < 0.05 else ("damp" if inner < 0.3 else "soaked"))
    return "Blotted {} (moisture {:.2f}, wetness {:.2f})".format(target, float(w.ent(target).get("moisture_inner", 0.0)), float(w.ent(target).get("wetness", 0.0)))


# ----------------------------------------------------------------------
# shake out / beat

@action("shake_out", params={"target": "entity", "method": "?str:shake|beat", "tool": "?entity", "against": "?entity",
                             "force": "?str:light|normal|hard"},
        duration=lambda ctx: ctx.data.get("seconds", 25.0), energy=0.02, noise=0.2, category="physical",
        doc="Shake out a held rug, doormat, car mat, dust-mop head, tablecloth or cushion cover (method=shake), or beat a heavy "
            "mat draped over a railing with a rug beater or against a sturdy surface (method=beat, against=...). Released dirt "
            "lands where you are: indoors it re-soils the room; outdoors upwind or near an open door it blows back inside. "
            "Shaking a new folded trash liner opens it.")
def shake_out(ctx):
    w = ctx.w
    target = ctx.args["target"]
    method = ctx.args.get("method") or "shake"
    rec = w.s["entities"].get(target)
    require(rec is not None, "That is not visible here", "perception")
    if rec.get("liner") or S.is_a(rec, "trash_liner"):
        require(target in w.held_items(), "Hold the liner", "precondition")
        m = w.mut(target)
        m["opened"] = True
        m["folded"] = False
        ctx.data["seconds"] = 4.0
        return "Shook the liner open"
    if method == "shake":
        require(target in w.held_items(), "Hold it to shake it", "precondition")
        size = rec.get("size_m") or [1, 1, 0.01]
        require(float(rec.get("mass_kg", 1.0)) <= 6.0 and max(float(size[0]), float(size[1])) <= 1.6,
                "Too big and heavy to shake; vacuum it or beat it over a railing", "capability")
    else:
        against = ctx.args.get("against")
        beater = ctx.args.get("tool")
        require(against or beater, "Beat it with a rug beater (tool) or against a sturdy surface (against)", "argument")
        if beater:
            require(beater in w.held_items(), "Hold the rug beater", "precondition")
        if not (target in w.held_items()):
            ctx.obj(target, touch=True)
        if against:
            arec = ctx.obj(against, touch=True)
            if arec.get("category") == "wall" or M.material_key(arec) in ("painted_flat", "painted", "drywall", "painted_satin"):
                S.add_face_soil(w, against, S.primary_face(arec), "dirt", 0.4)
                S.glog(w, "dirt_marks_on_wall", wall=against)
            if arec.get("vehicle") or S.label_has(arec, "car"):
                S.damage(w, against, "scratched", 0.3, "mat beaten against the car")
        if ctx.args.get("force") == "hard" and M.material_key(rec) in ("coir",):
            S.damage(w, target, "fiber_damage", 0.4, "beaten too hard")
    released = {}
    faces = S.faces_of(rec) or ["main"]
    if len(faces) > 1:
        S.ensure_faces(w, target)
    for face in faces:
        soil = dict(S.face_soil(w.ent(target), face))
        if not soil:
            continue
        part = S.mut_face(w, target, face) if S.has_parts(w.ent(target)) else None
        new = dict(soil)
        for kind, level in sorted(soil.items()):
            if M.soil_info(kind)["cls"] != "loose" and kind not in ("dirt", "mud"):
                continue
            eff = 0.7 if method == "shake" else 0.88
            if kind in ("dirt", "mud") and method == "shake" and M.material_key(rec) in ("coir",):
                eff = 0.4
            new[kind] = S.rnd(level * (1 - eff))
            released[kind] = released.get(kind, 0.0) + M.grams(kind, level * eff, E.area_of(rec))
        if part is not None:
            part.clear()
            part.update({k: v for k, v in new.items() if v > 0.0005})
            S.sync(w, target)
        else:
            w.mut(target)["soil"] = {k: v for k, v in new.items() if v > 0.0005}
    rid = w.robot["room"]
    room = w.s["rooms"][rid]
    if not room.get("outdoor"):
        drop_to_floor(w, rid, released)
        settle_room_dust(w, rid, "dust", released.get("dust", 0.0) * 0.5 + 2.0)
        where = "inside, re-soiling the room"
    else:
        wind, speed = wind_to(w)
        blew_in = None
        for cid, conn in sorted(w.s["connections"].items()):
            if rid not in conn["rooms"]:
                continue
            other = conn["rooms"][1] if conn["rooms"][0] == rid else conn["rooms"][0]
            if w.s["rooms"][other].get("outdoor"):
                continue
            door = w.s["entities"].get(conn.get("door") or "")
            if door is not None and not door.get("open"):
                continue
            cell = conn["cells"][rid]
            rpos = w.robot.get("pos") or [0, 0]
            vec = (cell[0] - rpos[0], cell[1] - rpos[1])
            close = geometry.chebyshev(tuple(rpos), tuple(cell)) <= 3
            downwind = speed >= 2.0 and (vec[0] * wind[0] + vec[1] * wind[1]) > 0
            if close or downwind and geometry.chebyshev(tuple(rpos), tuple(cell)) <= 6:
                blew_in = other
                break
        if blew_in:
            drop_to_floor_near_door(w, blew_in, rid, {k: v * 0.4 for k, v in released.items()})
            where = "outside, but it blew in through the open door to " + blew_in
        else:
            where = "outside"
    for pid in S.people_in(w, rid):
        person = w.s["people"][pid]
        if person.get("pos") and geometry.chebyshev(tuple(person["pos"]), tuple(w.robot.get("pos") or (0, 0))) <= 2:
            w.person_m(pid)["mood"] = "annoyed"
            S.glog(w, "dust_on_person", person=pid)
    ctx.data["seconds"] = 20.0 if method == "shake" else 45.0
    return "{} {} {} ({} released)".format("Shook out" if method == "shake" else "Beat", target, where, S.grams_text(ctx, sum(released.values()), "{:.0f} g"))


def drop_to_floor_near_door(w, rid, from_room, composition):
    cid = next((c for c, conn in sorted(w.s["connections"].items()) if set(conn["rooms"]) == {rid, from_room}), None)
    floor = S.floor_of(w, rid)
    if not floor or cid is None:
        return
    key = S.ck(w.s["connections"][cid]["cells"][rid])
    for kind, grams in sorted(composition.items()):
        S.add_cell_soil(w, floor, key, kind, M.level_from_grams(kind, grams, CELL_AREA))


# ----------------------------------------------------------------------
# liquids out of buckets etc.

def dump_liquid(ctx, container, dest):
    """Pour a container's liquid into a destination with consequences by destination type."""
    w = ctx.w
    S.norm_solution(w, container)
    rec = w.ent(container)
    liters = float(rec.get("liquid_l", 0.0) or 0.0)
    if liters <= 0:
        return "nothing to pour"
    classes = S.solution_classes(rec)
    dirty = S.turbidity(rec) > 2.0 or rec.get("liquid") in ("dirty_water", "garbage_juice", "mop_water")
    strong = any(classes.get(c, 0) >= 0.2 for c in ("chlorine", "ammonia", "acid", "alkali_strong", "solvent"))
    drec = w.ent(dest)
    outcome = "poured {} into {}".format(S.liters_text(ctx, liters, 1), dest)
    if S.is_liquid_container(drec) and not drec.get("drain"):
        hazard = M.incompatibility(classes, S.solution_classes(drec))
        if hazard:
            S.gas_event(w, w.root_room(dest), hazard, dest)
        dm = w.mut(dest)
        dm["liquid_l"] = S.rnd(float(drec.get("liquid_l", 0.0) or 0.0) + liters, 4)
        chem = dm.setdefault("cl_chem", {})
        for product, ml in sorted((rec.get("cl_chem") or {}).items()):
            chem[product] = S.rnd(float(chem.get(product, 0.0)) + float(ml), 2)
        dm["cl_soil_g"] = S.rnd(float(dm.get("cl_soil_g", 0.0) or 0.0) + float(rec.get("cl_soil_g", 0.0) or 0.0), 2)
        dm["_cl_ref_l"] = dm["liquid_l"]
    elif drec.get("storm_drain") or drec.get("water_body"):
        if dirty or strong or rec.get("cl_chem"):
            S.pollute(w, "wastewater", dest, liters, source=container)
            outcome += " (polluting)"
    elif S.is_floor(drec):
        room = w.s["rooms"][drec["location"]]
        if room.get("outdoor") and room.get("terrain") in ("grass", "soil", "gravel", "mulch") or M.material_key(drec) in ("grass", "soil"):
            if strong:
                S.pollute(w, "chemicals_on_plants", dest, liters, source=container)
                outcome += " (chemicals on the lawn/beds)"
        else:
            make_spill(w, drec["location"], "dirty_water" if dirty else (rec.get("liquid") or "water"), liters, near=container)
            w.stat("spills")
            outcome += " (onto the floor)"
    elif drec.get("drain") or drec.get("toilet") or drec.get("floor_drain"):
        hazard = M.incompatibility(classes, S.residue_of(drec))
        if hazard:
            S.gas_event(w, w.root_room(dest), hazard, dest)
        if drec.get("water_source") and not drec.get("toilet"):
            from .surfaces import sink_zone
            if sink_zone(w, dest) == "kitchen" and (dirty or rec.get("mop_bucket") or S.is_a(rec, "mop_bucket")):
                S.add_contamination(w, dest, "bacteria", 0.6)
                for other in w.children(dest):
                    S.add_contamination(w, other, "bacteria", 0.4)
                S.gsec(w)["zone_violations"] += 1
                S.glog(w, "mop_water_in_kitchen_sink", sink=dest)
                outcome += " (contaminating the food-prep sink)"
            if rec.get("cl_chem", {}).get("mineral_spirits") or "solvent" in classes and classes["solvent"] > 0.2:
                S.pollute(w, "solvent_down_drain", dest, liters, source=container)
        if rec.get("liquid") in ("cooking_oil", "grease", "oil"):
            dm = w.mut(dest)
            dm["clog"] = S.rnd(M.clamp(float(drec.get("clog", 0.0)) + liters * 0.8))
            S.glog(w, "grease_down_drain", drain=dest)
        S.add_residue(w, dest, classes, 0.2)
    else:
        raise ActionError("You cannot pour that there", "argument")
    m = w.mut(container)
    m["liquid_l"] = 0.0
    m["cl_chem"] = {}
    m["cl_soil_g"] = 0.0
    m["_cl_ref_l"] = 0.0
    if rec.get("liquid") not in (None,):
        m["liquid"] = None
    S.add_residue(w, container, classes, 0.3)
    if dirty:
        S.add_face_soil(w, container, S.primary_face(rec), "dirt", 0.3)
    return outcome


def hose_runoff(ctx, target):
    w = ctx.w
    rid = w.root_room(target)
    room = w.s["rooms"].get(rid, {})
    if not room.get("outdoor"):
        w.stat("flood")
        S.glog(w, "hosed_indoors", target=target)
        return
    rec = w.ent(target)
    carries = bool(rec.get("cl_applied") or rec.get("_chem_residue") or S.total_soil(rec) > 0.2)
    _, cells = geometry.anchor(w.s, target)
    for eid in sorted(w.s["entities"]):
        orec = w.s["entities"][eid]
        if orec.get("location") != rid or not (orec.get("storm_drain") or orec.get("water_body")):
            continue
        ocells = geometry.footprint(orec) or [tuple(orec.get("pos") or (0, 0))]
        if cells and min(geometry.chebyshev(a, b) for a in cells for b in ocells) <= 2 and carries:
            S.pollute(w, "runoff", eid, 1.0, source=target)


# ----------------------------------------------------------------------
# movement consequences: footprints on wet floors, drips, dustpan spills

def _path_cells(ctx):
    path = ctx.data.get("path") or []
    return [S.ck(c) for c in path]


@after("move", order=150)
@after("approach", order=150)
@after("walk_to", order=150)
def tracks_and_drips(ctx, result):
    w = ctx.w
    room_id = ctx.data.get("from_room") or w.robot["room"]
    cells = _path_cells(ctx)
    if not cells or room_id not in w.s["rooms"]:
        return
    if w.s["rooms"][room_id].get("wet_cells"):
        S.sync_wet(w, room_id)
    room = w.s["rooms"][room_id]
    film = room.get("water_film") or {}
    floor = S.floor_of(w, room_id)
    wet_hits = [k for k in cells if film.get(k, 0.0) > 0.004]
    if wet_hits and floor:
        for key in wet_hits:
            S.add_cell_soil(w, floor, key, "streaks", 0.3)
        speed = ctx.args.get("speed") or w.robot.get("speed_mode", "normal")
        rec = w.ent(floor)
        if speed != "careful" and M.material(rec).get("slippery_wet"):
            S.gsec(w)["slips"] += 1
        S.glog(w, "walked_on_wet_floor", room=room_id, cells=wet_hits[:6])
    for item in w.held_items():
        rec = w.ent(item)
        drip = rec.get("leaking") or (float(rec.get("wetness", 0.0) or 0.0) > 0.85 and S.tool_spec(rec).get("kind") in ("mop", "towel", "cloth", "sponge"))
        if drip and floor:
            for key in cells:
                S.set_film(w, room_id, key, max(S.film_of(w, room_id, key), 0.02))
                if rec.get("leaking"):
                    S.add_cell_soil(w, floor, key, "garbage_juice", 0.2)
            S.glog(w, "drip_trail", item=item, room=room_id)
        if (S.is_a(rec, "dustpan") or rec.get("dustpan")) and float(rec.get("cl_load_g", 0.0)) > 0 and (ctx.args.get("speed") or w.robot.get("speed_mode")) == "fast" and floor:
            spilled = {k: float(v) * 0.3 for k, v in (rec.get("cl_load") or {}).items()}
            for kind, grams in sorted(spilled.items()):
                S.add_cell_soil(w, floor, cells[0], kind, M.level_from_grams(kind, grams, CELL_AREA))
            m = w.mut(item)
            m["cl_load"] = {k: S.rnd(float(v) * 0.7, 2) for k, v in (rec.get("cl_load") or {}).items()}
            m["cl_load_g"] = S.rnd(sum(m["cl_load"].values()), 2)
            S.glog(w, "dustpan_spilled", item=item)


@after("place", order=150)
def wet_floor_placement(ctx, result):
    """Rugs, mats and furniture set down on a wet floor trap the moisture."""
    w = ctx.w
    target = ctx.args.get("target")
    rec = w.s["entities"].get(target)
    if not rec or rec.get("location") not in w.s["rooms"] or not rec.get("pos"):
        return
    rid = rec["location"]
    if w.s["rooms"][rid].get("wet_cells"):
        S.sync_wet(w, rid)
    film = w.s["rooms"][rid].get("water_film") or {}
    cells = [S.ck(c) for c in geometry.footprint(rec)]
    wet = [k for k in cells if film.get(k, 0.0) > 0.004]
    if not wet:
        return
    own = [k for k in wet if k not in S.foreign_wet(w.s["rooms"][rid])]
    m = w.mut(target)
    m["wetness"] = S.rnd(M.clamp(float(m.get("wetness", 0.0) or 0.0) + 0.4))
    room = w.room_m(rid)
    trapped = room.setdefault("cl_trapped_moisture", {})
    for key in wet:
        trapped[key] = target
    floor = S.floor_of(w, rid)
    if own and floor and M.material(w.ent(floor)).get("water", 1.0) < 0.1:
        S.damage(w, floor, "trapped_moisture", 0.2, "{} placed on a wet floor".format(target))
    S.glog(w, "placed_on_wet_floor", item=target, cells=wet)
